"""动量轮动策略（20 日涨跌幅突破 ±5%）。"""

import pandas as pd


def momentum_signal(df: pd.DataFrame, window: int = 20,
                    buy_threshold: float = 0.05,
                    sell_threshold: float = -0.05):
    """20 日动量上穿 +5% 返回「买」，下穿 -5% 返回「卖」，否则 None。"""
    if df is None or len(df) < window + 2 or "收盘" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    if close.iloc[-(window + 2):].isna().any():
        return None
    roc = close / close.shift(window) - 1
    prev_roc, cur_roc = roc.iloc[-2], roc.iloc[-1]
    if pd.isna(prev_roc) or pd.isna(cur_roc):
        return None
    if prev_roc <= buy_threshold and cur_roc > buy_threshold:
        return "买"
    if prev_roc >= sell_threshold and cur_roc < sell_threshold:
        return "卖"
    return None
