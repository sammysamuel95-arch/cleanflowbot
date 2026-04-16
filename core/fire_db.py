"""
core/fire_db.py — SQLite fire log database.

Records every press.do call with precision timestamps, staleness ages,
and odds at fire time. Outcomes recorded separately for performance analysis.

Tables:
  fires    — one row per press.do call
  outcomes — one row per fire_key (updated when market closes)

Usage:
    db = FireDB()
    db.log_fire(session_id, fire_key, ...)
    db.log_outcome(fire_key, ...)
    db.close()
"""

import json
import os
import sqlite3
import time
import uuid


DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'fires.db')


class FireDB:
    def __init__(self, db_path: str = None):
        self._path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._create_tables()

    def _create_tables(self):
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS fires (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT NOT NULL,

            fire_at             REAL NOT NULL,
            etop_captured_at    REAL,
            ps_raw_captured_at  REAL,
            ps_fair_captured_at REAL,
            ev_calculated_at    REAL,

            fire_key            TEXT NOT NULL,
            team1               TEXT,
            team2               TEXT,
            market              TEXT,
            map_num             INTEGER,

            side                INTEGER,
            vsid                INTEGER,
            remain_secs         REAL,
            ev_at_fire          REAL,
            priority_score      REAL,

            etop_o1             REAL,
            etop_o2             REAL,
            ps_raw_t1           REAL,
            ps_raw_t2           REAL,
            ps_fair_t1          REAL,
            ps_fair_t2          REAL,

            ps_age              REAL,
            aos_age             REAL,
            etop_age            REAL,

            items_count         INTEGER,
            item_ids            TEXT,
            item_value          REAL,

            press_result        TEXT,
            press_error         TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            session_id    TEXT PRIMARY KEY,
            started_at    REAL NOT NULL,
            ended_at      REAL,
            total_fires   INTEGER DEFAULT 0,
            total_items   INTEGER DEFAULT 0,
            total_value   REAL DEFAULT 0,
            config_snapshot TEXT
        );

        CREATE TABLE IF NOT EXISTS fire_state (
            fire_key      TEXT PRIMARY KEY,
            total_fired   INTEGER DEFAULT 0,
            locked_side   INTEGER DEFAULT 0,
            first_fire_at REAL,
            last_fire_at  REAL,
            cancelled     INTEGER DEFAULT 0,
            cancelled_count INTEGER DEFAULT 0,
            session_id    TEXT,
            total_value   REAL DEFAULT 0,
            value_cap     REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS cancels (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    TEXT,
            timestamp     REAL NOT NULL,
            fire_key      TEXT NOT NULL,
            reason        TEXT,
            attempted     INTEGER,
            succeeded     INTEGER,
            item_ids_freed TEXT,
            remain_at_cancel REAL
        );

        CREATE TABLE IF NOT EXISTS outcomes (
            fire_key            TEXT PRIMARY KEY,

            closing_etop_o1     REAL,
            closing_etop_o2     REAL,
            closing_ps_raw_t1   REAL,
            closing_ps_raw_t2   REAL,
            closing_ps_fair_t1  REAL,
            closing_ps_fair_t2  REAL,
            closing_ev_t1       REAL,
            closing_ev_t2       REAL,

            won                 INTEGER,
            pnl_items           INTEGER,
            pnl_value           REAL,

            closed_at           REAL,
            recorded_at         REAL
        );
        """)
        self._conn.commit()
        # Add new columns if they don't exist (migration)
        for col, typedef in [('total_value', 'REAL DEFAULT 0'), ('value_cap', 'REAL DEFAULT 0')]:
            try:
                self._conn.execute(f"ALTER TABLE fire_state ADD COLUMN {col} {typedef}")
                self._conn.commit()
            except Exception:
                pass  # column already exists

    def log_fire(self, session_id: str, fire_key: str,
                 team1: str, team2: str, market: str, map_num: int,
                 side: int, vsid: int, remain_secs: float,
                 etop_o1: float, etop_o2: float, etop_captured_at: float,
                 ps_raw_t1: float, ps_raw_t2: float, ps_raw_captured_at: float,
                 ps_fair_t1: float, ps_fair_t2: float, ps_fair_captured_at: float,
                 ev_at_fire: float, ev_calculated_at: float,
                 priority_score: float,
                 items_count: int, item_ids: list, item_value: float,
                 ps_age: float, aos_age: float, etop_age: float,
                 press_result: str, press_error: str = None):
        """Log every press.do call with precision timestamps and staleness."""
        fire_at = time.time()
        try:
            self._conn.execute(
                """INSERT INTO fires (
                    session_id, fire_at, etop_captured_at, ps_raw_captured_at,
                    ps_fair_captured_at, ev_calculated_at,
                    fire_key, team1, team2, market, map_num,
                    side, vsid, remain_secs, ev_at_fire, priority_score,
                    etop_o1, etop_o2, ps_raw_t1, ps_raw_t2, ps_fair_t1, ps_fair_t2,
                    ps_age, aos_age, etop_age,
                    items_count, item_ids, item_value,
                    press_result, press_error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (session_id, fire_at, etop_captured_at, ps_raw_captured_at,
                 ps_fair_captured_at, ev_calculated_at,
                 fire_key, team1, team2, market, map_num,
                 side, vsid, remain_secs, ev_at_fire, priority_score,
                 etop_o1, etop_o2, ps_raw_t1, ps_raw_t2, ps_fair_t1, ps_fair_t2,
                 ps_age, aos_age, etop_age,
                 items_count, json.dumps(item_ids), item_value,
                 press_result, press_error)
            )
            self._conn.commit()
        except Exception as e:
            print(f"[DB] log_fire FAILED: {e}", flush=True)

    def log_outcome(self, fire_key: str,
                    closing_etop_o1: float, closing_etop_o2: float,
                    closing_ps_raw_t1: float, closing_ps_raw_t2: float,
                    closing_ps_fair_t1: float, closing_ps_fair_t2: float,
                    closing_ev_t1: float, closing_ev_t2: float,
                    won: bool, pnl_items: int, pnl_value: float):
        """Log match outcome with closing odds for performance analysis."""
        try:
            self._conn.execute(
                """INSERT OR REPLACE INTO outcomes (
                    fire_key, closing_etop_o1, closing_etop_o2,
                    closing_ps_raw_t1, closing_ps_raw_t2,
                    closing_ps_fair_t1, closing_ps_fair_t2,
                    closing_ev_t1, closing_ev_t2,
                    won, pnl_items, pnl_value,
                    closed_at, recorded_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fire_key, closing_etop_o1, closing_etop_o2,
                 closing_ps_raw_t1, closing_ps_raw_t2,
                 closing_ps_fair_t1, closing_ps_fair_t2,
                 closing_ev_t1, closing_ev_t2,
                 int(won), pnl_items, pnl_value,
                 time.time(), time.time())
            )
            self._conn.commit()
        except Exception as e:
            print(f"[DB] log_outcome FAILED: {e}", flush=True)

    def update_fire_state(self, fire_key: str, total_fired: int,
                          locked_side: int = 0, total_value: float = 0.0,
                          value_cap: float = 0.0, session_id: str = ''):
        now = time.time()
        try:
            self._conn.execute(
                """INSERT INTO fire_state (fire_key, total_fired, locked_side,
                   first_fire_at, last_fire_at, session_id, total_value, value_cap)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(fire_key) DO UPDATE SET
                     total_fired = ?,
                     locked_side = ?,
                     last_fire_at = ?,
                     session_id = ?,
                     total_value = ?,
                     value_cap = ?,
                     cancelled = 0""",
                (fire_key, total_fired, locked_side, now, now, session_id, total_value, value_cap,
                 total_fired, locked_side, now, session_id, total_value, value_cap)
            )
            self._conn.commit()
        except Exception as e:
            print(f"[DB] update_fire_state FAILED: {e}", flush=True)

    def get_fire_state(self, fire_key: str) -> tuple:
        """Returns (total_fired, locked_side, total_value, value_cap)."""
        try:
            row = self._conn.execute(
                "SELECT total_fired, locked_side, COALESCE(total_value,0), COALESCE(value_cap,0) "
                "FROM fire_state WHERE fire_key = ? AND cancelled = 0",
                (fire_key,)
            ).fetchone()
            return (row[0], row[1], row[2], row[3]) if row else (0, 0, 0.0, 0.0)
        except Exception as e:
            print(f"[DB] get_fire_state FAILED: {e}", flush=True)
            return (0, 0, 0.0, 0.0)

    def get_all_fire_states(self) -> dict:
        """Returns {fire_key: (total_fired, locked_side)}."""
        try:
            rows = self._conn.execute(
                "SELECT fire_key, total_fired, locked_side FROM fire_state WHERE cancelled = 0"
            ).fetchall()
            return {r[0]: (r[1], r[2]) for r in rows}
        except Exception as e:
            print(f"[DB] get_all_fire_states FAILED: {e}", flush=True)
            return {}

    def reset_fire_state(self, fire_key: str, reason: str = 'cancel'):
        try:
            self._conn.execute(
                "UPDATE fire_state SET total_fired = 0, locked_side = 0, cancelled = 1 WHERE fire_key = ?",
                (fire_key,)
            )
            self._conn.commit()
        except Exception as e:
            print(f"[DB] reset_fire_state FAILED: {e}", flush=True)

    def start_session(self, config: dict) -> str:
        sid = str(uuid.uuid4())[:8]
        try:
            self._conn.execute(
                "INSERT INTO sessions (session_id, started_at, config_snapshot) VALUES (?, ?, ?)",
                (sid, time.time(), json.dumps(config))
            )
            self._conn.commit()
            print(f"[DB] Session started: {sid}", flush=True)
        except Exception as e:
            print(f"[DB] start_session FAILED: {e}", flush=True)
        return sid

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
