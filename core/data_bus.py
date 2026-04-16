"""
Reactive data bus — central event coordination.
Any data source calls notify() after writing new data.
The pipeline loop calls wait() to wake on any source change.
asyncio single-thread: between two awaits, all reads are atomic.
"""
import asyncio
import time
from core.logger import log_info


class DataBus:
    def __init__(self):
        self._event = asyncio.Event()
        self._versions = {}       # source_id → monotonic timestamp
        self._dirty = set()       # sources that notified since last wait()
        self._notify_count = 0

    def notify(self, source_id: str):
        """Called by any source after writing data. Non-blocking."""
        self._versions[source_id] = time.monotonic()
        self._dirty.add(source_id)
        self._notify_count += 1
        self._event.set()

    async def wait(self, timeout: float = 5.0):
        """Wait for any source to notify. Returns set of dirty source IDs."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self._event.clear()
        triggered = frozenset(self._dirty)
        self._dirty.clear()
        return triggered

    @property
    def notify_count(self):
        return self._notify_count

    def freshness(self):
        """Returns {source_id: seconds_since_last_notify}."""
        now = time.monotonic()
        return {k: round(now - v, 3) for k, v in self._versions.items()}
