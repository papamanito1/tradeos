import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.core.redis_client import get_redis, close_redis
from app.api import auth, overview, strategies, positions, orders, risk, market, backtest, journal, settings as settings_router, websocket
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

# ── Lifecycle ─────────────────────────────────────────────────────────────────
_background_tasks: list[asyncio.Task] = []


@app.on_event("startup")
async def startup():
    logger.info("TradeOS starting up...")

    # Init DB
    await init_db()
    logger.info("Database initialized")

    # Seed admin if missing
    try:
        from app.seeds.seed_data import seed
        await seed()
    except Exception as e:
        logger.warning(f"Seed skipped: {e}")

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

    # Start Live Market Stream Agent (Binance public WebSocket — no API key needed)
    try:
        from app.agents.live_market_stream import create_live_stream_agent
        live_stream = create_live_stream_agent()
        await live_stream.start()
        logger.info("Live market stream agent started (Binance public WS)")
    except Exception as e:
        logger.warning(f"Live stream agent failed to start: {e}")

    # Start Redis pub/sub listener (broadcasts WS events to all frontend clients)
    channels = [
        "market:ticker", "market:kline", "market:orderbook",
        "signal:new", "execution:order_placed",
        "execution:risk_block", "risk:kill_switch", "risk:position_closed",
        "journal:new",
    ]
    task = asyncio.create_task(redis_listener(channels))
    _background_tasks.append(task)
    logger.info("Redis listener started")

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
    return {"status": "ok", "version": "1.0.0", "mode": settings.trading_mode}
