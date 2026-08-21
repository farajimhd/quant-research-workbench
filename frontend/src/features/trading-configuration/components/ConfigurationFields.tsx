import { BadgeCheck, ChevronRight, CircleHelp, Clipboard, GitBranch, Network, Send, ShieldCheck, Sparkles, Target, WalletCards, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactElement, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { InventoryFilterSelect } from "../../../app/components/InventoryFilterSelect";
import { formatSemanticNumber } from "../../../app/format";
import type { CapabilityParameter, Primitive, RuntimeMode } from "../contracts";

export function readableLabel(value: string) {
  return value.replaceAll(".", " · ").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function round(value: number) {
  return Math.round(value * 10_000) / 10_000;
}
export function ConfigGroup({ action, children, summary, title }: { action?: ReactNode; children: ReactNode; summary: string; title: string }) {
  const visual = configGroupVisual(title);
  const Icon = visual.icon;
  return <section className="configuration-group" data-group-tone={visual.tone}><header><div className="configuration-group-heading"><span className="configuration-group-icon"><Icon size={15} /></span><div><strong>{title}</strong><p>{summary}</p></div></div>{action}</header><div className="configuration-group-body"><ConfigurationNarrative heading={title} paragraphs={configurationGroupStory(title)} />{children}</div></section>;
}

export function ConfigurationNarrative({ heading, paragraphs }: { heading: string; paragraphs: string[] }) {
  if (!paragraphs.length) return null;
  return <section className="configuration-narrative"><span>{heading}</span>{paragraphs.map((paragraph, index) => <p key={`${heading}-${index}`}>{paragraph}</p>)}</section>;
}

export function configurationGroupStory(title: string) {
  const normalized = title.toLowerCase();
  if (normalized.includes("watch universe")) return [
    "Universe source and symbols determine which tickers the Run Plan may observe. Portfolio book sets the scope for campaign ownership. Eligibility does not authorize entry; evidence and all downstream authorities must still pass.",
  ];
  if (normalized.includes("strategy and execution")) return [
    "Strategy Profile selects decision behavior. OMS profile selects execution and protection behavior. A run pins both independent revisions through the Run Plan.",
  ];
  if (normalized.includes("action authority")) return [
    "Default sets operator involvement for inherited actions. Manual records intent, Confirm waits for approval, and Automatic proceeds only after downstream checks pass. Per-action values override the default. Protective and emergency exits remain automatic.",
  ];
  if (normalized.includes("environments") && normalized.includes("safety")) return [
    "Allowed environments control where this Run Plan may launch. Safety may be configured for historical modes and is mandatory for Paper and Live.",
  ];
  if (normalized.includes("account mandate")) return [
    "A mandate links one Run Plan to one account. Its parameters limit cash, planned risk, position count, allocation mode, replacement, and maximum action authority. Portfolio still evaluates current state and competing requests for every intent.",
  ];
  if (normalized.includes("account safety")) return [
    "Exposure parameters bound normal allocation. Warning thresholds pause new risk. Hard loss and drawdown thresholds latch the account. These controls apply across all runs using the account and cannot be weakened by a Strategy Profile.",
  ];
  if (normalized.includes("risk group")) return [
    "A risk group applies shared gross and ticker exposure limits across its selected account keys. Use it when multiple accounts share economic or correlated risk.",
  ];
  if (normalized.includes("execution policy catalog")) return [
    "Quote source selects execution-time price authority. Price bounds limit acceptable execution. Deadline and repricing parameters control how long and how aggressively OMS works the approved quantity. Partial-fill policy controls the remainder.",
  ];
  if (normalized.includes("protection profile catalog")) return [
    "Slice fractions allocate the complete fill. Each slice requires a hard stop and may define a target and trail. Add policy controls protection after position increases; repair deadline and catastrophic-backstop settings control OMS recovery when broker protection is missing.",
  ];
  if (normalized === "protection") return [
    "Stop method selects structural, volatility, or combined invalidation. Buffers and multiples set distance. Maximum risk caps the resolved protection. Trailing enabled permits protection to tighten after favorable movement.",
  ];
  if (normalized.includes("execution behavior")) return [
    "Entry and exit defaults apply when the Strategy Intent has no phase-specific override. Urgency and limit offset affect execution speed and price tolerance. Smart routing still resolves broker instructions from session and account capabilities.",
  ];
  if (normalized.includes("configured account")) return [
    "Stable account key is the published identity used by mandates and runtime state. Source account and session locate execution state. Account class and modes constrain capabilities. Paper and Live broker identifiers are resolved only during backend preflight.",
  ];
  if (normalized.includes("readiness")) return [
    "Readiness confirms required configuration references exist. It does not prove future broker connectivity, market-data health, or Live order acceptance.",
  ];
  if (normalized.includes("effective configuration")) return [
    "Runtime mode selects the backend projection to inspect. The result shows resolved accounts, policies, and eligible Run Plans from this browser session; it is read-only derived evidence.",
  ];
  return [];
}

export function configGroupVisual(title: string) {
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

export function GuideCallout({ children, icon, title }: { children: ReactNode; icon: ReactNode; title: string }) {
  void children;
  void icon;
  void title;
  return null;
}

export type HelpContent = string | {
  note?: string;
  parameters?: Record<string, string>;
  role: string;
  values?: Record<string, string>;
};

export function FieldHelp({ content, title = "Parameter guide" }: { content: HelpContent; title?: string }) {
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

export type FieldDefinition = {
  choices?: readonly string[];
  help: HelpContent;
  kind: "boolean" | "choice" | "number" | "text";
  label: string;
  path: string;
  step?: number;
  unit?: string;
};

export function ParameterField({ definition, onChange, value }: { definition: FieldDefinition; onChange: (value: Primitive) => void; value: Primitive }) {
  if (definition.kind === "boolean") return <BooleanField help={definition.help} label={definition.label} onChange={onChange} value={Boolean(value)} />;
  if (definition.kind === "choice") return <SelectField help={definition.help} label={definition.label} onChange={onChange} options={(definition.choices ?? []).map((item) => ({ label: readableLabel(item), value: item }))} value={String(value)} />;
  if (definition.kind === "number") return <NumberField help={definition.help} label={definition.label} onChange={onChange} step={definition.step ?? 0.01} unit={definition.unit} value={Number(value)} />;
  return null;
}

export function CapabilityField({ definition, onChange, value }: { definition: CapabilityParameter; onChange: (value: Primitive) => void; value: Primitive }) {
  if (definition.type === "boolean") return <BooleanField help={definition.help} label={definition.label} onChange={onChange} value={Boolean(value)} />;
  if (definition.type === "choice") return <SelectField help={definition.help} label={definition.label} onChange={onChange} options={(definition.options ?? []).map((item) => ({ label: readableLabel(item), value: item }))} value={String(value)} />;
  return <NumberField help={definition.help} label={definition.label} maximum={definition.maximum} minimum={definition.minimum} onChange={onChange} step={definition.step ?? 0.01} unit={definition.display === "fraction" ? "fraction" : definition.unit} value={Number(value)} />;
}

export function TextField({ help, label, nextAction = false, onChange, value }: { help: HelpContent; label: string; nextAction?: boolean; onChange: (value: string) => void; value: string }) {
  return <label className="configuration-field" data-editable="true"><span>{label}</span><input data-next-action-control={nextAction ? "true" : undefined} onChange={(event) => onChange(event.target.value)} value={value} /><small>{fieldSummary(help)}</small></label>;
}

export function NumberField({ help, label, maximum, minimum, onChange, step, unit, value }: { help: HelpContent; label: string; maximum?: number; minimum?: number; onChange: (value: number) => void; step: number; unit?: string; value: number }) {
  const fraction = unit === "fraction";
  return <label className="configuration-field" data-editable="true"><span>{label}</span><div className="configuration-number"><input max={fraction ? 100 : maximum} min={fraction ? 0 : minimum} onChange={(event) => onChange(fraction ? Number(event.target.value) / 100 : Number(event.target.value))} step={fraction ? step * 100 : step} type="number" value={fraction ? round(value * 100) : value} />{unit ? <em>{fraction ? "%" : unit}</em> : null}</div><small>{fieldSummary(help)}</small></label>;
}

export function OptionalNumberField({ help, label, minimum, onChange, step, unit, value }: { help: HelpContent; label: string; minimum?: number; onChange: (value: number | null) => void; step: number; unit?: string; value: number | null }) {
  return <label className="configuration-field" data-editable="true"><span>{label}</span><div className="configuration-number"><input min={minimum} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} placeholder="Automatic" step={step} type="number" value={value ?? ""} />{unit ? <em>{unit}</em> : null}</div><small>{fieldSummary(help)}</small></label>;
}

export function SelectField({ disabled = false, help, label, onChange, options, searchable, value }: { disabled?: boolean; help: HelpContent; label: string; onChange: (value: string) => void; options: Array<{ description?: string; label: string; value: string }>; searchable?: boolean; value: string }) {
  const documentedOptions = options.map((option) => ({ ...option, description: option.description ?? choiceExplanation(option.label, option.value, help) }));
  return <div className="configuration-field configuration-lookup-field" data-editable={disabled ? "false" : "true"}><span>{label}</span>{disabled ? <strong>{options.find((option) => option.value === value)?.label ?? value}</strong> : <InventoryFilterSelect ariaLabel={label} className="configuration-lookup-button" onChange={onChange} options={documentedOptions} searchable={searchable ?? options.length > 7} searchPlaceholder={`Find ${label.toLowerCase()}…`} value={value} />}<small>{fieldSummary(help)}</small></div>;
}

export function BooleanField({ disabled = false, help, label, onChange, value }: { disabled?: boolean; help: HelpContent; label: string; onChange: (value: boolean) => void; value: boolean }) {
  return <label className="configuration-field configuration-boolean" data-editable={disabled ? "false" : "true"}><span>{label}</span><small>{fieldSummary(help)}</small><input checked={value} disabled={disabled} onChange={(event) => onChange(event.target.checked)} type="checkbox" /></label>;
}

export function fieldSummary(help: HelpContent) {
  return typeof help === "string" ? help : help.role;
}

export const STRATEGY_CHOICE_EXPLANATIONS: Record<string, string> = {
  accept_partial: "Keep the confirmed filled quantity and stop requesting the remainder.",
  acceleration_slowdown: "Act when favorable price acceleration weakens, preserving gains before momentum fully reverses.",
  all_available: "Ask Portfolio for every unit of capacity still available under the account mandate and current risk state.",
  automatic: "Allow the configured authority to act without waiting for a manual confirmation step.",
  close: "Request release of the entire broker-reconciled position.",
  complete_remainder: "Continue working only the approved quantity that remains unfilled after reconciliation.",
  confirm: "Require an explicit confirmation before the action may proceed.",
  fixed_quantity: "Request a fixed number of shares; Portfolio may approve fewer or reject the request.",
  favorable_move_pct: "Act after the position reaches the configured favorable percentage move.",
  hybrid: "Use the stricter valid boundary produced by structural and volatility evidence.",
  long: "Open by buying and reduce or close by selling.",
  mandate_fraction: "Request a fraction of the cash capacity assigned to this Run Plan's account mandate.",
  manual: "Prepare the action for a human operator without submitting it automatically.",
  patient: "Favor passive pricing and slower repricing when the selected execution policy permits it.",
  reduce: "Request only the configured fraction of the broker-reconciled position.",
  regular: "Use the normal balance between fill probability and price discipline.",
  risk_fraction: "Request exposure as a fraction of the risk budget; Portfolio converts it to account-specific quantity.",
  short: "Open by short-selling and reduce or close by buying to cover; broker shortability remains mandatory.",
  structure: "Anchor invalidation to the confirmed market structure that justified the position.",
  urgent: "Prioritize a prompt fill while remaining inside the selected OMS policy and approved quantity.",
  very_urgent: "Use the policy's fastest allowed repricing and terminal behavior for time-critical execution.",
  volatility: "Place the boundary at the configured volatility multiple so distance adapts to current movement.",
  volatility_multiple: "Act when the configured move or distance reaches the selected volatility multiple.",
  cancel_remainder: "Cancel the broker-confirmed unfilled remainder and keep only completed fills.",
};

export function choiceExplanation(label: string, value: string, help: HelpContent) {
  if (typeof help !== "string") {
    const documented = help.values?.[label] ?? help.values?.[readableLabel(value)] ?? help.values?.[value];
    if (documented) return documented;
  }
  return STRATEGY_CHOICE_EXPLANATIONS[value] ?? `Select ${label} for this setting. ${fieldSummary(help)}`;
}

export function ModeSelector({ modes, onChange }: { modes: RuntimeMode[]; onChange: (value: RuntimeMode[]) => void }) {
  const options: Array<{ label: string; value: RuntimeMode }> = [
    { label: "Replay", value: "replay" }, { label: "Backtest", value: "backtest" },
    { label: "Backtest Debug", value: "backtest_debug" }, { label: "Paper", value: "paper" }, { label: "Live", value: "live" },
  ];
  return <div className="configuration-mode-selector">{options.map((option) => <label key={option.value}><input checked={modes.includes(option.value)} onChange={(event) => onChange(event.target.checked ? [...modes, option.value] : modes.filter((item) => item !== option.value))} type="checkbox" /><span>{option.label}</span></label>)}</div>;
}

export function JsonInspector({ label, value }: { label: string; value: unknown }) {
  const content = JSON.stringify(value, null, 2);
  const [copied, setCopied] = useState(false);
  async function copy() {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_500);
  }
  return <details className="configuration-json-inspector"><summary><span><strong>Advanced · Generated JSON</strong><small>Inspect the canonical payload without using JSON as the primary editor</small></span><ChevronRight size={15} /></summary><header><span>{label}</span><button onClick={() => void copy()} type="button"><Clipboard size={13} /> {copied ? "Copied" : "Copy"}</button></header><pre>{content}</pre></details>;
}

export function EmptyState({ detail, title }: { detail: string; title: string }) {
  return <div className="configuration-empty"><strong>{title}</strong><span>{detail}</span></div>;
}
