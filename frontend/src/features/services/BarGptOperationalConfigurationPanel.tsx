import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Cpu,
  DatabaseZap,
  Layers3,
  Loader2,
  RefreshCcw,
  RotateCcw,
  Save,
  ShieldCheck,
  WifiOff,
} from "lucide-react";
import { useRef, useState, type ReactNode } from "react";

import { api } from "../../api/client";
import { Button } from "../../app/components/Button";
import { displayName, formatCompactNumber } from "../../app/format";
import { usePollingTask } from "../../app/hooks/usePollingTask";
import { ServicePanel as Panel } from "./ServicePanel";
import { formatServiceTime as formatTime } from "./time";

type BarGptOperationalSettings = {
  selected_release_ids: string[];
  release_roles: Record<string, "champion" | "shadow">;
  device: "auto" | "cuda" | "cpu";
  dtype: "bfloat16" | "float16" | "float32";
  maximum_tickers: number;
  maximum_batch_size: number;
  maximum_batch_delay_ms: number;
  queue_capacity: number;
  warm_concurrency: number;
  minimum_warm_1s_bars: number;
  prediction_history: number;
  connect_qmd: boolean;
};

type BarGptRelease = {
  release_id: string;
  model_id: string;
  version: "v2" | "v3";
  artifact_name: string;
  selected: boolean;
  desired_role: "champion" | "shadow";
  effective: boolean;
  effective_role: string;
  checkpoint_hash: string;
  contract_hash: string;
  parameter_count?: number | null;
  context_bars: Record<string, number>;
  horizons_us: number[];
};

type BarGptOperationalConfiguration = {
  schema_version: number;
  revision: number;
  updated_at: string;
  authority: string;
  desired: BarGptOperationalSettings;
  effective: BarGptOperationalSettings;
  restart_required: boolean;
  releases: BarGptRelease[];
  runtime: {
    status?: string;
    scope_count: number;
    active_ticker_count: number;
    queue: { active?: number; capacity?: number };
    cache_count: number;
  };
};

export function BarGptOperationalConfigurationPanel() {
  const [payload, setPayload] = useState<BarGptOperationalConfiguration | null>(null);
  const [draft, setDraft] = useState<BarGptOperationalSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const dirty = Boolean(payload && draft && JSON.stringify(payload.desired) !== JSON.stringify(draft));
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  async function load(showLoading = false, signal?: AbortSignal) {
    if (showLoading) setLoading(true);
    try {
      const next = await api<BarGptOperationalConfiguration>("/api/bar-gpt/configuration", { signal, timeoutMs: 5000 });
      setPayload(next);
      if (!dirtyRef.current) setDraft(structuredClone(next.desired));
      setError("");
    } catch (exc) {
      if (signal?.aborted) return;
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  usePollingTask({
    initialDelayMs: 0,
    intervalMs: 5_000,
    task: (signal) => load(false, signal),
  });

  function replaceSetting<K extends keyof BarGptOperationalSettings>(key: K, value: BarGptOperationalSettings[K]) {
    setDraft((current) => current ? { ...current, [key]: value } : current);
    setMessage("");
  }

  function toggleRelease(releaseId: string, selected: boolean) {
    if (!draft) return;
    const selectedIds = selected
      ? [...new Set([...draft.selected_release_ids, releaseId])].sort()
      : draft.selected_release_ids.filter((value) => value !== releaseId);
    const roles = { ...draft.release_roles };
    if (selected && !roles[releaseId]) roles[releaseId] = "shadow";
    if (!selected) delete roles[releaseId];
    setDraft({ ...draft, release_roles: roles, selected_release_ids: selectedIds });
    setMessage("");
  }

  function replaceRole(releaseId: string, role: "champion" | "shadow") {
    if (!draft) return;
    const releaseRoles = { ...draft.release_roles, [releaseId]: role };
    if (role === "champion") {
      for (const selectedId of draft.selected_release_ids) {
        if (selectedId !== releaseId) releaseRoles[selectedId] = "shadow";
      }
    }
    setDraft({ ...draft, release_roles: releaseRoles });
    setMessage("");
  }

  async function save() {
    if (!payload || !draft || !dirty) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const next = await api<BarGptOperationalConfiguration>("/api/bar-gpt/configuration", {
        method: "PUT",
        body: JSON.stringify({ expected_revision: payload.revision, ...draft }),
        timeoutMs: 10000,
      });
      setPayload(next);
      setDraft(structuredClone(next.desired));
      setMessage(next.restart_required ? "Desired configuration saved. Restart BarGPT to activate it." : "Configuration is active.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  if (loading && !payload) {
    return <Panel title="BarGPT Operational Configuration"><div className="bar-gpt-config-state"><Loader2 size={18} /><span>Loading service-owned configuration…</span></div></Panel>;
  }
  if (!payload || !draft) {
    return <Panel title="BarGPT Operational Configuration"><div className="bar-gpt-config-state is-error"><WifiOff size={18} /><div><strong>Configuration authority unavailable</strong><span>{error || "Start BarGPT to inspect or change its operational settings."}</span></div><Button onClick={() => void load(true)} variant="secondary"><RefreshCcw size={14} /> Retry</Button></div></Panel>;
  }

  const queueActive = Number(payload.runtime.queue.active ?? 0);
  const queueCapacity = Number(payload.runtime.queue.capacity ?? draft.queue_capacity);
  return (
    <Panel className="bar-gpt-configuration-panel" title="BarGPT Operational Configuration">
      <div className="bar-gpt-config">
        <div className="bar-gpt-config-summary">
          <BarGptSummary icon={ShieldCheck} label="Release authority" value={`${draft.selected_release_ids.length} selected`} detail={`Revision ${payload.revision}`} />
          <BarGptSummary icon={Cpu} label="Effective runtime" value={`${displayName(payload.effective.device)} · ${displayName(payload.effective.dtype)}`} detail={payload.restart_required ? `Desired ${displayName(draft.device)} · ${displayName(draft.dtype)}` : "Desired is effective"} tone={payload.restart_required ? "warn" : "ok"} />
          <BarGptSummary icon={DatabaseZap} label="Causal caches" value={String(payload.runtime.cache_count)} detail={`${payload.runtime.active_ticker_count} active tickers`} />
          <BarGptSummary icon={Layers3} label="Inference queue" value={`${formatCompactNumber(queueActive)} / ${formatCompactNumber(queueCapacity)}`} detail={`${payload.runtime.scope_count} active scopes`} tone={queueActive ? "active" : "neutral"} />
        </div>

        {payload.restart_required ? <div className="bar-gpt-restart-notice"><AlertTriangle size={16} /><div><strong>Restart required</strong><span>The saved desired configuration is durable, but the running process continues with the effective values shown beside each field.</span></div></div> : null}
        {error ? <div className="bar-gpt-config-feedback is-error"><AlertTriangle size={15} /><span>{error}</span></div> : null}
        {message ? <div className="bar-gpt-config-feedback is-success"><CheckCircle2 size={15} /><span>{message}</span></div> : null}

        <section className="bar-gpt-config-section" aria-labelledby="bar-gpt-release-heading">
          <div className="bar-gpt-config-section-heading">
            <div><span>Immutable registry</span><h3 id="bar-gpt-release-heading">Promoted releases</h3><p>Select only server-registered artifacts. Checkpoint paths are intentionally not editable or exposed.</p></div>
            <span>{payload.releases.length} registered</span>
          </div>
          <div className="bar-gpt-release-table">
            <div className="bar-gpt-release-table-head"><span>Serve</span><span>Release</span><span>Role</span><span>Model evidence</span><span>State</span></div>
            {payload.releases.length ? payload.releases.map((release) => {
              const selected = draft.selected_release_ids.includes(release.release_id);
              return <div className="bar-gpt-release-row" data-selected={selected ? "true" : "false"} key={release.release_id}>
                <label className="bar-gpt-release-toggle"><input aria-label={`Serve ${release.model_id}`} checked={selected} onChange={(event) => toggleRelease(release.release_id, event.target.checked)} type="checkbox" /><span /></label>
                <div className="bar-gpt-release-identity"><strong>{release.model_id}</strong><span>{release.version.toUpperCase()} · {release.artifact_name}</span></div>
                <select aria-label={`${release.model_id} serving role`} disabled={!selected} onChange={(event) => replaceRole(release.release_id, event.target.value as "champion" | "shadow")} value={draft.release_roles[release.release_id] ?? release.desired_role}><option value="champion">Champion</option><option value="shadow">Shadow</option></select>
                <div className="bar-gpt-release-evidence"><strong>{release.parameter_count ? `${formatCompactNumber(release.parameter_count)} parameters` : "Not loaded"}</strong><span title={release.checkpoint_hash}>{release.checkpoint_hash ? `Checkpoint ${shortHash(release.checkpoint_hash)}` : "Checkpoint hash available after load"}</span><span title={release.contract_hash}>{release.contract_hash ? `Contract ${shortHash(release.contract_hash)}` : "Contract hash available after load"}</span></div>
                <span className={`bar-gpt-release-state ${release.effective ? "is-effective" : selected ? "is-pending" : "is-idle"}`}>{release.effective ? `Effective ${displayName(release.effective_role)}` : selected ? "Pending restart" : "Not selected"}</span>
              </div>;
            }) : <div className="bar-gpt-release-empty">No promoted release records are registered on this service. Configure the server-side release registry before enabling inference.</div>}
          </div>
        </section>

        <div className="bar-gpt-config-columns">
          <BarGptSettingsGroup description="GPU placement and dynamic batch bounds. Effective values remain active until restart." title="Compute and capacity">
            <BarGptSelect label="Device" value={draft.device} effective={payload.effective.device} onChange={(value) => replaceSetting("device", value as BarGptOperationalSettings["device"])} options={["auto", "cuda", "cpu"]} />
            <BarGptSelect label="Precision" value={draft.dtype} effective={payload.effective.dtype} onChange={(value) => replaceSetting("dtype", value as BarGptOperationalSettings["dtype"])} options={["bfloat16", "float16", "float32"]} />
            <BarGptNumber label="Maximum tickers" value={draft.maximum_tickers} effective={payload.effective.maximum_tickers} min={1} max={5000} onChange={(value) => replaceSetting("maximum_tickers", value)} />
            <BarGptNumber label="Maximum batch size" value={draft.maximum_batch_size} effective={payload.effective.maximum_batch_size} min={1} max={2048} onChange={(value) => replaceSetting("maximum_batch_size", value)} />
            <BarGptNumber label="Batch delay" unit="ms" value={draft.maximum_batch_delay_ms} effective={payload.effective.maximum_batch_delay_ms} min={0} max={1000} onChange={(value) => replaceSetting("maximum_batch_delay_ms", value)} />
            <BarGptNumber label="Warm workers" value={draft.warm_concurrency} effective={payload.effective.warm_concurrency} min={1} max={128} onChange={(value) => replaceSetting("warm_concurrency", value)} />
          </BarGptSettingsGroup>
          <BarGptSettingsGroup description="Fixed causal context operation, queue protection, and retained prediction evidence." title="Cache and resilience">
            <BarGptNumber label="Inference queue" value={draft.queue_capacity} effective={payload.effective.queue_capacity} min={1} max={1_000_000} onChange={(value) => replaceSetting("queue_capacity", value)} />
            <BarGptNumber label="Minimum warm 1s bars" value={draft.minimum_warm_1s_bars} effective={payload.effective.minimum_warm_1s_bars} min={1} max={100_000} onChange={(value) => replaceSetting("minimum_warm_1s_bars", value)} />
            <BarGptNumber label="Prediction history" value={draft.prediction_history} effective={payload.effective.prediction_history} min={1} max={1_000_000} onChange={(value) => replaceSetting("prediction_history", value)} />
            <label className="bar-gpt-boolean-field"><span><strong>Connect QMD Live</strong><small>Consume the canonical compact-event stream for live cache updates.</small></span><input checked={draft.connect_qmd} onChange={(event) => replaceSetting("connect_qmd", event.target.checked)} type="checkbox" /><em>{payload.effective.connect_qmd ? "Effective: enabled" : "Effective: disabled"}</em></label>
          </BarGptSettingsGroup>
        </div>

        <div className="bar-gpt-config-ownership">
          <div><strong>Application intent stays in Market Discovery</strong><span>Watchlists, Auto or Manual mode, selected model use, ticker limits, Data Fields, Rule Sets, and Signal Streams remain part of the approved application revision.</span></div>
          <Button onClick={() => { window.location.hash = "market-discovery-configuration"; }} variant="secondary">Open Market Discovery <ArrowUpRight size={14} /></Button>
        </div>

        <div className="bar-gpt-config-actions">
          <span>{dirty ? "Unsaved desired configuration" : payload.updated_at ? `Saved ${formatTime(payload.updated_at)}` : "Using launch configuration"}</span>
          <Button disabled={!dirty || saving} onClick={() => { setDraft(structuredClone(payload.desired)); setMessage(""); setError(""); }} variant="secondary"><RotateCcw size={14} /> Reset</Button>
          <Button disabled={!dirty || saving} onClick={() => void save()} variant="primary">{saving ? <Loader2 size={14} /> : <Save size={14} />} Save desired configuration</Button>
        </div>
      </div>
    </Panel>
  );
}

function BarGptSummary({ detail, icon: Icon, label, tone = "neutral", value }: { detail: string; icon: typeof Cpu; label: string; tone?: string; value: string }) {
  return <div className={`bar-gpt-config-summary-item tone-${tone}`}><Icon size={16} /><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function BarGptSettingsGroup({ children, description, title }: { children: ReactNode; description: string; title: string }) {
  return <section className="bar-gpt-settings-group"><header><h3>{title}</h3><p>{description}</p></header><div className="bar-gpt-settings-grid">{children}</div></section>;
}

function BarGptSelect({ effective, label, onChange, options, value }: { effective: string; label: string; onChange: (value: string) => void; options: string[]; value: string }) {
  return <label className="bar-gpt-setting-field"><span>{label}</span><select onChange={(event) => onChange(event.target.value)} value={value}>{options.map((option) => <option key={option} value={option}>{displayName(option)}</option>)}</select><small>Effective: {displayName(effective)}</small></label>;
}

function BarGptNumber({ effective, label, max, min, onChange, unit = "", value }: { effective: number; label: string; max: number; min: number; onChange: (value: number) => void; unit?: string; value: number }) {
  return <label className="bar-gpt-setting-field"><span>{label}</span><div><input max={max} min={min} onChange={(event) => onChange(Math.max(min, Math.min(max, Number(event.target.value) || min)))} type="number" value={value} />{unit ? <em>{unit}</em> : null}</div><small>Effective: {formatCompactNumber(effective)}{unit ? ` ${unit}` : ""}</small></label>;
}

function shortHash(value: string) {
  return value.length > 15 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}
