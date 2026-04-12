"""
X Agent API -- dashboard control for @Tradeous X posting.

Read endpoints (status, next-post, confirm-post) are open so the local
poster script can operate without a token. All write/trigger endpoints
require a valid JWT.
"""
from __future__ import annotations

import random
import time
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.security import get_current_user

router = APIRouter(prefix="/api/x-agent", tags=["x-agent"])

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


# -- Status --------------------------------------------------------------------

@router.get("/status")
async def get_status():
    pub = _publisher()
    if not pub:
        return {"enabled": False, "recent_posts": [], "error": "Agent not running"}
    return pub.status()


# -- Manual triggers -----------------------------------------------------------

async def _send_now(pub, post_type: str, text: str) -> dict:
    text = text[:280]
    ok = await pub._send_tweet(text, post_type)
    if ok:
        pub._touch(post_type.replace("-", "_"))
        return {"ok": True, "queued": False, "posted": True, "text": text, "message": "Posted"}
    import uuid
    qid = str(uuid.uuid4())[:8] + f"_{post_type}"
    _tweet_queue.append({"id": qid, "type": post_type, "text": text, "ts": time.time()})
    return {"ok": True, "queued": True, "posted": False, "text": text,
            "id": qid, "message": "Ready -- sending via local poster"}


def _check(pub) -> dict | None:
    if not pub:
        return {"ok": False, "error": "Agent not running"}
    if not pub.enabled:
        return {"ok": False, "error": "X_AUTH_TOKEN / X_CT0 not set in Railway env vars"}
    return None


@router.post("/trigger/contrarian")
async def trigger_contrarian(_: dict = Depends(get_current_user)):
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["contrarian"] = 0
    ctx = pub._live_context
    price = pub._fmt_price(ctx.get("price", 0)) if ctx.get("price") else "unknown"
    regime = ctx.get("regime", "unknown").replace("_", " ")
    text = (
        f"BTC at {price}. Regime: {regime}.\n\n"
        f"Humans are euphoric. Algo remains disciplined.\n\n"
        f"Volume declining. Funding elevated. No structure break.\n"
        f"Staying flat until the edge appears."
    )
    return await _send_now(pub, "contrarian", text)


@router.post("/trigger/psychology")
async def trigger_psychology(_: dict = Depends(get_current_user)):
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["psychology_thread"] = 0
    text = (
        "The algo ignores news. Here's the pattern that repeated 7/8 times this cycle.\n\n"
        "Humans react to headlines. The model reacts to price structure.\n\n"
        "Thread below."
    )
    return await _send_now(pub, "psychology_thread", text)


@router.post("/trigger/poll")
async def trigger_poll(_: dict = Depends(get_current_user)):
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["poll"] = 0
    ctx = pub._live_context
    price = pub._fmt_price(ctx.get("price", 0)) if ctx.get("price") else "unknown"
    regime = ctx.get("regime", "unknown").replace("_", " ")
    text = (
        f"BTC at {price}. Regime: {regime}.\n\n"
        f"What would you do here?\n\n"
        f"A) Long -- breakout setup\n"
        f"B) Short -- distribution pattern\n"
        f"C) Flat -- no edge\n"
        f"D) Already positioned\n\n"
        f"Reply below. Algo's decision in 1 hour."
    )
    return await _send_now(pub, "poll", text)


@router.post("/trigger/breakdown")
async def trigger_breakdown(_: dict = Depends(get_current_user)):
    pub = _publisher()
    if err := _check(pub): return err
    pub._last["trade_breakdown"] = 0
    ctx = pub._live_context
    regime = ctx.get("regime", "unknown").replace("_", " ")
    text = (
        f"How the algo evaluates BTC setups right now:\n\n"
        f"Regime: {regime}\n"
        f"Checks: EMA confluence, VWAP distance, volume profile, OBI\n"
        f"Conviction threshold: 70%+\n"
        f"Risk/reward minimum: 1:2\n\n"
        f"No entry unless all conditions align."
    )
    return await _send_now(pub, "trade_breakdown", text)


# -- Test post (debug) --------------------------------------------------------

@router.post("/test-post")
async def test_post(_: dict = Depends(get_current_user)):
    pub = _publisher()
    if err := _check(pub): return err
    text = f"Test post from Tradeous algo -- {int(time.time())}"
    ok = await pub._send_tweet(text, "test")
    return {
        "ok": ok,
        "method_tried": "v1.1 + GraphQL",
        "last_error": pub._last_error if not ok else None,
        "curl_cffi_available": (lambda: __import__("app.agents.x_publisher", fromlist=["_CURL_AVAILABLE"])._CURL_AVAILABLE)(),
    }


# -- Manual compose ------------------------------------------------------------

class ManualPostRequest(BaseModel):
    text: str


@router.post("/post")
async def manual_post(req: ManualPostRequest, _: dict = Depends(get_current_user)):
    pub = _publisher()
    if err := _check(pub): return err
    if not req.text or len(req.text.strip()) < 3:
        return {"ok": False, "error": "Text too short"}
    ok = await pub._send_tweet(req.text.strip(), "manual")
    if ok:
        return {"ok": True, "message": "Posted"}
    return {"ok": False, "error": "Post failed -- check X credentials in Railway env vars"}


# -- Reset cooldowns -----------------------------------------------------------

@router.post("/reset-cooldowns")
async def reset_cooldowns(_: dict = Depends(get_current_user)):
    pub = _publisher()
    if not pub:
        return {"ok": False}
    for k in list(pub._last.keys()):
        pub._last[k] = 0
    return {"ok": True, "message": "All cooldowns reset -- next tick will evaluate"}


# -- Fire all now --------------------------------------------------------------

@router.post("/fire-all")
async def fire_all(_: dict = Depends(get_current_user)):
    """Reset all cooldowns and post available content types."""
    pub = _publisher()
    if err := _check(pub): return err

    results = {}

    for k in list(pub._last.keys()):
        pub._last[k] = 0

    ctx = pub._live_context
    price = pub._fmt_price(ctx.get("price", 0)) if ctx.get("price") else "unknown"
    regime = ctx.get("regime", "unknown").replace("_", " ")

    # Contrarian take
    text = (
        f"BTC at {price}. Regime: {regime}.\n\n"
        f"Humans are euphoric. Algo remains disciplined.\n\n"
        f"Volume declining. No structure break. Staying flat."
    )
    ok = await pub._send_tweet(text, "contrarian")
    results["contrarian"] = "posted" if ok else "failed"
    if ok:
        pub._touch("contrarian")
    await __import__("asyncio").sleep(3)

    # Poll
    text = (
        f"BTC at {price}. Regime: {regime}.\n\n"
        f"What would you do here?\n\n"
        f"A) Long\nB) Short\nC) Flat\nD) Already positioned\n\n"
        f"Reply below."
    )
    ok = await pub._send_tweet(text, "poll")
    results["poll"] = "posted" if ok else "failed"
    if ok:
        pub._touch("poll")

    n_ok = sum(1 for v in results.values() if v == "posted")
    return {"ok": True, "results": results, "posted": n_ok}


# -- Local poster queue -------------------------------------------------------

_DEFAULT_POSTER_SECRET = "tradeos-local-2024"

def _check_poster_secret(secret: str) -> None:
    import os
    from fastapi import HTTPException
    expected = os.environ.get("POSTER_SECRET", _DEFAULT_POSTER_SECRET)
    if not secret or secret != expected:
        raise HTTPException(status_code=403, detail="Invalid poster secret")


@router.get("/creds")
async def get_creds(secret: str = ""):
    _check_poster_secret(secret)
    import os
    auth_token = os.environ.get("X_AUTH_TOKEN", "").strip()
    ct0        = os.environ.get("X_CT0", "").strip()
    if not auth_token or not ct0:
        return {
            "ok": False,
            "error": "X_AUTH_TOKEN and/or X_CT0 not set in Railway environment variables.",
        }
    return {"ok": True, "a": auth_token, "c": ct0}


@router.get("/next-post")
async def next_post(secret: str = ""):
    """Return the next tweet for the local poster to send."""
    _check_poster_secret(secret)
    from app.agents import x_publisher as xp

    pub = _publisher()
    now = time.time()

    # 1. Manual queue -- highest priority
    if _tweet_queue:
        item = _tweet_queue.pop(0)
        return {"has_post": True, **item}

    # 2. Auto-schedule based on cooldowns (only high-value content)
    def _ok(key, cooldown):
        return pub._cooldown_ok(key, cooldown) if pub else True

    post_type = None
    text = ""

    ctx = pub._live_context if pub else {}
    price = f"${ctx.get('price', 0):,.0f}" if ctx.get("price") else "unknown"
    regime = ctx.get("regime", "unknown").replace("_", " ")

    if _ok("contrarian", xp.CONTRARIAN_COOLDOWN):
        text = (
            f"BTC at {price}. Regime: {regime}.\n\n"
            f"Humans are euphoric. Algo remains disciplined.\n\n"
            f"No edge. Staying flat."
        )
        post_type = "contrarian"

    elif _ok("poll", xp.POLL_COOLDOWN):
        text = (
            f"BTC at {price}. Regime: {regime}.\n\n"
            f"What would you do here?\n\n"
            f"A) Long\nB) Short\nC) Flat\nD) Already positioned\n\n"
            f"Reply below."
        )
        post_type = "poll"

    if not post_type or not text:
        return {"has_post": False}

    qid = f"{post_type}_{int(now)}"
    return {"has_post": True, "id": qid, "type": post_type, "text": text[:280]}


class ConfirmRequest(BaseModel):
    id: str
    post_type: str
    tweet_id: str = ""


@router.post("/confirm-post")
async def confirm_post(req: ConfirmRequest, secret: str = ""):
    """Called by local poster after successfully posting -- updates cooldowns."""
    _check_poster_secret(secret)
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
