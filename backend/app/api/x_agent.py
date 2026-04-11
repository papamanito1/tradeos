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


@router.get("/diagnose")
async def diagnose():
    """Check curl_cffi, credentials, and X API reachability."""
    import os, httpx as _httpx
    token = os.environ.get("X_AUTH_TOKEN", "")
    ct0   = os.environ.get("X_CT0", "")

    result = {
        "X_AUTH_TOKEN_len": len(token),
        "X_CT0_len": len(ct0),
        "curl_cffi_available": False,
        "x_api_status": None,
        "x_api_error": None,
    }

    try:
        from curl_cffi.requests import AsyncSession
        result["curl_cffi_available"] = True
    except Exception as e:
        result["curl_cffi_import_error"] = str(e)

    # Try actual X API call
    BEARER = (
        "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
        "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
    )
    QUERY_ID = "S1qcGUn68_U0lDKdMlYSGg"
    url = f"https://x.com/i/api/graphql/{QUERY_ID}/CreateTweet"
    headers = {
        "authorization": f"Bearer {BEARER}",
        "x-csrf-token": ct0,
        "cookie": f"auth_token={token}; ct0={ct0}",
        "content-type": "application/json",
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en",
        "referer": "https://x.com/compose/post",
        "origin": "https://x.com",
    }
    payload = {
        "variables": {"tweet_text": "__diagnose__", "dark_request": False,
                      "media": {"media_entities": [], "possibly_sensitive": False},
                      "semantic_annotation_ids": []},
        "features": {"tweetypie_unmention_optimization_enabled": True,
                     "responsive_web_edit_tweet_api_enabled": True,
                     "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                     "view_counts_everywhere_api_enabled": True,
                     "longform_notetweets_consumption_enabled": True,
                     "responsive_web_twitter_article_tweet_consumption_enabled": False,
                     "tweet_awards_web_tipping_enabled": False,
                     "freedom_of_speech_not_reach_fetch_enabled": True,
                     "standardized_nudges_misinfo": True,
                     "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
                     "rweb_video_timestamps_enabled": True,
                     "longform_notetweets_rich_text_read_enabled": True,
                     "longform_notetweets_inline_media_enabled": True,
                     "responsive_web_graphql_exclude_directive_enabled": True,
                     "verified_phone_label_enabled": False,
                     "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                     "responsive_web_graphql_timeline_navigation_enabled": True,
                     "responsive_web_enhance_cards_enabled": False},
        "queryId": QUERY_ID,
    }

    try:
        if result["curl_cffi_available"]:
            from curl_cffi.requests import AsyncSession
            async with AsyncSession(impersonate="edge101") as s:
                r = await s.post(url, json=payload, headers=headers, timeout=15)
        else:
            async with _httpx.AsyncClient(timeout=15) as s:
                r = await s.post(url, json=payload, headers=headers)
        result["x_api_status"] = r.status_code
        result["x_api_body_preview"] = r.text[:150]
    except Exception as e:
        result["x_api_error"] = str(e)

    return result



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
