"""
feeds/ps3838_rest.py — PS3838 REST API client.

One function per endpoint per API_BIBLE.md.
Uses auth._session (curl_cffi Chrome120 TLS).
"""

import time
import json as _json
from typing import Optional, List
from core.logger import log_info, log_warn
from feeds.ps3838_parse import parse_ws_match
import config
from config import SP_ESPORTS


# Rate limiter: max 25 requests per 60 seconds
_request_times = []
MAX_REQUESTS_PER_MIN = 25


async def _rate_check():
    global _request_times
    now = time.time()
    _request_times = [t for t in _request_times if now - t < 60]
    if len(_request_times) >= MAX_REQUESTS_PER_MIN:
        wait = 60 - (now - _request_times[0])
        if wait > 0:
            log_warn("ps_rest", f"Rate limit — waiting {wait:.1f}s")
            import asyncio
            await asyncio.sleep(wait)
    _request_times.append(time.time())


def _hint_to_sp(hint):
    return {'basketball': 4, 'esports': 12, 'soccer': 29}.get(hint, 29)


def _parse_compact_response(data, sp_id, mk):
    """Parse PS compact REST response (nested lists).

    Returns: list of (mkts, eid, home, away, league) tuples
    """
    results = []
    for section in ('l', 'n'):
        for block in (data.get(section) or []):
            if not isinstance(block, list) or len(block) < 3:
                continue
            blk_sp = block[0] if isinstance(block[0], int) else None
            if blk_sp != sp_id:
                continue
            for lb in (block[2] or []):
                if not isinstance(lb, list) or len(lb) < 3:
                    continue
                league = ''
                for item in lb[:3]:
                    if isinstance(item, str):
                        league = item
                        break
                for match in (lb[2] or []):
                    try:
                        mkts, eid, home, away = parse_ws_match(match, sp_id, league, msg_mk=mk)
                        if eid and home and away:
                            results.append((mkts, eid, home, away, league))
                    except Exception:
                        pass
    return results


# ── PS-REST-1: compact_events ─────────────────────────────────────────────────

async def compact_events(auth, sp: int, mk: int) -> list:
    """GET /sports-service/sv/compact/events — all events for a sport/market.

    Returns list of (mkts, eid, home, away, league) tuples.
    """
    await _rate_check()

    url = f"{config.PS_BASE_URL}/sports-service/sv/compact/events"
    params = {
        "sp": sp, "mk": mk,
        "btg": "1",
        "cl": "100",
        "g": "QQ==",
        "l": "100",
        "more": "false",
        "o": "0",
        "ot": "1",
        "pa": "0",
        "pimo": "0,1,8,39,2,3,6,7,4,5",
        "pn": "-1",
        "pv": "1",
        "tm": "0",
        "v": "0",
        "locale": "en_US",
        "_": str(int(time.time() * 1000)),
        "withCredentials": "true",
    }
    hdrs = auth.build_headers(method="GET")

    try:
        from urllib.parse import urlencode
        full_url = f"{url}?{urlencode(params)}"
        async with auth._session.get(full_url, headers=hdrs, timeout=15) as resp:
            if resp.status != 200:
                log_warn("ps_rest", f"compact_events failed: {resp.status}")
                return []
            data = await resp.json()

        if not isinstance(data, dict):
            log_warn("ps_rest", f"compact_events unexpected response: {str(data)[:200]}")
            return []

        return _parse_compact_response(data, sp, mk)

    except Exception as e:
        log_warn("ps_rest", f"compact_events error: {e}")
        return []


# ── Convenience helpers (NOT separate endpoints) ──────────────────────────────

async def search_by_teams(auth, team1: str, team2: str,
                          hint: str = None) -> Optional[dict]:
    """Search for a match by team names. Uses compact_events.

    Returns {eid, home, away, sp, league, score, lines} or None.
    """
    from thefuzz import fuzz
    import asyncio as _asyncio

    await _asyncio.sleep(2)

    sp = _hint_to_sp(hint)
    mks = [1, 3] if sp == SP_ESPORTS else [1]

    parsed = []
    for mk in mks:
        parsed.extend(await compact_events(auth, sp, mk))

    # Merge lines for same eid across mk fetches
    eid_lines: dict = {}
    eid_meta: dict = {}
    for mkts, eid, ps_home, ps_away, league in parsed:
        if eid not in eid_lines:
            eid_lines[eid] = list(mkts)
            eid_meta[eid] = (ps_home, ps_away, league)
        else:
            eid_lines[eid].extend(mkts)
    merged = [(eid_lines[eid], eid, *eid_meta[eid]) for eid in eid_lines]

    best_match = None
    best_score = 0

    for mkts, eid, ps_home, ps_away, league in merged:
        score1 = min(fuzz.ratio(team1.lower(), ps_home.lower()),
                     fuzz.ratio(team2.lower(), ps_away.lower()))
        score2 = min(fuzz.ratio(team1.lower(), ps_away.lower()),
                     fuzz.ratio(team2.lower(), ps_home.lower()))
        score = max(score1, score2)

        if score > best_score and score >= 70:
            best_score = score
            best_match = {
                'eid': eid, 'home': ps_home, 'away': ps_away,
                'sp': sp, 'league': league, 'score': score, 'lines': mkts,
            }

    if best_match:
        log_info(f"[PS_SEARCH] Found: {team1} vs {team2} → "
                 f"{best_match['home']} vs {best_match['away']} "
                 f"(score={best_match['score']}, {len(best_match['lines'])} lines)")
    else:
        log_info(f"[PS_SEARCH] Not found: {team1} vs {team2} (sp={sp})")

    return best_match


async def search_event(auth, team1: str, team2: str, hint: str = None) -> Optional[dict]:
    """Search PS3838 by team name. Finds EARLY events that compact_events misses.

    Calls /sports-service/sv/search/v2 for each team, then matches by shared eventId.
    Returns {eid, home, away, sp} or None.
    """
    await _rate_check()

    sp_filter = _hint_to_sp(hint) if hint else None
    url = f"{config.PS_BASE_URL}/sports-service/sv/search/v2"
    hdrs = auth.build_headers(method="GET")
    from urllib.parse import urlencode
    import asyncio as _aio

    team1_results = {}  # eid → ps_name
    team2_results = {}

    for search_term, result_dict in [(team1, team1_results), (team2, team2_results)]:
        params = {"s": 1, "t": search_term, "locale": "en_US",
                  "_": str(int(time.time() * 1000)), "withCredentials": "true"}
        try:
            full_url = f"{url}?{urlencode(params)}"
            async with auth._session.get(full_url, headers=hdrs, timeout=10) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
            for t in data.get("teams", []):
                if sp_filter and t.get("sportId", 0) != sp_filter:
                    continue
                ps_name = t.get("name", "")
                for ev in t.get("events", []):
                    eid = ev.get("id")
                    if eid:
                        result_dict[eid] = ps_name
        except Exception:
            continue
        await _aio.sleep(1)

    shared_eids = set(team1_results.keys()) & set(team2_results.keys())
    if shared_eids:
        eid = shared_eids.pop()
        home = team1_results[eid]
        away = team2_results[eid]
        log_info(f"[PS_SEARCH_V2] {team1} vs {team2} → {home} vs {away} eid={eid} (confirmed)")
        return {'eid': eid, 'home': home, 'away': away, 'sp': sp_filter or 0}

    if team1_results or team2_results:
        log_info(f"[PS_SEARCH_V2] {team1} vs {team2} → no shared eid "
                 f"(t1={len(team1_results)} t2={len(team2_results)} results)")
    return None


async def fetch_lines_for_eid(auth, eid: int, sp: int) -> List[dict]:
    """Get all lines for a known event ID. Uses compact_events.

    Returns list of market dicts from parse_ws_match.
    """
    all_mkts = []
    mks_to_fetch = [1, 3] if sp == SP_ESPORTS else [1]

    for mk in mks_to_fetch:
        parsed = await compact_events(auth, sp, mk)
        for mkts, parsed_eid, _, _, _ in parsed:
            if parsed_eid == eid:
                all_mkts.extend(mkts)

    if all_mkts:
        log_info(f"[PS_FETCH] eid={eid}: {len(all_mkts)} lines")

    return all_mkts


# ── PS-REST-2: all_odds_selections ────────────────────────────────────────────

async def all_odds_selections(auth, selections: list) -> Optional[dict]:
    """POST /member-betslip/v2/all-odds-selections — verify odds exist.

    Ghost odds protection. Returns API response or None on failure.
    selections = [{eid, period, mk, side, line, line_id}, ...]
      eid      — PS event ID (int)
      period   — period/map number (int, 0=full game)
      mk       — 1=ML, 2=HDP/OU (NOT the WS subscription mk!)
      side     — 0=home/over, 1=away/under
      line     — 0 for ML, handicap for HDP, total for OU
      line_id  — from WS (stored in TheOnlyStore._line_ids)

    Response statuses per selection:
      OK           → odds real, safe to fire. Returns .odds, .maxStake
      ODDS_CHANGE  → line moved, returns NEW real odds. Recompute EV.
      UNAVAILABLE  → ghost odds, DO NOT FIRE
    """
    url = f"{config.PS_BASE_URL}/member-betslip/v2/all-odds-selections"

    odds_selections = []
    for s in selections:
        line_val = s.get('line', 0)
        # Format line: 0 for ML, actual value for HDP/OU
        line_str = str(line_val) if line_val else "0"
        odds_id = f"{s['eid']}|{s['period']}|{s['mk']}|{s['side']}|0|{line_str}"
        sel_id = f"{s['line_id']}|{odds_id}|{s['side']}"
        odds_selections.append({
            "oddsFormat": 1,
            "oddsId": odds_id,
            "oddsSelectionsType": "NORMAL",
            "selectionId": sel_id,
        })

    body = {"oddsSelections": odds_selections}
    headers = auth.build_headers(method="POST")
    headers["Content-Type"] = "application/json"

    # v-hucode REQUIRED — without it → 400
    v_hucode = getattr(auth, 'v_hucode', None)
    if v_hucode:
        headers["v-hucode"] = v_hucode
    else:
        log_warn("ps_rest", "all_odds_selections: v-hucode MISSING — expect 400")

    ts = int(time.time() * 1000)
    full_url = f"{url}?locale=en_US&_={ts}&withCredentials=true"

    try:
        async with auth._session.post(full_url,
                                      data=_json.dumps(body),
                                      headers=headers,
                                      timeout=10) as resp:
            if resp.status == 400:
                log_warn("ps_rest", "all_odds_selections: 400 — likely v-hucode invalid")
                return None
            if resp.status == 429:
                log_warn("ps_rest", "all_odds_selections: 429 rate limited")
                return None
            if resp.status != 200:
                log_warn("ps_rest", f"all_odds_selections: status {resp.status}")
                return None
            return await resp.json()
    except Exception as e:
        log_warn("ps_rest", f"all_odds_selections failed: {e}")
        return None


# ── PS-REST-3: system_status ──────────────────────────────────────────────────

async def system_status(auth) -> None:
    """GET /member-service/v2/system/status — browser polls this frequently."""
    ts = int(time.time() * 1000)
    url = f"{config.PS_BASE_URL}/member-service/v2/system/status?locale=en_US&_={ts}&withCredentials=true"
    try:
        hdrs = auth.build_headers(method="GET")
        await auth._session.get(url, headers=hdrs, timeout=10)
    except Exception:
        pass
