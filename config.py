"""
config.py — loads data/bot_config.json, exposes every key as a module-level variable.
Missing key = KeyError at startup. No silent defaults anywhere.
"""
import json as _json
import os as _os

_cfg_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'data', 'bot_config.json')

def _load():
    with open(_cfg_path) as _f:
        return _json.load(_f)

_cfg = _load()

def _req(key):
    if key not in _cfg:
        raise KeyError(f"[CONFIG] Missing required key in bot_config.json: '{key}'")
    return _cfg[key]

# ── Mode ───────────────────────────────────────────────────────────────────────
DRY_RUN                  = _req('DRY_RUN')

# ── EV / timing ───────────────────────────────────────────────────────────────
MIN_EV                   = _req('MIN_EV')
PHASE1_EV                = _req('PHASE1_EV')
PHASE2_EV                = _req('PHASE2_EV')
PHASE3_EV                = _req('PHASE3_EV')
TRIGGER_SECS             = _req('TRIGGER_SECS')
EXTENSION_SECS           = _req('EXTENSION_SECS')
MAX_PS_AGE               = _req('MAX_PS_AGE')
MAX_ETOP_AGE             = _req('MAX_ETOP_AGE')

# ── Betting limits ─────────────────────────────────────────────────────────────
MAX_POOL_IMPACT          = _req('MAX_POOL_IMPACT')
HARD_CAP                 = _req('HARD_CAP')
MIN_RAW_POOL             = _req('MIN_RAW_POOL')
MAX_ITEMS                = _req('MAX_ITEMS')
MAX_ODDS                 = _req('MAX_ODDS')
TUHAO_SECS               = _req('TUHAO_SECS')
EXTRAP_MIN_LINES         = _req('EXTRAP_MIN_LINES')

# ── Fire queue / timing ────────────────────────────────────────────────────────
FIRE_QUEUE_GAP_MS        = _req('FIRE_QUEUE_GAP_MS')
FIRE_SAME_MKT_COOLDOWN_MS = _req('FIRE_SAME_MKT_COOLDOWN_MS')
FIRE_API_GAP_MS          = _req('FIRE_API_GAP_MS')
TUHAO_REFRESH_SECS       = _req('TUHAO_REFRESH_SECS')

# ── Auth ───────────────────────────────────────────────────────────────────────
COOKIE_REFRESH_INTERVAL  = _req('COOKIE_REFRESH_INTERVAL')

# ── Matching ───────────────────────────────────────────────────────────────────
PAIR_MIN_CONFIDENCE      = _req('PAIR_MIN_CONFIDENCE')
MATCH_CACHE_TTL          = _req('MATCH_CACHE_TTL')
MATCH_CACHE_MAX_MISSES   = _req('MATCH_CACHE_MAX_MISSES')

# ── PS provider ────────────────────────────────────────────────────────────────
PS_PROVIDER              = _cfg.get('PS_PROVIDER', 'vodds')

# ── PS3838 endpoints ───────────────────────────────────────────────────────────
PS_BASE_URL              = _req('PS_BASE_URL')
PS_WS_URL                = _req('PS_WS_URL')
PS_TOKEN_URL             = _req('PS_TOKEN_URL')
PS_LOGIN_URL             = _req('PS_LOGIN_URL')
PS_PROXY                 = _cfg.get('PS_PROXY', '')

# ── Etopfun endpoints ──────────────────────────────────────────────────────────
ETOP_BASE_URL            = _req('ETOP_BASE_URL')
ETOP_LIST_URL            = _req('ETOP_LIST_URL')

# ── Sport IDs ──────────────────────────────────────────────────────────────────
SP_BASKETBALL            = _req('SP_BASKETBALL')
SP_ESPORTS               = _req('SP_ESPORTS')
SP_SOCCER                = _req('SP_SOCCER')
SPORT_FILTER             = {SP_BASKETBALL, SP_ESPORTS, SP_SOCCER}
TARGET_SP                = {SP_BASKETBALL: "basketball", SP_ESPORTS: "esports", SP_SOCCER: "soccer"}

# ── WS market/period indices ───────────────────────────────────────────────────
MK_MAIN                  = _req('MK_MAIN')
MK_OU                    = _req('MK_OU')
MK_MAPS                  = _req('MK_MAPS')
WS_HDP_IDX               = _req('WS_HDP_IDX')
WS_OU_IDX                = _req('WS_OU_IDX')
WS_ML_IDX                = _req('WS_ML_IDX')
WS_PERIOD_IDX            = _req('WS_PERIOD_IDX')
REST_TEAM_TOTALS_IDX     = _req('REST_TEAM_TOTALS_IDX')
REST_SPECIAL_IDX         = _req('REST_SPECIAL_IDX')
REST_HDP_IDX             = _req('REST_HDP_IDX')
REST_OU_IDX              = _req('REST_OU_IDX')
REST_ML_IDX              = _req('REST_ML_IDX')
REST_PERIOD_IDX          = _req('REST_PERIOD_IDX')
HDP_HOME_IDX             = _req('HDP_HOME_IDX')
HDP_HOME_ODDS_IDX        = _req('HDP_HOME_ODDS_IDX')
HDP_AWAY_ODDS_IDX        = _req('HDP_AWAY_ODDS_IDX')


# ── Phase validation ───────────────────────────────────────────────────────────
if not (PHASE1_EV >= PHASE2_EV >= PHASE3_EV):
    print(f"[CONFIG] WARNING: Phase EVs not descending: "
          f"PHASE1_EV({PHASE1_EV}) >= PHASE2_EV({PHASE2_EV}) >= PHASE3_EV({PHASE3_EV})",
          flush=True)

# ── Paths (derived, not in bot_config.json) ────────────────────────────────────
import os
PROJECT_ROOT     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR         = os.path.join(PROJECT_ROOT, "data")
AUTH_DIR         = os.path.join(DATA_DIR, "auth")
COOKIE_FILE      = os.path.join(AUTH_DIR, "cookie.json")
SESSION_FILE     = os.path.join(AUTH_DIR, "session.json")
WS_TOKEN_FILE    = os.path.join(AUTH_DIR, "ws_token.json")
CREDENTIALS_FILE = os.path.join(AUTH_DIR, "credentials.json")
CONFIG_FILE      = os.path.join(DATA_DIR, "config.json")
ALIASES_FILE     = os.path.join(DATA_DIR, "aliases.json")
MATCH_CACHE_FILE = os.path.join(DATA_DIR, "match_cache.json")
DASH_STATE_FILE  = os.path.join(DATA_DIR, "dash_state.json")


def reload():
    """Re-read bot_config.json and update tuneable module-level vars."""
    global DRY_RUN, MIN_EV, PHASE1_EV, PHASE2_EV, PHASE3_EV, TRIGGER_SECS
    global MAX_PS_AGE, MAX_ETOP_AGE
    global MAX_POOL_IMPACT, HARD_CAP, MIN_RAW_POOL, MAX_ITEMS, MAX_ODDS, TUHAO_SECS, EXTRAP_MIN_LINES
    global FIRE_QUEUE_GAP_MS, FIRE_SAME_MKT_COOLDOWN_MS, FIRE_API_GAP_MS, TUHAO_REFRESH_SECS

    try:
        with open(_cfg_path) as f:
            cfg = _json.load(f)
    except Exception as e:
        print(f"[CONFIG] reload FAILED: {e}", flush=True)
        return False

    DRY_RUN               = cfg['DRY_RUN']
    MIN_EV                = cfg['MIN_EV']
    PHASE1_EV             = cfg['PHASE1_EV']
    PHASE2_EV             = cfg['PHASE2_EV']
    PHASE3_EV             = cfg['PHASE3_EV']
    if not (PHASE1_EV >= PHASE2_EV >= PHASE3_EV):
        print(f"[CONFIG] REJECTED: Phase EVs not descending: P1={PHASE1_EV} P2={PHASE2_EV} P3={PHASE3_EV}", flush=True)
        return False
    TRIGGER_SECS          = cfg['TRIGGER_SECS']
    MAX_PS_AGE            = cfg['MAX_PS_AGE']
    MAX_ETOP_AGE          = cfg['MAX_ETOP_AGE']
    MAX_POOL_IMPACT       = cfg['MAX_POOL_IMPACT']
    HARD_CAP              = cfg['HARD_CAP']
    MIN_RAW_POOL          = cfg['MIN_RAW_POOL']
    MAX_ITEMS             = cfg['MAX_ITEMS']
    MAX_ODDS              = cfg['MAX_ODDS']
    TUHAO_SECS            = cfg['TUHAO_SECS']
    EXTRAP_MIN_LINES          = cfg['EXTRAP_MIN_LINES']
    FIRE_QUEUE_GAP_MS         = cfg['FIRE_QUEUE_GAP_MS']
    FIRE_SAME_MKT_COOLDOWN_MS = cfg['FIRE_SAME_MKT_COOLDOWN_MS']
    FIRE_API_GAP_MS           = cfg['FIRE_API_GAP_MS']
    TUHAO_REFRESH_SECS        = cfg['TUHAO_REFRESH_SECS']
    print(f"[CONFIG] Reloaded: P1={PHASE1_EV}% P2={PHASE2_EV}% P3={PHASE3_EV}% "
          f"TRIGGER={TRIGGER_SECS} MAX_ITEMS={MAX_ITEMS} "
          f"DRY_RUN={DRY_RUN} TUHAO={TUHAO_SECS}", flush=True)
    return True
