from typing import List, Dict, Any, Optional, Union, TypedDict
import traceback
from datetime import datetime

from app.utils.logger import logger
from app.db.redis_models import ConversationTurn, format_file_for_api
from app.db.redis_file import get_file_by_s3_key
from app.models import UserConversationMessage, AssistantConversationMessage, FileInfo


class FileDict(TypedDict, total=False):
    file_id: Optional[str]
    s3_key: str
    original_filename: str
    file_type: Optional[str]
    thumbnail_s3_key: Optional[str]
    metadata: Optional[Dict[str, Any]]


async def format_conversation_turns_for_api(
    turns: List[ConversationTurn]
) -> List[Dict[str, Any]]:
    conversations_list = []

    if not turns:
        return conversations_list

    for turn in turns:
        try:
            if turn.role == 'user':
                processed_input_files = await _process_file_list(turn.input_files)
                
                message = UserConversationMessage(
                    timestamp=_format_timestamp(turn.timestamp),
                    prompt=turn.prompt,
                    input_files=[FileInfo(**f) for f in processed_input_files if f]
                )
                turn_api_dict = message.model_dump(exclude_none=True)
                
            elif turn.role == 'assistant':
                processed_output_files = await _process_file_list(turn.output_files)

                message = AssistantConversationMessage(
                    timestamp=_format_timestamp(turn.timestamp),
                    response=turn.response,
                    output_files=[FileInfo(**f) for f in processed_output_files if f]
                )
                turn_api_dict = message.model_dump(exclude_none=True)
                
            else:
                logger.warning(f"Unknown role in conversation turn: {turn.role}")
                continue
                
            conversations_list.append(turn_api_dict)
            
        except Exception as e:
            logger.error(
                f"Error formatting conversation turn: {e}\n"
                f"Turn ID: {getattr(turn, 'id', 'unknown')}\n"
                f"Traceback: {traceback.format_exc()}"
            )
            continue

    return conversations_list


async def _process_file_list(files: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    result = []
    
    if not files:
        return result
        
    for file_dict_ref in files:
        if not file_dict_ref or not isinstance(file_dict_ref, dict):
            logger.warning(f"Invalid file data reference found: {file_dict_ref}")
            continue

        actual_s3_key_to_use = file_dict_ref.get('s3_key')
        
        if not actual_s3_key_to_use:
            logger.warning(f"Could not find 's3_key' in file reference: {file_dict_ref}")
            continue
            
        try:
            # Always use get_file_by_s3_key now
            file_record = await get_file_by_s3_key(s3_key=actual_s3_key_to_use) 
            if file_record:
                formatted_file_info = format_file_for_api(file_record)
                if formatted_file_info:
                    result.append(formatted_file_info)
                else:
                    logger.warning(f"format_file_for_api returned None for s3_key: {actual_s3_key_to_use}")
            else:
                logger.warning(f"No FileRecord found for s3_key: {actual_s3_key_to_use}")
        except Exception as e:
            logger.error(f"Error processing file with s3_key '{actual_s3_key_to_use}' in _process_file_list: {e}", exc_info=True)
            continue
        
    return result


def _format_timestamp(timestamp: Union[datetime, str]) -> str:
    if isinstance(timestamp, datetime):
        return timestamp.isoformat()
    return str(timestamp)