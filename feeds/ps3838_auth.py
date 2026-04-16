"""
v2perfectbot — core/auth/ps3838_auth.py
PS3838 auth with curl_cffi Chrome TLS impersonation.

Uses curl_cffi to mimic Chrome's exact TLS fingerprint (JA3/JA4),
preventing server-side session binding rejection that caused hourly expiry.

Public interface unchanged — all callers use _session.get() / resp.status /
await resp.text() / await resp.json() exactly as before.
"""

import json
import time
from curl_cffi.requests import AsyncSession

from config import (
    PS_BASE_URL, PS_TOKEN_URL, COOKIE_FILE, WS_TOKEN_FILE, PS_PROXY,
)
from core.logger import log_info, log_error


# ── aiohttp-compatible response wrapper ────────────────────────────────────────

class _CurlResponse:
    """Wraps curl_cffi response to match aiohttp response interface."""

    def __init__(self, resp, url=''):
        self._resp = resp
        self.status = resp.status_code
        self.headers = resp.headers
        self._url = url
        # Log any Set-Cookie headers so we can identify which endpoints renew the session
        # curl_cffi joins multiple Set-Cookie headers with ", " between them
        sc = resp.headers.get('set-cookie', '') or resp.headers.get('Set-Cookie', '')
        if sc:
            path = url.split('ps3838.com')[-1].split('?')[0] if url else '?'
            # Split on ", " to separate multiple cookies, then take only the name (before first "=")
            # Each Set-Cookie entry: "name=value; Path=/; ..."  — we only want "name"
            cookie_names = []
            for entry in sc.split(', '):
                first_part = entry.split(';')[0].strip()
                if '=' in first_part:
                    name = first_part.split('=')[0].strip()
                    # Skip cookie attribute names (Path, Domain, SameSite, etc.)
                    if name and name[0] != '_' and name.lower() not in ('path', 'domain', 'samesite', 'secure', 'httponly', 'expires', 'max-age'):
                        cookie_names.append(name)
                    elif name and name[0] == '_':
                        cookie_names.append(name)
            if cookie_names:
                from core.logger import log_info
                log_info(f"[SETCOOKIE] {path} → {cookie_names}")

    async def text(self):
        return self._resp.text

    async def json(self, content_type=None):
        return self._resp.json()

    async def read(self):
        return self._resp.content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _CurlRequestContextManager:
    """Async context manager for curl_cffi requests."""

    def __init__(self, coro, curl_session=None):
        self._coro = coro
        self._resp = None
        self._curl_session = curl_session

    async def __aenter__(self):
        self._resp = await self._coro
        # Sync _ulp_value from jar after every response so Set-Cookie renewals
        # are immediately reflected in x-app-data on the next request.
        if self._curl_session:
            self._curl_session._update_ulp_from_jar()
            # Capture v-hucode from keep-alive response — browser saves and echoes this back.
            try:
                vh = self._resp.headers.get('v-hucode', '') or self._resp.headers.get('V-Hucode', '')
                if vh and vh != self._curl_session._v_hucode:
                    self._curl_session._v_hucode = vh
                    from core.logger import log_info
                    log_info(f"[HUCODE] v-hucode captured: {vh[:16]}…")
            except Exception:
                pass
            # PS3838 also renews _ulp via response x-app-data header (axios interceptor mechanism).
            # Read it here and inject into jar so the renewed value is used on the next request.
            try:
                xad = self._resp.headers.get('x-app-data', '') or self._resp.headers.get('X-App-Data', '')
                if xad:
                    for part in xad.split(';'):
                        part = part.strip()
                        if part.startswith('_ulp='):
                            new_ulp = part[5:].strip()
                            if new_ulp and new_ulp != self._curl_session._ulp_value:
                                old = self._curl_session._ulp_value[:8] + '…' if self._curl_session._ulp_value else 'none'
                                self._curl_session._ulp_value = new_ulp
                                self._curl_session._session.cookies.set('_ulp', new_ulp, domain='pinnacle888.com')
                                self._curl_session._session.cookies.set('_ulp', new_ulp, domain='pinnacle888.com')
                                from core.logger import log_info
                                log_info(f"[ULP] _ulp RENEWED via response x-app-data header: {old} → {new_ulp[:12]}…")
                            break
            except Exception:
                pass
        return _CurlResponse(self._resp, url=str(self._resp.url) if hasattr(self._resp, 'url') else '')

    async def __aexit__(self, *args):
        pass


# ── Chrome-impersonating session ───────────────────────────────────────────────

class _CurlSession:
    """curl_cffi AsyncSession with aiohttp-compatible interface.

    Impersonates Chrome 120 TLS fingerprint — matches what PS3838 server expects.
    Automatically maintains cookies across requests (same as browser).
    """

    def __init__(self, headers=None):
        self._session = AsyncSession(impersonate="chrome120", proxy=PS_PROXY) if PS_PROXY else AsyncSession(impersonate="chrome120")
        self._base_headers = headers or {}
        self._ulp_value = ''   # cached _ulp for x-app-data header
        self._v_hucode = ''    # from keep-alive response, sent back on every request

    def _resolve_timeout(self, timeout):
        if timeout is None:
            return 15
        if hasattr(timeout, 'total'):
            return timeout.total or 15
        return float(timeout)

    # Static directus CMS token from data.nocache-v2 response (headerBasedEnabled=true)
    _DIRECTUS_TOKEN = 'TwEdnphtyxsfMpXoJkCkWaPsL2KJJ3lo'

    def _update_ulp_from_jar(self):
        """Sync _ulp_value from the live cookie jar after each response.
        PS renews _ulp via Set-Cookie — curl captures it in the jar automatically.
        This keeps _ulp_value current so x-app-data always sends the fresh value,
        and get_cookie_str() saves the correct _ulp to disk (not the stale initial one).
        """
        try:
            new_ulp = self._session.cookies.get('_ulp', domain='pinnacle888.com')
            if new_ulp and new_ulp != self._ulp_value:
                old = self._ulp_value[:8] + '…' if self._ulp_value else 'none'
                self._ulp_value = new_ulp
                from core.logger import log_info
                log_info(f"[ULP] _ulp_value refreshed from jar: {old} → {new_ulp[:8]}…")
        except Exception:
            pass

    def _build_headers(self, url=''):
        headers = dict(self._base_headers)
        if self._ulp_value:
            headers['x-app-data'] = f'_ulp={self._ulp_value};directusToken={self._DIRECTUS_TOKEN}'
        # v-hucode: browser ONLY sends this for betslip/wager URLs, NOT general requests.
        # Sending it everywhere caused session regression (68min → 60min).
        if self._v_hucode and 'betslip' in url.lower():
            headers['v-hucode'] = self._v_hucode
        return headers

    def set_domain(self, host):
        """Set whitelabel domain for cookie scoping.
        Called by PSAuth._login_vodds() / _login_playwright() after login.
        Each provider sets its own domain — cookies always go to the right place."""
        self._wl_host_cached = host.replace("https://", "")

    def _get_wl_host(self):
        """Get whitelabel host from ws_token.json — dynamic, not hardcoded."""
        if not hasattr(self, '_wl_host_cached') or not self._wl_host_cached:
            try:
                with open("data/ws_token.json") as f:
                    self._wl_host_cached = json.load(f)["wl_domain"].replace("https://", "")
            except Exception:
                self._wl_host_cached = None
        return self._wl_host_cached

    def load_cookies(self, cookie_str):
        """Load cookies from 'k=v; k2=v2' string into session jar.
        Also extracts _ulp for the WS URL parameter.
        """
        for part in cookie_str.split(';'):
            part = part.strip()
            if '=' in part:
                k, v = part.split('=', 1)
                k = k.strip()
                v = v.strip()
                self._session.cookies.set(k, v, domain='pinnacle888.com')
                self._session.cookies.set(k, v, domain='pinnacle888.com')
                if k == '_ulp' and v:
                    self._ulp_value = v

    def get_cookie_str(self):
        """Return current cookies as 'k=v; k2=v2' string.

        _ulp is NOT in the cookie jar (excluded to prevent stale _ulp causing 403),
        but it IS included here so the WS URL parameter can extract it. The WS
        URL ?ulp=<value> requires a valid _ulp; HTTP requests do NOT send cookies
        returned here (they send the jar directly), so this is safe.

        Uses get_dict() which curl_cffi implements correctly for domain-scoped cookies.
        When the same name exists on multiple domains, get_dict() picks the right value
        (most specific domain) without raising CookieConflict.
        Falls back to jar iteration if get_dict() fails.
        """
        try:
            d = self._session.cookies.get_dict()
            parts = [f'{k}={v}' for k, v in d.items() if v]
        except Exception:
            # Fallback: jar iteration
            seen = {}
            try:
                for cookie in self._session.cookies.jar:
                    if cookie.name and cookie.value:
                        seen[cookie.name] = cookie.value
            except Exception:
                pass
            parts = [f'{k}={v}' for k, v in seen.items()]
        # Append _ulp_value for WS URL extraction.
        # _ulp_value is kept in sync with the jar via _update_ulp_from_jar(), so this
        # ensures the correct (most recently renewed) _ulp appears last in the string.
        # On reload via load_cookies(), last occurrence wins → renewed value is preserved.
        if self._ulp_value:
            parts.append(f'_ulp={self._ulp_value}')
        return '; '.join(parts)

    def get(self, url, params=None, timeout=None, **kwargs):
        total = self._resolve_timeout(timeout)
        kwargs.pop('ssl', None)
        extra_headers = kwargs.pop('headers', {}) or {}
        merged = {**self._build_headers(url), **extra_headers}
        coro = self._session.get(
            url, params=params,
            headers=merged,
            timeout=total,
            **kwargs
        )
        return _CurlRequestContextManager(coro, self)

    def post(self, url, data=None, json=None, timeout=None, **kwargs):
        total = self._resolve_timeout(timeout)
        kwargs.pop('ssl', None)
        extra_headers = kwargs.pop('headers', {}) or {}
        merged = {**self._build_headers(url), **extra_headers}
        coro = self._session.post(
            url, data=data, json=json,
            headers=merged,
            timeout=total,
            **kwargs
        )
        return _CurlRequestContextManager(coro, self)

    async def close(self):
        await self._session.close()


# ── PS3838Auth ─────────────────────────────────────────────────────────────────

class PS3838Auth:
    """PS3838 auth with Chrome TLS impersonation via curl_cffi.

    Cookie lifecycle:
      1. Load initial cookies from cookie.json into curl session
      2. All HTTP requests use persistent curl session → cookies auto-captured
      3. get_cookie() reads live cookies from session (always fresh)
      4. WS connect uses get_cookie() for _ulp extraction
    """

    def __init__(self):
        self._session = None
        self._cookie_str = ''
        self._load_initial_cookies()

    def _load_initial_cookies(self):
        try:
            with open(COOKIE_FILE) as f:
                data = json.load(f)
            if isinstance(data, list):
                self._cookie_str = '; '.join(f"{c['name']}={c['value']}" for c in data if 'name' in c and 'value' in c)
            else:
                self._cookie_str = data['cookie']
            log_info(f"PS3838 cookies loaded into jar ({len(self._cookie_str.split(';'))} cookies)")
        except Exception as e:
            log_error("ps3838_auth", f"Failed to load cookies: {e}")

    async def init_session(self):
        """Create persistent curl_cffi session. Call once at startup."""
        self._session = _CurlSession(
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": PS_BASE_URL + "/",
            }
        )
        self._session.load_cookies(self._cookie_str)
        ulp_status = "present" if self._session._ulp_value else "MISSING"
        log_info(f"PS3838 persistent session created (curl_cffi Chrome impersonation active, _ulp NOT in jar: {ulp_status})")

    def get_cookie(self) -> str:
        """Return current cookie string from session (always fresh)."""
        if self._session:
            return self._session.get_cookie_str()
        return self._cookie_str

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

    def build_headers(self, method: str = "GET", referer_sport: str = "soccer") -> dict:
        """Build headers matching real Chrome browser for PS3838.
        Call this for EVERY PS3838 request."""
        cookies = self.get_cookies_dict()

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
            "Sec-Ch-Ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Referer": f"https://www.pinnacle888.com/en/sports/{referer_sport}",
            "Priority": "u=1, i",
            "X-Browser-Session-Id": cookies.get("BrowserSessionId", ""),
            "X-Custid": cookies.get("custid", ""),
            "X-Lcu": cookies.get("lcu", ""),
            "X-Slid": cookies.get("SLID", ""),
            "X-U": cookies.get("u", ""),
        }

        if method == "POST":
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Origin"] = "https://www.pinnacle888.com"

        app_keys = [
            'dpJCA', 'pctag', 'directusToken', 'BrowserSessionId',
            'PCTR', '_og', '_ulp', 'custid', '_userDefaultView',
            '__prefs', 'lang'
        ]
        app_parts = [f"{k}={cookies[k]}" for k in app_keys if cookies.get(k)]
        headers["X-App-Data"] = ";".join(app_parts)

        return headers

    async def fetch_token(self) -> str:
        """Fetch WS token. Pure fetch — no recovery logic.

        Checks ws_token.json cache first (< 600s), then REST endpoint.
        Returns token string or None. Does NOT trigger Playwright.
        """
        # Check saved token file first (from Playwright refresh)
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
            log_error("ps3838_auth", "Session not initialized — call init_session() first")
            return None

        params = {
            "locale": "en_US",
            "_": int(time.time() * 1000),
            "withCredentials": "true",
        }
        try:
            hdrs = self.build_headers(method="GET")
            async with self._session.get(PS_TOKEN_URL, params=params, headers=hdrs) as r:
                log_info(f"Token endpoint status={r.status}")
                data = await r.json()
                token = data.get("token") or data.get("wsToken") or data.get("ws_token")
                if token:
                    log_info("[AUTH] PS3838 token OK")
                else:
                    log_error("ps3838_auth", f"Token empty — Response: {str(data)[:100]}")
                return token
        except Exception as e:
            log_error("ps3838_auth", f"Token fetch failed: {e}")
            return None

    def invalidate_token_cache(self):
        """Delete ws_token.json so next fetch_token() hits REST."""
        import os as _os
        try:
            _os.remove(WS_TOKEN_FILE)
            log_info("[AUTH] ws_token.json invalidated — next fetch will hit REST")
        except FileNotFoundError:
            pass

    async def refresh_cookies_via_playwright(self):
        """Run refresh_ps3838.py via Playwright to get fresh cookies."""
        import asyncio as _asyncio
        import os as _os
        try:
            script = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'data', 'refresh_ps3838.py')
            proc = await _asyncio.create_subprocess_exec(
                'python3', script,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=90)
            if proc.returncode == 0:
                self.reload_cookie()
                log_info("[AUTH] Playwright refresh succeeded — cookies reloaded")
            else:
                log_error("ps3838_auth", f"Playwright refresh failed: {stderr.decode()[:200]}")
        except _asyncio.TimeoutError:
            log_error("ps3838_auth", "Playwright refresh timed out after 90s")
        except Exception as e:
            log_error("ps3838_auth", f"Playwright refresh error: {e}")

    def reload_cookie(self):
        """Reload cookies from disk into session (after Chrome refresh)."""
        self._load_initial_cookies()
        if self._session:
            self._session.load_cookies(self._cookie_str)
        # Invalidate cached ws_token.json — it was fetched in the browser's
        # session context and is useless for the bot's WS connection.
        # fetch_token() will hit REST with the new cookies to get a bound token.
        import os as _os
        try:
            _os.remove(WS_TOKEN_FILE)
            log_info("PS3838 cookies reloaded — ws_token.json cleared (will fetch fresh)")
        except FileNotFoundError:
            log_info("PS3838 cookies reloaded into session")

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
