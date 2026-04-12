"""
Get fresh X cookies using Playwright with a persistent Chrome profile.
This logs into x.com using the real user profile, extracts decrypted cookies,
and saves them for Railway.
"""
import asyncio
import json
import os
import time

VALUES_FILE = "C:/tmp/x_cookie_values.txt"
PROFILE_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
os.makedirs("C:/tmp", exist_ok=True)


async def main():
    from playwright.async_api import async_playwright

    print("=" * 60)
    print("  Fresh X Cookie Extractor")
    print("=" * 60)
    print()

    async with async_playwright() as p:
        # Launch Playwright's own Chromium with the user's Chrome profile
        # This is separate from the system Chrome so no conflict
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--no-first-run",
            ],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await ctx.new_page()

        print("  Browser opened. Navigating to x.com...")
        try:
            await page.goto("https://x.com/login", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            await page.goto("https://x.com", wait_until="commit", timeout=30000)

        print()
        print("  *** LOG IN to @tradeous in the browser window ***")
        print("  The script will detect login automatically.")
        print("  Waiting up to 3 minutes...")
        print()

        auth_token = None
        ct0 = None

        for attempt in range(36):  # 3 min max
            await asyncio.sleep(5)
            try:
                cookies = await ctx.cookies("https://x.com")
            except Exception:
                break
            auth_token = next((c["value"] for c in cookies if c["name"] == "auth_token"), None)
            ct0 = next((c["value"] for c in cookies if c["name"] == "ct0"), None)
            if auth_token and ct0:
                print(f"  Login detected at {attempt * 5}s!")
                break
            if attempt % 6 == 5:
                print(f"  Still waiting... ({(attempt + 1) * 5}s)")

        await browser.close()

    if not auth_token or not ct0:
        print("\n  ERROR: No cookies found. Make sure you logged in fully.")
        raise SystemExit(1)

    with open(VALUES_FILE, "w") as f:
        f.write(f"X_AUTH_TOKEN={auth_token}\n")
        f.write(f"X_CT0={ct0}\n")

    print(f"  auth_token = {auth_token[:20]}...")
    print(f"  ct0        = {ct0[:20]}...")
    print()
    print(f"  SAVED to {VALUES_FILE}")
    print()
    print(f"  X_AUTH_TOKEN={auth_token}")
    print(f"  X_CT0={ct0}")
    print()
    print("  Now update Railway:")
    print("    1. Railway > your backend > Variables")
    print("    2. Set X_AUTH_TOKEN and X_CT0 to the values above")
    print("    3. Redeploy (or it auto-deploys)")
    print("=" * 60)


asyncio.run(main())
