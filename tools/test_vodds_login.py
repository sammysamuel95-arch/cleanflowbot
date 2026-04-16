"""
Quick vodds.com login test using curl_cffi.
Tests: login → get session cookies → access lenvora8 PS feed → fetch WS token
"""
import asyncio
import json
from curl_cffi.requests import AsyncSession

VODDS_BASE   = "https://vodds.com"
PS_BASE      = "https://uyfnltp.lenvora8.com"
LOGIN_URL    = f"{VODDS_BASE}/member/login"
CREDS_FILE   = "data/auth/credentials.json"

async def main():
    with open(CREDS_FILE) as f:
        creds = json.load(f)

    username = creds.get("vodds_user", "usdzc2861736")
    password = creds.get("vodds_pass", "Us5paB19uk")

    session = AsyncSession(impersonate="chrome120")

    # ── Step 1: Login ────────────────────────────────────────────────
    print(f"[1] Logging in as {username}...")
    resp = await session.post(
        LOGIN_URL,
        data={
            "username": username,
            "accessToken": password,
            "loginMethod": "NORMAL",
            "timezone": "Asia/Jakarta",
            "isMobile": "false",
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{VODDS_BASE}/static/login",
            "Origin": VODDS_BASE,
        }
    )
    print(f"    status={resp.status_code}")
    try:
        body = resp.json()
        print(f"    response={json.dumps(body)[:300]}")
    except Exception:
        print(f"    response={resp.text[:300]}")

    # Show vodds cookies
    vodds_cookies = {k: v for k, v in session.cookies.items()}
    print(f"    cookies={list(vodds_cookies.keys())}")

    if resp.status_code != 200:
        print("[FAIL] Login failed")
        return

    # ── Step 2: Access PS feed (lenvora8) ───────────────────────────
    print(f"\n[2] Accessing PS feed at {PS_BASE}...")
    import time
    ts = int(time.time() * 1000)
    token_url = (
        f"{PS_BASE}/member-auth/v2/keep-alive"
        f"?locale=en_US&_={ts}&withCredentials=true"
    )
    resp2 = await session.get(token_url)
    print(f"    keepalive status={resp2.status_code}")

    # ── Step 2b: Get dashboard HTML and find lenvora token ──────────
    print(f"\n[2b] Fetching dashboard to find lenvora token...")
    resp_dash = await session.get(f"{VODDS_BASE}/member/dashboard")
    html = resp_dash.text
    # Find lenvora iframe or token
    import re
    matches = re.findall(r'lenvora8\.com[^\s"\'<>]{0,300}', html)
    for m in matches[:5]:
        print(f"    found: {m[:200]}")
    token_matches = re.findall(r'token=([A-Za-z0-9+/=_%]{20,})', html)
    for t in token_matches[:3]:
        print(f"    token param: {t[:80]}")

    # ── Step 3: POST auth-token to establish PS session ─────────────
    print(f"\n[3] POST auth-token to establish PS session...")
    ts = int(time.time() * 1000)
    resp3 = await session.post(
        f"{PS_BASE}/member-auth/v2/auth-token?locale=en_US&_={ts}&withCredentials=true",
        headers={
            "x-app-data": "lang=en_US",
            "Referer": f"{PS_BASE}/en/compact/fwp",
            "Origin": PS_BASE,
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": "0",
        }
    )
    print(f"    auth-token status={resp3.status_code}")
    try:
        print(f"    response={resp3.json()}")
    except Exception:
        print(f"    response={resp3.text[:200]}")

    # ── Step 4: Fetch WS token ───────────────────────────────────────
    print(f"\n[4] Fetching WS token...")
    ts = int(time.time() * 1000)
    resp4 = await session.get(
        f"{PS_BASE}/member-auth/v2/wstoken?locale=en_US&_={ts}&withCredentials=true"
    )
    print(f"    wstoken status={resp4.status_code}")
    try:
        data = resp4.json()
        token = data.get("token") or data.get("wsToken") or data.get("ws_token")
        tok_str = ("OK (" + token[:20] + "...)") if token else "MISSING"
        print(f"    token={tok_str}")
        print(f"    full response={json.dumps(data)[:300]}")
    except Exception:
        print(f"    response={resp4.text[:300]}")

    await session.close()

if __name__ == "__main__":
    asyncio.run(main())
