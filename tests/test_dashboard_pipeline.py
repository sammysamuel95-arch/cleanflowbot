"""
tests/test_dashboard_pipeline.py — Full dashboard pipeline test.

Verifies:
  Container(4 markets) → to_dash_state() → dashboard.run() writes JSON
  → panel.get_status() reads JSON → every field panel consumes is present

Flags any field that panel.get_status() accesses but to_dash_state() doesn't supply.
"""

import json
import os
import sys
import time
import tempfile
import pytest

# ── Path setup ──────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from container import Container, Market
from modules import dashboard


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_container() -> Container:
    """4-market container: FIRE_ZONE, MONITOR, UNMATCHED, CLOSED."""
    c = Container()

    # ── FIRE_ZONE market ─────────────────────────────────────────
    fz = Market(
        mid='fz-001',
        fire_key='NAVI|Spirit|ml|s0',
        team1='NAVI', team2='Spirit',
        market_type='ml', label='Match Winner',
        map_num=0, line=0.0,
        game='cs2', sport='esports', league='BLAST Premier',
        o1=1.95, o2=1.85, remain=45.0, can_press=True,
        ps_event_id=100001,
        ps_name_team1='Natus Vincere', ps_name_team2='Team Spirit',
        match_confidence=92.5, match_method='auto_match',
        ev1=3.2, ev2=-1.1, best_ev=3.2, best_side=1,
        ps_fair_1=1.88, ps_fair_2=2.05,
        ps_raw_1=1.85, ps_raw_2=2.08,
        ps_age=12.0,
        state='FIRE_ZONE', phase='P3', phase_min_ev=1.0,
        total_fired=2, total_value=200.0,
        value_cap=500.0, cap_source='TUHAO', remaining_cap=300.0,
        locked_side=1, raw_pool=3200.0,
        first_fire_at=time.time() - 30,
        last_fire_at=time.time() - 5,
        consumed_item_ids=['item-a', 'item-b'],
        press_ids=['p1', 'p2'],
        last_gate_failures=[],
    )
    c.markets['fz-001'] = fz

    # ── MONITOR market ───────────────────────────────────────────
    mo = Market(
        mid='mo-001',
        fire_key='T1|G2|hdp|s0',
        team1='T1', team2='G2',
        market_type='hdp', label='T1 -1.5',
        map_num=0, line=-1.5,
        game='lol', sport='esports', league='Worlds',
        o1=2.10, o2=1.72, remain=300.0, can_press=True,
        ps_event_id=100002,
        ps_name_team1='T1', ps_name_team2='G2',
        match_confidence=88.0, match_method='auto_match',
        ev1=1.8, ev2=-2.5, best_ev=1.8, best_side=1,
        ps_fair_1=2.02, ps_fair_2=1.80,
        ps_raw_1=2.05, ps_raw_2=1.78,
        ps_age=35.0,
        state='MONITOR', phase='', phase_min_ev=0.0,
        total_fired=0, total_value=0.0,
        value_cap=0.0, cap_source='', remaining_cap=0.0,
        last_gate_failures=['pool=0<500'],
    )
    c.markets['mo-001'] = mo

    # ── UNMATCHED market ─────────────────────────────────────────
    um = Market(
        mid='um-001',
        fire_key='FooFC|BarFC|ml|s0',
        team1='Foo FC', team2='Bar FC',
        market_type='ml', label='Match Winner',
        map_num=0, line=0.0,
        game='soccer', sport='soccer', league='Italy - Serie A',
        o1=1.60, o2=2.40, remain=600.0, can_press=False,
        ps_event_id=None,
        state='UNMATCHED',
    )
    c.markets['um-001'] = um

    # ── CLOSED market ────────────────────────────────────────────
    cl = Market(
        mid='cl-001',
        fire_key='Lakers|Celtics|ml|s0',
        team1='Lakers', team2='Celtics',
        market_type='ml', label='Match Winner',
        game='basketball', sport='basketball', league='NBA',
        o1=0.0, o2=0.0, remain=0.0, can_press=False,
        ps_event_id=100003,
        ps_name_team1='Los Angeles Lakers', ps_name_team2='Boston Celtics',
        total_fired=3, total_value=450.0,
        state='CLOSED',
        locked_at=time.time() - 120,
    )
    c.markets['cl-001'] = cl

    c.ps_ws_connected = True
    c.ps_session_alive = True
    c.ps_store_size = 4200
    c.bag_count = 15
    c.bag_value = 1500.0
    c.etop_last_fetch = time.time() - 1.5

    return c


# Fields panel.get_status() reads per-market (from panel.py lines ~121-135)
PANEL_MARKET_FIELDS = [
    'n', 'b', 'e1', 'e2', 'etop', 'pf', 'pa', 's', 'st', 'ln',
    'ps', 'mid', 'pool', 'no_line', 'can_press', 'cp', 'locked_at',
    'fk', 'inv_value', 'inv_items', 'game', 'ml', 'remain_zero_at',
]

# Fields panel.get_status() reads at top level (panel.py lines ~150-161)
PANEL_TOP_FIELDS = [
    'ws', 'markets', 'tracked', 'live', 'listing',
    'bag_value', 'bag_count', 'session_tracker',
    # timestamp: panel reads updated_at OR ts
    'updated_at',
]


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

class TestDashboardPipeline:

    def setup_method(self):
        self.container = _make_container()
        self.tmpdir = tempfile.mkdtemp()

    # ── Step 1: to_dash_state() produces correct structure ──────

    def test_to_dash_state_returns_dict(self):
        dash = self.container.to_dash_state()
        assert isinstance(dash, dict)

    def test_to_dash_state_has_4_markets(self):
        dash = self.container.to_dash_state()
        assert len(dash['markets']) == 4

    def test_to_dash_state_top_level_fields(self):
        dash = self.container.to_dash_state()
        for field in ['ts', 'updated_at', 'etop_age', 'ws', 'total_markets',
                      'states', 'tracked', 'unmatched_count', 'bag_count',
                      'bag_value', 'markets']:
            assert field in dash, f"Missing top-level field: {field}"

    def test_to_dash_state_ws_reflects_container(self):
        dash = self.container.to_dash_state()
        assert dash['ws'] is True

    def test_to_dash_state_bag_values(self):
        dash = self.container.to_dash_state()
        assert dash['bag_count'] == 15
        assert dash['bag_value'] == 1500.0

    def test_to_dash_state_tracked_count(self):
        # 3 markets have ps_event_id (FIRE_ZONE, MONITOR, CLOSED); 1 UNMATCHED
        dash = self.container.to_dash_state()
        assert dash['tracked'] == 3
        assert dash['unmatched_count'] == 1

    def test_to_dash_state_states_dict(self):
        dash = self.container.to_dash_state()
        states = dash['states']
        assert states.get('FIRE_ZONE') == 1
        assert states.get('MONITOR') == 1
        assert states.get('UNMATCHED') == 1
        assert states.get('CLOSED') == 1

    def test_to_dash_state_markets_sorted_by_remain(self):
        """Markets must be sorted ascending by 's' (remain seconds)."""
        dash = self.container.to_dash_state()
        remains = [m['s'] for m in dash['markets']]
        assert remains == sorted(remains), f"Markets not sorted: {remains}"

    # ── Step 2: dashboard.run() writes valid JSON ────────────────

    def test_dashboard_writes_json(self):
        dashboard.run(self.container, data_dir=self.tmpdir)
        path = os.path.join(self.tmpdir, 'dash_state.json')
        assert os.path.exists(path), "dash_state.json not written"
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert 'markets' in data

    def test_dashboard_atomic_write(self):
        """Tmp file must not exist after successful write (os.replace used)."""
        dashboard.run(self.container, data_dir=self.tmpdir)
        tmp = os.path.join(self.tmpdir, 'dash_state.json.tmp')
        assert not os.path.exists(tmp), ".tmp file left behind (non-atomic write)"

    def test_dashboard_json_is_valid(self):
        dashboard.run(self.container, data_dir=self.tmpdir)
        path = os.path.join(self.tmpdir, 'dash_state.json')
        with open(path) as f:
            raw = f.read()
        # Must parse without error
        data = json.loads(raw)
        assert len(data['markets']) == 4

    # ── Step 3: per-market fields panel.get_status() reads ──────

    def test_panel_market_fields_present(self):
        """Every field panel reads via m.get() must exist in the market dict."""
        dash = self.container.to_dash_state()
        missing_by_market = {}
        for m in dash['markets']:
            mid = m.get('mid', '?')
            missing = [f for f in PANEL_MARKET_FIELDS if f not in m]
            if missing:
                missing_by_market[mid] = missing

        assert not missing_by_market, (
            "Fields panel reads but to_dash_state() doesn't export:\n"
            + "\n".join(f"  {mid}: {fields}" for mid, fields in missing_by_market.items())
        )

    def test_panel_top_level_fields_present(self):
        """Every top-level field panel reads must exist in to_dash_state()."""
        dash = self.container.to_dash_state()
        # panel reads updated_at OR ts — at least one must be present
        has_timestamp = 'updated_at' in dash or 'ts' in dash
        assert has_timestamp, "Neither 'updated_at' nor 'ts' in dash state"

        non_ts = [f for f in PANEL_TOP_FIELDS if f != 'updated_at']
        missing = [f for f in non_ts if f not in dash]
        assert not missing, f"Missing top-level fields panel reads: {missing}"

    # ── Step 4: per-state field correctness ─────────────────────

    def test_fire_zone_market_fields(self):
        dash = self.container.to_dash_state()
        fz = next(m for m in dash['markets'] if m['mid'] == 'fz-001')
        assert fz['st'] == 'FIRE_ZONE'
        assert fz['b'] == 3.2
        assert fz['e1'] == 3.2
        assert fz['e2'] == -1.1
        assert fz['s'] == 45
        assert fz['inv_items'] == 2
        assert fz['game'] == 'cs2'
        assert 'pf' in fz  # ps fair odds formatted

    def test_monitor_market_ev(self):
        dash = self.container.to_dash_state()
        mo = next(m for m in dash['markets'] if m['mid'] == 'mo-001')
        assert mo['st'] == 'MONITOR'
        assert mo['b'] == 1.8

    def test_unmatched_market_no_ps(self):
        dash = self.container.to_dash_state()
        um = next(m for m in dash['markets'] if m['mid'] == 'um-001')
        assert um['st'] == 'UNMATCHED'
        assert um['ps'] == ''       # no PS match → empty string
        assert um['b'] is None      # no EV

    def test_closed_market_locked_at(self):
        dash = self.container.to_dash_state()
        cl = next(m for m in dash['markets'] if m['mid'] == 'cl-001')
        assert cl['st'] == 'CLOSED'
        assert cl['locked_at'] > 0

    # ── Step 5: full roundtrip (write → read JSON) ───────────────

    def test_roundtrip_json_fields(self):
        """Write dash_state.json then read it back — all panel fields survive."""
        dashboard.run(self.container, data_dir=self.tmpdir)
        path = os.path.join(self.tmpdir, 'dash_state.json')
        with open(path) as f:
            state = json.load(f)

        # Top-level
        has_ts = 'updated_at' in state or 'ts' in state
        assert has_ts

        # Per-market
        missing_by_market = {}
        for m in state.get('markets', []):
            mid = m.get('mid', '?')
            missing = [f for f in PANEL_MARKET_FIELDS if f not in m]
            if missing:
                missing_by_market[mid] = missing

        assert not missing_by_market, (
            "Missing fields after JSON roundtrip:\n"
            + "\n".join(f"  {mid}: {fields}" for mid, fields in missing_by_market.items())
        )

    def test_roundtrip_preserves_numeric_types(self):
        """JSON roundtrip must not corrupt numeric types (e.g. None → null → None)."""
        dashboard.run(self.container, data_dir=self.tmpdir)
        path = os.path.join(self.tmpdir, 'dash_state.json')
        with open(path) as f:
            state = json.load(f)

        for m in state['markets']:
            # b can be None (UNMATCHED) or float
            if m['b'] is not None:
                assert isinstance(m['b'], (int, float)), f"b={m['b']!r} not numeric"
            # s must be int
            assert isinstance(m['s'], int), f"s={m['s']!r} not int"
            # pa must be int or 0
            assert isinstance(m['pa'], int), f"pa={m['pa']!r} not int"


# ═══════════════════════════════════════════════════════════════════════
# Field gap report (runs as a standalone diagnostic, not a test)
# ═══════════════════════════════════════════════════════════════════════

def report_field_gaps():
    """Print a gap report: what panel reads vs what to_dash_state() exports."""
    c = _make_container()
    dash = c.to_dash_state()
    sample = dash['markets'][0] if dash['markets'] else {}

    exported = set(sample.keys())
    panel_reads = set(PANEL_MARKET_FIELDS)

    missing = panel_reads - exported
    extra = exported - panel_reads

    print("\n=== Dashboard Field Gap Report ===")
    print(f"to_dash_state() exports {len(exported)} per-market fields")
    print(f"panel.get_status() reads {len(panel_reads)} per-market fields")

    if missing:
        print(f"\n[MISSING] panel reads but to_dash_state() doesn't export ({len(missing)}):")
        for f in sorted(missing):
            print(f"  - {f}")
    else:
        print("\n[OK] No missing fields — panel can read everything it expects")

    if extra:
        print(f"\n[EXTRA] to_dash_state() exports but panel doesn't read ({len(extra)}):")
        for f in sorted(extra):
            print(f"  + {f}")

    # Top-level check
    top_exported = set(dash.keys())
    top_missing = set(PANEL_TOP_FIELDS) - top_exported
    if top_missing:
        print(f"\n[MISSING TOP-LEVEL] {sorted(top_missing)}")
    else:
        print("\n[OK] All top-level fields present")


if __name__ == '__main__':
    report_field_gaps()
