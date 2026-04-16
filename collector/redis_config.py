"""
collector/redis_config.py — Redis connection factory.

Single place that knows how to build a Redis client.
Both etop_collector and ps_collector import from here.
"""

import redis


def make_redis(host: str = '127.0.0.1', port: int = 6379,
               password: str = '', db: int = 0) -> redis.Redis:
    """Return a connected Redis client. Raises on connection failure."""
    kwargs = dict(host=host, port=port, db=db, decode_responses=True)
    if password:
        kwargs['password'] = password
    r = redis.Redis(**kwargs)
    r.ping()   # fail fast
    return r


# Key namespace constants — used by both collector and Mac reader
class K:
    # Etop data
    PARENTS         = "etop:parents"          # JSON list
    ACTIVE_MIDS     = "etop:active_mids"      # JSON list of mid strings
    LISTING         = "etop:listing:{mid}"    # per-mid JSON
    ACTIVE_SPORTS   = "etop:active_sports"    # JSON list of PS sport IDs
    LAST_FETCH      = "etop:last_fetch"       # float timestamp string

    # PS data
    PS_ODDS         = "ps:odds:{eid}:{m}"     # JSON bucket
    PS_EVENT        = "ps:event:{eid}"        # JSON event dict
    PS_EVENTS_SP    = "ps:events_by_sport:{sp}"
    PS_TEAMS        = "ps:teams:{eid}"
    PS_LINE_AGE     = "ps:line_age:{eid}:{m}:{mkt}"

    # Health
    HB_ETOP         = "health:etop:vps1"
    HB_PS           = "health:ps:vps1"

    # TTLs (seconds)
    TTL_ETOP_DATA   = 15
    TTL_ETOP_META   = 30
    TTL_PS_ODDS     = 120
    TTL_PS_EVENT    = 86400
    TTL_HB          = 30
