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


@router.get("/creds")
async def get_creds(secret: str = ""):
    """Return auth cookies so local_poster.py can post from the residential IP.
    Requires POSTER_SECRET env var to match the `secret` query param."""
    import os
    expected = os.environ.get("POSTER_SECRET", "")
    if not expected or secret != expected:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")
    pub = _publisher()
    if not pub or not pub.enabled:
        return {"ok": False}
    return {"ok": True, "a": pub._auth_token, "c": pub._ct0}




# ── Manual triggers ───────────────────────────────────────────────────────────

async def _send_now(pub, post_type: str, text: str) -> dict:
    """
    Queue tweet for local_poster.py (Playwright via real Edge browser).
    Also tries direct Railway send — returns the text so frontend can post via localhost:4242.
    """
    text = text[:280]
    # Try direct Railway send (works if curl_cffi can bypass X IP block)
    ok = await pub._send_tweet(text, post_type)
    if ok:
        pub._touch(post_type.replace("-", "_"))
        return {"ok": True, "queued": False, "posted": True, "text": text, "message": "Posted ✓"}
    # Add to queue so local_poster.py can pick it up
    import uuid
    qid = str(uuid.uuid4())[:8] + f"_{post_type}"
    _tweet_queue.append({"id": qid, "type": post_type, "text": text, "ts": time.time()})
    # Return the text so the frontend can forward it to local_poster.py directly
    return {"ok": True, "queued": True, "posted": False, "text": text,
            "id": qid, "message": "Ready — sending via local poster"}


def _check(pub) -> dict | None:
    if not pub:
        return {"ok": False, "error": "Agent not running"}
    if not pub.enabled:
        return {"ok": False, "error": "X_AUTH_TOKEN / X_CT0 not set in Railway env vars"}
    return None


def _gen_hourly_text(pub) -> str:
    import random
    from datetime import datetime, timezone
    from app.agents import x_publisher as xp
    try:
        from app.agents.live_market_stream import LIVE_PRICES
        price = LIVE_PRICES.get("BTC/USDT", {}).get("last", 0.0)
    except Exception:
        price = 0.0
    utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
    quip = random.choice(xp.REGIME_QUIPS.get("unknown", ["Watching the market."]))
    price_str = f"${price:,.0f}" if price > 0 else "loading..."
    return (
        f"\U0001f916 BTC HOURLY \u2014 {utc}\n\n"
        f"Price: {price_str}\n"
        f"No open positions. Watching.\n\n"
        f"{quip}\n\n"
        f""
    )


@router.post("/trigger/news")
async def trigger_news():
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["news"] = 0
    story = await pub._fetch_top_news()
    if not story:
        return {"ok": False, "error": "Could not fetch news"}
    hooks = ["My take:", "Translation for traders:", "Signal implication:", "Algo opinion:", "The real story:"]
    comments = ["Watching for BTC reaction.", "Monitoring closely.", "Eyes on $BTC.", "Price is the final word."]
    text = (
        f"\U0001f4f0 CRYPTO NEWS\n\n"
        f"\u201c{story['title'][:120]}\u201d\n\n"
        f"{random.choice(hooks)} {random.choice(comments)}\n\n"
    )
    if story.get("link"):
        remaining = 280 - len(text)
        if remaining > 30:
            text += story["link"][:remaining]
    return await _send_now(pub, "news", text)


@router.post("/trigger/fear-greed")
async def trigger_fear_greed():
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["fear_greed"] = 0
    data = await pub._fetch_fear_greed()
    if not data:
        return {"ok": False, "error": "Could not fetch Fear & Greed"}
    from app.agents.x_publisher import FEAR_GREED_COMMENTARY
    score = int(data.get("value", 50))
    label = data.get("value_classification", "Neutral")
    templates = FEAR_GREED_COMMENTARY.get(label, FEAR_GREED_COMMENTARY["Neutral"])
    text = pub.memory.pick(f"fear_greed_{label}", templates).format(score=score, label=label)
    return await _send_now(pub, "fear_greed", text)


@router.post("/trigger/hot-take")
async def trigger_hot_take():
    from app.agents.x_publisher import HOT_TAKES
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["hot_take"] = 0
    text = pub.memory.pick("hot_take", HOT_TAKES)
    return await _send_now(pub, "hot_take", text)


@router.post("/trigger/philosophy")
async def trigger_philosophy():
    from app.agents.x_publisher import PHILOSOPHY_POSTS
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["philosophy"] = 0
    text = pub.memory.pick("philosophy", PHILOSOPHY_POSTS)
    return await _send_now(pub, "philosophy", text)


@router.post("/trigger/engagement")
async def trigger_engagement():
    from app.agents.x_publisher import ENGAGEMENT_QUESTIONS
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["engagement"] = 0
    text = pub.memory.pick("engagement", ENGAGEMENT_QUESTIONS)
    return await _send_now(pub, "engagement", text)


@router.post("/trigger/hourly")
async def trigger_hourly():
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["hourly"] = 0
    return await _send_now(pub, "hourly", _gen_hourly_text(pub))


# ── Test post (debug) ─────────────────────────────────────────────────────────

@router.post("/test-post")
async def test_post():
    """Debug endpoint — tries to post a test tweet and returns the exact error."""
    pub = _publisher()
    if err := _check(pub): return err
    text = f"Test post from Tradeous · {int(time.time())}"
    ok = await pub._send_tweet(text, "test")
    return {
        "ok": ok,
        "method_tried": "v1.1 + GraphQL",
        "last_error": pub._last_error if not ok else None,
        "curl_cffi_available": True,  # it's installed per requirements.txt
    }


# ── Manual compose ────────────────────────────────────────────────────────────

class ManualPostRequest(BaseModel):
    text: str


@router.post("/post")
async def manual_post(req: ManualPostRequest):
    pub = _publisher()
    if err := _check(pub): return err
    if not req.text or len(req.text.strip()) < 3:
        return {"ok": False, "error": "Text too short"}
    ok = await pub._send_tweet(req.text.strip(), "manual")
    if ok:
        return {"ok": True, "message": "Posted ✓"}
    return {"ok": False, "error": "Post failed — check X credentials in Railway env vars"}


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
    """Reset all cooldowns and immediately post every content type directly."""
    pub = _publisher()
    if err := _check(pub): return err

    results = {}

    # Reset all cooldowns first
    for k in list(pub._last.keys()):
        pub._last[k] = 0

    try:
        from app.agents.live_market_stream import LIVE_PRICES
        price = LIVE_PRICES.get("BTC/USDT", {}).get("last", 0.0)
    except Exception:
        price = 0.0

    # Post hourly/update first
    hourly_text = _gen_hourly_text(pub)
    ok = await pub._send_tweet(hourly_text, "hourly")
    results["hourly"] = "posted ✓" if ok else "failed"
    if ok:
        pub._touch("hourly")
    await __import__("asyncio").sleep(3)

    # Hot take
    from app.agents.x_publisher import HOT_TAKES, PHILOSOPHY_POSTS, ENGAGEMENT_QUESTIONS
    ok = await pub._send_tweet(pub.memory.pick("hot_take", HOT_TAKES), "hot_take")
    results["hot_take"] = "posted ✓" if ok else "failed"
    if ok:
        pub._touch("hot_take")
    await __import__("asyncio").sleep(3)

    # Philosophy
    ok = await pub._send_tweet(pub.memory.pick("philosophy", PHILOSOPHY_POSTS), "philosophy")
    results["philosophy"] = "posted ✓" if ok else "failed"
    if ok:
        pub._touch("philosophy")
    await __import__("asyncio").sleep(3)

    # News
    ok_news = await pub.post_news()
    results["news"] = "posted ✓" if ok_news else "failed"
    await __import__("asyncio").sleep(3)

    # Fear & Greed
    ok_fg = await pub.post_fear_greed()
    results["fear_greed"] = "posted ✓" if ok_fg else "failed"

    n_ok = sum(1 for v in results.values() if "✓" in str(v))
    return {"ok": True, "results": results, "posted": n_ok}


# ── Local poster queue (bypasses datacenter IP block) ─────────────────────────
# The local_poster.py script on the user's PC polls /next-post and posts via
# Playwright with their real residential IP + Edge session.

@router.get("/next-post")
async def next_post():
    """Return the next tweet for the local poster to send.
    Checks manual queue first, then auto-schedule by cooldown."""
    from app.agents import x_publisher as xp
    from app.agents.x_publisher import (
        HOT_TAKES, PHILOSOPHY_POSTS, ENGAGEMENT_QUESTIONS,
    )

    pub = _publisher()
    now = time.time()

    # 1. Manual queue (from dashboard "Post Now" buttons) — highest priority
    if _tweet_queue:
        item = _tweet_queue.pop(0)
        return {"has_post": True, **item}

    # 2. Auto-schedule based on cooldowns
    def _ok(key, cooldown):
        return pub._cooldown_ok(key, cooldown) if pub else True

    post_type = None
    text = ""

    if _ok("hourly", xp.HOURLY_COOLDOWN):
        text = _gen_hourly_text(pub)
        post_type = "hourly"

    elif _ok("hot_take", xp.HOT_TAKE_COOLDOWN):
        text = pub.memory.pick("hot_take", HOT_TAKES) if pub else random.choice(HOT_TAKES)
        post_type = "hot_take"

    elif _ok("philosophy", xp.PHILOSOPHY_COOLDOWN):
        text = pub.memory.pick("philosophy", PHILOSOPHY_POSTS) if pub else random.choice(PHILOSOPHY_POSTS)
        post_type = "philosophy"

    elif _ok("engagement", xp.ENGAGEMENT_COOLDOWN):
        text = pub.memory.pick("engagement", ENGAGEMENT_QUESTIONS) if pub else random.choice(ENGAGEMENT_QUESTIONS)
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
