import { BadgeCheck, Boxes, BriefcaseBusiness, CheckCircle2, ChevronRight, Clipboard, Network, Plus, ShieldCheck, Trash2, TriangleAlert } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { AbstractionCard } from "../../../app/components/AbstractionCard";
import type { TradingConfigurationSection } from "../../../app/routes";
import {
  BooleanField,
  ConfigGroup,
  EmptyState,
  FieldHelp,
  GuideCallout,
  ModeSelector,
  NumberField,
  OptionalNumberField,
  ParameterField,
  SelectField,
  TextField,
  readableLabel,
  round,
} from "../components/ConfigurationFields";
import type {
  AccountBinding,
  AccountSection,
  ActionAuthority,
  AssignmentSection,
  Draft,
  ExecutionPolicyConfig,
  Mandate,
  OmsProfile,
  OmsSection,
  ParameterMap,
  PortfolioSection,
  Primitive,
  ProtectionProfileConfig,
  ProtectionSliceConfig,
  ProtectionStopConfig,
  RuntimeMode,
  SessionProfile,
  SessionSection,
  ExecutionRoute,
  StrategyDeployment,
  StrategyRunPlan,
  WatchUniverse,
} from "../contracts";
import { newYorkSessionDate } from "../contracts";
import {
  accountName,
  canvasApprovalSnapshot,
  deepClone,
  deploymentName,
  field,
  percent,
  uniqueId,
  urgencyOptions,
} from "../utilities";
export type ConfigurableSection = Extract<TradingConfigurationSection, "assignments" | "portfolio" | "oms" | "accounts">;

export const SECTION_STUDIO_COPY: Record<ConfigurableSection, { guided: string; title: string }> = {
  accounts: { guided: "Compose sessions, account routes, and account-specific authority", title: "Accounts & Sessions" },
  assignments: { guided: "Assemble one runnable plan at a time", title: "Strategy Run Plans" },
  oms: { guided: "Configure execution and protection step by step", title: "OMS & Protection" },
  portfolio: { guided: "Configure mandates and account policy step by step", title: "Portfolio & Risk" },
};

export const SECTION_SYSTEM_KEYS = new Set([
  "assignment_id", "condition_id", "group_id", "revision", "slice_id", "origin", "editable", "runtime_assignments", "mandate_ids",
]);

export function ConfigurationSectionStudio({ draft, guided, onDraftChange, section }: {
  draft: Draft;
  guided: ReactNode;
  onChange: (value: Draft[ConfigurableSection]) => void;
  onDraftChange: (value: Draft) => void;
  section: ConfigurableSection;
}) {
  const copy = SECTION_STUDIO_COPY[section];

  return <div className="strategy-studio-workspace configuration-section-studio">
    <nav className="strategy-editor-toolbar">
      <span><strong>{copy.title}</strong><small>{copy.guided}</small></span>
    </nav>
    <div className="configuration-guided-workspace configuration-section-guided">{guided}</div>
    {section === "accounts" ? <details className="configuration-advanced configuration-session-authority-editor"><summary><span><strong>Edit complete session authority</strong><small>Session Profiles, Execution Routes, Strategy Deployments, and account bindings</small></span><ChevronRight size={15} /></summary><div className="configuration-stack"><AccountsEditor draft={draft} onChange={(accounts) => onDraftChange({ ...draft, accounts })} onSessionsChange={(sessions) => onDraftChange({ ...draft, sessions })} /></div></details> : null}
  </div>;
}

export function RunPlanCompositionEditor({ draft, onChange, onDraftChange }: {
  draft: Draft;
  onChange: (value: AssignmentSection) => void;
  onDraftChange: (value: Draft) => void;
}) {
  const section = draft.assignments;
  const [selectedId, setSelectedId] = useState(section.deployments[0]?.run_plan_id ?? "");
  const [mandateAccountKey, setMandateAccountKey] = useState(draft.accounts.bindings.find((row) => row.enabled)?.account_key ?? "");
  const selected = section.deployments.find((row) => row.run_plan_id === selectedId) ?? section.deployments[0];
  const canvas = useMemo(canvasApprovalSnapshot, [draft]);

  function createDeployment() {
    const run_plan_id = uniqueId("new-run-plan", section.deployments.map((row) => row.run_plan_id));
    const accountKey = draft.accounts.bindings.find((row) => row.enabled)?.account_key ?? draft.accounts.bindings[0]?.account_key ?? "";
    const mandate = accountKey ? createRunPlanMandate(run_plan_id, accountKey, draft.portfolio.mandates) : null;
    const runPlan: StrategyRunPlan = {
      run_plan_id,
      name: "New Run Plan",
      description: "",
      profile_id: draft.strategy.profiles[0]?.profile_id ?? "",
      oms_profile_id: draft.oms.profiles[0]?.profile_id ?? "",
      universe_id: "",
      watchlist_ids: draft.market_discovery.watchlists.filter((row) => row.enabled && row.availability !== "integration_pending").slice(0, 1).map((row) => row.watchlist_id),
      signal_stream_ids: draft.market_discovery.signal_streams.filter((row) => row.enabled).slice(0, 1).map((row) => row.signal_stream_id),
      activation: { event_policy: "new_occurrences", watchlist_policy: "any_selected" },
      enablement: { state: "enabled", scope: "persistent", effective_session: "" },
      canvas_profile_id: "current-canvas",
      data_plan_ids: { replay: "market.historical_scanner_materialization.v1" },
      source_revision_policy: "require_complete",
      book_id: "default",
      campaign_lifecycle: { initial_entry_authority: "confirm", reentry_authority: "confirm", exit_authority: "automatic", protective_exit_authority: "automatic", maximum_reentries: 3, reentry_cooldown_ms: 1000, maximum_initial_watch_ms: 0, session_end_behavior: "keep_watching", retain_ticker_while_paused: true },
      mandate_ids: mandate ? [mandate.mandate_id] : [],
      enabled: true,
      allowed_environments: ["replay"],
      action_authority: { default: "confirm", initial_entry: "inherit", add: "inherit", reentry: "inherit", strategic_exit: "inherit", protective_exit: "automatic", emergency_exit: "automatic" },
      safety_supervisor: { enabled_by_environment: { replay: true, backtest: true, backtest_debug: true, paper: true, live: true } },
      runtime_assignments: [],
    };
    onDraftChange({ ...draft, assignments: { ...section, deployments: [...section.deployments, runPlan] }, portfolio: { ...draft.portfolio, mandates: mandate ? [...draft.portfolio.mandates, mandate] : draft.portfolio.mandates } });
    setSelectedId(run_plan_id);
  }

  if (!selected) return <div className="configuration-empty-composer"><EmptyState title="No Run Plans" detail="Create the final runnable composition from the existing reusable definitions." /><button className="button primary" onClick={createDeployment} type="button"><Plus size={14} /> Create Run Plan</button></div>;

  const linkedMandates = draft.portfolio.mandates.filter((row) => row.run_plan_id === selected.run_plan_id);
  const selectedStrategy = draft.strategy.profiles.find((row) => row.profile_id === selected.profile_id);
  const selectedOms = draft.oms.profiles.find((row) => row.profile_id === selected.oms_profile_id);
  const selectedWatchlists = draft.market_discovery.watchlists.filter((row) => selected.watchlist_ids.includes(row.watchlist_id));
  const selectedSignalStreams = draft.market_discovery.signal_streams.filter((row) => selected.signal_stream_ids.includes(row.signal_stream_id));
  const availableMandateAccounts = draft.accounts.bindings.filter((account) => !linkedMandates.some((mandate) => mandate.account_key === account.account_key));
  const mandateAccountValue = availableMandateAccounts.some((account) => account.account_key === mandateAccountKey) ? mandateAccountKey : availableMandateAccounts[0]?.account_key ?? "";
  const readiness = [
    { label: "Strategy", selection: selectedStrategy?.name ?? "Not selected", ready: Boolean(selectedStrategy) },
    { label: "Signal Streams", selection: selectedSignalStreams.length ? `${selectedSignalStreams.length} selected` : "None", ready: selectedSignalStreams.length > 0 && selectedSignalStreams.every((row) => row.enabled) },
    { label: "Watchlist eligibility", selection: selectedWatchlists.length ? `${selectedWatchlists.length} selected` : "All signaled tickers", ready: selectedWatchlists.every((row) => row.availability !== "integration_pending") },
    { label: "Account mandates", selection: linkedMandates.length ? `${linkedMandates.length} linked` : "None", ready: linkedMandates.length > 0 },
    { label: "Portfolio policies", selection: `${new Set(linkedMandates.map((mandate) => draft.accounts.bindings.find((account) => account.account_key === mandate.account_key)?.portfolio_policy_id).filter(Boolean)).size} resolved`, ready: linkedMandates.length > 0 && linkedMandates.every((mandate) => Boolean(draft.accounts.bindings.find((account) => account.account_key === mandate.account_key)?.portfolio_policy_id)) },
    { label: "OMS", selection: selectedOms?.name ?? "Not selected", ready: Boolean(selectedOms) },
    { label: "Data plans", selection: `${selected.allowed_environments.length} modes`, ready: selected.allowed_environments.length > 0 && selected.allowed_environments.every((mode) => Boolean(selected.data_plan_ids[mode])) },
    { label: "Historical coverage", selection: readableLabel(selected.source_revision_policy), ready: selected.source_revision_policy === "require_complete" },
  ];

  function replace(next: StrategyRunPlan) {
    onChange({ ...section, deployments: section.deployments.map((row) => row.run_plan_id === selected.run_plan_id ? next : row) });
  }

  function cloneDeployment() {
    const run_plan_id = uniqueId(`${selected.run_plan_id}-copy`, section.deployments.map((row) => row.run_plan_id));
    const mandates = linkedMandates.map((row) => ({ ...deepClone(row), mandate_id: uniqueId(`${run_plan_id}-${row.account_key}`, draft.portfolio.mandates.map((item) => item.mandate_id)), run_plan_id }));
    const clone = { ...deepClone(selected), run_plan_id, name: `${selected.name} copy`, mandate_ids: mandates.map((row) => row.mandate_id) };
    onDraftChange({ ...draft, assignments: { ...section, deployments: [...section.deployments, clone] }, portfolio: { ...draft.portfolio, mandates: [...draft.portfolio.mandates, ...mandates] } });
    setSelectedId(run_plan_id);
  }

  function deleteDeployment() {
    if (selected.enabled) return;
    const deployments = section.deployments.filter((row) => row.run_plan_id !== selected.run_plan_id);
    onDraftChange({ ...draft, assignments: { ...section, deployments }, portfolio: { ...draft.portfolio, mandates: draft.portfolio.mandates.filter((row) => row.run_plan_id !== selected.run_plan_id) } });
    setSelectedId(deployments[0]?.run_plan_id ?? "");
  }

  function toggleWatchlist(watchlistId: string) {
    replace({ ...selected, watchlist_ids: selected.watchlist_ids.includes(watchlistId) ? selected.watchlist_ids.filter((value) => value !== watchlistId) : [...selected.watchlist_ids, watchlistId] });
  }

  function toggleSignalStream(signalStreamId: string) {
    replace({ ...selected, signal_stream_ids: selected.signal_stream_ids.includes(signalStreamId) ? selected.signal_stream_ids.filter((value) => value !== signalStreamId) : [...selected.signal_stream_ids, signalStreamId] });
  }

  function replaceModes(allowed_environments: RuntimeMode[]) {
    const data_plan_ids = { ...selected.data_plan_ids };
    for (const mode of allowed_environments) data_plan_ids[mode] ??= mode === "paper" || mode === "live" ? "qmd.scanner.snapshot.v1" : "market.historical_scanner_materialization.v1";
    replace({ ...selected, allowed_environments, data_plan_ids });
  }

  function addMandate() {
    if (!mandateAccountValue) return;
    const mandate = createRunPlanMandate(selected.run_plan_id, mandateAccountValue, draft.portfolio.mandates);
    onDraftChange({ ...draft, assignments: { ...section, deployments: section.deployments.map((row) => row.run_plan_id === selected.run_plan_id ? { ...row, mandate_ids: [...row.mandate_ids, mandate.mandate_id] } : row) }, portfolio: { ...draft.portfolio, mandates: [...draft.portfolio.mandates, mandate] } });
  }

  return <div className="configuration-composition-workspace">
    <aside className="configuration-library">
      <header><div><span>Run Plans</span><strong>{section.deployments.length} configured</strong></div><button onClick={createDeployment} title="Create Run Plan" type="button"><Plus size={15} /></button></header>
      <div>{section.deployments.map((row) => <button className={row.run_plan_id === selected.run_plan_id ? "active" : ""} key={row.run_plan_id} onClick={() => setSelectedId(row.run_plan_id)} type="button"><span><strong>{row.name}</strong><small>{row.enabled ? "Enabled" : "Disabled"} · {row.allowed_environments.map(readableLabel).join(", ")}</small></span><ChevronRight size={14} /></button>)}</div>
    </aside>
    <main className="configuration-detail configuration-composition-editor">
      <section className="configuration-detail-heading"><div><span>Run Plan · {selected.run_plan_id}</span><input aria-label="Run Plan name" onChange={(event) => replace({ ...selected, name: event.target.value })} value={selected.name} /><textarea aria-label="Run Plan summary" onChange={(event) => replace({ ...selected, description: event.target.value })} rows={2} value={selected.description} /></div><div className="configuration-object-actions"><button className="button compact" onClick={cloneDeployment} type="button">Clone</button><label className="configuration-enabled"><input checked={selected.enabled} onChange={(event) => replace({ ...selected, enabled: event.target.checked })} type="checkbox" /> Enabled</label><button className="button compact danger" disabled={selected.enabled} onClick={deleteDeployment} type="button">Delete draft</button></div></section>
      <AbstractionCard description={selected.description || "Final composition of reusable decision, discovery, capital, execution, workspace, and data definitions."} identity={selected.run_plan_id} kind="run_plan" metadata={[{ label: "Strategy", value: selectedStrategy?.name ?? selected.profile_id }, { label: "Signal Streams", value: selectedSignalStreams.length }, { label: "Watchlists", value: selectedWatchlists.length || "Optional" }, { label: "Mandates", value: linkedMandates.length }, { label: "OMS", value: selectedOms?.name ?? selected.oms_profile_id }, { label: "Modes", value: selected.allowed_environments.map(readableLabel).join(", ") }]} selected={selected.enablement.state === "enabled"} status={selected.enablement.state === "enabled" ? selected.enablement.scope === "current_session" ? "Enabled this session" : "Enabled" : "Disabled"} title={selected.name} />
      <GuideCallout icon={<Network size={17} />} title="Signal Stream → Strategy → Portfolio → OMS">Signal Streams activate Strategy evaluation. Optional Watchlists restrict eligible tickers. Portfolio grants capital and the first approved opening campaign receives ticker ownership; OMS alone executes the resulting intent.</GuideCallout>
      <ConfigGroup summary="Select the immutable occurrence streams that activate this Strategy, then choose whether activation persists across sessions." title="1. Signal activation"><div className="configuration-reference-grid">{draft.market_discovery.signal_streams.map((stream) => <AbstractionCard compact control={<input checked={selected.signal_stream_ids.includes(stream.signal_stream_id)} disabled={!stream.enabled} onChange={() => toggleSignalStream(stream.signal_stream_id)} type="checkbox" />} description={stream.description} identity={stream.signal_stream_id} key={stream.signal_stream_id} kind="signal_stream" metadata={[{ label: "Rule sets", value: stream.inclusion_rule_sets.length }, { label: "Evidence", value: stream.columns.length }]} selected={selected.signal_stream_ids.includes(stream.signal_stream_id)} status={!stream.enabled ? "Disabled" : selected.signal_stream_ids.includes(stream.signal_stream_id) ? "Selected" : "Available"} title={stream.name} unavailable={!stream.enabled} />)}</div><div className="configuration-field-grid"><SelectField help="New occurrences is the low-latency default and never replays stale activation when a runtime starts." label="Occurrence policy" onChange={(event_policy) => replace({ ...selected, activation: { ...selected.activation, event_policy: event_policy as StrategyRunPlan["activation"]["event_policy"] } })} options={[{ label: "New occurrences only", value: "new_occurrences" }, { label: "Latest session occurrence", value: "latest_session_occurrence" }]} value={selected.activation.event_policy} /><SelectField help="Persistent enables subsequent sessions. Current session expires at the market-session boundary." label="Enablement" onChange={(value) => replace({ ...selected, enablement: value === "disabled" ? { ...selected.enablement, state: "disabled" } : { state: "enabled", scope: value as StrategyRunPlan["enablement"]["scope"], effective_session: value === "current_session" ? newYorkSessionDate() : "" } })} options={[{ label: "Enabled for subsequent sessions", value: "persistent" }, { label: "Enabled for current session", value: "current_session" }, { label: "Disabled", value: "disabled" }]} value={selected.enablement.state === "disabled" ? "disabled" : selected.enablement.scope} /></div></ConfigGroup>
      <ConfigGroup summary="Optional eligibility constraint. With no selection, every ticker emitted by the selected Signal Streams is eligible." title="2. Watchlist eligibility"><div className="configuration-reference-grid">{draft.market_discovery.watchlists.map((watchlist) => <AbstractionCard compact control={<input checked={selected.watchlist_ids.includes(watchlist.watchlist_id)} disabled={watchlist.availability === "integration_pending"} onChange={() => toggleWatchlist(watchlist.watchlist_id)} type="checkbox" />} description={watchlist.description} identity={watchlist.watchlist_id} key={watchlist.watchlist_id} kind="watchlist" metadata={[{ label: "Members", value: watchlist.maximum_size }, { label: "Columns", value: watchlist.columns.length }]} selected={selected.watchlist_ids.includes(watchlist.watchlist_id)} status={watchlist.availability === "integration_pending" ? "Unavailable" : selected.watchlist_ids.includes(watchlist.watchlist_id) ? "Selected" : "Optional"} title={watchlist.name} unavailable={watchlist.availability === "integration_pending"} />)}</div></ConfigGroup>
      <div className="configuration-two-column"><ConfigGroup summary="References existing behavior and execution definitions." title="2. Strategy and OMS"><div className="configuration-field-grid one-column"><SelectField help="Reusable decision behavior." label="Strategy Profile" onChange={(profile_id) => replace({ ...selected, profile_id })} options={draft.strategy.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={selected.profile_id} /><SelectField help="Reusable execution and protection composition." label="OMS Profile" onChange={(oms_profile_id) => replace({ ...selected, oms_profile_id })} options={draft.oms.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={selected.oms_profile_id} /></div></ConfigGroup><ConfigGroup summary="The current configured workspace is frozen at publication." title="3. Canvas"><div className="configuration-fixed-value"><span>Canvas profile</span><strong>{canvas.revision}</strong><small>{canvas.containerCount} containers · {canvas.ready ? "ready" : "not ready"}</small></div></ConfigGroup></div>
      <ConfigGroup summary="Create or reference Portfolio mandates here; edit their limits under Portfolio & Risk." title="4. Account mandates"><div className="deployment-mandates">{linkedMandates.map((mandate) => <article key={mandate.mandate_id}><strong>{accountName(draft.accounts, mandate.account_key)}</strong><span>{percent(mandate.maximum_cash_fraction)} cash · {readableLabel(mandate.assignment_mode)} · max {readableLabel(mandate.maximum_action_authority)}</span></article>)}</div><div className="configuration-inline-add"><SelectField help="Existing governed account." label="Account to add" onChange={setMandateAccountKey} options={availableMandateAccounts.map((account) => ({ label: account.name, value: account.account_key }))} value={mandateAccountValue} /><button className="button compact" disabled={!mandateAccountValue} onClick={addMandate} type="button"><Plus size={14} /> Add mandate</button></div><a className="configuration-inline-link" href="#portfolio-configuration">Edit allocation and risk limits <ChevronRight size={13} /></a></ConfigGroup>
      <ConfigGroup summary="Portfolio mandate authority remains a hard upper bound." title="5. Action authority"><CampaignPolicyEditor deployment={selected} onChange={replace} /></ConfigGroup>
      <ConfigGroup summary="Every enabled environment resolves through an explicit registered query plan." title="6. Runtime modes and data plans"><ModeSelector modes={selected.allowed_environments} onChange={replaceModes} /><div className="configuration-data-plan-grid">{selected.allowed_environments.map((mode) => <div className="configuration-fixed-value" key={mode}><span>{readableLabel(mode)}</span><strong>{selected.data_plan_ids[mode]}</strong><small>Registered query plan</small></div>)}</div><SelectField help="Historical modes should fail before the first event when source coverage is incomplete." label="Source revision policy" onChange={(source_revision_policy) => replace({ ...selected, source_revision_policy: source_revision_policy as StrategyRunPlan["source_revision_policy"] })} options={[{ label: "Require complete", value: "require_complete" }, { label: "Allow partial (research only)", value: "allow_partial" }]} value={selected.source_revision_policy} /><div className="configuration-field-grid">{(["replay", "backtest", "backtest_debug", "paper", "live"] as RuntimeMode[]).map((mode) => <BooleanField disabled={mode === "paper" || mode === "live"} help={mode === "paper" || mode === "live" ? "Mandatory in this environment." : "Historical safety policy."} key={mode} label={`${readableLabel(mode)} safety`} onChange={(enabled) => replace({ ...selected, safety_supervisor: { enabled_by_environment: { ...selected.safety_supervisor.enabled_by_environment, [mode]: enabled } } })} value={selected.safety_supervisor.enabled_by_environment[mode]} />)}</div></ConfigGroup>
    </main>
    <aside className="configuration-dependency-inspector"><header><span>Run Plan graph</span><strong>Dependencies and validation</strong></header><section><h3>Readiness</h3><div className="configuration-readiness">{readiness.map((item) => <span data-ready={item.ready ? "true" : "false"} key={item.label}>{item.ready ? <CheckCircle2 size={14} /> : <TriangleAlert size={14} />}<span><strong>{item.label}</strong><small>{item.selection}</small></span></span>)}</div></section><section><h3>Used by</h3><p>{selected.enabled ? "Available to new published runtime releases." : "Disabled draft; safe to delete with its mandates."}</p></section><section><h3>Identity</h3><code>{selected.run_plan_id}</code><small>Schema v{draft.schema_version} · {selected.enabled ? "draft enabled" : "draft disabled"}</small></section><a className="button compact" href="#revision-configuration">Review and publish <ChevronRight size={13} /></a></aside>
  </div>;
}

export function createRunPlanMandate(runPlanId: string, accountKey: string, existing: Mandate[]): Mandate {
  return { mandate_id: uniqueId(`${runPlanId}-${accountKey}`, existing.map((row) => row.mandate_id)), run_plan_id: runPlanId, account_key: accountKey, enabled: true, maximum_cash_fraction: 1, maximum_planned_risk_fraction: 0.01, maximum_positions: 10, assignment_mode: "single", allocation_weight: 1, maximum_action_authority: "confirm", allow_replacement: false, minimum_replacement_improvement_pct: 20 };
}

export function DeploymentEditor({ draft, onChange }: { draft: Draft; onChange: (value: AssignmentSection) => void }) {
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
      watchlist_ids: draft.market_discovery.watchlists.slice(0, 1).map((row) => row.watchlist_id),
      signal_stream_ids: draft.market_discovery.signal_streams.filter((row) => row.enabled).slice(0, 1).map((row) => row.signal_stream_id),
      activation: { event_policy: "new_occurrences", watchlist_policy: "any_selected" },
      enablement: { state: "enabled", scope: "persistent", effective_session: "" },
      canvas_profile_id: "current-canvas",
      data_plan_ids: { replay: "market.historical_scanner_materialization.v1" },
      source_revision_policy: "require_complete",
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
        <AbstractionCard description={selected.description || "Connects a Strategy Profile, Watch Universe, OMS profile, environments, and runtime authority."} identity={selected.run_plan_id} kind="run_plan" metadata={[{ label: "Strategy profile", value: draft.strategy.profiles.find((row) => row.profile_id === selected.profile_id)?.name ?? selected.profile_id }, { label: "Watch Universe", value: section.universes.find((row) => row.universe_id === selected.universe_id)?.name ?? selected.universe_id }, { label: "OMS profile", value: draft.oms.profiles.find((row) => row.profile_id === selected.oms_profile_id)?.name ?? selected.oms_profile_id }, { label: "Environments", value: selected.allowed_environments.map(readableLabel).join(", ") }]} selected={selected.enabled} status={selected.enabled ? "Enabled" : "Disabled"} title={selected.name} />
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

export function WatchUniverseEditor({ onChange, section, selectedId }: {
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
  const sourceOptions = [
    { description: "Exact configured ticker set.", label: "Configured symbols", value: "configured_symbols" },
    { description: "Resolved causally from the named Market Discovery Watchlist.", label: "Watchlist", value: "watchlist" },
    ...(universe.source === "scanner_view" ? [{ description: "Legacy presentation-only source. Select a Watchlist or configured symbols before publication.", label: "Legacy scanner view", value: "scanner_view" }] : []),
  ];
  return (
    <div className="watch-universe-editor">
      <div className="configuration-field-grid">
        <label className="configuration-text-field"><span>Universe name</span><input onChange={(event) => replace({ ...universe, name: event.target.value })} value={universe.name} /></label>
        <SelectField help="Configured symbols are static. Watchlist is the versioned causal membership authority in Live/Paper and Replay/Backtest. Scanner views are presentation filters and are not valid Strategy universe sources." label="Source" onChange={(source) => replace({ ...universe, source: source as WatchUniverse["source"] })} options={sourceOptions} value={universe.source} />
      </div>
      {universe.source === "scanner_view" ? (
        <p className="configuration-safety-note"><TriangleAlert size={15} /> This legacy Scanner-view source remains fail closed because views are presentation filters, not causal membership authorities. Convert it to a Watchlist or configured-symbol source.</p>
      ) : universe.source === "watchlist" ? (
        <p className="configuration-safety-note"><BadgeCheck size={15} /> Watchlist resolution is implemented. Live and Paper wait for the current runtime snapshot; Replay and Backtest resolve membership at their pinned event clock.</p>
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

export function CampaignPolicyEditor({ deployment, onChange }: {
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

export function PortfolioEditor({ draft, onChange }: { draft: Draft; onChange: (value: PortfolioSection) => void }) {
  const section = draft.portfolio;
  const [activeTab, setActiveTab] = useState<"policies" | "mandates" | "groups">("policies");
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
      <nav aria-label="Portfolio configuration" className="configuration-domain-tabs">{([{"key":"policies","label":"Policies","count":section.policies.length},{"key":"mandates","label":"Mandates","count":section.mandates.length},{"key":"groups","label":"Groups","count":section.groups.length}] as const).map((tab) => <button aria-current={activeTab === tab.key ? "page" : undefined} key={tab.key} onClick={() => setActiveTab(tab.key)} type="button"><span>{tab.label}</span><em>{tab.count}</em></button>)}</nav>
      {activeTab === "policies" ? <ConfigGroup summary="Stable account-level limits apply to every strategy using the account." title="Account safety policy">
        <div className="configuration-toolbar">
          <SelectField help="Policy revision being edited." label="Policy" onChange={setSelectedPolicyId} options={section.policies.map((row) => ({ label: String(row.policy_id), value: String(row.policy_id) }))} value={selectedPolicyId} />
          <button className="button compact" onClick={clonePolicy} type="button"><Clipboard size={14} /> Clone policy</button>
        </div>
        {policy ? <AbstractionCard identity={`${String(policy.policy_id)}@${String(policy.revision)}`} kind="portfolio_policy" metadata={[{ label: "Eligible equity", value: Number(policy.eligible_equity_fraction || 0).toLocaleString(undefined, { style: "percent" }) }, { label: "Open positions", value: Number(policy.maximum_open_positions || 0).toLocaleString() }, { label: "Daily loss", value: Number(policy.maximum_daily_loss || 0).toLocaleString() }]} selected status="Configured" title={String(policy.name || policy.policy_id)} /> : null}
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
      </ConfigGroup> : null}
      {activeTab === "mandates" ? <ConfigGroup
        action={<button className="button compact" onClick={addMandate} type="button"><Plus size={14} /> Add mandate</button>}
        summary="Assign each Run Plan to one or more governed accounts."
        title="Strategy-account mandates"
      >
        <div className="mandate-grid">
          {section.mandates.map((mandate) => (
            <AbstractionCard actions={<button aria-label="Delete mandate" onClick={() => onChange({ ...section, mandates: section.mandates.filter((row) => row.mandate_id !== mandate.mandate_id) })} title="Delete mandate" type="button"><Trash2 size={14} /></button>} identity={mandate.mandate_id} key={mandate.mandate_id} kind="portfolio_mandate" metadata={[{ label: "Run Plan", value: deploymentName(draft.assignments, mandate.run_plan_id) }, { label: "Account", value: accountName(draft.accounts, mandate.account_key) }, { label: "Authority", value: readableLabel(mandate.maximum_action_authority) }]} status={readableLabel(mandate.assignment_mode)} title={`${deploymentName(draft.assignments, mandate.run_plan_id)} → ${accountName(draft.accounts, mandate.account_key)}`}>
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
            </AbstractionCard>
          ))}
        </div>
      </ConfigGroup> : null}
      {activeTab === "groups" ? <ConfigGroup action={<button className="button compact" onClick={addGroup} type="button"><Plus size={14} /> Add account group</button>} summary="Optional aggregate limits serialize decisions across several independently configured accounts." title="Cross-account risk groups">
        <div className="mandate-grid">
          {section.groups.map((group) => {
            const groupId = String(group.group_id || "");
            const accountKeys = Array.isArray(group.account_keys) ? group.account_keys.map(String) : [];
            return <AbstractionCard actions={<button aria-label={`Delete ${groupId}`} onClick={() => onChange({ ...section, groups: section.groups.filter((row) => String(row.group_id) !== groupId) })} type="button"><Trash2 size={14} /></button>} identity={groupId} key={groupId} kind="portfolio_group" metadata={[{ label: "Accounts", value: accountKeys.length }, { label: "Gross exposure", value: Number(group.maximum_gross_exposure || 0).toLocaleString() }, { label: "Ticker exposure", value: Number(group.maximum_ticker_exposure || 0).toLocaleString() }]} title={groupId}>
              <div className="configuration-field-grid one-column">
                <div className="configuration-fixed-value"><span>Stable group ID</span><strong>{groupId}</strong><small>Recorded in reservations and decisions.</small></div>
                <NumberField help="Maximum combined absolute exposure across group accounts." label="Gross exposure" minimum={0} onChange={(value) => replaceGroup(groupId, { ...group, maximum_gross_exposure: value })} step={1000} unit="currency" value={Number(group.maximum_gross_exposure || 0)} />
                <NumberField help="Maximum combined exposure to one ticker across group accounts." label="Ticker exposure" minimum={0} onChange={(value) => replaceGroup(groupId, { ...group, maximum_ticker_exposure: value })} step={1000} unit="currency" value={Number(group.maximum_ticker_exposure || 0)} />
              </div>
              <fieldset className="configuration-choice-set"><legend>Member accounts</legend><div>{draft.accounts.bindings.map((account) => <label key={account.account_key}><input checked={accountKeys.includes(account.account_key)} onChange={(event) => replaceGroup(groupId, { ...group, account_keys: event.target.checked ? [...accountKeys, account.account_key] : accountKeys.filter((key) => key !== account.account_key) })} type="checkbox" />{account.name}</label>)}</div></fieldset>
            </AbstractionCard>;
          })}
          {!section.groups.length ? <EmptyState title="No cross-account groups" detail="Each account remains independently governed until an aggregate group is added." /> : null}
        </div>
      </ConfigGroup> : null}
    </div>
  );
}

export function OmsEditor({ onChange, section }: { onChange: (value: OmsSection) => void; section: OmsSection }) {
  const [activeTab, setActiveTab] = useState<"profiles" | "execution" | "protection">("profiles");
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
    <nav aria-label="OMS configuration" className="configuration-domain-tabs">{([{"key":"profiles","label":"OMS Profiles","count":section.profiles.length},{"key":"execution","label":"Execution Policies","count":section.execution_policies.length},{"key":"protection","label":"Protection Profiles","count":section.protection_profiles.length}] as const).map((tab) => <button aria-current={activeTab === tab.key ? "page" : undefined} key={tab.key} onClick={() => setActiveTab(tab.key)} type="button"><span>{tab.label}</span><em>{tab.count}</em></button>)}</nav>
    {activeTab === "profiles" ? <div className="configuration-workbench">
      <aside className="configuration-library">
        <header><div><span>OMS profiles</span><strong>{section.profiles.length} configured</strong></div><button onClick={clone} title="Clone OMS profile" type="button"><Plus size={15} /></button></header>
        <p>Reusable profiles keep execution mechanics consistent across strategies and modes.</p>
        <div>{section.profiles.map((row) => <button className={row.profile_id === selected.profile_id ? "active" : ""} key={row.profile_id} onClick={() => setSelectedId(row.profile_id)} type="button"><span><strong>{row.name}</strong><small>{row.origin} · v{row.revision}</small></span><ChevronRight size={14} /></button>)}</div>
      </aside>
      <main className="configuration-detail">
        <section className="configuration-detail-heading"><div><span>Reusable OMS profile</span><input aria-label="OMS profile name" onChange={(event) => replace({ ...selected, name: event.target.value })} value={selected.name} /><textarea aria-label="OMS profile summary" onChange={(event) => replace({ ...selected, description: event.target.value })} rows={2} value={selected.description} /></div><button className="button compact" onClick={clone} type="button"><Clipboard size={14} /> Clone</button></section>
        <AbstractionCard description={selected.description} identity={selected.profile_id} kind="oms_profile" metadata={[{ label: "Revision", value: selected.revision }, { label: "Origin", value: readableLabel(selected.origin) }, { label: "Entry policy", value: selected.settings.entry_execution_policy_id }, { label: "Protection", value: selected.settings.protection_profile_id }]} selected status="Configured" title={selected.name} />
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
    </div> : null}
    {activeTab === "execution" ? <ExecutionPoliciesEditor policies={section.execution_policies} onChange={(execution_policies) => onChange({ ...section, execution_policies })} /> : null}
    {activeTab === "protection" ? <ProtectionProfilesEditor profiles={section.protection_profiles} onChange={(protection_profiles) => onChange({ ...section, protection_profiles })} /> : null}
    </div>
  );
}

export function ExecutionPoliciesEditor({ onChange, policies }: { onChange: (value: ExecutionPolicyConfig[]) => void; policies: ExecutionPolicyConfig[] }) {
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
    <AbstractionCard description={selected.description} identity={`${selected.policy_id}@${selected.revision}`} kind="execution_policy" metadata={[{ label: "Quote source", value: readableLabel(selected.quote_source) }, { label: "Partial fill", value: readableLabel(selected.partial_fill_policy) }, { label: "Deadline", value: `${selected.envelope.deadline_ms} ms` }]} selected status={readableLabel(selected.origin)} title={readableLabel(selected.name)} />
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

export function ProtectionProfilesEditor({ onChange, profiles }: { onChange: (value: ProtectionProfileConfig[]) => void; profiles: ProtectionProfileConfig[] }) {
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
    <AbstractionCard identity={`${selected.profile_id}@${selected.revision}`} kind="protection_profile" metadata={[{ label: "Slices", value: selected.slices.length }, { label: "Add policy", value: readableLabel(selected.add_policy) }, { label: "Profit transition", value: readableLabel(selected.profit_pocket_transition) }]} selected status={readableLabel(selected.origin)} title={selected.name} />
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

export function AccountsEditor({ draft, onChange, onSessionsChange }: { draft: Draft; onChange: (value: AccountSection) => void; onSessionsChange: (value: SessionSection) => void }) {
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
  function replaceProfile(profileIndex: number, profile: SessionProfile) {
    onSessionsChange({ ...draft.sessions, profiles: draft.sessions.profiles.map((row, index) => index === profileIndex ? profile : row) });
  }
  function replaceRoute(route: ExecutionRoute) {
    onSessionsChange({ ...draft.sessions, execution_routes: draft.sessions.execution_routes.map((row) => row.execution_route_id === route.execution_route_id ? { ...route, system_generated: false } : row) });
  }
  function replaceStrategyDeployment(deployment: StrategyDeployment) {
    onSessionsChange({ ...draft.sessions, strategy_deployments: draft.sessions.strategy_deployments.map((row) => row.strategy_deployment_id === deployment.strategy_deployment_id ? { ...deployment, system_generated: false } : row) });
  }
  return (
    <div className="configuration-stack">
      <GuideCallout icon={<Boxes size={17} />} title="Session Profile → Execution Route → account, Portfolio, and OMS">
        Manual and semi-automatic trading use a Session Profile directly. A Strategy Deployment may use the same route headlessly; Canvas is an optional presentation attachment and never owns execution.
      </GuideCallout>
      <ConfigGroup summary="Session Profiles own market clock and data authority. Execution Routes bind accounts, Portfolio mandates, and OMS without requiring a Strategy or Canvas." title="Session Profiles and execution routes">
        <div className="account-config-grid">{draft.sessions.profiles.map((profile, profileIndex) => {
          const routes = draft.sessions.execution_routes.filter((route) => route.session_profile_id === profile.session_profile_id);
          return <AbstractionCard actions={<label className="configuration-switch"><input checked={profile.enabled} onChange={(event) => replaceProfile(profileIndex, { ...profile, enabled: event.target.checked })} type="checkbox" /><span /></label>} description={profile.description} identity={profile.session_profile_id} key={profile.session_profile_id} kind="processing_step" metadata={[{ label: "Modes", value: profile.modes.map(readableLabel).join(", ") }, { label: "Data", value: profile.market_data.authority }, { label: "Clock", value: readableLabel(profile.market_data.clock) }, { label: "Routes", value: routes.length }]} selected={profile.enabled} status={profile.enabled ? "Ready" : "Disabled"} title={profile.name}>
            <div className="configuration-field-grid one-column">
              <TextField help="Operator-facing session name." label="Session name" onChange={(name) => replaceProfile(profileIndex, { ...profile, name })} value={profile.name} />
              <TextField help="Explain the clock, market-data authority, and intended execution modes." label="Description" onChange={(description) => replaceProfile(profileIndex, { ...profile, description })} value={profile.description} />
              <ModeSelector modes={profile.modes} onChange={(modes) => replaceProfile(profileIndex, { ...profile, modes })} />
              <SelectField help="Authoritative data plane for this session." label="Market-data authority" onChange={(authority) => replaceProfile(profileIndex, { ...profile, market_data: { ...profile.market_data, authority } })} options={[{ label: "QMD Live", value: "qmd_live" }, { label: "QMD History", value: "qmd_history" }]} value={profile.market_data.authority} />
              <SelectField help="Clock that timestamps decisions and orders." label="Clock" onChange={(clock) => replaceProfile(profileIndex, { ...profile, market_data: { ...profile.market_data, clock } })} options={[{ label: "Exchange time", value: "exchange_time" }, { label: "Event time", value: "event_time" }]} value={profile.market_data.clock} />
              <BooleanField help="Allows manual and Trading Action proposals to use this Session Profile without a Strategy Run Plan." label="Manual and semi-automatic trading" onChange={(enabled) => replaceProfile(profileIndex, { ...profile, manual_authority: { ...profile.manual_authority, enabled } })} value={profile.manual_authority.enabled} />
              {routes.map((route) => <article className="configuration-nested-card" key={route.execution_route_id}><div className="configuration-nested-card-header"><div><span>Execution Route</span><strong>{route.name}</strong><small>{route.execution_route_id}</small></div><label className="configuration-switch"><input checked={route.enabled} onChange={(event) => replaceRoute({ ...route, enabled: event.target.checked })} type="checkbox" /><span /></label></div><div className="configuration-field-grid two-columns"><TextField help="Name shown when selecting an execution path." label="Route name" onChange={(name) => replaceRoute({ ...route, name })} value={route.name} /><SelectField help="Stable broker or simulated account binding." label="Account" onChange={(account_key) => replaceRoute({ ...route, account_key })} options={draft.accounts.bindings.map((row) => ({ label: row.name, value: row.account_key }))} value={route.account_key} /><SelectField help="Portfolio allocation and risk authority for this session and account." label="Portfolio mandate" onChange={(portfolio_mandate_id) => replaceRoute({ ...route, portfolio_mandate_id })} options={draft.portfolio.mandates.filter((row) => row.account_key === route.account_key && row.principal_kind === "session" && row.principal_id === profile.session_profile_id).map((row) => ({ label: row.mandate_id, value: row.mandate_id }))} value={route.portfolio_mandate_id} /><SelectField help="OMS execution and protection contract." label="OMS profile" onChange={(oms_profile_id) => replaceRoute({ ...route, oms_profile_id })} options={draft.oms.profiles.map((row) => ({ label: row.name, value: row.profile_id }))} value={route.oms_profile_id} /><BooleanField help="Permit manual orders and confirmed Trading Actions through this route." label="Manual route" onChange={(manual_enabled) => replaceRoute({ ...route, manual_enabled })} value={route.manual_enabled} /></div><ModeSelector modes={route.modes} onChange={(modes) => replaceRoute({ ...route, modes })} /></article>)}
            </div>
          </AbstractionCard>;
        })}</div>
      </ConfigGroup>
      <ConfigGroup summary="Strategy Deployments bind reusable Run Plans to sessions and routes. Headless deployments do not depend on an open Canvas." title="Strategy Deployments">
        <div className="account-config-grid">{draft.sessions.strategy_deployments.map((deployment) => {
          const availableRoutes = draft.sessions.execution_routes.filter((route) => route.session_profile_id === deployment.session_profile_id);
          return <AbstractionCard actions={<label className="configuration-switch"><input checked={deployment.enabled} onChange={(event) => replaceStrategyDeployment({ ...deployment, enabled: event.target.checked })} type="checkbox" /><span /></label>} description={deployment.description} identity={deployment.strategy_deployment_id} key={deployment.strategy_deployment_id} kind="run_plan" metadata={[{ label: "Run Plan", value: draft.assignments.deployments.find((row) => row.run_plan_id === deployment.run_plan_id)?.name ?? deployment.run_plan_id }, { label: "Session", value: draft.sessions.profiles.find((row) => row.session_profile_id === deployment.session_profile_id)?.name ?? deployment.session_profile_id }, { label: "Routes", value: deployment.execution_route_ids.length }, { label: "Runtime", value: deployment.headless ? "Headless" : "Launch controlled" }]} selected={deployment.enabled} status={deployment.enabled ? "Enabled" : "Disabled"} title={deployment.name}><div className="configuration-field-grid two-columns"><TextField help="Operator-facing deployment name." label="Deployment name" onChange={(name) => replaceStrategyDeployment({ ...deployment, name })} value={deployment.name} /><TextField help="Explain when this deployment should run." label="Description" onChange={(description) => replaceStrategyDeployment({ ...deployment, description })} value={deployment.description} /><SelectField help="Session whose clock, market data, and execution routes this strategy uses." label="Session Profile" onChange={(session_profile_id) => replaceStrategyDeployment({ ...deployment, session_profile_id, execution_route_ids: [] })} options={draft.sessions.profiles.map((row) => ({ label: row.name, value: row.session_profile_id }))} value={deployment.session_profile_id} /><NumberField help="Lower numbers win deterministic arbitration when deployments compete before a ticker campaign is owned." label="Priority" minimum={0} onChange={(priority) => replaceStrategyDeployment({ ...deployment, priority })} step={1} value={deployment.priority} /><BooleanField help="Keep the strategy runtime active without any Canvas. Canvas may attach later using the run ID." label="Headless runtime" onChange={(headless) => replaceStrategyDeployment({ ...deployment, headless })} value={deployment.headless} /></div><ModeSelector modes={deployment.modes} onChange={(modes) => replaceStrategyDeployment({ ...deployment, modes })} /><fieldset className="configuration-choice-list"><legend>Execution routes</legend>{availableRoutes.map((route) => { const checked = deployment.execution_route_ids.includes(route.execution_route_id); return <label key={route.execution_route_id}><input checked={checked} onChange={(event) => replaceStrategyDeployment({ ...deployment, execution_route_ids: event.target.checked ? [...deployment.execution_route_ids, route.execution_route_id] : deployment.execution_route_ids.filter((value) => value !== route.execution_route_id) })} type="checkbox" /><span><strong>{route.name}</strong><small>{accountName(draft.accounts, route.account_key)} · {route.modes.map(readableLabel).join(", ")}</small></span></label>; })}</fieldset></AbstractionCard>;
        })}</div>
      </ConfigGroup>
      <ConfigGroup action={<button className="button compact" onClick={addAccount} type="button"><Plus size={14} /> Add account</button>} summary="Account settings are reusable across published Strategies." title="Configured accounts">
        <div className="account-config-grid">
          {section.bindings.map((account, index) => (
            <AbstractionCard actions={<label className="configuration-switch"><input checked={account.enabled} onChange={(event) => replace(index, { ...account, enabled: event.target.checked })} type="checkbox" /><span /></label>} identity={account.account_key} key={account.account_key} kind="account_binding" metadata={[{ label: "Class", value: readableLabel(account.account_class) }, { label: "Modes", value: account.modes.map(readableLabel).join(", ") }, { label: "Portfolio policy", value: account.portfolio_policy_id }]} selected={account.enabled} status={account.enabled ? "Enabled" : "Disabled"} title={account.name}>
              <div className="configuration-field-grid one-column">
                <TextField help="Human-readable name shown throughout configuration and runtime evidence." label="Account name" onChange={(value) => replace(index, { ...account, name: value })} value={account.name} />
                <div className="configuration-fixed-value"><span>Stable account key</span><strong>{account.account_key}</strong><small>Mandates, groups, and runtime state refer to this identity.</small></div>
                {account.modes.some((mode) => mode === "paper" || mode === "live") ? <div className="configuration-fixed-value"><span>IBKR account ID source</span><strong>{account.source_account_env || "Missing server-side binding"}</strong><small>The broker identity is resolved only by the backend and is never returned to this page or stored in a release.</small></div> : <TextField help="Simulated runtime account identity." label="Source account" onChange={(value) => replace(index, { ...account, source_account_id: value })} value={account.source_account_id} />}
                <SelectField help="Determines broker capability and regulatory constraints." label="Account class" onChange={(value) => replace(index, { ...account, account_class: value })} options={["simulated", "paper", "cash", "margin", "registered"].map((value) => ({ label: readableLabel(value), value }))} value={account.account_class} />
                <SelectField help="Reusable account-level capital and risk policy." label="Portfolio policy" onChange={(value) => replace(index, { ...account, portfolio_policy_id: value })} options={draft.portfolio.policies.map((row) => ({ label: String(row.policy_id), value: String(row.policy_id) }))} value={account.portfolio_policy_id} />
                <TextField help="Gateway or simulated session identity used to locate runtime state." label="Session key" onChange={(value) => replace(index, { ...account, session_key: value })} value={account.session_key} />
                <TextField help="Currency used for Portfolio limits and account summaries." label="Base currency" onChange={(value) => replace(index, { ...account, base_currency: value.toUpperCase() })} value={account.base_currency} />
              </div>
              <ModeSelector modes={account.modes} onChange={(modes) => replace(index, { ...account, modes })} />
              {account.modes.some((mode) => mode === "paper" || mode === "live") ? <p className="configuration-safety-note"><ShieldCheck size={15} /> Publication and broker preflight require this exact account ID, a matching external IBKR discovery binding, and compiled runtime coverage for every selected live mode.</p> : null}
            </AbstractionCard>
          ))}
        </div>
      </ConfigGroup>
    </div>
  );
}
