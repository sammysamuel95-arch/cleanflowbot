"""
tests/test_structured_matcher.py — Final scoring system tests.

Scoring: _team_score (token_set_ratio) → pair_score (geometric mean)
Thresholds: 80 auto, 65 auto-with-gap, 45 suggest, <45 no_match

Run: cd ~/VibeCoding/ProjectBot/CleanFlowBot-clean && python3 tests/test_structured_matcher.py
"""

import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matching.structured_matcher import pair_score, match_event, _team_score
from matching.league_map import league_score, game_type_compatible

_pass = 0
_fail = 0

def check(label, got, want, op='>='):
    global _pass, _fail
    ok = False
    if op == '>=' and got >= want: ok = True
    elif op == '<' and got < want: ok = True
    elif op == '<=' and got <= want: ok = True
    elif op == '==' and got == want: ok = True
    if ok:
        _pass += 1
        print(f"  ✓ {label}: {got} {op} {want}")
    else:
        _fail += 1
        print(f"  ✗ {label}: {got} NOT {op} {want}  ← FAIL")

# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 1: _team_score — individual team scoring")
print("=" * 60)

# Exact / near-exact
check("G2 Esports exact", _team_score("G2 Esports", "G2 Esports"), 95)
check("Fnatic exact", _team_score("Fnatic", "Fnatic"), 95)
check("Mouz case-only", _team_score("Mouz", "MOUZ"), 95)

# Shortened (shared token)
check("Clippers → LA Clippers", _team_score("Clippers", "Los Angeles Clippers"), 80)
check("Thunder → OKC Thunder", _team_score("Thunder", "Oklahoma City Thunder"), 80)
check("Spirit → Team Spirit", _team_score("Spirit", "Team Spirit"), 80)
check("Liquid → Team Liquid", _team_score("Liquid", "Team Liquid"), 80)
check("76ers → Philadelphia 76ers", _team_score("76ers", "Philadelphia 76ers"), 80)
check("Lakers → LA Lakers", _team_score("Lakers", "Los Angeles Lakers"), 80)
check("Keyd Stars → Vivo Keyd Stars", _team_score("Keyd Stars", "Vivo Keyd Stars"), 80)
check("Trail Blazers → Portland Trail Blazers", _team_score("Trail Blazers", "Portland Trail Blazers"), 80)

# Abbreviated (alias territory — score should be low-to-mid)
check("NAVI (alias job)", _team_score("NAVI", "Natus Vincere"), 50, '<')
check("NiP (alias job)", _team_score("NiP", "Ninjas In Pyjamas"), 50, '<')
check("EG (alias job)", _team_score("EG", "Evil Geniuses"), 50, '<')

# Abbreviated but similar chars (should pass through to SUGGEST)
check("Pvision → Parivision", _team_score("Pvision", "Parivision"), 55)

# Completely different
check("LOUD ≠ Cloud Rising", _team_score("LOUD", "Cloud Rising"), 55, '<')
check("EYEBALLERS ≠ ENCE", _team_score("EYEBALLERS", "ENCE"), 45, '<')
check("Yakult Brothers ≠ FURIA", _team_score("Yakult Brothers", "FURIA"), 45, '<')

print()
# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 2: pair_score — geometric mean")
print("=" * 60)

# Perfect pairs
score, fwd = pair_score("Clippers", "Warriors", "Los Angeles Clippers", "Golden State Warriors")
check("Clippers vs Warriors", score, 80)

score, fwd = pair_score("Thunder", "Suns", "Oklahoma City Thunder", "Phoenix Suns")
check("Thunder vs Suns", score, 80)

score, fwd = pair_score("Spirit", "Falcons", "Team Spirit", "Team Falcons")
check("Spirit vs Falcons", score, 80)

score, fwd = pair_score("G2 Esports", "Fnatic", "G2 Esports", "Fnatic")
check("G2 vs Fnatic exact", score, 95)

# Reversed order
score, fwd = pair_score("Warriors", "Clippers", "Los Angeles Clippers", "Golden State Warriors")
check("Warriors vs Clippers (reversed)", score, 80)
check("is_forward=False", fwd, False, '==')

# One abbreviated, opponent confirms → geometric mean promotes to SUGGEST
score, _ = pair_score("Parivision", "Nigma Galaxy", "Pvision", "Nigma Galaxy")
check("Parivision+NigmaGalaxy (one abbrev)", score, 60)
print(f"    (info: geometric mean = √({_team_score('Parivision','Pvision')} × {_team_score('Nigma Galaxy','Nigma Galaxy')}))")

# One needs alias, opponent exact → geometric mean pulls down
score, _ = pair_score("NAVI", "Spirit", "Natus Vincere", "Team Spirit")
navi_t = _team_score("NAVI", "Natus Vincere")
spirit_t = _team_score("Spirit", "Team Spirit")
geo = math.isqrt(navi_t * spirit_t)
check(f"NAVI+Spirit (geo={geo}, SUGGEST without alias)", score, 80, '<')
print(f"    (info: NAVI={navi_t}, Spirit={spirit_t}, geo={geo})")

# Garbage pairs → geometric mean kills them
score, _ = pair_score("LOUD", "FURIA", "Cloud Rising", "Vitality")
check("LOUD/FURIA garbage", score, 45, '<')

score, _ = pair_score("Yakult Brothers", "Cloud Rising", "LOUD", "FURIA")
check("Yakult/Cloud garbage", score, 45, '<')

score, _ = pair_score("EYEBALLERS", "UNiTY", "ENCE", "CSDIILIT")
check("EYEBALLERS/UNiTY garbage", score, 45, '<')

score, _ = pair_score("FooBar Gaming", "BazQux Esports", "G2 Esports", "Fnatic")
check("FooBar/BazQux garbage", score, 45, '<')

# Verify geometric mean property: √(100×64)=80 is the AUTO_MATCH boundary
boundary = math.isqrt(100 * 64)
check(f"Math: √(100×64) = {boundary} = AUTO boundary", boundary, 80, '==')

print()
# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 3: league_score + game_type_compatible")
print("=" * 60)

check("LCS overlap", league_score("LCS 2026 Spring", "LoL - LCS"), 85)
check("NBA overlap", league_score("NBA", "Basketball - NBA"), 100)
check("PGL Major", league_score("PGL Major Copenhagen 2026", "CS2 - PGL Major"), 100)
check("Different leagues low", league_score("NBA", "Soccer - Premier League"), 50, '<')
check("csgo vs CS2 = True", game_type_compatible('csgo', 'CS2 - PGL Major'), True, '==')
check("csgo vs LoL = False", game_type_compatible('csgo', 'LoL - LCK'), False, '==')
check("lol vs CS2 = False", game_type_compatible('lol', 'CS2 - BLAST'), False, '==')

print()
# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 4: match_event — full pipeline")
print("=" * 60)

ps_events = [
    ("Los Angeles Clippers", "Golden State Warriors", 1001, "Basketball - NBA"),
    ("Oklahoma City Thunder", "Phoenix Suns", 1002, "Basketball - NBA"),
    ("Boston Celtics", "Miami Heat", 1003, "Basketball - NBA"),
    ("Real Madrid", "Barcelona", 2001, "Soccer - La Liga"),
    ("Santos FC", "SE Palmeiras", 2002, "Soccer - Brasileirao"),
    ("Santos Laguna", "Club Leon", 2003, "Soccer - Liga MX"),
    ("Team Spirit", "Team Falcons", 3001, "CS2 - PGL Major Copenhagen 2026"),
    ("Natus Vincere", "FaZe Clan", 3002, "CS2 - BLAST Premier"),
    ("G2 Esports", "Fnatic", 3003, "CS2 - ESL Pro League"),
    ("G2 Esports", "Fnatic", 4001, "LoL - LEC 2026 Spring"),
    ("T1", "Gen.G", 4002, "LoL - LCK 2026"),
    ("Natus Vincere (Kills)", "FaZe Clan (Kills)", 3012, "CS2 - BLAST Premier"),
]

ev, score, method, fwd = match_event("Clippers", "Warriors", "sports_basketball", "NBA", ps_events)
check("Clippers→eid 1001", ev['eid'] if ev else 0, 1001, '==')
check("Clippers AUTO_MATCH", method, 'AUTO_MATCH', '==')

ev, score, method, fwd = match_event("Thunder", "Suns", "sports_basketball", "NBA", ps_events)
check("Thunder→eid 1002", ev['eid'] if ev else 0, 1002, '==')

ev, score, method, fwd = match_event("Spirit", "Falcons", "csgo", "PGL Major Copenhagen 2026", ps_events)
check("Spirit CS2→eid 3001", ev['eid'] if ev else 0, 3001, '==')

ev, score, method, fwd = match_event("G2 Esports", "Fnatic", "csgo", "ESL Pro League", ps_events)
check("G2 CS2→eid 3003", ev['eid'] if ev else 0, 3003, '==')

ev, score, method, fwd = match_event("G2 Esports", "Fnatic", "lol", "LEC 2026 Spring", ps_events)
check("G2 LoL→eid 4001", ev['eid'] if ev else 0, 4001, '==')

ev, score, method, fwd = match_event("Real Madrid", "Barcelona", "sports_soccer", "La Liga", ps_events)
check("Madrid→eid 2001", ev['eid'] if ev else 0, 2001, '==')

ev, score, method, fwd = match_event("Natus Vincere", "FaZe Clan", "csgo", "BLAST Premier", ps_events)
check("NAVI→eid 3002 (not kills)", ev['eid'] if ev else 0, 3002, '==')

ev, score, method, fwd = match_event("FooBar Gaming", "BazQux Esports", "csgo", "Random League", ps_events)
check("Unknown = no_match", method, 'no_match', '==')

print()
print("=" * 60)
print(f"RESULTS: {_pass} passed, {_fail} failed")
print("=" * 60)
sys.exit(1 if _fail > 0 else 0)
