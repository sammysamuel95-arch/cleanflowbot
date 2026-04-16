"""
v2perfectbot — feeds/ps3838_ws.py
Persistent WebSocket connection to PS3838.

Extracted from bot_working.py Pinnacle888LiveFeed class (lines 581-811).
Zero logic changes. Dependencies:
  - config.py for URLs, sport filter, subscription types
  - feeds/ps3838_parse.py for parse_ws_match()
  - core/logger.py for structured logging

Token fetching and cookie management are injected via callables,
so this module has NO dependency on Playwright or browser code.
"""

import asyncio
import json
import os
import uuid
import aiohttp
import time
from typing import Optional, Callable, Awaitable
from curl_cffi.requests import AsyncSession, CurlWsFlag
from curl_cffi.requests.websockets import WebSocketClosed, WebSocketTimeout, WebSocketError

import config
from config import (
    PS_PROXY,
    SPORT_FILTER, SP_ESPORTS, SP_SOCCER, SP_BASKETBALL,
    MK_MAIN, MK_OU, MK_MAPS,
    WS_TOKEN_FILE, DATA_DIR,
)
from feeds.ps3838_parse import parse_ws_match
from core.logger import log_ws, log_extract, log_info, log_error, log_warn
from core.event_store import TheOnlyStore


class Pinnacle888LiveFeed:
    """Persistent WebSocket connection to PS3838.

    Maintains in-memory odds_store and event_map.
    Auto-reconnects on disconnect.

    Extracted from bot_working.py lines 581-811 with zero logic changes.

    Args:
        token_fetcher: async callable that returns a WS token string or None.
        cookie_getter: callable that returns current SESSION_COOKIE string.
    """

    def __init__(self, token_fetcher: Callable[[], Awaitable[Optional[str]]],
                 cookie_getter: Callable[[], str],
                 bus=None):
        self._bus = bus
        self.event_store = TheOnlyStore()
        self.standard_store = self.event_store  # backward compat alias
        self._event_map = {}     # event_id → {home, away, sp, league}
        self._task = None
        self._ready = asyncio.Event()
        self._sub_mk = {}        # subscription_id → mk type
        self._resub_sent_at = {}  # subscription_id → sent timestamp (latency tracking)
        self._token_fetcher = token_fetcher
        self._cookie_getter = cookie_getter
        self._cookie_refresher = None  # set by caller for auto-refresh on 401
        self._cookie_injector = None  # set by caller to inject WS-issued cookies back into auth session
        self._debug_printed = False
        self._ws_connected = False
        self._msg_counts = {"FULL_ODDS": 0, "UPDATE_ODDS": 0, "other": 0}
        self._last_count_log = 0
        self._verify_cache = {}
        self._verify_queue = asyncio.Queue()
        self._ws_ref = None
        self._verify_ids = set()
        self._verify_sp = {}       # verify_id → sp (for parsing)
        self.active_sports = {29, 12, 4}  # start with all active, main loop narrows it
        self.session_alive = True          # False = ALL PS calls stop immediately
        self._ws_start = time.time()  # WS session birth — used for age-based verbose logging
        self._token_fail_cycles = 0       # consecutive REST_ONLY_COOLDOWN cycles from token 403
        self._ws_needs_recovery = False    # escalation flag for session manager
        self._event_map_file = os.path.join(DATA_DIR, "event_map.json")
        self._load_event_map()

    def _load_event_map(self):
        """Load persisted event_map from disk (survives bot restarts)."""
        try:
            with open(self._event_map_file) as f:
                loaded = json.load(f)
            if loaded:
                self._event_map.update(loaded)
                log_info(f"[EVENTMAP] Loaded {len(loaded)} events from disk")
                # Seed TheOnlyStore from persisted event_map
                for eid_str, info in loaded.items():
                    try:
                        eid_int = int(eid_str)
                        self.event_store.register_event(
                            eid_int, info['home'], info['away'],
                            info.get('sp', 0), info.get('league', ''),
                            'disk')
                        # Born stale: matcher won't see until WS confirms alive
                        if eid_int in self.event_store._events:
                            self.event_store._events[eid_int]['last_seen'] = 0
                    except (ValueError, KeyError):
                        pass
        except FileNotFoundError:
            pass
        except Exception as e:
            log_warn("LiveFeed", f"event_map load failed: {e}")

    def _save_event_map(self):
        """Persist event_map to disk on background thread."""
        import threading as _th
        try:
            payload = json.dumps(self._event_map)
        except Exception:
            return

        def _write():
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                tmp = self._event_map_file + '.tmp'
                with open(tmp, 'w') as f:
                    f.write(payload)
                os.replace(tmp, self._event_map_file)
            except Exception:
                pass

        _th.Thread(target=_write, daemon=True).start()

    async def start(self):
        """Start the persistent WS connection in background."""
        self._task = asyncio.create_task(self._run_forever())
        log_info("LiveFeed starting persistent WS...")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=15)
            log_info(f"LiveFeed ready — {len(self._event_map)} events, {self.standard_store.size} lines")
        except asyncio.TimeoutError:
            log_warn("LiveFeed", "WS not ready after 15s — continuing in REST-only mode (WS retrying in background)")

    async def _run_forever(self):
        """Reconnect loop with retry limits.

        After 3 consecutive failures, switch to REST-only mode for 1 hour.
        This prevents detection from hundreds of failed WS attempts.
        A human browser would try 1-2 times then give up or refresh the page.
        """
        delay = 3
        consecutive_401 = 0
        consecutive_fails = 0
        MAX_WS_RETRIES = 3
        REST_ONLY_COOLDOWN = 300  # 5 minutes (was 1 hour — too aggressive)

        while True:
            try:
                await self._connect_and_recv()
                # Success — reset all counters
                delay = 3
                consecutive_401 = 0
                consecutive_fails = 0
                self._token_fail_cycles = 0
                self._ws_needs_recovery = False
            except Exception as e:
                err_str = str(e)
                is_token_err = "Token fetch failed" in err_str
                is_clean_close = "1000" in err_str
                is_401 = "401" in err_str
                is_403 = "403" in err_str

                consecutive_fails += 1

                if is_401:
                    consecutive_401 += 1
                    delay = 20
                    if consecutive_401 >= 3:
                        consecutive_401 = 0
                        if hasattr(self, '_cookie_refresher') and self._cookie_refresher:
                            await self._cookie_refresher()
                elif is_token_err:
                    delay = min(delay * 4, 30)
                elif is_clean_close:
                    delay = 3
                    consecutive_fails = 0  # clean close is not a failure
                    # Token was bound to the dead WS session — get fresh one
                    try:
                        import os as _os
                        _os.remove(WS_TOKEN_FILE)
                        log_ws("ws", "[RECONNECT] Deleted ws_token.json after server 1000 close — will use fresh REST token")
                    except Exception:
                        pass
                else:
                    delay = min(delay * 2, 60)

                log_ws("ws", f"Disconnected: {e}  delay={delay}  retries={consecutive_fails}/{MAX_WS_RETRIES}")
                if consecutive_fails >= 2:
                    try:
                        import asyncio as _asyncio
                        from core.notifier import notify as _notify
                        _asyncio.create_task(_notify(
                            f"⚠️ WS disconnected (retries={consecutive_fails})\n{str(e)[:100]}",
                            debounce_key="ws_disconnect"
                        ))
                    except Exception:
                        pass

                # REST-only mode after too many failures
                if consecutive_fails >= MAX_WS_RETRIES:
                    self._ws_connected = False
                    self._ws_ref = None
                    try:
                        if hasattr(self, '_session_tracker') and self._session_tracker:
                            self._session_tracker.on_ws_disconnect('rest_only_mode')
                    except Exception:
                        pass
                    consecutive_fails = 0

                    # Track token-403 cycles for self-heal + escalation
                    if is_token_err or is_403:
                        self._token_fail_cycles += 1
                        log_warn("ws", f"WS token-fail cycle {self._token_fail_cycles} — REST-only mode for {REST_ONLY_COOLDOWN//60}min")

                        if self._token_fail_cycles == 1:
                            # Cycle 1 done (5min): cheap self-heal — reload cookies + invalidate token cache
                            log_warn("ws", "[WS SELF-HEAL] Token 403 persisting — reloading cookies + invalidating token cache")
                            try:
                                import os as _os
                                from config import WS_TOKEN_FILE
                                try:
                                    _os.remove(WS_TOKEN_FILE)
                                    log_info("[WS] ws_token.json invalidated")
                                except FileNotFoundError:
                                    pass
                                # Trigger cookie refresh via the refresher callback if available
                                if hasattr(self, '_cookie_refresher') and self._cookie_refresher:
                                    await self._cookie_refresher()
                            except Exception as e:
                                log_warn("ws", f"[WS SELF-HEAL] failed: {e}")

                        elif self._token_fail_cycles >= 2:
                            # Cycle 2+ (10min+): escalate to session manager for Playwright
                            if not self._ws_needs_recovery:
                                self._ws_needs_recovery = True
                                log_warn("ws", "[WS ESCALATE] Token 403 persists after self-heal — requesting Playwright recovery from session manager")
                    else:
                        log_warn("ws", f"WS failed {consecutive_fails}x (non-token) — REST-only mode for {REST_ONLY_COOLDOWN//60}min")

                    await asyncio.sleep(REST_ONLY_COOLDOWN)
                    log_info("[WS] REST-only cooldown ended — attempting WS reconnect")
                    delay = 3
                    continue

                await asyncio.sleep(delay)

    def _build_subscribe_body(self, sp: int, mk: int) -> dict:
        """Build SUBSCRIBE body matching Chrome browser exactly.

        Reverse-engineered from Chrome DevTools Network tab.
        Missing these parameters caused PS to send minimal UPDATE_ODDS.
        """
        return {
            "sp": sp,
            "lg": "",
            "ev": "",
            "mk": mk,
            "btg": "1",   # match browser — was "2" for esports, browser sends "1" always
            "ot": 1,
            "d": "",
            "o": 0,
            "l": 100,
            "v": "",
            "lv": "",
            "me": 0,
            "more": False,
            "c": "",
            "cl": 100,   # match browser — was 0 for esports, browser sends 100 always
            "dpJCA": "h1ft",
            "ec": "",
            "g": "QQ==",
            "hle": False,
            "ic": False,
            "ice": False,
            "inl": False,
            "lang": "",
            "locale": "en_US",
            "me01": "",
            "pa": 0,
            "pimo": "" if sp == 12 else "0,1,2",  # esports="", basketball/soccer="0,1,2"
            "pn": -1,
            "pv": 1,
            "tm": 0,
        }

    async def _connect_and_recv(self):
        """Single WS session: connect, subscribe, receive loop.

        Uses curl_cffi Chrome120 for the WS upgrade — same TLS fingerprint as a real
        browser. This lets the bot reconnect after a server-initiated 1000 close, which
        the previous Python websockets library (different TLS) could not do.
        """
        token = await self._token_fetcher()
        if not token:
            raise RuntimeError("Token fetch failed")

        cookie_str = self._cookie_getter()
        ulp = next((v for k, _, v in (p.strip().partition("=")
                    for p in cookie_str.split(";"))
                    if k.strip() == "_ulp"), "")
        ws_uri = f"{config.PS_WS_URL}?token={token}&ulp={ulp}"

        # Fresh curl_cffi session per connection — Chrome120 TLS fingerprint.
        # Load cookies so they are sent in the WS upgrade request automatically,
        # matching what a real Chrome browser does.
        _ws_session = AsyncSession(impersonate="chrome120", proxy=PS_PROXY) if PS_PROXY else AsyncSession(impersonate="chrome120")
        for part in cookie_str.split(';'):
            part = part.strip()
            if '=' in part:
                k, _, v = part.partition('=')
                k, v = k.strip(), v.strip()
                if k and v:
                    _ws_session.cookies.set(k, v, domain='pinnacle888.com')

        async def _send(ws, msg_dict):
            await ws.send(json.dumps(msg_dict).encode(), CurlWsFlag.TEXT)

        ws = await _ws_session.ws_connect(
            ws_uri,
            headers={
                "Origin": config.PS_BASE_URL,
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
            autoclose=True,
        )
        try:
            self._ws_ref = ws

            # Wait for CONNECTED message
            while True:
                data, flags = await ws.recv(timeout=30)
                if flags & CurlWsFlag.CLOSE:
                    raise RuntimeError("Server closed WS before CONNECTED (1000)")
                msg = json.loads(data.decode())
                if msg.get("type") == "CONNECTED":
                    log_ws("ws", f"[CONNECTED] full body: {json.dumps(msg)[:500]}")
                    break

            self._ws_start = time.time()  # track WS session age
            # LEFT_MENU subscription — browser sends this first
            await _send(ws, {
                "type": "SUBSCRIBE",
                "destination": "LEFT_MENU",
                "body": {"c": "US", "view": "COMPACT", "dpJCA": "h1ft", "locale": "en_US"}
            })
            await asyncio.sleep(0.2)

            # Subscribe: mk=1 + mk=2 for all sports, mk=3 for esports
            for sp in SPORT_FILTER:
                for mk in [MK_MAIN, MK_OU]:
                    sub_id = str(uuid.uuid4())
                    self._sub_mk[sub_id] = (sp, mk)
                    await _send(ws, {
                        "type": "SUBSCRIBE", "destination": "ODDS",
                        "id": sub_id,
                        "body": self._build_subscribe_body(sp, mk)
                    })
                    await asyncio.sleep(0.2)

            # Esports map markets (mk=3)
            sub_id = str(uuid.uuid4())
            self._sub_mk[sub_id] = (SP_ESPORTS, MK_MAPS)
            await _send(ws, {
                "type": "SUBSCRIBE", "destination": "ODDS",
                "id": sub_id,
                "body": self._build_subscribe_body(SP_ESPORTS, MK_MAPS)
            })
            await asyncio.sleep(0.2)

            # Esports mk=1: series ML/HDP (LoL, Valorant etc)
            sub_id_e1 = str(uuid.uuid4())
            self._sub_mk[sub_id_e1] = (SP_ESPORTS, 1)
            await _send(ws, {
                "type": "SUBSCRIBE", "destination": "ODDS",
                "id": sub_id_e1,
                "body": self._build_subscribe_body(SP_ESPORTS, 1)
            })
            await asyncio.sleep(0.2)

            log_ws("ws", "Connected, subscriptions sent")
            self._ws_connected = True
            try:
                if hasattr(self, '_session_tracker') and self._session_tracker:
                    self._session_tracker.on_ws_connect()
            except Exception:
                pass
            resub_task = asyncio.create_task(self._resub_loop(ws))

            try:
                # Receive loop
                while True:
                    # Restart resub task if it died unexpectedly
                    if resub_task.done() and not resub_task.cancelled():
                        log_warn("ps_ws", "[RESUB] resub task exited — restarting")
                        resub_task = asyncio.create_task(self._resub_loop(ws))
                    try:
                        data, flags = await ws.recv(timeout=30)
                    except WebSocketTimeout:
                        await _send(ws, {"type": "PONG", "destination": "ALL"})
                        log_ws("ws", f"[HB] WS alive  store={self.standard_store.size}  events={len(self._event_map)}")
                        continue
                    except (WebSocketClosed, WebSocketError) as e:
                        raise RuntimeError(f"WS error: {e}")

                    if flags & CurlWsFlag.CLOSE:
                        raise RuntimeError("received 1000 (OK); then sent 1000 (OK)")

                    msg = json.loads(data.decode())

                    if msg.get("type") == "PING":
                        await _send(ws, {"type": "PONG", "destination": "ALL"})
                        continue
                    if msg.get("type") not in ("FULL_ODDS", "UPDATE_ODDS"):
                        mtype = msg.get("type", "?")
                        self._msg_counts[mtype] = self._msg_counts.get(mtype, 0) + 1
                        ws_age_min = (time.time() - getattr(self, '_ws_start', time.time())) / 60
                        if ws_age_min >= 55 or self._msg_counts[mtype] <= 3:
                            log_ws("ws", f"[MSG] type={mtype} age={ws_age_min:.1f}min body={json.dumps(msg)[:300]}")
                        continue

                    msg_id = msg.get("id", "")

                    if msg.get("type") == "FULL_ODDS" and msg_id in self._verify_ids:
                        await self._verify_queue.put(msg)
                        sp = self._verify_sp.get(msg_id, 12)
                        store_copy = dict(msg)
                        store_copy["type"] = "UPDATE_ODDS"
                        self._process_msg(store_copy, msg_sp=sp, msg_mk=3)
                        self._verify_ids.discard(msg_id)
                        continue

                    sub_info = self._sub_mk.get(msg_id, (None, None))
                    if isinstance(sub_info, tuple):
                        msg_sp, msg_mk = sub_info
                    else:
                        msg_sp, msg_mk = None, sub_info  # backward compat

                    # Latency tracking for resub cycles
                    if msg_id in self._resub_sent_at and msg.get("type") == "FULL_ODDS":
                        latency_ms = (time.time() - self._resub_sent_at.pop(msg_id)) * 1000
                        log_ws("ws", f"[RESUB_LAT] sp={msg_sp} mk={msg_mk} latency={latency_ms:.0f}ms")
                    # ONE-TIME DEBUG: log raw mk=3 match structure
                    if msg_mk == 3 and not getattr(self, '_mk3_logged', False):
                        self._mk3_logged = True
                        try:
                            _raw = msg.get('l', msg.get('n', []))
                            if _raw and isinstance(_raw, list):
                                for _blk in _raw:
                                    if isinstance(_blk, list) and len(_blk) > 2:
                                        for _league in (_blk[2] or [])[:1]:
                                            if isinstance(_league, list) and len(_league) > 2:
                                                for _match in (_league[2] or [])[:1]:
                                                    if isinstance(_match, list) and len(_match) > 8:
                                                        _odds = _match[8] if isinstance(_match[8], dict) else None
                                                        pass
                        except Exception:
                            pass
                    self._process_msg(msg, msg_sp=msg_sp, msg_mk=msg_mk)

                    if not self._ready.is_set() and self._event_map:
                        self._ready.set()
            finally:
                self._ws_connected = False
                try:
                    if hasattr(self, '_session_tracker') and self._session_tracker:
                        self._session_tracker.on_ws_disconnect('ws_closed')
                except Exception:
                    pass
                self._ws_ref = None
                resub_task.cancel()
                try:
                    await resub_task
                except asyncio.CancelledError:
                    pass
        finally:
            await _ws_session.close()

    # ── WS periodic resub ─────────────────────────────────────────────────────

    async def _resub_loop(self, ws):
        """Rotate through active sports. Random 40-50s between steps.

        Bundled: esports sends mk=1 + mk=3 together.
        Adaptive: skips sports with no etop markets < 3h.
        active_sports is set by main loop every cycle.

        Normal cycle: ~3s (3 sports × 1s)
        If a sport is inactive: skip it, move to next immediately.
        """
        import random

        steps = {
            29: [(SP_SOCCER, 1)],
            12: [(SP_ESPORTS, 1), (SP_ESPORTS, 3)],
            4:  [(SP_BASKETBALL, 1)],
        }
        sport_order = [29, 12, 4]
        idx = 0
        await asyncio.sleep(60)  # let initial subs settle

        while True:
            if not self._ws_connected or not self.session_alive:
                # Wait briefly — could be a transient flag flip during reconnect
                await asyncio.sleep(5)
                if not self._ws_connected or not self.session_alive:
                    log_warn("ps_ws", "[RESUB] WS disconnected or session dead — exiting resub loop")
                    return

            sp_key = sport_order[idx % len(sport_order)]
            idx += 1

            if sp_key not in self.active_sports:
                log_ws("ws", f"[RESUB] sp={sp_key} — no active markets, skipping")
                await asyncio.sleep(5)
                continue

            step = steps[sp_key]
            for sp, mk in step:
                sub_id = f"resub_{sp}_{mk}_{int(time.time())}"
                self._sub_mk[sub_id] = (sp, mk)
                self._resub_sent_at[sub_id] = time.time()
                try:
                    await ws.send(json.dumps({
                        "type": "SUBSCRIBE",
                        "destination": "ODDS",
                        "id": sub_id,
                        "body": self._build_subscribe_body(sp, mk)
                    }).encode(), CurlWsFlag.TEXT)
                    log_ws("ws", f"[RESUB] sp={sp} mk={mk} id={sub_id}")
                except Exception as e:
                    log_warn("ps_ws", f"[RESUB] send failed: {e}")
                    return  # WS dead — exit, reconnect handles retry

                if len(step) > 1:
                    await asyncio.sleep(0.5)

            await asyncio.sleep(1)

    # ── Message processing ─────────────────────────────────────────────────────

    def _process_msg(self, msg, msg_sp=None, msg_mk=None):
        """Process FULL_ODDS or UPDATE_ODDS message.

        FULL_ODDS = complete snapshot → clean up stale lines after processing.
        UPDATE_ODDS = incremental delta → only touch changed lines.
        Cleanup scoped by BOTH sport AND mk to prevent cross-sport wipes.
        """
        if msg_mk == 3:
            _mk3_count = getattr(self, '_mk3_process_count', 0) + 1
            self._mk3_process_count = _mk3_count
            if _mk3_count <= 3:
                _body = msg.get('body', msg)
                _raw = _body.get('l', _body.get('n', [])) if isinstance(_body, dict) else []
                _match_count = 0
                if isinstance(_raw, list):
                    for _blk in _raw:
                        if isinstance(_blk, list) and len(_blk) > 2:
                            for _lg in (_blk[2] or []):
                                if isinstance(_lg, list) and len(_lg) > 2:
                                    _match_count += len(_lg[2] or [])
        is_full = msg.get("type") == "FULL_ODDS"

        # Count all message types
        mtype = msg.get("type", "other")
        self._msg_counts[mtype] = self._msg_counts.get(mtype, 0) + 1
        now_t = time.time()
        if now_t - self._last_count_log > 60:  # log every 60s
            self._last_count_log = now_t
            log_extract("ps3838_ws", f"WS msg counts: {self._msg_counts} | store={self.standard_store.size}")

        # Debug first message
        if not self._debug_printed:
            self._debug_printed = True
            log_extract("ps3838_ws", f"First WS msg: type={msg.get('type')} mk={msg_mk}")

        odds = msg.get("odds") or {}
        for section in ("l", "n"):   # l=live, n=prematch
            for block in odds.get(section) or []:
                if not isinstance(block, list) or len(block) < 3:
                    continue
                sp_id = block[0] if isinstance(block[0], int) else None
                if sp_id not in SPORT_FILTER:
                    continue
                for league_block in (block[2] or []):
                    if not isinstance(league_block, list) or len(league_block) < 3:
                        continue
                    league_name = next(
                        (league_block[i] for i in range(min(2, len(league_block)))
                         if isinstance(league_block[i], str) and league_block[i]), "")
                    for match in (league_block[2] or []):
                        mkts, eid, home, away = parse_ws_match(
                            match, sp_id, league_name, msg_mk=msg_mk)

                        # WRITE-ONCE identity: lock home/away on first sight
                        if eid and eid in self._event_map:
                            locked = self._event_map[eid]
                            locked_h = locked.get('home', '').strip().lower()
                            locked_a = locked.get('away', '').strip().lower()
                            parsed_h = home.strip().lower()
                            parsed_a = away.strip().lower()
                            # If this message has teams reversed from locked order, swap in each market dict
                            if parsed_h == locked_a and parsed_a == locked_h:
                                home, away = locked['home'], locked['away']  # use locked names
                                for m in mkts:
                                    # Swap fair_home/fair_away
                                    m['fair_home'], m['fair_away'] = m.get('fair_away', 0), m.get('fair_home', 0)
                                    # Swap home_odds/away_odds
                                    m['home_odds'], m['away_odds'] = m.get('away_odds', 0), m.get('home_odds', 0)
                                    # Swap home_hdp sign for HDP markets
                                    if m.get('market') == 'hdp' and 'home_hdp' in m:
                                        m['home_hdp'] = -m['home_hdp']
                                    # Fix team names in market dict
                                    m['home'] = home
                                    m['away'] = away
                        elif eid:
                            self._event_map[eid] = {
                                "home": home, "away": away,
                                "sp": sp_id, "league": league_name
                            }
                            self._save_event_map()
                            # Register in TheOnlyStore (single source of truth)
                            self.event_store.register_event(
                                eid, home, away, sp_id, league_name, 'ws_menu')

                        for m in mkts:
                            self._feed_standard_store(m, sp_id, msg_mk or 0)

        # Wake pipeline with fresh PS data — once per WS message, after all writes
        if self._bus:
            self._bus.notify('ps3838')


    def _feed_standard_store(self, m: dict, sp: int, mk: int):
        """Shadow-write one market dict into StandardStore.

        Writes one parsed market dict into TheOnlyStore.
        """
        mkt = m.get('market')
        eid = m.get('event_id')
        mp = m.get('map_num', 0)
        home = m.get('home', '')
        away = m.get('away', '')
        if not eid or not home or not away:
            return
        try:
            line_id = m.get('line_id')
            if mkt == 'ml':
                self.event_store.update_ml(
                    eid, mp, home, away,
                    m['home_odds'], m['away_odds'], 'ws', sp, mk,
                    line_id=line_id)
            elif mkt == 'hdp':
                self.event_store.update_hdp(
                    eid, mp, home, away,
                    m['home_hdp'], m['home_odds'], m['away_odds'], 'ws', sp, mk,
                    line_id=line_id)
            elif mkt in ('ou', 'team_total'):
                self.event_store.update_ou(
                    eid, mp, m['total'],
                    m['over_odds'], m['under_odds'], home, away, 'ws', sp, mk,
                    line_id=line_id)
        except Exception as e:
            from core.logger import log_warn
            log_warn("FEED", f"StandardStore feed failed: {e} | market={m.get('market')} home={m.get('home')} eid={m.get('event_id')}")

    # ── Store mutations ────────────────────────────────────────────────────────

    def merge_rest_markets(self, markets: list, sp_id: int = None, mk: int = None):
        """Merge REST markets into TheOnlyStore."""
        _hdp_count = sum(1 for m in markets if m.get('market') == 'hdp')
        _ou_count = sum(1 for m in markets if m.get('market') == 'ou')
        _maps = set(m.get('map_num', -1) for m in markets if m.get('market') in ('hdp', 'ou'))
        if _hdp_count or _ou_count:
            from core.logger import log_info
            log_info(f"[REST_MERGE] hdp={_hdp_count} ou={_ou_count} maps={sorted(_maps)} total={len(markets)}")
        for m in markets:
            self._feed_standard_store(m, sp_id or 0, mk or 0)

        # REST is authoritative — correct event_map lock if needed
        for m in markets:
            eid = m.get('event_id')
            if eid:
                self._event_map[eid] = {
                    "home": m['home'], "away": m['away'],
                    "sp": sp_id or self._event_map.get(eid, {}).get('sp', 0),
                    "league": self._event_map.get(eid, {}).get('league', ''),
                }
                self._save_event_map()
                self.event_store.register_event(
                    eid, m['home'], m['away'],
                    sp_id or self._event_map.get(eid, {}).get('sp', 0),
                    self._event_map.get(eid, {}).get('league', ''),
                    'rest')
                break  # all markets in same call share same event

    # ── Store queries ──────────────────────────────────────────────────────────

    def is_ws_connected(self) -> bool:
        """Check if WS is currently connected. 0ms — just reads a boolean."""
        return self._ws_connected

    async def resub_verify(self, sp, mk):
        """Send fresh SUBSCRIBE, wait for FULL_ODDS, parse and return.

        Returns: dict of (home, away, market, map_num, line) → market_dict
                 or None on failure/timeout

        Cache: same sp+mk within 3 seconds returns cached result.
        """
        import uuid as _uuid
        cache_key = (sp, mk)
        now = time.time()

        if cache_key in self._verify_cache:
            cached_time, cached_data = self._verify_cache[cache_key]
            if now - cached_time < 3:
                return cached_data

        if not self._ws_ref or not self._ws_connected:
            return None

        verify_id = str(_uuid.uuid4())
        msg = json.dumps({
            'type': 'SUBSCRIBE',
            'destination': 'ODDS',
            'id': verify_id,
            'body': self._build_subscribe_body(sp, mk)
        })
        try:
            self._verify_sp[verify_id] = sp
            self._verify_ids.add(verify_id)
            await self._ws_ref.send(msg.encode(), CurlWsFlag.TEXT)
        except Exception:
            self._verify_ids.discard(verify_id)
            self._verify_sp.pop(verify_id, None)
            return None

        result = await self._wait_for_full_odds(verify_id, timeout=3.0)

        if result is not None:
            self._verify_cache[cache_key] = (now, result)

        return result

    async def _wait_for_full_odds(self, verify_id, timeout=3.0):
        """Wait for FULL_ODDS message with matching id. Parse and return line dict."""
        from feeds.ps3838_parse import parse_ws_match
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                evt = await asyncio.wait_for(self._verify_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if evt.get('id') != verify_id:
                continue
            # Parse the snapshot
            result = {}
            odds = evt.get('odds', {})
            sp_for_parse = self._verify_sp.get(verify_id, 12)

            for section in ('l', 'n'):
                for block in (odds.get(section) or []):
                    if not isinstance(block, list) or len(block) < 3:
                        continue
                    for lb in (block[2] or []):
                        if not isinstance(lb, list) or len(lb) < 3:
                            continue
                        league = ''
                        for item in lb[:3]:
                            if isinstance(item, str):
                                league = item
                                break
                        for match in (lb[2] if isinstance(lb[2], list) else []):
                            try:
                                mkts, eid, home, away = parse_ws_match(match, sp_for_parse, league)
                                h = home.strip().lower()
                                a = away.strip().lower()
                                for mkt in mkts:
                                    market = mkt.get('market', '')
                                    map_num = mkt.get('map_num', 0)
                                    if market == 'ml':
                                        line = 0.0
                                    elif market == 'hdp':
                                        line = mkt.get('home_hdp')
                                    else:
                                        line = mkt.get('total')
                                    key = (h, a, market, map_num, line)
                                    result[key] = mkt
                                    # Also store reversed key
                                    key2 = (a, h, market, map_num, line)
                                    result[key2] = mkt
                            except Exception:
                                continue
            return result
        return None


