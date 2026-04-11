"""
Persistent Agent API
====================
REST endpoints for controlling and monitoring the 24/7 trading agent.

Read endpoints (status, live/status, brain, paper-trader) are open so
the dashboard can poll freely without a token.

All write/control endpoints require a valid JWT:
  Authorization: Bearer <token>
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.core.security import get_current_user

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _get():
    from app.agents.persistent_agent import get_agent
    return get_agent()


@router.get("/status")
async def get_status():
    """Full agent state snapshot — positions, trades, log, config. Open for dashboard polling."""
    return _get().get_status()


# ── Control (auth required) ───────────────────────────────────────────────────

@router.post("/start")
async def start_agent(_: dict = Depends(get_current_user)):
    agent = _get()
    await agent.start()
    agent.config["enabled"]      = True
    agent.config["auto_execute"] = True
    agent._schedule_db_save()
    return {"ok": True, "message": "Agent started", "config": agent.config}


@router.post("/stop")
async def stop_agent(_: dict = Depends(get_current_user)):
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
    enabled:              Optional[bool]  = None
    size_usdc:            Optional[float] = None
    min_confidence:       Optional[float] = None
    min_conditions:       Optional[int]   = None
    mode:                 Optional[str]   = None
    auto_execute:         Optional[bool]  = None
    leverage:             Optional[int]   = None
    strategy_overrides:   Optional[dict[str, StrategyOverride]] = None
    daily_loss_limit:     Optional[float] = None
    max_position_usdc:    Optional[float] = None


@router.post("/config")
async def update_config(patch: ConfigPatch, _: dict = Depends(get_current_user)):
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
async def reset_agent(_: dict = Depends(get_current_user)):
    _get().reset()
    return {"ok": True, "message": "Paper account reset"}


@router.post("/close/{strategy_key}")
async def close_position(strategy_key: str, _: dict = Depends(get_current_user)):
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
async def debug_agent(_: dict = Depends(get_current_user)):
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
async def force_scan(_: dict = Depends(get_current_user)):
    """Trigger an immediate strategy scan (for testing/debugging)."""
    agent = _get()
    if not agent._running:
        raise HTTPException(status_code=400, detail="Agent is not running")
    await agent._scan()
    return {"ok": True, "scan_count": agent.scan_count, "log": agent.log[:10]}


# ── Live Trading (BingX) ───────────────────────────────────────────────────────

@router.get("/live/status")
async def live_status():
    """Return live executor status — open BingX positions, daily P&L, circuit breaker."""
    agent = _get()
    if not agent._live:
        from app.core.config import settings
        keys_set = bool(settings.bingx_api_key)
        return {
            "connected":  False,
            "keys_set":   keys_set,
            "mode":       agent.config.get("mode", "paper"),
            "message":    "BingX keys not configured" if not keys_set else "Live executor not initialised (set mode=live to activate)",
        }
    return {
        "connected": True,
        "mode":      agent.config.get("mode"),
        **agent._live.status(),
    }


@router.post("/live/close/{strategy_key}")
async def live_close_position(strategy_key: str, _: dict = Depends(get_current_user)):
    """Manually close a live BingX position for the given strategy."""
    agent = _get()
    if not agent._live:
        raise HTTPException(status_code=400, detail="Live executor not active (mode is not 'live')")
    pos = agent._live.live_positions.get(strategy_key)
    if not pos:
        raise HTTPException(status_code=404, detail=f"No live position found for {strategy_key}")
    from app.agents.live_market_stream import LIVE_PRICES
    price = LIVE_PRICES.get("BTC/USDT", {}).get("last", pos["entry"])
    trade = await agent._live.close_position(strategy_key, price, "manual_api")
    if not trade:
        raise HTTPException(status_code=502, detail=f"BingX close failed: {getattr(agent._live, 'last_error', 'unknown')}")
    agent._record_trade_closure(strategy_key, pos, trade["pnl_usd"], trade["exit_price"], "manual_api", is_live=True)
    return {"ok": True, "trade": trade}


@router.get("/live/balance")
async def live_balance(_: dict = Depends(get_current_user)):
    """Fetch live BingX account USDT balance."""
    agent = _get()
    if not agent._live:
        raise HTTPException(status_code=400, detail="Live executor not active")
    bal = await agent._live.fetch_balance()
    return {"balance": bal}


@router.post("/live/reset-circuit-breaker")
async def reset_circuit_breaker(_: dict = Depends(get_current_user)):
    """Manually reset the daily loss circuit breaker (use with caution)."""
    agent = _get()
    if not agent._live:
        raise HTTPException(status_code=400, detail="Live executor not active")
    agent._live._halted    = False
    agent._live._daily_pnl = 0.0
    return {"ok": True, "message": "Circuit breaker reset — trading resumed"}


@router.post("/live/cancel-orphaned-orders")
async def cancel_orphaned_orders(_: dict = Depends(get_current_user)):
    """Cancel all open stop orders on BingX when there are no active positions."""
    agent = _get()
    if not agent._live:
        raise HTTPException(status_code=400, detail="Live executor not active")
    await agent._live._cancel_all_open_orders()
    return {"ok": True, "message": "All orphaned orders cancelled"}


# ── Master Brain ──────────────────────────────────────────────────────────

@router.get("/brain")
async def get_brain_status():
    """Full MasterBrain state — regime, trust, portfolio, decisions."""
    agent = _get()
    from app.agents.live_market_stream import LIVE_PRICES
    price = LIVE_PRICES.get("BTC/USDT", {}).get("last", 0)
    return agent.brain.get_status(agent.positions, price)


@router.post("/brain/reset-trust")
async def reset_brain_trust(_: dict = Depends(get_current_user)):
    """Reset all strategy trust scores to neutral (1.0)."""
    agent = _get()
    agent.brain.strategy_trust = {k: 1.0 for k in agent.brain.strategy_trust}
    agent.brain.consecutive_losses = 0
    agent._save_state()
    agent._schedule_db_save()
    return {"ok": True, "trust": agent.brain.strategy_trust}


# ── Paper Trader ──────────────────────────────────────────────────────────

@router.get("/paper-trader")
async def get_paper_trader():
    """Full paper trader status — balance, positions, trades, equity curve."""
    return _get().paper_trader.get_status()


@router.post("/paper-trader/reset")
async def reset_paper_trader(_: dict = Depends(get_current_user)):
    """Reset paper trader to $10K starting balance."""
    agent = _get()
    from app.agents.paper_trader import PaperTrader, STARTING_BALANCE
    agent.paper_trader = PaperTrader()
    agent._save_state()
    agent._schedule_db_save()
    return {"ok": True, "balance": STARTING_BALANCE}
