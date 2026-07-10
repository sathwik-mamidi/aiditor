from pathlib import Path
from typing import List, Optional, Union, AsyncIterable, Any

from google import genai
from google.genai import types as genai_types
from google.genai.types import GenerateContentResponse

from app.config.config import config
from app.utils.logger import logger
from app.services.file_manager import FileManager

class LLMError(Exception):
    pass

class LLMClient:
    DEFAULT_SAFETY_SETTINGS = [
        genai_types.SafetySetting(category=cat, threshold='BLOCK_NONE')
        for cat in ['HARM_CATEGORY_HARASSMENT', 'HARM_CATEGORY_HATE_SPEECH',
                   'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'HARM_CATEGORY_DANGEROUS_CONTENT']
    ]
    
    def __init__(self):
        self.initialized = False
        
        # Get Vertex AI configuration
        self.use_vertexai = config.get("GOOGLE_GENAI_USE_VERTEXAI", True)
        self.project_id = config.get("GOOGLE_CLOUD_PROJECT")
        self.location = config.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.service_account_path = config.get("GOOGLE_APPLICATION_CREDENTIALS")
        
        # Validate required configuration for Vertex AI
        if self.use_vertexai:
            if not self.project_id:
                raise LLMError("GOOGLE_CLOUD_PROJECT not configured for Vertex AI")
            logger.info(f"Using Vertex AI with project: {self.project_id}, location: {self.location}")
        else:
            # Fallback to API key mode (legacy)
            self.api_key = config.get("GEMINI_API_KEY")
            if not self.api_key:
                raise LLMError("GEMINI_API_KEY not configured and Vertex AI not enabled")
            
        self.model_name = config.get("GEMINI_MODEL_NAME")
    
        self.generation_profile = config.get("GEMINI_GENERATION_PROFILE", "default")
        if self.generation_profile == "custom":
            self.custom_temperature = config.get("GEMINI_TEMPERATURE")
            self.custom_top_p = config.get("GEMINI_TOP_P")
            self.custom_top_k = config.get("GEMINI_TOP_K")

        self.max_retries = 1
        self.retry_delay = float(config.get("GEMINI_RETRY_DELAY", 1.0))
        
        try:
            # Initialize client based on configuration
            if self.use_vertexai:
                # Vertex AI client initialization
                self.client = genai.Client(
                    vertexai=True,
                    project=self.project_id,
                    location=self.location
                )
                logger.info(f"Initialized Vertex AI client for project {self.project_id} in {self.location}")
            else:
                # Legacy API key client initialization
                self.client = genai.Client(api_key=self.api_key)
                logger.info("Initialized Gemini API client with API key")
            
            # Verify model availability
            model_info = self.client.models.get(model=self.model_name)
            if not model_info or not hasattr(model_info, 'name'):
                raise LLMError(f"Failed to verify model {self.model_name}")
                
        except Exception as e:
            raise LLMError(f"Failed to initialize Google GenAI Client: {e}")
        
        self.instructions = self._load_instructions()
        self.safety_settings = self.DEFAULT_SAFETY_SETTINGS
        self.initialized = True
        
        if self.use_vertexai:
            logger.info(f"Google GenAI Client initialized with Vertex AI - Model: {self.model_name}, Project: {self.project_id}")
        else:
            logger.info(f"Google GenAI Client initialized with API Key - Model: {self.model_name}")
            
        if self.generation_profile == "custom":
            logger.info(f"Using custom generation profile: T={self.custom_temperature}, P={self.custom_top_p}, K={self.custom_top_k}")
        else:
            logger.info("Using default LLM generation profile.")

    def _load_instructions(self) -> str:
        instruction_files = config.get("INSTRUCTIONS_FILES", {})
        instructions = {"core": "", "libraries": ""}
        
        for key, path in instruction_files.items():
            if path and Path(path).exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        instructions[key] = f.read()
                except Exception as e:
                    logger.error(f"Error reading {key} instructions file {path}: {e}")
            else:
                logger.warning(f"{key.capitalize()} instructions file not found at {path}")
                
        return f"{instructions['core']}\n\n=== Library & Package Details ===\n\n{instructions['libraries']}".strip()

    def get_client(self) -> genai.Client:
        if not self.client or not self.initialized:
            raise LLMError("Google GenAI client is not initialized")
        return self.client

    def create_generation_config(
        self, 
        safety_settings: Optional[List[genai_types.SafetySetting]] = None
    ) -> genai_types.GenerateContentConfig:
        gen_config_params = {
            "safety_settings": safety_settings or self.safety_settings
        }

        if self.generation_profile == "custom":
            gen_config_params["temperature"] = self.custom_temperature
            gen_config_params["top_p"] = self.custom_top_p
            gen_config_params["top_k"] = self.custom_top_k
            
        return genai_types.GenerateContentConfig(**gen_config_params)

    def _filter_supported_files(self, files, log_prefix=""):
        if not files:
            return [], 0
            
        filtered_files = []
        supported_count = 0
        
        for file_obj in files:
            if file_obj.mime_type in FileManager.SUPPORTED_MIME_TYPES:
                filtered_files.append(file_obj)
                supported_count += 1
            else:
                logger.warning(
                    f"{log_prefix}Skipping file '{file_obj.name}' with unsupported MIME type: {file_obj.mime_type}"
                )
        
        return filtered_files, supported_count

    async def count_input_tokens(
        self,
        prompt: str,
        files: Optional[List[Any]] = None,
        chat_id: Optional[str] = None
    ) -> int:
        if not self.initialized or not self.client:
            raise LLMError("Client not properly initialized")

        log_prefix = f"[Chat {chat_id}] " if chat_id else ""

        # With Vertex, files are passed as Parts directly in the prompt content.
        # The `files` list will contain these parts. For Gemini API, it contains File objects.
        content_list = [prompt] + (files or [])
        file_count = len(files) if files else 0

        try:
            logger.info(
                f"{log_prefix}Counting tokens for prompt ({len(prompt)} chars) and {file_count} files/parts"
            )
            response = await self.client.aio.models.count_tokens(
                model=self.model_name,
                contents=content_list
            )
            return response.total_tokens
        except Exception as e:
            logger.error(f"{log_prefix}Failed to count tokens: {e}")
            raise LLMError(f"Failed to count tokens: {str(e)}") from e

    async def generate_content(
        self,
        prompt: str,
        files: Optional[List[Any]] = None,
        chat_id: Optional[str] = None,
        max_retries: Optional[int] = None,
        stream: bool = False
    ) -> Union[str, AsyncIterable[GenerateContentResponse]]:
        if not self.initialized or not self.client:
            raise LLMError("Client not properly initialized")
            
        log_prefix = f"[Chat {chat_id}] " if chat_id else ""
        
        # `files` can be a list of genai_types.File for Gemini API
        # or a list of genai_types.Part for Vertex AI. The SDK handles both.
        content_list = [prompt] + (files or [])
        
        generation_config = self.create_generation_config()
        retries = max_retries if max_retries is not None else self.max_retries
        
        log_message = f"{log_prefix}Sending request to Gemini model '{self.model_name}'"
        if self.generation_profile == "custom":
            log_message += f" with T={generation_config.temperature}, P={generation_config.top_p}, K={generation_config.top_k}"
        logger.info(log_message)
        
        for retry in range(retries):
            try:
                if stream:
                    return await self.client.aio.models.generate_content_stream(
                        model=self.model_name,
                        contents=content_list,
                        config=generation_config
                    )
                else:
                    response = await self.client.aio.models.generate_content(
                        model=self.model_name,
                        contents=content_list,
                        config=generation_config
                    )
                    
                    if not response.candidates:
                        raise LLMError("No candidates in response from API")

                    candidate = response.candidates[0]
                    finish_reason_str = "UNKNOWN"
                    if hasattr(candidate.finish_reason, 'name'):
                        finish_reason_str = candidate.finish_reason.name
                    
                    logger.info(f"{log_prefix}Gemini response finished. Reason: {finish_reason_str}")
                    
                    if not candidate.content or not candidate.content.parts:
                        if finish_reason_str == "SAFETY":
                            logger.error(f"{log_prefix}Content generation stopped due to safety settings. Ratings: {candidate.safety_ratings}")
                        raise LLMError(f"Invalid response content structure from API. Finish reason: {finish_reason_str}")
                        
                    return candidate.content.parts[0].text
                    
            except Exception as e:
                if retry >= retries - 1:
                    raise LLMError(f"Failed after {retries} retries: {str(e)}")