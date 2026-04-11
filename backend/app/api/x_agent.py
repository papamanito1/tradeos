"""
X Agent API — dashboard control for @Tradeous X posting.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/x-agent", tags=["x-agent"])


def _publisher():
    try:
        from app.agents.persistent_agent import _agent_instance
        if _agent_instance and hasattr(_agent_instance, "x_publisher"):
            return _agent_instance.x_publisher
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


# ── Manual triggers ───────────────────────────────────────────────────────────

@router.post("/trigger/news")
async def trigger_news():
    pub = _publisher()
    if not pub:
        return {"ok": False, "error": "Agent not running"}
    ok = await pub.post_news()
    return {"ok": ok}


@router.post("/trigger/fear-greed")
async def trigger_fear_greed():
    pub = _publisher()
    if not pub:
        return {"ok": False, "error": "Agent not running"}
    ok = await pub.post_fear_greed()
    return {"ok": ok}


@router.post("/trigger/hot-take")
async def trigger_hot_take():
    pub = _publisher()
    if not pub:
        return {"ok": False, "error": "Agent not running"}
    pub.post_hot_take()
    return {"ok": True}


@router.post("/trigger/philosophy")
async def trigger_philosophy():
    pub = _publisher()
    if not pub:
        return {"ok": False, "error": "Agent not running"}
    pub.post_philosophy()
    return {"ok": True}


@router.post("/trigger/engagement")
async def trigger_engagement():
    pub = _publisher()
    if not pub:
        return {"ok": False, "error": "Agent not running"}
    pub.post_engagement()
    return {"ok": True}


@router.post("/trigger/hourly")
async def trigger_hourly():
    pub = _publisher()
    if not pub:
        return {"ok": False, "error": "Agent not running"}
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
    if not pub:
        return {"ok": False, "error": "Agent not running"}
    if not req.text or len(req.text.strip()) < 3:
        return {"ok": False, "error": "Text too short"}
    ok = await pub.post_manual(req.text.strip())
    return {"ok": ok}


# ── Reset cooldowns (for testing) ─────────────────────────────────────────────

@router.post("/reset-cooldowns")
async def reset_cooldowns():
    pub = _publisher()
    if not pub:
        return {"ok": False}
    for k in pub._last:
        pub._last[k] = 0
    return {"ok": True, "message": "All cooldowns reset"}
