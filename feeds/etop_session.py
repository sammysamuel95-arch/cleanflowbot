"""
feeds/etop_session.py — Etopfun session immortality.

Detects dead sessions (low parent count / failed keepalive) and
auto-recovers via Playwright persistent Chrome profile with Steam SSO.

Recovery chain:
  FAST PATH: loginsteam.do → Steam auto-approves (saved profile) → cookies
  SLOW PATH: loginsteam.do → Steam login form → fill creds → Steam Guard email
             → Protonmail tab → grab code → submit → cookies

Usage:
    session_mgr = EtopSessionManager(
        session_file='data/auth/session.json',
        profile_dir='data/playwright_etop_profile',
        etop_base_url='https://www.etopfun.com',
    )
    recovered = await session_mgr.auto_recover(etop_api)
"""

import asyncio
import json
import os
import re
import time

from core.logger import log_info, log_warn, log_error

# Path to credentials file — relative to project root (this file is feeds/etop_session.py)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CREDS_FILE = os.path.join(_PROJECT_ROOT, 'data', 'auth', 'credentials.json')
_DEBUG_DIR = os.path.join(_PROJECT_ROOT, 'data')


def _load_secrets() -> dict:
    """Load data/auth/credentials.json and return as uppercase-key dict for compatibility."""
    try:
        with open(_CREDS_FILE) as f:
            creds = json.load(f)
        return {
            'STEAM_USER':  creds.get('steam_user', ''),
            'STEAM_PASS':  creds.get('steam_pass', ''),
            'PROTON_EMAIL': creds.get('proton_email', ''),
            'PROTON_PASS': creds.get('proton_pass', ''),
        }
    except Exception:
        return {}


async def _screenshot(page, tag: str):
    """Save debug screenshot to data/. Swallow errors."""
    try:
        path = os.path.join(_DEBUG_DIR, f"debug_{tag}_{int(time.time())}.png")
        await page.screenshot(path=path)
        log_info(f"[ETOP_SESSION] Screenshot: {path}")
    except Exception:
        pass


class EtopSessionManager:

    def __init__(self, session_file: str, profile_dir: str, etop_base_url: str):
        self.session_file = session_file
        self.profile_dir = profile_dir
        self.etop_base_url = etop_base_url

    # ── Health check ──────────────────────────────────────────────────────────

    async def is_healthy(self, etop_api, expected_min_parents: int = 10) -> bool:
        """Return True only if match_list returns enough parents AND keepalive passes."""
        try:
            parents, _ = await asyncio.wait_for(etop_api.match_list(), timeout=10)
            if len(parents) < expected_min_parents:
                log_warn("ETOP_SESSION",
                         f"is_healthy: {len(parents)} parents < {expected_min_parents}")
                return False
        except Exception as e:
            log_warn("ETOP_SESSION", f"is_healthy match_list error: {e}")
            return False

        # userconn_check requires _uuid — skip if not set
        if etop_api._uuid:
            try:
                alive = await asyncio.wait_for(etop_api.userconn_check(), timeout=5)
                if not alive:
                    log_warn("ETOP_SESSION", "is_healthy: userconn_check failed")
                    return False
            except Exception as e:
                log_warn("ETOP_SESSION", f"is_healthy userconn_check error: {e}")
                return False

        return True

    # ── OpenID confirmation ───────────────────────────────────────────────────

    async def _handle_openid_confirm(self, page):
        """Click 'Sign In' on Steam's OpenID confirmation page if present.

        After Guard code (or sometimes after plain login), Steam shows:
          'Sign into sso.5etrade.com using your Steam account'
        with a green 'Sign In' button. We must click it to complete the OAuth flow.
        """
        try:
            current = page.url
            # loginform = login page, not confirmation page. Confirmation page
            # is /openid/login (no "form" in path) and shows the "Sign In" button.
            is_openid = (('steamcommunity.com/openid' in current or
                          'steampowered.com/openid' in current) and
                         'loginform' not in current)
            if not is_openid:
                return

            log_info(f"[STEAM] OpenID confirmation page detected: {current[:80]}")

            for sel in (
                'input[type="submit"][value="Sign In"]',
                'input[type="submit"]',
                'button:has-text("Sign In")',
                'button[type="submit"]',
            ):
                try:
                    el = await page.wait_for_selector(sel, timeout=4000, state='visible')
                    if el:
                        await el.click()
                        log_info(f"[STEAM] OpenID 'Sign In' clicked via {sel}")
                        return
                except Exception:
                    continue

            log_warn("ETOP_SESSION", "OpenID confirmation page found but Sign In button not clicked")
            await _screenshot(page, "steam_openid_no_button")
        except Exception as e:
            log_warn("ETOP_SESSION", f"_handle_openid_confirm error: {e}")

    # ── Steam form fill ───────────────────────────────────────────────────────

    async def _fill_steam_form(self, page, secrets: dict) -> bool:
        """Fill Steam login form with credentials from secrets. Returns True if submitted."""
        steam_user = secrets.get('STEAM_USER', '')
        steam_pass = secrets.get('STEAM_PASS', '')
        if not steam_user or not steam_pass or 'REPLACE_ME' in steam_user:
            log_error("ETOP_SESSION", "Steam credentials not set in secrets.env")
            return False

        log_info("[STEAM] Filling login form (credentials loaded: yes)")

        # Username field
        await page.wait_for_timeout(2000)  # let page settle before looking for fields
        username_selectors = [
            'input[type="text"]',
            '#input_username',
            'input[name="username"]',
            'input[autocomplete="username"]',
        ]
        filled_user = False
        for sel in username_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=8000, state='visible')
                if el:
                    await el.fill(steam_user)
                    filled_user = True
                    log_info(f"[STEAM] Username filled via {sel}")
                    break
            except Exception:
                continue

        if not filled_user:
            log_error("ETOP_SESSION", "Could not find Steam username field")
            await _screenshot(page, "steam_no_username")
            return False

        # Password field
        password_selectors = [
            'input[type="password"]',
            '#input_password',
            'input[name="password"]',
            'input[autocomplete="current-password"]',
        ]
        filled_pass = False
        for sel in password_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=4000, state='visible')
                if el:
                    await el.fill(steam_pass)
                    filled_pass = True
                    log_info(f"[STEAM] Password filled via {sel}")
                    break
            except Exception:
                continue

        if not filled_pass:
            log_error("ETOP_SESSION", "Could not find Steam password field")
            await _screenshot(page, "steam_no_password")
            return False

        await page.wait_for_timeout(500)  # human-like pause

        # Submit button
        submit_selectors = [
            'button[type="submit"]',
            '#login_btn_signin button',
            'button:has-text("Sign in")',
            'button:has-text("Sign In")',
        ]
        clicked = False
        for sel in submit_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=4000, state='visible')
                if el:
                    await el.click()
                    clicked = True
                    log_info(f"[STEAM] Submit clicked via {sel}")
                    break
            except Exception:
                continue

        if not clicked:
            # Last resort: press Enter in the password field
            log_warn("ETOP_SESSION", "Submit button not found — pressing Enter")
            try:
                await page.keyboard.press('Enter')
                clicked = True
            except Exception:
                pass

        if not clicked:
            log_error("ETOP_SESSION", "Could not submit Steam login form")
            await _screenshot(page, "steam_no_submit")
            return False

        return True

    # ── Protonmail code grab ──────────────────────────────────────────────────

    async def _get_proton_code(self, ctx, secrets: dict) -> str:
        """Open Protonmail in new tab, find Steam Guard email, extract 5-char code.

        Returns the code string, or '' on failure.
        """
        proton_email = secrets.get('PROTON_EMAIL', '')
        proton_pass = secrets.get('PROTON_PASS') or secrets.get('PROTON_BRIDGE_PASS', '')

        if not proton_email or not proton_pass or 'REPLACE_ME' in proton_email:
            log_error("ETOP_SESSION", "Protonmail credentials not set in secrets.env")
            return ''

        log_info("[STEAM] Opening Protonmail to grab Guard code...")
        ppage = await ctx.new_page()
        guard_code = ''

        # Record start time — we only want emails AFTER this point
        _poll_start = time.time()

        try:
            await ppage.goto('https://mail.proton.me',
                             wait_until='domcontentloaded', timeout=20000)
            await ppage.wait_for_timeout(3000)
            log_info(f"[STEAM] Protonmail URL after load: {ppage.url}")
            await _screenshot(ppage, "proton_after_nav")

            # ── Detect login form — search main frame AND all iframes ────
            # account.proton.me embeds the login form inside an iframe.
            # page.wait_for_selector() only searches the main frame, so we
            # must iterate ppage.frames to find which frame has the inputs.

            login_frame = None
            await ppage.wait_for_timeout(2000)  # let iframes render

            all_frames = ppage.frames
            log_info(f"[STEAM] Protonmail frames: {len(all_frames)} — {[f.url for f in all_frames]}")

            for frame in all_frames:
                try:
                    inputs = await frame.query_selector_all('input')
                    if inputs:
                        login_frame = frame
                        log_info(f"[STEAM] Login form frame: {frame.url} ({len(inputs)} inputs)")
                        break
                except Exception:
                    continue

            needs_login = login_frame is not None
            if not needs_login:
                log_info("[STEAM] No login form found in any frame — assuming already logged in")

            if needs_login:
                frame = login_frame
                log_info("[STEAM] Logging into Protonmail via frame...")

                # Click + type (not just fill) to ensure React state updates
                filled_email = False
                for sel in ('input[type="email"]', 'input[type="text"]',
                            'input[id="username"]', 'input'):
                    try:
                        el = await frame.wait_for_selector(sel, timeout=3000, state='visible')
                        if el:
                            await el.click()
                            await el.fill('')
                            await el.type(proton_email, delay=30)
                            filled_email = True
                            log_info(f"[STEAM] Protonmail email typed via {sel}")
                            break
                    except Exception:
                        continue

                if not filled_email:
                    log_error("ETOP_SESSION", "Protonmail email field not found in any frame")
                    await _screenshot(ppage, "proton_no_email_field")
                    await ppage.close()
                    return ''

                await ppage.wait_for_timeout(300)

                # Password
                try:
                    el = await frame.wait_for_selector(
                        'input[type="password"]', timeout=5000, state='visible')
                    await el.click()
                    await el.fill('')
                    await el.type(proton_pass, delay=30)
                    log_info("[STEAM] Protonmail password typed")
                except Exception as e:
                    log_error("ETOP_SESSION", f"Protonmail password field not found: {e}")
                    await _screenshot(ppage, "proton_no_password")
                    await ppage.close()
                    return ''

                await ppage.wait_for_timeout(300)

                # Submit
                submitted = False
                for sel in ('button[type="submit"]', 'button'):
                    try:
                        el = await frame.wait_for_selector(sel, timeout=5000, state='visible')
                        if el:
                            await el.click()
                            submitted = True
                            log_info("[STEAM] Protonmail login submitted")
                            break
                    except Exception:
                        continue

                if not submitted:
                    await frame.locator('input[type="password"]').press('Enter')
                    log_info("[STEAM] Protonmail login submitted via Enter")

                # Wait for inbox
                log_info("[STEAM] Waiting for Protonmail inbox after login...")
                await ppage.wait_for_timeout(6000)
                await _screenshot(ppage, "proton_after_login")
                log_info(f"[STEAM] Protonmail URL after login: {ppage.url}")

                # Check for 2FA
                content = await ppage.content()
                if 'two-factor' in content.lower() or '2fa' in content.lower():
                    log_error("ETOP_SESSION",
                              "Protonmail 2FA required — disable 2FA on this account")
                    await _screenshot(ppage, "proton_2fa")
                    await ppage.close()
                    return ''

                await ppage.wait_for_timeout(3000)

            _INBOX_SELS = (
                '[data-shortcut-target="item-container"]',
                '[data-testid="message-list-item"]',
                'li[data-element-id]',
                '.message-list-item',
                '[role="row"]',
            )

            # Wait for Steam to send the new email (usually arrives in 10-15s)
            log_info("[STEAM] Waiting 10s for Steam Guard email to arrive...")
            await ppage.wait_for_timeout(10000)

            # ── Poll for Steam thread (up to 90s) ────────────────────────
            # Always open the first (newest) Steam thread — Protonmail conversation
            # threads accumulate emails. We scroll to the bottom after clicking so
            # the about:blank iframe shows the NEWEST email. No snapshot needed —
            # reversed(candidates) picks the last (freshest) code in the thread.
            log_info("[STEAM] Polling inbox for Steam Guard email...")
            MAX_WAIT = 90
            INTERVAL = 8

            _NOT_CODE = {
                'EMAIL', 'STEAM', 'GUARD', 'STORE', 'ABOUT', 'CLICK',
                'LOGIN', 'ENTER', 'CODES', 'INBOX', 'PROTON', 'VALVE',
                'LEGAL', 'GAMES', 'ABOVE', 'LOWER', 'UPPER', 'RESET',
                'ALERT', 'CHECK', 'TRUST',
            }

            for attempt in range(MAX_WAIT // INTERVAL):
                try:
                    items = []
                    used_sel = ''
                    for sel in _INBOX_SELS:
                        found = await ppage.query_selector_all(sel)
                        if found:
                            items = found
                            used_sel = sel
                            break

                    log_info(f"[STEAM] Inbox: {len(items)} items via '{used_sel}' "
                             f"(attempt {attempt + 1}/{MAX_WAIT // INTERVAL})")

                    # Take the first Steam thread item — newest conversation is at top
                    steam_item = None
                    for item in items:
                        try:
                            text = await item.inner_text()
                        except Exception:
                            continue
                        if 'steam' in text.lower() or 'steampowered' in text.lower():
                            steam_item = item
                            log_info(f"[STEAM] Found Steam email item (attempt {attempt + 1})")
                            break

                    if steam_item:
                        # Snapshot frame identity before click (use object id, not URL —
                        # multiple about:blank frames all share the same URL string)
                        frames_before_ids = set(id(f) for f in ppage.frames)

                        await steam_item.click()
                        log_info("[STEAM] Clicked email — waiting for body iframe...")

                        # Wait for inbox URL to appear (conversation opened)
                        await ppage.wait_for_timeout(2000)

                        # Scroll Protonmail's internal conversation container to bottom
                        # so the NEWEST email in the thread is rendered/expanded.
                        try:
                            await ppage.evaluate("""
                                // Scroll every overflow container on the page to its bottom.
                                // This catches Protonmail's conversation message list.
                                document.querySelectorAll('*').forEach(el => {
                                    try {
                                        if (el.scrollHeight > el.clientHeight + 100
                                                && el.clientHeight > 50) {
                                            el.scrollTop = el.scrollHeight;
                                        }
                                    } catch(e) {}
                                });
                            """)
                            await ppage.wait_for_timeout(3000)  # let lazy-load render
                        except Exception:
                            pass

                        # Wait up to 8s for a NEW frame (any URL) to appear
                        email_frame = None
                        deadline = time.time() + 8
                        while time.time() < deadline:
                            await ppage.wait_for_timeout(500)
                            current_frames = ppage.frames
                            for f in current_frames:
                                if id(f) not in frames_before_ids:
                                    try:
                                        t = await f.inner_text('body')
                                        if len(t) > 50:
                                            email_frame = f
                                            log_info(f"[STEAM] Email body frame: {f.url[:80]}")
                                            break
                                    except Exception:
                                        continue
                            if email_frame:
                                break

                        await ppage.wait_for_timeout(500)
                        await _screenshot(ppage, "proton_email_open")

                        # Read text from ALL frames, log each separately.
                        # NEW frames (id not in frames_before_ids) = email body iframes.
                        # Run extraction first on NEW frames only (cleanest signal),
                        # then fall back to all frames combined.
                        new_frame_texts = []
                        all_text_parts = []
                        for frame in ppage.frames:
                            try:
                                t = await frame.inner_text('body')
                                if not t.strip():
                                    continue
                                all_text_parts.append(t)
                                if id(frame) not in frames_before_ids:
                                    new_frame_texts.append(t)
                                # Log first 200 chars of each frame separately
                                preview = t[:200].replace('\n', ' ')
                                log_info(f"[STEAM] Frame [{frame.url[:50]}]: {preview}")
                            except Exception:
                                continue

                        # Strategy 1: search NEW frames first (email body only),
                        # then combined text. The code appears alone on its own line
                        # in the email, separated from surrounding text.
                        search_texts = new_frame_texts if new_frame_texts else all_text_parts
                        for text_src in search_texts:
                            m = re.search(
                                r'(?:steam guard code|guard code|access your account)'
                                r'[\s\S]{0,200}?'          # up to 200 chars including newlines
                                r'\n\s*([A-Z0-9]{5})\s*\n', # code alone on its own line
                                text_src, re.IGNORECASE)
                            if m and m.group(1).upper() not in _NOT_CODE:
                                guard_code = m.group(1).upper()
                                log_info(f"[STEAM] Guard code (context): {guard_code}")
                                break

                        full_text = '\n'.join(all_text_parts)

                        # Strategy 2: all 5-char candidates, skip known words.
                        # Take the LAST qualifying candidate — conversation thread shows
                        # oldest email first, newest last, so last code = freshest.
                        if not guard_code:
                            candidates = re.findall(
                                r'(?<![A-Z0-9])([A-Z0-9]{5})(?![A-Z0-9])', full_text)
                            log_info(f"[STEAM] All candidates: {candidates}")
                            # Last mixed (alpha+digit) candidate
                            for c in reversed(candidates):
                                if c not in _NOT_CODE and re.search(r'[0-9]', c) \
                                        and re.search(r'[A-Z]', c):
                                    guard_code = c
                                    log_info(f"[STEAM] Guard code (last mixed): {guard_code}")
                                    break
                            if not guard_code:
                                for c in reversed(candidates):
                                    if c not in _NOT_CODE:
                                        guard_code = c
                                        log_info(f"[STEAM] Guard code (last fallback): {guard_code}")
                                        break

                        if not guard_code:
                            log_warn("ETOP_SESSION", "Steam email opened but no code extracted")
                            await _screenshot(ppage, "proton_email_no_code")
                        break

                    if guard_code:
                        break

                    await ppage.wait_for_timeout(INTERVAL * 1000)

                    # Refresh inbox
                    try:
                        reload_btn = await ppage.query_selector(
                            '[data-testid="toolbar:refreshButton"], button[title*="efresh"]')
                        if reload_btn:
                            await reload_btn.click()
                            await ppage.wait_for_timeout(1500)
                        else:
                            await ppage.reload()
                            await ppage.wait_for_timeout(3000)
                    except Exception:
                        pass

                except Exception as e:
                    log_warn("ETOP_SESSION", f"Inbox poll error (attempt {attempt + 1}): {e}")
                    await ppage.wait_for_timeout(INTERVAL * 1000)

            if not guard_code:
                log_error("ETOP_SESSION", "Steam Guard email not found after 90s")
                await _screenshot(ppage, "proton_no_guard_email")

        except Exception as e:
            log_error("ETOP_SESSION", f"_get_proton_code failed: {e}")
            await _screenshot(ppage, "proton_exception")
            guard_code = ''

        try:
            await ppage.close()
        except Exception:
            pass

        return guard_code

    # ── Steam Guard code entry ────────────────────────────────────────────────

    async def _submit_steam_guard(self, page, code: str) -> bool:
        """Enter Guard code on Steam Guard page and submit.

        Steam Guard uses individual character boxes that auto-advance.
        Click the first box, then keyboard.type() the full code — the browser
        advances between boxes automatically.
        """
        log_info(f"[STEAM] Submitting Guard code: {code}")

        guard_input_selectors = [
            'input[type="text"]',
            'input[maxlength="1"]',
            'input.authcode_entry',
            'input[name="authcode"]',
            '#authcode',
        ]

        first_input = None
        for sel in guard_input_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=3000, state='visible')
                if el:
                    first_input = el
                    log_info(f"[STEAM] Guard input found via {sel}")
                    break
            except Exception:
                continue

        if not first_input:
            log_error("ETOP_SESSION", "Could not find Steam Guard input field")
            await _screenshot(page, "steam_guard_no_input")
            return False

        # Clear any previous attempt, click first box, type full code.
        # keyboard.type() triggers keydown/keyup and respects auto-advance between boxes.
        await first_input.click()
        await page.wait_for_timeout(200)
        # Select all + delete to clear any previously typed code
        await page.keyboard.press('Control+a')
        await page.keyboard.press('Delete')
        await page.wait_for_timeout(100)
        await page.keyboard.type(code, delay=100)
        log_info(f"[STEAM] Guard code typed via keyboard: {code}")

        await _screenshot(page, "steam_guard_after_type")
        await page.wait_for_timeout(500)

        # Try submit button, fallback to Enter
        guard_submit_selectors = [
            'button[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Continue")',
            '#auth_buttonset_entercode button',
        ]

        submitted = False
        for sel in guard_submit_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=3000, state='visible')
                if el:
                    await el.click()
                    submitted = True
                    log_info(f"[STEAM] Guard submit clicked via {sel}")
                    break
            except Exception:
                continue

        if not submitted:
            log_info("[STEAM] Guard submit button not found — pressing Enter")
            await page.keyboard.press('Enter')
            submitted = True

        return submitted

    # ── Playwright login ──────────────────────────────────────────────────────

    async def refresh_login(self, headless: bool = False) -> dict:
        """Launch Chrome with persistent profile, navigate Steam SSO, extract cookies.

        FAST PATH: Steam profile still valid → instant redirect to etopfun
        SLOW PATH: Steam logged off → fill form → optional Guard code from Protonmail

        Returns {name: value} dict of etopfun cookies, or {} on failure.
        Saves cookies to session_file in list format (matches load_etop_cookies).
        """
        from playwright.async_api import async_playwright

        os.makedirs(self.profile_dir, exist_ok=True)
        secrets = _load_secrets()
        creds_available = bool(
            secrets.get('STEAM_USER') and
            secrets.get('STEAM_PASS') and
            'REPLACE_ME' not in secrets.get('STEAM_USER', '')
        )
        log_info(f"[ETOP_SESSION] refresh_login headless={headless} "
                 f"creds_available={creds_available}")

        try:
            async with async_playwright() as p:
                ctx = await p.chromium.launch_persistent_context(
                    self.profile_dir,
                    headless=headless,
                    args=['--no-sandbox', '--disable-dev-shm-usage'],
                )
                page = await ctx.new_page()

                # ── Step 1: Navigate to Steam SSO ─────────────────────────
                log_info("[ETOP_SESSION] Navigating to Steam SSO...")
                try:
                    await page.goto(
                        f"{self.etop_base_url}/loginsteam.do",
                        wait_until='domcontentloaded',
                        timeout=15000,
                    )
                except Exception as e:
                    log_warn("ETOP_SESSION", f"Initial navigation error: {e}")

                # ── Step 2: Wait to see where we land ─────────────────────
                # Give up to 30s for the redirect to settle
                try:
                    await page.wait_for_url(
                        lambda url: (
                            ('etopfun.com' in url and 'loginsteam' not in url) or
                            'steamcommunity.com' in url or
                            'steampowered.com' in url
                        ),
                        timeout=30000,
                    )
                except Exception:
                    pass  # check current URL regardless

                current_url = page.url
                log_info(f"[ETOP_SESSION] Landed at: {current_url}")

                # ── CASE A: Already on etopfun — fast path done ───────────
                if 'etopfun.com' in current_url and 'loginsteam' not in current_url:
                    log_info("[ETOP_SESSION] Fast path: Steam auto-approved")
                    return await self._extract_and_save_cookies(ctx)

                # ── CASE B: Steam login page ──────────────────────────────
                is_steam = ('steamcommunity.com' in current_url or
                            'steampowered.com' in current_url)

                if is_steam:
                    # Check for OpenID consent page first (already logged in Steam)
                    # URL is /openid/login but no loginform — just a "Sign In" button
                    is_openid_consent = (
                        'openid' in current_url and 'loginform' not in current_url
                    )
                    if is_openid_consent:
                        log_info("[ETOP_SESSION] OpenID consent page — clicking Sign In")
                        await self._handle_openid_confirm(page)
                        try:
                            await page.wait_for_url(
                                lambda url: 'etopfun.com' in url and 'loginsteam' not in url,
                                timeout=15000,
                            )
                        except Exception:
                            pass
                        if 'etopfun.com' in page.url and 'loginsteam' not in page.url:
                            return await self._extract_and_save_cookies(ctx)
                        log_error("ETOP_SESSION", "OpenID consent click did not land on etopfun")
                        await _screenshot(page, "steam_openid_consent_fail")
                        await ctx.close()
                        return {}

                    if not creds_available:
                        if not headless:
                            # No creds → fallback to manual login
                            log_warn("ETOP_SESSION",
                                     "Steam login page — no credentials in secrets.env. "
                                     "Complete login manually (120s)...")
                            try:
                                await page.wait_for_url(
                                    lambda url: 'etopfun.com' in url and 'loginsteam' not in url,
                                    timeout=120000,
                                )
                                log_info(f"[ETOP_SESSION] Manual login completed: {page.url}")
                                return await self._extract_and_save_cookies(ctx)
                            except Exception:
                                log_error("ETOP_SESSION", "Manual Steam login timed out")
                                await _screenshot(page, "steam_manual_timeout")
                                await ctx.close()
                                return {}
                        else:
                            log_error("ETOP_SESSION",
                                      "Steam login page + headless + no credentials. "
                                      "Set STEAM_USER/STEAM_PASS in data/secrets.env")
                            await ctx.close()
                            return {}

                    # ── Fill Steam form ───────────────────────────────────
                    submitted = await self._fill_steam_form(page, secrets)
                    if not submitted:
                        await ctx.close()
                        return {}

                    # ── Wait for response after form submit ───────────────
                    log_info("[STEAM] Waiting for Steam response after submit...")
                    await page.wait_for_timeout(3000)

                    # Poll up to 15s to see what happened
                    for _ in range(5):
                        current_url = page.url
                        content = await page.content()
                        content_lower = content.lower()

                        if 'etopfun.com' in current_url and 'loginsteam' not in current_url:
                            log_info("[STEAM] Logged in without Guard (no 2FA)")
                            return await self._extract_and_save_cookies(ctx)

                        guard_triggered = (
                            'guard' in content_lower or
                            'enter the code' in content_lower or
                            'check your email' in content_lower or
                            'steamguard' in current_url.lower()
                        )
                        if guard_triggered:
                            break

                        wrong_creds = (
                            'incorrect' in content_lower or
                            'wrong password' in content_lower or
                            'invalid' in content_lower
                        )
                        if wrong_creds:
                            log_error("ETOP_SESSION",
                                      "Steam rejected credentials — check STEAM_USER/STEAM_PASS")
                            await _screenshot(page, "steam_wrong_creds")
                            await ctx.close()
                            return {}

                        await page.wait_for_timeout(3000)

                    # Handle OpenID confirmation page (appears after plain login, no Guard)
                    await self._handle_openid_confirm(page)
                    await page.wait_for_timeout(2000)

                    current_url = page.url
                    if 'etopfun.com' in current_url and 'loginsteam' not in current_url:
                        return await self._extract_and_save_cookies(ctx)

                    # ── Steam Guard code flow ─────────────────────────────
                    log_info("[STEAM] Guard code required — fetching from Protonmail")
                    await _screenshot(page, "steam_guard_page")

                    code = await self._get_proton_code(ctx, secrets)
                    if not code:
                        log_error("ETOP_SESSION", "Failed to get Guard code from Protonmail")
                        await ctx.close()
                        return {}

                    submitted_guard = await self._submit_steam_guard(page, code)
                    if not submitted_guard:
                        await ctx.close()
                        return {}

                    # ── Wait for final redirect to etopfun ────────────────
                    log_info("[STEAM] Guard submitted — waiting for etopfun redirect...")

                    # Steam may show OpenID confirmation before redirecting to etopfun
                    # Poll until we land on etopfun or give up after 30s
                    for _ in range(15):
                        await page.wait_for_timeout(2000)
                        await self._handle_openid_confirm(page)
                        if 'etopfun.com' in page.url:
                            break

                    try:
                        await page.wait_for_url(
                            lambda url: 'etopfun.com' in url and 'loginsteam' not in url,
                            timeout=20000,
                        )
                        log_info(f"[ETOP_SESSION] Landed at: {page.url}")
                        return await self._extract_and_save_cookies(ctx)
                    except Exception:
                        log_error("ETOP_SESSION",
                                  f"Did not reach etopfun after Guard. URL: {page.url}")
                        await _screenshot(page, "steam_guard_no_redirect")
                        await ctx.close()
                        return {}

                # ── CASE C: Unknown landing page ──────────────────────────
                log_warn("ETOP_SESSION",
                         f"Unexpected landing URL: {current_url} — trying cookie grab anyway")
                await _screenshot(page, "unknown_landing")
                return await self._extract_and_save_cookies(ctx)

        except Exception as e:
            log_error("ETOP_SESSION", f"refresh_login failed: {e}")
            return {}

    # ── Cookie extraction helper ──────────────────────────────────────────────

    async def _extract_and_save_cookies(self, ctx) -> dict:
        """Extract etopfun cookies from context, save to session_file, close ctx."""
        try:
            raw_cookies = await ctx.cookies()
            etop_cookies = [
                c for c in raw_cookies
                if 'etopfun' in c.get('domain', '')
            ]

            await ctx.close()

            if not etop_cookies:
                log_warn("ETOP_SESSION", "No etopfun cookies found in context")
                return {}

            os.makedirs(os.path.dirname(self.session_file), exist_ok=True)
            with open(self.session_file, 'w') as f:
                json.dump(etop_cookies, f, indent=2)

            cookies = {c['name']: c['value'] for c in etop_cookies}
            names = list(cookies.keys())
            log_info(f"[ETOP_SESSION] Saved {len(etop_cookies)} cookies: {names}")

            for key in ('JSESSIONID', 'DJSP_UUID', 'DJSP_USER'):
                if key not in cookies:
                    log_warn("ETOP_SESSION", f"Missing expected cookie: {key}")

            return cookies

        except Exception as e:
            log_error("ETOP_SESSION", f"_extract_and_save_cookies failed: {e}")
            try:
                await ctx.close()
            except Exception:
                pass
            return {}

    # ── Session reload ────────────────────────────────────────────────────────

    def reload_etop_session(self, etop_api, cookies: dict):
        """Replace etop_api.session in-place with a new aiohttp session.

        All callers that hold a reference to etop_api automatically get
        the new session because we mutate the object, not the reference.
        """
        from feeds.etopfun_api import create_etop_session, build_etop_headers

        old_session = etop_api.session
        try:
            loop = asyncio.get_event_loop()
            if not old_session.closed:
                loop.create_task(old_session.close())
        except Exception:
            pass

        etop_api.session = create_etop_session(cookies)
        etop_api.headers = build_etop_headers()

        if 'DJSP_UUID' in cookies:
            etop_api.set_uuid(cookies['DJSP_UUID'])

        log_info("[ETOP_SESSION] Session reloaded with fresh cookies")

    # ── Auto recovery ─────────────────────────────────────────────────────────

    async def auto_recover(self, etop_api, headless: bool = False) -> bool:
        """refresh_login → reload_etop_session → verify is_healthy. Max 3 attempts."""
        MAX_ATTEMPTS = 3
        GAP_SECS = 30

        for attempt in range(1, MAX_ATTEMPTS + 1):
            log_info(f"[ETOP_SESSION] auto_recover attempt {attempt}/{MAX_ATTEMPTS}")

            cookies = await self.refresh_login(headless=headless)
            if not cookies:
                log_warn("ETOP_SESSION",
                         f"refresh_login returned no cookies (attempt {attempt})")
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(GAP_SECS)
                continue

            self.reload_etop_session(etop_api, cookies)
            await asyncio.sleep(2)

            healthy = await self.is_healthy(etop_api)
            if healthy:
                log_info(f"[ETOP_SESSION] Recovery successful on attempt {attempt}")
                return True

            log_warn("ETOP_SESSION",
                     f"is_healthy failed after reload (attempt {attempt})")
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(GAP_SECS)

        log_error("ETOP_SESSION", "auto_recover FAILED after all attempts")
        return False
