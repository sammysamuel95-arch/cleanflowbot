"""
commands.py — Runtime control interface for CleanFlowBot.

CommandHandler takes a command string (from panel or CLI) and
executes it against the live bot state. All commands return a
dict with 'ok' bool and 'msg' string (+ optional 'data' dict).

Commands:
  status                    — bot health snapshot
  list_tracked              — all tracked markets with state/EV
  list_slices               — inventory slice assignments
  release_all               — free all inventory slices
  release <fire_key>        — free one slice
  kill <fire_key>           — force-kill a market (mark DONE)
  refresh_event <fire_key>  — force REST refresh for one market
  refresh_all               — force REST refresh for all markets
  dump_store [eid]          — dump PS price store (optional filter)
  test_search <t1> vs <t2> [hint] — test PS team search
  test_ev <fire_key>        — compute EV for a market right now
  reload_config             — reload config.py values at runtime
  help                      — list all commands
"""

import time
import traceback
from core.logger import log_info, log_warn


# In-memory command log (last 100 entries)
_command_log = []
MAX_LOG = 100


def _log_cmd(cmd: str, result: dict):
    _command_log.append({
        'ts': time.strftime('%H:%M:%S'),
        'cmd': cmd,
        'ok': result.get('ok', False),
        'msg': result.get('msg', ''),
    })
    if len(_command_log) > MAX_LOG:
        _command_log.pop(0)


def get_command_log():
    """Return recent command history (newest last)."""
    return list(_command_log)


class CommandHandler:
    """Execute runtime commands against live bot state.

    Usage:
        handler = CommandHandler(bot)
        result = await handler.execute("status")
        result = await handler.execute("release home_team:away_team:market")
    """

    def __init__(self, bot):
        """
        bot: the Bot instance from main.py.
        Expected attributes:
          bot.markets         — dict[fire_key, TrackedMarket]
          bot.inventory       — InventoryManager
          bot.fire_zone       — FireZone
          bot.ps_store        — dict (PS price store)
          bot.ps_auth         — PS3838Auth
          bot.etop_api        — EtopfunAPI
        """
        self.bot = bot

    async def execute(self, raw: str) -> dict:
        """Parse and execute a command string."""
        raw = raw.strip()
        if not raw:
            return {'ok': False, 'msg': 'Empty command'}

        parts = raw.split()
        cmd = parts[0].lower()
        args = parts[1:]

        handlers = {
            'status': self._status,
            'list_tracked': self._list_tracked,
            'list_slices': self._list_slices,
            'release_all': self._release_all,
            'release': self._release_one,
            'kill': self._kill_market,
            'refresh_event': self._refresh_event,
            'refresh_all': self._refresh_all,
            'dump_store': self._dump_store,
            'test_search': self._test_search,
            'test_ev': self._test_ev,
            'reload_config': self._reload_config,
            'resub': self._resub,
            'aos': self._aos_check,
            'uptime': self._uptime,
            'check_etop': self._check_etop,
            'check_ps': self._check_ps,
            'help': self._help,
        }

        fn = handlers.get(cmd)
        if fn is None:
            result = {'ok': False, 'msg': f'Unknown command: {cmd}. Try: help'}
        else:
            try:
                result = await fn(args)
            except Exception as e:
                result = {'ok': False, 'msg': f'Command error: {e}', 'tb': traceback.format_exc()}

        _log_cmd(raw, result)
        log_info(f"[CMD] {raw!r} → ok={result.get('ok')} {result.get('msg', '')[:80]}")
        return result

    # ── Commands ──────────────────────────────────────────────────────────────

    async def _status(self, args) -> dict:
        """Bot health snapshot."""
        bot = self.bot
        markets = getattr(bot, 'markets', {})
        inventory = getattr(bot, 'inventory', None)
        ps_store = getattr(bot, 'ps_store', {})
        fire_zone = getattr(bot, 'fire_zone', None)

        by_state = {}
        for tm in markets.values():
            s = getattr(tm, 'state', 'UNKNOWN')
            by_state[s] = by_state.get(s, 0) + 1

        data = {
            'tracked': len(markets),
            'by_state': by_state,
            'pool_size': inventory.pool_size if inventory else -1,
            'pool_loaded': inventory.pool_loaded if inventory else False,
            'store_keys': len(ps_store),
            'fire_states': len(fire_zone._fire_state) if fire_zone else 0,
            'uptime_s': int(time.time() - getattr(bot, '_start_time', time.time())),
        }
        lines = [
            f"tracked={data['tracked']}  states={data['by_state']}",
            f"pool={data['pool_size']} loaded={data['pool_loaded']}",
            f"store_keys={data['store_keys']}  fire_states={data['fire_states']}",
            f"uptime={data['uptime_s']}s",
        ]
        return {'ok': True, 'msg': '\n'.join(lines), 'data': data}

    async def _list_tracked(self, args) -> dict:
        """List all tracked markets with state, remain, EV."""
        markets = getattr(self.bot, 'markets', {})
        if not markets:
            return {'ok': True, 'msg': 'No tracked markets.', 'data': []}

        rows = []
        now = time.time()
        for fk, tm in sorted(markets.items()):
            em = tm.etop_market
            remain = getattr(em, 'remain', 0)
            ev = getattr(em, 'ev', None)
            ev_str = f'{ev:+.1%}' if ev is not None else 'n/a'
            rows.append({
                'fire_key': fk,
                'state': tm.state,
                'remain': int(remain),
                'ev': ev_str,
                'ps_eid': tm.ps_event_id,
                'state': tm.state,
            })

        lines = [f"{r['state']:10} remain={r['remain']:5}s  ev={r['ev']:8}  {r['fire_key']}"
                 for r in rows]
        return {'ok': True, 'msg': '\n'.join(lines), 'data': rows}

    async def _list_slices(self, args) -> dict:
        """Show inventory pool status."""
        inv = getattr(self.bot, 'inventory', None)
        if not inv:
            return {'ok': False, 'msg': 'No inventory manager.'}
        summary = (f"pool={inv.pool_size} free={inv.pool_free_count()} "
                   f"consumed={len(inv._consumed)} "
                   f"free_value=Gold {inv.pool_free_value():.1f}")
        return {'ok': True, 'summary': summary, 'lines': [summary]}

    async def _release_all(self, args) -> dict:
        """Clear all consumed items (reset pool)."""
        inv = getattr(self.bot, 'inventory', None)
        if not inv:
            return {'ok': False, 'msg': 'No inventory manager.'}
        count = len(inv._consumed)
        inv._consumed.clear()
        return {'ok': True, 'msg': f'Cleared {count} consumed items. Pool fully free.'}

    async def _release_one(self, args) -> dict:
        """Force pool refresh."""
        inv = getattr(self.bot, 'inventory', None)
        if not inv:
            return {'ok': False, 'msg': 'No inventory manager.'}
        return {'ok': True, 'msg': 'Use release_all to clear consumed, or wait for auto-refresh.'}

    async def _kill_market(self, args) -> dict:
        """Force-kill a market (set state=DONE, release slice)."""
        if not args:
            return {'ok': False, 'msg': 'Usage: kill <fire_key>'}
        fk = ' '.join(args)
        markets = getattr(self.bot, 'markets', {})
        if fk not in markets:
            return {'ok': False, 'msg': f'Market not found: {fk}'}
        markets[fk].state = 'DONE'
        inv = getattr(self.bot, 'inventory', None)
        fz = getattr(self.bot, 'fire_zone', None)
        if inv and fz:
            fs = fz._fire_state.get(fk)
            if fs and fs.consumed_ids:
                inv.unconsume(fs.consumed_ids)
        if fz:
            fz.cleanup(fk)
        return {'ok': True, 'msg': f'Killed market: {fk}'}

    async def _refresh_event(self, args) -> dict:
        """Force REST refresh for one market."""
        if not args:
            return {'ok': False, 'msg': 'Usage: refresh_event <fire_key>'}
        fk = ' '.join(args)
        markets = getattr(self.bot, 'markets', {})
        if fk not in markets:
            return {'ok': False, 'msg': f'Market not found: {fk}'}
        tm = markets[fk]
        if not tm.ps_event_id:
            return {'ok': False, 'msg': f'No PS event ID for: {fk}'}
        from feeds.ps3838_rest import fetch_lines_for_eid
        ps_auth = getattr(self.bot, 'ps_auth', None)
        if ps_auth is None:
            return {'ok': False, 'msg': 'No ps_auth on bot.'}
        info = {'sp': getattr(tm.etop_market, 'sp', 29),
                'home': getattr(tm.etop_market, 'team1', ''),
                'away': getattr(tm.etop_market, 'team2', ''),
                'league': getattr(tm.etop_market, 'league', '')}
        lines = await fetch_lines_for_eid(ps_auth, tm.ps_event_id, info.get("sp", 29))
        # Merge into ps_store
        ps_store = getattr(self.bot, 'ps_store', None)
        if ps_store is not None and lines:
            for mkt in lines:
                key = mkt.get('key')
                if key:
                    ps_store[key] = mkt
        return {'ok': True, 'msg': f'Refreshed {len(lines)} lines for: {fk}'}

    async def _refresh_all(self, args) -> dict:
        """Force REST refresh for all tracked markets."""
        markets = getattr(self.bot, 'markets', {})
        results = []
        for fk in list(markets.keys()):
            r = await self._refresh_event([fk])
            results.append(f"{fk}: {'OK' if r['ok'] else 'FAIL'} — {r['msg']}")
        return {'ok': True, 'msg': '\n'.join(results) if results else 'No markets.'}

    async def _dump_store(self, args) -> dict:
        """Dump PS price store. Optional: dump_store <eid>"""
        ps_store = getattr(self.bot, 'ps_store', {})
        if not ps_store:
            return {'ok': True, 'msg': 'Store is empty.', 'data': {}}
        eid_filter = args[0] if args else None
        entries = {}
        for k, v in ps_store.items():
            if eid_filter and str(eid_filter) not in str(k):
                continue
            entries[k] = v
        lines = [f"{k}: {v}" for k, v in list(entries.items())[:50]]
        summary = f"{len(entries)} entries" + (f" (filtered by '{eid_filter}')" if eid_filter else "")
        return {'ok': True, 'msg': summary + '\n' + '\n'.join(lines), 'data': entries}

    async def _test_search(self, args) -> dict:
        """Test PS team search. Usage: test_search Team1 vs Team2 [hint]"""
        # Parse: "Team1 vs Team2 [hint]"
        raw = ' '.join(args)
        hint = None
        for h in ('esports', 'basketball', 'soccer'):
            if raw.lower().endswith(h):
                hint = h
                raw = raw[:-(len(h))].strip()
                break
        if ' vs ' not in raw.lower():
            return {'ok': False, 'msg': 'Usage: test_search Team1 vs Team2 [esports|basketball|soccer]'}
        idx = raw.lower().index(' vs ')
        team1 = raw[:idx].strip()
        team2 = raw[idx+4:].strip()
        from feeds.ps3838_rest import search_by_teams
        ps_auth = getattr(self.bot, 'ps_auth', None)
        if ps_auth is None:
            return {'ok': False, 'msg': 'No ps_auth on bot.'}
        result = await search_by_teams(ps_auth, team1, team2, hint)
        if result is None:
            return {'ok': True, 'msg': f'Not found: {team1} vs {team2} (hint={hint})', 'data': None}
        return {'ok': True,
                'msg': (f"Found: {result['home']} vs {result['away']}  "
                        f"eid={result['eid']}  score={result['score']}  "
                        f"lines={len(result.get('lines', []))}"),
                'data': result}

    async def _test_ev(self, args) -> dict:
        """Compute current EV for a market. Usage: test_ev <fire_key>"""
        if not args:
            return {'ok': False, 'msg': 'Usage: test_ev <fire_key>'}
        fk = ' '.join(args)
        markets = getattr(self.bot, 'markets', {})
        if fk not in markets:
            return {'ok': False, 'msg': f'Market not found: {fk}'}
        tm = markets[fk]
        em = tm.etop_market
        ps_store = getattr(self.bot, 'ps_store', {})
        from core.ev import compute_ev
        try:
            ev_result = compute_ev(em, ps_store)
        except Exception as e:
            return {'ok': False, 'msg': f'EV compute error: {e}'}
        if ev_result is None:
            return {'ok': True, 'msg': f'EV=None (no matching PS line) for: {fk}', 'data': None}
        return {'ok': True, 'msg': f'EV={ev_result:+.3%} for: {fk}', 'data': {'ev': ev_result}}

    async def _reload_config(self, args) -> dict:
        """Reload all config from bot_config.json."""
        import config
        success = config.reload()

        if success:
            cfg = {
                'MIN_EV': config.MIN_EV,
                'TRIGGER_SECS': config.TRIGGER_SECS,
                'PREFIRE_SECS': config.PREFIRE_SECS,
                'MAX_ITEMS': config.MAX_ITEMS,
                'AOS_CACHE_SECS': config.AOS_CACHE_SECS,
                'MAX_PS_AGE': config.MAX_PS_AGE,
            }
            strategy = getattr(self.bot, 'strategy', None)
            if strategy:
                strategy.reload(cfg)

            inventory = getattr(self.bot, 'inventory', None)
            if inventory and hasattr(inventory, 'reload_config'):
                inventory.reload_config()

        msg = (f'Reloaded from bot_config.json: MIN_EV={config.MIN_EV} '
               f'TRIGGER={config.TRIGGER_SECS}s MAX_ITEMS={config.MAX_ITEMS} '
               f'DRY_RUN={config.DRY_RUN}') if success else 'Reload FAILED'
        return {'ok': success, 'msg': msg}

    async def _resub(self, args) -> dict:
        """Resub WS for a sport. Usage: resub esports|soccer|basketball"""
        if not args:
            return {'ok': False, 'msg': 'Usage: resub esports|soccer|basketball'}
        sport = args[0].lower()
        sp_map = {'esports': 12, 'soccer': 29, 'basketball': 4}
        sp = sp_map.get(sport)
        if sp is None:
            return {'ok': False, 'msg': f'Unknown sport: {sport}. Use: esports, soccer, basketball'}

        live_feed = getattr(self.bot, 'live_feed', None)
        if live_feed is None:
            return {'ok': False, 'msg': 'No live_feed on bot.'}

        mk = 3 if sp == 12 else 1
        try:
            result = await live_feed.resub_verify(sp, mk)
            if result is None:
                return {'ok': False, 'msg': f'Resub {sport} failed — WS not connected or timeout'}
            return {'ok': True,
                    'msg': f'Resub {sport} (sp={sp} mk={mk}) → {len(result)} lines refreshed'}
        except Exception as e:
            return {'ok': False, 'msg': f'Resub error: {e}'}

    async def _aos_check(self, args) -> dict:
        """Check if a PS line is alive via all-odds-selections. Usage: aos <fire_key>"""
        if not args:
            return {'ok': False, 'msg': 'Usage: aos <fire_key>'}
        fk = ' '.join(args)
        markets = getattr(self.bot, 'markets', {})
        if fk not in markets:
            return {'ok': False, 'msg': f'Market not found: {fk}'}
        tm = markets[fk]
        em = tm.etop_market

        live_feed = getattr(self.bot, 'live_feed', None)
        ps_auth = getattr(self.bot, 'ps_auth', None)
        if not live_feed or not ps_auth:
            return {'ok': False, 'msg': 'Missing live_feed or ps_auth'}

        aos_mk = 1 if em.market == 'ml' else 2
        aos_line = 0 if em.market == 'ml' else em.line
        line_id = live_feed.standard_store.get_line_id(
            em.ps_event_id, em.map_num, em.market, aos_line)

        if line_id is None:
            return {'ok': False, 'msg': f'No line_id for {em.market} line={aos_line}'}

        from feeds.ps3838_rest import all_odds_selections
        sel = [{
            'eid': em.ps_event_id,
            'period': em.map_num,
            'mk': aos_mk,
            'side': 0,
            'line': aos_line,
            'line_id': line_id,
        }]
        try:
            resp = await all_odds_selections(ps_auth, sel)
            if resp is None:
                return {'ok': False, 'msg': 'AOS call failed (HTTP error)'}
            if isinstance(resp, dict):
                sel_results = resp.get('oddsSelections', [])
                if sel_results:
                    s = sel_results[0]
                    status = s.get('status', '?')
                    odds = s.get('odds', '?')
                    max_stake = s.get('maxStake', '?')
                    return {'ok': True,
                            'msg': f'{status} — odds={odds} maxStake={max_stake}'}
            return {'ok': False, 'msg': f'Unexpected response: {str(resp)[:200]}'}
        except Exception as e:
            return {'ok': False, 'msg': f'AOS error: {e}'}

    async def _check_etop(self, args) -> dict:
        """Call list.do right now, show raw remain/odds for all markets under 10 min.
        Usage: check_etop [all]"""
        show_all = args and args[0].lower() == 'all'
        etop_api = getattr(self.bot, 'etop_api', None)
        if not etop_api:
            return {'ok': False, 'msg': 'No etop_api'}

        try:
            parents, lookup = await etop_api.match_list()
        except Exception as e:
            return {'ok': False, 'msg': f'match_list failed: {e}'}

        lines = []
        for mid, sub in sorted(lookup.items(), key=lambda x: x[1]['remain']):
            remain = sub['remain']
            if not show_all and remain > 600:
                continue
            o1 = sub['o1']
            o2 = sub['o2']
            cp = sub['can_press']
            lines.append(f"mid={mid}  remain={int(remain)}s  odds={o1:.2f}/{o2:.2f}  canPress={cp}")

        summary = f"match_list: {len(parents)} parents, {len(lookup)} subs"
        if not lines:
            lines = ['No markets under 10 min (use: check_etop all)']
        return {'ok': True, 'msg': summary + '\n' + '\n'.join(lines[:30])}

    async def _check_ps(self, args) -> dict:
        """Show PS store data + age for tracked markets.
        Usage: check_ps [fire_key_substring]"""
        filt = args[0].lower() if args else None
        markets = getattr(self.bot, 'markets', {})
        live_feed = getattr(self.bot, 'live_feed', None)
        if not live_feed:
            return {'ok': False, 'msg': 'No live_feed'}

        store = live_feed.standard_store
        lines = []
        for fk, tm in sorted(markets.items(), key=lambda x: x[1].etop_market.remain):
            if filt and filt not in fk.lower():
                continue
            em = tm.etop_market
            ps_age = store.get_line_age(em.ps_event_id, em.map_num, em.market)
            line_id = store.get_line_id(em.ps_event_id, em.map_num, em.market,
                                        0 if em.market == 'ml' else em.line)

            if em.market == 'ml' and em.ps_name_team1:
                f1 = store.get_ml_fair(em.ps_event_id, em.map_num, em.ps_name_team1)
                f2 = store.get_ml_fair(em.ps_event_id, em.map_num, em.ps_name_team2)
                fair_str = f"{f1:.3f}/{f2:.3f}" if f1 and f2 else "MISSING"
            elif em.market == 'hdp':
                fair_str = "hdp"
            elif em.market in ('ou', 'team_total'):
                fo = store.get_ou_fair(em.ps_event_id, em.map_num, 'over', em.line)
                fu = store.get_ou_fair(em.ps_event_id, em.map_num, 'under', em.line)
                fair_str = f"{fo:.3f}/{fu:.3f}" if fo and fu else "MISSING"
            else:
                fair_str = "?"

            age_str = f"{int(ps_age)}s" if ps_age is not None else "NONE"
            lid_str = str(line_id) if line_id else "NONE"

            lines.append(
                f"remain={int(em.remain):>5}s  ps_age={age_str:>6}  "
                f"fair={fair_str:>15}  line_id={lid_str:>12}  "
                f"eid={em.ps_event_id}  {fk[:60]}")

        ws_status = "CONNECTED" if live_feed.is_ws_connected() else "DISCONNECTED"
        summary = f"WS: {ws_status}  Store: {store.size} lines  Events: {len(store._events)}"
        if not lines:
            lines = ['No tracked markets' + (f' matching "{filt}"' if filt else '')]
        return {'ok': True, 'msg': summary + '\n' + '\n'.join(lines[:40])}

    async def _uptime(self, args) -> dict:
        """Session uptime diagnostics."""
        tracker = getattr(self.bot, 'session_tracker', None)
        if not tracker:
            return {'ok': False, 'msg': 'Session tracker not available'}
        return {'ok': True, 'msg': tracker.summary_str()}

    async def _help(self, args) -> dict:
        """List all commands."""
        cmds = [
            'status                           — bot health snapshot',
            'list_tracked                     — all markets: state/remain/EV',
            'list_slices                      — inventory pool status',
            'release_all                      — free all inventory slices',
            'release <fire_key>               — free one slice',
            'kill <fire_key>                  — force-kill a market',
            'refresh_event <fire_key>         — REST refresh for one market',
            'refresh_all                      — REST refresh for all markets',
            'dump_store [eid]                 — dump PS price store',
            'test_search T1 vs T2 [hint]      — test PS team search',
            'test_ev <fire_key>               — compute EV right now',
            'reload_config                    — reload config.py at runtime',
            'check_etop [all]                 — raw list.do data (markets < 10 min)',
            'check_ps [filter]                — PS store age/fair/line_id for tracked',
            'resub esports|soccer|basketball  — refresh WS for a sport (like F5)',
            'aos <fire_key>                   — check if PS line alive via AOS',
            'help                             — this list',
        ]
        return {'ok': True, 'msg': '\n'.join(cmds)}
