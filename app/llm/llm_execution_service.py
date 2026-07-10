import json
import traceback
from typing import List, Dict, Optional, Tuple, Callable
from pathlib import Path

from app.utils.logger import logger
from app.llm.llm_client import LLMClient
from app.services.code_executor import CodeExecutor

API_COSTS_DELIMITER_START = "<<<API_COSTS_START>>>"
API_COSTS_DELIMITER_END = "<<<API_COSTS_END>>>"

def parse_output(output: str, start_delimiter: str, end_delimiter: str) -> Optional[Dict]:
    try:
        start_index = output.index(start_delimiter) + len(start_delimiter)
        end_index = output.index(end_delimiter, start_index)
        json_str = output[start_index:end_index].strip()
        return json.loads(json_str)
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse JSON between {start_delimiter} and {end_delimiter}: {e}")
        return None

class LLMExecutionService:
    def __init__(self, llm_client: LLMClient, code_executor: CodeExecutor, orchestrator_callbacks: Dict[str, Callable]):
        self.llm_client = llm_client
        self.code_executor = code_executor
        self._save_task_status = orchestrator_callbacks['save_task_status']
        self._save_file = orchestrator_callbacks['save_file']
        logger.info("LLMExecutionService initialized.")

    def build_error_code_string(self, error_message: str) -> str:
        sanitized_error_message = str(error_message).replace('\n', ' ')
        return (f"# Error: Code generation failed due to an internal issue: {sanitized_error_message}\n"
                f"# Execution of generated code will be skipped.")

    async def execute_code_wrapper(self, code_filename: str, host_code_path: Path, host_log_path: Path, chat_id: str, log_prefix: str, task_id_for_status: str, last_user_prompt: Optional[str]) -> Tuple[str, List[str], Dict]:
        default_api_cost_report = {"costs": [], "total_credits": 0}

        if not self.code_executor.is_available():
            logs = "Code execution skipped: Docker not available."
            await self._save_file(host_log_path, logs)
            await self._save_task_status(task_id_for_status, "processing", sub_status_message="Code execution skipped: Docker not available.")
            return logs, [], default_api_cost_report
        
        try:
            await self._save_task_status(task_id_for_status, "processing", sub_status_message="Executing code in sandbox...")
            
            summary_log_from_executor, raw_script_stdout, raw_script_stderr, script_exit_code, generated_filenames = await self.code_executor.execute_code(code_filename)

            logger.info(f"{log_prefix}Execution finished. Exit code: {script_exit_code}.")
            await self._save_task_status(task_id_for_status, "processing", sub_status_message=f"Execution finished. Exit code: {script_exit_code}.")
            logger.debug(f"{log_prefix}Executor returned {len(generated_filenames)} output filenames: {generated_filenames}")

            api_costs_data = parse_output(raw_script_stdout, API_COSTS_DELIMITER_START, API_COSTS_DELIMITER_END)
            if api_costs_data is None:
                logger.warning(f"{log_prefix}Could not parse API costs from script stdout. Defaulting to 0 credits.")
                api_costs_data = default_api_cost_report 
            else:
                logger.debug(f"{log_prefix}Parsed API costs from script stdout: {api_costs_data.get('total_credits', 0)} total credits.")

            await self._save_file(host_log_path, summary_log_from_executor)

            return summary_log_from_executor, generated_filenames, api_costs_data
        except Exception as e:
            error_logs = f"Orchestrator execute_code_wrapper failed: {e}\n{traceback.format_exc()}"
            logger.error(f"{log_prefix}{error_logs}")
            await self._save_file(host_log_path, error_logs)
            await self._save_task_status(task_id_for_status, "processing", sub_status_message=f"Orchestrator error during code execution: {e}")
            return error_logs, [], default_api_cost_report 