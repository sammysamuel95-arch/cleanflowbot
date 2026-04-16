"""
v2perfectbot — matching/index.py
Build PS3838 lookup index and structured list for pair matching.

Extracted from bot_working.py build_ps_index() lines 815-870
and build_ps_structured() lines 872-887.
"""

import re
from matching.aliases import AliasDB


def _norm_league(raw):
    return re.sub(r'[^\w\s]', '', raw or '').lower().strip()


def build_ps_index(markets: list) -> dict:
    """Build lookup index from all market dicts in the odds store.

    Keys:
      HDP:        (home, away, "hdp", map_num, hdp) + team-level keys
      OU:         (home, away, "ou", map_num, total) + team-level keys
      team_total: team-level keys only
      ML:         (home, away, "ml", map_num, 0.0) + team-level keys

    REST overrides WS for same key.
    Exact logic from bot_working.py lines 815-870.
    """
    idx = {}
    for m in markets:
        hl      = m["home"].lower()
        al      = m["away"].lower()
        map_num = m.get("map_num", 0)
        src     = m.get("src", "ws")
        mkt     = m["market"]

        if mkt == "hdp":
            hdp  = m["home_hdp"]
            keys = [(hl, al, "hdp", map_num, hdp),
                    ("_t_", hl, "hdp", map_num, hdp),
                    ("_t_", al, "hdp", map_num, hdp)]
        elif mkt == "ou":
            total = m["total"]
            keys  = [(hl, al, "ou", map_num, total),
                     ("_t_", hl, "ou", map_num, total),
                     ("_t_", al, "ou", map_num, total)]
        elif mkt == "team_total":
            total = m["total"]
            keys  = [(hl, al, "team_total", map_num, total),
                     ("_t_", hl, "team_total", map_num, total),
                     ("_t_", al, "team_total", map_num, total)]
        elif mkt == "ml":
            keys  = [(hl, al, "ml", map_num, 0.0),
                     ("_t_", hl, "ml", map_num, 0.0),
                     ("_t_", al, "ml", map_num, 0.0)]
        else:
            continue

        for k in keys:
            existing = idx.get(k)
            if existing is None or (src == "rest" and existing.get("src") != "rest"):
                idx[k] = m

        # (Kills) OU: also index under clean team names
        if mkt == "ou" and ("(kills)" in hl or "(kills)" in al):
            clean_home = hl.replace(" (kills)", "").replace("(kills)", "").strip()
            clean_away = al.replace(" (kills)", "").replace("(kills)", "").strip()
            extra_keys = [(clean_home, clean_away, "ou", map_num, total),
                          ("_t_", clean_home, "ou", map_num, total),
                          ("_t_", clean_away, "ou", map_num, total)]
            for k in extra_keys:
                existing = idx.get(k)
                if existing is None or (src == "rest" and existing.get("src") != "rest"):
                    idx[k] = m

    return idx


def build_ps_structured(markets: list, alias_db: AliasDB) -> list:
    """One entry per unique (sp, league_norm, home, away) for use by PairMatcher.

    Exact logic from bot_working.py lines 872-887.
    """
    seen = {}
    for m in markets:
        key = (m.get("sp"), _norm_league(m.get("league", "")), m["home"], m["away"])
        if key not in seen:
            seen[key] = {
                "sp":          m.get("sp"),
                "league_raw":  m.get("league", ""),
                "league_norm": _norm_league(m.get("league", "")),
                "home":        m["home"],
                "away":        m["away"],
                "home_norm":   alias_db.normalize(m["home"]),
                "away_norm":   alias_db.normalize(m["away"]),
            }
    return list(seen.values())
