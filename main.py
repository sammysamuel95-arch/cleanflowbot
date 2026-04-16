"""
CleanFlowBot — Clean Orchestrator

Boots the system, starts collectors, runs the pipeline.
All logic lives in modules/. This file only connects them.

Pipeline (every ~3s when etop data arrives):
  1. classifier.run()     → creates/updates Market entries
  2. matcher.run()        → finds PS event for UNMATCHED markets
  3. valuator.run()       → computes EV, sets phase per sport
  4. fire_engine.run()    → fires on FIRE_ZONE markets
  5. dashboard.run()      → writes dash_state.json

Background tasks:
  - Etop collector (3s poll)
  - PS WebSocket (streaming)
  - PS session manager (10s keepalive)
  - Cancel engine (5s check)
  - Tuhao pool estimation (5s)
  - Discovery REST search (10s)
"""

import os, time as _tz_time; os.environ['TZ'] = 'Asia/Jakarta'; _tz_time.tzset()

# ── Log rotation on startup ────────────────────────────────────────────────────
# Rotate bot_output.log on every restart — keeps log small, panel stays fast.
# Keeps last 5 logs as bot_output.log.1 .. .5
_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'bot.log')
if os.path.exists(_LOG_FILE) and os.path.getsize(_LOG_FILE) > 1024 * 1024:  # >1MB
    for _i in range(4, 0, -1):
        _src = f"{_LOG_FILE}.{_i}"
        _dst = f"{_LOG_FILE}.{_i+1}"
        if os.path.exists(_src):
            os.replace(_src, _dst)
    os.replace(_LOG_FILE, f"{_LOG_FILE}.1")
# Delete rotated logs older than 7 days
for _i in range(1, 6):
    _old = f"{_LOG_FILE}.{_i}"
    if os.path.exists(_old) and _tz_time.time() - os.path.getmtime(_old) > 7 * 86400:
        os.remove(_old)

import asyncio
import time
from aiohttp import web as sse_web
import traceback
import os
import signal
import atexit

import config
from container import Container
from core.logger import log_info, log_warn, log_error

# Modules
from modules import classifier, matcher, valuator, fire_engine, cancel_engine, dashboard
from core.data_bus import DataBus


def _sync_sport_configs(container):
    """Wire bot_config.json phase values into container sport_configs.
    Called at startup AND after every reload_config command."""
    from container import SportConfig
    container.sport_configs['esports'] = SportConfig(
        phase1_ev=config.PHASE1_EV,
        phase2_ev=config.PHASE2_EV,
        phase3_ev=config.PHASE3_EV,
        trigger_secs=config.TRIGGER_SECS,
        max_ps_age=config.MAX_PS_AGE,
    )
    sc = container.get_sport_config('esports')
    print(f"[CONFIG_SYNC] esports P1={sc.phase1_ev}% P2={sc.phase2_ev}% "
          f"P3={sc.phase3_ev}% trigger={sc.trigger_secs}s", flush=True)


# ═══════════════════════════════════════════════════════════════════════
# PROCESS SAFETY (same as original — PID lock, signal handling)
# ═══════════════════════════════════════════════════════════════════════

_PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'bot.pid')
_SHUTDOWN = False


# ═══════════════════════════════════════════════════════════════════════
# SSE SERVER — real-time push to panel
# ═══════════════════════════════════════════════════════════════════════

_sse_clients = []  # list of asyncio.Queue


async def _sse_handler(request):
    """SSE endpoint: GET /sse — panel subscribes here."""
    q = asyncio.Queue(maxsize=5)
    _sse_clients.append(q)
    resp = sse_web.StreamResponse(headers={
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Access-Control-Allow-Origin': '*',
    })
    await resp.prepare(request)
    try:
        while True:
            data = await asyncio.wait_for(q.get(), timeout=30.0)
            await resp.write(f"data: {data}\n\n".encode())
    except (asyncio.TimeoutError, ConnectionResetError, Exception):
        pass
    finally:
        try:
            _sse_clients.remove(q)
        except ValueError:
            pass
    return resp


def _push_sse(dash_data: dict):
    """Push a dash state snapshot to all connected SSE clients. Non-blocking."""
    if not _sse_clients:
        return
    import json as _json
    payload = _json.dumps(dash_data)
    dead = []
    for q in _sse_clients:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            _sse_clients.remove(q)
        except ValueError:
            pass


async def _start_sse_server():
    """Start lightweight SSE server on port 8889."""
    app = sse_web.Application()
    app.router.add_get('/sse', _sse_handler)
    runner = sse_web.AppRunner(app)
    await runner.setup()
    site = sse_web.TCPSite(runner, '0.0.0.0', 8889)
    await site.start()
    log_info("[SSE] Server started on port 8889")


def _acquire_lock():
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            log_warn("LOCK", f"Old bot running (PID {old_pid}) — killing...")
            os.kill(old_pid, signal.SIGTERM)
            time.sleep(3)
            try:
                os.kill(old_pid, 0)
                os.kill(old_pid, signal.SIGKILL)
                time.sleep(1)
            except ProcessLookupError:
                pass
        except (ProcessLookupError, ValueError):
            pass
    os.makedirs(os.path.dirname(_PID_FILE), exist_ok=True)
    with open(_PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    log_info(f"[LOCK] PID {os.getpid()} acquired")


def _release_lock():
    try:
        if os.path.exists(_PID_FILE):
            with open(_PID_FILE) as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(_PID_FILE)
    except Exception:
        pass


def _signal_handler(signum, frame):
    global _SHUTDOWN
    _SHUTDOWN = True
    log_warn("SHUTDOWN", f"Received signal {signum}")

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)
atexit.register(_release_lock)


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

async def main():
    _acquire_lock()

    container = Container()
    _sync_sport_configs(container)

    # ── 1. AUTH & SESSIONS ────────────────────────────────────────
    from feeds.ps_auth import PSAuth
    ps_auth = PSAuth(provider=config.PS_PROVIDER)
    await ps_auth.init_session()

    # Set config URLs from auth (ps_auth._ps_base is always correct after login)
    if ps_auth._ps_base:
        import config as _cfg
        _cfg.PS_BASE_URL = ps_auth._ps_base
        _cfg.PS_WS_URL = ps_auth._ps_base.replace("https://", "wss://") + "/sports-websocket/ws"
        log_info(f"[MAIN] Config URLs set from auth: {_cfg.PS_BASE_URL}")

    # Load v-hucode
    try:
        import json
        cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'auth', 'cookie.json')
        with open(cookie_path) as f:
            cookie_data = json.load(f)
        ps_auth.v_hucode = cookie_data.get('v_hucode', '950f80013a300a24c8032e374a27995f')
    except Exception:
        ps_auth.v_hucode = '950f80013a300a24c8032e374a27995f'

    # Test PS session
    token = await ps_auth.fetch_token()
    if not token:
        log_warn("AUTH", "Token failed — attempting recovery via provider")
        try:
            await ps_auth.refresh_cookies_via_playwright()
            token = await ps_auth.fetch_token()
        except Exception as e:
            log_error("AUTH", f"Recovery failed: {e}")
    if token:
        log_info(f"[AUTH] PS token OK")
    else:
        log_warn("AUTH", "No PS token — WS will retry in background")

    # Etop session
    from feeds.etopfun_api import load_etop_cookies, create_etop_session, build_etop_headers, EtopfunAPI
    etop_cookies = load_etop_cookies()
    etop_session = create_etop_session(etop_cookies)
    etop_api = EtopfunAPI(etop_session, build_etop_headers())
    if 'DJSP_UUID' in etop_cookies:
        etop_api.set_uuid(etop_cookies['DJSP_UUID'])

    # Etop session immortality
    from feeds.etop_session import EtopSessionManager
    _data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    etop_session_mgr = EtopSessionManager(
        session_file=os.path.join(_data_dir, 'auth', 'session.json'),
        profile_dir=os.path.join(_data_dir, 'playwright_etop_profile'),
        etop_base_url=config.ETOP_BASE_URL,
    )

    # Bootstrap etop session if dead or missing
    try:
        ok = await asyncio.wait_for(etop_api.userconn_check(), timeout=10)
        if not ok:
            log_warn("STARTUP", "Etop session dead — running auto-recovery")
            await etop_session_mgr.auto_recover(etop_api, headless=True)
        else:
            log_info("[STARTUP] Etop session healthy")
    except Exception as e:
        log_warn("STARTUP", f"Etop health check failed ({e}) — running auto-recovery")
        await etop_session_mgr.auto_recover(etop_api, headless=True)

    # Pool estimator
    from core.pool_estimator import PoolEstimator
    pool_estimator = PoolEstimator()
    try:
        await asyncio.wait_for(pool_estimator.load_exchange_db(etop_api), timeout=15)
    except asyncio.TimeoutError:
        log_warn("STARTUP", "load_exchange_db timed out")

    # PS WebSocket feed
    from feeds.ps3838_ws import Pinnacle888LiveFeed
    bus = DataBus()
    live_feed = Pinnacle888LiveFeed(
        token_fetcher=ps_auth.fetch_token,
        cookie_getter=ps_auth.get_cookie,
        bus=bus)
    live_feed._cookie_refresher = lambda: ps_auth.reload_cookie()
    live_feed._auth = ps_auth
    await live_feed.start()

    # Inventory
    from engine.inventory import InventoryManager
    inventory = InventoryManager()
    for _attempt in range(3):
        try:
            await inventory.load_pool(etop_api, force=True)
            if inventory.pool_free_count() > 0:
                break
            log_warn("STARTUP", f"Inventory empty on attempt {_attempt+1}, retrying...")
        except Exception as e:
            log_warn("STARTUP", f"Inventory load failed (attempt {_attempt+1}): {e}")
        await asyncio.sleep(3)
    if inventory.pool_free_count() == 0:
        log_warn("STARTUP", "Inventory still empty after 3 attempts — proceeding anyway")

    # Alias DB
    from matching.alias_db import AliasDB as EvidenceAliasDB
    evidence_db = EvidenceAliasDB()
    evidence_db.load_all_seeds()
    log_info(f"[ALIAS_DB] {evidence_db.get_stats()['total']} aliases loaded")

    # Session tracker
    from core.session_tracker import SessionTracker
    session_tracker = SessionTracker()
    live_feed._session_tracker = session_tracker

    # ── 2. ETOP STATE (shared between collector and pipeline) ────
    # We use a simple shared state for etop data within this process.
    # When we go distributed (VPS), this becomes RedisEtopState.
    etop_parents = []
    etop_listing = {}
    etop_last_fetch = [0.0]
    async def _etop_poller():
        nonlocal etop_parents, etop_listing
        _dead_count = 0
        _DEAD_THRESHOLD = 3   # 3 consecutive low-parent fetches → attempt recovery
        _MIN_PARENTS = 10     # below this = session is degraded
        _recovering = False
        await asyncio.sleep(3)
        while not _SHUTDOWN:
            # Back off while fire_engine is using etop session
            if container.fire_active:
                await asyncio.sleep(0.5)
                continue
            try:
                parents, lookup = await asyncio.wait_for(
                    etop_api.match_list(), timeout=10)
                if parents:
                    etop_parents = parents
                    etop_listing = lookup
                    etop_last_fetch[0] = time.time()
                    container.etop_last_fetch = time.time()
                    container.etop_fetch_count += 1
                    bus.notify('etop')

                # Dead-session detection
                if len(parents) >= _MIN_PARENTS:
                    _dead_count = 0
                else:
                    _dead_count += 1
                    log_warn("ETOP",
                             f"Low parent count: {len(parents)} "
                             f"(dead_count={_dead_count}/{_DEAD_THRESHOLD})")
                    if _dead_count >= _DEAD_THRESHOLD and not _recovering:
                        _recovering = True
                        log_warn("ETOP_SESSION",
                                 "Session appears dead — attempting auto-recovery")
                        try:
                            recovered = await etop_session_mgr.auto_recover(etop_api, headless=True)
                            if recovered:
                                _dead_count = 0
                                log_info("[ETOP_SESSION] Recovery successful")
                            else:
                                log_error("ETOP_SESSION",
                                          "Recovery FAILED after 3 attempts")
                                _dead_count = 0  # reset to avoid immediate retry spam
                        finally:
                            _recovering = False

            except asyncio.TimeoutError:
                log_warn("ETOP", "match_list timed out")
            except Exception as e:
                log_warn("ETOP", f"Fetch failed: {e}")
            await asyncio.sleep(5)

    # ── 3. START BACKGROUND TASKS ─────────────────────────────────
    from feeds.ps3838_rest import search_event

    # PS session manager (existing, proven)
    # Import the original function — it's battle-tested, don't rewrite
    # For now we import from original main if available, or inline
    await _start_sse_server()
    asyncio.create_task(_etop_poller())
    asyncio.create_task(_etop_keepalive(etop_api))
    asyncio.create_task(_ps_session_loop(ps_auth, live_feed, session_tracker))
    asyncio.create_task(cancel_engine.run_loop(
        container, etop_api, inventory,
        listing_getter=lambda: etop_listing))
    asyncio.create_task(_discovery_loop(
        container, live_feed, evidence_db, ps_auth, etop_api))

    # Catch unhandled asyncio task exceptions and alert via Telegram
    def _task_exception_handler(loop, context):
        exc = context.get("exception")
        msg = context.get("message", "Unknown")
        import traceback as _tb
        detail = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))[-800:] if exc else msg
        log_error("TASK_CRASH", detail)
        _send_crash_alert(f"🔴 Task crash: {msg}\n{detail[-600:]}")
    asyncio.get_event_loop().set_exception_handler(_task_exception_handler)

    log_info("CleanFlowBot started. Pipeline: classify → match → valuate → fire → dash")
    from core.notifier import notify
    from core import telegram_bot
    asyncio.create_task(notify("🤖 CleanFlowBot started", debounce_key="bot_start"))
    asyncio.create_task(telegram_bot.poll_loop(container, inventory))
    asyncio.create_task(telegram_bot.health_watchdog_loop(container))
    asyncio.create_task(_log_rotation_loop())

    # ── 4. MAIN PIPELINE LOOP ─────────────────────────────────────
    ps_store = live_feed.event_store  # TheOnlyStore (unchanged)

    _cycle_count = 0

    while not _SHUTDOWN:
        try:
            triggered = await bus.wait(timeout=5.0)
        except Exception:
            triggered = frozenset()

        try:
            _cycle_count += 1
            _t0 = time.time()

            # Update container health (cheap, always do)
            container.ps_ws_connected = live_feed._ws_connected
            container.ps_session_alive = live_feed.session_alive
            container.ps_store_size = ps_store.size
            container.bag_count = inventory.pool_free_count() if inventory.pool_loaded else 0
            container.bag_value = inventory.pool_free_value() if inventory.pool_loaded else 0
            container.last_brain_cycle = time.time()
            container.bus_freshness = bus.freshness()
            container.bus_notify_count = bus.notify_count

            # Refresh inventory (only on etop cycles, not every PS tick)
            etop_triggered = 'etop' in triggered

            if etop_triggered:
                if inventory.needs_refresh(60):
                    await inventory.load_pool(etop_api, force=True)

            # ── PIPELINE (split FULL vs FAST) ─────────────────────
            if etop_triggered or _cycle_count <= 3:
                # FULL: new etop data arrived (or first 3 cycles for warmup)
                classifier.run(container, etop_parents, etop_listing)
                matcher.run(container, ps_store, evidence_db)

            # FAST: recompute EV (PS odds may have changed)
            # On PS-only triggers, only valuate near-close markets (remain ≤ 300s)
            if etop_triggered or _cycle_count <= 3:
                valuator.run(container, ps_store)
            else:
                valuator.run(container, ps_store, fast_remain=300)
            await fire_engine.run(container, ps_store, etop_api, inventory, etop_listing, pool_estimator=pool_estimator)

            # Dashboard: build once, write + push
            dash_data = container.to_dash_state()
            dashboard.run_with_data(dash_data)
            _push_sse(dash_data)

            _elapsed = (time.time() - _t0) * 1000
            if _cycle_count % 50 == 0:
                log_info(f"[PIPELINE] cycle={_cycle_count} "
                         f"{'FULL' if etop_triggered else 'FAST'} "
                         f"{_elapsed:.0f}ms "
                         f"mkts={len(container.markets)} "
                         f"matched={sum(1 for m in container.markets.values() if m.ps_event_id)}")

        except Exception as e:
            log_error("PIPELINE", f"{type(e).__name__}: {e}")
            traceback.print_exc()

    # Shutdown
    log_warn("SHUTDOWN", "Closing connections...")
    try:
        await live_feed.close() if hasattr(live_feed, 'close') else None
        await ps_auth.close()
    except Exception:
        pass
    log_warn("SHUTDOWN", "Done.")


# ═══════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS (kept simple, proven logic)
# ═══════════════════════════════════════════════════════════════════════

async def _log_rotation_loop():
    """Check log file size every hour and rotate if >100MB. No restart needed."""
    from core import logger
    await asyncio.sleep(300)  # first check 5 min after startup
    while not _SHUTDOWN:
        try:
            logger.rotate_if_needed()
        except Exception:
            pass
        await asyncio.sleep(3600)  # then hourly


async def _etop_keepalive(etop_api):
    await asyncio.sleep(60)
    while not _SHUTDOWN:
        try:
            ok = await etop_api.userconn_check()
            if not ok:
                log_warn("ETOP", "keepalive failed")
        except Exception as e:
            log_warn("ETOP", f"keepalive error: {e}")
        await asyncio.sleep(300)


async def _ps_session_loop(ps_auth, live_feed, session_tracker):
    """PS session keepalive — raw HTTP calls (proven from old bot).

    Normal: every 10s keepalive + balance. Save cookies every 5 min.
    Recovery: L1 retry → L2 reload disk → L3 curl_cffi with cooldowns.
    NEVER regrab cookies while session is still fresh.
    """
    from feeds.ps3838_rest import system_status

    fail_count = 0
    MAX_FAILS = 3
    _last_cookie_save = time.time()

    # Recovery state
    L1_MAX = 3
    recovery_fails = 0
    auth_fail_count = 0
    disk_reloaded = False

    # L3 curl_cffi cooldowns
    PW_COOLDOWNS_MIN = [0, 3, 5, 8, 12, 18, 24]
    PW_MAX_CYCLES = 3
    pw_cycle = 0
    pw_idx = 0
    last_pw_at = 0
    restored_at = 0

    await asyncio.sleep(30)

    async def _test_keepalive():
        """Raw HTTP keepalive test. Returns (ok, reason)."""
        try:
            hdrs = ps_auth.build_headers(method="GET")
            ts = int(time.time() * 1000)
            ka_url = f"{ps_auth._ps_base}/member-auth/v2/keep-alive?locale=en_US&_={ts}&withCredentials=true"
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
            return (False, 'network')
        except Exception:
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

    def _restore_session(label):
        nonlocal restored_at
        live_feed.session_alive = True
        _reset_recovery()
        restored_at = time.time()
        ps_auth.invalidate_token_cache()
        log_info(f"[SESSION] SESSION RESTORED ({label})")

    async def _run_curl_cffi():
        try:
            await ps_auth.refresh_cookies_via_playwright()
            log_info("[SESSION] curl_cffi cookie refresh completed")
            if session_tracker:
                try:
                    session_tracker.on_cookie_refresh()
                except Exception:
                    pass
            await asyncio.sleep(2)
            return True
        except Exception as e:
            log_warn("SESSION", f"curl_cffi cookie refresh failed: {e}")
            return False

    while True:
        if _SHUTDOWN:
            return

        if live_feed.session_alive:
            # Sustained health — reset recovery counters after 60s stable
            if restored_at > 0 and time.time() - restored_at > 60:
                _full_reset()

            # ── Normal keepalive (raw HTTP, proven) ────────────
            try:
                hdrs = ps_auth.build_headers(method="GET")
                ts = int(time.time() * 1000)
                post_hdrs = ps_auth.build_headers(method="POST")
                post_hdrs["Content-Type"] = "application/x-www-form-urlencoded"
                post_hdrs["Content-Length"] = "0"

                bal_url = f"{ps_auth._ps_base}/member-service/v2/account-balance?locale=en_US&_={ts}&withCredentials=true"
                async with ps_auth._session.post(bal_url, headers=post_hdrs, timeout=10) as bal_resp:
                    await bal_resp.read()
                    bal_ok = bal_resp.status == 200

                ts = int(time.time() * 1000)
                ka_url = f"{ps_auth._ps_base}/member-auth/v2/keep-alive?locale=en_US&_={ts}&withCredentials=true"
                async with ps_auth._session.get(ka_url, headers=hdrs, timeout=10) as ka_resp:
                    await ka_resp.read()
                    ka_ok = ka_resp.status == 200

                await system_status(ps_auth)

                if ka_ok and bal_ok:
                    fail_count = 0

                    # Save cookies every 5 min (only when healthy)
                    if time.time() - _last_cookie_save > config.COOKIE_REFRESH_INTERVAL:
                        try:
                            ps_auth.save_cookies_to_disk()
                            _last_cookie_save = time.time()
                        except Exception:
                            pass

                    # WS escalation check
                    if hasattr(live_feed, '_ws_needs_recovery') and live_feed._ws_needs_recovery:
                        log_warn("SESSION", "WS token recovery escalated — running curl_cffi")
                        live_feed._ws_needs_recovery = False
                        pw_success = await _run_curl_cffi()
                        if pw_success:
                            ps_auth.invalidate_token_cache()
                            live_feed._token_fail_cycles = 0
                            live_feed._ws_recover_event.set()
                else:
                    fail_count += 1
                    log_warn("SESSION", f"PS keepalive fail ({fail_count}/{MAX_FAILS})")
            except Exception as e:
                fail_count += 1
                log_warn("SESSION", f"PS keepalive error: {e} ({fail_count}/{MAX_FAILS})")

            if fail_count >= MAX_FAILS:
                live_feed.session_alive = False
                live_feed._ws_connected = False
                _reset_recovery()
                log_warn("SESSION", "SESSION DOWN — entering recovery")

        else:
            # ══ RECOVERY MODE ═══════════════════════════════════

            # L1: Retry keepalive
            if recovery_fails < L1_MAX:
                recovery_fails += 1
                ok, reason = await _test_keepalive()
                if ok:
                    _restore_session(f"L1 attempt {recovery_fails}")
                    await asyncio.sleep(10)
                    continue
                if reason == 'auth':
                    auth_fail_count += 1
                log_info(f"[SESSION] L1 {recovery_fails}/{L1_MAX} reason={reason}")
                await asyncio.sleep(10)
                continue

            # L2: Reload disk cookies once
            if not disk_reloaded:
                disk_reloaded = True
                ps_auth.reload_cookie()
                await asyncio.sleep(2)
                ok, reason = await _test_keepalive()
                if ok:
                    _restore_session("L2 disk cookies fresh")
                    await asyncio.sleep(10)
                    continue
                log_warn("SESSION", f"L2 disk cookies stale ({reason})")
                if auth_fail_count < 2:
                    recovery_fails = 0
                    await asyncio.sleep(10)
                    continue

            # L3: curl_cffi with progressive cooldowns
            if pw_cycle >= PW_MAX_CYCLES:
                log_warn("SESSION", "HARD STOP — all recovery exhausted")
                while True:
                    await asyncio.sleep(300)
                    ok, _ = await _test_keepalive()
                    if ok:
                        _restore_session("HARD STOP self-healed")
                        _full_reset()
                        break
                if live_feed.session_alive:
                    continue

            cooldown_sec = PW_COOLDOWNS_MIN[pw_idx] * 60
            elapsed = time.time() - last_pw_at

            if elapsed < cooldown_sec:
                ok, reason = await _test_keepalive()
                if ok:
                    _restore_session(f"L3 self-healed during cooldown")
                    await asyncio.sleep(10)
                    continue
                await asyncio.sleep(10)
                continue

            log_warn("SESSION", f"L3 curl_cffi cycle {pw_cycle+1}/{PW_MAX_CYCLES} step {pw_idx+1}/{len(PW_COOLDOWNS_MIN)}")
            last_pw_at = time.time()
            pw_success = await _run_curl_cffi()

            if pw_success:
                ok, _ = await _test_keepalive()
                if ok:
                    _restore_session(f"L3 cycle {pw_cycle+1} step {pw_idx+1}")
                    await asyncio.sleep(10)
                    continue

            pw_idx += 1
            if pw_idx >= len(PW_COOLDOWNS_MIN):
                pw_idx = 0
                pw_cycle += 1

        await asyncio.sleep(10)


async def _discovery_loop(container, live_feed, evidence_db, ps_auth, etop_api):
    """REST search for UNMATCHED markets not found via WS."""
    from feeds.ps3838_rest import search_event
    from thefuzz import fuzz

    search_tried = {}
    await asyncio.sleep(15)

    while not _SHUTDOWN:
        try:
            unmatched_groups = {}
            for mid, m in container.markets.items():
                if m.ps_event_id is None and m.state == 'UNMATCHED':
                    key = f"{m.team1}|{m.team2}"
                    unmatched_groups.setdefault(key, []).append(m)

            rest_budget = 3
            for key, group in sorted(unmatched_groups.items(),
                                      key=lambda x: min(m.remain for m in x[1])):
                m0 = group[0]
                if m0.remain > 21600:
                    continue
                if key in search_tried and time.time() - search_tried[key] < 300:
                    continue
                if rest_budget <= 0:
                    continue

                search_tried[key] = time.time()
                alias = evidence_db.lookup(m0.team1, m0.sport) if evidence_db else None
                search_name = alias.ps_name if alias else m0.team1

                sr = await search_event(ps_auth, search_name, m0.team2, m0.sport)
                rest_budget -= 2

                if sr:
                    ps_eid = sr['eid']
                    h1 = fuzz.ratio(m0.team1.lower(), sr['home'].lower())
                    a1 = fuzz.ratio(m0.team1.lower(), sr['away'].lower())
                    if h1 >= a1:
                        ps_t1, ps_t2 = sr['home'], sr['away']
                    else:
                        ps_t1, ps_t2 = sr['away'], sr['home']

                    sp_id = {'basketball': 4, 'esports': 12, 'soccer': 29}.get(m0.sport, 29)
                    live_feed.event_store.register_event(ps_eid, ps_t1, ps_t2, sp_id, '', 'search_v2')

                    log_info(f"[DISCOVERY] {m0.team1} vs {m0.team2} → "
                             f"{ps_t1} vs {ps_t2} eid={ps_eid}")

                    for m in group:
                        m.ps_event_id = ps_eid
                        m.ps_name_team1 = ps_t1
                        m.ps_name_team2 = ps_t2
                        m.match_method = 'discovery'
                        m.state = 'MATCHED'

                        em = container._etop_markets.get(m.mid)
                        if em:
                            em.ps_event_id = ps_eid
                            em.ps_name_team1 = ps_t1
                            em.ps_name_team2 = ps_t2

        except Exception as e:
            log_error("DISCOVERY", f"Cycle error: {e}")
        await asyncio.sleep(10)


def _send_crash_alert(msg: str):
    """Send crash notification via stdlib urllib — works even if curl_cffi not loaded."""
    try:
        import urllib.request as _ur, json as _jj
        from core.notifier import TELEGRAM_TOKEN, TELEGRAM_CHAT
        body = _jj.dumps({"chat_id": TELEGRAM_CHAT, "text": msg}).encode()
        req = _ur.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=body, headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        import traceback as _tb
        _send_crash_alert(f"🔴 CleanFlowBot CRASHED:\n{_tb.format_exc()[-1000:]}")
