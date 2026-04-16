"""Run this directly: python3 tools/grab_cookies.py"""
import asyncio
from playwright.async_api import async_playwright
import json, os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto('https://www.pinnacle888.com/en/sports/esports')
        print('Log in with NKUS95 — waiting until sports page loads...', flush=True)
        for i in range(180):
            url = page.url
            cookies = await page.context.cookies()
            names = {c['name'] for c in cookies}
            if 'custid' in names and '/en/sports/' in url:
                cust = next((c['value'] for c in cookies if c['name']=='custid'), '')
                print(f'Logged in: {cust[:50]}', flush=True)
                await asyncio.sleep(2)
                break
            await asyncio.sleep(1)
        else:
            print('Timeout — saving anyway', flush=True)

        cookies = await page.context.cookies()
        cookie_list = [{'name': c['name'], 'value': c['value']} for c in cookies]
        out = os.path.join(DATA_DIR, 'cookie_fresh.json')
        with open(out, 'w') as f:
            json.dump(cookie_list, f, indent=2)
        print(f'SAVED {len(cookie_list)} cookies to {out}', flush=True)
        await browser.close()

asyncio.run(main())
