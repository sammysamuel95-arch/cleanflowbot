"""
Calls match_list every 2s. Logs raw o1/o2/remain.
Usage: python3 tools/test_odds_logger.py [MID]
  No MID = auto-pick closest to close.
"""
import asyncio, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from feeds.etopfun_api import load_etop_cookies, create_etop_session, build_etop_headers, EtopfunAPI

async def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None
    cookies = load_etop_cookies()
    session = create_etop_session(cookies)
    api = EtopfunAPI(session, build_etop_headers())
    if 'DJSP_UUID' in cookies:
        api.set_uuid(cookies['DJSP_UUID'])

    # First call — find a market
    parents, listing = await api.match_list()
    if not listing:
        print("match_list empty — session dead?")
        return

    if target and target in listing:
        mid = target
    else:
        picks = sorted(
            [(m, s) for m, s in listing.items()
             if 0 < s.get('remain', 0) < 300 and s.get('o1', 0) > 0],
            key=lambda x: x[1]['remain'])
        if not picks:
            print("No active markets")
            return
        mid = picks[0][0]

    s = listing[mid]
    print(f"Tracking mid={mid}  o1={s['o1']:.4f}  o2={s['o2']:.4f}  remain={s['remain']:.0f}s")
    print()
    print(f"{'#':>3}  {'o1':>8}  {'o2':>8}  {'remain':>6}  {'Δo1':>10}  {'Δo2':>10}  {'ms':>5}")
    print("-" * 60)

    prev_o1, prev_o2 = s['o1'], s['o2']

    for i in range(60):
        await asyncio.sleep(2.0)
        t0 = time.time()
        _, fresh = await api.match_list()
        ms = (time.time() - t0) * 1000

        if mid not in fresh:
            print(f"{i+1:3d}  MARKET CLOSED")
            break

        s = fresh[mid]
        o1, o2, r = s['o1'], s['o2'], s['remain']
        d1 = o1 - prev_o1
        d2 = o2 - prev_o2
        flag = " ← MOVED" if abs(d1) > 0.0001 or abs(d2) > 0.0001 else ""
        print(f"{i+1:3d}  {o1:8.4f}  {o2:8.4f}  {r:6.0f}  {d1:+10.4f}  {d2:+10.4f}  {ms:5.0f}{flag}")
        prev_o1, prev_o2 = o1, o2

    print("\nDone.")

if __name__ == '__main__':
    asyncio.run(main())
