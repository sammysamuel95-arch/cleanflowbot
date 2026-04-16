"""
debug_proton_email.py — Standalone Protonmail email body inspector.

Opens Protonmail, logs in, clicks the latest Steam email, dumps the FULL
text of every frame so we can nail the Guard code regex without touching Steam.

Usage:
    python3 debug_proton_email.py
"""

import asyncio
import os
import re
import time

# Load secrets the same way etop_session.py does
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_SECRETS_FILE = os.path.join(_PROJECT_ROOT, 'data', 'secrets.env')
_DATA_DIR     = os.path.join(_PROJECT_ROOT, 'data')

def _load_secrets() -> dict:
    try:
        from dotenv import dotenv_values
        return dotenv_values(_SECRETS_FILE)
    except ImportError:
        vals = {}
        if not os.path.exists(_SECRETS_FILE):
            return vals
        with open(_SECRETS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                vals[k.strip()] = v.strip()
        return vals


async def main():
    from playwright.async_api import async_playwright

    secrets = _load_secrets()
    proton_email = secrets.get('PROTON_EMAIL', '')
    proton_pass  = secrets.get('PROTON_PASS') or secrets.get('PROTON_BRIDGE_PASS', '')

    if not proton_email or not proton_pass:
        print("ERROR: PROTON_EMAIL / PROTON_BRIDGE_PASS not set in data/secrets.env")
        return

    # Use a TEMP profile (don't pollute the real one)
    profile_dir = os.path.join(_DATA_DIR, 'debug_proton_profile')
    os.makedirs(profile_dir, exist_ok=True)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            profile_dir,
            headless=False,
            args=['--no-sandbox'],
        )
        page = await ctx.new_page()

        # ── 1. Navigate to Protonmail ─────────────────────────────────────
        print("\n[1] Navigating to Protonmail...")
        await page.goto('https://mail.proton.me', wait_until='domcontentloaded', timeout=20000)
        await page.wait_for_timeout(3000)
        print(f"    URL after load: {page.url}")

        # ── 2. Log in if needed ───────────────────────────────────────────
        all_frames = page.frames
        print(f"[2] Frames ({len(all_frames)}): {[f.url for f in all_frames]}")

        login_frame = None
        for frame in all_frames:
            try:
                inputs = await frame.query_selector_all('input')
                if inputs:
                    login_frame = frame
                    print(f"    Login form frame: {frame.url} ({len(inputs)} inputs)")
                    break
            except Exception:
                continue

        if login_frame:
            print("[2] Logging in...")
            frame = login_frame

            for sel in ('input[type="email"]', 'input[type="text"]',
                        'input[id="username"]', 'input'):
                try:
                    el = await frame.wait_for_selector(sel, timeout=3000, state='visible')
                    if el:
                        await el.click()
                        await el.fill('')
                        await el.type(proton_email, delay=30)
                        print(f"    Email typed via {sel}")
                        break
                except Exception:
                    continue

            await page.wait_for_timeout(300)

            try:
                el = await frame.wait_for_selector('input[type="password"]',
                                                    timeout=5000, state='visible')
                await el.click()
                await el.fill('')
                await el.type(proton_pass, delay=30)
                print("    Password typed")
            except Exception as e:
                print(f"    ERROR: password field not found: {e}")
                await ctx.close()
                return

            await page.wait_for_timeout(300)
            for sel in ('button[type="submit"]', 'button'):
                try:
                    el = await frame.wait_for_selector(sel, timeout=5000, state='visible')
                    if el:
                        await el.click()
                        print(f"    Submit clicked via {sel}")
                        break
                except Exception:
                    continue

            print("    Waiting for inbox to load...")
            try:
                await page.wait_for_url(
                    lambda url: 'mail.proton.me/u/' in url or 'mail.proton.me/#' in url,
                    timeout=30000,
                )
            except Exception:
                pass
            await page.wait_for_timeout(4000)
            print(f"    URL after login: {page.url}")
        else:
            print("[2] Already logged in — skipping login")

        await page.wait_for_timeout(2000)

        # Screenshot: inbox
        ss_inbox = os.path.join(_DATA_DIR, f"debug_proton_inbox_{int(time.time())}.png")
        await page.screenshot(path=ss_inbox)
        print(f"\n[SCREENSHOT] Inbox: {ss_inbox}")

        # ── 3. Find latest Steam email thread ─────────────────────────────
        print("\n[3] Looking for Steam email thread...")
        _INBOX_SELS = (
            '[data-shortcut-target="item-container"]',
            '[data-testid="message-list-item"]',
            'li[data-element-id]',
            '.message-list-item',
            '[role="row"]',
        )
        items = []
        used_sel = ''
        for sel in _INBOX_SELS:
            found = await page.query_selector_all(sel)
            if found:
                items = found
                used_sel = sel
                break

        print(f"    {len(items)} inbox items via '{used_sel}'")

        steam_item = None
        for item in items:
            try:
                text = await item.inner_text()
            except Exception:
                continue
            print(f"    ITEM: {repr(text[:80])}")
            if 'steam' in text.lower():
                steam_item = item
                print(f"    -> Chose this Steam item")
                break

        if not steam_item:
            print("ERROR: No Steam email found in inbox")
            await ctx.close()
            return

        # ── 4. Click thread, scroll to bottom, wait for iframes ──────────
        print("\n[4] Clicking Steam thread...")
        frames_before_ids = set(id(f) for f in page.frames)
        print(f"    Frames before click: {len(page.frames)}")

        await steam_item.click()
        await page.wait_for_timeout(2000)

        # Scroll all containers to bottom
        print("    Scrolling all containers to bottom...")
        await page.evaluate("""
            document.querySelectorAll('*').forEach(el => {
                try {
                    if (el.scrollHeight > el.clientHeight + 100 && el.clientHeight > 50) {
                        el.scrollTop = el.scrollHeight;
                    }
                } catch(e) {}
            });
        """)
        await page.wait_for_timeout(4000)

        print(f"    Frames after scroll: {len(page.frames)}")

        # Screenshot: email open
        ss_email = os.path.join(_DATA_DIR, f"debug_proton_email_{int(time.time())}.png")
        await page.screenshot(path=ss_email)
        print(f"\n[SCREENSHOT] Email open: {ss_email}")

        # ── 5. Dump EVERY frame's full text ──────────────────────────────
        print("\n" + "="*70)
        print("FULL FRAME DUMP")
        print("="*70)

        all_text_parts = []
        for i, frame in enumerate(page.frames):
            try:
                t = await frame.inner_text('body')
            except Exception as e:
                t = f"<error reading body: {e}>"

            is_new = id(frame) not in frames_before_ids
            print(f"\n── Frame {i}: {frame.url[:80]} {'[NEW]' if is_new else ''}")
            print(f"   Length: {len(t)} chars")
            print(f"   Content:\n{t}")
            print(f"   ---")
            if t.strip():
                all_text_parts.append(t)

        # ── 6. Run extraction logic against combined text ─────────────────
        full_text = '\n'.join(all_text_parts)

        print("\n" + "="*70)
        print("EXTRACTION ANALYSIS")
        print("="*70)

        # All 5-char uppercase+digit tokens
        candidates = re.findall(r'(?<![A-Z0-9])([A-Z0-9]{5})(?![A-Z0-9])', full_text)
        print(f"\nAll 5-char [A-Z0-9] candidates: {candidates}")

        _NOT_CODE = {
            'EMAIL', 'STEAM', 'GUARD', 'STORE', 'ABOUT', 'CLICK',
            'LOGIN', 'ENTER', 'CODES', 'INBOX', 'PROTON', 'VALVE',
            'LEGAL', 'GAMES', 'ABOVE', 'LOWER', 'UPPER', 'RESET',
            'ALERT', 'CHECK', 'TRUST',
        }

        print(f"Candidates not in blocklist: "
              f"{[c for c in candidates if c not in _NOT_CODE]}")

        mixed = [c for c in candidates
                 if c not in _NOT_CODE
                 and re.search(r'[0-9]', c)
                 and re.search(r'[A-Z]', c)]
        print(f"Mixed alpha+digit candidates: {mixed}")
        if mixed:
            print(f"\n>>> Strategy 2 would pick (last mixed): {mixed[-1]}")

        # Strategy 1: contextual
        context_patterns = [
            (r'(?:steam guard code|guard code)[^A-Z0-9]*([A-Z0-9]{5})(?![A-Z0-9])', 'near guard code'),
            (r'(?:access your account)[^A-Z0-9]*([A-Z0-9]{5})(?![A-Z0-9])', 'near access account'),
            (r'(?:code is|your code|the code)[^A-Z0-9]*([A-Z0-9]{5})(?![A-Z0-9])', 'near code is'),
            (r'(?:^|\n)\s*([A-Z0-9]{5})\s*(?:\n|$)', 'alone on line'),
        ]
        print("\nStrategy 1 (contextual) results:")
        for pat, label in context_patterns:
            m = re.search(pat, full_text, re.IGNORECASE | re.MULTILINE)
            if m:
                print(f"  '{label}': {m.group(1).upper()}")
            else:
                print(f"  '{label}': no match")

        print("\n" + "="*70)
        print("DONE — check screenshots and frame dump above")
        print("="*70)

        # Keep browser open for visual inspection
        print("\nBrowser stays open for 30s for visual inspection...")
        await page.wait_for_timeout(30000)

        await ctx.close()


if __name__ == '__main__':
    asyncio.run(main())
