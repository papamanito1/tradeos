"""
Posts the intro tweet using your real Edge browser profile.
Close ALL Edge windows first, then run: python post_intro_tweet.py
"""
import asyncio
import os
from playwright.async_api import async_playwright

EDGE_USER_DATA = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")
EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

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


async def post():
    async with async_playwright() as p:
        print("Launching Edge with your profile...")
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=EDGE_USER_DATA,
            executable_path=EDGE_EXE,
            headless=False,
            channel="msedge",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        await page.goto("https://x.com/home", wait_until="load", timeout=30000)
        await page.wait_for_timeout(3000)

        if "login" in page.url:
            print("ERROR: Not logged into X. Please log in first.")
            await ctx.close()
            return

        # Extract cookies now while we have the session
        cookies = await ctx.cookies()
        auth_token = ct0 = ""
        for c in cookies:
            if c["name"] == "auth_token":
                auth_token = c["value"]
            if c["name"] == "ct0":
                ct0 = c["value"]
        print(f"Session found. auth_token: {auth_token[:20]}...")

        print("Opening compose window...")
        await page.goto("https://x.com/compose/post", wait_until="load", timeout=20000)
        await page.wait_for_timeout(3000)

        editor = await page.wait_for_selector("[data-testid='tweetTextarea_0']", timeout=15000)
        await editor.click()
        await page.wait_for_timeout(500)

        # Type tweet line by line
        lines = TWEET_TEXT.split("\n")
        for i, line in enumerate(lines):
            if line:
                await page.keyboard.type(line, delay=15)
            if i < len(lines) - 1:
                await page.keyboard.press("Shift+Enter")
                await page.wait_for_timeout(30)

        await page.wait_for_timeout(1500)

        # Try Ctrl+Enter to submit (X keyboard shortcut)
        print("Submitting with Ctrl+Enter...")
        await page.keyboard.press("Control+Enter")
        await page.wait_for_timeout(4000)

        final_url = page.url
        print("Final URL:", final_url)

        if "compose" not in final_url:
            print("\nTWEET POSTED!")
        else:
            print("\nCtrl+Enter did not post. The browser window is open.")
            print("Please CLICK THE POST BUTTON in the Edge window, then come back here.")
            print("Waiting 30 seconds for you to click Post...")
            await page.wait_for_timeout(30000)

        if auth_token and ct0:
            print("\n--- Railway env vars ---")
            print(f"X_AUTH_TOKEN = {auth_token}")
            print(f"X_CT0        = {ct0}")
            print("------------------------")

        await ctx.close()


asyncio.run(post())
