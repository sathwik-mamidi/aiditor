from pydantic_settings import BaseSettings, SettingsConfigDict

class GoogleOAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str

google_oauth_settings = GoogleOAuthSettings()
