"""
matching/evidence.py — Multi-Signal Evidence Matching

Stack weak signals into strong confidence. No single signal decides.

Signals:
  1. NAME     — token-aware fuzzy score (max of ratio + token_set_ratio)
  2. OPPONENT — does the other team also match?
  3. SPORT    — same sport on both platforms?
  4. TOKEN    — do the names share a distinctive word?
  5. HISTORY  — have we matched/fired this pair before?

Combined score → action:
  ≥85  → AUTO_MATCH  (no human needed, alias auto-learned)
  65-84 → SUGGEST     (logged for dashboard approval)
  <65  → IGNORE      (too risky)

Weights tuned by ChatGPT review + 26-case stress test:
  0 auto-match false positives, 15 correct auto-matches.

Session 21: ps_eid field added — eid travels from EventStore through matching
to _build_etop_market. No re-lookups.
"""

from thefuzz import fuzz
from dataclasses import dataclass
from typing import Optional, Tuple, List
from core.logger import log_info, log_warn


# ── Thresholds ────────────────────────────────────────────────────────────

SCORE_AUTO   = 65    # With game_type gate, 65 is safe within same game
SCORE_SUGGEST = 45   # Let more through for human review
MIN_NAME     = 20    # General baseline (opponent override lowers to 10)

# Signal weights (sum = 1.0)
# ChatGPT review: opponent must be DOMINANT signal — it's the #1 false-positive killer
W_NAME    = 0.25
W_OPPONENT = 0.40
W_SPORT   = 0.15
W_TOKEN   = 0.10
W_HISTORY = 0.10


# ── Filler words for token extraction ─────────────────────────────────────

FILLER = {
    'team', 'gaming', 'esports', 'esport', 'fc', 'sc', 'cf', 'ac',
    'united', 'city', 'jets', 'rovers', 'wanderers', 'warriors', 'republic', 'democratic', 'rep', 'of', 'the',
    'de', 'del', 'la', 'le', 'al', 'el', 'cd', 'real',
    'club', 'sport', 'athletic', 'atletico', 'sporting',
    'ua', 'eu', 'na', 'kr', 'cn', 'br',
    # Academy/junior indicators
    'u21', 'u23', 'u19', 'u18', 'u17',
    'academy', 'reserves', 'junior', 'youth', 'ii', 'b',
    'women', 'womens', 'ladies', 'w',
}


# ── Data structures ───────────────────────────────────────────────────────

@dataclass
class MatchSignals:
    """Evidence signals for one potential team match."""
    etop_name: str
    ps_name: str
    sport: str
    ps_eid: int = 0           # PS event ID from EventStore — travels to _build_etop_market

    name_score: int = 0
    opponent_score: int = 0
    sport_match: bool = False
    token_overlap: bool = False
    history_bonus: int = 0

    @property
    def combined(self) -> float:
        return (
            self.name_score      * W_NAME +
            self.opponent_score  * W_OPPONENT +
            (100 if self.sport_match else 0)  * W_SPORT +
            (100 if self.token_overlap else 0) * W_TOKEN +
            min(self.history_bonus * 10, 100)  * W_HISTORY
        )

    @property
    def action(self) -> str:
        s = self.combined
        if s >= SCORE_AUTO:
            return 'AUTO_MATCH'
        elif s >= SCORE_SUGGEST:
            return 'SUGGEST'
        return 'IGNORE'


# ── Helpers ───────────────────────────────────────────────────────────────

def _smart_score(a: str, b: str) -> int:
    """Best of character-level and token-level fuzzy, with safety caps.

    ChatGPT fixes applied:
    1. Token cap: if token_set=100 but ratio<70, cap at 85
    2. Subset penalty: if PS name has more tokens, cap at 90
    """
    al, bl = a.lower(), b.lower()
    al_clean = ' '.join(w for w in al.split() if w not in FILLER) or al
    bl_clean = ' '.join(w for w in bl.split() if w not in FILLER) or bl
    ratio = fuzz.ratio(al_clean, bl_clean)
    token = fuzz.token_set_ratio(al_clean, bl_clean)

    best = max(ratio, token)

    # Cap 1: token_set_ratio inflates subsets
    if token == 100 and ratio < 70:
        best = min(best, 85)

    # Cap 2: PS name has MORE tokens → superset match → penalize
    a_tokens = set(al.split()) - FILLER
    b_tokens = set(bl.split()) - FILLER
    if b_tokens and a_tokens and len(b_tokens) > len(a_tokens):
        best = min(best, 90)

    return best


def _smart_score_opponent(a: str, b: str) -> int:
    """Same as _smart_score but no caps — opponent matching should be raw."""
    al, bl = a.lower(), b.lower()
    return max(fuzz.ratio(al, bl), fuzz.token_set_ratio(al, bl))


def _get_tokens(name: str) -> set:
    """Extract distinctive tokens from a team name."""
    words = name.strip().lower().split()
    tokens = {w for w in words if w not in FILLER and len(w) >= 3}
    return tokens if tokens else {w for w in words if len(w) >= 2}


def _has_token_overlap(a: str, b: str) -> bool:
    return bool(_get_tokens(a) & _get_tokens(b))

# ── Game type detection from PS league strings ───────────────────────────

# PS groups all esports under sp=12. League name tells us the actual game.
_PS_LEAGUE_GAME_MAP = {
    # LoL keywords
    'lck': 'lol', 'lpl': 'lol', 'lec': 'lol', 'lcs': 'lol',
    'ljl': 'lol', 'pcs': 'lol', 'vcs': 'lol', 'cblol': 'lol',
    'tcl': 'lol', 'lla': 'lol', 'lco': 'lol',
    'league of legends': 'lol', 'lol': 'lol', 'worlds': 'lol', 'msi': 'lol',
    # Dota keywords
    'dota': 'dota2', 'dota 2': 'dota2', 'the international': 'dota2', 'dpc': 'dota2',
    'esl one': 'dota2', 'bts': 'dota2', 'fissure': 'dota2',
    # CS2 keywords
    'cs2': 'csgo', 'csgo': 'csgo', 'pgl': 'csgo', 'esl pro': 'csgo',
    'blast': 'csgo', 'iem': 'csgo', 'cct': 'csgo', 'esl challenger': 'csgo',
    'roobet': 'csgo',
    # Valorant keywords
    'valorant': 'valorant', 'vct': 'valorant', 'champions tour': 'valorant',
    # Sports (sp already distinguishes these, but for completeness)
    'nba': 'basketball', 'nbl': 'basketball', 'euroleague': 'basketball',
    'premier league': 'soccer', 'la liga': 'soccer', 'serie a': 'soccer',
    'bundesliga': 'soccer', 'ligue 1': 'soccer', 'mls': 'soccer',
    'concacaf': 'soccer', 'copa': 'soccer', 'uefa': 'soccer',
}

# Etop cat_type → normalized game
_ETOP_GAME_MAP = {
    'dota2': 'dota2', 'dota': 'dota2',
    'csgo': 'csgo', 'cs2': 'csgo',
    'lol': 'lol',
    'valorant': 'valorant',
    'sports_basketball': 'basketball',
    'sports_football': 'soccer', 'sports_soccer': 'soccer',
}


def _detect_ps_game(ps_league: str) -> str:
    """Detect game type from PS league string. Returns normalized game or ''."""
    lg = ps_league.lower()
    for keyword, game in _PS_LEAGUE_GAME_MAP.items():
        if keyword in lg:
            return game
    return ''


def _game_type_gate(etop_cat_type: str, ps_league: str) -> bool:
    """Returns True if game types are compatible. False = instant reject.

    If either side is unknown, passes (don't block on missing data).
    """
    etop_game = _ETOP_GAME_MAP.get(etop_cat_type, '')
    ps_game = _detect_ps_game(ps_league)

    if not etop_game or not ps_game:
        return True  # unknown = don't block

    return etop_game == ps_game


LEAGUE_FILLER = {
    'cs2', 'dota', 'dota2', 'lol', 'valorant', 'league', 'of', 'legends',
    'counter', 'strike', 'the', 'a', 'in', 'and', '2026', '2025', '2024',
    'season', 'stage', 'esports', 'esport', 'pro', 'tour', 'champions',
}

def _league_token_overlap(etop_lg: str, ps_lg: str) -> set:
    """Shared distinctive tokens between etop and PS league names."""
    def tokens(s):
        words = s.lower().replace('-', ' ').replace(':', ' ').split()
        return {w for w in words if w not in LEAGUE_FILLER and len(w) >= 3}
    return tokens(etop_lg) & tokens(ps_lg)


# ── Core matching ─────────────────────────────────────────────────────────

def evaluate_pair(
    etop_team: str,
    etop_opponent: str,
    ps_team: str,
    ps_opponent: str,
    sport: str,
    history_bonus: int = 0,
    ps_eid: int = 0,
) -> MatchSignals:
    """Score a single etop↔PS team pair using all signals.

    ChatGPT fix: short names (≤3 chars) require opponent ≥ 90.
    """
    name_score = _smart_score(etop_team, ps_team)
    opp_score = _smart_score_opponent(etop_opponent, ps_opponent)

    # Short name protection: "T1", "OG", "EG" — name score means nothing,
    # only opponent can confirm. If opponent < 90, kill the name signal.
    if len(etop_team.strip()) <= 3 and opp_score < 90:
        name_score = min(name_score, 50)

    return MatchSignals(
        etop_name=etop_team,
        ps_name=ps_team,
        sport=sport,
        ps_eid=ps_eid,
        name_score=name_score,
        opponent_score=opp_score,
        sport_match=True,  # caller pre-filters by sport
        token_overlap=_has_token_overlap(etop_team, ps_team),
        history_bonus=history_bonus,
    )


def find_best_match(
    etop_team: str,
    etop_opponent: str,
    ps_events: list,         # [(ps_home, ps_away, eid, league), ...] — from EventStore
    sport: str,
    alias_db=None,           # AliasDB instance (optional)
    etop_league: str = '',   # for league overlap check
    etop_cat_type: str = '', # for game type gate (dota2/csgo/lol/valorant/sports_basketball)
) -> Tuple[Optional[MatchSignals], str]:
    """Find best PS match for an etop team+opponent pair.

    ps_events: list of (home, away, eid) tuples from EventStore.
    Returns: (signals, method) or (None, 'no_match')
    Methods: 'alias', 'auto_match', 'suggest', 'ignored', 'no_match'
    """

    # Step 0: Alias lookup — name translation ONLY
    # Alias gives better search name but does NOT bypass scoring.
    if alias_db:
        alias = alias_db.lookup(etop_team, sport)
        if alias:
            etop_team = alias.ps_name  # translate name, fall through to Step 1

    # Step 1: Evaluate all PS events (with game type gate)
    candidates = []
    for ps_home, ps_away, ps_eid, ps_league in ps_events:
        # Game type gate: instant reject if game mismatch
        if etop_cat_type and not _game_type_gate(etop_cat_type, ps_league):
            continue
        hist = alias_db.get_history_bonus(etop_team, sport) if alias_db else 0

        # Try etop as home
        sig_h = evaluate_pair(etop_team, etop_opponent,
                              ps_home, ps_away, sport, hist, ps_eid)
        # Opponent override: if opponent is near-perfect, lower name gate
        # "NiP" (name=30) + "Gentle Mates" (opp=100) → let through
        name_gate = 10 if sig_h.opponent_score >= 95 else MIN_NAME
        if sig_h.name_score >= name_gate:
            candidates.append(sig_h)

        # Try etop as away
        sig_a = evaluate_pair(etop_team, etop_opponent,
                              ps_away, ps_home, sport, hist, ps_eid)
        name_gate = 10 if sig_a.opponent_score >= 95 else MIN_NAME
        if sig_a.name_score >= name_gate:
            candidates.append(sig_a)

    if not candidates:
        return None, 'no_match'

    # Step 2: Best combined score wins
    candidates.sort(key=lambda s: -s.combined)
    best = candidates[0]

    # Step 2b: Ambiguity penalty (ChatGPT fix)
    if len(candidates) >= 2:
        etop_tokens = _get_tokens(etop_team)
        shared_root = 0
        for sig in candidates[1:3]:
            ps_tokens = _get_tokens(sig.ps_name)
            if etop_tokens & ps_tokens:
                shared_root += 1
        if shared_root > 0:
            best = MatchSignals(
                etop_name=best.etop_name, ps_name=best.ps_name,
                sport=best.sport, ps_eid=best.ps_eid,
                name_score=int(best.name_score * 0.80),
                opponent_score=best.opponent_score,
                sport_match=best.sport_match,
                token_overlap=best.token_overlap,
                history_bonus=best.history_bonus,
            )
            log_info(f"[EVIDENCE] Ambiguity: {shared_root} competing "
                    f"candidates share tokens with '{etop_team}'")

    # Step 2c: League overlap boost (Session 21)
    # Opponent=95+ AND shared league keyword → promote SUGGEST → AUTO
    if (best.opponent_score >= 95 and etop_league
            and best.combined >= SCORE_SUGGEST and best.combined < SCORE_AUTO):
        ps_league_str = ''
        for ph, pa, pe, plg in ps_events:
            if pe == best.ps_eid:
                ps_league_str = plg
                break
        if ps_league_str:
            lg_overlap = _league_token_overlap(etop_league, ps_league_str)
            if lg_overlap:
                best = MatchSignals(
                    etop_name=best.etop_name, ps_name=best.ps_name,
                    sport=best.sport, ps_eid=best.ps_eid,
                    name_score=max(best.name_score, 70),
                    opponent_score=best.opponent_score,
                    sport_match=best.sport_match,
                    token_overlap=True,
                    history_bonus=best.history_bonus,
                )
                log_info(f"[EVIDENCE] League boost: '{etop_team}' "
                        f"league_overlap={lg_overlap} → promoted")

    # Log top 3 candidates
    for sig in candidates[:3]:
        log_info(f"[EVIDENCE] {sig.etop_name} ~ {sig.ps_name} | "
                f"name={sig.name_score} opp={sig.opponent_score} "
                f"token={'Y' if sig.token_overlap else 'N'} "
                f"hist={sig.history_bonus} eid={sig.ps_eid} "
                f"→ {sig.combined:.0f} [{sig.action}]")

    # Step 3: Take action
    if best.action == 'AUTO_MATCH':
        if alias_db:
            alias_db.auto_learn(best)
        return best, 'auto_match'

    elif best.action == 'SUGGEST':
        if alias_db:
            alias_db.suggest(best)
        return best, 'suggest'

    else:
        log_info(f"[EVIDENCE] {etop_team} best={best.combined:.0f} "
                f"→ IGNORED (below {SCORE_SUGGEST})")
        return None, 'ignored'
