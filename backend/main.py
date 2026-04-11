import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import init_db
from app.core.redis_client import get_redis, close_redis
from app.api import auth, overview, strategies, positions, orders, risk, market, backtest, journal, settings as settings_router, websocket, paper as paper_router, agent as agent_router
from app.api import x_agent as x_agent_router
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
app.include_router(x_agent_router.router)

# ── Lifecycle ─────────────────────────────────────────────────────────────────
_background_tasks: list[asyncio.Task] = []


@app.on_event("startup")
async def startup():
    """
    Startup hook — MUST return quickly (< 10 s) so Railway health check passes.
    All slow/network operations are kicked off as background tasks.
    """
    logger.info("TradeOS starting up…")

    # Kick off all heavy init as a background task so the hook returns immediately
    asyncio.create_task(_background_init())

    logger.info("TradeOS accepting requests — background init in progress")


async def _background_init():
    """All slow startup work runs here, off the critical startup path."""
    await asyncio.sleep(0.5)  # tiny delay so Uvicorn finishes binding first

    # 1 — DB (SQLite fallback if PG unreachable; 10 s hard cap)
    try:
        await asyncio.wait_for(init_db(), timeout=10)
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"DB init error ({type(e).__name__}): {e} — using SQLite fallback")

    # 2 — Seed admin (already handles upsert; 10 s cap)
    try:
        from app.seeds.seed_data import seed
        await asyncio.wait_for(seed(), timeout=10)
    except Exception as e:
        logger.warning(f"Seed skipped: {e}")

    # 3 — Ensure Kashan/Manan admin always exists
    async def _ensure_kashan():
        from app.core.database import AsyncSessionLocal
        from app.core.security import hash_password
        from app.models.user import User
        from sqlalchemy import select
        async with AsyncSessionLocal() as s:
            r = await s.execute(select(User).where(User.username == "Kashan"))
            u = r.scalar_one_or_none()
            if not u:
                s.add(User(username="Kashan", hashed_password=hash_password("Manan"),
                            is_active=True, is_admin=True))
            else:
                u.hashed_password = hash_password("Manan")
                u.is_active = True
            await s.commit()
            logger.info("Admin Kashan ensured")
    try:
        await asyncio.wait_for(_ensure_kashan(), timeout=8)
    except Exception as e:
        logger.warning(f"Kashan admin ensure failed: {e}")

    # 4 — Redis listener (background, non-blocking by design)
    channels = [
        "market:ticker", "market:kline", "market:orderbook",
        "signal:new", "execution:order_placed",
        "execution:risk_block", "risk:kill_switch", "risk:position_closed",
        "journal:new",
    ]
    _background_tasks.append(asyncio.create_task(redis_listener(channels)))
    logger.info("Redis listener task created")

    # 5 — Live market stream (background WebSocket — don't block on connect)
    try:
        from app.agents.live_market_stream import create_live_stream_agent
        ls = create_live_stream_agent()
        asyncio.create_task(ls.start())
        logger.info("Live market stream task created")
    except Exception as e:
        logger.warning(f"Live stream create failed: {e}")

    # 6 — Signal / execution pipeline (background)
    try:
        from app.agents.signal_agent import SignalAgent
        from app.agents.execution_agent import ExecutionAgent
        from app.exchange.paper_trading import PaperTradingEngine
        paper_engine = PaperTradingEngine()
        exec_agent  = ExecutionAgent(exchange=paper_engine, mode="paper")
        sig_agent   = SignalAgent(execution_agent=exec_agent)
        asyncio.create_task(sig_agent.start())
        logger.info("Signal agent task created")
    except Exception as e:
        logger.warning(f"Signal pipeline create failed: {e}")

    # 7 — 24/7 persistent trading agent
    try:
        from app.agents.persistent_agent import start_agent
        await asyncio.wait_for(start_agent(), timeout=12)
        logger.info("Persistent trading agent started")
    except Exception as e:
        logger.warning(f"Persistent agent failed: {e}")

    logger.info("Background init complete — all systems running")


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
