# SESSION IMMORTALITY — Complete Technical Guide v2
# How the bot stays connected forever without browser refreshes
# Updated with Vodds integration + all lessons from April 13 debugging marathon

---

## THE DISCOVERY (Session 19, confirmed Session 28)

Session 19 proved 3.5+ hours with zero Chrome refreshes.
Session 28 proved 4+ hours with zero reconnects after fixing heartbeat to use curl_cffi.
The session is truly immortal as long as the 5 rules are followed.

---

## THE 5 COMMANDMENTS OF SESSION IMMORTALITY

### 1. ONE curl_cffi session for EVERYTHING
- ALL HTTP calls use the SAME `curl_cffi.requests.AsyncSession(impersonate="chrome120")`
- NEVER use aiohttp, urllib, requests — they lack Chrome TLS fingerprint → 403
- Session 28 bug: heartbeat used aiohttp → all 403s → cookies never refreshed → death at 3.5h
- curl_cffi auto-captures Set-Cookie headers from every response → cookies refresh themselves

### 2. NEVER cache wstoken — always fetch fresh
- Token is session-bound to the WS connection
- Reusing a cached token on reconnect → recv() timeout (no data)
- Session 28 bug: `age < 600` cache meant reconnect reused stale tokens → WS got no data
- Every WS connect MUST call wstoken endpoint fresh

### 3. Heartbeat runs FOREVER, never stops
- POST account-balance + GET keep-alive + GET system/status every 5-10 seconds
- Heartbeat starts BEFORE WS connects
- Heartbeat continues during REST-only mode
- Response Set-Cookie headers are the ONLY thing keeping cookies alive

### 4. PONG with destination="ALL" on every PING
- Server sends PING every ~60 seconds
- Missing PONG → WS dies at 6 minutes
- Wrong PONG (missing destination field) → WS dies at 6 minutes
- Correct: `{"type":"PONG","destination":"ALL"}`

### 5. Save live cookies to disk every 5 minutes
- `session.cookies` → `cookie.json` periodically
- On restart, load cookies from disk → session jar
- If bot crashes, restart has cookies from <5 minutes ago → survives

---

## WHAT WAS KILLING SESSIONS (historical)

### Kill Pattern 1: Chrome refresh cycle (Session 1-18)
1. Open Playwright → login → grab cookies
2. Run bot with cookies for ~60 minutes
3. wstoken 403 → bot panics
4. Bot triggers Chrome refresh → new login → NEW `_ulp` cookie
5. Old `_ulp` orphaned → server sees two sessions → MULTIPLE_LOGIN → ban

### Kill Pattern 2: aiohttp heartbeat (Session 28, first 3.5 hours)
1. Heartbeat used aiohttp (not curl_cffi)
2. Every heartbeat call → Cloudflare 403 (no Chrome TLS fingerprint)
3. No Set-Cookie headers captured → cookies expired after 3.5h
4. WS survived on its own (WS connection kept session warm)
5. Eventually cookies too stale → wstoken 403 → WS can't reconnect → dead

### Kill Pattern 3: Token caching on reconnect (Session 28)
1. `fetch_token()` cached ws_token.json for 10 minutes (`age < 600`)
2. WS dropped → bot tried reconnect → reused cached token
3. Cached token was session-bound to OLD connection → server rejected it
4. `WebSocket recv() timed out` — connected but no data
5. Fix: removed cache, always fetch fresh from wstoken endpoint

---

## ARCHITECTURE: WHAT RUNS WHERE

### Mac only (cold start):
```
data/refresh_vodds.py
  → Playwright opens real Chrome
  → Login to vodds.com (manual — has captcha)
  → Vodds dashboard calls PS3838 whitelabel pre-auth
  → Captures: wstoken + 26 cookies + whitelabel domain
  → Saves: data/ws_token.json + data/cookie.json
  → SCP both to VPS
  → Never needed again (unless cookies fully expire on disk)
```

### VPS runtime (immortal):
```
core/auth/ps3838_auth.py
  → Loads cookie.json into curl_cffi session at startup
  → fetch_token() → hits {wl_host}/member-auth/v2/wstoken
  → build_headers() → Referer/Origin from wl_domain (dynamic, not hardcoded)
  → save_cookies_to_disk() → dumps live session cookies every 5 min
  → _get_wl_host() → reads domain from ws_token.json

main.py → _vodds_ulp_heartbeat()
  → POST {wl_host}/member-service/v2/account-balance (every 10s, MUST be POST not GET)
  → GET {wl_host}/member-auth/v2/keep-alive (every 10s)
  → GET {wl_host}/member-service/v2/system/status (every 5s)
  → Uses the ONE curl_cffi session
  → Set-Cookie from responses keeps cookies alive forever
  → Starts BEFORE WS connects

feeds/ps3838_ws.py
  → Connects wss://{wl_host}/sports-websocket/ws?token={token}&ulp={ulp_b64}
  → Subscribes to sports: sp=4 (basketball), sp=12 (esports), sp=29 (soccer)
  → PONG with {"type":"PONG","destination":"ALL"} on every PING
  → On disconnect → REST fallback → cooldown 300s → fresh wstoken → reconnect
  → Max 3 retries per cycle (not 300 — that gets you banned)
```

---

## THE COMPLETE COOKIE LIFECYCLE

```
COLD START (Mac, one time):
  Playwright → vodds.com → login → pre-auth on whitelabel
  → Server sets 26 cookies:
    _ulp        → session identity (NEVER changes, NEVER expires with activity)
    u_1145      → session token (refreshed by keep-alive)
    lcu         → last checked (refreshed by keep-alive)
    custid_1145 → customer ID
    BrowserSessionId_1145 → browser fingerprint
    _sig        → signature
    _og         → origin
    SLID_1145   → session login ID
    + 18 more supporting cookies

BOT RUNNING (VPS, forever):
  Every 5-10s heartbeat via curl_cffi:
    → Sends: all cookies in jar (including _ulp)
    → Receives: Set-Cookie headers refreshing u_1145, lcu, etc.
    → _ulp unchanged (permanent master key)
    → curl_cffi jar auto-updates with fresh cookie values
    → Session TTL extended indefinitely

  Every 5 minutes:
    → session.cookies dumped to cookie.json on disk
    → Protects against restart — fresh cookies always on disk

  WS connection:
    → Uses same cookies for wstoken fetch
    → wstoken valid for ~65 minutes
    → WS connection survives beyond wstoken expiry
    → If WS drops, fetch fresh wstoken (using live cookies) → reconnect

BOT RESTART:
    → Reads cookie.json (< 5 min old from periodic save)
    → Loads into curl_cffi session
    → If _ulp still valid server-side → session resumes immediately
    → If bot was down too long (hours) → _ulp expired → need Mac cold start
```

---

## WHITELABEL DOMAIN HANDLING

PS3838 whitelabel domains rotate between sessions:
- `uyfnltp.lenvora8.com`
- `uyfnltp.auremi88.com`
- `uyfnltp.eviran66.com`
- `uyfnltp.mervani99.com`

Domain is:
- Captured by `refresh_vodds.py` and stored in `ws_token.json`
- Read dynamically by `_get_wl_host()` — NEVER hardcoded
- Does NOT change during a session
- Cookie domain = TLD (`.lenvora8.com`), not full host

If you see `pinnacle888.com` hardcoded anywhere → BUG. Must use `_get_wl_host()`.

---

## VODDS-SPECIFIC NOTES

### What Vodds gives us:
- Free PS3838 whitelabel access (no deposit needed)
- Unlimited accounts (manual creation — has captcha)
- Same WS JSON format as PS3838 direct (FULL_ODDS, UPDATE_ODDS)
- Existing parser handles it perfectly — zero code changes for odds parsing

### Vodds auth flow:
1. Login to vodds.com → sets VSESS2 cookie (vodds session)
2. Dashboard loads PS3838 widget → calls `/member/sport/pin/login`
3. Returns `loginUrl` → pre-auth on whitelabel domain → sets PS3838 cookies
4. Widget calls wstoken → opens WS → FULL_ODDS streams

### VSESS2 cookie:
- Only needed during `refresh_vodds.py` (Mac cold start)
- Short TTL — not used by VPS bot at all
- Bot uses whitelabel cookies directly

### Account banned? → Create new Vodds account → refresh → new whitelabel session
No more PS3838 ban problem. Infinite free accounts.

---

## RECONNECT FLOW

```
WS drops (network blip, server restart)
  │
  ├─ Bot enters REST-only mode
  │  └─ Still gets PS3838 odds via REST compact endpoints
  │
  ├─ Heartbeat CONTINUES (keeps cookies alive!)
  │
  └─ After REST_ONLY_COOLDOWN (300s):
     │
     ├─ fetch_token() → wstoken endpoint with LIVE session cookies
     │  │
     │  ├─ 200 → fresh token → connect WS → streaming again ✅
     │  │
     │  └─ 403 → cookies dead
     │     │
     │     ├─ Retry 3x with backoff (12s, 30s, 60s)
     │     │
     │     └─ Still 403 → REST-only mode continues
     │        └─ Need Mac cold start (refresh_vodds.py)
     │
     └─ WS connected → subscriptions sent → FULL_ODDS flows
```

---

## DEATH CONDITIONS + FIXES

| Symptom | Cause | Fix |
|---------|-------|-----|
| wstoken 403 after restart | cookie.json stale on disk | Ensure save_cookies_to_disk() runs every 5 min |
| wstoken 403 while running | Heartbeat not using curl_cffi | Check imports — MUST be curl_cffi, NEVER aiohttp |
| WS recv() timeout | Cached token reused | Remove token caching — always fetch fresh |
| WS dies at 6 min | Wrong PONG format | Must send `{"type":"PONG","destination":"ALL"}` |
| MULTIPLE_LOGIN ban | Opened Chrome while bot runs | NEVER open browser on same account |
| MULTIPLE_LOGIN ban | Chrome refresh cycle | NEVER re-login while bot runs |
| Session fully dead | Bot down for hours, cookies expired | Mac cold start: refresh_vodds.py |
| Heartbeat all 403 | Using aiohttp/urllib/requests | Switch to curl_cffi with impersonate="chrome120" |
| account-balance 405 | Using GET instead of POST | account-balance endpoint requires POST |

---

## DEBUGGING CHECKLIST (check in order)

```
1. Is heartbeat running?
   → grep "heartbeat OK" bot_output.log | tail -5
   → Should see status=200 bal=200 ka=200 le=200

2. Is heartbeat using curl_cffi (not aiohttp)?
   → Check imports in heartbeat function
   → If aiohttp → all 403 → cookies die → session dies

3. Is wstoken returning 200?
   → grep "wstoken" bot_output.log | tail -5
   → 200 = good, 403 = cookies dead

4. Is token being fetched fresh (not cached)?
   → Should NOT see "Using saved ws_token.json"
   → Should see "[AUTH] Vodds wstoken status=200" on every reconnect

5. Is PONG being sent correctly?
   → grep "PONG" bot_output.log
   → Must have destination="ALL"

6. Is WS connected?
   → grep "Connected, subscriptions sent" bot_output.log | tail -3

7. Are cookies being saved to disk?
   → Check cookie.json modification time
   → Should update every 5 minutes
```

---

## ETOP SESSION IMMORTALITY

Etop uses different auth but same principles:

```
data/session.json → etop session cookies
  → directusToken for API calls
  → v-hucode for all-odds-selections endpoint
  → Session maintained by regular API calls (list.do, show.do)
  → If session dies → re-login via etop auth
  → DJSP_USER cookie refresh via Playwright (Mac)
```

Same rules apply:
- ONE session for everything
- Never re-login while running
- Regular API calls keep session alive
- curl_cffi for any Cloudflare-protected endpoints

---

## PROVEN METRICS

| Metric | Session 19 | Session 28 (pre-fix) | Session 28 (post-fix) |
|--------|-----------|---------------------|----------------------|
| Duration | 3.5+ hours | 3.5 hours (died) | 4+ hours (immortal) |
| Chrome refreshes | 0 | 0 | 0 |
| WS reconnects | 0 | 3 (then died) | 0 |
| Heartbeat failures | 0 | ALL (aiohttp 403) | 0 |
| FULL_ODDS received | ~8,000 | ~10,968 | counting... |
| Token refreshes | 0 | 1 (at 17:46) | auto |
| Mac dependency | cold start only | cold start only | cold start only |

---

## QUICK REFERENCE

```
Q: wstoken 403 — session dead?
A: If WS still connected → NO, wstoken has 65-min TTL, WS survives beyond it.
   If WS dead and heartbeat also 403 → YES, cookies expired. Need Mac cold start.
   If heartbeat 200 but wstoken 403 → wstoken-specific issue, will retry.

Q: Should I ever Chrome refresh?
A: NEVER while bot is running. Only refresh_vodds.py for cold start.

Q: Bot crashed — restart safe?
A: Yes, if cookie.json is < 30 min old (save_cookies_to_disk runs every 5 min).
   Just restart. Cookies should still be valid.

Q: Two bots on same account?
A: NEVER. MULTIPLE_LOGIN = ban. Kill old bot first. Verify with ps aux.

Q: How to switch to new Vodds account?
A: 1) Kill bot 2) Login new account on Mac 3) refresh_vodds.py 4) SCP 5) Start bot

Q: Session lasted 3 days then died — why?
A: Some server-side TTL we haven't hit yet. Just cold start again.
   The longer it runs, the more data we have on true limits.
```

---

## FILES INVOLVED

```
core/auth/ps3838_auth.py    → PS3838Auth class, curl_cffi session, cookie management
feeds/ps3838_ws.py          → WebSocket connection, PONG, retry logic, subscriptions
main.py                     → _vodds_ulp_heartbeat() — the immortality heartbeat
data/cookie.json            → Live cookies (saved every 5 min by bot)
data/ws_token.json          → WS token + whitelabel domain (dynamic)
data/refresh_vodds.py       → Mac-only: Playwright login to vodds.com
tools/vodds_restart.sh      → One-command: refresh + SCP + restart
docs/SESSION_IMMORTALITY_STANDARD.md → Quick reference for the 5 rules
```

## EVERY FUTURE SESSION MUST READ THIS FIRST
## EVERY NEW WHITELABEL INTEGRATION MUST FOLLOW THE 5 COMMANDMENTS
## NEVER USE AIOHTTP FOR ANYTHING CLOUDFLARE-PROTECTED
