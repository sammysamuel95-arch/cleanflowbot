"""
feeds/ps_auth.py — ONE unified PS auth for all providers.

Uses _CurlSession from core/auth/ps3838_auth.py (battle-tested wrapper with
_ulp renewal, x-app-data capture, Set-Cookie logging on every response).

Provider only changes _login(). Everything else is identical:
  vodds:       _login() = pure curl_cffi (vodds.com → pin/login → auth-token)
  ps3838:      _login() = Playwright cookie grab (ps3838.com)
  pinnacle888: _login() = Playwright cookie grab (pinnacle888.com)

Usage:
    ps_auth = PSAuth(provider="vodds")
    await ps_auth.init_session()
    token = await ps_auth.fetch_token()
"""

import json
import os
import re
import time
from urllib.parse import unquote

from curl_cffi.requests import AsyncSession

import config
from config import CREDENTIALS_FILE, COOKIE_FILE, WS_TOKEN_FILE
from core.logger import log_info, log_warn, log_error

# Shared session wrapper — has all survival features built in:
#   _ulp renewal, x-app-data capture, Set-Cookie logging, aiohttp interface
from feeds.ps3838_auth import _CurlSession

VODDS_BASE = "https://vodds.com"
VODDS_LOGIN = f"{VODDS_BASE}/member/login"
VODDS_PINLOGIN = f"{VODDS_BASE}/member/sport/pin/login"


def _load_creds() -> dict:
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


class PSAuth:
    """Unified PS auth — one class, all providers. Same interface as old PS3838Auth.

    _session is _CurlSession (shared wrapper), so:
      async with ps_auth._session.get(url) as resp:   ← works
      resp.status                                       ← works (mapped from status_code)
      _ulp renewal on every response                   ← automatic
    """

    SUPPORTED = {"vodds", "ps3838", "pinnacle888"}

    def __init__(self, provider: str = "vodds"):
        provider = provider.lower()
        if provider not in self.SUPPORTED:
            raise ValueError(f"PSAuth: unknown provider '{provider}'. Supported: {self.SUPPORTED}")

        self._provider = provider
        self._session = None        # _CurlSession (shared wrapper, ONE session for ALL)
        self._ps_base: str = ""     # https://uyfnltp.lenvora8.com (dynamic for vodds)
        self._cookie_str: str = ""
        log_info(f"[PSAuth] provider={provider}")

    @property
    def provider(self) -> str:
        return self._provider

    def _get_vodds_creds(self) -> tuple:
        """Get active vodds credentials — from accounts DB if available, else credentials.json."""
        accounts_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     '..', 'data', 'auth', 'vodds_accounts.json')
        try:
            with open(accounts_file) as f:
                db = json.load(f)
            active = next((a for a in db["accounts"] if a["status"] == "active"), None)
            if active:
                return active["user"], active["pass"]
        except Exception:
            pass
        creds = _load_creds()
        return creds["vodds_user"], creds["vodds_pass"]

    def _rotate_vodds_account(self, banned_user: str) -> tuple:
        """Mark current account banned, activate next ready account. Returns (user, pass) or (None, None)."""
        accounts_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     '..', 'data', 'auth', 'vodds_accounts.json')
        try:
            with open(accounts_file) as f:
                db = json.load(f)
            # Mark banned
            for a in db["accounts"]:
                if a["user"] == banned_user:
                    a["status"] = "banned"
            # Find next ready
            next_acc = next((a for a in db["accounts"] if a["status"] == "ready"), None)
            if next_acc:
                next_acc["status"] = "active"
                # Update credentials.json too
                creds = _load_creds()
                creds["vodds_user"] = next_acc["user"]
                creds["vodds_pass"] = next_acc["pass"]
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       '..', 'data', 'auth', 'credentials.json'), 'w') as f:
                    json.dump(creds, f, indent=2)
                with open(accounts_file, 'w') as f:
                    json.dump(db, f, indent=2)
                log_info(f"[AUTH] Account rotated: {banned_user} → {next_acc['user']}")
                return next_acc["user"], next_acc["pass"]
            else:
                log_warn("AUTH", "All vodds accounts banned! No ready accounts left.")
                with open(accounts_file, 'w') as f:
                    json.dump(db, f, indent=2)
        except Exception as e:
            log_warn("AUTH", f"Account rotation failed: {e}")
        return None, None

    # ═══════════════════════════════════════════════════════════════════
    # INIT
    # ═══════════════════════════════════════════════════════════════════

    async def init_session(self):
        """Create _CurlSession, load saved cookies, login only if needed.

        FIX for cookie overwrite bug:
          Old: always called _login() which overwrote 32 good cookies with ~10.
          New: if _ulp present in disk cookies, test wstoken first.
               Only call _login() if wstoken fails (stale session).
        """
        self._session = _CurlSession(
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/131.0.0.0 Safari/537.36",
                "Referer": config.PS_BASE_URL + "/",
            }
        )

        # Load saved cookies from disk
        self._load_initial_cookies()
        if self._cookie_str:
            self._session.load_cookies(self._cookie_str)

        # Check if we have a valid session already (skip re-login if so)
        ulp_in_cookies = "_ulp=" in self._cookie_str if self._cookie_str else False
        if ulp_in_cookies and self._provider == "vodds" and self._ps_base:
            log_info("[PSAuth] _ulp found in disk cookies — testing wstoken before login...")
            token = await self._raw_fetch_token()
            if token:
                log_info("[PSAuth] Disk cookies VALID — skipping re-login (cookie overwrite prevented)")
                # Save fresh token
                wl_domain = self._ps_base if self._ps_base.startswith("http") else f"https://{self._ps_base}"
                wl_host = wl_domain.replace("https://", "")
                with open(WS_TOKEN_FILE, "w") as f:
                    json.dump({
                        "token": token,
                        "wl_domain": wl_domain,
                        "ws_url": f"wss://{wl_host}/sports-websocket/ws?token={token}",
                        "saved_at": time.time(),
                    }, f)
            else:
                log_info("[PSAuth] Disk cookies STALE (wstoken 403) — falling back to re-login")
                try:
                    await self._login()
                except Exception as e:
                    log_warn("PSAuth", f"Re-login failed: {e} — continuing with disk cookies (WS will use pushed token)")
        else:
            # No _ulp or no ps_base → must login
            try:
                await self._login()
            except Exception as e:
                log_warn("PSAuth", f"Login failed: {e} — will rely on pushed token from Mac")

        # Log the active PS base URL (no config patching — readers use ps_auth._ps_base)
        if self._ps_base:
            log_info(f"[PSAuth] PS_BASE={self._ps_base}")

        ulp_status = "present" if self._session._ulp_value else "MISSING"
        log_info(f"[PSAuth] Session ready (_ulp: {ulp_status})")

    # ═══════════════════════════════════════════════════════════════════
    # LOGIN — the ONLY part that differs per provider
    # ═══════════════════════════════════════════════════════════════════

    async def _login(self):
        """Provider-specific login. Sets cookies in _CurlSession."""
        if self._provider == "vodds":
            await self._login_vodds()
        else:
            await self._login_playwright()

    async def _login_vodds(self):
        """Vodds login — pure curl_cffi via _CurlSession. Auto-rotates banned accounts."""
        username, password = self._get_vodds_creds()

        # Step 1: Login to vodds.com
        async with self._session.post(
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
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Vodds login failed: status={resp.status}")
            body = await resp.json(content_type=None)
            if body.get("messageType", -1) != 0:
                raise RuntimeError(f"Vodds login error: {body}")
            log_info(f"Vodds login OK (userId={body.get('userId')}) user={username}")

        # Step 2: Get pin/login URL
        async with self._session.post(
            VODDS_PINLOGIN,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": "0",
            }
        ) as r2:
            data2 = await r2.json(content_type=None)
            if "data" not in data2:
                # Account banned — rotate to next account and retry once
                log_warn("AUTH", f"pin/login banned for {username}: {data2}")
                next_user, next_pass = self._rotate_vodds_account(username)
                if next_user:
                    log_info(f"[AUTH] Rotated to account: {next_user}")
                    try:
                        import asyncio as _asyncio
                        from core.notifier import notify as _notify
                        _asyncio.create_task(_notify(
                            f"🔄 Vodds account {username} banned — switched to {next_user}",
                            debounce_key="vodds_rotate"
                        ))
                    except Exception:
                        pass
                    return await self._login_vodds()  # retry with new account
                raise RuntimeError(f"pin/login failed — all accounts banned: {data2}")
            raw_url = data2["data"]["loginUrl"]

        # Parse wl_prefix and pin_token from loginUrl
        fragment = raw_url.split("#")[1] if "#" in raw_url else ""
        wl_prefix = fragment.split("/")[0].replace("b2bp_", "") if fragment else ""
        token_m = re.search(r"token=([A-Za-z0-9+/=]+)", raw_url)
        ps_token = token_m.group(1) if token_m else ""
        if not wl_prefix or not ps_token:
            raise RuntimeError(f"Could not parse loginUrl: {raw_url[:100]}")
        log_info(f"wl_prefix={wl_prefix} token={ps_token[:20]}...")

        # Step 3: auth-token via proxy (bypasses IP block on datacenter VPS)
        # Try all WL domains — proxy is Amsterdam which is unblocked
        from curl_cffi.requests import AsyncSession as _DirectSession
        _PROXY = "http://168.199.244.251:80"
        _WL_DOMAINS = ["lenvora8.com", "auremi88.com", "eviran66.com", "mervani99.com"]

        proxy_session = _DirectSession(impersonate="chrome120", proxies={"https": _PROXY, "http": _PROXY})
        # Copy existing cookies to proxy session
        for k, v in self._session._session.cookies.items():
            proxy_session.cookies.set(k, v)

        ps_base_found = None
        for _domain in _WL_DOMAINS:
            _ps_base_try = f"https://{wl_prefix}.{_domain}"
            try:
                r3 = await proxy_session.post(
                    f"{_ps_base_try}/member-auth/v2/auth-token",
                    json={"token": ps_token},
                    headers={
                        "Content-Type": "application/json;charset=UTF-8",
                        "Origin": _ps_base_try,
                        "Referer": f"{_ps_base_try}/en/compact/fwp",
                    },
                    timeout=15,
                )
                body3 = r3.json()
                log_info(f"[AUTH] auth-token {_domain}: {r3.status_code} {body3.get('message','?')}")
                if body3.get("message") == "AUTHENTICATED":
                    ps_base_found = _ps_base_try
                    # Copy auth cookies back to main session
                    for k, v in proxy_session.cookies.items():
                        self._session._session.cookies.set(k, v)
                    log_info(f"[AUTH] AUTHENTICATED via proxy on {ps_base_found}")
                    break
                elif "BLOCKED" in str(body3):
                    log_warn("AUTH", f"LOGIN_BLOCKED on {_domain} — trying next")
            except Exception as _e:
                log_warn("AUTH", f"auth-token {_domain} error: {_e}")

        await proxy_session.close()

        if not ps_base_found:
            try:
                import asyncio as _a
                from core.notifier import notify as _n
                _a.get_event_loop().run_until_complete(_n(
                    "🚨 AUTH FAILED: auth-token LOGIN_BLOCKED on all domains\nProxy down or vodds account banned. Bot will crash.",
                    debounce_key="auth_blocked"
                ))
            except Exception:
                pass
            raise RuntimeError("auth-token LOGIN_BLOCKED on all domains — proxy may be down")

        self._ps_base = ps_base_found
        wl_host = self._ps_base.replace("https://", "")
        self._session.set_domain(wl_host)

        # _ulp is now in _session from proxy cookie copy above
        # Re-read _ulp from session cookies manually since proxy bypassed interceptor
        for k, v in self._session._session.cookies.items():
            if k == "_ulp":
                self._session._ulp_value = v
                break

        # _CurlSession already captured Set-Cookie + x-app-data _ulp via interceptor
        log_info(f"PS session established — _ulp={'OK' if self._session._ulp_value else 'MISSING'}")

        # Write wl_domain to ws_token.json — preserve existing token if present
        # (Mac token pusher keeps ws_token.json fed; don't wipe it on VPS)
        existing = {}
        try:
            with open(WS_TOKEN_FILE) as f:
                existing = json.load(f)
        except Exception:
            pass
        existing["wl_domain"] = self._ps_base
        existing.setdefault("saved_at", time.time())
        with open(WS_TOKEN_FILE, "w") as f:
            json.dump(existing, f)

        self.save_cookies_to_disk()

    async def _login_playwright(self):
        """PS3838/Pinnacle888 login — Playwright cookie grab."""
        import subprocess
        script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'refresh_ps3838.py')
        try:
            result = subprocess.run(
                ["python3", script],
                capture_output=True, text=True, timeout=120,
                cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'),
            )
            if result.returncode == 0:
                log_info("[AUTH] Playwright cookie grab OK")
                self._load_initial_cookies()
                # Set domain BEFORE loading cookies
                if self._provider == "ps3838":
                    self._session.set_domain("www.ps3838.com")
                elif self._provider == "pinnacle888":
                    self._session.set_domain("www.pinnacle888.com")
                if self._cookie_str:
                    self._session.load_cookies(self._cookie_str)
            else:
                log_warn("AUTH", f"Playwright failed: {result.stderr[:200]}")
        except Exception as e:
            log_warn("AUTH", f"Playwright error: {e}")

    # ═══════════════════════════════════════════════════════════════════
    # EVERYTHING BELOW IS SHARED — identical for ALL providers
    # _CurlSession handles _ulp renewal, Set-Cookie, x-app-data automatically
    # ═══════════════════════════════════════════════════════════════════

    def get_cookie(self) -> str:
        """Return current cookie string (always fresh from _CurlSession jar)."""
        if self._session:
            return self._session.get_cookie_str()
        return self._cookie_str

    def get_ulp(self) -> str:
        """Return current _ulp value."""
        if self._session:
            return self._session._ulp_value
        return ""

    def get_cookies_dict(self) -> dict:
        """Extract all cookies as {name: value} dict."""
        cookie_str = self.get_cookie()
        cookies = {}
        for part in cookie_str.split(';'):
            part = part.strip()
            if '=' in part:
                k, _, v = part.partition('=')
                cookies[k.strip()] = v.strip()
        return cookies

    def _get_wl_host(self):
        """Get whitelabel host — dynamic, not hardcoded."""
        if self._ps_base:
            return self._ps_base.replace("https://", "")
        if self._session:
            return self._session._get_wl_host()
        return None

    def build_headers(self, method: str = "GET", referer_sport: str = "soccer") -> dict:
        """Build headers matching real Chrome browser. Same for all providers."""
        cookies = self.get_cookies_dict()
        wl_host = self._get_wl_host() or "www.pinnacle888.com"

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
            "Sec-Ch-Ua": '"Chromium";v="131", "Not-A.Brand";v="24", "Google Chrome";v="131"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Referer": f"https://{wl_host}/en/sports/{referer_sport}",
            "Priority": "u=1, i",
            "X-Browser-Session-Id": cookies.get("BrowserSessionId", cookies.get("BrowserSessionId_1145", "")),
            "X-Custid": cookies.get("custid", cookies.get("custid_1145", "")),
            "X-Lcu": cookies.get("lcu", ""),
            "X-Slid": cookies.get("SLID", cookies.get("SLID_1145", "")),
            "X-U": cookies.get("u", cookies.get("u_1145", "")),
        }

        if method == "POST":
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Origin"] = f"https://{wl_host}"

        app_keys = [
            'dpJCA', 'pctag', 'directusToken', 'BrowserSessionId',
            'BrowserSessionId_1145', 'PCTR', '_og', '_ulp', 'custid',
            'custid_1145', '_userDefaultView', '__prefs', 'lang'
        ]
        app_parts = [f"{k}={cookies[k]}" for k in app_keys if cookies.get(k)]
        headers["X-App-Data"] = ";".join(app_parts)

        return headers

    async def _raw_fetch_token(self) -> str:
        """Simple wstoken check using current session cookies. No retry, no recovery.
        Used by init_session() to test if disk cookies are still valid.
        Returns token string on success, None on failure."""
        if not self._ps_base:
            return None
        wl_host = self._ps_base.replace("https://", "")
        ts = int(time.time() * 1000)
        token_url = f"https://{wl_host}/member-auth/v2/wstoken?locale=en_US&_={ts}&withCredentials=true"
        try:
            async with self._session.get(token_url, headers={
                "Referer": f"https://{wl_host}/",
                "Origin": self._ps_base,
                "Cookie": self.get_cookie(),
            }, timeout=10) as r:
                log_info(f"[PSAuth] raw wstoken check status={r.status}")
                if r.status == 200:
                    data = await r.json(content_type=None)
                    return data.get("token") or data.get("wsToken") or data.get("t")
        except Exception as e:
            log_warn("PSAuth", f"raw wstoken check failed: {e}")
        return None

    async def fetch_token(self) -> str:
        """Fetch WS token. Checks disk cache first (for VPS where wstoken endpoint is IP-blocked).
        Mac token pusher keeps ws_token.json fresh every 240s.
        On 403 → auto re-login via _login() → retry once."""
        # Check disk cache first — VPS can't hit wstoken endpoint (IP-blocked)
        try:
            with open(WS_TOKEN_FILE) as f:
                saved = json.load(f)
            age = time.time() - saved.get("saved_at", 0)
            disk_token = saved.get("token")
            if disk_token and age < 3600:  # up to 1hr — pusher keeps it fresh, use whatever exists
                log_info(f"[AUTH] wstoken from disk (age={age:.0f}s)")
                # Update ps_base from disk if not set
                if not self._ps_base and saved.get("wl_domain"):
                    self._ps_base = saved["wl_domain"]
                    wl_host_d = self._ps_base.replace("https://", "")
                    if self._session:
                        self._session.set_domain(wl_host_d)
                return disk_token
        except Exception:
            pass

        wl_host = self._get_wl_host()
        if not wl_host:
            log_error("ps_auth", "No wl_host — cannot fetch token")
            return None

        wl_domain = f"https://{wl_host}"
        cookie_str = self.get_cookie()
        ts = int(time.time() * 1000)
        token_url = f"https://{wl_host}/member-auth/v2/wstoken?locale=en_US&_={ts}&withCredentials=true"

        try:
            async with self._session.get(token_url, headers={
                "Referer": f"https://{wl_host}/",
                "Origin": wl_domain,
                "Cookie": cookie_str,
            }, timeout=10) as r:
                log_info(f"[AUTH] wstoken status={r.status}")
                if r.status == 200:
                    data = await r.json(content_type=None)
                    token = data.get("token") or data.get("wsToken") or data.get("t")
                    if token:
                        log_info("[AUTH] wstoken OK")
                        with open(WS_TOKEN_FILE, "w") as f:
                            json.dump({
                                "token": token,
                                "wl_domain": wl_domain,
                                "ws_url": f"wss://{wl_host}/sports-websocket/ws?token={token}",
                                "saved_at": time.time(),
                            }, f)
                        return token
                    else:
                        log_error("ps_auth", f"wstoken empty — {data}")
                elif r.status == 403:
                    log_warn("ps_auth", "wstoken 403 — auto re-login...")
                    try:
                        await self._login()
                        # Retry once with fresh session
                        wl_host2 = self._get_wl_host() or wl_host
                        ts2 = int(time.time() * 1000)
                        token_url2 = f"https://{wl_host2}/member-auth/v2/wstoken?locale=en_US&_={ts2}&withCredentials=true"
                        async with self._session.get(token_url2, headers={
                            "Referer": f"https://{wl_host2}/",
                            "Origin": f"https://{wl_host2}",
                            "Cookie": self.get_cookie(),
                        }, timeout=10) as r2:
                            log_info(f"[AUTH] wstoken retry status={r2.status}")
                            if r2.status == 200:
                                data2 = await r2.json(content_type=None)
                                token2 = data2.get("token") or data2.get("wsToken") or data2.get("t")
                                if token2:
                                    log_info("[AUTH] wstoken OK (after auto-recovery)")
                                    with open(WS_TOKEN_FILE, "w") as f:
                                        json.dump({
                                            "token": token2,
                                            "wl_domain": f"https://{wl_host2}",
                                            "ws_url": f"wss://{wl_host2}/sports-websocket/ws?token={token2}",
                                            "saved_at": time.time(),
                                        }, f)
                                    return token2
                    except Exception as e2:
                        log_error("ps_auth", f"Auto-recovery failed: {e2}")
                else:
                    log_error("ps_auth", f"wstoken status={r.status}")
        except Exception as e:
            log_error("ps_auth", f"Token fetch failed: {e}")
        return None

    def invalidate_token_cache(self):
        """Delete ws_token.json so next fetch_token() hits REST."""
        try:
            os.remove(WS_TOKEN_FILE)
            log_info("[AUTH] ws_token.json invalidated")
        except FileNotFoundError:
            pass

    def save_cookies_to_disk(self):
        """Save session cookies + ps_base to disk."""
        cookie_str = self.get_cookie()
        try:
            with open(COOKIE_FILE, "w") as f:
                json.dump({
                    "cookie": cookie_str,
                    "ps_base": self._ps_base,
                    "provider": self._provider,
                    "saved_at": time.time(),
                }, f)
        except Exception as e:
            log_error("ps_auth", f"Cookie save failed: {e}")

    def _load_initial_cookies(self):
        """Load cookies from disk at startup."""
        try:
            with open(COOKIE_FILE) as f:
                data = json.load(f)
            if isinstance(data, list):
                # Old format: [{name, value}, ...]
                self._cookie_str = '; '.join(
                    f"{c['name']}={c['value']}" for c in data if 'name' in c and 'value' in c)
            elif isinstance(data, dict):
                cookie_val = data.get('cookie', '')
                if isinstance(cookie_val, dict):
                    self._cookie_str = '; '.join(f"{k}={v}" for k, v in cookie_val.items())
                elif isinstance(cookie_val, str):
                    self._cookie_str = cookie_val
                if data.get('ps_base'):
                    self._ps_base = data['ps_base']
            log_info(f"Cookies loaded from disk ({len(self._cookie_str.split(';'))} cookies)")
        except Exception as e:
            log_info(f"[ps_auth] No saved cookies: {e}")

    def reload_cookie(self):
        """Reload cookies from disk into session. Clear ws_token.json."""
        self._load_initial_cookies()
        if self._session and self._cookie_str:
            self._session.load_cookies(self._cookie_str)
        # Preserve wl_domain in cookie.json before clearing ws_token
        try:
            with open(WS_TOKEN_FILE) as f:
                td = json.load(f)
            wl = td.get("wl_domain", "")
            if wl and not self._ps_base:
                self._ps_base = wl
        except Exception:
            pass
        # Clear ws_token.json (Commandment 2: always fetch fresh)
        try:
            os.remove(WS_TOKEN_FILE)
            log_info("Cookies reloaded — ws_token.json cleared")
        except FileNotFoundError:
            log_info("Cookies reloaded into session")
        # Invalidate cached wl_host
        if self._session and hasattr(self._session, '_wl_host_cached'):
            self._session._wl_host_cached = None

    async def refresh_cookies_via_playwright(self):
        """Re-login to get fresh cookies. Called by L3 recovery.
        vodds: pure curl_cffi (instant). ps3838/pinnacle888: Playwright."""
        log_info(f"[AUTH] Re-login for fresh cookies (provider={self._provider})...")
        await self._login()

    async def close(self):
        if self._session:
            await self._session.close()
