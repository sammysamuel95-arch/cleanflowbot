"""
v2perfectbot — matching/pair.py
Team PAIR matching: find both etopfun teams in the same PS3838 event.

Extracted from bot_working.py find_ps_match() lines 1101-1294.
Zero logic changes. Uses AliasDB instead of global dicts.
"""

import re
import time
import json
from thefuzz import fuzz
from typing import Optional, Tuple

from config import (
    SPORT_FILTER, PAIR_MIN_CONFIDENCE,
    MATCH_CACHE_FILE, MATCH_CACHE_TTL, MATCH_CACHE_MAX_MISSES,
)
from matching.aliases import AliasDB, clean_name
from core.logger import log_pair, log_info


# ── League keyword sets ────────────────────────────────────────────────────────

ESPORTS_LEAGUE_KW = {"cs2", "csgo", "cs:go", "valorant", "dota", "league of legends",
                      "lol", "mobile legends", "overwatch", "starcraft", "rainbow six",
                      "call of duty", "rocket league", "pubg", "counter-strike",
                      "esl", "blast", "cct", "pgl", "mpl", "esports", "gaming",
                      "lec", "lck", "lpl", "lcs", "vct", "cbl", "open qualifier",
                      # Additional leagues without obvious keywords — detected via image fallback
                      "ewc", "ferjee", "roman imperium", "digital crusade",
                      "fragadelphia", "nodwin", "kpl", "kog", "premier series"}

SOCCER_LEAGUE_KW  = {"bundesliga", "la liga", "serie a", "premier league", "ligue 1",
                      "eredivisie", "primera", "mls", "liga mx", "copa", "championship",
                      "super league", "primeira liga", "jupiler", "allsvenskan",
                      "eliteserien", "soccer", "football", "fa cup", "efl", "carabao",
                      "uefa", "champions league", "europa league", "world cup",
                      "concacaf", "conmebol", "afcon", "liga", "division", "amateur",
                      "ascenso"}

BASKETBALL_LEAGUE_KW = {"nba", "nbl", "kbl", "cba", "euroleague", "basketball", "acb",
                         "bbl", "lega basket", "jeep elite", "wnba", "ncaa"}

_GAME_KEYWORDS = {
    'dota': ['dota'],
    'cs': ['cs2', 'csgo', 'counter-strike', 'counter strike'],
    'lol': ['league of legends', 'lol'],
    'valorant': ['valorant'],
    'overwatch': ['overwatch'],
    'r6': ['rainbow six', 'r6'],
    'kog': ['kog', 'king of glory', 'kpl'],
}

# Map vs1.image path segment → sport/game hint
_IMAGE_GAME_MAP = {
    'lol': 'esports',
    'cs2': 'esports',
    'csgo': 'esports',
    'valorant': 'esports',
    'kog': 'esports',
    'dota': 'esports',
    'dota2': 'esports',
    'overwatch': 'esports',
    'foot': 'soccer',
}

# Category type from etop API → sport (definitive)
_CAT_SPORT_MAP = {
    'csgo': 'esports', 'cs2': 'esports', 'dota2': 'esports', 'dota': 'esports',
    'lol': 'esports', 'valorant': 'esports', 'overwatch': 'esports',
    'pubg': 'esports', 'kog': 'esports', 'starcraft': 'esports',
    'r6': 'esports', 'rocket_league': 'esports',
    'soccer': 'soccer', 'football': 'soccer',
    'basketball': 'basketball',
    'sports_basketball': 'basketball', 'sports_football': 'soccer', 'sports_soccer': 'soccer',
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def norm_league(raw: str) -> str:
    return re.sub(r'[^\w\s]', '', raw or '').lower().strip()

def etop_key(league_norm: str, team1: str, team2: str) -> str:
    return f"{league_norm}|{team1.lower().strip()}|{team2.lower().strip()}"

def _kw_match(kw: str, text: str) -> bool:
    """Word-boundary keyword match — prevents 'esl' matching 'bundesliga'."""
    return re.search(r'\b' + re.escape(kw) + r'\b', text) is not None

def infer_sport_hint(league: str = "", market: str = "", map_num: int = 0,
                     hdp_line: float = 0, image: str = "", cat_type: str = "") -> Optional[str]:
    """Determine sport from etop data.

    Priority:
    1. category.type from API (definitive — the API tells us the game)
    2. Image path game subfolder (backup)
    3. League name keywords (last resort)
    """
    # ── Priority 1: category.type (definitive) ────────────────────
    if cat_type:
        sport = _CAT_SPORT_MAP.get(cat_type.lower())
        if sport:
            return sport
        # Unknown category type but exists → likely esports
        return 'esports'

    # ── Priority 2: Image path game subfolder ─────────────────────
    if image:
        parts = image.rstrip('/').split('/')
        if len(parts) >= 5:
            game_seg = parts[3].lower()
            if game_seg in _IMAGE_GAME_MAP:
                return 'esports'
            return 'esports'
        else:
            # No game subfolder — check league keywords
            ln = league.lower() if league else ""
            for kw in ESPORTS_LEAGUE_KW:
                if _kw_match(kw, ln):
                    return 'esports'
            for kw in SOCCER_LEAGUE_KW:
                if _kw_match(kw, ln):
                    return 'soccer'
            for kw in BASKETBALL_LEAGUE_KW:
                if _kw_match(kw, ln):
                    return 'basketball'
            return None

    # ── Priority 3: League keywords only ──────────────────────────
    ln = league.lower() if league else ""
    if ln:
        for kw in ESPORTS_LEAGUE_KW:
            if _kw_match(kw, ln):
                return 'esports'
        for kw in SOCCER_LEAGUE_KW:
            if _kw_match(kw, ln):
                return 'soccer'
        for kw in BASKETBALL_LEAGUE_KW:
            if _kw_match(kw, ln):
                return 'basketball'

    # ── Structural heuristics ─────────────────────────────────────
    if map_num > 0:
        return 'esports'
    if market == "ou" and abs(hdp_line) >= 100:
        return 'basketball'
    if market == "hdp" and abs(hdp_line) >= 3:
        return 'basketball'
    return None


# ── Match cache ────────────────────────────────────────────────────────────────

def load_match_cache(path: str = None) -> dict:
    try:
        with open(path or MATCH_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_match_cache(cache: dict, path: str = None):
    try:
        with open(path or MATCH_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


# ── Main pair matcher ──────────────────────────────────────────────────────────

class PairMatcher:
    """Find both etopfun teams as a PAIR in PS3838 data.

    Extracted from bot_working.py find_ps_match() lines 1101-1294.
    Uses AliasDB for name resolution. Maintains match cache.
    """

    def __init__(self, alias_db: AliasDB, cache_file: str = None):
        self.aliases = alias_db
        self._cache = load_match_cache(cache_file)
        self._cache_file = cache_file or MATCH_CACHE_FILE

    def find(self, team1: str, team2: str, etop_league: str,
             sport_hint: Optional[str], ps_structured: list
    ) -> Tuple[Optional[str], Optional[str], Optional[bool], int, Optional[int], Optional[str], Optional[str]]:
        """Find PS3838 match for etopfun teams.

        Returns: (ps_home, ps_away, team1_is_home, confidence, ps_sp, ps_league, src)
        or       (None, None, None, 0, None, None, None)

        Exact logic from bot_working.py find_ps_match().
        """
        league_n = norm_league(etop_league)
        ek = etop_key(league_n, team1, team2)

        # ── Cache hit ──────────────────────────────────────────────────────────
        cached = self._cache.get(ek)
        if cached:
            conf = 100 if cached.get("match_count", 0) > 5 else 90
            return (cached["ps_home"], cached["ps_away"], cached["team1_is_home"],
                    conf, cached.get("ps_sp"), cached.get("ps_league"), "cache")

        # ── Sport filter ───────────────────────────────────────────────────────
        sp_map = {'basketball': {4}, 'esports': {12}, 'soccer': {29}}
        sp_filter = sp_map.get(sport_hint)

        if sp_filter:
            candidates = [e for e in ps_structured if e.get("sp") in sp_filter]
        else:
            candidates = [e for e in ps_structured if e.get("sp") in SPORT_FILTER]

        # Filter out (Kills) variants
        candidates = [e for e in candidates
                      if '(kills)' not in e.get('home', '').lower()
                      and '(kills)' not in e.get('away', '').lower()]

        # ── Esports game-type cross-filter ─────────────────────────────────────
        if sport_hint == 'esports' and league_n and candidates:
            etop_game = None
            ln_lower = league_n.lower()
            for gk, aliases in _GAME_KEYWORDS.items():
                if any(a in ln_lower for a in aliases):
                    etop_game = gk
                    break
            if etop_game:
                def _league_has_game(e):
                    el = e.get("league_norm", "").lower()
                    return any(a in el for a in _GAME_KEYWORDS[etop_game])
                game_filtered = [e for e in candidates if _league_has_game(e)]
                if game_filtered:
                    candidates = game_filtered

        # ── League filter (soft) ───────────────────────────────────────────────
        if league_n and candidates:
            league_filtered = [
                e for e in candidates
                if fuzz.token_sort_ratio(league_n, e.get("league_norm", "")) >= 60
            ]
            if league_filtered:
                candidates = league_filtered
                # Safety: if league filter produced candidates but no team close, fall back
                n1check = self.aliases.normalize(team1)
                has_any_near = False
                for entry in candidates[:100]:
                    s1 = max(fuzz.token_sort_ratio(n1check, entry["home_norm"]),
                             fuzz.token_sort_ratio(n1check, entry["away_norm"]))
                    if s1 >= 50:
                        has_any_near = True
                        break
                if not has_any_near:
                    candidates = [e for e in ps_structured
                                  if (not sp_filter or e.get("sp") in sp_filter)]

        # ── Team matching ──────────────────────────────────────────────────────
        best = None
        for entry in candidates:
            ps_home = entry["home"]
            ps_away = entry["away"]

            for t1_is_home, (t1_ps, t2_ps) in [(True, (ps_home, ps_away)),
                                                 (False, (ps_away, ps_home))]:
                n1e = self.aliases.normalize(team1)
                n2e = self.aliases.normalize(team2)
                n1p = self.aliases.normalize(t1_ps)
                n2p = self.aliases.normalize(t2_ps)
                k1 = team1.lower().strip()
                k2 = team2.lower().strip()

                alias1 = self.aliases.get(k1) if self.aliases.has_alias(k1) else None
                alias2 = self.aliases.get(k2) if self.aliases.has_alias(k2) else None
                a1_exact = alias1 and self.aliases.normalize(alias1) == n1p
                a2_exact = alias2 and self.aliases.normalize(alias2) == n2p
                a1_highuse = a1_exact and self.aliases.get_usage(k1) > 10
                a2_highuse = a2_exact and self.aliases.get_usage(k2) > 10

                exact1 = n1e == n1p or bool(n1e and n1p and (n1e in n1p or n1p in n1e))
                exact2 = n2e == n2p or bool(n2e and n2p and (n2e in n2p or n2p in n2e))

                s1 = 100 if (exact1 or a1_highuse) else fuzz.token_sort_ratio(n1e, n1p)
                s2 = 100 if (exact2 or a2_highuse) else fuzz.token_sort_ratio(n2e, n2p)

                m1 = exact1 or (a1_exact if a1_exact else False) or s1 >= 70
                m2 = exact2 or (a2_exact if a2_exact else False) or s2 >= 70
                if not (m1 and m2):
                    continue

                if (exact1 or a1_highuse) and (exact2 or a2_highuse):
                    conf, src = 100, "exact"
                elif a1_exact and exact2:
                    conf, src = 90, "alias"
                elif exact1 and a2_exact:
                    conf, src = 90, "alias"
                elif a1_exact and a2_exact:
                    conf, src = 85, "alias"
                elif (exact1 or a1_exact) and s2 >= 70:
                    conf, src = 80, "fuzzy"
                elif s1 >= 70 and (exact2 or a2_exact):
                    conf, src = 80, "fuzzy"
                elif s1 >= 80 and s2 >= 80:
                    conf, src = 80, "fuzzy"
                elif s1 >= 70 and s2 >= 70:
                    conf, src = 70, "fuzzy"
                else:
                    continue

                if best is None or conf > best[0]:
                    best = (conf, ps_home, ps_away, t1_is_home,
                            entry.get("sp"), entry.get("league_raw", ""), src)

        if best:
            conf, ps_home, ps_away, t1_is_home, ps_sp, ps_league_raw, src = best
            if conf >= 65 and src == "alias":
                self.aliases.save(team1, ps_home if t1_is_home else ps_away)
                self.aliases.save(team2, ps_away if t1_is_home else ps_home)
            self._cache[ek] = {
                "ps_home": ps_home, "ps_away": ps_away,
                "team1_is_home": t1_is_home, "confidence": conf,
                "match_count": 1, "ps_sp": ps_sp,
                "ps_league": ps_league_raw, "last_seen": time.time(),
                "consecutive_line_misses": 0,
            }
            save_match_cache(self._cache, self._cache_file)
            return ps_home, ps_away, t1_is_home, conf, ps_sp, ps_league_raw, src

        # ── Auto-abbreviation matching ─────────────────────────────────────────
        n1 = self.aliases.normalize(team1)
        n2 = self.aliases.normalize(team2)
        for entry in candidates:
            ps_h = entry["home"]
            ps_a = entry["away"]
            nh = self.aliases.normalize(ps_h)
            na = self.aliases.normalize(ps_a)
            for t1_is_home, (tp1, tp2, np1, np2) in [
                (True, (ps_h, ps_a, nh, na)),
                (False, (ps_a, ps_h, na, nh)),
            ]:
                abbr1 = (len(n1) <= 4 and len(np1) > 4 and
                          n1.upper() == ''.join(w[0] for w in np1.split() if w).upper()[:len(n1)])
                abbr2 = (len(n2) <= 4 and len(np2) > 4 and
                          n2.upper() == ''.join(w[0] for w in np2.split() if w).upper()[:len(n2)])
                sub1 = (len(n1) >= 3 and (n1 in np1 or np1 in n1))
                sub2 = (len(n2) >= 3 and (n2 in np2 or np2 in n2))
                init1 = (len(n1) <= 3 and len(np1) > 3 and
                          n1.lower() == ''.join(w[0] for w in np1.split() if w).lower()[:len(n1)])
                init2 = (len(n2) <= 3 and len(np2) > 3 and
                          n2.lower() == ''.join(w[0] for w in np2.split() if w).lower()[:len(n2)])
                m1 = abbr1 or sub1 or init1
                m2 = abbr2 or sub2 or init2
                if m1 and m2:
                    conf, src = 75, "auto-abbr"
                    self.aliases.save(team1, tp1)
                    self.aliases.save(team2, tp2)
                    self._cache[ek] = {
                        "ps_home": ps_h, "ps_away": ps_a,
                        "team1_is_home": t1_is_home, "confidence": conf,
                        "match_count": 1, "ps_sp": entry.get("sp"),
                        "ps_league": entry.get("league_raw", ""), "last_seen": time.time(),
                        "consecutive_line_misses": 0,
                    }
                    save_match_cache(self._cache, self._cache_file)
                    log_info(f"[AUTO-ALIAS] {team1}→{tp1}, {team2}→{tp2}")
                    return ps_h, ps_a, t1_is_home, conf, entry.get("sp"), entry.get("league_raw", ""), src

        return None, None, None, 0, None, None, None

    def update_cache_hit(self, ek: str):
        """Increment match_count on cache hit."""
        if ek in self._cache:
            self._cache[ek]["match_count"] = self._cache[ek].get("match_count", 0) + 1
            self._cache[ek]["consecutive_line_misses"] = 0
            self._cache[ek]["last_seen"] = time.time()
            save_match_cache(self._cache, self._cache_file)

    def record_line_miss(self, ek: str):
        """Record a line miss. Evict after MATCH_CACHE_MAX_MISSES."""
        if ek in self._cache:
            self._cache[ek]["consecutive_line_misses"] = \
                self._cache[ek].get("consecutive_line_misses", 0) + 1
            if self._cache[ek]["consecutive_line_misses"] >= MATCH_CACHE_MAX_MISSES:
                del self._cache[ek]
            save_match_cache(self._cache, self._cache_file)

    @property
    def cache(self) -> dict:
        return self._cache
