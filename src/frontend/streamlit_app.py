from __future__ import annotations

import json
import re
import shutil
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
CHART_EXTENDED_START_MINUTE = 4 * 60
CHART_REGULAR_START_MINUTE = 9 * 60 + 30
CHART_REGULAR_END_MINUTE = 16 * 60
CHART_EXTENDED_END_MINUTE = 20 * 60
CHART_EXCHANGE_TIME_ZONE = "America/New_York"

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
    "vwap": "#451F7F",
    "tema9": "#2563EB",
    "tema20": "#E10E0E",
    "macd_line": "#1C19D7",
    "macd_signal": "#F5680A",
    "macd_hist": "#33E42A",
}

DEFAULT_INDICATOR_WIDTHS = {
    "vwap": 3,
    "tema9": 1,
    "tema20": 1,
    "macd_line": 1,
    "macd_signal": 1,
    "macd_hist": 2,
}

DEFAULT_INDICATOR_OPACITIES = {
    "vwap": 0.3,
    "tema9": 0.5,
    "tema20": 0.5,
    "macd_line": 1.0,
    "macd_signal": 1.0,
    "macd_hist": 1.0,
}

INDICATOR_DISPLAY_NAMES = {
    "vwap": "VWAP",
    "tema9": "TEMA9",
    "tema20": "TEMA20",
    "macd_line": "MACD LINE",
    "macd_signal": "MACD SIGNAL",
    "macd_hist": "MACD HIST",
}

LEGEND_INDICATORS = {"vwap", "tema9", "tema20", "macd_line", "macd_signal"}

DEFAULT_CANDLE_CHART_SETTINGS = {
    "upColor": "#33E42A",
    "downColor": "#FD0E50",
    "borderUpColor": "#1DB914",
    "borderDownColor": "#CB093F",
    "wickUpColor": "#4DC746",
    "wickDownColor": "#C52A55",
    "borderVisible": True,
    "wickVisible": True,
    "priceLineVisible": True,
    "barSpacing": 40,
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
        div[class*="st-key-chart_component_"] {
            border: 1px solid #d8dee4;
            border-radius: 6px;
            background: #ffffff;
            overflow: hidden;
            padding: 0 !important;
        }
        div[class*="st-key-chart_toolbar_"] {
            border: 0 !important;
            border-bottom: 1px solid #d8dee4 !important;
            border-radius: 0 !important;
            padding: 0.2rem 0.45rem 0.25rem !important;
            margin: 0 0 0.35rem 0 !important;
            background: #ffffff;
        }
        div[class*="st-key-chart_toolbar_"] [data-testid="stHorizontalBlock"] {
            gap: 0.4rem;
        }
        div[class*="st-key-chart_toolbar_"] [data-testid="stSelectbox"],
        div[class*="st-key-chart_toolbar_"] [data-testid="stText"] {
            margin-bottom: 0 !important;
        }
        div[class*="st-key-chart_toolbar_"] [data-baseweb="select"] > div {
            background-color: #ffffff !important;
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


def date_strings_between(start: str, end: str) -> list[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    values = []
    cursor = start_date
    while cursor <= end_date:
        values.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return values


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


def chart_session_dates(data: dict[str, Any], period: str) -> list[str]:
    if period != "Whole Run":
        return [period]
    config = data.get("metadata", {}).get("config", {})
    if config.get("start_date") and config.get("end_date"):
        return date_strings_between(str(config["start_date"]), str(config["end_date"]))
    bars = data.get("bars_1m", pl.DataFrame())
    if not bars.is_empty() and "session_date" in bars.columns:
        return bars.select("session_date").unique().sort("session_date")["session_date"].cast(pl.Utf8).to_list()
    return []


def chart_source_config(data: dict[str, Any]) -> tuple[str, str]:
    config = data.get("metadata", {}).get("config", {})
    data_root = str(config.get("data_root") or DEFAULT_DATA_ROOT)
    exchange_timezone = str(config.get("exchange_timezone") or CHART_EXCHANGE_TIME_ZONE)
    return data_root, exchange_timezone


def chart_minute_file_path(data_root: Path, session_date: date) -> Path:
    return data_root / f"{session_date.year:04d}" / f"{session_date.month:02d}" / f"{session_date.isoformat()}.csv.gz"


def add_chart_exchange_time_columns(frame: pl.DataFrame, exchange_timezone: str) -> pl.DataFrame:
    return (
        frame.with_columns(pl.from_epoch("window_start", time_unit="ns").dt.replace_time_zone("UTC").alias("bar_time_utc"))
        .with_columns(pl.col("bar_time_utc").dt.convert_time_zone(exchange_timezone).alias("bar_time_market"))
        .with_columns(
            (
                (pl.col("bar_time_market").dt.hour().cast(pl.Int32) * 60)
                + pl.col("bar_time_market").dt.minute().cast(pl.Int32)
            ).alias("minute_of_day")
        )
    )


def consolidate_extended_chart_five_minute(minute_bars: pl.DataFrame) -> pl.DataFrame:
    if minute_bars.is_empty():
        return minute_bars
    return (
        minute_bars.with_columns(((pl.col("minute_of_day") // 5) * 5).alias("five_minute_bucket"))
        .group_by(["ticker", "session_date", "five_minute_bucket"])
        .agg(
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
            pl.col("transactions").sum().alias("transactions"),
            pl.col("window_start").min().alias("window_start"),
            pl.col("bar_time_utc").min().alias("bar_time_utc"),
            pl.col("bar_time_market").min().alias("bar_time_market"),
            pl.col("minute_of_day").min().alias("minute_of_day"),
        )
        .sort(["ticker", "bar_time_market"])
        .pipe(add_standard_indicators)
    )


@st.cache_data(show_spinner=False)
def load_extended_chart_bars(
    data_root_value: str,
    exchange_timezone: str,
    session_dates: tuple[str, ...],
    ticker: str,
    timeframe: str,
) -> pl.DataFrame:
    # Visualization data is intentionally separate from strategy execution windows.
    # The chart always renders exchange-time extended hours from raw UTC source bars.
    minute_frames = []
    for session_date_value in session_dates:
        session = date.fromisoformat(session_date_value)
        source = chart_minute_file_path(Path(data_root_value), session)
        if not source.exists():
            continue
        frame = (
            pl.scan_csv(source)
            .filter(pl.col("ticker") == ticker)
            .select("ticker", "volume", "open", "close", "high", "low", "window_start", "transactions")
            .collect()
        )
        if frame.is_empty():
            continue
        frame = (
            add_chart_exchange_time_columns(frame, exchange_timezone)
            .filter(
                (pl.col("minute_of_day") >= CHART_EXTENDED_START_MINUTE)
                & (pl.col("minute_of_day") < CHART_EXTENDED_END_MINUTE)
            )
            .sort(["ticker", "bar_time_market"])
            .with_columns(pl.lit(session.isoformat()).alias("session_date"))
            .pipe(add_standard_indicators)
        )
        minute_frames.append(frame)

    if not minute_frames:
        return pl.DataFrame()
    if timeframe == "5m":
        return pl.concat(
            [consolidate_extended_chart_five_minute(frame) for frame in minute_frames],
            how="diagonal",
        ).sort(["ticker", "bar_time_market"])
    return pl.concat(minute_frames, how="diagonal").sort(["ticker", "bar_time_market"])


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


def chart_timestamp(value, assumed_timezone: str = "UTC") -> int:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime_value(value)
    if not parsed:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(assumed_timezone))
    return int(parsed.astimezone(timezone.utc).timestamp())


def row_chart_timestamp(row: dict) -> int:
    if row.get("bar_time_utc") is not None:
        return chart_timestamp(row.get("bar_time_utc"), "UTC")
    return chart_timestamp(row.get("bar_time_market"), CHART_EXCHANGE_TIME_ZONE)


def row_exchange_datetime(row: dict) -> datetime | None:
    market_time = parse_datetime_value(row.get("bar_time_market"))
    if market_time:
        if market_time.tzinfo is None:
            return market_time.replace(tzinfo=ZoneInfo(CHART_EXCHANGE_TIME_ZONE))
        return market_time.astimezone(ZoneInfo(CHART_EXCHANGE_TIME_ZONE))
    utc_time = parse_datetime_value(row.get("bar_time_utc"))
    if utc_time:
        if utc_time.tzinfo is None:
            utc_time = utc_time.replace(tzinfo=timezone.utc)
        return utc_time.astimezone(ZoneInfo(CHART_EXCHANGE_TIME_ZONE))
    return None


def chart_marker_timestamp(value) -> int:
    return chart_timestamp(value, CHART_EXCHANGE_TIME_ZONE)


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


def minute_of_day_from_row(row: dict) -> int | None:
    value = row.get("minute_of_day")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    parsed = parse_datetime_value(row.get("bar_time_market"))
    if not parsed:
        parsed = row_exchange_datetime(row)
    if not parsed:
        return None
    return parsed.hour * 60 + parsed.minute


def extended_session_regions(rows: list[dict], candles: list[dict]) -> list[dict]:
    del candles
    regions: dict[tuple[str, str], dict] = {}
    for row in rows:
        timestamp = row_chart_timestamp(row)
        minute = minute_of_day_from_row(row)
        parsed = row_exchange_datetime(row)
        if not timestamp or minute is None or not parsed:
            continue
        if CHART_EXTENDED_START_MINUTE <= minute < CHART_REGULAR_START_MINUTE:
            phase = "premarket"
            color = "rgba(251, 146, 60, 0.16)"
        elif CHART_REGULAR_END_MINUTE <= minute < CHART_EXTENDED_END_MINUTE:
            phase = "afterhours"
            color = "rgba(96, 165, 250, 0.15)"
        else:
            continue
        key = (parsed.date().isoformat(), phase)
        region = regions.setdefault(key, {"start": timestamp, "end": timestamp, "color": color})
        region["start"] = min(region["start"], timestamp)
        region["end"] = max(region["end"], timestamp)
    return sorted(regions.values(), key=lambda item: item["start"])


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
            "opacity": DEFAULT_INDICATOR_OPACITIES.get(indicator, 0.72),
            "lineWidth": DEFAULT_INDICATOR_WIDTHS.get(indicator, 1),
        }
        for indicator in CHART_INDICATORS
    }


def chart_key_fragment(value: str) -> str:
    fragment = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value)).strip("_")
    return fragment or "chart"


def render_chart_fullscreen_button(component_key: str) -> None:
    component_selector_json = json.dumps(f".st-key-{component_key}")
    html = """
    <style>
    html, body {{
        margin: 0;
        padding: 0;
        background: transparent;
        overflow: hidden;
    }}
    #qq-fullscreen-toolbar-button {{
        width: 36px;
        height: 32px;
        border: 0;
        border-radius: 4px;
        background: #ffffff;
        color: #111827;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        margin: 0 0 0 auto;
        line-height: 1;
    }}
    #qq-fullscreen-toolbar-button:hover {{
        background: #f3f4f6;
    }}
    </style>
    <button id="qq-fullscreen-toolbar-button" title="Fullscreen" aria-label="Toggle fullscreen">
        <svg viewBox="0 0 24 24" width="23" height="23" aria-hidden="true">
            <path d="M8 3H3v5h2V5h3V3zm8 0v2h3v3h2V3h-5zM5 16H3v5h5v-2H5v-3zm14 3h-3v2h5v-5h-2v3z" fill="currentColor"></path>
        </svg>
    </button>
    <script>
    const componentSelector = __COMPONENT_SELECTOR__;
    const button = document.getElementById("qq-fullscreen-toolbar-button");
    const fullscreenIcon = `<svg viewBox="0 0 24 24" width="23" height="23" aria-hidden="true"><path d="M8 3H3v5h2V5h3V3zm8 0v2h3v3h2V3h-5zM5 16H3v5h5v-2H5v-3zm14 3h-3v2h5v-5h-2v3z" fill="currentColor"></path></svg>`;
    const exitFullscreenIcon = `<svg viewBox="0 0 24 24" width="25" height="25" aria-hidden="true"><path d="M9 3H7v4H3v2h6V3zm8 0h-2v6h6V7h-4V3zM3 17h4v4h2v-6H3v2zm12 4h2v-4h4v-2h-6v6z" fill="currentColor"></path></svg>`;
    let parentDocument = null;
    let component = null;
    let injectedStyle = null;
    try {{
        parentDocument = window.parent && window.parent.document;
        component = parentDocument ? parentDocument.querySelector(componentSelector) : null;
    }} catch (error) {{
        parentDocument = null;
        component = null;
    }}

    function ensureStyle() {{
        if (!parentDocument || injectedStyle) return;
        injectedStyle = parentDocument.createElement("style");
        injectedStyle.textContent = `
            .qq-streamlit-chart-fullscreen {{
                position: fixed !important;
                inset: 0 !important;
                width: 100vw !important;
                height: 100vh !important;
                max-width: 100vw !important;
                z-index: 2147483647 !important;
                border-radius: 0 !important;
                border: 0 !important;
                background: #ffffff !important;
                overflow: hidden !important;
                padding: 0 !important;
            }}
            .qq-streamlit-chart-fullscreen iframe {{
                width: 100vw !important;
                height: calc(100vh - 2.85rem) !important;
            }}
            .qq-streamlit-chart-fullscreen div[class*="st-key-chart_toolbar_"] {{
                position: relative !important;
                z-index: 2 !important;
                height: 2.85rem !important;
                margin: 0 !important;
                background: #ffffff !important;
            }}
            .qq-streamlit-chart-fullscreen div[class*="st-key-chart_toolbar_"] iframe {{
                width: 40px !important;
                height: 34px !important;
            }}
            .qq-streamlit-chart-body-lock {{
                overflow: hidden !important;
            }}
        `;
        parentDocument.head.appendChild(injectedStyle);
    }}

    function findComponent() {{
        if (!component && parentDocument) component = parentDocument.querySelector(componentSelector);
        return component;
    }}

    function isActive() {{
        const target = findComponent();
        return !!(target && target.classList.contains("qq-streamlit-chart-fullscreen"));
    }}

    function syncButton() {{
        const active = isActive();
        button.innerHTML = active ? exitFullscreenIcon : fullscreenIcon;
        button.title = active ? "Exit fullscreen" : "Fullscreen";
        button.setAttribute("aria-label", button.title);
    }}

    function setActive(active) {{
        const target = findComponent();
        if (!target || !parentDocument) return;
        ensureStyle();
        target.classList.toggle("qq-streamlit-chart-fullscreen", active);
        parentDocument.documentElement.classList.toggle("qq-streamlit-chart-body-lock", active);
        parentDocument.body.classList.toggle("qq-streamlit-chart-body-lock", active);
        syncButton();
    }}

    button.addEventListener("click", () => setActive(!isActive()));
    if (parentDocument) {{
        parentDocument.addEventListener("keydown", event => {{
            if (event.key === "Escape" && isActive()) setActive(false);
        }});
        new MutationObserver(syncButton).observe(component || parentDocument.body, {{
            attributes: true,
            attributeFilter: ["class"],
            subtree: false
        }});
    }}
    syncButton();
    </script>
    """
    html = html.replace("{{", "{").replace("}}", "}")
    html = html.replace("__COMPONENT_SELECTOR__", component_selector_json)
    components.html(html, height=34, scrolling=False)


def chart_toolbar(
    *,
    tickers: list[str] | None,
    selected_ticker: str | None,
    timeframe_key: str,
    indicator_key: str,
    component_key: str,
    timeframe_options: list[str] | None = None,
) -> tuple[str | None, str, dict]:
    del indicator_key
    options = timeframe_options or ["1m", "5m"]
    columns = st.columns([0.9, 1.15, 8.9, 0.4], gap="small", vertical_alignment="center")
    ticker = selected_ticker
    with columns[0]:
        if tickers:
            ticker = st.selectbox("Ticker", tickers, index=tickers.index(selected_ticker) if selected_ticker in tickers else 0, label_visibility="collapsed")
        else:
            st.text(selected_ticker or "")
    with columns[1]:
        timeframe = st.segmented_control("Timeframe", options, default=options[0], key=timeframe_key, label_visibility="collapsed")
    with columns[3]:
        render_chart_fullscreen_button(component_key)
    return ticker, timeframe, default_chart_indicator_settings()


def tradingview_chart_payload(bars: pl.DataFrame, orders: pl.DataFrame, indicators: dict[str, dict]) -> dict:
    bars = normalize_bar_columns(bars).sort("bar_time_market")
    rows = bars.to_dicts()
    candles = []
    volumes = []
    overlay_series = []
    oscillator_series = []

    for row in rows:
        timestamp = row_chart_timestamp(row)
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
                    "color": hex_to_rgba(DEFAULT_CANDLE_CHART_SETTINGS["upColor" if close_price >= open_price else "downColor"], 0.28),
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
            timestamp = row_chart_timestamp(row)
            value = numeric_value(row.get(column))
            if timestamp and value is not None:
                point = {"time": timestamp, "value": value}
                if column == "macd_hist":
                    point["color"] = hex_to_rgba(DEFAULT_CANDLE_CHART_SETTINGS["upColor" if value >= 0 else "downColor"], opacity)
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
                    "showInLegend": column in LEGEND_INDICATORS,
                    "label": INDICATOR_DISPLAY_NAMES.get(column, column.upper()),
                    "data": points,
                }
            )

    markers = []
    if not orders.is_empty() and "filled_at" in orders.columns:
        for row in orders.filter(pl.col("status") == "FILLED").sort("filled_at").to_dicts():
            timestamp = chart_marker_timestamp(row.get("filled_at"))
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
        "sessionRegions": extended_session_regions(rows, candles),
    }


def render_lightweight_candle_chart(payload: dict, height: int = 720) -> None:
    chart_id = f"tv-chart-{abs(hash(json.dumps(payload, sort_keys=True))) % 10_000_000}"
    payload_json = json.dumps(payload)
    candle_settings_json = json.dumps(DEFAULT_CANDLE_CHART_SETTINGS)
    pane_gap = 10 if payload.get("oscillators") else 0
    oscillator_ratio = 0.375
    price_height = int((height - pane_gap) / (1 + oscillator_ratio)) if payload.get("oscillators") else height
    oscillator_height = int(price_height * oscillator_ratio) if payload.get("oscillators") else 0
    total_height = price_height + oscillator_height + pane_gap
    html = f"""
    <style>
    #{chart_id} {{
        background: #ffffff;
    }}
    </style>
    <div id="{chart_id}" style="height:{total_height}px;width:100%;display:flex;flex-direction:column;gap:{pane_gap}px;position:relative;">
        <div id="{chart_id}-price" style="height:{price_height}px;width:100%;position:relative;"></div>
        <div id="{chart_id}-osc" style="height:{oscillator_height}px;width:100%;position:relative;display:{'block' if oscillator_height else 'none'};"></div>
    </div>
    <script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js"></script>
    <script>
    const payload = {payload_json};
    const candleSettings = {candle_settings_json};
    const container = document.getElementById("{chart_id}");
    const priceContainer = document.getElementById("{chart_id}-price");
    const oscillatorContainer = document.getElementById("{chart_id}-osc");
    const hasOscillators = !!(payload.oscillators && payload.oscillators.length);
    const rightScaleWidth = 62;
    const indicatorLabelWidth = 62;
    const normalPriceHeight = {price_height};
    const normalOscillatorHeight = {oscillator_height};
    const paneGap = {pane_gap};
    const chartWidth = () => Math.max(260, container.clientWidth - indicatorLabelWidth);
    const exchangeTimeZone = "{CHART_EXCHANGE_TIME_ZONE}";
    const exchangeDateTimeFormatter = new Intl.DateTimeFormat("en-US", {{
        timeZone: exchangeTimeZone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hourCycle: "h23"
    }});
    function formatDateTime(time) {{
        const date = new Date(Number(time) * 1000);
        const parts = Object.fromEntries(exchangeDateTimeFormatter.formatToParts(date).map(part => [part.type, part.value]));
        return `${{parts.year}}-${{parts.month}}-${{parts.day}} ${{parts.hour}}:${{parts.minute}}:${{parts.second}}`;
    }}
    function formatTickMark(time, tickMarkType) {{
        const text = formatDateTime(time);
        const datePart = text.slice(0, 10);
        const timePart = text.slice(11, 16);
        if (Number(tickMarkType) <= 2) {{
            return datePart;
        }}
        return timePart;
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
            tickMarkFormatter: formatTickMark,
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
        width: chartWidth(),
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
        width: chartWidth(),
        height: {oscillator_height},
        rightPriceScale: {{
            borderColor: "#d1d5db",
            minimumWidth: rightScaleWidth,
            scaleMargins: {{ top: 0.12, bottom: 0.12 }}
        }}
    }}) : null;

    function createSessionLayer(parent) {{
        if (!parent) return null;
        const layer = document.createElement("div");
        layer.style.position = "absolute";
        layer.style.top = "0";
        layer.style.left = "0";
        layer.style.right = `${{indicatorLabelWidth}}px`;
        layer.style.bottom = "0";
        layer.style.pointerEvents = "none";
        layer.style.zIndex = "1";
        parent.appendChild(layer);
        return layer;
    }}

    const priceSessionLayer = createSessionLayer(priceContainer);
    const oscillatorSessionLayer = oscillatorChart ? createSessionLayer(oscillatorContainer) : null;

    function drawSessionLayer(layer, chartInstance) {{
        if (!layer || !chartInstance) return;
        layer.replaceChildren();
        const width = Math.max(0, layer.clientWidth);
        (payload.sessionRegions || []).forEach(region => {{
            const start = chartInstance.timeScale().timeToCoordinate(region.start);
            const end = chartInstance.timeScale().timeToCoordinate(region.end);
            if (start === null && end === null) return;
            const left = Math.max(0, Math.min(start ?? 0, end ?? width));
            const right = Math.min(width, Math.max(start ?? 0, end ?? width));
            if (right <= left) return;
            const block = document.createElement("div");
            block.style.position = "absolute";
            block.style.top = "0";
            block.style.bottom = "0";
            block.style.left = `${{left}}px`;
            block.style.width = `${{right - left}}px`;
            block.style.background = region.color;
            block.style.pointerEvents = "none";
            layer.appendChild(block);
        }});
    }}

    function drawSessionRegions() {{
        drawSessionLayer(priceSessionLayer, chart);
        if (oscillatorChart) drawSessionLayer(oscillatorSessionLayer, oscillatorChart);
    }}

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
    const seriesLabelColors = new Map();
    const priceLabelSeries = [];
    const oscillatorLabelSeries = [];
    function mapByTime(data) {{
        const mapped = new Map();
        (data || []).forEach(point => mapped.set(point.time, point));
        return mapped;
    }}
    seriesLabelColors.set(candleSeries, {{
        kind: "candle",
        upColor: candleSettings.upColor,
        downColor: candleSettings.downColor,
        fallback: candleSettings.upColor,
        series: candleSeries,
        dataByTime: mapByTime(payload.candles)
    }});
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
        seriesLabelColors.set(volumeSeries, {{
            kind: "histogram",
            upColor: candleSettings.upColor,
            downColor: candleSettings.downColor,
            fallback: candleSettings.upColor,
            series: volumeSeries,
            dataByTime: mapByTime(payload.volumes),
            skipAxisLabel: true
        }});
    }}

    priceContainer.style.position = "relative";
    if (oscillatorContainer) oscillatorContainer.style.position = "relative";
    const priceAxisLabels = document.createElement("div");
    priceAxisLabels.style.position = "absolute";
    priceAxisLabels.style.top = "0";
    priceAxisLabels.style.right = "0";
    priceAxisLabels.style.width = `${{indicatorLabelWidth}}px`;
    priceAxisLabels.style.height = "100%";
    priceAxisLabels.style.zIndex = 5;
    priceAxisLabels.style.pointerEvents = "none";
    priceContainer.appendChild(priceAxisLabels);
    const oscillatorAxisLabels = document.createElement("div");
    oscillatorAxisLabels.style.position = "absolute";
    oscillatorAxisLabels.style.top = "0";
    oscillatorAxisLabels.style.right = "0";
    oscillatorAxisLabels.style.width = `${{indicatorLabelWidth}}px`;
    oscillatorAxisLabels.style.height = "100%";
    oscillatorAxisLabels.style.zIndex = 5;
    oscillatorAxisLabels.style.pointerEvents = "none";
    if (oscillatorChart) oscillatorContainer.appendChild(oscillatorAxisLabels);

    function createLegendShell(parent, indicatorCount) {{
        const shell = document.createElement("div");
        shell.style.position = "absolute";
        shell.style.left = "12px";
        shell.style.top = "8px";
        shell.style.zIndex = 6;
        shell.style.font = "11px system-ui";
        shell.style.color = "#111827";
        shell.style.maxWidth = "220px";
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.textContent = `v ${{indicatorCount}} indicators`;
        toggle.style.display = "none";
        toggle.style.border = "0";
        toggle.style.background = "rgba(255,255,255,0.78)";
        toggle.style.backdropFilter = "blur(2px)";
        toggle.style.borderRadius = "4px";
        toggle.style.padding = "3px 6px";
        toggle.style.font = "11px system-ui";
        toggle.style.color = "#111827";
        toggle.style.cursor = "pointer";
        const legendNode = document.createElement("div");
        legendNode.style.display = "flex";
        legendNode.style.flexDirection = "column";
        legendNode.style.alignItems = "stretch";
        legendNode.style.gap = "3px";
        legendNode.style.background = "rgba(255,255,255,0.76)";
        legendNode.style.backdropFilter = "blur(2px)";
        legendNode.style.padding = "4px 5px";
        legendNode.style.borderRadius = "4px";
        const items = document.createElement("div");
        items.style.display = "flex";
        items.style.flexDirection = "column";
        items.style.gap = "3px";
        const collapse = document.createElement("button");
        collapse.type = "button";
        collapse.textContent = "^";
        collapse.title = "Collapse legend";
        collapse.style.alignSelf = "center";
        collapse.style.border = "0";
        collapse.style.background = "transparent";
        collapse.style.color = "#374151";
        collapse.style.cursor = "pointer";
        collapse.style.font = "14px system-ui";
        collapse.style.lineHeight = "12px";
        collapse.style.padding = "1px 10px";
        legendNode.appendChild(items);
        legendNode.appendChild(collapse);
        shell.appendChild(toggle);
        shell.appendChild(legendNode);
        parent.appendChild(shell);
        collapse.addEventListener("click", event => {{
            event.stopPropagation();
            legendNode.style.display = "none";
            toggle.style.display = "inline-flex";
        }});
        toggle.addEventListener("click", event => {{
            event.stopPropagation();
            toggle.style.display = "none";
            legendNode.style.display = "flex";
        }});
        return {{ items }};
    }}

    const priceLegend = createLegendShell(priceContainer, (payload.overlays || []).length);
    const oscillatorLegend = oscillatorChart
        ? createLegendShell(oscillatorContainer, (payload.oscillators || []).length)
        : null;
    if (priceLegend.items.childElementCount === 0 && !(payload.overlays || []).length) {{
        priceLegend.items.parentElement.parentElement.style.display = "none";
    }}
    if (oscillatorLegend && !(payload.oscillators || []).length) {{
        oscillatorLegend.items.parentElement.parentElement.style.display = "none";
    }}

    function iconSvg(kind) {{
        if (kind === "eye") {{
            return '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
        }}
        return '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 21v-7"></path><path d="M4 10V3"></path><path d="M12 21v-9"></path><path d="M12 8V3"></path><path d="M20 21v-5"></path><path d="M20 12V3"></path><path d="M2 14h4"></path><path d="M10 8h4"></path><path d="M18 16h4"></path></svg>';
    }}

    const openLegendPanels = new Set();
    function closeLegendPanels(exceptPanel = null) {{
        openLegendPanels.forEach(panel => {{
            if (panel !== exceptPanel) {{
                panel.style.display = "none";
                if (panel._legendActions) panel._legendActions.style.display = "none";
            }}
        }});
    }}
    document.addEventListener("click", () => closeLegendPanels());
    window.addEventListener("blur", () => closeLegendPanels());

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
        label.textContent = indicator.label || String(indicator.name || "").toUpperCase();
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
        panel.style.minWidth = "210px";
        panel.addEventListener("click", event => event.stopPropagation());
        panel._legendActions = actions;
        const colorInput = document.createElement("input");
        colorInput.type = "color";
        colorInput.value = indicator.legendColor || "#2563eb";
        const hexInput = document.createElement("input");
        hexInput.type = "text";
        hexInput.value = colorInput.value.toUpperCase();
        hexInput.spellcheck = false;
        hexInput.style.width = "82px";
        hexInput.style.font = "11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
        hexInput.style.border = "1px solid #d1d5db";
        hexInput.style.borderRadius = "3px";
        hexInput.style.padding = "2px 4px";
        const opacityInput = document.createElement("input");
        opacityInput.type = "range";
        opacityInput.min = "0.15";
        opacityInput.max = "1";
        opacityInput.step = "0.05";
        opacityInput.value = indicator.opacity || 0.72;
        const opacityValue = document.createElement("span");
        opacityValue.style.font = "11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
        opacityValue.textContent = Number(opacityInput.value).toFixed(2);
        const widthInput = document.createElement("input");
        widthInput.type = "range";
        widthInput.min = "1";
        widthInput.max = "4";
        widthInput.step = "1";
        widthInput.value = indicator.lineWidth || 1;
        const widthValue = document.createElement("span");
        widthValue.style.font = "11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
        widthValue.textContent = widthInput.value;
        panel.innerHTML = '<div style="font-weight:700;margin-bottom:4px;">' + (indicator.label || String(indicator.name || "").toUpperCase()) + '</div>';
        [["Color", colorInput], ["Hex", hexInput], ["Opacity", opacityInput, opacityValue], ["Width", widthInput, widthValue]].forEach(([caption, input, value]) => {{
            const row = document.createElement("label");
            row.style.display = "grid";
            row.style.gridTemplateColumns = value ? "48px 1fr 36px" : "48px 1fr";
            row.style.alignItems = "center";
            row.style.gap = "6px";
            row.style.margin = "3px 0";
            row.appendChild(document.createTextNode(caption));
            row.appendChild(input);
            if (value) row.appendChild(value);
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
            hexInput.value = baseColor.toUpperCase();
            opacityValue.textContent = opacity.toFixed(2);
            widthValue.textContent = String(indicator.lineWidth);
            swatch.style.background = rgba;
            item.style.color = "#111827";
            if (indicator.style === "histogram") {{
                indicator.currentData = (indicator.data || []).map(point => ({{
                    ...point,
                    color: point.value >= 0 ? hexToRgba(candleSettings.upColor, opacity) : hexToRgba(candleSettings.downColor, opacity)
                }}));
                if (!indicator.hidden) series.setData(indicator.currentData);
            }} else {{
                series.applyOptions({{ color: rgba, lineWidth: indicator.lineWidth }});
            }}
        }}
        function normalizeHex(value) {{
            const match = String(value || "").trim().match(/^#?[0-9a-fA-F]{{6}}$/);
            return match ? ("#" + String(value).trim().replace("#", "")).toUpperCase() : null;
        }}
        item.addEventListener("mouseenter", () => {{ actions.style.display = "inline-flex"; }});
        item.addEventListener("mouseleave", () => {{ if (panel.style.display === "none") actions.style.display = "none"; }});
        item.addEventListener("click", event => event.stopPropagation());
        settings.addEventListener("click", event => {{
            event.stopPropagation();
            const shouldOpen = panel.style.display === "none";
            closeLegendPanels(shouldOpen ? panel : null);
            panel.style.display = shouldOpen ? "block" : "none";
            actions.style.display = "inline-flex";
            if (shouldOpen) openLegendPanels.add(panel);
        }});
        eye.addEventListener("click", event => {{
            event.stopPropagation();
            indicator.hidden = !indicator.hidden;
            const meta = seriesLabelColors.get(series);
            if (meta) meta.hidden = indicator.hidden;
            series.setData(indicator.hidden ? [] : (indicator.currentData || indicator.data || []));
            item.style.opacity = indicator.hidden ? "0.38" : "1";
            label.style.textDecoration = indicator.hidden ? "line-through" : "none";
        }});
        colorInput.addEventListener("input", applyVisuals);
        opacityInput.addEventListener("input", applyVisuals);
        widthInput.addEventListener("input", applyVisuals);
        hexInput.addEventListener("change", () => {{
            const normalized = normalizeHex(hexInput.value);
            if (normalized) {{
                colorInput.value = normalized;
                applyVisuals();
            }} else {{
                hexInput.value = colorInput.value.toUpperCase();
            }}
        }});
    }}

    function hexToRgba(hex, opacity) {{
        const normalized = hex.replace("#", "");
        const value = parseInt(normalized.length === 3 ? normalized.split("").map(ch => ch + ch).join("") : normalized, 16);
        const red = (value >> 16) & 255;
        const green = (value >> 8) & 255;
        const blue = value & 255;
        return `rgba(${{red}}, ${{green}}, ${{blue}}, ${{opacity.toFixed(2)}})`;
    }}

    function axisLabelValue(meta, point) {{
        if (!meta || !point) return null;
        if (meta.kind === "candle") return point.close;
        return point.value;
    }}

    function axisLabelColor(meta, point) {{
        if (!meta || !point) return null;
        if (meta.kind === "candle") {{
            return point.close >= point.open ? meta.upColor : meta.downColor;
        }}
        if (meta.kind === "histogram") {{
            if (point.color) return point.color;
            return point.value >= 0 ? meta.upColor : meta.downColor;
        }}
        return meta.fallback;
    }}

    function formatAxisLabelValue(value) {{
        const absValue = Math.abs(Number(value));
        if (!Number.isFinite(absValue)) return "";
        if (absValue >= 1000) return Number(value).toFixed(0);
        if (absValue >= 100) return Number(value).toFixed(2);
        if (absValue >= 1) return Number(value).toFixed(4);
        return Number(value).toFixed(5);
    }}

    function clearAxisLabels(layer) {{
        if (layer) layer.replaceChildren();
    }}

    function renderAxisLabels(layer, entries, time) {{
        if (!layer) return;
        layer.replaceChildren();
        if (time === undefined || time === null) return;
        entries.forEach(meta => {{
            if (!meta || meta.hidden || meta.skipAxisLabel || !meta.series || !meta.dataByTime) return;
            const point = meta.dataByTime.get(time);
            const value = axisLabelValue(meta, point);
            if (value === null || value === undefined) return;
            const coordinate = meta.series.priceToCoordinate(value);
            if (coordinate === null || coordinate === undefined || Number.isNaN(coordinate)) return;
            const label = document.createElement("div");
            label.textContent = formatAxisLabelValue(value);
            label.style.position = "absolute";
            label.style.left = "-1px";
            label.style.top = `${{coordinate}}px`;
            label.style.transform = "translateY(-50%)";
            label.style.maxWidth = `${{indicatorLabelWidth + 1}}px`;
            label.style.overflow = "hidden";
            label.style.textOverflow = "ellipsis";
            label.style.whiteSpace = "nowrap";
            label.style.background = axisLabelColor(meta, point) || meta.fallback || "#111827";
            label.style.color = "#ffffff";
            label.style.borderRadius = "0";
            label.style.padding = "2px 5px";
            label.style.font = "12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
            label.style.lineHeight = "15px";
            label.style.boxShadow = "0 1px 2px rgba(15,23,42,0.22)";
            layer.appendChild(label);
        }});
    }}

    function renderCrosshairAxisLabels(time) {{
        renderAxisLabels(priceAxisLabels, priceLabelSeries, time);
        if (oscillatorChart) {{
            renderAxisLabels(oscillatorAxisLabels, oscillatorLabelSeries, time);
        }}
    }}

    (payload.overlays || []).forEach((indicator) => {{
        const line = chart.addLineSeries({{
            color: indicator.color,
            lineWidth: indicator.lineWidth || 1,
            priceLineVisible: false,
            lastValueVisible: false
        }});
        line.setData(indicator.data || []);
        seriesLabelColors.set(line, {{
            kind: "line",
            fallback: indicator.legendColor || indicator.color,
            series: line,
            dataByTime: mapByTime(indicator.data)
        }});
        priceLabelSeries.push(seriesLabelColors.get(line));
        if (indicator.showInLegend !== false) addLegendItem(indicator, priceLegend.items, line);
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
            seriesLabelColors.set(series, {{
                kind: indicator.style === "histogram" ? "histogram" : "line",
                upColor: candleSettings.upColor,
                downColor: candleSettings.downColor,
                fallback: indicator.legendColor || indicator.color,
                series,
                dataByTime: mapByTime(indicator.data),
                skipAxisLabel: indicator.style === "histogram"
            }});
            if (indicator.style !== "histogram") oscillatorLabelSeries.push(seriesLabelColors.get(series));
            if (!firstOscillatorSeries) {{
                firstOscillatorSeries = series;
            }}
            (indicator.data || []).forEach(point => {{
                if (!oscillatorValueByTime.has(point.time)) oscillatorValueByTime.set(point.time, point.value);
            }});
            if (oscillatorLegend && indicator.showInLegend !== false) addLegendItem(indicator, oscillatorLegend.items, series);
        }});
    }}

    function alignTimeScales() {{
        chart.timeScale().fitContent();
        if (oscillatorChart) {{
            const range = chart.timeScale().getVisibleLogicalRange();
            if (range) oscillatorChart.timeScale().setVisibleLogicalRange(range);
        }}
        requestAnimationFrame(drawSessionRegions);
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
                drawSessionRegions();
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
                    clearAxisLabels(priceAxisLabels);
                    clearAxisLabels(oscillatorAxisLabels);
                    return;
                }}
                renderCrosshairAxisLabels(param.time);
                const value = oscillatorValueByTime.get(param.time);
                if (value !== undefined && firstOscillatorSeries) {{
                    oscillatorChart.setCrosshairPosition(value, param.time, firstOscillatorSeries);
                }}
            }});
            oscillatorChart.subscribeCrosshairMove(param => {{
                if (!param || param.time === undefined) {{
                    chart.clearCrosshairPosition();
                    clearAxisLabels(priceAxisLabels);
                    clearAxisLabels(oscillatorAxisLabels);
                    return;
                }}
                renderCrosshairAxisLabels(param.time);
                const value = priceByTime.get(param.time);
                if (value !== undefined) chart.setCrosshairPosition(value, param.time, candleSeries);
            }});
        }}
    }} else {{
        chart.subscribeCrosshairMove(param => {{
            if (!param || param.time === undefined) {{
                clearAxisLabels(priceAxisLabels);
                return;
            }}
            renderCrosshairAxisLabels(param.time);
        }});
    }}
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(drawSessionRegions));
    function isChartExpanded() {{
        return document.fullscreenElement === container || window.innerHeight > {total_height + 80};
    }}

    function chartHeights() {{
        if (!isChartExpanded()) {{
            return {{ price: normalPriceHeight, oscillator: normalOscillatorHeight, total: {total_height} }};
        }}
        const available = Math.max(360, window.innerHeight - 4);
        if (!hasOscillators) {{
            return {{ price: available, oscillator: 0, total: available }};
        }}
        const price = Math.floor((available - paneGap) / (1 + {oscillator_ratio}));
        const oscillator = Math.max(120, Math.floor(price * {oscillator_ratio}));
        return {{ price, oscillator, total: price + oscillator + paneGap }};
    }}

    function resizeCharts() {{
        const heights = chartHeights();
        container.style.height = `${{heights.total}}px`;
        priceContainer.style.height = `${{heights.price}}px`;
        if (oscillatorContainer) oscillatorContainer.style.height = `${{heights.oscillator}}px`;
        const width = chartWidth();
        chart.applyOptions({{ width, height: heights.price }});
        if (oscillatorChart) oscillatorChart.applyOptions({{ width, height: heights.oscillator }});
        alignTimeScales();
        requestAnimationFrame(drawSessionRegions);
    }}

    window.addEventListener("resize", resizeCharts);
    const resizeObserver = new ResizeObserver(entries => {{
        if (!entries.length) return;
        resizeCharts();
    }});
    resizeObserver.observe(container);
    </script>
    """
    components.html(html, height=total_height + 12, scrolling=False)


def candle_chart(
    bars: pl.DataFrame,
    orders: pl.DataFrame,
    indicators: dict[str, dict],
    height: int = 720,
) -> None:
    if bars.is_empty():
        st.info("No chart data available.")
        return
    bars = normalize_bar_columns(bars).sort("bar_time_market")
    required = ["bar_time_market", "open", "high", "low", "close"]
    if any(col not in bars.columns for col in required):
        st.info("Selected chart data is missing OHLC columns.")
        return
    payload = tradingview_chart_payload(bars, orders, indicators)
    render_lightweight_candle_chart(payload, height=height)


def bars_for(data: dict, period: str, ticker: str, timeframe: str) -> pl.DataFrame:
    data_root, exchange_timezone = chart_source_config(data)
    session_dates = tuple(chart_session_dates(data, period))
    if session_dates and ticker:
        extended_bars = load_extended_chart_bars(data_root, exchange_timezone, session_dates, ticker, timeframe)
        if not extended_bars.is_empty():
            return extended_bars
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
        chart_key = chart_key_fragment(period)
        component_key = f"chart_component_trades_{chart_key}"
        with st.container(key=component_key):
            with st.container(key=f"chart_toolbar_trades_{chart_key}"):
                _, timeframe, indicators = chart_toolbar(
                    tickers=None,
                    selected_ticker=str(trade.get("symbol", "")),
                    timeframe_key=f"trade_tf_{period}",
                    indicator_key=f"trade_ind_{period}",
                    component_key=component_key,
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
    chart_key = chart_key_fragment(period)
    component_key = f"chart_component_inspector_{chart_key}"
    with st.container(key=component_key):
        with st.container(key=f"chart_toolbar_inspector_{chart_key}"):
            ticker, timeframe, indicators = chart_toolbar(
                tickers=tickers,
                selected_ticker=tickers[0] if tickers else None,
                timeframe_key=f"inspect_tf_{period}",
                indicator_key=f"inspect_ind_{period}",
                component_key=component_key,
            )
        bars = bars_for(data, period, ticker, timeframe)
        orders = filter_df(data["orders"], period)
        if "symbol" in orders.columns:
            orders = orders.filter(pl.col("symbol") == ticker)
        candle_chart(bars, orders, indicators)


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
