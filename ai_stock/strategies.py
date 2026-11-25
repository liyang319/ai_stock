"""量化策略共享配置与工具：策略颜色、名称、日志格式。

各策略算法实现见同目录 strategy_dual_ma.py / strategy_macd.py / strategy_rsi.py。
"""

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


def format_signal_log(strategy: str, stock: str, action: str,
                      quantity: int, when: str) -> str:
    """生成策略日志：[策略名称]-[股票名称]-[买/卖]-[数量]-[提示时间: xx:xx:xx]。"""
    return f"[{strategy}]-[{stock}]-[{action}]-[{quantity}股]-[提示时间: {when}]"
