# VPS SESSION IMMORTALITY — Complete Deployment & Operations Guide
# How CleanFlowBot runs forever on VPS without browser refreshes
# Created after cracking the VPS wstoken 403 bug (April 16 2026)
#
# ANY AI SESSION DEPLOYING TO VPS READS THIS FIRST.
# Location: ~/VibeCoding/ProjectBot/CleanFlowBot-clean/docs/VPS_SESSION_IMMORTALITY.md

---

## THE ARCHITECTURE IN 30 SECONDS

Mac is the cookie factory. VPS is the runtime.

```
Mac (cold start only):
  refresh_vodds.py → Playwright → vodds.com login → whitelabel pre-auth
  → Captures: wstoken + 32 cookies + whitelabel domain
  → Saves: data/auth/ws_token.json + data/auth/cookie.json

Mac (background):
  ps_token_pusher.py → watches ws_token.json age
  → If token > 300s old → re-runs refresh_vodds.py
  → SCP fresh token + cookies to VPS every cycle

VPS (runs forever):
  main.py → loads cookies from disk → heartbeat keeps them alive
  → fetch_token() reads ws_token.json from DISK (not REST)
  → WS connects → streams FULL_ODDS → bot trades
```

---

## WHY VPS CANNOT DO ITS OWN AUTH

Three things the VPS cannot do:

1. **Playwright login on vodds.com** — Cloudflare blocks headless browsers from VPS IPs
2. **wstoken REST endpoint** — returns 403 from VPS IP regardless of cookies
3. **Full whitelabel pre-auth** — the `/member/sport/pin/login` → `loginUrl` → navigate chain requires a real browser

The VPS CAN do `_login_vodds()` via curl_cffi (gets VSESS2 + _ulp), but this produces only ~10 cookies. The wstoken endpoint needs all 32 cookies from the full Playwright flow.

**The solution**: Mac captures everything, pushes to VPS. VPS just loads and maintains.

---

## THE 5 COMMANDMENTS (unchanged from original)

### 1. ONE curl_cffi session for EVERYTHING
- ALL HTTP calls use `curl_cffi.requests.AsyncSession(impersonate="chrome120")`
- NEVER use `aiohttp`, `urllib`, `requests` for PS3838/whitelabel calls
- aiohttp is OK for SSE server (port 8889) and etop calls ONLY

### 2. NEVER fetch wstoken via REST from VPS
- VPS IP is blocked from wstoken endpoint (always 403)
- fetch_token() reads ws_token.json from DISK (pushed by Mac)
- Token file age < 540s = valid, use it
- Token file age > 540s = stale, wait for Mac pusher to refresh

### 3. Heartbeat runs FOREVER using curl_cffi
- POST `{wl_host}/member-service/v2/account-balance` (MUST be POST not GET)
- GET `{wl_host}/member-auth/v2/keep-alive`
- GET `{wl_host}/member-service/v2/system/status`
- Every 5-10 seconds, starts BEFORE WS connects
- Set-Cookie from responses keeps cookies alive forever

### 4. PONG with `destination="ALL"` on every PING
- Server sends PING every ~60 seconds
- Wrong/missing PONG = WS death at 6 minutes
- Correct: `{"type":"PONG","destination":"ALL"}`

### 5. Save live cookies to disk every 5 minutes
- session.cookies → cookie.json
- Only save when heartbeat is healthy (status=200)
- On restart, load from disk — session resumes if < 30 min old

---

## COOKIE OVERWRITE BUG (THE LESSON)

### What was killing VPS sessions:

```
1. init_session() loads 32 cookies from disk     ✅ (all 32 from Mac)
2. _login_vodds() fires immediately              ❌ (OVERWRITES jar)
3. Vodds curl_cffi login captures ~10 cookies    ❌ (partial set)
4. wstoken needs all 32 → only sees 10 → 403     ❌
```

### The fix (now in ps_auth.py):

```
1. init_session() loads 32 cookies from disk     ✅
2. Check: _ulp present in loaded cookies?
   ├── YES → _raw_fetch_token() from disk        ✅ (skip login entirely)
   │         wstoken from disk file, not REST
   └── NO  → _login_vodds() as fallback          (only when cookies missing)
```

### RULE: Never overwrite good disk cookies with a partial login set.

---

## COMPLETE FILE MAP

### Mac files:
```
data/refresh_vodds.py          → Playwright login, captures all cookies + token
tools/ps_token_pusher.py       → Background: watches token age, auto-refreshes, SCP to VPS
data/auth/cookie.json          → 32 cookies (Mac-generated, pushed to VPS)
data/auth/ws_token.json        → wstoken + wl_domain + ws_url + saved_at
data/auth/credentials.json     → Vodds username/password
```

### VPS files:
```
/opt/bot/                      → Bot root (CleanFlowBot-clean deployed here)
/opt/bot/data/auth/cookie.json → Pushed from Mac (loaded at startup + saved every 5 min)
/opt/bot/data/auth/ws_token.json → Pushed from Mac (read by fetch_token())
/opt/bot/data/log/bot.log      → Bot log (written by logger.py, NOT systemd)
/opt/botenv/                   → Python venv with curl_cffi
```

### VPS systemd:
```
/etc/systemd/system/cleanflowbot.service
  WorkingDirectory=/opt/bot
  ExecStart=/opt/botenv/bin/python3 main.py
  StandardOutput=journal        ← NOT append to bot.log (logger.py writes its own)
  StandardError=journal
```

---

## DEPLOYMENT PROCEDURE (from scratch)

### 1. VPS Setup

```bash
ssh root@45.32.25.201

# Directory
mkdir -p /opt/bot/data/auth /opt/bot/data/log

# Python venv
apt update && apt install -y python3-pip python3-venv
python3 -m venv /opt/botenv
source /opt/botenv/bin/activate

# Critical: curl_cffi FIRST
pip install curl_cffi
pip install websockets aiohttp aiofiles

# Verify
python3 -c "from curl_cffi.requests import AsyncSession; print('curl_cffi OK')"
```

### 2. Deploy Code (from Mac)

```bash
cd ~/VibeCoding/ProjectBot/CleanFlowBot-clean

rsync -avz --exclude='__pycache__' --exclude='.git' --exclude='node_modules' \
  --exclude='*.pyc' --exclude='data/auth/' \
  ./ root@45.32.25.201:/opt/bot/
```

### 3. Mac Cold Start

```bash
cd ~/VibeCoding/ProjectBot/CleanFlowBot-clean
python3 data/refresh_vodds.py
```

Wait for output:
```
[VODDS] Token: AAAAAAU_AEcAAAGd...
[VODDS] WS URL: wss://uyfnltp.XXXXX.com/sports-websocket/ws?token=...
[VODDS] Saved cookie.json (32 cookies)
```

### 4. Push Auth to VPS

```bash
scp data/auth/ws_token.json data/auth/cookie.json \
  root@45.32.25.201:/opt/bot/data/auth/
```

### 5. Create Systemd Service

```bash
ssh root@45.32.25.201 'cat > /etc/systemd/system/cleanflowbot.service << EOF
[Unit]
Description=CleanFlowBot Clean
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bot
ExecStart=/opt/botenv/bin/python3 main.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable cleanflowbot'
```

**IMPORTANT**: StandardOutput=journal, NOT append to bot.log. The bot's logger.py writes its own bot.log. Having both = double lines.

### 6. Verify Config

```bash
ssh root@45.32.25.201 "cd /opt/bot && \
  python3 -c \"import json; d=json.load(open('data/bot_config.json')); print('PS_PROVIDER:', d.get('PS_PROVIDER'))\" && \
  python3 -c \"import json; d=json.load(open('data/auth/ws_token.json')); print('wl_domain:', d.get('wl_domain')); print('token age:', int(__import__('time').time()-d.get('saved_at',0)), 's')\" && \
  python3 -c \"import json; d=json.load(open('data/auth/cookie.json')); print('cookies:', len(d.get('cookie','').split(';')), 'pairs')\""
```

Expected:
```
PS_PROVIDER: vodds
wl_domain: https://uyfnltp.XXXXX.com
token age: <300 s
cookies: 32 pairs
```

### 7. Open Ports

```bash
ssh root@45.32.25.201 "ufw allow 8888/tcp && ufw allow 8889/tcp"
```

### 8. Start

```bash
ssh root@45.32.25.201 "systemctl start cleanflowbot"
```

### 9. Start Mac Token Pusher

```bash
cd ~/VibeCoding/ProjectBot/CleanFlowBot-clean
nohup python3 tools/ps_token_pusher.py > /tmp/token_pusher.log 2>&1 &
```

This runs in background on Mac, auto-refreshes token via refresh_vodds.py when age > 300s, SCPs to VPS.

---

## PRE-FLIGHT AUDIT (run before every deploy)

```bash
cd /opt/bot

# 1. No aiohttp for PS calls
grep -rn "aiohttp" feeds/ps_auth.py feeds/ps3838_auth.py feeds/ps3838_ws.py feeds/ps3838_rest.py main.py | grep -v "SSE\|sse\|8889\|etop\|#"
# Expected: NOTHING

# 2. No hardcoded pinnacle888.com URLs
grep -rn "pinnacle888" --include="*.py" . | grep -v "comment\|#\|cookie.*domain"
# Expected: NOTHING (cookie domain references OK)

# 3. No stale config imports
grep -rn "from config import.*PS_BASE_URL\|from config import.*PS_WS_URL" --include="*.py" .
# Expected: NOTHING

# 4. No status_code with curl_cffi
grep -rn "status_code" feeds/ps_auth.py feeds/ps3838_auth.py
# Expected: NOTHING (must use resp.status)

# 5. curl_cffi impersonate set
grep -rn "impersonate" feeds/ps_auth.py feeds/ps3838_auth.py
# Expected: impersonate="chrome120"

# 6. PONG format correct
grep -rn "PONG" feeds/ps3838_ws.py
# Expected: {"type":"PONG","destination":"ALL"}

# 7. account-balance is POST
grep -rn "account-balance" main.py feeds/ps_auth.py
# Expected: POST method

# 8. fetch_token reads disk first
grep -n "ws_token.json\|disk.*token\|_raw_fetch" feeds/ps_auth.py
# Expected: disk cache check before any REST call
```

---

## HEALTH CHECKS

### Immediate (first 2 minutes):
```bash
# Single log lines (no duplicates)
journalctl -u cleanflowbot -n 5 --no-pager

# Heartbeat
ssh root@45.32.25.201 "grep 'heartbeat' /opt/bot/data/log/bot.log | tail -3"
# Expected: status=200 bal=200 ka=200

# WS connected
ssh root@45.32.25.201 "grep 'Connected\|FULL_ODDS' /opt/bot/data/log/bot.log | tail -3"

# No 403s
ssh root@45.32.25.201 "grep '403' /opt/bot/data/log/bot.log | tail -5"
# Expected: NOTHING (after initial startup)
```

### Ongoing (every few hours):
```bash
# Cookie freshness (should update every 5 min while running)
ssh root@45.32.25.201 "ls -la /opt/bot/data/auth/cookie.json"

# Token freshness (should update via Mac pusher)
ssh root@45.32.25.201 "python3 -c \"import json,time; d=json.load(open('/opt/bot/data/auth/ws_token.json')); print('age:', int(time.time()-d.get('saved_at',0)), 's')\""

# WS still streaming
ssh root@45.32.25.201 "grep 'FULL_ODDS\|UPDATE_ODDS' /opt/bot/data/log/bot.log | tail -1"

# Panel accessible
curl -s http://45.32.25.201:8888 | head -1
```

---

## TROUBLESHOOTING TREE

```
wstoken 403?
├── Is bot reading from DISK (ws_token.json)?
│   ├── YES, but token age > 540s → Mac pusher not running. Start ps_token_pusher.py on Mac.
│   ├── YES, token fresh → Bug in fetch_token(). Should return disk token, not hit REST.
│   └── NO, hitting REST endpoint → fetch_token() missing disk-first logic. Apply the fix.
│
Heartbeat 403?
├── Using curl_cffi? (not aiohttp/requests)
│   ├── NO → THAT'S THE BUG. Must be curl_cffi with impersonate="chrome120".
│   └── YES → Cookies expired. Check cookie.json age.
│       ├── < 30 min old → Shouldn't expire. Check cookie domain matches whitelabel.
│       └── > 30 min old → Bot wasn't saving cookies. Check save_cookies_to_disk().
│
WS recv() timeout (connected, no data)?
└── Token stale or reused. Must come fresh from ws_token.json (pushed by Mac).

WS dies at 6 minutes?
└── PONG format wrong. Must be {"type":"PONG","destination":"ALL"}.

MULTIPLE_LOGIN?
└── Two sessions on same account. Kill old bot. Check: ps aux | grep python.

Double log lines?
└── systemd AND logger.py both writing to bot.log.
    Fix: systemd StandardOutput=journal (let logger.py handle bot.log).

Bot starts but no matches?
└── Check etop session (data/auth/session.json) and PS subscription sports.

Cookie overwrite on restart?
└── init_session() calling _login_vodds() before testing disk cookies.
    Fix: check _ulp in loaded cookies → _raw_fetch_token() from disk → skip login if OK.
```

---

## WHITELABEL DOMAIN ROTATION

Domains rotate between sessions (set during Mac cold start):
- `uyfnltp.lenvora8.com`
- `uyfnltp.auremi88.com`
- `uyfnltp.eviran66.com`
- `uyfnltp.mervani99.com`

Domain is stored in `ws_token.json` `wl_domain` field. Read dynamically, NEVER hardcode.
Domain does NOT change during a session. Cookie domain = TLD (e.g. `.auremi88.com`).

---

## RECONNECT FLOW (VPS-specific)

```
WS drops (network blip, server restart)
  │
  ├─ Bot enters REST-only mode (still gets odds via REST compact endpoints)
  │
  ├─ Heartbeat CONTINUES (keeps cookies alive)
  │
  └─ After REST_ONLY_COOLDOWN (300s):
     │
     ├─ fetch_token() → reads ws_token.json from DISK
     │  │
     │  ├─ Token fresh (< 540s) → use it → connect WS → streaming ✅
     │  │
     │  └─ Token stale (> 540s) → wait for Mac pusher to refresh
     │     │
     │     ├─ Mac pusher alive → new token arrives via SCP → next cycle works
     │     │
     │     └─ Mac pusher dead → REST-only mode continues
     │        → Start ps_token_pusher.py on Mac manually
     │
     └─ WS connected → subscriptions sent → FULL_ODDS flows
```

---

## OPERATIONS CHEAT SHEET

```
Start bot:          systemctl start cleanflowbot
Stop bot:           systemctl stop cleanflowbot
Restart bot:        systemctl restart cleanflowbot
Bot logs:           tail -f /opt/bot/data/log/bot.log
Systemd logs:       journalctl -u cleanflowbot -f
Bot status:         systemctl status cleanflowbot

Start Mac pusher:   nohup python3 tools/ps_token_pusher.py > /tmp/token_pusher.log 2>&1 &
Mac pusher logs:    cat /tmp/token_pusher.log
Kill Mac pusher:    pkill -f ps_token_pusher

Fresh cold start:   python3 data/refresh_vodds.py (Mac only)
Push auth to VPS:   scp data/auth/{ws_token,cookie}.json root@45.32.25.201:/opt/bot/data/auth/

Token age check:    python3 -c "import json,time; d=json.load(open('/opt/bot/data/auth/ws_token.json')); print(int(time.time()-d.get('saved_at',0)), 's')"
Cookie count:       python3 -c "import json; d=json.load(open('/opt/bot/data/auth/cookie.json')); print(len(d.get('cookie','').split(';')))"

Panel:              http://45.32.25.201:8888
SSE:                http://45.32.25.201:8889/sse
```

---

## DEATH CONDITIONS & RECOVERY

| Condition | Recovery |
|-----------|----------|
| Mac pusher dies → token goes stale | Restart: `nohup python3 tools/ps_token_pusher.py &` |
| Mac sleeps/closes → pusher pauses | Wake Mac, pusher auto-resumes |
| VPS bot crash | systemctl auto-restarts (RestartSec=30). Cookies survive if < 30 min old. |
| Cookies fully expired (bot down hours) | Mac cold start: `refresh_vodds.py` → SCP → restart |
| Vodds account banned | Create new account on vodds.com → update credentials.json → cold start |
| Whitelabel domain changed | Automatic — refresh_vodds.py captures new domain each run |
| VPS IP blocked by Cloudflare | wstoken was always blocked from VPS. Mac pusher handles it. If heartbeat also blocked → new VPS needed. |

---

## PROVEN METRICS

| Metric | Value |
|--------|-------|
| VPS deployment | 108.160.142.44 (Vultr Tokyo) |
| First successful WS stream | April 16 2026 02:04 WIB |
| Cookie count (Mac Playwright) | 32 |
| Cookie count (VPS curl_cffi login) | ~10 (INSUFFICIENT for wstoken) |
| wstoken from VPS REST | ALWAYS 403 (IP blocked) |
| wstoken from disk (Mac-pushed) | 200 ✅ |
| Double-log cause | systemd + logger.py both writing bot.log |
| Token pusher refresh interval | 300s |
| Token validity window | 540s (fetch_token disk cache) |

---

## EVERY DEPLOY SESSION MUST:
## 1. Read THIS file first
## 2. Run the pre-flight audit
## 3. Ensure Mac pusher is running
## 4. NEVER overwrite disk cookies with _login_vodds()
## 5. NEVER try wstoken REST from VPS (always 403)
