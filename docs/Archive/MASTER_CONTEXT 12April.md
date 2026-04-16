# MASTER CONTEXT — CleanFlowBot
# ANY AI SESSION READS THIS FIRST BEFORE DOING ANYTHING.
# Location: ~/VibeCoding/ProjectBot/CleanFlowBot-clean/docs/MASTER_CONTEXT.md
#
# ══════════════════════════════════════════════════════════════════
# MAINTENANCE RULES — HOW TO KEEP THIS FILE UPDATED
# ══════════════════════════════════════════════════════════════════
#
# After EVERY successful change, the AI MUST update this file:
#
# 1. FILE MAP section:
#    - New file created → add row with file path + description
#    - File deleted → remove row
#    - File moved → update path
#
# 2. KNOWN BUGS section:
#    - Bug fixed → REMOVE it
#    - New bug found → ADD it with description
#
# 3. PROVEN & NEVER TOUCH section:
#    - Fix verified working in production → ADD it
#    - NEVER remove entries from this section
#
# 4. CONFIGURATION section:
#    - Config value changed → update it
#    - New config added → add it
#
# 5. CHANGELOG section (bottom of file):
#    - Add one line: "- SXX: [what changed]"
#
# HOW TO UPDATE:
#    At END of every session, the AI must say:
#    "Updating MASTER_CONTEXT.md with: [list changes]"
#    Then edit the file directly using str_replace or write.
#
# WHAT TO NEVER CHANGE IN THIS FILE:
#    - "WHAT THIS BOT DOES" section (unless core purpose changes)
#    - "ABSOLUTE RULES" section (only ADD rules, never remove)
#    - "ETOP ↔ PS NAMING DIFFERENCES" section
#    - "PROVEN & NEVER TOUCH" section (only ADD, never remove)
#
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
~/VibeCoding/ProjectBot/CleanFlowBot-clean/     ← ACTIVE (clean modular version)
~/VibeCoding/ProjectBot/CleanFlowBot/           ← OLD (monolith, still runs)
```

---

# FILE MAP

## Root
| File | What it does | Status |
|------|-------------|--------|
| `main.py` | Clean orchestrator: boot → pipeline loop (~280 lines) | NEW — replaces 1722-line monolith |
| `panel.py` | Dashboard web server | Unchanged from old bot |
| `config.py` | Loads data/bot_config.json as module vars | Unchanged |
| `container.py` | Single state object (Container + Market + SportConfig) | NEW — all modules read/write here |

## modules/ — Clean separated logic (NEW)
| File | What it does | Reads | Writes |
|------|-------------|-------|--------|
| `classifier.py` | Etop raw data → Market entries | etop parents + listing | container.markets (create + update odds) |
| `matcher.py` | Find PS event for UNMATCHED markets | container.markets + ps_store | container.markets (ps_event_id, names) |
| `valuator.py` | Compute EV, set per-sport phase | container.markets + ps_store | container.markets (ev, phase, state) |
| `fire_engine.py` | Execute press.do on FIRE_ZONE markets | container + listing + ps_store | container.markets (fire state) |
| `cancel_engine.py` | Cancel bets when remain > 300s | container.markets + listing | container.markets (reset fire state) |
| `dashboard.py` | Write dash_state.json from Container only | container | data/dash_state.json |

## core/ — Proven logic (DO NOT REWRITE)
| File | What it does | Touch? |
|------|-------------|--------|
| `ev.py` | `compute_ev()` — THE single EV computation | PROVEN |
| `factory.py` | `StandardStore`, `OddsEntry`, `no_vig` — PS pricing engine | PROVEN |
| `event_store.py` | `TheOnlyStore` — wraps StandardStore + event identity | PROVEN |
| `models.py` | `EtopMarket` dataclass | Stable |
| `math.py` | `no_vig()`, `norm_hdp()` | PROVEN |
| `logger.py` | Structured logging | Stable |
| `fire_db.py` | Fire history SQLite | Stable |
| `pool_estimator.py` | Tuhao pool estimation math | Stable |
| `commands.py` | Panel command handler (IPC) | Stable |
| `session_tracker.py` | Session health tracking | Stable |

## feeds/ — External connections
| File | What it does | Touch? |
|------|-------------|--------|
| `ps3838_ws.py` | WS connection, parse, feed into TheOnlyStore | Battle-tested, 760 lines |
| `ps3838_parse.py` | Raw WS arrays → structured market dicts | PROVEN |
| `ps3838_rest.py` | REST search_event, compact_events | Stable |
| `ps3838_auth.py` | PS auth, cookies, curl_cffi, Playwright recovery | PROVEN |
| `etopfun_api.py` | All etop HTTP calls | Stable |
| `etop_session.py` | Etop session immortality — is_healthy, refresh_login (Steam form fill + Protonmail Guard code), auto_recover | NEW |
| `types.py` | InventoryItem etc | Stable |

## matching/ — Identity resolution
| File | What it does | Touch? |
|------|-------------|--------|
| `evidence.py` | 5-signal scoring (name, opponent, sport, token, history) | Works but fragile |
| `classify.py` | Etop type → market descriptor (ml/hdp/ou/f10k) | PROVEN |
| `alias_db.py` | Self-learning name DB (auto_learn DISABLED) | Needs improvement |
| `pair.py` | `infer_sport_hint()` used, `PairMatcher` legacy | Partial use |
| `aliases.py` | Old alias system | Legacy |

## engine/ — Old firing code (being replaced)
| File | What it does | Touch? |
|------|-------------|--------|
| `fire_zone.py` | Old fire + cancel | REPLACED by modules/ |
| `strategy.py` | Old betting decisions | Partially replaced by SportConfig |
| `inventory.py` | Item management | Still used by fire_engine.py |

## data/ — Runtime state
| File | Git? | Notes |
|------|------|-------|
| `bot_config.json` | YES | All config values |
| `session.json` | NO | Etop cookies |
| `cookie.json` | NO | PS cookies |
| `aliases_learned.json` | NO | Learned aliases |
| `event_map.json` | NO | PS event cache |

## collector/ — VPS standalone fetchers (NEW)
| File | What it does |
|------|-------------|
| `collector_main.py` | VPS entry point — starts etop + PS collectors via asyncio.gather |
| `etop_collector.py` | Polls etop match_list → Redis. Rate limit detection + auto backoff. |
| `ps_collector.py` | Runs Pinnacle888LiveFeed + TheOnlyStore, syncs to Redis. Reads etop:active_sports for adaptive rotation. |
| `redis_config.py` | Redis connection factory + key namespace constants (K.*) |
| `setup_vps.sh` | Fresh Ubuntu VPS install script (Redis + Python deps + dirs) |

## tools/ — Utilities (NEW)
| File | What it does |
|------|-------------|
| `redis_reader_test.py` | Mac-side health check: reads VPS Redis, prints etop/PS status + sample data |

## docs/ — Documentation (always in git)
| File | What it is |
|------|-----------|
| `MASTER_CONTEXT.md` | THIS FILE |
| `SESSION_PROTOCOL.md` | How to start sessions, task template |
| `MODULAR_ARCHITECTURE.md` | Container + modules design |
| `API_BIBLE.md` | Every API endpoint documented |

---

# DATA FLOW

```
Etop API ──→ [ETOP POLLER] ──→ parents[] + listing{}
PS3838 WS ──→ [PS3838LiveFeed] ──→ TheOnlyStore
                        │
          ┌─────────────┼─────────────────────────┐
          ▼             ▼                         ▼
    [CLASSIFIER]   [MATCHER]                [VALUATOR]
    etop → Market  UNMATCHED → MATCHED      MATCHED → EV
          │             │                         │
          └─────────────┼─────────────────────────┘
                        ▼
              ┌────────────────────┐
              ▼                    ▼
        [FIRE ENGINE]      [CANCEL ENGINE]
        FIRE_ZONE→press    fired+300s→cancel
              │                    │
              └────────┬───────────┘
                       ▼
                 [DASHBOARD]
              container → JSON
```

Pipeline in main.py:
```python
classifier.run(container, etop_parents, etop_listing)
matcher.run(container, ps_store, evidence_db)
valuator.run(container, ps_store)
await fire_engine.run(container, ps_store, etop_api, inventory, etop_listing)
dashboard.run(container)
```

---

# PER-SPORT CONFIGURATION

```
esports:    P1=2.0% P2=1.5% P3=1.0%  trigger=90s  max_ps_age=300s
basketball: P1=0.0% P2=0.0% P3=0.0%  trigger=50s  max_ps_age=120s
soccer:     P1=3.0% P2=2.0% P3=1.5%  trigger=50s  max_ps_age=300s
```

---

# KNOWN BUGS & PROBLEMS

## 1. PS SESSION MANAGER SIMPLIFIED
- New main.py has basic keepalive only, NOT full L1→L2→L3 Playwright recovery
- Port from old main.py before going live
- Priority: HIGH

## 2. MATCHING STILL FRAGILE
- Self-healing added (confidence decay → auto-unmatch)
- But evidence.py scoring unchanged, auto_learn DISABLED
- No panel approve/reject, no schedule signal for soccer

## 3. CANCEL UNTESTED
- Clean logic exists (mid-based tracking, remain>300 → cancel)
- Needs real-world testing

## 4. IMPORT PATHS NEED VERIFICATION
- ps3838_auth.py moved to feeds/
- modules/ imports need testing

## 5. PANEL NOT YET CONNECTED TO CONTAINER
- panel.py still reads from old data sources
- Needs to be updated to read container.to_dash_state()

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
10. ps3838_parse.py index positions: verified across multiple sessions

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

---

# CHANGELOG

- S28: Created CleanFlowBot-clean/ with modular structure
- S28: container.py — Container + Market + SportConfig
- S28: 6 modules: classifier, matcher, valuator, fire_engine, cancel_engine, dashboard
- S28: main.py orchestrator (280 lines replaces 1722)
- S28: Per-sport config: NBA=0%, soccer=2%, esports=1-2%
- S28: Matcher self-healing: confidence decay → auto-unmatch
- S28: Cancel engine tracks by mid (not fire_key)
- S28: MASTER_CONTEXT.md maintenance rules added
- S29: Etop session immortality — feeds/etop_session.py + main.py dead-count detection + Playwright auto-recovery
- S29: Full Steam form fill + Protonmail Guard code grab added to refresh_login
- S30: VPS fetcher pair — collector/etop_collector.py (1s poll + rate limit backoff), collector/ps_collector.py (WS→Redis + adaptive sport rotation), collector/collector_main.py, collector/redis_config.py, collector/setup_vps.sh, tools/redis_reader_test.py
