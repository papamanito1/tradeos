import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import init_db
from app.core.redis_client import get_redis, close_redis
from app.api import auth, overview, strategies, positions, orders, risk, market, backtest, journal, settings as settings_router, websocket, paper as paper_router, agent as agent_router
from app.websockets.manager import redis_listener
from app.risk.risk_engine import RiskConfig, update_risk_engine
from app.exchange.paper_trading import PaperTradingEngine

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TradeOS API",
    description="Crypto Trading Operating System",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(overview.router)
app.include_router(strategies.router)
app.include_router(positions.router)
app.include_router(orders.router)
app.include_router(risk.router)
app.include_router(market.router)
app.include_router(backtest.router)
app.include_router(journal.router)
app.include_router(settings_router.router)
app.include_router(websocket.router)
app.include_router(paper_router.router)
app.include_router(agent_router.router)

# ── Lifecycle ─────────────────────────────────────────────────────────────────
_background_tasks: list[asyncio.Task] = []


@app.on_event("startup")
async def startup():
    logger.info("TradeOS starting up...")

    # Init DB (non-fatal — falls back to SQLite if PostgreSQL is unreachable)
    try:
        await init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"DB init failed entirely: {e} — continuing without persistent DB")

    # Seed admin if missing
    try:
        from app.seeds.seed_data import seed
        await seed()
    except Exception as e:
        logger.warning(f"Seed skipped: {e}")

    # ── Always ensure Kashan/Manan admin exists (survives SQLite resets) ──────
    try:
        from app.core.database import AsyncSessionLocal
        from app.core.security import hash_password
        from app.models.user import User
        from sqlalchemy import select, delete

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.username == "Kashan"))
            user = result.scalar_one_or_none()
            if not user:
                session.add(User(
                    username="Kashan",
                    hashed_password=hash_password("Manan"),
                    is_active=True,
                    is_admin=True,
                ))
            else:
                user.hashed_password = hash_password("Manan")
                user.is_active = True
            await session.commit()
            logger.info("Admin user Kashan ensured")
    except Exception as e:
        logger.warning(f"Kashan admin ensure failed: {e}")

    # Load risk settings from DB
    try:
        from app.core.database import AsyncSessionLocal
        from app.models.risk_settings import RiskSettings
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(RiskSettings).limit(1))
            rs = result.scalar_one_or_none()
            if rs:
                update_risk_engine(RiskConfig(
                    max_daily_loss_usd=rs.max_daily_loss_usd,
                    max_daily_loss_pct=rs.max_daily_loss_pct,
                    max_position_size_usd=rs.max_position_size_usd,
                    max_leverage=rs.max_leverage,
                    max_open_trades=rs.max_open_trades,
                    symbol_blacklist=rs.symbol_blacklist,
                ))
    except Exception as e:
        logger.warning(f"Risk settings load failed: {e}")

    # Start Redis pub/sub listener FIRST so the in-memory queue is registered
    # before the market stream starts publishing events (avoids race condition).
    channels = [
        "market:ticker", "market:kline", "market:orderbook",
        "signal:new", "execution:order_placed",
        "execution:risk_block", "risk:kill_switch", "risk:position_closed",
        "journal:new",
    ]
    task = asyncio.create_task(redis_listener(channels))
    _background_tasks.append(task)
    # Yield once so the listener task runs its setup code before we start streaming
    await asyncio.sleep(0)
    logger.info("Redis listener started")

    # Start Live Market Stream Agent (Binance public WebSocket — no API key needed)
    try:
        from app.agents.live_market_stream import create_live_stream_agent
        live_stream = create_live_stream_agent()
        await live_stream.start()
        logger.info("Live market stream agent started (Binance public WS)")
    except Exception as e:
        logger.warning(f"Live stream agent failed to start: {e}")

    # ── Trading pipeline: Signal → Risk → Execution (paper) ──────────────────
    try:
        from app.agents.signal_agent import SignalAgent
        from app.agents.execution_agent import ExecutionAgent
        from app.exchange.paper_trading import PaperTradingEngine

        paper_engine = PaperTradingEngine()
        exec_agent = ExecutionAgent(exchange=paper_engine, mode="paper")
        signal_agent = SignalAgent(execution_agent=exec_agent)

        await signal_agent.start()
        logger.info("Signal agent started — strategies will run every 60 s")
    except Exception as e:
        logger.warning(f"Signal/execution pipeline failed to start: {e}")

    # Start 24/7 persistent trading agent
    try:
        from app.agents.persistent_agent import start_agent
        await start_agent()
        logger.info("Persistent trading agent started — running 24/7 on server")
    except Exception as e:
        logger.warning(f"Persistent agent failed to start: {e}")

    logger.info("TradeOS ready")


@app.on_event("shutdown")
async def shutdown():
    for task in _background_tasks:
        task.cancel()
    try:
        from app.agents.live_market_stream import get_live_stream_agent
        agent = get_live_stream_agent()
        if agent:
            await agent.stop()
    except Exception:
        pass
    await close_redis()
    logger.info("TradeOS shut down")


@app.get("/health")
async def health():
    import os
    return {
        "status":           "ok",
        "version":          "1.0.4",
        "mode":             settings.trading_mode,
        "bingx_configured": bool(settings.bingx_api_key),
        "admin_username":   settings.admin_username,   # shows expected login username
    }


@app.post("/force-reseed")
async def force_reseed():
    """Emergency: re-create admin user with current env var credentials. Call once after deploy."""
    try:
        from app.seeds.seed_data import seed
        await seed()
        return {
            "ok":       True,
            "username": settings.admin_username,
            "message":  f"Admin user '{settings.admin_username}' created/updated. Use this username to log in.",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


class AdminCredentials(BaseModel):
    username: str = "Kashan"
    password: str = "Manan"

@app.post("/reset-admin-password")
async def reset_admin_password(creds: AdminCredentials = AdminCredentials()):
    """Emergency: create/update admin user with given credentials."""
    try:
        from app.core.database import AsyncSessionLocal
        from app.core.security import hash_password
        from app.models.user import User
        from sqlalchemy import select, delete

        async with AsyncSessionLocal() as session:
            # Delete all existing users to avoid conflicts
            await session.execute(delete(User))
            # Create fresh admin
            user = User(
                username=creds.username,
                hashed_password=hash_password(creds.password),
                is_active=True,
                is_admin=True,
            )
            session.add(user)
            await session.commit()
        return {
            "ok":       True,
            "username": creds.username,
            "message":  f"Admin user set to '{creds.username}'. You can now log in.",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
