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
import "../app/configurationVisuals.css";
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
  EffectiveConfigurationPreview,
  RevisionBadge,
  RevisionPublisher,
  releaseReadiness,
  type Revision,
} from "../features/trading-configuration/release";
import {
  AddStepsEditor,
  DecisionRulesEditor,
  OrderIntentEditor,
  PhaseOrderEditor,
  RuleStageComposition,
  RuleStageEditor,
} from "../features/trading-configuration/strategy/RuleAuthoring";
import {
  AccountConfigurationScope,
  DecisionOptions,
  GuidedEmpty,
  GuidedQuestion,
  GuidedReview,
  GuidedStrategyConfiguration,
  StrategyStudio,
  guidedQuestionRailLabel,
} from "../features/trading-configuration/strategy/StrategyStudio";
import {
  navigateGuidedStep,
  pageForGuidedStep,
  type ConfigurationExperience,
  type GuidedStep,
  type OmsGuidedStage,
} from "../features/trading-configuration/guidedNavigation";
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

function ConfigurationLoading() {
  return <div aria-live="polite" className="configuration-empty configuration-loading" role="status"><span className="loading-spinner" aria-hidden="true" /><span><strong>Loading configuration</strong><small>Reading the approved base for this browser session…</small></span></div>;
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
