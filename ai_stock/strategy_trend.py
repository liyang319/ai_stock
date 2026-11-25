"""趋势跟踪策略（收盘价上穿/下穿 60 日均线）。"""

import pandas as pd


def trend_signal(df: pd.DataFrame, window: int = 60):
    """收盘价上穿 60 日均线返回「买」，下穿返回「卖」，否则 None。"""
    if df is None or len(df) < window + 2 or "收盘" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    if close.iloc[-(window + 2):].isna().any():
        return None
    ma = close.rolling(window).mean()
    prev_close, cur_close = close.iloc[-2], close.iloc[-1]
    prev_ma, cur_ma = ma.iloc[-2], ma.iloc[-1]
    if pd.isna(prev_ma) or pd.isna(cur_ma):
        return None
    if prev_close <= prev_ma and cur_close > cur_ma:
        return "买"
    if prev_close >= prev_ma and cur_close < cur_ma:
        return "卖"
    return None
