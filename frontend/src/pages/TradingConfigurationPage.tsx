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
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { api } from "../api/client";
import { readCanvasRegistry, snapshotCanvasProfile } from "../app/canvasWorkspace";

export type TradingConfigurationSection =
  | "strategy"
  | "assignments"
  | "portfolio"
  | "oms"
  | "accounts"
  | "revisions";

type RuntimeMode = "replay" | "backtest" | "backtest_debug" | "paper" | "live";
type Primitive = boolean | number | string;
type ParameterMap = Record<string, unknown>;

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
  lifecycle: StrategyLifecycle;
  parameters: ParameterMap;
  profile_id: string;
  revision: number;
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

type RuleStage = {
  groups: RuleGroup[];
  operator: "all" | "any";
};

type EntryRules = {
  blockers: RuleStage;
  confirmation: RuleStage;
  opportunity: RuleStage;
};

type CapitalRequestConfig = {
  allow_replacement: boolean;
  mode: "fixed_quantity" | "mandate_fraction" | "risk_fraction" | "all_available";
  priority: number;
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
  trading_behavior: {
    adopt_manual_positions: boolean;
    eligible_sessions: string[];
    evaluation_trigger: string;
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

type Deployment = {
  book_id: string;
  campaign_policy: {
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
  deployment_id: string;
  description: string;
  enabled: boolean;
  mandate_ids: string[];
  modes: RuntimeMode[];
  name: string;
  oms_profile_id: string;
  profile_id: string;
  runtime_assignments: RuntimeAssignment[];
  selection_priority: number;
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

type AssignmentSection = { deployments: Deployment[]; universes: WatchUniverse[] };

type PortfolioPolicy = Record<string, Primitive | string[]>;
type Mandate = {
  account_key: string;
  allow_replacement: boolean;
  autonomy: "manual" | "confirm" | "automatic";
  deployment_id: string;
  enabled: boolean;
  mandate_id: string;
  maximum_cash_fraction: number;
  maximum_planned_risk_fraction: number;
  maximum_positions: number;
  minimum_replacement_improvement_pct: number;
  priority: number;
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
};
type AccountSection = { bindings: AccountBinding[] };

type Draft = {
  accounts: AccountSection;
  assignments: AssignmentSection;
  oms: OmsSection;
  portfolio: PortfolioSection;
  schema_version: number;
  strategy: StrategySection;
  updated_at?: string;
};

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
  strategy: {
    eyebrow: "Step 1 · Define behavior",
    icon: GitBranch,
    title: "Strategy Studio",
    description: "Create or adapt a Strategy Profile, tune its most-used behavior, then attach configurable capabilities. Profiles describe decisions; they do not own account capital or broker execution.",
  },
  assignments: {
    eyebrow: "Step 2 · Make it usable",
    icon: Network,
    title: "Strategy Deployments",
    description: "Turn a published Strategy Profile into a usable deployment by selecting its OMS profile, runtime modes, and account mandates. Ticker assignments remain run-local operational state.",
  },
  portfolio: {
    eyebrow: "Step 3 · Allocate capital",
    icon: BriefcaseBusiness,
    title: "Portfolio & Risk",
    description: "Set stable account safety policies, then define exactly how much of each account a deployment may use and whether it may propose replacing another position.",
  },
  oms: {
    eyebrow: "Shared execution authority",
    icon: ShieldCheck,
    title: "OMS & Protection",
    description: "Create reusable execution and protection profiles. Strategies select a profile; the shared OMS owns order tactics, broker lifecycle, partial fills, and protection.",
  },
  accounts: {
    eyebrow: "Stable runtime boundaries",
    icon: Boxes,
    title: "Accounts & Sessions",
    description: "Define stable application accounts and map them to broker or simulated sessions. Account capabilities and risk policy remain independent from any single strategy.",
  },
  revisions: {
    eyebrow: "Final publication gate",
    icon: BookOpenCheck,
    title: "Approved Releases",
    description: "Validate and publish one immutable release. New Replay runs pin the complete release, including Strategy Deployments, policies, OMS profiles, accounts, and every configured Canvas.",
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
  const [dirtySection, setDirtySection] = useState<TradingConfigurationSection | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "saving" | "saved" | "error">("loading");
  const [message, setMessage] = useState("");
  const [experience, setExperienceState] = useState<ConfigurationExperience>(() => readStoredExperience());
  const [showStudioHome, setShowStudioHome] = useState(() => section === "strategy" && !window.sessionStorage.getItem("configuration-studio-started"));
  const [omsGuidedStage, setOmsGuidedStageState] = useState<OmsGuidedStage>(() => readStoredOmsStage());
  const meta = SECTION_META[section];
  const Icon = meta.icon;

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    Promise.all([
      api<Draft>("/api/trading/configuration/draft"),
      api<{ approved: Revision | null }>("/api/trading/configuration/approved"),
      api<{ rows: Revision[] }>("/api/trading/configuration/revisions"),
    ])
      .then(([nextDraft, approvedPayload, revisionPayload]) => {
        if (cancelled) return;
        setDraft(nextDraft);
        setApproved(approvedPayload.approved);
        setRevisions(revisionPayload.rows);
        setDirtySection(null);
        setStatus("ready");
      })
      .catch((reason) => {
        if (cancelled) return;
        setMessage(reason instanceof Error ? reason.message : String(reason));
        setStatus("error");
      });
    return () => { cancelled = true; };
  }, [section]);

  function updateDraft<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => current ? { ...current, [key]: value } : current);
    setDirtySection(section);
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
    setStatus("saving");
    setMessage("");
    try {
      const saved = sections.length > 1
        ? await api<Draft>("/api/trading/configuration/draft", { body: JSON.stringify({ payload: next }), method: "PUT" })
        : await api<Draft>(`/api/trading/configuration/draft/${sections[0]}`, { body: JSON.stringify({ payload: next[sections[0]] }), method: "PUT" });
      setDraft(saved);
      setDirtySection(null);
      setStatus("saved");
      setMessage(successMessage);
      return saved;
    } catch (reason) {
      setStatus("error");
      setMessage(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    }
  }

  async function saveAndContinue(nextStep: GuidedStep) {
    if (!draft || section === "revisions") return;
    if (await saveSection()) navigateGuidedStep(nextStep, setOmsGuidedStage);
  }

  async function saveSection() {
    if (!draft || section === "revisions") return false;
    setStatus("saving");
    setMessage("");
    try {
      const nextDraft = await api<Draft>(`/api/trading/configuration/draft/${section}`, {
        body: JSON.stringify({ payload: draft[section] }),
        method: "PUT",
      });
      setDraft(nextDraft);
      setDirtySection(null);
      setStatus("saved");
      setMessage("Draft saved. Active and approved runs remain unchanged until you publish a release.");
      return true;
    } catch (reason) {
      setStatus("error");
      setMessage(reason instanceof Error ? reason.message : String(reason));
      return false;
    }
  }

  async function persistStrategy(value: StrategySection) {
    setStatus("saving");
    setMessage("");
    try {
      const nextDraft = await api<Draft>("/api/trading/configuration/draft/strategy", {
        body: JSON.stringify({ payload: value }),
        method: "PUT",
      });
      setDraft(nextDraft);
      setDirtySection(null);
      setStatus("saved");
      setMessage("Strategy clone saved to the configuration draft.");
      return nextDraft.strategy;
    } catch (reason) {
      setStatus("error");
      setMessage(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    }
  }

  async function publish() {
    if (!draft) return;
    setStatus("saving");
    setMessage("");
    try {
      const canvas = canvasApprovalSnapshot();
      if (!canvas.ready) throw new Error("Configure at least one Canvas container before publishing.");
      const revision = await api<Revision>("/api/trading/configuration/publish", {
        body: JSON.stringify({ canvas_profile: canvas.profile, canvas_revision: canvas.revision, label }),
        method: "POST",
      });
      setApproved(revision);
      setRevisions((current) => [revision, ...current.filter((row) => row.revision_id !== revision.revision_id)]);
      window.dispatchEvent(new CustomEvent("quant-trading-configuration-published"));
      setLabel("");
      setStatus("saved");
      setMessage(`Release ${revision.revision} is approved. New Replay runs now pin this exact configuration.`);
    } catch (reason) {
      setStatus("error");
      setMessage(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <div className="trading-configuration-page" data-configuration-section={section}>
      <header className="configuration-page-header">
        <div className="configuration-page-icon"><Icon size={20} /></div>
        <div>
          <span>{meta.eyebrow}</span>
          <h1>{meta.title}</h1>
          <p>{meta.description}</p>
        </div>
        <RevisionBadge approved={approved} />
      </header>

      {draft ? (
        <ConfigurationExperienceBar
          experience={experience}
          onExperienceChange={setExperience}
          onOpenHome={() => { window.sessionStorage.setItem("configuration-studio-started", "true"); setShowStudioHome(true); }}
        />
      ) : null}

      {draft && showStudioHome ? (
        <ConfigurationStudioHome
          approved={approved}
          draft={draft}
          pending={status === "saving"}
          onApplyRecommended={(next) => persistSections(next, ["strategy", "assignments"], "Protected Strategy and OMS starting points applied. Account and risk decisions were preserved for review.")}
          onCloneApproved={(next) => persistSections(next, ["strategy", "assignments", "portfolio", "oms", "accounts"], "The approved release was cloned into the mutable draft. Runtime authority remains unchanged until publication.")}
          onStart={(value) => { setExperience(value); if (value === "guided") navigateGuidedStep("strategy", setOmsGuidedStage); }}
        />
      ) : (
        <>
      <ConfigurationJourney active={section === "oms" ? omsGuidedStage : section} draft={draft} experience={experience} onOmsStageChange={setOmsGuidedStage} />

      {message ? (
        <div className={`configuration-message ${status === "error" ? "error" : "success"}`}>
          {status === "error" ? <TriangleAlert size={17} /> : <CheckCircle2 size={17} />}
          <span>{message}</span>
        </div>
      ) : null}

      {experience === "guided" && draft ? (
        <GuidedConfiguration
          approved={approved}
          draft={draft}
          label={label}
          omsStage={omsGuidedStage}
          onChange={updateDraft}
          onContinue={(step) => void saveAndContinue(step)}
          onLabelChange={setLabel}
          onOmsStageChange={setOmsGuidedStage}
          onPublish={publish}
          onSwitchToExpert={() => setExperience("expert")}
          publishing={status === "saving"}
          revisions={revisions}
          section={section}
        />
      ) : section === "revisions" ? (
        <RevisionPublisher
          approved={approved}
          draft={draft}
          label={label}
          revisions={revisions}
          publishing={status === "saving"}
          onLabelChange={setLabel}
          onPublish={publish}
        />
      ) : draft ? (
        <>
          {section === "strategy" ? <StrategyStudio draft={draft} section={draft.strategy} onChange={(value) => updateDraft("strategy", value)} onPersist={persistStrategy} /> : null}
          {section === "assignments" ? <DeploymentEditor draft={draft} onChange={(value) => updateDraft("assignments", value)} /> : null}
          {section === "portfolio" ? <PortfolioEditor draft={draft} onChange={(value) => updateDraft("portfolio", value)} /> : null}
          {section === "oms" ? <OmsEditor section={draft.oms} onChange={(value) => updateDraft("oms", value)} /> : null}
          {section === "accounts" ? <AccountsEditor draft={draft} onChange={(value) => updateDraft("accounts", value)} /> : null}
          {["assignments", "portfolio", "oms", "accounts"].includes(section) ? <EffectiveConfigurationPreview updatedAt={draft.updated_at || ""} /> : null}
          <div className="configuration-save-bar">
            <span>{dirtySection === section ? "Unsaved draft changes" : "Draft matches saved configuration"}</span>
            <button className="button primary" disabled={dirtySection !== section || status === "saving"} onClick={saveSection} type="button">
              <Save size={15} /> {status === "saving" ? "Saving…" : "Save draft"}
            </button>
          </div>
          <JsonInspector label={`${meta.title} generated JSON`} value={draft[section]} />
        </>
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
    <div><Sparkles size={16} /><span><strong>Configuration Studio</strong><small>One draft, two ways to work</small></span></div>
    <div className="configuration-experience-actions">
      <button onClick={onOpenHome} type="button"><RotateCcw size={14} /> Setup options</button>
      <div aria-label="Configuration editing mode" className="configuration-experience-switch" role="group">
        <button aria-pressed={experience === "guided"} onClick={() => onExperienceChange("guided")} type="button">Guided</button>
        <button aria-pressed={experience === "expert"} onClick={() => onExperienceChange("expert")} type="button"><Settings2 size={13} /> Expert</button>
      </div>
    </div>
  </div>;
}

function ConfigurationJourney({ active, draft, experience, onOmsStageChange }: {
  active: GuidedStep;
  draft: Draft | null;
  experience: ConfigurationExperience;
  onOmsStageChange: (value: OmsGuidedStage) => void;
}) {
  const steps = [
    { key: "strategy", label: "Strategy", ready: false },
    { key: "assignments", label: "Deploy", ready: false },
    { key: "portfolio", label: "Portfolio", ready: false },
    { key: "execution", label: "Execute", ready: false },
    { key: "protection", label: "Protect", ready: false },
    { key: "accounts", label: "Accounts", ready: false },
    { key: "revisions", label: "Review", ready: false },
  ];
  const activeIndex = steps.findIndex((step) => step.key === active);
  return (
    <nav aria-label="Configuration journey" className="configuration-journey" data-experience={experience}>
      {steps.map((step, index) => (
        <a aria-current={active === step.key ? "step" : undefined} data-ready={index < activeIndex ? "true" : "false"} data-step={step.key} href={`#${pageForGuidedStep(step.key as GuidedStep)}`} key={step.key} onClick={() => { if (step.key === "execution" || step.key === "protection") onOmsStageChange(step.key); }}>
          <span>{index < activeIndex ? <Check size={13} /> : index + 1}</span>
          <strong>{step.label}</strong>
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
      <div><span>Choose how to begin</span><h2>Set up a complete trading configuration without losing the details</h2><p>Every path edits the same schema-v{draft.schema_version} draft. Nothing becomes executable until the final release is reviewed and published.</p></div>
      <div className="configuration-home-authority"><ShieldCheck size={18} /><span><strong>One authority</strong><small>Guided choices become canonical draft patches</small></span></div>
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
        <span><ShieldCheck size={15} /><span><small>{deployment?.name ?? "Deployment"} OMS</small><strong>{systemOms?.name ?? "No system OMS available"}</strong></span></span>
        <span><BriefcaseBusiness size={15} /><span><small>Portfolio and accounts</small><strong>Preserved for review</strong></span></span>
      </div>
      <footer><p>This does not publish or enable Live. You will review all inherited mandate, protection, and broker bindings before approval.</p><button className="button primary" disabled={pending || !systemProfile || !systemOms || !deployment} onClick={() => void onApplyRecommended(recommended).then(() => onStart("guided"))} type="button">{pending ? "Applying…" : "Apply and review"}<ArrowRight size={15} /></button></footer>
    </div> : null}
    {selectedPath === "clone" && approved ? <div className="configuration-path-confirmation">
      <header><div><span>Clone preview</span><strong>Replace the mutable draft with release {approved.revision}</strong></div><button aria-label="Close preview" onClick={() => setSelectedPath(null)} type="button"><X size={16} /></button></header>
      <div className="configuration-clone-summary"><BadgeCheck size={17} /><p><strong>{approved.label}</strong><span>{approved.payload.strategy.profiles.length} profiles · {approved.payload.assignments.deployments.length} deployments · {approved.payload.portfolio.mandates.length} mandates · {approved.payload.accounts.bindings.length} accounts</span></p></div>
      <footer><p>The approved release remains immutable. Only the draft is replaced, and it still requires publication to affect new runs.</p><button className="button primary" disabled={pending} onClick={() => void onCloneApproved(cloneApprovedDraft(approved, draft)).then(() => onStart("guided"))} type="button">{pending ? "Cloning…" : "Clone into draft"}<ArrowRight size={15} /></button></footer>
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
  const profile = draft.strategy.profiles.find((row) => row.profile_id === draft.strategy.default_profile_id) ?? draft.strategy.profiles[0];
  const deployment = draft.assignments.deployments.find((row) => row.enabled) ?? draft.assignments.deployments[0];
  const mandate = draft.portfolio.mandates.find((row) => row.deployment_id === deployment?.deployment_id) ?? draft.portfolio.mandates[0];
  const omsProfile = draft.oms.profiles.find((row) => row.profile_id === deployment?.oms_profile_id) ?? draft.oms.profiles[0];
  const executionPolicy = draft.oms.execution_policies.find((row) => row.policy_id === omsProfile?.settings.entry_execution_policy_id) ?? draft.oms.execution_policies[0];
  const protectionProfile = draft.oms.protection_profiles.find((row) => row.profile_id === omsProfile?.settings.protection_profile_id) ?? draft.oms.protection_profiles[0];
  const account = draft.accounts.bindings.find((row) => row.account_key === mandate?.account_key) ?? draft.accounts.bindings[0];
  const steps: GuidedStep[] = ["strategy", "assignments", "portfolio", "execution", "protection", "accounts", "revisions"];
  const index = steps.indexOf(step);
  const previous = steps[index - 1];
  const next = steps[index + 1];
  const context = guidedContextRows(draft, step);
  const [questionIndex, setQuestionIndex] = useState(0);
  useEffect(() => setQuestionIndex(0), [step]);
  const questionCount = step === "assignments" || step === "protection" ? 2 : step === "accounts" && !account?.modes.some((mode) => mode === "paper" || mode === "live") ? 2 : 3;

  function replaceDeployment(nextDeployment: Deployment) {
    onChange("assignments", { ...draft.assignments, deployments: draft.assignments.deployments.map((row) => row.deployment_id === deployment.deployment_id ? nextDeployment : row) });
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
  if (step === "strategy") return <GuidedStrategyConfiguration draft={draft} onChange={onChange} onContinue={() => onContinue("assignments")} profile={profile} />;

  return <div className="guided-configuration-shell" data-guided-step={step}>
    <main className="guided-question-surface">
      <header><span>{guidedStepTitle(step)} · question {questionIndex + 1} of {questionCount}</span><h2>{guidedStepDescription(step)}</h2><div className="guided-question-progress"><span style={{ width: `${((questionIndex + 1) / questionCount) * 100}%` }} /></div></header>
      <div className="guided-question-list" data-question-index={questionIndex}>
        {step === "assignments" ? <>
          <GuidedQuestion description="A deployment is the runnable composition: behavior, universe, execution profile, account mandates, and runtime modes." label="What should this deployment run?" status={deployment.enabled ? "Configured" : "Needs review"}>
            <div className="configuration-field-grid"><SelectField help="Behavior evaluated by this deployment." label="Strategy Profile" onChange={(profile_id) => replaceDeployment({ ...deployment, profile_id })} options={draft.strategy.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={deployment.profile_id} /><SelectField help="Reusable execution and protection behavior." label="OMS profile" onChange={(oms_profile_id) => replaceDeployment({ ...deployment, oms_profile_id })} options={draft.oms.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={deployment.oms_profile_id} /><SelectField help="Eligible symbol authority." label="Watch Universe" onChange={(universe_id) => replaceDeployment({ ...deployment, universe_id })} options={draft.assignments.universes.map((row) => ({ label: row.name, value: row.universe_id }))} value={deployment.universe_id} /></div>
          </GuidedQuestion>
          <GuidedQuestion description="Enable only modes in which the account and mandate bindings should be valid." label="Where may it run?" status={deployment.modes.length ? "Configured" : "Needs decision"}><ModeSelector modes={deployment.modes} onChange={(modes) => replaceDeployment({ ...deployment, modes })} /></GuidedQuestion>
        </> : null}
        {step === "portfolio" ? <>
          <GuidedQuestion description={draft.accounts.bindings.length === 1 ? "Only one account is eligible, so it is selected automatically. Portfolio still synchronizes its cash, positions, buying power, and reservations before approving an order." : "Portfolio checks synchronized cash, positions, buying power, reservations, and policy limits before approving an order."} label="Which account supplies the capital?" status={draft.accounts.bindings.length === 1 ? "Selected automatically" : "Choose an account"}>{draft.accounts.bindings.length === 1 ? <div className="guided-confirmed-choice"><BadgeCheck size={20} /><span><strong>{account.name}</strong><small>{readableLabel(account.account_class)} account · {account.modes.map(readableLabel).join(", ")}</small></span></div> : <DecisionOptions onChange={(account_key) => replaceMandate({ ...mandate, account_key })} options={draft.accounts.bindings.map((row) => ({ detail: `${readableLabel(row.account_class)} account`, label: row.name, value: row.account_key }))} value={mandate.account_key} />}</GuidedQuestion>
          <GuidedQuestion description="These are ceilings, not target position sizes. Portfolio can approve less when cash, buying power, open positions, or another risk rule requires it." label="What are the maximum capital and loss limits?" status="Review these limits"><div className="guided-limit-fields"><div><NumberField help="Maximum share of otherwise available cash." label="Maximum cash fraction" maximum={1} minimum={0} onChange={(maximum_cash_fraction) => replaceMandate({ ...mandate, maximum_cash_fraction })} step={0.01} unit="fraction" value={mandate.maximum_cash_fraction} /><p>100% allows all otherwise available cash; Portfolio can still approve less.</p></div><div><NumberField help="Maximum combined loss planned at active stops." label="Maximum planned loss fraction" maximum={1} minimum={0} onChange={(maximum_planned_risk_fraction) => replaceMandate({ ...mandate, maximum_planned_risk_fraction })} step={0.001} unit="fraction" value={mandate.maximum_planned_risk_fraction} /><p>1% caps the combined planned loss at all active stops to 1% of account equity.</p></div><div><NumberField help="Maximum simultaneous positions for this deployment." label="Maximum open positions" minimum={1} onChange={(maximum_positions) => replaceMandate({ ...mandate, maximum_positions })} step={1} value={mandate.maximum_positions} /><p>This setup may have at most {mandate.maximum_positions} positions open at once.</p></div></div></GuidedQuestion>
          <GuidedQuestion description="Automatic autonomy may act only within every account policy, risk, broker, and protection gate." label="How much autonomy should Portfolio have?" status={mandate.autonomy === "automatic" ? "Needs review" : "Configured"}><DecisionOptions onChange={(autonomy) => replaceMandate({ ...mandate, autonomy: autonomy as Mandate["autonomy"] })} options={[{ detail: "Prepare proposals for an operator", label: "Manual", value: "manual" }, { detail: "Require confirmation before capital action", label: "Confirm", recommended: true, value: "confirm" }, { detail: "Act inside all published limits", label: "Automatic", value: "automatic" }]} value={mandate.autonomy} /></GuidedQuestion>
        </> : null}
        {step === "execution" ? <>
          <GuidedQuestion description="This controls how quickly OMS follows the market while staying inside the approved price and time limits. It never changes the approved quantity." label="How aggressively should entries seek a fill?" status="Choose a pace"><DecisionOptions onChange={(entry_execution_policy_id) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, entry_execution_policy_id } })} options={draft.oms.execution_policies.filter((row) => ["adaptive_patient", "adaptive_regular", "adaptive_urgent", executionPolicy.policy_id].includes(row.policy_id)).map((row) => ({ detail: row.policy_id === "adaptive_patient" ? "Wait longer for a favorable price." : row.policy_id === "adaptive_urgent" ? "Follow the market quickly when completing the entry matters most." : row.policy_id === "adaptive_regular" ? "Balance price quality with a timely fill." : `Keep the current ${readableLabel(row.name)} policy.`, label: row.policy_id === "adaptive_patient" ? "Patient" : row.policy_id === "adaptive_urgent" ? "Fast" : row.policy_id === "adaptive_regular" ? "Balanced" : readableLabel(row.name), recommended: row.policy_id === "adaptive_regular", value: row.policy_id }))} value={omsProfile.settings.entry_execution_policy_id} /></GuidedQuestion>
          <GuidedQuestion description="A partial fill means the broker filled only part of the approved quantity. OMS reconciles the broker's confirmed fill first, so it never orders more than the true remainder." label="What should happen to the unfilled remainder?" status="Choose a response"><DecisionOptions onChange={(partial_fill_policy) => replaceExecutionPolicy({ ...executionPolicy, partial_fill_policy: partial_fill_policy as ExecutionPolicyConfig["partial_fill_policy"] })} options={[{ detail: "Keep working only the broker-confirmed remainder, using the latest allowed price.", label: "Finish the approved quantity", recommended: true, value: "complete_remainder" }, { detail: "Keep the shares already filled and stop trying to fill the rest.", label: "Accept the partial fill", value: "accept_partial" }, { detail: "Cancel the remainder immediately; keep any shares already filled.", label: "Cancel the remainder", value: "cancel_remainder" }]} value={executionPolicy.partial_fill_policy} /></GuidedQuestion>
          <GuidedQuestion description="Adaptive orders need a current bid and ask. QMD is the shared normalized source; IBKR uses the broker stream; Simulated is deterministic for historical runs." label="Which price feed should adaptive orders use?" status="Choose a source"><DecisionOptions onChange={(quote_source) => replaceExecutionPolicy({ ...executionPolicy, quote_source: quote_source as ExecutionPolicyConfig["quote_source"] })} options={[{ detail: "Shared normalized market data for live and replay-capable logic.", label: "QMD", recommended: true, value: "qmd" }, { detail: "Quotes from the active IBKR gateway session.", label: "IBKR", value: "ibkr" }, { detail: "Deterministic quotes for repeatable historical execution.", label: "Simulated", value: "simulated" }]} value={executionPolicy.quote_source} /></GuidedQuestion>
        </> : null}
        {step === "protection" ? <>
          <GuidedQuestion description={draft.oms.protection_profiles.length === 1 ? "Only one approved protection design is available, so it is selected automatically. Its stop orders are placed and reconciled independently of normal strategy exits." : "Protection stays active independently of normal strategy exits and cannot be weakened by another authority."} label="How is a new position protected?" status={draft.oms.protection_profiles.length === 1 ? "Selected automatically" : "Choose protection"}>{draft.oms.protection_profiles.length === 1 ? <div className="guided-confirmed-choice"><ShieldCheck size={20} /><span><strong>{protectionProfile.name}</strong><small>{protectionProfile.slices.length} stop slice{protectionProfile.slices.length === 1 ? "" : "s"}; catastrophic backstop {protectionProfile.mandatory_catastrophic_backstop ? "required" : "not required"}</small></span></div> : <DecisionOptions onChange={(protection_profile_id) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, protection_profile_id } })} options={draft.oms.protection_profiles.map((row) => ({ detail: `${row.slices.length} stop slice${row.slices.length === 1 ? "" : "s"}`, label: row.name, recommended: row.mandatory_catastrophic_backstop, value: row.profile_id }))} value={omsProfile.settings.protection_profile_id} />}</GuidedQuestion>
          <GuidedQuestion description="Profit-pocket transitions define how stop protection changes after an intentional reduction." label="After pocketing profit, what happens to remaining protection?" status="Configured"><DecisionOptions onChange={(profit_pocket_transition) => replaceProtectionProfile({ ...protectionProfile, profit_pocket_transition })} options={[{ detail: "Raise the remaining floor to entry plus buffer", label: "Move to breakeven", recommended: true, value: "move_to_breakeven" }, { detail: "Activate the configured swing-based trail", label: "Start swing trail", value: "start_swing_trail" }, { detail: "Retain the existing stop contract", label: "Keep existing protection", value: "keep_existing" }]} value={protectionProfile.profit_pocket_transition} /></GuidedQuestion>
          <div className="guided-safety-callout"><ShieldCheck size={16} /><p><strong>Protection cannot be weakened by another authority.</strong><span>{protectionProfile.slices.length} configured slice{protectionProfile.slices.length === 1 ? "" : "s"}; catastrophic backstop {protectionProfile.mandatory_catastrophic_backstop ? "required" : "requires expert review"}.</span></p></div>
        </> : null}
        {step === "accounts" ? <>
          <GuidedQuestion description="This binding connects the setup to a simulated account or an externally discovered IBKR account. Runtime synchronizes cash, positions, and buying power before Portfolio acts." label="Which account will this configuration use?" status="Account selected"><div className="guided-confirmed-choice"><BadgeCheck size={20} /><span><strong>{account.name}</strong><small>{readableLabel(account.account_class)} · Portfolio policy {account.portfolio_policy_id}</small></span></div></GuidedQuestion>
          <GuidedQuestion description="Paper and Live require an exact external IBKR account and session match. Secrets remain outside configuration." label="Which modes may bind this account?" status={account.modes.length ? "Configured" : "Needs decision"}><ModeSelector modes={account.modes} onChange={(modes) => replaceAccount({ ...account, modes })} /></GuidedQuestion>
          {account.modes.some((mode) => mode === "paper" || mode === "live") ? <GuidedQuestion description="These identifiers must match IBKR discovery. Publication and runtime preflight fail closed on mismatch." label="Confirm the broker binding" status={account.source_account_id.trim() && account.session_key.trim() ? "Needs broker verification" : "Invalid"}><div className="configuration-field-grid"><TextField help="Exact externally discovered IBKR account ID." label="IBKR account ID" onChange={(source_account_id) => replaceAccount({ ...account, source_account_id })} value={account.source_account_id} /><TextField help="Configured gateway session identity." label="Session key" onChange={(session_key) => replaceAccount({ ...account, session_key })} value={account.session_key} /></div></GuidedQuestion> : null}
        </> : null}
      </div>
      <details className="guided-running-summary"><summary>Your setup so far <ChevronRight size={15} /></summary><div>{context.map((row) => <span key={row.label}><small>{row.label}</small><strong>{row.value}</strong></span>)}</div></details>
      <GuidedFooter isFirst={questionIndex === 0} isLast={questionIndex === questionCount - 1} next={next} onNext={() => questionIndex < questionCount - 1 ? setQuestionIndex(questionIndex + 1) : next && onContinue(next)} onPrevious={() => questionIndex > 0 ? setQuestionIndex(questionIndex - 1) : previous && navigateGuidedStep(previous, onOmsStageChange)} previous={previous} />
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

function GuidedStrategyConfiguration({ draft, onChange, onContinue, profile }: {
  draft: Draft;
  onChange: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  onContinue: () => void;
  profile: StrategyProfile;
}) {
  const [questionIndex, setQuestionIndex] = useState(0);
  const definition = draft.strategy.definitions.find((row) => row.strategy_id === profile.definition_id);
  const supportedSides = definition?.supported_sides?.length ? definition.supported_sides : ["long" as const];
  const initial = profile.lifecycle.initial_entry;
  const reentry = profile.lifecycle.reentry;
  const advanced = flattenPrimitives(profile.parameters).filter((row) => !LEGACY_ENTRY_LOGIC_PATHS.has(row.path));

  function replaceProfile(nextProfile: StrategyProfile) {
    onChange("strategy", {
      ...draft.strategy,
      profiles: draft.strategy.profiles.map((row) => row.profile_id === profile.profile_id ? nextProfile : row),
    });
  }
  function replaceInitial(nextInitial: StrategyLifecycle["initial_entry"]) {
    replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, initial_entry: nextInitial } });
  }
  function replaceReentry(nextReentry: StrategyLifecycle["reentry"]) {
    replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, reentry: nextReentry } });
  }
  function replaceAddStep(stepId: string, nextStep: AddStep) {
    replaceInitial({ ...initial, add_steps: initial.add_steps.map((row) => row.step_id === stepId ? nextStep : row) });
  }
  function addAddStep() {
    const source = draft.strategy.input_catalog[0];
    if (!source) return;
    const stepId = uniqueId("position-add", initial.add_steps.map((row) => row.step_id));
    replaceInitial({ ...initial, add_steps: [{
      capital_request: { allow_replacement: false, mode: "mandate_fraction", priority: 50, value: 0.1 },
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

  const questions: GuidedStrategyQuestionDefinition[] = [
    {
      id: "profile", section: "Behavior", title: "Which trading plan are you configuring?",
      description: "Choose the reusable plan whose complete lifecycle you want to review.",
      guide: "The protected profile is a safe starting point. Selecting a profile changes the draft default; it does not publish or start trading.",
      content: <DecisionOptions onChange={(profile_id) => onChange("strategy", { ...draft.strategy, default_profile_id: profile_id })} options={draft.strategy.profiles.map((row) => ({ detail: row.description || `${readableLabel(row.origin)} profile`, label: row.name, recommended: row.protected, value: row.profile_id }))} value={profile.profile_id} />,
    },
    {
      id: "identity", section: "Behavior", title: "How should this plan be identified?",
      description: "Give the configured profile a clear operator-facing name and description, and decide whether it remains available for deployments.",
      guide: "This label appears in deployments, reviews, journals, and runtime evidence. Disabling it preserves the configuration but prevents new use.",
      content: <div className="guided-form-grid"><TextField help="Operator-facing profile name." label="Plan name" onChange={(name) => replaceProfile({ ...profile, name })} value={profile.name} /><TextField help="A short explanation of the behavior and intended use." label="Plan description" onChange={(description) => replaceProfile({ ...profile, description })} value={profile.description} /><BooleanField help="Allow deployments to select this configured plan." label="Available for use" onChange={(enabled) => replaceProfile({ ...profile, enabled })} value={profile.enabled} /></div>,
    },
    {
      id: "side", section: "Behavior", title: "Should this plan trade long or short?",
      description: "Direction determines how a campaign opens, adds, reduces, and closes exposure.",
      guide: "Long buys first and sells later. Short sells borrowed shares first and buys them back; current broker shortability is still required.",
      content: <DecisionOptions onChange={(side) => replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, trading_behavior: { ...profile.lifecycle.trading_behavior, side: side as "long" | "short" } } })} options={supportedSides.map((side) => ({ detail: side === "long" ? "Buy to open; sell to reduce or close." : "Short-sell to open; buy to cover.", label: readableLabel(side), recommended: side === "long", value: side }))} value={profile.lifecycle.trading_behavior.side} />,
    },
    {
      id: "evaluation", section: "Behavior", title: "What should wake the strategy for a new evaluation?",
      description: "Choose the causal event that makes the engine re-check active ticker campaigns.",
      guide: "This is an evaluation clock, not an entry signal. Entry still requires its opportunity, confirmation, and blocker logic to pass.",
      content: <DecisionOptions onChange={(evaluation_trigger) => replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, trading_behavior: { ...profile.lifecycle.trading_behavior, evaluation_trigger } } })} options={[{ label: "Indicator update", detail: "Re-evaluate when a configured indicator publishes a new value.", recommended: true, value: "indicator_update" }, { label: "Signal event", detail: "Re-evaluate when a scored signal changes lifecycle state.", value: "signal_event" }, { label: "Bar close", detail: "Re-evaluate only when the selected calculation bar closes.", value: "bar_close" }]} value={profile.lifecycle.trading_behavior.evaluation_trigger} />,
    },
    {
      id: "manual-adoption", section: "Behavior", title: "May this plan take over a manually opened position?",
      description: "A managed campaign can adopt an existing position and apply the configured exit and protection behavior.",
      guide: "Adoption does not create a new entry order. Account, side, ticker ownership, and reconciliation must all match before management begins.",
      content: <DecisionOptions onChange={(value) => replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, trading_behavior: { ...profile.lifecycle.trading_behavior, adopt_manual_positions: value === "yes" } } })} options={[{ label: "Allow adoption", detail: "Manage a compatible manual position with this lifecycle.", recommended: true, value: "yes" }, { label: "Do not adopt", detail: "Manage only positions opened by this strategy campaign.", value: "no" }]} value={profile.lifecycle.trading_behavior.adopt_manual_positions ? "yes" : "no"} />,
    },
    {
      id: "sessions", section: "Behavior", title: "When may a new entry be evaluated?",
      description: "Select every market session in which initial entries, adds, and reentries may become eligible.",
      guide: "Protective exits remain active whenever exposure exists. OMS derives compatible broker session instructions from this choice.",
      content: <ModeChoices onChange={(eligible_sessions) => replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, trading_behavior: { ...profile.lifecycle.trading_behavior, eligible_sessions } } })} options={["premarket", "regular", "after_hours"]} values={profile.lifecycle.trading_behavior.eligible_sessions} />,
    },
    {
      id: "initial-capital", section: "Initial entry", title: "How much capital should the first entry request?",
      description: "Describe the strategy's relative request; Portfolio calculates the safe account-specific quantity.",
      guide: "This is a request, not an entitlement. Portfolio may reduce or reject it after checking mandates, cash, buying power, positions, and planned loss.",
      content: <GuidedCapitalRequestFields onChange={(capital_request) => replaceInitial({ ...initial, capital_request })} value={initial.capital_request} />,
    },
    {
      id: "initial-order", section: "Initial entry", title: "How should the approved first entry be executed and protected?",
      description: "Choose the semantic execution, partial-fill, deadline, and broker-held protection intent.",
      guide: "Strategy selects the policy. OMS reads the allowed quote source, manages repricing and partial fills, and cannot exceed Portfolio's approved quantity.",
      content: <GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceInitial({ ...initial, order_intent })} value={initial.order_intent} />,
    },
    ...(["opportunity", "confirmation", "blockers"] as const).map((stage) => ({
      id: `initial-${stage}`, section: "Initial entry", title: stage === "opportunity" ? "What identifies a possible first entry?" : stage === "confirmation" ? "What must confirm the first entry?" : "What must prevent the first entry?",
      description: stage === "opportunity" ? "Opportunity groups find a candidate setup." : stage === "confirmation" ? "Confirmation groups must validate that the opportunity is actionable." : "A passing blocker prevents entry even when opportunity and confirmation pass.",
      guide: "The complete sentence is: enter when Opportunity passes, Confirmation passes, and Blockers do not pass. Configure each group's ALL/ANY logic explicitly.",
      content: <RuleStageEditor catalog={draft.strategy.input_catalog} label={`Initial entry ${readableLabel(stage)}`} onChange={(value) => replaceInitial({ ...initial, [stage]: value })} stage={initial[stage]} />,
    })),
    {
      id: "adds-overview", section: "Position adds", title: "Which position-add actions are available?",
      description: "Each action may request more capital only while a position is already open.",
      guide: "Disabled actions remain saved. Maximum uses is a fill count; a rejected or unfilled request must not consume a use.",
      content: <div className="guided-action-list"><button className="button compact" onClick={addAddStep} type="button"><Plus size={14} /> Add another action</button>{initial.add_steps.map((step) => <article key={step.step_id}><div><TextField help="Operator-facing action name." label="Action name" onChange={(name) => replaceAddStep(step.step_id, { ...step, name })} value={step.name} /><NumberField help="Maximum confirmed fills during one campaign." label="Maximum uses" minimum={1} onChange={(maximum_uses) => replaceAddStep(step.step_id, { ...step, maximum_uses })} step={1} unit="fills" value={step.maximum_uses} /></div><BooleanField help="Allow this action to request an add." label="Enabled" onChange={(enabled) => replaceAddStep(step.step_id, { ...step, enabled })} value={step.enabled} /><button className="button compact danger" onClick={() => replaceInitial({ ...initial, add_steps: initial.add_steps.filter((row) => row.step_id !== step.step_id) })} type="button"><Trash2 size={14} /> Remove</button></article>)}</div>,
    },
    ...initial.add_steps.map((step) => ({
      id: `add-${step.step_id}`, section: "Position adds", title: `How should “${step.name}” work?`,
      description: "Configure its causal trigger, relative capital request, and execution/protection intent.",
      guide: "An add is not a reentry: it increases an open position. Portfolio re-sizes against current account risk, and OMS applies the selected protection add policy.",
      content: <div className="guided-composite-form"><RuleStageEditor catalog={draft.strategy.input_catalog} label={`${step.name} trigger`} onChange={(rules) => replaceAddStep(step.step_id, { ...step, rules })} stage={step.rules} /><GuidedCapitalRequestFields onChange={(capital_request) => replaceAddStep(step.step_id, { ...step, capital_request })} value={step.capital_request} /><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceAddStep(step.step_id, { ...step, order_intent })} value={step.order_intent} /></div>,
    })),
    {
      id: "reentry-policy", section: "Reentry", title: "May the campaign enter again after a complete exit?",
      description: "Reentry is a new flat-to-open transition while the same ticker campaign retains ownership.",
      guide: "It is separate from adding to an open position. Require fresh confirmation to prevent the previous entry evidence from being reused.",
      content: <div className="guided-form-grid"><BooleanField help="Permit another flat-to-open transition." label="Enable reentry" onChange={(enabled) => replaceReentry({ ...reentry, enabled })} value={reentry.enabled} /><BooleanField help="Require evidence newer than the previous entry." label="Require fresh confirmation" onChange={(require_new_confirmation) => replaceReentry({ ...reentry, require_new_confirmation })} value={reentry.require_new_confirmation} /><NumberField help="Minimum time after a confirmed full exit." label="Cooldown" minimum={0} onChange={(cooldown_ms) => replaceReentry({ ...reentry, cooldown_ms })} step={100} unit="ms" value={reentry.cooldown_ms} /><NumberField help="Maximum reentries in one ticker campaign." label="Maximum attempts" minimum={0} onChange={(maximum_attempts) => replaceReentry({ ...reentry, maximum_attempts })} step={1} unit="entries" value={reentry.maximum_attempts} /></div>,
    },
    ...(reentry.enabled ? [{
      id: "reentry-capital", section: "Reentry", title: "How much capital should a reentry request?", description: "Reentry owns an independent capital request.", guide: "Portfolio recalculates capacity from the current synchronized account; the previous position size is not reused automatically.", content: <GuidedCapitalRequestFields onChange={(capital_request) => replaceReentry({ ...reentry, capital_request })} value={reentry.capital_request} />,
    }, {
      id: "reentry-order", section: "Reentry", title: "How should an approved reentry be executed and protected?", description: "Choose the reentry-specific execution and protection intent.", guide: "This may differ from the first entry when a second opportunity is more urgent or requires different protection.", content: <GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceReentry({ ...reentry, order_intent })} value={reentry.order_intent} />,
    }, ...(["opportunity", "confirmation", "blockers"] as const).map((stage) => ({
      id: `reentry-${stage}`, section: "Reentry", title: stage === "opportunity" ? "What identifies a possible reentry?" : stage === "confirmation" ? "What must confirm a reentry?" : "What must prevent a reentry?", description: "Reentry owns independent decision rules; it does not silently reuse the first-entry rule set.", guide: "Importing or copying initial rules creates editable copies. Fresh-evidence and cooldown gates still apply before evaluation.", content: <RuleStageEditor catalog={draft.strategy.input_catalog} label={`Reentry ${readableLabel(stage)}`} onChange={(value) => replaceReentry({ ...reentry, rules: { ...reentry.rules, [stage]: value } })} stage={reentry.rules[stage]} />,
    }))] : []),
    {
      id: "exit-overview", section: "Strategic exits", title: "Which strategic exit routes are available?", description: "Enable, name, or add the rule sets that can reduce or close a position.", guide: "Broker-held protection is independent and remains active even when every strategic exit is disabled or delayed.", content: <div className="guided-action-list"><button className="button compact" onClick={addExit} type="button"><Plus size={14} /> Add exit route</button>{profile.lifecycle.exit.rule_sets.map((ruleSet) => <article key={ruleSet.rule_set_id}><div><TextField help="Operator-facing route name." label="Exit name" onChange={(name) => replaceExit(ruleSet.rule_set_id, { ...ruleSet, name })} value={ruleSet.name} /><TextField help="Short explanation of this exit thesis." label="Purpose" onChange={(summary) => replaceExit(ruleSet.rule_set_id, { ...ruleSet, summary })} value={ruleSet.summary} /></div><BooleanField help="Evaluate this route while a position is open." label="Enabled" onChange={(enabled) => replaceExit(ruleSet.rule_set_id, { ...ruleSet, enabled })} value={ruleSet.enabled} /><button className="button compact danger" disabled={profile.lifecycle.exit.rule_sets.length <= 1} onClick={() => replaceProfile({ ...profile, lifecycle: { ...profile.lifecycle, exit: { rule_sets: profile.lifecycle.exit.rule_sets.filter((row) => row.rule_set_id !== ruleSet.rule_set_id) } } })} type="button"><Trash2 size={14} /> Remove</button></article>)}</div>,
    },
    ...profile.lifecycle.exit.rule_sets.map((ruleSet) => ({
      id: `exit-${ruleSet.rule_set_id}`, section: "Strategic exits", title: `When should “${ruleSet.name}” act?`, description: "Configure its evidence, validity window, position action, and OMS execution intent.", guide: "Rule sets are evaluated in configured order. Reduce releases a fraction; Close requests the full current position. Protective stops remain separate.", content: <GuidedExitRuleFields catalog={draft.strategy.input_catalog} draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(next) => replaceExit(ruleSet.rule_set_id, next)} value={ruleSet} />,
    })),
    ...draft.strategy.capability_catalog.map((capability) => {
      const binding = profile.capabilities.find((row) => row.capability_id === capability.capability_id);
      return {
        id: `capability-${capability.capability_id}`, section: "Capabilities", title: `Should “${capability.name}” be available?`, description: capability.summary, guide: capability.order_entry_action ? "This capability also appears as a deliberate Order Entry action. Strategy still emits semantic intent; Portfolio and OMS retain authority." : "Capabilities extend the lifecycle without replacing initial entry, reentry, exit, Portfolio, or OMS authority.", content: binding ? <GuidedCapabilityFields binding={binding} definition={capability} onChange={(next) => replaceProfile(updateCapability(profile, binding.capability_id, next))} /> : <p>This profile does not contain the registered capability binding.</p>,
      };
    }),
  ];

  const advancedGroups = new Map<string, typeof advanced>();
  advanced.forEach((item) => {
    const parts = item.path.split(".");
    const group = parts[0] === "protection" ? parts.slice(0, 2).join(".") : parts[0];
    advancedGroups.set(group, [...(advancedGroups.get(group) ?? []), item]);
  });
  advancedGroups.forEach((items, group) => questions.push({
    id: `advanced-${group}`, section: "Advanced", title: `Review ${readableLabel(group)} tuning`,
    description: "These implementation-level values remain part of the published strategy profile.",
    guide: "Keep the current values when you do not have evidence to retune them. They are shown here so Guided mode never silently skips published strategy parameters.",
    content: <div className="guided-form-grid">{items.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").slice(-1)[0]), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} onChange={(value) => replaceProfile({ ...profile, parameters: setPath(profile.parameters, item.path, value) })} value={item.value} />)}</div>,
  }));

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

  return <main className="guided-strategy-wizard">
    <nav aria-label="Strategy setup sections" className="guided-strategy-section-nav">{sections.map((section) => { const firstIndex = questions.findIndex((question) => question.section === section); return <button aria-current={section === current.section ? "step" : undefined} key={section} onClick={() => setQuestionIndex(firstIndex)} type="button"><span>{section}</span><small>{questions.filter((question) => question.section === section).length}</small></button>; })}</nav>
    <section className="guided-strategy-question">
      <header><span>{current.section} · {sectionPosition} of {sectionQuestions.length}</span><small>Question {safeIndex + 1} of {questions.length}</small></header>
      <div className="guided-question-progress"><span style={{ width: `${((safeIndex + 1) / Math.max(questions.length, 1)) * 100}%` }} /></div>
      <div className="guided-strategy-prompt"><h2>{current.title}</h2><p>{current.description}</p><aside><CircleHelp size={17} /><span><strong>Why this matters</strong>{current.guide}</span></aside></div>
      <div className="guided-strategy-controls">{current.content}</div>
      <details className="guided-running-summary"><summary>Your setup so far <ChevronRight size={15} /></summary><div>{recap.map((row) => <span key={row.label}><small>{row.label}</small><strong>{row.value}</strong></span>)}</div></details>
      <footer className="guided-strategy-navigation"><button className="button" disabled={safeIndex === 0} onClick={() => setQuestionIndex(safeIndex - 1)} type="button"><ArrowLeft size={15} /> Previous</button><div>{nextSectionIndex > 0 ? <button onClick={() => setQuestionIndex(nextSectionIndex)} type="button">Keep remaining {current.section} values</button> : <span>Review each published strategy decision</span>}</div><button className="button primary" onClick={() => safeIndex < questions.length - 1 ? setQuestionIndex(safeIndex + 1) : onContinue()} type="button">{safeIndex < questions.length - 1 ? "Next question" : "Save strategy and continue"} <ArrowRight size={15} /></button></footer>
    </section>
  </main>;
}

function GuidedCapitalRequestFields({ onChange, value }: { onChange: (value: CapitalRequestConfig) => void; value: CapitalRequestConfig }) {
  const request = { fixed_quantity: { label: "Shares requested", maximum: undefined, minimum: 1, step: 1, unit: "shares" }, mandate_fraction: { label: "Mandate capacity", maximum: 1, minimum: .01, step: .05, unit: "fraction" }, risk_fraction: { label: "Risk budget", maximum: 1, minimum: .01, step: .05, unit: "fraction" }, all_available: { label: "", maximum: undefined, minimum: 0, step: 1, unit: "" } }[value.mode];
  return <div className="guided-form-grid"><SelectField help="How the strategy expresses its request before Portfolio sizing." label="Request method" onChange={(mode) => onChange({ ...value, mode: mode as CapitalRequestConfig["mode"], value: mode === "fixed_quantity" ? 100 : mode === "all_available" ? 1 : .2 })} options={[{ label: "Fixed shares", value: "fixed_quantity" }, { label: "Fraction of mandate cash", value: "mandate_fraction" }, { label: "Fraction of risk budget", value: "risk_fraction" }, { label: "All remaining mandate capacity", value: "all_available" }]} value={value.mode} />{value.mode !== "all_available" ? <NumberField help="The requested amount before Portfolio approval." label={request.label} maximum={request.maximum} minimum={request.minimum} onChange={(requestValue) => onChange({ ...value, value: requestValue })} step={request.step} unit={request.unit} value={value.value} /> : <div className="guided-readonly-value"><span>Request amount</span><strong>All capacity still allowed by the mandate</strong></div>}<NumberField help="Higher-priority requests are evaluated first when capital is constrained; risk limits still win." label="Request priority" maximum={100} minimum={0} onChange={(priority) => onChange({ ...value, priority })} step={1} unit="0–100" value={value.priority} /><BooleanField help="Allow Portfolio to propose replacing a weaker position when policy permits it." label="Allow replacement proposal" onChange={(allow_replacement) => onChange({ ...value, allow_replacement })} value={value.allow_replacement} /></div>;
}

function GuidedOrderIntentFields({ draft, eligibleSessions, onChange, value }: { draft: Draft; eligibleSessions: string[]; onChange: (value: OrderIntentConfig) => void; value: OrderIntentConfig }) {
  return <><div className="guided-form-grid"><SelectField help="Broker-neutral execution behavior selected by Strategy and implemented by OMS." label="Execution policy" onChange={(execution_policy) => onChange({ ...value, execution_policy })} options={draft.oms.execution_policies.map((row) => ({ label: `${readableLabel(row.name)} · v${row.revision}`, value: row.policy_id }))} value={value.execution_policy} /><SelectField help="Independent broker-held stop, target, and trailing plan applied after a fill." label="Protection profile" onChange={(protection_profile) => onChange({ ...value, protection_profile })} options={draft.oms.protection_profiles.map((row) => ({ label: `${row.name} · v${row.revision}`, value: row.profile_id }))} value={value.protection_profile} /><SelectField help="What OMS does after the broker fills only part of the approved quantity." label="Partial fill" onChange={(partial_fill_policy) => onChange({ ...value, partial_fill_policy: partial_fill_policy as OrderIntentConfig["partial_fill_policy"] })} options={[{ label: "Finish the approved remainder", value: "complete_remainder" }, { label: "Accept the partial position", value: "accept_partial" }, { label: "Cancel the remainder", value: "cancel_remainder" }]} value={value.partial_fill_policy} /><NumberField help="Maximum time OMS may work this policy before its terminal behavior." label="Execution deadline" minimum={0} onChange={(deadline_ms) => onChange({ ...value, deadline_ms })} step={50} unit="ms" value={value.deadline_ms} /></div><p className="guided-inline-note"><ShieldCheck size={15} /> Eligible sessions: {eligibleSessions.map(readableLabel).join(", ") || "none"}. OMS derives compatible broker routing automatically.</p></>;
}

function GuidedExitRuleFields({ catalog, draft, eligibleSessions, onChange, value }: { catalog: StrategyInput[]; draft: Draft; eligibleSessions: string[]; onChange: (value: ExitRuleSet) => void; value: ExitRuleSet }) {
  return <div className="guided-composite-form"><div className="guided-form-grid"><NumberField help="Delay after confirmed entry before this exit may act." label="Active after" minimum={0} onChange={(active_after_ms) => onChange({ ...value, timing: { ...value.timing, active_after_ms } })} step={1000} unit="ms" value={value.timing.active_after_ms} /><NumberField help="Zero keeps the thesis active while the position remains open." label="Expires after" minimum={0} onChange={(expires_after_ms) => onChange({ ...value, timing: { ...value.timing, expires_after_ms } })} step={1000} unit="ms" value={value.timing.expires_after_ms} /><SelectField help="Close requests the full position; Reduce requests only the configured fraction." label="Position action" onChange={(action) => onChange({ ...value, action: action as ExitRuleSet["action"] })} options={[{ label: "Close the position", value: "close" }, { label: "Reduce the position", value: "reduce" }]} value={value.action} />{value.action === "reduce" ? <NumberField help="Fraction of the current position to release." label="Reduction fraction" maximum={1} minimum={.01} onChange={(position_fraction) => onChange({ ...value, position_fraction })} step={.05} unit="fraction" value={value.position_fraction} /> : null}</div><RuleStageEditor catalog={catalog} label={`${value.name} evidence`} onChange={(rules) => onChange({ ...value, rules })} stage={value.rules} /><GuidedOrderIntentFields draft={draft} eligibleSessions={eligibleSessions} onChange={(order_intent) => onChange({ ...value, order_intent })} value={value.order_intent} /></div>;
}

function GuidedCapabilityFields({ binding, definition, onChange }: { binding: CapabilityBinding; definition: CapabilityDefinition; onChange: (value: CapabilityBinding) => void }) {
  return <div className="guided-capability-fields"><BooleanField help="Disabled capabilities stay configured but cannot act." label="Enabled" onChange={(enabled) => onChange({ ...binding, enabled })} value={binding.enabled} />{binding.enabled ? <div className="guided-form-grid">{definition.parameters.map((parameter) => <CapabilityField definition={parameter} key={parameter.key} onChange={(value) => onChange({ ...binding, settings: { ...binding.settings, [parameter.key]: value } })} value={binding.settings[parameter.key]} />)}</div> : <p>The capability is retained with its current values but will not participate in the strategy lifecycle.</p>}</div>;
}

function strategySetupRows(profile: StrategyProfile) {
  return [
    { label: "Trading plan", value: profile.name },
    { label: "Behavior", value: `${readableLabel(profile.lifecycle.trading_behavior.side)} · ${profile.lifecycle.trading_behavior.eligible_sessions.map(readableLabel).join(", ")}` },
    { label: "Initial entry", value: `${readableLabel(profile.lifecycle.initial_entry.capital_request.mode)} · ${profile.lifecycle.initial_entry.opportunity.groups.length}/${profile.lifecycle.initial_entry.confirmation.groups.length}/${profile.lifecycle.initial_entry.blockers.groups.length} rule groups` },
    { label: "Position adds", value: `${profile.lifecycle.initial_entry.add_steps.filter((row) => row.enabled).length} enabled` },
    { label: "Reentry", value: profile.lifecycle.reentry.enabled ? `${profile.lifecycle.reentry.maximum_attempts} attempts · ${profile.lifecycle.reentry.cooldown_ms} ms` : "Disabled" },
    { label: "Strategic exits", value: `${profile.lifecycle.exit.rule_sets.filter((row) => row.enabled).length} enabled` },
    { label: "Capabilities", value: `${profile.capabilities.filter((row) => row.enabled).length} enabled` },
  ];
}

function GuidedQuestion({ children, description, label, status }: { children: ReactNode; description: string; label: string; status: string }) {
  return <section className="guided-question"><header><div><span>{label}</span><p>{description}</p></div><em data-state={status.toLowerCase().replaceAll(" ", "-")}>{status}</em></header><div>{children}</div></section>;
}

function DecisionOptions({ onChange, options, value }: { onChange: (value: string) => void; options: Array<{ detail: string; label: string; recommended?: boolean; value: string }>; value: string }) {
  const name = useId();
  return <div className="guided-decision-options">{options.map((option) => <label key={option.value}><input checked={value === option.value} name={name} onChange={() => onChange(option.value)} type="radio" /><span><span><strong>{option.label}</strong>{option.recommended ? <em>Recommended</em> : null}</span><small>{option.detail}</small></span></label>)}</div>;
}

function ModeChoices({ onChange, options, values }: { onChange: (values: string[]) => void; options: string[]; values: string[] }) {
  return <div className="guided-mode-choices">{options.map((option) => <label key={option}><input checked={values.includes(option)} onChange={(event) => onChange(event.target.checked ? [...values, option] : values.filter((value) => value !== option))} type="checkbox" /><span><Check size={13} />{readableLabel(option)}</span></label>)}</div>;
}

function GuidedFooter({ isFirst, isLast, next, onNext, onPrevious, previous }: { isFirst: boolean; isLast: boolean; next?: GuidedStep; onNext: () => void; onPrevious: () => void; previous?: GuidedStep }) {
  return <footer className="guided-navigation"><button className="button" disabled={isFirst && !previous} onClick={onPrevious} type="button"><ArrowLeft size={15} /> Previous</button><span>Your changes stay in this draft until you publish a release.</span><button className="button primary" disabled={isLast && !next} onClick={onNext} type="button">{isLast ? "Save and continue" : "Next question"} <ArrowRight size={15} /></button></footer>;
}

function GuidedReview({ approved, draft, label, onLabelChange, onPublish, onReturn, publishing, revisions }: { approved: Revision | null; draft: Draft; label: string; onLabelChange: (value: string) => void; onPublish: () => void; onReturn: () => void; publishing: boolean; revisions: Revision[] }) {
  const rows = reviewRows(draft, approved);
  return <div className="guided-review">
    <header><span>Final step</span><h2>Review the effective configuration</h2><p>Resolve anything marked invalid or needing a decision. Publication freezes the entire draft and configured Canvas for new runs.</p></header>
    <div className="guided-review-layout"><div className="guided-review-matrix">{rows.map((row) => { const Icon = row.icon; return <article key={row.step}><span><Icon size={18} /><strong>{row.label}</strong></span><span>{row.selection}</span><em data-state={row.state.toLowerCase().replaceAll(" ", "-")}>{row.state}</em><button onClick={() => navigateGuidedStep(row.step, () => undefined)} type="button">Change <ChevronRight size={13} /></button></article>; })}</div><aside><RevisionPublisher approved={approved} draft={draft} guided label={label} onLabelChange={onLabelChange} onPublish={onPublish} publishing={publishing} revisions={revisions} /></aside><details className="guided-technical-preview"><summary>Show the technical runtime preview <ChevronRight size={15} /></summary><EffectiveConfigurationPreview updatedAt={draft.updated_at || ""} /></details></div>
    <button className="button" onClick={onReturn} type="button"><ArrowLeft size={15} /> Back to accounts</button>
  </div>;
}

function GuidedEmpty({ onSwitchToExpert }: { onSwitchToExpert: () => void }) {
  return <div className="guided-empty"><TriangleAlert size={20} /><h2>This step needs a base object</h2><p>Create the missing profile, deployment, mandate, OMS profile, policy, protection profile, or account in Expert mode. Guided setup never invents a live-critical object.</p><button className="button primary" onClick={onSwitchToExpert} type="button"><Settings2 size={15} /> Open Expert editor</button></div>;
}

function StrategyStudio({ draft, onChange, onPersist, section }: { draft: Draft; onChange: (value: StrategySection) => void; onPersist: (value: StrategySection) => Promise<StrategySection>; section: StrategySection }) {
  const [selectedId, setSelectedId] = useState(section.profiles[0]?.profile_id ?? "");
  const [cloning, setCloning] = useState(false);
  const selected = section.profiles.find((row) => row.profile_id === selectedId) ?? section.profiles[0];
  const profileInUse = draft.assignments.deployments.some((row) => row.profile_id === selected?.profile_id);
  useEffect(() => {
    if (!section.profiles.some((row) => row.profile_id === selectedId)) setSelectedId(section.profiles[0]?.profile_id ?? "");
  }, [section.profiles, selectedId]);
  if (!selected) return <EmptyState title="No Strategy Profiles" detail="Create a profile from a registered strategy definition." />;

  function replaceProfile(next: StrategyProfile) {
    onChange({ ...section, profiles: section.profiles.map((row) => row.profile_id === selected.profile_id ? next : row) });
  }

  async function cloneProfile() {
    const id = uniqueId(`${selected.profile_id}-copy`, section.profiles.map((row) => row.profile_id));
    const next = { ...deepClone(selected), profile_id: id, name: `${selected.name} copy`, origin: "user" as const, protected: false, revision: 1 };
    setCloning(true);
    try {
      await onPersist({ ...section, profiles: [...section.profiles, next] });
      setSelectedId(id);
    } finally {
      setCloning(false);
    }
  }

  function createProfile(template?: StrategyProfile) {
    const source = template ?? section.profiles[0];
    const id = uniqueId("new-strategy-profile", section.profiles.map((row) => row.profile_id));
    const next = {
      ...deepClone(source),
      profile_id: id,
      name: template ? template.name : "New Strategy Profile",
      description: template ? template.description : "Describe when and how this configured strategy should trade.",
      origin: "user" as const,
      protected: false,
      revision: 1,
    };
    onChange({ ...section, profiles: [...section.profiles, next] });
    setSelectedId(id);
  }

  function removeProfile() {
    if (selected.protected || selected.profile_id === section.default_profile_id || profileInUse || section.profiles.length <= 1) return;
    const remaining = section.profiles.filter((row) => row.profile_id !== selected.profile_id);
    onChange({ ...section, profiles: remaining });
    setSelectedId(remaining[0]?.profile_id ?? "");
  }

  const advanced = flattenPrimitives(selected.parameters).filter((row) => (
    !LEGACY_ENTRY_LOGIC_PATHS.has(row.path)
  ));
  const entryRules = selected.lifecycle.initial_entry;
  return (
    <div className="configuration-workbench">
      <aside className="configuration-library">
        <header>
          <div><span>Strategy Profiles</span><strong>{section.profiles.length} configured</strong></div>
          <button aria-label="Create Strategy Profile" onClick={() => createProfile()} title="Create Strategy Profile" type="button"><Plus size={15} /></button>
        </header>
        <p>System profiles are safe starting points. They remain editable and can be cloned without changing the code definition.</p>
        <div>
          {section.profiles.map((profile) => (
            <button className={profile.profile_id === selected.profile_id ? "active" : ""} key={profile.profile_id} onClick={() => setSelectedId(profile.profile_id)} type="button">
              <span><strong>{profile.name}</strong><small>{profile.protected ? "Protected default" : profile.origin} · v{profile.revision}</small></span>
              <ChevronRight size={14} />
            </button>
          ))}
        </div>
        {section.profile_templates.length ? (
          <section className="configuration-template-picker">
            <span>System templates</span>
            {section.profile_templates.map((template) => (
              <button key={template.profile_id} onClick={() => createProfile(template)} type="button">
                <strong>{template.name}</strong><small>Create editable profile</small>
              </button>
            ))}
          </section>
        ) : null}
      </aside>

      <main className="configuration-detail">
        <section className="configuration-detail-heading">
          <div>
            <span>Configured Strategy Profile</span>
            <input aria-label="Strategy Profile name" onChange={(event) => replaceProfile({ ...selected, name: event.target.value })} value={selected.name} />
            <textarea aria-label="Strategy Profile summary" onChange={(event) => replaceProfile({ ...selected, description: event.target.value })} rows={2} value={selected.description} />
          </div>
          <div className="configuration-heading-actions">
            <button className="button compact" disabled={cloning} onClick={cloneProfile} type="button"><Clipboard size={14} /> {cloning ? "Cloning…" : "Clone"}</button>
            <button
              aria-label="Delete Strategy Profile"
              className="button compact danger"
              disabled={selected.protected || selected.profile_id === section.default_profile_id || profileInUse || section.profiles.length <= 1}
              onClick={removeProfile}
              title={selected.protected ? "The protected default profile cannot be removed" : profileInUse ? "Remove or change the referencing deployment first" : "Delete profile"}
              type="button"
            ><Trash2 size={14} /></button>
          </div>
        </section>

        <GuideCallout icon={<Sparkles size={17} />} title="Tune behavior first">
          A Strategy Profile defines reusable trading logic. Its deployment decides which universe it watches, who may act, and when the resulting ticker campaign stops.
        </GuideCallout>

        <LifecyclePanel
          defaultOpen
          eyebrow="General configuration"
          summary={`${readableLabel(selected.lifecycle.trading_behavior.side)} side · ${selected.lifecycle.trading_behavior.eligible_sessions.map(readableLabel).join(", ")}`}
          title="Trading Behavior"
        >
          <TradingBehaviorEditor
            definition={section.definitions.find((row) => row.strategy_id === selected.definition_id)}
            profile={selected}
            onChange={replaceProfile}
          />
        </LifecyclePanel>

        <LifecyclePanel
          defaultOpen
          eyebrow="Phase 1"
          summary={`${entryRules.opportunity.groups.length} opportunity · ${entryRules.confirmation.groups.length} confirmation · ${entryRules.blockers.groups.length} blocker rule sets`}
          title="Initial Entry"
        >
          <PhaseOrderEditor
            capitalRequest={selected.lifecycle.initial_entry.capital_request}
            eligibleSessions={selected.lifecycle.trading_behavior.eligible_sessions}
            orderIntent={selected.lifecycle.initial_entry.order_intent}
            title="Initial order request"
            executionPolicies={draft.oms.execution_policies}
            protectionProfiles={draft.oms.protection_profiles}
            onCapitalRequest={(capital_request) => replaceProfile({
              ...selected,
              lifecycle: {
                ...selected.lifecycle,
                initial_entry: { ...selected.lifecycle.initial_entry, capital_request },
              },
            })}
            onOrderIntent={(order_intent) => replaceProfile({
              ...selected,
              lifecycle: {
                ...selected.lifecycle,
                initial_entry: { ...selected.lifecycle.initial_entry, order_intent },
              },
            })}
          />
          <DecisionRulesEditor
            catalog={section.input_catalog}
            rules={entryRules}
            title="How the initial position is opened"
            summary="Opportunity passes, configured confirmation rule sets pass, and no blocker passes. The resulting order request is resolved by Portfolio and executed by OMS."
            onChange={(value) => replaceProfile({
              ...selected,
              lifecycle: {
                ...selected.lifecycle,
                initial_entry: { ...selected.lifecycle.initial_entry, ...value },
              },
            })}
          />
          <AddStepsEditor
            catalog={section.input_catalog}
            eligibleSessions={selected.lifecycle.trading_behavior.eligible_sessions}
            executionPolicies={draft.oms.execution_policies}
            protectionProfiles={draft.oms.protection_profiles}
            steps={selected.lifecycle.initial_entry.add_steps}
            onChange={(add_steps) => replaceProfile({
              ...selected,
              lifecycle: {
                ...selected.lifecycle,
                initial_entry: { ...selected.lifecycle.initial_entry, add_steps },
              },
            })}
          />
        </LifecyclePanel>

        <LifecyclePanel
          eyebrow="Phase 2"
          summary={selected.lifecycle.reentry.enabled ? `Up to ${selected.lifecycle.reentry.maximum_attempts} reentries · ${selected.lifecycle.reentry.cooldown_ms} ms cooldown` : "Reentry disabled"}
          title="Reentry"
        >
          <ReentryEditor catalog={section.input_catalog} draft={draft} profile={selected} onChange={replaceProfile} />
        </LifecyclePanel>

        <LifecyclePanel
          eyebrow="Phase 3"
          summary={`${selected.lifecycle.exit.rule_sets.filter((row) => row.enabled).length} active rule sets · OMS protection always active`}
          title="Exit"
        >
          <ExitRuleSetsEditor catalog={section.input_catalog} draft={draft} profile={selected} onChange={replaceProfile} />
        </LifecyclePanel>

        <LifecyclePanel
          eyebrow="Reusable functions"
          summary={`${selected.capabilities.filter((row) => row.enabled).length} enabled capabilities`}
          title="Capabilities"
        >
          <CapabilitiesEditor catalog={section.capability_catalog} profile={selected} onChange={replaceProfile} />
        </LifecyclePanel>

        <details className="configuration-advanced">
          <summary><span><strong>Advanced strategy parameters</strong><small>Less frequently changed signal, protection, and implementation inputs</small></span><ChevronRight size={15} /></summary>
          <div className="configuration-field-grid">
            {advanced.map((item) => (
              <ParameterField
                definition={field(item.path, readableLabel(item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))}
                key={item.path}
                value={item.value}
                onChange={(value) => replaceProfile({ ...selected, parameters: setPath(selected.parameters, item.path, value) })}
              />
            ))}
          </div>
        </details>
      </main>
    </div>
  );
}

function LifecyclePanel({ children, defaultOpen = false, eyebrow, summary, title }: {
  children: ReactNode;
  defaultOpen?: boolean;
  eyebrow: string;
  summary: string;
  title: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const marker = {
    "Trading Behavior": "TB",
    "Initial Entry": "01",
    "Reentry": "02",
    "Exit": "03",
    "Capabilities": "FX",
  }[title] ?? "•";
  return (
    <details className="strategy-lifecycle-panel" data-section={title.toLowerCase().replaceAll(" ", "-")} onToggle={(event) => setOpen(event.currentTarget.open)} open={open}>
      <summary>
        <span aria-hidden="true" className="strategy-lifecycle-index">{marker}</span>
        <span>
          <small>{eyebrow}</small>
          <strong>{title}</strong>
          <em>{summary}</em>
        </span>
        <ChevronDown aria-hidden="true" size={18} />
      </summary>
      <div className="strategy-lifecycle-content">{children}</div>
    </details>
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
      <p className="configuration-section-guide">These settings describe the strategy itself. Account autonomy, concrete cash allocation, and broker execution remain in Deployment, Portfolio, and OMS.</p>
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
        <SelectField
          help="Causal event that causes this strategy to re-evaluate its current ticker campaigns."
          label="Evaluation trigger"
          onChange={(evaluation_trigger) => update({ ...behavior, evaluation_trigger })}
          options={["indicator_update", "signal_event", "bar_close"].map((value) => ({ label: readableLabel(value), value }))}
          value={behavior.evaluation_trigger}
        />
        <BooleanField
          help="Allow a manually opened position to be adopted by a campaign using this Strategy Profile."
          label="Adopt manual positions"
          onChange={(adopt_manual_positions) => update({ ...behavior, adopt_manual_positions })}
          value={behavior.adopt_manual_positions}
        />
      </div>
      <fieldset className="configuration-choice-set">
        <legend>Eligible sessions</legend>
        <p>The strategy evaluates entries only during selected sessions. Protective exits remain active whenever a position exists.</p>
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
      </fieldset>
      <p className="configuration-safety-note"><PencilLine size={15} /> Side changes campaign ownership and order direction. Review every directional rule after changing it; the editor never silently reverses trading logic.</p>
    </>
  );
}

function ReentryEditor({ catalog, draft, onChange, profile }: {
  catalog: StrategyInput[];
  draft: Draft;
  onChange: (value: StrategyProfile) => void;
  profile: StrategyProfile;
}) {
  const reentry = profile.lifecycle.reentry;
  const update = (next: StrategyLifecycle["reentry"]) => onChange({
    ...profile,
    lifecycle: { ...profile.lifecycle, reentry: next },
  });
  return (
    <>
      <PhaseOrderEditor
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
        <BooleanField help="Permit another flat-to-open transition within the same ticker campaign." label="Enable reentry" onChange={(enabled) => update({ ...reentry, enabled })} value={reentry.enabled} />
        <BooleanField help="Evidence used for the previous entry cannot be reused without a newer causal update." label="Require new confirmation" onChange={(require_new_confirmation) => update({ ...reentry, require_new_confirmation })} value={reentry.require_new_confirmation} />
        <NumberField help="Minimum time after a confirmed full exit before reentry becomes eligible." label="Cooldown" minimum={0} onChange={(cooldown_ms) => update({ ...reentry, cooldown_ms })} step={100} unit="ms" value={reentry.cooldown_ms} />
        <NumberField help="Maximum reentries during one ticker campaign. Zero allows only the initial entry." label="Maximum attempts" minimum={0} onChange={(maximum_attempts) => update({ ...reentry, maximum_attempts })} step={1} unit="entries" value={reentry.maximum_attempts} />
      </div>
      <DecisionRulesEditor
        catalog={catalog}
        importRules={profile.lifecycle.initial_entry}
        onChange={(rules) => update({ ...reentry, rules })}
        rules={reentry.rules}
        title="When a reentry becomes eligible"
        summary="Reentry owns an independent rule set. Import selected initial-entry groups as editable copies, then add reentry-only evidence as needed."
      />
    </>
  );
}

function ExitRuleSetsEditor({ catalog, draft, onChange, profile }: {
  catalog: StrategyInput[];
  draft: Draft;
  onChange: (value: StrategyProfile) => void;
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
    const source = catalog[0];
    onChange({
      ...profile,
      lifecycle: {
        ...profile.lifecycle,
        exit: {
          rule_sets: [{
            action: "close",
            enabled: true,
            name: "New exit rule set",
            order_intent: { deadline_ms: 750, execution_policy: "adaptive_urgent", partial_fill_policy: "complete_remainder", protection_profile: "hybrid-single" },
            position_fraction: 1,
            rule_set_id: ruleSetId,
            rules: {
              operator: "all",
              groups: [{
                conditions: [{ comparator: source.value_type === "boolean" ? "is_true" : "greater_or_equal", condition_id: `${ruleSetId}-condition`, enabled: true, left_source_id: source.source_id, left_timeframe: source.timeframes[0], right_source_id: "", right_timeframe: "", value: source.value_type === "boolean" ? null : 0 }],
                enabled: true,
                group_id: `${ruleSetId}-group`,
                label: "Exit evidence",
                operator: "all",
                required_score: 1,
              }],
            },
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
      <div className="configuration-protection-authority">
        <ShieldCheck size={19} />
        <div>
          <span>OMS safety authority</span>
          <strong>Protective stop is independent of strategic exit rules</strong>
          <p>OMS calculates, submits, repairs, and reconciles broker-held protection. Strategy rules cannot disable or delay it. {protectionProfile ? `${protectionProfile.name} protects ${protectionProfile.slices.length} independent slice${protectionProfile.slices.length === 1 ? "" : "s"}, applies ${readableLabel(protectionProfile.add_policy)} to adds, and uses ${readableLabel(protectionProfile.profit_pocket_transition)} after profit fills.` : "Select an OMS and protection profile in Deployment to resolve the exact plan."}</p>
          <a href="#oms-configuration">Configure OMS protection</a>
        </div>
      </div>
      <div className="strategy-exit-heading"><p className="configuration-section-guide">Exit uses the same source-aware rule-set model as Entry and Reentry. Rule sets are evaluated from top to bottom; each owns its validity window, position action, and OMS order request.</p><button className="button compact" onClick={addRuleSet} type="button"><Plus size={14} /> Add rule set</button></div>
      {routes.map((ruleSet) => (
        <details className="strategy-exit-route" data-enabled={ruleSet.enabled ? "true" : "false"} key={ruleSet.rule_set_id}>
          <summary>
            <div><span>Strategic exit · {ruleSet.action}</span><strong>{ruleSet.name}</strong><p>{ruleSet.summary}</p></div>
            <label className="configuration-switch" onClick={(event) => event.stopPropagation()} title="Enable exit rule set"><input checked={ruleSet.enabled} onChange={(event) => replace(ruleSet.rule_set_id, { ...ruleSet, enabled: event.target.checked })} type="checkbox" /><span /></label>
            <ChevronDown size={17} />
          </summary>
          <div className="strategy-exit-route-body">
            <div className="strategy-exit-rule-meta"><label className="strategy-rule-name"><span>Rule set name</span><input onChange={(event) => replace(ruleSet.rule_set_id, { ...ruleSet, name: event.target.value })} value={ruleSet.name} /></label><label><span>Purpose</span><input onChange={(event) => replace(ruleSet.rule_set_id, { ...ruleSet, summary: event.target.value })} value={ruleSet.summary} /></label><button aria-label={`Delete ${ruleSet.name}`} className="button compact danger" disabled={routes.length <= 1} onClick={() => onChange({ ...profile, lifecycle: { ...profile.lifecycle, exit: { rule_sets: routes.filter((row) => row.rule_set_id !== ruleSet.rule_set_id) } } })} type="button"><Trash2 size={14} /></button></div>
            <RuleStageEditor catalog={catalog} label={`${ruleSet.name} evidence`} onChange={(rules) => replace(ruleSet.rule_set_id, { ...ruleSet, rules })} stage={ruleSet.rules} />
            <div className="configuration-field-grid">
              <NumberField help={{ role: "Delay from confirmed entry until this rule set becomes eligible.", values: { "0 ms": "Active immediately.", "Positive value": "Matching evidence is ignored until this delay passes." } }} label="Active after" minimum={0} onChange={(active_after_ms) => replace(ruleSet.rule_set_id, { ...ruleSet, timing: { ...ruleSet.timing, active_after_ms } })} step={1000} unit="ms" value={ruleSet.timing.active_after_ms} />
              <NumberField help={{ role: "Maximum age of this exit thesis from confirmed entry.", values: { "0 ms": "Never expires while the position is open.", "Positive value": "Stops evaluating after this duration." }, note: "For a failed-breakout thesis, 60,000 ms means losing the reference after one minute no longer counts as the original failed entry." }} label="Expires after" minimum={0} onChange={(expires_after_ms) => replace(ruleSet.rule_set_id, { ...ruleSet, timing: { ...ruleSet.timing, expires_after_ms } })} step={1000} unit="ms" value={ruleSet.timing.expires_after_ms} />
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

const COMPARATOR_OPTIONS = [
  { label: "Is above by", value: "above_by_bps" },
  { label: "Is at least", value: "greater_or_equal" },
  { label: "Is greater than", value: "greater_than" },
  { label: "Is at most", value: "less_or_equal" },
  { label: "Is less than", value: "less_than" },
  { label: "Equals", value: "equals" },
  { label: "Is true", value: "is_true" },
];

function DecisionRulesEditor({ catalog, importRules, onChange, rules, summary, title }: {
  catalog: StrategyInput[];
  importRules?: EntryRules;
  onChange: (value: EntryRules) => void;
  rules: EntryRules;
  summary: string;
  title: string;
}) {
  const [openedGroupIds, setOpenedGroupIds] = useState<Set<string>>(new Set());
  if (!rules) return <EmptyState title="Decision rules unavailable" detail="Reload the migrated configuration draft to receive the typed source model." />;

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

  return (
    <div className="strategy-rule-editor">
      <div className="strategy-source-legend">
        <GitBranch size={18} />
        <div>
          <strong>{title}</strong>
          <p>{summary}</p>
        </div>
      </div>
      {(Object.keys(RULE_STAGE_META) as Array<keyof EntryRules>).map((stageName) => {
        const stage = rules[stageName];
        const meta = RULE_STAGE_META[stageName];
        return (
          <details className="strategy-rule-stage" data-stage={stageName} key={stageName}>
            <summary><div><span>{stageName}</span><strong>{meta.label}</strong><p>{meta.summary}</p></div><span>{stage.groups.length} rule sets</span><ChevronDown size={16} /></summary>
            <div className="strategy-rule-stage-body">
            <header>
              <div><span>{stageName}</span><strong>{meta.label}</strong><p>{meta.summary}</p></div>
              <div className="strategy-stage-controls">
                  <SelectField
                    help={{ role: "Combines the enabled rule sets in this phase group.", values: { "Any rule set": "The phase group passes when one enabled rule set passes.", "All rule sets": "Every enabled rule set must pass." } }}
                    label="Stage logic"
                    onChange={(operator) => replaceStage(stageName, { ...stage, operator: operator as "all" | "any" })}
                    options={[{ label: "Any rule set", value: "any" }, { label: "All rule sets", value: "all" }]}
                    value={stage.operator}
                  />
                <button className="button compact" onClick={() => addGroup(stageName)} type="button"><Plus size={14} /> Add rule set</button>
                {importRules?.[stageName]?.groups?.length ? (
                  <button className="button compact secondary" onClick={() => importStage(stageName)} type="button"><FileInput size={14} /> Add initial rules</button>
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
                  onChange={(next) => replaceGroup(stageName, group.group_id, next)}
                  onRemove={() => replaceStage(stageName, { ...stage, groups: stage.groups.filter((row) => row.group_id !== group.group_id) })}
                  removable={stage.groups.length > 1}
                />
              ))}
            </div>
            </div>
          </details>
        );
      })}
    </div>
  );
}

function RuleGroupEditor({ catalog, defaultOpen = false, group, onChange, onRemove, removable }: {
  catalog: StrategyInput[];
  defaultOpen?: boolean;
  group: RuleGroup;
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
      <header className="strategy-rule-toolbar">
        <div className="strategy-rule-toolbar-heading"><span>Rule set controls</span><p>Name the evidence bundle, choose how its conditions combine, and decide whether it participates in evaluation.</p></div>
        <div className="strategy-rule-toolbar-fields">
          <label className="strategy-rule-name"><span>Rule set name</span><input onChange={(event) => onChange({ ...group, label: event.target.value })} value={group.label} /></label>
          <label><span>Condition logic <FieldHelp title="Condition logic" content={{ role: "Defines how this rule set converts its enabled conditions into one pass or fail result.", values: { "All must pass": "Every enabled condition must be true.", "Any may pass": "One enabled condition is enough.", "Required score": "The fraction of enabled conditions that pass must meet this rule set's own score." }, note: "The score is local to this rule set. There is no global confirmation score." }} /></span><select onChange={(event) => onChange({ ...group, operator: event.target.value as RuleGroup["operator"] })} value={group.operator}><option value="all">All must pass</option><option value="any">Any may pass</option><option value="score">Required score</option></select></label>
          {group.operator === "score" ? <label><span>Required score <FieldHelp title="Required score" content={{ role: "Minimum fraction of this rule set's enabled conditions that must pass.", values: { "1.0": "Every condition must pass.", "0.75": "At least three quarters must pass.", "0.5": "At least half must pass." }, note: "This value belongs only to this rule set; changing it does not affect any other confirmation or phase." }} /></span><input max={1} min={0.01} onChange={(event) => onChange({ ...group, required_score: Number(event.target.value) })} step={0.05} type="number" value={group.required_score} /></label> : null}
          <div className="strategy-rule-toolbar-actions">
            <label className="strategy-rule-enabled"><span><strong>{group.enabled ? "Enabled" : "Disabled"}</strong><small>{group.enabled ? "Included in evaluation" : "Ignored by runtime"}</small></span><span className="configuration-switch"><input checked={group.enabled} onChange={(event) => onChange({ ...group, enabled: event.target.checked })} type="checkbox" /><span /></span></label>
            <button aria-label={`Delete ${group.label}`} className="button compact danger" disabled={!removable} onClick={onRemove} type="button"><Trash2 size={14} /> Delete</button>
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

function RuleStageEditor({ catalog, label, onChange, stage }: {
  catalog: StrategyInput[];
  label: string;
  onChange: (value: RuleStage) => void;
  stage: RuleStage;
}) {
  const [openedId, setOpenedId] = useState("");
  function addGroup() {
    const groupId = uniqueId("new-rule", stage.groups.map((row) => row.group_id));
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
      }, ...stage.groups],
    });
    setOpenedId(groupId);
  }
  return (
    <section className="strategy-rule-stage compact" data-stage="exit">
      <header>
        <div><span>Exit evidence</span><strong>{label}</strong><p>These rule sets are evaluated before the route may emit its configured exit order request.</p></div>
        <div className="strategy-stage-controls">
          <SelectField
            help={{ role: "Combines this route's rule sets.", values: { "Any rule set": "The route passes when at least one enabled rule set passes.", "All rule sets": "Every enabled rule set must pass." } }}
            label="Route logic"
            onChange={(operator) => onChange({ ...stage, operator: operator as "all" | "any" })}
            options={[{ label: "Any rule set", value: "any" }, { label: "All rule sets", value: "all" }]}
            value={stage.operator}
          />
          <button className="button compact" onClick={addGroup} type="button"><Plus size={14} /> Add rule set</button>
        </div>
      </header>
      <div className="strategy-rule-groups">
        {stage.groups.map((group) => (
          <RuleGroupEditor
            catalog={catalog}
            defaultOpen={group.group_id === openedId}
            group={group}
            key={group.group_id}
            onChange={(next) => onChange({ ...stage, groups: stage.groups.map((row) => row.group_id === group.group_id ? next : row) })}
            onRemove={() => onChange({ ...stage, groups: stage.groups.filter((row) => row.group_id !== group.group_id) })}
            removable={stage.groups.length > 1}
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
        <div><span>Step 1 · Portfolio</span><strong>Capital request</strong><p>Ask for capital in relative terms. Portfolio applies the deployment mandate, buying power, current positions, risk limits, and competing requests before approving shares.</p></div>
      </header>
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
            "Request priority": "Portfolio uses 0–100 when several requests compete; risk and mandate limits remain authoritative.",
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
        <NumberField help={{ role: "Ranks this request when several strategies compete for limited account capacity.", values: { "Higher value": "Portfolio evaluates the request earlier.", "Lower value": "The request yields to stronger opportunities." }, note: "Priority never overrides cash, mandate, concentration, or risk controls." }} label="Request priority" maximum={100} minimum={0} onChange={(priority) => onChange({ ...value, priority })} step={1} unit="0–100" value={value.priority} />
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
        value={value.execution_policy}
      />
      <SelectField help="Selects the independently versioned stop, target, and trailing plan used for a filled entry or add." label="Protection profile" onChange={(protection_profile) => onChange({ ...value, protection_profile })} options={protectionProfiles.map((profile) => ({ label: `${profile.name} · v${profile.revision}`, value: profile.profile_id }))} value={value.protection_profile} />
      <SelectField help={{ role: "Determines how OMS handles an incomplete fill.", values: { "Complete remainder": "Continue working the unfilled quantity under the selected policy.", "Accept partial": "Keep the fill received and stop requesting the remainder.", "Cancel remainder": "Cancel any remainder after the first partial fill." } }} label="Partial fill" onChange={(partial_fill_policy) => onChange({ ...value, partial_fill_policy: partial_fill_policy as OrderIntentConfig["partial_fill_policy"] })} options={["complete_remainder", "accept_partial", "cancel_remainder"].map((item) => ({ label: readableLabel(item), value: item }))} value={value.partial_fill_policy} />
      <NumberField help="Maximum time OMS may work this execution policy before its terminal policy is applied. Zero means the policy's immediate behavior." label="Execution deadline" minimum={0} onChange={(deadline_ms) => onChange({ ...value, deadline_ms })} step={50} unit="ms" value={value.deadline_ms} />
      </div>
      <div className="strategy-smart-session">
        <ShieldCheck size={17} />
        <div><span>Smart session routing</span><strong>{eligibleSessions.map(readableLabel).join(", ") || "No eligible session selected"}</strong><p>{usesExtendedHours ? "OMS enables eligible extended-session routing and selects compatible broker instructions after account, venue, and order-type checks." : "OMS keeps the request in the regular session and chooses compatible broker instructions automatically."}</p></div>
        <FieldHelp content={{ role: "Session routing is derived from Trading Behavior so entry, reentry, and exit requests cannot contradict the strategy's eligible sessions.", parameters: { "Eligible sessions": "Selected once in Trading Behavior.", "Time in force": "Chosen by OMS for the broker, venue, session, and execution method.", "Outside regular hours": "Enabled by OMS only when premarket or after-hours is selected and the broker path supports it." }, note: "Change session eligibility in Trading Behavior. Strategy phases intentionally do not expose raw time-in-force or outside-hours switches." }} />
      </div>
    </article>
  );
}

function AddStepsEditor({ catalog, eligibleSessions, executionPolicies, onChange, protectionProfiles, steps }: {
  catalog: StrategyInput[];
  eligibleSessions: string[];
  executionPolicies: ExecutionPolicyConfig[];
  onChange: (value: AddStep[]) => void;
  protectionProfiles: ProtectionProfileConfig[];
  steps: AddStep[];
}) {
  function addStep() {
    const stepId = uniqueId("position-add", steps.map((row) => row.step_id));
    const source = catalog[0];
    onChange([{
      capital_request: { allow_replacement: false, mode: "mandate_fraction", priority: 50, value: 0.1 },
      enabled: true,
      maximum_uses: 1,
      name: "New position add",
      order_intent: { deadline_ms: 750, execution_policy: "adaptive_urgent", partial_fill_policy: "complete_remainder", protection_profile: "hybrid-single" },
      rules: {
        groups: [{
          conditions: [{ comparator: source.value_type === "boolean" ? "is_true" : "greater_or_equal", condition_id: `${stepId}-condition`, enabled: true, left_source_id: source.source_id, left_timeframe: source.timeframes[0], right_source_id: "", right_timeframe: "", value: source.value_type === "boolean" ? null : 0 }],
          enabled: true, group_id: `${stepId}-rule`, label: "Add trigger", operator: "all", required_score: 1,
        }],
        operator: "any",
      },
      step_id: stepId,
    }, ...steps]);
  }
  return (
    <section className="strategy-add-plan">
      <header><div><span>Position construction</span><strong>Conditional add requests</strong><p>Each add owns its evidence, relative capital request, order policy, and usage limit. Newly added steps appear first.</p></div><button className="button compact" onClick={addStep} type="button"><Plus size={14} /> Add position step</button></header>
      <div>
        {steps.map((step) => (
          <details className="strategy-add-step" key={step.step_id}>
            <summary><span className="strategy-rule-state" /><div><strong>{step.name}</strong><small>{readableLabel(step.capital_request.mode)} · {step.maximum_uses} maximum uses</small></div><ChevronDown size={16} /></summary>
            <div className="strategy-add-step-body">
              <div className="configuration-field-grid">
                <TextField help="Operator-facing name for this ordered position-building step." label="Step name" onChange={(name) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, name } : row))} value={step.name} />
                <NumberField help="Maximum successful executions of this add step during one campaign." label="Maximum uses" minimum={1} onChange={(maximum_uses) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, maximum_uses } : row))} step={1} unit="fills" value={step.maximum_uses} />
                <BooleanField help="Disabled steps remain configured but cannot emit a capital request." label="Enabled" onChange={(enabled) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, enabled } : row))} value={step.enabled} />
                <button className="button compact danger" onClick={() => onChange(steps.filter((row) => row.step_id !== step.step_id))} type="button"><Trash2 size={14} /> Remove step</button>
              </div>
              <RuleStageEditor catalog={catalog} label={`${step.name} rules`} onChange={(rules) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, rules } : row))} stage={step.rules} />
              <PhaseOrderEditor capitalRequest={step.capital_request} eligibleSessions={eligibleSessions} executionPolicies={executionPolicies} protectionProfiles={protectionProfiles} orderIntent={step.order_intent} title={`${step.name} request`} onCapitalRequest={(capital_request) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, capital_request } : row))} onOrderIntent={(order_intent) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, order_intent } : row))} />
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}

function DeploymentEditor({ draft, onChange }: { draft: Draft; onChange: (value: AssignmentSection) => void }) {
  const section = draft.assignments;
  const [selectedId, setSelectedId] = useState(section.deployments[0]?.deployment_id ?? "");
  const selected = section.deployments.find((row) => row.deployment_id === selectedId) ?? section.deployments[0];
  if (!selected) return <EmptyState title="No deployments" detail="Create a deployment to connect a Strategy Profile to accounts, Portfolio mandates, and OMS." />;
  const linkedMandates = draft.portfolio.mandates.filter((row) => row.deployment_id === selected.deployment_id);
  const readiness = [
    { label: "Watch Universe selected", ready: section.universes.some((row) => row.universe_id === selected.universe_id) },
    { label: "Strategy Profile selected", ready: draft.strategy.profiles.some((row) => row.profile_id === selected.profile_id) },
    { label: "OMS profile selected", ready: draft.oms.profiles.some((row) => row.profile_id === selected.oms_profile_id) },
    { label: "Account mandate configured", ready: linkedMandates.length > 0 },
    { label: "Replay enabled", ready: selected.modes.includes("replay") },
  ];

  function replace(next: Deployment) {
    onChange({ ...section, deployments: section.deployments.map((row) => row.deployment_id === selected.deployment_id ? next : row) });
  }

  function createDeployment() {
    const id = uniqueId("new-deployment", section.deployments.map((row) => row.deployment_id));
    const next: Deployment = {
      deployment_id: id,
      name: "New deployment",
      description: "Connect this deployment to account mandates before publishing.",
      profile_id: draft.strategy.profiles[0]?.profile_id ?? "",
      oms_profile_id: draft.oms.profiles[0]?.profile_id ?? "",
      universe_id: section.universes[0]?.universe_id ?? "",
      book_id: "default",
      selection_priority: 50,
      campaign_policy: {
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
      modes: ["replay"],
      runtime_assignments: [],
    };
    onChange({ ...section, deployments: [...section.deployments, next] });
    setSelectedId(id);
  }

  return (
    <div className="configuration-workbench">
      <aside className="configuration-library">
        <header><div><span>Deployments</span><strong>{section.deployments.length} configured</strong></div><button onClick={createDeployment} title="Create deployment" type="button"><Plus size={15} /></button></header>
        <p>A deployment is the usable unit selected by Replay, Backtest, Live, or Order Entry.</p>
        <div>{section.deployments.map((row) => <button className={row.deployment_id === selected.deployment_id ? "active" : ""} key={row.deployment_id} onClick={() => setSelectedId(row.deployment_id)} type="button"><span><strong>{row.name}</strong><small>{row.enabled ? "Enabled" : "Disabled"} · {row.modes.join(", ")}</small></span><ChevronRight size={14} /></button>)}</div>
      </aside>
      <main className="configuration-detail">
        <section className="configuration-detail-heading">
          <div><span>Usable Strategy Deployment</span><input aria-label="Deployment name" onChange={(event) => replace({ ...selected, name: event.target.value })} value={selected.name} /><textarea aria-label="Deployment summary" onChange={(event) => replace({ ...selected, description: event.target.value })} rows={2} value={selected.description} /></div>
          <label className="configuration-enabled"><input checked={selected.enabled} onChange={(event) => replace({ ...selected, enabled: event.target.checked })} type="checkbox" /> Enabled</label>
        </section>
        <GuideCallout icon={<Network size={17} />} title="Profile → Deployment → Strategy Campaign">
          The profile defines decisions. This deployment selects a Watch Universe and campaign authority. The shared Strategy Orchestrator grants one exclusive active campaign per ticker before Portfolio and OMS may act.
        </GuideCallout>
        <ConfigGroup summary="Select which approved stock universe this strategy may evaluate. Several strategies may observe a stock, but only one active campaign may own it." title="1. Watch Universe">
          <div className="configuration-field-grid">
            <SelectField help="Configured source of eligible symbols for this deployment." label="Universe" onChange={(universe_id) => replace({ ...selected, universe_id })} options={section.universes.map((row) => ({ label: row.name, value: row.universe_id }))} value={selected.universe_id} />
            <SelectField help="Ticker ownership is exclusive inside one portfolio book and runtime mode." label="Portfolio book" onChange={(book_id) => replace({ ...selected, book_id })} options={[{ label: "Default book", value: "default" }]} value={selected.book_id} />
            <NumberField help="Higher values win deterministic selection when multiple eligible deployments request the same unowned ticker." label="Selection priority" maximum={100} minimum={0} onChange={(selection_priority) => replace({ ...selected, selection_priority })} step={1} unit="0–100" value={selected.selection_priority} />
          </div>
          <WatchUniverseEditor section={section} onChange={onChange} selectedId={selected.universe_id} />
        </ConfigGroup>
        <div className="configuration-two-column">
          <ConfigGroup summary="Select the configured behavior and shared execution profile." title="2. Strategy and execution">
            <div className="configuration-field-grid one-column">
              <SelectField help="Published Strategy Profile whose decision behavior this deployment runs." label="Strategy Profile" onChange={(value) => replace({ ...selected, profile_id: value })} options={draft.strategy.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={selected.profile_id} />
              <SelectField help="Reusable shared OMS and protection profile used to execute approved requests." label="OMS profile" onChange={(value) => replace({ ...selected, oms_profile_id: value })} options={draft.oms.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={selected.oms_profile_id} />
            </div>
          </ConfigGroup>
          <ConfigGroup summary="A release cannot run until its references and account mandates are complete." title="Readiness">
            <div className="configuration-readiness">{readiness.map((item) => <span data-ready={item.ready ? "true" : "false"} key={item.label}>{item.ready ? <CheckCircle2 size={14} /> : <TriangleAlert size={14} />}{item.label}</span>)}</div>
          </ConfigGroup>
        </div>
        <ConfigGroup summary="Initial entry, reentry, and normal exit authority are independent. Protective exits always remain automatic." title="3. Campaign lifecycle authority">
          <CampaignPolicyEditor deployment={selected} onChange={replace} />
        </ConfigGroup>
        <ConfigGroup summary="Choose where this deployment may run. Live and Paper still require their independent operational gates." title="4. Runtime modes">
          <ModeSelector modes={selected.modes} onChange={(modes) => replace({ ...selected, modes })} />
        </ConfigGroup>
        <ConfigGroup summary="Capital authority is configured on Portfolio & Risk. This page shows the linked account mandates." title="5. Account mandates">
          <div className="deployment-mandates">
            {linkedMandates.map((mandate) => <article key={mandate.mandate_id}><strong>{accountName(draft.accounts, mandate.account_key)}</strong><span>{percent(mandate.maximum_cash_fraction)} cash ceiling · {mandate.autonomy} · priority {mandate.priority}</span></article>)}
            {!linkedMandates.length ? <EmptyState title="No account mandate" detail="Open Portfolio & Risk and add an account mandate for this deployment." /> : null}
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
  if (!universe) return <EmptyState title="Universe unavailable" detail="Select or create a Watch Universe before publishing this deployment." />;
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
        <p className="configuration-safety-note"><TriangleAlert size={15} /> Draft-only source: connect and validate the {readableLabel(universe.source)} membership resolver before this release can be published.</p>
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
  deployment: Deployment;
  onChange: (value: Deployment) => void;
}) {
  const policy = deployment.campaign_policy;
  const replace = (campaign_policy: Deployment["campaign_policy"]) => onChange({ ...deployment, campaign_policy });
  const authorities = ["disabled", "manual", "confirm", "automatic"].map((value) => ({ label: readableLabel(value), value }));
  return (
    <>
      <div className="campaign-authority-flow" aria-label="Campaign lifecycle authority">
        <span><strong>1</strong> Initial entry</span><ChevronRight size={15} />
        <span><strong>2</strong> Reentry</span><ChevronRight size={15} />
        <span><strong>3</strong> Exit</span><ChevronRight size={15} />
        <span><strong>✓</strong> Stop or keep watching</span>
      </div>
      <div className="configuration-field-grid">
        <SelectField help="Who may authorize the first flat-to-open transition in a ticker campaign." label="Initial-entry authority" onChange={(initial_entry_authority) => replace({ ...policy, initial_entry_authority })} options={authorities.filter((row) => row.value !== "disabled")} value={policy.initial_entry_authority} />
        <SelectField help="Who may authorize a later entry after a confirmed full exit." label="Reentry authority" onChange={(reentry_authority) => replace({ ...policy, reentry_authority })} options={authorities} value={policy.reentry_authority} />
        <SelectField help="Who may authorize normal strategic exits. Protective exits remain automatic." label="Exit authority" onChange={(exit_authority) => replace({ ...policy, exit_authority })} options={authorities.filter((row) => row.value !== "disabled")} value={policy.exit_authority} />
        <SelectField help="What happens to the ticker campaign at the configured session boundary." label="Session-end behavior" onChange={(session_end_behavior) => replace({ ...policy, session_end_behavior })} options={["keep_watching", "stop_when_flat", "exit_and_stop"].map((value) => ({ label: readableLabel(value), value }))} value={policy.session_end_behavior} />
        <NumberField help="Operational ceiling applied even if the Strategy Profile permits more reentries." label="Campaign reentry ceiling" minimum={0} onChange={(maximum_reentries) => replace({ ...policy, maximum_reentries })} step={1} unit="entries" value={policy.maximum_reentries} />
        <NumberField help="Operational cooldown applied before the Strategy Profile may evaluate another entry." label="Campaign cooldown" minimum={0} onChange={(reentry_cooldown_ms) => replace({ ...policy, reentry_cooldown_ms })} step={100} unit="ms" value={policy.reentry_cooldown_ms} />
        <NumberField help="Maximum time to retain a newly armed ticker while waiting for its initial entry. Zero means no time limit." label="Initial watch limit" minimum={0} onChange={(maximum_initial_watch_ms) => replace({ ...policy, maximum_initial_watch_ms })} step={60000} unit="ms" value={policy.maximum_initial_watch_ms} />
        <BooleanField help="A paused campaign keeps exclusive ticker ownership. Releasing it requires a separate safe handoff while flat." label="Retain ticker while paused" onChange={(retain_ticker_while_paused) => replace({ ...policy, retain_ticker_while_paused })} value={policy.retain_ticker_while_paused} />
      </div>
      <div className="configuration-safety-note"><ShieldCheck size={15} /><span><strong>Protective exits: Automatic</strong> — this authority is fixed and cannot be weakened by a strategy or deployment.</span></div>
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
    const mandateId = uniqueId(`${deployment.deployment_id}-${account.account_key}`, section.mandates.map((row) => row.mandate_id));
    const mandate: Mandate = {
      mandate_id: mandateId,
      deployment_id: deployment.deployment_id,
      account_key: account.account_key,
      enabled: true,
      maximum_cash_fraction: 0.3,
      maximum_planned_risk_fraction: 0.01,
      maximum_positions: 10,
      priority: 50,
      autonomy: "confirm",
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
        summary="Many-to-many account rules: the same deployment may receive different capital, risk, priority, and autonomy on each account."
        title="Strategy-account mandates"
      >
        <div className="mandate-grid">
          {section.mandates.map((mandate) => (
            <article className="mandate-card" key={mandate.mandate_id}>
              <header>
                <div><strong>{deploymentName(draft.assignments, mandate.deployment_id)}</strong><span>{accountName(draft.accounts, mandate.account_key)}</span></div>
                <button aria-label="Delete mandate" onClick={() => onChange({ ...section, mandates: section.mandates.filter((row) => row.mandate_id !== mandate.mandate_id) })} title="Delete mandate" type="button"><Trash2 size={14} /></button>
              </header>
              <div className="configuration-field-grid one-column">
                <SelectField help="Deployment allowed to request capital." label="Deployment" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, deployment_id: value })} options={draft.assignments.deployments.map((row) => ({ label: row.name, value: row.deployment_id }))} value={mandate.deployment_id} />
                <SelectField help="Account whose cash, positions, and risk state govern the request." label="Account" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, account_key: value })} options={draft.accounts.bindings.map((row) => ({ label: row.name, value: row.account_key }))} value={mandate.account_key} />
                <NumberField help="Maximum account cash this strategy deployment may use." label="Maximum cash" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, maximum_cash_fraction: value })} step={0.05} unit="fraction" value={mandate.maximum_cash_fraction} />
                <NumberField help="Maximum planned loss admitted for one request under this mandate." label="Planned risk" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, maximum_planned_risk_fraction: value })} step={0.001} unit="fraction" value={mandate.maximum_planned_risk_fraction} />
                <NumberField help="Higher values are considered first when capital requests compete." label="Priority" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, priority: value })} step={1} unit="0–100" value={mandate.priority} />
                <SelectField help="Whether actions execute automatically or require operator involvement." label="Autonomy" onChange={(value) => replaceMandate(mandate.mandate_id, { ...mandate, autonomy: value as Mandate["autonomy"] })} options={["manual", "confirm", "automatic"].map((value) => ({ label: readableLabel(value), value }))} value={mandate.autonomy} />
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
        Deployments and Portfolio mandates reference the stable account key. Replay and Backtest bind simulated accounts; Paper and Live require the exact externally discovered IBKR account ID and consume the same published policy contract.
      </GuideCallout>
      <ConfigGroup action={<button className="button compact" onClick={addAccount} type="button"><Plus size={14} /> Add account</button>} summary="Account settings change less frequently than strategy behavior and remain reusable across deployments." title="Configured accounts">
        <div className="account-config-grid">
          {section.bindings.map((account, index) => (
            <article className="account-config-card" key={account.account_key}>
              <header><div><strong>{account.name}</strong><span>{account.account_key}</span></div><label className="configuration-switch"><input checked={account.enabled} onChange={(event) => replace(index, { ...account, enabled: event.target.checked })} type="checkbox" /><span /></label></header>
              <div className="configuration-field-grid one-column">
                <TextField help="Human-readable name shown throughout configuration and runtime evidence." label="Account name" onChange={(value) => replace(index, { ...account, name: value })} value={account.name} />
                <div className="configuration-fixed-value"><span>Stable account key</span><strong>{account.account_key}</strong><small>Mandates, groups, and runtime state refer to this identity.</small></div>
                <TextField help="IBKR account ID or simulated runtime account identity." label="Source account" onChange={(value) => replace(index, { ...account, source_account_id: value })} value={account.source_account_id} />
                <SelectField help="Determines broker capability and regulatory constraints." label="Account class" onChange={(value) => replace(index, { ...account, account_class: value })} options={["simulated", "cash", "margin", "registered"].map((value) => ({ label: readableLabel(value), value }))} value={account.account_class} />
                <SelectField help="Reusable account-level capital and risk policy." label="Portfolio policy" onChange={(value) => replace(index, { ...account, portfolio_policy_id: value })} options={draft.portfolio.policies.map((row) => ({ label: String(row.policy_id), value: String(row.policy_id) }))} value={account.portfolio_policy_id} />
                <TextField help="Gateway or simulated session identity used to locate runtime state." label="Session key" onChange={(value) => replace(index, { ...account, session_key: value })} value={account.session_key} />
                <TextField help="Currency used for Portfolio limits and account summaries." label="Base currency" onChange={(value) => replace(index, { ...account, base_currency: value.toUpperCase() })} value={account.base_currency} />
              </div>
              <ModeSelector modes={account.modes} onChange={(modes) => replace(index, { ...account, modes })} />
              {account.modes.some((mode) => mode === "paper" || mode === "live") ? <p className="configuration-safety-note"><ShieldCheck size={15} /> Publication and broker preflight require this exact account ID, a matching external IBKR discovery binding, and an enabled deployment for every selected live mode.</p> : null}
            </article>
          ))}
        </div>
      </ConfigGroup>
    </div>
  );
}

function RevisionBadge({ approved }: { approved: Revision | null }) {
  return <div className="configuration-revision-badge" data-approved={approved ? "true" : "false"}><small>Runtime authority</small><strong>{approved ? `Release ${approved.revision}` : "Not published"}</strong><span>{approved ? approved.label : "Replay is gated"}</span></div>;
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
  const visibleChecks = guided ? checks.filter((check) => ["Deployments", "Mode coverage", "Paper and Live bindings"].includes(check.label)) : checks;
  return (
    <div className="configuration-revision-layout">
      <section className="configuration-publish-card">
        <header><div><span>{guided ? "Ready for use" : "Completion gate"}</span><strong>{guided ? "Publish this setup" : "Publish the application release"}</strong></div><Send size={18} /></header>
        <p>{guided ? "Publishing makes this complete setup available to new runs. Existing runs keep the release they started with." : "A release freezes every referenced Strategy Profile, capability setting, deployment, mandate, policy, OMS profile, account binding, and Canvas. Active runs never change underneath you."}</p>
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
    && draft.portfolio.mandates.some((mandate) => mandate.enabled && mandate.deployment_id === deployment.deployment_id)
  ));
  const mandatesReady = draft.portfolio.mandates.length > 0 && draft.portfolio.mandates.every((mandate) => (
    deployments.some((deployment) => deployment.deployment_id === mandate.deployment_id)
    && accountKeys.has(mandate.account_key)
  ));
  const configuredModes = new Set(draft.accounts.bindings.filter((account) => account.enabled).flatMap((account) => account.modes));
  const modeCoverageReady = draft.accounts.bindings.filter((account) => account.enabled).every((account) => account.modes.every((mode) => deployments.some((deployment) => (
    deployment.enabled
    && deployment.modes.includes(mode)
    && draft.portfolio.mandates.some((mandate) => mandate.enabled && mandate.account_key === account.account_key && mandate.deployment_id === deployment.deployment_id)
  ))));
  const liveBindingsReady = draft.accounts.bindings.every((account) => !account.enabled || !account.modes.some((mode) => mode === "paper" || mode === "live") || Boolean(account.source_account_id.trim() && account.session_key.trim()));
  return [
    { detail: String(draft.strategy.profiles.length), label: "Strategy Profiles", ready: draft.strategy.profiles.length > 0 },
    { detail: deploymentsReady ? `${deployments.length} ready` : "needs mandate or profile", label: "Deployments", ready: deploymentsReady },
    { detail: String(draft.portfolio.mandates.length), label: "Account mandates", ready: mandatesReady },
    { detail: String(draft.oms.profiles.length), label: "OMS profiles", ready: draft.oms.profiles.length > 0 },
    { detail: String(draft.accounts.bindings.length), label: "Accounts", ready: draft.accounts.bindings.length > 0 },
    { detail: modeCoverageReady ? [...configuredModes].map(readableLabel).join(", ") : "deployment coverage missing", label: "Mode coverage", ready: modeCoverageReady },
    { detail: liveBindingsReady ? "exact bindings" : "broker id or session missing", label: "Paper and Live bindings", ready: liveBindingsReady },
    { detail: `${draft.oms.execution_policies.length} execution · ${draft.oms.protection_profiles.length} protection`, label: "Policy catalogs", ready: draft.oms.execution_policies.length > 0 && draft.oms.protection_profiles.length > 0 },
  ];
}

function publishCheckLabel(label: string) {
  if (label === "Deployments") return "Trading setup";
  if (label === "Mode coverage") return "Selected modes";
  if (label === "Paper and Live bindings") return "Broker connection";
  return label;
}

function EffectiveConfigurationPreview({ updatedAt }: { updatedAt: string }) {
  const [mode, setMode] = useState<RuntimeMode>("replay");
  const [payload, setPayload] = useState<{ accounts: Array<Record<string, unknown>>; runtime_count: number; source: string } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setError("");
    api<{ accounts: Array<Record<string, unknown>>; runtime_count: number; source: string }>(`/api/trading/configuration/effective?mode=${mode}`)
      .then((value) => { if (!cancelled) setPayload(value); })
      .catch((reason) => { if (!cancelled) { setPayload(null); setError(reason instanceof Error ? reason.message : String(reason)); } });
    return () => { cancelled = true; };
  }, [mode, updatedAt]);
  return <ConfigGroup summary="Backend-resolved saved-draft evidence. This is the exact account, policy, deployment, and mode projection that a new runtime will consume after publication." title="Effective configuration preview">
    <div className="configuration-toolbar"><SelectField help="Resolve the saved draft for one runtime mode." label="Runtime mode" onChange={(value) => setMode(value as RuntimeMode)} options={["replay", "backtest", "backtest_debug", "paper", "live"].map((value) => ({ label: readableLabel(value), value }))} value={mode} /></div>
    {error ? <p className="configuration-safety-note"><TriangleAlert size={15} /> {error}</p> : null}
    {payload ? <><p className="configuration-section-guide">{payload.runtime_count} eligible deployment{payload.runtime_count === 1 ? "" : "s"} · {payload.accounts.length} bound account{payload.accounts.length === 1 ? "" : "s"} · {readableLabel(payload.source)}</p><div className="mandate-grid">{payload.accounts.map((account) => <article className="mandate-card" key={String(account.account_key)}><header><div><strong>{String(account.name || account.account_key)}</strong><span>{String(account.account_key)} · {String(account.account_class)}</span></div></header><div className="configuration-fixed-value"><span>Broker/session binding</span><strong>{String(account.source_account_id || "Simulated")}</strong><small>{String(account.session_key)} · {String(account.policy_identity)}</small></div><div className="configuration-fixed-value"><span>Eligible deployments</span><strong>{Array.isArray(account.deployment_ids) ? account.deployment_ids.length : 0}</strong><small>{Array.isArray(account.deployment_ids) ? account.deployment_ids.join(", ") || "None for this mode" : "None for this mode"}</small></div></article>)}</div></> : null}
  </ConfigGroup>;
}

function ConfigGroup({ action, children, summary, title }: { action?: ReactNode; children: ReactNode; summary: string; title: string }) {
  const visual = configGroupVisual(title);
  const Icon = visual.icon;
  return <section className="configuration-group" data-group-tone={visual.tone}><header><div className="configuration-group-heading"><span className="configuration-group-icon"><Icon size={15} /></span><div><strong>{title}</strong><p>{summary}</p></div></div>{action}</header><div className="configuration-group-body">{children}</div></section>;
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
  return <aside className="configuration-guide">{icon}<div><strong>{title}</strong><p>{children}</p></div></aside>;
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
  return <TextField help={definition.help} label={definition.label} onChange={onChange} value={String(value)} />;
}

function CapabilityField({ definition, onChange, value }: { definition: CapabilityParameter; onChange: (value: Primitive) => void; value: Primitive }) {
  if (definition.type === "boolean") return <BooleanField help={definition.help} label={definition.label} onChange={onChange} value={Boolean(value)} />;
  if (definition.type === "choice") return <SelectField help={definition.help} label={definition.label} onChange={onChange} options={(definition.options ?? []).map((item) => ({ label: readableLabel(item), value: item }))} value={String(value)} />;
  return <NumberField help={definition.help} label={definition.label} maximum={definition.maximum} minimum={definition.minimum} onChange={onChange} step={definition.step ?? 0.01} unit={definition.display === "fraction" ? "fraction" : definition.unit} value={Number(value)} />;
}

function TextField({ help, label, onChange, value }: { help: HelpContent; label: string; onChange: (value: string) => void; value: string }) {
  return <label className="configuration-field" data-editable="true"><span>{label}<FieldHelp content={help} title={label} /></span><input onChange={(event) => onChange(event.target.value)} value={value} /></label>;
}

function NumberField({ help, label, maximum, minimum, onChange, step, unit, value }: { help: HelpContent; label: string; maximum?: number; minimum?: number; onChange: (value: number) => void; step: number; unit?: string; value: number }) {
  const fraction = unit === "fraction";
  return <label className="configuration-field" data-editable="true"><span>{label}<FieldHelp content={help} title={label} /></span><div className="configuration-number"><input max={fraction ? 100 : maximum} min={fraction ? 0 : minimum} onChange={(event) => onChange(fraction ? Number(event.target.value) / 100 : Number(event.target.value))} step={fraction ? step * 100 : step} type="number" value={fraction ? round(value * 100) : value} />{unit ? <em>{fraction ? "%" : unit}</em> : null}</div></label>;
}

function OptionalNumberField({ help, label, minimum, onChange, step, unit, value }: { help: HelpContent; label: string; minimum?: number; onChange: (value: number | null) => void; step: number; unit?: string; value: number | null }) {
  return <label className="configuration-field" data-editable="true"><span>{label}<FieldHelp content={help} title={label} /></span><div className="configuration-number"><input min={minimum} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} placeholder="Automatic" step={step} type="number" value={value ?? ""} />{unit ? <em>{unit}</em> : null}</div></label>;
}

function SelectField({ help, label, onChange, options, value }: { help: HelpContent; label: string; onChange: (value: string) => void; options: Array<{ label: string; value: string }>; value: string }) {
  return <label className="configuration-field" data-editable="true"><span>{label}<FieldHelp content={help} title={label} /></span><select onChange={(event) => onChange(event.target.value)} value={value}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>;
}

function BooleanField({ help, label, onChange, value }: { help: HelpContent; label: string; onChange: (value: boolean) => void; value: boolean }) {
  return <label className="configuration-field configuration-boolean" data-editable="true"><span>{label}<FieldHelp content={help} title={label} /></span><input checked={value} onChange={(event) => onChange(event.target.checked)} type="checkbox" /></label>;
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
  return <div className="configuration-empty"><strong>Loading configuration</strong><span>Reading the current draft and approved release…</span></div>;
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
  return { path, label, help, kind, choices, unit, step };
}

function flattenPrimitives(value: ParameterMap, prefix = ""): Array<{ path: string; value: Primitive }> {
  return Object.entries(value).flatMap(([key, item]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === "object" && !Array.isArray(item)) return flattenPrimitives(item as ParameterMap, path);
    if (["boolean", "number", "string"].includes(typeof item)) return [{ path, value: item as Primitive }];
    return [];
  });
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
  if (path.endsWith(".method")) return ["structure", "volatility", "hybrid"];
  if (path.endsWith(".trigger")) return ["acceleration_slowdown", "favorable_move_pct", "volatility_multiple"];
  if (path.endsWith(".entry_urgency")) return ["patient", "regular", "urgent", "very_urgent"];
  if (path.endsWith(".exit_urgency")) return ["urgent", "very_urgent"];
  return undefined;
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
function helpForPath(path: string) { return `Advanced ${readableLabel(path)} setting. Changes are validated by the registered strategy implementation before publication.`; }
function readableLabel(value: string) { return value.replaceAll(".", " · ").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function uniqueId(base: string, existing: string[]) { let value = base; let index = 2; while (existing.includes(value)) value = `${base}-${index++}`; return value; }
function round(value: number) { return Math.round(value * 10_000) / 10_000; }
function percent(value: number) { return `${round(value * 100)}%`; }
function accountName(section: AccountSection, id: string) { return section.bindings.find((row) => row.account_key === id)?.name ?? id; }
function deploymentName(section: AssignmentSection, id: string) { return section.deployments.find((row) => row.deployment_id === id)?.name ?? id; }
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

function guidedStepTitle(step: GuidedStep) {
  if (step === "strategy") return "Choose the trading behavior";
  if (step === "assignments") return "Make the strategy deployable";
  if (step === "portfolio") return "Set capital and risk authority";
  if (step === "execution") return "Choose order placement behavior";
  if (step === "protection") return "Define broker-held protection";
  if (step === "accounts") return "Bind accounts and sessions";
  return "Review and publish";
}

function guidedStepDescription(step: GuidedStep) {
  if (step === "strategy") return "Choose the plan, direction, and entry sessions.";
  if (step === "assignments") return "Choose what it watches and where it may run.";
  if (step === "portfolio") return "Set the account, capital limits, and approval level.";
  if (step === "execution") return "Choose how orders follow prices and handle partial fills.";
  if (step === "protection") return "Choose the initial stop design and what happens after taking profit.";
  if (step === "accounts") return "Confirm which account and broker session may be used.";
  return "Review the complete setup before publishing it for new runs.";
}

function guidedContextRows(draft: Draft, step: GuidedStep) {
  const profile = draft.strategy.profiles.find((row) => row.profile_id === draft.strategy.default_profile_id) ?? draft.strategy.profiles[0];
  const deployment = draft.assignments.deployments.find((row) => row.enabled) ?? draft.assignments.deployments[0];
  const mandate = draft.portfolio.mandates.find((row) => row.deployment_id === deployment?.deployment_id) ?? draft.portfolio.mandates[0];
  const oms = draft.oms.profiles.find((row) => row.profile_id === deployment?.oms_profile_id) ?? draft.oms.profiles[0];
  const execution = draft.oms.execution_policies.find((row) => row.policy_id === oms?.settings.entry_execution_policy_id) ?? draft.oms.execution_policies[0];
  const protection = draft.oms.protection_profiles.find((row) => row.profile_id === oms?.settings.protection_profile_id) ?? draft.oms.protection_profiles[0];
  const account = draft.accounts.bindings.find((row) => row.account_key === mandate?.account_key) ?? draft.accounts.bindings[0];
  if (step === "strategy") return [
    { label: "Profile", value: profile?.name ?? "Missing" },
    { label: "Direction", value: profile ? readableLabel(profile.lifecycle.trading_behavior.side) : "Missing" },
    { label: "Sessions", value: profile?.lifecycle.trading_behavior.eligible_sessions.map(readableLabel).join(", ") || "None" },
  ];
  if (step === "assignments") return [
    { label: "Deployment", value: deployment?.name ?? "Missing" },
    { label: "Universe", value: draft.assignments.universes.find((row) => row.universe_id === deployment?.universe_id)?.name ?? "Missing" },
    { label: "Modes", value: deployment?.modes.map(readableLabel).join(", ") || "None" },
  ];
  if (step === "portfolio") return [
    { label: "Account", value: account?.name ?? mandate?.account_key ?? "Missing" },
    { label: "Cash ceiling", value: mandate ? percent(mandate.maximum_cash_fraction) : "Missing" },
    { label: "Risk ceiling", value: mandate ? percent(mandate.maximum_planned_risk_fraction) : "Missing" },
    { label: "Autonomy", value: mandate ? readableLabel(mandate.autonomy) : "Missing" },
  ];
  if (step === "execution") return [
    { label: "OMS profile", value: oms?.name ?? "Missing" },
    { label: "Entry policy", value: execution ? readableLabel(execution.name) : "Missing" },
    { label: "Quote source", value: execution ? readableLabel(execution.quote_source) : "Missing" },
    { label: "Partial fill", value: execution ? readableLabel(execution.partial_fill_policy) : "Missing" },
  ];
  if (step === "protection") return [
    { label: "Protection", value: protection?.name ?? "Missing" },
    { label: "Slices", value: String(protection?.slices.length ?? 0) },
    { label: "Profit transition", value: protection ? readableLabel(protection.profit_pocket_transition) : "Missing" },
    { label: "Backstop", value: protection?.mandatory_catastrophic_backstop ? "Required" : "Not required" },
  ];
  return [
    { label: "Account", value: account?.name ?? "Missing" },
    { label: "Class", value: account ? readableLabel(account.account_class) : "Missing" },
    { label: "Modes", value: account?.modes.map(readableLabel).join(", ") || "None" },
    { label: "Policy", value: account?.portfolio_policy_id ?? "Missing" },
  ];
}

function reviewRows(draft: Draft, approved: Revision | null) {
  const profile = draft.strategy.profiles.find((row) => row.profile_id === draft.strategy.default_profile_id) ?? draft.strategy.profiles[0];
  const deployment = draft.assignments.deployments.find((row) => row.enabled) ?? draft.assignments.deployments[0];
  const mandate = draft.portfolio.mandates.find((row) => row.deployment_id === deployment?.deployment_id) ?? draft.portfolio.mandates[0];
  const oms = draft.oms.profiles.find((row) => row.profile_id === deployment?.oms_profile_id) ?? draft.oms.profiles[0];
  const execution = draft.oms.execution_policies.find((row) => row.policy_id === oms?.settings.entry_execution_policy_id) ?? draft.oms.execution_policies[0];
  const protection = draft.oms.protection_profiles.find((row) => row.profile_id === oms?.settings.protection_profile_id) ?? draft.oms.protection_profiles[0];
  const account = draft.accounts.bindings.find((row) => row.account_key === mandate?.account_key) ?? draft.accounts.bindings[0];
  const checks = releaseReadiness(draft);
  const inherited = <K extends keyof Draft>(key: K) => Boolean(approved && stableStringify(draft[key]) === stableStringify(approved.payload[key]));
  const state = (key: keyof Draft, valid: boolean, recommended: boolean): "Inherited" | "Invalid" | "Using recommended" | "Customized" => !valid ? "Invalid" : inherited(key) ? "Inherited" : recommended ? "Using recommended" : "Customized";
  return [
    { icon: GitBranch, label: "Strategy", selection: profile?.name ?? "Missing", state: state("strategy", Boolean(profile), Boolean(profile?.protected)), step: "strategy" as GuidedStep },
    { icon: Network, label: "Deployment", selection: deployment?.name ?? "Missing", state: state("assignments", Boolean(deployment && checks[1]?.ready), false), step: "assignments" as GuidedStep },
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
