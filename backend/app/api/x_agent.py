"""
X Agent API — dashboard control for @Tradeous X posting.
"""
from __future__ import annotations

import random
import time
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/x-agent", tags=["x-agent"])

# In-memory queue of tweets to be posted by the local poster script
_tweet_queue: list[dict] = []
_posted_ids:  set[str]   = set()


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


# ── Local poster queue (bypasses datacenter IP block) ─────────────────────────
# The local_poster.py script on the user's PC polls /next-post and posts via
# Playwright with their real residential IP + Edge session.

@router.get("/next-post")
async def next_post():
    """Return the next queued tweet for the local poster to send."""
    import os
    from app.agents import x_publisher as xp
    from app.agents.x_publisher import (
        HOT_TAKES, PHILOSOPHY_POSTS, ENGAGEMENT_QUESTIONS,
        REGIME_QUIPS, NEWS_FEEDS,
    )
    from datetime import datetime, timezone

    pub = _publisher()
    now = time.time()

    # Decide what to post based on cooldowns (default to always-ready if pub not available)
    def _ok(key, cooldown):
        return pub._cooldown_ok(key, cooldown) if pub else True

    post_type = None
    text = ""

    if _ok("hourly", xp.HOURLY_COOLDOWN):
        try:
            from app.agents.live_market_stream import LIVE_PRICES
            price = LIVE_PRICES.get("BTC/USDT", {}).get("last", 0.0)
        except Exception:
            price = 0.0
        utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
        quip = random.choice(xp.REGIME_QUIPS.get("unknown", ["Watching the market."]))
        price_str = f"${price:,.0f}" if price > 0 else "loading..."
        text = (
            f"\U0001f916 BTC HOURLY \u2014 {utc}\n\n"
            f"Price: {price_str}\n"
            f"No open positions. Watching.\n\n"
            f"{quip}\n\n"
            f"#Bitcoin #BTC #Crypto"
        )
        post_type = "hourly"

    elif _ok("hot_take", xp.HOT_TAKE_COOLDOWN):
        text = random.choice(HOT_TAKES)
        post_type = "hot_take"

    elif _ok("philosophy", xp.PHILOSOPHY_COOLDOWN):
        text = random.choice(PHILOSOPHY_POSTS)
        post_type = "philosophy"

    elif _ok("engagement", xp.ENGAGEMENT_COOLDOWN):
        text = random.choice(ENGAGEMENT_QUESTIONS)
        post_type = "engagement"

    if not post_type or not text:
        return {"has_post": False}

    qid = f"{post_type}_{int(now)}"
    return {"has_post": True, "id": qid, "type": post_type, "text": text[:280]}


class ConfirmRequest(BaseModel):
    id: str
    post_type: str
    tweet_id: str = ""


@router.post("/confirm-post")
async def confirm_post(req: ConfirmRequest):
    """Called by local poster after successfully posting — updates cooldowns."""
    pub = _publisher()
    if pub:
        key = req.post_type.replace("-", "_")
        if key in pub._last:
            pub._last[key] = time.time()
        pub._recent_posts.append({
            "id": req.tweet_id or req.id,
            "type": req.post_type,
            "text": f"Posted via local poster ({req.post_type})",
            "ts": time.time(),
            "url": f"https://x.com/tradeous/status/{req.tweet_id}" if req.tweet_id else "",
        })
    return {"ok": True}
