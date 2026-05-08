from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import polars as pl
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest.results import list_runs
from src.backtest.runner import run_backtest
from src.strategies.orb_5m_momentum.config import OrbMomentumConfig
from src.strategies.registry import available_strategies


DEFAULT_DATA_ROOT = Path("D:/TradingData/massive_flatfiles/us_stock_sip/minutes_agg_v1")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingData/qq-momentum-trading/runs")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_parquet(path: Path) -> pl.DataFrame:
    if not path.exists():
        return pl.DataFrame()
    return pl.read_parquet(path)


def build_config(strategy_name: str) -> dict:
    st.subheader("Run Parameters")
    start_date = st.date_input("Start date", value=date(2024, 5, 1))
    end_date = st.date_input("End date", value=date(2024, 5, 31))
    initial_cash = st.number_input("Initial cash", min_value=1000.0, value=10_000.0, step=1000.0)
    data_root = Path(st.text_input("Data root", value=str(DEFAULT_DATA_ROOT)))
    output_root = Path(st.text_input("Output root", value=str(DEFAULT_OUTPUT_ROOT)))
    slippage_bps = st.number_input("Slippage bps", min_value=0.0, value=2.0, step=0.5)
    save_symbol_bars = st.checkbox("Save symbol bars for charts", value=True)

    st.subheader("Strategy Parameters")
    defaults = OrbMomentumConfig().to_dict()
    params = {}
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        params["min_price"] = st.number_input("Min price", value=float(defaults["min_price"]))
        params["max_price"] = st.number_input("Max price", value=float(defaults["max_price"]))
        params["min_avg_daily_volume"] = st.number_input(
            "Min avg daily volume", value=float(defaults["min_avg_daily_volume"]), step=100_000.0
        )
        params["min_atr"] = st.number_input("Min ATR", value=float(defaults["min_atr"]), step=0.05)
    with col_b:
        params["watchlist_size"] = st.number_input("Watchlist size", value=int(defaults["watchlist_size"]), step=10)
        params["max_active_positions"] = st.number_input(
            "Max active positions", value=int(defaults["max_active_positions"]), step=1
        )
        params["min_setup_score"] = st.number_input("Min setup score", value=float(defaults["min_setup_score"]))
        params["min_live_score"] = st.number_input("Min live score", value=float(defaults["min_live_score"]))
    with col_c:
        params["min_gap_up_pct"] = st.number_input("Min gap", value=float(defaults["min_gap_up_pct"]), step=0.001)
        params["min_opening_relative_volume"] = st.number_input(
            "Min opening RV", value=float(defaults["min_opening_relative_volume"]), step=0.05
        )
        params["tema_entry_atr_buffer"] = st.number_input(
            "TEMA entry ATR buffer", value=float(defaults["tema_entry_atr_buffer"]), step=0.001, format="%.4f"
        )
        params["tema_exit_atr_buffer"] = st.number_input(
            "TEMA exit ATR buffer", value=float(defaults["tema_exit_atr_buffer"]), step=0.001, format="%.4f"
        )

    return {
        "strategy_name": strategy_name,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "data_root": str(data_root),
        "output_root": str(output_root),
        "initial_cash": initial_cash,
        "market_utc_offset_hours": -4.0,
        "slippage_bps": slippage_bps,
        "save_symbol_bars": save_symbol_bars,
        "strategy_params": params,
    }


def show_summary(run_dir: Path) -> None:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        st.warning("Selected run does not have a summary yet.")
        return

    summary = load_json(summary_path)
    st.subheader("Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Final equity", f"${summary.get('final_equity', 0):,.2f}")
    c2.metric("Total PnL", f"${summary.get('total_pnl', 0):,.2f}")
    c3.metric("Return", f"{summary.get('return_pct', 0) * 100:.2f}%")
    c4.metric("Trades", f"{summary.get('trade_count', 0)}")

    portfolio = read_parquet(run_dir / "portfolio.parquet")
    if portfolio.height:
        st.line_chart(portfolio.select("timestamp", "equity").to_pandas(), x="timestamp", y="equity")

    tabs = st.tabs(["Daily", "Orders", "Trades", "Scanner", "Inspect Day"])
    with tabs[0]:
        st.dataframe(read_parquet(run_dir / "daily_summary.parquet"))
    with tabs[1]:
        st.dataframe(read_parquet(run_dir / "orders.parquet"))
    with tabs[2]:
        st.dataframe(read_parquet(run_dir / "trades.parquet"))
    with tabs[3]:
        st.dataframe(read_parquet(run_dir / "candidate_rankings.parquet"))
    with tabs[4]:
        show_day_inspector(run_dir)


def show_day_inspector(run_dir: Path) -> None:
    bars = read_parquet(run_dir / "symbol_bars.parquet")
    if bars.is_empty():
        st.info("This run did not save symbol bars.")
        return

    sessions = bars.select("session_date").unique().sort("session_date")["session_date"].to_list()
    session = st.selectbox("Session", sessions)
    day_bars = bars.filter(pl.col("session_date") == session)
    tickers = day_bars.select("ticker").unique().sort("ticker")["ticker"].to_list()
    ticker = st.selectbox("Ticker", tickers)
    symbol_bars = day_bars.filter(pl.col("ticker") == ticker).sort("bar_time_market")

    trades = read_parquet(run_dir / "trades.parquet")
    orders = read_parquet(run_dir / "orders.parquet")
    st.dataframe(orders.filter(pl.col("symbol") == ticker) if orders.height else orders)

    chart_df = symbol_bars.select("bar_time_market", "close", "vwap", "tema9_5m", "tema20_5m").to_pandas()
    st.line_chart(chart_df, x="bar_time_market", y=["close", "vwap", "tema9_5m", "tema20_5m"])
    if trades.height:
        st.dataframe(trades.filter(pl.col("symbol") == ticker))


def main() -> None:
    st.set_page_config(page_title="QQ Momentum Backtests", layout="wide")
    st.title("QQ Momentum Backtests")

    strategy_name = st.sidebar.selectbox("Strategy", available_strategies())
    output_root = Path(st.sidebar.text_input("Runs root", value=str(DEFAULT_OUTPUT_ROOT)))
    runs = list_runs(output_root, strategy_name)
    run_labels = ["New run"] + [run.name for run in runs]
    selected_run = st.sidebar.selectbox("Run", run_labels)

    if selected_run == "New run":
        config = build_config(strategy_name)
        if st.button("Run backtest", type="primary"):
            progress = st.empty()

            def on_progress(session_date, daily_summary, run_dir):
                progress.write(f"Completed {session_date}: PnL ${daily_summary['pnl']:,.2f}")

            result = run_backtest(config, progress_callback=on_progress)
            st.success(f"Backtest complete: {result['run_dir']}")
            show_summary(Path(result["run_dir"]))
    else:
        show_summary(output_root / selected_run)


if __name__ == "__main__":
    main()
