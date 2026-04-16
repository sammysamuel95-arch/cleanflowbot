"""
v2perfectbot — core/math.py
No-vig fair odds removal and EV calculation.

These are the EXACT same formulas from bot_working.py lines 330-333, 1728-1732, 1734-1759.
Zero changes to math logic — just extracted into a clean module.
"""

from typing import Optional, Tuple


def no_vig(home_odds: float, away_odds: float) -> Tuple[float, float]:
    """Remove vigorish from a 2-way line. Returns (fair_home, fair_away).

    Example: home=1.925 away=1.840
      hp=1/1.925=0.5195, ap=1/1.840=0.5435, t=1.063
      fair_home = 1/(0.5195/1.063) = 2.046
      fair_away = 1/(0.5435/1.063) = 1.956
    """
    hp = 1.0 / home_odds
    ap = 1.0 / away_odds
    t = hp + ap
    return round(1.0 / (hp / t), 4), round(1.0 / (ap / t), 4)


def norm_hdp(v: float) -> float:
    """Normalize handicap to nearest 0.25 increment.

    Example: 1.3 → 1.25,  1.6 → 1.5,  -0.4 → -0.5
    """
    return round(float(v) * 4) / 4


def calculate_ev(etop_decimal: float, fair_odds: float) -> float:
    """Calculate EV% for a single side.

    etop_decimal: parimutuel decimal odds (1/pool_ratio)
    fair_odds: no-vig fair odds from PS3838

    Returns: EV as percentage. Positive = +EV.
    Example: etop_decimal=2.22, fair_odds=2.05
      fair_prob=1/2.05=0.4878
      EV = (2.22 × 0.4878 - 1) × 100 = +8.3%
    """
    if fair_odds <= 1.0:
        return -999.0  # Invalid fair odds
    fair_prob = 1.0 / fair_odds
    return round(((etop_decimal + 1) * fair_prob - 1.0) * 100, 2)


def compute_ev_pair(
    odds1: float,
    odds2: float,
    market: str,
    hdp_side: Optional[str],
    ou_side: Optional[str],
    team1_is_home: bool,
    fair_home: float,
    fair_away: float,
    fair_over: float = 0.0,
    fair_under: float = 0.0,
) -> Tuple[Optional[float], Optional[float]]:
    """Calculate EV for both sides of a market.

    Returns (ev1, ev2) or (None, None) if PS line is missing.

    This is the EXACT logic from bot_working.py compute_ev_pair() lines 1734-1759,
    but with explicit parameters instead of reaching into dicts.

    Args:
        odds1: etopfun vs1 odds (decimal)
        odds2: etopfun vs2 odds (decimal)
        market: "ml", "hdp", "ou", "team_total"
        hdp_side: "team1", "team2", or None
        ou_side: "over", "under", or None
        team1_is_home: does etop team1 map to PS home?
        fair_home/fair_away: PS fair ML/HDP odds (after no_vig)
        fair_over/fair_under: PS fair OU odds (after no_vig)

    Returns:
        (ev1, ev2): EV% for each side, or (None, None) if PS line dropped.
    """
    if market in ("ou", "team_total"):
        if ou_side == "over":
            fair1, fair2 = fair_over, fair_under
        else:
            fair1, fair2 = fair_under, fair_over
    elif hdp_side is None:
        # ML or HDP without explicit side — use team1_is_home mapping
        fair1 = fair_home if team1_is_home else fair_away
        fair2 = fair_away if team1_is_home else fair_home
    elif hdp_side == "team1":
        fair1 = fair_home if team1_is_home else fair_away
        fair2 = fair_away if team1_is_home else fair_home
    else:  # hdp_side == "team2"
        fair1 = fair_home if team1_is_home else fair_away
        fair2 = fair_away if team1_is_home else fair_home

    # If fair odds are zero/missing → PS line dropped
    if not fair1 or not fair2 or fair1 <= 0 or fair2 <= 0:
        return None, None

    return calculate_ev(odds1, fair1), calculate_ev(odds2, fair2)


# ── Normal Distribution (pure Python, no scipy) ──────────────────────────────

import math as _math

def norm_cdf(x: float) -> float:
    """Standard normal CDF. P(Z <= x)."""
    return 0.5 * (1 + _math.erf(x / _math.sqrt(2)))


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF. Returns z such that P(Z <= z) = p.
    Rational approximation, accurate to ~4.5e-4.
    """
    if p <= 0.0 or p >= 1.0:
        return None
    t = _math.sqrt(-2 * _math.log(min(p, 1 - p)))
    c = t - (2.515517 + 0.802853 * t + 0.010328 * t * t) / \
            (1 + 1.432788 * t + 0.189269 * t * t + 0.001308 * t * t * t)
    return c if p > 0.5 else -c


def estimate_fair_from_curve(hdp_lines: list, target_signed: float,
                              min_lines: int = 4):
    """Extrapolate fair odds for a missing HDP line using PS's existing HDP menu.

    Fits a normal distribution (mu, sigma) to PS's visible HDP lines,
    then computes P(margin covers target) and returns fair decimal odds.

    NBA-only. Game margins follow normal distribution.

    Args:
        hdp_lines: from StandardStore.get_all_hdp_lines()
                   [(abs_line, raw_neg, raw_pos, fair_neg, fair_pos), ...]
        target_signed: signed line from giving team's perspective
                       e.g. -11.5 (giving) or +11.5 (getting)
        min_lines: minimum HDP line pairs required to fit

    Returns: fair decimal odds, or None if can't fit / out of bounds
    """
    if len(hdp_lines) < min_lines:
        return None

    # Step 1: Convert each line's fair_neg probability to Z-score
    # fair_neg = fair odds for giving side (favorite at that line)
    # P(favorite covers) = 1/fair_neg
    # Z = ppf(P)
    points = []
    for abs_line, raw_neg, raw_pos, fair_neg, fair_pos in hdp_lines:
        if fair_neg <= 1.0 or fair_pos <= 1.0:
            continue
        p_cover = 1.0 / fair_neg
        if p_cover <= 0.01 or p_cover >= 0.99:
            continue
        z = norm_ppf(p_cover)
        if z is None:
            continue
        points.append((abs_line, z))

    if len(points) < min_lines:
        return None

    # Step 2: Linear regression  abs_line = mu - sigma * z
    # Rewrite: y = a + b*x  where y=abs_line, x=z, a=mu, b=-sigma
    n = len(points)
    sum_x = sum(z for _, z in points)
    sum_y = sum(al for al, _ in points)
    sum_xy = sum(al * z for al, z in points)
    sum_x2 = sum(z * z for _, z in points)

    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-10:
        return None

    b = (n * sum_xy - sum_x * sum_y) / denom
    a = (sum_y - b * sum_x) / n

    mu = a
    sigma = -b

    if sigma < 8.0 or sigma > 16.0:
        return None  # NBA margin std dev is ~11-12, outside 8-16 = bad fit

    # Step 3: Compute probability for target line
    abs_target = abs(target_signed)
    z_target = (mu - abs_target) / sigma
    p_cover = norm_cdf(z_target)

    if target_signed < 0:
        p = p_cover          # giving: wants margin > abs_target
    elif target_signed > 0:
        p = 1.0 - p_cover    # getting: wants margin < abs_target
    else:
        p = p_cover           # line=0: P(win)

    if p <= 0.005 or p >= 0.995:
        return None

    return round(1.0 / p, 4)
