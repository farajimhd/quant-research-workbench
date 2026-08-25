from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from services.reference_gateway.table_groups import REFERENCE_TABLE_GROUPS


@dataclass(frozen=True, slots=True)
class QueryPlanDefinition:
    plan_id: str
    owner: str
    implementation: str
    source_paths: tuple[str, ...]
    identity_join: str
    event_clock: str
    availability_clock: str
    coverage_path: str
    bounded: bool = True
    point_in_time: bool = True
    version: int = 1


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    field_id: str
    label: str
    presentation_label: str
    group: str
    value_type: str
    unit: str
    entity_grain: str
    owner: str
    source_path: str
    source_columns: tuple[str, ...]
    query_plan_id: str
    identity_join: str
    event_at: str
    available_at: str
    ttl_seconds: int | None
    publication_cadence: str
    historical_support: str
    modes: tuple[str, ...]
    provenance: str
    coverage_query_plan: str
    freshness_policy: str
    null_reasons: tuple[str, ...]
    security_classification: str = "internal_market_data"
    schema_version: int = 1
    status: str = "implemented"
    source_summary: str = ""
    calculation_summary: str = ""
    input_field_ids: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ()
    known_values: tuple[tuple[str, str, str], ...] = ()
    interval_semantics: str = ""
    aggregation_functions: tuple[str, ...] = ()
    default_aggregation: str = ""
    intrinsic_aggregation: str = ""
    aggregation_runtime_fields: tuple[tuple[str, str], ...] = ()
    presentation_value_type: str = "text"


@dataclass(frozen=True, slots=True)
class DiscoveryFieldPresentation:
    source_id: str
    field_id: str
    column_id: str
    label: str
    description: str
    semantic_type: str
    default_visible: bool
    filterable: bool
    sortable: bool
    filter_operators: tuple[str, ...]
    timeframes: tuple[str, ...]
    presentation_value_type: str = ""


@dataclass(frozen=True, slots=True)
class MarketSourceDefinition:
    source_id: str
    label: str
    owner: str
    source_path: str
    transport: str
    event_clock: str
    availability_clock: str
    coverage_path: str
    watermark_path: str
    retention_policy: str
    modes: tuple[str, ...]
    authoritative_for: tuple[str, ...]
    status: str = "implemented"
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class ProductDefinition:
    product_id: str
    label: str
    owner: str
    kind: str
    implementation: str
    source_ids: tuple[str, ...]
    dependency_products: tuple[str, ...]
    outputs: tuple[str, ...]
    delivery: tuple[str, ...]
    execution_scopes: tuple[str, ...]
    modes: tuple[str, ...]
    persistence_policy: str
    schema_version: int
    status: str = "implemented"


@dataclass(frozen=True, slots=True)
class LinkContractDefinition:
    link_id: str
    value_type: str
    producers: tuple[str, ...]
    consumers: tuple[str, ...]
    clock_policy: str
    identity_policy: str
    modes: tuple[str, ...]
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class ContainerDefinition:
    container_id: str
    label: str
    implementation: str
    input_links: tuple[str, ...]
    output_links: tuple[str, ...]
    product_ids: tuple[str, ...]
    modes: tuple[str, ...]
    state_schema_version: int
    status: str = "implemented"


@dataclass(frozen=True, slots=True)
class ConfigurationSchemaDefinition:
    schema_id: str
    owner: str
    implementation: str
    version: int
    modes: tuple[str, ...]
    immutable_when_published: bool
    status: str = "implemented"


@dataclass(frozen=True, slots=True)
class CompatibilityAliasDefinition:
    alias_id: str
    owner: str
    alias_path: str
    canonical_path: str
    retirement_state: str
    removal_condition: str


@dataclass(frozen=True, slots=True)
class RegistryTypeDefinition:
    kind: str
    label: str
    description: str
    icon: str
    accent: str
    configuration_mode: str
    user_facing: bool


@dataclass(frozen=True, slots=True)
class ConfigurationBindingDefinition:
    binding_id: str
    configuration_path: str
    kind: str
    identity_field: str
    configuration_mode: str
    editable_fields: tuple[str, ...]
    reference_fields: tuple[str, ...] = ()


REGISTRY_TYPES = (
    RegistryTypeDefinition("field", "Field", "One typed value contract with causal provenance.", "database", "blue", "select_reference", True),
    RegistryTypeDefinition("source", "Source", "External or persisted evidence authority.", "server", "slate", "locked", False),
    RegistryTypeDefinition("processing_step", "Processing step", "Compiled state transition in an owned event path.", "cable", "cyan", "locked", True),
    RegistryTypeDefinition("derivation", "Derivation", "Vectorized, set-based, or incremental transformation from Fields to Fields.", "sigma", "violet", "parameterized_reference", True),
    RegistryTypeDefinition("signal", "Signal", "Versioned event lifecycle published by QMD, News, SEC, model, or another registered producer.", "activity", "rose", "parameterized_reference", True),
    RegistryTypeDefinition("event_schema", "Event schema", "Typed event identity, clocks, properties, and evidence.", "braces", "rose", "locked", False),
    RegistryTypeDefinition("product", "Product", "Delivered record, dataset, or stream over registered definitions.", "package", "slate", "locked", False),
    RegistryTypeDefinition("query_plan", "Query plan", "Bounded causal retrieval implementation.", "route", "slate", "locked", False),
    RegistryTypeDefinition("column", "Column", "Presentation composition over Field or Signal bindings.", "columns", "amber", "select_reference", True),
    RegistryTypeDefinition("condition", "Condition", "Typed comparison over registered references.", "list_filter", "orange", "editable_instance", True),
    RegistryTypeDefinition("rule_set", "Rule set", "Reusable Boolean composition of Conditions.", "list_checks", "orange", "editable_instance", True),
    RegistryTypeDefinition("watchlist", "Watchlist", "Persistent candidate composition over Core Scan, rules, ranking, and columns.", "scan_search", "slate", "editable_instance", True),
    RegistryTypeDefinition("signal_stream", "Signal Stream", "Append-only occurrences emitted when configured Rule Sets transition into a matching state.", "radio_tower", "rose", "editable_instance", True),
    RegistryTypeDefinition("trading_action", "Trading action", "Atomic broker-neutral intent or campaign command shared by Strategy and Canvas.", "mouse_pointer_click", "violet", "locked", True),
    RegistryTypeDefinition("action_policy", "Action policy", "Reusable trigger, action, sizing, timing, and authority composition.", "workflow", "violet", "editable_instance", True),
    RegistryTypeDefinition("strategy", "Strategy", "Versioned executable decision definition.", "git_branch", "violet", "locked", True),
    RegistryTypeDefinition("strategy_profile", "Strategy profile", "Configured parameters and bindings for one Strategy revision.", "sliders", "violet", "editable_instance", True),
    RegistryTypeDefinition("run_plan", "Run Plan", "Executable selection of Strategy, universe, accounts, OMS, and modes.", "network", "violet", "editable_instance", True),
    RegistryTypeDefinition("account_binding", "Account binding", "Stable application account identity and mode-specific session binding.", "boxes", "cyan", "editable_instance", True),
    RegistryTypeDefinition("portfolio_policy", "Portfolio policy", "Account-wide capital, risk, capacity, and permission limits.", "briefcase", "green", "editable_instance", True),
    RegistryTypeDefinition("portfolio_mandate", "Portfolio mandate", "Run Plan-to-account allocation and authority limits.", "network", "green", "editable_instance", True),
    RegistryTypeDefinition("portfolio_group", "Portfolio group", "Aggregate limits over explicitly selected accounts.", "bar_chart", "green", "editable_instance", True),
    RegistryTypeDefinition("oms_profile", "OMS profile", "Reusable execution and protection selection.", "send", "orange", "editable_instance", True),
    RegistryTypeDefinition("execution_policy", "Execution policy", "Bounded quote, price, repricing, and fill behavior.", "radio_tower", "orange", "editable_instance", True),
    RegistryTypeDefinition("protection_profile", "Protection profile", "Broker-held stop, target, trailing, and repair policy.", "shield_check", "orange", "editable_instance", True),
    RegistryTypeDefinition("canvas_profile", "Canvas profile", "Persisted workspace composition and container state.", "layout_dashboard", "blue", "editable_instance", True),
)


CONFIGURATION_BINDINGS = (
    ConfigurationBindingDefinition("market_discovery.core_scan", "market_discovery.core_scan", "product", "scan_id", "editable_instance", ("name", "description", "inclusion_rule_sets", "ranking_field_ref", "ranking_interval", "ranking_aggregation", "ranking_direction", "maximum_size", "refresh_interval_ms", "columns", "column_intervals", "column_aggregations"), ("inclusion_rule_sets", "ranking_field_ref", "columns")),
    ConfigurationBindingDefinition("market_discovery.columns", "market_discovery.watchlists[].columns[]", "column", "column_id", "select_reference", (), ("column_id",)),
    ConfigurationBindingDefinition("market_discovery.conditions", "market_discovery.rule_sets[].conditions[]", "condition", "condition_id", "editable_instance", ("left_field_ref", "left_interval", "left_aggregation", "comparator", "right_field_ref", "right_interval", "right_aggregation", "value", "enabled"), ("left_field_ref", "right_field_ref")),
    ConfigurationBindingDefinition("market_discovery.rules", "market_discovery.rule_sets[]", "rule_set", "rule_set_id", "editable_instance", ("name", "description", "operator", "conditions", "enabled")),
    ConfigurationBindingDefinition("market_discovery.watchlists", "market_discovery.watchlists[]", "watchlist", "watchlist_id", "editable_instance", ("name", "description", "inclusion_rule_sets", "ranking_field_ref", "ranking_interval", "ranking_aggregation", "ranking_direction", "maximum_size", "refresh_interval_ms", "membership_expiry", "membership_ttl_ms", "manual_inclusions", "manual_exclusions", "columns", "column_intervals", "column_aggregations", "enabled"), ("source_scan_id", "inclusion_rule_sets", "ranking_field_ref", "columns")),
    ConfigurationBindingDefinition("market_discovery.signal_streams", "market_discovery.signal_streams[]", "signal_stream", "signal_stream_id", "editable_instance", ("name", "description", "inclusion_rule_sets", "inclusion_operator", "columns", "column_intervals", "column_aggregations", "refresh_interval_ms", "trigger_policy", "rearm_policy", "cooldown_ms", "maximum_events", "watchlist_routes", "enabled"), ("source_scan_id", "inclusion_rule_sets", "columns", "watchlist_routes")),
    ConfigurationBindingDefinition("trading_actions.definitions", "trading_actions.definitions[]", "trading_action", "action_id", "locked", (), ()),
    ConfigurationBindingDefinition("trading_actions.policies", "trading_actions.policies[]", "action_policy", "policy_id", "editable_instance", ("name", "description", "action_id", "trigger", "quantity", "authority", "maximum_uses", "enabled"), ("action_id", "trigger.rule_set_ids")),
    ConfigurationBindingDefinition("strategy.profiles", "strategy.profiles[]", "strategy_profile", "profile_id", "editable_instance", ("name", "description", "parameters", "lifecycle", "action_policy_ids"), ("definition_id", "action_policy_ids")),
    ConfigurationBindingDefinition("run_plans", "assignments.deployments[]", "run_plan", "run_plan_id", "editable_instance", ("name", "description", "profile_id", "signal_stream_ids", "watchlist_ids", "activation", "enablement", "mandate_ids", "oms_profile_id", "canvas_profile_id", "allowed_environments", "data_plan_ids", "source_revision_policy", "action_authority", "campaign_lifecycle", "enabled"), ("profile_id", "signal_stream_ids", "watchlist_ids", "mandate_ids", "oms_profile_id", "canvas_profile_id", "data_plan_ids")),
    ConfigurationBindingDefinition("accounts", "accounts.bindings[]", "account_binding", "account_key", "editable_instance", ("name", "account_class", "base_currency", "session_key", "portfolio_policy_id", "enabled", "modes"), ("portfolio_policy_id",)),
    ConfigurationBindingDefinition("portfolio.policies", "portfolio.policies[]", "portfolio_policy", "policy_id", "editable_instance", ("*",)),
    ConfigurationBindingDefinition("portfolio.mandates", "portfolio.mandates[]", "portfolio_mandate", "mandate_id", "editable_instance", ("run_plan_id", "account_key", "maximum_cash_fraction", "maximum_planned_risk_fraction", "maximum_positions", "assignment_mode", "allocation_weight", "maximum_action_authority", "allow_replacement", "minimum_replacement_improvement_pct", "enabled"), ("run_plan_id", "account_key")),
    ConfigurationBindingDefinition("portfolio.groups", "portfolio.groups[]", "portfolio_group", "group_id", "editable_instance", ("account_keys", "maximum_gross_exposure", "maximum_ticker_exposure"), ("account_keys",)),
    ConfigurationBindingDefinition("oms.profiles", "oms.profiles[]", "oms_profile", "profile_id", "editable_instance", ("name", "description", "settings"), ("entry_execution_policy_id", "exit_execution_policy_id", "protection_profile_id")),
    ConfigurationBindingDefinition("oms.execution_policies", "oms.execution_policies[]", "execution_policy", "policy_id", "editable_instance", ("name", "description", "quote_source", "partial_fill_policy", "envelope")),
    ConfigurationBindingDefinition("oms.protection_profiles", "oms.protection_profiles[]", "protection_profile", "profile_id", "editable_instance", ("name", "add_policy", "profit_pocket_transition", "mandatory_catastrophic_backstop", "emergency_repair_deadline_ms", "slices")),
    ConfigurationBindingDefinition("canvas.profile", "canvas.profile", "canvas_profile", "revision", "editable_instance", ("workspaceStates", "containerConfigurations", "groups")),
)


ALL_MODES = ("live", "paper", "replay", "backtest", "backtest_debug")
HISTORICAL_MODES = ("replay", "backtest", "backtest_debug")


MARKET_SOURCES = (
    MarketSourceDefinition(
        "qmd.massive_live",
        "Massive live WebSocket",
        "qmd_gateway",
        "websocket://massive/quotes+trades",
        "websocket",
        "SIP timestamp + source sequence",
        "QMD receive timestamp",
        "service://qmd/operational/massive_feed",
        "service://qmd/continuation/sequence",
        "ephemeral vendor stream",
        ("live", "paper"),
        ("current live quote/trade events",),
    ),
    MarketSourceDefinition(
        "qmd.live_memory",
        "QMD current live memory",
        "qmd_gateway",
        "service://qmd/stream/compact-events",
        "snapshot_and_delta",
        "canonical event clock",
        "QMD normalized_at",
        "service://qmd/metrics",
        "canonical event continuation cursor",
        "bounded current-session state",
        ("live", "paper"),
        ("current live continuation", "live scanner state"),
    ),
    MarketSourceDefinition(
        "qmd.recent_events",
        "QMD recent retained events",
        "qmd_gateway",
        "q_live.events",
        "clickhouse",
        "canonical SIP timestamp",
        "persisted_at",
        "q_live.qmd_live_event_coverage_v1",
        "latest verified recent interval",
        "three prior market sessions plus current session",
        ALL_MODES,
        ("recent historical events", "recent event-derived bars"),
    ),
    MarketSourceDefinition(
        "qmd.archive_events",
        "Market SIP compact archive",
        "market_sip_compact",
        "market_sip_compact.events_YYYY",
        "clickhouse",
        "canonical SIP timestamp",
        "archive build completion timestamp",
        "market_sip_compact.events_ordinal_continuity",
        "latest completed archive session",
        "durable",
        HISTORICAL_MODES,
        ("older historical events",),
    ),
    MarketSourceDefinition(
        "qmd.daily_bars",
        "Canonical daily session bars",
        "market_sip_compact",
        "market_sip_compact.daily_session_bars_by_symbol_time_v1",
        "clickhouse",
        "New York session date",
        "build completed_at",
        "market_sip_compact.daily_session_bars_by_symbol_time_v1",
        "latest completed session/build step",
        "durable",
        ALL_MODES,
        ("daily bars", "weekly/monthly/yearly derivation input"),
    ),
    MarketSourceDefinition(
        "reference.point_in_time",
        "Canonical point-in-time reference publications",
        "reference_gateway",
        "q_live.feature_tradable_universe_v1",
        "clickhouse",
        "source effective timestamp",
        "published_at_utc",
        "q_live.market_reference_publication_coverage_v1",
        "latest completed source publication",
        "source-specific",
        ALL_MODES,
        ("identity", "tradability", "scanner enrichment"),
    ),
)


PRODUCT_DEFINITIONS = (
    ProductDefinition("qmd.compact_events", "Canonical compact events", "qmd_core", "event", "qmd_core::compact_event", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events"), (), ("compact_event", "continuation_cursor", "coverage"), ("snapshot", "delta_stream", "historical_page"), ("universal_ingest", "request", "offline"), ALL_MODES, "recent_then_archive", 1),
    ProductDefinition("qmd.intraday_bars", "Canonical intraday bars", "qmd_core", "bar", "qmd_core::bars", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events"), ("qmd.compact_events",), ("ohlcv", "vwap", "closed_state"), ("snapshot", "delta_stream"), ("core_scan", "watchlist", "signal_stream", "strategy_run", "request", "offline"), ALL_MODES, "selected_q_live_plus_rebuildable_cache", 1),
    ProductDefinition("qmd.macro_bars", "Daily and macro bars", "qmd_core", "bar", "qmd_core::market_products", ("qmd.daily_bars",), (), ("daily", "weekly", "monthly", "yearly", "partial_state"), ("snapshot",), ("request", "offline"), ALL_MODES, "daily_authority_derived_macro", 1),
    ProductDefinition("qmd.indicators", "Reusable QMD indicators", "qmd_core", "indicator", "qmd_core::indicators", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events", "reference.point_in_time"), ("qmd.intraday_bars",), ("indicator_rows", "warmup", "provenance"), ("snapshot", "progressive_delta"), ("core_scan", "watchlist", "signal_stream", "strategy_run", "request", "offline"), ALL_MODES, "catalog_policy", 1),
    ProductDefinition("qmd.market_signals", "Reusable market observations", "qmd_core", "signal", "qmd_core::market_signal", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events"), ("qmd.indicators",), ("market_signal_event", "evidence"), ("snapshot", "delta_stream"), ("watchlist", "signal_stream", "strategy_run", "request", "offline"), ALL_MODES, "decision_snapshot_only", 1),
    ProductDefinition("qmd.scanner", "Market scanner projection", "qmd_gateway", "scanner", "services/qmd-gateway/src/scanner.rs", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events", "reference.point_in_time"), ("qmd.intraday_bars", "qmd.indicators", "qmd.market_signals"), ("candidate_rows", "membership", "as_of", "coverage"), ("snapshot", "delta_stream", "historical_snapshot"), ("core_scan", "watchlist", "signal_stream", "strategy_run"), ALL_MODES, "current_projection_plus_journal_evidence", 1),
    ProductDefinition("qmd.chart", "Progressive chart payload", "qmd_history_gateway", "chart", "services/qmd_history_gateway/src/api.rs", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events", "qmd.daily_bars"), ("qmd.intraday_bars", "qmd.macro_bars", "qmd.indicators", "qmd.market_signals"), ("bars", "indicators", "signals", "structure", "provenance"), ("base_snapshot", "progressive_delta"), ("request",), ALL_MODES, "bounded_revisioned_cache", 1),
    ProductDefinition("qmd.computation_targets", "Scoped computation leases", "qmd_gateway", "control", "services/qmd-gateway/src/computation_targets.rs", ("qmd.live_memory",), ("qmd.indicators", "qmd.market_signals"), ("target_lease", "effective_scope", "expiry"), ("snapshot", "command"), ("watchlist", "signal_stream", "strategy_run", "request"), ("live", "paper"), "ephemeral_lease", 1),
)


SYMBOL_LINK_CONSUMERS = (
    "chart",
    "charts_quotes",
    "facts",
    "microstructure",
    "ticker_news",
    "ticker_sec",
    "xbrl",
)

LINK_CONTRACTS = (
    LinkContractDefinition(
        "workspace.symbol_context",
        "point_in_time_symbol_identity",
        ("scanner", "signal_stream", "watchlist", "strategy_activity", "positions", "orders", "closed_trades"),
        SYMBOL_LINK_CONSUMERS,
        "preserve workspace clock",
        "resolve symbol through event-valid identity",
        ALL_MODES,
    ),
    LinkContractDefinition(
        "workspace.clock_context",
        "as_of_clock",
        ("workspace_controller",),
        ("all_containers",),
        "mode clock is authoritative",
        "not_applicable",
        ALL_MODES,
    ),
    LinkContractDefinition(
        "workspace.news_selection",
        "canonical_news_document_id",
        ("news", "ticker_news"),
        ("news_detail",),
        "selected record must be available by workspace clock",
        "canonical document ID plus event-valid ticker link",
        ALL_MODES,
    ),
    LinkContractDefinition(
        "workspace.sec_selection",
        "sec_accession",
        ("sec", "ticker_sec"),
        ("sec_detail",),
        "accepted_at must not exceed workspace clock",
        "accession plus event-valid CIK/security bridge",
        ALL_MODES,
    ),
    LinkContractDefinition(
        "workspace.order_selection",
        "canonical_order_identity",
        ("orders", "positions"),
        ("fills", "activity"),
        "run clock",
        "run ID plus canonical order ID",
        ALL_MODES,
    ),
)


def _container(
    container_id: str,
    label: str,
    implementation: str,
    *,
    products: tuple[str, ...] = (),
    inputs: tuple[str, ...] = ("workspace.clock_context",),
    outputs: tuple[str, ...] = (),
    modes: tuple[str, ...] = ALL_MODES,
) -> ContainerDefinition:
    return ContainerDefinition(
        container_id,
        label,
        implementation,
        inputs,
        outputs,
        products,
        modes,
        8,
    )


CONTAINER_DEFINITIONS = (
    _container("chart", "Chart", "frontend/src/app/components/ChartPanel.tsx", products=("qmd.chart",), inputs=("workspace.clock_context", "workspace.symbol_context")),
    _container("charts_quotes", "Charts & Quotes", "frontend/src/app/components/MarketMicrostructureContainers.tsx", products=("qmd.chart", "qmd.intraday_bars"), inputs=("workspace.clock_context", "workspace.symbol_context")),
    _container("facts", "Stock Facts", "frontend/src/app/components/StockFactsContainer.tsx", inputs=("workspace.clock_context", "workspace.symbol_context")),
    _container("microstructure", "Quotes & Tape", "frontend/src/app/components/MarketMicrostructureContainers.tsx", products=("qmd.intraday_bars", "qmd.indicators"), inputs=("workspace.clock_context", "workspace.symbol_context")),
    _container("scanner", "Scanner", "frontend/src/app/components/MarketScreenerContainers.tsx", products=("qmd.scanner",), outputs=("workspace.symbol_context",)),
    _container("signal_stream", "Signal Stream", "frontend/src/app/components/MarketScreenerContainers.tsx", products=("qmd.scanner",), outputs=("workspace.symbol_context",)),
    _container("watchlist", "Watch Universe", "frontend/src/app/components/MarketScreenerContainers.tsx", products=("qmd.scanner", "qmd.computation_targets"), outputs=("workspace.symbol_context",)),
    _container("strategy_activity", "Strategy Activity", "frontend/src/app/components/MarketScreenerContainers.tsx", outputs=("workspace.symbol_context",)),
    _container("strategy", "Strategy", "frontend/src/pages/CanvasConfigurationPage.tsx"),
    _container("portfolio", "Portfolio", "frontend/src/pages/CanvasConfigurationPage.tsx"),
    _container("positions", "Position Manager", "frontend/src/pages/CanvasConfigurationPage.tsx", outputs=("workspace.symbol_context", "workspace.order_selection")),
    _container("orders", "Orders & Fills", "frontend/src/pages/CanvasConfigurationPage.tsx", outputs=("workspace.symbol_context", "workspace.order_selection")),
    _container("fills", "Execution Audit", "frontend/src/pages/CanvasConfigurationPage.tsx", inputs=("workspace.clock_context", "workspace.order_selection")),
    _container("closed_trades", "Round-trip Audit", "frontend/src/pages/CanvasConfigurationPage.tsx", outputs=("workspace.symbol_context",)),
    _container("activity", "Trading Activity", "frontend/src/pages/CanvasConfigurationPage.tsx", inputs=("workspace.clock_context", "workspace.order_selection")),
    _container("performance_journal", "Trading Journal", "frontend/src/pages/CanvasConfigurationPage.tsx"),
    _container("news", "All News", "frontend/src/app/components/NewsContainers.tsx", outputs=("workspace.news_selection",)),
    _container("ticker_news", "Ticker News", "frontend/src/app/components/NewsContainers.tsx", inputs=("workspace.clock_context", "workspace.symbol_context"), outputs=("workspace.news_selection",)),
    _container("news_detail", "News Detail", "frontend/src/app/components/NewsContainers.tsx", inputs=("workspace.clock_context", "workspace.news_selection")),
    _container("sec", "All SEC", "frontend/src/app/components/SecContainers.tsx", outputs=("workspace.sec_selection",)),
    _container("ticker_sec", "Ticker SEC", "frontend/src/app/components/SecContainers.tsx", inputs=("workspace.clock_context", "workspace.symbol_context"), outputs=("workspace.sec_selection",)),
    _container("sec_detail", "SEC Detail", "frontend/src/app/components/SecContainers.tsx", inputs=("workspace.clock_context", "workspace.sec_selection")),
    _container("xbrl", "XBRL Financial Evidence", "frontend/src/app/components/XbrlAnalysisContainer.tsx", inputs=("workspace.clock_context", "workspace.symbol_context")),
)


CONFIGURATION_SCHEMAS = (
    ConfigurationSchemaDefinition("trading_configuration", "backend", "src/backend/trading_configuration_service.py", 30, ALL_MODES, True),
    ConfigurationSchemaDefinition("signal_stream", "backend", "src/backend/signal_stream_runtime_service.py", 1, ALL_MODES, True),
    ConfigurationSchemaDefinition("strategy_profile", "strategy_runtime", "src/trading_runtime/strategy_engine.py", 3, ALL_MODES, True),
    ConfigurationSchemaDefinition("watchlist", "backend", "src/backend/watchlist_runtime_service.py", 1, ALL_MODES, True),
    ConfigurationSchemaDefinition("historical_watchlist_plan", "backend", "src/backend/historical_watchlist_plan.py", 2, ("replay", "backtest"), False),
    ConfigurationSchemaDefinition("run_plan", "backend", "src/backend/trading_configuration_service.py", 1, ALL_MODES, True),
    ConfigurationSchemaDefinition("canvas_profile", "backend", "src/backend/trading_configuration_service.py", 1, ALL_MODES, True),
    ConfigurationSchemaDefinition("canvas_layout", "frontend", "frontend/src/app/components/TradingWorkspace.tsx", 8, ALL_MODES, False),
    ConfigurationSchemaDefinition("portfolio_policy", "portfolio", "src/trading_runtime/portfolio.py", 1, ALL_MODES, True),
    ConfigurationSchemaDefinition("oms_policy", "oms", "src/trading_runtime/order_management.py", 1, ALL_MODES, True),
    ConfigurationSchemaDefinition("strategy_intent", "strategy_runtime", "src/trading_runtime/signals.py", 1, ALL_MODES, True),
    ConfigurationSchemaDefinition("execution_policy", "oms", "src/trading_runtime/execution_policies.py", 1, ALL_MODES, True),
    ConfigurationSchemaDefinition("protection_profile", "oms", "src/trading_runtime/execution_policies.py", 1, ALL_MODES, True),
    ConfigurationSchemaDefinition("account_binding", "portfolio", "src/backend/trading_configuration_service.py", 1, ALL_MODES, True),
)


COMPATIBILITY_ALIASES = (
    CompatibilityAliasDefinition(
        "qmd.stream.scanner_primitives",
        "qmd_gateway",
        "/stream/scanner-primitives",
        "/stream/signals",
        "deprecated",
        "remove after every registered consumer uses /stream/signals",
    ),
)


def _query_plans() -> tuple[QueryPlanDefinition, ...]:
    reference_tables = tuple(
        f"q_live.{table}"
        for group in REFERENCE_TABLE_GROUPS
        for table in group.tables
    )
    return (
        QueryPlanDefinition(
            "qmd.scanner.snapshot.v1",
            "qmd_gateway",
            "src.backend.qmd_gateway_client:qmd_scanner_snapshot",
            ("service://qmd-gateway/scanner",),
            "canonical ticker identity",
            "QMD event time and scanner sequence",
            "QMD processing time",
            "service://qmd-gateway/coverage",
        ),
        QueryPlanDefinition(
            "qmd.compact-events.v4",
            "qmd_gateway",
            "services/qmd-gateway/src/bars.rs:TradeEvent,QuoteEvent,BarRow",
            ("market_sip_compact.events_YYYY", "service://qmd-gateway/live-events"),
            "canonical ticker identity",
            "SIP participant timestamp within the configured causal window",
            "QMD ingest timestamp and watermark",
            "market_sip_compact.events_ordinal_continuity",
        ),
        QueryPlanDefinition(
            "market.daily_session_bars.v1",
            "backend",
            "src.backend.query_plans.market_daily_bars_v1:daily_session_trade_bars",
            ("market_sip_compact.daily_session_bars_by_symbol_time_v1",),
            "canonical ticker with source-ticker fallback only when canonical coverage is absent",
            "New York session date and SIP bar timestamps",
            "available_at_us",
            "market_sip_compact.daily_session_bars_by_symbol_time_v1",
        ),
        QueryPlanDefinition(
            "market.historical_scanner_materialization.v1",
            "backend",
            "src.backend.query_plans.historical_scanner_materialization_v1:scanner_snapshot_materialization,technical_snapshot_materialization,source_revision_query",
            (
                "market_sip_compact.events_YYYY",
                "market_sip_compact.events_ordinal_continuity",
                "market_sip_compact.daily_session_bars_by_symbol_time_v1",
            ),
            "all-universe canonical ticker from the selected compact-event revision",
            "bounded SIP event window and New York session clock",
            "ordinal-continuity updated_at and daily-bar available_at_us",
            "market_sip_compact.events_ordinal_continuity",
        ),
        QueryPlanDefinition(
            "market.historical_scanner_cache.v1",
            "backend",
            "src.backend.query_plans.historical_scanner_cache_v1:snapshot_table_schema,qmd_snapshot_table_schemas,technical_snapshot_table_schema,qmd_snapshot_complete_queries,cached_qmd_rows_query,cached_qmd_signal_events_query,cached_technical_rows_query,cached_scanner_rows_query,latest_cached_scanner_snapshot_query,json_each_row_insert",
            (
                "q_live.canvas_historical_scanner_v1",
                "q_live.canvas_scanner_technical_v3",
                "q_live.canvas_historical_qmd_scanner_v1",
                "q_live.canvas_historical_qmd_signal_event_v1",
                "q_live.canvas_historical_qmd_snapshot_meta_v1",
            ),
            "snapshot clock, calculation/schema version, source revision, and ticker",
            "requested historical snapshot clock",
            "materialized_at_utc after complete cache commit",
            "complete meta row plus exact stored indicator count",
        ),
        QueryPlanDefinition(
            "market.ticker_presentation.v1",
            "backend",
            "src.backend.query_plans.market_ticker_presentation_v1:ticker_presentation",
            (
                "q_live.feature_tradable_universe_v1",
                "q_live.feature_scanner_static_v1",
                "q_live.id_issuer_v1",
                "q_live.market_issuer_company_profile_v1",
                "q_live.market_security_country_v1",
                "q_live.market_presentation_asset_v1",
                "q_live.market_issuer_presentation_selection_v1",
            ),
            "bounded uppercase ticker set to latest symbol and issuer identity",
            "latest universe_date and feature_date",
            "reference inserted_at and last_seen_at_utc",
            "q_live.market_reference_publication_coverage_v1",
        ),
        QueryPlanDefinition(
            "market.tradable_universe.v1",
            "backend",
            "src.backend.query_plans.market_tradable_universe_v1:full_tradable_universe",
            (
                "q_live.feature_tradable_universe_v1",
                "q_live.feature_scanner_static_v1",
                "q_live.id_issuer_v1",
                "q_live.market_presentation_asset_v1",
                "q_live.market_issuer_presentation_selection_v1",
            ),
            "latest canonical symbol, listing, security, and broker conid identity",
            "latest universe_date and feature_date",
            "Reference Gateway inserted_at",
            "q_live.market_reference_publication_coverage_v1",
        ),
        QueryPlanDefinition(
            "market.schema_inventory.v1",
            "backend",
            "src.backend.query_plans.market_schema_inventory_v1:schema_inventory_queries",
            ("system.tables", "system.columns"),
            "current database plus table and column identity",
            "ClickHouse catalog schema revision",
            "query processing time",
            "current ClickHouse system catalog",
        ),
        QueryPlanDefinition(
            "reference.schema_inventory.v1",
            "reference_gateway",
            "services.reference_gateway.table_groups:REFERENCE_TABLE_GROUPS",
            reference_tables,
            "registered table inventory",
            "table schema revision",
            "Reference Gateway publication time",
            "q_live.market_reference_publication_coverage_v1",
        ),
        QueryPlanDefinition(
            "reference.universe_snapshot.v1",
            "reference_gateway",
            "src.backend.historical_scanner_service:historical_scanner_reference_projection",
            ("q_live.feature_tradable_universe_v1", "q_live.feature_scanner_static_v1"),
            "symbol_id + listing_id + validity interval",
            "universe_date",
            "inserted_at",
            "q_live.market_reference_publication_coverage_v1",
        ),
        QueryPlanDefinition(
            "reference.identity_for_symbol.v1",
            "reference_gateway",
            "src.backend.query_plans.reference_ticker_facts_v1:identity_anchor",
            (
                "q_live.id_symbol_interval_v1",
                "q_live.id_symbol_v1",
                "q_live.id_listing_v1",
                "q_live.id_security_v1",
                "q_live.id_issuer_v1",
                "q_live.market_issuer_company_profile_v1",
                "q_live.market_security_country_v1",
            ),
            "source symbol ASOF valid_from <= cutoff < valid_to_exclusive",
            "valid_from",
            "inserted_at",
            "q_live.market_ticker_event_entity_coverage_v1",
        ),
        QueryPlanDefinition(
            "reference.scanner_asof.v1",
            "reference_gateway",
            "src.backend.query_plans.reference_scanner_asof_v1:scanner_reference_projection",
            (
                "q_live.feature_tradable_universe_v1",
                "q_live.feature_scanner_static_v1",
                "q_live.id_security_v1",
                "q_live.id_issuer_v1",
                "q_live.market_security_market_snapshot_v1",
                "q_live.market_security_float_v1",
                "q_live.market_short_interest_v1",
                "q_live.market_security_country_v1",
                "q_live.market_issuer_company_profile_v1",
                "q_live.market_presentation_asset_v1",
                "q_live.market_issuer_presentation_selection_v1",
                "q_live.market_ipo_v1",
                "q_live.market_stock_split_v1",
            ),
            "point-in-time symbol_id from tradable universe",
            "source observation/effective/publication date",
            "published_at_utc or inserted_at",
            "q_live.market_reference_publication_coverage_v1",
            version=3,
        ),
        QueryPlanDefinition(
            "reference.ticker_facts.v1",
            "reference_gateway",
            "src.backend.query_plans.reference_ticker_facts_v1:reference_fact_queries",
            (
                "q_live.id_issuer_identifier_v1",
                "q_live.id_security_identifier_v1",
                "q_live.market_cash_dividend_v1",
                "q_live.market_fails_to_deliver_v1",
                "q_live.market_reg_sho_threshold_v1",
                "q_live.market_security_borrow_v1",
                "q_live.market_security_classification_v1",
                "q_live.market_security_float_v1",
                "q_live.market_security_market_snapshot_v1",
                "q_live.market_issuer_company_profile_v1",
                "q_live.market_security_country_v1",
                "q_live.market_short_interest_v1",
                "q_live.market_short_volume_v1",
                "q_live.market_stock_split_v1",
                "market_sip_compact.daily_session_bars_by_symbol_time_v1",
            ),
            "point-in-time symbol_id/security_id",
            "source effective/trade/settlement date",
            "source publication timestamp or inserted_at",
            "q_live.market_reference_publication_coverage_v1",
        ),
        QueryPlanDefinition(
            "watchlist.external_feature_intervals.v1",
            "backend",
            "src.backend.historical_watchlist_feature_service:historical_watchlist_external_feature_intervals",
            (
                "q_live.feature_tradable_universe_v1",
                "q_live.feature_scanner_static_v1",
                "q_live.id_security_v1",
                "q_live.id_issuer_v1",
                "q_live.market_security_country_v1",
                "q_live.market_issuer_company_profile_v1",
                "q_live.market_security_market_snapshot_v1",
                "q_live.market_security_float_v1",
                "q_live.market_short_interest_v1",
                "q_live.market_ipo_v1",
                "q_live.market_stock_split_v1",
                "q_live.sec_xbrl_company_fact_v3",
            ),
            "point-in-time Watchlist ticker identity inherited from the registered as-of plans",
            "source observation/effective/filing clock",
            "maximum source event and publication clock",
            "complete bounded query result plus source interval content hashes",
        ),
        QueryPlanDefinition(
            "sec.fundamentals_asof.v1",
            "sec_gateway",
            "src.backend.query_plans.sec_fundamentals_asof_v1:fundamental_fact_queries",
            (
                "q_live.sec_xbrl_company_fact_v3",
                "q_live.feature_tradable_universe_v1",
            ),
            "causally resolved CIK or as-of tradable-universe issuer identity",
            "period_end_date",
            "filed_at_utc and recorded_at_utc",
            "q_live.sec_coverage_manifest_v3",
        ),
        QueryPlanDefinition(
            "news.company_asof.v1",
            "backend",
            "src.backend.query_plans.canvas_context_v1:company_news",
            ("q_live.benzinga_news_event_v2", "q_live.news_synthesis_v1"),
            "event-valid company ticker link",
            "published_at",
            "recorded_at",
            "service://news-gateway/coverage",
        ),
        QueryPlanDefinition(
            "news.scanner_company_asof.v1",
            "backend",
            "src.backend.query_plans.canvas_context_v1:scanner_company_news",
            ("q_live.benzinga_news_event_v2", "q_live.news_synthesis_v1"),
            "canonical news ID + issuer-scoped ticker",
            "published_at_utc",
            "synthesis recorded_at",
            "service://news-gateway/coverage",
        ),
        QueryPlanDefinition(
            "news.detail_asof.v1",
            "backend",
            "src.backend.query_plans.news_detail_asof_v1:service_article,trading_article,rendered_article,trading_tickers",
            (
                "q_live.benzinga_news_event_v2",
                "q_live.benzinga_news_rendered_v2",
                "q_live.benzinga_news_ticker_v2",
            ),
            "canonical news ID plus exact provider revision identity",
            "published_at_utc and published_date",
            "News Gateway updated_at_utc",
            "service://news-gateway/coverage",
        ),
        QueryPlanDefinition(
            "news.operations_intraday.v1",
            "backend",
            "src.backend.query_plans.news_operations_v1:intraday_histogram,today_summary,today_rows",
            (
                "q_live.benzinga_news_event_v2",
                "q_live.benzinga_news_rendered_v2",
            ),
            "canonical news ID and exact provider source revision",
            "bounded New York market-day published_at_utc window",
            "News Gateway updated_at_utc",
            "service://news-gateway/coverage",
        ),
        QueryPlanDefinition(
            "news.canvas_asof.v1",
            "backend",
            "src.backend.query_plans.news_canvas_asof_v1:trading_news_queries",
            (
                "q_live.benzinga_news_event_v2",
                "q_live.benzinga_news_rendered_v2",
                "q_live.news_synthesis_v1",
            ),
            "canonical News ID with exact provider source revision",
            "bounded published_at_utc window and stable page cursor",
            "News Gateway updated_at_utc and Synthesis recorded_at",
            "service://news-gateway/coverage",
        ),
        QueryPlanDefinition(
            "sec.filing_asof.v1",
            "backend",
            "src.backend.query_plans.canvas_context_v1:sec_filings",
            ("q_live.sec_filing_v3", "q_live.id_sec_market_bridge_v3"),
            "event-valid CIK bridge",
            "accepted_at",
            "recorded_at",
            "q_live.sec_coverage_manifest_v3",
        ),
        QueryPlanDefinition(
            "sec.operations_intraday.v1",
            "backend",
            "src.backend.query_plans.sec_operations_v1:intraday_histogram,today_summary,today_filings,related_filing_counts,identity_rows_by_cik,filing_detail_queries",
            (
                "q_live.sec_filing_v3",
                "q_live.sec_filing_document_v3",
                "q_live.sec_filing_text_rendered_v3",
                "q_live.sec_xbrl_company_fact_v3",
                "q_live.sec_xbrl_frame_observation_v3",
                "q_live.id_sec_market_bridge_v3",
                "q_live.id_issuer_v1",
                "q_live.id_security_v1",
                "q_live.id_listing_v1",
                "q_live.id_symbol_v1",
            ),
            "CIK plus accession number",
            "bounded New York market-day accepted_at_utc window",
            "SEC Gateway recorded_at_utc",
            "q_live.sec_coverage_manifest_v3",
        ),
        QueryPlanDefinition(
            "sec.canvas_asof.v1",
            "backend",
            "src.backend.query_plans.sec_canvas_v1:filing_list_sql,filing_detail_sql,detail_documents_sql,detail_text_metadata_sql,detail_source_text_metadata_sql,detail_text_page_sql,detail_source_text_page_sql,detail_facts_sql,detail_fact_count_sql",
            (
                "q_live.sec_filing_v3",
                "q_live.sec_disclosure_taxonomy_v3",
                "q_live.sec_filing_document_v3",
                "q_live.sec_filing_entity_v3",
                "q_live.sec_filing_text_rendered_v3",
                "q_live.sec_filing_text_v3",
                "q_live.sec_xbrl_company_fact_v3",
                "q_live.scoped_text_labels_v5",
                "q_live.id_sec_market_bridge_v3",
                "q_live.id_issuer_v1",
                "q_live.id_listing_v1",
                "q_live.id_symbol_v1",
            ),
            "CIK plus accession/document identity with event-valid ticker bridge",
            "accepted_at_utc, source revision, and XBRL filed_at_utc",
            "producer source_revision_at or inserted_at",
            "q_live.sec_coverage_manifest_v3",
        ),
        QueryPlanDefinition(
            "sec.scanner_filing_asof.v1",
            "backend",
            "src.backend.query_plans.canvas_context_v1:scanner_sec_filings",
            ("q_live.sec_filing_v3", "q_live.id_sec_market_bridge_v3"),
            "event-valid CIK bridge to ticker",
            "accepted_at_utc",
            "filing recorded_at and bridge publication",
            "q_live.sec_coverage_manifest_v3",
        ),
        QueryPlanDefinition(
            "sec.ticker_identity_batch.v1",
            "backend",
            "src.backend.query_plans.canvas_context_v1:sec_ticker_identities",
            ("q_live.id_sec_market_bridge_v3",),
            "bounded CIK set to highest-confidence ticker",
            "bridge validity interval",
            "bridge publication time",
            "q_live.market_reference_publication_coverage_v1",
        ),
        QueryPlanDefinition(
            "intelligence.published_consumer.v1",
            "backend",
            "src.backend.query_plans.text_intelligence_consumer_v1:scoped_labels,news_synthesis_by_id",
            (
                "q_live.scoped_text_labels_v5",
                "q_live.news_synthesis_v1",
            ),
            "bounded producer-issued source/document identity",
            "source_timestamp or source published_at_utc",
            "producer updated_at_utc",
            "service://text-intelligence/news-coverage",
        ),
        QueryPlanDefinition(
            "intelligence.news_asof.v1",
            "text_intelligence",
            "service://text-intelligence/news-synthesis-v1",
            ("service://text-intelligence/news-synthesis-v1",),
            "canonical news document_id + ticker identity",
            "source published_at",
            "analysis available_at",
            "service://text-intelligence/news-coverage",
        ),
        QueryPlanDefinition(
            "intelligence.news_synthesis_events.v1",
            "backend",
            "src.backend.news_signal_runtime_service:news_synthesis_events",
            ("q_live.news_synthesis_v1",),
            "canonical news ID plus issuer-view entity and resolved ticker",
            "published_at_utc",
            "updated_at_utc",
            "service://text-intelligence/news-coverage",
        ),
        QueryPlanDefinition(
            "intelligence.news_llm_review_events.v1",
            "backend",
            "src.backend.news_signal_runtime_service:news_llm_review_events",
            ("q_live.news_forecast_funnel_v1", "q_live.news_llm_issuer_review_v1"),
            "canonical news ID plus issuer ticker from validated structured LLM output",
            "published_at_utc",
            "updated_at_utc",
            "service://text-intelligence/news-coverage",
        ),
        QueryPlanDefinition(
            "intelligence.news_reaction_events.v1",
            "backend",
            "src.backend.news_signal_runtime_service:news_reaction_events",
            ("q_live.news_market_hypothesis_v1",),
            "canonical news ID plus issuer ticker from a persisted market hypothesis",
            "published_at_utc",
            "created_at_utc",
            "service://news-hypothesis/hypotheses",
        ),
        QueryPlanDefinition(
            "intelligence.sec_asof.v1",
            "text_intelligence",
            "service://text-intelligence/sec-synthesis-v1",
            ("service://text-intelligence/sec-synthesis-v1",),
            "SEC accession + event-valid issuer bridge",
            "accepted_at",
            "analysis available_at",
            "service://text-intelligence/sec-coverage",
        ),
        QueryPlanDefinition(
            "model.bargpt.prediction.v1",
            "bar_gpt",
            "service://bar-gpt/predictions",
            ("service://qmd/compact-events-batch", "service://qmd-history/event-bars", "artifact://bar-gpt/checkpoint"),
            "ticker + mode-scoped cache authority + checkpoint hash + causal origin",
            "completed model-origin bar timestamp",
            "BarGPT inference publication timestamp",
            "service://bar-gpt/health",
        ),
        QueryPlanDefinition(
            "model.context_asof.v1",
            "model_gateway",
            "service://model-gateway/context-by-artifact",
            ("service://text-embed/embeddings", "service://news-hypothesis/hypotheses"),
            "stable identity + frozen context hash",
            "source event_at",
            "artifact available_at",
            "service://model-gateway/artifact-readiness",
        ),
    )


QUERY_PLANS = _query_plans()


FIELD_OPERATOR_DOCUMENTATION: dict[str, dict[str, object]] = {
    "market.last_price": {
        "source": "QMD's causally accepted eligible-trade state for the security.",
        "calculation": "Uses the most recent eligible trade at or before the market clock. No application-side calculation is applied.",
        "inputs": (),
        "timeframes": ("event",),
    },
    "market.previous_close": {
        "source": "The completed prior regular-session close resolved by QMD for the security and session.",
        "calculation": "Selects the final eligible trade price from the preceding completed regular session.",
        "inputs": (),
        "timeframes": ("session",),
    },
    "market.change_pct": {
        "source": "QMD last price and the completed previous-session close available at the same market clock.",
        "calculation": "((last price / previous close) - 1) x 100. The value remains unavailable when the previous close is missing or not positive.",
        "inputs": ("market.last_price", "market.previous_close"),
        "timeframes": ("1s", "10s", "30s", "1m"),
    },
    "market.change_actual": {
        "source": "QMD last price and the completed previous-session close available at the same market clock.",
        "calculation": "Last price minus previous-session close. The value remains unavailable when either price is missing.",
        "inputs": ("market.last_price", "market.previous_close"),
        "timeframes": ("1s", "10s", "30s", "1m"),
    },
    "market.volume": {
        "source": "Eligible trades accepted by QMD for the current regular trading session.",
        "calculation": "Cumulative sum of eligible trade size from the session boundary through the current market clock.",
        "inputs": (),
        "timeframes": ("event", "1s", "10s", "30s", "1m"),
    },
    "market.session_dollar_volume": {
        "source": "Eligible trades accepted by QMD for the current extended-hours trading session.",
        "calculation": "Cumulative sum of eligible trade price multiplied by size from the 04:00 New York session boundary through the current market clock.",
        "inputs": ("market.volume", "market.last_price"),
        "timeframes": ("event", "1s", "10s", "30s", "1m"),
    },
    "market.relative_volume": {
        "source": "QMD current-session volume and a point-in-time 20-session volume baseline aligned to the same elapsed session interval.",
        "calculation": "Current cumulative session volume divided by the aligned 20-session baseline. A missing or non-positive baseline remains unavailable.",
        "inputs": ("market.volume",),
        "timeframes": ("10s", "30s", "1m"),
    },
    "market.vwap": {
        "source": "Eligible QMD trades from the current regular trading session.",
        "calculation": "Cumulative sum of eligible trade price multiplied by size, divided by cumulative eligible trade size.",
        "inputs": (),
        "timeframes": ("event", "1s", "10s", "30s", "1m"),
    },
    "market.spread_bps": {
        "source": "QMD's current valid national best bid and offer.",
        "calculation": "Ask minus bid, divided by the NBBO midpoint, multiplied by 10,000. Locked or invalid quotes remain unavailable.",
        "inputs": (),
        "timeframes": ("event", "1s", "10s", "30s", "1m"),
    },
    "market.halt_category": {
        "source": "Canonical compact-event condition and indicator references published with the QMD halt transition.",
        "calculation": "Decodes the halt condition and indicator token identifiers through the registered SIP reference catalog and selects the most specific halt category.",
        "inputs": (),
        "timeframes": ("event",),
    },
    "market.halt_direction": {
        "source": "QMD five-bar price change configured at a one-minute physical interval for the halt occurrence.",
        "calculation": "Labels a positive five-minute move Up, a negative move Down, zero Flat, and a missing move Unavailable.",
        "inputs": (),
        "timeframes": ("5m",),
    },
    "market.trade_rate_10s": {
        "source": "Eligible QMD trade events in the trailing 10-second event-time window.",
        "calculation": "Eligible trade-event count in the trailing 10 seconds divided by 10.",
        "inputs": (),
        "timeframes": ("10s",),
    },
    "market.trade_rate_60s": {
        "source": "Eligible QMD trade events in the trailing 60-second event-time window.",
        "calculation": "Eligible trade-event count in the trailing 60 seconds divided by 60.",
        "inputs": (),
        "timeframes": ("60s",),
    },
    "market.event_age_ms": {
        "source": "QMD event time and the current market-clock evaluation time.",
        "calculation": "Current market-clock timestamp minus the latest accepted event timestamp, expressed in milliseconds.",
        "inputs": ("market.event_at",),
        "timeframes": ("event",),
    },
    "market.liquidity_score": {
        "source": "The complete QMD scanner population at one market clock.",
        "calculation": "A 0-100 relative score combining session dollar-volume percentile (45%), trailing 10-second trade-rate percentile (30%), inverse quoted-spread percentile (15%), and displayed NBBO-depth percentile (10%). Only fresh rows with at least $500,000 session dollar volume, one trade per second over ten seconds, and spread no wider than 50 basis points may score 50 or higher.",
        "inputs": ("market.session_dollar_volume", "market.trade_rate_10s", "market.spread_bps"),
        "timeframes": ("scanner_clock",),
    },
    "market.liquidity_rank": {
        "source": "The active QMD scanner population's liquidity scores at the same market clock.",
        "calculation": "Ascending ordinal rank of the registered QMD liquidity score across the complete scanner population; rank 1 is the highest score.",
        "inputs": ("market.liquidity_score",),
        "timeframes": ("scanner_clock",),
    },
    "signal.squeeze_move_pct": {
        "source": "QMD's active event-time bullish squeeze episode for the security.",
        "calculation": "Latest eligible trade price divided by the episode anchor price, minus one, multiplied by 100. The episode expires five minutes after its early-move trigger.",
        "inputs": ("market.last_price",),
        "timeframes": ("event",),
    },
    "signal.squeeze_anchor_price": {
        "source": "The eligible trade price immediately before QMD admitted the early bullish squeeze move.",
        "calculation": "Frozen event-time anchor for one five-minute squeeze episode; it is not a five-minute bar close.",
        "inputs": ("market.last_price",),
        "timeframes": ("event",),
    },
    "signal.squeeze_high_water_pct": {
        "source": "QMD's active event-time bullish squeeze episode.",
        "calculation": "Maximum move percentage observed from the frozen episode anchor through the current observation.",
        "inputs": ("signal.squeeze_move_pct",),
        "timeframes": ("event",),
    },
    "signal.squeeze_episode_expires_at": {
        "source": "QMD's event-time squeeze episode clock.",
        "calculation": "Early-move trigger time plus five minutes; this timestamp expires the episode and does not delay either signal.",
        "inputs": (),
        "timeframes": ("event",),
    },
    "reference.market_cap": {
        "source": "The latest point-in-time market-capitalization publication from Reference Gateway available before evaluation.",
        "calculation": "Uses the provider-published market capitalization without an application-side recomputation.",
        "inputs": (),
        "timeframes": ("1d",),
    },
    "reference.float_shares": {
        "source": "Point-in-time public-float reference data, with SEC public-float evidence retained as a provenance-preserving fallback.",
        "calculation": "Selects the latest valid published float available before evaluation; unavailable sources are not filled by inference.",
        "inputs": (),
        "timeframes": ("1d",),
    },
    "reference.short_interest": {
        "source": "The latest exchange settlement report published before evaluation.",
        "calculation": "Uses the reported open short-position quantity for the applicable settlement date.",
        "inputs": (),
        "timeframes": ("settlement",),
    },
    "reference.short_interest_pct": {
        "source": "Point-in-time reported short interest and public float.",
        "calculation": "Reported short interest divided by point-in-time public float, multiplied by 100. Missing or non-positive float remains unavailable.",
        "inputs": ("reference.short_interest", "reference.float_shares"),
        "timeframes": ("settlement",),
    },
    "reference.days_to_cover": {
        "source": "Point-in-time short-interest report and the reporting source's average daily volume.",
        "calculation": "Reported short interest divided by reported average daily volume.",
        "inputs": ("reference.short_interest",),
        "timeframes": ("settlement",),
    },
    "event.ipo.days_to_event": {
        "source": "The latest causally available IPO event publication from Reference Gateway.",
        "calculation": "IPO event date minus the evaluation date in calendar days; negative values are past events and positive values are upcoming events.",
        "inputs": ("event.ipo.date",),
        "timeframes": ("event",),
    },
    "event.split.days_to_event": {
        "source": "The latest causally available stock-split execution date from Reference Gateway.",
        "calculation": "Split execution date minus the evaluation date in calendar days; negative values are past events and positive values are upcoming events.",
        "inputs": ("event.split.execution_date",),
        "timeframes": ("event",),
    },
    "classification.market_cap": {
        "source": "Point-in-time market capitalization from Reference Gateway.",
        "calculation": "Assigns the market-cap value to the single configured band whose lower bound is inclusive and upper bound is exclusive.",
        "inputs": ("reference.market_cap",),
        "timeframes": ("1d",),
    },
    "classification.float": {
        "source": "Point-in-time public float from Reference Gateway.",
        "calculation": "Assigns public float shares to the single configured float band whose lower bound is inclusive and upper bound is exclusive.",
        "inputs": ("reference.float_shares",),
        "timeframes": ("1d",),
    },
    "classification.sector": {
        "source": "The point-in-time issuer sector stored in q_live.id_issuer_v1.sector.",
        "calculation": "Selects argMax(sector, inserted_at) for the issuer at or before the evaluation cutoff; no numeric thresholding is applied.",
        "inputs": (),
        "timeframes": ("1d",),
    },
    "classification.industry": {
        "source": "The point-in-time issuer industry stored in q_live.id_issuer_v1.industry.",
        "calculation": "Selects argMax(industry, inserted_at) for the issuer at or before the evaluation cutoff; no numeric thresholding is applied.",
        "inputs": (),
        "timeframes": ("1d",),
    },
    "classification.short_pressure": {
        "source": "The point-in-time short-pressure label stored in q_live.feature_scanner_static_v1.short_pressure_label.",
        "calculation": "Selects argMax(short_pressure_label, inserted_at) for the latest scanner feature date at or before the evaluation cutoff; no application-side classification is applied.",
        "inputs": (),
        "timeframes": ("settlement", "1d"),
    },
}


FIELD_OPERATOR_DOCUMENTATION.update({
    "clock.observed_at": {"source": "QMD scanner market_clock.observed_at in UTC.", "calculation": "Reads the RFC-3339 timestamp at which QMD assembled the scanner snapshot.", "inputs": (), "timeframes": ("event",)},
    "clock.utc_date": {"source": "QMD scanner market_clock.utc_date.", "calculation": "Reads the UTC calendar date at the QMD evaluation timestamp.", "inputs": (), "timeframes": ("event",)},
    "clock.utc_time": {"source": "QMD scanner market_clock.utc_time.", "calculation": "Reads the UTC wall-clock time at the QMD evaluation timestamp.", "inputs": (), "timeframes": ("event",)},
    "clock.exchange_date": {"source": "QMD scanner market_clock.exchange_date.", "calculation": "Reads the calendar date after converting the evaluation timestamp to America/New_York.", "inputs": (), "timeframes": ("event",)},
    "clock.exchange_time": {"source": "QMD scanner market_clock.exchange_time.", "calculation": "Reads the wall-clock time after converting the evaluation timestamp to America/New_York.", "inputs": (), "timeframes": ("event",)},
    "clock.trading_date": {"source": "QMD scanner market_clock.trading_date.", "calculation": "Currently publishes the America/New_York calendar date. It does not roll weekends or holidays to another trading session; use is_trading_day to distinguish them.", "inputs": (), "timeframes": ("event",)},
    "clock.timezone": {"source": "QMD scanner market_clock.timezone.", "calculation": "Reads the IANA timezone used by QMD for exchange-local calendar calculations.", "inputs": (), "timeframes": ("event",)},
    "clock.weekday": {"source": "QMD scanner market_clock.weekday.", "calculation": "Reads the full English weekday name derived by QMD from the America/New_York calendar date.", "inputs": (), "timeframes": ("event",)},
    "clock.session_id": {"source": "QMD scanner market_clock.session_id.", "calculation": "Currently uses the America/New_York calendar date as the stable session identity, including closed dates.", "inputs": (), "timeframes": ("event",)},
    "clock.session_phase": {"source": "QMD scanner market_clock.session_phase.", "calculation": "Classifies America/New_York time as premarket (04:00-09:29), regular (09:30-15:59), aftermarket (16:00-19:59), or maintenance.", "inputs": (), "timeframes": ("event",)},
    "market.status": {"source": "QMD market-calendar snapshot.", "calculation": "Returns active when the market calendar admits live collection at the evaluation clock; otherwise closed.", "inputs": (), "timeframes": ("event",)},
    "market.feed_status": {"source": "QMD market-calendar snapshot.", "calculation": "Returns stale when the market-calendar observation exceeds its freshness policy; otherwise ready.", "inputs": (), "timeframes": ("event",)},
})


FIELD_KNOWN_VALUES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "news.composite_sentiment": (
        ("positive", "Positive", "Issuer-specific positive evidence exceeds negative evidence."),
        ("negative", "Negative", "Issuer-specific negative evidence exceeds positive evidence."),
        ("neutral", "Neutral", "No directional issuer evidence is established."),
        ("mixed", "Mixed", "Material positive and negative issuer evidence coexist."),
    ),
    "clock.weekday": tuple(
        (name, name, f"Exchange-local {name}.")
        for name in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    ),
    "clock.month_name": tuple(
        (name, name, f"Exchange-local calendar month {index}.")
        for index, name in enumerate(
            (
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December",
            ),
            start=1,
        )
    ),
    "clock.session_phase": (
        ("premarket", "Premarket", "Exchange-local time from 04:00 through 09:29."),
        ("regular", "Regular", "Regular trading session from 09:30 through 15:59."),
        ("aftermarket", "Aftermarket", "Extended session from 16:00 through 19:59."),
        ("maintenance", "Maintenance", "Weekend or time outside an active collection session."),
    ),
    "market.status": (
        ("active", "Active", "The market calendar admits live collection at this clock."),
        ("closed", "Closed", "The market calendar does not admit live collection at this clock."),
    ),
    "market.feed_status": (
        ("ready", "Ready", "The QMD market-calendar publication is current."),
        ("stale", "Stale", "The QMD market-calendar publication exceeded its freshness policy."),
    ),
    "market.luld_state": (
        ("inside", "Inside bands", "The latest price is inside the estimated LULD bands."),
        ("near", "Near band", "The latest price is near an estimated LULD band."),
        ("outside", "Outside bands", "The latest price is outside an estimated LULD band."),
        ("unavailable", "Unavailable", "Required LULD evidence is not available."),
    ),
    "market.quality_state": (
        ("ready", "Ready", "The QMD scanner snapshot satisfies its market-quality checks."),
        ("stale", "Stale", "The latest admissible market observation exceeded its freshness policy."),
        ("locked", "Locked", "The best bid equals the best ask."),
        ("crossed", "Crossed", "The best bid exceeds the best ask."),
        ("unavailable", "Unavailable", "QMD cannot establish a usable market-quality state."),
    ),
    "reference.float_quality": (
        ("reported", "Reported", "A point-in-time public-float publication is available."),
        ("shares_outstanding_only", "Shares outstanding only", "Only outstanding-share evidence is available; float is not inferred."),
        ("unavailable", "Unavailable", "No admissible public-float evidence is available."),
    ),
    "classification.short_pressure": (
        ("no_short_data", "No short data", "No reported short-interest, days-to-cover, or short-volume evidence is available."),
        ("crowded_short", "Crowded short", "Days to cover is at least 5 or short-volume ratio is at least 50%."),
        ("elevated_short", "Elevated short", "Days to cover is at least 3 or short-volume ratio is at least 35%."),
        ("normal", "Normal", "Available short-pressure evidence remains below the elevated thresholds."),
    ),
    "fundamental.trajectory_label": tuple(
        (value, value, f"Registered financial-trajectory result: {value}.")
        for value in ("Unavailable", "Strong", "Improving", "Stable", "Weak", "Deteriorating")
    ),
    "fundamental.valuation_label": tuple(
        (value, value, f"Registered valuation regime: {value}.")
        for value in ("Not meaningful", "Unavailable", "Discount", "Moderate", "Premium", "Very premium")
    ),
    "xbrl.quality_label": tuple(
        (value, value, f"Registered XBRL evidence-quality result: {value}.")
        for value in ("Insufficient", "Robust", "Strong", "Mixed", "Fragile", "Weak")
    ),
}


TEMPORAL_DERIVED_METHODS: dict[str, tuple[str, tuple[str, ...]]] = {
    "clock.calendar_year": ("Extracts the four-digit year from the exchange-local calendar date.", ("clock.exchange_date",)),
    "clock.calendar_quarter": ("Computes floor((exchange-local month - 1) / 3) + 1.", ("clock.exchange_date",)),
    "clock.month_number": ("Extracts the month number, 1 through 12, from the exchange-local calendar date.", ("clock.exchange_date",)),
    "clock.month_name": ("Formats the exchange-local calendar month using its full English name.", ("clock.exchange_date",)),
    "clock.iso_week": ("Extracts the ISO-8601 week number from the exchange-local calendar date.", ("clock.exchange_date",)),
    "clock.day_of_month": ("Extracts the day number within the exchange-local calendar month.", ("clock.exchange_date",)),
    "clock.day_of_year": ("Extracts the ordinal day, 1 through 366, from the exchange-local calendar date.", ("clock.exchange_date",)),
    "clock.weekday_number": ("Maps the exchange-local weekday to ISO-8601 numbering: Monday=1 through Sunday=7.", ("clock.exchange_date",)),
    "clock.hour": ("Extracts the hour, 0 through 23, from the exchange-local market time.", ("clock.exchange_time",)),
    "clock.minute": ("Extracts the minute, 0 through 59, from the exchange-local market time.", ("clock.exchange_time",)),
    "clock.second": ("Extracts the second, 0 through 59, from the exchange-local market time.", ("clock.exchange_time",)),
    "clock.minutes_since_midnight": ("Computes exchange-local hour x 60 + minute.", ("clock.exchange_time",)),
    "clock.is_weekend": ("Returns true when the exchange-local weekday is Saturday or Sunday.", ("clock.exchange_date",)),
    "clock.is_month_start": ("Returns true when the exchange-local date is the first calendar day of its month.", ("clock.exchange_date",)),
    "clock.is_month_end": ("Returns true when the next exchange-local calendar day belongs to another month.", ("clock.exchange_date",)),
    "clock.is_quarter_start": ("Returns true on the first calendar day of January, April, July, or October.", ("clock.exchange_date",)),
    "clock.is_quarter_end": ("Returns true on the last calendar day of March, June, September, or December.", ("clock.exchange_date",)),
}


FIELD_SOURCE_OVERRIDES: dict[str, tuple[str, tuple[str, ...]]] = {
    "classification.sector": ("q_live.id_issuer_v1", ("sector",)),
    "classification.industry": ("q_live.id_issuer_v1", ("industry",)),
    "classification.short_pressure": ("q_live.feature_scanner_static_v1", ("short_pressure_label",)),
}


DERIVED_FIELD_METHODS: dict[str, str] = {
    "fundamental.free_cash_flow": "Operating cash flow - absolute capital expenditure.",
    "fundamental.gross_margin_pct": "100 x gross profit / aligned revenue.",
    "fundamental.operating_margin_pct": "100 x operating income / aligned revenue.",
    "fundamental.net_margin_pct": "100 x net income / aligned revenue.",
    "fundamental.free_cash_flow_margin_pct": "100 x free cash flow / aligned revenue.",
    "fundamental.return_on_assets_pct": "100 x latest comparable net income / latest assets.",
    "fundamental.return_on_equity_pct": "100 x latest comparable net income / latest positive stockholders' equity.",
    "fundamental.working_capital": "Aligned current assets - aligned current liabilities.",
    "fundamental.current_ratio": "Aligned current assets / aligned current liabilities.",
    "fundamental.debt_to_equity": "Current plus noncurrent borrowings / positive stockholders' equity.",
    "fundamental.net_debt": "Interest-bearing debt - cash and equivalents.",
    "fundamental.interest_coverage": "Operating income / absolute interest expense.",
    "fundamental.revenue_change": "Latest comparable revenue - prior comparable revenue.",
    "fundamental.revenue_growth_pct": "100 x (latest comparable revenue - prior comparable revenue) / absolute prior comparable revenue.",
    "fundamental.earnings_change": "Latest comparable net income - prior comparable net income.",
    "fundamental.earnings_growth_pct": "100 x (latest comparable net income - prior comparable net income) / absolute prior comparable net income.",
    "fundamental.share_change": "Latest comparable weighted-average basic shares - prior comparable shares.",
    "fundamental.share_growth_pct": "100 x (latest weighted-average basic shares - prior comparable shares) / absolute prior comparable shares.",
    "fundamental.dilution_pct": "100 x (diluted shares - basic shares) / basic shares for the aligned fiscal period.",
    "fundamental.cash_conversion": "Operating cash flow / net income for the aligned fiscal period.",
    "fundamental.research_intensity_pct": "100 x research and development expense / aligned revenue.",
    "fundamental.sga_intensity_pct": "100 x selling, general, and administrative expense / aligned revenue.",
    "fundamental.trajectory_score": "Coverage-adjusted weighted mean of profitability (30%), growth (20%), cash quality (20%), balance sheet (20%), and capital discipline (10%) scores.",
    "fundamental.trajectory_label": "Maps the financial trajectory score and evidence coverage to the registered strength label.",
    "fundamental.latest_filing_at": "Maximum causally available SEC filing acceptance timestamp used by the current fundamental analysis.",
    "fundamental.valuation_pe": "Point-in-time market price / causally available diluted earnings per share.",
    "fundamental.valuation_label": "Maps the point-in-time price-to-earnings value to the registered valuation band.",
    "fundamental.profitability_score": "Reads the published 0-100 profitability facet from the causal SEC fundamental analysis.",
    "fundamental.cash_generation_score": "Reads the published 0-100 cash-generation facet from the causal SEC fundamental analysis.",
    "fundamental.balance_sheet_score": "Reads the published 0-100 balance-sheet facet from the causal SEC fundamental analysis.",
    "fundamental.quality_score": "Reads the coverage-adjusted 0-100 XBRL evidence quality score for the selected filing revision.",
    "fundamental.share_base_pressure_pct": "Reads the published share-base pressure percentage from the causal SEC fundamental analysis.",
    "fundamental.share_base_discipline_score": "Reads the published 0-100 capital-discipline score derived from share growth and dilution spread.",
    "xbrl.profitability_score": "Weighted 0-100 score from gross margin (20%), operating margin (30%), net margin (30%), and return on equity (20%).",
    "xbrl.growth_score": "Weighted 0-100 score from comparable revenue growth (55%) and earnings growth (45%).",
    "xbrl.cash_quality_score": "Weighted 0-100 score from free-cash-flow margin (60%) and cash conversion (40%).",
    "xbrl.balance_sheet_score": "Weighted 0-100 score from current ratio (40%), inverse debt-to-equity (35%), and interest coverage (25%).",
    "xbrl.capital_discipline_score": "Weighted inverse 0-100 score from basic-share growth (60%) and dilution spread (40%).",
    "xbrl.quality_coverage_pct": "100 x available effective component weight / total configured component weight.",
    "xbrl.quality_score": "Coverage-adjusted weighted mean of the registered XBRL analysis facets; withheld below the minimum evidence coverage.",
    "xbrl.quality_label": "Maps XBRL quality score and evidence coverage to the registered quality label.",
    "market.quality_state": "Returns QMD's current scanner-quality state for the latest causal market clock.",
    "market.quality_flags": "Returns the set of QMD validation or degradation flags active at the latest causal market clock.",
    "market.degradation_reason": "Returns QMD's explicit reason when scanner data is degraded; otherwise remains unavailable.",
    "signal.news_labeled": "True when Text Intelligence has published a causal news classification for the company event; false otherwise.",
    "signal.sec_labeled": "True when Text Intelligence has published a causal SEC classification for the filing event; false otherwise.",
}


DERIVED_FIELD_INPUTS: dict[str, tuple[str, ...]] = {
    "fundamental.free_cash_flow": ("fundamental.operating_cash_flow", "fundamental.capital_expenditure"),
    "fundamental.gross_margin_pct": ("fundamental.gross_profit", "fundamental.revenue"),
    "fundamental.operating_margin_pct": ("fundamental.operating_income", "fundamental.revenue"),
    "fundamental.net_margin_pct": ("fundamental.net_income", "fundamental.revenue"),
    "fundamental.free_cash_flow_margin_pct": ("fundamental.free_cash_flow", "fundamental.revenue"),
    "fundamental.return_on_assets_pct": ("fundamental.net_income", "fundamental.assets"),
    "fundamental.return_on_equity_pct": ("fundamental.net_income", "fundamental.stockholders_equity"),
    "fundamental.working_capital": ("fundamental.current_assets", "fundamental.current_liabilities"),
    "fundamental.current_ratio": ("fundamental.current_assets", "fundamental.current_liabilities"),
    "fundamental.debt_to_equity": ("fundamental.current_debt", "fundamental.long_term_debt", "fundamental.stockholders_equity"),
    "fundamental.net_debt": ("fundamental.current_debt", "fundamental.long_term_debt", "fundamental.cash"),
    "fundamental.interest_coverage": ("fundamental.operating_income", "fundamental.interest_expense"),
    "fundamental.revenue_change": ("fundamental.revenue",),
    "fundamental.revenue_growth_pct": ("fundamental.revenue",),
    "fundamental.earnings_change": ("fundamental.net_income",),
    "fundamental.earnings_growth_pct": ("fundamental.net_income",),
    "fundamental.share_change": ("fundamental.weighted_average_basic_shares",),
    "fundamental.share_growth_pct": ("fundamental.weighted_average_basic_shares",),
    "fundamental.dilution_pct": ("fundamental.weighted_average_basic_shares", "fundamental.weighted_average_diluted_shares"),
    "fundamental.cash_conversion": ("fundamental.operating_cash_flow", "fundamental.net_income"),
    "fundamental.research_intensity_pct": ("fundamental.research_development", "fundamental.revenue"),
    "fundamental.sga_intensity_pct": ("fundamental.sga_expense", "fundamental.revenue"),
    "fundamental.trajectory_score": ("xbrl.profitability_score", "xbrl.growth_score", "xbrl.cash_quality_score", "xbrl.balance_sheet_score", "xbrl.capital_discipline_score"),
    "fundamental.trajectory_label": ("fundamental.trajectory_score", "xbrl.quality_coverage_pct"),
    "fundamental.valuation_pe": ("market.last_price", "fundamental.diluted_eps"),
    "fundamental.valuation_label": ("fundamental.valuation_pe",),
    "xbrl.quality_label": ("xbrl.quality_score", "xbrl.quality_coverage_pct"),
}


PRESENTATION_ACRONYMS = {
    "ad": "AD",
    "adx": "ADX",
    "alma": "ALMA",
    "apo": "APO",
    "ai": "AI",
    "api": "API",
    "atr": "ATR",
    "avg": "Average",
    "bps": "BPS",
    "cci": "CCI",
    "cdl": "CDL",
    "cik": "CIK",
    "clickhouse": "ClickHouse",
    "cmf": "CMF",
    "cmo": "CMO",
    "conid": "CONID",
    "cusip": "CUSIP",
    "dema": "DEMA",
    "di": "DI",
    "dm": "DM",
    "ema": "EMA",
    "eom": "EOM",
    "etf": "ETF",
    "figi": "FIGI",
    "hma": "HMA",
    "ht": "HT",
    "id": "ID",
    "ibkr": "IBKR",
    "ipo": "IPO",
    "isin": "ISIN",
    "kama": "KAMA",
    "kst": "KST",
    "kvo": "KVO",
    "level1": "Level 1",
    "luld": "LULD",
    "ma": "MA",
    "macd": "MACD",
    "mfi": "MFI",
    "mom": "Momentum",
    "ms": "ms",
    "natr": "NATR",
    "nbbo": "NBBO",
    "nvi": "NVI",
    "obv": "OBV",
    "ofi": "OFI",
    "pct": "%",
    "ppo": "PPO",
    "psar": "PSAR",
    "pvi": "PVI",
    "pvt": "PVT",
    "qmd": "QMD",
    "rest": "REST",
    "roc": "ROC",
    "rsi": "RSI",
    "sec": "SEC",
    "sip": "SIP",
    "sma": "SMA",
    "std": "Standard Deviation",
    "talib": "TA-Lib",
    "tf": "Timeframe",
    "utc": "UTC",
    "vs": "vs",
    "vwap": "VWAP",
    "xbrl": "XBRL",
    "zscore": "Z-Score",
}


def _presentation_words(value: str) -> str:
    tokens = [token for token in value.replace("-", "_").replace(" ", "_").split("_") if token]
    return " ".join(
        (
            token.lower()
            if index > 0 and token.lower() in {"and", "at", "by", "for", "from", "of", "per", "to"}
            else PRESENTATION_ACRONYMS.get(token.lower(), token.capitalize())
        )
        for index, token in enumerate(tokens)
    )


def _field_presentation_label(field_id: str) -> str:
    exact = {
        "clock.weekday": "Market Clock Weekday Name",
        "clock.calendar_year": "Market Clock Calendar Year",
        "clock.calendar_quarter": "Market Clock Calendar Quarter",
        "clock.month_number": "Market Clock Month Number",
        "clock.month_name": "Market Clock Month Name",
        "clock.iso_week": "Market Clock ISO Week",
        "clock.day_of_month": "Market Clock Day of Month",
        "clock.day_of_year": "Market Clock Day of Year",
        "clock.weekday_number": "Market Clock Weekday Number",
        "clock.minutes_since_midnight": "Market Clock Minutes Since Midnight",
    }
    if field_id in exact:
        return exact[field_id]
    parts = [part for part in field_id.split(".") if part]
    if not parts:
        return "Unnamed Field"
    if parts[:2] == ["qmd", "field"]:
        leaf = parts[-1]
        qmd_overrides = {
            "ad": "Accumulation/Distribution",
            "adosc": "Chaikin A/D Oscillator",
            "open": "Bar Open",
            "high": "Bar High",
            "low": "Bar Low",
            "close": "Bar Close",
            "volume": "Bar Volume",
            "ht_dcperiod": "Hilbert Transform Dominant Cycle Period",
            "ht_dcphase": "Hilbert Transform Dominant Cycle Phase",
            "ht_phasor": "Hilbert Transform Phasor",
            "ht_sine": "Hilbert Transform Sine",
            "ht_trendline": "Hilbert Transform Trendline",
            "ht_trendmode": "Hilbert Transform Trend Mode",
        }
        return qmd_overrides.get(leaf, _presentation_words(leaf))
    namespace = parts[0]
    semantic_path = parts[1:]
    clock_labels = {
        "market.status": "Market Status",
        "market.is_open": "Market Is Open",
        "market.is_halted": "Market Is Halted",
        "market.luld_state": "Market LULD State",
        "market.feed_status": "Market Feed Status",
    }
    if field_id in clock_labels:
        return clock_labels[field_id]
    if namespace == "clock":
        return f"Market Clock {_presentation_words('_'.join(semantic_path))}"
    if namespace == "classification":
        return f"{_presentation_words('_'.join(semantic_path))} Classification"
    if namespace == "embedding" and len(semantic_path) >= 2:
        return f"{_presentation_words(semantic_path[0])} Embedding {_presentation_words('_'.join(semantic_path[1:]))}"
    if namespace in {"event", "signal", "indicator", "model"}:
        return _presentation_words("_".join(semantic_path))
    if namespace in {"news", "sec", "coverage", "schedule", "identity", "listing", "relationship", "fundamental", "xbrl"}:
        return f"{_presentation_words(namespace)} {_presentation_words('_'.join(semantic_path))}"
    return _presentation_words(parts[-1])


def _definition_presentation_label(row: dict[str, Any]) -> str:
    registry_id = str(row.get("registry_id") or "")
    if str(row.get("kind") or "") == "field" or registry_id.startswith((
        "classification.", "embedding.", "event.", "indicator.", "model.", "news.", "sec.", "signal.",
    )):
        return _field_presentation_label(registry_id)
    return str(row.get("presentation_label") or row.get("label") or _presentation_words(registry_id))


def _qualify_duplicate_presentation_labels(rows: list[dict[str, Any]]) -> None:
    by_label: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        label = _definition_presentation_label(row)
        row["presentation_label"] = label
        if str(row.get("kind") or "") in {"field", "derivation", "signal"}:
            by_label.setdefault(label.casefold(), []).append(row)
    for duplicates in by_label.values():
        if len(duplicates) < 2:
            continue
        for row in duplicates:
            registry_id = str(row.get("registry_id") or "")
            namespace = registry_id.split(".", 1)[0]
            qualifier = "QMD" if namespace == "qmd" else _presentation_words(namespace)
            current = str(row["presentation_label"])
            if not current.casefold().startswith(f"{qualifier} ".casefold()):
                row["presentation_label"] = f"{qualifier} {current}"


def _operator_source_summary(owner: str, source_path: str) -> str:
    if source_path == "service://qmd/scanner":
        return "QMD scanner state built from causally accepted quotes, eligible trades, and session references."
    if source_path.startswith("q_live."):
        return f"A point-in-time Reference Gateway publication from {source_path.removeprefix('q_live.').replace('_', ' ')}."
    if source_path.startswith("service://"):
        service = source_path.removeprefix("service://").split("/")[0].replace("-", " ")
        return f"The causally available {service} service output published before evaluation."
    if source_path.startswith("derived://"):
        return f"A registered {owner.replace('_', ' ')} derivation from causally available inputs."
    return f"The latest causally available publication owned by {owner.replace('_', ' ')}."


def _operator_calculation_summary(field_id: str, provenance: str, source_columns: tuple[str, ...]) -> str:
    registered_method = DERIVED_FIELD_METHODS.get(field_id) or TEMPORAL_DERIVED_METHODS.get(field_id, ("", ()))[0]
    if registered_method:
        return registered_method
    readable_columns = ", ".join(column.replace("_", " ") for column in source_columns)
    if provenance in {"raw", "reported"}:
        return f"Uses the published {readable_columns} value without an application-side calculation."
    if provenance == "model":
        return f"Reads {_field_presentation_label(field_id)} from the registered versioned model artifact; no application-side transformation is applied."
    return "The registered producer derives this value from its declared inputs; an exact method has not yet been published in the operator documentation contract."


def _field(
    field_id: str,
    group: str,
    owner: str,
    source_path: str,
    query_plan_id: str,
    *,
    value_type: str = "number",
    unit: str = "scalar",
    entity_grain: str = "symbol_interval",
    source_columns: Iterable[str] = (),
    event_at: str = "source effective timestamp",
    available_at: str = "source publication timestamp",
    ttl_seconds: int | None = 86_400,
    publication_cadence: str = "publication_driven",
    historical_support: str = "point_in_time",
    modes: tuple[str, ...] = ALL_MODES,
    provenance: str = "raw",
    coverage_query_plan: str = "reference.schema_inventory.v1",
    null_reasons: tuple[str, ...] = (
        "not_published",
        "outside_coverage",
        "identity_unresolved",
        "stale",
        "not_applicable",
    ),
    status: str = "implemented",
    source_summary: str = "",
    calculation_summary: str = "",
    input_field_ids: Iterable[str] = (),
    timeframes: Iterable[str] = (),
    known_values: Iterable[tuple[str, str, str]] = (),
    interval_semantics: str = "",
    aggregation_functions: Iterable[str] = (),
    default_aggregation: str = "",
    intrinsic_aggregation: str = "",
    aggregation_runtime_fields: Iterable[tuple[str, str]] = (),
    presentation_value_type: str = "",
    label: str = "",
) -> FieldDefinition:
    label = label or field_id.split(".")[-1].replace("_", " ").title()
    columns = tuple(source_columns) or (field_id.split(".")[-1],)
    operator_documentation = FIELD_OPERATOR_DOCUMENTATION.get(field_id, {})
    resolved_known_values = tuple(known_values or FIELD_KNOWN_VALUES.get(field_id, ()))
    return FieldDefinition(
        field_id=field_id,
        label=label,
        presentation_label=_field_presentation_label(field_id),
        group=group,
        value_type=value_type,
        unit=unit,
        entity_grain=entity_grain,
        owner=owner,
        source_path=source_path,
        source_columns=columns,
        query_plan_id=query_plan_id,
        identity_join="point-in-time symbol/security/issuer identity",
        event_at=event_at,
        available_at=available_at,
        ttl_seconds=ttl_seconds,
        publication_cadence=publication_cadence,
        historical_support=historical_support,
        modes=modes,
        provenance=provenance,
        coverage_query_plan=coverage_query_plan,
        freshness_policy="ttl" if ttl_seconds is not None else "source_revision",
        null_reasons=null_reasons,
        status=status,
        source_summary=(
            source_summary
            or str(operator_documentation.get("source") or "")
            or _operator_source_summary(owner, source_path)
        ),
        calculation_summary=(
            calculation_summary
            or str(operator_documentation.get("calculation") or "")
            or _operator_calculation_summary(field_id, provenance, columns)
        ),
        input_field_ids=tuple(
            input_field_ids
            or operator_documentation.get("inputs")
            or TEMPORAL_DERIVED_METHODS.get(field_id, ("", ()))[1]
            or DERIVED_FIELD_INPUTS.get(field_id)
            or ()
        ),
        timeframes=tuple(timeframes or operator_documentation.get("timeframes") or ()),
        known_values=resolved_known_values,
        interval_semantics=interval_semantics,
        aggregation_functions=tuple(aggregation_functions),
        default_aggregation=default_aggregation,
        intrinsic_aggregation=intrinsic_aggregation,
        aggregation_runtime_fields=tuple(aggregation_runtime_fields),
        presentation_value_type=presentation_value_type or _presentation_value_type(field_id, value_type, unit, resolved_known_values),
    )


def _presentation_value_type(field_id: str, value_type: str, unit: str, known_values: tuple[tuple[str, str, str], ...] = ()) -> str:
    """Return the registered UI presentation primitive without changing computation type."""
    key = field_id.lower()
    normalized_unit = unit.lower()
    normalized_type = value_type.lower()
    if known_values or normalized_type in {"boolean", "bool", "category", "enum"}:
        return "boolean" if normalized_type in {"boolean", "bool"} else "category"
    if normalized_unit == "timestamp": return "datetime"
    if normalized_unit == "date": return "date"
    if normalized_unit == "time": return "time"
    if normalized_unit == "percent": return "percent"
    if normalized_unit == "basis_points": return "basis_points"
    if normalized_unit == "currency":
        return "price" if any(token in key for token in ("price", "open", "high", "low", "close", "vwap", "bid", "ask")) else "money"
    if normalized_unit in {"shares", "count", "events"}: return "quantity"
    if normalized_unit in {"multiple", "ratio", "events_per_second"}: return "ratio"
    if normalized_unit == "score": return "score"
    if normalized_type in {"integer", "int"}: return "integer"
    if any(token in key.split(".")[-1] for token in ("status", "state", "phase", "direction", "category", "class", "role", "origin")): return "category"
    if any(token in key.split(".")[-1] for token in ("ticker", "symbol", "identifier", "_id")): return "identifier"
    return "text" if normalized_type in {"string", "json"} else "ratio"


def _bar_gpt_fields() -> list[FieldDefinition]:
    price_targets = tuple(
        f"{family}_{component}_return"
        for family in ("trade", "bid", "ask")
        for component in ("open", "high", "low", "close")
    )
    continuous = (*price_targets, "trade_realized_volatility", "log_trade_volume", "log_trade_count")
    availability = (
        "trade_available", "bid_available", "ask_available", "quote_pair_available",
        "halt_pause_within_horizon", "resume_within_horizon",
        "news_risk_within_horizon", "luld_limit_state_within_horizon",
    )
    horizons = ("5s", "30s", "1m", "5m", "15m", "1h")
    views = ("1s", "5s", "10s", "30s", "1m", "5m", "30m", "1h")
    classes = ("negative", "neutral", "positive")
    gap_classes = (
        "one_interval", "two_intervals", "three_to_five_intervals",
        "six_to_thirty_intervals", "more_than_thirty_intervals", "cross_session",
    )
    rows: list[FieldDefinition] = []

    def add(field_id: str, *, unit: str = "scalar", timeframe: str) -> None:
        rows.append(_field(
            field_id, "bar_gpt_forecast", "bar_gpt", "service://bar-gpt/predictions",
            "model.bargpt.prediction.v1", unit=unit, entity_grain="security_model_origin",
            event_at="completed causal model-origin bar timestamp",
            available_at="BarGPT inference publication timestamp", ttl_seconds=None,
            publication_cadence="completed_1s_bar_or_manual_request", provenance="model",
            coverage_query_plan="model.bargpt.prediction.v1", timeframes=(timeframe,),
            source_summary="BarGPT versioned checkpoint output from a causal full-prefix inference pass.",
            calculation_summary="Raw fields preserve checkpoint head values; probability and value fields are explicitly named decoded projections.",
            label=_bar_gpt_field_label(field_id),
        ))

    for version in ("v2", "v3"):
        for horizon in horizons:
            for target in continuous:
                unit = "currency" if target in price_targets else "count" if target.startswith("log_") else "ratio"
                for quantile in ("q10", "q50", "q90"):
                    prefix = f"model.bargpt.{version}.physical.{horizon}.{target}.{quantile}"
                    add(f"{prefix}.raw", timeframe=horizon)
                    add(f"{prefix}.value", unit=unit, timeframe=horizon)
            for target in availability:
                prefix = f"model.bargpt.{version}.physical.{horizon}.{target}"
                add(f"{prefix}.logit", timeframe=horizon)
                add(f"{prefix}.probability", unit="probability", timeframe=horizon)
            if version == "v2":
                for target in price_targets:
                    for label in classes:
                        prefix = f"model.bargpt.v2.physical.{horizon}.{target}.class_{label}"
                        add(f"{prefix}.logit", timeframe=horizon)
                        add(f"{prefix}.probability", unit="probability", timeframe=horizon)
        for view in views:
            for target in continuous:
                prefix = f"model.bargpt.{version}.next_bar.{view}.{target}"
                unit = "currency" if target in price_targets else "count" if target.startswith("log_") else "ratio"
                add(f"{prefix}.raw", timeframe=view)
                add(f"{prefix}.value", unit=unit, timeframe=view)
            for target in availability[:4]:
                prefix = f"model.bargpt.{version}.next_bar.{view}.{target}"
                add(f"{prefix}.logit", timeframe=view)
                add(f"{prefix}.probability", unit="probability", timeframe=view)
            if version == "v2":
                for target in price_targets:
                    for label in classes:
                        prefix = f"model.bargpt.v2.next_bar.{view}.{target}.class_{label}"
                        add(f"{prefix}.logit", timeframe=view)
                        add(f"{prefix}.probability", unit="probability", timeframe=view)
            else:
                for label in gap_classes:
                    add(f"model.bargpt.v3.next_bar.{view}.gap_logit.{label}", timeframe=view)
                    add(f"model.bargpt.v3.next_bar.{view}.gap_probability.{label}", unit="probability", timeframe=view)
    return rows


def _bar_gpt_field_label(field_id: str) -> str:
    """Return a semantic label while preserving the immutable model field ID."""

    parts = field_id.split(".")
    if len(parts) < 5 or parts[:2] != ["model", "bargpt"]:
        return _field_presentation_label(field_id)
    version = parts[2].upper()
    product = parts[3]
    path = parts[4:]
    representation = path[-1]
    representation_labels = {
        "raw": "Raw head",
        "value": "Decoded value",
        "logit": "Raw logit",
        "probability": "Decoded probability",
    }
    if product == "physical" and len(path) >= 3:
        timeframe = _presentation_words(path[0])
        semantic = path[1:-1]
        quantile = semantic[-1] if semantic and re.fullmatch(r"q\d{2}", semantic[-1]) else ""
        if quantile:
            semantic = semantic[:-1]
        target_key = "_".join(semantic)
        target = _presentation_words(target_key)
        if representation == "value" and target_key.endswith("_return"):
            target = _presentation_words(target_key.removesuffix("_return") + "_forecast_price")
        quantile_label = {
            "q10": "Lower quantile (q10)",
            "q50": "Median quantile (q50)",
            "q90": "Upper quantile (q90)",
        }.get(quantile, quantile.upper())
        components = [f"BarGPT {version}", f"Physical {timeframe}", target]
        if quantile_label:
            components.append(quantile_label)
        components.append(representation_labels.get(representation, _presentation_words(representation)))
        return " · ".join(components)
    if product == "next_bar" and len(path) >= 3:
        view = _presentation_words(path[0])
        semantic = path[1:-1]
        if semantic and semantic[0] in {"gap_logit", "gap_probability"}:
            representation = "logit" if semantic[0] == "gap_logit" else "probability"
            semantic = ["gap", path[-1]]
        target = _presentation_words("_".join(semantic))
        if representation == "value" and "_".join(semantic).endswith("_return"):
            target = _presentation_words("_".join(semantic).removesuffix("_return") + "_forecast_price")
        return " · ".join((
            f"BarGPT {version}", f"Next sparse {view} bar", target,
            representation_labels.get(representation, _presentation_words(representation)),
        ))
    return _field_presentation_label(field_id)


def _fields() -> tuple[FieldDefinition, ...]:
    rows: list[FieldDefinition] = []

    for field_id, value_type, unit in (
        ("market.last_price", "number", "currency"),
        ("market.previous_close", "number", "currency"),
        ("market.change_actual", "number", "currency"),
        ("market.change_pct", "number", "percent"),
        ("market.volume", "number", "shares"),
        ("market.session_dollar_volume", "number", "currency"),
        ("market.relative_volume", "number", "multiple"),
        ("market.vwap", "number", "currency"),
        ("market.spread_bps", "number", "basis_points"),
        ("market.halt_category", "string", "category"),
        ("market.halt_direction", "string", "category"),
        ("market.trade_rate_10s", "number", "events_per_second"),
        ("market.trade_rate_60s", "number", "events_per_second"),
        ("market.liquidity_score", "number", "score"),
        ("market.event_at", "string", "timestamp"),
        ("market.event_age_ms", "number", "milliseconds"),
        ("market.quality_state", "string", "state"),
        ("market.quality_flags", "json", "flags"),
        ("market.degradation_reason", "string", "text"),
        ("market.liquidity_rank", "number", "rank"),
        ("signal.squeeze_move_pct", "number", "percent"),
        ("signal.squeeze_anchor_price", "number", "currency"),
        ("signal.squeeze_high_water_pct", "number", "percent"),
        ("signal.squeeze_episode_expires_at", "string", "timestamp"),
        ("clock.observed_at", "string", "timestamp"),
        ("clock.utc_date", "string", "date"),
        ("clock.utc_time", "string", "time"),
        ("clock.exchange_date", "string", "date"),
        ("clock.exchange_time", "string", "time"),
        ("clock.trading_date", "string", "date"),
        ("clock.timezone", "string", "timezone"),
        ("clock.weekday", "string", "category"),
        ("clock.session_id", "string", "identity"),
        ("clock.session_phase", "string", "state"),
        ("clock.session_open_at", "string", "timestamp"),
        ("clock.session_close_at", "string", "timestamp"),
        ("clock.minutes_since_open", "number", "minutes"),
        ("clock.minutes_until_close", "number", "minutes"),
        ("clock.is_trading_day", "boolean", "boolean"),
        ("clock.is_early_close", "boolean", "boolean"),
        ("market.status", "string", "state"),
        ("market.is_open", "boolean", "boolean"),
        ("market.is_halted", "boolean", "boolean"),
        ("market.luld_state", "string", "state"),
        ("market.feed_status", "string", "state"),
    ):
        rows.append(_field(
            field_id,
            "market_clock" if field_id.startswith("clock.") else "qmd_scanner",
            "qmd_gateway",
            "service://qmd/scanner",
            "qmd.scanner.snapshot.v1",
            value_type=value_type,
            unit=unit,
            entity_grain="security_at_market_clock",
            ttl_seconds=60,
            publication_cadence="event_driven",
            historical_support=(
                "live_only" if field_id.startswith("signal.squeeze_") else "point_in_time"
            ),
            provenance=(
                "raw"
                if field_id in {"market.last_price", "market.volume", "market.event_at"}
                or field_id.startswith("clock.")
                or field_id in {"market.status", "market.is_open", "market.is_halted", "market.feed_status"}
                else "derived"
            ),
            coverage_query_plan="qmd.scanner.snapshot.v1",
            status="implemented",
        ))

    # Decoded compact SIP event members are first-class source observations.
    # Numeric members with an exact BarRow equivalent may be instantiated in a
    # Rule Set as a window plus aggregation. Other members remain discoverable
    # in the Data Catalog but fail closed for Market Discovery execution.
    event_windows = ("100ms", "1s", "1m", "1h", "1d", "1w", "1mo")
    event_specs = (
        ("trade.price", "number", "currency", ("first", "last", "min", "max", "volume_weighted_mean"), "last", (("first", "open"), ("last", "close"), ("min", "low"), ("max", "high"), ("volume_weighted_mean", "vwap"))),
        ("trade.size", "number", "shares", ("sum", "mean", "median", "max", "count"), "sum", (("sum", "volume"), ("mean", "avg_trade_size"), ("median", "median_trade_size"), ("max", "max_trade_size"), ("count", "trade_count"))),
        ("trade.notional", "number", "currency", ("sum",), "sum", (("sum", "dollar_volume"),)),
        ("trade.event_count", "integer", "count", ("count",), "count", (("count", "trade_count"),)),
        ("quote.bid_price", "number", "currency", ("first", "last", "min", "max"), "last", (("first", "bid_open"), ("last", "bid_close"), ("min", "bid_low"), ("max", "bid_high"))),
        ("quote.ask_price", "number", "currency", ("first", "last", "min", "max"), "last", (("first", "ask_open"), ("last", "ask_close"), ("min", "ask_low"), ("max", "ask_high"))),
        ("quote.mid_price", "number", "currency", ("first", "last", "min", "max"), "last", (("first", "mid_open"), ("last", "mid_close"), ("min", "mid_low"), ("max", "mid_high"))),
        ("quote.spread", "number", "currency", ("first", "last", "min", "max", "mean"), "mean", (("first", "spread_open"), ("last", "spread_close"), ("min", "spread_low"), ("max", "spread_high"), ("mean", "spread_mean"))),
        ("quote.bid_size", "number", "shares", ("mean",), "mean", (("mean", "quoted_bid_size_mean"),)),
        ("quote.ask_size", "number", "shares", ("mean",), "mean", (("mean", "quoted_ask_size_mean"),)),
        ("quote.event_count", "integer", "count", ("count",), "count", (("count", "quote_count"),)),
    )
    for field_id, value_type, unit, functions, default, runtime_fields in event_specs:
        rows.append(_field(
            field_id, "qmd_trade_events" if field_id.startswith("trade.") else "qmd_quote_events",
            "qmd_gateway", "qmd://compact-events/trade" if field_id.startswith("trade.") else "qmd://compact-events/quote",
            "qmd.compact-events.v4", value_type=value_type, unit=unit,
            entity_grain="security_event", event_at="SIP participant timestamp",
            available_at="QMD ingest timestamp", ttl_seconds=None,
            publication_cadence="event_driven", historical_support="point_in_time",
            provenance="raw", coverage_query_plan="qmd.compact-events.v4",
            timeframes=event_windows, interval_semantics="event_window",
            aggregation_functions=functions, default_aggregation=default,
            aggregation_runtime_fields=runtime_fields,
            calculation_summary=f"Aggregates the decoded {field_id} event member over the Rule Set window using the selected compatible function.",
        ))
    for prefix, names in {
        "trade": ("conditions", "exchange", "ingest_ts", "participant_ts", "sequence", "tape", "ticker", "trade_id", "trf_id", "trf_ts", "ts"),
        "quote": ("ask_exchange", "bid_exchange", "conditions", "indicators", "ingest_ts", "sequence", "tape", "ticker", "ts"),
    }.items():
        for name in names:
            value_type = "integer" if name in {"sequence", "tape", "trade_id", "trf_id"} else "string"
            unit = "timestamp" if name.endswith("_ts") or name == "ts" else "identity" if name == "ticker" else "category"
            rows.append(_field(
                f"{prefix}.{name}", f"qmd_{prefix}_events", "qmd_gateway",
                f"qmd://compact-events/{prefix}", "qmd.compact-events.v4",
                value_type=value_type, unit=unit, entity_grain="security_event",
                event_at="SIP participant timestamp", available_at="QMD ingest timestamp",
                ttl_seconds=None, publication_cadence="event_driven", provenance="raw",
                coverage_query_plan="qmd.compact-events.v4", status="integration_pending",
                timeframes=event_windows, interval_semantics="event_window",
                source_summary=f"Decoded {prefix} event member published by QMD compact-events v4.",
                calculation_summary="No aggregation-safe Market Discovery projection is registered; the source member remains available for inspection and future recipes.",
            ))

    for field_id, value_type, unit in (
        ("clock.calendar_year", "integer", "year"),
        ("clock.calendar_quarter", "integer", "quarter"),
        ("clock.month_number", "integer", "month"),
        ("clock.month_name", "string", "category"),
        ("clock.iso_week", "integer", "week"),
        ("clock.day_of_month", "integer", "day"),
        ("clock.day_of_year", "integer", "day"),
        ("clock.weekday_number", "integer", "day_index"),
        ("clock.hour", "integer", "hour"),
        ("clock.minute", "integer", "minute"),
        ("clock.second", "integer", "second"),
        ("clock.minutes_since_midnight", "integer", "minutes"),
        ("clock.is_weekend", "boolean", "boolean"),
        ("clock.is_month_start", "boolean", "boolean"),
        ("clock.is_month_end", "boolean", "boolean"),
        ("clock.is_quarter_start", "boolean", "boolean"),
        ("clock.is_quarter_end", "boolean", "boolean"),
    ):
        rows.append(_field(
            field_id,
            "market_clock",
            "backend",
            "derived://qmd-market-clock",
            "qmd.scanner.snapshot.v1",
            value_type=value_type,
            unit=unit,
            entity_grain="security_at_market_clock",
            source_columns=TEMPORAL_DERIVED_METHODS[field_id][1],
            ttl_seconds=60,
            publication_cadence="event_driven",
            provenance="derived",
            coverage_query_plan="qmd.scanner.snapshot.v1",
            timeframes=("event",),
        ))

    reference_specs = {
        "identity": (
            "q_live.id_symbol_interval_v1",
            "reference.identity_for_symbol.v1",
            ("issuer_id", "security_id", "listing_id", "symbol_id", "symbol", "company_name", "security_name", "composite_figi", "share_class_figi", "cik", "conid", "cusip", "isin", "valid_from", "valid_to_exclusive", "previous_symbol", "current_symbol"),
        ),
        "listing": (
            "q_live.id_listing_v1",
            "reference.identity_for_symbol.v1",
            ("exchange", "primary_exchange", "currency", "asset_class", "security_type", "ticker_type"),
        ),
        "tradability": (
            "q_live.feature_tradable_universe_v1",
            "reference.universe_snapshot.v1",
            ("is_tradable", "block_reason", "issue_count"),
        ),
        "country": (
            "q_live.market_security_country_v1",
            "reference.scanner_asof.v1",
            ("listing", "issuer_legal", "headquarters", "issue", "effective"),
        ),
        "presentation": (
            "q_live.market_issuer_presentation_selection_v1",
            "reference.scanner_asof.v1",
            ("logo_url", "asset_status", "source", "kind", "selection_revision", "quality_class"),
        ),
    }
    string_fields = {"symbol", "company_name", "security_name", "composite_figi", "share_class_figi", "cik", "conid", "cusip", "isin", "previous_symbol", "current_symbol", "exchange", "primary_exchange", "currency", "asset_class", "security_type", "ticker_type", "block_reason", "listing", "issuer_legal", "headquarters", "issue", "effective", "logo_url", "asset_status", "source", "kind", "selection_revision", "quality_class"}
    for group, (source, plan, names) in reference_specs.items():
        for name in names:
            rows.append(_field(f"{group}.{name}", group, "reference_gateway", source, plan, value_type="string" if name in string_fields else "boolean" if name == "is_tradable" else "number", coverage_query_plan="reference.schema_inventory.v1"))

    market_reference = {
        "market_security_market_snapshot_v1": ("market_cap", "shares_outstanding"),
        "market_security_float_v1": ("float_shares", "float_source", "float_quality"),
        "market_short_interest_v1": ("short_interest", "short_interest_pct", "days_to_cover"),
        "market_short_volume_v1": ("short_volume", "short_volume_pct"),
        "market_fails_to_deliver_v1": ("fails_to_deliver", "ftd_value"),
        "market_reg_sho_threshold_v1": ("reg_sho_threshold",),
        "market_security_borrow_v1": ("borrow_status", "borrow_shares", "borrow_fee"),
    }
    for table, names in market_reference.items():
        for name in names:
            live_only = name.startswith("borrow_")
            rows.append(_field(
                f"reference.{name}",
                "market_reference",
                "reference_gateway",
                f"q_live.{table}",
                "reference.scanner_asof.v1",
                value_type="string" if name in {"float_source", "float_quality", "borrow_status"} else "boolean" if name == "reg_sho_threshold" else "number",
                unit="shares" if name in {"shares_outstanding", "float_shares", "short_interest", "short_volume", "fails_to_deliver", "borrow_shares"} else "percent" if name.endswith("_pct") or name == "borrow_fee" else "currency" if name in {"market_cap", "ftd_value"} else "scalar",
                historical_support="live_observation_only" if live_only else "point_in_time",
                modes=("live", "paper") if live_only else ALL_MODES,
                status="live_only" if live_only else "implemented",
                coverage_query_plan="reference.scanner_asof.v1",
            ))

    for name in ("sector", "industry", "market_cap", "float", "short_pressure"):
        rows.append(_field(f"classification.{name}", "classification", "backend", "derived://reference-classification", "reference.scanner_asof.v1", value_type="string", provenance="derived"))

    corporate_events = {
        "split": ("q_live.market_stock_split_v1", ("execution_date", "from", "to", "factor", "days_to_event")),
        "dividend": ("q_live.market_cash_dividend_v1", ("ex_date", "amount", "currency")),
        "ipo": ("q_live.market_ipo_v1", ("date", "status", "days_to_event")),
        "ticker_change": ("q_live.market_ticker_event_v1", ("event_type", "effective_date", "old_symbol", "new_symbol")),
    }
    for family, (source, names) in corporate_events.items():
        for name in names:
            scanner_distance = name == "days_to_event" and family in {"ipo", "split"}
            scanner_projection = scanner_distance or family == "ipo"
            rows.append(_field(
                f"event.{family}.{name}",
                "corporate_event",
                "backend" if scanner_projection else "reference_gateway",
                "derived://reference-scanner-event-distance" if scanner_distance else source,
                "reference.scanner_asof.v1" if scanner_projection else "reference.ticker_facts.v1",
                value_type="string" if name in {"execution_date", "ex_date", "currency", "date", "status", "event_type", "effective_date", "old_symbol", "new_symbol"} else "number",
                entity_grain="security_event",
                ttl_seconds=None,
                provenance="derived" if scanner_distance else "reported",
                coverage_query_plan="reference.scanner_asof.v1" if scanner_projection else "reference.ticker_facts.v1",
            ))

    diagnostic_specs = {
        "quality": ("q_live.id_mapping_issue_v1", ("mapping_issue_count", "mapping_issue_types", "mapping_blocked", "source_mapping_state", "source_mapping_evidence")),
        "relationship": ("q_live.id_issuer_relationship_v1", ("issuer_type", "valid_from", "valid_to")),
        "sec": ("q_live.id_sec_market_bridge_v3", ("bridge_state", "bridge_reason", "bridge_version")),
        "coverage": ("q_live.market_reference_publication_coverage_v1", ("reference_source", "window_start", "window_end", "state", "ticker_event_entity_state")),
        "schedule": ("q_live.market_reference_source_schedule_v1", ("source", "next_due_at", "last_completed_at", "state")),
    }
    for group, (source, names) in diagnostic_specs.items():
        for name in names:
            rows.append(_field(f"{group}.{name}", "quality_and_coverage", "reference_gateway", source, "reference.schema_inventory.v1", value_type="boolean" if name.endswith("blocked") else "number" if name.endswith("count") or name.endswith("version") else "string", entity_grain="publication_or_identity", ttl_seconds=3_600))

    news_names = ("latest_at", "count", "recency", "latest_title", "document_id", "source_id", "publisher", "url", "topic", "event_type", "entities", "relationships", "direction", "score", "confidence", "impact", "uncertainty", "horizon", "eligible", "expires_at")
    for name in news_names:
        semantic = name in {"topic", "event_type", "entities", "relationships", "direction", "score", "confidence", "impact", "uncertainty", "horizon", "eligible", "expires_at"}
        rows.append(_field(f"news.{name}", "news", "text_intelligence" if semantic else "news_gateway", "service://text-intelligence/news-synthesis-v1" if semantic else "service://news-gateway/canonical-company-news", "intelligence.news_asof.v1" if semantic else "news.company_asof.v1", value_type="number" if name in {"count", "recency", "score", "confidence", "impact", "uncertainty"} else "boolean" if name == "eligible" else "json" if name in {"entities", "relationships"} else "string", entity_grain="company_news_event", ttl_seconds=900, publication_cadence="event_driven", status="integration_pending" if semantic else "implemented"))

    for field_id, value_type, unit, source_columns in (
        ("news.composite_sentiment", "string", "category", ("issuer_views.composite_sentiment",)),
        ("news.positive_strength", "integer", "ordinal", ("issuer_views.positive_strength",)),
        ("news.negative_strength", "integer", "ordinal", ("issuer_views.negative_strength",)),
        ("news.forecast_trigger_eligible", "boolean", "boolean", ("eligibility.eligible",)),
        ("news.canonical_news_id", "string", "identity", ("canonical_news_id",)),
        ("news.published_at", "string", "timestamp", ("published_at_utc",)),
    ):
        rows.append(_field(
            field_id,
            "news_synthesis",
            "text_intelligence",
            "q_live.news_synthesis_v1",
            "intelligence.news_synthesis_events.v1",
            value_type=value_type,
            unit=unit,
            entity_grain="issuer_news_event",
            source_columns=source_columns,
            event_at="published_at_utc",
            available_at="news_synthesis_v1.updated_at_utc",
            ttl_seconds=None,
            publication_cadence="event_driven",
            provenance="reported",
            coverage_query_plan="intelligence.news_synthesis_events.v1",
            timeframes=("event",),
            calculation_summary=(
                "Projects the exact issuer-specific value from the validated News Synthesis V1 document; "
                "forecast eligibility is selected for product=forecast_trigger and the same entity_id."
            ),
        ))

    for field_id, value_type, unit, source_column in (
        ("news.deepfm.eligible_probability", "number", "probability", "eligible_probability"),
        ("news.deepfm.forecast_eligible", "boolean", "boolean", "forecast_eligibility"),
        ("news.deepfm.status", "string", "category", "stage"),
        ("news.llm.review_complete", "boolean", "boolean", "status"),
        ("news.llm.forecast_relevance_probability", "number", "probability", "forecast_relevance_probability"),
        ("news.llm.forecast_eligible", "boolean", "boolean", "forecast_relevance_probability"),
        ("news.llm.positive_implication_probability", "number", "probability", "positive_implication_probability"),
        ("news.llm.negative_implication_probability", "number", "probability", "negative_implication_probability"),
        ("news.llm.language_sentiment", "string", "category", "language_sentiment"),
    ):
        rows.append(_field(
            field_id, "news_forecast", "text_intelligence",
            "q_live.news_forecast_funnel_v1" if field_id.startswith("news.deepfm") else "q_live.news_llm_issuer_review_v1",
            "intelligence.news_llm_review_events.v1", value_type=value_type, unit=unit,
            entity_grain="issuer_news_event", source_columns=(source_column,), event_at="published_at_utc",
            available_at="updated_at_utc", ttl_seconds=None, publication_cadence="event_driven",
            provenance="model_derived", coverage_query_plan="intelligence.news_llm_review_events.v1",
            timeframes=("event",),
        ))

    for horizon in ("1m", "5m", "30m", "regular_close", "extended_close"):
        for metric, value_type, unit in (
            ("upside_probability", "number", "probability"),
            ("downside_probability", "number", "probability"),
            ("no_action_probability", "number", "probability"),
            ("expected_return_pct", "number", "percent"),
            ("favorable_excursion_pct", "number", "percent"),
            ("adverse_excursion_pct", "number", "percent"),
            ("confidence", "number", "probability"),
            ("abstain", "boolean", "boolean"),
        ):
            rows.append(_field(
                f"news.reaction.{horizon}.{metric}", "news_reaction", "news_hypothesis",
                "q_live.news_market_hypothesis_v1", "intelligence.news_reaction_events.v1",
                value_type=value_type, unit=unit, entity_grain="issuer_news_event",
                source_columns=(f"predictions.{horizon}.{metric}",), event_at="published_at_utc",
                available_at="created_at_utc", ttl_seconds=None, publication_cadence="event_driven",
                provenance="model_derived", coverage_query_plan="intelligence.news_reaction_events.v1",
                timeframes=("event",),
            ))
    rows.append(_field(
        "news.reaction.regime_compatibility", "news_reaction", "news_hypothesis",
        "q_live.news_market_hypothesis_v1", "intelligence.news_reaction_events.v1",
        value_type="string", unit="category", entity_grain="issuer_news_event",
        source_columns=("regime_compatibility",), event_at="published_at_utc",
        available_at="created_at_utc", ttl_seconds=None, publication_cadence="event_driven",
        provenance="model_derived", coverage_query_plan="intelligence.news_reaction_events.v1",
        timeframes=("event",),
    ))

    sec_names = ("latest_at", "count", "recency", "latest_form", "cik", "accession", "form", "accepted_at", "filed_at", "period_end", "document_id", "document_type", "source_hash", "renderer_version", "topic", "event_type", "direction", "score", "confidence", "impact", "uncertainty", "entity_relationships", "market_bridge_state")
    for name in sec_names:
        semantic = name in {"topic", "event_type", "direction", "score", "confidence", "impact", "uncertainty", "entity_relationships"}
        rows.append(_field(f"sec.{name}", "sec", "text_intelligence" if semantic else "sec_gateway", "service://text-intelligence/sec-synthesis-v1" if semantic else "service://sec-gateway/filings-v3", "intelligence.sec_asof.v1" if semantic else "sec.filing_asof.v1", value_type="number" if name in {"count", "recency", "score", "confidence", "impact", "uncertainty", "renderer_version"} else "json" if name == "entity_relationships" else "string", entity_grain="sec_filing", ttl_seconds=900, publication_cadence="event_driven", status="integration_pending" if semantic else "implemented"))

    reported_fundamentals = (
        "revenue", "gross_profit", "operating_income", "net_income", "diluted_eps", "operating_cash_flow", "capital_expenditure", "cash", "current_assets", "current_liabilities", "accounts_receivable", "accounts_payable", "inventory", "assets", "liabilities", "stockholders_equity", "long_term_debt", "current_debt", "research_development", "sga_expense", "stock_based_compensation", "interest_expense", "income_tax_expense", "effective_tax_rate_pct", "goodwill", "intangible_assets", "deferred_revenue", "debt_issued", "debt_repaid", "common_stock_issuance", "common_shares_outstanding", "weighted_average_basic_shares", "weighted_average_diluted_shares", "sec_public_float_value", "dividends_per_share", "share_repurchases", "repurchased_shares",
    )
    derived_fundamentals = (
        "free_cash_flow", "gross_margin_pct", "operating_margin_pct", "net_margin_pct", "free_cash_flow_margin_pct", "return_on_assets_pct", "return_on_equity_pct", "working_capital", "current_ratio", "debt_to_equity", "net_debt", "interest_coverage", "revenue_change", "revenue_growth_pct", "earnings_change", "earnings_growth_pct", "share_change", "share_growth_pct", "dilution_pct", "cash_conversion", "research_intensity_pct", "sga_intensity_pct", "latest_filing_at", "trajectory_score", "trajectory_label", "profitability_score", "cash_generation_score", "balance_sheet_score", "share_base_pressure_pct", "share_base_discipline_score", "valuation_pe", "valuation_label",
    )
    xbrl_fields = ("quality_score", "quality_label", "quality_coverage_pct", "profitability_score", "growth_score", "cash_quality_score", "balance_sheet_score", "capital_discipline_score")
    for name in reported_fundamentals:
        rows.append(_field(f"fundamental.{name}", "fundamental", "sec_gateway", "q_live.sec_xbrl_company_fact_v3", "sec.fundamentals_asof.v1", unit="percent" if name.endswith("_pct") else "currency_or_shares", entity_grain="issuer_fiscal_period", ttl_seconds=None, publication_cadence="filing_driven", provenance="reported", coverage_query_plan="sec.fundamentals_asof.v1"))
    for name in derived_fundamentals:
        unit = (
            "percent"
            if name.endswith("_pct")
            else "currency"
            if name in {"revenue_change", "earnings_change"}
            else "shares"
            if name == "share_change"
            else "scalar"
        )
        rows.append(_field(f"fundamental.{name}", "fundamental", "backend", "derived://sec-xbrl-company-facts", "sec.fundamentals_asof.v1", value_type="string" if name.endswith("_label") or name.endswith("_at") else "number", unit=unit, entity_grain="issuer_fiscal_period", ttl_seconds=None, publication_cadence="filing_driven", provenance="derived", coverage_query_plan="sec.fundamentals_asof.v1"))
    for name in xbrl_fields:
        rows.append(_field(f"xbrl.{name}", "xbrl_quality", "backend", "derived://sec-xbrl-company-facts", "sec.fundamentals_asof.v1", value_type="string" if name.endswith("_label") else "number", unit="percent" if name.endswith("_pct") else "score", entity_grain="issuer_filing", ttl_seconds=None, publication_cadence="filing_driven", provenance="derived", coverage_query_plan="sec.fundamentals_asof.v1"))
    rows.append(_field("fundamental.quality_score", "fundamental", "backend", "derived://sec-xbrl-company-facts/xbrl-quality", "sec.fundamentals_asof.v1", unit="score", entity_grain="issuer_filing", ttl_seconds=None, publication_cadence="filing_driven", provenance="derived", coverage_query_plan="sec.fundamentals_asof.v1"))

    for field_id, source in (
        ("signal.news_labeled", "service://text-intelligence/news-synthesis-v1"),
        ("signal.sec_labeled", "service://text-intelligence/sec-synthesis-v1"),
    ):
        rows.append(_field(field_id, "intelligence_signal", "text_intelligence", source, "intelligence.news_asof.v1" if "news" in field_id else "intelligence.sec_asof.v1", value_type="boolean", entity_grain="company_event", ttl_seconds=900, publication_cadence="event_driven", provenance="derived", status="integration_pending"))

    for field_id, owner, source in (
        ("embedding.news.vector", "text_embed_gateway", "service://text-embed/news"),
        ("embedding.sec.vector", "text_embed_gateway", "service://text-embed/sec"),
        ("model.market_hypothesis.payload", "news_hypothesis", "service://news-hypothesis/hypotheses"),
        ("model.market_prediction.payload", "model_gateway", "service://model-gateway/predictions"),
    ):
        rows.append(_field(field_id, "model_context", owner, source, "model.context_asof.v1", value_type="vector" if field_id.startswith("embedding") else "json", entity_grain="frozen_context", ttl_seconds=None, publication_cadence="artifact_or_event_driven", provenance="model", status="integration_pending", coverage_query_plan="model.context_asof.v1"))

    rows.extend(_bar_gpt_fields())

    return tuple(rows)


FIELD_DEFINITIONS = _fields()
FIELD_BY_ID = {field.field_id: field for field in FIELD_DEFINITIONS}


DISCOVERY_FIELD_PRESENTATIONS = (
    DiscoveryFieldPresentation("identity.symbol", "identity.symbol", "symbol", "Symbol", "Point-in-time ticker identity for the eligible listing.", "reference", True, False, True, (), ("event",)),
    DiscoveryFieldPresentation("identity.company_name", "identity.company_name", "company_name", "Company", "Issuer or security name available for the listing at evaluation time.", "reference", True, False, True, (), ("event",)),
    DiscoveryFieldPresentation("market.last_price", "market.last_price", "last_price", "Last price", "Most recent causally available eligible trade price.", "market_data", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals", "above_by_bps"), ("event",)),
    DiscoveryFieldPresentation("market.previous_close", "market.previous_close", "previous_close", "Previous close", "Completed prior regular-session close available at the scanner clock.", "reference", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("session",)),
    DiscoveryFieldPresentation("market.change_actual", "market.change_actual", "change_actual", "Session price change", "Last price minus the completed previous-session close.", "market_data", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("session",)),
    DiscoveryFieldPresentation("market.change_pct", "market.change_pct", "change_pct", "Session change %", "Percentage change from the completed previous-session close.", "market_data", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("session",)),
    DiscoveryFieldPresentation("market.volume", "market.volume", "volume", "Session volume", "Cumulative eligible share volume for the current session.", "market_data", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("session",)),
    DiscoveryFieldPresentation("market.session_dollar_volume", "market.session_dollar_volume", "session_dollar_volume", "Session dollar volume", "Cumulative eligible trade notional since the 04:00 New York session boundary.", "market_data", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("session",)),
    DiscoveryFieldPresentation("market.relative_volume", "market.relative_volume", "relative_volume", "Relative volume", "Cumulative volume versus the aligned 20-session baseline.", "indicator", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("session",)),
    DiscoveryFieldPresentation("indicator.vwap.value", "market.vwap", "vwap", "Session VWAP", "Causal session volume-weighted average eligible trade price.", "indicator", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals", "above_by_bps"), ("session",)),
    DiscoveryFieldPresentation("identity.exchange", "listing.exchange", "exchange", "Exchange", "Point-in-time listing venue for the eligible security.", "reference", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("country.effective", "country.effective", "country", "Country", "Best point-in-time country assertion selected by the Reference Gateway.", "reference", False, False, True, (), ("1d",)),
    DiscoveryFieldPresentation("classification.sector", "classification.sector", "sector", "Sector", "Published issuer sector or the best available SIC description.", "reference", False, False, True, (), ("1d",)),
    DiscoveryFieldPresentation("identity.is_tradable", "tradability.is_tradable", "is_tradable", "Tradable", "Whether the listing is admitted to the QMD scanner universe at this clock.", "reference", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("market.event_at", "market.event_at", "market_event_at", "Last market event", "Timestamp of the latest accepted quote or eligible trade represented by this row.", "market_data", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("market.event_age_ms", "market.event_age_ms", "market_event_age_ms", "Market data age", "Elapsed milliseconds between the scanner clock and the latest accepted market event.", "market_data", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("market.quality_state", "market.quality_state", "market_quality_state", "Market quality", "QMD-owned ready, stale, locked, crossed, or unavailable market-state classification.", "market_data", True, False, True, (), ("event",)),
    DiscoveryFieldPresentation("market.quality_flags", "market.quality_flags", "market_quality_flags", "Quality flags", "QMD-owned market-data quality flags active at the scanner clock.", "market_data", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("market.degradation_reason", "market.degradation_reason", "market_degradation_reason", "Quality detail", "QMD explanation for a degraded market-quality state.", "market_data", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("market.liquidity_rank", "market.liquidity_rank", "liquidity_rank", "Market liquidity rank", "Ascending ordinal rank across the complete QMD scanner population; 1 is the highest 0-100 liquidity score.", "indicator", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("scanner_clock",)),
    DiscoveryFieldPresentation("market.spread_bps", "market.spread_bps", "spread_bps", "Spread", "Current quoted spread in basis points when both NBBO sides are available.", "market_data", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("market.halt_category", "market.halt_category", "halt_category", "Halt category", "Canonical SIP halt category decoded from the source condition and indicator tokens.", "market_data", False, False, True, ("equals", "not_equals"), ("event",)),
    DiscoveryFieldPresentation("market.halt_direction", "market.halt_direction", "halt_direction", "Halt direction", "Up, Down, or Flat label derived from the registered five-minute price change at the halt occurrence.", "market_data", False, False, True, ("equals", "not_equals"), ("event",)),
    DiscoveryFieldPresentation("market.trade_rate_10s", "market.trade_rate_10s", "trade_rate_10s", "Trades / sec (10s)", "Eligible trade-event rate over the latest ten seconds.", "market_data", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("10s",)),
    DiscoveryFieldPresentation("market.trade_rate_60s", "market.trade_rate_60s", "trade_rate_60s", "Trades / sec (60s)", "Eligible trade-event rate over the latest sixty seconds.", "market_data", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("1m",)),
    DiscoveryFieldPresentation("market.liquidity_score", "market.liquidity_score", "liquidity_score", "Relative liquidity score", "QMD score from 0 to 100. A value of 50 or higher additionally certifies the absolute session-dollar-volume, recent-trade-rate, spread, and freshness gates.", "indicator", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("scanner_clock",)),
    DiscoveryFieldPresentation("signal.squeeze_move_pct", "signal.squeeze_move_pct", "squeeze_move_pct", "Move from anchor", "Live percentage move from the event-time price immediately before the early squeeze trigger.", "signal", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("signal.squeeze_anchor_price", "signal.squeeze_anchor_price", "squeeze_anchor_price", "Move anchor", "Frozen eligible trade price immediately before the early squeeze trigger.", "signal", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("signal.squeeze_high_water_pct", "signal.squeeze_high_water_pct", "squeeze_high_water_pct", "Move high-water", "Largest percentage move reached from the episode anchor before this occurrence.", "signal", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("signal.squeeze_episode_expires_at", "signal.squeeze_episode_expires_at", "squeeze_expires_at", "Move expires", "Five-minute event-time expiry for the active move episode; it is not the detection timeframe.", "signal", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("clock.trading_date", "clock.trading_date", "trading_date", "Trading date", "QMD market-clock trading date in America/New_York.", "clock", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("clock.exchange_time", "clock.exchange_time", "market_time", "Market time", "QMD market-clock time in America/New_York.", "clock", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("clock.session_phase", "clock.session_phase", "session_phase", "Session phase", "Canonical QMD premarket, regular, aftermarket, or maintenance phase.", "clock", True, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("market.status", "market.status", "market_status", "Market status", "Canonical QMD active or closed market-calendar status.", "clock", True, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("market.is_open", "market.is_open", "market_is_open", "Market open", "Whether the QMD market calendar admits active collection at the evaluation clock.", "clock", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("market.is_halted", "market.is_halted", "market_is_halted", "Trading halted", "Whether QMD has an active exchange halt condition for this security at the evaluation clock.", "market_data", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.is_trading_day", "clock.is_trading_day", "is_trading_day", "Trading day", "Whether the evaluation date is a QMD-authorized trading session.", "clock", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.minutes_since_open", "clock.minutes_since_open", "minutes_since_open", "Minutes since open", "Elapsed minutes since the canonical regular-session open; unavailable outside an applicable session.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.minutes_until_close", "clock.minutes_until_close", "minutes_until_close", "Minutes until close", "Remaining minutes until the canonical regular-session close; unavailable after close.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("reference.market_cap", "reference.market_cap", "market_cap", "Market cap", "Latest point-in-time market capitalization.", "reference", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("1d",)),
    DiscoveryFieldPresentation("classification.market_cap", "classification.market_cap", "market_cap_category", "Cap category", "Small, Mid, or Large classification from the published configuration.", "reference", True, False, True, (), ("1d",)),
    DiscoveryFieldPresentation("reference.shares_outstanding", "reference.shares_outstanding", "shares_outstanding", "Shares outstanding", "Latest point-in-time reported share-class or provider outstanding shares.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("1d",)),
    DiscoveryFieldPresentation("reference.float_shares", "reference.float_shares", "float_shares", "Public float", "Tradable share supply with SEC-derived fallback provenance.", "reference", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("1d",)),
    DiscoveryFieldPresentation("classification.float", "classification.float", "float_category", "Float category", "Tiny through Broad Float classification from the published configuration.", "reference", True, False, True, (), ("1d",)),
    DiscoveryFieldPresentation("reference.float_source", "reference.float_source", "float_source", "Float source", "Reference publication tag identifying the evidence used for public float.", "reference", False, False, True, (), ("1d",)),
    DiscoveryFieldPresentation("reference.float_quality", "reference.float_quality", "float_quality", "Float coverage", "Reported float, shares-outstanding-only, or unavailable coverage state.", "reference", False, False, True, (), ("1d",)),
    DiscoveryFieldPresentation("classification.short_pressure", "classification.short_pressure", "short_pressure", "Short pressure", "Reference Gateway classification of reported short and volume evidence.", "reference", False, False, True, (), ("settlement",)),
    DiscoveryFieldPresentation("reference.short_interest", "reference.short_interest", "short_interest", "Short interest", "Latest reported short shares available before evaluation.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("settlement",)),
    DiscoveryFieldPresentation("reference.short_interest_pct", "reference.short_interest_pct", "short_interest_pct", "Short % float", "Short interest divided by point-in-time public float.", "reference", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("settlement",)),
    DiscoveryFieldPresentation("reference.days_to_cover", "reference.days_to_cover", "days_to_cover", "Days to cover", "Reported short interest divided by average daily volume.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("settlement",)),
    DiscoveryFieldPresentation("reference.short_volume", "reference.short_volume", "short_volume", "Short volume", "Latest published daily short-sale volume available before evaluation.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("1d",)),
    DiscoveryFieldPresentation("reference.short_volume_pct", "reference.short_volume_pct", "short_volume_pct", "Short volume %", "Latest published short-sale volume ratio.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("1d",)),
    DiscoveryFieldPresentation("reference.fails_to_deliver", "reference.fails_to_deliver", "fails_to_deliver", "Fails to deliver", "Latest SEC fails-to-deliver quantity available before evaluation.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("settlement",)),
    DiscoveryFieldPresentation("reference.ftd_value", "reference.ftd_value", "ftd_value", "FTD value", "Fails-to-deliver quantity multiplied by its published previous close.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("settlement",)),
    DiscoveryFieldPresentation("reference.reg_sho_threshold", "reference.reg_sho_threshold", "reg_sho_threshold", "Reg SHO threshold", "Whether a threshold-security publication exists at the scanner clock.", "reference", False, False, True, (), ("1d",)),
    DiscoveryFieldPresentation("reference.borrow_status", "reference.borrow_status", "borrow_status", "Borrow status", "Latest persisted broker shortability status available at the scanner clock.", "reference", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("reference.borrow_shares", "reference.borrow_shares", "borrow_shares", "Borrow shares", "Latest persisted shortable-share quantity available at the scanner clock.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("reference.borrow_fee", "reference.borrow_fee", "borrow_fee", "Borrow fee", "Latest persisted broker fee or indicative borrow rate.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("fundamental.trajectory_score", "fundamental.trajectory_score", "fundamental_trajectory", "Fundamental trajectory", "SEC-derived 0-100 financial trajectory score.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("filing",)),
    DiscoveryFieldPresentation("fundamental.quality_score", "fundamental.quality_score", "fundamental_quality", "Fundamental quality", "Coverage and comparability of the supporting SEC facts.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("filing",)),
    DiscoveryFieldPresentation("fundamental.revenue_change", "fundamental.revenue_change", "fundamental_revenue_change", "Comparable revenue change", "Latest comparable revenue minus prior comparable revenue.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("filing",)),
    DiscoveryFieldPresentation("fundamental.revenue_growth_pct", "fundamental.revenue_growth_pct", "fundamental_revenue_growth_pct", "Comparable revenue change %", "Comparable revenue change divided by the absolute prior-period revenue.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("filing",)),
    DiscoveryFieldPresentation("fundamental.earnings_change", "fundamental.earnings_change", "fundamental_earnings_change", "Comparable earnings change", "Latest comparable net income minus prior comparable net income.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("filing",)),
    DiscoveryFieldPresentation("fundamental.earnings_growth_pct", "fundamental.earnings_growth_pct", "fundamental_earnings_growth_pct", "Comparable earnings change %", "Comparable net-income change divided by the absolute prior-period net income.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("filing",)),
    DiscoveryFieldPresentation("fundamental.share_change", "fundamental.share_change", "fundamental_share_change", "Comparable share-count change", "Latest comparable weighted-average basic shares minus the prior comparable share count.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("filing",)),
    DiscoveryFieldPresentation("fundamental.share_growth_pct", "fundamental.share_growth_pct", "fundamental_share_growth_pct", "Comparable share-count change %", "Comparable basic-share change divided by the absolute prior-period share count.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("filing",)),
    DiscoveryFieldPresentation("signal.news_labeled", "signal.news_labeled", "", "News labeled", "Validated point-in-time Text Intelligence news-label availability.", "signal", False, True, False, ("is_true",), ("event",)),
    DiscoveryFieldPresentation("signal.company_news.score", "news.score", "news_sentiment", "News sentiment", "Latest validated point-in-time company-news score and label.", "signal", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("news.composite_sentiment", "news.composite_sentiment", "news_composite_sentiment", "Synthesis direction", "Informational issuer-specific News Synthesis direction; never decision-authorized.", "context", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("news.positive_strength", "news.positive_strength", "news_positive_strength", "Synthesis positive", "Informational synthesis evidence strength; never decision-authorized.", "context", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("news.negative_strength", "news.negative_strength", "news_negative_strength", "Synthesis negative", "Informational synthesis evidence strength; never decision-authorized.", "context", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("news.forecast_trigger_eligible", "news.forecast_trigger_eligible", "news_forecast_eligible", "Synthesis forecast view", "Informational synthesis product-suitability view; never decision-authorized.", "context", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("news.canonical_news_id", "news.canonical_news_id", "canonical_news_id", "News ID", "Stable canonical identity of the source news event.", "signal", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("news.published_at", "news.published_at", "news_published_at", "Published", "Authoritative source publication time in UTC.", "signal", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("news.deepfm.eligible_probability", "news.deepfm.eligible_probability", "news_deepfm_probability", "DeepFM probability", "DeepFM forecast-eligibility probability scored for every canonical article.", "signal", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than"), ("event",)),
    DiscoveryFieldPresentation("news.deepfm.forecast_eligible", "news.deepfm.forecast_eligible", "news_deepfm_eligible", "DeepFM eligible", "Whether DeepFM admitted the article at the configured operating threshold.", "signal", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("news.deepfm.status", "news.deepfm.status", "news_deepfm_status", "DeepFM status", "DeepFM eligible, filtered, pending, or failed state.", "signal", False, True, True, ("equals", "not_equals"), ("event",)),
    DiscoveryFieldPresentation("news.llm.review_complete", "news.llm.review_complete", "news_llm_reviewed", "LLM reviewed", "Whether a validated issuer-level manual or automatic review is durably available.", "signal", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("news.llm.forecast_relevance_probability", "news.llm.forecast_relevance_probability", "news_llm_forecast_probability", "LLM forecast relevance", "Issuer-level forecast relevance probability from a validated LLM review.", "signal", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than"), ("event",)),
    DiscoveryFieldPresentation("news.llm.forecast_eligible", "news.llm.forecast_eligible", "news_llm_forecast_eligible", "LLM forecast eligible", "Issuer-level forecast eligibility at the versioned review threshold.", "signal", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("news.llm.positive_implication_probability", "news.llm.positive_implication_probability", "news_llm_positive_probability", "LLM positive implication", "Issuer-specific positive language implication probability.", "signal", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than"), ("event",)),
    DiscoveryFieldPresentation("news.llm.negative_implication_probability", "news.llm.negative_implication_probability", "news_llm_negative_probability", "LLM negative implication", "Issuer-specific negative language implication probability.", "signal", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than"), ("event",)),
    DiscoveryFieldPresentation("news.llm.language_sentiment", "news.llm.language_sentiment", "news_llm_sentiment", "LLM language sentiment", "Positive, negative, mixed, or neutral issuer-language implication.", "signal", False, True, True, ("equals", "not_equals"), ("event",)),
    DiscoveryFieldPresentation("signal.sec_labeled", "signal.sec_labeled", "", "SEC labeled", "Validated point-in-time Text Intelligence SEC-label availability.", "signal", False, True, False, ("is_true",), ("event",)),
    DiscoveryFieldPresentation("signal.sec_filing.score", "sec.score", "sec_sentiment", "SEC sentiment", "Latest validated point-in-time filing score and label.", "signal", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("event.ipo.date", "event.ipo.date", "ipo_event", "IPO date", "Point-in-time past or upcoming IPO event date.", "event", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("event.ipo.days_to_event", "event.ipo.days_to_event", "ipo_days_to_event", "IPO event distance", "Signed calendar days from evaluation to the point-in-time IPO event.", "event", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("event.split.execution_date", "event.split.execution_date", "split_event", "Split date", "Latest published stock-split execution date and ratio.", "event", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("event.split.days_to_event", "event.split.days_to_event", "split_days_to_event", "Split event distance", "Signed calendar days from evaluation to the latest published split execution date.", "event", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
)

for _reaction_horizon in ("1m", "5m", "30m", "regular_close", "extended_close"):
    for _reaction_metric, _reaction_label, _reaction_type, _reaction_ops in (
        ("upside_probability", "Up probability", "probability", ("greater_or_equal", "greater_than", "less_or_equal", "less_than")),
        ("downside_probability", "Down probability", "probability", ("greater_or_equal", "greater_than", "less_or_equal", "less_than")),
        ("no_action_probability", "No-action probability", "probability", ("greater_or_equal", "greater_than", "less_or_equal", "less_than")),
        ("expected_return_pct", "Expected return", "percent", ("greater_or_equal", "greater_than", "less_or_equal", "less_than")),
        ("favorable_excursion_pct", "Favorable excursion", "percent", ("greater_or_equal", "greater_than", "less_or_equal", "less_than")),
        ("adverse_excursion_pct", "Adverse excursion", "percent", ("greater_or_equal", "greater_than", "less_or_equal", "less_than")),
        ("confidence", "Confidence", "probability", ("greater_or_equal", "greater_than", "less_or_equal", "less_than")),
        ("abstain", "Abstain", "boolean", ("is_true", "equals")),
    ):
        DISCOVERY_FIELD_PRESENTATIONS += (DiscoveryFieldPresentation(
            f"news.reaction.{_reaction_horizon}.{_reaction_metric}",
            f"news.reaction.{_reaction_horizon}.{_reaction_metric}",
            f"news_reaction_{_reaction_horizon}_{_reaction_metric}",
            f"{_reaction_horizon.replace('_', ' ').title()} {_reaction_label}",
            f"Persisted {_reaction_horizon.replace('_', ' ')} market-reaction {_reaction_label.lower()} available at model completion.",
            "signal", False, True, True, _reaction_ops, ("event",), _reaction_type,
        ),)
DISCOVERY_FIELD_PRESENTATIONS += (DiscoveryFieldPresentation(
    "news.reaction.regime_compatibility", "news.reaction.regime_compatibility",
    "news_reaction_regime", "Reaction regime", "Supportive, neutral, hostile, or unknown regime compatibility.",
    "signal", False, True, True, ("equals", "not_equals"), ("event",),
),)

DISCOVERY_FIELD_PRESENTATIONS += (
    DiscoveryFieldPresentation("clock.observed_at", "clock.observed_at", "clock_observed_at", "Observed at", "UTC timestamp at which QMD evaluated this market-clock snapshot.", "clock", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("clock.utc_date", "clock.utc_date", "utc_date", "UTC date", "Calendar date at the QMD evaluation clock in UTC.", "clock", False, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("clock.utc_time", "clock.utc_time", "utc_time", "UTC time", "Time of day at the QMD evaluation clock in UTC.", "clock", False, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("clock.exchange_date", "clock.exchange_date", "exchange_date", "Exchange date", "Calendar date at the evaluation clock in America/New_York.", "clock", False, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("clock.timezone", "clock.timezone", "market_timezone", "Market timezone", "IANA timezone used for the exchange-local clock.", "clock", False, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("clock.weekday", "clock.weekday", "market_weekday", "Weekday name", "Full English weekday name for the exchange-local calendar date.", "clock", False, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("clock.session_id", "clock.session_id", "session_id", "Session ID", "Stable exchange-local session identity for the market-clock date.", "clock", False, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("clock.session_open_at", "clock.session_open_at", "session_open_at", "Session open", "Canonical regular-session open timestamp for the exchange-local date.", "clock", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("clock.session_close_at", "clock.session_close_at", "session_close_at", "Session close", "Calendar-authoritative regular-session close timestamp, including early closes.", "clock", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("clock.is_early_close", "clock.is_early_close", "is_early_close", "Early close", "Whether the market calendar defines an early regular-session close for this trading date.", "clock", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("market.luld_state", "market.luld_state", "estimated_luld_state", "LULD state", "QMD estimate of whether the latest price is inside, near, or outside the applicable LULD bands.", "market_data", False, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("market.feed_status", "market.feed_status", "market_feed_status", "Market feed status", "QMD ready or stale market-calendar/feed status at the evaluation clock.", "market_data", False, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("clock.calendar_year", "clock.calendar_year", "calendar_year", "Calendar year", "Four-digit year from the exchange-local calendar date.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.calendar_quarter", "clock.calendar_quarter", "calendar_quarter", "Calendar quarter", "Exchange-local calendar quarter numbered 1 through 4.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.month_number", "clock.month_number", "month_number", "Month number", "Exchange-local calendar month numbered 1 through 12.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.month_name", "clock.month_name", "month_name", "Month name", "Full English month name for the exchange-local calendar date.", "clock", False, True, True, ("equals",), ("event",)),
    DiscoveryFieldPresentation("clock.iso_week", "clock.iso_week", "iso_week", "ISO week", "ISO-8601 week number for the exchange-local calendar date.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.day_of_month", "clock.day_of_month", "day_of_month", "Day of month", "Exchange-local calendar day number within the month.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.day_of_year", "clock.day_of_year", "day_of_year", "Day of year", "Exchange-local ordinal calendar day numbered 1 through 366.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.weekday_number", "clock.weekday_number", "weekday_number", "Weekday number", "ISO-8601 weekday number, Monday=1 through Sunday=7.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.hour", "clock.hour", "market_hour", "Market hour", "Exchange-local hour numbered 0 through 23.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.minute", "clock.minute", "market_minute", "Market minute", "Exchange-local minute numbered 0 through 59.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.second", "clock.second", "market_second", "Market second", "Exchange-local second numbered 0 through 59.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.minutes_since_midnight", "clock.minutes_since_midnight", "minutes_since_midnight", "Minutes since midnight", "Exchange-local hour multiplied by 60 plus minute.", "clock", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.is_weekend", "clock.is_weekend", "is_weekend", "Weekend", "Whether the exchange-local calendar date is Saturday or Sunday.", "clock", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.is_month_start", "clock.is_month_start", "is_month_start", "Month start", "Whether the exchange-local date is the first calendar day of its month.", "clock", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.is_month_end", "clock.is_month_end", "is_month_end", "Month end", "Whether the exchange-local date is the last calendar day of its month.", "clock", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.is_quarter_start", "clock.is_quarter_start", "is_quarter_start", "Quarter start", "Whether the exchange-local date is the first calendar day of a quarter.", "clock", False, True, True, ("is_true", "equals"), ("event",)),
    DiscoveryFieldPresentation("clock.is_quarter_end", "clock.is_quarter_end", "is_quarter_end", "Quarter end", "Whether the exchange-local date is the last calendar day of a quarter.", "clock", False, True, True, ("is_true", "equals"), ("event",)),
)

DISCOVERY_FIELD_PRESENTATIONS += tuple(
    DiscoveryFieldPresentation(
        field.field_id,
        field.field_id,
        f"event_{field.field_id.replace('.', '_')}",
        field.presentation_label,
        field.calculation_summary,
        "event_data",
        False,
        True,
        True,
        ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"),
        field.timeframes,
    )
    for field in FIELD_DEFINITIONS
    if field.aggregation_functions
)

# One authoritative translation from semantic discovery fields to the flat row
# keys published by the Scanner products.  Presentation column IDs are stable
# user configuration identities and are deliberately allowed to differ from
# producer-owned runtime keys.
DISCOVERY_RUNTIME_FIELDS: dict[str, str] = {
    presentation.source_id: presentation.column_id or presentation.source_id
    for presentation in DISCOVERY_FIELD_PRESENTATIONS
}
DISCOVERY_RUNTIME_FIELDS.update({
    "identity.symbol": "ticker",
    "signal.news_labeled": "news_labeled",
    "signal.company_news.score": "news_sentiment_score",
    "signal.sec_labeled": "sec_labeled",
    "signal.sec_filing.score": "sec_sentiment_score",
    "fundamental.trajectory_score": "financial_trajectory_score",
    "fundamental.quality_score": "xbrl_quality_score",
    "event.ipo.date": "ipo_date",
    "event.split.execution_date": "split_execution_date",
    "clock.trading_date": "trading_date",
    "clock.exchange_time": "market_time",
    "clock.session_phase": "session_phase",
    "market.status": "market_status",
    "market.is_open": "market_is_open",
    "market.luld_state": "estimated_luld_state",
    "market.feed_status": "market_feed_status",
    "clock.is_trading_day": "is_trading_day",
    "clock.minutes_since_open": "minutes_since_open",
    "clock.minutes_until_close": "minutes_until_close",
})


def validate_application_registry() -> None:
    source_ids = [source.source_id for source in MARKET_SOURCES]
    product_ids = [product.product_id for product in PRODUCT_DEFINITIONS]
    link_ids = [link.link_id for link in LINK_CONTRACTS]
    container_ids = [container.container_id for container in CONTAINER_DEFINITIONS]
    schema_ids = [schema.schema_id for schema in CONFIGURATION_SCHEMAS]
    alias_ids = [alias.alias_id for alias in COMPATIBILITY_ALIASES]
    discovery_source_ids = [field.source_id for field in DISCOVERY_FIELD_PRESENTATIONS]
    for label, values in (
        ("market source", source_ids),
        ("product", product_ids),
        ("link", link_ids),
        ("container", container_ids),
        ("configuration schema", schema_ids),
        ("compatibility alias", alias_ids),
        ("Market Discovery field", discovery_source_ids),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"{label} IDs must be unique")

    registered_field_ids = {field.field_id for field in FIELD_DEFINITIONS}
    for presentation in DISCOVERY_FIELD_PRESENTATIONS:
        if presentation.field_id and presentation.field_id not in registered_field_ids:
            raise ValueError(
                f"{presentation.source_id} references unknown application field "
                f"{presentation.field_id}"
            )
        if presentation.column_id and not presentation.sortable:
            raise ValueError(
                f"{presentation.source_id} display column must declare sort behavior"
            )
        if presentation.filterable and not presentation.filter_operators:
            raise ValueError(
                f"{presentation.source_id} is filterable without registered operators"
            )

    known_sources = set(source_ids)
    known_products = set(product_ids)
    known_links = set(link_ids)
    supported_modes = set(ALL_MODES)
    supported_scopes = {
        "universal_ingest",
        "core_scan",
        "watchlist",
        "signal_stream",
        "strategy_run",
        "request",
        "offline",
    }
    supported_statuses = {
        "implemented",
        "integration_pending",
        "live_only",
        "planned",
        "deprecated",
        "retired",
    }
    for source in MARKET_SOURCES:
        if not set(source.modes).issubset(supported_modes):
            raise ValueError(f"{source.source_id} has an unsupported mode")
        if not source.coverage_path or not source.watermark_path:
            raise ValueError(f"{source.source_id} must declare coverage and watermark authority")
        _require_text(source.source_id, "owner", source.owner)
        _require_text(source.source_id, "source path", source.source_path)
        _require_text(source.source_id, "event clock", source.event_clock)
        _require_text(source.source_id, "availability clock", source.availability_clock)
        if source.schema_version < 1 or source.status not in supported_statuses:
            raise ValueError(f"{source.source_id} has an invalid schema version or status")
    product_graph: dict[str, tuple[str, ...]] = {}
    for product in PRODUCT_DEFINITIONS:
        missing_sources = set(product.source_ids) - known_sources
        missing_products = set(product.dependency_products) - known_products
        if missing_sources or missing_products:
            raise ValueError(
                f"{product.product_id} has unknown sources/products: "
                f"{sorted(missing_sources | missing_products)}"
            )
        if not set(product.modes).issubset(supported_modes):
            raise ValueError(f"{product.product_id} has an unsupported mode")
        if not product.execution_scopes or not set(product.execution_scopes).issubset(supported_scopes):
            raise ValueError(f"{product.product_id} has an invalid execution scope")
        if product.schema_version < 1 or product.status not in supported_statuses:
            raise ValueError(f"{product.product_id} has an invalid schema version or status")
        _validate_implementation_reference(product.product_id, product.implementation)
        product_graph[product.product_id] = product.dependency_products
    _validate_acyclic_dependencies(product_graph)

    containers = set(container_ids)
    special_link_participants = {"all_containers", "workspace_controller"}
    for link in LINK_CONTRACTS:
        if not set(link.modes).issubset(supported_modes) or link.schema_version < 1:
            raise ValueError(f"{link.link_id} has an invalid mode or schema version")
        _require_text(link.link_id, "clock policy", link.clock_policy)
        _require_text(link.link_id, "identity policy", link.identity_policy)
        unknown = (set(link.producers) | set(link.consumers)) - containers - special_link_participants
        if unknown:
            raise ValueError(f"{link.link_id} references unknown containers: {sorted(unknown)}")
    for container in CONTAINER_DEFINITIONS:
        missing_links = (set(container.input_links) | set(container.output_links)) - known_links
        missing_products = set(container.product_ids) - known_products
        if missing_links or missing_products:
            raise ValueError(
                f"{container.container_id} has unknown links/products: "
                f"{sorted(missing_links | missing_products)}"
            )
        if not set(container.modes).issubset(supported_modes) or container.state_schema_version < 1:
            raise ValueError(f"{container.container_id} has an invalid mode or state schema")
        if container.status not in supported_statuses:
            raise ValueError(f"{container.container_id} has an invalid status")
        _validate_implementation_reference(container.container_id, container.implementation)
        for link_id in container.input_links:
            contract = next(link for link in LINK_CONTRACTS if link.link_id == link_id)
            if container.container_id not in contract.consumers and "all_containers" not in contract.consumers:
                raise ValueError(f"{container.container_id} is not a consumer of {link_id}")
        for link_id in container.output_links:
            contract = next(link for link in LINK_CONTRACTS if link.link_id == link_id)
            if container.container_id not in contract.producers:
                raise ValueError(f"{container.container_id} is not a producer of {link_id}")
    for schema in CONFIGURATION_SCHEMAS:
        if (
            schema.version < 1
            or not set(schema.modes).issubset(supported_modes)
            or schema.status not in supported_statuses
        ):
            raise ValueError(f"{schema.schema_id} has an invalid version or mode")
        _validate_implementation_reference(schema.schema_id, schema.implementation)

    plan_ids = [plan.plan_id for plan in QUERY_PLANS]
    if len(plan_ids) != len(set(plan_ids)):
        raise ValueError("query plan IDs must be unique")
    field_ids = [field.field_id for field in FIELD_DEFINITIONS]
    if len(field_ids) != len(set(field_ids)):
        duplicates = sorted({value for value in field_ids if field_ids.count(value) > 1})
        raise ValueError(f"field IDs must be unique: {duplicates}")
    known_plans = set(plan_ids)
    for plan in QUERY_PLANS:
        _require_text(plan.plan_id, "owner", plan.owner)
        _validate_implementation_reference(plan.plan_id, plan.implementation)
        if not plan.source_paths or any(not path.strip() for path in plan.source_paths):
            raise ValueError(f"{plan.plan_id} must declare non-empty source paths")
        _require_text(plan.plan_id, "identity join", plan.identity_join)
        _require_text(plan.plan_id, "event clock", plan.event_clock)
        _require_text(plan.plan_id, "availability clock", plan.availability_clock)
        _require_text(plan.plan_id, "coverage path", plan.coverage_path)
        if plan.version < 1:
            raise ValueError(f"{plan.plan_id} has an invalid version")
    for field in FIELD_DEFINITIONS:
        if field.query_plan_id not in known_plans:
            raise ValueError(f"{field.field_id} references unknown query plan {field.query_plan_id}")
        if field.coverage_query_plan not in known_plans:
            raise ValueError(
                f"{field.field_id} references unknown coverage plan {field.coverage_query_plan}"
            )
        if not field.modes:
            raise ValueError(f"{field.field_id} has no eligible mode")
        if not set(field.modes).issubset(supported_modes):
            raise ValueError(f"{field.field_id} has an unsupported mode")
        if field.status not in supported_statuses or field.schema_version < 1:
            raise ValueError(f"{field.field_id} has an invalid status or schema version")
        _require_text(field.field_id, "source path", field.source_path)
        _require_text(field.field_id, "identity join", field.identity_join)
        _require_text(field.field_id, "event clock", field.event_at)
        _require_text(field.field_id, "availability clock", field.available_at)
        if field.ttl_seconds is not None and field.ttl_seconds <= 0:
            raise ValueError(f"{field.field_id} has a non-positive TTL")

    registered_sources = {path for plan in QUERY_PLANS for path in plan.source_paths}
    missing_reference_tables = sorted(
        f"q_live.{table}"
        for group in REFERENCE_TABLE_GROUPS
        for table in group.tables
        if f"q_live.{table}" not in registered_sources
    )
    if missing_reference_tables:
        raise ValueError(f"Reference table inventory is incomplete: {missing_reference_tables}")

    alias_paths = [alias.alias_path for alias in COMPATIBILITY_ALIASES]
    if len(alias_paths) != len(set(alias_paths)):
        raise ValueError("compatibility alias paths must be unique")
    for alias in COMPATIBILITY_ALIASES:
        if alias.alias_path == alias.canonical_path:
            raise ValueError(f"{alias.alias_id} must not alias itself")
        if alias.retirement_state not in {"deprecated", "retired"}:
            raise ValueError(f"{alias.alias_id} has an invalid retirement state")
        _require_text(alias.alias_id, "canonical path", alias.canonical_path)
        _require_text(alias.alias_id, "removal condition", alias.removal_condition)


def _require_text(record_id: str, field: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{record_id} must declare {field}")


def _validate_implementation_reference(record_id: str, implementation: str) -> None:
    _require_text(record_id, "implementation", implementation)
    reference = implementation.split(":", 1)[0]
    if "::" in implementation and not implementation.startswith(("src/", "services/", "frontend/")):
        return
    if reference.startswith(("src/", "services/", "frontend/")):
        candidate = reference
    elif reference.startswith(("src.", "services.")):
        candidate = reference.replace(".", "/") + ".py"
    else:
        return
    repository_root = Path(__file__).resolve().parents[2]
    if not (repository_root / candidate).is_file():
        raise ValueError(f"{record_id} implementation path does not exist: {candidate}")


def _validate_acyclic_dependencies(graph: dict[str, tuple[str, ...]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError(f"product dependency cycle includes {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, ()):
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


@lru_cache(maxsize=1)
def application_registry_payload() -> dict[str, object]:
    validate_application_registry()
    return {
        "schema_version": 6,
        "market_sources": [asdict(source) for source in MARKET_SOURCES],
        "products": [asdict(product) for product in PRODUCT_DEFINITIONS],
        "link_contracts": [asdict(link) for link in LINK_CONTRACTS],
        "containers": [asdict(container) for container in CONTAINER_DEFINITIONS],
        "configuration_schemas": [asdict(schema) for schema in CONFIGURATION_SCHEMAS],
        "compatibility_aliases": [asdict(alias) for alias in COMPATIBILITY_ALIASES],
        "fields": [asdict(field) for field in FIELD_DEFINITIONS],
        "market_discovery_fields": [
            {**asdict(field), "presentation_value_type": field.presentation_value_type or _presentation_value_type(field.field_id, FIELD_BY_ID[field.field_id].value_type, FIELD_BY_ID[field.field_id].unit, FIELD_BY_ID[field.field_id].known_values)}
            for field in DISCOVERY_FIELD_PRESENTATIONS
        ],
        "query_plans": [asdict(plan) for plan in QUERY_PLANS],
        "counts": {
            "market_sources": len(MARKET_SOURCES),
            "products": len(PRODUCT_DEFINITIONS),
            "link_contracts": len(LINK_CONTRACTS),
            "containers": len(CONTAINER_DEFINITIONS),
            "configuration_schemas": len(CONFIGURATION_SCHEMAS),
            "compatibility_aliases": len(COMPATIBILITY_ALIASES),
            "fields": len(FIELD_DEFINITIONS),
            "market_discovery_fields": len(DISCOVERY_FIELD_PRESENTATIONS),
            "query_plans": len(QUERY_PLANS),
            "reference_tables": sum(len(group.tables) for group in REFERENCE_TABLE_GROUPS),
        },
    }


def runtime_capability_registry_payload(qmd_catalog: dict[str, object]) -> dict[str, object]:
    if str(qmd_catalog.get("authority") or "") != "qmd_runtime_catalog":
        raise ValueError("QMD runtime capability authority is unavailable")
    content_hash = str(qmd_catalog.get("content_hash") or "").strip()
    if not content_hash:
        raise ValueError("QMD runtime capability catalog has no content hash")
    families = {
        key: list(qmd_catalog.get(key) or [])
        for key in ("capability_catalog", "indicator_catalog", "signal_catalog")
    }
    if not families["capability_catalog"]:
        raise ValueError("QMD runtime capability catalog is empty")
    return {
        "schema_version": 1,
        "authority": "qmd_runtime_catalog",
        "provider": str(qmd_catalog.get("provider") or "qmd-gateway"),
        "content_hash": content_hash,
        **families,
        "counts": {
            "capabilities": len(families["capability_catalog"]),
            "indicators": len(families["indicator_catalog"]),
            "signals": len(families["signal_catalog"]),
        },
    }


def _registry_presentation(kind: str) -> dict[str, str]:
    definition = next((row for row in REGISTRY_TYPES if row.kind == kind), None)
    if definition is None:
        raise ValueError(f"Unknown registry kind: {kind}")
    return {
        "kind_label": definition.label,
        "icon": definition.icon,
        "accent": definition.accent,
    }


def _registry_definition(
    registry_id: str,
    kind: str,
    label: str,
    description: str,
    owner: str,
    version: int,
    status: str,
    *,
    configurable: bool | None = None,
    configuration_mode: str | None = None,
    configuration_binding_id: str = "",
    tags: Iterable[str] = (),
    relationships: dict[str, Iterable[str]] | None = None,
    documentation: dict[str, Any] | None = None,
    presentation_label: str = "",
) -> dict[str, Any]:
    type_definition = next(row for row in REGISTRY_TYPES if row.kind == kind)
    normalized_relationships = {
        key: sorted({str(value) for value in values if str(value).strip()})
        for key, values in (relationships or {}).items()
    }
    if documentation is None and kind in {"field", "derivation", "signal"}:
        producer_names = normalized_relationships.get("producer_ids") or [owner]
        source_fields = (
            normalized_relationships.get("input_field_ids")
            or (normalized_relationships.get("field_ids") if kind == "derivation" else None)
            or []
        )
        documentation = {
            "source_summary": (
                "Registered output published by "
                + ", ".join(name.replace("_", " ") for name in producer_names)
                + "."
            ),
            "calculation_summary": description,
            "input_field_ids": source_fields,
            "timeframes": [],
            "value_type": "event" if kind == "signal" else "number",
            "unit": "producer_defined",
            "entity_grain": "security_event" if kind == "signal" else "security_timeframe",
            "update_cadence": "producer cadence",
            "available_when": "After the registered producer publishes a causally available value.",
            "freshness_summary": "Freshness follows the registered producer contract.",
            "null_behavior": "Unavailable source evidence does not produce a substituted value.",
        }
    row = {
        "registry_id": registry_id,
        "kind": kind,
        "label": label,
        "presentation_label": presentation_label or label,
        "description": description,
        "owner": owner,
        "version": max(1, int(version)),
        "status": status,
        "tags": sorted({str(tag) for tag in tags if str(tag).strip()}),
        "configurable": (
            type_definition.configuration_mode != "locked"
            if configurable is None
            else bool(configurable)
        ),
        "configuration_mode": configuration_mode or type_definition.configuration_mode,
        "configuration_binding_id": configuration_binding_id,
        "relationships": normalized_relationships,
        "presentation": _registry_presentation(kind),
    }
    if documentation:
        row["documentation"] = documentation
    return row


def _field_operator_documentation(field: FieldDefinition) -> dict[str, Any]:
    source_location, source_fields = FIELD_SOURCE_OVERRIDES.get(
        field.field_id,
        (
            field.source_path,
            field.input_field_ids
            if field.provenance == "derived" and field.input_field_ids
            else field.source_columns,
        ),
    )
    stale_after = (
        f"The value is stale after {field.ttl_seconds:,} seconds."
        if field.ttl_seconds is not None
        else "Freshness follows the producer's published source revision."
    )
    available_when = {
        "source publication timestamp": "After the source publishes the value and before the evaluation clock.",
        "qmd event/bar clock": "After QMD completes the value at the current causal event or bar clock.",
        "analysis available_at": "After the producing analysis is complete and published.",
        "artifact available_at": "After the versioned model artifact publishes the value.",
    }.get(field.available_at, field.available_at)
    return {
        "documentation_status": (
            "partial"
            if "not yet published" in field.calculation_summary.lower()
            or "has not yet been published" in field.calculation_summary.lower()
            else "complete"
        ),
        "source_location": source_location,
        "source_fields": list(source_fields),
        "source_summary": field.source_summary,
        "operation_kind": (
            "model_output"
            if field.provenance == "model"
            else "source_read"
            if field.provenance in {"raw", "reported"}
            else "classification"
            if field.field_id.startswith("classification.")
            else "derivation"
        ),
        "operation_steps": [field.calculation_summary],
        "formula": DERIVED_FIELD_METHODS.get(field.field_id, "") or TEMPORAL_DERIVED_METHODS.get(field.field_id, ("", ()))[0],
        "classification_bands": [],
        "known_values": [
            {"value": value, "label": label, "description": description}
            for value, label, description in field.known_values
        ],
        "calculation_summary": field.calculation_summary,
        "input_field_ids": list(field.input_field_ids),
        "timeframes": list(field.timeframes),
        "value_type": field.value_type,
        "presentation_value_type": field.presentation_value_type,
        "unit": field.unit,
        "entity_grain": field.entity_grain,
        "update_cadence": field.publication_cadence,
        "available_when": available_when,
        "freshness_summary": stale_after,
        "null_behavior": (
            "Unavailable values remain unavailable. Registered reasons: "
            + ", ".join(reason.replace("_", " ") for reason in field.null_reasons)
            + "."
        ),
    }


def _application_information_definitions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(
        _registry_definition(
            field.field_id,
            "field",
            field.label,
            field.calculation_summary,
            field.owner,
            field.schema_version,
            field.status,
            configurable=True,
            configuration_mode="select_reference",
            tags=(field.group, field.provenance, field.value_type),
            relationships={
                "query_plan_ids": (field.query_plan_id,),
                "coverage_plan_ids": (field.coverage_query_plan,),
                "input_field_ids": field.input_field_ids,
            },
            documentation=_field_operator_documentation(field),
            presentation_label=field.presentation_label,
        )
        for field in FIELD_DEFINITIONS
    )
    rows.extend(
        _registry_definition(
            source.source_id,
            "source",
            source.label,
            ", ".join(source.authoritative_for),
            source.owner,
            source.schema_version,
            source.status,
            configurable=False,
            tags=(source.transport,),
        )
        for source in MARKET_SOURCES
    )
    rows.extend(
        _registry_definition(
            product.product_id,
            "product",
            product.label,
            f"{product.kind} delivered through {', '.join(product.delivery)}.",
            product.owner,
            product.schema_version,
            product.status,
            configurable=False,
            tags=(product.kind, *product.execution_scopes),
            relationships={
                "source_ids": product.source_ids,
                "product_ids": product.dependency_products,
            },
        )
        for product in PRODUCT_DEFINITIONS
    )
    rows.extend(
        _registry_definition(
            plan.plan_id,
            "query_plan",
            plan.plan_id,
            f"Bounded point-in-time retrieval owned by {plan.owner}.",
            plan.owner,
            plan.version,
            "implemented",
            configurable=False,
            relationships={"source_paths": plan.source_paths},
        )
        for plan in QUERY_PLANS
    )
    rows.extend(
        _registry_definition(
            f"column.{presentation.column_id}",
            "column",
            presentation.label,
            presentation.description,
            "application_registry",
            1,
            "implemented",
            configurable=True,
            configuration_mode="select_reference",
            configuration_binding_id="market_discovery.columns",
            tags=(presentation.semantic_type,),
            relationships={"field_ids": (presentation.field_id,)},
        )
        for presentation in DISCOVERY_FIELD_PRESENTATIONS
    )
    rows.extend((
        _registry_definition(
            "signal.company_news",
            "signal",
            "Company news intelligence",
            "Versioned company-news event lifecycle published from News Gateway content and validated Text Intelligence outputs.",
            "text_intelligence",
            1,
            "integration_pending",
            configurable=False,
            configuration_mode="locked",
            tags=("news", "external_service", "event_signal"),
            relationships={
                "field_ids": ("signal.news_labeled", "signal.company_news.score"),
                "producer_ids": ("news_gateway", "text_intelligence"),
            },
        ),
        _registry_definition(
            "signal.sec_filing",
            "signal",
            "SEC filing intelligence",
            "Versioned filing-event lifecycle published from SEC Gateway evidence and validated Text Intelligence outputs.",
            "text_intelligence",
            1,
            "integration_pending",
            configurable=False,
            configuration_mode="locked",
            tags=("sec", "external_service", "event_signal"),
            relationships={
                "field_ids": ("signal.sec_labeled", "signal.sec_filing.score"),
                "producer_ids": ("sec_gateway", "text_intelligence"),
            },
        ),
    ))
    return rows


def _configuration_information_definitions(
    configuration: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not configuration:
        return []
    rows: list[dict[str, Any]] = []
    discovery = dict(configuration.get("market_discovery") or {})
    operational_kinds = {
        "instrument-identity": "processing_step",
        "market-quality": "processing_step",
        "liquidity-rank": "derivation",
        "news-events": "product",
        "sec-events": "product",
        "membership-history": "product",
    }
    for capability in discovery.get("calculation_catalog") or []:
        capability_id = str(capability.get("capability_id") or "").strip()
        if not capability_id or capability_id.startswith("qmd."):
            continue
        capability_type = str(capability.get("capability_type") or "").strip()
        kind = operational_kinds.get(capability_id) or (
            "signal" if capability_type == "signal"
            else "derivation" if capability_type == "indicator"
            else "product" if capability_type == "event"
            else "field"
        )
        configurable = bool(capability.get("configurable")) and not bool(capability.get("system_required"))
        configuration_mode = (
            "parameterized_reference" if kind in {"derivation", "signal"} and configurable
            else "select_reference" if configurable
            else "locked"
        )
        binding_id = (
            "market_discovery.core_scan" if kind == "derivation"
            else "market_discovery.signals" if kind == "signal"
            else "market_discovery.products" if kind == "product" and configurable
            else ""
        )
        rows.append(_registry_definition(
            capability_id, kind, str(capability.get("name") or capability_id),
            str(capability.get("description") or capability.get("calculation") or "Registered Market Discovery reference."),
            str(capability.get("owner") or capability.get("provider") or "application_configuration"),
            int(capability.get("implementation_version") or 1),
            str(capability.get("implementation_status") or capability.get("availability") or "implemented"),
            configurable=configurable,
            configuration_mode=configuration_mode,
            configuration_binding_id=binding_id,
            tags=(str(capability.get("category") or "market_discovery"), str(capability.get("tier") or "")),
            relationships={"field_ids": tuple(str(value) for value in capability.get("fields") or [])},
        ))
    for rule_set in discovery.get("rule_sets") or []:
        rule_id = str(rule_set.get("rule_set_id") or "").strip()
        if rule_id:
            condition_ids = []
            for index, condition in enumerate(rule_set.get("conditions") or []):
                condition_id = str(condition.get("condition_id") or f"condition-{index + 1}").strip()
                registry_id = f"condition.{rule_id}.{condition_id}"
                condition_ids.append(registry_id)
                rows.append(_registry_definition(
                    registry_id, "condition", condition_id,
                    "Typed comparison over registered Field or Signal references.", "application_configuration", 1,
                    "implemented", configuration_binding_id="market_discovery.conditions",
                    relationships={
                        "field_ids": tuple(str(value) for value in (condition.get("left_source_id"), condition.get("right_source_id")) if value),
                    },
                ))
            rows.append(_registry_definition(
                f"rule_set.{rule_id}", "rule_set", str(rule_set.get("name") or rule_id),
                str(rule_set.get("description") or "Reusable rule composition."),
                str(rule_set.get("origin") or "application_configuration"),
                int(rule_set.get("revision") or 1),
                "implemented",
                configurable=not bool(rule_set.get("atomic")),
                configuration_mode="locked" if bool(rule_set.get("atomic")) else "editable_instance",
                configuration_binding_id="market_discovery.rules",
                tags=(str(rule_set.get("scope") or "shared"), "atomic" if rule_set.get("atomic") else "custom"),
                relationships={"condition_ids": tuple(condition_ids)},
            ))
    for watchlist in discovery.get("watchlists") or []:
        watchlist_id = str(watchlist.get("watchlist_id") or "").strip()
        if watchlist_id:
            rows.append(_registry_definition(
                f"watchlist.{watchlist_id}", "watchlist", str(watchlist.get("name") or watchlist_id),
                str(watchlist.get("description") or "Configured Watchlist."), "application_configuration", int(watchlist.get("revision") or 1),
                "implemented" if watchlist.get("availability") != "integration_pending" else "integration_pending",
                configuration_binding_id="market_discovery.watchlists",
                relationships={
                    "rule_set_ids": tuple(f"rule_set.{value}" for value in watchlist.get("inclusion_rule_sets") or []),
                    "column_ids": tuple(f"column.{value}" for value in watchlist.get("columns") or []),
                    "derivation_ids": tuple(str(value) for value in watchlist.get("calculations") or []),
                },
            ))
    strategy = dict(configuration.get("strategy") or {})
    for definition in strategy.get("definitions") or []:
        strategy_id = str(definition.get("strategy_id") or "").strip()
        revision = int(definition.get("revision") or 1)
        if strategy_id:
            rows.append(_registry_definition(
                f"strategy.{strategy_id}@{revision}", "strategy", str(definition.get("name") or strategy_id),
                str(definition.get("description") or "Executable Strategy definition."), "strategy_registry", revision,
                "implemented" if definition.get("executor_installed", True) else "integration_pending", configurable=False,
            ))
    for profile in strategy.get("profiles") or []:
        profile_id = str(profile.get("profile_id") or "").strip()
        if profile_id:
            definition_id = str(profile.get("definition_id") or "")
            definition_revision = int(profile.get("definition_revision") or 1)
            rows.append(_registry_definition(
                f"strategy_profile.{profile_id}", "strategy_profile", str(profile.get("name") or profile_id),
                str(profile.get("description") or "Configured Strategy profile."), "application_configuration", int(profile.get("revision") or 1),
                "implemented", configuration_binding_id="strategy.profiles",
                relationships={"strategy_ids": (f"strategy.{definition_id}@{definition_revision}",)},
            ))
    assignments = dict(configuration.get("assignments") or {})
    portfolio = dict(configuration.get("portfolio") or {})
    for run_plan in assignments.get("deployments") or []:
        run_plan_id = str(run_plan.get("run_plan_id") or "").strip()
        if run_plan_id:
            rows.append(_registry_definition(
                f"run_plan.{run_plan_id}", "run_plan", str(run_plan.get("name") or run_plan_id),
                str(run_plan.get("description") or "Configured executable Run Plan."), "application_configuration", 1,
                "implemented", configuration_binding_id="run_plans",
                relationships={
                    "strategy_profile_ids": (f"strategy_profile.{run_plan.get('profile_id')}",),
                    "oms_profile_ids": (f"oms_profile.{run_plan.get('oms_profile_id')}",),
                    "watchlist_ids": tuple(f"watchlist.{value}" for value in run_plan.get("watchlist_ids") or []),
                    "portfolio_mandate_ids": tuple(
                        f"portfolio_mandate.{row.get('mandate_id')}"
                        for row in portfolio.get("mandates") or []
                        if str(row.get("run_plan_id") or "") == run_plan_id
                    ),
                    "canvas_profile_ids": (f"canvas_profile.{run_plan.get('canvas_profile_id')}",),
                    "query_plan_ids": tuple(str(value) for value in dict(run_plan.get("data_plan_ids") or {}).values()),
                },
            ))
    accounts = dict(configuration.get("accounts") or {})
    for account in accounts.get("bindings") or []:
        account_key = str(account.get("account_key") or "").strip()
        if account_key:
            rows.append(_registry_definition(
                f"account_binding.{account_key}", "account_binding", str(account.get("name") or account_key),
                "Stable application account identity; broker identity resolves only at runtime.", "application_configuration", 1,
                "implemented", configuration_binding_id="accounts",
                relationships={"portfolio_policy_ids": (f"portfolio_policy.{account.get('portfolio_policy_id')}",)},
            ))
    for policy in portfolio.get("policies") or []:
        policy_id = str(policy.get("policy_id") or "").strip()
        if policy_id:
            rows.append(_registry_definition(
                f"portfolio_policy.{policy_id}", "portfolio_policy", str(policy.get("name") or policy_id),
                "Account-wide capital, exposure, risk, capacity, and permission limits.", "portfolio", int(policy.get("revision") or 1),
                "implemented", configuration_binding_id="portfolio.policies",
            ))
    for mandate in portfolio.get("mandates") or []:
        mandate_id = str(mandate.get("mandate_id") or "").strip()
        if mandate_id:
            rows.append(_registry_definition(
                f"portfolio_mandate.{mandate_id}", "portfolio_mandate", mandate_id,
                "Explicit Run Plan-to-account allocation and authority limits.", "portfolio", 1, "implemented",
                configuration_binding_id="portfolio.mandates",
                relationships={
                    "run_plan_ids": (f"run_plan.{mandate.get('run_plan_id')}",),
                    "account_binding_ids": (f"account_binding.{mandate.get('account_key')}",),
                },
            ))
    for group in portfolio.get("groups") or []:
        group_id = str(group.get("group_id") or "").strip()
        if group_id:
            rows.append(_registry_definition(
                f"portfolio_group.{group_id}", "portfolio_group", group_id,
                "Aggregate Portfolio limits over explicit account members.", "portfolio", 1, "implemented",
                configuration_binding_id="portfolio.groups",
                relationships={"account_binding_ids": tuple(f"account_binding.{value}" for value in group.get("account_keys") or [])},
            ))
    oms = dict(configuration.get("oms") or {})
    for profile in oms.get("profiles") or []:
        profile_id = str(profile.get("profile_id") or "").strip()
        if profile_id:
            settings = dict(profile.get("settings") or {})
            rows.append(_registry_definition(
                f"oms_profile.{profile_id}", "oms_profile", str(profile.get("name") or profile_id),
                str(profile.get("description") or "Reusable execution and protection selection."), "oms", int(profile.get("revision") or 1),
                "implemented", configuration_binding_id="oms.profiles",
                relationships={
                    "execution_policy_ids": tuple(f"execution_policy.{value}" for value in (settings.get("entry_execution_policy_id"), settings.get("exit_execution_policy_id")) if value),
                    "protection_profile_ids": (f"protection_profile.{settings.get('protection_profile_id')}",),
                },
            ))
    for policy in oms.get("execution_policies") or []:
        policy_id = str(policy.get("policy_id") or "").strip()
        if policy_id:
            rows.append(_registry_definition(
                f"execution_policy.{policy_id}", "execution_policy", str(policy.get("name") or policy_id),
                str(policy.get("description") or "Bounded broker-neutral execution policy."), "oms", int(policy.get("revision") or 1),
                "implemented", configuration_binding_id="oms.execution_policies",
            ))
    for profile in oms.get("protection_profiles") or []:
        profile_id = str(profile.get("profile_id") or "").strip()
        if profile_id:
            rows.append(_registry_definition(
                f"protection_profile.{profile_id}", "protection_profile", str(profile.get("name") or profile_id),
                "Broker-held stop, target, trailing, and repair policy.", "oms", int(profile.get("revision") or 1),
                "implemented", configuration_binding_id="oms.protection_profiles",
            ))
    canvas = dict(configuration.get("canvas") or {})
    if canvas:
        rows.append(_registry_definition(
            f"canvas_profile.{str(canvas.get('revision') or 'draft')}", "canvas_profile", "Canvas profile",
            "Persisted workspace composition and container configuration.", "canvas", 1, "implemented",
            configuration_binding_id="canvas.profile",
        ))
    return rows


def _qmd_operator_documentation(
    row: dict[str, object],
    rows_by_id: dict[str, dict[str, object]],
) -> dict[str, Any]:
    registered = dict(row.get("documentation") or {})
    producer_id = str(row.get("producer_id") or "").strip()
    producer = rows_by_id.get(producer_id, {}) if producer_id else {}
    producer_documentation = dict(producer.get("documentation") or {})
    inputs = [
        str(value)
        for value in (
            registered.get("input_field_ids")
            or row.get("input_field_ids")
            or producer_documentation.get("input_field_ids")
            or producer.get("input_field_ids")
            or []
        )
        if str(value).strip()
    ]
    parameter_timeframes = next((
        list(parameter.get("allowed_values") or [])
        for parameter in (row.get("parameters") or producer.get("parameters") or [])
        if str(parameter.get("parameter_id") or parameter.get("name") or "") == "timeframes"
    ), [])
    kind = str(row.get("kind") or "definition")
    registry_id = str(row.get("registry_id") or "")
    label = (
        _field_presentation_label(registry_id)
        if kind == "field"
        else str(
            row.get("presentation_label")
            or row.get("label")
            or registry_id
            or "QMD definition"
        )
    )
    source_summary = str(
        registered.get("source_summary")
        or producer_documentation.get("source_summary")
        or (
            f"Output published by the registered QMD producer {producer.get('label') or producer_id}."
            if producer_id
            else "QMD market events, closed bars, or reference inputs declared by this definition."
        )
    )
    calculation_summary = str(
        registered.get("calculation_summary")
        or producer_documentation.get("calculation_summary")
        or producer.get("description")
        or row.get("description")
        or "The QMD producer has not published an operator-facing method description."
    )
    leaf = registry_id.rsplit(".", 1)[-1]
    producer_label = str(producer.get("label") or producer_id or "QMD")
    if registry_id.startswith("signal."):
        member = _presentation_words(leaf)
        operation = f"Projects the {member} member from each emitted {producer_label} event; no additional calculation is applied."
    elif kind == "field":
        prefix, separator, period = leaf.rpartition("_")
        period_method = {
            "rsi": "Wilder Relative Strength Index from closed-bar gains and losses",
            "ema": "exponential moving average of closed-bar prices",
            "sma": "simple moving average of closed-bar prices",
            "atr": "Wilder Average True Range from closed-bar true ranges",
            "roc": "rate of change from closed-bar prices",
            "stddev": "standard deviation of closed-bar prices",
        }.get(prefix)
        exact_methods = {
            "open": "First eligible trade price in the completed bar.",
            "high": "Maximum eligible trade price in the completed bar.",
            "low": "Minimum eligible trade price in the completed bar.",
            "close": "Last eligible trade price in the completed bar.",
            "volume": "Sum of eligible trade size in the completed bar.",
            "trade_count": "Count of eligible trades in the completed bar.",
            "dollar_volume": "Sum of eligible trade price multiplied by trade size in the completed bar.",
            "vwap": "Sum of eligible trade price multiplied by size, divided by eligible share volume.",
            "macd_line": "12-period EMA minus 26-period EMA of closed-bar prices.",
            "macd_signal": "9-period EMA of the MACD line.",
            "macd_histogram": "MACD line minus MACD signal line.",
        }
        if period_method and separator and period.isdigit():
            operation = f"Computes the {period}-period {period_method}."
        elif leaf.startswith("bollinger_") and leaf.rsplit("_", 1)[-1].isdigit():
            operation = f"Computes the {label} component from a {leaf.rsplit('_', 1)[-1]}-bar moving mean and standard-deviation envelope."
        elif leaf in exact_methods:
            operation = exact_methods[leaf]
        elif producer_id:
            input_text = ", ".join(inputs) if inputs else "its registered inputs"
            operation = f"{producer_label} computes {label} from {input_text}. {calculation_summary}"
        else:
            operation = f"Reads {label} from the accepted QMD input {leaf}; no additional calculation is applied."
    else:
        operation = calculation_summary
    return {
        "documentation_status": "complete" if calculation_summary.strip() else "partial",
        "source_location": (
            f"qmd://{producer_id}" if producer_id else "qmd://accepted-input"
        ),
        "source_fields": sorted(set(inputs)) or [str(row.get("registry_id") or "")],
        "source_summary": source_summary,
        "operation_kind": (
            "producer_output" if producer_id else "source_read"
        ),
        "operation_steps": [
            operation
        ],
        "formula": "",
        "classification_bands": [],
        "calculation_summary": calculation_summary,
        "input_field_ids": sorted(set(inputs)),
        "timeframes": [str(value) for value in (registered.get("timeframes") or parameter_timeframes)],
        "value_type": str(registered.get("value_type") or ("event" if kind == "signal" else "number" if kind in {"field", "derivation"} else "record")),
        "unit": str(registered.get("unit") or "producer_defined"),
        "entity_grain": str(registered.get("entity_grain") or ("security_event" if kind == "signal" else "security_timeframe")),
        "update_cadence": str(registered.get("update_cadence") or "producer cadence"),
        "available_when": str(registered.get("available_when") or f"After {label} publishes a causally complete value."),
        "freshness_summary": str(registered.get("freshness_summary") or "Freshness follows the registered QMD producer revision and market clock."),
        "null_behavior": str(registered.get("null_behavior") or "Unavailable inputs do not produce a substituted value."),
    }


def _apply_classification_documentation(
    definitions_by_id: dict[str, dict[str, Any]],
    configuration: dict[str, Any],
) -> None:
    classifications = list(
        dict(configuration.get("market_discovery") or {}).get("classifications") or []
    )
    families = {
        "classification.market_cap": [
            row for row in classifications
            if str(row.get("classification_id") or "").startswith("market_cap.")
        ],
        "classification.float": [
            row for row in classifications
            if str(row.get("classification_id") or "").startswith("float.")
        ],
    }
    for registry_id, bands in families.items():
        definition = definitions_by_id.get(registry_id)
        if definition is None or not bands:
            continue
        documentation = dict(definition.get("documentation") or {})
        input_ids = [str(value) for value in documentation.get("input_field_ids") or []]
        input_definition = definitions_by_id.get(input_ids[0], {}) if input_ids else {}
        input_documentation = dict(input_definition.get("documentation") or {})
        documentation["source_location"] = str(
            input_documentation.get("source_location")
            or documentation.get("source_location")
            or ""
        )
        documentation["source_fields"] = list(
            input_documentation.get("source_fields") or input_ids
        )
        documentation["classification_bands"] = [
            {
                "band_id": str(row.get("classification_id") or ""),
                "label": str(row.get("name") or row.get("classification_id") or "Band"),
                "minimum": row.get("minimum"),
                "maximum": row.get("maximum"),
                "minimum_inclusive": True,
                "maximum_inclusive": False,
                "unit": str(row.get("unit") or ""),
            }
            for row in bands
        ]
        documentation["documentation_status"] = "complete"
        definition["documentation"] = documentation


def _ensure_data_definition_documentation(
    definitions_by_id: dict[str, dict[str, Any]],
) -> None:
    for registry_id, definition in definitions_by_id.items():
        if str(definition.get("kind") or "") not in {"field", "derivation", "signal"}:
            continue
        documentation = dict(definition.get("documentation") or {})
        producer_id = str(definition.get("producer_id") or "").strip()
        input_ids = [
            str(value) for value in (
                documentation.get("input_field_ids")
                or definition.get("input_field_ids")
                or dict(definition.get("relationships") or {}).get("input_field_ids")
                or []
            ) if str(value).strip()
        ]
        operation = str(
            (documentation.get("operation_steps") or [""])[0]
            or documentation.get("calculation_summary")
            or definition.get("description")
            or ""
        ).strip()
        documentation.setdefault(
            "source_location",
            f"qmd://{producer_id}" if producer_id else f"registry://{definition.get('owner') or 'unknown'}",
        )
        documentation.setdefault("source_fields", input_ids or [registry_id])
        documentation.setdefault(
            "source_summary",
            f"Registered value published by {definition.get('owner') or 'the declared producer'}.",
        )
        documentation.setdefault(
            "operation_kind",
            "producer_output" if producer_id else "registered_method",
        )
        documentation.setdefault(
            "operation_steps",
            [operation or "The exact producer operation is not registered."],
        )
        documentation.setdefault("formula", "")
        documentation.setdefault("classification_bands", [])
        documentation.setdefault(
            "documentation_status",
            "complete" if operation else "partial",
        )
        documentation.setdefault("input_field_ids", input_ids)
        definition["documentation"] = documentation


def information_registry_payload(
    qmd_catalog: dict[str, object],
    configuration: dict[str, Any] | None = None,
) -> dict[str, object]:
    qmd_definitions = dict(qmd_catalog.get("definition_catalog") or {})
    if str(qmd_definitions.get("authority") or "") != "qmd_core_definition_registry":
        raise ValueError("QMD definition registry authority is unavailable")
    if int(qmd_definitions.get("schema_version") or 0) != 1:
        raise ValueError("Unsupported QMD definition registry schema")
    qmd_rows = list(qmd_definitions.get("definitions") or [])
    if not qmd_rows:
        raise ValueError("QMD definition registry is empty")

    qmd_rows_by_id = {
        str(row.get("registry_id") or ""): row
        for row in qmd_rows
        if str(row.get("registry_id") or "").strip()
    }
    definitions_by_id = {
        str(row["registry_id"]): row
        for row in _application_information_definitions()
    }
    registered_kinds = {row.kind for row in REGISTRY_TYPES}
    for qmd_row in qmd_rows:
        registry_id = str(qmd_row.get("registry_id") or "").strip()
        if not registry_id:
            raise ValueError("QMD definition is missing registry_id")
        kind = str(qmd_row.get("kind") or "").strip()
        if kind not in registered_kinds:
            raise ValueError(f"Unknown QMD registry kind for {registry_id}: {kind}")
        existing = definitions_by_id.get(registry_id)
        if existing is not None:
            if existing["kind"] != kind:
                raise ValueError(f"Conflicting registry kind for {registry_id}")
            continue
        normalized_qmd_row = dict(qmd_row)
        normalized_qmd_row["documentation"] = _qmd_operator_documentation(
            qmd_row,
            qmd_rows_by_id,
        )
        normalized_qmd_row["configuration_binding_id"] = (
            "market_discovery.core_scan"
            if kind == "derivation"
            else "market_discovery.signals"
            if kind == "signal"
            else ""
        )
        normalized_qmd_row["relationships"] = {
            "input_field_ids": sorted({str(value) for value in qmd_row.get("input_field_ids") or [] if str(value).strip()}),
            "output_field_ids": sorted({str(value) for value in qmd_row.get("output_field_ids") or [] if str(value).strip()}),
            "producer_ids": sorted({str(qmd_row.get("producer_id"))} if qmd_row.get("producer_id") else set()),
        }
        definitions_by_id[registry_id] = normalized_qmd_row
    for row in _configuration_information_definitions(configuration):
        registry_id = str(row["registry_id"])
        existing = definitions_by_id.get(registry_id)
        if existing is not None:
            # The configuration may expose a selectable projection whose ID is
            # already a canonical Field. Configuration never redefines that
            # registered semantic kind or its producer authority.
            continue
        definitions_by_id[registry_id] = row

    _ensure_data_definition_documentation(definitions_by_id)
    _apply_classification_documentation(definitions_by_id, configuration or {})

    type_rows = [asdict(row) for row in REGISTRY_TYPES]
    binding_rows = [asdict(row) for row in CONFIGURATION_BINDINGS]
    aliases = []
    for row in qmd_rows:
        registry_id = str(row.get("registry_id") or "")
        if registry_id.startswith("qmd.derivation."):
            aliases.append({"alias_id": f"qmd.family.{registry_id.removeprefix('qmd.derivation.')}", "registry_id": registry_id})
        elif registry_id.startswith("qmd.processing_step."):
            key = registry_id.removeprefix("qmd.processing_step.")
            aliases.extend((
                {"alias_id": f"qmd.universal.{key}", "registry_id": registry_id},
                {"alias_id": f"qmd.primitive.{key}", "registry_id": registry_id},
                {"alias_id": f"qmd.primitive.{key.replace('_', '-')}", "registry_id": registry_id},
            ))

    definitions = sorted(definitions_by_id.values(), key=lambda row: str(row["registry_id"]))
    _qualify_duplicate_presentation_labels(definitions)
    payload: dict[str, object] = {
        "schema_version": 1,
        "authority": "application_information_registry",
        "qmd_authority": "qmd_core_definition_registry",
        "types": type_rows,
        "definitions": definitions,
        "configuration_bindings": binding_rows,
        "aliases": sorted(aliases, key=lambda row: row["alias_id"]),
        "counts": {
            "types": len(type_rows),
            "definitions": len(definitions),
            "configurable": sum(bool(row.get("configurable")) for row in definitions),
            "configuration_bindings": len(binding_rows),
            "aliases": len(aliases),
        },
    }
    payload["content_hash"] = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload
