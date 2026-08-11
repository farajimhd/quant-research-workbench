from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

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
    ProductDefinition("qmd.intraday_bars", "Canonical intraday bars", "qmd_core", "bar", "qmd_core::bars", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events"), ("qmd.compact_events",), ("ohlcv", "vwap", "closed_state"), ("snapshot", "delta_stream"), ("core_scan", "watchlist", "strategy_run", "request", "offline"), ALL_MODES, "selected_q_live_plus_rebuildable_cache", 1),
    ProductDefinition("qmd.macro_bars", "Daily and macro bars", "qmd_core", "bar", "qmd_core::market_products", ("qmd.daily_bars",), (), ("daily", "weekly", "monthly", "yearly", "partial_state"), ("snapshot",), ("request", "offline"), ALL_MODES, "daily_authority_derived_macro", 1),
    ProductDefinition("qmd.indicators", "Reusable QMD indicators", "qmd_core", "indicator", "qmd_core::indicators", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events", "reference.point_in_time"), ("qmd.intraday_bars",), ("indicator_rows", "warmup", "provenance"), ("snapshot", "progressive_delta"), ("core_scan", "watchlist", "strategy_run", "request", "offline"), ALL_MODES, "catalog_policy", 1),
    ProductDefinition("qmd.market_signals", "Reusable market observations", "qmd_core", "signal", "qmd_core::market_signal", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events"), ("qmd.indicators",), ("market_signal_event", "evidence"), ("snapshot", "delta_stream"), ("watchlist", "strategy_run", "request", "offline"), ALL_MODES, "decision_snapshot_only", 1),
    ProductDefinition("qmd.scanner", "Market scanner projection", "qmd_gateway", "scanner", "services/qmd-gateway/src/scanner.rs", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events", "reference.point_in_time"), ("qmd.intraday_bars", "qmd.indicators", "qmd.market_signals"), ("candidate_rows", "membership", "as_of", "coverage"), ("snapshot", "delta_stream", "historical_snapshot"), ("core_scan", "watchlist", "strategy_run"), ALL_MODES, "current_projection_plus_journal_evidence", 1),
    ProductDefinition("qmd.chart", "Progressive chart payload", "qmd_history_gateway", "chart", "services/qmd_history_gateway/src/api.rs", ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events", "qmd.daily_bars"), ("qmd.intraday_bars", "qmd.macro_bars", "qmd.indicators", "qmd.market_signals"), ("bars", "indicators", "signals", "structure", "provenance"), ("base_snapshot", "progressive_delta"), ("request",), ALL_MODES, "bounded_revisioned_cache", 1),
    ProductDefinition("qmd.computation_targets", "Scoped computation leases", "qmd_gateway", "control", "services/qmd-gateway/src/computation_targets.rs", ("qmd.live_memory",), ("qmd.indicators", "qmd.market_signals"), ("target_lease", "effective_scope", "expiry"), ("snapshot", "command"), ("watchlist", "strategy_run", "request"), ("live", "paper"), "ephemeral_lease", 1),
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
    _container("signal_stream", "Signal Stream", "frontend/src/app/components/MarketScreenerContainers.tsx", products=("qmd.market_signals",), outputs=("workspace.symbol_context",)),
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
    ConfigurationSchemaDefinition("trading_configuration", "backend", "src/backend/trading_configuration_service.py", 18, ALL_MODES, True),
    ConfigurationSchemaDefinition("strategy_profile", "strategy_runtime", "src/trading_runtime/strategy_engine.py", 3, ALL_MODES, True),
    ConfigurationSchemaDefinition("watchlist", "backend", "src/backend/watchlist_runtime_service.py", 1, ALL_MODES, True),
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
            "market.ticker_presentation.v1",
            "backend",
            "src.backend.query_plans.market_ticker_presentation_v1:ticker_presentation",
            (
                "q_live.feature_tradable_universe_v1",
                "q_live.feature_scanner_static_v1",
                "q_live.id_issuer_v1",
                "q_live.market_presentation_asset_v1",
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
            ),
            "latest canonical symbol, listing, security, and broker conid identity",
            "latest universe_date and feature_date",
            "Reference Gateway inserted_at",
            "q_live.market_reference_publication_coverage_v1",
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
            "src.backend.ticker_facts_service:ticker_facts",
            (
                "q_live.id_symbol_interval_v1",
                "q_live.id_symbol_v1",
                "q_live.id_listing_v1",
                "q_live.id_security_v1",
                "q_live.id_issuer_v1",
            ),
            "source symbol ASOF valid_from <= cutoff < valid_to_exclusive",
            "valid_from",
            "inserted_at",
            "q_live.market_ticker_event_entity_coverage_v1",
        ),
        QueryPlanDefinition(
            "reference.scanner_asof.v1",
            "reference_gateway",
            "src.backend.historical_scanner_service:historical_scanner_reference_projection",
            (
                "q_live.market_security_market_snapshot_v1",
                "q_live.market_security_float_v1",
                "q_live.market_short_interest_v1",
                "q_live.market_security_country_v1",
                "q_live.market_presentation_asset_v1",
                "q_live.market_ipo_v1",
                "q_live.market_stock_split_v1",
            ),
            "point-in-time symbol_id from tradable universe",
            "source observation/effective/publication date",
            "published_at_utc or inserted_at",
            "q_live.market_reference_publication_coverage_v1",
        ),
        QueryPlanDefinition(
            "reference.ticker_facts.v1",
            "reference_gateway",
            "src.backend.ticker_facts_service:ticker_facts",
            (
                "q_live.market_short_volume_v1",
                "q_live.market_fails_to_deliver_v1",
                "q_live.market_reg_sho_threshold_v1",
                "q_live.market_security_borrow_v1",
                "q_live.market_stock_split_v1",
                "q_live.market_cash_dividend_v1",
                "q_live.market_ipo_v1",
            ),
            "point-in-time symbol_id/security_id",
            "source effective/trade/settlement date",
            "source publication timestamp or inserted_at",
            "q_live.market_reference_publication_coverage_v1",
        ),
        QueryPlanDefinition(
            "sec.fundamentals_asof.v1",
            "sec_gateway",
            "src.backend.historical_scanner_service:historical_scanner_fundamental_projection",
            ("q_live.sec_xbrl_company_fact_v3", "q_live.id_sec_market_bridge_v3"),
            "event-valid CIK bridge to issuer/security/listing",
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
            "model.context_asof.v1",
            "model_gateway",
            "service://model-gateway/context-by-artifact",
            ("service://text-embed/embeddings", "service://market-ai/hypotheses"),
            "stable identity + frozen context hash",
            "source event_at",
            "artifact available_at",
            "service://model-gateway/artifact-readiness",
        ),
    )


QUERY_PLANS = _query_plans()


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
) -> FieldDefinition:
    label = field_id.split(".")[-1].replace("_", " ").title()
    return FieldDefinition(
        field_id=field_id,
        label=label,
        group=group,
        value_type=value_type,
        unit=unit,
        entity_grain=entity_grain,
        owner=owner,
        source_path=source_path,
        source_columns=tuple(source_columns) or (field_id.split(".")[-1],),
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
    )


def _fields() -> tuple[FieldDefinition, ...]:
    rows: list[FieldDefinition] = []

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
            "q_live.market_presentation_asset_v1",
            "reference.scanner_asof.v1",
            ("logo_url", "asset_status"),
        ),
    }
    string_fields = {"symbol", "company_name", "security_name", "composite_figi", "share_class_figi", "cik", "conid", "cusip", "isin", "previous_symbol", "current_symbol", "exchange", "primary_exchange", "currency", "asset_class", "security_type", "ticker_type", "block_reason", "listing", "issuer_legal", "headquarters", "issue", "effective", "logo_url", "asset_status"}
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
                "reference.ticker_facts.v1" if table not in {"market_security_market_snapshot_v1", "market_security_float_v1", "market_short_interest_v1"} else "reference.scanner_asof.v1",
                value_type="string" if name in {"float_source", "float_quality", "borrow_status"} else "boolean" if name == "reg_sho_threshold" else "number",
                unit="shares" if name in {"shares_outstanding", "float_shares", "short_interest", "short_volume", "fails_to_deliver", "borrow_shares"} else "percent" if name.endswith("_pct") else "currency" if name in {"market_cap", "ftd_value", "borrow_fee"} else "scalar",
                historical_support="live_observation_only" if live_only else "point_in_time",
                modes=("live", "paper") if live_only else ALL_MODES,
                status="live_only" if live_only else "implemented",
                coverage_query_plan="reference.ticker_facts.v1",
            ))

    for name in ("sector", "industry", "market_cap", "float"):
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
            rows.append(_field(
                f"event.{family}.{name}",
                "corporate_event",
                "backend" if scanner_distance else "reference_gateway",
                "derived://reference-scanner-event-distance" if scanner_distance else source,
                "reference.scanner_asof.v1" if scanner_distance else "reference.ticker_facts.v1",
                value_type="string" if name in {"execution_date", "ex_date", "currency", "date", "status", "event_type", "effective_date", "old_symbol", "new_symbol"} else "number",
                entity_grain="security_event",
                ttl_seconds=None,
                provenance="derived" if scanner_distance else "reported",
                coverage_query_plan="reference.scanner_asof.v1" if scanner_distance else "reference.ticker_facts.v1",
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

    sec_names = ("latest_at", "count", "recency", "latest_form", "cik", "accession", "form", "accepted_at", "filed_at", "period_end", "document_id", "document_type", "source_hash", "renderer_version", "topic", "event_type", "direction", "score", "confidence", "impact", "uncertainty", "entity_relationships", "market_bridge_state")
    for name in sec_names:
        semantic = name in {"topic", "event_type", "direction", "score", "confidence", "impact", "uncertainty", "entity_relationships"}
        rows.append(_field(f"sec.{name}", "sec", "text_intelligence" if semantic else "sec_gateway", "service://text-intelligence/sec-synthesis-v1" if semantic else "service://sec-gateway/filings-v3", "intelligence.sec_asof.v1" if semantic else "sec.filing_asof.v1", value_type="number" if name in {"count", "recency", "score", "confidence", "impact", "uncertainty", "renderer_version"} else "json" if name == "entity_relationships" else "string", entity_grain="sec_filing", ttl_seconds=900, publication_cadence="event_driven", status="integration_pending" if semantic else "implemented"))

    reported_fundamentals = (
        "revenue", "gross_profit", "operating_income", "net_income", "diluted_eps", "operating_cash_flow", "capital_expenditure", "cash", "current_assets", "current_liabilities", "accounts_receivable", "accounts_payable", "inventory", "assets", "liabilities", "stockholders_equity", "long_term_debt", "current_debt", "research_development", "sga_expense", "stock_based_compensation", "interest_expense", "income_tax_expense", "effective_tax_rate_pct", "goodwill", "intangible_assets", "deferred_revenue", "debt_issued", "debt_repaid", "common_stock_issuance", "common_shares_outstanding", "weighted_average_basic_shares", "weighted_average_diluted_shares", "sec_public_float_value", "dividends_per_share", "share_repurchases", "repurchased_shares",
    )
    derived_fundamentals = (
        "free_cash_flow", "gross_margin_pct", "operating_margin_pct", "net_margin_pct", "free_cash_flow_margin_pct", "return_on_assets_pct", "return_on_equity_pct", "working_capital", "current_ratio", "debt_to_equity", "net_debt", "interest_coverage", "revenue_growth_pct", "earnings_growth_pct", "share_growth_pct", "dilution_pct", "cash_conversion", "research_intensity_pct", "sga_intensity_pct", "latest_filing_at", "trajectory_score", "trajectory_label", "profitability_score", "cash_generation_score", "balance_sheet_score", "share_base_pressure_pct", "share_base_discipline_score", "valuation_pe", "valuation_label",
    )
    xbrl_fields = ("quality_score", "quality_label", "quality_coverage_pct", "profitability_score", "growth_score", "cash_quality_score", "balance_sheet_score", "capital_discipline_score")
    for name in reported_fundamentals:
        rows.append(_field(f"fundamental.{name}", "fundamental", "sec_gateway", "q_live.sec_xbrl_company_fact_v3", "sec.fundamentals_asof.v1", unit="percent" if name.endswith("_pct") else "currency_or_shares", entity_grain="issuer_fiscal_period", ttl_seconds=None, publication_cadence="filing_driven", provenance="reported", coverage_query_plan="sec.fundamentals_asof.v1"))
    for name in derived_fundamentals:
        rows.append(_field(f"fundamental.{name}", "fundamental", "backend", "derived://sec-xbrl-company-facts", "sec.fundamentals_asof.v1", value_type="string" if name.endswith("_label") or name.endswith("_at") else "number", unit="percent" if name.endswith("_pct") else "scalar", entity_grain="issuer_fiscal_period", ttl_seconds=None, publication_cadence="filing_driven", provenance="derived", coverage_query_plan="sec.fundamentals_asof.v1"))
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
        ("model.market_hypothesis.payload", "market_ai", "service://market-ai/hypotheses"),
        ("model.market_prediction.payload", "model_gateway", "service://model-gateway/predictions"),
    ):
        rows.append(_field(field_id, "model_context", owner, source, "model.context_asof.v1", value_type="vector" if field_id.startswith("embedding") else "json", entity_grain="frozen_context", ttl_seconds=None, publication_cadence="artifact_or_event_driven", provenance="model", status="integration_pending", coverage_query_plan="model.context_asof.v1"))

    return tuple(rows)


FIELD_DEFINITIONS = _fields()


DISCOVERY_FIELD_PRESENTATIONS = (
    DiscoveryFieldPresentation("identity.symbol", "identity.symbol", "symbol", "Symbol", "Point-in-time ticker identity for the eligible listing.", "reference", True, False, True, (), ("event",)),
    DiscoveryFieldPresentation("identity.company_name", "identity.company_name", "company_name", "Company", "Issuer or security name available for the listing at evaluation time.", "reference", True, False, True, (), ("event",)),
    DiscoveryFieldPresentation("market.last_price", "", "last_price", "Last price", "Most recent causally available eligible trade price.", "market_data", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals", "above_by_bps"), ("1s", "10s", "30s", "1m")),
    DiscoveryFieldPresentation("market.change_pct", "", "change_pct", "Change %", "Percentage change from the completed previous-session close.", "market_data", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("1s", "10s", "30s", "1m")),
    DiscoveryFieldPresentation("market.volume", "", "volume", "Volume", "Cumulative eligible share volume for the current session.", "market_data", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("1s", "10s", "30s", "1m")),
    DiscoveryFieldPresentation("market.relative_volume", "", "relative_volume", "Relative volume", "Cumulative volume versus the aligned 20-session baseline.", "indicator", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("10s", "30s", "1m")),
    DiscoveryFieldPresentation("indicator.vwap.value", "", "vwap", "VWAP", "Causal session volume-weighted average eligible trade price.", "indicator", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals", "above_by_bps"), ("1s", "10s", "30s", "1m")),
    DiscoveryFieldPresentation("reference.market_cap", "reference.market_cap", "market_cap", "Market cap", "Latest point-in-time market capitalization.", "reference", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("1d",)),
    DiscoveryFieldPresentation("classification.market_cap", "classification.market_cap", "market_cap_category", "Cap category", "Small, Mid, or Large classification from the published configuration.", "reference", True, False, True, (), ("1d",)),
    DiscoveryFieldPresentation("reference.float_shares", "reference.float_shares", "float_shares", "Public float", "Tradable share supply with SEC-derived fallback provenance.", "reference", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("1d",)),
    DiscoveryFieldPresentation("classification.float", "classification.float", "float_category", "Float category", "Tiny through Broad Float classification from the published configuration.", "reference", True, False, True, (), ("1d",)),
    DiscoveryFieldPresentation("reference.short_interest", "reference.short_interest", "short_interest", "Short interest", "Latest reported short shares available before evaluation.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("settlement",)),
    DiscoveryFieldPresentation("reference.short_interest_pct", "reference.short_interest_pct", "short_interest_pct", "Short % float", "Short interest divided by point-in-time public float.", "reference", True, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("settlement",)),
    DiscoveryFieldPresentation("reference.days_to_cover", "reference.days_to_cover", "days_to_cover", "Days to cover", "Reported short interest divided by average daily volume.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("settlement",)),
    DiscoveryFieldPresentation("fundamental.trajectory_score", "fundamental.trajectory_score", "fundamental_trajectory", "Fundamental trajectory", "SEC-derived 0-100 financial trajectory score.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("filing",)),
    DiscoveryFieldPresentation("fundamental.quality_score", "fundamental.quality_score", "fundamental_quality", "Fundamental quality", "Coverage and comparability of the supporting SEC facts.", "reference", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("filing",)),
    DiscoveryFieldPresentation("signal.news_labeled", "signal.news_labeled", "", "News labeled", "Validated point-in-time Text Intelligence news-label availability.", "signal", False, True, False, ("is_true",), ("event",)),
    DiscoveryFieldPresentation("signal.company_news.score", "news.score", "news_sentiment", "News sentiment", "Latest validated point-in-time company-news score and label.", "signal", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("signal.sec_labeled", "signal.sec_labeled", "", "SEC labeled", "Validated point-in-time Text Intelligence SEC-label availability.", "signal", False, True, False, ("is_true",), ("event",)),
    DiscoveryFieldPresentation("signal.sec_filing.score", "sec.score", "sec_sentiment", "SEC sentiment", "Latest validated point-in-time filing score and label.", "signal", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("event.ipo.date", "event.ipo.date", "ipo_event", "IPO date", "Point-in-time past or upcoming IPO event date.", "event", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("event.ipo.days_to_event", "event.ipo.days_to_event", "ipo_days_to_event", "IPO event distance", "Signed calendar days from evaluation to the point-in-time IPO event.", "event", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
    DiscoveryFieldPresentation("event.split.execution_date", "event.split.execution_date", "split_event", "Split date", "Latest published stock-split execution date and ratio.", "event", False, False, True, (), ("event",)),
    DiscoveryFieldPresentation("event.split.days_to_event", "event.split.days_to_event", "split_days_to_event", "Split event distance", "Signed calendar days from evaluation to the latest published split execution date.", "event", False, True, True, ("greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"), ("event",)),
)


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
        "schema_version": 4,
        "market_sources": [asdict(source) for source in MARKET_SOURCES],
        "products": [asdict(product) for product in PRODUCT_DEFINITIONS],
        "link_contracts": [asdict(link) for link in LINK_CONTRACTS],
        "containers": [asdict(container) for container in CONTAINER_DEFINITIONS],
        "configuration_schemas": [asdict(schema) for schema in CONFIGURATION_SCHEMAS],
        "compatibility_aliases": [asdict(alias) for alias in COMPATIBILITY_ALIASES],
        "fields": [asdict(field) for field in FIELD_DEFINITIONS],
        "market_discovery_fields": [
            asdict(field) for field in DISCOVERY_FIELD_PRESENTATIONS
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
