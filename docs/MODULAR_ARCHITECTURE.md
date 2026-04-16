# CleanFlowBot — Modular Redesign
# One document to rule them all. Every future Claude Code session reads this FIRST.

---

# THE PROBLEM

Everything is tangled in main.py (1722 lines). Brain does matching AND
classification AND state management AND dashboard writing. Fire reads
from 3 different places. Dashboard pulls from live_feed, fire_zone,
inventory, etop_state, markets dict, listing — all separately.
Every fix breaks something else. Every new AI session loses context.

# THE SOLUTION

Split into 8 independent modules. Each module:
- Is ONE file
- Has ONE job
- Reads from ONE place (the Container)
- Writes to ONE section of the Container
- Cannot import from other modules (only from Container + shared libs)
- Can be restarted without affecting other modules

The Container is the SINGLE shared state object. Dashboard reads ONLY
from Container. No module reaches into another module's internals.

---

# THE CONTAINER

```python
# container.py — THE single source of truth
# Every module reads from here. Every module writes to its own section.
# Dashboard reads from here and NOWHERE ELSE.

class Container:
    """Shared state for the entire bot. Single object, clear ownership."""

    def __init__(self):

        # ══════════════════════════════════════════════════════
        # SECTION 1: RAW DATA (written by Collectors)
        # ══════════════════════════════════════════════════════

        self.etop = EtopData()
        self.ps = PSData()

        # ══════════════════════════════════════════════════════
        # SECTION 2: MARKETS (written by Classifier + Matcher)
        # ══════════════════════════════════════════════════════

        self.markets: Dict[str, Market] = {}
        # key = mid (etop sub-match ID)
        # Classifier creates entries
        # Matcher updates match fields
        # Valuator updates ev fields
        # Fire Engine updates fire fields

        # ══════════════════════════════════════════════════════
        # SECTION 3: INVENTORY (written by Inventory module)
        # ══════════════════════════════════════════════════════

        self.inventory = InventoryData()

        # ══════════════════════════════════════════════════════
        # SECTION 4: HEALTH (written by all modules)
        # ══════════════════════════════════════════════════════

        self.health = HealthData()

        # ══════════════════════════════════════════════════════
        # SECTION 5: CONFIG (loaded at startup, hot-reloadable)
        # ══════════════════════════════════════════════════════

        self.config = BotConfig()


class EtopData:
    """Raw etop state. Written ONLY by Etop Collector."""
    parents: list = []           # raw parent list from match_list
    listing: dict = {}           # {mid: {o1, o2, remain, can_press, cancel_code}}
    last_fetch: float = 0        # timestamp of last successful fetch
    fetch_count: int = 0

    @property
    def age(self) -> float:
        return time.time() - self.last_fetch if self.last_fetch > 0 else 9999


class PSData:
    """PS3838 state. Written ONLY by PS Collector."""
    store: TheOnlyStore          # the proven pricing engine (unchanged)
    ws_connected: bool = False
    session_alive: bool = True
    last_ws_msg: float = 0


class Market:
    """Everything about one market. ALL in one place.

    Dashboard reads THIS. Not fire_zone._fire_state. Not etop_state.listing.
    Not live_feed.standard_store. THIS.
    """

    # ── Identity (set by Classifier, never changes) ──
    mid: str                           # etop sub-match ID
    fire_key: str                      # dedup key
    team1: str                         # etop team names
    team2: str
    market_type: str                   # ml, hdp, ou, f10k, etc.
    line: float                        # absolute line value
    map_num: int
    label: str                         # human readable
    giving_side: str                   # team1 or team2 or None
    game: str                          # cs2, dota, lol, valorant, soccer, basketball
    sport: str                         # esports, soccer, basketball
    league: str
    cat_type: str                      # raw etopfun category type
    parent_id: str

    # ── Live Etop Odds (updated every cycle from listing) ──
    o1: float = 0                      # current etop odds team1
    o2: float = 0                      # current etop odds team2
    remain: float = 0                  # seconds remaining
    can_press: bool = False
    cancel_code: str = None
    etop_age: float = 0                # how old is this etop data

    # ── PS Match (set by Matcher) ──
    ps_event_id: int = None
    ps_name_team1: str = None          # PS name for etop team1
    ps_name_team2: str = None          # PS name for etop team2
    match_confidence: float = 0        # evidence match score
    match_method: str = ''             # how it was matched
    match_locked: bool = False         # human-approved, don't rematch
    match_rejected_eids: list = []     # eids that were wrong (negative alias)

    # ── Valuation (set by Valuator every cycle) ──
    ev1: float = None                  # EV% for team1/over side
    ev2: float = None                  # EV% for team2/under side
    best_ev: float = None              # max(ev1, ev2)
    best_side: int = 0                 # 1 or 2
    ps_fair_1: float = None            # no-vig fair odds side 1
    ps_fair_2: float = None            # no-vig fair odds side 2
    ps_age: float = None               # seconds since PS line updated
    ps_raw_1: float = None             # raw PS odds side 1
    ps_raw_2: float = None             # raw PS odds side 2

    # ── Fire State (set by Fire Engine) ──
    fire_state: str = 'IDLE'           # IDLE, APPROACHING, PREFIRE, FIRE_ZONE, FIRED, LOCKED
    phase: str = ''                    # P1, P2, P3
    phase_min_ev: float = 0
    total_fired: int = 0
    total_value: float = 0
    value_cap: float = 0              # from TUHAO or HARD_CAP
    cap_source: str = ''              # TUHAO or HARD_CAP
    remaining_cap: float = 0
    locked_side: int = 0              # 0=none, 1=team1, 2=team2
    raw_pool: float = 0
    first_fire_at: float = 0
    last_fire_at: float = 0
    consumed_item_ids: list = []
    press_ids: list = []              # for cancel

    # ── Lifecycle ──
    state: str = 'UNMATCHED'          # UNMATCHED, MATCHED, MONITOR, APPROACHING,
                                      # PREFIRE, FIRE_ZONE, FIRED, CLOSED
    created_at: float = 0
    last_seen: float = 0              # last time appeared in listing
    dead_at: float = 0                # when it disappeared from listing
    locked_at: float = 0              # when state became CLOSED

    # ── Gate Failures (for dashboard + debugging) ──
    last_gate_failures: list = []     # ["ev=+0.5%<P1:2.0%", "ps_stale=450s"]


class BotConfig:
    """Per-sport, per-phase configuration. Hot-reloadable from bot_config.json.

    THIS is where "NBA min_ev=0%, soccer min_ev=2%" lives.
    """

    # ── Global defaults ──
    trigger_secs: float = 90          # remain ≤ this → FIRE_ZONE
    hard_cap: float = 100
    max_items: int = 40
    max_etop_age: float = 5.0
    max_ps_age: float = 300
    fire_queue_gap_ms: int = 75
    dry_run: bool = True

    # ── Per-sport overrides ──
    # Each sport has its own EV thresholds per phase
    sport_config: Dict[str, SportConfig] = {
        'esports': SportConfig(
            phase1_ev=2.0,    # 90-60s: conservative
            phase2_ev=1.5,    # 60-30s: medium
            phase3_ev=1.0,    # 30-0s:  aggressive (pool is set)
            trigger_secs=90,
            max_ps_age=300,
        ),
        'basketball': SportConfig(
            phase1_ev=0.0,    # NBA: volume + rebate strategy
            phase2_ev=0.0,
            phase3_ev=0.0,
            trigger_secs=50,
            max_ps_age=120,   # NBA lines move fast, tighter freshness
        ),
        'soccer': SportConfig(
            phase1_ev=3.0,    # Soccer: wider margin for safety
            phase2_ev=2.0,
            phase3_ev=1.5,
            trigger_secs=50,
            max_ps_age=300,
        ),
    }

    def get_sport_config(self, sport: str) -> SportConfig:
        return self.sport_config.get(sport, self.sport_config['esports'])


class SportConfig:
    phase1_ev: float         # remain > 2/3 of trigger
    phase2_ev: float         # remain > 1/3 of trigger
    phase3_ev: float         # remain ≤ 1/3 of trigger
    trigger_secs: float
    max_ps_age: float
```

---

# THE 8 MODULES

Each module is described with:
- **READS:** what it looks at
- **WRITES:** what it updates
- **DOES NOT:** what it must never touch
- **SELF-HEALING:** how it recovers from problems

---

## MODULE 1: ETOP COLLECTOR
**File:** `collectors/etop_collector.py`
**Cadence:** every 3s

```
READS:  etopfun API (match_list.do)
WRITES: container.etop.parents
        container.etop.listing
        container.etop.last_fetch
        container.health.etop_collector

DOES NOT:
  - Classify markets (Module 3's job)
  - Know what EV is
  - Know what PS is
  - Touch container.markets

SELF-HEALING:
  - Cookie expiry → attempt re-login
  - HTTP timeout → exponential backoff (3s → 6s → 12s → 30s cap)
  - Returns empty → log warning, keep last known data
  - Keepalive: userconn_check every 5min
```

---

## MODULE 2: PS COLLECTOR
**File:** `collectors/ps_collector.py`
**Cadence:** event-driven (WS streaming)

```
READS:  PS3838 WebSocket (FULL_ODDS, UPDATE_ODDS, LEFT_MENU)
WRITES: container.ps.store (TheOnlyStore — unchanged proven code)
        container.ps.ws_connected
        container.ps.session_alive
        container.health.ps_collector

DOES NOT:
  - Compute EV (Module 5's job)
  - Match events (Module 4's job)
  - Touch container.markets
  - Call etop API

SELF-HEALING:
  - WS disconnect → reconnect with backoff (12s, 30s, 60s)
  - Token 403 → reload cookies → Playwright (L1→L2→L3)
  - 3 failures → REST-only mode for 5min
  - Session death → full recovery cascade
  (all existing session_manager logic, unchanged)
```

---

## MODULE 3: CLASSIFIER
**File:** `modules/classifier.py`
**Cadence:** runs after every etop fetch (event-driven)

```
READS:  container.etop.parents (raw parent list)
        container.etop.listing (fresh odds)

WRITES: container.markets[mid] — CREATES new Market entries
        container.markets[mid].o1/o2/remain/can_press — UPDATES live odds
        container.markets[mid].state — lifecycle transitions

DOES NOT:
  - Match to PS (Module 4's job)
  - Compute EV (Module 5's job)
  - Fire bets (Module 6's job)
  - Delete markets (only marks CLOSED)

LOGIC:
  for each parent in container.etop.parents:
    for each sub in parent.sublist:
      1. classify_etop_sub() → market descriptor (existing code, unchanged)
      2. If new mid → create Market entry in container.markets
      3. If existing mid → update o1/o2/remain/can_press from listing
      4. Handle death/close/return transitions

  EXACTLY what brain STEP 1 + STEP 3 does today.
  But ONLY classification + odds update + lifecycle.
  No matching. No EV. No dashboard.
```

---

## MODULE 4: MATCHER
**File:** `modules/matcher.py`
**Cadence:** runs after Classifier (event-driven)

```
READS:  container.markets where state == UNMATCHED
        container.ps.store.get_events_for_matching()

WRITES: container.markets[mid].ps_event_id
        container.markets[mid].ps_name_team1/2
        container.markets[mid].match_confidence
        container.markets[mid].match_method
        container.markets[mid].state → MATCHED

DOES NOT:
  - Classify markets (Module 3's job)
  - Compute EV (Module 5's job)
  - Fire bets (Module 6's job)
  - Rebuild EtopMarket objects

LOGIC:
  1. Group UNMATCHED markets by parent (team1|team2)
  2. For each group:
     a. evidence_match() — existing code, unchanged
     b. game_type_gate — existing code, unchanged
     c. verify: pair score ≥ 120
     d. EID conflict resolution — existing code, unchanged
     e. Set ps_event_id + ps_name_team1/2 on all markets in group

SELF-HEALING (NEW — the core improvement):

  A. CONFIDENCE DECAY
     Every matched market tracks match_confidence.
     If EV is consistently None for >5 minutes after matching:
       → match_confidence *= 0.8 each minute
       → When confidence < 30: UNMATCH (reset ps_event_id to None)
       → Market re-enters UNMATCHED pool for fresh matching
     WHY: wrong match → no PS line → EV stays None → auto-corrects

  B. NEGATIVE ALIASES
     container.markets[mid].match_rejected_eids = [eid1, eid2, ...]
     When human rejects (panel button) or auto-unmatch triggers:
       → Add eid to rejected list
       → Matcher skips rejected eids for this team pair
       → Persists to disk (negative_aliases.json)
     WHY: wrong match keeps returning → blocked forever

  C. CROSS-VALIDATION
     After matching, check: does compute_ev return non-None?
       → Yes: match is probably correct (PS has lines for this eid+market)
       → No for >60s: flag as SUSPECT, log warning
       → No for >300s: auto-unmatch, add to rejected
     WHY: humans never get confused because they see the odds.
           If there are no odds, the match was wrong.

  D. ALIAS LEARNING
     When match fires successfully AND wins:
       → Boost alias confidence
       → Store exact name mapping: "BBL" → "BBL Esports"
     When match fires but was wrong eid:
       → Negative alias stored permanently

  E. DISCOVERY FALLBACK (existing, unchanged)
     REST search_event for UNMATCHED markets.
     Budget: 3 REST calls per 10s cycle.
     Throttle: 5min per team pair.
```

---

## MODULE 5: VALUATOR
**File:** `modules/valuator.py`
**Cadence:** runs after Matcher (event-driven)

```
READS:  container.markets where state >= MATCHED
        container.ps.store (fair odds)
        container.config.sport_config (per-sport EV thresholds)

WRITES: container.markets[mid].ev1/ev2/best_ev/best_side
        container.markets[mid].ps_fair_1/ps_fair_2
        container.markets[mid].ps_age
        container.markets[mid].ps_raw_1/ps_raw_2
        container.markets[mid].state → MONITOR (when EV computable)
        container.markets[mid].phase/phase_min_ev

DOES NOT:
  - Match events (Module 4's job)
  - Fire bets (Module 6's job)
  - Touch fire state fields

LOGIC:
  for each market where ps_event_id is set:
    1. compute_ev(etop_market, store) — existing code, unchanged
    2. Alt-eid resolution — existing code, unchanged
    3. Kills eid lookup — existing code, unchanged
    4. Set ev1/ev2/best_ev/best_side
    5. Set ps_fair_1/2, ps_raw_1/2, ps_age
    6. Determine phase based on sport config:
       sport_cfg = config.get_sport_config(market.sport)
       if remain > sport_cfg.trigger_secs * 2/3: phase=P1, min_ev=phase1_ev
       elif remain > sport_cfg.trigger_secs * 1/3: phase=P2, min_ev=phase2_ev
       else: phase=P3, min_ev=phase3_ev
    7. Update state: MONITOR/APPROACHING/PREFIRE/FIRE_ZONE

  PER-SPORT EXAMPLE:
    NBA market, remain=40s, ev=+0.3%:
      sport_cfg = basketball → phase3_ev=0.0
      phase=P3, phase_min_ev=0.0
      0.3% > 0.0% → FIRE_ZONE ✓

    Soccer market, remain=40s, ev=+0.3%:
      sport_cfg = soccer → phase3_ev=1.5
      phase=P3, phase_min_ev=1.5
      0.3% < 1.5% → stays PREFIRE, not fired ✗

    Same EV, different sport = different decision. Clean.
```

---

## MODULE 6: FIRE ENGINE
**File:** `modules/fire_engine.py`
**Cadence:** event-driven (wakes on etop data arrival)

```
READS:  container.markets where state == FIRE_ZONE
        container.etop.listing (FRESH odds at fire time)
        container.ps.store (FRESH fair odds at fire time)
        container.config
        container.inventory

WRITES: container.markets[mid].fire_state
        container.markets[mid].total_fired/total_value
        container.markets[mid].locked_side
        container.markets[mid].consumed_item_ids
        container.markets[mid].press_ids
        container.markets[mid].last_gate_failures

DOES NOT:
  - Match events
  - Classify markets
  - Update dashboard directly

LOGIC:
  1. For each FIRE_ZONE market:
     a. RE-READ etop odds from container.etop.listing (not cached)
     b. RE-COMPUTE EV from container.ps.store (not cached from Valuator)
     c. Get sport config for per-sport gates
     d. Run gates:

  GATES (per-sport aware):
    sport_cfg = config.get_sport_config(market.sport)
    gates = [
      (o1 > 0 and o2 > 0,                              "no_odds"),
      (best_ev > market.phase_min_ev,                   f"ev={best_ev}<{market.phase}:{market.phase_min_ev}%"),
      (container.etop.age < config.max_etop_age,        f"etop_stale={container.etop.age}s"),
      (market.ps_age < sport_cfg.max_ps_age,            f"ps_stale={market.ps_age}s"),
      (market.remaining_cap > 0,                        f"cap_full"),
      (market.total_fired < config.max_items,            f"max_items"),
      (market.raw_pool >= config.min_raw_pool,           f"pool_low"),
    ]
    failed = [reason for passed, reason in gates if not passed]
    market.last_gate_failures = failed  ← dashboard shows WHY it didn't fire

  2. Sort candidates by priority (existing logic)
  3. Fire sequentially (75ms gap)
  4. Update fire state on market

  STALE DATA CIRCUIT BREAKER:
    If container.etop.age > 5s → block ALL fires (etop fetcher is down)
    If container.ps.ws_connected == False → allow but log WARNING
    If market.ps_age > sport_cfg.max_ps_age → block this market only

  WHY RECOMPUTE AT FIRE TIME:
    Valuator runs on brain cycle (~3s). Fire runs on etop event (~instant).
    Between Valuator and Fire, odds may have changed.
    Fire ALWAYS reads fresh data. Never uses cached EV.
```

---

## MODULE 7: CANCEL ENGINE
**File:** `modules/cancel_engine.py`
**Cadence:** every 5s (independent of fire)

```
READS:  container.markets where total_fired > 0
        container.etop.listing (current remain)

WRITES: container.markets[mid].total_fired → 0
        container.markets[mid].locked_side → 0
        container.markets[mid].total_value → 0
        container.markets[mid].consumed_item_ids → []
        container.markets[mid].state → MONITOR
        container.inventory (unconsume items)

DOES NOT:
  - Fire bets
  - Match events
  - Depend on any other module running

LOGIC:
  for each market where total_fired > 0:
    if remain > 300s (market extended):
      1. Fetch cancellable press IDs: etop_api.get_cancellable_presses(mid)
      2. For each press ID: etop_api.regret(mid, press_id)
      3. Unconsume items back to inventory
      4. Reset fire state
      5. Log: [CANCEL] team1 vs team2 cancelled=X/Y freed=Z

  WHY INDEPENDENT:
    Cancel must work even if fire engine is broken/crashed.
    Cancel must work even if brain is stuck.
    Cancel only needs: etop API + market fire state. Nothing else.
```

---

## MODULE 8: DASHBOARD
**File:** `modules/dashboard.py`
**Cadence:** every 3s (after brain cycle)

```
READS:  container.markets (ALL markets, ALL fields)
        container.etop.age
        container.ps.ws_connected
        container.health
        container.inventory
        container.config

WRITES: data/dash_state.json (ONE file, atomic write)

DOES NOT:
  - Import from fire_zone
  - Import from live_feed
  - Import from etop_state
  - Call ANY API
  - Read from ANY source other than Container

LOGIC:
  dash_state = {
    'ts': time.time(),
    'etop_age': container.etop.age,
    'ws_connected': container.ps.ws_connected,
    'markets': [market_to_dash_dict(m) for m in container.markets.values()],
    'health': container.health.summary(),
    'bag_count': container.inventory.free_count,
    'bag_value': container.inventory.free_value,
    'states': count_states(container.markets),
  }

  def market_to_dash_dict(m: Market) -> dict:
      """ONE function converts Market → dashboard dict.

      Everything dashboard needs is ON the Market object.
      No reaching into fire_zone._fire_state.
      No reaching into live_feed.standard_store.
      No reaching into etop_state.listing.
      """
      return {
          'n': f'{m.team1} vs {m.team2}',
          'ml': m.label,
          'fk': m.fire_key,
          'b': m.best_ev,
          'e1': m.ev1,
          'e2': m.ev2,
          'etop': f'{m.o1:.2f}/{m.o2:.2f}' if m.o1 > 0 else '–',
          'pf': f'{m.ps_fair_1:.3f}/{m.ps_fair_2:.3f}' if m.ps_fair_1 else '–',
          'pa': int(m.ps_age) if m.ps_age else 0,
          's': int(m.remain),
          'st': m.state,
          'ln': m.line,
          'ps': f'{m.ps_name_team1} vs {m.ps_name_team2}' if m.ps_name_team1 else '',
          'mid': m.mid,
          'game': m.game,
          'phase': m.phase,
          'phase_min_ev': m.phase_min_ev,
          'cap': f'{m.cap_source}:{m.remaining_cap:.0f}/{m.value_cap:.0f}g',
          'inv_items': m.total_fired,
          'inv_value': m.total_value,
          'can_press': m.can_press,
          'locked_at': m.locked_at,
          'gates': m.last_gate_failures,    # ← NEW: shows WHY it didn't fire
          'match_conf': m.match_confidence, # ← NEW: shows match quality
      }
```

---

# DATA FLOW — End to End

```
Etop API ──→ [ETOP COLLECTOR] ──→ container.etop
                                       │
PS3838 WS ──→ [PS COLLECTOR] ──→ container.ps
                                       │
                                       ▼
                               [CLASSIFIER]
                    reads: container.etop
                    writes: container.markets (creates + updates odds)
                                       │
                                       ▼
                                [MATCHER]
                    reads: container.markets (UNMATCHED)
                         + container.ps.store (events)
                    writes: container.markets (ps_event_id, names)
                                       │
                                       ▼
                               [VALUATOR]
                    reads: container.markets (MATCHED+)
                         + container.ps.store (fair odds)
                         + container.config (per-sport EV)
                    writes: container.markets (ev, phase, state)
                                       │
                              ┌────────┼────────┐
                              ▼                 ▼
                       [FIRE ENGINE]    [CANCEL ENGINE]
                    reads: container.*  reads: container.markets
                    fires: etop API    cancels: etop API
                    writes: fire state writes: reset fire state
                              │                 │
                              └────────┬────────┘
                                       ▼
                               [DASHBOARD]
                    reads: container.* (ONLY)
                    writes: dash_state.json
```

---

# MAIN.PY — Simplified Orchestrator

```python
async def main():
    container = Container()

    # Start collectors (background tasks)
    asyncio.create_task(etop_collector.run(container))
    asyncio.create_task(ps_collector.run(container))

    # Main loop — sequential pipeline, event-driven
    while True:
        # Wait for fresh etop data
        await container.etop.wait_for_update(timeout=5)

        # Pipeline: each step reads/writes its own section
        classifier.run(container)         # container.etop → container.markets
        matcher.run(container)            # container.markets + container.ps → matches
        valuator.run(container)           # container.markets + container.ps → EVs
        await fire_engine.run(container)  # container.markets → fires (async: API calls)
        dashboard.run(container)          # container → dash_state.json

    # Independent background tasks
    asyncio.create_task(cancel_engine.run_loop(container))  # every 5s
    asyncio.create_task(tuhao.run_loop(container))          # every 5s
    asyncio.create_task(discovery.run_loop(container))      # every 10s
```

---

# CLAUDE CODE SESSION RULES

Every Claude Code instruction file MUST start with:

```
SCOPE: You are editing ONLY modules/{module_name}.py
DO NOT touch any other file.
DO NOT add imports from other modules.
You may ONLY read from Container and write to your section.
Read MODULAR_ARCHITECTURE.md before making any changes.
```

Example instruction file:

```
SCOPE: modules/matcher.py ONLY

TASK: Add confidence decay — if a matched market has ev=None
for 5 consecutive cycles (15 seconds), reduce match_confidence
by 20%. If confidence drops below 30, set ps_event_id=None
and state=UNMATCHED so it re-matches.

READS: container.markets[mid].ev1 (set by Valuator)
WRITES: container.markets[mid].match_confidence
        container.markets[mid].ps_event_id (to None on unmatch)
        container.markets[mid].state (to UNMATCHED on unmatch)

TEST: After change, verify that:
  1. A correctly matched market keeps confidence at 100
  2. A wrongly matched market (ev=None for 15s) drops to UNMATCHED
  3. After unmatch, Matcher re-runs and finds a different eid
```

---

# MIGRATION PATH (from current main.py)

## Phase 1: Create Container + Dashboard (SAFE)
- Create container.py with Market dataclass
- Create dashboard.py that reads ONLY from Container
- In main.py: after brain cycle, populate Container from existing state
- Dashboard reads from Container instead of scattered sources
- RISK: Zero. Container is populated FROM existing code. Read-only.

## Phase 2: Extract Classifier (SAFE)
- Move brain STEP 1 + STEP 3 (odds update) → classifier.py
- Classifier writes to container.markets
- Brain STEP 1 calls classifier.run(container)
- RISK: Low. Same logic, different file.

## Phase 3: Extract Matcher (SAFE)
- Move brain STEP 2 → matcher.py
- Add self-healing (confidence decay, negative aliases)
- RISK: Low. Matching logic unchanged, self-healing is additive.

## Phase 4: Extract Valuator
- Move EV computation from brain STEP 3 → valuator.py
- Add per-sport config
- RISK: Low. compute_ev() unchanged, config is additive.

## Phase 5: Extract Fire Engine
- Move _fire_cycle → fire_engine.py
- Fire reads from Container instead of etop_state + live_feed
- RISK: Medium. Fire is critical path. Run parallel first.

## Phase 6: Extract Cancel Engine
- Move check_cancels → cancel_engine.py
- Finally test end-to-end
- RISK: Low. Cancel never worked anyway.

## Phase 7: Clean up main.py
- main.py becomes just the orchestrator (~100 lines)
- Each module is independent, testable, debuggable

## Phase 8: VPS deployment
- NOW it makes sense. Clean modules → easy to distribute.
- Collectors go to VPS 1/2/3, Container becomes Redis-backed.
