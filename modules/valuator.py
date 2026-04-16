"""
modules/valuator.py — Compute EV for matched markets.

READS:  container.markets where ps_event_id is set
        container._etop_markets (EtopMarket for compute_ev)
        ps_store (TheOnlyStore — fair odds)
        container.sport_configs (per-sport phase EV thresholds)

WRITES: container.markets[mid].ev1/ev2/best_ev/best_side
        container.markets[mid].ps_fair_1/ps_fair_2/ps_raw_1/ps_raw_2
        container.markets[mid].ps_age
        container.markets[mid].phase/phase_min_ev
        container.markets[mid].state (MATCHED/MONITOR/APPROACHING/PREFIRE/FIRE_ZONE)

DOES NOT:
  - Match events (matcher's job)
  - Fire bets (fire engine's job)
  - Touch UNMATCHED or CLOSED markets
  - Modify EtopMarket or TheOnlyStore

Uses:
  - core/ev.py (compute_ev — proven, unchanged)
  - core/math.py (no_vig — proven, unchanged)
"""

from core.ev import compute_ev
from core.math import no_vig
from core.logger import log_info, log_market

# Market types that compute_ev supports — only these can produce EV
TRADEABLE_MARKETS = {'ml', 'hdp', 'ou', 'team_total'}


def run(container, ps_store, fast_remain=None):
    """One valuator cycle. Called after classifier + matcher.

    For every matched market:
      1. Compute EV using proven compute_ev (unchanged)
      2. Try alt-eids if EV is None (existing logic, unchanged)
      3. Set phase based on per-sport config
      4. Update state: MATCHED → MONITOR → APPROACHING → PREFIRE → FIRE_ZONE
    """
    for mid, m in container.markets.items():
        # Skip UNMATCHED — no ps_event_id means compute_ev returns None always
        if m.ps_event_id is None:
            continue
        # FAST path: skip markets far from close (valued on FULL cycles)
        if fast_remain is not None and m.remain > fast_remain:
            continue
        if m.state == 'CLOSED':
            continue

        em = container._etop_markets.get(mid)
        if not em:
            continue

        # Untradeable market type → DROPPED (matched but can't compute EV)
        if em.market not in TRADEABLE_MARKETS:
            if m.state not in ('CLOSED', 'DROPPED'):
                m.state = 'DROPPED'
            continue

        # ── Compute EV ────────────────────────────────────────
        ev1, ev2 = compute_ev(em, ps_store)
        resolved_eid = em.ps_event_id  # track which eid actually has data

        # ── Alt-eid resolution ─────────────────────────────────
        # Some map markets (mk=3) live under different PS eids.
        # CRITICAL: always restore original eid via try/finally.
        # Never let an exception leave em.ps_event_id corrupted.
        if ev1 is None and em.ps_event_id and em.ps_name_team1:
            original_eid = em.ps_event_id
            try:
                # Try alternate eids (mk=3 map markets)
                alt_eids = ps_store.find_alternate_eids(original_eid, em.ps_name_team1)
                for alt_eid in alt_eids:
                    em.ps_event_id = alt_eid
                    ev1, ev2 = compute_ev(em, ps_store)
                    if ev1 is not None:
                        resolved_eid = alt_eid
                        break
                    em.ps_event_id = original_eid
            finally:
                em.ps_event_id = original_eid  # ALWAYS restore, no exceptions

        # ── Write EV to container market ──────────────────────
        if ev1 is None and em.market in ('ou', 'team_total') and em.map_num > 0:
            evt = ps_store._event_teams.get(em.ps_event_id) if em.ps_event_id else None
            log_info(f"[VALUATOR_EV_NONE] {m.team1} vs {m.team2} [{m.label}] "
                     f"eid={em.ps_event_id} t1={em.ps_name_team1} t2={em.ps_name_team2} "
                     f"evt_names={evt} line={em.line} map={em.map_num}")
        m.ev1 = ev1
        m.ev2 = ev2
        if ev1 is not None and ev2 is not None:
            m.best_ev = max(ev1, ev2)
            m.best_side = 1 if ev1 >= ev2 else 2
        else:
            m.best_ev = None
            m.best_side = 0

        # ── Write PS fair/raw odds using resolved eid ─────────
        _write_ps_odds(m, em, ps_store, resolved_eid)

        # ── PS age using resolved eid ─────────────────────────
        ps_age = ps_store.get_line_age(resolved_eid, em.map_num, em.market)
        m.ps_age = ps_age

        # ── Phase + state ─────────────────────────────────────
        _update_phase_and_state(m, container)


def _write_ps_odds(m, em, ps_store, resolved_eid=None):
    """Extract PS fair + raw odds using the eid that actually has data."""
    try:
        eid = resolved_eid or em.ps_event_id
        map_num = em.map_num
        data = ps_store._data.get((eid, map_num))
        if not data:
            return

        if em.market == 'ml':
            e1 = data.get('ml', {}).get(ps_store._n(em.ps_name_team1))
            e2 = data.get('ml', {}).get(ps_store._n(em.ps_name_team2))
        elif em.market == 'hdp':
            gps = em.giving_team_ps or em.ps_name_team1
            ops = em.ps_name_team2 if gps == em.ps_name_team1 else em.ps_name_team1
            e1 = data.get('hdp', {}).get((ps_store._n(gps), round(-abs(em.line), 2)))
            e2 = data.get('hdp', {}).get((ps_store._n(ops), round(abs(em.line), 2)))
        elif em.market in ('ou', 'team_total'):
            e1 = data.get('ou', {}).get(('over', round(em.line, 2)))
            e2 = data.get('ou', {}).get(('under', round(em.line, 2)))
        else:
            return

        m.ps_raw_1 = e1.raw if e1 else None
        m.ps_raw_2 = e2.raw if e2 else None
        m.ps_fair_1 = e1.fair if e1 else None
        m.ps_fair_2 = e2.fair if e2 else None
    except Exception:
        pass


def _update_phase_and_state(m, container):
    """Set phase (P1/P2/P3) and state based on per-sport config.

    States (simple):
      UNMATCHED  → no PS match (classifier sets this)
      MATCHED    → has PS match, no EV yet
      MONITOR    → has EV, remain > trigger
      FIRE_ZONE  → remain ≤ trigger, P1/P2/P3 are phases within
      CLOSED     → dead (classifier sets this)

    Phases within FIRE_ZONE:
      P1: trigger to trigger×2/3   (conservative, higher EV threshold)
      P2: trigger×2/3 to trigger/3 (medium)
      P3: trigger/3 to 0           (aggressive, lowest EV threshold)

    Esports (trigger=90):    P1=90-60s  P2=60-30s  P3=30-0s
    Basketball (trigger=50): P1=50-33s  P2=33-16s  P3=16-0s
    Soccer (trigger=50):     P1=50-33s  P2=33-16s  P3=16-0s
    """
    seconds = int(m.remain)
    sport_cfg = container.get_sport_config(m.sport)
    trigger = sport_cfg.trigger_secs
    p2_boundary = trigger * 2 / 3
    p3_boundary = trigger / 3

    # ── Phase (which EV threshold applies) ─────────────────
    if seconds > trigger:
        m.phase = ''
        m.phase_min_ev = 0.0
    elif seconds > p2_boundary:
        m.phase = 'P1'
        m.phase_min_ev = sport_cfg.phase1_ev
    elif seconds > p3_boundary:
        m.phase = 'P2'
        m.phase_min_ev = sport_cfg.phase2_ev
    else:
        m.phase = 'P3'
        m.phase_min_ev = sport_cfg.phase3_ev

    # ── State (simple: MATCHED / MONITOR / FIRE_ZONE) ─────
    if m.state == 'CLOSED':
        return  # classifier owns close transitions

    if seconds <= 0:
        return  # classifier handles via death timer

    if m.ev1 is None:
        m.state = 'MATCHED'
    elif seconds > trigger:
        m.state = 'MONITOR'
    else:
        m.state = 'FIRE_ZONE'  # P1/P2/P3 are phases, not states
