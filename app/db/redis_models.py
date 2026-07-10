import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import pathlib
import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError, EmailStr

from app.config.config import config

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"
DEFAULT_MIME_TYPE = "application/octet-stream"
DEFAULT_CREDITS = 100
DEFAULT_PLAN = "free"

logger = logging.getLogger(__name__)

class ConversationTurn(BaseModel):
    model_config = ConfigDict(
        json_encoders={datetime: lambda value: value.isoformat()}
    )

    role: str
    prompt: Optional[str] = None
    response: Optional[Dict[str, Any]] = None # Example: {"code": "s3_key_or_local_path", "log": "s3_key_or_local_path", "message": "User-facing message", "execution_status": "success"}
    input_files: List[Dict[str, Any]] = Field(default_factory=list)
    output_files: List[Dict[str, Any]] = Field(default_factory=list)
    api_costs: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class RedisChat(BaseModel):
    chat_id: str
    user_id: str
    conversations: List[ConversationTurn] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    chat_name: Optional[str] = None

    @classmethod
    def from_redis(cls, data: Dict[str, str]) -> Optional['RedisChat']:
        if not data:
            return None
        try:
            if 'conversations' in data and isinstance(data['conversations'], str):
                try:
                    conv_list = json.loads(data['conversations'])
                    validated_turns = []
                    for turn_data in conv_list:
                        try:
                            if 'type' in turn_data and 'role' not in turn_data:
                                turn_data['role'] = turn_data.pop('type')

                            response_value = turn_data.get('response')
                            parsed_response_dict = None

                            if response_value is not None:
                                if isinstance(response_value, dict):
                                    parsed_response_dict = response_value
                                elif isinstance(response_value, str):
                                    try:
                                        parsed = json.loads(response_value)
                                        if isinstance(parsed, dict):
                                            parsed_response_dict = parsed
                                    except json.JSONDecodeError:
                                        pass
                                
                            turn_data['response'] = parsed_response_dict

                            if turn_data.get('role') == 'user':
                                turn_data.pop('response', None)

                            if 'input_files' in turn_data and not isinstance(turn_data['input_files'], list):
                                turn_data['input_files'] = []
                            if 'output_files' in turn_data and not isinstance(turn_data['output_files'], list):
                                turn_data['output_files'] = []

                            validated_turns.append(ConversationTurn(**turn_data))
                        except ValidationError:
                            continue
                    data['conversations'] = validated_turns
                except (json.JSONDecodeError, TypeError):
                    data['conversations'] = []
            else:
                data['conversations'] = []

            for key in ['created_at', 'updated_at']:
                if key in data and isinstance(data[key], str):
                    try:
                        data[key] = datetime.fromisoformat(data[key])
                    except ValueError:
                        data[key] = datetime.now(timezone.utc)

            return cls(**data)
        except (ValidationError, Exception):
            return None

    def to_redis(self) -> Dict[str, str]:
        redis_data = self.model_dump(exclude={'conversations'}, exclude_none=True, mode='json')

        serializable_turns = []
        for turn in self.conversations:
            try:
                turn_dict = turn.model_dump(exclude_none=True)
                if 'timestamp' in turn_dict and isinstance(turn_dict['timestamp'], datetime):
                    turn_dict['timestamp'] = turn_dict['timestamp'].isoformat()
                serializable_turns.append(turn_dict)
            except Exception:
                continue

        try:
            redis_data['conversations'] = json.dumps(serializable_turns)
        except TypeError:
            redis_data['conversations'] = json.dumps([])

        final_redis_data = {}
        for key, value in redis_data.items():
            final_redis_data[key] = str(value) if not isinstance(value, str) else value

        return final_redis_data

class FileDimensions(BaseModel):
    width: int
    height: int

class FileRecord(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={
            datetime: lambda value: value.isoformat(),
            FileDimensions: lambda value: json.dumps(value.model_dump()) if value else None,
        },
    )

    file_id: str = Field(alias='id')
    chat_id: Optional[str] = None
    original_filename: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = DEFAULT_MIME_TYPE
    dimensions: Optional[FileDimensions] = None
    color_mode: Optional[str] = None
    bit_depth: Optional[int] = None
    dpi: Optional[str] = None
    has_alpha: Optional[bool] = None
    duration: Optional[float] = None
    video_codec: Optional[str] = None
    frame_rate: Optional[float] = None
    bitrate_kbps: Optional[int] = None
    has_audio: Optional[bool] = None
    audio_codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    created_at: datetime
    thumbnail_path: Optional[str] = None # Local thumbnail path, to be superseded by thumbnail_s3_key
    api_filename: Optional[str] = None
    
    # New image-specific fields
    animation_info: Optional[Dict[str, Any]] = None # E.g., for GIFs

    # New fields for S3 integration
    s3_key: Optional[str] = None
    thumbnail_s3_key: Optional[str] = None
    user_id: Optional[str] = None
    source: Optional[str] = None # Added to track the origin of the file (user_upload, script_generated_output, etc.)

    def to_redis(self) -> Dict[str, str]:
        data = self.model_dump(exclude_none=True)
        redis_data = {}
        
        for key, value in data.items():
            if isinstance(value, bool):
                redis_data[key] = str(value).lower()
            elif key == 'dimensions' and isinstance(value, dict):
                redis_data[key] = json.dumps(value)
            elif key == 'animation_info' and isinstance(value, dict):
                redis_data[key] = json.dumps(value) # Serialize dict to JSON string
            elif key == 'dpi' and isinstance(value, tuple):
                redis_data[key] = f"{int(value[0])}x{int(value[1])}"
            elif isinstance(value, datetime):
                redis_data[key] = value.isoformat()
            elif value is not None:
                if isinstance(value, BaseModel):
                    redis_data[key] = value.model_dump_json()
                else:
                    redis_data[key] = str(value)
                    
        return redis_data

    @classmethod
    def from_redis(cls, data: Dict[str, str]) -> Optional['FileRecord']:
        if not data:
            return None

        original_data_repr = repr(data) 
        processed_data = data.copy() 

        try:
            # Backward compatibility: Populate thumbnail_s3_key from thumbnail_path if thumbnail_s3_key is not present
            if 'thumbnail_s3_key' not in processed_data and 'thumbnail_path' in processed_data:
                processed_data['thumbnail_s3_key'] = processed_data['thumbnail_path']

            if 'dimensions' in processed_data and isinstance(processed_data['dimensions'], str):
                try:
                    processed_data['dimensions'] = json.loads(processed_data['dimensions'])
                except json.JSONDecodeError:
                    processed_data['dimensions'] = None
                    
            if 'animation_info' in processed_data and isinstance(processed_data['animation_info'], str):
                try:
                    processed_data['animation_info'] = json.loads(processed_data['animation_info'])
                except json.JSONDecodeError:
                    processed_data['animation_info'] = None
                    
            if 'is_output' in processed_data:
                processed_data['is_output'] = str(processed_data['is_output']).lower() == 'true'

            numeric_fields = {
                'float': ['duration', 'frame_rate'],
                'int': ['file_size', 'bit_depth', 'bitrate_kbps', 'sample_rate', 'channels']
            }
            
            for field_type, fields in numeric_fields.items():
                for field in fields:
                    if field in processed_data and processed_data[field] is not None:
                        try:
                            value_str = str(processed_data[field])
                            processed_data[field] = float(value_str) if field_type == 'float' else int(value_str)
                        except (ValueError, TypeError):
                            processed_data[field] = None

            for field in ['has_alpha', 'has_audio']:
                if field in processed_data and processed_data[field] is not None:
                    processed_data[field] = str(processed_data[field]).lower() == 'true'

            if 'created_at' in processed_data and isinstance(processed_data['created_at'], str):
                try:
                    processed_data['created_at'] = datetime.fromisoformat(processed_data['created_at'])
                except ValueError:
                    processed_data['created_at'] = datetime.now(timezone.utc)

            if 'file_id' in processed_data and 'id' not in processed_data:
                processed_data['id'] = processed_data['file_id']

            return cls(**processed_data)
        except (ValidationError, Exception) as e:
            logger.error(f"[FileRecord.from_redis] Failed to validate/parse data. Error: {e}.", exc_info=True)
            return None

def format_file_for_api(file_record: Optional[FileRecord]) -> Optional[Dict[str, Any]]:
    if not file_record:
        return None

    logger.info(f"[format_file_for_api] Processing file_id: {file_record.file_id}")
    logger.info(f"[format_file_for_api] Raw file_record.original_filename: {getattr(file_record, 'original_filename', 'NOT_FOUND')}")
    logger.info(f"[format_file_for_api] Raw file_record.s3_key: {getattr(file_record, 's3_key', 'NOT_FOUND')}")

    effective_original_filename = file_record.original_filename

    primary_type_from_mime = file_record.mime_type.split('/')[0] if file_record.mime_type and '/' in file_record.mime_type else "unknown"


    file_ext = pathlib.Path(file_record.original_filename or file_record.s3_key or "").suffix[1:].lower() or None

    metadata_dict: Dict[str, Any] = {
        "mime_type": file_record.mime_type,
        "size": file_record.file_size,
        "dimensions": file_record.dimensions.model_dump() if file_record.dimensions else None,
        "file_extension": file_ext,
    }

    # Use primary_type_from_mime for these checks
    if primary_type_from_mime in ["audio", "video"]:
        if file_record.duration is not None: # Ensure duration is not None before adding
            metadata_dict["duration"] = file_record.duration
    # Removed the 'elif file_record.duration is not None: pass', as it was redundant if primary type wasn't audio/video

    if primary_type_from_mime == "image":
        if hasattr(file_record, 'color_mode') and file_record.color_mode is not None:
            metadata_dict["color_mode"] = file_record.color_mode
        if hasattr(file_record, 'bit_depth') and file_record.bit_depth is not None:
            metadata_dict["bit_depth"] = file_record.bit_depth
        if hasattr(file_record, 'dpi') and file_record.dpi is not None:
            metadata_dict["dpi"] = file_record.dpi
        if hasattr(file_record, 'has_alpha') and file_record.has_alpha is not None:
            metadata_dict["has_alpha"] = file_record.has_alpha
        
        if hasattr(file_record, 'animation_info') and file_record.animation_info is not None:
            metadata_dict["animation_info"] = file_record.animation_info

    file_info: Dict[str, Any] = {
        "file_id": file_record.file_id,
        "s3_key": file_record.s3_key, # Should always be the S3 key
        "original_filename": effective_original_filename, # User-facing/script-generated name
        "api_filename": file_record.api_filename,
        "file_type": file_record.file_type or primary_type_from_mime,
        "metadata": metadata_dict
    }
    
    logger.info(f"[format_file_for_api] Returning file_info for {file_record.file_id}: original_filename='{effective_original_filename}', s3_key='{file_record.s3_key}'")
    return file_info

class RedisUser(BaseModel):
    user_id: str
    email: Optional[EmailStr] = None
    email_verified: Optional[bool] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    hashed_password: Optional[str] = None
    signup_method: Optional[str] = None
    plan: str = DEFAULT_PLAN
    credits: int = DEFAULT_CREDITS
    paddle_customer_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_redis(cls, data: Dict[str, Any]) -> Optional['RedisUser']:
        if not data:
            return None
        
        processed_data = data.copy()

        if 'email_verified' in processed_data and processed_data['email_verified'] is not None:
            processed_data['email_verified'] = str(processed_data['email_verified']).lower() == 'true'
        
        for dt_field in ['created_at', 'updated_at']:
            if dt_field in processed_data and processed_data[dt_field] is not None:
                try:
                    processed_data[dt_field] = datetime.fromisoformat(processed_data[dt_field])
                except ValueError:
                    pass
        
        if 'credits' in processed_data and processed_data['credits'] is not None:
            try:
                processed_data['credits'] = int(processed_data['credits'])
            except ValueError:
                processed_data['credits'] = None
        
        if 'paddle_customer_id' in processed_data and processed_data['paddle_customer_id'] == '':
            processed_data['paddle_customer_id'] = None
        
        try:
            return cls.model_validate(processed_data)
        except Exception:
            return None

    def to_redis(self) -> Dict[str, str]:
        data = self.model_dump(exclude_none=True)
        redis_data = {}
        
        for key, value in data.items():
            if isinstance(value, bool):
                redis_data[key] = str(value).lower()
            elif isinstance(value, datetime):
                redis_data[key] = value.isoformat()
            elif isinstance(value, (int, float)):
                redis_data[key] = str(value)
            elif isinstance(value, str):
                redis_data[key] = value
            else:
                redis_data[key] = str(value)
                
        return redis_data
