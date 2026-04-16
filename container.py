"""
container.py — Single source of truth for all bot state.

Every module reads from here. Every module writes to its own section.
Dashboard reads from here and NOWHERE ELSE.

Usage:
    container = Container()

    # Classifier writes:
    container.markets[mid] = Market(...)

    # Matcher writes:
    container.markets[mid].ps_event_id = 12345

    # Valuator writes:
    container.markets[mid].ev1 = 3.5

    # Dashboard reads:
    for m in container.markets.values():
        print(m.state, m.best_ev, m.ps_age)
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ═══════════════════════════════════════════════════════════════════════
# MARKET — everything about one market in ONE place
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Market:
    """All data for one etop sub-market. Dashboard reads THIS, nothing else.

    Sections:
        IDENTITY    — set by Classifier, never changes
        ETOP LIVE   — updated every cycle from listing
        PS MATCH    — set by Matcher
        VALUATION   — set by Valuator every cycle
        FIRE STATE  — set by Fire Engine
        LIFECYCLE   — managed by Classifier + Fire Engine
        DEBUG       — gate failures, for dashboard troubleshooting
    """

    # ── IDENTITY (set by Classifier once, never changes) ──────────
    mid: str = ''                           # etop sub-match ID (NEVER changes)
    fire_key: str = ''                      # dedup key
    team1: str = ''                         # etop vs1 name
    team2: str = ''                         # etop vs2 name
    market_type: str = ''                   # ml, hdp, ou, f10k, f5k, duration, race
    line: float = 0.0                       # absolute line value
    map_num: int = 0                        # 0=series, 1-5=map
    label: str = ''                         # human readable: "NAVI -1.5 Map1"
    giving_side: Optional[str] = None       # 'team1' or 'team2' or None
    game: str = ''                          # cs2, dota, lol, valorant, soccer, basketball
    sport: str = ''                         # esports, soccer, basketball
    league: str = ''
    cat_type: str = ''                      # raw etop category.type
    parent_id: str = ''
    raw_type: int = 0                       # original etop mtype for debugging

    # ── ETOP LIVE (updated every cycle from listing) ──────────────
    o1: float = 0.0                         # current etop odds team1 (asian)
    o2: float = 0.0                         # current etop odds team2 (asian)
    remain: float = 0.0                     # seconds remaining
    can_press: bool = False                 # betting open
    cancel_code: Optional[str] = None       # None = active
    etop_age: float = 0.0                   # how old is this etop data

    # ── PS MATCH (set by Matcher) ─────────────────────────────────
    ps_event_id: Optional[int] = None       # PS event ID
    ps_name_team1: Optional[str] = None     # PS name for etop team1
    ps_name_team2: Optional[str] = None     # PS name for etop team2
    match_confidence: float = 0.0           # evidence match score (0-100)
    match_method: str = ''                  # how matched: auto_match, suggest, alias
    match_locked: bool = False              # human-approved, don't rematch
    # ── VALUATION (set by Valuator every cycle) ───────────────────
    ev1: Optional[float] = None             # EV% team1/over side
    ev2: Optional[float] = None             # EV% team2/under side
    best_ev: Optional[float] = None         # max(ev1, ev2)
    best_side: int = 0                      # 1=team1/over, 2=team2/under
    ps_fair_1: Optional[float] = None       # no-vig fair odds side 1
    ps_fair_2: Optional[float] = None       # no-vig fair odds side 2
    ps_raw_1: Optional[float] = None        # raw PS odds side 1
    ps_raw_2: Optional[float] = None        # raw PS odds side 2
    ps_age: Optional[float] = None          # seconds since PS line updated

    # ── FIRE STATE (set by Fire Engine) ───────────────────────────
    phase: str = ''                         # P1, P2, P3
    phase_min_ev: float = 0.0              # minimum EV for current phase
    total_fired: int = 0                    # items fired on this market
    total_value: float = 0.0                # gold value bet on this market
    value_cap: float = 0.0                  # from TUHAO or HARD_CAP
    cap_source: str = ''                    # 'TUHAO' or 'HARD_CAP'
    remaining_cap: float = 0.0              # value_cap - total_value
    locked_side: int = 0                    # 0=none, 1=team1, 2=team2
    raw_pool: float = 0.0                   # estimated pool size
    first_fire_at: float = 0.0
    last_fire_at: float = 0.0
    last_tuhao_at: float = 0.0
    consumed_item_ids: List[str] = field(default_factory=list)
    press_ids: List[str] = field(default_factory=list)

    # ── LIFECYCLE ─────────────────────────────────────────────────
    state: str = 'UNMATCHED'                # UNMATCHED, MATCHED, MONITOR, FIRE_ZONE, CLOSED
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    dead_at: float = 0.0                    # when disappeared from listing
    locked_at: float = 0.0                  # when state became CLOSED

    # ── DEBUG (for dashboard) ─────────────────────────────────────
    last_gate_failures: List[str] = field(default_factory=list)  # ["ev<P1:2.0%", "ps_stale"]


# ═══════════════════════════════════════════════════════════════════════
# SPORT CONFIG — per-sport EV thresholds and settings
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SportConfig:
    """Per-sport firing parameters."""
    phase1_ev: float = 2.0          # conservative (far from close)
    phase2_ev: float = 1.5          # medium
    phase3_ev: float = 1.0          # aggressive (near close, pool is set)
    trigger_secs: float = 90.0      # remain ≤ this → FIRE_ZONE eligible
    max_ps_age: float = 300.0       # PS odds older than this → block


# Default sport configs
DEFAULT_SPORT_CONFIGS = {
    'esports': SportConfig(
        phase1_ev=10.0, phase2_ev=5.0, phase3_ev=1.0,
        trigger_secs=90, max_ps_age=300,
    ),
    'basketball': SportConfig(
        phase1_ev=0.0, phase2_ev=0.0, phase3_ev=0.0,
        trigger_secs=50, max_ps_age=120,
    ),
    'soccer': SportConfig(
        phase1_ev=3.0, phase2_ev=2.0, phase3_ev=1.5,
        trigger_secs=50, max_ps_age=300,
    ),
}


# ═══════════════════════════════════════════════════════════════════════
# CONTAINER — the single shared state object
# ═══════════════════════════════════════════════════════════════════════

class Container:
    """Single source of truth for the entire bot.

    Modules read/write their own sections:
        Etop Collector  → self.etop_*
        PS Collector    → (via TheOnlyStore, external)
        Classifier      → self.markets (create + update odds)
        Matcher         → self.markets (ps_event_id, names)
        Valuator        → self.markets (ev, phase)
        Fire Engine     → self.markets (fire state)
        Cancel Engine   → self.markets (reset fire state)
        Dashboard       → reads self.markets + self.etop_* + self.health

    Dashboard reads ONLY from here. No fire_zone._fire_state.
    No live_feed.standard_store. No etop_state.listing.
    """

    def __init__(self):
        # ── Markets (the core data) ───────────────────────────────
        self.markets: Dict[str, Market] = {}  # key = mid

        # ── Etop state ────────────────────────────────────────────
        self.etop_last_fetch: float = 0.0
        self.etop_fetch_count: int = 0
        self.fire_active: bool = False

        # ── PS state ──────────────────────────────────────────────
        self.ps_ws_connected: bool = False
        self.ps_session_alive: bool = True
        self.ps_store_size: int = 0

        # ── Inventory ─────────────────────────────────────────────
        self.bag_count: int = 0
        self.bag_value: float = 0.0

        # ── Health ────────────────────────────────────────────────
        self.bot_start_time: float = time.time()
        self.last_brain_cycle: float = 0.0
        self.last_fire_cycle: float = 0.0

        # Internal: EtopMarket objects for compute_ev compatibility
        # Classifier writes, Valuator reads. Dashboard never touches.
        self._etop_markets: dict = {}  # mid → EtopMarket

        # ── Sport configs ─────────────────────────────────────────
        self.sport_configs: Dict[str, SportConfig] = dict(DEFAULT_SPORT_CONFIGS)

    @property
    def etop_age(self) -> float:
        if self.etop_last_fetch <= 0:
            return 9999.0
        return time.time() - self.etop_last_fetch

    def get_sport_config(self, sport: str) -> SportConfig:
        """Get config for a sport. Falls back to esports defaults."""
        return self.sport_configs.get(sport, self.sport_configs.get('esports', SportConfig()))

    # ── Market helpers ────────────────────────────────────────────

    def get_markets_by_state(self, state: str) -> Dict[str, Market]:
        """Get all markets in a specific state."""
        return {mid: m for mid, m in self.markets.items() if m.state == state}

    def get_fire_zone_markets(self) -> Dict[str, Market]:
        """Markets ready to fire."""
        return {mid: m for mid, m in self.markets.items()
                if m.state in ('FIRE_ZONE', 'FIRED')}

    def get_matched_markets(self) -> Dict[str, Market]:
        """Markets that have PS match (any state beyond UNMATCHED)."""
        return {mid: m for mid, m in self.markets.items()
                if m.ps_event_id is not None}

    def count_states(self) -> Dict[str, int]:
        """Count markets per state."""
        counts = {}
        for m in self.markets.values():
            counts[m.state] = counts.get(m.state, 0) + 1
        return counts

    # ── Dashboard export ──────────────────────────────────────────

    def to_dash_state(self) -> dict:
        """Export EVERYTHING dashboard needs in one dict.

        Dashboard calls this ONE function. Reads NOTHING else.
        """
        dash_markets = []
        for m in self.markets.values():
            dash_markets.append({
                # Identity
                'n': f'{m.team1} vs {m.team2}',
                'ml': m.label,
                'fk': m.fire_key,
                'mid': m.mid,
                'game': m.game,
                'sport': m.sport,

                # Live etop
                'etop': f'{m.o1:.2f}/{m.o2:.2f}' if m.o1 > 0 else '–',
                's': int(m.remain),
                'cp': m.can_press,

                # PS match
                'ps': f'{m.ps_name_team1} vs {m.ps_name_team2}' if m.ps_name_team1 else '',
                'match_conf': round(m.match_confidence, 1),
                'lg': m.league,

                # Valuation
                'b': round(m.best_ev, 2) if m.best_ev is not None else None,
                'e1': round(m.ev1, 2) if m.ev1 is not None else None,
                'e2': round(m.ev2, 2) if m.ev2 is not None else None,
                'pf': f'{m.ps_fair_1:.3f}/{m.ps_fair_2:.3f}' if m.ps_fair_1 else '–',
                'pa': int(m.ps_age) if m.ps_age else 0,

                # Fire state
                'st': m.state,
                'phase': m.phase,
                'phase_min_ev': m.phase_min_ev,
                'cap': f'{m.cap_source}:{m.remaining_cap:.0f}/{m.value_cap:.0f}g' if m.value_cap else '',
                'inv_items': m.total_fired,
                'inv_value': round(m.total_value, 1),

                # Lifecycle
                'ln': m.line,
                'locked_at': m.locked_at,
                'in_listing': m.last_seen > 0 and (time.time() - m.last_seen) < 30,

                # Panel aliases / extra fields
                'pool': m.raw_pool,          # panel reads 'pool' (not 'raw_pool')
                'can_press': m.can_press,    # panel reads both 'can_press' and 'cp'
                'no_line': False,            # legacy field — always False in CleanFlow
                'remain_zero_at': None,      # legacy field — not tracked in CleanFlow

                # Debug
                'gates': m.last_gate_failures,
            })

        return {
            'ts': time.time(),
            'updated_at': time.time(),
            'etop_age': round(self.etop_age, 1),
            'ws': self.ps_ws_connected,
            'total_markets': len(self.markets),
            'states': self.count_states(),
            'tracked': sum(1 for m in self.markets.values() if m.ps_event_id is not None),
            'unmatched_count': sum(1 for m in self.markets.values() if m.ps_event_id is None),
            'bag_count': self.bag_count,
            'bag_value': round(self.bag_value, 1),
            'markets': sorted(dash_markets, key=lambda m: m['s']),
            # Panel compatibility
            'live': [],          # live events list (populated by live feed if wired)
            'listing': 0,        # etop listing count (not tracked here)
            'session_tracker': None,  # populated by session manager if wired
            # DataBus freshness
            'bus_freshness': getattr(self, 'bus_freshness', {}),
            'bus_notify_count': getattr(self, 'bus_notify_count', 0),
        }
