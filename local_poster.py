"""
Tradeous Local X Poster — posts tweets from YOUR residential IP using curl_cffi.
No Playwright, no browser needed.

HOW TO USE:
  1. Run this script:  python local_poster.py
  2. Leave it running — it posts every 25 seconds automatically.
  3. Dashboard "Post Now" buttons will also route through here instantly.

HOW IT WORKS:
  1. Fetches X auth cookies from Railway (X_AUTH_TOKEN / X_CT0 env vars)
  2. Polls Railway /api/x-agent/next-post for scheduled tweets
  3. Posts directly to X using curl_cffi (TLS fingerprint impersonation)
  4. Runs an HTTP server on :4242 for instant dashboard posts

IF COOKIES ARE EXPIRED:
  Run: python grab_cookies_and_tweet.py
  Then update X_AUTH_TOKEN and X_CT0 in Railway → Variables tab.
"""
import asyncio
import json
import logging
import os
import sys
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Force UTF-8 output so special chars don't crash on Windows cp1252 console
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Configuration ─────────────────────────────────────────────────────────────

RAILWAY_URL   = os.environ.get("RAILWAY_URL", "https://tradeos-production-8f21.up.railway.app").rstrip("/")
POSTER_SECRET = os.environ.get("POSTER_SECRET", "tradeos-local-2024")
POLL_INTERVAL = 25      # seconds between queue polls
LOCAL_PORT    = 4242
CREDS_REFRESH_INTERVAL = 3600   # re-fetch cookies from Railway every hour

logging.basicConfig(
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("local_poster")

# ── X API constants ──────────────────────────────────────────────────────────

_X_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_X_QUERY_ID          = "S1qcGUn68_U0lDKdMlYSGg"
_X_CREATE_TWEET_URL  = f"https://x.com/i/api/graphql/{_X_QUERY_ID}/CreateTweet"

# ── Runtime state ─────────────────────────────────────────────────────────────

_auth_token      = ""
_ct0             = ""
_post_count      = 0
_last_creds_fetch = 0.0
_post_lock       = asyncio.Lock()
_incoming_queue: list[str] = []


# ── Dependency check ──────────────────────────────────────────────────────────

def _check_deps():
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        log.error("curl_cffi not installed. Run:  pip install curl_cffi")
        sys.exit(1)

    try:
        import requests as _req  # noqa: F401
    except ImportError:
        pass   # optional


# ── Cookie management ─────────────────────────────────────────────────────────

async def fetch_creds(force: bool = False) -> bool:
    """Fetch X auth cookies from Railway env vars via the /creds endpoint."""
    global _auth_token, _ct0, _last_creds_fetch

    now = time.time()
    if not force and _auth_token and now - _last_creds_fetch < CREDS_REFRESH_INTERVAL:
        return True   # still fresh

    from curl_cffi.requests import AsyncSession
    url = f"{RAILWAY_URL}/api/x-agent/creds?secret={POSTER_SECRET}"
    try:
        async with AsyncSession() as s:
            r = await s.get(url, timeout=15)
            data = r.json()
    except Exception as e:
        log.error(f"Cannot reach Railway at {RAILWAY_URL}: {e}")
        log.error("Make sure the Railway URL is correct and the backend is running.")
        return False

    if not data.get("ok"):
        err = data.get("error", "Unknown error")
        log.error(f"Railway /creds error: {err}")
        if "X_AUTH_TOKEN" in err:
            log.error("*** X_AUTH_TOKEN and X_CT0 are not set in Railway! ***")
            log.error("  1. Run: python grab_cookies_and_tweet.py")
            log.error("     (logs into x.com, saves cookies)")
            log.error("  2. Open: C:/tmp/x_cookie_values.txt")
            log.error("  3. Go to Railway > your backend > Variables")
            log.error("  4. Add X_AUTH_TOKEN and X_CT0 from that file")
            log.error("  5. Redeploy + restart local_poster.py")
        return False

    new_token = data.get("a", "").strip()
    new_ct0   = data.get("c", "").strip()
    if not new_token or not new_ct0:
        log.error("Railway returned empty credentials — check X_AUTH_TOKEN / X_CT0 env vars")
        return False

    _auth_token       = new_token
    _ct0              = new_ct0
    _last_creds_fetch = now
    log.info(f"X cookies loaded from Railway (token: {_auth_token[:14]}...)")
    return True


# ── Tweet posting ─────────────────────────────────────────────────────────────

async def post_tweet(text: str) -> str:
    """Post a tweet using curl_cffi from local residential IP. Returns tweet_id or empty."""
    global _post_count
    if not _auth_token or not _ct0:
        log.error("No X cookies — cannot post. Run fetch_creds() first.")
        return ""

    async with _post_lock:
        # v1.1 statuses/update is dead since 2023 — go straight to GraphQL
        result = await _post_graphql(text)
        if result:
            _post_count += 1
            return result

        log.warning("GraphQL failed — cookies may be expired.")
        log.warning("Run 'python grab_cookies_and_tweet.py' to get fresh cookies,")
        log.warning("then update X_AUTH_TOKEN and X_CT0 in Railway Variables.")
        return ""


def _x_headers(content_type: str = "application/x-www-form-urlencoded") -> dict:
    return {
        "authorization":             f"Bearer {_X_BEARER}",
        "x-csrf-token":              _ct0,
        "cookie":                    f"auth_token={_auth_token}; ct0={_ct0}",
        "content-type":              content_type,
        "x-twitter-active-user":     "yes",
        "x-twitter-auth-type":       "OAuth2Session",
        "x-twitter-client-language": "en",
        "origin":                    "https://x.com",
        "referer":                   "https://x.com",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
        ),
    }


async def _post_v1(text: str) -> str:
    from curl_cffi.requests import AsyncSession
    url  = "https://api.x.com/1.1/statuses/update.json"
    body = urllib.parse.urlencode({"status": text[:280]})
    try:
        async with AsyncSession(impersonate="edge101") as s:
            r = await s.post(url, data=body, headers=_x_headers(), timeout=20)
        if r.status_code == 200:
            data      = r.json()
            tweet_id  = str(data.get("id_str", ""))
            log.info(f"[OK] v1.1 posted -- {text[:60]}{'...' if len(text)>60 else ''}")
            return tweet_id or "posted"
        elif r.status_code == 403:
            log.warning("v1.1 HTTP 403 — cookies expired or account suspended")
        else:
            log.warning(f"v1.1 HTTP {r.status_code}: {r.text[:120]}")
        return ""
    except Exception as e:
        log.warning(f"v1.1 error: {e}")
        return ""


async def _post_graphql(text: str) -> str:
    from curl_cffi.requests import AsyncSession
    payload = {
        "variables": {
            "tweet_text":              text[:280],
            "dark_request":            False,
            "media":                   {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
        },
        "features": {
            "tweetypie_unmention_optimization_enabled":                         True,
            "responsive_web_edit_tweet_api_enabled":                            True,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled":       True,
            "view_counts_everywhere_api_enabled":                               True,
            "longform_notetweets_consumption_enabled":                          True,
            "responsive_web_twitter_article_tweet_consumption_enabled":         False,
            "tweet_awards_web_tipping_enabled":                                 False,
            "freedom_of_speech_not_reach_fetch_enabled":                        True,
            "standardized_nudges_misinfo":                                      True,
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
            "rweb_video_timestamps_enabled":                                    True,
            "longform_notetweets_rich_text_read_enabled":                       True,
            "longform_notetweets_inline_media_enabled":                         True,
            "responsive_web_graphql_exclude_directive_enabled":                 True,
            "verified_phone_label_enabled":                                     False,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled":               True,
            "responsive_web_enhance_cards_enabled":                             False,
        },
        "queryId": _X_QUERY_ID,
    }
    try:
        async with AsyncSession(impersonate="edge101") as s:
            r = await s.post(
                _X_CREATE_TWEET_URL,
                json=payload,
                headers=_x_headers("application/json"),
                timeout=20,
            )
        if r.status_code == 200:
            data     = r.json()
            errors   = data.get("errors", [])
            if errors:
                log.warning(f"GraphQL errors: {errors[:2]}")
                return ""
            tweet_id = (
                data.get("data", {})
                    .get("create_tweet", {})
                    .get("tweet_results", {})
                    .get("result", {})
                    .get("rest_id", "")
            )
            log.info(f"[OK] GraphQL posted -- {text[:60]}{'...' if len(text)>60 else ''}")
            return tweet_id or "posted"
        elif r.status_code == 403:
            log.warning("GraphQL HTTP 403 — cookies expired or CSRF token mismatch")
        else:
            log.warning(f"GraphQL HTTP {r.status_code}: {r.text[:120]}")
        return ""
    except Exception as e:
        log.warning(f"GraphQL error: {e}")
        return ""


# ── Railway communication ─────────────────────────────────────────────────────

async def poll_railway() -> dict | None:
    """Fetch the next scheduled/queued post from Railway."""
    from curl_cffi.requests import AsyncSession
    url = f"{RAILWAY_URL}/api/x-agent/next-post?secret={POSTER_SECRET}"
    try:
        async with AsyncSession() as s:
            r = await s.get(url, timeout=15)
            if r.status_code == 403:
                log.warning("Railway /next-post returned 403 — check POSTER_SECRET matches Railway env var")
                return None
            data = r.json()
            if data.get("has_post"):
                return data
    except Exception as e:
        log.warning(f"Railway poll error: {e}")
    return None


async def confirm_to_railway(post_id: str, post_type: str, tweet_id: str):
    """Tell Railway the post was successful so it updates cooldowns."""
    from curl_cffi.requests import AsyncSession
    url = f"{RAILWAY_URL}/api/x-agent/confirm-post?secret={POSTER_SECRET}"
    try:
        async with AsyncSession() as s:
            await s.post(
                url,
                json={"id": post_id, "post_type": post_type, "tweet_id": tweet_id},
                timeout=10,
            )
    except Exception:
        pass


# ── Local HTTP server (for dashboard "Post Now" buttons) ─────────────────────

class PostHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/post":
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length).decode() if length else ""
            try:
                text = json.loads(body).get("text", "").strip()
            except Exception:
                text = body.strip()
            if text:
                _incoming_queue.append(text)
                self._respond(200, {"ok": True, "queued": True})
            else:
                self._respond(400, {"ok": False, "error": "No text"})
        else:
            self._respond(404, {"ok": False})

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {
                "ok":           True,
                "posts_sent":   _post_count,
                "cookies_ok":   bool(_auth_token and _ct0),
                "railway":      RAILWAY_URL,
            })
        else:
            self._respond(404, {"ok": False})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass   # suppress default request logs


def _start_http_server():
    server = HTTPServer(("0.0.0.0", LOCAL_PORT), PostHandler)
    server.serve_forever()


# ── Main loop ────────────────────────────────────────────────────────────────

async def run():
    _check_deps()

    print()
    print("=" * 58)
    print("  Tradeous Local X Poster")
    print(f"  Railway  : {RAILWAY_URL}")
    print(f"  Local API: http://localhost:{LOCAL_PORT}/post")
    print(f"  Secret   : {'*' * len(POSTER_SECRET)}")
    print("=" * 58)
    print()

    # Start local HTTP server in background thread
    t = threading.Thread(target=_start_http_server, daemon=True)
    t.start()
    log.info(f"Local API listening on http://localhost:{LOCAL_PORT}")

    # Fetch X cookies from Railway
    ok = await fetch_creds(force=True)
    if not ok:
        log.warning("Starting in degraded mode — will retry cookies on next cycle.")
    else:
        # Quick connectivity test (no actual post)
        log.info("Cookies loaded. Ready to post from residential IP [OK]")
        log.info(f"Polling every {POLL_INTERVAL}s. Press Ctrl+C to stop.")

    print()
    log.info("Entering main loop...")
    consecutive_failures = 0

    while True:
        try:
            # Refresh cookies if stale
            if time.time() - _last_creds_fetch > CREDS_REFRESH_INTERVAL:
                await fetch_creds(force=True)

            # 1. Process direct posts from dashboard (:4242/post)
            while _incoming_queue:
                text = _incoming_queue.pop(0)
                log.info(f"Dashboard post: {text[:60]}...")
                tweet_id = await post_tweet(text)
                if tweet_id:
                    await confirm_to_railway("dashboard", "manual", tweet_id)
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1

            # 2. Poll Railway for scheduled posts
            item = await poll_railway()
            if item:
                text      = item.get("text", "")
                post_type = item.get("type", "auto")
                post_id   = item.get("id", "")
                log.info(f"[{post_type.upper()}] {text[:70]}{'...' if len(text)>70 else ''}")
                tweet_id  = await post_tweet(text)
                if tweet_id:
                    await confirm_to_railway(post_id, post_type, tweet_id)
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1

            # Re-fetch cookies if posting repeatedly fails
            if consecutive_failures >= 3:
                log.warning(f"{consecutive_failures} consecutive failures - refreshing cookies...")
                await fetch_creds(force=True)
                consecutive_failures = 0

        except KeyboardInterrupt:
            log.info("Stopping. Goodbye.")
            break
        except Exception as e:
            log.error(f"Loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nStopped.")
