from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import Candle, Signal, utc_now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candles (
    instrument TEXT NOT NULL,
    granularity TEXT NOT NULL,
    time TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    PRIMARY KEY (instrument, granularity, time)
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    instrument TEXT NOT NULL,
    action TEXT NOT NULL,
    probability_up REAL NOT NULL,
    confidence REAL NOT NULL,
    regime TEXT NOT NULL,
    directive TEXT NOT NULL,
    spread_pips REAL,
    reason TEXT NOT NULL,
    model TEXT NOT NULL,
    features_json TEXT NOT NULL,
    executed INTEGER NOT NULL DEFAULT 0,
    execution_mode TEXT,
    execution_ref TEXT
);
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument TEXT NOT NULL,
    side TEXT NOT NULL,
    units INTEGER NOT NULL,
    open_time TEXT NOT NULL,
    open_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    take_price REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    close_time TEXT,
    close_price REAL,
    pnl_jpy REAL,
    exit_reason TEXT,
    bars_held INTEGER NOT NULL DEFAULT 0,
    reviewed INTEGER NOT NULL DEFAULT 0,
    strategy TEXT NOT NULL DEFAULT 'session'
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    category TEXT NOT NULL,
    verdict TEXT NOT NULL,
    lesson TEXT NOT NULL,
    model TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    hypothesis_json TEXT NOT NULL,
    baseline_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    verdict TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            # 既存DBの移行: strategy列(戦略アリーナ用)が無ければ追加
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(paper_trades)")}
            if "strategy" not in cols:
                conn.execute(
                    "ALTER TABLE paper_trades ADD COLUMN strategy TEXT NOT NULL DEFAULT 'session'"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def set_state(self, key: str, value: Any) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO state(key,value_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), now),
            )

    def get_state(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value_json FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def save_candles(self, instrument: str, granularity: str, candles: list[Candle]) -> None:
        rows = [
            (instrument, granularity, c.time, c.open, c.high, c.low, c.close, c.volume)
            for c in candles
            if c.complete
        ]
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO candles "
                "(instrument,granularity,time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
                rows,
            )

    def load_candles(self, instrument: str, granularity: str, limit: int = 2000) -> list[Candle]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM candles WHERE instrument=? AND granularity=? "
                "ORDER BY time DESC LIMIT ?",
                (instrument, granularity, limit),
            ).fetchall()
        return [
            Candle(
                time=row["time"], open=row["open"], high=row["high"], low=row["low"],
                close=row["close"], volume=row["volume"], complete=True,
            )
            for row in reversed(rows)
        ]

    def record_decision(self, signal: Signal, spread_pips: float | None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO decisions(created_at,instrument,action,probability_up,confidence,"
                "regime,directive,spread_pips,reason,model,features_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    utc_now_iso(), signal.instrument, signal.action, signal.probability_up,
                    signal.confidence, signal.regime, signal.directive, spread_pips,
                    signal.reason, signal.model, json.dumps(signal.features, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def mark_executed(self, decision_id: int, mode: str, reference: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE decisions SET executed=1, execution_mode=?, execution_ref=? WHERE id=?",
                (mode, reference, decision_id),
            )

    def open_paper_trade(
        self, instrument: str, side: str, units: int, price: float,
        stop_price: float, take_price: float, strategy: str = "session",
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO paper_trades(instrument,side,units,open_time,open_price,stop_price,"
                "take_price,strategy) VALUES(?,?,?,?,?,?,?,?)",
                (instrument, side, units, utc_now_iso(), price, stop_price, take_price, strategy),
            )
            return int(cur.lastrowid)

    def open_paper_trades(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM paper_trades WHERE status='open' ORDER BY id")

    def advance_paper_trade(self, trade_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE paper_trades SET bars_held=bars_held+1 WHERE id=?", (trade_id,))

    def close_paper_trade(
        self, trade_id: int, close_price: float, pnl_jpy: float, exit_reason: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE paper_trades SET status='closed',close_time=?,close_price=?,pnl_jpy=?,"
                "exit_reason=? WHERE id=?",
                (utc_now_iso(), close_price, pnl_jpy, exit_reason, trade_id),
            )

    def today_pnl_jpy(self) -> float:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl_jpy),0) AS pnl FROM paper_trades "
                "WHERE status='closed' AND date(close_time)=date('now')"
            ).fetchone()
        return float(row["pnl"])

    def start_cycle(self) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO cycles(started_at,status) VALUES(?,?)", (utc_now_iso(), "running")
            )
            return int(cur.lastrowid)

    def finish_cycle(self, cycle_id: int, status: str, detail: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE cycles SET finished_at=?,status=?,detail=? WHERE id=?",
                (utc_now_iso(), status, detail[:2000], cycle_id),
            )

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


