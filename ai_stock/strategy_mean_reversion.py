"""均值回归策略（20 日均线 Z-Score ±2）。"""

import pandas as pd


def mean_reversion_signal(df: pd.DataFrame, window: int = 20, z: float = 2.0):
    """Z-Score 跌破 -2 返回「买」，升破 +2 返回「卖」，否则 None。"""
    if df is None or len(df) < window + 2 or "收盘" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    if close.iloc[-(window + 2):].isna().any():
        return None
    ma = close.rolling(window).mean()
    sd = close.rolling(window).std(ddof=0)
    zscore = (close - ma) / sd
    prev_z, cur_z = zscore.iloc[-2], zscore.iloc[-1]
    if pd.isna(prev_z) or pd.isna(cur_z):
        return None
    if prev_z >= -z and cur_z < -z:
        return "买"
    if prev_z <= z and cur_z > z:
        return "卖"
    return None
