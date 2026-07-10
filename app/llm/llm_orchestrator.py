import asyncio
import aiofiles
import json
import math
import re
import traceback
import os
import uuid
from typing import List, Dict, Any, Set, Tuple, Optional
from datetime import datetime, timezone
from fastapi import BackgroundTasks
from pathlib import Path

from app.config.config import config
from app.utils.logger import logger
from app.db.redis_models import ConversationTurn, FileRecord, format_file_for_api
from app.db.redis_file import create_file
from app.db.redis_credit_ops import deduct_user_credits, get_user_credits
from app.db.redis_chat import get_user_id_for_chat
from app.db.redis_client import get_redis_client
from app.llm.llm_client import LLMClient
from app.services.file_manager import FileManager
from app.services.code_executor import CodeExecutor
from app.tasks.file_processing_tasks import process_s3_file_metadata_and_thumbnail

from .llm_preparation_service import LLMPreparationService
from .llm_execution_service import LLMExecutionService

RESPONSE_MESSAGE_DELIMITER_START = "<<<RESPONSE_MESSAGE_START>>>"
RESPONSE_MESSAGE_DELIMITER_END = "<<<RESPONSE_MESSAGE_END>>>"


def parse_output(output: str, start_delimiter: str, end_delimiter: str) -> Optional[Dict]:
    try:
        start_index = output.index(start_delimiter) + len(start_delimiter)
        end_index = output.index(end_delimiter, start_index)
        json_str = output[start_index:end_index].strip()
        return json.loads(json_str)
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse JSON between {start_delimiter} and {end_delimiter}: {e}")
        return None


class LLMOrchestrator:
    def __init__(self):
        try:
            self.llm_client = LLMClient()
            self.file_manager = FileManager(self.llm_client.get_client())
            self.code_executor = CodeExecutor()
            self.files_dir_host = Path(config["FILES_DIR"])
            self.instructions = self.llm_client.instructions
            self.preparation_service = LLMPreparationService(
                file_manager=self.file_manager, 
                llm_client=self.llm_client, 
                instructions=self.instructions
            )
            
            orchestrator_callbacks = {
                'save_task_status': self._save_task_status,
                'save_file': self.save_file
            }
            self.execution_service = LLMExecutionService(
                llm_client=self.llm_client,
                code_executor=self.code_executor,
                orchestrator_callbacks=orchestrator_callbacks
            )
            
            self.background_tasks_instance: Optional[BackgroundTasks] = None

        except Exception as e:
            logger.error(f"Error during synchronous LLMOrchestrator initialization: {e}", exc_info=True)
            self.llm_client = None
            self.file_manager = None
            self.code_executor = None
            self.preparation_service = None
            self.execution_service = None

        self.redis_client = None

    @classmethod
    async def create(cls) -> 'LLMOrchestrator':
        instance = cls()
        
        if not all([instance.llm_client, instance.file_manager, instance.code_executor, instance.preparation_service, instance.execution_service]):
            logger.error("LLMOrchestrator.create(): One or more core components failed to initialize in __init__.")
            raise RuntimeError("Core component initialization failed in LLMOrchestrator")

        try:
            instance.redis_client = await get_redis_client()
            if instance.redis_client is None:
                 logger.error("LLMOrchestrator failed to initialize Redis client during create().")
                 raise RuntimeError("Failed to get Redis client during orchestrator initialization")
            else:
                 logger.info("LLMOrchestrator successfully initialized Redis client.")
        except Exception as e:
            logger.error(f"Failed to get Redis client during LLMOrchestrator.create(): {e}", exc_info=True)
            raise RuntimeError("Failed to get Redis client during orchestrator initialization") from e
            
        logger.info("LLMOrchestrator instance created and initialized successfully.")
        return instance

    async def _save_task_status(self, task_id: str, status: str, data: Optional[Dict] = None, sub_status_message: Optional[str] = None):
        if not self.redis_client:
            logger.error(f"Redis client not available. Cannot save task status for {task_id}")
            return
        
        def dt_serializer(o):
            if isinstance(o, datetime):
                return o.isoformat()
            raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

        try:
            current_data = data if data is not None else {}
            current_data['task_id'] = task_id
            current_data['status'] = status
            
            if sub_status_message:
                current_data['message'] = sub_status_message
            elif 'message' not in current_data:
                current_data['message'] = status.capitalize()

            pipeline = self.redis_client.pipeline()
            pipeline.set(f"task:{task_id}:details", json.dumps(current_data, default=dt_serializer), ex=config["TASK_STATUS_EXPIRY_SECONDS"])
            await pipeline.execute()
            logger.info(f"Saved status '{status}' for task {task_id} with message: '{current_data['message']}'.")
        except Exception as e:
            logger.error(f"Error saving task status for {task_id} to Redis: {e}", exc_info=True)

    async def _execute_llm_pipeline(self, conversations: List[ConversationTurn], chat_id: str, response_id: str, background_tasks: BackgroundTasks):
        log_prefix = f"[Chat {chat_id}, Resp {response_id}, Task {response_id}] "
        logger.info(f"{log_prefix}Background task started for LLM pipeline.")

        user_id_for_s3: Optional[str] = None
        final_generated_code_string: Optional[str] = None
        final_execution_summary_log: str = "Pipeline did not complete."
        final_file_records: List[FileRecord] = []
        final_s3_code_key: Optional[str] = None
        final_s3_log_key: Optional[str] = None
        final_code_filename_ref: str = f"{response_id}.py"
        final_log_filename_ref: str = f"{response_id}.log"
        final_user_message: str = "An unexpected error occurred."
        current_execution_status: str = "pipeline_error"

        try:
            try:
                user_id_for_s3 = await get_user_id_for_chat(self.redis_client, chat_id)
                if not user_id_for_s3:
                    logger.warning(f"{log_prefix}Could not retrieve user_id for chat {chat_id}. Using 'unknown_user' for S3 paths.")
                    user_id_for_s3 = "unknown_user"
            except Exception as e_get_user:
                logger.error(f"{log_prefix}Error getting user_id for chat: {e_get_user}. Using 'unknown_user'.")
                user_id_for_s3 = "unknown_user"

            if not self.preparation_service or not self.execution_service or not self.file_manager or not self.redis_client:
                logger.error(f"{log_prefix}LLMOrchestrator services not initialized properly. Aborting pipeline.")
                await self._save_task_status(response_id, "failed", data={
                    "chat_id_associated_with_task": chat_id,
                    "response": {"message": "Internal system error: Core services not available.", "execution_status": "pipeline_error"},
                    "output_file_records": [], "assistant_turn": None, "error": "Core services not initialized"
                }, sub_status_message="Task failed: Core services not initialized.")
                return

            original_conversations = [turn.model_copy(deep=True) for turn in conversations]

            accumulated_llm_costs: float = 0.0
            final_api_costs_data_for_turn: Dict[str, Any] = {"costs": [], "total_credits": 0}

            last_user_prompt: Optional[str] = None
            if original_conversations:
                for turn in reversed(original_conversations):
                    if turn.role == 'user' and turn.prompt:
                        last_user_prompt = turn.prompt
                        break

            logger.info(f"{log_prefix}Starting pipeline execution with retry logic.")

            current_conversations_for_pipeline = [turn.model_copy(deep=True) for turn in original_conversations]

            MAX_ATTEMPTS = 3
            generated_filenames: List[str] = []
            llm_provided_response_message_content: Optional[str] = None
            script_reported_api_costs_dict: Optional[Dict] = None

            code_filename = f"{response_id}.py"
            log_filename = f"{response_id}.log"
            host_code_path = self.files_dir_host / code_filename
            host_log_path = self.files_dir_host / log_filename

            for attempt in range(MAX_ATTEMPTS):
                log_prefix_attempt = f"{log_prefix}Attempt {attempt + 1}/{MAX_ATTEMPTS}: "
                logger.info(f"{log_prefix_attempt}Starting.")

                try:
                    await self._save_task_status(response_id, "processing", sub_status_message=f"Attempt {attempt+1}/{MAX_ATTEMPTS}: Preparing files...")
                    files_input, enriched_convos, current_turn_api_to_s3_map = await self.preparation_service.prepare_files(current_conversations_for_pipeline, chat_id)

                    try:
                        prompt_log = json.dumps([turn.model_dump(mode='json', exclude_none=True) for turn in enriched_convos], indent=2)
                        logger.info(f"{log_prefix_attempt}Sending prompt to LLM. Full conversation for prompt:\n{prompt_log}")
                    except Exception as e_log:
                        logger.warning(f"{log_prefix_attempt}Could not serialize and log the full prompt: {e_log}")

                    await self._save_task_status(response_id, "processing", sub_status_message=f"Attempt {attempt+1}/{MAX_ATTEMPTS}: Generating code...")

                    generated_code_string, llm_cost = await self.preparation_service.generate_initial_code(
                        files_input,
                        enriched_convos,
                        chat_id,
                        current_turn_api_to_s3_map
                    )
                    final_generated_code_string = generated_code_string
                    accumulated_llm_costs += llm_cost

                    await self.save_file(host_code_path, generated_code_string)

                    attempt_summary_log = "Code generation resulted in an error, skipping execution."
                    attempt_generated_filenames: List[str] = []

                    if not generated_code_string.strip().startswith("# Error:"):
                        await self._save_task_status(response_id, "processing", sub_status_message=f"Attempt {attempt+1}/{MAX_ATTEMPTS}: Executing code...")
                        attempt_summary_log, attempt_generated_filenames, script_reported_api_costs_dict = await self.execution_service.execute_code_wrapper(
                            code_filename, host_code_path, host_log_path, chat_id, log_prefix_attempt, response_id, last_user_prompt
                        )

                    final_execution_summary_log = attempt_summary_log

                    _, attempt_status = self.determine_final_response_and_status(
                        generated_code_string, final_execution_summary_log, attempt_generated_filenames, None
                    )

                    if attempt_status not in ["code_generation_error", "execution_failed"]:
                        logger.info(f"{log_prefix_attempt}Succeeded with status: {attempt_status}.")
                        generated_filenames = attempt_generated_filenames
                        parsed_llm_message_json = parse_output(final_execution_summary_log, RESPONSE_MESSAGE_DELIMITER_START, RESPONSE_MESSAGE_DELIMITER_END)
                        if parsed_llm_message_json and isinstance(parsed_llm_message_json.get("message"), str):
                            llm_provided_response_message_content = parsed_llm_message_json["message"]
                        break

                    logger.warning(f"{log_prefix_attempt}Failed with status: {attempt_status}.")
                    
                    if attempt < MAX_ATTEMPTS - 1:
                        logger.info(f"{log_prefix_attempt}Preparing for retry by updating the assistant turn with the failure details.")

                        # On retry, we don't append a new turn. We update the last assistant turn
                        # to show the model its most recent mistake in the same conversation bubble.
                        last_turn = current_conversations_for_pipeline[-1] if current_conversations_for_pipeline else None

                        failed_assistant_response = {
                            "message": "The code I generated failed. I will try again with a fix.",
                            "execution_status": attempt_status,
                            "code": generated_code_string,
                            "log": final_execution_summary_log
                        }

                        if last_turn and last_turn.role == 'assistant':
                            # Update the last turn's response
                            last_turn.response = failed_assistant_response
                            last_turn.timestamp = datetime.now(timezone.utc)
                            logger.info(f"{log_prefix_attempt}Updated existing assistant turn with new failure details.")
                        else:
                            # If this is the first failure, create and append a new assistant turn.
                            failed_assistant_turn = ConversationTurn(
                                role="assistant",
                                response=failed_assistant_response,
                                timestamp=datetime.now(timezone.utc)
                            )
                            current_conversations_for_pipeline.append(failed_assistant_turn)
                            logger.info(f"{log_prefix_attempt}Added new assistant turn with failure details.")

                except Exception as inner_e:
                    logger.error(f"{log_prefix_attempt}An unexpected error occurred in the pipeline loop: {inner_e}", exc_info=True)
                    final_execution_summary_log += f"\n\nPipeline Error: {inner_e}"
                    # Break from the retry loop in case of an unexpected orchestrator error
                    break
            
            final_user_message, current_execution_status = self.determine_final_response_and_status(
                final_generated_code_string, final_execution_summary_log, generated_filenames, llm_provided_response_message_content
            )

            # After loop, process results
            s3_code_key = f"chat_data/{user_id_for_s3}/{chat_id}/assistant_code/{code_filename}"
            s3_log_key = f"chat_data/{user_id_for_s3}/{chat_id}/assistant_logs/{log_filename}"
            code_upload_success = False

            if final_generated_code_string and os.path.exists(host_code_path):
                try:
                    await self.file_manager.upload_local_file_to_s3(str(host_code_path), s3_code_key, "text/x-python")
                    logger.info(f"{log_prefix}Uploaded generated code to S3: {s3_code_key}")
                    code_upload_success = True
                    final_s3_code_key = s3_code_key
                    final_code_filename_ref = s3_code_key

                    user_id_for_code_record = await get_user_id_for_chat(self.redis_client, chat_id)
                    if user_id_for_code_record:
                        code_file_size = os.path.getsize(host_code_path)
                        await create_file(chat_id, {
                            "file_id": str(uuid.uuid4()), "original_filename": code_filename,
                            "s3_key": s3_code_key, "user_id": user_id_for_code_record, "chat_id": chat_id,
                            "status": "uploaded", "file_type": "py", "mime_type": "text/x-python",
                            "size": code_file_size, "created_at": datetime.now(timezone.utc),
                            "updated_at": datetime.now(timezone.utc), "source": "assistant_generated_code"
                        })
                except Exception as e_upload_code:
                    logger.error(f"{log_prefix}Failed to upload code {host_code_path} to S3: {e_upload_code}")
                    final_code_filename_ref = code_filename
            else:
                final_code_filename_ref = code_filename

            log_upload_success = False
            if os.path.exists(host_log_path):
                try:
                    await self.file_manager.upload_local_file_to_s3(str(host_log_path), s3_log_key, "text/plain")
                    logger.info(f"{log_prefix}Uploaded execution log to S3: {s3_log_key}")
                    log_upload_success = True
                    final_s3_log_key = s3_log_key
                    final_log_filename_ref = s3_log_key

                    user_id_for_log_record = await get_user_id_for_chat(self.redis_client, chat_id)
                    if user_id_for_log_record:
                        log_file_size = os.path.getsize(host_log_path)
                        await create_file(chat_id, {
                            "file_id": str(uuid.uuid4()), "original_filename": log_filename,
                            "s3_key": s3_log_key, "user_id": user_id_for_log_record, "chat_id": chat_id,
                            "status": "uploaded", "file_type": "log", "mime_type": "text/plain",
                            "size": log_file_size, "created_at": datetime.now(timezone.utc),
                            "updated_at": datetime.now(timezone.utc), "source": "assistant_generated_log"
                        })
                    os.remove(host_log_path)
                except Exception as e_upload_log:
                    logger.error(f"{log_prefix}Failed to upload log {host_log_path} to S3: {e_upload_log}")
                    final_log_filename_ref = log_filename
            else:
                final_log_filename_ref = log_filename

            if code_upload_success and os.path.exists(host_code_path):
                try:
                    os.remove(host_code_path)
                except OSError as e:
                    logger.error(f"{log_prefix}Error removing code file {host_code_path}: {e}")

            await self._save_task_status(response_id, "processing", sub_status_message="Processing output files...")
            final_file_records = await self.process_output_files(generated_filenames, chat_id, log_prefix, background_tasks, response_id)

            final_user_message, current_execution_status = self.determine_final_response_and_status(
                final_generated_code_string,
                final_execution_summary_log,
                final_file_records,
                llm_provided_response_message_content
            )
            logger.info(f"{log_prefix}Pipeline resulted in status: {current_execution_status}. Message: '{final_user_message[:100]}...'")

            if script_reported_api_costs_dict and isinstance(script_reported_api_costs_dict.get("costs"), list):
                final_api_costs_data_for_turn = {
                    "costs": [{"api": "gemini_llm", "credits": accumulated_llm_costs}] + script_reported_api_costs_dict["costs"],
                    "total_credits": accumulated_llm_costs + script_reported_api_costs_dict.get("total_credits",0)
                }
            else:
                 final_api_costs_data_for_turn = {
                    "costs": [{"api": "gemini_llm", "credits": accumulated_llm_costs}],
                    "total_credits": accumulated_llm_costs
                }

            if final_generated_code_string is None:
                 final_generated_code_string = "# Error: Code generation failed."

            output_files_for_final_turn = [format_file_for_api(rec) for rec in final_file_records if format_file_for_api(rec) is not None]

            final_assistant_turn = ConversationTurn(
                role="assistant",
                timestamp=datetime.now(timezone.utc),
                response= {
                    "code": final_s3_code_key,
                    "log": final_s3_log_key,
                    "message": final_user_message,
                    "execution_status": current_execution_status,
                },
                input_files=[], # Input files are part of the user turn, not duplicated here
                output_files=output_files_for_final_turn,
                api_costs=final_api_costs_data_for_turn,
            )

            total_credits_used = final_api_costs_data_for_turn.get("total_credits", 0)
            logger.info(f"{log_prefix}Attempting credit deduction. Total credits to deduct: {total_credits_used}")
            if total_credits_used > 0 and self.redis_client:
                user_id_for_deduction = await get_user_id_for_chat(self.redis_client, chat_id)
                logger.info(f"{log_prefix}User ID for credit deduction: {user_id_for_deduction}")
                if user_id_for_deduction:
                    logger.info(f"{log_prefix}Calling deduct_user_credits for user {user_id_for_deduction} with {total_credits_used} credits.")
                    try:
                        deduction_amount = int(math.ceil(total_credits_used))
                        if deduction_amount > 0:
                            await deduct_user_credits(self.redis_client, user_id_for_deduction, deduction_amount)
                            logger.info(f"{log_prefix}deduct_user_credits call completed for user {user_id_for_deduction} with amount {deduction_amount}.")
                        else:
                            logger.info(f"{log_prefix}Credit deduction skipped: calculated deduction_amount is {deduction_amount}.")
                    except Exception as e_deduct:
                        logger.error(f"{log_prefix}Error during deduct_user_credits for user {user_id_for_deduction}: {e_deduct}", exc_info=True)
                else:
                    logger.warning(f"{log_prefix}Credit deduction skipped: user_id_for_deduction is None or False.")
            elif total_credits_used <= 0:
                logger.info(f"{log_prefix}Credit deduction skipped: total_credits_used is {total_credits_used}.")
            elif not self.redis_client:
                logger.warning(f"{log_prefix}Credit deduction skipped: Redis client is not available.")

            final_result_data = {
                "chat_id_associated_with_task": chat_id,
                "response": final_assistant_turn.response,
                "output_file_records": [rec.model_dump() for rec in final_file_records],
                "assistant_turn": final_assistant_turn.model_dump(),
                "error": None if current_execution_status == "success" else f"Task ended with status: {current_execution_status}"
            }

            if current_execution_status == "success":
                await self._save_task_status(response_id, "completed", data=final_result_data, sub_status_message="Task completed successfully.")
                logger.info(f"{log_prefix}Pipeline completed successfully.")
            else:
                await self._save_task_status(response_id, "failed", data=final_result_data, sub_status_message=f"Task failed with status: {current_execution_status}.")
                logger.error(f"{log_prefix}Pipeline failed. Final status: {current_execution_status}")

        except Exception as e_pipeline:
            logger.error(f"{log_prefix}Critical error in LLM pipeline: {e_pipeline}", exc_info=True)
            error_log_content = f"Internal error: {e_pipeline}\n{traceback.format_exc()}"

            final_log_ref_on_error = final_log_filename_ref if final_log_filename_ref else f"{response_id}_pipeline_error.log"
            host_err_log_path = self.files_dir_host / (os.path.basename(str(final_log_ref_on_error)) if isinstance(final_log_ref_on_error, str) and final_log_ref_on_error.startswith('chat_data') else final_log_ref_on_error)

            if final_execution_summary_log:
                error_log_content = f"{final_execution_summary_log}\n\nPIPELINE ERROR: {error_log_content}"

            try:
                await self.save_file(host_err_log_path, error_log_content)
                user_id_for_err_log = user_id_for_s3 if user_id_for_s3 else "unknown_user"
                s3_err_log_key = f"chat_data/{user_id_for_err_log}/{chat_id}/assistant_logs/{os.path.basename(str(host_err_log_path))}"
                await self.file_manager.upload_local_file_to_s3(str(host_err_log_path), s3_err_log_key, "text/plain")
                if os.path.exists(host_err_log_path):
                    os.remove(host_err_log_path)
                final_log_ref_on_error = s3_err_log_key
            except Exception as e_log_err:
                logger.error(f"{log_prefix}Failed to save/upload critical error log: {e_log_err}")

            error_response_data = {
                "code": final_s3_code_key or final_code_filename_ref,
                "log": final_log_ref_on_error,
                "message": "Internal error processing your request. Please check logs or try again later.",
                "execution_status": "pipeline_error"
            }

            error_data = {
                "chat_id_associated_with_task": chat_id,
                "response": error_response_data,
                "output_file_records": [],
                "assistant_turn": ConversationTurn(
                    role="assistant",
                    response=error_response_data,
                    timestamp=datetime.now(timezone.utc)
                ).model_dump(),
                "error": str(e_pipeline)
            }
            await self._save_task_status(response_id, "failed", data=error_data, sub_status_message=f"Task failed due to critical pipeline error: {str(e_pipeline)}")
            logger.error(f"{log_prefix}Background task critically failed.")


    async def process_command(self, conversations: List[ConversationTurn], chat_id: str, background_tasks: BackgroundTasks) -> Dict[str, Any]:
        response_id = str(uuid.uuid4())
        
        user_id = await get_user_id_for_chat(self.redis_client, chat_id)
        if not user_id:
            return {"task_id": response_id, "status": "failed", "message": "Failed to identify user for credit check."}

        credits = await get_user_credits(self.redis_client, user_id)
        if credits <= 0:
            insufficient_credits_data = {
                "task_id": response_id,
                "status": "failed",
                "message": "You have run out of credits. Please upgrade your plan to continue.",
                "assistant_turn": {
                    "response": {
                        "message": "You have run out of credits. Please upgrade your plan to continue.",
                        "execution_status": "insufficient_credits"
                    }
                }
            }
            await self._save_task_status(response_id, "failed", data=insufficient_credits_data)
            return insufficient_credits_data

        initial_task_data = {
            "task_id": response_id, "status": "pending",
            "message": "Task submitted and waiting to be processed.",
            "chat_id_associated_with_task": chat_id
        }
        await self._save_task_status(response_id, "pending", data=initial_task_data)        
        background_tasks.add_task(self._execute_llm_pipeline, conversations, chat_id, response_id, background_tasks)
        return initial_task_data

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        if not self.redis_client: return {"task_id": task_id, "status": "error", "message": "System error: Redis unavailable."}
        try:
            details_json = await self.redis_client.get(f"task:{task_id}:details")
            if not details_json: 
                # If details are not found, the task is considered not_found or has expired.
                # The caller should handle None appropriately, e.g., by showing "task not found".
                return None
            
            return json.loads(details_json.decode() if isinstance(details_json, bytes) else str(details_json))
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON for task {task_id}: {e}", exc_info=True)
            # If details were found but couldn't be parsed, this indicates corruption.
            return {"task_id": task_id, "status": "error", "message": f"Corrupted task data: {str(e)}"}
        except Exception as e: 
            logger.error(f"Error fetching task status {task_id}: {e}", exc_info=True)
            return {"task_id": task_id, "status": "error", "message": f"Error: {str(e)}"}

    async def process_output_files(self, filenames: List[str], chat_id: str, log_prefix: str, background_tasks: BackgroundTasks, response_id_for_genai: str) -> List[FileRecord]:
        logger.debug(f"{log_prefix}Processing {len(filenames)} generated files: {filenames}")
        processed_file_records: List[FileRecord] = []
        if not self.redis_client: return processed_file_records
        user_id = await get_user_id_for_chat(self.redis_client, chat_id)
        if not user_id: return processed_file_records

        for filename in filenames:
            local_sandbox_output_path = self.files_dir_host / filename
            try:
                file_record = await self.file_manager.process_sandbox_output_file(str(local_sandbox_output_path), user_id, chat_id)
                if file_record and file_record.s3_key and file_record.file_id:
                    logger.info(f"{log_prefix}Processed sandbox output {filename} to S3: {file_record.s3_key}")
                    background_tasks.add_task(process_s3_file_metadata_and_thumbnail, file_record.s3_key, file_record.file_id, self.file_manager)
                    processed_file_records.append(file_record)

                    async def _upload_output_to_google(fr: FileRecord, c_id: str, r_id: str):
                        ul_prefix = f"[Chat {c_id}, Resp {r_id}, SecUpload {fr.original_filename or 'N/A'}] "
                        try:
                            api_f = await self.file_manager.upload_file_if_needed(fr.s3_key, c_id)
                            if api_f: logger.info(f"{ul_prefix}Uploaded output {fr.s3_key} to GenAI: {api_f.name}")
                            else: logger.error(f"{ul_prefix}Failed GenAI upload for {fr.s3_key}")
                        except Exception as e_ul: logger.error(f"{ul_prefix}GenAI upload error for {fr.s3_key}: {e_ul}")
                    asyncio.create_task(_upload_output_to_google(file_record, chat_id, response_id_for_genai))
                else: logger.warning(f"{log_prefix}Failed to process {filename} or record incomplete.")
            except Exception as e: logger.error(f"{log_prefix}Error processing {filename}: {e}", exc_info=True)
        return processed_file_records

    def determine_final_response_and_status(self, generated_code_string: str, execution_summary_log: str, files: List[Any], llm_provided_message: Optional[str]) -> Tuple[str, str]:
        execution_status: str
        final_message: str

        # Determine execution_status
        if generated_code_string.strip().startswith("# Error:"):
            execution_status = "code_generation_error"
        elif "FAILED" in execution_summary_log.upper() or "TRACEBACK (MOST RECENT CALL LAST):" in execution_summary_log.upper() or "ERROR:" in execution_summary_log.upper() : # Crude check, might need refinement
            execution_status = "execution_failed"
        elif not files:
            execution_status = "no_output_generated"
        else:
            execution_status = "success"

        # Determine final_message
        if llm_provided_message and llm_provided_message.strip():
            final_message = llm_provided_message.strip()
        else:
            if execution_status == "code_generation_error":
                final_message = "The LLM was unable to generate the initial code. An internal error likely occurred or the request was too complex."
            elif execution_status == "execution_failed":
                final_message = "The generated code encountered errors during execution. Please review the logs for details."
            elif execution_status == "no_output_generated":
                final_message = "The code executed, but no output files were produced. This could be an error or the task might not require file generation. Please check the logs."
            elif execution_status == "success":
                final_message = "The process completed successfully and output files have been generated."
            else: # Should not happen with current status logic
                final_message = "An unexpected state was reached."
        
        logger.info(f"Determined execution status: {execution_status}, Final user message: '{final_message[:100]}...'")
        return final_message, execution_status

    async def save_file(self, path: Path, content: str) -> None:
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)