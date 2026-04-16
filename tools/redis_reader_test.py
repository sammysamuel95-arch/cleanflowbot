"""
tools/redis_reader_test.py — Read VPS Redis data from Mac and print health/data.

Usage:
    # Local test (Mac Redis, no password):
    python3 tools/redis_reader_test.py --host 127.0.0.1

    # VPS:
    python3 tools/redis_reader_test.py --host VPS_IP --password YOUR_REDIS_PASS
"""

import argparse
import json
import sys
import time
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import redis
from collector.redis_config import K


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host',     default='127.0.0.1')
    parser.add_argument('--port',     type=int, default=6379)
    parser.add_argument('--password', default='')
    args = parser.parse_args()

    try:
        kwargs = dict(host=args.host, port=args.port,
                      decode_responses=True)
        if args.password:
            kwargs['password'] = args.password
        r = redis.Redis(**kwargs)
        r.ping()
        print(f"Redis connected: {args.host}:{args.port}\n")
    except Exception as e:
        print(f"ERROR: Cannot connect to Redis: {e}")
        sys.exit(1)

    now = time.time()
    SP_NAMES = {4: 'basketball', 12: 'esports', 29: 'soccer'}

    # ── Etop health ───────────────────────────────────────────────────────────
    print("=" * 55)
    print("ETOP FETCHER")
    print("=" * 55)
    raw = r.get(K.HB_ETOP)
    if raw:
        h = json.loads(raw)
        age = now - h['ts']
        print(f"Status:        ALIVE ({age:.1f}s ago)")
        print(f"Parents:       {h['parents']}")
        print(f"Subs:          {h['subs']}")
        print(f"Cycle:         {h['cycle_ms']}ms")
        print(f"Poll interval: {h['poll_interval']}s")
        print(f"Total polls:   {h['total_polls']}")
        print(f"Rate limited:  {h.get('rate_limited', 0)} times")
        print(f"Errors:        {h.get('errors', 0)} consecutive")
    else:
        print("Status:        DEAD (no heartbeat)")

    etop_ts = r.get(K.LAST_FETCH)
    if etop_ts:
        age = now - float(etop_ts)
        print(f"Data age:      {age:.1f}s")

    sports_raw = r.get(K.ACTIVE_SPORTS)
    if sports_raw:
        active = json.loads(sports_raw)
        names = [SP_NAMES.get(int(s), str(s)) for s in active]
        print(f"Active sports: {names}")

    parents_raw = r.get(K.PARENTS)
    if parents_raw:
        parents = json.loads(parents_raw)
        print(f"\nParents ({len(parents)} total):")
        for p in parents[:5]:
            vs1 = (p.get('vs1') or {}).get('name', '?')
            vs2 = (p.get('vs2') or {}).get('name', '?')
            cat = (p.get('category') or {}).get('type', '?')
            remain = p.get('remainTime', 0) // 1000
            print(f"  [{cat}] {vs1} vs {vs2}  remain={remain}s")
        if len(parents) > 5:
            print(f"  ... +{len(parents) - 5} more")

    # ── PS health ─────────────────────────────────────────────────────────────
    print()
    print("=" * 55)
    print("PS FETCHER")
    print("=" * 55)
    raw = r.get(K.HB_PS)
    if raw:
        h = json.loads(raw)
        age = now - h['ts']
        print(f"Status:        ALIVE ({age:.1f}s ago)")
        print(f"WS connected:  {h['ws_connected']}")
        print(f"Store size:    {h['store_size']} lines")
        print(f"Events:        {h['events']}")
        print(f"Syncs:         {h['syncs']}")
        sp_names = [SP_NAMES.get(int(s), str(s))
                    for s in h.get('active_sports', [])]
        print(f"Active sports: {sp_names}")
    else:
        print("Status:        DEAD (no heartbeat)")

    # Count keys
    odds_count  = sum(1 for _ in r.scan_iter("ps:odds:*"))
    event_count = sum(1 for _ in r.scan_iter("ps:event:*"))
    print(f"\nOdds buckets:  {odds_count}")
    print(f"Event keys:    {event_count}")

    # Sample events per sport
    for sp_id, sp_name in SP_NAMES.items():
        sp_raw = r.get(K.PS_EVENTS_SP.format(sp=sp_id))
        if not sp_raw:
            continue
        eids = json.loads(sp_raw)
        print(f"\n{sp_name.upper()} ({len(eids)} events):")
        for eid in eids[:3]:
            ev_raw = r.get(K.PS_EVENT.format(eid=eid))
            if ev_raw:
                ev = json.loads(ev_raw)
                age = now - ev.get('last_seen', now)
                print(f"  [{eid}] {ev.get('home')} vs {ev.get('away')} "
                      f"(last_seen {age:.0f}s ago)")
        if len(eids) > 3:
            print(f"  ... +{len(eids) - 3} more")

    print()
    print("=" * 55)
    print("DONE")
    print("=" * 55)


if __name__ == '__main__':
    main()
