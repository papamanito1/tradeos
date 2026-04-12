"""
Opens a real browser window — log in to @tradeous manually.
After login, cookies are saved automatically.
Then it posts an intro tweet (you can skip by closing the browser).
"""
import asyncio
import json
import os

COOKIES_FILE = "C:/tmp/tradeos_x_cookies.json"
VALUES_FILE  = "C:/tmp/x_cookie_values.txt"
os.makedirs("C:/tmp", exist_ok=True)


async def run():
    from playwright.async_api import async_playwright

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
            viewport=None,
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await ctx.new_page()
        await page.goto("https://x.com/login", wait_until="load", timeout=30000)

        print("=" * 60)
        print("  A browser window has opened.")
        print("  Please LOG IN to @tradeous manually.")
        print("  The script will wait up to 5 minutes.")
        print("  DO NOT close the browser window!")
        print("=" * 60)

        # Poll for login cookies — check every 5 seconds for up to 5 minutes
        auth_token = None
        ct0 = None
        for attempt in range(60):
            try:
                await page.wait_for_timeout(5000)
            except Exception:
                break
            try:
                cookies = await ctx.cookies("https://x.com")
            except Exception:
                print("ERROR: Browser was closed before cookies could be read.")
                print("Please try again and don't close the browser until you see 'COOKIES SAVED'.")
                return
            auth_token = next((c["value"] for c in cookies if c["name"] == "auth_token"), None)
            ct0        = next((c["value"] for c in cookies if c["name"] == "ct0"), None)
            if auth_token and ct0:
                print(f"  Login detected! (attempt {attempt + 1})")
                break
            if attempt % 6 == 5:
                print(f"  Still waiting for login... ({(attempt + 1) * 5}s elapsed)")

        if not auth_token or not ct0:
            # One final try — grab current page cookies
            try:
                cookies = await ctx.cookies("https://x.com")
                auth_token = next((c["value"] for c in cookies if c["name"] == "auth_token"), None)
                ct0        = next((c["value"] for c in cookies if c["name"] == "ct0"), None)
            except Exception:
                pass

        auth_token = next((c["value"] for c in cookies if c["name"] == "auth_token"), None)
        ct0        = next((c["value"] for c in cookies if c["name"] == "ct0"), None)

        if not auth_token or not ct0:
            print("ERROR: Could not find auth_token or ct0 cookies.")
            print("Make sure you are fully logged in to x.com.")
            try:
                await browser.close()
            except Exception:
                pass
            return

        print(f"\nGot auth_token: {auth_token[:20]}...")
        print(f"Got ct0:        {ct0[:20]}...")

        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f)
        print(f"Full cookies saved to {COOKIES_FILE}")

        with open(VALUES_FILE, "w") as f:
            f.write(f"X_AUTH_TOKEN={auth_token}\n")
            f.write(f"X_CT0={ct0}\n")

        print(f"\n{'=' * 60}")
        print(f"  COOKIES SAVED to {VALUES_FILE}")
        print(f"  ")
        print(f"  Next steps:")
        print(f"    1. Go to Railway > your backend > Variables")
        print(f"    2. Update X_AUTH_TOKEN and X_CT0 with these values")
        print(f"    3. Railway will auto-redeploy")
        print(f"    4. Then run: python local_poster.py")
        print(f"{'=' * 60}\n")

        try:
            await browser.close()
        except Exception:
            pass


asyncio.run(run())
