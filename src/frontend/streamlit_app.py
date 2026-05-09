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
import streamlit.components.v1 as components

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest.metrics import compute_summary
from src.backtest.indicators import add_standard_indicators
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

SUMMARY_METRIC_LAYOUT = {
    "rows": [
        ["Final Equity", "Cash", "Net Profit", "Return", "Realized P/L", "Unrealized P/L", "Max Drawdown"],
        ["Trades", "Win Rate", "Profit Factor", "Sharpe", "Sortino", "Turnover", "Volume"],
    ],
}

PRICE_CHART_INDICATORS = ["vwap", "tema9", "tema20"]

OSCILLATOR_CHART_INDICATORS = ["macd_line", "macd_signal", "macd_hist"]

CHART_INDICATORS = PRICE_CHART_INDICATORS + OSCILLATOR_CHART_INDICATORS

DEFAULT_INDICATOR_COLORS = {
    "vwap": "#0891b2",
    "tema9": "#2563eb",
    "tema20": "#db2777",
    "macd_line": "#16a34a",
    "macd_signal": "#f59e0b",
    "macd_hist": "#7c3aed",
}

DEFAULT_INDICATOR_WIDTHS = {
    "vwap": 1,
    "tema9": 1,
    "tema20": 1,
    "macd_line": 1,
    "macd_signal": 1,
    "macd_hist": 2,
}

DEFAULT_CANDLE_CHART_SETTINGS = {
    "upColor": "#059669",
    "downColor": "#dc2626",
    "borderUpColor": "#047857",
    "borderDownColor": "#b91c1c",
    "wickUpColor": "#065f46",
    "wickDownColor": "#991b1b",
    "borderVisible": True,
    "wickVisible": True,
    "priceLineVisible": True,
    "barSpacing": 18,
    "minBarSpacing": 7,
    "rightOffset": 2,
}

TRADE_STAT_GROUPS = {
    "Trade Count": [
        ("totalNumberOfTrades", "Total Trades"),
        ("numberOfWinningTrades", "Winning Trades"),
        ("numberOfLosingTrades", "Losing Trades"),
        ("winRate", "Win Rate"),
        ("lossRate", "Loss Rate"),
    ],
    "Profit And Loss": [
        ("totalProfitLoss", "Total P/L"),
        ("totalProfit", "Gross Profit"),
        ("totalLoss", "Gross Loss"),
        ("averageProfitLoss", "Avg P/L"),
        ("largestProfit", "Largest Profit"),
        ("largestLoss", "Largest Loss"),
    ],
    "Trade Quality": [
        ("profitFactor", "Profit Factor"),
        ("profitLossRatio", "Profit/Loss Ratio"),
        ("winLossRatio", "Win/Loss Ratio"),
        ("sharpeRatio", "Trade Sharpe"),
        ("sortinoRatio", "Trade Sortino"),
    ],
    "Timing": [
        ("averageTradeDuration", "Avg Duration"),
        ("medianTradeDuration", "Median Duration"),
        ("averageWinningTradeDuration", "Avg Win Duration"),
        ("averageLosingTradeDuration", "Avg Loss Duration"),
    ],
    "Streaks And Excursion": [
        ("maxConsecutiveWinningTrades", "Max Win Streak"),
        ("maxConsecutiveLosingTrades", "Max Loss Streak"),
        ("averageMAE", "Avg MAE"),
        ("averageMFE", "Avg MFE"),
        ("largestMAE", "Largest MAE"),
        ("largestMFE", "Largest MFE"),
    ],
    "Drawdown And Costs": [
        ("maximumClosedTradeDrawdown", "Closed DD"),
        ("maximumIntraTradeDrawdown", "Intra Trade DD"),
        ("profitToMaxDrawdownRatio", "Profit/DD"),
        ("totalFees", "Fees"),
    ],
}

PORTFOLIO_STAT_GROUPS = {
    "Equity": [
        ("startEquity", "Start Equity"),
        ("endEquity", "End Equity"),
        ("totalNetProfit", "Net Return"),
        ("compoundingAnnualReturn", "Annual Return"),
    ],
    "Risk": [
        ("drawdown", "Drawdown"),
        ("valueAtRisk95", "VaR 95"),
        ("valueAtRisk99", "VaR 99"),
        ("drawdownRecovery", "DD Recovery Bars"),
    ],
    "Risk Adjusted": [
        ("sharpeRatio", "Sharpe"),
        ("sortinoRatio", "Sortino"),
        ("annualStandardDeviation", "Annual Std Dev"),
        ("annualVariance", "Annual Variance"),
    ],
    "Trade Edge": [
        ("winRate", "Win Rate"),
        ("lossRate", "Loss Rate"),
        ("averageWinRate", "Avg Win Rate"),
        ("averageLossRate", "Avg Loss Rate"),
        ("profitLossRatio", "Profit/Loss Ratio"),
        ("expectancy", "Expectancy"),
    ],
    "Activity": [
        ("portfolioTurnover", "Turnover"),
    ],
}

PERCENT_KEYS = {
    "winRate",
    "lossRate",
    "averageWinRate",
    "averageLossRate",
    "totalNetProfit",
    "compoundingAnnualReturn",
    "drawdown",
    "annualStandardDeviation",
    "annualVariance",
    "valueAtRisk99",
    "valueAtRisk95",
}

MONEY_KEYS = {
    "totalProfitLoss",
    "totalProfit",
    "totalLoss",
    "largestProfit",
    "largestLoss",
    "averageProfitLoss",
    "averageProfit",
    "averageLoss",
    "averageMAE",
    "averageMFE",
    "largestMAE",
    "largestMFE",
    "maximumClosedTradeDrawdown",
    "maximumIntraTradeDrawdown",
    "maximumEndTradeDrawdown",
    "averageEndTradeDrawdown",
    "totalFees",
    "startEquity",
    "endEquity",
}


def install_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 100%;
            padding: 2.25rem 2rem 2rem;
        }
        [data-testid="stSidebar"] > div:first-child {
            padding: 1rem 1rem;
        }
        h1 {
            font-size: 1.45rem !important;
            line-height: 1.2 !important;
            margin-bottom: 0.2rem !important;
        }
        .st-key-run_header h1 {
            margin: 0 !important;
        }
        .st-key-run_header pre {
            margin: 0;
            padding: 0;
            line-height: 1.25;
            white-space: pre-wrap;
        }
        .st-key-run_header [data-testid="stButton"] {
            display: flex;
            align-items: center;
        }
        .qq-run-summary {
            color: #4b5563;
            font-size: 0.86rem;
            line-height: 1.3;
            margin: 0.05rem 0 0.1rem 0;
        }
        .st-key-back_to_runs button {
            min-width: 2rem;
            height: 2rem;
            min-height: 2rem;
            margin-top: 0;
            padding: 0;
            border-radius: 999px;
            color: #6b7280;
            background: #f3f4f6;
            border: 1px solid #e5e7eb;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            line-height: 1;
        }
        .qq-period-label {
            color: #4b5563;
            font-size: 0.86rem;
            font-weight: 600;
            line-height: 2.35rem;
            margin: 0;
        }
        .st-key-overview_metrics [data-testid="stMetric"] {
            min-height: 0;
            padding-top: 0;
            padding-bottom: 0;
        }
        .st-key-overview_metrics [data-testid="stMetricLabel"] p {
            font-size: 0.78rem;
            line-height: 1;
            margin-bottom: 0;
        }
        .st-key-overview_metrics [data-testid="stMetricValue"] {
            font-size: 1.66rem;
            line-height: 1;
        }
        .st-key-overview_metrics [data-testid="stMetricDelta"] {
            font-size: 0.9rem;
            line-height: 1;
        }
        .qq-overview-divider {
            border: 0;
            border-top: 1px solid #e5e7eb;
            margin: 0.25rem 0 0.35rem 0;
        }
        .qq-page-description {
            color: #6b7280;
            font-size: 0.84rem;
            line-height: 1.25;
            margin: 0 0 0.45rem 0;
        }
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


def add_chart_indicators(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    required = {"ticker", "bar_time_market", "open", "high", "low", "close", "volume"}
    if not required.issubset(set(frame.columns)):
        return frame
    return add_standard_indicators(frame.sort(["ticker", "bar_time_market"]))


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
        "bars_1m": add_chart_indicators(safe_read_parquet(run_dir / "symbol_bars.parquet")),
        "bars_5m": add_chart_indicators(safe_read_parquet(run_dir / "symbol_bars_5m.parquet")),
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


def metric_delta(label: str, raw_value: float) -> float | None:
    if label in {"Cash", "Trades", "Turnover", "Volume"}:
        return None
    return raw_value


def summary_metric_specs(summary: dict) -> dict[str, dict]:
    runtime = summary.get("runtimeStatistics", {})
    return {
        "Final Equity": {
            "value": money(summary.get("final_equity")),
            "raw": float(summary.get("total_pnl") or 0.0),
        },
        "Cash": {
            "value": money(runtime.get("Equity", summary.get("final_equity"))),
            "raw": 0.0,
        },
        "Net Profit": {
            "value": money(summary.get("total_pnl")),
            "raw": float(summary.get("total_pnl") or 0.0),
        },
        "Return": {
            "value": pct(summary.get("return_pct")),
            "raw": float(summary.get("return_pct") or 0.0),
        },
        "Realized P/L": {
            "value": money(summary.get("total_pnl")),
            "raw": float(summary.get("total_pnl") or 0.0),
        },
        "Unrealized P/L": {
            "value": money(runtime.get("Unrealized", 0.0)),
            "raw": float(runtime.get("Unrealized", 0.0) or 0.0),
        },
        "Max Drawdown": {
            "value": pct(summary.get("max_drawdown_pct")),
            "raw": float(summary.get("max_drawdown_pct") or 0.0),
            "inverse": True,
        },
        "Trades": {
            "value": str(summary.get("trade_count", 0)),
            "raw": 0.0,
        },
        "Win Rate": {
            "value": pct(summary.get("win_rate")),
            "raw": float(summary.get("win_rate") or 0.0),
        },
        "Profit Factor": {
            "value": num(summary.get("profit_factor")),
            "raw": float(summary.get("profit_factor") or 0.0),
        },
        "Sharpe": {
            "value": num(summary.get("sharpe_ratio")),
            "raw": float(summary.get("sharpe_ratio") or 0.0),
        },
        "Sortino": {
            "value": num(summary.get("sortino_ratio")),
            "raw": float(summary.get("sortino_ratio") or 0.0),
        },
        "Turnover": {
            "value": pct(summary.get("portfolio_turnover")),
            "raw": 0.0,
        },
        "Volume": {
            "value": money(runtime.get("Volume", 0.0)),
            "raw": 0.0,
        },
    }


def render_summary_metric(label: str, spec: dict, compact: bool = False) -> None:
    raw = float(spec.get("raw") or 0.0)
    delta = None if compact else metric_delta(label, raw)
    st.metric(
        label,
        spec.get("value", "-"),
        delta=delta,
        delta_color="inverse" if spec.get("inverse") else "normal",
        help=METRIC_HELP.get(label),
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


def planned_session_rows(config: dict, completed_rows: list[dict] | None = None) -> list[dict]:
    completed_rows = completed_rows or []
    completed_by_day = {row.get("session_date"): row for row in completed_rows}
    sessions = available_sessions(
        Path(config["data_root"]),
        date.fromisoformat(config["start_date"]),
        date.fromisoformat(config["end_date"]),
    )
    rows = []
    for session in sessions:
        session_key = session.isoformat()
        completed = completed_by_day.get(session_key)
        rows.append(
            {
                "session_date": session_key,
                "status": "complete" if completed else "pending",
                "pnl": completed.get("pnl") if completed else None,
                "trades": completed.get("trade_count") if completed else None,
                "candidates": completed.get("candidate_count") if completed else None,
                "signals": completed.get("signal_count") if completed else None,
            }
        )
    return rows


def render_live_run_header(config: dict, completed_rows: list[dict], run_dir: str | None, status: str) -> None:
    sessions = planned_session_rows(config, completed_rows)
    completed = len(completed_rows)
    total = max(1, len(sessions))
    latest = completed_rows[-1] if completed_rows else {}
    summary = {}
    if run_dir:
        metadata = read_run_metadata(Path(run_dir)) or {}
        summary = metadata.get("summary", {})

    st.markdown(f"### {config['run_name']}")
    top = st.columns(5)
    top[0].metric("Status", status)
    top[1].metric("Sessions", f"{completed}/{total}")
    top[2].metric("Latest Day", latest.get("session_date", "-"))
    top[3].metric("Latest P/L", money(latest.get("pnl", 0.0)))
    top[4].metric("Run Return", pct(summary.get("return_pct", 0.0)))
    st.progress(min(1.0, completed / total))
    st.dataframe(pl.DataFrame(sessions), width="stretch", hide_index=True)
    if run_dir:
        st.caption(f"Writing artifacts to {run_dir}")


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
    specs = summary_metric_specs(summary)
    with st.container(key="overview_metrics"):
        for row in SUMMARY_METRIC_LAYOUT["rows"]:
            cols = st.columns([1, 2, 2, 2, 2, 2, 2, 2, 1])
            for col, label in zip(cols[1:-1], row):
                with col:
                    render_summary_metric(label, specs[label])


def format_stat_value(key: str, value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value
    if key in MONEY_KEYS:
        return money(value)
    if key in PERCENT_KEYS:
        return pct(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return num(value)
    return str(value)


def stat_help(title: str, key: str, label: str) -> str:
    return f"{label} from {title.lower()}, calculated from the selected run artifacts."


def render_stat_group_card(title: str, group_name: str, entries: list[tuple[str, str]], stats: dict) -> None:
    with st.container(border=True):
        st.markdown(f"**{group_name}**")
        cols = st.columns(3)
        metric_index = 0
        for key, label in entries:
            if key not in stats:
                continue
            with cols[metric_index % 3]:
                st.metric(
                    label,
                    format_stat_value(key, stats.get(key)),
                    help=stat_help(title, key, label),
                )
            metric_index += 1
        if metric_index == 0:
            st.caption("No values available for this group.")


def render_grouped_stats(title: str, stats: dict, groups: dict[str, list[tuple[str, str]]]) -> None:
    st.subheader(title)
    if not stats:
        st.info("No statistics available.")
        return
    grouped_keys = {key for entries in groups.values() for key, _ in entries}
    cards = list(groups.items())
    remaining = [(key, key) for key in stats.keys() if key not in grouped_keys]
    if remaining:
        cards.append(("Other", remaining))
    for idx in range(0, len(cards), 2):
        cols = st.columns(2)
        for col_idx, (group_name, entries) in enumerate(cards[idx : idx + 2]):
            with cols[col_idx]:
                render_stat_group_card(title, group_name, entries, stats)


def render_profit_loss_chart(daily: pl.DataFrame) -> None:
    st.subheader("Profit / Loss")
    if daily.is_empty():
        st.info("No daily P/L data available.")
        return
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
        .properties(height=720, title="Daily Profit / Loss")
    )
    st.altair_chart(chart, width="stretch")


def render_equity_cash_chart(portfolio: pl.DataFrame) -> None:
    st.subheader("Equity / Cash")
    if portfolio.is_empty():
        st.info("No portfolio equity data available.")
        return
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
        .properties(height=720, title="Equity and Cash")
        .interactive()
    )
    st.altair_chart(chart, width="stretch")


def render_overview(data: dict, period: str) -> None:
    summary = selected_summary(data, period)
    render_metrics(summary)
    st.markdown('<hr class="qq-overview-divider" />', unsafe_allow_html=True)
    daily = filter_df(data["daily"], period)
    portfolio = filter_df(data["portfolio"], period)
    left, right = st.columns(2)
    with left:
        render_profit_loss_chart(daily)
    with right:
        render_equity_cash_chart(portfolio)
    render_grouped_stats("Trade Statistics", summary.get("tradeStatistics", {}), TRADE_STAT_GROUPS)
    render_grouped_stats("Portfolio Statistics", summary.get("portfolioStatistics", {}), PORTFOLIO_STAT_GROUPS)


def normalize_bar_columns(df: pl.DataFrame) -> pl.DataFrame:
    return df


def chart_timestamp(value) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    parsed = parse_datetime_value(value)
    return int(parsed.timestamp()) if parsed else 0


def parse_datetime_value(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def numeric_value(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def hex_to_rgba(hex_color: str, opacity: float) -> str:
    color = hex_color.lstrip("#")
    if len(color) != 6:
        return f"rgba(37, 99, 235, {opacity:.2f})"
    red = int(color[0:2], 16)
    green = int(color[2:4], 16)
    blue = int(color[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {opacity:.2f})"


def default_chart_indicator_settings() -> dict:
    return {
        indicator: {
            "color": DEFAULT_INDICATOR_COLORS.get(indicator, "#2563eb"),
            "opacity": 0.72,
            "lineWidth": DEFAULT_INDICATOR_WIDTHS.get(indicator, 1),
        }
        for indicator in CHART_INDICATORS
    }


def candle_settings_from_state(key_prefix: str) -> dict:
    st.session_state.setdefault(f"{key_prefix}_up_color", DEFAULT_CANDLE_CHART_SETTINGS["upColor"])
    st.session_state.setdefault(f"{key_prefix}_down_color", DEFAULT_CANDLE_CHART_SETTINGS["downColor"])
    st.session_state.setdefault(f"{key_prefix}_border_up_color", DEFAULT_CANDLE_CHART_SETTINGS["borderUpColor"])
    st.session_state.setdefault(f"{key_prefix}_border_down_color", DEFAULT_CANDLE_CHART_SETTINGS["borderDownColor"])
    st.session_state.setdefault(f"{key_prefix}_wick_up_color", DEFAULT_CANDLE_CHART_SETTINGS["wickUpColor"])
    st.session_state.setdefault(f"{key_prefix}_wick_down_color", DEFAULT_CANDLE_CHART_SETTINGS["wickDownColor"])
    st.session_state.setdefault(f"{key_prefix}_border_visible", DEFAULT_CANDLE_CHART_SETTINGS["borderVisible"])
    st.session_state.setdefault(f"{key_prefix}_wick_visible", DEFAULT_CANDLE_CHART_SETTINGS["wickVisible"])
    st.session_state.setdefault(f"{key_prefix}_price_line_visible", DEFAULT_CANDLE_CHART_SETTINGS["priceLineVisible"])
    st.session_state.setdefault(f"{key_prefix}_bar_spacing", DEFAULT_CANDLE_CHART_SETTINGS["barSpacing"])
    st.session_state.setdefault(f"{key_prefix}_min_bar_spacing", DEFAULT_CANDLE_CHART_SETTINGS["minBarSpacing"])
    st.session_state.setdefault(f"{key_prefix}_right_offset", DEFAULT_CANDLE_CHART_SETTINGS["rightOffset"])
    return {
        "upColor": st.session_state[f"{key_prefix}_up_color"],
        "downColor": st.session_state[f"{key_prefix}_down_color"],
        "borderUpColor": st.session_state[f"{key_prefix}_border_up_color"],
        "borderDownColor": st.session_state[f"{key_prefix}_border_down_color"],
        "wickUpColor": st.session_state[f"{key_prefix}_wick_up_color"],
        "wickDownColor": st.session_state[f"{key_prefix}_wick_down_color"],
        "borderVisible": bool(st.session_state[f"{key_prefix}_border_visible"]),
        "wickVisible": bool(st.session_state[f"{key_prefix}_wick_visible"]),
        "priceLineVisible": bool(st.session_state[f"{key_prefix}_price_line_visible"]),
        "barSpacing": int(st.session_state[f"{key_prefix}_bar_spacing"]),
        "minBarSpacing": int(st.session_state[f"{key_prefix}_min_bar_spacing"]),
        "rightOffset": int(st.session_state[f"{key_prefix}_right_offset"]),
    }


def render_candle_settings(key_prefix: str) -> dict:
    candle_settings_from_state(key_prefix)
    colors = st.columns(2, gap="small")
    with colors[0]:
        st.color_picker("Up", key=f"{key_prefix}_up_color")
        st.color_picker("Border up", key=f"{key_prefix}_border_up_color")
        st.color_picker("Wick up", key=f"{key_prefix}_wick_up_color")
    with colors[1]:
        st.color_picker("Down", key=f"{key_prefix}_down_color")
        st.color_picker("Border down", key=f"{key_prefix}_border_down_color")
        st.color_picker("Wick down", key=f"{key_prefix}_wick_down_color")
    toggles = st.columns(3, gap="small")
    toggles[0].checkbox("Border", key=f"{key_prefix}_border_visible")
    toggles[1].checkbox("Wick", key=f"{key_prefix}_wick_visible")
    toggles[2].checkbox("Price line", key=f"{key_prefix}_price_line_visible")
    spacing = st.columns(3, gap="small")
    spacing[0].slider("Bar spacing", 5, 40, key=f"{key_prefix}_bar_spacing")
    spacing[1].slider("Min spacing", 2, 20, key=f"{key_prefix}_min_bar_spacing")
    spacing[2].slider("Right offset", 0, 20, key=f"{key_prefix}_right_offset")
    return candle_settings_from_state(key_prefix)


def chart_toolbar(
    *,
    tickers: list[str] | None,
    selected_ticker: str | None,
    timeframe_key: str,
    indicator_key: str,
) -> tuple[str | None, str, dict, dict]:
    columns = st.columns([1.8, 1.15, 1.0, 7.4], gap="small", vertical_alignment="center")
    ticker = selected_ticker
    with columns[0]:
        if tickers:
            ticker = st.selectbox("Ticker", tickers, index=tickers.index(selected_ticker) if selected_ticker in tickers else 0, label_visibility="collapsed")
        else:
            st.text(selected_ticker or "")
    with columns[1]:
        timeframe = st.segmented_control("Timeframe", ["1m", "5m"], default="1m", key=timeframe_key, label_visibility="collapsed")
    with columns[2]:
        with st.popover("Candles", width="content"):
            candle_settings = render_candle_settings(f"{indicator_key}_candles")
    return ticker, timeframe, default_chart_indicator_settings(), candle_settings


def tradingview_chart_payload(bars: pl.DataFrame, orders: pl.DataFrame, indicators: dict[str, dict]) -> dict:
    bars = normalize_bar_columns(bars).sort("bar_time_market")
    rows = bars.to_dicts()
    candles = []
    volumes = []
    overlay_series = []
    oscillator_series = []

    for row in rows:
        timestamp = chart_timestamp(row.get("bar_time_market"))
        if not timestamp:
            continue
        open_price = numeric_value(row.get("open"))
        high_price = numeric_value(row.get("high"))
        low_price = numeric_value(row.get("low"))
        close_price = numeric_value(row.get("close"))
        if None in {open_price, high_price, low_price, close_price}:
            continue
        candles.append(
            {
                "time": timestamp,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )
        volume = numeric_value(row.get("volume"))
        if volume is not None:
            volumes.append(
                {
                    "time": timestamp,
                    "value": volume,
                    "color": "rgba(15, 138, 59, 0.28)" if close_price >= open_price else "rgba(192, 54, 44, 0.28)",
                }
            )

    for column, options in indicators.items():
        if column not in bars.columns:
            continue
        points = []
        color = options.get("color", DEFAULT_INDICATOR_COLORS.get(column, "#2563eb"))
        opacity = float(options.get("opacity", 0.72))
        line_width = int(options.get("lineWidth", DEFAULT_INDICATOR_WIDTHS.get(column, 1)))
        for row in rows:
            timestamp = chart_timestamp(row.get("bar_time_market"))
            value = numeric_value(row.get(column))
            if timestamp and value is not None:
                point = {"time": timestamp, "value": value}
                if column == "macd_hist":
                    point["color"] = hex_to_rgba("#0f8a3b" if value >= 0 else "#c0362c", opacity)
                points.append(point)
        if points:
            target = oscillator_series if column in OSCILLATOR_CHART_INDICATORS else overlay_series
            target.append(
                {
                    "name": column,
                    "color": hex_to_rgba(color, opacity),
                    "legendColor": color,
                    "opacity": opacity,
                    "lineWidth": line_width,
                    "style": "histogram" if column == "macd_hist" else "line",
                    "data": points,
                }
            )

    markers = []
    if not orders.is_empty() and "filled_at" in orders.columns:
        for row in orders.filter(pl.col("status") == "FILLED").sort("filled_at").to_dicts():
            timestamp = chart_timestamp(row.get("filled_at"))
            if not timestamp:
                continue
            side = str(row.get("side", "")).upper()
            is_buy = side == "BUY"
            markers.append(
                {
                    "time": timestamp,
                    "position": "belowBar" if is_buy else "aboveBar",
                    "color": "#0f8a3b" if is_buy else "#c0362c",
                    "shape": "arrowUp" if is_buy else "arrowDown",
                    "text": f"{side} {row.get('quantity', '')} @ {money(row.get('fill_price'))}",
                }
            )

    return {
        "candles": candles,
        "volumes": volumes,
        "overlays": overlay_series,
        "oscillators": oscillator_series,
        "markers": markers,
    }


def render_lightweight_candle_chart(payload: dict, candle_settings: dict | None = None, height: int = 720) -> None:
    chart_id = f"tv-chart-{abs(hash(json.dumps(payload, sort_keys=True))) % 10_000_000}"
    payload_json = json.dumps(payload)
    candle_settings_json = json.dumps({**DEFAULT_CANDLE_CHART_SETTINGS, **(candle_settings or {})})
    pane_gap = 10 if payload.get("oscillators") else 0
    oscillator_ratio = 0.375
    price_height = int((height - pane_gap) / (1 + oscillator_ratio)) if payload.get("oscillators") else height
    oscillator_height = int(price_height * oscillator_ratio) if payload.get("oscillators") else 0
    total_height = price_height + oscillator_height + pane_gap
    html = f"""
    <div id="{chart_id}" style="height:{total_height}px;width:100%;display:flex;flex-direction:column;gap:{pane_gap}px;position:relative;">
        <div id="{chart_id}-price" style="height:{price_height}px;width:100%;"></div>
        <div id="{chart_id}-osc" style="height:{oscillator_height}px;width:100%;display:{'block' if oscillator_height else 'none'};"></div>
    </div>
    <script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js"></script>
    <script>
    const payload = {payload_json};
    const candleSettings = {candle_settings_json};
    const container = document.getElementById("{chart_id}");
    const priceContainer = document.getElementById("{chart_id}-price");
    const oscillatorContainer = document.getElementById("{chart_id}-osc");
    const hasOscillators = !!(payload.oscillators && payload.oscillators.length);
    const rightScaleWidth = 90;
    function formatDateTime(time) {{
        const date = new Date(Number(time) * 1000);
        const pad = value => String(value).padStart(2, "0");
        return `${{date.getFullYear()}}-${{pad(date.getMonth() + 1)}}-${{pad(date.getDate())}} ${{pad(date.getHours())}}:${{pad(date.getMinutes())}}:${{pad(date.getSeconds())}}`;
    }}
    const commonOptions = {{
        layout: {{
            background: {{ type: "solid", color: "#ffffff" }},
            textColor: "#111827",
            fontSize: 12
        }},
        localization: {{
            timeFormatter: formatDateTime
        }},
        grid: {{
            vertLines: {{ color: "#f3f4f6" }},
            horzLines: {{ color: "#f3f4f6" }}
        }},
        crosshair: {{
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {{
                visible: true,
                labelVisible: true,
                color: "rgba(17,24,39,0.38)",
                width: 1,
                labelBackgroundColor: "#111827"
            }},
            horzLine: {{ color: "rgba(17,24,39,0.28)", width: 1, style: 2 }}
        }},
        rightPriceScale: {{
            borderColor: "#d1d5db",
            minimumWidth: rightScaleWidth,
            scaleMargins: {{ top: 0.08, bottom: 0.22 }}
        }},
        timeScale: {{
            borderColor: "#d1d5db",
            timeVisible: true,
            secondsVisible: true,
            rightOffset: candleSettings.rightOffset,
            barSpacing: candleSettings.barSpacing,
            minBarSpacing: candleSettings.minBarSpacing
        }},
        handleScroll: {{
            mouseWheel: true,
            pressedMouseMove: true,
            horzTouchDrag: true,
            vertTouchDrag: false
        }},
        handleScale: {{
            axisPressedMouseMove: true,
            mouseWheel: true,
            pinch: true
        }}
    }};
    const chart = LightweightCharts.createChart(priceContainer, {{
        ...commonOptions,
        width: priceContainer.clientWidth,
        height: {price_height},
        rightPriceScale: {{
            borderColor: "#d1d5db",
            minimumWidth: rightScaleWidth,
            scaleMargins: {{ top: 0.06, bottom: 0.18 }}
        }},
        timeScale: {{
            ...commonOptions.timeScale,
            visible: !hasOscillators
        }}
    }});
    const oscillatorChart = oscillatorContainer && payload.oscillators && payload.oscillators.length ? LightweightCharts.createChart(oscillatorContainer, {{
        ...commonOptions,
        width: oscillatorContainer.clientWidth,
        height: {oscillator_height},
        rightPriceScale: {{
            borderColor: "#d1d5db",
            minimumWidth: rightScaleWidth,
            scaleMargins: {{ top: 0.12, bottom: 0.12 }}
        }}
    }}) : null;

    const candleSeries = chart.addCandlestickSeries({{
        upColor: candleSettings.upColor,
        downColor: candleSettings.downColor,
        borderUpColor: candleSettings.borderUpColor,
        borderDownColor: candleSettings.borderDownColor,
        wickUpColor: candleSettings.wickUpColor,
        wickDownColor: candleSettings.wickDownColor,
        borderVisible: candleSettings.borderVisible,
        wickVisible: candleSettings.wickVisible,
        priceLineVisible: candleSettings.priceLineVisible
    }});
    candleSeries.setData(payload.candles || []);
    if (payload.markers && payload.markers.length) {{
        candleSeries.setMarkers(payload.markers);
    }}

    if (payload.volumes && payload.volumes.length) {{
        const volumeSeries = chart.addHistogramSeries({{
            priceFormat: {{ type: "volume" }},
            priceScaleId: ""
        }});
        volumeSeries.priceScale().applyOptions({{
            scaleMargins: {{ top: 0.8, bottom: 0 }}
        }});
        volumeSeries.setData(payload.volumes);
    }}

    const legendShell = document.createElement("div");
    legendShell.style.position = "absolute";
    legendShell.style.left = "12px";
    legendShell.style.top = "8px";
    legendShell.style.zIndex = 6;
    legendShell.style.font = "11px system-ui";
    legendShell.style.color = "#111827";
    legendShell.style.maxWidth = "220px";
    const legendToggle = document.createElement("button");
    legendToggle.type = "button";
    legendToggle.textContent = `v ${{(payload.overlays || []).length + (payload.oscillators || []).length}} indicators`;
    legendToggle.style.display = "none";
    legendToggle.style.border = "0";
    legendToggle.style.background = "rgba(255,255,255,0.78)";
    legendToggle.style.backdropFilter = "blur(2px)";
    legendToggle.style.borderRadius = "4px";
    legendToggle.style.padding = "3px 6px";
    legendToggle.style.font = "11px system-ui";
    legendToggle.style.color = "#111827";
    legendToggle.style.cursor = "pointer";
    const legend = document.createElement("div");
    legend.style.display = "flex";
    legend.style.flexDirection = "column";
    legend.style.alignItems = "stretch";
    legend.style.gap = "3px";
    legend.style.background = "rgba(255,255,255,0.76)";
    legend.style.backdropFilter = "blur(2px)";
    legend.style.padding = "4px 5px";
    legend.style.borderRadius = "4px";
    const legendItems = document.createElement("div");
    legendItems.style.display = "flex";
    legendItems.style.flexDirection = "column";
    legendItems.style.gap = "3px";
    const legendCollapse = document.createElement("button");
    legendCollapse.type = "button";
    legendCollapse.textContent = "^";
    legendCollapse.title = "Collapse legend";
    legendCollapse.style.alignSelf = "center";
    legendCollapse.style.border = "0";
    legendCollapse.style.background = "transparent";
    legendCollapse.style.color = "#374151";
    legendCollapse.style.cursor = "pointer";
    legendCollapse.style.font = "14px system-ui";
    legendCollapse.style.lineHeight = "12px";
    legendCollapse.style.padding = "1px 10px";
    legend.appendChild(legendItems);
    legend.appendChild(legendCollapse);
    legendShell.appendChild(legendToggle);
    legendShell.appendChild(legend);
    priceContainer.style.position = "relative";
    priceContainer.appendChild(legendShell);
    legendCollapse.addEventListener("click", event => {{
        event.stopPropagation();
        legend.style.display = "none";
        legendToggle.style.display = "inline-flex";
    }});
    legendToggle.addEventListener("click", event => {{
        event.stopPropagation();
        legendToggle.style.display = "none";
        legend.style.display = "flex";
    }});

    function iconSvg(kind) {{
        if (kind === "eye") {{
            return '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
        }}
        return '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 21v-7"></path><path d="M4 10V3"></path><path d="M12 21v-9"></path><path d="M12 8V3"></path><path d="M20 21v-5"></path><path d="M20 12V3"></path><path d="M2 14h4"></path><path d="M10 8h4"></path><path d="M18 16h4"></path></svg>';
    }}

    function addLegendItem(indicator, host, series) {{
        const item = document.createElement("span");
        item.style.display = "inline-flex";
        item.style.alignItems = "center";
        item.style.justifyContent = "flex-start";
        item.style.gap = "4px";
        item.style.color = "#111827";
        item.style.fontWeight = "400";
        item.style.padding = "1px 3px";
        item.style.borderRadius = "3px";
        item.style.position = "relative";
        item.style.minWidth = "125px";
        const swatch = document.createElement("span");
        swatch.style.width = "24px";
        swatch.style.height = indicator.style === "histogram" ? "6px" : "4px";
        swatch.style.borderRadius = "999px";
        swatch.style.background = indicator.color;
        const label = document.createElement("span");
        label.textContent = indicator.name;
        indicator.currentData = indicator.data || [];
        const actions = document.createElement("span");
        actions.style.display = "none";
        actions.style.gap = "2px";
        actions.style.alignItems = "center";
        const eye = document.createElement("button");
        eye.type = "button";
        eye.innerHTML = iconSvg("eye");
        eye.title = "Hide or show";
        const settings = document.createElement("button");
        settings.type = "button";
        settings.innerHTML = iconSvg("settings");
        settings.title = "Visual settings";
        [eye, settings].forEach(button => {{
            button.style.border = "0";
            button.style.background = "rgba(255,255,255,0.9)";
            button.style.color = "#374151";
            button.style.width = "18px";
            button.style.height = "18px";
            button.style.padding = "2px";
            button.style.borderRadius = "3px";
            button.style.cursor = "pointer";
        }});
        actions.appendChild(eye);
        actions.appendChild(settings);
        const panel = document.createElement("div");
        panel.style.display = "none";
        panel.style.position = "absolute";
        panel.style.top = "22px";
        panel.style.left = "0";
        panel.style.zIndex = 8;
        panel.style.background = "rgba(255,255,255,0.98)";
        panel.style.border = "1px solid #d1d5db";
        panel.style.borderRadius = "5px";
        panel.style.padding = "6px";
        panel.style.boxShadow = "0 8px 24px rgba(15,23,42,0.14)";
        panel.style.color = "#111827";
        panel.style.font = "11px system-ui";
        panel.style.minWidth = "150px";
        const colorInput = document.createElement("input");
        colorInput.type = "color";
        colorInput.value = indicator.legendColor || "#2563eb";
        const opacityInput = document.createElement("input");
        opacityInput.type = "range";
        opacityInput.min = "0.15";
        opacityInput.max = "1";
        opacityInput.step = "0.05";
        opacityInput.value = indicator.opacity || 0.72;
        const widthInput = document.createElement("input");
        widthInput.type = "range";
        widthInput.min = "1";
        widthInput.max = "4";
        widthInput.step = "1";
        widthInput.value = indicator.lineWidth || 1;
        panel.innerHTML = '<div style="font-weight:700;margin-bottom:4px;">' + indicator.name + '</div>';
        [["Color", colorInput], ["Opacity", opacityInput], ["Width", widthInput]].forEach(([caption, input]) => {{
            const row = document.createElement("label");
            row.style.display = "grid";
            row.style.gridTemplateColumns = "48px 1fr";
            row.style.alignItems = "center";
            row.style.gap = "6px";
            row.style.margin = "3px 0";
            row.appendChild(document.createTextNode(caption));
            row.appendChild(input);
            panel.appendChild(row);
        }});
        item.appendChild(swatch);
        item.appendChild(label);
        item.appendChild(actions);
        item.appendChild(panel);
        host.appendChild(item);

        function applyVisuals() {{
            const opacity = parseFloat(opacityInput.value);
            const baseColor = colorInput.value;
            const rgba = hexToRgba(baseColor, opacity);
            indicator.legendColor = baseColor;
            indicator.color = rgba;
            indicator.opacity = opacity;
            indicator.lineWidth = parseInt(widthInput.value, 10);
            swatch.style.background = rgba;
            item.style.color = "#111827";
            if (indicator.style === "histogram") {{
                indicator.currentData = (indicator.data || []).map(point => ({{
                    ...point,
                    color: rgba
                }}));
                if (!indicator.hidden) series.setData(indicator.currentData);
            }} else {{
                series.applyOptions({{ color: rgba, lineWidth: indicator.lineWidth }});
            }}
        }}
        item.addEventListener("mouseenter", () => {{ actions.style.display = "inline-flex"; }});
        item.addEventListener("mouseleave", () => {{ if (panel.style.display === "none") actions.style.display = "none"; }});
        settings.addEventListener("click", event => {{
            event.stopPropagation();
            panel.style.display = panel.style.display === "none" ? "block" : "none";
            actions.style.display = "inline-flex";
        }});
        eye.addEventListener("click", event => {{
            event.stopPropagation();
            indicator.hidden = !indicator.hidden;
            series.setData(indicator.hidden ? [] : (indicator.currentData || indicator.data || []));
            item.style.opacity = indicator.hidden ? "0.38" : "1";
            label.style.textDecoration = indicator.hidden ? "line-through" : "none";
        }});
        [colorInput, opacityInput, widthInput].forEach(input => input.addEventListener("input", applyVisuals));
    }}

    function hexToRgba(hex, opacity) {{
        const normalized = hex.replace("#", "");
        const value = parseInt(normalized.length === 3 ? normalized.split("").map(ch => ch + ch).join("") : normalized, 16);
        const red = (value >> 16) & 255;
        const green = (value >> 8) & 255;
        const blue = value & 255;
        return `rgba(${{red}}, ${{green}}, ${{blue}}, ${{opacity.toFixed(2)}})`;
    }}

    (payload.overlays || []).forEach((indicator) => {{
        const line = chart.addLineSeries({{
            color: indicator.color,
            lineWidth: indicator.lineWidth || 1,
            priceLineVisible: false,
            lastValueVisible: false
        }});
        line.setData(indicator.data || []);
        addLegendItem(indicator, legendItems, line);
    }});

    let firstOscillatorSeries = null;
    const oscillatorValueByTime = new Map();
    const priceByTime = new Map((payload.candles || []).map(bar => [bar.time, bar.close]));
    if (oscillatorChart) {{
        (payload.oscillators || []).forEach((indicator) => {{
            const series = indicator.style === "histogram"
                ? oscillatorChart.addHistogramSeries({{
                    color: indicator.color,
                    priceLineVisible: false,
                    lastValueVisible: false
                }})
                : oscillatorChart.addLineSeries({{
                    color: indicator.color,
                    lineWidth: indicator.lineWidth || 1,
                    priceLineVisible: false,
                    lastValueVisible: false
            }});
            series.setData(indicator.data || []);
            if (!firstOscillatorSeries) firstOscillatorSeries = series;
            (indicator.data || []).forEach(point => {{
                if (!oscillatorValueByTime.has(point.time)) oscillatorValueByTime.set(point.time, point.value);
            }});
            addLegendItem(indicator, legendItems, series);
        }});
    }}

    function alignTimeScales() {{
        chart.timeScale().fitContent();
        if (!oscillatorChart) return;
        const range = chart.timeScale().getVisibleLogicalRange();
        if (range) oscillatorChart.timeScale().setVisibleLogicalRange(range);
    }}
    alignTimeScales();
    let syncing = false;
    function syncRange(source, target) {{
        source.timeScale().subscribeVisibleLogicalRangeChange(range => {{
            if (syncing || !range) return;
            syncing = true;
            requestAnimationFrame(() => {{
                target.timeScale().setVisibleLogicalRange(range);
                syncing = false;
            }});
        }});
    }}
    if (oscillatorChart) {{
        syncRange(chart, oscillatorChart);
        syncRange(oscillatorChart, chart);
        if (chart.setCrosshairPosition && chart.clearCrosshairPosition && oscillatorChart.setCrosshairPosition && oscillatorChart.clearCrosshairPosition) {{
            chart.subscribeCrosshairMove(param => {{
                if (!param || param.time === undefined) {{
                    oscillatorChart.clearCrosshairPosition();
                    return;
                }}
                const value = oscillatorValueByTime.get(param.time);
                if (value !== undefined && firstOscillatorSeries) {{
                    oscillatorChart.setCrosshairPosition(value, param.time, firstOscillatorSeries);
                }}
            }});
            oscillatorChart.subscribeCrosshairMove(param => {{
                if (!param || param.time === undefined) {{
                    chart.clearCrosshairPosition();
                    return;
                }}
                const value = priceByTime.get(param.time);
                if (value !== undefined) chart.setCrosshairPosition(value, param.time, candleSeries);
            }});
        }}
    }}
    const resizeObserver = new ResizeObserver(entries => {{
        if (!entries.length) return;
        const width = entries[0].contentRect.width;
        chart.applyOptions({{ width }});
        if (oscillatorChart) oscillatorChart.applyOptions({{ width }});
        alignTimeScales();
    }});
    resizeObserver.observe(container);
    </script>
    """
    components.html(html, height=total_height + 12, scrolling=False)


def candle_chart(bars: pl.DataFrame, orders: pl.DataFrame, indicators: dict[str, dict], candle_settings: dict | None = None) -> None:
    if bars.is_empty():
        st.info("No chart data available.")
        return
    bars = normalize_bar_columns(bars).sort("bar_time_market")
    required = ["bar_time_market", "open", "high", "low", "close"]
    if any(col not in bars.columns for col in required):
        st.info("Selected chart data is missing OHLC columns.")
        return
    payload = tradingview_chart_payload(bars, orders, indicators)
    render_lightweight_candle_chart(payload, candle_settings)


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
        _, timeframe, indicators, candle_settings = chart_toolbar(
            tickers=None,
            selected_ticker=str(trade.get("symbol", "")),
            timeframe_key=f"trade_tf_{period}",
            indicator_key=f"trade_ind_{period}",
        )
        trade_day = str(trade.get("entry_time", ""))[:10] if period == "Whole Run" else period
        bars = bars_for(data, trade_day, trade["symbol"], timeframe)
        orders = filter_df(data["orders"], trade_day)
        if "symbol" in orders.columns:
            orders = orders.filter(pl.col("symbol") == trade["symbol"])
        candle_chart(bars, orders, indicators, candle_settings)


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
    ticker, timeframe, indicators, candle_settings = chart_toolbar(
        tickers=tickers,
        selected_ticker=tickers[0] if tickers else None,
        timeframe_key=f"inspect_tf_{period}",
        indicator_key=f"inspect_ind_{period}",
    )
    bars = bars_for(data, period, ticker, timeframe)
    orders = filter_df(data["orders"], period)
    if "symbol" in orders.columns:
        orders = orders.filter(pl.col("symbol") == ticker)
    candle_chart(bars, orders, indicators, candle_settings)


def render_run_dashboard(run_dir: Path, show_header: bool = True, show_back_button: bool = False) -> None:
    data = load_run_artifacts(str(run_dir), artifact_mtime(run_dir))
    metadata = data["metadata"]
    config = metadata.get("config", {})
    if show_header:
        render_run_header(config, metadata.get("status", "unknown"), metadata.get("summary") or data["summary"])
    periods = period_options(data)
    period_cols = st.columns([0.35, 0.7, 2.2, 8.75]) if show_back_button else st.columns([0.7, 2.2, 9])
    offset = 1 if show_back_button else 0
    if show_back_button:
        with period_cols[0]:
            if st.button("<", key="back_to_runs", help="Back to runs", type="tertiary"):
                st.session_state.pop("active_run_dir", None)
                st.rerun()
    with period_cols[offset]:
        st.markdown('<div class="qq-period-label">Result Period</div>', unsafe_allow_html=True)
    with period_cols[offset + 1]:
        period = st.selectbox("Result Period", periods, key=f"period_{run_dir.name}", label_visibility="collapsed")
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


def humanize_key(key: str) -> str:
    return key.replace("_", " ").replace("-", " ").title()


def render_detail_table(items: list[tuple[str, object]]) -> None:
    rows = [{"Field": label, "Value": "-" if value is None else str(value)} for label, value in items]
    st.dataframe(pl.DataFrame(rows), width="stretch", hide_index=True)


def render_run_details_content(run_dir: Path) -> None:
    metadata = read_run_metadata(run_dir) or {}
    summary = metadata.get("summary") or load_json(run_dir / "summary.json")
    config = metadata.get("config", {})
    params = config.get("strategy_params", {})

    top = st.columns(4)
    top[0].metric("Status", metadata.get("status", "unknown"))
    top[1].metric("Return", pct(summary.get("return_pct", 0.0)))
    top[2].metric("Net P/L", money(summary.get("total_pnl", 0.0)))
    top[3].metric("Trades", summary.get("trade_count", 0))

    st.subheader("Run")
    render_detail_table(
        [
            ("Run name", metadata.get("run_name", run_dir.name)),
            ("Strategy", metadata.get("strategy_name", config.get("strategy_name", ""))),
            ("Created", metadata.get("created_at", "")),
            ("Date range", f"{config.get('start_date', '')} to {config.get('end_date', '')}"),
            ("Initial cash", money(config.get("initial_cash", 0.0))),
            ("Run folder", str(run_dir)),
        ]
    )

    st.subheader("Execution")
    render_detail_table(
        [
            ("Data root", config.get("data_root", "")),
            ("Output root", config.get("output_root", "")),
            ("Market UTC offset", config.get("market_utc_offset_hours", "")),
            ("Slippage bps", config.get("slippage_bps", "")),
            ("Save chart bars", config.get("save_symbol_bars", "")),
        ]
    )

    st.subheader("Strategy Parameters")
    param_rows = [{"Parameter": humanize_key(key), "Value": str(value)} for key, value in sorted(params.items())]
    st.dataframe(pl.DataFrame(param_rows), width="stretch", hide_index=True)


if hasattr(st, "dialog"):
    @st.dialog("Run Details", width="large")
    def run_details_dialog(run_dir_value: str) -> None:
        render_run_details_content(Path(run_dir_value))
else:
    run_details_dialog = None


def render_selected_run_header(run_dir: Path) -> None:
    metadata = read_run_metadata(run_dir) or {}
    summary = metadata.get("summary") or load_json(run_dir / "summary.json")
    config = metadata.get("config", {})
    run_name = metadata.get("run_name", run_dir.name)
    status = metadata.get("status", "unknown")
    date_range = f"{config.get('start_date', '')} to {config.get('end_date', '')}"

    with st.container(key="run_header"):
        info_cols = st.columns([3, 5.1, 1.25, 3.15], vertical_alignment="center")
        with info_cols[0]:
            st.title(run_name)
        with info_cols[1]:
            summary_items = [
                str(metadata.get("strategy_name", config.get("strategy_name", ""))),
                str(status),
                date_range,
                f"return {pct(summary.get('return_pct', 0.0))}",
                f"P/L {money(summary.get('total_pnl', 0.0))}",
                f"trades {summary.get('trade_count', 0)}",
            ]
            summary_text = " | ".join(summary_items)
            st.text(summary_text)
        with info_cols[2]:
            if run_details_dialog is not None:
                if st.button("See more details", key=f"run_details_{run_dir.name}", type="tertiary", width="content"):
                    run_details_dialog(str(run_dir))
            else:
                with st.expander("See more details"):
                    render_run_details_content(run_dir)


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
    live_rows: list[dict] = []
    header_placeholder = st.empty()
    dashboard_placeholder = st.empty()
    error_placeholder = st.empty()

    with header_placeholder.container():
        render_live_run_header(config, live_rows, None, "Starting")
    with dashboard_placeholder.container():
        empty_tabs = st.tabs(["Overview", "Trades", "Orders", "Scanner", "Rejected", "Positions", "Chart Inspector", "Logs"])
        with empty_tabs[0]:
            st.info("The dashboard will populate after the first session completes.")

    def on_progress(session_date, daily_summary, run_dir):
        live_rows.append(daily_summary)
        with header_placeholder.container():
            render_live_run_header(config, live_rows, str(run_dir), "Running")
        with dashboard_placeholder.container():
            render_run_dashboard(Path(run_dir), show_header=False)

    try:
        result = run_backtest(config, progress_callback=on_progress)
        with header_placeholder.container():
            render_live_run_header(config, live_rows, result["run_dir"], "Complete")
        with dashboard_placeholder.container():
            render_run_dashboard(Path(result["run_dir"]), show_header=False)
        return result["run_dir"]
    except Exception as exc:
        with header_placeholder.container():
            render_live_run_header(config, live_rows, None, "Error")
        with error_placeholder.container():
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
    rows = run_table_rows(runs)
    actions_slot = st.empty()
    table_state = st.dataframe(
        pl.DataFrame(rows),
        width="stretch",
        hide_index=True,
        key=f"runs_table_{strategy_name}",
        on_select="rerun",
        selection_mode="single-row",
    )
    selection = getattr(table_state, "selection", {})
    selected_rows = getattr(selection, "rows", []) if selection is not None else []
    selected = runs[selected_rows[0]] if selected_rows else None
    with actions_slot.container():
        actions = st.columns([1, 1, 4])
        with actions[0]:
            if st.button("Open Selected Run", type="primary", disabled=selected is None):
                st.session_state["active_run_dir"] = str(selected)
                st.rerun()
        with actions[1]:
            if st.button("Delete Run", disabled=selected is None):
                st.session_state["delete_run_dir"] = str(selected)
                st.rerun()
        with actions[2]:
            st.caption("Select a run row in the table.")

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
    output_root = DEFAULT_OUTPUT_ROOT
    active_run = st.session_state.get("active_run_dir")
    if active_run:
        active_run_path = Path(active_run)
        render_selected_run_header(active_run_path)
        render_run_dashboard(active_run_path, show_header=False, show_back_button=True)
        return
    st.title(strategy_name)
    description = STRATEGY_DESCRIPTIONS.get(strategy_name, "No description available.")
    st.markdown(f'<div class="qq-page-description">{description}</div>', unsafe_allow_html=True)
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
