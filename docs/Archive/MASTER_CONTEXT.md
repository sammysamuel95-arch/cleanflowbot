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
~/VibeCoding/ProjectBot/CleanFlowBot/           ← OLD (monolith, still runs live bot)
~/VibeCoding/ProjectBot/CleanFlowBot-clean/     ← NEW (modular, clean structure)
```

## VPS Infrastructure
```
VPS: 207.148.79.2 (Vultr Singapore, 2GB, $10/mo)
  - Redis: port 6379, password 'Cfb2025!vps'
  - Etop collector: 2s poll interval, direct connection (62ms)
  - PS collector: WS through SOCKS5 proxy (Cloudflare blocks Vultr IPs)
  - Proxy: socks5://rxosiilx:ixxq4bdohxzj@142.111.67.146:5611 (webshare.io free, Tokyo)
  - PS account on VPS: NKUS95 / @Abcdbot12345 (pinnacle888.com)
  - Etop uses same session as Mac bot

Old VPS: 45.77.47.73 — DESTROY THIS (replaced by 207.148.79.2)
```

---

# FILE MAP

## Root (CleanFlowBot-clean/)
| File | What it does | Status |
|------|-------------|--------|
| `main.py` | Clean orchestrator (~280 lines) | NEW — replaces 1722-line monolith |
| `main_old_monolith.py` | Original main.py kept as reference | Reference only |
| `panel.py` | Dashboard web server | Unchanged |
| `config.py` | Loads data/bot_config.json | Unchanged |
| `container.py` | Single state object (Container + Market + SportConfig) | NEW |

## modules/ — Clean separated logic (NEW)
| File | Reads | Writes |
|------|-------|--------|
| `classifier.py` | etop parents + listing | container.markets (create + update) |
| `matcher.py` | container.markets + ps_store | container.markets (ps_event_id) |
| `valuator.py` | container.markets + ps_store + sport_config | container.markets (ev, phase) |
| `fire_engine.py` | container + listing + ps_store | container.markets (fire state) |
| `cancel_engine.py` | container.markets + listing | container.markets (reset) |
| `dashboard.py` | container | data/dash_state.json |

## collector/ — VPS standalone fetchers (NEW)
| File | What it does |
|------|-------------|
| `collector_main.py` | VPS entry point: --etop-interval, --redis-pass, --etop-only, --ps-only |
| `etop_collector.py` | Polls match_list → Redis. Rate limit detection + backoff |
| `ps_collector.py` | Runs Pinnacle888LiveFeed → Redis. Reads etop:active_sports for adaptive rotation |
| `redis_config.py` | Redis connection factory + key namespace constants (K.*) |
| `setup_vps.sh` | Fresh Ubuntu VPS install script |

## tools/
| File | What it does |
|------|-------------|
| `redis_reader_test.py` | Mac-side health check: reads VPS Redis |
| `ps_token_pusher.py` | (if exists) Pushes Mac WS token to VPS |

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

## feeds/ — External connections
| File | What it does | Touch? |
|------|-------------|--------|
| `ps3838_ws.py` | WS connection + feed into TheOnlyStore | Battle-tested. Has `_ws_recover_event` for immediate reconnect after Playwright |
| `ps3838_parse.py` | Raw WS arrays → structured dicts | PROVEN |
| `ps3838_rest.py` | REST search_event, compact_events | Stable |
| `ps3838_auth.py` | PS auth, cookies, curl_cffi, Playwright | PROVEN. VPS version has SOCKS5 proxy |
| `etopfun_api.py` | All etop HTTP calls | Stable |
| `etop_session.py` | **Etop session immortality** — auto-detect dead session, Playwright re-login with Steam form fill + Protonmail Guard code grab | NEW, TESTED 6/6 |
| `types.py` | InventoryItem etc | Stable |

## matching/ — Identity resolution
| File | What it does | Touch? |
|------|-------------|--------|
| `evidence.py` | 5-signal scoring | Works but fragile |
| `classify.py` | Etop type → market descriptor | PROVEN |
| `alias_db.py` | Self-learning name DB (auto_learn DISABLED) | Needs improvement |
| `pair.py` | `infer_sport_hint()` used, `PairMatcher` legacy | Partial use |
| `aliases.py` | Old alias system | Legacy |

## engine/ — Old firing code (being replaced by modules/)
| File | What it does | Touch? |
|------|-------------|--------|
| `fire_zone.py` | Old fire + cancel | REPLACED by modules/ |
| `strategy.py` | Old betting decisions | Partially replaced by SportConfig |
| `inventory.py` | Item management | Still used |

## data/
| File | Git? | Notes |
|------|------|-------|
| `bot_config.json` | YES | All config. PS URLs = pinnacle888.com |
| `session.json` | NO | Etop cookies |
| `cookie.json` | NO | PS cookies |
| `secrets.env` | NO (.gitignore) | Steam + Protonmail creds |
| `aliases_learned.json` | NO | Learned aliases |
| `event_map.json` | NO | PS event cache |
| `playwright_etop_profile/` | NO | Saved Steam + Protonmail browser session |

## docs/
| File | What it is |
|------|-----------|
| `MASTER_CONTEXT.md` | THIS FILE |
| `SESSION_PROTOCOL.md` | How to start sessions, task template, examples |
| `MODULAR_ARCHITECTURE.md` | Container + 8 modules design |
| `API_BIBLE.md` | Every API endpoint documented |

---

# DATA FLOW

```
VPS (207.148.79.2):
  Etop collector (2s) → Redis (etop:parents, etop:listing:*)
  PS collector (WS via proxy) → Redis (ps:odds:*, ps:event:*)

Mac Bot reads from:
  Local etop (3s poll) — primary
  Local PS WS — primary  
  VPS Redis — supplementary (TODO: wire into bot)

Pipeline (in new modular main.py):
  classifier.run()  → container.markets
  matcher.run()     → ps_event_id
  valuator.run()    → EV + phase
  fire_engine.run() → press.do
  dashboard.run()   → dash_state.json

Background:
  cancel_engine     → remain>300 + fired → cancel
  tuhao             → pool estimation
  discovery         → REST search for unmatched
```

---

# KEY DATA STRUCTURES

## Market (container.py)
```
IDENTITY:   mid, fire_key, team1, team2, market_type, line, map_num,
            label, giving_side, game, sport, league, cat_type
ETOP LIVE:  o1, o2, remain, can_press, cancel_code, etop_age
PS MATCH:   ps_event_id, ps_name_team1/2, match_confidence, match_method,
            match_locked, match_rejected_eids, ev_none_cycles
VALUATION:  ev1, ev2, best_ev, best_side, ps_fair_1/2, ps_raw_1/2, ps_age
FIRE STATE: phase, phase_min_ev, total_fired, total_value, value_cap,
            cap_source, remaining_cap, locked_side, consumed_item_ids, press_ids
LIFECYCLE:  state, created_at, last_seen, dead_at, locked_at
DEBUG:      last_gate_failures
```

## Container._etop_markets
```
Dict[mid → EtopMarket] — internal bridge for compute_ev compatibility
Classifier writes. Valuator reads. Dashboard never touches.
```

---

# PER-SPORT CONFIGURATION (container.py SportConfig)

```
esports:    P1=2.0% P2=1.5% P3=1.0%  trigger=90s  max_ps_age=300s
basketball: P1=0.0% P2=0.0% P3=0.0%  trigger=50s  max_ps_age=120s
soccer:     P1=3.0% P2=2.0% P3=1.5%  trigger=50s  max_ps_age=300s
```

---

# ETOP SESSION IMMORTALITY (feeds/etop_session.py)

```
Detection: match_list returns < 10 parents for 3 consecutive fetches
Recovery chain:
  1. Playwright opens etopfun.com/loginsteam.do
  2. If Steam session alive → auto-redirect → fresh cookies (2-5 sec)
  3. If Steam dead → fill form from secrets.env
  4. If Steam Guard → open Protonmail tab → grab code → type it
  5. SSO confirm page → click Sign In
  6. Extract cookies → reload session → verified working
Tested: 6/6 tests pass. Full headless recovery in ~2 seconds (fast path)
         or ~1 min (full chain with Steam Guard email).
```

---

# PS WS RECOVERY (feeds/ps3838_ws.py)

```
Problem: After Playwright cookie refresh, WS loop was stuck in 5-min REST_ONLY_COOLDOWN sleep
Fix: Added _ws_recover_event (asyncio.Event)
  - Playwright success → live_feed._ws_recover_event.set()
  - WS loop wakes immediately instead of sleeping 5 min
  - Also resets _token_fail_cycles = 0 to prevent re-escalation
Applied to: OLD bot (CleanFlowBot/) — live fix, verified working
```

---

# VPS INFRASTRUCTURE

```
VPS: 207.148.79.2 (Vultr Singapore, 2GB Ubuntu)
Redis: port 6379, password 'Cfb2025!vps', max 256MB, no disk persistence
Firewall: port 6379 open only for Mac IP (114.10.41.240)

Collectors:
  Etop: direct connection, 2s interval, ~62ms per cycle
  PS: through SOCKS5 proxy (Vultr IPs blocked by Cloudflare for PS3838)
  Proxy: socks5://rxosiilx:ixxq4bdohxzj@142.111.67.146:5611 (webshare.io free, Tokyo)
  PS account: NKUS95 / @Abcdbot12345 (pinnacle888.com)

Rate limits discovered:
  Etop: ~60 requests/minute (per IP). Safe interval = 2s
  PS: no rate limit on WS (streaming). Token refresh = 1.2s through proxy

Redis key schema:
  etop:parents             → full parent list JSON (TTL 15s)
  etop:listing:{mid}       → {o1, o2, remain, can_press} (TTL 15s)
  etop:active_mids         → [mid1, mid2, ...] (TTL 15s)
  etop:active_sports       → [12, 29, 4] (TTL 30s, PS uses for adaptive rotation)
  etop:last_fetch          → timestamp (TTL 30s)
  ps:odds:{eid}:{map}      → serialized odds bucket (TTL 120s)
  ps:event:{eid}           → {home, away, sp, league} (TTL 86400s)
  ps:events_by_sport:{sp}  → [eid list] (TTL 300s)
  ps:line_age:{eid}:{map}:{mkt} → timestamp (TTL 600s)
  ps:teams:{eid}           → {home, away} (TTL 86400s)
  health:etop:vps1         → heartbeat (TTL 30s)
  health:ps:vps1           → heartbeat (TTL 30s)

IMPORTANT: Vultr Singapore IPs are Cloudflare-blocked for PS3838/pinnacle888.
  All PS HTTP/WS traffic must go through SOCKS5 proxy.
  Etop is NOT blocked — goes direct for lowest latency.
  Free webshare proxy works but is slow (~1.2s per request).
  $2.99/mo paid plan recommended for production.
```

---

# KNOWN BUGS & PROBLEMS

## 1. PS SESSION MANAGER SIMPLIFIED IN NEW MAIN.PY
- New main.py has basic keepalive only, NOT full L1→L2→L3 Playwright recovery
- Old bot has the full recovery — port when going live
- Priority: HIGH

## 2. MATCHING STILL FRAGILE
- Self-healing added in modules/matcher.py (confidence decay → auto-unmatch)
- But evidence.py scoring unchanged, auto_learn DISABLED
- No panel approve/reject, no schedule signal for soccer

## 3. CANCEL UNTESTED IN REAL SCENARIO
- cancel_engine.py has clean logic (mid-based tracking, remain>300 → cancel)
- Needs real-world testing

## 4. IMPORT PATHS NEED VERIFICATION
- New main.py + modules not tested end-to-end yet
- ps3838_auth.py lives in feeds/ (moved from core/auth/)

## 5. PANEL DOWNTIME COUNTER BUG
- Red counter keeps increasing after WS reconnects
- Should reset when ws_connected goes True
- Display bug in panel.py

## 6. BOT NOT YET READING FROM VPS REDIS
- VPS collectors write to Redis, Mac can read (verified)
- Bot still uses local fetchers only
- Need to wire Redis as supplementary data source

## 7. FREE PROXY IS SLOW
- webshare.io free proxy adds ~1.2s per PS request
- WS streaming works fine once connected
- Consider $2.99/mo paid plan for production

## 8. OLD VPS NEEDS DESTROYING
- 45.77.47.73 still exists, wasting money
- Destroy in Vultr dashboard

---

# PROVEN & NEVER TOUCH

1. ML WS index: `ml_data[0]`=AWAY, `ml_data[1]`=HOME (ALL sports)
2. classify_etop_sub: type=3 totalScore>0 = KILLS not Game Winner
3. type=8 offerScore=0: Dota/LoL = Game Winner, CS2 = 10 Kills
4. HDP giving_side: etop offerTeam=1 → team1 gives → store uses negative line
5. no_vig formula in factory.py
6. WS home/away lock: _event_teams write-once prevents flip corruption
7. Session immortality: L1→L2→L3 recovery (proven S24+)
8. Sequential press.do: 75ms gap, parallel = "trygain.later"
9. compute_ev(): THE single EV path — never create a second one
10. ps3838_parse.py index positions: verified across sessions
11. Etop session immortality: Playwright Steam SSO + Protonmail Guard code (6/6 tests pass)
12. WS recover_event: Playwright success → immediate WS reconnect (not 5min sleep)
13. Etop rate limit: ~60 req/min per IP. Safe at 2s interval.
14. Vultr Singapore IPs blocked by Cloudflare for PS — use SOCKS5 proxy

---

# ETOP ↔ PS NAMING DIFFERENCES

| Concept | Etop | PS3838 |
|---------|------|--------|
| Teams | vs1, vs2 (arbitrary) | home, away (WS flips) |
| Names | "NAVI" | "Natus Vincere" |
| Game ID | category.type = "csgo" | League keywords |
| Market IDs | mid | eid + period + mk |
| Odds | Asian (0.52) | Decimal (1.52) |
| Handicap | offerScore + offerTeam | home_hdp (home perspective) |
| Close | remainTime in ms | Markets disappear |
| Domain | etopfun.com | pinnacle888.com (was ps3838.com) |

---

# ACCOUNTS

```
PS3838 (Mac bot):     pinnacle95 / Abcd1234 (ps3838.com)
PS3838 (VPS):         NKUS95 / @Abcdbot12345 (pinnacle888.com)
Etop:                 Steam SSO via etop01proton
Steam:                etop01proton / in secrets.env
Protonmail:           etop01@proton.me / in secrets.env
Webshare proxy:       autoprofitz@gmail.com (free tier)
VPS (Vultr):          207.148.79.2 (root, password in Vultr dashboard)
Old VPS (DESTROY):    45.77.47.73
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
- S29: Etop session immortality (feeds/etop_session.py, 6/6 tests pass)
- S29: Full Steam form fill + Protonmail Guard code grab via Playwright
- S29: WS recover_event fix — immediate reconnect after Playwright (not 5min sleep)
- S29: Applied WS fix to old bot (CleanFlowBot/), verified working
- S30: VPS collectors deployed (207.148.79.2, Vultr Singapore)
- S30: Etop collector: 2s interval, direct, 62ms latency
- S30: PS collector: WS through SOCKS5 proxy (webshare.io Tokyo)
- S30: PS account NKUS95 on VPS, pinnacle888.com URLs
- S30: Redis verified: Mac can read VPS data (redis_reader_test.py)
- S30: Discovered: Vultr IPs Cloudflare-blocked for PS, proxy solves it
- S30: Discovered: Etop rate limit ~60 req/min per IP
