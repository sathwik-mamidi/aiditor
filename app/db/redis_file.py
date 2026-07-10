from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import json
from pydantic import ValidationError, BaseModel
from uuid import uuid4

from app.utils.logger import logger
from app.config.config import config
from app.db.redis_client import get_redis_client, get_model_from_redis
from app.db.redis_models import FileRecord

FILE_PREFIX = config.get("REDIS_KEY_PREFIXES", {}).get("FILE")
CHAT_FILES_PREFIX = config.get("REDIS_KEY_PREFIXES", {}).get("CHAT_FILES")
PENDING_FILES_PREFIX = config.get("REDIS_KEY_PREFIXES", {}).get("PENDING_FILES")
S3_KEY_INDEX_PREFIX = config.get("REDIS_KEY_PREFIXES", {}).get("S3_KEY_INDEX")
USER_FILES_PREFIX = config.get("REDIS_KEY_PREFIXES", {}).get("USER_FILES")

if not all([FILE_PREFIX, CHAT_FILES_PREFIX, PENDING_FILES_PREFIX, S3_KEY_INDEX_PREFIX, USER_FILES_PREFIX]):
    logger.critical("CRITICAL: One or more Redis key prefixes are not defined in the configuration.")

S3_KEY_INDEX_GLOBAL_KEY = f"{S3_KEY_INDEX_PREFIX}global" if S3_KEY_INDEX_PREFIX else "s3_key_idx:global"

ASSISTANT_SOURCES = {"assistant_generated_code", "assistant_generated_log", "sandbox_output", "script_generated_output"}

async def create_file(chat_id: Optional[str], file_data: dict) -> Optional[FileRecord]:
    logger.debug(f"[create_file] Called with chat_id: '{chat_id}', original_filename: '{file_data.get('original_filename')}', source: '{file_data.get('source')}'")
    redis_client = await get_redis_client()
    
    generated_file_id = str(uuid4())
    current_chat_id = chat_id 

    if 's3_key' not in file_data or not file_data['s3_key']:
        logger.error(f"[create_file] s3_key is missing or empty in file_data for potential file {file_data.get('original_filename')}. Cannot create file record.")
        return None

    file_record_data = {
        **file_data,
        "file_id": generated_file_id,
        "id": generated_file_id,
        "created_at": datetime.now(timezone.utc),
        "chat_id": current_chat_id
    }

    try:
        file_record = FileRecord(**file_record_data)
    except Exception as e:
        logger.error(f"[create_file] Failed to validate file data for {file_data.get('original_filename') or file_data.get('s3_key')}. Error: {e}", exc_info=True)
        return None

    file_key = f"{FILE_PREFIX}{generated_file_id}"
    
    s3_key_for_index = file_record.s3_key # s3_key MUST be present now
    if not s3_key_for_index: 
        logger.error(f"[create_file] s3_key is missing from FileRecord for file_id {generated_file_id} after instantiation. This should not happen. Cannot create s3_key index.")
    
    redis_hash_data = file_record.to_redis()

    try:
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.hset(file_key, mapping=redis_hash_data)
            if s3_key_for_index: 
                pipe.hset(S3_KEY_INDEX_GLOBAL_KEY, s3_key_for_index, generated_file_id)
            
            if current_chat_id:
                chat_files_key = f"{CHAT_FILES_PREFIX}{current_chat_id}"
                pipe.sadd(chat_files_key, generated_file_id) # Always add to general chat files list (Set)
                
                # Only add to pending_files list (List) if it's not from an assistant source.
                # file_record.source comes from file_data['source'] which is part of file_record_data.
                if not any(file_record.source.startswith(src_prefix) for src_prefix in ASSISTANT_SOURCES):
                    pending_files_key = f"{PENDING_FILES_PREFIX}{current_chat_id}"
                    pipe.rpush(pending_files_key, generated_file_id)
                    logger.debug(f"[create_file] Added file_id '{generated_file_id}' to PENDING list '{pending_files_key}' (source: '{file_record.source}')")
                else:
                    logger.debug(f"[create_file] SKIPPED adding file_id '{generated_file_id}' to PENDING list for chat '{current_chat_id}' (source: '{file_record.source}')")
            else:
                logger.debug(f"[create_file] No current_chat_id, SKIPPED adding file_id '{generated_file_id}' to any PENDING list.")
            
            results = await pipe.execute()
            
            if not results or not all(res for res in results):
                 logger.warning(f"[create_file] Some pipeline operations might have failed for filename '{s3_key_for_index}'. Results: {results}")

        return file_record
    except Exception as e:
        logger.error(f"[create_file] Failed to create file record in Redis for filename '{s3_key_for_index}'. Error: {e}", exc_info=True)
        return None

async def get_file(file_id: str) -> Optional[FileRecord]:
    file_key = f"{FILE_PREFIX}{file_id}"
    record = await get_model_from_redis(FileRecord, file_key)
    return record

async def delete_file(
    user_id: str,
    chat_id: str,
    file_id: str 
) -> bool:
    """
    Deletes a file record from Redis and its associated index entries.
    Uses file_id (UUID) to identify the file.
    """
    redis_client = await get_redis_client()
    
    # Get the FileRecord to find the S3 key for index deletion
    file_record = await get_file_by_id(actual_file_id=file_id, user_id=user_id, chat_id=chat_id)
    if not file_record:
        logger.warning(f"File with ID {file_id} not found or access denied for user {user_id} / chat {chat_id} during delete attempt.")
        return False # File not found or not authorized

    # The S3 key is stored in file_record.s3_key
    s3_key_for_index_removal = file_record.s3_key
    if not s3_key_for_index_removal:
        logger.error(f"File record {file_id} does not have an s3_key for index removal.")

    file_key = f"{FILE_PREFIX}{file_id}"
    user_files_key = f"{USER_FILES_PREFIX}{user_id}"
    chat_files_key = f"{CHAT_FILES_PREFIX}{chat_id}"
    pending_files_key = f"{PENDING_FILES_PREFIX}{chat_id}"

    async with redis_client.pipeline(transaction=True) as pipe:
        pipe.delete(file_key)
        if s3_key_for_index_removal: # Ensure we have a field to delete from the hash
            pipe.hdel(S3_KEY_INDEX_GLOBAL_KEY, s3_key_for_index_removal)
        pipe.srem(user_files_key, file_id) # Store file_id (UUID) in sets
        pipe.srem(chat_files_key, file_id) # Store file_id (UUID) in sets
        pipe.lrem(pending_files_key, 0, file_id) # Remove from pending files list
        results = await pipe.execute()

    deleted_count = results[0] if results and len(results) > 0 else 0
    
    if s3_key_for_index_removal and len(results) > 1:
        hdel_result = results[1] # Result of HDEL
        if hdel_result == 1:
            logger.info(f"Successfully removed '{s3_key_for_index_removal}' from global index '{S3_KEY_INDEX_GLOBAL_KEY}'.")
        elif hdel_result == 0:
            logger.warning(f"Field '{s3_key_for_index_removal}' not found in global index '{S3_KEY_INDEX_GLOBAL_KEY}' during delete for file {file_id}.")
        # else: something unexpected, but we proceed based on file_key deletion

    # Check LREM result (adjust index based on whether HDEL was run)
    lrem_result_index = -1
    if s3_key_for_index_removal:
        if len(results) > 4 : # delete, hdel, srem, srem, lrem
             lrem_result_index = 4
    elif len(results) > 3: # delete, srem, srem, lrem
        lrem_result_index = 3
    
    if lrem_result_index != -1 and len(results) > lrem_result_index :
        lrem_val = results[lrem_result_index]
        if isinstance(lrem_val, int):
            if lrem_val > 0:
                logger.info(f"Successfully removed {lrem_val} instance(s) of file_id {file_id} from pending list {pending_files_key}.")
            else:
                logger.info(f"File_id {file_id} not found in pending list {pending_files_key} for LREM operation.")
        else:
            logger.warning(f"Unexpected LREM result type for {pending_files_key}, file_id {file_id}: {lrem_val}")

    if deleted_count > 0:
        logger.info(f"Successfully deleted file record {file_key} and its indices.")
        return True
    else:
        logger.warning(f"File record {file_key} might not have been deleted (delete command returned 0). It may also indicate the key didn't exist.") # Added clarification
        return False

async def get_pending_files(chat_id: str) -> List[FileRecord]:
    redis_client = await get_redis_client()
    pending_files_key = f"{PENDING_FILES_PREFIX}{chat_id}"
    file_ids = await redis_client.lrange(pending_files_key, 0, -1)
    
    files = []
    for file_id in file_ids:
        file_record = await get_file(file_id)
        if file_record:
            files.append(file_record)
        else:
            await redis_client.lrem(pending_files_key, 0, file_id)
            
    return files

async def clear_pending_files(chat_id: str) -> bool:
    redis_client = await get_redis_client()
    pending_files_key = f"{PENDING_FILES_PREFIX}{chat_id}"
    logger.debug(f"[clear_pending_files] Attempting to delete key: '{pending_files_key}'")
    result = await redis_client.delete(pending_files_key)
    deleted = result > 0
    logger.debug(f"[clear_pending_files] Key '{pending_files_key}' deletion result: {result} (Success: {deleted})")
    return deleted

async def get_file_by_s3_key(s3_key: str) -> Optional[FileRecord]: # Renamed function and parameter
    redis_client = await get_redis_client()
    
    file_id_from_redis = await redis_client.hget(S3_KEY_INDEX_GLOBAL_KEY, s3_key) # Updated here
    
    if not file_id_from_redis:
        logger.warning(f"[get_file_by_s3_key] S3 key '{s3_key}' not found in index '{S3_KEY_INDEX_GLOBAL_KEY}'.") # Updated here
        return None
        
    if isinstance(file_id_from_redis, bytes):
        file_id = file_id_from_redis.decode('utf-8')
    elif isinstance(file_id_from_redis, str):
        file_id = file_id_from_redis
    else:
        logger.error(f"[get_file_by_s3_key] Unexpected type for file_id from Redis: {type(file_id_from_redis)}. S3 key: '{s3_key}'") # Updated log message
        return None

    return await get_file(file_id)

async def update_file_thumbnail(file_id: str, thumbnail_s3_key_val: str) -> bool:
    redis_client = await get_redis_client()
    file_key = f"{FILE_PREFIX}{file_id}"
    
    try:
        # Update to use "thumbnail_s3_key"
        await redis_client.hset(file_key, "thumbnail_s3_key", thumbnail_s3_key_val)
        exists = await redis_client.exists(file_key)
        if exists > 0:
            logger.info(f"Successfully updated thumbnail_s3_key for file {file_id}.")
            return True
        else:
            logger.warning(f"File {file_id} not found when trying to update thumbnail_s3_key.")
            return False
    except Exception as e:
        logger.error(f"Error updating thumbnail_s3_key for file {file_id}: {e}", exc_info=True)
        return False

async def update_file_metadata(file_id: str, metadata_update: Dict[str, Any]) -> bool:
    redis_client = await get_redis_client()
    file_key = f"{FILE_PREFIX}{file_id}"

    redis_update_dict = {}
    for key, value in metadata_update.items():
        if value is None:
            continue

        if isinstance(value, bool):
            redis_update_dict[key] = str(value).lower()
        elif isinstance(value, BaseModel):
            redis_update_dict[key] = value.model_dump_json()
        elif key == 'dimensions' and isinstance(value, dict):
            redis_update_dict[key] = json.dumps(value)
        elif key == 'animation_info' and isinstance(value, dict):
            redis_update_dict[key] = json.dumps(value)
        elif key == 'dpi' and isinstance(value, tuple):
            redis_update_dict[key] = f"{int(value[0])}x{int(value[1])}"
        elif isinstance(value, datetime):
            redis_update_dict[key] = value.isoformat()
        else:
            redis_update_dict[key] = str(value)

    if not redis_update_dict:
        return True

    try:
        await redis_client.hset(file_key, mapping=redis_update_dict)
        exists = await redis_client.exists(file_key)
        return exists > 0
    except Exception as e:
        logger.error(f"Error updating metadata for file {file_id}: {e}")
        return False

async def get_file_by_id(
    actual_file_id: str,
    user_id: str,
    chat_id: Optional[str] = None
) -> Optional[FileRecord]:
    """
    Retrieves a file record from Redis by its unique file_id (UUID).
    Also verifies ownership if user_id and optionally chat_id are provided.
    """
    if not actual_file_id:
        return None

    redis_client = await get_redis_client()
    file_key = f"{FILE_PREFIX}{actual_file_id}"
    
    logger.debug(f"[get_file_by_id] Attempting to fetch file with key: {file_key}") # Log attempt
    file_data = await redis_client.hgetall(file_key)

    if not file_data:
        logger.warning(f"[get_file_by_id] File record not found in Redis for key: {file_key}") # Log not found
        return None

    logger.debug(f"[get_file_by_id] Raw data from Redis for {file_key}: {file_data}") # Log raw data

    try:
        decoded_file_data = file_data 
        logger.debug(f"[get_file_by_id] Data for {actual_file_id} (assumed already decoded): {decoded_file_data}")
        
        # Pydantic v2 expects an 'id' field. If it's missing, use 'file_id'.
        if 'id' not in decoded_file_data and 'file_id' in decoded_file_data:
            decoded_file_data['id'] = decoded_file_data['file_id']
            logger.debug(f"[get_file_by_id] Added 'id' field from 'file_id' for Pydantic validation: {decoded_file_data['id']}")

        # Attempt to parse dimensions if it's a JSON string
        if 'dimensions' in decoded_file_data and isinstance(decoded_file_data['dimensions'], str):
            try:
                decoded_file_data['dimensions'] = json.loads(decoded_file_data['dimensions'])
                logger.debug(f"[get_file_by_id] Parsed 'dimensions' field from JSON string for {actual_file_id}")
            except json.JSONDecodeError:
                logger.warning(f"[get_file_by_id] Failed to parse 'dimensions' JSON string: {decoded_file_data['dimensions']} for {actual_file_id}. Setting to None.")
                decoded_file_data['dimensions'] = None

        if 'user_id' not in decoded_file_data or not decoded_file_data['user_id']:
            logger.warning(f"[get_file_by_id] File record {actual_file_id} missing 'user_id' field in Redis data. Data: {decoded_file_data}")
            return None

        # Log IDs before comparison
        stored_user_id = decoded_file_data.get('user_id')
        stored_chat_id = decoded_file_data.get('chat_id')
        logger.debug(f"[get_file_by_id] Comparing provided user_id '{user_id}' with stored user_id '{stored_user_id}' for file {actual_file_id}.")
        if chat_id:
            logger.debug(f"[get_file_by_id] Comparing provided chat_id '{chat_id}' with stored chat_id '{stored_chat_id}' for file {actual_file_id}.")

        file_record = FileRecord(**decoded_file_data)

        # Verify ownership (already logged above for direct comparison)
        if file_record.user_id != user_id:
            logger.warning(f"[get_file_by_id] User ID mismatch for file {actual_file_id}. Provided: '{user_id}', Stored: '{file_record.user_id}'. Access denied.")
            return None
        
        # If chat_id is provided, verify that as well
        if chat_id and file_record.chat_id and file_record.chat_id != chat_id:
            logger.warning(f"[get_file_by_id] Chat ID mismatch for file {actual_file_id}. Provided: '{chat_id}', Stored: '{file_record.chat_id}'. Access denied.")
            return None
            
        logger.debug(f"[get_file_by_id] Successfully fetched and verified file record for {actual_file_id}")
        return file_record
    except ValidationError as e:
        logger.error(f"Validation error for file {actual_file_id}: {e}. Data: {file_data}")
        return None
    except Exception as e:
        logger.error(f"Error processing file record {actual_file_id}: {e}. Data: {file_data}")
        return None

async def get_user_files(user_id: str, chat_id: Optional[str] = None) -> List[FileRecord]:
    redis_client = await get_redis_client()
    user_files_key = f"{USER_FILES_PREFIX}{user_id}"
    file_ids = await redis_client.smembers(user_files_key)
    
    files = []
    for file_id in file_ids:
        file_record = await get_file(file_id)
        if file_record:
            files.append(file_record)
        else:
            await redis_client.srem(user_files_key, file_id)
            
    return files