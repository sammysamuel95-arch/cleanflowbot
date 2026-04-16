"""
Auto-login to ps3838 using credentials from data/auth/credentials.json.
Saves data/auth/cookie.json and data/auth/ws_token.json for the bot.

    python3 data/refresh_ps3838.py
"""
import asyncio, json, os, time
from playwright.async_api import async_playwright

_DATA_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT     = os.path.dirname(_DATA_DIR)
BASE      = "https://www.pinnacle888.com"
TOKEN_URL = BASE + "/member-auth/v2/wstoken"

with open(os.path.join(_DATA_DIR, 'auth', 'credentials.json')) as _f:
    _creds = json.load(_f)
USERNAME = _creds['ps_user']
PASSWORD = _creds['ps_pass']

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome", headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            storage_state=None)  # fresh context — no cached session
        page = await ctx.new_page()

        await page.goto(BASE + "/en/", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        # Fill login form
        await page.fill('#loginId', USERNAME)
        await page.fill('#pass', PASSWORD)
        await page.keyboard.press('Enter')
        await page.wait_for_timeout(6000)
        print(f"Logged in as {USERNAME}, current URL: {page.url}")

        # Try to fetch WS token from inside the browser (has all HttpOnly cookies)
        token_url = f"{TOKEN_URL}?locale=en_US&_={int(time.time()*1000)}&withCredentials=true"
        resp = await ctx.request.get(token_url)
        try:
            data  = await resp.json()
            token = data.get("token") or data.get("wsToken") or data.get("ws_token")
        except Exception:
            token = None
        print(f"Token fetch: status={resp.status}  token={str(token)[:30] if token else 'NONE'}")

        # Grab v-hucode from localStorage (required for all-odds-selections)
        v_hucode = await page.evaluate("localStorage.getItem('v-hucode')")
        if v_hucode:
            print(f"v-hucode: {v_hucode}")
        else:
            print("WARNING: v-hucode not found in localStorage — all-odds-selections will fail")

        # Save cookies + v-hucode
        cookies    = await ctx.cookies()
        ps_cookies = [c for c in cookies if "pinnacle888" in c.get("domain", "") and c.get("value")]
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in ps_cookies)
        print(f"Got {len(ps_cookies)} PS3838 cookies: {[c['name'] for c in ps_cookies]}")

        cookie_data = {"cookie": cookie_str}
        if v_hucode:
            cookie_data["v_hucode"] = v_hucode

        with open(os.path.join(_DATA_DIR, 'auth', 'cookie.json'), "w") as f:
            json.dump(cookie_data, f)
        print(f"Saved cookie.json (v-hucode={'YES' if v_hucode else 'MISSING'})")

        # Save token separately so bot can use it directly
        if token:
            with open(os.path.join(_DATA_DIR, 'auth', 'ws_token.json'), "w") as f:
                json.dump({"token": token, "saved_at": time.time()}, f)
            print(f"Saved ws_token.json  token={token[:20]}...")

        await browser.close()
        print("\nDone — cookie refreshed. Bot will auto-reload.")

asyncio.run(main())
