"""交易数据备份、清空，以及盘点暂存（temp）文件管理。"""

import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

import db_log
import excel_log

_LOCK = threading.Lock()
_DATE_DIR = re.compile(r"^\d{8}$")
_TEMP_FILE = re.compile(r"^ai_stock_inventory_\d{8}_\d{6}_temp\.(xlsx|db)$")


def _base_dir() -> Path:
    return excel_log.MAIN_FILE.parent


def temp_files() -> list[Path]:
    return sorted(
        p for p in _base_dir().iterdir()
        if p.is_file() and _TEMP_FILE.fullmatch(p.name)
    )


def current_temp() -> dict | None:
    files = temp_files()
    if not files:
        return None
    excel = next((p for p in files if p.suffix == ".xlsx"), None)
    db = next((p for p in files if p.suffix == ".db"), None)
    if not excel or not db:
        return None
    return {"excel": excel, "db": db}


def create_temp_files() -> dict:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = _base_dir()
    excel = base / f"ai_stock_inventory_{stamp}_temp.xlsx"
    db = base / f"ai_stock_inventory_{stamp}_temp.db"
    excel_log.ensure_workbook(excel)
    db_log.ensure_database(db)
    return {"excel": excel, "db": db, "stamp": stamp}


def delete_temp_files() -> int:
    removed = 0
    for path in temp_files():
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def backup_data() -> dict:
    """备份主文件与盘点归档文件到日期文件夹（不删除、不清空）。"""
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    folder = _base_dir() / now.strftime("%Y%m%d")
    copied = []
    with _LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        sources = (
            excel_log.MAIN_FILE, excel_log.INVENTORY_FILE,
            db_log.MAIN_DB, db_log.INVENTORY_DB,
        )
        for path in sources:
            if not path.exists():
                continue
            target = folder / f"{path.stem}_{stamp}{path.suffix}"
            counter = 1
            while target.exists():
                target = folder / f"{path.stem}_{stamp}_{counter}{path.suffix}"
                counter += 1
            shutil.copy2(path, target)
            copied.append(target)
    return {"folder": folder, "files": copied}


def clear_realtime() -> None:
    excel_log.clear_data(excel_log.MAIN_FILE)
    db_log.clear_data(db_log.MAIN_DB)


def clear_inventory() -> None:
    excel_log.clear_data(excel_log.INVENTORY_FILE)
    db_log.clear_data(db_log.INVENTORY_DB)
    delete_temp_files()
    excel_log.delete_merge_sheet(excel_log.MAIN_FILE)
    db_log.drop_merge(db_log.MAIN_DB)


def clear_all_data() -> int:
    """清空实时 + 盘点 + temp；历史备份文件夹保留。返回删除的 temp 数量。"""
    removed = delete_temp_files()
    clear_realtime()
    clear_inventory()
    return removed
