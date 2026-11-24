"""股票行情助手：大盘指数 + 自选看板（K线/分时，点击放大，持久化）。

运行方式：
    streamlit run app.py

数据来源：AKShare（新浪财经接口，避免东方财富限流）。
"""

import datetime as dt
import json
import os
import re
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from pypinyin import Style, lazy_pinyin
from streamlit_sortables import sort_items

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
REFRESH_OPTIONS = {"关闭": 0, "15秒": 15, "30秒": 30, "60秒": 60}

MA_PERIODS = (5, 10, 20, 30)
# 通达信/同花顺习惯：5日白、10日黄、20日紫、30日绿（白底上 5日 用灰色替代）
MA_COLORS = {5: "#9e9e9e", 10: "#e6a700", 20: "#d000d0", 30: "#00a651"}
PERIOD_UNITS = {"日K": "日", "周K": "周", "月K": "月"}

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


def add_to_watchlist(code: str) -> None:
    if code not in st.session_state.watchlist:
        st.session_state.watchlist.append(code)
        save_watchlist(st.session_state.watchlist)


def remove_from_watchlist(code: str) -> None:
    st.session_state.watchlist = [
        c for c in st.session_state.watchlist if c != code
    ]
    save_watchlist(st.session_state.watchlist)


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

    for col in ("最新价", "涨跌额", "涨跌幅"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=30, show_spinner=False)
def get_a_spot() -> pd.DataFrame:
    """沪深京 A 股实时快照（含名称、最新价、昨收、涨跌幅）。"""
    df = ak.stock_zh_a_spot().copy()
    df["代码"] = df["代码"].astype(str).map(normalize_code)
    for col in ("最新价", "涨跌额", "涨跌幅", "昨收", "今开", "最高", "最低"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


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


def ma_legend_html(unit: str) -> str:
    items = "".join(
        f"<span style='color:{MA_COLORS[n]}'>●</span>"
        f"<span style='color:#666;margin:0 10px 0 3px'>{n}{unit}</span>"
        for n in MA_PERIODS
    )
    return f"<div style='font-size:0.72rem;line-height:1.4;margin-left:2px'>{items}</div>"


def render_watch_card(col, code: str, spot: pd.DataFrame, chart_type: str,
                      period_label: str, range_label: str) -> None:
    row = spot[spot["代码"] == code] if not spot.empty else spot.iloc[0:0]
    name = str(row.iloc[0]["名称"]) if not row.empty else ""
    price = to_float(row.iloc[0].get("最新价")) if not row.empty else None
    chg = to_float(row.iloc[0].get("涨跌额")) if not row.empty else None
    pct = to_float(row.iloc[0].get("涨跌幅")) if not row.empty else None
    prev_close = to_float(row.iloc[0].get("昨收")) if not row.empty else None

    with col:
        with st.container(border=True):
            color = color_of(chg)
            price_txt = f"{price:.2f}" if price is not None else "--"
            pct_txt = f"{pct:+.2f}%" if pct is not None else "--"
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
                f"<span style='font-weight:600'>{name or '未知股票'}</span>"
                f"<span style='color:#999;font-size:0.8rem'>{code}</span></div>"
                f"<div style='color:{color};font-size:1.1rem;font-weight:600'>"
                f"{price_txt}　{pct_txt}</div>",
                unsafe_allow_html=True,
            )
            try:
                if chart_type == "分时":
                    df = get_intraday(code)
                    fig = make_intraday_fig(df, prev_close, height=280, compact=True) if not df.empty else None
                else:
                    df = load_kline(code, period_label, range_label)
                    fig = make_kline_fig(
                        df, height=280, compact=True, unit=PERIOD_UNITS[period_label]
                    ) if not df.empty else None
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
            except Exception as exc:
                st.caption(f"数据获取失败：{exc}")

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


def render_index_panel() -> None:
    try:
        index_spot = get_index_spot()
        render_index_cards(index_spot)
        st.caption(f"大盘指数更新于 {dt.datetime.now():%H:%M:%S}　数据来源：AKShare")
    except Exception as exc:
        st.warning(f"大盘指数获取失败：{exc}")


def render_watch_panel(chart_type: str, period_label: str, range_label: str) -> None:
    codes = st.session_state.get("watchlist", [])
    if not codes:
        st.info("自选看板为空：在上方输入代码或名称首字母，选择匹配结果后点击「添加」。")
        return
    try:
        spot = get_a_spot()
    except Exception as exc:
        spot = pd.DataFrame()
        st.caption(f"个股快照获取失败：{exc}")
    with st.spinner("加载自选图表…"):
        for i in range(0, len(codes), 4):
            cols = st.columns(4)
            for col, code in zip(cols, codes[i:i + 4]):
                render_watch_card(col, code, spot, chart_type, period_label, range_label)
    st.caption(f"自选图表更新于 {dt.datetime.now():%H:%M:%S}")


def main() -> None:
    title_col, refresh_col = st.columns([4.5, 1], vertical_alignment="bottom")
    with title_col:
        st.title("📈 股票行情助手")
    with refresh_col:
        refresh_label = st.selectbox("自动刷新", list(REFRESH_OPTIONS.keys()), index=2)

    refresh = REFRESH_OPTIONS[refresh_label]
    if refresh:
        index_panel = st.fragment(render_index_panel, run_every=refresh)
        watch_panel = st.fragment(render_watch_panel, run_every=refresh)
    else:
        index_panel = render_index_panel
        watch_panel = render_watch_panel

    index_panel()
    st.divider()

    if "watchlist" not in st.session_state:
        st.session_state.watchlist = load_watchlist()

    try:
        stock_index = get_stock_index()
    except Exception as exc:
        stock_index = pd.DataFrame(columns=["代码", "名称", "首字母"])
        st.warning(f"股票列表获取失败，暂时只能按代码添加：{exc}")

    st.subheader("自选看板")

    a1, a2, a3 = st.columns([2.4, 2.4, 1])
    query = a1.text_input(
        "添加自选", placeholder="输入代码或名称首字母，如 600519 / gzmt", key="add_query"
    )
    matches = resolve_stocks(query, stock_index)
    labels = [f"{c}　{n}" if n else c for c, n in matches[:30]]
    picked_idx = None
    if labels:
        picked = a2.selectbox("匹配结果", labels, key="add_pick")
        picked_idx = labels.index(picked)
    elif query.strip():
        a2.caption("无匹配股票")
    else:
        a2.caption("输入后从匹配结果中选择")
    if a3.button("添加", type="primary", width="stretch",
                 disabled=picked_idx is None, key="add_btn"):
        code = matches[picked_idx][0]
        if code in st.session_state.watchlist:
            st.toast("已在自选看板中")
        else:
            add_to_watchlist(code)
            st.toast("已添加到自选看板")

    c1, c2, c3 = st.columns([1.4, 1, 1])
    chart_type = c1.radio("图表类型", ["K线", "分时"], horizontal=True)
    is_kline = chart_type == "K线"
    period_label = c2.selectbox("K线周期", list(PERIODS.keys()), disabled=not is_kline)
    range_label = c3.selectbox("时间范围", list(RANGES.keys()), index=2, disabled=not is_kline)

    if len(st.session_state.watchlist) >= 2:
        st.caption("拖动下方标签可调整自选看板顺序")
        apply_sort_order(stock_index)

    watch_panel(chart_type, period_label, range_label)


if __name__ == "__main__":
    main()
