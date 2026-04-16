"""
tests/test_live_comparison.py — Compare structured_matcher vs old bot's matches.

Uses real data from CleanFlowBot (old bot):
  - data/event_map.json → 4,248 PS events
  - data/dash_state.json → 116 matched + 29 unmatched markets

For each matched market:
  1. Run structured_matcher with same inputs
  2. Check: does it find the SAME PS team pair?

For each unmatched market:
  1. Run structured_matcher
  2. Report any NEW matches found (bonus, not required)

Run: cd ~/VibeCoding/ProjectBot/CleanFlowBot-clean && python3 tests/test_live_comparison.py
"""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matching.structured_matcher import match_event, pair_score

# ── Paths ──
OLD_BOT = os.path.expanduser('~/VibeCoding/ProjectBot/CleanFlowBot')
EVENT_MAP = os.path.join(OLD_BOT, 'data', 'event_map.json')
DASH_STATE = os.path.join(OLD_BOT, 'data', 'dash_state.json')

# ── Load PS events ──
with open(EVENT_MAP) as f:
    raw_events = json.load(f)

# Build ps_events list: [(home, away, eid, league), ...]
ps_events_all = []
for eid_str, ev in raw_events.items():
    ps_events_all.append((ev['home'], ev['away'], int(eid_str), ev.get('league', '')))

print(f"Loaded {len(ps_events_all)} PS events from event_map.json")

# Pre-filter by sport for speed
ps_by_sp = {}
for h, a, eid, lg in ps_events_all:
    sp = raw_events[str(eid)]['sp']
    ps_by_sp.setdefault(sp, []).append((h, a, eid, lg))

print(f"  sp=12 (esports): {len(ps_by_sp.get(12, []))}")
print(f"  sp=29 (soccer):  {len(ps_by_sp.get(29, []))}")
print(f"  sp=4  (basketball): {len(ps_by_sp.get(4, []))}")

# ── Load dash_state ──
with open(DASH_STATE) as f:
    dash = json.load(f)

markets = dash.get('markets', [])
matched_raw = [m for m in markets if m.get('ps') and m['ps'] != '']
unmatched_raw = [m for m in markets if not m.get('ps') or m['ps'] == '']

print(f"\nLoaded {len(matched_raw)} matched + {len(unmatched_raw)} unmatched markets")

# ── Game → cat_type mapping ──
GAME_TO_CAT = {
    'dota': 'dota2', 'dota2': 'dota2',
    'cs2': 'csgo', 'csgo': 'csgo',
    'lol': 'lol',
    'valorant': 'valorant',
    'basketball': 'sports_basketball',
    'soccer': 'sports_soccer', 'sports_soccer': 'sports_soccer',
}

GAME_TO_HINT = {
    'dota': 'esports', 'dota2': 'esports',
    'cs2': 'esports', 'csgo': 'esports',
    'lol': 'esports', 'valorant': 'esports',
    'basketball': 'basketball',
    'soccer': 'soccer',
}

HINT_TO_SP = {'esports': 12, 'basketball': 4, 'soccer': 29}

# ── Deduplicate by team pair (same pair has multiple markets: ML, HDP, OU) ──
def extract_pair(market):
    """Extract (etop_t1, etop_t2, game) from dash market."""
    n = market.get('n', '')
    parts = n.split(' vs ', 1)
    if len(parts) != 2:
        return None
    t1, t2 = parts[0].strip(), parts[1].strip()
    game = market.get('game', '')
    return (t1, t2, game)

# Build unique matched pairs
matched_pairs = {}
for m in matched_raw:
    pair = extract_pair(m)
    if not pair:
        continue
    key = f"{pair[0]}|{pair[1]}"
    if key not in matched_pairs:
        ps_parts = m['ps'].split(' vs ', 1)
        expected_ps = (ps_parts[0].strip(), ps_parts[1].strip()) if len(ps_parts) == 2 else None
        matched_pairs[key] = {
            'etop_t1': pair[0], 'etop_t2': pair[1],
            'game': pair[2], 'expected_ps': expected_ps,
        }

# Build unique unmatched pairs
unmatched_pairs = {}
for m in unmatched_raw:
    pair = extract_pair(m)
    if not pair:
        continue
    key = f"{pair[0]}|{pair[1]}"
    if key not in unmatched_pairs:
        unmatched_pairs[key] = {
            'etop_t1': pair[0], 'etop_t2': pair[1],
            'game': pair[2],
        }

print(f"\nUnique matched pairs: {len(matched_pairs)}")
print(f"Unique unmatched pairs: {len(unmatched_pairs)}")

# ═══════════════════════════════════════════════════════════════
# TEST 1: Regression — does structured_matcher find same matches?
# ═══════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("TEST 1: REGRESSION — matched pairs (must find same PS match)")
print("=" * 70)

same = 0
different = 0
missed = 0
details_diff = []
details_miss = []

for key, info in sorted(matched_pairs.items()):
    t1, t2, game = info['etop_t1'], info['etop_t2'], info['game']
    expected = info['expected_ps']
    cat_type = GAME_TO_CAT.get(game, '')
    hint = GAME_TO_HINT.get(game, 'esports')
    sp = HINT_TO_SP.get(hint, 12)

    # Get PS events for this sport
    ps_events = ps_by_sp.get(sp, [])

    # Run structured_matcher (no alias_db for this test)
    ev_match, score, method, is_forward = match_event(
        t1, t2, cat_type, '', ps_events, alias_db=None, hint=hint)

    if ev_match and method == 'AUTO_MATCH':
        # Check if same PS teams (order doesn't matter)
        got_home = ev_match['home']
        got_away = ev_match['away']
        got_set = {got_home.lower(), got_away.lower()}
        exp_set = {expected[0].lower(), expected[1].lower()} if expected else set()

        if got_set == exp_set:
            same += 1
        else:
            different += 1
            details_diff.append(f"  {t1} vs {t2} [{game}]")
            details_diff.append(f"    expected: {expected[0]} vs {expected[1]}")
            details_diff.append(f"    got:      {got_home} vs {got_away} (score={score})")
    else:
        missed += 1
        details_miss.append(f"  {t1} vs {t2} [{game}] → {method} (score={score})")

print(f"\n  SAME MATCH:  {same}/{len(matched_pairs)}")
print(f"  DIFFERENT:   {different}/{len(matched_pairs)}")
print(f"  MISSED:      {missed}/{len(matched_pairs)}")

if details_diff:
    print(f"\n  --- Different matches ---")
    for line in details_diff:
        print(line)

if details_miss:
    print(f"\n  --- Missed (structured_matcher couldn't match) ---")
    for line in details_miss:
        print(line)

# ═══════════════════════════════════════════════════════════════
# TEST 2: Bonus — can structured_matcher find any unmatched?
# ═══════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("TEST 2: BONUS — unmatched pairs (any new matches?)")
print("=" * 70)

new_matches = 0
still_unmatched = 0

for key, info in sorted(unmatched_pairs.items()):
    t1, t2, game = info['etop_t1'], info['etop_t2'], info['game']
    cat_type = GAME_TO_CAT.get(game, '')
    hint = GAME_TO_HINT.get(game, 'esports')
    sp = HINT_TO_SP.get(hint, 12)

    ps_events = ps_by_sp.get(sp, [])

    ev_match, score, method, is_forward = match_event(
        t1, t2, cat_type, '', ps_events, alias_db=None, hint=hint)

    if ev_match and method in ('AUTO_MATCH', 'SUGGEST'):
        new_matches += 1
        print(f"  NEW: {t1} vs {t2} [{game}] → {ev_match['home']} vs {ev_match['away']} "
              f"score={score} method={method}")
    else:
        still_unmatched += 1

print(f"\n  New matches found:  {new_matches}/{len(unmatched_pairs)}")
print(f"  Still unmatched:    {still_unmatched}/{len(unmatched_pairs)}")

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
regression_pct = (same / len(matched_pairs) * 100) if matched_pairs else 0
print(f"  Regression: {same}/{len(matched_pairs)} same ({regression_pct:.1f}%)")
print(f"  Different:  {different}")
print(f"  Missed:     {missed}")
print(f"  New bonus:  {new_matches}")

if regression_pct >= 90:
    print(f"\n  ✓ PASS — {regression_pct:.1f}% regression match rate")
else:
    print(f"\n  ✗ NEEDS REVIEW — {regression_pct:.1f}% regression match rate")

sys.exit(0 if regression_pct >= 90 else 1)
