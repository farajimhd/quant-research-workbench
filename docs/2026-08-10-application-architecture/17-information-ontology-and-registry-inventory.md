# Information ontology and registry inventory

Status: accepted target terminology with a current-state audit and migration register

Audit date: 2026-08-14

Scope: application fields, QMD processing and derivations, observations, signals,
columns, rules, Watchlists, Strategy inputs, service products, query plans, and
physical ClickHouse storage

## Canonical design

### Type graph

```text
RegistryDefinition
├── FieldDefinition
├── SourceDefinition
├── ProcessingStepDefinition
├── DerivationDefinition
├── SignalDefinition
├── EventSchemaDefinition
├── ProductDefinition
├── QueryPlanDefinition
├── ColumnDefinition
├── ConditionDefinition
├── RuleSetDefinition
├── WatchlistDefinition
├── StrategyInputBinding
├── StrategyDefinition
├── StrategyProfile
└── RunPlan

Runtime values (not registry definitions)
├── Observation
├── Record
├── DatasetPage
└── SignalEvent
```

### RegistryDefinition

| Property | Type / allowed values |
| --- | --- |
| `registry_id` | Stable namespaced string |
| `kind` | `field`, `source`, `processing_step`, `derivation`, `signal`, `event_schema`, `product`, `query_plan`, `column`, `condition`, `rule_set`, `watchlist`, `strategy_binding`, `strategy`, `strategy_profile`, `run_plan` |
| `label` | User-facing string |
| `description` | Short semantic definition |
| `owner` | Registered service/domain ID |
| `version` | Positive integer or immutable revision |
| `status` | `implemented`, `integration_pending`, `live_only`, `planned`, `deprecated`, `retired` |
| `tags` | Registered grouping IDs |

Common metadata only. No semantic inheritance between sibling definitions.

### FieldDefinition

One typed value contract. No `RawField`, `DerivedField`, or `SignalProjectionField` subclasses.

| Property | Type / allowed values |
| --- | --- |
| `field_id` | Stable namespaced ID |
| `label` | User-facing value name |
| `value_type` | `number`, `integer`, `string`, `boolean`, `timestamp`, `date`, `category`, `vector`, `json` |
| `unit` | `currency`, `shares`, `percent`, `basis_points`, `milliseconds`, `score`, `ratio`, `count`, `scalar`, registered unit |
| `entity_grain` | Security, issuer, listing, document, signal, account, or registered composite grain |
| `producer` | `SourceFieldRef`, `ProcessingOutputRef`, `DerivationOutputRef`, or `SignalProjectionRef` |
| `product_ids` | Products that deliver the Field |
| `event_at` | Event/effective clock expression |
| `available_at` | Causal availability clock expression |
| `provenance` | `source`, `derived`, `estimated`, `model` |
| `freshness_policy` | TTL/publication policy |
| `null_reasons` | Registered explicit reason IDs |
| `modes` | Subset of `live`, `paper`, `replay`, `backtest`, `backtest_debug` |

```text
ProducerRef =
  SourceFieldRef(source_id, source_field)
| ProcessingOutputRef(step_id, output_name)
| DerivationOutputRef(derivation_id, output_name)
| SignalProjectionRef(signal_id, property)
```

| Field | Producer reference | Product/status |
| --- | --- | --- |
| `market.last_price` | `ProcessingOutputRef(nbbo_trade_state, last_eligible_trade)` | `qmd.scanner`; current Field, target producer link |
| `reference.float_shares` | `SourceFieldRef(reference.point_in_time, free_float)` | Scanner reference projection; current Field |
| `qmd.bar.close` | `DerivationOutputRef(core_bars, close)` | `qmd.intraday_bars`; registration required before cross-boundary use |
| `indicator.macd.histogram` | `DerivationOutputRef(momentum_core, macd_histogram)` | `qmd.indicators`; target Field/alias mapping required |
| `classification.market_cap` | `DerivationOutputRef(reference_market_cap_classification, category)` | Reference Scanner projection; current Field, target producer link |
| `signal.liquidity_dislocation.score` | `SignalProjectionRef(liquidity_dislocation, score)` | `qmd.market_signals`; target Field/alias mapping required |

### Runtime value contracts

| Type | Required identity | Payload |
| --- | --- | --- |
| `Observation` | `field_id`, entity identity, `event_at`, `available_at`, source/schema revision | Typed value, provenance, freshness, null reason |
| `Record` | Product schema, entity identity, clocks | Named Field observations |
| `DatasetPage` | Product/revision, bounds, cursor | Records, coverage, completeness |
| `SignalEvent` | `signal_id`, `event_id`, entity, lifecycle state, clocks | Score/confidence/direction, evidence, expiry/resolution |

### SourceDefinition

| Property | Value |
| --- | --- |
| `source_id` | Stable source ID |
| `transport` | `websocket`, `clickhouse`, `http`, `snapshot_delta`, `file` |
| `event_clock` / `availability_clock` | Required clock contracts |
| `coverage_path` / `watermark_path` | Required operational contracts |
| `authoritative_for` | Declared evidence domains |
| `retention_policy` | Source retention |

### ProcessingStepDefinition

| Property | Value |
| --- | --- |
| `step_id` | Stable processing ID |
| `input_schema_ids` / `output_schema_ids` | Artifact/state schemas |
| `execution_scope` | Normally `universal_ingest` |
| `implementation` / `version` | Compiled implementation reference |
| `required` | Boolean |

Current values: validation, identity preservation, sequencing, NBBO/trade state, freshness/quality, persistence/fanout.

### DerivationDefinition

| Property | Type / allowed values |
| --- | --- |
| `derivation_id` | Stable namespaced ID |
| `derivation_type` | `metric`, `indicator`, `oscillator`, `classification`, `ranking`, `aggregation` |
| `input_field_ids` | Non-empty Field references |
| `output_field_ids` | Non-empty Field references |
| `parameters` | Typed parameter definitions/defaults |
| `supported_timeframes` | Registered timeframes |
| `execution_scopes` | Subset of `core_scan`, `watchlist`, `strategy_run`, `request`, `offline` |
| `warmup` | Required history/state |
| `execution_mode` | `set_based_sql`, `vectorized_batch`, `incremental_state_machine` |
| `implementation` / `version` | Compiled implementation authority |

```text
MACD Derivation
inputs  = [qmd.bar.close]
outputs = [indicator.macd.line, indicator.macd.signal, indicator.macd.histogram]
type    = indicator
mode    = incremental_state_machine
```

Indicator and oscillator are Derivation types. Their outputs are Fields.

### SignalDefinition

| Property | Type / allowed values |
| --- | --- |
| `signal_id` | Stable namespaced ID |
| `input_field_ids` | Field references |
| `lifecycle_states` | `opened`, `updated`, `resolved`, `expired` |
| `event_schema_id` | SignalEvent schema reference |
| `projection_field_ids` | Optional scalar Field projections |
| `execution_scopes` | `watchlist`, `strategy_run`, `request`, `offline` |
| `execution_mode` | `vectorized_batch` or `incremental_state_machine` |
| `implementation` / `version` | Compiled detector authority |

```text
Liquidity Dislocation Signal
inputs      = [market.spread_bps, market.liquidity_score, market.trade_rate_10s]
events      = liquidity_dislocation_event.v1
projections = [signal.liquidity_dislocation.state,
               signal.liquidity_dislocation.score,
               signal.liquidity_dislocation.confidence]
```

SignalEvent is authoritative. Projection Fields support scalar columns, rules, and Strategy bindings.

### EventSchemaDefinition

| Property | Value |
| --- | --- |
| `event_schema_id` | Stable schema ID |
| `event_type` | Corporate event, market signal, News, SEC, order, execution, membership |
| `identity_fields` | Event/entity identity |
| `clock_fields` | Observed, effective, available, resolved, expired clocks |
| `property_fields` | Typed event properties |
| `evidence_schema` | Provenance/evidence contract |

### ProductDefinition and QueryPlanDefinition

| Definition | Role | Required references |
| --- | --- | --- |
| `ProductDefinition` | Delivered record, dataset, or stream | Source IDs, dependency products, output schema/Field IDs, scopes, delivery, persistence |
| `QueryPlanDefinition` | Bounded causal retrieval | Source paths, identity join, event clock, availability clock, coverage, implementation |

```text
ProductDefinition
  product_id, product_kind
  source_ids[], dependency_product_ids[]
  output_field_ids[], output_event_schema_ids[]
  delivery_modes[], execution_scopes[], supported_modes[]
  persistence_policy, implementation, version

QueryPlanDefinition
  plan_id, source_paths[], implementation
  identity_join, event_clock, availability_clock
  coverage_path, bounded, point_in_time, version
```

### ColumnDefinition

Presentation composition only. No value semantics.

```text
ColumnDefinition
  column_id, label, renderer
  bindings[]:
    FieldBinding(field_id, role)
    SignalBinding(signal_id, event_view, role)
  primary_binding
  sort_binding?
  filter_binding?
  format, alignment, visibility
```

| Property | Allowed values |
| --- | --- |
| Binding role | `primary`, `secondary`, `badge`, `icon`, `trend`, `detail` |
| Signal event view | `latest_state`, `latest_event`, `active_event`, `event_count`, `recency` |
| Renderer | `number`, `currency`, `percent`, `category`, `timestamp`, `symbol_composite`, `indicator_composite`, `signal_state`, registered renderer |

| Column | Bindings |
| --- | --- |
| `last_price` | `FieldBinding(market.last_price, primary)` |
| `macd` | MACD line, signal, histogram FieldBindings; histogram sort binding |
| `vwap_transition` | `SignalBinding(vwap_transition, active_event, primary)` or projected score FieldBinding |
| `symbol` | Symbol/company/logo FieldBindings plus News/SEC event accessors |

| Current status | Current `column_id` values |
| --- | --- |
| Implemented identity/market | `symbol`, `company_name`, `last_price`, `previous_close`, `change_pct`, `volume`, `relative_volume`, `vwap`, `exchange`, `country`, `sector`, `is_tradable` |
| Implemented market state | `market_event_at`, `market_event_age_ms`, `market_quality_state`, `market_quality_flags`, `market_degradation_reason`, `liquidity_rank`, `spread_bps`, `trade_rate_10s`, `trade_rate_60s`, `liquidity_score` |
| Implemented reference | `market_cap`, `market_cap_category`, `shares_outstanding`, `float_shares`, `float_category`, `float_source`, `float_quality`, `short_pressure`, `short_interest`, `short_interest_pct`, `days_to_cover`, `short_volume`, `short_volume_pct`, `fails_to_deliver`, `ftd_value`, `reg_sho_threshold` |
| Live-only reference | `borrow_status`, `borrow_shares`, `borrow_fee` |
| Implemented fundamental/event | `fundamental_trajectory`, `fundamental_quality`, `ipo_event`, `ipo_days_to_event`, `split_event`, `split_days_to_event` |
| Integration-pending | `news_sentiment`, `sec_sentiment` |
| No ColumnDefinition | `signal.news_labeled`, `signal.sec_labeled` |

### Composition definitions

| Definition | Required references | User result |
| --- | --- | --- |
| `ConditionDefinition` | Left Field/Signal property, comparator, right Field/value, timeframe | Readable Boolean clause |
| `RuleSetDefinition` | Condition IDs, `all`/`any`, score policy | Removable Rule-set card |
| `WatchlistDefinition` | Source scan, Rule-set IDs, ranking Field, limit/expiry/overrides, Column IDs, Derivation IDs | Persistent Watchlist tab/container |
| `StrategyInputBinding` | Field or Signal property ID, runtime binding, timeframe/anchor, availability policy | Evidence-binding chip |
| `StrategyDefinition` | Strategy ID/revision, required bindings, stages, decision contract | Executable Strategy implementation |
| `StrategyProfile` | Strategy ID/revision, parameters, evidence/rule references | Configured Strategy card |
| `RunPlan` | Strategy Profile, Watchlists, Canvas profile, mode, account/broker/data plans | Versioned executable selection |

```text
ConditionDefinition
  condition_id
  left_ref       = FieldRef | SignalPropertyRef
  comparator     = equals | not_equals | greater_than | greater_or_equal |
                   less_than | less_or_equal | above_by_bps | is_true |
                   is_false | in | not_in | state_is
  right_ref      = FieldRef | ConstantValue
  timeframe, parameters, enabled

RuleSetDefinition
  rule_set_id, condition_ids[]
  operator       = all | any
  required_score, enabled

WatchlistDefinition
  watchlist_id, source_scan_id
  rule_set_ids[], ranking_field_id, ranking_direction
  maximum_size, refresh_interval_ms
  membership_expiry, membership_ttl_ms
  manual_inclusions[], manual_exclusions[]
  column_ids[], derivation_ids[]

StrategyInputBinding
  binding_id
  source_ref      = FieldRef | SignalPropertyRef
  runtime_name, timeframe, anchor
  availability_policy, required

StrategyDefinition
  strategy_id, revision, implementation
  required_binding_ids[], stage_definitions[]
  intent_schema_id, checkpoint_schema_id

StrategyProfile
  profile_id, strategy_id, strategy_revision
  parameter_values, binding_ids[], rule_set_ids[]

RunPlan
  run_plan_id, mode
  strategy_profile_id, watchlist_ids[], canvas_profile_id
  account_binding_id, portfolio_policy_id, oms_policy_id
  data_plan_id, source_revision_policy
```

### Relationship map

| From | Relationship | To |
| --- | --- | --- |
| Source | produces | Fields / source event schemas |
| Processing step | transforms | Artifact/state schemas |
| Derivation | consumes / produces | Fields / Fields |
| Signal | consumes / emits / projects | Fields / SignalEvents / Fields |
| Product | delivers | Records, DatasetPages, SignalEvents |
| Column | presents | Fields and/or Signal event views |
| Condition | evaluates | Fields or Signal properties |
| Rule set | composes | Conditions |
| Watchlist | composes | Rule sets, ranking Field, Columns, Derivations |
| Strategy binding | adapts | Field or Signal property |
| Strategy Profile | configures | StrategyDefinition |
| Run Plan | selects | Profile, Watchlists, Canvas, mode/account/data plans |

### Current-to-target mapping

| Current object | Target object | Action |
| --- | --- | --- |
| `FieldDefinition` | `FieldDefinition` | Retain; add typed `producer` reference |
| `DiscoveryFieldPresentation` | `ColumnDefinition` | Replace single-field assumption with typed bindings |
| QMD `primitive` capability | `ProcessingStepDefinition` | Rename and type |
| QMD `indicator_family` capability | `DerivationDefinition` | Register outputs as Fields only when cross-boundary/selectable |
| QMD `market_observation` capability | `SignalDefinition` + `EventSchemaDefinition` | Preserve lifecycle; register scalar projections separately |
| Backend `core_scan.calculations` | Split definitions | Remove mixed collection |
| Frontend `DiscoveryCapability` | `CatalogItemView` | Presentation projection only |
| Frontend `canonicalCapabilityType` | Remove | Consume explicit `kind` |
| Strategy `runtime_field` aliases | `StrategyInputBinding` | Explicit canonical source mapping |

### User access mapping

| Surface | Picker/view | Selectable definitions | Result |
| --- | --- | --- | --- |
| Universal Ingest | Processing | Processing steps | Read-only step cards |
| Core Scan | Fields / Indicators | Fields, Core Derivations | Core record schema and computations |
| Watchlist Rules | Rule sets / Fields / Signals | Rule sets and rule operands | Rule-set cards |
| Watchlist Columns | Data | Fields, Derivation outputs/composites, Signals | Column chips and table columns |
| Watchlist Calculations | Indicators | Derivations | Enabled derivation instances |
| Scanner/Watchlist runtime | Columns | ColumnDefinitions | Formatted cells |
| Chart | Indicators / Signals | Derivations, Fields, Signals | Series, panes, markers |
| Strategy Studio | Evidence | Fields, Signal properties, Rule sets | StrategyInputBindings |
| Facts/News/SEC/XBRL | Grouped values/events | Fields, records, events | Values and evidence cards |
| Service Health | Sources / Products / Processing / Coverage | Administrative definitions | Operational tables |
| Run Plan | Profiles / Watchlists / Canvas | Existing configured parts | Versioned references |

Shared control: `Data & Analytics picker`.

```text
CatalogItemView
  kind, canonical_id, label, description, group
  owner, provenance, status, supported_timeframes
  allowed_actions, output_field_ids?, event_property_ids?
```

Allowed actions: `add_column`, `add_operand`, `enable_derivation`, `add_signal`, `add_rule_set`, `add_watchlist`, `add_strategy_evidence`, `add_chart_series`.

### Concrete composition: Top Mid Cap Gainers

```text
WatchlistDefinition(top-mid-cap-gainers)
  source_scan          = qmd-core-scan
  rule_set_ids         = [watchlist-mid-caps, watchlist-positive-gainer]
  ranking_field_id     = market.change_pct
  ranking_direction    = descending
  maximum_size         = 250
  column_ids           = [symbol, last_price, change_pct, volume,
                          relative_volume, market_cap, market_cap_category,
                          float_shares, float_category, short_interest_pct]
  focused_derivations  = declared Watchlist calculation IDs
  expiry               = end_of_trading_day
```

### Execution contract

| Work | Required execution mode |
| --- | --- |
| Historical/cross-sectional reads | Set-based ClickHouse query |
| Bulk field transforms | Native Arrow/Polars/vectorized compiled batch |
| Live indicators/signals | Compiled incremental state machine with bounded batches |
| Multi-consumer requests | Deduplicated by derivation, parameters, timeframe, anchor, source revision |
| Python | Configuration, validation, orchestration, bounded metadata assembly |

Prohibited data-plane patterns: per-row Python UDF, `iter_rows`, per-ticker query, per-field service request, duplicate calculation per consumer.

### Registration decision map

| Existing object | Register as | Condition |
| --- | --- | --- |
| Cross-boundary typed value | FieldDefinition | Consumer-selectable or delivered by a product |
| Source acquisition/read | SourceDefinition | Authoritative evidence boundary |
| Integrity/transport operation | ProcessingStepDefinition | Versioned observable step |
| Reusable computation | DerivationDefinition | Declared inputs/outputs/version/scope |
| Lifecycle detector | SignalDefinition + EventSchemaDefinition | Declared evidence, states, clocks, version |
| Table presentation | ColumnDefinition | Approved typed bindings and renderer |
| Bounded read | QueryPlanDefinition | Identity/clocks/coverage/implementation declared |
| Delivered record/stream | ProductDefinition | Output schemas and delivery declared |
| Staging/backup/scratch/cache | Storage inventory | Never semantic authority |
| Training/model artifact | Dataset/model manifest | Only promoted product outputs become Fields |

## Current-state audit summary

| Audit surface | Endpoint / authority | Verification |
| --- | --- | --- |
| Application registry | Repository source | Loaded and validated |
| Backend configuration | `127.0.0.1:8000` | Queried |
| QMD History | `127.0.0.1:8801` | Shared catalog queried |
| ClickHouse | `system.tables`, `system.columns` | Live inventory queried |
| QMD Live | `127.0.0.1:8795` | Unavailable; no operational claim |
| Shared QMD catalog | Rust source + QMD History | Source/catalog verified |

| Registry or runtime surface | Current count | Finding |
| --- | ---: | --- |
| Static application field definitions | 229 | Canonical semantic field records in `application_registry.py`. |
| Static Market Discovery presentations | 51 | 49 resolve into the live column catalog; two integration-pending labeled-signal presentations are intentionally absent. |
| Resolved Market Discovery field catalog | 255 | It mixes fields with 31 QMD/runtime processing, event, and alias rows while omitting/replacing five static IDs. This collection is not currently a pure field registry. |
| Resolved Market Discovery columns | 49 | Presentation projections only; this should remain separate from fields. |
| Market Discovery processing/analytic entries | 45 | Six Universal steps, field projections, derivations, event products, signal projections, and system composites are mixed under `core_scan.calculations`. |
| Rule sets | 41 | Reusable condition compositions. |
| Watchlists | 19 | One Core set plus 18 configured Watchlists. |
| Strategy input bindings | 21 | Runtime aliases over registered fields/signal projections. |
| QMD primitive processing steps | 6 | Currently named `primitive` capabilities. |
| QMD derivation families | 26 | Currently named `indicator_family` capabilities. |
| QMD signal definitions | 7 | Currently named `market_observation` capabilities. |
| QMD derivation output names | 295 (294 unique) | Only 8 names directly match the current application field/presentation namespace. The other 287 require semantic review if exposed; they must not be bulk-promoted automatically. |
| Application query plans | 28 | Bounded point-in-time read contracts. |
| Market sources / products / links / containers | 6 / 8 / 5 / 22 | Separate registries already exist and should remain separate. |
| Configuration schemas / compatibility aliases | 13 / 1 | Reusable larger-part contracts and one deprecated endpoint alias; semantic field aliases are not yet registered. |

### Confirmed mixed-registry drift

The resolved field catalog adds processing steps (`qmd.primitive.*`), event
products (`news-events`, `sec-events`, `membership-history`), system composites,
signal projections, indicator projections, and runtime aliases. Five static IDs
are replaced rather than represented through explicit aliases:

| Static field ID | Current resolved replacement |
| --- | --- |
| `listing.exchange` | `identity.exchange` |
| `tradability.is_tradable` | `identity.is_tradable` |
| `market.vwap` | `indicator.vwap.value` |
| `news.score` | `signal.company_news.score` |
| `sec.score` | `signal.sec_filing.score` |

| Drift site | Current behavior | Target |
| --- | --- | --- |
| `_qmd_runtime_capabilities` | Adds mixed kinds | Emit typed definitions |
| `_market_discovery_field_catalog` | Builds mixed catalog | Return FieldDefinitions only |
| `_bind_discovery_scanner_columns` | Infers bindings | Resolve ColumnDefinition IDs |
| `_watchlist_column_catalog` | Rebuilds column semantics | Resolve ColumnDefinition IDs |
| `DiscoveryCapability` | Combines outputs and `scanner_columns` | Typed definition union |
| `canonicalCapabilityType` | Infers kind from names | Read explicit `definition_kind` |
| Five replacements above | Silent replacement | Versioned alias or canonical-ID migration |

## Application field inventory

All 229 current FieldDefinitions are listed below. A row groups fields only when
they share owner, source, query plan, and implementation status.

| Semantic group (count) | Field IDs | Owner and source | Query plan | Status / required action |
| --- | --- | --- | --- | --- |
| Identity (17) | `identity.issuer_id`, `identity.security_id`, `identity.listing_id`, `identity.symbol_id`, `identity.symbol`, `identity.company_name`, `identity.security_name`, `identity.composite_figi`, `identity.share_class_figi`, `identity.cik`, `identity.conid`, `identity.cusip`, `identity.isin`, `identity.valid_from`, `identity.valid_to_exclusive`, `identity.previous_symbol`, `identity.current_symbol` | Reference Gateway; `q_live.id_symbol_interval_v1` | `reference.identity_for_symbol.v1` | Registered; retain. |
| Listing (6) | `listing.exchange`, `listing.primary_exchange`, `listing.currency`, `listing.asset_class`, `listing.security_type`, `listing.ticker_type` | Reference Gateway; `q_live.id_listing_v1` | `reference.identity_for_symbol.v1` | Registered; make alias handling explicit. |
| Tradability (3) | `tradability.is_tradable`, `tradability.block_reason`, `tradability.issue_count` | Reference Gateway; `q_live.feature_tradable_universe_v1` | `reference.universe_snapshot.v1` | Registered; make alias handling explicit. |
| Presentation (2) | `presentation.logo_url`, `presentation.asset_status` | Reference Gateway; `q_live.market_presentation_asset_v1` | `reference.scanner_asof.v1` | Registered; presentation evidence, not ColumnDefinitions. |
| Country (5) | `country.listing`, `country.issuer_legal`, `country.headquarters`, `country.issue`, `country.effective` | Reference Gateway; `q_live.market_security_country_v1` | `reference.scanner_asof.v1` | Registered; retain. |
| Classification (5) | `classification.sector`, `classification.industry`, `classification.market_cap`, `classification.float`, `classification.short_pressure` | Backend derivation; `derived://reference-classification` | `reference.scanner_asof.v1` | Registered output fields; add explicit DerivationDefinitions and threshold/taxonomy versions. |
| Market reference (2) | `reference.market_cap`, `reference.shares_outstanding` | Reference Gateway; `q_live.market_security_market_snapshot_v1` | `reference.scanner_asof.v1` | Registered; retain. |
| Market reference (3) | `reference.float_shares`, `reference.float_source`, `reference.float_quality` | Reference Gateway; `q_live.market_security_float_v1` | `reference.scanner_asof.v1` | Registered; retain. |
| Market reference (3) | `reference.short_interest`, `reference.short_interest_pct`, `reference.days_to_cover` | Reference Gateway; `q_live.market_short_interest_v1` | `reference.scanner_asof.v1` | Registered; derived percentage needs explicit input/version metadata. |
| Market reference (2) | `reference.short_volume`, `reference.short_volume_pct` | Reference Gateway; `q_live.market_short_volume_v1` | `reference.scanner_asof.v1` | Registered; retain. |
| Market reference (2) | `reference.fails_to_deliver`, `reference.ftd_value` | Reference Gateway; `q_live.market_fails_to_deliver_v1` | `reference.scanner_asof.v1` | Registered; retain. |
| Market reference (1) | `reference.reg_sho_threshold` | Reference Gateway; `q_live.market_reg_sho_threshold_v1` | `reference.scanner_asof.v1` | Registered; retain. |
| Market reference (3) | `reference.borrow_status`, `reference.borrow_shares`, `reference.borrow_fee` | Reference Gateway; `q_live.market_security_borrow_v1` | `reference.scanner_asof.v1` | Registered `live_only`; historical consumers must fail/mark unavailable. |
| QMD Scanner (16) | `market.last_price`, `market.previous_close`, `market.change_pct`, `market.volume`, `market.relative_volume`, `market.vwap`, `market.spread_bps`, `market.trade_rate_10s`, `market.trade_rate_60s`, `market.liquidity_score`, `market.event_at`, `market.event_age_ms`, `market.quality_state`, `market.quality_flags`, `market.degradation_reason`, `market.liquidity_rank` | QMD Gateway; `service://qmd/scanner` | `qmd.scanner.snapshot.v1` | Registered; separate raw fields, derived fields, and ranking outputs through producer references. |
| Corporate event (4) | `event.split.execution_date`, `event.split.from`, `event.split.to`, `event.split.factor` | Reference Gateway; `q_live.market_stock_split_v1` | `reference.ticker_facts.v1` | Registered. |
| Corporate event (2) | `event.split.days_to_event`, `event.ipo.days_to_event` | Backend derivation; `derived://reference-scanner-event-distance` | `reference.scanner_asof.v1` | Registered output fields; add DerivationDefinition. |
| Corporate event (3) | `event.dividend.ex_date`, `event.dividend.amount`, `event.dividend.currency` | Reference Gateway; `q_live.market_cash_dividend_v1` | `reference.ticker_facts.v1` | Registered. |
| Corporate event (2) | `event.ipo.date`, `event.ipo.status` | Backend projection; `q_live.market_ipo_v1` | `reference.scanner_asof.v1` | Registered. |
| Corporate event (4) | `event.ticker_change.event_type`, `event.ticker_change.effective_date`, `event.ticker_change.old_symbol`, `event.ticker_change.new_symbol` | Reference Gateway; `q_live.market_ticker_event_v1` | `reference.ticker_facts.v1` | Registered. |
| Fundamental raw (37) | `fundamental.revenue`, `fundamental.gross_profit`, `fundamental.operating_income`, `fundamental.net_income`, `fundamental.diluted_eps`, `fundamental.operating_cash_flow`, `fundamental.capital_expenditure`, `fundamental.cash`, `fundamental.current_assets`, `fundamental.current_liabilities`, `fundamental.accounts_receivable`, `fundamental.accounts_payable`, `fundamental.inventory`, `fundamental.assets`, `fundamental.liabilities`, `fundamental.stockholders_equity`, `fundamental.long_term_debt`, `fundamental.current_debt`, `fundamental.research_development`, `fundamental.sga_expense`, `fundamental.stock_based_compensation`, `fundamental.interest_expense`, `fundamental.income_tax_expense`, `fundamental.effective_tax_rate_pct`, `fundamental.goodwill`, `fundamental.intangible_assets`, `fundamental.deferred_revenue`, `fundamental.debt_issued`, `fundamental.debt_repaid`, `fundamental.common_stock_issuance`, `fundamental.common_shares_outstanding`, `fundamental.weighted_average_basic_shares`, `fundamental.weighted_average_diluted_shares`, `fundamental.sec_public_float_value`, `fundamental.dividends_per_share`, `fundamental.share_repurchases`, `fundamental.repurchased_shares` | SEC Gateway; `q_live.sec_xbrl_company_fact_v3` | `sec.fundamentals_asof.v1` | Registered. “Raw” here means source fact projection, not absence of XBRL normalization. |
| Fundamental derived (29) | `fundamental.free_cash_flow`, `fundamental.gross_margin_pct`, `fundamental.operating_margin_pct`, `fundamental.net_margin_pct`, `fundamental.free_cash_flow_margin_pct`, `fundamental.return_on_assets_pct`, `fundamental.return_on_equity_pct`, `fundamental.working_capital`, `fundamental.current_ratio`, `fundamental.debt_to_equity`, `fundamental.net_debt`, `fundamental.interest_coverage`, `fundamental.revenue_growth_pct`, `fundamental.earnings_growth_pct`, `fundamental.share_growth_pct`, `fundamental.dilution_pct`, `fundamental.cash_conversion`, `fundamental.research_intensity_pct`, `fundamental.sga_intensity_pct`, `fundamental.latest_filing_at`, `fundamental.trajectory_score`, `fundamental.trajectory_label`, `fundamental.profitability_score`, `fundamental.cash_generation_score`, `fundamental.balance_sheet_score`, `fundamental.share_base_pressure_pct`, `fundamental.share_base_discipline_score`, `fundamental.valuation_pe`, `fundamental.valuation_label` | Backend derivation; `derived://sec-xbrl-company-facts` | `sec.fundamentals_asof.v1` | Registered outputs; DerivationDefinitions are missing. |
| Fundamental quality (1) | `fundamental.quality_score` | Backend derivation; `derived://sec-xbrl-company-facts/xbrl-quality` | `sec.fundamentals_asof.v1` | Registered output; DerivationDefinition is missing. |
| XBRL quality (8) | `xbrl.quality_score`, `xbrl.quality_label`, `xbrl.quality_coverage_pct`, `xbrl.profitability_score`, `xbrl.growth_score`, `xbrl.cash_quality_score`, `xbrl.balance_sheet_score`, `xbrl.capital_discipline_score` | Backend derivation; `derived://sec-xbrl-company-facts` | `sec.fundamentals_asof.v1` | Registered outputs; DerivationDefinitions are missing. |
| SEC canonical (15) | `sec.latest_at`, `sec.count`, `sec.recency`, `sec.latest_form`, `sec.cik`, `sec.accession`, `sec.form`, `sec.accepted_at`, `sec.filed_at`, `sec.period_end`, `sec.document_id`, `sec.document_type`, `sec.source_hash`, `sec.renderer_version`, `sec.market_bridge_state` | SEC Gateway; `service://sec-gateway/filings-v3` | `sec.filing_asof.v1` | Registered. |
| SEC semantics (8) | `sec.topic`, `sec.event_type`, `sec.direction`, `sec.score`, `sec.confidence`, `sec.impact`, `sec.uncertainty`, `sec.entity_relationships` | Text Intelligence; `service://text-intelligence/sec-synthesis-v1` | `intelligence.sec_asof.v1` | Registered `integration_pending`; do not claim runtime availability. |
| News canonical (8) | `news.latest_at`, `news.count`, `news.recency`, `news.latest_title`, `news.document_id`, `news.source_id`, `news.publisher`, `news.url` | News Gateway; canonical company-news service | `news.company_asof.v1` | Registered. |
| News semantics (12) | `news.topic`, `news.event_type`, `news.entities`, `news.relationships`, `news.direction`, `news.score`, `news.confidence`, `news.impact`, `news.uncertainty`, `news.horizon`, `news.eligible`, `news.expires_at` | Text Intelligence; `service://text-intelligence/news-synthesis-v1` | `intelligence.news_asof.v1` | Registered `integration_pending`. |
| Intelligence projections (2) | `signal.news_labeled`, `signal.sec_labeled` | Text Intelligence News/SEC Synthesis | `intelligence.news_asof.v1`, `intelligence.sec_asof.v1` | Registered `integration_pending`; these are Boolean/event projections, not SignalDefinitions. |
| Model context (4) | `embedding.news.vector`, `embedding.sec.vector`, `model.market_hypothesis.payload`, `model.market_prediction.payload` | Text Embed, Market AI, Model Gateway services | `model.context_asof.v1` | Registered `integration_pending`; remain non-column by default. |
| Quality and coverage (5) | `quality.mapping_issue_count`, `quality.mapping_issue_types`, `quality.mapping_blocked`, `quality.source_mapping_state`, `quality.source_mapping_evidence` | Reference Gateway; `q_live.id_mapping_issue_v1` | `reference.schema_inventory.v1` | Registered diagnostics. |
| Quality and coverage (3) | `relationship.issuer_type`, `relationship.valid_from`, `relationship.valid_to` | Reference Gateway; `q_live.id_issuer_relationship_v1` | `reference.schema_inventory.v1` | Registered diagnostics/evidence. |
| Quality and coverage (3) | `sec.bridge_state`, `sec.bridge_reason`, `sec.bridge_version` | Reference Gateway; `q_live.id_sec_market_bridge_v3` | `reference.schema_inventory.v1` | Registered diagnostics/evidence. |
| Quality and coverage (5) | `coverage.reference_source`, `coverage.window_start`, `coverage.window_end`, `coverage.state`, `coverage.ticker_event_entity_state` | Reference Gateway; publication coverage | `reference.schema_inventory.v1` | Registered diagnostics. |
| Quality and coverage (4) | `schedule.source`, `schedule.next_due_at`, `schedule.last_completed_at`, `schedule.state` | Reference Gateway; source schedule | `reference.schema_inventory.v1` | Registered operations fields; not ordinary Scanner columns. |

## QMD production inventory

### Universal processing steps

These six current `primitive` catalog entries should be renamed
`ProcessingStepDefinition`. Their outputs are artifacts/state schemas, not
ordinary application fields.

| ID | Outputs | Scope / status |
| --- | --- | --- |
| `event_validation_encoding` | canonical compact event; rejection reason | `universal_ingest`; implemented |
| `point_in_time_source_identity` | stable source identity; identity validity evidence | `universal_ingest`; implemented |
| `event_order_sequence` | ordered event; sequence gap state; continuation cursor | `universal_ingest`; implemented |
| `nbbo_trade_state` | current NBBO; last eligible trade; market state revision | `universal_ingest`; implemented |
| `freshness_quality` | freshness; quality flags; degradation reason | `universal_ingest`; implemented |
| `compact_persistence_fanout` | q_live event row; coverage update; live event notification | `universal_ingest`; implemented |

### Derivation families and output registration queue

| Current object | Target object | Output rule | Unmatched output |
| --- | --- | --- | --- |
| 26 `indicator_family` entries | `DerivationDefinition` with subtype | Register FieldDefinition when cross-boundary or selectable | Semantic review queue |

| Derivation ID | Scope / status | Existing outputs | Current application-field action |
| --- | --- | --- | --- |
| `core_bars` | Core / implemented | `open`, `high`, `low`, `close`, `volume`, `dollar_volume`, `trade_count`, `vwap`, `price_change_pct`, `high_low_range_pct` | `volume` and `vwap` directly match; review/register 8 if exposed. |
| `quote_mid_spread_bars` | Core / implemented | `bid_open`, `bid_high`, `bid_low`, `bid_close`, `ask_open`, `ask_high`, `ask_low`, `ask_close`, `mid_open`, `mid_high`, `mid_low`, `mid_close`, `spread_mean`, `spread_bps_mean`, `spread_bps_close` | Review/register 15 if exposed. |
| `session_context` | Watchlist / planned realtime | `minute_of_day`, `session_phase`, `session_elapsed_pct`, `day_open`, `day_high`, `day_low`, `distance_from_open_pct`, `distance_from_day_high_pct`, `distance_from_day_low_pct`, `gap_from_previous_close_pct` | Review/register 10 only with implemented producer/version. |
| `opening_range` | Watchlist / planned realtime | `opening_range_high`, `opening_range_low`, `opening_range_mid`, `opening_range_volume`, `opening_range_dollar_volume`, `opening_range_breakout`, `opening_range_reclaim`, `opening_range_position_pct` | Review/register 8 only with implemented producer/version. |
| `tape_rates` | Core / implemented | `trade_rate_10s`, `trade_rate_60s`, `quote_rate_10s`, `quote_rate_60s`, `trade_accel_10s_60s`, `quote_accel_10s_60s` | Two trade rates registered; review/register 4. |
| `tape_pressure` | Watchlist / implemented | `rolling_vwap_60s`, `tape_imbalance_60s`, `buy_pressure_60s`, `sell_pressure_60s`, `buy_volume`, `sell_volume`, `buy_sell_volume_delta`, `cumulative_delta` | Review/register 8 if exposed. |
| `flow_structure_composite` | Watchlist / implemented | `microstructure_unified_signal`, `microstructure_unified_confidence`, `microstructure_unified_action`, `flow_structure_composite_score`, `flow_structure_composite_confidence`, `flow_structure_composite_bias`, `flow_structure_composite_reason`, `microstructure_buy_trade_count`, `microstructure_sell_trade_count`, `microstructure_buy_volume`, `microstructure_sell_volume`, `microstructure_signed_volume_delta`, `microstructure_cumulative_signed_volume_delta`, `microstructure_anchored_flow_relationship`, `microstructure_anchored_flow_relationship_score`, `microstructure_transaction_imbalance`, `microstructure_signed_volume_imbalance`, `microstructure_level1_ofi_delta`, `microstructure_cumulative_level1_ofi`, `microstructure_level1_ofi`, `microstructure_queue_imbalance`, `microstructure_microprice_lean`, `microstructure_midpoint_return_bps`, `microstructure_trade_return_bps`, `microstructure_aggressor_persistence`, `microstructure_arrival_intensity_imbalance`, `microstructure_arrival_rate_per_second`, `microstructure_resiliency`, `microstructure_aggressive_flow_score`, `microstructure_displayed_liquidity_score`, `microstructure_response_resiliency_score`, `microstructure_regime_reliability` | Review/register 32; current Strategy aliases must map explicitly to canonical outputs. |
| `large_trade_activity` | Watchlist / implemented | `avg_trade_size`, `median_trade_size`, `max_trade_size`, `large_trade_count`, `large_trade_volume`, `large_trade_notional` | Review/register 6 if exposed. |
| `nbbo_liquidity` | Core / implemented | `spread_bps`, `spread_bps_mean`, `quoted_bid_size_mean`, `quoted_ask_size_mean`, `quote_pressure`, `depth_imbalance_proxy`, `locked_crossed_quote_count`, `effective_spread_mean`, `slippage_proxy_bps`, `liquidity_score` | `spread_bps` and `liquidity_score` registered; review/register 8. |
| `volume_relative` | Watchlist / planned realtime | `rvol_1m`, `rvol_5m`, `relative_dollar_volume`, `volume_sma_20`, `volume_ema_20`, `dollar_volume_sma_20`, `volume_vs_avg_so_far`, `volume_vs_recent_3` | Review/register 8 only with implemented producer/version. |
| `volume_classic` | Watchlist / planned realtime | `obv`, `ad`, `adosc`, `cmf`, `mfi`, `pvt`, `nvi`, `pvi`, `eom`, `kvo`, `force_index` | Review/register 11 only with implemented producer/version. |
| `momentum_core` | Watchlist / implemented | `rsi_14`, `macd_line`, `macd_signal`, `macd_histogram`, `return_1_bar`, `price_vs_vwap_pct` | Review/register 6; current dotted MACD aliases need explicit mappings. |
| `momentum_extended` | Strategy / strategy-specific | `roc`, `rocp`, `rocr`, `mom`, `stoch`, `stochrsi`, `cci`, `cmo`, `williams_r`, `trix`, `ppo`, `apo`, `awesome_oscillator`, `kst`, `tsi`, `ultimate_oscillator` | Review/register 16 only when selected by a Strategy/product. |
| `trend_moving_averages` | Watchlist / implemented | `sma`, `ema_9`, `ema_20`, `ema_50`, `wma`, `dema`, `tema`, `hma`, `kama`, `zlema`, `alma`, `vwma`, `t3`, `ma_ribbon` | Review/register 14 if exposed. |
| `trend_directional` | Watchlist / planned realtime | `adx`, `plus_di`, `minus_di`, `plus_dm`, `minus_dm`, `supertrend`, `psar`, `ichimoku` | Review/register 8 only with implemented producer/version. |
| `volatility_core` | Watchlist / implemented | `true_range`, `atr_14`, `natr`, `bollinger_mid_20`, `bollinger_upper_20`, `bollinger_lower_20`, `bollinger_std_20`, `realized_volatility` | Review/register 8 if exposed. |
| `volatility_extended` | Strategy / strategy-specific | `keltner_channel`, `donchian_channel`, `historical_volatility`, `parkinson_volatility`, `garman_klass_volatility`, `yang_zhang_volatility`, `rvi`, `chop_index` | Review/register 8 only when selected. |
| `price_action` | Watchlist / planned realtime | `body_pct`, `upper_wick_pct`, `lower_wick_pct`, `close_location_value`, `inside_bar`, `outside_bar`, `body_break`, `range_expansion`, `range_compression`, `higher_high`, `lower_low` | Review/register 11 only with implemented producer/version. |
| `qmd_generic_structure` | Watchlist / implemented | `qmd_structure_direction`, `qmd_structure_score`, `qmd_structure_agreement`, `qmd_structure_strength`, `qmd_structure_confidence`, `qmd_structure_support_field`, `qmd_structure_resistance_field`, `qmd_structure_pressure_bias`, `qmd_structure_pressure_confidence`, `qmd_structure_up_probability`, `qmd_structure_support_price`, `qmd_structure_support_lower`, `qmd_structure_support_upper`, `qmd_structure_resistance_price`, `qmd_structure_resistance_lower`, `qmd_structure_resistance_upper`, `qmd_structure_active_levels`, `qmd_structure_timeframe_states`, `qmd_structure_developing_high`, `qmd_structure_developing_low`, `qmd_structure_developing_direction`, `qmd_structure_event_kind`, `qmd_structure_event_timeframe`, `qmd_structure_event_direction`, `qmd_structure_event_price`, `qmd_structure_event_pivot_at_ms`, `qmd_structure_session_high`, `qmd_structure_session_low`, `qmd_structure_opening_range_high`, `qmd_structure_opening_range_low`, `qmd_structure_trade_volume_poc`, `qmd_structure_nearest_round`, `qmd_structure_luld_upper`, `qmd_structure_luld_lower`, `qmd_structure_52_week_high`, `qmd_structure_52_week_low`, `qmd_structure_prior_month_high`, `qmd_structure_prior_month_low`, `qmd_structure_prior_month_close` | Review/register 39; current Strategy structure aliases require explicit mappings. |
| `shock_features` | Watchlist / planned realtime | `return_zscore`, `volume_zscore`, `dollar_volume_zscore`, `spread_zscore`, `trade_rate_zscore`, `quote_rate_zscore`, `volatility_expansion`, `gap_shock`, `liquidity_dry_up`, `price_volume_shock` | Review/register 10 only with implemented producer/version. |
| `cross_timeframe_confirmation` | Strategy / strategy-specific | `trend_alignment_1m_5m`, `trend_alignment_5m_1h`, `ema_stack_alignment`, `lower_tf_accel_higher_tf_trend`, `multi_tf_breakout_confirmed` | Review/register 5 only when selected. |
| `statistics` | Offline / offline-only | `rolling_mean`, `rolling_std`, `rolling_skew`, `rolling_kurtosis`, `zscore`, `correlation`, `beta`, `covariance`, `entropy`, `hurst`, `autocorrelation`, `linear_regression_forecast` | Keep dataset-local unless promoted to an application product; then register 12 with parameters/grain. |
| `cycles` | Offline / offline-only | `ht_trendline`, `ht_dcperiod`, `ht_dcphase`, `ht_phasor`, `ht_sine`, `ht_trendmode` | Keep dataset-local unless promoted; then register 6. |
| `candlestick_patterns` | Offline / offline-only | `cdl_all_talib_patterns`, `doji`, `hammer`, `engulfing`, `harami`, `morning_star`, `evening_star`, `shooting_star`, `three_white_soldiers`, `three_black_crows` | Keep dataset-local unless promoted; then register 10. |
| `performance` | Offline / offline-only | `log_return`, `cumulative_return`, `drawdown`, `sharpe`, `sortino`, `realized_pnl`, `unrealized_pnl`, `exposure` | These belong to research/trading-performance field groups, not QMD market fields; register with the owning product if promoted. |
| `reference_context` | Core / reference-only | `ibkr_conid`, `float_bucket`, `short_pressure_label`, `short_squeeze_likelihood`, `market_cap_bucket`, `sector`, `industry`, `news_flag`, `ssr_flag`, `halt_flag` | `sector` and `industry` directly match. Map others to Reference/News/QMD fields or register explicit outputs; do not let QMD become source authority. |

### Signal definitions

| Current kind | Target kind | IDs |
| --- | --- | --- |
| `market_observation` | `SignalDefinition` | `flow_structure_alignment`, `directional_flow_acceleration`, `price_volume_expansion`, `vwap_transition`, `liquidity_dislocation`, `liquidity_recovery`, `flow_price_divergence` |

```text
SignalEventSchema =
  schema_version, signal_version, engine_version,
  event_id, signal_id, signal_key, producer, domain, ticker,
  working_timeframe, clock, observed_at, effective_at,
  state, direction, score, rank_score, confidence,
  trigger_reason, resolution_reason,
  reference_price, invalidation_price, expires_at, evidence
```

| Registration object | Rule |
| --- | --- |
| Event schema | Register once |
| Scalar projection | Register FieldDefinition only for rule/table/Strategy consumption |
| Causal authority | SignalEvent |

## Configured composition inventory

### Rule sets

The current 41 rule sets are already the correct larger-part abstraction. They
should reference registered conditions and fields, not be recast as fields:

| Semantic group | Rule-set IDs |
| --- | --- |
| Entry opportunity | `initial-entry-opportunity-break-structure`, `initial-entry-opportunity-break-vwap`, `initial-entry-opportunity-bullish-choch`, `initial-entry-opportunity-price-volume-expansion`, `initial-entry-opportunity-vwap-transition`, `initial-entry-opportunity-company-news` |
| Entry confirmation | `initial-entry-confirmation-qmd-alignment`, `initial-entry-confirmation-vwap-confirmation`, `initial-entry-confirmation-macd-confirmation` |
| Entry blockers | `initial-entry-blockers-flow-price-divergence`, `initial-entry-blockers-liquidity-dislocation` |
| Add/exit | `add-confirmed-position-add-bullish-structure-add`, `exit-failed-entry-thesis-lose-entry-structure`, `exit-adverse-momentum-adverse-qmd-score`, `exit-adverse-momentum-qmd-confidence`, `exit-adverse-momentum-adverse-macd-line`, `exit-adverse-momentum-adverse-macd-histogram` |
| Price/market-cap classification | `watchlist-penny-stocks`, `watchlist-small-caps`, `watchlist-mid-caps`, `watchlist-large-caps` |
| Float classification | `watchlist-float-tiny`, `watchlist-float-extra_small`, `watchlist-float-small`, `watchlist-float-medium`, `watchlist-float-medium_plus`, `watchlist-float-large`, `watchlist-float-extra_large`, `watchlist-float-broad` |
| Market behavior | `watchlist-positive-gainer`, `watchlist-relative-volume-gainer`, `watchlist-price-or-volume-squeeze`, `watchlist-vwap-breakout` |
| News/SEC/fundamental | `watchlist-news-bullish`, `watchlist-news-bearish`, `watchlist-sec-bullish`, `watchlist-sec-bearish`, `watchlist-fundamental-bullish`, `watchlist-fundamental-bearish` |
| Corporate event | `watchlist-ipo-window`, `watchlist-split-window` |

### Watchlists

```text
WatchlistDefinition IDs =
  core-candidates
  top-penny-gainers, top-penny-volume-gainers
  top-small-cap-gainers, top-small-cap-volume-gainers
  top-mid-cap-gainers, top-mid-cap-volume-gainers
  top-large-cap-gainers, top-large-cap-volume-gainers
  price-or-volume-squeeze, vwap-breakout
  news-bullish-sentiment, news-bearish-sentiment
  sec-bullish-sentiment, sec-bearish-sentiment
  fundamental-bullish, fundamental-bearish
  past-upcoming-ipos, stock-splits
```

| Watchlist property | Reference |
| --- | --- |
| Membership | `rule_set_ids[]` |
| Presentation | `column_ids[]` |
| Order | `ranking_field_id` |
| Computation | `focused_derivation_ids[]` |
| Lifecycle | `timing`, `overrides` |

### Strategy input bindings

The current 21 bindings should be retained as adapters and validated against
canonical field or event-property IDs:

| Source IDs | Runtime bindings |
| --- | --- |
| `indicator.flow_structure.confidence`, `indicator.flow_structure.score` | `qmd_confidence`, `qmd_score` |
| `indicator.macd.histogram`, `indicator.macd.line`, `indicator.macd.signal` | `macd_histogram`, `macd_line`, `macd_signal` |
| `indicator.structure.bullish_choch`, `indicator.structure.swing_high`, `indicator.structure.swing_low` | `bullish_choch`, `swing_high`, `swing_low` |
| `indicator.vwap.slope`, `indicator.vwap.value` | `vwap_slope_bps_per_second`, `vwap` |
| `market.last_price`, `market.previous_close`, `market.previous_high` | `price`, `previous_close`, `previous_high` |
| `signal.company_news.score`, `signal.flow_price_divergence.score`, `signal.liquidity_dislocation.score`, `signal.price_volume_expansion.score`, `signal.sec_filing.score`, `signal.vwap_transition.score` | `news_score`, `flow_price_divergence_score`, `liquidity_dislocation_score`, `price_volume_expansion_score`, `sec_filing_score`, `vwap_transition_score` |
| `signal.news_labeled`, `signal.sec_labeled` | `news_labeled`, `sec_labeled` |

| Current state | Count | Target |
| --- | ---: | --- |
| Runtime-injected source IDs | 17 | FieldDefinition or SignalEvent property projection |

## Service and query-plan inventory

### Registered sources and products

| Kind | IDs | Disposition |
| --- | --- | --- |
| Market sources (6) | `qmd.massive_live`, `qmd.live_memory`, `qmd.recent_events`, `qmd.archive_events`, `qmd.daily_bars`, `reference.point_in_time` | Retain as SourceDefinitions; never flatten into fields. |
| QMD products (8) | `qmd.compact_events`, `qmd.intraday_bars`, `qmd.macro_bars`, `qmd.indicators`, `qmd.market_signals`, `qmd.scanner`, `qmd.chart`, `qmd.computation_targets` | Retain as ProductDefinitions. Product output schemas reference fields/events. |
| Workspace links (5) | `workspace.symbol_context`, `workspace.clock_context`, `workspace.news_selection`, `workspace.sec_selection`, `workspace.order_selection` | Retain as interaction contracts, not information fields. |
| Canvas containers (22) | `chart`, `charts_quotes`, `facts`, `microstructure`, `scanner`, `watchlist`, `strategy_activity`, `strategy`, `portfolio`, `positions`, `orders`, `fills`, `closed_trades`, `activity`, `performance_journal`, `news`, `ticker_news`, `news_detail`, `sec`, `ticker_sec`, `sec_detail`, `xbrl` | Retain as UI/container registry; container state references products/links/columns. |

### Configuration schemas and compatibility

```text
CompositionSchema IDs =
  trading_configuration, strategy_profile, watchlist,
  historical_watchlist_plan, run_plan,
  canvas_profile, canvas_layout,
  portfolio_policy, oms_policy, strategy_intent,
  execution_policy, protection_profile, account_binding
```

| Compatibility ID | From | To | Coverage gap |
| --- | --- | --- | --- |
| `qmd.stream.scanner_primitives` | `/stream/scanner-primitives` | `/stream/signals` | Five field replacements; Strategy runtime aliases |

### Query plans

| Semantic group | Plan IDs | Current authority/action |
| --- | --- | --- |
| QMD current Scanner | `qmd.scanner.snapshot.v1` | QMD Gateway service; retain. |
| Historical market and cache | `market.daily_session_bars.v1`, `market.historical_scanner_materialization.v1`, `market.historical_scanner_cache.v1` | Market SIP archive/QMD historical materialization; retain with source revision and completeness. |
| Market/reference lookup | `market.ticker_presentation.v1`, `market.tradable_universe.v1`, `market.schema_inventory.v1` | Backend bounded projections; retain. |
| Reference authority | `reference.schema_inventory.v1`, `reference.universe_snapshot.v1`, `reference.identity_for_symbol.v1`, `reference.scanner_asof.v1`, `reference.ticker_facts.v1`, `watchlist.external_feature_intervals.v1` | Reference Gateway/point-in-time backend projections; retain. |
| SEC fundamentals | `sec.fundamentals_asof.v1` | SEC fact projection; retain. |
| News | `news.company_asof.v1`, `news.scanner_company_asof.v1`, `news.detail_asof.v1`, `news.operations_intraday.v1`, `news.canvas_asof.v1` | News Gateway/current backend query modules; retain. |
| SEC | `sec.filing_asof.v1`, `sec.operations_intraday.v1`, `sec.canvas_asof.v1`, `sec.scanner_filing_asof.v1`, `sec.ticker_identity_batch.v1` | SEC Gateway/current backend query modules; retain. |
| Intelligence | `intelligence.published_consumer.v1`, `intelligence.news_asof.v1`, `intelligence.sec_asof.v1` | Published Text Intelligence boundary; retain but honor integration status. |
| Model context | `model.context_asof.v1` | Model Gateway/Market AI context contract; integration-pending fields stay unavailable. |

## Physical database inventory and registration disposition

Physical storage is inventoried to prevent hidden authorities. It is not a list
of fields to expose automatically. Row counts were inspected during the audit
but are intentionally omitted here because they change continuously.

### `q_live` (164 tables)

| Storage family | Exact tables | Registration disposition |
| --- | --- | --- |
| QMD events, bars, coverage, and structures | `events`, `live_market_events_v1`, `live_symbol_market_event_v1`, `intraday_bars_v1`, `intraday_family_bars_v2`, `live_market_indicators`, `qmd_compact_event_issue_v1`, `qmd_flatfile_coverage_v2`, `qmd_gap_fill_runs`, `qmd_gap_fill_symbol_universe_v1`, `qmd_live_event_coverage_v1`, `qmd_market_coverage_manifest_v1`, `qmd_structure_events_v2`, `qmd_structure_focus_registry_v1`, `qmd_structure_state_v2` | Sources/products/coverage. Register cross-boundary output fields and signal schemas; do not register issue/manifest columns as business fields unless exposed diagnostically. `live_market_indicators` is empty and not proof of an implemented product. |
| Canvas historical projections/caches | `canvas_historical_qmd_scanner_v1`, `canvas_historical_qmd_signal_event_v1`, `canvas_historical_qmd_snapshot_meta_v1`, `canvas_historical_scanner_v1`, `canvas_scanner_technical_v1`, `canvas_scanner_technical_v3` | Registered query-plan cache sources only; never canonical field authority. Retire v1 consumers after migration. |
| Reference dimensions and identity | `ref_asset_class_v1`, `ref_country_v1`, `ref_exchange_currency_v1`, `ref_exchange_v1`, `ref_ticker_type_v1`, `id_issuer_identifier_v1`, `id_issuer_relationship_v1`, `id_issuer_v1`, `id_listing_v1`, `id_mapping_issue_v1`, `id_security_identifier_v1`, `id_security_v1`, `id_source_mapping_v1`, `id_symbol_interval_v1`, `id_symbol_v1`, `id_sec_market_bridge_v1`, `id_sec_market_bridge_v3` | Reference Gateway authorities. Register semantic projections, not every key/evidence column. `id_sec_market_bridge_v1` is legacy; v3 is active. |
| Reference publications | `feature_scanner_static_v1`, `feature_tradable_universe_v1`, `market_cash_dividend_v1`, `market_fails_to_deliver_v1`, `market_ipo_v1`, `market_presentation_asset_v1`, `market_reference_alert_consumer_state_v1`, `market_reference_alert_v1`, `market_reference_publication_coverage_v1`, `market_reference_source_schedule_v1`, `market_reg_sho_threshold_v1`, `market_security_borrow_v1`, `market_security_classification_v1`, `market_security_country_v1`, `market_security_float_v1`, `market_security_market_snapshot_v1`, `market_short_interest_v1`, `market_short_volume_v1`, `market_stock_split_v1`, `market_ticker_event_correction_v1`, `market_ticker_event_entity_coverage_v1`, `market_ticker_event_entity_v1`, `market_ticker_event_v1`, `massive_flatfile_source_file_v1` | Active Reference Gateway source/projection/operations tables. Most business projections are registered; candidate gaps are listed below. Alert/manifest/file rows are operations products, not ordinary fields. |
| Planned security fact products | `feature_sec_event_market_bridge_v1`, `issuer_fundamental_metric_fact_v1`, `security_liquidity_profile_fact_v1`, `security_news_catalyst_fact_v1`, `security_routing_fact_v1`, `security_sec_filing_event_fact_v1`, `security_sec_text_signal_fact_v1`, `security_share_supply_fact_v1`, `security_tradability_fact_v1`, `security_valuation_fact_v1`, `source_artifact_v1` | Several are empty/planned. Register product/output fields only after writers, clocks, provenance, and consumers are implemented. Nonempty routing/tradability facts remain projections, not new authorities. |
| Benzinga/News canonical and render | `benzinga_news_block_v2`, `benzinga_news_coverage_manifest_v1`, `benzinga_news_event_v2`, `benzinga_news_file_ingest_manifest_v1`, `benzinga_news_normalized_v1`, `benzinga_news_render_authority_v2`, `benzinga_news_rendered_v2`, `benzinga_news_source_v2`, `benzinga_news_ticker_v1`, `benzinga_news_ticker_v2`, `canonical_text_live_status_v1` | News products and manifests. Current application fields use bounded News query plans; blocks/manifests are not fields. Ticker v1 is legacy where v2 is authoritative. |
| News semantics/reaction research | `news_language_assessment_v2`, `news_language_features_v1`, `news_language_review_v1`, `news_phrase_dictionary_v1`, `news_phrase_reaction_stats_v2`, `news_phrase_reaction_stats_v3`, `news_phrase_reaction_stats_v5`, `news_reaction_baseline_v2`, `news_reaction_build_status_v1`, `news_reaction_calendar_v1`, `news_reaction_event_effects_v2`, `news_reaction_finalization_state_v1`, `news_reaction_labels_v2`, `news_reaction_labels_v3`, `news_reaction_model_status_v2`, `news_reaction_predictions_v2`, `news_reaction_quality_overlay_v1`, `news_reaction_scale_v2`, `news_semantic_event_dictionary_v2`, `news_semantic_event_features_v2`, `news_synthesis_build_status_v1`, `news_synthesis_v1`, `news_ticker_identity_alias_v2`, `news_ticker_relevance_v2`, `news_url_policy_v1` | Dataset/model/published semantic products. Only promoted, causal consumer outputs should become fields; build/review/reaction targets are not Scanner fields. |
| Scoped Text Intelligence | `scoped_content_relations_v2`, `scoped_content_relations_v3`, `scoped_text_labels_v4`, `scoped_text_labels_v4_build_status`, `scoped_text_labels_v5`, `scoped_text_labels_v5_build_status`, `scoped_text_live_status_v2` | V5/current published consumer path; earlier versions/build status are lineage/operations, not new fields. |
| SEC filings/documents/rendering | `sec_coverage_manifest_v1`, `sec_coverage_manifest_v3`, `sec_disclosure_taxonomy_candidate_v3`, `sec_disclosure_taxonomy_v3`, `sec_filing_archive_accession_current_v3`, `sec_filing_archive_accession_v3`, `sec_filing_archive_ingest_manifest_v3`, `sec_filing_document_skip_v1`, `sec_filing_document_skip_v3`, `sec_filing_document_v2`, `sec_filing_document_v3`, `sec_filing_entity_archive_manifest_v3`, `sec_filing_entity_current_v3`, `sec_filing_entity_v3`, `sec_filing_live_ingest_manifest_v3`, `sec_filing_pac_event_v3`, `sec_filing_text_file_ingest_manifest_v1`, `sec_filing_text_file_ingest_manifest_v3`, `sec_filing_text_render_candidate_v3`, `sec_filing_text_render_repair_manifest_v3`, `sec_filing_text_rendered_rebuild_bundle_manifest_v3`, `sec_filing_text_rendered_rebuild_manifest_v3`, `sec_filing_text_rendered_v3`, `sec_filing_text_v2`, `sec_filing_text_v3`, `sec_filing_v2`, `sec_filing_v3` | V3 product/query-plan sources are active. Current views, manifests, skips, candidates, and repair state remain operational. V2 is compatibility/lineage, not a new authority. |
| SEC XBRL | `sec_xbrl_company_fact_v1`, `sec_xbrl_company_fact_v3`, `sec_xbrl_concept_v1`, `sec_xbrl_concept_v3`, `sec_xbrl_frame_observation_v1`, `sec_xbrl_frame_observation_v3`, `sec_xbrl_frame_v1`, `sec_xbrl_frame_v3` | V3 sources back registered fundamental/XBRL projections. V1 remains lineage/compatibility. Physical concepts do not auto-register as application fields. |
| Operations | `ibkr_gateway_supervisor_event_v1`, `source_run_v1`, `sync_validation_v1`, `sync_watermark_v1` | Operational products. Register diagnostics only for an explicit service-health consumer. |
| Scratch/backups | `_codex_news_language_features`, `_codex_news_phrase_dictionary`, `_codex_news_phrase_reaction_stats`, `_codex_news_reaction_calendar`, `_codex_news_reaction_labels`, `_codex_news_reaction_status`, `id_sec_market_bridge_v3__backup_step_06_bridge_features_20260729_124446`, `sec_filing_text_rendered_pre_v8_20260716151718_v3`, `sec_filing_text_v3_inserted_at_engine_backup` | Never register as authorities or fields. Preserve only under explicit recovery/cleanup policy. |

### `market_sip_compact` (68 tables)

| Storage family | Exact tables | Registration disposition |
| --- | --- | --- |
| Canonical compact events | `events_2019`, `events_2020`, `events_2021`, `events_2022`, `events_2023`, `events_2024`, `events_2025`, `events_2026`, `events_build_manifest`, `events_ordinal_continuity`, `events_source_day_stats`, `events_ticker_day_index`, `event_condition_token_reference` | Registered source/product/coverage contracts; event fields remain QMD schema, not individual UI columns by default. |
| Bars and Scanner sidecars | `daily_session_bars_by_symbol_time_v1`, `daily_session_bars_manifest_v1`, `intraday_aux_build_status`, `intraday_base_bars_build_status`, `intraday_base_bars_by_time_ticker`, `intraday_condition_bars_by_time_ticker`, `intraday_condition_events_by_time_ticker`, `macro_bars_by_time_symbol`, `packed_scanner_sidecar_bars` | Registered products/query-plan sources. Build status and sidecars are not semantic authorities. |
| Market code references | `ref_cta_security_status`, `ref_financial_status`, `ref_halt_reason`, `ref_held_trade_indicators`, `ref_luld_indicators`, `ref_misc_indicators`, `ref_nbbo_indicators`, `ref_quote_conditions`, `ref_stock_exchanges`, `ref_stock_tapes`, `ref_trade_conditions`, `ref_trade_corrections_nyse`, `ref_utp_security_status` | Service-local decoding/reference inputs; register only if exposed as a bounded application reference product. |
| News embeddings/reaction datasets | `news_openai_embedding_batches_v1`, `news_openai_embedding_items_v1`, `news_openai_embeddings_v1`, `news_reaction_certified_target_status_v1`, `news_reaction_certified_targets_v1`, `news_reaction_embedding_dataset_v1`, `news_reaction_embedding_dataset_v1_manifest`, `news_reaction_numeric_tfidf_dataset_v6`, `news_reaction_numeric_tfidf_dataset_v6_manifest`, `news_reaction_openai_stock_state_dataset_v8`, `news_reaction_openai_stock_state_dataset_v8_manifest`, `news_reaction_percentage_dataset_v4`, `news_reaction_percentage_dataset_v4_manifest`, `news_reaction_sparse_tfidf_dataset_v5`, `news_reaction_sparse_tfidf_dataset_v5_manifest`, `news_reaction_stock_state_dataset_v7`, `news_reaction_stock_state_dataset_v7_manifest`, `news_text_embeddings`, `news_text_tokens` | Offline datasets/artifacts; do not register as application fields until a promoted model/product contract consumes them. |
| SEC/text model context | `sec_embedding_policy_v3`, `sec_filing_context`, `sec_filing_context_v3`, `sec_filing_text_context`, `sec_filing_text_embeddings`, `sec_filing_text_tokens`, `sec_xbrl_context`, `sec_xbrl_context_sync_manifest_v3`, `sec_xbrl_context_v3`, `text_embedding_coverage_v1`, `training_category_reference` | Offline/model-context and coverage products. Register promoted artifact contracts, not every stored column. |
| BarGPT manifests | `bar_gpt_1s_build_manifest_v1`, `bar_gpt_1s_build_manifest_v1_cohort_2tb`, `bar_gpt_1s_build_manifest_v1_identity_aliases` | Training/build manifests; never ordinary application fields. |

### Reference/SEC source and staging databases

| Database | Exact tables | Registration disposition |
| --- | --- | --- |
| `q_reference_tmp` (30) | `id_mapping_issue_v1`, `id_symbol_interval_v1`, `issuer_fundamental_metric_fact_v1`, `market_cash_dividend_v1`, `market_fails_to_deliver_v1`, `market_ipo_v1`, `market_presentation_asset_v1`, `market_reference_alert_consumer_state_v1`, `market_reference_alert_v1`, `market_reference_publication_coverage_v1`, `market_reg_sho_threshold_v1`, `market_security_borrow_v1`, `market_security_country_v1`, `market_security_float_v1`, `market_security_market_snapshot_v1`, `market_short_interest_v1`, `market_short_volume_v1`, `market_stock_split_v1`, `market_ticker_event_entity_coverage_v1`, `market_ticker_event_entity_v1`, `market_ticker_event_v1`, `massive_flatfile_source_file_v1`, `security_liquidity_profile_fact_v1`, `security_news_catalyst_fact_v1`, `security_routing_fact_v1`, `security_sec_filing_event_fact_v1`, `security_sec_text_signal_fact_v1`, `security_share_supply_fact_v1`, `security_tradability_fact_v1`, `security_valuation_fact_v1` | Staging only. Never register as authority; publication into `q_live` must establish the product/coverage boundary. |
| `sec_core` (16) | `sec_bulk_mirror_company_ticker_v1`, `sec_bulk_mirror_company_ticker_v3`, `sec_bulk_mirror_company_v1`, `sec_bulk_mirror_company_v3`, `sec_bulk_mirror_filing_acceptance_v1`, `sec_bulk_mirror_filing_v1`, `sec_bulk_mirror_filing_v3`, `sec_bulk_mirror_member_manifest_v1`, `sec_bulk_mirror_raw_source_file_v1`, `sec_bulk_mirror_raw_source_file_v3`, `sec_bulk_mirror_snapshot_manifest_v3`, `sec_bulk_mirror_submission_file_ref_v1`, `sec_bulk_mirror_submission_file_ref_v3`, `sec_bulk_mirror_xbrl_fact_v1`, `sec_bulk_mirror_xbrl_fact_v3`, `sec_submissions_filing_overlay_v3` | SEC acquisition/mirror authority feeding published SEC products. Register source/product contracts, not mirror columns as UI fields. V3 is current where present. |
| `trading_dashboard_dev` (33) | `market_asset_class_v1`, `market_canonical_reference_issue_v1`, `market_cash_dividend_v1`, `market_condition_definition_v1`, `market_country_v1`, `market_exchange_asset_class_coverage_v1`, `market_exchange_currency_v1`, `market_exchange_product_coverage_v1`, `market_exchange_v1`, `market_financial_statement_snapshot_v1`, `market_identity_merge_ambiguity_v1`, `market_ipo_v1`, `market_issuer_address_v1`, `market_issuer_v1`, `market_listing_v1`, `market_presentation_asset_v1`, `market_security_classification_v1`, `market_security_float_v1`, `market_security_identifier_v1`, `market_security_market_snapshot_v1`, `market_security_v1`, `market_short_interest_v1`, `market_short_volume_v1`, `market_source_identity_mapping_v1`, `market_stock_split_v1`, `market_symbol_v1`, `market_ticker_type_v1`, `massive_flatfile_source_file_v1`, `sec_filing_v1`, `sec_xbrl_company_fact_v1`, `sec_xbrl_concept_v1`, `sec_xbrl_frame_observation_v1`, `sec_xbrl_frame_v1` | Physical upstream/reference source currently used by publication code. The application authority is the Reference/SEC Gateway projection and registered query plans, not direct UI reads. |
| `trading_dashboard_dev_backup_20260517` (20) | `market_asset_class_v1`, `market_canonical_reference_issue_v1`, `market_condition_definition_v1`, `market_country_v1`, `market_exchange_asset_class_coverage_v1`, `market_exchange_currency_v1`, `market_exchange_product_coverage_v1`, `market_exchange_v1`, `market_identity_merge_ambiguity_v1`, `market_identity_merge_review_v1`, `market_issuer_v1`, `market_listing_v1`, `market_presentation_asset_v1`, `market_security_classification_v1`, `market_security_identifier_v1`, `market_security_market_snapshot_v1`, `market_security_v1`, `market_source_identity_mapping_v1`, `market_symbol_v1`, `market_ticker_type_v1` | Backup only; never register. |
| `trading_dashboard_semantic_graph_review` (2) | `semantic_graph_review_state`, `semantic_graph_section_review_state` | Review tooling only; never register as market/application fields. |

## Reference Gateway business-column review

The Reference Gateway owns 36 table contracts. Existing application fields
cover the common Scanner/Watchlist projections, but the following physical
business values are not represented by equivalent canonical FieldDefinitions
or need more precise producer mappings. They are candidates, not automatic
registrations:

| Semantic group | Candidate physical values | Decision |
| --- | --- | --- |
| Issuer identity/profile | legal/branding name, entity type, domicile, incorporation state, SIC, SIC description, industry group, websites | Register only those needed by Facts, screening, Strategy, or exports; keep canonical issuer grain and validity/publication clocks. |
| Security/listing | product/instrument type, options availability, board, segment, listing status, primary-listing flag, list/delist dates | Register the subset consumed by tradability, facts, rules, or routing. Avoid duplicates of existing `listing.*` fields. |
| Symbol evidence | display/root/suffix, provider identifier kind/value, mapping status/confidence, source event ID | Keep mapping/evidence fields diagnostic unless a consumer needs them. Do not expose provider keys as canonical identity. |
| Market snapshot | round lot, share-class and weighted shares outstanding, snapshot evidence reference | Register round lot for routing/size validation; reconcile outstanding-share variants with existing semantic fields before adding IDs. |
| Float | effective date, free-float percent, shares outstanding, source tag/evidence | Register `reference.float_pct` if screening needs it; reuse existing source/quality fields and do not duplicate aliases. |
| Short interest | settlement/publication dates, venue, average daily volume, event/evidence keys | Register dates/venue only for point-in-time explanation; average volume is an input and should reference a canonical volume field/derivation. |
| Short volume | trade/publication dates, venue, total/exempt/non-exempt volume | Register when a table/rule needs the values; define distinct units and publication clock. |
| Dividends | declaration, record, pay dates; type/frequency | Register for event calendars/rules; map current semantic aliases (`amount`, `currency`, `ex_date`) explicitly to physical columns. |
| IPO | announced/listing/offer-window dates, price range/final price, share range, offer size, exchange/security descriptors | Register event properties needed by Watchlists or research; retain one IPO event definition rather than independent ad hoc fields. |
| Fails to deliver | settlement date, CUSIP, quantity, previous close | Quantity/value are already projected; register settlement/evidence fields only when shown or filtered. |
| Reg SHO | threshold date, exchange, status | Register status/date for explanation if required; existing Boolean projection remains the common filter. |
| Borrow | broker, conid, shortable shares, lender count, indicative rate, fee rate | Reconcile physical aliases with existing borrow fields; broker-specific values need live-only provenance. |
| Country assertion | assertion date, per-dimension country codes, confidence, evidence | Current five country fields cover common values; register confidence/evidence only for explanation/audit consumers. |
| Ticker events | entity name/current ticker/provider timestamps, mapping status/reason, event coverage counts and dates | Keep event/coverage schemas; project only consumer-required properties. |
| Publication operations | coverage kind/object/date bounds, row counts/failures/details; schedule frequency/status/details | Existing high-level coverage/schedule fields are sufficient for general UI. Additional values belong in Service Health unless explicitly requested. |

## Migration register

| Priority | Required change | Acceptance condition |
| --- | --- | --- |
| P0 | Split the resolved catalog into explicit `fields`, `processing_steps`, `derivations`, `signals`, `products`, and `columns`. | Every entry has exactly one registry kind; no processing/event/product row appears in the FieldDefinition collection. |
| P0 | Establish explicit alias/version mappings for current dotted IDs and runtime names. | The five replaced static IDs and all 21 Strategy bindings resolve deterministically to one canonical semantic ID; no frontend name inference. |
| P0 | Make QMD the authority for QMD processing, derivation, and signal definitions while the application registry owns cross-service field/presentation contracts. | Backend imports typed QMD catalogs; Canvas, Market Discovery, Watchlists, Strategy Studio, Replay, Backtest, and Debug consume the same IDs/versions. |
| P1 | Add `DerivationDefinition` records for current backend and QMD derived outputs. | Each exposed derived FieldDefinition names inputs, algorithm/version, parameters, scope, warm-up, clocks, and null/coverage policy. |
| P1 | Register QMD output fields deliberately. | Every output crossing QMD has a canonical field/event-property ID; private/offline outputs remain private and are not bulk-added. |
| P1 | Replace `DiscoveryCapability` and `canonicalCapabilityType` heuristics with typed API contracts. | Frontend renders labels/groups/status from registry data and does not determine semantic kind from ID/name. |
| P1 | Implement the shared Data & Analytics picker and typed card shell. | Market Discovery, table Columns, charts, rule authoring, and Strategy Studio reuse one registry-driven picker with context-specific allowed actions; users never see a generic Capability item. |
| P1 | Normalize ColumnDefinitions. | Scanner and Watchlist headings/renderers come only from the shared column registry; every column has typed Field/Signal bindings, and sortable/filterable composites designate one valid scalar or signal predicate binding. |
| P2 | Review Reference Gateway candidate values against concrete consumers. | Each accepted candidate has a unique semantic ID and query-plan mapping; rejected candidates remain physical/internal without UI exposure. |
| P2 | Mark legacy/staging/backup/scratch storage explicitly. | Schema inventory and operational UI cannot select those tables as field authority. |

## Invariants for implementation

1. One semantic fact has one canonical field ID and any aliases are explicit,
   versioned, and directional.
2. A derivation consumes fields and produces fields; it is never itself inserted
   into the field registry.
3. An indicator or oscillator is a derivation subtype; its output values are
   fields.
4. A signal is an event lifecycle. Scalar “latest signal” values are explicitly
   registered projections and never replace event history.
5. A ColumnDefinition contains presentation policy only. It references a
   non-empty set of existing FieldDefinitions and/or SignalDefinitions; it
   never redefines their semantics. A sortable/filterable composite explicitly
   identifies the binding that owns that operation.
6. A physical table/column is not automatically an application authority.
7. Staging, backup, scratch, cache, build-status, and training tables never
   become field authority by discovery.
8. Every cross-service observation preserves entity identity, `event_at`,
   `available_at`, provenance, source/schema revision, freshness, coverage, and
   explicit null reason.
9. Live, Paper, Replay, Backtest, and Debug resolve the same definitions; mode
   changes the source/clock plan, not semantic meaning.
10. Larger configured parts reference existing smaller parts by stable ID and
    version. They never recreate those parts in UI-local constants.
11. The shared picker is a presentation projection only. Selecting an item
    creates a reference to its canonical definition; it never copies or mutates
    that definition.

## Navigation

[Previous: Release and recovery](16-release-rollback-and-recovery.md) ·
[Architecture home](README.md)
