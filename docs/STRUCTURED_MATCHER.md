# STRUCTURED MATCHER — Architecture Design
# Location: ~/VibeCoding/ProjectBot/CleanFlowBot-clean/docs/STRUCTURED_MATCHER.md
#
# Replaces: matching/evidence.py (fuzzy-first, weak)
# New file: matching/structured_matcher.py (filter-first, strong)

---

# THE PROBLEM

Current matching (evidence.py) works backwards:
1. Takes ALL PS events for a sport (~500)
2. Fuzzy matches ONE team name against all 500
3. Scores opponent, tokens, history → combined score
4. Verify gate: fuzz.ratio on both teams must exceed 120
5. Result: slow, fragile, misses short names, false positives on common names

Examples of failures:
- "Clippers" vs "Los Angeles Clippers" → fuzz.ratio = 56 → VERIFY_FAIL
- "Thunder" vs "Oklahoma City Thunder" → fuzz.ratio = 50 → VERIFY_FAIL  
- "Santos" (Brazil) vs "Santos" (Mexico) → wrong match, same name
- "Spirit" vs "Free Spirit Gaming" → false positive
- "LOUD" matches multiple teams → ambiguous

---

# THE SOLUTION: FILTER FIRST, FUZZY LAST

The key insight: `cat_type + league + team1 + team2` is a natural primary key.
There is only ONE "Clippers vs Warriors" in NBA. Only ONE "Liquid vs Na'Vi" in LCS.
Two team names matching within the same league is essentially unique.

New flow:
```
STEP 1: FILTER by game type (cat_type → sport_id)
  500 PS events → 50 (eliminates 90%)
  
STEP 2: FILTER by league (fuzzy league match)  
  50 → 5-10 (eliminates 80% of remainder)
  
STEP 3: SCORE both teams as a PAIR against 5-10 candidates
  Use partial_ratio (substring match, not full string)
  Score = (team1_match + team2_match) / 2
  Both teams must contribute — single team match not enough
  
STEP 4: PICK best pair score
  No verify gate needed — filters already guarantee correctness
  If best score < 40 → no match (genuinely different teams)
```

---

# GAME TYPE MAPPING

```python
# Etop cat_type → PS sport_id
GAME_TYPE_MAP = {
    # Esports (sp=12)
    'csgo': 12, 'cs2': 12,
    'dota2': 12, 'dota': 12,
    'lol': 12,
    'valorant': 12,
    'overwatch': 12,
    'pubg': 12,
    'kog': 12,          # King of Glory
    'starcraft': 12,
    'r6': 12,           # Rainbow Six
    
    # Traditional sports
    'sports_basketball': 4, 'basketball': 4,
    'sports_football': 29, 'sports_soccer': 29, 'soccer': 29, 'football': 29,
    'sports_tennis': 33,
    'sports_baseball': 3,
    'sports_hockey': 19,
}
```

This mapping is 100% reliable. cat_type from etop's category.type field never lies (proven S27).

---

# LEAGUE MAPPING

League matching is fuzzier because naming differs:

```
Etop                          PS3838
────────────────────────────  ────────────────────────
LCS 2026 Spring               LoL - LCS
VCT 2026 Pacific              Valorant - VCT Pacific  
NBA                            Basketball - NBA
Copa del Rey                   Soccer - Copa del Rey
PGL Major Copenhagen 2026      CS2 - PGL Major
MSI 2026                       LoL - MSI
```

Strategy: extract keywords, not exact match.
```python
def league_score(etop_league, ps_league):
    """Score league similarity using keyword overlap."""
    etop_tokens = set(tokenize(etop_league))  # {'lcs', '2026', 'spring'}
    ps_tokens = set(tokenize(ps_league))      # {'lol', 'lcs'}
    
    # Key tokens (league identifiers, not common words)
    KEY_WORDS = {'nba', 'lcs', 'lec', 'lck', 'lpl', 'vct', 'msi',
                 'copa', 'premier', 'bundesliga', 'serie', 'ligue',
                 'pgl', 'major', 'blast', 'esl', 'iem',
                 'pacific', 'americas', 'emea', 'worlds'}
    
    overlap = etop_tokens & ps_tokens & KEY_WORDS
    if overlap:
        return 100  # confident league match
    
    # Fallback: partial_ratio
    return fuzz.partial_ratio(etop_league.lower(), ps_league.lower())
```

---

# PAIR SCORING

```python
from thefuzz import fuzz

def pair_score(et1, et2, ps_home, ps_away):
    """Score how well BOTH etop teams match a PS event as a PAIR.
    
    Uses partial_ratio (substring match):
      "Clippers" inside "Los Angeles Clippers" → 100
      "Na'Vi" inside "Natus Vincere" → low, needs alias
    
    Returns: 0-100 (average of both team scores)
    """
    # Try both orderings (etop team order is arbitrary)
    forward = (
        fuzz.partial_ratio(et1.lower(), ps_home.lower()) +
        fuzz.partial_ratio(et2.lower(), ps_away.lower())
    ) / 2
    
    reverse = (
        fuzz.partial_ratio(et1.lower(), ps_away.lower()) +
        fuzz.partial_ratio(et2.lower(), ps_home.lower())
    ) / 2
    
    return max(forward, reverse)


def match_event(etop_t1, etop_t2, cat_type, league, ps_events, alias_db=None):
    """Find best PS event match using filter-first approach.
    
    Args:
        etop_t1: etop team 1 name
        etop_t2: etop team 2 name
        cat_type: etop category.type (csgo, lol, basketball, etc.)
        league: etop league name
        ps_events: list of PS events [{home, away, eid, sp, league}, ...]
        alias_db: optional alias database for name translation
    
    Returns:
        (best_event, score, method) or (None, 0, 'none')
    """
    
    # Resolve aliases first
    t1 = etop_t1
    t2 = etop_t2
    if alias_db:
        a1 = alias_db.lookup(t1, cat_type)
        a2 = alias_db.lookup(t2, cat_type)
        if a1: t1 = a1.ps_name
        if a2: t2 = a2.ps_name
    
    # ── STEP 1: Filter by game type ──────────────────────
    target_sp = GAME_TYPE_MAP.get(cat_type)
    if target_sp:
        candidates = [e for e in ps_events if e['sp'] == target_sp]
    else:
        candidates = ps_events  # unknown game type, try all
    
    if not candidates:
        return None, 0, 'no_candidates_for_sport'
    
    # ── STEP 2: Filter by league ─────────────────────────
    if league:
        league_scored = []
        for e in candidates:
            ls = league_score(league, e.get('league', ''))
            league_scored.append((e, ls))
        
        # Keep events with league score > 50
        league_filtered = [e for e, ls in league_scored if ls > 50]
        
        # If league filter killed everything, fall back to sport-only
        if league_filtered:
            candidates = league_filtered
    
    # ── STEP 3: Score both teams as a pair ───────────────
    scored = []
    for event in candidates:
        ps_home = event['home']
        ps_away = event['away']
        
        # Primary: direct name match
        score = pair_score(t1, t2, ps_home, ps_away)
        
        # Bonus: if using alias, also try original names
        if t1 != etop_t1 or t2 != etop_t2:
            orig_score = pair_score(etop_t1, etop_t2, ps_home, ps_away)
            score = max(score, orig_score)
        
        scored.append((event, score))
    
    # ── STEP 4: Pick best ────────────────────────────────
    scored.sort(key=lambda x: -x[1])
    
    if not scored:
        return None, 0, 'no_candidates'
    
    best_event, best_score = scored[0]
    
    # Confidence thresholds
    if best_score >= 80:
        return best_event, best_score, 'AUTO_MATCH'
    elif best_score >= 60:
        # Check: is there a close second? If so, ambiguous
        if len(scored) > 1 and scored[1][1] > best_score - 10:
            return best_event, best_score, 'AMBIGUOUS'
        return best_event, best_score, 'AUTO_MATCH'
    elif best_score >= 45:
        return best_event, best_score, 'SUGGEST'
    else:
        return None, best_score, 'no_match'
```

---

# ALIAS DATABASE ROLE

Aliases are still needed for:
- "NAVI" → "Natus Vincere" (abbreviation, partial_ratio won't help)
- "BBL" → "BBL Esports" (acronym)
- "Gen.G" → "Gen.G" (these happen to match)

But aliases are NOT needed for:
- "Clippers" → "Los Angeles Clippers" (partial_ratio handles this)
- "Thunder" → "Oklahoma City Thunder" (partial_ratio handles this)
- "Spirit" → "Team Spirit" (partial_ratio handles this)

So the alias DB becomes smaller and more focused. Only true translations, not city name expansions.

---

# VERIFY GATE REMOVED

No verify gate needed. Here's why:

Old system: match by team1 alone → need verify to catch false positives
New system: match by pair (both teams) within league → false positives nearly impossible

The filters + pair scoring IS the verification:
- Right sport? ✓ (cat_type filter)
- Right league? ✓ (league filter)
- Both teams match? ✓ (pair score)
- No ambiguity? ✓ (gap between #1 and #2 score)

If all four pass, the match is correct. No separate verify step.

---

# EID CONFLICT RESOLUTION (unchanged)

Same logic as current matcher.py:
- If two etop pairs claim the same PS eid, higher pair_score wins
- Loser gets evicted back to UNMATCHED
- Block counts prevent re-matching the same pair to same eid

---

# KILLS EVENT MATCHING

Kills events have "(Kills)" appended: "Liquid (Kills) vs Natus Vincere (Kills)"

After matching the main event, automatically look for kills event:
```python
kills_eid = store.find_event_id(
    ps_home + " (Kills)", 
    ps_away + " (Kills)"
)
if kills_eid:
    market.kills_eid = kills_eid  # used by valuator for kills OU/f10k
```

This is deterministic — no fuzzy matching needed. Once main event is matched, kills event is derived.

---

# IMPLEMENTATION PLAN

1. CREATE: matching/structured_matcher.py
   - GAME_TYPE_MAP
   - league_score()
   - pair_score()  (partial_ratio)
   - match_event() (filter → score → pick)

2. CREATE: matching/league_map.py
   - KEY_WORDS set
   - tokenize()
   - league_score()

3. EDIT: modules/matcher.py
   - Replace evidence_match() call with match_event()
   - Remove verify gate entirely
   - Keep: EID conflict resolution (unchanged)
   - Keep: self-healing (confidence decay, unchanged)

4. TEST with real data:
   - Dump current etop parents + PS events to JSON
   - Run structured_matcher against all pairs
   - Compare: which ones match correctly vs current system?
   - Target: 100% of current matches + Clippers/Thunder/short name fixes

5. KEEP evidence.py as fallback (don't delete yet)

---

# EXPECTED IMPROVEMENTS

| Case | Current | New |
|------|---------|-----|
| Clippers vs Warriors | VERIFY_FAIL (112<120) | AUTO_MATCH (partial_ratio ~90) |
| Thunder vs Suns | VERIFY_FAIL (100<120) | AUTO_MATCH (partial_ratio ~90) |
| NAVI → Natus Vincere | Works via alias | Works via alias (unchanged) |
| Santos (Brazil) vs Santos (Mexico) | Wrong match possible | Correct — league filter separates |
| Spirit vs Free Spirit Gaming | False positive risk | Lower risk — pair score + league |
| New team, no alias | UNMATCHED | May match via partial_ratio |
| Kills OU markets | Separate fallback in 3 places | Automatic kills_eid on match |

---

# FILES

```
matching/
├── structured_matcher.py   ← NEW: filter-first matching
├── league_map.py           ← NEW: league keyword matching
├── evidence.py             ← KEEP as fallback (don't delete)
├── classify.py             ← KEEP (unchanged, PROVEN)
├── alias_db.py             ← KEEP (focused on true translations)
├── pair.py                 ← LEGACY (infer_sport_hint still used)
└── aliases.py              ← LEGACY
```
