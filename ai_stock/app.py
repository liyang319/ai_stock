"""股票行情助手：大盘指数 + 自选看板（K线/分时，点击放大，持久化）。

运行方式：
    streamlit run app.py

数据来源：AKShare（新浪财经接口，避免东方财富限流）。
"""

import datetime as dt
import html
import json
import os
import re
import time
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from pypinyin import Style, lazy_pinyin
from streamlit_sortables import sort_items

import db_log
import excel_log
import storage_backup
import strategies
import strategy_bollinger
import strategy_dual_ma
import strategy_grid
import strategy_macd
import strategy_mean_reversion
import strategy_momentum
import strategy_multifactor
import strategy_pairs
import strategy_pv_divergence
import strategy_rsi
import strategy_trend
import strategy_turtle

st.set_page_config(page_title="老李股票行情助手", page_icon="📈", layout="wide")

UP_COLOR = "#e5333c"      # 中国习惯：红涨
DOWN_COLOR = "#1aa260"    # 绿跌
FLAT_COLOR = "#666666"
FLAT_BAR_COLOR = "#9e9e9e"   # 涨跌条中的平盘段，加深的灰色

INDEXES = [
    ("000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
    ("000300", "沪深300"),
    ("000688", "科创50"),
]

QUANT_STRATEGIES = strategies.STRATEGY_NAMES
SIGNAL_QUANTITY = 1000

# 已实现的策略：名称 -> 信号函数；勾选后按此注册表计算
STRATEGY_RUNNERS = {
    "双均线交叉": strategy_dual_ma.dual_ma_signal,
    "MACD 金叉死叉": strategy_macd.macd_signal,
    "RSI 超买超卖": strategy_rsi.rsi_signal,
    "布林带突破": strategy_bollinger.bollinger_signal,
    "动量轮动": strategy_momentum.momentum_signal,
    "均值回归": strategy_mean_reversion.mean_reversion_signal,
    "网格交易": strategy_grid.grid_signal,
    "海龟交易法则": strategy_turtle.turtle_signal,
    "量价背离": strategy_pv_divergence.pv_divergence_signal,
    "多因子选股": strategy_multifactor.multifactor_signal,
    "趋势跟踪": strategy_trend.trend_signal,
    "配对交易": strategy_pairs.pairs_signal,
}

# (外盘代码, 名称关键字, 显示名称)
COMMODITIES = [
    ("GC", "黄金", "黄金"),
    ("SI", "白银", "白银"),
    ("CL", "原油", "原油"),
    ("NG", "天然气", "天然气"),
    ("HG", "铜", "铜"),
]

# (接口类型, AKShare 代码, 显示代码, 显示名称)
GLOBAL_INDEXES = [
    ("us", ".DJI", "DJI", "道琼斯"),
    ("us", ".IXIC", "IXIC", "纳斯达克"),
    ("us", ".INX", "SPX", "标普500"),
    ("sina", "日经225指数", "N225", "日经225"),
    ("sina", "首尔综合指数", "KOSPI", "韩国综合"),
    ("sina", "中国台湾加权指数", "TWII", "台湾加权"),
]

PERIODS = {"日K": "daily", "周K": "weekly", "月K": "monthly"}
RANGES = {"3个月": 90, "6个月": 180, "1年": 365, "3年": 1095, "全部": None}
REFRESH_OPTIONS = {"关闭": 0, "15秒": 15, "30秒": 30, "60秒": 60}

MA_PERIODS = (5, 10, 20, 30)
# 通达信/同花顺习惯：5日白、10日黄、20日紫、30日绿（白底上 5日 用灰色替代）
MA_COLORS = {5: "#9e9e9e", 10: "#e6a700", 20: "#d000d0", 30: "#00a651"}
PERIOD_UNITS = {"日K": "日", "周K": "周", "月K": "月"}

LOG_COLORS = {"info": "#111111", "debug": "#1e3a8a", "trade": "#111111"}
LOG_MAX_ENTRIES = 100
SOURCE_LIVE = excel_log.SOURCE_LIVE
SOURCE_REPLAY = excel_log.SOURCE_REPLAY
BTN_WIDTH = 170
STORE_BUTTON_CSS = (
    "<style>"
    ".st-key-merge_temp_btn button{background-color:#dbeafe;color:#1e3a8a;border:1px solid #93c5fd;}"
    ".st-key-merge_all_btn button{background-color:#dcfce7;color:#166534;border:1px solid #86efac;}"
    ".st-key-backup_data_btn button{background-color:#fef9c3;color:#854d0e;border:1px solid #fde047;}"
    ".st-key-clear_rt_btn button{background-color:#ffedd5;color:#9a3412;border:1px solid #fdba74;}"
    ".st-key-clear_inv_btn button{background-color:#fee2e2;color:#991b1b;border:1px solid #fca5a5;}"
    ".st-key-clear_all_btn button{background-color:#ede9fe;color:#5b21b6;border:1px solid #c4b5fd;}"
    ".st-key-merge_temp_btn button:hover{background-color:#bfdbfe;}"
    ".st-key-merge_all_btn button:hover{background-color:#bbf7d0;}"
    ".st-key-backup_data_btn button:hover{background-color:#fef08a;}"
    ".st-key-clear_rt_btn button:hover{background-color:#fed7aa;}"
    ".st-key-clear_inv_btn button:hover{background-color:#fecaca;}"
    ".st-key-clear_all_btn button:hover{background-color:#ddd6fe;}"
    "</style>"
)

WATCHLIST_FILE = Path(
    os.environ.get("WATCHLIST_FILE", Path(__file__).with_name("watchlist.json"))
)


def normalize_code(raw: str) -> str:
    m = re.search(r"\d{6}", str(raw))
    return m.group(0) if m else ""


def with_prefix(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("0", "2", "3")):
        return "sz" + code
    return "bj" + code


def to_float(value):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def color_of(value) -> str:
    if value is None:
        return FLAT_COLOR
    return UP_COLOR if value > 0 else DOWN_COLOR if value < 0 else FLAT_COLOR


def is_trading_time(now: dt.datetime | None = None) -> bool:
    """A 股交易时段（工作日 09:30-11:30、13:00-15:00）。"""
    now = now or dt.datetime.now()
    if now.weekday() >= 5:
        return False
    clock = now.time()
    return (
        (dt.time(9, 30) <= clock <= dt.time(11, 30))
        or (dt.time(13, 0) <= clock <= dt.time(15, 0))
    )


def parse_cn_amount(value):
    """解析「1.15亿」「9945.84万」「-3094.94万」这类中文金额为元。"""
    text = str(value).strip()
    if text in ("", "--", "nan", "None"):
        return None
    mult = 1.0
    if text.endswith("亿"):
        mult, text = 1e8, text[:-1]
    elif text.endswith("万"):
        mult, text = 1e4, text[:-1]
    try:
        return float(text) * mult
    except ValueError:
        return None


def fmt_amount(value, signed: bool = False) -> str:
    if value is None:
        return "--"
    prefix = "-" if value < 0 else ("+" if signed and value > 0 else "")
    amount = abs(value)
    if amount >= 1e8:
        body = f"{amount / 1e8:.2f}亿"
    elif amount >= 1e4:
        body = f"{amount / 1e4:.0f}万"
    else:
        body = f"{amount:.0f}"
    return prefix + body


def name_initials(name: str) -> str:
    py = "".join(lazy_pinyin(str(name), style=Style.FIRST_LETTER)).lower()
    return re.sub(r"[^a-z0-9]", "", py)


# ---------------------------------------------------------------- 自选持久化

def load_watchlist() -> list[str]:
    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    codes = []
    for raw in data:
        code = normalize_code(raw)
        if code and code not in codes:
            codes.append(code)
    return codes


def save_watchlist(codes: list[str]) -> None:
    try:
        WATCHLIST_FILE.write_text(
            json.dumps(codes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        st.warning(f"自选列表保存失败：{exc}")


def add_log(message: str, level: str = "info", color: str | None = None) -> None:
    """记录系统日志。量化算法产生信号后可直接调用此函数写入提示，color 指定标识色。"""
    logs = st.session_state.setdefault("system_logs", [])
    entry = {
        "time": dt.datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "message": message,
    }
    if color:
        entry["color"] = color
    logs.append(entry)
    del logs[:-LOG_MAX_ENTRIES]


def add_to_watchlist(code: str) -> None:
    if code not in st.session_state.watchlist:
        st.session_state.watchlist.append(code)
        save_watchlist(st.session_state.watchlist)
        add_log(f"添加自选 {code}", "info")


def remove_from_watchlist(code: str) -> None:
    st.session_state.watchlist = [
        c for c in st.session_state.watchlist if c != code
    ]
    save_watchlist(st.session_state.watchlist)
    add_log(f"移除自选 {code}", "info")


# ---------------------------------------------------------------- 数据接口

@st.cache_data(ttl=10, show_spinner=False)
def get_index_spot() -> pd.DataFrame:
    """大盘指数实时行情，优先新浪，失败则回退东方财富。"""
    try:
        df = ak.stock_zh_index_spot_sina().copy()
        df["代码"] = df["代码"].map(normalize_code)
    except Exception:
        frames = []
        for series in ("上证系列指数", "深证系列指数", "中证系列指数"):
            try:
                frames.append(ak.stock_zh_index_spot_em(symbol=series))
            except Exception:
                continue
        if not frames:
            raise
        df = pd.concat(frames, ignore_index=True)
        df["代码"] = df["代码"].astype(str).map(normalize_code)

    for col in ("最新价", "涨跌额", "涨跌幅", "成交量", "成交额"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def get_a_spot() -> pd.DataFrame:
    """沪深京 A 股实时快照（含名称、最新价、昨收、涨跌幅）。"""
    df = ak.stock_zh_a_spot().copy()
    df["代码"] = df["代码"].astype(str).map(normalize_code)
    for col in ("最新价", "涨跌额", "涨跌幅", "昨收", "今开", "最高", "最低"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=180, show_spinner=False)
def get_fund_flow() -> pd.DataFrame:
    """同花顺个股资金流（即时）：换手率、流入/流出/净额，单位统一为元。"""
    df = ak.stock_fund_flow_individual(symbol="即时").copy()
    df = df.rename(columns={"股票代码": "代码"})
    df["代码"] = df["代码"].astype(str).str.zfill(6).map(normalize_code)
    df["换手率"] = pd.to_numeric(
        df["换手率"].astype(str).str.rstrip("%"), errors="coerce"
    )
    df["涨跌幅"] = pd.to_numeric(
        df["涨跌幅"].astype(str).str.rstrip("%"), errors="coerce"
    )
    for col in ("流入资金", "流出资金", "净额", "成交额"):
        df[col] = df[col].map(parse_cn_amount)
    return df[["代码", "换手率", "涨跌幅", "流入资金", "流出资金", "净额", "成交额"]]


@st.cache_data(ttl=180, show_spinner=False)
def get_hsgt_summary() -> pd.DataFrame:
    """沪深港通资金流汇总（东财 datacenter）：南北向净买额与涨跌家数。"""
    df = ak.stock_hsgt_fund_flow_summary_em().copy()
    for col in ("成交净买额", "资金净流入", "上涨数", "持平数", "下跌数", "指数涨跌幅"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def collect_main_fund_tiers():
    """东方财富大盘分级资金流（主力/超大单/大单/中单/小单），限流时静默降级。"""
    last_fail = st.session_state.get("em_flow_fail_ts", 0.0)
    if time.time() - last_fail < 600:
        return None
    try:
        df = ak.stock_market_fund_flow()
        last = df.iloc[-1]
        mapping = {
            "主力净流入-净额": "主力",
            "超大单净流入-净额": "超大单",
            "大单净流入-净额": "大单",
            "中单净流入-净额": "中单",
            "小单净流入-净额": "小单",
        }
        tiers = {label: float(last[col]) for col, label in mapping.items() if col in df.columns}
        return tiers or None
    except Exception:
        st.session_state["em_flow_fail_ts"] = time.time()
        return None


@st.cache_data(ttl=60, show_spinner=False)
def get_global_index_spot() -> pd.DataFrame:
    """美股/日韩台主要指数，按最新两根日线计算涨跌。"""
    rows = []
    for kind, symbol, code, label in GLOBAL_INDEXES:
        try:
            if kind == "us":
                df = ak.index_us_stock_sina(symbol=symbol)
            else:
                df = ak.index_global_hist_sina(symbol=symbol)
            if df is None or len(df) < 2:
                continue
            close = float(df["close"].iloc[-1])
            prev_close = float(df["close"].iloc[-2])
            change = close - prev_close
            pct = change / prev_close * 100 if prev_close else float("nan")
            rows.append({
                "代码": code, "名称": label,
                "最新价": close, "涨跌额": change, "涨跌幅": pct,
            })
        except Exception:
            continue
    return pd.DataFrame(rows)


@st.cache_data(ttl=60, show_spinner=False)
def get_commodity_spot() -> pd.DataFrame:
    """COMEX/NYMEX 主要物资实时行情（黄金、白银、原油、天然气、铜）。"""
    df = ak.futures_foreign_commodity_realtime(
        symbol=[symbol for symbol, _, _ in COMMODITIES]
    )
    if df is None or df.empty:
        return pd.DataFrame()
    for col in ("最新价", "涨跌额", "涨跌幅"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    rows = []
    for symbol, keyword, label in COMMODITIES:
        match = df[df["名称"].astype(str).str.contains(keyword, regex=False)]
        if match.empty:
            continue
        r = match.iloc[0]
        rows.append({
            "代码": symbol, "名称": label,
            "最新价": to_float(r.get("最新价")),
            "涨跌额": to_float(r.get("涨跌额")),
            "涨跌幅": to_float(r.get("涨跌幅")),
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=86400, show_spinner=False)
def get_stock_index() -> pd.DataFrame:
    """代码 / 名称 / 名称拼音首字母，用于自选搜索。"""
    spot = get_a_spot()[["代码", "名称"]].copy()
    spot["首字母"] = spot["名称"].map(name_initials)
    return spot


@st.cache_data(ttl=60, show_spinner=False)
def get_daily(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    df = ak.stock_zh_a_daily(
        symbol=with_prefix(symbol),
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        "date": "日期", "open": "开盘", "high": "最高",
        "low": "最低", "close": "收盘", "volume": "成交量", "amount": "成交额",
    })
    df["日期"] = pd.to_datetime(df["日期"])
    for col in ("开盘", "收盘", "最高", "最低", "成交量", "成交额"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额"]]


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"开盘": "first", "最高": "max", "最低": "min",
           "收盘": "last", "成交量": "sum", "成交额": "sum"}
    indexed = df.set_index("日期").sort_index()
    grouped = indexed.resample(rule)
    out = grouped.agg(agg).dropna(subset=["开盘", "收盘"])
    last_day = grouped["收盘"].apply(lambda s: s.index.max() if len(s) else pd.NaT)
    out.index = pd.DatetimeIndex(last_day.reindex(out.index).values, name="日期")
    return out.reset_index()


@st.cache_data(ttl=60, show_spinner=False)
def get_kline(symbol: str, period: str, start_date: str, end_date: str) -> pd.DataFrame:
    daily = get_daily(symbol, start_date, end_date)
    if daily.empty or period == "daily":
        return daily
    return _resample(daily, "W-FRI" if period == "weekly" else "ME")


@st.cache_data(ttl=15, show_spinner=False)
def get_intraday(symbol: str) -> pd.DataFrame:
    """最近一个交易日的 1 分钟分时数据。"""
    df = ak.stock_zh_a_minute(symbol=with_prefix(symbol), period="1", adjust="")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"day": "时间", "open": "开盘", "high": "最高",
                            "low": "最低", "close": "收盘", "volume": "成交量",
                            "amount": "成交额"})
    df["时间"] = pd.to_datetime(df["时间"])
    df = df[df["时间"].dt.date == df["时间"].dt.date.max()].reset_index(drop=True)
    for col in ("开盘", "收盘", "最高", "最低", "成交量", "成交额"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=300, show_spinner=False)
def get_minute_bars_for_date(code: str, day: str) -> pd.DataFrame:
    """指定交易日的 1 分钟数据（新浪仅保留最近约 5 个交易日）。"""
    df = ak.stock_zh_a_minute(symbol=with_prefix(code), period="1", adjust="")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"day": "时间", "open": "开盘", "high": "最高",
                            "low": "最低", "close": "收盘", "volume": "成交量",
                            "amount": "成交额"})
    df["时间"] = pd.to_datetime(df["时间"])
    df = df[df["时间"].dt.strftime("%Y-%m-%d") == day].reset_index(drop=True)
    for col in ("开盘", "收盘", "最高", "最低", "成交量", "成交额"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_kline(code: str, period_label: str, range_label: str) -> pd.DataFrame:
    days = RANGES[range_label]
    today = dt.date.today()
    start = "19900101" if days is None else (today - dt.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    return get_kline(code, PERIODS[period_label], start, end)


def resolve_stocks(query: str, index_df: pd.DataFrame) -> list[tuple[str, str]]:
    """按 6 位代码、名称首字母或名称片段匹配股票。"""
    q = query.strip()
    if not q:
        return []
    if re.fullmatch(r"\d{6}", q):
        if index_df.empty:
            return [(q, "")]
        row = index_df[index_df["代码"] == q]
        return [(q, str(row.iloc[0]["名称"]))] if not row.empty else []
    if index_df.empty:
        return []
    letters = re.sub(r"[^a-z]", "", q.lower())
    if letters:
        exact = index_df[index_df["首字母"] == letters]
        if not exact.empty:
            return list(zip(exact["代码"], exact["名称"].astype(str)))
        prefix = index_df[index_df["首字母"].str.startswith(letters)]
        if not prefix.empty:
            return list(zip(prefix["代码"], prefix["名称"].astype(str)))
    hit = index_df[index_df["名称"].astype(str).str.contains(q, regex=False)]
    return list(zip(hit["代码"], hit["名称"].astype(str)))


# ---------------------------------------------------------------- 图表

def make_kline_fig(df: pd.DataFrame, title: str = "", height: int = 620,
                   compact: bool = False, unit: str = "日") -> go.Figure:
    df = df.copy()
    for n in MA_PERIODS:
        df[f"MA{n}"] = df["收盘"].rolling(n).mean()

    vol_colors = np.where(df["收盘"] >= df["开盘"], UP_COLOR, DOWN_COLOR)
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.75, 0.25], vertical_spacing=0.03,
    )
    fig.add_trace(
        go.Candlestick(
            x=df["日期"], open=df["开盘"], high=df["最高"],
            low=df["最低"], close=df["收盘"], name="K线",
            increasing_line_color=UP_COLOR, increasing_fillcolor=UP_COLOR,
            decreasing_line_color=DOWN_COLOR, decreasing_fillcolor=DOWN_COLOR,
        ),
        row=1, col=1,
    )
    for n in MA_PERIODS:
        fig.add_trace(
            go.Scatter(
                x=df["日期"], y=df[f"MA{n}"], name=f"{n}{unit}",
                mode="lines", line=dict(color=MA_COLORS[n], width=1.1),
            ),
            row=1, col=1,
        )
    fig.add_trace(
        go.Bar(x=df["日期"], y=df["成交量"], name="成交量", marker_color=vol_colors),
        row=2, col=1,
    )
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=4, r=4, t=10, b=4) if compact else dict(l=10, r=10, t=50, b=10),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
    )
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    if compact:
        fig.update_layout(showlegend=False)
        fig.update_xaxes(nticks=4)
        fig.update_yaxes(showticklabels=False, row=1, col=1)
        fig.update_yaxes(showticklabels=False, row=2, col=1)
    else:
        fig.update_layout(
            title=dict(text=title, x=0, xanchor="left"),
            legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        )
        fig.update_yaxes(title_text="价格", row=1, col=1)
        fig.update_yaxes(title_text="成交量", row=2, col=1)
    return fig


def make_intraday_fig(df: pd.DataFrame, prev_close, title: str = "",
                      height: int = 620, compact: bool = False) -> go.Figure:
    price = df["收盘"]
    volume = df["成交量"].fillna(0)
    cum_vol = volume.cumsum().replace(0, np.nan)
    avg = (price * volume).cumsum() / cum_vol
    if avg.isna().all():
        avg = price.expanding().mean()

    bar_colors = np.where(
        price >= price.shift(1).fillna(price.iloc[0]), UP_COLOR, DOWN_COLOR
    )
    labels = df["时间"].dt.strftime("%H:%M")

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.72, 0.28], vertical_spacing=0.04,
    )
    fig.add_trace(
        go.Scatter(x=labels, y=price, name="价格", line=dict(color="#1f77b4", width=1.6)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=labels, y=avg, name="均价", line=dict(color="#ff9800", width=1.2)),
        row=1, col=1,
    )
    if prev_close:
        fig.add_hline(
            y=prev_close, line=dict(color="#999", width=1, dash="dash"),
            annotation_text=f"昨收 {prev_close:.2f}" if not compact else None,
            annotation_position="top left",
            row=1, col=1,
        )
    fig.add_trace(
        go.Bar(x=labels, y=volume, name="成交量", marker_color=bar_colors),
        row=2, col=1,
    )
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=4, r=4, t=10, b=4) if compact else dict(l=10, r=10, t=50, b=10),
        hovermode="x unified",
    )
    fig.update_xaxes(type="category", nticks=6 if compact else 12, row=2, col=1)
    if compact:
        fig.update_layout(showlegend=False)
        fig.update_yaxes(showticklabels=False, row=1, col=1)
        fig.update_yaxes(showticklabels=False, row=2, col=1)
    else:
        fig.update_layout(
            title=dict(text=title, x=0, xanchor="left"),
            legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        )
        fig.update_yaxes(title_text="价格", row=1, col=1)
        fig.update_yaxes(title_text="成交量", row=2, col=1)
    return fig


@st.dialog("图表放大", width="large")
def show_enlarged(code: str, name: str, chart_type: str,
                  period_label: str, range_label: str, prev_close=None) -> None:
    heading = f"{name}（{code}）" if name else code
    try:
        if chart_type == "分时":
            df = get_intraday(code)
            if df.empty:
                st.info("暂无分时数据（可能为非交易日或停牌）。")
                return
            day = df["时间"].dt.strftime("%Y-%m-%d").iloc[-1]
            fig = make_intraday_fig(df, prev_close, f"{heading}　分时 {day}", height=640)
        else:
            df = load_kline(code, period_label, range_label)
            if df.empty:
                st.info("暂无 K 线数据。")
                return
            fig = make_kline_fig(
                df, f"{heading}　{period_label}", height=640,
                unit=PERIOD_UNITS[period_label],
            )
    except Exception as exc:
        st.error(f"图表数据获取失败：{exc}")
        return
    st.plotly_chart(fig, width="stretch", key=f"big_{chart_type}_{code}")


# ---------------------------------------------------------------- 页面

def render_index_cards(spot: pd.DataFrame, indexes) -> None:
    cols = st.columns(len(indexes))
    for col, (code, label) in zip(cols, indexes):
        row = spot[spot["代码"] == code]
        with col:
            if row.empty:
                col.markdown(f"**{label}**\n\n暂无数据")
                continue
            r = row.iloc[0]
            price = to_float(r.get("最新价"))
            chg = to_float(r.get("涨跌额"))
            pct = to_float(r.get("涨跌幅"))
            color = color_of(chg)
            price_txt = f"{price:,.2f}" if price is not None else "--"
            chg_txt = f"{chg:+.2f}" if chg is not None else "--"
            pct_txt = f"{pct:+.2f}%" if pct is not None else "--"
            col.markdown(
                f"""<div style="border:1px solid #e6e6e6;border-radius:12px;padding:12px 14px;background:#fff">
<div style="color:#555;font-size:0.92rem">{label} <span style="color:#999">{code}</span></div>
<div style="color:{color};font-size:1.55rem;font-weight:700;line-height:1.4">{price_txt}</div>
<div style="color:{color};font-size:0.92rem">{chg_txt}　{pct_txt}</div>
</div>""",
                unsafe_allow_html=True,
            )


def ma_legend_html(unit: str) -> str:
    items = "".join(
        f"<span style='color:{MA_COLORS[n]}'>●</span>"
        f"<span style='color:#666;margin:0 10px 0 3px'>{n}{unit}</span>"
        for n in MA_PERIODS
    )
    return f"<div style='font-size:0.72rem;line-height:1.4;margin-left:2px'>{items}</div>"


def render_watch_card(col, code: str, spot: pd.DataFrame, flow: pd.DataFrame,
                      chart_df: pd.DataFrame, chart_type: str,
                      period_label: str, range_label: str) -> None:
    row = spot[spot["代码"] == code] if not spot.empty else spot.iloc[0:0]
    name = str(row.iloc[0]["名称"]) if not row.empty else ""
    price = to_float(row.iloc[0].get("最新价")) if not row.empty else None
    chg = to_float(row.iloc[0].get("涨跌额")) if not row.empty else None
    pct = to_float(row.iloc[0].get("涨跌幅")) if not row.empty else None
    prev_close = to_float(row.iloc[0].get("昨收")) if not row.empty else None

    flow_row = flow[flow["代码"] == code] if not flow.empty else flow.iloc[0:0]
    turnover = to_float(flow_row.iloc[0].get("换手率")) if not flow_row.empty else None
    inflow = to_float(flow_row.iloc[0].get("流入资金")) if not flow_row.empty else None
    outflow = to_float(flow_row.iloc[0].get("流出资金")) if not flow_row.empty else None
    net = to_float(flow_row.iloc[0].get("净额")) if not flow_row.empty else None

    with col:
        with st.container(border=True):
            color = color_of(chg)
            price_txt = f"{price:.2f}" if price is not None else "--"
            pct_txt = f"{pct:+.2f}%" if pct is not None else "--"
            turnover_txt = f"{turnover:.2f}%" if turnover is not None else "--"
            net_color = color_of(net)
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
                f"<span style='font-weight:600'>{name or '未知股票'}</span>"
                f"<span style='color:#999;font-size:0.8rem'>{code}</span></div>"
                f"<div style='color:{color};font-size:1.1rem;font-weight:600'>"
                f"{price_txt}　{pct_txt}</div>"
                f"<div style='font-size:0.75rem;color:#666;margin-top:2px'>"
                f"换手率 <span style='color:#333'>{turnover_txt}</span>"
                f"<span style='margin:0 8px;color:#ddd'>|</span>"
                f"净流入 <span style='color:{net_color};font-weight:600'>"
                f"{fmt_amount(net, signed=True)}</span></div>"
                f"<div style='font-size:0.72rem;color:#888;margin-top:1px'>"
                f"流入 <span style='color:{UP_COLOR}'>{fmt_amount(inflow)}</span>"
                f"　流出 <span style='color:{DOWN_COLOR}'>{fmt_amount(outflow)}</span></div>",
                unsafe_allow_html=True,
            )
            fig = None
            try:
                if chart_df is not None and not chart_df.empty:
                    if chart_type == "分时":
                        fig = make_intraday_fig(chart_df, prev_close, height=280, compact=True)
                    else:
                        fig = make_kline_fig(
                            chart_df, height=280, compact=True,
                            unit=PERIOD_UNITS[period_label],
                        )
            except Exception as exc:
                st.caption(f"图表绘制失败：{exc}")
            if fig is None:
                st.caption("暂无数据")
            else:
                st.plotly_chart(
                    fig, width="stretch", key=f"mini_{code}",
                    config={"displayModeBar": False},
                )
                if chart_type == "K线":
                    st.markdown(
                        ma_legend_html(PERIOD_UNITS[period_label]),
                        unsafe_allow_html=True,
                    )

            b1, b2 = st.columns(2)
            if b1.button("放大", key=f"zoom_{code}", width="stretch"):
                show_enlarged(code, name, chart_type, period_label, range_label, prev_close)
            if b2.button("删除", key=f"del_{code}", width="stretch"):
                remove_from_watchlist(code)
                st.rerun()


def reorder_codes(sorted_labels, labels, label_to_code, current_codes):
    """应用拖拽结果：只有标签集合完全一致时才生效，避免增删股票时误覆盖。"""
    if not sorted_labels or set(sorted_labels) != set(labels):
        return current_codes
    try:
        return [label_to_code[lab] for lab in sorted_labels]
    except KeyError:
        return current_codes


def apply_sort_order(stock_index: pd.DataFrame) -> None:
    codes = st.session_state.watchlist
    if len(codes) < 2:
        return
    name_of = dict(zip(stock_index["代码"], stock_index["名称"])) if not stock_index.empty else {}
    label_of = {c: f"{name_of.get(c) or c}　{c}" for c in codes}
    label_to_code = {lab: c for c, lab in label_of.items()}
    labels = [label_of[c] for c in codes]
    sorted_labels = sort_items(
        labels,
        direction="horizontal",
        custom_style=(
            ".sortable-container-body{display:flex;flex-wrap:wrap;gap:8px;"
            "background:transparent;padding:0;min-height:auto}"
            ".sortable-item,.sortable-item:hover,.sortable-item:active{"
            "border-radius:10px;padding:6px 10px;margin:0;"
            "width:calc((100% - 16px)/3);box-sizing:border-box;text-align:center;"
            "font-size:0.9rem;background:#eef1f6;color:#333;white-space:nowrap;"
            "box-shadow:none}"
        ),
        key="watch_sort",
    )
    new_codes = reorder_codes(sorted_labels, labels, label_to_code, codes)
    if new_codes != codes:
        st.session_state.watchlist = new_codes
        save_watchlist(new_codes)
        add_log("调整自选看板顺序", "info")


def compute_market_breadth(spot: pd.DataFrame):
    """统计 A 股上涨/下跌/平盘家数（剔除停牌，即成交量为 0 的股票）。"""
    if spot is None or spot.empty or "涨跌幅" not in spot.columns:
        return None
    pct = pd.to_numeric(spot["涨跌幅"], errors="coerce")
    mask = pct.notna()
    if "成交量" in spot.columns:
        mask &= pd.to_numeric(spot["成交量"], errors="coerce").fillna(0) > 0
    pct = pct[mask]
    if pct.empty:
        return None
    return {
        "up": int((pct > 0).sum()),
        "down": int((pct < 0).sum()),
        "flat": int((pct == 0).sum()),
        "total": int(pct.size),
    }


def compute_breadth_from_flow(flow: pd.DataFrame):
    """按同花顺沪深 A 股列表统计涨跌家数（口径与同花顺等软件一致，不含北交所）。"""
    if flow is None or flow.empty or "涨跌幅" not in flow.columns:
        return None
    pct = pd.to_numeric(flow["涨跌幅"], errors="coerce").dropna()
    if pct.empty:
        return None
    return {
        "up": int((pct > 0).sum()),
        "down": int((pct < 0).sum()),
        "flat": int((pct == 0).sum()),
        "total": int(pct.size),
    }


@st.cache_data(ttl=30, show_spinner=False)
def get_hs_breadth_legu():
    """乐咕乐股-沪深 A 股涨跌家数（软件常用口径），停牌计入平盘。"""
    df = ak.stock_market_activity_legu()
    data = {str(row["item"]).strip(): to_float(row["value"]) for _, row in df.iterrows()}
    up = int(data.get("上涨") or 0)
    down = int(data.get("下跌") or 0)
    flat = int(data.get("平盘") or 0)
    suspended = int(data.get("停牌") or 0)
    total = up + down + flat + suspended
    if total <= 0:
        return None
    return {
        "up": up, "down": down, "flat": flat + suspended,
        "suspended": suspended, "total": total,
    }


def get_bj_breadth():
    """新浪快照中的北交所涨跌家数与成交额。"""
    spot = get_a_spot()
    if spot is None or spot.empty:
        return None
    codes = spot["代码"].astype(str)
    bj = spot[codes.str.startswith(("4", "8", "920"))]
    if bj.empty:
        return None
    pct = pd.to_numeric(bj["涨跌幅"], errors="coerce")
    amount = pd.to_numeric(
        bj.get("成交额", pd.Series(dtype=float)), errors="coerce"
    )
    valid = pct.notna()
    if not valid.any():
        return None
    return {
        "up": int((pct > 0).sum()),
        "down": int((pct < 0).sum()),
        "flat": int((pct == 0).sum()),
        "total": int(valid.sum()),
        "amount": float(amount[valid].fillna(0).sum()),
    }


def collect_index_snapshot() -> dict:
    errors = []
    try:
        index_spot = get_index_spot()
    except Exception as exc:
        index_spot = pd.DataFrame()
        errors.append(f"大盘指数获取失败：{exc}")
    try:
        global_spot = get_global_index_spot()
    except Exception as exc:
        global_spot = pd.DataFrame()
        errors.append(f"全球指数获取失败：{exc}")
    try:
        commodity_spot = get_commodity_spot()
    except Exception as exc:
        commodity_spot = pd.DataFrame()
        errors.append(f"主要物资行情获取失败：{exc}")

    # 两市成交额优先取指数口径（上证指数 + 深证综指），与交易软件一致
    market_amount = None
    market_amount_sh = None
    market_amount_sz = None

    def _index_amount(code: str):
        if index_spot is None or index_spot.empty or "成交额" not in index_spot.columns:
            return None
        row = index_spot[index_spot["代码"] == code]
        if row.empty:
            return None
        value = to_float(row.iloc[0].get("成交额"))
        return value if value and value > 0 else None

    market_amount_sh = _index_amount("000001")
    market_amount_sz = _index_amount("399106") or _index_amount("399001")
    if market_amount_sh and market_amount_sz:
        market_amount = market_amount_sh + market_amount_sz

    bj_breadth = None
    try:
        bj_breadth = get_bj_breadth()
    except Exception as exc:
        errors.append(f"北交所涨跌家数获取失败：{exc}")
    market_amount_bj = bj_breadth["amount"] if bj_breadth else None
    if market_amount is not None and market_amount_bj:
        market_amount += market_amount_bj

    breadth = None
    flow_agg = None
    breadth_source = "沪深A股"

    def _combine_bj(base: dict) -> dict:
        if not bj_breadth:
            return base
        return {
            "up": base["up"] + bj_breadth["up"],
            "down": base["down"] + bj_breadth["down"],
            "flat": base["flat"] + bj_breadth["flat"],
            "total": base["total"] + bj_breadth["total"],
        }

    hs_breadth = None
    try:
        hs_breadth = get_hs_breadth_legu()
    except Exception as exc:
        errors.append(f"沪深涨跌家数获取失败：{exc}")
    if hs_breadth:
        breadth = _combine_bj(hs_breadth)
        breadth_source = "沪深京·含北交所" if bj_breadth else "沪深A股"

    try:
        fund_flow = get_fund_flow()
        if not fund_flow.empty:
            flow_agg = {
                "inflow": float(pd.to_numeric(fund_flow["流入资金"], errors="coerce").fillna(0).sum()),
                "outflow": float(pd.to_numeric(fund_flow["流出资金"], errors="coerce").fillna(0).sum()),
                "net": float(pd.to_numeric(fund_flow["净额"], errors="coerce").fillna(0).sum()),
            }
            if breadth is None:
                ths_breadth = compute_breadth_from_flow(fund_flow)
                if ths_breadth:
                    breadth = _combine_bj(ths_breadth)
                    breadth_source = (
                        "沪深京·同花顺+北交所" if bj_breadth else "沪深A股·同花顺"
                    )
            if market_amount is None:  # 指数成交额不可用时回退为个股成交额加总
                amounts = pd.to_numeric(fund_flow["成交额"], errors="coerce").fillna(0)
                amount_sum = float(amounts.sum())
                market_amount = amount_sum if amount_sum > 0 else None
                flow_codes = fund_flow["代码"].astype(str)
                sh_amount = float(amounts[flow_codes.str.startswith("6")].sum())
                if sh_amount > 0:
                    market_amount_sh = sh_amount
                    sz_amount = amount_sum - sh_amount
                    market_amount_sz = sz_amount if sz_amount > 0 else None
                if market_amount is not None and market_amount_bj:
                    market_amount += market_amount_bj
    except Exception as exc:
        errors.append(f"市场资金流获取失败：{exc}")

    if breadth is None:  # 最后回退新浪全市场口径（已含北交所）
        breadth_source = "沪深京·新浪"
        try:
            a_spot = get_a_spot()
            breadth = compute_market_breadth(a_spot)
        except Exception as exc:
            errors.append(f"A股涨跌家数获取失败：{exc}")

    hsgt = None
    try:
        summary = get_hsgt_summary()
        if not summary.empty:
            north = summary[summary["资金方向"] == "北向"]
            south = summary[summary["资金方向"] == "南向"]
            # 汇总接口的净买额单位为亿元，统一换算为元
            north_net = float(north["成交净买额"].fillna(0).sum()) * 1e8
            south_net = float(south["成交净买额"].fillna(0).sum()) * 1e8
            hsgt = {
                "north_net": north_net,
                "north_disclosed": north_net != 0,
                "south_net": south_net,
            }
    except Exception as exc:
        errors.append(f"沪深港通资金流获取失败：{exc}")

    main_flow = collect_main_fund_tiers()

    return {
        "index_spot": index_spot,
        "global_spot": global_spot,
        "commodity_spot": commodity_spot,
        "breadth": breadth,
        "breadth_source": breadth_source,
        "market_amount": market_amount,
        "market_amount_sh": market_amount_sh,
        "market_amount_sz": market_amount_sz,
        "market_amount_bj": market_amount_bj,
        "flow_agg": flow_agg,
        "hsgt": hsgt,
        "main_flow": main_flow,
        "updated": dt.datetime.now(),
        "errors": errors,
    }


def collect_watch_snapshot(chart_type: str, period_label: str, range_label: str) -> dict:
    codes = tuple(st.session_state.get("watchlist", []))
    errors = []
    spot = pd.DataFrame()
    flow = pd.DataFrame()
    charts: dict[str, pd.DataFrame] = {}
    if codes:
        try:
            spot = get_a_spot()
        except Exception as exc:
            errors.append(f"个股快照获取失败：{exc}")
        try:
            flow = get_fund_flow()
        except Exception as exc:
            errors.append(f"资金流数据获取失败：{exc}")
        for code in codes:
            try:
                if chart_type == "分时":
                    charts[code] = get_intraday(code)
                else:
                    charts[code] = load_kline(code, period_label, range_label)
            except Exception as exc:
                charts[code] = pd.DataFrame()
                errors.append(f"{code} 图表获取失败：{exc}")
    return {
        "key": (codes, chart_type, period_label, range_label),
        "spot": spot,
        "flow": flow,
        "charts": charts,
        "updated": dt.datetime.now(),
        "errors": errors,
    }


def ensure_watch_snapshot(chart_type: str, period_label: str, range_label: str) -> None:
    key = (
        tuple(st.session_state.get("watchlist", [])),
        chart_type, period_label, range_label,
    )
    snapshot = st.session_state.get("watch_snapshot")
    if snapshot is None or snapshot.get("key") != key:
        st.session_state["watch_snapshot"] = collect_watch_snapshot(
            chart_type, period_label, range_label
        )


def run_enabled_strategies(snapshot: dict) -> None:
    """对已勾选的策略实时计算自选股信号，命中后写入系统日志（带策略专属颜色）。

    仅在 A 股交易时段内产生实时信号；收盘后不再生成“实时”交易记录（历史信号
    请使用「盘点数据」回放）。
    """
    if not is_trading_time():
        return
    codes = list(st.session_state.get("watchlist", []))
    if not codes:
        return
    spot = snapshot.get("spot", pd.DataFrame())
    names = {}
    if not spot.empty:
        names = dict(zip(spot["代码"].astype(str), spot["名称"].astype(str)))
    seen = st.session_state.setdefault("strategy_signal_keys", set())
    trade_entries = []

    for strategy, signal_fn in STRATEGY_RUNNERS.items():
        try:
            idx = QUANT_STRATEGIES.index(strategy)
        except ValueError:
            continue
        if not st.session_state.get(f"strategy_{idx}"):
            continue
        color = strategies.STRATEGY_COLORS.get(strategy)
        for code in codes:
            try:
                daily = load_kline(code, "日K", "6个月")
                action = signal_fn(daily)
            except Exception:
                continue
            if action is None or daily.empty:
                continue
            bar_date = pd.to_datetime(daily["日期"].iloc[-1]).strftime("%Y-%m-%d")
            signal_key = f"{strategy}-{code}-{action}-{bar_date}"
            if signal_key in seen:
                continue
            seen.add(signal_key)
            price = to_float(daily["收盘"].iloc[-1])
            price_txt = f"{price:.2f}" if price is not None else "--"
            now_dt = dt.datetime.now()
            now = now_dt.strftime("%H:%M:%S")
            trade_date = now_dt.strftime("%Y-%m-%d")
            stock_name = names.get(code, code)
            add_log(
                strategies.format_signal_log(
                    strategy, stock_name, action, price_txt,
                    SIGNAL_QUANTITY, now, trade_date, SOURCE_LIVE,
                ),
                level="trade",
                color=color,
            )
            if price is not None:
                trade_entries.append({
                    "strategy": strategy,
                    "stock": stock_name,
                    "action": action,
                    "price": price,
                    "quantity": SIGNAL_QUANTITY,
                    "time": now,
                    "date": trade_date,
                    "source": SOURCE_LIVE,
                })

    if trade_entries:
        try:
            excel_log.append_trade_logs(trade_entries, dedupe=True)
        except Exception as exc:
            add_log(f"Excel 交易日志写入失败：{exc}", "debug")
        try:
            db_log.append_trade_logs(trade_entries, dedupe=True)
        except Exception as exc:
            add_log(f"SQLite 交易日志写入失败：{exc}", "debug")


def load_kline_before(code: str, trade_day: dt.date) -> pd.DataFrame:
    """取所选日期之前（不含当日）的日 K，模拟当日盘中实时可用的行情。

    前复权，约 9 个月窗口供长周期策略使用。
    """
    cutoff = trade_day - dt.timedelta(days=1)
    start = (cutoff - dt.timedelta(days=270)).strftime("%Y%m%d")
    end = cutoff.strftime("%Y%m%d")
    return get_kline(code, "daily", start, end)


def _add_replay_entry(strategy, color, stock_name, code, action, price, when,
                      trade_date, logged, entries) -> None:
    log_key = f"{strategy}-{code}-{action}-{trade_date}-{SOURCE_REPLAY}"
    if log_key not in logged:
        logged.add(log_key)
        add_log(
            strategies.format_signal_log(
                strategy, stock_name, action, f"{price:.2f}",
                SIGNAL_QUANTITY, when, trade_date, SOURCE_REPLAY,
            ),
            level="trade",
            color=color,
        )
    entries.append({
        "strategy": strategy,
        "stock": stock_name,
        "action": action,
        "price": price,
        "quantity": SIGNAL_QUANTITY,
        "time": when,
        "date": trade_date,
        "source": SOURCE_REPLAY,
    })


def run_replay(target_date: dt.date) -> dict:
    """按所选日期，用已勾选策略分钟级回放自选股信号，并写入日志/Excel/数据库。

    历史为当日之前的日 K；叠加所选交易日的 1 分钟数据逐分钟演进当日 K 线，
    首次满足买卖条件的分钟即为交易时间（如 14:55:11），价格取该分钟收盘价。
    无分钟数据（历史日期）时回退为日线信号，交易时间记为 15:00:00。
    """
    result = {"computed": 0, "excel": 0, "db": 0, "data_date": None,
              "minute": 0, "fallback": 0, "temp_excel": None, "temp_db": None}
    codes = list(st.session_state.get("watchlist", []))
    if not codes:
        return result
    # 每次盘点前删除旧 temp，并创建新的暂存文件
    storage_backup.delete_temp_files()
    temp = storage_backup.create_temp_files()
    result["temp_excel"] = temp["excel"]
    result["temp_db"] = temp["db"]
    snapshot = st.session_state.get("watch_snapshot", {})
    spot = snapshot.get("spot", pd.DataFrame())
    names = (
        dict(zip(spot["代码"].astype(str), spot["名称"].astype(str)))
        if not spot.empty else {}
    )
    logged = st.session_state.setdefault("replay_logged_keys", set())
    entries = []
    trade_date = target_date.strftime("%Y-%m-%d")

    enabled = []
    for strategy, signal_fn in STRATEGY_RUNNERS.items():
        idx = QUANT_STRATEGIES.index(strategy)
        if st.session_state.get(f"strategy_{idx}"):
            enabled.append((strategy, signal_fn, strategies.STRATEGY_COLORS[strategy]))
    if not enabled:
        return result

    history_dates = []
    for code in codes:
        try:
            history = load_kline_before(code, target_date)
        except Exception:
            continue
        if history is None or history.empty:
            continue
        history_dates.append(
            pd.to_datetime(history["日期"].iloc[-1]).strftime("%Y-%m-%d")
        )
        stock_name = names.get(code, code)
        try:
            minutes = get_minute_bars_for_date(code, trade_date)
        except Exception:
            minutes = pd.DataFrame()

        if minutes.empty:
            # 无分钟数据：回退为日线信号，交易时间记 15:00:00
            result["fallback"] += 1
            for strategy, signal_fn, color in enabled:
                try:
                    action = signal_fn(history)
                except Exception:
                    continue
                if action is None:
                    continue
                price = to_float(history["收盘"].iloc[-1])
                if price is None:
                    continue
                _add_replay_entry(
                    strategy, color, stock_name, code, action, price,
                    "15:00:00", trade_date, logged, entries,
                )
            continue

        # 分钟级回放：历史日 K + 当日盘中演进的 K 线
        result["minute"] += 1
        day_open = float(minutes["开盘"].iloc[0])
        highs = minutes["最高"].cummax().tolist()
        lows = minutes["最低"].cummin().tolist()
        vols = minutes["成交量"].fillna(0).cumsum().tolist()
        if "成交额" in minutes.columns:
            amts = minutes["成交额"].fillna(0).cumsum().tolist()
        else:
            amts = [0.0] * len(minutes)
        closes = minutes["收盘"].tolist()
        times = minutes["时间"].dt.strftime("%H:%M:%S").tolist()

        found = {strategy: set() for strategy, _, _ in enabled}
        for i in range(len(minutes)):
            if all(len(found[s]) >= 2 for s, _, _ in enabled):
                break
            day_bar = pd.DataFrame([{
                "日期": pd.Timestamp(trade_date),
                "开盘": day_open,
                "最高": float(highs[i]),
                "最低": float(lows[i]),
                "收盘": float(closes[i]),
                "成交量": float(vols[i]),
                "成交额": float(amts[i]),
            }])
            candidate = pd.concat([history, day_bar], ignore_index=True)
            for strategy, signal_fn, color in enabled:
                if len(found[strategy]) >= 2:
                    continue
                try:
                    action = signal_fn(candidate)
                except Exception:
                    continue
                if action is None or action in found[strategy]:
                    continue
                price = to_float(closes[i])
                if price is None:
                    continue
                found[strategy].add(action)
                _add_replay_entry(
                    strategy, color, stock_name, code, action, price,
                    times[i], trade_date, logged, entries,
                )

    result["computed"] = len(entries)
    result["data_date"] = max(history_dates) if history_dates else None
    if entries:
        try:
            result["excel"] = excel_log.append_trade_logs(
                entries, dedupe=True, path=temp["excel"]
            )
        except Exception as exc:
            add_log(f"盘点暂存 Excel 写入失败：{exc}", "debug")
        try:
            result["db"] = db_log.append_trade_logs(
                entries, dedupe=True, path=temp["db"]
            )
        except Exception as exc:
            add_log(f"盘点暂存数据库写入失败：{exc}", "debug")
    return result


def _replay_result_message(stats: dict, replay_date) -> str:
    data_date = stats.get("data_date") or "--"
    if stats["computed"] == 0:
        return (
            f"盘点完成：{replay_date} 没有产生交易信号"
            f"（日线历史截至 {data_date}）。"
        )
    extra = f"（日线历史截至 {data_date}"
    if stats["fallback"]:
        extra += (
            f"，{stats['fallback']} 只股票无当日分钟数据，"
            "交易时间按 15:00:00 记录"
        )
    extra += "）"
    return (
        f"盘点完成：生成 {stats['computed']} 条信号，已写入盘点暂存文件"
        f"{extra}；可在下方「交易数据」面板合并或查看。"
    )


def refresh_snapshots() -> None:
    """不可见的定时刷新：耗时请求与可见渲染分离，避免刷新时整块区域发白卡顿。"""
    st.session_state["index_snapshot"] = collect_index_snapshot()
    params = st.session_state.get("panel_params")
    if params:
        snapshot = collect_watch_snapshot(*params)
        st.session_state["watch_snapshot"] = snapshot
        run_enabled_strategies(snapshot)


def render_market_breadth(breadth: dict, market_amount, market_amount_sh,
                          market_amount_sz, market_amount_bj, flow_agg,
                          breadth_source: str, hsgt, main_flow) -> None:
    up, down, flat = breadth["up"], breadth["down"], breadth["flat"]
    total = breadth["total"] or 1
    up_w = up / total * 100
    flat_w = flat / total * 100
    down_w = down / total * 100
    amount_txt = fmt_amount(market_amount) if market_amount else "--"
    scope = breadth_source

    flow_html = ""
    if flow_agg:
        net_color = color_of(flow_agg["net"])
        flow_html = (
            "<div style='display:flex;align-items:center;gap:18px;flex-wrap:wrap'>"
            "<span style='color:#555;font-size:0.92rem;white-space:nowrap'>市场资金</span>"
            f"<span style='color:#888;font-size:0.88rem;white-space:nowrap'>流入 "
            f"<span style='color:{UP_COLOR};font-weight:600'>{fmt_amount(flow_agg['inflow'])}</span></span>"
            f"<span style='color:#888;font-size:0.88rem;white-space:nowrap'>流出 "
            f"<span style='color:{DOWN_COLOR};font-weight:600'>{fmt_amount(flow_agg['outflow'])}</span></span>"
            f"<span style='color:#888;font-size:0.88rem;white-space:nowrap'>净流入 "
            f"<span style='color:{net_color};font-weight:600'>{fmt_amount(flow_agg['net'], signed=True)}</span></span>"
            "</div>"
        )

    parts = []
    if hsgt:
        if hsgt.get("north_disclosed"):
            parts.append(
                "<span style='color:#888;font-size:0.88rem;white-space:nowrap'>北向资金 "
                f"<span style='color:{color_of(hsgt['north_net'])};font-weight:600'>"
                f"{fmt_amount(hsgt['north_net'], signed=True)}</span></span>"
            )
        else:
            parts.append(
                "<span style='color:#888;font-size:0.88rem;white-space:nowrap'>北向资金 "
                "<span style='color:#999'>净买入已停止实时披露（2024-08 起）</span></span>"
            )
        parts.append(
            "<span style='color:#888;font-size:0.88rem;white-space:nowrap'>南向资金 "
            f"<span style='color:{color_of(hsgt['south_net'])};font-weight:600'>"
            f"{fmt_amount(hsgt['south_net'], signed=True)}</span></span>"
        )
    if main_flow:
        for name in ("主力", "超大单", "大单", "中单", "小单"):
            value = main_flow.get(name)
            if value is None:
                continue
            parts.append(
                f"<span style='color:#888;font-size:0.88rem;white-space:nowrap'>{name}净流入 "
                f"<span style='color:{color_of(value)};font-weight:600'>"
                f"{fmt_amount(value, signed=True)}</span></span>"
            )
    else:
        parts.append(
            "<span style='color:#aaa;font-size:0.82rem;white-space:nowrap'>"
            "主力分级资金暂不可用（数据源限流）</span>"
        )
    money_html = (
        "<div style='display:flex;align-items:center;gap:18px;flex-wrap:wrap'>"
        "<span style='color:#555;font-size:0.92rem;white-space:nowrap'>资金流向</span>"
        + "".join(parts) + "</div>"
    )

    amount_parts = []
    if market_amount_sh:
        amount_parts.append(
            "<span style='color:#888;font-size:0.88rem;white-space:nowrap'>沪市 "
            f"<span style='color:#333;font-weight:600'>{fmt_amount(market_amount_sh)}</span></span>"
        )
    if market_amount_sz:
        amount_parts.append(
            "<span style='color:#888;font-size:0.88rem;white-space:nowrap'>深市 "
            f"<span style='color:#333;font-weight:600'>{fmt_amount(market_amount_sz)}</span></span>"
        )
    if market_amount_bj:
        amount_parts.append(
            "<span style='color:#888;font-size:0.88rem;white-space:nowrap'>北交所 "
            f"<span style='color:#333;font-weight:600'>{fmt_amount(market_amount_bj)}</span></span>"
        )
    amount_parts.append(
        "<span style='color:#888;font-size:0.88rem;white-space:nowrap'>合计 "
        f"<span style='color:#333;font-weight:600'>{amount_txt}</span></span>"
    )
    amount_html = (
        "<div style='display:flex;align-items:center;gap:18px;flex-wrap:wrap'>"
        "<span style='color:#555;font-size:0.92rem;white-space:nowrap'>市场成交额</span>"
        + "".join(amount_parts) + "</div>"
    )

    st.markdown(
        f"""<div style="border:1px solid #e6e6e6;border-radius:12px;padding:10px 14px;background:#fff;display:flex;flex-direction:column;gap:6px;margin-top:6px">
<div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap">
<span style="color:#555;font-size:0.92rem;white-space:nowrap">沪深京A股涨跌家数
<span style="color:#bbb;font-size:0.75rem">（{scope}）</span></span>
<span style="color:{UP_COLOR};font-weight:600;white-space:nowrap">上涨 {up:,} 家</span>
<span style="color:{FLAT_COLOR};white-space:nowrap">平盘 {flat:,} 家</span>
<span style="color:{DOWN_COLOR};font-weight:600;white-space:nowrap">下跌 {down:,} 家</span>
<div style="flex:1;min-width:120px;height:8px;border-radius:999px;overflow:hidden;background:#eee;display:flex">
<div style="width:{up_w:.1f}%;background:{UP_COLOR}"></div>
<div style="width:{flat_w:.1f}%;background:{FLAT_BAR_COLOR}"></div>
<div style="width:{down_w:.1f}%;background:{DOWN_COLOR}"></div>
</div>
<span style="color:#999;font-size:0.78rem;white-space:nowrap">共 {total:,} 只</span>
</div>
{flow_html}
{money_html}
{amount_html}
</div>""",
        unsafe_allow_html=True,
    )


def render_index_panel() -> None:
    snapshot = st.session_state.get("index_snapshot", {})
    updated = snapshot.get("updated")
    stamp = f"{updated:%H:%M:%S}" if updated else "--"

    index_spot = snapshot.get("index_spot", pd.DataFrame())
    if not index_spot.empty:
        render_index_cards(index_spot, INDEXES)
        st.caption(f"大盘指数更新于 {stamp}　数据来源：AKShare")

    global_spot = snapshot.get("global_spot", pd.DataFrame())
    if not global_spot.empty:
        items = [(code, label) for _, _, code, label in GLOBAL_INDEXES]
        render_index_cards(global_spot, items)
        st.caption(
            f"全球指数更新于 {stamp}　"
            "美股为最近收盘/盘中最新价，数据来源：新浪财经"
        )

    commodity_spot = snapshot.get("commodity_spot", pd.DataFrame())
    if not commodity_spot.empty:
        items = [(symbol, label) for symbol, _, label in COMMODITIES]
        render_index_cards(commodity_spot, items)
        st.caption(
            f"主要物资价格更新于 {stamp}　"
            "COMEX/NYMEX 期货，数据来源：新浪财经"
        )

    breadth = snapshot.get("breadth")
    if breadth:
        render_market_breadth(
            breadth,
            snapshot.get("market_amount"),
            snapshot.get("market_amount_sh"),
            snapshot.get("market_amount_sz"),
            snapshot.get("market_amount_bj"),
            snapshot.get("flow_agg"),
            snapshot.get("breadth_source", "沪深京·含北交所"),
            snapshot.get("hsgt"),
            snapshot.get("main_flow"),
        )

    errors = snapshot.get("errors") or []
    if errors:
        st.warning("；".join(errors))


def render_system_log_panel(rows: int = 1) -> None:
    logs = st.session_state.get("system_logs", [])
    inner_height = max(300, rows * 463 + (rows - 1) * 16 - 92)
    with st.container(border=True, key="system_log_panel"):
        st.markdown("#### :material/terminal: 系统日志")
        st.caption("量化信号与系统事件")
        with st.container(height=inner_height, border=False):
            if not logs:
                st.caption("暂无日志")
                return
            rows = []
            for entry in reversed(logs[-60:]):
                level = entry.get("level", "info")
                strategy_color = entry.get("color") if level == "trade" else None
                dot_color = strategy_color or LOG_COLORS.get(level, "#111111")
                message = entry["message"]
                if (level == "trade" and strategy_color
                        and message.startswith("[") and "]" in message):
                    split = message.index("]") + 1
                    text_html = (
                        f"<span style='color:{strategy_color};font-weight:600'>"
                        f"{html.escape(message[:split])}</span>"
                        f"<span style='color:#111111'>{html.escape(message[split:])}</span>"
                    )
                else:
                    text_html = f"<span style='color:#111111'>{html.escape(message)}</span>"
                rows.append(
                    "<div style='display:flex;gap:8px;align-items:flex-start;"
                    "padding:6px 4px;border-bottom:1px dashed #ececec'>"
                    f"<span style='color:#9aa0a6;font-size:0.7rem;"
                    f"font-family:ui-monospace,monospace;padding-top:2px;"
                    f"white-space:nowrap'>{entry['time']}</span>"
                    f"<span style='color:{dot_color};font-size:0.72rem;"
                    f"line-height:1.5'>●</span>"
                    f"<span style='font-size:0.78rem;line-height:1.5'>{text_html}</span>"
                    "</div>"
                )
            st.markdown("".join(rows), unsafe_allow_html=True)


def _toggle_all_strategies() -> None:
    value = bool(st.session_state.get("select_all_strategies"))
    for i in range(len(QUANT_STRATEGIES)):
        st.session_state[f"strategy_{i}"] = value


def _do_backup() -> str:
    info = storage_backup.backup_data()
    return f"已备份 {len(info['files'])} 个文件到 {info['folder'].name} 文件夹"


def _do_clear_realtime() -> str:
    storage_backup.clear_realtime()
    st.session_state.pop("replay_logged_keys", None)
    return "已清空实时数据（保留盘点数据与历史备份）"


def _do_clear_inventory() -> str:
    storage_backup.clear_inventory()
    st.session_state.pop("replay_logged_keys", None)
    return "已清空盘点数据与临时盘点文件（保留实时数据与历史备份）"


def _do_clear_all() -> str:
    removed = storage_backup.clear_all_data()
    st.session_state.pop("replay_logged_keys", None)
    return f"已清空实时与盘点数据（含 {removed} 个临时盘点文件），历史备份保留"


def _do_merge_temp() -> str:
    temp = storage_backup.current_temp()
    if not temp:
        return "没有待合并的盘点暂存文件"
    rows = db_log.read_trades(temp["db"])
    if not rows:
        storage_backup.delete_temp_files()
        return "盘点暂存文件为空，已删除临时文件"
    entries = [dict(row) for row in rows]
    excel_n = excel_log.append_trade_logs(
        entries, dedupe=True, path=excel_log.INVENTORY_FILE
    )
    db_n = db_log.append_trade_logs(
        entries, dedupe=True, path=db_log.INVENTORY_DB
    )
    storage_backup.delete_temp_files()
    return (
        f"盘点数据合并完成：新增入库 Excel {excel_n} 条 / 数据库 {db_n} 条，"
        "已删除临时文件"
    )


def _do_merge_all() -> str:
    excel_n = excel_log.rebuild_merge()
    db_n = db_log.rebuild_merge()
    return f"所有数据合并完成：merge sheet {excel_n} 条 / merge 表 {db_n} 条"


@st.dialog("确认清空实时数据")
def _dialog_clear_realtime() -> None:
    st.warning("将清空 ai_stock.xlsx / ai_stock.db 的全部实时数据，并删除已生成的 merge 结果。")
    c1, c2 = st.columns(2)
    if c1.button("确认清空实时数据", type="primary", key="confirm_clear_rt_btn", width="stretch"):
        try:
            msg = _do_clear_realtime()
        except Exception as exc:
            msg = f"清空实时数据失败：{exc}"
        st.session_state["store_action_msg"] = msg
        st.rerun()
    if c2.button("取消", key="cancel_clear_rt_btn", width="stretch"):
        st.rerun()


@st.dialog("确认清空盘点数据")
def _dialog_clear_inventory() -> None:
    st.warning("将清空 ai_stock_inventory.xlsx / .db 的盘点数据、删除临时盘点文件，并删除 merge 结果。")
    c1, c2 = st.columns(2)
    if c1.button("确认清空盘点数据", type="primary", key="confirm_clear_inv_btn", width="stretch"):
        try:
            msg = _do_clear_inventory()
        except Exception as exc:
            msg = f"清空盘点数据失败：{exc}"
        st.session_state["store_action_msg"] = msg
        st.rerun()
    if c2.button("取消", key="cancel_clear_inv_btn", width="stretch"):
        st.rerun()


@st.dialog("确认清空所有数据")
def _dialog_clear_all() -> None:
    st.warning("将清空实时数据、盘点数据与临时盘点文件（历史备份文件夹保留）。")
    c1, c2 = st.columns(2)
    if c1.button("确认清空所有", type="primary", key="confirm_clear_all_btn", width="stretch"):
        try:
            msg = _do_clear_all()
        except Exception as exc:
            msg = f"清空失败：{exc}"
        st.session_state["store_action_msg"] = msg
        st.rerun()
    if c2.button("取消", key="cancel_clear_all_btn", width="stretch"):
        st.rerun()


@st.fragment
def render_quant_strategy_box() -> None:
    """独立 fragment：勾选策略只重跑本区域，不触发整页重跑（避免全页发白）。"""
    with st.container(border=True, key="quant_strategies"):
        st.subheader("量化策略")
        st.caption("勾选需要关注的策略，交易信号将显示在右侧系统日志中")
        all_selected = all(
            st.session_state.get(f"strategy_{i}", False)
            for i in range(len(QUANT_STRATEGIES))
        )
        # 全选框状态与各策略勾选情况保持一致
        st.session_state["select_all_strategies"] = all_selected
        st.checkbox(
            "全选",
            key="select_all_strategies",
            on_change=_toggle_all_strategies,
        )
        per_row = 4
        for start in range(0, len(QUANT_STRATEGIES), per_row):
            names = QUANT_STRATEGIES[start:start + per_row]
            widths = []
            for _ in names:
                widths += [2.2, 0.55]
            cols = st.columns(widths, vertical_alignment="center")
            for j, name in enumerate(names):
                idx = start + j
                cols[j * 2].checkbox(name, key=f"strategy_{idx}")
                color = strategies.STRATEGY_COLORS[name]
                cols[j * 2 + 1].markdown(
                    f"<span style='color:{color};font-size:1rem'>●</span>",
                    unsafe_allow_html=True,
                )
        selected = [
            QUANT_STRATEGIES[i] for i in range(len(QUANT_STRATEGIES))
            if st.session_state.get(f"strategy_{i}")
        ]
        if selected:
            chips = "　".join(
                f"<span style='color:{strategies.STRATEGY_COLORS[name]}'>●</span>"
                f"<span style='color:#555'> {name}</span>"
                for name in selected
            )
            st.markdown(
                f"<div style='font-size:0.85rem;color:#666'>"
                f"已选择 {len(selected)} 个：{chips}</div>",
                unsafe_allow_html=True,
            )

        st.divider()
        btn_col, date_col = st.columns([1, 1.6], vertical_alignment="bottom")
        with date_col:
            replay_date = st.date_input(
                "盘点日期",
                value=dt.date.today(),
                max_value=dt.date.today(),
                key="replay_date",
            )
        with btn_col:
            replay_clicked = st.button(
                "盘点数据", key="replay_btn", type="primary", width=BTN_WIDTH
            )
        if replay_clicked:
            if not st.session_state.get("watchlist"):
                st.info("自选看板为空，无法盘点。")
            elif not selected:
                st.info("请先勾选至少一个量化策略。")
            else:
                # 先整页刷新，把耗时的分钟级回放放到页面渲染完成之后执行，
                # 避免盘点期间整页发白/无响应。
                st.session_state["pending_replay"] = replay_date
                st.rerun(scope="app")
        if st.session_state.get("pending_replay"):
            st.info(
                f"正在盘点 {st.session_state['pending_replay']}，"
                "计算完成后会自动刷新「交易数据」。"
            )
        replay_done_msg = st.session_state.pop("replay_done_msg", None)
        if replay_done_msg:
            st.success(replay_done_msg)
        st.caption(
            "盘点按所选交易日的分钟数据逐分钟回放（日线历史截至前一日），"
            "交易时间取首次满足买卖条件的时刻，无分钟数据时按 15:00:00 回退；"
            "盘点结果先写入临时暂存文件，不污染实时数据。实时信号仅在交易时段内产生。"
        )


def _render_trade_table(view: pd.DataFrame) -> None:
    """交易列表（HTML 表格）：策略名用策略色、买卖红绿、表头与内容居中。"""
    columns = ["量化策略名称", "股票名称", "买卖", "当前交易价格",
               "数量", "交易时间", "交易日期", "来源"]
    th_style = (
        "position:sticky;top:0;background:#f7f8fa;padding:6px 8px;"
        "border-bottom:1px solid #e5e7eb;font-weight:600;text-align:center;"
        "white-space:nowrap;z-index:1"
    )
    header = "".join(
        f"<th style='{th_style}'>{html.escape(col)}</th>" for col in columns
    )
    body_rows = []
    for idx, (_, row) in enumerate(view.iterrows()):
        strategy = str(row["量化策略名称"])
        strategy_color = strategies.STRATEGY_COLORS.get(strategy, "#111111")
        action = str(row["买卖"])
        action_color = (
            UP_COLOR if action == "买"
            else DOWN_COLOR if action == "卖" else "#111111"
        )
        price = to_float(row["当前交易价格"])
        quantity = to_float(row["数量"])
        price_txt = f"{price:.2f}" if price is not None else "--"
        qty_txt = f"{int(quantity)}" if quantity is not None else "--"
        bg = "#ffffff" if idx % 2 == 0 else "#fafbfc"
        td = f"padding:5px 8px;border-bottom:1px solid #f0f0f0;text-align:center;background:{bg}"
        cells = (
            f"<td style=\"{td};color:{strategy_color};font-weight:600\">"
            f"{html.escape(strategy)}</td>"
            f"<td style='{td}'>{html.escape(str(row['股票名称']))}</td>"
            f"<td style=\"{td};color:{action_color};font-weight:600\">"
            f"{html.escape(action)}</td>"
            f"<td style='{td}'>{price_txt}</td>"
            f"<td style='{td}'>{qty_txt}</td>"
            f"<td style='{td}'>{html.escape(str(row['交易时间']))}</td>"
            f"<td style='{td}'>{html.escape(str(row['交易日期']))}</td>"
            f"<td style='{td}'>{html.escape(str(row['来源']))}</td>"
        )
        body_rows.append(f"<tr>{cells}</tr>")
    st.markdown(
        "<div style='max-height:420px;overflow:auto;border:1px solid #e5e7eb;"
        "border-radius:8px'><table style='width:100%;border-collapse:collapse;"
        "font-size:0.84rem'>"
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


@st.fragment
def render_trade_data_panel() -> None:
    """交易数据统计视图：按日期/来源/策略筛选，并管理盘点暂存与合并。"""
    st.markdown(STORE_BUTTON_CSS, unsafe_allow_html=True)
    with st.container(border=True, key="trade_data_panel"):
        st.subheader("交易数据")
        rows = [dict(r) for r in db_log.read_trades(db_log.MAIN_DB)]
        rows += [dict(r) for r in db_log.read_trades(db_log.INVENTORY_DB)]
        temp = storage_backup.current_temp()
        if temp:
            rows += [dict(r) for r in db_log.read_trades(temp["db"])]

        if not rows:
            st.caption("暂无交易数据。完成盘点并合并后可在此查看。")
        else:
            df = pd.DataFrame(rows).rename(columns={
                "strategy": "量化策略名称", "stock": "股票名称",
                "action": "买卖", "price": "当前交易价格", "quantity": "数量",
                "time": "交易时间", "date": "交易日期", "source": "来源",
            })
            dates = sorted(df["交易日期"].astype(str).unique(), reverse=True)
            f1, f2, f3 = st.columns([1.3, 1, 1.3])
            date_sel = f1.selectbox("交易日期", ["全部"] + dates, key="stats_date")
            source_sel = f2.selectbox(
                "来源", ["全部", SOURCE_LIVE, SOURCE_REPLAY], key="stats_source"
            )
            strategy_sel = f3.selectbox(
                "策略", ["全部"] + QUANT_STRATEGIES, key="stats_strategy"
            )
            view = df
            if date_sel != "全部":
                view = view[view["交易日期"].astype(str) == date_sel]
            if source_sel != "全部":
                view = view[view["来源"] == source_sel]
            if strategy_sel != "全部":
                view = view[view["量化策略名称"] == strategy_sel]

            price = pd.to_numeric(view["当前交易价格"], errors="coerce")
            quantity = pd.to_numeric(view["数量"], errors="coerce")
            buys = int((view["买卖"] == "买").sum())
            sells = int((view["买卖"] == "卖").sum())
            amount = float((price.fillna(0) * quantity.fillna(0)).sum())
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("交易总数", f"{len(view)} 笔")
            m2.metric("买入", f"{buys} 笔")
            m3.metric("卖出", f"{sells} 笔")
            m4.metric("涉及股票", f"{int(view['股票名称'].nunique())} 只")
            m5.metric("成交金额", fmt_amount(amount))

            if view.empty:
                st.caption("当前筛选条件下没有数据。")
            else:
                _render_trade_table(view)

        st.divider()
        row1 = st.columns(3)
        if row1[0].button("盘点数据合并", key="merge_temp_btn", width="stretch"):
            st.session_state["store_action_msg"] = _do_merge_temp()
            st.rerun()
        if row1[1].button("所有数据合并", key="merge_all_btn", width="stretch"):
            st.session_state["store_action_msg"] = _do_merge_all()
            st.rerun()
        if row1[2].button("备份数据", key="backup_data_btn", width="stretch"):
            st.session_state["store_action_msg"] = _do_backup()
            st.rerun()
        row2 = st.columns(3)
        if row2[0].button("清空实时数据", key="clear_rt_btn", width="stretch"):
            _dialog_clear_realtime()
        if row2[1].button("清空盘点数据", key="clear_inv_btn", width="stretch"):
            _dialog_clear_inventory()
        if row2[2].button("清空所有数据", key="clear_all_btn", width="stretch"):
            _dialog_clear_all()

        msg = st.session_state.pop("store_action_msg", None)
        if msg:
            st.success(msg)
        st.caption(
            "「盘点数据合并」：临时文件 → ai_stock_inventory.*；"
            "「所有数据合并」：重建 ai_stock.xlsx 的 merge sheet 与 "
            "ai_stock.db 的 merge 表（实时优先去重）；实时与盘点数据保持隔离。"
        )


def render_watch_panel(chart_type: str, period_label: str, range_label: str) -> None:
    codes = st.session_state.get("watchlist", [])
    snapshot = st.session_state.get("watch_snapshot", {})
    spot = snapshot.get("spot", pd.DataFrame())
    flow = snapshot.get("flow", pd.DataFrame())
    charts = snapshot.get("charts", {})
    updated = snapshot.get("updated")
    stamp = f"{updated:%H:%M:%S}" if updated else "--"

    main_col, log_col = st.columns([2.4, 1.6], gap="medium")

    with main_col:
        if not codes:
            st.info("自选看板为空：在上方输入代码或名称首字母，选择匹配结果后点击「添加」。")
        else:
            errors = snapshot.get("errors") or []
            if errors:
                st.caption("；".join(errors))
            for i in range(0, len(codes), 3):
                cols = st.columns(3)
                for col, code in zip(cols, codes[i:i + 3]):
                    render_watch_card(
                        col, code, spot, flow,
                        charts.get(code, pd.DataFrame()),
                        chart_type, period_label, range_label,
                    )
            st.caption(f"自选图表更新于 {stamp}")

    with log_col:
        render_system_log_panel(max(1, (len(codes) + 2) // 3))


def main() -> None:
    title_col, refresh_col = st.columns([4.5, 1], vertical_alignment="bottom")
    with title_col:
        st.title("📈 老李股票行情助手")
    with refresh_col:
        refresh_label = st.selectbox("自动刷新", list(REFRESH_OPTIONS.keys()), index=2)

    refresh = REFRESH_OPTIONS[refresh_label]

    if "watchlist" not in st.session_state:
        st.session_state.watchlist = load_watchlist()

    if "system_logs" not in st.session_state:
        st.session_state.system_logs = []
        add_log("行情服务已连接，自选看板就绪", "info")
        add_log(
            f"策略引擎初始化完成，已注册 {len(STRATEGY_RUNNERS)} 个量化策略",
            "debug",
        )

    if not st.session_state.get("store_ready"):
        try:
            storage_backup.delete_temp_files()
            excel_log.ensure_workbook(excel_log.MAIN_FILE)
            excel_log.ensure_workbook(excel_log.INVENTORY_FILE)
            db_log.ensure_database(db_log.MAIN_DB)
            db_log.ensure_database(db_log.INVENTORY_DB)
            st.session_state["store_ready"] = True
        except Exception as exc:
            add_log(f"交易数据文件创建失败：{exc}", "debug")

    if "index_snapshot" not in st.session_state:
        st.session_state["index_snapshot"] = collect_index_snapshot()

    if refresh:
        refresh_frag = st.fragment(refresh_snapshots, run_every=refresh)
        index_panel = st.fragment(render_index_panel, run_every=refresh)
        watch_panel = st.fragment(render_watch_panel, run_every=refresh)
    else:
        refresh_snapshots()
        index_panel = render_index_panel
        watch_panel = render_watch_panel

    index_panel()
    st.divider()

    try:
        stock_index = get_stock_index()
    except Exception as exc:
        stock_index = pd.DataFrame(columns=["代码", "名称", "首字母"])
        st.warning(f"股票列表获取失败，暂时只能按代码添加：{exc}")

    with st.container(border=True, key="watch_controls"):
        st.subheader("自选看板")

        a1, a2, a3 = st.columns([2.4, 2.4, 1], vertical_alignment="bottom")
        query = a1.text_input(
            "添加自选", placeholder="输入代码或名称首字母，如 600519 / gzmt", key="add_query"
        )
        matches = resolve_stocks(query, stock_index)
        labels = [f"{c}　{n}" if n else c for c, n in matches[:30]]
        picked_idx = None
        if labels:
            picked = a2.selectbox("匹配结果", labels, key="add_pick")
            picked_idx = labels.index(picked)
        if a3.button("添加", type="primary", width="stretch",
                     disabled=picked_idx is None, key="add_btn"):
            code = matches[picked_idx][0]
            if code in st.session_state.watchlist:
                st.toast("已在自选看板中")
            else:
                add_to_watchlist(code)
                st.toast("已添加到自选看板")
        if not labels:
            st.caption("无匹配股票" if query.strip() else "输入代码或名称首字母，再从「匹配结果」中选择后点击添加")

        c1, c2, c3 = st.columns([1.4, 1, 1])
        chart_type = c1.radio("图表类型", ["K线", "分时"], horizontal=True)
        is_kline = chart_type == "K线"
        period_label = c2.selectbox("K线周期", list(PERIODS.keys()), disabled=not is_kline)
        range_label = c3.selectbox("时间范围", list(RANGES.keys()), index=2, disabled=not is_kline)

        if len(st.session_state.watchlist) >= 2:
            st.caption("拖动下方标签可调整自选看板顺序")
            apply_sort_order(stock_index)

    render_quant_strategy_box()
    render_trade_data_panel()

    st.session_state["panel_params"] = (chart_type, period_label, range_label)
    ensure_watch_snapshot(chart_type, period_label, range_label)
    watch_panel(chart_type, period_label, range_label)

    # 页面渲染完成后再执行耗时的数据刷新，避免整页发白
    if refresh:
        refresh_frag()

    # 盘点计算同样放到最后执行，完成后整页刷新显示结果
    pending_replay = st.session_state.pop("pending_replay", None)
    if pending_replay:
        stats = run_replay(pending_replay)
        st.session_state["replay_done_msg"] = _replay_result_message(
            stats, pending_replay
        )
        st.rerun(scope="app")


if __name__ == "__main__":
    main()
