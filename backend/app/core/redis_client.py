"""
Redis client with automatic in-memory fallback for local dev.
When Redis is unavailable, all operations use a module-level dict —
pub/sub events are delivered via asyncio queues.
"""
import json
import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── In-memory fallback store ──────────────────────────────────────────────────
_mem_store: dict[str, Any] = {}
_subscribers: list[asyncio.Queue] = []
_use_memory = False

try:
    import redis.asyncio as aioredis
    from app.core.config import settings as _settings
    _redis_instance: Optional[aioredis.Redis] = None
except ImportError:
    _use_memory = True


async def _try_connect() -> Optional[Any]:
    global _redis_instance, _use_memory
    if _use_memory:
        return None
    if _redis_instance is not None:
        return _redis_instance
    try:
        from app.core.config import settings
        import redis.asyncio as aioredis
        r = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
        )
        await r.ping()
        _redis_instance = r
        logger.info("Connected to Redis")
        return r
    except Exception as e:
        logger.warning(f"Redis unavailable ({e}) — using in-memory fallback")
        _use_memory = True
        return None


async def get_redis():
    return await _try_connect()


async def redis_set(key: str, value: Any, ex: int = 300) -> None:
    r = await _try_connect()
    if r:
        await r.set(key, json.dumps(value), ex=ex)
    else:
        _mem_store[key] = json.dumps(value)


async def redis_get(key: str) -> Optional[Any]:
    r = await _try_connect()
    if r:
        raw = await r.get(key)
    else:
        raw = _mem_store.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def redis_delete(key: str) -> None:
    r = await _try_connect()
    if r:
        await r.delete(key)
    else:
        _mem_store.pop(key, None)


async def redis_publish(channel: str, message: Any) -> None:
    r = await _try_connect()
    if r:
        await r.publish(channel, json.dumps(message))
    else:
        # Deliver to in-process subscribers
        payload = json.dumps({"channel": channel, "data": message})
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def _subscribe_in_memory() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.append(q)
    return q


async def close_redis() -> None:
    global _redis_instance
    if _redis_instance:
        await _redis_instance.aclose()
        _redis_instance = None
