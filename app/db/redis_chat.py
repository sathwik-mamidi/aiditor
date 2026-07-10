from datetime import datetime, timezone
from typing import Optional, List
from redis.asyncio import Redis
import json
import uuid

from app.utils.logger import logger
from app.config.config import config
from app.db.redis_client import get_redis_client, get_model_from_redis
from app.db.redis_models import RedisChat, ConversationTurn
from app.db.redis_file import delete_file as delete_file_record_from_redis, get_file_by_id
from app.services.file_manager import FileManager

CHAT_PREFIX = config["REDIS_KEY_PREFIXES"]["CHAT"]
USER_CHATS_PREFIX = config["REDIS_KEY_PREFIXES"]["USER_CHATS"]
CHAT_FILES_PREFIX = config["REDIS_KEY_PREFIXES"]["CHAT_FILES"]
PENDING_FILES_PREFIX = config["REDIS_KEY_PREFIXES"]["PENDING_FILES"]

MAX_CHAT_NAME_LENGTH = 30
DEFAULT_CHAT_NAME_WORDS = 5

async def get_user_id_for_chat(redis: Redis, chat_id: str) -> Optional[str]:
    if not redis or not chat_id:
        return None
        
    try:
        redis_key = f"{CHAT_PREFIX}{chat_id}"
        return await redis.hget(redis_key, "user_id")
    except Exception as e:
        logger.error(f"Redis error fetching user_id for chat {chat_id}: {e}")
        return None

async def create_chat(user_id: str, chat_id_override: Optional[str] = None) -> RedisChat:
    redis_client = await get_redis_client()
    now = datetime.now(timezone.utc)

    chat_id = chat_id_override if chat_id_override else str(uuid.uuid4())

    new_chat = RedisChat(
        chat_id=chat_id,
        user_id=user_id,
        created_at=now,
        updated_at=now,
        conversations=[]
    )

    chat_key = f"{CHAT_PREFIX}{chat_id}"
    user_chats_key = f"{USER_CHATS_PREFIX}{user_id}"

    try:
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.hset(chat_key, mapping=new_chat.to_redis())
            pipe.sadd(user_chats_key, chat_id)
            results = await pipe.execute()
            
        if not all(results):
            raise IOError(f"Failed to create chat record for {chat_id}")
            
        return new_chat
    except Exception as e:
        logger.error(f"Error creating chat {chat_id}: {e}")
        raise

async def get_chat(chat_id: str) -> Optional[RedisChat]:
    chat_key = f"{CHAT_PREFIX}{chat_id}"
    return await get_model_from_redis(RedisChat, chat_key)

async def update_chat_conversations(chat_id: str, conversations: List[ConversationTurn]) -> Optional[RedisChat]:
    redis_client = await get_redis_client()
    chat_key = f"{CHAT_PREFIX}{chat_id}"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    current_chat_name = await redis_client.hget(chat_key, "chat_name")
    new_chat_name = None
    
    if not current_chat_name:
        for turn in conversations:
            if turn.role == 'user' and turn.prompt:
                prompt_words = turn.prompt.split()
                name_from_prompt = " ".join(prompt_words[:DEFAULT_CHAT_NAME_WORDS])
                
                if len(name_from_prompt) > MAX_CHAT_NAME_LENGTH:
                    name_from_prompt = name_from_prompt[:MAX_CHAT_NAME_LENGTH].strip() + "..."
                elif len(prompt_words) > DEFAULT_CHAT_NAME_WORDS:
                    name_from_prompt += "..."

                if name_from_prompt:
                    new_chat_name = name_from_prompt
                    break

    try:
        cleaned_turn_dicts = []
        for turn in conversations:
            turn_dict = {
                "role": turn.role,
                "timestamp": turn.timestamp.isoformat() if isinstance(turn.timestamp, datetime) else str(turn.timestamp)
            }
            
            if turn.role == 'user':
                if turn.prompt is not None: 
                    turn_dict["prompt"] = turn.prompt
                if turn.input_files: 
                    turn_dict["input_files"] = turn.input_files
            elif turn.role == 'assistant':
                if turn.response is not None: 
                    turn_dict["response"] = turn.response
                if turn.output_files: 
                    turn_dict["output_files"] = turn.output_files
                if turn.api_costs is not None: 
                    turn_dict["api_costs"] = turn.api_costs
            
            cleaned_turn_dicts.append(turn_dict)

        conversations_json = json.dumps(cleaned_turn_dicts)
        update_mapping = {
            "conversations": conversations_json,
            "updated_at": now_iso
        }

        if new_chat_name:
            update_mapping["chat_name"] = new_chat_name

        await redis_client.hset(chat_key, mapping=update_mapping)
        return await get_chat(chat_id)
        
    except Exception as e:
        logger.error(f"Failed to update conversations for chat {chat_id}: {e}")
        return None

async def fetch_user_chats_from_db(user_id: str) -> List[RedisChat]:
    redis_client = await get_redis_client()
    user_chats_key = f"{USER_CHATS_PREFIX}{user_id}"
    
    chat_ids = await redis_client.smembers(user_chats_key)
    chats = []
    
    for chat_id_member in chat_ids:
        chat = await get_chat(chat_id_member)
        if chat:
            chats.append(chat)
        else:
            await redis_client.srem(user_chats_key, chat_id_member)
            
    return chats

async def delete_chat(user_id: str, chat_id: str, file_manager: FileManager) -> bool:
    redis_client = await get_redis_client()
    
    chat_key = f"{CHAT_PREFIX}{chat_id}"
    user_chats_key = f"{USER_CHATS_PREFIX}{user_id}"
    chat_files_key = f"{CHAT_FILES_PREFIX}{chat_id}"
    pending_files_key = f"{PENDING_FILES_PREFIX}{chat_id}"

    chat_data = await redis_client.hgetall(chat_key)
    if not chat_data:
        return False
        
    stored_user_id = chat_data.get("user_id")
    if stored_user_id != user_id:
        return False

    file_ids_to_delete = await redis_client.smembers(chat_files_key)
    pending_file_ids = await redis_client.lrange(pending_files_key, 0, -1)
    all_file_ids = set(file_ids_to_delete) | set(pending_file_ids)

    for file_id_str in all_file_ids:
        try:
            # Get the full file record to access S3 keys
            file_record = await get_file_by_id(actual_file_id=file_id_str, user_id=user_id, chat_id=chat_id)

            if file_record:
                # Delete main file from S3
                if file_record.s3_key:
                    s3_main_deleted, error = await file_manager.delete_file_from_s3(file_record.s3_key)
                    if not s3_main_deleted:
                        logger.error(f"Failed to delete main S3 file {file_record.s3_key} for file ID {file_id_str} during chat {chat_id} deletion. Error: {error}")
                
                # Delete thumbnail from S3
                if file_record.thumbnail_s3_key:
                    s3_thumb_deleted, error = await file_manager.delete_file_from_s3(file_record.thumbnail_s3_key)
                    if not s3_thumb_deleted:
                        logger.error(f"Failed to delete S3 thumbnail {file_record.thumbnail_s3_key} for file ID {file_id_str} during chat {chat_id} deletion. Error: {error}")
            else:
                logger.warning(f"File record {file_id_str} not found or access denied during chat {chat_id} deletion, cannot delete from S3.")

            # Delete file record from Redis
            success_redis = await delete_file_record_from_redis(user_id=user_id, chat_id=chat_id, file_id=file_id_str)
            if not success_redis:
                logger.warning(f"Failed to delete file record {file_id_str} from Redis associated with chat {chat_id} during chat deletion.")
        except Exception as e:
            logger.error(f"Error processing file {file_id_str} during chat {chat_id} deletion: {e}", exc_info=True)

    try:
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.delete(chat_key)
            pipe.srem(user_chats_key, chat_id)
            pipe.delete(chat_files_key)
            pipe.delete(pending_files_key)
            results = await pipe.execute()

        return results[0] > 0
    except Exception as e:
        logger.error(f"Error deleting chat {chat_id}: {e}")
        return False