"""MACD 金叉死叉策略（12, 26, 9）。"""

import pandas as pd


def macd_signal(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                signal: int = 9):
    """DIF 上穿 DEA 返回「买」，下穿返回「卖」，否则返回 None。"""
    if df is None or len(df) < slow + signal or "收盘" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    if close.iloc[-2:].isna().any():
        return None
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    prev_dif, prev_dea = dif.iloc[-2], dea.iloc[-2]
    cur_dif, cur_dea = dif.iloc[-1], dea.iloc[-1]
    if any(pd.isna(v) for v in (prev_dif, prev_dea, cur_dif, cur_dea)):
        return None
    if prev_dif <= prev_dea and cur_dif > cur_dea:
        return "买"
    if prev_dif >= prev_dea and cur_dif < cur_dea:
        return "卖"
    return None
