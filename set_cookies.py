"""
Paste your X cookies from Chrome DevTools and save them for Railway + local_poster.
No browser automation needed.

HOW TO GET THE COOKIES:
  1. Open Chrome normally → go to https://x.com (log in if needed)
  2. Press F12 (DevTools) → click "Application" tab
  3. Left sidebar: Cookies → https://x.com
  4. Find "auth_token" → copy its Value
  5. Find "ct0" → copy its Value
  6. Run this script and paste them when prompted
"""
import os

VALUES_FILE = "C:/tmp/x_cookie_values.txt"
os.makedirs("C:/tmp", exist_ok=True)

print("=" * 60)
print("  Tradeous X Cookie Setup")
print("=" * 60)
print()
print("  Open Chrome -> x.com -> F12 -> Application -> Cookies -> x.com")
print("  Copy the values for 'auth_token' and 'ct0'")
print()

auth_token = input("  Paste auth_token: ").strip()
ct0        = input("  Paste ct0:        ").strip()

if not auth_token or not ct0:
    print("\n  ERROR: Both values are required.")
    raise SystemExit(1)

with open(VALUES_FILE, "w") as f:
    f.write(f"X_AUTH_TOKEN={auth_token}\n")
    f.write(f"X_CT0={ct0}\n")

print()
print(f"  Saved to {VALUES_FILE}")
print()
print("  Next steps:")
print("    1. Go to Railway -> your backend -> Variables")
print(f"    2. Set X_AUTH_TOKEN = {auth_token[:15]}...")
print(f"    3. Set X_CT0        = {ct0[:15]}...")
print("    4. Railway auto-redeploys")
print("    5. Run: python local_poster.py")
print()
print("=" * 60)
