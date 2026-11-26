"""交易日志 SQLite 存储：ai_stock.db（表 trades，字段与 Excel 七列一致）。"""

import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

DB_FILE = Path(
    os.environ.get("AI_STOCK_DB", Path(__file__).with_name("ai_stock.db"))
)

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    stock TEXT NOT NULL,
    action TEXT NOT NULL,
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    trade_time TEXT NOT NULL,
    trade_date TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_trades_date_time ON trades(trade_date, trade_time)",
    "CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)",
)


def _open():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute(_SCHEMA)
    for stmt in _INDEXES:
        conn.execute(stmt)
    return conn


def ensure_database() -> None:
    """启动时创建数据库与 trades 表。"""
    with _LOCK, closing(_open()) as conn:
        conn.commit()


def append_trade_logs(entries: list[dict], dedupe: bool = False) -> int:
    """批量写入交易日志（与 Excel all_deal 相同的字段）。

    dedupe=True 时按「策略+股票+买卖+交易日期」跳过已存在记录。
    返回实际写入的条数。
    """
    if not entries:
        return 0
    rows = [
        (
            entry["strategy"], entry["stock"], entry["action"],
            float(entry["price"]), int(entry["quantity"]),
            entry["time"], entry["date"],
        )
        for entry in entries
    ]
    with _LOCK, closing(_open()) as conn:
        if dedupe:
            dates = sorted({row[6] for row in rows})
            placeholders = ",".join("?" for _ in dates)
            existing = set(conn.execute(
                "SELECT strategy, stock, action, trade_date FROM trades "
                f"WHERE trade_date IN ({placeholders})",
                dates,
            ).fetchall())
            rows = [r for r in rows if (r[0], r[1], r[2], r[6]) not in existing]
        if rows:
            conn.executemany(
                "INSERT INTO trades "
                "(strategy, stock, action, price, quantity, trade_time, trade_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)


def clear_data() -> None:
    """清空 trades 表（保留表结构）。"""
    with _LOCK, closing(_open()) as conn:
        conn.execute("DELETE FROM trades")
        conn.commit()
