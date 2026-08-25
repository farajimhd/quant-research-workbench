import { BadgeCheck, CheckCircle2, LockKeyhole, Send, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../../api/client";
import { AbstractionCard } from "../../app/components/AbstractionCard";
import { ConfigGroup, FieldHelp, JsonInspector, SelectField, readableLabel } from "./components/ConfigurationFields";
import type { Draft, RuntimeMode } from "./contracts";
import { serializeDraft } from "./draft";
import { canvasApprovalSnapshot } from "./utilities";
export type Revision = {
  approved_at: string;
  content_hash: string;
  label: string;
  payload: Draft & { canvas: { profile: Record<string, unknown>; revision: string } };
  revision: number;
  revision_id: string;
};

export type TestCandidate = {
  candidate_id: string;
  candidate_revision: number;
  content_hash: string;
  created_at: string;
  label: string;
  payload: Revision["payload"];
  release_state: "test_candidate";
};


export function RevisionBadge({ approved, candidate }: { approved: Revision | null; candidate?: TestCandidate }) {
  const Icon = approved || candidate ? BadgeCheck : LockKeyhole;
  return <div className="configuration-revision-badge" data-approved={approved ? "true" : "false"}><span className="configuration-revision-icon"><Icon aria-hidden="true" size={16} /></span><span className="configuration-revision-copy"><small>Runtime authority</small><strong>{approved ? `Release ${approved.revision}` : candidate ? `Test t${candidate.candidate_revision}` : "Session only"}</strong><span>{approved ? approved.label : candidate ? "Debug and Backtest only" : "Create a Test Candidate"}</span></span></div>;
}

export function RevisionPublisher({ approved, candidates = [], draft, guided = false, label, onLabelChange, onPublish, publishing, revisions }: {
  approved: Revision | null;
  candidates?: TestCandidate[];
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
        <header><div><span>{guided ? "Ready to test" : "Test-first gate"}</span><strong>Create an immutable Test Candidate</strong></div><Send size={18} /></header>
        <p>This freezes every referenced Strategy, Run Plan, mandate, policy, OMS configuration, account binding, and Canvas reference for reproducible Debug and Backtest runs. It does not authorize Paper or Live trading.</p>
        <div className="configuration-publish-proof">
          {visibleChecks.map((check) => <span data-ready={check.ready ? "true" : "false"} key={check.label}>{check.ready ? <CheckCircle2 size={14} /> : <TriangleAlert size={14} />} {guided ? publishCheckLabel(check.label) : check.label} · {check.detail}</span>)}
          <span data-ready="true"><CheckCircle2 size={14} /> Optional Canvas · {canvas.containerCount} saved containers</span>
        </div>
        <label><span>Candidate label <FieldHelp content="Use a short label that identifies the hypothesis and parameters under test." /></span><input onChange={(event) => onLabelChange(event.target.value)} placeholder="Long momentum squeeze · baseline" value={label} /></label>
        <button className="button primary" disabled={!draft || !configurationReady || !label.trim() || publishing} onClick={onPublish} type="button"><Send size={15} /> {publishing ? "Freezing…" : "Create Test Candidate"}</button>
      </section>
      <section className="configuration-history-card">
        <header><span>Immutable test history</span><strong>{candidates.length} candidate{candidates.length === 1 ? "" : "s"}</strong></header>
        <div>{candidates.map((candidate) => <article key={candidate.candidate_id}><span><strong>t{candidate.candidate_revision} · {candidate.label}</strong><small>{new Date(candidate.created_at).toLocaleString()}</small></span><code>{candidate.content_hash.slice(0, 12)}</code></article>)}{!candidates.length ? <div className="configuration-empty-history">No Test Candidate exists. Create one before Debug or Backtest.</div> : null}</div>
        {revisions.length ? <><header><span>Promoted history</span><strong>{revisions.length} approved release{revisions.length === 1 ? "" : "s"}</strong></header><div>{revisions.map((revision) => <article data-current={revision.revision_id === approved?.revision_id ? "true" : "false"} key={revision.revision_id}><span><strong>r{revision.revision} · {revision.label}</strong><small>{new Date(revision.approved_at).toLocaleString()}</small></span><code>{revision.content_hash.slice(0, 12)}</code></article>)}</div></> : null}
      </section>
      {draft ? <JsonInspector label="Complete generated release JSON" value={draft} /> : null}
    </div>
  );
}

export function releaseReadiness(draft: Draft) {
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

export function publishCheckLabel(label: string) {
  if (label === "Runtime compilation") return "Trading setup";
  if (label === "Mode coverage") return "Selected modes";
  if (label === "Paper and Live bindings") return "Broker connection";
  return label;
}

export function EffectiveConfigurationPreview({ draft }: { draft: Draft }) {
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
