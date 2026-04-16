"""
collector/ps_collector.py — Standalone PS3838 WS collector → Redis.

Runs the proven Pinnacle888LiveFeed + TheOnlyStore unchanged.
Syncs store state to Redis after every batch of WS updates.
Reads etop:active_sports from Redis for adaptive sport rotation.

Session keepalive: calls the keep-alive endpoint directly (ps_auth
does not expose keep_alive() as a method — implemented inline here).
"""

import asyncio
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from feeds.ps3838_ws import Pinnacle888LiveFeed
from feeds.ps3838_auth import PS3838Auth
from collector.redis_config import K

import config

_SYNC_MIN_INTERVAL = 1.0   # don't flood Redis faster than this
_KA_INTERVAL       = 10    # keepalive every 10s
_ADAPT_INTERVAL    = 5     # read etop:active_sports from Redis every 5s


class PSCollector:

    def __init__(self, redis_client):
        self.redis = redis_client
        self.live_feed: Pinnacle888LiveFeed = None
        self.ps_auth: PS3838Auth = None
        self._sync_count = 0
        self._last_sync = 0.0

    async def run(self):
        # ── Auth + feed init ─────────────────────────────────────────────
        self.ps_auth = PS3838Auth()
        await self.ps_auth.init_session()

        self.live_feed = Pinnacle888LiveFeed(
            token_fetcher=self.ps_auth.fetch_token,
            cookie_getter=self.ps_auth.get_cookie,
        )
        self.live_feed._cookie_refresher = lambda: self.ps_auth.reload_cookie()

        # ── Hook: sync to Redis after each WS message batch ──────────────
        # Wrap _process_msg — if the store gained entries, sync.
        original_process = self.live_feed._process_msg

        def _hooked_process(msg, msg_sp=None, msg_mk=None):
            before = self.live_feed.event_store._updates
            original_process(msg, msg_sp=msg_sp, msg_mk=msg_mk)
            if self.live_feed.event_store._updates > before:
                now = time.time()
                if now - self._last_sync >= _SYNC_MIN_INTERVAL:
                    self._sync_to_redis()
                    self._last_sync = now

        self.live_feed._process_msg = _hooked_process

        # ── Start WS ─────────────────────────────────────────────────────
        print("[PS] Starting WS feed...", flush=True)
        await self.live_feed.start()

        # ── Background tasks ─────────────────────────────────────────────
        asyncio.create_task(self._adaptive_rotation_loop())
        asyncio.create_task(self._keepalive_loop())
        asyncio.create_task(self._periodic_sync_loop())

        # ── Main loop: keep process alive ────────────────────────────────
        while True:
            await asyncio.sleep(30)

    # ── Adaptive sport rotation ───────────────────────────────────────────

    async def _adaptive_rotation_loop(self):
        """Read etop:active_sports from Redis every 5s and update WS."""
        while True:
            try:
                raw = self.redis.get(K.ACTIVE_SPORTS)
                if raw:
                    sports_list = json.loads(raw)
                    if sports_list:
                        new_sports = set(int(s) for s in sports_list)
                        if new_sports != self.live_feed.active_sports:
                            self.live_feed.active_sports = new_sports
                            print(f"[PS] Active sports updated: {new_sports}",
                                  flush=True)
                # If no etop data, keep current active_sports (default {29,12,4})
            except Exception as e:
                print(f"[PS] Adaptive rotation error: {e}", flush=True)
            await asyncio.sleep(_ADAPT_INTERVAL)

    # ── Keepalive ─────────────────────────────────────────────────────────

    async def _keepalive_loop(self):
        """Ping PS keep-alive endpoint every 10s."""
        while True:
            await asyncio.sleep(_KA_INTERVAL)
            try:
                hdrs = self.ps_auth.build_headers(method="GET")
                ts = int(time.time() * 1000)
                ka_url = (f"{config.PS_BASE_URL}/member-auth/v2/keep-alive"
                          f"?locale=en_US&_={ts}&withCredentials=true")
                async with self.ps_auth._session.get(
                        ka_url, headers=hdrs, timeout=10) as resp:
                    if resp.status == 200:
                        self.live_feed.session_alive = True
                    else:
                        print(f"[PS] Keep-alive {resp.status}", flush=True)
                        if resp.status in (401, 403):
                            self.live_feed.session_alive = False
            except Exception as e:
                print(f"[PS] Keep-alive error: {e}", flush=True)

    # ── Periodic full sync ────────────────────────────────────────────────

    async def _periodic_sync_loop(self):
        """Full store sync every 10s regardless of WS activity."""
        while True:
            await asyncio.sleep(10)
            self._sync_to_redis()

    # ── Redis sync ────────────────────────────────────────────────────────

    def _sync_to_redis(self):
        store = self.live_feed.event_store
        pipe = self.redis.pipeline()
        now = time.time()

        # Odds buckets
        for (eid, m), bucket in store._data.items():
            serialized = self._serialize_bucket(bucket)
            pipe.set(K.PS_ODDS.format(eid=eid, m=m),
                     json.dumps(serialized), ex=K.TTL_PS_ODDS)

        # Events
        for eid, ev in store._events.items():
            pipe.set(K.PS_EVENT.format(eid=eid), json.dumps({
                'home':      ev['home'],
                'away':      ev['away'],
                'sp':        ev['sp'],
                'league':    ev.get('league', ''),
                'has_odds':  ev.get('has_odds', False),
                'last_seen': ev.get('last_seen', 0),
            }), ex=K.TTL_PS_EVENT)

        # Events by sport index
        by_sport: dict = {}
        for eid, ev in store._events.items():
            by_sport.setdefault(ev['sp'], []).append(eid)
        for sp, eids in by_sport.items():
            pipe.set(K.PS_EVENTS_SP.format(sp=sp),
                     json.dumps(eids), ex=300)

        # Team names
        for eid, (home, away) in store._event_teams.items():
            pipe.set(K.PS_TEAMS.format(eid=eid),
                     json.dumps({'home': home, 'away': away}),
                     ex=K.TTL_PS_EVENT)

        # Heartbeat
        pipe.set(K.HB_PS, json.dumps({
            'ts':           now,
            'ws_connected': self.live_feed._ws_connected,
            'store_size':   store.size,
            'events':       len(store._events),
            'syncs':        self._sync_count,
            'active_sports': list(self.live_feed.active_sports),
        }), ex=K.TTL_HB)

        pipe.execute()
        self._sync_count += 1

        if self._sync_count % 50 == 0:
            print(f"[PS] Sync #{self._sync_count}: "
                  f"{len(store._events)} events, "
                  f"{len(store._data)} buckets, "
                  f"ws={self.live_feed._ws_connected}", flush=True)

    def _serialize_bucket(self, bucket: dict) -> dict:
        result = {}
        for mkt in ('ml', 'hdp', 'ou', 'team_total'):
            entries = bucket.get(mkt, {})
            serialized = {}
            for key, entry in entries.items():
                str_key = '|'.join(str(k) for k in key) if isinstance(key, tuple) else str(key)
                serialized[str_key] = {
                    'raw':  entry.raw,
                    'fair': entry.fair,
                    'ts':   entry.timestamp,
                    'src':  entry.source,
                }
            result[mkt] = serialized
        return result
