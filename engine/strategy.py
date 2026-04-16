"""
engine/strategy.py — Pure betting decision logic.

Zero dependencies on API, store, or bot state.
Receives MarketSnapshot → returns decisions.
Modify this file freely without breaking fire zone mechanics.

To add new strategy logic:
  1. Add fields to MarketSnapshot (the input)
  2. Use them in Strategy methods
  3. Fire zone populates them — zero changes to mechanics

Parameters live here, not in config.py.
Panel overrides via bot_config.json → Strategy(config).

See FIRE_FLOW_GUIDEBOOK.md Chapter 4 (Priority) and Chapter 6 (Gates).
"""

from dataclasses import dataclass
from typing import List, Optional


# ═══════════════════════════════════════════════════════════════════════
# MARKET SNAPSHOT — everything strategy sees
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class MarketSnapshot:
    """Point-in-time data for one fire zone market.

    Built by fire_zone from live data. Strategy never fetches data.
    Add fields here when new data becomes available.
    """
    fire_key: str
    remain: float                  # seconds until close
    ev1: float                     # EV% side 1 (team1/over)
    ev2: float                     # EV% side 2 (team2/under)
    etop_o1: float                 # etop odds side 1
    etop_o2: float                 # etop odds side 2
    ps_fair_1: float               # no-vig fair side 1 (from store)
    ps_fair_2: float               # no-vig fair side 2 (from store)
    can_press: bool = True
    pool_size: float = 0           # estimated pool (0 = unknown)
    support_1: float = 0           # etop pool share side 1
    support_2: float = 0           # etop pool share side 2
    total_fired: int = 0           # items already fired on this market
    ps_age: float = 9999           # seconds since last WS update
    total_value: float = 0.0       # gold value already bet on this market
    value_cap: float = 0.0         # gold value cap (0 = uncapped)
    # Future: pinnacle_confidence, line_movement_speed, kelly_edge

    @property
    def best_ev(self) -> float:
        return max(self.ev1, self.ev2)

    @property
    def best_side(self) -> int:
        """1 = team1/over, 2 = team2/under."""
        return 1 if self.ev1 >= self.ev2 else 2


# ═══════════════════════════════════════════════════════════════════════
# STRATEGY — all betting decisions
# ═══════════════════════════════════════════════════════════════════════

class Strategy:
    """All betting decisions in one place.

    Pure logic. No API calls. No state mutations.

    Usage:
        strategy = Strategy()                    # defaults
        strategy = Strategy(bot_config)          # panel overrides
        strategy = Strategy({'MIN_EV': 2.0})     # custom
    """

    def __init__(self, config: dict = None):
        cfg = config or {}

        # ── Betting parameters ────────────────────────────────
        self.min_ev: float = cfg.get('MIN_EV', 1.5)
        self.max_items: int = int(cfg.get('MAX_ITEMS', 40))
        self.trigger_secs: float = cfg.get('TRIGGER_SECS', 50)
        self.prefire_secs: float = cfg.get('PREFIRE_SECS', 80)

        # ── Data freshness limits ─────────────────────────────
        self.max_ps_age: float = cfg.get('MAX_PS_AGE', 300)

        # ── Priority tuning ───────────────────────────────────
        self.urgency_weight: float = cfg.get('URGENCY_WEIGHT', 1.0)
        self.ev_weight: float = cfg.get('EV_WEIGHT', 1.0)
        self.pool_weight: float = cfg.get('POOL_WEIGHT', 1.0)
        self.urgency_grouping: float = cfg.get('URGENCY_GROUPING', 2.0)

    # ── Gates ─────────────────────────────────────────────────

    def should_fire(self, snap: MarketSnapshot) -> bool:
        """DEPRECATED: Gates now live in _fire_cycle (unified gate list).
        Kept for backwards compatibility. Not called by fire_zone."""
        if not snap.can_press:
            return False
        if snap.remain <= 0:
            return False
        if snap.best_ev < self.min_ev:
            return False
        if snap.total_fired >= self.max_items:
            return False
        # Value cap gate
        if snap.value_cap > 0 and snap.total_value >= snap.value_cap:
            return False
        # Data freshness gate
        if snap.ps_age > self.max_ps_age:
            return False
        return True

    def reject_reason(self, snap: MarketSnapshot) -> str:
        """DEPRECATED: Gate failure reasons now logged by _fire_cycle."""
        if not snap.can_press:
            return 'LOCKED'
        if snap.remain <= 0:
            return 'EXPIRED'
        if snap.best_ev < self.min_ev:
            return f'EV={snap.best_ev:+.2f}% < {self.min_ev}%'
        if snap.total_fired >= self.max_items:
            return f'CAPPED ({snap.total_fired}/{self.max_items})'
        if snap.value_cap > 0 and snap.total_value >= snap.value_cap:
            return f'VALUE_CAPPED total={snap.total_value:.1f} cap={snap.value_cap:.1f}'
        if snap.ps_age > self.max_ps_age:
            return f'STALE (ps_age={int(snap.ps_age)}s)'
        return 'UNKNOWN'

    # ── Side selection ────────────────────────────────────────

    def pick_side(self, snap: MarketSnapshot) -> int:
        """Which side to bet. 1 = team1/over, 2 = team2/under."""
        return snap.best_side

    # ── Priority ──────────────────────────────────────────────

    def priority(self, snap: MarketSnapshot) -> float:
        """Higher = fire first.

        Priority = urgency × EV × pool_factor
        See FIRE_FLOW_GUIDEBOOK.md Chapter 4.
        """
        ev = snap.best_ev
        urgency = max(1.0, self.trigger_secs - snap.remain) * self.urgency_weight

        if snap.pool_size > 0:
            pool_factor = (1000.0 / max(snap.pool_size, 100)) * self.pool_weight
            return urgency * ev * pool_factor

        return urgency * ev * self.ev_weight

    # ── Sizing ────────────────────────────────────────────────

    def bet_count(self, snap: MarketSnapshot) -> int:
        """DEPRECATED: Gate + inventory handle this now."""
        if snap.total_fired >= self.max_items:
            return 0
        if snap.value_cap > 0 and snap.total_value >= snap.value_cap:
            return 0
        return 1

    # ── Ordering ──────────────────────────────────────────────

    def sort_candidates(self, snaps: List[MarketSnapshot]) -> List[MarketSnapshot]:
        """Sort fire zone candidates by priority.

        Groups markets within ±tolerance seconds (same urgency).
        Within a group: sort by priority.
        Between groups: most urgent first.
        See FIRE_FLOW_GUIDEBOOK.md Chapter 4 (Urgency Grouping).
        """
        if not snaps:
            return []

        groups = self._group_by_urgency(snaps)
        ordered = []
        for group in groups:
            group.sort(key=lambda s: -self.priority(s))
            ordered.extend(group)
        return ordered

    def _group_by_urgency(self, snaps: List[MarketSnapshot]) -> List[List[MarketSnapshot]]:
        by_time = sorted(snaps, key=lambda s: s.remain)
        groups = []
        current = [by_time[0]]
        for s in by_time[1:]:
            if s.remain - current[0].remain <= self.urgency_grouping:
                current.append(s)
            else:
                groups.append(current)
                current = [s]
        groups.append(current)
        return groups

    # ── Config reload ─────────────────────────────────────────

    def reload(self, config: dict):
        """Hot-reload parameters from panel config."""
        self.__init__(config)
