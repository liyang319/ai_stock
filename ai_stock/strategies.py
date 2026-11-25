"""量化策略定义与算法实现，供 app.py 调用。

目前实现：双均线交叉（MA5 / MA10）。
"""

import pandas as pd

# 每个策略分配唯一颜色，用于系统日志中的信号标识
STRATEGY_COLORS = {
    "双均线交叉": "#e5333c",      # 红
    "MACD 金叉死叉": "#f59e0b",   # 橙
    "RSI 超买超卖": "#d946ef",    # 品红
    "布林带突破": "#3b82f6",      # 蓝
    "动量轮动": "#16a34a",        # 绿
    "均值回归": "#0d9488",        # 青绿
    "网格交易": "#8b5cf6",        # 紫
    "海龟交易法则": "#b45309",    # 棕
    "量价背离": "#0891b2",        # 青
    "多因子选股": "#db2777",      # 玫红
    "趋势跟踪": "#65a30d",        # 黄绿
    "配对交易": "#475569",        # 石板灰
}

STRATEGY_NAMES = list(STRATEGY_COLORS.keys())


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


def format_signal_log(strategy: str, stock: str, action: str,
                      quantity: int, when: str) -> str:
    """生成策略日志：[策略名称]-[股票名称]-[买/卖]-[数量]-[提示时间: xx:xx:xx]。"""
    return f"[{strategy}]-[{stock}]-[{action}]-[{quantity}股]-[提示时间: {when}]"
