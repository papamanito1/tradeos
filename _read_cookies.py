"""
Extract X cookies from Chrome via Chrome DevTools Protocol.
Restarts Chrome with --remote-debugging-port, fetches decrypted cookies
via CDP, then restarts Chrome normally.
"""
import json
import os
import subprocess
import time

VALUES_FILE = "C:/tmp/x_cookie_values.txt"
os.makedirs("C:/tmp", exist_ok=True)

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
if not os.path.exists(CHROME_EXE):
    CHROME_EXE = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
DEBUG_PORT = 9222


def main():
    print("=" * 60)
    print("  Chrome X Cookie Extractor (via DevTools Protocol)")
    print("=" * 60)

    # 1. Close Chrome
    print("  Closing Chrome...")
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True)
    time.sleep(2)

    # 2. Relaunch Chrome with remote debugging on Profile 3
    print(f"  Launching Chrome with debug port {DEBUG_PORT}...")
    chrome_proc = subprocess.Popen([
        CHROME_EXE,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--profile-directory=Profile 3",
        "--restore-last-session",
    ])
    time.sleep(5)

    # 3. Connect to CDP and get cookies
    import urllib.request
    auth_token = None
    ct0 = None

    try:
        # Get list of debuggable targets
        targets_url = f"http://127.0.0.1:{DEBUG_PORT}/json"
        resp = urllib.request.urlopen(targets_url, timeout=5)
        targets = json.loads(resp.read())
        print(f"  Connected to Chrome CDP ({len(targets)} tab(s))")
    except Exception as e:
        print(f"  Cannot connect to Chrome CDP: {e}")
        print("  Restarting Chrome normally...")
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True)
        time.sleep(1)
        subprocess.Popen([CHROME_EXE, "--profile-directory=Profile 3", "--restore-last-session"])
        raise SystemExit(1)

    # Use websocket to send CDP command
    try:
        import websockets
        HAS_WS = True
    except ImportError:
        HAS_WS = False

    if not HAS_WS:
        # Fallback: use the /json/protocol endpoint or a simple WS client
        try:
            import asyncio
            from websockets.sync.client import connect as ws_connect

            HAS_WS = True
        except ImportError:
            pass

    if not HAS_WS:
        print("  Installing websockets...")
        subprocess.run(["pip", "install", "websockets"], capture_output=True)
        import websockets  # noqa

    # Connect to first available tab
    ws_url = None
    for target in targets:
        if target.get("type") == "page":
            ws_url = target.get("webSocketDebuggerUrl")
            if ws_url:
                break

    if not ws_url:
        # Use browser-level endpoint
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=5)
            version_info = json.loads(resp.read())
            ws_url = version_info.get("webSocketDebuggerUrl")
        except Exception:
            pass

    if not ws_url:
        print("  ERROR: No debuggable target found")
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True)
        time.sleep(1)
        subprocess.Popen([CHROME_EXE, "--profile-directory=Profile 3", "--restore-last-session"])
        raise SystemExit(1)

    print(f"  WebSocket: {ws_url[:60]}...")

    import asyncio

    async def get_cookies():
        import websockets
        async with websockets.connect(ws_url) as ws:
            # First navigate to x.com to ensure cookies are accessible
            await ws.send(json.dumps({
                "id": 1,
                "method": "Network.getCookies",
                "params": {"urls": ["https://x.com", "https://twitter.com"]}
            }))
            resp = json.loads(await ws.recv())
            return resp.get("result", {}).get("cookies", [])

    try:
        cookies = asyncio.run(get_cookies())
        print(f"  Got {len(cookies)} cookies from Chrome")
    except Exception as e:
        print(f"  CDP cookie fetch error: {e}")
        cookies = []

    for c in cookies:
        if c["name"] == "auth_token" and c.get("value"):
            auth_token = c["value"]
            print(f"  auth_token = {auth_token[:20]}...")
        elif c["name"] == "ct0" and c.get("value"):
            ct0 = c["value"]
            print(f"  ct0        = {ct0[:20]}...")

    # 4. Restart Chrome without debug port
    print("  Restarting Chrome normally...")
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True)
    time.sleep(1.5)
    subprocess.Popen([CHROME_EXE, "--profile-directory=Profile 3", "--restore-last-session"])

    if not auth_token or not ct0:
        print("\n  ERROR: X cookies not found. Make sure you're logged in to x.com.")
        raise SystemExit(1)

    with open(VALUES_FILE, "w") as f:
        f.write(f"X_AUTH_TOKEN={auth_token}\n")
        f.write(f"X_CT0={ct0}\n")

    print()
    print(f"  SAVED to {VALUES_FILE}")
    print(f"  X_AUTH_TOKEN={auth_token}")
    print(f"  X_CT0={ct0}")
    print("=" * 60)


if __name__ == "__main__":
    main()
