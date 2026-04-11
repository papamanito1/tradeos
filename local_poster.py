"""
local_poster.py — Tradeous Local X Poster
==========================================
Runs on your Windows PC 24/7 in the background.
Every few minutes it checks Railway for tweet content and posts via Edge browser
(your real IP bypasses X's datacenter block).

Run:  python local_poster.py
Keep this terminal open (or add to Windows startup).
"""
import asyncio
import os
import time
import urllib.request
import json
import logging

from playwright.async_api import async_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("poster")

RAILWAY_URL = "https://tradeos-production-8f21.up.railway.app"
EDGE_USER_DATA = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")
EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# How often to check for new content (seconds)
CHECK_INTERVAL = 300   # 5 minutes


async def post_tweet(text: str) -> str:
    """Post text via Edge with your session. Returns tweet ID or ''."""
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=EDGE_USER_DATA,
            executable_path=EDGE_EXE,
            headless=True,
            channel="msedge",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            # Intercept the CreateTweet response to capture tweet ID
            tweet_id = ""

            async def capture_response(resp):
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

            page.on("response", capture_response)

            await page.goto("https://x.com/compose/post", wait_until="load", timeout=25000)
            await page.wait_for_timeout(2000)

            editor = await page.wait_for_selector("[data-testid='tweetTextarea_0']", timeout=10000)
            await editor.click()
            await page.wait_for_timeout(300)

            # Type tweet line by line (Shift+Enter for newlines)
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
        finally:
            await ctx.close()


def fetch_next_post() -> dict | None:
    """Ask Railway what to post next."""
    try:
        r = urllib.request.urlopen(f"{RAILWAY_URL}/api/x-agent/next-post", timeout=10)
        data = json.loads(r.read())
        return data if data.get("has_post") else None
    except Exception as e:
        log.warning(f"Could not reach Railway: {e}")
        return None


def confirm_post(post_id: str, post_type: str, tweet_id: str) -> None:
    """Tell Railway the tweet was posted so it updates cooldowns."""
    try:
        body = json.dumps({"id": post_id, "post_type": post_type, "tweet_id": tweet_id}).encode()
        req = urllib.request.Request(
            f"{RAILWAY_URL}/api/x-agent/confirm-post",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning(f"Confirm failed: {e}")


async def run():
    log.info("=" * 50)
    log.info("Tradeous Local X Poster started")
    log.info(f"Checking Railway every {CHECK_INTERVAL // 60} minutes")
    log.info("=" * 50)

    while True:
        post = fetch_next_post()

        if post:
            post_type = post["type"]
            text = post["text"]
            post_id = post["id"]

            log.info(f"Posting [{post_type}]: {text[:60]}…")
            try:
                tweet_id = await post_tweet(text)
                if tweet_id:
                    confirm_post(post_id, post_type, tweet_id)
                    log.info(f"✅ Posted! tweet_id={tweet_id}")
                else:
                    log.warning("❌ Post returned empty ID")
            except Exception as e:
                log.error(f"❌ Post error: {e}")
        else:
            log.info("Nothing to post yet (all on cooldown)")

        log.info(f"Next check in {CHECK_INTERVAL // 60} min…")
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run())
