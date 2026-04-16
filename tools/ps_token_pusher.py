"""
tools/ps_token_pusher.py — runs on Mac, keeps VPS fed with fresh wstoken.

Flow:
  1. Every PUSH_INTERVAL (240s): push ws_token.json to VPS via SCP
  2. If token is stale (age > TOKEN_REFRESH_SECS), run refresh_vodds.py first
  3. Always push immediately if file was just changed

Mac bot holds the valid vodds session + wstoken.
VPS bot has no Chrome/vodds session — this keeps it fed.

Usage:
    nohup python3 tools/ps_token_pusher.py > /tmp/token_pusher.log 2>&1 &
"""
import asyncio
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS_TOKEN_FILE = os.path.join(BOT_DIR, "data", "auth", "ws_token.json")
REFRESH_SCRIPT = os.path.join(BOT_DIR, "data", "refresh_vodds.py")
VPS = "root@45.32.25.201"
REMOTE_PATH = "/opt/bot/data/auth/ws_token.json"
PUSH_INTERVAL = 240       # 4 min push cadence
TOKEN_REFRESH_SECS = 300  # refresh token if older than 5 min (TTL=10min)


def token_age() -> float:
    """Return age of current token in seconds, or inf if missing/unknown."""
    try:
        d = json.load(open(WS_TOKEN_FILE))
        return time.time() - d.get("saved_at", 0)
    except Exception:
        return float('inf')


def refresh_token():
    """Run refresh_vodds.py to get a fresh token. Returns True on success."""
    print(f"[PUSHER] Token stale — running refresh_vodds.py...", flush=True)
    result = subprocess.run(
        ["python3", REFRESH_SCRIPT],
        capture_output=True, text=True, timeout=120, cwd=BOT_DIR
    )
    if result.returncode == 0:
        print(f"[PUSHER] refresh_vodds.py OK", flush=True)
        return True
    else:
        print(f"[PUSHER] refresh_vodds.py FAILED: {result.stderr[:200]}", flush=True)
        return False


def push_token():
    if not os.path.exists(WS_TOKEN_FILE):
        print(f"[PUSHER] ws_token.json not found", flush=True)
        return False
    result = subprocess.run(
        ["scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
         WS_TOKEN_FILE, f"{VPS}:{REMOTE_PATH}"],
        capture_output=True, timeout=15
    )
    if result.returncode == 0:
        try:
            token = json.load(open(WS_TOKEN_FILE)).get("token", "")[:20]
        except Exception:
            token = "?"
        print(f"[PUSHER] Pushed token={token}... to {VPS}", flush=True)
        return True
    else:
        print(f"[PUSHER] SCP failed: {result.stderr.decode()[:100]}", flush=True)
        return False


async def main():
    print(f"[PUSHER] Starting — pushing {WS_TOKEN_FILE} → {VPS}:{REMOTE_PATH} every {PUSH_INTERVAL}s", flush=True)
    last_mtime = 0
    while True:
        try:
            # Refresh token if stale before pushing
            age = token_age()
            if age > TOKEN_REFRESH_SECS:
                print(f"[PUSHER] Token age={age:.0f}s > {TOKEN_REFRESH_SECS}s — refreshing", flush=True)
                refresh_token()

            mtime = os.path.getmtime(WS_TOKEN_FILE)
            if mtime != last_mtime:
                print(f"[PUSHER] Token file changed — pushing immediately", flush=True)
                if push_token():
                    last_mtime = mtime
            else:
                push_token()
        except Exception as e:
            print(f"[PUSHER] Error: {e}", flush=True)
        await asyncio.sleep(PUSH_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
