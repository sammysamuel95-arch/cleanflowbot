"""
modules/fire_engine.py — Execute bets on FIRE_ZONE markets.

READS:  container.markets where state == FIRE_ZONE
        container.etop (fresh listing for real-time odds)
        ps_store (fresh fair odds for real-time EV)
        container.sport_configs (per-sport gates)

WRITES: container.markets[mid].total_fired/total_value/locked_side
        container.markets[mid].consumed_item_ids/press_ids
        container.markets[mid].last_gate_failures
        container.markets[mid].first_fire_at/last_fire_at
        container.markets[mid].state → FIRED

DOES NOT:
  - Match events (matcher's job)
  - Classify markets (classifier's job)
  - Cancel bets (cancel engine's job)

CRITICAL:
  - RECOMPUTES EV at fire time (never uses cached EV from valuator)
  - Reads fresh odds from listing (not from Market.o1/o2 which may be 3s old)
  - All gates must pass before firing
"""

import time
import asyncio
from core.ev import compute_ev
from core.logger import log_info, log_warn, log_fire, log_prefire
import config as cfg


async def run(container, ps_store, etop_api, inventory, listing, pool_estimator=None):
    """One fire cycle. Scans FIRE_ZONE markets, checks gates, fires.

    Called on every etop data arrival (event-driven, ~3s).
    """
    if not listing:
        return

    container.fire_active = True
    try:
        await _run(container, ps_store, etop_api, inventory, listing, pool_estimator)
    finally:
        container.fire_active = False


async def _run(container, ps_store, etop_api, inventory, listing, pool_estimator=None):

    # Capture etop age at pipeline start — don't read container.etop_age
    # during fire loop because poller might update it during awaits
    # while our listing still references the older poll's data
    _etop_ts = container.etop_last_fetch
    etop_age = time.time() - _etop_ts

    candidates = []

    for mid, m in container.markets.items():
        if m.state not in ('FIRE_ZONE', 'FIRED'):
            continue
        if m.ps_event_id is None:
            continue

        # Read FRESH odds from listing (not cached on Market)
        sub = listing.get(mid)
        if not sub:
            continue

        seconds = int(sub.get('remain', 0))
        if seconds <= 0:
            continue

        # ── Per-sport config (from bot_config.json via container) ──
        sport_cfg = container.get_sport_config(m.sport)
        trigger = sport_cfg.trigger_secs

        # ── GATE 0: remain must be inside fire zone ──
        # If etop extended the market (remain jumped above trigger), kick back
        if seconds > trigger:
            m.state = 'MONITOR'
            m.phase = ''
            continue

        # ── Compute FRESH phase from fresh remain ──
        # P1: trigger to trigger×2/3 (conservative, high threshold)
        # P2: trigger×2/3 to trigger/3 (medium)
        # P3: trigger/3 to 0 (aggressive, capture volume)
        p2_boundary = trigger * 2 / 3
        p3_boundary = trigger / 3

        if seconds > p2_boundary:
            fresh_phase = 'P1'
            fresh_min_ev = sport_cfg.phase1_ev
        elif seconds > p3_boundary:
            fresh_phase = 'P2'
            fresh_min_ev = sport_cfg.phase2_ev
        else:
            fresh_phase = 'P3'
            fresh_min_ev = sport_cfg.phase3_ev

        # Update market for dashboard accuracy
        m.phase = fresh_phase
        m.phase_min_ev = fresh_min_ev

        o1 = sub['o1']
        o2 = sub['o2']

        # RECOMPUTE EV with latest data
        em = container._etop_markets.get(mid)
        if not em:
            continue

        # Temporarily update em with fresh listing odds for accurate EV
        em.update_odds(o1, o2, sub['remain'], sub['can_press'])
        ev1, ev2 = compute_ev(em, ps_store)

        # Alt-eids fallback (mk=3 map markets — compute_ev also tries these internally)
        if ev1 is None and em.ps_event_id and em.ps_name_team1:
            original_eid = em.ps_event_id
            try:
                # Alt-eids (mk=3 map markets)
                alt_eids = ps_store.find_alternate_eids(original_eid, em.ps_name_team1)
                for alt_eid in alt_eids:
                    em.ps_event_id = alt_eid
                    ev1, ev2 = compute_ev(em, ps_store)
                    if ev1 is not None:
                        break
                    em.ps_event_id = original_eid
            finally:
                em.ps_event_id = original_eid

        if ev1 is None:
            continue

        # Determine side FIRST (locked takes priority)
        if m.locked_side > 0:
            side = m.locked_side
        else:
            side = 1 if ev1 >= ev2 else 2

        # Use the ACTUAL side's EV for all gates
        side_ev = ev1 if side == 1 else ev2
        ps_age = m.ps_age  # already computed by valuator with resolved_eid

        # ── GATES (all fire decisions in ONE place) ──
        max_odds = max(o1, o2)
        gates = [
            (o1 > 0 and o2 > 0,                                    "no_odds"),
            (max_odds <= cfg.MAX_ODDS,                              f"lopsided={max_odds:.2f}>{cfg.MAX_ODDS}"),
            (side_ev > fresh_min_ev,                                f"ev={side_ev:+.1f}%<{fresh_phase}:{fresh_min_ev}%"),
            (ps_age is not None and ps_age < sport_cfg.max_ps_age,  f"ps_stale={ps_age or 'N/A'}>{sport_cfg.max_ps_age}s"),
            (m.remaining_cap > 0 or m.value_cap == 0,               f"cap_full"),
            (m.total_fired < cfg.MAX_ITEMS,                         f"max_items={m.total_fired}/{cfg.MAX_ITEMS}"),
        ]
        failed = [reason for passed, reason in gates if not passed]
        m.last_gate_failures = failed

        if failed:
            log_info(f"[FIRE_SKIP] {m.team1} vs {m.team2} [{m.label}] "
                     f"{fresh_phase} remain={seconds}s ev={side_ev:+.1f}% blocked: {', '.join(failed)}")
            continue

        candidates.append((mid, m, ev1, ev2, side, o1, o2, seconds, fresh_min_ev, fresh_phase, side_ev))

    if not candidates:
        return

    # ── Priority scoring (restored from old strategy.py) ──
    # priority = urgency × EV × cap_factor
    #   urgency: closer to close = fire first (time is running out)
    #   EV: higher edge = more profitable
    #   cap_factor: more room to bet = more value to extract
    # Urgency grouping: markets within ±2s = same urgency tier

    def _priority_score(c):
        _mid, _m, _ev1, _ev2, _side, _o1, _o2, _secs = c[:8]
        best_ev = max(_ev1, _ev2)
        trigger = getattr(cfg, 'TRIGGER_SECS', 90)
        urgency = max(1.0, trigger - _secs)

        # Cap factor: more room to bet = higher priority
        if _m.raw_pool > 0:
            cap_factor = 1000.0 / max(_m.raw_pool, 100)
        elif _m.remaining_cap > 0:
            cap_factor = _m.remaining_cap / max(getattr(cfg, 'HARD_CAP', 100), 1)
        else:
            cap_factor = 1.0

        return urgency * best_ev * cap_factor

    def _urgency_group_sort(cands):
        """Group by urgency (±2s), sort by priority within each group.
        Most urgent group first. Within group: highest score first."""
        if not cands:
            return []
        by_time = sorted(cands, key=lambda c: c[7])  # sort by remain (closest first)
        groups = [[by_time[0]]]
        for c in by_time[1:]:
            if c[7] - groups[-1][0][7] <= 2.0:
                groups[-1].append(c)
            else:
                groups.append([c])
        ordered = []
        for group in groups:
            group.sort(key=_priority_score, reverse=True)
            ordered.extend(group)
        return ordered

    candidates = _urgency_group_sort(candidates)

    # ── Fire with per-market cooldown ──────────────────────────
    # ALL ready markets fire in one cycle (fast across markets)
    # SAME market respects cooldown (fresh odds between its fires)
    # Configurable via bot_config.json

    cooldown_sec = getattr(cfg, 'FIRE_SAME_MKT_COOLDOWN_MS', 400) / 1000.0
    api_gap_sec = getattr(cfg, 'FIRE_API_GAP_MS', 50) / 1000.0
    now = time.time()
    fired_this_cycle = 0

    for mid, m, ev1, ev2, side, o1, o2, seconds, fresh_min_ev, fresh_phase, side_ev in candidates:
        em = container._etop_markets.get(mid)
        if not em:
            continue

        # ── 1. Per-market cooldown ──
        if m.last_fire_at > 0 and (now - m.last_fire_at) < cooldown_sec:
            continue

        # ── 2. Tuhao (10s cache — data only updates every 10-20s) ──
        tuhao_ms = 0
        tuhao_refresh = getattr(cfg, 'TUHAO_REFRESH_SECS', 10)
        if time.time() - m.last_tuhao_at >= tuhao_refresh:
            tuhao_ts = time.time()
            if pool_estimator and pool_estimator._loaded:
                try:
                    fresh_pool = await pool_estimator.estimate_pool(etop_api, mid)
                    m.last_tuhao_at = time.time()

                    if fresh_pool > 0 and abs(fresh_pool - m.raw_pool) > 1:
                        old_pool = m.raw_pool
                        m.raw_pool = fresh_pool
                        m.value_cap = pool_estimator.calc_value_cap(
                            fresh_pool, cfg.MAX_POOL_IMPACT, cfg.HARD_CAP)
                        m.cap_source = 'TUHAO_LIVE'
                        m.remaining_cap = m.value_cap - m.total_value
                        log_info(f"[TUHAO_UPDATE] {m.team1} vs {m.team2} [{m.label}] "
                                 f"mid={mid} pool={old_pool:.0f}→{fresh_pool:.0f}g "
                                 f"cap={m.value_cap:.0f}g remain={seconds}s")
                    elif fresh_pool > 0 and m.raw_pool == 0:
                        # First time seeing pool data
                        m.raw_pool = fresh_pool
                        m.value_cap = pool_estimator.calc_value_cap(
                            fresh_pool, cfg.MAX_POOL_IMPACT, cfg.HARD_CAP)
                        m.cap_source = 'TUHAO_LIVE'
                        m.remaining_cap = m.value_cap - m.total_value
                        log_info(f"[TUHAO_FIRST] {m.team1} vs {m.team2} [{m.label}] "
                                 f"mid={mid} pool={fresh_pool:.0f}g "
                                 f"cap={m.value_cap:.0f}g remain={seconds}s")
                    elif fresh_pool == 0 and m.value_cap == 0:
                        m.value_cap = cfg.HARD_CAP
                        m.raw_pool = 0
                        m.cap_source = 'HARD_CAP'
                        m.remaining_cap = m.value_cap - m.total_value
                except Exception as e:
                    log_warn("TUHAO_LIVE", f"Failed {mid}: {e}")
                    m.last_tuhao_at = time.time()  # don't retry immediately
                    if m.value_cap == 0:
                        m.value_cap = cfg.HARD_CAP
                        m.remaining_cap = m.value_cap - m.total_value
                        m.cap_source = 'HARD_CAP'
            tuhao_ms = (time.time() - tuhao_ts) * 1000

            # 100ms gap after tuhao API call (rate control)
            await asyncio.sleep(0.1)

        # Pool + cap gate
        if m.raw_pool > 0 and m.raw_pool < cfg.MIN_RAW_POOL:
            log_info(f"[FIRE_SKIP] {m.team1} vs {m.team2} [{m.label}] "
                     f"pool={m.raw_pool:.0f}<{cfg.MIN_RAW_POOL} (tuhao)")
            continue
        if m.remaining_cap <= 0 and m.value_cap > 0:
            log_info(f"[FIRE_SKIP_CAP] {m.team1} vs {m.team2} [{m.label}] cap_full remain={seconds}s")
            continue

        # ── 3. Fresh match_list — freshest odds for ALL markets ──
        list_ts = time.time()
        try:
            _, fresh_listing = await etop_api.match_list()
            container.etop_last_fetch = time.time()
        except Exception as e:
            log_warn("FIRE_LIST", f"match_list failed: {e}")
            fresh_listing = listing  # fallback to cycle's listing
        list_ms = (time.time() - list_ts) * 1000

        # ── 4. Recompute EV with FRESHEST data ──
        ev_ts = time.time()
        sub = fresh_listing.get(mid)
        if not sub:
            sub = listing.get(mid)
        if not sub:
            log_info(f"[FIRE_SKIP_NOSUB] {m.team1} vs {m.team2} [{m.label}] mid={mid} not in fresh listing")
            continue
        fresh_o1 = sub.get('o1', 0)
        fresh_o2 = sub.get('o2', 0)
        fresh_remain = int(sub.get('remain', 0))
        if fresh_remain <= 0 or fresh_o1 <= 0 or fresh_o2 <= 0:
            log_info(f"[FIRE_SKIP_ODDS] {m.team1} vs {m.team2} [{m.label}] o1={fresh_o1} o2={fresh_o2} remain={fresh_remain}")
            if fresh_remain <= 0:
                m.state = 'CLOSED'
                m.locked_at = time.time()
            continue

        em.update_odds(fresh_o1, fresh_o2, sub['remain'], sub.get('can_press', True))
        ev1, ev2 = compute_ev(em, ps_store)

        # Alt-eids fallback (mk=3 map markets — compute_ev also tries these internally)
        if ev1 is None and em.ps_event_id and em.ps_name_team1:
            original_eid = em.ps_event_id
            try:
                alt_eids = ps_store.find_alternate_eids(original_eid, em.ps_name_team1)
                for alt_eid in alt_eids:
                    em.ps_event_id = alt_eid
                    ev1, ev2 = compute_ev(em, ps_store)
                    if ev1 is not None:
                        break
                    em.ps_event_id = original_eid
            finally:
                em.ps_event_id = original_eid

        if ev1 is None:
            log_info(f"[FIRE_EV_NONE] {m.team1} vs {m.team2} [{m.label}] "
                     f"mid={mid} remain={fresh_remain}s "
                     f"eid={em.ps_event_id} t1={em.ps_name_team1} t2={em.ps_name_team2} "
                     f"market={em.market} map={em.map_num} line={em.line}")
            continue

        best_ev = max(ev1, ev2)
        side = 1 if ev1 >= ev2 else 2
        ps_age = ps_store.get_line_age(em.ps_event_id, em.map_num, em.market)
        ev_ms = (time.time() - ev_ts) * 1000

        # ── 4. Gate check (fresh EV + fresh pool — ALL computed above) ──
        sport_cfg = container.get_sport_config(m.sport)
        etop_age = time.time() - _etop_ts  # from captured timestamp, not container

        gates = [
            (fresh_o1 > 0 and fresh_o2 > 0,                            "no_odds"),
            (best_ev > m.phase_min_ev,                                  f"ev={best_ev:+.1f}%<{m.phase}:{m.phase_min_ev}%"),
            (etop_age < cfg.MAX_ETOP_AGE,                               f"etop_age={etop_age:.1f}s"),
            (ps_age is not None and ps_age < sport_cfg.max_ps_age,      f"ps_age={ps_age:.1f}s"),
            (m.raw_pool >= cfg.MIN_RAW_POOL,                            f"pool={m.raw_pool:.0f}<{cfg.MIN_RAW_POOL}"),
            (m.remaining_cap > 0 or m.value_cap == 0,                   f"cap_full"),
            (m.total_fired < cfg.MAX_ITEMS,                             f"max_items={m.total_fired}/{cfg.MAX_ITEMS}"),
        ]
        failed = [reason for passed, reason in gates if not passed]
        m.last_gate_failures = failed

        if failed:
            log_info(f"[FIRE_SKIP] {m.team1} vs {m.team2} [{m.label}] "
                     f"ev={best_ev:+.1f}% remain={fresh_remain}s blocked: {', '.join(failed)}")
            continue

        # ── Locked side enforcement ──
        if m.locked_side > 0:
            side = m.locked_side
            locked_ev = ev1 if side == 1 else ev2
            if locked_ev < m.phase_min_ev:
                log_prefire(f"{m.team1} vs {m.team2} [{m.label}]",
                           f"SKIP: locked_side={side} ev={locked_ev:.2f}% flipped")
                continue

        # ── Pick item from inventory ──
        remaining_cap = m.value_cap - m.total_value if m.value_cap > 0 else float('inf')
        item_id = inventory.get_next_item(remaining_cap)
        if item_id is None:
            log_prefire(f"{m.team1} vs {m.team2} [{m.label}]",
                       f"NO_ITEM remaining_cap={remaining_cap:.1f}")
            continue

        item_value = inventory.get_item_value(item_id)

        if m.total_fired == 0:
            m.first_fire_at = time.time()

        # Log priority + freshness (AUDIT TRAIL)
        score = _priority_score((mid, m, ev1, ev2, side, fresh_o1, fresh_o2, fresh_remain))
        tuhao_str = f"tuhao={tuhao_ms:.0f}ms" if tuhao_ms > 0 else "tuhao=cached"
        log_info(f"[FIRE_READY] {m.team1} vs {m.team2} [{m.label}] "
                 f"ev={best_ev:+.2f}% pool={m.raw_pool:.0f}g cap={m.remaining_cap:.0f}g "
                 f"remain={fresh_remain}s priority={score:.1f} "
                 f"freshness: {tuhao_str} list={list_ms:.0f}ms ev={ev_ms:.0f}ms ps_age={ps_age}s")

        # ── 5. FIRE ──
        if cfg.DRY_RUN:
            side_name = 'team1' if side == 1 else 'team2'
            log_fire(f"{m.team1} vs {m.team2} [{m.label}]",
                    m.total_fired, "DRY_RUN",
                    side=side_name, ev=f"{best_ev:+.2f}%",
                    remain=f"{fresh_remain}s")
            continue

        success, msg, press_id = await etop_api.press(em.mid, [item_id], side)

        if success:
            inventory.consume([item_id])
            m.consumed_item_ids.append(item_id)
            m.total_fired += 1
            m.total_value += item_value
            m.last_fire_at = time.time()
            m.remaining_cap = (m.value_cap - m.total_value) if m.value_cap > 0 else 0
            if press_id:
                m.press_ids.append(str(press_id))

            if m.locked_side == 0:
                m.locked_side = side

            try:
                fresh_pids = await etop_api.get_cancellable_presses(em.mid)
                if fresh_pids:
                    m.press_ids = [str(p) for p in fresh_pids]
            except Exception:
                pass

            side_name = 'team1' if side == 1 else 'team2'
            log_fire(f"{m.team1} vs {m.team2} [{m.label}]",
                    m.total_fired, "OK",
                    side=side_name, ev=f"{best_ev:+.2f}%",
                    remain=f"{fresh_remain}s")

            log_info(f"[FIRE] {m.team1} vs {m.team2} [{m.label}] "
                     f"#{m.total_fired} {side_name} ev={best_ev:+.2f}% "
                     f"cap={m.total_value:.0f}/{m.value_cap:.0f}g "
                     f"pool={m.raw_pool:.0f}g priority={score:.1f}")
            try:
                import asyncio as _asyncio
                from core.notifier import notify as _notify
                _asyncio.create_task(_notify(
                    f"🔥 FIRE #{m.total_fired} {m.team1} vs {m.team2}\n"
                    f"[{m.label}] {side_name} ev={best_ev:+.2f}% remain={fresh_remain}s\n"
                    f"cap={m.total_value:.0f}/{m.value_cap:.0f}g pool={m.raw_pool:.0f}g",
                    debounce_key=f"fire_{m.mid}_{m.total_fired}"
                ))
            except Exception:
                pass

            fired_this_cycle += 1
        else:
            m.last_fire_at = time.time()
            log_fire(f"{m.team1} vs {m.team2} [{m.label}]",
                    m.total_fired, "FAIL", msg=msg)

        await asyncio.sleep(0.2)  # 200ms gap before next market (rate control)

    if fired_this_cycle > 0:
        log_info(f"[FIRE_CYCLE] fired={fired_this_cycle} total_candidates={len(candidates)} "
                 f"cooldown={cooldown_sec*1000:.0f}ms mode=UNIFIED")
