import type { AbstractionKind } from "../../app/components/AbstractionCard";
import type { AtomicField, DataFieldDefinition } from "../../pages/DataConfigurationPages";
import type { TradingActionsConfiguration } from "../../pages/TradingActionsPage";
export type StrategyAuthoringStage = "identity" | "overview" | "entry" | "position" | "reentry" | "exit" | "handoff";

export type RuntimeMode = "replay" | "backtest" | "backtest_debug" | "paper" | "live";
export type ActionAuthority = "disabled" | "manual" | "confirm" | "automatic" | "inherit";
export type Primitive = boolean | number | string;
export type ParameterMap = Record<string, unknown>;
export type StrategyPhaseMode = "automatic" | "manual";

export function newYorkSessionDate(now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "America/New_York",
    year: "numeric",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export type CapabilityParameter = {
  display?: string;
  help: string;
  key: string;
  label: string;
  maximum?: number;
  minimum?: number;
  options?: string[];
  step?: number;
  type: "boolean" | "choice" | "number";
  unit?: string;
};

export type CapabilityDefinition = {
  capability_id: string;
  category: string;
  defaults: Record<string, Primitive>;
  name: string;
  order_entry_action: boolean;
  parameters: CapabilityParameter[];
  revision: number;
  summary: string;
};

export type CapabilityBinding = {
  capability_id: string;
  enabled: boolean;
  revision: number;
  settings: Record<string, Primitive>;
};

export type StrategyProfile = {
  action_policy_ids: string[];
  capabilities: CapabilityBinding[];
  definition_id: string;
  definition_revision: number;
  description: string;
  editable: boolean;
  enabled: boolean;
  name: string;
  origin: "system" | "user";
  protected: boolean;
  rule_set_ids: string[];
  lifecycle: StrategyLifecycle;
  parameters: ParameterMap;
  profile_id: string;
  revision: number;
  publication_status: "draft" | "published" | "template";
  derived_from_profile_id: string;
};

export type StrategyInput = {
  category: string;
  label: string;
  parameter: string;
  provider: string;
  runtime_field: string;
  source_id: string;
  summary: string;
  timeframes: string[];
  value_type: string;
  filter_operators?: string[];
  unit?: string;
};

export type RuleCondition = {
  comparator: string;
  condition_id: string;
  enabled: boolean;
  left_source_id: string;
  left_timeframe: string;
  left_interval?: import("../../app/components/IntervalSelect").IntervalValue;
  left_aggregation?: string;
  right_source_id: string;
  right_timeframe: string;
  right_interval?: import("../../app/components/IntervalSelect").IntervalValue;
  right_aggregation?: string;
  value: Primitive | null;
};

export type RuleGroup = {
  conditions: RuleCondition[];
  enabled: boolean;
  group_id: string;
  label: string;
  operator: "all" | "any" | "score";
  required_score: number;
};

export type RuleSetDefinition = {
  atomic?: boolean;
  conditions: RuleCondition[];
  description: string;
  enabled: boolean;
  editable?: boolean;
  name: string;
  operator: "all" | "any" | "score";
  required_score: number;
  rule_set_id: string;
  origin?: string;
  protected?: boolean;
  publication_status?: string;
  revision?: number;
  scope?: "shared" | "strategy" | "watchlist";
};

export type RuleExpression =
  | { kind: "rule_set"; rule_set_id: string }
  | { children: RuleExpression[]; kind: "operator"; operator: "and" | "or" };

export type RuleStage = {
  expression?: RuleExpression;
  groups?: RuleGroup[];
  operator?: "all" | "any";
};

export type EntryRules = {
  blockers: RuleStage;
  confirmation: RuleStage;
  opportunity: RuleStage;
};

export type EntryAuthoringPage = keyof EntryRules | "mode" | "capital" | "priority" | "execution" | "partial_fill" | "protection" | "initial_stop";

export const ENTRY_AUTHORING_PAGES: Array<{ description: string; id: EntryAuthoringPage; label: string; title: string }> = [
  { description: "Choose whether Strategy evaluates and emits initial-entry intent for this profile.", id: "mode", label: "Mode", title: "Should Strategy automate initial entry?" },
  { description: "Define the evidence paths that identify a candidate setup before confirmation is considered.", id: "opportunity", label: "Opportunity", title: "What identifies a possible entry?" },
  { description: "Define the independent evidence paths that must validate an identified opportunity.", id: "confirmation", label: "Confirmation", title: "What proves the entry is actionable?" },
  { description: "Define the conditions that veto entry even when opportunity and confirmation have passed.", id: "blockers", label: "Blockers", title: "What must prevent the entry?" },
  { description: "Express the desired exposure before Portfolio applies account capacity, mandates, and risk limits.", id: "capital", label: "Capital", title: "How much exposure should Strategy request?" },
  { description: "Permit or prohibit Portfolio from considering a stronger opportunity as a replacement candidate.", id: "priority", label: "Replacement", title: "May Portfolio propose a replacement?" },
  { description: "Choose the broker-neutral execution behavior OMS applies after Portfolio approves quantity.", id: "execution", label: "Execution", title: "How should OMS seek the entry fill?" },
  { description: "Choose how OMS handles an incomplete fill without exceeding Portfolio's approved quantity.", id: "partial_fill", label: "Partial fill", title: "What should happen after a partial fill?" },
  { description: "Select the independently versioned protection contract attached to confirmed entry fills.", id: "protection", label: "Protection", title: "Which protection follows the entry fill?" },
  { description: "Define the price that invalidates a new position. Strategy compares a causal market-structure boundary with a volatility-based boundary, applies the selected method, and caps the final distance from the current price. Portfolio still controls position size and OMS maintains the approved broker-held protection.", id: "initial_stop", label: "Initial stop", title: "How is the initial stop price calculated?" },
];

export type ManageAuthoringPage =
  | "mode" | "add_actions" | "add_evidence" | "add_capital" | "add_replacement" | "add_execution" | "add_partial_fill" | "add_protection"
  | "trailing" | "action_policies";

export const MANAGE_AUTHORING_PAGES: Array<{ description: string; id: ManageAuthoringPage; label: string; title: string }> = [
  { description: "Choose whether Strategy manages an open position through registered add routes, trailing behavior, and Action Policies.", id: "mode", label: "Mode", title: "Should Strategy automate position management?" },
  { description: "Create, name, enable, and limit the add actions that may increase an already-open position.", id: "add_actions", label: "Add actions", title: "Which position-add actions are available?" },
  { description: "Combine predefined rule sets to decide when the selected add action may request more exposure.", id: "add_evidence", label: "Add evidence", title: "What permits the selected add action?" },
  { description: "Express the selected add action's desired exposure before Portfolio applies current account and risk limits.", id: "add_capital", label: "Add capital", title: "How much exposure should the add request?" },
  { description: "Permit or prohibit Portfolio from considering a stronger add opportunity as a replacement candidate.", id: "add_replacement", label: "Add replacement", title: "May the add request propose a replacement?" },
  { description: "Choose the broker-neutral execution behavior OMS uses after Portfolio approves the selected add quantity.", id: "add_execution", label: "Add execution", title: "How should OMS seek the add fill?" },
  { description: "Choose how OMS handles the unfilled remainder of the selected add without exceeding approved quantity.", id: "add_partial_fill", label: "Add partial fill", title: "What should happen after a partial add fill?" },
  { description: "Select the independently versioned protection contract attached to confirmed fills from the selected add.", id: "add_protection", label: "Add protection", title: "Which protection follows the add fill?" },
  { description: "Configure definition-specific trailing behavior that may activate while the position remains open.", id: "trailing", label: "Trailing", title: "How may protection trail the open position?" },
  { description: "Reference reusable Action Policies without copying their Trading Action or Rule Set definitions.", id: "action_policies", label: "Action policies", title: "Which reusable Action Policies may this Strategy use?" },
];

export type ReentryAuthoringPage =
  | "mode" | "reentry_policy" | "reentry_opportunity" | "reentry_confirmation" | "reentry_blockers"
  | "reentry_capital" | "reentry_replacement" | "reentry_execution" | "reentry_partial_fill" | "reentry_protection";

export const REENTRY_AUTHORING_PAGES: Array<{ description: string; id: ReentryAuthoringPage; label: string; title: string }> = [
  { description: "Choose whether Strategy may evaluate and emit another flat-to-open request after a full exit.", id: "mode", label: "Mode", title: "Should Strategy automate reentry?" },
  { description: "Control whether a flat campaign may enter again and bound its evidence freshness, delay, and attempts.", id: "reentry_policy", label: "Reentry policy", title: "May the campaign enter again after a full exit?" },
  { description: "Combine predefined rule sets that identify a possible reentry after the campaign becomes flat.", id: "reentry_opportunity", label: "Reentry opportunity", title: "What identifies a possible reentry?" },
  { description: "Combine predefined rule sets that must validate the reentry opportunity before a request is emitted.", id: "reentry_confirmation", label: "Reentry confirmation", title: "What proves the reentry is actionable?" },
  { description: "Combine predefined rule sets that veto reentry even when opportunity and confirmation pass.", id: "reentry_blockers", label: "Reentry blockers", title: "What must prevent the reentry?" },
  { description: "Express the desired reentry exposure before Portfolio recalculates current account capacity and risk.", id: "reentry_capital", label: "Reentry capital", title: "How much exposure should reentry request?" },
  { description: "Permit or prohibit Portfolio from considering a stronger reentry as a replacement candidate.", id: "reentry_replacement", label: "Reentry replacement", title: "May reentry propose a replacement?" },
  { description: "Choose the broker-neutral execution behavior OMS uses after Portfolio approves the reentry quantity.", id: "reentry_execution", label: "Reentry execution", title: "How should OMS seek the reentry fill?" },
  { description: "Choose how OMS handles only the broker-confirmed unfilled remainder of the reentry request.", id: "reentry_partial_fill", label: "Reentry partial fill", title: "What should happen after a partial reentry fill?" },
  { description: "Select the independently versioned protection contract attached to confirmed reentry fills.", id: "reentry_protection", label: "Reentry protection", title: "Which protection follows the reentry fill?" },
];

export type ExitAuthoringPage = "mode" | "targets" | "profit_pocket" | "routes" | "evidence" | "timing" | "action" | "execution" | "partial_fill" | "protection";

export const EXIT_AUTHORING_PAGES: Array<{ description: string; id: ExitAuthoringPage; label: string; title: string }> = [
  { description: "Choose whether Strategy evaluates strategic reductions and exits for this profile. OMS protection and account safety remain active.", id: "mode", label: "Mode", title: "Should Strategy automate strategic exits?" },
  { description: "Configure the definition-specific profit target derived from the authoritative volatility band.", id: "targets", label: "LULD target", title: "How may the volatility band define a target?" },
  { description: "Configure definition-specific conditions and quantity for an intentional profit reduction.", id: "profit_pocket", label: "Profit pocket", title: "When may the strategy preserve a profit pocket?" },
  { description: "Create, name, enable, and describe the strategic routes that may reduce or close an open position.", id: "routes", label: "Exit routes", title: "Which strategic exit routes are available?" },
  { description: "Combine predefined rule sets to decide when the selected strategic exit route passes.", id: "evidence", label: "Exit evidence", title: "What permits the selected exit route?" },
  { description: "Set when the selected route becomes active and when it stops being eligible while the position is open.", id: "timing", label: "Validity window", title: "When may the selected exit route act?" },
  { description: "Choose whether the selected route requests a full close or a partial reduction of the reconciled position.", id: "action", label: "Position action", title: "How much of the position should be released?" },
  { description: "Choose the broker-neutral execution behavior OMS applies to the approved strategic exit quantity.", id: "execution", label: "Exit execution", title: "How should OMS seek the exit fill?" },
  { description: "Choose how OMS handles only the broker-confirmed unfilled remainder of the strategic exit.", id: "partial_fill", label: "Exit partial fill", title: "What should happen after a partial exit fill?" },
  { description: "Select the protection contract retained for any reconciled shares that remain after the exit fill.", id: "protection", label: "Remaining protection", title: "How should remaining shares stay protected?" },
];

export type CapitalRequestConfig = {
  allow_replacement: boolean;
  mode: "fixed_quantity" | "mandate_fraction" | "risk_fraction" | "all_available";
  value: number;
};

export type OrderIntentConfig = {
  deadline_ms: number;
  execution_policy: string;
  partial_fill_policy: "complete_remainder" | "accept_partial" | "cancel_remainder";
  protection_profile: string;
};

export type AddStep = {
  action_id: string;
  capital_request: CapitalRequestConfig;
  enabled: boolean;
  maximum_uses: number;
  name: string;
  order_intent: OrderIntentConfig;
  rules: RuleStage;
  step_id: string;
};

export type ExitRuleSet = {
  action: "close" | "reduce";
  action_id: string;
  enabled: boolean;
  name: string;
  position_fraction: number;
  order_intent: OrderIntentConfig;
  rules: RuleStage;
  rule_set_id: string;
  summary: string;
  timing: { active_after_ms: number; expires_after_ms: number };
};

export type StrategyLifecycle = {
  phase_modes: {
    initial_entry: StrategyPhaseMode;
    manage: StrategyPhaseMode;
    reentry: StrategyPhaseMode;
    exit: StrategyPhaseMode;
  };
  trading_behavior: {
    eligible_sessions: string[];
    side: "long" | "short";
  };
  initial_entry: EntryRules & {
    action_id: string;
    add_steps: AddStep[];
    capital_request: CapitalRequestConfig;
    order_intent: OrderIntentConfig;
  };
  reentry: {
    action_id: string;
    capital_request: CapitalRequestConfig;
    cooldown_ms: number;
    enabled: boolean;
    maximum_attempts: number;
    order_intent: OrderIntentConfig;
    require_new_confirmation: boolean;
    rules: EntryRules;
  };
  exit: { rule_sets: ExitRuleSet[] };
};

export type StrategyDefinition = {
  automatic: boolean;
  direction: string;
  executor_installed?: boolean;
  executor_key?: string;
  executor_schema_version?: number | null;
  input_source_ids: string[];
  name: string;
  parameter_defaults?: ParameterMap;
  revision: number;
  strategy_id: string;
  supported_sides: Array<"long" | "short">;
};

export type StrategySection = {
  capability_catalog: CapabilityDefinition[];
  default_profile_id: string;
  definitions: StrategyDefinition[];
  input_catalog: StrategyInput[];
  profile_templates: StrategyProfile[];
  profiles: StrategyProfile[];
};

export type RuntimeAssignment = {
  account_key: string;
  assignment_id: string;
  conid: number;
  parameters?: ParameterMap;
  permissions?: Record<string, boolean>;
  status?: string;
  ticker: string;
};

export type StrategyRunPlan = {
  activation: {
    event_policy: "new_occurrences" | "latest_session_occurrence";
    watchlist_policy: "any_selected" | "all_selected" | "not_required";
  };
  action_authority: {
    add: ActionAuthority;
    default: Exclude<ActionAuthority, "disabled" | "inherit">;
    emergency_exit: "automatic";
    initial_entry: ActionAuthority;
    protective_exit: "automatic";
    reentry: ActionAuthority;
    strategic_exit: ActionAuthority;
  };
  allowed_environments: RuntimeMode[];
  book_id: string;
  canvas_profile_id: string;
  campaign_lifecycle: {
    exit_authority: string;
    initial_entry_authority: string;
    maximum_initial_watch_ms: number;
    maximum_reentries: number;
    protective_exit_authority: "automatic";
    reentry_authority: string;
    reentry_cooldown_ms: number;
    retain_ticker_while_paused: boolean;
    session_end_behavior: string;
  };
  run_plan_id: string;
  description: string;
  data_plan_ids: Partial<Record<RuntimeMode, string>>;
  enabled: boolean;
  enablement: {
    effective_session: string;
    scope: "current_session" | "persistent";
    state: "enabled" | "disabled";
  };
  mandate_ids: string[];
  name: string;
  oms_profile_id: string;
  profile_id: string;
  runtime_assignments: RuntimeAssignment[];
  safety_supervisor: { enabled_by_environment: Record<RuntimeMode, boolean> };
  source_revision_policy: "require_complete" | "allow_partial";
  signal_stream_ids: string[];
  universe_id: string;
  watchlist_ids: string[];
};

export type WatchUniverse = {
  description: string;
  enabled: boolean;
  name: string;
  scanner_view_id: string;
  source: "configured_symbols" | "scanner_view" | "watchlist" | "signal_stream";
  symbols: string[];
  universe_id: string;
};

export type AssignmentSection = { deployments: StrategyRunPlan[]; universes: WatchUniverse[] };

export type DiscoveryCapability = {
  capability_id: string;
  name: string;
  description: string;
  category: string;
  provider: string;
  output_type: string;
  capability_type: "market_data" | "indicator" | "signal" | "event" | "reference" | "system";
  priority: "p0" | "p1" | "p2" | "p3";
  availability: "implemented" | "integration_pending" | "planned_realtime" | "strategy_specific" | "offline_only" | "reference_only";
  inputs: string[];
  fields: string[];
  scanner_columns: Array<{ column_id: string; name: string; source_id: string }>;
  calculation: string;
  timeframes: string[];
  selected_timeframes: string[];
  enabled: boolean;
  configurable: boolean;
  system_required: boolean;
  tier: "universal" | "core" | "watchlist" | "strategy" | "request" | "offline";
  execution_scope: "universal_ingest" | "core_scan" | "watchlist" | "strategy_run" | "request" | "offline";
  allowed_scopes: Array<"universal_ingest" | "core_scan" | "watchlist" | "strategy_run" | "request" | "offline">;
  configuration_policy: "locked" | "configurable" | "generated" | "retired";
  implementation_status: string;
  operational_status: string;
  coverage_status: string;
  cost_class: "minimal" | "low" | "medium" | "high" | "offline" | "unknown";
  stateful: boolean;
  owner: string;
  implementation_version: number;
  cadence: string;
  persistence_policy: string;
  consumers: Array<"universal_ingest" | "core_scan" | "watchlist" | "strategy_run" | "request" | "offline">;
};
export type WatchlistConfig = {
  watchlist_id: string;
  name: string;
  description: string;
  enabled: boolean;
  source_scan_id: string;
  inclusion_rule_sets: string[];
  inclusion_operator: "all" | "any";
  exclusion_rule_sets: string[];
  ranking_field: string;
  ranking_direction: "ascending" | "descending";
  maximum_size: number;
  refresh_interval_ms: number;
  membership_expiry: "end_of_trading_day" | "time_to_live" | "never";
  membership_ttl_ms: number;
  manual_inclusions: string[];
  manual_exclusions: string[];
  columns: string[];
  column_intervals?: Record<string, import("../../app/components/IntervalSelect").IntervalValue>;
  column_aggregations?: Record<string, string>;
  ranking_field_ref?: string;
  ranking_interval?: import("../../app/components/IntervalSelect").IntervalValue;
  ranking_aggregation?: string;
  membership_history: Array<Record<string, unknown>>;
  origin?: "system" | "user";
  template?: boolean;
  availability?: "available" | "integration_pending";
  availability_detail?: string;
};
export type SignalStreamConfig = {
  signal_stream_id: string;
  revision: number;
  name: string;
  description: string;
  enabled: boolean;
  origin?: "system" | "user";
  source_id: string;
  source_scan_id: string;
  source_type: "core_scan" | "watchlist" | "news_events";
  occurrence_source?: "rule_evaluator" | "qmd_live_market_state" | "qmd_squeeze_episode";
  episode_role?: "start" | "milestone";
  episode_ttl_ms?: number;
  inclusion_rule_sets: string[];
  inclusion_operator: "all" | "any";
  columns: string[];
  column_intervals?: Record<string, import("../../app/components/IntervalSelect").IntervalValue>;
  column_aggregations?: Record<string, string>;
  refresh_interval_ms: number;
  trigger_policy: "false_to_true";
  rearm_policy: "after_false" | "after_cooldown";
  cooldown_ms: number;
  maximum_events: number;
  watchlist_routes: Array<{ watchlist_id: string; membership_expiry: "end_of_trading_day" | "time_to_live" | "never"; membership_ttl_ms: number }>;
};
export type WatchlistRuntimeSnapshot = {
  as_of: string;
  history: Array<Record<string, unknown>>;
  history_count: number;
  member_count: number;
  status: "awaiting_first_resolution" | "ready";
  watchlist_count: number;
  watchlists: Array<{ member_count: number; members?: Array<Record<string, unknown>>; watchlist_id: string }>;
  computation_demand?: {
    active_symbol_count: number;
    active_target_count: number;
    active_requirement_count?: number;
    deduplicated_demand_units_saved?: number;
    estimated_demand_units: number;
    requested_demand_units?: number;
    requirement_ref_counts?: Record<string, number>;
    scope_estimated_demand_units: Record<string, number>;
    scope_symbol_counts: Record<string, number>;
  };
  computation_demand_error?: string;
  computation_requirements?: {
    active_requirement_count: number;
    complete: boolean;
    live_requirement_count: number;
    offline_requirement_count: number;
    errors: Record<string, string>;
  };
};
export type HistoricalScannerSnapshot = {
  as_of: string;
  engine_version: string;
  event_count: number;
  indicators: Array<Record<string, unknown>>;
  schema_version: string;
  source_revision: { complete_for_history?: boolean; source_tiers?: string[]; token?: string };
  ticker_count: number;
};
export type EnrichmentFieldDefinition = {
  available_at: string;
  coverage_query_plan: string;
  field_id: string;
  freshness_policy: string;
  group: string;
  historical_support: string;
  label: string;
  null_reasons: string[];
  owner: string;
  provenance: string;
  publication_cadence: string;
  query_plan_id: string;
  source_path: string;
  status: string;
};
export type DiscoveryField = {
  available_at: string;
  column_id: string;
  default_visible: boolean;
  description: string;
  field_ref?: string;
  field_id: string;
  filter_operators: string[];
  filterable: boolean;
  implementation_status: string;
  name: string;
  provenance: string;
  query_plan_id: string;
  registry_authority: string;
  semantic_type: DiscoveryCapability["capability_type"] | "rule_set" | "system";
  source_kind?: "data_field" | "rule_set";
  sortable: boolean;
  source: string;
  source_id: string;
  source_path: string;
  timeframes: string[];
  unit: string;
  value_type: string;
};
export type WatchlistColumn = DiscoveryField & { column_id: string };
export type MarketClassification = { classification_id: string; group: string; name: string; description: string; minimum: number; maximum: number | null; unit: string; source_id: string };
export const WATCHLIST_GUIDED_STEPS = ["identity", "rules", "ranking", "columns", "timing", "overrides", "review"] as const;
export type WatchlistGuidedStep = typeof WATCHLIST_GUIDED_STEPS[number];
export type MarketDiscoverySection = {
  security_universe: { universe_id: string; name: string; description: string; enabled: boolean; configurable: boolean };
  core_scan: { scan_id: string; name: string; description: string; refresh_interval_ms: number; published: boolean; inclusion_rule_sets: string[]; inclusion_operator: "all" | "any"; ranking_field: string; ranking_field_ref?: string; ranking_interval?: import("../../app/components/IntervalSelect").IntervalValue; ranking_aggregation?: string; ranking_direction: "ascending" | "descending"; maximum_size: number; columns: string[]; column_intervals?: Record<string, import("../../app/components/IntervalSelect").IntervalValue>; column_aggregations?: Record<string, string> };
  calculation_catalog: DiscoveryCapability[];
  atomic_fields: AtomicField[];
  data_fields: DataFieldDefinition[];
  data_field_plan?: { field_refs?: string[]; timeframes?: string[] };
  classifications: MarketClassification[];
  field_catalog: DiscoveryField[];
  column_catalog: WatchlistColumn[];
  rule_sets: RuleSetDefinition[];
  watchlists: WatchlistConfig[];
  signal_streams: SignalStreamConfig[];
};

export function capabilityTypeLabel(type: DiscoveryCapability["capability_type"]): string {
  return {
    event: "Event",
    indicator: "Indicator",
    market_data: "Market data",
    reference: "Reference data",
    signal: "Signal",
    system: "System",
  }[type];
}

export function abstractionKindForCapability(type: DiscoveryCapability["capability_type"]): AbstractionKind {
  return {
    event: "signal",
    indicator: "derivation",
    market_data: "field",
    reference: "field",
    signal: "signal",
    system: "processing_step",
  }[type] as AbstractionKind;
}

export function discoveryFieldInput(field: DiscoveryField): StrategyInput {
  return {
    category: readableLabel(field.semantic_type),
    label: field.name,
    parameter: field.source_id,
    provider: field.source,
    runtime_field: field.column_id || field.field_id || field.source_id,
    source_id: field.source_id,
    summary: field.description,
    timeframes: field.timeframes.length ? field.timeframes : ["event"],
    unit: field.unit,
    value_type: field.value_type,
    filter_operators: field.filter_operators,
  };
}

export function capabilityScopeLabel(scope: DiscoveryCapability["execution_scope"]): string {
  return {
    core_scan: "Core Scan",
    offline: "Offline",
    request: "Chart/request",
    strategy_run: "Strategy Run",
    universal_ingest: "Universal Ingest",
    watchlist: "Watchlist",
  }[scope];
}

export function normalizedDiscoveryCapability(capability: DiscoveryCapability): DiscoveryCapability {
  const value = capability as DiscoveryCapability & Partial<DiscoveryCapability>;
  return {
    ...capability,
    availability: value.availability ?? "implemented",
    calculation: value.calculation ?? value.description ?? "QMD publishes this causally available observation.",
    capability_type: value.capability_type,
    fields: value.fields ?? [value.capability_id],
    scanner_columns: value.scanner_columns ?? [],
    inputs: value.inputs ?? [value.provider || "QMD"],
    priority: value.priority ?? (value.system_required ? "p0" : "p2"),
    selected_timeframes: value.selected_timeframes ?? value.timeframes ?? [],
    execution_scope: value.execution_scope ?? (value.tier === "universal" ? "universal_ingest" : value.tier === "watchlist" ? "watchlist" : value.tier === "strategy" ? "strategy_run" : value.tier === "request" ? "request" : value.tier === "offline" ? "offline" : "core_scan"),
    allowed_scopes: value.allowed_scopes ?? [],
    configuration_policy: value.configuration_policy ?? (value.system_required ? "locked" : value.configurable ? "configurable" : "generated"),
    implementation_status: value.implementation_status ?? value.availability ?? "implemented",
    operational_status: value.operational_status ?? "unknown",
    coverage_status: value.coverage_status ?? "unknown",
    cost_class: value.cost_class ?? "unknown",
    stateful: value.stateful ?? false,
    owner: value.owner ?? value.provider ?? "Unknown",
    implementation_version: value.implementation_version ?? 1,
    cadence: value.cadence ?? "service_owned",
    persistence_policy: value.persistence_policy ?? "not_registered",
    consumers: value.consumers ?? value.allowed_scopes ?? [],
  } as DiscoveryCapability;
}

export type PortfolioPolicy = Record<string, Primitive | string[]>;
export type Mandate = {
  account_key: string;
  allow_replacement: boolean;
  assignment_mode: "single" | "replicated" | "weighted" | "partitioned";
  maximum_action_authority: "manual" | "confirm" | "automatic";
  run_plan_id: string;
  principal_kind?: "session" | "strategy_deployment";
  principal_id?: string;
  enabled: boolean;
  mandate_id: string;
  maximum_cash_fraction: number;
  maximum_planned_risk_fraction: number;
  maximum_positions: number;
  minimum_replacement_improvement_pct: number;
  allocation_weight: number;
};
export type PortfolioSection = { groups: ParameterMap[]; mandates: Mandate[]; policies: PortfolioPolicy[] };

export type OmsProfile = {
  description: string;
  editable: boolean;
  name: string;
  origin: "system" | "user";
  profile_id: string;
  revision: number;
  settings: {
    entry_execution_policy_id: string;
    exit_execution_policy_id: string;
    protection_profile_id: string;
    entry_urgency: string;
    exit_urgency: string;
    limit_offset_bps: number;
    protection: {
      maximum_risk_pct: number;
      stop_method: string;
      structure_buffer_bps: number;
      trailing_enabled: boolean;
      volatility_multiple: number;
    };
    tick_size: number;
    session_routing: "smart";
  };
};
export type ExecutionPolicyConfig = {
  description: string;
  editable: boolean;
  envelope: {
    deadline_ms: number;
    maximum_buy_price: number | null;
    maximum_reprices: number;
    minimum_reprice_interval_ms: number;
    minimum_sell_price: number | null;
  };
  name: string;
  origin: "system" | "user";
  partial_fill_policy: "complete_remainder" | "accept_partial" | "cancel_remainder";
  policy_id: string;
  quote_source: "qmd" | "ibkr" | "simulated";
  revision: number;
};
export type ProtectionStopConfig = {
  anchor_ordinal: string;
  anchor_source: "strategy_swing" | "explicit";
  buffer_bps: number;
  distance_bps: number | null;
  distance_percent: number | null;
  maximum_cash_risk: number | null;
  order_type: "STP" | "STOP_LIMIT";
  price: number | null;
  rule_type: string;
  stop_limit_offset_bps: number | null;
  structural_timeframe: string;
  volatility_multiple: number | null;
};
export type ProtectionTrailingConfig = {
  activation_gain_percent: number;
  amount: number | null;
  breakeven_buffer_bps: number;
  percent: number | null;
  rule_type: string;
  structural_timeframe: string;
  volatility_multiple: number | null;
};
export type ProtectionSliceConfig = {
  profit_target_price: number | null;
  quantity_fraction: number;
  slice_id: string;
  stop: ProtectionStopConfig;
  trailing: ProtectionTrailingConfig;
  use_strategy_profit_target: boolean;
};
export type ProtectionProfileConfig = {
  add_policy: string;
  description: string;
  editable: boolean;
  emergency_repair_deadline_ms: number;
  mandatory_catastrophic_backstop: boolean;
  name: string;
  origin: "system" | "user";
  profile_id: string;
  profit_pocket_transition: string;
  revision: number;
  slices: ProtectionSliceConfig[];
};
export type OmsSection = {
  execution_policies: ExecutionPolicyConfig[];
  profiles: OmsProfile[];
  protection_profiles: ProtectionProfileConfig[];
};

export type AccountBinding = {
  account_class: string;
  account_key: string;
  base_currency: string;
  enabled: boolean;
  modes: RuntimeMode[];
  name: string;
  portfolio_policy_id: string;
  session_key: string;
  source_account_id: string;
  source_account_env?: string;
  system_managed?: boolean;
};
export type AccountSection = { bindings: AccountBinding[] };
export type SessionProfile = {
  session_profile_id: string;
  name: string;
  description: string;
  enabled: boolean;
  modes: RuntimeMode[];
  market_data: { authority: string; clock: string };
  manual_authority: { enabled: boolean; maximum: "manual" | "confirm" | "automatic" };
  recovery_policy: string;
  execution_route_ids: string[];
  default_execution_route_id: string;
};
export type ExecutionRoute = {
  execution_route_id: string;
  name: string;
  session_profile_id: string;
  account_key: string;
  portfolio_mandate_id: string;
  oms_profile_id: string;
  modes: RuntimeMode[];
  enabled: boolean;
  manual_enabled: boolean;
  system_generated?: boolean;
};
export type StrategyDeployment = {
  strategy_deployment_id: string;
  name: string;
  description: string;
  run_plan_id: string;
  session_profile_id: string;
  execution_route_ids: string[];
  portfolio_mandate_ids: string[];
  enabled: boolean;
  headless: boolean;
  priority: number;
  modes: RuntimeMode[];
  system_generated?: boolean;
};
export type SessionSection = { profiles: SessionProfile[]; execution_routes: ExecutionRoute[]; strategy_deployments: StrategyDeployment[] };

export type Draft = {
  accounts: AccountSection;
  assignments: AssignmentSection;
  market_discovery: MarketDiscoverySection;
  oms: OmsSection;
  portfolio: PortfolioSection;
  schema_version: number;
  sessions: SessionSection;
  strategy: StrategySection;
  trading_actions: TradingActionsConfiguration;
  updated_at?: string;
};

function readableLabel(value: string) {
  return value.replaceAll(".", " · ").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
