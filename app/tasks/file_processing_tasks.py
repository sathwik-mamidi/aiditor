from pathlib import Path
import os
from typing import Optional, Dict, Any

from app.services.file_manager import FileManager
from app.utils import media_utils 
from app.db.redis_file import update_file_metadata 
from app.utils.logger import logger

async def process_s3_file_metadata_and_thumbnail(
    s3_key: str,
    file_record_id: str,
    file_manager: FileManager
):
    """
    Background task to download a file from S3, extract metadata, 
    generate & upload a thumbnail (if applicable), and update Redis.
    """
    logger.info(f"[BG Task] Starting S3 metadata/thumbnail processing for s3_key: {s3_key}, record_id: {file_record_id}")

    local_temp_file_path: Optional[str] = None
    local_temp_thumbnail_path: Optional[str] = None
    uploaded_thumbnail_s3_key: Optional[str] = None

    if not file_manager or not file_manager.s3_client:
        logger.error(f"[BG Task] FileManager not available or S3 client not configured. Aborting for {s3_key}.")
        return

    try:
        # 1. Download file from S3 to a temporary location
        local_temp_file_path = await file_manager.download_file_from_s3_to_temp(s3_key)
        if not local_temp_file_path:
            logger.error(f"[BG Task] Failed to download {s3_key} from S3. Aborting.")
            return

        # 2. Get detailed file info using media_utils
        logger.debug(f"[BG Task] Getting media info for temp file: {local_temp_file_path} (from S3 key {s3_key})")
        media_info = await media_utils.get_file_info(local_temp_file_path)
        if not media_info or media_info.get("error"):
            logger.error(f"[BG Task] Failed to get media info for {local_temp_file_path} (S3: {s3_key}). Error: {media_info.get('error', 'Unknown')}")
            # Still proceed to update what we can, or decide to return
        
        metadata_to_update: Dict[str, Any] = {
            "file_size": media_info.get("size"), # Re-confirm size from downloaded file
            "mime_type": media_info.get("type", "application/octet-stream"),
            "dimensions": media_info.get("dimensions"), # Expects dict or FileDimensions model
            "color_mode": media_info.get("color_mode"),
            "bit_depth": media_info.get("bit_depth"),
            "dpi": media_info.get("dpi"), # Expects tuple e.g. (300,300) or string from model
            "has_alpha": media_info.get("has_alpha"),
            "duration": media_info.get("duration"),
            "video_codec": media_info.get("video_codec"),
            "audio_codec": media_info.get("audio_codec"),
            "frame_rate": media_info.get("frame_rate"),
            "bitrate_kbps": media_info.get("bitrate_kbps"),
            "has_audio": media_info.get("has_audio"),
            "sample_rate": media_info.get("sample_rate"),
            "channels": media_info.get("channels"),
            # New image-specific fields from media_utils
            "animation_info": media_info.get("animation_info"),
        }
        
        # Convert FileDimensions if media_utils returns a dict
        if isinstance(metadata_to_update["dimensions"], dict):
             try:
                 # Assuming FileDimensions Pydantic model is available from redis_models
                 from app.db.redis_models import FileDimensions as PydanticFileDimensions
                 metadata_to_update["dimensions"] = PydanticFileDimensions(**metadata_to_update["dimensions"])
             except Exception: # Pydantic validation error or other
                 logger.warning(f"[BG Task] Could not convert dimensions dict to Pydantic model for {s3_key}. Setting to None.")
                 metadata_to_update["dimensions"] = None


        # 3. Generate thumbnail for videos
        if media_info.get("type", "").startswith("video/"):
            logger.debug(f"[BG Task] Generating thumbnail for video: {local_temp_file_path}")
            try:
                local_temp_thumbnail_path = await media_utils.generate_video_thumbnail(local_temp_file_path)
                if local_temp_thumbnail_path:
                    logger.debug(f"[BG Task] Local thumbnail generated: {local_temp_thumbnail_path}")
                    
                    s3_key_path = Path(s3_key)
                    # Expecting s3_key like: chat_data/user_id_val/chat_id_val/category/file.ext
                    # So, parts[0]=chat_data, parts[1]=user_id_val, parts[2]=chat_id_val
                    if len(s3_key_path.parts) < 3: 
                        logger.error(f"[BG Task] S3 key {s3_key} does not have enough parts (expected chat_data/user/chat/...). Path parts: {s3_key_path.parts}. Aborting thumbnail upload.")
                    else:
                        user_id_val = s3_key_path.parts[1] # Actual user_id
                        chat_id_val = s3_key_path.parts[2] # Actual chat_id
                        original_file_name_stem = Path(s3_key_path.name).stem # Stem of the actual filename
                        
                        thumbnail_s3_key = f"chat_data/{user_id_val}/{chat_id_val}/thumbnails/{original_file_name_stem}.jpg"
                        logger.debug(f"[BG Task] Uploading video thumbnail to S3: {thumbnail_s3_key}")
                        thumb_content_type = "image/jpeg"
                        
                        uploaded_thumbnail_s3_key, s3_thumb_error = await file_manager.upload_local_file_to_s3(
                            local_file_path=local_temp_thumbnail_path,
                            s3_key=thumbnail_s3_key,
                            content_type=thumb_content_type
                        )
                        if s3_thumb_error or not uploaded_thumbnail_s3_key:
                            logger.error(f"[BG Task] Failed to upload video thumbnail {local_temp_thumbnail_path} to S3 ({thumbnail_s3_key}): {s3_thumb_error}")
                        else:
                            metadata_to_update["thumbnail_s3_key"] = uploaded_thumbnail_s3_key
                            logger.info(f"[BG Task] Video thumbnail uploaded to S3: {uploaded_thumbnail_s3_key}")
                else:
                    logger.warning(f"[BG Task] Video thumbnail generation returned None for {local_temp_file_path}.")
            except Exception as e_vid_thumb:
                logger.error(f"[BG Task] Error generating video thumbnail for {local_temp_file_path}: {e_vid_thumb}", exc_info=True)
        
        elif media_info.get("type", "").startswith("image/"): # Check for image types
            logger.debug(f"[BG Task] Generating thumbnail for image: {local_temp_file_path}")
            try:
                local_temp_thumbnail_path = await media_utils.generate_image_thumbnail(local_temp_file_path)
                if local_temp_thumbnail_path:
                    logger.debug(f"[BG Task] Local image thumbnail generated: {local_temp_thumbnail_path}")

                    s3_key_path = Path(s3_key)
                    # Expecting s3_key like: chat_data/user_id_val/chat_id_val/category/file.ext
                    if len(s3_key_path.parts) < 3:
                        logger.error(f"[BG Task] S3 key {s3_key} does not have enough parts (expected chat_data/user/chat/...). Path parts: {s3_key_path.parts}. Aborting thumbnail upload.")
                    else:
                        user_id_val = s3_key_path.parts[1] # Actual user_id
                        chat_id_val = s3_key_path.parts[2] # Actual chat_id
                        original_file_name_stem = Path(s3_key_path.name).stem # Stem of the actual filename

                        thumbnail_s3_key = f"chat_data/{user_id_val}/{chat_id_val}/thumbnails/{original_file_name_stem}.jpg"
                        logger.debug(f"[BG Task] Uploading image thumbnail to S3: {thumbnail_s3_key}")
                        thumb_content_type = "image/jpeg"

                        uploaded_thumbnail_s3_key, s3_thumb_error = await file_manager.upload_local_file_to_s3(
                            local_file_path=local_temp_thumbnail_path,
                            s3_key=thumbnail_s3_key,
                            content_type=thumb_content_type
                        )
                        if s3_thumb_error or not uploaded_thumbnail_s3_key:
                            logger.error(f"[BG Task] Failed to upload image thumbnail {local_temp_thumbnail_path} to S3 ({thumbnail_s3_key}): {s3_thumb_error}")
                        else:
                            metadata_to_update["thumbnail_s3_key"] = uploaded_thumbnail_s3_key
                            logger.info(f"[BG Task] Image thumbnail uploaded to S3: {uploaded_thumbnail_s3_key}")
                else:
                    logger.warning(f"[BG Task] Image thumbnail generation returned None for {local_temp_file_path}.")
            except Exception as e_img_thumb:
                logger.error(f"[BG Task] Error generating image thumbnail for {local_temp_file_path}: {e_img_thumb}", exc_info=True)


        # 4. Update FileRecord in Redis
        # Remove None values before updating, as update_file_metadata might expect this
        final_metadata_update = {k: v for k, v in metadata_to_update.items() if v is not None}

        if final_metadata_update:
            logger.debug(f"[BG Task] Updating Redis record {file_record_id} with metadata: {final_metadata_update}")
            success = await update_file_metadata(file_record_id, final_metadata_update)
            if success:
                logger.info(f"[BG Task] Successfully updated metadata for file ID {file_record_id} in Redis.")
            else:
                logger.error(f"[BG Task] Failed to update metadata for file ID {file_record_id} in Redis.")
        else:
            logger.debug(f"[BG Task] No new metadata to update for Redis record {file_record_id}.")

    except Exception as e:
        logger.error(f"[BG Task] Unhandled exception during S3 metadata/thumbnail processing for {s3_key}: {e}", exc_info=True)
    finally:
        # 5. Clean up local temporary files
        if local_temp_file_path and Path(local_temp_file_path).exists():
            try:
                os.unlink(local_temp_file_path)
                logger.info(f"[BG Task] Deleted temporary downloaded file: {local_temp_file_path}")
            except OSError as e:
                logger.error(f"[BG Task] Error deleting temporary downloaded file {local_temp_file_path}: {e}")
        
        if local_temp_thumbnail_path and Path(local_temp_thumbnail_path).exists():
            try:
                os.unlink(local_temp_thumbnail_path)
                logger.info(f"[BG Task] Deleted temporary thumbnail file: {local_temp_thumbnail_path}")
            except OSError as e:
                logger.error(f"[BG Task] Error deleting temporary thumbnail file {local_temp_thumbnail_path}: {e}")