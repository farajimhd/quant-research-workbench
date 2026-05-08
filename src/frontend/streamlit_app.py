from __future__ import annotations

import json
import sys
import traceback
from datetime import date
from pathlib import Path

import altair as alt
import polars as pl
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest.results import list_runs, read_run_metadata
from src.backtest.runner import run_backtest
from src.strategies.orb_5m_momentum.config import OrbMomentumConfig
from src.strategies.registry import available_strategies


DEFAULT_DATA_ROOT = Path("D:/TradingData/massive_flatfiles/us_stock_sip/minutes_agg_v1")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingData/qq-momentum-trading/runs")


STRATEGY_DESCRIPTIONS = {
    "orb_5m_momentum": (
        "Opening-range momentum strategy using a 09:30-09:35 box, top-100 setup ranking, "
        "live minute reranking, and completed 5-minute MACD/TEMA confirmation."
    )
}


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        st.error(f"Could not parse {path.name}: {exc}")
        return {}


def read_parquet(path: Path) -> pl.DataFrame:
    try:
        if not path.exists():
            return pl.DataFrame()
        return pl.read_parquet(path)
    except Exception as exc:
        st.error(f"Could not read {path.name}: {exc}")
        return pl.DataFrame()


def pct(value) -> str:
    return f"{float(value or 0.0) * 100:.2f}%"


def money(value) -> str:
    return f"${float(value or 0.0):,.2f}"


def number(value, decimals: int = 2) -> str:
    return f"{float(value or 0.0):,.{decimals}f}"


def strategy_readme(strategy_name: str) -> str:
    readme = PROJECT_ROOT / "src" / "strategies" / strategy_name / "README.md"
    if readme.exists():
        return readme.read_text(encoding="utf-8")
    return "No strategy README found."


def run_label(run_dir: Path) -> str:
    metadata = read_run_metadata(run_dir) or {}
    summary = metadata.get("summary") or load_json(run_dir / "summary.json")
    run_name = metadata.get("run_name", run_dir.name)
    status = metadata.get("status", "unknown")
    created_at = metadata.get("created_at", "")
    result = summary.get("return_pct", 0.0)
    return f"{run_name} | {status} | {pct(result)} | {created_at}"


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


def default_strategy_params() -> dict:
    return OrbMomentumConfig().to_dict()


def minute_file_path(data_root: Path, session: date) -> Path:
    return data_root / f"{session.year:04d}" / f"{session.month:02d}" / f"{session.isoformat()}.csv.gz"


def available_sessions_for_form(data_root: Path, start: date, end: date) -> list[date]:
    sessions = []
    cursor = start
    while cursor <= end:
        if minute_file_path(data_root, cursor).exists():
            sessions.append(cursor)
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return sessions


def build_run_config(strategy_name: str, output_root: Path) -> dict | None:
    defaults = default_strategy_params()

    with st.form("new_run_form"):
        run_name = st.text_input("Run name", placeholder="May 2024 baseline ORB", key=f"{strategy_name}_run_name")

        dataset, portfolio, execution = st.columns(3)
        with dataset:
            st.markdown("**Dataset**")
            start_date = st.date_input("Start date", value=date(2024, 5, 1), key=f"{strategy_name}_start_date")
            end_date = st.date_input("End date", value=date(2024, 5, 31), key=f"{strategy_name}_end_date")
            data_root = Path(st.text_input("Data root", value=str(DEFAULT_DATA_ROOT), key=f"{strategy_name}_data_root"))
            market_utc_offset = st.number_input("Market UTC offset", value=-4.0, step=1.0, key=f"{strategy_name}_market_offset")
        with portfolio:
            st.markdown("**Portfolio**")
            initial_cash = st.number_input(
                "Initial cash", min_value=1000.0, value=10_000.0, step=1000.0, key=f"{strategy_name}_initial_cash"
            )
            max_active = st.number_input(
                "Max active positions", value=int(defaults["max_active_positions"]), step=1, key=f"{strategy_name}_max_active"
            )
            cash_reserve = st.number_input(
                "Cash reserve %", value=float(defaults["cash_reserve_pct"]) * 100, step=1.0, key=f"{strategy_name}_cash_reserve"
            )
            save_symbol_bars = st.checkbox("Save symbol bars for chart inspector", value=True, key=f"{strategy_name}_save_bars")
        with execution:
            st.markdown("**Fill Model**")
            slippage_bps = st.number_input("Slippage bps", min_value=0.0, value=2.0, step=0.5, key=f"{strategy_name}_slippage")
            output_root_value = Path(st.text_input("Output root", value=str(output_root), key=f"{strategy_name}_output_root"))

        requested_days = max(0, (end_date - start_date).days + 1)
        available_sessions = available_sessions_for_form(data_root, start_date, end_date) if end_date >= start_date else []
        if available_sessions:
            st.info(
                f"Resolved sessions: {len(available_sessions)} file(s) inside {requested_days} requested calendar day(s). "
                f"First: {available_sessions[0].isoformat()}, last: {available_sessions[-1].isoformat()}."
            )
        else:
            st.warning("No available minute-bar files were found for the selected date range.")

        with st.expander("Scanner Parameters", expanded=True):
            scanner_cols = st.columns(4)
            with scanner_cols[0]:
                min_price = st.number_input("Min price", value=float(defaults["min_price"]), key=f"{strategy_name}_min_price")
                max_price = st.number_input("Max price", value=float(defaults["max_price"]), key=f"{strategy_name}_max_price")
            with scanner_cols[1]:
                min_avg_daily_volume = st.number_input(
                    "Min avg daily volume", value=float(defaults["min_avg_daily_volume"]), step=100_000.0, key=f"{strategy_name}_min_adv"
                )
                min_atr = st.number_input("Min ATR", value=float(defaults["min_atr"]), step=0.05, key=f"{strategy_name}_min_atr")
            with scanner_cols[2]:
                watchlist_size = st.number_input("Watchlist size", value=int(defaults["watchlist_size"]), step=10, key=f"{strategy_name}_watchlist")
                min_setup_score = st.number_input("Min setup score", value=float(defaults["min_setup_score"]), key=f"{strategy_name}_min_setup")
            with scanner_cols[3]:
                min_gap_up_pct = st.number_input("Min gap", value=float(defaults["min_gap_up_pct"]), step=0.001, key=f"{strategy_name}_min_gap")
                min_opening_relative_volume = st.number_input(
                    "Min opening RV", value=float(defaults["min_opening_relative_volume"]), step=0.05, key=f"{strategy_name}_min_orv"
                )

        with st.expander("Entry, Exit, and Risk Parameters"):
            cols = st.columns(4)
            with cols[0]:
                min_live_score = st.number_input("Min live score", value=float(defaults["min_live_score"]), key=f"{strategy_name}_min_live")
                entry_buffer_pct = st.number_input(
                    "Entry buffer %", value=float(defaults["entry_buffer_pct"]) * 100, step=0.01, key=f"{strategy_name}_entry_buffer"
                )
            with cols[1]:
                stop_box_pullback_fraction = st.number_input(
                    "Stop box pullback fraction", value=float(defaults["stop_box_pullback_fraction"]), step=0.05, key=f"{strategy_name}_stop_frac"
                )
                minimum_hold_minutes = st.number_input(
                    "Minimum hold minutes", value=int(defaults["minimum_hold_minutes"]), step=1, key=f"{strategy_name}_min_hold"
                )
            with cols[2]:
                tema_entry_atr_buffer = st.number_input(
                    "TEMA entry ATR buffer", value=float(defaults["tema_entry_atr_buffer"]), step=0.001, format="%.4f", key=f"{strategy_name}_tema_entry"
                )
                tema_exit_atr_buffer = st.number_input(
                    "TEMA exit ATR buffer", value=float(defaults["tema_exit_atr_buffer"]), step=0.001, format="%.4f", key=f"{strategy_name}_tema_exit"
                )
            with cols[3]:
                min_risk_pct = st.number_input("Min risk %", value=float(defaults["min_risk_pct"]) * 100, step=0.05, key=f"{strategy_name}_min_risk")
                max_risk_pct = st.number_input("Max risk %", value=float(defaults["max_risk_pct"]) * 100, step=0.05, key=f"{strategy_name}_max_risk")

        submitted = st.form_submit_button("Start Run", type="primary")
        if not submitted:
            return None

    if not run_name.strip():
        st.error("Run name is required.")
        return None
    if end_date < start_date:
        st.error("End date must be on or after start date.")
        return None
    if not data_root.exists():
        st.error(f"Data root does not exist: {data_root}")
        return None
    if not available_sessions:
        st.error("No available session files found for that date range. The run was not started.")
        return None

    return {
        "run_name": run_name.strip(),
        "strategy_name": strategy_name,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "data_root": str(data_root),
        "output_root": str(output_root_value),
        "initial_cash": initial_cash,
        "market_utc_offset_hours": market_utc_offset,
        "slippage_bps": slippage_bps,
        "save_symbol_bars": save_symbol_bars,
        "created_by_app": True,
        "strategy_params": {
            "min_price": min_price,
            "max_price": max_price,
            "min_avg_daily_volume": min_avg_daily_volume,
            "min_atr": min_atr,
            "watchlist_size": int(watchlist_size),
            "max_active_positions": int(max_active),
            "cash_reserve_pct": cash_reserve / 100.0,
            "min_setup_score": min_setup_score,
            "min_live_score": min_live_score,
            "min_gap_up_pct": min_gap_up_pct,
            "min_opening_relative_volume": min_opening_relative_volume,
            "entry_buffer_pct": entry_buffer_pct / 100.0,
            "stop_box_pullback_fraction": stop_box_pullback_fraction,
            "minimum_hold_minutes": int(minimum_hold_minutes),
            "tema_entry_atr_buffer": tema_entry_atr_buffer,
            "tema_exit_atr_buffer": tema_exit_atr_buffer,
            "min_risk_pct": min_risk_pct / 100.0,
            "max_risk_pct": max_risk_pct / 100.0,
        },
    }


def run_backtest_with_live_view(config: dict) -> None:
    status = st.status(f"Running {config['run_name']}", expanded=True)
    progress_bar = st.progress(0)
    daily_placeholder = st.empty()
    live_rows = []
    expected_sessions = max(
        1,
        (date.fromisoformat(config["end_date"]) - date.fromisoformat(config["start_date"])).days + 1,
    )

    def on_progress(session_date, daily_summary, run_dir):
        live_rows.append(daily_summary)
        completed = len(live_rows)
        status.write(f"Completed {session_date}: {money(daily_summary.get('pnl'))}")
        progress_bar.progress(min(1.0, completed / expected_sessions))
        daily_placeholder.dataframe(pl.DataFrame(live_rows), width="stretch")

    try:
        result = run_backtest(config, progress_callback=on_progress)
        status.update(label=f"Completed {config['run_name']}", state="complete", expanded=False)
        st.session_state["active_run_dir"] = result["run_dir"]
        st.success(f"Backtest complete: {result['run_dir']}")
    except Exception as exc:
        status.update(label=f"Run failed: {config['run_name']}", state="error", expanded=True)
        st.error(str(exc))
        with st.expander("Error details"):
            st.code(traceback.format_exc())


def metric_grid(summary: dict) -> None:
    cells = [
        ("Final Equity", money(summary.get("final_equity"))),
        ("Net Profit", money(summary.get("total_pnl"))),
        ("Return", pct(summary.get("return_pct"))),
        ("Max Drawdown", pct(summary.get("max_drawdown_pct"))),
        ("Trades", str(summary.get("trade_count", 0))),
        ("Win Rate", pct(summary.get("win_rate"))),
        ("Profit Factor", number(summary.get("profit_factor"))),
        ("Sharpe", number(summary.get("sharpe_ratio"))),
        ("Sortino", number(summary.get("sortino_ratio"))),
        ("Turnover", pct(summary.get("portfolio_turnover"))),
    ]
    columns = st.columns(5)
    for index, (label, value) in enumerate(cells):
        columns[index % 5].metric(label, value)


def show_overview(run_dir: Path, summary: dict) -> None:
    metric_grid(summary)
    portfolio = read_parquet(run_dir / "portfolio.parquet")
    if portfolio.height:
        chart_df = portfolio.select("timestamp", "equity", "cash", "open_positions").to_pandas()
        st.line_chart(chart_df, x="timestamp", y=["equity", "cash"])
    stats_cols = st.columns(2)
    with stats_cols[0]:
        st.markdown("**Trade Statistics**")
        st.json(summary.get("tradeStatistics", {}), expanded=False)
    with stats_cols[1]:
        st.markdown("**Portfolio Statistics**")
        st.json(summary.get("portfolioStatistics", {}), expanded=False)


def candlestick_chart(symbol_bars: pl.DataFrame, orders: pl.DataFrame) -> None:
    if symbol_bars.is_empty():
        st.info("No bars available for this symbol.")
        return

    base = symbol_bars.select("bar_time_market", "open", "high", "low", "close", "vwap", "tema9_5m", "tema20_5m").to_pandas()
    rules = (
        alt.Chart(base)
        .mark_rule()
        .encode(x="bar_time_market:T", y="low:Q", y2="high:Q", color=alt.condition("datum.close >= datum.open", alt.value("#0f9d58"), alt.value("#d93025")))
    )
    bars = (
        alt.Chart(base)
        .mark_bar(size=4)
        .encode(x="bar_time_market:T", y="open:Q", y2="close:Q", color=alt.condition("datum.close >= datum.open", alt.value("#0f9d58"), alt.value("#d93025")))
    )
    lines = (
        alt.Chart(base)
        .transform_fold(["vwap", "tema9_5m", "tema20_5m"], as_=["indicator", "value"])
        .mark_line()
        .encode(x="bar_time_market:T", y="value:Q", color="indicator:N")
    )
    chart = (rules + bars + lines).properties(height=520)

    if orders.height and "filled_at" in orders.columns:
        markers_df = (
            orders.filter(pl.col("status") == "FILLED")
            .select(pl.col("filled_at").alias("bar_time_market"), pl.col("fill_price").alias("price"), "side", "reason")
            .to_pandas()
        )
        markers = (
            alt.Chart(markers_df)
            .mark_point(size=100, filled=True)
            .encode(x="bar_time_market:T", y="price:Q", shape="side:N", color="reason:N", tooltip=list(markers_df.columns))
        )
        chart = chart + markers

    st.altair_chart(chart, width="stretch")


def show_chart_inspector(run_dir: Path) -> None:
    bars = read_parquet(run_dir / "symbol_bars.parquet")
    if bars.is_empty():
        st.info("This run did not save symbol bars. Enable that option in the next run.")
        return

    orders = read_parquet(run_dir / "orders.parquet")
    positions = read_parquet(run_dir / "positions.parquet")
    scanner = read_parquet(run_dir / "candidate_rankings.parquet")
    rejections = read_parquet(run_dir / "rejection_events.parquet")

    sessions = bars.select("session_date").unique().sort("session_date")["session_date"].to_list()
    session = st.selectbox("Session", sessions)
    day_bars = bars.filter(pl.col("session_date") == session)

    active_symbols = []
    if orders.height:
        active_symbols.extend(orders.filter(pl.col("status") == "FILLED").select("symbol").unique()["symbol"].to_list())
    active_symbols.extend(scanner.filter(pl.col("session_date") == session).select("ticker").head(25)["ticker"].to_list() if scanner.height else [])
    tickers = sorted(set(active_symbols)) or day_bars.select("ticker").unique().sort("ticker")["ticker"].to_list()
    ticker = st.selectbox("Ticker", tickers)
    symbol_bars = day_bars.filter(pl.col("ticker") == ticker).sort("bar_time_market")
    symbol_orders = orders.filter(pl.col("symbol") == ticker) if orders.height and "symbol" in orders.columns else pl.DataFrame()

    left, right = st.columns([3, 1])
    with left:
        candlestick_chart(symbol_bars, symbol_orders)
    with right:
        st.markdown("**Scanner State**")
        if scanner.height:
            st.dataframe(scanner.filter((pl.col("session_date") == session) & (pl.col("ticker") == ticker)), width="stretch")
        st.markdown("**Open Position Snapshots**")
        if positions.height and "symbol" in positions.columns:
            st.dataframe(positions.filter(pl.col("symbol") == ticker).tail(20), width="stretch")

    st.markdown("**Orders**")
    st.dataframe(symbol_orders, width="stretch")
    if rejections.height and "ticker" in rejections.columns:
        st.markdown("**Rejected Signals**")
        st.dataframe(rejections.filter(pl.col("ticker") == ticker).tail(100), width="stretch")


def show_config_dialog(run_dir: Path) -> None:
    metadata = read_run_metadata(run_dir) or {}
    config = metadata.get("config") or load_json(run_dir / "config.json")
    edited = st.text_area("Saved configuration", json.dumps(config, indent=2), height=500)
    if st.button("Run Again With Edited Config"):
        try:
            new_config = json.loads(edited)
            new_config["run_name"] = f"{new_config.get('run_name', 'run')} copy"
            run_backtest_with_live_view(new_config)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")


if hasattr(st, "dialog"):
    @st.dialog("Run Parameters")
    def run_parameters_dialog(run_dir_value: str) -> None:
        show_config_dialog(Path(run_dir_value))
else:
    run_parameters_dialog = None


def show_run_detail(run_dir: Path) -> None:
    metadata = read_run_metadata(run_dir) or {}
    summary = metadata.get("summary") or load_json(run_dir / "summary.json")
    run_name = metadata.get("run_name", run_dir.name)

    top = st.columns([3, 1, 1])
    with top[0]:
        st.header(run_name)
        st.caption(f"{metadata.get('strategy_name', '')} | {metadata.get('status', 'unknown')} | {run_dir}")
    with top[1]:
        if st.button("Back to Runs"):
            st.session_state.pop("active_run_dir", None)
            st.rerun()
    with top[2]:
        if run_parameters_dialog is not None:
            if st.button("View / Edit Parameters"):
                run_parameters_dialog(str(run_dir))
        else:
            config_popover = st.popover("View / Edit Parameters")
            with config_popover:
                show_config_dialog(run_dir)

    tabs = st.tabs(["Overview", "Daily", "Trades", "Orders", "Scanner", "Rejected", "Positions", "Chart Inspector", "Logs"])
    with tabs[0]:
        show_overview(run_dir, summary)
    with tabs[1]:
        st.dataframe(read_parquet(run_dir / "daily_summary.parquet"), width="stretch")
    with tabs[2]:
        st.dataframe(read_parquet(run_dir / "trades.parquet"), width="stretch")
    with tabs[3]:
        st.dataframe(read_parquet(run_dir / "orders.parquet"), width="stretch")
    with tabs[4]:
        st.dataframe(read_parquet(run_dir / "candidate_rankings.parquet"), width="stretch")
    with tabs[5]:
        st.dataframe(read_parquet(run_dir / "rejection_events.parquet"), width="stretch")
    with tabs[6]:
        st.dataframe(read_parquet(run_dir / "positions.parquet"), width="stretch")
    with tabs[7]:
        show_chart_inspector(run_dir)
    with tabs[8]:
        log_path = run_dir / "logs.txt"
        st.text(log_path.read_text(encoding="utf-8") if log_path.exists() else "No logs.")


def strategy_workspace(strategy_name: str) -> None:
    st.title(strategy_name)
    st.caption(STRATEGY_DESCRIPTIONS.get(strategy_name, "No description available."))

    output_root = Path(st.text_input("Runs root", value=str(DEFAULT_OUTPUT_ROOT)))
    active_run = st.session_state.get("active_run_dir")
    if active_run:
        show_run_detail(Path(active_run))
        return

    tabs = st.tabs(["Runs", "New Run", "Strategy README"])
    with tabs[0]:
        runs = list_runs(output_root, strategy_name)
        st.subheader("Runs")
        if not runs:
            st.info("No app-created runs exist for this strategy yet.")
        else:
            st.dataframe(pl.DataFrame(run_table_rows(runs)), width="stretch")
            selected = st.selectbox("Open run", runs, format_func=run_label)
            if st.button("Open Selected Run", type="primary"):
                st.session_state["active_run_dir"] = str(selected)
                st.rerun()
    with tabs[1]:
        st.subheader("Create Run")
        config = build_run_config(strategy_name, output_root)
        if config is not None:
            run_backtest_with_live_view(config)
    with tabs[2]:
        st.markdown(strategy_readme(strategy_name))


def main() -> None:
    st.set_page_config(page_title="QQ Momentum Backtests", layout="wide")
    st.sidebar.title("Strategies")
    strategy_name = st.sidebar.radio("Select strategy", available_strategies(), label_visibility="collapsed")
    strategy_workspace(strategy_name)


if __name__ == "__main__":
    main()
