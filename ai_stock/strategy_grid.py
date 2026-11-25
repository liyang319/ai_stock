"""网格交易策略（以 20 日均线为中轴，上下 3% 为网格线）。"""

import pandas as pd


def grid_signal(df: pd.DataFrame, window: int = 20, step: float = 0.03):
    """价格跌破下网格线返回「买」，升破上网格线返回「卖」，否则 None。"""
    if df is None or len(df) < window + 2 or "收盘" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    if close.iloc[-(window + 2):].isna().any():
        return None
    base = close.rolling(window).mean()
    prev_close, cur_close = close.iloc[-2], close.iloc[-1]
    prev_base, cur_base = base.iloc[-2], base.iloc[-1]
    if pd.isna(prev_base) or pd.isna(cur_base):
        return None
    if prev_close >= prev_base * (1 - step) and cur_close < cur_base * (1 - step):
        return "买"
    if prev_close <= prev_base * (1 + step) and cur_close > cur_base * (1 + step):
        return "卖"
    return None
