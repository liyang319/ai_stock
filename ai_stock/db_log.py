"""交易日志 SQLite 存储：主库 + 盘点归档 + merge 表。"""

import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

MAIN_DB = Path(
    os.environ.get("AI_STOCK_DB", Path(__file__).with_name("ai_stock.db"))
)
INVENTORY_DB = Path(
    os.environ.get(
        "AI_STOCK_INVENTORY_DB", MAIN_DB.parent / "ai_stock_inventory.db"
    )
)
DB_FILE = MAIN_DB  # 兼容旧引用

TABLE_TRADES = "trades"
TABLE_MERGE = "merge"
SOURCE_LIVE = "实时"
SOURCE_REPLAY = "盘点"

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
    trade_date TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '实时'
)
"""

_MERGE_SCHEMA = _SCHEMA.replace("trades", "merge")

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_trades_date_time ON trades(trade_date, trade_time)",
    "CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)",
)


def _open(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute(_SCHEMA)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(trades)")]
    if "source" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN source TEXT DEFAULT '实时'")
    conn.execute("UPDATE trades SET source='实时' WHERE source IS NULL OR source=''")
    for stmt in _INDEXES:
        conn.execute(stmt)
    return conn


def ensure_database(path: Path | None = None) -> None:
    target = path or MAIN_DB
    with _LOCK, closing(_open(target)) as conn:
        conn.commit()


def append_trade_logs(entries: list[dict], dedupe: bool = False,
                      path: Path | None = None) -> int:
    """批量写入 trades；dedupe 时按「策略+股票+买卖+交易日期」跳过。"""
    if not entries:
        return 0
    target = path or MAIN_DB
    rows = [
        (
            entry["strategy"], entry["stock"], entry["action"],
            float(entry["price"]), int(entry["quantity"]),
            entry["time"], entry["date"],
            entry.get("source", SOURCE_LIVE),
        )
        for entry in entries
    ]
    with _LOCK, closing(_open(target)) as conn:
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
                "(strategy, stock, action, price, quantity, trade_time, trade_date, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)


def read_trades(path: Path | None = None) -> list[dict]:
    target = path or MAIN_DB
    if not target.exists():
        return []
    with closing(_open(target)) as conn:
        rows = conn.execute(
            "SELECT strategy, stock, action, price, quantity, trade_time, "
            "trade_date, source FROM trades ORDER BY trade_date, trade_time, id"
        ).fetchall()
    return [
        {
            "strategy": r[0], "stock": r[1], "action": r[2], "price": r[3],
            "quantity": r[4], "time": r[5], "date": r[6],
            "source": r[7] or SOURCE_LIVE,
        }
        for r in rows
    ]


def rebuild_merge(main_path: Path | None = None,
                  inventory_path: Path | None = None) -> int:
    """重建 merge 表：实时优先去重合并实时 + 盘点全部数据。"""
    main = main_path or MAIN_DB
    inventory = inventory_path or INVENTORY_DB
    merged = {}
    for row in read_trades(inventory):
        merged[(row["strategy"], row["stock"], row["action"], row["date"])] = row
    for row in read_trades(main):
        merged[(row["strategy"], row["stock"], row["action"], row["date"])] = row
    rows = sorted(merged.values(), key=lambda r: (r["date"], r["time"], r["strategy"]))
    with _LOCK, closing(_open(main)) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TABLE_MERGE}")
        conn.execute(_MERGE_SCHEMA)
        conn.executemany(
            "INSERT INTO merge "
            "(strategy, stock, action, price, quantity, trade_time, trade_date, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (r["strategy"], r["stock"], r["action"], float(r["price"]),
                 int(r["quantity"]), r["time"], r["date"],
                 r.get("source", SOURCE_LIVE))
                for r in rows
            ],
        )
        conn.commit()
    return len(rows)


def drop_merge(path: Path | None = None) -> None:
    target = path or MAIN_DB
    with _LOCK, closing(_open(target)) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TABLE_MERGE}")
        conn.commit()


def clear_data(path: Path | None = None) -> None:
    """清空 trades 表与 merge 表（保留结构）。"""
    target = path or MAIN_DB
    with _LOCK, closing(_open(target)) as conn:
        conn.execute("DELETE FROM trades")
        conn.execute(f"DROP TABLE IF EXISTS {TABLE_MERGE}")
        conn.commit()
