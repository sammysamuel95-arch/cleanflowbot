"""StandardStore — PS pricing store. Identity-keyed. Flip-proof.

Core principle: store each team's odds from THAT TEAM's perspective.
(team_name, signed_line) is the invariant.

WRITE: update_ml, update_hdp, update_ou — called by WS, REST, resub
READ: get_ml_fair, get_hdp_fair, get_ou_fair — called by compute_ev
CHECK: has_ml, has_hdp, has_ou — called by find_line

Designed across 7 rounds of architecture review. See HOLY_GRAIL_GUIDEBOOK.
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Tuple, List
from core.math import no_vig
from core.logger import log_info, log_warn


@dataclass
class OddsEntry:
    """One side of one market line. Internal to StandardStore."""
    raw: float                  # PS raw odds for this side
    fair: float                 # PS no-vig fair odds for this side
    timestamp: float = field(default_factory=time.time)
    source: str = 'ws'          # 'ws', 'rest', 'resub'
    origin_sp: int = 0          # sport ID — for cleanup scoping
    origin_mk: int = 0          # mk — for cleanup scoping


class StandardStore:
    """PS pricing indexed by (event_id, map_num) → market buckets.

    Structure:
      _data[(event_id, map_num)]["ml"][team_name] → OddsEntry
      _data[(event_id, map_num)]["hdp"][(team_name, signed_line)] → OddsEntry
      _data[(event_id, map_num)]["ou"][("over"|"under", total)] → OddsEntry
      _data[(event_id, map_num)]["team_total"][(team_name, total)] → OddsEntry

    Each team has its OWN entry from ITS perspective.
    When WS flips home/away, normalize produces SAME keys with SAME values.
    """

    STALE_SECONDS = 300
    MIN_READY = 100  # warmup: block firing until this many entries

    def __init__(self):
        self._data: Dict[Tuple[int, int], dict] = {}
        self._event_teams: Dict[int, Tuple[str, str]] = {}  # eid → (home, away) first-seen
        self._updates: int = 0

    def _bucket(self, eid: int, m: int) -> dict:
        """Get or create market bucket for an event+map."""
        key = (eid, m)
        if key not in self._data:
            self._data[key] = {"ml": {}, "hdp": {}, "ou": {}, "team_total": {}}
        return self._data[key]

    def _n(self, name: str) -> str:
        """Normalize team name: lowercase + strip. Minimal only."""
        return name.lower().strip()

    # ─── PROPERTIES ───────────────────────────────────────────────

    @property
    def size(self) -> int:
        """Total number of entries across all buckets."""
        return sum(len(mkt) for b in self._data.values() for mkt in b.values())

    @property
    def ready(self) -> bool:
        """Has enough data loaded to start firing?"""
        return self.size >= self.MIN_READY

    # ─── WRITE ────────────────────────────────────────────────────

    def update_ml(self, eid: int, m: int, home: str, away: str,
                  h_odds: float, a_odds: float,
                  src: str = 'ws', sp: int = 0, mk: int = 0):
        """Store ML. Each team gets its own fair odds entry.

        home/away: PS team names (any order — we store by identity).
        h_odds/a_odds: PS raw odds for home/away.
        """
        if h_odds < 1.05 or a_odds < 1.05:
            return
        fh, fa = no_vig(h_odds, a_odds)
        b = self._bucket(eid, m)
        ts = time.time()
        b["ml"][self._n(home)] = OddsEntry(h_odds, fh, ts, src, sp, mk)
        b["ml"][self._n(away)] = OddsEntry(a_odds, fa, ts, src, sp, mk)
        # Lock first-seen team names per event (for find_event_id)
        if eid not in self._event_teams:
            self._event_teams[eid] = (home, away)
        self._updates += 1

    def update_hdp(self, eid: int, m: int, home: str, away: str,
                   h_hdp: float, h_odds: float, a_odds: float,
                   src: str = 'ws', sp: int = 0, mk: int = 0):
        """Store HDP. Each team gets entry from ITS perspective.

        h_hdp: handicap from home perspective.
          -1.5 = home gives 1.5 → store (home, -1.5)
          +1.5 = home receives 1.5 → store (home, +1.5)
        Away always gets the opposite sign.

        When WS flips home/away, signs also flip → same keys, same values.
        """
        if h_odds < 1.05 or a_odds < 1.05:
            return
        fh, fa = no_vig(h_odds, a_odds)
        b = self._bucket(eid, m)
        ts = time.time()
        # Home team: from home's perspective
        b["hdp"][(self._n(home), round(h_hdp, 2))] = OddsEntry(h_odds, fh, ts, src, sp, mk)
        # Away team: opposite sign
        b["hdp"][(self._n(away), round(-h_hdp, 2))] = OddsEntry(a_odds, fa, ts, src, sp, mk)
        if eid not in self._event_teams:
            self._event_teams[eid] = (home, away)
        self._updates += 1

    def update_ou(self, eid: int, m: int, total: float,
                  o_odds: float, u_odds: float,
                  home: str = '', away: str = '',
                  src: str = 'ws', sp: int = 0, mk: int = 0):
        """Store OU. Over and under as separate entries."""
        if o_odds < 1.05 or u_odds < 1.05:
            return
        fo, fu = no_vig(o_odds, u_odds)
        b = self._bucket(eid, m)
        ts = time.time()
        tr = round(total, 2)
        b["ou"][("over", tr)] = OddsEntry(o_odds, fo, ts, src, sp, mk)
        b["ou"][("under", tr)] = OddsEntry(u_odds, fu, ts, src, sp, mk)
        if home and away and eid not in self._event_teams:
            self._event_teams[eid] = (home, away)
        self._updates += 1

    # ─── READ ─────────────────────────────────────────────────────

    def get_ml_fair(self, eid: int, m: int, team: str) -> Optional[float]:
        """Fair odds for a team in ML. Exact name lookup."""
        b = self._data.get((eid, m))
        if not b:
            return None
        e = b["ml"].get(self._n(team))
        if e is None:
            return None
        if self._stale(e):
            log_warn("STORE", f"Stale ML ({int(time.time() - e.timestamp)}s): {team} eid={eid}")
        return e.fair

    def get_hdp_fair(self, eid: int, m: int, team: str,
                     signed: float) -> Optional[float]:
        """Fair odds for team at specific HDP.

        signed: from THIS TEAM's perspective.
          -1.5 = this team gives 1.5
          +1.5 = this team receives 1.5
        """
        b = self._data.get((eid, m))
        if not b:
            return None
        e = b["hdp"].get((self._n(team), round(signed, 2)))
        if e is None:
            return None
        if self._stale(e):
            log_warn("STORE", f"Stale HDP ({int(time.time() - e.timestamp)}s): {team}@{signed} eid={eid}")
        return e.fair

    def get_all_hdp_lines(self, eid: int, m: int) -> list:
        """All HDP entries for an event+map. Returns [(abs_line, raw_neg, raw_pos, fair_neg, fair_pos), ...].

        Groups by abs(line). Each abs line has giving side (negative) and getting side (positive).
        Used by NBA HDP extrapolation to fit fair-odds curve.
        """
        b = self._data.get((eid, m))
        if not b or 'hdp' not in b:
            return []
        from collections import defaultdict
        groups = defaultdict(dict)
        for (team, signed), entry in b['hdp'].items():
            al = round(abs(signed), 2)
            if signed < 0:
                groups[al]['neg'] = entry
            elif signed > 0:
                groups[al]['pos'] = entry
            else:
                groups[al].setdefault('neg', entry)
                groups[al].setdefault('pos', entry)
        result = []
        for al, sides in sorted(groups.items()):
            if 'neg' in sides and 'pos' in sides:
                neg = sides['neg']
                pos = sides['pos']
                result.append((al, neg.raw, pos.raw, neg.fair, pos.fair))
        return result

    def get_ou_fair(self, eid: int, m: int, side: str,
                    total: float) -> Optional[float]:
        """Fair odds for over or under. side='over' or 'under'."""
        b = self._data.get((eid, m))
        if not b:
            return None
        e = b["ou"].get((side, round(total, 2)))
        if e is None:
            return None
        if self._stale(e):
            log_warn("STORE", f"Stale OU ({int(time.time() - e.timestamp)}s): {side}@{total} eid={eid}")
        return e.fair

    # ─── EXISTENCE CHECKS ─────────────────────────────────────────

    def has_ml(self, eid: int, m: int) -> bool:
        """Does ML exist for this event+map?"""
        b = self._data.get((eid, m))
        return bool(b and b["ml"])

    def get_line_age(self, eid: int, m: int, market: str) -> Optional[int]:
        """Seconds since oldest line in this market was updated. None if no data."""
        b = self._data.get((eid, m))
        if not b:
            return None
        bucket = b.get(market, {})
        if not bucket:
            return None
        newest_ts = max(e.timestamp for e in bucket.values())
        return int(time.time() - newest_ts)

    def has_hdp(self, eid: int, m: int, absline: float,
                t1: str, t2: str) -> bool:
        """Do BOTH sides of HDP exist at this abs line?

        Requires both giving and getting entries to prevent
        partial-market false positives.
        """
        b = self._data.get((eid, m))
        if not b:
            return False
        al = round(abs(absline), 2)
        n1, n2 = self._n(t1), self._n(t2)
        has_neg = (n1, -al) in b["hdp"] or (n2, -al) in b["hdp"]
        has_pos = (n1, al) in b["hdp"] or (n2, al) in b["hdp"]
        return has_neg and has_pos

    def has_ou(self, eid: int, m: int, total: float) -> bool:
        """Do both over AND under exist for this total?"""
        b = self._data.get((eid, m))
        if not b:
            return False
        t = round(total, 2)
        return ("over", t) in b["ou"] and ("under", t) in b["ou"]

    # ─── EVENT LOOKUP ─────────────────────────────────────────────

    def find_event_id(self, ps_home: str, ps_away: str) -> Optional[int]:
        """Find event_id by PS team names. Checks both orderings."""
        h, a = self._n(ps_home), self._n(ps_away)
        for eid, (sh, sa) in self._event_teams.items():
            shn, san = self._n(sh), self._n(sa)
            if (shn == h and san == a) or (shn == a and san == h):
                return eid
        return None

    def find_alternate_eids(self, eid: int, team: str) -> list:
        """Find other eids that have the same team name.

        PS creates multiple eids per match (mk=1 vs mk=3).
        Returns list of alternative eids (excluding the given one).
        """
        team_n = self._n(team)
        alts = []
        for alt_eid, (h, a) in self._event_teams.items():
            if alt_eid == eid:
                continue
            if self._n(h) == team_n or self._n(a) == team_n:
                alts.append(alt_eid)
        return alts

    # ─── MAINTENANCE ──────────────────────────────────────────────

    def cleanup_full_odds(self, sp: int, mk: int,
                          surviving_keys: set):
        """Remove entries from this sp+mk NOT in surviving set.

        Source-scoped: only removes entries where origin matches.
        Called when FULL_ODDS arrives with complete snapshot.
        """
        for (eid, m), b in list(self._data.items()):
            for mt in ("ml", "hdp", "ou", "team_total"):
                rm = [k for k, v in b.get(mt, {}).items()
                      if v.origin_sp == sp and v.origin_mk == mk
                      and (eid, m, mt, k) not in surviving_keys]
                for k in rm:
                    del b[mt][k]
            # Remove empty buckets
            if not any(b[mt] for mt in ("ml", "hdp", "ou", "team_total")):
                del self._data[(eid, m)]

    def clear_event_teams(self):
        """Clear on FULL_ODDS reconnect to prevent stale name mappings."""
        self._event_teams.clear()

    def _stale(self, e: OddsEntry, max_age: float = None) -> bool:
        """Is this entry too old to trust?"""
        return (time.time() - e.timestamp) > (max_age or self.STALE_SECONDS)

    # ─── STATS & DIAGNOSTICS ─────────────────────────────────────

    def stats(self) -> dict:
        return {'entries': self.size, 'events': len(self._event_teams),
                'updates': self._updates}

    def diagnose(self, eid: int, m: int = 0) -> str:
        """Dump complete state for an event. For live debugging.

        One call shows everything: teams, all markets, odds, fairs, ages.
        Would have saved hours in Session 16.
        """
        b = self._data.get((eid, m))
        if not b:
            return f"No data for eid={eid} map={m}"
        lines = [f"EVENT {eid} map={m} teams={self._event_teams.get(eid, '?')}"]
        now = time.time()
        for mt in ("ml", "hdp", "ou", "team_total"):
            entries = b.get(mt, {})
            if entries:
                lines.append(f"  {mt.upper()}:")
                for key, e in sorted(entries.items(), key=lambda x: str(x[0])):
                    age = now - e.timestamp
                    lines.append(
                        f"    {key} → raw={e.raw:.3f} fair={e.fair:.3f} "
                        f"age={age:.0f}s src={e.source}"
                    )
        return '\n'.join(lines)
