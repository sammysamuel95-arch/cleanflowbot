"""
core/event_store.py — Single source of truth for PS3838 events.

Wraps StandardStore (proven pricing logic, untouched).
Adds identity layer: eid → {home, away, sp, league, has_odds}.
Evidence matching reads from here. eid travels straight through to firing.

Design reviewed by ChatGPT (9/10). Fixes identity fragmentation that caused
silent drops across 5 stores (event_map, _odds_store, standard_store, pair_cache, tracked).

WRITE: register_event (identity), update_ml/hdp/ou (pricing → delegates)
READ:  get_events_for_matching (evidence), get_event (identity), get_*_fair (pricing → delegates)
CHECK: has_odds, has_ml/hdp/ou (→ delegates)
"""

import time
from typing import Optional, Dict, Tuple
from core.factory import StandardStore
from core.logger import log_info, log_warn


class MatchCache:
    """TTL cache: (etop_team1, etop_team2) → match result.

    Replaces pair_cache. Persistent within session (not per-cycle).
    Prevents re-matching every 60s cycle for known pairs.
    """

    def __init__(self, ttl: float = 60.0):
        self._cache: Dict[Tuple[str, str], dict] = {}
        self._ttl = ttl

    def get(self, t1: str, t2: str) -> Optional[dict]:
        key = (t1.strip().lower(), t2.strip().lower())
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() - entry['ts'] > self._ttl:
            del self._cache[key]
            return None
        return entry

    def set(self, t1: str, t2: str, eid: int,
            ps_name_t1: str, ps_name_t2: str,
            confidence: float, method: str):
        key = (t1.strip().lower(), t2.strip().lower())
        self._cache[key] = {
            'eid': eid,
            'ps_name_t1': ps_name_t1,
            'ps_name_t2': ps_name_t2,
            'confidence': confidence,
            'method': method,
            'ts': time.time(),
        }

    def clear(self):
        self._cache.clear()

    @property
    def size(self):
        return len(self._cache)


class TheOnlyStore(StandardStore):
    """Single source of truth for PS3838 event identity + pricing.

    Identity: eid → {home, away, sp, league, sources, has_odds, first_seen}
    Pricing: delegates to StandardStore (zero changes to proven code)

    Usage:
        store = TheOnlyStore()
        # WS feeds identity
        store.register_event(eid, home, away, sp, league, 'ws_menu')
        # WS feeds pricing (auto-registers if needed)
        store.update_ml(eid, m, home, away, h_odds, a_odds, 'ws', sp, mk)
        # Evidence matching reads all events
        events = store.get_events_for_matching('esports')
        # Discovery passes eid straight to _build_etop_market
        # find_line and compute_ev read pricing via delegation
    """

    def __init__(self):
        super().__init__()  # StandardStore init (_data, _event_teams, _updates)
        self._events: Dict[int, dict] = {}
        self.match_cache = MatchCache(ttl=60.0)
        self._line_ids: Dict[tuple, int] = {}  # (eid, map, market, line_val) → line_id from WS

    # ── Identity writes ──────────────────────────────────────────────

    def register_event(self, eid: int, home: str, away: str,
                       sp: int, league: str, source: str = 'ws_menu'):
        """Register event identity. Write-once for team names.

        Called by: WS LEFT_MENU, WS FULL_ODDS (via _ensure_registered), REST.
        Safe to call multiple times — only first call sets names.
        """
        if eid in self._events:
            self._events[eid]['sources'].add(source)
            if source == 'rest' and league:
                self._events[eid]['league'] = league
            return

        self._events[eid] = {
            'home': home,
            'away': away,
            'sp': sp,
            'league': league,
            'sources': {source},
            'first_seen': time.time(),
            'last_seen': time.time(),
            'has_odds': False,
        }

    # ── Pricing writes (override to add identity tracking) ───────────

    def update_ml(self, eid, m, home, away, h_odds, a_odds,
                  src='ws', sp=0, mk=0, line_id=None):
        self._ensure_registered(eid, home, away, sp, src)
        self._mark_has_odds(eid)
        if line_id is not None:
            self._line_ids[(eid, m, 'ml', 0)] = line_id
        super().update_ml(eid, m, home, away, h_odds, a_odds, src, sp, mk)

    def update_hdp(self, eid, m, home, away, h_hdp, h_odds, a_odds,
                   src='ws', sp=0, mk=0, line_id=None):
        self._ensure_registered(eid, home, away, sp, src)
        self._mark_has_odds(eid)
        if line_id is not None:
            self._line_ids[(eid, m, 'hdp', h_hdp)] = line_id
        super().update_hdp(eid, m, home, away, h_hdp, h_odds, a_odds,
                           src, sp, mk)

    def update_ou(self, eid, m, total, o_odds, u_odds,
                  home='', away='', src='ws', sp=0, mk=0, line_id=None):
        if eid in self._events:
            self._mark_has_odds(eid)
        if line_id is not None:
            self._line_ids[(eid, m, 'ou', total)] = line_id
        super().update_ou(eid, m, total, o_odds, u_odds,
                          home, away, src, sp, mk)

    def _ensure_registered(self, eid, home, away, sp, source):
        if eid not in self._events:
            self.register_event(eid, home, away, sp, '', source)

    def _mark_has_odds(self, eid):
        if eid in self._events:
            ev = self._events[eid]
            ev['has_odds'] = True
            ev['last_seen'] = time.time()

    # ── Evidence matching interface ──────────────────────────────────

    def get_events_for_matching(self, sport_hint: str = None, max_age_hours: float = 6.0) -> list:
        """Recent events for evidence matching. Returns [(home, away, eid, league), ...].

        Filtered by sport + age. Only returns events seen in the last max_age_hours.
        Prevents matching against stale events from days ago.
        """
        sp_filter = {'esports': 12, 'soccer': 29, 'basketball': 4}.get(sport_hint)
        cutoff = time.time() - (max_age_hours * 3600)
        result = []
        for eid, ev in self._events.items():
            if ev.get('last_seen', 0) < cutoff:
                continue
            if sp_filter and ev['sp'] != sp_filter:
                continue
            result.append((ev['home'], ev['away'], eid, ev.get('league', '')))
        return result

    def find_event_id_any(self, ps_home: str, ps_away: str) -> Optional[int]:
        """Like find_event_id but also searches _events (REST-registered, no WS odds yet).

        Use when WS hasn't sent odds for a known event — e.g. kills event seen in REST
        listing but not yet broadcast by WS. Returns eid or None.
        """
        # Try _event_teams first (has WS odds)
        eid = self.find_event_id(ps_home, ps_away)
        if eid:
            return eid
        # Fall back to _events (REST-registered, may have no WS odds yet)
        h, a = self._n(ps_home), self._n(ps_away)
        for eid, ev in self._events.items():
            eh, ea = self._n(ev['home']), self._n(ev['away'])
            if (eh == h and ea == a) or (eh == a and ea == h):
                return eid
        return None

    def get_event(self, eid: int) -> Optional[dict]:
        """Full event identity. Returns None if not registered."""
        return self._events.get(eid)

    def has_odds(self, eid: int) -> bool:
        """Does this event have ANY pricing data?"""
        ev = self._events.get(eid)
        return ev['has_odds'] if ev else False

    def get_line_id(self, eid: int, map_num: int, market: str, line_value: float = 0) -> Optional[int]:
        """Get WS line_id for a specific market. Needed for all-odds-selections."""
        return self._line_ids.get((eid, map_num, market, line_value))

    # ── Lifecycle ────────────────────────────────────────────────────

    def cleanup_stale(self, max_age_hours: float = 2.0):
        """Remove events not seen in max_age_hours. Prevents memory growth."""
        cutoff = time.time() - (max_age_hours * 3600)
        stale = [eid for eid, ev in self._events.items()
                 if ev['last_seen'] < cutoff]
        for eid in stale:
            del self._events[eid]
        if stale:
            log_info(f"[EVENTSTORE] Cleaned {len(stale)} stale events "
                    f"(>{max_age_hours}h old)")

    # ── Properties + diagnostics ─────────────────────────────────────

    def stats(self) -> dict:
        return {
            'events_total': len(self._events),
            'events_with_odds': sum(1 for e in self._events.values()
                                    if e['has_odds']),
            'match_cache': self.match_cache.size,
            'pricing': super().stats(),
        }

    def diagnose(self, eid, m=0) -> str:
        ev = self._events.get(eid, {})
        header = (f"EVENT {eid}: {ev.get('home','?')} vs {ev.get('away','?')} "
                 f"sp={ev.get('sp','?')} has_odds={ev.get('has_odds','?')} "
                 f"sources={ev.get('sources','?')}")
        pricing = super().diagnose(eid, m)
        return f"{header}\n{pricing}"
