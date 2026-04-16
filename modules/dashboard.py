"""
modules/dashboard.py — Write dash_state.json from Container.

READS:  container (via to_dash_state — ONE call, ONE dict)
WRITES: data/dash_state.json (non-blocking via background thread)

File is a BACKUP. SSE is the primary path to panel.
File exists for: diagnostic tool, panel restart, debugging.
"""

import os
import json
import threading

_dash_lock = threading.Lock()
_dash_thread = None


def run(container, data_dir='data'):
    """Write dashboard state. Delegates to run_with_data."""
    dash = container.to_dash_state()
    run_with_data(dash, data_dir)


def run_with_data(dash, data_dir='data'):
    """Write pre-built dashboard data to disk on background thread.

    Non-blocking: serializes JSON on calling thread (fast, ~5ms CPU),
    hands file write to a background thread. If previous write still
    running, skips this one (don't queue up stale writes).
    """
    global _dash_thread

    # Serialize on calling thread (CPU-bound, fast, no I/O)
    try:
        payload = json.dumps(dash)
    except Exception:
        return

    # If previous write still running, skip (don't block or queue)
    if _dash_thread is not None and _dash_thread.is_alive():
        return

    def _write():
        path = os.path.join(data_dir, 'dash_state.json')
        tmp = path + '.tmp'
        try:
            with _dash_lock:
                with open(tmp, 'w') as f:
                    f.write(payload)
                os.replace(tmp, path)
        except Exception:
            pass

    _dash_thread = threading.Thread(target=_write, daemon=True)
    _dash_thread.start()
