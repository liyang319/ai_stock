"""配对交易策略（单票简化版：60 日价差 Z-Score 回归）。"""

import pandas as pd


def pairs_signal(df: pd.DataFrame, window: int = 60, z: float = 2.0):
    """以 60 日均线为价差中枢：Z-Score 跌破 -2 返回「买」，升破 +2 返回「卖」。

    标准配对交易需要两只相关标的的共同价差；此处用单票价格相对长期均值
    的偏离作为价差替代，供看板内单只股票直接使用。
    """
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
