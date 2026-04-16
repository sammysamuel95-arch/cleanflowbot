"""
modules/cancel_engine.py — Cancel bets when market extends.

READS:  container.markets where total_fired > 0
        container.etop listing (current remain)

WRITES: container.markets[mid] — resets fire state
        inventory — unconsumes items

LOGIC:
  Simple: if total_fired > 0 AND remain > 300s → cancel everything.
  The market was extended by a human operator. Our bets are now stale.
  Cancel, reset, recalculate.

TRACKS BY MID:
  Uses mid (etop sub-match ID, NEVER changes) not fire_key (can change
  when line moves). This is the fix for the cancel bug — fire_key changes
  on line change, orphaning the fire state. mid is permanent.

INDEPENDENT:
  Runs on its own 5s loop. Does not depend on brain, fire, or valuator.
  If fire engine crashes, cancel still works.
"""

import asyncio
import config as cfg
from core.logger import log_info, log_warn


async def run_loop(container, etop_api, inventory, listing_getter):
    """Background loop. Checks every 5s for markets that need cancelling.

    listing_getter: callable that returns current etop listing dict.
    We read listing directly (not container.markets.remain) for freshness.
    """
    await asyncio.sleep(10)  # let bot stabilize
    log_info("[CANCEL] Cancel engine started (5s cadence)")

    while True:
        try:
            listing = listing_getter()
            await _cancel_cycle(container, etop_api, inventory, listing)
        except Exception as e:
            log_warn("CANCEL", f"Cycle error: {e}")
        await asyncio.sleep(5)


async def _cancel_cycle(container, etop_api, inventory, listing):
    """One cancel check cycle.

    For every market with total_fired > 0:
      - Read remain from listing (fresh, not cached)
      - If remain > EXTENSION_SECS → cancel all bets, reset everything
      - LOG every fired market's status (not just cancels)
    """
    # Check CLOSED markets that re-appeared in listing (extended after close)
    for mid, m in container.markets.items():
        if m.state == 'CLOSED' and m.total_fired > 0:
            sub = listing.get(mid)
            if sub and int(sub.get('remain', 0)) > 0:
                log_info(f"[CANCEL_REOPEN] {m.team1} vs {m.team2} [{m.label}] "
                         f"mid={mid} was CLOSED but remain={int(sub.get('remain',0))}s — market extended, reopening")
                m.state = 'MONITOR' if m.ps_event_id else 'UNMATCHED'

    fired_markets = [(mid, m) for mid, m in container.markets.items()
                     if m.total_fired > 0 and m.state != 'CLOSED']

    if not fired_markets:
        return

    for mid, m in fired_markets:
        # Read remain from LISTING (fresh truth, not cached m.remain)
        sub = listing.get(mid)
        if not sub:
            # CRITICAL: we have fired bets but can't find this market in listing
            log_warn("CANCEL", f"FIRED BUT MISSING FROM LISTING: "
                     f"{m.team1} vs {m.team2} [{m.label}] "
                     f"mid={mid} fired={m.total_fired} press_ids={m.press_ids}")
            # Try with string version of mid (listing keys might be str or int)
            sub = listing.get(str(mid)) or listing.get(int(mid) if str(mid).isdigit() else mid)
            if not sub:
                continue

        remain = int(sub.get('remain', 0))
        extension_secs = getattr(cfg, 'EXTENSION_SECS', 300)

        # Always log fired market status
        log_info(f"[CANCEL_CHECK] {m.team1} vs {m.team2} [{m.label}] "
                 f"fired={m.total_fired} remain={remain}s threshold={extension_secs}s "
                 f"press_ids={len(m.press_ids)} mid={mid}")

        if remain <= extension_secs:
            continue

        # ── Market extended past threshold with active bets → CANCEL ──
        log_info(f"[CANCEL] {m.team1} vs {m.team2} [{m.label}] "
                 f"EXTENDED remain={remain}s fired={m.total_fired} → cancelling")

        # Step 1: Get cancellable press IDs from server
        try:
            fresh_press_ids = await etop_api.get_cancellable_presses(mid)
            if fresh_press_ids:
                m.press_ids = fresh_press_ids
                log_info(f"[CANCEL] Got {len(fresh_press_ids)} cancellable presses from server")
            else:
                log_warn("CANCEL", f"No cancellable presses from server, using stored: {m.press_ids}")
        except Exception as e:
            log_warn("CANCEL", f"get_cancellable_presses failed: {e}")

        # Step 2: Cancel each press
        cancelled = 0
        for pid in m.press_ids:
            try:
                ok, msg = await etop_api.regret(mid, pid)
                if ok:
                    cancelled += 1
                    log_info(f"[CANCEL] regret OK pid={pid}")
                else:
                    log_warn("CANCEL", f"regret failed pid={pid}: {msg}")
            except Exception as e:
                log_warn("CANCEL", f"regret error pid={pid}: {e}")

        # Step 3: Unconsume items back to inventory
        freed_count = len(m.consumed_item_ids)
        if m.consumed_item_ids:
            inventory.unconsume(m.consumed_item_ids)

        log_info(f"[CANCEL_DONE] {m.team1} vs {m.team2} [{m.label}] "
                 f"cancelled={cancelled}/{len(m.press_ids)} freed={freed_count} items "
                 f"value={m.total_value:.0f}g remain={remain}s — RESETTING TO MONITOR")
        print(f"[CANCEL_DONE] {m.team1} vs {m.team2} [{m.label}] "
              f"cancelled={cancelled} bets, {freed_count} items freed, "
              f"value={m.total_value:.0f}g — market reset, will re-enter fire zone", flush=True)

        # Step 4: Full reset
        m.total_fired = 0
        m.total_value = 0.0
        m.locked_side = 0
        m.consumed_item_ids = []
        m.press_ids = []
        m.value_cap = 0.0
        m.cap_source = ''
        m.remaining_cap = 0.0
        m.raw_pool = 0.0
        m.first_fire_at = 0.0
        m.last_fire_at = 0.0
        m.last_tuhao_at = 0.0  # force fresh tuhao/cap fetch on next fire
        m.last_gate_failures = []

        # Step 5: Reset state → MONITOR (will re-enter pipeline)
        if m.ps_event_id is not None:
            m.state = 'MONITOR'
        else:
            m.state = 'UNMATCHED'
