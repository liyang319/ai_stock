"""交易数据备份与清空：按日期文件夹备份 ai_stock.xlsx / ai_stock.db。"""

import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

import db_log
import excel_log

_LOCK = threading.Lock()
_DATE_DIR = re.compile(r"^\d{8}$")


def _base_dir() -> Path:
    return excel_log.EXCEL_FILE.parent


def backup_and_clear() -> dict:
    """把当前 Excel/DB 备份到日期文件夹（文件名含备份时间），再重建空文件。"""
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    folder = _base_dir() / now.strftime("%Y%m%d")
    moved = []
    with _LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        for path in (excel_log.EXCEL_FILE, db_log.DB_FILE):
            if not path.exists():
                continue
            target = folder / f"{path.stem}_{stamp}{path.suffix}"
            counter = 1
            while target.exists():
                target = folder / f"{path.stem}_{stamp}_{counter}{path.suffix}"
                counter += 1
            shutil.move(str(path), str(target))
            moved.append(target)
        excel_log.ensure_workbook()
        db_log.ensure_database()
    return {"folder": folder, "moved": moved}


def clear_all() -> dict:
    """删除所有日期文件夹内的备份文件，并清空当前 Excel/数据库数据。"""
    removed_files = 0
    removed_dirs = 0
    stems = {excel_log.EXCEL_FILE.stem, db_log.DB_FILE.stem}
    suffixes = {excel_log.EXCEL_FILE.suffix, db_log.DB_FILE.suffix}

    def is_backup(file: Path) -> bool:
        return (
            file.is_file()
            and file.suffix in suffixes
            and any(file.name.startswith(f"{stem}_") for stem in stems)
        )

    with _LOCK:
        for child in _base_dir().iterdir():
            if not (child.is_dir() and _DATE_DIR.fullmatch(child.name)):
                continue
            for file in child.iterdir():
                if is_backup(file):
                    file.unlink()
                    removed_files += 1
            try:
                child.rmdir()
                removed_dirs += 1
            except OSError:
                pass
        excel_log.clear_data()
        db_log.clear_data()
    return {"files": removed_files, "dirs": removed_dirs}
