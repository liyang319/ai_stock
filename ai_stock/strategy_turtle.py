"""海龟交易法则（20 日高点突破买入，10 日低点跌破卖出）。"""

import pandas as pd


def turtle_signal(df: pd.DataFrame, entry_window: int = 20,
                  exit_window: int = 10):
    """收盘价突破前 20 日最高价返回「买」，跌破前 10 日最低价返回「卖」。"""
    if df is None or len(df) < entry_window + 2 or "收盘" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    high = pd.to_numeric(df["最高"], errors="coerce")
    low = pd.to_numeric(df["最低"], errors="coerce")
    if close.iloc[-2:].isna().any() or high.iloc[-entry_window - 1:].isna().any():
        return None
    prev_entry_high = high.shift(1).rolling(entry_window).max()
    prev_exit_low = low.shift(1).rolling(exit_window).min()
    prev_close, cur_close = close.iloc[-2], close.iloc[-1]
    if (prev_close <= prev_entry_high.iloc[-2]
            and cur_close > prev_entry_high.iloc[-1]):
        return "买"
    if (prev_close >= prev_exit_low.iloc[-2]
            and cur_close < prev_exit_low.iloc[-1]):
        return "卖"
    return None
