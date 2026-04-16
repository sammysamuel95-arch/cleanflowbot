"""
feeds/etopfun_api.py — Etopfun HTTP API client.

One function per endpoint. Function named after endpoint per API_BIBLE.md.
"""

import json
import time as _time
import aiohttp
from aiohttp import FormData
from typing import List, Tuple, Optional, Dict

from config import (
    ETOP_BASE_URL, ETOP_LIST_URL, SESSION_FILE,
)
from feeds.types import InventoryItem
from core.logger import log_error, log_info, log_warn


def load_etop_cookies(session_file: str = None) -> dict:
    """Load etopfun cookies from session.json. Returns {name: value}. Empty dict if missing."""
    path = session_file or SESSION_FILE
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    cookies = {}
    cookie_list = data if isinstance(data, list) else data.get('cookies', [])
    for c in cookie_list:
        if 'etopfun' in c.get('domain', 'etopfun'):
            cookies[c['name']] = c['value']
    return cookies


def build_etop_headers() -> dict:
    """Build HTTP headers. No Cookie — jar handles it."""
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Referer": f"{ETOP_BASE_URL}/en/match/",
    }


def create_etop_session(cookies: dict) -> aiohttp.ClientSession:
    """Create aiohttp session with cookie_jar pre-loaded."""
    from http.cookies import SimpleCookie
    from yarl import URL

    jar = aiohttp.CookieJar(unsafe=True)
    url = URL(ETOP_BASE_URL)
    for name, value in cookies.items():
        jar.update_cookies(SimpleCookie(f"{name}={value}"), url)

    return aiohttp.ClientSession(
        cookie_jar=jar,
        headers=build_etop_headers(),
    )


class EtopfunAPI:
    """Etopfun HTTP API client. One method per endpoint per API_BIBLE.md."""

    def __init__(self, session: aiohttp.ClientSession, headers: dict):
        self.session = session
        self.headers = dict(headers)
        self._uuid = None
        self._tuhao_last = {}

    def set_uuid(self, uuid: str):
        self._uuid = uuid

    # ── ETOP-1: match_list ────────────────────────────────────────────────────

    async def match_list(self) -> Tuple[list, Dict[str, dict]]:
        """GET /api/match/list.do — ALL running matches.

        Returns (parents, lookup) from ONE call.
          parents = full parent list for discovery
          lookup  = {mid: {o1, o2, remain, can_press}} for monitoring
        """
        url = f"{ETOP_LIST_URL}?status=run&game=all&rows=50&page=1&lang=en"
        try:
            async with self.session.get(url, headers=self.headers) as r:
                data = await r.json()
            parents = data.get('datas', {}).get('list', [])
            lookup = {}
            for par in parents:
                for sub in par.get('sublist', []):
                    mid = str(sub.get('id', ''))
                    if not mid:
                        continue
                    lookup[mid] = {
                        'o1':        (sub.get('vs1') or {}).get('odds', 0),
                        'o2':        (sub.get('vs2') or {}).get('odds', 0),
                        'remain':    max(0.0, sub.get('remainTime', 0) / 1000.0),
                        'can_press': sub.get('canPress', False),
                        'cancel_code': sub.get('cancelCode'),
                    }
            return parents, lookup
        except Exception as e:
            log_error('etopfun_api', f'match_list failed: {e}')
            return [], {}

    # ── ETOP-2: match_subs ────────────────────────────────────────────────────

    async def match_subs(self, parent_id, sub_id) -> Optional[dict]:
        """GET /api/match/{parentId}/list.do — live odds for one sub-match.

        Returns {o1, o2, can_press, remain, support1, support2, cancel_code} or None.
        """
        url = f"{ETOP_BASE_URL}/api/match/{parent_id}/list.do?id={sub_id}&lang=en"
        try:
            async with self.session.get(url, headers=self.headers) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    return None
                datas = data["datas"]
            for m in datas.get("running", []):
                if str(m.get("id")) == str(sub_id):
                    return {
                        'o1':          (m.get("vs1") or {}).get("odds", 0),
                        'o2':          (m.get("vs2") or {}).get("odds", 0),
                        'can_press':   m.get("canPress", False),
                        'remain':      m.get("remainTime", 0) / 1000.0,
                        'cancel_code': m.get("cancelCode"),
                        'support1':    (m.get("vs1") or {}).get("support", 0),
                        'support2':    (m.get("vs2") or {}).get("support", 0),
                    }
            for m in datas.get("ended", []):
                if str(m.get("id")) == str(sub_id):
                    return {
                        'o1':        (m.get("vs1") or {}).get("odds", 0),
                        'o2':        (m.get("vs2") or {}).get("odds", 0),
                        'can_press': False,
                        'remain':    0,
                        'support1':  (m.get("vs1") or {}).get("support", 0),
                        'support2':  (m.get("vs2") or {}).get("support", 0),
                    }
            return None
        except Exception as e:
            log_error('etopfun_api', f'match_subs failed: {e}')
            return None

    # ── ETOP-3: match_show ────────────────────────────────────────────────────

    async def match_show(self, sub_id) -> Optional[dict]:
        """GET /api/match/{subMatchId}/show.do — detail + user bets."""
        url = f"{ETOP_BASE_URL}/api/match/{sub_id}/show.do?rel=match_details&loaded=false&data=loading&lang=en"
        try:
            async with self.session.get(url, headers=self.headers) as r:
                data = await r.json()
            result = data.get('datas', {})
            return result
        except Exception as e:
            log_error('etopfun_api', f'match_show failed: {e}')
            return None

    async def get_cancellable_presses(self, sub_id) -> list:
        """Convenience: returns cancellable press IDs via match_show."""
        data = await self.match_show(sub_id)
        if not data:
            return []
        press_list = data.get('user', {}).get('pressTotalList', []) or []
        return [str(e['id']) for e in press_list
                if e.get('canRegretFund') and e.get('id')]

    # ── ETOP-4: match_pool ────────────────────────────────────────────────────

    async def match_pool(self, sub_id) -> Optional[list]:
        """GET /api/match/bc/{subMatchId}/list.do — bet pool."""
        url = f"{ETOP_BASE_URL}/api/match/bc/{sub_id}/list.do?lang=en"
        try:
            async with self.session.get(url, headers=self.headers) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    return None
                return data["datas"].get("list", [])
        except Exception as e:
            log_error('etopfun_api', f'match_pool failed: {e}')
            return None

    # ── ETOP-5: press ─────────────────────────────────────────────────────────

    async def press(self, sub_id, item_ids: list, vsid: int) -> Tuple[bool, str, Optional[int]]:
        """POST /match/570/v2/press.do — place bet.

        Returns (success, message, press_id).
        Sequential only — parallel = trygain.later error.
        """
        url = f"{ETOP_BASE_URL}/match/570/v2/press.do"
        form = FormData()
        form.add_field("id", str(sub_id))
        form.add_field("ids", ",".join(str(i) for i in item_ids))
        form.add_field("vsid", str(vsid))
        from core.logger import log_info
        log_info(f"[PRESS] mid={sub_id} vsid={vsid} items={len(item_ids)}")
        form.add_field("lang", "en")
        try:
            async with self.session.post(url, data=form, headers=self.headers) as resp:
                data = await resp.json()
                success = data.get("code") == 0
                msg = data.get("message", data.get("errors", ""))
                datas = data.get("datas")
                log_info(f"[PRESS_RAW] mid={sub_id} vsid={vsid} code={data.get('code')} msg={msg!r} datas={datas}")
                return success, msg, None
        except Exception as e:
            log_error('etopfun_api', f'press failed: {e}')
            return False, str(e), None

    # ── ETOP-6: changepress ───────────────────────────────────────────────────

    async def changepress(self, sub_id, vsid: int) -> Tuple[bool, str]:
        """POST /match/changepress.do — change bet side after placing."""
        url = f"{ETOP_BASE_URL}/match/changepress.do"
        form = FormData()
        form.add_field("id", str(sub_id))
        form.add_field("vsid", str(vsid))
        form.add_field("lang", "en")
        try:
            async with self.session.post(url, data=form, headers=self.headers) as resp:
                data = await resp.json()
                return data.get("code") == 0, data.get("message", "")
        except Exception as e:
            log_error('etopfun_api', f'changepress failed: {e}')
            return False, str(e)

    # ── ETOP-7: regret ────────────────────────────────────────────────────────

    async def regret(self, sub_id, press_id) -> Tuple[bool, str]:
        """POST /match/{subMatchId}/regretFund.do — cancel a placed bet."""
        url = f"{ETOP_BASE_URL}/match/{sub_id}/regretFund.do"
        data = {'pressid': str(press_id), 'lang': 'en'}
        try:
            async with self.session.post(url, headers=self.headers, data=data) as r:
                resp = await r.json()
                success = resp.get('code') == 0
                log_info(f"[CANCEL] regretFund pressid={press_id} → code={resp.get('code')} msg={resp.get('msg', 'ok')}")
                return success, str(resp.get('msg', 'ok'))
        except Exception as e:
            log_warn("cancel", f"regretFund failed: {e}")
            return False, str(e)

    # ── ETOP-8: bag_listbc ────────────────────────────────────────────────────

    async def bag_listbc(self, page: int = 1, rows: int = 120) -> Tuple[List[InventoryItem], int]:
        """GET /api/user/bag/570/listbc.do — bet-compatible items.

        Returns (items, total_count). Filters lock=0 AND state=0.
        """
        url = f"{ETOP_BASE_URL}/api/user/bag/570/listbc.do?page={page}&rows={rows}&desc=1&lang=en"
        try:
            async with self.session.get(url, headers=self.headers) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    return [], 0
                items = data["datas"].get("list", [])
                total = data["datas"].get("counts", 0)
                available = []
                for item in items:
                    lock = item.get("status", {}).get("lock", 1)
                    state = item.get("state", -1)
                    if lock == 0 and state == 0:
                        available.append(InventoryItem(
                            id=item["id"],
                            value=item.get("value", 0),
                            name=item.get("shortName", "?"),
                        ))
                return available, total
        except Exception as e:
            log_error('etopfun_api', f'bag_listbc failed: {e}')
            return [], 0

    # ── ETOP-10: fetch_tuhao ─────────────────────────────────────────────────

    async def fetch_tuhao(self, parent_id: str) -> list:
        """Fetch top magnates (bettors) for a match. Returns list of bettors with items."""
        url = f"{ETOP_BASE_URL}/api/match/{parent_id}/tuhao.do"
        params = {'lang': 'en'}
        try:
            async with self.session.get(url, params=params, headers=self.headers) as r:
                if r.status == 429:
                    log_warn("tuhao", f"[TUHAO] HTTP 429 for parent={parent_id} (using cached)")
                    return self._tuhao_last.get(str(parent_id), [])
                if r.status != 200:
                    log_warn("tuhao", f"[TUHAO] HTTP {r.status} for parent={parent_id}")
                    return self._tuhao_last.get(str(parent_id), [])
                data = await r.json()
                bettors = data.get('datas', {}).get('list', [])
                log_info(f"[TUHAO] parent={parent_id}: {len(bettors)} bettors returned")
                # Cache successful result
                if bettors:
                    self._tuhao_last[str(parent_id)] = bettors
                return bettors
        except Exception as e:
            log_warn("tuhao", f"[TUHAO] FAIL parent={parent_id}: {e}")
            return self._tuhao_last.get(str(parent_id), [])

    # ── ETOP-11: fetch_exchange_db ────────────────────────────────────────────

    async def fetch_exchange_db(self, appid: int = 570) -> dict:
        """Fetch item gold values from exchange. Returns {shortName: gold_value}."""
        url = f"{ETOP_BASE_URL}/api/ingotitems/v2/list.do"
        params = {'appid': appid, 'page': 1, 'rows': 60, 'lang': 'en'}
        try:
            async with self.session.get(url, params=params, headers=self.headers) as r:
                if r.status != 200:
                    log_warn("exchange", f"[EXCHANGE] HTTP {r.status} for appid={appid}")
                    return {}
                data = await r.json()
                items = data.get('datas', {}).get('list', [])
                db = {}
                for item in items:
                    name = item.get('shortName', '')
                    value = item.get('ingot') or item.get('value') or 0
                    if name and value:
                        db[name] = float(value)
                log_info(f"[EXCHANGE] Loaded {len(db)} items for appid={appid}")
                return db
        except Exception as e:
            log_warn("exchange", f"[EXCHANGE] FAIL appid={appid}: {e}")
            return {}

    # ── ETOP-9: userconn_check ────────────────────────────────────────────────

    async def userconn_check(self) -> bool:
        """POST /api/userconn/check.do — session keepalive. Returns True if alive."""
        if not self._uuid:
            return False
        url = f"{ETOP_BASE_URL}/api/userconn/check.do"
        try:
            form = FormData()
            form.add_field('uuid', self._uuid)
            form.add_field('lang', 'en')
            async with self.session.post(url, data=form, headers=self.headers) as r:
                body = await r.json(content_type=None)
                return body.get('code') == 0
        except Exception:
            return False
