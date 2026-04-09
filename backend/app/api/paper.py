"""
Paper Trading API
Endpoints for resetting balance, querying state, and fetching live activity.
"""
from fastapi import APIRouter, Depends
from app.core.security import get_current_user
from app.core.redis_client import redis_set, redis_get, redis_publish

router = APIRouter(prefix="/api/paper", tags=["paper"])

PAPER_BALANCE_KEY  = "paper:balance"
PAPER_ORDERS_KEY   = "paper:orders"
INITIAL_BALANCE    = 10_000.0


@router.post("/reset")
async def reset_paper_account(current_user: dict = Depends(get_current_user)):
    """Reset paper trading account to $10,000 and clear all open positions/orders."""
    fresh_state = {
        "balance_usd": INITIAL_BALANCE,
        "positions":   {},
        "equity":      INITIAL_BALANCE,
    }
    await redis_set(PAPER_BALANCE_KEY, fresh_state, ex=86400 * 365)
    await redis_set(PAPER_ORDERS_KEY,  [],           ex=86400 * 365)
    await redis_publish("execution:order_placed", {
        "event":   "paper_reset",
        "message": f"Paper account reset to ${INITIAL_BALANCE:,.0f}",
    })
    return {"ok": True, "balance": INITIAL_BALANCE, "message": "Paper account reset to $10,000"}


@router.get("/state")
async def get_paper_state(current_user: dict = Depends(get_current_user)):
    """Current paper balance + positions."""
    state  = await redis_get(PAPER_BALANCE_KEY)
    orders = await redis_get(PAPER_ORDERS_KEY) or []
    if not state:
        state = {"balance_usd": INITIAL_BALANCE, "positions": {}, "equity": INITIAL_BALANCE}
    return {
        "balance_usd": state.get("balance_usd", INITIAL_BALANCE),
        "equity":      state.get("equity",      INITIAL_BALANCE),
        "positions":   state.get("positions",   {}),
        "order_count": len(orders),
        "filled_orders": [o for o in orders if o.get("status") == "filled"][-20:],
    }


@router.get("/activity")
async def get_activity(
    limit: int = 40,
    current_user: dict = Depends(get_current_user),
):
    """
    Merged feed of recent signals + paper orders, newest first.
    Used by the dashboard live activity panel.
    """
    from app.core.signal_cache import get_all as get_all_signals

    events: list[dict] = []

    # ── Signals ───────────────────────────────────────────────────────────────
    all_signals = get_all_signals()
    for sym, sigs in all_signals.items():
        for s in sigs:
            events.append({
                "kind":          "signal",
                "timestamp":     s["timestamp"],
                "symbol":        s["symbol"],
                "direction":     s["direction"],
                "entry":         s.get("entry", 0),
                "sl":            s.get("sl"),
                "tp":            s.get("tp"),
                "confidence":    s.get("confidence", 0),
                "strategy_name": s.get("strategy_name", ""),
                "reasoning":     s.get("reasoning", ""),
                "timeframe":     s.get("timeframe", ""),
            })

    # ── Paper orders ──────────────────────────────────────────────────────────
    orders = await redis_get(PAPER_ORDERS_KEY) or []
    for o in orders:
        if o.get("status") == "filled":
            events.append({
                "kind":          "trade",
                "timestamp":     o.get("created_at", ""),
                "symbol":        o.get("symbol", ""),
                "direction":     "long" if o.get("side") == "buy" else "short",
                "side":          o.get("side", ""),
                "amount":        o.get("amount", 0),
                "fill_price":    o.get("average_fill_price", 0),
                "fee":           o.get("fee", 0),
                "strategy_name": "Paper Trade",
            })

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:limit]
