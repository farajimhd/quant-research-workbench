import { BadgeCheck, ArrowLeft, ArrowRight, BookOpenCheck, Boxes, BriefcaseBusiness, Check, CheckCircle2, ChevronDown, ChevronRight, Clipboard, GitBranch, LockKeyhole, Network, PencilLine, Plus, Save, Search, Send, Settings2, ShieldCheck, Sparkles, Target, Trash2, TriangleAlert, WalletCards } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { AbstractionCard } from "../../../app/components/AbstractionCard";
import { InventoryFilterSelect } from "../../../app/components/InventoryFilterSelect";
import {
  BooleanField,
  CapabilityField,
  ConfigurationNarrative,
  EmptyState,
  NumberField,
  ParameterField,
  SelectField,
  TextField,
  readableLabel,
  round,
} from "../components/ConfigurationFields";
import {
  ENTRY_AUTHORING_PAGES,
  EXIT_AUTHORING_PAGES,
  MANAGE_AUTHORING_PAGES,
  REENTRY_AUTHORING_PAGES,
  type AccountBinding,
  type AddStep,
  type CapitalRequestConfig,
  type CapabilityBinding,
  type CapabilityDefinition,
  type Draft,
  type EntryAuthoringPage,
  type EntryRules,
  type ExecutionPolicyConfig,
  type ExitAuthoringPage,
  type ExitRuleSet,
  type ManageAuthoringPage,
  type OrderIntentConfig,
  type ParameterMap,
  type Primitive,
  type ReentryAuthoringPage,
  type RuleExpression,
  type RuleSetDefinition,
  type RuleStage,
  type StrategyAuthoringStage,
  type StrategyDefinition,
  type StrategyInput,
  type StrategyLifecycle,
  type StrategyPhaseMode,
  type StrategyProfile,
  type StrategySection,
} from "../contracts";
import { collectLifecycleRuleSetIds, normalizeStrategyProfileReferences } from "../draft";
import { navigateGuidedStep, reviewRows } from "../guidedNavigation";
import { EffectiveConfigurationPreview, RevisionPublisher, type Revision } from "../release";
import { AccountsEditor, DeploymentEditor, OmsEditor, PortfolioEditor } from "../sections/OperationalConfigurationSections";
import {
  AddStepsEditor,
  DecisionRulesEditor,
  OrderIntentEditor,
  PhaseOrderEditor,
  RuleStageComposition,
  RuleStageEditor,
} from "./RuleAuthoring";
import type { ActionPolicyDefinition, TradingActionDefinition } from "../../../pages/TradingActionsPage";
import {
  choicesFor,
  controlFor,
  deepClone,
  field,
  flattenPrimitives,
  helpForPath,
  isDirectlyEditableStrategyParameter,
  labelForStrategyParameter,
  setPath,
  stepFor,
  uniqueId,
  unitFor,
} from "../utilities";
export const LEGACY_ENTRY_LOGIC_PATHS = new Set([
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

export function guidedQuestionRailLabel(key: string, fallback: string) {
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

export type GuidedStrategyQuestionDefinition = {
  content: ReactNode;
  description: string;
  guide: string;
  id: string;
  section: string;
  title: string;
};

export function GuidedStrategyConfiguration({ draft, onChange, onContinue, onProfileChange, profile }: {
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

export function StrategyStartWorkflow({ cloneSourceId, definitionId, mode, name, onClone, onCloneSourceChange, onCreate, onDefinitionChange, onModeChange, onNameChange, profiles, section }: {
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

export function NextActionArea({ active, children, className = "", description, focusKey, title }: { active: boolean; children: ReactNode; className?: string; description: string; focusKey: string; title: string }) {
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

export function strategySourceSummary(profile: StrategyProfile) {
  const actionPolicies = profile.action_policy_ids.length;
  const adds = profile.lifecycle.initial_entry.add_steps.filter((row) => row.enabled).length;
  const exits = profile.lifecycle.exit.rule_sets.filter((row) => row.enabled).length;
  return `${readableLabel(profile.lifecycle.trading_behavior.side)} · ${actionPolicies} action policies · ${adds} adds · ${exits} exits`;
}

export function StrategyProfileFeaturePreview({ profile }: { profile: StrategyProfile }) {
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

export function GuidedCapitalRequestFields({ onChange, segment, value }: { onChange: (value: CapitalRequestConfig) => void; segment: "amount" | "priority"; value: CapitalRequestConfig }) {
  const request = { fixed_quantity: { label: "Shares requested", maximum: undefined, minimum: 1, step: 1, unit: "shares" }, mandate_fraction: { label: "Mandate capacity", maximum: 1, minimum: .01, step: .05, unit: "fraction" }, risk_fraction: { label: "Risk budget", maximum: 1, minimum: .01, step: .05, unit: "fraction" }, all_available: { label: "", maximum: undefined, minimum: 0, step: 1, unit: "" } }[value.mode];
  if (segment === "priority") return <BooleanField help="Allow Portfolio to propose releasing a weaker position when policy permits." label="Allow replacement proposal" onChange={(allow_replacement) => onChange({ ...value, allow_replacement })} value={value.allow_replacement} />;
  return <div className="guided-form-grid"><SelectField help="Choose how Strategy expresses the desired size before Portfolio evaluates account-specific capacity and risk." label="Request method" onChange={(mode) => onChange({ ...value, mode: mode as CapitalRequestConfig["mode"], value: mode === "fixed_quantity" ? 100 : mode === "all_available" ? 1 : .2 })} options={[{ label: "Fixed shares", value: "fixed_quantity" }, { label: "Fraction of mandate cash", value: "mandate_fraction" }, { label: "Fraction of risk budget", value: "risk_fraction" }, { label: "All remaining mandate capacity", value: "all_available" }]} value={value.mode} />{value.mode !== "all_available" ? <NumberField help="Enter the desired amount in the units implied by the selected request method. Portfolio may approve less or reject it." label={request.label} maximum={request.maximum} minimum={request.minimum} onChange={(requestValue) => onChange({ ...value, value: requestValue })} step={request.step} unit={request.unit} value={value.value} /> : <div className="guided-readonly-value"><span>Request amount</span><strong>All capacity still allowed by the mandate</strong><small>Portfolio computes the actual quantity after every current account and risk check.</small></div>}</div>;
}

export function GuidedOrderIntentFields({ draft, eligibleSessions, onChange, segment, value }: { draft: Draft; eligibleSessions: string[]; onChange: (value: OrderIntentConfig) => void; segment: "execution" | "partial-fill" | "protection"; value: OrderIntentConfig }) {
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

export function GuidedSelectionGuide({ description, eyebrow, facts, icon, label, note, tone }: { description: string; eyebrow: string; facts: Array<{ label: string; value: string }>; icon: ReactNode; label: string; note: string; tone: "execution" | "protection" | "remainder" }) {
  return <section className="strategy-selected-guide" data-tone={tone}><header><span>{icon}</span><div><small>{eyebrow}</small><strong>{label}</strong></div></header><div className="strategy-selected-guide-description">{description}</div><dl>{facts.map((fact) => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl><footer><ShieldCheck size={15} /><span>{note}</span></footer></section>;
}

export function executionPolicyBehavior(policy: ExecutionPolicyConfig): string {
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

export function executionPolicyDuration(milliseconds: number): string {
  return milliseconds >= 1_000 ? `${round(milliseconds / 1_000)} s` : `${milliseconds} ms`;
}

export function executionPolicyLookupDescription(policy: ExecutionPolicyConfig): string {
  const deadline = executionPolicyDuration(policy.envelope.deadline_ms);
  const repricing = policy.envelope.maximum_reprices
    ? `Up to ${policy.envelope.maximum_reprices} reprice${policy.envelope.maximum_reprices === 1 ? "" : "s"}, no more often than every ${policy.envelope.minimum_reprice_interval_ms} ms, within ${deadline}.`
    : `No OMS reprices; the working deadline is ${deadline}.`;
  return `${executionPolicyBehavior(policy)} ${repricing}`;
}

export function GuidedActionForm({ sections }: { sections: Array<{ content: ReactNode; description: string; title: string }> }) {
  return <div className="guided-action-form">{sections.map((section) => <section key={section.title}><header><strong>{section.title}</strong><p>{section.description}</p></header><div>{section.content}</div></section>)}</div>;
}

export function GuidedExitTimingFields({ onChange, value }: { onChange: (value: ExitRuleSet) => void; value: ExitRuleSet }) {
  return <div className="guided-form-grid"><NumberField help="Set the delay after the confirmed entry before this strategic exit route may act." label="Active after" minimum={0} onChange={(active_after_ms) => onChange({ ...value, timing: { ...value.timing, active_after_ms } })} step={1000} unit="ms" value={value.timing.active_after_ms} /><NumberField help="Set how long the route remains eligible. Zero keeps it eligible while the position is open." label="Expires after" minimum={0} onChange={(expires_after_ms) => onChange({ ...value, timing: { ...value.timing, expires_after_ms } })} step={1000} unit="ms" value={value.timing.expires_after_ms} /></div>;
}

export function GuidedExitActionFields({ actions = [], onChange, value }: { actions?: TradingActionDefinition[]; onChange: (value: ExitRuleSet) => void; value: ExitRuleSet }) {
  const supported = actions.filter((action) => ["exit", "reduce"].includes(action.category));
  return <div className="guided-form-grid">{supported.length ? <SelectField help="Registered broker-neutral intent emitted when this route passes." label="Trading Action" onChange={(action_id) => { const definition = supported.find((action) => action.action_id === action_id); onChange({ ...value, action: definition?.category === "reduce" ? "reduce" : "close", action_id }); }} options={supported.map((action) => ({ label: action.name, value: action.action_id }))} value={value.action_id} /> : <SelectField help="Choose whether this route requests the full current position or only a configured fraction." label="Position action" onChange={(action) => onChange({ ...value, action: action as ExitRuleSet["action"] })} options={[{ label: "Close the position", value: "close" }, { label: "Reduce the position", value: "reduce" }]} value={value.action} />}{value.action === "reduce" ? <NumberField help="Set the fraction of the reconciled current position that Strategy requests to release." label="Reduction fraction" maximum={1} minimum={.01} onChange={(position_fraction) => onChange({ ...value, position_fraction })} step={.05} unit="fraction" value={value.position_fraction} /> : <div className="guided-readonly-value"><span>Requested quantity</span><strong>Entire reconciled position</strong><small>Portfolio and OMS still verify the broker-authoritative current quantity.</small></div>}</div>;
}

export function GuidedCapabilityFields({ binding, definition, onChange }: { binding: CapabilityBinding; definition: CapabilityDefinition; onChange: (value: CapabilityBinding) => void }) {
  return <div className="guided-capability-fields"><BooleanField help="Enable this capability for the selected Strategy Profile. Disabling it retains every saved parameter for later review." label="Enabled" onChange={(enabled) => onChange({ ...binding, enabled })} value={binding.enabled} />{binding.enabled ? <div className="guided-form-grid">{definition.parameters.map((parameter) => <CapabilityField definition={parameter} key={parameter.key} onChange={(value) => onChange({ ...binding, settings: { ...binding.settings, [parameter.key]: value } })} value={binding.settings[parameter.key]} />)}</div> : <p>The capability remains configured but cannot participate in the Strategy lifecycle while disabled.</p>}</div>;
}

export function strategySetupRows(profile: StrategyProfile) {
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

export function countRuleReferences(expression?: RuleExpression): number {
  if (!expression) return 0;
  return expression.kind === "rule_set" ? 1 : expression.children.reduce((sum, child) => sum + countRuleReferences(child), 0);
}

export function blankStrategyProfile(source: StrategyProfile, draft: Draft, definition?: StrategyDefinition): StrategyProfile {
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

export function strategyDefinitionParameters(definition: StrategyDefinition): ParameterMap {
  const parameters = deepClone(definition.parameter_defaults ?? {});
  delete parameters.entry_rules;
  delete parameters.phase_policy;
  delete parameters.strategy_behavior;
  return parameters;
}

export function strategyExecutorDescription(definition: StrategyDefinition) {
  const key = definition.executor_key || `${definition.strategy_id}@${definition.revision}`;
  return definition.executor_schema_version
    ? `${key} · executor schema ${definition.executor_schema_version}`
    : `${key} · installed execution contract`;
}

export function cloneStrategyProfile(source: StrategyProfile, existing: StrategyProfile[], requestedName: string): StrategyProfile {
  const profileId = uniqueId(`${source.profile_id}-copy`, existing.map((row) => row.profile_id));
  return { ...deepClone(source), derived_from_profile_id: source.profile_id, editable: true, name: requestedName.trim(), origin: "user", profile_id: profileId, protected: false, publication_status: "draft", revision: 1 };
}

export function uniqueProfileName(base: string, existing: StrategyProfile[]) {
  const taken = new Set(existing.map((row) => row.name.trim().toLocaleLowerCase()));
  let value = base;
  let index = 2;
  while (taken.has(value.toLocaleLowerCase())) value = `${base} ${index++}`;
  return value;
}

export function GuidedQuestion({ children, description, label, status }: { children: ReactNode; description: string; label: string; status: string }) {
  return <section className="guided-question"><section className="guided-question-prompt"><header><div><span>{label}</span><p>{description}</p></div><em data-state={status.toLowerCase().replaceAll(" ", "-")}>{status}</em></header></section><section className="guided-answer-surface"><div className="guided-answer-content">{children}</div></section></section>;
}

export function AccountConfigurationScope({ account }: { account: AccountBinding }) {
  return <section className="account-configuration-scope" aria-label={`Editing ${account.name}`}>
    <span><WalletCards aria-hidden="true" size={17} /></span>
    <div><small>Editing account</small><strong>{account.name}</strong><code>{account.account_key}</code></div>
    <dl><div><dt>Class</dt><dd>{readableLabel(account.account_class)}</dd></div><div><dt>Modes</dt><dd>{account.modes.map(readableLabel).join(", ") || "None"}</dd></div><div><dt>Status</dt><dd>{account.enabled ? "Enabled" : "Disabled"}</dd></div></dl>
  </section>;
}

export function ConfigurationGuidance({ items }: { items: Array<{ label: string; value: string }> }) {
  return <dl className="configuration-guidance">{items.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>;
}

export function DecisionOptions({ onChange, options, value }: { onChange: (value: string) => void; options: Array<{ detail: string; label: string; recommended?: boolean; value: string }>; value: string }) {
  const name = useId();
  return <div className="guided-decision-options">{options.map((option) => <label key={option.value}><input checked={value === option.value} name={name} onChange={() => onChange(option.value)} type="radio" /><span className="guided-choice-card"><span className="guided-choice-copy"><span className="guided-choice-title"><strong>{option.label}</strong>{option.recommended ? <em>Recommended</em> : null}</span><small>{option.detail}</small></span><span aria-hidden="true" className="guided-choice-marker">{value === option.value ? <Check size={14} /> : null}</span></span></label>)}</div>;
}

export function ModeChoices({ onChange, options, values }: { onChange: (values: string[]) => void; options: string[]; values: string[] }) {
  return <div className="guided-mode-choices">{options.map((option) => <label key={option}><input checked={values.includes(option)} onChange={(event) => onChange(event.target.checked ? [...values, option] : values.filter((value) => value !== option))} type="checkbox" /><span><Check size={13} />{readableLabel(option)}</span></label>)}</div>;
}

export function GuidedReview({ approved, draft, label, onLabelChange, onPublish, onReturn, publishing, revisions }: { approved: Revision | null; draft: Draft; label: string; onLabelChange: (value: string) => void; onPublish: () => void; onReturn: () => void; publishing: boolean; revisions: Revision[] }) {
  const rows = reviewRows(draft, approved);
  return <div className="guided-review">
    <header><span>Final step</span><h2>Review the effective configuration</h2><p>Resolve anything marked invalid or needing a decision. Publication freezes the entire draft and configured Canvas for new runs.</p></header>
    <div className="guided-review-layout"><div className="guided-review-matrix">{rows.map((row) => { const Icon = row.icon; return <article key={row.step}><span><Icon size={18} /><strong>{row.label}</strong></span><span>{row.selection}</span><em data-state={row.state.toLowerCase().replaceAll(" ", "-")}>{row.state}</em><button onClick={() => navigateGuidedStep(row.step, () => undefined)} type="button">Change <ChevronRight size={13} /></button></article>; })}</div><aside><RevisionPublisher approved={approved} draft={draft} guided label={label} onLabelChange={onLabelChange} onPublish={onPublish} publishing={publishing} revisions={revisions} /></aside><details className="guided-technical-preview"><summary>Show the technical runtime preview <ChevronRight size={15} /></summary><EffectiveConfigurationPreview draft={draft} /></details></div>
    <button className="button" onClick={onReturn} type="button"><ArrowLeft size={15} /> Back to accounts</button>
  </div>;
}

export function GuidedEmpty({ onSwitchToExpert }: { onSwitchToExpert: () => void }) {
  return <div className="guided-empty"><TriangleAlert size={20} /><h2>This step needs a base object</h2><p>Create the missing profile, Run Plan, mandate, OMS profile, policy, protection profile, or account in Expert mode. Guided setup does not create a Live-critical object implicitly.</p><button className="button primary" onClick={onSwitchToExpert} type="button"><Settings2 size={15} /> Open Expert editor</button></div>;
}

export function StrategyStudio({ approved, draft, label, onChange, onDeleteProfile, onDraftChange, onLabelChange, onPublish, publishing, revisions, section }: {
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

export function StrategySelectionPage({ creationMode, definitionId, definitions, name, nameConflict, onCancel, onClone, onCreate, onCreateStart, onDefinitionChange, onDelete, onModify, onNameChange, profiles }: {
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

export function StrategyAuthoringFlow({ activeStage, advanced, approved, draft, entryRules, label, onLabelChange, onProfileChange, onPublish, onRuleSetEdit, onStageChange, profile, publishing, revisions, ruleSets, section }: {
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

export function ManageAuthoringSurface({ activeAddStep, activePage, draft, enabledAdds, entryRules, mode, onAddStep, onModeChange, onPageChange, onProfileChange, onReplaceAddStep, onReplaceInitialEntry, onRuleSetEdit, onSelectedAddStepChange, profile, ruleSets, section, trailingParameters }: {
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

export function ActionPolicyBindingsEditor({ onChange, policies, selected }: {
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

export function StrategyPhaseModeEditor({ mode, onChange, phase }: {
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

export function ReentryAuthoringSurface({ activePage, draft, mode, onModeChange, onPageChange, onProfileChange, onReplaceReentry, onRuleSetEdit, profile, ruleSets, section }: {
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

export function ExitAuthoringSurface({ activePage, activeRoute, catalog, draft, luldTargetParameters, mode, onAddRoute, onModeChange, onPageChange, onProfileChange, onReplaceRoute, onRuleSetEdit, onSelectedRouteChange, profile, profitPocketParameters, ruleSets }: {
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

export function StrategyStageIntro({ children, hideDescription = false, title }: { children: ReactNode; hideDescription?: boolean; title: string }) {
  return <header className="strategy-stage-intro"><h2>{title}</h2><p aria-hidden={hideDescription || undefined} data-layout-placeholder={hideDescription || undefined}>{children}</p></header>;
}

export function StrategyEngineParameterGroup({ items, onChange, summary, title }: { items: Array<{ path: string; value: Primitive }>; onChange: (path: string, value: Primitive) => void; summary: string; title: string }) {
  if (!items.length) return null;
  return <details className="configuration-advanced strategy-authoring-advanced strategy-engine-parameter-group"><summary><span><strong>{title}</strong><small>{summary} · {items.length} parameters</small></span><ChevronRight size={15} /></summary><div className="configuration-field-grid">{items.map((item) => <ParameterField definition={field(item.path, readableLabel(item.path.split(".").at(-1) ?? item.path), helpForPath(item.path), controlFor(item.value), choicesFor(item.path), unitFor(item.path), stepFor(item.value))} key={item.path} value={item.value} onChange={(value) => onChange(item.path, value)} />)}</div></details>;
}

export function StrategyHandoffLinks({ draft, profile }: { draft: Draft; profile: StrategyProfile }) {
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

export function StoryChapter({ children, eyebrow, marker, title }: { children: ReactNode; eyebrow: string; marker: string; title: string }) {
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

export function BookPart({ label, title }: { label: string; title: string }) {
  return (
    <header className="strategy-book-part"><span>{label}</span><h2>{title}</h2></header>
  );
}

export function StrategyMechanismOverview() {
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

export function MechanismNode({ detail, icon: Icon, label, tone }: { detail: string; icon: typeof GitBranch; label: string; tone: string }) {
  return <div className="strategy-mechanism-node" data-tone={tone}><Icon aria-hidden="true" size={16} /><span><strong>{label}</strong><small>{detail}</small></span></div>;
}

export function MechanismArrow({ label }: { label: string }) {
  return <div aria-hidden="true" className="strategy-mechanism-arrow"><small>{label}</small><ArrowRight size={15} /></div>;
}

export function BookConfigurationSurface({ children, label }: { children: ReactNode; label: string }) {
  return (
    <section className="strategy-book-configuration">
      <header><PencilLine aria-hidden="true" size={15} /><span>{label}</span></header>
      <div>{children}</div>
    </section>
  );
}

export function TradingBehaviorEditor({ definition, onChange, profile }: {
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

export function ReentryEditor({ catalog, draft, onChange, onRuleSetEdit = () => undefined, profile, ruleSets }: {
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

export function ExitRuleSetsEditor({ catalog, draft, onChange, onRuleSetEdit = () => undefined, profile, ruleSets }: {
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

export function CapabilitiesEditor({ catalog, onChange, profile }: {
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

export function updateCapability(profile: StrategyProfile, id: string, binding: CapabilityBinding): StrategyProfile {
  return { ...profile, capabilities: profile.capabilities.map((row) => row.capability_id === id ? binding : row) };
}
