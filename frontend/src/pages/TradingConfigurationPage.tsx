import {
  BadgeCheck,
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
  Save,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
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
type OmsSection = { profiles: OmsProfile[] };

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

  async function saveSection() {
    if (!draft || section === "revisions") return;
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
    } catch (reason) {
      setStatus("error");
      setMessage(reason instanceof Error ? reason.message : String(reason));
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
    <div className="trading-configuration-page">
      <header className="configuration-page-header">
        <div className="configuration-page-icon"><Icon size={20} /></div>
        <div>
          <span>{meta.eyebrow}</span>
          <h1>{meta.title}</h1>
          <p>{meta.description}</p>
        </div>
        <RevisionBadge approved={approved} />
      </header>

      <ConfigurationJourney active={section} draft={draft} />

      {message ? (
        <div className={`configuration-message ${status === "error" ? "error" : "success"}`}>
          {status === "error" ? <TriangleAlert size={17} /> : <CheckCircle2 size={17} />}
          <span>{message}</span>
        </div>
      ) : null}

      {section === "revisions" ? (
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
          <div className="configuration-save-bar">
            <span>{dirtySection === section ? "Unsaved draft changes" : "Draft matches saved configuration"}</span>
            <button className="button primary" disabled={dirtySection !== section || status === "saving"} onClick={saveSection} type="button">
              <Save size={15} /> {status === "saving" ? "Saving…" : "Save draft"}
            </button>
          </div>
          <JsonInspector label={`${meta.title} generated JSON`} value={draft[section]} />
        </>
      ) : <ConfigurationLoading />}
    </div>
  );
}

function ConfigurationJourney({ active, draft }: { active: TradingConfigurationSection; draft: Draft | null }) {
  const steps = [
    { key: "strategy", label: "Strategy Profile", ready: Boolean(draft?.strategy.profiles.length) },
    { key: "assignments", label: "Deployment", ready: Boolean(draft?.assignments.deployments.length) },
    { key: "portfolio", label: "Capital mandates", ready: Boolean(draft?.portfolio.mandates.length) },
    { key: "revisions", label: "Publish", ready: false },
  ];
  return (
    <nav aria-label="Configuration journey" className="configuration-journey">
      {steps.map((step, index) => (
        <a aria-current={active === step.key ? "step" : undefined} data-ready={step.ready ? "true" : "false"} href={`#${pageForSection(step.key as TradingConfigurationSection)}`} key={step.key}>
          <span>{step.ready ? <Check size={13} /> : index + 1}</span>
          <strong>{step.label}</strong>
          {index < steps.length - 1 ? <ChevronRight size={14} /> : null}
        </a>
      ))}
    </nav>
  );
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
          <ReentryEditor catalog={section.input_catalog} profile={selected} onChange={replaceProfile} />
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

function ReentryEditor({ catalog, onChange, profile }: {
  catalog: StrategyInput[];
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
            order_intent: { deadline_ms: 750, execution_policy: "adaptive_urgent", partial_fill_policy: "complete_remainder" },
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
  return (
    <div className="strategy-exit-routes">
      <div className="configuration-protection-authority">
        <ShieldCheck size={19} />
        <div>
          <span>OMS safety authority</span>
          <strong>Protective stop is independent of strategic exit rules</strong>
          <p>OMS calculates, submits, repairs, and reconciles the protective order. Strategy rules cannot disable or delay it. {omsProfile ? `${omsProfile.name} uses ${readableLabel(omsProfile.settings.protection.stop_method)}, a ${omsProfile.settings.protection.structure_buffer_bps} bps structure buffer, ${omsProfile.settings.protection.volatility_multiple}× volatility, ${omsProfile.settings.protection.maximum_risk_pct}% maximum risk, and trailing ${omsProfile.settings.protection.trailing_enabled ? "enabled" : "disabled"}.` : "Select an OMS profile in Deployment to resolve its exact parameters."}</p>
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

function PhaseOrderEditor({ capitalRequest, eligibleSessions, onCapitalRequest, onOrderIntent, orderIntent, title }: {
  capitalRequest: CapitalRequestConfig;
  eligibleSessions: string[];
  onCapitalRequest: (value: CapitalRequestConfig) => void;
  onOrderIntent: (value: OrderIntentConfig) => void;
  orderIntent: OrderIntentConfig;
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
        <OrderIntentEditor eligibleSessions={eligibleSessions} onChange={onOrderIntent} value={orderIntent} />
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

function OrderIntentEditor({ eligibleSessions, onChange, value }: {
  eligibleSessions: string[];
  onChange: (value: OrderIntentConfig) => void;
  value: OrderIntentConfig;
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
        options={["passive", "midpoint", "adaptive_patient", "adaptive_regular", "adaptive_urgent", "adaptive_very_urgent", "immediate_with_limit", "ibkr_native_adaptive", "cancel_if_not_filled"].map((policy) => ({ label: readableLabel(policy), value: policy }))}
        value={value.execution_policy}
      />
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

function AddStepsEditor({ catalog, eligibleSessions, onChange, steps }: {
  catalog: StrategyInput[];
  eligibleSessions: string[];
  onChange: (value: AddStep[]) => void;
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
      order_intent: { deadline_ms: 750, execution_policy: "adaptive_urgent", partial_fill_policy: "complete_remainder" },
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
              <PhaseOrderEditor capitalRequest={step.capital_request} eligibleSessions={eligibleSessions} orderIntent={step.order_intent} title={`${step.name} request`} onCapitalRequest={(capital_request) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, capital_request } : row))} onOrderIntent={(order_intent) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, order_intent } : row))} />
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

  function updatePolicy(key: string, value: Primitive) {
    const policies = section.policies.map((row, index) => index === policyIndex ? { ...row, [key]: value } : row);
    onChange({ ...section, policies });
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

  return (
    <div className="configuration-stack">
      <GuideCallout icon={<BriefcaseBusiness size={17} />} title="Strategy requests are relative; Portfolio makes them account-specific">
        A strategy may request an aggressive allocation, but the mandate and current account state determine final quantity. Replacement is a separately governed, auditable proposal—not an implicit strategy side effect.
      </GuideCallout>
      <ConfigGroup summary="Stable account-level limits apply to every strategy using the account." title="Account safety policy">
        <div className="configuration-toolbar">
          <SelectField help="Policy revision being edited." label="Policy" onChange={setSelectedPolicyId} options={section.policies.map((row) => ({ label: String(row.policy_id), value: String(row.policy_id) }))} value={selectedPolicyId} />
        </div>
        {policy ? <div className="configuration-field-grid">
          {[
            field("eligible_equity_fraction", "Eligible equity", "Fraction of account equity available to all trading mandates.", "number", undefined, "fraction", 0.05),
            field("minimum_cash_reserve", "Cash reserve", "Cash that Portfolio must leave unused.", "number", undefined, "currency", 100),
            field("maximum_buying_power_utilization", "Buying power use", "Maximum fraction of broker buying power Portfolio may consume.", "number", undefined, "fraction", 0.05),
            field("maximum_position_fraction", "Position ceiling", "Maximum account equity attributable to one position.", "number", undefined, "fraction", 0.01),
            field("maximum_open_risk_fraction", "Open risk ceiling", "Maximum aggregate planned open risk.", "number", undefined, "fraction", 0.005),
            field("maximum_open_positions", "Open positions", "Maximum simultaneous positions for this account policy.", "number", undefined, "positions", 1),
            field("maximum_daily_loss", "Daily loss limit", "New entries stop when the loss limit is reached.", "number", undefined, "currency", 100),
            field("maximum_drawdown", "Drawdown limit", "Hard peak-to-trough account control.", "number", undefined, "currency", 100),
          ].map((definition) => <ParameterField definition={definition} key={definition.path} value={policy[definition.path] as Primitive} onChange={(value) => updatePolicy(definition.path, value)} />)}
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
    </div>
  );
}

function OmsEditor({ onChange, section }: { onChange: (value: OmsSection) => void; section: OmsSection }) {
  const [selectedId, setSelectedId] = useState(section.profiles[0]?.profile_id ?? "");
  const selected = section.profiles.find((row) => row.profile_id === selectedId) ?? section.profiles[0];
  if (!selected) return <EmptyState title="No OMS profile" detail="Create a shared execution and protection profile." />;
  function replace(next: OmsProfile) {
    onChange({ profiles: section.profiles.map((row) => row.profile_id === selected.profile_id ? next : row) });
  }
  function clone() {
    const id = uniqueId(`${selected.profile_id}-copy`, section.profiles.map((row) => row.profile_id));
    const next = { ...deepClone(selected), profile_id: id, name: `${selected.name} copy`, origin: "user" as const, revision: 1 };
    onChange({ profiles: [...section.profiles, next] });
    setSelectedId(id);
  }
  return (
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
  );
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
        Deployments and Portfolio mandates reference the stable account key. Replay uses simulated state; Paper and Live later bind the same policy contract to authenticated broker accounts.
      </GuideCallout>
      <ConfigGroup action={<button className="button compact" onClick={addAccount} type="button"><Plus size={14} /> Add account</button>} summary="Account settings change less frequently than strategy behavior and remain reusable across deployments." title="Configured accounts">
        <div className="account-config-grid">
          {section.bindings.map((account, index) => (
            <article className="account-config-card" key={account.account_key}>
              <header><div><strong>{account.name}</strong><span>{account.account_key}</span></div><label className="configuration-switch"><input checked={account.enabled} onChange={(event) => replace(index, { ...account, enabled: event.target.checked })} type="checkbox" /><span /></label></header>
              <div className="configuration-field-grid one-column">
                <TextField help="Human-readable name shown throughout configuration and runtime evidence." label="Account name" onChange={(value) => replace(index, { ...account, name: value })} value={account.name} />
                <TextField help="Stable application identity. Existing mandates refer to this value." label="Account key" onChange={(value) => replace(index, { ...account, account_key: value })} value={account.account_key} />
                <TextField help="IBKR account ID or simulated runtime account identity." label="Source account" onChange={(value) => replace(index, { ...account, source_account_id: value })} value={account.source_account_id} />
                <SelectField help="Determines broker capability and regulatory constraints." label="Account class" onChange={(value) => replace(index, { ...account, account_class: value })} options={["simulated", "cash", "margin", "registered"].map((value) => ({ label: readableLabel(value), value }))} value={account.account_class} />
                <SelectField help="Reusable account-level capital and risk policy." label="Portfolio policy" onChange={(value) => replace(index, { ...account, portfolio_policy_id: value })} options={draft.portfolio.policies.map((row) => ({ label: String(row.policy_id), value: String(row.policy_id) }))} value={account.portfolio_policy_id} />
                <TextField help="Gateway or simulated session identity used to locate runtime state." label="Session key" onChange={(value) => replace(index, { ...account, session_key: value })} value={account.session_key} />
                <TextField help="Currency used for Portfolio limits and account summaries." label="Base currency" onChange={(value) => replace(index, { ...account, base_currency: value.toUpperCase() })} value={account.base_currency} />
              </div>
              <ModeSelector modes={account.modes} onChange={(modes) => replace(index, { ...account, modes })} />
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

function RevisionPublisher({ approved, draft, label, onLabelChange, onPublish, publishing, revisions }: {
  approved: Revision | null;
  draft: Draft | null;
  label: string;
  onLabelChange: (value: string) => void;
  onPublish: () => void;
  publishing: boolean;
  revisions: Revision[];
}) {
  const canvas = useMemo(canvasApprovalSnapshot, [approved, draft]);
  const checks = draft ? releaseReadiness(draft) : [];
  const configurationReady = checks.every((check) => check.ready);
  return (
    <div className="configuration-revision-layout">
      <section className="configuration-publish-card">
        <header><div><span>Completion gate</span><strong>Publish the application release</strong></div><Send size={18} /></header>
        <p>A release freezes every referenced Strategy Profile, capability setting, deployment, mandate, policy, OMS profile, account binding, and Canvas. Active runs never change underneath you.</p>
        <div className="configuration-publish-proof">
          {checks.map((check) => <span data-ready={check.ready ? "true" : "false"} key={check.label}>{check.ready ? <CheckCircle2 size={14} /> : <TriangleAlert size={14} />} {check.label} · {check.detail}</span>)}
          <span data-ready={canvas.ready ? "true" : "false"}><CheckCircle2 size={14} /> Canvas · {canvas.containerCount} containers</span>
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
  const replayReady = deployments.some((deployment) => deployment.enabled && deployment.modes.includes("replay"));
  return [
    { detail: String(draft.strategy.profiles.length), label: "Strategy Profiles", ready: draft.strategy.profiles.length > 0 },
    { detail: deploymentsReady ? `${deployments.length} ready` : "needs mandate or profile", label: "Deployments", ready: deploymentsReady },
    { detail: String(draft.portfolio.mandates.length), label: "Account mandates", ready: mandatesReady },
    { detail: String(draft.oms.profiles.length), label: "OMS profiles", ready: draft.oms.profiles.length > 0 },
    { detail: String(draft.accounts.bindings.length), label: "Accounts", ready: draft.accounts.bindings.length > 0 },
    { detail: replayReady ? "enabled" : "required", label: "Replay mode", ready: replayReady },
  ];
}

function ConfigGroup({ action, children, summary, title }: { action?: ReactNode; children: ReactNode; summary: string; title: string }) {
  return <section className="configuration-group"><header><div><strong>{title}</strong><p>{summary}</p></div>{action}</header><div className="configuration-group-body">{children}</div></section>;
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
