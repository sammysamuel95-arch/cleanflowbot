"""
engine/fire_zone.py — Fire zone mechanics.

Handles: sequential firing, cancel flow.
Delegates ALL decisions to Strategy.

Flow per cycle:
  1. Build MarketSnapshot from store data (WS + REST refresh)
  2. strategy.should_fire() → gate
  3. strategy.sort_candidates() → priority order
  4. press.do sequentially (75ms gap)
  5. Log full fire data for post-review

Usage (in unified loop):
    fz = FireZone(strategy, etop_api, ps_auth, inventory)
    summary = await fz.run_cycle(fire_candidates, store, listing)
    await fz.check_cancels(tracked)
"""

import asyncio
import time
from typing import List, Dict, Optional, Tuple

from engine.strategy import Strategy, MarketSnapshot
from core.ev import calculate_ev, compute_ev
from core.math import no_vig
from core.logger import log_info, log_warn, log_market, log_prefire, log_fire
from core.fire_db import FireDB
import config


# ═══════════════════════════════════════════════════════════════════════
# FIRE STATE — per-market tracking across cycles
# ═══════════════════════════════════════════════════════════════════════

class FireState:
    """Tracks fire progress for one market across cycles."""
    __slots__ = (
        'fire_key', 'total_fired', 'press_ids',
        'first_fire_at', 'last_fire_at',
        'pre_fire_odds', 'cumulative_value', 'rounds_fired',
        'locked_side',
        'total_value',
        'value_cap',
        'raw_pool',
        '_last_phase',
        'consumed_ids',
    )

    def __init__(self, fire_key: str):
        self.fire_key = fire_key
        self.total_fired: int = 0
        self.press_ids: List[str] = []
        self.first_fire_at: float = 0
        self.last_fire_at: float = 0
        self.pre_fire_odds: float = 0
        self.cumulative_value: float = 0
        self.rounds_fired: int = 0
        self.locked_side: int = 0  # 0 = not locked, 1 = team1/over, 2 = team2/under
        self.total_value: float = 0.0
        self.value_cap: float = 0.0
        self.raw_pool: float = 0.0
        self._last_phase: str = ""
        self.consumed_ids: list = []


# ═══════════════════════════════════════════════════════════════════════
# FIRE ZONE
# ═══════════════════════════════════════════════════════════════════════

class FireZone:
    """Fire zone mechanics. Strategy makes decisions, this executes.

    See FIRE_FLOW_GUIDEBOOK.md for complete architecture.
    """

    def __init__(self, strategy: Strategy, etop_api, ps_auth, inventory,
                 session_id: str = None):
        self.strategy = strategy
        self.etop_api = etop_api
        self.ps_auth = ps_auth
        self.inventory = inventory
        self._fire_state: Dict[str, FireState] = {}
        self._session_id = session_id or f"s_{int(time.time())}"
        self._fire_db = FireDB()

    def get_fire_state(self, fire_key: str) -> FireState:
        if fire_key not in self._fire_state:
            self._fire_state[fire_key] = FireState(fire_key)
        return self._fire_state[fire_key]

    # ══════════════════════════════════════════════════════════
    # MAIN FIRE CYCLE
    # ══════════════════════════════════════════════════════════

    async def run_cycle(
        self,
        candidates: List[Tuple[str, object]],
        store,
        listing: dict,
    ) -> dict:
        """One fire zone cycle. Returns summary dict.

        Simplified: no AOS. Uses store data (WS + REST refresh) directly.
        """
        cycle_start = time.time()
        summary = {
            'candidates': len(candidates),
            'verified': 0, 'fired': 0, 'skipped': 0,
            'items_placed': 0,
        }

        if not candidates:
            return summary

        # ── Step 1: Build snapshots from store data ──
        snapshots = []

        for i, item in enumerate(candidates):
            if len(item) == 5:
                fk, tm, cached_ev1, cached_ev2, phase_label = item
            elif len(item) == 4:
                fk, tm, cached_ev1, cached_ev2 = item
                phase_label = "P?"
            else:
                fk, tm = item
                cached_ev1, cached_ev2 = None, None
                phase_label = "P?"
            em = tm.etop_market
            sub = listing.get(em.mid, {})
            fs = self.get_fire_state(fk)

            ps_age = store.get_line_age(
                em.ps_event_id, em.map_num, em.market)
            ps_age = ps_age if ps_age is not None else 9999

            snap = self._build_snapshot(em, sub, fs, store, ps_age,
                                        cached_ev=(cached_ev1, cached_ev2))
            if snap is None:
                summary['skipped'] += 1
                continue

            snap._phase_label = phase_label
            summary['verified'] += 1
            snapshots.append((fk, tm, snap))

        # ── Step 2: Locked side enforcement ──
        fireable = []
        for fk, tm, snap in snapshots:
            fs = self.get_fire_state(fk)
            em = tm.etop_market

            # Locked side: if EV on locked side dropped below floor, skip
            if fs.locked_side > 0:
                locked_ev = snap.ev1 if fs.locked_side == 1 else snap.ev2
                if locked_ev < self.strategy.min_ev:
                    log_prefire(f"{em.team1} vs {em.team2} [{em.label}]",
                               f"SKIP: locked_side={fs.locked_side} ev={locked_ev:.2f}% flipped")
                    summary['skipped'] += 1
                    continue

            fireable.append((fk, tm, snap))

        if not fireable:
            return summary

        # ── Step 3: Priority order ──
        ordered_snaps = self.strategy.sort_candidates(
            [s for _, _, s in fireable])

        snap_to_tm = {s.fire_key: (fk, tm) for fk, tm, s in fireable}
        ordered = []
        for s in ordered_snaps:
            if s.fire_key in snap_to_tm:
                fk, tm = snap_to_tm[s.fire_key]
                ordered.append((fk, tm, s))

        # ── Step 4: Fire sequentially ──
        for fk, tm, snap in ordered:
            em = tm.etop_market
            fs = self.get_fire_state(fk)
            if fs.locked_side > 0:
                side = fs.locked_side
            else:
                side = self.strategy.pick_side(snap)

            # Pick item
            remaining_cap = snap.value_cap - snap.total_value if snap.value_cap > 0 else float('inf')
            item_id = self.inventory.get_next_item(remaining_cap)
            if item_id is None:
                log_prefire(f"{em.team1} vs {em.team2} [{em.label}]",
                           f"NO_ITEM remaining_cap={remaining_cap:.1f} fired={fs.total_fired}")
                summary['skipped'] += 1
                continue
            item_ids = [item_id]
            item_val = self.inventory.get_item_value(item_id)

            # Pre-fire tracking
            if fs.total_fired == 0:
                fs.pre_fire_odds = em.o1 if side == 1 else em.o2
                fs.first_fire_at = time.time()

            # FIRE
            success, msg, press_id = await self.etop_api.press(
                em.mid, item_ids, side)

            if success:
                self.inventory.consume(item_ids)
                fs.consumed_ids.extend(item_ids)
                fs.total_fired += len(item_ids)
                fs.last_fire_at = time.time()
                fs.rounds_fired += 1
                tm.state = 'MONITOR'
                if fs.locked_side == 0:
                    fs.locked_side = side
                for iid in item_ids:
                    fs.total_value += self.inventory.get_item_value(iid)
                log_info(f"[FIRE] {fk} total_value=Gold {fs.total_value:.1f}/{fs.value_cap:.1f} total_fired={fs.total_fired}")
                if self._fire_db:
                    self._fire_db.update_fire_state(
                        fk, fs.total_fired, fs.locked_side,
                        fs.total_value, fs.value_cap, self._session_id
                    )

                # Fetch press IDs for cancel
                try:
                    fresh_pids = await self.etop_api.get_cancellable_presses(em.mid)
                    if fresh_pids:
                        fs.press_ids = fresh_pids
                except Exception:
                    pass

                # ── FULL FIRE LOG ──
                side_name = 'left' if side == 1 else 'right'
                fair_str = f"{snap.ps_fair_1:.3f}/{snap.ps_fair_2:.3f}" if snap.ps_fair_1 > 0 else "store"
                priority_score = self.strategy.priority(snap)
                log_market(em.team1, em.team2, em.market, em.map_num,
                          "FIRE!",
                          mid=em.mid,
                          remain=f"{int(snap.remain)}s",
                          ev=f"{snap.best_ev:+.2f}%",
                          phase=getattr(snap, '_phase_label', 'P?'),
                          side=side_name,
                          items=len(item_ids),
                          total=fs.total_fired,
                          cap=f"{fs.total_value:.0f}/{fs.value_cap:.0f}g",
                          etop=f"{snap.etop_o1:.2f}/{snap.etop_o2:.2f}",
                          ps_fair=fair_str,
                          ps_age=f"{int(snap.ps_age)}s",
                          priority=f"{priority_score:.1f}")

                # ── DB RECORD ──
                ps_raw_t1, ps_raw_t2, ps_raw_ts = self._get_store_raw(em, store)
                item_value = sum(self.inventory.get_item_value(iid) for iid in item_ids)
                self._fire_db.log_fire(
                    session_id=self._session_id,
                    fire_key=fk,
                    team1=em.team1, team2=em.team2,
                    market=em.market, map_num=em.map_num,
                    side=side, vsid=side,
                    remain_secs=snap.remain,
                    etop_o1=snap.etop_o1, etop_o2=snap.etop_o2,
                    etop_captured_at=0,
                    ps_raw_t1=ps_raw_t1, ps_raw_t2=ps_raw_t2,
                    ps_raw_captured_at=ps_raw_ts,
                    ps_fair_t1=snap.ps_fair_1, ps_fair_t2=snap.ps_fair_2,
                    ps_fair_captured_at=ps_raw_ts,
                    ev_at_fire=snap.best_ev, ev_calculated_at=time.time(),
                    priority_score=priority_score,
                    items_count=len(item_ids), item_ids=item_ids,
                    item_value=item_value,
                    ps_age=snap.ps_age, aos_age=0, etop_age=0,
                    press_result='success',
                )

                summary['fired'] += 1
                summary['items_placed'] += len(item_ids)
            else:
                log_fire(f"{em.team1} vs {em.team2} [{em.label}]",
                        fs.total_fired, "FAIL", msg=msg)
                self._fire_db.log_fire(
                    session_id=self._session_id,
                    fire_key=fk,
                    team1=em.team1, team2=em.team2,
                    market=em.market, map_num=em.map_num,
                    side=side, vsid=side,
                    remain_secs=snap.remain,
                    etop_o1=snap.etop_o1, etop_o2=snap.etop_o2,
                    etop_captured_at=0,
                    ps_raw_t1=0, ps_raw_t2=0, ps_raw_captured_at=0,
                    ps_fair_t1=snap.ps_fair_1, ps_fair_t2=snap.ps_fair_2,
                    ps_fair_captured_at=0,
                    ev_at_fire=snap.best_ev, ev_calculated_at=time.time(),
                    priority_score=self.strategy.priority(snap),
                    items_count=len(item_ids), item_ids=item_ids,
                    item_value=0,
                    ps_age=snap.ps_age, aos_age=0, etop_age=0,
                    press_result='fail', press_error=msg,
                )

            # 75ms gap (etop sequential requirement)
            await asyncio.sleep(0.075)

        # ── Cap check ──
        for fk, tm, snap in ordered:
            fs = self.get_fire_state(fk)
            if fs.total_fired >= self.strategy.max_items:
                em = tm.etop_market
                log_market(em.team1, em.team2, em.market, em.map_num,
                          "COMPLETE", total=fs.total_fired,
                          rounds=fs.rounds_fired)

        # ── Cycle summary log ──
        cycle_ms = int((time.time() - cycle_start) * 1000)
        summary['cycle_ms'] = cycle_ms
        if summary['fired'] > 0:
            log_info(f"[FIRE_ZONE] {summary}")

        return summary

    # ══════════════════════════════════════════════════════════
    # SNAPSHOT BUILDER — store data
    # ══════════════════════════════════════════════════════════

    def _build_snapshot(self, em, sub, fs, store, ps_age, cached_ev=None) -> Optional[MarketSnapshot]:
        """Build MarketSnapshot from store data. Returns None if no usable data.

        Gate: ps_age must be < max_ps_age_unverified (300s default).
        """
        if cached_ev and cached_ev[0] is not None:
            ev1, ev2 = cached_ev
        else:
            ev1, ev2 = compute_ev(em, store)
            if ev1 is None:
                return None

        # Extract fair odds from store for logging
        fair_h, fair_a = 0.0, 0.0
        try:
            raw_t1, raw_t2, _ = self._get_store_raw(em, store)
            if raw_t1 > 1.0 and raw_t2 > 1.0:
                fair_h, fair_a = no_vig(raw_t1, raw_t2)
        except Exception:
            pass

        snap = MarketSnapshot(
            fire_key=em.fire_key,
            remain=em.remain,
            ev1=ev1, ev2=ev2,
            etop_o1=em.o1, etop_o2=em.o2,
            ps_fair_1=fair_h, ps_fair_2=fair_a,
            can_press=em.can_press,
            support_1=sub.get('support1', sub.get('vs1_support', 0)) if sub else 0,
            support_2=sub.get('support2', sub.get('vs2_support', 0)) if sub else 0,
            total_fired=fs.total_fired,
            ps_age=ps_age,
        )
        snap.total_value = fs.total_value
        snap.value_cap = min(fs.value_cap, config.HARD_CAP) if fs.value_cap > 0 else config.HARD_CAP
        return snap

    def _get_store_raw(self, em, store):
        """Return (raw_t1, raw_t2, timestamp) from store for this market."""
        try:
            eid, m = em.ps_event_id, em.map_num
            t1, t2 = em.ps_name_team1, em.ps_name_team2
            data = store._data.get((eid, m), {})
            if em.market == 'ml':
                e1 = data.get('ml', {}).get(t1)
                e2 = data.get('ml', {}).get(t2)
            elif em.market == 'hdp':
                line1 = -abs(em.line) if em.giving_side == 'team1' else abs(em.line)
                line2 = abs(em.line) if em.giving_side == 'team1' else -abs(em.line)
                gps = em.giving_team_ps or t1
                ops = t2 if gps == t1 else t1
                e1 = data.get('hdp', {}).get((t1, line1)) or data.get('hdp', {}).get((t1, line2))
                e2 = data.get('hdp', {}).get((t2, line1)) or data.get('hdp', {}).get((t2, line2))
            elif em.market in ('ou', 'team_total'):
                e1 = data.get('ou', {}).get(('over', em.line))
                e2 = data.get('ou', {}).get(('under', em.line))
            else:
                return 0.0, 0.0, 0.0
            raw_t1 = e1.raw if e1 else 0.0
            raw_t2 = e2.raw if e2 else 0.0
            ts = max(
                e1.timestamp if e1 else 0,
                e2.timestamp if e2 else 0,
            )
            return raw_t1, raw_t2, ts
        except Exception:
            return 0.0, 0.0, 0.0

    # ══════════════════════════════════════════════════════════
    # CANCEL FLOW
    # ══════════════════════════════════════════════════════════

    async def check_cancels(self, tracked: dict):
        """Cancel bets on markets extended beyond 300s."""
        for _mid, tm in list(tracked.items()):
            fk = tm.fire_key
            fs = self._fire_state.get(fk)
            if not fs or fs.total_fired == 0:
                continue
            em = tm.etop_market
            if em.remain <= 300:
                continue

            log_info(f"[CANCEL] {em.team1} vs {em.team2} [{em.label}] "
                     f"extended_to={int(em.remain)}s fired={fs.total_fired}")

            try:
                fresh_pids = await self.etop_api.get_cancellable_presses(em.mid)
                if fresh_pids:
                    fs.press_ids = fresh_pids
            except Exception:
                pass

            cancelled = 0
            for pid in fs.press_ids:
                try:
                    ok, msg = await self.etop_api.regret(em.mid, pid)
                    if ok:
                        cancelled += 1
                    else:
                        log_warn("CANCEL", f"{fk} pid={pid} FAILED: {msg}")
                except Exception as e:
                    log_warn("CANCEL", f"{fk} pid={pid} ERROR: {e}")

            self.inventory.unconsume(fs.consumed_ids)
            freed_count = len(fs.consumed_ids)

            log_info(f"[CANCEL] {em.team1} vs {em.team2} [{em.label}] "
                     f"cancelled={cancelled}/{len(fs.press_ids)} "
                     f"freed={freed_count}")

            # Full reset
            fs.total_fired = 0
            fs.press_ids = []
            fs.locked_side = 0
            fs.total_value = 0
            fs.consumed_ids = []
            fs.value_cap = 0
            tm.state = 'MONITOR'

    # ══════════════════════════════════════════════════════════
    # CLEANUP
    # ══════════════════════════════════════════════════════════

    def cleanup(self, fire_key: str):
        self._fire_state.pop(fire_key, None)
