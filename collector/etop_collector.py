"""
collector/etop_collector.py — Standalone etopfun poller → Redis.

Polls match_list every POLL_INTERVAL seconds.
Writes parents, per-sub listings, active sports, and heartbeat to Redis.
Handles session death via EtopSessionManager auto_recover.

Rate limit testing:
  - Start at 1s (default)
  - On 5 consecutive empty responses: log, back off (double interval)
  - Auto-resume after 60s cooldown at doubled interval
  - Logs "[ETOP] RATE_LIMITED at poll #X" for tracking
"""

import asyncio
import json
import os
import sys
import time

# Allow running from collector/ or project root
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from feeds.etopfun_api import load_etop_cookies, create_etop_session, build_etop_headers, EtopfunAPI
from feeds.etop_session import EtopSessionManager
from collector.redis_config import K

_DATA_DIR = os.path.join(_ROOT, 'data')

# Sport category → PS sport ID
_CAT_TO_SP = {
    # Esports
    'csgo': 12, 'cs2': 12, 'dota2': 12, 'dota': 12, 'lol': 12,
    'valorant': 12, 'overwatch': 12, 'pubg': 12, 'kog': 12,
    'starcraft': 12, 'r6': 12, 'esport': 12,
    # Basketball
    'sports_basketball': 4, 'basketball': 4,
    # Soccer
    'sports_football': 29, 'sports_soccer': 29, 'soccer': 29, 'football': 29,
}
_SOCCER_LEAGUE_KEYS = ('liga', 'premier', 'bundesliga', 'serie', 'ligue',
                        'copa', 'uefa', 'mls', 'champions', 'eredivisie')

_BACKOFF_ERRORS   = 5    # consecutive empty responses before backing off
_BACKOFF_COOLDOWN = 60   # seconds to wait after backing off


def _infer_sports(parents: list) -> set:
    """Map etop parents to PS sport IDs for adaptive WS rotation."""
    active = set()
    for par in parents:
        cat = (par.get('category') or {}).get('type', '').lower()
        league = (par.get('league') or {}).get('name', '').lower()

        sp = _CAT_TO_SP.get(cat)
        if sp:
            active.add(sp)
            continue

        # Soccer by league name
        if any(k in league for k in _SOCCER_LEAGUE_KEYS):
            active.add(29)
        # Basketball by league name
        elif 'nba' in league or 'basketball' in league:
            active.add(4)

    return active or {29, 12, 4}   # default: all sports if no signal


class EtopCollector:

    def __init__(self, redis_client, poll_interval: float = 1.0):
        self.redis = redis_client
        self.poll_interval = poll_interval
        self.consecutive_errors = 0
        self.total_polls = 0
        self.rate_limited_count = 0
        self._api = None
        self._session_mgr = None

    def _build_api(self):
        cookies = load_etop_cookies()
        session = create_etop_session(cookies)
        api = EtopfunAPI(session, build_etop_headers())
        if 'DJSP_UUID' in cookies:
            api.set_uuid(cookies['DJSP_UUID'])
        return api

    async def _recover(self):
        """Attempt session recovery via EtopSessionManager."""
        print(f"[ETOP] Session dead — attempting auto_recover...", flush=True)
        recovered = await self._session_mgr.auto_recover(self._api)
        if recovered:
            print(f"[ETOP] Session recovered", flush=True)
        else:
            print(f"[ETOP] Session recovery FAILED", flush=True)
        return recovered

    async def run(self):
        # Load session
        self._api = self._build_api()
        self._session_mgr = EtopSessionManager(
            session_file=os.path.join(_DATA_DIR, 'auth', 'session.json'),
            profile_dir=os.path.join(_DATA_DIR, 'playwright_etop_profile'),
            etop_base_url='https://www.etopfun.com',
        )

        print(f"[ETOP] Starting — interval={self.poll_interval}s", flush=True)

        while True:
            t0 = time.time()
            try:
                parents, lookup = await asyncio.wait_for(
                    self._api.match_list(), timeout=8)

                if parents:
                    self.consecutive_errors = 0
                    self.total_polls += 1

                    active_sports = _infer_sports(parents)
                    elapsed_ms = int((time.time() - t0) * 1000)

                    pipe = self.redis.pipeline()

                    pipe.set(K.PARENTS, json.dumps(parents),
                             ex=K.TTL_ETOP_DATA)
                    pipe.set(K.ACTIVE_MIDS, json.dumps(list(lookup.keys())),
                             ex=K.TTL_ETOP_DATA)
                    pipe.set(K.ACTIVE_SPORTS, json.dumps(list(active_sports)),
                             ex=K.TTL_ETOP_META)
                    pipe.set(K.LAST_FETCH, str(time.time()),
                             ex=K.TTL_ETOP_META)

                    for mid, sub in lookup.items():
                        pipe.set(K.LISTING.format(mid=mid), json.dumps(sub),
                                 ex=K.TTL_ETOP_DATA)

                    pipe.set(K.HB_ETOP, json.dumps({
                        'ts': time.time(),
                        'parents': len(parents),
                        'subs': len(lookup),
                        'cycle_ms': elapsed_ms,
                        'poll_interval': self.poll_interval,
                        'total_polls': self.total_polls,
                        'errors': self.consecutive_errors,
                        'rate_limited': self.rate_limited_count,
                    }), ex=K.TTL_HB)

                    pipe.execute()

                    if self.total_polls % 100 == 0:
                        print(f"[ETOP] Poll #{self.total_polls}: "
                              f"{len(parents)} parents, {len(lookup)} subs, "
                              f"{elapsed_ms}ms, sports={active_sports}", flush=True)

                else:
                    self.consecutive_errors += 1

                    if self.consecutive_errors >= _BACKOFF_ERRORS:
                        self.rate_limited_count += 1
                        print(
                            f"[ETOP] RATE_LIMITED at poll #{self.total_polls} "
                            f"after {self.total_polls * self.poll_interval:.0f}s "
                            f"at interval {self.poll_interval}s — "
                            f"backing off to {self.poll_interval * 2}s",
                            flush=True)

                        # Check if session is actually dead
                        healthy = await self._session_mgr.is_healthy(self._api)
                        if not healthy:
                            await self._recover()

                        self.poll_interval = min(self.poll_interval * 2, 10.0)
                        self.consecutive_errors = 0
                        print(f"[ETOP] Cooldown {_BACKOFF_COOLDOWN}s...", flush=True)
                        await asyncio.sleep(_BACKOFF_COOLDOWN)
                        continue

            except asyncio.TimeoutError:
                self.consecutive_errors += 1
                print(f"[ETOP] Timeout (#{self.consecutive_errors})", flush=True)
            except Exception as e:
                self.consecutive_errors += 1
                print(f"[ETOP] Error: {e} (#{self.consecutive_errors})", flush=True)
                if self.consecutive_errors >= 10:
                    print("[ETOP] 10 consecutive errors — attempting recovery",
                          flush=True)
                    await self._recover()
                    self.consecutive_errors = 0

            elapsed = time.time() - t0
            await asyncio.sleep(max(0.05, self.poll_interval - elapsed))
