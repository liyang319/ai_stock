"""交易日志 Excel 存储：主文件 + 盘点归档 + merge 表。

工作表结构：all_deal（全部交易）+ 每个量化策略一个 sheet；每次「所有数据合并」
会重建 merge sheet（实时 + 盘点去重后的完整结果）。
"""

import os
import threading
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

import strategies

MAIN_FILE = Path(
    os.environ.get("AI_STOCK_EXCEL", Path(__file__).with_name("ai_stock.xlsx"))
)
INVENTORY_FILE = Path(
    os.environ.get(
        "AI_STOCK_INVENTORY_EXCEL", MAIN_FILE.parent / "ai_stock_inventory.xlsx"
    )
)
EXCEL_FILE = MAIN_FILE  # 兼容旧引用

HEADER = ["量化策略名称", "股票名称", "买卖", "当前交易价格",
          "数量", "交易时间", "交易日期", "来源"]
TABLE_SHEETS = ["all_deal"] + strategies.STRATEGY_NAMES
MERGE_SHEET = "merge"
SOURCE_LIVE = "实时"
SOURCE_REPLAY = "盘点"
COLUMN_WIDTHS = [18, 14, 8, 14, 10, 14, 14, 10]

_LOCK = threading.Lock()


def _argb(color: str) -> str:
    return "FF" + color.lstrip("#").upper()


def _fill(color: str) -> PatternFill:
    rgb = _argb(color)
    return PatternFill(fill_type="solid", start_color=rgb, end_color=rgb)


def _font(color: str) -> Font:
    rgb = color.lstrip("#")
    r, g, b = int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return Font(color="FFFFFFFF" if luminance < 140 else "FF000000")


def _write_header(ws) -> None:
    for col, name in enumerate(HEADER, start=1):
        cell = ws.cell(row=1, column=col)
        if cell.value is None:
            cell.value = name
        elif col == 6 and cell.value == "提示时间":
            cell.value = "交易时间"  # 旧表头迁移
        if col <= len(HEADER):
            cell.font = Font(bold=True, color="FF111111")
            cell.fill = PatternFill(fill_type="solid", start_color="FFF2F2F2", end_color="FFF2F2F2")
    for idx, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _migrate_sources(ws) -> None:
    for row in range(2, ws.max_row + 1):
        cell = ws.cell(row=row, column=8)
        if cell.value in (None, ""):
            cell.value = SOURCE_LIVE


def _prepare_sheet(ws) -> None:
    _write_header(ws)
    _migrate_sources(ws)


def _prepare_workbook(wb) -> None:
    for name in TABLE_SHEETS:
        if name not in wb.sheetnames:
            wb.create_sheet(title=name)
        _prepare_sheet(wb[name])
    wb._sheets.sort(
        key=lambda s: TABLE_SHEETS.index(s.title)
        if s.title in TABLE_SHEETS else len(TABLE_SHEETS)
    )


def _load_or_new(path: Path):
    try:
        return load_workbook(path)
    except Exception:
        wb = Workbook()
        if "Sheet" in wb.sheetnames:
            del wb["Sheet"]
        return wb


def ensure_workbook(path: Path | None = None) -> None:
    target = path or MAIN_FILE
    with _LOCK:
        wb = _load_or_new(target)
        _prepare_workbook(wb)
        wb.save(target)


def _append_row(ws, entry: dict) -> None:
    row = ws.max_row + 1
    values = [
        entry["strategy"], entry["stock"], entry["action"],
        float(entry["price"]), int(entry["quantity"]), entry["time"],
        entry["date"], entry.get("source", SOURCE_LIVE),
    ]
    for col, value in enumerate(values, start=1):
        ws.cell(row=row, column=col, value=value)
    color = strategies.STRATEGY_COLORS.get(entry["strategy"], "#000000")
    name_cell = ws.cell(row=row, column=1)
    name_cell.fill = _fill(color)
    name_cell.font = _font(color)
    ws.cell(row=row, column=4).number_format = "0.00"
    ws.cell(row=row, column=5).number_format = '0"股"'


def _existing_keys(wb) -> set:
    keys = set()
    for name in TABLE_SHEETS:
        ws = wb[name]
        for row in range(2, ws.max_row + 1):
            keys.add((
                ws.cell(row, 1).value, ws.cell(row, 2).value,
                ws.cell(row, 3).value, ws.cell(row, 7).value,
            ))
    return keys


def append_trade_logs(entries: list[dict], dedupe: bool = False,
                      path: Path | None = None) -> int:
    """追加交易日志；dedupe 时按「策略+股票+买卖+交易日期」跳过重复。"""
    if not entries:
        return 0
    target = path or MAIN_FILE
    ordered = sorted(entries, key=lambda e: (e.get("date", ""), e.get("time", "")))
    with _LOCK:
        wb = _load_or_new(target)
        _prepare_workbook(wb)
        if dedupe:
            existing = _existing_keys(wb)
            ordered = [
                e for e in ordered
                if (e["strategy"], e["stock"], e["action"], e["date"]) not in existing
            ]
        for entry in ordered:
            if entry["strategy"] in wb.sheetnames:
                _append_row(wb[entry["strategy"]], entry)
            _append_row(wb["all_deal"], entry)
        wb.save(target)
        return len(ordered)


def read_rows(path: Path | None = None) -> list[dict]:
    """读取 all_deal 全部数据行（来源为空按「实时」处理）。"""
    target = path or MAIN_FILE
    if not target.exists():
        return []
    wb = load_workbook(target, read_only=True)
    if "all_deal" not in wb.sheetnames:
        wb.close()
        return []
    ws = wb["all_deal"]
    rows = []
    for values in ws.iter_rows(min_row=2, max_col=8, values_only=True):
        if values[0] is None:
            continue
        rows.append({
            "strategy": values[0], "stock": values[1], "action": values[2],
            "price": values[3], "quantity": values[4], "time": values[5],
            "date": values[6], "source": values[7] or SOURCE_LIVE,
        })
    wb.close()
    return rows


def _row_key(row: dict) -> tuple:
    return (row["strategy"], row["stock"], row["action"], row["date"])


def rebuild_merge(main_path: Path | None = None,
                  inventory_path: Path | None = None) -> int:
    """重建 merge sheet：实时优先去重合并实时 + 盘点全部数据。"""
    main = main_path or MAIN_FILE
    inventory = inventory_path or INVENTORY_FILE
    merged = {}
    for row in read_rows(inventory):
        merged[_row_key(row)] = row
    for row in read_rows(main):
        merged[_row_key(row)] = row  # 实时覆盖盘点
    rows = sorted(
        merged.values(),
        key=lambda r: (str(r["date"]), str(r["time"]), str(r["strategy"])),
    )
    with _LOCK:
        wb = _load_or_new(main)
        _prepare_workbook(wb)
        if MERGE_SHEET in wb.sheetnames:
            del wb[MERGE_SHEET]
        ws = wb.create_sheet(title=MERGE_SHEET)
        _write_header(ws)
        for row in rows:
            _append_row(ws, row)
        wb.save(main)
    return len(rows)


def delete_merge_sheet(path: Path | None = None) -> None:
    target = path or MAIN_FILE
    if not target.exists():
        return
    with _LOCK:
        wb = _load_or_new(target)
        if MERGE_SHEET in wb.sheetnames:
            del wb[MERGE_SHEET]
            wb.save(target)


def clear_data(path: Path | None = None) -> None:
    """清空所有交易 sheet 的数据行与 merge sheet，保留表头结构。"""
    target = path or MAIN_FILE
    with _LOCK:
        wb = _load_or_new(target)
        _prepare_workbook(wb)
        for name in TABLE_SHEETS:
            ws = wb[name]
            if ws.max_row > 1:
                ws.delete_rows(2, ws.max_row - 1)
        if MERGE_SHEET in wb.sheetnames:
            del wb[MERGE_SHEET]
        wb.save(target)
