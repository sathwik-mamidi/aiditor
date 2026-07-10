from datetime import datetime, timezone
from typing import Optional, Dict, Any
import secrets

from app.utils.logger import logger
from app.db.redis_client import get_redis_client
from app.models import User 
from app.db.redis_models import RedisUser
from app.config.config import config

USER_PREFIX = config["REDIS_KEY_PREFIXES"]["USER"]
EMAIL_TO_USER_ID_INDEX_KEY = config["REDIS_KEY_PREFIXES"]["EMAIL_INDEX"]
PADDLE_CUSTOMER_ID_TO_USER_ID_INDEX_KEY = config["REDIS_KEY_PREFIXES"]["PADDLE_CUSTOMER_INDEX"]
DEFAULT_CREDITS = 100
DEFAULT_PLAN = "free"

async def get_user(user_id: str) -> Optional[User]:
    redis_client = await get_redis_client()
    user_key = f"{USER_PREFIX}{user_id}"
    user_data = await redis_client.hgetall(user_key)
    
    if not user_data:
        logger.debug(f"User {user_id} not found in Redis (key: {user_key}).")
        return None
        
    redis_user = RedisUser.from_redis(user_data)
    if not redis_user:
        logger.error(f"Failed to parse RedisUser from data for user {user_id}. Data: {user_data}")
        return None
        
    return User(**redis_user.model_dump())

async def get_user_by_email(email: str) -> Optional[User]:
    redis_client = await get_redis_client()
    normalized_email = email.lower()
    user_id = await redis_client.hget(EMAIL_TO_USER_ID_INDEX_KEY, normalized_email)
    
    if not user_id:
        logger.debug(f"No user found for email {email}")
        return None
        
    return await get_user(user_id)

async def create_or_update_user_from_google(user_info: Dict[str, Any]) -> Optional[User]:
    redis_client = await get_redis_client()
    raw_user_id = user_info.get("sub")
    
    if not raw_user_id:
        logger.error("Google user_info dictionary missing 'sub' field.")
        return None

    user_key = f"{USER_PREFIX}{raw_user_id}"
    now = datetime.now(timezone.utc)

    user_data = {
        "user_id": raw_user_id,
        "email": user_info.get("email"),
        "email_verified": user_info.get("email_verified"),
        "name": user_info.get("name"),
        "picture": user_info.get("picture"),
        "given_name": user_info.get("given_name"),
        "family_name": user_info.get("family_name"),
        "signup_method": "google",
        "updated_at": now,
    }

    existing_data = await redis_client.hgetall(user_key)
    if existing_data:
        try:
            created_at_str = existing_data.get("created_at")
            user_data["created_at"] = datetime.fromisoformat(created_at_str) if created_at_str else now
        except ValueError:
            user_data["created_at"] = now
            logger.warning(f"Invalid created_at format for user {raw_user_id}, using current time.")

        user_data["plan"] = existing_data.get("plan", DEFAULT_PLAN)
        try:
            user_data["credits"] = int(existing_data.get("credits", str(DEFAULT_CREDITS)))
        except ValueError:
             user_data["credits"] = DEFAULT_CREDITS
    else:
        user_data["created_at"] = now
        user_data["plan"] = DEFAULT_PLAN
        user_data["credits"] = DEFAULT_CREDITS

    try:
        redis_user = RedisUser(**{k: v for k, v in user_data.items() if v is not None})
        redis_hash_data = redis_user.to_redis()
        
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.hset(user_key, mapping=redis_hash_data)
            email = redis_hash_data.get("email")
            if email:
                pipe.hset(EMAIL_TO_USER_ID_INDEX_KEY, email.lower(), raw_user_id)
            await pipe.execute()
        
        return await get_user(raw_user_id)
    except Exception as e:
        logger.error(f"Error creating/updating user {raw_user_id} in Redis: {e}", exc_info=True)
        return None

async def create_email_user(email: str, hashed_password: str) -> Optional[User]:
    redis_client = await get_redis_client()
    normalized_email = email.lower()

    existing_user_id = await redis_client.hget(EMAIL_TO_USER_ID_INDEX_KEY, normalized_email)
    if existing_user_id:
        logger.warning(f"Attempt to create user with existing email: {email}")
        return None

    raw_user_id = str(secrets.randbelow(9 * (10**20)) + 10**20)
    user_key = f"{USER_PREFIX}{raw_user_id}"
    now = datetime.now(timezone.utc)

    user_data = {
        "user_id": raw_user_id,
        "email": normalized_email,
        "hashed_password": hashed_password,
        "created_at": now,
        "updated_at": now,
        "email_verified": False,
        "signup_method": "email",
        "plan": DEFAULT_PLAN,
        "credits": DEFAULT_CREDITS
    }

    try:
        redis_user = RedisUser(**user_data)
        redis_hash_data = redis_user.to_redis()

        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.hset(user_key, mapping=redis_hash_data)
            pipe.hset(EMAIL_TO_USER_ID_INDEX_KEY, normalized_email, raw_user_id)
            await pipe.execute()
        
        logger.info(f"Redis pipeline executed for creating user {raw_user_id}. Attempting to fetch user for return.")
        return await get_user(raw_user_id)
    except Exception as e:
        logger.error(f"Error creating email user {raw_user_id}: {e}", exc_info=True)
        return None

async def get_user_id_by_paddle_customer_id(paddle_customer_id: str) -> Optional[str]:
    if not paddle_customer_id:
        return None
        
    redis_client = await get_redis_client()
    return await redis_client.hget(PADDLE_CUSTOMER_ID_TO_USER_ID_INDEX_KEY, paddle_customer_id)

async def update_user_plan(
    user_id: str,
    new_plan: str,
    credits_to_add: int,
    paddle_customer_id: Optional[str] = None
) -> Optional[User]:
    redis_client = await get_redis_client()
    user_key = f"{USER_PREFIX}{user_id}"

    raw_user_data = await redis_client.hgetall(user_key)
    if not raw_user_data:
        logger.warning(f"User {user_id} not found for plan update.")
        return None

    redis_user = RedisUser.from_redis(raw_user_data)
    if not redis_user:
        logger.error(f"Failed to parse existing user data for {user_id} during plan update.")
        return None

    redis_user.plan = new_plan
    current_credits = redis_user.credits if isinstance(redis_user.credits, int) else 0
    redis_user.credits = current_credits + credits_to_add 
    
    if paddle_customer_id is not None:
        redis_user.paddle_customer_id = paddle_customer_id
        
    redis_user.updated_at = datetime.now(timezone.utc)

    try:
        redis_hash_data = redis_user.to_redis()
        
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.hset(user_key, mapping=redis_hash_data)
            if paddle_customer_id and redis_user.email:
                pipe.hset(PADDLE_CUSTOMER_ID_TO_USER_ID_INDEX_KEY, paddle_customer_id, user_id)
            await pipe.execute()
            
        return User(**redis_user.model_dump())
    except Exception as e:
        logger.error(f"Error updating user plan for {user_id}: {e}", exc_info=True)
        return None

async def update_user_password(user_id: str, new_hashed_password: str) -> bool:
    """
    Updates the user's hashed password in Redis.
    """
    redis_client = await get_redis_client()
    user_key = f"{USER_PREFIX}{user_id}"

    # Check if user exists
    if not await redis_client.exists(user_key):
        logger.warning(f"Attempted to update password for non-existent user: {user_id}")
        return False

    try:
        # It's good practice to also update the 'updated_at' timestamp
        updated_at_iso = datetime.now(timezone.utc).isoformat()
        await redis_client.hmset(user_key, {
            "hashed_password": new_hashed_password,
            "updated_at": updated_at_iso
        })
        logger.info(f"Password updated successfully for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error updating password for user {user_id} in Redis: {e}", exc_info=True)
        return False