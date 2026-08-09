import {
  BadgeCheck,
  ArrowLeft,
  ArrowRight,
  BookOpenCheck,
  Boxes,
  BriefcaseBusiness,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clipboard,
  FileInput,
  GitBranch,
  LockKeyhole,
  Network,
  PencilLine,
  Plus,
  RotateCcw,
  Save,
  ScanSearch,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Target,
  Trash2,
  TriangleAlert,
  WalletCards,
  X,
} from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { api } from "../api/client";
import { readCanvasRegistry, snapshotCanvasProfile } from "../app/canvasWorkspace";
import { InventoryFilterSelect } from "../app/components/InventoryFilterSelect";

export type TradingConfigurationSection =
  | "strategy"
  | "discovery"
  | "assignments"
  | "portfolio"
  | "oms"
  | "accounts"
  | "revisions";

type StrategyAuthoringStage = "identity" | "discovery" | "overview" | "entry" | "position" | "reentry" | "exit" | "portfolio" | "oms" | "authority" | "handoff";

type RuntimeMode = "replay" | "backtest" | "backtest_debug" | "paper" | "live";
type ActionAuthority = "disabled" | "manual" | "confirm" | "automatic" | "inherit";
type Primitive = boolean | number | string;
type CatalogParameterValue = Primitive | null;
type SectionCatalogValue = CatalogParameterValue | string[];
type ParameterMap = Record<string, unknown>;
type StrategyPhaseMode = "automatic" | "manual";

type CapabilityParameter = {
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

type CapabilityDefinition = {
  capability_id: string;
  category: string;
  defaults: Record<string, Primitive>;
  name: string;
  order_entry_action: boolean;
  parameters: CapabilityParameter[];
  revision: number;
  summary: string;
};

type CapabilityBinding = {
  capability_id: string;
  enabled: boolean;
  revision: number;
  settings: Record<string, Primitive>;
};

type StrategyProfile = {
  capabilities: CapabilityBinding[];
  definition_id: string;
  definition_revision: number;
  description: string;
  editable: boolean;
  enabled: boolean;
  name: string;
  origin: "system" | "user";
  protected: boolean;
  rule_set_catalog: RuleSetDefinition[];
  lifecycle: StrategyLifecycle;
  parameters: ParameterMap;
  profile_id: string;
  revision: number;
  publication_status: "draft" | "published" | "template";
  derived_from_profile_id: string;
  composition: StrategyComposition;
};

type StrategyComposition = {
  watchlist_id: string;
  portfolio_policy_id: string;
  oms_profile_id: string;
  account_keys: string[];
  allowed_environments: RuntimeMode[];
  action_authority: StrategyRunPlan["action_authority"];
};

type StrategyInput = {
  category: string;
  label: string;
  parameter: string;
  provider: string;
  runtime_field: string;
  source_id: string;
  summary: string;
  timeframes: string[];
  value_type: string;
};

type StrategyCatalogItem = {
  category: string;
  detail: string;
  group: string;
  groupOrder: number;
  id: string;
  importance: number;
  kind: string;
  label: string;
  metadata: Array<{ label: string; value: string }>;
  parameter: string;
  ruleSetId?: string;
  usage: string;
};

type SectionCatalogItem = {
  detail: string;
  group: string;
  groupOrder: number;
  id: string;
  label: string;
  path: string;
  value: SectionCatalogValue;
};

type RuleCondition = {
  comparator: string;
  condition_id: string;
  enabled: boolean;
  left_source_id: string;
  left_timeframe: string;
  right_source_id: string;
  right_timeframe: string;
  value: Primitive | null;
};

type RuleGroup = {
  conditions: RuleCondition[];
  enabled: boolean;
  group_id: string;
  label: string;
  operator: "all" | "any" | "score";
  required_score: number;
};

type RuleSetDefinition = {
  conditions: RuleCondition[];
  description: string;
  enabled: boolean;
  name: string;
  operator: "all" | "any" | "score";
  required_score: number;
  rule_set_id: string;
};

type RuleExpression =
  | { kind: "rule_set"; rule_set_id: string }
  | { children: RuleExpression[]; kind: "operator"; operator: "and" | "or" };

type RuleStage = {
  expression?: RuleExpression;
  groups?: RuleGroup[];
  operator?: "all" | "any";
};

type EntryRules = {
  blockers: RuleStage;
  confirmation: RuleStage;
  opportunity: RuleStage;
};

type EntryAuthoringPage = keyof EntryRules | "mode" | "capital" | "priority" | "execution" | "partial_fill" | "protection" | "initial_stop";

const ENTRY_AUTHORING_PAGES: Array<{ description: string; id: EntryAuthoringPage; label: string; title: string }> = [
  { description: "Choose whether Strategy evaluates and emits initial-entry intent for this profile.", id: "mode", label: "Mode", title: "Should Strategy automate initial entry?" },
  { description: "Define the evidence paths that identify a candidate setup before confirmation is considered.", id: "opportunity", label: "Opportunity", title: "What identifies a possible entry?" },
  { description: "Define the independent evidence paths that must validate an identified opportunity.", id: "confirmation", label: "Confirmation", title: "What proves the entry is actionable?" },
  { description: "Define the conditions that veto entry even when opportunity and confirmation have passed.", id: "blockers", label: "Blockers", title: "What must prevent the entry?" },
  { description: "Express the desired exposure before Portfolio applies account capacity, mandates, and risk limits.", id: "capital", label: "Capital", title: "How much exposure should Strategy request?" },
  { description: "Permit or prohibit Portfolio from considering a stronger opportunity as a replacement candidate.", id: "priority", label: "Replacement", title: "May Portfolio propose a replacement?" },
  { description: "Choose the broker-neutral execution behavior OMS applies after Portfolio approves quantity.", id: "execution", label: "Execution", title: "How should OMS seek the entry fill?" },
  { description: "Choose how OMS handles an incomplete fill without exceeding Portfolio's approved quantity.", id: "partial_fill", label: "Partial fill", title: "What should happen after a partial fill?" },
  { description: "Select the independently versioned protection contract attached to confirmed entry fills.", id: "protection", label: "Protection", title: "Which protection follows the entry fill?" },
  { description: "Set the definition-specific invalidation values used by the strategy's initial risk boundary.", id: "initial_stop", label: "Initial stop", title: "What defines the initial invalidation boundary?" },
];

type ManageAuthoringPage =
  | "mode" | "add_actions" | "add_evidence" | "add_capital" | "add_replacement" | "add_execution" | "add_partial_fill" | "add_protection"
  | "trailing" | "capabilities";

const MANAGE_AUTHORING_PAGES: Array<{ description: string; id: ManageAuthoringPage; label: string; title: string }> = [
  { description: "Choose whether Strategy manages an open position through adds, trailing behavior, and optional capabilities.", id: "mode", label: "Mode", title: "Should Strategy automate position management?" },
  { description: "Create, name, enable, and limit the add actions that may increase an already-open position.", id: "add_actions", label: "Add actions", title: "Which position-add actions are available?" },
  { description: "Combine predefined rule sets to decide when the selected add action may request more exposure.", id: "add_evidence", label: "Add evidence", title: "What permits the selected add action?" },
  { description: "Express the selected add action's desired exposure before Portfolio applies current account and risk limits.", id: "add_capital", label: "Add capital", title: "How much exposure should the add request?" },
  { description: "Permit or prohibit Portfolio from considering a stronger add opportunity as a replacement candidate.", id: "add_replacement", label: "Add replacement", title: "May the add request propose a replacement?" },
  { description: "Choose the broker-neutral execution behavior OMS uses after Portfolio approves the selected add quantity.", id: "add_execution", label: "Add execution", title: "How should OMS seek the add fill?" },
  { description: "Choose how OMS handles the unfilled remainder of the selected add without exceeding approved quantity.", id: "add_partial_fill", label: "Add partial fill", title: "What should happen after a partial add fill?" },
  { description: "Select the independently versioned protection contract attached to confirmed fills from the selected add.", id: "add_protection", label: "Add protection", title: "Which protection follows the add fill?" },
  { description: "Configure definition-specific trailing behavior that may activate while the position remains open.", id: "trailing", label: "Trailing", title: "How may protection trail the open position?" },
  { description: "Enable optional code-defined position behavior without changing entry, exit, Portfolio, OMS, or safety authority.", id: "capabilities", label: "Capabilities", title: "Which optional management capability is enabled?" },
];

type ReentryAuthoringPage =
  | "mode" | "reentry_policy" | "reentry_opportunity" | "reentry_confirmation" | "reentry_blockers"
  | "reentry_capital" | "reentry_replacement" | "reentry_execution" | "reentry_partial_fill" | "reentry_protection";

const REENTRY_AUTHORING_PAGES: Array<{ description: string; id: ReentryAuthoringPage; label: string; title: string }> = [
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

type ExitAuthoringPage = "mode" | "targets" | "profit_pocket" | "routes" | "evidence" | "timing" | "action" | "execution" | "partial_fill" | "protection";

const EXIT_AUTHORING_PAGES: Array<{ description: string; id: ExitAuthoringPage; label: string; title: string }> = [
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

type CapitalRequestConfig = {
  allow_replacement: boolean;
  mode: "fixed_quantity" | "mandate_fraction" | "risk_fraction" | "all_available";
  value: number;
};

type OrderIntentConfig = {
  deadline_ms: number;
  execution_policy: string;
  partial_fill_policy: "complete_remainder" | "accept_partial" | "cancel_remainder";
  protection_profile: string;
};

type AddStep = {
  capital_request: CapitalRequestConfig;
  enabled: boolean;
  maximum_uses: number;
  name: string;
  order_intent: OrderIntentConfig;
  rules: RuleStage;
  step_id: string;
};

type ExitRuleSet = {
  action: "close" | "reduce";
  enabled: boolean;
  name: string;
  position_fraction: number;
  order_intent: OrderIntentConfig;
  rules: RuleStage;
  rule_set_id: string;
  summary: string;
  timing: { active_after_ms: number; expires_after_ms: number };
};

type StrategyLifecycle = {
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
    add_steps: AddStep[];
    capital_request: CapitalRequestConfig;
    order_intent: OrderIntentConfig;
  };
  reentry: {
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

type StrategySection = {
  capability_catalog: CapabilityDefinition[];
  default_profile_id: string;
  definitions: Array<{ automatic: boolean; direction: string; name: string; revision: number; strategy_id: string; supported_sides: Array<"long" | "short"> }>;
  input_catalog: StrategyInput[];
  profile_templates: StrategyProfile[];
  profiles: StrategyProfile[];
};

type RuntimeAssignment = {
  account_key: string;
  assignment_id: string;
  conid: number;
  parameters?: ParameterMap;
  permissions?: Record<string, boolean>;
  status?: string;
  ticker: string;
};

type StrategyRunPlan = {
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
  enabled: boolean;
  mandate_ids: string[];
  name: string;
  oms_profile_id: string;
  profile_id: string;
  runtime_assignments: RuntimeAssignment[];
  safety_supervisor: { enabled_by_environment: Record<RuntimeMode, boolean> };
  universe_id: string;
};

type WatchUniverse = {
  description: string;
  enabled: boolean;
  name: string;
  scanner_view_id: string;
  source: "configured_symbols" | "scanner_view" | "watchlist";
  symbols: string[];
  universe_id: string;
};

type AssignmentSection = { deployments: StrategyRunPlan[]; universes: WatchUniverse[] };

type DiscoveryCapability = {
  capability_id: string;
  name: string;
  description: string;
  category: string;
  provider: string;
  output_type: string;
  timeframes: string[];
  enabled: boolean;
  configurable: boolean;
  system_required: boolean;
  tier: "core" | "watchlist";
};
type WatchlistConfig = {
  watchlist_id: string;
  name: string;
  description: string;
  enabled: boolean;
  source_scan_id: string;
  inclusion_rule_sets: string[];
  exclusion_rule_sets: string[];
  ranking_field: string;
  maximum_size: number;
  refresh_interval_ms: number;
  membership_ttl_ms: number;
  manual_inclusions: string[];
  manual_exclusions: string[];
  calculations: string[];
  membership_history: Array<Record<string, unknown>>;
};
const WATCHLIST_GUIDED_STEPS = ["identity", "rules", "ranking", "timing", "overrides", "calculations", "review"] as const;
type WatchlistGuidedStep = typeof WATCHLIST_GUIDED_STEPS[number];
type MarketDiscoverySection = {
  security_universe: { universe_id: string; name: string; description: string; enabled: boolean; configurable: boolean };
  core_scan: { scan_id: string; name: string; description: string; refresh_interval_ms: number; published: boolean; calculations: DiscoveryCapability[] };
  rule_sets: RuleSetDefinition[];
  watchlists: WatchlistConfig[];
};

type PortfolioPolicy = Record<string, Primitive | string[]>;
type Mandate = {
  account_key: string;
  allow_replacement: boolean;
  assignment_mode: "single" | "replicated" | "weighted" | "partitioned";
  maximum_action_authority: "manual" | "confirm" | "automatic";
  run_plan_id: string;
  enabled: boolean;
  mandate_id: string;
  maximum_cash_fraction: number;
  maximum_planned_risk_fraction: number;
  maximum_positions: number;
  minimum_replacement_improvement_pct: number;
  allocation_weight: number;
};
type PortfolioSection = { groups: ParameterMap[]; mandates: Mandate[]; policies: PortfolioPolicy[] };

type OmsProfile = {
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
type ExecutionPolicyConfig = {
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
type ProtectionStopConfig = {
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
type ProtectionTrailingConfig = {
  activation_gain_percent: number;
  amount: number | null;
  breakeven_buffer_bps: number;
  percent: number | null;
  rule_type: string;
  structural_timeframe: string;
  volatility_multiple: number | null;
};
type ProtectionSliceConfig = {
  profit_target_price: number | null;
  quantity_fraction: number;
  slice_id: string;
  stop: ProtectionStopConfig;
  trailing: ProtectionTrailingConfig;
  use_strategy_profit_target: boolean;
};
type ProtectionProfileConfig = {
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
type OmsSection = {
  execution_policies: ExecutionPolicyConfig[];
  profiles: OmsProfile[];
  protection_profiles: ProtectionProfileConfig[];
};

type AccountBinding = {
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
};
type AccountSection = { bindings: AccountBinding[] };

type Draft = {
  accounts: AccountSection;
  assignments: AssignmentSection;
  market_discovery: MarketDiscoverySection;
  oms: OmsSection;
  portfolio: PortfolioSection;
  schema_version: number;
  strategy: StrategySection;
  updated_at?: string;
};

function normalizeDraft(payload: any): Draft {
  const runPlans = payload?.run_plans ?? payload?.assignments ?? { plans: [], universes: [] };
  const strategy = payload?.strategy ?? {};
  const normalizeProfile = (profile: any) => {
    const phaseModes = {
      initial_entry: "automatic",
      manage: "automatic",
      reentry: profile.lifecycle?.reentry?.enabled === false ? "manual" : "automatic",
      exit: "automatic",
      ...(profile.lifecycle?.phase_modes ?? {}),
    };
    return {
      ...profile,
      publication_status: profile.publication_status ?? (profile.origin === "system" ? "template" : "draft"),
      derived_from_profile_id: profile.derived_from_profile_id ?? "",
      composition: {
        watchlist_id: "core-candidates",
        portfolio_policy_id: payload?.portfolio?.policies?.[0]?.policy_id ?? "default",
        oms_profile_id: payload?.oms?.profiles?.[0]?.profile_id ?? "adaptive-regular",
        account_keys: (payload?.accounts?.bindings ?? []).filter((row: any) => row.enabled).map((row: any) => row.account_key),
        allowed_environments: ["replay", "backtest", "backtest_debug"],
        action_authority: {
          default: "confirm", initial_entry: "inherit", add: "inherit", reentry: "inherit", strategic_exit: "automatic", protective_exit: "automatic", emergency_exit: "automatic",
        },
        ...(profile.composition ?? {}),
      },
      lifecycle: {
        ...profile.lifecycle,
        phase_modes: phaseModes,
        reentry: { ...profile.lifecycle?.reentry, enabled: phaseModes.reentry === "automatic" },
        trading_behavior: {
          eligible_sessions: profile.lifecycle?.trading_behavior?.eligible_sessions ?? ["regular"],
          side: profile.lifecycle?.trading_behavior?.side ?? "long",
        },
      },
    };
  };
  return {
    ...payload,
    market_discovery: payload?.market_discovery ?? { security_universe: {}, core_scan: { calculations: [] }, rule_sets: [], watchlists: [] },
    strategy: {
      ...strategy,
      profile_templates: (strategy.profile_templates ?? []).map(normalizeProfile),
      profiles: (strategy.profiles ?? []).map(normalizeProfile),
    },
    assignments: {
      deployments: runPlans.plans ?? runPlans.deployments ?? [],
      universes: runPlans.universes ?? [],
    },
  } as Draft;
}

function serializeDraft(draft: Draft) {
  const { assignments, ...rest } = draft;
  return { ...rest, run_plans: { plans: assignments.deployments, universes: assignments.universes } };
}

const CONFIGURATION_SESSION_KEY = "trading-configuration-session-v1";

function readSessionConfiguration(base: Draft): Draft {
  try {
    const stored = window.sessionStorage.getItem(CONFIGURATION_SESSION_KEY);
    return stored ? normalizeDraft(JSON.parse(stored)) : base;
  } catch {
    window.sessionStorage.removeItem(CONFIGURATION_SESSION_KEY);
    return base;
  }
}

function writeSessionConfiguration(draft: Draft) {
  window.sessionStorage.setItem(CONFIGURATION_SESSION_KEY, JSON.stringify(serializeDraft(draft)));
}

type ConfigurationExperience = "guided" | "expert";
type OmsGuidedStage = "execution" | "protection";
type GuidedStep = TradingConfigurationSection | OmsGuidedStage;

type Revision = {
  approved_at: string;
  content_hash: string;
  label: string;
  payload: Draft & { canvas: { profile: Record<string, unknown>; revision: string } };
  revision: number;
  revision_id: string;
};

const SECTION_META = {
  discovery: {
    eyebrow: "QMD discovery authority",
    icon: ScanSearch,
    title: "Market Discovery",
    description: "Review QMD's complete capability inventory and configure reusable Watchlists.",
  },
  strategy: {
    eyebrow: "Step 1 · Define behavior",
    icon: GitBranch,
    title: "Strategy Studio",
    description: "Configure reusable strategy behavior, lifecycle rules, and capabilities.",
  },
  assignments: {
    eyebrow: "Step 2 · Make it usable",
    icon: Network,
    title: "Strategy Run Plans",
    description: "Bind a Strategy Profile to environments, action authority, OMS, and account mandates.",
  },
  portfolio: {
    eyebrow: "Step 3 · Allocate capital",
    icon: BriefcaseBusiness,
    title: "Portfolio & Risk",
    description: "Define account risk limits, capital mandates, and replacement permissions.",
  },
  oms: {
    eyebrow: "Shared execution authority",
    icon: ShieldCheck,
    title: "OMS & Protection",
    description: "Define reusable execution tactics, partial-fill behavior, and position protection.",
  },
  accounts: {
    eyebrow: "Stable runtime boundaries",
    icon: Boxes,
    title: "Accounts & Sessions",
    description: "Bind application accounts to broker or simulated sessions and permissions.",
  },
  revisions: {
    eyebrow: "Final publication gate",
    icon: BookOpenCheck,
    title: "Approved Releases",
    description: "Validate and publish the immutable configuration used by new runs.",
  },
} as const;

const LEGACY_ENTRY_LOGIC_PATHS = new Set([
  "entry.breakout_timeframe",
  "entry.breakout_reference",
  "entry.breakout_buffer_bps",
  "entry.minimum_confirmation_score",
  "entry.news_minimum_score",
  "entry.price_expansion_minimum_score",
  "entry.vwap_transition_minimum_score",
  "entry.qmd.minimum_score",
  "entry.qmd.minimum_confidence",
  "entry.qmd.weight",
  "entry.vwap.minimum_slope_bps_per_second",
  "entry.vwap.weight",
  "entry.macd.require_positive_histogram",
  "entry.macd.weight",
  "entry.veto.flow_price_divergence",
  "entry.veto.liquidity_dislocation",
]);

export function TradingConfigurationPage({ section }: { section: TradingConfigurationSection }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [approved, setApproved] = useState<Revision | null>(null);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [label, setLabel] = useState("");
  const [status, setStatus] = useState<"loading" | "ready" | "saving" | "saved" | "error">("loading");
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"success" | "error">("success");
  const [experience, setExperienceState] = useState<ConfigurationExperience>("expert");
  const [showStudioHome, setShowStudioHome] = useState(false);
  const [omsGuidedStage, setOmsGuidedStageState] = useState<OmsGuidedStage>(() => readStoredOmsStage());
  const meta = SECTION_META[section];
  const Icon = meta.icon;

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    Promise.all([
      api<Draft>("/api/trading/configuration/base"),
      api<{ approved: Revision | null }>("/api/trading/configuration/approved"),
      api<{ rows: Revision[] }>("/api/trading/configuration/revisions"),
    ])
      .then(([nextDraft, approvedPayload, revisionPayload]) => {
        if (cancelled) return;
        setDraft(readSessionConfiguration(normalizeDraft(nextDraft)));
        setApproved(approvedPayload.approved ? { ...approvedPayload.approved, payload: normalizeDraft(approvedPayload.approved.payload) as Revision["payload"] } : null);
        setRevisions(revisionPayload.rows.map((row) => ({ ...row, payload: normalizeDraft(row.payload) as Revision["payload"] })));
        setStatus("ready");
      })
      .catch((reason) => {
        if (cancelled) return;
        setMessage(reason instanceof Error ? reason.message : String(reason));
        setMessageTone("error");
        setStatus("error");
      });
    return () => { cancelled = true; };
  }, [section]);

  function updateDraft<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => {
      if (!current) return current;
      const next = { ...current, [key]: value };
      writeSessionConfiguration(next);
      return next;
    });
    setMessage("");
    setStatus("ready");
  }

  function updateConfigurationBook(value: Draft) {
    writeSessionConfiguration(value);
    setDraft(value);
    setMessage("");
    setStatus("ready");
  }

  function setExperience(value: ConfigurationExperience) {
    window.localStorage.setItem("trading-configuration-experience", value);
    window.sessionStorage.setItem("configuration-studio-started", "true");
    setExperienceState(value);
    setShowStudioHome(false);
  }

  function setOmsGuidedStage(value: OmsGuidedStage) {
    window.localStorage.setItem("trading-configuration-oms-stage", value);
    setOmsGuidedStageState(value);
  }

  async function persistSections(next: Draft, sections: Array<Exclude<TradingConfigurationSection, "revisions">>, successMessage: string) {
    void sections;
    writeSessionConfiguration(next);
    setDraft(next);
    setStatus("ready");
    setMessageTone("success");
    setMessage(successMessage);
    return next;
  }

  function continueSession(nextStep: GuidedStep) {
    if (!draft || section === "revisions") return;
    navigateGuidedStep(nextStep, setOmsGuidedStage);
  }

  async function deleteStrategyProfile(profileId: string) {
    if (!draft) throw new Error("Configuration session is unavailable");
    const profile = draft.strategy.profiles.find((row) => row.profile_id === profileId);
    if (!profile) throw new Error(`Unknown Strategy Profile: ${profileId}`);
    if (profile.publication_status === "published") throw new Error("Published strategies are immutable and cannot be deleted; clone one to create a new strategy");
    if (profile.protected || profileId === draft.strategy.default_profile_id) throw new Error("The protected default Strategy Profile cannot be deleted");
    const fallbackId = draft.strategy.default_profile_id;
    const next = normalizeDraft({
      ...draft,
      strategy: { ...draft.strategy, profiles: draft.strategy.profiles.filter((row) => row.profile_id !== profileId) },
      assignments: {
        ...draft.assignments,
        deployments: draft.assignments.deployments.map((row) => row.profile_id === profileId ? { ...row, profile_id: fallbackId } : row),
      },
    });
    writeSessionConfiguration(next);
    setDraft(next);
    setStatus("ready");
    setMessageTone("success");
    setMessage("Strategy removed from this session.");
    return next;
  }

  async function publish(strategyProfileId = "") {
    if (!draft) return;
    setStatus("saving");
    setMessage("");
    try {
      const canvas = canvasApprovalSnapshot();
      if (!canvas.ready) throw new Error("Configure at least one Canvas container before publishing.");
      const revision = await api<Revision>("/api/trading/configuration/publish", {
        body: JSON.stringify({ canvas_profile: canvas.profile, canvas_revision: canvas.revision, configuration: serializeDraft(draft), label, strategy_profile_id: strategyProfileId }),
        method: "POST",
      });
      setApproved(revision);
      window.sessionStorage.removeItem(CONFIGURATION_SESSION_KEY);
      setDraft(normalizeDraft(revision.payload));
      setRevisions((current) => [revision, ...current.filter((row) => row.revision_id !== revision.revision_id)]);
      window.dispatchEvent(new CustomEvent("quant-trading-configuration-published"));
      setLabel("");
      setStatus("saved");
      setMessageTone("success");
      setMessage(`Release ${revision.revision} is approved. New Replay runs now pin this exact configuration.`);
    } catch (reason) {
      setStatus("error");
      setMessageTone("error");
      setMessage(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <div className="trading-configuration-page" data-configuration-experience={experience} data-configuration-section={section}>
      <header className="configuration-page-header">
        <div className="configuration-page-icon"><Icon size={20} /></div>
        <div className="configuration-page-heading">
          <span>{meta.eyebrow}</span>
          <h1>{meta.title}</h1>
          <p>{meta.description}</p>
        </div>
        <div className="configuration-header-controls">
          <RevisionBadge approved={approved} />
        </div>
      </header>

      {draft && showStudioHome ? (
        <ConfigurationStudioHome
          approved={approved}
          draft={draft}
          pending={status === "saving"}
          onApplyRecommended={(next) => persistSections(next, ["strategy", "assignments"], "Protected Strategy and OMS starting points applied. Account and risk decisions were preserved for review.")}
          onCloneApproved={(next) => persistSections(next, ["strategy", "assignments", "portfolio", "oms", "accounts"], "The approved release was copied into this session. Runtime authority remains unchanged until publication.")}
          onStart={(value) => { setExperience(value); if (value === "guided") navigateGuidedStep("strategy", setOmsGuidedStage); }}
        />
      ) : (
        <>
      {message ? (
        <div className={`configuration-message ${messageTone}`}>
          {messageTone === "error" ? <TriangleAlert size={17} /> : <CheckCircle2 size={17} />}
          <span>{message}</span>
        </div>
      ) : null}

      {section !== "strategy" && section !== "discovery" && section !== "revisions" && draft ? (
        <ConfigurationSectionStudio
          draft={draft}
          guided={<GuidedConfiguration
            approved={approved}
            draft={draft}
            label={label}
            omsStage={omsGuidedStage}
            onChange={updateDraft}
            onContinue={continueSession}
            onLabelChange={setLabel}
            onOmsStageChange={setOmsGuidedStage}
            onPublish={publish}
            onSwitchToExpert={() => undefined}
            publishing={status === "saving"}
            revisions={revisions}
            section={section}
          />}
          onChange={(value) => updateDraft(section, value as never)}
          section={section}
        />
      ) : experience === "guided" && draft ? (
        <div className="configuration-guided-workspace"><GuidedConfiguration
          approved={approved}
          draft={draft}
          label={label}
          omsStage={omsGuidedStage}
          onChange={updateDraft}
          onContinue={continueSession}
          onLabelChange={setLabel}
          onOmsStageChange={setOmsGuidedStage}
          onPublish={publish}
          onSwitchToExpert={() => setExperience("expert")}
          publishing={status === "saving"}
          revisions={revisions}
          section={section}
        /></div>
      ) : section === "revisions" ? (
        <div className="configuration-expert-workspace"><RevisionPublisher
          approved={approved}
          draft={draft}
          label={label}
          revisions={revisions}
          publishing={status === "saving"}
          onLabelChange={setLabel}
          onPublish={publish}
        /></div>
      ) : draft ? (
        <div className="configuration-expert-workspace">
          <div className="configuration-expert-editor">
            {section === "strategy" ? <StrategyStudio approved={approved} draft={draft} label={label} onChange={(value) => updateDraft("strategy", value)} onDeleteProfile={deleteStrategyProfile} onDraftChange={updateConfigurationBook} onLabelChange={setLabel} onPublish={publish} publishing={status === "saving"} revisions={revisions} section={draft.strategy} /> : null}
            {section === "discovery" ? <MarketDiscoveryStudio onChange={(value) => updateDraft("market_discovery", value)} section={draft.market_discovery} /> : null}
            {section === "assignments" ? <DeploymentEditor draft={draft} onChange={(value) => updateDraft("assignments", value)} /> : null}
            {section === "portfolio" ? <PortfolioEditor draft={draft} onChange={(value) => updateDraft("portfolio", value)} /> : null}
            {section === "oms" ? <OmsEditor section={draft.oms} onChange={(value) => updateDraft("oms", value)} /> : null}
            {section === "accounts" ? <AccountsEditor draft={draft} onChange={(value) => updateDraft("accounts", value)} /> : null}
          </div>
        </div>
      ) : <ConfigurationLoading />}
        </>
      )}
    </div>
  );
}

function ConfigurationExperienceBar({ experience, onExperienceChange, onOpenHome }: {
  experience: ConfigurationExperience;
  onExperienceChange: (value: ConfigurationExperience) => void;
  onOpenHome: () => void;
}) {
  return <div className="configuration-experience-bar">
    <span className="configuration-editing-label">Editing mode</span>
    <div className="configuration-experience-actions">
      <button className="configuration-setup-link" onClick={onOpenHome} type="button"><RotateCcw size={14} /> Start options</button>
      <div aria-label="Configuration editing mode" className="configuration-experience-switch" role="group">
        <button aria-pressed={experience === "guided"} onClick={() => onExperienceChange("guided")} type="button"><BookOpenCheck size={13} /> Guided</button>
        <button aria-pressed={experience === "expert"} onClick={() => onExperienceChange("expert")} type="button"><Settings2 size={13} /> Expert</button>
      </div>
    </div>
  </div>;
}

const EXPERT_GUIDANCE: Record<Exclude<TradingConfigurationSection, "revisions">, { authority: string; outcome: string; subjects: string[] }> = {
  discovery: { authority: "QMD owns the broad universe, observations, candidate ranking, and point-in-time Watchlist membership.", outcome: "One visible Core Scan and reusable Watchlists selected by Strategies.", subjects: ["QMD capabilities", "Core Scan", "Watchlists and membership history"] },
  strategy: { authority: "Strategy owns trading decisions. Portfolio sizes approved intent; OMS executes it.", outcome: "A reusable Strategy Profile with explicit lifecycle rules and optional capabilities.", subjects: ["Behavior and evaluation", "Entry, add, and reentry", "Exit and capabilities"] },
  assignments: { authority: "Run Plan binds reusable objects to environments and authority.", outcome: "A runnable plan with explicit strategy, accounts, OMS, and safety.", subjects: ["Watch universe", "Profile and OMS", "Action authority"] },
  portfolio: { authority: "Portfolio is the sole authority for account allocation, capital approval, and continuous risk.", outcome: "Per-account mandates and risk limits that can approve, reduce, or reject requests.", subjects: ["Capital policy", "Account mandates", "Risk groups"] },
  oms: { authority: "OMS may use current market data to execute approved intent; it never creates trading intent or capital.", outcome: "Versioned execution and protection policies selected by Strategy or Run Plan defaults.", subjects: ["OMS defaults", "Execution policies", "Protection profiles"] },
  accounts: { authority: "IBKR remains authoritative for live and paper account state; configuration binds discovered identity and permissions.", outcome: "Mode-specific account bindings that fail closed when broker identity or capability differs.", subjects: ["Broker identity", "Mode permissions", "Safety limits"] },
};

function ExpertWorkspaceGuide({ section }: { section: Exclude<TradingConfigurationSection, "revisions"> }) {
  const guidance = EXPERT_GUIDANCE[section];
  return <section className="configuration-expert-guide">
    <div><span>Expert workspace</span><h2>{SECTION_META[section].title} contract</h2><p>{guidance.outcome}</p></div>
    <dl><div><dt>Authority boundary</dt><dd>{guidance.authority}</dd></div><div><dt>Work by subject</dt><dd>{guidance.subjects.map((subject) => <span key={subject}><Check size={12} />{subject}</span>)}</dd></div><div><dt>Release behavior</dt><dd>Changes remain in this browser session until validation and publication.</dd></div></dl>
  </section>;
}

function ConfigurationJourney({ active, draft, experience, onOmsStageChange }: {
  active: GuidedStep;
  draft: Draft | null;
  experience: ConfigurationExperience;
  onOmsStageChange: (value: OmsGuidedStage) => void;
}) {
  const steps = [
    { caption: "Decisions", key: "strategy", label: "Strategy" },
    { caption: "Runtime", key: "assignments", label: "Run Plan" },
    { caption: "Capital", key: "portfolio", label: "Portfolio" },
    { caption: "Orders", key: "execution", label: "Execute" },
    { caption: "Stops", key: "protection", label: "Protect" },
    { caption: "Bindings", key: "accounts", label: "Accounts" },
    { caption: "Release", key: "revisions", label: "Review" },
  ];
  const activeIndex = steps.findIndex((step) => step.key === active);
  return (
    <nav aria-label="Configuration flow" className="configuration-journey" data-experience={experience}>
      {steps.map((step, index) => (
        <a aria-current={active === step.key ? "step" : undefined} data-ready={index < activeIndex ? "true" : "false"} data-step={step.key} href={`#${pageForGuidedStep(step.key as GuidedStep)}`} key={step.key} onClick={() => { if (step.key === "execution" || step.key === "protection") onOmsStageChange(step.key); }}>
          <span>{index < activeIndex ? <Check size={13} /> : index + 1}</span>
          <span className="configuration-journey-copy"><strong>{step.label}</strong><small>{step.caption}</small></span>
          {index < steps.length - 1 ? <ChevronRight size={14} /> : null}
        </a>
      ))}
    </nav>
  );
}

function ConfigurationStudioHome({ approved, draft, onApplyRecommended, onCloneApproved, onStart, pending }: {
  approved: Revision | null;
  draft: Draft;
  onApplyRecommended: (value: Draft) => Promise<Draft>;
  onCloneApproved: (value: Draft) => Promise<Draft>;
  onStart: (value: ConfigurationExperience) => void;
  pending: boolean;
}) {
  const [selectedPath, setSelectedPath] = useState<"recommended" | "clone" | null>(null);
  const systemProfile = draft.strategy.profiles.find((profile) => profile.protected) ?? draft.strategy.profiles.find((profile) => profile.origin === "system");
  const systemOms = draft.oms.profiles.find((profile) => profile.origin === "system");
  const deployment = draft.assignments.deployments[0];
  const recommended = recommendedDraft(draft);
  return <section className="configuration-studio-home">
    <header>
      <div><span>Choose how to begin</span><h2>Choose your starting point</h2><p>Every option edits the same schema-v{draft.schema_version} session configuration; publication is required before runtime.</p></div>
      <div className="configuration-home-authority"><ShieldCheck size={18} /><span><strong>One authority</strong><small>Guided choices update this browser session</small></span></div>
    </header>
    <div className="configuration-start-paths">
      <button className="recommended" onClick={() => setSelectedPath("recommended")} type="button"><span className="configuration-path-icon"><Sparkles size={19} /></span><span><em>Fastest safe start</em><strong>Use recommended setup</strong><small>Apply the protected Strategy and system OMS starting points, then review account and risk decisions.</small></span><ChevronRight size={18} /></button>
      <button onClick={() => onStart("guided")} type="button"><span className="configuration-path-icon"><BookOpenCheck size={19} /></span><span><em>Best for most changes</em><strong>Guided setup</strong><small>Answer a small set of consequential questions across Strategy, Portfolio, execution, and accounts.</small></span><ChevronRight size={18} /></button>
      <button disabled={!approved} onClick={() => setSelectedPath("clone")} type="button"><span className="configuration-path-icon"><FileInput size={19} /></span><span><em>{approved ? `Release ${approved.revision}` : "No approved release"}</em><strong>Clone approved release</strong><small>Start from the immutable runtime configuration and change only what is different.</small></span><ChevronRight size={18} /></button>
      <button onClick={() => onStart("expert")} type="button"><span className="configuration-path-icon"><Settings2 size={19} /></span><span><em>Full control</em><strong>Expert editor</strong><small>Open every field, rule set, policy catalog, and generated payload in the existing editors.</small></span><ChevronRight size={18} /></button>
    </div>
    {selectedPath === "recommended" ? <div className="configuration-path-confirmation">
      <header><div><span>Recommended patch preview</span><strong>Two references change; account-specific limits remain untouched</strong></div><button aria-label="Close preview" onClick={() => setSelectedPath(null)} type="button"><X size={16} /></button></header>
      <div className="configuration-change-preview">
        <span><GitBranch size={15} /><span><small>Default Strategy Profile</small><strong>{systemProfile?.name ?? "No protected profile available"}</strong></span></span>
        <span><ShieldCheck size={15} /><span><small>{deployment?.name ?? "Compiled runtime"} OMS</small><strong>{systemOms?.name ?? "No system OMS available"}</strong></span></span>
        <span><BriefcaseBusiness size={15} /><span><small>Portfolio and accounts</small><strong>Preserved for review</strong></span></span>
      </div>
      <footer><p>This does not publish or enable Live. You will review all inherited mandate, protection, and broker bindings before approval.</p><button className="button primary" disabled={pending || !systemProfile || !systemOms || !deployment} onClick={() => void onApplyRecommended(recommended).then(() => onStart("guided"))} type="button">{pending ? "Applying…" : "Apply and review"}<ArrowRight size={15} /></button></footer>
    </div> : null}
    {selectedPath === "clone" && approved ? <div className="configuration-path-confirmation">
      <header><div><span>Copy preview</span><strong>Copy release {approved.revision} into this session</strong></div><button aria-label="Close preview" onClick={() => setSelectedPath(null)} type="button"><X size={16} /></button></header>
      <div className="configuration-clone-summary"><BadgeCheck size={17} /><p><strong>{approved.label}</strong><span>{approved.payload.strategy.profiles.length} strategies · {approved.payload.assignments.deployments.length} compiled runtimes · {approved.payload.portfolio.mandates.length} mandates · {approved.payload.accounts.bindings.length} accounts</span></p></div>
      <footer><p>The approved release remains immutable. Only this session is replaced, and publication is still required to affect new runs.</p><button className="button primary" disabled={pending} onClick={() => void onCloneApproved(cloneApprovedDraft(approved, draft)).then(() => onStart("guided"))} type="button">{pending ? "Copying…" : "Copy into session"}<ArrowRight size={15} /></button></footer>
    </div> : null}
  </section>;
}

function GuidedConfiguration({ approved, draft, label, omsStage, onChange, onContinue, onLabelChange, onOmsStageChange, onPublish, onSwitchToExpert, publishing, revisions, section }: {
  approved: Revision | null;
  draft: Draft;
  label: string;
  omsStage: OmsGuidedStage;
  onChange: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  onContinue: (step: GuidedStep) => void;
  onLabelChange: (value: string) => void;
  onOmsStageChange: (value: OmsGuidedStage) => void;
  onPublish: () => void;
  onSwitchToExpert: () => void;
  publishing: boolean;
  revisions: Revision[];
  section: TradingConfigurationSection;
}) {
  const step: GuidedStep = section === "oms" ? omsStage : section;
  const [activeStrategyProfileId, setActiveStrategyProfileId] = useState(() => window.sessionStorage.getItem("guided-strategy-profile-id") || draft.strategy.default_profile_id);
  const profile = draft.strategy.profiles.find((row) => row.profile_id === activeStrategyProfileId) ?? draft.strategy.profiles.find((row) => row.profile_id === draft.strategy.default_profile_id) ?? draft.strategy.profiles[0];
  const deployment = draft.assignments.deployments.find((row) => row.enabled) ?? draft.assignments.deployments[0];
  const mandate = draft.portfolio.mandates.find((row) => row.run_plan_id === deployment?.run_plan_id) ?? draft.portfolio.mandates[0];
  const omsProfile = draft.oms.profiles.find((row) => row.profile_id === deployment?.oms_profile_id) ?? draft.oms.profiles[0];
  const executionPolicy = draft.oms.execution_policies.find((row) => row.policy_id === omsProfile?.settings.entry_execution_policy_id) ?? draft.oms.execution_policies[0];
  const protectionProfile = draft.oms.protection_profiles.find((row) => row.profile_id === omsProfile?.settings.protection_profile_id) ?? draft.oms.protection_profiles[0];
  const account = draft.accounts.bindings.find((row) => row.account_key === mandate?.account_key) ?? draft.accounts.bindings[0];
  const steps: GuidedStep[] = ["strategy", "assignments", "portfolio", "execution", "protection", "accounts", "revisions"];
  const index = steps.indexOf(step);
  const previous = steps[index - 1];
  const next = steps[index + 1];
  const [questionIndex, setQuestionIndex] = useState(0);
  useEffect(() => setQuestionIndex(0), [step]);
  useEffect(() => {
    if (!profile || profile.profile_id === activeStrategyProfileId) return;
    setActiveStrategyProfileId(profile.profile_id);
    window.sessionStorage.setItem("guided-strategy-profile-id", profile.profile_id);
  }, [activeStrategyProfileId, profile]);

  function selectStrategyProfile(profileId: string) {
    setActiveStrategyProfileId(profileId);
    window.sessionStorage.setItem("guided-strategy-profile-id", profileId);
  }

  function replaceDeployment(nextDeployment: StrategyRunPlan) {
    onChange("assignments", { ...draft.assignments, deployments: draft.assignments.deployments.map((row) => row.run_plan_id === deployment.run_plan_id ? nextDeployment : row) });
  }
  function replaceMandate(nextMandate: Mandate) {
    onChange("portfolio", { ...draft.portfolio, mandates: draft.portfolio.mandates.map((row) => row.mandate_id === mandate.mandate_id ? nextMandate : row) });
  }
  function replaceOmsProfile(nextProfile: OmsProfile) {
    onChange("oms", { ...draft.oms, profiles: draft.oms.profiles.map((row) => row.profile_id === omsProfile.profile_id ? nextProfile : row) });
  }
  function replaceExecutionPolicy(nextPolicy: ExecutionPolicyConfig) {
    onChange("oms", { ...draft.oms, execution_policies: draft.oms.execution_policies.map((row) => row.policy_id === executionPolicy.policy_id ? nextPolicy : row) });
  }
  function replaceProtectionProfile(nextProfile: ProtectionProfileConfig) {
    onChange("oms", { ...draft.oms, protection_profiles: draft.oms.protection_profiles.map((row) => row.profile_id === protectionProfile.profile_id ? nextProfile : row) });
  }
  function replaceAccount(nextAccount: AccountBinding) {
    onChange("accounts", { bindings: draft.accounts.bindings.map((row) => row.account_key === account.account_key ? nextAccount : row) });
  }

  if (section === "revisions") return <GuidedReview approved={approved} draft={draft} label={label} onLabelChange={onLabelChange} onPublish={onPublish} onReturn={() => navigateGuidedStep("accounts", onOmsStageChange)} publishing={publishing} revisions={revisions} />;
  if (!profile || !deployment || !mandate || !omsProfile || !executionPolicy || !protectionProfile || !account) return <GuidedEmpty onSwitchToExpert={onSwitchToExpert} />;
  if (step === "strategy") return <GuidedStrategyConfiguration draft={draft} onChange={onChange} onContinue={() => onContinue("assignments")} onProfileChange={selectStrategyProfile} profile={profile} />;

  const questions: Array<ReactElement<{ label: string }>> = [];
  if (step === "assignments") questions.push(
    <GuidedQuestion description="The Strategy Profile owns trading behavior and lifecycle decisions. Choosing it does not select symbols, capital, or broker behavior." key="deployment-strategy" label="Which Strategy Profile should this Run Plan execute?" status={deployment.enabled ? "Configured" : "Needs review"}>
      <SelectField help="Select the reusable trading behavior evaluated by this Run Plan. Its entries, adds, reentries, and strategic exits remain unchanged." label="Strategy Profile" onChange={(profile_id) => replaceDeployment({ ...deployment, profile_id })} options={draft.strategy.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={deployment.profile_id} />
    </GuidedQuestion>,
    <GuidedQuestion description="The Watch Universe is the symbol-selection boundary. The Run Plan can evaluate only tickers supplied by this universe." key="deployment-universe" label="Which symbols may this Run Plan watch?" status="Configured">
      <SelectField help="Select the reusable universe that supplies eligible symbols. This changes what may be watched, not how a Strategy decides or how OMS executes." label="Watch Universe" onChange={(universe_id) => replaceDeployment({ ...deployment, universe_id })} options={draft.assignments.universes.map((row) => ({ label: row.name, value: row.universe_id }))} value={deployment.universe_id} />
    </GuidedQuestion>,
    <GuidedQuestion description="The OMS profile supplies reusable execution and protection defaults after Portfolio approves quantity. It cannot change Strategy intent or Portfolio limits." key="deployment-oms" label="Which execution profile should the Run Plan use?" status="Configured">
      <SelectField help="Select the reusable OMS profile that resolves execution policy and protection defaults for this Run Plan." label="OMS profile" onChange={(oms_profile_id) => replaceDeployment({ ...deployment, oms_profile_id })} options={draft.oms.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={deployment.oms_profile_id} />
    </GuidedQuestion>,
    <GuidedQuestion description="Select environments whose bindings will be validated before publication." key="deployment-modes" label="Where may this Run Plan run?" status={deployment.allowed_environments.length ? "Configured" : "Needs decision"}>
      <ModeSelector modes={deployment.allowed_environments} onChange={(allowed_environments) => replaceDeployment({ ...deployment, allowed_environments })} />
    </GuidedQuestion>,
  );
  if (step === "portfolio") questions.push(
    <GuidedQuestion description={draft.accounts.bindings.length === 1 ? "This is the only eligible account. Portfolio still synchronizes its cash, positions, buying power, reservations, and current broker state before approving any quantity." : "Portfolio evaluates this account's synchronized capital and positions before approving a Strategy request."} key="portfolio-account" label="Which account supplies the capital?" status={draft.accounts.bindings.length === 1 ? "Selected automatically" : "Choose an account"}>{draft.accounts.bindings.length === 1 ? <div className="guided-confirmed-choice"><BadgeCheck size={20} /><span><strong>{account.name}</strong><small>{readableLabel(account.account_class)} account · {account.modes.map(readableLabel).join(", ")}</small></span></div> : <DecisionOptions onChange={(account_key) => replaceMandate({ ...mandate, account_key })} options={draft.accounts.bindings.map((row) => ({ detail: `${readableLabel(row.account_class)} account`, label: row.name, value: row.account_key }))} value={mandate.account_key} />}</GuidedQuestion>,
    <GuidedQuestion description="This is a ceiling on otherwise available cash, not a target allocation. Portfolio may approve less when another account, position, reservation, or risk limit requires it." key="portfolio-cash" label="How much available cash may this Run Plan use?" status="Review limit">
      <NumberField help="Set the maximum fraction of otherwise available mandate cash. One hundred percent still remains subject to buying power, reservations, position limits, and planned-loss checks." label="Maximum cash fraction" maximum={1} minimum={0} onChange={(maximum_cash_fraction) => replaceMandate({ ...mandate, maximum_cash_fraction })} step={0.01} unit="fraction" value={mandate.maximum_cash_fraction} />
    </GuidedQuestion>,
    <GuidedQuestion description="Portfolio sums the loss implied by every active protective stop. A new order is reduced or rejected when its planned loss would cross this account-level ceiling." key="portfolio-risk" label="What is the maximum combined planned loss?" status="Review limit">
      <NumberField help="Set the maximum fraction of account equity that may be lost if every active protective stop executes at its planned price." label="Maximum planned loss" maximum={1} minimum={0} onChange={(maximum_planned_risk_fraction) => replaceMandate({ ...mandate, maximum_planned_risk_fraction })} step={0.001} unit="fraction" value={mandate.maximum_planned_risk_fraction} />
    </GuidedQuestion>,
    <GuidedQuestion description="This limits simultaneous positions for this Run Plan. Pending reservations and already open positions are counted before another entry is approved." key="portfolio-positions" label="How many positions may be open at once?" status="Review limit">
      <NumberField help="Set the maximum number of simultaneous positions attributable to this Run Plan on the selected account." label="Maximum open positions" minimum={1} onChange={(maximum_positions) => replaceMandate({ ...mandate, maximum_positions })} step={1} unit="positions" value={mandate.maximum_positions} />
    </GuidedQuestion>,
    <GuidedQuestion description="Caps exposure-increasing Run Plan actions for this account. Exits may remain automatic because they reduce exposure." key="portfolio-autonomy" label="Maximum action authority" status={mandate.maximum_action_authority === "automatic" ? "Needs review" : "Configured"}><DecisionOptions onChange={(maximum_action_authority) => replaceMandate({ ...mandate, maximum_action_authority: maximum_action_authority as Mandate["maximum_action_authority"] })} options={[{ detail: "Operator initiates entry, add, and reentry actions.", label: "Manual", value: "manual" }, { detail: "Operator confirms each entry, add, and reentry proposal.", label: "Confirm", recommended: true, value: "confirm" }, { detail: "Exposure may increase automatically while every guardrail passes.", label: "Automatic", value: "automatic" }]} value={mandate.maximum_action_authority} /></GuidedQuestion>,
  );
  if (step === "execution") questions.push(
    <GuidedQuestion description="This controls how quickly OMS follows the market while staying inside the policy's approved price and time envelope. It never changes Portfolio's approved quantity." key="execution-pace" label="How aggressively should entries seek a fill?" status="Choose a pace"><DecisionOptions onChange={(entry_execution_policy_id) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, entry_execution_policy_id } })} options={draft.oms.execution_policies.filter((row) => ["adaptive_patient", "adaptive_regular", "adaptive_urgent", executionPolicy.policy_id].includes(row.policy_id)).map((row) => ({ detail: row.policy_id === "adaptive_patient" ? "Wait longer for a favorable price before moving." : row.policy_id === "adaptive_urgent" ? "Follow the market quickly when completing the entry matters most." : row.policy_id === "adaptive_regular" ? "Balance price quality with a timely fill." : `Keep the current ${readableLabel(row.name)} policy.`, label: row.policy_id === "adaptive_patient" ? "Patient" : row.policy_id === "adaptive_urgent" ? "Fast" : row.policy_id === "adaptive_regular" ? "Balanced" : readableLabel(row.name), recommended: row.policy_id === "adaptive_regular", value: row.policy_id }))} value={omsProfile.settings.entry_execution_policy_id} /></GuidedQuestion>,
    <GuidedQuestion description="A partial fill means the broker completed only part of the approved quantity. OMS reconciles the broker-confirmed fill first, then applies this choice only to the true remainder." key="execution-partial" label="What should happen to an unfilled remainder?" status="Choose a response"><DecisionOptions onChange={(partial_fill_policy) => replaceExecutionPolicy({ ...executionPolicy, partial_fill_policy: partial_fill_policy as ExecutionPolicyConfig["partial_fill_policy"] })} options={[{ detail: "Keep working only the confirmed remainder with the latest price allowed by policy.", label: "Finish the approved quantity", recommended: true, value: "complete_remainder" }, { detail: "Keep the shares already filled and stop trying to fill the rest.", label: "Accept the partial fill", value: "accept_partial" }, { detail: "Cancel the remainder immediately while retaining any confirmed shares.", label: "Cancel the remainder", value: "cancel_remainder" }]} value={executionPolicy.partial_fill_policy} /></GuidedQuestion>,
    <GuidedQuestion description="Adaptive repricing needs a current bid and ask. Choose the authoritative feed for this execution policy; stale or unavailable quotes cause the policy to fail closed." key="execution-quotes" label="Which price feed should adaptive orders use?" status="Choose a source"><DecisionOptions onChange={(quote_source) => replaceExecutionPolicy({ ...executionPolicy, quote_source: quote_source as ExecutionPolicyConfig["quote_source"] })} options={[{ detail: "Use shared normalized market data for live and replay-capable logic.", label: "QMD", recommended: true, value: "qmd" }, { detail: "Use quotes from the active IBKR gateway session.", label: "IBKR", value: "ibkr" }, { detail: "Use deterministic quotes for repeatable historical execution.", label: "Simulated", value: "simulated" }]} value={executionPolicy.quote_source} /></GuidedQuestion>,
  );
  if (step === "protection") questions.push(
    <GuidedQuestion description={draft.oms.protection_profiles.length === 1 ? "This is the only available protection design. Its broker-held stops are placed and reconciled independently of normal Strategy exits." : "Protection remains active independently of normal Strategy exits and cannot be weakened by another authority."} key="protection-profile" label="How is a new position protected?" status={draft.oms.protection_profiles.length === 1 ? "Selected automatically" : "Choose protection"}>{draft.oms.protection_profiles.length === 1 ? <div className="guided-confirmed-choice"><ShieldCheck size={20} /><span><strong>{protectionProfile.name}</strong><small>{protectionProfile.slices.length} stop slice{protectionProfile.slices.length === 1 ? "" : "s"}; catastrophic backstop {protectionProfile.mandatory_catastrophic_backstop ? "required" : "not required"}</small></span></div> : <DecisionOptions onChange={(protection_profile_id) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, protection_profile_id } })} options={draft.oms.protection_profiles.map((row) => ({ detail: `${row.slices.length} stop slice${row.slices.length === 1 ? "" : "s"}; catastrophic backstop ${row.mandatory_catastrophic_backstop ? "required" : "not required"}.`, label: row.name, recommended: row.mandatory_catastrophic_backstop, value: row.profile_id }))} value={omsProfile.settings.protection_profile_id} />}</GuidedQuestion>,
    <GuidedQuestion description={`After an intentional profit reduction, this transition changes only the protection for the remaining shares. The ${protectionProfile.slices.length} configured stop slice${protectionProfile.slices.length === 1 ? " remains" : "s remain"} broker-authoritative.`} key="protection-profit" label="After pocketing profit, what protects the remainder?" status="Configured"><DecisionOptions onChange={(profit_pocket_transition) => replaceProtectionProfile({ ...protectionProfile, profit_pocket_transition })} options={[{ detail: "Raise the remaining loss floor to entry plus the configured buffer.", label: "Move to breakeven", recommended: true, value: "move_to_breakeven" }, { detail: "Activate the configured swing-based trailing protection.", label: "Start swing trail", value: "start_swing_trail" }, { detail: "Retain the existing broker-held protection contract.", label: "Keep existing protection", value: "keep_existing" }]} value={protectionProfile.profit_pocket_transition} /></GuidedQuestion>,
  );
  if (step === "accounts") questions.push(
    <GuidedQuestion description="This binding connects the setup to simulated state or an externally discovered IBKR account. Runtime synchronizes cash, positions, buying power, and orders before Portfolio acts." key="account-selection" label="Which account will this configuration use?" status="Account selected"><div className="guided-confirmed-choice"><BadgeCheck size={20} /><span><strong>{account.name}</strong><small>{readableLabel(account.account_class)} · Portfolio policy {account.portfolio_policy_id}</small></span></div></GuidedQuestion>,
    <GuidedQuestion description="Paper and Live require an exact broker account and session match. Replay and Backtest use deterministic simulated account state instead." key="account-modes" label="Which modes may bind this account?" status={account.modes.length ? "Configured" : "Needs decision"}><ModeSelector modes={account.modes} onChange={(modes) => replaceAccount({ ...account, modes })} /></GuidedQuestion>,
    ...(account.modes.some((mode) => mode === "paper" || mode === "live") ? [<GuidedQuestion description="The broker account ID is resolved only by the local runtime environment. Publication and broker preflight fail closed when the resolved identity differs from IBKR discovery." key="account-broker" label="Confirm the broker account and gateway session" status={(account.source_account_env || account.source_account_id.trim()) && account.session_key.trim() ? "Needs broker verification" : "Invalid"}><div className="guided-form-grid">{account.source_account_env ? <div className="configuration-fixed-value"><span>IBKR account ID source</span><strong>{account.source_account_env}</strong><small>Resolved only in the local runtime; the account ID is not stored in configuration.</small></div> : <TextField help="Enter the exact IBKR account identifier returned by the active broker session. It is an identity, not a display label." label="IBKR account ID" onChange={(source_account_id) => replaceAccount({ ...account, source_account_id })} value={account.source_account_id} />}<TextField help="Enter the configured gateway session identity that owns this account connection." label="Session key" onChange={(session_key) => replaceAccount({ ...account, session_key })} value={account.session_key} /></div></GuidedQuestion>] : []),
  );
  const questionCount = questions.length;
  const safeQuestionIndex = Math.min(questionIndex, Math.max(questionCount - 1, 0));
  const atFirstQuestion = safeQuestionIndex === 0;
  const atLastQuestion = safeQuestionIndex === questionCount - 1;
  const movePrevious = () => atFirstQuestion ? previous && navigateGuidedStep(previous, onOmsStageChange) : setQuestionIndex(safeQuestionIndex - 1);
  const moveNext = () => atLastQuestion ? next && onContinue(next) : setQuestionIndex(safeQuestionIndex + 1);

  return <div className="guided-configuration-shell" data-guided-step={step}>
    <div className="configuration-guided-step-navigation">
      <button className="button compact configuration-guided-direction" disabled={atFirstQuestion && !previous} onClick={movePrevious} type="button">&lt; Previous</button>
      <nav aria-label={`${readableLabel(step)} questions`} className="configuration-guided-question-tabs" style={{ gridTemplateColumns: `repeat(${questionCount}, minmax(0, 1fr))` }}>
        {questions.map((question, index) => <button aria-current={index === safeQuestionIndex ? "step" : undefined} key={question.key ?? index} onClick={() => setQuestionIndex(index)} title={question.props.label} type="button"><span>{index + 1}</span><strong>{question.props.label}</strong></button>)}
      </nav>
      <button className="button compact primary configuration-guided-direction" disabled={atLastQuestion && !next} onClick={moveNext} type="button">Next &gt;</button>
    </div>
    <main className="guided-question-surface">
      <div className="guided-question-list">{questions[safeQuestionIndex]}</div>
    </main>
  </div>;
}

type GuidedStrategyQuestionDefinition = {
  content: ReactNode;
  description: string;
  guide: string;
  id: string;
  section: string;
  title: string;
};

function GuidedStrategyConfiguration({ draft, onChange, onContinue, onProfileChange, profile }: {
  draft: Draft;
  onChange: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  onContinue: () => void;
  onProfileChange: (profileId: string) => void;
  profile: StrategyProfile;
}) {
  const [questionIndex, setQuestionIndex] = useState(0);
  const [startMode, setStartMode] = useState<"create" | "clone" | null>(null);
  const [cloneSourceId, setCloneSourceId] = useState("");
  const [profileName, setProfileName] = useState("");
  const definition = draft.strategy.definitions.find((row) => row.strategy_id === profile.definition_id);
  const supportedSides = definition?.supported_sides?.length ? definition.supported_sides : ["long" as const];
  const initial = profile.lifecycle.initial_entry;
  const reentry = profile.lifecycle.reentry;
  const advanced = flattenPrimitives(profile.parameters).filter((row) => !LEGACY_ENTRY_LOGIC_PATHS.has(row.path) && isDirectlyEditableStrategyParameter(row.path, row.value));

  function replaceProfile(nextProfile: StrategyProfile) {
    onChange("strategy", {
      ...draft.strategy,
      profiles: draft.strategy.profiles.map((row) => row.profile_id === profile.profile_id ? nextProfile : row),
    });
  }
  function createNewProfile() {
    const nextProfile = blankStrategyProfile(profile, draft);
    nextProfile.name = profileName.trim() || "Untitled Strategy";
    onChange("strategy", { ...draft.strategy, profiles: [...draft.strategy.profiles, nextProfile] });
    onProfileChange(nextProfile.profile_id);
    setQuestionIndex(1);
  }
  function cloneExistingProfile() {
    const source = draft.strategy.profiles.find((row) => row.profile_id === cloneSourceId) ?? profile;
    const nextProfile = cloneStrategyProfile(source, draft.strategy.profiles, profileName);
    onChange("strategy", { ...draft.strategy, profiles: [...draft.strategy.profiles, nextProfile] });
    onProfileChange(nextProfile.profile_id);
    setQuestionIndex(1);
  }
  function chooseStartMode(mode: "create" | "clone") {
    setStartMode(mode);
    if (mode === "create") setProfileName(uniqueProfileName("Untitled Strategy", draft.strategy.profiles));
    else {
      setCloneSourceId("");
      setProfileName("");
    }
  }
  function chooseCloneSource(profileId: string) {
    const source = draft.strategy.profiles.find((row) => row.profile_id === profileId);
    if (!source) return;
    setCloneSourceId(profileId);
    setProfileName(uniqueProfileName(`${source.name} copy`, draft.strategy.profiles));
  }
  function replaceInitial(nextInitial: StrategyLifecycle["initial_entry"]) {
    replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, initial_entry: nextInitial } });
  }
  function replaceReentry(nextReentry: StrategyLifecycle["reentry"]) {
    replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, reentry: nextReentry } });
  }
  function replacePhaseMode(phase: keyof StrategyLifecycle["phase_modes"], mode: StrategyPhaseMode) {
    replaceProfile({
      ...profile,
      lifecycle: {
        ...profile.lifecycle,
        phase_modes: { ...profile.lifecycle.phase_modes, [phase]: mode },
        ...(phase === "reentry" ? { reentry: { ...reentry, enabled: mode === "automatic" } } : {}),
      },
    });
  }
  function replaceAddStep(stepId: string, nextStep: AddStep) {
    replaceInitial({ ...initial, add_steps: initial.add_steps.map((row) => row.step_id === stepId ? nextStep : row) });
  }
  function addAddStep() {
    const source = draft.strategy.input_catalog[0];
    if (!source) return;
    const stepId = uniqueId("position-add", initial.add_steps.map((row) => row.step_id));
    replaceInitial({ ...initial, add_steps: [{
      capital_request: { allow_replacement: false, mode: "mandate_fraction", value: 0.1 },
      enabled: true,
      maximum_uses: 1,
      name: "New position add",
      order_intent: { deadline_ms: 750, execution_policy: "adaptive_regular", partial_fill_policy: "complete_remainder", protection_profile: draft.oms.protection_profiles[0]?.profile_id ?? "" },
      rules: { groups: [{ conditions: [{ comparator: source.value_type === "boolean" ? "is_true" : "greater_or_equal", condition_id: `${stepId}-condition`, enabled: true, left_source_id: source.source_id, left_timeframe: source.timeframes[0], right_source_id: "", right_timeframe: "", value: source.value_type === "boolean" ? null : 0 }], enabled: true, group_id: `${stepId}-rule`, label: "Add trigger", operator: "all", required_score: 1 }], operator: "any" },
      step_id: stepId,
    }, ...initial.add_steps] });
  }
  function replaceExit(ruleSetId: string, nextRuleSet: ExitRuleSet) {
    replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, exit: { rule_sets: profile.lifecycle.exit.rule_sets.map((row) => row.rule_set_id === ruleSetId ? nextRuleSet : row) } } });
  }
  function addExit() {
    const source = draft.strategy.input_catalog[0];
    if (!source) return;
    const ruleSetId = uniqueId("new-exit-rule", profile.lifecycle.exit.rule_sets.map((row) => row.rule_set_id));
    const next: ExitRuleSet = {
      action: "close", enabled: true, name: "New strategic exit", position_fraction: 1,
      order_intent: { deadline_ms: 750, execution_policy: "adaptive_urgent", partial_fill_policy: "complete_remainder", protection_profile: draft.oms.protection_profiles[0]?.profile_id ?? "" },
      rules: { groups: [{ conditions: [{ comparator: source.value_type === "boolean" ? "is_true" : "greater_or_equal", condition_id: `${ruleSetId}-condition`, enabled: true, left_source_id: source.source_id, left_timeframe: source.timeframes[0], right_source_id: "", right_timeframe: "", value: source.value_type === "boolean" ? null : 0 }], enabled: true, group_id: `${ruleSetId}-group`, label: "Exit evidence", operator: "all", required_score: 1 }], operator: "all" },
      rule_set_id: ruleSetId, summary: "Describe when this exit becomes valid.", timing: { active_after_ms: 0, expires_after_ms: 0 },
    };
    replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, exit: { rule_sets: [next, ...profile.lifecycle.exit.rule_sets] } } });
  }

  let questions: GuidedStrategyQuestionDefinition[] = [
    {
      id: "profile", section: "Behavior", title: "How do you want to build this strategy?",
      description: "Start with a blank strategy and answer every decision, or copy an existing strategy after reviewing exactly what it contains.",
      guide: "A new strategy begins with no active entry rules, position adds, reentry, strategic exits, or capabilities. A clone is an independent editable copy; the source remains unchanged.",
      content: <StrategyStartWorkflow cloneSourceId={cloneSourceId} mode={startMode} name={profileName} onClone={cloneExistingProfile} onCloneSourceChange={chooseCloneSource} onCreate={createNewProfile} onModeChange={chooseStartMode} onNameChange={setProfileName} profiles={draft.strategy.profiles} section={draft.strategy} />,
    },
    {
      id: "identity", section: "Behavior", title: "How should this plan be identified?",
      description: "Set the operator-facing name and summarize the trading behavior this profile defines.",
      guide: "The name and description appear in selection, reviews, journals, and runtime evidence. They do not change strategy logic.",
      content: <div className="guided-form-grid"><TextField help="Use a concise name that distinguishes this Strategy in configuration and runtime views." label="Strategy name" onChange={(name) => replaceProfile({ ...profile, name })} value={profile.name} /><TextField help="State the setup, intended market behavior, and purpose." label="Strategy description" onChange={(description) => replaceProfile({ ...profile, description })} value={profile.description} /></div>,
    },
    {
      id: "availability", section: "Behavior", title: "Should this strategy remain available to new runs?",
      description: "Availability controls whether the backend may compile this Strategy for a new run. Turning it off preserves the complete configuration and historical references.",
      guide: "Disable a profile when it must remain auditable but unavailable to new runtime configurations.",
      content: <BooleanField help="Allow new runtime configurations to use this Strategy." label="Available for use" onChange={(enabled) => replaceProfile({ ...profile, enabled })} value={profile.enabled} />,
    },
    {
      id: "side", section: "Behavior", title: "Should this plan trade long or short?",
      description: "Direction determines how a campaign opens, adds, reduces, and closes exposure.",
      guide: "Long buys first and sells later. Short sells borrowed shares first and buys them back; current broker shortability is still required.",
      content: <DecisionOptions onChange={(side) => replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, trading_behavior: { ...profile.lifecycle.trading_behavior, side: side as "long" | "short" } } })} options={supportedSides.map((side) => ({ detail: side === "long" ? "Buy to open; sell to reduce or close." : "Short-sell to open; buy to cover.", label: readableLabel(side), recommended: side === "long", value: side }))} value={profile.lifecycle.trading_behavior.side} />,
    },
    {
      id: "sessions", section: "Behavior", title: "When may a new entry be evaluated?",
      description: "Select every market session in which initial entries, adds, and reentries may become eligible.",
      guide: "Protective exits remain active whenever exposure exists. OMS derives compatible broker session instructions from this choice.",
      content: <ModeChoices onChange={(eligible_sessions) => replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, trading_behavior: { ...profile.lifecycle.trading_behavior, eligible_sessions } } })} options={["premarket", "regular", "after_hours"]} values={profile.lifecycle.trading_behavior.eligible_sessions} />,
    },
    {
      id: "initial-mode", section: "Initial entry", title: "Should Strategy automate initial entry?",
      description: "Choose whether Strategy evaluates the first-entry configuration and may emit entry intent.",
      guide: "Manual preserves every initial-entry answer but skips those questions and emits no first-entry intent.",
      content: <DecisionOptions onChange={(value) => replacePhaseMode("initial_entry", value as StrategyPhaseMode)} options={[{ detail: "Evaluate the saved initial-entry rules and request settings.", label: "Automatic", recommended: true, value: "automatic" }, { detail: "Skip initial-entry evaluation while preserving its configuration.", label: "Manual", value: "manual" }]} value={profile.lifecycle.phase_modes.initial_entry} />,
    },
    {
      id: "initial-capital", section: "Initial entry", title: "How much capital should the first entry request?",
      description: "Choose only the sizing method and requested amount. Portfolio later converts this broker-neutral request into a safe account-specific quantity.",
      guide: "This is a request, not an entitlement. Cash, buying power, position limits, reservations, and planned loss may reduce or reject it.",
      content: <GuidedCapitalRequestFields onChange={(capital_request) => replaceInitial({ ...initial, capital_request })} segment="amount" value={initial.capital_request} />,
    },
    {
      id: "initial-capital-priority", section: "Initial entry", title: "How should this request compete for constrained capital?",
      description: "Priority orders competing requests. Replacement permission decides whether Portfolio may propose releasing a weaker position when policy permits it.",
      guide: "Neither setting bypasses risk. Portfolio still rejects any request that violates the selected account mandate or protection requirements.",
      content: <GuidedCapitalRequestFields onChange={(capital_request) => replaceInitial({ ...initial, capital_request })} segment="priority" value={initial.capital_request} />,
    },
    {
      id: "initial-execution", section: "Initial entry", title: "How should OMS work the approved first-entry order?",
      description: "Choose the execution behavior. OMS may adapt price only inside that policy and cannot exceed Portfolio's approved quantity.",
      guide: "Execution policy controls pace and price behavior. OMS applies its tested, versioned terminal timing independently of the strategy author's choice.",
      content: <GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceInitial({ ...initial, order_intent })} segment="execution" value={initial.order_intent} />,
    },
    {
      id: "initial-partial-fill", section: "Initial entry", title: "How should OMS handle a partial first fill?",
      description: "The broker may fill only part of the approved quantity. This choice applies only to the reconciled remainder and never recreates already filled shares.",
      guide: "OMS reads broker-confirmed cumulative fills before any cancel or replace action, preventing an over-order during fast partial-fill events.",
      content: <GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceInitial({ ...initial, order_intent })} segment="partial-fill" value={initial.order_intent} />,
    },
    {
      id: "initial-protection", section: "Initial entry", title: "Which protection follows the first fill?",
      description: "Protection is a separate order intent applied after confirmed fills. It remains independent of normal strategic exits.",
      guide: "Choose a published protection profile that defines stop slices, catastrophic backstop behavior, and any later trailing transition.",
      content: <GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceInitial({ ...initial, order_intent })} segment="protection" value={initial.order_intent} />,
    },
    ...(["opportunity", "confirmation", "blockers"] as const).map((stage) => ({
      id: `initial-${stage}`, section: "Initial entry", title: stage === "opportunity" ? "What identifies a possible first entry?" : stage === "confirmation" ? "What must confirm the first entry?" : "What must prevent the first entry?",
      description: stage === "opportunity" ? "Opportunity groups find a candidate setup." : stage === "confirmation" ? "Confirmation groups must validate that the opportunity is actionable." : "A passing blocker prevents entry even when opportunity and confirmation pass.",
      guide: "The complete sentence is: enter when Opportunity passes, Confirmation passes, and Blockers do not pass. Configure each group's ALL/ANY logic explicitly.",
      content: <RuleStageEditor catalog={draft.strategy.input_catalog} intent="entry" label={`Initial entry ${readableLabel(stage)}`} onChange={(value) => replaceInitial({ ...initial, [stage]: value })} stage={initial[stage]} />,
    })),
    {
      id: "manage-mode", section: "Position adds", title: "Should Strategy automate position management?",
      description: "Choose whether Strategy evaluates adds, trailing behavior, and optional management capabilities.",
      guide: "Manual preserves the position-management configuration but prevents Strategy from changing the position. Existing protection remains active.",
      content: <DecisionOptions onChange={(value) => replacePhaseMode("manage", value as StrategyPhaseMode)} options={[{ detail: "Evaluate configured management actions while a position is open.", label: "Automatic", recommended: true, value: "automatic" }, { detail: "Skip position-management actions while preserving their settings.", label: "Manual", value: "manual" }]} value={profile.lifecycle.phase_modes.manage} />,
    },
    {
      id: "adds-overview", section: "Position adds", title: "Which position-add actions are available?",
      description: "Each action may request more capital only while a position is already open.",
      guide: "Disabled actions remain saved. Maximum uses is a fill count; a rejected or unfilled request must not consume a use.",
      content: <div className="guided-action-list"><button className="button compact" onClick={addAddStep} type="button"><Plus size={14} /> Add another action</button>{initial.add_steps.map((step) => <article key={step.step_id}><div><TextField help="Operator-facing action name." label="Action name" onChange={(name) => replaceAddStep(step.step_id, { ...step, name })} value={step.name} /><NumberField help="Maximum confirmed fills during one campaign." label="Maximum uses" minimum={1} onChange={(maximum_uses) => replaceAddStep(step.step_id, { ...step, maximum_uses })} step={1} unit="fills" value={step.maximum_uses} /></div><BooleanField help="Allow this action to request an add." label="Enabled" onChange={(enabled) => replaceAddStep(step.step_id, { ...step, enabled })} value={step.enabled} /><button className="button compact danger" onClick={() => replaceInitial({ ...initial, add_steps: initial.add_steps.filter((row) => row.step_id !== step.step_id) })} type="button"><Trash2 size={14} /> Remove</button></article>)}</div>,
    },
    ...initial.add_steps.map((step) => ({
      id: `add-${step.step_id}`, section: "Position adds", title: `How should “${step.name}” work?`,
      description: "This form is limited to one position-add action: its trigger, capital request, execution response, and protection for newly filled shares.",
      guide: "An add increases an open position; it is not a reentry. Portfolio re-sizes from current account risk, and OMS cannot exceed the newly approved quantity.",
      content: <GuidedActionForm sections={[
        { content: <RuleStageEditor catalog={draft.strategy.input_catalog} intent="add" label={`${step.name} trigger`} onChange={(rules) => replaceAddStep(step.step_id, { ...step, rules })} stage={step.rules} />, description: "Evidence required while the position is already open.", title: "Trigger" },
        { content: <><GuidedCapitalRequestFields onChange={(capital_request) => replaceAddStep(step.step_id, { ...step, capital_request })} segment="amount" value={step.capital_request} /><GuidedCapitalRequestFields onChange={(capital_request) => replaceAddStep(step.step_id, { ...step, capital_request })} segment="priority" value={step.capital_request} /></>, description: "Broker-neutral size and capital-contention behavior.", title: "Capital request" },
        { content: <><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceAddStep(step.step_id, { ...step, order_intent })} segment="execution" value={step.order_intent} /><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceAddStep(step.step_id, { ...step, order_intent })} segment="partial-fill" value={step.order_intent} /></>, description: "How OMS works and completes only the approved add quantity.", title: "Execution" },
        { content: <GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceAddStep(step.step_id, { ...step, order_intent })} segment="protection" value={step.order_intent} />, description: "Broker-held protection applied to confirmed add fills.", title: "Protection" },
      ]} />,
    })),
    {
      id: "reentry-policy", section: "Reentry", title: "May the campaign enter again after a complete exit?",
      description: "Choose whether Strategy evaluates reentry after a confirmed complete exit.",
      guide: "Manual preserves every reentry answer but skips those questions and emits no reentry intent.",
      content: <DecisionOptions onChange={(value) => replacePhaseMode("reentry", value as StrategyPhaseMode)} options={[{ detail: "Evaluate the saved reentry rules after the campaign becomes flat.", label: "Automatic", recommended: true, value: "automatic" }, { detail: "Skip reentry evaluation while preserving its configuration.", label: "Manual", value: "manual" }]} value={profile.lifecycle.phase_modes.reentry} />,
    },
    ...(profile.lifecycle.phase_modes.reentry === "automatic" ? [{
      id: "reentry-guardrails", section: "Reentry", title: "What limits reentry timing and frequency?", description: "These campaign-level guardrails control evidence freshness, minimum waiting time, and the maximum number of successful reentries.", guide: "A rejected or unfilled request does not consume an attempt. Fresh confirmation prevents evidence from the prior entry from being silently reused.", content: <div className="guided-form-grid"><BooleanField help="Require confirmation evidence with a timestamp newer than the previous confirmed entry." label="Require fresh confirmation" onChange={(require_new_confirmation) => replaceReentry({ ...reentry, require_new_confirmation })} value={reentry.require_new_confirmation} /><NumberField help="Set the minimum elapsed time after a confirmed full exit before another reentry may become eligible." label="Cooldown" minimum={0} onChange={(cooldown_ms) => replaceReentry({ ...reentry, cooldown_ms })} step={100} unit="ms" value={reentry.cooldown_ms} /><NumberField help="Set the maximum number of confirmed reentry fills allowed during one ticker campaign." label="Maximum attempts" minimum={0} onChange={(maximum_attempts) => replaceReentry({ ...reentry, maximum_attempts })} step={1} unit="entries" value={reentry.maximum_attempts} /></div>,
    }, {
      id: "reentry-capital", section: "Reentry", title: "How much capital should a reentry request?", description: "Choose only the reentry sizing method and amount. Reentry owns a request independent from the first entry.", guide: "Portfolio recalculates capacity from current synchronized account state; the previous position size is never reused automatically.", content: <GuidedCapitalRequestFields onChange={(capital_request) => replaceReentry({ ...reentry, capital_request })} segment="amount" value={reentry.capital_request} />,
    }, {
      id: "reentry-replacement", section: "Reentry", title: "May reentry propose a replacement?", description: "Portfolio may propose releasing a weaker position when policy permits.", guide: "Replacement never bypasses account risk or protection limits.", content: <GuidedCapitalRequestFields onChange={(capital_request) => replaceReentry({ ...reentry, capital_request })} segment="priority" value={reentry.capital_request} />,
    }, {
      id: "reentry-execution", section: "Reentry", title: "How should OMS execute an approved reentry?", description: "Choose the execution policy for the new approved quantity.", guide: "A reentry may use a different pace from the first entry, but OMS still stays inside the same broker, quote-freshness, and tested terminal-timing safety contracts.", content: <GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceReentry({ ...reentry, order_intent })} segment="execution" value={reentry.order_intent} />,
    }, {
      id: "reentry-partial", section: "Reentry", title: "What should happen after a partial reentry fill?", description: "Choose the response for only the broker-confirmed unfilled remainder.", guide: "OMS reconciles fills before acting, preventing a partial reentry from duplicating exposure during a fast replace cycle.", content: <GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceReentry({ ...reentry, order_intent })} segment="partial-fill" value={reentry.order_intent} />,
    }, {
      id: "reentry-protection", section: "Reentry", title: "How should a confirmed reentry be protected?", description: "Select the broker-held protection profile attached to reentry fills.", guide: "This may differ from the first entry when reentry requires a different stop structure or trailing transition.", content: <GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceReentry({ ...reentry, order_intent })} segment="protection" value={reentry.order_intent} />,
    }, ...(["opportunity", "confirmation", "blockers"] as const).map((stage) => ({
      id: `reentry-${stage}`, section: "Reentry", title: stage === "opportunity" ? "What identifies a possible reentry?" : stage === "confirmation" ? "What must confirm a reentry?" : "What must prevent a reentry?", description: "Reentry owns independent decision rules; it does not silently reuse the first-entry rule set.", guide: "Importing or copying initial rules creates editable copies. Fresh-evidence and cooldown gates still apply before evaluation.", content: <RuleStageEditor catalog={draft.strategy.input_catalog} intent="reentry" label={`Reentry ${readableLabel(stage)}`} onChange={(value) => replaceReentry({ ...reentry, rules: { ...reentry.rules, [stage]: value } })} stage={reentry.rules[stage]} />,
    }))] : []),
    {
      id: "exit-mode", section: "Strategic exits", title: "Should Strategy automate strategic exits?",
      description: "Choose whether Strategy evaluates reduction and close rules for an open position.",
      guide: "Manual preserves strategic-exit settings. Broker-held protection, emergency exits, and account safety remain active.",
      content: <DecisionOptions onChange={(value) => replacePhaseMode("exit", value as StrategyPhaseMode)} options={[{ detail: "Evaluate strategic exit routes while a position is open.", label: "Automatic", recommended: true, value: "automatic" }, { detail: "Skip strategic exits while retaining mandatory protection.", label: "Manual", value: "manual" }]} value={profile.lifecycle.phase_modes.exit} />,
    },
    {
      id: "exit-overview", section: "Strategic exits", title: "Which strategic exit routes are available?", description: "Enable, name, or add the rule sets that can reduce or close a position.", guide: "Broker-held protection is independent and remains active even when every strategic exit is disabled or delayed.", content: <div className="guided-action-list"><button className="button compact" onClick={addExit} type="button"><Plus size={14} /> Add exit route</button>{profile.lifecycle.exit.rule_sets.map((ruleSet) => <article key={ruleSet.rule_set_id}><div><TextField help="Operator-facing route name." label="Exit name" onChange={(name) => replaceExit(ruleSet.rule_set_id, { ...ruleSet, name })} value={ruleSet.name} /><TextField help="State the market condition this route handles." label="Purpose" onChange={(summary) => replaceExit(ruleSet.rule_set_id, { ...ruleSet, summary })} value={ruleSet.summary} /></div><BooleanField help="Evaluate this route while a position is open." label="Enabled" onChange={(enabled) => replaceExit(ruleSet.rule_set_id, { ...ruleSet, enabled })} value={ruleSet.enabled} /><button className="button compact danger" disabled={profile.lifecycle.exit.rule_sets.length <= 1} onClick={() => replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, exit: { rule_sets: profile.lifecycle.exit.rule_sets.filter((row) => row.rule_set_id !== ruleSet.rule_set_id) } } })} type="button"><Trash2 size={14} /> Remove</button></article>)}</div>,
    },
    ...profile.lifecycle.exit.rule_sets.map((ruleSet) => ({
      id: `exit-${ruleSet.rule_set_id}`, section: "Strategic exits", title: `How should “${ruleSet.name}” act?`, description: "This form is limited to one strategic exit route: its validity window, causal evidence, position action, execution response, and protection for any remaining shares.", guide: "Strategy requests a reduction or close; Portfolio and OMS reconcile the broker-authoritative position. Protective stops are never silently cancelled.", content: <GuidedActionForm sections={[
        { content: <GuidedExitTimingFields onChange={(next) => replaceExit(ruleSet.rule_set_id, next)} value={ruleSet} />, description: "When this route may evaluate after entry.", title: "Validity window" },
        { content: <RuleStageEditor catalog={draft.strategy.input_catalog} intent="exit" label={`${ruleSet.name} evidence`} onChange={(rules) => replaceExit(ruleSet.rule_set_id, { ...ruleSet, rules })} stage={ruleSet.rules} />, description: "Causal evidence and Boolean grouping for this route.", title: "Exit evidence" },
        { content: <GuidedExitActionFields onChange={(next) => replaceExit(ruleSet.rule_set_id, next)} value={ruleSet} />, description: "How much of the reconciled position Strategy requests to release.", title: "Position action" },
        { content: <><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceExit(ruleSet.rule_set_id, { ...ruleSet, order_intent })} segment="execution" value={ruleSet.order_intent} /><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceExit(ruleSet.rule_set_id, { ...ruleSet, order_intent })} segment="partial-fill" value={ruleSet.order_intent} /></>, description: "How OMS works the approved exit and reconciles partial fills.", title: "Execution" },
        { content: <GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceExit(ruleSet.rule_set_id, { ...ruleSet, order_intent })} segment="protection" value={ruleSet.order_intent} />, description: "Broker-held protection retained for any shares that remain.", title: "Remaining protection" },
      ]} />,
    })),
    ...draft.strategy.capability_catalog.map((capability) => {
      const binding = profile.capabilities.find((row) => row.capability_id === capability.capability_id);
      return {
        id: `capability-${capability.capability_id}`, section: "Capabilities", title: `Configure “${capability.name}”`, description: capability.summary, guide: capability.order_entry_action ? "This form belongs only to this deliberate Order Entry action. Strategy emits semantic intent; Portfolio and OMS retain sizing and execution authority." : "Every field on this form belongs only to this capability and does not replace the normal lifecycle, Portfolio, OMS, or protection contracts.", content: binding ? <GuidedCapabilityFields binding={binding} definition={capability} onChange={(next) => replaceProfile(updateCapability(profile, binding.capability_id, next))} /> : <p>The capability cannot be configured until its binding is added in Expert mode.</p>,
      };
    }),
  ];

  const advancedGroups = new Map<string, typeof advanced>();
  advanced.forEach((item) => {
    const parts = item.path.split(".");
    const group = parts[0] === "protection" ? parts.slice(0, 2).join(".") : parts[0];
    advancedGroups.set(group, [...(advancedGroups.get(group) ?? []), item]);
  });
  advancedGroups.forEach((items, group) => {
    questions.push({
      id: `advanced-${group}`, section: "Advanced", title: `Review ${readableLabel(group)} tuning`,
      description: `Every field on this form belongs to ${readableLabel(group)} tuning in the published Strategy Profile. Each value is explained directly beside its control.`,
      guide: "Keep the current values unless you have evidence and validation for retuning this behavior. Guided mode shows them so no published parameter is silently skipped.",
      content: <div className="guided-form-grid">{items.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").slice(-1)[0]), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} onChange={(value) => replaceProfile({ ...profile, parameters: setPath(profile.parameters, item.path, value) })} value={item.value} />)}</div>,
    });
  });
  questions = questions.filter((question) => {
    if (profile.lifecycle.phase_modes.initial_entry === "manual" && question.id.startsWith("initial-") && question.id !== "initial-mode") return false;
    if (profile.lifecycle.phase_modes.manage === "manual" && (question.id === "adds-overview" || question.id.startsWith("add-") || question.id.startsWith("capability-"))) return false;
    if (profile.lifecycle.phase_modes.reentry === "manual" && question.id.startsWith("reentry-") && question.id !== "reentry-policy") return false;
    if (profile.lifecycle.phase_modes.exit === "manual" && question.id.startsWith("exit-") && question.id !== "exit-mode") return false;
    return true;
  });

  const safeIndex = Math.min(questionIndex, Math.max(questions.length - 1, 0));
  const current = questions[safeIndex];
  const sections = [...new Set(questions.map((question) => question.section))];
  const sectionQuestions = questions.filter((question) => question.section === current.section);
  const sectionPosition = sectionQuestions.findIndex((question) => question.id === current.id) + 1;
  const nextSectionIndex = questions.findIndex((question, index) => index > safeIndex && question.section !== current.section);
  const recap = strategySetupRows(profile);
  useEffect(() => {
    if (questionIndex >= questions.length) setQuestionIndex(Math.max(questions.length - 1, 0));
  }, [questionIndex, questions.length]);
  const guidanceItems = current.id === "profile"
    ? [{ label: "Create new", value: "Begin without active trading logic and answer every lifecycle question." }, { label: "Clone existing", value: "Inspect one source, name the independent copy, then revise any answer." }, { label: "Runtime effect", value: "Neither path changes the protected system fallback or affects runtime before publication." }]
    : [{ label: "Why this matters", value: current.guide }, { label: "Using the default", value: "Leave the displayed answer unchanged to approve the current default." }, { label: "Runtime effect", value: "This answer remains a draft until the configuration is published." }];

  return <main className="guided-strategy-wizard">
    <nav aria-label="Strategy setup sections" className="guided-strategy-section-nav">{sections.map((section) => { const firstIndex = questions.findIndex((question) => question.section === section); return <button aria-current={section === current.section ? "step" : undefined} key={section} onClick={() => setQuestionIndex(firstIndex)} type="button"><span>{section}</span><small>{questions.filter((question) => question.section === section).length}</small></button>; })}</nav>
    <section className="guided-strategy-question">
      <header><span>{current.section} · {sectionPosition} of {sectionQuestions.length}</span><small>Question {safeIndex + 1} of {questions.length}</small></header>
      <div className="guided-question-progress"><span style={{ width: `${((safeIndex + 1) / Math.max(questions.length, 1)) * 100}%` }} /></div>
      <section className="guided-strategy-prompt guided-question-prompt"><h2>{current.title}</h2><p>{current.description}</p><ConfigurationGuidance items={guidanceItems} /></section>
      <section className="guided-answer-surface"><header><strong>{current.id === "profile" ? "Choose how to begin" : "Choose or configure one response"}</strong><small>{current.id === "profile" ? "The selected path creates a new editable Strategy Profile; it does not modify an existing source." : "Only this decision is being edited. You can return to it before publication."}</small></header><div className="guided-strategy-controls guided-answer-content">{current.content}</div></section>
      <details className="guided-running-summary"><summary>Your setup so far <ChevronRight size={15} /></summary><div>{recap.map((row) => <span key={row.label}><small>{row.label}</small><strong>{row.value}</strong></span>)}</div></details>
      <footer className="guided-strategy-navigation"><button className="button" disabled={safeIndex === 0} onClick={() => setQuestionIndex(safeIndex - 1)} type="button"><ArrowLeft size={15} /> Previous</button><div>{nextSectionIndex > 0 ? <button onClick={() => setQuestionIndex(nextSectionIndex)} type="button">Keep remaining {current.section} values</button> : <span>Review each published strategy decision</span>}</div><button className="button primary" disabled={safeIndex === 0} onClick={() => safeIndex < questions.length - 1 ? setQuestionIndex(safeIndex + 1) : onContinue()} type="button">{safeIndex === 0 ? "Choose a path above" : safeIndex < questions.length - 1 ? "Next question" : "Save strategy and continue"} <ArrowRight size={15} /></button></footer>
    </section>
  </main>;
}

function StrategyStartWorkflow({ cloneSourceId, mode, name, onClone, onCloneSourceChange, onCreate, onModeChange, onNameChange, profiles, section }: {
  cloneSourceId: string;
  mode: "create" | "clone" | null;
  name: string;
  onClone: () => void;
  onCloneSourceChange: (profileId: string) => void;
  onCreate: () => void;
  onModeChange: (mode: "create" | "clone") => void;
  onNameChange: (value: string) => void;
  profiles: StrategyProfile[];
  section: StrategySection;
}) {
  const source = profiles.find((row) => row.profile_id === cloneSourceId);
  const normalizedName = name.trim().toLocaleLowerCase();
  const nameConflict = Boolean(normalizedName) && profiles.some((row) => row.name.trim().toLocaleLowerCase() === normalizedName);
  const invalidName = !source || !normalizedName || nameConflict;
  return <div className="strategy-start-workflow">
    <div className="strategy-start-paths">
      <button aria-pressed={mode === "create"} onClick={() => onModeChange("create")} type="button"><span className="strategy-start-icon"><Plus size={18} /></span><span><small>Build from zero</small><strong>Create new strategy</strong><em>Begin with no active trading decisions. The guide will ask about identity, entries, adds, reentry, exits, protection, and capabilities.</em></span><ChevronRight size={17} /></button>
      <button aria-pressed={mode === "clone"} onClick={() => onModeChange("clone")} type="button"><span className="strategy-start-icon"><Clipboard size={18} /></span><span><small>Reuse proven structure</small><strong>Clone an existing strategy</strong><em>Inspect its behavior and capabilities first, then create an independent copy with a new name.</em></span><ChevronRight size={17} /></button>
    </div>
    {mode === "create" ? <section className="strategy-create-workflow">
      <header><span>New strategy</span><strong>Start with a clean decision set</strong><p>Nothing is enabled for trading. Complete the required lifecycle questions before this strategy can be published.</p></header>
      <div className="strategy-blank-summary">
        <span><Check size={15} /><span><strong>No active entry logic</strong><small>Opportunity, confirmation, and blocker rules begin empty.</small></span></span>
        <span><Check size={15} /><span><strong>No position management</strong><small>Adds, reentry, strategic exits, and optional capabilities begin disabled.</small></span></span>
        <span><Check size={15} /><span><strong>Session only</strong><small>The protected system fallback remains unchanged until this strategy is complete and published.</small></span></span>
      </div>
      <NextActionArea active description="Give the blank draft a distinct operator-facing name." focusKey="create-name" title="Name the new strategy">
        <div className="strategy-start-name"><TextField help={nameConflict ? "Choose a name not already used by another Strategy Profile." : "You can refine this name in the next question."} label="Strategy name" nextAction onChange={onNameChange} value={name} />{nameConflict ? <span role="alert">A strategy with this name already exists.</span> : null}</div>
        <footer><button className="button primary" disabled={!normalizedName || nameConflict} onClick={onCreate} type="button">Create blank strategy <ArrowRight size={15} /></button></footer>
      </NextActionArea>
    </section> : null}
    {mode === "clone" ? <section className="strategy-clone-workflow">
      <header><span>Clone existing</span><strong>Choose, inspect, then name the copy</strong><p>The source remains unchanged. The new strategy receives its own identity and can be revised in every following question.</p></header>
      <div className="strategy-clone-layout">
        <NextActionArea active={!source} className="strategy-clone-source-step" description="Choose one strategy to reveal its behavior, lifecycle, execution, and capabilities." focusKey="clone-source" title="Select a source strategy">
          <nav aria-label="Strategies available to clone">{profiles.map((row, index) => { const summary = strategySourceSummary(row); return <button aria-current={row.profile_id === source?.profile_id ? "true" : undefined} data-next-action-control={!source && index === 0 ? "true" : undefined} key={row.profile_id} onClick={() => onCloneSourceChange(row.profile_id)} type="button"><span><strong>{row.name}</strong><small>{row.protected ? "Protected system strategy" : "Editable strategy"}</small><em>{summary}</em></span><ChevronRight size={15} /></button>; })}</nav>
        </NextActionArea>
        <div className="strategy-clone-review">{source ? <>
          <StrategyProfileFeaturePreview profile={source} section={section} />
          <NextActionArea active description="Use a distinct name for the independent copy." focusKey={`clone-name-${source.profile_id}`} title="Name the cloned strategy">
            <div className="strategy-start-name"><TextField help={nameConflict ? "Choose a name not already used by another Strategy Profile." : "The source keeps its current name and configuration."} label="Clone name" nextAction onChange={onNameChange} value={name} />{nameConflict ? <span role="alert">A strategy with this name already exists.</span> : null}</div>
            <footer><span><LockKeyhole size={14} /> Source remains unchanged</span><button className="button primary" disabled={invalidName} onClick={onClone} type="button">Clone and configure <ArrowRight size={15} /></button></footer>
          </NextActionArea>
        </> : <div className="strategy-clone-empty"><Clipboard size={22} /><strong>No source selected</strong><p>Choose a strategy on the left. Its complete configuration summary will appear here before you create the copy.</p></div>}</div>
      </div>
    </section> : null}
  </div>;
}

function NextActionArea({ active, children, className = "", description, focusKey, title }: { active: boolean; children: ReactNode; className?: string; description: string; focusKey: string; title: string }) {
  const regionRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!active) return;
    const frame = window.requestAnimationFrame(() => {
      const control = regionRef.current?.querySelector<HTMLElement>("[data-next-action-control]");
      if (!control) return;
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      regionRef.current?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "nearest" });
      control.focus({ preventScroll: true });
      if (control instanceof HTMLInputElement && control.type === "text") control.select();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active, focusKey]);
  return <section aria-label={`${active ? "Next action" : "Completed step"}: ${title}`} className={`guided-action-step${active ? " is-next" : ""}${className ? ` ${className}` : ""}`} ref={regionRef}>
    <header><span>{active ? "Next action" : "Source selected"}</span><strong>{title}</strong><p>{description}</p></header>
    {children}
  </section>;
}

function strategySourceSummary(profile: StrategyProfile) {
  const capabilities = profile.capabilities.filter((row) => row.enabled).length;
  const adds = profile.lifecycle.initial_entry.add_steps.filter((row) => row.enabled).length;
  const exits = profile.lifecycle.exit.rule_sets.filter((row) => row.enabled).length;
  return `${readableLabel(profile.lifecycle.trading_behavior.side)} · ${capabilities} capabilities · ${adds} adds · ${exits} exits`;
}

function StrategyProfileFeaturePreview({ profile, section }: { profile: StrategyProfile; section: StrategySection }) {
  const behavior = profile.lifecycle.trading_behavior;
  const initial = profile.lifecycle.initial_entry;
  const opportunityCount = countRuleReferences(initial.opportunity.expression);
  const confirmationCount = countRuleReferences(initial.confirmation.expression);
  const blockerCount = countRuleReferences(initial.blockers.expression);
  const addCount = initial.add_steps.filter((row) => row.enabled).length;
  const exitCount = profile.lifecycle.exit.rule_sets.filter((row) => row.enabled).length;
  const capabilities = profile.capabilities.filter((row) => row.enabled).map((row) => section.capability_catalog.find((item) => item.capability_id === row.capability_id)?.name ?? readableLabel(row.capability_id));
  const executionPolicies = new Set([initial.order_intent.execution_policy, profile.lifecycle.reentry.order_intent.execution_policy, ...initial.add_steps.map((row) => row.order_intent.execution_policy), ...profile.lifecycle.exit.rule_sets.map((row) => row.order_intent.execution_policy)].filter(Boolean));
  return <div className="strategy-feature-preview">
    <header><span>{profile.protected ? "Protected source" : "Editable source"}</span><strong>{profile.name}</strong><p>{profile.description || "No strategy description has been provided."}</p></header>
    <dl>
      <div><dt>Trading behavior</dt><dd><strong>{readableLabel(behavior.side)}</strong><span>{behavior.eligible_sessions.map(readableLabel).join(", ")}</span></dd></div>
      <div><dt>Initial entry</dt><dd><strong>{opportunityCount} opportunity · {confirmationCount} confirmation</strong><span>{blockerCount} blocker rule set{blockerCount === 1 ? "" : "s"} · {readableLabel(initial.capital_request.mode)}</span></dd></div>
      <div><dt>Position lifecycle</dt><dd><strong>{addCount} add action{addCount === 1 ? "" : "s"} · {exitCount} strategic exit{exitCount === 1 ? "" : "s"}</strong><span>{profile.lifecycle.phase_modes.reentry === "automatic" ? `Reentry up to ${profile.lifecycle.reentry.maximum_attempts} times` : "Reentry manual"}</span></dd></div>
      <div><dt>Order behavior</dt><dd><strong>{executionPolicies.size} execution polic{executionPolicies.size === 1 ? "y" : "ies"}</strong><span>{readableLabel(initial.order_intent.partial_fill_policy)} · OMS applies tested terminal timing</span></dd></div>
    </dl>
    <section><span>Enabled capabilities · {capabilities.length}</span>{capabilities.length ? <div>{capabilities.map((capability) => <strong key={capability}><CheckCircle2 size={13} />{capability}</strong>)}</div> : <p>No optional capabilities are enabled.</p>}</section>
  </div>;
}

function GuidedCapitalRequestFields({ onChange, segment, value }: { onChange: (value: CapitalRequestConfig) => void; segment: "amount" | "priority"; value: CapitalRequestConfig }) {
  const request = { fixed_quantity: { label: "Shares requested", maximum: undefined, minimum: 1, step: 1, unit: "shares" }, mandate_fraction: { label: "Mandate capacity", maximum: 1, minimum: .01, step: .05, unit: "fraction" }, risk_fraction: { label: "Risk budget", maximum: 1, minimum: .01, step: .05, unit: "fraction" }, all_available: { label: "", maximum: undefined, minimum: 0, step: 1, unit: "" } }[value.mode];
  if (segment === "priority") return <BooleanField help="Allow Portfolio to propose releasing a weaker position when policy permits." label="Allow replacement proposal" onChange={(allow_replacement) => onChange({ ...value, allow_replacement })} value={value.allow_replacement} />;
  return <div className="guided-form-grid"><SelectField help="Choose how Strategy expresses the desired size before Portfolio evaluates account-specific capacity and risk." label="Request method" onChange={(mode) => onChange({ ...value, mode: mode as CapitalRequestConfig["mode"], value: mode === "fixed_quantity" ? 100 : mode === "all_available" ? 1 : .2 })} options={[{ label: "Fixed shares", value: "fixed_quantity" }, { label: "Fraction of mandate cash", value: "mandate_fraction" }, { label: "Fraction of risk budget", value: "risk_fraction" }, { label: "All remaining mandate capacity", value: "all_available" }]} value={value.mode} />{value.mode !== "all_available" ? <NumberField help="Enter the desired amount in the units implied by the selected request method. Portfolio may approve less or reject it." label={request.label} maximum={request.maximum} minimum={request.minimum} onChange={(requestValue) => onChange({ ...value, value: requestValue })} step={request.step} unit={request.unit} value={value.value} /> : <div className="guided-readonly-value"><span>Request amount</span><strong>All capacity still allowed by the mandate</strong><small>Portfolio computes the actual quantity after every current account and risk check.</small></div>}</div>;
}

function GuidedOrderIntentFields({ draft, eligibleSessions, onChange, segment, value }: { draft: Draft; eligibleSessions: string[]; onChange: (value: OrderIntentConfig) => void; segment: "execution" | "partial-fill" | "protection"; value: OrderIntentConfig }) {
  if (segment === "partial-fill") {
    const responses = {
      accept_partial: { description: "Keep every broker-confirmed fill and stop requesting the unfilled quantity.", label: "Accept the partial position", remainder: "Stop working it" },
      cancel_remainder: { description: "Cancel the broker-confirmed remainder after a partial fill while retaining shares already filled.", label: "Cancel the remainder", remainder: "Cancel immediately" },
      complete_remainder: { description: "Keep every broker-confirmed fill and continue working only the true unfilled quantity under the selected execution policy.", label: "Finish the approved remainder", remainder: "Continue under policy" },
    } as const;
    const selected = responses[value.partial_fill_policy];
    return <><SelectField help="Choose the terminal behavior for only the broker-confirmed unfilled remainder. OMS always reconciles cumulative fills before acting." label="Partial-fill response" onChange={(partial_fill_policy) => onChange({ ...value, partial_fill_policy: partial_fill_policy as OrderIntentConfig["partial_fill_policy"] })} options={Object.entries(responses).map(([responseValue, response]) => ({ description: response.description, label: response.label, value: responseValue }))} value={value.partial_fill_policy} /><GuidedSelectionGuide description={selected.description} eyebrow="Selected partial-fill response" facts={[{ label: "Confirmed fills", value: "Always retained" }, { label: "Unfilled remainder", value: selected.remainder }, { label: "Exposure accounting", value: "Broker reconciled first" }]} icon={<CheckCircle2 size={18} />} label={selected.label} note="This choice cannot duplicate or discard a confirmed fill. OMS reconciles cumulative broker fills before acting on the remainder." tone="remainder" /></>;
  }
  if (segment === "protection") {
    const selected = draft.oms.protection_profiles.find((row) => row.profile_id === value.protection_profile) ?? draft.oms.protection_profiles[0];
    return <><SelectField help="Select the independent broker-held stop, catastrophic backstop, target, and trailing contract applied after confirmed fills." label="Protection profile" onChange={(protection_profile) => onChange({ ...value, protection_profile })} options={draft.oms.protection_profiles.map((row) => ({ description: row.description, label: `${row.name} · v${row.revision}`, value: row.profile_id }))} value={value.protection_profile} />{selected ? <GuidedSelectionGuide description={selected.description} eyebrow="Selected protection profile" facts={[{ label: "Protected slices", value: String(selected.slices.length) }, { label: "Stop design", value: [...new Set(selected.slices.map((slice) => readableLabel(slice.stop.rule_type)))].join(", ") }, { label: "Add handling", value: readableLabel(selected.add_policy) }, { label: "Emergency repair", value: `${selected.emergency_repair_deadline_ms} ms` }]} icon={<ShieldCheck size={18} />} label={`${selected.name} · v${selected.revision}`} note={`${selected.mandatory_catastrophic_backstop ? "A broker-held catastrophic backstop is mandatory." : "A catastrophic backstop is not mandatory."} Protection attaches only to broker-confirmed fills and does not change approved quantity.`} tone="protection" /> : null}</>;
  }
  const selected = draft.oms.execution_policies.find((row) => row.policy_id === value.execution_policy) ?? draft.oms.execution_policies[0];
  return <><div className="guided-form-grid"><SelectField help="Select the broker-neutral execution behavior that OMS uses to choose price, repricing cadence, and terminal actions. OMS owns the tested terminal timing for the selected policy." label="Execution policy" onChange={(execution_policy) => onChange({ ...value, execution_policy })} options={draft.oms.execution_policies.map((row) => ({ description: executionPolicyLookupDescription(row), label: `${readableLabel(row.name)} · v${row.revision}`, value: row.policy_id }))} searchable={false} value={value.execution_policy} /></div>{selected ? <GuidedSelectionGuide description={executionPolicyBehavior(selected)} eyebrow="Selected execution policy" facts={[{ label: "Quote authority", value: readableLabel(selected.quote_source) }, { label: "Working deadline", value: executionPolicyDuration(selected.envelope.deadline_ms) }, { label: "Repricing", value: selected.envelope.maximum_reprices ? `${selected.envelope.maximum_reprices} max · ${selected.envelope.minimum_reprice_interval_ms} ms apart` : "No OMS reprices" }, { label: "Unfilled remainder", value: readableLabel(selected.partial_fill_policy) }]} icon={<Send size={18} />} label={`${readableLabel(selected.name)} · v${selected.revision}`} note={`Eligible sessions: ${eligibleSessions.map(readableLabel).join(", ") || "none"}. OMS chooses compatible broker routing and cannot exceed Portfolio's approved quantity or the policy's price envelope.`} tone="execution" /> : null}</>;
}

function GuidedSelectionGuide({ description, eyebrow, facts, icon, label, note, tone }: { description: string; eyebrow: string; facts: Array<{ label: string; value: string }>; icon: ReactNode; label: string; note: string; tone: "execution" | "protection" | "remainder" }) {
  return <section className="strategy-selected-guide" data-tone={tone}><header><span>{icon}</span><div><small>{eyebrow}</small><strong>{label}</strong></div></header><div className="strategy-selected-guide-description">{description}</div><dl>{facts.map((fact) => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl><footer><ShieldCheck size={15} /><span>{note}</span></footer></section>;
}

function executionPolicyBehavior(policy: ExecutionPolicyConfig): string {
  return ({
    passive: "Posts at the near-side quote without crossing the spread, prioritizing price quality over fill certainty.",
    midpoint: "Posts at the current bid-ask midpoint, seeking spread improvement while accepting that the order may not fill.",
    adaptive_patient: "Starts at the near-side quote and advances only to midpoint after repeated attempts, favoring price quality over speed.",
    adaptive_regular: "Moves progressively from the near-side quote toward executable liquidity, balancing price improvement with fill probability.",
    adaptive_urgent: "Quotes at the executable touch immediately, prioritizing a timely fill within the approved price envelope.",
    adaptive_very_urgent: "Starts at the executable touch and may move through it by bounded ticks, maximizing fill urgency within hard limits.",
    immediate_with_limit: "Seeks an immediate fill at executable liquidity but never crosses the configured buy ceiling or sell floor.",
    ibkr_native_adaptive: "Uses urgent touch pricing without OMS repricing. The current runtime does not delegate this policy to a broker-native adaptive algorithm.",
    cancel_if_not_filled: "Moves from passive toward executable pricing while time remains, then cancels the unfilled remainder at the deadline.",
  } as Record<string, string>)[policy.name] ?? policy.description;
}

function executionPolicyDuration(milliseconds: number): string {
  return milliseconds >= 1_000 ? `${round(milliseconds / 1_000)} s` : `${milliseconds} ms`;
}

function executionPolicyLookupDescription(policy: ExecutionPolicyConfig): string {
  const deadline = executionPolicyDuration(policy.envelope.deadline_ms);
  const repricing = policy.envelope.maximum_reprices
    ? `Up to ${policy.envelope.maximum_reprices} reprice${policy.envelope.maximum_reprices === 1 ? "" : "s"}, no more often than every ${policy.envelope.minimum_reprice_interval_ms} ms, within ${deadline}.`
    : `No OMS reprices; the working deadline is ${deadline}.`;
  return `${executionPolicyBehavior(policy)} ${repricing}`;
}

function GuidedActionForm({ sections }: { sections: Array<{ content: ReactNode; description: string; title: string }> }) {
  return <div className="guided-action-form">{sections.map((section) => <section key={section.title}><header><strong>{section.title}</strong><p>{section.description}</p></header><div>{section.content}</div></section>)}</div>;
}

function GuidedExitTimingFields({ onChange, value }: { onChange: (value: ExitRuleSet) => void; value: ExitRuleSet }) {
  return <div className="guided-form-grid"><NumberField help="Set the delay after the confirmed entry before this strategic exit route may act." label="Active after" minimum={0} onChange={(active_after_ms) => onChange({ ...value, timing: { ...value.timing, active_after_ms } })} step={1000} unit="ms" value={value.timing.active_after_ms} /><NumberField help="Set how long the route remains eligible. Zero keeps it eligible while the position is open." label="Expires after" minimum={0} onChange={(expires_after_ms) => onChange({ ...value, timing: { ...value.timing, expires_after_ms } })} step={1000} unit="ms" value={value.timing.expires_after_ms} /></div>;
}

function GuidedExitActionFields({ onChange, value }: { onChange: (value: ExitRuleSet) => void; value: ExitRuleSet }) {
  return <div className="guided-form-grid"><SelectField help="Choose whether this route requests the full current position or only a configured fraction." label="Position action" onChange={(action) => onChange({ ...value, action: action as ExitRuleSet["action"] })} options={[{ label: "Close the position", value: "close" }, { label: "Reduce the position", value: "reduce" }]} value={value.action} />{value.action === "reduce" ? <NumberField help="Set the fraction of the reconciled current position that Strategy requests to release." label="Reduction fraction" maximum={1} minimum={.01} onChange={(position_fraction) => onChange({ ...value, position_fraction })} step={.05} unit="fraction" value={value.position_fraction} /> : <div className="guided-readonly-value"><span>Requested quantity</span><strong>Entire reconciled position</strong><small>Portfolio and OMS still verify the broker-authoritative current quantity.</small></div>}</div>;
}

function GuidedCapabilityFields({ binding, definition, onChange }: { binding: CapabilityBinding; definition: CapabilityDefinition; onChange: (value: CapabilityBinding) => void }) {
  return <div className="guided-capability-fields"><BooleanField help="Enable this capability for the selected Strategy Profile. Disabling it retains every saved parameter for later review." label="Enabled" onChange={(enabled) => onChange({ ...binding, enabled })} value={binding.enabled} />{binding.enabled ? <div className="guided-form-grid">{definition.parameters.map((parameter) => <CapabilityField definition={parameter} key={parameter.key} onChange={(value) => onChange({ ...binding, settings: { ...binding.settings, [parameter.key]: value } })} value={binding.settings[parameter.key]} />)}</div> : <p>The capability remains configured but cannot participate in the Strategy lifecycle while disabled.</p>}</div>;
}

function strategySetupRows(profile: StrategyProfile) {
  return [
    { label: "Trading plan", value: profile.name },
    { label: "Behavior", value: `${readableLabel(profile.lifecycle.trading_behavior.side)} · ${profile.lifecycle.trading_behavior.eligible_sessions.map(readableLabel).join(", ")}` },
    { label: "Initial entry", value: `${readableLabel(profile.lifecycle.initial_entry.capital_request.mode)} · ${countRuleReferences(profile.lifecycle.initial_entry.opportunity.expression)}/${countRuleReferences(profile.lifecycle.initial_entry.confirmation.expression)}/${countRuleReferences(profile.lifecycle.initial_entry.blockers.expression)} rule references` },
    { label: "Position adds", value: `${profile.lifecycle.initial_entry.add_steps.filter((row) => row.enabled).length} enabled` },
    { label: "Reentry", value: profile.lifecycle.phase_modes.reentry === "automatic" ? `${profile.lifecycle.reentry.maximum_attempts} attempts · ${profile.lifecycle.reentry.cooldown_ms} ms` : "Manual" },
    { label: "Strategic exits", value: `${profile.lifecycle.exit.rule_sets.filter((row) => row.enabled).length} enabled` },
    { label: "Capabilities", value: `${profile.capabilities.filter((row) => row.enabled).length} enabled` },
  ];
}

function countRuleReferences(expression?: RuleExpression): number {
  if (!expression) return 0;
  return expression.kind === "rule_set" ? 1 : expression.children.reduce((sum, child) => sum + countRuleReferences(child), 0);
}

function blankStrategyProfile(source: StrategyProfile, draft: Draft): StrategyProfile {
  const profileId = uniqueId("new-strategy", draft.strategy.profiles.map((row) => row.profile_id));
  const emptyStage = (): RuleStage => ({ expression: { children: [], kind: "operator", operator: "and" } });
  const executionPolicy = draft.oms.execution_policies.find((row) => row.policy_id === "adaptive_regular")?.policy_id ?? draft.oms.execution_policies[0]?.policy_id ?? "";
  const protectionProfile = draft.oms.protection_profiles[0]?.profile_id ?? "";
  const capitalRequest: CapitalRequestConfig = { allow_replacement: false, mode: "mandate_fraction", value: 0.1 };
  const orderIntent: OrderIntentConfig = { deadline_ms: 750, execution_policy: executionPolicy, partial_fill_policy: "complete_remainder", protection_profile: protectionProfile };
  return {
    ...deepClone(source),
    capabilities: source.capabilities.map((row) => ({ ...deepClone(row), enabled: false })),
    description: "",
    editable: true,
    enabled: false,
    rule_set_catalog: [],
    lifecycle: {
      phase_modes: { initial_entry: "automatic", manage: "automatic", reentry: "automatic", exit: "automatic" },
      trading_behavior: { eligible_sessions: ["regular"], side: source.lifecycle.trading_behavior.side },
      initial_entry: { add_steps: [], blockers: emptyStage(), capital_request: deepClone(capitalRequest), confirmation: emptyStage(), opportunity: emptyStage(), order_intent: deepClone(orderIntent) },
      reentry: { capital_request: deepClone(capitalRequest), cooldown_ms: 0, enabled: true, maximum_attempts: 0, order_intent: deepClone(orderIntent), require_new_confirmation: true, rules: { blockers: emptyStage(), confirmation: emptyStage(), opportunity: emptyStage() } },
      exit: { rule_sets: [{ action: "close", enabled: false, name: "Strategic exit", order_intent: deepClone(orderIntent), position_fraction: 1, rule_set_id: "strategic-exit", rules: emptyStage(), summary: "Define the evidence that should close the position.", timing: { active_after_ms: 0, expires_after_ms: 0 } }] },
    },
    name: "Untitled Strategy",
    origin: "user",
    profile_id: profileId,
    protected: false,
    publication_status: "draft",
    derived_from_profile_id: "",
    revision: 1,
  };
}

function cloneStrategyProfile(source: StrategyProfile, existing: StrategyProfile[], requestedName: string): StrategyProfile {
  const profileId = uniqueId(`${source.profile_id}-copy`, existing.map((row) => row.profile_id));
  return { ...deepClone(source), derived_from_profile_id: source.profile_id, editable: true, name: requestedName.trim(), origin: "user", profile_id: profileId, protected: false, publication_status: "draft", revision: 1 };
}

function uniqueProfileName(base: string, existing: StrategyProfile[]) {
  const taken = new Set(existing.map((row) => row.name.trim().toLocaleLowerCase()));
  let value = base;
  let index = 2;
  while (taken.has(value.toLocaleLowerCase())) value = `${base} ${index++}`;
  return value;
}

function GuidedQuestion({ children, description, label, status }: { children: ReactNode; description: string; label: string; status: string }) {
  return <section className="guided-question"><section className="guided-question-prompt"><header><div><span>{label}</span><p>{description}</p></div><em data-state={status.toLowerCase().replaceAll(" ", "-")}>{status}</em></header></section><section className="guided-answer-surface"><div className="guided-answer-content">{children}</div></section></section>;
}

function ConfigurationGuidance({ items }: { items: Array<{ label: string; value: string }> }) {
  return <dl className="configuration-guidance">{items.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>;
}

function DecisionOptions({ onChange, options, value }: { onChange: (value: string) => void; options: Array<{ detail: string; label: string; recommended?: boolean; value: string }>; value: string }) {
  const name = useId();
  return <div className="guided-decision-options">{options.map((option) => <label key={option.value}><input checked={value === option.value} name={name} onChange={() => onChange(option.value)} type="radio" /><span className="guided-choice-card"><span className="guided-choice-copy"><span className="guided-choice-title"><strong>{option.label}</strong>{option.recommended ? <em>Recommended</em> : null}</span><small>{option.detail}</small></span><span aria-hidden="true" className="guided-choice-marker">{value === option.value ? <Check size={14} /> : null}</span></span></label>)}</div>;
}

function ModeChoices({ onChange, options, values }: { onChange: (values: string[]) => void; options: string[]; values: string[] }) {
  return <div className="guided-mode-choices">{options.map((option) => <label key={option}><input checked={values.includes(option)} onChange={(event) => onChange(event.target.checked ? [...values, option] : values.filter((value) => value !== option))} type="checkbox" /><span><Check size={13} />{readableLabel(option)}</span></label>)}</div>;
}

function GuidedReview({ approved, draft, label, onLabelChange, onPublish, onReturn, publishing, revisions }: { approved: Revision | null; draft: Draft; label: string; onLabelChange: (value: string) => void; onPublish: () => void; onReturn: () => void; publishing: boolean; revisions: Revision[] }) {
  const rows = reviewRows(draft, approved);
  return <div className="guided-review">
    <header><span>Final step</span><h2>Review the effective configuration</h2><p>Resolve anything marked invalid or needing a decision. Publication freezes the entire draft and configured Canvas for new runs.</p></header>
    <div className="guided-review-layout"><div className="guided-review-matrix">{rows.map((row) => { const Icon = row.icon; return <article key={row.step}><span><Icon size={18} /><strong>{row.label}</strong></span><span>{row.selection}</span><em data-state={row.state.toLowerCase().replaceAll(" ", "-")}>{row.state}</em><button onClick={() => navigateGuidedStep(row.step, () => undefined)} type="button">Change <ChevronRight size={13} /></button></article>; })}</div><aside><RevisionPublisher approved={approved} draft={draft} guided label={label} onLabelChange={onLabelChange} onPublish={onPublish} publishing={publishing} revisions={revisions} /></aside><details className="guided-technical-preview"><summary>Show the technical runtime preview <ChevronRight size={15} /></summary><EffectiveConfigurationPreview draft={draft} /></details></div>
    <button className="button" onClick={onReturn} type="button"><ArrowLeft size={15} /> Back to accounts</button>
  </div>;
}

function GuidedEmpty({ onSwitchToExpert }: { onSwitchToExpert: () => void }) {
  return <div className="guided-empty"><TriangleAlert size={20} /><h2>This step needs a base object</h2><p>Create the missing profile, Run Plan, mandate, OMS profile, policy, protection profile, or account in Expert mode. Guided setup does not create a Live-critical object implicitly.</p><button className="button primary" onClick={onSwitchToExpert} type="button"><Settings2 size={15} /> Open Expert editor</button></div>;
}

function MarketDiscoveryStudio({ onChange, section }: { onChange: (value: MarketDiscoverySection) => void; section: MarketDiscoverySection }) {
  const [mode, setMode] = useState<"catalog" | "guided">("guided");
  const [guidedStep, setGuidedStep] = useState<"core" | "watchlists" | "history">("core");
  const [capabilityQuery, setCapabilityQuery] = useState("");
  const [capabilityToAddId, setCapabilityToAddId] = useState("");
  const [selectedCapabilityId, setSelectedCapabilityId] = useState(section.core_scan.calculations[0]?.capability_id ?? "");
  const [selectedWatchlistId, setSelectedWatchlistId] = useState(section.watchlists[0]?.watchlist_id ?? "");
  const [watchlistView, setWatchlistView] = useState<"select" | "guided">("select");
  const [watchlistQuestionIndex, setWatchlistQuestionIndex] = useState(0);
  const selectedCapability = section.core_scan.calculations.find((row) => row.capability_id === selectedCapabilityId) ?? section.core_scan.calculations[0];
  const selectedWatchlist = section.watchlists.find((row) => row.watchlist_id === selectedWatchlistId) ?? section.watchlists[0];
  const watchlistQuestion = WATCHLIST_GUIDED_STEPS[watchlistQuestionIndex] as WatchlistGuidedStep;
  const groupedCapabilities = useMemo(() => {
    const groups = new Map<string, DiscoveryCapability[]>();
    const query = capabilityQuery.trim().toLocaleLowerCase();
    section.core_scan.calculations.filter((capability) => !query || [capability.name, capability.category, capability.provider, capability.description].some((value) => value.toLocaleLowerCase().includes(query))).forEach((capability) => {
      const key = capability.tier === "core" ? "Core Scan" : capability.category || "Watchlist calculations";
      groups.set(key, [...(groups.get(key) ?? []), capability]);
    });
    return [...groups.entries()];
  }, [capabilityQuery, section.core_scan.calculations]);
  const visibleCapabilityCount = groupedCapabilities.reduce((count, [, capabilities]) => count + capabilities.length, 0);
  const activeDiscoveryCapabilities = useMemo(
    () => section.core_scan.calculations.filter((capability) => capability.enabled || capability.system_required),
    [section.core_scan.calculations],
  );
  const availableDiscoveryCapabilities = useMemo(
    () => section.core_scan.calculations.filter((capability) => capability.configurable && !capability.system_required && !capability.enabled),
    [section.core_scan.calculations],
  );

  useEffect(() => {
    if (availableDiscoveryCapabilities.some((capability) => capability.capability_id === capabilityToAddId)) return;
    setCapabilityToAddId(availableDiscoveryCapabilities[0]?.capability_id ?? "");
  }, [availableDiscoveryCapabilities, capabilityToAddId]);

  function replaceWatchlist(next: WatchlistConfig) {
    onChange({ ...section, watchlists: section.watchlists.map((row) => row.watchlist_id === next.watchlist_id ? next : row) });
  }

  function addWatchlist() {
    const watchlistId = uniqueId("watchlist", section.watchlists.map((row) => row.watchlist_id));
    const next: WatchlistConfig = {
      watchlist_id: watchlistId, name: "Untitled Watchlist", description: "", enabled: true,
      source_scan_id: section.core_scan.scan_id, inclusion_rule_sets: [], exclusion_rule_sets: [], ranking_field: "liquidity-rank",
      maximum_size: 100, refresh_interval_ms: 1000, membership_ttl_ms: 300000, manual_inclusions: [], manual_exclusions: [],
      calculations: section.core_scan.calculations.filter((row) => row.tier === "watchlist" && row.enabled).map((row) => row.capability_id), membership_history: [],
    };
    onChange({ ...section, watchlists: [...section.watchlists, next] });
    setSelectedWatchlistId(watchlistId);
    setWatchlistQuestionIndex(0);
    setWatchlistView("guided");
  }

  function configureWatchlist(watchlistId: string) {
    setSelectedWatchlistId(watchlistId);
    setWatchlistQuestionIndex(0);
    setWatchlistView("guided");
  }

  function nextWatchlistQuestion() {
    if (watchlistQuestionIndex >= WATCHLIST_GUIDED_STEPS.length - 1) {
      setWatchlistView("select");
      return;
    }
    setWatchlistQuestionIndex((index) => index + 1);
  }

  function setDiscoveryCapabilityEnabled(capabilityId: string, enabled: boolean) {
    const capability = section.core_scan.calculations.find((row) => row.capability_id === capabilityId);
    if (!capability || (!enabled && (capability.system_required || !capability.configurable))) return;
    const calculations = section.core_scan.calculations.map((row) => row.capability_id === capabilityId ? { ...row, enabled } : row);
    const fallbackRankingField = calculations.find((row) => row.enabled && row.capability_id === "liquidity-rank")?.capability_id
      ?? calculations.find((row) => row.enabled && row.system_required)?.capability_id
      ?? "";
    const watchlists = section.watchlists.map((watchlist) => enabled ? watchlist : {
      ...watchlist,
      calculations: watchlist.calculations.filter((id) => id !== capabilityId),
      ranking_field: watchlist.ranking_field === capabilityId ? fallbackRankingField : watchlist.ranking_field,
    });
    onChange({ ...section, core_scan: { ...section.core_scan, calculations }, watchlists });
  }

  function addDiscoveryCapability() {
    if (!capabilityToAddId) return;
    setDiscoveryCapabilityEnabled(capabilityToAddId, true);
  }

  return <div className="strategy-studio-workspace market-discovery-studio">
    <nav className="strategy-editor-toolbar" aria-label="Market Discovery navigation">
      <span><strong>QMD MARKET DISCOVERY</strong><small>{mode === "catalog" ? "Capability Catalog" : "Guided Configuration"}</small></span>
      <div className="strategy-editor-modes" role="tablist" aria-label="Market Discovery views">
        <button aria-selected={mode === "catalog"} onClick={() => setMode("catalog")} role="tab" type="button"><Search size={14} /> Capability Catalog</button>
        <button aria-selected={mode === "guided"} onClick={() => setMode("guided")} role="tab" type="button"><BookOpenCheck size={14} /> Guided Configuration</button>
      </div>
    </nav>
    {mode === "catalog" ? <div className="configuration-workbench strategy-editor-catalog discovery-capability-workbench">
      <aside className="strategy-parameter-catalog">
        <header><div><span>QMD capability catalog</span><strong>{visibleCapabilityCount} of {section.core_scan.calculations.length}</strong></div><p>Review every QMD extraction, indicator, signal, quality check, and membership service.</p></header>
        <label className="strategy-parameter-search"><Search aria-hidden="true" size={15} /><input aria-label="Search QMD capabilities" onChange={(event) => setCapabilityQuery(event.target.value)} placeholder="Search capabilities" type="search" value={capabilityQuery} /></label>
        <div className="strategy-parameter-list">{groupedCapabilities.map(([group, capabilities]) => <section className="strategy-parameter-group" key={group}><header><strong>{group}</strong><span>{capabilities.length}</span></header>{capabilities.map((capability) => <button aria-current={capability.capability_id === selectedCapability?.capability_id ? "true" : undefined} key={capability.capability_id} onClick={() => setSelectedCapabilityId(capability.capability_id)} type="button"><span><strong>{capability.name}</strong><small>{capability.provider} · {capability.configurable ? "Configurable" : capability.system_required ? "Required" : "Read only"}</small></span><ChevronRight size={14} /></button>)}</section>)}{visibleCapabilityCount === 0 ? <div className="strategy-parameter-empty-list"><Search size={18} /><span>No capabilities match this search.</span></div> : null}</div>
      </aside>
      <main className="strategy-parameter-detail-page discovery-capability-detail">
        {selectedCapability ? <>
          <header><span>{selectedCapability.category}</span><h2>{selectedCapability.name}</h2><p>{selectedCapability.description}</p></header>
          <section className="discovery-capability-control">
            <label className="configuration-field configuration-boolean"><span>Enabled</span><small>{selectedCapability.configurable ? "Controls whether this capability belongs to the active configuration. Disabled capabilities remain available in the catalog." : "This behavior is active but owned by QMD and cannot be changed here."}</small><input checked={selectedCapability.enabled} disabled={!selectedCapability.configurable || selectedCapability.system_required} onChange={(event) => setDiscoveryCapabilityEnabled(selectedCapability.capability_id, event.target.checked)} type="checkbox" /></label>
          </section>
          <ParameterDocumentation documentation={{ role: [`${selectedCapability.provider} owns this ${selectedCapability.output_type} output and publishes it for Scanner, Watchlists, and eligible Strategy rules.`], timing: [selectedCapability.timeframes.length ? `Available calculation clocks: ${selectedCapability.timeframes.join(", ")}.` : "The service owns its publication clock and causal availability."], impact: [selectedCapability.tier === "core" ? "Core Scan evaluates it across the broad Security Universe." : "It is intended for the smaller candidate set after Core Scan nomination."], caution: [selectedCapability.configurable ? "Changing it affects future Watchlist resolution after publication." : "The control is intentionally read-only. Disabled styling means non-editable, not inactive."], cautionTone: "information" }} group={selectedCapability.category} path={selectedCapability.capability_id} value={selectedCapability.enabled} />
        </> : null}
      </main>
    </div> : <article className="strategy-authoring discovery-guided-authoring">
      <div className="strategy-authoring-step-navigation discovery-step-navigation">
        <span />
        <nav className="strategy-authoring-steps" aria-label="Market Discovery configuration steps">
          {([['core', '1', 'Core Scan'], ['watchlists', '2', 'Watchlists'], ['history', '3', 'History']] as const).map(([id, number, label]) => <button aria-current={guidedStep === id ? "step" : undefined} key={id} onClick={() => setGuidedStep(id)} type="button"><span>{number}</span><strong>{label}</strong></button>)}
        </nav>
        <span />
      </div>
      <section className="strategy-authoring-stage discovery-guided-stage">
        {guidedStep === "core" ? <>
          <header className="strategy-identity-intro"><h2>Review what QMD evaluates across the market</h2></header>
          <div className="discovery-core-summary"><article><span>Security universe</span><strong>{section.security_universe.name}</strong><p>{section.security_universe.description}</p><em>System managed</em></article><article><span>Published default</span><strong>{section.core_scan.name}</strong><p>{section.core_scan.description}</p><em>{activeDiscoveryCapabilities.length} active capabilities</em></article></div>
          <header className="discovery-capability-heading">
            <div><span>Active capabilities</span><strong>{activeDiscoveryCapabilities.length} of {section.core_scan.calculations.length}</strong><small>Required capabilities are fixed. Modifiable capabilities can be removed and added again from the catalog.</small></div>
            <div className="discovery-capability-add">
              <InventoryFilterSelect ariaLabel="Capability to add" className="configuration-lookup-button" onChange={setCapabilityToAddId} options={availableDiscoveryCapabilities.length ? availableDiscoveryCapabilities.map((capability) => ({ description: capability.description, label: capability.name, value: capability.capability_id })) : [{ description: "Every available capability is already active.", label: "No available capabilities", value: "" }]} searchable={availableDiscoveryCapabilities.length > 7} searchPlaceholder="Find a capability…" value={capabilityToAddId} />
              <button className="button compact" disabled={!capabilityToAddId} onClick={addDiscoveryCapability} type="button"><Plus size={14} /> Add</button>
            </div>
          </header>
          <div className="discovery-capability-matrix">
            {activeDiscoveryCapabilities.map((capability, index) => {
              const removable = capability.configurable && !capability.system_required;
              const status = capability.system_required ? "Required" : capability.configurable ? "Modifiable" : "Read only";
              return <article data-status={status.toLowerCase().replaceAll(" ", "-")} key={capability.capability_id}>
                <span aria-label={`Capability ${index + 1} of ${activeDiscoveryCapabilities.length}`} className="discovery-capability-index">{index + 1}</span>
                <div className="discovery-capability-copy"><strong>{capability.name}</strong><small>{capability.description}</small><span>{capability.provider} · {capability.timeframes.length ? capability.timeframes.join(", ") : "Service clock"}</span></div>
                <div className="discovery-capability-actions"><em>{status}</em>{removable ? <button aria-label={`Remove ${capability.name}`} className="button compact danger" onClick={() => setDiscoveryCapabilityEnabled(capability.capability_id, false)} type="button"><Trash2 size={13} /> Remove</button> : null}</div>
              </article>;
            })}
          </div>
        </> : null}
        {guidedStep === "watchlists" ? watchlistView === "select" ? <div className="discovery-watchlist-selection">
          <section className="discovery-watchlist-create" aria-label="Create a Watchlist">
            <span>Create a Watchlist</span>
            <button onClick={addWatchlist} type="button"><span><Plus size={17} /></span><span><strong>New Watchlist</strong><small>Build candidate membership from the Core Scan</small></span><ArrowRight size={15} /></button>
          </section>
          <section className="discovery-watchlist-available" aria-label="Available Watchlists">
            <header><span>Available Watchlists</span><h2>Choose a Watchlist to configure</h2><p>Open an existing Watchlist or create one above. Configuration starts after you choose.</p></header>
            <div>{section.watchlists.map((watchlist) => <article key={watchlist.watchlist_id}><span className="discovery-watchlist-card-icon"><ScanSearch size={17} /></span><span><strong>{watchlist.name}</strong><small>{watchlist.description || "No description yet."}</small><em>{watchlist.maximum_size} members · refreshes every {watchlist.refresh_interval_ms} ms</em></span><button className="button compact" onClick={() => configureWatchlist(watchlist.watchlist_id)} type="button">Configure <ArrowRight size={13} /></button></article>)}{section.watchlists.length === 0 ? <EmptyState title="No Watchlists configured" detail="Create a Watchlist to define how QMD narrows Core Scan candidates for strategies." /> : null}</div>
          </section>
          <p className="configuration-safety-note discovery-runtime-notice"><TriangleAlert size={15} /> Automatic causal membership resolution is not connected to trading runtime. Publication remains fail-closed and uses only explicit manual inclusions until the resolver is available.</p>
        </div> : selectedWatchlist ? <div className="discovery-watchlist-guide">
          <header className="discovery-watchlist-editor-toolbar"><button className="button compact" onClick={() => setWatchlistView("select")} type="button"><ArrowLeft size={14} /> All Watchlists</button><span><strong>{selectedWatchlist.name}</strong><small>Guided configuration</small></span></header>
          <div className="discovery-watchlist-step-navigation">
            <button aria-label="Previous Watchlist question" className="button compact discovery-watchlist-direction" disabled={watchlistQuestionIndex === 0} onClick={() => setWatchlistQuestionIndex((index) => Math.max(0, index - 1))} type="button"><ArrowLeft size={14} /> Previous</button>
            <nav aria-label="Watchlist configuration steps">{([['identity', '1', 'Identity'], ['rules', '2', 'Rules'], ['ranking', '3', 'Ranking'], ['timing', '4', 'Timing'], ['overrides', '5', 'Overrides'], ['calculations', '6', 'Calculations'], ['review', '7', 'Review']] as const).map(([id, number, label], index) => <button aria-current={watchlistQuestion === id ? "step" : undefined} key={id} onClick={() => setWatchlistQuestionIndex(index)} type="button"><span>{number}</span><strong>{label}</strong></button>)}</nav>
            <button aria-label={watchlistQuestion === "review" ? "Finish Watchlist configuration" : "Next Watchlist question"} className="button compact primary discovery-watchlist-direction" onClick={nextWatchlistQuestion} type="button">{watchlistQuestion === "review" ? "Done" : "Next"} <ArrowRight size={14} /></button>
          </div>
          <section className="discovery-watchlist-question">
            {watchlistQuestion === "identity" ? <><header><h2>Name and describe this Watchlist</h2></header><div className="strategy-identity-fields"><label className="strategy-identity-field"><span>Watchlist name</span><input onChange={(event) => replaceWatchlist({ ...selectedWatchlist, name: event.target.value })} value={selectedWatchlist.name} /><small>Strategies use this name when selecting their discovery source.</small></label><label className="strategy-identity-field"><span>Description</span><textarea onChange={(event) => replaceWatchlist({ ...selectedWatchlist, description: event.target.value })} rows={3} value={selectedWatchlist.description} /><small>Explain which candidates this Watchlist is intended to retain.</small></label><label className="configuration-field configuration-boolean"><span>Enabled</span><small>Controls whether this Watchlist is enabled in the published configuration. Runtime membership resolution remains fail-closed until its resolver is connected.</small><input checked={selectedWatchlist.enabled} onChange={(event) => replaceWatchlist({ ...selectedWatchlist, enabled: event.target.checked })} type="checkbox" /></label></div></> : null}
            {watchlistQuestion === "rules" ? <><header><h2>Which candidates may enter or must be excluded?</h2></header><WatchlistRuleChoices onChange={replaceWatchlist} ruleSets={section.rule_sets} watchlist={selectedWatchlist} /></> : null}
            {watchlistQuestion === "ranking" ? <><header><h2>How should passing candidates be ranked and limited?</h2></header><div className="configuration-field-grid"><SelectField help="QMD sorts passing candidates by this published observation before applying maximum membership." label="Ranking field" onChange={(ranking_field) => replaceWatchlist({ ...selectedWatchlist, ranking_field })} options={section.core_scan.calculations.filter((row) => row.enabled).map((row) => ({ description: row.description, label: row.name, value: row.capability_id }))} searchable value={selectedWatchlist.ranking_field} /><NumberField help="Maximum current membership after ranking and exclusions." label="Maximum members" minimum={1} onChange={(maximum_size) => replaceWatchlist({ ...selectedWatchlist, maximum_size })} step={1} unit="symbols" value={selectedWatchlist.maximum_size} /></div></> : null}
            {watchlistQuestion === "timing" ? <><header><h2>How often should membership be refreshed and expire?</h2></header><div className="configuration-field-grid"><NumberField help="How often QMD resolves the Watchlist from its source scan." label="Refresh interval" minimum={1} onChange={(refresh_interval_ms) => replaceWatchlist({ ...selectedWatchlist, refresh_interval_ms })} step={100} unit="ms" value={selectedWatchlist.refresh_interval_ms} /><NumberField help="How long membership remains valid without a confirming refresh. Zero means no automatic expiry." label="Membership TTL" minimum={0} onChange={(membership_ttl_ms) => replaceWatchlist({ ...selectedWatchlist, membership_ttl_ms })} step={1000} unit="ms" value={selectedWatchlist.membership_ttl_ms} /></div></> : null}
            {watchlistQuestion === "overrides" ? <><header><h2>Which symbols should always be included or excluded?</h2></header><div className="configuration-field-grid"><TextField help="Comma-separated symbols forced into membership. Every override is recorded in membership history." label="Manual inclusions" onChange={(value) => replaceWatchlist({ ...selectedWatchlist, manual_inclusions: value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean) })} value={selectedWatchlist.manual_inclusions.join(", ")} /><TextField help="Comma-separated symbols excluded after rules pass. Every override is recorded in membership history." label="Manual exclusions" onChange={(value) => replaceWatchlist({ ...selectedWatchlist, manual_exclusions: value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean) })} value={selectedWatchlist.manual_exclusions.join(", ")} /></div></> : null}
            {watchlistQuestion === "calculations" ? <><header><h2>Which focused calculations should run for members?</h2></header><fieldset className="configuration-choice-set discovery-calculation-choices"><legend>Focused calculations</legend><p>These higher-cost calculations run only for current Watchlist members when causal QMD membership resolution is available.</p><div>{section.core_scan.calculations.filter((row) => row.tier === "watchlist" && row.enabled).map((capability) => <label key={capability.capability_id}><input checked={selectedWatchlist.calculations.includes(capability.capability_id)} onChange={(event) => replaceWatchlist({ ...selectedWatchlist, calculations: event.target.checked ? [...selectedWatchlist.calculations, capability.capability_id] : selectedWatchlist.calculations.filter((id) => id !== capability.capability_id) })} type="checkbox" /><span><strong>{capability.name}</strong><small>{capability.description}</small></span></label>)}</div></fieldset></> : null}
            {watchlistQuestion === "review" ? <><header><h2>Review this Watchlist</h2></header><div className="discovery-watchlist-review"><article><span>Source</span><strong>{section.core_scan.name}</strong><small>Core Scan candidates</small></article><article><span>Membership</span><strong>Up to {selectedWatchlist.maximum_size} symbols</strong><small>Refreshes every {selectedWatchlist.refresh_interval_ms} ms</small></article><article><span>Rules</span><strong>{selectedWatchlist.inclusion_rule_sets.length} include · {selectedWatchlist.exclusion_rule_sets.length} exclude</strong><small>{selectedWatchlist.manual_inclusions.length} manual inclusions · {selectedWatchlist.manual_exclusions.length} manual exclusions</small></article><article><span>Focused work</span><strong>{selectedWatchlist.calculations.length} calculations</strong><small>{selectedWatchlist.enabled ? "Enabled in this configuration" : "Disabled in this configuration"}</small></article></div></> : null}
          </section>
        </div> : <EmptyState title="Watchlist unavailable" detail="Return to the Watchlist list and choose another configuration." /> : null}
        {guidedStep === "history" ? <><header className="strategy-identity-intro"><h2>Trace every Watchlist membership change</h2></header><div className="discovery-history-intro"><BadgeCheck size={20} /><div><strong>Append-only membership evidence contract</strong><p>Add, remove, expiry, and manual-override events retain the Watchlist identity, configuration snapshot, event and availability clocks, causal rule, rank, scores, and reason. Current membership will be a projection of this history once the causal resolver is connected.</p></div></div><div className="discovery-history-list">{section.watchlists.flatMap((watchlist) => watchlist.membership_history.map((event, index) => <article key={`${watchlist.watchlist_id}-${index}`}><strong>{String(event.ticker ?? "Unknown symbol")}</strong><span>{watchlist.name}</span><small>{String(event.reason ?? "Membership event")}</small></article>))}{section.watchlists.every((watchlist) => !watchlist.membership_history.length) ? <EmptyState title="No recorded membership events" detail="The runtime resolver is not connected, and session configuration does not fabricate history." /> : null}</div></> : null}
      </section>
    </article>}
  </div>;
}

function WatchlistRuleChoices({ onChange, ruleSets, watchlist }: { onChange: (value: WatchlistConfig) => void; ruleSets: RuleSetDefinition[]; watchlist: WatchlistConfig }) {
  const groups: Array<{ key: "inclusion_rule_sets" | "exclusion_rule_sets"; label: string; detail: string }> = [
    { key: "inclusion_rule_sets", label: "Inclusion rules", detail: "A candidate must pass the selected reusable evidence before ranking." },
    { key: "exclusion_rule_sets", label: "Exclusion rules", detail: "A passing exclusion removes the candidate even when inclusion rules pass." },
  ];
  return <div className="discovery-rule-choices">{groups.map((group) => <fieldset className="configuration-choice-set" key={group.key}><legend>{group.label}</legend><p>{group.detail}</p><div>{ruleSets.map((ruleSet) => <label key={ruleSet.rule_set_id}><input checked={watchlist[group.key].includes(ruleSet.rule_set_id)} onChange={(event) => onChange({ ...watchlist, [group.key]: event.target.checked ? [...watchlist[group.key], ruleSet.rule_set_id] : watchlist[group.key].filter((id) => id !== ruleSet.rule_set_id) })} type="checkbox" /><span><strong>{ruleSet.name}</strong><small>{ruleSet.description || `${ruleSet.conditions.length} configured conditions`}</small></span></label>)}</div></fieldset>)}</div>;
}

function StrategyStudio({ approved, draft, label, onChange, onDeleteProfile, onDraftChange, onLabelChange, onPublish, publishing, revisions, section }: {
  approved: Revision | null;
  draft: Draft;
  label: string;
  onChange: (value: StrategySection) => void;
  onDeleteProfile: (profileId: string) => Promise<Draft>;
  onDraftChange: (value: Draft) => void;
  onLabelChange: (value: string) => void;
  onPublish: (profileId: string) => void;
  publishing: boolean;
  revisions: Revision[];
  section: StrategySection;
}) {
  const [selectedId, setSelectedId] = useState(section.profiles[0]?.profile_id ?? "");
  const [studioView, setStudioView] = useState<"select" | "configure">("select");
  const [editorMode, setEditorMode] = useState<"catalog" | "guided">("guided");
  const [activeStage, setActiveStage] = useState<StrategyAuthoringStage>("identity");
  const [catalogItem, setCatalogItem] = useState<StrategyCatalogItem | null>(null);
  const [creationMode, setCreationMode] = useState<"blank" | null>(null);
  const [creationName, setCreationName] = useState("");
  const selected = section.profiles.find((row) => row.profile_id === selectedId) ?? section.profiles[0];
  useEffect(() => {
    if (!section.profiles.some((row) => row.profile_id === selectedId)) setSelectedId(section.profiles[0]?.profile_id ?? "");
  }, [section.profiles, selectedId]);
  if (!selected) return <EmptyState title="No Strategy Profiles" detail="Create a profile from a registered strategy definition." />;
  function replaceProfile(next: StrategyProfile) {
    onChange({ ...section, profiles: section.profiles.map((row) => row.profile_id === selected.profile_id ? next : row) });
  }

  function cloneProfileFromSelection(profileId: string) {
    const source = section.profiles.find((row) => row.profile_id === profileId);
    if (!source) return;
    const next = cloneStrategyProfile(source, section.profiles, uniqueProfileName(`${source.name} copy`, section.profiles));
    onChange({ ...section, profiles: [...section.profiles, next] });
    setSelectedId(next.profile_id);
    setCatalogItem(null);
    setActiveStage("identity");
    setEditorMode("guided");
    setStudioView("configure");
  }

  function beginProfileCreation() {
    setCreationMode("blank");
    setCreationName(uniqueProfileName("Untitled Strategy", section.profiles));
  }

  function createProfile() {
    const normalizedName = creationName.trim();
    if (!creationMode || !normalizedName || section.profiles.some((row) => row.name.trim().toLocaleLowerCase() === normalizedName.toLocaleLowerCase())) return;
    const next = { ...blankStrategyProfile(selected, draft), name: normalizedName };
    onChange({ ...section, profiles: [...section.profiles, next] });
    setSelectedId(next.profile_id);
    setActiveStage("identity");
    setEditorMode("guided");
    setStudioView("configure");
    setCreationMode(null);
    setCreationName("");
  }

  function publishSelected() {
    onPublish(selected.profile_id);
  }

  async function removeProfile(profileId = selected.profile_id) {
    const target = section.profiles.find((row) => row.profile_id === profileId);
    const targetFallback = section.profiles.find((row) => row.profile_id === section.default_profile_id && row.profile_id !== profileId)
      ?? section.profiles.find((row) => row.protected && row.profile_id !== profileId)
      ?? section.profiles.find((row) => row.profile_id !== profileId);
    if (!target || target.protected || target.profile_id === section.default_profile_id || !targetFallback) return;
    const confirmed = window.confirm(`Delete “${target.name}” permanently?`);
    if (!confirmed) return;
    try {
      const saved = await onDeleteProfile(profileId);
      setSelectedId(saved.strategy.default_profile_id);
      setCatalogItem(null);
      setStudioView("select");
    } catch {
      // The parent displays the backend error and preserves the current profile.
    }
  }

  const advanced = flattenPrimitives(selected.parameters).filter((row) => (
    !LEGACY_ENTRY_LOGIC_PATHS.has(row.path) && isDirectlyEditableStrategyParameter(row.path, row.value)
  ));
  const catalogParameters = strategyEditableParameters(selected);
  const entryRules = selected.lifecycle.initial_entry;
  const creationNameConflict = Boolean(creationName.trim()) && section.profiles.some((row) => row.name.trim().toLocaleLowerCase() === creationName.trim().toLocaleLowerCase());

  if (studioView === "select") return <StrategySelectionPage
    creationMode={creationMode}
    name={creationName}
    nameConflict={creationNameConflict}
    onCancel={() => { setCreationMode(null); setCreationName(""); }}
    onCreate={createProfile}
    onCreateStart={beginProfileCreation}
    onDelete={(profileId) => void removeProfile(profileId)}
    onClone={cloneProfileFromSelection}
    onNameChange={setCreationName}
    onModify={(profileId) => { setSelectedId(profileId); setCatalogItem(null); setActiveStage("identity"); setEditorMode("guided"); setStudioView("configure"); }}
    profiles={section.profiles}
  />;

  return (
    <div className="strategy-studio-workspace">
      <nav className="strategy-editor-toolbar" aria-label="Strategy editor navigation">
        <button className="button compact" onClick={() => { setCatalogItem(null); setStudioView("select"); }} type="button"><ArrowLeft size={14} /> All strategies</button>
        <span><strong>{selected.name}</strong><small>{editorMode === "catalog" ? "Parameter Catalog" : "Guided Configuration"}</small></span>
        <div className="strategy-editor-modes" role="tablist" aria-label="Strategy editor views">
          <button aria-selected={editorMode === "catalog"} onClick={() => setEditorMode("catalog")} role="tab" type="button"><Search size={14} /> Parameter Catalog</button>
          <button aria-selected={editorMode === "guided"} onClick={() => { setActiveStage("identity"); setEditorMode("guided"); }} role="tab" type="button"><BookOpenCheck size={14} /> Guided Configuration</button>
        </div>
      </nav>
      <div className={`configuration-workbench strategy-editor-${editorMode}`}>
      {editorMode === "catalog" ? <>
      <StrategyParameterCatalog catalog={section.input_catalog} parameters={catalogParameters} ruleSets={selected.rule_set_catalog} onSelect={setCatalogItem} selectedId={catalogItem?.id ?? null} />

      {catalogItem?.ruleSetId ? <StrategyRuleSetDetail catalog={section.input_catalog} onChange={(ruleSet) => replaceProfile({ ...selected, rule_set_catalog: selected.rule_set_catalog.map((row) => row.rule_set_id === ruleSet.rule_set_id ? ruleSet : row) })} ruleSet={selected.rule_set_catalog.find((row) => row.rule_set_id === catalogItem.ruleSetId)} /> : catalogItem ? <StrategyParameterDetail item={catalogItem} onChange={(value) => replaceProfile(setStrategyProfilePath(selected, catalogItem.parameter, value))} value={catalogParameters.find((row) => row.path === catalogItem.parameter)?.value} /> : <main className="strategy-parameter-empty-detail"><Search size={24} /><h2>Select a parameter or rule set</h2><p>Choose an item from the catalog to review and edit it.</p></main>}
      </> : <main className="configuration-detail">
        <StrategyAuthoringFlow
          activeStage={activeStage}
          advanced={advanced}
          approved={approved}
          draft={draft}
          entryRules={entryRules}
          label={label}
          onLabelChange={onLabelChange}
          onProfileChange={replaceProfile}
          onPublish={() => void publishSelected()}
          onRuleSetEdit={(ruleSetId, created) => { const ruleSet = created ?? selected.rule_set_catalog.find((row) => row.rule_set_id === ruleSetId); if (ruleSet) { setCatalogItem(strategyRuleSetCatalogItem(ruleSet, section.input_catalog)); setEditorMode("catalog"); } }}
          onStageChange={setActiveStage}
          profile={selected}
          publishing={publishing}
          revisions={revisions}
          section={section}
        />

        <details className="strategy-technical-book" hidden>
          <summary><span><strong>Complete technical configuration</strong><small>All Strategy, Run Plan, Portfolio, OMS, account, and release fields</small></span><ChevronRight size={15} /></summary>
        <article className="strategy-book" aria-label={`${selected.name} complete technical configuration`}>
          <header className="strategy-book-cover">
            <BookOpenCheck aria-hidden="true" size={24} />
            <div>
              <span>System configuration guide</span>
              <h2>How configuration becomes a governed Strategy Run</h2>
              <p>Start with the Strategy Definition, then configure each dependency in order. Every section explains which authority acts, what it receives, what it produces, and how its parameters change runtime behavior.</p>
            </div>
          </header>

          <StrategyMechanismOverview />

          <BookPart label="Part I" title="Configure strategy behavior" />

          <StoryChapter marker="01" eyebrow="Definition and profile" title="Configure the reusable strategy behavior">
            <div className="strategy-book-prose">
              <p>The Strategy Definition is tested code. The Strategy Profile supplies its configurable values and lifecycle rules. Profile parameters change how the engine evaluates evidence; they do not select accounts, allocate capital, resolve competing runs, or create orders. Availability controls whether new Run Plans may select this profile without deleting historical references.</p>
            </div>
            <BookConfigurationSurface label="Configure the reusable profile">
              <div className="strategy-story-fields compact">
                <BooleanField help="Allow new or edited Run Plans to select this configured Strategy Profile." label="Available for use" onChange={(enabled) => replaceProfile({ ...selected, enabled })} value={selected.enabled} />
              </div>
              {advanced.length ? (
                <details className="configuration-advanced strategy-story-advanced">
                  <summary><span><strong>Engine parameters</strong><small>Signal, protection, and implementation values read by the strategy code</small></span><ChevronRight size={15} /></summary>
                  <div className="configuration-field-grid">
                    {advanced.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} value={item.value} onChange={(value) => replaceProfile({ ...selected, parameters: setPath(selected.parameters, item.path, value) })} />)}
                  </div>
                </details>
              ) : null}
            </BookConfigurationSurface>
          </StoryChapter>

          <StoryChapter marker="02" eyebrow="Observation" title="Configure the strategy's market context">
            <div className="strategy-book-prose">
              <p>Each active rule listens to the sources and timeframes it references. When one of those sources publishes, the strategy evaluates the applicable entry, management, or exit expression from the causal snapshot. Eligible sessions restrict new entries, not protection of existing exposure. Side determines position direction and campaign ownership. Manual-position adoption permits the campaign to manage existing exposure without claiming that it generated the original entry.</p>
            </div>
            <BookConfigurationSurface label="Configure observation behavior">
              <TradingBehaviorEditor definition={section.definitions.find((row) => row.strategy_id === selected.definition_id)} profile={selected} onChange={replaceProfile} />
            </BookConfigurationSurface>
          </StoryChapter>

          <StoryChapter marker="03" eyebrow="Initial entry" title="Configure how evidence produces an entry request">
            <div className="strategy-book-prose">
              <p>Initial entry evaluates opportunity, then confirmation, then blockers. A passing result creates a Strategy Intent containing a relative capital request and broker-neutral execution preferences. It does not reserve cash or create an order. Run Plan authority, Portfolio approval, and OMS execution must still pass.</p>
            </div>
            <BookConfigurationSurface label="Configure the first entry">
              <DecisionRulesEditor catalog={section.input_catalog} rules={entryRules} title="Initial-entry evidence" summary="Opportunity, confirmation, and blockers are evaluated in that order from causal inputs." onChange={(value) => replaceProfile({ ...selected, lifecycle: { ...selected.lifecycle, initial_entry: { ...selected.lifecycle.initial_entry, ...value } } })} />
              <PhaseOrderEditor capitalRequest={selected.lifecycle.initial_entry.capital_request} eligibleSessions={selected.lifecycle.trading_behavior.eligible_sessions} orderIntent={selected.lifecycle.initial_entry.order_intent} title="Initial order request" executionPolicies={draft.oms.execution_policies} protectionProfiles={draft.oms.protection_profiles} onCapitalRequest={(capital_request) => replaceProfile({ ...selected, lifecycle: { ...selected.lifecycle, initial_entry: { ...selected.lifecycle.initial_entry, capital_request } } })} onOrderIntent={(order_intent) => replaceProfile({ ...selected, lifecycle: { ...selected.lifecycle, initial_entry: { ...selected.lifecycle.initial_entry, order_intent } } })} />
            </BookConfigurationSurface>
          </StoryChapter>

          <StoryChapter marker="04" eyebrow="Position lifecycle" title="Configure adds, reentry, capabilities, and strategic exits">
            <div className="strategy-book-prose">
              <p>Add steps request more exposure while a position is open. Reentry creates a new position after the campaign becomes flat. Capabilities enable optional code-defined behavior. Strategic exits reduce or close exposure when strategy evidence passes. All exposure-increasing actions repeat the authority and Portfolio checks; protective and emergency exits remain independent and automatic.</p>
            </div>
            <BookConfigurationSurface label="Configure position management, reentry, and exit">
              <AddStepsEditor catalog={section.input_catalog} eligibleSessions={selected.lifecycle.trading_behavior.eligible_sessions} executionPolicies={draft.oms.execution_policies} protectionProfiles={draft.oms.protection_profiles} steps={selected.lifecycle.initial_entry.add_steps} onChange={(add_steps) => replaceProfile({ ...selected, lifecycle: { ...selected.lifecycle, initial_entry: { ...selected.lifecycle.initial_entry, add_steps } } })} />
              <CapabilitiesEditor catalog={section.capability_catalog} profile={selected} onChange={replaceProfile} />
              <ReentryEditor catalog={section.input_catalog} draft={draft} profile={selected} onChange={replaceProfile} />
              <ExitRuleSetsEditor catalog={section.input_catalog} draft={draft} profile={selected} onChange={replaceProfile} />
            </BookConfigurationSurface>
          </StoryChapter>

          <BookPart label="Part II" title="Configure runtime authority and dependencies" />

          <StoryChapter marker="05" eyebrow="Accounts and sessions" title="Configure where positions and orders can exist">
            <div className="strategy-book-prose">
              <p>An account binding maps a stable application account key to a simulated or broker session. Modes define where the binding may be used. They do not prove connectivity or broker readiness. Paper and Live still require backend account discovery, capability checks, session health, and safety preflight.</p>
            </div>
            <BookConfigurationSurface label="Configure account bindings">
              <AccountsEditor draft={draft} onChange={(accounts) => onDraftChange({ ...draft, accounts })} />
            </BookConfigurationSurface>
          </StoryChapter>

          <StoryChapter marker="06" eyebrow="OMS and protection" title="Configure how approved quantity becomes broker orders">
            <div className="strategy-book-prose">
              <p>OMS converts Portfolio-approved quantity into broker-compatible orders, handles repricing and partial fills, reconciles broker state, and maintains protection. Execution parameters affect fill speed, price limits, and cancellation behavior. Protection parameters affect stops, targets, trailing transitions, and repair deadlines. Neither may increase Portfolio-approved exposure.</p>
            </div>
            <BookConfigurationSurface label="Configure OMS execution and protection">
              <OmsEditor section={draft.oms} onChange={(oms) => onDraftChange({ ...draft, oms })} />
            </BookConfigurationSurface>
          </StoryChapter>

          <StoryChapter marker="07" eyebrow="Run Plan" title="Connect behavior, universe, authority, and execution">
            <div className="strategy-book-prose">
              <p>The Run Plan selects the Strategy Profile, watch universe, OMS profile, portfolio book, environments, action authority, and campaign lifecycle. These values vary by launch context, so they do not belong in the reusable profile. The control plane arbitrates ticker campaigns; Portfolio arbitrates account resources. The Strategy Profile has no cross-run priority.</p>
            </div>
            <BookConfigurationSurface label="Configure Run Plans and watch universes">
              <DeploymentEditor draft={draft} onChange={(assignments) => onDraftChange({ ...draft, assignments })} />
            </BookConfigurationSurface>
          </StoryChapter>

          <StoryChapter marker="08" eyebrow="Portfolio and risk" title="Configure capital allocation and shared guardrails">
            <div className="strategy-book-prose">
              <p>A mandate links a Run Plan to an account and limits cash, planned risk, position count, assignment mode, and maximum action authority. Account policies and groups apply shared exposure, loss, and drawdown limits across runs. Portfolio uses current state and reservations to approve, reduce, reject, or replace requests; strategies cannot allocate capital to themselves.</p>
            </div>
            <BookConfigurationSurface label="Configure portfolio policies, mandates, groups, and safety">
              <PortfolioEditor draft={draft} onChange={(portfolio) => onDraftChange({ ...draft, portfolio })} />
            </BookConfigurationSurface>
          </StoryChapter>

          <StoryChapter marker="09" eyebrow="Approved release" title="Validate and freeze the complete runtime configuration">
            <div className="strategy-book-prose">
              <p>The browser session has no runtime authority. Publication validates all references and freezes Strategy, Run Plan, Portfolio, OMS, account, safety, and Canvas configuration into one release. Each new run pins one release; later session changes cannot alter an active run.</p>
            </div>
            <BookConfigurationSurface label="Review and publish the release">
              <RevisionPublisher approved={approved} draft={draft} label={label} onLabelChange={onLabelChange} onPublish={() => onPublish(selected.profile_id)} publishing={publishing} revisions={revisions} />
            </BookConfigurationSurface>
          </StoryChapter>

          <BookPart label="Part III" title="Runtime execution sequence" />

          <StoryChapter marker="10" eyebrow="Runtime flow" title="How the configured authorities process one decision">
            <div className="strategy-book-prose">
              <p>Launch resolves the approved release, Run Plan, mandates, accounts, and sessions. The control plane creates the run and manages ticker ownership. A configured trigger causes Strategy evaluation. Passing evidence creates an intent. Action authority may require operator confirmation. Portfolio returns an approved account quantity or a rejection. OMS executes only the approval and maintains protection.</p>
              <p>Adds and reentries repeat the same exposure-increasing path. Strategic exits use configured evidence and authority; protective and emergency exits bypass discretionary delay. Portfolio and the Safety Supervisor continuously enforce exposure, loss, drawdown, data, broker, and order-health limits. The run ends according to campaign policy and retains a linked audit record of every decision and state transition.</p>
            </div>
          </StoryChapter>
        </article>
        </details>
      </main>}
    </div>
    </div>
  );
}

function StrategySelectionPage({ creationMode, name, nameConflict, onCancel, onClone, onCreate, onCreateStart, onDelete, onModify, onNameChange, profiles }: {
  creationMode: "blank" | null;
  name: string;
  nameConflict: boolean;
  onCancel: () => void;
  onClone: (value: string) => void;
  onCreate: () => void;
  onCreateStart: () => void;
  onDelete: (value: string) => void;
  onModify: (value: string) => void;
  onNameChange: (value: string) => void;
  profiles: StrategyProfile[];
}) {
  return <main className="strategy-selection-page" aria-label="Choose a strategy">
    <section className="strategy-profile-command" aria-label="Create a strategy">
      <div className="strategy-profile-create-choice">
      <span>Create a strategy</span>
      <div role="group" aria-label="New strategy starting point">
        <button aria-pressed={creationMode === "blank"} onClick={onCreateStart} type="button"><Plus size={15} /><span><strong>Empty strategy</strong><small>Start with no active decisions</small></span></button>
      </div>
      </div>
      {creationMode ? <div className="strategy-profile-create-detail">
      <header><span>Empty strategy</span><strong>Create a strategy with disabled lifecycle decisions</strong></header>
      <TextField help={nameConflict ? "This name is already used." : "You can change it later."} label="Strategy name" onChange={onNameChange} value={name} />
      <div><button className="button" onClick={onCancel} type="button">Cancel</button><button className="button primary" disabled={!name.trim() || nameConflict} onClick={onCreate} type="button">Create strategy <ArrowRight size={14} /></button></div>
      </div> : null}
    </section>
    <header className="strategy-selection-heading"><span>Available strategies</span><h2>Choose a strategy to configure</h2><p>The protected template and every strategy you create appear here.</p></header>
    <section className="strategy-selection-list" aria-label="Available strategies">
      {profiles.map((profile) => <article key={profile.profile_id}>
        <span className="strategy-selection-icon"><GitBranch size={18} /></span>
        <span className="strategy-selection-copy">
          <span className="strategy-selection-identity"><strong>{profile.name}</strong><span className="strategy-selection-meta">{profile.publication_status === "published" ? "Published" : profile.publication_status === "template" ? "Template" : "Draft"}</span></span>
          <small>{profile.description || "No description"}</small>
        </span>
        <span className="strategy-selection-actions">
          {profile.publication_status === "draft" && profile.origin === "user" && profile.editable ? <button onClick={() => onModify(profile.profile_id)} type="button"><PencilLine size={14} /> Modify</button> : null}
          <button onClick={() => onClone(profile.profile_id)} type="button"><Clipboard size={14} /> Clone</button>
          {profile.publication_status === "draft" && profile.origin === "user" && !profile.protected ? <button aria-label={`Delete ${profile.name}`} className="danger" onClick={() => onDelete(profile.profile_id)} title="Delete permanently" type="button"><Trash2 size={14} /> Delete</button> : null}
        </span>
      </article>)}
    </section>
  </main>;
}

function strategyRuleSetCatalogItem(ruleSet: RuleSetDefinition, catalog: StrategyInput[]): StrategyCatalogItem {
  return { category: "Rule sets", detail: ruleSetMeaning(ruleSet, catalog), group: "Rule sets", groupOrder: -1, id: `rule-set:${ruleSet.rule_set_id}`, importance: 0, kind: "Rule set", label: ruleSet.name, metadata: [{ label: "Conditions", value: String(ruleSet.conditions.length) }, { label: "Condition logic", value: readableLabel(ruleSet.operator) }], parameter: "", ruleSetId: ruleSet.rule_set_id, usage: "Lifecycle expressions reference this definition by stable id. Editing it updates every stage that uses it." };
}

function StrategyParameterCatalog({ catalog, parameters, ruleSets, onSelect, selectedId }: { catalog: StrategyInput[]; parameters: Array<{ group: string; groupOrder: number; importance: number; path: string; value: CatalogParameterValue }>; ruleSets: RuleSetDefinition[]; onSelect: (item: StrategyCatalogItem) => void; selectedId: string | null }) {
  const [search, setSearch] = useState("");
  const items = useMemo(() => [...ruleSets.map((ruleSet) => strategyRuleSetCatalogItem(ruleSet, catalog)), ...parameters.map((parameter) => ({
      category: parameter.group,
      detail: helpForPath(parameter.path),
      group: parameter.group,
      groupOrder: parameter.groupOrder,
      id: `strategy:${parameter.path}`,
      importance: parameter.importance,
      kind: "Strategy parameter",
      label: strategyParameterLabel(parameter.path),
      metadata: [{ label: "Value type", value: parameter.value === null ? "unset" : typeof parameter.value }, { label: "Current value", value: parameter.value === null ? "Unset" : String(parameter.value) }],
      parameter: parameter.path,
      usage: "This value is part of the Strategy Profile and is read when the corresponding stage of the strategy lifecycle runs.",
    }))].sort((left, right) => left.groupOrder - right.groupOrder || left.importance - right.importance), [catalog, parameters, ruleSets]);
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const filtered = items.filter((item) => !normalizedSearch || [item.label, item.parameter, item.category, item.kind, item.detail, ...item.metadata.flatMap((row) => [row.label, row.value])].join(" ").toLocaleLowerCase().includes(normalizedSearch));
  const groups = filtered.reduce<Array<{ label: string; items: StrategyCatalogItem[] }>>((result, item) => {
    const existing = result.find((group) => group.label === item.group);
    if (existing) existing.items.push(item);
    else result.push({ label: item.group, items: [item] });
    return result;
  }, []);

  return <aside className="strategy-parameter-catalog">
    <header><div><span>Parameter catalog</span><strong>{filtered.length} of {items.length}</strong></div><small>Search every editable parameter in this Strategy Profile.</small></header>
    <label className="strategy-parameter-search"><Search size={14} /><input aria-label="Search strategy parameters" onChange={(event) => setSearch(event.target.value)} placeholder="Search parameters" type="search" value={search} /></label>
    <div className="strategy-parameter-list">
      {groups.map((group) => <section className="strategy-parameter-group" key={group.label}><header><strong>{group.label}</strong><span>{group.items.length}</span></header>{group.items.map((item) => <button aria-current={selectedId === item.id ? "true" : undefined} key={item.id} onClick={() => onSelect(item)} type="button"><span><strong>{item.label}</strong><small>{item.ruleSetId ? item.detail : `${item.kind} · ${readableLabel(item.category)}`}</small></span><ChevronRight size={13} /></button>)}</section>)}
      {!filtered.length ? <div className="strategy-parameter-empty"><Search size={16} /><span>No matching parameters</span></div> : null}
    </div>
  </aside>;
}

function StrategyRuleSetDetail({ catalog, onChange, ruleSet }: { catalog: StrategyInput[]; onChange: (value: RuleSetDefinition) => void; ruleSet?: RuleSetDefinition }) {
  if (!ruleSet) return <main className="strategy-parameter-empty-detail"><TriangleAlert size={24} /><h2>Rule set unavailable</h2><p>The selected lifecycle reference does not resolve to a catalog definition.</p></main>;
  const group: RuleGroup = { conditions: ruleSet.conditions, enabled: ruleSet.enabled, group_id: ruleSet.rule_set_id, label: ruleSet.name, operator: ruleSet.operator, required_score: ruleSet.required_score };
  return <main className="strategy-parameter-detail-page strategy-rule-set-detail">
    <section className="strategy-rule-set-authoring">
      <header className="strategy-identity-intro"><h2>{ruleSet.name}</h2>{ruleSet.description ? <p>{ruleSet.description}</p> : null}</header>
      <RuleSetMeaning catalog={catalog} ruleSet={ruleSet} />
      <div className="strategy-rule-set-editor"><RuleGroupEditor catalog={catalog} defaultOpen group={group} hideName onChange={(next) => onChange({ ...ruleSet, conditions: next.conditions, enabled: next.enabled, operator: next.operator, required_score: next.required_score })} onRemove={() => undefined} removable={false} /></div>
    </section>
  </main>;
}

function StrategyParameterDetail({ item, onChange, value }: { item: StrategyCatalogItem; onChange: (value: Primitive) => void; value: CatalogParameterValue | undefined }) {
  const fieldHelp: HelpContent = item.parameter.startsWith("lifecycle.phase_modes.") ? {
    role: "Choose whether Strategy owns this lifecycle decision.",
    values: {
      Automatic: "Strategy evaluates the saved phase configuration and may emit intent when it passes.",
      Manual: "Strategy skips this phase and emits no intent; the saved configuration remains available.",
    },
  } : item.detail;
  return <main className="strategy-parameter-detail-page">
    <header><span>{item.group}</span><h2>{item.label}</h2><p>{item.detail}</p></header>
    <section className="strategy-parameter-editor"><ParameterField definition={field(item.parameter, item.label, fieldHelp, controlFor(value ?? ""), choicesFor(item.parameter), unitFor(item.parameter), stepFor(value ?? ""))} onChange={onChange} value={value ?? ""} /></section>
    <ParameterDocumentation group={item.group} path={item.parameter} value={value} />
    <section className="strategy-parameter-reference"><div><span>Runtime parameter</span><strong>{item.parameter}</strong></div>{item.metadata.filter((row) => row.label !== "Current value").map((row) => <div key={`${item.id}-${row.label}`}><span>{row.label}</span><strong>{row.value}</strong></div>)}</section>
    <footer><Target size={18} /><div><strong>Runtime effect</strong><p>{item.usage}</p></div></footer>
  </main>;
}

function ParameterDocumentation({ documentation: suppliedDocumentation, group, path, value }: { documentation?: StrategyParameterDocumentation; group: string; path: string; value: CatalogParameterValue | undefined }) {
  const documentation = suppliedDocumentation ?? strategyParameterDocumentation(path, group, value);
  return <section aria-label="Parameter documentation" className="strategy-parameter-documentation">
    <header><BookOpenCheck size={18} /><div><span>Parameter guidance</span><h3>Understand this setting before changing it</h3></div></header>
    <div className="strategy-parameter-documentation-copy">
      <article data-tone="information"><CircleHelp size={18} /><div><strong>What it controls</strong>{documentation.role.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}</div></article>
      <article data-tone="behavior"><GitBranch size={18} /><div><strong>When it applies</strong>{documentation.timing.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}</div></article>
      <article data-tone="impact"><Target size={18} /><div><strong>What changes</strong>{documentation.impact.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}</div></article>
      <article data-tone={documentation.cautionTone}><TriangleAlert size={18} /><div><strong>Check before changing</strong>{documentation.caution.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}</div></article>
    </div>
  </section>;
}

function StrategyAuthoringFlow({ activeStage, advanced, approved, draft, entryRules, label, onLabelChange, onProfileChange, onPublish, onRuleSetEdit, onStageChange, profile, publishing, revisions, section }: {
  activeStage: StrategyAuthoringStage;
  advanced: Array<{ path: string; value: Primitive }>;
  approved: Revision | null;
  draft: Draft;
  entryRules: EntryRules & { add_steps: AddStep[]; capital_request: CapitalRequestConfig; order_intent: OrderIntentConfig };
  label: string;
  onLabelChange: (value: string) => void;
  onProfileChange: (value: StrategyProfile) => void;
  onPublish: () => void;
  onRuleSetEdit: (ruleSetId: string, created?: RuleSetDefinition) => void;
  onStageChange: (value: StrategyAuthoringStage) => void;
  profile: StrategyProfile;
  publishing: boolean;
  revisions: Revision[];
  section: StrategySection;
}) {
  const [activeEntryPage, setActiveEntryPage] = useState<EntryAuthoringPage>("mode");
  const [activeManagePage, setActiveManagePage] = useState<ManageAuthoringPage>("mode");
  const [activeReentryPage, setActiveReentryPage] = useState<ReentryAuthoringPage>("mode");
  const [activeExitPage, setActiveExitPage] = useState<ExitAuthoringPage>("mode");
  const [selectedAddStepId, setSelectedAddStepId] = useState(profile.lifecycle.initial_entry.add_steps[0]?.step_id ?? "");
  const [selectedExitRouteId, setSelectedExitRouteId] = useState(profile.lifecycle.exit.rule_sets[0]?.rule_set_id ?? "");
  const [selectedCapabilityId, setSelectedCapabilityId] = useState(profile.capabilities[0]?.capability_id ?? "");
  const enabledAdds = profile.lifecycle.initial_entry.add_steps.filter((step) => step.enabled).length;
  const definition = section.definitions.find((row) => row.strategy_id === profile.definition_id);
  const entryStopParameters = advanced.filter((item) => item.path.startsWith("protection.stop."));
  const trailingParameters = advanced.filter((item) => item.path.startsWith("protection.trailing."));
  const luldTargetParameters = advanced.filter((item) => item.path.startsWith("protection.luld_profit_target."));
  const profitPocketParameters = advanced.filter((item) => item.path.startsWith("profit_pocket."));
  const assignedParameterPaths = new Set([...entryStopParameters, ...trailingParameters, ...luldTargetParameters, ...profitPocketParameters].map((item) => item.path));
  const remainingParameters = advanced.filter((item) => !assignedParameterPaths.has(item.path));
  const stages: Array<[StrategyAuthoringStage, string, string, string]> = [
    ["identity", "1", "Identity", "Name and description"],
    ["discovery", "2", "Discovery", "Watchlist"],
    ["overview", "3", "Observe", "Market context"],
    ["entry", "4", "Enter", "Evidence and request"],
    ["position", "5", "Manage", "Adds and capabilities"],
    ["reentry", "6", "Reentry", "Flat-to-open rules"],
    ["exit", "7", "Exit", "Reduction conditions"],
    ["portfolio", "8", "Portfolio", "Capital and accounts"],
    ["oms", "9", "OMS", "Execution and protection"],
    ["authority", "10", "Authority", "Modes and confirmation"],
    ["handoff", "11", "Review", "Publish readiness"],
  ];
  const activeIndex = stages.findIndex(([stage]) => stage === activeStage);
  const activeEntryIndex = ENTRY_AUTHORING_PAGES.findIndex((page) => page.id === activeEntryPage);
  const activeEntry = ENTRY_AUTHORING_PAGES[activeEntryIndex];
  const activeEntryRuleStage = activeEntryPage === "opportunity" || activeEntryPage === "confirmation" || activeEntryPage === "blockers" ? activeEntryPage : null;
  const activeManageIndex = MANAGE_AUTHORING_PAGES.findIndex((page) => page.id === activeManagePage);
  const activeManage = MANAGE_AUTHORING_PAGES[activeManageIndex];
  const activeReentryIndex = REENTRY_AUTHORING_PAGES.findIndex((page) => page.id === activeReentryPage);
  const activeReentry = REENTRY_AUTHORING_PAGES[activeReentryIndex];
  const activeExitIndex = EXIT_AUTHORING_PAGES.findIndex((page) => page.id === activeExitPage);
  const activeExit = EXIT_AUTHORING_PAGES[activeExitIndex];
  const activeAddStep = entryRules.add_steps.find((step) => step.step_id === selectedAddStepId) ?? entryRules.add_steps[0];
  const activeExitRoute = profile.lifecycle.exit.rule_sets.find((route) => route.rule_set_id === selectedExitRouteId) ?? profile.lifecycle.exit.rule_sets[0];
  const activeCapabilityDefinition = section.capability_catalog.find((definition) => definition.capability_id === selectedCapabilityId) ?? section.capability_catalog[0];
  const activeCapabilityBinding = profile.capabilities.find((binding) => binding.capability_id === activeCapabilityDefinition?.capability_id);
  const isFinalQuestion = activeIndex === stages.length - 1;
  const phaseModes = profile.lifecycle.phase_modes;

  function replaceInitialEntry(value: Partial<StrategyLifecycle["initial_entry"]>) {
    onProfileChange({ ...profile, lifecycle: { ...profile.lifecycle, initial_entry: { ...profile.lifecycle.initial_entry, ...value } } });
  }

  function replaceAddStep(stepId: string, next: AddStep) {
    replaceInitialEntry({ add_steps: entryRules.add_steps.map((step) => step.step_id === stepId ? next : step) });
  }

  function addAddStep() {
    const stepId = uniqueId("position-add", entryRules.add_steps.map((step) => step.step_id));
    const evidenceRuleSet = profile.rule_set_catalog[0];
    const next: AddStep = {
      capital_request: { allow_replacement: false, mode: "mandate_fraction", value: 0.1 }, enabled: true, maximum_uses: 1,
      name: "New position add", order_intent: { deadline_ms: 750, execution_policy: "adaptive_urgent", partial_fill_policy: "complete_remainder", protection_profile: "hybrid-single" },
      rules: { expression: { children: evidenceRuleSet ? [{ kind: "rule_set", rule_set_id: evidenceRuleSet.rule_set_id }] : [], kind: "operator", operator: "and" } }, step_id: stepId,
    };
    replaceInitialEntry({ add_steps: [next, ...entryRules.add_steps] });
    setSelectedAddStepId(stepId);
  }

  function replaceReentry(next: StrategyLifecycle["reentry"]) {
    onProfileChange({ ...profile, lifecycle: { ...profile.lifecycle, reentry: next } });
  }

  function replacePhaseMode(phase: keyof StrategyLifecycle["phase_modes"], mode: StrategyPhaseMode) {
    onProfileChange({
      ...profile,
      lifecycle: {
        ...profile.lifecycle,
        phase_modes: { ...phaseModes, [phase]: mode },
        ...(phase === "reentry" ? { reentry: { ...profile.lifecycle.reentry, enabled: mode === "automatic" } } : {}),
      },
    });
  }

  function changeStage(stage: StrategyAuthoringStage) {
    if (stage === "entry") setActiveEntryPage("mode");
    if (stage === "position") setActiveManagePage("mode");
    if (stage === "reentry") setActiveReentryPage("mode");
    if (stage === "exit") setActiveExitPage("mode");
    onStageChange(stage);
  }

  function replaceExitRoute(routeId: string, next: ExitRuleSet) {
    onProfileChange({ ...profile, lifecycle: { ...profile.lifecycle, exit: { rule_sets: profile.lifecycle.exit.rule_sets.map((route) => route.rule_set_id === routeId ? next : route) } } });
  }

  function addExitRoute() {
    const ruleSetId = uniqueId("new-exit-rule", profile.lifecycle.exit.rule_sets.map((route) => route.rule_set_id));
    const evidenceRuleSet = profile.rule_set_catalog[0];
    const next: ExitRuleSet = {
      action: "close", enabled: true, name: "New strategic exit",
      order_intent: { deadline_ms: 750, execution_policy: "adaptive_urgent", partial_fill_policy: "complete_remainder", protection_profile: "hybrid-single" },
      position_fraction: 1, rule_set_id: ruleSetId,
      rules: { expression: { children: evidenceRuleSet ? [{ kind: "rule_set", rule_set_id: evidenceRuleSet.rule_set_id }] : [], kind: "operator", operator: "and" } },
      summary: "Describe when this exit becomes valid.", timing: { active_after_ms: 0, expires_after_ms: 0 },
    };
    onProfileChange({ ...profile, lifecycle: { ...profile.lifecycle, exit: { rule_sets: [next, ...profile.lifecycle.exit.rule_sets] } } });
    setSelectedExitRouteId(ruleSetId);
  }

  function previousQuestion() {
    if (activeStage === "entry" && activeEntryIndex > 0) {
      setActiveEntryPage(ENTRY_AUTHORING_PAGES[activeEntryIndex - 1].id);
      return;
    }
    if (activeStage === "position" && activeManageIndex > 0) {
      setActiveManagePage(MANAGE_AUTHORING_PAGES[activeManageIndex - 1].id);
      return;
    }
    if (activeStage === "reentry" && activeReentryIndex > 0) {
      setActiveReentryPage(REENTRY_AUTHORING_PAGES[activeReentryIndex - 1].id);
      return;
    }
    if (activeStage === "exit" && activeExitIndex > 0) {
      setActiveExitPage(EXIT_AUTHORING_PAGES[activeExitIndex - 1].id);
      return;
    }
    if (activeIndex > 0) changeStage(stages[activeIndex - 1][0]);
  }

  function nextQuestion() {
    if (
      (activeStage === "entry" && phaseModes.initial_entry === "manual")
      || (activeStage === "position" && phaseModes.manage === "manual")
      || (activeStage === "reentry" && phaseModes.reentry === "manual")
      || (activeStage === "exit" && phaseModes.exit === "manual")
    ) {
      if (activeIndex < stages.length - 1) changeStage(stages[activeIndex + 1][0]);
      return;
    }
    if (activeStage === "entry" && activeEntryIndex < ENTRY_AUTHORING_PAGES.length - 1) {
      setActiveEntryPage(ENTRY_AUTHORING_PAGES[activeEntryIndex + 1].id);
      return;
    }
    if (activeStage === "position" && activeManageIndex < MANAGE_AUTHORING_PAGES.length - 1) {
      setActiveManagePage(MANAGE_AUTHORING_PAGES[activeManageIndex + 1].id);
      return;
    }
    if (activeStage === "reentry" && activeReentryIndex < REENTRY_AUTHORING_PAGES.length - 1) {
      setActiveReentryPage(REENTRY_AUTHORING_PAGES[activeReentryIndex + 1].id);
      return;
    }
    if (activeStage === "exit" && activeExitIndex < EXIT_AUTHORING_PAGES.length - 1) {
      setActiveExitPage(EXIT_AUTHORING_PAGES[activeExitIndex + 1].id);
      return;
    }
    if (activeIndex < stages.length - 1) changeStage(stages[activeIndex + 1][0]);
  }

  return <article className="strategy-authoring" aria-label={`${profile.name} strategy authoring flow`}>
    <div className="strategy-authoring-step-navigation">
      <button aria-label="Previous configuration question" className="button compact strategy-step-direction strategy-step-direction-previous" disabled={activeIndex <= 0 && activeStage !== "entry" && activeStage !== "position" && activeStage !== "reentry" && activeStage !== "exit"} onClick={previousQuestion} type="button"><ArrowLeft aria-hidden="true" size={15} /><span>Previous</span></button>
      <nav aria-label="Strategy configuration steps" className="strategy-authoring-steps">
        {stages.map(([stage, number, title, detail]) => <button aria-current={activeStage === stage ? "step" : undefined} key={stage} onClick={() => changeStage(stage)} type="button"><span>{number}</span><strong>{title}</strong><small>{detail}</small></button>)}
      </nav>
      {isFinalQuestion ? <span /> : <button aria-label="Next configuration question" className="button compact primary strategy-step-direction strategy-step-direction-next" onClick={nextQuestion} type="button"><span>Next</span><ArrowRight aria-hidden="true" size={15} /></button>}
    </div>

    <section className={`strategy-authoring-stage${activeStage === "entry" || activeStage === "position" || activeStage === "reentry" || activeStage === "exit" ? " strategy-authoring-stage-entry" : ""}`}>
      {activeStage === "identity" ? <>
        <header className="strategy-identity-intro">
          <h2>Name and describe this strategy</h2>
        </header>
        <div className="strategy-identity-fields">
          <label className="strategy-identity-field">
            <span>Strategy name</span>
            <input autoComplete="off" className="strategy-identity-name" onChange={(event) => onProfileChange({ ...profile, name: event.target.value })} value={profile.name} />
            <small>This name identifies the strategy in selection, publication, and runtime views.</small>
          </label>
          <label className="strategy-identity-field">
            <span>Strategy description</span>
            <textarea className="strategy-identity-description" onChange={(event) => onProfileChange({ ...profile, description: event.target.value })} rows={4} value={profile.description} />
            <small>Summarize the setup, intended market behavior, and purpose.</small>
          </label>
        </div>
      </> : null}
      {activeStage === "discovery" ? <>
        <header className="strategy-identity-intro"><h2>Which Watchlist should this strategy evaluate?</h2></header>
        <div className="strategy-connection-surface">
          <SelectField help={{ role: "Selects the QMD-owned candidate membership evaluated for new entries.", values: Object.fromEntries(draft.market_discovery.watchlists.map((watchlist) => [watchlist.name, watchlist.description || "QMD resolves this Watchlist from the Core Scan."])), note: "Leaving a Watchlist blocks new entries but never abandons an open position; management, protection, and exits continue until safe close." }} label="Watchlist" onChange={(watchlist_id) => onProfileChange({ ...profile, composition: { ...profile.composition, watchlist_id } })} options={draft.market_discovery.watchlists.map((watchlist) => ({ label: watchlist.name, value: watchlist.watchlist_id }))} value={profile.composition.watchlist_id} />
          {draft.market_discovery.watchlists.filter((watchlist) => watchlist.watchlist_id === profile.composition.watchlist_id).map((watchlist) => <article className="strategy-connection-summary" key={watchlist.watchlist_id}><ScanSearch size={20} /><div><strong>{watchlist.name}</strong><p>{watchlist.description}</p><span>{watchlist.maximum_size} maximum members · resolves every {watchlist.refresh_interval_ms} ms · {watchlist.calculations.length} focused calculations</span></div></article>)}
        </div>
      </> : null}
      {activeStage === "overview" ? <>
        <header className="strategy-identity-intro strategy-observe-intro"><h2>What market context does the strategy use?</h2></header>
        <div className="strategy-observe-fields"><TradingBehaviorEditor definition={definition} profile={profile} onChange={onProfileChange} /></div>
      </> : null}

      {activeStage === "entry" ? <>
        <header className="strategy-identity-intro strategy-entry-intro"><h2>{activeEntry.title}</h2><p>{activeEntry.description}</p></header>
        <div className="strategy-entry-layout">
          <div className="strategy-entry-question-surface">
            {activeEntryPage === "mode" ? <StrategyPhaseModeEditor mode={phaseModes.initial_entry} onChange={(mode) => replacePhaseMode("initial_entry", mode)} phase="Initial entry" /> : null}
            {activeEntryRuleStage ? <DecisionRulesEditor catalog={section.input_catalog} onChange={(value) => replaceInitialEntry(value)} onRuleSetEdit={onRuleSetEdit} onRuleSetsChange={(rule_set_catalog, nextRules) => onProfileChange({ ...profile, lifecycle: { ...profile.lifecycle, initial_entry: { ...profile.lifecycle.initial_entry, ...nextRules } }, rule_set_catalog })} ruleSetCatalog={profile.rule_set_catalog} rules={entryRules} stageName={activeEntryRuleStage} title="Initial-entry evidence" summary="" /> : null}
            {activeEntryPage === "capital" ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => replaceInitialEntry({ capital_request })} segment="amount" value={entryRules.capital_request} /></div> : null}
            {activeEntryPage === "priority" ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => replaceInitialEntry({ capital_request })} segment="priority" value={entryRules.capital_request} /></div> : null}
            {activeEntryPage === "execution" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceInitialEntry({ order_intent })} segment="execution" value={entryRules.order_intent} /></div> : null}
            {activeEntryPage === "partial_fill" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceInitialEntry({ order_intent })} segment="partial-fill" value={entryRules.order_intent} /></div> : null}
            {activeEntryPage === "protection" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceInitialEntry({ order_intent })} segment="protection" value={entryRules.order_intent} /></div> : null}
            {activeEntryPage === "initial_stop" ? <div className="configuration-field-grid strategy-entry-engine-fields">{entryStopParameters.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").at(-1) ?? item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} onChange={(value) => onProfileChange({ ...profile, parameters: setPath(profile.parameters, item.path, value) })} value={item.value} />)}{!entryStopParameters.length ? <EmptyState detail="This strategy definition does not expose initial-stop engine parameters." title="No initial-stop parameters" /> : null}</div> : null}
          </div>
          <nav aria-label="Initial entry questions" className="strategy-entry-navigation">
            {ENTRY_AUTHORING_PAGES.map((page, index) => <button aria-current={page.id === activeEntryPage ? "step" : undefined} aria-label={page.label} disabled={phaseModes.initial_entry === "manual" && page.id !== "mode"} key={page.id} onClick={() => setActiveEntryPage(page.id)} title={page.label} type="button"><span>{index + 1}</span><strong>{page.label}</strong></button>)}
          </nav>
        </div>
      </> : null}

      {activeStage === "position" ? <>
        <header className="strategy-identity-intro strategy-entry-intro"><h2>{activeManage.title}</h2><p>{activeManage.description}</p></header>
        <ManageAuthoringSurface activeAddStep={activeAddStep} activeCapabilityBinding={activeCapabilityBinding} activeCapabilityDefinition={activeCapabilityDefinition} activePage={activeManagePage} draft={draft} enabledAdds={enabledAdds} entryRules={entryRules} mode={phaseModes.manage} onAddStep={addAddStep} onModeChange={(mode) => replacePhaseMode("manage", mode)} onPageChange={setActiveManagePage} onProfileChange={onProfileChange} onReplaceAddStep={replaceAddStep} onReplaceInitialEntry={replaceInitialEntry} onRuleSetEdit={onRuleSetEdit} onSelectedAddStepChange={setSelectedAddStepId} onSelectedCapabilityChange={setSelectedCapabilityId} profile={profile} section={section} trailingParameters={trailingParameters} />
      </> : null}

      {activeStage === "reentry" ? <>
        <header className="strategy-identity-intro strategy-entry-intro"><h2>{activeReentry.title}</h2><p>{activeReentry.description}</p></header>
        <ReentryAuthoringSurface activePage={activeReentryPage} draft={draft} mode={phaseModes.reentry} onModeChange={(mode) => replacePhaseMode("reentry", mode)} onPageChange={setActiveReentryPage} onProfileChange={onProfileChange} onReplaceReentry={replaceReentry} onRuleSetEdit={onRuleSetEdit} profile={profile} section={section} />
      </> : null}

      {activeStage === "exit" ? <>
        <header className="strategy-identity-intro strategy-entry-intro"><h2>{activeExit.title}</h2><p>{activeExit.description}</p></header>
        <ExitAuthoringSurface activePage={activeExitPage} activeRoute={activeExitRoute} catalog={section.input_catalog} draft={draft} luldTargetParameters={luldTargetParameters} mode={phaseModes.exit} onAddRoute={addExitRoute} onModeChange={(mode) => replacePhaseMode("exit", mode)} onPageChange={setActiveExitPage} onProfileChange={onProfileChange} onReplaceRoute={replaceExitRoute} onRuleSetEdit={onRuleSetEdit} onSelectedRouteChange={setSelectedExitRouteId} profile={profile} profitPocketParameters={profitPocketParameters} />
      </> : null}

      {activeStage === "portfolio" ? <>
        <header className="strategy-identity-intro"><h2>Which Portfolio policy and accounts may fund this strategy?</h2></header>
        <div className="strategy-connection-surface">
          <SelectField help="Portfolio remains the sole authority for sizing, shared-capital arbitration, reservations, exposure, loss, and drawdown limits." label="Portfolio policy" onChange={(portfolio_policy_id) => onProfileChange({ ...profile, composition: { ...profile.composition, portfolio_policy_id } })} options={draft.portfolio.policies.map((policy) => ({ label: String(policy.name || policy.policy_id), value: String(policy.policy_id) }))} value={profile.composition.portfolio_policy_id} />
          <fieldset className="configuration-choice-set strategy-account-choices"><legend>Eligible accounts</legend><div>{draft.accounts.bindings.map((account) => <label key={account.account_key}><input checked={profile.composition.account_keys.includes(account.account_key)} onChange={(event) => onProfileChange({ ...profile, composition: { ...profile.composition, account_keys: event.target.checked ? [...profile.composition.account_keys, account.account_key] : profile.composition.account_keys.filter((key) => key !== account.account_key) } })} type="checkbox" /><span><strong>{account.name}</strong><small>{readableLabel(account.account_class)} · {account.modes.map(readableLabel).join(", ")}</small></span></label>)}</div></fieldset>
        </div>
      </> : null}

      {activeStage === "oms" ? <>
        <header className="strategy-identity-intro"><h2>Which OMS configuration should execute approved intent?</h2></header>
        <div className="strategy-connection-surface">
          <SelectField help={{ role: "Selects the reusable execution, routing, partial-fill, and protection defaults used after Portfolio approves quantity.", values: Object.fromEntries(draft.oms.profiles.map((oms) => [oms.name, oms.description])), note: "OMS cannot create intent or increase Portfolio's approved quantity." }} label="OMS and protection" onChange={(oms_profile_id) => onProfileChange({ ...profile, composition: { ...profile.composition, oms_profile_id } })} options={draft.oms.profiles.map((oms) => ({ label: oms.name, value: oms.profile_id }))} value={profile.composition.oms_profile_id} />
          {draft.oms.profiles.filter((oms) => oms.profile_id === profile.composition.oms_profile_id).map((oms) => <article className="strategy-connection-summary" key={oms.profile_id}><ShieldCheck size={20} /><div><strong>{oms.name}</strong><p>{oms.description}</p><span>{readableLabel(oms.settings.entry_urgency)} entry · {readableLabel(oms.settings.exit_urgency)} exit · {readableLabel(oms.settings.session_routing)} routing</span></div></article>)}
        </div>
      </> : null}

      {activeStage === "authority" ? <>
        <header className="strategy-identity-intro"><h2>Where may this strategy run and who confirms its actions?</h2></header>
        <div className="strategy-connection-surface">
          <fieldset className="configuration-choice-set"><legend>Permitted environments</legend><ModeChoices onChange={(values) => onProfileChange({ ...profile, composition: { ...profile.composition, allowed_environments: values as RuntimeMode[] } })} options={["replay", "backtest", "backtest_debug", "paper", "live"]} values={profile.composition.allowed_environments} /></fieldset>
          <SelectField help="This is the default exposure-increasing action authority. Phase-specific automatic/manual modes still determine whether Strategy evaluates that phase. Portfolio and mandatory safety may always reduce or reject exposure." label="Default action authority" onChange={(value) => onProfileChange({ ...profile, composition: { ...profile.composition, action_authority: { ...profile.composition.action_authority, default: value as StrategyRunPlan["action_authority"]["default"] } } })} options={[{ label: "Manual", value: "manual" }, { label: "Confirm", value: "confirm" }, { label: "Automatic", value: "automatic" }]} value={profile.composition.action_authority.default} />
          <div className="strategy-safety-lock"><LockKeyhole size={19} /><div><strong>Live guardrails cannot be disabled</strong><p>Portfolio limits, broker and data health, protective exits, and emergency authority remain mandatory in Live. Replay and Backtest may explicitly disable selected non-production checks.</p></div></div>
        </div>
      </> : null}

      {activeStage === "handoff" ? <>
        <StrategyStageIntro title="Review the complete strategy before publication">Publication freezes this Strategy and every connected configuration. It cannot be modified afterward; use Clone to create a new draft derived from it.</StrategyStageIntro>
        <div className="strategy-publication-review">
          <article><span>Watchlist</span><strong>{draft.market_discovery.watchlists.find((row) => row.watchlist_id === profile.composition.watchlist_id)?.name ?? "Not selected"}</strong></article>
          <article><span>Portfolio</span><strong>{String(draft.portfolio.policies.find((row) => String(row.policy_id) === profile.composition.portfolio_policy_id)?.name ?? profile.composition.portfolio_policy_id)}</strong></article>
          <article><span>OMS</span><strong>{draft.oms.profiles.find((row) => row.profile_id === profile.composition.oms_profile_id)?.name ?? "Not selected"}</strong></article>
          <article><span>Accounts</span><strong>{profile.composition.account_keys.length} selected</strong></article>
          <article><span>Environments</span><strong>{profile.composition.allowed_environments.map(readableLabel).join(", ")}</strong></article>
          <article><span>Default authority</span><strong>{readableLabel(profile.composition.action_authority.default)}</strong></article>
        </div>
        <div className="strategy-safety-lock"><BadgeCheck size={19} /><div><strong>{profile.publication_status === "published" ? "Published and immutable" : "Unpublished strategy"}</strong><p>{profile.publication_status === "published" ? "Runtime results remain permanently associated with this identity. Clone it to make changes." : "Changes remain in this browser session. Publish from the final review to retain and activate them for new runs."}</p></div></div>
        {profile.publication_status !== "published" ? <RevisionPublisher approved={approved} draft={draft} guided label={label} onLabelChange={onLabelChange} onPublish={onPublish} publishing={publishing} revisions={revisions} /> : null}
        <StrategyEngineParameterGroup items={remainingParameters} onChange={(path, value) => onProfileChange({ ...profile, parameters: setPath(profile.parameters, path, value) })} summary="Definition-specific values not assigned to another lifecycle step" title="Other engine parameters" />
      </> : null}
    </section>
  </article>;
}

function ManageAuthoringSurface({ activeAddStep, activeCapabilityBinding, activeCapabilityDefinition, activePage, draft, enabledAdds, entryRules, mode, onAddStep, onModeChange, onPageChange, onProfileChange, onReplaceAddStep, onReplaceInitialEntry, onRuleSetEdit, onSelectedAddStepChange, onSelectedCapabilityChange, profile, section, trailingParameters }: {
  activeAddStep?: AddStep;
  activeCapabilityBinding?: CapabilityBinding;
  activeCapabilityDefinition?: CapabilityDefinition;
  activePage: ManageAuthoringPage;
  draft: Draft;
  enabledAdds: number;
  entryRules: StrategyLifecycle["initial_entry"];
  mode: StrategyPhaseMode;
  onAddStep: () => void;
  onModeChange: (mode: StrategyPhaseMode) => void;
  onPageChange: (page: ManageAuthoringPage) => void;
  onProfileChange: (value: StrategyProfile) => void;
  onReplaceAddStep: (stepId: string, next: AddStep) => void;
  onReplaceInitialEntry: (value: Partial<StrategyLifecycle["initial_entry"]>) => void;
  onRuleSetEdit: (ruleSetId: string, created?: RuleSetDefinition) => void;
  onSelectedAddStepChange: (stepId: string) => void;
  onSelectedCapabilityChange: (capabilityId: string) => void;
  profile: StrategyProfile;
  section: StrategySection;
  trailingParameters: Array<{ path: string; value: Primitive }>;
}) {
  const addPageWithoutAction = activePage.startsWith("add_") && activePage !== "add_actions" && !activeAddStep;
  return <div className="strategy-entry-layout strategy-lifecycle-layout">
    <div className="strategy-entry-question-surface">
      {activePage === "mode" ? <StrategyPhaseModeEditor mode={mode} onChange={onModeChange} phase="Position management" /> : null}
      {activePage === "add_actions" ? <div className="strategy-guided-entity-list">
        <header><span>{enabledAdds} enabled</span><button className="button compact" onClick={onAddStep} type="button"><Plus size={14} /> Add action</button></header>
        {entryRules.add_steps.map((step) => <article data-selected={activeAddStep?.step_id === step.step_id ? "true" : "false"} key={step.step_id}>
          <div className="guided-form-grid"><TextField help="Operator-facing name for this position-building action." label="Action name" onChange={(name) => onReplaceAddStep(step.step_id, { ...step, name })} value={step.name} /><NumberField help="Maximum confirmed fills from this action during one campaign." label="Maximum uses" minimum={1} onChange={(maximum_uses) => onReplaceAddStep(step.step_id, { ...step, maximum_uses })} step={1} unit="fills" value={step.maximum_uses} /></div>
          <BooleanField help="Disabled actions remain saved but cannot emit an add request." label="Enabled" onChange={(enabled) => onReplaceAddStep(step.step_id, { ...step, enabled })} value={step.enabled} />
          <div className="strategy-guided-entity-actions"><button className="button compact" onClick={() => { onSelectedAddStepChange(step.step_id); onPageChange("add_evidence"); }} type="button">Configure</button><button className="button compact danger" onClick={() => onReplaceInitialEntry({ add_steps: entryRules.add_steps.filter((row) => row.step_id !== step.step_id) })} type="button"><Trash2 size={14} /> Remove</button></div>
        </article>)}
        {!entryRules.add_steps.length ? <EmptyState detail="Create an action before configuring its evidence, capital request, and execution." title="No position-add actions" /> : null}
      </div> : null}
      {activePage === "add_evidence" && activeAddStep ? <RuleStageComposition catalog={section.input_catalog} label={`${activeAddStep.name} evidence`} onChange={(rules) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, rules })} onEditRuleSet={onRuleSetEdit} ruleSets={profile.rule_set_catalog} stage={activeAddStep.rules} /> : null}
      {activePage === "add_capital" && activeAddStep ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, capital_request })} segment="amount" value={activeAddStep.capital_request} /></div> : null}
      {activePage === "add_replacement" && activeAddStep ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, capital_request })} segment="priority" value={activeAddStep.capital_request} /></div> : null}
      {activePage === "add_execution" && activeAddStep ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, order_intent })} segment="execution" value={activeAddStep.order_intent} /></div> : null}
      {activePage === "add_partial_fill" && activeAddStep ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, order_intent })} segment="partial-fill" value={activeAddStep.order_intent} /></div> : null}
      {activePage === "add_protection" && activeAddStep ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, order_intent })} segment="protection" value={activeAddStep.order_intent} /></div> : null}
      {addPageWithoutAction ? <EmptyState detail="Return to Add actions and create an action first." title="No add action selected" /> : null}
      {activePage === "trailing" ? <div className="configuration-field-grid strategy-entry-engine-fields">{trailingParameters.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").at(-1) ?? item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} onChange={(value) => onProfileChange({ ...profile, parameters: setPath(profile.parameters, item.path, value) })} value={item.value} />)}{!trailingParameters.length ? <EmptyState detail="This strategy definition does not expose trailing parameters." title="No trailing parameters" /> : null}</div> : null}
      {activePage === "capabilities" ? <div className="strategy-entry-fields strategy-capability-focus"><SelectField help="Choose one optional code-defined behavior to review." label="Capability" onChange={onSelectedCapabilityChange} options={section.capability_catalog.map((definition) => ({ label: definition.name, value: definition.capability_id }))} value={activeCapabilityDefinition?.capability_id ?? ""} />{activeCapabilityDefinition && activeCapabilityBinding ? <GuidedCapabilityFields binding={activeCapabilityBinding} definition={activeCapabilityDefinition} onChange={(binding) => onProfileChange(updateCapability(profile, binding.capability_id, binding))} /> : <EmptyState detail="This profile has no configurable capability binding." title="Capability unavailable" />}</div> : null}
    </div>
    <nav aria-label="Position management questions" className="strategy-entry-navigation strategy-lifecycle-navigation">{MANAGE_AUTHORING_PAGES.map((page, index) => <button aria-current={page.id === activePage ? "step" : undefined} aria-label={page.label} disabled={mode === "manual" && page.id !== "mode"} key={page.id} onClick={() => onPageChange(page.id)} title={page.label} type="button"><span>{index + 1}</span><strong>{page.label}</strong></button>)}</nav>
  </div>;
}

function StrategyPhaseModeEditor({ mode, onChange, phase }: {
  mode: StrategyPhaseMode;
  onChange: (mode: StrategyPhaseMode) => void;
  phase: "Initial entry" | "Position management" | "Reentry" | "Strategic exit";
}) {
  const choices: Array<{ detail: string; icon: typeof Sparkles; label: string; value: StrategyPhaseMode }> = [
    {
      detail: `Strategy evaluates the configured ${phase.toLowerCase()} rules and may emit intent when they pass.`,
      icon: Sparkles,
      label: "Automatic",
      value: "automatic",
    },
    {
      detail: `Strategy does not evaluate or emit ${phase.toLowerCase()} intent. Its saved configuration is preserved.`,
      icon: PencilLine,
      label: "Manual",
      value: "manual",
    },
  ];
  return <div className="strategy-phase-mode-editor">
    <div aria-label={`${phase} mode`} className="strategy-phase-mode-options" role="radiogroup">
      {choices.map((choice) => {
        const Icon = choice.icon;
        return <button aria-checked={mode === choice.value} className="strategy-phase-mode-choice" data-selected={mode === choice.value ? "true" : "false"} key={choice.value} onClick={() => onChange(choice.value)} role="radio" type="button">
          <Icon aria-hidden="true" size={20} />
          <span><strong>{choice.label}</strong><small>{choice.detail}</small></span>
          <span aria-hidden="true" className="strategy-phase-mode-indicator"><Check size={14} /></span>
        </button>;
      })}
    </div>
    <div className="strategy-phase-mode-guidance"><ShieldCheck aria-hidden="true" size={18} /><p>{phase === "Strategic exit" ? "Broker-held protection, emergency exits, and account safety remain active in both modes." : "The configured action authority still decides whether emitted intent executes automatically or requires confirmation."}</p></div>
  </div>;
}

function ReentryAuthoringSurface({ activePage, draft, mode, onModeChange, onPageChange, onProfileChange, onReplaceReentry, onRuleSetEdit, profile, section }: {
  activePage: ReentryAuthoringPage;
  draft: Draft;
  mode: StrategyPhaseMode;
  onModeChange: (mode: StrategyPhaseMode) => void;
  onPageChange: (page: ReentryAuthoringPage) => void;
  onProfileChange: (value: StrategyProfile) => void;
  onReplaceReentry: (value: StrategyLifecycle["reentry"]) => void;
  onRuleSetEdit: (ruleSetId: string, created?: RuleSetDefinition) => void;
  profile: StrategyProfile;
  section: StrategySection;
}) {
  const reentry = profile.lifecycle.reentry;
  return <div className="strategy-entry-layout strategy-lifecycle-layout">
    <div className="strategy-entry-question-surface">
      {activePage === "mode" ? <StrategyPhaseModeEditor mode={mode} onChange={onModeChange} phase="Reentry" /> : null}
      {activePage === "reentry_policy" ? <div className="strategy-entry-fields"><div className="guided-form-grid"><BooleanField help="Require confirmation evidence newer than the evidence used by the previous confirmed entry." label="Require new confirmation" onChange={(require_new_confirmation) => onReplaceReentry({ ...reentry, require_new_confirmation })} value={reentry.require_new_confirmation} /><NumberField help="Minimum time after a confirmed full exit before reentry may become eligible." label="Cooldown" minimum={0} onChange={(cooldown_ms) => onReplaceReentry({ ...reentry, cooldown_ms })} step={100} unit="ms" value={reentry.cooldown_ms} /><NumberField help="Maximum confirmed reentry fills during one ticker campaign." label="Maximum attempts" minimum={0} onChange={(maximum_attempts) => onReplaceReentry({ ...reentry, maximum_attempts })} step={1} unit="entries" value={reentry.maximum_attempts} /></div></div> : null}
      {activePage === "reentry_opportunity" ? <DecisionRulesEditor catalog={section.input_catalog} onChange={(rules) => onReplaceReentry({ ...reentry, rules })} onRuleSetEdit={onRuleSetEdit} onRuleSetsChange={(rule_set_catalog, rules) => onProfileChange({ ...profile, lifecycle: { ...profile.lifecycle, reentry: { ...reentry, rules } }, rule_set_catalog })} ruleSetCatalog={profile.rule_set_catalog} rules={reentry.rules} stageName="opportunity" summary="" title="Reentry evidence" /> : null}
      {activePage === "reentry_confirmation" ? <DecisionRulesEditor catalog={section.input_catalog} onChange={(rules) => onReplaceReentry({ ...reentry, rules })} onRuleSetEdit={onRuleSetEdit} onRuleSetsChange={(rule_set_catalog, rules) => onProfileChange({ ...profile, lifecycle: { ...profile.lifecycle, reentry: { ...reentry, rules } }, rule_set_catalog })} ruleSetCatalog={profile.rule_set_catalog} rules={reentry.rules} stageName="confirmation" summary="" title="Reentry evidence" /> : null}
      {activePage === "reentry_blockers" ? <DecisionRulesEditor catalog={section.input_catalog} onChange={(rules) => onReplaceReentry({ ...reentry, rules })} onRuleSetEdit={onRuleSetEdit} onRuleSetsChange={(rule_set_catalog, rules) => onProfileChange({ ...profile, lifecycle: { ...profile.lifecycle, reentry: { ...reentry, rules } }, rule_set_catalog })} ruleSetCatalog={profile.rule_set_catalog} rules={reentry.rules} stageName="blockers" summary="" title="Reentry evidence" /> : null}
      {activePage === "reentry_capital" ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => onReplaceReentry({ ...reentry, capital_request })} segment="amount" value={reentry.capital_request} /></div> : null}
      {activePage === "reentry_replacement" ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => onReplaceReentry({ ...reentry, capital_request })} segment="priority" value={reentry.capital_request} /></div> : null}
      {activePage === "reentry_execution" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceReentry({ ...reentry, order_intent })} segment="execution" value={reentry.order_intent} /></div> : null}
      {activePage === "reentry_partial_fill" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceReentry({ ...reentry, order_intent })} segment="partial-fill" value={reentry.order_intent} /></div> : null}
      {activePage === "reentry_protection" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceReentry({ ...reentry, order_intent })} segment="protection" value={reentry.order_intent} /></div> : null}
    </div>
    <nav aria-label="Reentry questions" className="strategy-entry-navigation strategy-lifecycle-navigation">{REENTRY_AUTHORING_PAGES.map((page, index) => <button aria-current={page.id === activePage ? "step" : undefined} aria-label={page.label} disabled={mode === "manual" && page.id !== "mode"} key={page.id} onClick={() => onPageChange(page.id)} title={page.label} type="button"><span>{index + 1}</span><strong>{page.label}</strong></button>)}</nav>
  </div>;
}

function ExitAuthoringSurface({ activePage, activeRoute, catalog, draft, luldTargetParameters, mode, onAddRoute, onModeChange, onPageChange, onProfileChange, onReplaceRoute, onRuleSetEdit, onSelectedRouteChange, profile, profitPocketParameters }: {
  activePage: ExitAuthoringPage;
  activeRoute?: ExitRuleSet;
  catalog: StrategyInput[];
  draft: Draft;
  luldTargetParameters: Array<{ path: string; value: Primitive }>;
  mode: StrategyPhaseMode;
  onAddRoute: () => void;
  onModeChange: (mode: StrategyPhaseMode) => void;
  onPageChange: (page: ExitAuthoringPage) => void;
  onProfileChange: (value: StrategyProfile) => void;
  onReplaceRoute: (routeId: string, next: ExitRuleSet) => void;
  onRuleSetEdit: (ruleSetId: string, created?: RuleSetDefinition) => void;
  onSelectedRouteChange: (routeId: string) => void;
  profile: StrategyProfile;
  profitPocketParameters: Array<{ path: string; value: Primitive }>;
}) {
  const routeRequired = activePage !== "mode" && activePage !== "targets" && activePage !== "profit_pocket" && activePage !== "routes";
  return <div className="strategy-entry-layout strategy-lifecycle-layout">
    <div className="strategy-entry-question-surface">
      {activePage === "mode" ? <StrategyPhaseModeEditor mode={mode} onChange={onModeChange} phase="Strategic exit" /> : null}
      {activePage === "targets" ? <div className="configuration-field-grid strategy-entry-engine-fields">{luldTargetParameters.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").at(-1) ?? item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} onChange={(value) => onProfileChange({ ...profile, parameters: setPath(profile.parameters, item.path, value) })} value={item.value} />)}{!luldTargetParameters.length ? <EmptyState detail="This strategy definition does not expose LULD target parameters." title="No LULD target parameters" /> : null}</div> : null}
      {activePage === "profit_pocket" ? <div className="configuration-field-grid strategy-entry-engine-fields">{profitPocketParameters.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").at(-1) ?? item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} onChange={(value) => onProfileChange({ ...profile, parameters: setPath(profile.parameters, item.path, value) })} value={item.value} />)}{!profitPocketParameters.length ? <EmptyState detail="This strategy definition does not expose profit-pocket parameters." title="No profit-pocket parameters" /> : null}</div> : null}
      {activePage === "routes" ? <div className="strategy-guided-entity-list"><header><span>{profile.lifecycle.exit.rule_sets.filter((route) => route.enabled).length} enabled</span><button className="button compact" onClick={onAddRoute} type="button"><Plus size={14} /> Add route</button></header>{profile.lifecycle.exit.rule_sets.map((route) => <article data-selected={activeRoute?.rule_set_id === route.rule_set_id ? "true" : "false"} key={route.rule_set_id}><div className="guided-form-grid"><TextField help="Operator-facing name for this strategic exit route." label="Route name" onChange={(name) => onReplaceRoute(route.rule_set_id, { ...route, name })} value={route.name} /><TextField help="State the market condition and purpose handled by this route." label="Purpose" onChange={(summary) => onReplaceRoute(route.rule_set_id, { ...route, summary })} value={route.summary} /></div><BooleanField help="Disabled routes remain saved but cannot emit an exit request." label="Enabled" onChange={(enabled) => onReplaceRoute(route.rule_set_id, { ...route, enabled })} value={route.enabled} /><div className="strategy-guided-entity-actions"><button className="button compact" onClick={() => { onSelectedRouteChange(route.rule_set_id); onPageChange("evidence"); }} type="button">Configure</button><button className="button compact danger" disabled={profile.lifecycle.exit.rule_sets.length <= 1} onClick={() => onProfileChange({ ...profile, lifecycle: { ...profile.lifecycle, exit: { rule_sets: profile.lifecycle.exit.rule_sets.filter((row) => row.rule_set_id !== route.rule_set_id) } } })} type="button"><Trash2 size={14} /> Remove</button></div></article>)}</div> : null}
      {activePage === "evidence" && activeRoute ? <RuleStageComposition catalog={catalog} label={`${activeRoute.name} evidence`} onChange={(rules) => onReplaceRoute(activeRoute.rule_set_id, { ...activeRoute, rules })} onEditRuleSet={onRuleSetEdit} ruleSets={profile.rule_set_catalog} stage={activeRoute.rules} /> : null}
      {activePage === "timing" && activeRoute ? <div className="strategy-entry-fields"><GuidedExitTimingFields onChange={(route) => onReplaceRoute(activeRoute.rule_set_id, route)} value={activeRoute} /></div> : null}
      {activePage === "action" && activeRoute ? <div className="strategy-entry-fields"><GuidedExitActionFields onChange={(route) => onReplaceRoute(activeRoute.rule_set_id, route)} value={activeRoute} /></div> : null}
      {activePage === "execution" && activeRoute ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceRoute(activeRoute.rule_set_id, { ...activeRoute, order_intent })} segment="execution" value={activeRoute.order_intent} /></div> : null}
      {activePage === "partial_fill" && activeRoute ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceRoute(activeRoute.rule_set_id, { ...activeRoute, order_intent })} segment="partial-fill" value={activeRoute.order_intent} /></div> : null}
      {activePage === "protection" && activeRoute ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceRoute(activeRoute.rule_set_id, { ...activeRoute, order_intent })} segment="protection" value={activeRoute.order_intent} /></div> : null}
      {routeRequired && !activeRoute ? <EmptyState detail="Return to Exit routes and create a route first." title="No exit route selected" /> : null}
    </div>
    <nav aria-label="Strategic exit questions" className="strategy-entry-navigation strategy-lifecycle-navigation">{EXIT_AUTHORING_PAGES.map((page, index) => <button aria-current={page.id === activePage ? "step" : undefined} aria-label={page.label} disabled={mode === "manual" && page.id !== "mode"} key={page.id} onClick={() => onPageChange(page.id)} title={page.label} type="button"><span>{index + 1}</span><strong>{page.label}</strong></button>)}</nav>
  </div>;
}

function StrategyStageIntro({ children, hideDescription = false, title }: { children: ReactNode; hideDescription?: boolean; title: string }) {
  return <header className="strategy-stage-intro"><h2>{title}</h2><p aria-hidden={hideDescription || undefined} data-layout-placeholder={hideDescription || undefined}>{children}</p></header>;
}

function StrategyEngineParameterGroup({ items, onChange, summary, title }: { items: Array<{ path: string; value: Primitive }>; onChange: (path: string, value: Primitive) => void; summary: string; title: string }) {
  if (!items.length) return null;
  return <details className="configuration-advanced strategy-authoring-advanced strategy-engine-parameter-group"><summary><span><strong>{title}</strong><small>{summary} · {items.length} parameters</small></span><ChevronRight size={15} /></summary><div className="configuration-field-grid">{items.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").at(-1) ?? item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} value={item.value} onChange={(value) => onChange(item.path, value)} />)}</div></details>;
}

function StrategyHandoffLinks({ draft, profile }: { draft: Draft; profile: StrategyProfile }) {
  const runPlans = draft.assignments.deployments.filter((plan) => plan.profile_id === profile.profile_id);
  const mandateCount = draft.portfolio.mandates.filter((mandate) => runPlans.some((plan) => plan.run_plan_id === mandate.run_plan_id)).length;
  const links = [
    { detail: `${runPlans.length} linked`, href: "#assignment-configuration", icon: Network, outcome: "Selects universe, action authority, environment, OMS profile, and campaign ownership.", title: "Run Plan" },
    { detail: `${mandateCount} linked`, href: "#portfolio-configuration", icon: WalletCards, outcome: "Chooses the account and approves, reduces, or rejects requested exposure.", title: "Portfolio & Risk" },
    { detail: `${draft.oms.profiles.length} profiles`, href: "#oms-configuration", icon: Send, outcome: "Turns approved quantity into broker orders and maintains protection.", title: "OMS & Protection" },
    { detail: `${draft.accounts.bindings.filter((account) => account.enabled).length} enabled`, href: "#account-configuration", icon: Boxes, outcome: "Provides the runtime account identity, session, mode, and broker capabilities.", title: "Accounts & Sessions" },
    { detail: "Required", href: "#revision-configuration", icon: LockKeyhole, outcome: "Freezes all referenced revisions. A new run pins this release and cannot change underneath itself.", title: "Approved release" },
  ];
  return <div className="strategy-handoff-links">{links.map(({ detail, href, icon: Icon, outcome, title }) => <a href={href} key={title}><span><Icon size={18} /></span><div><strong>{title}</strong><p>{outcome}</p></div><small>{detail}</small><ChevronRight size={15} /></a>)}</div>;
}

function StoryChapter({ children, eyebrow, marker, title }: { children: ReactNode; eyebrow: string; marker: string; title: string }) {
  return (
    <section className="strategy-story-chapter">
      <div aria-hidden="true" className="strategy-story-marker">{marker}</div>
      <div className="strategy-story-chapter-body">
        <header><span>{eyebrow}</span><h2>{title}</h2></header>
        {children}
      </div>
    </section>
  );
}

function BookPart({ label, title }: { label: string; title: string }) {
  return (
    <header className="strategy-book-part"><span>{label}</span><h2>{title}</h2></header>
  );
}

function StrategyMechanismOverview() {
  return (
    <section className="strategy-mechanism-overview" aria-label="Strategy configuration and runtime mechanism">
      <header><span>Runtime path</span><h2>A strategy decides; other authorities permit and execute</h2><p>Every exposure-increasing action follows this path. Failure at any step stops the action.</p></header>
      <div className="strategy-mechanism-grid">
        <figure className="strategy-mechanism-diagram">
          <div className="strategy-mechanism-flow runtime-flow">
            <MechanismNode detail="Rules pass" icon={GitBranch} label="Strategy intent" tone="strategy" />
            <MechanismArrow label="Run Plan permits" />
            <MechanismNode detail="Account quantity" icon={BriefcaseBusiness} label="Portfolio approval" tone="portfolio" />
            <MechanismArrow label="OMS executes" />
            <MechanismNode detail="Orders and stops" icon={Send} label="Broker state" tone="oms" />
          </div>
          <div className="strategy-mechanism-safety"><ShieldCheck size={16} /><div><strong>Portfolio and Safety Supervisor remain active</strong><span>Account exposure, loss, drawdown, market-data, broker, and order-health failures can block new risk or trigger emergency action.</span></div></div>
        </figure>
      </div>
    </section>
  );
}

function MechanismNode({ detail, icon: Icon, label, tone }: { detail: string; icon: typeof GitBranch; label: string; tone: string }) {
  return <div className="strategy-mechanism-node" data-tone={tone}><Icon aria-hidden="true" size={16} /><span><strong>{label}</strong><small>{detail}</small></span></div>;
}

function MechanismArrow({ label }: { label: string }) {
  return <div aria-hidden="true" className="strategy-mechanism-arrow"><small>{label}</small><ArrowRight size={15} /></div>;
}

function BookConfigurationSurface({ children, label }: { children: ReactNode; label: string }) {
  return (
    <section className="strategy-book-configuration">
      <header><PencilLine aria-hidden="true" size={15} /><span>{label}</span></header>
      <div>{children}</div>
    </section>
  );
}

function TradingBehaviorEditor({ definition, onChange, profile }: {
  definition?: StrategySection["definitions"][number];
  onChange: (value: StrategyProfile) => void;
  profile: StrategyProfile;
}) {
  const behavior = profile.lifecycle.trading_behavior;
  const supportedSides = definition?.supported_sides?.length ? definition.supported_sides : ["long"];
  const update = (next: StrategyLifecycle["trading_behavior"]) => onChange({
    ...profile,
    lifecycle: { ...profile.lifecycle, trading_behavior: next },
  });
  const sessions = ["premarket", "regular", "after_hours"];
  return (
    <>
      <div className="configuration-field-grid">
        <SelectField
          help={{
            role: "Sets the campaign direction and reserves ticker ownership for that side.",
            values: {
              Long: "May open or add long exposure and exits by selling.",
              Short: "May open or add short exposure and exits by buying to cover. A current shortability check remains mandatory.",
            },
            note: "A ticker may have one long and one short campaign in the same portfolio book, but a single brokerage account cannot hold both because positions are netted.",
          }}
          label="Side"
          onChange={(side) => update({ ...behavior, side: side as StrategyLifecycle["trading_behavior"]["side"] })}
          options={supportedSides.map((value) => ({ label: readableLabel(value), value }))}
          value={behavior.side}
        />
        <div aria-label="Eligible sessions" className="configuration-field configuration-session-field" role="group">
          <span>Eligible sessions</span>
          <div>{sessions.map((session) => (
            <label key={session}>
              <input
                checked={behavior.eligible_sessions.includes(session)}
                onChange={(event) => update({
                  ...behavior,
                  eligible_sessions: event.target.checked
                    ? [...behavior.eligible_sessions, session]
                    : behavior.eligible_sessions.filter((value) => value !== session),
                })}
                type="checkbox"
              />
              {readableLabel(session)}
            </label>
          ))}</div>
        </div>
      </div>
    </>
  );
}

function ReentryEditor({ catalog, draft, onChange, onRuleSetEdit = () => undefined, profile }: {
  catalog: StrategyInput[];
  draft: Draft;
  onChange: (value: StrategyProfile) => void;
  onRuleSetEdit?: (ruleSetId: string, created?: RuleSetDefinition) => void;
  profile: StrategyProfile;
}) {
  const reentry = profile.lifecycle.reentry;
  const update = (next: StrategyLifecycle["reentry"]) => onChange({
    ...profile,
    lifecycle: { ...profile.lifecycle, reentry: next },
  });
  const mode = profile.lifecycle.phase_modes.reentry;
  const updateMode = (nextMode: string) => onChange({
    ...profile,
    lifecycle: {
      ...profile.lifecycle,
      phase_modes: { ...profile.lifecycle.phase_modes, reentry: nextMode as StrategyPhaseMode },
      reentry: { ...reentry, enabled: nextMode === "automatic" },
    },
  });
  return (
    <>
      <ConfigurationNarrative heading="Reentry" paragraphs={[
        "Reentry is a new flat-to-open decision after a confirmed full exit. Cooldown delays eligibility, maximum attempts bounds repeated exposure, and fresh confirmation prevents reuse of the evidence that supported the previous entry. Reentry has independent evidence, capital, execution, and protection settings.",
      ]} />
      <SelectField help={{ role: "Choose whether Strategy evaluates and emits reentry intent.", values: { Automatic: "Evaluate the saved reentry configuration.", Manual: "Preserve the configuration but skip reentry evaluation." } }} label="Reentry mode" onChange={updateMode} options={[{ label: "Automatic", value: "automatic" }, { label: "Manual", value: "manual" }]} value={mode} />
      {mode === "automatic" ? <><PhaseOrderEditor
        capitalRequest={reentry.capital_request}
        eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions}
        orderIntent={reentry.order_intent}
        title="Reentry order request"
        executionPolicies={draft.oms.execution_policies}
        protectionProfiles={draft.oms.protection_profiles}
        onCapitalRequest={(capital_request) => update({ ...reentry, capital_request })}
        onOrderIntent={(order_intent) => update({ ...reentry, order_intent })}
      />
      <p className="configuration-section-guide">A reentry occurs only after a full exit while the same Strategy Campaign retains ticker ownership. Adding to an open position is a capability, not a reentry.</p>
      <div className="configuration-field-grid">
        <BooleanField help="Evidence used for the previous entry cannot be reused without a newer causal update." label="Require new confirmation" onChange={(require_new_confirmation) => update({ ...reentry, require_new_confirmation })} value={reentry.require_new_confirmation} />
        <NumberField help="Minimum time after a confirmed full exit before reentry becomes eligible." label="Cooldown" minimum={0} onChange={(cooldown_ms) => update({ ...reentry, cooldown_ms })} step={100} unit="ms" value={reentry.cooldown_ms} />
        <NumberField help="Maximum reentries during one ticker campaign. Zero allows only the initial entry." label="Maximum attempts" minimum={0} onChange={(maximum_attempts) => update({ ...reentry, maximum_attempts })} step={1} unit="entries" value={reentry.maximum_attempts} />
      </div>
      <DecisionRulesEditor
        catalog={catalog}
        onChange={(rules) => update({ ...reentry, rules })}
        onRuleSetEdit={onRuleSetEdit}
        onRuleSetsChange={(rule_set_catalog) => onChange({ ...profile, rule_set_catalog })}
        ruleSetCatalog={profile.rule_set_catalog}
        rules={reentry.rules}
        title="When a reentry becomes eligible"
        summary="Reentry owns an independent rule set. Import selected initial-entry groups as editable copies, then add reentry-only evidence as needed."
      /></> : null}
    </>
  );
}

function ExitRuleSetsEditor({ catalog, draft, onChange, onRuleSetEdit = () => undefined, profile }: {
  catalog: StrategyInput[];
  draft: Draft;
  onChange: (value: StrategyProfile) => void;
  onRuleSetEdit?: (ruleSetId: string, created?: RuleSetDefinition) => void;
  profile: StrategyProfile;
}) {
  const routes = profile.lifecycle.exit.rule_sets;
  function replace(routeId: string, next: ExitRuleSet) {
    onChange({
      ...profile,
      lifecycle: {
        ...profile.lifecycle,
        exit: { rule_sets: routes.map((row) => row.rule_set_id === routeId ? next : row) },
      },
    });
  }
  function addRuleSet() {
    const ruleSetId = uniqueId("new-exit-rule", routes.map((row) => row.rule_set_id));
    const evidenceRuleSet = profile.rule_set_catalog[0];
    onChange({
      ...profile,
      lifecycle: {
        ...profile.lifecycle,
        exit: {
          rule_sets: [{
            action: "close",
            enabled: true,
            name: "New strategic exit",
            order_intent: { deadline_ms: 750, execution_policy: "adaptive_urgent", partial_fill_policy: "complete_remainder", protection_profile: "hybrid-single" },
            position_fraction: 1,
            rule_set_id: ruleSetId,
            rules: { expression: { children: evidenceRuleSet ? [{ kind: "rule_set", rule_set_id: evidenceRuleSet.rule_set_id }] : [], kind: "operator", operator: "and" } },
            summary: "Describe when this exit becomes valid.",
            timing: { active_after_ms: 0, expires_after_ms: 0 },
          }, ...routes],
        },
      },
    });
  }
  const deployment = draft.assignments.deployments.find((row) => row.profile_id === profile.profile_id);
  const omsProfile = draft.oms.profiles.find((row) => row.profile_id === deployment?.oms_profile_id);
  const protectionProfile = draft.oms.protection_profiles.find((row) => row.profile_id === omsProfile?.settings.protection_profile_id);
  return (
    <div className="strategy-exit-routes">
      <ConfigurationNarrative heading="Strategic exits" paragraphs={[
        "Strategic exit routes are checked in order. Each route defines its evidence, active window, expiry, position fraction, and OMS execution intent. These routes may wait for configured authority; broker-held protection and emergency liquidation do not.",
      ]} />
      <div className="configuration-protection-authority">
        <ShieldCheck size={19} />
        <div>
          <span>OMS safety authority</span>
          <strong>Protective stop is independent of strategic exit rules</strong>
          <p>OMS calculates, submits, repairs, and reconciles broker-held protection. Strategy rules cannot disable or delay it. {protectionProfile ? `${protectionProfile.name} protects ${protectionProfile.slices.length} independent slice${protectionProfile.slices.length === 1 ? "" : "s"}, applies ${readableLabel(protectionProfile.add_policy)} to adds, and uses ${readableLabel(protectionProfile.profit_pocket_transition)} after profit fills.` : "Select an OMS and protection profile in the Run Plan to resolve the exact plan."}</p>
          <a href="#oms-configuration">Configure OMS protection</a>
        </div>
      </div>
      <div className="strategy-exit-heading"><p className="configuration-section-guide">Each exit route composes predefined catalog rule sets, then adds its own validity window, position action, and OMS order request.</p><button className="button compact" onClick={addRuleSet} type="button"><Plus size={14} /> Add exit route</button></div>
      {routes.map((ruleSet) => (
        <details className="strategy-exit-route" data-enabled={ruleSet.enabled ? "true" : "false"} key={ruleSet.rule_set_id}>
          <summary>
            <div><span>Strategic exit · {ruleSet.action}</span><strong>{ruleSet.name}</strong><p>{ruleSet.summary}</p></div>
            <label className="configuration-switch" onClick={(event) => event.stopPropagation()} title="Enable exit rule set"><input checked={ruleSet.enabled} onChange={(event) => replace(ruleSet.rule_set_id, { ...ruleSet, enabled: event.target.checked })} type="checkbox" /><span /></label>
            <ChevronDown size={17} />
          </summary>
          <div className="strategy-exit-route-body">
            <ConfigurationNarrative heading={ruleSet.name} paragraphs={[
              "Evidence determines when this route passes. Active-after delays evaluation; expires-after stops evaluation; action and position fraction determine requested reduction. The result remains an intent processed by Run Plan authority and OMS.",
            ]} />
            <div className="strategy-exit-rule-meta"><label className="strategy-rule-name"><span>Rule set name</span><input onChange={(event) => replace(ruleSet.rule_set_id, { ...ruleSet, name: event.target.value })} value={ruleSet.name} /></label><label><span>Purpose</span><input onChange={(event) => replace(ruleSet.rule_set_id, { ...ruleSet, summary: event.target.value })} value={ruleSet.summary} /></label><button aria-label={`Delete ${ruleSet.name}`} className="button compact danger" disabled={routes.length <= 1} onClick={() => onChange({ ...profile, lifecycle: { ...profile.lifecycle, exit: { rule_sets: routes.filter((row) => row.rule_set_id !== ruleSet.rule_set_id) } } })} type="button"><Trash2 size={14} /></button></div>
            <RuleStageComposition catalog={catalog} label={`${ruleSet.name} evidence`} onChange={(rules) => replace(ruleSet.rule_set_id, { ...ruleSet, rules })} onEditRuleSet={onRuleSetEdit} ruleSets={profile.rule_set_catalog} stage={ruleSet.rules} />
            <div className="configuration-field-grid">
              <NumberField help={{ role: "Delay from confirmed entry until this rule set becomes eligible.", values: { "0 ms": "Active immediately.", "Positive value": "Matching evidence is ignored until this delay passes." } }} label="Active after" minimum={0} onChange={(active_after_ms) => replace(ruleSet.rule_set_id, { ...ruleSet, timing: { ...ruleSet.timing, active_after_ms } })} step={1000} unit="ms" value={ruleSet.timing.active_after_ms} />
              <NumberField help={{ role: "Maximum time this exit condition remains eligible after confirmed entry.", values: { "0 ms": "Never expires while the position is open.", "Positive value": "Stops evaluating after this duration." }, note: "For a failed-breakout condition, 60,000 ms means evidence arriving after one minute no longer qualifies for this route." }} label="Expires after" minimum={0} onChange={(expires_after_ms) => replace(ruleSet.rule_set_id, { ...ruleSet, timing: { ...ruleSet.timing, expires_after_ms } })} step={1000} unit="ms" value={ruleSet.timing.expires_after_ms} />
              <SelectField help={{ role: "Position intent emitted when the evidence and timing pass.", values: { "Close position": "Request the entire current position.", "Reduce position": "Request only the configured fraction." } }} label="Action" onChange={(action) => replace(ruleSet.rule_set_id, { ...ruleSet, action: action as ExitRuleSet["action"] })} options={[{ label: "Close position", value: "close" }, { label: "Reduce position", value: "reduce" }]} value={ruleSet.action} />
              {ruleSet.action === "reduce" ? <NumberField help={{ role: "Fraction of the current position to release.", values: { "1.0": "The full position.", "Below 1.0": "A partial reduction; the remainder stays managed." } }} label="Position fraction" maximum={1} minimum={0.01} onChange={(position_fraction) => replace(ruleSet.rule_set_id, { ...ruleSet, position_fraction })} step={0.05} unit="fraction" value={ruleSet.position_fraction} /> : null}
            </div>
            <OrderIntentEditor
              eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions}
              executionPolicies={draft.oms.execution_policies}
              protectionProfiles={draft.oms.protection_profiles}
              value={ruleSet.order_intent}
              onChange={(order_intent) => replace(ruleSet.rule_set_id, { ...ruleSet, order_intent })}
            />
          </div>
        </details>
      ))}
    </div>
  );
}

function CapabilitiesEditor({ catalog, onChange, profile }: {
  catalog: CapabilityDefinition[];
  onChange: (value: StrategyProfile) => void;
  profile: StrategyProfile;
}) {
  return (
    <>
      <ConfigurationNarrative heading="Capabilities" paragraphs={[
        "Capabilities enable optional code-defined functions for this Profile revision. Their parameters change only that function. A capability cannot bypass entry, reentry, exit, Portfolio, OMS, or safety authority.",
      ]} />
      <p className="configuration-section-guide">Capabilities are code-defined reusable functions attached to a campaign lifecycle. They may extend position management or Order Entry, but they do not replace Initial Entry, Reentry, or Exit.</p>
      <div className="capability-grid">
        {catalog.map((definition) => {
          const binding = profile.capabilities.find((row) => row.capability_id === definition.capability_id);
          if (!binding) return null;
          return (
            <article className="capability-card" data-enabled={binding.enabled ? "true" : "false"} key={definition.capability_id}>
              <header>
                <div><span>{definition.category.replaceAll("_", " ")}</span><strong>{definition.name}</strong></div>
                <label className="configuration-switch"><input checked={binding.enabled} onChange={(event) => onChange(updateCapability(profile, binding.capability_id, { ...binding, enabled: event.target.checked }))} type="checkbox" /><span /></label>
              </header>
              <ConfigurationNarrative heading={definition.name} paragraphs={[`${definition.summary} Its values apply only when this capability is enabled and are pinned with the Profile revision.`]} />
              <p>{definition.summary}</p>
              {definition.order_entry_action ? <em><BadgeCheck size={12} /> Appears in Order Entry</em> : null}
              {binding.enabled ? (
                <div className="capability-fields">
                  {definition.parameters.map((parameter) => (
                    <CapabilityField
                      definition={parameter}
                      key={parameter.key}
                      value={binding.settings[parameter.key]}
                      onChange={(value) => onChange(updateCapability(profile, binding.capability_id, {
                        ...binding,
                        settings: { ...binding.settings, [parameter.key]: value },
                      }))}
                    />
                  ))}
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </>
  );
}

const RULE_STAGE_META = {
  opportunity: {
    label: "Opportunity conditions",
    summary: "Evidence that identifies a possible initial entry.",
  },
  confirmation: {
    label: "Confirmation requirements",
    summary: "Each rule set owns its own condition logic and optional required score.",
  },
  blockers: {
    label: "Entry blockers",
    summary: "A passing blocker prevents a new position even when opportunity and confirmation pass.",
  },
} as const;

const RULE_STAGE_STORY: Record<keyof EntryRules, string[]> = {
  opportunity: [
    "Opportunity determines whether a ticker becomes an entry candidate. Each rule set is an independent detection path. Stage logic selects whether any path or every path must pass before confirmation is evaluated.",
  ],
  confirmation: [
    "Confirmation determines whether the detected opportunity is actionable now. Each rule set combines its own conditions and optional required score; confirmation settings do not create a shared global score.",
  ],
  blockers: [
    "A passing blocker prevents new exposure even when opportunity and confirmation pass. Use blockers for strategy-level invalidation such as stale evidence or an incompatible regime; account exposure and loss limits belong to Portfolio.",
  ],
};

const COMPARATOR_OPTIONS = [
  { label: "Is above by", value: "above_by_bps" },
  { label: "Is at least", value: "greater_or_equal" },
  { label: "Is greater than", value: "greater_than" },
  { label: "Is at most", value: "less_or_equal" },
  { label: "Is less than", value: "less_than" },
  { label: "Equals", value: "equals" },
  { label: "Is true", value: "is_true" },
];

function DecisionRulesEditor({ catalog = [], onChange, onRuleSetEdit = () => undefined, onRuleSetsChange, ruleSetCatalog = [], rules, stageName, summary, title }: {
  catalog?: StrategyInput[];
  importRules?: EntryRules;
  onChange: (value: EntryRules) => void;
  onRuleSetEdit?: (ruleSetId: string, created?: RuleSetDefinition) => void;
  onRuleSetsChange?: (value: RuleSetDefinition[], rules: EntryRules) => void;
  ruleSetCatalog?: RuleSetDefinition[];
  rules: EntryRules;
  stageName?: keyof EntryRules;
  summary: string;
  title: string;
}) {
  const [selectedRuleSetId, setSelectedRuleSetId] = useState(ruleSetCatalog[0]?.rule_set_id ?? "");
  const [newRuleSet, setNewRuleSet] = useState<RuleSetDefinition | null>(null);
  const stageNames = stageName ? [stageName] : (Object.keys(RULE_STAGE_META) as Array<keyof EntryRules>);
  function replaceStage(name: keyof EntryRules, stage: RuleStage) { onChange({ ...rules, [name]: stage }); }
  function createRuleSet(name: keyof EntryRules) {
    const ruleSetId = uniqueId(`${name}-rule-set`, ruleSetCatalog.map((row) => row.rule_set_id));
    const source = catalog[0];
    const next: RuleSetDefinition = { conditions: source ? [{ comparator: source.value_type === "boolean" ? "is_true" : "greater_or_equal", condition_id: `${ruleSetId}-condition`, enabled: true, left_source_id: source.source_id, left_timeframe: source.timeframes[0], right_source_id: "", right_timeframe: "", value: source.value_type === "boolean" ? null : 0 }] : [], description: "", enabled: true, name: "New rule set", operator: "all", required_score: 1, rule_set_id: ruleSetId };
    setNewRuleSet(next);
  }
  function saveRuleSet(name: keyof EntryRules) {
    if (!newRuleSet?.name.trim()) return;
    const saved = { ...newRuleSet, name: newRuleSet.name.trim() };
    const nextRules = { ...rules, [name]: { expression: appendRuleExpression(rules[name].expression, { kind: "rule_set", rule_set_id: saved.rule_set_id }) } };
    if (onRuleSetsChange) onRuleSetsChange([...ruleSetCatalog, saved], nextRules);
    else onChange(nextRules);
    setSelectedRuleSetId(saved.rule_set_id);
    setNewRuleSet(null);
  }
  return <div className={`strategy-rule-editor${stageName ? " strategy-entry-rule-editor" : ""}`}>
    {!stageName ? <div className="strategy-source-legend"><GitBranch size={18} /><div><strong>{title}</strong><p>{summary}</p></div></div> : null}
    {stageNames.map((name) => {
      const stage = rules[name];
      return <section className="strategy-entry-rule-page strategy-rule-composition-page" key={name}>
        <header className="strategy-rule-composition-toolbar"><div><span>Predefined rule sets</span><strong>{RULE_STAGE_META[name].label}</strong><small>Choose existing definitions or create a reusable rule set without leaving this page.</small></div><div><select aria-label="Rule set to add" onChange={(event) => setSelectedRuleSetId(event.target.value)} value={selectedRuleSetId}><option value="">Choose a rule set</option>{ruleSetCatalog.map((ruleSet) => <option key={ruleSet.rule_set_id} value={ruleSet.rule_set_id}>{ruleSet.name}</option>)}</select><button className="button compact" disabled={!selectedRuleSetId} onClick={() => replaceStage(name, { expression: appendRuleExpression(stage.expression, { kind: "rule_set", rule_set_id: selectedRuleSetId }) })} type="button"><Plus size={14} /> Add</button>{onRuleSetsChange ? <button className="button compact secondary" onClick={() => createRuleSet(name)} type="button"><Plus size={14} /> New</button> : null}</div></header>
        {stage.expression ? <RuleExpressionEditor catalog={catalog} expression={stage.expression} onChange={(expression) => replaceStage(name, { expression })} onEditRuleSet={onRuleSetEdit} ruleSets={ruleSetCatalog} /> : <EmptyState detail="Add a predefined rule set to compose this decision." title="No rule-set expression" />}
        {stage.expression ? <div className="strategy-rule-expression-summary"><span>Final logic</span><strong>{formatRuleExpression(stage.expression, ruleSetCatalog)}</strong></div> : null}
        {newRuleSet ? <RuleSetCreationDialog catalog={catalog} onCancel={() => setNewRuleSet(null)} onChange={setNewRuleSet} onSave={() => saveRuleSet(name)} ruleSet={newRuleSet} /> : null}
      </section>;
    })}
  </div>;
}

function RuleSetCreationDialog({ catalog, onCancel, onChange, onSave, ruleSet }: {
  catalog: StrategyInput[];
  onCancel: () => void;
  onChange: (value: RuleSetDefinition) => void;
  onSave: () => void;
  ruleSet: RuleSetDefinition;
}) {
  const titleId = `${useId()}-title`;
  const nameInput = useRef<HTMLInputElement>(null);
  const onCancelRef = useRef(onCancel);
  onCancelRef.current = onCancel;
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    nameInput.current?.focus();
    nameInput.current?.select();
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onCancelRef.current(); };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
      previouslyFocused?.focus();
    };
  }, []);
  const group: RuleGroup = { conditions: ruleSet.conditions, enabled: ruleSet.enabled, group_id: ruleSet.rule_set_id, label: ruleSet.name, operator: ruleSet.operator, required_score: ruleSet.required_score };
  return createPortal(
    <div className="modal-backdrop strategy-rule-set-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
      <section aria-labelledby={titleId} aria-modal="true" className="modal-panel strategy-rule-set-dialog" role="dialog">
        <header className="strategy-rule-set-dialog-header"><div><span>New reusable rule set</span><h2 id={titleId}>Define the evidence and save it</h2><p>The saved rule set becomes available in the Parameter Catalog and every lifecycle rule selector.</p></div><button aria-label="Close new rule set" onClick={onCancel} type="button"><X size={18} /></button></header>
        <div className="strategy-rule-set-dialog-body">
          <div className="strategy-rule-set-dialog-identity">
            <label><span>Rule set name</span><input aria-invalid={!ruleSet.name.trim()} onChange={(event) => onChange({ ...ruleSet, name: event.target.value })} ref={nameInput} value={ruleSet.name} /></label>
            <label><span>Description</span><textarea onChange={(event) => onChange({ ...ruleSet, description: event.target.value })} placeholder="Explain what a passing result means." rows={2} value={ruleSet.description} /></label>
          </div>
          <RuleSetMeaning catalog={catalog} ruleSet={ruleSet} />
          <RuleGroupEditor catalog={catalog} defaultOpen group={group} hideName onChange={(next) => onChange({ ...ruleSet, conditions: next.conditions, enabled: next.enabled, operator: next.operator, required_score: next.required_score })} onRemove={() => undefined} removable={false} />
        </div>
        <footer className="strategy-rule-set-dialog-actions"><button className="button secondary" onClick={onCancel} type="button">Cancel</button><button className="button" disabled={!ruleSet.name.trim()} onClick={onSave} type="button"><Save size={15} /> Save rule set</button></footer>
      </section>
    </div>,
    document.body,
  );
}

function appendRuleExpression(expression: RuleExpression | undefined, child: RuleExpression): RuleExpression {
  if (!expression) return { children: [child], kind: "operator", operator: "and" };
  if (expression.kind === "operator") return { ...expression, children: [...expression.children, child] };
  return { children: [expression, child], kind: "operator", operator: "and" };
}

function formatRuleExpression(expression: RuleExpression, ruleSets: RuleSetDefinition[]): string {
  if (expression.kind === "rule_set") return ruleSets.find((row) => row.rule_set_id === expression.rule_set_id)?.name ?? "Missing rule set";
  return `(${expression.children.map((child) => formatRuleExpression(child, ruleSets)).join(` ${expression.operator.toUpperCase()} `)})`;
}

function formatRuleCondition(condition: RuleCondition, catalog: StrategyInput[]): string {
  const left = inputSource(catalog, condition.left_source_id);
  const right = condition.right_source_id ? inputSource(catalog, condition.right_source_id) : null;
  const sourceReference = (source: StrategyInput | undefined | null, sourceId: string, timeframe: string) => `${source?.label ?? readableLabel(sourceId)}${timeframe ? ` (${timeframe})` : ""}`;
  const leftReference = sourceReference(left, condition.left_source_id, condition.left_timeframe);
  const rightReference = condition.right_source_id
    ? sourceReference(right, condition.right_source_id, condition.right_timeframe)
    : condition.value === null || condition.value === undefined ? "an unset threshold" : String(condition.value);
  if (condition.comparator === "is_true") return `${leftReference} is true`;
  if (condition.comparator === "above_by_bps") return `${leftReference} is ${condition.value ?? 0} bps above ${rightReference}`;
  const comparator = {
    equals: "equals",
    greater_or_equal: "is at least",
    greater_than: "is greater than",
    less_or_equal: "is at most",
    less_than: "is less than",
  }[condition.comparator] ?? readableLabel(condition.comparator).toLocaleLowerCase();
  return `${leftReference} ${comparator} ${rightReference}`;
}

function ruleSetMeaning(ruleSet: Pick<RuleSetDefinition, "conditions" | "enabled" | "operator" | "required_score">, catalog: StrategyInput[]): string {
  const enabledConditions = ruleSet.conditions.filter((condition) => condition.enabled);
  if (!enabledConditions.length) return "No enabled conditions are configured, so this rule set cannot pass.";
  const conditions = enabledConditions.map((condition) => formatRuleCondition(condition, catalog));
  let meaning: string;
  if (ruleSet.operator === "score") {
    const score = ruleSet.required_score <= 1 ? `${Math.round(ruleSet.required_score * 100)}%` : String(ruleSet.required_score);
    meaning = `At least ${score} of these conditions must pass: ${conditions.join("; ")}.`;
  } else {
    meaning = `${conditions.join(ruleSet.operator === "all" ? " AND " : " OR ")}.`;
  }
  const disabledCount = ruleSet.conditions.length - enabledConditions.length;
  return `${meaning}${disabledCount ? ` ${disabledCount} disabled condition${disabledCount === 1 ? " is" : "s are"} excluded.` : ""}`;
}

function RuleEvidenceOperand({ catalog, sourceId, timeframe }: { catalog: StrategyInput[]; sourceId: string; timeframe: string }) {
  const source = inputSource(catalog, sourceId);
  return <span className="strategy-rule-evidence-operand"><strong>{source?.label ?? readableLabel(sourceId)}</strong>{timeframe ? <small>{timeframe}</small> : null}</span>;
}

function RuleConditionMeaning({ catalog, condition }: { catalog: StrategyInput[]; condition: RuleCondition }) {
  const relation = condition.comparator === "above_by_bps"
    ? `${condition.value ?? 0} bps above`
    : ({
        equals: "equals",
        greater_or_equal: "is at least",
        greater_than: "is greater than",
        is_true: "is true",
        less_or_equal: "is at most",
        less_than: "is less than",
      }[condition.comparator] ?? readableLabel(condition.comparator).toLocaleLowerCase());
  const showTarget = condition.comparator !== "is_true";
  return <div aria-hidden="true" className="strategy-rule-evidence-expression">
    <RuleEvidenceOperand catalog={catalog} sourceId={condition.left_source_id} timeframe={condition.left_timeframe} />
    <span className="strategy-rule-evidence-relation">{relation}</span>
    {showTarget ? condition.right_source_id
      ? <RuleEvidenceOperand catalog={catalog} sourceId={condition.right_source_id} timeframe={condition.right_timeframe} />
      : <strong className="strategy-rule-evidence-value">{condition.value === null || condition.value === undefined ? "Unset" : String(condition.value)}</strong> : null}
  </div>;
}

function RuleSetMeaning({ catalog, ruleSet }: { catalog: StrategyInput[]; ruleSet: Pick<RuleSetDefinition, "conditions" | "enabled" | "operator" | "required_score"> }) {
  const enabledConditions = ruleSet.conditions.filter((condition) => condition.enabled);
  const disabledCount = ruleSet.conditions.length - enabledConditions.length;
  const score = ruleSet.required_score <= 1 ? `${Math.round(ruleSet.required_score * 100)}%` : String(ruleSet.required_score);
  const logic = ruleSet.operator === "score" ? `Score ≥ ${score}` : ruleSet.operator.toLocaleUpperCase();
  return <div className="strategy-rule-set-meaning" data-enabled={ruleSet.enabled ? "true" : "false"}>
    <span className="sr-only">{ruleSetMeaning(ruleSet, catalog)}</span>
    <header aria-hidden="true"><span>{ruleSet.enabled ? "Passes when" : "If enabled"}</span><strong>{logic}</strong></header>
    {enabledConditions.length ? <div className="strategy-rule-evidence-list">
      {enabledConditions.map((condition, index) => <div className="strategy-rule-evidence-clause" key={condition.condition_id}>{index ? <span className="strategy-rule-evidence-logic">{ruleSet.operator === "all" ? "AND" : ruleSet.operator === "any" ? "OR" : "PLUS"}</span> : null}<RuleConditionMeaning catalog={catalog} condition={condition} /></div>)}
    </div> : <p className="strategy-rule-evidence-empty">No enabled conditions are configured.</p>}
    {disabledCount ? <small className="strategy-rule-evidence-disabled">{disabledCount} disabled condition{disabledCount === 1 ? "" : "s"} excluded</small> : null}
  </div>;
}

function RuleExpressionEditor({ catalog, expression, onChange, onEditRuleSet, ruleSets }: { catalog: StrategyInput[]; expression: RuleExpression; onChange: (value: RuleExpression) => void; onEditRuleSet: (ruleSetId: string) => void; ruleSets: RuleSetDefinition[] }) {
  if (expression.kind === "rule_set") {
    const ruleSet = ruleSets.find((row) => row.rule_set_id === expression.rule_set_id);
    return <article className="strategy-rule-expression-leaf"><GitBranch size={16} /><div className="strategy-rule-expression-copy"><strong>{ruleSet?.name ?? "Missing rule set"}</strong><small>{ruleSet ? `${ruleSet.conditions.filter((condition) => condition.enabled).length} active condition${ruleSet.conditions.filter((condition) => condition.enabled).length === 1 ? "" : "s"} · ${readableLabel(ruleSet.operator)}` : "The referenced catalog definition is unavailable."}</small>{ruleSet ? <RuleSetMeaning catalog={catalog} ruleSet={ruleSet} /> : null}</div><button className="button compact" onClick={() => onEditRuleSet(expression.rule_set_id)} type="button"><PencilLine size={13} /> Modify</button></article>;
  }
  const fallbackRuleSet = ruleSets[0];
  return <section className="strategy-rule-expression-group"><header><span className="strategy-rule-parenthesis">(</span><div role="group" aria-label="Expression operator"><button aria-pressed={expression.operator === "and"} onClick={() => onChange({ ...expression, operator: "and" })} type="button">AND</button><button aria-pressed={expression.operator === "or"} onClick={() => onChange({ ...expression, operator: "or" })} type="button">OR</button></div><button className="button compact secondary" disabled={!fallbackRuleSet} onClick={() => fallbackRuleSet && onChange({ ...expression, children: [...expression.children, { children: [{ kind: "rule_set", rule_set_id: fallbackRuleSet.rule_set_id }], kind: "operator", operator: expression.operator === "and" ? "or" : "and" }] })} type="button">( ) Add group</button></header><div>{expression.children.map((child, index) => <div className="strategy-rule-expression-child" key={`${child.kind}-${index}`}><RuleExpressionEditor catalog={catalog} expression={child} onChange={(next) => onChange({ ...expression, children: expression.children.map((row, childIndex) => childIndex === index ? next : row) })} onEditRuleSet={onEditRuleSet} ruleSets={ruleSets} /><button aria-label="Remove from expression" className="button compact danger" disabled={expression.children.length === 1} onClick={() => onChange({ ...expression, children: expression.children.filter((_, childIndex) => childIndex !== index) })} type="button"><Trash2 size={13} /></button>{index < expression.children.length - 1 ? <span className="strategy-rule-expression-operator">{expression.operator.toUpperCase()}</span> : null}</div>)}</div><span className="strategy-rule-parenthesis">)</span></section>;
}

function RuleStageComposition({ catalog, label, onChange, onEditRuleSet, ruleSets, stage }: { catalog: StrategyInput[]; label: string; onChange: (value: RuleStage) => void; onEditRuleSet: (ruleSetId: string) => void; ruleSets: RuleSetDefinition[]; stage: RuleStage }) {
  const [selectedRuleSetId, setSelectedRuleSetId] = useState(ruleSets[0]?.rule_set_id ?? "");
  return <section className="strategy-rule-composition-page"><header className="strategy-rule-composition-toolbar"><div><span>Predefined rule sets</span><strong>{label}</strong><small>Choose catalog definitions, then combine them with nested AND and OR groups.</small></div><div><select onChange={(event) => setSelectedRuleSetId(event.target.value)} value={selectedRuleSetId}><option value="">Choose a rule set</option>{ruleSets.map((ruleSet) => <option key={ruleSet.rule_set_id} value={ruleSet.rule_set_id}>{ruleSet.name}</option>)}</select><button className="button compact" disabled={!selectedRuleSetId} onClick={() => onChange({ expression: appendRuleExpression(stage.expression, { kind: "rule_set", rule_set_id: selectedRuleSetId }) })} type="button"><Plus size={14} /> Add</button></div></header>{stage.expression ? <RuleExpressionEditor catalog={catalog} expression={stage.expression} onChange={(expression) => onChange({ expression })} onEditRuleSet={onEditRuleSet} ruleSets={ruleSets} /> : <EmptyState detail="Add a catalog rule set to define this lifecycle decision." title="No rule sets selected" />}{stage.expression ? <div className="strategy-rule-expression-summary"><span>Final logic</span><strong>{formatRuleExpression(stage.expression, ruleSets)}</strong></div> : null}</section>;
}

type LegacyEntryRules = Record<keyof EntryRules, RuleStage & { groups: RuleGroup[]; operator: "all" | "any" }>;

function LegacyDecisionRulesEditor({ catalog, importRules, onChange, rules, stageName, summary, title }: {
  catalog: StrategyInput[];
  importRules?: LegacyEntryRules;
  onChange: (value: LegacyEntryRules) => void;
  rules: LegacyEntryRules;
  stageName?: keyof EntryRules;
  summary: string;
  title: string;
}) {
  const [openedGroupIds, setOpenedGroupIds] = useState<Set<string>>(new Set());
  if (!rules) return <EmptyState title="Decision rules unavailable" detail="Reload the configuration session to receive the typed source model." />;

  function replaceStage(stageName: keyof EntryRules, stage: RuleStage) {
    onChange({ ...rules, [stageName]: stage });
  }

  function replaceGroup(stageName: keyof EntryRules, groupId: string, group: RuleGroup) {
    const stage = rules[stageName];
    replaceStage(stageName, { ...stage, groups: stage.groups.map((row) => row.group_id === groupId ? group : row) });
  }

  function addGroup(stageName: keyof EntryRules) {
    const stage = rules[stageName];
    const source = catalog[0];
    const groupId = uniqueId(`${stageName}-rule`, stage.groups.map((row) => row.group_id));
    const condition: RuleCondition = {
      comparator: source.value_type === "boolean" ? "is_true" : "greater_or_equal",
      condition_id: `${groupId}-condition`,
      enabled: true,
      left_source_id: source.source_id,
      left_timeframe: source.timeframes[0],
      right_source_id: "",
      right_timeframe: "",
      value: source.value_type === "boolean" ? null : 0,
    };
    replaceStage(stageName, {
      ...stage,
      groups: [{
        conditions: [condition],
        enabled: true,
        group_id: groupId,
        label: "New rule set",
        operator: "all",
        required_score: 1,
      }, ...stage.groups],
    });
    setOpenedGroupIds((current) => new Set(current).add(groupId));
  }

  function importStage(stageName: keyof EntryRules) {
    const sourceGroups = importRules?.[stageName]?.groups ?? [];
    const takenIds = rules[stageName].groups.map((row) => row.group_id);
    const imported = sourceGroups.map((group) => {
      const groupId = uniqueId(`${group.group_id}-copy`, takenIds);
      takenIds.push(groupId);
      return {
        ...deepClone(group),
        group_id: groupId,
        label: `${group.label} · imported`,
        conditions: group.conditions.map((condition, index) => ({
          ...condition,
          condition_id: `${groupId}-condition-${index + 1}`,
        })),
      };
    });
    replaceStage(stageName, {
      ...rules[stageName],
      groups: [...imported, ...rules[stageName].groups],
    });
    setOpenedGroupIds((current) => new Set([...current, ...imported.map((row) => row.group_id)]));
  }

  const stageNames = stageName ? [stageName] : (Object.keys(RULE_STAGE_META) as Array<keyof EntryRules>);

  return (
    <div className={`strategy-rule-editor${stageName ? " strategy-entry-rule-editor" : ""}`}>
      {!stageName ? <div className="strategy-source-legend">
        <GitBranch size={18} />
        <div>
          <strong>{title}</strong>
          <p>{summary}</p>
        </div>
      </div> : null}
      {stageNames.map((currentStageName) => {
        const stage = rules[currentStageName];
        const meta = RULE_STAGE_META[currentStageName];
        if (stageName) return <section className="strategy-entry-rule-page" key={currentStageName}>
          <header className="strategy-entry-rule-toolbar">
            <SelectField
              help={{ role: "Combines the enabled rule sets on this page.", values: { "Any rule set": "The page passes when one enabled rule set passes.", "All rule sets": "Every enabled rule set must pass." } }}
              label="Rule-set logic"
              onChange={(operator) => replaceStage(currentStageName, { ...stage, operator: operator as "all" | "any" })}
              options={[{ label: "Any rule set", value: "any" }, { label: "All rule sets", value: "all" }]}
              value={stage.operator}
            />
            <button className="button compact" onClick={() => addGroup(currentStageName)} type="button"><Plus size={14} /> Add rule set</button>
          </header>
          <div className="strategy-rule-groups">
            {stage.groups.map((group) => <RuleGroupEditor catalog={catalog} defaultOpen={openedGroupIds.has(group.group_id)} group={group} key={group.group_id} onChange={(next) => replaceGroup(currentStageName, group.group_id, next)} onRemove={() => replaceStage(currentStageName, { ...stage, groups: stage.groups.filter((row) => row.group_id !== group.group_id) })} removable={stage.groups.length > 1} />)}
            {!stage.groups.length ? <EmptyState detail={`Add the first ${readableLabel(currentStageName)} rule set to define this part of entry evidence.`} title="No rule sets configured" /> : null}
          </div>
        </section>;
        return (
          <section className="strategy-rule-stage-chapter" key={currentStageName}>
          <ConfigurationNarrative heading={meta.label} paragraphs={RULE_STAGE_STORY[currentStageName]} />
          <details className="strategy-rule-stage" data-stage={currentStageName}>
            <summary><div><span>{currentStageName}</span><strong>{meta.label}</strong><p>{meta.summary}</p></div><span>{stage.groups.length} rule sets</span><ChevronDown size={16} /></summary>
            <div className="strategy-rule-stage-body">
            <header>
              <div><span>{currentStageName}</span><strong>{meta.label}</strong><p>{meta.summary}</p></div>
              <div className="strategy-stage-controls">
                  <SelectField
                    help={{ role: "Combines the enabled rule sets in this phase group.", values: { "Any rule set": "The phase group passes when one enabled rule set passes.", "All rule sets": "Every enabled rule set must pass." } }}
                    label="Stage logic"
                    onChange={(operator) => replaceStage(currentStageName, { ...stage, operator: operator as "all" | "any" })}
                    options={[{ label: "Any rule set", value: "any" }, { label: "All rule sets", value: "all" }]}
                    value={stage.operator}
                  />
                <button className="button compact" onClick={() => addGroup(currentStageName)} type="button"><Plus size={14} /> Add rule set</button>
                {importRules?.[currentStageName]?.groups?.length ? (
                  <button className="button compact secondary" onClick={() => importStage(currentStageName)} type="button"><FileInput size={14} /> Add initial rules</button>
                ) : null}
              </div>
            </header>
            <div className="strategy-rule-groups">
              {stage.groups.map((group) => (
                <RuleGroupEditor
                  catalog={catalog}
                  group={group}
                  key={group.group_id}
                  defaultOpen={openedGroupIds.has(group.group_id)}
                  onChange={(next) => replaceGroup(currentStageName, group.group_id, next)}
                  onRemove={() => replaceStage(currentStageName, { ...stage, groups: stage.groups.filter((row) => row.group_id !== group.group_id) })}
                  removable={stage.groups.length > 1}
                />
              ))}
            </div>
            </div>
          </details>
          </section>
        );
      })}
    </div>
  );
}

function RuleGroupEditor({ catalog, defaultOpen = false, group, hideName = false, onChange, onRemove, removable }: {
  catalog: StrategyInput[];
  defaultOpen?: boolean;
  group: RuleGroup;
  hideName?: boolean;
  onChange: (value: RuleGroup) => void;
  onRemove: () => void;
  removable: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  function replaceCondition(conditionId: string, condition: RuleCondition) {
    onChange({ ...group, conditions: group.conditions.map((row) => row.condition_id === conditionId ? condition : row) });
  }

  function addCondition() {
    const source = catalog[0];
    const conditionId = uniqueId(`${group.group_id}-condition`, group.conditions.map((row) => row.condition_id));
    onChange({
      ...group,
      conditions: [...group.conditions, {
        comparator: source.value_type === "boolean" ? "is_true" : "greater_or_equal",
        condition_id: conditionId,
        enabled: true,
        left_source_id: source.source_id,
        left_timeframe: source.timeframes[0],
        right_source_id: "",
        right_timeframe: "",
        value: source.value_type === "boolean" ? null : 0,
      }],
    });
  }

  return (
    <details className="strategy-rule-group" data-enabled={group.enabled ? "true" : "false"} onToggle={(event) => setOpen(event.currentTarget.open)} open={open}>
      <summary>
        <span className="strategy-rule-state" />
        <div><strong>{group.label}</strong><small>{group.conditions.length} conditions · {group.operator === "all" ? "all required" : "any may pass"}</small></div>
        <span>{group.enabled ? "Enabled" : "Disabled"}</span>
        <ChevronDown size={16} />
      </summary>
      <div className="strategy-rule-group-body">
      <ConfigurationNarrative heading={group.label} paragraphs={[
        "This rule set combines enabled conditions into one result. Condition logic selects all, any, or a required passing fraction. Each condition compares a causal source and timeframe with a constant or another source using only data available at evaluation time.",
      ]} />
      <header className="strategy-rule-toolbar">
        <div className="strategy-rule-toolbar-heading"><span>Rule set controls</span><p>Name the evidence bundle, choose how its conditions combine, and decide whether it participates in evaluation.</p></div>
        <div className="strategy-rule-toolbar-fields">
          {!hideName ? <label className="strategy-rule-name"><span>Rule set name</span><input onChange={(event) => onChange({ ...group, label: event.target.value })} value={group.label} /></label> : null}
          <label><span>Condition logic <FieldHelp title="Condition logic" content={{ role: "Defines how this rule set converts its enabled conditions into one pass or fail result.", values: { "All must pass": "Every enabled condition must be true.", "Any may pass": "One enabled condition is enough.", "Required score": "The fraction of enabled conditions that pass must meet this rule set's own score." }, note: "The score is local to this rule set. There is no global confirmation score." }} /></span><select onChange={(event) => onChange({ ...group, operator: event.target.value as RuleGroup["operator"] })} value={group.operator}><option value="all">All must pass</option><option value="any">Any may pass</option><option value="score">Required score</option></select></label>
          {group.operator === "score" ? <label><span>Required score <FieldHelp title="Required score" content={{ role: "Minimum fraction of this rule set's enabled conditions that must pass.", values: { "1.0": "Every condition must pass.", "0.75": "At least three quarters must pass.", "0.5": "At least half must pass." }, note: "This value belongs only to this rule set; changing it does not affect any other confirmation or phase." }} /></span><input max={1} min={0.01} onChange={(event) => onChange({ ...group, required_score: Number(event.target.value) })} step={0.05} type="number" value={group.required_score} /></label> : null}
          <div className="strategy-rule-toolbar-actions">
            <label className="strategy-rule-enabled"><span><strong>{group.enabled ? "Enabled" : "Disabled"}</strong><small>{group.enabled ? "Included in evaluation" : "Ignored by runtime"}</small></span><span className="configuration-switch"><input checked={group.enabled} onChange={(event) => onChange({ ...group, enabled: event.target.checked })} type="checkbox" /><span /></span></label>
            {removable ? <button aria-label={`Delete ${group.label}`} className="button compact danger" onClick={onRemove} type="button"><Trash2 size={14} /> Delete</button> : null}
          </div>
        </div>
      </header>
      <div className="strategy-rule-conditions">
        {group.conditions.map((condition, index) => (
          <RuleConditionEditor
            catalog={catalog}
            condition={condition}
            index={index}
            key={condition.condition_id}
            onChange={(next) => replaceCondition(condition.condition_id, next)}
            onRemove={() => onChange({ ...group, conditions: group.conditions.filter((row) => row.condition_id !== condition.condition_id) })}
            removable={group.conditions.length > 1}
          />
        ))}
      </div>
      <button className="configuration-inline-action" onClick={addCondition} type="button"><Plus size={13} /> Add condition to this rule set</button>
      </div>
    </details>
  );
}

function RuleConditionEditor({ catalog, condition, index, onChange, onRemove, removable }: {
  catalog: StrategyInput[];
  condition: RuleCondition;
  index: number;
  onChange: (value: RuleCondition) => void;
  onRemove: () => void;
  removable: boolean;
}) {
  const left = inputSource(catalog, condition.left_source_id);
  const targetMode = condition.right_source_id ? "source" : "constant";
  const right = condition.right_source_id ? inputSource(catalog, condition.right_source_id) : null;
  const comparatorOptions = left?.value_type === "boolean"
    ? COMPARATOR_OPTIONS.filter((row) => row.value === "is_true" || row.value === "equals")
    : COMPARATOR_OPTIONS.filter((row) => row.value !== "is_true");

  function selectLeft(sourceId: string) {
    const source = inputSource(catalog, sourceId) ?? catalog[0];
    onChange({
      ...condition,
      comparator: source.value_type === "boolean" ? "is_true" : condition.comparator === "is_true" ? "greater_or_equal" : condition.comparator,
      left_source_id: source.source_id,
      left_timeframe: source.timeframes[0],
      value: source.value_type === "boolean" ? null : condition.value ?? 0,
    });
  }

  function selectTargetMode(mode: string) {
    if (mode === "source") {
      const source = catalog.find((row) => row.value_type !== "boolean") ?? catalog[0];
      onChange({ ...condition, right_source_id: source.source_id, right_timeframe: source.timeframes[0], value: condition.comparator === "above_by_bps" ? 0 : null });
    } else {
      onChange({ ...condition, right_source_id: "", right_timeframe: "", value: 0 });
    }
  }

  return (
    <div className="strategy-rule-condition">
      <span className="strategy-condition-index">{index + 1}</span>
      <label><span>Data source</span><select onChange={(event) => selectLeft(event.target.value)} value={condition.left_source_id}>{sourceOptions(catalog)}</select></label>
      <label><span>Timeframe</span><select onChange={(event) => onChange({ ...condition, left_timeframe: event.target.value })} value={condition.left_timeframe}>{left?.timeframes.map((timeframe) => <option key={timeframe}>{timeframe}</option>)}</select></label>
      <label><span>Comparison</span><select onChange={(event) => {
        const comparator = event.target.value;
        if (comparator === "above_by_bps" && !condition.right_source_id) {
          const source = catalog.find((row) => row.value_type === left?.value_type && row.source_id !== condition.left_source_id) ?? catalog[0];
          onChange({ ...condition, comparator, right_source_id: source.source_id, right_timeframe: source.timeframes[0], value: 0 });
        } else {
          onChange({ ...condition, comparator });
        }
      }} value={condition.comparator}>{comparatorOptions.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}</select></label>
      {condition.comparator !== "is_true" ? (
        <>
          <label><span>Compare with</span><select disabled={condition.comparator === "above_by_bps"} onChange={(event) => selectTargetMode(event.target.value)} value={targetMode}><option value="constant">Fixed value</option><option value="source">Another source</option></select></label>
          {targetMode === "source" ? (
            <>
              <label><span>Target source</span><select onChange={(event) => {
                const source = inputSource(catalog, event.target.value) ?? catalog[0];
                onChange({ ...condition, right_source_id: source.source_id, right_timeframe: source.timeframes[0] });
              }} value={condition.right_source_id}>{sourceOptions(catalog, left?.value_type)}</select></label>
              <label><span>Target timeframe</span><select onChange={(event) => onChange({ ...condition, right_timeframe: event.target.value })} value={condition.right_timeframe}>{right?.timeframes.map((timeframe) => <option key={timeframe}>{timeframe}</option>)}</select></label>
            </>
          ) : (
            <label><span>Threshold</span><input onChange={(event) => onChange({ ...condition, value: Number(event.target.value) })} step="any" type="number" value={Number(condition.value ?? 0)} /></label>
          )}
          {condition.comparator === "above_by_bps" && targetMode === "source" ? <label><span>Buffer (bps)</span><input min={0} onChange={(event) => onChange({ ...condition, value: Number(event.target.value) })} step={0.5} type="number" value={Number(condition.value ?? 0)} /></label> : null}
        </>
      ) : null}
      <button aria-label={`Delete condition ${index + 1}`} className="button compact danger" disabled={!removable} onClick={onRemove} type="button"><Trash2 size={13} /></button>
      <div className="strategy-source-detail">
        <strong>{left?.provider ?? "Unknown provider"}</strong>
        <span>{left?.category} · {left?.parameter} · runtime field {left?.runtime_field}</span>
        <p>{left?.summary}</p>
      </div>
    </div>
  );
}

function RuleStageEditor({ catalog, intent, label, onChange, stage }: {
  catalog: StrategyInput[];
  intent: "add" | "entry" | "exit" | "reentry";
  label: string;
  onChange: (value: RuleStage) => void;
  stage: RuleStage;
}) {
  const [openedId, setOpenedId] = useState("");
  const groups = stage.groups ?? [];
  const context = {
    add: { eyebrow: "Add evidence", noun: "add action", text: "These rule sets are evaluated while a position is open before Strategy may request more exposure." },
    entry: { eyebrow: "Entry evidence", noun: "entry stage", text: "These rule sets are evaluated before Strategy may emit its configured initial-entry request." },
    exit: { eyebrow: "Exit evidence", noun: "exit route", text: "These rule sets are evaluated before Strategy may emit its configured exit request." },
    reentry: { eyebrow: "Reentry evidence", noun: "reentry stage", text: "These rule sets are evaluated after the campaign is flat before Strategy may emit a new reentry request." },
  }[intent];
  function addGroup() {
    const groupId = uniqueId("new-rule", groups.map((row) => row.group_id));
    const source = catalog[0];
    onChange({
      ...stage,
      groups: [{
        conditions: [{
          comparator: source.value_type === "boolean" ? "is_true" : "greater_or_equal",
          condition_id: `${groupId}-condition`,
          enabled: true,
          left_source_id: source.source_id,
          left_timeframe: source.timeframes[0],
          right_source_id: "",
          right_timeframe: "",
          value: source.value_type === "boolean" ? null : 0,
        }],
        enabled: true,
        group_id: groupId,
        label: "New rule set",
        operator: "all",
        required_score: 1,
      }, ...groups],
    });
    setOpenedId(groupId);
  }
  return (
    <section className="strategy-rule-stage compact" data-stage={intent}>
      <header>
        <div><span>{context.eyebrow}</span><strong>{label}</strong><p>{context.text}</p></div>
        <div className="strategy-stage-controls">
          <SelectField
            help={{ role: `Combines this ${context.noun}'s rule sets.`, values: { "Any rule set": `The ${context.noun} passes when at least one enabled rule set passes.`, "All rule sets": "Every enabled rule set must pass." } }}
            label="Rule-set logic"
            onChange={(operator) => onChange({ ...stage, operator: operator as "all" | "any" })}
            options={[{ label: "Any rule set", value: "any" }, { label: "All rule sets", value: "all" }]}
            value={stage.operator ?? "any"}
          />
          <button className="button compact" onClick={addGroup} type="button"><Plus size={14} /> Add rule set</button>
        </div>
      </header>
      <ConfigurationNarrative heading={label} paragraphs={[
        `${context.text} Rule-set logic selects whether any path or every path must pass; each path applies its own condition logic and required score.`,
      ]} />
      <div className="strategy-rule-groups">
        {groups.map((group) => (
          <RuleGroupEditor
            catalog={catalog}
            defaultOpen={group.group_id === openedId}
            group={group}
            key={group.group_id}
            onChange={(next) => onChange({ ...stage, groups: groups.map((row) => row.group_id === group.group_id ? next : row) })}
            onRemove={() => onChange({ ...stage, groups: groups.filter((row) => row.group_id !== group.group_id) })}
            removable={groups.length > 1}
          />
        ))}
      </div>
    </section>
  );
}

function PhaseOrderEditor({ capitalRequest, eligibleSessions, executionPolicies, onCapitalRequest, onOrderIntent, orderIntent, protectionProfiles, title }: {
  capitalRequest: CapitalRequestConfig;
  eligibleSessions: string[];
  executionPolicies: ExecutionPolicyConfig[];
  onCapitalRequest: (value: CapitalRequestConfig) => void;
  onOrderIntent: (value: OrderIntentConfig) => void;
  orderIntent: OrderIntentConfig;
  protectionProfiles: ProtectionProfileConfig[];
  title: string;
}) {
  return (
    <section className="strategy-order-request">
      <header>
        <div><span>Portfolio + OMS handoff</span><strong>{title}</strong><p>The strategy describes intent. Portfolio resolves the approved account quantity, then OMS chooses session-safe broker instructions and manages the order.</p></div>
        <div className="strategy-handoff-flow" aria-label="Order handoff sequence"><span>Strategy request</span><ChevronRight size={14} /><span>Portfolio approval</span><ChevronRight size={14} /><span>OMS execution</span></div>
      </header>
      <ConfigurationNarrative heading={title} paragraphs={[
        "This request contains relative capital demand and broker-neutral execution preferences. Portfolio returns an approved account quantity or rejection. OMS may resolve broker mechanics for the approval but cannot increase its quantity or risk envelope.",
      ]} />
      <div className="strategy-handoff-grid">
        <CapitalRequestEditor onChange={onCapitalRequest} value={capitalRequest} />
        <OrderIntentEditor eligibleSessions={eligibleSessions} executionPolicies={executionPolicies} protectionProfiles={protectionProfiles} onChange={onOrderIntent} value={orderIntent} />
      </div>
    </section>
  );
}

function CapitalRequestEditor({ onChange, value }: {
  onChange: (value: CapitalRequestConfig) => void;
  value: CapitalRequestConfig;
}) {
  const requestHelp = {
    fixed_quantity: { label: "Shares requested", unit: "shares", maximum: undefined },
    mandate_fraction: { label: "Mandate capacity", unit: "fraction", maximum: 1 },
    risk_fraction: { label: "Risk budget", unit: "fraction", maximum: 1 },
    all_available: { label: "", unit: "", maximum: undefined },
  }[value.mode];
  return (
    <article className="strategy-handoff-card strategy-capital-request">
      <header>
        <BriefcaseBusiness size={18} />
        <div><span>Step 1 · Portfolio</span><strong>Capital request</strong><p>Ask for capital in relative terms. Portfolio applies the Run Plan mandate, buying power, current positions, risk limits, and competing requests before approving shares.</p></div>
      </header>
      <ConfigurationNarrative heading="Capital request" paragraphs={[
        "Mode determines whether demand is expressed as shares, mandate capacity, planned-risk budget, or remaining mandate capacity. Value sets the amount in that unit. Replacement only permits Portfolio to evaluate displacement under the mandate threshold; it does not close another position directly.",
      ]} />
      <div className="configuration-field-grid">
      <SelectField
        help={{
          role: "Describes the strategy's relative capital request. Portfolio converts it into an account-specific quantity after applying mandates, current positions, buying power, and risk.",
          values: {
            "Fixed quantity": "Request an explicit number of shares. Portfolio may approve less.",
            "Mandate fraction": "Request a percentage of this strategy's approved cash capacity on the account.",
            "Risk fraction": "Request a percentage of the account mandate's planned-risk budget.",
            "All available": "Request all remaining capacity allowed by the account mandate; this is not all account cash.",
          },
          parameters: {
            "Request value": "Shares for fixed quantity, or a fraction for mandate and risk modes. All available has no independent value.",
            "Allow replacement": "Permits Portfolio to propose releasing a weaker position when the new request materially improves the account plan.",
          },
        }}
        label="Capital request"
        onChange={(mode) => onChange({ ...value, mode: mode as CapitalRequestConfig["mode"], value: mode === "all_available" ? 1 : mode === "fixed_quantity" ? 100 : 0.2 })}
        options={["fixed_quantity", "mandate_fraction", "risk_fraction", "all_available"].map((mode) => ({ label: readableLabel(mode), value: mode }))}
        value={value.mode}
      />
      {value.mode !== "all_available" ? (
        <NumberField
          help={{
            role: value.mode === "fixed_quantity" ? "The share quantity requested before Portfolio approval." : `The fraction of the ${value.mode === "mandate_fraction" ? "strategy-account cash mandate" : "planned-risk budget"} requested by this trigger.`,
            note: "This value is local to this entry, reentry, or add trigger. It is not a strategy-wide position ceiling.",
          }}
          label={requestHelp.label}
          maximum={requestHelp.maximum}
          minimum={value.mode === "fixed_quantity" ? 1 : 0.01}
          onChange={(requestValue) => onChange({ ...value, value: requestValue })}
          step={value.mode === "fixed_quantity" ? 1 : 0.05}
          unit={requestHelp.unit}
          value={value.value}
        />
      ) : (
        <div className="configuration-context-value"><span>Request value</span><strong>Portfolio resolves remaining mandate capacity</strong><small>No stale quantity field is retained.</small></div>
      )}
        <BooleanField help={{ role: "Allows Portfolio to propose funding this request by reducing or closing a weaker position.", parameters: { "Replacement threshold": "Configured on the Portfolio mandate and must show sufficient improvement before displacement is allowed." }, note: "The strategy grants permission; Portfolio decides whether replacement is safe and beneficial." }} label="Allow replacement" onChange={(allow_replacement) => onChange({ ...value, allow_replacement })} value={value.allow_replacement} />
      </div>
      <div className="strategy-handoff-result"><span>Portfolio output</span><strong>Approved quantity and account allocation</strong><small>The approved result may be smaller than requested or rejected with a reason.</small></div>
    </article>
  );
}

function OrderIntentEditor({ eligibleSessions, executionPolicies, onChange, protectionProfiles, value }: {
  eligibleSessions: string[];
  executionPolicies: ExecutionPolicyConfig[];
  onChange: (value: OrderIntentConfig) => void;
  value: OrderIntentConfig;
  protectionProfiles: ProtectionProfileConfig[];
}) {
  const usesExtendedHours = eligibleSessions.some((session) => session === "premarket" || session === "after_hours");
  return (
    <article className="strategy-handoff-card strategy-order-intent">
      <header>
        <Send size={18} />
        <div><span>Step 2 · OMS</span><strong>Execution policy</strong><p>Choose urgency and fill behavior, not broker-specific flags. OMS converts this intent into the fastest compatible order for the selected sessions, account, venue, and broker.</p></div>
      </header>
      <ConfigurationNarrative heading="Execution intent" paragraphs={[
        "Execution policy controls urgency, repricing, and price boundaries. Protection attaches to actual fills. Partial-fill behavior controls whether OMS completes, accepts, or cancels the unfilled remainder. Session routing is derived from eligible sessions and broker capabilities.",
      ]} />
      <div className="configuration-field-grid">
      <SelectField
        help={{
          role: "Selects the broker-neutral execution policy sent to OMS after this trigger passes.",
          values: {
            Passive: "Prioritizes price improvement and avoids crossing. Use only when a missed or slow fill is acceptable.",
            Midpoint: "Starts near the spread midpoint. It can improve price but may miss a fast move or remain unfilled in a wide market.",
            "Adaptive patient": "Works slowly inside the permitted price envelope. Best for price quality when the opportunity can wait.",
            "Adaptive regular": "Balances fill probability and price improvement. This is the normal default for non-emergency orders.",
            "Adaptive urgent": "Reprices quickly toward executable liquidity. It improves fill probability but may pay more spread or slippage.",
            "Adaptive very urgent": "Uses the fastest bounded repricing for protection or time-critical exits. Expect the highest execution cost within the approved envelope.",
            "Immediate with limit": "Submits immediately with a hard price boundary. It can remain unfilled beyond that limit.",
            "IBKR native adaptive": "Uses IBKR's adaptive algorithm only when the broker and account support it; unsupported combinations must be rejected or safely mapped by OMS.",
            "Cancel if not filled": "Stops working the remainder at the deadline. Use where a partial or absent fill is safer than chasing an expiring opportunity.",
          },
          note: "The strategy never emits raw broker orders. OMS remains the only authority that creates, modifies, cancels, and reconciles orders.",
        }}
        label="Execution policy"
        onChange={(execution_policy) => onChange({ ...value, execution_policy })}
        options={executionPolicies.map((policy) => ({ label: `${readableLabel(policy.name)} · v${policy.revision}`, value: policy.policy_id }))}
        searchable={false}
        value={value.execution_policy}
      />
      <SelectField help="Selects the independently versioned stop, target, and trailing plan used for a filled entry or add." label="Protection profile" onChange={(protection_profile) => onChange({ ...value, protection_profile })} options={protectionProfiles.map((profile) => ({ label: `${profile.name} · v${profile.revision}`, value: profile.profile_id }))} value={value.protection_profile} />
      <SelectField help={{ role: "Determines how OMS handles an incomplete fill.", values: { "Complete remainder": "Continue working the unfilled quantity under the selected policy.", "Accept partial": "Keep the fill received and stop requesting the remainder.", "Cancel remainder": "Cancel any remainder after the first partial fill." } }} label="Partial fill" onChange={(partial_fill_policy) => onChange({ ...value, partial_fill_policy: partial_fill_policy as OrderIntentConfig["partial_fill_policy"] })} options={["complete_remainder", "accept_partial", "cancel_remainder"].map((item) => ({ label: readableLabel(item), value: item }))} value={value.partial_fill_policy} />
      </div>
      <div className="strategy-smart-session">
        <ShieldCheck size={17} />
        <div><span>Smart session routing</span><strong>{eligibleSessions.map(readableLabel).join(", ") || "No eligible session selected"}</strong><p>{usesExtendedHours ? "OMS enables eligible extended-session routing and selects compatible broker instructions after account, venue, and order-type checks." : "OMS keeps the request in the regular session and chooses compatible broker instructions automatically."}</p></div>
        <FieldHelp content={{ role: "Session routing is derived from Trading Behavior so entry, reentry, and exit requests cannot contradict the strategy's eligible sessions.", parameters: { "Eligible sessions": "Selected once in Trading Behavior.", "Time in force": "Chosen by OMS for the broker, venue, session, and execution method.", "Outside regular hours": "Enabled by OMS only when premarket or after-hours is selected and the broker path supports it." }, note: "Change session eligibility in Trading Behavior. Strategy phases intentionally do not expose raw time-in-force or outside-hours switches." }} />
      </div>
    </article>
  );
}

function AddStepsEditor({ catalog, eligibleSessions, executionPolicies, onChange, onRuleSetEdit = () => undefined, protectionProfiles, ruleSets = [], steps }: {
  catalog: StrategyInput[];
  eligibleSessions: string[];
  executionPolicies: ExecutionPolicyConfig[];
  onChange: (value: AddStep[]) => void;
  onRuleSetEdit?: (ruleSetId: string) => void;
  protectionProfiles: ProtectionProfileConfig[];
  ruleSets?: RuleSetDefinition[];
  steps: AddStep[];
}) {
  function addStep() {
    const stepId = uniqueId("position-add", steps.map((row) => row.step_id));
    const evidenceRuleSet = ruleSets[0];
    onChange([{
      capital_request: { allow_replacement: false, mode: "mandate_fraction", value: 0.1 },
      enabled: true,
      maximum_uses: 1,
      name: "New position add",
      order_intent: { deadline_ms: 750, execution_policy: "adaptive_urgent", partial_fill_policy: "complete_remainder", protection_profile: "hybrid-single" },
      rules: { expression: { children: evidenceRuleSet ? [{ kind: "rule_set", rule_set_id: evidenceRuleSet.rule_set_id }] : [], kind: "operator", operator: "and" } },
      step_id: stepId,
    }, ...steps]);
  }
  return (
    <section className="strategy-add-plan">
      <header><div><span>Position construction</span><strong>Conditional add requests</strong><p>Each add owns its evidence, relative capital request, order policy, and usage limit. Newly added steps appear first.</p></div><button className="button compact" onClick={addStep} type="button"><Plus size={14} /> Add position step</button></header>
      <ConfigurationNarrative heading="Position construction" paragraphs={[
        "Each add step defines evidence, maximum successful uses, capital demand, execution, and protection for increasing an open position. Every passing step creates a new request; Portfolio re-evaluates current account state, so initial-entry approval does not guarantee add approval.",
      ]} />
      <div>
        {steps.map((step) => (
          <details className="strategy-add-step" key={step.step_id}>
            <summary><span className="strategy-rule-state" /><div><strong>{step.name}</strong><small>{readableLabel(step.capital_request.mode)} · {step.maximum_uses} maximum uses</small></div><ChevronDown size={16} /></summary>
            <div className="strategy-add-step-body">
              <ConfigurationNarrative heading={step.name} paragraphs={[
                "Enabled determines whether this step participates. Maximum uses counts successful executions in one campaign. Passing evidence sends a new request through Run Plan authority, Portfolio sizing, and OMS execution; rejected requests do not consume a successful use.",
              ]} />
              <div className="configuration-field-grid">
                <TextField help="Operator-facing name for this ordered position-building step." label="Step name" onChange={(name) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, name } : row))} value={step.name} />
                <NumberField help="Maximum successful executions of this add step during one campaign." label="Maximum uses" minimum={1} onChange={(maximum_uses) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, maximum_uses } : row))} step={1} unit="fills" value={step.maximum_uses} />
                <BooleanField help="Disabled steps remain configured but cannot emit a capital request." label="Enabled" onChange={(enabled) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, enabled } : row))} value={step.enabled} />
                <button className="button compact danger" onClick={() => onChange(steps.filter((row) => row.step_id !== step.step_id))} type="button"><Trash2 size={14} /> Remove step</button>
              </div>
              <RuleStageComposition catalog={catalog} label={`${step.name} rules`} onChange={(rules) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, rules } : row))} onEditRuleSet={onRuleSetEdit} ruleSets={ruleSets} stage={step.rules} />
              <PhaseOrderEditor capitalRequest={step.capital_request} eligibleSessions={eligibleSessions} executionPolicies={executionPolicies} protectionProfiles={protectionProfiles} orderIntent={step.order_intent} title={`${step.name} request`} onCapitalRequest={(capital_request) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, capital_request } : row))} onOrderIntent={(order_intent) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, order_intent } : row))} />
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}

type ConfigurableSection = Exclude<TradingConfigurationSection, "strategy" | "discovery" | "revisions">;
type SectionStudioView = "guided" | "catalog" | "structure";

const SECTION_STUDIO_COPY: Record<ConfigurableSection, { managed: string; title: string }> = {
  accounts: { managed: "Manage accounts", title: "ACCOUNTS & SESSIONS" },
  assignments: { managed: "Manage Run Plans", title: "STRATEGY RUN PLANS" },
  oms: { managed: "Manage policies", title: "OMS & PROTECTION" },
  portfolio: { managed: "Manage mandates", title: "PORTFOLIO & RISK" },
};

const SECTION_SYSTEM_KEYS = new Set([
  "assignment_id", "condition_id", "group_id", "revision", "slice_id", "origin", "editable", "runtime_assignments", "mandate_ids",
]);

function ConfigurationSectionStudio({ draft, guided, onChange, section }: {
  draft: Draft;
  guided: ReactNode;
  onChange: (value: Draft[ConfigurableSection]) => void;
  section: ConfigurableSection;
}) {
  const [view, setView] = useState<SectionStudioView>("guided");
  const items = useMemo(() => sectionCatalogItems(section, draft), [draft, section]);
  const [selectedPath, setSelectedPath] = useState(items[0]?.path ?? "");
  const selected = items.find((item) => item.path === selectedPath) ?? items[0];
  useEffect(() => {
    if (!selectedPath || !items.some((item) => item.path === selectedPath)) setSelectedPath(items[0]?.path ?? "");
  }, [items, selectedPath]);
  const copy = SECTION_STUDIO_COPY[section];
  const structure = section === "assignments"
    ? <DeploymentEditor draft={draft} onChange={onChange as (value: AssignmentSection) => void} />
    : section === "portfolio"
      ? <PortfolioEditor draft={draft} onChange={onChange as (value: PortfolioSection) => void} />
      : section === "oms"
        ? <OmsEditor section={draft.oms} onChange={onChange as (value: OmsSection) => void} />
        : <AccountsEditor draft={draft} onChange={onChange as (value: AccountSection) => void} />;

  function replacePath(path: string, value: SectionCatalogValue) {
    onChange(setSectionValue(draft[section], path, value) as Draft[ConfigurableSection]);
  }

  return <div className="strategy-studio-workspace configuration-section-studio">
    <nav className="strategy-editor-toolbar">
      <span><strong>{copy.title}</strong><small>{view === "guided" ? "Guided Configuration" : view === "catalog" ? "Parameter Catalog" : copy.managed}</small></span>
      <div className="configuration-section-toolbar-actions">
        <div aria-label="Configuration view" className="strategy-editor-mode-tabs" role="tablist">
          <button aria-selected={view === "catalog"} onClick={() => setView("catalog")} role="tab" type="button"><Search size={13} /> Parameter Catalog</button>
          <button aria-selected={view === "guided"} onClick={() => setView("guided")} role="tab" type="button"><BookOpenCheck size={13} /> Guided Configuration</button>
        </div>
        <button className="button compact configuration-structure-button" onClick={() => setView(view === "structure" ? "guided" : "structure")} type="button"><Settings2 size={14} /> {view === "structure" ? "Done" : copy.managed}</button>
      </div>
    </nav>
    {view === "guided" ? <div className="configuration-guided-workspace configuration-section-guided">{guided}</div> : null}
    {view === "catalog" ? <div className="configuration-workbench strategy-editor-catalog configuration-section-catalog">
      <SectionParameterCatalog items={items} onSelect={setSelectedPath} selectedPath={selected?.path ?? ""} />
      {selected ? <SectionParameterDetail draft={draft} item={selected} onChange={(value) => replacePath(selected.path, value)} section={section} /> : <EmptyState detail="No user-adjustable parameters are available in this section." title="No parameters" />}
    </div> : null}
    {view === "structure" ? <div className="configuration-section-structure">{structure}</div> : null}
  </div>;
}

function SectionParameterCatalog({ items, onSelect, selectedPath }: { items: SectionCatalogItem[]; onSelect: (path: string) => void; selectedPath: string }) {
  const [query, setQuery] = useState("");
  const filtered = items.filter((item) => `${item.label} ${item.group} ${item.detail} ${item.path}`.toLowerCase().includes(query.trim().toLowerCase()));
  const groups = [...new Set(filtered.map((item) => item.group))];
  return <aside className="strategy-parameter-catalog">
    <header><div><span>Parameter catalog</span><strong>{filtered.length} of {items.length}</strong></div><p>Search every editable setting in this configuration.</p></header>
    <label className="strategy-parameter-search"><Search size={14} /><input aria-label="Search parameters" onChange={(event) => setQuery(event.target.value)} placeholder="Search parameters" value={query} /></label>
    <div className="strategy-parameter-list">{groups.map((group) => <section className="strategy-parameter-group" key={group}><header><span>{group}</span><strong>{filtered.filter((item) => item.group === group).length}</strong></header>{filtered.filter((item) => item.group === group).map((item) => <button aria-current={selectedPath === item.path ? "page" : undefined} key={item.id} onClick={() => onSelect(item.path)} type="button"><span><strong>{item.label}</strong><small>{item.detail}</small></span><ChevronRight size={14} /></button>)}</section>)}</div>
  </aside>;
}

function SectionParameterDetail({ draft, item, onChange, section }: { draft: Draft; item: SectionCatalogItem; onChange: (value: SectionCatalogValue) => void; section: ConfigurableSection }) {
  const documentation = sectionParameterDocumentation(section, item);
  return <main className="strategy-parameter-detail-page">
    <header><span>{item.group}</span><h2>{item.label}</h2><p>{item.detail}</p></header>
    <section className="strategy-parameter-editor"><SectionParameterField draft={draft} item={item} onChange={onChange} section={section} /></section>
    <ParameterDocumentation documentation={documentation} group={item.group} path={item.path} value={Array.isArray(item.value) ? item.value.join(", ") : item.value} />
    <section className="strategy-parameter-reference"><div><span>Configuration path</span><strong>{item.path}</strong></div><div><span>Value type</span><strong>{Array.isArray(item.value) ? "list" : item.value === null ? "optional number" : typeof item.value}</strong></div></section>
    <footer><Target size={18} /><div><strong>Authority</strong><p>{sectionAuthority(section)}</p></div></footer>
  </main>;
}

function SectionParameterField({ draft, item, onChange, section }: { draft: Draft; item: SectionCatalogItem; onChange: (value: SectionCatalogValue) => void; section: ConfigurableSection }) {
  const help = item.detail;
  const options = sectionParameterOptions(section, item.path, draft);
  if (Array.isArray(item.value)) {
    if (item.path.endsWith("allowed_environments") || item.path.endsWith(".modes")) return <div className="configuration-field configuration-list-field" data-editable="true"><span>{item.label}</span><ModeSelector modes={item.value as RuntimeMode[]} onChange={onChange} /><small>{help}</small></div>;
    return <TextField help={help} label={item.label} onChange={(value) => onChange(value.split(",").map((part) => part.trim()).filter(Boolean))} value={item.value.join(", ")} />;
  }
  if (options) return <SelectField help={help} label={item.label} onChange={onChange} options={options} value={String(item.value ?? "")} />;
  if (typeof item.value === "boolean") return <BooleanField help={help} label={item.label} onChange={onChange} value={item.value} />;
  if (typeof item.value === "number") return <NumberField help={help} label={item.label} onChange={onChange} step={stepFor(item.value)} unit={unitFor(item.path)} value={item.value} />;
  if (item.value === null) return <OptionalNumberField help={help} label={item.label} onChange={onChange} step={0.01} unit={unitFor(item.path)} value={null} />;
  if (item.path.endsWith("description")) return <label className="configuration-field configuration-textarea-field" data-editable="true"><span>{item.label}</span><small>{help}</small><textarea onChange={(event) => onChange(event.target.value)} value={String(item.value)} /></label>;
  return <TextField help={help} label={item.label} onChange={onChange} value={String(item.value)} />;
}

function sectionCatalogItems(section: ConfigurableSection, draft: Draft): SectionCatalogItem[] {
  const source = draft[section];
  return flattenSectionValues(source).filter(({ path }) => isSectionEditablePath(path)).map(({ path, value }) => {
    const group = sectionCatalogGroup(section, path, source);
    return { detail: sectionParameterHelp(section, path), group: group.group, groupOrder: group.order, id: `${section}:${path}`, label: sectionParameterLabel(path), path, value };
  }).sort((left, right) => left.groupOrder - right.groupOrder || left.label.localeCompare(right.label));
}

function flattenSectionValues(value: unknown, prefix = "", result: Array<{ path: string; value: SectionCatalogValue }> = []) {
  if (value === null && prefix) result.push({ path: prefix, value: null });
  else if (["boolean", "number", "string"].includes(typeof value) && prefix) result.push({ path: prefix, value: value as Primitive });
  else if (Array.isArray(value)) {
    if (value.every((item) => typeof item === "string")) result.push({ path: prefix, value: value as string[] });
    else value.forEach((item, index) => flattenSectionValues(item, `${prefix}.${index}`, result));
  } else if (value && typeof value === "object") Object.entries(value).forEach(([key, item]) => flattenSectionValues(item, prefix ? `${prefix}.${key}` : key, result));
  return result;
}

function isSectionEditablePath(path: string) {
  const parts = path.split(".");
  const leaf = parts.at(-1) ?? path;
  if (parts.some((part) => SECTION_SYSTEM_KEYS.has(part))) return false;
  if (["system_managed", "source_account_id", "scanner_view_id", "anchor_source"].includes(leaf)) return false;
  if (["emergency_exit", "protective_exit", "protective_exit_authority"].includes(leaf)) return false;
  if (/^deployments\.\d+\.run_plan_id$|^universes\.\d+\.universe_id$|^mandates\.\d+\.mandate_id$|^policies\.\d+\.policy_id$|^groups\.\d+\.group_id$|^profiles\.\d+\.profile_id$|^execution_policies\.\d+\.policy_id$|^protection_profiles\.\d+\.profile_id$|^bindings\.\d+\.account_key$/.test(path)) return false;
  if (/^protection_profiles\.\d+\.slices\.\d+\.slice_id$/.test(path)) return false;
  if (path.includes("runtime_assignments") || path.includes("safety_supervisor.enabled_by_environment.live")) return false;
  return true;
}

function sectionCatalogGroup(section: ConfigurableSection, path: string, source: Draft[ConfigurableSection]) {
  const [collection, rawIndex, subgroup] = path.split(".");
  const index = Number(rawIndex);
  const entity = Array.isArray((source as unknown as ParameterMap)[collection]) ? ((source as unknown as ParameterMap)[collection] as ParameterMap[])[index] : undefined;
  const name = typeof entity?.name === "string" ? entity.name : `${readableLabel(collection)} ${Number.isFinite(index) ? index + 1 : ""}`.trim();
  const orderMap: Record<ConfigurableSection, Record<string, number>> = {
    accounts: { bindings: 0 }, assignments: { deployments: 0, universes: 1 }, oms: { profiles: 0, execution_policies: 1, protection_profiles: 2 }, portfolio: { mandates: 0, policies: 1, groups: 2 },
  };
  return { group: subgroup === "slices" ? `${name} · Protection slices` : name, order: (orderMap[section][collection] ?? 9) * 100 + (Number.isFinite(index) ? index : 0) };
}

function sectionParameterLabel(path: string) {
  const parts = path.split(".");
  const rawLeaf = parts.at(-1) ?? path;
  const aliases: Record<string, string> = {
    book_id: "Portfolio book", oms_profile_id: "OMS profile", policy_id: "Policy", portfolio_policy_id: "Portfolio policy",
    profile_id: path.startsWith("deployments.") ? "Strategy Profile" : "Profile", protection_profile_id: "Protection profile",
    run_plan_id: "Run Plan", session_key: "Gateway session", source_account_env: "Broker account environment key", universe_id: "Watch Universe",
  };
  const leaf = aliases[rawLeaf] ?? readableLabel(rawLeaf);
  const parent = parts.at(-2) ?? "";
  if (/^\d+$/.test(parent)) return leaf;
  if (["settings", "envelope", "protection", "stop", "trailing"].includes(parent)) return `${readableLabel(parent)} · ${leaf}`;
  return leaf;
}

function sectionParameterHelp(section: ConfigurableSection, path: string) {
  const leaf = path.split(".").at(-1) ?? path;
  const specific: Record<string, string> = {
    account_class: "Sets the account capability class used by Portfolio and broker preflight.",
    allowed_environments: "Limits the runtime environments in which this Run Plan may be selected.",
    allocation_weight: "Sets this mandate's relative allocation when capital is distributed across eligible mandates.",
    enabled: "Includes or excludes this configured object from new runs after publication.",
    maximum_cash_fraction: "Caps the share of otherwise available account cash this mandate may use.",
    maximum_planned_risk_fraction: "Caps combined planned loss after broker state, reservations, and protective stops are reconciled.",
    maximum_positions: "Caps simultaneous open and reserved positions attributable to this mandate.",
    modes: "Limits which runtime modes may bind this account.",
    partial_fill_policy: "Controls what OMS does with the broker-confirmed unfilled remainder.",
    quote_source: "Selects the authoritative quote feed used by this execution policy.",
    source_account_env: "Names the local environment key that resolves the broker account without storing its value in configuration.",
  };
  return specific[leaf] ?? `Changes ${readableLabel(leaf).toLowerCase()} for this ${section === "assignments" ? "Run Plan configuration" : readableLabel(section)}.`;
}

function sectionParameterOptions(section: ConfigurableSection, path: string, draft: Draft) {
  const leaf = path.split(".").at(-1) ?? path;
  const values: Record<string, string[]> = {
    account_class: ["simulated", "paper", "cash", "margin", "registered"], add: ["inherit", "disabled", "manual", "confirm", "automatic"], add_policy: ["inherit", "replace", "append"], assignment_mode: ["single", "replicated", "weighted", "partitioned"],
    base_currency: ["USD", "CAD", "EUR", "GBP", "JPY"], maximum_action_authority: ["manual", "confirm", "automatic"],
    default: ["manual", "confirm", "automatic"], entry_urgency: ["patient", "regular", "urgent", "very_urgent"], exit_authority: ["manual", "confirm", "automatic"], exit_urgency: ["urgent", "very_urgent"],
    initial_entry: ["inherit", "disabled", "manual", "confirm", "automatic"], initial_entry_authority: ["manual", "confirm", "automatic"], order_type: ["STP", "STOP_LIMIT"],
    partial_fill_policy: ["complete_remainder", "accept_partial", "cancel_remainder"], profit_pocket_transition: ["move_to_breakeven", "start_swing_trail", "keep_existing"], quote_source: ["qmd", "ibkr", "simulated"],
    reentry: ["inherit", "disabled", "manual", "confirm", "automatic"], reentry_authority: ["manual", "confirm", "automatic"], session_end_behavior: ["keep_watching", "stop_when_flat", "exit_and_stop"], session_routing: ["smart"],
    source: ["configured_symbols", "scanner_view", "watchlist"], stop_method: ["structure", "volatility", "hybrid"], strategic_exit: ["inherit", "disabled", "manual", "confirm", "automatic"], structural_timeframe: ["100ms", "1s", "5s", "10s", "1m"],
  };
  if (path.endsWith(".stop.rule_type")) return ["fixed_price", "fixed_percent", "fixed_bps", "fixed_cash_risk", "swing_anchored", "volatility", "hybrid", "catastrophic"].map((value) => ({ label: readableLabel(value), value }));
  if (path.endsWith(".trailing.rule_type")) return ["none", "broker_amount", "broker_percent", "volatility_trail", "swing_trail", "chandelier", "breakeven_then_trail", "profit_lock_r", "time_tightening"].map((value) => ({ label: readableLabel(value), value }));
  if (path.endsWith("anchor_ordinal")) return ["most_recent", "second_recent", "third_recent", "fourth_recent"].map((value) => ({ label: readableLabel(value), value }));
  let options: Array<{ description?: string; label: string; value: string }> | undefined;
  if (leaf === "profile_id" && section === "assignments") options = draft.strategy.profiles.map((row) => ({ description: row.description, label: row.name, value: row.profile_id }));
  else if (leaf === "oms_profile_id") options = draft.oms.profiles.map((row) => ({ description: row.description, label: row.name, value: row.profile_id }));
  else if (leaf === "universe_id") options = draft.assignments.universes.map((row) => ({ description: row.description, label: row.name, value: row.universe_id }));
  else if (leaf === "account_key") options = draft.accounts.bindings.map((row) => ({ description: `${readableLabel(row.account_class)} account`, label: row.name, value: row.account_key }));
  else if (leaf === "run_plan_id") options = draft.assignments.deployments.map((row) => ({ description: row.description, label: row.name, value: row.run_plan_id }));
  else if (leaf === "portfolio_policy_id") options = draft.portfolio.policies.map((row) => ({ label: String(row.name ?? row.policy_id), value: String(row.policy_id) }));
  else if (leaf.endsWith("execution_policy_id")) options = draft.oms.execution_policies.map((row) => ({ description: row.description, label: row.name, value: row.policy_id }));
  else if (leaf === "protection_profile_id") options = draft.oms.protection_profiles.map((row) => ({ description: row.description, label: row.name, value: row.profile_id }));
  else if (leaf === "book_id") options = [{ description: "Use the default portfolio ownership and arbitration book.", label: "Default book", value: "default" }];
  else if (values[leaf]) options = values[leaf].map((value) => ({ label: readableLabel(value), value }));
  return options;
}

function setSectionValue<T>(source: T, path: string, value: SectionCatalogValue): T {
  const result = deepClone(source) as unknown;
  const parts = path.split(".");
  let cursor = result as ParameterMap | unknown[];
  parts.slice(0, -1).forEach((part) => { cursor = Array.isArray(cursor) ? cursor[Number(part)] as ParameterMap : cursor[part] as ParameterMap; });
  const leaf = parts.at(-1) ?? path;
  if (Array.isArray(cursor)) cursor[Number(leaf)] = value;
  else cursor[leaf] = value;
  return result as T;
}

function sectionAuthority(section: ConfigurableSection) {
  if (section === "assignments") return "Run Plans bind reusable behavior, universe, OMS, and permitted environments. They do not allocate account capital.";
  if (section === "portfolio") return "Portfolio approves account-specific quantity and arbitrates shared capital after synchronized broker state and risk checks.";
  if (section === "oms") return "OMS executes approved intent and maintains protection. It cannot increase Portfolio-approved quantity or invent Strategy intent.";
  return "Account bindings establish exact runtime identity and eligible modes. Broker identifiers remain local when an environment key is configured.";
}

function sectionParameterDocumentation(section: ConfigurableSection, item: SectionCatalogItem): StrategyParameterDocumentation {
  const value = Array.isArray(item.value) ? item.value.join(", ") : String(item.value ?? "Automatic");
  return {
    role: [item.detail, sectionAuthority(section)],
    timing: [`The session value is ${value}. It applies only to new runs after the configuration is validated and published.`],
    impact: [`Changing ${item.label.toLowerCase()} updates the ${readableLabel(section)} configuration used by every guided and catalog view in this session.`],
    caution: [section === "accounts" ? "Verify broker identity and session ownership before enabling Paper or Live. Secret account values are never stored through this catalog." : "Review dependent references and safety limits before publication; active pinned runs remain unchanged."],
    cautionTone: section === "accounts" || section === "portfolio" || section === "oms" ? "safety" : "warning",
  };
}

function DeploymentEditor({ draft, onChange }: { draft: Draft; onChange: (value: AssignmentSection) => void }) {
  const section = draft.assignments;
  const [selectedId, setSelectedId] = useState(section.deployments[0]?.run_plan_id ?? "");
  const selected = section.deployments.find((row) => row.run_plan_id === selectedId) ?? section.deployments[0];
  if (!selected) return <EmptyState title="No Run Plans" detail="Create a Run Plan to connect a Strategy Profile to runtime authority." />;
  const linkedMandates = draft.portfolio.mandates.filter((row) => row.run_plan_id === selected.run_plan_id);
  const readiness = [
    { label: "Watch Universe selected", ready: section.universes.some((row) => row.universe_id === selected.universe_id) },
    { label: "Strategy Profile selected", ready: draft.strategy.profiles.some((row) => row.profile_id === selected.profile_id) },
    { label: "OMS profile selected", ready: draft.oms.profiles.some((row) => row.profile_id === selected.oms_profile_id) },
    { label: "Account mandate configured", ready: linkedMandates.length > 0 },
    { label: "Replay enabled", ready: selected.allowed_environments.includes("replay") },
  ];

  function replace(next: StrategyRunPlan) {
    onChange({ ...section, deployments: section.deployments.map((row) => row.run_plan_id === selected.run_plan_id ? next : row) });
  }

  function createDeployment() {
    const id = uniqueId("new-run-plan", section.deployments.map((row) => row.run_plan_id));
    const next: StrategyRunPlan = {
      run_plan_id: id,
      name: "New Run Plan",
      description: "",
      profile_id: draft.strategy.profiles[0]?.profile_id ?? "",
      oms_profile_id: draft.oms.profiles[0]?.profile_id ?? "",
      universe_id: section.universes[0]?.universe_id ?? "",
      book_id: "default",
      campaign_lifecycle: {
        initial_entry_authority: "confirm",
        reentry_authority: "confirm",
        exit_authority: "automatic",
        protective_exit_authority: "automatic",
        maximum_reentries: 3,
        reentry_cooldown_ms: 1000,
        maximum_initial_watch_ms: 0,
        session_end_behavior: "keep_watching",
        retain_ticker_while_paused: true,
      },
      mandate_ids: [],
      enabled: true,
      allowed_environments: ["replay"],
      action_authority: { default: "confirm", initial_entry: "inherit", add: "inherit", reentry: "inherit", strategic_exit: "inherit", protective_exit: "automatic", emergency_exit: "automatic" },
      safety_supervisor: { enabled_by_environment: { replay: true, backtest: true, backtest_debug: true, paper: true, live: true } },
      runtime_assignments: [],
    };
    onChange({ ...section, deployments: [...section.deployments, next] });
    setSelectedId(id);
  }

  return (
    <div className="configuration-workbench">
      <aside className="configuration-library">
        <header><div><span>Run Plans</span><strong>{section.deployments.length} configured</strong></div><button onClick={createDeployment} title="Create Run Plan" type="button"><Plus size={15} /></button></header>
        <div>{section.deployments.map((row) => <button className={row.run_plan_id === selected.run_plan_id ? "active" : ""} key={row.run_plan_id} onClick={() => setSelectedId(row.run_plan_id)} type="button"><span><strong>{row.name}</strong><small>{row.enabled ? "Enabled" : "Disabled"} · {row.allowed_environments.map(readableLabel).join(", ")}</small></span><ChevronRight size={14} /></button>)}</div>
      </aside>
      <main className="configuration-detail">
        <section className="configuration-detail-heading">
          <div><span>Strategy Run Plan</span><input aria-label="Run Plan name" onChange={(event) => replace({ ...selected, name: event.target.value })} value={selected.name} /><textarea aria-label="Run Plan summary" onChange={(event) => replace({ ...selected, description: event.target.value })} rows={2} value={selected.description} /></div>
          <label className="configuration-enabled"><input checked={selected.enabled} onChange={(event) => replace({ ...selected, enabled: event.target.checked })} type="checkbox" /> Enabled</label>
        </section>
        <GuideCallout icon={<Network size={17} />} title="Profile → Run Plan → Strategy Campaign">
          The profile defines decisions. This Run Plan selects a Watch Universe and campaign authority. The shared Strategy Orchestrator grants one exclusive active campaign per ticker before Portfolio and OMS may act.
        </GuideCallout>
        <ConfigGroup summary="Select which approved stock universe this strategy may evaluate. Several strategies may observe a stock, but only one active campaign may own it." title="1. Watch Universe">
          <div className="configuration-field-grid">
            <SelectField help="Configured source of eligible symbols for this Run Plan." label="Universe" onChange={(universe_id) => replace({ ...selected, universe_id })} options={section.universes.map((row) => ({ label: row.name, value: row.universe_id }))} value={selected.universe_id} />
            <SelectField help="Ticker ownership is exclusive inside one portfolio book and runtime mode." label="Portfolio book" onChange={(book_id) => replace({ ...selected, book_id })} options={[{ label: "Default book", value: "default" }]} value={selected.book_id} />
          </div>
          <WatchUniverseEditor section={section} onChange={onChange} selectedId={selected.universe_id} />
        </ConfigGroup>
        <div className="configuration-two-column">
          <ConfigGroup summary="Select the configured behavior and shared execution profile." title="2. Strategy and execution">
            <div className="configuration-field-grid one-column">
              <SelectField help="Published Strategy Profile evaluated by this Run Plan." label="Strategy Profile" onChange={(value) => replace({ ...selected, profile_id: value })} options={draft.strategy.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={selected.profile_id} />
              <SelectField help="Reusable shared OMS and protection profile used to execute approved requests." label="OMS profile" onChange={(value) => replace({ ...selected, oms_profile_id: value })} options={draft.oms.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={selected.oms_profile_id} />
            </div>
          </ConfigGroup>
          <ConfigGroup summary="A release cannot run until its references and account mandates are complete." title="Readiness">
            <div className="configuration-readiness">{readiness.map((item) => <span data-ready={item.ready ? "true" : "false"} key={item.label}>{item.ready ? <CheckCircle2 size={14} /> : <TriangleAlert size={14} />}{item.label}</span>)}</div>
          </ConfigGroup>
        </div>
        <ConfigGroup summary="Set a default, then override individual actions." title="3. Action authority">
          <CampaignPolicyEditor deployment={selected} onChange={replace} />
        </ConfigGroup>
        <ConfigGroup summary="Select eligible environments and safety enforcement." title="4. Environments & safety">
          <ModeSelector modes={selected.allowed_environments} onChange={(allowed_environments) => replace({ ...selected, allowed_environments })} />
          <div className="configuration-field-grid">
            {(["replay", "backtest", "backtest_debug", "paper", "live"] as RuntimeMode[]).map((mode) => <BooleanField disabled={mode === "paper" || mode === "live"} help={mode === "paper" || mode === "live" ? "Mandatory in this environment." : "Enabled by default for historical analysis."} key={mode} label={`${readableLabel(mode)} safety`} onChange={(enabled) => replace({ ...selected, safety_supervisor: { enabled_by_environment: { ...selected.safety_supervisor.enabled_by_environment, [mode]: enabled } } })} value={selected.safety_supervisor.enabled_by_environment[mode]} />)}
          </div>
        </ConfigGroup>
        <ConfigGroup summary="Capital authority is configured on Portfolio & Risk. This page shows the linked account mandates." title="5. Account mandates">
          <div className="deployment-mandates">
            {linkedMandates.map((mandate) => <article key={mandate.mandate_id}><strong>{accountName(draft.accounts, mandate.account_key)}</strong><span>{percent(mandate.maximum_cash_fraction)} cash · {readableLabel(mandate.assignment_mode)} · max {readableLabel(mandate.maximum_action_authority)}</span></article>)}
            {!linkedMandates.length ? <EmptyState title="No account mandate" detail="Add an account mandate for this Run Plan." /> : null}
          </div>
          <a className="configuration-inline-link" href="#portfolio-configuration">Configure account mandates <ChevronRight size={13} /></a>
        </ConfigGroup>
      </main>
    </div>
  );
}

function WatchUniverseEditor({ onChange, section, selectedId }: {
  onChange: (value: AssignmentSection) => void;
  section: AssignmentSection;
  selectedId: string;
}) {
  const universe = section.universes.find((row) => row.universe_id === selectedId);
  if (!universe) return <EmptyState title="Universe unavailable" detail="Select or create a Watch Universe before publishing this Run Plan." />;
  const universeId = universe.universe_id;
  function replace(next: WatchUniverse) {
    onChange({ ...section, universes: section.universes.map((row) => row.universe_id === universeId ? next : row) });
  }
  return (
    <div className="watch-universe-editor">
      <div className="configuration-field-grid">
        <label className="configuration-text-field"><span>Universe name</span><input onChange={(event) => replace({ ...universe, name: event.target.value })} value={universe.name} /></label>
        <SelectField help="Configured symbols are runtime-ready. Scanner and Watchlist may be designed here, but publication stays blocked until their point-in-time membership resolver is registered." label="Source" onChange={(source) => replace({ ...universe, source: source as WatchUniverse["source"] })} options={["configured_symbols", "scanner_view", "watchlist"].map((value) => ({ label: readableLabel(value), value }))} value={universe.source} />
      </div>
      {universe.source !== "configured_symbols" ? (
        <p className="configuration-safety-note"><TriangleAlert size={15} /> Unresolved source: connect and validate the {readableLabel(universe.source)} membership resolver before this release can be published.</p>
      ) : null}
      {universe.source === "configured_symbols" ? (
        <label className="configuration-text-field">
          <span>Symbols <small>Comma-separated</small></span>
          <input onChange={(event) => replace({ ...universe, symbols: event.target.value.split(",").map((value) => value.trim().toUpperCase()).filter(Boolean) })} placeholder="AAPL, NVDA, TSLA" value={universe.symbols.join(", ")} />
        </label>
      ) : (
        <label className="configuration-text-field">
          <span>{universe.source === "scanner_view" ? "Scanner view id" : "Watchlist id"}</span>
          <input onChange={(event) => replace({ ...universe, scanner_view_id: event.target.value })} value={universe.scanner_view_id} />
        </label>
      )}
      <p>{universe.symbols.length} explicit symbols. Passive evaluation does not claim a ticker; ownership begins only when the orchestrator arms a campaign.</p>
    </div>
  );
}

function CampaignPolicyEditor({ deployment, onChange }: {
  deployment: StrategyRunPlan;
  onChange: (value: StrategyRunPlan) => void;
}) {
  const policy = deployment.campaign_lifecycle;
  const replace = (campaign_lifecycle: StrategyRunPlan["campaign_lifecycle"]) => onChange({ ...deployment, campaign_lifecycle });
  const authorities = ["inherit", "disabled", "manual", "confirm", "automatic"].map((value) => ({ label: readableLabel(value), value }));
  return (
    <>
      <div className="configuration-field-grid">
        <SelectField help="Inherited by actions without an override." label="Default authority" onChange={(value) => onChange({ ...deployment, action_authority: { ...deployment.action_authority, default: value as StrategyRunPlan["action_authority"]["default"] } })} options={["manual", "confirm", "automatic"].map((value) => ({ label: readableLabel(value), value }))} value={deployment.action_authority.default} />
        {(["initial_entry", "add", "reentry", "strategic_exit"] as const).map((action) => <SelectField help="Overrides the Run Plan default for this action." key={action} label={readableLabel(action)} onChange={(value) => onChange({ ...deployment, action_authority: { ...deployment.action_authority, [action]: value as ActionAuthority } })} options={action === "reentry" ? authorities : authorities.filter((row) => row.value !== "disabled")} value={deployment.action_authority[action]} />)}
        <SelectField disabled help="Cannot be weakened." label="Protective exit" onChange={() => undefined} options={[{ label: "Automatic", value: "automatic" }]} value="automatic" />
        <SelectField disabled help="Cannot be weakened." label="Emergency exit" onChange={() => undefined} options={[{ label: "Automatic", value: "automatic" }]} value="automatic" />
        <SelectField help="What happens to the ticker campaign at the configured session boundary." label="Session-end behavior" onChange={(session_end_behavior) => replace({ ...policy, session_end_behavior })} options={["keep_watching", "stop_when_flat", "exit_and_stop"].map((value) => ({ label: readableLabel(value), value }))} value={policy.session_end_behavior} />
        <NumberField help="Operational ceiling applied even if the Strategy Profile permits more reentries." label="Campaign reentry ceiling" minimum={0} onChange={(maximum_reentries) => replace({ ...policy, maximum_reentries })} step={1} unit="entries" value={policy.maximum_reentries} />
        <NumberField help="Operational cooldown applied before the Strategy Profile may evaluate another entry." label="Campaign cooldown" minimum={0} onChange={(reentry_cooldown_ms) => replace({ ...policy, reentry_cooldown_ms })} step={100} unit="ms" value={policy.reentry_cooldown_ms} />
        <NumberField help="Maximum time to retain a newly armed ticker while waiting for its initial entry. Zero means no time limit." label="Initial watch limit" minimum={0} onChange={(maximum_initial_watch_ms) => replace({ ...policy, maximum_initial_watch_ms })} step={60000} unit="ms" value={policy.maximum_initial_watch_ms} />
        <BooleanField help="A paused campaign keeps exclusive ticker ownership. Releasing it requires a separate safe handoff while flat." label="Retain ticker while paused" onChange={(retain_ticker_while_paused) => replace({ ...policy, retain_ticker_while_paused })} value={policy.retain_ticker_while_paused} />
      </div>
    </>
  );
}

function PortfolioEditor({ draft, onChange }: { draft: Draft; onChange: (value: PortfolioSection) => void }) {
  const section = draft.portfolio;
  const [selectedPolicyId, setSelectedPolicyId] = useState(String(section.policies[0]?.policy_id ?? ""));
  const policyIndex = Math.max(0, section.policies.findIndex((row) => String(row.policy_id) === selectedPolicyId));
  const policy = section.policies[policyIndex];

  function updatePolicy(key: string, value: Primitive | string[]) {
    const policies = section.policies.map((row, index) => index === policyIndex ? { ...row, [key]: value } : row);
    onChange({ ...section, policies });
  }

  function clonePolicy() {
    if (!policy) return;
    const policy_id = uniqueId(`${String(policy.policy_id)}-copy`, section.policies.map((row) => String(row.policy_id)));
    onChange({ ...section, policies: [...section.policies, { ...deepClone(policy), policy_id, revision: 1 }] });
    setSelectedPolicyId(policy_id);
  }

  function addMandate() {
    const deployment = draft.assignments.deployments[0];
    const account = draft.accounts.bindings[0];
    if (!deployment || !account) return;
    const mandateId = uniqueId(`${deployment.run_plan_id}-${account.account_key}`, section.mandates.map((row) => row.mandate_id));
    const mandate: Mandate = {
      mandate_id: mandateId,
      run_plan_id: deployment.run_plan_id,
      account_key: account.account_key,
      enabled: true,
      maximum_cash_fraction: 0.3,
      maximum_planned_risk_fraction: 0.01,
      maximum_positions: 10,
      assignment_mode: "single",
      allocation_weight: 1,
      maximum_action_authority: "confirm",
      allow_replacement: false,
      minimum_replacement_improvement_pct: 20,
    };
    onChange({ ...section, mandates: [...section.mandates, mandate] });
  }

  function replaceMandate(id: string, next: Mandate) {
    const mandates = section.mandates.map((row) => row.mandate_id === id ? next : row);
    onChange({ ...section, mandates });
  }

  function addGroup() {
    const group_id = uniqueId("account-group", section.groups.map((row) => String(row.group_id)));
    onChange({ ...section, groups: [...section.groups, { group_id, account_keys: [], maximum_gross_exposure: 0, maximum_ticker_exposure: 0 }] });
  }

  function replaceGroup(groupId: string, next: ParameterMap) {
    onChange({ ...section, groups: section.groups.map((row) => String(row.group_id) === groupId ? next : row) });
  }

  return (
    <div className="configuration-stack">
      <GuideCallout icon={<BriefcaseBusiness size={17} />} title="Strategy requests are relative; Portfolio makes them account-specific">
        A strategy may request an aggressive allocation, but the mandate and current account state determine final quantity. Replacement is a separately governed, auditable proposal—not an implicit strategy side effect.
      </GuideCallout>
      <ConfigGroup summary="Stable account-level limits apply to every strategy using the account." title="Account safety policy">
        <div className="configuration-toolbar">
          <SelectField help="Policy revision being edited." label="Policy" onChange={setSelectedPolicyId} options={section.policies.map((row) => ({ label: String(row.policy_id), value: String(row.policy_id) }))} value={selectedPolicyId} />
          <button className="button compact" onClick={clonePolicy} type="button"><Clipboard size={14} /> Clone policy</button>
        </div>
        {policy ? <div className="configuration-field-grid">
          {[
            field("revision", "Revision", "Immutable policy revision published with the release.", "number", undefined, "revision", 1),
            field("eligible_equity_fraction", "Eligible equity", "Fraction of account equity available to all trading mandates.", "number", undefined, "fraction", 0.05),
            field("minimum_cash_reserve", "Cash reserve", "Cash that Portfolio must leave unused.", "number", undefined, "currency", 100),
            field("maximum_buying_power_utilization", "Buying power use", "Maximum fraction of broker buying power Portfolio may consume.", "number", undefined, "fraction", 0.05),
            field("maximum_gross_exposure", "Gross exposure", "Maximum absolute long plus short exposure.", "number", undefined, "currency", 1000),
            field("maximum_net_long_exposure", "Net long exposure", "Maximum directional long exposure.", "number", undefined, "currency", 1000),
            field("maximum_net_short_exposure", "Net short exposure", "Maximum directional short exposure.", "number", undefined, "currency", 1000),
            field("maximum_position_fraction", "Position ceiling", "Maximum account equity attributable to one position.", "number", undefined, "fraction", 0.01),
            field("maximum_ticker_fraction", "Ticker ceiling", "Maximum account equity attributable to one ticker.", "number", undefined, "fraction", 0.01),
            field("maximum_strategy_fraction", "Strategy ceiling", "Maximum eligible equity allocated to one strategy.", "number", undefined, "fraction", 0.01),
            field("maximum_sector_fraction", "Sector ceiling", "Maximum eligible equity allocated to one sector.", "number", undefined, "fraction", 0.01),
            field("maximum_industry_fraction", "Industry ceiling", "Maximum eligible equity allocated to one industry.", "number", undefined, "fraction", 0.01),
            field("maximum_correlated_group_fraction", "Correlated-group ceiling", "Maximum eligible equity allocated to one correlated group.", "number", undefined, "fraction", 0.01),
            field("maximum_planned_risk_fraction", "Per-request risk", "Maximum planned loss for one approved request.", "number", undefined, "fraction", 0.001),
            field("maximum_open_risk_fraction", "Open risk ceiling", "Maximum aggregate planned open risk.", "number", undefined, "fraction", 0.005),
            field("maximum_open_positions", "Open positions", "Maximum simultaneous positions for this account policy.", "number", undefined, "positions", 1),
            field("maximum_order_quantity", "Order quantity", "Maximum approved quantity for one order request.", "number", undefined, "shares", 1),
            field("maximum_order_notional", "Order notional", "Maximum worst-price notional for one order request.", "number", undefined, "currency", 1000),
            field("maximum_daily_loss", "Daily loss limit", "New entries stop when the loss limit is reached.", "number", undefined, "currency", 100),
            field("maximum_drawdown", "Drawdown limit", "Hard peak-to-trough account control.", "number", undefined, "currency", 100),
            field("daily_loss_warning", "Loss warning", "Pause entries before the hard daily-loss limit.", "number", undefined, "currency", 100),
            field("emergency_loss", "Emergency loss", "Escalate to the configured emergency action at this daily loss.", "number", undefined, "currency", 100),
            field("maximum_snapshot_age_ms", "Snapshot age", "Maximum broker-state age allowed for new risk.", "number", undefined, "ms", 100),
            field("maximum_protection_slices", "Protection slices", "Maximum independently protected slices per entry or add.", "number", undefined, "slices", 1),
            field("maximum_internal_reaction_ms", "Reaction limit", "Maximum measured internal risk reaction time.", "number", undefined, "ms", 10),
        ].map((definition) => <ParameterField definition={definition} key={definition.path} value={policy[definition.path] as Primitive} onChange={(value) => updatePolicy(definition.path, value)} />)}
          <div className="configuration-fixed-value"><span>Stable policy ID</span><strong>{String(policy.policy_id)}</strong><small>Clone the policy to create a new identity.</small></div>
          {[
            ["allow_long", "Allow long", "Permit new long exposure."],
            ["allow_short", "Allow short", "Permit new short exposure when the account also supports it."],
            ["allow_margin", "Allow margin", "Permit margin use when the broker account supports it."],
            ["allow_unsettled_cash", "Allow unsettled cash", "Permit eligible unsettled cash in buying power."],
            ["allow_outside_rth", "Allow extended hours", "Permit orders outside regular trading hours."],
            ["allow_overnight", "Allow overnight", "Permit positions to remain open overnight."],
            ["block_on_unattributed_position", "Block unattributed positions", "Fail closed when broker positions cannot be attributed."],
            ["allow_stop_limit_protection", "Allow stop-limit protection", "Permit protection that may remain unfilled through a gap."],
            ["allow_partial_profit_pocket", "Allow partial profit pocket", "Permit profit taking that leaves a managed remainder."],
            ["allow_emergency_auto_liquidation", "Emergency auto-liquidation", "Authorize account-scoped emergency flattening after reconciliation."],
          ].map(([key, label, help]) => <BooleanField help={help} key={key} label={label} onChange={(value) => updatePolicy(key, value)} value={Boolean(policy[key])} />)}
          {[
            ["allowed_security_types", "Security types", "Allowed broker security types such as STK."],
            ["allowed_currencies", "Currencies", "Allowed contract and ledger currencies."],
            ["restricted_symbols", "Restricted symbols", "Symbols that Portfolio must reject."],
            ["allowed_execution_policies", "Execution allowlist", "Policy IDs or immutable identities; use * for all."],
            ["allowed_protection_profiles", "Protection allowlist", "Profile IDs or immutable identities; use * for all."],
          ].map(([key, label, help]) => <TextField help={help} key={key} label={label} onChange={(value) => updatePolicy(key, value.split(",").map((item) => item.trim()).filter(Boolean))} value={Array.isArray(policy[key]) ? (policy[key] as string[]).join(", ") : ""} />)}
        </div> : null}
      </ConfigGroup>
      <ConfigGroup
        action={<button className="button compact" onClick={addMandate} type="button"><Plus size={14} /> Add mandate</button>}
        summary="Assign each Run Plan to one or more governed accounts."
        title="Strategy-account mandates"
      >
        <div className="mandate-grid">
          {section.mandates.map((mandate) => (
            <article className="mandate-card" key={mandate.mandate_id}>
              <header>
                <div><strong>{deploymentName(draft.assignments, mandate.run_plan_id)}</strong><span>{accountName(draft.accounts, mandate.account_key)}</span></div>
                <button aria-label="Delete mandate" onClick={() => onChange({ ...section, mandates: section.mandates.filter((row) => row.mandate_id !== mandate.mandate_id) })} title="Delete mandate" type="button"><Trash2 size={14} /></button>
              </header>
              <div className="configuration-field-grid one-column">
                <SelectField help="Run Plan allowed to request capital." label="Run Plan" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, run_plan_id: value })} options={draft.assignments.deployments.map((row) => ({ label: row.name, value: row.run_plan_id }))} value={mandate.run_plan_id} />
                <SelectField help="Account whose cash, positions, and risk state govern the request." label="Account" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, account_key: value })} options={draft.accounts.bindings.map((row) => ({ label: row.name, value: row.account_key }))} value={mandate.account_key} />
                <NumberField help="Maximum account cash this Run Plan may use." label="Maximum cash" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, maximum_cash_fraction: value })} step={0.05} unit="fraction" value={mandate.maximum_cash_fraction} />
                <NumberField help="Maximum planned loss admitted for one request under this mandate." label="Planned risk" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, maximum_planned_risk_fraction: value })} step={0.001} unit="fraction" value={mandate.maximum_planned_risk_fraction} />
                <SelectField help="How this Run Plan uses its assigned accounts." label="Assignment mode" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, assignment_mode: value as Mandate["assignment_mode"] })} options={["single", "replicated", "weighted", "partitioned"].map((value) => ({ label: readableLabel(value), value }))} value={mandate.assignment_mode} />
                {mandate.assignment_mode === "weighted" ? <NumberField help="Relative allocation weight across assigned accounts." label="Allocation weight" minimum={0.01} onChange={(allocation_weight) => replaceMandate(mandate.mandate_id, { ...mandate, allocation_weight })} step={0.1} value={mandate.allocation_weight} /> : null}
                <SelectField help="Caps exposure-increasing Run Plan actions on this account; exits may remain automatic." label="Maximum action authority" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, maximum_action_authority: value as Mandate["maximum_action_authority"] })} options={["manual", "confirm", "automatic"].map((value) => ({ label: readableLabel(value), value }))} value={mandate.maximum_action_authority} />
                <BooleanField help="Allows Portfolio to propose reductions or exits to fund a stronger request." label="Allow replacement proposals" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, allow_replacement: value })} value={mandate.allow_replacement} />
                {mandate.allow_replacement ? <NumberField help="Required improvement over an existing position before replacement can be proposed." label="Minimum improvement" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, minimum_replacement_improvement_pct: value })} step={1} unit="%" value={mandate.minimum_replacement_improvement_pct} /> : null}
              </div>
            </article>
          ))}
        </div>
      </ConfigGroup>
      <ConfigGroup action={<button className="button compact" onClick={addGroup} type="button"><Plus size={14} /> Add account group</button>} summary="Optional aggregate limits serialize decisions across several independently configured accounts." title="Cross-account risk groups">
        <div className="mandate-grid">
          {section.groups.map((group) => {
            const groupId = String(group.group_id || "");
            const accountKeys = Array.isArray(group.account_keys) ? group.account_keys.map(String) : [];
            return <article className="mandate-card" key={groupId}>
              <header><div><strong>{groupId}</strong><span>{accountKeys.length} accounts</span></div><button aria-label={`Delete ${groupId}`} onClick={() => onChange({ ...section, groups: section.groups.filter((row) => String(row.group_id) !== groupId) })} type="button"><Trash2 size={14} /></button></header>
              <div className="configuration-field-grid one-column">
                <div className="configuration-fixed-value"><span>Stable group ID</span><strong>{groupId}</strong><small>Recorded in reservations and decisions.</small></div>
                <NumberField help="Maximum combined absolute exposure across group accounts." label="Gross exposure" minimum={0} onChange={(value) => replaceGroup(groupId, { ...group, maximum_gross_exposure: value })} step={1000} unit="currency" value={Number(group.maximum_gross_exposure || 0)} />
                <NumberField help="Maximum combined exposure to one ticker across group accounts." label="Ticker exposure" minimum={0} onChange={(value) => replaceGroup(groupId, { ...group, maximum_ticker_exposure: value })} step={1000} unit="currency" value={Number(group.maximum_ticker_exposure || 0)} />
              </div>
              <fieldset className="configuration-choice-set"><legend>Member accounts</legend><div>{draft.accounts.bindings.map((account) => <label key={account.account_key}><input checked={accountKeys.includes(account.account_key)} onChange={(event) => replaceGroup(groupId, { ...group, account_keys: event.target.checked ? [...accountKeys, account.account_key] : accountKeys.filter((key) => key !== account.account_key) })} type="checkbox" />{account.name}</label>)}</div></fieldset>
            </article>;
          })}
          {!section.groups.length ? <EmptyState title="No cross-account groups" detail="Each account remains independently governed until an aggregate group is added." /> : null}
        </div>
      </ConfigGroup>
    </div>
  );
}

function OmsEditor({ onChange, section }: { onChange: (value: OmsSection) => void; section: OmsSection }) {
  const [selectedId, setSelectedId] = useState(section.profiles[0]?.profile_id ?? "");
  const selected = section.profiles.find((row) => row.profile_id === selectedId) ?? section.profiles[0];
  if (!selected) return <EmptyState title="No OMS profile" detail="Create a shared execution and protection profile." />;
  function replace(next: OmsProfile) {
    onChange({ ...section, profiles: section.profiles.map((row) => row.profile_id === selected.profile_id ? next : row) });
  }
  function clone() {
    const id = uniqueId(`${selected.profile_id}-copy`, section.profiles.map((row) => row.profile_id));
    const next = { ...deepClone(selected), profile_id: id, name: `${selected.name} copy`, origin: "user" as const, revision: 1 };
    onChange({ ...section, profiles: [...section.profiles, next] });
    setSelectedId(id);
  }
  return (
    <div className="configuration-stack">
    <div className="configuration-workbench">
      <aside className="configuration-library">
        <header><div><span>OMS profiles</span><strong>{section.profiles.length} configured</strong></div><button onClick={clone} title="Clone OMS profile" type="button"><Plus size={15} /></button></header>
        <p>Reusable profiles keep execution mechanics consistent across strategies and modes.</p>
        <div>{section.profiles.map((row) => <button className={row.profile_id === selected.profile_id ? "active" : ""} key={row.profile_id} onClick={() => setSelectedId(row.profile_id)} type="button"><span><strong>{row.name}</strong><small>{row.origin} · v{row.revision}</small></span><ChevronRight size={14} /></button>)}</div>
      </aside>
      <main className="configuration-detail">
        <section className="configuration-detail-heading"><div><span>Reusable OMS profile</span><input aria-label="OMS profile name" onChange={(event) => replace({ ...selected, name: event.target.value })} value={selected.name} /><textarea aria-label="OMS profile summary" onChange={(event) => replace({ ...selected, description: event.target.value })} rows={2} value={selected.description} /></div><button className="button compact" onClick={clone} type="button"><Clipboard size={14} /> Clone</button></section>
        <GuideCallout icon={<ShieldCheck size={17} />} title="Execution mechanics are shared">
          Strategy decides what it wants and Portfolio approves account quantity. OMS decides how to work the order, reconcile fills, and maintain broker-held protection without expanding the approved envelope.
        </GuideCallout>
        <ConfigGroup summary="Common execution choices used for new entries and exits." title="Execution behavior">
          <div className="strategy-smart-session configuration-smart-routing">
            <ShieldCheck size={17} />
            <div><span>Smart session routing</span><strong>Automatic broker-compatible instructions</strong><p>OMS derives regular or extended-session handling from each Strategy Profile's Trading Behavior, then validates the account, venue, broker, and order type before submission.</p></div>
            <FieldHelp content={{ role: "Keeps broker mechanics centralized in OMS while Strategy Profiles define only when they are eligible to trade.", parameters: { "Strategy input": "Eligible sessions from Trading Behavior.", "OMS decision": "Compatible time in force, outside-hours flag, venue, and broker order instructions.", "Safety gate": "Account, broker, venue, and order-type support must all permit the resolved instruction." }, note: "There is intentionally no manual time-in-force or outside-hours override here. A raw override could contradict the strategy session or broker capabilities." }} />
          </div>
          <div className="configuration-field-grid">
            <SelectField help="Default entry policy used when a phase does not override execution." label="Default entry policy" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, entry_execution_policy_id: value } })} options={section.execution_policies.map((policy) => ({ label: `${readableLabel(policy.name)} · v${policy.revision}`, value: policy.policy_id }))} value={selected.settings.entry_execution_policy_id} />
            <SelectField help="Default risk-reducing and final-exit execution policy." label="Default exit policy" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, exit_execution_policy_id: value } })} options={section.execution_policies.map((policy) => ({ label: `${readableLabel(policy.name)} · v${policy.revision}`, value: policy.policy_id }))} value={selected.settings.exit_execution_policy_id} />
            <SelectField help="Default independently versioned protection plan for entries and adds." label="Default protection" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, protection_profile_id: value } })} options={section.protection_profiles.map((profile) => ({ label: `${profile.name} · v${profile.revision}`, value: profile.profile_id }))} value={selected.settings.protection_profile_id} />
            <SelectField help="Default urgency for entries. Strategy capabilities may select only an allowed profile." label="Entry urgency" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, entry_urgency: value } })} options={urgencyOptions()} value={selected.settings.entry_urgency} />
            <SelectField help="Default urgency for risk-reducing and final exits." label="Exit urgency" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, exit_urgency: value } })} options={urgencyOptions()} value={selected.settings.exit_urgency} />
            <NumberField help="Permitted limit-price offset from current execution evidence." label="Limit offset" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, limit_offset_bps: value } })} step={0.5} unit="bps" value={selected.settings.limit_offset_bps} />
            <NumberField help="Minimum price increment used by the planner." label="Tick size" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, tick_size: value } })} step={0.01} unit="price" value={selected.settings.tick_size} />
          </div>
        </ConfigGroup>
        <ConfigGroup summary="Stops and trails are held and reconciled by the shared OMS." title="Protection">
          <div className="configuration-field-grid">
            <SelectField help="Choose structural, volatility, or stricter hybrid invalidation." label="Stop method" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, protection: { ...selected.settings.protection, stop_method: value } } })} options={["structure", "volatility", "hybrid"].map((value) => ({ label: readableLabel(value), value }))} value={selected.settings.protection.stop_method} />
            <NumberField help="Distance beyond causal structure before invalidation." label="Structure buffer" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, protection: { ...selected.settings.protection, structure_buffer_bps: value } } })} step={0.5} unit="bps" value={selected.settings.protection.structure_buffer_bps} />
            <NumberField help="Volatility distance used when structure alone is insufficient." label="Volatility multiple" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, protection: { ...selected.settings.protection, volatility_multiple: value } } })} step={0.05} unit="×" value={selected.settings.protection.volatility_multiple} />
            <NumberField help="Maximum strategy risk percentage used when constructing protection." label="Maximum risk" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, protection: { ...selected.settings.protection, maximum_risk_pct: value } } })} step={0.1} unit="%" value={selected.settings.protection.maximum_risk_pct} />
            <BooleanField help="Allow protection to tighten as favorable evidence develops." label="Trailing enabled" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, protection: { ...selected.settings.protection, trailing_enabled: value } } })} value={selected.settings.protection.trailing_enabled} />
          </div>
        </ConfigGroup>
      </main>
    </div>
    <ExecutionPoliciesEditor policies={section.execution_policies} onChange={(execution_policies) => onChange({ ...section, execution_policies })} />
    <ProtectionProfilesEditor profiles={section.protection_profiles} onChange={(protection_profiles) => onChange({ ...section, protection_profiles })} />
    </div>
  );
}

function ExecutionPoliciesEditor({ onChange, policies }: { onChange: (value: ExecutionPolicyConfig[]) => void; policies: ExecutionPolicyConfig[] }) {
  const [selectedId, setSelectedId] = useState(policies[0]?.policy_id ?? "");
  const selected = policies.find((row) => row.policy_id === selectedId) ?? policies[0];
  if (!selected) return <EmptyState title="No execution policies" detail="Create at least one bounded broker-neutral execution policy." />;
  const replace = (next: ExecutionPolicyConfig) => onChange(policies.map((row) => row.policy_id === selected.policy_id ? next : row));
  const clone = () => {
    const policy_id = uniqueId(`${selected.policy_id}-copy`, policies.map((row) => row.policy_id));
    onChange([...policies, { ...deepClone(selected), policy_id, name: selected.name, description: `${selected.description} Copy.`, origin: "user", revision: 1 }]);
    setSelectedId(policy_id);
  };
  return <ConfigGroup action={<button className="button compact" onClick={clone} type="button"><Clipboard size={14} /> Clone execution policy</button>} summary="Each immutable policy owns its quote authority, price envelope, bounded repricing, deadline, and partial-fill behavior." title="Execution policy catalog">
    <div className="configuration-toolbar"><SelectField help="Execution policy revision being edited." label="Policy" onChange={setSelectedId} options={policies.map((row) => ({ label: `${readableLabel(row.name)} · ${row.policy_id}@${row.revision}`, value: row.policy_id }))} value={selectedId} /></div>
    <div className="configuration-field-grid">
      <div className="configuration-fixed-value"><span>Stable policy ID</span><strong>{selected.policy_id}</strong><small>Clone the policy to create a new identity.</small></div>
      <NumberField help="Immutable revision published with the release." label="Revision" minimum={1} onChange={(revision) => replace({ ...selected, revision })} step={1} unit="revision" value={selected.revision} />
      <SelectField help="Adaptive behavior implemented by OMS." label="Policy behavior" onChange={(name) => replace({ ...selected, name })} options={["passive", "midpoint", "adaptive_patient", "adaptive_regular", "adaptive_urgent", "adaptive_very_urgent", "immediate_with_limit", "ibkr_native_adaptive", "cancel_if_not_filled"].map((value) => ({ label: readableLabel(value), value }))} value={selected.name} />
      <SelectField help="Market-data authority used for execution-time repricing." label="Quote source" onChange={(quote_source) => replace({ ...selected, quote_source: quote_source as ExecutionPolicyConfig["quote_source"] })} options={["qmd", "ibkr", "simulated"].map((value) => ({ label: readableLabel(value), value }))} value={selected.quote_source} />
      <SelectField help="Action applied to the broker-known unfilled quantity." label="Partial fill" onChange={(partial_fill_policy) => replace({ ...selected, partial_fill_policy: partial_fill_policy as ExecutionPolicyConfig["partial_fill_policy"] })} options={["complete_remainder", "accept_partial", "cancel_remainder"].map((value) => ({ label: readableLabel(value), value }))} value={selected.partial_fill_policy} />
      <OptionalNumberField help="Hard buy-price ceiling. Empty means the strategy or broker band supplies the boundary." label="Maximum buy price" minimum={0.0001} onChange={(maximum_buy_price) => replace({ ...selected, envelope: { ...selected.envelope, maximum_buy_price } })} step={0.01} unit="price" value={selected.envelope.maximum_buy_price} />
      <OptionalNumberField help="Hard sell-price floor. Empty means the strategy or broker band supplies the boundary." label="Minimum sell price" minimum={0.0001} onChange={(minimum_sell_price) => replace({ ...selected, envelope: { ...selected.envelope, minimum_sell_price } })} step={0.01} unit="price" value={selected.envelope.minimum_sell_price} />
      <NumberField help="Maximum time OMS may work this policy." label="Deadline" minimum={0} onChange={(deadline_ms) => replace({ ...selected, envelope: { ...selected.envelope, deadline_ms } })} step={25} unit="ms" value={selected.envelope.deadline_ms} />
      <NumberField help="Maximum broker modifications before terminal policy applies." label="Maximum reprices" minimum={0} onChange={(maximum_reprices) => replace({ ...selected, envelope: { ...selected.envelope, maximum_reprices } })} step={1} unit="replaces" value={selected.envelope.maximum_reprices} />
      <NumberField help="Minimum interval between modifications; partial-fill events still wake OMS immediately." label="Reprice interval" minimum={0} onChange={(minimum_reprice_interval_ms) => replace({ ...selected, envelope: { ...selected.envelope, minimum_reprice_interval_ms } })} step={5} unit="ms" value={selected.envelope.minimum_reprice_interval_ms} />
    </div>
  </ConfigGroup>;
}

function ProtectionProfilesEditor({ onChange, profiles }: { onChange: (value: ProtectionProfileConfig[]) => void; profiles: ProtectionProfileConfig[] }) {
  const [selectedId, setSelectedId] = useState(profiles[0]?.profile_id ?? "");
  const selected = profiles.find((row) => row.profile_id === selectedId) ?? profiles[0];
  if (!selected) return <EmptyState title="No protection profiles" detail="Create at least one broker-held protection profile." />;
  const replace = (next: ProtectionProfileConfig) => onChange(profiles.map((row) => row.profile_id === selected.profile_id ? next : row));
  const replaceSlice = (sliceId: string, next: ProtectionSliceConfig) => replace({ ...selected, slices: selected.slices.map((row) => row.slice_id === sliceId ? next : row) });
  const rebalance = (rows: ProtectionSliceConfig[]) => rows.map((row) => ({ ...row, quantity_fraction: 1 / rows.length }));
  const addSlice = () => {
    if (selected.slices.length >= 4) return;
    const slice_id = uniqueId("slice", selected.slices.map((row) => row.slice_id));
    const template = deepClone(selected.slices[0]);
    replace({ ...selected, slices: rebalance([...selected.slices, { ...template, slice_id, stop: { ...template.stop, anchor_ordinal: ["most_recent", "second_recent", "third_recent", "fourth_recent"][selected.slices.length] } }]) });
  };
  const removeSlice = (sliceId: string) => {
    if (selected.slices.length <= 1) return;
    replace({ ...selected, slices: rebalance(selected.slices.filter((row) => row.slice_id !== sliceId)) });
  };
  const clone = () => {
    const profile_id = uniqueId(`${selected.profile_id}-copy`, profiles.map((row) => row.profile_id));
    onChange([...profiles, { ...deepClone(selected), profile_id, name: `${selected.name} copy`, origin: "user", revision: 1 }]);
    setSelectedId(profile_id);
  };
  return <ConfigGroup action={<div><button className="button compact" onClick={clone} type="button"><Clipboard size={14} /> Clone profile</button> <button className="button compact" disabled={selected.slices.length >= 4} onClick={addSlice} type="button"><Plus size={14} /> Add slice</button></div>} summary="One to four fractions must total exactly 100 percent. Every slice owns a hard stop and may own a target and trailing rule." title="Protection profile catalog">
    <div className="configuration-toolbar"><SelectField help="Protection profile revision being edited." label="Profile" onChange={setSelectedId} options={profiles.map((row) => ({ label: `${row.name} · ${row.profile_id}@${row.revision}`, value: row.profile_id }))} value={selectedId} /></div>
    <div className="configuration-field-grid">
      <div className="configuration-fixed-value"><span>Stable profile ID</span><strong>{selected.profile_id}</strong><small>Clone the profile to create a new identity.</small></div>
      <NumberField help="Immutable revision published with the release." label="Revision" minimum={1} onChange={(revision) => replace({ ...selected, revision })} step={1} unit="revision" value={selected.revision} />
      <TextField help="Operator-facing profile name." label="Profile name" onChange={(name) => replace({ ...selected, name })} value={selected.name} />
      <SelectField help="How a filled add changes existing protection." label="Add protection" onChange={(add_policy) => replace({ ...selected, add_policy })} options={["independent_slice", "inherit_position_stop", "rebase_all", "tighten_only", "preserve_existing"].map((value) => ({ label: readableLabel(value), value }))} value={selected.add_policy} />
      <SelectField help="Protection transition after an actual profit-pocket fill." label="Profit-pocket transition" onChange={(profit_pocket_transition) => replace({ ...selected, profit_pocket_transition })} options={["keep_existing", "move_to_breakeven", "lock_profit_price", "start_broker_trail", "start_volatility_trail", "start_swing_trail", "tighten_existing", "replan_remaining_slices", "full_exit_and_optional_reentry"].map((value) => ({ label: readableLabel(value), value }))} value={selected.profit_pocket_transition} />
      <NumberField help="Maximum time allowed to repair missing broker-held protection." label="Repair deadline" minimum={1} onChange={(emergency_repair_deadline_ms) => replace({ ...selected, emergency_repair_deadline_ms })} step={25} unit="ms" value={selected.emergency_repair_deadline_ms} />
      <BooleanField help="Require OMS to retain or repair a catastrophic broker-held backstop." label="Mandatory catastrophic backstop" onChange={(mandatory_catastrophic_backstop) => replace({ ...selected, mandatory_catastrophic_backstop })} value={selected.mandatory_catastrophic_backstop} />
    </div>
    <div className="mandate-grid">{selected.slices.map((slice) => <article className="mandate-card" key={slice.slice_id}>
      <header><div><strong>{slice.slice_id}</strong><span>{round(slice.quantity_fraction * 100)}% of filled quantity</span></div><button aria-label={`Delete ${slice.slice_id}`} disabled={selected.slices.length <= 1} onClick={() => removeSlice(slice.slice_id)} type="button"><Trash2 size={14} /></button></header>
      <div className="configuration-field-grid one-column">
        <TextField help="Stable slice identity used in broker mappings and fill attribution." label="Slice ID" onChange={(slice_id) => replaceSlice(slice.slice_id, { ...slice, slice_id })} value={slice.slice_id} />
        <NumberField help="Fraction of the filled entry protected by this slice; all slices must total 100 percent." label="Quantity fraction" maximum={1} minimum={0.01} onChange={(quantity_fraction) => replaceSlice(slice.slice_id, { ...slice, quantity_fraction })} step={0.05} unit="fraction" value={slice.quantity_fraction} />
        <SelectField help="Hard-stop calculation applied from causal entry evidence." label="Stop rule" onChange={(rule_type) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, rule_type } })} options={["fixed_price", "fixed_percent", "fixed_bps", "fixed_cash_risk", "swing_anchored", "volatility", "hybrid", "catastrophic"].map((value) => ({ label: readableLabel(value), value }))} value={slice.stop.rule_type} />
        <SelectField help="Broker-held stop type. Stop-limit requires account-policy permission and may not fill through a gap." label="Stop order" onChange={(order_type) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, order_type: order_type as ProtectionStopConfig["order_type"] } })} options={[{ label: "Stop market", value: "STP" }, { label: "Stop limit", value: "STOP_LIMIT" }]} value={slice.stop.order_type} />
        {slice.stop.rule_type === "fixed_price" || slice.stop.rule_type === "catastrophic" ? <OptionalNumberField help="Absolute stop price. Empty uses the strategy-computed causal invalidation price." label="Stop price" minimum={0.0001} onChange={(price) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, price } })} step={0.01} unit="price" value={slice.stop.price} /> : null}
        {slice.stop.rule_type === "fixed_percent" ? <OptionalNumberField help="Distance from the approved entry price." label="Stop distance" minimum={0} onChange={(distance_percent) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, distance_percent } })} step={0.1} unit="%" value={slice.stop.distance_percent} /> : null}
        {slice.stop.rule_type === "fixed_bps" ? <OptionalNumberField help="Distance from the approved entry price." label="Stop distance" minimum={0} onChange={(distance_bps) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, distance_bps } })} step={1} unit="bps" value={slice.stop.distance_bps} /> : null}
        {slice.stop.rule_type === "fixed_cash_risk" ? <OptionalNumberField help="Maximum cash loss assigned to this slice." label="Cash risk" minimum={0} onChange={(maximum_cash_risk) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, maximum_cash_risk } })} step={10} unit="currency" value={slice.stop.maximum_cash_risk} /> : null}
        {["swing_anchored", "hybrid"].includes(slice.stop.rule_type) ? <><SelectField help="Causal confirmed swing selected from the strategy observation history." label="Swing ordinal" onChange={(anchor_ordinal) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, anchor_source: "strategy_swing", anchor_ordinal } })} options={["most_recent", "second_recent", "third_recent", "fourth_recent"].map((value) => ({ label: readableLabel(value), value }))} value={slice.stop.anchor_ordinal} /><TextField help="Timeframe recorded with the selected structural anchor." label="Structure timeframe" onChange={(structural_timeframe) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, structural_timeframe } })} value={slice.stop.structural_timeframe} /><NumberField help="Additional distance beyond the confirmed swing." label="Structure buffer" minimum={0} onChange={(buffer_bps) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, buffer_bps } })} step={0.5} unit="bps" value={slice.stop.buffer_bps} /></> : null}
        {["volatility", "hybrid"].includes(slice.stop.rule_type) ? <OptionalNumberField help="Volatility distance used to resolve the stop." label="Volatility multiple" minimum={0.01} onChange={(volatility_multiple) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, volatility_multiple } })} step={0.05} unit="×" value={slice.stop.volatility_multiple} /> : null}
        {slice.stop.order_type === "STOP_LIMIT" ? <OptionalNumberField help="Positive limit offset beyond the stop trigger." label="Stop-limit offset" minimum={0.01} onChange={(stop_limit_offset_bps) => replaceSlice(slice.slice_id, { ...slice, stop: { ...slice.stop, stop_limit_offset_bps } })} step={0.5} unit="bps" value={slice.stop.stop_limit_offset_bps} /> : null}
        <BooleanField help="Use the strategy's causal target for this slice." label="Use strategy target" onChange={(use_strategy_profit_target) => replaceSlice(slice.slice_id, { ...slice, use_strategy_profit_target })} value={slice.use_strategy_profit_target} />
        {!slice.use_strategy_profit_target ? <OptionalNumberField help="Optional absolute profit-target price." label="Profit target" minimum={0.0001} onChange={(profit_target_price) => replaceSlice(slice.slice_id, { ...slice, profit_target_price })} step={0.01} unit="price" value={slice.profit_target_price} /> : null}
        <SelectField help="Trailing behavior for the slice after its activation condition." label="Trailing rule" onChange={(rule_type) => replaceSlice(slice.slice_id, { ...slice, trailing: { ...slice.trailing, rule_type } })} options={["none", "broker_amount", "broker_percent", "volatility_trail", "swing_trail", "chandelier", "breakeven_then_trail", "profit_lock_r", "time_tightening"].map((value) => ({ label: readableLabel(value), value }))} value={slice.trailing.rule_type} />
        {slice.trailing.rule_type === "broker_amount" ? <OptionalNumberField help="Broker-held trailing amount; empty uses the strategy-computed amount." label="Trail amount" minimum={0.0001} onChange={(amount) => replaceSlice(slice.slice_id, { ...slice, trailing: { ...slice.trailing, amount } })} step={0.01} unit="price" value={slice.trailing.amount} /> : null}
        {slice.trailing.rule_type === "broker_percent" ? <OptionalNumberField help="Broker-held trailing percentage." label="Trail percent" minimum={0.01} onChange={(percent) => replaceSlice(slice.slice_id, { ...slice, trailing: { ...slice.trailing, percent } })} step={0.1} unit="%" value={slice.trailing.percent} /> : null}
        {["volatility_trail", "chandelier", "breakeven_then_trail"].includes(slice.trailing.rule_type) ? <OptionalNumberField help="Volatility multiple used by the dynamic trail." label="Trail volatility" minimum={0.01} onChange={(volatility_multiple) => replaceSlice(slice.slice_id, { ...slice, trailing: { ...slice.trailing, volatility_multiple } })} step={0.05} unit="×" value={slice.trailing.volatility_multiple} /> : null}
        {slice.trailing.rule_type !== "none" ? <><NumberField help="Minimum favorable gain before trailing activates." label="Activation gain" minimum={0} onChange={(activation_gain_percent) => replaceSlice(slice.slice_id, { ...slice, trailing: { ...slice.trailing, activation_gain_percent } })} step={0.1} unit="%" value={slice.trailing.activation_gain_percent} /><NumberField help="Profit retained beyond breakeven when applicable." label="Breakeven buffer" minimum={0} onChange={(breakeven_buffer_bps) => replaceSlice(slice.slice_id, { ...slice, trailing: { ...slice.trailing, breakeven_buffer_bps } })} step={0.5} unit="bps" value={slice.trailing.breakeven_buffer_bps} /></> : null}
      </div>
    </article>)}</div>
    <p className="configuration-safety-note"><ShieldCheck size={15} /> Slice fractions total {round(selected.slices.reduce((total, row) => total + row.quantity_fraction, 0) * 100)}%. Publication requires exactly 100%, causal swing availability, and account-policy permission for every selected stop and transition.</p>
  </ConfigGroup>;
}

function AccountsEditor({ draft, onChange }: { draft: Draft; onChange: (value: AccountSection) => void }) {
  const section = draft.accounts;
  function replace(index: number, next: AccountBinding) {
    onChange({ bindings: section.bindings.map((row, rowIndex) => rowIndex === index ? next : row) });
  }
  function addAccount() {
    const accountKey = uniqueId("account", section.bindings.map((row) => row.account_key));
    onChange({ bindings: [...section.bindings, {
      account_key: accountKey,
      name: "New account",
      source_account_id: "replay",
      account_class: "simulated",
      base_currency: "USD",
      session_key: "replay",
      portfolio_policy_id: String(draft.portfolio.policies[0]?.policy_id ?? "default"),
      enabled: true,
      modes: ["replay"],
    }] });
  }
  return (
    <div className="configuration-stack">
      <GuideCallout icon={<Boxes size={17} />} title="One stable key, mode-specific session binding">
        Published Strategies and Portfolio mandates reference the stable account key. Replay and Backtest bind simulated accounts; Paper and Live require the exact externally discovered IBKR account ID.
      </GuideCallout>
      <ConfigGroup action={<button className="button compact" onClick={addAccount} type="button"><Plus size={14} /> Add account</button>} summary="Account settings are reusable across published Strategies." title="Configured accounts">
        <div className="account-config-grid">
          {section.bindings.map((account, index) => (
            <article className="account-config-card" key={account.account_key}>
              <header><div><strong>{account.name}</strong><span>{account.account_key}</span></div><label className="configuration-switch"><input checked={account.enabled} onChange={(event) => replace(index, { ...account, enabled: event.target.checked })} type="checkbox" /><span /></label></header>
              <div className="configuration-field-grid one-column">
                <TextField help="Human-readable name shown throughout configuration and runtime evidence." label="Account name" onChange={(value) => replace(index, { ...account, name: value })} value={account.name} />
                <div className="configuration-fixed-value"><span>Stable account key</span><strong>{account.account_key}</strong><small>Mandates, groups, and runtime state refer to this identity.</small></div>
                {account.source_account_env ? <div className="configuration-fixed-value"><span>IBKR account ID source</span><strong>{account.source_account_env}</strong><small>Resolved only by the backend at runtime; the ID is never returned to this page or stored in a release.</small></div> : <TextField help="IBKR account ID or simulated runtime account identity." label="Source account" onChange={(value) => replace(index, { ...account, source_account_id: value })} value={account.source_account_id} />}
                <SelectField help="Determines broker capability and regulatory constraints." label="Account class" onChange={(value) => replace(index, { ...account, account_class: value })} options={["simulated", "paper", "cash", "margin", "registered"].map((value) => ({ label: readableLabel(value), value }))} value={account.account_class} />
                <SelectField help="Reusable account-level capital and risk policy." label="Portfolio policy" onChange={(value) => replace(index, { ...account, portfolio_policy_id: value })} options={draft.portfolio.policies.map((row) => ({ label: String(row.policy_id), value: String(row.policy_id) }))} value={account.portfolio_policy_id} />
                <TextField help="Gateway or simulated session identity used to locate runtime state." label="Session key" onChange={(value) => replace(index, { ...account, session_key: value })} value={account.session_key} />
                <TextField help="Currency used for Portfolio limits and account summaries." label="Base currency" onChange={(value) => replace(index, { ...account, base_currency: value.toUpperCase() })} value={account.base_currency} />
              </div>
              <ModeSelector modes={account.modes} onChange={(modes) => replace(index, { ...account, modes })} />
              {account.modes.some((mode) => mode === "paper" || mode === "live") ? <p className="configuration-safety-note"><ShieldCheck size={15} /> Publication and broker preflight require this exact account ID, a matching external IBKR discovery binding, and compiled runtime coverage for every selected live mode.</p> : null}
            </article>
          ))}
        </div>
      </ConfigGroup>
    </div>
  );
}

function RevisionBadge({ approved }: { approved: Revision | null }) {
  const Icon = approved ? BadgeCheck : LockKeyhole;
  return <div className="configuration-revision-badge" data-approved={approved ? "true" : "false"}><span className="configuration-revision-icon"><Icon aria-hidden="true" size={16} /></span><span className="configuration-revision-copy"><small>Runtime authority</small><strong>{approved ? `Release ${approved.revision}` : "Session only"}</strong><span>{approved ? approved.label : "Publish to retain"}</span></span></div>;
}

function RevisionPublisher({ approved, draft, guided = false, label, onLabelChange, onPublish, publishing, revisions }: {
  approved: Revision | null;
  draft: Draft | null;
  guided?: boolean;
  label: string;
  onLabelChange: (value: string) => void;
  onPublish: () => void;
  publishing: boolean;
  revisions: Revision[];
}) {
  const canvas = useMemo(canvasApprovalSnapshot, [approved, draft]);
  const checks = draft ? releaseReadiness(draft) : [];
  const configurationReady = checks.every((check) => check.ready);
  const visibleChecks = guided ? checks.filter((check) => ["Runtime compilation", "Mode coverage", "Paper and Live bindings"].includes(check.label)) : checks;
  return (
    <div className="configuration-revision-layout">
      <section className="configuration-publish-card">
        <header><div><span>{guided ? "Ready for use" : "Completion gate"}</span><strong>{guided ? "Publish this setup" : "Publish the application release"}</strong></div><Send size={18} /></header>
        <p>{guided ? "Publishing makes this complete setup available to new runs. Existing runs keep the release they started with." : "A release freezes every referenced Strategy, capability setting, compiled runtime contract, mandate, policy, OMS configuration, account binding, and Canvas. Active runs keep the release they started with."}</p>
        <div className="configuration-publish-proof">
          {visibleChecks.map((check) => <span data-ready={check.ready ? "true" : "false"} key={check.label}>{check.ready ? <CheckCircle2 size={14} /> : <TriangleAlert size={14} />} {guided ? publishCheckLabel(check.label) : check.label} · {check.detail}</span>)}
          <span data-ready={canvas.ready ? "true" : "false"}><CheckCircle2 size={14} /> {guided ? "Workspace layout" : "Canvas"} · {canvas.containerCount} containers</span>
        </div>
        <label><span>Release label <FieldHelp content="Use a short operational label that explains what this release is intended to validate." /></span><input onChange={(event) => onLabelChange(event.target.value)} placeholder="Replay strategy-studio acceptance" value={label} /></label>
        <button className="button primary" disabled={!draft || !configurationReady || !canvas.ready || !label.trim() || publishing} onClick={onPublish} type="button"><Send size={15} /> {publishing ? "Publishing…" : "Publish release"}</button>
      </section>
      <section className="configuration-history-card">
        <header><span>Immutable history</span><strong>{revisions.length} approved release{revisions.length === 1 ? "" : "s"}</strong></header>
        <div>{revisions.map((revision) => <article data-current={revision.revision_id === approved?.revision_id ? "true" : "false"} key={revision.revision_id}><span><strong>r{revision.revision} · {revision.label}</strong><small>{new Date(revision.approved_at).toLocaleString()}</small></span><code>{revision.content_hash.slice(0, 12)}</code></article>)}{!revisions.length ? <div className="configuration-empty-history">No release has been approved. Replay remains correctly blocked.</div> : null}</div>
      </section>
      {draft ? <JsonInspector label="Complete generated release JSON" value={draft} /> : null}
    </div>
  );
}

function releaseReadiness(draft: Draft) {
  const profileIds = new Set(draft.strategy.profiles.map((row) => row.profile_id));
  const omsIds = new Set(draft.oms.profiles.map((row) => row.profile_id));
  const accountKeys = new Set(draft.accounts.bindings.map((row) => row.account_key));
  const deployments = draft.assignments.deployments;
  const deploymentsReady = deployments.length > 0 && deployments.every((deployment) => (
    profileIds.has(deployment.profile_id)
    && omsIds.has(deployment.oms_profile_id)
    && draft.portfolio.mandates.some((mandate) => mandate.enabled && mandate.run_plan_id === deployment.run_plan_id)
  ));
  const mandatesReady = draft.portfolio.mandates.length > 0 && draft.portfolio.mandates.every((mandate) => (
    deployments.some((deployment) => deployment.run_plan_id === mandate.run_plan_id)
    && accountKeys.has(mandate.account_key)
  ));
  const configuredModes = new Set(draft.accounts.bindings.filter((account) => account.enabled).flatMap((account) => account.modes));
  const modeCoverageReady = draft.accounts.bindings.filter((account) => account.enabled).every((account) => account.modes.every((mode) => deployments.some((deployment) => (
    deployment.enabled
    && deployment.allowed_environments.includes(mode)
    && draft.portfolio.mandates.some((mandate) => mandate.enabled && mandate.account_key === account.account_key && mandate.run_plan_id === deployment.run_plan_id)
  ))));
  const liveBindingsReady = draft.accounts.bindings.every((account) => !account.enabled || !account.modes.some((mode) => mode === "paper" || mode === "live") || Boolean((account.source_account_env || account.source_account_id.trim()) && account.session_key.trim()));
  return [
    { detail: String(draft.strategy.profiles.length), label: "Strategy Profiles", ready: draft.strategy.profiles.length > 0 },
    { detail: deploymentsReady ? `${deployments.length} ready` : "needs mandate or strategy", label: "Runtime compilation", ready: deploymentsReady },
    { detail: String(draft.portfolio.mandates.length), label: "Account mandates", ready: mandatesReady },
    { detail: String(draft.oms.profiles.length), label: "OMS profiles", ready: draft.oms.profiles.length > 0 },
    { detail: String(draft.accounts.bindings.length), label: "Accounts", ready: draft.accounts.bindings.length > 0 },
    { detail: modeCoverageReady ? [...configuredModes].map(readableLabel).join(", ") : "runtime coverage missing", label: "Mode coverage", ready: modeCoverageReady },
    { detail: liveBindingsReady ? "exact bindings" : "broker id or session missing", label: "Paper and Live bindings", ready: liveBindingsReady },
    { detail: `${draft.oms.execution_policies.length} execution · ${draft.oms.protection_profiles.length} protection`, label: "Policy catalogs", ready: draft.oms.execution_policies.length > 0 && draft.oms.protection_profiles.length > 0 },
  ];
}

function publishCheckLabel(label: string) {
  if (label === "Runtime compilation") return "Trading setup";
  if (label === "Mode coverage") return "Selected modes";
  if (label === "Paper and Live bindings") return "Broker connection";
  return label;
}

function EffectiveConfigurationPreview({ draft }: { draft: Draft }) {
  const [mode, setMode] = useState<RuntimeMode>("replay");
  const [payload, setPayload] = useState<{ accounts: Array<Record<string, unknown>>; runtime_count: number; source: string } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setError("");
    api<{ accounts: Array<Record<string, unknown>>; runtime_count: number; source: string }>("/api/trading/configuration/effective/session", { body: JSON.stringify({ configuration: serializeDraft(draft), mode }), method: "POST" })
      .then((value) => { if (!cancelled) setPayload(value); })
      .catch((reason) => { if (!cancelled) { setPayload(null); setError(reason instanceof Error ? reason.message : String(reason)); } });
    return () => { cancelled = true; };
  }, [draft, mode]);
  return <ConfigGroup summary="Backend-resolved session evidence. This is the exact account, policy, compiled contract, and mode projection that publication will validate." title="Effective configuration preview">
    <div className="configuration-toolbar"><SelectField help="Resolve this session's configuration for one runtime mode." label="Runtime mode" onChange={(value) => setMode(value as RuntimeMode)} options={["replay", "backtest", "backtest_debug", "paper", "live"].map((value) => ({ label: readableLabel(value), value }))} value={mode} /></div>
    {error ? <p className="configuration-safety-note"><TriangleAlert size={15} /> {error}</p> : null}
    {payload ? <><p className="configuration-section-guide">{payload.runtime_count} eligible compiled runtime{payload.runtime_count === 1 ? "" : "s"} · {payload.accounts.length} bound account{payload.accounts.length === 1 ? "" : "s"} · {readableLabel(payload.source)}</p><div className="mandate-grid">{payload.accounts.map((account) => <article className="mandate-card" key={String(account.account_key)}><header><div><strong>{String(account.name || account.account_key)}</strong><span>{String(account.account_key)} · {String(account.account_class)}</span></div></header><div className="configuration-fixed-value"><span>Broker/session binding</span><strong>{String(account.source_account_env || account.source_account_id || "Simulated")}</strong><small>{String(account.session_key)} · {String(account.policy_identity)}</small></div><div className="configuration-fixed-value"><span>Eligible compiled runtimes</span><strong>{Array.isArray(account.run_plan_ids) ? account.run_plan_ids.length : 0}</strong><small>{Array.isArray(account.run_plan_ids) ? account.run_plan_ids.join(", ") || "None for this mode" : "None for this mode"}</small></div></article>)}</div></> : null}
  </ConfigGroup>;
}

function ConfigGroup({ action, children, summary, title }: { action?: ReactNode; children: ReactNode; summary: string; title: string }) {
  const visual = configGroupVisual(title);
  const Icon = visual.icon;
  return <section className="configuration-group" data-group-tone={visual.tone}><header><div className="configuration-group-heading"><span className="configuration-group-icon"><Icon size={15} /></span><div><strong>{title}</strong><p>{summary}</p></div></div>{action}</header><div className="configuration-group-body"><ConfigurationNarrative heading={title} paragraphs={configurationGroupStory(title)} />{children}</div></section>;
}

function ConfigurationNarrative({ heading, paragraphs }: { heading: string; paragraphs: string[] }) {
  if (!paragraphs.length) return null;
  return <section className="configuration-narrative"><span>{heading}</span>{paragraphs.map((paragraph, index) => <p key={`${heading}-${index}`}>{paragraph}</p>)}</section>;
}

function configurationGroupStory(title: string) {
  const normalized = title.toLowerCase();
  if (normalized.includes("watch universe")) return [
    "Universe source and symbols determine which tickers the Run Plan may observe. Portfolio book sets the scope for campaign ownership. Eligibility does not authorize entry; evidence and all downstream authorities must still pass.",
  ];
  if (normalized.includes("strategy and execution")) return [
    "Strategy Profile selects decision behavior. OMS profile selects execution and protection behavior. A run pins both independent revisions through the Run Plan.",
  ];
  if (normalized.includes("action authority")) return [
    "Default sets operator involvement for inherited actions. Manual records intent, Confirm waits for approval, and Automatic proceeds only after downstream checks pass. Per-action values override the default. Protective and emergency exits remain automatic.",
  ];
  if (normalized.includes("environments") && normalized.includes("safety")) return [
    "Allowed environments control where this Run Plan may launch. Safety may be configured for historical modes and is mandatory for Paper and Live.",
  ];
  if (normalized.includes("account mandate")) return [
    "A mandate links one Run Plan to one account. Its parameters limit cash, planned risk, position count, allocation mode, replacement, and maximum action authority. Portfolio still evaluates current state and competing requests for every intent.",
  ];
  if (normalized.includes("account safety")) return [
    "Exposure parameters bound normal allocation. Warning thresholds pause new risk. Hard loss and drawdown thresholds latch the account. These controls apply across all runs using the account and cannot be weakened by a Strategy Profile.",
  ];
  if (normalized.includes("risk group")) return [
    "A risk group applies shared gross and ticker exposure limits across its selected account keys. Use it when multiple accounts share economic or correlated risk.",
  ];
  if (normalized.includes("execution policy catalog")) return [
    "Quote source selects execution-time price authority. Price bounds limit acceptable execution. Deadline and repricing parameters control how long and how aggressively OMS works the approved quantity. Partial-fill policy controls the remainder.",
  ];
  if (normalized.includes("protection profile catalog")) return [
    "Slice fractions allocate the complete fill. Each slice requires a hard stop and may define a target and trail. Add policy controls protection after position increases; repair deadline and catastrophic-backstop settings control OMS recovery when broker protection is missing.",
  ];
  if (normalized === "protection") return [
    "Stop method selects structural, volatility, or combined invalidation. Buffers and multiples set distance. Maximum risk caps the resolved protection. Trailing enabled permits protection to tighten after favorable movement.",
  ];
  if (normalized.includes("execution behavior")) return [
    "Entry and exit defaults apply when the Strategy Intent has no phase-specific override. Urgency and limit offset affect execution speed and price tolerance. Smart routing still resolves broker instructions from session and account capabilities.",
  ];
  if (normalized.includes("configured account")) return [
    "Stable account key is the published identity used by mandates and runtime state. Source account and session locate execution state. Account class and modes constrain capabilities. Paper and Live broker identifiers are resolved only during backend preflight.",
  ];
  if (normalized.includes("readiness")) return [
    "Readiness confirms required configuration references exist. It does not prove future broker connectivity, market-data health, or Live order acceptance.",
  ];
  if (normalized.includes("effective configuration")) return [
    "Runtime mode selects the backend projection to inspect. The result shows resolved accounts, policies, and eligible Run Plans from this browser session; it is read-only derived evidence.",
  ];
  return [];
}

function configGroupVisual(title: string) {
  const normalized = title.toLowerCase();
  if (normalized.includes("watch universe")) return { icon: Target, tone: "strategy" } as const;
  if (normalized.includes("account safety")) return { icon: ShieldCheck, tone: "portfolio" } as const;
  if (normalized.includes("protection")) return { icon: ShieldCheck, tone: "protection" } as const;
  if (normalized.includes("account mandate") || normalized.includes("configured account")) return { icon: WalletCards, tone: "portfolio" } as const;
  if (normalized.includes("risk group")) return { icon: Network, tone: "portfolio" } as const;
  if (normalized.includes("execution") || normalized.includes("runtime mode")) return { icon: Send, tone: "oms" } as const;
  if (normalized.includes("readiness") || normalized.includes("effective configuration")) return { icon: BadgeCheck, tone: "ready" } as const;
  if (normalized.includes("campaign lifecycle")) return { icon: GitBranch, tone: "strategy" } as const;
  return { icon: Sparkles, tone: "section" } as const;
}

function GuideCallout({ children, icon, title }: { children: ReactNode; icon: ReactNode; title: string }) {
  void children;
  void icon;
  void title;
  return null;
}

type HelpContent = string | {
  note?: string;
  parameters?: Record<string, string>;
  role: string;
  values?: Record<string, string>;
};

function FieldHelp({ content, title = "Parameter guide" }: { content: HelpContent; title?: string }) {
  const anchor = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const dialogId = useId();
  const titleId = `${dialogId}-title`;
  const [open, setOpen] = useState(false);
  const detail = typeof content === "string" ? { role: content } : content;
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
      anchor.current?.focus();
    };
  }, [open]);
  return (
    <span className="configuration-help">
      <button
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={`Explain ${title}`}
        onClick={(event) => { event.preventDefault(); event.stopPropagation(); setOpen(true); }}
        ref={anchor}
        type="button"
      ><CircleHelp size={15} /></button>
      {open ? createPortal(
        <div className="configuration-help-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
          <section aria-labelledby={titleId} aria-modal="true" className="configuration-help-dialog" id={dialogId} role="dialog">
            <header>
              <div className="configuration-help-dialog-title"><span><CircleHelp size={18} /></span><div><small>Configuration guide</small><h2 id={titleId}>{title}</h2></div></div>
              <button aria-label="Close guide" onClick={() => setOpen(false)} ref={closeButton} type="button"><X size={18} /></button>
            </header>
            <div className="configuration-help-dialog-body">
              <section><strong>What this controls</strong><p>{detail.role}</p></section>
              {detail.parameters ? (
                <section><strong>Parameters</strong><dl>{Object.entries(detail.parameters).map(([label, explanation]) => <div key={label}><dt>{label}</dt><dd>{explanation}</dd></div>)}</dl></section>
              ) : null}
              {detail.values ? (
                <section><strong>Available values</strong><dl>{Object.entries(detail.values).map(([label, explanation]) => <div key={label}><dt>{label}</dt><dd>{explanation}</dd></div>)}</dl></section>
              ) : null}
              {detail.note ? <footer><strong>Important</strong><p>{detail.note}</p></footer> : null}
            </div>
          </section>
        </div>,
        document.body,
      ) : null}
    </span>
  );
}

type FieldDefinition = {
  choices?: readonly string[];
  help: HelpContent;
  kind: "boolean" | "choice" | "number" | "text";
  label: string;
  path: string;
  step?: number;
  unit?: string;
};

function ParameterField({ definition, onChange, value }: { definition: FieldDefinition; onChange: (value: Primitive) => void; value: Primitive }) {
  if (definition.kind === "boolean") return <BooleanField help={definition.help} label={definition.label} onChange={onChange} value={Boolean(value)} />;
  if (definition.kind === "choice") return <SelectField help={definition.help} label={definition.label} onChange={onChange} options={(definition.choices ?? []).map((item) => ({ label: readableLabel(item), value: item }))} value={String(value)} />;
  if (definition.kind === "number") return <NumberField help={definition.help} label={definition.label} onChange={onChange} step={definition.step ?? 0.01} unit={definition.unit} value={Number(value)} />;
  return null;
}

function CapabilityField({ definition, onChange, value }: { definition: CapabilityParameter; onChange: (value: Primitive) => void; value: Primitive }) {
  if (definition.type === "boolean") return <BooleanField help={definition.help} label={definition.label} onChange={onChange} value={Boolean(value)} />;
  if (definition.type === "choice") return <SelectField help={definition.help} label={definition.label} onChange={onChange} options={(definition.options ?? []).map((item) => ({ label: readableLabel(item), value: item }))} value={String(value)} />;
  return <NumberField help={definition.help} label={definition.label} maximum={definition.maximum} minimum={definition.minimum} onChange={onChange} step={definition.step ?? 0.01} unit={definition.display === "fraction" ? "fraction" : definition.unit} value={Number(value)} />;
}

function TextField({ help, label, nextAction = false, onChange, value }: { help: HelpContent; label: string; nextAction?: boolean; onChange: (value: string) => void; value: string }) {
  return <label className="configuration-field" data-editable="true"><span>{label}</span><input data-next-action-control={nextAction ? "true" : undefined} onChange={(event) => onChange(event.target.value)} value={value} /><small>{fieldSummary(help)}</small></label>;
}

function NumberField({ help, label, maximum, minimum, onChange, step, unit, value }: { help: HelpContent; label: string; maximum?: number; minimum?: number; onChange: (value: number) => void; step: number; unit?: string; value: number }) {
  const fraction = unit === "fraction";
  return <label className="configuration-field" data-editable="true"><span>{label}</span><div className="configuration-number"><input max={fraction ? 100 : maximum} min={fraction ? 0 : minimum} onChange={(event) => onChange(fraction ? Number(event.target.value) / 100 : Number(event.target.value))} step={fraction ? step * 100 : step} type="number" value={fraction ? round(value * 100) : value} />{unit ? <em>{fraction ? "%" : unit}</em> : null}</div><small>{fieldSummary(help)}</small></label>;
}

function OptionalNumberField({ help, label, minimum, onChange, step, unit, value }: { help: HelpContent; label: string; minimum?: number; onChange: (value: number | null) => void; step: number; unit?: string; value: number | null }) {
  return <label className="configuration-field" data-editable="true"><span>{label}</span><div className="configuration-number"><input min={minimum} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} placeholder="Automatic" step={step} type="number" value={value ?? ""} />{unit ? <em>{unit}</em> : null}</div><small>{fieldSummary(help)}</small></label>;
}

function SelectField({ disabled = false, help, label, onChange, options, searchable, value }: { disabled?: boolean; help: HelpContent; label: string; onChange: (value: string) => void; options: Array<{ description?: string; label: string; value: string }>; searchable?: boolean; value: string }) {
  const documentedOptions = options.map((option) => ({ ...option, description: option.description ?? choiceExplanation(option.label, option.value, help) }));
  return <div className="configuration-field configuration-lookup-field" data-editable={disabled ? "false" : "true"}><span>{label}</span>{disabled ? <strong>{options.find((option) => option.value === value)?.label ?? value}</strong> : <InventoryFilterSelect ariaLabel={label} className="configuration-lookup-button" onChange={onChange} options={documentedOptions} searchable={searchable ?? options.length > 7} searchPlaceholder={`Find ${label.toLowerCase()}…`} value={value} />}<small>{fieldSummary(help)}</small></div>;
}

function BooleanField({ disabled = false, help, label, onChange, value }: { disabled?: boolean; help: HelpContent; label: string; onChange: (value: boolean) => void; value: boolean }) {
  return <label className="configuration-field configuration-boolean" data-editable={disabled ? "false" : "true"}><span>{label}</span><small>{fieldSummary(help)}</small><input checked={value} disabled={disabled} onChange={(event) => onChange(event.target.checked)} type="checkbox" /></label>;
}

function fieldSummary(help: HelpContent) {
  return typeof help === "string" ? help : help.role;
}

const STRATEGY_CHOICE_EXPLANATIONS: Record<string, string> = {
  accept_partial: "Keep the confirmed filled quantity and stop requesting the remainder.",
  acceleration_slowdown: "Act when favorable price acceleration weakens, preserving gains before momentum fully reverses.",
  all_available: "Ask Portfolio for every unit of capacity still available under the account mandate and current risk state.",
  automatic: "Allow the configured authority to act without waiting for a manual confirmation step.",
  close: "Request release of the entire broker-reconciled position.",
  complete_remainder: "Continue working only the approved quantity that remains unfilled after reconciliation.",
  confirm: "Require an explicit confirmation before the action may proceed.",
  fixed_quantity: "Request a fixed number of shares; Portfolio may approve fewer or reject the request.",
  favorable_move_pct: "Act after the position reaches the configured favorable percentage move.",
  hybrid: "Use the stricter valid boundary produced by structural and volatility evidence.",
  long: "Open by buying and reduce or close by selling.",
  mandate_fraction: "Request a fraction of the cash capacity assigned to this Run Plan's account mandate.",
  manual: "Prepare the action for a human operator without submitting it automatically.",
  patient: "Favor passive pricing and slower repricing when the selected execution policy permits it.",
  reduce: "Request only the configured fraction of the broker-reconciled position.",
  regular: "Use the normal balance between fill probability and price discipline.",
  risk_fraction: "Request exposure as a fraction of the risk budget; Portfolio converts it to account-specific quantity.",
  short: "Open by short-selling and reduce or close by buying to cover; broker shortability remains mandatory.",
  structure: "Anchor invalidation to the confirmed market structure that justified the position.",
  urgent: "Prioritize a prompt fill while remaining inside the selected OMS policy and approved quantity.",
  very_urgent: "Use the policy's fastest allowed repricing and terminal behavior for time-critical execution.",
  volatility: "Place the boundary at the configured volatility multiple so distance adapts to current movement.",
  volatility_multiple: "Act when the configured move or distance reaches the selected volatility multiple.",
  cancel_remainder: "Cancel the broker-confirmed unfilled remainder and keep only completed fills.",
};

function choiceExplanation(label: string, value: string, help: HelpContent) {
  if (typeof help !== "string") {
    const documented = help.values?.[label] ?? help.values?.[readableLabel(value)] ?? help.values?.[value];
    if (documented) return documented;
  }
  return STRATEGY_CHOICE_EXPLANATIONS[value] ?? `Select ${label} for this setting. ${fieldSummary(help)}`;
}

function ModeSelector({ modes, onChange }: { modes: RuntimeMode[]; onChange: (value: RuntimeMode[]) => void }) {
  const options: Array<{ label: string; value: RuntimeMode }> = [
    { label: "Replay", value: "replay" }, { label: "Backtest", value: "backtest" },
    { label: "Backtest Debug", value: "backtest_debug" }, { label: "Paper", value: "paper" }, { label: "Live", value: "live" },
  ];
  return <div className="configuration-mode-selector">{options.map((option) => <label key={option.value}><input checked={modes.includes(option.value)} onChange={(event) => onChange(event.target.checked ? [...modes, option.value] : modes.filter((item) => item !== option.value))} type="checkbox" /><span>{option.label}</span></label>)}</div>;
}

function JsonInspector({ label, value }: { label: string; value: unknown }) {
  const content = JSON.stringify(value, null, 2);
  const [copied, setCopied] = useState(false);
  async function copy() {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_500);
  }
  return <details className="configuration-json-inspector"><summary><span><strong>Advanced · Generated JSON</strong><small>Inspect the canonical payload without using JSON as the primary editor</small></span><ChevronRight size={15} /></summary><header><span>{label}</span><button onClick={() => void copy()} type="button"><Clipboard size={13} /> {copied ? "Copied" : "Copy"}</button></header><pre>{content}</pre></details>;
}

function EmptyState({ detail, title }: { detail: string; title: string }) {
  return <div className="configuration-empty"><strong>{title}</strong><span>{detail}</span></div>;
}

function ConfigurationLoading() {
  return <div className="configuration-empty"><strong>Loading configuration</strong><span>Reading the approved base for this browser session…</span></div>;
}

function updateCapability(profile: StrategyProfile, id: string, binding: CapabilityBinding): StrategyProfile {
  return { ...profile, capabilities: profile.capabilities.map((row) => row.capability_id === id ? binding : row) };
}

function inputSource(catalog: StrategyInput[], sourceId: string) {
  return catalog.find((row) => row.source_id === sourceId);
}

function sourceOptions(catalog: StrategyInput[], valueType?: string) {
  const categories = [...new Set(catalog.map((row) => row.category))];
  return categories.map((category) => (
    <optgroup key={category} label={category}>
      {catalog.filter((row) => row.category === category && (!valueType || row.value_type === valueType)).map((source) => (
        <option key={source.source_id} value={source.source_id}>{source.label} · {source.parameter}</option>
      ))}
    </optgroup>
  ));
}

function field(path: string, label: string, help: HelpContent, kind: FieldDefinition["kind"], choices?: readonly string[], unit?: string, step?: number): FieldDefinition {
  return { path, label, help, kind: choices?.length ? "choice" : kind, choices, unit, step };
}

function flattenPrimitives(value: ParameterMap, prefix = ""): Array<{ path: string; value: Primitive }> {
  return Object.entries(value).flatMap(([key, item]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === "object" && !Array.isArray(item)) return flattenPrimitives(item as ParameterMap, path);
    if (["boolean", "number", "string"].includes(typeof item)) return [{ path, value: item as Primitive }];
    return [];
  });
}

const STRATEGY_CATALOG_GROUPS = [
  "Profile",
  "Observe",
  "Initial entry · Opportunity",
  "Initial entry · Confirmation",
  "Initial entry · Blockers",
  "Initial entry · Request",
  "Position adds",
  "Reentry",
  "Strategic exits",
  "Capabilities",
  "Engine parameters",
] as const;

const STRATEGY_CATALOG_OMITTED_KEYS = new Set([
  "profile_id", "definition_id", "definition_revision", "origin", "protected", "editable", "revision",
  "condition_id", "group_id", "step_id", "rule_set_id", "capability_id",
]);

const STRATEGY_CATALOG_GUIDED_OMITTED_LEAVES = new Set([
  "deadline_ms",
  "description",
  "name",
  "summary",
]);

function flattenStrategyPrimitives(value: unknown, prefix = "", result: Array<{ path: string; value: CatalogParameterValue }> = []): Array<{ path: string; value: CatalogParameterValue }> {
  if (value === null && prefix) {
    result.push({ path: prefix, value: null });
    return result;
  }
  if (["boolean", "number", "string"].includes(typeof value)) {
    result.push({ path: prefix, value: value as Primitive });
    return result;
  }
  if (!value || typeof value !== "object") return result;
  Object.entries(value).forEach(([key, item]) => {
    if (STRATEGY_CATALOG_OMITTED_KEYS.has(key)) return;
    flattenStrategyPrimitives(item, prefix ? `${prefix}.${key}` : key, result);
  });
  return result;
}

function strategyCatalogGroupForPath(path: string): { group: string; groupOrder: number } {
  let groupOrder = 0;
  if (path === "lifecycle.phase_modes.initial_entry") return { group: "Initial entry · Mode", groupOrder: 2 };
  if (path === "lifecycle.phase_modes.manage") return { group: "Position management", groupOrder: 6 };
  if (path === "lifecycle.phase_modes.reentry") return { group: "Reentry", groupOrder: 7 };
  if (path === "lifecycle.phase_modes.exit") return { group: "Strategic exits", groupOrder: 8 };
  if (path.startsWith("lifecycle.trading_behavior")) groupOrder = 1;
  else if (path.startsWith("lifecycle.initial_entry.opportunity")) groupOrder = 2;
  else if (path.startsWith("lifecycle.initial_entry.confirmation")) groupOrder = 3;
  else if (path.startsWith("lifecycle.initial_entry.blockers")) groupOrder = 4;
  else if (path.startsWith("lifecycle.initial_entry.capital_request") || path.startsWith("lifecycle.initial_entry.order_intent")) groupOrder = 5;
  else if (path.startsWith("lifecycle.initial_entry.add_steps")) groupOrder = 6;
  else if (path.startsWith("lifecycle.reentry")) groupOrder = 7;
  else if (path.startsWith("lifecycle.exit")) groupOrder = 8;
  else if (path.startsWith("capabilities")) groupOrder = 9;
  else if (path.startsWith("parameters.protection.stop.")) return { group: "Initial stop", groupOrder: 5 };
  else if (path.startsWith("parameters.protection.trailing.")) return { group: "Position management", groupOrder: 7 };
  else if (path.startsWith("parameters.protection.luld_profit_target.") || path.startsWith("parameters.profit_pocket.")) return { group: "Strategic exits", groupOrder: 8 };
  else if (path.startsWith("parameters")) return { group: "Strategy tuning", groupOrder: 10 };
  return { group: STRATEGY_CATALOG_GROUPS[groupOrder], groupOrder };
}

function strategyEditableParameters(profile: StrategyProfile) {
  const editableProfile = {
    lifecycle: profile.lifecycle,
    capabilities: profile.capabilities,
    parameters: profile.parameters,
  };
  return flattenStrategyPrimitives(editableProfile).filter(({ path }) => {
    if (path === "lifecycle.reentry.enabled") return false;
    const leaf = path.split(".").at(-1) ?? path;
    if (STRATEGY_CATALOG_GUIDED_OMITTED_LEAVES.has(leaf)) return false;
    if (path.includes(".expression.")) return false;
    if (leaf === "operator" && !path.startsWith("parameters.")) return false;
    return true;
  }).filter(({ path, value }) => {
    if (typeof value !== "string") return true;
    return Boolean(choicesFor(path));
  }).map((parameter, importance) => ({
    ...parameter,
    ...strategyCatalogGroupForPath(parameter.path),
    importance,
  }));
}

function strategyParameterLabel(path: string) {
  const parts = path.split(".");
  const leaf = readableLabel(parts.at(-1) ?? path);
  if (path.startsWith("lifecycle.phase_modes.")) return `${leaf} mode`;
  const indexedParent = parts.findIndex((part) => /^\d+$/.test(part));
  if (indexedParent < 1) return leaf;
  const index = Number(parts[indexedParent]) + 1;
  const parent = parts[indexedParent - 1];
  if (parent === "conditions") return `Condition ${index} · ${leaf}`;
  if (parent === "groups") return `Evidence group ${index} · ${leaf}`;
  if (parent === "add_steps") return `Add ${index} · ${leaf}`;
  if (parent === "rule_sets") return `Exit route ${index} · ${leaf}`;
  if (parent === "capabilities") return `Capability ${index} · ${leaf}`;
  if (parent === "eligible_sessions") return `Eligible session ${index}`;
  return `${readableLabel(parent)} ${index} · ${leaf}`;
}

function setStrategyProfilePath(profile: StrategyProfile, path: string, value: Primitive): StrategyProfile {
  const result = deepClone(profile) as unknown as ParameterMap;
  const parts = path.split(".");
  let cursor: unknown = result;
  parts.slice(0, -1).forEach((part, index) => {
    const nextPart = parts[index + 1];
    if (Array.isArray(cursor)) {
      cursor = cursor[Number(part)];
      return;
    }
    const record = cursor as ParameterMap;
    if (!record[part] || typeof record[part] !== "object") record[part] = /^\d+$/.test(nextPart) ? [] : {};
    cursor = record[part];
  });
  const finalPart = parts.at(-1) ?? path;
  if (Array.isArray(cursor)) cursor[Number(finalPart)] = value;
  else (cursor as ParameterMap)[finalPart] = value;
  return result as unknown as StrategyProfile;
}

function getPath(source: ParameterMap, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => current && typeof current === "object" ? (current as ParameterMap)[key] : undefined, source);
}

function setPath(source: ParameterMap, path: string, value: Primitive): ParameterMap {
  const result = deepClone(source);
  const parts = path.split(".");
  let cursor = result;
  parts.slice(0, -1).forEach((part) => {
    cursor[part] = cursor[part] && typeof cursor[part] === "object" ? cursor[part] : {};
    cursor = cursor[part] as ParameterMap;
  });
  cursor[parts.at(-1) ?? path] = value;
  return result;
}

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function controlFor(value: Primitive): FieldDefinition["kind"] {
  return typeof value === "boolean" ? "boolean" : typeof value === "number" ? "number" : "text";
}

function choicesFor(path: string): readonly string[] | undefined {
  if (path.startsWith("lifecycle.phase_modes.")) return ["automatic", "manual"];
  if (path.endsWith(".method")) return ["structure", "volatility", "hybrid"];
  if (path.endsWith(".trigger")) return ["acceleration_slowdown", "favorable_move_pct", "volatility_multiple"];
  if (path.endsWith(".side")) return ["long", "short"];
  if (path.endsWith(".capital_request.mode")) return ["fixed_quantity", "mandate_fraction", "risk_fraction", "all_available"];
  if (path.endsWith(".partial_fill_policy")) return ["complete_remainder", "accept_partial", "cancel_remainder"];
  if (/\.exit\.rule_sets\.\d+\.action$/.test(path)) return ["close", "reduce"];
  if (path.endsWith(".entry_urgency")) return ["patient", "regular", "urgent", "very_urgent"];
  if (path.endsWith(".exit_urgency")) return ["urgent", "very_urgent"];
  return undefined;
}

function isDirectlyEditableStrategyParameter(path: string, value: Primitive) {
  return typeof value !== "string" || Boolean(choicesFor(path));
}

function unitFor(path: string) {
  if (path.endsWith("_bps")) return "bps";
  if (path.endsWith("_pct")) return "%";
  if (path.endsWith("_ms")) return "ms";
  if (path.includes("quantity")) return "shares";
  if (path.endsWith("_fraction")) return "fraction";
  return undefined;
}

function stepFor(value: Primitive) { return typeof value === "number" && Number.isInteger(value) ? 1 : 0.01; }

type StrategyParameterDocumentation = {
  caution: string[];
  cautionTone: "information" | "warning" | "safety";
  impact: string[];
  role: string[];
  timing: string[];
};

function strategyParameterDocumentation(path: string, group: string, value: CatalogParameterValue | undefined): StrategyParameterDocumentation {
  const leaf = path.split(".").at(-1) ?? path;
  const current = value === null || value === undefined || value === "" ? "unset" : typeof value === "boolean" ? (value ? "enabled" : "disabled") : `set to ${String(value)}`;
  const base: StrategyParameterDocumentation = {
    role: [`This parameter belongs to ${group} and is stored in the Strategy Profile. Its current value is ${current}.`],
    timing: ["The strategy reads this value only when execution reaches the lifecycle stage shown by this catalog group. Editing this session does not affect a running campaign until the configuration is validated and published in a release."],
    impact: [`Changing ${readableLabel(leaf)} changes the configured decision at this stage. The Strategy Engine emits intent from the resulting behavior; Portfolio, safety, and OMS authorities still decide whether that intent may proceed and how it is executed.`],
    caution: ["Review the resulting behavior in Replay or Backtest before publication. Live runs remain subject to mandatory account guardrails, but guardrails do not make an incorrect strategy rule correct."],
    cautionTone: "information",
  };

  if (path === "name") return { ...base, role: ["The name is the human-readable identity of this Strategy Profile. It appears in selection lists, Run Plans, releases, and runtime evidence."], timing: ["The name is descriptive metadata and is not consulted by trading logic."], impact: ["Renaming improves operator recognition without changing entries, position sizing, exits, or order behavior."], caution: ["Use a unique, stable name that describes the strategy rather than a temporary test label. Renaming does not create a new strategy revision by itself."], cautionTone: "information" };
  if (path === "description") return { ...base, role: ["The description records the strategy's intended setup, market context, and operating purpose for reviewers and operators."], timing: ["It is shown during configuration and review but is not evaluated by the Strategy Engine."], impact: ["A precise description makes later changes auditable and helps users distinguish expected behavior from a defect. It has no direct trading effect."], caution: ["Describe intent and boundaries, not implementation guesses. Keep behavior-changing facts in typed parameters where the runtime can enforce them."], cautionTone: "information" };
  if (path === "enabled") return { ...base, role: ["This switch controls whether a Run Plan may select the Strategy Profile for a new run."], timing: ["It is checked before a new Strategy Run is admitted. It does not start, stop, or mutate an already running campaign."], impact: [value ? "The profile is currently available to eligible Run Plans." : "The profile is currently unavailable for new Strategy Runs."], caution: ["Disabling availability is not an emergency stop. Use the shared safety authority to halt active trading or block new account risk."], cautionTone: "warning" };
  if (path.startsWith("lifecycle.phase_modes.")) {
    const phase = readableLabel(path.split(".").at(-1) ?? "phase");
    return { ...base, role: [`This mode decides whether Strategy owns the ${phase.toLowerCase()} decision.`], timing: ["The mode is checked before Strategy evaluates any rules or emits intent for this lifecycle phase."], impact: [value === "manual" ? `The ${phase.toLowerCase()} configuration is preserved, but Strategy skips its evaluation and emits no intent for it.` : `Strategy evaluates the configured ${phase.toLowerCase()} pages and may emit intent when their rules pass.`], caution: [path.endsWith(".exit") ? "Manual strategic exit never disables broker-held protection, emergency exits, or account safety." : "Run Plan authority remains separate and still decides whether emitted intent executes automatically or requires confirmation."], cautionTone: path.endsWith(".exit") ? "safety" : "information" };
  }

  if (path.includes("eligible_sessions")) return { ...base, role: ["This session is one of the time windows in which the strategy may create exposure-increasing actions."], timing: ["The session gate is checked before initial entry, position adds, and reentry. Existing-position protection and emergency action remain active outside eligible sessions."], impact: ["Adding a session expands when the strategy may take risk; removing it narrows opportunity and can prevent otherwise valid evidence from producing an entry request."], caution: ["Session eligibility is not a protection schedule. Broker-held stops and account safety supervision must remain active regardless of this value."], cautionTone: "safety" };
  if (path.endsWith(".side")) return { ...base, role: ["Side reserves the campaign's trade direction and determines whether entry intent seeks long or short exposure."], timing: ["It is applied when the campaign is created and is carried into entry, add, reentry, and exit interpretation."], impact: ["Changing side reverses the economic meaning of price movement, evidence comparisons, and closing orders. It is a fundamental strategy change, not a display preference."], caution: ["Review every directional evidence source and exit rule after changing side. A rule written for long behavior may be logically wrong for short behavior even if the schema accepts it."], cautionTone: "warning" };

  if (path.includes(".groups.") && leaf === "operator") return { ...base, role: ["The group operator determines how the enabled conditions inside one evidence path are combined."], timing: ["It is evaluated after each enabled condition has produced a pass, fail, or score result for the current event."], impact: ["All requires every enabled condition; Any requires at least one; Score compares accumulated evidence with Required Score. The choice directly changes how selective the strategy is."], caution: ["A permissive operator can increase trade frequency substantially. Disabled conditions do not contribute, so review the effective enabled set together with this operator."], cautionTone: "warning" };
  if (leaf === "comparator") return { ...base, role: ["The comparator defines the relationship that must hold between the left evidence source and either the right source or configured literal value."], timing: ["It runs whenever its parent evidence stage is evaluated and the condition is enabled."], impact: ["Changing the comparator can invert or materially widen the condition. The same inputs can move from passing to failing without either source changing."], caution: ["Confirm units, direction, and timeframe on both operands. Comparing a percentage to a price, or a current value to a differently timed value, can produce valid-looking but meaningless evidence."], cautionTone: "warning" };
  if (leaf === "left_source_id" || leaf === "right_source_id") return { ...base, role: [`This selects the ${leaf.startsWith("left") ? "primary" : "comparison"} evidence source used by the condition.`], timing: ["The source is resolved at evaluation time using causal data available to the strategy at that moment."], impact: ["Changing the source changes the information the condition measures, even when the comparator and threshold remain unchanged."], caution: ["Use only sources with compatible value types and causal availability. Missing, stale, or unavailable evidence must fail according to the registered strategy contract rather than being silently substituted."], cautionTone: "safety" };
  if (leaf === "left_timeframe" || leaf === "right_timeframe") return { ...base, role: ["The timeframe selects the aggregation horizon for this side of the evidence comparison."], timing: ["It is applied when the selected source is resolved for each evaluation event."], impact: ["Shorter horizons react faster and usually contain more noise; longer horizons respond more slowly and represent broader structure."], caution: ["Timeframes must be supported by the selected source. Mixing horizons is valid only when the rule intentionally compares different temporal contexts."], cautionTone: "warning" };
  if (leaf === "value") return { ...base, role: ["This literal supplies the condition's comparison threshold when the rule does not use a second evidence source."], timing: ["It is read when the enabled condition evaluates against its left source."], impact: ["Moving the threshold changes the boundary between pass and fail and therefore changes entry, add, reentry, or exit frequency according to the parent stage."], caution: ["An unset value is valid only when the comparator uses another source. Match the threshold's unit and scale to the selected evidence source."], cautionTone: "warning" };
  if (leaf === "required_score") return { ...base, role: ["Required Score is the minimum accumulated evidence score needed for a score-based group to pass."], timing: ["It is consulted only when the parent group operator is Score."], impact: ["A higher score makes the path more selective; a lower score permits weaker combinations of evidence."], caution: ["This value has no effect under All or Any. Review individual condition weights and the maximum attainable score before changing it."], cautionTone: "information" };

  if (path.includes("capital_request.mode")) return { ...base, role: ["Capital request mode describes how the strategy expresses desired exposure to Portfolio Management."], timing: ["It is used only after the associated evidence passes. Portfolio converts the request into an approved, reduced, or rejected account quantity."], impact: ["Fixed quantity requests shares; mandate or risk fractions scale against portfolio authority; all available asks for the maximum permitted allocation."], caution: ["This is a request, not sizing authority. Account limits, reservations, competing runs, and safety guardrails remain authoritative."], cautionTone: "safety" };
  if (path.includes("capital_request.value")) return { ...base, role: ["This is the magnitude interpreted by the selected capital request mode."], timing: ["It is sent with entry, add, or reentry intent after strategy evidence passes."], impact: ["Increasing it asks Portfolio for more exposure; decreasing it asks for less. Final approved quantity can still be smaller or zero."], caution: ["Interpret the number together with request mode. The same numeric value can mean shares, a mandate fraction, or a risk fraction."], cautionTone: "safety" };
  if (leaf === "execution_policy") return { ...base, role: ["The execution policy identifies the OMS algorithm allowed to turn approved quantity into broker orders."], timing: ["OMS resolves it only after Portfolio has approved the request and the Run Plan has permitted the action."], impact: ["It changes order style, pacing, price constraints, and amendment behavior without changing the strategy's evidence decision."], caution: ["The referenced policy must exist in the same approved release and be compatible with the runtime account and mode."], cautionTone: "safety" };
  if (leaf === "protection_profile") return { ...base, role: ["The protection profile selects the OMS rules that establish and maintain broker-visible protection for the resulting position."], timing: ["It is attached during order planning and remains relevant after fills while the position is open."], impact: ["It changes stop placement and protection maintenance independently of strategic exit evidence."], caution: ["Protection is safety-critical. Live orders must fail closed if the referenced protection profile cannot be resolved or established as required."], cautionTone: "safety" };
  if (leaf === "partial_fill_policy") return { ...base, role: ["This policy tells OMS what to do when only part of the requested quantity fills."], timing: ["It is applied after a partial execution and before the order deadline or cancellation workflow completes."], impact: ["The choice determines whether OMS continues seeking the remainder, accepts the partial position, or cancels what is left."], caution: ["Partial fills can leave a smaller position than strategy assumptions expect. Ensure protection and minimum-size rules remain valid for the accepted remainder."], cautionTone: "warning" };
  if (leaf === "deadline_ms") return { ...base, role: ["The deadline limits how long OMS may pursue this execution intent before applying its timeout behavior."], timing: ["The timer begins when the associated order intent becomes active in OMS."], impact: ["A shorter deadline reduces stale execution risk but can lower fill probability; a longer deadline increases opportunity to fill while exposing the order to more market change."], caution: ["The deadline does not override emergency cancellation, market-hours rules, broker state, or account safety."], cautionTone: "warning" };

  if (group === "Position adds") return { ...base, role: ["This parameter controls an ordered add step that may increase an already open campaign position."], timing: ["The add step is evaluated only after the initial position exists, its step is enabled, its usage limit is not exhausted, and its evidence passes."], impact: [base.impact[0], "Because an add increases exposure, Portfolio must independently reserve and approve additional account quantity."], caution: ["Adds compound risk in an existing campaign. They remain subject to cross-run account limits, shared reservations, and mandatory Live safety guardrails."], cautionTone: "safety" };
  if (group === "Reentry") return { ...base, role: ["This parameter controls whether and how a flat campaign may open a new position after a completed exit."], timing: ["Reentry is evaluated only after the prior position is fully closed and the configured cooldown, attempt limit, session gate, and evidence requirements allow it."], impact: [base.impact[0], "A more permissive value can increase repeated exposure to the same market thesis after an exit."], caution: ["Reentry is a new exposure request, not continuation of the old position. Portfolio approval and all account safety checks run again."], cautionTone: "safety" };
  if (group === "Strategic exits") return { ...base, role: ["This parameter belongs to a strategic exit route that may reduce or close an open campaign position when its evidence passes."], timing: ["The route is evaluated while a position is open and only within its configured activation and expiry window."], impact: [base.impact[0], "Strategic exits express trading intent; broker-held protection and emergency liquidation remain separate and can act first."], caution: ["Do not rely on a strategic exit as the only loss protection. Live safety and broker-held protection must remain effective even if strategy evaluation or market data fails."], cautionTone: "safety" };
  if (group === "Capabilities") return { ...base, role: ["This parameter configures optional behavior implemented by a registered strategy capability."], timing: ["It is read only when the capability binding is enabled and the registered implementation reaches the corresponding position-management behavior."], impact: [base.impact[0], "Capabilities can add code-defined actions beyond the base lifecycle, so their effect depends on the pinned capability revision."], caution: ["Confirm the registered capability revision and parameter contract before publication. Unknown settings must not be silently ignored in Live mode."], cautionTone: "warning" };
  if (group === "Engine parameters") return { ...base, role: ["This is an implementation-specific parameter read by the registered Strategy Engine definition."], timing: ["Its exact read point is defined by the pinned strategy definition revision; the surrounding path indicates the behavior it configures."], impact: [`The current value is ${current}. Changing it can alter calculations or thresholds inside the engine even when the visible lifecycle rules remain unchanged.`], caution: ["Treat engine parameters as advanced controls. Validate the exact definition revision in Replay or Backtest and do not infer semantics from the label alone."], cautionTone: "warning" };
  return base;
}

function helpForPath(path: string) { return `Advanced ${readableLabel(path)} setting. Changes are validated by the registered strategy implementation before publication.`; }
function readableLabel(value: string) { return value.replaceAll(".", " · ").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function uniqueId(base: string, existing: string[]) { let value = base; let index = 2; while (existing.includes(value)) value = `${base}-${index++}`; return value; }
function round(value: number) { return Math.round(value * 10_000) / 10_000; }
function percent(value: number) { return `${round(value * 100)}%`; }
function accountName(section: AccountSection, id: string) { return section.bindings.find((row) => row.account_key === id)?.name ?? id; }
function deploymentName(section: AssignmentSection, id: string) { return section.deployments.find((row) => row.run_plan_id === id)?.name ?? id; }
function urgencyOptions() { return ["patient", "regular", "urgent", "very_urgent"].map((value) => ({ label: readableLabel(value), value })); }

function readStoredExperience(): ConfigurationExperience {
  return window.localStorage.getItem("trading-configuration-experience") === "expert" ? "expert" : "guided";
}

function readStoredOmsStage(): OmsGuidedStage {
  return window.localStorage.getItem("trading-configuration-oms-stage") === "protection" ? "protection" : "execution";
}

function recommendedDraft(draft: Draft): Draft {
  const next = deepClone(draft);
  const profile = next.strategy.profiles.find((row) => row.protected) ?? next.strategy.profiles.find((row) => row.origin === "system");
  const oms = next.oms.profiles.find((row) => row.origin === "system");
  const deployment = next.assignments.deployments[0];
  if (profile) next.strategy.default_profile_id = profile.profile_id;
  if (deployment && profile && oms) {
    next.assignments.deployments[0] = { ...deployment, profile_id: profile.profile_id, oms_profile_id: oms.profile_id };
  }
  return next;
}

function cloneApprovedDraft(approved: Revision, current: Draft): Draft {
  return {
    accounts: deepClone(approved.payload.accounts),
    assignments: deepClone(approved.payload.assignments),
    market_discovery: deepClone(approved.payload.market_discovery),
    oms: deepClone(approved.payload.oms),
    portfolio: deepClone(approved.payload.portfolio),
    schema_version: approved.payload.schema_version,
    strategy: deepClone(approved.payload.strategy),
    updated_at: current.updated_at,
  };
}

function navigateGuidedStep(step: GuidedStep, onOmsStageChange: (value: OmsGuidedStage) => void) {
  if (step === "execution" || step === "protection") {
    window.localStorage.setItem("trading-configuration-oms-stage", step);
    onOmsStageChange(step);
  }
  window.location.hash = pageForGuidedStep(step);
}

function pageForGuidedStep(step: GuidedStep) {
  if (step === "execution" || step === "protection") return "oms-configuration";
  return pageForSection(step);
}

function reviewRows(draft: Draft, approved: Revision | null) {
  const profile = draft.strategy.profiles.find((row) => row.profile_id === draft.strategy.default_profile_id) ?? draft.strategy.profiles[0];
  const deployment = draft.assignments.deployments.find((row) => row.enabled) ?? draft.assignments.deployments[0];
  const mandate = draft.portfolio.mandates.find((row) => row.run_plan_id === deployment?.run_plan_id) ?? draft.portfolio.mandates[0];
  const oms = draft.oms.profiles.find((row) => row.profile_id === deployment?.oms_profile_id) ?? draft.oms.profiles[0];
  const execution = draft.oms.execution_policies.find((row) => row.policy_id === oms?.settings.entry_execution_policy_id) ?? draft.oms.execution_policies[0];
  const protection = draft.oms.protection_profiles.find((row) => row.profile_id === oms?.settings.protection_profile_id) ?? draft.oms.protection_profiles[0];
  const account = draft.accounts.bindings.find((row) => row.account_key === mandate?.account_key) ?? draft.accounts.bindings[0];
  const checks = releaseReadiness(draft);
  const inherited = <K extends keyof Draft>(key: K) => Boolean(approved && stableStringify(draft[key]) === stableStringify(approved.payload[key]));
  const state = (key: keyof Draft, valid: boolean, recommended: boolean): "Inherited" | "Invalid" | "Using recommended" | "Customized" => !valid ? "Invalid" : inherited(key) ? "Inherited" : recommended ? "Using recommended" : "Customized";
  return [
    { icon: GitBranch, label: "Strategy", selection: profile?.name ?? "Missing", state: state("strategy", Boolean(profile), Boolean(profile?.protected)), step: "strategy" as GuidedStep },
    { icon: Network, label: "Run Plan", selection: deployment?.name ?? "Missing", state: state("assignments", Boolean(deployment && checks[1]?.ready), false), step: "assignments" as GuidedStep },
    { icon: BriefcaseBusiness, label: "Portfolio", selection: mandate ? `${account?.name ?? mandate.account_key} · ${percent(mandate.maximum_planned_risk_fraction)} risk` : "Missing", state: state("portfolio", Boolean(checks[2]?.ready), false), step: "portfolio" as GuidedStep },
    { icon: Send, label: "Execution", selection: execution ? readableLabel(execution.name) : "Missing", state: state("oms", Boolean(execution), execution?.origin === "system"), step: "execution" as GuidedStep },
    { icon: ShieldCheck, label: "Protection", selection: protection?.name ?? "Missing", state: state("oms", Boolean(protection?.mandatory_catastrophic_backstop), protection?.origin === "system"), step: "protection" as GuidedStep },
    { icon: Boxes, label: "Accounts", selection: account ? `${account.name} · ${account.modes.map(readableLabel).join(", ")}` : "Missing", state: state("accounts", Boolean(checks[4]?.ready && checks[6]?.ready), false), step: "accounts" as GuidedStep },
  ];
}

function pageForSection(section: TradingConfigurationSection) {
  if (section === "strategy") return "strategy-configuration";
  if (section === "assignments") return "assignment-configuration";
  if (section === "portfolio") return "portfolio-configuration";
  if (section === "oms") return "oms-configuration";
  if (section === "accounts") return "account-configuration";
  return "revision-configuration";
}

function canvasApprovalSnapshot() {
  const profile = snapshotCanvasProfile(readCanvasRegistry());
  const states = Object.values(profile.workspaceStates ?? {});
  const containerCount = states.reduce((count, state) => count + state.openIds.length, 0);
  const serialized = stableStringify(profile);
  let hash = 2166136261;
  for (let index = 0; index < serialized.length; index += 1) {
    hash ^= serialized.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return { containerCount, profile, ready: containerCount > 0, revision: `canvas-${(hash >>> 0).toString(16).padStart(8, "0")}` };
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`).join(",")}}`;
  return JSON.stringify(value);
}
