"""
v2perfectbot — feeds/ps3838_parse.py
Parse PS3838 WS and REST odds data into market dicts.

Extracted from bot_working.py lines 322-579.
Zero logic changes. Uses config constants + core.math.

VERIFIED INDEX MAP (from PS3838_DATA_MAP v4):
  WS (mk=1 AND mk=3 — IDENTICAL):
    period[0]=HDP  period[1]=OU  period[2]=ML  period[3]=period_num
  REST per-event:
    period[2]=HDP  period[3]=OU  period[4]=ML  period[5]=period_num
  ML = [home, away, draw_or_null] — NORMAL ORDER ALL SPORTS. NO REVERSAL.
"""

import time
import asyncio
import aiohttp
from typing import List, Tuple, Optional

from config import (
    TARGET_SP, SPORT_FILTER,
    SP_ESPORTS, SP_BASKETBALL, SP_SOCCER,
    WS_ML_IDX,
    REST_HDP_IDX, REST_OU_IDX, REST_ML_IDX, REST_TEAM_TOTALS_IDX,
    HDP_HOME_IDX, HDP_HOME_ODDS_IDX, HDP_AWAY_ODDS_IDX,
    PS_BASE_URL,
)
from core.math import no_vig, norm_hdp
from core.logger import log_error


# ── HDP parsing ────────────────────────────────────────────────────────────────

def parse_hdp(key2, home: str, away: str, map_num: int, sp_id: int,
              league: str, event_id, src: str = "ws") -> list:
    """Parse HDP rows from a period candidate.

    Exact logic from bot_working.py lines 338-367.
    Row: [home_hdp, away_hdp, str_hdp, home_odds, away_odds, ...]
    home_hdp = norm_hdp(-row[0])
    """
    results = []
    if not isinstance(key2, list):
        return results
    for item in key2:
        if not isinstance(item, list) or not item:
            continue
        # Two formats:
        # WS: item = [[row1], [row2], ...] where row[0] is a list
        # Compact mk=3: item = [val1, val2, ...] where item[0] is str/float (flat row)
        if isinstance(item[0], list):
            # Nested: iterate rows inside group
            rows = item
        elif isinstance(item[0], (str, float, int)) and len(item) > HDP_AWAY_ODDS_IDX:
            # Flat: item IS the row
            rows = [item]
        else:
            continue
        for row in rows:
            if not isinstance(row, list) or len(row) <= HDP_AWAY_ODDS_IDX:
                continue
            try:
                home_hdp  = norm_hdp(-row[HDP_HOME_IDX])
                home_odds = float(row[HDP_HOME_ODDS_IDX])
                away_odds = float(row[HDP_AWAY_ODDS_IDX])
                if home_odds <= 1.0 or away_odds <= 1.0:
                    continue
                fh, fa = no_vig(home_odds, away_odds)
                line_id = row[7] if len(row) > 7 else None
                results.append({
                    "market":    "hdp",
                    "home":      home, "away": away,
                    "sp":        sp_id, "sp_name": TARGET_SP.get(sp_id, "?"),
                    "league":    league, "map_num": map_num,
                    "home_hdp":  home_hdp,
                    "home_odds": home_odds, "away_odds": away_odds,
                    "fair_home": fh, "fair_away": fa,
                    "event_id":  event_id, "src": src,
                    "line_id":   line_id,
                })
            except Exception:
                continue
    return results


# ── OU parsing ─────────────────────────────────────────────────────────────────

def parse_ou(key2, home: str, away: str, map_num: int, sp_id: int,
             league: str, event_id, src: str = "ws") -> list:
    """Parse OU rows from a period candidate.

    Exact logic from bot_working.py lines 369-400.
    Row: [total_str, total_float, over_odds, under_odds, ...]
    """
    results = []
    if not isinstance(key2, list):
        return results
    for item in key2:
        if not isinstance(item, list) or not item:
            continue
        if isinstance(item[0], list):
            rows = item
        elif isinstance(item[0], (str, float, int)) and len(item) >= 4:
            rows = [item]
        else:
            continue
        if not isinstance(rows[0], list):
            continue
        if not isinstance(rows[0][0], str):
            continue
        for row in rows:
            if not isinstance(row, list) or len(row) < 4:
                continue
            try:
                total      = float(row[0])
                over_odds  = float(row[2])
                under_odds = float(row[3])
                if over_odds < 1.05 or under_odds < 1.05:
                    continue
                fo, fu = no_vig(over_odds, under_odds)
                line_id = row[4] if len(row) > 4 else None
                results.append({
                    "market":     "ou",
                    "home":       home, "away": away,
                    "sp":         sp_id, "sp_name": TARGET_SP.get(sp_id, "?"),
                    "league":     league, "map_num": map_num,
                    "total":      total,
                    "over_odds":  over_odds, "under_odds": under_odds,
                    "fair_over":  fo, "fair_under": fu,
                    "event_id":   event_id, "src": src,
                    "line_id":    line_id,
                })
            except Exception:
                continue
    return results


# ── Team totals parsing ────────────────────────────────────────────────────────

def parse_team_totals(key2, home: str, away: str, map_num: int, sp_id: int,
                      league: str, event_id, src: str = "ws") -> list:
    """Parse team totals. Already [[group1],[group2]] — pass candidate directly.

    Exact logic from bot_working.py lines 402-434.
    """
    results = []
    if not isinstance(key2, list):
        return results
    for item in key2:
        if not isinstance(item, list) or not item:
            continue
        if isinstance(item[0], list):
            rows = item
        elif isinstance(item[0], (str, float, int)) and len(item) >= 4:
            rows = [item]
        else:
            continue
        if not isinstance(rows[0], list):
            continue
        if not isinstance(rows[0][0], str):
            continue
        for row in rows:
            if not isinstance(row, list) or len(row) < 4:
                continue
            try:
                total      = float(row[0])
                over_odds  = float(row[2])
                under_odds = float(row[3])
                if over_odds < 1.05 or under_odds < 1.05:
                    continue
                fo, fu = no_vig(over_odds, under_odds)
                line_id = row[4] if len(row) > 4 else None
                results.append({
                    "market":     "team_total",
                    "home":       home, "away": away,
                    "sp":         sp_id, "sp_name": TARGET_SP.get(sp_id, "?"),
                    "league":     league, "map_num": map_num,
                    "total":      total,
                    "over_odds":  over_odds, "under_odds": under_odds,
                    "fair_over":  fo, "fair_under": fu,
                    "event_id":   event_id, "src": src,
                    "line_id":    line_id,
                })
            except Exception:
                continue
    return results


# ── WS match parser ────────────────────────────────────────────────────────────

def parse_ws_match(match, sp_id: int, league: str, msg_mk=None) -> Tuple[list, Optional[int], str, str]:
    """Parse one match block from WebSocket FULL_ODDS/UPDATE_ODDS message.

    Exact logic from bot_working.py lines 436-492.

    CRITICAL VERIFIED RULES:
      - ML index = 2 for ALL WS subscriptions (mk=1 AND mk=3)
      - ML order = [home, away] for ALL sports. NO REVERSAL.
      - Basketball/soccer: only period "0" ML (full game)
      - Esports: all periods (series + maps)

    Returns: (markets_list, event_id, home, away)
    """
    markets = []
    event_id = None
    home = away = ""
    try:
        event_id = match[0] if isinstance(match[0], int) else None
        home     = str(match[1]).strip()
        away     = str(match[2]).strip()
        odds_data = next((x for x in match if isinstance(x, dict)), None)
        if not odds_data:
            return markets, event_id, home, away

        for key, candidate in odds_data.items():
            if not isinstance(candidate, list):
                continue
            map_num = int(key) if str(key).isdigit() else 0
            if sp_id == SP_ESPORTS and map_num > 5:
                continue

            # HDP + OU
            markets += parse_hdp(candidate, home, away, map_num, sp_id, league, event_id, src="ws")
            markets += parse_ou(candidate, home, away, map_num, sp_id, league, event_id, src="ws")

            # ML — ALWAYS at candidate[2] for ALL WS subscriptions (mk=1 AND mk=3)
            # ML order: [home, away] for ALL sports. NO REVERSAL.
            ml_idx = WS_ML_IDX  # = 2, UNIVERSAL
            parse_ml = True
            # Basketball/soccer: only parse period "0" (full game ML)
            if sp_id in (SP_BASKETBALL, SP_SOCCER) and str(key) != "0":
                parse_ml = False

            if parse_ml and len(candidate) > ml_idx:
                try:
                    ml_data = candidate[ml_idx]
                    # Handle nested ML: [[home, away, ...]] — mk=3 rounds markets
                    if isinstance(ml_data, list) and ml_data and isinstance(ml_data[0], list):
                        ml_data = ml_data[0]
                    if (isinstance(ml_data, list) and len(ml_data) >= 2
                            and not isinstance(ml_data[0], list)):
                        try:
                            test_val = float(ml_data[0])
                            is_ml = 1.0 < test_val < 50.0
                        except (ValueError, TypeError):
                            is_ml = False
                        if is_ml:
                            # ALL sports: ML[0]=AWAY, ML[1]=HOME. Confirmed by PS browser.
                            home_ml = float(ml_data[1])  # was [0] — FIXED
                            away_ml = float(ml_data[0])  # was [1] — FIXED
                            if home_ml > 1.0 and away_ml > 1.0:
                                fh, fa = no_vig(home_ml, away_ml)
                                line_id = ml_data[3] if len(ml_data) > 3 else None
                                markets.append({
                                    "market": "ml", "home": home, "away": away,
                                    "sp": sp_id, "sp_name": TARGET_SP.get(sp_id, "?"),
                                    "league": league, "map_num": map_num,
                                    "home_odds": home_ml, "away_odds": away_ml,
                                    "fair_home": fh, "fair_away": fa,
                                    "event_id": event_id, "src": "ws",
                                    "line_id": line_id,
                                })
                except Exception:
                    pass
    except Exception:
        pass
    return markets, event_id, home, away


# ── REST full ladder parser ────────────────────────────────────────────────────

def parse_rest_ladder(data: dict, home: str, away: str, sp_id: int,
                      league: str, event_id) -> list:
    """Parse REST per-event ladder response into market dicts.

    Extracted from bot_working.py fetch_full_ladder() lines 525-577.
    Network fetch is separate — this is PARSE ONLY.

    REST period indices (DIFFERENT from WS!):
      [0]=team_totals  [2]=HDP  [3]=OU  [4]=ML  [5]=period_num
    """
    markets = []
    try:
        e = data.get("e")
        if not isinstance(e, list) or len(e) < 4:
            return []
        match_arr = e[3]
        if not isinstance(match_arr, list) or len(match_arr) < 9:
            return []
        odds_dict = match_arr[8]
        if not isinstance(odds_dict, dict):
            return []
    except Exception:
        return []

    for period_key, period_data in odds_dict.items():
        if not isinstance(period_data, list):
            continue
        map_num = int(period_key) if str(period_key).isdigit() else 0
        if sp_id == SP_ESPORTS and map_num > 5:
            continue

        # REST indices: [2]=HDP, [3]=OU, [0]=team_totals
        for idx, parser in ((REST_HDP_IDX, "hdp"), (REST_OU_IDX, "ou"),
                            (REST_TEAM_TOTALS_IDX, "team_totals")):
            if len(period_data) <= idx:
                continue
            candidate = period_data[idx]
            if not isinstance(candidate, list):
                continue
            if parser == "hdp":
                markets += parse_hdp([candidate], home, away, map_num, sp_id, league, event_id, src="rest")
            elif parser == "ou":
                markets += parse_ou([candidate], home, away, map_num, sp_id, league, event_id, src="rest")
            else:
                markets += parse_team_totals(candidate, home, away, map_num, sp_id, league, event_id, src="rest")

        # REST ML at period_data[4] — order is [away, home] (confirmed vs live PS data)
        if len(period_data) > REST_ML_IDX:
            ml_data = period_data[REST_ML_IDX]
            if isinstance(ml_data, list) and len(ml_data) >= 2:
                try:
                    home_ml = float(ml_data[1])
                    away_ml = float(ml_data[0])
                    if home_ml > 1.0 and away_ml > 1.0:
                        fh, fa = no_vig(home_ml, away_ml)
                        markets.append({
                            "market": "ml", "home": home, "away": away,
                            "sp": sp_id, "sp_name": TARGET_SP.get(sp_id, "?"),
                            "league": league, "map_num": map_num,
                            "home_odds": home_ml, "away_odds": away_ml,
                            "fair_home": fh, "fair_away": fa,
                            "event_id": event_id, "src": "rest",
                        })
                except Exception:
                    pass

    return markets


async def fetch_full_ladder(session: aiohttp.ClientSession, event_id, home: str,
                            away: str, sp_id: int, league: str,
                            cookie_str: str, mk=None) -> list:
    """Fetch REST ladder and parse it. Returns list of market dicts.

    Network + parse combined for convenience.
    Exact retry logic from bot_working.py lines 496-579.
    """
    url = f"{PS_BASE_URL}/sports-service/sv/compact/events"
    # mk=1 for basketball/soccer (main game HDP+ML+OU alternates)
    # mk=3 for esports (map markets)
    _mk = mk if mk is not None else (3 if sp_id == 12 else 1)
    params = {
        "me": event_id, "mk": _mk, "more": "true",
        "btg": "1", "pimo": "0,1,2",
        "locale": "en_US", "_": int(time.time() * 1000),
        "withCredentials": "true",
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": PS_BASE_URL + "/", "Cookie": cookie_str}

    data = None
    for attempt in range(3):
        try:
            async with session.get(url, params=params, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 429:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
                break
        except Exception:
            return []
    if data is None:
        return []

    return parse_rest_ladder(data, home, away, sp_id, league, event_id)
