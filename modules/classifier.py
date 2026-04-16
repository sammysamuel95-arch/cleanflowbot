"""
modules/classifier.py — Etop data → Market entries in Container.

READS:  etop parents (raw parent list from match_list)
        etop listing (flat odds per sub-market)

WRITES: container.markets[mid] — creates new entries, updates odds + lifecycle
        container._etop_markets[mid] — EtopMarket for compute_ev compatibility

DOES NOT:
  - Match to PS (matcher's job)
  - Compute EV (valuator's job)
  - Fire bets (fire engine's job)
  - Write dashboard (dashboard's job)

Uses:
  - matching/classify.py (classify_etop_sub — proven, unchanged)
  - matching/pair.py (infer_sport_hint — proven, unchanged)
  - core/models.py (EtopMarket — proven, unchanged)
"""

import time
from core.logger import log_info, log_warn, log_market
from matching.classify import classify_etop_sub
from matching.pair import infer_sport_hint
from core.models import EtopMarket
from container import Market


def run(container, parents, listing):
    """One classifier cycle. Called every time etop data arrives.

    1. Register: create Market entries for new etop subs
    2. Update: refresh odds/remain/can_press from listing
    3. Lifecycle: handle death, close, return, cancel, cleanup
    """
    _register(container, parents, listing)
    _update_lifecycle(container, listing)


# ═══════════════════════════════════════════════════════════════════════
# REGISTER — every etop sub enters container.markets immediately
# ═══════════════════════════════════════════════════════════════════════

def _register(container, parents, listing):
    """Create new Market entries for unseen subs. Update existing ones.

    Mirrors brain STEP 1 from main.py exactly. No logic changes.
    """
    # Sort parents by closest remain first
    sorted_parents = sorted(parents, key=lambda p: (
        p.get('sublist', [{}])[0].get('remainTime', 999999999)
        if p.get('sublist') else 999999999))

    for par in sorted_parents:
        vs1 = (par.get('vs1') or {}).get('name', '')
        vs2 = (par.get('vs2') or {}).get('name', '')
        if not vs1 or not vs2:
            continue

        cat_type = (par.get('category') or {}).get('type', '').lower()
        league = (par.get('league') or {}).get('name', '')
        vs1_image = (par.get('vs1') or {}).get('image', '')
        hint = infer_sport_hint(league=league, image=vs1_image, cat_type=cat_type)

        for sub in par.get('sublist', []):
            em = _build_etop_market(sub, par, hint)
            if not em:
                continue

            mid = em.mid

            if mid in container.markets:
                # Existing market — update EtopMarket, preserve PS match data
                m = container.markets[mid]
                old_fk = m.fire_key

                # Preserve PS match (set by matcher, not our job)
                em.ps_event_id = m.ps_event_id or em.ps_event_id
                em.ps_name_team1 = m.ps_name_team1 or em.ps_name_team1
                em.ps_name_team2 = m.ps_name_team2 or em.ps_name_team2

                # Update fields that can change (line moves, label changes)
                m.fire_key = em.fire_key
                m.line = em.line or m.line
                m.label = em.label
                m.giving_side = em.giving_side
                m.raw_type = em.raw_type

                if old_fk != em.fire_key:
                    log_info(f"[CLASSIFIER] [LINE_CHANGE] {em.team1} vs {em.team2} "
                             f"mid={mid} {old_fk} → {em.fire_key}")

                # Store updated EtopMarket (for compute_ev)
                container._etop_markets[mid] = em

            else:
                # New market — create Market entry
                if not cat_type:
                    log_warn("CLASSIFIER", f"Empty cat_type for {vs1} vs {vs2} league={league}")

                m = Market(
                    mid=mid,
                    fire_key=em.fire_key,
                    team1=vs1,
                    team2=vs2,
                    market_type=em.market,
                    line=em.line or 0.0,
                    map_num=em.map_num,
                    label=em.label,
                    giving_side=em.giving_side,
                    game=em.game,
                    sport=hint or '',
                    league=league,
                    cat_type=cat_type,
                    parent_id=em.parent_id,
                    raw_type=em.raw_type,
                )
                container.markets[mid] = m
                container._etop_markets[mid] = em


# ═══════════════════════════════════════════════════════════════════════
# UPDATE LIFECYCLE — odds, death, close, return, cancel, cleanup
# ═══════════════════════════════════════════════════════════════════════

def _update_lifecycle(container, listing):
    """Update odds from listing. Handle death/close/return transitions.

    Mirrors brain STEP 3 from main.py (lifecycle portion only).
    Does NOT compute EV or determine monitor/prefire/fire_zone states.
    Those are the valuator's job.
    """
    now = time.time()

    for mid in list(container.markets.keys()):
        m = container.markets[mid]
        sub = listing.get(mid)

        # ── CLOSED is terminal — only revives if remain > 0 ──
        if m.state == 'CLOSED':
            if sub and int(sub.get('remain', 0)) > 0:
                m.dead_at = 0.0
                m.locked_at = 0.0
                m.state = 'UNMATCHED' if m.ps_event_id is None else 'MATCHED'
                log_info(f"[CLASSIFIER] {m.team1} vs {m.team2} [{m.label}] "
                         f"RETURNED from CLOSED remain={int(sub['remain'])}s")
            else:
                continue

        # ── Not in listing → start death timer ──
        if not sub:
            if m.dead_at == 0:
                m.dead_at = now

            # 120s gone → CLOSED
            if m.dead_at > 0 and (now - m.dead_at) > 120:
                m.can_press = False
                if not m.locked_at:
                    m.locked_at = m.dead_at
                m.state = 'CLOSED'
                m.remain = 0

            # 7 days gone → cleanup
            if m.dead_at > 0 and (now - m.dead_at) > 604800:
                log_info(f"[CLASSIFIER] {m.team1} vs {m.team2} [{m.label}] CLEANUP 7d")
                del container.markets[mid]
                container._etop_markets.pop(mid, None)

            continue

        # ── In listing — read fresh data ──
        o1 = sub['o1']
        o2 = sub['o2']
        remain = sub['remain']
        can_press = sub['can_press']
        cancel_code = sub.get('cancel_code')
        seconds = int(remain)

        # ── Cancel code → CLOSED ──
        if cancel_code:
            if m.state != 'CLOSED':
                log_info(f"[CLASSIFIER] {m.team1} vs {m.team2} [{m.label}] "
                         f"CANCELLED code={cancel_code}")
            m.can_press = False
            if not m.locked_at:
                m.locked_at = now
            m.state = 'CLOSED'
            m.remain = 0
            continue

        # ── Track death timer ──
        if seconds <= 0:
            if m.dead_at == 0:
                m.dead_at = now
        else:
            if m.dead_at > 0:
                m.locked_at = 0.0
            m.dead_at = 0.0

        # ── Update live odds ──
        m.o1 = o1
        m.o2 = o2
        m.remain = remain
        m.can_press = can_press
        m.cancel_code = cancel_code
        m.last_seen = now
        m.etop_age = container.etop_age

        # Also update the EtopMarket (for compute_ev)
        em = container._etop_markets.get(mid)
        if em:
            em.update_odds(o1, o2, remain, can_press)

        # ── Dead for 120s → CLOSED ──
        if m.dead_at > 0 and (now - m.dead_at) > 120:
            m.can_press = False
            if not m.locked_at:
                m.locked_at = m.dead_at
            m.state = 'CLOSED'
            m.remain = 0
            continue

        # ── State: classifier only sets UNMATCHED or preserves current ──
        # Valuator will set MATCHED/MONITOR/APPROACHING/PREFIRE/FIRE_ZONE
        if m.ps_event_id is None and m.state not in ('CLOSED',):
            m.state = 'UNMATCHED'


# ═══════════════════════════════════════════════════════════════════════
# BUILD ETOP MARKET — classify one etop sub into EtopMarket
# ═══════════════════════════════════════════════════════════════════════

def _build_etop_market(sub, parent, hint):
    """Classify one etop sub → EtopMarket. No PS data needed.

    Mirrors _build_market() from main.py exactly. No logic changes.
    Uses classify_etop_sub (proven, unchanged).
    """
    vs1 = (parent.get('vs1') or {}).get('name', '')
    vs2 = (parent.get('vs2') or {}).get('name', '')
    league = (parent.get('league') or {}).get('name', '')
    cat_type = (parent.get('category') or {}).get('type', '').lower()
    vs1_image = (parent.get('vs1') or {}).get('image', '')
    gw_id = str((parent.get('offerMatch') or {}).get('id', ''))
    parent_bo = parent.get('bo', 3)

    mtype = sub.get('type', 0)
    remain = sub.get('remainTime', 0) / 1000.0

    mkt_desc = classify_etop_sub(
        mtype=mtype, map_num=sub.get('map', 0),
        offer_score=sub.get('offerScore', 0) or 0,
        offer_team=sub.get('offerTeam', 0) or 0,
        total_score=sub.get('totalScore', None),
        total_time=sub.get('totalTime', None),
        mid=str(sub.get('id', '')),
        gw_id=gw_id,
        parent_bo=parent_bo, vs1=vs1, vs2=vs2, hint=hint,
        image=vs1_image, league=league,
        cat_type=cat_type, sport_hint=hint)

    if not mkt_desc:
        return None

    fav = mkt_desc.get('favorite')
    giving_side = fav if fav in ('team1', 'team2') else None

    return EtopMarket(
        team1=vs1, team2=vs2,
        o1=(sub.get('vs1') or {}).get('odds', 0),
        o2=(sub.get('vs2') or {}).get('odds', 0),
        market=mkt_desc['market'],
        line=mkt_desc.get('line') or 0,
        map_num=mkt_desc['map'],
        label=mkt_desc['label'],
        giving_side=giving_side,
        mid=str(sub.get('id', '')),
        parent_id=str(parent.get('id', '')),
        remain=remain,
        can_press=sub.get('canPress', False),
        raw_type=mtype,
        league=league,
        url='',
        game=mkt_desc.get('game', ''),
        ps_name_team1=None,
        ps_name_team2=None,
        ps_event_id=None,
    )
