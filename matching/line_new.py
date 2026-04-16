"""Line matching — does PS have this market?

Uses StandardStore. No _pick_by_favorite. No HDP sign guessing.
Just: does the market exist?
"""


def find_line(etop_market, standard_store):
    """Does PS have a matching line for this EtopMarket?

    Returns True/False. That's it.
    f10k, duration, unknown → always False (no PS equivalent).
    """
    if etop_market.market not in ('ml', 'hdp', 'ou', 'team_total'):
        return False
    if not etop_market.ps_event_id:
        return False

    eid = etop_market.ps_event_id
    m = etop_market.map_num

    if etop_market.market == 'ml':
        return standard_store.has_ml(eid, m)
    if etop_market.market == 'hdp':
        return standard_store.has_hdp(
            eid, m, etop_market.line,
            etop_market.ps_name_team1, etop_market.ps_name_team2)
    if etop_market.market in ('ou', 'team_total'):
        return standard_store.has_ou(eid, m, etop_market.line)
    return False
