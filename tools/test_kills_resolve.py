"""
tools/test_kills_resolve.py — Offline test harness for _resolve_kills_eids.

Runs in <1s. No bot connection needed. Proves:
  1. Swap case       — matched kills OU gets eid re-pointed to "(Kills)" event
  2. Idempotency     — second call does nothing (no re-log, no re-swap)
  3. No kills event  — skips cleanly, retries next cycle
  4. Not-yet-matched — no PS names in _event_teams → skips
  5. Non-kills OU    — regular map OU untouched
  6. ML with teammate-copied kills eid — NOT touched (compute_ev handles it)
  7. Missing em      — defensive skip on missing _etop_markets entry

Run: python3 tools/test_kills_resolve.py
Exit: 0 on all pass, 1 on any fail.
"""

import sys
import os

# Allow running from project root: `python3 tools/test_kills_resolve.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.matcher import _resolve_kills_eids


# ─────────────────────────────────────────────────────────────────────
# FAKES — minimum surface area needed by _resolve_kills_eids
# ─────────────────────────────────────────────────────────────────────

class FakeMarket:
    def __init__(self, mid, team1, team2, label, state='MATCHED', ps_event_id=None):
        self.mid = mid
        self.team1 = team1
        self.team2 = team2
        self.label = label
        self.state = state
        self.ps_event_id = ps_event_id


class FakeEtopMarket:
    def __init__(self, market, map_num, ps_event_id):
        self.market = market
        self.map_num = map_num
        self.ps_event_id = ps_event_id


class FakeContainer:
    def __init__(self):
        self.markets = {}
        self._etop_markets = {}


class FakePSStore:
    """Minimum surface: _event_teams dict + find_event_id_any method.

    _event_teams: {eid: (home, away)} — populated by ML/HDP WS updates
    _events:      {eid: {home, away, ...}} — populated by LEFT_MENU / REST
    find_event_id_any: searches both.
    """

    def __init__(self):
        self._event_teams = {}  # eid → (home, away), only for ML/HDP events
        self._events = {}        # eid → dict, all registered events (incl. kills)

    def _n(self, s):
        return s.strip().lower()

    def register_regular(self, eid, home, away):
        """Regular event: populates BOTH _event_teams and _events."""
        self._event_teams[eid] = (home, away)
        self._events[eid] = {'home': home, 'away': away}

    def register_kills_only(self, eid, home, away):
        """Kills event: populates ONLY _events (kills have no ML/HDP).
        This is the real-world shape per event_store.py."""
        self._events[eid] = {'home': home, 'away': away}

    def find_event_id(self, home, away):
        h, a = self._n(home), self._n(away)
        for eid, (eh, ea) in self._event_teams.items():
            if self._n(eh) == h and self._n(ea) == a:
                return eid
        return None

    def find_event_id_any(self, home, away):
        eid = self.find_event_id(home, away)
        if eid:
            return eid
        h, a = self._n(home), self._n(away)
        for eid, ev in self._events.items():
            eh, ea = self._n(ev['home']), self._n(ev['away'])
            if (eh == h and ea == a) or (eh == a and ea == h):
                return eid
        return None


# ─────────────────────────────────────────────────────────────────────
# TEST UTILITIES
# ─────────────────────────────────────────────────────────────────────

results = []

def check(name, cond, detail=''):
    status = 'PASS' if cond else 'FAIL'
    results.append((name, cond, detail))
    marker = '✓' if cond else '✗'
    print(f"  {marker} {name}" + (f"  [{detail}]" if detail else ''))


def make_env():
    """Fresh container + store for each test."""
    return FakeContainer(), FakePSStore()


# ─────────────────────────────────────────────────────────────────────
# TEST 1: Swap case — regular eid → kills eid
# ─────────────────────────────────────────────────────────────────────

def test_swap():
    print("\nTEST 1: regular eid → kills eid swap")
    c, s = make_env()

    # Register regular event (ML/HDP layer populated)
    s.register_regular(1001, 'Team Liquid', 'G2 Esports')
    # Register kills event (OU-only, separate eid, _events only)
    s.register_kills_only(2001, 'Team Liquid (Kills)', 'G2 Esports (Kills)')

    m = FakeMarket('mid1', 'Team Liquid', 'G2 Esports',
                   'Total Kills Map1', state='MATCHED', ps_event_id=1001)
    em = FakeEtopMarket(market='ou', map_num=1, ps_event_id=1001)
    c.markets['mid1'] = m
    c._etop_markets['mid1'] = em

    _resolve_kills_eids(c, s)

    check('m.ps_event_id swapped to kills', m.ps_event_id == 2001,
          f'got {m.ps_event_id}')
    check('em.ps_event_id swapped to kills', em.ps_event_id == 2001,
          f'got {em.ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# TEST 2: Idempotency — second call is no-op
# ─────────────────────────────────────────────────────────────────────

def test_idempotent():
    print("\nTEST 2: idempotency (second call is no-op)")
    c, s = make_env()
    s.register_regular(1001, 'Team Liquid', 'G2 Esports')
    s.register_kills_only(2001, 'Team Liquid (Kills)', 'G2 Esports (Kills)')

    m = FakeMarket('mid1', 'Team Liquid', 'G2 Esports',
                   'Total Kills Map1', state='MATCHED', ps_event_id=1001)
    em = FakeEtopMarket(market='ou', map_num=1, ps_event_id=1001)
    c.markets['mid1'] = m
    c._etop_markets['mid1'] = em

    _resolve_kills_eids(c, s)
    first_eid = m.ps_event_id

    # CRITICAL: after swap, m.ps_event_id = 2001, but _event_teams[2001] does NOT exist
    # (kills event is only in _events). The idempotency guard relies on PS names
    # containing "(Kills)" — but we read from _event_teams[em.ps_event_id].
    # After swap, em.ps_event_id = 2001, _event_teams.get(2001) = None → early skip.
    # This is the ACTUAL idempotency path: `if not evt: continue`.
    _resolve_kills_eids(c, s)

    check('eid unchanged on second call', m.ps_event_id == first_eid == 2001,
          f'first={first_eid} second={m.ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# TEST 3: Idempotency via _event_teams already containing (Kills)
# ─────────────────────────────────────────────────────────────────────
# Edge case: if some future code path populates _event_teams[kills_eid],
# the `'(Kills)' in ps_h or ps_a` guard is the second line of defense.

def test_idempotent_via_name_guard():
    print("\nTEST 3: name-suffix guard (if _event_teams has kills entry)")
    c, s = make_env()
    # Simulate future case: kills event is in _event_teams somehow
    s._event_teams[2001] = ('Team Liquid (Kills)', 'G2 Esports (Kills)')
    s._events[2001] = {'home': 'Team Liquid (Kills)', 'away': 'G2 Esports (Kills)'}

    m = FakeMarket('mid1', 'Team Liquid', 'G2 Esports',
                   'Total Kills Map1', state='MATCHED', ps_event_id=2001)
    em = FakeEtopMarket(market='ou', map_num=1, ps_event_id=2001)
    c.markets['mid1'] = m
    c._etop_markets['mid1'] = em

    _resolve_kills_eids(c, s)

    check('eid not re-swapped', m.ps_event_id == 2001,
          f'got {m.ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# TEST 4: No kills event registered → skip, don't touch eid
# ─────────────────────────────────────────────────────────────────────

def test_no_kills_event():
    print("\nTEST 4: no kills event → skip, retry next cycle")
    c, s = make_env()
    s.register_regular(1001, 'Team Liquid', 'G2 Esports')
    # NO kills event registered

    m = FakeMarket('mid1', 'Team Liquid', 'G2 Esports',
                   'Total Kills Map1', state='MATCHED', ps_event_id=1001)
    em = FakeEtopMarket(market='ou', map_num=1, ps_event_id=1001)
    c.markets['mid1'] = m
    c._etop_markets['mid1'] = em

    _resolve_kills_eids(c, s)

    check('eid stays on regular', m.ps_event_id == 1001,
          f'got {m.ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# TEST 5: No PS names yet (regular not in _event_teams) → skip
# ─────────────────────────────────────────────────────────────────────

def test_no_ps_names_yet():
    print("\nTEST 5: regular eid not in _event_teams → skip")
    c, s = make_env()
    # Only kills event registered, no regular
    s.register_kills_only(2001, 'Team Liquid (Kills)', 'G2 Esports (Kills)')

    m = FakeMarket('mid1', 'Team Liquid', 'G2 Esports',
                   'Total Kills Map1', state='MATCHED', ps_event_id=1001)
    em = FakeEtopMarket(market='ou', map_num=1, ps_event_id=1001)
    c.markets['mid1'] = m
    c._etop_markets['mid1'] = em

    _resolve_kills_eids(c, s)

    check('eid stays on regular (will retry)', m.ps_event_id == 1001,
          f'got {m.ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# TEST 6: Non-kills OU (e.g. rounds OU) — untouched
# ─────────────────────────────────────────────────────────────────────

def test_non_kills_ou():
    print("\nTEST 6: non-kills OU (e.g. rounds) — untouched")
    c, s = make_env()
    s.register_regular(1001, 'Team Liquid', 'G2 Esports')
    s.register_kills_only(2001, 'Team Liquid (Kills)', 'G2 Esports (Kills)')

    # Map OU but label does NOT contain 'Kills'
    m = FakeMarket('mid1', 'Team Liquid', 'G2 Esports',
                   'Total Rounds Map1', state='MATCHED', ps_event_id=1001)
    em = FakeEtopMarket(market='ou', map_num=1, ps_event_id=1001)
    c.markets['mid1'] = m
    c._etop_markets['mid1'] = em

    _resolve_kills_eids(c, s)

    check('eid not swapped (no "Kills" in label)', m.ps_event_id == 1001,
          f'got {m.ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# TEST 7: ML market with (accidental) teammate-copied kills eid
#         — function does NOT touch ML/HDP markets
# ─────────────────────────────────────────────────────────────────────

def test_ml_untouched():
    print("\nTEST 7: ML market with kills eid — not touched by function")
    c, s = make_env()
    s.register_regular(1001, 'Team Liquid', 'G2 Esports')
    s.register_kills_only(2001, 'Team Liquid (Kills)', 'G2 Esports (Kills)')

    # ML market (market='ml') that somehow got kills_eid via teammate copy
    m = FakeMarket('mid1', 'Team Liquid', 'G2 Esports',
                   'Moneyline', state='MATCHED', ps_event_id=2001)
    em = FakeEtopMarket(market='ml', map_num=1, ps_event_id=2001)
    c.markets['mid1'] = m
    c._etop_markets['mid1'] = em

    _resolve_kills_eids(c, s)

    check('ML market eid unchanged (market!=ou)', m.ps_event_id == 2001,
          f'got {m.ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# TEST 8: Series-level market (map_num=0) — untouched
# ─────────────────────────────────────────────────────────────────────

def test_series_untouched():
    print("\nTEST 8: series-level kills OU (map_num=0) — untouched")
    c, s = make_env()
    s.register_regular(1001, 'Team Liquid', 'G2 Esports')
    s.register_kills_only(2001, 'Team Liquid (Kills)', 'G2 Esports (Kills)')

    m = FakeMarket('mid1', 'Team Liquid', 'G2 Esports',
                   'Total Kills Series', state='MATCHED', ps_event_id=1001)
    em = FakeEtopMarket(market='ou', map_num=0, ps_event_id=1001)
    c.markets['mid1'] = m
    c._etop_markets['mid1'] = em

    _resolve_kills_eids(c, s)

    check('series market eid unchanged (map_num=0)', m.ps_event_id == 1001,
          f'got {m.ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# TEST 9: Reverse name ordering — find_event_id_any tries both
# ─────────────────────────────────────────────────────────────────────

def test_reverse_ordering():
    print("\nTEST 9: kills event registered in reversed order")
    c, s = make_env()
    # Regular: home=Liquid, away=G2
    s.register_regular(1001, 'Team Liquid', 'G2 Esports')
    # Kills: home=G2, away=Liquid (reversed order)
    s.register_kills_only(2001, 'G2 Esports (Kills)', 'Team Liquid (Kills)')

    m = FakeMarket('mid1', 'Team Liquid', 'G2 Esports',
                   'Total Kills Map1', state='MATCHED', ps_event_id=1001)
    em = FakeEtopMarket(market='ou', map_num=1, ps_event_id=1001)
    c.markets['mid1'] = m
    c._etop_markets['mid1'] = em

    _resolve_kills_eids(c, s)

    check('swap works despite reversed names', m.ps_event_id == 2001,
          f'got {m.ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# TEST 10: Missing _etop_markets entry — defensive skip
# ─────────────────────────────────────────────────────────────────────

def test_missing_em():
    print("\nTEST 10: missing _etop_markets entry — defensive skip")
    c, s = make_env()
    s.register_regular(1001, 'Team Liquid', 'G2 Esports')
    s.register_kills_only(2001, 'Team Liquid (Kills)', 'G2 Esports (Kills)')

    m = FakeMarket('mid1', 'Team Liquid', 'G2 Esports',
                   'Total Kills Map1', state='MATCHED', ps_event_id=1001)
    c.markets['mid1'] = m
    # NOTE: _etop_markets['mid1'] NOT set

    try:
        _resolve_kills_eids(c, s)
        check('no exception on missing em', True)
    except Exception as e:
        check('no exception on missing em', False, f'raised: {e}')

    check('m.ps_event_id untouched', m.ps_event_id == 1001,
          f'got {m.ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# TEST 11: UNMATCHED / CLOSED / DROPPED states skipped
# ─────────────────────────────────────────────────────────────────────

def test_state_filters():
    print("\nTEST 11: UNMATCHED/CLOSED/DROPPED states skipped")
    c, s = make_env()
    s.register_regular(1001, 'Team Liquid', 'G2 Esports')
    s.register_kills_only(2001, 'Team Liquid (Kills)', 'G2 Esports (Kills)')

    for state in ('UNMATCHED', 'CLOSED', 'DROPPED'):
        mid = f'mid_{state}'
        m = FakeMarket(mid, 'Team Liquid', 'G2 Esports',
                       'Total Kills Map1', state=state, ps_event_id=1001)
        em = FakeEtopMarket(market='ou', map_num=1, ps_event_id=1001)
        c.markets[mid] = m
        c._etop_markets[mid] = em

    _resolve_kills_eids(c, s)

    for state in ('UNMATCHED', 'CLOSED', 'DROPPED'):
        mid = f'mid_{state}'
        check(f'{state} not swapped',
              c.markets[mid].ps_event_id == 1001,
              f'got {c.markets[mid].ps_event_id}')


# ─────────────────────────────────────────────────────────────────────
# RUN ALL
# ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("═" * 60)
    print("Test harness for _resolve_kills_eids (S33 P1a)")
    print("═" * 60)

    test_swap()
    test_idempotent()
    test_idempotent_via_name_guard()
    test_no_kills_event()
    test_no_ps_names_yet()
    test_non_kills_ou()
    test_ml_untouched()
    test_series_untouched()
    test_reverse_ordering()
    test_missing_em()
    test_state_filters()

    print("\n" + "═" * 60)
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    print(f"RESULTS: {passed}/{total} passed, {failed} failed")
    print("═" * 60)

    sys.exit(0 if failed == 0 else 1)
