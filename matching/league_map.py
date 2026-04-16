"""
matching/league_map.py — League keyword matching for structured matcher.

Extracts distinctive keywords from league names and scores similarity.
Used by structured_matcher.py to filter PS events by league before pair scoring.

Design: docs/STRUCTURED_MATCHER.md
"""

from thefuzz import fuzz


# ── Keywords that identify specific leagues/tournaments ──────────────────
# If ANY of these overlap between etop and PS league names → confident match
KEY_WORDS = frozenset({
    # Basketball
    'nba', 'wnba', 'ncaa', 'nbl', 'euroleague', 'acb', 'cba', 'kbl', 'bbl',
    # LoL leagues
    'lcs', 'lec', 'lck', 'lpl', 'ljl', 'pcs', 'vcs', 'cblol', 'tcl', 'lla', 'lco', 'nacl',
    'msi', 'worlds',
    # CS2 tournaments
    'pgl', 'blast', 'esl', 'iem', 'cct', 'roobet', 'faceit',
    # Dota tournaments
    'dpc', 'fissure',
    # Valorant
    'vct',
    # Soccer leagues
    'copa', 'premier', 'bundesliga', 'serie', 'ligue',
    'eredivisie', 'allsvenskan', 'eliteserien', 'superliga',
    'concacaf', 'conmebol', 'afcon',
    'champions', 'europa',
    'laliga',
    # Regional esports
    'pacific', 'americas', 'emea',
    # Other
    'challengers', 'ascension',
})

# ── Words with zero signal (appear in almost every league name) ──────────
LEAGUE_FILLER = frozenset({
    'cs2', 'csgo', 'dota', 'dota2', 'lol', 'valorant',
    'league', 'of', 'legends', 'counter', 'strike',
    'the', 'a', 'in', 'and', 'for', 'to',
    '2024', '2025', '2026', '2027',
    'season', 'stage', 'split', 'phase', 'week',
    'esports', 'esport', 'pro', 'tour',
    'spring', 'summer', 'fall', 'winter',
    'group', 'playoff', 'playoffs', 'finals', 'grand', 'final',
    'regular', 'round', 'day', 'match',
    'international', 'global', 'regional', 'open', 'closed', 'qualifier',
})


# ── PS league string → game type (within sp=12 esports) ─────────────────
# PS groups ALL esports under sp=12. League name is the ONLY way to tell
# CS2 from LoL from Dota from Valorant.
# Copied from evidence.py _PS_LEAGUE_GAME_MAP (proven, zero changes).
PS_LEAGUE_GAME_MAP = {
    # LoL
    'lck': 'lol', 'lpl': 'lol', 'lec': 'lol', 'lcs': 'lol',
    'ljl': 'lol', 'pcs': 'lol', 'vcs': 'lol', 'cblol': 'lol',
    'tcl': 'lol', 'lla': 'lol', 'lco': 'lol', 'nacl': 'lol',
    'league of legends': 'lol', 'lol': 'lol', 'worlds': 'lol', 'msi': 'lol',
    # Dota
    'dota': 'dota2', 'dota 2': 'dota2', 'the international': 'dota2', 'dpc': 'dota2',
    'esl one': 'dota2', 'bts': 'dota2', 'fissure': 'dota2',
    # CS2
    'cs2': 'csgo', 'csgo': 'csgo', 'pgl': 'csgo', 'esl pro': 'csgo',
    'blast': 'csgo', 'iem': 'csgo', 'cct': 'csgo', 'esl challenger': 'csgo',
    'roobet': 'csgo',
    # Valorant
    'valorant': 'valorant', 'vct': 'valorant', 'champions tour': 'valorant',
    # Sports (sp already handles, but included for completeness)
    'nba': 'basketball', 'nbl': 'basketball', 'euroleague': 'basketball',
    'premier league': 'soccer', 'la liga': 'soccer', 'serie a': 'soccer',
    'bundesliga': 'soccer', 'ligue 1': 'soccer', 'mls': 'soccer',
    'concacaf': 'soccer', 'copa': 'soccer', 'uefa': 'soccer',
}

# ── Etop cat_type → normalized game ──────────────────────────────────────
# Copied from evidence.py _ETOP_GAME_MAP (proven).
ETOP_GAME_MAP = {
    'dota2': 'dota2', 'dota': 'dota2',
    'csgo': 'cs2', 'cs2': 'cs2',
    'lol': 'lol',
    'valorant': 'valorant',
    'sports_basketball': 'basketball', 'basketball': 'basketball',
    'sports_football': 'soccer', 'sports_soccer': 'soccer', 'soccer': 'soccer',
}


def tokenize(text: str) -> set:
    """Extract distinctive tokens from a league name.

    Strips filler words, punctuation, short tokens.
    Returns lowercase token set.
    """
    cleaned = text.lower().replace('-', ' ').replace(':', ' ').replace('.', ' ').replace(',', ' ')
    words = cleaned.split()
    tokens = {w for w in words if w not in LEAGUE_FILLER and len(w) >= 2}
    return tokens


# ── PS league prefix → game_type (deterministic parsing) ─────────────────
# PS league format:
#   Esports: "<Game> - <Tournament>"     e.g. "CS2 - BLAST Premier"
#   Sports:  "<Country> - <League>"      e.g. "Italy - Serie A"
#   Special: "<League>"                  e.g. "NBA" (no separator)
#
# Esports prefixes tell us the GAME TYPE (cs2, lol, dota2, valorant).
# Country prefixes tell us nothing about sport (Germany could be soccer OR basketball).
# We only map known game prefixes — everything else returns '' (let sp filter handle it).

PS_PREFIX_MAP = {
    # Esports game names (these ARE the game type)
    'cs2': 'cs2', 'csgo': 'cs2',
    'league of legends': 'lol', 'lol': 'lol',
    'dota 2': 'dota2', 'dota2': 'dota2',
    'valorant': 'valorant',
    'overwatch': 'overwatch',
    'starcraft': 'starcraft', 'starcraft 2': 'starcraft',
    'call of duty': 'cod',
    'rainbow six': 'r6',
    'rocket league': 'rocket_league',
    'king of glory': 'kog',
    'mobile legends': 'mobile_legends',
    # Sport-level prefixes (only when PS uses sport name, not country)
    'basketball': 'basketball',
    'soccer': 'soccer',
    'tennis': 'tennis',
    'baseball': 'baseball',
    'hockey': 'hockey',
    'e sports': 'esports',
}


def parse_ps_league(ps_league: str) -> tuple:
    """Parse structured PS league string.

    'CS2 - BLAST Premier'           → ('cs2', 'BLAST Premier')
    'League of Legends - LCK CL'   → ('lol', 'LCK CL')
    'Italy - Serie A'               → ('', 'Serie A')      ← country = unknown sport
    'Germany - Bundesliga'          → ('', 'Bundesliga')    ← could be soccer OR basketball!
    'NBA'                           → ('', 'NBA')           ← no separator
    ''                              → ('', '')

    Returns: (game_type, clean_league_name)
    game_type = '' means prefix was a country or absent (sp filter handles sport)
    """
    if not ps_league:
        return '', ''

    if ' - ' not in ps_league:
        return '', ps_league

    prefix, league = ps_league.split(' - ', 1)
    prefix_clean = prefix.strip()
    league_clean = league.strip()

    # Check known game/sport prefixes
    game = PS_PREFIX_MAP.get(prefix_clean.lower())
    if game:
        return game, league_clean

    # Unknown prefix = country name (Italy, Germany, Spain, Norway, ...)
    # DON'T assume sport — "Germany - Bundesliga" is basketball OR soccer.
    # The sp filter already separates sports before we get here.
    return '', league_clean


def detect_ps_game(ps_league: str) -> str:
    """Detect game type from PS league string. Returns normalized game or ''.

    Primary: parse prefix (deterministic for esports).
    Fallback: keyword search ONLY when no separator found (e.g. "NBA").
    Country prefixes ("Italy - Serie A") return '' — sp filter handles sport.
    """
    game, _ = parse_ps_league(ps_league)
    if game:
        return game

    # Only keyword fallback if there was NO separator at all
    # Country prefix ("Italy - Serie A") already parsed → don't guess sport
    if ' - ' not in (ps_league or ''):
        return _detect_ps_game_keywords(ps_league)

    return ''


def _detect_ps_game_keywords(ps_league: str) -> str:
    """Fallback: keyword search for unusual PS league formats."""
    lg = ps_league.lower()
    for keyword, game in PS_LEAGUE_GAME_MAP.items():
        if keyword in lg:
            return game
    return ''


def game_type_compatible(etop_cat_type: str, ps_league: str) -> bool:
    """Returns True if game types are compatible. False = instant reject.

    If either side is unknown, passes (don't block on missing data).
    This is the structured replacement for evidence.py _game_type_gate.
    """
    etop_game = ETOP_GAME_MAP.get(etop_cat_type, '') if etop_cat_type else ''
    ps_game = detect_ps_game(ps_league)

    if not etop_game or not ps_game:
        return True  # unknown = don't block

    return etop_game == ps_game


def league_score(etop_league: str, ps_league: str) -> int:
    """Score league similarity. Parses PS prefix for cleaner comparison.

    PS: "CS2 - BLAST Premier" → strips prefix → compare "BLAST Premier" vs etop league
    PS: "Italy - Serie A" → strips country → compare "Serie A" vs etop league
    This removes noise so "LCS 2026 Spring" vs "LCS" works cleanly.

    Returns 0-100:
        100 = confident match (shared KEY_WORDS)
         85 = shared distinctive tokens
        0-84 = fuzzy partial_ratio fallback
    """
    if not etop_league or not ps_league:
        return 50  # unknown = neutral

    # Parse PS league to strip game/country prefix
    _, ps_clean = parse_ps_league(ps_league)

    etop_tokens = tokenize(etop_league)
    ps_tokens = tokenize(ps_clean)  # compare against cleaned name

    # Check for KEY_WORDS overlap — high confidence
    key_overlap = etop_tokens & ps_tokens & KEY_WORDS
    if key_overlap:
        return 100

    # Check for ANY distinctive token overlap
    any_overlap = etop_tokens & ps_tokens
    if any_overlap:
        return 85

    # Fallback: fuzzy partial_ratio on cleaned strings
    return fuzz.partial_ratio(etop_league.lower(), ps_clean.lower())
