"""
X Agent API -- dashboard control for @Tradeous X posting.

Read endpoints (status, next-post, confirm-post) are open so the local
poster script can operate without a token. All write/trigger endpoints
require a valid JWT.
"""
from __future__ import annotations

import logging
import os
import random
import time
from fastapi import APIRouter, Depends

logger = logging.getLogger(__name__)
from pydantic import BaseModel
from app.core.security import get_current_user

router = APIRouter(prefix="/api/x-agent", tags=["x-agent"])

_tweet_queue: list[dict] = []


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
    # queue_on_fail=False: we handle queuing here to avoid double-queuing
    ok = await pub._send_tweet(text, post_type, queue_on_fail=False)
    if ok:
        pub._touch(post_type.replace("-", "_"))
        return {"ok": True, "queued": False, "posted": True, "text": text, "message": "Posted"}
    import uuid
    qid = str(uuid.uuid4())[:8] + f"_{post_type}"
    _tweet_queue.append({"id": qid, "type": post_type, "text": text, "ts": time.time()})
    return {"ok": True, "queued": True, "posted": False, "text": text, "type": post_type,
            "id": qid, "message": "Queued -- start local_poster.py on your PC to send"}


def _check(pub) -> dict | None:
    if not pub:
        return {"ok": False, "error": "Agent not running"}
    if not pub.enabled:
        return {"ok": False, "error": "No X credentials. Add X_API_KEY/X_API_SECRET/X_ACCESS_TOKEN/X_ACCESS_SECRET in Railway (recommended), or X_AUTH_TOKEN+X_CT0."}
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


# -- Approval queue -----------------------------------------------------------

@router.get("/pending")
async def get_pending(_: dict = Depends(get_current_user)):
    """Return all tweets pending approval."""
    pub = _publisher()
    return {"ok": True, "pending": pub.get_pending_approvals()}


@router.post("/approve/{pid}")
async def approve_tweet(pid: str, _: dict = Depends(get_current_user)):
    """Immediately post a pending tweet."""
    pub = _publisher()
    ok = await pub.approve_pending(pid)
    return {"ok": ok, "msg": "Posted ✓" if ok else "Not found or already posted"}


@router.post("/reject/{pid}")
async def reject_tweet(pid: str, _: dict = Depends(get_current_user)):
    """Discard a pending tweet."""
    pub = _publisher()
    ok = pub.reject_pending(pid)
    return {"ok": ok, "msg": "Discarded" if ok else "Not found"}


# -- Grok viral trigger -------------------------------------------------------

@router.post("/trigger/grok-viral")
async def trigger_grok_viral(_: dict = Depends(get_current_user)):
    """Force Grok to search X right now, decide what's viral, and post it."""
    pub = _publisher()
    if err := _check(pub): return err
    if not pub.grok or not getattr(pub.grok, "enabled", False):
        return {"ok": False, "error": "Grok is not enabled — set XAI_API_KEY in Railway"}

    # Reset cooldown so it fires even if recent
    pub._last["grok_viral"] = 0

    ctx = pub._live_context
    recent = pub._recent_texts_for_ai(15)

    try:
        price  = ctx.get("price", 0)
        regime = ctx.get("regime", "")

        # Try the full viral suggestion first
        suggestion = await pub.grok.suggest_and_generate_post(
            recent_posts=recent,
            btc_price=price,
            regime=regime,
            mood_tone=pub.mood.tone,
        )

        # Fallback: simpler direct viral post if suggestion parsing failed
        if not suggestion or not suggestion.get("tweet"):
            tweet = await pub.grok.generate_viral_post(
                btc_price=price,
                regime=regime,
                recent_posts=recent,
                post_type="viral_reaction",
            )
            if tweet:
                suggestion = {"tweet": tweet, "post_type": "grok_viral", "angle": ""}
            else:
                # Last fallback: generate_viral_commentary
                tweet = await pub.grok.generate_viral_commentary(
                    btc_price=price,
                    mood_tone=pub.mood.tone,
                    recent_posts=recent,
                )
                if tweet:
                    suggestion = {"tweet": tweet, "post_type": "viral_commentary", "angle": ""}

        if not suggestion or not suggestion.get("tweet"):
            return {"ok": False, "error": "Grok is not returning content right now — API may be slow, try again in 30s"}

        tweet     = suggestion["tweet"][:280]
        post_type = suggestion.get("post_type", "grok_viral")
        angle     = suggestion.get("angle", "")

        # For manual trigger, skip the approval queue and post directly
        ok = await pub._send_tweet(tweet, post_type)
        return {
            "ok": ok,
            "post_type": post_type,
            "angle": angle,
            "tweet": tweet,
            "queued_for_local_poster": not ok,
        }
    except Exception as e:
        import traceback
        logger.error(f"[XAgent] trigger/grok-viral error: {traceback.format_exc()}")
        return {"ok": False, "error": f"Error: {str(e)}"}


# -- Test post (debug) --------------------------------------------------------

@router.post("/test-post")
async def test_post(_: dict = Depends(get_current_user)):
    pub = _publisher()
    if err := _check(pub): return err
    text = f"Tradeous algo online -- {int(time.time())} -- ignore this test"
    ok = await pub._send_tweet(text, "test", queue_on_fail=False)
    from app.agents import x_publisher as xp
    return {
        "ok": ok,
        "posted": ok,
        "queued": False,
        "last_error": pub._last_error if not ok else None,
        "note": "v1.1 statuses/update is dead (X killed it 2023). Only GraphQL works with cookies.",
        "curl_cffi_available": xp._CURL_AVAILABLE,
        "cookies_set": bool(pub._auth_token and pub._ct0),
        "auth_token_prefix": pub._auth_token[:8] + "..." if pub._auth_token else "MISSING",
        "ct0_prefix": pub._ct0[:8] + "..." if pub._ct0 else "MISSING",
        "fix": "Run local_poster.py on your PC — Railway's datacenter IP is blocked by X, but your residential IP works.",
    }


# -- Diagnose (no test post, just checks) --------------------------------------

@router.get("/diagnose")
async def diagnose():
    """Returns detailed status without posting anything — use this to debug."""
    from app.agents import x_publisher as xp
    pub = _publisher()
    auth_token   = os.environ.get("X_AUTH_TOKEN", "").strip()
    ct0          = os.environ.get("X_CT0", "").strip()
    api_key      = os.environ.get("X_API_KEY", "").strip()
    api_secret   = os.environ.get("X_API_SECRET", "").strip()
    access_token = os.environ.get("X_ACCESS_TOKEN", "").strip()
    access_secret = (os.environ.get("X_ACCESS_SECRET", "") or os.environ.get("X_ACCESS_TOKEN_SECRET", "")).strip()
    official_api_configured = bool(api_key and api_secret and access_token and access_secret)
    posting_method = pub._posting_method if pub else "none"

    if official_api_configured:
        hint = "Official X API configured -- posting works from Railway with no PC needed."
    elif auth_token and ct0:
        hint = "Using cookie auth. Railway IP may be blocked by X (226 error). Switch to Official X API to fix this permanently -- see developer.twitter.com."
    else:
        hint = "No credentials. Add X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET in Railway (recommended), or X_AUTH_TOKEN + X_CT0."

    return {
        "agent_running":              pub is not None,
        "x_enabled":                  pub.enabled if pub and hasattr(pub, "enabled") else False,
        "posting_method":             posting_method,
        "official_api_configured":    official_api_configured,
        "tweepy_available":           xp._TWEEPY_AVAILABLE,
        "X_API_KEY":                  api_key[:8] + "..." if api_key else "MISSING",
        "X_API_SECRET":               api_secret[:8] + "..." if api_secret else "MISSING",
        "X_ACCESS_TOKEN":             access_token[:20] + "..." if access_token else "MISSING",
        "X_ACCESS_SECRET":            access_secret[:20] + "..." if access_secret else "MISSING",
        "ACCESS_SECRET_LEN":          len(access_secret),
        "cookies_in_env":             bool(auth_token and ct0),
        "auth_token_prefix":          auth_token[:10] + "..." if auth_token else "MISSING",
        "ct0_prefix":                 ct0[:10] + "..." if ct0 else "MISSING",
        "curl_cffi_available":        xp._CURL_AVAILABLE,
        "last_error":                 pub._last_error if pub else "agent not running",
        "queue_length":               len(_tweet_queue),
        "daily_posts":                pub._daily_posts if pub else 0,
        "daily_budget":               xp.MAX_DAILY_POSTS,
        "recent_posts_count":         len(pub._recent_posts) if pub else 0,
        "hint":                       hint,
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


# -- Quick test (no JWT needed, uses poster secret) ---------------------------

@router.get("/test-api")
async def test_api(secret: str = ""):
    """Test Official API credentials directly -- returns full error detail."""
    _check_poster_secret(secret)
    from app.agents import x_publisher as xp
    api_key      = os.environ.get("X_API_KEY", "").strip()
    api_secret   = os.environ.get("X_API_SECRET", "").strip()
    access_token = os.environ.get("X_ACCESS_TOKEN", "").strip()
    access_secret = (os.environ.get("X_ACCESS_SECRET", "") or os.environ.get("X_ACCESS_TOKEN_SECRET", "")).strip()

    if not (api_key and api_secret and access_token and access_secret):
        return {"ok": False, "error": "Missing API keys", "missing": [
            k for k, v in {"X_API_KEY": api_key, "X_API_SECRET": api_secret,
                           "X_ACCESS_TOKEN": access_token, "X_ACCESS_SECRET": access_secret}.items() if not v
        ]}

    if not xp._TWEEPY_AVAILABLE:
        return {"ok": False, "error": "tweepy not installed"}

    import tweepy as _tw
    client = _tw.Client(
        consumer_key=api_key, consumer_secret=api_secret,
        access_token=access_token, access_token_secret=access_secret,
    )

    # Step 1: verify identity
    try:
        me = client.get_me()
        username = me.data.username if me.data else "unknown"
    except Exception as e:
        return {"ok": False, "step": "get_me", "error": str(e),
                "hint": "API Key/Secret or Access Token/Secret are invalid or mismatched."}

    # Step 2: try posting
    import time as _time
    try:
        resp = client.create_tweet(text=f"Tradeos API test {int(_time.time())} — ignore")
        tweet_id = str(resp.data["id"])
        return {"ok": True, "username": username,
                "tweet_id": tweet_id,
                "url": f"https://x.com/{username}/status/{tweet_id}"}
    except Exception as e:
        detail = ""
        if hasattr(e, "api_messages"):
            detail = str(e.api_messages)
        elif hasattr(e, "response") and e.response is not None:
            try: detail = e.response.text[:300]
            except Exception: pass
        return {"ok": False, "step": "create_tweet", "authenticated_as": username,
                "error": str(e), "detail": detail,
                "hint": "Authenticated OK but tweet failed. Check app permissions (Read+Write) and regenerate Access Token after saving permissions."}


# -- Local poster queue -------------------------------------------------------

_POSTER_SECRET_DEFAULT = "tradeos-local-2024"

def _check_poster_secret(secret: str) -> None:
    from fastapi import HTTPException
    expected = os.environ.get("POSTER_SECRET", _POSTER_SECRET_DEFAULT).strip()
    if not secret or secret != expected:
        raise HTTPException(status_code=403, detail="Invalid poster secret")


@router.get("/creds")
async def get_creds(secret: str = ""):
    _check_poster_secret(secret)
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
    """Called by local poster after successfully posting -- updates cooldowns + budget."""
    _check_poster_secret(secret)
    pub = _publisher()
    if pub:
        key = req.post_type.replace("-", "_")
        pub._touch(key)
        pub._increment_daily()
        pub.memory.record_post(key, f"local_poster:{req.post_type}")
        pub._recent_posts.append({
            "id": req.tweet_id or req.id,
            "type": req.post_type,
            "text": f"Posted via local poster ({req.post_type})",
            "ts": time.time(),
            "url": f"https://x.com/tradeous/status/{req.tweet_id}" if req.tweet_id else "",
        })
    return {"ok": True}
