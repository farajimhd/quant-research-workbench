from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.data_provider.config import FEATURE_VERSION, SCHEMA_VERSION, SUPERVISION_VERSION
from src.data_provider.features import FEATURE_COLUMNS
from src.data_provider.supervision import METHOD_BAR_WINDOWS


CATALOG_VERSION = 2
PRESENTATION_OVERRIDE_FILE = "catalog_presentation_overrides.json"

BAR_COLUMNS = [
    "bar_id",
    "ticker",
    "timeframe",
    "bar_time_utc",
    "bar_time_market",
    "session_date",
    "minute_of_day",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "transactions",
]

BAR_SUPERVISION_COLUMNS = [
    "bar_id",
    "ticker",
    "timeframe",
    "bar_time_utc",
    "bar_time_market",
    "session_date",
    "horizon",
    "horizon_bars",
    "horizon_minutes",
    "future_bar_count",
    "valid_future_window",
    "fwd_close_return",
    "fwd_high_return",
    "fwd_low_return",
    "fwd_mfe",
    "fwd_mae",
    "fwd_mfe_to_mae_ratio",
    "time_to_mfe_bars",
    "time_to_mae_bars",
    "time_to_mfe_minutes",
    "time_to_mae_minutes",
    "mfe_before_mae",
    "oracle_best_exit_bar_id",
    "oracle_best_exit_time_utc",
    "oracle_best_exit_price",
    "oracle_best_exit_return",
    "oracle_long_entry_signal",
    "oracle_long_entry_confidence",
    "oracle_long_exit_signal",
    "oracle_long_exit_confidence",
    "path_efficiency",
    "green_bar_ratio",
    "fwd_volume_sum",
    "fwd_dollar_volume_sum",
    "fwd_transactions_sum",
    "fwd_max_volume",
    "fwd_max_dollar_volume",
    "fwd_max_relative_volume20",
    "fwd_max_relative_dollar_volume20",
    "fwd_max_volume_z20",
    "fwd_volume_expansion_ratio",
    "fwd_dollar_volume_expansion_ratio",
    "fwd_liquidity_confirmed",
    "fwd_first_volume_shock_bar_id",
    "fwd_first_volume_shock_time_utc",
    "fwd_first_volume_shock_time_market",
    "fwd_minutes_to_volume_shock",
    "fwd_volume_shock_before_mfe",
    "fwd_return_at_volume_shock",
    "fwd_drawdown_before_volume_shock",
    "fwd_estimated_capacity_dollars",
    "fwd_capacity_score",
    "fwd_price_outcome_quality",
    "fwd_liquidity_quality_score",
    "fwd_outcome_bucket",
]

METHOD_SUPERVISION_COLUMNS = [
    "bar_id",
    "ticker",
    "timeframe",
    "bar_time_utc",
    "bar_time_market",
    "session_date",
    "trade_method",
    "method_min_horizon_bars",
    "method_max_horizon_bars",
    "method_min_horizon_minutes",
    "method_max_horizon_minutes",
    "valid_future_window",
    "method_best_exit_bar_id",
    "method_best_exit_time_utc",
    "method_best_horizon_bars",
    "method_best_horizon_minutes",
    "method_best_price",
    "method_best_return",
    "method_mae_before_best",
    "method_mfe_mae_ratio",
    "method_path_efficiency",
    "method_entry_signal",
    "method_exit_signal",
    "method_confidence",
    "oracle_action",
    "current_price_shock",
    "current_volume_shock",
    "current_confirmed_price_volume_shock",
    "shock_confirmation_type",
    "shock_confirmation_delay_minutes",
    "shock_price_score",
    "shock_volume_score",
    "shock_score",
    "shock_drawdown_before_confirmation",
    "shock_return_after_confirmation",
    "shock_best_exit_after_confirmation_bar_id",
    "shock_best_exit_after_confirmation_time_utc",
]

SCANNER_SUPERVISION_COLUMNS = [
    "bar_id",
    "ticker",
    "timeframe",
    "bar_time_utc",
    "bar_time_market",
    "session_date",
    "trade_method",
    "universe_size",
    "oracle_rank",
    "oracle_percentile",
    "method_best_return",
    "method_mae_before_best",
    "method_best_horizon_minutes",
    "method_confidence",
    "oracle_action",
    "current_price_shock",
    "current_volume_shock",
    "current_confirmed_price_volume_shock",
    "shock_confirmation_type",
    "shock_confirmation_delay_minutes",
    "shock_score",
    "shock_return_after_confirmation",
    "shock_drawdown_before_confirmation",
    "is_top_1",
    "is_top_3",
    "is_top_5",
    "is_top_10",
    "is_top_1pct",
    "is_top_5pct",
]

KEY_COLUMNS = {"bar_id", "ticker", "timeframe", "bar_time_utc", "bar_time_market", "session_date", "session_month", "minute_of_day"}
INDICATOR_PREFIXES = ("sma", "ema", "tema", "macd", "rsi", "roc", "cci", "stoch", "atr", "bb_", "donchian", "keltner")
INDICATOR_COLUMNS = {"vwap", "obv", "mfi14", "cmf20"}
PRICE_OVERLAY_TERMS = ("sma", "ema", "tema", "vwap", "bb_", "donchian", "keltner", "hvn", "lvn", "price_proxy", "open", "high", "low")
OSCILLATOR_TERMS = ("macd", "rsi", "roc", "cci", "stoch", "z20", "relative_", "score", "ratio", "pct", "confidence", "percentile")
DEFAULT_VISIBLE_COLUMNS = {"vwap", "tema9", "tema20", "macd_line", "macd_signal", "macd_hist"}
DYNAMIC_COLORS = ["#1E3A5F", "#B7791F", "#067647", "#B42318", "#2563EB", "#7C3AED", "#0E7490", "#C2410C"]
TITLE_ACRONYMS = {
    "atr": "ATR",
    "bb": "BB",
    "bp": "bp",
    "bps": "bps",
    "cci": "CCI",
    "cmf": "CMF",
    "ema": "EMA",
    "fvg": "FVG",
    "fwd": "FWD",
    "hvn": "HVN",
    "id": "ID",
    "lvn": "LVN",
    "macd": "MACD",
    "mae": "MAE",
    "mfe": "MFE",
    "mfi": "MFI",
    "obv": "OBV",
    "orb": "ORB",
    "pct": "pct",
    "roc": "ROC",
    "rsi": "RSI",
    "sma": "SMA",
    "tema": "TEMA",
    "utc": "UTC",
    "vwap": "VWAP",
}
TITLE_LOWERCASE_WORDS = {"a", "an", "and", "as", "at", "before", "by", "for", "from", "in", "into", "of", "on", "or", "per", "the", "to", "vs", "with", "without"}
BAR_KNOWLEDGE = {
    "bar_id": {
        "short": "Stable provider bar identifier.",
        "detailed": "Bar ID uniquely identifies a provider bar across ticker, timeframe, and timestamp context.",
        "equation": "$$bar\\_id=f(Ticker, Timeframe, Time_t)$$",
        "variables": {"Time_t": "Provider bar timestamp"},
    },
    "ticker": {
        "short": "Security symbol for the bar.",
        "detailed": "Ticker identifies the listed instrument represented by this provider row.",
        "equation": "$$Ticker_t=Symbol$$",
        "variables": {"Symbol": "Security identifier in the processed artifact"},
    },
    "timeframe": {
        "short": "Provider timeframe for the bar.",
        "detailed": "Timeframe identifies the bar interval, such as 1m, 5m, 1h, or 1d.",
        "equation": "$$Timeframe \\in \\{1m,5m,15m,30m,1h,2h,4h,1d\\}$$",
        "variables": {"Timeframe": "Provider aggregation interval"},
    },
    "bar_time_utc": {
        "short": "Bar timestamp in UTC.",
        "detailed": "UTC bar time gives an exchange-independent timestamp for joining data and comparing markets.",
        "equation": "$$Time_{UTC}=convert(Time_{NY}, UTC)$$",
        "variables": {"Time_{NY}": "New York market timestamp"},
    },
    "bar_time_market": {
        "short": "Bar timestamp in New York market time.",
        "detailed": "Market bar time is the exchange-local timestamp used for intraday session logic and chart display.",
        "equation": "$$Time_{NY}=convert(Time_{UTC}, America/New\\_York)$$",
        "variables": {"Time_{UTC}": "UTC bar timestamp"},
    },
    "session_date": {
        "short": "New York trading session date.",
        "detailed": "Session date assigns each bar to its New York market session, including extended-hours bars that belong to that session.",
        "equation": "$$SessionDate=date(Time_{NY})$$",
        "variables": {"Time_{NY}": "New York market timestamp"},
    },
    "minute_of_day": {
        "short": "New York minute of day.",
        "detailed": "Minute of day is the New York hour multiplied by 60 plus the minute. The provider uses it for intraday bucketing, session alignment, and extended-hours shading.",
        "equation": "$$MinuteOfDay=60 \\cdot Hour_{NY}+Minute_{NY}$$",
        "variables": {"Hour_{NY}": "Hour in New York market time", "Minute_{NY}": "Minute in New York market time"},
    },
    "open": {
        "short": "Opening price of the bar.",
        "detailed": "Open is the first observed or aggregated trade price in the provider bar interval.",
        "equation": "$$Open_t=P_{first,t}$$",
        "variables": {"P_{first,t}": "First price in bar t"},
    },
    "high": {
        "short": "Highest price of the bar.",
        "detailed": "High is the maximum observed or aggregated trade price in the provider bar interval.",
        "equation": "$$High_t=max(P_t)$$",
        "variables": {"P_t": "Prices inside bar t"},
    },
    "low": {
        "short": "Lowest price of the bar.",
        "detailed": "Low is the minimum observed or aggregated trade price in the provider bar interval.",
        "equation": "$$Low_t=min(P_t)$$",
        "variables": {"P_t": "Prices inside bar t"},
    },
    "close": {
        "short": "Closing price of the bar.",
        "detailed": "Close is the final observed or aggregated trade price in the provider bar interval.",
        "equation": "$$Close_t=P_{last,t}$$",
        "variables": {"P_{last,t}": "Last price in bar t"},
    },
    "volume": {
        "short": "Share volume in the bar.",
        "detailed": "Volume is the total traded shares represented by the provider bar interval.",
        "equation": "$$Volume_t=\\sum_i Shares_{i,t}$$",
        "variables": {"Shares_{i,t}": "Trade size inside bar t"},
    },
    "transactions": {
        "short": "Transaction count in the bar.",
        "detailed": "Transactions counts the number of reported trades represented by the provider bar interval.",
        "equation": "$$Transactions_t=count(Trades_t)$$",
        "variables": {"Trades_t": "Trades inside bar t"},
    },
}


def provider_catalog(processed_root: Path | None = None) -> dict[str, Any]:
    catalog = base_provider_catalog()
    if processed_root is not None:
        apply_presentation_overrides(catalog, load_presentation_overrides(processed_root))
    return catalog


def base_provider_catalog() -> dict[str, Any]:
    columns = build_column_contracts()
    return {
        "catalogVersion": CATALOG_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "featureVersion": FEATURE_VERSION,
        "supervisionVersion": SUPERVISION_VERSION,
        "columns": columns,
        "supervisionMethods": build_method_contracts(),
        "scanners": build_scanner_contracts(),
        "presentationOptions": {
            "chartRoles": ["price_overlay", "oscillator", "histogram", "marker", "band", "table_only"],
            "panes": ["price", "macd", "oscillator", "new", "supervision"],
            "lineStyles": ["solid", "dashed", "dotted"],
            "markerShapes": ["circle", "arrowUp", "arrowDown", "square"],
            "markerPositions": ["aboveBar", "belowBar", "inBar"],
            "valueFormats": ["price", "percent", "number", "integer", "boolean", "datetime", "text"],
        },
    }


def build_column_contracts() -> list[dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for column in BAR_COLUMNS:
        add_column(entries, column, group="bars", artifact_group="bars")
    for feature_group, columns in FEATURE_COLUMNS.items():
        for column in columns:
            add_column(entries, column, group=feature_group, artifact_group=f"features_{feature_group}")
    for column in BAR_SUPERVISION_COLUMNS:
        add_column(entries, column, group="supervision_bar", artifact_group="supervision_bar")
    for column in METHOD_SUPERVISION_COLUMNS:
        add_column(entries, column, group="supervision_method", artifact_group="supervision_method")
    for column in SCANNER_SUPERVISION_COLUMNS:
        add_column(entries, column, group="supervision_scanner", artifact_group="supervision_scanner")
    return sorted(entries.values(), key=lambda item: (category_order(item["category"]), item["group"], item["title"], item["id"]))


def add_column(entries: dict[str, dict[str, Any]], column: str, *, group: str, artifact_group: str) -> None:
    if column in entries:
        if artifact_group not in entries[column]["artifactGroups"]:
            entries[column]["artifactGroups"].append(artifact_group)
        if group not in entries[column]["groups"]:
            entries[column]["groups"].append(group)
        return
    category = category_for_column(column, group)
    title = title_for_column(column)
    contract = {
        "id": column,
        "column": column,
        "title": title,
        "shortTitle": short_title_for_column(column, title),
        "category": category,
        "group": group,
        "groups": [group],
        "artifactGroups": [artifact_group],
        "dtype": dtype_for_column(column),
        "knowledge": knowledge_for_column(column, group, category, title),
        "semantics": semantics_for_column(column),
        "presentation": presentation_for_column(column, group, category),
    }
    if group.startswith("supervision_"):
        contract["leakage"] = leakage_block()
    entries[column] = contract


def build_method_contracts() -> list[dict[str, Any]]:
    descriptions = {
        "PRICE_VOLUME_SHOCK": {
            "title": "Price Volume Shock",
            "thesis": "A fast price displacement becomes more actionable when participation confirms it on the same bar or shortly after.",
            "summary": "Combines shock context, future price path, confirmation speed, and liquidity quality into a method-level entry label.",
            "equation": "$$Confidence = 0.45S_{shock} + 0.35Q_{path} + 0.12Q_{speed} + B_{confirm}$$",
        },
        "SCALP": {
            "title": "Scalp",
            "thesis": "Short-horizon continuation can be evaluated by the best favorable move versus adverse excursion over the next bars.",
            "summary": "Ranks immediate price-path quality across a compact future window.",
            "equation": "$$Quality = 0.45R_{best} + 0.35E_{path} + 0.20(1 - Risk_{mae})$$",
        },
        "MOMENTUM_SCALP": {
            "title": "Momentum Scalp",
            "thesis": "A slightly wider short-term window captures continuation after the initial impulse while still avoiding swing-trade horizons.",
            "summary": "Evaluates the same path-quality objective across a later short-horizon window.",
            "equation": "$$Signal = I(Q_{path} \\ge \\theta_q \\land R_{best} > \\theta_r)$$",
        },
    }
    methods = []
    for method, (min_bars, max_bars) in METHOD_BAR_WINDOWS.items():
        details = descriptions.get(method, {})
        methods.append(
            {
                "id": f"method.{method}",
                "method": method,
                "title": details.get("title", title_for_column(method)),
                "category": "supervision_method",
                "direction": "long",
                "validTimeframes": "all",
                "horizonBars": [min_bars, max_bars],
                "thesis": details.get("thesis", "Method-level supervision label for a future-looking trading thesis."),
                "requiredFeatures": ["close", "high", "low", "volume", "dollar_volume"],
                "outputColumns": [column for column in METHOD_SUPERVISION_COLUMNS if column not in KEY_COLUMNS],
                "labelLogic": {
                    "summary": details.get("summary", "Future-looking method label built from best return, drawdown, and confidence thresholds."),
                    "markdownEquation": details.get("equation", "$$Method_t = f(Path_{t+1:t+h}, Features_t)$$"),
                    "implementationRef": "src.data_provider.supervision._method_frame",
                },
                "knowledge": knowledge_block(
                    short=details.get("summary", "Method supervision label."),
                    detailed=details.get("thesis", "Future-looking method supervision for research and model training."),
                    theory="Method labels convert future path information into a research target. They should be evaluated offline and never treated as live signals without a delay-safe model.",
                    interpretation="Higher confidence means the future path better matched the method thesis over its horizon window.",
                    equation=details.get("equation", "$$Method_t = f(Path_{t+1:t+h}, Features_t)$$"),
                    variables={"Path": "Future high, low, close, and volume sequence", "h": "Method horizon in bars"},
                ),
                "leakage": leakage_block(),
                "presentation": {
                    "selectable": True,
                    "defaultVisible": False,
                    "chartRole": "marker",
                    "pane": "price",
                    "markerShape": "arrowUp",
                    "markerPosition": "belowBar",
                    "color": "#2563EB",
                    "valueFormat": "boolean",
                    "legend": True,
                },
            }
        )
    return methods


def build_scanner_contracts() -> list[dict[str, Any]]:
    return [
        {
            "id": "scanner.method_rank",
            "title": "Method Scanner Rank",
            "category": "supervision_scanner",
            "purpose": "Ranks tickers at the same timestamp for each method using method confidence.",
            "candidateConditions": ["valid_future_window", "method_confidence is available"],
            "rankingColumns": ["oracle_rank", "oracle_percentile", "method_confidence"],
            "confidenceColumns": ["method_confidence", "shock_score"],
            "outputColumns": [column for column in SCANNER_SUPERVISION_COLUMNS if column not in KEY_COLUMNS],
            "knowledge": knowledge_block(
                short="Cross-sectional ranking label for method candidates.",
                detailed="Scanner supervision ranks all available tickers at a timestamp for each method, making it useful for research on candidate selection and ranking quality.",
                theory="A scanner is a cross-sectional problem: at each timestamp, candidates compete against each other. Rank and percentile labels measure relative opportunity quality rather than absolute path quality.",
                interpretation="Lower oracle rank and higher oracle percentile indicate stronger candidates for that timestamp and method.",
                equation="$$Rank_{i,t,m} = rank_{desc}(Confidence_{i,t,m})$$\n\n$$Percentile_{i,t,m}=1-\\frac{Rank_{i,t,m}-1}{N_{t,m}-1}$$",
                variables={"i": "Ticker", "t": "Timestamp", "m": "Trade method", "N": "Candidate universe size"},
            ),
            "leakage": leakage_block(),
            "presentation": {
                "selectable": True,
                "defaultVisible": False,
                "chartRole": "marker",
                "pane": "price",
                "markerShape": "arrowUp",
                "markerPosition": "aboveBar",
                "color": "#7C3AED",
                "valueFormat": "integer",
                "legend": True,
            },
        }
    ]


def category_for_column(column: str, group: str) -> str:
    if group.startswith("supervision_"):
        return "label"
    if group == "bars" or column in KEY_COLUMNS:
        return "bar"
    if is_indicator_column(column):
        return "indicator"
    return "feature"


def is_indicator_column(column: str) -> bool:
    lower = column.lower()
    return lower in INDICATOR_COLUMNS or lower.startswith(INDICATOR_PREFIXES)


def dtype_for_column(column: str) -> str:
    lower = column.lower()
    if lower in {"macd_line", "macd_signal", "macd_hist"}:
        return "float"
    if lower.endswith("_utc") or lower.endswith("_market") or lower.endswith("_time"):
        return "datetime"
    if lower.endswith("_date"):
        return "date"
    if lower.startswith("is_") or lower.endswith("_signal") or lower.endswith("_confirmed") or lower in {"valid_future_window", "mfe_before_mae"} or "shock_before" in lower:
        return "bool"
    if lower in {"ticker", "timeframe", "horizon", "trade_method", "oracle_action", "fwd_outcome_bucket", "shock_confirmation_type"}:
        return "string"
    if lower.endswith("_bars") or lower.endswith("_minutes") or lower.endswith("_count") or lower.endswith("_rank") or lower.endswith("_size") or lower in {"minute_of_day", "transactions", "volume"}:
        return "int"
    return "float"


def semantics_for_column(column: str) -> dict[str, Any]:
    lower = column.lower()
    if dtype_for_column(column) == "bool":
        unit = "boolean"
    elif dtype_for_column(column) in {"datetime", "date", "string"}:
        unit = dtype_for_column(column)
    elif lower.endswith("_return") or lower.endswith("_pct") or "percentile" in lower:
        unit = "percent"
    elif lower.endswith("_minutes"):
        unit = "minutes"
    elif lower.endswith("_bars"):
        unit = "bars"
    elif "score" in lower or "confidence" in lower or "quality" in lower:
        unit = "score"
    elif "volume" in lower:
        unit = "shares" if "dollar" not in lower else "currency"
    elif any(term in lower for term in ("price", "open", "high", "low", "close", "vwap", "sma", "ema", "tema", "bb_", "donchian", "keltner")):
        unit = "price"
    else:
        unit = "number"
    direction = "higher_better" if any(term in lower for term in ("confidence", "score", "quality", "return", "percentile")) else "neutral"
    return {"unit": unit, "direction": direction, "nullable": column not in KEY_COLUMNS}


def presentation_for_column(column: str, group: str, category: str) -> dict[str, Any]:
    lower = column.lower()
    role = chart_role_for_column(column, group, category)
    pane = pane_for_role(column, role)
    presentation: dict[str, Any] = {
        "selectable": category in {"indicator", "feature", "label"},
        "defaultVisible": column in DEFAULT_VISIBLE_COLUMNS,
        "chartRole": role,
        "pane": pane,
        "groupKey": "macd" if lower.startswith("macd_") else None,
        "color": color_for_column(column),
        "lineStyle": "solid",
        "lineWidth": 2 if column in {"vwap", "tema9", "tema20"} else 1,
        "valueFormat": value_format_for_column(column),
        "precision": precision_for_column(column),
        "legend": role not in {"table_only"},
    }
    if role == "marker":
        if group == "supervision_scanner":
            presentation.update({"markerShape": "arrowUp", "markerPosition": "aboveBar", "color": "#7C3AED"})
        elif group == "supervision_method":
            presentation.update({"markerShape": "arrowUp", "markerPosition": "belowBar", "color": "#2563EB"})
        else:
            presentation.update({"markerShape": "circle", "markerPosition": "belowBar", "color": "#067647"})
    return {key: value for key, value in presentation.items() if value is not None}


def chart_role_for_column(column: str, group: str, category: str) -> str:
    lower = column.lower()
    if category == "bar" or column in KEY_COLUMNS:
        return "table_only"
    if group.startswith("supervision_"):
        return "marker" if lower in {"oracle_long_entry_signal", "method_entry_signal", "is_top_1", "is_top_3", "is_top_5"} else "table_only"
    if lower == "macd_hist":
        return "histogram"
    if any(term in lower for term in PRICE_OVERLAY_TERMS):
        return "price_overlay"
    if any(term in lower for term in OSCILLATOR_TERMS):
        return "oscillator"
    if dtype_for_column(column) == "bool":
        return "marker"
    return "oscillator"


def pane_for_role(column: str, role: str) -> str:
    if role in {"price_overlay", "band", "marker"}:
        return "price"
    if column.lower().startswith("macd_"):
        return "macd"
    if role in {"oscillator", "histogram"}:
        return "oscillator"
    return "price"


def color_for_column(column: str) -> str:
    special = {
        "vwap": "#5B21B6",
        "tema9": "#2563EB",
        "tema20": "#B7791F",
        "macd_line": "#1E3A5F",
        "macd_signal": "#B54708",
        "macd_hist": "inherit_candle_direction",
        "rsi14": "#7C3AED",
        "atr14": "#0E7490",
    }
    if column in special:
        return special[column]
    return DYNAMIC_COLORS[stable_index(column) % len(DYNAMIC_COLORS)]


def value_format_for_column(column: str) -> str:
    unit = semantics_for_column(column)["unit"]
    if unit == "boolean":
        return "boolean"
    if unit in {"datetime", "date"}:
        return "datetime"
    if unit == "percent":
        return "percent"
    if unit == "price":
        return "price"
    if dtype_for_column(column) == "int":
        return "integer"
    if unit == "string":
        return "text"
    return "number"


def precision_for_column(column: str) -> int:
    unit = semantics_for_column(column)["unit"]
    if unit == "percent":
        return 2
    if unit in {"score", "price"}:
        return 4
    return 2


def knowledge_for_column(column: str, group: str, category: str, title: str) -> dict[str, Any]:
    lower = column.lower()
    if category == "bar":
        return bar_knowledge_for_column(column, title)
    if lower == "vwap":
        return knowledge_block(
            short="Volume-weighted average price.",
            detailed="VWAP estimates the average execution price weighted by traded volume through the session or available window.",
            theory="VWAP anchors price to participation. Trading above VWAP often indicates demand is paying above the volume-weighted consensus, while trading below VWAP indicates weaker demand.",
            interpretation="Use VWAP as a price-location and mean-reversion/trend anchor. Breaks and reclaims can mark intraday regime changes.",
            equation="$$VWAP_t=\\frac{\\sum_{i=1}^{t}P_iV_i}{\\sum_{i=1}^{t}V_i}$$",
            variables={"P_i": "Typical or close price at bar i", "V_i": "Volume at bar i"},
        )
    if lower.startswith("sma"):
        window = trailing_number(lower, 20)
        return knowledge_block(
            short=f"Simple moving average over {window} bars.",
            detailed="SMA smooths price by averaging the last N closes with equal weight.",
            theory="Moving averages reduce noise and provide a lagging estimate of trend direction and dynamic support/resistance.",
            interpretation="Price above a rising SMA suggests trend strength; compression across SMAs suggests congestion.",
            equation=f"$$SMA_{{{window},t}}=\\frac{{1}}{{{window}}}\\sum_{{i=0}}^{{{window - 1}}}C_{{t-i}}$$",
            variables={"C": "Close price"},
        )
    if lower.startswith("ema"):
        window = trailing_number(lower, 20)
        return knowledge_block(
            short=f"Exponential moving average over {window} bars.",
            detailed="EMA smooths price while weighting recent bars more heavily than older bars.",
            theory="EMA reacts faster than SMA and is useful when the latest price action should carry more information.",
            interpretation="A fast EMA crossing above a slow EMA can indicate momentum expansion.",
            equation=f"$$EMA_t=\\alpha C_t+(1-\\alpha)EMA_{{t-1}},\\quad \\alpha=\\frac{{2}}{{{window}+1}}$$",
            variables={"C_t": "Close price at bar t"},
        )
    if lower.startswith("tema"):
        window = trailing_number(lower, 20)
        return knowledge_block(
            short=f"Triple exponential moving average over {window} bars.",
            detailed="TEMA combines three EMA layers to reduce lag while retaining smoothing.",
            theory="TEMA attempts to preserve trend responsiveness without giving up the noise reduction of moving averages.",
            interpretation="TEMA slope and price crosses can show faster momentum changes than SMA/EMA.",
            equation="$$TEMA_t=3EMA_1-3EMA_2+EMA_3$$",
            variables={"EMA_1": f"EMA(close, {window})", "EMA_2": f"EMA(EMA_1, {window})", "EMA_3": f"EMA(EMA_2, {window})"},
        )
    if lower.startswith("macd"):
        return knowledge_block(
            short="Momentum oscillator based on fast and slow EMAs.",
            detailed="MACD compares a fast EMA to a slow EMA, then smooths the difference with a signal line. The histogram measures the distance between the two.",
            theory="MACD is a trend-momentum tool. Expanding separation indicates accelerating momentum; contraction indicates weakening momentum.",
            interpretation="Line/signal crosses and histogram expansion can help identify momentum shifts, but can whipsaw in ranges.",
            equation="$$MACD_t=EMA_{12}(C_t)-EMA_{26}(C_t)$$\n\n$$Signal_t=EMA_9(MACD_t)$$\n\n$$Hist_t=MACD_t-Signal_t$$",
            variables={"C_t": "Close price at bar t"},
        )
    if lower.startswith("rsi"):
        window = trailing_number(lower, 14)
        return knowledge_block(
            short=f"Relative Strength Index over {window} bars.",
            detailed="RSI compares average gains and losses to measure momentum pressure on a bounded 0-100 scale.",
            theory="RSI captures persistence of directional closes. Extremes can indicate trend strength or exhaustion depending on context.",
            interpretation="High RSI can signal strong momentum in breakouts but overextension in ranges.",
            equation=f"$$RSI_t=100-\\frac{{100}}{{1+RS_t}},\\quad RS_t=\\frac{{AvgGain_{{{window}}}}}{{AvgLoss_{{{window}}}}}$$",
            variables={"AvgGain": "Average positive close-to-close change", "AvgLoss": "Average negative close-to-close change"},
        )
    if lower.startswith("atr") or lower == "true_range":
        return knowledge_block(
            short="Volatility measure based on true range.",
            detailed="ATR smooths true range to estimate recent realized volatility.",
            theory="True range accounts for gaps by considering high-low, high-prior-close, and low-prior-close movement.",
            interpretation="Use ATR for volatility normalization, stop sizing, and range expansion detection.",
            equation="$$TR_t=max(H_t-L_t, |H_t-C_{t-1}|, |L_t-C_{t-1}|)$$\n\n$$ATR_t=SMA(TR_t,14)$$",
            variables={"H": "High", "L": "Low", "C": "Close"},
        )
    if lower.startswith("bb_"):
        return knowledge_block(
            short="Bollinger Band statistic around a moving average.",
            detailed="Bollinger Bands place upper/lower volatility bands around a moving average using rolling standard deviation.",
            theory="Bands widen as volatility expands and tighten as volatility contracts.",
            interpretation="Band expansion can confirm volatility breakouts; band touches can show overextension in ranges.",
            equation="$$Upper_t=SMA_{20}(C_t)+2\\sigma_{20}(C_t)$$\n\n$$Lower_t=SMA_{20}(C_t)-2\\sigma_{20}(C_t)$$",
            variables={"C": "Close price", "\\sigma": "Rolling standard deviation"},
        )
    if lower in {"price_shock_score", "volume_shock_score", "price_volume_shock_score"} or "shock" in lower:
        return knowledge_block(
            short="Shock context feature or label.",
            detailed="Shock fields describe abnormal price displacement, abnormal participation, and whether price and volume confirmed each other in sequence.",
            theory="Strong price moves are more informative when participation confirms them. Sequence matters because price can lead volume or volume can lead price.",
            interpretation="Higher shock scores identify more unusual and better-confirmed dislocation events.",
            equation="$$ShockScore_t=w_pS^{price}_t+w_vS^{volume}_t+w_cI(confirmed_t)$$",
            variables={"S": "Normalized shock score", "I": "Indicator function", "w": "Component weight"},
        )
    if group.startswith("supervision_"):
        return knowledge_block(
            short=f"{title} future-looking supervision label.",
            detailed="Supervision labels use future bars to describe realized opportunity quality, path risk, confirmation, ranking, or oracle action.",
            theory="These fields create supervised-learning and research targets. They intentionally look forward and must not be used as live features.",
            interpretation="Use for offline diagnostics, training targets, and chart review with explicit leakage awareness.",
            equation="$$Label_t=f(Bars_{t+1:t+h}, Features_t)$$",
            variables={"h": "Future horizon in bars", "Features_t": "Information available at the source bar"},
        )
    return knowledge_block(
        short=f"{title} from the {group} group.",
        detailed=f"{title} is a provider-generated {category} column in the {group} artifact group.",
        theory=theory_for_group(group, category),
        interpretation="Interpret this field together with its units, timeframe, and related provider columns.",
        equation=f"$$\\text{{{title}}}_t=f(Inputs_t)$$",
        variables={"Inputs_t": "Provider inputs listed in the implementation reference"},
    )


def theory_for_group(group: str, category: str) -> str:
    if group == "session":
        return "Session features anchor intraday state to market structure such as the open, premarket range, and opening range."
    if group == "volume_liquidity":
        return "Volume and liquidity features measure participation, relative activity, and estimated trading capacity."
    if group == "price_action":
        return "Price-action features translate candle geometry and local breakouts into structured signals."
    if group == "market_structure":
        return "Market-structure features describe local swings, breaks of structure, and trend regime."
    if group == "order_blocks":
        return "Order-block features approximate supply and demand zones after displacement."
    if group == "fvg":
        return "Fair value gap features identify displacement gaps between adjacent candles."
    if category == "indicator":
        return "Technical indicators compress historical price, volume, or volatility behavior into a lower-dimensional signal."
    return "Feature columns transform raw bars into structured context for charts, research, and downstream models."


def knowledge_block(short: str, detailed: str, theory: str, interpretation: str, equation: str, variables: dict[str, str]) -> dict[str, Any]:
    return {
        "shortDescription": short,
        "detailedDescription": detailed,
        "theory": theory,
        "interpretation": interpretation,
        "caveats": [
            "Interpret in the context of timeframe and session regime.",
            "Do not treat future-looking supervision fields as live signals.",
        ],
        "equations": [{"title": "Definition", "markdown": equation, "variables": variables}],
    }


def leakage_block() -> dict[str, Any]:
    return {
        "usesFutureData": True,
        "allowedUses": ["training", "research", "chart_review"],
        "forbiddenUses": ["live_signal", "backtest_entry_without_delay"],
    }


def bar_knowledge_for_column(column: str, title: str) -> dict[str, Any]:
    details = BAR_KNOWLEDGE.get(column.lower())
    if details is None:
        details = {
            "short": f"{title} bar metadata.",
            "detailed": f"{title} is provider-owned bar context used for indexing, joining, filtering, or interpreting market data artifacts.",
            "equation": f"$$\\text{{{title}}}_t=f(Bar_t)$$",
            "variables": {"Bar_t": "Provider bar row"},
        }
    return knowledge_block(
        short=details["short"],
        detailed=details["detailed"],
        theory="Bar metadata preserves the canonical time, identity, and OHLCV context that downstream features, labels, charts, and backtests consume.",
        interpretation="Use this as provider context for filtering, joining, chart display, and session-aware analysis. It is table metadata, not a derived trading indicator.",
        equation=details["equation"],
        variables=details["variables"],
    )


def title_for_column(column: str) -> str:
    parts = column.replace("-", "_").split("_")
    last_index = len(parts) - 1
    return " ".join(title_part(part, index, last_index) for index, part in enumerate(parts) if part)


def title_part(part: str, index: int, last_index: int) -> str:
    lower = part.lower()
    numeric_unit = re.fullmatch(r"(\d+)([a-z]+)", lower)
    if numeric_unit and numeric_unit.group(2) in TITLE_ACRONYMS:
        return f"{numeric_unit.group(1)} {TITLE_ACRONYMS[numeric_unit.group(2)]}"
    trailing_number_match = re.fullmatch(r"([a-z]+)(\d+)", lower)
    if trailing_number_match and trailing_number_match.group(1) in TITLE_ACRONYMS:
        return f"{TITLE_ACRONYMS[trailing_number_match.group(1)]}{trailing_number_match.group(2)}"
    if lower in TITLE_ACRONYMS:
        return TITLE_ACRONYMS[lower]
    if 0 < index < last_index and lower in TITLE_LOWERCASE_WORDS:
        return lower
    return lower[:1].upper() + lower[1:]


def short_title_for_column(column: str, title: str) -> str:
    special = {"macd_hist": "Hist", "macd_signal": "Signal", "macd_line": "MACD"}
    return special.get(column, title)


def trailing_number(value: str, default: int) -> int:
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else default


def stable_index(value: str) -> int:
    return sum((index + 1) * ord(character) for index, character in enumerate(value))


def category_order(category: str) -> int:
    return {"bar": 0, "indicator": 1, "feature": 2, "label": 3}.get(category, 99)


def override_path(processed_root: Path) -> Path:
    return processed_root / PRESENTATION_OVERRIDE_FILE


def load_presentation_overrides(processed_root: Path) -> dict[str, dict[str, Any]]:
    path = override_path(processed_root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    overrides = payload.get("presentation") if isinstance(payload, dict) else None
    return overrides if isinstance(overrides, dict) else {}


def save_presentation_override(processed_root: Path, item_id: str, presentation: dict[str, Any]) -> dict[str, Any]:
    processed_root.mkdir(parents=True, exist_ok=True)
    overrides = load_presentation_overrides(processed_root)
    clean = {str(key): value for key, value in presentation.items() if value is not None}
    overrides[item_id] = clean
    payload = {"catalogVersion": CATALOG_VERSION, "presentation": overrides}
    override_path(processed_root).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return provider_catalog(processed_root)


def apply_presentation_overrides(catalog: dict[str, Any], overrides: dict[str, dict[str, Any]]) -> None:
    if not overrides:
        return
    for section in ("columns", "supervisionMethods", "scanners"):
        for item in catalog.get(section, []):
            item_id = str(item.get("id") or "")
            presentation = overrides.get(item_id)
            if isinstance(presentation, dict):
                merged = deepcopy(item.get("presentation") or {})
                merged.update(presentation)
                item["presentation"] = merged


def catalog_columns_by_column(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("column")): item for item in catalog.get("columns", []) if item.get("column")}


def catalog_item_by_id(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for section in ("columns", "supervisionMethods", "scanners"):
        for item in catalog.get(section, []):
            if item.get("id"):
                items[str(item["id"])] = item
    return items
