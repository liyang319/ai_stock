"""布林带突破策略（20 日均线 ± 2 倍标准差）。"""

import pandas as pd


def bollinger_signal(df: pd.DataFrame, window: int = 20, k: float = 2.0):
    """收盘价上穿上轨返回「买」，跌破下轨返回「卖」，否则 None。"""
    if df is None or len(df) < window + 1 or "收盘" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    if close.iloc[-(window + 1):].isna().any():
        return None
    ma = close.rolling(window).mean()
    sd = close.rolling(window).std(ddof=0)
    upper = ma + k * sd
    lower = ma - k * sd
    prev_close, cur_close = close.iloc[-2], close.iloc[-1]
    if prev_close <= upper.iloc[-2] and cur_close > upper.iloc[-1]:
        return "买"
    if prev_close >= lower.iloc[-2] and cur_close < lower.iloc[-1]:
        return "卖"
    return None
