"""量价背离策略（创新高且缩量提示卖出，创新低且缩量提示买入）。"""

import pandas as pd


def pv_divergence_signal(df: pd.DataFrame, window: int = 20,
                         vol_short: int = 5, vol_long: int = 20):
    """高位缩量（价创 20 日新高、5 日均量小于 20 日均量）返回「卖」；
    低位缩量（价创 20 日新低且缩量）返回「买」，否则 None。"""
    if df is None or len(df) < max(window, vol_long) + 1:
        return None
    if "收盘" not in df.columns or "成交量" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    volume = pd.to_numeric(df["成交量"], errors="coerce")
    if close.iloc[-window:].isna().any() or volume.iloc[-vol_long:].isna().any():
        return None
    roll_max = close.rolling(window).max()
    roll_min = close.rolling(window).min()
    vol_ma_short = volume.rolling(vol_short).mean()
    vol_ma_long = volume.rolling(vol_long).mean()
    if pd.isna(vol_ma_short.iloc[-1]) or pd.isna(vol_ma_long.iloc[-1]):
        return None
    if vol_ma_short.iloc[-1] >= vol_ma_long.iloc[-1]:
        return None
    prev_close, cur_close = close.iloc[-2], close.iloc[-1]
    if cur_close >= roll_max.iloc[-1] and prev_close < roll_max.iloc[-1]:
        return "卖"
    if cur_close <= roll_min.iloc[-1] and prev_close > roll_min.iloc[-1]:
        return "买"
    return None
