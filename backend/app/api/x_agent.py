"""
X Agent API — dashboard control for @Tradeous X posting.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/x-agent", tags=["x-agent"])


def _publisher():
    try:
        from app.agents.persistent_agent import get_agent
        agent = get_agent()
        if agent and hasattr(agent, "x_publisher"):
            return agent.x_publisher
    except Exception:
        pass
    return None


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    pub = _publisher()
    if not pub:
        return {"enabled": False, "recent_posts": [], "error": "Agent not running"}
    return pub.status()


@router.get("/debug-env")
async def debug_env():
    """Temporary: check what env vars the backend actually sees."""
    import os
    token = os.environ.get("X_AUTH_TOKEN", "")
    ct0   = os.environ.get("X_CT0", "")
    return {
        "X_AUTH_TOKEN_set": bool(token),
        "X_AUTH_TOKEN_len": len(token),
        "X_AUTH_TOKEN_preview": token[:8] + "..." if token else "(empty)",
        "X_CT0_set": bool(ct0),
        "X_CT0_len": len(ct0),
        "X_CT0_preview": ct0[:8] + "..." if ct0 else "(empty)",
    }


# ── Manual triggers ───────────────────────────────────────────────────────────

def _check(pub) -> dict | None:
    if not pub:
        return {"ok": False, "error": "Agent not running"}
    if not pub.enabled:
        return {"ok": False, "error": "X_AUTH_TOKEN / X_CT0 not set in Railway env vars"}
    return None


@router.post("/trigger/news")
async def trigger_news():
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["news"] = 0
    ok = await pub.post_news()
    return {"ok": ok, "error": None if ok else "Post failed — check Railway logs"}


@router.post("/trigger/fear-greed")
async def trigger_fear_greed():
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["fear_greed"] = 0
    ok = await pub.post_fear_greed()
    return {"ok": ok, "error": None if ok else "Post failed — check Railway logs"}


@router.post("/trigger/hot-take")
async def trigger_hot_take():
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["hot_take"] = 0
    pub.post_hot_take()
    return {"ok": True}


@router.post("/trigger/philosophy")
async def trigger_philosophy():
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["philosophy"] = 0
    pub.post_philosophy()
    return {"ok": True}


@router.post("/trigger/engagement")
async def trigger_engagement():
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["engagement"] = 0
    pub.post_engagement()
    return {"ok": True}


@router.post("/trigger/hourly")
async def trigger_hourly():
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["hourly"] = 0
    try:
        from app.agents.live_market_stream import LIVE_PRICES
        price = LIVE_PRICES.get("BTC/USDT", {}).get("last", 0.0)
    except Exception:
        price = 0.0
    pub.post_hourly(btc_price=price, open_positions=[], daily_pnl=0.0,
                    regime="unknown", regime_stability="unknown")
    return {"ok": True}


# ── Manual compose ────────────────────────────────────────────────────────────

class ManualPostRequest(BaseModel):
    text: str


@router.post("/post")
async def manual_post(req: ManualPostRequest):
    pub = _publisher()
    if err := _check(pub): return err
    if not req.text or len(req.text.strip()) < 3:
        return {"ok": False, "error": "Text too short"}
    ok = await pub.post_manual(req.text.strip())
    return {"ok": ok, "error": None if ok else "Post failed — X credentials may be expired"}


# ── Reset cooldowns ───────────────────────────────────────────────────────────

@router.post("/reset-cooldowns")
async def reset_cooldowns():
    pub = _publisher()
    if not pub:
        return {"ok": False}
    for k in list(pub._last.keys()):
        pub._last[k] = 0
    return {"ok": True, "message": "All cooldowns reset — next scheduler tick will post everything"}


# ── Fire all now ──────────────────────────────────────────────────────────────

@router.post("/fire-all")
async def fire_all():
    """Reset all cooldowns and immediately post every content type."""
    pub = _publisher()
    if err := _check(pub): return err

    results = {}

    # Reset all cooldowns
    for k in list(pub._last.keys()):
        pub._last[k] = 0

    # Sync posts (fire-and-forget via create_task)
    pub.post_hot_take()
    results["hot_take"] = "fired"

    pub.post_philosophy()
    results["philosophy"] = "fired"

    pub.post_engagement()
    results["engagement"] = "fired"

    # Async posts
    try:
        from app.agents.live_market_stream import LIVE_PRICES
        price = LIVE_PRICES.get("BTC/USDT", {}).get("last", 0.0)
    except Exception:
        price = 0.0

    pub._last["hourly"] = 0
    pub.post_hourly(btc_price=price, open_positions=[], daily_pnl=0.0,
                    regime="unknown", regime_stability="starting up")
    results["hourly"] = "fired"

    ok_news = await pub.post_news()
    results["news"] = "posted" if ok_news else "failed"

    ok_fg = await pub.post_fear_greed()
    results["fear_greed"] = "posted" if ok_fg else "failed"

    return {"ok": True, "results": results}
