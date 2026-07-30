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
  GitBranch,
  Network,
  Plus,
  Save,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

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
  operator: "all" | "any";
  weight: number;
};

type RuleStage = {
  groups: RuleGroup[];
  minimum_score?: number;
  operator: "all" | "any" | "weighted";
};

type EntryRules = {
  blockers: RuleStage;
  confirmation: RuleStage;
  opportunity: RuleStage;
};

type ExitRoute = {
  action: "close" | "reduce";
  category: "protective" | "strategic" | "profit" | "emergency";
  enabled: boolean;
  mechanism: string;
  name: string;
  priority: number;
  protected: boolean;
  route_id: string;
  settings: Record<string, Primitive>;
  summary: string;
};

type StrategyLifecycle = {
  trading_behavior: {
    adopt_manual_positions: boolean;
    eligible_sessions: string[];
    evaluation_trigger: string;
    side: "long" | "short" | "both";
  };
  initial_entry: EntryRules;
  reentry: {
    cooldown_ms: number;
    enabled: boolean;
    maximum_attempts: number;
    require_new_confirmation: boolean;
    reuse_initial_entry: boolean;
  };
  exit: { routes: ExitRoute[] };
};

type StrategySection = {
  capability_catalog: CapabilityDefinition[];
  default_profile_id: string;
  definitions: Array<{ automatic: boolean; direction: string; name: string; revision: number; strategy_id: string }>;
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
    outside_rth: boolean;
    protection: {
      maximum_risk_pct: number;
      stop_method: string;
      structure_buffer_bps: number;
      trailing_enabled: boolean;
      volatility_multiple: number;
    };
    tick_size: number;
    time_in_force: string;
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

const FREQUENT_PARAMETERS = [
  field("sizing.request_mode", "Capital request", "Express size as a fixed quantity, fraction of mandate capacity, risk fraction, or all available mandate capacity.", "choice", ["fixed_quantity", "mandate_fraction", "risk_fraction", "all_available"]),
  field("sizing.request_value", "Request value", "Meaning depends on Capital request: shares for fixed quantity and a fraction for mandate or risk sizing.", "number", undefined, "value", 0.01),
  field("sizing.add_fraction", "Add size", "Fraction of the initial request used for an approved add.", "number", undefined, "fraction", 0.05),
  field("sizing.maximum_position_quantity", "Position ceiling", "Hard strategy-level quantity ceiling before Portfolio applies stricter account limits.", "number", undefined, "shares", 1),
] as const;

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
          {section === "strategy" ? <StrategyStudio draft={draft} section={draft.strategy} onChange={(value) => updateDraft("strategy", value)} /> : null}
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

function StrategyStudio({ draft, onChange, section }: { draft: Draft; onChange: (value: StrategySection) => void; section: StrategySection }) {
  const [selectedId, setSelectedId] = useState(section.profiles[0]?.profile_id ?? "");
  const selected = section.profiles.find((row) => row.profile_id === selectedId) ?? section.profiles[0];
  const profileInUse = draft.assignments.deployments.some((row) => row.profile_id === selected?.profile_id);
  useEffect(() => {
    if (!section.profiles.some((row) => row.profile_id === selectedId)) setSelectedId(section.profiles[0]?.profile_id ?? "");
  }, [section.profiles, selectedId]);
  if (!selected) return <EmptyState title="No Strategy Profiles" detail="Create a profile from a registered strategy definition." />;

  function replaceProfile(next: StrategyProfile) {
    onChange({ ...section, profiles: section.profiles.map((row) => row.profile_id === selected.profile_id ? next : row) });
  }

  function cloneProfile() {
    const id = uniqueId(`${selected.profile_id}-copy`, section.profiles.map((row) => row.profile_id));
    const next = { ...deepClone(selected), profile_id: id, name: `${selected.name} copy`, origin: "user" as const, protected: false, revision: 1 };
    onChange({ ...section, profiles: [...section.profiles, next] });
    setSelectedId(id);
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
    !FREQUENT_PARAMETERS.some((fieldDefinition) => fieldDefinition.path === row.path)
    && !LEGACY_ENTRY_LOGIC_PATHS.has(row.path)
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
            <button className="button compact" onClick={cloneProfile} type="button"><Clipboard size={14} /> Clone</button>
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
          <DecisionRulesEditor
            catalog={section.input_catalog}
            rules={entryRules}
            onChange={(value) => replaceProfile({
              ...selected,
              lifecycle: {
                ...selected.lifecycle,
                initial_entry: value,
              },
            })}
          />
        </LifecyclePanel>

        <LifecyclePanel
          eyebrow="Phase 2"
          summary={selected.lifecycle.reentry.enabled ? `Up to ${selected.lifecycle.reentry.maximum_attempts} reentries · ${selected.lifecycle.reentry.cooldown_ms} ms cooldown` : "Reentry disabled"}
          title="Reentry"
        >
          <ReentryEditor profile={selected} onChange={replaceProfile} />
        </LifecyclePanel>

        <LifecyclePanel
          eyebrow="Phase 3"
          summary={`${selected.lifecycle.exit.routes.filter((row) => row.enabled).length} active routes · protective stop always enabled`}
          title="Exit"
        >
          <ExitRoutesEditor profile={selected} onChange={replaceProfile} />
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
    <details className="strategy-lifecycle-panel" onToggle={(event) => setOpen(event.currentTarget.open)} open={open}>
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
  const supportedSides = definition?.direction === "long_only" ? ["long"] : ["long", "short", "both"];
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
          help={supportedSides.length === 1 ? "This registered strategy implementation supports long campaigns only." : "Direction this strategy implementation may request."}
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
        <legend>Eligible sessions <CircleHelp size={13} /></legend>
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
      <div className="configuration-field-grid">
        {FREQUENT_PARAMETERS.map((definition) => (
          <ParameterField
            definition={definition}
            key={definition.path}
            value={getPath(profile.parameters, definition.path) as Primitive}
            onChange={(value) => onChange({ ...profile, parameters: setPath(profile.parameters, definition.path, value) })}
          />
        ))}
      </div>
    </>
  );
}

function ReentryEditor({ onChange, profile }: {
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
      <p className="configuration-section-guide">A reentry occurs only after a full exit while the same Strategy Campaign retains ticker ownership. Adding to an open position is a capability, not a reentry.</p>
      <div className="configuration-field-grid">
        <BooleanField help="Permit another flat-to-open transition within the same ticker campaign." label="Enable reentry" onChange={(enabled) => update({ ...reentry, enabled })} value={reentry.enabled} />
        <BooleanField help="Evaluate the same explicit opportunity, confirmation, and blocker rules used for the first entry." label="Reuse initial-entry rules" onChange={(reuse_initial_entry) => update({ ...reentry, reuse_initial_entry })} value={reentry.reuse_initial_entry} />
        <BooleanField help="Evidence used for the previous entry cannot be reused without a newer causal update." label="Require new confirmation" onChange={(require_new_confirmation) => update({ ...reentry, require_new_confirmation })} value={reentry.require_new_confirmation} />
        <NumberField help="Minimum time after a confirmed full exit before reentry becomes eligible." label="Cooldown" minimum={0} onChange={(cooldown_ms) => update({ ...reentry, cooldown_ms })} step={100} unit="ms" value={reentry.cooldown_ms} />
        <NumberField help="Maximum reentries during one ticker campaign. Zero allows only the initial entry." label="Maximum attempts" minimum={0} onChange={(maximum_attempts) => update({ ...reentry, maximum_attempts })} step={1} unit="entries" value={reentry.maximum_attempts} />
      </div>
    </>
  );
}

function ExitRoutesEditor({ onChange, profile }: {
  onChange: (value: StrategyProfile) => void;
  profile: StrategyProfile;
}) {
  const routes = profile.lifecycle.exit.routes;
  function replace(routeId: string, next: ExitRoute) {
    onChange({
      ...profile,
      lifecycle: {
        ...profile.lifecycle,
        exit: { routes: routes.map((row) => row.route_id === routeId ? next : row) },
      },
    });
  }
  return (
    <div className="strategy-exit-routes">
      <p className="configuration-section-guide">Exit routes are evaluated by priority. A full exit may continue into Reentry; campaign termination is configured on the Deployment. Protective exits cannot be disabled or delayed.</p>
      {routes.map((route) => (
        <article data-enabled={route.enabled ? "true" : "false"} key={route.route_id}>
          <header>
            <div><span>{readableLabel(route.category)} · priority {route.priority}</span><strong>{route.name}</strong><p>{route.summary}</p></div>
            <label className="configuration-switch" title={route.protected ? "Required safety route" : "Enable exit route"}>
              <input checked={route.enabled} disabled={route.protected} onChange={(event) => replace(route.route_id, { ...route, enabled: event.target.checked })} type="checkbox" />
              <span />
            </label>
          </header>
          <div className="configuration-field-grid">
            {route.protected ? (
              <div className="configuration-fixed-value"><span>Priority</span><strong>100 · First</strong><small>Fixed safety authority</small></div>
            ) : <NumberField help="Higher-priority routes are evaluated first when several exit conditions occur together." label="Priority" maximum={99} minimum={0} onChange={(priority) => replace(route.route_id, { ...route, priority })} step={1} unit="0–99" value={route.priority} />}
            {route.protected ? (
              <div className="configuration-fixed-value"><span>Action</span><strong>Close position</strong><small>Cannot be weakened</small></div>
            ) : <SelectField help="Intent emitted when this route passes." label="Action" onChange={(action) => replace(route.route_id, { ...route, action: action as ExitRoute["action"] })} options={[{ label: "Close position", value: "close" }, { label: "Reduce position", value: "reduce" }]} value={route.action} />}
            {route.mechanism === "bearish_qmd_macd" ? (
              <>
                <NumberField help="Signed QMD score at or below which adverse momentum becomes eligible." label="QMD score" maximum={1} minimum={-1} onChange={(qmd_score) => replace(route.route_id, { ...route, settings: { ...route.settings, qmd_score } })} step={0.05} unit="score" value={Number(route.settings.qmd_score ?? -0.35)} />
                <NumberField help="Minimum confidence required for the adverse QMD score." label="QMD confidence" maximum={1} minimum={0} onChange={(qmd_confidence) => replace(route.route_id, { ...route, settings: { ...route.settings, qmd_confidence } })} step={0.05} unit="score" value={Number(route.settings.qmd_confidence ?? 0.55)} />
                <BooleanField help="Require MACD line and histogram to confirm the adverse QMD evidence." label="Require bearish MACD" onChange={(require_macd_bearish) => replace(route.route_id, { ...route, settings: { ...route.settings, require_macd_bearish } })} value={Boolean(route.settings.require_macd_bearish)} />
              </>
            ) : null}
          </div>
        </article>
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
    summary: "Passing groups contribute their configured weight to the minimum confirmation score.",
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

function DecisionRulesEditor({ catalog, onChange, rules }: {
  catalog: StrategyInput[];
  onChange: (value: EntryRules) => void;
  rules: EntryRules;
}) {
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
      groups: [...stage.groups, {
        conditions: [condition],
        enabled: true,
        group_id: groupId,
        label: "New rule set",
        operator: "all",
        weight: stageName === "confirmation" ? 0.25 : 1,
      }],
    });
  }

  return (
    <div className="strategy-rule-editor">
      <div className="strategy-source-legend">
        <GitBranch size={18} />
        <div>
          <strong>How initial entry is decided</strong>
          <p>Opportunity passes, weighted confirmation passes, and no entry blocker passes. Manual and automatic authority is configured on the deployment.</p>
        </div>
      </div>
      {(Object.keys(RULE_STAGE_META) as Array<keyof EntryRules>).map((stageName) => {
        const stage = rules[stageName];
        const meta = RULE_STAGE_META[stageName];
        return (
          <section className="strategy-rule-stage" data-stage={stageName} key={stageName}>
            <header>
              <div><span>{stageName}</span><strong>{meta.label}</strong><p>{meta.summary}</p></div>
              <div className="strategy-stage-controls">
                {stageName === "confirmation" ? (
                  <NumberField
                    help="Minimum weighted fraction of confirmation groups that must pass."
                    label="Required score"
                    maximum={1}
                    minimum={0}
                    onChange={(minimum_score) => replaceStage(stageName, { ...stage, minimum_score })}
                    step={0.05}
                    unit="score"
                    value={Number(stage.minimum_score ?? 0.55)}
                  />
                ) : (
                  <SelectField
                    help="Choose whether any rule set or every rule set must pass."
                    label="Stage logic"
                    onChange={(operator) => replaceStage(stageName, { ...stage, operator: operator as "all" | "any" })}
                    options={[{ label: "Any rule set", value: "any" }, { label: "All rule sets", value: "all" }]}
                    value={stage.operator}
                  />
                )}
                <button className="button compact" onClick={() => addGroup(stageName)} type="button"><Plus size={14} /> Add rule set</button>
              </div>
            </header>
            <div className="strategy-rule-groups">
              {stage.groups.map((group) => (
                <RuleGroupEditor
                  catalog={catalog}
                  group={group}
                  key={group.group_id}
                  onChange={(next) => replaceGroup(stageName, group.group_id, next)}
                  onRemove={() => replaceStage(stageName, { ...stage, groups: stage.groups.filter((row) => row.group_id !== group.group_id) })}
                  removable={stage.groups.length > 1}
                  showWeight={stageName === "confirmation"}
                />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function RuleGroupEditor({ catalog, group, onChange, onRemove, removable, showWeight }: {
  catalog: StrategyInput[];
  group: RuleGroup;
  onChange: (value: RuleGroup) => void;
  onRemove: () => void;
  removable: boolean;
  showWeight: boolean;
}) {
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
    <article className="strategy-rule-group" data-enabled={group.enabled ? "true" : "false"}>
      <header>
        <label><span>Rule set name</span><input onChange={(event) => onChange({ ...group, label: event.target.value })} value={group.label} /></label>
        <label><span>Conditions</span><select onChange={(event) => onChange({ ...group, operator: event.target.value as "all" | "any" })} value={group.operator}><option value="all">All must pass</option><option value="any">Any may pass</option></select></label>
        {showWeight ? <label><span>Weight</span><input max={1} min={0} onChange={(event) => onChange({ ...group, weight: Number(event.target.value) })} step={0.05} type="number" value={group.weight} /></label> : null}
        <label className="configuration-enabled"><input checked={group.enabled} onChange={(event) => onChange({ ...group, enabled: event.target.checked })} type="checkbox" /> Enabled</label>
        <button aria-label={`Delete ${group.label}`} className="button compact danger" disabled={!removable} onClick={onRemove} type="button"><Trash2 size={14} /></button>
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
    </article>
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
          <div className="configuration-field-grid">
            <SelectField help="Default urgency for entries. Strategy capabilities may select only an allowed profile." label="Entry urgency" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, entry_urgency: value } })} options={urgencyOptions()} value={selected.settings.entry_urgency} />
            <SelectField help="Default urgency for risk-reducing and final exits." label="Exit urgency" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, exit_urgency: value } })} options={urgencyOptions()} value={selected.settings.exit_urgency} />
            <NumberField help="Permitted limit-price offset from current execution evidence." label="Limit offset" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, limit_offset_bps: value } })} step={0.5} unit="bps" value={selected.settings.limit_offset_bps} />
            <NumberField help="Minimum price increment used by the planner." label="Tick size" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, tick_size: value } })} step={0.01} unit="price" value={selected.settings.tick_size} />
            <SelectField help="Broker order lifetime." label="Time in force" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, time_in_force: value } })} options={["DAY", "GTC", "IOC", "OPG"].map((value) => ({ label: value, value }))} value={selected.settings.time_in_force} />
            <BooleanField help="Permit execution outside the regular session when account policy also allows it." label="Outside regular hours" onChange={(value) => replace({ ...selected, settings: { ...selected.settings, outside_rth: value } })} value={selected.settings.outside_rth} />
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
        <label><span>Release label <FieldHelp text="Use a short operational label that explains what this release is intended to validate." /></span><input onChange={(event) => onLabelChange(event.target.value)} placeholder="Replay strategy-studio acceptance" value={label} /></label>
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

function FieldHelp({ text }: { text: string }) {
  return (
    <details className="configuration-help">
      <summary aria-label={`Help: ${text}`}><CircleHelp size={14} /></summary>
      <span role="tooltip">{text}</span>
    </details>
  );
}

type FieldDefinition = {
  choices?: readonly string[];
  help: string;
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

function TextField({ help, label, onChange, value }: { help: string; label: string; onChange: (value: string) => void; value: string }) {
  return <label className="configuration-field"><span>{label}<FieldHelp text={help} /></span><input onChange={(event) => onChange(event.target.value)} value={value} /></label>;
}

function NumberField({ help, label, maximum, minimum, onChange, step, unit, value }: { help: string; label: string; maximum?: number; minimum?: number; onChange: (value: number) => void; step: number; unit?: string; value: number }) {
  const fraction = unit === "fraction";
  return <label className="configuration-field"><span>{label}<FieldHelp text={help} /></span><div className="configuration-number"><input max={fraction ? 100 : maximum} min={fraction ? 0 : minimum} onChange={(event) => onChange(fraction ? Number(event.target.value) / 100 : Number(event.target.value))} step={fraction ? step * 100 : step} type="number" value={fraction ? round(value * 100) : value} />{unit ? <em>{fraction ? "%" : unit}</em> : null}</div></label>;
}

function SelectField({ help, label, onChange, options, value }: { help: string; label: string; onChange: (value: string) => void; options: Array<{ label: string; value: string }>; value: string }) {
  return <label className="configuration-field"><span>{label}<FieldHelp text={help} /></span><select onChange={(event) => onChange(event.target.value)} value={value}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>;
}

function BooleanField({ help, label, onChange, value }: { help: string; label: string; onChange: (value: boolean) => void; value: boolean }) {
  return <label className="configuration-field configuration-boolean"><span>{label}<FieldHelp text={help} /></span><input checked={value} onChange={(event) => onChange(event.target.checked)} type="checkbox" /></label>;
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

function field(path: string, label: string, help: string, kind: FieldDefinition["kind"], choices?: readonly string[], unit?: string, step?: number): FieldDefinition {
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
  if (path.endsWith(".time_in_force")) return ["DAY", "GTC", "IOC", "OPG"];
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
