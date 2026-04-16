"""Auto-login to pinnacle888.com and save cookies. No manual interaction needed."""
import asyncio
from playwright.async_api import async_playwright
import json, os, sys

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT = os.path.join(DATA_DIR, "cookie.json")

USERNAME = "NKUS95"
PASSWORD = "@Abcdbot12345"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await ctx.new_page()

        print("Navigating to login page...", flush=True)
        await page.goto("https://www.pinnacle888.com/en/sports/esports", wait_until="networkidle", timeout=30000)

        # Click Sign In
        try:
            await page.click("text=Sign In", timeout=5000)
            await asyncio.sleep(1)
        except Exception:
            await page.goto("https://www.pinnacle888.com/en/login", wait_until="networkidle", timeout=30000)

        print("Filling credentials...", flush=True)
        await page.fill("input[name='username'], input[placeholder*='sername'], input[type='text']", USERNAME)
        await page.fill("input[name='password'], input[placeholder*='assword'], input[type='password']", PASSWORD)
        await page.keyboard.press("Enter")

        print("Waiting for login...", flush=True)
        for i in range(30):
            cookies = await ctx.cookies()
            names = {c["name"] for c in cookies}
            if "_ulp" in names and "custid" in names:
                cust = next(c["value"] for c in cookies if c["name"] == "custid")
                print(f"Logged in: {cust[:40]}", flush=True)
                break
            await asyncio.sleep(1)
        else:
            print("Login timeout — saving whatever cookies exist", flush=True)

        cookies = await ctx.cookies()
        cookie_list = [{"name": c["name"], "value": c["value"]} for c in cookies]
        with open(OUT, "w") as f:
            json.dump(cookie_list, f, indent=2)
        ulp = next((c["value"][:20] for c in cookie_list if c["name"] == "_ulp"), "MISSING")
        print(f"Saved {len(cookie_list)} cookies — _ulp={ulp}", flush=True)
        await browser.close()

asyncio.run(main())
