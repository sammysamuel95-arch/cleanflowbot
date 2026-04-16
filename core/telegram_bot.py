"""
core/telegram_bot.py — Telegram command interface.

Polls for incoming messages and responds to commands.
Only accepts messages from the configured TELEGRAM_CHAT.

Commands:
  !status   — uptime, fires today, active/fire-zone markets, WS state
  !fired    — last 5 fired markets
  !log [N]  — last N lines of bot.log (default 20, max 50)
  !accounts — vodds account pool status
  !config   — current thresholds and key config values
  !restart  — restart bot process

Push alerts (bot → you, no command needed) stay in core/notifier.py.
This file only handles pull (you → bot).
"""

import asyncio
import os
import sys
import time
import json

from core.notifier import TELEGRAM_TOKEN, TELEGRAM_CHAT
from core.logger import log_info, log_warn

_POLL_URL    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
_SEND_URL    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
_last_update = 0  # last processed update_id

_AUTH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'auth')
_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'log', 'bot.log')

_start_time = time.time()


async def _send(text: str):
    """Send a reply message."""
    try:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession() as s:
            await s.post(_SEND_URL, json={
                "chat_id": TELEGRAM_CHAT,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=10)
    except Exception as e:
        log_warn("TGBOT", f"Send failed: {e}")


async def _handle(cmd: str, args: list, container, inventory):
    """Dispatch command to handler."""
    cmd = cmd.lower().strip()

    if cmd == "!status":
        await _cmd_status(container)
    elif cmd == "!fired":
        await _cmd_fired(container)
    elif cmd == "!log":
        n = int(args[0]) if args and args[0].isdigit() else 20
        await _cmd_log(min(n, 50))
    elif cmd == "!accounts":
        await _cmd_accounts()
    elif cmd == "!config":
        await _cmd_config()
    elif cmd == "!restart":
        await _cmd_restart()
    else:
        await _send(
            "Commands:\n"
            "!status — uptime + fires\n"
            "!fired — last 5 fires\n"
            "!log [N] — last N log lines\n"
            "!accounts — vodds pool\n"
            "!config — thresholds\n"
            "!restart — restart bot"
        )


async def _cmd_status(container):
    uptime_s = int(time.time() - _start_time)
    h, m, s = uptime_s // 3600, (uptime_s % 3600) // 60, uptime_s % 60

    total   = len(container.markets)
    matched = sum(1 for m in container.markets.values() if m.ps_event_id)
    fzone   = sum(1 for m in container.markets.values() if m.state == 'FIRE_ZONE')
    fired   = sum(1 for m in container.markets.values() if m.total_fired > 0)
    fires   = sum(m.total_fired for m in container.markets.values())

    lines = [
        f"<b>✅ Bot Status</b>",
        f"Uptime: {h}h {m}m {s}s",
        f"Markets: {total} total | {matched} matched | {fzone} FIRE_ZONE",
        f"Fired markets: {fired} | Total fires: {fires}",
    ]
    await _send("\n".join(lines))


async def _cmd_fired(container):
    fired = [
        (m.last_fire_at, m.team1, m.team2, m.label, m.total_fired, m.total_value)
        for m in container.markets.values()
        if m.total_fired > 0
    ]
    if not fired:
        await _send("No fires yet this session.")
        return

    fired.sort(key=lambda x: x[0], reverse=True)
    lines = ["<b>🔥 Last fires:</b>"]
    for ts, t1, t2, label, n, val in fired[:5]:
        t = time.strftime("%H:%M:%S", time.localtime(ts))
        lines.append(f"{t} {t1} vs {t2} [{label}] ×{n} val={val:.0f}g")
    await _send("\n".join(lines))


async def _cmd_log(n: int):
    try:
        with open(_LOG_PATH, 'r') as f:
            lines = f.readlines()
        tail = "".join(lines[-n:]).strip()
        # Telegram max 4096 chars
        if len(tail) > 3800:
            tail = "..." + tail[-3800:]
        await _send(f"<pre>{tail}</pre>")
    except Exception as e:
        await _send(f"Log read error: {e}")


async def _cmd_accounts():
    try:
        with open(os.path.join(_AUTH_DIR, 'vodds_accounts.json')) as f:
            db = json.load(f)
        counts = {}
        for a in db['accounts']:
            st = a['status']
            counts[st] = counts.get(st, 0) + 1
        active = next((a['user'] for a in db['accounts'] if a['status'] == 'active'), 'none')
        lines = [
            "<b>📋 Vodds Accounts</b>",
            f"Active: {active}",
        ]
        for st, n in counts.items():
            lines.append(f"{st}: {n}")
        await _send("\n".join(lines))
    except Exception as e:
        await _send(f"Accounts read error: {e}")


async def _cmd_config():
    try:
        import config as cfg
        lines = [
            "<b>⚙️ Config</b>",
            f"DRY_RUN: {cfg.DRY_RUN}",
            f"P1/P2/P3: {cfg.PHASE1_EV}% / {cfg.PHASE2_EV}% / {cfg.PHASE3_EV}%",
            f"TRIGGER: {cfg.TRIGGER_SECS}s",
            f"MIN_RAW_POOL: {cfg.MIN_RAW_POOL}g",
            f"HARD_CAP: {cfg.HARD_CAP}g",
            f"MAX_ODDS: {cfg.MAX_ODDS}",
            f"MAX_ITEMS: {cfg.MAX_ITEMS}",
            f"COOLDOWN: {cfg.FIRE_SAME_MKT_COOLDOWN_MS}ms",
        ]
        await _send("\n".join(lines))
    except Exception as e:
        await _send(f"Config read error: {e}")


async def _cmd_restart():
    await _send("♻️ Restarting bot...")
    log_info("[TGBOT] Restart command received — restarting process")
    await asyncio.sleep(1)
    os.execv(sys.executable, [sys.executable] + sys.argv)


async def health_watchdog_loop(container):
    """Send a health ping every 30 minutes. Alert if silent (no fires) while FIRE_ZONE markets exist."""
    INTERVAL = 30 * 60          # 30 min normal ping
    SILENT_THRESHOLD = 4 * 3600 # alert if no fires for 4h with FIRE_ZONE markets
    _last_fire_ts = [0.0]       # tracks last known fire time

    await asyncio.sleep(60)     # wait 1 min after startup before first ping
    log_info("[WATCHDOG] Health watchdog started")

    while True:
        try:
            uptime_s = int(time.time() - _start_time)
            h, m, s = uptime_s // 3600, (uptime_s % 3600) // 60, uptime_s % 60

            total   = len(container.markets)
            matched = sum(1 for mk in container.markets.values() if mk.ps_event_id)
            fzone   = sum(1 for mk in container.markets.values() if mk.state == 'FIRE_ZONE')
            fires   = sum(mk.total_fired for mk in container.markets.values())

            # Update last known fire time
            for mk in container.markets.values():
                if mk.total_fired > 0 and mk.last_fire_at > _last_fire_ts[0]:
                    _last_fire_ts[0] = mk.last_fire_at

            # Silent alert: FIRE_ZONE markets exist but no fires in 4h
            silent_secs = time.time() - _last_fire_ts[0] if _last_fire_ts[0] > 0 else uptime_s
            silent_alert = fzone > 0 and silent_secs > SILENT_THRESHOLD

            if silent_alert:
                lines = [
                    f"⚠️ <b>SILENT ALERT</b>",
                    f"No fires for {int(silent_secs/3600)}h {int((silent_secs%3600)/60)}m",
                    f"But {fzone} FIRE_ZONE markets exist — check bot!",
                    f"Uptime: {h}h {m}m {s}s | Markets: {total} matched: {matched}",
                ]
            else:
                lines = [
                    f"💓 <b>Health ping</b>",
                    f"Uptime: {h}h {m}m {s}s",
                    f"Markets: {total} | matched: {matched} | FIRE_ZONE: {fzone}",
                    f"Total fires this session: {fires}",
                ]

            await _send("\n".join(lines))

        except Exception as e:
            log_warn("WATCHDOG", f"Ping failed: {e}")

        await asyncio.sleep(INTERVAL)


async def poll_loop(container, inventory):
    """Long-poll Telegram for incoming commands. Runs forever."""
    global _last_update
    log_info("[TGBOT] Telegram command bot started")

    while True:
        try:
            from curl_cffi.requests import AsyncSession
            async with AsyncSession() as s:
                r = await s.get(_POLL_URL, params={
                    "offset": _last_update + 1,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                }, timeout=35)

                if r.status_code != 200:
                    await asyncio.sleep(5)
                    continue

                data = r.json()
                for update in data.get("result", []):
                    _last_update = update["update_id"]
                    msg = update.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "").strip()

                    # Only respond to authorized chat
                    if chat_id != TELEGRAM_CHAT:
                        continue
                    if not text.startswith("!"):
                        continue

                    parts = text.split()
                    cmd, args = parts[0], parts[1:]
                    log_info(f"[TGBOT] Command: {text}")
                    asyncio.create_task(_handle(cmd, args, container, inventory))

        except asyncio.CancelledError:
            break
        except Exception as e:
            log_warn("TGBOT", f"Poll error: {e}")
            await asyncio.sleep(10)
