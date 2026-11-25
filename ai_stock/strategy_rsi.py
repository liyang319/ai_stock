"""RSI 超买超卖策略（默认 14 日，30/70 阈值）。"""

import pandas as pd


def rsi_signal(df: pd.DataFrame, period: int = 14,
               oversold: float = 30, overbought: float = 70):
    """RSI 下穿超卖线返回「买」，上穿超买线返回「卖」，否则返回 None。"""
    if df is None or len(df) < period + 2 or "收盘" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    if close.iloc[-2:].isna().any():
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    rsi = rsi.mask((loss == 0) & (gain > 0), 100.0)
    rsi = rsi.mask((loss == 0) & (gain == 0), 50.0)
    prev_rsi, cur_rsi = rsi.iloc[-2], rsi.iloc[-1]
    if pd.isna(prev_rsi) or pd.isna(cur_rsi):
        return None
    if prev_rsi >= oversold and cur_rsi < oversold:
        return "买"
    if prev_rsi <= overbought and cur_rsi > overbought:
        return "卖"
    return None
