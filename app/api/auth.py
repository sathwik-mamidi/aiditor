from fastapi import APIRouter, HTTPException, Response, Depends, status, Cookie
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
import secrets
import httpx
from typing import Optional
import uuid

from pydantic import BaseModel, EmailStr

from app.services.auth import auth_service
from app.config.google_oauth import google_oauth_settings
from app.db.redis_user import create_or_update_user_from_google, get_user_by_email, create_email_user, update_user_password
from app.utils.security import get_password_hash, verify_password
from app.models import UserCreate, DetailResponse
from app.config.config import config
from app.utils.logger import logger
from app.utils.email_service import send_password_reset_email

router = APIRouter()

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

# Cookie configuration
COOKIE_SETTINGS = {
    "httponly": config.get("TOKEN_COOKIE_HTTPONLY", True),
    "secure": config.get("TOKEN_COOKIE_SECURE", False),
    "samesite": config.get("TOKEN_COOKIE_SAMESITE", "lax"),
    "path": "/"
}

# Token expiration times in seconds
TOKEN_EXPIRY = {
    "access": config.get("ACCESS_TOKEN_EXPIRE_SECONDS"),
    "refresh": config.get("REFRESH_TOKEN_EXPIRE_SECONDS")
}

async def set_auth_cookies(response: Response, user_id: str, user_details: dict, email: str = ""):
    # session_id = str(uuid.uuid4())
    # await auth_service.store_session(session_id, session_data) # session_data is now user_details
    
    access_token = auth_service.create_jwt_token(
        user_id=user_id,
        email=email, # email from param is fine, or user_details.get("email")
        email_verified=user_details.get("email_verified"),
        name=user_details.get("name"),
        picture=user_details.get("picture"),
        given_name=user_details.get("given_name"),
        family_name=user_details.get("family_name")
    )
    refresh_token = auth_service.create_refresh_token(user_id)
    
    # response.set_cookie(key="session_id", value=session_id, max_age=TOKEN_EXPIRY["session"], **COOKIE_SETTINGS)
    response.set_cookie(key="access_token", value=access_token, max_age=TOKEN_EXPIRY["access"], **COOKIE_SETTINGS)
    response.set_cookie(key="refresh_token", value=refresh_token, max_age=TOKEN_EXPIRY["refresh"], **COOKIE_SETTINGS)
    logger.debug(f"Set access_token and refresh_token cookies for user {user_id}")

@router.get("/auth/google")
async def google_auth():
    state = secrets.token_urlsafe(32)
    redis = await auth_service._get_redis_client()
    await redis.setex(f"oauth_state:{state}", 300, "1")
    
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={google_oauth_settings.GOOGLE_CLIENT_ID}&"
        f"redirect_uri={google_oauth_settings.GOOGLE_REDIRECT_URI}&"
        "response_type=code&"
        "scope=email profile&"
        f"state={state}"
    )
    
    return RedirectResponse(url=auth_url)

@router.get("/auth/google/callback")
async def google_auth_callback(code: str, state: str):
    redis = await auth_service._get_redis_client()
    stored_state = await redis.get(f"oauth_state:{state}")
    
    if not stored_state:
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    
    await redis.delete(f"oauth_state:{state}")
    
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": google_oauth_settings.GOOGLE_CLIENT_ID,
                "client_secret": google_oauth_settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": google_oauth_settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        
        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to exchange code for token")
        
        tokens = token_response.json()
        user_info = await auth_service.verify_google_token(tokens["id_token"])
        
        db_user = await create_or_update_user_from_google(user_info)
        if not db_user:
            raise HTTPException(status_code=500, detail="Failed to process user information")
        
        response = RedirectResponse(url="/")
        await set_auth_cookies(
            response=response,
            user_id=user_info["sub"],
            user_details=user_info,
            email=user_info.get("email", "")
        )
        
        return response

@router.post("/signup", response_model=DetailResponse, status_code=status.HTTP_201_CREATED)
async def signup_with_email(user_data: UserCreate, response: Response):
    existing_user = await get_user_by_email(user_data.email)
    
    if existing_user:
        if existing_user.signup_method == "google":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This email is already registered with Google Sign-In. Please use Google Sign-In to access your account."
            )
        else:
            # Email exists and it's an email/password account. Try to sign them in.
            if not existing_user.hashed_password or not verify_password(user_data.password, existing_user.hashed_password):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, # Changed from 400
                    detail="Email already registered with a different password. Please sign in or use 'Forgot Password'."
                )
            
            # Password matches, proceed to sign in
            logger.info(f"User {existing_user.email} attempted signup with existing email and correct password. Signing them in.")
            session_data = {
                "sub": existing_user.user_id, 
                "email": existing_user.email,
                "email_verified": existing_user.email_verified,
                "name": existing_user.name,
                "picture": existing_user.picture,
                "given_name": existing_user.given_name,
                "family_name": existing_user.family_name
            }
            
            # Create a RedirectResponse. Note: The original 'response' param is for the overall endpoint.
            # We will be returning this new redirect_response directly.
            redirect_response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
            
            await set_auth_cookies(
                response=redirect_response, # Pass the redirect_response here
                user_id=existing_user.user_id,
                user_details=session_data,
                email=existing_user.email or ""
            )
            return redirect_response # Return the redirect, not the original 'response' object

    # If existing_user is None, or it was a Google account (handled above), proceed to create new user
    hashed_password = get_password_hash(user_data.password)
    new_user = await create_email_user(user_data.email, hashed_password)
    
    if not new_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create user"
        )
    
    # This part is for new user creation
    session_data_new_user = {"sub": new_user.user_id, "email": new_user.email}
    
    # Create a RedirectResponse for new user
    redirect_response_new_user = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    await set_auth_cookies(
        response=redirect_response_new_user,
        user_id=new_user.user_id,
        user_details=session_data_new_user,
        email=new_user.email or ""
    )
    
    # The endpoint is decorated with status_code=201 and response_model=DetailResponse.
    # Returning a redirect means these are not strictly applied for this path.
    # If we needed to return a JSON body for a successful NEW signup (201 created),
    # we would not redirect here, but perhaps return DetailResponse(detail="Signup successful, user created").
    # However, a redirect to '/' is usually the desired UX.
    return redirect_response_new_user

@router.post("/signin")
async def signin_with_email(response: Response, form_data: OAuth2PasswordRequestForm = Depends()):
    user = await get_user_by_email(form_data.username)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if user.signup_method == "google" or not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This email is registered with Google Sign-In. Please use Google Sign-In to access your account.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    session_data = {
        "sub": user.user_id, 
        "email": user.email,
        "email_verified": user.email_verified,
        "name": user.name,
        "picture": user.picture,
        "given_name": user.given_name,
        "family_name": user.family_name
    }
    
    await set_auth_cookies(
        response=response,
        user_id=user.user_id,
        user_details=session_data,
        email=user.email or ""
    )
    
    return DetailResponse(detail="Signin successful")

@router.post("/auth/refresh")
async def refresh_tokens(response: Response, refresh_token_cookie: Optional[str] = Cookie(None)):
    if not refresh_token_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token provided"
        )
    
    new_tokens = await auth_service.refresh_access_token(refresh_token_cookie)
    if not new_tokens:
        response.delete_cookie("access_token", path="/")
        response.delete_cookie("refresh_token", path="/")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )
    
    response.set_cookie(
        key="access_token",
        value=new_tokens["access_token"],
        max_age=TOKEN_EXPIRY["access"], 
        **COOKIE_SETTINGS
    )
    response.set_cookie(
        key="refresh_token",
        value=new_tokens["refresh_token"],
        max_age=TOKEN_EXPIRY["refresh"], 
        **COOKIE_SETTINGS
    )
    
    return DetailResponse(detail="Tokens refreshed successfully")

@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    
    return {"message": "Logged out successfully"}

@router.post("/auth/forgot-password", response_model=DetailResponse)
async def forgot_password(request_data: ForgotPasswordRequest):
    """
    Initiates the password reset process.
    If the email exists and is eligible for password reset,
    a reset token is generated and an email should be sent to the user.
    (Email sending logic is to be implemented separately)
    """
    user = await get_user_by_email(request_data.email)
    generic_success_message = "If your email is registered and eligible, a password reset link has been sent."

    if not user:
        logger.info(f"Password reset requested for non-existent email: {request_data.email}")
        # Return a generic message to avoid email enumeration
        return DetailResponse(detail=generic_success_message)

    if user.signup_method == "google":
        # This check can remain as it's a specific condition not revealing general existence
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset is not available for accounts registered with Google Sign-In."
        )
    
    if not user.hashed_password: # Should not happen if signup_method is 'email' but good to check
        logger.warn(f"Password reset attempted for user {user.email} without a hashed password.")
        # Return a generic message
        return DetailResponse(detail=generic_success_message)

    reset_token = await auth_service.generate_password_reset_token(user.user_id)
    if not reset_token:
        logger.error(f"Could not generate password reset token for user {user.email}.")
        # Return a generic message
        return DetailResponse(detail=generic_success_message)

    app_base_url = config.get("APP_BASE_URL") 
    reset_link = f"{app_base_url}/reset-password?token={reset_token}"

    # Send the password reset email
    email_sent = await send_password_reset_email(user.email, reset_link)

    if not email_sent:
        logger.error(f"Failed to send password reset email to {user.email}. Token was: {reset_token}")
        # Even if email fails, return the generic success message
        return DetailResponse(detail=generic_success_message)

    logger.info(f"Password reset process initiated for {user.email}. Token: {reset_token}. Link: {reset_link}")
    return DetailResponse(detail=generic_success_message)

@router.post("/auth/reset-password", response_model=DetailResponse)
async def reset_password(request_data: ResetPasswordRequest):
    user_id = await auth_service.verify_password_reset_token(request_data.token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token."
        )
    
    # Add password strength validation if desired (e.g., length, complexity)
    if len(request_data.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters long."
        )

    hashed_password = get_password_hash(request_data.new_password)
    
    # We need a db function to update the password by user_id
    success = await update_user_password(user_id, hashed_password)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not update password. Please try again."
        )
    
    return DetailResponse(detail="Your password has been reset successfully.")