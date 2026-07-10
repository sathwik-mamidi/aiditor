from redis.asyncio import Redis

from app.utils.logger import logger

async def deduct_user_credits(redis: Redis, user_id: str, amount: int) -> bool:
    if not redis or not user_id or amount <= 0:
        logger.error(f"Invalid deduction request: user_id={user_id}, amount={amount}")
        return False

    try:
        redis_key = f"user:{user_id}"
        new_balance = await redis.hincrby(redis_key, "credits", -amount)
        logger.info(f"User {user_id}: deducted {amount} credits, balance={new_balance}")
        return True
    except Exception as e:
        logger.error(f"Redis error deducting credits for user {user_id}: {e}")
        return False

async def get_user_credits(redis: Redis, user_id: str) -> int:
    if not redis or not user_id:
        return 0
    try:
        credits = await redis.hget(f"user:{user_id}", "credits")
        return int(credits) if credits else 0
    except (ValueError, TypeError) as e:
        logger.error(f"Error converting credits to int for user {user_id}: {e}")
        return 0
    except Exception as e:
        logger.error(f"Redis error getting credits for user {user_id}: {e}")
        return 0

async def add_user_credits(redis: Redis, user_id: str, amount: int) -> int:
    if not redis or not user_id or amount <= 0:
        logger.error(f"Invalid credit addition request: user_id={user_id}, amount={amount}")
        return -1 
    try:
        redis_key = f"user:{user_id}"
        new_balance = await redis.hincrby(redis_key, "credits", amount)
        logger.info(f"User {user_id}: added {amount} credits, new balance={new_balance}")
        return int(new_balance)
    except Exception as e:
        logger.error(f"Redis error adding credits for user {user_id}: {e}")
        return -1