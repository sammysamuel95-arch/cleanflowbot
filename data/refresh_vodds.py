"""
refresh_vodds.py — Get fresh PS WS token via Vodds whitelabel (pure curl_cffi, no Playwright).

Full auth chain (per HOLY_GRAIL_VPS_SESSION_IMMORTALITY.md):
  Step 1: POST vodds.com/member/login              → VSESS2 cookie
  Step 2: POST vodds.com/member/sport/pin/login    → loginUrl with pin_token
  Step 3: POST {wl}/member-auth/v2/auth-token      → 13 cookies incl _ulp
  Step 4: GET  {wl}/member-auth/v2/wstoken         → WS token

Saves: data/auth/cookie.json + data/auth/ws_token.json

Usage:
    python3 data/refresh_vodds.py
"""
import asyncio, json, os, re, time, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_DIR = os.path.join(DATA_DIR, "auth")

with open(os.path.join(AUTH_DIR, "credentials.json")) as f:
    _creds = json.load(f)

# Use active account from vodds_accounts.json if available
try:
    with open(os.path.join(AUTH_DIR, "vodds_accounts.json")) as _f:
        _db = json.load(_f)
    _active = next((a for a in _db["accounts"] if a["status"] == "active"), None)
    if _active:
        VODDS_USER = _active["user"]
        VODDS_PASS = _active["pass"]
    else:
        VODDS_USER = _creds.get("vodds_user", "")
        VODDS_PASS = _creds.get("vodds_pass", "")
except Exception:
    VODDS_USER = _creds.get("vodds_user", "")
    VODDS_PASS = _creds.get("vodds_pass", "")

WL_DOMAINS = ["lenvora8.com", "auremi88.com", "eviran66.com", "mervani99.com"]

# Fallback proxy for auth-token when running from a blocked IP (e.g. VPS)
# Proven working: Amsterdam Webshare proxy
FALLBACK_PROXY = "http://168.199.244.251:80"

VODDS_BASE   = "https://vodds.com"
VODDS_LOGIN  = f"{VODDS_BASE}/member/login"
VODDS_PINLOGIN = f"{VODDS_BASE}/member/sport/pin/login"


async def refresh() -> tuple:
    """Full auth chain. Returns (token, ws_url, wl_domain, cookie_str)."""
    from curl_cffi.requests import AsyncSession

    session = AsyncSession(impersonate="chrome120")

    # ── Step 1: Login to vodds.com ────────────────────────────────────
    print(f"[REFRESH] Step 1: Login to vodds.com (user={VODDS_USER})...")
    r1 = await session.post(
        VODDS_LOGIN,
        data={
            "username": VODDS_USER,
            "accessToken": VODDS_PASS,
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
    body1 = r1.json()
    if body1.get("messageType", -1) != 0:
        raise RuntimeError(f"Vodds login failed: {body1}")
    print(f"[REFRESH] Login OK (userId={body1.get('userId')})")

    # ── Step 2: Get pin/login URL ─────────────────────────────────────
    print("[REFRESH] Step 2: POST pin/login...")
    r2 = await session.post(
        VODDS_PINLOGIN,
        headers={
            "Referer": f"{VODDS_BASE}/member/dashboard",
            "Origin": VODDS_BASE,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Length": "0",
        }
    )
    body2 = r2.json()
    if "data" not in body2:
        raise RuntimeError(f"pin/login failed (account banned?): {body2}")

    raw_url = body2["data"]["loginUrl"]
    print(f"[REFRESH] Got loginUrl: {raw_url[:80]}...")

    # Parse pin_token and wl_prefix from loginUrl
    pin_token = re.search(r"token=([A-Za-z0-9+/=]+)", raw_url)
    if not pin_token:
        raise RuntimeError(f"No token in loginUrl: {raw_url[:100]}")
    pin_token = pin_token.group(1)

    fragment = raw_url.split("#")[1] if "#" in raw_url else ""
    wl_prefix = fragment.split("/")[0].replace("b2bp_", "") if fragment else ""
    if not wl_prefix:
        raise RuntimeError(f"Could not parse wl_prefix from: {raw_url[:100]}")
    print(f"[REFRESH] wl_prefix={wl_prefix} pin_token={pin_token[:20]}...")

    # ── Step 3: auth-token via proxy (proxy bypasses IP block) ───────
    print(f"[REFRESH] Step 3: POST auth-token via proxy {FALLBACK_PROXY}...")
    ps_base = None

    proxy_session = AsyncSession(impersonate="chrome120", proxies={"https": FALLBACK_PROXY, "http": FALLBACK_PROXY})
    # Copy cookies from main session to proxy session
    for k, v in session.cookies.items():
        proxy_session.cookies.set(k, v)

    for domain in WL_DOMAINS:
        url = f"https://{wl_prefix}.{domain}/member-auth/v2/auth-token"
        try:
            r3 = await proxy_session.post(
                url,
                json={"token": pin_token},
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Origin": f"https://{wl_prefix}.{domain}",
                    "Referer": f"https://{wl_prefix}.{domain}/en/compact/fwp",
                },
                timeout=15,
            )
            body3 = r3.json()
            print(f"[REFRESH] auth-token {domain}: status={r3.status_code} result={body3.get('message','?')}")
            if body3.get("message") == "AUTHENTICATED":
                ps_base = f"https://{wl_prefix}.{domain}"
                # Copy auth cookies back to main session
                for k, v in proxy_session.cookies.items():
                    session.cookies.set(k, v)
                print(f"[REFRESH] AUTHENTICATED on {ps_base}")
                break
            elif "BLOCKED" in str(body3):
                print(f"[REFRESH] LOGIN_BLOCKED on {domain} — trying next domain")
        except Exception as e:
            print(f"[REFRESH] {domain} error: {e}")

    await proxy_session.close()

    if not ps_base:
        raise RuntimeError("auth-token LOGIN_BLOCKED on all domains via proxy — check proxy or try different proxy")

    # ── Step 4: Get wstoken ───────────────────────────────────────────
    print("[REFRESH] Step 4: GET wstoken...")
    ts = int(time.time() * 1000)
    wl_host = ps_base.replace("https://", "")
    r4 = await session.get(
        f"{ps_base}/member-auth/v2/wstoken?locale=en_US&_={ts}&withCredentials=true",
        headers={
            "Referer": f"{ps_base}/",
            "Origin": ps_base,
        },
        timeout=10,
    )
    body4 = r4.json()
    token = body4.get("token") or body4.get("wsToken")
    if not token:
        raise RuntimeError(f"wstoken failed: {body4}")
    print(f"[REFRESH] wstoken OK: {token[:30]}...")

    # ── Collect cookies ───────────────────────────────────────────────
    cookie_str = "; ".join(f"{k}={v}" for k, v in session.cookies.items())

    await session.close()

    # ── Save ──────────────────────────────────────────────────────────
    os.makedirs(AUTH_DIR, exist_ok=True)

    ws_url = f"wss://{wl_host}/sports-websocket/ws?token={token}"
    with open(os.path.join(AUTH_DIR, "ws_token.json"), "w") as f:
        json.dump({
            "token": token,
            "ws_url": ws_url,
            "wl_domain": ps_base,
            "saved_at": time.time(),
        }, f)
    print(f"[REFRESH] Saved ws_token.json")

    with open(os.path.join(AUTH_DIR, "cookie.json"), "w") as f:
        json.dump({
            "cookie": cookie_str,
            "ps_base": ps_base,
            "wl_domain": ps_base,
            "provider": "vodds",
            "saved_at": time.time(),
        }, f)
    print(f"[REFRESH] Saved cookie.json")

    return token, ws_url, ps_base, cookie_str


if __name__ == "__main__":
    token, ws_url, ps_base, _ = asyncio.run(refresh())
    print(f"\nDone.")
    print(f"Token: {token[:40]}...")
    print(f"WS URL: {ws_url[:80]}...")
    print(f"PS base: {ps_base}")
