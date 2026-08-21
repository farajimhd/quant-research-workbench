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
  LayoutGrid,
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

import { api, apiCached, invalidateApiCache } from "../api/client";
import { clearConfigurationSession } from "../app/configurationSession";
import { AbstractionCard } from "../app/components/AbstractionCard";
import { DefinitionRegistryProvider, type InformationRegistry } from "../app/components/DefinitionRegistry";
import { InventoryFilterSelect } from "../app/components/InventoryFilterSelect";
import { formatSemanticNumber } from "../app/format";
import type { TradingConfigurationSection } from "../app/routes";
import { DataCatalogPage, RuleSetLibraryPage, dataFieldRuleDefinitions, type DataRuleSet } from "./DataConfigurationPages";
import { MarketDiscoveryComposer, type MarketDiscoveryConfiguration } from "./MarketDiscoveryComposer";
import { TradingActionsPage, type ActionPolicyDefinition, type TradingActionDefinition } from "./TradingActionsPage";
import {
  BooleanField,
  CapabilityField,
  ConfigGroup,
  ConfigurationNarrative,
  EmptyState,
  FieldHelp,
  GuideCallout,
  JsonInspector,
  ModeSelector,
  NumberField,
  OptionalNumberField,
  ParameterField,
  SelectField,
  TextField,
  choiceExplanation,
  fieldSummary,
  readableLabel,
  round,
  type FieldDefinition,
  type HelpContent,
} from "../features/trading-configuration/components/ConfigurationFields";
import {
  AccountsEditor,
  CampaignPolicyEditor,
  ConfigurationSectionStudio,
  DeploymentEditor,
  OmsEditor,
  PortfolioEditor,
  type ConfigurableSection,
} from "../features/trading-configuration/sections/OperationalConfigurationSections";
import {
  accountName,
  canvasApprovalSnapshot,
  choicesFor,
  controlFor,
  deepClone,
  deploymentName,
  field,
  flattenPrimitives,
  helpForPath,
  isDirectlyEditableStrategyParameter,
  labelForStrategyParameter,
  percent,
  registryGroupLabel,
  setPath,
  stableStringify,
  stepFor,
  uniqueId,
  unitFor,
  urgencyOptions,
} from "../features/trading-configuration/utilities";

export type { TradingConfigurationSection } from "../app/routes";

import {
  AccountBinding,
  AccountSection,
  ActionAuthority,
  AddStep,
  AssignmentSection,
  CapabilityBinding,
  CapabilityDefinition,
  CapabilityParameter,
  CapitalRequestConfig,
  DiscoveryCapability,
  DiscoveryField,
  Draft,
  EnrichmentFieldDefinition,
  EntryAuthoringPage,
  EntryRules,
  ENTRY_AUTHORING_PAGES,
  ExecutionPolicyConfig,
  ExecutionRoute,
  ExitAuthoringPage,
  ExitRuleSet,
  EXIT_AUTHORING_PAGES,
  HistoricalScannerSnapshot,
  ManageAuthoringPage,
  Mandate,
  MANAGE_AUTHORING_PAGES,
  MarketClassification,
  MarketDiscoverySection,
  OmsProfile,
  OmsSection,
  OrderIntentConfig,
  ParameterMap,
  PortfolioPolicy,
  PortfolioSection,
  Primitive,
  ProtectionProfileConfig,
  ProtectionSliceConfig,
  ProtectionStopConfig,
  ProtectionTrailingConfig,
  ReentryAuthoringPage,
  REENTRY_AUTHORING_PAGES,
  RuleCondition,
  RuleExpression,
  RuleGroup,
  RuleSetDefinition,
  RuleStage,
  RuntimeAssignment,
  RuntimeMode,
  SessionProfile,
  SessionSection,
  SignalStreamConfig,
  StrategyAuthoringStage,
  StrategyDefinition,
  StrategyDeployment,
  StrategyInput,
  StrategyLifecycle,
  StrategyPhaseMode,
  StrategyProfile,
  StrategyRunPlan,
  StrategySection,
  WatchlistColumn,
  WatchlistConfig,
  WatchlistGuidedStep,
  WatchlistRuntimeSnapshot,
  WatchUniverse,
  WATCHLIST_GUIDED_STEPS,
  abstractionKindForCapability,
  capabilityScopeLabel,
  capabilityTypeLabel,
  discoveryFieldInput,
  newYorkSessionDate,
  normalizedDiscoveryCapability,
} from "../features/trading-configuration/contracts";
import {
  collectLifecycleRuleSetIds,
  normalizeDraft,
  normalizeStrategyProfileReferences,
  readSessionConfiguration,
  serializeDraft,
  serializeSessionDraft,
  writeSessionConfiguration,
} from "../features/trading-configuration/draft";
import {
  AddStepsEditor,
  DecisionRulesEditor,
  OrderIntentEditor,
  PhaseOrderEditor,
  RuleStageComposition,
  RuleStageEditor,
} from "../features/trading-configuration/strategy/RuleAuthoring";
type ConfigurationExperience = "guided" | "expert";
type OmsGuidedStage = "execution" | "protection";
type GuidedStep = TradingConfigurationSection | OmsGuidedStage | "canvas";

type Revision = {
  approved_at: string;
  content_hash: string;
  label: string;
  payload: Draft & { canvas: { profile: Record<string, unknown>; revision: string } };
  revision: number;
  revision_id: string;
};

const SECTION_META = {
  data_catalog: {
    eyebrow: "Data configuration · semantic authority",
    icon: Boxes,
    title: "Data Catalog",
    description: "Search and inspect every registered field, derivation, and signal used throughout the application.",
  },
  rule_sets: {
    eyebrow: "Data configuration · reusable decisions",
    icon: BookOpenCheck,
    title: "Rule Set Library",
    description: "Inspect built-in defaults and compose editable rule sets from exact registered Data Field outputs.",
  },
  discovery: {
    eyebrow: "QMD discovery authority",
    icon: ScanSearch,
    title: "Market Discovery",
    description: "Compose the Core Scan, mutable Watchlists, and append-only Signal Stream from registered Data Fields and Rule Sets.",
  },
  actions: {
    eyebrow: "System configuration · executable behavior",
    icon: Send,
    title: "Trading Actions",
    description: "Inspect atomic trading intents and compose reusable Action Policies from registered Rule Sets.",
  },
  strategy: {
    eyebrow: "Step 1 · Define behavior",
    icon: GitBranch,
    title: "Strategy Studio",
    description: "Compose a strategy lifecycle from registered Rule Sets, Trading Actions, and Action Policies.",
  },
  assignments: {
    eyebrow: "Step 5 · Assemble runtime",
    icon: Network,
    title: "Strategy Run Plans",
    description: "Bind a Strategy Profile to environments, action authority, OMS, and account mandates.",
  },
  portfolio: {
    eyebrow: "Step 3 · Govern capital",
    icon: BriefcaseBusiness,
    title: "Portfolio & Risk",
    description: "Define account risk limits, capital mandates, and replacement permissions.",
  },
  oms: {
    eyebrow: "Step 4 · Define execution",
    icon: ShieldCheck,
    title: "OMS & Protection",
    description: "Define reusable execution tactics, partial-fill behavior, and position protection.",
  },
  accounts: {
    eyebrow: "Step 2 · Bind accounts",
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
  const [registry, setRegistry] = useState<InformationRegistry | null>(null);
  const [label, setLabel] = useState("");
  const [status, setStatus] = useState<"loading" | "ready" | "saving" | "saved" | "error">("loading");
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"success" | "error">("success");
  const [discoveryRuntimeStatus, setDiscoveryRuntimeStatus] = useState<"idle" | "materializing" | "ready" | "error">("idle");
  const [discoveryRuntimeError, setDiscoveryRuntimeError] = useState("");
  const [experience, setExperienceState] = useState<ConfigurationExperience>("expert");
  const [showStudioHome, setShowStudioHome] = useState(false);
  const [omsGuidedStage, setOmsGuidedStageState] = useState<OmsGuidedStage>(() => readStoredOmsStage());
  const materializedDiscoveryBodyRef = useRef("");
  const meta = SECTION_META[section];
  const Icon = meta.icon;

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    const revisionsRequest = ["strategy", "revisions"].includes(section)
      ? api<{ rows: Revision[] }>("/api/trading/configuration/revisions")
      : Promise.resolve({ rows: [] as Revision[] });
    Promise.all([
      apiCached<Draft>("/api/trading/configuration/base", { timeoutMs: 20_000, ttlMs: 300_000 }),
      api<{ approved: Revision | null }>("/api/trading/configuration/approved"),
      revisionsRequest,
      apiCached<InformationRegistry>("/api/registries/definitions", { timeoutMs: 20_000, ttlMs: 300_000 }),
    ])
      .then(([nextDraft, approvedPayload, revisionPayload, registryPayload]) => {
        if (cancelled) return;
        setDraft(readSessionConfiguration(normalizeDraft(nextDraft)));
        setApproved(approvedPayload.approved ? { ...approvedPayload.approved, payload: normalizeDraft(approvedPayload.approved.payload) as Revision["payload"] } : null);
        setRevisions(revisionPayload.rows.map((row) => ({ ...row, payload: normalizeDraft(row.payload) as Revision["payload"] })));
        setRegistry(registryPayload);
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

  useEffect(() => {
    if (section !== "discovery" || !draft) return undefined;
    const body = JSON.stringify({ market_discovery: draft.market_discovery });
    if (body === materializedDiscoveryBodyRef.current) {
      setDiscoveryRuntimeStatus("ready");
      setDiscoveryRuntimeError("");
      return undefined;
    }
    const controller = new AbortController();
    setDiscoveryRuntimeStatus("materializing");
    setDiscoveryRuntimeError("");
    const timer = window.setTimeout(() => {
      api("/api/market-discovery/configuration/materialize", {
        body,
        method: "POST",
        signal: controller.signal,
        timeoutMs: 15000,
      })
        .then(() => {
          materializedDiscoveryBodyRef.current = body;
          setDiscoveryRuntimeStatus("ready");
        })
        .catch((reason) => {
          if (controller.signal.aborted) return;
          const detail = reason instanceof Error ? reason.message : String(reason);
          setDiscoveryRuntimeStatus("error");
          setDiscoveryRuntimeError(detail || "The backend rejected this draft without a diagnostic.");
        });
    }, 750);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [draft, section]);

  function updateDraft<K extends keyof Draft>(key: K, value: Draft[K]) {
    if (!draft) return;
    const next = { ...draft, [key]: value };
    try {
      writeSessionConfiguration(next);
    } catch (reason) {
      setMessageTone("error");
      setMessage(`This draft could not be saved in the browser session: ${reason instanceof Error ? reason.message : String(reason)}`);
      setStatus("error");
      return;
    }
    setDraft(next);
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

  async function publish(selectionId = "") {
    if (!draft) return;
    setStatus("saving");
    setMessage("");
    try {
      const canvas = canvasApprovalSnapshot();
      const selectedRunPlan = draft.assignments.deployments.find((row) => row.run_plan_id === selectionId)
        ?? draft.assignments.deployments.find((row) => row.profile_id === selectionId)
        ?? draft.assignments.deployments.find((row) => row.enabled)
        ?? draft.assignments.deployments[0];
      if (!selectedRunPlan) throw new Error("Configure a Run Plan before publishing.");
      const configuration = serializeDraft(draft);
      const revision = await api<Revision>("/api/trading/configuration/publish", {
        body: JSON.stringify({ canvas_profile: canvas.profile, canvas_revision: canvas.revision, configuration, label, run_plan_id: selectedRunPlan.run_plan_id }),
        method: "POST",
      });
      setApproved(revision);
      invalidateApiCache("/api/trading/configuration/base");
      invalidateApiCache("/api/market-discovery/configuration/presentation");
      clearConfigurationSession();
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

  function validateSession() {
    if (!draft) return;
    const failed = releaseReadiness(draft).filter((check) => !check.ready);
    const canvas = canvasApprovalSnapshot();
    if (!canvas.ready) failed.push({ detail: "configure at least one container", label: "Canvas", ready: false });
    setMessageTone(failed.length ? "error" : "success");
    setMessage(failed.length ? `Validation found ${failed.length} incomplete dependencies: ${failed.map((check) => check.label).join(", ")}.` : "Configuration graph is complete and ready for publication.");
    setStatus(failed.length ? "error" : "ready");
  }

  if (!draft || !registry) {
    return <div className="trading-configuration-page" data-configuration-experience={experience} data-configuration-section={section}>
      <header className="configuration-page-header">
        <div className="configuration-page-icon"><Icon size={20} /></div>
        <div className="configuration-page-heading"><span>{meta.eyebrow}</span><h1>{meta.title}</h1><p>{meta.description}</p></div>
      </header>
      {status === "error" ? <div className="configuration-message error"><TriangleAlert size={17} /><span>{message}</span></div> : <ConfigurationLoading />}
    </div>;
  }

  return (
    <DefinitionRegistryProvider registry={registry}>
    <div className="trading-configuration-page" data-configuration-experience={experience} data-configuration-section={section}>
      <header className="configuration-page-header">
        <div className="configuration-page-icon"><Icon size={20} /></div>
        <div className="configuration-page-heading">
          <span>{meta.eyebrow}</span>
          <h1>{meta.title}</h1>
          <p>{meta.description}</p>
        </div>
        <div className="configuration-header-controls">
          {draft ? <div className="configuration-session-state"><span>Session draft</span><strong>Schema v{draft.schema_version}</strong></div> : null}
          {draft && section === "discovery" ? <div className="configuration-session-state"><span>QMD materialization</span><strong title={discoveryRuntimeError || undefined}>{discoveryRuntimeStatus === "ready" ? "Runtime active" : discoveryRuntimeStatus === "error" ? "Invalid draft" : "Applying…"}</strong></div> : null}
          {draft && !["revisions", "data_catalog"].includes(section) ? <button className="button compact" onClick={validateSession} type="button"><BadgeCheck size={14} /> Validate</button> : null}
          {draft && !["revisions", "data_catalog"].includes(section) ? <a className="button compact primary" href="#revision-configuration">Review release <ChevronRight size={13} /></a> : null}
          <RevisionBadge approved={approved} />
        </div>
      </header>

      {section === "discovery" && discoveryRuntimeError ? <div className="configuration-message error"><TriangleAlert size={17} /><span>QMD did not apply this Market Discovery draft: {discoveryRuntimeError}</span></div> : null}

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

      {section === "data_catalog" ? <DataCatalogPage atomicFields={draft?.market_discovery.atomic_fields} dataFields={draft?.market_discovery.data_fields} onDataFieldsChange={draft ? (dataFields) => updateConfigurationBook({ ...draft, market_discovery: { ...draft.market_discovery, data_fields: dataFields } }) : undefined} registry={registry} /> : section === "rule_sets" && draft ? <RuleSetLibraryPage
        fields={dataFieldRuleDefinitions(draft.market_discovery.data_fields)}
        ruleSets={draft.market_discovery.rule_sets as DataRuleSet[]}
        onChange={(ruleSets) => updateConfigurationBook({
          ...draft,
          market_discovery: { ...draft.market_discovery, rule_sets: ruleSets as RuleSetDefinition[] },
        })}
      /> : section === "actions" && draft ? <TradingActionsPage
        onChange={(value) => updateDraft("trading_actions", value)}
        ruleSets={draft.market_discovery.rule_sets.map((ruleSet) => ({ description: ruleSet.description, name: ruleSet.name, rule_set_id: ruleSet.rule_set_id }))}
        section={draft.trading_actions}
      /> : (["assignments", "portfolio", "oms", "accounts"] as TradingConfigurationSection[]).includes(section) && draft ? (
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
          onChange={(value) => updateDraft(section as ConfigurableSection, value as never)}
          onDraftChange={updateConfigurationBook}
          section={section as ConfigurableSection}
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
            {section === "discovery" ? <MarketDiscoveryComposer onChange={(value) => updateDraft("market_discovery", value as MarketDiscoverySection)} section={draft.market_discovery as MarketDiscoveryConfiguration} /> : null}
          </div>
        </div>
      ) : <ConfigurationLoading />}
        </>
      )}
    </div>
    </DefinitionRegistryProvider>
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
  data_catalog: { authority: "Producer registries own data semantics; this page is read-only documentation.", outcome: "One complete searchable catalog of registered fields, derivations, and signals.", subjects: ["Semantic contracts", "Producer provenance", "Dependencies and parameters"] },
  rule_sets: { authority: "Rule sets reference exact registered Data Field outputs without copying their semantics.", outcome: "Named, described reusable decisions with locked built-in defaults and editable custom definitions.", subjects: ["Built-in defaults", "Custom rule sets", "Registered Data Field outputs"] },
  discovery: { authority: "QMD owns the broad universe, observations, candidate ranking, and point-in-time Watchlist membership.", outcome: "One visible Core Scan and reusable Watchlists selected by Strategies.", subjects: ["QMD capabilities", "Core Scan", "Watchlists and membership history"] },
  actions: { authority: "The Trading Action registry owns broker-neutral intent names; Action Policies reference Rule Sets and actions without copying either definition.", outcome: "One shared action vocabulary for Strategy, Canvas, Portfolio, OMS, and runtime.", subjects: ["Atomic actions", "Rule Set triggers", "Reusable Action Policies"] },
  strategy: { authority: "Strategy owns trading decisions. Portfolio sizes approved intent; OMS executes it.", outcome: "A reusable Strategy Profile with a Rule Set-driven lifecycle and explicit Trading Action routes.", subjects: ["Behavior and evaluation", "Entry, add, and reentry", "Strategic exits and Action Policies"] },
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
    { caption: "Bindings", key: "accounts", label: "Accounts" },
    { caption: "Capital", key: "portfolio", label: "Portfolio" },
    { caption: "Orders", key: "execution", label: "Execute" },
    { caption: "Stops", key: "protection", label: "Protect" },
    { caption: "Runtime", key: "assignments", label: "Run Plan" },
    { caption: "Workspace", key: "canvas", label: "Canvas" },
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
  const [activeRunPlanId, setActiveRunPlanId] = useState(() => window.sessionStorage.getItem("guided-run-plan-id") || draft.assignments.deployments.find((row) => row.enabled)?.run_plan_id || draft.assignments.deployments[0]?.run_plan_id || "");
  const [activeMandateId, setActiveMandateId] = useState(() => window.sessionStorage.getItem("guided-portfolio-mandate-id") || draft.portfolio.mandates[0]?.mandate_id || "");
  const [activeOmsProfileId, setActiveOmsProfileId] = useState(() => window.sessionStorage.getItem("guided-oms-profile-id") || draft.oms.profiles[0]?.profile_id || "");
  const [activeExecutionPolicyId, setActiveExecutionPolicyId] = useState(() => window.sessionStorage.getItem("guided-execution-policy-id") || draft.oms.profiles[0]?.settings.entry_execution_policy_id || "");
  const [activeAccountKey, setActiveAccountKey] = useState(() => window.sessionStorage.getItem("guided-account-key") || draft.accounts.bindings[0]?.account_key || "");
  const [activeSessionProfileId, setActiveSessionProfileId] = useState(() => window.sessionStorage.getItem("guided-session-profile-id") || draft.sessions.profiles.find((row) => row.enabled)?.session_profile_id || draft.sessions.profiles[0]?.session_profile_id || "");
  const [activeStrategyDeploymentId, setActiveStrategyDeploymentId] = useState(() => window.sessionStorage.getItem("guided-strategy-deployment-id") || draft.sessions.strategy_deployments.find((row) => row.enabled)?.strategy_deployment_id || draft.sessions.strategy_deployments[0]?.strategy_deployment_id || "");
  const profile = draft.strategy.profiles.find((row) => row.profile_id === activeStrategyProfileId) ?? draft.strategy.profiles.find((row) => row.profile_id === draft.strategy.default_profile_id) ?? draft.strategy.profiles[0];
  const deployment = draft.assignments.deployments.find((row) => row.run_plan_id === activeRunPlanId) ?? draft.assignments.deployments.find((row) => row.enabled) ?? draft.assignments.deployments[0];
  const mandate = draft.portfolio.mandates.find((row) => row.mandate_id === activeMandateId) ?? draft.portfolio.mandates.find((row) => row.run_plan_id === deployment?.run_plan_id) ?? draft.portfolio.mandates[0];
  const omsProfile = draft.oms.profiles.find((row) => row.profile_id === activeOmsProfileId) ?? draft.oms.profiles.find((row) => row.profile_id === deployment?.oms_profile_id) ?? draft.oms.profiles[0];
  const executionPolicy = draft.oms.execution_policies.find((row) => row.policy_id === activeExecutionPolicyId) ?? draft.oms.execution_policies.find((row) => row.policy_id === omsProfile?.settings.entry_execution_policy_id) ?? draft.oms.execution_policies[0];
  const protectionProfile = draft.oms.protection_profiles.find((row) => row.profile_id === omsProfile?.settings.protection_profile_id) ?? draft.oms.protection_profiles[0];
  const account = draft.accounts.bindings.find((row) => row.account_key === (section === "accounts" ? activeAccountKey : mandate?.account_key)) ?? draft.accounts.bindings[0];
  const sessionProfile = draft.sessions.profiles.find((row) => row.session_profile_id === activeSessionProfileId) ?? draft.sessions.profiles.find((row) => row.enabled) ?? draft.sessions.profiles[0];
  const strategyDeployment = draft.sessions.strategy_deployments.find((row) => row.strategy_deployment_id === activeStrategyDeploymentId) ?? draft.sessions.strategy_deployments.find((row) => row.enabled) ?? draft.sessions.strategy_deployments[0];
  const sessionRoutes = draft.sessions.execution_routes.filter((row) => row.session_profile_id === sessionProfile?.session_profile_id);
  const eligibleDeploymentRoutes = draft.sessions.execution_routes.filter((row) => row.session_profile_id === strategyDeployment?.session_profile_id);
  const selectedDeploymentRoutes = eligibleDeploymentRoutes.filter((row) => strategyDeployment?.execution_route_ids.includes(row.execution_route_id));
  const selectedDeploymentMandates = draft.portfolio.mandates.filter((row) => strategyDeployment?.portfolio_mandate_ids.includes(row.mandate_id));
  const deploymentRunPlan = draft.assignments.deployments.find((row) => row.run_plan_id === strategyDeployment?.run_plan_id);
  const deploymentStrategyProfile = draft.strategy.profiles.find((row) => row.profile_id === deploymentRunPlan?.profile_id);
  const deploymentCapitalRequest = deploymentStrategyProfile?.lifecycle.initial_entry.capital_request;
  const steps: GuidedStep[] = ["strategy", "accounts", "portfolio", "execution", "protection", "assignments", "revisions"];
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

  function selectRunPlan(runPlanId: string) {
    setActiveRunPlanId(runPlanId);
    window.sessionStorage.setItem("guided-run-plan-id", runPlanId);
  }

  function selectMandate(mandateId: string) {
    setActiveMandateId(mandateId);
    window.sessionStorage.setItem("guided-portfolio-mandate-id", mandateId);
  }

  function selectOmsProfile(profileId: string) {
    setActiveOmsProfileId(profileId);
    window.sessionStorage.setItem("guided-oms-profile-id", profileId);
  }

  function selectExecutionPolicy(policyId: string) {
    setActiveExecutionPolicyId(policyId);
    window.sessionStorage.setItem("guided-execution-policy-id", policyId);
  }

  function selectAccount(accountKey: string) {
    setActiveAccountKey(accountKey);
    window.sessionStorage.setItem("guided-account-key", accountKey);
  }

  function selectSessionProfile(sessionProfileId: string) {
    setActiveSessionProfileId(sessionProfileId);
    window.sessionStorage.setItem("guided-session-profile-id", sessionProfileId);
  }

  function selectStrategyDeployment(strategyDeploymentId: string) {
    setActiveStrategyDeploymentId(strategyDeploymentId);
    window.sessionStorage.setItem("guided-strategy-deployment-id", strategyDeploymentId);
  }

  function replaceSessionProfile(nextProfile: SessionProfile) {
    onChange("sessions", { ...draft.sessions, profiles: draft.sessions.profiles.map((row) => row.session_profile_id === nextProfile.session_profile_id ? nextProfile : row) });
  }

  function configureStrategyDeployment(routeIds: string[], requestedMode: Mandate["assignment_mode"]) {
    if (!strategyDeployment) return;
    const selectedRoutes = eligibleDeploymentRoutes.filter((route) => routeIds.includes(route.execution_route_id));
    const assignmentMode: Mandate["assignment_mode"] = selectedRoutes.length <= 1 ? "single" : requestedMode === "single" ? "replicated" : requestedMode;
    const ownedMandates = draft.portfolio.mandates.filter((row) => row.principal_kind === "strategy_deployment" && row.principal_id === strategyDeployment.strategy_deployment_id);
    const occupiedIds = draft.portfolio.mandates.map((row) => row.mandate_id);
    const nextMandates = selectedRoutes.map((route) => {
      const current = ownedMandates.find((row) => row.account_key === route.account_key);
      if (current) return { ...current, assignment_mode: assignmentMode, enabled: true };
      const mandate_id = uniqueId(`${strategyDeployment.run_plan_id}-${route.account_key}`, occupiedIds);
      occupiedIds.push(mandate_id);
      return {
        mandate_id,
        run_plan_id: strategyDeployment.run_plan_id,
        account_key: route.account_key,
        enabled: true,
        maximum_cash_fraction: .3,
        maximum_planned_risk_fraction: .01,
        maximum_positions: 10,
        assignment_mode: assignmentMode,
        allocation_weight: 1,
        maximum_action_authority: "confirm" as const,
        allow_replacement: false,
        minimum_replacement_improvement_pct: 20,
        principal_kind: "strategy_deployment" as const,
        principal_id: strategyDeployment.strategy_deployment_id,
      };
    });
    const ownedIds = new Set(ownedMandates.map((row) => row.mandate_id));
    const mandateIds = nextMandates.map((row) => row.mandate_id);
    onChange("portfolio", { ...draft.portfolio, mandates: [...draft.portfolio.mandates.filter((row) => !ownedIds.has(row.mandate_id)), ...nextMandates] });
    onChange("assignments", { ...draft.assignments, deployments: draft.assignments.deployments.map((row) => row.run_plan_id === strategyDeployment.run_plan_id ? { ...row, mandate_ids: [...row.mandate_ids.filter((value) => !ownedIds.has(value)), ...mandateIds] } : row) });
    onChange("sessions", { ...draft.sessions, strategy_deployments: draft.sessions.strategy_deployments.map((row) => row.strategy_deployment_id === strategyDeployment.strategy_deployment_id ? { ...row, execution_route_ids: selectedRoutes.map((route) => route.execution_route_id), portfolio_mandate_ids: mandateIds, system_generated: false } : row) });
  }

  function replaceDeploymentMandate(mandateId: string, patch: Partial<Mandate>) {
    onChange("portfolio", { ...draft.portfolio, mandates: draft.portfolio.mandates.map((row) => row.mandate_id === mandateId ? { ...row, ...patch } : row) });
  }

  function replaceDeployment(nextDeployment: StrategyRunPlan) {
    onChange("assignments", { ...draft.assignments, deployments: draft.assignments.deployments.map((row) => row.run_plan_id === deployment.run_plan_id ? nextDeployment : row) });
  }
  function replaceMandate(nextMandate: Mandate) {
    onChange("portfolio", { ...draft.portfolio, mandates: draft.portfolio.mandates.map((row) => row.mandate_id === mandate.mandate_id ? nextMandate : row) });
  }
  function replacePortfolioPolicy(nextPolicy: PortfolioPolicy) {
    const policyId = String(nextPolicy.policy_id ?? "");
    onChange("portfolio", { ...draft.portfolio, policies: draft.portfolio.policies.map((row) => String(row.policy_id ?? "") === policyId ? nextPolicy : row) });
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

  function addGuidedRunPlan() {
    const run_plan_id = uniqueId("new-run-plan", draft.assignments.deployments.map((row) => row.run_plan_id));
    const next: StrategyRunPlan = {
      run_plan_id,
      name: "New Run Plan",
      description: "",
      profile_id: draft.strategy.profiles[0]?.profile_id ?? "",
      oms_profile_id: draft.oms.profiles[0]?.profile_id ?? "",
      universe_id: "",
      watchlist_ids: draft.market_discovery.watchlists.filter((row) => row.enabled && row.availability !== "integration_pending").slice(0, 1).map((row) => row.watchlist_id),
      signal_stream_ids: draft.market_discovery.signal_streams.filter((row) => row.enabled).slice(0, 1).map((row) => row.signal_stream_id),
      activation: { event_policy: "new_occurrences", watchlist_policy: "any_selected" },
      enablement: { state: "disabled", scope: "persistent", effective_session: "" },
      canvas_profile_id: "current-canvas",
      data_plan_ids: { replay: "market.historical_scanner_materialization.v1" },
      source_revision_policy: "require_complete",
      book_id: "default",
      campaign_lifecycle: { initial_entry_authority: "confirm", reentry_authority: "confirm", exit_authority: "automatic", protective_exit_authority: "automatic", maximum_reentries: 3, reentry_cooldown_ms: 1000, maximum_initial_watch_ms: 0, session_end_behavior: "keep_watching", retain_ticker_while_paused: true },
      mandate_ids: [],
      enabled: false,
      allowed_environments: ["replay"],
      action_authority: { default: "confirm", initial_entry: "inherit", add: "inherit", reentry: "inherit", strategic_exit: "inherit", protective_exit: "automatic", emergency_exit: "automatic" },
      safety_supervisor: { enabled_by_environment: { replay: true, backtest: true, backtest_debug: true, paper: true, live: true } },
      runtime_assignments: [],
    };
    onChange("assignments", { ...draft.assignments, deployments: [...draft.assignments.deployments, next] });
    selectRunPlan(run_plan_id);
  }

  function cloneGuidedRunPlan() {
    const run_plan_id = uniqueId(`${deployment.run_plan_id}-copy`, draft.assignments.deployments.map((row) => row.run_plan_id));
    const linkedMandates = draft.portfolio.mandates.filter((row) => row.run_plan_id === deployment.run_plan_id);
    const clonedMandates = linkedMandates.map((row) => ({ ...deepClone(row), mandate_id: uniqueId(`${run_plan_id}-${row.account_key}`, [...draft.portfolio.mandates, ...linkedMandates].map((item) => item.mandate_id)), run_plan_id }));
    const next = { ...deepClone(deployment), run_plan_id, name: `${deployment.name} copy`, enabled: false, mandate_ids: clonedMandates.map((row) => row.mandate_id), runtime_assignments: [] };
    onChange("assignments", { ...draft.assignments, deployments: [...draft.assignments.deployments, next] });
    if (clonedMandates.length) onChange("portfolio", { ...draft.portfolio, mandates: [...draft.portfolio.mandates, ...clonedMandates] });
    selectRunPlan(run_plan_id);
  }

  function removeGuidedRunPlan() {
    if (deployment.enabled || draft.assignments.deployments.length <= 1) return;
    const deployments = draft.assignments.deployments.filter((row) => row.run_plan_id !== deployment.run_plan_id);
    onChange("assignments", { ...draft.assignments, deployments });
    onChange("portfolio", { ...draft.portfolio, mandates: draft.portfolio.mandates.filter((row) => row.run_plan_id !== deployment.run_plan_id) });
    selectRunPlan(deployments[0]?.run_plan_id ?? "");
  }

  function addGuidedAccount() {
    const account_key = uniqueId("account", draft.accounts.bindings.map((row) => row.account_key));
    const next: AccountBinding = { account_key, name: "New simulated account", source_account_id: account_key, account_class: "simulated", base_currency: "USD", session_key: account_key, portfolio_policy_id: String(draft.portfolio.policies[0]?.policy_id ?? "default"), enabled: false, modes: ["replay"], system_managed: false };
    onChange("accounts", { bindings: [...draft.accounts.bindings, next] });
    selectAccount(account_key);
  }

  function removeGuidedAccount() {
    const referencedByMandate = draft.portfolio.mandates.some((row) => row.account_key === account.account_key);
    const referencedByGroup = draft.portfolio.groups.some((row) => Array.isArray(row.account_keys) && row.account_keys.map(String).includes(account.account_key));
    if (account.system_managed || draft.accounts.bindings.length <= 1 || referencedByMandate || referencedByGroup) return;
    const bindings = draft.accounts.bindings.filter((row) => row.account_key !== account.account_key);
    onChange("accounts", { bindings });
    selectAccount(bindings[0]?.account_key ?? "");
  }

  function addGuidedMandate() {
    const sourceDeployment = draft.assignments.deployments[0];
    const sourceAccount = draft.accounts.bindings[0];
    if (!sourceDeployment || !sourceAccount) return;
    const mandate_id = uniqueId(`${sourceDeployment.run_plan_id}-${sourceAccount.account_key}`, draft.portfolio.mandates.map((row) => row.mandate_id));
    const next: Mandate = { mandate_id, run_plan_id: sourceDeployment.run_plan_id, account_key: sourceAccount.account_key, enabled: true, maximum_cash_fraction: .3, maximum_planned_risk_fraction: .01, maximum_positions: 10, assignment_mode: "single", allocation_weight: 1, maximum_action_authority: "confirm", allow_replacement: false, minimum_replacement_improvement_pct: 20 };
    onChange("portfolio", { ...draft.portfolio, mandates: [...draft.portfolio.mandates, next] });
    selectMandate(mandate_id);
  }

  function removeGuidedMandate() {
    if (draft.portfolio.mandates.length <= 1) return;
    const mandates = draft.portfolio.mandates.filter((row) => row.mandate_id !== mandate.mandate_id);
    onChange("portfolio", { ...draft.portfolio, mandates });
    selectMandate(mandates[0]?.mandate_id ?? "");
  }

  function cloneGuidedPortfolioPolicy() {
    if (!portfolioPolicy) return;
    const policy_id = uniqueId(`${String(portfolioPolicy.policy_id)}-copy`, draft.portfolio.policies.map((row) => String(row.policy_id)));
    const next = { ...deepClone(portfolioPolicy), policy_id, name: `${String(portfolioPolicy.name ?? portfolioPolicy.policy_id)} copy`, revision: 1 };
    onChange("portfolio", { ...draft.portfolio, policies: [...draft.portfolio.policies, next] });
    onChange("accounts", { bindings: draft.accounts.bindings.map((row) => row.account_key === account.account_key ? { ...account, portfolio_policy_id: policy_id } : row) });
  }

  function cloneGuidedOmsProfile() {
    const profile_id = uniqueId(`${omsProfile.profile_id}-copy`, draft.oms.profiles.map((row) => row.profile_id));
    const next = { ...deepClone(omsProfile), profile_id, name: `${omsProfile.name} copy`, description: `${omsProfile.description} Copy.`, origin: "user" as const, editable: true, revision: 1 };
    onChange("oms", { ...draft.oms, profiles: [...draft.oms.profiles, next] });
    selectOmsProfile(profile_id);
  }

  function cloneGuidedExecutionPolicy() {
    const policy_id = uniqueId(`${executionPolicy.policy_id}-copy`, draft.oms.execution_policies.map((row) => row.policy_id));
    const next = { ...deepClone(executionPolicy), policy_id, description: `${executionPolicy.description} Copy.`, origin: "user" as const, editable: true, revision: 1 };
    onChange("oms", { ...draft.oms, execution_policies: [...draft.oms.execution_policies, next] });
    selectExecutionPolicy(policy_id);
  }

  function cloneGuidedProtectionProfile() {
    const profile_id = uniqueId(`${protectionProfile.profile_id}-copy`, draft.oms.protection_profiles.map((row) => row.profile_id));
    const next = { ...deepClone(protectionProfile), profile_id, name: `${protectionProfile.name} copy`, description: `${protectionProfile.description} Copy.`, origin: "user" as const, editable: true, revision: 1 };
    onChange("oms", {
      ...draft.oms,
      profiles: draft.oms.profiles.map((row) => row.profile_id === omsProfile.profile_id ? { ...omsProfile, settings: { ...omsProfile.settings, protection_profile_id: profile_id } } : row),
      protection_profiles: [...draft.oms.protection_profiles, next],
    });
  }

  if (section === "revisions") return <GuidedReview approved={approved} draft={draft} label={label} onLabelChange={onLabelChange} onPublish={onPublish} onReturn={() => navigateGuidedStep("assignments", onOmsStageChange)} publishing={publishing} revisions={revisions} />;
  if (!profile || !deployment || !mandate || !omsProfile || !executionPolicy || !protectionProfile || !account) return <GuidedEmpty onSwitchToExpert={onSwitchToExpert} />;
  if (step === "strategy") return <GuidedStrategyConfiguration draft={draft} onChange={onChange} onContinue={() => onContinue("accounts")} onProfileChange={selectStrategyProfile} profile={profile} />;

  const questions: Array<ReactElement<{ label: string }>> = [];
  if (step === "assignments") questions.push(
    <GuidedQuestion description="A Run Plan is the publishable composition that connects reusable trading behavior, candidate membership, execution, governed accounts, data coverage, and the workspace used at runtime." key="run-plan-identity" label="Which Run Plan are you configuring?" status={!deployment.enabled ? "Draft" : deployment.enablement.state === "disabled" ? "Installed · disabled" : deployment.enablement.scope === "current_session" ? "Enabled this session" : "Enabled persistently"}>
      <div className="guided-form-grid">
        <SelectField help="Choose the runnable composition edited by the following questions." label="Run Plan" onChange={selectRunPlan} options={draft.assignments.deployments.map((row) => ({ description: `${!row.enabled ? "Draft" : row.enablement.state === "disabled" ? "Installed · disabled" : row.enablement.scope === "current_session" ? "Enabled this session" : "Enabled persistently"} · ${row.allowed_environments.map(readableLabel).join(", ") || "No modes"}`, label: row.name, value: row.run_plan_id }))} value={deployment.run_plan_id} />
        <div className="guided-inline-actions"><button className="button compact" onClick={addGuidedRunPlan} type="button"><Plus size={14} /> Add Run Plan</button><button className="button compact" onClick={cloneGuidedRunPlan} type="button"><Clipboard size={14} /> Clone</button><button className="button compact danger" disabled={deployment.enabled || draft.assignments.deployments.length <= 1} onClick={removeGuidedRunPlan} type="button"><Trash2 size={14} /> Remove</button></div>
        <TextField help="Operator-facing name shown in release review and runtime selection." label="Run Plan name" onChange={(name) => replaceDeployment({ ...deployment, name })} value={deployment.name} />
        <TextField help="Explain the complete trading purpose of this composition and where it is intended to run." label="Description" onChange={(description) => replaceDeployment({ ...deployment, description })} value={deployment.description} />
        <BooleanField help="Installed Run Plans remain selectable for configuration. Signal activation is controlled separately by Strategy enablement below." label="Run Plan installed" onChange={(enabled) => replaceDeployment({ ...deployment, enabled })} value={deployment.enabled} />
        <div className="configuration-fixed-value"><span>Stable Run Plan ID</span><strong>{deployment.run_plan_id}</strong><small>Published releases retain this identity and its frozen references.</small></div>
      </div>
    </GuidedQuestion>,
    <GuidedQuestion description="The Strategy Profile owns trading behavior and lifecycle decisions. Choosing it does not select symbols, capital, or broker behavior." key="deployment-strategy" label="Which Strategy Profile should this Run Plan execute?" status={deployment.enabled ? "Configured" : "Needs review"}>
      <SelectField help="Select the reusable trading behavior evaluated by this Run Plan. Its entries, adds, reentries, and strategic exits remain unchanged." label="Strategy Profile" onChange={(profile_id) => replaceDeployment({ ...deployment, profile_id })} options={draft.strategy.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={deployment.profile_id} />
    </GuidedQuestion>,
    <GuidedQuestion description="A Strategy Deployment binds this reusable Run Plan to a Session Profile and one or more Execution Routes. Enabled headless deployments keep running when no Canvas is open; Canvas may attach later by run ID." key="strategy-deployments" label="Where and how is this Run Plan deployed?" status={draft.sessions.strategy_deployments.some((row) => row.run_plan_id === deployment.run_plan_id && row.enabled) ? "Enabled" : "Disabled"}><div className="configuration-reference-grid">{draft.sessions.strategy_deployments.filter((row) => row.run_plan_id === deployment.run_plan_id).map((strategyDeployment) => <AbstractionCard compact control={<input checked={strategyDeployment.enabled} onChange={(event) => onChange("sessions", { ...draft.sessions, strategy_deployments: draft.sessions.strategy_deployments.map((row) => row.strategy_deployment_id === strategyDeployment.strategy_deployment_id ? { ...row, enabled: event.target.checked } : row) })} type="checkbox" />} description={strategyDeployment.description} identity={strategyDeployment.strategy_deployment_id} key={strategyDeployment.strategy_deployment_id} kind="processing_step" metadata={[{ label: "Session", value: draft.sessions.profiles.find((row) => row.session_profile_id === strategyDeployment.session_profile_id)?.name ?? strategyDeployment.session_profile_id }, { label: "Modes", value: strategyDeployment.modes.map(readableLabel).join(", ") }, { label: "Routes", value: strategyDeployment.execution_route_ids.length }, { label: "Runtime", value: strategyDeployment.headless ? "Headless" : "Launch only" }]} selected={strategyDeployment.enabled} status={strategyDeployment.enabled ? "Enabled" : "Disabled"} title={strategyDeployment.name} />)}</div>{!draft.sessions.strategy_deployments.some((row) => row.run_plan_id === deployment.run_plan_id) ? <div className="configuration-empty-state"><strong>No Strategy Deployment exists yet.</strong><span>Save this Run Plan, then bind it to a Session Profile and Execution Route.</span></div> : null}</GuidedQuestion>,
    <GuidedQuestion description="Signal Streams are the Strategy activation authority. Every new immutable occurrence carries the exact Rule Set revision and trigger-time evidence into Strategy evaluation." key="deployment-signal-streams" label="Which Signal Streams activate this Strategy?" status={deployment.signal_stream_ids.length ? "Configured" : "Needs decision"}>
      <div className="configuration-reference-grid">{draft.market_discovery.signal_streams.map((stream) => <AbstractionCard compact control={<input checked={deployment.signal_stream_ids.includes(stream.signal_stream_id)} disabled={!stream.enabled} onChange={() => replaceDeployment({ ...deployment, signal_stream_ids: deployment.signal_stream_ids.includes(stream.signal_stream_id) ? deployment.signal_stream_ids.filter((value) => value !== stream.signal_stream_id) : [...deployment.signal_stream_ids, stream.signal_stream_id] })} type="checkbox" />} description={stream.description} identity={stream.signal_stream_id} key={stream.signal_stream_id} kind="signal_stream" metadata={[{ label: "Rule sets", value: stream.inclusion_rule_sets.length }, { label: "Evidence columns", value: stream.columns.length }]} selected={deployment.signal_stream_ids.includes(stream.signal_stream_id)} status={!stream.enabled ? "Disabled" : deployment.signal_stream_ids.includes(stream.signal_stream_id) ? "Selected" : "Available"} title={stream.name} unavailable={!stream.enabled} />)}</div>
      <div className="guided-form-grid"><SelectField help="New occurrences avoids replaying earlier session signals when a Run Plan starts. Latest session occurrence may arm from the newest already-captured event." label="Occurrence policy" onChange={(event_policy) => replaceDeployment({ ...deployment, activation: { ...deployment.activation, event_policy: event_policy as StrategyRunPlan["activation"]["event_policy"] } })} options={[{ label: "New occurrences only", value: "new_occurrences" }, { label: "Latest session occurrence", value: "latest_session_occurrence" }]} value={deployment.activation.event_policy} /><SelectField help="Persistent remains enabled for subsequent sessions. Current session automatically expires at the session boundary." label="Strategy enablement" onChange={(value) => replaceDeployment({ ...deployment, enablement: value === "disabled" ? { state: "disabled", scope: deployment.enablement.scope, effective_session: "" } : { state: "enabled", scope: value as StrategyRunPlan["enablement"]["scope"], effective_session: value === "current_session" ? newYorkSessionDate() : "" } })} options={[{ label: "Enabled for subsequent sessions", value: "persistent" }, { label: "Enabled for current session", value: "current_session" }, { label: "Disabled", value: "disabled" }]} value={deployment.enablement.state === "disabled" ? "disabled" : deployment.enablement.scope} /></div>
    </GuidedQuestion>,
    <GuidedQuestion description="A Strategy Watchlist is an optional eligibility constraint. Leave it empty to accept any ticker emitted by the selected Signal Streams, or select one or more lists to restrict the candidates." key="deployment-watchlists" label="Should a Watchlist restrict eligible tickers?" status={deployment.watchlist_ids.length ? "Restricted" : "All signaled tickers"}>
      <div className="configuration-reference-grid">{draft.market_discovery.watchlists.map((watchlist) => <AbstractionCard compact control={<input checked={deployment.watchlist_ids.includes(watchlist.watchlist_id)} disabled={watchlist.availability === "integration_pending"} onChange={() => replaceDeployment({ ...deployment, watchlist_ids: deployment.watchlist_ids.includes(watchlist.watchlist_id) ? deployment.watchlist_ids.filter((value) => value !== watchlist.watchlist_id) : [...deployment.watchlist_ids, watchlist.watchlist_id] })} type="checkbox" />} description={watchlist.description} identity={watchlist.watchlist_id} key={watchlist.watchlist_id} kind="watchlist" metadata={[{ label: "Columns", value: watchlist.columns.length }, { label: "Maximum members", value: watchlist.maximum_size }]} selected={deployment.watchlist_ids.includes(watchlist.watchlist_id)} status={watchlist.availability === "integration_pending" ? "Unavailable" : deployment.watchlist_ids.includes(watchlist.watchlist_id) ? "Selected" : "Available"} title={watchlist.name} unavailable={watchlist.availability === "integration_pending"} />)}</div>
    </GuidedQuestion>,
    <GuidedQuestion description="The OMS profile supplies reusable execution and protection defaults after Portfolio approves quantity. It cannot change Strategy intent or Portfolio limits." key="deployment-oms" label="Which execution profile should the Run Plan use?" status="Configured">
      <SelectField help="Select the reusable OMS profile that resolves execution policy and protection defaults for this Run Plan." label="OMS profile" onChange={(oms_profile_id) => replaceDeployment({ ...deployment, oms_profile_id })} options={draft.oms.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={deployment.oms_profile_id} />
    </GuidedQuestion>,
    <GuidedQuestion description="The Run Plan sets the maximum authority for each Strategy action. Portfolio may narrow exposure-increasing authority, while protective and emergency exits remain automatic and cannot be weakened." key="deployment-authority" label="How much action authority may this Run Plan grant?" status="Configured">
      <CampaignPolicyEditor deployment={deployment} onChange={replaceDeployment} />
    </GuidedQuestion>,
    <GuidedQuestion description="Each runtime mode resolves through an explicit registered data plan. Historical modes should fail before the first event when required source coverage is incomplete." key="deployment-modes" label="Where may this Run Plan run, and with which data?" status={deployment.allowed_environments.length ? "Configured" : "Needs decision"}>
      <ModeSelector modes={deployment.allowed_environments} onChange={(allowed_environments) => replaceDeployment({ ...deployment, allowed_environments, data_plan_ids: { ...deployment.data_plan_ids, ...Object.fromEntries(allowed_environments.map((mode) => [mode, deployment.data_plan_ids[mode] ?? (mode === "paper" || mode === "live" ? "qmd.scanner.snapshot.v1" : "market.historical_scanner_materialization.v1")])) } })} />
      <div className="configuration-data-plan-grid">{deployment.allowed_environments.map((mode) => <div className="configuration-fixed-value" key={mode}><span>{readableLabel(mode)}</span><strong>{deployment.data_plan_ids[mode]}</strong><small>Registered query plan</small></div>)}</div>
      <div className="guided-form-grid"><SelectField help="Require complete is the fail-closed default. Allow partial is limited to explicitly reviewed research use." label="Source revision policy" onChange={(source_revision_policy) => replaceDeployment({ ...deployment, source_revision_policy: source_revision_policy as StrategyRunPlan["source_revision_policy"] })} options={[{ label: "Require complete", value: "require_complete" }, { label: "Allow partial (research only)", value: "allow_partial" }]} value={deployment.source_revision_policy} /><div className="configuration-fixed-value"><span>Portfolio book</span><strong>Default book</strong><small>The currently registered account book used by Portfolio mandates.</small></div></div>
    </GuidedQuestion>,
    <GuidedQuestion description="The safety supervisor is mandatory in Paper and Live. Historical modes may be configured explicitly so acceptance tests can verify both supervised and unsupervised behavior." key="deployment-safety" label="Which modes require the safety supervisor?" status="Fail closed">
      <div className="guided-form-grid">{(["replay", "backtest", "backtest_debug", "paper", "live"] as RuntimeMode[]).map((mode) => <BooleanField disabled={mode === "paper" || mode === "live"} help={mode === "paper" || mode === "live" ? "Mandatory for broker-connected operation." : "Controls the supervisor for this historical runtime mode."} key={mode} label={`${readableLabel(mode)} safety`} onChange={(enabled) => replaceDeployment({ ...deployment, safety_supervisor: { enabled_by_environment: { ...deployment.safety_supervisor.enabled_by_environment, [mode]: enabled } } })} value={deployment.safety_supervisor.enabled_by_environment[mode]} />)}</div>
    </GuidedQuestion>,
    <GuidedQuestion description="Portfolio mandates are the account-specific capital and risk authority for this Run Plan. They are configured on Portfolio & Risk and referenced here; the Run Plan cannot duplicate or override their limits." key="deployment-mandates" label="Which governed accounts are attached?" status={draft.portfolio.mandates.some((row) => row.run_plan_id === deployment.run_plan_id && row.enabled) ? "Configured" : "Needs mandate"}>
      <div className="configuration-reference-grid">{draft.portfolio.mandates.filter((row) => row.run_plan_id === deployment.run_plan_id).map((row) => <AbstractionCard compact description={`${accountName(draft.accounts, row.account_key)} · ${readableLabel(row.assignment_mode)} assignment`} identity={row.mandate_id} key={row.mandate_id} kind="portfolio_mandate" metadata={[{ label: "Maximum positions", value: row.maximum_positions }, { label: "Action authority", value: readableLabel(row.maximum_action_authority) }]} selected={row.enabled} status={row.enabled ? "Enabled" : "Disabled"} title={accountName(draft.accounts, row.account_key)} />)}</div>
      {!draft.portfolio.mandates.some((row) => row.run_plan_id === deployment.run_plan_id) ? <div className="configuration-empty-state"><strong>No Portfolio mandate is attached.</strong><span>Configure an account allocation before this Run Plan can approve capital.</span></div> : null}
      <div className="guided-inline-actions"><button className="button compact" onClick={() => { window.location.hash = "portfolio-configuration"; }} type="button"><ArrowRight size={14} /> Configure Portfolio mandates</button></div>
    </GuidedQuestion>,
  );
  const portfolioPolicy = draft.portfolio.policies.find((row) => String(row.policy_id ?? "") === account.portfolio_policy_id) ?? draft.portfolio.policies[0];
  const updatePortfolioPolicy = (key: string, value: Primitive | string[]) => portfolioPolicy && replacePortfolioPolicy({ ...portfolioPolicy, [key]: value });
  if (step === "portfolio") questions.push(
    <GuidedQuestion description="Choose the Run Plan-to-account mandate to configure, then connect it to the governed account and its account-wide safety policy." key="portfolio-account" label="Which Portfolio mandate are you configuring?" status={mandate.enabled ? "Enabled" : "Disabled"}><div className="guided-form-grid"><SelectField help="Each mandate gives one Run Plan bounded authority over one synchronized account." label="Portfolio mandate" onChange={selectMandate} options={draft.portfolio.mandates.map((row) => ({ description: `${deploymentName(draft.assignments, row.run_plan_id)} → ${accountName(draft.accounts, row.account_key)}`, label: deploymentName(draft.assignments, row.run_plan_id), value: row.mandate_id }))} value={mandate.mandate_id} /><div className="guided-inline-actions"><button className="button compact" onClick={addGuidedMandate} type="button"><Plus size={14} /> Add mandate</button><button className="button compact danger" disabled={draft.portfolio.mandates.length <= 1} onClick={removeGuidedMandate} type="button"><Trash2 size={14} /> Remove</button></div><SelectField help="The synchronized account whose cash, positions, buying power, reservations, and broker state govern every request." label="Account" onChange={(account_key) => replaceMandate({ ...mandate, account_key })} options={draft.accounts.bindings.map((row) => ({ description: `${readableLabel(row.account_class)} account`, label: row.name, value: row.account_key }))} value={mandate.account_key} /><SelectField help="The reusable account-level limits and permissions applied to this account before mandate-specific limits." label="Account safety policy" onChange={(portfolio_policy_id) => replaceAccount({ ...account, portfolio_policy_id })} options={draft.portfolio.policies.map((row) => ({ label: String(row.name ?? row.policy_id), value: String(row.policy_id) }))} value={account.portfolio_policy_id} /><BooleanField help="Disabled mandates remain saved but cannot approve new exposure for this Run Plan and account." label="Mandate enabled" onChange={(enabled) => replaceMandate({ ...mandate, enabled })} value={mandate.enabled} /></div></GuidedQuestion>,
    <GuidedQuestion description="These limits belong to this Strategy-account relationship. They narrow the account policy; they can never expand it." key="portfolio-mandate-limits" label="Set this Strategy's account allocation" status="Review limits"><div className="guided-form-grid"><NumberField help="Maximum fraction of otherwise available account cash that this mandate may use." label="Maximum cash fraction" maximum={1} minimum={0} onChange={(maximum_cash_fraction) => replaceMandate({ ...mandate, maximum_cash_fraction })} step={0.01} unit="fraction" value={mandate.maximum_cash_fraction} /><NumberField help="Maximum planned loss attributable to this mandate after open positions and reservations are included." label="Maximum planned loss" maximum={1} minimum={0} onChange={(maximum_planned_risk_fraction) => replaceMandate({ ...mandate, maximum_planned_risk_fraction })} step={0.001} unit="fraction" value={mandate.maximum_planned_risk_fraction} /><NumberField help="Maximum simultaneous open or reserved positions attributable to this mandate." label="Maximum positions" minimum={1} onChange={(maximum_positions) => replaceMandate({ ...mandate, maximum_positions })} step={1} unit="positions" value={mandate.maximum_positions} /></div></GuidedQuestion>,
    <GuidedQuestion description="Assignment controls how the same Strategy is distributed when more than one governed account is available." key="portfolio-assignment" label="Choose how capital is assigned" status="Configured"><div className="guided-form-grid"><SelectField help="Single uses one account; replicated mirrors requests; weighted divides by relative weight; partitioned assigns distinct capacity." label="Assignment mode" onChange={(assignment_mode) => replaceMandate({ ...mandate, assignment_mode: assignment_mode as Mandate["assignment_mode"] })} options={["single", "replicated", "weighted", "partitioned"].map((value) => ({ label: readableLabel(value), value }))} value={mandate.assignment_mode} />{mandate.assignment_mode === "weighted" ? <NumberField help="Relative share used when Portfolio distributes capacity across weighted mandates." label="Allocation weight" minimum={0.01} onChange={(allocation_weight) => replaceMandate({ ...mandate, allocation_weight })} step={0.1} value={mandate.allocation_weight} /> : null}</div></GuidedQuestion>,
    <GuidedQuestion description="Replacement is an explicit Portfolio proposal to reduce weaker exposure before funding a stronger request. It is never an implicit Strategy action." key="portfolio-replacement" label="Configure replacement proposals" status={mandate.allow_replacement ? "Enabled" : "Disabled"}><div className="guided-form-grid"><BooleanField help="Allow Portfolio to propose a risk-reducing sale or exit to free capacity for a stronger request." label="Allow replacement" onChange={(allow_replacement) => replaceMandate({ ...mandate, allow_replacement })} value={mandate.allow_replacement} />{mandate.allow_replacement ? <NumberField help="Minimum score improvement required before Portfolio may propose replacing existing exposure." label="Minimum improvement" minimum={0} onChange={(minimum_replacement_improvement_pct) => replaceMandate({ ...mandate, minimum_replacement_improvement_pct })} step={1} unit="%" value={mandate.minimum_replacement_improvement_pct} /> : null}</div></GuidedQuestion>,
    <GuidedQuestion description="This is the maximum authority for exposure-increasing actions. Risk-reducing and emergency actions remain governed separately." key="portfolio-authority" label="Set maximum action authority" status="Configured"><DecisionOptions onChange={(maximum_action_authority) => replaceMandate({ ...mandate, maximum_action_authority: maximum_action_authority as Mandate["maximum_action_authority"] })} options={[{ detail: "The operator initiates entries, adds, and reentries.", label: "Manual", value: "manual" }, { detail: "Portfolio prepares the action and waits for operator confirmation.", label: "Confirm", recommended: true, value: "confirm" }, { detail: "Exposure may increase automatically only while every policy and safety check passes.", label: "Automatic", value: "automatic" }]} value={mandate.maximum_action_authority} /></GuidedQuestion>,
    <GuidedQuestion description="The policy is reusable account-wide authority. Clone a policy before changing protected defaults, then give the revision a clear operator-facing name." key="portfolio-policy-identity" label="Which account safety policy are you configuring?" status={`Revision ${String(portfolioPolicy?.revision ?? 1)}`}><div className="guided-form-grid"><SelectField help="Selecting a policy also assigns it to the currently governed account." label="Account safety policy" onChange={(portfolio_policy_id) => replaceAccount({ ...account, portfolio_policy_id })} options={draft.portfolio.policies.map((row) => ({ label: String(row.name ?? row.policy_id), value: String(row.policy_id) }))} value={account.portfolio_policy_id} /><div className="guided-inline-actions"><button className="button compact" onClick={cloneGuidedPortfolioPolicy} type="button"><Clipboard size={14} /> Clone policy</button></div>{portfolioPolicy ? <><TextField help="Operator-facing policy name used throughout configuration and review." label="Policy name" onChange={(value) => updatePortfolioPolicy("name", value)} value={String(portfolioPolicy.name ?? portfolioPolicy.policy_id)} /><NumberField help="Revision frozen with the published release." label="Revision" minimum={1} onChange={(value) => updatePortfolioPolicy("revision", value)} step={1} unit="revision" value={Number(portfolioPolicy.revision ?? 1)} /><div className="configuration-fixed-value"><span>Stable policy ID</span><strong>{String(portfolioPolicy.policy_id)}</strong><small>Clone the policy to create a new identity.</small></div></> : null}</div></GuidedQuestion>,
    <GuidedQuestion description="The account policy first determines how much synchronized equity and buying power may participate at all." key="portfolio-capital-policy" label="Configure eligible account capital" status="Account policy"><div className="guided-form-grid">{portfolioPolicy ? <><NumberField help="Fraction of synchronized account equity available to all trading mandates." label="Eligible equity" maximum={1} minimum={0} onChange={(value) => updatePortfolioPolicy("eligible_equity_fraction", value)} step={0.01} unit="fraction" value={Number(portfolioPolicy.eligible_equity_fraction ?? 0)} /><NumberField help="Cash Portfolio must leave unused after approved reservations and orders." label="Minimum cash reserve" minimum={0} onChange={(value) => updatePortfolioPolicy("minimum_cash_reserve", value)} step={100} unit="currency" value={Number(portfolioPolicy.minimum_cash_reserve ?? 0)} /><NumberField help="Maximum fraction of broker buying power that all approved exposure may consume." label="Buying power utilization" maximum={1} minimum={0} onChange={(value) => updatePortfolioPolicy("maximum_buying_power_utilization", value)} step={0.01} unit="fraction" value={Number(portfolioPolicy.maximum_buying_power_utilization ?? 0)} /></> : null}</div></GuidedQuestion>,
    <GuidedQuestion description="Exposure ceilings are evaluated together. Portfolio reduces or rejects a request when any applicable account, ticker, Strategy, sector, industry, or correlated-group boundary would be crossed." key="portfolio-exposure-policy" label="Configure account exposure ceilings" status="Account policy"><div className="guided-form-grid">{portfolioPolicy ? [["maximum_gross_exposure", "Gross exposure", "Maximum absolute long plus short exposure.", "currency"], ["maximum_net_long_exposure", "Net long exposure", "Maximum directional long exposure.", "currency"], ["maximum_net_short_exposure", "Net short exposure", "Maximum directional short exposure.", "currency"], ["maximum_position_fraction", "Position ceiling", "Maximum account equity attributable to one position.", "fraction"], ["maximum_ticker_fraction", "Ticker ceiling", "Maximum account equity attributable to one ticker.", "fraction"], ["maximum_strategy_fraction", "Strategy ceiling", "Maximum eligible equity attributable to one Strategy.", "fraction"], ["maximum_sector_fraction", "Sector ceiling", "Maximum eligible equity attributable to one sector.", "fraction"], ["maximum_industry_fraction", "Industry ceiling", "Maximum eligible equity attributable to one industry.", "fraction"], ["maximum_correlated_group_fraction", "Correlated-group ceiling", "Maximum eligible equity attributable to one correlated group.", "fraction"]].map(([key, label, help, unit]) => <NumberField help={help} key={key} label={label} minimum={0} onChange={(value) => updatePortfolioPolicy(key, value)} step={unit === "fraction" ? 0.01 : 1000} unit={unit} value={Number(portfolioPolicy[key] ?? 0)} />) : null}</div></GuidedQuestion>,
    <GuidedQuestion description="Risk limits combine planned protective-stop loss, current open risk, order size, realized loss, and drawdown. A warning pauses new exposure; hard and emergency thresholds invoke stronger actions." key="portfolio-risk-policy" label="Configure account risk and loss limits" status="Account policy"><div className="guided-form-grid">{portfolioPolicy ? [["maximum_planned_risk_fraction", "Per-request planned risk", "Maximum planned loss admitted for one request.", "fraction", .001], ["maximum_open_risk_fraction", "Open risk ceiling", "Maximum aggregate planned loss across open and reserved exposure.", "fraction", .001], ["maximum_open_positions", "Open positions", "Maximum simultaneous positions under this account policy.", "positions", 1], ["maximum_order_quantity", "Order quantity", "Maximum approved shares in one request.", "shares", 1], ["maximum_order_notional", "Order notional", "Maximum worst-price notional for one request.", "currency", 1000], ["daily_loss_warning", "Daily loss warning", "Pause new exposure before the hard daily-loss limit.", "currency", 100], ["maximum_daily_loss", "Daily loss limit", "Block new exposure when realized and marked daily loss reaches this value.", "currency", 100], ["maximum_drawdown", "Drawdown limit", "Hard peak-to-trough account loss boundary.", "currency", 100], ["emergency_loss", "Emergency loss", "Escalate to the configured emergency action at this account loss.", "currency", 100]].map(([key, label, help, unit, increment]) => <NumberField help={String(help)} key={String(key)} label={String(label)} minimum={0} onChange={(value) => updatePortfolioPolicy(String(key), value)} step={Number(increment)} unit={String(unit)} value={Number(portfolioPolicy[String(key)] ?? 0)} />) : null}</div></GuidedQuestion>,
    <GuidedQuestion description="Permissions are hard policy gates. A Strategy or OMS selection cannot opt into behavior that the account policy disallows." key="portfolio-permissions" label="Configure account permissions" status="Account policy"><div className="guided-form-grid">{portfolioPolicy ? [["allow_long", "Allow long", "Permit new long exposure."], ["allow_short", "Allow short", "Permit new short exposure when the account supports it."], ["allow_margin", "Allow margin", "Permit margin use when the broker account supports it."], ["allow_unsettled_cash", "Allow unsettled cash", "Permit eligible unsettled cash in buying power."], ["allow_outside_rth", "Allow extended hours", "Permit orders outside regular trading hours."], ["allow_overnight", "Allow overnight", "Permit positions to remain open overnight."], ["block_on_unattributed_position", "Block unattributed positions", "Fail closed when broker positions cannot be attributed."], ["allow_stop_limit_protection", "Allow stop-limit protection", "Permit protection that may remain unfilled through a gap."], ["allow_partial_profit_pocket", "Allow partial profit pocket", "Permit a profit reduction that leaves a protected remainder."], ["allow_emergency_auto_liquidation", "Emergency auto-liquidation", "Authorize account-scoped emergency flattening after reconciliation."]].map(([key, label, help]) => <BooleanField help={help} key={key} label={label} onChange={(value) => updatePortfolioPolicy(key, value)} value={Boolean(portfolioPolicy[key])} />) : null}</div></GuidedQuestion>,
    <GuidedQuestion description="Allowlists constrain compatible instruments and OMS behaviors. Restricted symbols always lose to broader permission lists." key="portfolio-allowlists" label="Configure instrument and OMS allowlists" status="Account policy"><div className="guided-form-grid">{portfolioPolicy ? [["allowed_security_types", "Security types", "Allowed broker security types, such as STK."], ["allowed_currencies", "Currencies", "Allowed contract and ledger currencies."], ["restricted_symbols", "Restricted symbols", "Symbols Portfolio must reject even when a Strategy requests them."], ["allowed_execution_policies", "Execution policies", "OMS policy IDs allowed for this account; use * to allow all published policies."], ["allowed_protection_profiles", "Protection profiles", "Protection profile IDs allowed for this account; use * to allow all published profiles."]].map(([key, label, help]) => <TextField help={help} key={key} label={label} onChange={(value) => updatePortfolioPolicy(key, value.split(",").map((part) => part.trim()).filter(Boolean))} value={Array.isArray(portfolioPolicy[key]) ? (portfolioPolicy[key] as string[]).join(", ") : ""} />) : null}</div></GuidedQuestion>,
    <GuidedQuestion description="These limits control how fresh state must be and how quickly Portfolio must react when protection or account state changes." key="portfolio-operational" label="Configure freshness and reaction limits" status="Account policy"><div className="guided-form-grid">{portfolioPolicy ? <><NumberField help="Maximum broker-state age accepted before approving new exposure." label="Maximum snapshot age" minimum={0} onChange={(value) => updatePortfolioPolicy("maximum_snapshot_age_ms", value)} step={100} unit="ms" value={Number(portfolioPolicy.maximum_snapshot_age_ms ?? 0)} /><NumberField help="Maximum independently protected slices allowed for an entry or add." label="Maximum protection slices" minimum={1} onChange={(value) => updatePortfolioPolicy("maximum_protection_slices", value)} step={1} unit="slices" value={Number(portfolioPolicy.maximum_protection_slices ?? 1)} /><NumberField help="Maximum measured internal time allowed for a risk response." label="Maximum internal reaction" minimum={0} onChange={(value) => updatePortfolioPolicy("maximum_internal_reaction_ms", value)} step={10} unit="ms" value={Number(portfolioPolicy.maximum_internal_reaction_ms ?? 0)} /></> : null}</div></GuidedQuestion>,
    <GuidedQuestion description="Account groups apply shared gross and ticker exposure ceilings across several governed accounts. An account may participate only when its stable key is selected here." key="portfolio-groups" label="Configure shared account groups" status={`${draft.portfolio.groups.length} groups`}><div className="guided-entity-stack"><div className="guided-inline-actions"><button className="button compact" onClick={() => { const group_id = uniqueId("account-group", draft.portfolio.groups.map((row) => String(row.group_id))); onChange("portfolio", { ...draft.portfolio, groups: [...draft.portfolio.groups, { group_id, account_keys: [], maximum_gross_exposure: 0, maximum_ticker_exposure: 0 }] }); }} type="button"><Plus size={14} /> Add account group</button></div>{draft.portfolio.groups.map((group) => { const groupId = String(group.group_id); const accountKeys = Array.isArray(group.account_keys) ? group.account_keys as string[] : []; const replaceGroup = (next: ParameterMap) => onChange("portfolio", { ...draft.portfolio, groups: draft.portfolio.groups.map((row) => String(row.group_id) === groupId ? next : row) }); return <article className="guided-entity-card" key={groupId}><header><div><span>Account group</span><strong>{groupId}</strong></div><button aria-label={`Delete ${groupId}`} className="button compact danger" onClick={() => onChange("portfolio", { ...draft.portfolio, groups: draft.portfolio.groups.filter((row) => String(row.group_id) !== groupId) })} type="button"><Trash2 size={14} /> Remove</button></header><div className="guided-form-grid"><TextField help="Stable group identity referenced by Portfolio policy and audit evidence." label="Group ID" onChange={(value) => replaceGroup({ ...group, group_id: value })} value={groupId} /><NumberField help="Maximum aggregate absolute long plus short exposure across the selected accounts." label="Maximum gross exposure" minimum={0} onChange={(value) => replaceGroup({ ...group, maximum_gross_exposure: value })} step={1000} unit="currency" value={Number(group.maximum_gross_exposure ?? 0)} /><NumberField help="Maximum aggregate exposure to one ticker across the selected accounts." label="Maximum ticker exposure" minimum={0} onChange={(value) => replaceGroup({ ...group, maximum_ticker_exposure: value })} step={1000} unit="currency" value={Number(group.maximum_ticker_exposure ?? 0)} /></div><div className="guided-check-grid">{draft.accounts.bindings.map((binding) => <label key={binding.account_key}><input checked={accountKeys.includes(binding.account_key)} onChange={() => replaceGroup({ ...group, account_keys: accountKeys.includes(binding.account_key) ? accountKeys.filter((value) => value !== binding.account_key) : [...accountKeys, binding.account_key] })} type="checkbox" /><span><strong>{binding.name}</strong><small>{binding.account_key}</small></span></label>)}</div></article>; })}</div></GuidedQuestion>,
  );
  if (step === "execution") questions.push(
    <GuidedQuestion description="Choose the reusable OMS profile to configure. Run Plans reference its stable identity while the revision freezes its execution and protection defaults." key="oms-profile-identity" label="Which OMS profile are you configuring?" status={omsProfile.editable ? "Editable profile" : "Protected profile"}><div className="guided-form-grid"><SelectField help="Changing this selection changes which reusable OMS profile the walkthrough edits." label="OMS profile" onChange={selectOmsProfile} options={draft.oms.profiles.map((row) => ({ description: row.description, label: row.name, value: row.profile_id }))} value={omsProfile.profile_id} /><div className="guided-inline-actions"><button className="button compact" onClick={cloneGuidedOmsProfile} type="button"><Clipboard size={14} /> Clone profile</button></div><TextField help="Operator-facing profile name shown in Run Plans and review." label="Profile name" onChange={(name) => replaceOmsProfile({ ...omsProfile, name })} value={omsProfile.name} /><TextField help="Explain the execution and protection behavior this profile provides." label="Description" onChange={(description) => replaceOmsProfile({ ...omsProfile, description })} value={omsProfile.description} /><NumberField help="Revision frozen with the published configuration." label="Revision" minimum={1} onChange={(revision) => replaceOmsProfile({ ...omsProfile, revision })} step={1} unit="revision" value={omsProfile.revision} /><div className="configuration-fixed-value"><span>Stable OMS profile ID</span><strong>{omsProfile.profile_id}</strong><small>Clone the profile to create a new identity.</small></div></div></GuidedQuestion>,
    <GuidedQuestion description="The OMS profile groups default entry, exit, routing, and broker-held protection choices applied after Portfolio approves an action." key="execution-profile" label="Choose the profile's execution and protection defaults" status="Configured"><div className="guided-form-grid"><SelectField help="Default broker-neutral policy used to work approved entry quantity." label="Entry execution policy" onChange={(entry_execution_policy_id) => { replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, entry_execution_policy_id } }); selectExecutionPolicy(entry_execution_policy_id); }} options={draft.oms.execution_policies.map((row) => ({ description: row.description, label: readableLabel(row.name), value: row.policy_id }))} value={omsProfile.settings.entry_execution_policy_id} /><SelectField help="Default policy used for risk-reducing and final exits." label="Exit execution policy" onChange={(exit_execution_policy_id) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, exit_execution_policy_id } })} options={draft.oms.execution_policies.map((row) => ({ description: row.description, label: readableLabel(row.name), value: row.policy_id }))} value={omsProfile.settings.exit_execution_policy_id} /><SelectField help="Broker-held protection design attached to new fills unless a Strategy requests another allowed profile." label="Protection profile" onChange={(protection_profile_id) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, protection_profile_id } })} options={draft.oms.protection_profiles.map((row) => ({ description: row.description, label: row.name, value: row.profile_id }))} value={omsProfile.settings.protection_profile_id} /><div className="configuration-fixed-value"><span>Session routing</span><strong>{readableLabel(omsProfile.settings.session_routing)}</strong><small>Smart routing is the registered runtime authority for this schema revision.</small></div></div></GuidedQuestion>,
    <GuidedQuestion description="Urgency is a readable default for Strategy intent. The selected execution policy still supplies the hard price, time, and repricing envelope." key="execution-urgency" label="Configure default urgency and price increments" status="OMS profile"><div className="guided-form-grid"><SelectField help="Default urgency attached to entry intent when the Strategy does not override it." label="Entry urgency" onChange={(entry_urgency) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, entry_urgency } })} options={urgencyOptions()} value={omsProfile.settings.entry_urgency} /><SelectField help="Default urgency attached to risk-reducing and final exits." label="Exit urgency" onChange={(exit_urgency) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, exit_urgency } })} options={urgencyOptions()} value={omsProfile.settings.exit_urgency} /><NumberField help="Permitted limit-price offset from current execution evidence." label="Limit offset" minimum={0} onChange={(limit_offset_bps) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, limit_offset_bps } })} step={0.5} unit="bps" value={omsProfile.settings.limit_offset_bps} /><NumberField help="Minimum price increment the OMS planner uses when constructing or repricing orders." label="Tick size" minimum={0.0001} onChange={(tick_size) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, tick_size } })} step={0.01} unit="price" value={omsProfile.settings.tick_size} /></div></GuidedQuestion>,
    <GuidedQuestion description="These profile-level guardrails constrain Strategy-supplied invalidation before the independently versioned broker-held protection profile is resolved." key="execution-protection-guardrails" label="Configure profile-level protection guardrails" status="OMS profile"><div className="guided-form-grid"><SelectField help="How OMS combines causal structure and volatility evidence when the Strategy has not supplied a stricter boundary." label="Stop method" onChange={(stop_method) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, protection: { ...omsProfile.settings.protection, stop_method } } })} options={["structure", "volatility", "hybrid"].map((value) => ({ label: readableLabel(value), value }))} value={omsProfile.settings.protection.stop_method} /><NumberField help="Additional distance beyond a confirmed structural invalidation level." label="Structure buffer" minimum={0} onChange={(structure_buffer_bps) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, protection: { ...omsProfile.settings.protection, structure_buffer_bps } } })} step={0.5} unit="bps" value={omsProfile.settings.protection.structure_buffer_bps} /><NumberField help="Volatility distance used when the selected stop method requires it." label="Volatility multiple" minimum={0.01} onChange={(volatility_multiple) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, protection: { ...omsProfile.settings.protection, volatility_multiple } } })} step={0.05} unit="×" value={omsProfile.settings.protection.volatility_multiple} /><NumberField help="Maximum Strategy risk percentage accepted before Portfolio performs its own independent admission checks." label="Maximum risk" minimum={0} onChange={(maximum_risk_pct) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, protection: { ...omsProfile.settings.protection, maximum_risk_pct } } })} step={0.1} unit="%" value={omsProfile.settings.protection.maximum_risk_pct} /><BooleanField help="Allow resolved protection to tighten as favorable evidence develops; it can never loosen the hard stop." label="Trailing enabled" onChange={(trailing_enabled) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, protection: { ...omsProfile.settings.protection, trailing_enabled } } })} value={omsProfile.settings.protection.trailing_enabled} /></div></GuidedQuestion>,
    <GuidedQuestion description="Choose an execution policy to inspect and edit. This is the actual bounded behavior behind labels such as Patient, Balanced, or Adaptive." key="execution-behavior" label="Configure an execution policy" status={readableLabel(executionPolicy.name)}><div className="guided-form-grid"><SelectField help="Select any registered execution policy; this does not change the OMS profile's default until you assign it on the Defaults page." label="Execution policy" onChange={selectExecutionPolicy} options={draft.oms.execution_policies.map((row) => ({ description: row.description, label: readableLabel(row.name), value: row.policy_id }))} value={executionPolicy.policy_id} /><div className="guided-inline-actions"><button className="button compact" onClick={cloneGuidedExecutionPolicy} type="button"><Clipboard size={14} /> Clone policy</button></div><SelectField help="The bounded execution behavior implemented by OMS for this policy identity." label="Policy behavior" onChange={(name) => replaceExecutionPolicy({ ...executionPolicy, name })} options={["passive", "midpoint", "adaptive_patient", "adaptive_regular", "adaptive_urgent", "adaptive_very_urgent", "immediate_with_limit", "ibkr_native_adaptive", "cancel_if_not_filled"].map((value) => ({ label: readableLabel(value), value }))} value={executionPolicy.name} /><TextField help="Explain when this execution policy should be selected." label="Description" onChange={(description) => replaceExecutionPolicy({ ...executionPolicy, description })} value={executionPolicy.description} /><NumberField help="Revision frozen with the published release." label="Revision" minimum={1} onChange={(revision) => replaceExecutionPolicy({ ...executionPolicy, revision })} step={1} unit="revision" value={executionPolicy.revision} /><SelectField help="Authoritative quote feed used for pricing and repricing. A stale or unavailable feed fails closed." label="Quote source" onChange={(quote_source) => replaceExecutionPolicy({ ...executionPolicy, quote_source: quote_source as ExecutionPolicyConfig["quote_source"] })} options={["qmd", "ibkr", "simulated"].map((value) => ({ label: readableLabel(value), value }))} value={executionPolicy.quote_source} /><SelectField help="Action applied only to the broker-confirmed unfilled remainder after fills are reconciled." label="Partial-fill policy" onChange={(partial_fill_policy) => replaceExecutionPolicy({ ...executionPolicy, partial_fill_policy: partial_fill_policy as ExecutionPolicyConfig["partial_fill_policy"] })} options={[{ description: "Continue working only the confirmed remainder.", label: "Complete remainder", value: "complete_remainder" }, { description: "Keep the fill and stop pursuing the remainder.", label: "Accept partial", value: "accept_partial" }, { description: "Cancel the remainder while retaining confirmed fills.", label: "Cancel remainder", value: "cancel_remainder" }]} value={executionPolicy.partial_fill_policy} /><div className="configuration-fixed-value"><span>Stable execution policy ID</span><strong>{executionPolicy.policy_id}</strong><small>Clone the policy to create a new identity.</small></div></div></GuidedQuestion>,
    <GuidedQuestion description="Price bounds are hard policy limits. Empty values mean the Strategy or broker-compatible band supplies the boundary; they do not mean unbounded execution." key="execution-price-envelope" label="Configure the execution price envelope" status="Execution policy"><div className="guided-form-grid"><OptionalNumberField help="Hard ceiling for buy orders under this policy." label="Maximum buy price" minimum={0.0001} onChange={(maximum_buy_price) => replaceExecutionPolicy({ ...executionPolicy, envelope: { ...executionPolicy.envelope, maximum_buy_price } })} step={0.01} unit="price" value={executionPolicy.envelope.maximum_buy_price} /><OptionalNumberField help="Hard floor for sell orders under this policy." label="Minimum sell price" minimum={0.0001} onChange={(minimum_sell_price) => replaceExecutionPolicy({ ...executionPolicy, envelope: { ...executionPolicy.envelope, minimum_sell_price } })} step={0.01} unit="price" value={executionPolicy.envelope.minimum_sell_price} /></div></GuidedQuestion>,
    <GuidedQuestion description="The deadline and repricing limits define how long OMS may work an order and how frequently it may amend broker instructions." key="execution-timing" label="Configure deadline and repricing" status="Execution policy"><div className="guided-form-grid"><NumberField help="Maximum time OMS may work the approved intent before terminal policy is applied." label="Deadline" minimum={0} onChange={(deadline_ms) => replaceExecutionPolicy({ ...executionPolicy, envelope: { ...executionPolicy.envelope, deadline_ms } })} step={25} unit="ms" value={executionPolicy.envelope.deadline_ms} /><NumberField help="Maximum broker modifications allowed before terminal policy is applied." label="Maximum reprices" minimum={0} onChange={(maximum_reprices) => replaceExecutionPolicy({ ...executionPolicy, envelope: { ...executionPolicy.envelope, maximum_reprices } })} step={1} unit="replaces" value={executionPolicy.envelope.maximum_reprices} /><NumberField help="Minimum interval between amendments; broker fill events still wake OMS immediately." label="Minimum reprice interval" minimum={0} onChange={(minimum_reprice_interval_ms) => replaceExecutionPolicy({ ...executionPolicy, envelope: { ...executionPolicy.envelope, minimum_reprice_interval_ms } })} step={5} unit="ms" value={executionPolicy.envelope.minimum_reprice_interval_ms} /></div></GuidedQuestion>,
  );
  if (step === "protection") questions.push(
    <GuidedQuestion description="Protection stays broker-visible and independently reconciled while a position is open. Choose the reusable profile, clone protected defaults when necessary, and identify the behavior clearly." key="protection-profile" label="Which protection profile are you configuring?" status={protectionProfile.editable ? "Editable profile" : "Protected profile"}><div className="guided-form-grid"><SelectField help="Reusable broker-held stop, target, and trailing design attached to approved fills." label="Protection profile" onChange={(protection_profile_id) => replaceOmsProfile({ ...omsProfile, settings: { ...omsProfile.settings, protection_profile_id } })} options={draft.oms.protection_profiles.map((row) => ({ description: row.description, label: row.name, value: row.profile_id }))} value={omsProfile.settings.protection_profile_id} /><div className="guided-inline-actions"><button className="button compact" onClick={cloneGuidedProtectionProfile} type="button"><Clipboard size={14} /> Clone profile</button></div><TextField help="Operator-facing name shown in OMS configuration and Run Plans." label="Profile name" onChange={(name) => replaceProtectionProfile({ ...protectionProfile, name })} value={protectionProfile.name} /><TextField help="Explain the stop, target, trailing, and recovery design." label="Description" onChange={(description) => replaceProtectionProfile({ ...protectionProfile, description })} value={protectionProfile.description} /><NumberField help="Revision frozen with the published release." label="Revision" minimum={1} onChange={(revision) => replaceProtectionProfile({ ...protectionProfile, revision })} step={1} unit="revision" value={protectionProfile.revision} /><div className="configuration-fixed-value"><span>Stable protection profile ID</span><strong>{protectionProfile.profile_id}</strong><small>{protectionProfile.slices.length} configured slices</small></div></div></GuidedQuestion>,
    <GuidedQuestion description="These transitions define how OMS preserves or tightens protection after an add or an intentional profit reduction." key="protection-transitions" label="Configure protection transitions" status="Protection profile"><div className="guided-form-grid"><SelectField help="How an added fill changes protection already held for the open position." label="Add protection policy" onChange={(add_policy) => replaceProtectionProfile({ ...protectionProfile, add_policy })} options={["independent_slice", "inherit_position_stop", "rebase_all", "tighten_only", "preserve_existing"].map((value) => ({ label: readableLabel(value), value }))} value={protectionProfile.add_policy} /><SelectField help="What OMS does to protection on the remaining shares after a confirmed profit-pocket fill." label="Profit-pocket transition" onChange={(profit_pocket_transition) => replaceProtectionProfile({ ...protectionProfile, profit_pocket_transition })} options={["keep_existing", "move_to_breakeven", "lock_profit_price", "start_broker_trail", "start_volatility_trail", "start_swing_trail", "tighten_existing", "replan_remaining_slices", "full_exit_and_optional_reentry"].map((value) => ({ label: readableLabel(value), value }))} value={protectionProfile.profit_pocket_transition} /></div></GuidedQuestion>,
    <GuidedQuestion description="Create one to four protection slices and allocate exactly 100 percent of confirmed fills across them. Every slice is configured on the following stop and target pages." key="protection-slices" label="Configure protection slice allocation" status={`${protectionProfile.slices.length} slices`}><div className="guided-entity-stack"><div className="guided-inline-actions"><button className="button compact" disabled={protectionProfile.slices.length >= 4 || !protectionProfile.slices[0]} onClick={() => { const slice_id = uniqueId("slice", protectionProfile.slices.map((row) => row.slice_id)); const nextRows = [...protectionProfile.slices, { ...deepClone(protectionProfile.slices[0]), slice_id }].map((row) => ({ ...row, quantity_fraction: 1 / (protectionProfile.slices.length + 1) })); replaceProtectionProfile({ ...protectionProfile, slices: nextRows }); }} type="button"><Plus size={14} /> Add slice</button></div>{protectionProfile.slices.map((slice) => <article className="guided-entity-card" key={slice.slice_id}><header><div><span>Protection slice</span><strong>{slice.slice_id}</strong></div><button aria-label={`Delete ${slice.slice_id}`} className="button compact danger" disabled={protectionProfile.slices.length <= 1} onClick={() => { const remaining = protectionProfile.slices.filter((row) => row.slice_id !== slice.slice_id).map((row) => ({ ...row, quantity_fraction: 1 / (protectionProfile.slices.length - 1) })); replaceProtectionProfile({ ...protectionProfile, slices: remaining }); }} type="button"><Trash2 size={14} /> Remove</button></header><div className="guided-form-grid"><TextField help="Stable slice identity used in broker mappings and fill attribution." label="Slice ID" onChange={(slice_id) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, slice_id } : row) })} value={slice.slice_id} /><NumberField help="Fraction of the total confirmed fill protected by this slice." label="Quantity fraction" maximum={1} minimum={0.01} onChange={(quantity_fraction) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, quantity_fraction } : row) })} step={0.05} unit="fraction" value={slice.quantity_fraction} /></div></article>)}</div></GuidedQuestion>,
    ...protectionProfile.slices.map((slice, sliceIndex) => <GuidedQuestion description={`Configure the complete hard broker-held invalidation boundary for ${slice.slice_id}. This stop remains active independently of normal Strategy exit evaluation.`} key={`protection-stop-${slice.slice_id}`} label={`Configure stop ${sliceIndex + 1}: ${slice.slice_id}`} status={readableLabel(slice.stop.rule_type)}><div className="guided-form-grid"><SelectField help="Calculation used to resolve the hard stop from causal Strategy evidence or explicit policy values." label="Stop rule" onChange={(rule_type) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, rule_type } } : row) })} options={["fixed_price", "fixed_percent", "fixed_bps", "fixed_cash_risk", "swing_anchored", "volatility", "hybrid", "catastrophic"].map((value) => ({ label: readableLabel(value), value }))} value={slice.stop.rule_type} /><SelectField help="Broker-held order type. Stop-limit protection may remain unfilled through a gap and must be allowed by Portfolio policy." label="Stop order type" onChange={(order_type) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, order_type: order_type as ProtectionStopConfig["order_type"] } } : row) })} options={[{ label: "Stop market", value: "STP" }, { label: "Stop limit", value: "STOP_LIMIT" }]} value={slice.stop.order_type} />{["fixed_price", "catastrophic"].includes(slice.stop.rule_type) ? <OptionalNumberField help="Absolute stop price. Empty allows the Strategy's causal invalidation price." label="Stop price" minimum={0.0001} onChange={(price) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, price } } : row) })} step={0.01} unit="price" value={slice.stop.price} /> : null}{slice.stop.rule_type === "fixed_percent" ? <OptionalNumberField help="Percentage distance from the approved entry price." label="Stop distance" minimum={0} onChange={(distance_percent) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, distance_percent } } : row) })} step={0.1} unit="%" value={slice.stop.distance_percent} /> : null}{slice.stop.rule_type === "fixed_bps" ? <OptionalNumberField help="Basis-point distance from the approved entry price." label="Stop distance" minimum={0} onChange={(distance_bps) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, distance_bps } } : row) })} step={1} unit="bps" value={slice.stop.distance_bps} /> : null}{slice.stop.rule_type === "fixed_cash_risk" ? <OptionalNumberField help="Maximum cash loss assigned to this slice." label="Maximum cash risk" minimum={0} onChange={(maximum_cash_risk) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, maximum_cash_risk } } : row) })} step={10} unit="currency" value={slice.stop.maximum_cash_risk} /> : null}{["swing_anchored", "hybrid"].includes(slice.stop.rule_type) ? <><SelectField help="Causal confirmed swing selected from Strategy observation history." label="Swing ordinal" onChange={(anchor_ordinal) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, anchor_ordinal } } : row) })} options={["most_recent", "second_recent", "third_recent", "fourth_recent"].map((value) => ({ label: readableLabel(value), value }))} value={slice.stop.anchor_ordinal} /><TextField help="Timeframe recorded with the selected structural anchor." label="Structural timeframe" onChange={(structural_timeframe) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, structural_timeframe } } : row) })} value={slice.stop.structural_timeframe} /><NumberField help="Additional distance beyond the confirmed swing." label="Structure buffer" minimum={0} onChange={(buffer_bps) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, buffer_bps } } : row) })} step={0.5} unit="bps" value={slice.stop.buffer_bps} /></> : null}{["volatility", "hybrid"].includes(slice.stop.rule_type) ? <OptionalNumberField help="Volatility distance used to resolve the stop." label="Volatility multiple" minimum={0.01} onChange={(volatility_multiple) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, volatility_multiple } } : row) })} step={0.05} unit="×" value={slice.stop.volatility_multiple} /> : null}{slice.stop.order_type === "STOP_LIMIT" ? <OptionalNumberField help="Positive limit offset beyond the stop trigger." label="Stop-limit offset" minimum={0.01} onChange={(stop_limit_offset_bps) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, stop: { ...row.stop, stop_limit_offset_bps } } : row) })} step={0.5} unit="bps" value={slice.stop.stop_limit_offset_bps} /> : null}</div></GuidedQuestion>),
    ...protectionProfile.slices.map((slice, sliceIndex) => <GuidedQuestion description={`Configure the optional profit target and the trailing behavior for ${slice.slice_id}. These settings never loosen its hard stop.`} key={`protection-target-${slice.slice_id}`} label={`Configure target and trail ${sliceIndex + 1}`} status={readableLabel(slice.trailing.rule_type)}><div className="guided-form-grid"><BooleanField help="Resolve this slice's target from the Strategy's causal target evidence." label="Use Strategy profit target" onChange={(use_strategy_profit_target) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, use_strategy_profit_target } : row) })} value={slice.use_strategy_profit_target} />{!slice.use_strategy_profit_target ? <OptionalNumberField help="Optional absolute profit-target price for this slice." label="Profit target price" minimum={0.0001} onChange={(profit_target_price) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, profit_target_price } : row) })} step={0.01} unit="price" value={slice.profit_target_price} /> : null}<SelectField help="Trailing behavior activated after this slice reaches its configured gain threshold." label="Trailing rule" onChange={(rule_type) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, trailing: { ...row.trailing, rule_type } } : row) })} options={["none", "broker_amount", "broker_percent", "volatility_trail", "swing_trail", "chandelier", "breakeven_then_trail", "profit_lock_r", "time_tightening"].map((value) => ({ label: readableLabel(value), value }))} value={slice.trailing.rule_type} /><NumberField help="Gain required before the trailing rule becomes active." label="Trail activation gain" minimum={0} onChange={(activation_gain_percent) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, trailing: { ...row.trailing, activation_gain_percent } } : row) })} step={0.1} unit="%" value={slice.trailing.activation_gain_percent} /><NumberField help="Amount above entry protected when a breakeven transition activates." label="Breakeven buffer" minimum={0} onChange={(breakeven_buffer_bps) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, trailing: { ...row.trailing, breakeven_buffer_bps } } : row) })} step={0.5} unit="bps" value={slice.trailing.breakeven_buffer_bps} />{slice.trailing.rule_type === "broker_amount" ? <OptionalNumberField help="Broker-held trailing amount." label="Trail amount" minimum={0.0001} onChange={(amount) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, trailing: { ...row.trailing, amount } } : row) })} step={0.01} unit="price" value={slice.trailing.amount} /> : null}{slice.trailing.rule_type === "broker_percent" ? <OptionalNumberField help="Broker-held trailing percentage." label="Trail percent" minimum={0.01} onChange={(percent) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, trailing: { ...row.trailing, percent } } : row) })} step={0.1} unit="%" value={slice.trailing.percent} /> : null}{["volatility_trail", "chandelier", "breakeven_then_trail"].includes(slice.trailing.rule_type) ? <OptionalNumberField help="Volatility multiple used by the dynamic trailing boundary." label="Trail volatility" minimum={0.01} onChange={(volatility_multiple) => replaceProtectionProfile({ ...protectionProfile, slices: protectionProfile.slices.map((row) => row.slice_id === slice.slice_id ? { ...row, trailing: { ...row.trailing, volatility_multiple } } : row) })} step={0.05} unit="×" value={slice.trailing.volatility_multiple} /> : null}</div></GuidedQuestion>),
    <GuidedQuestion description="OMS continuously reconciles broker-held protection. These values bound repair time and determine whether a catastrophic fallback is mandatory." key="protection-recovery" label="Configure protection recovery" status={protectionProfile.mandatory_catastrophic_backstop ? "Fail closed" : "Review required"}><div className="guided-form-grid"><NumberField help="Maximum time allowed to repair missing or inconsistent broker-held protection." label="Emergency repair deadline" minimum={1} onChange={(emergency_repair_deadline_ms) => replaceProtectionProfile({ ...protectionProfile, emergency_repair_deadline_ms })} step={25} unit="ms" value={protectionProfile.emergency_repair_deadline_ms} /><BooleanField help="Require OMS to retain or immediately repair a catastrophic broker-held backstop." label="Mandatory catastrophic backstop" onChange={(mandatory_catastrophic_backstop) => replaceProtectionProfile({ ...protectionProfile, mandatory_catastrophic_backstop })} value={protectionProfile.mandatory_catastrophic_backstop} /></div></GuidedQuestion>,
  );
  if (step === "accounts") questions.push(
    <GuidedQuestion description="Choose one operational session first. Its clock and market-data authority determine which account routes are valid for manual, semi-automatic, and strategy execution." key="session-profiles" label="Choose the session and review its execution routes" status={sessionProfile?.enabled ? "Enabled" : "Disabled"}>
      <div className="accounts-session-configuration">
        <div className="guided-form-grid accounts-session-fields">
          <SelectField help="The selected Session Profile owns the runtime clock, data authority, and compatible account routes shown below." label="Session Profile" onChange={selectSessionProfile} options={draft.sessions.profiles.map((row) => ({ description: `${row.modes.map(readableLabel).join(", ")} · ${row.market_data.authority}`, label: row.name, value: row.session_profile_id }))} value={sessionProfile.session_profile_id} />
          <BooleanField help="Disabled sessions remain configured but cannot start a new manual or strategy runtime." label="Session enabled" onChange={(enabled) => replaceSessionProfile({ ...sessionProfile, enabled })} value={sessionProfile.enabled} />
          <BooleanField help="Allows manual orders and confirmed Trading Actions to use this session without requiring a Strategy Deployment." label="Manual and semi-automatic trading" onChange={(enabled) => replaceSessionProfile({ ...sessionProfile, manual_authority: { ...sessionProfile.manual_authority, enabled } })} value={sessionProfile.manual_authority.enabled} />
        </div>
        <dl className="accounts-session-facts">
          <div><dt>Market data</dt><dd>{sessionProfile.market_data.authority}</dd></div>
          <div><dt>Clock</dt><dd>{readableLabel(sessionProfile.market_data.clock)}</dd></div>
          <div><dt>Modes</dt><dd>{sessionProfile.modes.map(readableLabel).join(", ")}</dd></div>
          <div><dt>Recovery</dt><dd>{readableLabel(sessionProfile.recovery_policy)}</dd></div>
        </dl>
        <section className="accounts-route-register">
          <header><div><span>Execution routes</span><strong>{sessionRoutes.length} account route{sessionRoutes.length === 1 ? "" : "s"}</strong></div><small>Each route binds exactly one account, Portfolio mandate, and OMS profile.</small></header>
          <div className="accounts-route-table" role="table" aria-label={`${sessionProfile.name} execution routes`}>
            <div className="accounts-route-table-header" role="row"><span>Route and account</span><span>Portfolio</span><span>OMS</span><span>Access</span></div>
            {sessionRoutes.map((route) => {
              const routeAccount = draft.accounts.bindings.find((row) => row.account_key === route.account_key);
              const routeMandate = draft.portfolio.mandates.find((row) => row.mandate_id === route.portfolio_mandate_id);
              const routeOms = draft.oms.profiles.find((row) => row.profile_id === route.oms_profile_id);
              return <button className="accounts-route-table-row" key={route.execution_route_id} onClick={() => { selectAccount(route.account_key); setQuestionIndex(2); }} role="row" type="button">
                <span><strong>{route.name}</strong><small>{routeAccount?.name ?? route.account_key}</small></span>
                <span><strong>{routeMandate ? `${percent(routeMandate.maximum_cash_fraction)} maximum cash` : "Missing mandate"}</strong><small>{routeMandate ? readableLabel(routeMandate.maximum_action_authority) : route.portfolio_mandate_id}</small></span>
                <span><strong>{routeOms?.name ?? route.oms_profile_id}</strong><small>{route.modes.map(readableLabel).join(", ")}</small></span>
                <span><em data-state={route.enabled && routeAccount?.enabled ? "ready" : "disabled"}>{route.enabled && routeAccount?.enabled ? "Ready" : "Disabled"}</em><small>{route.manual_enabled ? "Manual allowed" : "Strategy only"}</small></span>
              </button>;
            })}
          </div>
        </section>
      </div>
    </GuidedQuestion>,
    <GuidedQuestion description="A Strategy Deployment may send the same broker-neutral intent to several account routes. Portfolio then calculates a separate safe quantity from each account's synchronized funds and mandate; OMS submits and reconciles each account independently." key="account-allocation" label="Configure multi-account strategy distribution" status={selectedDeploymentRoutes.length > 1 ? `${selectedDeploymentRoutes.length} accounts` : "Single account"}>
      {strategyDeployment ? <div className="accounts-allocation-configuration">
        <div className="guided-form-grid accounts-allocation-fields">
          <SelectField help="Choose the deployed Run Plan whose account distribution you want to configure." label="Strategy Deployment" onChange={selectStrategyDeployment} options={draft.sessions.strategy_deployments.map((row) => ({ description: `${draft.sessions.profiles.find((profileRow) => profileRow.session_profile_id === row.session_profile_id)?.name ?? row.session_profile_id} · ${row.headless ? "Headless capable" : "Launch controlled"}`, label: row.name, value: row.strategy_deployment_id }))} value={strategyDeployment.strategy_deployment_id} />
          <SelectField help="Replicated evaluates the same request independently in every selected account. Weighted scales each account by its relative weight. Partitioned reserves distinct capacity for deployment-specific routing." label="Distribution method" onChange={(value) => configureStrategyDeployment(value === "single" ? selectedDeploymentRoutes.slice(0, 1).map((row) => row.execution_route_id) : selectedDeploymentRoutes.map((row) => row.execution_route_id), value as Mandate["assignment_mode"])} options={[{ description: "One account route only.", label: "Single account", value: "single" }, { description: "Same intent; each account sizes from its own available funds and mandate.", label: "Proportional per account", value: "replicated" }, { description: "Same intent with a relative allocation weight per account.", label: "Weighted per account", value: "weighted" }, { description: "Reserve distinct account capacity for assigned work.", label: "Partitioned capacity", value: "partitioned" }]} value={selectedDeploymentMandates[0]?.assignment_mode ?? "single"} />
        </div>
        <div className="accounts-allocation-explanation">
          <GitBranch size={17} />
          <div><strong>One decision, account-specific quantities</strong><p>{deploymentCapitalRequest ? `The Strategy requests ${readableLabel(deploymentCapitalRequest.mode)}${deploymentCapitalRequest.mode === "all_available" ? "" : deploymentCapitalRequest.mode === "fixed_quantity" ? ` ${deploymentCapitalRequest.value} shares` : ` ${percent(deploymentCapitalRequest.value)}`}. Portfolio applies that request separately to each selected account's live funds, reservations, risk limits, and maximum cash allowance.` : "Choose a Strategy Deployment with a registered Strategy Profile to inspect its sizing request."}</p></div>
        </div>
        <fieldset className="accounts-route-choice-list"><legend>Accounts used by this Strategy Deployment</legend>
          {eligibleDeploymentRoutes.map((route) => {
            const routeAccount = draft.accounts.bindings.find((row) => row.account_key === route.account_key);
            const checked = strategyDeployment.execution_route_ids.includes(route.execution_route_id);
            const selectedOmsProfileId = selectedDeploymentRoutes[0]?.oms_profile_id;
            const incompatibleOms = Boolean(selectedOmsProfileId && selectedOmsProfileId !== route.oms_profile_id);
            const unavailable = !route.enabled || !routeAccount?.enabled || incompatibleOms;
            return <label data-selected={checked ? "true" : "false"} key={route.execution_route_id}>
              <input checked={checked} disabled={unavailable} onChange={(event) => configureStrategyDeployment(event.target.checked ? [...strategyDeployment.execution_route_ids, route.execution_route_id] : strategyDeployment.execution_route_ids.filter((value) => value !== route.execution_route_id), selectedDeploymentMandates[0]?.assignment_mode ?? "replicated")} type="checkbox" />
              <span><strong>{routeAccount?.name ?? route.account_key}</strong><small>{route.name} · {route.modes.map(readableLabel).join(", ")}</small></span>
              <em data-state={unavailable ? "disabled" : checked ? "selected" : "available"}>{incompatibleOms ? "Different OMS" : unavailable ? "Unavailable" : checked ? "Included" : "Available"}</em>
            </label>;
          })}
        </fieldset>
        {selectedDeploymentMandates.length ? <div className="accounts-allocation-limits">
          {selectedDeploymentMandates.map((deploymentMandate) => <section key={deploymentMandate.mandate_id}><header><div><span>Account mandate</span><strong>{accountName(draft.accounts, deploymentMandate.account_key)}</strong></div><small>{readableLabel(deploymentMandate.assignment_mode)}</small></header><div className="guided-form-grid"><NumberField help="Upper percentage of this account's available cash that the Strategy Deployment may request. Portfolio can still approve less or reject it." label="Maximum account funds" maximum={1} minimum={0} onChange={(maximum_cash_fraction) => replaceDeploymentMandate(deploymentMandate.mandate_id, { maximum_cash_fraction })} step={0.05} unit="fraction" value={deploymentMandate.maximum_cash_fraction} />{deploymentMandate.assignment_mode === "weighted" ? <NumberField help="Relative share used after each account's own funds and limits are evaluated." label="Allocation weight" minimum={0.01} onChange={(allocation_weight) => replaceDeploymentMandate(deploymentMandate.mandate_id, { allocation_weight })} step={0.1} unit="weight" value={deploymentMandate.allocation_weight} /> : null}</div></section>)}
        </div> : null}
        <div className="accounts-manual-boundary"><ShieldCheck size={16} /><p><strong>Manual and semi-automatic orders remain explicitly routed.</strong><span>Select one Execution Route when submitting a manual proposal. Multi-account fan-out is a Strategy Deployment behavior so every account retains an independent Portfolio approval and OMS audit trail.</span></p></div>
      </div> : <div className="configuration-empty-state"><strong>No Strategy Deployment is registered.</strong><span>Create a Run Plan and Strategy Deployment before configuring multi-account distribution.</span></div>}
    </GuidedQuestion>,
    <GuidedQuestion description="Choose one stable account binding, create a custom simulated account, or remove an unused custom binding. Every following page keeps this selection visible so account-specific values cannot be mistaken for session or deployment settings." key="account-selection" label="Which account binding are you configuring?" status={account.enabled ? "Enabled" : "Disabled"}><AccountConfigurationScope account={account} /><div className="guided-form-grid"><SelectField help="Changing this selection changes which account the following questions edit; it does not reassign any Portfolio mandate or Execution Route." label="Account binding" onChange={selectAccount} options={draft.accounts.bindings.map((row) => ({ description: `${row.system_managed ? "Managed broker binding" : "Custom binding"} · ${readableLabel(row.account_class)} · ${row.modes.map(readableLabel).join(", ")}`, label: row.name, value: row.account_key }))} value={account.account_key} /><div className="guided-inline-actions"><button className="button compact" onClick={addGuidedAccount} type="button"><Plus size={14} /> Add account</button><button className="button compact danger" disabled={Boolean(account.system_managed) || draft.accounts.bindings.length <= 1 || draft.portfolio.mandates.some((row) => row.account_key === account.account_key) || draft.portfolio.groups.some((row) => Array.isArray(row.account_keys) && row.account_keys.map(String).includes(account.account_key))} onClick={removeGuidedAccount} title={account.system_managed ? "Managed broker bindings are disabled instead of deleted." : draft.portfolio.mandates.some((row) => row.account_key === account.account_key) || draft.portfolio.groups.some((row) => Array.isArray(row.account_keys) && row.account_keys.map(String).includes(account.account_key)) ? "Remove this account from Portfolio mandates and groups first." : "Remove this custom account binding."} type="button"><Trash2 size={14} /> Remove account</button></div><TextField help="Operator-facing name shown in configuration, Run Plans, and runtime evidence." label="Account name" onChange={(name) => replaceAccount({ ...account, name })} value={account.name} /><BooleanField help="Disabled bindings remain saved but cannot be selected by a new runtime. Use this control to retire a managed broker binding." label="Account enabled" onChange={(enabled) => replaceAccount({ ...account, enabled })} value={account.enabled} /><div className="configuration-fixed-value"><span>Stable account key</span><strong>{account.account_key}</strong><small>{account.system_managed ? "Backend-managed broker identity; disable it when unused." : "Run Plans, mandates, and runtime state retain this custom identity."}</small></div></div></GuidedQuestion>,
    <GuidedQuestion description="These values belong only to the selected account. Account class and currency define broker capabilities and Portfolio's monetary unit; the selected policy remains the account-wide capital and risk authority." key="account-authority" label="How is this account governed?" status="Configured"><AccountConfigurationScope account={account} /><div className="guided-form-grid"><SelectField help="Determines broker capability and regulatory constraints." label="Account class" onChange={(account_class) => replaceAccount({ ...account, account_class })} options={["simulated", "paper", "cash", "margin", "registered"].map((value) => ({ label: readableLabel(value), value }))} value={account.account_class} /><SelectField help="Reusable account-level capital, exposure, loss, and permission policy." label="Portfolio policy" onChange={(portfolio_policy_id) => replaceAccount({ ...account, portfolio_policy_id })} options={draft.portfolio.policies.map((row) => ({ label: String(row.name ?? row.policy_id), value: String(row.policy_id) }))} value={account.portfolio_policy_id} /><TextField help="Currency used for Portfolio limits and account summaries." label="Base currency" onChange={(base_currency) => replaceAccount({ ...account, base_currency: base_currency.toUpperCase() })} value={account.base_currency} /></div></GuidedQuestion>,
    <GuidedQuestion description="These mode permissions belong only to the selected account. A Session Profile and Execution Route must also allow the mode before a runtime can use it." key="account-modes" label="Which modes may bind this account?" status={account.modes.length ? "Configured" : "Needs decision"}><AccountConfigurationScope account={account} /><ModeSelector modes={account.modes} onChange={(modes) => replaceAccount({ ...account, modes })} /></GuidedQuestion>,
    ...(account.modes.some((mode) => mode === "paper" || mode === "live") ? [<GuidedQuestion description="These broker-session values belong only to the selected account. The account ID is resolved by the backend environment, and preflight fails closed when it differs from IBKR discovery." key="account-broker" label="Confirm the broker account and gateway session" status={account.source_account_env && account.session_key.trim() ? "Needs broker verification" : "Invalid"}><AccountConfigurationScope account={account} /><div className="guided-form-grid"><div className="configuration-fixed-value"><span>IBKR account ID source</span><strong>{account.source_account_env || "Missing server-side binding"}</strong><small>The browser can select the stable account key but cannot enter or receive the broker account ID.</small></div><TextField help="Enter the configured gateway session identity that owns this account connection." label="Session key" onChange={(session_key) => replaceAccount({ ...account, session_key })} value={account.session_key} /></div></GuidedQuestion>] : [<GuidedQuestion description="These deterministic simulation values belong only to the selected account and do not grant broker access." key="account-simulated" label="Configure the simulated account session" status={account.source_account_id.trim() && account.session_key.trim() ? "Configured" : "Invalid"}><AccountConfigurationScope account={account} /><div className="guided-form-grid"><TextField help="Simulated runtime account identity used in broker-neutral account snapshots." label="Source account" onChange={(source_account_id) => replaceAccount({ ...account, source_account_id })} value={account.source_account_id} /><TextField help="Simulated session identity used to locate deterministic runtime state." label="Session key" onChange={(session_key) => replaceAccount({ ...account, session_key })} value={account.session_key} /></div></GuidedQuestion>]),
  );
  const questionCount = questions.length;
  const safeQuestionIndex = Math.min(questionIndex, Math.max(questionCount - 1, 0));
  const atFirstQuestion = safeQuestionIndex === 0;
  const atLastQuestion = safeQuestionIndex === questionCount - 1;
  const movePrevious = () => atFirstQuestion ? previous && navigateGuidedStep(previous, onOmsStageChange) : setQuestionIndex(safeQuestionIndex - 1);
  const moveNext = () => atLastQuestion ? next && onContinue(next) : setQuestionIndex(safeQuestionIndex + 1);

  const usesRightRail = step === "assignments" || step === "portfolio" || step === "execution" || step === "protection" || step === "accounts";
  return <div className={`guided-configuration-shell${usesRightRail ? " configuration-guided-drilldown" : ""}`} data-guided-step={step}>
    <div className="configuration-guided-step-navigation">
      <button aria-label="Previous configuration question" className="button compact configuration-guided-direction configuration-guided-direction-previous" disabled={atFirstQuestion && !previous} onClick={movePrevious} type="button"><ArrowLeft aria-hidden="true" size={15} /><span>Previous</span></button>
      {usesRightRail ? <div className="configuration-guided-flow-title"><span>{step === "assignments" ? "Strategy Run Plan" : step === "portfolio" ? "Portfolio and risk" : step === "execution" ? "OMS execution" : step === "protection" ? "OMS protection" : safeQuestionIndex < 2 ? "Sessions and account routing" : account.name}</span><strong>{questions[safeQuestionIndex]?.props.label}</strong></div> : <nav aria-label={`${readableLabel(step)} questions`} className="configuration-guided-question-tabs" style={{ gridTemplateColumns: `repeat(${questionCount}, minmax(0, 1fr))` }}>
        {questions.map((question, index) => <button aria-current={index === safeQuestionIndex ? "step" : undefined} key={question.key ?? index} onClick={() => setQuestionIndex(index)} title={question.props.label} type="button"><span>{index + 1}</span><strong>{question.props.label}</strong></button>)}
      </nav>}
      <button aria-label="Next configuration question" className="button compact primary configuration-guided-direction configuration-guided-direction-next" disabled={atLastQuestion && !next} onClick={moveNext} type="button"><span>Next</span><ArrowRight aria-hidden="true" size={15} /></button>
    </div>
    <main className="guided-question-surface">
      {usesRightRail ? <div className="strategy-entry-layout configuration-guided-drilldown-layout"><div className="guided-question-list strategy-entry-question-surface">{questions[safeQuestionIndex]}</div><nav aria-label={`${readableLabel(step)} detail pages`} className="strategy-entry-navigation configuration-guided-drilldown-navigation">{questions.map((question, index) => <button aria-current={index === safeQuestionIndex ? "step" : undefined} key={question.key ?? index} onClick={() => setQuestionIndex(index)} title={question.props.label} type="button"><span>{index + 1}</span><strong>{guidedQuestionRailLabel(String(question.key ?? ""), question.props.label)}</strong></button>)}</nav></div> : <div className="guided-question-list">{questions[safeQuestionIndex]}</div>}
    </main>
  </div>;
}

function guidedQuestionRailLabel(key: string, fallback: string) {
  const labels: Record<string, string> = {
    "run-plan-identity": "Plan", "deployment-strategy": "Strategy", "strategy-deployments": "Deployment", "deployment-signal-streams": "Signals", "deployment-watchlists": "Eligibility", "deployment-oms": "OMS", "deployment-authority": "Authority", "deployment-modes": "Modes & data", "deployment-safety": "Safety", "deployment-mandates": "Mandates", "deployment-canvas": "Canvas",
    "portfolio-account": "Account & policy", "portfolio-policy-identity": "Policy", "portfolio-mandate-limits": "Mandate limits", "portfolio-assignment": "Assignment", "portfolio-replacement": "Replacement", "portfolio-authority": "Authority", "portfolio-capital-policy": "Capital", "portfolio-exposure-policy": "Exposure", "portfolio-risk-policy": "Risk & loss", "portfolio-permissions": "Permissions", "portfolio-allowlists": "Allowlists", "portfolio-operational": "Operations", "portfolio-groups": "Account groups",
    "oms-profile-identity": "OMS profile", "execution-profile": "Defaults", "execution-urgency": "Urgency", "execution-protection-guardrails": "Guardrails", "execution-behavior": "Behavior", "execution-price-envelope": "Price bounds", "execution-timing": "Timing",
    "protection-profile": "Profile", "protection-transitions": "Transitions", "protection-slices": "Slices", "protection-recovery": "Recovery",
    "session-profiles": "Session & routes", "account-allocation": "Multi-account", "account-selection": "Account identity", "account-authority": "Governance", "account-modes": "Modes", "account-broker": "Broker session", "account-simulated": "Simulation",
  };
  if (key.startsWith("protection-stop-")) return "Hard stop";
  if (key.startsWith("protection-target-")) return "Target & trail";
  return labels[key] ?? fallback;
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
  const executableDefinitions = draft.strategy.definitions.filter((row) => row.executor_installed !== false);
  const [definitionId, setDefinitionId] = useState(profile.definition_id);
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
    const selectedDefinition = executableDefinitions.find((row) => row.strategy_id === definitionId);
    if (!selectedDefinition) return;
    const nextProfile = blankStrategyProfile(profile, draft, selectedDefinition);
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
    if (mode === "create") {
      setDefinitionId(executableDefinitions.find((row) => row.strategy_id === profile.definition_id)?.strategy_id ?? executableDefinitions[0]?.strategy_id ?? "");
      setProfileName(uniqueProfileName("Untitled Strategy", draft.strategy.profiles));
    }
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
      action_id: `position.add_${profile.lifecycle.trading_behavior.side}`,
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
      action: "close", action_id: `position.exit_${profile.lifecycle.trading_behavior.side}`, enabled: true, name: "New strategic exit", position_fraction: 1,
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
      content: <StrategyStartWorkflow cloneSourceId={cloneSourceId} definitionId={definitionId} mode={startMode} name={profileName} onClone={cloneExistingProfile} onCloneSourceChange={chooseCloneSource} onCreate={createNewProfile} onDefinitionChange={setDefinitionId} onModeChange={chooseStartMode} onNameChange={setProfileName} profiles={draft.strategy.profiles} section={draft.strategy} />,
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

function StrategyStartWorkflow({ cloneSourceId, definitionId, mode, name, onClone, onCloneSourceChange, onCreate, onDefinitionChange, onModeChange, onNameChange, profiles, section }: {
  cloneSourceId: string;
  definitionId: string;
  mode: "create" | "clone" | null;
  name: string;
  onClone: () => void;
  onCloneSourceChange: (profileId: string) => void;
  onCreate: () => void;
  onDefinitionChange: (strategyId: string) => void;
  onModeChange: (mode: "create" | "clone") => void;
  onNameChange: (value: string) => void;
  profiles: StrategyProfile[];
  section: StrategySection;
}) {
  const source = profiles.find((row) => row.profile_id === cloneSourceId);
  const normalizedName = name.trim().toLocaleLowerCase();
  const nameConflict = Boolean(normalizedName) && profiles.some((row) => row.name.trim().toLocaleLowerCase() === normalizedName);
  const invalidName = !source || !normalizedName || nameConflict;
  const executableDefinitions = section.definitions.filter((row) => row.executor_installed !== false);
  const selectedDefinition = executableDefinitions.find((row) => row.strategy_id === definitionId);
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
        <div className="strategy-start-name"><InventoryFilterSelect ariaLabel="Strategy executor" className="configuration-lookup-button" onChange={onDefinitionChange} options={executableDefinitions.map((definition) => ({ description: strategyExecutorDescription(definition), label: definition.name, value: definition.strategy_id }))} searchable={executableDefinitions.length > 7} showAllOnOpen value={definitionId} /><TextField help={nameConflict ? "Choose a name not already used by another Strategy Profile." : "You can refine this name in the next question."} label="Strategy name" nextAction onChange={onNameChange} value={name} />{nameConflict ? <span role="alert">A strategy with this name already exists.</span> : null}</div>
        <p className="strategy-executor-evidence">{selectedDefinition ? `${selectedDefinition.name} revision ${selectedDefinition.revision} is installed and will be pinned when this profile is published.` : "No installed Strategy executor is available. Deploy a registered executor before authoring a profile."}</p>
        <footer><button className="button primary" disabled={!normalizedName || nameConflict || !selectedDefinition} onClick={onCreate} type="button">Create blank strategy <ArrowRight size={15} /></button></footer>
      </NextActionArea>
    </section> : null}
    {mode === "clone" ? <section className="strategy-clone-workflow">
      <header><span>Clone existing</span><strong>Choose, inspect, then name the copy</strong><p>The source remains unchanged. The new strategy receives its own identity and can be revised in every following question.</p></header>
      <div className="strategy-clone-layout">
        <NextActionArea active={!source} className="strategy-clone-source-step" description="Choose one strategy to reveal its behavior, lifecycle, execution, and capabilities." focusKey="clone-source" title="Select a source strategy">
          <nav aria-label="Strategies available to clone">{profiles.map((row, index) => { const summary = strategySourceSummary(row); return <button aria-current={row.profile_id === source?.profile_id ? "true" : undefined} data-next-action-control={!source && index === 0 ? "true" : undefined} key={row.profile_id} onClick={() => onCloneSourceChange(row.profile_id)} type="button"><span><strong>{row.name}</strong><small>{row.protected ? "Protected system strategy" : "Editable strategy"}</small><em>{summary}</em></span><ChevronRight size={15} /></button>; })}</nav>
        </NextActionArea>
        <div className="strategy-clone-review">{source ? <>
          <StrategyProfileFeaturePreview profile={source} />
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
  const actionPolicies = profile.action_policy_ids.length;
  const adds = profile.lifecycle.initial_entry.add_steps.filter((row) => row.enabled).length;
  const exits = profile.lifecycle.exit.rule_sets.filter((row) => row.enabled).length;
  return `${readableLabel(profile.lifecycle.trading_behavior.side)} · ${actionPolicies} action policies · ${adds} adds · ${exits} exits`;
}

function StrategyProfileFeaturePreview({ profile }: { profile: StrategyProfile }) {
  const behavior = profile.lifecycle.trading_behavior;
  const initial = profile.lifecycle.initial_entry;
  const opportunityCount = countRuleReferences(initial.opportunity.expression);
  const confirmationCount = countRuleReferences(initial.confirmation.expression);
  const blockerCount = countRuleReferences(initial.blockers.expression);
  const addCount = initial.add_steps.filter((row) => row.enabled).length;
  const exitCount = profile.lifecycle.exit.rule_sets.filter((row) => row.enabled).length;
  const actionPolicies = profile.action_policy_ids.map(readableLabel);
  const executionPolicies = new Set([initial.order_intent.execution_policy, profile.lifecycle.reentry.order_intent.execution_policy, ...initial.add_steps.map((row) => row.order_intent.execution_policy), ...profile.lifecycle.exit.rule_sets.map((row) => row.order_intent.execution_policy)].filter(Boolean));
  return <div className="strategy-feature-preview">
    <header><span>{profile.protected ? "Protected source" : "Editable source"}</span><strong>{profile.name}</strong><p>{profile.description || "No strategy description has been provided."}</p></header>
    <dl>
      <div><dt>Trading behavior</dt><dd><strong>{readableLabel(behavior.side)}</strong><span>{behavior.eligible_sessions.map(readableLabel).join(", ")}</span></dd></div>
      <div><dt>Initial entry</dt><dd><strong>{opportunityCount} opportunity · {confirmationCount} confirmation</strong><span>{blockerCount} blocker rule set{blockerCount === 1 ? "" : "s"} · {readableLabel(initial.capital_request.mode)}</span></dd></div>
      <div><dt>Position lifecycle</dt><dd><strong>{addCount} add action{addCount === 1 ? "" : "s"} · {exitCount} strategic exit{exitCount === 1 ? "" : "s"}</strong><span>{profile.lifecycle.phase_modes.reentry === "automatic" ? `Reentry up to ${profile.lifecycle.reentry.maximum_attempts} times` : "Reentry manual"}</span></dd></div>
      <div><dt>Order behavior</dt><dd><strong>{executionPolicies.size} execution polic{executionPolicies.size === 1 ? "y" : "ies"}</strong><span>{readableLabel(initial.order_intent.partial_fill_policy)} · OMS applies tested terminal timing</span></dd></div>
    </dl>
    <section><span>Referenced action policies · {actionPolicies.length}</span>{actionPolicies.length ? <div>{actionPolicies.map((policy) => <strong key={policy}><CheckCircle2 size={13} />{policy}</strong>)}</div> : <p>No optional action policies are referenced.</p>}</section>
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

function GuidedExitActionFields({ actions = [], onChange, value }: { actions?: TradingActionDefinition[]; onChange: (value: ExitRuleSet) => void; value: ExitRuleSet }) {
  const supported = actions.filter((action) => ["exit", "reduce"].includes(action.category));
  return <div className="guided-form-grid">{supported.length ? <SelectField help="Registered broker-neutral intent emitted when this route passes." label="Trading Action" onChange={(action_id) => { const definition = supported.find((action) => action.action_id === action_id); onChange({ ...value, action: definition?.category === "reduce" ? "reduce" : "close", action_id }); }} options={supported.map((action) => ({ label: action.name, value: action.action_id }))} value={value.action_id} /> : <SelectField help="Choose whether this route requests the full current position or only a configured fraction." label="Position action" onChange={(action) => onChange({ ...value, action: action as ExitRuleSet["action"] })} options={[{ label: "Close the position", value: "close" }, { label: "Reduce the position", value: "reduce" }]} value={value.action} />}{value.action === "reduce" ? <NumberField help="Set the fraction of the reconciled current position that Strategy requests to release." label="Reduction fraction" maximum={1} minimum={.01} onChange={(position_fraction) => onChange({ ...value, position_fraction })} step={.05} unit="fraction" value={value.position_fraction} /> : <div className="guided-readonly-value"><span>Requested quantity</span><strong>Entire reconciled position</strong><small>Portfolio and OMS still verify the broker-authoritative current quantity.</small></div>}</div>;
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
    { label: "Action policies", value: `${profile.action_policy_ids.length} referenced` },
  ];
}

function countRuleReferences(expression?: RuleExpression): number {
  if (!expression) return 0;
  return expression.kind === "rule_set" ? 1 : expression.children.reduce((sum, child) => sum + countRuleReferences(child), 0);
}

function blankStrategyProfile(source: StrategyProfile, draft: Draft, definition?: StrategyDefinition): StrategyProfile {
  const profileId = uniqueId("new-strategy", draft.strategy.profiles.map((row) => row.profile_id));
  const emptyStage = (): RuleStage => ({ expression: { children: [], kind: "operator", operator: "and" } });
  const executionPolicy = draft.oms.execution_policies.find((row) => row.policy_id === "adaptive_regular")?.policy_id ?? draft.oms.execution_policies[0]?.policy_id ?? "";
  const protectionProfile = draft.oms.protection_profiles[0]?.profile_id ?? "";
  const capitalRequest: CapitalRequestConfig = { allow_replacement: false, mode: "mandate_fraction", value: 0.1 };
  const orderIntent: OrderIntentConfig = { deadline_ms: 750, execution_policy: executionPolicy, partial_fill_policy: "complete_remainder", protection_profile: protectionProfile };
  return {
    ...deepClone(source),
    action_policy_ids: [],
    capabilities: source.capabilities.map((row) => ({ ...deepClone(row), enabled: false })),
    description: "",
    definition_id: definition?.strategy_id ?? source.definition_id,
    definition_revision: definition?.revision ?? source.definition_revision,
    editable: true,
    enabled: false,
    rule_set_ids: [],
    lifecycle: {
      phase_modes: { initial_entry: "automatic", manage: "automatic", reentry: "automatic", exit: "automatic" },
      trading_behavior: { eligible_sessions: ["regular"], side: source.lifecycle.trading_behavior.side },
      initial_entry: { action_id: `position.enter_${source.lifecycle.trading_behavior.side}`, add_steps: [], blockers: emptyStage(), capital_request: deepClone(capitalRequest), confirmation: emptyStage(), opportunity: emptyStage(), order_intent: deepClone(orderIntent) },
      reentry: { action_id: `position.enter_${source.lifecycle.trading_behavior.side}`, capital_request: deepClone(capitalRequest), cooldown_ms: 0, enabled: true, maximum_attempts: 0, order_intent: deepClone(orderIntent), require_new_confirmation: true, rules: { blockers: emptyStage(), confirmation: emptyStage(), opportunity: emptyStage() } },
      exit: { rule_sets: [{ action: "close", action_id: `position.exit_${source.lifecycle.trading_behavior.side}`, enabled: false, name: "Strategic exit", order_intent: deepClone(orderIntent), position_fraction: 1, rule_set_id: "strategic-exit", rules: emptyStage(), summary: "Define the evidence that should close the position.", timing: { active_after_ms: 0, expires_after_ms: 0 } }] },
    },
    name: "Untitled Strategy",
    origin: "user",
    profile_id: profileId,
    protected: false,
    publication_status: "draft",
    derived_from_profile_id: "",
    revision: 1,
    parameters: definition ? strategyDefinitionParameters(definition) : deepClone(source.parameters),
  };
}

function strategyDefinitionParameters(definition: StrategyDefinition): ParameterMap {
  const parameters = deepClone(definition.parameter_defaults ?? {});
  delete parameters.entry_rules;
  delete parameters.phase_policy;
  delete parameters.strategy_behavior;
  return parameters;
}

function strategyExecutorDescription(definition: StrategyDefinition) {
  const key = definition.executor_key || `${definition.strategy_id}@${definition.revision}`;
  return definition.executor_schema_version
    ? `${key} · executor schema ${definition.executor_schema_version}`
    : `${key} · installed execution contract`;
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

function AccountConfigurationScope({ account }: { account: AccountBinding }) {
  return <section className="account-configuration-scope" aria-label={`Editing ${account.name}`}>
    <span><WalletCards aria-hidden="true" size={17} /></span>
    <div><small>Editing account</small><strong>{account.name}</strong><code>{account.account_key}</code></div>
    <dl><div><dt>Class</dt><dd>{readableLabel(account.account_class)}</dd></div><div><dt>Modes</dt><dd>{account.modes.map(readableLabel).join(", ") || "None"}</dd></div><div><dt>Status</dt><dd>{account.enabled ? "Enabled" : "Disabled"}</dd></div></dl>
  </section>;
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
  const [activeStage, setActiveStage] = useState<StrategyAuthoringStage>("identity");
  const [creationMode, setCreationMode] = useState<"blank" | null>(null);
  const [creationName, setCreationName] = useState("");
  const [creationDefinitionId, setCreationDefinitionId] = useState("");
  const selected = section.profiles.find((row) => row.profile_id === selectedId) ?? section.profiles[0];
  const ruleSets = draft.market_discovery.rule_sets;
  useEffect(() => {
    if (!section.profiles.some((row) => row.profile_id === selectedId)) setSelectedId(section.profiles[0]?.profile_id ?? "");
  }, [section.profiles, selectedId]);
  if (!selected) return <EmptyState title="No Strategy Profiles" detail="Create a profile from a registered strategy definition." />;
  function replaceProfile(next: StrategyProfile) {
    const normalized = normalizeStrategyProfileReferences(next);
    onChange({ ...section, profiles: section.profiles.map((row) => row.profile_id === selected.profile_id ? normalized : row) });
  }

  function cloneProfileFromSelection(profileId: string) {
    const source = section.profiles.find((row) => row.profile_id === profileId);
    if (!source) return;
    const next = cloneStrategyProfile(source, section.profiles, uniqueProfileName(`${source.name} copy`, section.profiles));
    onChange({ ...section, profiles: [...section.profiles, next] });
    setSelectedId(next.profile_id);
    setActiveStage("identity");
    setStudioView("configure");
  }

  function beginProfileCreation() {
    setCreationMode("blank");
    setCreationDefinitionId(section.definitions.find((row) => row.executor_installed !== false)?.strategy_id ?? "");
    setCreationName(uniqueProfileName("Untitled Strategy", section.profiles));
  }

  function createProfile() {
    const normalizedName = creationName.trim();
    if (!creationMode || !normalizedName || section.profiles.some((row) => row.name.trim().toLocaleLowerCase() === normalizedName.toLocaleLowerCase())) return;
    const definition = section.definitions.find((row) => row.executor_installed !== false && row.strategy_id === creationDefinitionId);
    if (!definition) return;
    const next = { ...blankStrategyProfile(selected, draft, definition), name: normalizedName };
    onChange({ ...section, profiles: [...section.profiles, next] });
    setSelectedId(next.profile_id);
    setActiveStage("identity");
    setStudioView("configure");
    setCreationMode(null);
    setCreationName("");
    setCreationDefinitionId("");
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
      setStudioView("select");
    } catch {
      // The parent displays the backend error and preserves the current profile.
    }
  }

  const advanced = flattenPrimitives(selected.parameters).filter((row) => (
    !LEGACY_ENTRY_LOGIC_PATHS.has(row.path) && isDirectlyEditableStrategyParameter(row.path, row.value)
  ));
  const entryRules = selected.lifecycle.initial_entry;
  const creationNameConflict = Boolean(creationName.trim()) && section.profiles.some((row) => row.name.trim().toLocaleLowerCase() === creationName.trim().toLocaleLowerCase());

  if (studioView === "select") return <StrategySelectionPage
    creationMode={creationMode}
    definitionId={creationDefinitionId}
    definitions={section.definitions}
    name={creationName}
    nameConflict={creationNameConflict}
    onCancel={() => { setCreationMode(null); setCreationName(""); setCreationDefinitionId(""); }}
    onCreate={createProfile}
    onCreateStart={beginProfileCreation}
    onDefinitionChange={setCreationDefinitionId}
    onDelete={(profileId) => void removeProfile(profileId)}
    onClone={cloneProfileFromSelection}
    onNameChange={setCreationName}
    onModify={(profileId) => { setSelectedId(profileId); setActiveStage("identity"); setStudioView("configure"); }}
    profiles={section.profiles}
  />;

  return (
    <div className="strategy-studio-workspace">
      <nav className="strategy-editor-toolbar" aria-label="Strategy editor navigation">
        <button className="button compact" onClick={() => setStudioView("select")} type="button"><ArrowLeft size={14} /> All strategies</button>
        <span><strong>{selected.name}</strong><small>Guided Configuration</small></span>
      </nav>
      <div className="configuration-workbench strategy-editor-guided">
      <main className="configuration-detail">
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
          onRuleSetEdit={(ruleSetId) => { window.location.hash = `rule-set-configuration?rule_set_id=${encodeURIComponent(ruleSetId)}`; }}
          onStageChange={setActiveStage}
          profile={selected}
          publishing={publishing}
          revisions={revisions}
          ruleSets={ruleSets}
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

          <StoryChapter marker="04" eyebrow="Position lifecycle" title="Configure adds, reentry, Action Policies, and strategic exits">
            <div className="strategy-book-prose">
              <p>Add steps request more exposure while a position is open. Reentry creates a new position after the campaign becomes flat. Action Policies reference registered Trading Actions and Rule Set triggers. Strategic exits reduce or close exposure when strategy evidence passes. All exposure-increasing actions repeat the authority and Portfolio checks; protective and emergency exits remain independent and automatic.</p>
            </div>
            <BookConfigurationSurface label="Configure position management, reentry, and exit">
              <AddStepsEditor catalog={section.input_catalog} eligibleSessions={selected.lifecycle.trading_behavior.eligible_sessions} executionPolicies={draft.oms.execution_policies} protectionProfiles={draft.oms.protection_profiles} steps={selected.lifecycle.initial_entry.add_steps} onChange={(add_steps) => replaceProfile({ ...selected, lifecycle: { ...selected.lifecycle, initial_entry: { ...selected.lifecycle.initial_entry, add_steps } } })} />
              <ActionPolicyBindingsEditor onChange={(action_policy_ids) => replaceProfile({ ...selected, action_policy_ids })} policies={draft.trading_actions.policies} selected={selected.action_policy_ids} />
              <ReentryEditor catalog={section.input_catalog} draft={draft} profile={selected} ruleSets={ruleSets} onChange={replaceProfile} />
              <ExitRuleSetsEditor catalog={section.input_catalog} draft={draft} profile={selected} ruleSets={ruleSets} onChange={replaceProfile} />
            </BookConfigurationSurface>
          </StoryChapter>

          <BookPart label="Part II" title="Configure runtime authority and dependencies" />

          <StoryChapter marker="05" eyebrow="Accounts and sessions" title="Configure where positions and orders can exist">
            <div className="strategy-book-prose">
              <p>An account binding maps a stable application account key to a simulated or broker session. Modes define where the binding may be used. They do not prove connectivity or broker readiness. Paper and Live still require backend account discovery, capability checks, session health, and safety preflight.</p>
            </div>
            <BookConfigurationSurface label="Configure account bindings">
              <AccountsEditor draft={draft} onChange={(accounts) => onDraftChange({ ...draft, accounts })} onSessionsChange={(sessions) => onDraftChange({ ...draft, sessions })} />
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
      </main>
    </div>
    </div>
  );
}

function StrategySelectionPage({ creationMode, definitionId, definitions, name, nameConflict, onCancel, onClone, onCreate, onCreateStart, onDefinitionChange, onDelete, onModify, onNameChange, profiles }: {
  creationMode: "blank" | null;
  definitionId: string;
  definitions: StrategyDefinition[];
  name: string;
  nameConflict: boolean;
  onCancel: () => void;
  onClone: (value: string) => void;
  onCreate: () => void;
  onCreateStart: () => void;
  onDefinitionChange: (strategyId: string) => void;
  onDelete: (value: string) => void;
  onModify: (value: string) => void;
  onNameChange: (value: string) => void;
  profiles: StrategyProfile[];
}) {
  const executableDefinitions = definitions.filter((row) => row.executor_installed !== false);
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
      <InventoryFilterSelect ariaLabel="Strategy executor" className="configuration-lookup-button" onChange={onDefinitionChange} options={executableDefinitions.map((definition) => ({ description: strategyExecutorDescription(definition), label: definition.name, value: definition.strategy_id }))} searchable={executableDefinitions.length > 7} showAllOnOpen value={definitionId} />
      <TextField help={nameConflict ? "This name is already used." : "You can change it later."} label="Strategy name" onChange={onNameChange} value={name} />
      <div><button className="button" onClick={onCancel} type="button">Cancel</button><button className="button primary" disabled={!name.trim() || nameConflict || !definitionId} onClick={onCreate} type="button">Create strategy <ArrowRight size={14} /></button></div>
      </div> : null}
    </section>
    <header className="strategy-selection-heading"><span>Available strategies</span><h2>Choose a strategy to configure</h2><p>The protected template and every strategy you create appear here.</p></header>
    <section className="strategy-selection-list abstraction-card-grid" aria-label="Available strategies">
      {profiles.map((profile) => <AbstractionCard actions={<span className="strategy-selection-actions">
          {profile.publication_status === "draft" && profile.origin === "user" && profile.editable ? <button className="strategy-profile-action primary" onClick={() => onModify(profile.profile_id)} type="button"><PencilLine size={14} /> Configure</button> : null}
          <button className="strategy-profile-action" onClick={() => onClone(profile.profile_id)} type="button"><Clipboard size={14} /> Duplicate</button>
          {profile.publication_status === "draft" && profile.origin === "user" && !profile.protected ? <button aria-label={`Delete ${profile.name}`} className="strategy-profile-action danger" onClick={() => onDelete(profile.profile_id)} title="Delete permanently" type="button"><Trash2 size={14} /> Delete</button> : null}
        </span>} className="strategy-profile-card" description={profile.description || "No description"} identity={profile.profile_id} key={profile.profile_id} kind="strategy_profile" metadata={[{ label: "Definition", value: profile.definition_id }, { label: "Revision", value: profile.revision }, { label: "Ownership", value: readableLabel(profile.origin) }]} status={profile.publication_status === "published" ? "Published" : profile.publication_status === "template" ? "Protected template" : "Draft"} title={profile.name} />)}
    </section>
  </main>;
}

function StrategyAuthoringFlow({ activeStage, advanced, approved, draft, entryRules, label, onLabelChange, onProfileChange, onPublish, onRuleSetEdit, onStageChange, profile, publishing, revisions, ruleSets, section }: {
  activeStage: StrategyAuthoringStage;
  advanced: Array<{ path: string; value: Primitive }>;
  approved: Revision | null;
  draft: Draft;
  entryRules: StrategyLifecycle["initial_entry"];
  label: string;
  onLabelChange: (value: string) => void;
  onProfileChange: (value: StrategyProfile) => void;
  onPublish: () => void;
  onRuleSetEdit: (ruleSetId: string, created?: RuleSetDefinition) => void;
  onStageChange: (value: StrategyAuthoringStage) => void;
  profile: StrategyProfile;
  publishing: boolean;
  revisions: Revision[];
  ruleSets: RuleSetDefinition[];
  section: StrategySection;
}) {
  const [activeEntryPage, setActiveEntryPage] = useState<EntryAuthoringPage>("mode");
  const [activeManagePage, setActiveManagePage] = useState<ManageAuthoringPage>("mode");
  const [activeReentryPage, setActiveReentryPage] = useState<ReentryAuthoringPage>("mode");
  const [activeExitPage, setActiveExitPage] = useState<ExitAuthoringPage>("mode");
  const [selectedAddStepId, setSelectedAddStepId] = useState(profile.lifecycle.initial_entry.add_steps[0]?.step_id ?? "");
  const [selectedExitRouteId, setSelectedExitRouteId] = useState(profile.lifecycle.exit.rule_sets[0]?.rule_set_id ?? "");
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
    ["overview", "2", "Observe", "Market context"],
    ["entry", "3", "Enter", "Evidence and request"],
    ["position", "4", "Manage", "Adds and action policies"],
    ["reentry", "5", "Reentry", "Flat-to-open rules"],
    ["exit", "6", "Exit", "Reduction conditions"],
    ["handoff", "7", "Review", "Behavior readiness"],
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
    const evidenceRuleSet = ruleSets[0];
    const next: AddStep = {
      action_id: `position.add_${profile.lifecycle.trading_behavior.side}`,
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
    const evidenceRuleSet = ruleSets[0];
    const next: ExitRuleSet = {
      action: "close", action_id: `position.exit_${profile.lifecycle.trading_behavior.side}`, enabled: true, name: "New strategic exit",
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
      {activeStage === "overview" ? <>
        <header className="strategy-identity-intro strategy-observe-intro"><h2>What market context does the strategy use?</h2></header>
        <div className="strategy-observe-fields"><TradingBehaviorEditor definition={definition} profile={profile} onChange={onProfileChange} /></div>
      </> : null}

      {activeStage === "entry" ? <>
        <header className="strategy-identity-intro strategy-entry-intro"><h2>{activeEntry.title}</h2><p>{activeEntry.description}</p></header>
        <div className="strategy-entry-layout">
          <div className="strategy-entry-question-surface">
            {activeEntryPage === "mode" ? <div className="strategy-entry-fields"><StrategyPhaseModeEditor mode={phaseModes.initial_entry} onChange={(mode) => replacePhaseMode("initial_entry", mode)} phase="Initial entry" /><SelectField help="Registered broker-neutral intent emitted after entry evidence passes." label="Trading Action" onChange={(action_id) => replaceInitialEntry({ action_id })} options={draft.trading_actions.definitions.filter((action) => action.category === "enter").map((action) => ({ label: action.name, value: action.action_id }))} value={entryRules.action_id} /></div> : null}
            {activeEntryRuleStage ? <DecisionRulesEditor catalog={section.input_catalog} onChange={(value) => replaceInitialEntry(value)} onRuleSetEdit={onRuleSetEdit} ruleSetCatalog={ruleSets} rules={entryRules} stageName={activeEntryRuleStage} title="Initial-entry evidence" summary="" /> : null}
            {activeEntryPage === "capital" ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => replaceInitialEntry({ capital_request })} segment="amount" value={entryRules.capital_request} /></div> : null}
            {activeEntryPage === "priority" ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => replaceInitialEntry({ capital_request })} segment="priority" value={entryRules.capital_request} /></div> : null}
            {activeEntryPage === "execution" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceInitialEntry({ order_intent })} segment="execution" value={entryRules.order_intent} /></div> : null}
            {activeEntryPage === "partial_fill" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceInitialEntry({ order_intent })} segment="partial-fill" value={entryRules.order_intent} /></div> : null}
            {activeEntryPage === "protection" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => replaceInitialEntry({ order_intent })} segment="protection" value={entryRules.order_intent} /></div> : null}
            {activeEntryPage === "initial_stop" ? <div className="configuration-field-grid strategy-entry-engine-fields">{entryStopParameters.map((item) => <ParameterField definition={field(item.path, labelForStrategyParameter(item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} onChange={(value) => onProfileChange({ ...profile, parameters: setPath(profile.parameters, item.path, value) })} value={item.value} />)}{!entryStopParameters.length ? <EmptyState detail="This strategy definition does not expose initial-stop engine parameters." title="No initial-stop parameters" /> : null}</div> : null}
          </div>
          <nav aria-label="Initial entry questions" className="strategy-entry-navigation">
            {ENTRY_AUTHORING_PAGES.map((page, index) => <button aria-current={page.id === activeEntryPage ? "step" : undefined} aria-label={page.label} disabled={phaseModes.initial_entry === "manual" && page.id !== "mode"} key={page.id} onClick={() => setActiveEntryPage(page.id)} title={page.label} type="button"><span>{index + 1}</span><strong>{page.label}</strong></button>)}
          </nav>
        </div>
      </> : null}

      {activeStage === "position" ? <>
        <header className="strategy-identity-intro strategy-entry-intro"><h2>{activeManage.title}</h2><p>{activeManage.description}</p></header>
        <ManageAuthoringSurface activeAddStep={activeAddStep} activePage={activeManagePage} draft={draft} enabledAdds={enabledAdds} entryRules={entryRules} mode={phaseModes.manage} onAddStep={addAddStep} onModeChange={(mode) => replacePhaseMode("manage", mode)} onPageChange={setActiveManagePage} onProfileChange={onProfileChange} onReplaceAddStep={replaceAddStep} onReplaceInitialEntry={replaceInitialEntry} onRuleSetEdit={onRuleSetEdit} onSelectedAddStepChange={setSelectedAddStepId} profile={profile} ruleSets={ruleSets} section={section} trailingParameters={trailingParameters} />
      </> : null}

      {activeStage === "reentry" ? <>
        <header className="strategy-identity-intro strategy-entry-intro"><h2>{activeReentry.title}</h2><p>{activeReentry.description}</p></header>
        <ReentryAuthoringSurface activePage={activeReentryPage} draft={draft} mode={phaseModes.reentry} onModeChange={(mode) => replacePhaseMode("reentry", mode)} onPageChange={setActiveReentryPage} onProfileChange={onProfileChange} onReplaceReentry={replaceReentry} onRuleSetEdit={onRuleSetEdit} profile={profile} ruleSets={ruleSets} section={section} />
      </> : null}

      {activeStage === "exit" ? <>
        <header className="strategy-identity-intro strategy-entry-intro"><h2>{activeExit.title}</h2><p>{activeExit.description}</p></header>
        <ExitAuthoringSurface activePage={activeExitPage} activeRoute={activeExitRoute} catalog={section.input_catalog} draft={draft} luldTargetParameters={luldTargetParameters} mode={phaseModes.exit} onAddRoute={addExitRoute} onModeChange={(mode) => replacePhaseMode("exit", mode)} onPageChange={setActiveExitPage} onProfileChange={onProfileChange} onReplaceRoute={replaceExitRoute} onRuleSetEdit={onRuleSetEdit} onSelectedRouteChange={setSelectedExitRouteId} profile={profile} profitPocketParameters={profitPocketParameters} ruleSets={ruleSets} />
      </> : null}

      {activeStage === "handoff" ? <>
        <StrategyStageIntro title="Review reusable strategy behavior">The Strategy Profile contains decision behavior only. Watchlists, accounts, Portfolio mandates, OMS, Canvas, data plans, environments, and action authority are selected later by a Run Plan.</StrategyStageIntro>
        <div className="strategy-publication-review">
          <article><span>Definition</span><strong>{profile.definition_id}@{profile.definition_revision}</strong></article>
          <article><span>Rule sets</span><strong>{collectLifecycleRuleSetIds(profile.lifecycle).length}</strong></article>
          <article><span>Action policies</span><strong>{profile.action_policy_ids.length} referenced</strong></article>
          <article><span>Entry mode</span><strong>{readableLabel(profile.lifecycle.phase_modes.initial_entry)}</strong></article>
          <article><span>Reentry mode</span><strong>{readableLabel(profile.lifecycle.phase_modes.reentry)}</strong></article>
          <article><span>Exit mode</span><strong>{readableLabel(profile.lifecycle.phase_modes.exit)}</strong></article>
        </div>
        <div className="strategy-safety-lock"><BadgeCheck size={19} /><div><strong>{profile.publication_status === "published" ? "Referenced by a published release" : "Available to Run Plans"}</strong><p>{profile.publication_status === "published" ? "Runtime results remain associated with this immutable revision. Clone it to change behavior." : "Continue to Accounts, Portfolio, OMS, and Run Plans to assemble a runnable configuration."}</p></div></div>
        <StrategyEngineParameterGroup items={remainingParameters} onChange={(path, value) => onProfileChange({ ...profile, parameters: setPath(profile.parameters, path, value) })} summary="Definition-specific values not assigned to another lifecycle step" title="Other engine parameters" />
      </> : null}
    </section>
  </article>;
}

function ManageAuthoringSurface({ activeAddStep, activePage, draft, enabledAdds, entryRules, mode, onAddStep, onModeChange, onPageChange, onProfileChange, onReplaceAddStep, onReplaceInitialEntry, onRuleSetEdit, onSelectedAddStepChange, profile, ruleSets, section, trailingParameters }: {
  activeAddStep?: AddStep;
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
  profile: StrategyProfile;
  ruleSets: RuleSetDefinition[];
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
          <div className="guided-form-grid"><TextField help="Operator-facing name for this position-building action." label="Action name" onChange={(name) => onReplaceAddStep(step.step_id, { ...step, name })} value={step.name} /><SelectField help="Registered broker-neutral intent emitted when this route passes." label="Trading Action" onChange={(action_id) => onReplaceAddStep(step.step_id, { ...step, action_id })} options={draft.trading_actions.definitions.filter((action) => action.category === "add").map((action) => ({ label: action.name, value: action.action_id }))} value={step.action_id} /><NumberField help="Maximum confirmed fills from this action during one campaign." label="Maximum uses" minimum={1} onChange={(maximum_uses) => onReplaceAddStep(step.step_id, { ...step, maximum_uses })} step={1} unit="fills" value={step.maximum_uses} /></div>
          <BooleanField help="Disabled actions remain saved but cannot emit an add request." label="Enabled" onChange={(enabled) => onReplaceAddStep(step.step_id, { ...step, enabled })} value={step.enabled} />
          <div className="strategy-guided-entity-actions"><button className="button compact" onClick={() => { onSelectedAddStepChange(step.step_id); onPageChange("add_evidence"); }} type="button">Configure</button><button className="button compact danger" onClick={() => onReplaceInitialEntry({ add_steps: entryRules.add_steps.filter((row) => row.step_id !== step.step_id) })} type="button"><Trash2 size={14} /> Remove</button></div>
        </article>)}
        {!entryRules.add_steps.length ? <EmptyState detail="Create an action before configuring its evidence, capital request, and execution." title="No position-add actions" /> : null}
      </div> : null}
      {activePage === "add_evidence" && activeAddStep ? <RuleStageComposition catalog={section.input_catalog} label={`${activeAddStep.name} evidence`} onChange={(rules) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, rules })} onEditRuleSet={onRuleSetEdit} ruleSets={ruleSets} stage={activeAddStep.rules} /> : null}
      {activePage === "add_capital" && activeAddStep ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, capital_request })} segment="amount" value={activeAddStep.capital_request} /></div> : null}
      {activePage === "add_replacement" && activeAddStep ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, capital_request })} segment="priority" value={activeAddStep.capital_request} /></div> : null}
      {activePage === "add_execution" && activeAddStep ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, order_intent })} segment="execution" value={activeAddStep.order_intent} /></div> : null}
      {activePage === "add_partial_fill" && activeAddStep ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, order_intent })} segment="partial-fill" value={activeAddStep.order_intent} /></div> : null}
      {activePage === "add_protection" && activeAddStep ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceAddStep(activeAddStep.step_id, { ...activeAddStep, order_intent })} segment="protection" value={activeAddStep.order_intent} /></div> : null}
      {addPageWithoutAction ? <EmptyState detail="Return to Add actions and create an action first." title="No add action selected" /> : null}
      {activePage === "trailing" ? <div className="configuration-field-grid strategy-entry-engine-fields">{trailingParameters.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").at(-1) ?? item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} onChange={(value) => onProfileChange({ ...profile, parameters: setPath(profile.parameters, item.path, value) })} value={item.value} />)}{!trailingParameters.length ? <EmptyState detail="This strategy definition does not expose trailing parameters." title="No trailing parameters" /> : null}</div> : null}
      {activePage === "action_policies" ? <ActionPolicyBindingsEditor onChange={(action_policy_ids) => onProfileChange({ ...profile, action_policy_ids })} policies={draft.trading_actions.policies} selected={profile.action_policy_ids} /> : null}
    </div>
    <nav aria-label="Position management questions" className="strategy-entry-navigation strategy-lifecycle-navigation">{MANAGE_AUTHORING_PAGES.map((page, index) => <button aria-current={page.id === activePage ? "step" : undefined} aria-label={page.label} disabled={mode === "manual" && page.id !== "mode"} key={page.id} onClick={() => onPageChange(page.id)} title={page.label} type="button"><span>{index + 1}</span><strong>{page.label}</strong></button>)}</nav>
  </div>;
}

function ActionPolicyBindingsEditor({ onChange, policies, selected }: {
  onChange: (policyIds: string[]) => void;
  policies: ActionPolicyDefinition[];
  selected: string[];
}) {
  const [policyToAdd, setPolicyToAdd] = useState("");
  const selectedPolicies = selected.map((policyId) => policies.find((policy) => policy.policy_id === policyId)).filter((policy): policy is ActionPolicyDefinition => Boolean(policy));
  const available = policies.filter((policy) => policy.enabled && !selected.includes(policy.policy_id));
  return <div className="strategy-action-policy-bindings">
    <header><div><span>Reusable behavior</span><strong>{selectedPolicies.length} Action Policies</strong><p>References only. The policy retains its registered Trading Action, Rule Set trigger, quantity, and authority.</p></div><a className="button compact" href="#trading-action-configuration">Open Trading Actions</a></header>
    <div className="strategy-action-policy-add"><InventoryFilterSelect ariaLabel="Action Policy to add" onChange={setPolicyToAdd} options={available.map((policy) => ({ description: policy.description, group: readableLabel(policy.category), label: policy.name, subgroup: readableLabel(policy.authority), value: policy.policy_id }))} placeholder="Choose an Action Policy" presentation="catalog" searchable searchPlaceholder="Search Action Policies" showAllOnOpen value={policyToAdd} /><button className="button compact" disabled={!policyToAdd} onClick={() => { onChange([...selected, policyToAdd]); setPolicyToAdd(""); }} type="button"><Plus size={13} /> Add</button></div>
    <div className="strategy-action-policy-list">{selectedPolicies.map((policy) => <article key={policy.policy_id}><div><span>{readableLabel(policy.authority)} · {readableLabel(policy.category)}</span><strong>{policy.name}</strong><p>{policy.description}</p><code>{policy.action_id}</code></div><button aria-label={`Remove ${policy.name}`} className="button compact danger" onClick={() => onChange(selected.filter((policyId) => policyId !== policy.policy_id))} type="button"><Trash2 size={13} /> Remove</button></article>)}</div>
    {!selectedPolicies.length ? <EmptyState detail="Add a registered Action Policy. Lifecycle entry, add, reentry, and exit routes remain configured separately from Rule Sets." title="No Action Policies referenced" /> : null}
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

function ReentryAuthoringSurface({ activePage, draft, mode, onModeChange, onPageChange, onProfileChange, onReplaceReentry, onRuleSetEdit, profile, ruleSets, section }: {
  activePage: ReentryAuthoringPage;
  draft: Draft;
  mode: StrategyPhaseMode;
  onModeChange: (mode: StrategyPhaseMode) => void;
  onPageChange: (page: ReentryAuthoringPage) => void;
  onProfileChange: (value: StrategyProfile) => void;
  onReplaceReentry: (value: StrategyLifecycle["reentry"]) => void;
  onRuleSetEdit: (ruleSetId: string, created?: RuleSetDefinition) => void;
  profile: StrategyProfile;
  ruleSets: RuleSetDefinition[];
  section: StrategySection;
}) {
  const reentry = profile.lifecycle.reentry;
  return <div className="strategy-entry-layout strategy-lifecycle-layout">
    <div className="strategy-entry-question-surface">
      {activePage === "mode" ? <div className="strategy-entry-fields"><StrategyPhaseModeEditor mode={mode} onChange={onModeChange} phase="Reentry" /><SelectField help="Registered broker-neutral intent emitted after reentry evidence passes." label="Trading Action" onChange={(action_id) => onReplaceReentry({ ...reentry, action_id })} options={draft.trading_actions.definitions.filter((action) => action.category === "enter").map((action) => ({ label: action.name, value: action.action_id }))} value={reentry.action_id} /></div> : null}
      {activePage === "reentry_policy" ? <div className="strategy-entry-fields"><div className="guided-form-grid"><BooleanField help="Require confirmation evidence newer than the evidence used by the previous confirmed entry." label="Require new confirmation" onChange={(require_new_confirmation) => onReplaceReentry({ ...reentry, require_new_confirmation })} value={reentry.require_new_confirmation} /><NumberField help="Minimum time after a confirmed full exit before reentry may become eligible." label="Cooldown" minimum={0} onChange={(cooldown_ms) => onReplaceReentry({ ...reentry, cooldown_ms })} step={100} unit="ms" value={reentry.cooldown_ms} /><NumberField help="Maximum confirmed reentry fills during one ticker campaign." label="Maximum attempts" minimum={0} onChange={(maximum_attempts) => onReplaceReentry({ ...reentry, maximum_attempts })} step={1} unit="entries" value={reentry.maximum_attempts} /></div></div> : null}
      {activePage === "reentry_opportunity" ? <DecisionRulesEditor catalog={section.input_catalog} onChange={(rules) => onReplaceReentry({ ...reentry, rules })} onRuleSetEdit={onRuleSetEdit} ruleSetCatalog={ruleSets} rules={reentry.rules} stageName="opportunity" summary="" title="Reentry evidence" /> : null}
      {activePage === "reentry_confirmation" ? <DecisionRulesEditor catalog={section.input_catalog} onChange={(rules) => onReplaceReentry({ ...reentry, rules })} onRuleSetEdit={onRuleSetEdit} ruleSetCatalog={ruleSets} rules={reentry.rules} stageName="confirmation" summary="" title="Reentry evidence" /> : null}
      {activePage === "reentry_blockers" ? <DecisionRulesEditor catalog={section.input_catalog} onChange={(rules) => onReplaceReentry({ ...reentry, rules })} onRuleSetEdit={onRuleSetEdit} ruleSetCatalog={ruleSets} rules={reentry.rules} stageName="blockers" summary="" title="Reentry evidence" /> : null}
      {activePage === "reentry_capital" ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => onReplaceReentry({ ...reentry, capital_request })} segment="amount" value={reentry.capital_request} /></div> : null}
      {activePage === "reentry_replacement" ? <div className="strategy-entry-fields"><GuidedCapitalRequestFields onChange={(capital_request) => onReplaceReentry({ ...reentry, capital_request })} segment="priority" value={reentry.capital_request} /></div> : null}
      {activePage === "reentry_execution" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceReentry({ ...reentry, order_intent })} segment="execution" value={reentry.order_intent} /></div> : null}
      {activePage === "reentry_partial_fill" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceReentry({ ...reentry, order_intent })} segment="partial-fill" value={reentry.order_intent} /></div> : null}
      {activePage === "reentry_protection" ? <div className="strategy-entry-fields"><GuidedOrderIntentFields draft={draft} eligibleSessions={profile.lifecycle.trading_behavior.eligible_sessions} onChange={(order_intent) => onReplaceReentry({ ...reentry, order_intent })} segment="protection" value={reentry.order_intent} /></div> : null}
    </div>
    <nav aria-label="Reentry questions" className="strategy-entry-navigation strategy-lifecycle-navigation">{REENTRY_AUTHORING_PAGES.map((page, index) => <button aria-current={page.id === activePage ? "step" : undefined} aria-label={page.label} disabled={mode === "manual" && page.id !== "mode"} key={page.id} onClick={() => onPageChange(page.id)} title={page.label} type="button"><span>{index + 1}</span><strong>{page.label}</strong></button>)}</nav>
  </div>;
}

function ExitAuthoringSurface({ activePage, activeRoute, catalog, draft, luldTargetParameters, mode, onAddRoute, onModeChange, onPageChange, onProfileChange, onReplaceRoute, onRuleSetEdit, onSelectedRouteChange, profile, profitPocketParameters, ruleSets }: {
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
  ruleSets: RuleSetDefinition[];
}) {
  const routeRequired = activePage !== "mode" && activePage !== "targets" && activePage !== "profit_pocket" && activePage !== "routes";
  return <div className="strategy-entry-layout strategy-lifecycle-layout">
    <div className="strategy-entry-question-surface">
      {activePage === "mode" ? <StrategyPhaseModeEditor mode={mode} onChange={onModeChange} phase="Strategic exit" /> : null}
      {activePage === "targets" ? <div className="configuration-field-grid strategy-entry-engine-fields">{luldTargetParameters.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").at(-1) ?? item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} onChange={(value) => onProfileChange({ ...profile, parameters: setPath(profile.parameters, item.path, value) })} value={item.value} />)}{!luldTargetParameters.length ? <EmptyState detail="This strategy definition does not expose LULD target parameters." title="No LULD target parameters" /> : null}</div> : null}
      {activePage === "profit_pocket" ? <div className="configuration-field-grid strategy-entry-engine-fields">{profitPocketParameters.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").at(-1) ?? item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} onChange={(value) => onProfileChange({ ...profile, parameters: setPath(profile.parameters, item.path, value) })} value={item.value} />)}{!profitPocketParameters.length ? <EmptyState detail="This strategy definition does not expose profit-pocket parameters." title="No profit-pocket parameters" /> : null}</div> : null}
      {activePage === "routes" ? <div className="strategy-guided-entity-list"><header><span>{profile.lifecycle.exit.rule_sets.filter((route) => route.enabled).length} enabled</span><button className="button compact" onClick={onAddRoute} type="button"><Plus size={14} /> Add route</button></header>{profile.lifecycle.exit.rule_sets.map((route) => <article data-selected={activeRoute?.rule_set_id === route.rule_set_id ? "true" : "false"} key={route.rule_set_id}><div className="guided-form-grid"><TextField help="Operator-facing name for this strategic exit route." label="Route name" onChange={(name) => onReplaceRoute(route.rule_set_id, { ...route, name })} value={route.name} /><TextField help="State the market condition and purpose handled by this route." label="Purpose" onChange={(summary) => onReplaceRoute(route.rule_set_id, { ...route, summary })} value={route.summary} /></div><BooleanField help="Disabled routes remain saved but cannot emit an exit request." label="Enabled" onChange={(enabled) => onReplaceRoute(route.rule_set_id, { ...route, enabled })} value={route.enabled} /><div className="strategy-guided-entity-actions"><button className="button compact" onClick={() => { onSelectedRouteChange(route.rule_set_id); onPageChange("evidence"); }} type="button">Configure</button><button className="button compact danger" disabled={profile.lifecycle.exit.rule_sets.length <= 1} onClick={() => onProfileChange({ ...profile, lifecycle: { ...profile.lifecycle, exit: { rule_sets: profile.lifecycle.exit.rule_sets.filter((row) => row.rule_set_id !== route.rule_set_id) } } })} type="button"><Trash2 size={14} /> Remove</button></div></article>)}</div> : null}
      {activePage === "evidence" && activeRoute ? <RuleStageComposition catalog={catalog} label={`${activeRoute.name} evidence`} onChange={(rules) => onReplaceRoute(activeRoute.rule_set_id, { ...activeRoute, rules })} onEditRuleSet={onRuleSetEdit} ruleSets={ruleSets} stage={activeRoute.rules} /> : null}
      {activePage === "timing" && activeRoute ? <div className="strategy-entry-fields"><GuidedExitTimingFields onChange={(route) => onReplaceRoute(activeRoute.rule_set_id, route)} value={activeRoute} /></div> : null}
      {activePage === "action" && activeRoute ? <div className="strategy-entry-fields"><GuidedExitActionFields actions={draft.trading_actions.definitions} onChange={(route) => onReplaceRoute(activeRoute.rule_set_id, route)} value={activeRoute} /></div> : null}
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
  const updateSide = (side: StrategyLifecycle["trading_behavior"]["side"]) => onChange({
    ...profile,
    lifecycle: {
      ...profile.lifecycle,
      trading_behavior: { ...behavior, side },
      initial_entry: {
        ...profile.lifecycle.initial_entry,
        action_id: `position.enter_${side}`,
        add_steps: profile.lifecycle.initial_entry.add_steps.map((step) => ({ ...step, action_id: `position.add_${side}` })),
      },
      reentry: { ...profile.lifecycle.reentry, action_id: `position.enter_${side}` },
      exit: { rule_sets: profile.lifecycle.exit.rule_sets.map((route) => ({ ...route, action_id: `position.${route.action === "reduce" ? "reduce" : "exit"}_${side}` })) },
    },
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
          onChange={(side) => updateSide(side as StrategyLifecycle["trading_behavior"]["side"])}
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

function ReentryEditor({ catalog, draft, onChange, onRuleSetEdit = () => undefined, profile, ruleSets }: {
  catalog: StrategyInput[];
  draft: Draft;
  onChange: (value: StrategyProfile) => void;
  onRuleSetEdit?: (ruleSetId: string, created?: RuleSetDefinition) => void;
  profile: StrategyProfile;
  ruleSets: RuleSetDefinition[];
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
        ruleSetCatalog={ruleSets}
        rules={reentry.rules}
        title="When a reentry becomes eligible"
        summary="Reentry owns an independent rule set. Import selected initial-entry groups as editable copies, then add reentry-only evidence as needed."
      /></> : null}
    </>
  );
}

function ExitRuleSetsEditor({ catalog, draft, onChange, onRuleSetEdit = () => undefined, profile, ruleSets }: {
  catalog: StrategyInput[];
  draft: Draft;
  onChange: (value: StrategyProfile) => void;
  onRuleSetEdit?: (ruleSetId: string, created?: RuleSetDefinition) => void;
  profile: StrategyProfile;
  ruleSets: RuleSetDefinition[];
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
    const evidenceRuleSet = ruleSets[0];
    onChange({
      ...profile,
      lifecycle: {
        ...profile.lifecycle,
        exit: {
          rule_sets: [{
            action: "close",
            action_id: `position.exit_${profile.lifecycle.trading_behavior.side}`,
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
            <RuleStageComposition catalog={catalog} label={`${ruleSet.name} evidence`} onChange={(rules) => replace(ruleSet.rule_set_id, { ...ruleSet, rules })} onEditRuleSet={onRuleSetEdit} ruleSets={ruleSets} stage={ruleSet.rules} />
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
        <p>{guided ? "Publishing makes this execution setup available to new runs. Existing runs keep the release they started with." : "A release freezes every referenced Strategy, Session Profile, Execution Route, mandate, policy, OMS configuration, and account binding. Canvas profiles remain separately versioned presentation and may attach to a run by run ID."}</p>
        <div className="configuration-publish-proof">
          {visibleChecks.map((check) => <span data-ready={check.ready ? "true" : "false"} key={check.label}>{check.ready ? <CheckCircle2 size={14} /> : <TriangleAlert size={14} />} {guided ? publishCheckLabel(check.label) : check.label} · {check.detail}</span>)}
          <span data-ready="true"><CheckCircle2 size={14} /> Optional Canvas · {canvas.containerCount} saved containers</span>
        </div>
        <label><span>Release label <FieldHelp content="Use a short operational label that explains what this release is intended to validate." /></span><input onChange={(event) => onLabelChange(event.target.value)} placeholder="Replay strategy-studio acceptance" value={label} /></label>
        <button className="button primary" disabled={!draft || !configurationReady || !label.trim() || publishing} onClick={onPublish} type="button"><Send size={15} /> {publishing ? "Publishing…" : "Publish release"}</button>
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
  const policyIds = new Set(draft.portfolio.policies.map((row) => String(row.policy_id ?? "")));
  const watchlists = new Map(draft.market_discovery.watchlists.map((row) => [row.watchlist_id, row]));
  const signalStreams = new Map(draft.market_discovery.signal_streams.map((row) => [row.signal_stream_id, row]));
  const deployments = draft.assignments.deployments;
  const deploymentsReady = deployments.length > 0 && deployments.every((deployment) => (
    profileIds.has(deployment.profile_id)
    && omsIds.has(deployment.oms_profile_id)
    && draft.portfolio.mandates.some((mandate) => mandate.enabled && mandate.run_plan_id === deployment.run_plan_id)
  ));
  const watchlistsReady = deployments.length > 0 && deployments.every((deployment) => deployment.watchlist_ids.every((watchlistId) => {
    const watchlist = watchlists.get(watchlistId);
    return Boolean(watchlist && watchlist.enabled && watchlist.availability !== "integration_pending");
  }));
  const signalStreamsReady = deployments.length > 0 && deployments.every((deployment) => deployment.signal_stream_ids.length > 0 && deployment.signal_stream_ids.every((streamId) => signalStreams.get(streamId)?.enabled));
  const dataPlansReady = deployments.length > 0 && deployments.every((deployment) => deployment.allowed_environments.length > 0 && deployment.allowed_environments.every((mode) => Boolean(deployment.data_plan_ids[mode])) && Boolean(deployment.canvas_profile_id));
  const mandatesReady = draft.portfolio.mandates.length > 0 && draft.portfolio.mandates.every((mandate) => (
    deployments.some((deployment) => deployment.run_plan_id === mandate.run_plan_id)
    && accountKeys.has(mandate.account_key)
    && policyIds.has(draft.accounts.bindings.find((account) => account.account_key === mandate.account_key)?.portfolio_policy_id ?? "")
  ));
  const configuredModes = new Set(draft.accounts.bindings.filter((account) => account.enabled).flatMap((account) => account.modes));
  const modeCoverageReady = draft.accounts.bindings.filter((account) => account.enabled).every((account) => account.modes.every((mode) => deployments.some((deployment) => (
    deployment.enabled
    && deployment.allowed_environments.includes(mode)
    && draft.portfolio.mandates.some((mandate) => mandate.enabled && mandate.account_key === account.account_key && mandate.run_plan_id === deployment.run_plan_id)
  ))));
  const liveBindingsReady = draft.accounts.bindings.every((account) => !account.enabled || !account.modes.some((mode) => mode === "paper" || mode === "live") || Boolean(account.source_account_env && account.session_key.trim()));
  return [
    { detail: String(draft.strategy.profiles.length), label: "Strategy Profiles", ready: draft.strategy.profiles.length > 0 },
    { detail: deploymentsReady ? `${deployments.length} ready` : "needs mandate or strategy", label: "Runtime compilation", ready: deploymentsReady },
    { detail: signalStreamsReady ? "immutable activation references" : "missing or disabled Signal Stream", label: "Signal Streams", ready: signalStreamsReady },
    { detail: watchlistsReady ? "optional eligibility references valid" : "unavailable Watchlist", label: "QMD Watchlists", ready: watchlistsReady },
    { detail: String(draft.portfolio.mandates.length), label: "Account mandates", ready: mandatesReady },
    { detail: String(draft.oms.profiles.length), label: "OMS profiles", ready: draft.oms.profiles.length > 0 },
    { detail: String(draft.accounts.bindings.length), label: "Accounts", ready: draft.accounts.bindings.length > 0 },
    { detail: dataPlansReady ? "mode plans and Canvas selected" : "query plan or Canvas missing", label: "Run Plan dependencies", ready: dataPlansReady },
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
    {payload ? <><p className="configuration-section-guide">{payload.runtime_count} eligible compiled runtime{payload.runtime_count === 1 ? "" : "s"} · {payload.accounts.length} bound account{payload.accounts.length === 1 ? "" : "s"} · {readableLabel(payload.source)}</p><div className="mandate-grid abstraction-card-grid">{payload.accounts.map((account) => <AbstractionCard description={`Broker/session: ${String(account.source_account_env || account.source_account_id || "Simulated")}`} identity={String(account.account_key)} key={String(account.account_key)} kind="account_binding" metadata={[{ label: "Account class", value: readableLabel(String(account.account_class)) }, { label: "Session", value: String(account.session_key) }, { label: "Portfolio policy", value: String(account.policy_identity) }, { label: "Eligible Run Plans", value: Array.isArray(account.run_plan_ids) ? account.run_plan_ids.join(", ") || "None for this mode" : "None for this mode" }]} status="Runtime eligible" title={String(account.name || account.account_key)} />)}</div></> : null}
  </ConfigGroup>;
}

function ConfigurationLoading() {
  return <div aria-live="polite" className="configuration-empty configuration-loading" role="status"><span className="loading-spinner" aria-hidden="true" /><span><strong>Loading configuration</strong><small>Reading the approved base for this browser session…</small></span></div>;
}

function updateCapability(profile: StrategyProfile, id: string, binding: CapabilityBinding): StrategyProfile {
  return { ...profile, capabilities: profile.capabilities.map((row) => row.capability_id === id ? binding : row) };
}

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
    sessions: deepClone(approved.payload.sessions ?? current.sessions),
    strategy: deepClone(approved.payload.strategy),
    trading_actions: deepClone(approved.payload.trading_actions),
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
  if (step === "canvas") return "canvas-configuration";
  if (step === "execution" || step === "protection") return "oms-configuration";
  return pageForSection(step as TradingConfigurationSection);
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
  const ready = (label: string) => Boolean(checks.find((check) => check.label === label)?.ready);
  const inherited = <K extends keyof Draft>(key: K) => Boolean(approved && stableStringify(draft[key]) === stableStringify(approved.payload[key]));
  const state = (key: keyof Draft, valid: boolean, recommended: boolean): "Inherited" | "Invalid" | "Using recommended" | "Customized" => !valid ? "Invalid" : inherited(key) ? "Inherited" : recommended ? "Using recommended" : "Customized";
  return [
    { icon: GitBranch, label: "Strategy", selection: profile?.name ?? "Missing", state: state("strategy", Boolean(profile), Boolean(profile?.protected)), step: "strategy" as GuidedStep },
    { icon: Boxes, label: "Accounts", selection: account ? `${account.name} · ${account.modes.map(readableLabel).join(", ")}` : "Missing", state: state("accounts", ready("Accounts") && ready("Paper and Live bindings"), false), step: "accounts" as GuidedStep },
    { icon: BriefcaseBusiness, label: "Portfolio", selection: mandate ? `${account?.name ?? mandate.account_key} · ${percent(mandate.maximum_planned_risk_fraction)} risk` : "Missing", state: state("portfolio", ready("Account mandates"), false), step: "portfolio" as GuidedStep },
    { icon: Send, label: "Execution", selection: execution ? readableLabel(execution.name) : "Missing", state: state("oms", Boolean(execution), execution?.origin === "system"), step: "execution" as GuidedStep },
    { icon: ShieldCheck, label: "Protection", selection: protection?.name ?? "Missing", state: state("oms", Boolean(protection?.mandatory_catastrophic_backstop), protection?.origin === "system"), step: "protection" as GuidedStep },
    { icon: Network, label: "Run Plan", selection: deployment?.name ?? "Missing", state: state("assignments", Boolean(deployment && ready("Runtime compilation") && ready("QMD Watchlists") && ready("Run Plan dependencies")), false), step: "assignments" as GuidedStep },
    { icon: LayoutGrid, label: "Canvas", selection: canvasApprovalSnapshot().ready ? `${canvasApprovalSnapshot().containerCount} containers` : "Missing", state: canvasApprovalSnapshot().ready ? "Customized" : "Invalid", step: "canvas" as GuidedStep },
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
