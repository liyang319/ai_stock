"""交易日志 Excel 导出：ai_stock.xlsx。

工作表结构：all_deal（全部交易，按时间顺序）+ 每个量化策略一个 sheet。
"""

import os
import threading
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

import strategies

EXCEL_FILE = Path(
    os.environ.get("AI_STOCK_EXCEL", Path(__file__).with_name("ai_stock.xlsx"))
)

HEADER = ["量化策略名称", "股票名称", "买卖", "当前交易价格", "数量", "提示时间", "交易日期"]
ALL_SHEET = "all_deal"
SEQUENCE = [ALL_SHEET] + strategies.STRATEGY_NAMES
COLUMN_WIDTHS = [18, 14, 8, 14, 10, 14, 14]

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


def _prepare_sheet(ws) -> None:
    # 逐列补齐表头（兼容旧文件新增列，如「交易日期」）
    for col, name in enumerate(HEADER, start=1):
        cell = ws.cell(row=1, column=col)
        if cell.value is None:
            cell.value = name
            cell.font = Font(bold=True, color="FF111111")
            cell.fill = PatternFill(fill_type="solid", start_color="FFF2F2F2", end_color="FFF2F2F2")
    for idx, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _prepare_workbook(wb) -> None:
    for name in SEQUENCE:
        if name in wb.sheetnames:
            sheet = wb[name]
        else:
            sheet = wb.create_sheet(title=name)
        _prepare_sheet(sheet)
    # 固定顺序：all_deal 在最前，其后为各策略
    wb._sheets.sort(key=lambda s: SEQUENCE.index(s.title) if s.title in SEQUENCE else len(SEQUENCE))


def _load_or_new():
    try:
        return load_workbook(EXCEL_FILE)
    except Exception:
        wb = Workbook()
        if "Sheet" in wb.sheetnames:
            del wb["Sheet"]
        return wb


def ensure_workbook() -> None:
    """启动时创建或补齐工作簿（含全部策略 sheet 与表头）。"""
    with _LOCK:
        wb = _load_or_new()
        _prepare_workbook(wb)
        wb.save(EXCEL_FILE)


def _append_row(ws, entry: dict) -> None:
    row = ws.max_row + 1
    values = [
        entry["strategy"], entry["stock"], entry["action"],
        float(entry["price"]), int(entry["quantity"]), entry["time"],
        entry["date"],
    ]
    for col, value in enumerate(values, start=1):
        ws.cell(row=row, column=col, value=value)
    color = strategies.STRATEGY_COLORS.get(entry["strategy"], "#000000")
    name_cell = ws.cell(row=row, column=1)
    name_cell.fill = _fill(color)
    name_cell.font = _font(color)
    ws.cell(row=row, column=4).number_format = "0.00"
    ws.cell(row=row, column=5).number_format = '0"股"'


def append_trade_logs(entries: list[dict]) -> None:
    """追加交易日志：写入各自策略表，并按时间顺序汇总到 all_deal。"""
    if not entries:
        return
    ordered = sorted(entries, key=lambda e: (e.get("date", ""), e["time"]))
    with _LOCK:
        wb = _load_or_new()
        _prepare_workbook(wb)
        for entry in ordered:
            if entry["strategy"] in wb.sheetnames:
                _append_row(wb[entry["strategy"]], entry)
            _append_row(wb[ALL_SHEET], entry)
        wb.save(EXCEL_FILE)
