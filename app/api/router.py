from fastapi import APIRouter

from .files import router as files_router
from .chats import router as chats_router
from .users import router as users_router
from .billing import router as billing_router

api_router = APIRouter()

api_router.include_router(files_router, tags=["Files"])
api_router.include_router(chats_router, tags=["Chats"])
api_router.include_router(users_router, tags=["Users"])
api_router.include_router(billing_router, tags=["Billing"])