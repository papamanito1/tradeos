"""
Tradeous Local X Poster — posts tweets from YOUR residential IP using curl_cffi.
No Playwright, no browser needed. Just run: python local_poster.py

How it works:
  1) Fetches X auth cookies from Railway backend
  2) Polls Railway /next-post every 25 seconds for scheduled + queued tweets
  3) Posts directly to X using curl_cffi (TLS fingerprint impersonation)
  4) Runs an HTTP server on :4242 so the dashboard can send tweets immediately

Your local machine's residential IP won't be blocked by X (unlike Railway's datacenter IP).
"""
import asyncio
import json
import logging
import os
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

RAILWAY_URL = "https://tradeos-production-8f21.up.railway.app"
POLL_INTERVAL = 25  # seconds between queue polls
LOCAL_PORT = 4242

logging.basicConfig(
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("local_poster")

# ── X API constants ──────────────────────────────────────────────────────────

_X_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_X_QUERY_ID = "S1qcGUn68_U0lDKdMlYSGg"
_X_CREATE_TWEET_URL = f"https://x.com/i/api/graphql/{_X_QUERY_ID}/CreateTweet"

# ── State ────────────────────────────────────────────────────────────────────

_auth_token = ""
_ct0 = ""
_post_count = 0
_post_lock = asyncio.Lock()


async def fetch_creds():
    """Fetch X auth cookies from Railway."""
    global _auth_token, _ct0
    try:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession() as s:
            r = await s.get(f"{RAILWAY_URL}/api/x-agent/creds", timeout=15)
            data = r.json()
            if data.get("ok"):
                _auth_token = data["a"].strip()
                _ct0 = data["c"].strip()
                log.info(f"Got X cookies from Railway (auth_token: {_auth_token[:12]}...)")
                return True
            else:
                log.error("Railway returned ok=false for /creds — check X_AUTH_TOKEN / X_CT0 env vars")
                return False
    except Exception as e:
        log.error(f"Failed to fetch creds from Railway: {e}")
        return False


async def post_tweet(text: str) -> str:
    """Post a tweet using curl_cffi from local residential IP. Returns tweet_id or empty."""
    global _post_count
    if not _auth_token or not _ct0:
        log.error("No X cookies — can't post")
        return ""

    async with _post_lock:
        # Try v1.1 API first, then GraphQL
        result = await _post_v1(text)
        if result:
            _post_count += 1
            return result

        result = await _post_graphql(text)
        if result:
            _post_count += 1
            return result

        return ""


async def _post_v1(text: str) -> str:
    """Post via Twitter v1.1 client API."""
    from curl_cffi.requests import AsyncSession

    url = "https://api.x.com/1.1/statuses/update.json"
    headers = {
        "authorization": f"Bearer {_X_BEARER}",
        "x-csrf-token": _ct0,
        "cookie": f"auth_token={_auth_token}; ct0={_ct0}",
        "content-type": "application/x-www-form-urlencoded",
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en",
        "origin": "https://x.com",
        "referer": "https://x.com",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    }
    body = urllib.parse.urlencode({"status": text[:280]})
    try:
        async with AsyncSession(impersonate="edge101") as session:
            resp = await session.post(url, data=body, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            tweet_id = str(data.get("id_str", ""))
            log.info(f"✅ v1.1 posted: {text[:50]}… (id={tweet_id})")
            return tweet_id or "posted"
        log.warning(f"v1.1 HTTP {resp.status_code}: {resp.text[:120]}")
        return ""
    except Exception as e:
        log.warning(f"v1.1 error: {e}")
        return ""


async def _post_graphql(text: str) -> str:
    """Post via X GraphQL CreateTweet endpoint."""
    from curl_cffi.requests import AsyncSession

    headers = {
        "authorization": f"Bearer {_X_BEARER}",
        "x-csrf-token": _ct0,
        "cookie": f"auth_token={_auth_token}; ct0={_ct0}",
        "content-type": "application/json",
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en",
        "referer": "https://x.com/compose/post",
        "origin": "https://x.com",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    }
    payload = {
        "variables": {
            "tweet_text": text[:280],
            "dark_request": False,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
        },
        "features": {
            "tweetypie_unmention_optimization_enabled": True,
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
            "responsive_web_enhance_cards_enabled": False,
        },
        "queryId": _X_QUERY_ID,
    }
    try:
        async with AsyncSession(impersonate="edge101") as session:
            resp = await session.post(_X_CREATE_TWEET_URL, json=payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            tweet_id = (
                data.get("data", {})
                    .get("create_tweet", {})
                    .get("tweet_results", {})
                    .get("result", {})
                    .get("rest_id", "")
            )
            log.info(f"✅ GraphQL posted: {text[:50]}… (id={tweet_id})")
            return tweet_id or "posted"
        log.warning(f"GraphQL HTTP {resp.status_code}: {resp.text[:120]}")
        return ""
    except Exception as e:
        log.warning(f"GraphQL error: {e}")
        return ""


async def confirm_to_railway(post_id: str, post_type: str, tweet_id: str):
    """Tell Railway the post was successful so it updates cooldowns + recent_posts."""
    try:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession() as s:
            await s.post(
                f"{RAILWAY_URL}/api/x-agent/confirm-post",
                json={"id": post_id, "post_type": post_type, "tweet_id": tweet_id},
                timeout=10,
            )
    except Exception:
        pass


# ── HTTP server for direct posts from dashboard ──────────────────────────────

_incoming_queue: list[str] = []


class PostHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/post":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode() if length else ""
            try:
                data = json.loads(body)
                text = data.get("text", "").strip()
            except Exception:
                text = body.strip()
            if text:
                _incoming_queue.append(text)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "queued": True}).encode())
            else:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "posts": _post_count}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, *args):
        pass


def start_http_server():
    server = HTTPServer(("0.0.0.0", LOCAL_PORT), PostHandler)
    server.serve_forever()


# ── Main loop ────────────────────────────────────────────────────────────────

async def poll_railway():
    """Fetch the next scheduled/queued post from Railway."""
    try:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession() as s:
            r = await s.get(f"{RAILWAY_URL}/api/x-agent/next-post", timeout=15)
            data = r.json()
            if data.get("has_post"):
                return data
    except Exception as e:
        log.warning(f"Railway poll error: {e}")
    return None


async def run():
    log.info("=" * 52)
    log.info("  Tradeous Local X Poster — curl_cffi mode")
    log.info(f"  Local API  →  http://localhost:{LOCAL_PORT}/post")
    log.info(f"  Railway    →  {RAILWAY_URL}")
    log.info("=" * 52)

    # Start HTTP server in background thread
    t = threading.Thread(target=start_http_server, daemon=True)
    t.start()
    log.info(f"Local API listening on http://localhost:{LOCAL_PORT}")

    # Fetch X auth cookies from Railway
    ok = await fetch_creds()
    if not ok:
        log.error("Cannot get X cookies. Make sure X_AUTH_TOKEN and X_CT0 are set in Railway env vars.")
        log.error("Continuing in receive-only mode (will queue posts but cannot send).")

    # Quick test post to verify cookies work
    if _auth_token and _ct0:
        log.info("Testing X connection from your local IP...")
        test_id = await post_tweet(f"Tradeous is online. 🤖📈 · {int(time.time())}")
        if test_id:
            log.info(f"✅ Test post successful! Local posting works. (id={test_id})")
            await confirm_to_railway("test_init", "hourly", test_id)
        else:
            log.warning("⚠ Test post failed — cookies may be expired.")
            log.warning("Run 'python grab_cookies_and_tweet.py' to get fresh cookies, then update Railway env vars.")

    log.info("Entering main loop — polling every %d seconds...", POLL_INTERVAL)

    while True:
        try:
            # 1. Process any direct posts from dashboard (via :4242/post)
            while _incoming_queue:
                text = _incoming_queue.pop(0)
                log.info(f"Direct post from dashboard: {text[:50]}…")
                tweet_id = await post_tweet(text)
                if tweet_id:
                    await confirm_to_railway("dashboard", "manual", tweet_id)
                else:
                    log.warning("Direct post failed")

            # 2. Poll Railway for scheduled/queued posts
            item = await poll_railway()
            if item:
                text = item.get("text", "")
                post_type = item.get("type", "auto")
                post_id = item.get("id", "")
                log.info(f"Auto [{post_type}]: {text[:60]}…")
                tweet_id = await post_tweet(text)
                if tweet_id:
                    await confirm_to_railway(post_id, post_type, tweet_id)
                else:
                    log.warning(f"Auto post [{post_type}] failed")

        except Exception as e:
            log.error(f"Loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run())
