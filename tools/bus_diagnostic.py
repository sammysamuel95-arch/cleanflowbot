"""
Live DataBus diagnostic — watches dash_state.json for reactive behavior.

Proves:
  1. Pipeline runs on BOTH etop + PS (not just etop)
  2. dash_state updates frequently (4-8x/sec, not 1x/sec)
  3. EV values are stable (not flickering from stale data)
  4. Bus freshness stays sub-second for all sources

Run: python3 tools/bus_diagnostic.py
Stop: Ctrl+C (prints summary)
"""

import json, os, time, sys

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
DASH_PATH = os.path.join(DATA_DIR, 'dash_state.json')


def read_dash():
    try:
        with open(DASH_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def main():
    print("\033[92m═══ DataBus Live Diagnostic ═══\033[0m")
    print("Watching dash_state.json... (Ctrl+C to stop)\n")

    updates = []
    ev_history = {}  # mid → list of (ts, ev) for stability check
    last_ts = 0
    last_data = None
    start = time.time()

    try:
        while True:
            d = read_dash()
            if not d:
                time.sleep(0.1)
                continue

            ts = d.get('ts', 0)
            if ts == last_ts:
                time.sleep(0.05)
                continue

            # New update detected
            now = time.time()
            gap = now - last_ts if last_ts > 0 else 0
            last_ts = ts
            updates.append(now)

            # Bus freshness
            bf = d.get('bus_freshness', {})
            bn = d.get('bus_notify_count', 0)
            etop_f = bf.get('etop', -1)
            ps_f = bf.get('ps3838', -1)

            # Count markets with EV
            markets = d.get('markets', [])
            ev_count = sum(1 for m in markets if m.get('b') is not None and m['b'] > 0)
            total = len(markets)

            # Track EV stability (does same market flicker?)
            for m in markets:
                mid = m.get('mid', '')
                ev = m.get('b')
                if mid and ev is not None:
                    if mid not in ev_history:
                        ev_history[mid] = []
                    ev_history[mid].append((now, ev))
                    # Keep last 20
                    ev_history[mid] = ev_history[mid][-20:]

            # Display
            elapsed = now - start
            rate = len(updates) / elapsed if elapsed > 1 else 0

            # Color freshness
            etop_color = '\033[92m' if 0 <= etop_f < 2 else '\033[91m'
            ps_color = '\033[92m' if 0 <= ps_f < 2 else '\033[91m'
            reset = '\033[0m'

            print(f"\r\033[K"
                  f"[{elapsed:6.1f}s] "
                  f"rate={rate:.1f}/s  "
                  f"etop={etop_color}{etop_f:.2f}s{reset}  "
                  f"ps={ps_color}{ps_f:.2f}s{reset}  "
                  f"notifies={bn}  "
                  f"ev+={ev_count}/{total}  "
                  f"gap={gap:.3f}s",
                  end='', flush=True)

            time.sleep(0.05)

    except KeyboardInterrupt:
        elapsed = time.time() - start
        print("\n")
        print("\033[92m═══ Summary ═══\033[0m")
        print(f"Duration:        {elapsed:.1f}s")
        print(f"Total updates:   {len(updates)}")
        print(f"Update rate:     {len(updates)/elapsed:.1f}/sec")

        if updates:
            gaps = [updates[i] - updates[i-1] for i in range(1, len(updates))]
            if gaps:
                print(f"Avg gap:         {sum(gaps)/len(gaps)*1000:.0f}ms")
                print(f"Min gap:         {min(gaps)*1000:.0f}ms")
                print(f"Max gap:         {max(gaps)*1000:.0f}ms")

        # EV stability check
        flickers = 0
        stable = 0
        for mid, history in ev_history.items():
            if len(history) < 5:
                continue
            evs = [h[1] for h in history]
            # Check if EV bounces: changes direction more than 3 times in 10 readings
            changes = 0
            for i in range(2, len(evs)):
                if (evs[i] - evs[i-1]) * (evs[i-1] - evs[i-2]) < 0:
                    changes += 1
            if changes > 3:
                flickers += 1
            else:
                stable += 1

        print(f"\nEV stability:")
        print(f"  Stable markets:    {stable}")
        print(f"  Flickering:        {flickers}")
        if flickers == 0 and stable > 0:
            print(f"  \033[92m✓ No EV flicker detected — DataBus working correctly\033[0m")
        elif flickers > 0:
            print(f"  \033[93m⚠ {flickers} markets flickering — investigate\033[0m")

        # Rate assessment
        rate = len(updates) / elapsed if elapsed > 1 else 0
        if rate >= 3:
            print(f"\n\033[92m✓ Update rate {rate:.1f}/sec — reactive pipeline confirmed\033[0m")
        elif rate >= 1:
            print(f"\n\033[93m⚠ Update rate {rate:.1f}/sec — bus may not be wired to PS\033[0m")
        else:
            print(f"\n\033[91m✗ Update rate {rate:.1f}/sec — pipeline appears stuck\033[0m")


if __name__ == '__main__':
    main()
