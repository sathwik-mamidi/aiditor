from datetime import datetime, timedelta, timezone
import json
import secrets
from typing import Dict, Optional, Any, TypedDict
import uuid

import jwt
from fastapi import HTTPException, status
from google.oauth2 import id_token
from google.auth.transport import requests
from google.auth.exceptions import GoogleAuthError

from app.config.config import config
from app.config.google_oauth import google_oauth_settings
from app.db.redis_client import get_redis_client
from app.db.redis_user import get_user
from app.models import User
from app.utils.logger import logger

ACCESS_TOKEN_EXPIRY_DAYS = 1
REFRESH_TOKEN_EXPIRY_DAYS = 7
PASSWORD_RESET_TOKEN_EXPIRY_SECONDS = 3600 # 1 hour

class UserPayload(TypedDict, total=False):
    sub: str
    email: str
    email_verified: Optional[bool]
    name: Optional[str]
    picture: Optional[str]
    given_name: Optional[str]
    family_name: Optional[str]
    exp: datetime

class TokenResponse(TypedDict):
    access_token: str
    refresh_token: str

class AuthService:
    def __init__(self):
        self.redis_client = None
        self.google_client_id = google_oauth_settings.GOOGLE_CLIENT_ID
        self.jwt_secret = config["JWT_SECRET_KEY"]
        self.refresh_token_secret = config["REFRESH_SECRET_KEY"]

    async def _get_redis_client(self):
        if self.redis_client is None:
            self.redis_client = await get_redis_client()
        return self.redis_client

    async def verify_google_token(self, token: str) -> dict:
        try:
            idinfo = id_token.verify_oauth2_token(
                token, 
                requests.Request(), 
                self.google_client_id
            )
            
            if idinfo['aud'] != self.google_client_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
                                   detail="Invalid token audience")
            
            return idinfo
        except GoogleAuthError as e:
            logger.error(f"Google auth error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, 
                               detail="Invalid or expired Google token")
        except Exception as e:
            logger.error(f"Unexpected error verifying Google token: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
                               detail="Invalid token")

    def _create_user_payload(self, user_id: str, email: str, email_verified: Optional[bool] = None, 
                            name: Optional[str] = None, picture: Optional[str] = None,
                            given_name: Optional[str] = None, family_name: Optional[str] = None) -> UserPayload:
        payload: UserPayload = {
            "sub": user_id,
            "email": email,
        }
        
        if email_verified is not None:
            payload["email_verified"] = email_verified
        if name is not None:
            payload["name"] = name
        if picture is not None:
            payload["picture"] = picture
        if given_name is not None:
            payload["given_name"] = given_name
        if family_name is not None:
            payload["family_name"] = family_name
            
        return payload

    def create_jwt_token(self, user_id: str, email: str, email_verified: Optional[bool] = None, 
                         name: Optional[str] = None, picture: Optional[str] = None,
                         given_name: Optional[str] = None, family_name: Optional[str] = None) -> str:
        payload = self._create_user_payload(
            user_id, email, email_verified, name, picture, given_name, family_name
        )
        
        payload["exp"] = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRY_DAYS)
        
        return jwt.encode(payload, self.jwt_secret, algorithm="HS256")

    def decode_access_token(self, token: str) -> Optional[UserPayload]:
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
            return payload
        except jwt.ExpiredSignatureError:
            logger.info("Access token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid access token: {str(e)}")
            return None

    def create_refresh_token(self, user_id: str) -> str:
        payload = {
            "sub": user_id,
            "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRY_DAYS)
        }
        return jwt.encode(payload, self.refresh_token_secret, algorithm="HS256")

    async def verify_refresh_token(self, token: str) -> Optional[UserPayload]:
        try:
            payload = jwt.decode(token, self.refresh_token_secret, algorithms=["HS256"])
            return payload
        except jwt.ExpiredSignatureError:
            logger.info("Refresh token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid refresh token: {str(e)}")
            return None

    async def refresh_access_token(self, refresh_token: str) -> Optional[TokenResponse]:
        refresh_payload = await self.verify_refresh_token(refresh_token)
        if not refresh_payload:
            return None

        user_id = refresh_payload.get("sub")
        if not user_id:
            logger.warning("User ID not found in refresh token payload")
            return None
        
        user = await get_user(user_id)
        if not user:
            logger.warning(f"User not found for user_id {user_id}")
            return None

        access_token = self.create_jwt_token(
            user_id=user.user_id,
            email=user.email,
            email_verified=user.email_verified,
            name=user.name,
            picture=user.picture,
            given_name=user.given_name,
            family_name=user.family_name
        )
        new_refresh_token = self.create_refresh_token(user.user_id)

        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token
        }

    async def generate_password_reset_token(self, user_id: str) -> Optional[str]:
        """
        Generates a secure password reset token and stores it in Redis with an expiry.
        """
        try:
            token = secrets.token_urlsafe(32)
            redis_client = await self._get_redis_client()
            await redis_client.set(
                f"reset_token:{token}", 
                user_id, 
                ex=PASSWORD_RESET_TOKEN_EXPIRY_SECONDS
            )
            return token
        except Exception as e:
            logger.error(f"Error generating password reset token for user {user_id}: {str(e)}")
            return None

    async def verify_password_reset_token(self, token: str) -> Optional[str]:
        """
        Verifies a password reset token and retrieves the user_id if valid.
        Deletes the token after successful verification to prevent reuse.
        """
        try:
            redis_client = await self._get_redis_client()
            key = f"reset_token:{token}"
            user_id = await redis_client.get(key)
            if user_id:
                await redis_client.delete(key) # Invalidate token after use
                return user_id.decode("utf-8") if isinstance(user_id, bytes) else user_id
            return None
        except Exception as e:
            logger.error(f"Error verifying password reset token: {str(e)}")
            return None

auth_service = AuthService()