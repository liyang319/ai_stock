"""双均线交叉策略（MA5 / MA10）。"""

import pandas as pd


def dual_ma_signal(df: pd.DataFrame):
    """MA5 上穿 MA10 返回「买」，下穿返回「卖」，否则返回 None。

    df 需包含「收盘」列，按日期升序；以最后两根 K 线的均线交叉为准。
    """
    if df is None or len(df) < 12 or "收盘" not in df.columns:
        return None
    close = pd.to_numeric(df["收盘"], errors="coerce")
    ma_fast = close.rolling(5).mean()
    ma_slow = close.rolling(10).mean()
    prev_fast, prev_slow = ma_fast.iloc[-2], ma_slow.iloc[-2]
    fast, slow = ma_fast.iloc[-1], ma_slow.iloc[-1]
    if any(pd.isna(v) for v in (prev_fast, prev_slow, fast, slow)):
        return None
    if prev_fast <= prev_slow and fast > slow:
        return "买"
    if prev_fast >= prev_slow and fast < slow:
        return "卖"
    return None
