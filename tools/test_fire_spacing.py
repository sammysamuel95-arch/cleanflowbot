"""
Test fire spacing on a real market.
Calls tuhao → 100ms → match_list → compute → press → 200ms
40 times. Measures timing and 429s.

Usage:
  python3 tools/test_fire_spacing.py 588039

STOP THE BOT FIRST — they share the same etop session/cookies.
"""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.etopfun_api import load_etop_cookies, create_etop_session, build_etop_headers, EtopfunAPI
from core.pool_estimator import PoolEstimator

MAX_FIRES = 40
TUHAO_GAP = 0.1    # 100ms after tuhao
PRESS_GAP = 0.2    # 200ms after press


async def main():
    mid = sys.argv[1] if len(sys.argv) > 1 else '588039'
    print(f"\033[92m═══ Fire Spacing Test ═══\033[0m")
    print(f"Target: mid={mid}")
    print(f"Fires: {MAX_FIRES}")
    print(f"Gaps: tuhao+{TUHAO_GAP*1000:.0f}ms → match_list → press+{PRESS_GAP*1000:.0f}ms")
    print(f"Expected: {MAX_FIRES * 0.644:.1f}s total ({MAX_FIRES} × 644ms)")
    print()

    # Setup
    cookies = load_etop_cookies()
    session = create_etop_session(cookies)
    api = EtopfunAPI(session, build_etop_headers())
    if 'DJSP_UUID' in cookies:
        api.set_uuid(cookies['DJSP_UUID'])

    pool_est = PoolEstimator()
    await pool_est.load_exchange_db(api)

    # Get inventory
    from engine.inventory import InventoryManager
    inv = InventoryManager()
    await inv.load_pool(api)
    print(f"Inventory: {inv.pool_free_count()} items")
    print()

    fires = 0
    errors_429 = 0
    timings = []
    start = time.time()

    for i in range(MAX_FIRES):
        cycle_start = time.time()

        # ── 1. Tuhao ──
        t0 = time.time()
        pool_est._pool_cache = {}  # no cache
        try:
            pool = await pool_est.estimate_pool(api, mid)
        except Exception as e:
            pool = 0
            print(f"  [{i+1}] TUHAO ERROR: {e}")
        tuhao_ms = (time.time() - t0) * 1000

        # ── 100ms gap ──
        await asyncio.sleep(TUHAO_GAP)

        # ── 2. Match list ──
        t0 = time.time()
        try:
            parents, listing = await api.match_list()
            sub = listing.get(mid) or listing.get(int(mid))
        except Exception as e:
            sub = None
            print(f"  [{i+1}] MATCH_LIST ERROR: {e}")
        list_ms = (time.time() - t0) * 1000

        if not sub:
            print(f"  [{i+1}] mid={mid} not in listing, skipping")
            await asyncio.sleep(PRESS_GAP)
            continue

        o1 = sub.get('o1', 0)
        o2 = sub.get('o2', 0)
        remain = int(sub.get('remain', 0))

        # ── 3. Press (DRY RUN — just measure timing) ──
        t0 = time.time()
        # Pick an item
        remaining_cap = 999999
        item_id = inv.get_next_item(remaining_cap)
        if item_id is None:
            print(f"  [{i+1}] NO ITEMS LEFT")
            break

        item_value = inv.get_item_value(item_id)
        side = 1 if o1 < o2 else 2  # bet on cheaper side for test

        success, msg, press_id = await api.press(mid, [item_id], side)
        press_ms = (time.time() - t0) * 1000

        if success:
            inv.consume([item_id])
            fires += 1
            status = "\033[92mOK\033[0m"
        else:
            if '429' in str(msg):
                errors_429 += 1
                status = "\033[91m429\033[0m"
            else:
                status = f"\033[93m{msg}\033[0m"

        cycle_ms = (time.time() - cycle_start) * 1000
        timings.append(cycle_ms)

        print(f"  [{i+1:2d}] {status} tuhao={tuhao_ms:.0f}ms list={list_ms:.0f}ms "
              f"press={press_ms:.0f}ms cycle={cycle_ms:.0f}ms "
              f"pool={pool:.0f}g o1={o1} o2={o2} remain={remain}s")

        # ── 200ms gap ──
        await asyncio.sleep(PRESS_GAP)

    elapsed = time.time() - start

    print()
    print(f"\033[92m═══ Results ═══\033[0m")
    print(f"Fires:      {fires}/{MAX_FIRES}")
    print(f"429s:       {errors_429}")
    print(f"Duration:   {elapsed:.1f}s")
    print(f"Expected:   {MAX_FIRES * 0.644:.1f}s")
    print(f"Per fire:   {elapsed/max(fires,1)*1000:.0f}ms (expected: 644ms)")
    if timings:
        print(f"Avg cycle:  {sum(timings)/len(timings):.0f}ms")
        print(f"Min cycle:  {min(timings):.0f}ms")
        print(f"Max cycle:  {max(timings):.0f}ms")
    print(f"API rate:   {fires*3/(elapsed):.1f} calls/sec ({fires*3/(elapsed)*60:.0f}/min)")
    print()
    if errors_429 == 0:
        print("\033[92mZERO 429s — spacing is safe.\033[0m")
    else:
        print(f"\033[91m{errors_429} 429s — need larger gaps.\033[0m")


if __name__ == '__main__':
    asyncio.run(main())
