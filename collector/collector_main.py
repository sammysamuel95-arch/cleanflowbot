"""
collector/collector_main.py — VPS entry point.

Starts etop + PS collectors concurrently, both writing to Redis.

Usage:
    # Local test (Mac):
    python3 collector/collector_main.py --etop-interval 1

    # VPS (with Redis password):
    python3 /opt/cleanflowbot/collector/collector_main.py \\
        --etop-interval 1 \\
        --redis-pass YOUR_REDIS_PASS

Options:
    --etop-interval FLOAT   Etop poll interval in seconds (default 1.0)
    --redis-host    STR     Redis host (default 127.0.0.1)
    --redis-port    INT     Redis port (default 6379)
    --redis-pass    STR     Redis password (default none)
    --etop-only             Run only etop collector (skip PS)
    --ps-only               Run only PS collector (skip etop)
"""

import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from collector.redis_config import make_redis
from collector.etop_collector import EtopCollector
from collector.ps_collector import PSCollector


async def main():
    parser = argparse.ArgumentParser(description='CleanFlowBot VPS Collector')
    parser.add_argument('--etop-interval', type=float, default=1.0,
                        help='Etop poll interval seconds (default 1.0)')
    parser.add_argument('--redis-host', default='127.0.0.1')
    parser.add_argument('--redis-port', type=int, default=6379)
    parser.add_argument('--redis-pass', default='')
    parser.add_argument('--etop-only', action='store_true')
    parser.add_argument('--ps-only',   action='store_true')
    args = parser.parse_args()

    r = make_redis(host=args.redis_host, port=args.redis_port,
                   password=args.redis_pass)
    print(f"[MAIN] Redis connected: {args.redis_host}:{args.redis_port}",
          flush=True)

    tasks = []

    if not args.ps_only:
        etop = EtopCollector(r, poll_interval=args.etop_interval)
        print(f"[MAIN] Starting etop collector (interval={args.etop_interval}s)",
              flush=True)
        tasks.append(etop.run())

    if not args.etop_only:
        ps = PSCollector(r)
        print("[MAIN] Starting PS collector", flush=True)
        tasks.append(ps.run())

    if not tasks:
        print("[MAIN] ERROR: both --etop-only and --ps-only passed — nothing to run")
        return

    await asyncio.gather(*tasks)


if __name__ == '__main__':
    asyncio.run(main())
