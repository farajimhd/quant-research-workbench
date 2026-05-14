from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.data_provider.config import FEATURE_VERSION, SCHEMA_VERSION, SUPERVISION_VERSION
from src.data_provider.features import FEATURE_COLUMNS
from src.data_provider.supervision import METHOD_BAR_WINDOWS


CATALOG_VERSION = 10
PRESENTATION_OVERRIDE_FILE = "catalog_presentation_overrides.json"

BAR_COLUMNS = [
    "bar_id",
    "ticker",
    "timeframe",
    "window_start",
    "bar_time_utc",
    "bar_time_market",
    "session_date",
    "session_month",
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

KEY_COLUMNS = {"bar_id", "ticker", "timeframe", "window_start", "bar_time_utc", "bar_time_market", "session_date", "session_month", "minute_of_day"}
INDICATOR_PREFIXES = ("sma", "ema", "tema", "macd", "rsi", "roc", "cci", "stoch", "atr", "bb_", "donchian", "keltner")
INDICATOR_COLUMNS = {"vwap", "obv", "mfi14", "cmf20"}
OSCILLATOR_TERMS = ("macd", "rsi", "roc", "cci", "stoch", "z20", "relative_", "score", "ratio", "pct", "confidence", "percentile")
DEFAULT_VISIBLE_COLUMNS = {"vwap", "tema9", "tema20", "macd_line", "macd_signal", "macd_hist"}
DEFAULT_VISIBLE_DISPLAY_ITEMS = {"indicator.vwap", "indicator.tema_trend", "indicator.macd"}
DYNAMIC_COLORS = ["#1E3A5F", "#B7791F", "#067647", "#B42318", "#2563EB", "#7C3AED", "#0E7490", "#C2410C"]
OPERATIONAL_HELPER_COLUMNS = {"indicator_bar_count", "macd_ready", "tema_ready"}
SESSION_STRUCTURE_SCALAR_COLUMNS = {
    "premarket_range",
    "or_5m_range",
    "or_10m_range",
    "or_15m_range",
    "or_30m_range",
}
DATA_SHAPES = ["continuous_series", "bar_event", "anchored_zone", "regime_state", "data_only"]
DATA_ONLY_ROLES = {"data_only", "table_only"}
ANCHOR_ZONE_ROLES = {"anchored_zone", "price_zone"}
PRICE_TARGET_ROLES = {"price_overlay", "marker", "continuous_band", "anchored_zone", "price_zone"}
LOWER_PANE_ROLES = {"oscillator", "histogram"}
LOWER_PANE_OPTIONS = ("macd", "pane_2", "pane_3")
DEFAULT_LOWER_PANE = "pane_2"
MACD_PANE = "macd"
BOOLEAN_COLUMNS = {
    "is_green",
    "is_red",
    "bullish_fvg",
    "bearish_fvg",
    "swing_high_3",
    "swing_low_3",
    "swing_high_5",
    "swing_low_5",
    "higher_high",
    "lower_low",
    "bos_up",
    "bos_down",
    "bullish_displacement",
    "bearish_displacement",
    "inside_bar",
    "outside_bar",
    "bullish_engulfing",
    "bearish_engulfing",
    "nr4",
    "nr7",
    "breaks_high20",
    "breaks_low20",
    "reclaim_vwap",
    "breakdown_vwap",
    "return_shock",
    "range_shock",
    "structure_break_shock",
    "price_shock",
    "relative_volume_shock",
    "dollar_volume_shock",
    "transactions_shock",
    "volume_shock",
    "price_shock_recent",
    "volume_shock_recent",
    "price_shock_before_volume_shock",
    "confirmed_price_volume_shock",
    "macd_ready",
    "tema_ready",
    "valid_future_window",
    "mfe_before_mae",
    "oracle_long_entry_signal",
    "oracle_long_exit_signal",
    "method_entry_signal",
    "method_exit_signal",
    "fwd_liquidity_confirmed",
    "fwd_volume_shock_before_mfe",
    "current_price_shock",
    "current_volume_shock",
    "current_confirmed_price_volume_shock",
    "is_top_1",
    "is_top_3",
    "is_top_5",
    "is_top_10",
    "is_top_1pct",
    "is_top_5pct",
}
STRING_COLUMNS = {
    "bar_id",
    "ticker",
    "timeframe",
    "session_date",
    "session_month",
    "horizon",
    "trade_method",
    "oracle_action",
    "fwd_outcome_bucket",
    "shock_confirmation_type",
    "oracle_best_exit_bar_id",
    "method_best_exit_bar_id",
    "shock_best_exit_after_confirmation_bar_id",
    "fwd_first_volume_shock_bar_id",
    "trend_regime",
}
INTEGER_COLUMNS = {
    "window_start",
    "minute_of_day",
    "horizon_bars",
    "horizon_minutes",
    "future_bar_count",
    "time_to_mfe_bars",
    "time_to_mae_bars",
    "time_to_mfe_minutes",
    "time_to_mae_minutes",
    "indicator_bar_count",
    "bars_since_price_shock",
    "bars_since_volume_shock",
    "minutes_since_price_shock",
    "minutes_since_volume_shock",
    "shock_confirmation_delay_minutes",
    "fwd_minutes_to_volume_shock",
    "method_min_horizon_bars",
    "method_max_horizon_bars",
    "method_min_horizon_minutes",
    "method_max_horizon_minutes",
    "method_best_horizon_bars",
    "method_best_horizon_minutes",
    "universe_size",
    "oracle_rank",
    "method_best_horizon_minutes",
}
MARKET_STRUCTURE_EVENT_LEVELS = {
    "swing_high_3": ("high", "#B7791F", 3),
    "swing_low_3": ("low", "#0E7490", 3),
    "swing_high_5": ("high", "#C2410C", 5),
    "swing_low_5": ("low", "#2563EB", 5),
    "higher_high": ("high", "#067647", 4),
    "lower_low": ("low", "#B42318", 4),
    "bos_up": ("high", "#1E3A5F", 8),
    "bos_down": ("low", "#B42318", 8),
}
PRICE_ACTION_EVENT_LEVELS = {
    "breaks_high20": ("high", "#067647", 6),
    "breaks_low20": ("low", "#B42318", 6),
}
PRICE_ACTION_MARKERS = {
    "inside_bar": ("Inside Bar", "circle", "inBar", "#475467"),
    "outside_bar": ("Outside Bar", "square", "inBar", "#7C3AED"),
    "bullish_engulfing": ("Bullish Engulfing", "arrowUp", "belowBar", "#067647"),
    "bearish_engulfing": ("Bearish Engulfing", "arrowDown", "aboveBar", "#B42318"),
    "nr4": ("NR4", "circle", "belowBar", "#0E7490"),
    "nr7": ("NR7", "circle", "belowBar", "#2563EB"),
    "reclaim_vwap": ("VWAP Reclaim", "arrowUp", "belowBar", "#067647"),
    "breakdown_vwap": ("VWAP Breakdown", "arrowDown", "aboveBar", "#B42318"),
}
SHOCK_MARKERS = {
    "price_shock": ("Price Shock", "arrowUp", "aboveBar", "#2563EB"),
    "volume_shock": ("Volume Shock", "circle", "belowBar", "#B7791F"),
    "confirmed_price_volume_shock": ("Confirmed Price Volume Shock", "arrowUp", "aboveBar", "#030213"),
    "price_shock_before_volume_shock": ("Price Before Volume Shock", "square", "belowBar", "#7C3AED"),
}


def event_zone_padding_bps(column: str) -> float:
    lower = column.lower()
    if lower.startswith("swing_"):
        return 0.0
    if lower in {"higher_high", "lower_low"}:
        return 0.0
    if lower.startswith("bos_") or lower.startswith("breaks_"):
        return 0.0
    return 6.0


def event_zone_semantic_style(column: str) -> dict[str, Any]:
    lower = column.lower()
    if lower.startswith("swing_"):
        return {
            "zoneHeightMode": "fixed_px",
            "minPixelHeight": 3,
            "maxPixelHeight": 4,
            "bandFillOpacity": 0.10,
            "borderWidth": 2,
            "borderOpacity": 0.22,
        }
    if lower in {"higher_high", "lower_low"}:
        return {
            "zoneHeightMode": "fixed_px",
            "minPixelHeight": 3,
            "maxPixelHeight": 4,
            "bandFillOpacity": 0.08,
            "borderWidth": 1,
            "borderOpacity": 0.20,
        }
    if lower.startswith("bos_"):
        return {
            "zoneHeightMode": "fixed_px",
            "minPixelHeight": 4,
            "maxPixelHeight": 5,
            "bandFillOpacity": 0.08,
            "borderWidth": 1,
            "borderOpacity": 0.20,
        }
    if lower.startswith("breaks_"):
        return {
            "zoneHeightMode": "fixed_px",
            "minPixelHeight": 3,
            "maxPixelHeight": 4,
            "bandFillOpacity": 0.07,
            "borderWidth": 1,
            "borderOpacity": 0.18,
        }
    return {
        "zoneHeightMode": "fixed_px",
        "minPixelHeight": 3,
        "maxPixelHeight": 5,
        "bandFillOpacity": 0.08,
        "borderWidth": 1,
        "borderOpacity": 0.18,
    }


DISPLAY_PRESETS: dict[str, dict[str, Any]] = {
    "price_overlay": {
        "label": "Price Overlay",
        "dataShapes": ["continuous_series"],
        "target": "price",
        "lockedFields": ["pane"],
        "styleFields": ["color", "opacity", "lineStyle", "lineWidth", "valueFormat", "precision"],
        "description": "Continuous numeric series drawn as a line on the candle price pane.",
    },
    "oscillator": {
        "label": "Oscillator",
        "dataShapes": ["continuous_series"],
        "target": "lower_pane",
        "styleFields": ["pane", "color", "opacity", "lineStyle", "lineWidth", "valueFormat", "precision"],
        "description": "Continuous numeric series drawn as a line in a lower pane.",
    },
    "histogram": {
        "label": "Histogram",
        "dataShapes": ["continuous_series"],
        "target": "lower_pane",
        "styleFields": ["pane", "color", "opacity", "baseline", "valueFormat", "precision"],
        "description": "Continuous numeric series drawn as vertical bars around a baseline.",
    },
    "marker": {
        "label": "Marker",
        "dataShapes": ["bar_event", "anchored_zone"],
        "target": "price",
        "lockedFields": ["pane"],
        "styleFields": ["color", "markerShape", "markerPosition", "markerSize", "labelMode", "labelText"],
        "description": "Discrete event rendered as a symbol on the source bar.",
    },
    "text_label": {
        "label": "Text Label",
        "dataShapes": ["bar_event", "anchored_zone"],
        "target": "price",
        "lockedFields": ["pane"],
        "styleFields": ["color", "markerPosition", "fontSize", "labelMode", "labelText"],
        "description": "Discrete event rendered as a compact text annotation at the source bar.",
    },
    "continuous_band": {
        "label": "Continuous Band",
        "dataShapes": ["continuous_series"],
        "target": "price",
        "lockedFields": ["pane"],
        "styleFields": ["color", "lineStyle", "lineWidth", "bandFillColor", "bandFillOpacity", "valueFormat", "precision"],
        "description": "Price-following envelope around one or more continuous boundary series.",
    },
    "anchored_zone": {
        "label": "Anchored Zone",
        "dataShapes": ["anchored_zone"],
        "target": "price",
        "lockedFields": ["pane"],
        "styleFields": ["color", "bandFillColor", "bandFillOpacity", "borderStyle", "borderWidth", "borderOpacity", "extendRule", "maxBars", "zoneHeightMode", "minPixelHeight", "maxPixelHeight", "zonePaddingBps", "stopOnMitigation"],
        "description": "Event-created price/time zone such as an FVG or order-block proxy.",
    },
    "background_state": {
        "label": "Background State",
        "dataShapes": ["regime_state"],
        "target": "chart_background",
        "lockedFields": ["pane"],
        "styleFields": ["color", "opacity", "priority"],
        "description": "Regime or session state rendered as chart background shading.",
    },
    "composite": {
        "label": "Grouped Display",
        "dataShapes": ["continuous_series", "anchored_zone", "bar_event"],
        "target": "mixed",
        "styleFields": ["valueFormat", "precision"],
        "description": "Group-level contract whose child display items carry their own display types.",
    },
    "data_only": {
        "label": "Data Only",
        "dataShapes": ["any"],
        "target": "none",
        "lockedFields": ["pane"],
        "styleFields": ["valueFormat", "precision"],
        "description": "Available in tables and catalog, but not rendered on the chart.",
    },
}
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
        "displayItems": build_display_items(columns),
        "supervisionMethods": build_method_contracts(),
        "scanners": build_scanner_contracts(),
        "presentationPresets": DISPLAY_PRESETS,
        "presentationOptions": {
            "chartRoles": list(DISPLAY_PRESETS.keys()),
            "dataShapes": DATA_SHAPES,
            "panes": list(LOWER_PANE_OPTIONS),
            "lineStyles": ["solid", "dashed", "dotted"],
            "borderStyles": ["solid", "dashed", "dotted"],
            "extendRules": ["fixed_bars", "until_mitigated", "session_end"],
            "zoneHeightModes": ["price_range", "fixed_px"],
            "labelModes": ["none", "short", "value", "full"],
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


def build_display_items(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_column = {str(item.get("column")): item for item in columns if item.get("column")}
    items: list[dict[str, Any]] = []
    grouped_columns: set[str] = set()

    def add(item: dict[str, Any] | None) -> None:
        if item is None:
            return
        source_columns = [str(column) for column in item.get("sourceColumns", [])]
        if not source_columns or any(column not in by_column for column in source_columns):
            return
        items.append(item)
        grouped_columns.update(source_columns)

    add(single_display_item(by_column, "indicator.vwap", "VWAP", ["vwap"], "core", default_visible=True))
    add(
        composite_display_item(
            by_column,
            item_id="indicator.tema_trend",
            title="TEMA Trend",
            source_columns=["tema9", "tema20"],
            group="momentum",
            pane="price",
            parts=[
                display_part(by_column, "tema9", label="TEMA9", color="#2563EB"),
                display_part(by_column, "tema20", label="TEMA20", color="#B7791F"),
            ],
            default_visible=True,
            short="Fast and slower TEMA overlays for trend responsiveness.",
            detailed="TEMA Trend groups TEMA9 and TEMA20 so the chart treats their relationship as one display choice instead of two unrelated raw columns.",
        )
    )
    add(
        composite_display_item(
            by_column,
            item_id="indicator.macd",
            title="MACD",
            source_columns=["macd_line", "macd_signal", "macd_hist"],
            group="momentum",
            pane="macd",
            parts=[
                display_part(by_column, "macd_line", label="MACD", pane="macd", color="#1E3A5F"),
                display_part(by_column, "macd_signal", label="Signal", pane="macd", color="#B54708"),
                display_part(by_column, "macd_hist", label="Hist", pane="macd", role="histogram", style="histogram", color="inherit_candle_direction"),
            ],
            default_visible=True,
            short="MACD line, signal line, and histogram in one oscillator pane.",
            detailed="MACD is a three-part indicator. Grouping the line, signal, and histogram keeps the pane, legend, and selection behavior faithful to the indicator.",
            value_format="number",
        )
    )
    add(
        composite_display_item(
            by_column,
            item_id="indicator.sma_stack",
            title="SMA Stack",
            source_columns=["sma9", "sma20", "sma50", "sma200"],
            group="momentum",
            pane="price",
            parts=[display_part(by_column, column, label=column.upper(), line_width=1) for column in ["sma9", "sma20", "sma50", "sma200"]],
            short="Simple moving average stack on the price pane.",
            detailed="SMA Stack groups the short, medium, and long simple moving averages so trend compression and separation can be reviewed together.",
        )
    )
    add(
        composite_display_item(
            by_column,
            item_id="indicator.ema_stack",
            title="EMA Stack",
            source_columns=["ema9", "ema20", "ema50", "ema200"],
            group="momentum",
            pane="price",
            parts=[display_part(by_column, column, label=column.upper(), line_width=1) for column in ["ema9", "ema20", "ema50", "ema200"]],
            short="Exponential moving average stack on the price pane.",
            detailed="EMA Stack groups the short, medium, and long exponential moving averages as one trend display.",
        )
    )
    add(channel_display_item(by_column, "indicator.bollinger20", "Bollinger Bands 20", ["bb_upper20", "bb_mid20", "bb_lower20"], "volatility", "#475467"))
    add(channel_display_item(by_column, "indicator.donchian20", "Donchian Channel 20", ["donchian_high20", "donchian_mid20", "donchian_low20"], "volatility", "#0E7490"))
    add(channel_display_item(by_column, "indicator.keltner20", "Keltner Channel 20", ["keltner_upper20", "keltner_mid20", "keltner_lower20"], "volatility", "#B7791F"))
    add(
        composite_display_item(
            by_column,
            item_id="feature.premarket_range",
            title="Premarket Range",
            source_columns=["premarket_high", "premarket_low"],
            group="session",
            pane="price",
            parts=[
                display_part(by_column, "premarket_high", label="Premarket High", color="#B7791F", line_style="dashed"),
                display_part(by_column, "premarket_low", label="Premarket Low", color="#B7791F", line_style="dashed"),
            ],
            short="Premarket high and low range on the price pane.",
            detailed="Premarket range is a two-boundary structure. Grouping the high and low makes it clear that both lines define the same extended-hours reference.",
        )
    )
    for minutes in (5, 10, 15, 30):
        add(
            composite_display_item(
                by_column,
                item_id=f"feature.opening_range_{minutes}m",
                title=f"Opening Range {minutes}m",
                source_columns=[f"or_{minutes}m_high", f"or_{minutes}m_low"],
                group="session",
                pane="price",
                parts=[
                    display_part(by_column, f"or_{minutes}m_high", label=f"OR {minutes}m High", color="#1E3A5F", line_style="dotted"),
                    display_part(by_column, f"or_{minutes}m_low", label=f"OR {minutes}m Low", color="#1E3A5F", line_style="dotted"),
                ],
                short=f"Opening range {minutes}-minute high and low.",
                detailed=f"Opening Range {minutes}m groups the high and low that frame the first {minutes} regular-session minutes.",
            )
        )
    add(
        composite_display_item(
            by_column,
            item_id="feature.session_range",
            title="Session Range",
            source_columns=["day_open", "day_high_so_far", "day_low_so_far"],
            group="session",
            pane="price",
            parts=[
                display_part(by_column, "day_open", label="Open", color="#475467", line_style="dashed"),
                display_part(by_column, "day_high_so_far", label="High", color="#067647"),
                display_part(by_column, "day_low_so_far", label="Low", color="#B42318"),
            ],
            short="Session open, high-so-far, and low-so-far.",
            detailed="Session Range groups the current session anchor and running extremes into one price-structure display.",
        )
    )
    add(anchored_zone_display_item(by_column, "feature.fvg_bullish", "Bullish FVG", "fvg", "bullish_fvg", "fvg_high", "fvg_low", "#067647", 18))
    add(anchored_zone_display_item(by_column, "feature.fvg_bearish", "Bearish FVG", "fvg", "bearish_fvg", "fvg_high", "fvg_low", "#B42318", 18))
    add(anchored_zone_display_item(by_column, "feature.order_block_bullish", "Bullish Order Block", "order_blocks", "bullish_displacement", "bullish_order_block_high", "bullish_order_block_low", "#0E7490", 24))
    add(anchored_zone_display_item(by_column, "feature.order_block_bearish", "Bearish Order Block", "order_blocks", "bearish_displacement", "bearish_order_block_high", "bearish_order_block_low", "#C2410C", 24))
    for signal_column, (level_column, color, extend_bars) in MARKET_STRUCTURE_EVENT_LEVELS.items():
        add(
            anchored_event_level_display_item(
                by_column,
                f"feature.market_structure.{signal_column}",
                str(by_column.get(signal_column, {}).get("title") or title_for_column(signal_column)),
                "market_structure",
                signal_column,
                level_column,
                color,
                extend_bars,
                zone_padding_bps=event_zone_padding_bps(signal_column),
            )
        )
    add(background_state_display_item(by_column, "feature.market_structure.trend_regime", "Trend Regime", "market_structure", "trend_regime"))
    for signal_column, (level_column, color, extend_bars) in PRICE_ACTION_EVENT_LEVELS.items():
        add(
            anchored_event_level_display_item(
                by_column,
                f"feature.price_action.{signal_column}",
                str(by_column.get(signal_column, {}).get("title") or title_for_column(signal_column)),
                "price_action",
                signal_column,
                level_column,
                color,
                extend_bars,
                zone_padding_bps=event_zone_padding_bps(signal_column),
            )
        )
    for signal_column, (title, shape, position, color) in PRICE_ACTION_MARKERS.items():
        add(marker_display_item(by_column, f"feature.price_action.{signal_column}", title, "price_action", signal_column, color, marker_shape=shape, marker_position=position))
    for signal_column, (title, shape, position, color) in SHOCK_MARKERS.items():
        add(marker_display_item(by_column, f"feature.shock.{signal_column}", title, "shock", signal_column, color, marker_shape=shape, marker_position=position))
    add(
        composite_display_item(
            by_column,
            item_id="feature.price_volume_shock",
            title="Price Volume Shock",
            source_columns=["price_shock_score", "volume_shock_score", "price_volume_shock_score"],
            group="shock",
            pane="shock",
            parts=[
                display_part(by_column, "price_shock_score", label="Price", pane="shock", color="#2563EB"),
                display_part(by_column, "volume_shock_score", label="Volume", pane="shock", color="#B7791F"),
                display_part(by_column, "price_volume_shock_score", label="Combined", pane="shock", color="#030213", line_width=2),
            ],
            short="Price, volume, and combined shock scores in one pane.",
            detailed="Price Volume Shock is sequence-aware. The grouped display keeps price abnormality, volume abnormality, and combined confirmation score together.",
            value_format="number",
        )
    )
    add(
        composite_display_item(
            by_column,
            item_id="feature.volume_participation",
            title="Volume Participation",
            source_columns=["relative_volume20", "relative_dollar_volume20", "volume_z20", "transactions_z20"],
            group="volume_liquidity",
            pane="participation",
            parts=[
                display_part(by_column, "relative_volume20", label="Rel Vol", pane="participation", color="#067647"),
                display_part(by_column, "relative_dollar_volume20", label="Rel Dollar", pane="participation", color="#0E7490"),
                display_part(by_column, "volume_z20", label="Vol Z", pane="participation", color="#B7791F"),
                display_part(by_column, "transactions_z20", label="Txn Z", pane="participation", color="#7C3AED"),
            ],
            short="Relative volume, dollar volume, and activity z-scores.",
            detailed="Volume Participation groups the provider participation metrics so abnormal activity can be reviewed as one lower-pane context.",
            value_format="number",
        )
    )

    for column, contract in by_column.items():
        presentation = contract.get("presentation", {})
        role = str(presentation.get("chartRole") or "")
        if (
            column not in grouped_columns
            and contract.get("category") in {"indicator", "feature"}
            and presentation.get("selectable", True)
            and column not in SESSION_STRUCTURE_SCALAR_COLUMNS
            and role not in {"", "marker", "text_label", "data_only", "table_only"}
        ):
            add(single_display_item(by_column, f"column.{column}", str(contract.get("title") or title_for_column(column)), [column], str(contract.get("group") or ""), default_visible=False))

    return sorted(items, key=lambda item: (category_order(str(item.get("category"))), str(item.get("group")), str(item.get("title")), str(item.get("id"))))


def display_item_contract(
    by_column: dict[str, dict[str, Any]],
    *,
    item_id: str,
    title: str,
    source_columns: list[str],
    group: str,
    category: str,
    presentation: dict[str, Any],
    short: str,
    detailed: str,
) -> dict[str, Any] | None:
    if any(column not in by_column for column in source_columns):
        return None
    artifact_groups = sorted({artifact for column in source_columns for artifact in by_column[column].get("artifactGroups", [])})
    feature_groups = sorted({artifact.replace("features_", "", 1) for artifact in artifact_groups if artifact.startswith("features_")})
    normalized_presentation = normalize_presentation(presentation)
    data_shape = str(normalized_presentation.get("dataShape") or common_data_shape(by_column, source_columns))
    return {
        "id": item_id,
        "title": title,
        "shortTitle": title,
        "category": category,
        "group": group,
        "groups": [group],
        "dataShape": data_shape,
        "sourceColumns": source_columns,
        "artifactGroups": artifact_groups,
        "featureGroups": feature_groups,
        "knowledge": knowledge_block(
            short=short,
            detailed=detailed,
            theory=theory_for_group(group, category),
            interpretation=interpretation_for_group(group, category),
            equation=display_item_equation(title, source_columns),
            variables={"SourceColumns": ", ".join(source_columns)},
        ),
        "presentation": normalized_presentation,
    }


def single_display_item(
    by_column: dict[str, dict[str, Any]],
    item_id: str,
    title: str,
    source_columns: list[str],
    group: str,
    *,
    default_visible: bool,
) -> dict[str, Any] | None:
    column = source_columns[0]
    contract = by_column.get(column)
    if not contract:
        return None
    base_presentation = deepcopy(contract.get("presentation") or {})
    base_presentation.update(
        {
            "defaultVisible": default_visible or item_id in DEFAULT_VISIBLE_DISPLAY_ITEMS,
            "displayItem": True,
            "sourceColumn": column,
        }
    )
    return display_item_contract(
        by_column,
        item_id=item_id,
        title=title,
        source_columns=source_columns,
        group=group or str(contract.get("group") or ""),
        category=str(contract.get("category") or "feature"),
        presentation=base_presentation,
        short=str(contract.get("knowledge", {}).get("shortDescription") or f"{title} chart display."),
        detailed=str(contract.get("knowledge", {}).get("detailedDescription") or f"{title} displays the provider column {column}."),
    )


def composite_display_item(
    by_column: dict[str, dict[str, Any]],
    *,
    item_id: str,
    title: str,
    source_columns: list[str],
    group: str,
    pane: str,
    parts: list[dict[str, Any]],
    short: str,
    detailed: str,
    default_visible: bool = False,
    value_format: str = "price",
) -> dict[str, Any] | None:
    return display_item_contract(
        by_column,
        item_id=item_id,
        title=title,
        source_columns=source_columns,
        group=group,
        category="indicator" if item_id.startswith("indicator.") else "feature",
        presentation={
            "selectable": True,
            "defaultVisible": default_visible or item_id in DEFAULT_VISIBLE_DISPLAY_ITEMS,
            "chartRole": "composite",
            "dataShape": "continuous_series",
            "pane": pane,
            "legend": True,
            "valueFormat": value_format,
            "parts": parts,
            "presentationSource": "auto",
            "presentationConfidence": 0.92,
        },
        short=short,
        detailed=detailed,
    )


def channel_display_item(by_column: dict[str, dict[str, Any]], item_id: str, title: str, source_columns: list[str], group: str, color: str) -> dict[str, Any] | None:
    labels = ["Upper", "Middle", "Lower"] if "bb_" in source_columns[0] or "keltner" in source_columns[0] else ["High", "Middle", "Low"]
    return composite_display_item(
        by_column,
        item_id=item_id,
        title=title,
        source_columns=source_columns,
        group=group,
        pane="price",
        parts=[
            display_part(by_column, source_columns[0], label=labels[0], color=color, line_width=1),
            display_part(by_column, source_columns[1], label=labels[1], color=color, line_style="dashed", line_width=1),
            display_part(by_column, source_columns[2], label=labels[2], color=color, line_width=1),
        ],
        short=f"{title} channel boundaries.",
        detailed=f"{title} is a multi-column channel and is therefore exposed as one grouped chart display with its boundaries kept together.",
    )


def anchored_zone_display_item(
    by_column: dict[str, dict[str, Any]],
    item_id: str,
    title: str,
    group: str,
    signal_column: str,
    upper_column: str,
    lower_column: str,
    color: str,
    extend_bars: int,
) -> dict[str, Any] | None:
    source_columns = [signal_column, upper_column, lower_column]
    direction = "bullish" if "bullish" in item_id else "bearish" if "bearish" in item_id else "neutral"
    semantic_style = {
        "bandFillOpacity": 0.07 if group == "fvg" else 0.06,
        "borderWidth": 1,
        "borderOpacity": 0.16 if group == "fvg" else 0.14,
        "zoneHeightMode": "price_range",
    }
    return display_item_contract(
        by_column,
        item_id=item_id,
        title=title,
        source_columns=source_columns,
        group=group,
        category="feature",
        presentation={
            "selectable": True,
            "defaultVisible": False,
            "chartRole": "anchored_zone",
            "dataShape": "anchored_zone",
            "pane": "price",
            "legend": True,
            "color": color,
            "bandFillColor": color,
            "bandFillOpacity": semantic_style["bandFillOpacity"],
            "borderColor": color,
            "borderStyle": "solid",
            "borderWidth": semantic_style["borderWidth"],
            "borderOpacity": semantic_style["borderOpacity"],
            "direction": direction,
            "signalColumn": signal_column,
            "upperColumn": upper_column,
            "lowerColumn": lower_column,
            "extendBars": extend_bars,
            "maxBars": extend_bars,
            "extendRule": "fixed_bars",
            "zoneHeightMode": semantic_style["zoneHeightMode"],
            "zonePaddingBps": 0,
            "stopOnMitigation": False,
            "valueFormat": "price",
            "presentationSource": "auto",
            "presentationConfidence": 0.95,
        },
        short=f"{title} zone on the candle pane.",
        detailed=f"{title} is a candle-structure display, not an oscillator. It uses {signal_column} to find events and draws the {lower_column}-{upper_column} price zone forward for review.",
    )


def anchored_event_level_display_item(
    by_column: dict[str, dict[str, Any]],
    item_id: str,
    title: str,
    group: str,
    signal_column: str,
    level_column: str,
    color: str,
    extend_bars: int,
    *,
    zone_padding_bps: float = 8.0,
) -> dict[str, Any] | None:
    semantic_style = event_zone_semantic_style(signal_column)
    return display_item_contract(
        by_column,
        item_id=item_id,
        title=title,
        category="feature",
        group=group,
        source_columns=[signal_column, level_column],
        presentation={
            "selectable": True,
            "defaultVisible": False,
            "chartRole": "anchored_zone",
            "dataShape": "anchored_zone",
            "pane": "price",
            "legend": True,
            "color": color,
            "bandFillColor": color,
            "bandFillOpacity": semantic_style["bandFillOpacity"],
            "borderColor": color,
            "borderStyle": "solid",
            "borderWidth": semantic_style["borderWidth"],
            "borderOpacity": semantic_style["borderOpacity"],
            "signalColumn": signal_column,
            "upperColumn": level_column,
            "lowerColumn": level_column,
            "maxBars": extend_bars,
            "extendRule": "fixed_bars",
            "zoneHeightMode": semantic_style["zoneHeightMode"],
            "minPixelHeight": semantic_style["minPixelHeight"],
            "maxPixelHeight": semantic_style["maxPixelHeight"],
            "zonePaddingBps": zone_padding_bps,
            "stopOnMitigation": False,
            "valueFormat": "price",
            "presentationSource": "auto",
            "presentationConfidence": 0.93,
        },
        short=f"{title} event zone anchored to the source bar's {level_column}.",
        detailed=f"{title} is a discrete structural event, so it is not drawn as a continuous line. The chart highlights a narrow price band at the event {level_column} and extends it forward for a configurable number of bars.",
    )


def marker_display_item(
    by_column: dict[str, dict[str, Any]],
    item_id: str,
    title: str,
    group: str,
    signal_column: str,
    color: str,
    *,
    marker_shape: str = "circle",
    marker_position: str = "belowBar",
) -> dict[str, Any] | None:
    return display_item_contract(
        by_column,
        item_id=item_id,
        title=title,
        category="feature",
        group=group,
        source_columns=[signal_column],
        presentation={
            "selectable": True,
            "defaultVisible": False,
            "chartRole": "marker",
            "dataShape": "bar_event",
            "pane": "price",
            "legend": True,
            "color": color,
            "markerShape": marker_shape,
            "markerPosition": marker_position,
            "signalColumn": signal_column,
            "labelMode": "short",
            "valueFormat": "boolean",
            "presentationSource": "auto",
            "presentationConfidence": 0.9,
        },
        short=f"{title} marker on the event bar.",
        detailed=f"{title} is a boolean event. The chart renders it as a marker at the source bar instead of converting True/False values into a misleading numeric line.",
    )


def background_state_display_item(
    by_column: dict[str, dict[str, Any]],
    item_id: str,
    title: str,
    group: str,
    state_column: str,
) -> dict[str, Any] | None:
    return display_item_contract(
        by_column,
        item_id=item_id,
        title=title,
        category="feature",
        group=group,
        source_columns=[state_column],
        presentation={
            "selectable": True,
            "defaultVisible": False,
            "chartRole": "background_state",
            "dataShape": "regime_state",
            "pane": "price",
            "legend": True,
            "stateColumn": state_column,
            "color": "#667085",
            "stateColors": {"up": "#067647", "down": "#B42318", "range": "#667085"},
            "opacity": 0.08,
            "presentationSource": "auto",
            "presentationConfidence": 0.9,
        },
        short=f"{title} as light chart-background regime shading.",
        detailed=f"{title} is a categorical state, not a numeric series. The chart displays contiguous state intervals as subtle background shading so the regime context is visible without creating a false y-axis value.",
    )


def display_part(
    by_column: dict[str, dict[str, Any]],
    column: str,
    *,
    label: str | None = None,
    pane: str = "price",
    role: str = "price_overlay",
    style: str = "line",
    color: str | None = None,
    line_style: str = "solid",
    line_width: int | None = None,
    opacity: float | None = None,
) -> dict[str, Any]:
    contract = by_column.get(column, {})
    presentation = contract.get("presentation", {}) if contract else {}
    resolved_role = role if role != "price_overlay" or pane == "price" else "oscillator"
    resolved_line_width = line_width if line_width is not None else int(presentation.get("lineWidth") or line_width_for_column(column, resolved_role))
    return {
        "id": column,
        "column": column,
        "label": label or str(contract.get("shortTitle") or contract.get("title") or title_for_column(column)),
        "chartRole": resolved_role,
        "dataShape": str(presentation.get("dataShape") or data_shape_for_column(column, str(contract.get("group") or ""), str(contract.get("category") or ""))),
        "pane": pane,
        "style": style,
        "color": color or str(presentation.get("color") or color_for_column(column)),
        "lineStyle": line_style,
        "lineWidth": resolved_line_width,
        "opacity": opacity if opacity is not None else float(presentation.get("opacity") or opacity_for_column(column, resolved_role)),
        "legend": True,
    }


def display_item_equation(title: str, source_columns: list[str]) -> str:
    source_text = ", ".join(source_columns)
    return f"$$\\text{{{title}}}_t=Display({source_text})$$"


def common_data_shape(by_column: dict[str, dict[str, Any]], source_columns: list[str]) -> str:
    shapes = [str(by_column.get(column, {}).get("dataShape") or by_column.get(column, {}).get("presentation", {}).get("dataShape") or "data_only") for column in source_columns]
    if not shapes:
        return "data_only"
    if all(shape == shapes[0] for shape in shapes):
        return shapes[0]
    if "anchored_zone" in shapes:
        return "anchored_zone"
    if "bar_event" in shapes:
        return "bar_event"
    if "regime_state" in shapes:
        return "regime_state"
    if "continuous_series" in shapes:
        return "continuous_series"
    return "data_only"


def normalize_chart_role(role: Any) -> str:
    value = str(role or "data_only")
    if value == "table_only":
        return "data_only"
    if value == "price_zone":
        return "anchored_zone"
    if value == "band":
        return "continuous_band"
    return value if value in DISPLAY_PRESETS else "data_only"


def normalize_lower_pane(pane: Any) -> str:
    value = str(pane or DEFAULT_LOWER_PANE).strip().lower()
    aliases = {
        "": DEFAULT_LOWER_PANE,
        "new": DEFAULT_LOWER_PANE,
        "oscillator": DEFAULT_LOWER_PANE,
        "participation": DEFAULT_LOWER_PANE,
        "shock": DEFAULT_LOWER_PANE,
        "stochastic": DEFAULT_LOWER_PANE,
        "supervision": DEFAULT_LOWER_PANE,
        "pane2": "pane_2",
        "pane 2": "pane_2",
        "pane3": "pane_3",
        "pane 3": "pane_3",
        "macd_pane": MACD_PANE,
        "macd pane": MACD_PANE,
    }
    value = aliases.get(value, value)
    return value if value in LOWER_PANE_OPTIONS else DEFAULT_LOWER_PANE


def presentation_allows_macd_pane(item: dict[str, Any], presentation: dict[str, Any]) -> bool:
    candidates = [
        str(item.get("id") or ""),
        str(item.get("column") or ""),
        str(presentation.get("id") or ""),
        str(presentation.get("column") or ""),
        str(item.get("presentation", {}).get("groupKey") or ""),
        str(presentation.get("groupKey") or ""),
    ]
    candidates.extend(str(column) for column in item.get("sourceColumns", []) if column)
    candidates.extend(str(column) for column in presentation.get("sourceColumns", []) if column)
    return any(candidate.lower() == "macd" or candidate.lower().startswith(("macd_", "indicator.macd")) for candidate in candidates)


def enforce_pane_contract(item: dict[str, Any], presentation: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(presentation)
    role = normalize_chart_role(normalized.get("chartRole"))
    if role in LOWER_PANE_ROLES:
        pane = normalize_lower_pane(normalized.get("pane"))
        if pane == MACD_PANE and not presentation_allows_macd_pane(item, normalized):
            pane = DEFAULT_LOWER_PANE
        normalized["pane"] = pane
    elif role == "composite" and normalized.get("pane") and normalized.get("pane") != "price":
        pane = normalize_lower_pane(normalized.get("pane"))
        if pane == MACD_PANE and not presentation_allows_macd_pane(item, normalized):
            pane = DEFAULT_LOWER_PANE
        normalized["pane"] = pane
    parts = normalized.get("parts")
    if isinstance(parts, list):
        normalized["parts"] = [
            enforce_pane_contract(item, part) if isinstance(part, dict) else part
            for part in parts
        ]
    return normalized


def normalize_presentation(presentation: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(presentation)
    role = normalize_chart_role(normalized.get("chartRole"))
    normalized["chartRole"] = role
    normalized["styleFields"] = list(DISPLAY_PRESETS.get(role, DISPLAY_PRESETS["data_only"]).get("styleFields", []))
    normalized["presentationSource"] = normalized.get("presentationSource") or "auto"
    normalized["presentationConfidence"] = float(normalized.get("presentationConfidence") or 0.85)
    if role == "data_only":
        normalized["legend"] = False
        normalized.pop("pane", None)
    elif role in PRICE_TARGET_ROLES:
        normalized["pane"] = "price"
    elif role in LOWER_PANE_ROLES:
        normalized["pane"] = normalize_lower_pane(normalized.get("pane"))
    elif role == "composite" and normalized.get("pane") and normalized.get("pane") != "price":
        normalized["pane"] = normalize_lower_pane(normalized.get("pane"))
    parts = normalized.get("parts")
    if isinstance(parts, list):
        normalized["parts"] = [normalize_presentation(part) if isinstance(part, dict) else part for part in parts]
    return {key: value for key, value in normalized.items() if value is not None}


def chart_role_supports_shape(role: str, data_shape: str) -> bool:
    shapes = DISPLAY_PRESETS.get(role, DISPLAY_PRESETS["data_only"]).get("dataShapes", [])
    return "any" in shapes or data_shape in shapes


def merge_presentation_override(item: dict[str, Any], presentation: dict[str, Any]) -> dict[str, Any]:
    base = normalize_presentation(deepcopy(item.get("presentation") or {}))
    override = normalize_presentation(presentation)
    requested_role = str(override.get("chartRole") or base.get("chartRole") or "data_only")
    data_shape = str(item.get("dataShape") or base.get("dataShape") or "data_only")
    if not chart_role_supports_shape(requested_role, data_shape):
        override = {key: value for key, value in override.items() if key in {"selectable", "defaultVisible"}}
    merged = deepcopy(base)
    merged.update(override)
    return enforce_pane_contract(item, normalize_presentation(merged))


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
        "dataShape": data_shape_for_column(column, group, category),
        "artifactGroups": [artifact_group],
        "dtype": dtype_for_column(column),
        "knowledge": knowledge_for_column(column, group, category, title),
        "semantics": semantics_for_column(column),
        "presentation": normalize_presentation(presentation_for_column(column, group, category)),
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
                "dataShape": "bar_event",
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
                    caveats=[
                        "Uses future bars by design. Use for research, training labels, and chart review only.",
                        "Do not use method supervision fields as live signals without a delay-safe model.",
                    ],
                ),
                "leakage": leakage_block(),
                "presentation": {
                    "selectable": True,
                    "defaultVisible": False,
                    "chartRole": "marker",
                    "dataShape": "bar_event",
                    "pane": "price",
                    "markerShape": "arrowUp",
                    "markerPosition": "belowBar",
                    "color": "#2563EB",
                    "labelMode": "short",
                    "labelText": "ENTRY",
                    "markerSize": 1.15,
                    "valueFormat": "boolean",
                    "legend": True,
                    "presentationSource": "auto",
                    "presentationConfidence": 0.9,
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
            "dataShape": "bar_event",
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
                "dataShape": "bar_event",
                "pane": "price",
                "markerShape": "square",
                "markerPosition": "aboveBar",
                "color": "#7C3AED",
                "labelMode": "short",
                "labelText": "RANK",
                "markerSize": 1.2,
                "valueFormat": "integer",
                "legend": True,
                "presentationSource": "auto",
                "presentationConfidence": 0.9,
            },
        }
    ]


def category_for_column(column: str, group: str) -> str:
    if group.startswith("supervision_"):
        return "label"
    if group == "bars" or column in KEY_COLUMNS:
        return "bar"
    if column.lower() in OPERATIONAL_HELPER_COLUMNS:
        return "feature"
    if is_indicator_column(column):
        return "indicator"
    return "feature"


def is_indicator_column(column: str) -> bool:
    lower = column.lower()
    return lower in INDICATOR_COLUMNS or lower.startswith(INDICATOR_PREFIXES)


def dtype_for_column(column: str) -> str:
    lower = column.lower()
    if lower in BOOLEAN_COLUMNS or lower.startswith("is_") or lower.endswith("_signal") or lower.endswith("_confirmed") or "shock_before" in lower:
        return "bool"
    if lower in STRING_COLUMNS or lower.endswith("_bar_id"):
        return "string"
    if lower in {"macd_line", "macd_signal", "macd_hist"}:
        return "float"
    if lower.endswith("_utc") or lower.endswith("_market") or lower.endswith("_time"):
        return "datetime"
    if lower.endswith("_date"):
        return "date"
    if lower in INTEGER_COLUMNS or lower.endswith("_bars") or lower.endswith("_minutes") or lower.endswith("_count") or lower.endswith("_rank"):
        return "int"
    return "float"


def is_price_level_column(column: str) -> bool:
    lower = column.lower()
    if lower in {"open", "high", "low", "close", "vwap", "prev_close", "day_open", "day_high_so_far", "day_low_so_far"}:
        return True
    if re.match(r"^(sma|ema|tema)\d+$", lower):
        return True
    if re.match(r"^or_\d+m_(high|low)$", lower):
        return True
    if lower in {"premarket_high", "premarket_low", "hvn_price_proxy20", "lvn_price_proxy20"}:
        return True
    if lower.startswith(("bb_upper", "bb_mid", "bb_lower", "donchian_high", "donchian_mid", "donchian_low", "keltner_upper", "keltner_mid", "keltner_lower")):
        return True
    if lower.endswith("_price") or lower.endswith("_price_proxy"):
        return True
    if lower.endswith("_high") or lower.endswith("_low"):
        return lower.startswith(("fvg_", "bullish_order_block_", "bearish_order_block_"))
    return False


def semantics_for_column(column: str) -> dict[str, Any]:
    lower = column.lower()
    if dtype_for_column(column) == "bool":
        unit = "boolean"
    elif dtype_for_column(column) in {"datetime", "date", "string"}:
        unit = dtype_for_column(column)
    elif lower.endswith("_return") or lower.endswith("_pct") or "percentile" in lower:
        unit = "percent"
    elif lower.endswith("_minutes") or lower.startswith("minutes_since"):
        unit = "minutes"
    elif lower.endswith("_bars") or lower.startswith("bars_since"):
        unit = "bars"
    elif "score" in lower or "confidence" in lower or "quality" in lower:
        unit = "score"
    elif "volume" in lower:
        unit = "shares" if "dollar" not in lower else "currency"
    elif is_price_level_column(column):
        unit = "price"
    else:
        unit = "number"
    direction = "higher_better" if any(term in lower for term in ("confidence", "score", "quality", "return", "percentile")) else "neutral"
    role = "operational_helper" if lower in OPERATIONAL_HELPER_COLUMNS else "analysis_field"
    return {"unit": unit, "direction": direction, "nullable": column not in KEY_COLUMNS, "role": role}


def presentation_for_column(column: str, group: str, category: str) -> dict[str, Any]:
    lower = column.lower()
    data_shape = data_shape_for_column(column, group, category)
    role = chart_role_for_column(column, group, category)
    pane = pane_for_role(column, role)
    selectable = lower not in OPERATIONAL_HELPER_COLUMNS and (
        category in {"indicator", "feature"} or (category == "label" and role not in DATA_ONLY_ROLES)
    )
    presentation: dict[str, Any] = {
        "selectable": selectable,
        "defaultVisible": column in DEFAULT_VISIBLE_COLUMNS,
        "chartRole": role,
        "dataShape": data_shape,
        "pane": pane,
        "groupKey": "macd" if lower.startswith("macd_") else None,
        "color": color_for_column(column),
        "lineStyle": "solid",
        "lineWidth": line_width_for_column(column, role),
        "opacity": opacity_for_column(column, role),
        "valueFormat": value_format_for_column(column),
        "precision": precision_for_column(column),
        "legend": role not in DATA_ONLY_ROLES,
        "presentationSource": "auto",
        "presentationConfidence": presentation_confidence_for_column(column, group, category, role, data_shape),
    }
    if role == "marker":
        if group == "supervision_scanner":
            label = {"is_top_1": "TOP1", "is_top_3": "TOP3", "is_top_5": "TOP5", "is_top_10": "TOP10", "is_top_1pct": "TOP1%", "is_top_5pct": "TOP5%"}.get(lower, "TOP")
            presentation.update({"markerShape": "square", "markerPosition": "aboveBar", "color": "#7C3AED", "labelMode": "short", "labelText": label, "markerSize": 1.2})
        elif group == "supervision_method":
            if lower == "method_exit_signal":
                presentation.update({"markerShape": "arrowDown", "markerPosition": "aboveBar", "color": "#B42318", "labelMode": "short", "labelText": "IGNORE", "markerSize": 1.1})
            else:
                presentation.update({"markerShape": "arrowUp", "markerPosition": "belowBar", "color": "#2563EB", "labelMode": "short", "labelText": "ENTRY", "markerSize": 1.15})
        elif group == "supervision_bar":
            if lower == "oracle_long_exit_signal":
                presentation.update({"markerShape": "arrowDown", "markerPosition": "aboveBar", "color": "#B42318", "labelMode": "short", "labelText": "EXIT", "markerSize": 1.1})
            elif lower == "fwd_liquidity_confirmed":
                presentation.update({"markerShape": "circle", "markerPosition": "belowBar", "color": "#0E7490", "labelMode": "short", "labelText": "LIQ", "markerSize": 1.0})
            elif lower == "fwd_volume_shock_before_mfe":
                presentation.update({"markerShape": "square", "markerPosition": "belowBar", "color": "#0891B2", "labelMode": "short", "labelText": "VOL", "markerSize": 1.0})
            elif lower == "mfe_before_mae":
                presentation.update({"markerShape": "circle", "markerPosition": "belowBar", "color": "#15803D", "labelMode": "short", "labelText": "MFE", "markerSize": 1.0})
            else:
                presentation.update({"markerShape": "arrowUp", "markerPosition": "belowBar", "color": "#067647", "labelMode": "short", "labelText": "BAR", "markerSize": 1.15})
        else:
            presentation.update({"markerShape": "circle", "markerPosition": "belowBar", "color": "#067647"})
    if role == "anchored_zone":
        level_column, zone_color, extend_bars = {**MARKET_STRUCTURE_EVENT_LEVELS, **PRICE_ACTION_EVENT_LEVELS}.get(lower, ("close", color_for_column(column), 12))
        semantic_style = event_zone_semantic_style(column)
        presentation.update(
            {
                "signalColumn": column,
                "upperColumn": level_column,
                "lowerColumn": level_column,
                "maxBars": extend_bars,
                "extendRule": "fixed_bars",
                "zoneHeightMode": semantic_style["zoneHeightMode"],
                "minPixelHeight": semantic_style["minPixelHeight"],
                "maxPixelHeight": semantic_style["maxPixelHeight"],
                "zonePaddingBps": event_zone_padding_bps(column),
                "bandFillColor": zone_color,
                "bandFillOpacity": semantic_style["bandFillOpacity"],
                "borderColor": zone_color,
                "borderStyle": "solid",
                "borderWidth": semantic_style["borderWidth"],
                "borderOpacity": semantic_style["borderOpacity"],
                "color": zone_color,
            }
        )
    return {key: value for key, value in presentation.items() if value is not None}


def data_shape_for_column(column: str, group: str, category: str) -> str:
    lower = column.lower()
    if lower in OPERATIONAL_HELPER_COLUMNS:
        return "data_only"
    if category == "bar" or column in KEY_COLUMNS:
        return "data_only"
    if group.startswith("supervision_"):
        if lower in {"oracle_long_entry_signal", "oracle_long_exit_signal", "mfe_before_mae", "fwd_liquidity_confirmed", "fwd_volume_shock_before_mfe", "method_entry_signal", "method_exit_signal", "is_top_1", "is_top_3", "is_top_5", "is_top_10", "is_top_1pct", "is_top_5pct"}:
            return "bar_event"
        return "data_only"
    if group == "fvg":
        return "bar_event" if lower in {"bullish_fvg", "bearish_fvg"} else "data_only"
    if group == "market_structure" and lower in MARKET_STRUCTURE_EVENT_LEVELS:
        return "anchored_zone"
    if group == "price_action" and lower in PRICE_ACTION_EVENT_LEVELS:
        return "anchored_zone"
    if group == "order_blocks":
        if lower in {"bullish_displacement", "bearish_displacement"}:
            return "bar_event"
        if lower in {"distance_to_demand_pct", "distance_to_supply_pct"}:
            return "continuous_series"
        return "data_only"
    if lower == "trend_regime" or lower.endswith("_regime"):
        return "regime_state"
    if dtype_for_column(column) == "bool":
        return "bar_event"
    if dtype_for_column(column) == "string":
        return "data_only"
    if category in {"indicator", "feature"}:
        return "continuous_series"
    return "data_only"


def chart_role_for_column(column: str, group: str, category: str) -> str:
    lower = column.lower()
    if category == "bar" or column in KEY_COLUMNS:
        return "data_only"
    if lower in OPERATIONAL_HELPER_COLUMNS:
        return "data_only"
    if group.startswith("supervision_"):
        return "marker" if lower in {"oracle_long_entry_signal", "oracle_long_exit_signal", "mfe_before_mae", "fwd_liquidity_confirmed", "fwd_volume_shock_before_mfe", "method_entry_signal", "method_exit_signal", "is_top_1", "is_top_3", "is_top_5", "is_top_10", "is_top_1pct", "is_top_5pct"} else "data_only"
    if group == "fvg":
        return "data_only"
    if group == "market_structure" and lower in MARKET_STRUCTURE_EVENT_LEVELS:
        return "anchored_zone"
    if group == "price_action" and lower in PRICE_ACTION_EVENT_LEVELS:
        return "anchored_zone"
    if group == "order_blocks" and ("order_block" in lower or "displacement" in lower):
        return "data_only"
    if lower == "trend_regime" or lower.endswith("_regime"):
        return "background_state"
    if dtype_for_column(column) == "bool":
        return "marker"
    if dtype_for_column(column) == "string":
        return "data_only"
    if lower == "macd_hist":
        return "histogram"
    if is_price_level_column(column):
        return "price_overlay"
    if any(term in lower for term in OSCILLATOR_TERMS):
        return "oscillator"
    return "oscillator"


def pane_for_role(column: str, role: str) -> str:
    if role in PRICE_TARGET_ROLES:
        return "price"
    if column.lower().startswith("macd_"):
        return MACD_PANE
    if role in {"oscillator", "histogram"}:
        return DEFAULT_LOWER_PANE
    return "price"


def line_width_for_column(column: str, role: str) -> int:
    lower = column.lower()
    if role in DATA_ONLY_ROLES or role == "marker":
        return 1
    if role == "histogram":
        return 1
    if role == "price_overlay" and lower in {"vwap", "ema200", "sma200"}:
        return 3
    return 1


def opacity_for_column(column: str, role: str) -> float:
    lower = column.lower()
    if role == "price_overlay" and lower in {"vwap", "ema200", "sma200"}:
        return 0.46
    if role == "price_overlay":
        return 0.82
    if role in {"oscillator", "histogram"}:
        return 0.9
    if role in ANCHOR_ZONE_ROLES or role == "continuous_band":
        return 0.72
    return 1.0


def presentation_confidence_for_column(column: str, group: str, category: str, role: str, data_shape: str) -> float:
    lower = column.lower()
    if column in DEFAULT_VISIBLE_COLUMNS or lower in {"vwap", "ema200", "sma200", "macd_line", "macd_signal", "macd_hist", "rsi14", "atr14"}:
        return 0.96
    if group in {"fvg", "order_blocks", "shock", "session", "volume_liquidity", "momentum"}:
        return 0.9
    if role == "data_only" and data_shape == "data_only":
        return 0.88
    if category in {"indicator", "feature", "label"}:
        return 0.82
    return 0.75


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
    if group.startswith("supervision_"):
        return supervision_knowledge_for_column(column, group, title)
    if lower == "vwap":
        return knowledge_block(
            short="Volume-weighted average price.",
            detailed="VWAP is the cumulative dollar-volume divided by cumulative share volume for the same ticker and session. In this provider it resets by ticker and session_date, so the value describes where the session has traded on a volume-weighted basis up to the current bar.",
            theory="VWAP is a participation-weighted estimator of the session's traded consensus price. Unlike a time-weighted moving average, every bar contributes in proportion to traded shares, so high-participation intervals move the anchor more than quiet intervals. In microstructure terms it is a practical benchmark for whether new trades are occurring above or below the volume-weighted cost basis of the session.",
            interpretation="Read price above a rising VWAP as evidence that buyers are accepting prices above the session's volume-weighted consensus. A reclaim after trading below VWAP can indicate a change in intraday control, while repeated failures near VWAP often mark mean-reversion pressure. Its usefulness increases when combined with relative volume, range expansion, and session context.",
            equation="$$VWAP_t=\\frac{\\sum_{i=1}^{t}Close_i\\cdot Volume_i}{\\sum_{i=1}^{t}Volume_i}$$",
            variables={"Close_i": "Close price for earlier bar i in the same ticker/session", "Volume_i": "Share volume for bar i"},
            caveats=[
                "VWAP is path-dependent and session-reset; do not compare one session's VWAP directly with another without controlling for session structure.",
                "A price crossing VWAP is not a signal by itself. Low-volume crosses and noisy midday rotation can produce false regime changes.",
                "Provider VWAP uses bar close times volume, so it is an aggregate approximation rather than trade-level VWAP.",
            ],
        )
    if lower.startswith("sma"):
        window = trailing_number(lower, 20)
        return knowledge_block(
            short=f"Simple moving average over {window} bars.",
            detailed="SMA smooths price by averaging the last N closes with equal weight.",
            theory="SMA is a finite impulse response smoother: each close in the lookback window receives identical weight and all older observations receive zero weight. That makes it transparent and stable, but it also introduces lag roughly proportional to the window length. The slope and relative ordering of multiple SMAs summarize low-frequency trend information after filtering high-frequency candle noise.",
            interpretation="Use SMA as a slow trend and location reference. Price above a rising long SMA suggests persistent upward drift; stacked short-over-long averages show trend alignment; compressed averages show reduced directional separation. Treat the level as a reference zone rather than a precise support or resistance line.",
            equation=f"$$SMA_{{{window},t}}=\\frac{{1}}{{{window}}}\\sum_{{i=0}}^{{{window - 1}}}C_{{t-i}}$$",
            variables={"C": "Close price"},
            caveats=[
                "SMA is lagging by construction and reacts slowly to regime changes.",
                "Equal weighting can make the estimate jump when an old extreme leaves the window.",
                "Moving-average support and resistance are empirical conventions, not structural market laws.",
            ],
        )
    if lower.startswith("ema"):
        window = trailing_number(lower, 20)
        return knowledge_block(
            short=f"Exponential moving average over {window} bars.",
            detailed="EMA smooths price while weighting recent bars more heavily than older bars.",
            theory="EMA is an infinite impulse response smoother with exponentially decaying weights. The smoothing constant gives recent closes more influence while retaining a memory of older prices. Compared with SMA, EMA reduces window-exit discontinuities and responds faster to new information, which makes it useful for trend-following and momentum-state estimation.",
            interpretation="Use fast EMAs to track short-horizon pressure and slow EMAs to define the broader drift. Crossovers indicate that recent prices have shifted enough to overcome the slower baseline, but the quality of that shift depends on range, volume, and session context. EMA200 is treated as a major long-horizon reference, so the catalog displays it with a thicker but more transparent line.",
            equation=f"$$EMA_t=\\alpha C_t+(1-\\alpha)EMA_{{t-1}},\\quad \\alpha=\\frac{{2}}{{{window}+1}}$$",
            variables={"C_t": "Close price at bar t"},
            caveats=[
                "EMA reacts faster than SMA but still lags turning points.",
                "Crossover systems can whipsaw in range-bound markets.",
                "The effective meaning of a window depends on timeframe; EMA200 on 1m bars is not the same market horizon as EMA200 on daily bars.",
            ],
        )
    if lower.startswith("tema"):
        window = trailing_number(lower, 20)
        return knowledge_block(
            short=f"Triple exponential moving average over {window} bars.",
            detailed="TEMA combines three EMA layers to reduce lag while retaining smoothing.",
            theory="TEMA combines first-, second-, and third-order EMA smoothers using a lag-correction identity. The construction attempts to keep the smoothing benefit of exponential averages while subtracting part of the delay introduced by repeated smoothing. It is therefore more responsive than a single EMA of the same nominal window, but also more sensitive to short-lived bursts.",
            interpretation="Use TEMA slope and TEMA9/TEMA20 separation as a fast momentum-state overlay. Expanding separation indicates acceleration; flattening or crossing indicates loss of short-term trend pressure. It should be confirmed with participation because fast smoothers can react strongly to isolated candles.",
            equation="$$TEMA_t=3EMA_1-3EMA_2+EMA_3$$",
            variables={"EMA_1": f"EMA(close, {window})", "EMA_2": f"EMA(EMA_1, {window})", "EMA_3": f"EMA(EMA_2, {window})"},
            caveats=[
                "Lower lag comes with higher sensitivity to noise.",
                "TEMA can overstate the importance of isolated high-range bars.",
                "Use warm-up readiness fields before trusting early-session values.",
            ],
        )
    if lower.startswith("macd"):
        return knowledge_block(
            short="Momentum oscillator based on fast and slow EMAs.",
            detailed="MACD compares a fast EMA to a slow EMA, then smooths the difference with a signal line. The histogram measures the distance between the two.",
            theory="MACD is a two-scale trend-momentum decomposition. The MACD line measures the spread between short and longer exponential trend estimates; the signal line smooths that spread; the histogram approximates the first difference between the spread and its smoother. This makes MACD most informative when trend acceleration or deceleration is more important than absolute price level.",
            interpretation="Read MACD above signal with a rising histogram as improving positive momentum. A falling histogram while both lines remain positive can warn that acceleration is fading before price has reversed. Because all three components describe one model, the catalog groups them into a single pane rather than separate independent indicators.",
            equation="$$MACD_t=EMA_{12}(C_t)-EMA_{26}(C_t)$$\n\n$$Signal_t=EMA_9(MACD_t)$$\n\n$$Hist_t=MACD_t-Signal_t$$",
            variables={"C_t": "Close price at bar t"},
            caveats=[
                "MACD is derived from moving averages, so it lags sudden reversals.",
                "Zero-line and signal-line crosses are less reliable in low-volatility ranges.",
                "Histogram color follows candle direction by default for visual consistency, but the value itself is MACD minus signal.",
            ],
        )
    if lower.startswith("rsi"):
        window = trailing_number(lower, 14)
        return knowledge_block(
            short=f"Relative Strength Index over {window} bars.",
            detailed="RSI compares rolling average positive and negative close-to-close movement on a bounded 0-100 scale. In this provider the Polars expression uses `close - close.shift(1)` by ticker, then averages positive and negative deltas over 14 bars.",
            theory="RSI is a bounded transform of the ratio between smoothed positive and negative close-to-close movement. It measures recent directional pressure after compressing the gain/loss balance into a 0-100 oscillator, which makes it useful for comparing momentum pressure across tickers and regimes.",
            interpretation="Use RSI as a regime-dependent momentum-pressure measure. In a strong breakout, RSI holding above 60 can indicate persistent demand; in a range, RSI above 70 can mark overextension. Divergence between price making a new high and RSI failing to confirm can indicate weaker marginal momentum.",
            equation=f"$$Delta_t=Close_t-Close_{{t-1}}$$\n\n$$AvgGain_t=SMA_{{{window}}}(\\max(Delta_t,0))$$\n\n$$AvgLoss_t=SMA_{{{window}}}(\\max(-Delta_t,0))$$\n\n$$RSI_t=\\begin{{cases}}100-\\frac{{100}}{{1+AvgGain_t/AvgLoss_t}},&AvgLoss_t>0\\\\100,&otherwise\\end{{cases}}$$",
            variables={"Delta_t": "Close-to-close change", "AvgGain_t": "14-bar rolling mean of positive close deltas by ticker", "AvgLoss_t": "14-bar rolling mean of negative close-delta magnitudes by ticker"},
            caveats=[
                "RSI thresholds are regime-dependent; fixed 70/30 rules are too crude for all markets.",
                "Strong trends can remain overbought or oversold for long periods.",
                "RSI ignores volume and intrabar path, so confirm with participation and structure.",
            ],
        )
    if lower.startswith("atr") or lower == "true_range":
        return knowledge_block(
            short="Volatility measure based on true range.",
            detailed="ATR smooths true range to estimate recent realized volatility.",
            theory="ATR estimates realized range volatility with a gap-aware range definition. By comparing high-low movement with high/prior-close and low/prior-close displacement, true range captures both intrabar movement and discontinuities between bars. ATR is therefore a scale variable: it helps compare movement magnitude across tickers, prices, and volatility regimes.",
            interpretation="Use ATR to normalize candle range, shock thresholds, and stop distance. A move of one dollar is not meaningful without volatility context; a move of several ATR units is more comparable across names. Rising ATR indicates expanding realized movement but does not determine direction.",
            equation="$$TR_t=max(H_t-L_t, |H_t-C_{t-1}|, |L_t-C_{t-1}|)$$\n\n$$ATR_t=SMA(TR_t,14)$$",
            variables={"H": "High", "L": "Low", "C": "Close"},
            caveats=[
                "ATR measures magnitude, not direction.",
                "Large opening gaps can dominate short-window ATR.",
                "ATR-based thresholds should be recalibrated when changing timeframe.",
            ],
        )
    if lower.startswith("bb_"):
        return knowledge_block(
            short="Bollinger Band statistic around a moving average.",
            detailed="Bollinger Bands use the 20-bar close mean as the center line and two rolling standard deviations as the upper/lower envelope. Band width normalizes the full band span by the middle band.",
            theory="Bollinger Bands combine a location estimator with a rolling dispersion estimator. The envelope expands when recent closes become more variable and contracts when price distribution narrows. The construction assumes recent variance is informative, not that prices are normally distributed; the two-standard-deviation convention is a visual volatility envelope rather than a probability guarantee.",
            interpretation="Use band width to read volatility compression and expansion. A close riding the upper band during high participation can indicate trend pressure; repeated upper-band rejection in a range can indicate exhaustion. The middle band is the local mean reference, while upper and lower bands contextualize stretch.",
            equation="$$Middle_t=SMA_{20}(Close_t)$$\n\n$$Upper_t=Middle_t+2\\sigma_{20}(Close_t)$$\n\n$$Lower_t=Middle_t-2\\sigma_{20}(Close_t)$$\n\n$$Width_t=\\frac{Upper_t-Lower_t}{Middle_t}$$",
            variables={"C": "Close price", "\\sigma": "Rolling standard deviation"},
            caveats=[
                "Band touches are not automatically reversal signals.",
                "Rolling standard deviation is sensitive to recent outliers.",
                "The envelope should be interpreted with trend state and volume confirmation.",
            ],
        )
    feature_knowledge = feature_knowledge_for_column(column, group, category, title)
    if feature_knowledge is not None:
        return feature_knowledge
    return fallback_knowledge_for_column(column, group, category, title)


def feature_knowledge_for_column(column: str, group: str, category: str, title: str) -> dict[str, Any] | None:
    lower = column.lower()
    direct = {
        "hlc3": (
            "Typical price using high, low, and close.",
            "HLC3 averages the high, low, and close of the current bar. It is less close-biased than close alone and is used by money-flow style features.",
            "$$HLC3_t=\\frac{High_t+Low_t+Close_t}{3}$$",
            {"High_t": "Current bar high", "Low_t": "Current bar low", "Close_t": "Current bar close"},
        ),
        "ohlc4": (
            "Four-price average for the current bar.",
            "OHLC4 averages open, high, low, and close. It gives a compact central price for the complete candle instead of only the last trade.",
            "$$OHLC4_t=\\frac{Open_t+High_t+Low_t+Close_t}{4}$$",
            {"Open_t": "Current bar open", "High_t": "Current bar high", "Low_t": "Current bar low", "Close_t": "Current bar close"},
        ),
        "dollar_volume": (
            "Notional traded value for the bar.",
            "Dollar volume multiplies close by share volume. It is a liquidity proxy that weights activity by price level, making a high-volume low-priced stock comparable to a lower-volume high-priced stock.",
            "$$DollarVolume_t=Close_t\\cdot Volume_t$$",
            {"Close_t": "Current bar close", "Volume_t": "Current bar share volume"},
        ),
        "return_1": (
            "One-bar simple close-to-close return.",
            "Return 1 compares the current close with the prior close for the same ticker. Null first bars are filled with zero.",
            "$$Return1_t=\\frac{Close_t}{Close_{t-1}}-1$$",
            {"Close_t": "Current close", "Close_{t-1}": "Previous bar close for the same ticker"},
        ),
        "log_return_1": (
            "One-bar log close-to-close return.",
            "Log return is the natural log of the current close divided by the prior close. It is additive across bars and useful for volatility statistics.",
            "$$LogReturn1_t=\\ln\\left(\\frac{Close_t}{Close_{t-1}}\\right)$$",
            {"Close_t": "Current close", "Close_{t-1}": "Previous bar close for the same ticker"},
        ),
        "bar_range": (
            "Full high-low candle range.",
            "Bar range measures the full price excursion inside the bar, regardless of where it opened or closed.",
            "$$Range_t=High_t-Low_t$$",
            {"High_t": "Current high", "Low_t": "Current low"},
        ),
        "body": (
            "Signed candle body.",
            "Body is close minus open. Positive values mean the candle closed above its open; negative values mean it closed below its open.",
            "$$Body_t=Close_t-Open_t$$",
            {"Close_t": "Current close", "Open_t": "Current open"},
        ),
        "body_abs": (
            "Absolute candle body size.",
            "Body absolute size removes direction and measures how much net movement occurred from open to close.",
            "$$BodyAbs_t=|Close_t-Open_t|$$",
            {"Close_t": "Current close", "Open_t": "Current open"},
        ),
        "upper_wick": (
            "Upper candle wick length.",
            "Upper wick measures how far price traded above the larger of open and close. Long upper wicks can show rejection or profit taking.",
            "$$UpperWick_t=High_t-\\max(Open_t,Close_t)$$",
            {"High_t": "Current high", "Open_t": "Current open", "Close_t": "Current close"},
        ),
        "lower_wick": (
            "Lower candle wick length.",
            "Lower wick measures how far price traded below the smaller of open and close. Long lower wicks can show absorption or dip buying.",
            "$$LowerWick_t=\\min(Open_t,Close_t)-Low_t$$",
            {"Low_t": "Current low", "Open_t": "Current open", "Close_t": "Current close"},
        ),
        "close_location": (
            "Close location inside the candle range.",
            "Close location maps the close into the high-low range. A value near 1 means the bar closed near its high; near 0 means it closed near its low. Flat bars return 0.",
            "$$CloseLocation_t=\\frac{Close_t-Low_t}{High_t-Low_t}\\quad \\text{if }High_t>Low_t\\text{ else }0$$",
            {"Close_t": "Current close", "High_t": "Current high", "Low_t": "Current low"},
        ),
        "is_green": (
            "True when close is above open.",
            "A green candle closes above its opening price. This is a directional candle-state flag, not a trend signal by itself.",
            "$$IsGreen_t=I(Close_t>Open_t)$$",
            {"I": "Indicator function"},
        ),
        "is_red": (
            "True when close is below open.",
            "A red candle closes below its opening price. This is a directional candle-state flag, not a trend signal by itself.",
            "$$IsRed_t=I(Close_t<Open_t)$$",
            {"I": "Indicator function"},
        ),
    }
    if lower in direct:
        short, detailed, equation, variables = direct[lower]
        return knowledge_block(short, detailed, theory_for_group(group, category), interpretation_for_group(group, category), equation, variables)

    if lower.startswith("day_") or lower.startswith("premarket_") or lower.startswith("or_") or lower.startswith("distance_to_day_") or lower in {"prev_close", "gap_pct"}:
        return session_feature_knowledge(lower, group, category, title)
    if lower in {"roc10", "indicator_bar_count", "macd_ready", "tema_ready"}:
        return momentum_extra_knowledge(lower, group, category, title)
    if lower.startswith("donchian_") or lower.startswith("keltner_") or lower.endswith("_z20"):
        return volatility_feature_knowledge(lower, group, category, title)
    if lower in {"volume_sma20", "dollar_volume_sma20", "transactions_sma20", "relative_volume20", "relative_dollar_volume20", "obv", "mfi14", "cmf20"} or lower.startswith("liquidity_band_") or lower in {"hvn_price_proxy20", "lvn_price_proxy20"}:
        return volume_feature_knowledge(lower, group, category, title)
    if lower in {
        "inside_bar",
        "outside_bar",
        "bullish_engulfing",
        "bearish_engulfing",
        "nr4",
        "nr7",
        "consecutive_green",
        "consecutive_red",
        "breaks_high20",
        "breaks_low20",
        "pullback_from_high20_pct",
        "reclaim_vwap",
        "breakdown_vwap",
    }:
        return price_action_knowledge(lower, group, category, title)
    if lower.endswith("_fvg") or lower.startswith("fvg_"):
        return fvg_knowledge(lower, group, category, title)
    if lower in {
        "swing_high_3",
        "swing_low_3",
        "swing_high_5",
        "swing_low_5",
        "higher_high",
        "lower_low",
        "bos_up",
        "bos_down",
        "trend_regime",
    }:
        return market_structure_knowledge(lower, group, category, title)
    if "order_block" in lower or "displacement" in lower or lower in {"distance_to_demand_pct", "distance_to_supply_pct"}:
        return order_block_knowledge(lower, group, category, title)
    if group == "shock" or "shock" in lower:
        return shock_knowledge(lower, group, category, title)
    return None


def session_feature_knowledge(lower: str, group: str, category: str, title: str) -> dict[str, Any]:
    details = {
        "day_open": (
            "First open for the ticker/session.",
            "Day open is the first open price observed for the same ticker and session_date. It anchors intraday gap and distance calculations.",
            "$$DayOpen_t=Open_{first(ticker,session)}$$",
            {"Open_{first(ticker,session)}": "First open in the same ticker/session"},
        ),
        "day_high_so_far": (
            "Session high up to the current bar.",
            "Day high so far is a cumulative maximum of high within ticker and session_date. It updates only when the session makes a new high.",
            "$$DayHighSoFar_t=\\max_{i\\le t, session_i=session_t} High_i$$",
            {"High_i": "Earlier high in the same ticker/session"},
        ),
        "day_low_so_far": (
            "Session low up to the current bar.",
            "Day low so far is a cumulative minimum of low within ticker and session_date. It updates only when the session makes a new low.",
            "$$DayLowSoFar_t=\\min_{i\\le t, session_i=session_t} Low_i$$",
            {"Low_i": "Earlier low in the same ticker/session"},
        ),
        "day_volume_so_far": (
            "Cumulative session volume.",
            "Day volume so far sums share volume from the beginning of the ticker/session through the current bar.",
            "$$DayVolumeSoFar_t=\\sum_{i\\le t, session_i=session_t} Volume_i$$",
            {"Volume_i": "Earlier volume in the same ticker/session"},
        ),
        "prev_close": (
            "Previous completed-session close.",
            "Previous close is the prior completed session close for the same ticker. It is used for session gap context and requires prior-session calculation context.",
            "$$PrevClose_t=SessionClose_{d-1}$$",
            {"SessionClose_{d-1}": "Previous session close for the same ticker"},
        ),
        "gap_pct": (
            "Session open compared with the previous session close.",
            "Gap percent is implemented as `day_open / prev_close - 1`, where `prev_close` is the previous completed session close for the ticker. The value is fixed across the current session and is null-safe when prior session context is unavailable.",
            "$$GapPct_t=\\frac{DayOpen_t}{PrevClose_t}-1$$",
            {"DayOpen_t": "First open for the ticker/session", "PrevClose_t": "Previous completed session close for the ticker"},
        ),
        "premarket_high": (
            "Highest premarket price for the session.",
            "Premarket high is the maximum high before 09:30 market time for the same ticker/session. It is used as an extended-hours breakout reference.",
            "$$PremarketHigh_t=\\max_{minute(i)<570}High_i$$",
            {"minute(i)": "Minute of day for bar i; 570 is 09:30"},
        ),
        "premarket_low": (
            "Lowest premarket price for the session.",
            "Premarket low is the minimum low before 09:30 market time for the same ticker/session. It provides downside extended-hours structure.",
            "$$PremarketLow_t=\\min_{minute(i)<570}Low_i$$",
            {"minute(i)": "Minute of day for bar i; 570 is 09:30"},
        ),
        "premarket_volume": (
            "Total premarket share volume.",
            "Premarket volume sums all share volume before 09:30 market time for the same ticker/session.",
            "$$PremarketVolume_t=\\sum_{minute(i)<570}Volume_i$$",
            {"minute(i)": "Minute of day for bar i; 570 is 09:30"},
        ),
        "premarket_range": (
            "Premarket high-low range.",
            "Premarket range measures how wide the extended-hours session traded before the regular open.",
            "$$PremarketRange_t=PremarketHigh_t-PremarketLow_t$$",
            {"PremarketHigh_t": "Highest premarket high", "PremarketLow_t": "Lowest premarket low"},
        ),
    }
    if lower in details:
        short, detailed, equation, variables = details[lower]
        return knowledge_block(short, detailed, theory_for_group(group, category), interpretation_for_group(group, category), equation, variables)
    opening_range = re.fullmatch(r"or_(\d+)m_(high|low|range)", lower)
    if opening_range:
        minutes = int(opening_range.group(1))
        field = opening_range.group(2)
        if field == "high":
            equation = f"$$OR{minutes}High_t=\\max_{{570\\le minute(i)<{570 + minutes}}}High_i$$"
            detailed = f"Opening range {minutes}m high is the maximum high from 09:30 through the first {minutes} regular-session minutes for the same ticker/session. It is null until the range has closed."
        elif field == "low":
            equation = f"$$OR{minutes}Low_t=\\min_{{570\\le minute(i)<{570 + minutes}}}Low_i$$"
            detailed = f"Opening range {minutes}m low is the minimum low from 09:30 through the first {minutes} regular-session minutes for the same ticker/session. It is null until the range has closed."
        else:
            equation = f"$$OR{minutes}Range_t=OR{minutes}High_t-OR{minutes}Low_t$$"
            detailed = f"Opening range {minutes}m range is the high-low span of the first {minutes} regular-session minutes. It is null until the range has closed."
        return knowledge_block(
            short=f"{minutes}-minute opening-range {field}.",
            detailed=detailed,
            theory=theory_for_group(group, category),
            interpretation="Opening-range levels frame early regular-session structure. Breaks above the high or below the low can mark momentum or failed-open behavior.",
            equation=equation,
            variables={"minute(i)": "Minute of day for bar i; 570 is 09:30"},
        )
    distance = re.fullmatch(r"distance_to_day_(open|high|low)_pct", lower)
    if distance:
        ref = distance.group(1)
        ref_name = {"open": "DayOpen", "high": "DayHighSoFar", "low": "DayLowSoFar"}[ref]
        return knowledge_block(
            short=f"Close distance to session {ref}.",
            detailed=f"Distance to day {ref} compares the current close with the session {ref} reference. Positive values mean close is above the reference; negative values mean close is below it.",
            theory=theory_for_group(group, category),
            interpretation="Use this as normalized price-location context. The day high and low distances show whether price is pressing an extreme or pulling back from it.",
            equation=f"$$DistanceTo{ref_name}_t=\\frac{{Close_t}}{{{ref_name}_t}}-1$$",
            variables={"Close_t": "Current close", f"{ref_name}_t": f"Session {ref} reference"},
        )
    return fallback_knowledge_for_column(lower, group, category, title)


def momentum_extra_knowledge(lower: str, group: str, category: str, title: str) -> dict[str, Any]:
    details = {
        "roc10": (
            "Ten-bar cumulative one-bar return.",
            "ROC10 is implemented as the rolling 10-bar sum of one-bar simple returns. It measures recent directional persistence in the same units as return, with zero meaning no net summed pressure.",
            "$$ROC10_t=\\sum_{i=0}^{9}Return1_{t-i}$$",
            {"Return1": "One-bar simple return"},
        ),
        "indicator_bar_count": (
            "Operational helper: bars available for indicator warm-up.",
            "Indicator bar count is an operational metadata field, not a trading indicator. The Polars pipeline computes it with `pl.cum_count(\"close\").over(\"ticker\")` after sorting by ticker and bar_time_utc. It tells the catalog and downstream review code how much history exists before trusting warm-up-sensitive indicators.",
            "$$IndicatorBarCount_t=\\operatorname{cum\\_count}_{ticker}(Close_t)$$",
            {"cum_count": "Polars cumulative count over the ticker partition after time sorting"},
        ),
        "macd_ready": (
            "Operational helper: MACD warm-up readiness flag.",
            "MACD ready is not the MACD signal and should not be interpreted as bullish or bearish. It is a data-only helper calculated in Polars as `pl.cum_count(\"close\").over(\"ticker\") >= 35`, marking rows where the EMA12, EMA26, and EMA9 signal stack has enough bars to be more stable.",
            "$$MACDReady_t=I(\\operatorname{cum\\_count}_{ticker}(Close_t)\\ge35)$$",
            {"I": "Indicator function"},
        ),
        "tema_ready": (
            "Operational helper: TEMA warm-up readiness flag.",
            "TEMA ready is not a trend signal. It is a data-only helper calculated in Polars as `pl.cum_count(\"close\").over(\"ticker\") >= 20`, marking rows where the TEMA9/TEMA20 stack has enough prior closes to avoid the earliest warm-up region.",
            "$$TEMAReady_t=I(\\operatorname{cum\\_count}_{ticker}(Close_t)\\ge20)$$",
            {"I": "Indicator function"},
        ),
    }
    short, detailed, equation, variables = details[lower]
    block = knowledge_block(short, detailed, theory_for_group(group, category), interpretation_for_group(group, category), equation, variables)
    if lower in OPERATIONAL_HELPER_COLUMNS:
        block["caveats"] = [
            "Operational helper metadata, not a tradable signal.",
            "The catalog marks this field data-only and non-selectable for chart display.",
            "Use it to understand indicator warm-up and data availability, not market direction.",
        ]
    return block


def volatility_feature_knowledge(lower: str, group: str, category: str, title: str) -> dict[str, Any]:
    if lower.startswith("donchian_"):
        field = lower.replace("donchian_", "").replace("20", "")
        equations = {
            "high": "$$DonchianHigh20_t=\\max_{i=t-19}^{t}High_i$$",
            "low": "$$DonchianLow20_t=\\min_{i=t-19}^{t}Low_i$$",
            "mid": "$$DonchianMid20_t=\\frac{DonchianHigh20_t+DonchianLow20_t}{2}$$",
        }
        return knowledge_block(
            short=f"20-bar Donchian {field}.",
            detailed=f"Donchian {field} summarizes the rolling 20-bar high-low channel for the ticker. The high and low show breakout boundaries; the mid is the channel center.",
            theory="Donchian channels expose range boundaries. A new high or low against the channel can mark breakout pressure or exhaustion depending on context.",
            interpretation="Use Donchian levels with volume and session context. A break without participation can fail quickly.",
            equation=equations.get(field, "$$Donchian_t=Channel20_t$$"),
            variables={"High_i": "Historical high", "Low_i": "Historical low"},
        )
    if lower.startswith("keltner_"):
        field = lower.replace("keltner_", "").replace("20", "")
        equations = {
            "mid": "$$KeltnerMid20_t=EMA20(Close_t)$$",
            "upper": "$$KeltnerUpper20_t=EMA20(Close_t)+2\\cdot ATR14_t$$",
            "lower": "$$KeltnerLower20_t=EMA20(Close_t)-2\\cdot ATR14_t$$",
        }
        return knowledge_block(
            short=f"Keltner channel {field}.",
            detailed=f"Keltner {field} is part of an ATR-based envelope around EMA20. It adapts to volatility through ATR rather than close standard deviation.",
            theory="Keltner channels combine trend location and realized range. Expansions beyond the channel can indicate momentum, while returns inside the channel can indicate normalization.",
            interpretation="Use Keltner channels to compare price stretch against recent true range.",
            equation=equations.get(field, "$$Keltner_t=EMA20_t\\pm 2ATR14_t$$"),
            variables={"EMA20": "20-bar exponential moving average of close", "ATR14": "14-bar average true range"},
        )
    z_source = {
        "return_z20": ("Return1", "one-bar return"),
        "range_z20": ("Range", "bar range"),
        "volume_z20": ("Volume", "share volume"),
        "transactions_z20": ("Transactions", "transaction count"),
    }.get(lower)
    if z_source:
        symbol, description = z_source
        return knowledge_block(
            short=f"20-bar z-score of {description}.",
            detailed=f"{title} standardizes current {description} versus its own 20-bar rolling mean and standard deviation for the same ticker. The Polars helper returns 0 when the rolling standard deviation is not positive. Positive values mean the current bar is above recent normal; negative values mean below recent normal.",
            theory="Z-scores normalize activity across tickers and regimes. They make abnormal movement or participation easier to compare than raw values.",
            interpretation="Values above 2 to 3 are unusually high. Confirm whether the abnormality is useful by checking direction, volume, and session context.",
            equation=f"$$Z20_t=\\begin{{cases}}\\frac{{{symbol}_t-Mean20({symbol})_t}}{{Std20({symbol})_t}},&Std20({symbol})_t>0\\\\0,&otherwise\\end{{cases}}$$",
            variables={f"{symbol}_t": f"Current {description}", "Mean20": "20-bar rolling mean", "Std20": "20-bar rolling standard deviation"},
        )
    return fallback_knowledge_for_column(lower, group, category, title)


def volume_feature_knowledge(lower: str, group: str, category: str, title: str) -> dict[str, Any]:
    rolling = {
        "volume_sma20": ("Volume", "share volume"),
        "dollar_volume_sma20": ("DollarVolume", "dollar volume"),
        "transactions_sma20": ("Transactions", "transaction count"),
    }
    if lower in rolling:
        symbol, description = rolling[lower]
        return knowledge_block(
            short=f"20-bar average {description}.",
            detailed=f"{title} is the 20-bar rolling average of {description} for the same ticker. It defines the local baseline used by relative activity features.",
            theory=theory_for_group(group, category),
            interpretation="Use the rolling average as a baseline, not as a signal by itself. Its usefulness increases when paired with current activity and price movement.",
            equation=f"$$SMA20({symbol})_t=\\frac{{1}}{{20}}\\sum_{{i=0}}^{{19}}{symbol}_{{t-i}}$$",
            variables={symbol: description},
        )
    relative = {
        "relative_volume20": ("Volume", "VolumeSMA20"),
        "relative_dollar_volume20": ("DollarVolume", "DollarVolumeSMA20"),
    }
    if lower in relative:
        numerator, denominator = relative[lower]
        return knowledge_block(
            short="Current activity divided by its 20-bar baseline.",
            detailed=f"{title} compares current {numerator.lower()} with its 20-bar moving average. The Polars expression returns 0 when the denominator baseline is not positive. A value of 3 means the current bar is trading at roughly three times its recent average activity.",
            theory=theory_for_group(group, category),
            interpretation="High relative activity is more meaningful when price is also moving directionally or breaking structure.",
            equation=f"$$Relative{numerator}20_t=\\begin{{cases}}\\frac{{{numerator}_t}}{{{denominator}_t}},&{denominator}_t>0\\\\0,&otherwise\\end{{cases}}$$",
            variables={numerator: "Current activity", denominator: "20-bar activity baseline"},
        )
    details = {
        "obv": (
            "On-balance volume running total.",
            "OBV adds volume when close is at least the prior close and subtracts volume otherwise. It is a cumulative participation-pressure proxy.",
            "$$OBV_t=OBV_{t-1}+\\begin{cases}Volume_t,&Close_t\\ge Close_{t-1}\\\\-Volume_t,&Close_t<Close_{t-1}\\end{cases}$$",
            {"Volume_t": "Current share volume"},
        ),
        "mfi14": (
            "Money Flow Index over 14 bars.",
            "MFI is a bounded oscillator that compares positive and negative typical-money-flow over 14 bars. It is RSI-like, but volume weighted.",
            "$$MFI14_t=100-\\frac{100}{1+\\frac{PositiveFlow14_t}{NegativeFlow14_t}}$$",
            {"PositiveFlow14": "14-bar sum of HLC3*Volume when HLC3 rises", "NegativeFlow14": "14-bar sum when HLC3 falls"},
        ),
        "cmf20": (
            "Chaikin Money Flow over 20 bars.",
            "CMF weights volume by where the close lands inside the candle range and sums that pressure over 20 bars.",
            "$$CMF20_t=\\frac{\\sum_{i=t-19}^{t}MFM_i\\cdot Volume_i}{\\sum_{i=t-19}^{t}Volume_i}$$\n\n$$MFM_i=\\frac{(Close_i-Low_i)-(High_i-Close_i)}{High_i-Low_i}$$",
            {"MFM": "Money flow multiplier"},
        ),
        "hvn_price_proxy20": (
            "High-volume-node price proxy.",
            "This provider proxy uses the 20-bar rolling mean of close as a lightweight reference for where recent trading has centered.",
            "$$HVNProxy20_t=SMA20(Close_t)$$",
            {"Close_t": "Close price"},
        ),
        "lvn_price_proxy20": (
            "Low-volume-node price proxy.",
            "This provider proxy uses the 20-bar rolling median of close as a robust recent price reference that is less sensitive to outlier bars.",
            "$$LVNProxy20_t=Median20(Close_t)$$",
            {"Close_t": "Close price"},
        ),
    }
    if lower in details:
        short, detailed, equation, variables = details[lower]
        return knowledge_block(short, detailed, theory_for_group(group, category), interpretation_for_group(group, category), equation, variables)
    liquidity = re.fullmatch(r"liquidity_band_(\d+)bp_volume", lower)
    if liquidity:
        window = {"25": 20, "50": 50, "100": 100}.get(liquidity.group(1), int(liquidity.group(1)))
        return knowledge_block(
            short=f"Rolling {window}-bar volume capacity proxy.",
            detailed=f"{title} is named as a liquidity-band proxy and is implemented as rolling share volume over {window} bars. It is meant to approximate available participation depth over a recent window.",
            theory=theory_for_group(group, category),
            interpretation="Use as a rough capacity context. It is not order-book depth and should not be treated as exact executable liquidity.",
            equation=f"$$LiquidityBandVolume_t=\\sum_{{i=0}}^{{{window - 1}}}Volume_{{t-i}}$$",
            variables={"Volume": "Share volume"},
        )
    return fallback_knowledge_for_column(lower, group, category, title)


def price_action_knowledge(lower: str, group: str, category: str, title: str) -> dict[str, Any]:
    details = {
        "inside_bar": ("Inside bar.", "Inside bar is true when the current high is below the prior high and the current low is above the prior low. It marks compression inside the previous bar.", "$$Inside_t=I(High_t<High_{t-1}\\land Low_t>Low_{t-1})$$"),
        "outside_bar": ("Outside bar.", "Outside bar is true when the current high exceeds the prior high and the current low breaks the prior low. It marks expansion beyond the previous bar.", "$$Outside_t=I(High_t>High_{t-1}\\land Low_t<Low_{t-1})$$"),
        "bullish_engulfing": ("Bullish engulfing candle.", "Bullish engulfing is true when the candle closes green and its body crosses above the prior candle body range.", "$$BullEngulf_t=I(Close_t>Open_t\\land Open_t<Close_{t-1}\\land Close_t>Open_{t-1})$$"),
        "bearish_engulfing": ("Bearish engulfing candle.", "Bearish engulfing is true when the candle closes red and its body crosses below the prior candle body range.", "$$BearEngulf_t=I(Close_t<Open_t\\land Open_t>Close_{t-1}\\land Close_t<Open_{t-1})$$"),
        "nr4": ("Narrowest range in four bars.", "NR4 is true when the current high-low range is less than or equal to the rolling 4-bar minimum range. It identifies short compression.", "$$NR4_t=I(Range_t\\le \\min_{i=t-3}^{t}Range_i)$$"),
        "nr7": ("Narrowest range in seven bars.", "NR7 is true when the current high-low range is less than or equal to the rolling 7-bar minimum range. It identifies stronger compression.", "$$NR7_t=I(Range_t\\le \\min_{i=t-6}^{t}Range_i)$$"),
        "consecutive_green": ("Current green-candle streak.", "This field counts consecutive green candles and resets to zero when a candle is not green.", "$$ConsecutiveGreen_t=I(Green_t)(ConsecutiveGreen_{t-1}+1)$$"),
        "consecutive_red": ("Current red-candle streak.", "This field counts consecutive red candles and resets to zero when a candle is not red.", "$$ConsecutiveRed_t=I(Red_t)(ConsecutiveRed_{t-1}+1)$$"),
        "breaks_high20": ("Breaks the prior 20-bar high.", "Breaks high20 is true when the current high exceeds the highest high from the prior 20 bars, excluding the current bar from the reference.", "$$BreaksHigh20_t=I(High_t>\\max_{i=t-20}^{t-1}High_i)$$"),
        "breaks_low20": ("Breaks the prior 20-bar low.", "Breaks low20 is true when the current low breaks below the lowest low from the prior 20 bars, excluding the current bar from the reference.", "$$BreaksLow20_t=I(Low_t<\\min_{i=t-20}^{t-1}Low_i)$$"),
        "pullback_from_high20_pct": ("Percent distance from Donchian high.", "Pullback from high20 compares close with the 20-bar Donchian high. Negative values show how far price has pulled back from the recent high.", "$$PullbackHigh20_t=\\frac{Close_t}{DonchianHigh20_t}-1$$"),
        "reclaim_vwap": ("VWAP reclaim event.", "Reclaim VWAP is true when close moves from at or below VWAP on the prior bar to above VWAP on the current bar.", "$$ReclaimVWAP_t=I(Close_t>VWAP_t\\land Close_{t-1}\\le VWAP_{t-1})$$"),
        "breakdown_vwap": ("VWAP breakdown event.", "Breakdown VWAP is true when close moves from at or above VWAP on the prior bar to below VWAP on the current bar.", "$$BreakdownVWAP_t=I(Close_t<VWAP_t\\land Close_{t-1}\\ge VWAP_{t-1})$$"),
    }
    short, detailed, equation = details[lower]
    return knowledge_block(short, detailed, theory_for_group(group, category), interpretation_for_group(group, category), equation, {"I": "Indicator function"})


def fvg_knowledge(lower: str, group: str, category: str, title: str) -> dict[str, Any]:
    details = {
        "bullish_fvg": ("Bullish fair value gap.", "Bullish FVG is true when the current low is above the high from two bars ago. It marks a three-candle displacement gap where price did not trade back into the earlier high.", "$$BullishFVG_t=I(Low_t>High_{t-2})$$"),
        "bearish_fvg": ("Bearish fair value gap.", "Bearish FVG is true when the current high is below the low from two bars ago. It marks a three-candle displacement gap to the downside.", "$$BearishFVG_t=I(High_t<Low_{t-2})$$"),
        "fvg_high": ("Upper boundary of the fair value gap.", "FVG high is the upper price boundary of the detected gap. For bullish gaps it is the current low; for bearish gaps it is the low from two bars ago.", "$$FVGHigh_t=\\begin{cases}Low_t,&BullishFVG_t\\\\Low_{t-2},&BearishFVG_t\\end{cases}$$"),
        "fvg_low": ("Lower boundary of the fair value gap.", "FVG low is the lower price boundary of the detected gap. For bullish gaps it is the high from two bars ago; for bearish gaps it is the current high.", "$$FVGLow_t=\\begin{cases}High_{t-2},&BullishFVG_t\\\\High_t,&BearishFVG_t\\end{cases}$$"),
        "fvg_mid": ("Middle of the fair value gap.", "FVG mid is the midpoint between FVG high and FVG low. It is useful as a compact reference for gap mitigation.", "$$FVGMid_t=\\frac{FVGHigh_t+FVGLow_t}{2}$$"),
        "fvg_size": ("Absolute fair value gap size.", "FVG size is the absolute distance between the gap boundaries.", "$$FVGSize_t=|FVGHigh_t-FVGLow_t|$$"),
        "fvg_size_pct": ("Fair value gap size normalized by close.", "FVG size percent divides absolute gap size by close so gaps can be compared across price levels.", "$$FVGSizePct_t=\\frac{|FVGHigh_t-FVGLow_t|}{Close_t}$$"),
    }
    short, detailed, equation = details[lower]
    return knowledge_block(short, detailed, theory_for_group(group, category), interpretation_for_group(group, category), equation, {"I": "Indicator function"})


def market_structure_knowledge(lower: str, group: str, category: str, title: str) -> dict[str, Any]:
    details = {
        "swing_high_3": ("Causal 3-bar local high.", "True when the current high is at least the highest high in the current and prior two bars. It does not use future bars.", "$$SwingHigh3_t=I(High_t\\ge \\max_{i=t-2}^{t}High_i)$$"),
        "swing_low_3": ("Causal 3-bar local low.", "True when the current low is at most the lowest low in the current and prior two bars. It does not use future bars.", "$$SwingLow3_t=I(Low_t\\le \\min_{i=t-2}^{t}Low_i)$$"),
        "swing_high_5": ("Causal 5-bar local high.", "True when the current high is at least the highest high in the current and prior four bars. It does not use future bars.", "$$SwingHigh5_t=I(High_t\\ge \\max_{i=t-4}^{t}High_i)$$"),
        "swing_low_5": ("Causal 5-bar local low.", "True when the current low is at most the lowest low in the current and prior four bars. It does not use future bars.", "$$SwingLow5_t=I(Low_t\\le \\min_{i=t-4}^{t}Low_i)$$"),
        "higher_high": ("Higher high versus prior bar.", "True when the current high is above the previous bar high.", "$$HigherHigh_t=I(High_t>High_{t-1})$$"),
        "lower_low": ("Lower low versus prior bar.", "True when the current low is below the previous bar low.", "$$LowerLow_t=I(Low_t<Low_{t-1})$$"),
        "bos_up": ("Bullish break of structure.", "True when close breaks above the prior 20-bar high, excluding the current bar from the reference.", "$$BOSUp_t=I(Close_t>\\max_{i=t-20}^{t-1}High_i)$$"),
        "bos_down": ("Bearish break of structure.", "True when close breaks below the prior 20-bar low, excluding the current bar from the reference.", "$$BOSDown_t=I(Close_t<\\min_{i=t-20}^{t-1}Low_i)$$"),
        "trend_regime": ("EMA20 versus EMA50 regime label.", "Trend regime is 'up' when EMA20 is above EMA50, 'down' when EMA20 is below EMA50, and 'range' otherwise.", "$$Regime_t=\\begin{cases}up,&EMA20_t>EMA50_t\\\\down,&EMA20_t<EMA50_t\\\\range,&otherwise\\end{cases}$$"),
    }
    short, detailed, equation = details[lower]
    return knowledge_block(short, detailed, theory_for_group(group, category), interpretation_for_group(group, category), equation, {"I": "Indicator function"})


def order_block_knowledge(lower: str, group: str, category: str, title: str) -> dict[str, Any]:
    details = {
        "bullish_displacement": ("Bullish displacement candle.", "True when the bar range is greater than 1.5 times ATR14 and close is above open. It marks a large directional expansion bar.", "$$BullDisp_t=I(Range_t>1.5\\cdot ATR14_t\\land Close_t>Open_t)$$"),
        "bearish_displacement": ("Bearish displacement candle.", "True when the bar range is greater than 1.5 times ATR14 and close is below open. It marks a large downside expansion bar.", "$$BearDisp_t=I(Range_t>1.5\\cdot ATR14_t\\land Close_t<Open_t)$$"),
        "bullish_order_block_high": ("Prior high after bullish displacement.", "When bullish displacement is true, this stores the prior bar high as the upper boundary of a simple demand-zone proxy.", "$$BullOBHigh_t=High_{t-1}\\quad\\text{if }BullDisp_t$$"),
        "bullish_order_block_low": ("Prior low after bullish displacement.", "When bullish displacement is true, this stores the prior bar low as the lower boundary of a simple demand-zone proxy.", "$$BullOBLow_t=Low_{t-1}\\quad\\text{if }BullDisp_t$$"),
        "bearish_order_block_high": ("Prior high after bearish displacement.", "When bearish displacement is true, this stores the prior bar high as the upper boundary of a simple supply-zone proxy.", "$$BearOBHigh_t=High_{t-1}\\quad\\text{if }BearDisp_t$$"),
        "bearish_order_block_low": ("Prior low after bearish displacement.", "When bearish displacement is true, this stores the prior bar low as the lower boundary of a simple supply-zone proxy.", "$$BearOBLow_t=Low_{t-1}\\quad\\text{if }BearDisp_t$$"),
        "distance_to_demand_pct": ("Close distance to demand proxy.", "Distance to demand compares close with the bullish order block high. Positive values mean close is above the demand proxy boundary.", "$$DistanceDemand_t=\\frac{Close_t}{BullOBHigh_t}-1$$"),
        "distance_to_supply_pct": ("Close distance to supply proxy.", "Distance to supply compares close with the bearish order block low. Positive values mean close is above the supply proxy boundary.", "$$DistanceSupply_t=\\frac{Close_t}{BearOBLow_t}-1$$"),
    }
    short, detailed, equation = details[lower]
    return knowledge_block(short, detailed, theory_for_group(group, category), interpretation_for_group(group, category), equation, {"I": "Indicator function"})


def shock_knowledge(lower: str, group: str, category: str, title: str) -> dict[str, Any]:
    details = {
        "return_shock": ("Positive return z-score shock.", "True when return_z20 is at least 2.5 and the one-bar return is positive. It captures unusually large positive close-to-close movement.", "$$ReturnShock_t=I(ReturnZ20_t\\ge2.5\\land Return1_t>0)$$"),
        "range_shock": ("Positive range expansion shock.", "True when range_z20 is at least 2.5 and the candle body is positive. It captures unusually large bullish range expansion.", "$$RangeShock_t=I(RangeZ20_t\\ge2.5\\land Body_t>0)$$"),
        "structure_break_shock": ("Breakout structure shock.", "True when price breaks one of the provider's structure references: the rolling 20-bar high, the previous bar's session-high-so-far, premarket high, 5-minute opening range high, or VWAP reclaim. The Polars code names the previous running session high `prior_day_high`, but it is computed as `day_high_so_far.shift(1).over([ticker, session_date])`, not yesterday's completed high.", "$$StructureBreakShock_t=BreaksHigh20_t\\lor (Close_t>DayHighSoFar_{t-1,session})\\lor (Close_t>PremarketHigh_t)\\lor (Close_t>OR5High_t)\\lor ReclaimVWAP_t$$"),
        "price_shock": ("Confirmed price-side shock flag.", "True when return, range, displacement, or structure shock occurs and the close is in the upper part of the bar range.", "$$PriceShock_t=I((ReturnShock_t\\lor RangeShock_t\\lor BullDisp_t\\lor StructureBreakShock_t)\\land CloseLocation_t\\ge0.55)$$"),
        "relative_volume_shock": ("Relative volume shock.", "True when current volume is at least three times its 20-bar average.", "$$RelativeVolumeShock_t=I(RelativeVolume20_t\\ge3)$$"),
        "dollar_volume_shock": ("Relative dollar-volume shock.", "True when current dollar volume is at least three times its 20-bar average.", "$$DollarVolumeShock_t=I(RelativeDollarVolume20_t\\ge3)$$"),
        "transactions_shock": ("Transaction-count shock.", "True when transactions_z20 is at least 2.5.", "$$TransactionsShock_t=I(TransactionsZ20_t\\ge2.5)$$"),
        "volume_shock": ("Volume-side shock flag.", "True when relative volume, relative dollar volume, transaction count, or volume_z20 shows abnormal participation.", "$$VolumeShock_t=RelativeVolumeShock_t\\lor DollarVolumeShock_t\\lor TransactionsShock_t\\lor I(VolumeZ20_t\\ge2.5)$$"),
        "price_shock_score": ("Weighted price shock score.", "Score from 0 to 1 combining clipped return abnormality, clipped range abnormality, close location, structure break, and bullish displacement. The Polars code divides positive z-scores by 5 before applying weights.", "$$PriceShockScore_t=\\min\\left(1,0.30\\frac{\\max(ReturnZ20_t,0)}{5}+0.25\\frac{\\max(RangeZ20_t,0)}{5}+0.15clip(CloseLocation_t,0,1)+0.15I(StructureBreakShock_t)+0.15I(BullishDisplacement_t)\\right)$$"),
        "volume_shock_score": ("Weighted volume shock score.", "Score from 0 to 1 combining clipped volume z-score, capped relative volume, capped relative dollar volume, and clipped transaction z-score. The Polars code caps relative ratios at 5 and scales z-scores by 5.", "$$VolumeShockScore_t=\\min\\left(1,0.30\\frac{\\max(VolumeZ20_t,0)}{5}+0.25\\frac{\\min(RelativeVolume20_t,5)}{5}+0.25\\frac{\\min(RelativeDollarVolume20_t,5)}{5}+0.20\\frac{\\max(TransactionsZ20_t,0)}{5}\\right)$$"),
        "bars_since_price_shock": ("Bars since last price shock.", "Count of bars since the most recent price_shock for the ticker. Null before the first price shock.", "$$BarsSincePriceShock_t=BarSeq_t-LastPriceShockSeq_t$$"),
        "bars_since_volume_shock": ("Bars since last volume shock.", "Count of bars since the most recent volume_shock for the ticker. Null before the first volume shock.", "$$BarsSinceVolumeShock_t=BarSeq_t-LastVolumeShockSeq_t$$"),
        "minutes_since_price_shock": ("Minutes since last price shock.", "Bars since price shock multiplied by the timeframe step in minutes.", "$$MinutesSincePriceShock_t=BarsSincePriceShock_t\\cdot TimeframeStepMinutes_t$$"),
        "minutes_since_volume_shock": ("Minutes since last volume shock.", "Bars since volume shock multiplied by the timeframe step in minutes.", "$$MinutesSinceVolumeShock_t=BarsSinceVolumeShock_t\\cdot TimeframeStepMinutes_t$$"),
        "price_shock_recent": ("Price shock recency flag.", "True when the last price shock happened within the current bar through 15 bars ago.", "$$PriceShockRecent_t=I(0\\le BarsSincePriceShock_t\\le15)$$"),
        "volume_shock_recent": ("Volume shock recency flag.", "True when the last volume shock happened within the current bar through 15 bars ago.", "$$VolumeShockRecent_t=I(0\\le BarsSinceVolumeShock_t\\le15)$$"),
        "price_shock_before_volume_shock": ("Price-first volume confirmation flag.", "True when the current bar has a volume shock and a price shock happened 1 to 15 bars earlier.", "$$PriceBeforeVolume_t=I(VolumeShock_t\\land 0<BarsSincePriceShock_t\\le15)$$"),
        "confirmed_price_volume_shock": ("Price and volume shock confirmation.", "True when a volume shock occurs while a price shock is recent. This captures the sequence-aware idea that price displacement can lead and volume can confirm shortly afterward.", "$$ConfirmedPVS_t=I(VolumeShock_t\\land PriceShockRecent_t)$$"),
        "shock_confirmation_delay_minutes": ("Delay from price shock to confirmation.", "When price-volume shock is confirmed, this stores minutes since the price shock. Null when there is no confirmation.", "$$DelayMinutes_t=MinutesSincePriceShock_t\\quad\\text{if }ConfirmedPVS_t$$"),
        "shock_confirmation_type": ("Categorical shock sequence label.", "Classifies shock sequence as same-bar, price-first immediate, price-first delayed, volume-first breakout, price-only, volume-only, or none.", "$$Type_t=case(PriceShock_t,VolumeShock_t,BarsSincePriceShock_t,BarsSinceVolumeShock_t)$$"),
        "price_volume_shock_score": ("Combined price-volume shock score.", "Score from 0 to 1 combining price score, volume score, and a recency bonus when price-volume confirmation occurs quickly.", "$$PVSScore_t=\\min(1,0.45PriceScore_t+0.45VolumeScore_t+0.10ConfirmationRecency_t)$$"),
    }
    if lower in details:
        short, detailed, equation = details[lower]
        return knowledge_block(short, detailed, theory_for_group(group, category), "Higher values or true flags indicate more unusual movement, stronger participation, or better price-volume confirmation. Sequence fields should be read from left to right: first detect price abnormality, then check whether volume confirmed it soon enough.", equation, {"I": "Indicator function"})
    return fallback_knowledge_for_column(lower, group, category, title)


def supervision_knowledge_for_column(column: str, group: str, title: str) -> dict[str, Any]:
    lower = column.lower()
    horizon_details = {
        "horizon": ("Future horizon label.", "String label for the future bar window used by a bar-level supervision row, such as 3bar or 15bar.", "$$HorizonLabel_t=str(horizon\\_bars)$$"),
        "horizon_bars": ("Future horizon length in bars.", "Number of future bars included in the bar-level supervision path.", "$$HorizonBars_t=h$$"),
        "horizon_minutes": ("Future horizon length in minutes.", "Future horizon converted to minutes by multiplying horizon bars by the timeframe step.", "$$HorizonMinutes_t=h\\cdot StepMinutes_t$$"),
        "future_bar_count": ("Available future bars.", "Actual count of non-null future closes available inside the horizon. It can be smaller near the end of a ticker/session sequence.", "$$FutureBarCount_t=count(Close_{t+1:t+h})$$"),
        "valid_future_window": ("Whether any future bars exist.", "True when at least one future bar is available for the requested horizon.", "$$ValidFutureWindow_t=I(FutureBarCount_t>0)$$"),
    }
    if lower in horizon_details:
        short, detailed, equation = horizon_details[lower]
        return supervision_block(short, detailed, equation, {"h": "Horizon in bars", "StepMinutes_t": "Timeframe size in minutes"})
    path_details = {
        "fwd_close_return": ("Future close return.", "Return from current close to the last available future close inside the horizon.", "$$FwdCloseReturn_t=\\frac{Close_{t+h}}{Close_t}-1$$"),
        "fwd_high_return": ("Best future high return.", "Maximum favorable excursion measured from current close to the highest future high in the horizon.", "$$FwdHighReturn_t=\\max_{1\\le i\\le h}\\left(\\frac{High_{t+i}}{Close_t}-1\\right)$$"),
        "fwd_low_return": ("Worst future low return.", "Maximum adverse excursion measured from current close to the lowest future low in the horizon.", "$$FwdLowReturn_t=\\min_{1\\le i\\le h}\\left(\\frac{Low_{t+i}}{Close_t}-1\\right)$$"),
        "fwd_mfe": ("Maximum favorable excursion.", "Alias of future high return. It is the best upside reached after the source bar within the horizon.", "$$MFE_t=\\max_{1\\le i\\le h}\\left(\\frac{High_{t+i}}{Close_t}-1\\right)$$"),
        "fwd_mae": ("Maximum adverse excursion.", "Alias of future low return. It is the worst downside reached after the source bar within the horizon.", "$$MAE_t=\\min_{1\\le i\\le h}\\left(\\frac{Low_{t+i}}{Close_t}-1\\right)$$"),
        "fwd_mfe_to_mae_ratio": ("Reward-to-adverse-excursion ratio.", "MFE divided by the absolute value of MAE. Higher values mean upside dominated downside inside the future path.", "$$MFEToMAE_t=\\frac{MFE_t}{|MAE_t|}$$"),
        "time_to_mfe_bars": ("Bars until best future high.", "Position of the future bar that achieved MFE, counted from 1.", "$$TimeToMFEbars_t=argmax(High_{t+1:t+h})+1$$"),
        "time_to_mae_bars": ("Bars until worst future low.", "Position of the future bar that achieved MAE, counted from 1.", "$$TimeToMAEbars_t=argmin(Low_{t+1:t+h})+1$$"),
        "time_to_mfe_minutes": ("Minutes until best future high.", "Bars until MFE multiplied by the timeframe step in minutes.", "$$TimeToMFEminutes_t=TimeToMFEbars_t\\cdot StepMinutes_t$$"),
        "time_to_mae_minutes": ("Minutes until worst future low.", "Bars until MAE multiplied by the timeframe step in minutes.", "$$TimeToMAEminutes_t=TimeToMAEbars_t\\cdot StepMinutes_t$$"),
        "mfe_before_mae": ("Whether upside arrived before downside.", "True when the best future high occurs before or at the same future index as the worst future low.", "$$MFEBeforeMAE_t=I(TimeToMFEbars_t\\le TimeToMAEbars_t)$$"),
        "path_efficiency": ("Efficiency of the future path.", "Path efficiency measures how directly the future close path traveled toward the best high. Higher values mean less wasted movement before the best outcome.", "$$PathEfficiency_t=\\frac{BestHigh_t-Close_t}{\\sum |\\Delta Close_{future}|}$$"),
        "green_bar_ratio": ("Share of future bars closing green.", "Fraction of available future bars where close is greater than open.", "$$GreenBarRatio_t=\\frac{1}{n}\\sum_{i=1}^{n}I(Close_{t+i}>Open_{t+i})$$"),
    }
    if lower in path_details:
        short, detailed, equation = path_details[lower]
        return supervision_block(short, detailed, equation, {"h": "Horizon in bars", "n": "Available future bar count", "I": "Indicator function"})
    oracle_details = {
        "oracle_best_exit_bar_id": ("Bar id of the best future exit.", "Identifier of the future bar where the highest high inside the horizon occurred.", "$$BestExitBarId_t=BarId_{t+argmax(High_{t+1:t+h})}$$"),
        "oracle_best_exit_time_utc": ("UTC time of the best future exit.", "UTC timestamp of the future bar where the highest high inside the horizon occurred.", "$$BestExitTimeUTC_t=TimeUTC_{t+argmax(High_{t+1:t+h})}$$"),
        "oracle_best_exit_price": ("Best future exit price.", "Highest future high inside the horizon.", "$$BestExitPrice_t=\\max_{1\\le i\\le h}High_{t+i}$$"),
        "oracle_best_exit_return": ("Best future exit return.", "Return from current close to the best future exit price.", "$$BestExitReturn_t=\\frac{BestExitPrice_t}{Close_t}-1$$"),
        "oracle_long_entry_signal": ("Oracle long entry label.", "True when the future path achieved at least 1% MFE, had no worse than 0.5% MAE, and MFE occurred before or with MAE.", "$$Entry_t=I(MFE_t\\ge0.01\\land |MAE_t|\\le0.005\\land MFEBeforeMAE_t)$$"),
        "oracle_long_entry_confidence": ("Oracle long entry confidence.", "Bounded path-quality score derived from favorable return, adverse excursion, and path efficiency. In code, `Q` is `_bounded(best_return * 20 * 0.45 + efficiency * 0.35 + (1 - min(abs(mae) * 20, 1)) * 0.20)`.", "$$EntryConfidence_t=clip\\left(20MFE_t\\cdot0.45+PathEfficiency_t\\cdot0.35+(1-\\min(20|MAE_t|,1))\\cdot0.20,0,1\\right)$$"),
        "oracle_long_exit_signal": ("Oracle long exit label.", "True when the best upside is less than or equal to the absolute downside risk inside the future path.", "$$Exit_t=I(MFE_t\\le |MAE_t|)$$"),
        "oracle_long_exit_confidence": ("Oracle long exit confidence.", "Bounded inverse-path score calculated with the same quality function but using adverse movement as the reward term, negative MFE as the risk term, and `1 - path_efficiency`.", "$$ExitConfidence_t=clip\\left(20|MAE_t|\\cdot0.45+(1-PathEfficiency_t)\\cdot0.35+(1-\\min(20|MFE_t|,1))\\cdot0.20,0,1\\right)$$"),
    }
    if lower in oracle_details:
        short, detailed, equation = oracle_details[lower]
        return supervision_block(short, detailed, equation, {"Q": "Provider bounded quality function", "I": "Indicator function"})
    if lower.startswith("fwd_"):
        return forward_liquidity_knowledge(lower, title)
    if lower.startswith("method_") or lower in {"trade_method", "oracle_action"}:
        return method_label_knowledge(lower, title)
    if lower in {
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
    }:
        return method_shock_label_knowledge(lower, title)
    if group == "supervision_scanner" or lower in {"universe_size", "oracle_rank", "oracle_percentile", "is_top_1", "is_top_3", "is_top_5", "is_top_10", "is_top_1pct", "is_top_5pct"}:
        return scanner_label_knowledge(lower, title)
    return supervision_block(
        short=f"{title} future-looking supervision field.",
        detailed=f"{title} is a supervision value derived from the future bar path for research, chart review, and model-target construction. It should be interpreted with the horizon and method columns in the same row.",
        equation=f"$$\\text{{{title}}}_t=SupervisionValue_t$$",
        variables={"SupervisionValue_t": "Provider supervision output for the current row"},
    )


def forward_liquidity_knowledge(lower: str, title: str) -> dict[str, Any]:
    details = {
        "fwd_volume_sum": ("Future volume sum.", "Total share volume across available future bars in the horizon.", "$$FwdVolumeSum_t=\\sum_{i=1}^{h}Volume_{t+i}$$"),
        "fwd_dollar_volume_sum": ("Future dollar-volume sum.", "Total close times volume across available future bars in the horizon.", "$$FwdDollarVolumeSum_t=\\sum_{i=1}^{h}Close_{t+i}\\cdot Volume_{t+i}$$"),
        "fwd_transactions_sum": ("Future transaction-count sum.", "Total transaction count across future bars in the horizon.", "$$FwdTransactionsSum_t=\\sum_{i=1}^{h}Transactions_{t+i}$$"),
        "fwd_max_volume": ("Maximum future bar volume.", "Largest share volume observed in any future bar inside the horizon.", "$$FwdMaxVolume_t=\\max_{1\\le i\\le h}Volume_{t+i}$$"),
        "fwd_max_dollar_volume": ("Maximum future dollar volume.", "Largest close times volume observed in any future bar inside the horizon.", "$$FwdMaxDollarVolume_t=\\max_{1\\le i\\le h}(Close_{t+i}\\cdot Volume_{t+i})$$"),
        "fwd_max_relative_volume20": ("Maximum future relative volume.", "Maximum relative_volume20 observed inside the future horizon.", "$$FwdMaxRelVol20_t=\\max_{1\\le i\\le h}RelativeVolume20_{t+i}$$"),
        "fwd_max_relative_dollar_volume20": ("Maximum future relative dollar volume.", "Maximum relative_dollar_volume20 observed inside the future horizon.", "$$FwdMaxRelDollarVol20_t=\\max_{1\\le i\\le h}RelativeDollarVolume20_{t+i}$$"),
        "fwd_max_volume_z20": ("Maximum future volume z-score.", "Maximum volume_z20 observed inside the future horizon.", "$$FwdMaxVolumeZ20_t=\\max_{1\\le i\\le h}VolumeZ20_{t+i}$$"),
        "fwd_volume_expansion_ratio": ("Future max-volume expansion ratio.", "Largest single future-bar volume divided by current bar volume. The Polars code uses `_safe_ratio(_max_volume, volume)`, so this is a peak future participation ratio, not the future volume sum divided by current volume.", "$$FwdVolumeExpansion_t=\\frac{\\max_{1\\le i\\le h}Volume_{t+i}}{Volume_t}$$"),
        "fwd_dollar_volume_expansion_ratio": ("Future max-dollar-volume expansion ratio.", "Largest single future-bar dollar volume divided by current dollar volume. The Polars code uses `_safe_ratio(_max_dollar_volume, dollar_volume)`, so this is a peak future notional participation ratio.", "$$FwdDollarVolumeExpansion_t=\\frac{\\max_{1\\le i\\le h}(Close_{t+i}\\cdot Volume_{t+i})}{DollarVolume_t}$$"),
        "fwd_liquidity_confirmed": ("Future liquidity confirmation flag.", "True when at least one volume shock appears in the future horizon.", "$$FwdLiquidityConfirmed_t=I(\\exists i\\le h:VolumeShock_{t+i})$$"),
        "fwd_first_volume_shock_bar_id": ("First future volume-shock bar id.", "Bar id of the first future bar in the horizon where volume_shock is true.", "$$FirstVolumeShockBarId_t=BarId_{t+j},\\quad j=\\min\\{i:VolumeShock_{t+i}\\}$$"),
        "fwd_first_volume_shock_time_utc": ("First future volume-shock UTC time.", "UTC time of the first future volume shock in the horizon.", "$$FirstVolumeShockUTC_t=TimeUTC_{t+j},\\quad j=\\min\\{i:VolumeShock_{t+i}\\}$$"),
        "fwd_first_volume_shock_time_market": ("First future volume-shock market time.", "Market-local time of the first future volume shock in the horizon.", "$$FirstVolumeShockMarket_t=TimeMarket_{t+j},\\quad j=\\min\\{i:VolumeShock_{t+i}\\}$$"),
        "fwd_minutes_to_volume_shock": ("Minutes to future volume shock.", "Time from source bar to the first future volume shock.", "$$MinutesToVolumeShock_t=j\\cdot StepMinutes_t$$"),
        "fwd_volume_shock_before_mfe": ("Whether future volume shock arrives before best high.", "True when the first future volume shock occurs before or at the MFE bar.", "$$VolumeShockBeforeMFE_t=I(j\\le TimeToMFEbars_t)$$"),
        "fwd_return_at_volume_shock": ("Return at future volume shock.", "Return from current close to the close of the first future volume-shock bar.", "$$ReturnAtVolumeShock_t=\\frac{Close_{t+j}}{Close_t}-1$$"),
        "fwd_drawdown_before_volume_shock": ("Drawdown before future volume shock.", "Worst low before the first future volume shock, measured from current close.", "$$DrawdownBeforeVolumeShock_t=\\frac{\\min_{1\\le i\\le j}Low_{t+i}}{Close_t}-1$$"),
        "fwd_estimated_capacity_dollars": ("Estimated future tradable capacity.", "Provider capacity estimate equal to 1 percent of the maximum single-bar future dollar volume inside the horizon. It is a simple research proxy for available participation after the source bar.", "$$CapacityDollars_t=0.01\\cdot\\max_{1\\le i\\le h}(Close_{t+i}\\cdot Volume_{t+i})$$"),
        "fwd_capacity_score": ("Future capacity score.", "Bounded capacity score scaled against 25,000 dollars exactly as `min(capacity_dollars / 25000, 1)` in the Polars code.", "$$CapacityScore_t=\\min\\left(\\frac{CapacityDollars_t}{25000},1\\right)$$"),
        "fwd_price_outcome_quality": ("Future price outcome quality.", "Bounded price-path quality score using MFE, MAE, and path efficiency.", "$$PriceQuality_t=Q(Close_t,MFE_t,MAE_t,PathEfficiency_t)$$"),
        "fwd_liquidity_quality_score": ("Future liquidity quality score.", "Bounded score combining the maximum future relative volume, maximum future volume z-score, and the capacity score. The Polars code clips relative volume at 5x and positive volume z-score at 4 before applying weights.", "$$LiquidityQuality_t=0.40\\min\\left(\\frac{MaxRelVol20_t}{5},1\\right)+0.30\\min\\left(\\frac{\\max(MaxVolumeZ20_t,0)}{4},1\\right)+0.30CapacityScore_t$$"),
        "fwd_outcome_bucket": ("Combined price/liquidity outcome bucket.", "Categorical label splitting future outcome into good/bad price and good/bad volume buckets using 0.60 quality thresholds.", "$$Bucket_t=case(PriceQuality_t\\ge0.60,LiquidityQuality_t\\ge0.60)$$"),
    }
    if lower in details:
        short, detailed, equation = details[lower]
        return supervision_block(short, detailed, equation, {"h": "Horizon in bars", "Q": "Provider bounded quality function", "I": "Indicator function"})
    return supervision_block(f"{title} future liquidity label.", f"{title} describes future participation or liquidity quality over the supervision horizon.", f"$$\\text{{{title}}}_t=FutureLiquidityValue_t$$", {"FutureLiquidityValue_t": "Provider future liquidity output"})


def method_label_knowledge(lower: str, title: str) -> dict[str, Any]:
    details = {
        "trade_method": ("Trade method name.", "The method thesis used for the row, such as PRICE_VOLUME_SHOCK, SCALP, or MOMENTUM_SCALP.", "$$TradeMethod_t\\in\\{PRICE\\_VOLUME\\_SHOCK,SCALP,MOMENTUM\\_SCALP\\}$$"),
        "method_min_horizon_bars": ("Minimum method horizon in bars.", "Earliest future bar included by the method window for the current timeframe.", "$$MinHorizonBars_t=minBars(method,timeframe)$$"),
        "method_max_horizon_bars": ("Maximum method horizon in bars.", "Latest future bar included by the method window for the current timeframe.", "$$MaxHorizonBars_t=maxBars(method,timeframe)$$"),
        "method_min_horizon_minutes": ("Minimum method horizon in minutes.", "Minimum method horizon converted to minutes using the timeframe step.", "$$MinHorizonMinutes_t=MinHorizonBars_t\\cdot StepMinutes_t$$"),
        "method_max_horizon_minutes": ("Maximum method horizon in minutes.", "Maximum method horizon converted to minutes using the timeframe step.", "$$MaxHorizonMinutes_t=MaxHorizonBars_t\\cdot StepMinutes_t$$"),
        "method_best_exit_bar_id": ("Best method exit bar id.", "Bar id of the best future high inside the method window.", "$$BestExitBarId_t=BarId_{t+argmax(High_{methodWindow})}$$"),
        "method_best_exit_time_utc": ("Best method exit UTC time.", "UTC timestamp of the best future high inside the method window.", "$$BestExitTimeUTC_t=TimeUTC_{best}$$"),
        "method_best_horizon_bars": ("Bars to best method exit.", "Number of bars from source bar to the best future high in the method window.", "$$BestHorizonBars_t=argmax(High_{methodWindow})+MinHorizonBars_t$$"),
        "method_best_horizon_minutes": ("Minutes to best method exit.", "Bars to best method exit multiplied by timeframe step minutes.", "$$BestHorizonMinutes_t=BestHorizonBars_t\\cdot StepMinutes_t$$"),
        "method_best_price": ("Best method exit price.", "Highest future high reached inside the method window.", "$$BestPrice_t=\\max(High_{methodWindow})$$"),
        "method_best_return": ("Best method return.", "Return from source close to the best method exit price.", "$$BestReturn_t=\\frac{BestPrice_t}{Close_t}-1$$"),
        "method_mae_before_best": ("Adverse excursion before best exit.", "Worst low from the start of the method window through the best-exit bar, measured from current close.", "$$MAEBeforeBest_t=\\frac{\\min(Low_{windowBeforeBest})}{Close_t}-1$$"),
        "method_mfe_mae_ratio": ("Method reward-to-risk path ratio.", "Best method return divided by the absolute adverse excursion before the best exit.", "$$MethodMFEtoMAE_t=\\frac{BestReturn_t}{|MAEBeforeBest_t|}$$"),
        "method_path_efficiency": ("Method path efficiency.", "How directly the future close path traveled toward the best method price.", "$$MethodPathEfficiency_t=\\frac{BestPrice_t-Close_t}{\\sum |\\Delta Close_{methodWindow}|}$$"),
        "method_entry_signal": ("Method entry label.", "True when the method oracle action is ENTER_NOW.", "$$MethodEntry_t=I(OracleAction_t=ENTER\\_NOW)$$"),
        "method_exit_signal": ("Method exit label.", "True when the method oracle action is IGNORE.", "$$MethodExit_t=I(OracleAction_t=IGNORE)$$"),
        "method_confidence": ("Method confidence score.", "Bounded score from 0 to 1. Generic methods use the path-quality score. PRICE_VOLUME_SHOCK uses the code path `clip(0.45 * shock_context + 0.35 * base_confidence + 0.12 * confirmation_speed + confirmation_bonus, 0, 1)`.", "$$MethodConfidence_t=\\begin{cases}BaseConfidence_t,&method\\ne PRICE\\_VOLUME\\_SHOCK\\\\clip(0.45ShockContext_t+0.35BaseConfidence_t+0.12ConfirmationSpeed_t+ConfirmationBonus_t,0,1),&method=PRICE\\_VOLUME\\_SHOCK\\end{cases}$$"),
        "oracle_action": ("Method oracle action.", "Categorical supervision action derived from method confidence, best return, and shock context. Generic methods enter when base confidence is at least 0.65 and best return is above 0.5 percent; PRICE_VOLUME_SHOCK ignores weak/no confirmation, enters at confidence at least 0.68 with best return above 0.4 percent, watches at confidence at least 0.45, and otherwise ignores.", "$$OracleAction_t=case(MethodConfidence_t,BestReturn_t,ShockContext_t,ShockType_t)$$"),
    }
    if lower in details:
        short, detailed, equation = details[lower]
        return supervision_block(short, detailed, equation, {"I": "Indicator function", "Q": "Provider bounded quality function"})
    return supervision_block(f"{title} method label.", f"{title} describes the future method window, best outcome, adverse path, or oracle decision for the selected trade method.", f"$$\\text{{{title}}}_t=MethodValue_t$$", {"MethodValue_t": "Provider method supervision output"})


def method_shock_label_knowledge(lower: str, title: str) -> dict[str, Any]:
    mapped = {
        "current_price_shock": ("Current price shock at source bar.", "$$CurrentPriceShock_t=PriceShock_t$$"),
        "current_volume_shock": ("Current volume shock at source bar.", "$$CurrentVolumeShock_t=VolumeShock_t$$"),
        "current_confirmed_price_volume_shock": ("Current confirmed price-volume shock.", "$$CurrentConfirmedPVS_t=ConfirmedPVS_t$$"),
        "shock_confirmation_type": ("Shock sequence type used by method label.", "$$ShockType_t=case(PriceShock_t,VolumeShock_t,FutureConfirm_t)$$"),
        "shock_confirmation_delay_minutes": ("Minutes from source shock to confirmation.", "$$ShockDelay_t=DelayMinutes_t$$"),
        "shock_price_score": ("Price-side shock score at source bar.", "$$ShockPriceScore_t=PriceShockScore_t$$"),
        "shock_volume_score": ("Volume-side shock score at source bar.", "$$ShockVolumeScore_t=VolumeShockScore_t$$"),
        "shock_score": ("Current combined price-volume shock score copied from the feature table.", "$$ShockScore_t=PriceVolumeShockScore_t$$"),
        "shock_drawdown_before_confirmation": ("Drawdown before shock confirmation.", "$$ShockDrawdownBeforeConfirm_t=\\frac{LowBeforeConfirm_t}{Close_t}-1$$"),
        "shock_return_after_confirmation": ("Best return after shock confirmation.", "$$ShockReturnAfterConfirm_t=\\frac{PostConfirmBestHigh_t}{ConfirmationPrice_t}-1$$"),
        "shock_best_exit_after_confirmation_bar_id": ("Best post-confirmation exit bar id.", "$$ShockBestExitBarId_t=BarId_{postConfirmBest}$$"),
        "shock_best_exit_after_confirmation_time_utc": ("Best post-confirmation exit UTC time.", "$$ShockBestExitTimeUTC_t=TimeUTC_{postConfirmBest}$$"),
    }
    short, equation = mapped[lower]
    return supervision_block(
        short=short,
        detailed=f"{title} is the method-level copy of the shock context used to evaluate PRICE_VOLUME_SHOCK and compare shock candidates across methods.",
        equation=equation,
        variables={"PriceShockScore": "Provider price shock score", "VolumeShockScore": "Provider volume shock score"},
    )


def scanner_label_knowledge(lower: str, title: str) -> dict[str, Any]:
    details = {
        "universe_size": ("Number of candidates in the timestamp/method universe.", "Universe size counts rows for the same bar_time_utc and trade_method before ranking.", "$$UniverseSize_{t,m}=count(candidates_{t,m})$$"),
        "oracle_rank": ("Dense descending rank by method confidence.", "Oracle rank is 1 for the highest-confidence candidate at the timestamp for the method. Ties share the same dense rank.", "$$OracleRank_{i,t,m}=rank_{dense,desc}(MethodConfidence_{i,t,m})$$"),
        "oracle_percentile": ("Cross-sectional percentile from oracle rank.", "Oracle percentile maps rank into 0 to 1 where 1 is best in the timestamp/method universe.", "$$OraclePercentile_{i,t,m}=1-\\frac{OracleRank_{i,t,m}-1}{\\max(UniverseSize_{t,m}-1,1)}$$"),
        "is_top_1": ("Top one candidate flag.", "True when oracle_rank is 1.", "$$IsTop1_t=I(OracleRank_t\\le1)$$"),
        "is_top_3": ("Top three candidate flag.", "True when oracle_rank is 3 or better.", "$$IsTop3_t=I(OracleRank_t\\le3)$$"),
        "is_top_5": ("Top five candidate flag.", "True when oracle_rank is 5 or better.", "$$IsTop5_t=I(OracleRank_t\\le5)$$"),
        "is_top_10": ("Top ten candidate flag.", "True when oracle_rank is 10 or better.", "$$IsTop10_t=I(OracleRank_t\\le10)$$"),
        "is_top_1pct": ("Top one percent candidate flag.", "True when oracle_percentile is at least 0.99.", "$$IsTop1Pct_t=I(OraclePercentile_t\\ge0.99)$$"),
        "is_top_5pct": ("Top five percent candidate flag.", "True when oracle_percentile is at least 0.95.", "$$IsTop5Pct_t=I(OraclePercentile_t\\ge0.95)$$"),
    }
    if lower in details:
        short, detailed, equation = details[lower]
        return supervision_block(short, detailed, equation, {"I": "Indicator function"})
    return supervision_block(f"{title} scanner label.", f"{title} is part of the cross-sectional scanner supervision output for ranking tickers at the same timestamp and method.", f"$$\\text{{{title}}}_t=ScannerValue_t$$", {"ScannerValue_t": "Provider scanner supervision output"})


def supervision_block(short: str, detailed: str, equation: str, variables: dict[str, str]) -> dict[str, Any]:
    return knowledge_block(
        short=short,
        detailed=detailed,
        theory="Supervision fields intentionally look forward to convert the realized future path into training labels, research diagnostics, and chart-review context. They are not live features.",
        interpretation="Use only in offline analysis or model training. In charts, read them as what eventually happened after the source bar, not what was knowable at the source bar.",
        equation=equation,
        variables=variables,
        caveats=[
            "Uses future bars by design. Use for research, training labels, and chart review only.",
            "Do not use this field as a live feature or entry condition without an explicit prediction delay.",
        ],
    )


def fallback_knowledge_for_column(column: str, group: str, category: str, title: str) -> dict[str, Any]:
    unit = semantics_for_column(column)["unit"]
    return knowledge_block(
        short=f"{title} provider field.",
        detailed=f"{title} is a {unit} field in the {group} artifact group. It should be interpreted with the row ticker, timeframe, session, and neighboring provider fields.",
        theory=theory_for_group(group, category),
        interpretation=interpretation_for_group(group, category),
        equation=f"$$\\text{{{title}}}_t=ProviderColumn_t$$",
        variables={"ProviderColumn_t": "Materialized provider value for this column at row t"},
    )


def theory_for_group(group: str, category: str) -> str:
    if group == "session":
        return "Session features encode where the current bar sits relative to time-segmented market structure. Intraday behavior is not stationary across premarket, open auction aftermath, midday liquidity, and close; anchoring to premarket range, opening range, session open, and running extremes makes price location comparable inside those regimes."
    if group == "volume_liquidity":
        return "Volume and liquidity features model participation as a constraint on signal quality and execution capacity. Abnormal price movement without abnormal participation is more likely to be fragile; high dollar volume and transaction activity improve cross-sectional comparability and indicate whether a move had enough traded interest to matter."
    if group == "price_action":
        return "Price-action features convert candle geometry and local breakout events into deterministic state variables. They summarize how price traveled inside recent bars, whether new local extremes were reached, and whether buyers or sellers controlled the close relative to the range."
    if group == "market_structure":
        return "Market-structure features approximate the sequence of local pivots and structural breaks. They are designed to separate random candle movement from a change in the market's accepted high-low structure using causal local extrema and prior-bar references."
    if group == "order_blocks":
        return "Order-block features are deterministic proxies for displacement-derived supply and demand zones. They do not observe hidden institutional orders; they identify large directional expansion candles and use adjacent prices as reproducible chart-review zones where later reaction can be studied."
    if group == "fvg":
        return "Fair value gap features identify three-candle displacement gaps where the current candle leaves a price interval untraded relative to the candle two bars earlier. The academic value is not the name of the pattern, but the measurable imbalance: price moved fast enough that an interval was skipped in the bar sequence."
    if group == "shock":
        return "Shock features combine abnormal price movement, abnormal participation, and event ordering. They are designed for sequence-aware momentum research where a price displacement can lead and volume can confirm shortly afterward, rather than requiring all evidence on the same bar."
    if category == "indicator":
        return "Technical indicators are deterministic transformations that compress a recent path of price, volume, or volatility into a lower-dimensional state variable. They reduce raw-bar dimensionality, but the compression discards information and should be evaluated as context rather than a standalone truth source."
    return "Feature columns transform raw bars into structured state variables with explicit formulas. Their purpose is to make chart review, model training, and backtest diagnostics reproducible across tickers, sessions, and timeframes."


def interpretation_for_group(group: str, category: str) -> str:
    if group == "session":
        return "Read session values as anchors and distances. A breakout above premarket high, a reclaim of day open, or a press into day high has different meaning depending on time of day, current volume, and whether the move is extending or mean-reverting from earlier structure."
    if group == "volume_liquidity":
        return "Read high values as evidence of stronger participation or tradable capacity, then check whether price direction and range expansion agree. Relative measures are usually more informative than raw volume because they compare current activity with the ticker's own recent baseline."
    if group == "price_action":
        return "Read true flags as local events, not complete trade decisions. Their value comes from clustering: a reclaim, breakout, strong close location, and expanding participation together are more informative than any single candle flag."
    if group == "market_structure":
        return "Read these values as local swing and regime descriptors. Break-of-structure fields point to a change in accepted highs or lows, while swing fields identify causal local extrema from the current and prior bars."
    if group == "order_blocks":
        return "Read these zones as reproducible displacement-derived supply or demand proxies. Their usefulness should be tested by later reaction, mitigation, and failure behavior, not by assuming they reveal hidden order flow."
    if group == "fvg":
        return "Read FVG boundaries as event-anchored zones created by fast directional movement. Later revisits can be studied as mitigation, continuation, or failure events; the zone is point-originated and should not be confused with a price-following indicator band."
    if group == "shock":
        return "Read shock fields as a sequence: price abnormality, participation abnormality, confirmation delay, and combined score. The order matters because a price shock followed by volume confirmation has a different research meaning from volume appearing first without price displacement."
    if category == "indicator":
        return "Use the value as context for trend, momentum, volatility, or participation. Confirm with price action, volume, and session state; indicators summarize historical data and should not be interpreted as causal predictors by themselves."
    return "Interpret this field together with its units, timeframe, source artifact, and related provider columns. The same numeric value can carry different meaning on 1m versus daily bars."


def knowledge_block(
    short: str,
    detailed: str,
    theory: str,
    interpretation: str,
    equation: str,
    variables: dict[str, str],
    caveats: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "shortDescription": short,
        "detailedDescription": detailed,
        "theory": theory,
        "interpretation": interpretation,
        "caveats": caveats or [
            "This is a deterministic provider field, not an independent trading rule.",
            "Interpret it with ticker liquidity, timeframe, session regime, and related provider fields.",
            "Validate usefulness empirically before using it as a model feature or chart decision input.",
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
    base_item = catalog_item_by_id(base_provider_catalog()).get(item_id, {})
    clean = enforce_pane_contract(base_item, normalize_presentation({str(key): value for key, value in presentation.items() if value is not None}))
    overrides[item_id] = clean
    payload = {"catalogVersion": CATALOG_VERSION, "presentation": overrides}
    override_path(processed_root).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return provider_catalog(processed_root)


def apply_presentation_overrides(catalog: dict[str, Any], overrides: dict[str, dict[str, Any]]) -> None:
    if not overrides:
        return
    for section in ("columns", "displayItems", "supervisionMethods", "scanners"):
        for item in catalog.get(section, []):
            item_id = str(item.get("id") or "")
            presentation = overrides.get(item_id)
            if isinstance(presentation, dict):
                item["presentation"] = merge_presentation_override(item, presentation)


def catalog_columns_by_column(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("column")): item for item in catalog.get("columns", []) if item.get("column")}


def catalog_display_items(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id")): item for item in catalog.get("displayItems", []) if item.get("id")}


def catalog_item_by_id(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for section in ("columns", "displayItems", "supervisionMethods", "scanners"):
        for item in catalog.get(section, []):
            if item.get("id"):
                items[str(item["id"])] = item
    return items
