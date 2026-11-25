"""多因子选股策略（单票简化版：动量 + 低波动综合评分）。"""

import pandas as pd


def multifactor_signal(df: pd.DataFrame, window: int = 20,
                       vol_weight: float = 2.0, threshold: float = 0.05):
    """综合评分 = 20 日动量 - 2×波动率；上穿 +0.05 返回「买」，
    下穿 -0.05 返回「卖」，否则 None。"""
    if df is None or len(df) < window + 2 or "收盘" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    if close.iloc[-(window + 2):].isna().any():
        return None
    returns = close.pct_change()
    momentum = close / close.shift(window) - 1
    volatility = returns.rolling(window).std(ddof=0)
    score = momentum - vol_weight * volatility
    prev_score, cur_score = score.iloc[-2], score.iloc[-1]
    if pd.isna(prev_score) or pd.isna(cur_score):
        return None
    if prev_score <= threshold and cur_score > threshold:
        return "买"
    if prev_score >= -threshold and cur_score < -threshold:
        return "卖"
    return None
