"""
v2perfectbot — core/logger.py
Structured logging: [GATE] [MARKET] action reason

Every log line follows this format for easy grep/analysis:
  [HH:MM:SS] [GATE] [market_label] action | key=value key=value

Gates: EXTRACT, PAIR, LINE, EV, TIMING, PREFIRE, FIRE, EXTEND, WS, SCAN
"""

import os
import sys
import time
import queue
import threading

os.environ['TZ'] = 'Asia/Jakarta'
time.tzset()

# Force line-buffered stdout so Claude Code sees output immediately
sys.stdout.reconfigure(line_buffering=True)

# ── File logging — data/log/bot.log ───────────────────────────────────────────
_LOG_DIR  = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'log')
_LOG_FILE = os.path.join(_LOG_DIR, 'bot.log')
os.makedirs(_LOG_DIR, exist_ok=True)
_log_fh = open(_LOG_FILE, 'a', buffering=1)  # line-buffered
_log_lock = threading.Lock()               # protects _log_fh swaps during rotation
_log_queue = queue.Queue(maxsize=10000)

_MAX_LOG_BYTES = 100 * 1024 * 1024  # 100 MB per file
_MAX_LOG_DAYS  = 7                   # delete rotated files older than this


def _emit(line: str):
    """Queue log line for background write. Non-blocking."""
    try:
        _log_queue.put_nowait(line)
    except queue.Full:
        pass  # drop if full — better than blocking event loop


def _log_writer():
    """Background thread: reads queue, writes to stdout + file."""
    while True:
        try:
            line = _log_queue.get(timeout=1.0)
            formatted = line.rstrip() + '\n'
            try:
                sys.stdout.write(formatted)
            except Exception:
                pass
            try:
                with _log_lock:
                    _log_fh.write(formatted)
            except Exception:
                pass
        except queue.Empty:
            continue
        except Exception:
            pass


threading.Thread(target=_log_writer, daemon=True, name='log_writer').start()


def rotate_if_needed():
    """Rotate bot.log if it exceeds _MAX_LOG_BYTES.

    Renames: bot.log → bot.log.1, .1 → .2, …, .6 → .7
    Deletes rotated files older than _MAX_LOG_DAYS.
    Thread-safe: swaps _log_fh under _log_lock so _log_writer never sees a
    half-swapped handle.
    """
    global _log_fh
    try:
        if not os.path.exists(_LOG_FILE):
            return
        if os.path.getsize(_LOG_FILE) < _MAX_LOG_BYTES:
            return

        # Shift existing rotated files up one slot (oldest first)
        for i in range(6, 0, -1):
            src = f"{_LOG_FILE}.{i}"
            dst = f"{_LOG_FILE}.{i + 1}"
            if os.path.exists(src):
                os.rename(src, dst)

        # Rename current log to .1  (old handle still writes to renamed inode on Unix)
        os.rename(_LOG_FILE, f"{_LOG_FILE}.1")

        # Open fresh file, then swap handle under lock
        new_fh = open(_LOG_FILE, 'a', buffering=1)
        with _log_lock:
            old_fh = _log_fh
            _log_fh = new_fh

        old_fh.close()

        # Purge rotated files older than _MAX_LOG_DAYS
        cutoff = time.time() - _MAX_LOG_DAYS * 86400
        for i in range(1, 10):
            f = f"{_LOG_FILE}.{i}"
            if not os.path.exists(f):
                break
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
            except Exception:
                pass

        log_info(f"[LOGROTATE] Rotated bot.log (exceeded {_MAX_LOG_BYTES // 1024 // 1024}MB)")
    except Exception:
        pass  # never crash the bot over log rotation


def _ts() -> str:
    """Current time as HH:MM:SS."""
    return time.strftime("%H:%M:%S")


def _label(etop=None, fire_key=None) -> str:
    """Build market label from EtopMarket or fire_key string."""
    if fire_key:
        parts = fire_key.split("|")
        if len(parts) >= 3:
            return f"{parts[0]} vs {parts[1]} [{parts[2]}]"
        return fire_key
    if etop is not None:
        t1 = getattr(etop, "team1", "?")
        t2 = getattr(etop, "team2", "?")
        br = getattr(etop, "bracket", "?")
        return f"{t1} vs {t2} [{br}]"
    return "[unknown]"


# ── Gate loggers ───────────────────────────────────────────────────────────────

def log_extract(source: str, msg: str, **kw):
    """[EXTRACT] Data extraction from etopfun or PS3838."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [EXTRACT] [{source}] {msg}  {extra}".rstrip())


def log_pair(team1: str, team2: str, action: str, **kw):
    """[PAIR] Team pair matching results."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [PAIR] [{team1} vs {team2}] {action}  {extra}".rstrip())


def log_line(market_label: str, action: str, **kw):
    """[LINE] PS line lookup results."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [LINE] [{market_label}] {action}  {extra}".rstrip())


def log_ev(market_label: str, ev1: float, ev2: float, **kw):
    """[EV] EV calculation results."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [EV] [{market_label}] ev1={ev1:+.2f}% ev2={ev2:+.2f}%  {extra}".rstrip())


def log_timing(market_label: str, remain: int, status: str, **kw):
    """[TIMING] Countdown and fire zone status."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [TIMING] [{market_label}] {remain}s {status}  {extra}".rstrip())


def log_prefire(market_label: str, action: str, **kw):
    """[PREFIRE] Pre-fire validation checks."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [PREFIRE] [{market_label}] {action}  {extra}".rstrip())


def log_fire(market_label: str, step: int, action: str, **kw):
    """[FIRE] Bet placement and re-check cycle."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [FIRE] [{market_label}] step={step} {action}  {extra}".rstrip())


def log_fire_complete(market_label: str, total_items: int, total_value: float, side: str):
    """[FIRE] Final summary after progressive fire completes."""
    _emit(f"[{_ts()}] [FIRE] [{market_label}] COMPLETE items={total_items} "
          f"value=${total_value:.0f} side={side.upper()}".rstrip())


def log_extend(market_label: str, action: str, **kw):
    """[EXTEND] Extension monitoring after timer=0."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [EXTEND] [{market_label}] {action}  {extra}".rstrip())


def log_ws(component: str, action: str, **kw):
    """[WS] Self-healing: reconnect, refresh, retry."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [WS] [{component}] {action}  {extra}".rstrip())


def log_scan(action: str, **kw):
    """[SCAN] Full market scan cycle."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [SCAN] {action}  {extra}".rstrip())


def log_monitor(market_label: str, remain: int, ev1, ev2, **kw):
    """[MONITOR] Per-market poll status line.

    This is the main status line that replaces the old bot's verbose output.
    Compact single-line format for each tracked market.
    """
    ev1_str = f"{ev1:+.2f}%" if ev1 is not None else "N/A"
    ev2_str = f"{ev2:+.2f}%" if ev2 is not None else "N/A"
    extra = "  ".join(f"{k}={v}" for k, v in kw.items())
    _emit(f"[{_ts()}] [MONITOR] [{market_label}] {remain}s "
          f"EV={ev1_str}/{ev2_str}  {extra}".rstrip())


# ── Utility ────────────────────────────────────────────────────────────────────

def log_info(msg: str):
    """General info message."""
    _emit(f"[{_ts()}] [INFO] {msg}".rstrip())


def log_error(component: str, msg: str, error=None):
    """Error with optional exception."""
    err_str = f" | {error}" if error else ""
    _emit(f"[{_ts()}] [ERROR] [{component}] {msg}{err_str}".rstrip())


def log_warn(component: str, msg: str):
    """Warning."""
    _emit(f"[{_ts()}] [WARN] [{component}] {msg}".rstrip())


def log_market(team1: str, team2: str, market: str, map_num: int, status: str, **kw):
    """Unified market lifecycle log. One tag for everything."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items() if v is not None)
    _emit(f"[{_ts()}] [MARKET] {team1} vs {team2} | {market} | m{map_num} | {status}  {extra}".rstrip())

def log_market_unmatched(team1: str, team2: str, **kw):
    """Parent-level unmatched log."""
    extra = "  ".join(f"{k}={v}" for k, v in kw.items() if v is not None)
    _emit(f"[{_ts()}] [MARKET] {team1} vs {team2} | UNMATCHED  {extra}".rstrip())


# ── API Call Tracker ──────────────────────────────────────────────────────
# Not a gate. Not a limiter. Just structured logging for external calls.
# Every API call goes through track_api(). Panel reads the summary.
# When things degrade, you SEE it before it becomes a crisis.

import time as _time
from collections import defaultdict

class APITracker:
    """Structured API call logging. Read-only observability."""

    def __init__(self):
        self._calls = defaultdict(list)   # service → [{ts, endpoint, status, ms, error}]
        self._alerts = []                  # recent alerts
        self.MAX_HISTORY = 500             # per service

    def track(self, service: str, endpoint: str, status: int = 200,
              ms: float = 0, error: str = None):
        """Log an API call. Call this AFTER every external request.

        Args:
            service: 'ps3838', 'etop_listing', 'etop_detail', 'etop_bet'
            endpoint: URL path or description
            status: HTTP status code (0 if connection failed)
            ms: response time in milliseconds
            error: error message if failed

        Usage:
            t0 = time.time()
            resp = await session.get(url)
            api_tracker.track('ps3838', '/keep-alive',
                            status=resp.status,
                            ms=(time.time()-t0)*1000)
        """
        entry = {
            'ts': _time.time(),
            'endpoint': endpoint,
            'status': status,
            'ms': round(ms, 1),
            'error': error,
        }
        self._calls[service].append(entry)

        # Trim history
        if len(self._calls[service]) > self.MAX_HISTORY:
            self._calls[service] = self._calls[service][-self.MAX_HISTORY:]

        # Auto-detect degradation
        self._check_health(service)

    def _check_health(self, service: str):
        """Detect degradation patterns. Log warnings automatically."""
        recent = self._last_n(service, 10)
        if len(recent) < 5:
            return

        # High error rate
        errors = sum(1 for c in recent if c['status'] >= 400 or c['error'])
        if errors >= 3:
            msg = f"[API_HEALTH] {service}: {errors}/{len(recent)} recent calls failed"
            if not self._recent_alert(service, 'error_rate'):
                log_warn("api_health", msg)
                self._alerts.append({
                    'ts': _time.time(), 'service': service,
                    'type': 'error_rate', 'msg': msg
                })

        # Slow responses
        avg_ms = sum(c['ms'] for c in recent) / len(recent)
        if avg_ms > 5000:
            msg = f"[API_HEALTH] {service}: avg response {avg_ms:.0f}ms (stuck?)"
            if not self._recent_alert(service, 'slow'):
                log_warn("api_health", msg)
                self._alerts.append({
                    'ts': _time.time(), 'service': service,
                    'type': 'slow', 'msg': msg
                })

        # Rapid fire detection (too many calls)
        last_min = [c for c in self._calls[service] if _time.time() - c['ts'] < 60]
        if len(last_min) > 50:
            msg = f"[API_HEALTH] {service}: {len(last_min)} calls/min (spam?)"
            if not self._recent_alert(service, 'spam'):
                log_warn("api_health", msg)
                self._alerts.append({
                    'ts': _time.time(), 'service': service,
                    'type': 'spam', 'msg': msg
                })

    def _recent_alert(self, service: str, alert_type: str) -> bool:
        """Was this alert already raised in last 60s?"""
        cutoff = _time.time() - 60
        return any(a for a in self._alerts
                   if a['service'] == service and a['type'] == alert_type
                   and a['ts'] > cutoff)

    def _last_n(self, service: str, n: int) -> list:
        """Last N calls for a service."""
        return self._calls[service][-n:]

    # ── Read methods (for panel + commands) ───────────────────────

    def summary(self) -> dict:
        """Full summary for panel display."""
        result = {}
        now = _time.time()

        for service, calls in self._calls.items():
            last_min = [c for c in calls if now - c['ts'] < 60]
            last_5min = [c for c in calls if now - c['ts'] < 300]

            errors_1m = sum(1 for c in last_min if c['status'] >= 400 or c['error'])
            errors_5m = sum(1 for c in last_5min if c['status'] >= 400 or c['error'])

            avg_ms = (sum(c['ms'] for c in last_min) / len(last_min)) if last_min else 0

            result[service] = {
                'per_min': len(last_min),
                'per_5min': len(last_5min),
                'total': len(calls),
                'avg_ms': round(avg_ms),
                'errors_1m': errors_1m,
                'errors_5m': errors_5m,
                'last_status': calls[-1]['status'] if calls else None,
                'last_endpoint': calls[-1]['endpoint'] if calls else None,
            }

        return result

    def service_detail(self, service: str, last_n: int = 20) -> list:
        """Last N calls for a specific service. For debugging."""
        return self._last_n(service, last_n)

    def get_alerts(self, last_n: int = 10) -> list:
        """Recent health alerts."""
        return self._alerts[-last_n:]


# Global instance — import and use everywhere
api_tracker = APITracker()
