from __future__ import annotations

import json
import shutil
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any

import altair as alt
import polars as pl
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest.metrics import compute_summary
from src.backtest.results import list_runs, read_run_metadata
from src.backtest.runner import run_backtest
from src.strategies.orb_5m_momentum.config import OrbMomentumConfig
from src.strategies.registry import available_strategies


DEFAULT_DATA_ROOT = Path("D:/TradingData/massive_flatfiles/us_stock_sip/minutes_agg_v1")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingData/qq-momentum-trading/runs")

STRATEGY_DESCRIPTIONS = {
    "orb_5m_momentum": (
        "Opening-range momentum strategy using a 09:30-09:35 setup ranking, "
        "minute-by-minute live ranking, and completed 5-minute MACD/TEMA confirmation."
    )
}

METRIC_HELP = {
    "Final Equity": "Cash plus marked value of any open positions at the selected period end.",
    "Cash": "Portfolio cash at the selected period end.",
    "Net Profit": "Final equity minus starting equity for the selected period.",
    "Return": "Net profit divided by starting equity.",
    "Realized P/L": "Sum of closed trade P/L in the selected period.",
    "Unrealized P/L": "Marked open-position P/L at the selected period end. Currently zero after EOD liquidation.",
    "Max Drawdown": "Largest peak-to-trough equity decline inside the selected period.",
    "Trades": "Number of closed trades in the selected period.",
    "Win Rate": "Winning trades divided by all closed trades.",
    "Profit Factor": "Gross profit divided by absolute gross loss.",
    "Sharpe": "Annualized mean daily return divided by daily return standard deviation.",
    "Sortino": "Annualized mean daily return divided by downside daily return deviation.",
    "Turnover": "Filled notional order volume divided by average equity.",
    "Volume": "Total filled notional order volume.",
}


def install_css() -> None:
    st.markdown(
        """
        <style>
        .qq-card {
            border: 1px solid #d8dee4;
            border-radius: 8px;
            padding: 14px 16px;
            background: #ffffff;
            margin-bottom: 10px;
        }
        .qq-card h4 { margin: 0 0 8px 0; font-size: 1.0rem; }
        .qq-muted { color: #6b7280; font-size: 0.86rem; }
        .qq-good { color: #0f8a3b; font-weight: 650; }
        .qq-bad { color: #c0362c; font-weight: 650; }
        .qq-neutral { color: #374151; font-weight: 650; }
        .qq-metric-label { color: #6b7280; font-size: 0.78rem; margin-bottom: 3px; }
        .qq-metric-value { font-size: 1.15rem; font-weight: 700; }
        .qq-pill {
            display: inline-block;
            border-radius: 999px;
            padding: 2px 8px;
            border: 1px solid #d8dee4;
            margin-right: 4px;
            font-size: 0.78rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def safe_read_parquet(path: Path) -> pl.DataFrame:
    try:
        if not path.exists():
            return pl.DataFrame()
        return pl.read_parquet(path)
    except Exception as exc:
        st.error(f"Could not read {path.name}: {exc}")
        return pl.DataFrame()


def artifact_mtime(run_dir: Path) -> float:
    if not run_dir.exists():
        return 0.0
    return max((path.stat().st_mtime for path in run_dir.glob("*") if path.is_file()), default=run_dir.stat().st_mtime)


@st.cache_data(show_spinner=False)
def load_run_artifacts(run_dir_value: str, cache_key: float) -> dict[str, Any]:
    run_dir = Path(run_dir_value)
    data = {
        "metadata": read_run_metadata(run_dir) or {},
        "summary": load_json(run_dir / "summary.json"),
        "daily": safe_read_parquet(run_dir / "daily_summary.parquet"),
        "orders": safe_read_parquet(run_dir / "orders.parquet"),
        "trades": safe_read_parquet(run_dir / "trades.parquet"),
        "positions": safe_read_parquet(run_dir / "positions.parquet"),
        "portfolio": safe_read_parquet(run_dir / "portfolio.parquet"),
        "setup_rankings": safe_read_parquet(run_dir / "candidate_rankings.parquet"),
        "live_rankings": safe_read_parquet(run_dir / "live_rankings.parquet"),
        "signals": safe_read_parquet(run_dir / "signal_events.parquet"),
        "rejections": safe_read_parquet(run_dir / "rejection_events.parquet"),
        "bars_1m": safe_read_parquet(run_dir / "symbol_bars.parquet"),
        "bars_5m": safe_read_parquet(run_dir / "symbol_bars_5m.parquet"),
    }
    return data


def money(value) -> str:
    return f"${float(value or 0.0):,.2f}"


def pct(value) -> str:
    return f"{float(value or 0.0) * 100:.2f}%"


def num(value, decimals: int = 2) -> str:
    return f"{float(value or 0.0):,.{decimals}f}"


def value_class(value: float, inverse: bool = False) -> str:
    if value == 0:
        return "qq-neutral"
    good = value > 0
    if inverse:
        good = value < 0
    return "qq-good" if good else "qq-bad"


def metric_card(label: str, value: str, raw_value: float = 0.0, inverse: bool = False) -> None:
    help_text = METRIC_HELP.get(label, "")
    st.markdown(
        f"""
        <div class="qq-card">
            <div class="qq-metric-label">{label}</div>
            <div class="qq-metric-value {value_class(raw_value, inverse)}">{value}</div>
            <div class="qq-muted" title="{help_text}">?</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def strategy_readme(strategy_name: str) -> str:
    path = PROJECT_ROOT / "src" / "strategies" / strategy_name / "README.md"
    return path.read_text(encoding="utf-8") if path.exists() else "No strategy README found."


def default_config(strategy_name: str, output_root: Path) -> dict:
    params = OrbMomentumConfig().to_dict()
    start = date(2024, 5, 1)
    end = date(2024, 5, 31)
    return {
        "run_name": f"{strategy_name} {start.isoformat()} to {end.isoformat()}",
        "strategy_name": strategy_name,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "data_root": str(DEFAULT_DATA_ROOT),
        "output_root": str(output_root),
        "initial_cash": 10_000.0,
        "market_utc_offset_hours": -4.0,
        "slippage_bps": 2.0,
        "save_symbol_bars": True,
        "created_by_app": True,
        "strategy_params": params,
    }


def minute_file_path(data_root: Path, session: date) -> Path:
    return data_root / f"{session.year:04d}" / f"{session.month:02d}" / f"{session.isoformat()}.csv.gz"


def available_sessions(data_root: Path, start: date, end: date) -> list[date]:
    sessions = []
    cursor = start
    while cursor <= end:
        if minute_file_path(data_root, cursor).exists():
            sessions.append(cursor)
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return sessions


def render_run_header(config: dict, status: str = "Draft", summary: dict | None = None) -> None:
    params = config.get("strategy_params", {})
    sessions = available_sessions(Path(config["data_root"]), date.fromisoformat(config["start_date"]), date.fromisoformat(config["end_date"]))
    summary = summary or {}
    st.markdown(
        f"""
        <div class="qq-card">
          <h4>{config.get("run_name", "Untitled run")}</h4>
          <div class="qq-muted">{config.get("strategy_name")} | {status} | {config.get("start_date")} to {config.get("end_date")}</div>
          <div style="margin-top:8px;">
            <span class="qq-pill">sessions {len(sessions)}</span>
            <span class="qq-pill">cash {money(config.get("initial_cash"))}</span>
            <span class="qq-pill">max pos {params.get("max_active_positions")}</span>
            <span class="qq-pill">watchlist {params.get("watchlist_size")}</span>
            <span class="qq-pill">setup {num(params.get("min_setup_score"))}</span>
            <span class="qq-pill">live {num(params.get("min_live_score"))}</span>
            <span class="qq-pill">return {pct(summary.get("return_pct", 0.0))}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def run_label(run_dir: Path) -> str:
    metadata = read_run_metadata(run_dir) or {}
    summary = metadata.get("summary") or load_json(run_dir / "summary.json")
    return f"{metadata.get('run_name', run_dir.name)} | {metadata.get('status', 'unknown')} | {pct(summary.get('return_pct', 0.0))}"


def run_table_rows(runs: list[Path]) -> list[dict]:
    rows = []
    for run in runs:
        metadata = read_run_metadata(run) or {}
        summary = metadata.get("summary") or load_json(run / "summary.json")
        config = metadata.get("config", {})
        rows.append(
            {
                "Run Name": metadata.get("run_name", run.name),
                "Status": metadata.get("status", "unknown"),
                "Created": metadata.get("created_at", ""),
                "Date Range": f"{config.get('start_date', '')} .. {config.get('end_date', '')}",
                "Return": pct(summary.get("return_pct", 0.0)),
                "PnL": money(summary.get("total_pnl", 0.0)),
                "Trades": summary.get("trade_count", 0),
                "Run Folder": run.name,
            }
        )
    return rows


def filter_df(df: pl.DataFrame, period: str) -> pl.DataFrame:
    if df.is_empty() or period == "Whole Run":
        return df
    if "session_date" in df.columns:
        return df.filter(pl.col("session_date") == period)
    for column in ["timestamp", "created_at", "filled_at", "entry_time", "exit_time", "bar_time_market"]:
        if column in df.columns:
            return df.filter(pl.col(column).cast(pl.Utf8).str.starts_with(period))
    return df


def period_options(data: dict) -> list[str]:
    daily = data["daily"]
    if daily.is_empty() or "session_date" not in daily.columns:
        return ["Whole Run"]
    return ["Whole Run"] + daily.select("session_date").unique().sort("session_date")["session_date"].to_list()


def selected_summary(data: dict, period: str) -> dict:
    if period == "Whole Run":
        return data["metadata"].get("summary") or data["summary"]
    daily = filter_df(data["daily"], period)
    trades = filter_df(data["trades"], period).to_dicts()
    orders = filter_df(data["orders"], period).to_dicts()
    portfolio = filter_df(data["portfolio"], period).to_dicts()
    if daily.is_empty():
        return data["metadata"].get("summary") or data["summary"]
    initial_cash = float(daily.row(0, named=True).get("start_equity") or 0.0)
    return compute_summary(
        run_dir="selected-period",
        strategy_name=data["metadata"].get("strategy_name", ""),
        run_name=data["metadata"].get("run_name", ""),
        initial_cash=initial_cash,
        trades=trades,
        orders=orders,
        portfolio_rows=portfolio,
        daily_rows=daily.to_dicts(),
    )


def render_metrics(summary: dict) -> None:
    runtime = summary.get("runtimeStatistics", {})
    metrics = [
        ("Final Equity", money(summary.get("final_equity")), float(summary.get("final_equity") or 0.0)),
        ("Cash", money(runtime.get("Equity", summary.get("final_equity"))), float(runtime.get("Equity", summary.get("final_equity")) or 0.0)),
        ("Net Profit", money(summary.get("total_pnl")), float(summary.get("total_pnl") or 0.0)),
        ("Return", pct(summary.get("return_pct")), float(summary.get("return_pct") or 0.0)),
        ("Realized P/L", money(summary.get("total_pnl")), float(summary.get("total_pnl") or 0.0)),
        ("Unrealized P/L", money(runtime.get("Unrealized", 0.0)), float(runtime.get("Unrealized", 0.0) or 0.0)),
        ("Max Drawdown", pct(summary.get("max_drawdown_pct")), float(summary.get("max_drawdown_pct") or 0.0), True),
        ("Trades", str(summary.get("trade_count", 0)), float(summary.get("trade_count", 0) or 0.0)),
        ("Win Rate", pct(summary.get("win_rate")), float(summary.get("win_rate") or 0.0)),
        ("Profit Factor", num(summary.get("profit_factor")), float(summary.get("profit_factor") or 0.0)),
        ("Sharpe", num(summary.get("sharpe_ratio")), float(summary.get("sharpe_ratio") or 0.0)),
        ("Sortino", num(summary.get("sortino_ratio")), float(summary.get("sortino_ratio") or 0.0)),
        ("Turnover", pct(summary.get("portfolio_turnover")), float(summary.get("portfolio_turnover") or 0.0)),
        ("Volume", money(runtime.get("Volume", 0.0)), float(runtime.get("Volume", 0.0) or 0.0)),
    ]
    cols = st.columns(4)
    for idx, item in enumerate(metrics):
        label, value, raw = item[:3]
        inverse = bool(item[3]) if len(item) > 3 else False
        with cols[idx % 4]:
            metric_card(label, value, raw, inverse)


def render_stats_cards(title: str, stats: dict) -> None:
    st.subheader(title)
    if not stats:
        st.info("No statistics available.")
        return
    keys = list(stats.keys())
    for chunk_start in range(0, len(keys), 4):
        cols = st.columns(4)
        for idx, key in enumerate(keys[chunk_start : chunk_start + 4]):
            value = stats.get(key)
            with cols[idx]:
                st.markdown(
                    f"""
                    <div class="qq-card">
                      <div class="qq-metric-label">{key}</div>
                      <div class="qq-metric-value qq-neutral">{value}</div>
                      <div class="qq-muted" title="Calculated from local backtest artifacts.">?</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


def render_overview(data: dict, period: str) -> None:
    summary = selected_summary(data, period)
    render_metrics(summary)
    daily = filter_df(data["daily"], period)
    portfolio = filter_df(data["portfolio"], period)
    left, right = st.columns(2)
    with left:
        st.subheader("Profit / Loss")
        if not daily.is_empty():
            chart_df = daily.with_columns(
                pl.when(pl.col("pnl") >= 0).then(pl.lit("profit")).otherwise(pl.lit("loss")).alias("direction")
            ).to_pandas()
            chart = (
                alt.Chart(chart_df)
                .mark_bar()
                .encode(
                    x=alt.X("session_date:N", title="Session", axis=alt.Axis(labelAngle=-35)),
                    y=alt.Y("pnl:Q", title="P/L ($)"),
                    color=alt.Color("direction:N", scale=alt.Scale(domain=["profit", "loss"], range=["#0f8a3b", "#c0362c"])),
                    tooltip=list(chart_df.columns),
                )
                .properties(height=300, title="Daily Profit / Loss")
            )
            st.altair_chart(chart, width="stretch")
    with right:
        st.subheader("Equity / Cash")
        if not portfolio.is_empty():
            chart_df = portfolio.select("timestamp", "equity", "cash").to_pandas()
            chart = (
                alt.Chart(chart_df)
                .transform_fold(["equity", "cash"], as_=["series", "value"])
                .mark_line()
                .encode(
                    x=alt.X("timestamp:T", title="Time", axis=alt.Axis(labelOverlap=True, labelAngle=-25)),
                    y=alt.Y("value:Q", title="$"),
                    color=alt.Color("series:N", scale=alt.Scale(range=["#2563eb", "#f59e0b"])),
                    tooltip=["timestamp:T", "series:N", "value:Q"],
                )
                .properties(height=300, title="Equity and Cash")
                .interactive()
            )
            st.altair_chart(chart, width="stretch")
    render_stats_cards("Trade Statistics", summary.get("tradeStatistics", {}))
    render_stats_cards("Portfolio Statistics", summary.get("portfolioStatistics", {}))


def normalize_bar_columns(df: pl.DataFrame) -> pl.DataFrame:
    rename = {}
    if "macd_line" in df.columns:
        rename["macd_line"] = "macd_line_5m"
    if "macd_signal" in df.columns:
        rename["macd_signal"] = "macd_signal_5m"
    if "macd_hist" in df.columns:
        rename["macd_hist"] = "macd_hist_5m"
    if "tema9" in df.columns:
        rename["tema9"] = "tema9_5m"
    if "tema20" in df.columns:
        rename["tema20"] = "tema20_5m"
    return df.rename(rename) if rename else df


def candle_chart(bars: pl.DataFrame, orders: pl.DataFrame, indicators: list[str]) -> None:
    if bars.is_empty():
        st.info("No chart data available.")
        return
    bars = normalize_bar_columns(bars).sort("bar_time_market")
    required = ["bar_time_market", "open", "high", "low", "close"]
    if any(col not in bars.columns for col in required):
        st.info("Selected chart data is missing OHLC columns.")
        return
    base = bars.to_pandas()
    rule = (
        alt.Chart(base)
        .mark_rule()
        .encode(
            x=alt.X("bar_time_market:T", title="Time", axis=alt.Axis(labelOverlap=True, labelAngle=-25)),
            y=alt.Y("low:Q", title="Price"),
            y2="high:Q",
            color=alt.condition("datum.close >= datum.open", alt.value("#0f8a3b"), alt.value("#c0362c")),
        )
    )
    body = (
        alt.Chart(base)
        .mark_bar(size=5)
        .encode(
            x="bar_time_market:T",
            y="open:Q",
            y2="close:Q",
            color=alt.condition("datum.close >= datum.open", alt.value("#0f8a3b"), alt.value("#c0362c")),
            tooltip=list(base.columns),
        )
    )
    chart = rule + body
    overlay_cols = [col for col in indicators if col in bars.columns]
    if overlay_cols:
        lines = (
            alt.Chart(base)
            .transform_fold(overlay_cols, as_=["indicator", "value"])
            .mark_line()
            .encode(x="bar_time_market:T", y="value:Q", color="indicator:N")
        )
        chart = chart + lines
    if not orders.is_empty() and "filled_at" in orders.columns:
        marker_df = orders.filter(pl.col("status") == "FILLED").select(
            pl.col("filled_at").alias("bar_time_market"),
            pl.col("fill_price").alias("price"),
            "side",
            "reason",
            "quantity",
        ).to_pandas()
        markers = (
            alt.Chart(marker_df)
            .mark_point(size=120, filled=True)
            .encode(
                x="bar_time_market:T",
                y="price:Q",
                shape="side:N",
                color="reason:N",
                tooltip=list(marker_df.columns),
            )
        )
        chart = chart + markers
    st.altair_chart(chart.properties(height=560).interactive(), width="stretch")


def bars_for(data: dict, period: str, ticker: str, timeframe: str) -> pl.DataFrame:
    key = "bars_5m" if timeframe == "5m" else "bars_1m"
    bars = filter_df(data[key], period)
    if not bars.is_empty() and "ticker" in bars.columns:
        bars = bars.filter(pl.col("ticker") == ticker)
    return bars


def render_trade_card(trade: dict, selected: bool) -> bool:
    pnl = float(trade.get("pnl") or 0.0)
    label = f"{trade.get('symbol')} | {money(pnl)} | {trade.get('exit_reason', '')}"
    return st.button(label, key=f"trade_{trade.get('symbol')}_{trade.get('entry_time')}", type="primary" if selected else "secondary")


def render_trades(data: dict, period: str) -> None:
    trades = filter_df(data["trades"], period)
    if trades.is_empty():
        st.info("No trades for this period.")
        return
    rows = trades.sort("entry_time").to_dicts()
    selected_key = f"selected_trade_{period}"
    st.session_state.setdefault(selected_key, 0)
    left, right = st.columns([1, 2])
    with left:
        st.subheader("Trades")
        for idx, trade in enumerate(rows):
            if render_trade_card(trade, st.session_state[selected_key] == idx):
                st.session_state[selected_key] = idx
                st.rerun()
            st.caption(f"{trade.get('entry_time')} -> {trade.get('exit_time')} | {pct(trade.get('return_pct'))}")
    trade = rows[min(st.session_state[selected_key], len(rows) - 1)]
    with right:
        st.subheader(f"{trade.get('symbol')} trade chart")
        timeframe = st.segmented_control("Timeframe", ["1m", "5m"], default="1m", key=f"trade_tf_{period}")
        default_indicators = ["vwap", "tema9_5m", "tema20_5m", "macd_line_5m", "macd_signal_5m"]
        indicators = st.multiselect(
            "Indicators",
            ["vwap", "tema9_5m", "tema20_5m", "macd_line_5m", "macd_signal_5m", "macd_hist_5m"],
            default=default_indicators,
            key=f"trade_ind_{period}",
        )
        trade_day = str(trade.get("entry_time", ""))[:10] if period == "Whole Run" else period
        bars = bars_for(data, trade_day, trade["symbol"], timeframe)
        orders = filter_df(data["orders"], trade_day)
        if "symbol" in orders.columns:
            orders = orders.filter(pl.col("symbol") == trade["symbol"])
        candle_chart(bars, orders, indicators)


def render_orders(data: dict, period: str) -> None:
    orders = filter_df(data["orders"], period)
    if orders.is_empty():
        st.info("No orders for this period.")
        return
    for row in orders.sort("created_at").to_dicts():
        title = f"{row.get('created_at')} | {row.get('symbol')} | {row.get('side')} {row.get('order_type')} | {row.get('status')}"
        with st.expander(title):
            cols = st.columns(4)
            cols[0].metric("Quantity", row.get("quantity"))
            cols[1].metric("Fill", money(row.get("fill_price")))
            cols[2].metric("Stop", row.get("stop_price"))
            cols[3].metric("Reason", row.get("reason"))
            st.code(row.get("tag") or "")
            st.json(row, expanded=False)


def render_scanner(data: dict, period: str) -> None:
    setup = filter_df(data["setup_rankings"], period)
    live = filter_df(data["live_rankings"], period)
    ranking_type = st.segmented_control("Ranking", ["Opening setup", "Live minute"], default="Opening setup")
    if ranking_type == "Opening setup":
        st.caption("Fixed setup ranking created after the opening box.")
        st.dataframe(setup.sort("rank") if not setup.is_empty() else setup, width="stretch")
        return
    st.caption("Minute-by-minute live ranking. Search a timestamp such as 10:30 to inspect that minute.")
    if live.is_empty():
        st.info("No live ranking snapshots were saved for this run. Re-run the strategy after this update.")
        return
    times = live.select(pl.col("timestamp").cast(pl.Utf8).alias("timestamp")).unique().sort("timestamp")["timestamp"].to_list()
    default_idx = min(len(times) - 1, max(0, len(times) // 4))
    selected_time = st.selectbox("Live ranking timestamp", times, index=default_idx)
    snapshot = live.filter(pl.col("timestamp").cast(pl.Utf8) == selected_time).sort("live_rank")
    status_filter = st.multiselect("Status", snapshot.select("status").unique()["status"].to_list(), default=[])
    if status_filter:
        snapshot = snapshot.filter(pl.col("status").is_in(status_filter))
    st.dataframe(snapshot, width="stretch")


def render_rejections(data: dict, period: str) -> None:
    rejections = filter_df(data["rejections"], period)
    if rejections.is_empty():
        st.info("No rejections for this period.")
        return
    if "reject_reason" in rejections.columns:
        counts = rejections.group_by("reject_reason").len().sort("len", descending=True)
        st.bar_chart(counts.to_pandas(), x="reject_reason", y="len")
    st.dataframe(rejections.tail(500), width="stretch")


def render_positions(data: dict, period: str) -> None:
    positions = filter_df(data["positions"], period)
    if positions.is_empty():
        st.info("No position snapshots for this period.")
        return
    st.dataframe(positions, width="stretch")


def render_chart_inspector(data: dict, period: str) -> None:
    bars_1m = filter_df(data["bars_1m"], period)
    if bars_1m.is_empty():
        st.info("This run did not save chart bars.")
        return
    tickers = bars_1m.select("ticker").unique().sort("ticker")["ticker"].to_list()
    ticker = st.selectbox("Ticker", tickers)
    timeframe = st.segmented_control("Timeframe", ["1m", "5m"], default="1m", key=f"inspect_tf_{period}")
    indicators = st.multiselect(
        "Indicators",
        ["vwap", "tema9_5m", "tema20_5m", "macd_line_5m", "macd_signal_5m", "macd_hist_5m"],
        default=["vwap", "tema9_5m", "tema20_5m", "macd_line_5m", "macd_signal_5m"],
        key=f"inspect_ind_{period}",
    )
    bars = bars_for(data, period, ticker, timeframe)
    orders = filter_df(data["orders"], period)
    if "symbol" in orders.columns:
        orders = orders.filter(pl.col("symbol") == ticker)
    candle_chart(bars, orders, indicators)


def render_run_dashboard(run_dir: Path) -> None:
    data = load_run_artifacts(str(run_dir), artifact_mtime(run_dir))
    metadata = data["metadata"]
    config = metadata.get("config", {})
    render_run_header(config, metadata.get("status", "unknown"), metadata.get("summary") or data["summary"])
    periods = period_options(data)
    period = st.selectbox("Result Period", periods, key=f"period_{run_dir.name}")
    tabs = st.tabs(["Overview", "Trades", "Orders", "Scanner", "Rejected", "Positions", "Chart Inspector", "Logs"])
    with tabs[0]:
        render_overview(data, period)
    with tabs[1]:
        render_trades(data, period)
    with tabs[2]:
        render_orders(data, period)
    with tabs[3]:
        render_scanner(data, period)
    with tabs[4]:
        render_rejections(data, period)
    with tabs[5]:
        render_positions(data, period)
    with tabs[6]:
        render_chart_inspector(data, period)
    with tabs[7]:
        log_path = run_dir / "logs.txt"
        st.text(log_path.read_text(encoding="utf-8") if log_path.exists() else "No logs.")


def render_new_run_update_form(config_key: str) -> None:
    config = dict(st.session_state[config_key])
    params = dict(config.get("strategy_params", {}))
    with st.form(f"{config_key}_form"):
        config["run_name"] = st.text_input("Run name", value=config["run_name"])
        cols = st.columns(3)
        with cols[0]:
            config["start_date"] = st.date_input("Start date", value=date.fromisoformat(config["start_date"])).isoformat()
            config["end_date"] = st.date_input("End date", value=date.fromisoformat(config["end_date"])).isoformat()
            config["data_root"] = st.text_input("Data root", value=config["data_root"])
        with cols[1]:
            config["initial_cash"] = st.number_input("Initial cash", value=float(config["initial_cash"]), step=1000.0)
            params["max_active_positions"] = st.number_input("Max positions", value=int(params["max_active_positions"]), step=1)
            config["save_symbol_bars"] = st.checkbox("Save chart bars", value=bool(config["save_symbol_bars"]))
        with cols[2]:
            params["watchlist_size"] = st.number_input("Watchlist", value=int(params["watchlist_size"]), step=10)
            params["min_setup_score"] = st.number_input("Min setup score", value=float(params["min_setup_score"]))
            params["min_live_score"] = st.number_input("Min live score", value=float(params["min_live_score"]))
        with st.expander("Advanced parameters"):
            adv_cols = st.columns(4)
            with adv_cols[0]:
                params["min_price"] = st.number_input("Min price", value=float(params["min_price"]))
                params["max_price"] = st.number_input("Max price", value=float(params["max_price"]))
                params["min_avg_daily_volume"] = st.number_input("Min ADV", value=float(params["min_avg_daily_volume"]), step=100_000.0)
            with adv_cols[1]:
                params["min_atr"] = st.number_input("Min ATR", value=float(params["min_atr"]), step=0.05)
                params["min_gap_up_pct"] = st.number_input("Min gap", value=float(params["min_gap_up_pct"]), step=0.001)
                params["min_opening_relative_volume"] = st.number_input("Min opening RV", value=float(params["min_opening_relative_volume"]), step=0.05)
            with adv_cols[2]:
                params["entry_buffer_pct"] = st.number_input("Entry buffer", value=float(params["entry_buffer_pct"]), format="%.4f")
                params["stop_box_pullback_fraction"] = st.number_input("Stop box fraction", value=float(params["stop_box_pullback_fraction"]), step=0.05)
                params["minimum_hold_minutes"] = st.number_input("Min hold", value=int(params["minimum_hold_minutes"]), step=1)
            with adv_cols[3]:
                params["tema_entry_atr_buffer"] = st.number_input("TEMA entry buffer", value=float(params["tema_entry_atr_buffer"]), format="%.4f")
                params["tema_exit_atr_buffer"] = st.number_input("TEMA exit buffer", value=float(params["tema_exit_atr_buffer"]), format="%.4f")
                config["slippage_bps"] = st.number_input("Slippage bps", value=float(config["slippage_bps"]), step=0.5)
        submitted = st.form_submit_button("Save Parameters", type="primary")
    if submitted:
        config["strategy_params"] = params
        st.session_state[config_key] = config
        st.rerun()


if hasattr(st, "dialog"):
    @st.dialog("Update Run Parameters")
    def update_run_parameters_dialog(config_key: str) -> None:
        render_new_run_update_form(config_key)
else:
    update_run_parameters_dialog = None


def run_backtest_live(config: dict) -> str | None:
    status = st.status(f"Running {config['run_name']}", expanded=True)
    progress = st.progress(0)
    live_rows: list[dict] = []
    expected = max(1, len(available_sessions(Path(config["data_root"]), date.fromisoformat(config["start_date"]), date.fromisoformat(config["end_date"]))))
    placeholder = st.empty()

    def on_progress(session_date, daily_summary, run_dir):
        live_rows.append(daily_summary)
        status.write(f"{session_date}: {money(daily_summary.get('pnl'))}")
        progress.progress(min(1.0, len(live_rows) / expected))
        placeholder.dataframe(pl.DataFrame(live_rows), width="stretch")

    try:
        result = run_backtest(config, progress_callback=on_progress)
        status.update(label=f"Completed {config['run_name']}", state="complete", expanded=False)
        return result["run_dir"]
    except Exception as exc:
        status.update(label=f"Run failed: {config['run_name']}", state="error", expanded=True)
        st.error(str(exc))
        with st.expander("Error details"):
            st.code(traceback.format_exc())
        return None


def render_new_run(strategy_name: str, output_root: Path) -> None:
    key = f"{strategy_name}_draft_config"
    if key not in st.session_state:
        st.session_state[key] = default_config(strategy_name, output_root)
    config = st.session_state[key]
    render_run_header(config, "Draft")
    cols = st.columns([1, 1, 3])
    with cols[0]:
        if update_run_parameters_dialog is not None:
            if st.button("Update Run Parameters"):
                update_run_parameters_dialog(key)
        else:
            with st.expander("Update Run Parameters"):
                render_new_run_update_form(key)
    with cols[1]:
        if st.button("Start Backtest", type="primary"):
            sessions = available_sessions(Path(config["data_root"]), date.fromisoformat(config["start_date"]), date.fromisoformat(config["end_date"]))
            if not sessions:
                st.error("No local data files found for this run range.")
            else:
                run_dir = run_backtest_live(config)
                if run_dir:
                    st.session_state["active_run_dir"] = run_dir
                    st.rerun()
    st.markdown("### Run Results")
    st.info("Start the backtest to populate this dashboard. During execution, daily progress appears here.")


def render_runs(strategy_name: str, output_root: Path) -> None:
    runs = list_runs(output_root, strategy_name)
    if not runs:
        st.info("No app-created runs exist for this strategy yet.")
        return
    st.dataframe(pl.DataFrame(run_table_rows(runs)), width="stretch")
    selected = st.selectbox("Open run", runs, format_func=run_label)
    actions = st.columns([1, 1, 4])
    with actions[0]:
        if st.button("Open Selected Run", type="primary"):
            st.session_state["active_run_dir"] = str(selected)
            st.rerun()
    with actions[1]:
        if st.button("Delete Run"):
            st.session_state["delete_run_dir"] = str(selected)
            st.rerun()

    pending_delete = st.session_state.get("delete_run_dir")
    if pending_delete:
        render_delete_run_confirmation(Path(pending_delete), output_root)


def render_delete_run_confirmation(run_dir: Path, output_root: Path) -> None:
    metadata = read_run_metadata(run_dir) or {}
    run_name = metadata.get("run_name", run_dir.name)
    st.warning(f"Delete run and all saved artifacts: {run_name}")
    st.caption(str(run_dir))
    buttons = st.columns([1, 1, 4])
    with buttons[0]:
        if st.button("Confirm Delete", type="primary"):
            if delete_run_folder(run_dir, output_root):
                st.success(f"Deleted {run_name}")
                st.session_state.pop("delete_run_dir", None)
                st.rerun()
    with buttons[1]:
        if st.button("Cancel Delete"):
            st.session_state.pop("delete_run_dir", None)
            st.rerun()


def delete_run_folder(run_dir: Path, output_root: Path) -> bool:
    try:
        resolved_root = output_root.resolve()
        resolved_run = run_dir.resolve()
    except OSError as exc:
        st.error(f"Could not resolve run path: {exc}")
        return False

    if not resolved_run.exists():
        st.error("Run folder no longer exists.")
        return False
    if resolved_root != resolved_run and resolved_root not in resolved_run.parents:
        st.error("Refusing to delete a folder outside the configured runs root.")
        return False

    metadata = read_run_metadata(resolved_run)
    if not metadata or not metadata.get("created_by_app"):
        st.error("Refusing to delete a run that was not created by the app.")
        return False

    try:
        shutil.rmtree(resolved_run)
        return True
    except OSError as exc:
        st.error(f"Could not delete run folder: {exc}")
        return False


def strategy_workspace(strategy_name: str) -> None:
    st.title(strategy_name)
    st.caption(STRATEGY_DESCRIPTIONS.get(strategy_name, "No description available."))
    output_root = Path(st.text_input("Runs root", value=str(DEFAULT_OUTPUT_ROOT)))
    active_run = st.session_state.get("active_run_dir")
    if active_run:
        cols = st.columns([1, 5])
        with cols[0]:
            if st.button("Back to Runs"):
                st.session_state.pop("active_run_dir", None)
                st.rerun()
        render_run_dashboard(Path(active_run))
        return
    tabs = st.tabs(["Runs", "New Run", "Strategy README"])
    with tabs[0]:
        render_runs(strategy_name, output_root)
    with tabs[1]:
        render_new_run(strategy_name, output_root)
    with tabs[2]:
        st.markdown(strategy_readme(strategy_name))


def main() -> None:
    st.set_page_config(page_title="QQ Momentum Backtests", layout="wide")
    install_css()
    st.sidebar.title("Strategies")
    strategy_name = st.sidebar.radio("Select strategy", available_strategies(), label_visibility="collapsed")
    strategy_workspace(strategy_name)


if __name__ == "__main__":
    main()
