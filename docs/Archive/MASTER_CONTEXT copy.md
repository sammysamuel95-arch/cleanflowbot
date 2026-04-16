# MASTER CONTEXT — CleanFlowBot
# ANY AI SESSION READS THIS FIRST BEFORE DOING ANYTHING.
# Location: ~/VibeCoding/ProjectBot/CleanFlowBot-clean/docs/MASTER_CONTEXT.md
#
# ══════════════════════════════════════════════════════════════════
# MAINTENANCE RULES — HOW TO KEEP THIS FILE UPDATED
# ══════════════════════════════════════════════════════════════════
#
# After EVERY successful change, the AI MUST update this file:
#   1. FILE MAP: new file → add row. Deleted → remove. Moved → update path.
#   2. KNOWN BUGS: fixed → REMOVE. New → ADD.
#   3. PROVEN & NEVER TOUCH: verified fix → ADD. NEVER remove.
#   4. CONFIGURATION: value changed → update. New → add.
#   5. CHANGELOG (bottom): add "- SXX: [what changed]"
#
# At END of every session: "Updating MASTER_CONTEXT.md with: [list]"
#
# NEVER CHANGE: "WHAT THIS BOT DOES", "ABSOLUTE RULES", "PROVEN & NEVER TOUCH"
# ══════════════════════════════════════════════════════════════════

---

# WHAT THIS BOT DOES (3 sentences)

CleanFlowBot monitors etopfun.com parimutuel markets against PS3838 (Pinnacle)
sharp odds. When etop odds deviate from PS fair odds in the final 30-90 seconds
before market close, the bot fires bets on the +EV side. The edge comes from
parimutuel pools being inefficient near close while Pinnacle's lines are sharp.

---

# PROJECT STRUCTURE

```
~/VibeCoding/ProjectBot/CleanFlowBot/           ← OLD (monolith, runs on VPS + local Mac)
~/VibeCoding/ProjectBot/CleanFlowBot-clean/     ← NEW (modular, LIVE on Mac, verified S31)
```

## VPS Infrastructure
```
VPS: 207.148.112.199 (Vultr Tokyo, 4GB, $20/mo — free credits)
  - Bot running at /opt/bot/ (old monolith deployed)
  - Redis: port 6379, password 'Cfb2025!vps'
  - PS account: NKUS95 / @Abcdbot12345 (pinnacle888.com)
  - Etop: direct connection (~62ms)
  - PS: direct connection (no proxy needed from Tokyo)
  - Playwright installed for etop session immortality
  - Panel accessible at http://207.148.112.199:8080

OLD VPS DESTROYED:
  - 45.77.47.73 (Singapore) — destroyed
  - 207.148.79.2 (Singapore) — destroyed
  - Singapore Vultr IPs blocked by Cloudflare for PS3838 (gambling ban)
  - Tokyo works direct — no proxy needed
```

---

# FILE MAP

## Root (CleanFlowBot-clean/)
| File | What it does | Status |
|------|-------------|--------|
| `main.py` | Clean orchestrator (~500 lines). Full L1→L2→L3 session manager. | S31 VERIFIED LIVE |
| `panel.py` | Dashboard web server (reads dash_state.json only) | S31 VERIFIED — data flow 33/33 |
| `config.py` | Loads data/bot_config.json. Has PS_PROXY support. | Updated |
| `container.py` | Single state object (Container + Market + SportConfig + to_dash_state) | S31 VERIFIED |

## modules/ — Clean separated logic (ALL VERIFIED S31)
| File | Reads | Writes | Status |
|------|-------|--------|--------|
| `classifier.py` | etop parents + listing | container.markets (create + update) | S31 verified, 44/45 |
| `matcher.py` | container.markets + ps_store | container.markets (ps_event_id) | S31 wired to structured_matcher |
| `valuator.py` | container.markets + ps_store + sport_config | container.markets (ev, phase) | S31 verified, try/finally |
| `fire_engine.py` | container + listing + ps_store | container.markets (fire state) | S31 all gates + kills-eid |
| `cancel_engine.py` | container.markets + listing | container.markets (reset) | S31 tracks by mid |
| `dashboard.py` | container | data/dash_state.json | S31 verified, 10 lines |

## matching/ — Identity resolution (UPGRADED S31)
| File | What it does | Status |
|------|-------------|--------|
| `structured_matcher.py` | Filter-first matching: game_type → league → geometric mean pair scoring | S31 NEW — 47/47 tests, 33/33 live regression |
| `league_map.py` | PS prefix parser, league keyword scoring, ETOP_GAME_MAP | S31 NEW — deterministic game_type |
| `alias_db.py` | Game_type keyed aliases (cs2/dota2/lol/valorant/basketball/soccer), 108 seeds | S31 UPGRADED — Bayern bug impossible |
| `classify.py` | Etop type → market descriptor | PROVEN |
| `pair.py` | `infer_sport_hint()` used | Stable |
| `evidence.py` | OLD fuzzy-first scoring | REPLACED by structured_matcher |
| `aliases.py` | Old alias system | Legacy |

## core/ — Proven logic (DO NOT REWRITE)
| File | What it does | Touch? |
|------|-------------|--------|
| `ev.py` | `compute_ev()` — THE single EV computation | PROVEN |
| `factory.py` | `StandardStore`, `OddsEntry`, `no_vig` | PROVEN |
| `event_store.py` | `TheOnlyStore` — wraps StandardStore + identity | PROVEN |
| `models.py` | `EtopMarket` dataclass | Stable |
| `math.py` | `no_vig()`, `norm_hdp()` | PROVEN |
| `logger.py` | Structured logging | Stable |
| `fire_db.py` | Fire history SQLite | Stable |
| `pool_estimator.py` | Tuhao pool estimation | Stable |
| `commands.py` | Panel command handler | Stable |
| `session_tracker.py` | Session health tracking | Stable |
| `auth/ps3838_auth.py` | PS auth, cookies, curl_cffi. Reads data/auth/cookie.json | PROVEN |

## feeds/ — External connections
| File | What it does | Touch? |
|------|-------------|--------|
| `ps3838_ws.py` | WS + feed into TheOnlyStore. Has `_ws_recover_event`. | Battle-tested |
| `ps3838_parse.py` | Raw WS arrays → structured dicts | PROVEN |
| `ps3838_rest.py` | REST search_event, compact_events | Stable |
| `etopfun_api.py` | All etop HTTP calls | Stable |
| `etop_session.py` | Etop session immortality — Steam SSO + Protonmail Guard | S31 TESTED 3/3 |
| `types.py` | InventoryItem etc | Stable |

## engine/ — Old firing code (being replaced by modules/)
| File | What it does | Touch? |
|------|-------------|--------|
| `fire_zone.py` | Old fire + cancel | REPLACED by modules/ |
| `strategy.py` | Old betting decisions | Partially replaced by SportConfig |
| `inventory.py` | Item management | Still used |

## tools/
| File | What it does | Status |
|------|-------------|--------|
| `test_vodds_login.py` | VODDS pure curl_cffi auth test | S31 NEW — full chain works |
| `redis_reader_test.py` | Mac-side health check: reads VPS Redis | Stable |

## data/auth/ (consolidated S31)
| File | Git? | Notes |
|------|------|-------|
| `credentials.json` | NO (.gitignore) | PS + Steam + Protonmail creds |
| `cookie.json` | NO | PS cookies (saved every 5min when healthy) |
| `session.json` | NO | Etop cookies |
| `ws_token.json` | NO | PS WS token + whitelabel domain |

## data/
| File | Git? | Notes |
|------|------|-------|
| `bot_config.json` | YES | All config. PS URLs = pinnacle888.com. PS_PROXY = '' |
| `aliases_learned.json` | NO | Learned aliases |
| `event_map.json` | NO | PS event cache |
| `playwright_etop_profile/` | NO | Saved Steam + Protonmail browser session |
| `log/bot.log` | NO | Bot output log |

## docs/
| File | What it is |
|------|-----------|
| `MASTER_CONTEXT.md` | THIS FILE |
| `SESSION_PROTOCOL.md` | How to start sessions, task template, examples |
| `MODULAR_ARCHITECTURE.md` | Container + 8 modules design |
| `STRUCTURED_MATCHER.md` | Filter-first matching architecture |
| `SESSION_IMMORTALITY_GUIDE_v2.md` | PS session 5 commandments |
| `API_BIBLE.md` | Every API endpoint documented |

---

# DATA FLOW

```
Pipeline (event-driven, wakes on etop data ~3s):
  classifier.run()  → container.markets (create + update odds)
  matcher.run()     → ps_event_id (structured_matcher: game_type → league → geometric mean)
  valuator.run()    → EV + phase (computes ONCE, try/finally alt-eid)
  fire_engine.run() → press.do (RECOMPUTES EV fresh, all 7 gates in ONE place)
  dashboard.run()   → dash_state.json (reads container only)

Background:
  cancel_engine     → remain>300 + fired → cancel (tracks by mid, independent 5s loop)
  tuhao             → pool estimation (5s, writes value_cap + raw_pool to Market)
  discovery         → REST search for unmatched (10s, budget=3)
  fetcher           → etop match_list poll (3s, dead-session detection)
  ps_session_loop   → keepalive 10s, cookie save 5min, L1→L2→L3 recovery
  etop_keepalive    → userconn_check every 5min

States (simplified S31):
  UNMATCHED → MATCHED → MONITOR → FIRE_ZONE → CLOSED
                                    P1 P2 P3
```

---

# MATCHING SYSTEM (S31 — structured_matcher.py)

```
STEP 0: Alias lookup by (name, game_type)     ← game_type keyed, Bayern-safe
STEP 1: Game type filter (parsed PS prefix)    ← deterministic, not fuzzy
STEP 2: League filter (cleaned comparison)     ← 85-100 for real matches
STEP 3: Geometric mean pair score              ← √(t1 × t2), both must contribute

Scoring:
  _team_score():  strip filler words → fuzz.token_set_ratio (whole-word)
  pair_score():   geometric mean √(t1 × t2) — one garbage kills pair
  Thresholds:     ≥80 AUTO_MATCH, 65-79 if gap≥10, 45-64 SUGGEST, <45 no_match
  MIN_INDIVIDUAL: 50 (either team below 50 → pair rejected)

PS League Parser (league_map.py):
  parse_ps_league("CS2 - BLAST Premier") → ("cs2", "BLAST Premier")
  Esports: prefix = game name (CS2, Dota 2, League of Legends, Valorant)
  Traditional: prefix = country (Italy, Germany) → returns ('', league)
  Keyword fallback only for no-separator strings (e.g. "NBA")

Alias DB (alias_db.py):
  Key: (normalized_name, game_type) — NOT (name, sport)
  108 seeds: cs2=19, dota2=12, lol=35, valorant=7, basketball=7, soccer=28
  Multi-game orgs: NAVI in cs2 ≠ NAVI in dota2

Proven: 47/47 unit tests, 33/33 live regression, 0 false positives
```

---

# ARCHITECTURAL DESIGN DECISIONS

## 1. Container Pattern (container.py)
All state lives in ONE object. Modules read/write to it. No duplicate computation.
- Valuator computes EV once → stores on Market
- Dashboard reads Market.ev1 → no recompute
- Fire engine RECOMPUTES fresh (by design — odds may change in 3s)
- Eliminates the old bug where brain, dashboard, and fire each computed EV separately

## 2. Simplified States (S31)
5 states only: UNMATCHED → MATCHED → MONITOR → FIRE_ZONE → CLOSED
- P1/P2/P3 are phases WITHIN FIRE_ZONE, not separate states
- Removed: APPROACHING, PREFIRE, FIRED, DISCOVERED (all useless complexity)

## 3. Fire Engine Gates (ONE place)
All 7 fire decisions in fire_engine.py:
```python
gates = [
    (o1 > 0 and o2 > 0,                                    "no_odds"),
    (best_ev > m.phase_min_ev,                              "ev<phase"),
    (etop_age < cfg.MAX_ETOP_AGE,                           "etop_stale"),
    (ps_age < sport_cfg.max_ps_age,                         "ps_stale"),
    (m.raw_pool >= cfg.MIN_RAW_POOL,                        "pool"),
    (m.remaining_cap > 0 or m.value_cap == 0,               "cap_full"),
    (m.total_fired < cfg.MAX_ITEMS,                         "max_items"),
]
```
Dashboard shows `m.last_gate_failures` — see exactly WHY a market didn't fire.

## 4. Session Immortality
- Etop: Playwright Steam SSO → Protonmail Guard code grab → auto-recovery (3/3 tests)
- PS: Raw HTTP keepalive (bal + ka + system_status every 10s), cookie save every 5min
- PS recovery: L1 retry → L2 disk reload → L3 curl_cffi with progressive cooldowns
- WS: _ws_recover_event wakes WS immediately after cookie refresh
- VODDS: Pure curl_cffi auth (no Playwright) — login → pin/login → auth-token → wstoken

---

# PER-SPORT CONFIGURATION (container.py SportConfig)

```
esports:    P1=2.0% P2=1.5% P3=1.0%  trigger=90s  max_ps_age=300s
basketball: P1=0.0% P2=0.0% P3=0.0%  trigger=50s  max_ps_age=120s
soccer:     P1=3.0% P2=2.0% P3=1.5%  trigger=50s  max_ps_age=300s
```

---

# PS SESSION RECOVERY (main.py _ps_session_loop)

```
Normal (session_alive=True):
  Every 10s: raw HTTP calls (NOT method calls — ps_auth.keep_alive() DOES NOT EXIST)
    POST /member-service/v2/account-balance (bal_ok)
    GET  /member-auth/v2/keep-alive (ka_ok)
    GET  system_status()
  If ka_ok AND bal_ok:
    Save cookies to disk every 5min (Commandment 5)
    Check WS escalation (_ws_needs_recovery)
  3 consecutive fails → session_alive=False → recovery

Recovery (session_alive=False):
  L1 RETRY:    3 keepalive tests — transient blips self-heal
  L2 RELOAD:   reload disk cookies → retest
  L3 CURL_CFFI: progressive cooldowns [0,3,5,8,12,18,24] min × 3 cycles
                keeps testing during cooldowns (self-heal check)
                After all exhausted → HARD STOP (human intervention)

CRITICAL: keepalive uses raw HTTP calls via ps_auth._session
  NEVER call ps_auth.keep_alive() or ps_auth.account_balance() — THEY DON'T EXIST
  This was a bug in S31 that caused unnecessary L3 triggers → MULTIPLE_LOGIN risk
```

---

# ETOP SESSION IMMORTALITY (feeds/etop_session.py)

```
Detection: match_list returns < 10 parents for 3 consecutive fetches
  OR: userconn_check() returns False at startup

Recovery chain:
  1. Playwright opens etopfun.com/loginsteam.do
  2. If Steam alive → auto-redirect → fresh cookies (2-5 sec)
  3. If Steam dead → fill form from data/auth/credentials.json
  4. If Steam Guard → open Protonmail tab → grab code → type it
  5. SSO confirm → click Sign In
  6. Extract cookies → reload session

Startup bootstrap: userconn_check() → if dead → auto_recover()
Tested: 3/3 pass (2 headless=False, 1 headless=True). ~1m12s-1m28s per recovery.
Credentials in data/auth/credentials.json (steam_user, steam_pass, proton_email, proton_pass)
```

---

# VODDS AUTH (tools/test_vodds_login.py — S31 NEW)

```
Pure curl_cffi. NO Playwright. NO browser. Fully automatable.

Flow:
  1. POST vodds.com/member/login → VSESS2 cookie (username=usdzc2861736)
  2. POST vodds.com/member/sport/pin/login → returns pre-auth URL with token
     URL format: https://3.33.215.100#b2bp_uyfnltp/member-auth/v2/pre-auth?...
     Parse: IP#b2bp_{subdomain} → https://{subdomain}.lenvora8.com/...
  3. POST lenvora8.com/member-auth/v2/auth-token (JSON body with token)
     → x-app-data response header contains _ulp + PS cookies
  4. GET lenvora8.com/member-auth/v2/wstoken → WS token

Whitelabel domains rotate: lenvora8.com, auremi88.com, eviran66.com, mervani99.com
Domain stored in ws_token.json, read by _get_wl_host() — NEVER hardcoded

Status: Full chain tested, WS token obtained. Needs wiring into vodds_auth.py module.
Account: usdzc2861736 (free, unlimited accounts, no deposit)
```

---

# VPS INFRASTRUCTURE

```
VPS: 207.148.112.199 (Vultr Tokyo, 4GB Ubuntu)
Redis: port 6379, password 'Cfb2025!vps'
Firewall: port 6379 open for Mac IP (114.10.41.240), 8080+5000 open for panel
Bot: /opt/bot/ (old monolith deployed, DRY_RUN mode)
Panel: http://207.148.112.199:8080

Rate limits:
  Etop: ~60 req/min per IP. Safe at 2s interval.
  PS: no rate limit on WS. Token refresh instant from Tokyo.

CRITICAL: Singapore Vultr IPs blocked by Cloudflare for PS (gambling ban).
  Tokyo works direct — discovered after wasting 2 Singapore VPS instances.
  If future VPS needed, use Tokyo or any non-gambling-banned country.
```

---

# KNOWN BUGS & PROBLEMS

## 1. VODDS AUTH NOT WIRED INTO BOT
- test_vodds_login.py proves the full chain works (pure curl_cffi)
- Need vodds_auth.py module + wire into main.py replacing pinnacle888 auth
- Priority: HIGH (next session)

## 2. CANCEL UNTESTED IN REAL SCENARIO
- cancel_engine.py has clean logic (mid-based tracking)
- Needs real-world testing
- Priority: MEDIUM

## 3. PANEL DOWNTIME COUNTER BUG
- Red counter keeps increasing after WS reconnects
- Should reset when ws_connected goes True
- Display bug in panel.py
- Priority: LOW

## 4. POLLING TOO SLOW
- Etop poller: currently 3s, should be 1.25s
- WS resub: should be 1s per sport (not all at once)
- Priority: MEDIUM (next session)

## 5. FIRE_DB NOT WIRED
- fire_engine.py doesn't log to SQLite yet
- No post-session analysis without it
- Priority: LOW (not blocking live operation)

---

# PROVEN & NEVER TOUCH

1. ML WS index: `ml_data[0]`=AWAY, `ml_data[1]`=HOME (ALL sports)
2. classify_etop_sub: type=3 totalScore>0 = KILLS not Game Winner
3. type=8 offerScore=0: Dota/LoL = Game Winner, CS2 = 10 Kills
4. HDP giving_side: etop offerTeam=1 → team1 gives → store uses negative line
5. no_vig formula in factory.py
6. WS home/away lock: _event_teams write-once prevents flip corruption
7. Session immortality: L1→L2→L3 recovery with RAW HTTP calls (proven S24+, S31)
8. Sequential press.do: 75ms gap, parallel = "trygain.later"
9. compute_ev(): THE single EV path — never create a second one
10. ps3838_parse.py index positions: verified across sessions
11. Etop session immortality: Playwright Steam SSO + Protonmail Guard code (3/3 tests S31)
12. WS recover_event: cookie refresh → immediate WS reconnect (not 5min sleep)
13. Etop rate limit: ~60 req/min per IP. Safe at 2s.
14. Singapore Vultr IPs blocked by Cloudflare for PS — use Tokyo
15. curl_cffi PS login works from Tokyo VPS — no Playwright needed for PS
16. Kills OU: compute_ev needs kills eid fallback in ALL paths (valuator + fire_engine)
17. Structured matcher: geometric mean √(t1×t2), token_set_ratio, 33/33 live regression
18. Alias DB: game_type keys (cs2/dota2/lol/etc), NOT sport keys — prevents cross-game pollution
19. PS league parser: prefix is game name for esports, country for traditional — deterministic
20. PS keepalive: RAW HTTP calls only. ps_auth.keep_alive()/account_balance() DO NOT EXIST
21. Cookie save: only when ka_ok AND bal_ok (healthy), throttle 5min
22. VODDS auth: pure curl_cffi, no Playwright. x-app-data header has PS cookies.

---

# ETOP ↔ PS NAMING DIFFERENCES

| Concept | Etop | PS3838 |
|---------|------|--------|
| Teams | vs1, vs2 (arbitrary) | home, away (WS flips) |
| Names | "NAVI" / "Clippers" | "Natus Vincere" / "Los Angeles Clippers" |
| Game ID | category.type = "csgo" | League prefix ("CS2 - BLAST Premier") |
| Market IDs | mid | eid + period + mk |
| Odds | Asian (0.52) | Decimal (1.52) |
| Handicap | offerScore + offerTeam | home_hdp (home perspective) |
| Close | remainTime in ms | Markets disappear |
| Domain | etopfun.com | pinnacle888.com (or VODDS whitelabel) |

---

# ACCOUNTS

```
PS3838 (Mac bot):     rans95 / @Abcdbot12345 (pinnacle888.com)
PS3838 (VPS):         NKUS95 / @Abcdbot12345 (pinnacle888.com)
VODDS:                usdzc2861736 (free, unlimited accounts, no deposit)
Etop:                 Steam SSO via etop01proton
Steam:                etop01proton / in data/auth/credentials.json
Protonmail:           etop01@proton.me / in data/auth/credentials.json
Webshare proxy:       autoprofitz@gmail.com (paid $31.39/yr, not needed from Tokyo)
VPS (Vultr Tokyo):    207.148.112.199 (root, password in Vultr dashboard)
```

---

# ABSOLUTE RULES FOR ANY AI SESSION

1. NEVER rewrite working code from scratch
2. One change → verify → next change
3. Discuss with Sammy BEFORE writing code
4. Trust screenshots over bot output
5. Backup before any change
6. Never tell Sammy to sleep/rest/stop
7. Short surgical changes, not mega-rewrites
8. If stuck, say "I don't know"
9. Code is wrong until proven otherwise
10. Read THIS FILE first before touching anything
11. UPDATE THIS FILE after every successful change
12. Only edit files within your assigned SCOPE
13. NEVER restart the bot without explicit permission from Sammy
14. NEVER overwrite bot_output.log — check logs FIRST, report, then ask
15. PS keepalive = RAW HTTP calls. NEVER call ps_auth.keep_alive() — IT DOES NOT EXIST.
16. All Claude Code instructions as downloadable .md files — NEVER paste in chat

---

# CHANGELOG

- S28: Created CleanFlowBot-clean/ with modular structure
- S28: container.py — Container + Market + SportConfig
- S28: 6 modules: classifier, matcher, valuator, fire_engine, cancel_engine, dashboard
- S28: main.py orchestrator (280 lines replaces 1722)
- S28: Per-sport config: NBA=0%, soccer=2%, esports=1-2%
- S28: Matcher self-healing: confidence decay → auto-unmatch
- S28: Cancel engine tracks by mid (not fire_key)
- S28: MASTER_CONTEXT.md + SESSION_PROTOCOL.md created
- S29: Etop session immortality (feeds/etop_session.py, 6/6 tests)
- S29: Full Steam form fill + Protonmail Guard code grab via Playwright
- S29: WS recover_event fix — immediate reconnect after Playwright
- S29: Applied WS fix to old bot, verified working
- S30: VPS deployed — 207.148.112.199 (Vultr Tokyo, 4GB)
- S30: Discovered: Singapore blocked by Cloudflare (gambling ban), Tokyo works
- S30: Etop collector: 2s interval, 62ms latency, 0 rate limits
- S30: PS: curl_cffi login from Tokyo (no Playwright, no proxy needed)
- S30: Bot deployed to VPS in DRY_RUN mode
- S30: Dashboard kills EV fix — added kills fallback to both dash compute_ev paths
- S30: Panel kills display fix — check e1==null instead of pf=='–'
- S30: Verify gate fix — partial_ratio replaces ratio (fixes NBA short names)
- S30: STRUCTURED_MATCHER.md written — filter-first matching architecture
- S31: structured_matcher.py — geometric mean scoring, 47/47 tests, 33/33 live regression
- S31: league_map.py — PS prefix parser, ETOP_GAME_MAP, keyword fallback for no-separator
- S31: alias_db.py upgraded — game_type keys (cs2/dota2/lol/etc), 108 seeds, Bayern-safe
- S31: modules/matcher.py wired to structured_matcher (evidence_match → match_event)
- S31: container.py verified — Market dataclass, to_dash_state(), SportConfig
- S31: classifier.py verified — line update fix, 44/45 synthetic tests
- S31: valuator.py — try/finally alt-eid, simplified states (removed APPROACHING/PREFIRE)
- S31: fire_engine.py — all 7 gates, kills-eid fallback, press IDs for cancel
- S31: dashboard.py verified — 10 lines, reads container only
- S31: panel.py — data flow verified 33/33 (19/19 pipeline test)
- S31: main.py — full L1→L2→L3 session manager with raw HTTP keepalive
- S31: Cookie save every 5min on healthy state (Commandment 5)
- S31: live_feed._auth = ps_auth wired
- S31: Etop startup bootstrap — userconn_check() → auto_recover()
- S31: Etop session immortality re-tested — 3/3 pass (headless works)
- S31: VODDS pure curl_cffi auth — login → pin/login → auth-token → wstoken (no Playwright)
- S31: Credentials consolidated into data/auth/credentials.json
- S31: PS_PROXY cleared for Mac (no SOCKS5 needed)
- S31: Bot tested live on Mac — WS connected, matching working, pipeline flowing
