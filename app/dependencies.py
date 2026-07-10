import asyncio
from fastapi import Request, Response, HTTPException, Depends, status, Cookie, Header
from typing import Optional

from app.config.config import config
from app.utils.logger import logger
from app.services.auth import auth_service
from app.db.redis_user import get_user
from app.db.redis_chat import get_chat
from app.db.redis_models import RedisChat
from app.models import User
from app.services.file_manager import FileManager
from app.llm.llm_client import LLMClient


file_manager_instance: Optional[FileManager] = None
_file_manager_lock = asyncio.Lock()

async def get_file_manager() -> FileManager:
    """Create the model-backed file service on first use, not at import time."""
    global file_manager_instance

    if file_manager_instance is None:
        async with _file_manager_lock:
            if file_manager_instance is None:
                llm_client = LLMClient()
                file_manager_instance = FileManager(genai_client=llm_client.get_client())

    return file_manager_instance

async def verify_authenticated_session(
    request: Request, 
    response: Response, 
    access_token_param: Optional[str] = Cookie(None, alias="access_token"),
    refresh_token_param: Optional[str] = Cookie(None, alias="refresh_token"),
    authorization: Optional[str] = Header(None)
) -> User:
    logger.debug(f"[verify_authenticated_session] Attempting to verify token for request path: {request.url.path}")
    user: Optional[User] = None
    
    current_access_token = access_token_param

    if authorization:
        scheme, _, credentials = authorization.partition(' ')
        if scheme.lower() == 'bearer' and credentials:
            logger.debug("Found Bearer token in Authorization header.")
            current_access_token = credentials
        elif scheme.lower() == 'bearer':
            logger.debug("Bearer token found in Authorization header but no credentials.")
        else:
            logger.debug(f"Authorization header found with scheme '{scheme}', not 'bearer'.")

    if current_access_token:
        token_payload = auth_service.decode_access_token(current_access_token)
        if token_payload:
            user_id = token_payload.get("sub")
            if user_id:
                db_user = await get_user(user_id)
                if db_user:
                    user = db_user
                    logger.debug(f"User {user_id} authenticated via access token.")
                else:
                    logger.warn(f"User {user_id} from access token not found in DB.")
            else:
                logger.warn("Access token is missing 'sub' (user_id) field.")
        else:
            logger.info("Invalid or expired access token.")

    if user is None:
        logger.info("No valid user from access token. Attempting refresh token.")
        if not refresh_token_param:
            logger.info("No refresh token cookie found.")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated, no refresh token.")

        token_response = await auth_service.refresh_access_token(refresh_token_param)

        if token_response:
            logger.info("Access token successfully refreshed using refresh token.")
            new_access_token = token_response["access_token"]
            new_refresh_token = token_response["refresh_token"]

            new_token_payload = auth_service.decode_access_token(new_access_token)
            if new_token_payload:
                user_id = new_token_payload.get("sub")
                if user_id:
                    db_user = await get_user(user_id)
                    if db_user:
                        user = db_user
                    else:
                        logger.error(f"User {user_id} from NEWLY REFRESHED access token not found in DB.")
                else:
                    logger.error("NEWLY REFRESHED access token is missing 'sub' (user_id).")
            else:
                logger.error("Could not decode NEWLY REFRESHED access token immediately after creation.")
            
            if user:
                secure_cookie = config.get("TOKEN_COOKIE_SECURE", False)
                samesite_cookie = config.get("TOKEN_COOKIE_SAMESITE", "lax")
                access_max_age = config.get("ACCESS_TOKEN_EXPIRE_SECONDS", 86400)  
                refresh_max_age = config.get("REFRESH_TOKEN_EXPIRE_SECONDS", 604800)

                response.set_cookie(
                    key="access_token", value=new_access_token, httponly=True,
                    secure=secure_cookie, samesite=samesite_cookie,
                    max_age=access_max_age, path="/"
                )
                response.set_cookie(
                    key="refresh_token", value=new_refresh_token, httponly=True,
                    secure=secure_cookie, samesite=samesite_cookie,
                    max_age=refresh_max_age, path="/"
                )
                logger.debug(f"Set new access_token and refresh_token cookies for user {user.user_id}")
            else:
                logger.error("Failed to retrieve user details after access token refresh.")
                response.delete_cookie("access_token", path="/")
                response.delete_cookie("refresh_token", path="/")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token refresh failed to retrieve user.")
        else:
            logger.warn("Failed to refresh access token using refresh token.")
            response.delete_cookie("access_token", path="/")
            response.delete_cookie("refresh_token", path="/")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")
        
    if not user:
        logger.error("verify_authenticated_session is about to return No User, this should not happen.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed.")
        
    return user


async def get_optional_authenticated_user(
    request: Request,
    access_token_param: Optional[str] = Cookie(None, alias="access_token"),
    authorization: Optional[str] = Header(None)
) -> Optional[User]:
    logger.debug(f"Attempting optional auth. Access token from cookie (aliased): '{access_token_param}'")
    logger.debug(f"Authorization header: '{authorization}'")
    current_access_token = access_token_param
    if authorization:
        scheme, _, credentials = authorization.partition(' ')
        if scheme.lower() == 'bearer' and credentials:
            current_access_token = credentials
            logger.debug("Optional auth: Using Bearer token from header.")
        elif scheme.lower() == 'bearer':
            logger.debug("Optional auth: Bearer token in header without credentials.")
        
    if not current_access_token:
        logger.debug("Optional auth: No access token found in cookie or header.")
        return None

    token_payload = auth_service.decode_access_token(current_access_token)
    if not token_payload:
        logger.info("Optional auth: Invalid or expired access token.")
        return None

    user_id = token_payload.get("sub")
    if not user_id:
        logger.warn("Optional auth: Access token missing 'sub' (user_id) field.")
        return None

    db_user = await get_user(user_id)
    if not db_user:
        logger.warn(f"Optional auth: User {user_id} from access token not found in DB.")
        return None
    
    logger.debug(f"Optional auth: User {user_id} identified.")
    return db_user


async def get_authorized_chat(
    chat_id: str,
    response: Response, 
    current_user: User = Depends(verify_authenticated_session) 
) -> RedisChat:
    chat_data = await get_chat(chat_id)
    if not chat_data:
        logger.warn(f"Chat not found during authorization - Chat ID: {chat_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    
    if chat_data.user_id != current_user.user_id:
        logger.error(f"Unauthorized access attempt to chat {chat_id} by user {current_user.user_id}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized access to chat")
    
    return chat_data

async def verify_admin_user(current_user: User = Depends(verify_authenticated_session)) -> User:
    admin_ids = config.get("ADMIN_USER_IDS", [])
    if not admin_ids:
        logger.error("ADMIN_USER_IDS not configured. Denying access to admin resource.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin resource access denied: configuration missing."
        )
    if current_user.user_id not in admin_ids:
        logger.warn(f"User {current_user.user_id} ({current_user.email}) attempted to access admin resource without authorization.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this resource."
        )
    logger.info(f"Admin user {current_user.user_id} ({current_user.email}) accessed an admin resource.")
    return current_user

async def get_user_with_optional_refresh(
    request: Request,
    response: Response, 
    access_token_param: Optional[str] = Cookie(None, alias="access_token"),
    refresh_token_param: Optional[str] = Cookie(None, alias="refresh_token"),
    authorization: Optional[str] = Header(None)
) -> Optional[User]:
    logger.debug(f"[get_user_with_optional_refresh] Attempting to get user for request path: {request.url.path}")
    user: Optional[User] = None
    
    current_access_token = access_token_param
    if authorization:
        scheme, _, credentials = authorization.partition(' ')
        if scheme.lower() == 'bearer' and credentials:
            logger.debug("[get_user_with_optional_refresh] Found Bearer token in Authorization header.")
            current_access_token = credentials
        elif scheme.lower() == 'bearer':
            logger.debug("[get_user_with_optional_refresh] Bearer token found in Authorization header but no credentials.")
        else:
            logger.debug(f"[get_user_with_optional_refresh] Authorization header found with scheme '{scheme}', not 'bearer'.")

    if current_access_token:
        token_payload = auth_service.decode_access_token(current_access_token)
        if token_payload:
            user_id = token_payload.get("sub")
            if user_id:
                db_user = await get_user(user_id)
                if db_user:
                    user = db_user
                    logger.debug(f"[get_user_with_optional_refresh] User {user_id} authenticated via access token.")
                else:
                    logger.warn(f"[get_user_with_optional_refresh] User {user_id} from access token not found in DB.")
            else:
                logger.warn("[get_user_with_optional_refresh] Access token is missing 'sub' (user_id) field.")
        else:
            logger.info("[get_user_with_optional_refresh] Invalid or expired access token.")

    if user is None and refresh_token_param:
        logger.info("[get_user_with_optional_refresh] No valid user from access token. Attempting refresh token.")
        
        token_response_data = await auth_service.refresh_access_token(refresh_token_param)

        if token_response_data:
            logger.info("[get_user_with_optional_refresh] Access token successfully refreshed using refresh token.")
            new_access_token = token_response_data["access_token"]
            new_refresh_token = token_response_data["refresh_token"]

            new_token_payload = auth_service.decode_access_token(new_access_token)
            if new_token_payload:
                user_id = new_token_payload.get("sub")
                if user_id:
                    db_user = await get_user(user_id)
                    if db_user:
                        user = db_user # Assign to user
                    else:
                        logger.error(f"[get_user_with_optional_refresh] User {user_id} from NEWLY REFRESHED access token not found in DB.")
                else:
                    logger.error("[get_user_with_optional_refresh] NEWLY REFRESHED access token is missing 'sub' (user_id).")
            else:
                logger.error("[get_user_with_optional_refresh] Could not decode NEWLY REFRESHED access token immediately after creation.")
            
            if user: # Check if user was successfully populated
                secure_cookie = config.get("TOKEN_COOKIE_SECURE", False)
                samesite_cookie = config.get("TOKEN_COOKIE_SAMESITE", "lax")
                access_max_age = config.get("ACCESS_TOKEN_EXPIRE_SECONDS", 86400)  
                refresh_max_age = config.get("REFRESH_TOKEN_EXPIRE_SECONDS", 604800)

                response.set_cookie(
                    key="access_token", value=new_access_token, httponly=True,
                    secure=secure_cookie, samesite=samesite_cookie,
                    max_age=access_max_age, path="/"
                )
                response.set_cookie(
                    key="refresh_token", value=new_refresh_token, httponly=True,
                    secure=secure_cookie, samesite=samesite_cookie,
                    max_age=refresh_max_age, path="/"
                )
                logger.debug(f"[get_user_with_optional_refresh] Set new access_token and refresh_token cookies for user {user.user_id}")
            else: # User not populated after refresh logic
                logger.warn("[get_user_with_optional_refresh] Failed to retrieve user details after access token refresh. Clearing cookies.")
                response.delete_cookie("access_token", path="/", domain=None, secure=config.get("TOKEN_COOKIE_SECURE", False), httponly=config.get("TOKEN_COOKIE_HTTPONLY", True), samesite=config.get("TOKEN_COOKIE_SAMESITE", "lax"))
                response.delete_cookie("refresh_token", path="/", domain=None, secure=config.get("TOKEN_COOKIE_SECURE", False), httponly=config.get("TOKEN_COOKIE_HTTPONLY", True), samesite=config.get("TOKEN_COOKIE_SAMESITE", "lax"))
        else: # token_response_data is None
            logger.warn("[get_user_with_optional_refresh] Failed to refresh access token using refresh token. Clearing cookies.")
            response.delete_cookie("access_token", path="/", domain=None, secure=config.get("TOKEN_COOKIE_SECURE", False), httponly=config.get("TOKEN_COOKIE_HTTPONLY", True), samesite=config.get("TOKEN_COOKIE_SAMESITE", "lax"))
            response.delete_cookie("refresh_token", path="/", domain=None, secure=config.get("TOKEN_COOKIE_SECURE", False), httponly=config.get("TOKEN_COOKIE_HTTPONLY", True), samesite=config.get("TOKEN_COOKIE_SAMESITE", "lax"))
            
    elif user is None and not refresh_token_param:
        logger.debug("[get_user_with_optional_refresh] No access token and no refresh token cookie found.")

    return user
