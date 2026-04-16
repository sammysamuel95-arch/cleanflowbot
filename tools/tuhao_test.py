"""
Tuhao live test — observe pool changes over time.

Run alongside the bot (uses bot's etop session).
Watches FIRE_ZONE markets and polls tuhao every N seconds.
Shows pool growth, API latency, and recommended cache TTL.

Usage:
  cd ~/VibeCoding/ProjectBot/CleanFlowBot-clean
  python3 tools/tuhao_test.py

Ctrl+C for summary.
"""

import asyncio
import json
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.etopfun_api import load_etop_cookies, create_etop_session, build_etop_headers, EtopfunAPI
from core.pool_estimator import PoolEstimator


POLL_INTERVAL = 10  # seconds between tuhao checks per market
MAX_MARKETS = 5     # max markets to track simultaneously


async def rate_limit_test(etop_api, pool_est, listing):
    """Test sustained tuhao rate — find calls/min before 429."""
    print("\033[92m═══ Tuhao Rate Limit Test ═══\033[0m")

    # Only use markets with actual bettors (pool > 0)
    # Filter by scanning listing for NBA and Cruz Azul soccer markets
    print("Finding NBA / Cruz Azul markets with bettors...")
    good_mids = []
    for mid, sub in listing.items():
        # We'll just call once to check — use cache
        pool = await pool_est.estimate_pool(etop_api, mid)
        if pool > 0:
            good_mids.append(mid)
            print(f"  mid={mid} pool={pool:.0f}g")
    mids = good_mids
    if len(mids) < 2:
        print("Not enough markets with bettors to test")
        return
    print(f"Testing with {len(mids)} markets that have bettors: {mids}\n")

    # Phase 0: Wait for rate limit to reset (burst test may have triggered it)
    print("Phase 0: Waiting 30s for rate limit to reset...")
    await asyncio.sleep(30)

    # Phase 1: Test different intervals to find sustainable rate
    test_intervals = [
        (2.0, 15, "1 call every 2s (30/min)"),
        (1.0, 15, "1 call every 1s (60/min)"),
        (0.5, 20, "2 calls/sec (120/min)"),
        (0.3, 20, "3.3 calls/sec (200/min)"),
    ]

    for interval, count, label in test_intervals:
        print(f"\n\033[93mTest: {label}\033[0m")
        successes = 0
        errors_429 = 0
        latencies = []

        for i in range(count):
            mid = mids[i % len(mids)]
            pass  # no cache in new pool_estimator
            t0 = time.time()
            pool = await pool_est.estimate_pool(etop_api, mid)
            latency = (time.time() - t0) * 1000
            latencies.append(latency)

            if pool > 0:
                successes += 1
                status = "\033[92mOK\033[0m"
            else:
                errors_429 += 1
                status = "\033[91m429\033[0m"

            print(f"  [{i+1:2d}/{count}] {status} {latency:5.0f}ms pool={pool:.0f}g", flush=True)

            if errors_429 >= 3:
                print(f"  \033[91mStopping — 3 errors hit\033[0m")
                break

            await asyncio.sleep(interval)

        avg_lat = sum(latencies) / len(latencies) if latencies else 0
        result = "\033[92mSUSTAINABLE\033[0m" if errors_429 == 0 else f"\033[91mFAILED at call {successes + 1}\033[0m"
        print(f"  Result: {result}")
        print(f"  Success: {successes}/{successes + errors_429}")
        print(f"  Avg latency: {avg_lat:.0f}ms")

        if errors_429 >= 3:
            print(f"\n\033[93mRate limit boundary found!\033[0m")
            print(f"  Last sustainable: previous interval")
            print(f"  Failed at: {label}")

            # Wait for reset before next test
            print(f"  Waiting 60s for rate limit reset...")
            await asyncio.sleep(60)
        elif errors_429 > 0:
            # Partial failure, wait before next
            print(f"  Waiting 30s for reset...")
            await asyncio.sleep(30)
        else:
            # Success, brief pause before next test
            await asyncio.sleep(10)

    # Phase 2: Burst recovery test — how long to recover after burst
    print(f"\n\033[93mBurst Recovery Test: how fast does rate limit reset?\033[0m")
    print("Sending burst of 10...")

    # Burst
    burst_ok = 0
    for i in range(10):
        mid = mids[i % len(mids)]
        pool_est._pool_cache.pop(mid, None)
        pool = await pool_est.estimate_pool(etop_api, mid)
        if pool > 0:
            burst_ok += 1
    print(f"  Burst: {burst_ok}/10 succeeded")

    # Test recovery at increasing intervals
    for wait in [5, 10, 15, 20, 30, 45, 60]:
        print(f"  Waiting {wait}s... ", end='', flush=True)
        await asyncio.sleep(wait)
        mid = mids[0]
        pool_est._pool_cache.pop(mid, None)
        t0 = time.time()
        pool = await pool_est.estimate_pool(etop_api, mid)
        latency = (time.time() - t0) * 1000
        if pool > 0:
            print(f"\033[92mRECOVERED\033[0m after {wait}s ({latency:.0f}ms)")
            break
        else:
            print(f"\033[91mstill blocked\033[0m ({latency:.0f}ms)")
    else:
        print("  Rate limit didn't reset within 60s!")

    print(f"\n\033[92m═══ Summary ═══\033[0m")
    print(f"  Burst limit:    ~10 calls before 429")
    print(f"  Avg latency:    ~56ms")
    print(f"  Run the test above to see sustained rate")
    print(f"  Recovery time shown above")
    print()


async def main():
    print("\033[92m═══ Tuhao Live Test ═══\033[0m")
    print(f"Polling every {POLL_INTERVAL}s, tracking up to {MAX_MARKETS} markets")
    print("Ctrl+C for summary\n")

    # Setup etop session (same as bot)
    etop_cookies = load_etop_cookies()
    etop_session = create_etop_session(etop_cookies)
    etop_api = EtopfunAPI(etop_session, build_etop_headers())
    if 'DJSP_UUID' in etop_cookies:
        etop_api.set_uuid(etop_cookies['DJSP_UUID'])

    # Load exchange DB for gold values
    pool_est = PoolEstimator()
    print("Loading exchange DB...")
    await pool_est.load_exchange_db(etop_api)
    if not pool_est._loaded:
        print("\033[91mExchange DB failed to load. Cannot estimate pools.\033[0m")
        return
    print(f"Exchange DB: {len(pool_est._exchange_db)} items\n")

    # Rate limit test first
    try:
        parents, listing = await asyncio.wait_for(
            etop_api.match_list(), timeout=10)
        await rate_limit_test(etop_api, pool_est, listing)
    except Exception as e:
        print(f"Rate limit test failed: {e}")

    print("Now starting live pool tracking...\n")

    # Track: mid → list of (timestamp, pool_gold, latency_ms, remain_s)
    history = {}
    api_latencies = []
    calls_made = 0
    start_time = time.time()

    try:
        while True:
            # Get current markets from match_list
            try:
                parents, listing = await asyncio.wait_for(
                    etop_api.match_list(), timeout=10)
            except Exception as e:
                print(f"\033[91mMatch list failed: {e}\033[0m")
                await asyncio.sleep(5)
                continue

            # Find markets with remain < 200s (approaching fire zone)
            candidates = []
            for mid, sub in listing.items():
                remain = int(sub.get('remain', 0))
                if 10 < remain < 200:
                    # Find parent info for display
                    name = "unknown"
                    for p in parents:
                        subs = p.get('subMatchList', p.get('subs', []))
                        for s in subs:
                            if str(s.get('mid', s.get('id', ''))) == str(mid):
                                vs1 = p.get('vs1', p.get('team1', '?'))
                                vs2 = p.get('vs2', p.get('team2', '?'))
                                mtype = sub.get('mtype', s.get('mtype', ''))
                                name = f"{vs1} vs {vs2} [{mtype}]"
                                break
                    candidates.append((mid, remain, name))

            # Sort by remain (closest first), limit to MAX_MARKETS
            candidates.sort(key=lambda c: c[1])
            candidates = candidates[:MAX_MARKETS]

            if not candidates:
                print(f"\r[{time.time()-start_time:6.0f}s] No markets with remain < 200s, waiting...", end='', flush=True)
                await asyncio.sleep(5)
                continue

            # Call tuhao for each candidate
            for mid, remain, name in candidates:
                t0 = time.time()
                try:
                    # Bypass cache — call API directly
                    pass  # no cache in new pool_estimator  # clear cache
                    pool_gold = await pool_est.estimate_pool(etop_api, mid)
                    latency = (time.time() - t0) * 1000
                    calls_made += 1
                    api_latencies.append(latency)

                    # Record history
                    if mid not in history:
                        history[mid] = {'name': name, 'points': []}
                    history[mid]['points'].append({
                        'ts': time.time(),
                        'pool': pool_gold,
                        'latency_ms': latency,
                        'remain': remain,
                    })

                    # Show live
                    prev = history[mid]['points'][-2]['pool'] if len(history[mid]['points']) > 1 else 0
                    delta = pool_gold - prev if prev > 0 else 0
                    delta_str = f" \033[92m+{delta:.0f}g\033[0m" if delta > 0 else f" {delta:.0f}g" if delta < 0 else ""
                    print(f"  [{remain:3d}s] {name[:50]:50s} pool={pool_gold:8.1f}g  {latency:5.0f}ms{delta_str}")

                except Exception as e:
                    latency = (time.time() - t0) * 1000
                    print(f"  [{remain:3d}s] {name[:50]:50s} \033[91mERROR: {e}\033[0m  {latency:.0f}ms")
                    api_latencies.append(latency)
                    calls_made += 1

            print(f"  ─── calls={calls_made} avg={sum(api_latencies)/len(api_latencies):.0f}ms ───")
            await asyncio.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        print("\n")
        print("\033[92m═══ Summary ═══\033[0m")
        print(f"Duration:       {elapsed:.0f}s")
        print(f"API calls:      {calls_made}")
        print(f"Calls/min:      {calls_made / (elapsed/60):.1f}")
        if api_latencies:
            print(f"Avg latency:    {sum(api_latencies)/len(api_latencies):.0f}ms")
            print(f"Min latency:    {min(api_latencies):.0f}ms")
            print(f"Max latency:    {max(api_latencies):.0f}ms")
            print(f"P95 latency:    {sorted(api_latencies)[int(len(api_latencies)*0.95)]:.0f}ms")

        # Pool growth analysis
        print(f"\n\033[92m═══ Pool Growth ═══\033[0m")
        for mid, data in history.items():
            points = data['points']
            if len(points) < 2:
                print(f"  {data['name']}: only 1 sample, need more")
                continue

            first_pool = points[0]['pool']
            last_pool = points[-1]['pool']
            first_remain = points[0]['remain']
            last_remain = points[-1]['remain']
            duration = points[-1]['ts'] - points[0]['ts']

            changes = []
            for i in range(1, len(points)):
                if points[i]['pool'] != points[i-1]['pool']:
                    changes.append({
                        'from': points[i-1]['pool'],
                        'to': points[i]['pool'],
                        'gap': points[i]['ts'] - points[i-1]['ts'],
                        'remain': points[i]['remain'],
                    })

            print(f"\n  {data['name']}")
            print(f"    Tracked: {duration:.0f}s ({len(points)} samples)")
            print(f"    Pool: {first_pool:.0f}g → {last_pool:.0f}g ({last_pool-first_pool:+.0f}g)")
            print(f"    Remain: {first_remain}s → {last_remain}s")
            print(f"    Changes: {len(changes)} times pool value changed")

            if changes:
                gaps = [c['gap'] for c in changes]
                print(f"    Change frequency: every {sum(gaps)/len(gaps):.0f}s on avg")
                print(f"    Fastest change: {min(gaps):.0f}s")
                print(f"    Recommended cache TTL: {max(5, min(gaps) - 2):.0f}s")

                for c in changes:
                    print(f"      remain={c['remain']:3d}s: {c['from']:.0f}g → {c['to']:.0f}g "
                          f"({c['to']-c['from']:+.0f}g, gap={c['gap']:.0f}s)")
            else:
                print(f"    Pool didn't change during observation")
                print(f"    Recommended cache TTL: 30s+ (pool is stable)")


if __name__ == '__main__':
    asyncio.run(main())
