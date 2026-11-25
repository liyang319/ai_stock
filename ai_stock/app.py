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

import strategies
import strategy_dual_ma
import strategy_macd
import strategy_rsi

st.set_page_config(page_title="股票行情助手", page_icon="📈", layout="wide")

UP_COLOR = "#e5333c"      # 中国习惯：红涨
DOWN_COLOR = "#1aa260"    # 绿跌
FLAT_COLOR = "#666666"

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
}

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

LOG_COLORS = {"info": "#3b82f6", "success": "#1aa260", "warning": "#f59e0b", "error": "#e5333c"}
LOG_MAX_ENTRIES = 100

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
        add_log(f"添加自选 {code}", "success")


def remove_from_watchlist(code: str) -> None:
    st.session_state.watchlist = [
        c for c in st.session_state.watchlist if c != code
    ]
    save_watchlist(st.session_state.watchlist)
    add_log(f"移除自选 {code}", "warning")


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
            ".sortable-container-body{display:flex;flex-wrap:wrap;"
            "background:transparent;padding:0;min-height:auto}"
            ".sortable-item,.sortable-item:hover,.sortable-item:active{"
            "border-radius:999px;padding:2px 12px;margin:4px;"
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

    breadth = None
    flow_agg = None
    breadth_source = "同花顺"
    try:
        fund_flow = get_fund_flow()
        if not fund_flow.empty:
            breadth = compute_breadth_from_flow(fund_flow)
            flow_agg = {
                "inflow": float(pd.to_numeric(fund_flow["流入资金"], errors="coerce").fillna(0).sum()),
                "outflow": float(pd.to_numeric(fund_flow["流出资金"], errors="coerce").fillna(0).sum()),
                "net": float(pd.to_numeric(fund_flow["净额"], errors="coerce").fillna(0).sum()),
            }
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
    except Exception as exc:
        errors.append(f"市场资金流获取失败：{exc}")

    if breadth is None:  # 同花顺不可用时回退新浪口径
        breadth_source = "新浪"
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
        "breadth": breadth,
        "breadth_source": breadth_source,
        "market_amount": market_amount,
        "market_amount_sh": market_amount_sh,
        "market_amount_sz": market_amount_sz,
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
    """对已勾选的策略实时计算自选股信号，命中后写入系统日志（带策略专属颜色）。"""
    codes = list(st.session_state.get("watchlist", []))
    if not codes:
        return
    spot = snapshot.get("spot", pd.DataFrame())
    names = {}
    if not spot.empty:
        names = dict(zip(spot["代码"].astype(str), spot["名称"].astype(str)))
    seen = st.session_state.setdefault("strategy_signal_keys", set())

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
            add_log(
                strategies.format_signal_log(
                    strategy, names.get(code, code), action,
                    SIGNAL_QUANTITY, dt.datetime.now().strftime("%H:%M:%S"),
                ),
                color=color,
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
                          market_amount_sz, flow_agg, breadth_source: str,
                          hsgt, main_flow) -> None:
    up, down, flat = breadth["up"], breadth["down"], breadth["flat"]
    total = breadth["total"] or 1
    up_w = up / total * 100
    down_w = down / total * 100
    amount_txt = fmt_amount(market_amount) if market_amount else "--"
    scope = "同花顺口径·沪深A股" if breadth_source == "同花顺" else "新浪口径"

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
<span style="color:#555;font-size:0.92rem;white-space:nowrap">沪深A股涨跌家数
<span style="color:#bbb;font-size:0.75rem">（{scope}）</span></span>
<span style="color:{UP_COLOR};font-weight:600;white-space:nowrap">上涨 {up:,} 家</span>
<span style="color:{FLAT_COLOR};white-space:nowrap">平盘 {flat:,} 家</span>
<span style="color:{DOWN_COLOR};font-weight:600;white-space:nowrap">下跌 {down:,} 家</span>
<div style="flex:1;min-width:120px;height:8px;border-radius:999px;overflow:hidden;background:#eee;display:flex">
<div style="width:{up_w:.1f}%;background:{UP_COLOR}"></div>
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

    breadth = snapshot.get("breadth")
    if breadth:
        render_market_breadth(
            breadth,
            snapshot.get("market_amount"),
            snapshot.get("market_amount_sh"),
            snapshot.get("market_amount_sz"),
            snapshot.get("flow_agg"),
            snapshot.get("breadth_source", "同花顺"),
            snapshot.get("hsgt"),
            snapshot.get("main_flow"),
        )

    errors = snapshot.get("errors") or []
    if errors:
        st.warning("；".join(errors))


def render_system_log_panel() -> None:
    logs = st.session_state.get("system_logs", [])
    with st.container(border=True, key="system_log_panel"):
        st.markdown("#### :material/terminal: 系统日志")
        st.caption("量化信号与系统事件")
        with st.container(height=520, border=False):
            if not logs:
                st.caption("暂无日志")
                return
            rows = []
            for entry in reversed(logs[-60:]):
                color = entry.get("color") or LOG_COLORS.get(entry["level"], "#9aa0a6")
                rows.append(
                    "<div style='display:flex;gap:8px;align-items:flex-start;"
                    "padding:6px 4px;border-bottom:1px dashed #ececec'>"
                    f"<span style='color:#9aa0a6;font-size:0.72rem;"
                    f"font-family:ui-monospace,monospace;padding-top:2px'>{entry['time']}</span>"
                    f"<span style='color:{color};font-size:0.72rem;line-height:1.5'>●</span>"
                    f"<span style='color:#333;font-size:0.8rem;line-height:1.5;"
                    f"word-break:break-all'>{html.escape(entry['message'])}</span></div>"
                )
            st.markdown("".join(rows), unsafe_allow_html=True)


@st.fragment
def render_quant_strategy_box() -> None:
    """独立 fragment：勾选策略只重跑本区域，不触发整页重跑（避免全页发白）。"""
    with st.container(border=True, key="quant_strategies"):
        st.subheader("量化策略")
        st.caption("勾选需要关注的策略（双均线交叉已接入，其余算法待实现）")
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


def render_watch_panel(chart_type: str, period_label: str, range_label: str) -> None:
    codes = st.session_state.get("watchlist", [])
    snapshot = st.session_state.get("watch_snapshot", {})
    spot = snapshot.get("spot", pd.DataFrame())
    flow = snapshot.get("flow", pd.DataFrame())
    charts = snapshot.get("charts", {})
    updated = snapshot.get("updated")
    stamp = f"{updated:%H:%M:%S}" if updated else "--"

    main_col, log_col = st.columns([3.3, 1], gap="medium")

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
        render_system_log_panel()


def main() -> None:
    title_col, refresh_col = st.columns([4.5, 1], vertical_alignment="bottom")
    with title_col:
        st.title("📈 股票行情助手")
    with refresh_col:
        refresh_label = st.selectbox("自动刷新", list(REFRESH_OPTIONS.keys()), index=2)

    refresh = REFRESH_OPTIONS[refresh_label]

    if "watchlist" not in st.session_state:
        st.session_state.watchlist = load_watchlist()

    if "system_logs" not in st.session_state:
        st.session_state.system_logs = []
        add_log("行情服务已连接，自选看板就绪", "success")
        add_log("量化信号模块待接入，交易提示将显示在此", "info")

    if "index_snapshot" not in st.session_state:
        st.session_state["index_snapshot"] = collect_index_snapshot()

    if refresh:
        refresh_frag = st.fragment(refresh_snapshots, run_every=refresh)
        index_panel = st.fragment(render_index_panel, run_every=refresh)
        watch_panel = st.fragment(render_watch_panel, run_every=refresh)
        refresh_frag()
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

    st.session_state["panel_params"] = (chart_type, period_label, range_label)
    ensure_watch_snapshot(chart_type, period_label, range_label)
    watch_panel(chart_type, period_label, range_label)


if __name__ == "__main__":
    main()
