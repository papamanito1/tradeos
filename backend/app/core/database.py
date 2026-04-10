import logging

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.core.config import settings

logger = logging.getLogger(__name__)

_SQLITE_FALLBACK = "sqlite+aiosqlite:///./tradeos_fallback.db"

# Detect SQLite for local dev
_is_sqlite = settings.database_url.startswith("sqlite")

def _make_engine(url: str, is_sqlite: bool):
    kwargs: dict = {"echo": settings.environment == "development"}
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10
        kwargs["connect_args"] = {"server_settings": {"application_name": "tradeos"}}
    return create_async_engine(url, **kwargs)

engine = _make_engine(settings.database_url, _is_sqlite)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Initialise DB schema. If PostgreSQL is unreachable, fall back to SQLite
    so the app still starts and the persistent agent can run via file state.
    """
    global engine, AsyncSessionLocal, _is_sqlite

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info(f"Database ready — {settings.database_url[:40]}…")
    except Exception as primary_err:
        logger.warning(
            f"Primary DB unreachable ({type(primary_err).__name__}: {primary_err}). "
            f"Falling back to SQLite — trade history will be local only."
        )
        try:
            # Recreate engine with SQLite fallback
            engine = _make_engine(_SQLITE_FALLBACK, is_sqlite=True)
            AsyncSessionLocal = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
            _is_sqlite = True
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("SQLite fallback database initialised — app running in degraded DB mode")
        except Exception as fallback_err:
            logger.error(f"SQLite fallback also failed: {fallback_err} — continuing without DB")
