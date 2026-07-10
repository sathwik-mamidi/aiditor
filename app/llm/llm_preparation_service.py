import asyncio
import json
import re
from typing import List, Dict, Any, Set, Tuple, Optional
from datetime import datetime

from google.genai import types as genai_types

from app.utils.logger import logger
from app.db.redis_models import ConversationTurn, FileRecord, format_file_for_api
from app.db.redis_file import get_file_by_s3_key
from app.llm.llm_client import LLMClient, LLMError # Assuming LLMClient is in the same directory
from app.services.file_manager import FileManager # Assuming FileManager is accessible
import math # For cost calculation
from app.config.config import config

def clean_code(code: str) -> str:
    """
    Cleans the raw code string from the LLM by extracting the content
    from markdown code blocks (e.g., ```python ... ```).
    If no code block is found, it returns an error string.
    This version is more robust against code blocks containing '```' in string literals.
    """
    if not code:
        return "# Error: LLM returned an empty response."

    code = code.strip()

    # Find the start of the code block
    start_index = code.find("```")
    
    # Find the end of the code block, which we assume is the last occurrence of ```
    end_index = code.rfind("```")

    # If we don't find a pair of markers, it's not a valid block.
    if start_index == -1:
        # If no markdown block is found, assume it's not code.
        # This prevents executing error messages from the LLM.
        return f"# Error: LLM response did not contain a code block. Raw response: {code}"
    
    if start_index == end_index:
        return f"# Error: LLM response contained an unterminated code block. Raw response: {code}"

    # Extract content between the first and last markers
    content = code[start_index + 3:end_index]
    
    # Remove optional language identifier (e.g., 'python') that might be at the start
    cleaned_content = re.sub(r"^\s*python\s*", "", content, count=1, flags=re.IGNORECASE).strip()

    return cleaned_content

class LLMPreparationService:
    def __init__(self, file_manager: FileManager, llm_client: LLMClient, instructions: str):
        self.file_manager = file_manager
        self.llm_client = llm_client
        self.instructions = instructions
        self.use_vertex = config.get("GOOGLE_GENAI_USE_VERTEXAI", False)
        logger.info(f"LLMPreparationService initialized (Vertex AI mode: {self.use_vertex}).")

    async def prepare_files(self, conversations: List[ConversationTurn], chat_id: str) -> Tuple[List[Any], List[ConversationTurn], Dict[str, str]]:
        processed_s3_keys = set()
        files_to_process_records: List[FileRecord] = []
        api_name_to_s3_key_map: Dict[str, str] = {}
        
        for turn in conversations:
            for file_dict in (turn.input_files or []) + (turn.output_files or []):
                s3_key_to_lookup = file_dict.get('s3_key')
                if s3_key_to_lookup:
                    if s3_key_to_lookup not in processed_s3_keys:
                        file_record = await get_file_by_s3_key(s3_key=s3_key_to_lookup)
                        if file_record:
                            if file_record.s3_key: # s3_key should exist if record found by it
                                # Filter based on source before adding for upload
                                if file_record.source not in ["assistant_generated_code", "assistant_generated_log"]:
                                    files_to_process_records.append(file_record)
                                    processed_s3_keys.add(file_record.s3_key)
                                else:
                                    logger.info(f"[Chat {chat_id}] prepare_files: Skipping GenAI prep for file {file_record.s3_key} (source: {file_record.source}). It will not be uploaded to Gemini for this turn.")
                                    processed_s3_keys.add(file_record.s3_key) # Add to processed_s3_keys to avoid reprocessing, even if skipped for upload
                            else:
                                logger.warning(f"[Chat {chat_id}] FileRecord {file_record.file_id} (lookup key: {s3_key_to_lookup}) missing s3_key itself. Skipping for GenAI prep.")
                        else:
                            logger.warning(f"[Chat {chat_id}] No FileRecord found for s3_key: {s3_key_to_lookup}. Skipping for GenAI prep.")
        
        tasks = []
        if self.use_vertex:
            logger.info(f"[Chat {chat_id}] Vertex mode: Preparing file parts from {len(files_to_process_records)} records.")
            for file_rec in files_to_process_records:
                tasks.append(self.file_manager.get_file_part_for_vertex(file_rec.s3_key, chat_id))
        else:
            logger.info(f"[Chat {chat_id}] Gemini API mode: Uploading {len(files_to_process_records)} files.")
            for file_rec in files_to_process_records:
                tasks.append(self.file_manager.upload_file_if_needed(file_rec.s3_key, chat_id))
            
        results = await asyncio.gather(*tasks)
        
        api_files_list = []
        for i, result_item in enumerate(results):
            if result_item:
                api_files_list.append(result_item)
                # For Gemini API mode, we need to map the API name back to the S3 key
                if not self.use_vertex and isinstance(result_item, genai_types.File):
                    original_file_rec = files_to_process_records[i]
                    if original_file_rec.s3_key:
                        api_name_to_s3_key_map[result_item.name] = original_file_rec.s3_key
                    else:
                        logger.warning(f"[Chat {chat_id}] FileRecord {original_file_rec.file_id} (used for API file {result_item.name}) was missing s3_key when building map.")
        
        enriched = []
        for turn in conversations:
            copy = turn.model_copy(deep=True)
            if turn.input_files:
                copy.input_files = await self._format_file_dicts_for_llm_history(turn.input_files)
            if turn.output_files:
                copy.output_files = await self._format_file_dicts_for_llm_history(turn.output_files)
            enriched.append(copy)
            
        return api_files_list, enriched, api_name_to_s3_key_map

    async def _format_file_dicts_for_llm_history(self, file_dicts: List[Dict]) -> List[Dict]:
        updated = []
        for file_dict in file_dicts:
            s3_key_to_lookup = file_dict.get('s3_key')
            record_found_and_formatted = False
            if s3_key_to_lookup:
                record = await get_file_by_s3_key(s3_key=s3_key_to_lookup)
                if record:
                    formatted = format_file_for_api(record)
                    if formatted:
                        updated.append(formatted)
                        record_found_and_formatted = True
            if not record_found_and_formatted:
                updated.append(file_dict) # Keep original if record not found or not formatted
        return updated

    async def process_conversation_history(self, conversations: List[ConversationTurn], chat_id: str) -> Tuple[List[Dict], Set[str], List[Any], Dict[str,str]]:
        history = []
        mentioned_files = set()
        historical_assistant_generated_api_files: List[Any] = []
        historical_api_name_to_s3_key_map: Dict[str, str] = {}

        for turn in conversations:
            turn_dict = {"timestamp": turn.timestamp.isoformat(), "role": turn.role}

            if turn.role == 'user':
                if turn.prompt:
                    turn_dict['prompt'] = turn.prompt
                if turn.input_files:
                    # For user inputs, we assume files listed are intended for LLM.
                    # The `prepare_files` method (called by generate_initial_code before this usually)
                    # would have already filtered user-uploaded files if they had restricted sources,
                    # though typically user files won't have 'assistant_generated_code/log' sources.
                    turn_dict['input_files'] = await self._format_file_dicts_for_llm_history(turn.input_files)
                    for file_desc in turn_dict['input_files']: # Ensure user input files are added to mentioned_files if they have an API name
                        if file_desc.get('api_name'):
                             mentioned_files.add(file_desc['api_name'])
                        elif file_desc.get('s3_key'): # Fallback to s3_key if api_name not yet resolved
                             mentioned_files.add(file_desc['s3_key'])


            elif turn.role == 'assistant':
                if turn.response:
                    # Start with a copy of the response, excluding file reference keys ('code', 'log') 
                    # that will be replaced by their actual content.
                    response_for_prompt = {k: v for k, v in turn.response.items() if k not in [
                        'generated_code_content', 'generated_log_content', 'code', 'log'
                    ]}

                    # --- Content Injection for Historical Assistant Turns ---
                    # On retry attempts, the orchestrator places the failed code and log content directly
                    # into the 'code' and 'log' fields of the assistant's response. We inject that
                    # content here for the LLM's context.

                    code_content = turn.response.get('code')
                    if code_content and isinstance(code_content, str):
                        response_for_prompt['generated_code_content'] = code_content

                    log_content = turn.response.get('log')
                    if log_content and isinstance(log_content, str):
                        response_for_prompt['generated_log_content'] = log_content
                    
                    turn_dict['response'] = response_for_prompt

                if turn.output_files: # These are script-generated output files from a previous turn
                    # These should have been filtered by source in `prepare_files` if they were problematic.
                    # Here we mostly ensure they are formatted correctly for history.
                    turn_dict['output_files'] = await self._format_file_dicts_for_llm_history(turn.output_files)
                    for file_desc in turn_dict['output_files']: # Ensure these are added to mentioned_files if they have an API name
                        if file_desc.get('api_name'):
                             mentioned_files.add(file_desc['api_name'])
                        elif file_desc.get('s3_key'):
                             mentioned_files.add(file_desc['s3_key'])
            
            history.append(turn_dict)
        return history, mentioned_files, historical_assistant_generated_api_files, historical_api_name_to_s3_key_map

    def process_file_list(self, files: List[Dict], mentioned_files: Set[str]) -> List[Dict]:
        cleaned = []
        for file_dict in files:
            if not file_dict:
                continue
            cleaned_file = {k: v for k, v in file_dict.items() if v is not None}
            identifier_for_context = cleaned_file.get('s3_key')
            if identifier_for_context:
                mentioned_files.add(identifier_for_context)
            cleaned.append(cleaned_file)
        return cleaned

    def build_prompt(self, history: List[Dict], api_file_references: List[str], llm_cost_credits: int, api_name_to_s3_key_map: Dict[str, str]) -> str:
        unique_api_file_references = sorted(list(dict.fromkeys(api_file_references)))
        file_context_lines = []
        if unique_api_file_references:
            file_context_lines.append("Available files (passed to this LLM call via File API):")
            for api_name in unique_api_file_references:
                s3_key = api_name_to_s3_key_map.get(api_name)
                s3_key_info = f" (S3 Key: {s3_key})" if s3_key else ""
                file_context_lines.append(f"- API Name: {api_name}{s3_key_info}")
            file_context = "\n".join(file_context_lines)
        else:
            file_context = "- No files made available via File API for current operation."

        api_costs_config = {
            "whisper": 0.01,  # $0.006 / minute -> 0.01 credits / second
            "imagen": 3,      # Cost for Imagen 3 image generation ($0.03 per image) -> 3 credits
            "gemini_image": 4, # Cost for Gemini 2.0 Flash image generation ($0.039 per image) -> ~4 credits
            "veo": 35,        # Cost for Veo 2 video generation ($0.35 per second) -> 35 credits
            "openai_tts": {   # OpenAI TTS (gpt-4o-mini-tts)
                "input_credits_per_1M_chars": 60,   # $0.60 per 1M input characters
                "output_credits_per_1M_tokens": 1200 # $12.00 per 1M output audio tokens (approximately $0.015 per minute)
            },
            "lyria": 0.2, # Cost for Lyria music generation, in credits per second of generated audio.
            "gemini_llm": llm_cost_credits # Dynamically calculated cost for the main LLM call itself
        }
        api_cost_info = json.dumps(api_costs_config, indent=2)

        return (
            f"{self.instructions}\n\n"
            f"Chat History (most recent turn last):\n"
            f"{json.dumps(history, indent=2)}\n\n"
            f"File Context:\n"
            f"{file_context}\n\n"
            f"File Information:\n"
            f"- You have access to {len(unique_api_file_references)} files via the API. "
            f"These files are referenced by their API names and S3 keys (e.g., 'files/xxxx' (S3 Key: 'chat_data/user_id/chat_id/category/xxxx.extension')) "
            f"in the chat history or File Context section.\n"
            f"- When referring to these files in your generated code, use their S3 keys. If the user's request implies an operation on multiple provided files (e.g., 'process these images', 'summarize these documents'), ensure your generated code iterates through and processes ALL relevant S3 keys listed in the 'File Context' section above. The user's last message is the primary instruction for what to do with the file(s).\n"
            f"- Last user message is the primary instruction.\n\n"
            f"API Cost Information (in credits):\n"
            f"{api_cost_info}\n"
        )

    async def generate_initial_code(
        self,
        files_input: List[Any], # Can be genai_types.File or genai_types.Part
        enriched_conversations: List[ConversationTurn],
        chat_id: str,
        current_turn_api_to_s3_map: Dict[str, str]
    ) -> Tuple[str, float]:
        log_prefix = f"[Chat {chat_id}] "

        # Calculate token count for the final prompt with files
        input_token_count = 0
        llm_cost_credits = 0
        
        try:
            # Uses methods from this class
            history, _, historical_assistant_api_files, api_name_to_s3_key_map_historical = await self.process_conversation_history(enriched_conversations, chat_id)
            
            all_api_files_for_llm = files_input + historical_assistant_api_files
            final_api_name_to_s3_key_map = {**current_turn_api_to_s3_map, **api_name_to_s3_key_map_historical}

            prompt_api_file_references = []
            for f_obj in all_api_files_for_llm: # Renamed f to f_obj to avoid conflict
                if f_obj and hasattr(f_obj, 'name') and f_obj.name:
                    prompt_api_file_references.append(f_obj.name)
                elif f_obj and hasattr(f_obj, 'uri') and f_obj.uri:
                    prompt_api_file_references.append(f_obj.uri)

            temp_prompt_for_counting = self.build_prompt(history, prompt_api_file_references, 0, final_api_name_to_s3_key_map)

            try:
                input_token_count = await self.llm_client.count_input_tokens(prompt=temp_prompt_for_counting, files=all_api_files_for_llm, chat_id=chat_id)
                logger.debug(f"{log_prefix}Calculated input tokens: {input_token_count}")
                token_based_cost_usd = 0
                if input_token_count > 0: # Simplified cost calculation
                    token_based_cost_usd = (input_token_count / 1_000_000) * (2.50 if input_token_count > 200_000 else 1.25)
                
                # Add a default 3 credits for code generation, plus token-based cost
                llm_cost_credits = 3 + math.ceil(token_based_cost_usd * 100)
                logger.debug(f"{log_prefix}Calculated LLM cost (3 base + token-based): {llm_cost_credits} credits.")
            except LLMError as token_error:
                logger.error(f"{log_prefix}Failed to count input tokens: {token_error}. Proceeding with default 3 credits.")
                llm_cost_credits = 3 # Default to 3 credits if token counting fails

            final_prompt = self.build_prompt(history, prompt_api_file_references, llm_cost_credits, final_api_name_to_s3_key_map)
            
            # Call LLM client (from self.llm_client) for code generation
            raw_response = await self.llm_client.generate_content(
                prompt=final_prompt, 
                files=files_input, # Pass list of Parts or Files
                chat_id=chat_id
            )
            
            if not raw_response: # Directly use build_error_code_string from LLMExecutionService if it's better there.
                                # For now, let's assume this service can create a simple error string.
                logger.error(f"{log_prefix}Code generation failed (empty response from LLM).")
                return "# Error: Code generation failed (empty response from LLM)", llm_cost_credits 
            
            return clean_code(raw_response), llm_cost_credits
            
        except Exception as e:
            logger.error(f"{log_prefix}Code generation error in LLMPreparationService: {e}", exc_info=True)
            error_message_processed = str(e).replace('\n', ' ')
            return f"# Error: Code generation failed due to an internal issue: {error_message_processed}", llm_cost_credits 