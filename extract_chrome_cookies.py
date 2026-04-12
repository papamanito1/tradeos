"""
Extract X (Twitter) cookies from Chrome's local cookie database.
Works while Chrome is running (copies the DB first).
Decrypts using Windows DPAPI — only works on the same Windows user account.
"""
import base64
import json
import os
import shutil
import sqlite3
import tempfile

import win32crypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CHROME_USER_DATA = os.path.expandvars(
    r"%LOCALAPPDATA%\Google\Chrome\User Data"
)
LOCAL_STATE_PATH = os.path.join(CHROME_USER_DATA, "Local State")
VALUES_FILE = "C:/tmp/x_cookie_values.txt"
os.makedirs("C:/tmp", exist_ok=True)


def get_encryption_key():
    with open(LOCAL_STATE_PATH, "r", encoding="utf-8") as f:
        local_state = json.load(f)
    encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    encrypted_key = encrypted_key[5:]  # strip "DPAPI" prefix
    return win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]


def decrypt_cookie(encrypted_value, key):
    if not encrypted_value:
        return ""
    # v10/v20 prefix (3 bytes)
    if encrypted_value[:3] in (b"v10", b"v20"):
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
    # Old DPAPI-only encryption
    return win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)[1].decode("utf-8")


def find_cookie_db():
    """Search Default and numbered profiles for x.com cookies."""
    profiles = ["Default"] + [f"Profile {i}" for i in range(1, 10)]
    for profile in profiles:
        db_path = os.path.join(CHROME_USER_DATA, profile, "Network", "Cookies")
        if os.path.exists(db_path):
            yield db_path, profile


def extract():
    print("=" * 60)
    print("  Chrome X Cookie Extractor")
    print("=" * 60)

    if not os.path.exists(LOCAL_STATE_PATH):
        print("\n  ERROR: Chrome Local State not found at:")
        print(f"    {LOCAL_STATE_PATH}")
        print("  Make sure Google Chrome is installed.")
        return None, None

    key = get_encryption_key()
    print("  Encryption key loaded")

    auth_token = None
    ct0 = None

    for db_path, profile in find_cookie_db():
        # Copy DB + WAL + SHM so we get a consistent snapshot even while Chrome has it open
        tmp_dir = tempfile.mkdtemp(prefix="chrome_cookies_")
        tmp_db = os.path.join(tmp_dir, "Cookies")
        copied = False
        for ext in ("", "-wal", "-shm"):
            src = db_path + ext
            dst = tmp_db + ext
            if os.path.exists(src):
                try:
                    # Use raw binary copy to bypass sharing violations
                    with open(src, "rb") as sf:
                        data = sf.read()
                    with open(dst, "wb") as df:
                        df.write(data)
                    if ext == "":
                        copied = True
                except (PermissionError, OSError) as e:
                    print(f"  [{profile}] Could not read {os.path.basename(src)}: {e}")

        if not copied:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            continue

        conn = sqlite3.connect(tmp_db)
        try:
            cursor = conn.execute(
                "SELECT name, encrypted_value FROM cookies "
                "WHERE host_key LIKE '%x.com' AND name IN ('auth_token', 'ct0')"
            )
            for name, enc_val in cursor.fetchall():
                val = decrypt_cookie(enc_val, key)
                if name == "auth_token" and val:
                    auth_token = val
                    print(f"  [{profile}] auth_token = {val[:20]}...")
                elif name == "ct0" and val:
                    ct0 = val
                    print(f"  [{profile}] ct0        = {val[:20]}...")
        except Exception as e:
            print(f"  [{profile}] DB read error: {e}")
        finally:
            conn.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if auth_token and ct0:
            break

    if not auth_token or not ct0:
        # Also try .twitter.com domain
        for db_path, profile in find_cookie_db():
            tmp_dir = tempfile.mkdtemp(prefix="chrome_cookies_tw_")
            tmp_db = os.path.join(tmp_dir, "Cookies")
            copied = False
            for ext in ("", "-wal", "-shm"):
                src = db_path + ext
                dst = tmp_db + ext
                if os.path.exists(src):
                    try:
                        with open(src, "rb") as sf:
                            data = sf.read()
                        with open(dst, "wb") as df:
                            df.write(data)
                        if ext == "":
                            copied = True
                    except (PermissionError, OSError):
                        pass
            if not copied:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                continue
            conn = sqlite3.connect(tmp_db)
            try:
                cursor = conn.execute(
                    "SELECT name, encrypted_value FROM cookies "
                    "WHERE host_key LIKE '%twitter.com' AND name IN ('auth_token', 'ct0')"
                )
                for name, enc_val in cursor.fetchall():
                    val = decrypt_cookie(enc_val, key)
                    if name == "auth_token" and val and not auth_token:
                        auth_token = val
                        print(f"  [{profile}] auth_token (twitter.com) = {val[:20]}...")
                    elif name == "ct0" and val and not ct0:
                        ct0 = val
                        print(f"  [{profile}] ct0 (twitter.com)        = {val[:20]}...")
            except Exception:
                pass
            finally:
                conn.close()
                shutil.rmtree(tmp_dir, ignore_errors=True)
            if auth_token and ct0:
                break

    return auth_token, ct0


if __name__ == "__main__":
    auth_token, ct0 = extract()

    if not auth_token or not ct0:
        print("\n  ERROR: Could not find X cookies in Chrome.")
        print("  Make sure you are logged in to x.com in Chrome.")
        raise SystemExit(1)

    with open(VALUES_FILE, "w") as f:
        f.write(f"X_AUTH_TOKEN={auth_token}\n")
        f.write(f"X_CT0={ct0}\n")

    print()
    print(f"  SAVED to {VALUES_FILE}")
    print()
    print(f"  X_AUTH_TOKEN = {auth_token[:20]}...")
    print(f"  X_CT0        = {ct0[:20]}...")
    print()
    print("=" * 60)
