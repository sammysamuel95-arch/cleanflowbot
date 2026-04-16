"""
core/notifier.py — Telegram alerts for critical bot events.

Alerts on:
  - Bot crash / restart
  - WS disconnect / reconnect
  - Fire placed
  - Account banned (pin/login error)
  - Token refresh failed

Usage:
    from core.notifier import notify
    await notify("message")
"""
import asyncio
import time
from core.logger import log_info, log_warn

TELEGRAM_TOKEN = "8205194596:AAFTDOctSeqxsvGplSV0UwbdKiVIvgGLVe0"
TELEGRAM_CHAT  = "8774309371"
TELEGRAM_URL   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# Debounce: don't spam same message within N seconds
_last_sent: dict = {}
DEBOUNCE_SECS = 60


async def notify(msg: str, debounce_key: str = None, parse_mode: str = None):
    """Send Telegram alert. Non-blocking, swallows errors."""
    key = debounce_key or msg[:80]
    now = time.time()
    if now - _last_sent.get(key, 0) < DEBOUNCE_SECS:
        return
    _last_sent[key] = now

    try:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession() as s:
            payload = {"chat_id": TELEGRAM_CHAT, "text": msg}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            r = await s.post(TELEGRAM_URL, json=payload, timeout=10)
            if r.status_code != 200:
                log_warn("NOTIFY", f"Telegram failed: {r.status_code} {r.text[:100]}")
    except Exception as e:
        log_warn("NOTIFY", f"Telegram error: {e}")


def notify_sync(msg: str, debounce_key: str = None):
    """Sync wrapper for use in non-async contexts."""
    try:
        asyncio.get_event_loop().run_until_complete(notify(msg, debounce_key))
    except Exception:
        pass
