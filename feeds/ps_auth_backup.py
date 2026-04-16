"""
feeds/ps_auth.py — Unified PS odds source auth

Wraps vodds_auth / ps3838_auth under a single interface.
Provider is selected at construction time from bot_config.json PS_PROVIDER.

Usage:
    ps_auth = PSAuth(provider="vodds")   # or "ps3838" / "pinnacle888"
    await ps_auth.init_session()
    token = await ps_auth.fetch_token()
    cookie = ps_auth.get_cookie()
    ...
"""

from core.logger import log_info


class PSAuth:
    """Unified auth facade — routes all calls to the active provider."""

    SUPPORTED = {"vodds", "ps3838", "pinnacle888"}

    def __init__(self, provider: str = "vodds"):
        provider = provider.lower()
        if provider not in self.SUPPORTED:
            raise ValueError(f"PSAuth: unknown provider '{provider}'. Supported: {self.SUPPORTED}")

        self._provider = provider
        self._impl = self._make_impl(provider)
        log_info(f"[PSAuth] provider={provider}")

    def _make_impl(self, provider: str):
        if provider == "vodds":
            from feeds.vodds_auth import VoddsAuth
            return VoddsAuth()
        elif provider in ("ps3838", "pinnacle888"):
            from feeds.ps3838_auth import PS3838Auth
            return PS3838Auth()
        raise ValueError(f"No impl for provider '{provider}'")

    @property
    def provider(self) -> str:
        return self._provider

    # ── Delegate everything to impl ───────────────────────────────────

    async def init_session(self):
        await self._impl.init_session()
        # For vodds: _ps_base is dynamic — patch config so WS feed uses the right URL
        if self._provider == "vodds" and hasattr(self._impl, '_ps_base') and self._impl._ps_base:
            import config
            ps_base = self._impl._ps_base   # e.g. https://uyfnltp.lenvora8.com
            ws_url = ps_base.replace("https://", "wss://") + "/sports-websocket/ws"
            config.PS_WS_URL = ws_url
            config.PS_BASE_URL = ps_base
            log_info(f"[PSAuth] Patched config — PS_BASE_URL={ps_base}  PS_WS_URL={ws_url}")

    async def fetch_token(self):
        return await self._impl.fetch_token()

    def invalidate_token_cache(self):
        self._impl.invalidate_token_cache()

    def get_cookie(self) -> str:
        return self._impl.get_cookie()

    def get_ulp(self) -> str:
        return self._impl.get_ulp()

    def build_headers(self, method: str = "GET") -> dict:
        return self._impl.build_headers(method=method)

    async def keep_alive(self):
        await self._impl.keep_alive()

    def save_cookies_to_disk(self):
        self._impl.save_cookies_to_disk()

    def reload_cookie(self):
        self._impl.reload_cookie()

    async def refresh_cookies_via_playwright(self):
        await self._impl.refresh_cookies_via_playwright()

    async def close(self):
        await self._impl.close()

    # ── Pass-through attribute access for raw session use (e.g. _session) ────────

    def __getattr__(self, name):
        # Only called when attribute not found on PSAuth itself
        return getattr(self._impl, name)
