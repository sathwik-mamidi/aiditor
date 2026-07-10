import os
import pathlib
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
FILES_DIR = pathlib.Path("/tmp")
CONTAINER_FILES_DIR = pathlib.Path("/tmp")

def ensure_directory_exists(dir_path: pathlib.Path):
    dir_path.mkdir(parents=True, exist_ok=True)

def get_csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]

config = {
    # LLM settings - Vertex AI Configuration
    "GOOGLE_GENAI_USE_VERTEXAI": os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true",
    "GOOGLE_CLOUD_PROJECT": os.getenv("GOOGLE_CLOUD_PROJECT"),
    "GOOGLE_CLOUD_LOCATION": os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
    "GOOGLE_APPLICATION_CREDENTIALS": os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),  # Optional service account path
    
    # Model settings (updated for Vertex AI model naming)
    "GEMINI_MODEL_NAME": os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-pro"),  # Vertex AI model name
    "GEMINI_GENERATION_PROFILE": os.getenv("GEMINI_GENERATION_PROFILE", "default"),  # "default" or "custom"
    "GEMINI_TEMPERATURE": float(os.getenv("GEMINI_TEMPERATURE", 0)),
    "GEMINI_TOP_P": float(os.getenv("GEMINI_TOP_P", 1)),
    "GEMINI_TOP_K": int(os.getenv("GEMINI_TOP_K", 100)),
    
    # Legacy API key (kept for reference)
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),

    # AWS SES settings
    "AWS_REGION": os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")),
    "SENDER_EMAIL_ADDRESS": os.getenv("SENDER_EMAIL_ADDRESS", "Aiditor <noreply@aiditor.ai>"),
    "APP_BASE_URL": os.getenv("APP_BASE_URL", "http://localhost:3000"),
    "S3_BUCKET_NAME": os.getenv("S3_BUCKET_NAME"),
    "S3_PRESIGNED_URL_EXPIRY_SECONDS": int(os.getenv("S3_PRESIGNED_URL_EXPIRY_SECONDS", 3600)),
    
    # Environment and directory settings
    "BASE_DIR": BASE_DIR,
    "CONTAINER_FILES_DIR": CONTAINER_FILES_DIR,
    "FILES_DIR": FILES_DIR,
    "LOG_LEVEL": os.getenv("LOG_LEVEL", "DEBUG"),
    "PORT": int(os.getenv("PORT", 3000)),
    "PROJECT_NAME": "AIDITOR",
    
    # Auth and security
    "ACCESS_TOKEN_EXPIRE_SECONDS": int(os.getenv("ACCESS_TOKEN_EXPIRE_SECONDS", 86400)), # 1 day
    "REFRESH_TOKEN_EXPIRE_SECONDS": int(os.getenv("REFRESH_TOKEN_EXPIRE_SECONDS", 604800)), # 7 days
    "JWT_SECRET_KEY": os.getenv("JWT_SECRET_KEY"),
    "REFRESH_SECRET_KEY": os.getenv("REFRESH_SECRET_KEY"),
    "TOKEN_COOKIE_HTTPONLY": True,
    "TOKEN_COOKIE_SAMESITE": "lax",
    "TOKEN_COOKIE_SECURE": os.getenv("NODE_ENV") == "production",
    "ADMIN_USER_IDS": get_csv_env("ADMIN_USER_IDS"),
    
    # API configuration
    "API_V1_STR": "/api",
    "DOCKER_SOCKET_PATH": "/var/run/docker.sock",
    "INSTRUCTIONS_FILES": {
        "core": BASE_DIR / "app/config/instructions.txt",
        "libraries": BASE_DIR / "app/config/libraries.txt",
    },
    "PYTHON_SANDBOX_CONTAINER_NAME": "python-sandbox",
    
    # Redis configuration
    "REDIS_KEY_PREFIXES": {
        "CHAT": "chat:",
        "CHAT_FILES": "chat_files:",
        "EMAIL_INDEX": "email_to_user_id",
        "FILE": "file:",
        "S3_KEY_INDEX": "s3_key_idx:",
        "PENDING_FILES": "pending_files:",
        "PADDLE_CUSTOMER_INDEX": "paddle_customer_to_user_id",
        "USER": "user:",
        "USER_CHATS": "user_chats:",
        "USER_FILES": "user_files:",
        "TASK_STATUS": "task:",
    },
    "REDIS_URL": os.getenv("REDIS_URL", "redis://redis:6379"),
    "TASK_STATUS_EXPIRY_SECONDS": int(os.getenv("TASK_STATUS_EXPIRY_SECONDS", 1800)),
    
    # Paddle payment settings
    "PADDLE_API_BASE_URL": os.getenv("PADDLE_API_BASE_URL"),
    "PADDLE_API_KEY": os.getenv("PADDLE_API_KEY"),
    "PADDLE_CLIENT_TOKEN": os.getenv("PADDLE_CLIENT_TOKEN"),
    "PADDLE_WEBHOOK_SECRET": os.getenv("PADDLE_WEBHOOK_SECRET"),
    "PADDLE_PRO_PLAN_PRICE_ID": os.getenv("PADDLE_PRO_PLAN_PRICE_ID"),
    "PADDLE_CREDITS_20_PRICE_ID": os.getenv("PADDLE_CREDITS_20_PRICE_ID"),
    "PADDLE_CREDITS_100_PRICE_ID": os.getenv("PADDLE_CREDITS_100_PRICE_ID"),
    "PADDLE_CREDITS_500_PRICE_ID": os.getenv("PADDLE_CREDITS_500_PRICE_ID"),

    # Code execution settings
    "CODE_EXECUTION_TIMEOUT_SECONDS": 300,  # 5 minutes max execution time
    "CODE_OUTPUT_DELIM_END": "<<<OUTPUT_FILES_END>>>",
    "CODE_OUTPUT_DELIM_START": "<<<OUTPUT_FILES_START>>>",
}

def get_config():
    return config
