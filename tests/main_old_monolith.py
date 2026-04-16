import os, time as _tz_time; os.environ['TZ'] = 'Asia/Jakarta'; _tz_time.tzset()
"""
CleanFlowBot — Unified main loop.

ONE loop does everything:
  1. FETCH: Get etop listing (one call, shared by all steps)
  2. DISCOVER: For new matches, search PS for lines
  3. LIFECYCLE: Remove dead markets, release resources
  4. MONITOR: Calculate EV for each tracked market
  5. FIRE: If gates pass, add to fire queue
  6. REFRESH: Update stale PS data
  7. SLEEP: Adaptive based on closest market

No separate scan loop. No separate monitor loop. No fighting.
"""

import asyncio
import time
import aiohttp
import traceback
import os
import random

import config
from config import (
    PS_BASE_URL, ETOP_BASE_URL, SP_ESPORTS,
)
from core.logger import log_info, log_warn, log_error, log_market
from core.ev import compute_ev
from core.auth.ps3838_auth import PS3838Auth
from feeds.etopfun_api import EtopfunAPI
from feeds.ps3838_ws import Pinnacle888LiveFeed as PS3838LiveFeed
from feeds.ps3838_rest import search_by_teams, search_event, fetch_lines_for_eid, system_status
from engine.inventory import InventoryManager
from engine.strategy import Strategy
from engine.fire_zone import FireZone
from matching.pair import infer_sport_hint
from matching.classify import classify_etop_sub
from matching.line_new import find_line
from core.models import EtopMarket
from matching.evidence import find_best_match as evidence_match
from matching.alias_db import AliasDB as EvidenceAliasDB
from core.commands import CommandHandler
from core.pool_estimator import PoolEstimator


# Tracks consecutive EID conflict blocks per (match_key, eid) to suppress log spam
_eid_block_counts: dict = {}
# Tracks total blocks per match_key to skip evidence_match entirely after threshold
_match_block_counts: dict = {}


# ── Shared Etop State ──────────────────────────────────────────────────────────

class EtopState:
    """Shared etop listing data. Event-driven notification.

    Fetcher writes → sets events → brain + fire wake instantly.
    Zero polling. Data-driven.
    """
    def __init__(self):
        self.parents = []
        self.listing = {}
        self.last_fetch_at = 0
        self.fetch_count = 0
        # Separate events so brain and fire don't steal each other's wake
        self.brain_event = asyncio.Event()
        self.fire_event = asyncio.Event()

    @property
    def age(self) -> float:
        return time.time() - self.last_fetch_at if self.last_fetch_at > 0 else 9999

    def update(self, parents, listing):
        """Called by etop fetcher. Wakes brain + fire instantly."""
        self.parents = parents
        self.listing = listing
        self.last_fetch_at = time.time()
        self.fetch_count += 1
        self.brain_event.set()
        self.fire_event.set()


# ── Process Safety ─────────────────────────────────────────────────────────────

import signal
import atexit

_PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'bot.pid')
_SHUTDOWN = False  # set True by signal handler, checked by main loop


def _acquire_lock():
    """PID lockfile — prevents double instances.

    On startup:
      1. If pidfile exists and process alive → KILL old process and take over
      2. If pidfile exists but process dead → stale lock, overwrite
      3. Write our PID → we own the lock
    """
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            # Check if old process is alive
            os.kill(old_pid, 0)  # signal 0 = existence check
            # Process alive — kill it and take over
            log_warn("LOCK", f"Old bot still running (PID {old_pid}) — killing it...")
            print(f"⚠ Killing old bot (PID {old_pid})...")
            os.kill(old_pid, signal.SIGTERM)  # graceful first
            import time as _t
            _t.sleep(3)
            try:
                os.kill(old_pid, 0)  # still alive?
                os.kill(old_pid, signal.SIGKILL)  # force kill
                _t.sleep(1)
                log_warn("LOCK", f"Old bot (PID {old_pid}) force-killed")
            except ProcessLookupError:
                log_info(f"[LOCK] Old bot (PID {old_pid}) exited gracefully")
        except ProcessLookupError:
            # Process dead — stale lockfile
            log_warn("LOCK", f"Stale lockfile found (PID {old_pid} dead) — overwriting")
        except ValueError:
            # Corrupt pidfile
            log_warn("LOCK", "Corrupt pidfile — overwriting")

    # Write our PID
    os.makedirs(os.path.dirname(_PID_FILE), exist_ok=True)
    with open(_PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    log_info(f"[LOCK] PID {os.getpid()} — lockfile acquired")


def _release_lock():
    """Remove pidfile on exit."""
    try:
        if os.path.exists(_PID_FILE):
            with open(_PID_FILE) as f:
                pid = int(f.read().strip())
            if pid == os.getpid():  # only remove if it's ours
                os.remove(_PID_FILE)
                log_info(f"[LOCK] PID {os.getpid()} — lockfile released")
    except Exception:
        pass


def _cleanup_orphans():
    """Kill lingering chromium processes from previous Playwright crashes."""
    import subprocess as _sp
    try:
        result = _sp.run(['pkill', '-f', 'chromium.*ps3838'], capture_output=True, text=True)
        if result.returncode == 0:
            log_warn("CLEANUP", "Killed orphan chromium processes from previous run")
    except Exception:
        pass  # pkill not available or no matches — fine


def _signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    global _SHUTDOWN
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
    log_warn("SHUTDOWN", f"Received {sig_name} — initiating graceful shutdown...")
    _SHUTDOWN = True


# Register signal handlers
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)
# Register atexit cleanup
atexit.register(_release_lock)


# Etop sub-match type → human label (for UNMATCHED panel display)
_UM_TYPE_LABELS = {
    2: 'Game Winner', 3: 'F10K', 5: 'O/U',
    6: 'HDP', 8: 'Game Winner', 9: 'O/U',
    11: '5Kills', 12: 'HDP', 13: 'O/U',
}

# ── Market Lifecycle States ──────────────────────────────────────────────────
# DISCOVERED: Found on etop, PS lookup pending or done
# MONITOR:    Paired with PS, EV being tracked, remain > PRELOAD_SECS
# PREFIRE:    remain <= PRELOAD_SECS, inventory loading
# FIRE_ZONE:  remain <= TRIGGER_SECS, ready to fire
# FIRED:      Fire command sent, waiting for confirmation
# DONE:       Market expired or disappeared from etop
# Each state transition happens in ONE place in the unified loop.


class TrackedMarket:
    """Single source of truth for a market's state."""
    __slots__ = (
        'fire_key', 'etop_market', 'ps_event_id', 'state',
        'last_seen', 'death_timer', 'locked_at', 'dead_at',
        'hint', 'cat_type',
    )

    def __init__(self, etop_market, ps_event_id=None, hint='', cat_type=''):
        self.fire_key = etop_market.fire_key
        self.etop_market = etop_market
        self.ps_event_id = ps_event_id
        self.state = 'UNMATCHED' if ps_event_id is None else 'MATCHED'
        self.last_seen = time.time()
        self.death_timer = None
        self.locked_at = 0
        self.dead_at = 0
        self.hint = hint
        self.cat_type = cat_type


async def main():
    """Entry point. Setup → unified loop."""

    # ── 0. Process safety ─────────────────────────────────────────────────
    _acquire_lock()
    _cleanup_orphans()

    # ── 1. Auth & Sessions ───────────────────────────────────────────────────
    ps_auth = PS3838Auth()
    await ps_auth.init_session()
    # Load v-hucode for all-odds-selections (static per browser, from cookie.json)
    try:
        import json as _json_startup
        cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'cookie.json')
        with open(cookie_path) as _f:
            _cookie_data = _json_startup.load(_f)
        _saved_vh = _cookie_data.get('v_hucode')
        if _saved_vh:
            ps_auth.v_hucode = _saved_vh
            log_info(f"[AUTH] v-hucode: {ps_auth.v_hucode[:8]}... (from cookie.json)")
        else:
            ps_auth.v_hucode = '950f80013a300a24c8032e374a27995f'
            log_warn("AUTH", "[AUTH] v-hucode not in cookie.json — run refresh_ps3838.py")
    except Exception as e:
        ps_auth.v_hucode = '950f80013a300a24c8032e374a27995f'
        log_warn("AUTH", f"[AUTH] v-hucode load failed: {e} — run refresh_ps3838.py")
    # Test session: fetch token as real auth test
    cookie_str = ps_auth.get_cookie()
    has_ulp = '_ulp=' in cookie_str
    if has_ulp:
        log_info("[AUTH] _ulp present — testing token...")
    else:
        log_warn("AUTH", "_ulp MISSING — attempting fresh login")

    token = await ps_auth.fetch_token()
    if token:
        log_info("[AUTH] Token valid — session alive")
    else:
        log_warn("AUTH", "Token failed — running Playwright for fresh cookies (ONE TIME)")
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", os.path.join("data", "refresh_ps3838.py"),
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
            if proc.returncode == 0:
                ps_auth.reload_cookie()
                log_info("[AUTH] Fresh cookies loaded — retesting token")
                token = await ps_auth.fetch_token()
                if token:
                    log_info("[AUTH] Token valid after refresh — session alive")
                else:
                    log_error("AUTH", "Token still failed — bot runs REST-only")
            else:
                log_error("AUTH", f"Playwright failed: {stderr.decode()[:200]}")
        except asyncio.TimeoutError:
            log_error("AUTH", "Playwright timed out after 90s")
        except Exception as e:
            log_error("AUTH", f"Playwright error: {e}")
    from feeds.etopfun_api import load_etop_cookies, create_etop_session, build_etop_headers
    etop_cookies = load_etop_cookies()
    etop_session = create_etop_session(etop_cookies)
    etop_headers = build_etop_headers()
    etop_api = EtopfunAPI(etop_session, etop_headers)
    if 'DJSP_UUID' in etop_cookies:
        etop_api.set_uuid(etop_cookies['DJSP_UUID'])
    etop_state = EtopState()
    pool_estimator = PoolEstimator()
    try:
        await asyncio.wait_for(pool_estimator.load_exchange_db(etop_api), timeout=15)
    except asyncio.TimeoutError:
        log_warn("STARTUP", "load_exchange_db timed out — starting without exchange data")
    live_feed = PS3838LiveFeed(
        token_fetcher=ps_auth.fetch_token,
        cookie_getter=ps_auth.get_cookie,
    )

    async def _ws_cookie_refresh():
        ps_auth.reload_cookie()

    live_feed._cookie_refresher = _ws_cookie_refresh
    inventory = InventoryManager()
    # Load strategy config from bot_config.json
    _strategy_config = {}
    try:
        import json as _json_cfg
        _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'bot_config.json')
        with open(_cfg_path) as _f:
            _strategy_config = _json_cfg.load(_f)
    except Exception:
        pass
    strategy = Strategy(_strategy_config)
    fire_zone = FireZone(strategy, etop_api, ps_auth, inventory)
    log_info(f"[STRATEGY] min_ev={strategy.min_ev}% trigger={strategy.trigger_secs}s max_items={strategy.max_items}")
    evidence_db = EvidenceAliasDB()
    evidence_db.load_all_seeds()
    log_info(f"[ALIAS_DB] {evidence_db.get_stats()['total']} aliases loaded")

    # ── 2. Start background services ────────────────────────────────────────
    # Session tracker (must be created before live_feed.start so WS connect hook fires)
    from core.session_tracker import SessionTracker
    session_tracker = SessionTracker()
    live_feed._session_tracker = session_tracker

    # WS is OPTIONAL — connects in background, feeds live updates as bonus
    await live_feed.start()  # non-blocking, doesn't wait for WS
    # fire_zone runs inline — no background task

    # Session keepalive (proven immortal)
    asyncio.create_task(_ps_session_manager(ps_auth, live_feed, session_tracker))
    asyncio.create_task(_human_browse(ps_auth, live_feed))
    asyncio.create_task(_etop_keepalive(etop_api))
    asyncio.create_task(_etop_poller(etop_api, etop_state))
    log_info("Session poll + human browse + etop keepalive started (immortal mode)")

    log_info("CleanFlowBot started. Etop drives. PS serves.")

    # ── Command handler (panel IPC) ──────────────────────────────────────────
    # Build a minimal bot proxy so CommandHandler can reach live state
    class _BotProxy:
        pass
    bot_proxy = _BotProxy()
    bot_proxy.markets = {}          # filled each cycle by reference below
    bot_proxy.inventory = inventory
    bot_proxy.fire_zone = fire_zone
    bot_proxy.strategy = strategy
    bot_proxy.ps_store = live_feed.store if hasattr(live_feed, 'store') else {}
    bot_proxy.ps_auth = ps_auth
    bot_proxy.etop_api = etop_api
    bot_proxy.live_feed = live_feed
    bot_proxy.etop_state = etop_state
    bot_proxy.session_tracker = session_tracker
    bot_proxy._start_time = time.time()
    cmd_handler = CommandHandler(bot_proxy)
    asyncio.create_task(_command_poll(cmd_handler))

    # ── 3. Shared state ──────────────────────────────────────────────────
    markets = {}
    _search_tried = {}
    _rest_fetch_ts = {}
    bot_proxy.markets = markets

    # ── 4. Start independent tasks (event-driven) ────────────────────────
    log_info("[MAIN] Starting 5 tasks: brain(event) + fire(event) + tuhao(5s) + discovery(10s) + fetcher(3s)")

    async def _shutdown_waiter():
        while not _SHUTDOWN:
            await asyncio.sleep(1)
        log_warn("SHUTDOWN", "Graceful shutdown — closing connections...")
        try:
            await live_feed.close() if hasattr(live_feed, 'close') else None
            await ps_auth.close()
        except Exception:
            pass
        log_warn("SHUTDOWN", "Shutdown complete.")

    await asyncio.gather(
        _brain_loop(
            etop_state, live_feed, fire_zone, inventory, evidence_db,
            markets, _rest_fetch_ts, etop_api, ps_auth, session_tracker,
        ),
        _fire_loop(
            etop_state, live_feed, fire_zone, inventory, markets,
        ),
        _tuhao_loop(markets, fire_zone, pool_estimator, etop_api, etop_state),
        _discovery_loop(
            etop_state, live_feed, evidence_db, markets,
            _search_tried, ps_auth, etop_api,
        ),
        _shutdown_waiter(),
        return_exceptions=True,
    )


async def _command_poll(cmd_handler):
    """Poll data/cmd_in.json for panel commands. Write result to cmd_out.json."""
    import json as _json
    import os as _os
    data_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'data')
    cmd_in = _os.path.join(data_dir, 'cmd_in.json')
    cmd_out = _os.path.join(data_dir, 'cmd_out.json')
    cmd_log = _os.path.join(data_dir, 'cmd_log.json')
    last_ts = None

    while True:
        try:
            if _os.path.exists(cmd_in):
                with open(cmd_in) as f:
                    req = _json.load(f)
                ts = req.get('ts')
                if ts != last_ts:
                    last_ts = ts
                    result = await cmd_handler.execute(req.get('cmd', ''))
                    result['ts'] = ts
                    with open(cmd_out, 'w') as f:
                        _json.dump(result, f)
                    # Append to log
                    from core.commands import get_command_log
                    with open(cmd_log, 'w') as f:
                        _json.dump(get_command_log(), f)
        except Exception as e:
            log_warn("cmd_poll", f"Error: {e}")
        await asyncio.sleep(0.2)


# ── Session immortality ───────────────────────────────────────────────────

_HUMAN_BROWSE_URLS = [
    f"{PS_BASE_URL}/en/sports/soccer",
    f"{PS_BASE_URL}/en/sports/esports",
    f"{PS_BASE_URL}/en/sports/basketball",
]


async def _human_browse(ps_auth, live_feed=None):
    """Passive browsing sim: visit pages every 8-20min. Purely cosmetic."""
    await asyncio.sleep(180)
    log_info("[BROWSE] Human sim started")
    while True:
        if live_feed and not live_feed.session_alive:
            await asyncio.sleep(30)
            continue
        try:
            url = random.choice(_HUMAN_BROWSE_URLS)
            hdrs = ps_auth.build_headers(method="GET")
            async with ps_auth._session.get(url, headers=hdrs, timeout=15) as resp:
                await resp.read()
                log_info(f"[BROWSE] Visited {url.split('/')[-1]} ({resp.status})")
        except Exception as e:
            log_warn("browse", f"Browse failed: {e}")
        await asyncio.sleep(random.randint(480, 1200))


async def _etop_poller(etop_api, etop_state):
    """Independent etop polling — never blocked by PS REST.

    Polls etop match_list every 3s. Writes to shared EtopState.
    Unified loop reads from EtopState — always fresh odds.
    """
    await asyncio.sleep(5)  # let bot stabilize first
    while True:
        try:
            parents, lookup = await asyncio.wait_for(etop_api.match_list(), timeout=10)
            if parents:
                etop_state.update(parents, lookup)
                if etop_state.fetch_count % 100 == 1:
                    log_info(f"[ETOP_POLL] {len(parents)} parents, {len(lookup)} subs, age={etop_state.age:.1f}s")
        except asyncio.TimeoutError:
            log_warn("ETOP_POLL", "match_list() timed out after 10s — skipping cycle")
        except Exception as e:
            log_warn("ETOP_POLL", f"Fetch failed: {e}")
        await asyncio.sleep(3)


async def _etop_keepalive(etop_api):
    """Ping etopfun /api/userconn/check.do every 5 min to keep session alive."""
    await asyncio.sleep(60)
    while True:
        try:
            ok = await etop_api.userconn_check()
            if not ok:
                log_warn("[ETOP] keepalive returned non-0 — session may be dead")
        except Exception as e:
            log_warn(f"[ETOP] keepalive error: {e}")
        await asyncio.sleep(300)  # every 5 min


async def _ps_session_manager(ps_auth, live_feed, session_tracker=None):
    """Unified PS session manager — SOLE owner of all recovery.

    Normal mode (session_alive=True):
        Every 10s: keep_alive + account_balance
        3 consecutive fails → session_alive=False

    Recovery mode (session_alive=False):
        L1 RETRY:      3 keepalive tests (~30s) — diagnoses failure type
        L2 RELOAD:     reload cookies from disk once
        L3 PLAYWRIGHT: browser cookie regrab, 3 cycles of progressive cooldowns
                       keeps testing keepalive during cooldown waits

    Playwright cooldowns (minutes): 0, 3, 5, 8, 12, 18, 24 × 3 cycles then HARD STOP.
    """
    fail_count = 0
    MAX_FAILS = 3

    # ── Recovery state ────────────────────────────────────
    L1_MAX = 3                          # 3 retries (~30s) — also collects failure types
    recovery_fails = 0                  # L1 counter
    auth_fail_count = 0                 # how many of L1 retries were auth (403/401)
    disk_reloaded = False               # L2 — only once per recovery episode

    # L3 Playwright: 3 cycles of [0, 3, 5, 8, 12, 18, 24] minutes
    PW_COOLDOWNS_MIN = [0, 3, 5, 8, 12, 18, 24]  # minutes
    PW_MAX_CYCLES = 3
    pw_cycle = 0                        # which cycle (0, 1, 2)
    pw_idx = 0                          # index within current cycle
    last_pw_at = 0                      # timestamp of last Playwright run

    # Sustained restore tracking (don't wipe PW progress on flaky restore)
    restored_at = 0

    await asyncio.sleep(30)

    async def _test_keepalive():
        """Test PS session health. Returns (ok, reason).
        reason: 'ok', 'auth' (401/403), 'network' (timeout/conn), 'server' (5xx), 'bad_body' (200 but garbage)
        """
        try:
            hdrs = ps_auth.build_headers(method="GET")
            ts = int(time.time() * 1000)
            ka_url = f"{PS_BASE_URL}/member-auth/v2/keep-alive?locale=en_US&_={ts}&withCredentials=true"
            async with ps_auth._session.get(ka_url, headers=hdrs, timeout=10) as resp:
                body = await resp.read()
                if resp.status == 200:
                    if body and body.strip()[:1] == b'<'[0:1]:
                        return (False, 'bad_body')
                    return (True, 'ok')
                elif resp.status in (401, 403):
                    return (False, 'auth')
                else:
                    return (False, 'server')
        except (asyncio.TimeoutError, OSError, ConnectionError) as e:
            log_warn("SESSION", f"Keepalive network error: {e}")
            return (False, 'network')
        except Exception as e:
            log_warn("SESSION", f"Keepalive unexpected error: {e}")
            return (False, 'network')

    def _reset_recovery():
        nonlocal recovery_fails, auth_fail_count, disk_reloaded
        recovery_fails = 0
        auth_fail_count = 0
        disk_reloaded = False

    def _full_reset():
        nonlocal fail_count, pw_cycle, pw_idx, last_pw_at, restored_at
        _reset_recovery()
        fail_count = 0
        pw_cycle = 0
        pw_idx = 0
        last_pw_at = 0
        restored_at = 0

    def _restore_session(layer_label):
        nonlocal restored_at
        live_feed.session_alive = True
        _reset_recovery()
        restored_at = time.time()
        ps_auth.invalidate_token_cache()
        log_info(f"[SESSION] SESSION RESTORED ({layer_label})")

    async def _run_playwright():
        try:
            proc = await asyncio.create_subprocess_exec(
                'python3', 'data/refresh_ps3838.py',
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
            if proc.returncode == 0:
                log_info("[SESSION] Playwright refresh completed")
                if session_tracker:
                    try:
                        session_tracker.on_cookie_refresh()
                    except Exception:
                        pass
                ps_auth.reload_cookie()
                await asyncio.sleep(2)
                return True
            else:
                log_warn("SESSION", f"Playwright failed: {stderr.decode()[:200]}")
                return False
        except asyncio.TimeoutError:
            log_warn("SESSION", "Playwright timed out (90s)")
            try:
                await asyncio.create_subprocess_exec('pkill', '-f', 'chromium.*ps3838')
            except Exception:
                pass
            return False
        except Exception as e:
            log_warn("SESSION", f"Playwright error: {e}")
            return False

    while True:
        if _SHUTDOWN:
            log_info("[SESSION] Shutdown signal — exiting session manager")
            return
        if live_feed.session_alive:
            # ── Sustained health check ─────────────────────────
            if restored_at > 0 and time.time() - restored_at > 60:
                if pw_cycle > 0 or pw_idx > 0:
                    log_info(f"[SESSION] 60s sustained health — resetting Playwright counters (was cycle {pw_cycle + 1} idx {pw_idx})")
                _full_reset()

            # ── Normal keepalive ───────────────────────────────
            try:
                hdrs = ps_auth.build_headers(method="GET")
                ts = int(time.time() * 1000)
                post_hdrs = ps_auth.build_headers(method="POST")
                post_hdrs["Content-Type"] = "application/x-www-form-urlencoded"
                post_hdrs["Content-Length"] = "0"
                bal_url = f"{PS_BASE_URL}/member-service/v2/account-balance?locale=en_US&_={ts}&withCredentials=true"
                async with ps_auth._session.post(bal_url, headers=post_hdrs, timeout=10) as resp:
                    await resp.read()
                    bal_ok = resp.status == 200

                ts = int(time.time() * 1000)
                ka_url = f"{PS_BASE_URL}/member-auth/v2/keep-alive?locale=en_US&_={ts}&withCredentials=true"
                async with ps_auth._session.get(ka_url, headers=hdrs, timeout=10) as resp:
                    await resp.read()
                    ka_ok = resp.status == 200

                await system_status(ps_auth)

                if ka_ok and bal_ok:
                    fail_count = 0
                    # ── WS escalation check ───────────────────────
                    # WS token 403 while keepalive healthy — WS self-heal failed, needs Playwright
                    if live_feed._ws_needs_recovery:
                        log_warn("SESSION", "WS token recovery escalated — running Playwright to fix token auth")
                        live_feed._ws_needs_recovery = False  # clear flag before attempt
                        pw_success = await _run_playwright()
                        if pw_success:
                            ps_auth.invalidate_token_cache()
                            log_info("[SESSION] Playwright completed for WS token recovery — WS should reconnect on next cycle")
                        else:
                            log_warn("SESSION", "Playwright failed for WS token recovery — WS will keep retrying")
                else:
                    fail_count += 1
                    log_warn("SESSION", f"PS keepalive falsy ({fail_count}/{MAX_FAILS})")
            except Exception as e:
                fail_count += 1
                log_warn("SESSION", f"PS keepalive exception: {e} ({fail_count}/{MAX_FAILS})")

            # ── 3 fails → enter recovery ───────────────────────
            if fail_count >= MAX_FAILS:
                live_feed.session_alive = False
                live_feed._ws_connected = False
                _reset_recovery()
                log_warn("SESSION", "SESSION DOWN — entering recovery mode")

        else:
            # ══════════════════════════════════════════════════
            # RECOVERY MODE
            # ══════════════════════════════════════════════════

            # ── L1 RETRY: diagnose failure type ────────────────
            if recovery_fails < L1_MAX:
                recovery_fails += 1
                ok, reason = await _test_keepalive()
                if ok:
                    _restore_session(f"L1 RETRY — transient, attempt {recovery_fails}")
                    await asyncio.sleep(10)
                    continue
                if reason == 'auth':
                    auth_fail_count += 1
                log_info(f"[SESSION] L1 RETRY {recovery_fails}/{L1_MAX} — reason={reason} (auth_count={auth_fail_count})")

                if recovery_fails >= L1_MAX:
                    if auth_fail_count >= 2:
                        log_info("[SESSION] L1 done — mostly auth failures, cookies likely expired")
                    else:
                        log_info("[SESSION] L1 done — mostly network failures, may self-heal or cookies expired during outage")
                await asyncio.sleep(10)
                continue

            # ── L2 RELOAD: disk cookies once ───────────────────
            if not disk_reloaded:
                disk_reloaded = True
                log_info("[SESSION] L2 RELOAD — reloading cookies from disk...")
                ps_auth.reload_cookie()
                await asyncio.sleep(2)
                ok, reason = await _test_keepalive()
                if ok:
                    _restore_session("L2 RELOAD — disk cookies were fresh")
                    await asyncio.sleep(10)
                    continue
                log_warn("SESSION", f"L2 RELOAD — disk cookies also stale (reason={reason})")

                if auth_fail_count < 2:
                    log_info("[SESSION] Network-type failure — retrying L1 once more before L3")
                    recovery_fails = 0
                    await asyncio.sleep(10)
                    continue

            # ── L3 PLAYWRIGHT: progressive cooldowns ───────────
            if pw_cycle >= PW_MAX_CYCLES:
                log_warn("SESSION", f"HARD STOP — {PW_MAX_CYCLES} Playwright cycles exhausted. Human intervention needed.")
                log_warn("SESSION", "Bot is alive but making ZERO external calls. Restart manually.")
                while True:
                    await asyncio.sleep(300)
                    ok, _ = await _test_keepalive()
                    if ok:
                        _restore_session("HARD STOP — PS came back alive!")
                        _full_reset()
                        break
                    log_warn("SESSION", "Still in hard stop. Restart bot to resume.")
                if live_feed.session_alive:
                    continue

            cooldown_sec = PW_COOLDOWNS_MIN[pw_idx] * 60
            elapsed = time.time() - last_pw_at

            if elapsed < cooldown_sec:
                remaining = int(cooldown_sec - elapsed)
                ok, reason = await _test_keepalive()
                if ok:
                    _restore_session(f"L3 — self-healed during Playwright cooldown (cycle {pw_cycle + 1}, remaining {remaining}s)")
                    await asyncio.sleep(10)
                    continue
                if remaining > 30:
                    log_info(f"[SESSION] L3 cooldown: {remaining}s until Playwright attempt (cycle {pw_cycle + 1}, idx {pw_idx + 1}/{len(PW_COOLDOWNS_MIN)}) — keepalive={reason}")
                await asyncio.sleep(10)
                continue

            # ── Run Playwright ─────────────────────────────────
            attempt_num = pw_cycle * len(PW_COOLDOWNS_MIN) + pw_idx + 1
            total_attempts = PW_MAX_CYCLES * len(PW_COOLDOWNS_MIN)
            log_warn("SESSION", f"L3 PLAYWRIGHT cycle {pw_cycle + 1}/{PW_MAX_CYCLES} step {pw_idx + 1}/{len(PW_COOLDOWNS_MIN)} (attempt {attempt_num}/{total_attempts})")

            last_pw_at = time.time()
            pw_success = await _run_playwright()

            if pw_success:
                ok, reason = await _test_keepalive()
                if ok:
                    _restore_session(f"L3 PLAYWRIGHT — cycle {pw_cycle + 1} step {pw_idx + 1}")
                    await asyncio.sleep(10)
                    continue
                else:
                    log_warn("SESSION", f"Post-Playwright keepalive failed (reason={reason})")

            pw_idx += 1
            if pw_idx >= len(PW_COOLDOWNS_MIN):
                pw_idx = 0
                pw_cycle += 1
                if pw_cycle < PW_MAX_CYCLES:
                    log_info(f"[SESSION] Playwright cycle {pw_cycle} done — starting cycle {pw_cycle + 1}")

        await asyncio.sleep(10)



def _hint_to_sp(hint):
    """Convert hint to PS sport ID."""
    return {'basketball': 4, 'esports': 12, 'soccer': 29}.get(hint, 29)


def _build_market(sub, parent, hint):
    """Classify one etop sub → EtopMarket. No find_line. No PS required.

    Every valid sub gets an EtopMarket. PS data attached later by matching.
    This is the ONLY build function. One path. No fallbacks.
    """
    vs1 = (parent.get('vs1') or {}).get('name', '')
    vs2 = (parent.get('vs2') or {}).get('name', '')
    league = (parent.get('league') or {}).get('name', '')
    cat_type = (parent.get('category') or {}).get('type', '').lower()
    vs1_image = (parent.get('vs1') or {}).get('image', '')
    gw_id = str((parent.get('offerMatch') or {}).get('id', ''))
    parent_bo = parent.get('bo', 3)

    mtype = sub.get('type', 0)
    remain = sub.get('remainTime', 0) / 1000.0

    mkt_desc = classify_etop_sub(
        mtype=mtype, map_num=sub.get('map', 0),
        offer_score=sub.get('offerScore', 0) or 0,
        offer_team=sub.get('offerTeam', 0) or 0,
        total_score=sub.get('totalScore', None),
        total_time=sub.get('totalTime', None),
        mid=str(sub.get('id', '')),
        gw_id=gw_id,
        parent_bo=parent_bo, vs1=vs1, vs2=vs2, hint=hint,
        image=vs1_image, league=league,
        cat_type=cat_type, sport_hint=hint)

    if not mkt_desc:
        return None

    fav = mkt_desc.get('favorite')
    giving_side = fav if fav in ('team1', 'team2') else None

    return EtopMarket(
        team1=vs1, team2=vs2,
        o1=(sub.get('vs1') or {}).get('odds', 0),
        o2=(sub.get('vs2') or {}).get('odds', 0),
        market=mkt_desc['market'],
        line=mkt_desc.get('line') or 0,
        map_num=mkt_desc['map'],
        label=mkt_desc['label'],
        giving_side=giving_side,
        mid=str(sub.get('id', '')),
        parent_id=str(parent.get('id', '')),
        remain=remain,
        can_press=sub.get('canPress', False),
        raw_type=mtype,
        league=league,
        url='',
        game=mkt_desc.get('game', ''),
        ps_name_team1=None,
        ps_name_team2=None,
        ps_event_id=None,
    )


# ═══════════════════════════════════════════════════════════════════════
# BRAIN — event-driven. Wakes when etop data arrives.
# Register + Monitor + Dash. NO firing (fire is independent).
# ═══════════════════════════════════════════════════════════════════════

async def _brain_loop(
    etop_state, live_feed, fire_zone, inventory, evidence_db,
    markets, rest_fetch_ts,
    etop_api, ps_auth, session_tracker=None,
):
    """Wakes on etop_state.update(). Processes immediately.

    ~1.5ms from data arrival to processing complete.
    Timeout 5s safety net (runs anyway if event lost).
    """
    while etop_state.fetch_count == 0:
        await asyncio.sleep(0.5)
    log_info("[BRAIN] Brain loop started (event-driven, 5s timeout)")

    cycle_count = 0
    while True:
        if _SHUTDOWN:
            return
        etop_state.brain_event.clear()
        try:
            await asyncio.wait_for(etop_state.brain_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        cycle_count += 1
        try:
            await _brain_cycle(
                etop_state, live_feed, fire_zone, inventory, evidence_db,
                markets, rest_fetch_ts,
                etop_api, ps_auth, cycle_count, session_tracker,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            log_error("BRAIN", f"Cycle error: {type(e).__name__}: {e}")
            traceback.print_exc()


async def _brain_cycle(
    etop_state, live_feed, fire_zone, inventory, evidence_db,
    markets, rest_fetch_ts,
    etop_api, ps_auth, cycle_count, session_tracker=None,
):
    """One brain cycle: REGISTER → MATCH → UPDATE → DASH."""

    _remain_at = time.time()
    event_store = live_feed.event_store
    listing = etop_state.listing
    parents = etop_state.parents

    if not listing:
        return

    # Housekeeping
    if cycle_count % 100 == 1:
        event_store.cleanup_stale(max_age_hours=48.0)
        _states = {}
        for tm in markets.values():
            _states[tm.state] = _states.get(tm.state, 0) + 1
        log_info(f"[BRAIN] {len(parents)} parents, {len(listing)} subs, "
                 f"etop_age={etop_state.age:.1f}s, markets={len(markets)}, "
                 f"states={_states}")

    if inventory.needs_refresh(60):
        await inventory.load_pool(etop_api, force=True)

    # Always subscribe to ALL sports
    _active = {29, 12, 4}
    if _active != live_feed.active_sports:
        live_feed.active_sports = _active

    # ════════════════════════════════════════════════════════════════
    # STEP 1: REGISTER — every etop sub enters markets{} immediately
    # ════════════════════════════════════════════════════════════════

    _sorted_parents = sorted(parents, key=lambda p: (
        p.get('sublist', [{}])[0].get('remainTime', 999999999)
        if p.get('sublist') else 999999999))

    for par in _sorted_parents:
        vs1 = (par.get('vs1') or {}).get('name', '')
        vs2 = (par.get('vs2') or {}).get('name', '')
        if not vs1 or not vs2:
            continue

        cat_type = (par.get('category') or {}).get('type', '').lower()
        league = (par.get('league') or {}).get('name', '')
        vs1_image = (par.get('vs1') or {}).get('image', '')
        hint = infer_sport_hint(league=league, image=vs1_image, cat_type=cat_type)

        for sub in par.get('sublist', []):
            if hint == 'soccer' and cycle_count < 3:
                _gw = str(par.get('offerMatch', {}).get('id', ''))
                log_info(f"[RAW_SUB] {vs1} vs {vs2} | mid={sub.get('id')} type={sub.get('type')} "
                         f"offerScore={sub.get('offerScore')} totalScore={sub.get('totalScore')} "
                         f"o1={sub.get('o1')} o2={sub.get('o2')} gw={str(sub.get('id',''))==_gw}")
            em = _build_market(sub, par, hint)
            if em:
                _mid = em.mid
                if _mid in markets:
                    old_tm = markets[_mid]
                    old_fk = old_tm.fire_key
                    # Preserve PS match data
                    em.ps_event_id = old_tm.etop_market.ps_event_id or em.ps_event_id
                    em.ps_name_team1 = old_tm.etop_market.ps_name_team1 or em.ps_name_team1
                    em.ps_name_team2 = old_tm.etop_market.ps_name_team2 or em.ps_name_team2
                    old_tm.etop_market = em
                    old_tm.fire_key = em.fire_key
                    if old_fk != em.fire_key:
                        log_info(f"[LINE_CHANGE] {em.team1} vs {em.team2} mid={_mid} "
                                 f"{old_fk} → {em.fire_key}")
                else:
                    if not cat_type:
                        log_warn("BRAIN", f"Empty cat_type for {vs1} vs {vs2} league={league}")
                    tm = TrackedMarket(em, ps_event_id=None, hint=hint, cat_type=cat_type)
                    markets[_mid] = tm

    # ════════════════════════════════════════════════════════════════
    # STEP 2: MATCH — find PS eid for UNMATCHED groups
    # ════════════════════════════════════════════════════════════════

    _unmatched_groups = {}
    for mid, tm in markets.items():
        if tm.ps_event_id is None:
            key = f"{tm.etop_market.team1}|{tm.etop_market.team2}"
            _unmatched_groups.setdefault(key, []).append(tm)

    # Reset match block counts for pairs that are no longer in conflict
    # (their blocker was evicted or disappeared)
    _active_eids = {tm.ps_event_id for tm in markets.values() if tm.ps_event_id is not None}
    for _mk in list(_match_block_counts.keys()):
        # If no market with a conflicting eid is still active, reset
        # Exception: eid count == 999 means VERIFY_FAIL permanent block — never reset
        _mk_eids = {eid for (mk, eid) in _eid_block_counts if mk == _mk}
        if any(_eid_block_counts.get((_mk, eid), 0) == 999 for eid in _mk_eids):
            continue  # permanent VERIFY_FAIL block — never auto-clear
        if not _mk_eids.intersection(_active_eids):
            _match_block_counts.pop(_mk, None)
            for _bk in [k for k in _eid_block_counts if k[0] == _mk]:
                _eid_block_counts.pop(_bk, None)

    for key, group in _unmatched_groups.items():
        # Skip evidence_match entirely if this pair is persistently blocked
        if _match_block_counts.get(key, 0) >= 5:
            continue

        tm0 = group[0]
        vs1 = tm0.etop_market.team1
        vs2 = tm0.etop_market.team2
        hint = tm0.hint
        league = tm0.etop_market.league

        ps_events = event_store.get_events_for_matching(hint)
        best, method = evidence_match(
            vs1, vs2, ps_events, hint or 'unknown', evidence_db,
            etop_league=league, etop_cat_type=tm0.cat_type)

        if not (best and best.action == 'AUTO_MATCH'):
            continue

        ps_event_id = best.ps_eid

        # ── Verify etop teams match PS event teams ──
        # Checks BOTH teams as a pair. Alias-safe: even if one name is
        # translated (BBL→Bad Boys Lost), the opponent still matches.
        # Combined pair score: avg 50 each = 100 total minimum.
        _ev_info = event_store.get_event(ps_event_id)
        if _ev_info:
            from thefuzz import fuzz as _vf
            _verify = max(
                _vf.ratio(vs1.lower(), _ev_info['home'].lower()) + _vf.ratio(vs2.lower(), _ev_info['away'].lower()),
                _vf.ratio(vs1.lower(), _ev_info['away'].lower()) + _vf.ratio(vs2.lower(), _ev_info['home'].lower()),
            )
            if _verify < 120:
                log_warn("BRAIN", f"[VERIFY_FAIL] {vs1} vs {vs2} ≠ "
                         f"{_ev_info['home']} vs {_ev_info['away']} "
                         f"pair={_verify} eid={ps_event_id} → REJECTED")
                _eid_block_counts[(key, ps_event_id)] = 999
                continue

        # ── EID conflict resolution ──
        # If another match already claims this eid, compare who matches better.
        # Better match wins. Loser gets evicted back to UNMATCHED.
        _eid_conflict = False
        for _fk, _tm in list(markets.items()):
            if _tm.ps_event_id == ps_event_id and _tm.ps_event_id is not None:
                if _tm.etop_market.team1 != vs1 or _tm.etop_market.team2 != vs2:
                    # Different teams claim same eid — resolve by name match quality
                    ev_info = event_store.get_event(ps_event_id)
                    if ev_info:
                        from thefuzz import fuzz as _cf
                        ps_home = ev_info['home'].lower()
                        ps_away = ev_info['away'].lower()

                        # Score: how well do the teams match the PS event?
                        old_t1 = _tm.etop_market.team1.lower()
                        old_t2 = _tm.etop_market.team2.lower()
                        old_score = max(
                            _cf.ratio(old_t1, ps_home) + _cf.ratio(old_t2, ps_away),
                            _cf.ratio(old_t1, ps_away) + _cf.ratio(old_t2, ps_home))

                        new_score = max(
                            _cf.ratio(vs1.lower(), ps_home) + _cf.ratio(vs2.lower(), ps_away),
                            _cf.ratio(vs1.lower(), ps_away) + _cf.ratio(vs2.lower(), ps_home))

                        if new_score > old_score:
                            # New match is better — evict old match
                            log_info(f"[EID_RESOLVE] Evicting {_tm.etop_market.team1} vs "
                                     f"{_tm.etop_market.team2} (score={old_score}) "
                                     f"in favor of {vs1} vs {vs2} (score={new_score}) "
                                     f"for eid={ps_event_id}")
                            # Reset block counts so evicted pair can re-log if needed
                            _old_key = f"{_tm.etop_market.team1}|{_tm.etop_market.team2}"
                            _eid_block_counts.pop((_old_key, ps_event_id), None)
                            _match_block_counts.pop(_old_key, None)
                            # Reset ALL markets from old match that used this eid
                            _old_key = f"{_tm.etop_market.team1}|{_tm.etop_market.team2}"
                            for _fk2, _tm2 in markets.items():
                                if (_tm2.ps_event_id == ps_event_id and
                                    f"{_tm2.etop_market.team1}|{_tm2.etop_market.team2}" == _old_key):
                                    _tm2.ps_event_id = None
                                    _tm2.etop_market.ps_event_id = None
                                    _tm2.etop_market.ps_name_team1 = None
                                    _tm2.etop_market.ps_name_team2 = None
                                    _tm2.state = 'UNMATCHED'
                        else:
                            # Old match is better — block new match
                            _bk = (key, ps_event_id)
                            _eid_block_counts[_bk] = _eid_block_counts.get(_bk, 0) + 1
                            _match_block_counts[key] = _match_block_counts.get(key, 0) + 1
                            if _eid_block_counts[_bk] <= 3:
                                log_warn("BRAIN", f"[EID_CONFLICT] {vs1} vs {vs2} "
                                         f"(score={new_score}) blocked by "
                                         f"{_tm.etop_market.team1} vs {_tm.etop_market.team2} "
                                         f"(score={old_score}) for eid={ps_event_id}")
                            elif _eid_block_counts[_bk] == 4:
                                log_warn("BRAIN", f"[EID_CONFLICT] {vs1} vs {vs2} eid={ps_event_id} "
                                         f"— suppressing further conflict logs for this pair")
                            _eid_conflict = True
                    else:
                        # No event info — can't resolve, block
                        _bk = (key, ps_event_id)
                        _eid_block_counts[_bk] = _eid_block_counts.get(_bk, 0) + 1
                        _match_block_counts[key] = _match_block_counts.get(key, 0) + 1
                        if _eid_block_counts[_bk] <= 3:
                            log_warn("BRAIN", f"[EID_CONFLICT] {vs1} vs {vs2} → eid={ps_event_id} "
                                     f"used by {_tm.etop_market.team1} vs {_tm.etop_market.team2} "
                                     f"(no event info to resolve)")
                        _eid_conflict = True
                break
        if _eid_conflict:
            continue

        ev_info = event_store.get_event(ps_event_id)
        if not ev_info:
            continue

        from thefuzz import fuzz as _ev_fuzz
        h1 = _ev_fuzz.ratio(vs1.lower(), ev_info['home'].lower())
        a1 = _ev_fuzz.ratio(vs1.lower(), ev_info['away'].lower())
        if h1 >= a1:
            ps_name_t1, ps_name_t2 = ev_info['home'], ev_info['away']
        else:
            ps_name_t1, ps_name_t2 = ev_info['away'], ev_info['home']

        log_info(f"[MATCH] {vs1} vs {vs2} → {ps_name_t1} vs {ps_name_t2} "
                 f"eid={ps_event_id} method={method} score={best.combined:.0f}")

        for tm in group:
            tm.etop_market.ps_event_id = ps_event_id
            tm.etop_market.ps_name_team1 = ps_name_t1
            tm.etop_market.ps_name_team2 = ps_name_t2
            tm.ps_event_id = ps_event_id
            tm.state = 'MATCHED'

    # ════════════════════════════════════════════════════════════════
    # STEP 3: UPDATE — odds, remain, state, EV
    # ════════════════════════════════════════════════════════════════

    for mid, tm in list(markets.items()):
        em = tm.etop_market
        sub = listing.get(em.mid)

        # CLOSED is terminal — only revives if remain > 0 (real extension)
        if tm.state == 'CLOSED':
            if sub and int(sub.get('remain', 0)) > 0:
                tm.dead_at = 0
                tm.locked_at = 0
                log_info(f"[BRAIN] {em.team1} vs {em.team2} [{em.label}] RETURNED from CLOSED remain={int(sub['remain'])}s")
            else:
                continue

        if not sub:
            if tm.dead_at == 0:
                tm.dead_at = time.time()
            if tm.dead_at > 0 and (time.time() - tm.dead_at) > 120:
                em.can_press = False
                if not tm.locked_at:
                    tm.locked_at = tm.dead_at
                tm.state = 'CLOSED'
            if tm.death_timer is None:
                tm.death_timer = time.time()
            elif time.time() - tm.death_timer > 604800:
                log_info(f"[BRAIN] {em.team1} vs {em.team2} [{em.label}] CLEANUP 7d")
                fire_zone.cleanup(tm.fire_key)
                del markets[mid]
            continue

        o1, o2 = sub['o1'], sub['o2']
        remain = sub['remain']
        can_press = sub['can_press']
        cancel_code = sub.get('cancel_code')
        seconds = int(remain)

        if cancel_code:
            if tm.state != 'CLOSED':
                log_info(f"[BRAIN] {em.team1} vs {em.team2} [{em.label}] CANCELLED code={cancel_code}")
            em.can_press = False
            if not tm.locked_at:
                tm.locked_at = time.time()
            tm.state = 'CLOSED'
            continue

        if seconds <= 0:
            if tm.dead_at == 0:
                tm.dead_at = time.time()
        else:
            if tm.dead_at > 0:
                tm.locked_at = 0
            tm.dead_at = 0

        em.update_odds(o1, o2, remain, can_press)

        if tm.dead_at > 0 and (time.time() - tm.dead_at) > 120:
            em.can_press = False
            if not tm.locked_at:
                tm.locked_at = tm.dead_at
            tm.state = 'CLOSED'
            continue

        if tm.death_timer is not None:
            log_info(f"[BRAIN] {em.team1} vs {em.team2} [{em.label}] RETURNED")
            tm.death_timer = None

        if em.ps_event_id is None:
            new_state = 'UNMATCHED'
        else:
            ev1, ev2 = compute_ev(em, live_feed.standard_store)

            # Alt-eid resolution: map ML/OU sometimes lives under a different PS eid.
            # IMPORTANT: never mutate em.ps_event_id here — use _ev_eid as a local
            # working variable. Mutating em.ps_event_id caused the eid to oscillate
            # every brain cycle, making EV flicker between found/not-found every 3s.
            if ev1 is None and em.ps_event_id and em.ps_name_team1:
                store = live_feed.standard_store
                _ev_eid = em.ps_event_id  # working eid — never written back to em

                # Try alternate eids (mk=3 map markets have separate eids)
                alt_eids = store.find_alternate_eids(_ev_eid, em.ps_name_team1)
                for alt_eid in alt_eids:
                    em.ps_event_id = alt_eid  # temp: compute_ev reads em.ps_event_id
                    ev1, ev2 = compute_ev(em, store)
                    em.ps_event_id = _ev_eid  # always restore immediately
                    if ev1 is not None:
                        _ev_eid = alt_eid     # remember which eid worked (local only)
                        break

                # Try kills eid (Total Kills OU lives under "(Kills)" event)
                if ev1 is None and em.market in ('ou', 'team_total') and em.map_num > 0:
                    _kname1 = em.ps_name_team1 + " (Kills)"
                    _kname2 = em.ps_name_team2 + " (Kills)"
                    kills_eid = store.find_event_id(_kname1, _kname2)
                    if kills_eid:
                        em.ps_event_id = kills_eid
                        ev1, ev2 = compute_ev(em, store)
                        em.ps_event_id = _ev_eid  # always restore

            if seconds <= 0:
                new_state = 'CLOSED'
            elif ev1 is None:
                new_state = 'MATCHED'
            else:
                new_state = 'MONITOR'

        if tm.state != new_state and new_state not in ('UNMATCHED', 'MATCHED'):
            ps_age = live_feed.standard_store.get_line_age(em.ps_event_id, em.map_num, em.market) if em.ps_event_id else None
            log_market(em.team1, em.team2, em.market, em.map_num, new_state,
                       remain=f"{seconds}s", etop=f"{o1}/{o2}",
                       ps_age=f"{ps_age}s" if ps_age else "?",
                       ev=f"{max(ev1,ev2):+.2f}%" if ev1 is not None else "?",
                       line=em.line)
        tm.state = new_state

    # ════════════════════════════════════════════════════════════════
    # DASH STATE — one dict, no merging
    # ════════════════════════════════════════════════════════════════

    import json as _json
    try:
        dash_markets = []
        for mid, tm in markets.items():
            em = tm.etop_market
            sub = listing.get(em.mid)
            live_o1 = sub.get('o1', em.o1) if sub else em.o1
            live_o2 = sub.get('o2', em.o2) if sub else em.o2
            live_remain = sub.get('remain', em.remain) if sub else em.remain
            live_can_press = sub.get('can_press', em.can_press) if sub else em.can_press

            ps_age = live_feed.standard_store.get_line_age(
                em.ps_event_id, em.map_num, em.market) if em.ps_event_id else None
            ev1, ev2 = compute_ev(em, live_feed.standard_store) if em.ps_event_id else (None, None)
            best_ev = max(ev1, ev2) if ev1 is not None else -999

            ps_fair_str = '–'
            if em.ps_event_id and ev1 is not None:
                if em.market == 'ml' and em.ps_name_team1:
                    f1 = live_feed.standard_store.get_ml_fair(em.ps_event_id, em.map_num, em.ps_name_team1)
                    f2 = live_feed.standard_store.get_ml_fair(em.ps_event_id, em.map_num, em.ps_name_team2)
                    if f1 and f2:
                        ps_fair_str = f'{f1:.3f}/{f2:.3f}'
                elif em.market == 'hdp' and em.giving_team_ps:
                    gps = em.giving_team_ps
                    ops = em.ps_name_team2 if gps == em.ps_name_team1 else em.ps_name_team1
                    gf = live_feed.standard_store.get_hdp_fair(em.ps_event_id, em.map_num, gps, -abs(em.line))
                    of = live_feed.standard_store.get_hdp_fair(em.ps_event_id, em.map_num, ops, abs(em.line))
                    if gf and of:
                        ps_fair_str = f'{gf:.3f}/{of:.3f}' if gps == em.ps_name_team1 else f'{of:.3f}/{gf:.3f}'
                elif em.market in ('ou', 'team_total'):
                    fo = live_feed.standard_store.get_ou_fair(em.ps_event_id, em.map_num, 'over', em.line)
                    fu = live_feed.standard_store.get_ou_fair(em.ps_event_id, em.map_num, 'under', em.line)
                    if fo and fu:
                        ps_fair_str = f'{fo:.3f}/{fu:.3f}'

            name = f'{em.team1} vs {em.team2}'
            _mp = f'M{em.map_num}' if em.map_num > 0 else ''
            if em.market == 'ml':
                mkt_label = f'Moneyline {_mp}'.strip()
            elif em.market == 'hdp':
                giver = em.giving_team_etop or em.team1
                mkt_label = f'{giver} -{em.line} {_mp}'.strip()
            elif em.market in ('ou', 'team_total'):
                if em.game in ('dota', 'lol'):
                    mkt_label = f'O/U Kills {em.line} {_mp}'.strip()
                elif em.game == 'cs2':
                    mkt_label = f'O/U Rounds {em.line} {_mp}'.strip()
                else:
                    mkt_label = f'O/U {em.line} {_mp}'.strip()
            elif em.market == 'race':
                mkt_label = f'First 5 Rounds {_mp}'.strip()
            elif em.market == 'f10k':
                mkt_label = f'10 Kills {_mp}'.strip()
            elif em.market == 'f5k':
                mkt_label = f'5 Kills {_mp}'.strip()
            elif em.market == 'duration':
                mkt_label = f'Duration {em.line} {_mp}'.strip()
            else:
                mkt_label = f'{em.market} {_mp}'.strip()

            _fs = fire_zone._fire_state.get(tm.fire_key)
            if _fs and _fs.value_cap > 0:
                _cap_src = "TUHAO"
                _eff_cap = _fs.value_cap
            else:
                _cap_src = "HARD_CAP"
                _eff_cap = config.HARD_CAP
            _rem_cap = _eff_cap - (_fs.total_value if _fs else 0)

            dash_markets.append({
                'n': name, 'ml': mkt_label, 'fk': tm.fire_key,
                'b': round(best_ev, 2),
                'e1': round(ev1, 2) if ev1 is not None else None,
                'e2': round(ev2, 2) if ev2 is not None else None,
                'etop': f'{live_o1:.2f}/{live_o2:.2f}' if live_o1 > 0 else '–',
                'pf': ps_fair_str,
                'pa': int(ps_age) if ps_age else 0,
                's': int(live_remain),
                'st': tm.state,
                'ln': em.line,
                'ps': f'{em.ps_name_team1} vs {em.ps_name_team2}' if em.ps_name_team1 else '',
                'mid': em.mid,
                'pool': inventory.pool_free_count() if inventory.pool_loaded else -1,
                'inv_value': round(_fs.total_value, 1) if _fs else 0,
                'inv_items': _fs.total_fired if _fs else 0,
                'can_press': bool(live_can_press), 'cp': bool(live_can_press),
                'locked_at': tm.locked_at or 0,
                'in_listing': sub is not None,
                'game': getattr(em, 'game', ''),
                'cap': f"{_cap_src}:{_rem_cap:.0f}/{_eff_cap:.0f}g",
            })

        dash_live = []
        for _mid, _tm in markets.items():
            _fs_check = fire_zone._fire_state.get(_tm.fire_key)
            if not ((_tm.etop_market.remain > 0 and _tm.etop_market.remain <= config.TUHAO_SECS) or (_fs_check and _fs_check.total_fired > 0)):
                continue
            _em = _tm.etop_market
            _sub = listing.get(_em.mid)
            _lo1 = _sub.get('o1', _em.o1) if _sub else _em.o1
            _lo2 = _sub.get('o2', _em.o2) if _sub else _em.o2
            _ev1, _ev2 = compute_ev(_em, live_feed.standard_store) if _em.ps_event_id else (None, None)
            _bev = max(_ev1, _ev2) if _ev1 is not None else None
            _fs = fire_zone._fire_state.get(_tm.fire_key)
            _mp = f' Map {_em.map_num}' if _em.map_num > 0 else ''
            if _em.market == 'ml':
                _lname = f'{_em.team1} vs {_em.team2} ML{_mp}'
            elif _em.market == 'hdp':
                _giver = _em.giving_team_etop or _em.team1
                _lname = f'{_em.team1} vs {_em.team2} [{_giver} -{_em.line}]{_mp}'
            elif _em.market in ('ou', 'team_total'):
                _pfx = 'O/U' if _em.market == 'ou' else 'TT'
                _lname = f'{_em.team1} vs {_em.team2} {_pfx} [{_em.line}]{_mp}'
            else:
                _lname = _tm.fire_key
            dash_live.append({
                'fk': _tm.fire_key, 'n': _lname, 'st': _tm.state,
                's': int(_em.remain),
                'ev': round(_bev, 2) if _bev is not None else None,
                'etop': f'{_lo1:.2f}/{_lo2:.2f}' if _lo1 > 0 else '–',
                'fired': _fs.total_fired if _fs else 0,
                'pool': inventory.pool_free_count(),
                'fired_at': _fs.first_fire_at if _fs else 0,
            })

        _states = {}
        for tm in markets.values():
            _states[tm.state] = _states.get(tm.state, 0) + 1

        dash_state = {
            'ts': time.time(), 'updated_at': time.time(),
            'remain_at': _remain_at,
            'etop_age': round(etop_state.age, 1),
            'markets': sorted(dash_markets, key=lambda m: m['s']),
            'live': sorted(dash_live, key=lambda m: m['s']),
            'ws': live_feed._ws_connected if hasattr(live_feed, '_ws_connected') else False,
            'total_markets': len(markets),
            'states': _states,
            'tracked': sum(1 for tm in markets.values() if tm.ps_event_id is not None),
            'unmatched_count': sum(1 for tm in markets.values() if tm.ps_event_id is None),
            'listing': len(listing),
            'bag_value': round(inventory.pool_free_value(), 1) if inventory.pool_loaded else 0,
            'bag_count': inventory.pool_free_count() if inventory.pool_loaded else 0,
            'session_tracker': session_tracker.summary() if session_tracker else None,
        }
        tmp = 'data/dash_state.tmp'
        with open(tmp, 'w') as f:
            _json.dump(dash_state, f)
        os.replace(tmp, 'data/dash_state.json')
    except Exception as e:
        log_warn("DASH", f"Failed to write dash_state: {e}")


# ═══════════════════════════════════════════════════════════════════════
# FIRE — event-driven. Wakes when ANY data changes. Independent of brain.
# ═══════════════════════════════════════════════════════════════════════

async def _fire_loop(
    etop_state, live_feed, fire_zone, inventory, markets,
):
    """Wakes on etop_state.fire_event. ~1.5ms from data to fire decision.

    Reads latest etop odds + PS fair from shared memory.
    Recomputes EV at fire time — never uses cached EV.
    """
    while etop_state.fetch_count == 0:
        await asyncio.sleep(0.5)
    log_info("[FIRE] Fire loop started (event-driven, 5s timeout)")

    while True:
        if _SHUTDOWN:
            return
        etop_state.fire_event.clear()
        try:
            await asyncio.wait_for(etop_state.fire_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        try:
            await _fire_cycle(etop_state, live_feed, fire_zone, inventory, markets)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            log_error("FIRE", f"Cycle error: {type(e).__name__}: {e}")


async def _fire_cycle(etop_state, live_feed, fire_zone, inventory, markets):
    """One fire cycle. Scans markets in fire window, checks gates, fires."""

    listing = etop_state.listing
    if not listing:
        return

    etop_age = etop_state.age
    store = live_feed.standard_store
    _fire_candidates = []
    _p2_boundary = config.TRIGGER_SECS * 2 // 3   # 60s when TRIGGER=90
    _p3_boundary = config.TRIGGER_SECS // 3        # 30s when TRIGGER=90
    _scanned = 0
    _skipped = 0
    _phase_counts = {}

    for mid, tm in list(markets.items()):
        em = tm.etop_market

        # Read remain DIRECTLY from listing — never depend on brain's state
        sub = listing.get(em.mid)
        if not sub:
            continue

        seconds = int(sub.get('remain', 0))
        if seconds > config.TRIGGER_SECS or seconds <= 0:
            continue

        # Must have PS match
        if not em.ps_event_id or not em.ps_name_team1:
            continue

        o1, o2 = sub['o1'], sub['o2']

        # Recompute EV with LATEST data (not cached from brain)
        ev1, ev2 = compute_ev(em, store)
        if ev1 is None:
            continue
        best_ev = max(ev1, ev2)
        side = 'left' if ev1 >= ev2 else 'right'

        ps_age = store.get_line_age(em.ps_event_id, em.map_num, em.market)

        # Cap: TUHAO if available, HARD_CAP fallback (never blocks)
        fk = tm.fire_key
        fs = fire_zone.get_fire_state(fk)
        if fs.value_cap > 0:
            effective_cap = fs.value_cap
            cap_source = "TUHAO"
        else:
            effective_cap = config.HARD_CAP
            cap_source = "HARD_CAP"
        remaining_cap = effective_cap - fs.total_value
        if cap_source == "TUHAO":
            cap_str = f"TUHAO:{remaining_cap:.0f}/{effective_cap:.0f}g(est={fs.value_cap:.0f})"
        else:
            cap_str = f"HARD_CAP:{remaining_cap:.0f}/{effective_cap:.0f}g"

        # ── PHASE ──
        if seconds > _p2_boundary:
            phase_min_ev = config.PHASE1_EV
            phase_label = "P1"
        elif seconds > _p3_boundary:
            phase_min_ev = config.PHASE2_EV
            phase_label = "P2"
        else:
            phase_min_ev = config.PHASE3_EV
            phase_label = "P3"

        _scanned += 1
        _phase_counts[phase_label] = _phase_counts.get(phase_label, 0) + 1

        if phase_label != fs._last_phase:
            log_market(em.team1, em.team2, em.market, em.map_num,
                      f"ENTERING_{phase_label}", remain=f"{seconds}s",
                      min_ev=f"{phase_min_ev}%", pool=f"{fs.raw_pool:.0f}",
                      cap=f"{fs.value_cap:.0f}")
            fs._last_phase = phase_label

        # Fire zone logging
        try:
            _eid, _m = em.ps_event_id, em.map_num
            _b = store._data.get((_eid, _m))
            if em.market == 'ml' and _b:
                _n = store._n
                _e1 = _b['ml'].get(_n(em.ps_name_team1))
                _e2 = _b['ml'].get(_n(em.ps_name_team2))
                _r1 = _e1.raw if _e1 else None; _r2 = _e2.raw if _e2 else None
                _f1 = _e1.fair if _e1 else None; _f2 = _e2.fair if _e2 else None
            elif em.market == 'ou' and _b:
                _e1 = _b['ou'].get(('over', round(em.line, 2)))
                _e2 = _b['ou'].get(('under', round(em.line, 2)))
                _r1 = _e1.raw if _e1 else None; _r2 = _e2.raw if _e2 else None
                _f1 = _e1.fair if _e1 else None; _f2 = _e2.fair if _e2 else None
            elif em.market == 'hdp' and _b:
                _gps = store._n(em.giving_team_ps or em.ps_name_team1)
                _ops = store._n(em.ps_name_team2 if _gps == store._n(em.ps_name_team1) else em.ps_name_team1)
                _e1 = _b['hdp'].get((_gps, round(-abs(em.line), 2)))
                _e2 = _b['hdp'].get((_ops, round(abs(em.line), 2)))
                _r1 = _e1.raw if _e1 else None; _r2 = _e2.raw if _e2 else None
                _f1 = _e1.fair if _e1 else None; _f2 = _e2.fair if _e2 else None
            else:
                _r1 = _r2 = _f1 = _f2 = None
            _raw_str = f"{_r1:.3f}/{_r2:.3f}" if _r1 and _r2 else "?"
            _fair_str = f"{_f1:.3f}/{_f2:.3f}" if _f1 and _f2 else "?"
        except Exception:
            _raw_str = _fair_str = "?"

        log_market(em.team1, em.team2, em.market, em.map_num, "FIRE_ZONE",
                   remain=f"{seconds}s", etop=f"{o1}/{o2}",
                   ps_age=f"{ps_age}s" if ps_age else "?",
                   ev=f"{best_ev:+.2f}%", cap=cap_str, line=em.line,
                   ps_raw=_raw_str, ps_fair=_fair_str,
                   etop_age=f"{etop_age:.1f}s", phase=phase_label)

        # ── GATES (single list, single check) ──
        gates = [
            (o1 > 0 and o2 > 0,                            "no_odds"),
            (best_ev > phase_min_ev,                         f"ev={best_ev:+.1f}%<{phase_label}:{phase_min_ev}%"),
            (etop_age < config.MAX_ETOP_AGE,                 f"etop_stale={etop_age:.1f}s>{config.MAX_ETOP_AGE}"),
            (ps_age is not None and ps_age < config.MAX_PS_AGE, f"ps_stale={ps_age or 'N/A'}>{config.MAX_PS_AGE}s"),
            (fs.raw_pool >= config.MIN_RAW_POOL,             f"pool={fs.raw_pool:.0f}<{config.MIN_RAW_POOL}"),
            (remaining_cap > 0,                              f"cap_full={cap_str}"),
            (fs.total_fired < config.MAX_ITEMS,              f"max_items={fs.total_fired}/{config.MAX_ITEMS}"),
        ]
        failed = [reason for passed, reason in gates if not passed]

        if not failed:
            _fire_candidates.append((fk, tm, ev1, ev2, phase_label))
            log_market(em.team1, em.team2, em.market, em.map_num,
                      "FIRE_CANDIDATE", ev=f"{best_ev:+.2f}%",
                      side=side, cap=cap_str, phase=phase_label)
        else:
            _skipped += 1
            if best_ev > config.MIN_EV:
                log_info(f"[FIRE_SKIP] {em.team1} vs {em.team2} [{em.label}] "
                         f"{phase_label} blocked: {', '.join(failed)}")

    # ── Cycle summary ──
    if _scanned > 0:
        log_info(f"[FIRE_CYCLE] scanned={_scanned} "
                 f"P1={_phase_counts.get('P1',0)} P2={_phase_counts.get('P2',0)} P3={_phase_counts.get('P3',0)} "
                 f"candidates={len(_fire_candidates)} skipped={_skipped}")

    # Fire + cancel
    if _fire_candidates:
        fz_summary = await fire_zone.run_cycle(
            _fire_candidates, live_feed.standard_store, listing)
        if fz_summary.get('fired', 0) > 0:
            log_info(f"[FIRE] cycle: {fz_summary}")

    await fire_zone.check_cancels(markets)


# ═══════════════════════════════════════════════════════════════════════
# TUHAO — every 5s. Pool estimation. Sorted by remain ASC.
# ═══════════════════════════════════════════════════════════════════════

async def _tuhao_loop(markets, fire_zone, pool_estimator, etop_api, etop_state):
    """Independent pool estimation. Writes fire_state.value_cap.

    Brain/fire read value_cap:
      > 0 → TUHAO data used
      == 0 → HARD_CAP fallback (fires anyway)
    """
    while etop_state.fetch_count == 0:
        await asyncio.sleep(0.5)
    log_info("[TUHAO] Tuhao loop started (5s cadence)")

    while True:
        if _SHUTDOWN:
            return
        try:
            listing = etop_state.listing
            candidates = []
            for mid, tm in markets.items():
                em = tm.etop_market
                # Read remain from listing directly — not brain's em.remain
                sub = listing.get(em.mid)
                if not sub:
                    continue
                seconds = int(sub.get('remain', 0))
                if seconds <= config.TUHAO_SECS and seconds > 0:
                    fs = fire_zone.get_fire_state(tm.fire_key)
                    if fs.value_cap == 0:
                        candidates.append((seconds, tm.fire_key, em, fs))

            candidates.sort(key=lambda x: x[0])  # closest first

            for seconds, fk, em, fs in candidates:
                if pool_estimator._loaded and em.mid:
                    min_pool = await pool_estimator.estimate_pool(etop_api, em.mid)
                    if min_pool > 0:
                        fs.value_cap = pool_estimator.calc_value_cap(min_pool, config.MAX_POOL_IMPACT, config.HARD_CAP)
                        fs.raw_pool = min_pool
                    else:
                        fs.value_cap = config.HARD_CAP
                        fs.raw_pool = 0
                else:
                    fs.value_cap = config.HARD_CAP
                    fs.raw_pool = 0
                log_info(f"[TUHAO] {em.team1} vs {em.team2} [{em.label}]: "
                         f"value_cap=Gold {fs.value_cap:.1f} raw_pool={fs.raw_pool:.0f} remain={seconds}s")
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            log_error("TUHAO", f"Cycle error: {type(e).__name__}: {e}")
        await asyncio.sleep(5)


# ═══════════════════════════════════════════════════════════════════════
# DISCOVERY — every 10s. REST search for unmatched. Budgeted.
# ═══════════════════════════════════════════════════════════════════════

async def _discovery_loop(
    etop_state, live_feed, evidence_db, markets,
    search_tried, ps_auth, etop_api,
):
    """REST search for UNMATCHED markets. Budgeted: 3 REST calls per cycle.
    Sorted by remain ASC. Skips during active firing.
    """
    await asyncio.sleep(15)
    log_info("[DISCOVERY] Discovery loop started (10s cadence)")

    while True:
        if _SHUTDOWN:
            return
        try:
            await _discovery_cycle(
                etop_state, live_feed, evidence_db, markets,
                search_tried, ps_auth, etop_api,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            log_error("DISCOVERY", f"Cycle error: {type(e).__name__}: {e}")
        await asyncio.sleep(10)


async def _discovery_cycle(
    etop_state, live_feed, evidence_db, markets,
    search_tried, ps_auth, etop_api,
):
    """REST search_event for UNMATCHED parents."""
    event_store = live_feed.event_store

    # Group UNMATCHED by parent
    _unmatched_groups = {}
    for mid, tm in markets.items():
        if tm.ps_event_id is None:
            key = f"{tm.etop_market.team1}|{tm.etop_market.team2}"
            _unmatched_groups.setdefault(key, []).append(tm)

    if not _unmatched_groups:
        return

    # Skip REST during active firing
    _fire_active = any(
        (tm.etop_market.remain <= config.TUHAO_SECS and tm.etop_market.remain > 0) for tm in markets.values()
    )

    sorted_groups = sorted(_unmatched_groups.items(),
                           key=lambda x: min(tm.etop_market.remain for tm in x[1]))

    rest_budget = 3

    for key, group in sorted_groups:
        tm0 = group[0]
        vs1 = tm0.etop_market.team1
        vs2 = tm0.etop_market.team2
        hint = tm0.hint
        remain = min(tm.etop_market.remain for tm in group)

        if remain > 21600:
            continue

        _sk = f"{vs1}|{vs2}"
        if _sk in search_tried and time.time() - search_tried[_sk] < 300:
            continue

        if _fire_active or rest_budget <= 0:
            continue

        search_tried[_sk] = time.time()
        _alias_entry = evidence_db.lookup(vs1, hint or 'esports') if evidence_db else None
        _rest_t1 = _alias_entry.ps_name if _alias_entry else vs1

        _sr = await search_event(ps_auth, _rest_t1, vs2, hint)
        rest_budget -= 2

        if _sr:
            ps_event_id = _sr['eid']
            from thefuzz import fuzz as _sfuzz
            _sh = _sfuzz.ratio(vs1.lower(), _sr['home'].lower())
            _sa = _sfuzz.ratio(vs1.lower(), _sr['away'].lower())
            if _sh >= _sa:
                ps_name_t1, ps_name_t2 = _sr['home'], _sr['away']
            else:
                ps_name_t1, ps_name_t2 = _sr['away'], _sr['home']

            sp_id = {'basketball': 4, 'esports': 12, 'soccer': 29}.get(hint, 29)
            event_store.register_event(ps_event_id, ps_name_t1, ps_name_t2, sp_id, '', 'search_v2')
            log_info(f"[DISCOVERY] {vs1} vs {vs2} → {ps_name_t1} vs {ps_name_t2} eid={ps_event_id}")

            for tm in group:
                tm.etop_market.ps_event_id = ps_event_id
                tm.etop_market.ps_name_team1 = ps_name_t1
                tm.etop_market.ps_name_team2 = ps_name_t2
                tm.ps_event_id = ps_event_id
                tm.state = 'MATCHED'


if __name__ == "__main__":
    asyncio.run(main())
