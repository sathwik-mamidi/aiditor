from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Dict, Any, Optional
import uuid # ADDED FOR UUID VALIDATION

from app.utils.logger import logger
from app.dependencies import verify_admin_user, get_file_manager
from app.models import User, ChatSummary # Removed DetailResponse as it's not used yet.
from app.db.redis_user import get_user_by_email, get_user # Corrected import
from app.db.redis_chat import fetch_user_chats_from_db, get_chat # Reusing existing functions
from app.db.redis_file import get_file as get_file_record, get_file_by_s3_key # Added get_file_record to get FileRecord by file_id
from app.utils.format_helpers import format_conversation_turns_for_api # Reusing
from app.db.redis_models import RedisChat, FileRecord # Added FileRecord
from app.services.file_manager import FileManager # Added FileManager

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(verify_admin_user)]
)

@router.get("/user-details-by-email", response_model=User) # Adjust response_model if needed
async def get_user_details_by_email(email: str, current_admin: User = Depends(verify_admin_user)):
    logger.info(f"Admin {current_admin.email} requesting details for user email: {email}")
    target_user = await get_user_by_email(email) # Using get_user_by_email
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return target_user

@router.get("/user-chats/{target_user_id}", response_model=List[ChatSummary])
async def get_user_chats_by_id(target_user_id: str, current_admin: User = Depends(verify_admin_user)):
    logger.info(f"Admin {current_admin.email} requesting chats for user ID: {target_user_id}")
    target_user_check = await get_user(target_user_id) # get_user is from app.db.redis_user
    if not target_user_check:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Target user with ID {target_user_id} not found.")
        
    chats = await fetch_user_chats_from_db(target_user_id)
    return [
        ChatSummary(
            chat_id=chat.chat_id,
            user_id=chat.user_id,
            created_at=chat.created_at.isoformat(),
            updated_at=chat.updated_at.isoformat(),
            chat_name=chat.chat_name,
        ) for chat in chats
    ]

@router.get("/chat-details/{target_chat_id}", response_model=Dict[str, Any])
async def get_chat_details_by_id(target_chat_id: str, current_admin: User = Depends(verify_admin_user)):
    logger.info(f"Admin {current_admin.email} requesting details for chat ID: {target_chat_id}")
    chat_data: Optional[RedisChat] = await get_chat(target_chat_id)
    if not chat_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    try:
        conversations_list = await format_conversation_turns_for_api(chat_data.conversations)
        response_dict = {
            "chat_id": chat_data.chat_id,
            "user_id": chat_data.user_id,
            "created_at": chat_data.created_at.isoformat(),
            "updated_at": chat_data.updated_at.isoformat(),
            "chat_name": chat_data.chat_name,
            "conversations": conversations_list
        }
        
        if response_dict["chat_name"] is None:
            del response_dict["chat_name"]

        return response_dict
    except Exception as e:
        logger.error(f"Error preparing admin chat data for response {target_chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Error preparing chat data for admin response.")

@router.get("/file-access-url/{file_identifier:path}", response_model=Dict[str, Optional[str]])
async def get_admin_file_access_url(
    file_identifier: str,
    current_admin: User = Depends(verify_admin_user),
    file_manager: FileManager = Depends(get_file_manager)
):
    logger.info(f"Admin {current_admin.email} requesting access URL for file_identifier: {file_identifier}")
    
    file_record: Optional[FileRecord] = None
    
    # Try to get by UUID first
    is_valid_uuid = False
    try:
        uuid.UUID(file_identifier)
        is_valid_uuid = True
    except ValueError:
        logger.debug(f"Identifier '{file_identifier}' is not a valid UUID format.")

    if is_valid_uuid:
        logger.debug(f"Attempting to fetch file record by UUID: {file_identifier}")
        file_record = await get_file_record(file_identifier)
    
    # If not found by UUID (or if it wasn't a UUID), try by S3 key
    if not file_record:
        logger.info(f"File record not found by UUID (or identifier was not a UUID) for '{file_identifier}', trying by S3 key.")
        file_record = await get_file_by_s3_key(file_identifier)

    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File record not found")

    if not file_record.s3_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File S3 key not found in record")

    inline_url = None
    download_url = None
    thumbnail_url = None
    
    if not file_manager.s3_client:
        logger.error(f"S3 client not available in FileManager. Cannot generate pre-signed URLs for file_id: {file_record.file_id}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="S3 service not configured or available.")

    inline_url = file_manager.generate_presigned_s3_url(
        s3_key=file_record.s3_key,
        expiration=3600,  # 1 hour
        for_upload=False
    )

    content_disposition_for_download = None
    effective_original_filename = file_record.original_filename

    if not effective_original_filename and file_record.s3_key:
        try:
            effective_original_filename = file_record.s3_key.split('/')[-1]
        except:
            pass # Keep it None if split fails

    if effective_original_filename:
        ascii_filename = effective_original_filename.encode('ascii', 'ignore').decode('ascii')
        if ascii_filename:
             content_disposition_for_download = f"attachment; filename=\"{ascii_filename}\""
        else:
             content_disposition_for_download = f"attachment; filename=\"file\""
    else:
        content_disposition_for_download = "attachment; filename=\"file\""

    logger.debug(f"[Admin Download URL] s3_key: {file_record.s3_key}, original_filename on record: {file_record.original_filename}, effective_original_filename for disposition: {effective_original_filename}, generated_content_disposition: {content_disposition_for_download}")

    download_url = file_manager.generate_presigned_s3_url(
        s3_key=file_record.s3_key,
        expiration=3600,  # 1 hour
        for_upload=False,
        response_content_disposition=content_disposition_for_download
    )

    if file_record.thumbnail_s3_key:
        thumbnail_url = file_manager.generate_presigned_s3_url(
            s3_key=file_record.thumbnail_s3_key,
            expiration=3600,  # 1 hour
            for_upload=False
        )
    
    if not inline_url or not download_url: # Both are important now
        logger.error(f"Failed to generate one or more pre-signed URLs for s3_key: {file_record.s3_key} (file_id: {file_record.file_id})")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate file access URLs")

    return {
        "file_id": file_record.file_id,
        "original_filename": effective_original_filename if effective_original_filename else file_record.s3_key, # Return derived or s3_key as fallback
        "mime_type": file_record.mime_type,
        "url": inline_url, # This will be used for display
        "download_url": download_url, # This will be used for the download button
        "thumbnail_url": thumbnail_url
    }