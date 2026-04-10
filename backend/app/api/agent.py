"""
Persistent Agent API
====================
REST endpoints for controlling and monitoring the 24/7 trading agent.
No auth required for read endpoints so the frontend can poll freely.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _get() :
    from app.agents.persistent_agent import get_agent
    return get_agent()


# ── Read ──────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    """Full agent state snapshot — positions, trades, log, config."""
    return _get().get_status()


# ── Control ───────────────────────────────────────────────────────────────────

@router.post("/start")
async def start_agent():
    agent = _get()
    await agent.start()
    # Force-enable trading even if stale DB config had it off
    agent.config["enabled"]      = True
    agent.config["auto_execute"] = True
    agent._schedule_db_save()
    return {"ok": True, "message": "Agent started", "config": agent.config}


@router.post("/stop")
async def stop_agent():
    agent = _get()
    agent.config["enabled"] = False
    await agent.stop()
    return {"ok": True, "message": "Agent stopped"}


class StrategyOverride(BaseModel):
    enabled:        Optional[bool]  = None
    size_usdc:      Optional[float] = None
    leverage:       Optional[int]   = None
    min_confidence: Optional[float] = None
    min_conditions: Optional[int]   = None

class ConfigPatch(BaseModel):
    enabled:             Optional[bool]  = None
    size_usdc:           Optional[float] = None
    min_confidence:      Optional[float] = None
    min_conditions:      Optional[int]   = None
    mode:                Optional[str]   = None
    auto_execute:        Optional[bool]  = None
    leverage:            Optional[int]   = None
    strategy_overrides:  Optional[dict[str, StrategyOverride]] = None


@router.post("/config")
async def update_config(patch: ConfigPatch):
    agent = _get()
    data  = patch.model_dump(exclude_none=True)
    # Convert StrategyOverride models to plain dicts for the agent
    if "strategy_overrides" in data and data["strategy_overrides"]:
        data["strategy_overrides"] = {
            k: {fk: fv for fk, fv in v.items() if fv is not None}
            for k, v in data["strategy_overrides"].items()
        }
    agent.update_config(data)
    return {"ok": True, "config": agent.config}


@router.post("/reset")
async def reset_agent():
    _get().reset()
    return {"ok": True, "message": "Paper account reset"}


@router.post("/close/{strategy_key}")
async def close_position(strategy_key: str):
    ok = _get().close_position(strategy_key)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No open position for {strategy_key}")
    return {"ok": True, "closed": strategy_key}


@router.get("/net-test")
async def network_test():
    """
    Test outbound HTTP from Railway — call this endpoint to see which price
    sources are reachable. Visit: <your-railway-url>/api/agent/net-test
    """
    import httpx, time
    results = {}
    sources = {
        "coingecko":    "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
        "blockchain":   "https://blockchain.info/ticker",
        "kraken":       "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
        "bybit":        "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT",
        "binance_us":   "https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT",
        "binance":      "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
    }
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        for name, url in sources.items():
            t0 = time.time()
            try:
                r = await client.get(url)
                ms = int((time.time() - t0) * 1000)
                results[name] = {"ok": r.status_code == 200, "status": r.status_code, "ms": ms}
            except Exception as e:
                ms = int((time.time() - t0) * 1000)
                results[name] = {"ok": False, "error": f"{type(e).__name__}: {e}", "ms": ms}
    working = [k for k, v in results.items() if v["ok"]]
    return {"working": working, "all": results}


@router.get("/debug")
async def debug_agent():
    """Detailed diagnostic — shows market data availability and strategy state."""
    from app.agents.live_market_stream import LIVE_CANDLES, LIVE_PRICES, LIVE_ORDERBOOK
    agent = _get()
    price_data = LIVE_PRICES.get("BTC/USDT", {})
    orderbook  = LIVE_ORDERBOOK.get("BTC/USDT")
    return {
        "agent_running":   agent._running,
        "scan_count":      agent.scan_count,
        "last_scan":       agent.last_scan,
        "config":          agent.config,
        "positions":       agent.positions,
        "live_price":      price_data.get("last", 0),
        "candles_1m":      len(LIVE_CANDLES.get("BTC/USDT:1m", [])),
        "candles_15m":     len(LIVE_CANDLES.get("BTC/USDT:15m", [])),
        "orderbook_bids":  len(orderbook.get("bids", [])) if orderbook else 0,
        "last_5_logs":     agent.log[:5],
    }


@router.post("/force-scan")
async def force_scan():
    """Trigger an immediate strategy scan (for testing/debugging)."""
    agent = _get()
    if not agent._running:
        raise HTTPException(status_code=400, detail="Agent is not running")
    await agent._scan()
    return {"ok": True, "scan_count": agent.scan_count, "log": agent.log[:10]}
