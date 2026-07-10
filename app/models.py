from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

class DetailResponse(BaseModel):
    detail: str

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, password: str) -> str:
        if not re.search(r"[A-Z]", password):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", password):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", password):
            raise ValueError("Password must contain at least one number")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            raise ValueError("Password must contain at least one special character")
        return password

class UserSignin(BaseModel):
    email: EmailStr
    password: str

class Dimensions(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)

class FileMetadata(BaseModel):
    mime_type: Optional[str] = None
    size: Optional[int] = None
    dimensions: Optional[Dimensions] = None
    duration: Optional[float] = None
    original_filename: Optional[str] = None
    api_filename: Optional[str] = None
    file_extension: Optional[str] = None
    # Image specific fields
    color_mode: Optional[str] = None
    bit_depth: Optional[int] = None
    dpi: Optional[str] = None # Stored as string like "72x72"
    has_alpha: Optional[bool] = None
    animation_info: Optional[Dict[str, Any]] = None

class FileInfo(BaseModel):
    file_id: str
    s3_key: str
    original_filename: Optional[str] = None
    metadata: Optional[FileMetadata] = None
    file_type: Optional[str] = None
    thumbnail_s3_key: Optional[str] = None

class UserConversationMessage(BaseModel):
    timestamp: str
    role: str = "user"
    prompt: Optional[str] = None
    input_files: List[FileInfo] = Field(default_factory=list)

class AssistantConversationMessage(BaseModel):
    timestamp: str
    role: str = "assistant"
    response: Optional[Dict[str, Any]] = None
    output_files: List[FileInfo] = Field(default_factory=list)

class FileRecordForCreate(BaseModel):
    original_filename: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = "application/octet-stream"
    dimensions: Optional[Dimensions] = None
    color_mode: Optional[str] = None
    bit_depth: Optional[int] = None
    dpi: Optional[str] = None
    has_alpha: Optional[bool] = None
    duration: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    frame_rate: Optional[float] = None
    bitrate_kbps: Optional[int] = None
    has_audio: Optional[bool] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    thumbnail_path: Optional[str] = None
    api_filename: Optional[str] = None
    # New image-specific fields, ensure they match FileRecord if used for direct creation
    animation_info: Optional[Dict[str, Any]] = None

class User(BaseModel):
    user_id: str
    email: Optional[str] = None
    email_verified: Optional[bool] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    # This model is returned by API routes. Keep the credential hash available to
    # authentication internals without ever including it in serialized output.
    hashed_password: Optional[str] = Field(default=None, exclude=True, repr=False)
    signup_method: Optional[str] = None
    plan: str = "free"
    credits: int = 100
    paddle_customer_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Chat(BaseModel):
    chat_id: str
    user_id: str
    conversations: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    chat_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class ChatSummary(BaseModel):
    chat_id: str
    user_id: str
    created_at: str
    updated_at: str
    chat_name: Optional[str] = None
    last_message_preview: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class ChatCreateUpdateResponse(BaseModel):
    message: str
    chat_id: str

class UploadResponse(BaseModel):
    message: str
    chat_id: str
    files: List[Dict[str, Any]]

class DeleteFileResponse(BaseModel):
    message: str
    deleted_file_id: Optional[str] = None

class ProcessChatRequest(BaseModel):
    """Marker body for starting processing of files already attached to a chat."""

class LLMErrorResponse(BaseModel):
    detail: str
    error_details: Optional[str] = None

class ProcessChatResponse(BaseModel):
    assistant_response: Dict[str, Any]
    output_files: List[Dict[str, Any]]

class AppendAssistantTurnRequest(BaseModel):
    assistant_turn_data: Dict[str, Any]

class FileAccessUrlResponse(BaseModel):
    url: str
    s3_key: str
    filename: str # Original filename for context
