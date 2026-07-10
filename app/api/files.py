from fastapi import APIRouter, File, UploadFile, Form, Depends, HTTPException, BackgroundTasks, status, Query
from typing import List, Optional, Dict
import pathlib
from pathlib import Path
import urllib.parse # Added for URL decoding

from app.config.config import config
from app.utils.logger import logger
from app.db.redis_chat import create_chat
from app.db.redis_file import create_file, delete_file as delete_file_from_redis, get_file_by_id, get_file_by_s3_key
from app.db.redis_models import format_file_for_api, FileRecord
from app.models import User, UploadResponse, DeleteFileResponse
from app.dependencies import verify_authenticated_session, get_file_manager
from app.services.file_manager import FileManager
from app.tasks.file_processing_tasks import process_s3_file_metadata_and_thumbnail

router = APIRouter()

@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    chat_id: Optional[str] = Form(None),
    current_user: User = Depends(verify_authenticated_session),
    file_manager: FileManager = Depends(get_file_manager)
):
    user_id = current_user.user_id
    current_chat_id = chat_id

    if not file_manager.s3_client:
        logger.error(f"User {user_id} attempted upload but S3 is not configured.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File storage service is not available."
        )

    if not current_chat_id:
        new_chat = await create_chat(user_id)
        if not new_chat:
            logger.error(f"User {user_id}: Failed to create new chat during upload.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to initialize chat for upload."
            )
        current_chat_id = new_chat.chat_id
        logger.info(f"Created new chat via upload: {current_chat_id} for user {user_id}")

    processed_file_records: List[FileRecord] = []
    for file in files:
        if not file.filename:
            logger.warn(f"User {user_id}, Chat {current_chat_id}: Received upload with no filename.")
            continue

        original_name = file.filename
        
        # Get file size
        file_size = file.size if file.size is not None else 0

        if file_size == 0:
            # If size is 0 or not provided by header, try to determine it by reading.
            logger.warn(f"User {user_id}, Chat {current_chat_id}: File '{original_name}' has size 0 or Content-Length missing. Attempting to read size.")
            contents = await file.read()
            file_size = len(contents)
            await file.seek(0) # Reset pointer after read
            logger.debug(f"User {user_id}, Chat {current_chat_id}: Determined size for '{original_name}' by reading: {file_size} bytes.")
        else:
            # If size was obtained from header, still ensure pointer is at start for S3 upload
            await file.seek(0)

        if file_size == 0:
            logger.warn(f"User {user_id}, Chat {current_chat_id}: Received empty file: {original_name}")

        s3_key, error_message = await file_manager.save_fastapi_upload_to_s3(
            file=file, 
            user_id=user_id,
            chat_id=current_chat_id,
        )

        if error_message or not s3_key:
            logger.error(f"User {user_id}, Chat {current_chat_id}: Failed to upload {original_name} to S3: {error_message}")
            continue

        file_ext = pathlib.Path(original_name).suffix[1:].lower() if pathlib.Path(original_name).suffix else None
        
        file_create_data = {
            "original_filename": original_name,
            "file_type": file_ext,
            "file_size": file_size,
            "mime_type": file.content_type or "application/octet-stream",
            "dimensions": None,
            "duration": None,
            "thumbnail_path": None,
            "api_filename": None,
            "s3_key": s3_key,
            "user_id": user_id,
            "chat_id": current_chat_id,
            "source": "user_upload"
        }

        try:
            created_file_record = await create_file(current_chat_id, file_create_data)
            if created_file_record:
                processed_file_records.append(created_file_record)
                logger.info(f"User {user_id}, Chat {current_chat_id}: Successfully created file record for S3 object {s3_key} (original: {original_name})")
                background_tasks.add_task(
                    process_s3_file_metadata_and_thumbnail,
                    s3_key=s3_key,
                    file_record_id=created_file_record.file_id,
                    file_manager=file_manager
                )
            else:
                logger.error(f"User {user_id}, Chat {current_chat_id}: Failed to create file record for S3 object: {s3_key} (original: {original_name})")
        except Exception as e:
            logger.error(f"User {user_id}, Chat {current_chat_id}: Error creating file record for {original_name} (S3 key: {s3_key}): {e}")

    formatted_files = [format_file_for_api(rec) for rec in processed_file_records if format_file_for_api(rec)]

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files provided.")

    if not processed_file_records and files:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Could not process any of the uploaded files. Please try again later."
        )

    return UploadResponse(
        message="Files processed. Some uploads may have failed if S3 interaction encountered issues.",
        chat_id=current_chat_id,
        files=formatted_files
    )

@router.delete("/upload/{filename:path}", response_model=DeleteFileResponse)
async def delete_uploaded_file(
    filename: str,
    current_user: User = Depends(verify_authenticated_session),
    file_manager: FileManager = Depends(get_file_manager)
):
    user_id = current_user.user_id
    
    # URL-decode the S3 key received from the path
    decoded_s3_key = urllib.parse.unquote(filename)
    logger.info(f"User {user_id}: Attempting to delete file with decoded S3 key: '{decoded_s3_key}'")

    # Fetch the file record using the S3 key
    file_record = await get_file_by_s3_key(s3_key=decoded_s3_key)

    if not file_record:
        logger.warning(f"User {user_id}: File record with S3 key '{decoded_s3_key}' not found for deletion.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    # Explicit permission check: Ensure the current user owns the file
    if file_record.user_id != user_id:
        logger.warning(f"User {user_id} attempted to delete file {file_record.file_id} (S3 key: {decoded_s3_key}) owned by user {file_record.user_id}. Permission denied.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this file.")

    # Ensure we have the primary S3 key from the record to delete
    main_s3_key_to_delete = file_record.s3_key
    if not main_s3_key_to_delete:
        logger.error(f"User {user_id}: File record {file_record.file_id} is missing its primary S3 key. Cannot delete main file from S3.")
        # Depending on policy, you might still want to delete the Redis record
        raise HTTPException(status_code=500, detail="File record is incomplete (missing S3 key).")

    # Attempt to delete the main file from S3
    success_s3_main = await file_manager.delete_file_from_s3(main_s3_key_to_delete)
    if not success_s3_main:
        logger.error(f"User {user_id}: Failed to delete main S3 file '{main_s3_key_to_delete}'. Proceeding with other deletions.")
        # Don't raise immediately, try to clean up as much as possible

    # Attempt to delete the thumbnail from S3 if it exists
    success_s3_thumbnail = True # Default to true if no thumbnail to delete
    if file_record.thumbnail_s3_key:
        success_s3_thumbnail = await file_manager.delete_file_from_s3(file_record.thumbnail_s3_key)
        if not success_s3_thumbnail:
            logger.error(f"User {user_id}: Failed to delete S3 thumbnail '{file_record.thumbnail_s3_key}'.")
            # Log error but continue

    # Delete the file record from Redis using its file_id (UUID)
    success_redis = await delete_file_from_redis(
        user_id=user_id, # current_user.user_id
        chat_id=file_record.chat_id, # Pass chat_id from the fetched record
        file_id=file_record.file_id 
    )
    
    if not success_redis:
        logger.error(f"User {user_id}: S3 deletions attempted (main success: {success_s3_main}, thumb success: {success_s3_thumbnail}), but FAILED to delete Redis record for file ID {file_record.file_id}.")
        # This is a more critical state as S3 objects might be gone but the record persists
        raise HTTPException(status_code=500, detail="File deleted from storage, but failed to update database record. Please contact support.")

    logger.info(f"User {user_id}: Successfully processed deletion for file ID {file_record.file_id} (Original S3 key: {decoded_s3_key}). Main S3 del: {success_s3_main}, Thumb S3 del: {success_s3_thumbnail}, Redis del: {success_redis}")
    return DeleteFileResponse(
        message="File deletion processed. Associated record removed.",
        deleted_file_id=file_record.file_id
    )

@router.get("/url/{file_id}", response_model=Dict[str, str])
async def get_s3_presigned_url_route(
    file_id: str,
    user: User = Depends(verify_authenticated_session),
    file_manager: FileManager = Depends(get_file_manager),
    thumbnail: bool = Query(False, description="Request URL for thumbnail instead of main file"),
    download: bool = Query(False, description="Request URL for download with Content-Disposition")
):
    """Generate a pre-signed S3 URL for a given file_id (or its thumbnail)."""
    logger.debug(f"[get_s3_presigned_url_route] Entered. file_id from path: {file_id}, thumbnail_query: {thumbnail}, download_query: {download}")
    
    actual_file_id = file_id 

    logger.debug(f"[get_s3_presigned_url_route] About to call get_file_by_id with actual_file_id='{actual_file_id}', user_id='{user.user_id}'")
    file_record = await get_file_by_id(actual_file_id, user.user_id)

    if not file_record:
        logger.warning(f"[get_s3_presigned_url_route] File not found for ID: {actual_file_id}, User: {user.user_id}")
        raise HTTPException(status_code=404, detail="File not found or access denied.")

    if not file_manager.s3_client:
        logger.error("[get_s3_presigned_url_route] S3 client not available.")
        raise HTTPException(status_code=503, detail="S3 service not configured.")

    logger.debug(f"[get_s3_presigned_url_route] For file_id {actual_file_id}: thumbnail_query={thumbnail}, record_has_thumb_key='{file_record.thumbnail_s3_key}', record_s3_key='{file_record.s3_key}'")
    s3_key_to_use = file_record.thumbnail_s3_key if thumbnail and file_record.thumbnail_s3_key else file_record.s3_key
    logger.debug(f"[get_s3_presigned_url_route] For file_id {actual_file_id}: Determined s3_key_to_use='{s3_key_to_use}'")
    
    if not s3_key_to_use:
        detail_msg = "Thumbnail S3 key not found." if thumbnail else "S3 key not found for this file."
        logger.warning(f"[get_s3_presigned_url_route] {detail_msg} File ID: {actual_file_id}")
        raise HTTPException(status_code=404, detail=detail_msg)

    # Determine the filename for Content-Disposition and API response
    resolved_download_filename = ""

    # Simplified logic: Prioritize original_filename, then s3_key basename
    if file_record.original_filename:
        resolved_download_filename = file_record.original_filename
    
    if not resolved_download_filename and file_record.s3_key: # Check if still empty and s3_key exists
        resolved_download_filename = Path(file_record.s3_key).name
    
    # Absolute fallback to "file" if both are somehow missing or empty
    if not resolved_download_filename or not resolved_download_filename.strip():
        resolved_download_filename = "file"

    logger.debug(f"[get_s3_presigned_url_route] Resolved download filename: '{resolved_download_filename}' for S3 key: '{s3_key_to_use}' (Source: {file_record.source})")

    params = {
        'Bucket': config["S3_BUCKET_NAME"],
        'Key': s3_key_to_use
    }

    if download:
        safe_filename = resolved_download_filename.replace('"', '_').replace('\'', '_').replace(';', '_')
        params['ResponseContentDisposition'] = f'attachment; filename="{safe_filename}"'
        logger.debug(f"[get_s3_presigned_url_route] Setting Content-Disposition for download: filename='{safe_filename}'")


    try:
        url = file_manager.s3_client.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=config.get("S3_PRESIGNED_URL_EXPIRY_SECONDS", 18000) # Default to 5 hours if not set
        )
        logger.info(f"[get_s3_presigned_url_route] Generated presigned URL for S3 key: {s3_key_to_use}")
    except Exception as e:
        logger.error(f"[get_s3_presigned_url_route] Error generating presigned URL for {s3_key_to_use}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not generate file access URL.")

    return {"url": url, "filename": resolved_download_filename}