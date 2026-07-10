import docker
import docker.errors
import asyncio
import pathlib
import os
from typing import Tuple, List, Optional, Dict
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import io
import tarfile
import json
import aiofiles
import uuid

from app.config.config import config
from app.utils.logger import logger


class DockerExecutionError(Exception):
    def __init__(self, message: str, exit_code: int = 1, logs: str = ""):
        self.message = message
        self.exit_code = exit_code
        self.logs = logs
        super().__init__(message)


class CodeExecutor:
    EXECUTION_TIMEOUT_SECONDS = config["CODE_EXECUTION_TIMEOUT_SECONDS"]
    OUTPUT_DELIM_START = config["CODE_OUTPUT_DELIM_START"]
    OUTPUT_DELIM_END = config["CODE_OUTPUT_DELIM_END"]
    
    def __init__(self):
        self.docker_client = None
        self.python_sandbox_container_name = None
        self.files_dir_host = pathlib.Path(config["FILES_DIR"])
        self.files_dir_container = pathlib.Path(config["CONTAINER_FILES_DIR"])
        self._initialize_docker()

    def _initialize_docker(self) -> None:
        docker_socket = config.get('DOCKER_SOCKET_PATH')
        connection_methods = []
        
        if docker_socket:
            connection_methods.append({
                'method': 'socket',
                'url': f"unix://{docker_socket}",
                'description': f"socket {docker_socket}"
            })
            
        connection_methods.append({
            'method': 'environment',
            'url': None,
            'description': "environment variables"
        })
        
        for conn in connection_methods:
            try:
                logger.info(f"Attempting Docker connection via {conn['description']}")
                
                if conn['method'] == 'environment':
                    client = docker.from_env(timeout=10)
                else:
                    client = docker.DockerClient(base_url=conn['url'], timeout=10)
                    
                client.ping()
                self.docker_client = client
                self.python_sandbox_container_name = config["PYTHON_SANDBOX_CONTAINER_NAME"]
                logger.info(f"Successfully connected to Docker daemon via {conn['description']}. Container: {self.python_sandbox_container_name}")
                return
            except Exception as e:
                logger.warning(f"Failed to connect via {conn['description']}: {e}")
                self.docker_client = None
        
        logger.error("Docker client could not be initialized. Code execution will be disabled.")

    async def _ensure_container_running(self, container_name: str) -> Optional[docker.models.containers.Container]:
        if not self.docker_client:
            raise DockerExecutionError("Docker client not available")
            
        try:
            container = await asyncio.to_thread(
                self.docker_client.containers.get, container_name
            )
            
            if container.status != 'running':
                logger.info(f"Starting container {container_name}...")
                await asyncio.to_thread(container.start)
                await asyncio.sleep(2)
                
                container = await asyncio.to_thread(
                    self.docker_client.containers.get, container_name
                )
                
                if container.status != 'running':
                    logger.error(f"Container {container_name} status is '{container.status}' after start attempt.")
                    raise DockerExecutionError(f"Failed to start container {container_name}, status: {container.status}")
                    
                logger.info(f"Container {container_name} started successfully")
                
            return container
            
        except docker.errors.NotFound:
            logger.error(f"Docker container '{container_name}' not found. This is a configuration or deployment issue.")
            raise DockerExecutionError(f"Container {container_name} not found")
        except docker.errors.APIError as e:
            logger.error(f"Docker API error while ensuring container {container_name} is running: {str(e)}", exc_info=True)
            raise DockerExecutionError(f"Docker API error accessing container {container_name}: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error ensuring container {container_name} is running: {str(e)}", exc_info=True)
            raise DockerExecutionError(f"Unexpected error with container {container_name}: {str(e)}")

    @asynccontextmanager
    async def _execute_in_container(self, container: docker.models.containers.Container, 
                                 cmd: List[str]) -> None:
        yield None, None, None

    async def _get_exec_exit_code(self, exec_id: str) -> int:
        try:
            inspect_info = await asyncio.to_thread(
                self.docker_client.api.exec_inspect, exec_id
            )
            return inspect_info.get('ExitCode', -1)
        except Exception as e:
            logger.error(f"Error getting exec exit code for {exec_id}: {e}")
            return -1

    def _parse_script_stdout_json(self, script_stdout: str, start_delimiter: str, end_delimiter: str) -> Optional[Dict]:
        """Helper to parse JSON from script's stdout between delimiters."""
        try:
            start_index = script_stdout.index(start_delimiter) + len(start_delimiter)
            end_index = script_stdout.index(end_delimiter, start_index)
            json_str = script_stdout[start_index:end_index].strip()
            return json.loads(json_str)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to parse JSON from script stdout between {start_delimiter} and {end_delimiter}: {e}")
            return None

    def _extract_output_files_basenames(self, script_stdout: str) -> List[str]:
        """Extracts list of output file basenames from script's stdout."""
        output_files_json = self._parse_script_stdout_json(script_stdout, self.OUTPUT_DELIM_START, self.OUTPUT_DELIM_END)
        if output_files_json and isinstance(output_files_json.get("files"), list):
            # Ensure all elements are strings
            files_list = [f for f in output_files_json["files"] if isinstance(f, str)]
            if len(files_list) != len(output_files_json["files"]):
                logger.warning("Some non-string elements found in 'files' list from script output.")
            return files_list
        return []

    def _create_execution_summary(self, 
                                code_filename: str, 
                                script_stdout: str,
                                script_stderr: str,
                                exit_code: int,
                                timed_out: bool,
                                executor_error_message: Optional[str] = None
                                ) -> str:
        timestamp = datetime.now(timezone.utc).isoformat()
        
        status = f"FAILED (Exit Code: {exit_code})"
        if executor_error_message:
            status = f"EXECUTOR ERROR"
        elif timed_out:
            status = f"TIMED OUT (Exit Code: {exit_code}, likely 137)"
        elif exit_code == 0:
            status = "SUCCESS"
        
        
        summary_lines = [
            f"=== Code Execution Summary - {timestamp} ===",
            f"Status: {status}",
            f"Container: {self.python_sandbox_container_name or 'N/A'}",
            f"Script: {code_filename}",
        ]

        if executor_error_message:
            summary_lines.append(f"Executor Error: {executor_error_message}")

        summary_lines.append("--- SCRIPT STDOUT ---")
        if script_stdout.strip():
            summary_lines.append(script_stdout.strip())
        else:
            summary_lines.append("(empty)")
        summary_lines.append("--- END SCRIPT STDOUT ---")
        
        summary_lines.append("--- SCRIPT STDERR ---")
        if script_stderr.strip():
            summary_lines.append(script_stderr.strip())
        else:
            summary_lines.append("(empty)")
        summary_lines.append("--- END SCRIPT STDERR ---")
        
        summary_lines.append("=== End of Execution Summary ===")
        
        return "\n".join(summary_lines)

    async def execute_code(self, code_filename: str) -> Tuple[str, str, str, int, List[str]]:
        retrieved_file_basenames: List[str] = []
        if not self.is_available():
            error_msg = "Docker environment not available. Code execution skipped."
            logger.error(error_msg)
            summary = self._create_execution_summary(code_filename, "", "", 1, False, executor_error_message=error_msg)
            return summary, "", "", 1, retrieved_file_basenames
            
        code_filename_basename = os.path.basename(code_filename)
        container_code_path = str(self.files_dir_container / code_filename_basename)
        
        actual_host_code_path = self.files_dir_host / code_filename_basename
        if not actual_host_code_path.is_file():
            error_msg = f"Code file not found on host: {actual_host_code_path}"
            logger.error(error_msg)
            summary = self._create_execution_summary(code_filename_basename, "", "", 1, False, executor_error_message=error_msg)
            return summary, "", "", 1, retrieved_file_basenames
            
        script_stdout_str = ""
        script_stderr_str = ""
        exit_code = -1
        timed_out = False
        executor_error = None
        container = None
        execution_id = str(uuid.uuid4())
        sandbox_execution_tmp_dir = self.files_dir_container / execution_id

        try:
            container = await self._ensure_container_running(self.python_sandbox_container_name)
            if not container:
                 raise DockerExecutionError(f"Failed to get running container: {self.python_sandbox_container_name}")

            try:
                logger.info(f"Attempting to copy {actual_host_code_path} (from aiditor) to {container.name}:{self.files_dir_container} (in sandbox)")
                file_data = actual_host_code_path.read_bytes()
                tar_stream = io.BytesIO()
                with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                    tarinfo = tarfile.TarInfo(name=code_filename_basename)
                    tarinfo.size = len(file_data)
                    tarinfo.mtime = int(actual_host_code_path.stat().st_mtime)
                    tar.addfile(tarinfo, io.BytesIO(file_data))
                tar_stream.seek(0)

                await asyncio.to_thread(
                    container.put_archive,
                    path=str(self.files_dir_container),
                    data=tar_stream
                )
                logger.info(f"Successfully copied {actual_host_code_path} to {container.name}:{container_code_path}")
            except Exception as copy_err:
                executor_error = f"Failed to copy script to container: {copy_err}"
                logger.error(f"{executor_error}", exc_info=True)
                summary = self._create_execution_summary(code_filename_basename, "", "", 1, False, executor_error_message=executor_error)
                return summary, "", "", 1, retrieved_file_basenames

            command = ["python3", "-u", container_code_path]
            logger.info(f"Executing command in container {container.name}: {command} with EXECUTION_ID={execution_id}")
            
            exec_result = await asyncio.wait_for(
                asyncio.to_thread(
                    container.exec_run,
                    cmd=command,
                    stdout=True,
                    stderr=True,
                    demux=True,
                    tty=False,
                    environment={"EXECUTION_ID": execution_id}
                ),
                timeout=self.EXECUTION_TIMEOUT_SECONDS
            )
            
            exit_code = exec_result.exit_code
            stdout_bytes, stderr_bytes = exec_result.output
            
            script_stdout_str = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            script_stderr_str = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            
            logger.info(f"Script {code_filename_basename} executed. Exit code: {exit_code}.")
            if script_stdout_str:
                 logger.debug(f"Script STDOUT:\n{script_stdout_str[:500]}...")
            if script_stderr_str:
                 logger.debug(f"Script STDERR:\n{script_stderr_str[:500]}...")

            if exit_code == 0 and script_stdout_str:
                reported_basenames = self._extract_output_files_basenames(script_stdout_str)
                logger.info(f"Script reported {len(reported_basenames)} output files: {reported_basenames}")
                for basename in reported_basenames:
                    sandbox_file_path = sandbox_execution_tmp_dir / basename
                    host_target_path = self.files_dir_host / basename
                    try:
                        logger.info(f"Attempting to retrieve '{basename}' from sandbox:{sandbox_file_path} to host:{host_target_path}")
                        bits, stat = await asyncio.to_thread(container.get_archive, str(sandbox_file_path))
                        
                        with tarfile.open(fileobj=io.BytesIO(b"".join(bits)), mode='r:') as tar:
                            member = tar.next()
                            if member and member.isfile():
                                extracted_file_data = tar.extractfile(member).read()
                                async with aiofiles.open(host_target_path, "wb") as f:
                                    await f.write(extracted_file_data)
                                retrieved_file_basenames.append(basename)
                                logger.info(f"Successfully retrieved '{basename}' to {host_target_path} ({len(extracted_file_data)} bytes)")
                            else:
                                logger.warning(f"Could not extract '{basename}' from archive retrieved from sandbox path {sandbox_file_path}. Tar member not found or not a file.")
                    except docker.errors.NotFound:
                        logger.warning(f"File '{basename}' (path {sandbox_file_path}) not found in sandbox for retrieval.")
                    except Exception as e_retrieve:
                        logger.error(f"Failed to retrieve '{basename}' from sandbox path {sandbox_file_path}: {e_retrieve}", exc_info=True)
            elif exit_code == 0 and not script_stdout_str:
                 logger.warning("Script exited successfully but produced no stdout. Cannot determine output files to retrieve.")

        except asyncio.TimeoutError:
            timed_out = True
            exit_code = 137
            timeout_msg = f"Code execution timed out after {self.EXECUTION_TIMEOUT_SECONDS} seconds for script {code_filename_basename}."
            logger.error(timeout_msg)
            script_stderr_str += f"\n\n=== EXECUTOR TIMEOUT ===\n{timeout_msg}\n======================\n"
            executor_error = timeout_msg
            
        except DockerExecutionError as e:
            logger.error(f"DockerExecutionError during execution of {code_filename_basename}: {e.message}", exc_info=True)
            script_stderr_str += f"\n\n=== DOCKER EXECUTION ERROR ===\n{e.message}\n==========================\n"
            exit_code = e.exit_code if hasattr(e, 'exit_code') and e.exit_code is not None else 1
            executor_error = f"DockerExecutionError: {e.message}"

        except docker.errors.APIError as e:
            logger.error(f"Docker APIError during execution of {code_filename_basename}: {str(e)}", exc_info=True)
            script_stderr_str += f"\n\n=== DOCKER API ERROR ===\n{str(e)}\n======================\n"
            exit_code = 1
            executor_error = f"Docker APIError: {str(e)}"
            
        except Exception as e:
            error_msg = f"Unexpected error during code execution of {code_filename_basename}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            script_stderr_str += f"\n\n=== UNEXPECTED EXECUTOR ERROR ===\n{error_msg}\n===============================\n"
            exit_code = 1
            executor_error = error_msg
            
        execution_summary = self._create_execution_summary(
            code_filename_basename, script_stdout_str, script_stderr_str, exit_code, timed_out, executor_error
        )
        
        # --- BEGIN SANDBOX /tmp CLEANUP ---
        if container: # Only if we had a container to begin with
            try:
                logger.info(f"Attempting to clean execution directory {sandbox_execution_tmp_dir} in sandbox container: {container.name} after script execution.")
                cleanup_cmd = ["rm", "-rf", str(sandbox_execution_tmp_dir)]
                cleanup_exec_result = await asyncio.to_thread(
                    container.exec_run,
                    cmd=cleanup_cmd,
                    stdout=True,
                    stderr=True
                )
                if cleanup_exec_result.exit_code == 0:
                    logger.info(f"Successfully cleaned {sandbox_execution_tmp_dir} in sandbox container: {container.name}.")
                else:
                    cleanup_stderr = cleanup_exec_result.output[1].decode(errors='replace') if cleanup_exec_result.output[1] else 'None'
                    logger.warning(f"Failed to clean {sandbox_execution_tmp_dir} in sandbox container {container.name}. Exit: {cleanup_exec_result.exit_code}. Stderr: {cleanup_stderr}")
            except Exception as e_cleanup:
                logger.error(f"Error during sandbox execution directory cleanup for {sandbox_execution_tmp_dir} in container {container.name}: {e_cleanup}", exc_info=True)
        # --- END SANDBOX /tmp CLEANUP ---
        
        return execution_summary, script_stdout_str, script_stderr_str, exit_code, retrieved_file_basenames

    def is_available(self) -> bool:
        return (self.docker_client is not None and 
                self.python_sandbox_container_name is not None)