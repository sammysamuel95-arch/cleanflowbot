"""
core/session_tracker.py — Session uptime diagnostics.

Purely additive. Never affects bot operation.
Every external call is wrapped in try/except at the call site.
"""

import time
import json
import os

_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'session_tracker.json')


class SessionTracker:
    def __init__(self):
        self.bot_start = time.time()
        self.ws_connects = []       # [(timestamp, ...)]
        self.ws_disconnects = []    # [(timestamp, reason)]
        self.cookie_refreshes = []  # [timestamp]
        self._ws_up_since = 0       # timestamp when WS last connected
        self._ws_total_uptime = 0   # accumulated WS uptime seconds
        self._load()

    def _load(self):
        """Load previous session stats if available."""
        try:
            if os.path.exists(_STATE_PATH):
                with open(_STATE_PATH) as f:
                    d = json.load(f)
                self.cookie_refreshes = d.get('cookie_refreshes', [])[-50:]
        except Exception:
            pass

    def _flush(self):
        """Best-effort write to disk."""
        try:
            d = {
                'bot_start': self.bot_start,
                'ws_connects': len(self.ws_connects),
                'ws_disconnects': len(self.ws_disconnects),
                'cookie_refreshes': self.cookie_refreshes[-50:],
                'last_flush': time.time(),
            }
            with open(_STATE_PATH, 'w') as f:
                json.dump(d, f, indent=2)
        except Exception:
            pass

    def on_ws_connect(self):
        """Call when WS connects successfully."""
        now = time.time()
        self.ws_connects.append(now)
        self._ws_up_since = now
        self._flush()

    def on_ws_disconnect(self, reason=''):
        """Call when WS disconnects."""
        now = time.time()
        self.ws_disconnects.append((now, reason))
        if self._ws_up_since > 0:
            self._ws_total_uptime += now - self._ws_up_since
            self._ws_up_since = 0
        self._flush()

    def on_cookie_refresh(self):
        """Call when Playwright refreshes cookies."""
        self.cookie_refreshes.append(time.time())
        self._flush()

    def summary(self) -> dict:
        """Full summary for panel/command display."""
        now = time.time()
        bot_uptime = now - self.bot_start

        # WS uptime
        ws_uptime = self._ws_total_uptime
        if self._ws_up_since > 0:
            ws_uptime += now - self._ws_up_since
        ws_pct = (ws_uptime / bot_uptime * 100) if bot_uptime > 0 else 0

        # Last WS drop
        last_drop = None
        last_drop_ago = None
        if self.ws_disconnects:
            last_drop = self.ws_disconnects[-1][0]
            last_drop_ago = now - last_drop

        # Cookie age
        last_cookie = self.cookie_refreshes[-1] if self.cookie_refreshes else self.bot_start
        cookie_age = now - last_cookie

        return {
            'bot_uptime_s': int(bot_uptime),
            'bot_uptime': _fmt_duration(bot_uptime),
            'ws_uptime': _fmt_duration(ws_uptime),
            'ws_pct': round(ws_pct, 1),
            'ws_reconnects': len(self.ws_connects) - 1 if self.ws_connects else 0,
            'last_drop_ago': _fmt_duration(last_drop_ago) if last_drop_ago else 'never',
            'last_drop_reason': self.ws_disconnects[-1][1] if self.ws_disconnects else '',
            'cookie_age': _fmt_duration(cookie_age),
            'cookie_refreshes': len(self.cookie_refreshes),
            'ws_connected': self._ws_up_since > 0,
        }

    def summary_str(self) -> str:
        """Formatted string for command output."""
        s = self.summary()
        lines = [
            f"Bot uptime:  {s['bot_uptime']}",
            f"WS uptime:   {s['ws_uptime']} ({s['ws_pct']}%) — {s['ws_reconnects']} reconnects",
            f"WS status:   {'CONNECTED' if s['ws_connected'] else 'DISCONNECTED'}",
            f"Last drop:   {s['last_drop_ago']} ago" + (f" ({s['last_drop_reason']})" if s['last_drop_reason'] else ''),
            f"Cookie age:  {s['cookie_age']} ({s['cookie_refreshes']} refreshes)",
        ]
        return '\n'.join(lines)


def _fmt_duration(secs) -> str:
    """Format seconds to human string."""
    if secs is None:
        return 'N/A'
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    h = secs // 3600
    m = (secs % 3600) // 60
    return f"{h}h {m}m"
