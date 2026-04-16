"""
engine/inventory.py — Global pool inventory. No slices, no reservations.

Each market picks from the global free pool at fire time.
Consumed items are tracked globally. Periodic refresh syncs with backpack.
value_cap on FireState controls per-market gold limits.
MAX_ITEMS=40 is etopfun's hard cap per market.
"""

from core.logger import log_info, log_warn
from config import MAX_ITEMS


class InventoryManager:
    """Global pool inventory. ID-based. Collision-proof via sequential firing."""

    def __init__(self):
        self._pool: list = []           # [{id, value, name}, ...]
        self._consumed: set = set()     # item IDs fired (gone from pool)
        self.pool_loaded: bool = False
        self._last_pool_load: float = 0

    # ── Pool info ────────────────────────────────────────────────

    @property
    def pool_size(self) -> int:
        return len(self._pool)

    def pool_free_count(self) -> int:
        """Items available to fire right now."""
        return len([it for it in self._pool if it['id'] not in self._consumed])

    def pool_free_value(self) -> float:
        """Total gold value of free items."""
        return sum(float(it.get('value', 0) or 0)
                   for it in self._pool if it['id'] not in self._consumed)

    # ── Refresh ──────────────────────────────────────────────────

    def needs_refresh(self, interval: float = 300) -> bool:
        """True if pool should be reloaded (stale or never loaded)."""
        if not self.pool_loaded:
            return True
        import time
        return (time.time() - self._last_pool_load) > interval

    # ── Pool loading ─────────────────────────────────────────────

    async def load_pool(self, api, force=False) -> int:
        """Load ALL backpack items from bag_listbc. Fail fast."""
        if self.pool_loaded and not force:
            return len(self._pool)

        all_items = []
        page = 1

        while True:
            try:
                result = await api.bag_listbc(page=page, rows=2000)
            except Exception as e:
                log_warn("POOL", f"FAIL page={page}: {e}")
                if not all_items:
                    return len(self._pool)
                break

            if isinstance(result, tuple):
                raw_items, total = result
                items = [{'id': it.id, 'value': getattr(it, 'value', 0),
                          'name': getattr(it, 'name', '?')} for it in raw_items]
            elif isinstance(result, list):
                items = result
            elif isinstance(result, dict):
                items = result.get('datas', [])
            else:
                items = []

            if not items:
                break

            all_items.extend(items)
            if len(items) < 2000:
                break
            page += 1

        valid = [it for it in all_items if isinstance(it, dict) and 'id' in it]
        skipped = len(all_items) - len(valid)

        if not valid:
            log_warn("POOL", f"0 valid items — keeping previous pool ({len(self._pool)})")
            return len(self._pool)

        if skipped:
            log_warn("POOL", f"{skipped} items skipped — missing 'id'")

        old_count = len(self._pool)
        self._pool = valid
        self._pool.sort(key=lambda it: float(it.get('value', 0) or 0), reverse=True)
        self.pool_loaded = True
        import time
        self._last_pool_load = time.time()

        # Prune consumed: remove IDs no longer in pool (bet items gone from backpack)
        current_ids = {it['id'] for it in self._pool}
        before_consumed = len(self._consumed)
        self._consumed = self._consumed & current_ids
        pruned = before_consumed - len(self._consumed)
        if pruned > 0:
            log_info(f"[POOL] Pruned {pruned} consumed IDs (items gone from backpack)")

        total_value = sum(float(it.get('value', 0) or 0) for it in self._pool)
        free = self.pool_free_count()
        log_info(f"[POOL] {len(self._pool)} items loaded (Gold {total_value:.1f}) "
                 f"{page} pages (was {old_count}) free={free} consumed={len(self._consumed)}")

        if old_count == 0 and self._pool:
            sample = self._pool[0]
            log_info(f"[POOL] Sample keys: {sorted(sample.keys())}")
            log_info(f"[POOL] Sample: id={sample.get('id')} value={sample.get('value')}")

        return len(self._pool)

    # ── Fire operations ──────────────────────────────────────────

    def get_next_item(self, remaining_cap: float = 0) -> int:
        """Pick best item that fits within remaining gold cap.
        Pool is pre-sorted by value DESC at load time.
        Returns item ID or None if nothing available.
        """
        # Find largest item that fits under remaining cap
        if remaining_cap > 0:
            for it in self._pool:
                if it['id'] not in self._consumed:
                    val = float(it.get('value', 0) or 0)
                    if val <= remaining_cap:
                        return it['id']
            # Nothing fits under cap — return None, let VALUE_CAPPED stop it
            return None

        # No cap constraint (value_cap=0) — just take highest value free item
        for it in self._pool:
            if it['id'] not in self._consumed:
                return it['id']
        return None

    def has_free_items(self) -> bool:
        """Quick check: any unconsumed items in pool?"""
        for it in self._pool:
            if it['id'] not in self._consumed:
                return True
        return False

    def get_item_value(self, item_id) -> float:
        """Gold value of a specific item."""
        for it in self._pool:
            if it['id'] == item_id:
                return float(it.get('value', 0) or 0)
        return 0

    def consume(self, item_ids: list):
        """Mark items as fired. They're gone from the pool."""
        for iid in item_ids:
            self._consumed.add(iid)
        log_info(f"[POOL] Consumed {len(item_ids)} (total consumed: {len(self._consumed)} "
                 f"free: {self.pool_free_count()})")

    def unconsume(self, item_ids: list):
        """Free items back to pool after successful cancel."""
        for iid in item_ids:
            self._consumed.discard(iid)
        if item_ids:
            log_info(f"[POOL] Freed {len(item_ids)} items after cancel "
                     f"(free: {self.pool_free_count()})")

    def reload_config(self):
        """Update MAX_ITEMS from config module."""
        global MAX_ITEMS
        from config import MAX_ITEMS as new_max
        MAX_ITEMS = new_max
        log_info(f"[POOL] Config reloaded: MAX_ITEMS={MAX_ITEMS}")
