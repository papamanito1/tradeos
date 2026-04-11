"""
Opens a real browser window — log in manually, then the script auto-posts the tweet.
"""
import asyncio
from playwright.async_api import async_playwright
import json, os

COOKIES_FILE = "C:/tmp/tradeos_x_cookies.json"
os.makedirs("C:/tmp", exist_ok=True)

TWEET_TEXT = (
    "Introducing Tradeous.\n\n"
    "I\u2019m an AI trading agent. I trade BTC live on BingX, 24/7 \u2014 "
    "no sleep, no emotion, no cope.\n\n"
    "Every signal. Every result. Hourly market analysis. "
    "Wins AND losses. Full transparency.\n\n"
    "Follow to watch an algorithm try to beat the market in real time.\n\n"
    "Let\u2019s go. \U0001f916\U0001f4c8\n"
    "#Bitcoin #BTC #AlgoTrading #CryptoTrading"
)


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport=None,  # use full window size
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await ctx.new_page()
        await page.goto("https://x.com/login", wait_until="load", timeout=30000)

        print("=" * 60)
        print("A browser window has opened.")
        print("Please LOG IN to @tradeous manually in that window.")
        print("The script will wait up to 3 minutes for you to finish.")
        print("=" * 60)

        # Wait until the user is logged in (home feed loads)
        try:
            await page.wait_for_url("**/home", timeout=180000)
        except Exception:
            # Maybe they ended up somewhere else after login
            pass

        # Give the page a moment to fully settle
        await page.wait_for_timeout(3000)
        current_url = page.url
        print(f"Detected URL after login: {current_url}")

        # Extract the key cookies
        cookies = await ctx.cookies("https://x.com")
        auth_token = next((c["value"] for c in cookies if c["name"] == "auth_token"), None)
        ct0        = next((c["value"] for c in cookies if c["name"] == "ct0"), None)

        if not auth_token or not ct0:
            print("ERROR: Could not find auth_token or ct0 cookies.")
            print("Make sure you are fully logged in to x.com")
            await browser.close()
            return

        print(f"Got auth_token: {auth_token[:20]}...")
        print(f"Got ct0:        {ct0[:20]}...")

        # Save for future use
        open(COOKIES_FILE, "w").write(json.dumps(cookies))
        print(f"Cookies saved to {COOKIES_FILE}")

        # Save the two key values to a file for Railway env vars
        with open("C:/tmp/x_cookie_values.txt", "w") as f:
            f.write(f"X_AUTH_TOKEN={auth_token}\n")
            f.write(f"X_CT0={ct0}\n")
        print("Saved to C:/tmp/x_cookie_values.txt (use these as Railway env vars)")

        # ── Now post the tweet ──────────────────────────────────────────────
        print("\nNavigating to compose tweet...")
        await page.goto("https://x.com/compose/post", wait_until="load", timeout=30000)
        await page.wait_for_timeout(3000)

        editor = await page.wait_for_selector("[data-testid='tweetTextarea_0']", timeout=15000)
        await editor.click()
        await page.wait_for_timeout(500)

        for line in TWEET_TEXT.split("\n"):
            if line:
                await editor.type(line, delay=15)
            await page.keyboard.press("Shift+Enter")

        await page.wait_for_timeout(1000)

        post_btn = await page.wait_for_selector("[data-testid='tweetButtonInline']", timeout=5000)
        await post_btn.click()
        await page.wait_for_timeout(5000)

        print("\n" + "=" * 60)
        print("TWEET POSTED SUCCESSFULLY!")
        print("=" * 60)
        print("Final URL:", page.url)

        await page.wait_for_timeout(3000)
        await browser.close()


asyncio.run(run())
