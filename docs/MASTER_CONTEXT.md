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
~/VibeCoding/ProjectBot/CleanFlowBot/           ← OLD (monolith, runs on VPS)
~/VibeCoding/ProjectBot/CleanFlowBot-clean/     ← NEW (modular, LIVE on Mac via VODDS)
```

## VPS Infrastructure
```
VPS: 207.148.112.199 (Vultr Tokyo, 4GB, $20/mo — free credits)
  - Bot running at /opt/bot/ (old monolith deployed)
  - PS account: NKUS95 / @Abcdbot12345 (pinnacle888.com)
  - Etop: direct connection (~62ms)
  - PS: direct connection (no proxy needed from Tokyo)
  - Panel accessible at http://207.148.112.199:8080
```

---

# FILE MAP

## Root (CleanFlowBot-clean/)
| File | What it does | Status |
|------|-------------|--------|
| `main.py` | Orchestrator. DataBus reactive pipeline. SSE server port 8889. FULL/FAST split. Poller pauses during fire_active. | S32 LIVE |
| `panel.py` | Dashboard. SSE primary (EventSource), 5s poll fallback. Bus freshness. Open/Closed filter uses state. | S32 UPGRADED |
| `config.py` | Loads data/bot_config.json. PS_PROVIDER switch. | Updated |
| `container.py` | Market dataclass + Container + SportConfig + to_dash_state. Self-heal fields removed. | S31 CLEANED |

## modules/
| File | What it does | Status |
|------|-------------|--------|
| `classifier.py` | Creates + updates markets from etop data. CLASSIFY_UNKNOWN debug log. | S31 verified |
| `matcher.py` | 1. _lookup_teammates 2. _match_unmatched (structured matcher) 3. _resolve_kills_eids (kills OU re-point to "(Kills)" event). No self-heal. | S33 KILLS OWNER |
| `valuator.py` | EV + ps_fair + ps_age via resolved_eid. TRADEABLE gate → DROPPED. | S31 FIXED |
| `fire_engine.py` | Unified fire stream: tuhao(10s cache) → 100ms → match_list(fresh) → compute_ev → press.do → 200ms. Priority sort. 750ms cooldown. Poller paused during fire. Kills fallbacks removed (now in matcher). | S33 CLEANED |
| `cancel_engine.py` | Tracks by mid, independent 5s loop. Diagnostic logging for every fired market. | S32 UPGRADED |
| `dashboard.py` | Writes dash_state.json on background thread. SSE is primary panel path. | S32 UPGRADED |
| `classifier.py` | Creates + updates markets. CLOSED → m.remain=0 at source (3 locations). | S32 FIXED |
| `valuator.py` | EV + ps_fair + ps_age via resolved_eid. Alt_eids loop + try/finally. Kills logic removed (now in matcher). | S33 CLEANED |

## matching/
| File | What it does | Status |
|------|-------------|--------|
| `structured_matcher.py` | Filter-first matching + unique opponent promotion (SUGGEST→AUTO when one side 80+ and unique). | S32 UPGRADED |
| `league_map.py` | PS prefix parser, deterministic game_type | Stable |
| `alias_db.py` | Game_type keyed aliases, 108+ seeds. YeS → Yellow Submarine added. | S32 UPDATED |
| `classify.py` | Etop type→market. _RACE_DEFAULTS={'cs2':5}. SYNCED both bots. | S31 FIXED |
| `pair.py` | infer_sport_hint | Stable |
| `evidence.py` | **DEAD** — replaced by structured_matcher |
| `aliases.py` | **DEAD** — replaced by alias_db |
| `index.py` | **DEAD** |
| `line_new.py` | **DEAD** |

## core/
| File | What it does | Status |
|------|-------------|--------|
| `ev.py` | compute_ev — THE single EV path. ml/hdp/ou/team_total only. | PROVEN |
| `factory.py` | StandardStore, OddsEntry, get_line_age | PROVEN |
| `event_store.py` | TheOnlyStore | PROVEN |
| `models.py` | EtopMarket dataclass | Stable |
| `math.py` | no_vig, norm_hdp | PROVEN |
| `logger.py` | Structured logging. Non-blocking via queue + background thread. | S32 UPGRADED |
| `fire_db.py` | Fire history SQLite (not wired yet) | Stable |
| `pool_estimator.py` | Tuhao pool estimation. No persistent cache. 10s refresh per market in fire_engine. | S32 REWRITTEN |
| `data_bus.py` | DataBus — reactive event coordination. notify()/wait() with dirty set. | S32 PROVEN |
| `session_tracker.py` | Session health tracking | LIVE |
| `commands.py` | **DEAD** — only in old monolith |

## feeds/
| File | What it does | Status |
|------|-------------|--------|
| `ps_auth.py` | UNIFIED auth. ONE class, all providers. _CurlSession. set_domain(). | S31 UNIFIED |
| `ps3838_auth.py` | _CurlSession wrappers. set_domain() added. | PROVEN |
| `ps3838_ws.py` | WS feed. Uses config.PS_WS_URL (module ref). | S31 FIXED |
| `ps3838_parse.py` | WS parser | PROVEN |
| `ps3838_rest.py` | REST endpoints. Uses config.PS_BASE_URL (module ref). | S31 VERIFIED |
| `etopfun_api.py` | Etop HTTP calls. Tuhao 429 fallback (_tuhao_last returns cached on rate limit). | S32 UPGRADED |
| `etop_session.py` | Etop session immortality | S31 TESTED 3/3 |
| `types.py` | InventoryItem etc | Stable |
| `vodds_auth.py` | **DEAD** — merged into ps_auth.py |
| `vodds_auth_backup.py` | **DEAD** |
| `ps_auth_backup.py` | **DEAD** |

## engine/
| File | What it does | Status |
|------|-------------|--------|
| `inventory.py` | Item management. Loaded at startup + every 60s. | LIVE |
| `fire_zone.py` | **DEAD** — replaced by modules/ |
| `strategy.py` | **DEAD** — replaced by SportConfig |

## collector/ — SharpEdge project, **ALL DEAD** for bot

## tools/
| File | What it does | Status |
|------|-------------|--------|
| `bus_diagnostic.py` | Live DataBus health monitor — rate, freshness, EV stability | S32 NEW |
| `test_fire_spacing.py` | Standalone fire timing test — tuhao+list+press with measured gaps | S32 NEW |
| `test_kills_ev.py` | Kills eid diagnostic — verifies PS names vs find_event_id | S32 NEW |
| `test_kills_resolve.py` | Offline test harness for matcher._resolve_kills_eids — 11 cases, 15 assertions (swap, idempotency, state filters, edge cases) | S33 NEW |

---

# DATA FLOW

```
Pipeline (reactive — wakes on ANY source via DataBus):
  bus.wait()       → wakes on etop OR ps3838 (or future sources)

  IF etop triggered (FULL path):
    classifier     → create + update markets
    matcher        → teammate lookup + structured matcher + unique opponent promotion

  ALWAYS (FAST path):
    valuator       → EV + ps_fair + ps_age (kills eid via _event_teams PS names)
    fire_engine    → unified stream per candidate:
                     tuhao(10s cache) → 100ms → match_list(fresh) → compute_ev → press.do → 200ms
                     Poller PAUSED during fire (fire_active flag)
    dashboard      → dash_state.json (background thread)
    _push_sse()    → real-time push to panel (port 8889)

  Background:
    cancel_engine  → 5s loop, diagnostic logging for fired markets
    discovery      → 10s REST search for unmatched
    etop_poller    → 5s poll (pauses when fire_active=True), bus.notify('etop')
    ps_ws          → streaming, bus.notify('ps3838') per message
    ps_session     → 10s keepalive, L1→L2→L3
    etop_keepalive → 5min userconn_check
    SSE server     → aiohttp port 8889 (/sse)

States:
  UNMATCHED → MATCHED → MONITOR → FIRE_ZONE → CLOSED
                     ↘ DROPPED (untradeable)

Config URL flow:
  ps_auth._ps_base = correct URL (set by login)
  main.py patches config ONCE after init (for ps3838_ws.py)
  session_loop reads ps_auth._ps_base (never stale)
  NEVER: from config import PS_BASE_URL
```

---

# MATCHING

```
Every cycle:
  1. _lookup_teammates: same team + same map matched → copy eid
  2. _match_unmatched: structured matcher for new markets

No self-heal. No auto-unmatch. No rejected_eids.
Once matched, stays matched. Valuator retries compute_ev every cycle.
```

---

# CLASSIFICATION (classify.py)

```
type=2: series ML/HDP
type=3: map>0 + totalScore>0 = Kills. totalScore=0 = Game Winner.
type=5: OU
type=6: Rounds. totalScore present = "First to win N rounds". Absent = use _RACE_DEFAULTS.
        _RACE_DEFAULTS = {'cs2': 5}
type=8: offerScore=0 = Game Winner (ALL games). offerScore>0 = HDP map.
type=9: Duration OU
type=11: 5 Kills (LoL)
type=13: Total Kills OU (market='ou')

TRADEABLE: ml, hdp, ou, team_total
UNTRADEABLE → DROPPED: f10k, f5k, duration, first_rounds, race, unknown_*
```

---

# VALUATOR — resolved_eid

```
compute_ev(regular_eid) → None → tries alt_eid → tries kills_eid
resolved_eid = whichever eid produced EV
_write_ps_odds(resolved_eid) → correct ps_fair
get_line_age(resolved_eid) → correct ps_age
fire_engine reads m.ps_age → gate passes
```

---

# AUTH (feeds/ps_auth.py)

```
ONE file. ONE class. _CurlSession for everything.
  PSAuth("vodds")       → _login_vodds()      = curl_cffi (instant recovery)
  PSAuth("ps3838")      → _login_playwright()  = Playwright
  PSAuth("pinnacle888") → _login_playwright()  = Playwright

set_domain() before load_cookies() — correct whitelabel domain.
ONE session — no _raw_session. All cookies in one jar.
Recovery: _login_vodds() on 403 — instant, no Playwright.

_CurlSession interface:
  async with session.get(url) as resp: → resp.status
  NEVER: resp = await session.get() → resp.status_code
```

---

# PER-SPORT CONFIGURATION

```
esports:    P1=10.0% P2=5.0% P3=1.0%  trigger=90s  max_ps_age=180s
basketball: P1=0.0% P2=0.0% P3=0.0%  trigger=50s  max_ps_age=120s
soccer:     P1=3.0% P2=2.0% P3=1.5%  trigger=50s  max_ps_age=300s
```

# FIRE TIMING

```
FIRE_SAME_MKT_COOLDOWN_MS = 750    # ms between fires on SAME market
TUHAO_REFRESH_SECS = 10            # seconds between tuhao API calls per market
Fire loop gaps: 100ms after tuhao, 200ms after press.do
```

# PORTS

```
8888 = panel.py (HTML, logs, config, commands)
8889 = SSE server in main.py (real-time market push)
```

---

# ACCOUNTS

```
Mac bot:    VODDS provider (PS_PROVIDER=vodds in bot_config.json)
VPS:        NKUS95 / @Abcdbot12345 (pinnacle888.com)
VODDS:      usdzc2861736
Etop:       Steam SSO via etop01proton
BANNED:     rans95 (pinnacle888.com) — S31 config patching bug
```

---

# KNOWN BUGS

1. Cancel untested in real scenario — MEDIUM
2. Panel downtime counter doesn't reset — LOW
4. fire_db not wired — LOW
5. 16+ dead files need cleanup — LOW
6. Session survival unproven on new bot (need 4+ hour clean run) — HIGH
7. Inventory must load before pipeline starts — startup race causes "no item found" (S32 observed)
8. Failed fire (trygain.later) doesn't set cooldown — can cause rapid retry burst
9. classify.py type=6 totalScore>16 misclassified as race (should be OU rounds) — cosmetic, no fire risk
11. Panel fired count disappears on CLOSED — display bug
12. Silent continues in fire_engine.py candidates loop and fire loop — identified 8 silent continues. S32 fixed 2 FIRE_SKIP conditional logs (line 172 and line 388). Remaining 6 silent continues are filters (state, ps_event_id, sub missing, cooldown) but should be audited. Line numbers shifted after S33 P1c/P1d — re-grep before audit. S33 Priority 3 target.

---

# PROVEN & NEVER TOUCH

1. ML WS index: ml_data[0]=AWAY, ml_data[1]=HOME
2. type=3 totalScore>0 = KILLS not Game Winner
3. type=8 offerScore=0 = Game Winner (ALL games including CS2)
4. HDP giving_side: offerTeam=1 → team1 gives → negative line
5. no_vig formula in factory.py
6. WS home/away lock: write-once prevents flip
7. Session immortality: L1→L2→L3 with RAW HTTP
8. Sequential press.do: 75ms gap
9. compute_ev(): THE single EV path
10. ps3838_parse.py index positions
11. Etop session immortality: Steam SSO + Protonmail Guard (3/3)
12. WS recover_event: immediate reconnect
13. Etop rate limit: ~60 req/min, safe at 2s
14. Singapore Vultr blocked — use Tokyo
15. curl_cffi PS login works from Tokyo VPS
16. Kills OU: resolved_eid tracks correct eid through valuator
17. Structured matcher: geometric mean, 33/33 live, 0 false positives
18. Alias DB: game_type keys, not sport keys
19. PS league parser: prefix = game (esports) or country (traditional)
20. PS keepalive: RAW HTTP via ps_auth._session (async with + resp.status)
21. Cookie save: only when healthy, throttle 5min
22. VODDS: ONE session, set_domain(), pure curl_cffi
23. NEVER `from config import PS_BASE_URL` — stale copy BANNED rans95
24. _CurlSession: async with + resp.status. NEVER await + resp.status_code
25. type=6 = rounds (CS2 only). _RACE_DEFAULTS = {'cs2': 5}
26. Teammate lookup: same team + same map = same eid (every cycle)
27. DROPPED: untradeable markets stay matched, never unmatch
28. fire_engine reads m.ps_age from valuator (no duplicate lookup)
29. resolved_eid: passes to _write_ps_odds + get_line_age
30. classify.py MUST be identical between old + clean bot
31. DataBus: bus.notify() → bus.wait(). Single asyncio.Event + dirty set. ONLY trigger for pipeline.
32. Pipeline split: FULL (etop) = classify+match+valuate+fire. FAST (ps-only) = valuate+fire.
33. SSE push: aiohttp port 8889. _push_sse() after every pipeline cycle. Panel uses EventSource.
34. Non-blocking logger: _emit() → queue.put(). Background thread writes to stdout + file.
35. Non-blocking dashboard: json.dumps on event loop, file write on background thread.
36. Fire order: tuhao → 100ms → match_list(fresh) → compute_ev(sync) → gates(sync) → press.do → 200ms. ZERO await between compute_ev and press.do.
37. Priority: urgency × EV × cap_factor. Urgency grouping ±2s. From old strategy.py.
38. Per-market cooldown: FIRE_SAME_MKT_COOLDOWN_MS (default 750ms).
39. Tuhao 10s cache: m.last_tuhao_at timestamp. Skip if < TUHAO_REFRESH_SECS. Log only on change (TUHAO_UPDATE).
40. Tuhao 429 fallback: _tuhao_last in etopfun_api. On 429, returns last good result, never empty.
41. Poller pauses during fire: container.fire_active flag. Poller checks before match_list.
42. Pool gate: raw_pool >= MIN_RAW_POOL checked AFTER tuhao in fire loop (not in candidate building).
43. CLOSED → m.remain = 0. Set at source in classifier.py (3 locations). Panel reads same truth.
44. Panel Open/Closed filter: uses m.st (state), not m.s (remain).
45. Unique opponent promotion: SUGGEST + one side 80+ + unique in candidates → AUTO_MATCH. 5 safety checks.
46. Kills eid: uses PS names from ps_store._event_teams[eid], NOT em.ps_name_team1. Checks both orderings.
47. Inventory check at top of fire_engine.run() — if no items, return immediately.
48. Fire spacing test: 40/40 fires, zero 429s at 329 calls/min (proven with test_fire_spacing.py).
49. FIRE_SKIP logging must NEVER be conditional on EV threshold. `if best_ev > cfg.MIN_EV: log` hides EV-too-low skips — cost 2 days of kills OU debugging. ALWAYS log every gate failure.
50. Kills OU markets have thin edge (3-5% typical). P3 threshold=1% catches them. P2=5% and P1=10% will skip most kills OU.
51. Total Kills OU verified firing at P3 after S32 fixes (kills eid from _event_teams + always-log FIRE_SKIP).
52. Kills eid resolution lives ONLY in matcher._resolve_kills_eids. Never add kills fallback to valuator, fire_engine, core/ev.py, or anywhere else. Uses find_event_id_any (kills events live in _events, not _event_teams). Proven by tools/test_kills_resolve.py (15/15) and S33 live fire.

---

# ABSOLUTE RULES

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
13. NEVER restart the bot without explicit permission
14. NEVER overwrite logs — check first, report, then ask
15. PS keepalive = RAW HTTP. ps_auth.keep_alive() DOES NOT EXIST.
16. All Claude Code instructions as downloadable .md files
17. NEVER `from config import PS_BASE_URL` — module ref or ps_auth._ps_base
18. NEVER await + resp.status_code with _CurlSession
19. classify.py changes MUST apply to BOTH bots simultaneously
20. DataBus is the ONLY pipeline trigger. NEVER bypass bus.wait() with brain_event or etop_event.
21. NEVER add await between compute_ev and press.do in fire loop — breaks freshness guarantee.
22. Tuhao uses 10s cache (TUHAO_REFRESH_SECS). NOT every fire. pool_estimator has no persistent cache.
23. Kills eid: ALWAYS use ps_store._event_teams[eid] for PS names. NEVER use em.ps_name_team1 + " (Kills)".
24. classify.py changes MUST be verified: type=6 totalScore field can be OU line, not race target.
25. NEVER add conditional logging to skip paths. Every `continue` gets a log line immediately above it, or make it log_debug. Silent skips hide bugs for days.
26. When fixing a bug in fire_engine.py, CHECK BOTH candidate building (~line 100-180) AND fire loop (~line 240-400). They have duplicated logic.
27. Before any fire_engine edit, run: `grep -n "continue" modules/fire_engine.py` to see all exit points. Log every one.

---

# CHANGELOG

- S28: Created CleanFlowBot-clean/ modular structure
- S28: container.py, 6 modules, main.py orchestrator
- S29: Etop session immortality (3/3 tests)
- S30: VPS deployed Tokyo, Singapore blocked
- S30: curl_cffi PS login from VPS, structured matcher designed
- S31: structured_matcher.py (47/47 tests, 33/33 live)
- S31: All modules verified + wired
- S31: VODDS pure curl_cffi auth
- S31: Bot live on Mac via VODDS
- S31 (cont): Unified ps_auth.py — ONE file, _CurlSession, set_domain()
- S31 (cont): ONE session for vodds login (deleted _raw_session → fixed wstoken 403)
- S31 (cont): Config patching FIXED — module refs everywhere, ps_auth._ps_base in session loop
- S31 (cont): Removed hardcoded refresh_ps3838.py fallback from main.py
- S31 (cont): rans95 BANNED — config patching caused pinnacle888 hammering
- S31 (cont): Teammate lookup in matcher (_lookup_teammates every cycle)
- S31 (cont): DROPPED state for untradeable markets
- S31 (cont): Self-heal REMOVED (no auto-unmatch, no ev_none_cycles, no rejected_eids)
- S31 (cont): resolved_eid in valuator (ps_fair + ps_age use correct eid)
- S31 (cont): fire_engine uses m.ps_age from valuator (no duplicate lookup)
- S31 (cont): type=6 = First to win N rounds (_RACE_DEFAULTS={'cs2':5})
- S31 (cont): type=8 CS2 = Game Winner (was wrongly 10 Kills)
- S31 (cont): classify.py synced between both bots
- S31 (cont): CLASSIFY_UNKNOWN debug logging
- S31 (cont): Inventory loaded at startup
- S31 (cont): Full architecture audit — 16+ dead files identified
- S32: DataBus reactive architecture — pipeline wakes on ANY source
- S32: core/data_bus.py — notify()/wait() with dirty set tracking
- S32: Pipeline split: FULL (etop) vs FAST (PS-only, skips classify+match)
- S32: SSE server port 8889 — real-time push to panel
- S32: Panel uses EventSource, 5s poll fallback
- S32: Non-blocking logger — queue + background thread
- S32: Non-blocking dashboard + event_map — background threads
- S32: Priority fire — urgency × EV × cap_factor, urgency grouping ±2s
- S32: Per-market cooldown — FIRE_SAME_MKT_COOLDOWN_MS from panel
- S32: Inline tuhao — fresh pool before every press.do, no cache
- S32: Fire order: tuhao(120ms) → compute_ev(sync) → gates(sync) → press.do
- S32: Pool gate moved to after inline tuhao in fire loop
- S32: Background tuhao loop removed
- S32: pool_estimator simplified — no cache
- S32: Cancel engine diagnostic logging
- S32: Etop poll 3s → 1.25s
- S32: 80 bets fired Suns vs Trail Blazers — full flow verified live
- S32: CLOSED lifecycle fix — m.remain=0 at source (3 locations in classifier.py)
- S32: Panel Open/Closed filter uses state not remain time
- S32: Unique opponent promotion in structured_matcher — SUGGEST→AUTO_MATCH when one side 80+ and unique
- S32: YeS → Yellow Submarine alias added (dota2)
- S32: Gen.G, Hanwha Life auto-promoted via unique opponent (previously stuck SUGGEST)
- S32: Inventory early-exit in fire_engine — no items = immediate return
- S32: Unified fire stream — tuhao → match_list → compute_ev → press per market
- S32: fire_active flag on container — poller backs off during fire (0.5s retry)
- S32: Etop poller interval 1s → 5s (no need for aggressive polling)
- S32: match_list called fresh per market in fire loop — guaranteed fresh odds at fire time
- S32: 100ms sleep after press.do (rate control)
- S32: Pool + cap gate after tuhao in fire loop (early skip if pool too small or cap full)
- S32: FIRE_EV_NONE log — shows eid/team names when compute_ev returns None in fire loop
- S32: TUHAO_LIVE log — pool/cap/remain/fired_so_far after every tuhao call
- S32: remain= added to FIRE_SKIP log
- S32: FIRE_READY log shows list=Xms freshness
- S32: FIRE_CYCLE log shows mode=UNIFIED
- S32: Panel: SSE-only market data (poll no longer updates lastMkts — eliminates stale-data flicker)
- S32: Panel: League column added (beside Market column)
- S32: Panel: PS/ETOP freshness badges renamed + color coded (green ≤2s, yellow 2-5s, red >5s)
- S32: Panel: Redundant "Xs ago" data age badge removed
- S32: Config: MAX_POOL_IMPACT 0.075 → 0.05, MAX_ODDS 5.0 → 3.5
- S32: matcher.py: m.league updated on match (from ev_info)
- S32: container.py: lg field added to to_dash_state()
- S32: Tuhao 10s cache — m.last_tuhao_at, TUHAO_REFRESH_SECS=10
- S32: Tuhao 429 fallback — _tuhao_last returns cached data on rate limit
- S32: Poller pauses during fire — container.fire_active flag
- S32: Kills eid fix — use ps_store._event_teams PS names, not etop names
- S32: FIRE_SAME_MKT_COOLDOWN_MS 500 → 750ms
- S32: EV thresholds: P1=10% P2=5% P3=1% (esports)
- S32: max_ps_age wired from config.MAX_PS_AGE (was hardcoded 300)
- S32: Cancel engine: CANCEL_DONE log, resets last_tuhao_at, skips CLOSED markets
- S32: Cancel engine: REOPEN check — CLOSED market extended → reset to MONITOR
- S32: Press raw response logging (PRESS_RAW)
- S32: Fire spacing test — 40/40, zero 429s at 329/min (test_fire_spacing.py)
- S32: 80 bets Suns (NBA), 40 bets CBA basketball — verified live
- S32: Gen.G + Hanwha Life auto-promoted via unique opponent
- S32 (final): Fixed silent FIRE_SKIP in fire_engine fire loop (line 388) — removed `if best_ev > cfg.MIN_EV` condition, always log
- S32 (final): Total Kills OU fires verified at P3 with EV>1% after kills eid + always-log fixes
- S32 (final): Identified duplicated logic in fire_engine (candidates vs fire loop) — S33 refactor target
- S32 (final): Fire spacing test proved 329/min safe, zero 429s
- S32 (final): test_fire_spacing.py, test_kills_ev.py, bus_diagnostic.py in tools/
- S33: P1 consolidation — kills OU eid resolution moved to matcher.py as single owner
- S33: matcher._resolve_kills_eids — runs every cycle after _match_unmatched, idempotent, uses find_event_id_any for _events lookup
- S33: tools/test_kills_resolve.py — 15/15 offline tests proving swap, idempotency, state filters, edge cases
- S33: valuator.py kills fallback deleted (commit 2af49cc)
- S33: fire_engine.py candidate kills fallback deleted (commit 3afed4b)
- S33: fire_engine.py fire loop kills fallback deleted (commit ff1a72a)
- S33: Bugs #10 and #13 closed (kills OU matching root-fix, duplication eliminated)
