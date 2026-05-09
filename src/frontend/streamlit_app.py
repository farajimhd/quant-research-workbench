from __future__ import annotations

import json
import re
import shutil
import sys
import traceback
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from html import escape
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
from src.data_provider.builder import build_market_data
from src.data_provider.config import (
    DEFAULT_PROCESSED_ROOT,
    FEATURE_GROUPS,
    SUPERVISION_GROUPS,
    TIMEFRAMES,
    BuildRequest,
    DataProviderConfig,
)
from src.data_provider.calendar import discover_raw_bounds, scan_market_source
from src.data_provider.manifest import read_manifest
from src.data_provider.provider import MarketDataProvider
from src.strategies.orb_5m_momentum.config import OrbMomentumConfig
from src.strategies.registry import available_strategies


DEFAULT_DATA_ROOT = Path("D:/TradingData/massive_flatfiles/us_stock_sip/minutes_agg_v1")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingData/quant-research-workbench/runs")
DEFAULT_MARKET_DATA_ROOT = DEFAULT_PROCESSED_ROOT
CHART_EXTENDED_START_MINUTE = 4 * 60
CHART_REGULAR_START_MINUTE = 9 * 60 + 30
CHART_REGULAR_END_MINUTE = 16 * 60
DEFAULT_TABLE_ROW_HEIGHT = 46
CHART_EXTENDED_END_MINUTE = 20 * 60
CHART_EXCHANGE_TIME_ZONE = "America/New_York"
APP_TITLE = "Quant Research Workbench"

STRATEGY_DESCRIPTIONS = {
    "orb_5m_momentum": (
        "Opening-range momentum strategy using a 09:30-09:35 setup ranking, "
        "minute-by-minute live ranking, and completed 5-minute MACD/TEMA confirmation."
    )
}

DISPLAY_TOKEN_OVERRIDES = {
    "ibkr": "IBKR",
    "macd": "MACD",
    "orb": "ORB",
    "qq": "QQ",
    "vwap": "VWAP",
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
    "vwap": "#5B21B6",
    "tema9": "#2563EB",
    "tema20": "#B7791F",
    "macd_line": "#1E3A5F",
    "macd_signal": "#B54708",
    "macd_hist": "#067647",
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
    "upColor": "#067647",
    "downColor": "#B42318",
    "borderUpColor": "#05603A",
    "borderDownColor": "#912018",
    "wickUpColor": "#17B26A",
    "wickDownColor": "#D92D20",
    "borderVisible": True,
    "wickVisible": True,
    "priceLineVisible": True,
    "barSpacing": 40,
    "minBarSpacing": 0.2,
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
        @import url("https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap");
        :root {
            --qq-bg: var(--st-background-color, #FFFFFF);
            --qq-surface: #FFFFFF;
            --qq-surface-muted: var(--st-secondary-background-color, #F8FAFC);
            --qq-surface-soft: #F9FAFB;
            --qq-text: var(--st-text-color, #111827);
            --qq-muted: #6B7280;
            --qq-muted-strong: #4B5563;
            --qq-border: var(--st-border-color, #EAEFF5);
            --qq-border-soft: #EEF2F7;
            --qq-primary: var(--st-primary-color, #1E3A5F);
            --qq-primary-hover: #172D49;
            --qq-primary-soft: #EFF6FF;
            --qq-primary-border: #BFDBFE;
            --qq-success: #067647;
            --qq-success-bg: #ECFDF3;
            --qq-success-border: #ABEFC6;
            --qq-danger: #B42318;
            --qq-danger-bg: #FEF3F2;
            --qq-neutral-bg: #F9FAFB;
            --qq-radius: 3px;
        }
        html, body, body *, .stApp, button, input, textarea, select {
            font-family: "Inter", sans-serif !important;
        }
        [data-testid="stIconMaterial"],
        [class*="material-icons"],
        [class*="material-symbols"],
        [class*="MaterialIcons"],
        [class*="MaterialSymbols"] {
            font-family: "Material Symbols Rounded", "Material Icons" !important;
            font-feature-settings: "liga" !important;
            font-size: 1rem !important;
            font-style: normal !important;
            font-weight: 400 !important;
            letter-spacing: normal !important;
            line-height: 1 !important;
            text-transform: none !important;
            white-space: nowrap !important;
        }
        .stApp {
            background: var(--qq-bg);
            color: var(--qq-text);
        }
        [data-testid="stDataFrameColumnMenu"] {
            background: #FFFFFF !important;
            border: 1px solid var(--qq-border) !important;
            border-radius: var(--qq-radius) !important;
            box-shadow: 0 10px 24px rgba(17, 24, 39, 0.10) !important;
            min-width: 11.5rem !important;
            padding: 0.25rem !important;
        }
        [data-testid="stDataFrameColumnMenu"] [role="menuitem"],
        [data-testid="stDataFrameColumnMenu"] button {
            color: var(--qq-text) !important;
            font-family: "Inter", sans-serif !important;
            font-weight: 400 !important;
        }
        [data-testid="stDataFrameColumnMenu"] [role="menuitem"]:hover,
        [data-testid="stDataFrameColumnMenu"] button:hover {
            background: #F8FAFC !important;
            color: var(--qq-primary) !important;
        }
        [data-testid="stDataFrameColumnMenu"] button[title="Copy column name"]:focus,
        [data-testid="stDataFrameColumnMenu"] button[title="Copy column name"]:focus-visible {
            border-color: var(--qq-primary) !important;
            box-shadow: none !important;
            outline: 1px solid var(--qq-primary) !important;
        }
        div[data-testid="stButton"] button[kind="primary"] {
            background: var(--qq-primary) !important;
            border-color: var(--qq-primary) !important;
            color: #FFFFFF !important;
            font-weight: 500;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover {
            background: var(--qq-primary-hover) !important;
            border-color: var(--qq-primary-hover) !important;
            color: #FFFFFF !important;
        }
        div[data-testid="stButton"] button[kind="secondary"] {
            border-color: var(--qq-border) !important;
            color: var(--qq-text) !important;
        }
        div[data-testid="stButton"] button[kind="secondary"]:hover {
            border-color: var(--qq-primary) !important;
            color: var(--qq-primary) !important;
        }
        div[data-testid="stButton"] button p {
            white-space: nowrap;
        }
        [data-testid="stMultiSelect"] [data-baseweb="tag"] {
            background-color: var(--qq-primary-soft) !important;
            border: 1px solid var(--qq-primary-border) !important;
            border-radius: var(--qq-radius) !important;
            color: var(--qq-primary) !important;
            height: 1.62rem !important;
            margin-bottom: 0.12rem !important;
            margin-top: 0.12rem !important;
            min-height: 1.62rem !important;
        }
        [data-testid="stMultiSelect"] [data-baseweb="tag"] span,
        [data-testid="stMultiSelect"] [data-baseweb="tag"] svg,
        [data-testid="stMultiSelect"] [data-baseweb="tag"] button {
            color: var(--qq-primary) !important;
            fill: var(--qq-primary) !important;
        }
        [data-testid="stMultiSelect"] [data-baseweb="tag"] span {
            font-size: 0.88rem !important;
            line-height: 1 !important;
        }
        [data-testid="stMultiSelect"] [data-baseweb="tag"]:hover {
            background-color: #DBEAFE !important;
            border-color: #93C5FD !important;
        }
        [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
        [data-testid="stTextInput"] input,
        [data-testid="stDateInput"] input {
            background-color: #FFFFFF !important;
            border-color: var(--qq-border) !important;
            border-radius: var(--qq-radius) !important;
        }
        [data-testid="stDataFrame"] {
            background: #FFFFFF !important;
            border: 0 !important;
            border-bottom: 1px solid #E5E7EB !important;
            border-top: 1px solid #E5E7EB !important;
            border-radius: 2px !important;
            box-shadow: none !important;
            overflow: hidden !important;
        }
        [data-testid="stDataFrame"] > div {
            background: #FFFFFF !important;
            border-left: 0 !important;
            border-right: 0 !important;
            border-radius: 2px !important;
        }
        [data-testid="stDataFrame"] canvas {
            background: #FFFFFF !important;
        }
        [data-testid="stDataFrameResizable"] {
            border-color: transparent !important;
            position: relative !important;
        }
        [data-testid="stDataFrameResizable"]::after {
            background-image:
                linear-gradient(to bottom, transparent 0, transparent 35px, #E5E7EB 35px, #E5E7EB 36px, transparent 36px),
                repeating-linear-gradient(to bottom, transparent 0, transparent 45px, #E5E7EB 45px, #E5E7EB 46px);
            background-position: 0 0, 0 35px;
            background-repeat: no-repeat, repeat;
            background-size: 100% 100%, 100% 46px;
            content: "";
            inset: 0;
            pointer-events: none;
            position: absolute;
            z-index: 2;
        }
        [data-testid="stDataFrame"] [data-testid="stElementToolbar"] {
            padding: 0.35rem !important;
        }
        [data-testid="stDataFrame"] [data-testid="stElementToolbarButton"] button {
            background: rgba(255, 255, 255, 0.92) !important;
            border: 1px solid #E5E7EB !important;
            border-radius: 3px !important;
            box-shadow: none !important;
            color: #4B5563 !important;
            height: 2rem !important;
            width: 2rem !important;
        }
        [data-testid="stDataFrame"] [data-testid="stElementToolbarButton"] button:hover {
            background: #F9FAFB !important;
            border-color: #D1D5DB !important;
            color: #111827 !important;
        }
        [data-testid="stMarkdownContainer"] table {
            background: #FFFFFF;
            border: 0;
            border-bottom: 1px solid #E5E7EB;
            border-top: 1px solid #E5E7EB;
            border-collapse: collapse;
            border-radius: 2px;
            overflow: hidden;
            width: 100%;
        }
        [data-testid="stMarkdownContainer"] th {
            background: transparent !important;
            color: #4B5563;
            font-size: 0.74rem;
            font-weight: 600;
            letter-spacing: 0.04em;
            line-height: 1;
            text-transform: uppercase;
        }
        [data-testid="stMarkdownContainer"] th,
        [data-testid="stMarkdownContainer"] td {
            border-color: #E5E7EB transparent;
            padding: 0.78rem 1rem;
        }
        [data-baseweb="tab"][aria-selected="true"] {
            color: var(--qq-primary) !important;
        }
        [data-baseweb="tab-highlight"] {
            background-color: var(--qq-primary) !important;
        }
        .block-container {
            max-width: 100%;
            padding: 5.35rem 2rem 2rem;
        }
        body:has(.qq-sidebar-state-collapsed) .block-container {
            padding-left: 1rem;
        }
        [data-testid="stHeader"] {
            align-items: center;
            background: rgba(255, 255, 255, 0.82);
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
            border-bottom: 1px solid rgba(216, 224, 234, 0.92);
            box-shadow: none;
            display: flex;
            height: 4.5rem;
            left: 0 !important;
            padding-left: 2rem;
            position: fixed;
            right: 0 !important;
            top: 0;
            width: 100vw !important;
            z-index: 999;
        }
        [data-testid="stHeader"]::before {
            color: var(--qq-text);
            content: "Quant Research Workbench";
            font-family: "Inter", sans-serif !important;
            font-size: 1.05rem;
            font-weight: 650;
            letter-spacing: 0;
            line-height: 1;
            white-space: nowrap;
        }
        [data-testid="stSidebar"] {
            background: var(--qq-surface);
            border-right: 1px solid var(--qq-border-soft);
            flex: 0 0 15.5rem !important;
            height: calc(100vh - 4.5rem) !important;
            min-width: 15.5rem !important;
            overflow: visible !important;
            position: relative;
            top: 4.5rem !important;
            width: 15.5rem !important;
            z-index: 1000;
        }
        [data-testid="stSidebar"] > div,
        [data-testid="stSidebarContent"] {
            background: var(--qq-surface);
            overflow: visible !important;
            width: 15.5rem !important;
        }
        [data-testid="stSidebarHeader"] {
            display: none !important;
            height: 0 !important;
            min-height: 0 !important;
            padding: 0 !important;
        }
        [data-testid="stSidebar"] > div:first-child {
            background: var(--qq-surface);
            padding: 1.05rem 0.95rem 1.1rem;
        }
        .qq-sidebar-group {
            color: var(--qq-muted);
            font-size: 0.78rem;
            font-weight: 600;
            letter-spacing: 0.09em;
            line-height: 1;
            padding: 0.95rem 0.55rem 0.55rem;
            text-transform: uppercase;
        }
        [data-testid="stSidebar"] div[data-testid="stButton"] {
            margin: 0.05rem 0;
            width: 100% !important;
        }
        [data-testid="stSidebar"] button[kind="secondary"],
        [data-testid="stSidebar"] button[kind="primary"],
        [data-testid="stSidebar"] div[data-testid="stButton"] button {
            display: inline-flex;
            align-items: center;
            justify-content: flex-start !important;
            width: 100% !important;
            min-height: 3rem;
            padding: 0.5rem 0.9rem;
            border: 0;
            border-radius: var(--qq-radius);
            background: transparent;
            color: var(--qq-muted);
            box-shadow: none;
            font-size: 1rem;
            font-weight: 400;
            gap: 0.85rem;
            line-height: 1;
        }
        [data-testid="stSidebar"] div[data-testid="stButton"] button > div,
        [data-testid="stSidebar"] div[data-testid="stButton"] button span {
            justify-content: flex-start !important;
            text-align: left !important;
        }
        [data-testid="stSidebar"] div[data-testid="stButton"] button:disabled {
            background: transparent !important;
            color: var(--qq-muted-strong) !important;
            cursor: default;
            font-weight: 400;
            opacity: 1;
        }
        [data-testid="stSidebar"] button[kind="secondary"]:hover {
            background: transparent !important;
            color: var(--qq-muted-strong) !important;
            border: 0;
        }
        [data-testid="stSidebar"] button[kind="primary"] {
            background: transparent;
            color: var(--qq-muted-strong);
            font-weight: 400;
        }
        [data-testid="stSidebar"] button[kind="primary"]:hover {
            background: transparent !important;
            color: var(--qq-muted-strong) !important;
            border: 0;
        }
        [data-testid="stSidebar"] button svg {
            width: 1.42rem;
            height: 1.42rem;
            color: currentColor;
            flex: 0 0 1.42rem;
        }
        [data-testid="stSidebar"] button p {
            color: currentColor;
            font-size: 1rem;
            font-weight: inherit;
            line-height: 1;
            margin: 0;
            min-width: 0;
            overflow: hidden;
            text-align: left;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        [data-testid="stSidebar"] div[class*="st-key-sidebar_strategy"] button::before,
        [data-testid="stSidebar"] div[class*="st-key-sidebar_market_data"] button::before,
        [data-testid="stSidebar"] div[class*="st-key-sidebar_toggle"] button::before {
            content: "";
            display: inline-block;
            width: 1.42rem;
            height: 1.42rem;
            flex: 0 0 1.42rem;
            background-color: currentColor;
            mask-repeat: no-repeat;
            mask-position: center;
            mask-size: 1.25rem 1.25rem;
            -webkit-mask-repeat: no-repeat;
            -webkit-mask-position: center;
            -webkit-mask-size: 1.25rem 1.25rem;
        }
        [data-testid="stSidebar"] div[class*="st-key-sidebar_strategy"] button::before {
            mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 19V9'/%3E%3Cpath d='M10 19V5'/%3E%3Cpath d='M16 19v-8'/%3E%3Cpath d='M22 19V3'/%3E%3Cpath d='m3 9 7-4 6 6 6-8'/%3E%3C/svg%3E");
            -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 19V9'/%3E%3Cpath d='M10 19V5'/%3E%3Cpath d='M16 19v-8'/%3E%3Cpath d='M22 19V3'/%3E%3Cpath d='m3 9 7-4 6 6 6-8'/%3E%3C/svg%3E");
        }
        [data-testid="stSidebar"] div[class*="st-key-sidebar_market_data"] button::before {
            mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cellipse cx='12' cy='5' rx='8' ry='3'/%3E%3Cpath d='M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5'/%3E%3Cpath d='M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'/%3E%3C/svg%3E");
            -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cellipse cx='12' cy='5' rx='8' ry='3'/%3E%3Cpath d='M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5'/%3E%3Cpath d='M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'/%3E%3C/svg%3E");
        }
        [data-testid="stSidebar"] div[class*="st-key-sidebar_toggle"] button::before {
            mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m15 18-6-6 6-6'/%3E%3C/svg%3E");
            -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m15 18-6-6 6-6'/%3E%3C/svg%3E");
        }
        [data-testid="stSidebar"] div[class*="st-key-sidebar_toggle"] {
            margin: 0 0 0.75rem auto !important;
            pointer-events: auto;
            position: relative;
            transform: translateX(1.05rem);
            width: 2.1rem !important;
            z-index: 1001;
        }
        [data-testid="stSidebar"] div[class*="st-key-sidebar_toggle"] button {
            align-items: center;
            aspect-ratio: 1 / 1;
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            background: rgba(255, 255, 255, 0.54) !important;
            border: 1px solid rgba(216, 224, 234, 0.74);
            border-radius: 999px !important;
            box-shadow: 0 6px 18px rgba(23, 32, 51, 0.08);
            display: inline-flex;
            gap: 0 !important;
            height: 2.1rem;
            justify-content: center !important;
            line-height: 0 !important;
            max-height: 2.1rem;
            max-width: 2.1rem;
            min-height: 2.1rem;
            min-width: 2.1rem;
            padding: 0;
            position: relative;
            width: 2.1rem !important;
        }
        [data-testid="stSidebar"] div[class*="st-key-sidebar_toggle"] button:hover {
            background: rgba(255, 255, 255, 0.72) !important;
            color: var(--qq-primary) !important;
            border: 1px solid rgba(194, 205, 219, 0.9);
        }
        [data-testid="stSidebar"] div[class*="st-key-sidebar_toggle"] button p {
            font-size: 0;
            height: 0;
            margin: 0;
            overflow: hidden;
            width: 0;
        }
        [data-testid="stSidebar"] div[class*="st-key-sidebar_toggle"] button::before {
            display: block;
            width: 1.1rem;
            height: 1.1rem;
            flex-basis: 1.1rem;
            left: 50%;
            margin: 0 !important;
            mask-size: 1.1rem 1.1rem;
            position: absolute;
            top: 50%;
            transform: translate(-50%, -50%);
            -webkit-mask-size: 1.1rem 1.1rem;
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) {
            flex: 0 0 3.35rem !important;
            min-width: 3.35rem !important;
            width: 3.35rem !important;
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) > div,
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) [data-testid="stSidebarContent"] {
            width: 3.35rem !important;
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) > div:first-child {
            padding-left: 0.3rem;
            padding-right: 0.3rem;
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) div[class*="st-key-sidebar_toggle"] button::before {
            mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m9 18 6-6-6-6'/%3E%3C/svg%3E");
            -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m9 18 6-6-6-6'/%3E%3C/svg%3E");
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) .qq-sidebar-group {
            display: none;
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) div[data-testid="stButton"] {
            margin: 0.18rem auto;
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) div[data-testid="stButton"] button {
            align-items: center !important;
            display: flex !important;
            justify-content: center !important;
            min-height: 2.85rem;
            padding: 0;
            width: 100% !important;
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) div[data-testid="stButton"] button > div,
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) div[data-testid="stButton"] button span,
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) div[data-testid="stButton"] button p {
            display: none;
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) div[class*="st-key-sidebar_strategy"] button::before,
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) div[class*="st-key-sidebar_market_data"] button::before {
            flex: 0 0 1.42rem;
            margin: 0 !important;
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) div[class*="st-key-sidebar_toggle"] {
            display: flex !important;
            justify-content: center !important;
            margin: 0.18rem -0.3rem 0.6rem !important;
            transform: translateX(0.75rem);
            width: calc(100% + 0.6rem) !important;
        }
        [data-testid="stSidebar"]:has(.qq-sidebar-state-collapsed) div[class*="st-key-sidebar_toggle"] button {
            background: rgba(255, 255, 255, 0.54) !important;
            border: 1px solid rgba(216, 224, 234, 0.74) !important;
            border-radius: 999px !important;
            height: 2rem !important;
            max-height: 2rem !important;
            max-width: 2rem !important;
            min-height: 2rem !important;
            min-width: 2rem !important;
            padding: 0 !important;
            width: 2rem !important;
        }
        h1 {
            font-size: 1.45rem !important;
            line-height: 1.2 !important;
            margin-bottom: 0.2rem !important;
        }
        .st-key-run_header h1 {
            margin: 0 !important;
        }
        .qq-run-header-line {
            display: flex;
            align-items: center;
            gap: 0.65rem;
            min-height: 2.15rem;
            overflow: hidden;
            white-space: nowrap;
        }
        .qq-run-header-title {
            color: var(--qq-text);
            font-size: 1.35rem;
            font-weight: 750;
            line-height: 1;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .qq-run-badges {
            display: flex;
            flex-wrap: nowrap;
            gap: 0.35rem;
            align-items: center;
            min-width: 0;
            overflow: hidden;
        }
        .qq-run-badge {
            display: inline-flex;
            align-items: center;
            flex: 0 1 auto;
            max-width: 100%;
            border: 1px solid var(--qq-border);
            border-radius: var(--qq-radius);
            padding: 0.18rem 0.5rem;
            background: var(--qq-surface-soft);
            color: var(--qq-muted-strong);
            font-size: 0.78rem;
            line-height: 1.1;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .qq-run-badge-status {
            border-color: var(--qq-success-border);
            background: var(--qq-success-bg);
            color: var(--qq-success);
            text-transform: capitalize;
        }
        .st-key-back_to_runs button {
            min-width: 2rem;
            height: 2rem;
            min-height: 2rem;
            margin-top: 0;
            padding: 0;
            border-radius: var(--qq-radius);
            color: var(--qq-muted);
            background: var(--qq-neutral-bg);
            border: 1px solid var(--qq-border-soft);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            line-height: 1;
        }
        .qq-period-label {
            color: var(--qq-muted-strong);
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
            border-top: 1px solid var(--qq-border-soft);
            margin: 0.25rem 0 0.35rem 0;
        }
        .qq-page-description {
            color: var(--qq-muted);
            font-size: 0.84rem;
            line-height: 1.25;
            margin: 0 0 0.45rem 0;
        }
        .qq-card {
            border: 1px solid var(--qq-border);
            border-radius: var(--qq-radius);
            padding: 14px 16px;
            background: var(--qq-surface);
            margin-bottom: 10px;
        }
        .qq-card h4 { margin: 0 0 8px 0; font-size: 1.0rem; }
        .qq-muted { color: var(--qq-muted); font-size: 0.86rem; }
        .qq-good { color: var(--qq-success); font-weight: 650; }
        .qq-bad { color: var(--qq-danger); font-weight: 650; }
        .qq-neutral { color: var(--qq-muted-strong); font-weight: 650; }
        .qq-metric-label { color: var(--qq-muted); font-size: 0.78rem; margin-bottom: 3px; }
        .qq-metric-value { font-size: 1.15rem; font-weight: 700; }
        .qq-pill {
            display: inline-block;
            border-radius: var(--qq-radius);
            padding: 2px 8px;
            border: 1px solid var(--qq-border);
            margin-right: 4px;
            font-size: 0.78rem;
        }
        .qq-build-shell {
            display: grid;
            grid-template-columns: minmax(0, 1fr);
            gap: 0.65rem;
        }
        .qq-build-header {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(280px, 410px);
            gap: 0.85rem;
            align-items: start;
        }
        .qq-scope-card {
            border-radius: var(--qq-radius);
            background: #F8FAFC;
            padding: 0.8rem 0.9rem;
        }
        .qq-scope-title {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 0.62rem;
        }
        .qq-scope-title strong {
            font-size: 0.92rem;
            color: var(--qq-text);
        }
        .qq-rebuild-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.28rem;
            border-radius: var(--qq-radius);
            padding: 0.16rem 0.48rem;
            background: #FEF3F2;
            color: var(--qq-danger);
            font-size: 0.72rem;
            font-weight: 650;
            white-space: nowrap;
        }
        .qq-rebuild-badge span {
            display: inline-flex;
            width: 0.86rem;
            height: 0.86rem;
            align-items: center;
            justify-content: center;
            border: 1px solid var(--qq-danger);
            border-radius: 999px;
            font-size: 0.58rem;
            line-height: 1;
        }
        .qq-scope-grid {
            display: grid;
            grid-template-columns: minmax(92px, 0.72fr) minmax(0, 1.5fr);
            gap: 0.65rem 1rem;
            align-items: start;
        }
        .qq-scope-column {
            display: grid;
            gap: 0.55rem;
            min-width: 0;
        }
        .qq-scope-item {
            min-width: 0;
        }
        .qq-scope-item span {
            display: block;
            color: var(--qq-muted);
            font-size: 0.72rem;
            line-height: 1.1;
            margin-bottom: 0.15rem;
        }
        .qq-scope-item b {
            display: block;
            color: var(--qq-text);
            font-size: 0.82rem;
            font-weight: 400;
            line-height: 1.2;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .st-key-build_metrics [data-testid="stMetric"] {
            min-height: 0;
        }
        .st-key-build_metrics [data-testid="stMetricLabel"] p {
            color: var(--qq-muted);
            font-size: 0.68rem;
            line-height: 1;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .st-key-build_metrics [data-testid="stMetricValue"] {
            font-size: 2.5rem;
            line-height: 0.95;
        }
        .qq-progress-card {
            border: 1px solid var(--qq-border);
            border-radius: var(--qq-radius);
            background: var(--qq-surface);
            padding: 0.8rem 0.9rem;
            margin-bottom: 0.65rem;
        }
        .qq-progress-top {
            display: flex;
            justify-content: space-between;
            gap: 0.75rem;
            align-items: center;
            margin-bottom: 0.55rem;
        }
        .qq-progress-track {
            height: 0.55rem;
            border-radius: var(--qq-radius);
            background: var(--qq-neutral-bg);
            overflow: hidden;
            border: 1px solid var(--qq-border-soft);
        }
        .qq-progress-fill {
            height: 100%;
            background: var(--qq-primary);
            width: 0%;
        }
        .qq-phase-summary {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.36rem 1.45rem;
            margin-top: 0.7rem;
        }
        .qq-phase-row {
            display: grid;
            grid-template-columns: minmax(92px, 1fr) auto auto;
            gap: 0.5rem;
            align-items: baseline;
            min-width: 0;
            font-size: 0.76rem;
            line-height: 1.2;
        }
        .qq-phase-name {
            color: var(--qq-muted-strong);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .qq-phase-progress {
            color: var(--qq-text);
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }
        .qq-phase-time {
            color: var(--qq-muted);
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }
        .qq-build-board {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.85rem;
            align-items: start;
        }
        .qq-build-list {
            border: 1px solid var(--qq-border);
            border-radius: var(--qq-radius);
            background: var(--qq-surface);
            padding: 0.65rem;
        }
        .qq-build-list h4 {
            margin: 0 0 0.55rem 0;
            font-size: 0.95rem;
        }
        .qq-build-scroll {
            max-height: 590px;
            overflow-y: auto;
            padding-right: 0.15rem;
        }
        .qq-file-card {
            border: 1px solid var(--qq-border-soft);
            border-radius: var(--qq-radius);
            background: var(--qq-surface);
            padding: 0.68rem 0.72rem;
            margin-bottom: 0.55rem;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .qq-file-card-header {
            display: flex;
            justify-content: space-between;
            gap: 0.65rem;
            align-items: flex-start;
            margin-bottom: 0.42rem;
        }
        .qq-file-card-header strong {
            font-size: 0.98rem;
            line-height: 1.05;
        }
        .qq-file-card-subtitle {
            color: var(--qq-muted);
            font-size: 0.74rem;
            margin-top: 0.18rem;
        }
        .qq-card-progress-value {
            color: var(--qq-text);
            font-size: 1.08rem;
            font-weight: 700;
            line-height: 1;
            text-align: right;
        }
        .qq-card-status {
            border: 1px solid var(--qq-border);
            border-radius: var(--qq-radius);
            padding: 0.1rem 0.45rem;
            font-size: 0.72rem;
            color: var(--qq-muted-strong);
            background: var(--qq-surface);
            white-space: nowrap;
        }
        .qq-file-progress {
            height: 0.42rem;
            border-radius: var(--qq-radius);
            background: var(--qq-neutral-bg);
            overflow: hidden;
            margin: 0.15rem 0 0.62rem 0;
        }
        .qq-file-progress-fill {
            height: 100%;
            background: var(--qq-primary);
            width: 0%;
        }
        .qq-step-list {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.35rem;
        }
        .qq-step-row {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 0.16rem 0.4rem;
            align-items: center;
            border: 1px solid var(--qq-border-soft);
            border-radius: var(--qq-radius);
            background: var(--qq-surface-soft);
            padding: 0.34rem 0.42rem;
            font-size: 0.72rem;
            line-height: 1.15;
        }
        .qq-step-row span {
            color: var(--qq-muted-strong);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .qq-step-row b {
            color: var(--qq-muted);
            font-weight: 500;
            white-space: nowrap;
        }
        .qq-step-row.qq-step-done b {
            color: var(--qq-success);
        }
        .qq-step-row.qq-step-running b {
            color: var(--qq-primary);
        }
        @media (max-width: 1100px) {
            .qq-build-header,
            .qq-build-board {
                grid-template-columns: 1fr;
            }
        }
        div[class*="st-key-chart_component_"] {
            border: 1px solid var(--qq-border);
            border-radius: var(--qq-radius);
            background: var(--qq-surface);
            overflow: hidden;
            padding: 0 !important;
        }
        div[class*="st-key-chart_toolbar_"] {
            border: 0 !important;
            border-bottom: 1px solid var(--qq-border) !important;
            border-radius: 0 !important;
            padding: 0.2rem 0.45rem 0.25rem !important;
            margin: 0 0 0.35rem 0 !important;
            background: var(--qq-surface);
        }
        div[class*="st-key-chart_toolbar_"] [data-testid="stHorizontalBlock"] {
            gap: 0.4rem;
        }
        div[class*="st-key-chart_toolbar_"] [data-testid="stSelectbox"],
        div[class*="st-key-chart_toolbar_"] [data-testid="stText"] {
            margin-bottom: 0 !important;
        }
        div[class*="st-key-chart_toolbar_"] [data-baseweb="select"] > div {
            background-color: var(--qq-surface) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def app_dataframe(data, **kwargs):
    kwargs.setdefault("row_height", DEFAULT_TABLE_ROW_HEIGHT)
    return st.dataframe(data, **kwargs)


def display_name(value: str) -> str:
    tokens = re.split(r"[_\-\s]+", value.strip())
    labels = []
    for token in tokens:
        if not token:
            continue
        lower = token.lower()
        labels.append(DISPLAY_TOKEN_OVERRIDES.get(lower, token.upper() if re.fullmatch(r"\d+[a-zA-Z]+", token) else token.title()))
    return " ".join(labels)


def select_sidebar_page(page_key: str) -> None:
    st.session_state["sidebar_page"] = page_key
    if not page_key.startswith("strategy:"):
        st.session_state.pop("active_run_dir", None)


def render_sidebar() -> str:
    strategies = available_strategies()
    default_page = f"strategy:{strategies[0]}" if strategies else "market_data:build_data"
    selected_page = st.session_state.setdefault("sidebar_page", default_page)
    collapsed = bool(st.session_state.setdefault("sidebar_collapsed", False))

    if collapsed:
        st.sidebar.markdown('<div class="qq-sidebar-state-collapsed"></div>', unsafe_allow_html=True)
    if st.sidebar.button(
        "Expand sidebar" if collapsed else "Collapse sidebar",
        key="sidebar_toggle",
        type="secondary",
        width="stretch",
    ):
        st.session_state["sidebar_collapsed"] = not collapsed
        st.rerun()

    st.sidebar.markdown('<div class="qq-sidebar-group">Strategies</div>', unsafe_allow_html=True)
    for strategy_name in strategies:
        page_key = f"strategy:{strategy_name}"
        if st.sidebar.button(
            display_name(strategy_name),
            key=f"sidebar_{page_key}",
            type="secondary",
            disabled=selected_page == page_key,
            width="stretch",
        ):
            select_sidebar_page(page_key)
            st.rerun()

    st.sidebar.markdown('<div class="qq-sidebar-group">Market Data</div>', unsafe_allow_html=True)
    build_data_key = "market_data:build_data"
    if st.sidebar.button(
        "Build Data",
        key=f"sidebar_{build_data_key}",
        type="secondary",
        disabled=selected_page == build_data_key,
        width="stretch",
    ):
        select_sidebar_page(build_data_key)
        st.rerun()

    return st.session_state["sidebar_page"]


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


def chart_processed_config(data: dict[str, Any]) -> DataProviderConfig:
    config = data.get("metadata", {}).get("config", {})
    return DataProviderConfig(
        raw_root=Path(config.get("data_root") or DEFAULT_DATA_ROOT),
        processed_root=Path(config.get("processed_data_root") or DEFAULT_MARKET_DATA_ROOT),
        exchange_timezone=str(config.get("exchange_timezone") or CHART_EXCHANGE_TIME_ZONE),
    )


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
def load_extended_chart_day_bars(
    data_root_value: str,
    exchange_timezone: str,
    session_date_value: str,
    ticker: str,
) -> pl.DataFrame:
    # Visualization data is intentionally separate from strategy execution windows.
    # The chart always renders exchange-time extended hours from raw UTC source bars.
    session = date.fromisoformat(session_date_value)
    source = chart_minute_file_path(Path(data_root_value), session)
    if not source.exists():
        return pl.DataFrame()
    frame = (
        pl.scan_csv(source)
        .filter(pl.col("ticker") == ticker)
        .select("ticker", "volume", "open", "close", "high", "low", "window_start", "transactions")
        .collect()
    )
    if frame.is_empty():
        return frame
    return (
        add_chart_exchange_time_columns(frame, exchange_timezone)
        .filter(
            (pl.col("minute_of_day") >= CHART_EXTENDED_START_MINUTE)
            & (pl.col("minute_of_day") < CHART_EXTENDED_END_MINUTE)
        )
        .sort(["ticker", "bar_time_market"])
        .with_columns(pl.lit(session.isoformat()).alias("session_date"))
        .pipe(add_standard_indicators)
    )


def load_extended_chart_bars(
    data_root_value: str,
    exchange_timezone: str,
    session_dates: tuple[str, ...],
    ticker: str,
    timeframe: str,
) -> pl.DataFrame:
    minute_frames = [
        frame
        for session_date_value in session_dates
        if not (frame := load_extended_chart_day_bars(data_root_value, exchange_timezone, session_date_value, ticker)).is_empty()
    ]

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
        "processed_data_root": str(DEFAULT_MARKET_DATA_ROOT),
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


def processed_sessions(processed_root: Path, start: date, end: date, timeframe: str = "1m") -> list[str]:
    provider = MarketDataProvider(DataProviderConfig(processed_root=processed_root))
    available = set(provider.available_dates(timeframe))
    return [session for session in date_strings_between(start.isoformat(), end.isoformat()) if session in available]


def render_run_header(config: dict, status: str = "Draft", summary: dict | None = None) -> None:
    params = config.get("strategy_params", {})
    sessions = available_sessions(Path(config["data_root"]), date.fromisoformat(config["start_date"]), date.fromisoformat(config["end_date"]))
    processed_ready = len(
        processed_sessions(
            Path(config.get("processed_data_root") or DEFAULT_MARKET_DATA_ROOT),
            date.fromisoformat(config["start_date"]),
            date.fromisoformat(config["end_date"]),
        )
    )
    summary = summary or {}
    st.markdown(
        f"""
        <div class="qq-card">
          <h4>{config.get("run_name", "Untitled run")}</h4>
          <div class="qq-muted">{config.get("strategy_name")} | {status} | {config.get("start_date")} to {config.get("end_date")}</div>
          <div style="margin-top:8px;">
            <span class="qq-pill">sessions {len(sessions)}</span>
            <span class="qq-pill">processed {processed_ready}/{len(sessions)}</span>
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
    app_dataframe(pl.DataFrame(sessions), width="stretch", hide_index=True)
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
            color=alt.Color("direction:N", scale=alt.Scale(domain=["profit", "loss"], range=["#067647", "#B42318"])),
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
            color=alt.Color("series:N", scale=alt.Scale(range=["#1E3A5F", "#B7791F"])),
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


def exchange_session_timestamp(session_date_value: str, minute_of_day: int) -> int:
    session = date.fromisoformat(session_date_value)
    hour, minute = divmod(minute_of_day, 60)
    exchange_dt = datetime(session.year, session.month, session.day, hour, minute, tzinfo=ZoneInfo(CHART_EXCHANGE_TIME_ZONE))
    return int(exchange_dt.astimezone(timezone.utc).timestamp())


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


def chart_session_dates_from_rows(rows: list[dict]) -> list[str]:
    session_dates: set[str] = set()
    for row in rows:
        parsed = row_exchange_datetime(row)
        if parsed:
            session_dates.add(parsed.date().isoformat())
    return sorted(session_dates)


def chart_step_minutes(rows: list[dict]) -> int:
    deltas = []
    for session_date_value in chart_session_dates_from_rows(rows):
        minutes = sorted(
            {
                minute
                for row in rows
                if (parsed := row_exchange_datetime(row))
                and parsed.date().isoformat() == session_date_value
                and (minute := minute_of_day_from_row(row)) is not None
            }
        )
        deltas.extend(b - a for a, b in zip(minutes, minutes[1:]) if 0 < b - a <= 15)
    if not deltas:
        return 1
    deltas.sort()
    median = deltas[len(deltas) // 2]
    return 5 if median >= 4 else 1


def complete_candle_timeline(rows: list[dict], candles: list[dict]) -> list[dict]:
    candles_by_time = {int(candle["time"]): candle for candle in candles}
    step = chart_step_minutes(rows)
    timeline_times = set(candles_by_time)
    for session_date_value in chart_session_dates_from_rows(rows):
        minute = CHART_EXTENDED_START_MINUTE
        while minute <= CHART_EXTENDED_END_MINUTE:
            timeline_times.add(exchange_session_timestamp(session_date_value, minute))
            minute += step
    return [candles_by_time.get(timestamp, {"time": timestamp}) for timestamp in sorted(timeline_times)]


def complete_points_timeline(points: list[dict], timeline_times: list[int]) -> list[dict]:
    points_by_time = {int(point["time"]): point for point in points}
    return [points_by_time.get(timestamp, {"time": timestamp}) for timestamp in timeline_times]


def extended_session_regions(rows: list[dict]) -> list[dict]:
    regions = []
    for session_date_value in chart_session_dates_from_rows(rows):
        regions.append(
            {
                "start": exchange_session_timestamp(session_date_value, CHART_EXTENDED_START_MINUTE),
                "end": exchange_session_timestamp(session_date_value, CHART_REGULAR_START_MINUTE),
                "color": "rgba(183, 121, 31, 0.10)",
            }
        )
        regions.append(
            {
                "start": exchange_session_timestamp(session_date_value, CHART_REGULAR_END_MINUTE),
                "end": exchange_session_timestamp(session_date_value, CHART_EXTENDED_END_MINUTE),
                "color": "rgba(30, 58, 95, 0.08)",
            }
        )
    return regions


def hex_to_rgba(hex_color: str, opacity: float) -> str:
    color = hex_color.lstrip("#")
    if len(color) != 6:
        return f"rgba(30, 58, 95, {opacity:.2f})"
    red = int(color[0:2], 16)
    green = int(color[2:4], 16)
    blue = int(color[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {opacity:.2f})"


def default_chart_indicator_settings() -> dict:
    return {
        indicator: {
            "color": DEFAULT_INDICATOR_COLORS.get(indicator, "#1E3A5F"),
            "opacity": DEFAULT_INDICATOR_OPACITIES.get(indicator, 0.72),
            "lineWidth": DEFAULT_INDICATOR_WIDTHS.get(indicator, 1),
        }
        for indicator in CHART_INDICATORS
    }


def chart_key_fragment(value: str) -> str:
    fragment = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value)).strip("_")
    return fragment or "chart"


def render_chart_toolbar_buttons(component_key: str) -> None:
    component_selector_json = json.dumps(f".st-key-{component_key}")
    html = """
    <style>
    html, body {{
        margin: 0;
        padding: 0;
        background: transparent;
        overflow: hidden;
    }}
    #qq-chart-toolbar-buttons {{
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 4px;
        height: 34px;
    }}
    .qq-chart-tool-button {{
        width: 36px;
        height: 32px;
        border: 0;
        border-radius: 4px;
        background: #FFFFFF;
        color: #111827;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        line-height: 1;
    }}
    .qq-chart-tool-button:hover {{
        background: #F9FAFB;
    }}
    .qq-chart-toolbar-divider {{
        width: 1px;
        height: 20px;
        background: #E5E7EB;
        margin: 0 8px 0 6px;
    }}
    </style>
    <div id="qq-chart-toolbar-buttons">
        <button class="qq-chart-tool-button" data-action="fit-first-day" title="Fit first day" aria-label="Fit first day">
            <svg viewBox="0 0 24 24" width="21" height="21" aria-hidden="true">
                <path d="M7 2h2v2h6V2h2v2h3v18H4V4h3V2zm11 8H6v10h12V10zM6 8h12V6H6v2zm2 4h3v3H8v-3z" fill="currentColor"></path>
            </svg>
        </button>
        <button class="qq-chart-tool-button" data-action="fit-recent" title="Reset zoom to latest hour" aria-label="Reset zoom to latest hour">
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
                <path d="M12 5V2L7 6l5 4V7c3.31 0 6 2.69 6 6 0 1.3-.42 2.5-1.12 3.48l1.46 1.46A7.94 7.94 0 0 0 20 13c0-4.42-3.58-8-8-8zm-6 6a5.98 5.98 0 0 1 1.12-3.48L5.66 6.06A7.94 7.94 0 0 0 4 11c0 4.42 3.58 8 8 8v3l5-4-5-4v3c-3.31 0-6-2.69-6-6z" fill="currentColor"></path>
            </svg>
        </button>
        <span class="qq-chart-toolbar-divider"></span>
        <button id="qq-fullscreen-toolbar-button" class="qq-chart-tool-button" title="Fullscreen" aria-label="Toggle fullscreen">
            <svg viewBox="0 0 24 24" width="23" height="23" aria-hidden="true">
                <path d="M8 3H3v5h2V5h3V3zm8 0v2h3v3h2V3h-5zM5 16H3v5h5v-2H5v-3zm14 3h-3v2h5v-5h-2v3z" fill="currentColor"></path>
            </svg>
        </button>
    </div>
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
                background: #FFFFFF !important;
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
                background: #FFFFFF !important;
            }}
            .qq-streamlit-chart-fullscreen div[class*="st-key-chart_toolbar_"] iframe {{
                width: 158px !important;
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

    function dispatchChartAction(action) {{
        if (!parentDocument) return;
        parentDocument.dispatchEvent(new CustomEvent("qq-chart-action", {{
            detail: {{ componentSelector, action }}
        }}));
    }}

    document.querySelectorAll("[data-action]").forEach(actionButton => {{
        actionButton.addEventListener("click", () => dispatchChartAction(actionButton.dataset.action));
    }});
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
    columns = st.columns([0.9, 1.15, 8.25, 1.05], gap="small", vertical_alignment="center")
    ticker = selected_ticker
    with columns[0]:
        if tickers:
            ticker = st.selectbox("Ticker", tickers, index=tickers.index(selected_ticker) if selected_ticker in tickers else 0, label_visibility="collapsed")
        else:
            st.text(selected_ticker or "")
    with columns[1]:
        timeframe = st.segmented_control("Timeframe", options, default=options[0], key=timeframe_key, label_visibility="collapsed")
    with columns[3]:
        render_chart_toolbar_buttons(component_key)
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
        color = options.get("color", DEFAULT_INDICATOR_COLORS.get(column, "#1E3A5F"))
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
                    "color": "#067647" if is_buy else "#B42318",
                    "shape": "arrowUp" if is_buy else "arrowDown",
                    "text": f"{side} {row.get('quantity', '')} @ {money(row.get('fill_price'))}",
                }
            )

    candles = complete_candle_timeline(rows, candles)
    timeline_times = [int(candle["time"]) for candle in candles]
    volumes = complete_points_timeline(volumes, timeline_times)
    for series in overlay_series + oscillator_series:
        series["data"] = complete_points_timeline(series.get("data") or [], timeline_times)

    return {
        "candles": candles,
        "volumes": volumes,
        "overlays": overlay_series,
        "oscillators": oscillator_series,
        "markers": markers,
        "sessionRegions": extended_session_regions(rows),
    }


def render_lightweight_candle_chart(payload: dict, height: int = 720, component_key: str | None = None) -> None:
    chart_id = f"tv-chart-{abs(hash(json.dumps(payload, sort_keys=True))) % 10_000_000}"
    payload_json = json.dumps(payload)
    candle_settings_json = json.dumps(DEFAULT_CANDLE_CHART_SETTINGS)
    component_selector_json = json.dumps(f".st-key-{component_key}" if component_key else "")
    pane_gap = 6 if payload.get("oscillators") else 0
    oscillator_ratio = 0.375
    price_height = int((height - pane_gap) / (1 + oscillator_ratio)) if payload.get("oscillators") else height
    oscillator_height = int(price_height * oscillator_ratio) if payload.get("oscillators") else 0
    total_height = price_height + oscillator_height + pane_gap
    bottom_padding = 10
    outer_height = total_height + bottom_padding
    html = f"""
    <style>
    #{chart_id} {{
        background: #FFFFFF;
    }}
    </style>
    <div id="{chart_id}" style="height:{outer_height}px;width:100%;display:flex;flex-direction:column;gap:0;position:relative;">
        <div id="{chart_id}-price" style="height:{price_height}px;width:100%;position:relative;"></div>
        <div id="{chart_id}-splitter" style="height:{pane_gap}px;width:100%;display:{'flex' if oscillator_height else 'none'};align-items:center;justify-content:center;cursor:row-resize;user-select:none;touch-action:none;">
            <div style="width:100%;height:0;border-top:1px dotted #98A2B3;"></div>
        </div>
        <div id="{chart_id}-osc" style="height:{oscillator_height}px;width:100%;position:relative;display:{'block' if oscillator_height else 'none'};"></div>
    </div>
    <script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js"></script>
    <script>
    const payload = {payload_json};
    const candleSettings = {candle_settings_json};
    const container = document.getElementById("{chart_id}");
    const priceContainer = document.getElementById("{chart_id}-price");
    const splitter = document.getElementById("{chart_id}-splitter");
    const oscillatorContainer = document.getElementById("{chart_id}-osc");
    const hasOscillators = !!(payload.oscillators && payload.oscillators.length);
    const componentSelector = {component_selector_json};
    let parentDocument = null;
    try {{
        parentDocument = window.parent && window.parent.document ? window.parent.document : null;
    }} catch (error) {{
        parentDocument = null;
    }}
    const rightScaleWidth = 62;
    const indicatorLabelWidth = 62;
    const timeAxisHeight = 28;
    const fitScaleMargins = {{ top: 0.1, bottom: 0.1 }};
    const normalPriceHeight = {price_height};
    const normalOscillatorHeight = {oscillator_height};
    const paneGap = {pane_gap};
    const chartBottomPadding = {bottom_padding};
    let paneRatio = {oscillator_ratio};
    const chartWidth = () => Math.max(260, container.clientWidth - indicatorLabelWidth);
    const exchangeTimeZone = "{CHART_EXCHANGE_TIME_ZONE}";
    const allTimes = (payload.candles || []).map(bar => Number(bar.time)).filter(time => Number.isFinite(time));
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
    const exchangeDateFormatter = new Intl.DateTimeFormat("en-CA", {{
        timeZone: exchangeTimeZone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit"
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
            background: {{ type: "solid", color: "#FFFFFF" }},
            textColor: "#111827",
            fontSize: 12
        }},
        localization: {{
            timeFormatter: formatDateTime
        }},
        grid: {{
            vertLines: {{ color: "#F3F4F6" }},
            horzLines: {{ color: "#F3F4F6" }}
        }},
        crosshair: {{
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {{
                visible: true,
                labelVisible: true,
                color: "rgba(17,24,39,0.36)",
                width: 1,
                labelBackgroundColor: "#111827"
            }},
            horzLine: {{ color: "rgba(17,24,39,0.26)", width: 1, style: 2 }}
        }},
        rightPriceScale: {{
            borderColor: "#E5E7EB",
            minimumWidth: rightScaleWidth,
            scaleMargins: {{ top: 0.08, bottom: 0.22 }}
        }},
        timeScale: {{
            borderColor: "#E5E7EB",
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
            borderColor: "#E5E7EB",
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
            borderColor: "#E5E7EB",
            minimumWidth: rightScaleWidth,
            scaleMargins: {{ top: 0.12, bottom: 0.12 }}
        }}
    }}) : null;

    function createSessionLayer(parent, reserveTimeAxis) {{
        if (!parent) return null;
        const layer = document.createElement("div");
        layer.style.position = "absolute";
        layer.style.top = "0";
        layer.style.left = "0";
        layer.style.right = `${{indicatorLabelWidth + rightScaleWidth}}px`;
        layer.style.bottom = reserveTimeAxis ? `${{timeAxisHeight}}px` : "0";
        layer.style.pointerEvents = "none";
        layer.style.zIndex = "1";
        parent.appendChild(layer);
        return layer;
    }}

    const priceSessionLayer = createSessionLayer(priceContainer, !hasOscillators);
    const oscillatorSessionLayer = oscillatorChart ? createSessionLayer(oscillatorContainer, true) : null;

    function drawSessionLayer(layer, chartInstance) {{
        if (!layer || !chartInstance) return;
        layer.replaceChildren();
        const width = Math.max(0, layer.clientWidth);
        (payload.sessionRegions || []).forEach(region => {{
            const start = chartInstance.timeScale().timeToCoordinate(region.start);
            const end = chartInstance.timeScale().timeToCoordinate(region.end);
            if (start === null || end === null) return;
            const left = Math.max(0, Math.min(start, end));
            const right = Math.min(width, Math.max(start, end));
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
        shell.style.font = "11px Inter, sans-serif";
        shell.style.color = "#111827";
        shell.style.maxWidth = "220px";
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.textContent = `v ${{indicatorCount}} indicators`;
        toggle.style.display = "none";
        toggle.style.border = "0";
        toggle.style.background = "rgba(255,255,255,0.82)";
        toggle.style.backdropFilter = "blur(2px)";
        toggle.style.borderRadius = "4px";
        toggle.style.padding = "3px 6px";
        toggle.style.font = "11px Inter, sans-serif";
        toggle.style.color = "#111827";
        toggle.style.cursor = "pointer";
        const legendNode = document.createElement("div");
        legendNode.style.display = "flex";
        legendNode.style.flexDirection = "column";
        legendNode.style.alignItems = "stretch";
        legendNode.style.gap = "3px";
        legendNode.style.background = "rgba(255,255,255,0.82)";
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
        collapse.style.color = "#475467";
        collapse.style.cursor = "pointer";
        collapse.style.font = "14px Inter, sans-serif";
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
            button.style.background = "rgba(255,255,255,0.92)";
            button.style.color = "#475467";
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
        panel.style.border = "1px solid #E5E7EB";
        panel.style.borderRadius = "5px";
        panel.style.padding = "6px";
        panel.style.boxShadow = "0 8px 24px rgba(17,24,39,0.12)";
        panel.style.color = "#111827";
        panel.style.font = "11px Inter, sans-serif";
        panel.style.minWidth = "210px";
        panel.addEventListener("click", event => event.stopPropagation());
        panel._legendActions = actions;
        const colorInput = document.createElement("input");
        colorInput.type = "color";
        colorInput.value = indicator.legendColor || "#1E3A5F";
        const hexInput = document.createElement("input");
        hexInput.type = "text";
        hexInput.value = colorInput.value.toUpperCase();
        hexInput.spellcheck = false;
        hexInput.style.width = "82px";
        hexInput.style.font = "11px Inter, sans-serif";
        hexInput.style.border = "1px solid #E5E7EB";
        hexInput.style.borderRadius = "3px";
        hexInput.style.padding = "2px 4px";
        const opacityInput = document.createElement("input");
        opacityInput.type = "range";
        opacityInput.min = "0.15";
        opacityInput.max = "1";
        opacityInput.step = "0.05";
        opacityInput.value = indicator.opacity || 0.72;
        const opacityValue = document.createElement("span");
        opacityValue.style.font = "11px Inter, sans-serif";
        opacityValue.textContent = Number(opacityInput.value).toFixed(2);
        const widthInput = document.createElement("input");
        widthInput.type = "range";
        widthInput.min = "1";
        widthInput.max = "4";
        widthInput.step = "1";
        widthInput.value = indicator.lineWidth || 1;
        const widthValue = document.createElement("span");
        widthValue.style.font = "11px Inter, sans-serif";
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
            label.style.color = "#FFFFFF";
            label.style.borderRadius = "0";
            label.style.padding = "2px 5px";
            label.style.font = "12px Inter, sans-serif";
            label.style.lineHeight = "15px";
            label.style.boxShadow = "0 1px 2px rgba(17,24,39,0.20)";
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
            lastValueVisible: false,
            autoscaleInfoProvider: () => null
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

    function synchronizeOscillatorRange() {{
        if (oscillatorChart) {{
            const range = chart.timeScale().getVisibleLogicalRange();
            if (range) oscillatorChart.timeScale().setVisibleLogicalRange(range);
        }}
        requestAnimationFrame(drawSessionRegions);
    }}

    function applyLogicalRange(from, to) {{
        if (!allTimes.length) return;
        const range = {{
            from: Math.max(0, from),
            to: Math.max(0, to)
        }};
        chart.timeScale().setVisibleLogicalRange(range);
        if (oscillatorChart) oscillatorChart.timeScale().setVisibleLogicalRange(range);
        fitVisibleYScales();
        requestAnimationFrame(drawSessionRegions);
    }}

    function fitVisibleYScales() {{
        chart.priceScale("right").applyOptions({{ autoScale: true, scaleMargins: fitScaleMargins }});
        if (oscillatorChart) {{
            oscillatorChart.priceScale("right").applyOptions({{ autoScale: true, scaleMargins: fitScaleMargins }});
        }}
    }}

    function applyRightOffset(offset) {{
        const normalized = Math.max(0, offset);
        chart.timeScale().applyOptions({{ rightOffset: normalized }});
        if (oscillatorChart) oscillatorChart.timeScale().applyOptions({{ rightOffset: normalized }});
    }}

    function fitRecentBars() {{
        if (!allTimes.length) return;
        const lastIndex = allTimes.length - 1;
        const latestTime = allTimes[lastIndex];
        const firstRecentIndex = allTimes.findIndex(time => time >= latestTime - 3600);
        const leftIndex = firstRecentIndex >= 0 ? firstRecentIndex : Math.max(0, lastIndex - 60);
        const leftBars = Math.max(1, lastIndex - leftIndex + 1);
        applyRightOffset(leftBars);
        applyLogicalRange(leftIndex, lastIndex + leftBars);
    }}

    function fitFirstDay() {{
        if (!allTimes.length) return;
        const firstDate = exchangeDateFormatter.format(new Date(Number(allTimes[0]) * 1000));
        let firstIndex = null;
        let lastIndex = null;
        allTimes.forEach((time, index) => {{
            if (exchangeDateFormatter.format(new Date(Number(time) * 1000)) === firstDate) {{
                if (firstIndex === null) firstIndex = index;
                lastIndex = index;
            }}
        }});
        if (firstIndex !== null && lastIndex !== null) {{
            applyRightOffset(Number(candleSettings.rightOffset || 0));
            applyLogicalRange(firstIndex, lastIndex + Number(candleSettings.rightOffset || 0));
        }}
    }}

    function alignTimeScales() {{
        synchronizeOscillatorRange();
    }}

    fitRecentBars();

    if (parentDocument && componentSelector) {{
        parentDocument.addEventListener("qq-chart-action", event => {{
            if (!event.detail || event.detail.componentSelector !== componentSelector) return;
            if (event.detail.action === "fit-recent") fitRecentBars();
            if (event.detail.action === "fit-first-day") fitFirstDay();
        }});
    }}
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
        const available = isChartExpanded() ? Math.max(360, window.innerHeight - 4) : {total_height};
        if (!hasOscillators) {{
            return {{ price: available, oscillator: 0, total: available }};
        }}
        const usable = Math.max(260, available - paneGap);
        const oscillator = Math.max(90, Math.min(Math.floor(usable * 0.62), Math.floor((usable * paneRatio) / (1 + paneRatio))));
        const price = Math.max(170, usable - oscillator);
        return {{ price, oscillator, total: price + oscillator + paneGap }};
    }}

    function resizeCharts() {{
        const heights = chartHeights();
        container.style.height = `${{heights.total + chartBottomPadding}}px`;
        priceContainer.style.height = `${{heights.price}}px`;
        if (oscillatorContainer) oscillatorContainer.style.height = `${{heights.oscillator}}px`;
        const width = chartWidth();
        chart.applyOptions({{ width, height: heights.price }});
        if (oscillatorChart) oscillatorChart.applyOptions({{ width, height: heights.oscillator }});
        alignTimeScales();
        requestAnimationFrame(drawSessionRegions);
    }}

    function setPaneRatioFromPointer(clientY) {{
        if (!hasOscillators || !oscillatorChart) return;
        const rect = container.getBoundingClientRect();
        const usable = Math.max(260, rect.height - paneGap);
        const splitY = Math.max(170, Math.min(clientY - rect.top, usable - 90));
        const oscillator = usable - splitY;
        paneRatio = oscillator / Math.max(1, splitY);
        resizeCharts();
    }}

    if (splitter && hasOscillators) {{
        splitter.addEventListener("pointerdown", event => {{
            event.preventDefault();
            splitter.setPointerCapture(event.pointerId);
            setPaneRatioFromPointer(event.clientY);
        }});
        splitter.addEventListener("pointermove", event => {{
            if (splitter.hasPointerCapture(event.pointerId)) {{
                event.preventDefault();
                setPaneRatioFromPointer(event.clientY);
            }}
        }});
        splitter.addEventListener("pointerup", event => {{
            if (splitter.hasPointerCapture(event.pointerId)) splitter.releasePointerCapture(event.pointerId);
        }});
        splitter.addEventListener("pointercancel", event => {{
            if (splitter.hasPointerCapture(event.pointerId)) splitter.releasePointerCapture(event.pointerId);
        }});
    }}

    window.addEventListener("resize", resizeCharts);
    const resizeObserver = new ResizeObserver(entries => {{
        if (!entries.length) return;
        resizeCharts();
    }});
    resizeObserver.observe(container);
    </script>
    """
    components.html(html, height=outer_height + 4, scrolling=False)


def candle_chart(
    bars: pl.DataFrame,
    orders: pl.DataFrame,
    indicators: dict[str, dict],
    height: int = 900,
    component_key: str | None = None,
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
    render_lightweight_candle_chart(payload, height=height, component_key=component_key)


def bars_for(data: dict, period: str, ticker: str, timeframe: str) -> pl.DataFrame:
    provider = MarketDataProvider(chart_processed_config(data))
    session_dates = tuple(chart_session_dates(data, period))
    if session_dates and ticker:
        start = date.fromisoformat(session_dates[0])
        end = date.fromisoformat(session_dates[-1])
        provider_bars = provider.load_bars(
            start_date=start,
            end_date=end,
            timeframe=timeframe,
            tickers=[ticker],
            feature_groups=list(FEATURE_GROUPS),
        )
        if not provider_bars.is_empty():
            if period != "Whole Run" and "session_date" in provider_bars.columns:
                provider_bars = provider_bars.filter(pl.col("session_date").is_in(session_dates))
            return provider_bars

    data_root, exchange_timezone = chart_source_config(data)
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
            candle_chart(bars, orders, indicators, component_key=component_key)


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
        app_dataframe(setup.sort("rank") if not setup.is_empty() else setup, width="stretch")
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
    app_dataframe(snapshot, width="stretch")


def render_rejections(data: dict, period: str) -> None:
    rejections = filter_df(data["rejections"], period)
    if rejections.is_empty():
        st.info("No rejections for this period.")
        return
    if "reject_reason" in rejections.columns:
        counts = rejections.group_by("reject_reason").len().sort("len", descending=True)
        st.bar_chart(counts.to_pandas(), x="reject_reason", y="len")
    app_dataframe(rejections.tail(500), width="stretch")


def render_positions(data: dict, period: str) -> None:
    positions = filter_df(data["positions"], period)
    if positions.is_empty():
        st.info("No position snapshots for this period.")
        return
    app_dataframe(positions, width="stretch")


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
        candle_chart(bars, orders, indicators, component_key=component_key)


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
    app_dataframe(pl.DataFrame(rows), width="stretch", hide_index=True)


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
            ("Processed data root", config.get("processed_data_root", "")),
            ("Output root", config.get("output_root", "")),
            ("Market UTC offset", config.get("market_utc_offset_hours", "")),
            ("Slippage bps", config.get("slippage_bps", "")),
            ("Save chart bars", config.get("save_symbol_bars", "")),
        ]
    )

    st.subheader("Strategy Parameters")
    param_rows = [{"Parameter": humanize_key(key), "Value": str(value)} for key, value in sorted(params.items())]
    app_dataframe(pl.DataFrame(param_rows), width="stretch", hide_index=True)


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

    with st.container(key="run_header", horizontal_alignment="left", vertical_alignment="center"):
        info_cols = st.columns([0.25, 0.45, 0.3], gap="small", vertical_alignment="center")
        with info_cols[0]:
            st.markdown(f'<div class="qq-run-header-title">{escape(str(run_name))}</div>', unsafe_allow_html=True)
        with info_cols[1]:
            strategy_name = metadata.get("strategy_name", config.get("strategy_name", ""))
            badge_cols = st.columns([0.45, 0.55], gap="small", vertical_alignment="center")
            with badge_cols[0]:
                st.markdown(
                    (
                        '<div class="qq-run-header-line">'
                        '<div class="qq-run-badges">'
                        f'<span class="qq-run-badge">{escape(str(strategy_name))}</span>'
                        f'<span class="qq-run-badge qq-run-badge-status">{escape(str(status))}</span>'
                        f'<span class="qq-run-badge">{escape(date_range)}</span>'
                        "</div>"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
            with badge_cols[1]:
                if run_details_dialog is not None:
                    if st.button("See more details", key=f"run_details_{run_dir.name}", type="tertiary", width="content"):
                        run_details_dialog(str(run_dir))
                else:
                    with st.expander("See more details"):
                        render_run_details_content(run_dir)
        with info_cols[2]:
            metric_cols = st.columns(3, gap="small", vertical_alignment="center")
            metric_cols[0].metric("Return", pct(summary.get("return_pct", 0.0)))
            metric_cols[1].metric("P/L", money(summary.get("total_pnl", 0.0)))
            metric_cols[2].metric("Trades", summary.get("trade_count", 0))


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
            config["processed_data_root"] = st.text_input(
                "Processed data root",
                value=str(config.get("processed_data_root") or DEFAULT_MARKET_DATA_ROOT),
            )
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
            if st.button("Update Run Parameters", width="content"):
                update_run_parameters_dialog(key)
        else:
            with st.expander("Update Run Parameters"):
                render_new_run_update_form(key)
    with cols[1]:
        if st.button("Start Backtest", type="primary", width="content"):
            sessions = available_sessions(Path(config["data_root"]), date.fromisoformat(config["start_date"]), date.fromisoformat(config["end_date"]))
            missing_processed = MarketDataProvider(
                DataProviderConfig(processed_root=Path(config.get("processed_data_root") or DEFAULT_MARKET_DATA_ROOT))
            ).missing_dates(date.fromisoformat(config["start_date"]), date.fromisoformat(config["end_date"]), "1m")
            if not sessions:
                st.error("No local data files found for this run range.")
            elif missing_processed:
                st.error(
                    "Processed provider data is missing. Build data first for: "
                    + ", ".join(missing_processed[:8])
                    + ("..." if len(missing_processed) > 8 else "")
                )
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
    table_state = app_dataframe(
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


def format_bytes(value: int | float | None) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def format_duration(value: int | float | None) -> str:
    seconds = float(value or 0)
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {int(remainder)}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m"


def build_scope_defaults() -> dict[str, Any]:
    raw_root = Path(st.session_state.get("build_raw_root", DEFAULT_DATA_ROOT))
    processed_root = Path(st.session_state.get("build_processed_root", DEFAULT_MARKET_DATA_ROOT))
    first_raw, last_raw, raw_count = discover_raw_bounds(raw_root)
    start_value = st.session_state.get("build_start_date", first_raw or date(2024, 5, 1))
    end_value = st.session_state.get("build_end_date", last_raw or date(2024, 5, 31))
    st.session_state.setdefault("build_raw_root", str(raw_root))
    st.session_state.setdefault("build_processed_root", str(processed_root))
    st.session_state.setdefault("build_start_date", start_value)
    st.session_state.setdefault("build_end_date", end_value)
    return {
        "raw_root": raw_root,
        "processed_root": processed_root,
        "start_date": start_value,
        "end_date": end_value,
        "raw_count": raw_count,
    }


def render_scope_card(scope: dict[str, Any]) -> None:
    st.markdown(
        f"""
        <div class="qq-scope-card">
            <div class="qq-scope-title"><strong>Data Scope</strong><span class="qq-rebuild-badge"><span>!</span>Force rebuild</span></div>
            <div class="qq-scope-grid">
                <div class="qq-scope-column">
                    <div class="qq-scope-item"><span>Start</span><b>{scope["start_date"]}</b></div>
                    <div class="qq-scope-item"><span>End</span><b>{scope["end_date"]}</b></div>
                </div>
                <div class="qq-scope-column">
                    <div class="qq-scope-item"><span>Raw root</span><b title="{escape(str(scope["raw_root"]))}">{escape(str(scope["raw_root"]))}</b></div>
                    <div class="qq-scope-item"><span>Processed root</span><b title="{escape(str(scope["processed_root"]))}">{escape(str(scope["processed_root"]))}</b></div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_scope_dialog() -> None:
    if not hasattr(st, "dialog"):
        with st.expander("Update Data Scope", expanded=True):
            raw_root = st.text_input("Raw data root", value=str(st.session_state.get("build_raw_root", DEFAULT_DATA_ROOT)))
            processed_root = st.text_input("Processed data root", value=str(st.session_state.get("build_processed_root", DEFAULT_MARKET_DATA_ROOT)))
            start_date = st.date_input("Start", value=st.session_state.get("build_start_date", date(2024, 5, 1)))
            end_date = st.date_input("End", value=st.session_state.get("build_end_date", date(2024, 5, 31)))
            if st.button("Apply", type="primary", width="stretch"):
                st.session_state["build_raw_root"] = raw_root
                st.session_state["build_processed_root"] = processed_root
                st.session_state["build_start_date"] = start_date
                st.session_state["build_end_date"] = end_date
                st.rerun()
        return

    @st.dialog("Update Data Scope")
    def update_scope_dialog() -> None:
        raw_root = st.text_input("Raw data root", value=str(st.session_state.get("build_raw_root", DEFAULT_DATA_ROOT)))
        processed_root = st.text_input("Processed data root", value=str(st.session_state.get("build_processed_root", DEFAULT_MARKET_DATA_ROOT)))
        start_date = st.date_input("Start", value=st.session_state.get("build_start_date", date(2024, 5, 1)))
        end_date = st.date_input("End", value=st.session_state.get("build_end_date", date(2024, 5, 31)))
        if st.button("Apply", type="primary", width="stretch"):
            st.session_state["build_raw_root"] = raw_root
            st.session_state["build_processed_root"] = processed_root
            st.session_state["build_start_date"] = start_date
            st.session_state["build_end_date"] = end_date
            st.rerun()

    update_scope_dialog()


def render_build_metrics(metrics: dict[str, str]) -> None:
    with st.container(key="build_metrics", border=False):
        items = list(metrics.items())
        for start in range(0, len(items), 6):
            columns = st.columns(6, gap="small", border=False)
            for column, (label, value) in zip(columns, items[start : start + 6]):
                column.metric(label, value, border=False)


def status_class(status: str) -> str:
    if status in {"complete", "ready"}:
        return "qq-good"
    if status in {"missing", "missing_raw", "failed"}:
        return "qq-bad"
    return "qq-neutral"


def session_step_plan() -> dict[str, int]:
    session_timeframes = [timeframe for timeframe in TIMEFRAMES if timeframe != "1mo"]
    return {
        "raw": 1,
        "normalize": 1,
        "bars": len(session_timeframes),
        "features": len(session_timeframes) * len(FEATURE_GROUPS),
        "labels": len(session_timeframes) * len(SUPERVISION_GROUPS),
        "complete": 1,
    }


def step_status(done: int, total: int) -> str:
    if done <= 0:
        return "waiting"
    if done >= total:
        return "done"
    return "running"


def step_status_class(status: str) -> str:
    if status == "done":
        return "qq-step-done"
    if status == "running":
        return "qq-step-running"
    return "qq-step-waiting"


def step_row(label: str, done: int, total: int) -> str:
    status = step_status(done, total)
    value = "done" if total == 1 and done >= 1 else ("-" if done <= 0 else f"{done}/{total}")
    return (
        f'<div class="qq-step-row {step_status_class(status)}">'
        f'<span>{escape(label)}</span>'
        f"<b>{escape(value)}</b>"
        "</div>"
    )


def file_card_html(row: dict) -> str:
    status = str(row.get("status") or "queued")
    session_date = str(row.get("session_date") or "-")
    current_step = str(row.get("phase") or status).replace("_", " ")
    steps = row.get("steps", {})
    total_units = int(row.get("step_total") or 1)
    completed_units = int(row.get("step_done") or 0)
    progress_pct = min(100.0, max(0.0, (completed_units / total_units) * 100.0))
    duration = format_duration(row.get("duration_sec", 0))
    status_text = status.replace("_", " ")
    step_rows = "".join(
        [
            step_row("Raw load", int(steps.get("raw", 0)), 1),
            step_row("Normalize 1m", int(steps.get("normalize", 0)), 1),
            step_row("Bars", int(steps.get("bars", 0)), session_step_plan()["bars"]),
            step_row("Features", int(steps.get("features", 0)), session_step_plan()["features"]),
            step_row("Labels", int(steps.get("labels", 0)), session_step_plan()["labels"]),
            step_row("Complete", int(steps.get("complete", 0)), 1),
        ]
    )
    return (
        '<div class="qq-file-card">'
        '<div class="qq-file-card-header">'
        "<div>"
        f"<strong>{escape(session_date)}</strong>"
        f'<div class="qq-file-card-subtitle">Current: {escape(current_step)} | {escape(duration)}</div>'
        "</div>"
        "<div>"
        f'<div class="qq-card-progress-value">{progress_pct:.0f}%</div>'
        f'<span class="qq-card-status {status_class(status)}">{escape(status_text)}</span>'
        "</div>"
        "</div>"
        f'<div class="qq-file-progress"><div class="qq-file-progress-fill" style="width:{progress_pct:.1f}%"></div></div>'
        f'<div class="qq-step-list">{step_rows}</div>'
        "</div>"
    )


def render_file_container(title: str, rows: list[dict]) -> str:
    cards = "".join(file_card_html(row) for row in rows) or '<div class="qq-muted">No files yet.</div>'
    return f'<div class="qq-build-list"><h4>{escape(title)}</h4><div class="qq-build-scroll">{cards}</div></div>'


def render_manifest_card(manifest: dict, processed_root: Path) -> None:
    artifacts = manifest.get("artifacts", {})
    rows = [
        ("Processed root", str(processed_root)),
        ("Artifact records", f"{len(artifacts):,}"),
        ("Schema version", str(manifest.get("schema_version", "-"))),
        ("Feature version", str(manifest.get("feature_version", "-"))),
        ("Supervision version", str(manifest.get("supervision_version", "-"))),
        ("Updated at", str(manifest.get("updated_at") or "-")),
    ]
    fields = "".join(
        f'<div class="qq-scope-item"><span>{escape(label)}</span><b title="{escape(value)}">{escape(value)}</b></div>'
        for label, value in rows
    )
    st.markdown(
        f"""
        <div class="qq-scope-card">
            <div class="qq-scope-title"><strong>Manifest Summary</strong><span class="qq-card-status">metadata</span></div>
            <div class="qq-scope-grid">{fields}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_monitor_metrics(plan_rows: list[dict], events: list[dict], manifest: dict, started_at: datetime | None) -> dict[str, str]:
    expected = [row for row in plan_rows if row.get("expected_market_session")]
    buildable = [row for row in expected if row.get("exists")]
    missing = [row for row in expected if not row.get("exists")]
    artifact_events = [event for event in events if event.get("event") == "artifact_complete"]
    work_total = max((int(event.get("work_total") or 0) for event in events), default=0)
    work_completed = max((int(event.get("work_completed") or 0) for event in events), default=0)
    elapsed = (datetime.now() - started_at).total_seconds() if started_at else 0
    slowest = max((event for event in events if event.get("duration_sec") is not None), key=lambda item: float(item.get("duration_sec") or 0), default={})
    return {
        "Raw": f"{len(buildable):,}",
        "Exp": f"{len(expected):,}",
        "Miss": f"{len(missing):,}",
        "Closed": f"{len(plan_rows) - len(expected):,}",
        "Art": f"{len(artifact_events):,}",
        "Manif": f"{len(manifest.get('artifacts', {})):,}",
        "Rows": f"{sum(int(event.get('rows_out') or 0) for event in artifact_events):,}",
        "Written": format_bytes(sum(int(event.get("size_bytes") or 0) for event in artifact_events)),
        "Prog": f"{work_completed:,}/{work_total:,}" if work_total else "-",
        "Elapsed": format_duration(elapsed),
        "Slowest": str(slowest.get("phase") or "-").replace("_", " "),
        "Status": str(events[-1].get("status") if events else "ready"),
    }


def build_phase_summary(plan_rows: list[dict], events: list[dict]) -> str:
    buildable = [row for row in plan_rows if row.get("expected_market_session") and row.get("exists")]
    buildable_count = len(buildable)
    touched_months = {str(row["session_date"])[:7] for row in buildable}
    month_count = len(touched_months)
    session_timeframes = [timeframe for timeframe in TIMEFRAMES if timeframe != "1mo"]
    intraday_timeframes = [timeframe for timeframe in session_timeframes if timeframe not in {"1d"}]
    contexts_with_monthly = buildable_count * len(session_timeframes) + month_count
    phase_totals = {
        "scan_source": 1,
        "raw_load": buildable_count,
        "canonicalize_1m": buildable_count,
        "aggregate": buildable_count * len(intraday_timeframes),
        "aggregate_daily": buildable_count,
        "bars_write": contexts_with_monthly,
        "feature_compute": contexts_with_monthly,
        "feature_write": contexts_with_monthly * len(FEATURE_GROUPS),
        "supervision_bar": contexts_with_monthly,
        "supervision_method": contexts_with_monthly,
        "supervision_scanner": contexts_with_monthly,
        "monthly_aggregate": month_count,
        "run": 1,
    }
    phase_labels = [
        ("scan_source", "Scan"),
        ("raw_load", "Raw load"),
        ("canonicalize_1m", "Normalize"),
        ("aggregate", "Intraday bars"),
        ("aggregate_daily", "Daily bars"),
        ("bars_write", "Write bars"),
        ("feature_compute", "Feature calc"),
        ("feature_write", "Write features"),
        ("supervision_bar", "Bar labels"),
        ("supervision_method", "Method labels"),
        ("supervision_scanner", "Scanner labels"),
        ("monthly_aggregate", "Monthly bars"),
        ("run", "Total run"),
    ]
    completed: dict[str, int] = {phase: 0 for phase in phase_totals}
    elapsed: dict[str, float] = {phase: 0.0 for phase in phase_totals}
    for event in events:
        phase = str(event.get("phase") or "")
        if phase not in phase_totals:
            continue
        completed[phase] += 1
        elapsed[phase] += float(event.get("duration_sec") or 0.0)

    rows = []
    for phase, label in phase_labels:
        total = phase_totals[phase]
        done = min(completed[phase], total)
        progress = f"{done}/{total}" if total else "-"
        rows.append(
            '<div class="qq-phase-row">'
            f'<span class="qq-phase-name">{escape(label)}</span>'
            f'<span class="qq-phase-progress">{escape(progress)}</span>'
            f'<span class="qq-phase-time">{escape(format_duration(elapsed[phase]))}</span>'
            "</div>"
        )
    return '<div class="qq-phase-summary">' + "".join(rows) + "</div>"


def build_session_cards(plan_rows: list[dict], events: list[dict]) -> tuple[list[dict], list[dict]]:
    session_rows: dict[str, dict] = {}
    planned_steps = session_step_plan()
    planned_total = sum(planned_steps.values())
    for row in plan_rows:
        session_rows[row["session_date"]] = {
            "session_date": row["session_date"],
            "status": "queued" if row.get("exists") and row.get("expected_market_session") else row.get("status", "closed"),
            "phase": row.get("status", "queued"),
            "duration_sec": 0.0,
            "steps": {key: 0 for key in planned_steps},
            "step_done": 0,
            "step_total": planned_total,
        }
    completed_dates: set[str] = set()
    for event in events:
        session_date = event.get("session_date")
        if not session_date or session_date not in session_rows:
            continue
        row = session_rows[session_date]
        row["phase"] = event.get("phase", row.get("phase"))
        if event.get("event") == "session_started":
            row["status"] = "running"
        elif event.get("event") not in {"session_complete", "session_skipped"}:
            row["status"] = "running"
        row["duration_sec"] = float(row.get("duration_sec") or 0) + float(event.get("duration_sec") or 0)
        steps = row["steps"]
        phase = event.get("phase")
        group = event.get("group")
        if phase == "raw_load":
            steps["raw"] = 1
        elif phase == "canonicalize_1m":
            steps["normalize"] = 1
        elif event.get("event") == "artifact_complete" and group == "bars":
            steps["bars"] = min(planned_steps["bars"], int(steps.get("bars", 0)) + 1)
        elif event.get("event") == "artifact_complete" and str(group or "").startswith("features_"):
            steps["features"] = min(planned_steps["features"], int(steps.get("features", 0)) + 1)
        elif event.get("event") == "artifact_complete" and str(group or "").startswith("supervision_"):
            steps["labels"] = min(planned_steps["labels"], int(steps.get("labels", 0)) + 1)
        if event.get("event") in {"session_complete", "session_skipped"}:
            row["status"] = event.get("status", "complete")
            if event.get("event") == "session_complete":
                steps["complete"] = 1
            completed_dates.add(session_date)
        row["step_done"] = sum(int(value) for value in steps.values())
    completed = [session_rows[session_date] for session_date in sorted(completed_dates, reverse=True)]
    active = [
        row
        for row in session_rows.values()
        if row["session_date"] not in completed_dates and row.get("status") in {"queued", "running", "complete"}
    ]
    return active[:5], completed


def render_data_provider_page() -> None:
    scope = build_scope_defaults()
    raw_root = Path(scope["raw_root"])
    processed_root = Path(scope["processed_root"])
    start_date = scope["start_date"]
    end_date = scope["end_date"]
    if start_date > end_date:
        st.error("Start date must be on or before end date.")
        return
    source_rows = [asdict(row) for row in scan_market_source(raw_root, start_date, end_date)]
    manifest = read_manifest(processed_root)

    header_cols = st.columns([1.55, 1.0], gap="medium", vertical_alignment="top")
    with header_cols[0]:
        st.title("Build Data")
        st.markdown(
            '<div class="qq-page-description">Rebuild the canonical market-data store with every supported timeframe, feature group, and supervision label.</div>',
            unsafe_allow_html=True,
        )
        action_cols = st.columns([0.42, 0.24, 0.34], gap="small", vertical_alignment="center")
        start_clicked = action_cols[0].button("Rebuild selected range", type="primary", width="stretch")
        if action_cols[1].button("Edit scope", width="stretch"):
            render_scope_dialog()
    with header_cols[1]:
        render_scope_card(scope)

    events: list[dict] = st.session_state.setdefault("build_progress_events", [])
    started_at = st.session_state.get("build_started_at")
    render_build_metrics(build_monitor_metrics(source_rows, events, manifest, started_at))
    missing_sessions = [row["session_date"] for row in source_rows if row.get("expected_market_session") and not row.get("exists")]
    if missing_sessions:
        st.warning(
            "Missing raw files for expected market sessions: "
            + ", ".join(missing_sessions[:10])
            + ("..." if len(missing_sessions) > 10 else "")
        )

    progress_slot = st.empty()
    board_slot = st.empty()
    detail_slot = st.empty()

    def render_progress_board(current_events: list[dict]) -> None:
        current_manifest = read_manifest(processed_root)
        metrics = build_monitor_metrics(source_rows, current_events, current_manifest, st.session_state.get("build_started_at"))
        work_total = max((int(event.get("work_total") or 0) for event in current_events), default=0)
        work_completed = max((int(event.get("work_completed") or 0) for event in current_events), default=0)
        ratio = min(1.0, work_completed / work_total) if work_total else 0.0
        current = current_events[-1] if current_events else {}
        phase_summary = build_phase_summary(source_rows, current_events)
        with progress_slot.container():
            st.markdown(
                f"""
                <div class="qq-progress-card">
                    <div class="qq-progress-top">
                        <div><strong>{escape(str(current.get("session_date") or "Ready"))}</strong><div class="qq-muted">{escape(str(current.get("phase") or "Waiting to start").replace("_", " "))}</div></div>
                        <div class="qq-neutral">{escape(metrics["Prog"])}</div>
                    </div>
                    <div class="qq-progress-track"><div class="qq-progress-fill" style="width:{ratio * 100:.1f}%"></div></div>
                    {phase_summary}
                </div>
                """,
                unsafe_allow_html=True,
            )
        active_cards, completed_cards = build_session_cards(source_rows, current_events)
        with board_slot.container():
            st.markdown(
                '<div class="qq-build-board">'
                + render_file_container("Active Queue", active_cards)
                + render_file_container("Completed Files", completed_cards)
                + "</div>",
                unsafe_allow_html=True,
            )
        artifact_events = [event for event in current_events if event.get("event") == "artifact_complete"]
        phase_events = [event for event in current_events if event.get("duration_sec") is not None]
        ready_rows = MarketDataProvider(DataProviderConfig(raw_root=raw_root, processed_root=processed_root)).status_rows(start_date, end_date, list(TIMEFRAMES))
        with detail_slot.container():
            tabs = st.tabs(["Build Timings", "Artifacts", "Plan", "Processed Store", "Manifest"])
            with tabs[0]:
                st.caption("Measured build steps such as raw loading, timestamp normalization, timeframe aggregation, feature writes, supervision writes, and monthly aggregation.")
                app_dataframe(pl.DataFrame(phase_events[-500:]), width="stretch", hide_index=True)
            with tabs[1]:
                app_dataframe(pl.DataFrame(artifact_events[-500:]), width="stretch", hide_index=True)
            with tabs[2]:
                app_dataframe(pl.DataFrame(source_rows), width="stretch", hide_index=True)
            with tabs[3]:
                app_dataframe(pl.DataFrame(ready_rows), width="stretch", hide_index=True)
            with tabs[4]:
                render_manifest_card(current_manifest, processed_root)

    render_progress_board(events)

    if start_clicked:
        st.session_state["build_progress_events"] = []
        st.session_state["build_started_at"] = datetime.now()
        progress_rows: list[dict] = []

        def on_progress(event: dict) -> None:
            progress_rows.append(event)
            st.session_state["build_progress_events"] = progress_rows
            render_progress_board(progress_rows)

        request = BuildRequest(
            raw_root=raw_root,
            processed_root=processed_root,
            start_date=start_date,
            end_date=end_date,
            timeframes=list(TIMEFRAMES),
            feature_groups=list(FEATURE_GROUPS),
            supervision_groups=list(SUPERVISION_GROUPS),
            rebuild_mode="force_rebuild",
            tickers=None,
        )
        try:
            result = build_market_data(request, progress_callback=on_progress)
            st.session_state["build_progress_events"] = progress_rows
            st.success(f"Build complete. Processed root: {result['processed_root']}")
        except Exception as exc:
            st.error(str(exc))
            with st.expander("Build error details"):
                st.code(traceback.format_exc())


def strategy_workspace(strategy_name: str) -> None:
    output_root = DEFAULT_OUTPUT_ROOT
    active_run = st.session_state.get("active_run_dir")
    if active_run:
        active_run_path = Path(active_run)
        render_selected_run_header(active_run_path)
        render_run_dashboard(active_run_path, show_header=False, show_back_button=True)
        return
    st.title(display_name(strategy_name))
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
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    install_css()
    page_key = render_sidebar()
    if page_key == "market_data:build_data":
        render_data_provider_page()
        return
    if page_key.startswith("strategy:"):
        strategy_workspace(page_key.removeprefix("strategy:"))
        return
    st.error("Unknown sidebar page.")


if __name__ == "__main__":
    main()
