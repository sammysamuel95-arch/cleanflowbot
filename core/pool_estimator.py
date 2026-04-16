"""
pool_estimator.py — Estimate minimum pool size from tuhao.do top bettors.

No cache — fire_engine calls inline per fire, always wants fresh data.
"""
from core.logger import log_info, log_warn


class PoolEstimator:
    def __init__(self):
        self._exchange_db = {}
        self._loaded = False
        self._unknown_items = set()

    async def load_exchange_db(self, etop_api):
        for appid in [570, 730]:
            db = await etop_api.fetch_exchange_db(appid)
            self._exchange_db.update(db)
        if self._exchange_db:
            self._loaded = True
            log_info(f"[POOL_EST] Exchange DB: {len(self._exchange_db)} items loaded")
            by_value = sorted(self._exchange_db.items(), key=lambda x: -x[1])
            for name, val in by_value[:5]:
                log_info(f"[POOL_EST]   {name} = Gold {val}")
            log_info(f"[POOL_EST]   ... lowest: {by_value[-1][0]} = Gold {by_value[-1][1]}")
        else:
            log_warn("pool_est", "[POOL_EST] Exchange DB EMPTY — pool estimation disabled")

    def _item_value(self, item: dict) -> float:
        name = item.get('shortName', '')
        if not name:
            return 0
        if name in self._exchange_db:
            return self._exchange_db[name]
        if name not in self._unknown_items:
            self._unknown_items.add(name)
            log_warn("pool_est", f"[POOL_EST] Unknown item: '{name}' — not in exchange DB")
        return 0

    async def estimate_pool(self, etop_api, mid: str) -> float:
        """Fetch tuhao and return total pool in gold. No cache."""
        if not self._loaded:
            return 0
        bettors = await etop_api.fetch_tuhao(mid)
        if not bettors:
            return 0
        total_gold = sum(
            sum(self._item_value(it) for it in bettor.get('items', []))
            for bettor in bettors
        )
        log_info(f"[TUHAO] mid={mid} pool={total_gold:.0f}g bettors={len(bettors)}")
        return total_gold

    def calc_value_cap(self, min_pool: float, max_pool_impact: float, hard_cap: float) -> float:
        if min_pool <= 0:
            return hard_cap
        return min(min_pool * max_pool_impact, hard_cap)
