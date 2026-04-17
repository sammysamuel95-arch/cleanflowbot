"""
IP vs session bucket test.
Run this simultaneously on Mac AND VPS.

If IP-based: each hits 429 at ~60 calls independently.
If session-based: combined calls drain faster, both hit 429 at ~30 each.

Usage:
  Terminal 1 (Mac): python3 tools/test_ip_bucket.py
  Terminal 2 (VPS): python3 tools/test_ip_bucket.py
  Start both within 2s of each other.
"""
import asyncio, sys, os, time, socket
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from feeds.etopfun_api import load_etop_cookies, create_etop_session, build_etop_headers

ETOP_URL = "https://etopfun.com/api/match/list.do?status=run&game=all&rows=50&page=1&lang=en"


async def main():
    host = socket.gethostname()
    cookies = load_etop_cookies()
    session = create_etop_session(cookies)
    headers = build_etop_headers()

    print(f"Host: {host}")
    print(f"{'#':>4}  {'time':>12}  {'status':>6}  {'ms':>6}")
    print("-" * 35)

    ok = fail = 0
    first_429 = None

    for i in range(70):
        t0 = time.time()
        ts = datetime.now().strftime('%H:%M:%S.%f')[:12]
        async with session.get(ETOP_URL, headers=headers) as r:
            ms = (time.time() - t0) * 1000
            status = r.status
        flag = " ← 429!" if status == 429 else ""
        print(f"{i+1:4d}  {ts}  {status:>6}  {ms:5.0f}ms{flag}")
        if status == 429:
            if first_429 is None:
                first_429 = i + 1
            fail += 1
            if fail >= 3:
                break
        else:
            ok += 1

    await session.close()
    print()
    print(f"Host={host}  OK={ok}  429-start={first_429 or 'never'}")
    print()
    if first_429:
        if first_429 <= 35:
            print("→ Hit 429 early (~30) — likely SHARED/SESSION bucket")
        else:
            print("→ Hit 429 late (~60) — likely IP-BASED independent bucket")


if __name__ == '__main__':
    asyncio.run(main())
