"""
matching/structured_matcher.py — Filter-first event matching.

Replaces evidence.py's fuzzy-first approach with:
  STEP 1: Filter by game type (cat_type → league keyword gate)
  STEP 2: Filter by league (keyword overlap score)
  STEP 3: Score BOTH teams as a PAIR (partial_ratio = substring match)
  STEP 4: Pick best pair score — no verify gate needed

The key insight: cat_type + league + team1 + team2 is a natural primary key.
There's only ONE "Clippers vs Warriors" in NBA.
Two team names matching within the same league is essentially unique.

Caller: modules/matcher.py (future) or main.py STEP 2 MATCH (current)
Input:  ps_events = [(home, away, eid, league), ...] from EventStore
Output: (event_dict, score, method, is_forward)

Design: docs/STRUCTURED_MATCHER.md
"""

import math

from thefuzz import fuzz
from typing import Optional, Tuple
from core.logger import log_info, log_warn
from matching.league_map import (
    league_score, game_type_compatible, detect_ps_game, ETOP_GAME_MAP,
)

# ── Team name filler words (stripped before scoring) ──────────────────────
TEAM_FILLER = frozenset({
    'team', 'gaming', 'esports', 'esport', 'fc', 'sc', 'cf', 'ac',
    'united', 'city', 'rovers', 'wanderers', 'republic',
    'de', 'del', 'la', 'le', 'al', 'el', 'cd', 'real',
    'club', 'sport', 'athletic', 'atletico', 'sporting',
})


def _strip_filler(name: str) -> str:
    """Remove common team filler words. Preserves at least one word."""
    words = name.lower().strip().split()
    cleaned = [w for w in words if w not in TEAM_FILLER]
    return ' '.join(cleaned) if cleaned else name.lower().strip()


# ── Thresholds ────────────────────────────────────────────────────────────
SCORE_AUTO     = 80   # geometric mean ≥80 → AUTO_MATCH
SCORE_SUGGEST  = 65   # 65-79 → AUTO_MATCH if gap ≥10 over #2
SCORE_AMBIG    = 45   # 45-64 → SUGGEST (dashboard review, never fires)
# Below 45 → no_match

# League filter threshold
LEAGUE_THRESHOLD = 50


def _team_score(etop_name: str, ps_name: str) -> int:
    """Score a single team name match.

    Uses token_set_ratio on filler-stripped names.
    Whole-word matching, not character substrings.

    Examples:
      "Clippers" vs "Los Angeles Clippers"  → shared token → 100
      "Spirit" vs "Team Spirit"             → shared token → 100
      "Pvision" vs "Parivision"             → similar chars → ~70
      "LOUD" vs "Cloud Rising"              → different words → ~47
      "NAVI" vs "Natus Vincere"             → different words → ~25 (alias job)
    """
    et_clean = _strip_filler(etop_name)
    ps_clean = _strip_filler(ps_name)

    if not et_clean or not ps_clean:
        return fuzz.token_set_ratio(etop_name.lower().strip(),
                                    ps_name.lower().strip())

    return fuzz.token_set_ratio(et_clean, ps_clean)


def pair_score(et1: str, et2: str, ps_home: str, ps_away: str) -> Tuple[int, bool]:
    """Score how well BOTH etop teams match a PS event as a PAIR.

    Uses GEOMETRIC MEAN: √(team1 × team2)
    Both teams must contribute — one 0 kills the pair.
    But a strong opponent still boosts a weaker (abbreviated) match.

    Properties:
      100 + 100 = 100  (both exact)
      100 + 85  = 92   (one shortened)
      100 + 64  = 80   (minimum for AUTO_MATCH)
      100 + 50  = 70   (SUGGEST range)
      100 + 25  = 50   (one needs alias)
      47  + 25  = 34   (both garbage → no_match)

    Returns: (score 0-100, is_forward)
        is_forward = True if et1↔ps_home, et2↔ps_away
    """
    # Forward: et1=home, et2=away
    fwd_t1 = _team_score(et1, ps_home)
    fwd_t2 = _team_score(et2, ps_away)

    # Reverse: et1=away, et2=home
    rev_t1 = _team_score(et1, ps_away)
    rev_t2 = _team_score(et2, ps_home)

    # Geometric mean: √(a × b) — both must contribute
    fwd_geo = math.isqrt(fwd_t1 * fwd_t2)
    rev_geo = math.isqrt(rev_t1 * rev_t2)

    if fwd_geo >= rev_geo:
        return fwd_geo, True
    return rev_geo, False


def match_event(
    etop_t1: str,
    etop_t2: str,
    cat_type: str,
    league: str,
    ps_events: list,
    alias_db=None,
    hint: str = '',
) -> Tuple[Optional[dict], int, str, bool]:
    """Find best PS event match using filter-first approach.

    Args:
        etop_t1:   etop team 1 name (vs1)
        etop_t2:   etop team 2 name (vs2)
        cat_type:  etop category.type ('csgo', 'lol', 'dota2', 'valorant', ...)
        league:    etop league name
        ps_events: [(ps_home, ps_away, eid, league), ...] from EventStore
        alias_db:  optional AliasDB instance for name translation
        hint:      sport hint for alias lookup ('esports', 'basketball', 'soccer')

    Returns:
        (event_dict, score, method, is_forward)
        event_dict: {'home': str, 'away': str, 'eid': int, 'league': str} or None
        score:      0-100 pair score
        method:     'AUTO_MATCH', 'SUGGEST', 'AMBIGUOUS', 'no_match', ...
        is_forward: True if etop_t1 maps to ps_home
    """
    if not ps_events:
        return None, 0, 'no_events', False

    # ── Resolve aliases (name translation only) ──────────────────────
    t1, t2 = etop_t1, etop_t2
    alias_used = False
    if alias_db:
        game_key = cat_type or hint or ''
        a1 = alias_db.lookup(t1, game_key)
        a2 = alias_db.lookup(t2, game_key)
        if a1:
            t1 = a1.ps_name
            alias_used = True
        if a2:
            t2 = a2.ps_name
            alias_used = True

    # ── STEP 1: Filter by game type ──────────────────────────────────
    # Within sp=12 (esports), PS league string tells us the actual game.
    # cat_type = 'csgo' but PS event is 'LoL - LCS' → instant reject.
    # For non-esports, caller already filtered by sp via event_store.
    if cat_type:
        candidates = []
        for h, a, eid, lg in ps_events:
            # Skip (Kills) variant events — matched deterministically later
            if '(Kills)' in h or '(Kills)' in a:
                continue
            if game_type_compatible(cat_type, lg):
                candidates.append({'home': h, 'away': a, 'eid': eid, 'league': lg})
    else:
        candidates = [
            {'home': h, 'away': a, 'eid': eid, 'league': lg}
            for h, a, eid, lg in ps_events
            if '(Kills)' not in h and '(Kills)' not in a
        ]

    step1_count = len(candidates)
    if not candidates:
        return None, 0, 'no_candidates_after_gametype', False

    # ── STEP 2: Filter by league ─────────────────────────────────────
    if league:
        league_scored = []
        for ev in candidates:
            ls = league_score(league, ev['league'])
            league_scored.append((ev, ls))

        league_filtered = [ev for ev, ls in league_scored if ls > LEAGUE_THRESHOLD]

        # If league filter killed everything, fall back to game-type-only
        if league_filtered:
            candidates = league_filtered

    step2_count = len(candidates)

    # ── STEP 3: Score both teams as a pair ───────────────────────────
    scored = []
    for ev in candidates:
        # Primary: translated names (alias-resolved)
        score, is_fwd = pair_score(t1, t2, ev['home'], ev['away'])

        # Also try original names if alias was used (sometimes original is better)
        if alias_used:
            orig_score, orig_fwd = pair_score(etop_t1, etop_t2, ev['home'], ev['away'])
            if orig_score > score:
                score, is_fwd = orig_score, orig_fwd

        scored.append((ev, score, is_fwd))

    scored.sort(key=lambda x: -x[1])

    if not scored:
        return None, 0, 'no_candidates', False

    best_ev, best_score, best_fwd = scored[0]

    # Log top candidates for debugging
    for ev, sc, fwd in scored[:3]:
        direction = "FWD" if fwd else "REV"
        log_info(f"[STRUCT_MATCH] {etop_t1} vs {etop_t2} ~ "
                 f"{ev['home']} vs {ev['away']} | "
                 f"pair={sc} {direction} eid={ev['eid']} "
                 f"lg_match={'Y' if league_score(league, ev['league']) > LEAGUE_THRESHOLD else 'N'}")

    # ── STEP 3b: Unique opponent promotion ───────────────────────────
    # If pair score is in SUGGEST range (45-64) BUT one team scores 80+
    # individually AND that team appears in only ONE candidate →
    # the opponent uniquely identifies the match. Safe to promote.
    if SCORE_AMBIG <= best_score < SCORE_SUGGEST:
        if best_fwd:
            ind_t1 = _team_score(t1, best_ev['home'])
            ind_t2 = _team_score(t2, best_ev['away'])
        else:
            ind_t1 = _team_score(t1, best_ev['away'])
            ind_t2 = _team_score(t2, best_ev['home'])

        strong_score = max(ind_t1, ind_t2)

        if strong_score >= 80:
            if ind_t2 >= ind_t1:
                strong_ps_name = best_ev['away'] if best_fwd else best_ev['home']
            else:
                strong_ps_name = best_ev['home'] if best_fwd else best_ev['away']

            strong_clean = _strip_filler(strong_ps_name)
            unique_count = sum(
                1 for ev in candidates
                if _strip_filler(ev['home']) == strong_clean
                or _strip_filler(ev['away']) == strong_clean
            )

            if unique_count == 1:
                best_score = SCORE_SUGGEST
                log_info(f"[STRUCT_MATCH] PROMOTE {etop_t1} vs {etop_t2}: "
                         f"unique opponent '{strong_ps_name}' "
                         f"(individual={strong_score}, "
                         f"{'t2' if ind_t2 >= ind_t1 else 't1'} strong, "
                         f"1/{len(candidates)} candidates)")
            else:
                log_info(f"[STRUCT_MATCH] NO_PROMOTE {etop_t1} vs {etop_t2}: "
                         f"'{strong_ps_name}' in {unique_count} candidates "
                         f"(ambiguous)")

    # ── STEP 4: Pick best + classify confidence ──────────────────────
    if best_score >= SCORE_AUTO:
        method = 'AUTO_MATCH'
    elif best_score >= SCORE_SUGGEST:
        # Check for close second — if gap < 10, ambiguous
        if len(scored) > 1 and scored[1][1] > best_score - 10:
            method = 'AMBIGUOUS'
            log_warn("STRUCT_MATCH",
                     f"Ambiguous: {etop_t1} vs {etop_t2} | "
                     f"#1={best_ev['home']} vs {best_ev['away']} score={best_score} | "
                     f"#2={scored[1][0]['home']} vs {scored[1][0]['away']} score={scored[1][1]}")
        else:
            method = 'AUTO_MATCH'
    elif best_score >= SCORE_AMBIG:
        method = 'SUGGEST'
    else:
        log_info(f"[STRUCT_MATCH] {etop_t1} vs {etop_t2} → no match "
                 f"(best={best_score}, need≥{SCORE_AMBIG}) "
                 f"step1={step1_count} step2={step2_count}")
        return None, best_score, 'no_match', False

    log_info(f"[STRUCT_MATCH] {etop_t1} vs {etop_t2} → "
             f"{best_ev['home']} vs {best_ev['away']} "
             f"eid={best_ev['eid']} score={best_score} method={method} "
             f"{'FWD' if best_fwd else 'REV'} "
             f"(step1={step1_count} step2={step2_count})")

    return best_ev, best_score, method, best_fwd


def find_kills_eid(ps_home: str, ps_away: str, event_store) -> Optional[int]:
    """After matching main event, find the (Kills) variant deterministically.

    No fuzzy matching — exact name + " (Kills)" suffix.
    Returns eid or None.
    """
    kills_home = ps_home + " (Kills)"
    kills_away = ps_away + " (Kills)"
    return event_store.find_event_id(kills_home, kills_away)
