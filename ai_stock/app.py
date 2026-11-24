"""股票行情助手：大盘指数 + 个股 K 线（日/周/月）与分时图。

运行方式：
    streamlit run app.py

数据来源：AKShare（新浪财经接口，避免东方财富限流）。
"""

import datetime as dt
import re

import akshare as ak
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

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

PERIODS = {"日K": "daily", "周K": "weekly", "月K": "monthly"}
RANGES = {"3个月": 90, "6个月": 180, "1年": 365, "3年": 1095, "全部": None}


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


@st.cache_data(ttl=30, show_spinner=False)
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

    for col in ("最新价", "涨跌额", "涨跌幅"):
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


@st.cache_data(ttl=300, show_spinner=False)
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


@st.cache_data(ttl=300, show_spinner=False)
def get_kline(symbol: str, period: str, start_date: str, end_date: str) -> pd.DataFrame:
    daily = get_daily(symbol, start_date, end_date)
    if daily.empty or period == "daily":
        return daily
    return _resample(daily, "W-FRI" if period == "weekly" else "ME")


@st.cache_data(ttl=60, show_spinner=False)
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


def render_index_cards(spot: pd.DataFrame) -> None:
    cols = st.columns(len(INDEXES))
    for col, (code, label) in zip(cols, INDEXES):
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


def make_kline_fig(df: pd.DataFrame, title: str) -> go.Figure:
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
    fig.add_trace(
        go.Bar(x=df["日期"], y=df["成交量"], name="成交量", marker_color=vol_colors),
        row=2, col=1,
    )
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left"),
        template="plotly_white",
        height=620,
        margin=dict(l=10, r=10, t=50, b=10),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
    )
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)
    return fig


def make_intraday_fig(df: pd.DataFrame, prev_close, title: str) -> go.Figure:
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
            annotation_text=f"昨收 {prev_close:.2f}",
            annotation_position="top left",
            row=1, col=1,
        )
    fig.add_trace(
        go.Bar(x=labels, y=volume, name="成交量", marker_color=bar_colors),
        row=2, col=1,
    )
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left"),
        template="plotly_white",
        height=620,
        margin=dict(l=10, r=10, t=50, b=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
    )
    fig.update_xaxes(type="category", nticks=12, row=2, col=1)
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)
    return fig


def render_stock_header(code: str) -> dict:
    """展示个股名称与实时价格，返回快照信息。"""
    info = {}
    try:
        spot = get_a_spot()
        row = spot[spot["代码"] == code]
        if not row.empty:
            info = row.iloc[0].to_dict()
    except Exception as exc:
        st.caption(f"实时快照获取失败：{exc}")

    name = str(info.get("名称", "")).strip()
    heading = f"{name}（{code}）" if name else code
    price = to_float(info.get("最新价"))
    chg = to_float(info.get("涨跌额"))
    pct = to_float(info.get("涨跌幅"))

    if price is not None and chg is not None and pct is not None:
        color = color_of(chg)
        st.markdown(
            f"### {heading}　<span style='color:{color}'>"
            f"{price:.2f}　{chg:+.2f}（{pct:+.2f}%）</span>",
            unsafe_allow_html=True,
        )
    else:
        st.subheader(heading)
    return info


def main() -> None:
    st.title("📈 股票行情助手")

    try:
        spot = get_index_spot()
        render_index_cards(spot)
        st.caption(f"大盘指数更新于 {dt.datetime.now():%H:%M:%S}　数据来源：AKShare")
    except Exception as exc:
        st.warning(f"大盘指数获取失败：{exc}")

    st.divider()

    c1, c2, c3 = st.columns([2.2, 1, 1])
    with c1:
        raw_code = st.text_input("股票代码", value="600519", help="6 位 A 股代码，如 600519、000001")
    with c2:
        period_label = st.selectbox("K线周期", list(PERIODS.keys()))
    with c3:
        range_label = st.selectbox("时间范围", list(RANGES.keys()), index=2)

    code = normalize_code(raw_code)
    if not code:
        st.info("请输入 6 位 A 股代码，例如 600519。")
        return

    with st.spinner("加载行情数据…"):
        info = render_stock_header(code)

    tab_kline, tab_intraday = st.tabs(["K线图", "分时图"])

    with tab_kline:
        days = RANGES[range_label]
        today = dt.date.today()
        start = "19900101" if days is None else (today - dt.timedelta(days=days)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        try:
            kdf = get_kline(code, PERIODS[period_label], start, end)
        except Exception as exc:
            kdf = pd.DataFrame()
            st.error(f"K线数据获取失败：{exc}")
        if kdf.empty:
            st.info("暂无 K 线数据（请检查代码是否正确）。")
        else:
            heading = info.get("名称") or code
            st.plotly_chart(
                make_kline_fig(kdf, f"{heading}（{code}）　{period_label}"),
                width="stretch",
            )
            st.caption(f"共 {len(kdf)} 根 K 线，前复权，数据来源：新浪财经")

    with tab_intraday:
        try:
            idf = get_intraday(code)
        except Exception as exc:
            idf = pd.DataFrame()
            st.error(f"分时数据获取失败：{exc}")
        if idf.empty:
            st.info("暂无分时数据（可能为非交易日或停牌）。")
        else:
            prev_close = to_float(info.get("昨收"))
            if prev_close is None and len(idf) > 1:
                prev_close = to_float(idf["开盘"].iloc[0])
            heading = info.get("名称") or code
            day = idf["时间"].dt.strftime("%Y-%m-%d").iloc[-1]
            st.plotly_chart(
                make_intraday_fig(idf, prev_close, f"{heading}（{code}）　分时 {day}"),
                width="stretch",
            )
            st.caption("数据来源：新浪财经，按 1 分钟聚合")


if __name__ == "__main__":
    main()
