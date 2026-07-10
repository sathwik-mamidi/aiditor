import uvicorn
from fastapi import FastAPI, Request, HTTPException, Depends, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi import status
from typing import Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from uuid import UUID

from app.config.config import config
from app.utils.logger import logger
from app.db.redis_client import get_redis_client, close_redis_client
from app.llm.llm_orchestrator import LLMOrchestrator

from app.api.router import api_router
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.dependencies import verify_authenticated_session, get_optional_authenticated_user, verify_admin_user, get_user_with_optional_refresh
from app.models import User

app = FastAPI(
    title=config["PROJECT_NAME"],
    openapi_url=f"{config['API_V1_STR']}/openapi.json"
)

app.include_router(api_router, prefix=config["API_V1_STR"])
app.include_router(auth_router, prefix="")
app.include_router(admin_router, prefix=config["API_V1_STR"])

app.state.limiter = Limiter(key_func=get_remote_address, storage_uri=config["REDIS_URL"])
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Helper function to delete authentication cookies
def _delete_auth_cookies(response: Response):
    logger.debug("Clearing stale access_token and refresh_token cookies.")
    secure_cookie = config.get("TOKEN_COOKIE_SECURE", False)
    httponly_cookie = config.get("TOKEN_COOKIE_HTTPONLY", True)
    samesite_cookie = config.get("TOKEN_COOKIE_SAMESITE", "lax")
    
    response.delete_cookie(
        "access_token", 
        path="/", 
        domain=None, 
        secure=secure_cookie, 
        httponly=httponly_cookie, 
        samesite=samesite_cookie
    )
    response.delete_cookie(
        "refresh_token", 
        path="/", 
        domain=None, 
        secure=secure_cookie, 
        httponly=httponly_cookie, 
        samesite=samesite_cookie
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and (request.url.path.startswith("/c/") or request.url.path == "/admin"):
        logger.info(f"Unauthenticated access to {request.url.path}, redirecting to /signin")
        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
        return RedirectResponse(url="/signin", status_code=status.HTTP_303_SEE_OTHER, headers=headers)
    
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None)
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred"},
    )

@app.on_event("startup")
async def startup_event():
    logger.info("Application startup...")
    await get_redis_client() 
    try:
        app.state.llm_orchestrator = await LLMOrchestrator.create()
        logger.info("LLM Orchestrator initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize LLM Orchestrator during startup: {e}")
        app.state.llm_orchestrator = None
    logger.info("Application startup complete.")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutdown...")
    await close_redis_client()
    logger.info("Application shutdown complete.")

app.mount("/static", StaticFiles(directory=config["BASE_DIR"] / "static"), name="static")

@app.get("/", include_in_schema=False)
async def home_or_chat_page(request: Request, response: Response, current_user: Optional[User] = Depends(get_user_with_optional_refresh)):
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    if current_user:
        logger.info(f"User {current_user.user_id} authenticated for GET /. Serving chat.html. User details: {current_user.model_dump_json() if current_user else 'None'}")
        file_path = config["BASE_DIR"] / "static" / "chat.html"
        logger.info(f"For GET /, serving file: {file_path}")
        return FileResponse(file_path, headers=headers)
    else:
        logger.info("No authenticated user for GET /. Clearing cookies and serving home.html.")
        file_path = config["BASE_DIR"] / "static" / "home.html"
        logger.info(f"For GET /, serving file: {file_path}")
        final_response = FileResponse(file_path, headers=headers)
        _delete_auth_cookies(final_response)
        return final_response

@app.get("/signin", include_in_schema=False)
async def signin(request: Request, response: Response, current_user: Optional[User] = Depends(get_optional_authenticated_user)):
    if current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    logger.info("No authenticated user for GET /signin. Clearing cookies and serving signin.html.")
    file_path = config["BASE_DIR"] / "static" / "signin.html"
    final_response = FileResponse(file_path)
    _delete_auth_cookies(final_response)
    return final_response

@app.get("/signup", include_in_schema=False)
async def signup(request: Request, response: Response, current_user: Optional[User] = Depends(get_optional_authenticated_user)):
    if current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    logger.debug("Clearing auth cookies on GET /signup page load.")
    file_path = config["BASE_DIR"] / "static" / "signup.html"
    final_response = FileResponse(file_path)
    _delete_auth_cookies(final_response)
    return final_response

@app.get("/forgot-password", include_in_schema=False)
async def forgot_password_page(request: Request, response: Response, current_user: Optional[User] = Depends(get_optional_authenticated_user)):
    if current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        
    logger.info("No authenticated user for GET /forgot-password. Clearing cookies and serving forgot-password.html.")
    file_path = config["BASE_DIR"] / "static" / "forgot-password.html"
    final_response = FileResponse(file_path)
    _delete_auth_cookies(final_response)
    return final_response

@app.get("/reset-password", include_in_schema=False)
async def reset_password_page(request: Request, response: Response, current_user: Optional[User] = Depends(get_optional_authenticated_user)):
    if current_user:
        logger.info(f"User {current_user.user_id} authenticated, redirecting from /reset-password to /.")
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    logger.info("No authenticated user for GET /reset-password. Clearing cookies and serving reset-password.html.")
    file_path = config["BASE_DIR"] / "static" / "reset-password.html"
    final_response = FileResponse(file_path)
    _delete_auth_cookies(final_response)
    return final_response

@app.get("/c/{chat_id:uuid}", include_in_schema=False)
async def chat_page_with_id(chat_id: UUID, response: Response, user: User = Depends(verify_authenticated_session)):
    return FileResponse(config["BASE_DIR"] / "static" / "chat.html")

@app.get("/admin", include_in_schema=False)
async def debug_page(request: Request, current_user: User = Depends(verify_admin_user)):
    """Serves the admin page, only accessible by admin users."""
    return FileResponse(config["BASE_DIR"] / "static" / "admin.html")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(config["BASE_DIR"] / "static" / "favicon.ico")

@app.get("/terms", include_in_schema=False)
async def terms():
    return FileResponse(config["BASE_DIR"] / "static" / "terms.html")

@app.get("/privacy", include_in_schema=False)
async def privacy():
    return FileResponse(config["BASE_DIR"] / "static" / "privacy.html")

if __name__ == "__main__":
    port = config["PORT"]
    logger.info(f"Starting FastAPI server on port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, timeout_keep_alive=300)