"""
v2perfectbot — matching/classify.py
Pure classification: etop raw sublist fields → market descriptor.

Zero PS knowledge. Zero matching. Zero killing/blocking/skipping.
Every input produces an output. PS matching gatekeeps (no PS line = no bet).

Tested against: RAW_SUB data from session 13 (20+ live entries).
Rules from: ETOPFUN_MTYPE_DATABASE.md (sessions 12+13, verified by real-money losses).
"""

# Etop type constants (from config.py)
ETOP_TYPE_SERIES = 2
ETOP_TYPE_MAP_GW = 3
ETOP_TYPE_STANDALONE_OU = 5
ETOP_TYPE_TOTAL_ROUNDS = 6    # "First to X Rounds" race market (PS no equivalent yet)
ETOP_TYPE_10K = 8
ETOP_TYPE_OU = 9
ETOP_TYPE_LOL_5KILLS = 11   # was wrongly named LOL_MAP_GW — type=11 = 5 Kills, NOT Game Winner
ETOP_TYPE_TOTAL_ROUNDS_ALT = 12
_RACE_DEFAULTS = {'cs2': 5}
ETOP_TYPE_TOTAL_KILLS_OU = 13



# category.type → game mapping (definitive, from etopfun API)
_CAT_GAME_MAP = {
    'dota2': 'dota', 'dota': 'dota',
    'lol': 'lol',
    'csgo': 'cs2', 'cs2': 'cs2',
    'valorant': 'valorant',
    'overwatch': 'overwatch',
    'pubg': 'pubg',
    'kog': 'kog',
    'starcraft': 'starcraft',
    'r6': 'r6',
    'rocket_league': 'rocket_league',
}


def _detect_game(image: str, league: str) -> str:
    """Detect game from vs1.image path or league name.
    
    PROVEN (Session 25):
      /upload/team/csgo/  = cs2
      /upload/team/dota2/ = dota
      /upload/team/LOL/   = lol
    Fallback: league name keywords.
    """
    if image:
        img = image.lower()
        if '/csgo/' in img or '/cs2/' in img:
            return 'cs2'
        if '/dota2/' in img or '/dota/' in img:
            return 'dota'
        if '/lol/' in img:
            return 'lol'
        if '/valorant/' in img:
            return 'valorant'
    if league:
        lw = league.lower()
        if any(x in lw for x in ('blast', 'esl', 'iem', 'pgl', 'faceit')):
            return 'cs2'
        if any(x in lw for x in ('dota', 'dota 2')):
            return 'dota'
        if any(x in lw for x in ('lck', 'lpl', 'lec', 'lcs', 'nacl', 'worlds', 'msi')):
            return 'lol'
        if any(x in lw for x in ('valorant', 'vct')):
            return 'valorant'
    return 'unknown'


def _classify_etop_sub(mtype, map_num, offer_score, offer_team,
                       total_score, total_time, mid, gw_id,
                       parent_bo, vs1, vs2, hint,
                       image='', league='', cat_type='', sport_hint=''):
    """Classify one etop sublist entry into a market descriptor.

    NEVER kills, blocks, or skips. Every input produces an output.
    PS matching gatekeeps — no PS line = no bet.

    Args:
        mtype:       sublist type (2, 3, 5, 6, 8, 9, 12, 13, ...)
        map_num:     map number (0=series, 1-5=map)
        offer_score: spread value (0=none, >0=spread)
        offer_team:  who is favorite (0=none, 1=team1, 2=team2)
        total_score: OU line or 10K indicator (None/0 or float)
        total_time:  timer type (40=ML/GW/OU, 38=HDP/10K)
        mid:         sublist match ID
        gw_id:       parent offerMatch.id (Game Winner entry)
        parent_bo:   best-of (1, 3, 5, 7)
        vs1:         etop team1 name
        vs2:         etop team2 name
        hint:        sport hint ('esports', 'basketball', 'soccer', or None)

    Returns: dict with keys:
        market:    str   — 'ml', 'hdp', 'ou', 'f10k', 'duration', 'unknown_X'
        line:      float — ABSOLUTE line value (always positive). None for ML/f10k.
        map:       int   — 0=series, 1-5=map number
        label:     str   — human readable for logging
        favorite:  str   — 'team1', 'team2', or None (who gives points)
        raw_type:  int   — original mtype for debugging
    """
    # Normalize inputs
    offer_score = offer_score or 0
    offer_team = offer_team or 0
    mid = str(mid)
    gw_id = str(gw_id)

    # ── type=2: Series-level (map=0) ──────────────────────────────────
    if mtype == ETOP_TYPE_SERIES and map_num == 0:

        if offer_score > 0:
            # Series HDP
            line = float(offer_score)
            fav = 'team1' if offer_team != 2 else 'team2'
            fav_name = vs1 if fav == 'team1' else vs2
            return {
                'market': 'hdp',
                'line': line,
                'map': 0,
                'label': f'{fav_name} -{line}',
                'favorite': fav,
                'raw_type': mtype,
            }

        if mid == gw_id:
            # Soccer "Game Winner" on etopfun = Asian HDP 0 on PS (2-way: win or push on draw)
            if sport_hint == 'soccer':
                return {
                    'market': 'hdp',
                    'line': 0.0,
                    'map': 0,
                    'label': f'{vs1} 0',
                    'favorite': 'team1',
                    'raw_type': mtype,
                }

            # Series Game Winner ML — esports and other 2-way sports
            return {
                'market': 'ml',
                'line': None,
                'map': 0,
                'label': 'Moneyline',
                'favorite': None,
                'raw_type': mtype,
            }

        if hint == 'esports':
            # Esports series HDP -0.5 (non-GW type=2 entry)
            return {
                'market': 'hdp',
                'line': 0.5,
                'map': 0,
                'label': f'{vs1} -0.5',
                'favorite': 'team1',
                'raw_type': mtype,
            }

        # type=2 map=0 but no offerScore, not GW, not esports — unknown
        return _unknown(mtype, map_num, total_score, offer_score, offer_team, cat_type, vs1, vs2, hint)

    # ── type=3: Map-level ─────────────────────────────────────────────
    if mtype == ETOP_TYPE_MAP_GW:

        # Detect game FIRST — Valorant type=3 is NOT Kills
        game = _CAT_GAME_MAP.get(cat_type, '') or _detect_game(image, league)
        if game == 'valorant':
            if total_score is not None and total_score > 0:
                if offer_team:
                    fav = 'team1' if offer_team != 2 else 'team2'
                    fav_name = vs1 if fav == 'team1' else vs2
                    return {
                        'market': 'hdp',
                        'line': float(total_score),
                        'map': map_num,
                        'label': f'{fav_name} -{total_score}, Map{map_num}',
                        'favorite': fav,
                        'raw_type': mtype,
                    }
                return {
                    'market': 'ou',
                    'line': float(total_score),
                    'map': map_num,
                    'label': f'O/U Rounds[{total_score}], Map{map_num}',
                    'favorite': None,
                    'raw_type': mtype,
                }
            return {
                'market': 'ml',
                'line': None,
                'map': map_num,
                'label': f'Game Winner, Map{map_num}',
                'favorite': None,
                'raw_type': mtype,
            }

        if total_score is not None and total_score > 0:
            # 10 Kills disguised as type=3 (totalScore>0 = kill count)
            # CONFIRMED: Yandex vs Spirit loss (session 10)
            return {
                'market': 'f10k',
                'line': None,
                'map': map_num,
                'label': f'Kills[{total_score}], Map{map_num}',
                'favorite': None,
                'raw_type': mtype,
            }

        # Game-specific type=3 interpretation (Session 25 root cause fix)
        # PROVEN: Dota type=3 = 10 Kills (PARIVISION mid=585001 = "10 Kills")
        # PROVEN: LoL  type=3 = 5 Kills  (FearX 0.70 = "5 Kills, Map2")
        # CS2 type=3 map>0 not seen in any data — treat as kills until proven
        game = _CAT_GAME_MAP.get(cat_type, '') or _detect_game(image, league)

        if game in ('dota', 'lol', 'unknown'):
            # Dota/LoL: type=3 map>0 = KILLS, never Game Winner
            return {
                'market': 'f10k',
                'line': None,
                'map': map_num,
                'label': f'10 Kills, Map{map_num}',
                'favorite': None,
                'raw_type': mtype,
            }

        if game == 'cs2':
            if parent_bo < 2:
                # BO1 type=3 = 10 Kills (session 12)
                return {
                    'market': 'f10k',
                    'line': None,
                    'map': map_num,
                    'label': f'10 Kills, Map{map_num}',
                    'favorite': None,
                    'raw_type': mtype,
                }
            # CS2 BO>=2, totalScore=None -> Map Game Winner (needs future verification)
            return {
                'market': 'ml',
                'line': None,
                'map': map_num,
                'label': f'Game Winner, Map{map_num}',
                'favorite': None,
                'raw_type': mtype,
            }

        # Fallback: unknown game, type=3 = kills (safe default)
        return {
            'market': 'f10k',
            'line': None,
            'map': map_num,
            'label': f'10 Kills, Map{map_num}',
            'favorite': None,
            'raw_type': mtype,
        }

    # ── type=5: Standalone OU (basketball/soccer) ─────────────────────
    if mtype == ETOP_TYPE_STANDALONE_OU and total_score:
        return {
            'market': 'ou',
            'line': float(total_score),
            'map': 0,
            'label': f'Over/Under[{total_score}]',
            'favorite': None,
            'raw_type': mtype,
        }

    # ── type=6 no totalScore: race market, number unknown. PS has no equivalent. ──
    if mtype == ETOP_TYPE_TOTAL_ROUNDS and not total_score and map_num > 0:
        _game = _CAT_GAME_MAP.get(cat_type, '')
        _n = _RACE_DEFAULTS.get(_game, '')
        return {
            'market': 'first_rounds',
            'line': None,
            'map': map_num,
            'label': f'First to win {_n} rounds, Map{map_num}' if _n else f'First rounds, Map{map_num}',
            'game': _game,
            'favorite': None,
        }

    # ── type=6 with totalScore: First X Rounds (race market — PS no equivalent) ──
    if mtype == ETOP_TYPE_TOTAL_ROUNDS and total_score:
        return {
            'market': 'race',
            'line': float(total_score),
            'map': map_num,
            'label': f'First to win {int(total_score)} rounds, Map{map_num}',
            'favorite': None,
            'raw_type': mtype,
        }

    # ── type=8: 10K / First-to-X ─────────────────────────────────────
    if mtype == ETOP_TYPE_10K:
        # Game-specific type=8 interpretation (Session 25 root cause fix)
        # PROVEN: Dota type=8 offerScore=0 = Game Winner (PARIVISION mid=585004)
        # PROVEN: CS2  type=8 offerScore>0 = Round HDP (Heroic/G2 offerScore=2.5)
        # LoL type=8 offerScore=0 = likely Game Winner (same pattern as Dota)
        game = _CAT_GAME_MAP.get(cat_type, '') or _detect_game(image, league)

        if game == 'cs2':
            if offer_score > 0:
                # CS2 Round Handicap — PS carries as HDP
                fav = 'team1' if offer_team != 2 else 'team2'
                fav_name = vs1 if fav == 'team1' else vs2
                return {
                    'market': 'hdp',
                    'line': float(offer_score),
                    'map': map_num,
                    'label': f'{fav_name} -{offer_score}, Map{map_num}',
                    'favorite': fav,
                    'raw_type': mtype,
                }
            else:
                # CS2 offerScore=0 = Map Winner (PROVEN S31: G2/Vitality/Spirit/Falcons screenshot)
                # Same as Dota/LoL — type=8 offerScore=0 = Game Winner across ALL games
                return {
                    'market': 'ml',
                    'line': None,
                    'map': map_num,
                    'label': f'Game Winner, Map{map_num}',
                    'favorite': None,
                    'raw_type': mtype,
                }

        if game == 'valorant':
            if offer_score == 0 or offer_score is None:
                # Valorant: type=8 offerScore=0 = GAME WINNER
                return {
                    'market': 'ml',
                    'line': None,
                    'map': map_num,
                    'label': f'Game Winner, Map{map_num}',
                    'favorite': None,
                    'raw_type': mtype,
                }
            else:
                # Valorant: type=8 offerScore>0 = Round HDP (NOT Kills — Valorant has NO kills markets)
                fav = 'team1' if offer_team != 2 else 'team2'
                fav_name = vs1 if fav == 'team1' else vs2
                return {
                    'market': 'hdp',
                    'line': float(offer_score),
                    'map': map_num,
                    'label': f'{fav_name} -{offer_score}, Map{map_num}',
                    'favorite': fav,
                    'raw_type': mtype,
                }

        if game in ('dota', 'lol'):
            if offer_score == 0 or offer_score is None:
                # Dota/LoL: type=8 offerScore=0 = GAME WINNER
                return {
                    'market': 'ml',
                    'line': None,
                    'map': map_num,
                    'label': f'Game Winner, Map{map_num}',
                    'favorite': None,
                    'raw_type': mtype,
                }
            else:
                # Dota/LoL: type=8 offerScore>0 = Kills HDP (NOT Round HDP)
                return {
                    'market': 'f10k',
                    'line': float(offer_score),
                    'map': map_num,
                    'label': f'Kills[{offer_score}], Map{map_num}',
                    'favorite': None,
                    'raw_type': mtype,
                }

        # Unknown game: safe default = f10k (don't fire on unknown)
        return {
            'market': 'f10k',
            'line': float(offer_score) if offer_score and offer_score > 0 else None,
            'map': map_num,
            'label': f'10 Kills, Map{map_num}',
            'favorite': None,
            'raw_type': mtype,
        }

    # ── type=9: OU market ─────────────────────────────────────────────
    if mtype == ETOP_TYPE_OU and total_score:
        if total_time and abs(total_score - total_time) <= 2:
            # Duration Time — PS has no equivalent
            return {
                'market': 'duration',
                'line': float(total_score),
                'map': map_num,
                'label': f'Duration[{total_score}], Map{map_num}',
                'favorite': None,
                'raw_type': mtype,
            }
        else:
            # Total Kills OU
            return {
                'market': 'ou',
                'line': float(total_score),
                'map': map_num,
                'label': f'Total Kills[{total_score}], Map{map_num}',
                'favorite': None,
                'raw_type': mtype,
            }

    # ── type=11: 5 Kills per map for LoL (NOT Game Winner) ───────────────
    # PROVEN (Session 26): type=11 and type=8 both appear per map for LoL.
    # type=11 = 5 Kills (kills odds ~0.63/1.44), type=8 offerScore=0 = real Game Winner.
    # Old name ETOP_TYPE_LOL_MAP_GW was WRONG — type=11 is always kills.
    if mtype == ETOP_TYPE_LOL_5KILLS and map_num > 0:
        game = _CAT_GAME_MAP.get(cat_type, '') or _detect_game(image, league)
        return {
            'market': 'f5k',
            'line': None,
            'map': map_num,
            'label': f'5 Kills, Map{map_num}',
            'favorite': None,
            'raw_type': mtype,
        }

    # ── type=13: Total Kills OU (confirmed: screenshot + PS betslip) ──
    if mtype == ETOP_TYPE_TOTAL_KILLS_OU and total_score:
        return {
            'market': 'ou',
            'line': float(total_score),
            'map': map_num,
            'label': f'Total Kills[{total_score}], Map{map_num}',
            'favorite': None,
            'raw_type': mtype,
        }

    # ── type=12: Total Rounds OU per map (alternate line) ─────────────
    # Confirmed: CS2 Map2, totalScore=21.5, PS store has ou/21.5/m2
    if mtype == ETOP_TYPE_TOTAL_ROUNDS_ALT and total_score:
        return {
            'market': 'ou',
            'line': float(total_score),
            'map': map_num,
            'label': f'O/U Rounds[{total_score}], Map{map_num}',
            'favorite': None,
            'raw_type': mtype,
        }

    # ── Anything else ─────────────────────────────────────────────────
    return _unknown(mtype, map_num, total_score, offer_score, offer_team, cat_type, vs1, vs2, hint)


def _unknown(mtype, map_num, total_score, offer_score, offer_team, cat_type, vs1, vs2, hint):
    """Honest label for unrecognized market types."""
    # Debug: capture unknown market types for future classification
    from core.logger import log_info as _dbg
    _dbg(f"[CLASSIFY_UNKNOWN] mtype={mtype} map={map_num} "
         f"offerScore={offer_score} totalScore={total_score} "
         f"offerTeam={offer_team} cat_type={cat_type} "
         f"vs1={vs1} vs2={vs2} hint={hint}")
    return {
        'market': f'unknown_{mtype}',
        'line': float(total_score) if total_score else None,
        'map': map_num,
        'label': f'Type{mtype}[{total_score}], Map{map_num}',
        'favorite': None,
        'raw_type': mtype,
    }


def classify_etop_sub(mtype, map_num, offer_score, offer_team,
                       total_score, total_time, mid, gw_id,
                       parent_bo, vs1, vs2, hint,
                       image='', league='', cat_type='', sport_hint=''):
    """Wrapper that injects 'game' into the result from _classify_etop_sub."""
    result = _classify_etop_sub(mtype, map_num, offer_score, offer_team,
                                total_score, total_time, mid, gw_id,
                                parent_bo, vs1, vs2, hint,
                                image=image, league=league,
                                cat_type=cat_type, sport_hint=sport_hint)
    if result is None:
        return result
    # Game detection priority: cat_type (definitive) → sport_hint → image/league fallback
    if cat_type and _CAT_GAME_MAP.get(cat_type):
        result['game'] = _CAT_GAME_MAP[cat_type]
    elif sport_hint == 'soccer':
        result['game'] = 'soccer'
    elif sport_hint == 'basketball':
        result['game'] = 'basketball'
    else:
        result['game'] = _CAT_GAME_MAP.get(cat_type, '') or _detect_game(image, league)
    return result
