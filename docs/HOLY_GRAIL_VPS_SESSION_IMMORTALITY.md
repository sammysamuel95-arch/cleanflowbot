# THE HOLY GRAIL — VPS SESSION IMMORTALITY
# Complete guide to autonomous PS3838/Vodds authentication from VPS
# Reverse-engineered April 16 2026 over 6-hour session
# Updated April 16 2026 — proxy + account rotation added
# Every fact proven with live tests. Zero assumptions.
#
# Location: docs/HOLY_GRAIL_VPS_SESSION_IMMORTALITY.md
# ANY AI SESSION READS THIS BEFORE TOUCHING AUTH CODE.

---

## WHAT THIS BOT DOES (context)

CleanFlowBot monitors etopfun.com parimutuel markets against PS3838 (Pinnacle)
sharp odds via Vodds whitelabel. It needs persistent authenticated connections
to both etopfun and PS3838 to stream live odds and fire bets.

---

## THE COMPLETE AUTH CHAIN (fully decoded)

```
Step 1: POST https://vodds.com/member/login
        Content-Type: application/x-www-form-urlencoded
        Body: username=X&accessToken=X&loginMethod=NORMAL&timezone=Asia/Jakarta&isMobile=false
        Response: {"userId": 64355}
        Sets: VSESS2 cookie
        Works from: everywhere ✅

Step 2: POST https://vodds.com/member/sport/pin/login
        Headers: Accept: application/json, X-Requested-With: XMLHttpRequest
        Response: {"data": {"loginUrl": "https://3.33.215.100#b2bp_uyfnltp/member-auth/v2/pre-auth?locale=en&sport=soccer&oddsFormat=EU&mode=LIGHT&target=LIVE&token=OTUvektj..."}}
        Works from: everywhere ✅

Step 3: POST https://{prefix}.{domain}/member-auth/v2/auth-token
        Content-Type: application/json;charset=UTF-8    ← MUST BE JSON
        Body: {"token": "pin_token_from_step_2"}        ← NOT form-encoded, NOT empty
        Response: {"success":true,"message":"AUTHENTICATED","tokens":{"X-U":"...","X-Custid-1145":"..."}}
        Sets 13 cookies: _ulp, u_1145, custid_1145, BrowserSessionId_1145, SLID_1145,
                         lcu, _og, __prefs, _userDefaultView, isEsportsHub_1145, pctag, uoc, VSESS2
        Works from: residential IPs + some datacenters ⚠️ (see IP BLOCKING section)

Step 4: GET https://{prefix}.{domain}/member-auth/v2/wstoken
        Response: {"token": "AAAAAAU_bLMAAAGd..."}
        Works from: everywhere (with valid cookies) ✅

Step 5: WSS connection + heartbeat
        Heartbeat keeps cookies alive indefinitely via Set-Cookie responses.
        Works from: everywhere ✅
```

---

## LOGIN URL FORMAT DECODER

pin/login returns a loginUrl in this format:
```
https://3.33.215.100#b2bp_uyfnltp/member-auth/v2/pre-auth?locale=en&sport=soccer&oddsFormat=EU&mode=LIGHT&target=LIVE&token=OTUvektj...
```

How to parse:
```
Full URL
  │
  ├── IP: 3.33.215.100
  │   └── AWS load balancer. DO NOT use directly (returns 404).
  │
  ├── Fragment (after #): b2bp_uyfnltp/member-auth/v2/pre-auth?...
  │   ├── b2bp_uyfnltp = JS routing hint
  │   ├── prefix = strip "b2bp_" → "uyfnltp" (customer prefix)
  │   └── path = everything after first "/" → /member-auth/v2/pre-auth?...
  │
  └── Token (query param): OTUvektj...
      └── Base64-encoded auth token. Double-encoded (b64 of b64).
          Inner layer is encrypted binary, not human-readable.

To reconstruct the auth-token call:
  URL: https://{prefix}.{domain}/member-auth/v2/auth-token
  Body: {"token": "OTUvektj..."}  (the raw token value from URL)
  Try domains: lenvora8.com → auremi88.com → eviran66.com → mervani99.com
```

Python parsing:
```python
import re
raw_url = pin_response.json()["data"]["loginUrl"]
pin_token = re.search(r"token=([A-Za-z0-9+/=]+)", raw_url).group(1)
wl_prefix = raw_url.split("#")[1].split("/")[0].replace("b2bp_", "")
# Try: f"https://{wl_prefix}.lenvora8.com/member-auth/v2/auth-token"
# Body: json={"token": pin_token}
```

---

## auth-token SPECIFICS

This endpoint is the gatekeeper. Get it wrong and nothing works.

### CORRECT (proven working):
```python
await session.post(f"{ps_base}/member-auth/v2/auth-token",
    json={"token": pin_token},
    headers={
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": ps_base,
        "Referer": f"{ps_base}/en/compact/fwp"
    })
```

### WRONG (all tested, all fail):
```
Form-encoded body              → 415 Unsupported Media Type
Empty body                     → 415 Unsupported Media Type
GET method                     → 405 Method Not Allowed
JSON without charset=UTF-8     → works (charset optional)
Missing Origin/Referer         → works (headers optional for auth)
```

### Response on success:
```json
{
  "success": true,
  "message": "AUTHENTICATED",
  "tokens": {
    "X-U": "AAAAAwAAAAAFP2yz...",
    "X-Custid-1145": "id=Q4301003R0&login=202604151718&roundTrip=..."
  }
}
```
Plus 13 Set-Cookie headers setting _ulp, u_1145, etc.

### Response on IP block:
```json
{
  "success": false,
  "message": "LOGIN_BLOCKED",
  "blockedAuthentication": {
    "ipAddress": "64.176.35.74",
    "msgBlockedPage": "The page that you are trying to access has been restricted in this location."
  }
}
```

---

## IP BLOCKING — WHAT WE DISCOVERED

PS3838 blocks auth-token from datacenter IPs using an IP reputation database.
This is APPLICATION-LEVEL blocking, not Cloudflare. HTTP 200 with LOGIN_BLOCKED in body.

### Tested IPs:
| IP | Provider | Region | auth-token Result |
|----|----------|--------|-------------------|
| Mac residential | ISP | Indonesia | AUTHENTICATED ✅ |
| 45.32.25.201 | Vultr | Tokyo | LOGIN_BLOCKED ❌ |
| 64.176.35.74 | Vultr | Tokyo | LOGIN_BLOCKED ❌ |
| 66.245.216.27 | Vultr | Tokyo | LOGIN_BLOCKED ❌ |
| 158.247.202.22 | Vultr | Korea | LOGIN_BLOCKED ❌ |
| 35.234.17.80 | Google Cloud | ? | LOGIN_BLOCKED ❌ |
| Cloudflare Worker | Cloudflare | Edge | Cloudflare 403 ❌ |
| 168.199.244.251 | Webshare proxy | Amsterdam | AUTHENTICATED ✅ |
| 108.61.165.203 | Vultr | Amsterdam | AUTHENTICATED ✅ |

### Key findings:
- NOT all Vultr IPs are blocked — Amsterdam works, Tokyo/Korea don't
- NOT all datacenters are blocked — it's IP-range specific
- Google Cloud IPs are blocked
- Cloudflare-to-Cloudflare requests are blocked (Workers can't reach it)
- Residential proxies labeled "residential" may actually be datacenter (Webshare)
- The block is ONLY on auth-token — all other endpoints work from all IPs:
  - vodds.com/member/login → 200 ✅
  - vodds.com/member/sport/pin/login → 200 ✅
  - whitelabel/member-auth/v2/keep-alive → 200 ✅
  - whitelabel/member-service/v2/account-balance → 200 ✅
  - whitelabel/member-auth/v2/wstoken (with cookies) → 200 ✅

### When choosing a VPS:
1. Test auth-token FIRST before deploying anything
2. Use the one-liner test script (see QUICK AUTH TEST below)
3. Amsterdam (Netherlands) works. Try other EU locations if needed.
4. Keep the test script handy — if you need to move, test before committing.

---

## HOW THE BROWSER DOES IT (reverse-engineered)

The browser flow is different from the API flow we use:

```
Browser:
  1. Loads pre-auth URL → 302 redirect to /en/compact/fwp?token=X
  2. fwp page loads JS bundle (main.ab10936b.js, 3.8MB webpack)
  3. JS reads token from URL param
  4. JS calls auth-token internally → sets cookies
  5. JS calls wstoken → opens WebSocket
  6. Cookie values stored in localStorage under "x-app-data" key
  7. Subsequent requests send _ulp in X-App-Data header, not as cookie
  8. Only _sig and _apt sent as actual cookies to WS/keep-alive
```

Key JS details:
- `window.env.headerBasedEnabled = true` → app uses X-App-Data header instead of cookies
- STORAGE_KEY maps: `_ulp → "_ulp"`, `xAppData → "x-app-data"`, `_sig → cookie`
- `setLocalStorageAndCookie()` writes to both localStorage and document.cookie
- `getParameterByName("token")` reads the pin_token from URL
- `loginByToken()` → POST to auth-token endpoint
- `buildResponseSignIn()` processes the response
- `handleResponseSignIn()` checks for BLOCKED_IP_RING_FENCING

We DON'T need to replicate the browser flow. The direct curl_cffi auth-token call
sets all 13 cookies via Set-Cookie headers. That's sufficient for wstoken + WS.

---

## THE 5 COMMANDMENTS

### 1. ONE curl_cffi session for ALL whitelabel calls
```python
session = AsyncSession(impersonate="chrome120")
```
- NEVER use aiohttp, urllib, requests for whitelabel endpoints
- aiohttp OK for: SSE server (port 8889), etop calls
- curl_cffi provides Chrome TLS fingerprint → passes Cloudflare
- Without it → Cloudflare 403 on every request

### 2. auth-token MUST be JSON POST with pin_token
```python
await session.post(url, json={"token": pin_token},
    headers={"Content-Type": "application/json;charset=UTF-8"})
```
- NOT form-encoded (415)
- NOT empty body (415)
- NOT GET (405)
- pin_token comes from pin/login response URL

### 3. Heartbeat runs FOREVER
```
POST {whitelabel}/member-service/v2/account-balance  ← MUST be POST (GET = 405)
GET  {whitelabel}/member-auth/v2/keep-alive
GET  {whitelabel}/member-service/v2/system/status
```
- Every 5-10 seconds
- Starts BEFORE WS connects
- Uses curl_cffi (same session as everything else)
- Set-Cookie responses refresh cookies indefinitely
- Save cookies to disk every 5 minutes

### 4. PONG with destination="ALL"
```json
{"type":"PONG","destination":"ALL"}
```
- Server PINGs every ~60 seconds
- Wrong/missing PONG → WS dies at exactly 6 minutes
- Must include `destination` field

### 5. init_session() must NOT overwrite disk cookies
```python
async def init_session(self):
    self._load_cookies()                    # Load from disk
    if self._has_ulp():                     # If _ulp present from disk
        token = await self._raw_fetch_token()  # Test wstoken
        if token:
            return                          # Cookies still valid, SKIP login
    await self._login_vodds()              # Only login if cookies dead
```
- Loading 13 cookies then calling _login_vodds() → overwrites with partial set
- This was the original bug that caused wstoken 403 on the old VPS
- _login_vodds() is ONLY for: first boot, or cookies expired

---

## COOKIE LIFECYCLE

```
FIRST BOOT:
  _login_vodds() → 4 steps → 13 cookies → save to disk
  
RUNNING (forever):
  heartbeat every 5-10s → Set-Cookie refreshes u_1145, lcu, etc.
  _ulp never changes (permanent session key)
  save cookies to disk every 5 minutes
  
RESTART (< 30 min downtime):
  Load cookies from disk → _ulp still valid → wstoken works → resume
  
RESTART (> few hours downtime):
  Load cookies from disk → _ulp expired → wstoken 403
  → _login_vodds() runs → fresh 13 cookies → resume
  
ACCOUNT BANNED:
  Bot detects pin/login "Internal Server Error" → auto-rotates to next account
  from data/auth/vodds_accounts.json → updates credentials.json → retries login
  Telegram alert: "🔄 Vodds account X banned — switched to Y"
  If ALL accounts banned → Telegram "🚨 all accounts banned" → add more to DB
  To add accounts manually: register free vodds.com account, add to vodds_accounts.json
```

---

## WHITELABEL DOMAIN HANDLING

PS3838 whitelabel domains rotate between sessions:
```
uyfnltp.lenvora8.com
uyfnltp.auremi88.com
uyfnltp.eviran66.com
uyfnltp.mervani99.com
```

- Customer prefix `uyfnltp` is constant
- Domain changes per session (set during auth)
- Read dynamically from saved state, NEVER hardcode
- Cookie domain = TLD (e.g. `.lenvora8.com`)
- If you see `pinnacle888.com` anywhere → BUG

Auth-token may work on some domains but not others in the same session.
Always try all 4 domains in order during _login_vodds().

---

## RECONNECT FLOW

```
WS drops
  │
  ├─ REST-only mode (odds via REST compact endpoints)
  │
  ├─ Heartbeat CONTINUES (keeps cookies alive)
  │
  └─ After REST_ONLY_COOLDOWN (300s):
     │
     ├─ fetch_token() → wstoken → 200 → reconnect WS ✅
     │
     └─ wstoken 403 → cookies dead
        │
        ├─ _login_vodds() → full auth chain → fresh cookies
        │  │
        │  ├─ AUTHENTICATED → wstoken → reconnect ✅
        │  │
        │  └─ LOGIN_BLOCKED → VPS IP changed? Need new VPS.
        │
        └─ Max 3 retries per cycle (not 300 — gets you banned)
```

---

## QUICK AUTH TEST (one-liner for any new VPS)

Replace credentials and run. Tests full auth chain in 10 seconds:

```bash
ssh -o StrictHostKeyChecking=no root@NEW_IP "apt update -qq && apt install -y -qq python3-pip && pip install curl_cffi -q --break-system-packages && python3 -c \"
import asyncio, json, re
from curl_cffi.requests import AsyncSession
async def main():
    session = AsyncSession(impersonate='chrome120')
    await session.post('https://vodds.com/member/login',
        data={'username': 'VODDS_USER', 'accessToken': 'VODDS_PASS',
              'loginMethod': 'NORMAL', 'timezone': 'Asia/Jakarta', 'isMobile': 'false'},
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Referer': 'https://vodds.com/static/login', 'Origin': 'https://vodds.com'})
    pin = await session.post('https://vodds.com/member/sport/pin/login',
        headers={'Referer': 'https://vodds.com/member/dashboard', 'Origin': 'https://vodds.com',
                 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})
    raw_url = pin.json()['data']['loginUrl']
    pin_token = re.search(r'token=([A-Za-z0-9+/=]+)', raw_url).group(1)
    wl_prefix = raw_url.split('#')[1].split('/')[0].replace('b2bp_', '')
    ps_base = f'https://{wl_prefix}.lenvora8.com'
    r = await session.post(f'{ps_base}/member-auth/v2/auth-token',
        json={'token': pin_token},
        headers={'Content-Type': 'application/json;charset=UTF-8', 'Origin': ps_base,
                 'Referer': f'{ps_base}/en/compact/fwp'})
    print(f'auth-token: {r.status_code}')
    b = r.text[:200]
    print(f'result: {\"AUTHENTICATED\" if \"AUTHENTICATED\" in b else \"BLOCKED\" if \"BLOCKED\" in b else b}')
    await session.close()
asyncio.run(main())
\""
```

If AUTHENTICATED → deploy there. If BLOCKED → try different region/provider.

---

## PROVEN VPS: AMSTERDAM

```
IP: 108.61.165.203
Provider: Vultr
Region: Amsterdam, Netherlands
Auth: AUTHENTICATED ✅ (direct, no proxy)
Latency:
  etopfun.com:    1.2ms ping, 357ms TTFB
  vodds.com:      1.2ms ping
  whitelabel:     1.1ms ping, 108ms TTFB
  
Previous Tokyo VPS: 62ms to etop (50x slower)
```

Amsterdam is co-located with Cloudflare edge nodes serving all three services.
Packets don't leave the datacenter. This is the optimal location.

---

## INTERFACE RULES (curl_cffi specifics)

```python
# CORRECT — _CurlSession interface:
async with session.get(url) as resp:
    status = resp.status          # ← .status (not .status_code)

# WRONG:
resp = await session.get(url)
status = resp.status_code         # ← WRONG attribute for _CurlSession
```

```python
# CORRECT — config URL:
import config as _cfg
url = _cfg.PS_BASE_URL            # module reference (always current)

# WRONG:
from config import PS_BASE_URL    # stale copy at import time
```

---

## FILES

```
feeds/ps_auth.py              → PSAuth class, _login_vodds(), curl_cffi session, account rotation
feeds/ps3838_ws.py            → WebSocket, PONG, subscriptions, reconnect
feeds/ps3838_rest.py          → REST fallback endpoints
feeds/etop_session.py         → Etop Steam SSO + Protonmail Guard (Playwright)
main.py                       → Orchestrator, heartbeat, session loop
core/logger.py                → Non-blocking logger (writes own bot.log)
core/notifier.py              → Telegram alerts (fire, WS drop, auth fail, startup)
data/auth/cookie.json         → 13 cookies (saved every 5 min by bot)
data/auth/ws_token.json       → WS token + whitelabel domain
data/auth/credentials.json    → active vodds_user/pass, steam, proton creds
data/auth/vodds_accounts.json → account pool: status=active|ready|banned
data/auth/session.json        → Etop session cookies
data/refresh_vodds.py         → pure curl_cffi token refresh (no Playwright)
                                 uses proxy for auth-token, saves to data/auth/
tools/ps_token_pusher.py      → Mac-side token pusher (no longer required for Tokyo VPS)
```

---

## SYSTEMD

```ini
[Service]
WorkingDirectory=/opt/cleanvps1
ExecStart=/opt/botenv/bin/python3 main.py
StandardOutput=journal          ← NOT append to bot.log
StandardError=journal           ← logger.py writes its own bot.log
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=30
```

Having BOTH systemd AND logger.py write to bot.log = double log lines.
SystemD captures stdout → journal. Logger.py writes → bot.log. Separate.

---

## TROUBLESHOOTING TREE

```
auth-token LOGIN_BLOCKED?
├── VPS IP in blocked range
│   └── Test with quick auth test script
│       ├── AUTHENTICATED → IP is fine, bug is elsewhere
│       └── BLOCKED → need different VPS region/provider
│           └── Amsterdam (Vultr) proven working

auth-token 415?
└── Wrong Content-Type. Must be: application/json;charset=UTF-8
    Body must be: {"token": "pin_token"} — NOT form-encoded, NOT empty

wstoken 403?
├── Are 13 cookies loaded? (not just VSESS2)
│   └── Check cookie.json has _ulp, u_1145, custid_1145, etc.
├── Did init_session() overwrite disk cookies?
│   └── Fix: skip _login_vodds() if _ulp present
└── Cookies expired (bot down too long)?
    └── _login_vodds() runs automatically on restart

Heartbeat 403?
├── Using curl_cffi? (not aiohttp)
│   └── Must be: AsyncSession(impersonate="chrome120")
└── Cookies expired → _login_vodds() recovery

WS dies at 6 minutes?
└── PONG wrong → {"type":"PONG","destination":"ALL"}

Double log lines?
└── systemd + logger.py both writing bot.log
    Fix: StandardOutput=journal in systemd

MULTIPLE_LOGIN?
└── Two sessions on same account → kill old bot first
```

---

## DISCOVERY TIMELINE (for future reference)

1. wstoken 403 from Tokyo VPS → initially blamed on cookies
2. Found: init_session() overwrites good disk cookies with partial login set
3. Fixed overwrite bug → still 403 → deeper investigation needed
4. Discovered: auth-token returns LOGIN_BLOCKED from datacenter IPs
5. Tested: Vultr Tokyo, Korea, Google Cloud → all blocked
6. Reverse-engineered browser flow: pre-auth → JS → auth-token → cookies
7. Found: browser uses X-App-Data header with _ulp, not just cookies
8. Decoded JS bundle: loginByToken(), handleResponseSignIn(), STORAGE_KEY
9. Confirmed: no way around auth-token — it's the only path to _ulp
10. Tested residential proxy (Webshare Amsterdam) → AUTHENTICATED ✅
11. Tested Vultr Amsterdam → AUTHENTICATED ✅
12. Final: Amsterdam VPS = 1ms latency + unblocked auth = perfect deployment

---

## THE GOLDEN RULE

If the bot can authenticate once, the heartbeat keeps it alive forever.
auth-token is the only gate. Everything after it is immortal.
Choose a VPS where auth-token works, and you never need Mac again.

## EVERY AI SESSION MUST READ THIS BEFORE TOUCHING AUTH CODE.
## EVERY VPS MIGRATION MUST TEST auth-token FIRST.
## NEVER USE form-encoded OR empty body FOR auth-token.
