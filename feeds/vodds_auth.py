"""
feeds/vodds_auth.py — Vodds.com PS feed authentication

Login flow:
  1. POST vodds.com/member/login           → VSESS2 cookie
  2. POST vodds.com/member/sport/pin/login → pre-auth URL with PS token
  3. POST lenvora8/member-auth/v2/auth-token (JSON body) → x-app-data with _ulp + session vars
  4. Parse x-app-data → set as cookies on lenvora8 domain
  5. GET lenvora8/member-auth/v2/wstoken   → WS token for WS connection

Keepalive: GET lenvora8/member-auth/v2/keep-alive every 10s
Cookie save: every 5min while healthy
"""

import json
import os
import re
import time
from urllib.parse import unquote

from curl_cffi.requests import AsyncSession

from config import CREDENTIALS_FILE, COOKIE_FILE, WS_TOKEN_FILE
from core.logger import log_info, log_warn, log_error

VODDS_BASE   = "https://vodds.com"
VODDS_LOGIN  = f"{VODDS_BASE}/member/login"
VODDS_PINLOGIN = f"{VODDS_BASE}/member/sport/pin/login"


def _load_creds() -> dict:
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


class VoddsAuth:
    """Vodds.com session manager — PS feed via lenvora8 whitelabel."""

    def __init__(self):
        self._session = None  # AsyncSession
        self._ps_base: str = ""          # https://uyfnltp.lenvora8.com
        self._ulp: str = ""
        self._xapp_cookies: dict = {}    # parsed from auth-token x-app-data

    # ── Init ─────────────────────────────────────────────────────────

    async def init_session(self):
        """Create curl_cffi session and log in."""
        self._session = AsyncSession(impersonate="chrome120")
        await self._login()

    async def _login(self):
        """Full login flow: vodds → lenvora8 → WS token ready."""
        creds = _load_creds()
        username = creds["vodds_user"]
        password = creds["vodds_pass"]

        # Step 1: Login to vodds
        resp = await self._session.post(
            VODDS_LOGIN,
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
        if resp.status_code != 200:
            raise RuntimeError(f"Vodds login failed: status={resp.status_code}")
        body = resp.json()
        if body.get("messageType", -1) != 0:
            raise RuntimeError(f"Vodds login error: {body}")
        log_info(f"Vodds login OK (userId={body.get('userId')})")

        # Step 2: Get lenvora8 pre-auth URL
        r2 = await self._session.post(
            VODDS_PINLOGIN,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": "0",
            }
        )
        raw_url = r2.json()["data"]["loginUrl"]
        # Format: https://IP#b2bp_SUBDOMAIN/path
        m = re.match(r"https://[^#]+#b2bp_([^/]+)(/.+)", raw_url)
        if not m:
            raise RuntimeError(f"Unexpected pin/login URL format: {raw_url[:100]}")
        subdomain, path = m.group(1), m.group(2)
        self._ps_base = f"https://{subdomain}.lenvora8.com"

        # Extract PS token from path
        token_m = re.search(r"token=([^&]+)", path)
        ps_token = token_m.group(1) if token_m else ""
        if not ps_token:
            raise RuntimeError("Could not find PS token in pre-auth URL")
        log_info(f"PS base: {self._ps_base}  token={ps_token[:20]}...")

        # Step 3: POST auth-token with JSON body → get x-app-data
        ts = int(time.time() * 1000)
        r3 = await self._session.post(
            f"{self._ps_base}/member-auth/v2/auth-token?locale=en_US&_={ts}&withCredentials=true",
            json={
                "token": ps_token,
                "locale": "en",
                "oddsFormat": "EU",
                "sport": "soccer",
                "view": None,
                "mode": "LIGHT",
                "parentUrl": None,
            },
            headers={
                "x-app-data": "lang=en_US",
                "Referer": f"{self._ps_base}/en/compact/fwp?token={ps_token}&locale=en&oddsFormat=EU&sport=soccer&mode=LIGHT",
                "Origin": self._ps_base,
            }
        )
        if r3.status_code != 200:
            raise RuntimeError(f"auth-token failed: status={r3.status_code}")

        # Step 4: Parse x-app-data → set as cookies
        xapp = r3.headers.get("x-app-data", "")
        self._xapp_cookies = {}
        for part in xapp.split(";"):
            part = part.strip()
            if "=" in part:
                k, _, v = part.partition("=")
                k, v = k.strip(), unquote(v.strip())
                self._xapp_cookies[k] = v
                self._session.cookies.set(k, v, domain=f"{subdomain}.lenvora8.com")

        self._ulp = self._xapp_cookies.get("_ulp", "")
        log_info(f"PS session established — _ulp={'OK' if self._ulp else 'MISSING'}")

        # Save cookies to disk
        self.save_cookies_to_disk()

    # ── WS Token ─────────────────────────────────────────────────────

    async def fetch_token(self):
        """Fetch WS token. Checks cache first (< 600s), then REST."""
        # Check cache
        try:
            with open(WS_TOKEN_FILE) as f:
                td = json.load(f)
            age = time.time() - td.get("saved_at", 0)
            if age < 600 and td.get("token"):
                log_info(f"Using saved ws_token.json (age={age:.0f}s)")
                return td["token"]
        except Exception:
            pass

        if not self._session:
            return None

        ts = int(time.time() * 1000)
        try:
            resp = await self._session.get(
                f"{self._ps_base}/member-auth/v2/wstoken"
                f"?locale=en_US&_={ts}&withCredentials=true"
            )
            data = resp.json()
            token = data.get("token") or data.get("wsToken") or data.get("ws_token")
            if token:
                log_info("[AUTH] Vodds WS token OK")
                # Cache to disk
                with open(WS_TOKEN_FILE, "w") as f:
                    json.dump({"token": token, "saved_at": time.time()}, f)
            else:
                log_error("vodds_auth", f"WS token empty: {str(data)[:100]}")
            return token
        except Exception as e:
            log_error("vodds_auth", f"WS token fetch failed: {e}")
            return None

    def invalidate_token_cache(self):
        """Delete ws_token.json so next fetch hits REST."""
        try:
            os.remove(WS_TOKEN_FILE)
        except FileNotFoundError:
            pass

    # ── Session health ────────────────────────────────────────────────

    def get_cookie(self) -> str:
        """Return cookie string for WS upgrade request."""
        parts = []
        for c in self._session.cookies.jar:
            parts.append(f"{c.name}={c.value}")
        return "; ".join(parts)

    def get_ulp(self) -> str:
        return self._ulp

    def build_headers(self, method: str = "GET") -> dict:
        """Build request headers matching browser."""
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-GB,en;q=0.9",
            "Origin": self._ps_base,
            "Referer": f"{self._ps_base}/en/sports/soccer",
        }
        if method == "POST":
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
            hdrs["Content-Length"] = "0"
        return hdrs

    # ── Keepalive ─────────────────────────────────────────────────────

    async def keep_alive(self):
        """Raw HTTP keepalive. Raises on failure."""
        ts = int(time.time() * 1000)
        hdrs = self.build_headers(method="GET")
        resp = await self._session.get(
            f"{self._ps_base}/member-auth/v2/keep-alive"
            f"?locale=en_US&_={ts}&withCredentials=true",
            headers=hdrs, timeout=10
        )
        if resp.status_code != 200:
            raise RuntimeError(f"keepalive status={resp.status_code}")

    # ── Cookie persistence ────────────────────────────────────────────

    def save_cookies_to_disk(self):
        """Save current session cookies to COOKIE_FILE."""
        cookies = {}
        for c in self._session.cookies.jar:
            cookies[c.name] = c.value
        with open(COOKIE_FILE, "w") as f:
            json.dump({"cookie": cookies, "saved_at": time.time()}, f)
        log_info("Vodds cookies saved to disk")

    def reload_cookie(self):
        """Reload cookies from disk into session."""
        try:
            with open(COOKIE_FILE) as f:
                data = json.load(f)
            cookies = data.get("cookie", {})
            for k, v in cookies.items():
                self._session.cookies.set(k, v)
            log_info(f"Vodds cookies reloaded from disk ({len(cookies)} cookies)")
        except Exception as e:
            log_warn("vodds_auth", f"Cookie reload failed: {e}")

    async def refresh_cookies_via_playwright(self):
        """Re-login fully to get fresh cookies. Used by L3 recovery."""
        log_info("[VODDS] Re-logging in for fresh cookies...")
        await self._login()

    async def close(self):
        if self._session:
            await self._session.close()
