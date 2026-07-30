import {
  BadgeCheck,
  BookOpenCheck,
  Boxes,
  BriefcaseBusiness,
  CheckCircle2,
  GitBranch,
  Network,
  Save,
  Send,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { readCanvasRegistry, snapshotCanvasProfile } from "../app/canvasWorkspace";

export type TradingConfigurationSection =
  | "strategy"
  | "assignments"
  | "portfolio"
  | "oms"
  | "accounts"
  | "revisions";

type Draft = {
  accounts: unknown;
  assignments: unknown;
  oms: unknown;
  portfolio: unknown;
  strategy: unknown;
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
    eyebrow: "Decision authority",
    icon: GitBranch,
    title: "Strategies",
    description: "Select the executable strategy revision and its approved parameter set.",
  },
  assignments: {
    eyebrow: "Deployment scope",
    icon: Network,
    title: "Assignments",
    description: "Bind approved strategies to account keys and instruments. Replay may change assignment state locally, but never the definition.",
  },
  portfolio: {
    eyebrow: "Capital authority",
    icon: BriefcaseBusiness,
    title: "Portfolio & Risk",
    description: "Define allocation, exposure, loss, drawdown, capability, and emergency limits consumed by the shared portfolio engine.",
  },
  oms: {
    eyebrow: "Execution authority",
    icon: ShieldCheck,
    title: "OMS & Protection",
    description: "Configure execution urgency, price protection, order timing, stop construction, and trailing behavior used by the shared OMS.",
  },
  accounts: {
    eyebrow: "Runtime binding",
    icon: Boxes,
    title: "Accounts & Sessions",
    description: "Map stable application account keys to mode-specific broker or simulated sessions and portfolio policies.",
  },
  revisions: {
    eyebrow: "Publication gate",
    icon: BookOpenCheck,
    title: "Approved Revisions",
    description: "Publish one immutable application configuration. Replay pins it for the complete run, including every configured Canvas.",
  },
} as const;

export function TradingConfigurationPage({ section }: { section: TradingConfigurationSection }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [approved, setApproved] = useState<Revision | null>(null);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [editor, setEditor] = useState("");
  const [label, setLabel] = useState("");
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
        if (section !== "revisions") {
          setEditor(JSON.stringify(nextDraft[section], null, 2));
        }
        setStatus("ready");
      })
      .catch((reason) => {
        if (cancelled) return;
        setMessage(reason instanceof Error ? reason.message : String(reason));
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [section]);

  const changed = useMemo(() => {
    if (!draft || section === "revisions") return false;
    try {
      return JSON.stringify(JSON.parse(editor)) !== JSON.stringify(draft[section]);
    } catch {
      return true;
    }
  }, [draft, editor, section]);

  async function saveSection() {
    if (section === "revisions") return;
    setStatus("saving");
    setMessage("");
    try {
      const payload = JSON.parse(editor);
      const nextDraft = await api<Draft>(`/api/trading/configuration/draft/${section}`, {
        body: JSON.stringify({ payload }),
        method: "PUT",
      });
      setDraft(nextDraft);
      setEditor(JSON.stringify(nextDraft[section], null, 2));
      setStatus("saved");
      setMessage("Draft saved. Runtime behavior is unchanged until a revision is published.");
    } catch (reason) {
      setStatus("error");
      setMessage(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function publish() {
    setStatus("saving");
    setMessage("");
    try {
      const canvas = canvasApprovalSnapshot();
      if (!canvas.ready) throw new Error("Configure at least one Canvas container before publishing.");
      const revision = await api<Revision>("/api/trading/configuration/publish", {
        body: JSON.stringify({
          canvas_profile: canvas.profile,
          canvas_revision: canvas.revision,
          label,
        }),
        method: "POST",
      });
      setApproved(revision);
      setRevisions((current) => [revision, ...current.filter((row) => row.revision_id !== revision.revision_id)]);
      window.dispatchEvent(new CustomEvent("quant-trading-configuration-published"));
      setLabel("");
      setStatus("saved");
      setMessage(`Revision ${revision.revision} is approved and is now the only configuration new Replay runs consume.`);
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
      ) : (
        <section className="configuration-editor-card">
          <header>
            <div>
              <strong>Draft {meta.title.toLowerCase()}</strong>
              <span>Validated against the same domain contracts used by the trading runtime.</span>
            </div>
            <button className="button primary compact" disabled={!changed || status === "saving" || status === "loading"} onClick={saveSection} type="button">
              <Save size={14} /> Save draft
            </button>
          </header>
          <div className="configuration-editor-note">
            <BadgeCheck size={16} />
            <span>Editing this draft cannot alter an active Replay. Publish from Approved Revisions when all sections are ready.</span>
          </div>
          <label>
            <span>Structured configuration</span>
            <textarea
              aria-label={`${meta.title} structured configuration`}
              disabled={status === "loading"}
              onChange={(event) => setEditor(event.target.value)}
              spellCheck={false}
              value={editor}
            />
          </label>
        </section>
      )}
    </div>
  );
}

function RevisionBadge({ approved }: { approved: Revision | null }) {
  return (
    <div className="configuration-revision-badge" data-approved={approved ? "true" : "false"}>
      <small>Runtime authority</small>
      <strong>{approved ? `Revision ${approved.revision}` : "Not published"}</strong>
      <span>{approved ? approved.label : "Replay is gated"}</span>
    </div>
  );
}

function RevisionPublisher({
  approved,
  draft,
  label,
  onLabelChange,
  onPublish,
  publishing,
  revisions,
}: {
  approved: Revision | null;
  draft: Draft | null;
  label: string;
  onLabelChange: (value: string) => void;
  onPublish: () => void;
  publishing: boolean;
  revisions: Revision[];
}) {
  const canvas = useMemo(canvasApprovalSnapshot, [approved, draft]);
  return (
    <div className="configuration-revision-layout">
      <section className="configuration-publish-card">
        <header>
          <div><span>Completion gate</span><strong>Publish the application configuration</strong></div>
          <Send size={18} />
        </header>
        <p>Publishing snapshots Strategies, Assignments, Portfolio & Risk, OMS & Protection, Accounts & Sessions, and the complete Canvas registry into one immutable revision.</p>
        <div className="configuration-publish-proof">
          {["strategy", "assignments", "portfolio", "oms", "accounts"].map((item) => (
            <span key={item}><CheckCircle2 size={14} /> {item.replaceAll("_", " ")}</span>
          ))}
          <span data-ready={canvas.ready ? "true" : "false"}><CheckCircle2 size={14} /> Canvas · {canvas.containerCount} containers</span>
        </div>
        <label>
          <span>Approval label</span>
          <input onChange={(event) => onLabelChange(event.target.value)} placeholder="Replay acceptance candidate" value={label} />
        </label>
        <button className="button primary" disabled={!draft || !canvas.ready || !label.trim() || publishing} onClick={onPublish} type="button">
          <Send size={15} /> {publishing ? "Publishing…" : "Publish revision"}
        </button>
      </section>

      <section className="configuration-history-card">
        <header><span>Immutable history</span><strong>{revisions.length} approved revision{revisions.length === 1 ? "" : "s"}</strong></header>
        <div>
          {revisions.map((revision) => (
            <article data-current={revision.revision_id === approved?.revision_id ? "true" : "false"} key={revision.revision_id}>
              <span><strong>r{revision.revision} · {revision.label}</strong><small>{new Date(revision.approved_at).toLocaleString()}</small></span>
              <code>{revision.content_hash.slice(0, 12)}</code>
            </article>
          ))}
          {!revisions.length ? <div className="configuration-empty-history">No runtime revision has been approved. Replay remains correctly blocked.</div> : null}
        </div>
      </section>
    </div>
  );
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
  return {
    containerCount,
    profile,
    ready: containerCount > 0,
    revision: `canvas-${(hash >>> 0).toString(16).padStart(8, "0")}`,
  };
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}
