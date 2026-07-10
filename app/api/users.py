from fastapi import APIRouter, Depends

from app.dependencies import verify_authenticated_session
from app.models import User

router = APIRouter()

@router.get("/user", response_model=User)
async def read_user(current_user: User = Depends(verify_authenticated_session)):
    return current_user