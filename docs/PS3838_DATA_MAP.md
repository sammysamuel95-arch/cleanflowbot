# PS3838 Data Map v5.2
## Verified from live WS + REST captures
## Updated: 2026-03-26 (session 12)

---

## WebSocket Structure (mk=1 AND mk=3 — IDENTICAL)

Message: { type: "FULL_ODDS"|"UPDATE_ODDS", odds: { l: [...], n: [...] } }

### Period dict (WS)
| Index | Content | Notes |
|-------|---------|-------|
| 0 | HDP rows | [[home_hdp, away_hdp, str, home_odds, away_odds, ...], ...] |
| 1 | OU rows | [[total_str, total_float, over_odds, under_odds, ...], ...] |
| 2 | **ML** | [home_odds, away_odds] — NORMAL ORDER ALL SPORTS |
| 3 | period_num | Integer |
| 4 | null | Always null |

ML = [home, away] for ALL sports, ALL sources. NO REVERSAL. EVER.

---

## REST Structure (per-event ladder)

Endpoint: /sports-service/sv/compact/events?me={event_id}&mk=3&more=true

### Period dict (REST) — DIFFERENT INDICES FROM WS
| Index | Content |
|-------|---------|
| 2 | HDP rows |
| 3 | OU rows |
| 4 | ML [home, away] |
| 5 | period_num |

---

## REST Rate Limit (Session 12 Discovery)

### Endpoint: /sports-service/sv/compact/events
- **NOT per-second rate limit. IP-based exhaustible budget.**
- Fresh session: works fine (startup 3 calls = OK)
- After ~30-80 calls: 429 for ALL subsequent calls
- Recovery: ~60s of zero calls → budget refills
- "Maintenance" page = 429 disguised, not real maintenance
- Browser login works fine (separate context)

### Budget Killers (what burned it)
```
Startup bulk REST:     3 calls (one-time, acceptable)
Keepalive ping:        1 call/5min × hours = 24+ calls (WASTEFUL)
Scan loop REST ladder: 5-10 calls per scan × many scans = 50+ calls (WASTEFUL)
Total per session:     80+ calls → budget exhausted
```

### Solution: Reserve budget for critical calls only
```
KEEP:   startup bulk fetch (3 calls, one-time)
ADD:    approaching zone verify (1 call per event, ~6-10 per session)
REMOVE: keepalive from REST endpoint (use WS heartbeat instead)
REDUCE: scan loop REST to every 5min not every scan
Target: ~15 calls per session instead of 80+
```

---

## Store Behavior

### _odds_store
Key: (home, away, market, map_num, line) — all lowercase
Value: market dict with fair_home, fair_away, fair_over, fair_under, _ts

### _ts Timestamp
Set on every store entry. Updated by WS deltas and REST merges.
**WARNING:** _ts indicates "last time WS/REST updated this entry."
It does NOT indicate "line is still live on PS3838."
A line can have _ts from 2 hours ago and still be in the store (24h expiry).

### 24h Expiry (replaced FULL_ODDS cleanup)
Old: FULL_ODDS snapshot → delete lines not in snapshot → NBA wipe bug
New: Lines persist 24h. Active lines get _ts refreshed by WS. Dead lines expire.
**PROBLEM:** PS removes a line → WS doesn't push removal → store keeps corpse for 24h.
**IMPACT:** Bot fires on 2-hour-old dead PS odds (BIG -2.5 Map2 = +35% fake EV).

### Stale Line Detection (UNFIXED)
- read_ps_store() returns True on dead data (no freshness check)
- _ts check in fire.py was dead code (_ts key never copied to ps_mkt)
- event_id check in fire.py was dead code (key never in ps_mkt)
- PS REST verify at approaching zone is the planned solution

---

## Subscription Types
| mk | What | Subscribe for |
|----|------|---------------|
| 1 | Main (HDP + OU + ML) | All sports |
| 2 | Returns nothing | Do NOT use |
| 3 | Maps (same as mk=1) | Esports only |

---

## Auth
- Token: /auth-service/api/v1/users/token (~10 min validity)
- WS: wss://push.pinnacle888.com/ws?token=...&ulp=...
- Cookie: from refresh_pinnacle888.py → cookie.json
- Cookie kept alive by keepalive ping (not REST — use different method)

---

## Version History
- v5 (2026-03-25): 100% verified all sports
- v5.1 (2026-03-26): FULL_ODDS cleanup removal, 24h expiry
- v5.2 (2026-03-26): REST budget discovery, stale line detection analysis, keepalive findings
