import redis.asyncio as redis
from typing import Optional, Union, TypeVar, Type
from pydantic import BaseModel

from app.config.config import config
from app.utils.logger import logger

redis_client: Optional[redis.Redis] = None
T = TypeVar('T', bound=BaseModel)

async def init_redis_client() -> redis.Redis:
    global redis_client
    if redis_client is None:
        try:
            redis_client = redis.from_url(
                config["REDIS_URL"],
                decode_responses=True,
                retry_on_timeout=True,
                socket_connect_timeout=5,
            )
            await redis_client.ping()
            logger.info("Redis connected")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            redis_client = None
            raise
    return redis_client

async def get_redis_client() -> redis.Redis:
    if redis_client is None:
        await init_redis_client()
    if redis_client is None:
        raise ConnectionError("Failed to get Redis client connection")
    return redis_client

async def close_redis_client() -> None:
    global redis_client
    if redis_client:
        try:
            await redis_client.close()
            await redis_client.connection_pool.disconnect()
            logger.info("Redis connection closed")
        except Exception as e:
            logger.error(f"Error closing Redis connection: {e}")
        finally:
            redis_client = None

async def get_model_from_redis(model_cls: Type[T], key: str) -> Optional[T]:
    r = await get_redis_client()
    data = await r.hgetall(key)
    
    if not data:
        logger.warning(f"[get_model_from_redis] No data found (HGETALL returned empty) for key '{key}'.")
        return None
        
    try:
        if hasattr(model_cls, 'from_redis'):
            return model_cls.from_redis(data)
            
        parsed_data = {}
        for k, v in data.items():
            field_annotation = model_cls.__annotations__.get(k)
            is_bool = False
            
            if field_annotation:
                origin = getattr(field_annotation, '__origin__', None)
                args = getattr(field_annotation, '__args__', [])
                if field_annotation is bool or (origin is Union and bool in args):
                    is_bool = True
                    
            if is_bool and isinstance(v, str):
                parsed_data[k] = v.lower() == 'true' if v.lower() in ('true', 'false') else v
            else:
                parsed_data[k] = v

        if model_cls.__name__ == 'FileRecord' and 'id' in parsed_data and 'file_id' not in parsed_data:
            parsed_data['file_id'] = parsed_data.pop('id')
        elif model_cls.__name__ == 'Chat' and 'id' in parsed_data and 'chat_id' not in parsed_data:
            parsed_data['chat_id'] = parsed_data.pop('id')

        return model_cls(**parsed_data)
    except Exception as e:
        logger.error(f"Error parsing {model_cls.__name__} key {key}: {e}")
        return None
