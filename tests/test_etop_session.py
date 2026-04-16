"""
test_etop_session.py — Run all EtopSessionManager tests in order.

Usage:
    cd ~/VibeCoding/ProjectBot/CleanFlowBot-clean
    python3 test_etop_session.py [--test 2]   # run specific test
    python3 test_etop_session.py              # run all tests

Tests:
    1 - Module imports cleanly
    2 - Playwright launches, Steam SSO works (headless=False — opens browser)
    3 - Cookies saved to session.json correctly
    4 - Session reload + match_list returns 10+ parents
    5 - Simulate dead session → auto_recover → healthy again
    6 - Headless recovery
"""

import asyncio
import json
import os
import sys
import shutil

# Run from project root
os.chdir(os.path.dirname(os.path.abspath(__file__)))

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


# ── TEST 1 ────────────────────────────────────────────────────────────────────

def test_1_import():
    print("\n── TEST 1: Module imports cleanly ──")
    try:
        from feeds.etop_session import EtopSessionManager
        mgr = EtopSessionManager('data/auth/session.json', 'data/playwright_etop_profile', 'https://www.etopfun.com')
        print(f"  {PASS}: EtopSessionManager imported and instantiated")
        return True
    except Exception as e:
        print(f"  {FAIL}: {e}")
        return False


# ── TEST 2 ────────────────────────────────────────────────────────────────────

async def test_2_playwright_login(headless=False):
    print(f"\n── TEST 2: Playwright launches + Steam SSO (headless={headless}) ──")
    from feeds.etop_session import EtopSessionManager

    # Backup existing session
    if os.path.exists('data/auth/session.json'):
        shutil.copy('data/auth/session.json', 'data/session.json.bak')
        print("  Backed up session.json → session.json.bak")

    mgr = EtopSessionManager(
        session_file='data/auth/session.json',
        profile_dir='data/playwright_etop_profile',
        etop_base_url='https://www.etopfun.com',
    )

    print(f"  Launching browser (headless={headless})...")
    cookies = await mgr.refresh_login(headless=headless)

    if not cookies:
        print(f"  {FAIL}: refresh_login returned no cookies")
        print("  → If first run: set headless=False to complete Steam login manually")
        return False

    print(f"  Got {len(cookies)} cookies: {list(cookies.keys())}")

    ok = True
    for key in ('JSESSIONID', 'DJSP_UUID'):
        if key in cookies:
            print(f"  ✓ {key}: {cookies[key][:20]}...")
        else:
            print(f"  ✗ Missing: {key}")
            ok = False

    if 'DJSP_USER' in cookies:
        print(f"  ✓ DJSP_USER present (logged-in session)")
    else:
        print(f"  ⚠ DJSP_USER missing — NBA/soccer markets may be limited")

    if ok:
        print(f"  {PASS}")
    else:
        print(f"  {FAIL}: Missing required cookies")
    return ok


# ── TEST 3 ────────────────────────────────────────────────────────────────────

def test_3_session_json():
    print("\n── TEST 3: Cookies saved to session.json ──")
    if not os.path.exists('data/auth/session.json'):
        print(f"  {FAIL}: data/session.json not found — run TEST 2 first")
        return False

    try:
        with open('data/auth/session.json') as f:
            data = json.load(f)
        cookie_list = data if isinstance(data, list) else data.get('cookies', [])
        names = [c.get('name', '?') for c in cookie_list]
        print(f"  {len(cookie_list)} cookies: {names}")

        ok = True
        for key in ('JSESSIONID', 'DJSP_UUID'):
            if key in names:
                print(f"  ✓ {key} present")
            else:
                print(f"  ✗ {key} missing")
                ok = False

        if ok:
            print(f"  {PASS}")
        else:
            print(f"  {FAIL}")
        return ok
    except Exception as e:
        print(f"  {FAIL}: {e}")
        return False


# ── TEST 4 ────────────────────────────────────────────────────────────────────

async def test_4_session_reload():
    print("\n── TEST 4: Session reload + match_list returns 10+ parents ──")
    from feeds.etopfun_api import load_etop_cookies, create_etop_session, build_etop_headers, EtopfunAPI

    try:
        cookies = load_etop_cookies()
        session = create_etop_session(cookies)
        api = EtopfunAPI(session, build_etop_headers())
        if 'DJSP_UUID' in cookies:
            api.set_uuid(cookies['DJSP_UUID'])

        parents, lookup = await api.match_list()
        print(f"  Parents: {len(parents)}, Subs: {len(lookup)}")

        await session.close()

        if len(parents) >= 10:
            print(f"  {PASS}: {len(parents)} parents (≥10)")
            return True
        else:
            print(f"  {FAIL}: Only {len(parents)} parents — session not fully logged in")
            print("  → DJSP_USER missing? Re-run TEST 2 and complete Steam login fully")
            return False
    except Exception as e:
        print(f"  {FAIL}: {e}")
        return False


# ── TEST 5 ────────────────────────────────────────────────────────────────────

async def test_5_simulate_dead_and_recover(headless=False):
    print(f"\n── TEST 5: Simulate dead session + auto_recover (headless={headless}) ──")
    from feeds.etop_session import EtopSessionManager
    from feeds.etopfun_api import load_etop_cookies, create_etop_session, build_etop_headers, EtopfunAPI

    # Backup
    if os.path.exists('data/auth/session.json'):
        shutil.copy('data/auth/session.json', 'data/session.json.bak')
        print("  Backed up session.json")

    # Build working API
    cookies = load_etop_cookies()
    session = create_etop_session(cookies)
    api = EtopfunAPI(session, build_etop_headers())
    if 'DJSP_UUID' in cookies:
        api.set_uuid(cookies['DJSP_UUID'])

    mgr = EtopSessionManager(
        session_file='data/auth/session.json',
        profile_dir='data/playwright_etop_profile',
        etop_base_url='https://www.etopfun.com',
    )

    # Verify healthy
    healthy = await mgr.is_healthy(api)
    print(f"  Before corruption: healthy={healthy}")
    if not healthy:
        print(f"  {FAIL}: Session not healthy before test — fix TEST 4 first")
        await session.close()
        return False

    # Corrupt session.json — remove DJSP_USER
    with open('data/auth/session.json') as f:
        data = json.load(f)
    cookie_list = data if isinstance(data, list) else data.get('cookies', [])
    corrupted = [c for c in cookie_list if c.get('name') != 'DJSP_USER']
    with open('data/auth/session.json', 'w') as f:
        json.dump(corrupted, f)
    print(f"  Corrupted session.json: removed DJSP_USER ({len(cookie_list)} → {len(corrupted)} cookies)")

    # Reload with corrupted cookies
    bad_cookies = load_etop_cookies()
    bad_session = create_etop_session(bad_cookies)
    api_bad = EtopfunAPI(bad_session, build_etop_headers())
    if 'DJSP_UUID' in bad_cookies:
        api_bad.set_uuid(bad_cookies['DJSP_UUID'])

    parents_bad, _ = await api_bad.match_list()
    print(f"  After corruption: {len(parents_bad)} parents")
    if len(parents_bad) >= 10:
        print("  ⚠ Still 10+ parents after removing DJSP_USER — "
              "detection relies on parent count dropping. "
              "Bot may not detect this on your current live state.")

    # Auto recover
    print(f"  Calling auto_recover(headless={headless})...")
    recovered = await mgr.auto_recover(api_bad, headless=headless)
    print(f"  recovered={recovered}")

    if recovered:
        parents_after, _ = await api_bad.match_list()
        print(f"  After recovery: {len(parents_after)} parents")
        if len(parents_after) >= 10:
            print(f"  {PASS}: Full cycle — healthy → corrupt → detect → recover → healthy")
        else:
            print(f"  {FAIL}: Recovered={recovered} but only {len(parents_after)} parents")
            await bad_session.close()
            return False
    else:
        print(f"  {FAIL}: auto_recover returned False")
        # Restore backup
        if os.path.exists('data/session.json.bak'):
            shutil.copy('data/session.json.bak', 'data/auth/session.json')
            print("  Restored session.json from backup")
        await bad_session.close()
        return False

    await bad_session.close()
    return True


# ── TEST 6 ────────────────────────────────────────────────────────────────────

async def test_6_headless_recovery():
    print("\n── TEST 6: Headless recovery (simulating VPS) ──")
    print("  (Same as TEST 5 but headless=True — requires saved Steam session in profile)")
    return await test_5_simulate_dead_and_recover(headless=True)


# ── RUNNER ────────────────────────────────────────────────────────────────────

async def run_only(n: int):
    """Run exactly one test by number."""
    if n == 1:
        result = test_1_import()
    elif n == 2:
        result = await test_2_playwright_login(headless=False)
    elif n == 3:
        result = test_3_session_json()
    elif n == 4:
        result = await test_4_session_reload()
    elif n == 5:
        result = await test_5_simulate_dead_and_recover(headless=False)
    elif n == 6:
        result = await test_6_headless_recovery()
    else:
        print(f"Unknown test number: {n}")
        return

    print(f"\n── RESULT: TEST {n}: {PASS if result else FAIL}")


async def run_all(start_from=1):
    results = {}

    if start_from <= 1:
        results[1] = test_1_import()
        if not results[1]:
            print("\nTest 1 failed — fix imports before continuing")
            return results

    if start_from <= 2:
        results[2] = await test_2_playwright_login(headless=False)
        if not results[2]:
            print("\nTest 2 failed — fix Playwright/Steam login before continuing")
            return results

    if start_from <= 3:
        results[3] = test_3_session_json()
        if not results[3]:
            print("\nTest 3 failed — fix cookie save logic")
            return results

    if start_from <= 4:
        results[4] = await test_4_session_reload()
        if not results[4]:
            print("\nTest 4 failed — need 10+ parents. Check DJSP_USER in cookies.")
            return results

    if start_from <= 5:
        results[5] = await test_5_simulate_dead_and_recover(headless=False)
        if not results[5]:
            print("\nTest 5 failed")
            return results

    if start_from <= 6:
        results[6] = await test_6_headless_recovery()

    print("\n── SUMMARY ──")
    for n, r in results.items():
        status = PASS if r else FAIL
        print(f"  TEST {n}: {status}")

    return results


if __name__ == '__main__':
    # --test N  → run only test N
    # --from N  → run from test N onwards (inclusive)
    # (no args) → run all tests from 1
    only = None
    start = 1

    if '--test' in sys.argv:
        idx = sys.argv.index('--test')
        if idx + 1 < len(sys.argv):
            only = int(sys.argv[idx + 1])
    elif '--from' in sys.argv:
        idx = sys.argv.index('--from')
        if idx + 1 < len(sys.argv):
            start = int(sys.argv[idx + 1])

    if only is not None:
        asyncio.run(run_only(only))
    else:
        asyncio.run(run_all(start_from=start))
