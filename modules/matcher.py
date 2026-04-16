"""
modules/matcher.py — Find PS event for UNMATCHED markets.

READS:  container.markets where state == UNMATCHED
        ps_store.get_events_for_matching() (PS event list)
        evidence_db (alias database)

WRITES: container.markets[mid].ps_event_id
        container.markets[mid].ps_name_team1/2
        container.markets[mid].match_confidence
        container.markets[mid].match_method
        container.markets[mid].state → MATCHED
        container._etop_markets[mid].ps_event_id/ps_name_team1/2

DOES NOT:
  - Classify markets (classifier's job)
  - Compute EV (valuator's job)
  - Fire bets (fire engine's job)

Uses:
  - matching/structured_matcher.py (match_event — filter-first, replaces evidence.py)
  - matching/league_map.py (league scoring + game type gate)
  - matching/alias_db.py (AliasDB — proven, unchanged)

MATCHING:
  A. Teammate lookup — same team+map already matched → copy eid
  B. Structured matcher — filter-first, geometric mean scoring
  C. EID conflict resolution — better match wins, loser gets evicted
"""

from thefuzz import fuzz
from core.logger import log_info, log_warn
from matching.structured_matcher import match_event


# ── Module state (persists across cycles — EID conflict resolution) ───
_eid_block_counts: dict = {}    # (match_key, eid) → block count
_match_block_counts: dict = {}  # match_key → total blocks


def run(container, ps_store, evidence_db):
    """One matcher cycle. Called after classifier.

    1. Teammate lookup: UNMATCHED market with matched teammate → copy eid
    2. Match: find PS event for remaining UNMATCHED markets
    3. Kills eid resolution: re-point Total Kills OU to "(Kills)" event
    """
    _lookup_teammates(container)
    _match_unmatched(container, ps_store, evidence_db)
    _resolve_kills_eids(container, ps_store)


# ═══════════════════════════════════════════════════════════════════════
# TEAMMATE LOOKUP — copy eid from matched teammate (every cycle)
# ═══════════════════════════════════════════════════════════════════════

def _lookup_teammates(container):
    """Copy eid from matched teammate every cycle.
    Key: (team1, team2, map_num) — same team+map = same eid."""
    known = {}
    for mid, m in container.markets.items():
        if m.ps_event_id is not None:
            key = (m.team1, m.team2, m.map_num)
            if key not in known:
                known[key] = m

    if not known:
        return

    for mid, m in container.markets.items():
        if m.ps_event_id is not None or m.state != 'UNMATCHED':
            continue
        key = (m.team1, m.team2, m.map_num)
        teammate = known.get(key)
        if teammate:
            m.ps_event_id = teammate.ps_event_id
            m.ps_name_team1 = teammate.ps_name_team1
            m.ps_name_team2 = teammate.ps_name_team2
            m.match_method = 'teammate'
            m.match_confidence = 100.0
            m.state = 'MATCHED'
            em = container._etop_markets.get(mid)
            if em:
                em.ps_event_id = teammate.ps_event_id
                em.ps_name_team1 = teammate.ps_name_team1
                em.ps_name_team2 = teammate.ps_name_team2
            log_info(f"[MATCHER] {m.team1} vs {m.team2} [{m.label}] "
                     f"← teammate [{teammate.label}] eid={teammate.ps_event_id}")


# ═══════════════════════════════════════════════════════════════════════
# MATCH — find PS event for UNMATCHED markets
# ═══════════════════════════════════════════════════════════════════════

def _match_unmatched(container, ps_store, evidence_db):
    """Find PS events for UNMATCHED markets.

    Uses structured_matcher.match_event (filter-first: game type → league → pair score).
    Added: rejected_eids filtering, cleaner structure.
    """
    global _eid_block_counts, _match_block_counts

    # Group UNMATCHED markets by parent team pair
    unmatched_groups = {}
    for mid, m in container.markets.items():
        if m.ps_event_id is None and m.state == 'UNMATCHED':
            key = f"{m.team1}|{m.team2}"
            unmatched_groups.setdefault(key, []).append(m)

    if not unmatched_groups:
        return

    # Clean up block counts for pairs no longer in conflict
    active_eids = {m.ps_event_id for m in container.markets.values()
                   if m.ps_event_id is not None}
    for mk in list(_match_block_counts.keys()):
        mk_eids = {eid for (k, eid) in _eid_block_counts if k == mk}
        # Never clear permanent blocks (count == 999)
        if any(_eid_block_counts.get((mk, eid), 0) == 999 for eid in mk_eids):
            continue
        if not mk_eids.intersection(active_eids):
            _match_block_counts.pop(mk, None)
            for bk in [k for k in _eid_block_counts if k[0] == mk]:
                _eid_block_counts.pop(bk, None)

    # Try to match each group
    for key, group in unmatched_groups.items():
        # Skip if persistently blocked
        if _match_block_counts.get(key, 0) >= 5:
            continue

        m0 = group[0]
        vs1 = m0.team1
        vs2 = m0.team2
        hint = m0.sport
        league = m0.league

        # Get PS events for this sport
        ps_events = ps_store.get_events_for_matching(hint)

        # Run structured matching (filter-first: game type → league → pair score)
        ev_match, match_score, method, is_forward = match_event(
            vs1, vs2, m0.cat_type, league, ps_events, evidence_db,
            hint=hint or '')

        if not (ev_match and method == 'AUTO_MATCH'):
            continue

        ps_event_id = ev_match['eid']

        # VERIFY GATE REMOVED — structured matcher does verification:
        # game type filter + league filter + both-team pair score = no false positives

        # ── EID conflict resolution ──
        if _has_eid_conflict(container, key, vs1, vs2, ps_event_id, ps_store):
            continue

        # ── Determine PS name order (is_forward from structured_matcher) ──
        ev_info = ps_store.get_event(ps_event_id)
        if not ev_info:
            continue

        if is_forward:
            ps_name_t1, ps_name_t2 = ev_info['home'], ev_info['away']
        else:
            ps_name_t1, ps_name_t2 = ev_info['away'], ev_info['home']

        log_info(f"[MATCHER] {vs1} vs {vs2} → {ps_name_t1} vs {ps_name_t2} "
                 f"eid={ps_event_id} method={method} score={match_score}")

        # ── Apply match to all markets in group ──
        for m in group:
            m.ps_event_id = ps_event_id
            m.ps_name_team1 = ps_name_t1
            m.ps_name_team2 = ps_name_t2
            m.match_confidence = match_score
            m.match_method = method
            m.league = ev_info.get('league', '')
            m.state = 'MATCHED'

            # Also set on EtopMarket (for compute_ev)
            em = container._etop_markets.get(m.mid)
            if em:
                em.ps_event_id = ps_event_id
                em.ps_name_team1 = ps_name_t1
                em.ps_name_team2 = ps_name_t2


def _has_eid_conflict(container, key, vs1, vs2, ps_event_id, ps_store):
    """Check if another market already claims this eid. Resolve by name quality.

    Returns True if conflict blocks the new match.
    Returns False if no conflict or new match wins (old evicted).
    """
    global _eid_block_counts, _match_block_counts

    for other_mid, other_m in list(container.markets.items()):
        if other_m.ps_event_id != ps_event_id:
            continue
        if other_m.ps_event_id is None:
            continue
        if other_m.team1 == vs1 and other_m.team2 == vs2:
            continue  # same team pair, not a conflict

        # Different teams claim same eid — resolve by name quality
        ev_info = ps_store.get_event(ps_event_id)
        if not ev_info:
            # Can't resolve without event info — block new match
            _bk = (key, ps_event_id)
            _eid_block_counts[_bk] = _eid_block_counts.get(_bk, 0) + 1
            _match_block_counts[key] = _match_block_counts.get(key, 0) + 1
            if _eid_block_counts[_bk] <= 3:
                log_warn("MATCHER", f"[EID_CONFLICT] {vs1} vs {vs2} → eid={ps_event_id} "
                         f"used by {other_m.team1} vs {other_m.team2} (no event info)")
            return True

        ps_home = ev_info['home'].lower()
        ps_away = ev_info['away'].lower()

        old_score = max(
            fuzz.ratio(other_m.team1.lower(), ps_home) +
            fuzz.ratio(other_m.team2.lower(), ps_away),
            fuzz.ratio(other_m.team1.lower(), ps_away) +
            fuzz.ratio(other_m.team2.lower(), ps_home))

        new_score = max(
            fuzz.ratio(vs1.lower(), ps_home) + fuzz.ratio(vs2.lower(), ps_away),
            fuzz.ratio(vs1.lower(), ps_away) + fuzz.ratio(vs2.lower(), ps_home))

        if new_score > old_score:
            # New match is better — evict old
            log_info(f"[MATCHER] [EID_RESOLVE] Evicting {other_m.team1} vs "
                     f"{other_m.team2} (score={old_score}) in favor of "
                     f"{vs1} vs {vs2} (score={new_score}) for eid={ps_event_id}")

            old_key = f"{other_m.team1}|{other_m.team2}"
            _eid_block_counts.pop((old_key, ps_event_id), None)
            _match_block_counts.pop(old_key, None)

            # Reset ALL markets from old pair that used this eid
            for reset_mid, reset_m in container.markets.items():
                if (reset_m.ps_event_id == ps_event_id and
                        reset_m.team1 == other_m.team1 and
                        reset_m.team2 == other_m.team2):
                    reset_m.ps_event_id = None
                    reset_m.ps_name_team1 = None
                    reset_m.ps_name_team2 = None
                    reset_m.match_confidence = 0.0
                    reset_m.state = 'UNMATCHED'

                    em = container._etop_markets.get(reset_mid)
                    if em:
                        em.ps_event_id = None
                        em.ps_name_team1 = None
                        em.ps_name_team2 = None

            return False  # no conflict anymore, proceed

        else:
            # Old match is better — block new
            _bk = (key, ps_event_id)
            _eid_block_counts[_bk] = _eid_block_counts.get(_bk, 0) + 1
            _match_block_counts[key] = _match_block_counts.get(key, 0) + 1
            if _eid_block_counts[_bk] <= 3:
                log_warn("MATCHER", f"[EID_CONFLICT] {vs1} vs {vs2} "
                         f"(score={new_score}) blocked by "
                         f"{other_m.team1} vs {other_m.team2} "
                         f"(score={old_score}) for eid={ps_event_id}")
            elif _eid_block_counts[_bk] == 4:
                log_warn("MATCHER", f"[EID_CONFLICT] {vs1} vs {vs2} eid={ps_event_id} "
                         f"— suppressing further logs")
            return True

    return False  # no conflict found


# ═══════════════════════════════════════════════════════════════════════
# KILLS EID RESOLUTION — re-point Total Kills OU to "(Kills)" event
# ═══════════════════════════════════════════════════════════════════════

def _resolve_kills_eids(container, ps_store):
    """Re-point matched Total Kills OU markets from regular event eid to "(Kills)" event.

    PS3838 publishes kills OU under a separate event with "(Kills)" suffixed to
    team names. Matcher (structured or teammate lookup) initially assigns the
    regular event eid. This function finds the corresponding "(Kills)" event
    and swaps em.ps_event_id + m.ps_event_id.

    Runs every cycle (idempotent):
      - Already pointing at kills eid (PS names contain "(Kills)") → skip
      - No PS names in _event_teams yet for regular eid → skip, retry next cycle
      - No kills event found in PS store → skip, retry next cycle

    Uses find_event_id_any (NOT find_event_id) for the kills lookup:
      - find_event_id searches only _event_teams. _event_teams is populated
        when WS sends ML/HDP updates (StandardStore writes home/away then).
      - Kills events carry only OU data, so _event_teams is never populated
        for them — find_event_id would silently miss every kills event.
      - find_event_id_any falls back to _events (populated via LEFT_MENU /
        REST / register_event), which is where kills events actually live.

    Does NOT update ps_name_team1/2 — keeps regular-event names. compute_ev's
    alt_eid fallback in core/ev.py handles the reverse case (ML/HDP market
    that accidentally inherited a kills eid via teammate lookup).
    """
    for mid, m in container.markets.items():
        if m.ps_event_id is None:
            continue
        if m.state in ('UNMATCHED', 'CLOSED', 'DROPPED'):
            continue
        em = container._etop_markets.get(mid)
        if not em:
            continue
        if em.market not in ('ou', 'team_total'):
            continue
        if em.map_num <= 0:
            continue
        if 'Kills' not in (m.label or ''):
            continue

        # Get PS team names for the REGULAR eid from _event_teams (ML/HDP layer).
        # Regular events carry ML/HDP so they populate _event_teams normally.
        evt = ps_store._event_teams.get(em.ps_event_id)
        if not evt:
            continue  # no PS names yet — retry next cycle
        ps_h, ps_a = evt[0], evt[1]

        # Already resolved to kills event? Skip.
        if '(Kills)' in ps_h or '(Kills)' in ps_a:
            continue

        # Look up the "(Kills)" event. Use find_event_id_any because kills
        # events only have OU data → never populate _event_teams → live in
        # _events only. Try both team orderings.
        kills_eid = (
            ps_store.find_event_id_any(f"{ps_h} (Kills)", f"{ps_a} (Kills)")
            or ps_store.find_event_id_any(f"{ps_a} (Kills)", f"{ps_h} (Kills)")
        )
        if not kills_eid or kills_eid == em.ps_event_id:
            continue

        # Swap eid on both Market and EtopMarket
        old_eid = em.ps_event_id
        em.ps_event_id = kills_eid
        m.ps_event_id = kills_eid

        log_info(f"[MATCH_KILLS] {m.team1} vs {m.team2} [{m.label}] "
                 f"regular={old_eid} → kills={kills_eid}")
