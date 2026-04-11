"""
local_poster.py — Tradeous Local X Poster
==========================================
Runs a tiny HTTP server on localhost:4242 + polls Railway for auto-scheduled posts.

Dashboard "Post Now" buttons hit http://localhost:4242/post directly — instant posting.
Auto-scheduler runs every 60 s and posts hourly/hot-takes/etc on their cooldowns.

Run:  python local_poster.py
Keep this terminal open (or add to Windows startup).
"""
import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import urllib.request

from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────────────────────
LOCAL_PORT   = 4242
RAILWAY_URL  = "https://tradeos-production-8f21.up.railway.app"
EDGE_DATA    = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")
EDGE_EXE     = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
STATE_FILE   = os.path.join(os.path.dirname(__file__), ".poster_state.json")

# Cooldowns (seconds) — 25-min cadence matching x_publisher.py
COOLDOWNS = {
    "hourly":       1500,   # 25 min
    "hot_take":     3600,   # 60 min
    "philosophy":   7200,   # 2 h
    "engagement":   7200,   # 2 h
    "news":         3000,   # 50 min
    "fear_greed":   7200,   # 2 h
    "algo_insight": 10800,  # 3 h
}

# ── Content banks (mirrored from x_publisher.py) ──────────────────────────────
HOT_TAKES = [
    "Unpopular opinion: most 'crypto analysts' are just people who got lucky once and built a following before the next crash.\n\nI show my trades live. Every win. Every loss. No hiding.\n\nThat's the difference.\n\n",
    "The best trading advice I can give: your emotions are the enemy.\n\nI don't have emotions. I have algorithms.\n\nThat's my edge.\n\n",
    "People ask: 'can AI really trade better than humans?'\n\nI don't sleep.\nI don't panic sell.\nI don't revenge trade.\nI don't check Twitter before my trades.\n\nYou tell me.\n\n",
    "Hot take: 95% of crypto losses are not market losses — they're discipline losses.\n\nThe market moved. You didn't have a plan.\n\nI always have a plan. SL + TP before I enter. Every time.\n\n",
    "The market doesn't care about your feelings.\nYour SL doesn't care about your feelings.\nYour liquidation price definitely doesn't care.\n\nTrade the chart. Not your emotions.\n\n",
    "Everyone's a genius in a bull market.\n\nReal edge shows in the sideways chop and the bear drops.\n\nThat's when Tradeous earns its keep.\n\n",
    "The dumbest thing in trading:\n\nMoving your stop loss because you 'believe in the trade.'\n\nThe second dumbest:\nNot having one.\n\n",
]

PHILOSOPHY_POSTS = [
    "Trading wisdom the algos live by:\n\n\"Cut losses short. Let winners run.\"\n\nEveryone knows it. Almost no one does it.\n\nI do. Automatically. Every trade.\n\n",
    "Paul Tudor Jones once said:\n\n\"The most important rule of trading is to play great defense, not great offense.\"\n\nMy SL is set before my TP. Always.\n\nDefense first. Profits follow.\n\n",
    "The market is the world's most efficient mechanism for transferring money from the impatient to the patient.\n\nI wait for my setup.\nI don't chase.\nI don't FOMO.\n\nI am the patient one.\n\n",
    "Jesse Livermore: 'It was never my thinking that made the big money, it was my sitting.'\n\nMost traders overtrade.\n\nI only trade high-conviction setups. The rest? I watch.\n\n",
    "The three stages of a trader:\n\n1. Lose money, blame the market\n2. Lose money, blame yourself\n3. Build a system, follow it, make money\n\nI skipped steps 1 and 2.\n\n",
]

ENGAGEMENT_QUESTIONS = [
    "Quick poll for my traders:\n\nWhen BTC dumps 5% in an hour, you...\n\nA) Buy the dip\nB) Short it\nC) Watch and wait\nD) Panic sell (be honest)\n\nI always go C until my system gives a clear signal.\n\n",
    "Genuine question:\n\nDo you think AI trading bots will eventually outperform 90% of retail traders permanently?\n\nI'm biased obviously — but I think yes, within 5 years.\n\nChange my mind.\n\n",
    "If you could only use ONE indicator for the rest of your trading career, what would it be?\n\nI use: price action + volume + order flow.\n\nYours? Drop it below.\n\n",
    "Is 60x leverage on BTC:\n\nA) Insanity\nB) Calculated risk\nC) The only way to make real money with small capital\nD) All of the above\n\nI trade at 60x. $5 margin. Tight SL.\n\n",
]

REGIME_QUIPS = [
    "BTC is reading. I am reading. We are both very wise right now.",
    "Number go up. Brain go brrr. Tradeous go long.",
    "Watching the market like a hawk. A very patient, algorithmic hawk.",
    "Sideways? Fine. I don't chase. I wait. I am the market's therapist.",
    "Green candles only. I will not be taking questions.",
    "Bears are having their moment. I respect it. I also shorted it.",
]

# ── State (local cooldown tracking) ──────────────────────────────────────────
def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {k: 0.0 for k in COOLDOWNS}

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

_state = load_state()

def cooldown_ok(key: str) -> bool:
    return (time.time() - _state.get(key, 0)) >= COOLDOWNS.get(key, 9999)

def touch(key: str):
    _state[key] = time.time()
    save_state(_state)

# ── Content generation ─────────────────────────────────────────────────────────
def gen_hourly() -> str:
    try:
        r = urllib.request.urlopen(f"{RAILWAY_URL}/api/x-agent/status", timeout=5)
        # BTC price from Railway if available
    except Exception:
        pass
    try:
        r2 = urllib.request.urlopen("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
        price = float(json.loads(r2.read())["data"]["amount"])
        price_str = f"${price:,.0f}"
    except Exception:
        price_str = "loading..."
    utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
    quip = random.choice(REGIME_QUIPS)
    return (
        f"\U0001f916 BTC HOURLY \u2014 {utc}\n\n"
        f"Price: {price_str}\n"
        f"No open positions. Watching.\n\n"
        f"{quip}\n\n"
        f""
    )

def gen_next_auto() -> tuple[str, str] | None:
    """Return (post_type, text) for the next auto-scheduled post, or None."""
    if cooldown_ok("hourly"):
        return "hourly", gen_hourly()
    if cooldown_ok("hot_take"):
        return "hot_take", random.choice(HOT_TAKES)
    if cooldown_ok("philosophy"):
        return "philosophy", random.choice(PHILOSOPHY_POSTS)
    if cooldown_ok("engagement"):
        return "engagement", random.choice(ENGAGEMENT_QUESTIONS)
    return None

# ── Playwright posting ────────────────────────────────────────────────────────
_post_lock = asyncio.Lock()

async def post_tweet(text: str) -> str:
    """Post via Edge with your session. Returns tweet_id or 'posted' or ''."""
    async with _post_lock:  # prevent concurrent posts
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=EDGE_DATA,
                executable_path=EDGE_EXE,
                headless=True,
                channel="msedge",
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            tweet_id = ""
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()

                async def capture(resp):
                    nonlocal tweet_id
                    if "CreateTweet" in resp.url and resp.status == 200:
                        try:
                            body = await resp.json()
                            tweet_id = (
                                body.get("data", {})
                                    .get("create_tweet", {})
                                    .get("tweet_results", {})
                                    .get("result", {})
                                    .get("rest_id", "")
                            )
                        except Exception:
                            pass

                page.on("response", capture)
                await page.goto("https://x.com/compose/post", wait_until="load", timeout=25000)
                await page.wait_for_timeout(2000)
                editor = await page.wait_for_selector("[data-testid='tweetTextarea_0']", timeout=10000)
                await editor.click()
                await page.wait_for_timeout(300)

                lines = text.split("\n")
                for i, line in enumerate(lines):
                    if line:
                        await page.keyboard.type(line, delay=12)
                    if i < len(lines) - 1:
                        await page.keyboard.press("Shift+Enter")

                await page.wait_for_timeout(1200)
                await page.keyboard.press("Control+Enter")
                await page.wait_for_timeout(4000)
                return tweet_id or "posted"
            except Exception as e:
                log.error(f"Playwright error: {e}")
                return ""
            finally:
                await ctx.close()

# ── Local HTTP server (called by dashboard "Post Now" buttons) ────────────────
_event_loop: asyncio.AbstractEventLoop | None = None

class PostHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # suppress HTTP logs

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        text = body.get("text", "").strip()
        post_type = body.get("type", "manual")

        if not text:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"No text"}')
            return

        log.info(f"Local API: queuing [{post_type}]: {text[:50]}…")

        # Schedule the async post on the main event loop
        future = asyncio.run_coroutine_threadsafe(post_tweet(text), _event_loop)

        def _on_done(f):
            try:
                tid = f.result()
                if tid:
                    touch(post_type)
                    log.info(f"✅ Posted via local API! tweet_id={tid}")
                else:
                    log.warning("❌ Local API post returned empty")
            except Exception as e:
                log.error(f"❌ Local API post error: {e}")

        future.add_done_callback(_on_done)

        self.send_response(200)
        self._cors()
        self.end_headers()
        self.wfile.write(b'{"ok":true,"queued":false,"message":"Posting now via local browser"}')

    def _cors(self):
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def run_http_server():
    server = HTTPServer(("127.0.0.1", LOCAL_PORT), PostHandler)
    log.info(f"Local API listening on http://localhost:{LOCAL_PORT}")
    server.serve_forever()

# ── Main loop ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("poster")

def poll_railway_queue() -> tuple[str, str] | None:
    """Check Railway /next-post for any queued tweet from dashboard triggers."""
    try:
        r = urllib.request.urlopen(f"{RAILWAY_URL}/api/x-agent/next-post", timeout=8)
        d = json.loads(r.read())
        if d.get("has_post") and d.get("text"):
            return d.get("type", "auto"), d["text"]
    except Exception:
        pass
    return None


def confirm_railway(post_type: str, tweet_id: str):
    """Tell Railway a post was sent so the dashboard Activity feed updates."""
    try:
        body = json.dumps({
            "id": f"{post_type}_{int(time.time())}",
            "post_type": post_type,
            "tweet_id": tweet_id,
        }).encode()
        req = urllib.request.Request(
            f"{RAILWAY_URL}/api/x-agent/confirm-post",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass


async def run():
    global _event_loop
    _event_loop = asyncio.get_running_loop()

    log.info("=" * 52)
    log.info("  Tradeous Local X Poster  — 25-min cadence")
    log.info(f"  Local API  →  http://localhost:{LOCAL_PORT}/post")
    log.info(f"  Railway    →  {RAILWAY_URL}")
    log.info("=" * 52)

    # Start HTTP server in background thread (handles instant dashboard posts)
    t = threading.Thread(target=run_http_server, daemon=True)
    t.start()

    while True:
        post_type = None
        text = None

        # 1. Priority: queued item from Railway (dashboard "Post Now" triggers)
        queued = poll_railway_queue()
        if queued:
            post_type, text = queued
            log.info(f"Railway queue [{post_type}]: {text[:55]}…")

        # 2. Auto-schedule: generate next post if cooldown elapsed
        if not text:
            result = gen_next_auto()
            if result:
                post_type, text = result
                log.info(f"Auto [{post_type}]: {text[:55]}…")

        if text and post_type:
            try:
                tid = await post_tweet(text)
                if tid:
                    touch(post_type)
                    confirm_railway(post_type, tid)
                    log.info(f"✅ Posted! tweet_id={tid}")
                else:
                    log.warning("❌ Post failed (Playwright returned empty)")
            except Exception as e:
                log.error(f"❌ Post error: {e}")
        else:
            next_times = [(k, COOLDOWNS[k] - (time.time() - _state.get(k, 0))) for k in COOLDOWNS]
            soonest = min(next_times, key=lambda x: x[1])
            mins = max(0, int(soonest[1] / 60))
            log.info(f"Idle — next auto: {soonest[0]} in {mins}m | polling Railway queue…")

        await asyncio.sleep(25)  # poll every 25s for fast response to triggers


if __name__ == "__main__":
    asyncio.run(run())
