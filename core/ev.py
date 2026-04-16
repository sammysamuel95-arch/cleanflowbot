"""EV calculation. Pure functions. No state.

calculate_ev: one side EV%
compute_ev: both sides of an EtopMarket against TheOnlyStore

THE single EV computation path. No other code in the system computes EV.
"""

from typing import Tuple, Optional
from core.logger import log_warn, log_info


def calculate_ev(etop_asian: float, fair_odds: float) -> float:
    """EV% for one side.

    etop_asian: Asian format odds (e.g. 0.52)
    fair_odds: PS no-vig decimal odds (e.g. 1.47)

    Formula: ((asian + 1) * (1/fair) - 1) * 100
    """
    if fair_odds <= 1.0:
        return -999.0
    fair_prob = 1.0 / fair_odds
    return round(((etop_asian + 1) * fair_prob - 1.0) * 100, 2)


last_reject_reason = None

def compute_ev(etop, store) -> Tuple[Optional[float], Optional[float]]:
    """Compute EV for an EtopMarket against TheOnlyStore.

    THE single EV function. Nothing else in the system computes EV.

    Returns: (ev1, ev2) or (None, None)
      ev1 = EV% for etop team1 (o1 side)
      ev2 = EV% for etop team2 (o2 side)
      None = can't compute (missing data, stale, ambiguous) → don't fire
    """
    global last_reject_reason
    last_reject_reason = None
    # ── Gates ──
    if not store.ready:
        last_reject_reason = "STORE_NOT_READY"
        return None, None

    if not etop.ps_name_team1 or not etop.ps_name_team2 or not etop.ps_event_id:
        last_reject_reason = "NO_PS_MATCH"
        return None, None

    if etop.market not in ('ml', 'hdp', 'ou', 'team_total'):
        last_reject_reason = f"UNKNOWN_MARKET({etop.market})"
        return None, None

    eid = etop.ps_event_id
    m = etop.map_num
    t1 = etop.ps_name_team1
    t2 = etop.ps_name_team2

    # ── ML ──
    if etop.market == 'ml':
        f1 = store.get_ml_fair(eid, m, t1)
        f2 = store.get_ml_fair(eid, m, t2)
        if f1 is None or f2 is None:
            # Try alternate eids (PS splits mk=1 and mk=3 into different eids)
            for _alt in store.find_alternate_eids(eid, t1):
                _f1 = store.get_ml_fair(_alt, m, t1)
                _f2 = store.get_ml_fair(_alt, m, t2)
                if _f1 is not None and _f2 is not None:
                    f1, f2 = _f1, _f2
                    break
            if f1 is None or f2 is None:
                last_reject_reason = "NO_PS_LINE"
                return None, None
        ev1 = calculate_ev(etop.o1, f1)
        ev2 = calculate_ev(etop.o2, f2)

    # ── HDP ──
    elif etop.market == 'hdp':
        gps = etop.giving_team_ps
        if not gps:
            log_warn("EV", f"HDP no giving team: {etop.label} eid={eid}")
            last_reject_reason = "NO_GIVING_TEAM"
            return None, None

        # Other team = the one NOT giving
        ops = t2 if gps == t1 else t1

        # giving team's signed line = -abs(line) (they GIVE)
        # getting team's signed line = +abs(line) (they RECEIVE)
        gf = store.get_hdp_fair(eid, m, gps, -abs(etop.line))
        of = store.get_hdp_fair(eid, m, ops, +abs(etop.line))

        if gf is None or of is None:
            # Try alternate eids (PS splits mk=1 and mk=3 into different eids)
            for _alt in store.find_alternate_eids(eid, t1):
                _gf2 = store.get_hdp_fair(_alt, m, gps, -abs(etop.line))
                _of2 = store.get_hdp_fair(_alt, m, ops, +abs(etop.line))
                if _gf2 is not None and _of2 is not None:
                    gf, of = _gf2, _of2
                    break
            if gf is None or of is None:
                # ── NBA HDP Extrapolation ──
                if 'nba' in (getattr(etop, 'league', '') or '').lower():
                    from core.math import estimate_fair_from_curve
                    import config as _cfg
                    _hdp_lines = store.get_all_hdp_lines(eid, m)
                    if _hdp_lines:
                        _min_l = getattr(_cfg, 'EXTRAP_MIN_LINES', 4)
                        _gf_est = estimate_fair_from_curve(_hdp_lines, -abs(etop.line),
                                                            min_lines=_min_l)
                        _of_est = estimate_fair_from_curve(_hdp_lines, +abs(etop.line),
                                                            min_lines=_min_l)
                        if _gf_est is not None and _of_est is not None:
                            gf, of = _gf_est, _of_est
                            log_info(f"[NBA_EXTRAP] {etop.label} eid={eid} "
                                     f"line={etop.line} gf={gf:.3f} of={of:.3f} "
                                     f"from {len(_hdp_lines)} PS lines")
                if gf is None or of is None:
                    last_reject_reason = "NO_PS_LINE"
                    return None, None

        # Map giving/getting → o1/o2
        if etop.giving_side == 'team1':
            ev1 = calculate_ev(etop.o1, gf)
            ev2 = calculate_ev(etop.o2, of)
        else:
            ev1 = calculate_ev(etop.o1, of)
            ev2 = calculate_ev(etop.o2, gf)

    # ── OU ──
    elif etop.market in ('ou', 'team_total'):
        fo = store.get_ou_fair(eid, m, "over", etop.line)
        fu = store.get_ou_fair(eid, m, "under", etop.line)
        if fo is None or fu is None:
            # Try alternate eids (PS splits mk=1 and mk=3 into different eids)
            for _alt in store.find_alternate_eids(eid, t1):
                _fo2 = store.get_ou_fair(_alt, m, "over", etop.line)
                _fu2 = store.get_ou_fair(_alt, m, "under", etop.line)
                if _fo2 is not None and _fu2 is not None:
                    fo, fu = _fo2, _fu2
                    break
            if fo is None or fu is None:
                last_reject_reason = "NO_PS_LINE"
                return None, None
        # etop: o1=over, o2=under (⚠️ PENDING VERIFICATION — see Chapter 28)
        ev1 = calculate_ev(etop.o1, fo)
        ev2 = calculate_ev(etop.o2, fu)

    else:
        return None, None

    return ev1, ev2
