from google import genai
from google.genai import types as genai_types
import asyncio
import time
from typing import Optional, Tuple
from pathlib import Path
from contextlib import asynccontextmanager
import os
import boto3
from botocore.exceptions import ClientError
from fastapi import UploadFile
from uuid import uuid4
import tempfile
import mimetypes
from datetime import datetime, timezone

from app.config.config import config
from app.utils.logger import logger
from app.db.redis_client import get_redis_client
from app.db.redis_models import FileRecord
from app.db.redis_file import get_file_by_s3_key, create_file
from app.utils import media_utils

FILE_PREFIX = config["REDIS_KEY_PREFIXES"]["FILE"]


class FileUploadError(Exception):
    def __init__(self, message: str, filename: str, error: Optional[Exception] = None):
        self.message = message
        self.filename = filename
        self.error = error
        super().__init__(f"{message} (File: {filename})")


class FileManager:
    SUPPORTED_MIME_TYPES = [
        # Images
        "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif",
        # Videos
        "video/mp4", "video/mpeg", "video/mov", "video/avi", "video/x-flv", 
        "video/mpg", "video/webm", "video/wmv", "video/3gpp",
        # Audio
        "audio/wav", "audio/mp3", "audio/aiff", "audio/aac", "audio/ogg", "audio/flac",
        # Documents & Text (aligned with GenAI supported types)
        "application/pdf",
        "application/x-javascript", "text/javascript", # JavaScript
        "application/x-python", "text/x-python",       # Python
        "text/plain",                                   # TXT
        "text/html",                                    # HTML
        "text/css",                                     # CSS
        "text/markdown", "text/md",                     # Markdown
        "text/csv",                                     # CSV
        "application/xml", "text/xml",                  # XML
        "application/rtf", "text/rtf"                   # RTF
    ]
    
    DEFAULT_MAX_WAIT_SECONDS = 120
    DEFAULT_INITIAL_POLL_INTERVAL = 3
    DEFAULT_MAX_POLL_INTERVAL = 15
    DEFAULT_BACKOFF_FACTOR = 1.5

    def __init__(self, genai_client: genai.Client):
        self.client = genai_client
        self.use_vertex = config.get("GOOGLE_GENAI_USE_VERTEXAI", False)
        if not self.use_vertex and not genai_client:
            raise ValueError("GenAI client must be provided when not using Vertex AI.")

        self.files_dir_host = Path(config["FILES_DIR"])
        self.redis_prefix = FILE_PREFIX

        # S3 Configuration
        self.s3_bucket_name = config.get("S3_BUCKET_NAME")
        logger.debug(f"FileManager init: Attempting to read S3_BUCKET_NAME. Value from config: '{self.s3_bucket_name}'")
        self.aws_region = config.get("AWS_DEFAULT_REGION", "us-east-1") # Default if not set
        logger.debug(f"FileManager init: AWS Region from config: '{self.aws_region}'")

        if not self.s3_bucket_name:
            logger.warning("FileManager init: S3_BUCKET_NAME not configured or empty. S3 operations will be disabled.")
            self.s3_client = None
        else:
            logger.info(f"FileManager init: S3_BUCKET_NAME is '{self.s3_bucket_name}'. Attempting to initialize S3 client.")
            try:
                self.s3_client = boto3.client(
                    "s3",
                    region_name=self.aws_region
                    # Credentials will be picked up from environment (local or EC2 IAM role)
                )
                logger.info(f"FileManager init: S3 client initialized successfully. Bucket: {self.s3_bucket_name}, Region: {self.aws_region}")
            except Exception as e:
                logger.error(f"FileManager init: Failed to initialize S3 client for bucket '{self.s3_bucket_name}': {e}", exc_info=True)
                self.s3_client = None

    def _generate_s3_key(self, user_id: str, chat_id: str, file_category: str, original_filename: str) -> str:
        file_ext = Path(original_filename).suffix.lower()
        unique_filename_stem = str(uuid4())
        
        # New structure: chat_data/user_id/chat_id/file_category/uuid.ext
        return f"chat_data/{user_id}/{chat_id}/{file_category}/{unique_filename_stem}{file_ext}"

    async def save_fastapi_upload_to_s3(
        self, 
        file: UploadFile, 
        user_id: str,
        chat_id: str,
        s3_metadata: Optional[dict] = None,
        s3_key_override: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Uploads a FastAPI UploadFile to S3.
        Returns a tuple: (s3_object_key, error_message). 
        s3_object_key is None if upload fails.
        """
        if not self.s3_client or not self.s3_bucket_name:
            msg = "S3 client not initialized or bucket name not configured."
            logger.error(msg)
            return None, msg

        if not file.filename:
            msg = "Cannot upload file to S3: filename is missing."
            logger.error(msg)
            return None, msg

        if s3_key_override:
            s3_key_to_use = s3_key_override
        else:
            s3_key_to_use = self._generate_s3_key(
                user_id=user_id,
                chat_id=chat_id,
                file_category="uploads",
                original_filename=file.filename
            )
        
        extra_args = {}
        if file.content_type:
            extra_args['ContentType'] = file.content_type
        if s3_metadata:
            extra_args['Metadata'] = {str(k): str(v) for k, v in s3_metadata.items()}

        try:
            await file.seek(0) # Ensure stream is at the beginning
            
            # boto3 s3 client methods are synchronous, run in a thread
            await asyncio.to_thread(
                self.s3_client.upload_fileobj,
                file.file,
                self.s3_bucket_name,
                s3_key_to_use,
                ExtraArgs=extra_args if extra_args else None
            )
            logger.info(f"File '{file.filename}' uploaded to S3. Bucket: '{self.s3_bucket_name}', Key: '{s3_key_to_use}'.")
            return s3_key_to_use, None
        except ClientError as e:
            logger.error(f"S3 ClientError uploading '{file.filename}' to key '{s3_key_to_use}': {e}")
            return None, str(e)
        except Exception as e:
            logger.error(f"Unexpected error uploading '{file.filename}' to S3 key '{s3_key_to_use}': {e}")
            return None, str(e)

    async def download_file_from_s3_to_temp(self, s3_key: str) -> Optional[str]:
        """
        Downloads a file from S3 to a local temporary file.
        Returns the path to the temporary file, or None if download fails.
        The caller is responsible for deleting the temporary file.
        """
        if not self.s3_client or not self.s3_bucket_name:
            logger.error("S3 client not initialized or bucket name not configured. Cannot download from S3.")
            return None

        temp_file_path = None
        fd = -1 # Initialize file descriptor to an invalid value

        try:
            # Create a temporary file with a name, ensuring it's deleted if an error occurs
            # Suffix can be derived from s3_key if needed for type hinting some tools
            original_suffix = Path(s3_key).suffix
            fd, temp_file_path = tempfile.mkstemp(suffix=original_suffix)
            
            logger.debug(f"Attempting to download s3://{self.s3_bucket_name}/{s3_key} to temp path {temp_file_path}")

            with open(temp_file_path, 'wb') as f_out:
                await asyncio.to_thread(
                    self.s3_client.download_fileobj,
                    self.s3_bucket_name,
                    s3_key,
                    f_out
                )
            
            logger.info(f"File s3://{self.s3_bucket_name}/{s3_key} downloaded to temporary path {temp_file_path}")
            return temp_file_path
        except ClientError as e:
            logger.error(f"S3 ClientError downloading key '{s3_key}': {e}")
            if temp_file_path and Path(temp_file_path).exists():
                try: Path(temp_file_path).unlink()
                except OSError as unlink_e: logger.error(f"Error deleting temp file {temp_file_path} after S3 download error: {unlink_e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error downloading key '{s3_key}' from S3: {e}")
            if temp_file_path and Path(temp_file_path).exists():
                try: Path(temp_file_path).unlink()
                except OSError as unlink_e: logger.error(f"Error deleting temp file {temp_file_path} after unexpected download error: {unlink_e}")
            return None
        finally:
            if fd != -1: # If mkstemp was successful
                try:
                    os.close(fd)
                except OSError as close_e:
                    logger.error(f"Error closing file descriptor for temp file {temp_file_path}: {close_e}")
    
    async def delete_file_from_s3(self, s3_key: str) -> Tuple[bool, Optional[str]]:
        """
        Deletes a file from S3.
        Returns a tuple: (success_status, error_message).
        success_status is False if deletion fails.
        """
        if not self.s3_client or not self.s3_bucket_name:
            msg = "S3 client not initialized or bucket name not configured. Cannot delete from S3."
            logger.error(msg)
            return False, msg

        try:
            logger.info(f"Attempting to delete s3://{self.s3_bucket_name}/{s3_key}")
            await asyncio.to_thread(
                self.s3_client.delete_object,
                Bucket=self.s3_bucket_name,
                Key=s3_key
            )
            logger.info(f"Successfully deleted s3://{self.s3_bucket_name}/{s3_key}")
            return True, None
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == 'NoSuchKey':
                logger.warning(f"Attempted to delete non-existent key '{s3_key}' from S3 bucket '{self.s3_bucket_name}'. Considering as success.")
                return True, None # If key doesn't exist, it's effectively 'deleted'
            logger.error(f"S3 ClientError deleting key '{s3_key}': {e}")
            return False, str(e)
        except Exception as e:
            logger.error(f"Unexpected error deleting key '{s3_key}' from S3: {e}")
            return False, str(e)

    async def upload_local_file_to_s3(
        self,
        local_file_path: str,
        s3_key: str,
        content_type: Optional[str] = None,
        s3_metadata: Optional[dict] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Uploads a local file to S3.
        Returns a tuple: (s3_object_key, error_message). 
        s3_object_key is None if upload fails (it will be the provided s3_key on success).
        """
        if not self.s3_client or not self.s3_bucket_name:
            msg = "S3 client not initialized or bucket name not configured."
            logger.error(msg)
            return None, msg

        if not Path(local_file_path).is_file():
            msg = f"Local file not found for S3 upload: {local_file_path}"
            logger.error(msg)
            return None, msg
        
        extra_args = {}
        if content_type:
            extra_args['ContentType'] = content_type
        if s3_metadata:
            extra_args['Metadata'] = {str(k): str(v) for k, v in s3_metadata.items()}

        try:
            with open(local_file_path, "rb") as f:
                await asyncio.to_thread(
                    self.s3_client.upload_fileobj,
                    f, # File-like object
                    self.s3_bucket_name,
                    s3_key,
                    ExtraArgs=extra_args if extra_args else None
                )
            logger.info(f"Local file '{local_file_path}' uploaded to S3. Bucket: '{self.s3_bucket_name}', Key: '{s3_key}'.")
            return s3_key, None
        except ClientError as e:
            logger.error(f"S3 ClientError uploading local file '{local_file_path}' to key '{s3_key}': {e}")
            return None, str(e)
        except Exception as e:
            logger.error(f"Unexpected error uploading local file '{local_file_path}' to S3 key '{s3_key}': {e}")
            return None, str(e)

    async def upload_file_if_needed(
        self, 
        s3_key: str,
        chat_id: Optional[str] = None,
        file_content_type_override: Optional[str] = None
    ) -> Optional[genai_types.File]:
        """
        DEPRECATED FOR VERTEX AI. This uploads a file to the GenAI File API.
        For Vertex AI, use `get_file_part_for_vertex` instead.
        """
        if self.use_vertex:
            logger.warning("`upload_file_if_needed` was called in Vertex AI mode. This function is deprecated for Vertex. Returning None.")
            return None
        
        log_prefix = f"[Chat {chat_id}] " if chat_id else ""
        
        logger.info(f"{log_prefix}Attempting to ensure file (S3 Key: {s3_key}) is uploaded to GenAI.")

        file_record = await self._get_file_record_by_s3_key(s3_key)
        
        if not file_record:
            logger.error(f"{log_prefix}No FileRecord found for S3 key '{s3_key}'. Cannot upload to GenAI.")
            return None

        # Check if already uploaded and active on GenAI API
        if file_record.api_filename:
            existing_api_file = await self._check_existing_file(file_record, chat_id)
            if existing_api_file:
                logger.info(f"{log_prefix}File {s3_key} (API: {file_record.api_filename}) already active on GenAI.")
                return existing_api_file
        
        # Determine mime_type from FileRecord, with override
        mime_type_to_use = file_content_type_override or file_record.mime_type or "application/octet-stream"
            
        if not self._check_mime_type_support(mime_type_to_use):
            logger.warning(
                f"{log_prefix}MIME type {mime_type_to_use} for file (S3 key: {s3_key}) "
                f"is not in the extended list of GenAI supported MIME types. "
                f"Upload will proceed, but ensure this type is compatible with GenAI."
            )
        
        # Download file from S3 to a temporary local path to upload to GenAI
        if not file_record.s3_key:
            logger.error(f"{log_prefix}FileRecord {file_record.file_id} for {s3_key} has no s3_key. Cannot download from S3.")
            return None

        if not self.s3_client:
            logger.error(f"{log_prefix}S3 client not available. Cannot download {file_record.s3_key} for GenAI upload.")
            return None

        local_temp_s3_file_path: Optional[str] = None
        api_file: Optional[genai_types.File] = None
        active_file: Optional[genai_types.File] = None

        try:
            local_temp_s3_file_path = await self.download_file_from_s3_to_temp(file_record.s3_key)
            if not local_temp_s3_file_path:
                logger.error(f"{log_prefix}Failed to download S3 file {file_record.s3_key} for GenAI upload.")
                return None

            logger.info(f"{log_prefix}Uploading {file_record.s3_key} (downloaded to {local_temp_s3_file_path}, mime: {mime_type_to_use}) to GenAI API...")
            # _upload_to_api expects a Path object and uses its path for the GenAI API, 
            # but the MIME type for GenAI upload must be passed to client.files.upload config.
            # Modify _upload_to_api to accept mime_type
            api_file = await self._upload_to_api(Path(local_temp_s3_file_path), mime_type_to_use)
            
            if not api_file:
                logger.error(f"{log_prefix}Failed to upload {file_record.s3_key} (from {local_temp_s3_file_path}) to GenAI API.")
                return None
                
            logger.info(f"{log_prefix}Uploaded S3 file {file_record.s3_key} to GenAI as {api_file.name}, waiting for ACTIVE state...")
            active_file = await self._wait_for_file_active(api_file.name, chat_id)
            
            if not active_file:
                logger.error(f"{log_prefix}GenAI file {api_file.name} (from S3 {file_record.s3_key}) failed to become ACTIVE.")
                # Attempt to delete the failed GenAI file resource if it exists and is in a FAILED state
                # This might involve checking its state again or just attempting delete.
                # For now, we log and return. Consider adding cleanup for failed GenAI uploads.
                return None
                
            # Update FileRecord with the new GenAI API filename
            await self._update_api_filename(file_record, active_file.name)
            return active_file
            
        except Exception as e:
            logger.error(f"{log_prefix}Unexpected error during GenAI upload for S3 key {s3_key}: {e}", exc_info=True)
            return None
        finally:
            # Clean up the temporary downloaded file
            if local_temp_s3_file_path and Path(local_temp_s3_file_path).exists():
                try:
                    os.unlink(local_temp_s3_file_path)
                    logger.info(f"{log_prefix}Deleted temporary S3 file copy: {local_temp_s3_file_path}")
                except OSError as e_unlink:
                    logger.error(f"{log_prefix}Error deleting temporary S3 file copy {local_temp_s3_file_path}: {e_unlink}")

    async def process_sandbox_output_file(
        self, 
        local_output_path_str: str, 
        user_id: str,
        chat_id: str,
        original_input_s3_key: Optional[str] = None 
    ) -> Optional[FileRecord]:
        if not self.s3_client or not self.s3_bucket_name:
            logger.error("S3 client not initialized or bucket name not configured. Cannot process sandbox output to S3.")
            return None

        local_output_path = Path(local_output_path_str)
        if not local_output_path.exists() or not local_output_path.is_file():
            logger.error(f"Sandbox output file not found or is not a file: {local_output_path_str}")
            return None

        # Generate S3 key for the sandbox output using the new structure
        output_s3_key = self._generate_s3_key(
            user_id=user_id,
            chat_id=chat_id,
            file_category="assistant_outputs",
            original_filename=local_output_path.name
        )
        
        s3_metadata_for_output = {
            "user_id": user_id,
            "chat_id": chat_id,
            "source": "script_generated_output"
        }
        if original_input_s3_key:
            s3_metadata_for_output["original_input_s3_key"] = original_input_s3_key

        content_type, _ = mimetypes.guess_type(local_output_path.name)
        guessed_mime_type = content_type or "application/octet-stream"
        guessed_primary_type = guessed_mime_type.split('/')[0] if '/' in guessed_mime_type else guessed_mime_type
        
        # Upload the sandbox output to S3
        uploaded_key, error_msg = await self.upload_local_file_to_s3(
            local_file_path=str(local_output_path),
            s3_key=output_s3_key,
            content_type=guessed_mime_type,
            s3_metadata=s3_metadata_for_output
        )

        if not uploaded_key:
            logger.error(f"Failed to upload sandbox output {local_output_path.name} to S3 (key: {output_s3_key}): {error_msg}")
            # Optionally, decide if local file should be deleted even if S3 upload fails
            # For now, we'll proceed to delete it as per original logic's intent
        else:
            logger.info(f"Sandbox output file {local_output_path.name} uploaded to S3 key: {uploaded_key}")
        
        # Create FileRecord in Redis
        actual_original_filename = local_output_path.name # This is the filename from the sandbox
        generated_file_id = str(uuid4()) 
        
        logger.debug(f"[FileManager] Preparing FileRecord. actual_original_filename: {actual_original_filename}, s3_key for record: {uploaded_key}")

        file_record_data = {
            "id": generated_file_id, 
            "file_id": generated_file_id, 
            "original_filename": actual_original_filename,
            "s3_key": uploaded_key, 
            "user_id": user_id,
            "chat_id": chat_id,
            "status": "uploaded",
            "file_type": guessed_primary_type,
            "mime_type": guessed_mime_type,
            "size": local_output_path.stat().st_size,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "source": "script_generated_output"
        }
        
        try:
            created_file_record = await create_file(chat_id=chat_id, file_data=file_record_data)
            if not created_file_record:
                logger.error(f"Failed to create FileRecord in Redis (create_file returned None) for sandbox output {actual_original_filename}, s3_key: {uploaded_key}")
                try:
                    local_output_path.unlink()
                    logger.info(f"Cleaned up local sandbox output file after Redis creation failure: {local_output_path_str}")
                except OSError as e_unlink:
                    logger.error(f"Error deleting local sandbox output file {local_output_path_str} after Redis creation failure: {e_unlink}")
                return None
            
            logger.info(f"FileRecord created in Redis for sandbox output: {actual_original_filename}, s3_key: {uploaded_key}, record_id: {created_file_record.file_id}")
            file_record = created_file_record
        except Exception as e:
            logger.error(f"Exception during call to create_file for sandbox output {actual_original_filename} (s3_key: {uploaded_key}): {e}", exc_info=True)
            
            try:
                local_output_path.unlink()
                logger.info(f"Cleaned up local sandbox output file: {local_output_path_str}")
            except OSError as e_unlink:
                logger.error(f"Error deleting local sandbox output file {local_output_path_str}: {e_unlink}")
            return None


        try:
            local_output_path.unlink()
            logger.info(f"Cleaned up local sandbox output file: {local_output_path_str}")
        except OSError as e:
            logger.error(f"Error deleting local sandbox output file {local_output_path_str}: {e}")

        return file_record 

    @asynccontextmanager
    async def _redis_client(self):
        client = await get_redis_client()
        try:
            yield client
        finally:
            pass

    async def _get_file_record_by_s3_key(self, s3_key: str) -> Optional[FileRecord]:
        try:
            return await get_file_by_s3_key(s3_key)
        except Exception as e:
            logger.error(f"Error retrieving file record for s3_key {s3_key}: {e}")
            return None

    async def _update_api_filename(self, file_record: FileRecord, api_name: str) -> bool:
        try:
            async with self._redis_client() as r:
                await r.hset(
                    f"{self.redis_prefix}{file_record.file_id}",
                    "api_filename",
                    api_name
                )
                logger.info(f"Updated API filename to '{api_name}' for file ID {file_record.file_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to update api_filename in Redis for {file_record.file_id}: {e}")
            return False

    async def _clear_api_filename(self, file_record: FileRecord) -> bool:
        try:
            async with self._redis_client() as r:
                await r.hdel(
                    f"{self.redis_prefix}{file_record.file_id}",
                    "api_filename"
                )
                logger.info(f"Cleared API filename for file ID {file_record.file_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to clear api_filename in Redis for {file_record.file_id}: {e}")
            return False

    async def _get_file_state(self, api_filename: str) -> Tuple[Optional[genai_types.File], Optional[str]]:
        try:
            api_file = await self.client.aio.files.get(name=api_filename)
            state = getattr(api_file, 'state', None)
            state_str = str(state) if state is not None else "None"
            return api_file, state_str
        except Exception as e:
            if "not found" in str(e).lower():
                logger.warning(f"File '{api_filename}' not found on API")
            else:
                logger.error(f"Error getting file state for '{api_filename}': {e}")
            return None, None

    async def _wait_for_file_active(
        self, 
        api_filename: str,
        chat_id: Optional[str],
        max_wait_seconds: int = DEFAULT_MAX_WAIT_SECONDS
    ) -> Optional[genai_types.File]:
        start_time = time.time()
        poll_interval = self.DEFAULT_INITIAL_POLL_INTERVAL
        last_state = None
        attempt = 0
        
        context = f"[Chat {chat_id}]" if chat_id else ""

        while (time.time() - start_time) < max_wait_seconds:
            attempt += 1
            api_file, state_str = await self._get_file_state(api_filename)
            
            if api_file is None:
                logger.error(f"{context} Failed to get file state during polling (attempt {attempt})")
                await asyncio.sleep(min(poll_interval, self.DEFAULT_MAX_POLL_INTERVAL))
                poll_interval *= self.DEFAULT_BACKOFF_FACTOR
                continue
                
            last_state = state_str
            logger.debug(f"{context} File {api_filename} polling (attempt {attempt}): state = {state_str}")
            
            if state_str == str(genai_types.FileState.ACTIVE):
                logger.info(f"{context} File {api_filename} is now ACTIVE")
                return api_file
            elif state_str == str(genai_types.FileState.FAILED):
                logger.error(f"{context} File {api_filename} processing FAILED")
                return None
            
            poll_interval = min(poll_interval * self.DEFAULT_BACKOFF_FACTOR, self.DEFAULT_MAX_POLL_INTERVAL)
            await asyncio.sleep(poll_interval)
        
        logger.error(
            f"{context} Timeout waiting for file {api_filename} to become ACTIVE "
            f"after {max_wait_seconds} seconds. Last state: {last_state or 'Unknown'}"
        )
        return None

    async def _upload_to_api(self, file_path: Path, mime_type: Optional[str] = None) -> Optional[genai_types.File]:
        try:
            upload_config = {}
            if mime_type:
                upload_config['mime_type'] = mime_type
            
            final_config_for_upload = genai_types.UploadFileConfig()
            if mime_type:
                final_config_for_upload.mime_type = mime_type

            return await asyncio.to_thread(
                self.client.files.upload,
                file=file_path,
                config=final_config_for_upload if mime_type else None 
            )
        except Exception as e:
            logger.error(f"Error uploading file {file_path.name} to GenAI API: {e}", exc_info=True)
            return None

    async def _check_existing_file(
        self, 
        file_record: FileRecord, 
        chat_id: Optional[str]
    ) -> Optional[genai_types.File]:
        context = f"[Chat {chat_id}]" if chat_id else ""
        
        if not file_record or not file_record.api_filename:
            return None
            
        api_filename = file_record.api_filename

        logger.debug(f"{context} Found existing API filename '{api_filename}' for file (S3 key: {file_record.s3_key}). Checking status...")
        
        api_file, state_str = await self._get_file_state(api_filename)
        
        if api_file is None:
            logger.warning(f"{context} File {api_filename} exists in DB but not found on API. Clearing DB reference.")
            await self._clear_api_filename(file_record)
            return None
        
        if state_str == str(genai_types.FileState.ACTIVE):
            logger.debug(f"{context} File {api_filename} is already ACTIVE on API.")
            return api_file
        elif state_str == str(genai_types.FileState.PROCESSING):
            logger.debug(f"{context} File {api_filename} is currently PROCESSING. Waiting...")
            return await self._wait_for_file_active(api_filename, chat_id)
        else:
            logger.warning(f"{context} File {api_filename} is in state {state_str or 'Unknown'} on API. Attempting re-upload.")
            await self._delete_api_file(api_filename)
            await self._clear_api_filename(file_record)
            return None

    async def _delete_api_file(self, api_filename: str) -> bool:
        try:
            await self.client.aio.files.delete(name=api_filename)
            logger.info(f"Deleted file {api_filename} from API")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete file {api_filename} from API: {e}")
            return False

    def _check_mime_type_support(self, mime_type: str) -> bool:
        return mime_type in self.SUPPORTED_MIME_TYPES

    def generate_presigned_s3_url(
        self, 
        s3_key: str, 
        expiration: int = 3600, 
        for_upload: bool = False, 
        http_method: Optional[str] = None, 
        content_type: Optional[str] = None,
        response_content_disposition: Optional[str] = None # New parameter
    ) -> Optional[str]:
        if not self.s3_client:
            logger.error("S3 client not initialized, cannot generate presigned URL.")
            return None

        params = {'Bucket': self.s3_bucket_name, 'Key': s3_key}
        
        actual_http_method = http_method if http_method else ('PUT' if for_upload else 'GET')

        if actual_http_method == 'PUT' and content_type:
            params['ContentType'] = content_type
        
        if actual_http_method == 'GET' and response_content_disposition:
            params['ResponseContentDisposition'] = response_content_disposition

        try:
            url = self.s3_client.generate_presigned_url(
                ClientMethod='get_object' if actual_http_method == 'GET' else 'put_object',
                Params=params,
                ExpiresIn=expiration,
                HttpMethod=actual_http_method
            )
            logger.info(f"Generated presigned URL for S3 key '{s3_key}' (expires in {expiration}s): {url if len(url) < 150 else url[:150] + '...'}")
            return url
        except ClientError as e:
            logger.error(f"Failed to generate presigned URL for S3 key '{s3_key}': {e}", exc_info=True)
            return None

    async def get_file_part_for_vertex(self, s3_key: str, chat_id: Optional[str] = None) -> Optional[genai_types.Part]:
        """
        Downloads a file from S3 and prepares it as a genai.types.Part for a Vertex AI request.
        """
        log_prefix = f"[Chat {chat_id}] " if chat_id else ""
        logger.info(f"{log_prefix}Preparing file part for Vertex from S3 key: {s3_key}")

        temp_file_path = await self.download_file_from_s3_to_temp(s3_key)
        if not temp_file_path:
            logger.error(f"{log_prefix}Failed to download {s3_key} from S3.")
            return None

        try:
            # Determine MIME type
            mime_type, _ = mimetypes.guess_type(temp_file_path)
            if not mime_type:
                # Fallback for unknown types
                mime_type = "application/octet-stream"
                logger.warning(f"{log_prefix}Could not guess MIME type for {temp_file_path}. Defaulting to {mime_type}.")

            # Read file bytes
            with open(temp_file_path, "rb") as f:
                file_bytes = f.read()

            logger.info(f"{log_prefix}Successfully read {len(file_bytes)} bytes from {temp_file_path} (MIME: {mime_type}).")
            
            # Create a Part object
            return genai_types.Part(inline_data=genai_types.Blob(mime_type=mime_type, data=file_bytes))

        except Exception as e:
            logger.error(f"{log_prefix}Error processing temp file {temp_file_path} for Vertex: {e}", exc_info=True)
            return None
        finally:
            # Clean up the temporary file
            if Path(temp_file_path).exists():
                try:
                    Path(temp_file_path).unlink()
                    logger.debug(f"{log_prefix}Cleaned up temporary file: {temp_file_path}")
                except OSError as unlink_e:
                    logger.error(f"{log_prefix}Error deleting temp file {temp_file_path}: {unlink_e}")