# TASK LIST — Zero Human Intervention Bot
# Goal: One command deploy, self-sustaining forever.
# Feed this file + the listed context files to Opus per task.
# Do tasks in order — 5 and 6 depend on all previous.

---

## TASK 1: systemd service
**Goal:** Bot auto-restarts on crash, auto-starts on VPS reboot.

**Context needed:** None beyond this file.

**Facts:**
- VPS: 45.32.25.201 (Vultr Tokyo, Ubuntu)
- Bot path: `/opt/bot/`
- Python: `/opt/botenv/bin/python3`
- Start command: `python3 main.py`
- Log output: `data/log/bot_output.log`
- Run as: root

**Deliverable:**
- `/etc/systemd/system/cleanflowbot.service` unit file
- Commands to enable + start: `systemctl enable cleanflowbot && systemctl start cleanflowbot`
- Restart=always, RestartSec=5, WorkingDirectory=/opt/bot

**Verify:**
```bash
systemctl status cleanflowbot
# Should show: active (running)

# Simulate crash:
kill -9 $(pgrep -f main.py)
sleep 6
systemctl status cleanflowbot
# Should show: restarted, new PID
```

---

## TASK 2: logrotate config
**Goal:** Logs never fill the disk.

**Context needed:** None beyond this file.

**Facts:**
- Log files:
  - `/opt/bot/data/log/bot.log`
  - `/opt/bot/data/log/bot_output.log`
- VPS disk: ~80GB, logs grow ~50MB/day

**Deliverable:**
- `/etc/logrotate.d/cleanflowbot` config
- Rotate: daily, keep 7 days, compress, missing ok, notifempty
- copytruncate (so running bot doesn't lose file handle)

**Verify:**
```bash
logrotate -d /etc/logrotate.d/cleanflowbot
# Should show: rotating log, no errors
```

---

## TASK 3: Proxy fallback list
**Goal:** Auth-token never permanently fails if one proxy dies.

**Context files to feed Opus:**
- `docs/HOLY_GRAIL_VPS_SESSION_IMMORTALITY.md` — why proxy is needed for auth-token
- `feeds/ps_auth.py` — FALLBACK_PROXY usage (Step 3 auth-token)
- `data/refresh_vodds.py` — second FALLBACK_PROXY usage

**Facts:**
- Current proxy: `168.199.244.251:80` (Amsterdam Webshare)
- Proxy ONLY needed for Step 3 (auth-token POST) — all other steps use VPS directly
- If proxy fails: auth fails → WS token expires → bot stops firing
- Telegram token: `8205194596:AAFTDOctSeqxsvGplSV0UwbdKiVIvgGLVe0`
- Telegram chat: `8774309371`

**Deliverable:**
- Replace `FALLBACK_PROXY = "single string"` with `FALLBACK_PROXIES = ["proxy1", "proxy2", ...]`
- Try each proxy in order for auth-token step
- If all fail: send Telegram alert "⚠️ All proxies failed — auth blocked"
- Applied in BOTH `feeds/ps_auth.py` AND `data/refresh_vodds.py`

**Verify:**
```bash
# Set first proxy to invalid, run refresh_vodds.py
# Should try next proxy, succeed
# Should NOT crash — should fall through list
```

---

## TASK 4: Health watchdog
**Goal:** Know bot is alive without checking panel. Alert if stalled.

**Context files to feed Opus:**
- `main.py` — where to add background task
- `core/notifier.py` — how Telegram notify works
- `container.py` — where to read fire counts

**Facts:**
- Telegram token: `8205194596:AAFTDOctSeqxsvGplSV0UwbdKiVIvgGLVe0`
- Telegram chat: `8774309371`
- notify() already has 60s debounce — watchdog needs its own send (bypass debounce)
- Bot fires ~10-80 bets per match depending on market
- "Active hours" = any time (esports runs 24/7 globally)

**Deliverable:**
- Background asyncio task in main.py: `_health_watchdog()`
- Every 60 minutes: send "✅ Bot alive | uptime=Xh | fires today=N | markets=M"
- If `container.total_fired_session` hasn't increased in 4 hours AND markets in FIRE_ZONE > 0: send "⚠️ Bot stalled — FIRE_ZONE markets exist but no fires in 4h"
- Track: `last_fire_time` on container, `fires_today` counter (reset at midnight UTC)

**Verify:**
```bash
grep "Bot alive" data/log/bot.log
# Should appear every 60 minutes
```

---

## TASK 5: deploy.sh
**Goal:** One script, fresh VPS → fully running bot in <10 minutes.

**Context files to feed Opus:**
- `docs/MASTER_CONTEXT.md` — full architecture
- `docs/HOLY_GRAIL_VPS_SESSION_IMMORTALITY.md` — auth chain + credential files needed
- `data/auth/credentials.json` — structure (not values) of what needs to be pre-filled
- `data/auth/vodds_accounts.json` — structure
- Tasks 1+2+3+4 must be DONE before writing this script

**Facts:**
- Target OS: Ubuntu 22.04
- Bot repo: local Mac → SCP to VPS (no git on VPS)
- Python env: `/opt/botenv/` (virtualenv)
- Required pip packages: curl_cffi, aiohttp, playwright, thefuzz, python-Levenshtein
- Panel port: 8888, SSE port: 8889
- Must NOT overwrite: `data/auth/credentials.json`, `data/auth/vodds_accounts.json`, `data/auth/ws_token.json`, `data/auth/cookie.json`

**Deliverable:**
- `deploy.sh` in repo root
- Steps:
  1. apt-get install python3-venv python3-pip
  2. Create /opt/bot/ structure
  3. Create /opt/botenv/ virtualenv
  4. pip install all deps
  5. Copy bot files (rsync or scp)
  6. Create systemd service (Task 1)
  7. Create logrotate config (Task 2)
  8. systemctl enable + start
  9. Print: "Deploy done. Bot running. Check: systemctl status cleanflowbot"
- Script is idempotent (safe to run again on existing install)

**Verify:**
```bash
bash deploy.sh
systemctl status cleanflowbot
# Active (running)
```

---

## TASK 6: RUNBOOK.md
**Goal:** Any human can keep this bot running forever with this one doc.

**Context files to feed Opus:**
- `docs/MASTER_CONTEXT.md`
- `docs/HOLY_GRAIL_VPS_SESSION_IMMORTALITY.md`
- `docs/S33_PRIORITIES.md`
- All tasks 1-5 must be DONE first

**Deliverable:** `docs/RUNBOOK.md` covering:

1. **What's autonomous** (list everything bot handles itself)
2. **One-time setup** (deploy.sh, credential files to fill manually)
3. **Daily ops** (check systemctl status, read Telegram — that's it)
4. **Adding vodds accounts** (when all 9 banned, how to register + add to JSON)
5. **Pushing code updates** (SCP which files, restart command)
6. **Telegram alert meanings** (every alert type + what to do)
7. **Only remaining human touchpoint** (register new vodds accounts when pool exhausted)

**Format:** Short, copy-paste commands everywhere. No fluff.

---

## ORDER OF EXECUTION

```
Task 1 (systemd)    ← no dependencies, do first
Task 2 (logrotate)  ← no dependencies, do first
Task 3 (proxy)      ← needs ps_auth.py + refresh_vodds.py
Task 4 (watchdog)   ← needs main.py + notifier.py
Task 5 (deploy.sh)  ← needs 1+2+3+4 done
Task 6 (RUNBOOK)    ← needs everything done
```

Tasks 1+2 can be done in parallel.
Tasks 3+4 can be done in parallel.
Task 5 only after 1+2+3+4.
Task 6 last.
