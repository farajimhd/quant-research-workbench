import { CheckCircle2, CircleStop, RefreshCcw, TriangleAlert, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { InventoryFilterSelect, type InventoryFilterOption } from "./InventoryFilterSelect";
import "./TradingModeLaunch.css";

export type TradingLaunchCheck = {
  action?: { hash?: string; label?: string };
  evidence?: unknown;
  id: string;
  label: string;
  required?: boolean;
  status: string;
  summary?: string;
};

export function TradingLaunchEvidence({ evidence }: { evidence: unknown }) {
  if (evidence == null || evidence === "") return null;
  if (typeof evidence !== "object") return <small>{String(evidence)}</small>;
  return <details className="mode-launch-evidence"><summary>Validation details</summary><dl>
    {Object.entries(evidence).map(([key, value]) => <div key={key}>
      <dt>{key.replaceAll("_", " ")}</dt>
      <dd>{value == null ? "Unavailable" : typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
    </div>)}
  </dl></details>;
}

export function TradingModeSelectField({
  ariaLabel,
  disabled = false,
  help,
  label,
  onChange,
  options,
  presentation = "compact",
  searchable,
  value,
}: {
  ariaLabel?: string;
  disabled?: boolean;
  help: ReactNode;
  label: string;
  onChange: (value: string) => void;
  options: InventoryFilterOption[];
  presentation?: "catalog" | "compact";
  searchable?: boolean;
  value: string;
}) {
  return <div className="configuration-field configuration-lookup-field" data-editable={disabled ? "false" : "true"}>
    <span>{label}</span>
    <InventoryFilterSelect
      ariaLabel={ariaLabel ?? label}
      className="configuration-lookup-button mode-launch-select"
      disabled={disabled}
      onChange={onChange}
      optionLimit={0}
      options={options}
      placeholder={`Select ${label.toLowerCase()}`}
      presentation={presentation}
      searchable={searchable ?? options.length > 7}
      searchPlaceholder={`Find ${label.toLowerCase()}…`}
      showAllOnOpen
      value={value}
    />
    <small>{help}</small>
  </div>;
}

export function TradingModeLaunch({
  actionLabel,
  actionSummary,
  busy,
  busyLabel = "Preparing Canvas…",
  checking,
  checkingLabel = "Checking setup…",
  checks,
  children,
  description,
  error,
  eyebrow,
  icon: Icon,
  onAction,
  onRefresh,
  ready,
  secondary,
  setupEyebrow = "Run setup",
  setupTitle = "Required parameters",
  title,
}: {
  actionLabel: string;
  actionSummary: ReactNode;
  busy?: boolean;
  busyLabel?: string;
  checking?: boolean;
  checkingLabel?: string;
  checks: TradingLaunchCheck[];
  children: ReactNode;
  description: string;
  error?: string;
  eyebrow: string;
  icon: LucideIcon;
  onAction: () => void;
  onRefresh?: () => void;
  ready: boolean;
  secondary?: ReactNode;
  setupEyebrow?: string;
  setupTitle?: string;
  title: string;
}) {
  const requiredChecks = checks.filter((check) => check.required !== false);
  const readyCount = requiredChecks.filter((check) => check.status === "ready").length;
  return <main className="mode-launch-page trading-configuration-page" data-configuration-section="runtime">
    <header className="configuration-page-header mode-launch-heading">
      <div className="configuration-page-icon mode-launch-heading-icon"><Icon aria-hidden="true" size={20} /></div>
      <div className="configuration-page-heading"><span>{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>
    </header>

    {error ? <div className="mode-launch-error" role="alert"><TriangleAlert aria-hidden="true" size={17} /><div><strong>Unable to prepare this mode</strong><span>{error}</span></div></div> : null}

    <section className="mode-launch-surface">
      <div className="configuration-expert-workspace mode-launch-definition">
        <div className="mode-launch-section-heading">
          <div><span>{setupEyebrow}</span><strong>{setupTitle}</strong></div>
          {onRefresh ? <button aria-label="Check readiness again" className="button secondary compact" disabled={checking} onClick={onRefresh} type="button"><RefreshCcw aria-hidden="true" size={14} /> Check again</button> : null}
        </div>
        <div className="configuration-field-grid mode-launch-fields">{children}</div>
      </div>

      <aside className="mode-launch-readiness" data-ready={ready ? "true" : "false"}>
        <header>
          <div className="mode-launch-state-icon">{ready ? <CheckCircle2 aria-hidden="true" size={19} /> : <CircleStop aria-hidden="true" size={19} />}</div>
          <div><span>Readiness</span><strong>{checking ? "Checking setup" : ready ? "Ready to open" : "Action required"}</strong></div>
          <small>{readyCount}/{requiredChecks.length || checks.length} required</small>
        </header>
        <div aria-live="polite" className="mode-launch-validation-status" role="status">
          <span aria-hidden="true" className={`loading-spinner${checking ? "" : " is-idle"}`} />
          <span>{checking ? checkingLabel : ready ? "Setup checks passed." : "Review the checks below."}</span>
        </div>
        <div className="mode-launch-checks">
          {checks.map((check) => <article data-status={check.status} key={check.id}>
            <span>{check.status === "ready" ? <CheckCircle2 aria-hidden="true" size={15} /> : <TriangleAlert aria-hidden="true" size={15} />}</span>
            <div><strong>{check.label}</strong>{check.status !== "ready" && check.summary ? <p>{check.summary}</p> : null}{check.status !== "ready" && check.evidence !== check.summary ? <TradingLaunchEvidence evidence={check.evidence} /> : null}</div>
            {check.action?.hash ? <button className="button secondary compact" onClick={() => { window.location.hash = check.action?.hash || "#revision-configuration"; }} type="button">{check.action.label || "Resolve"}</button> : null}
          </article>)}
        </div>
        <div className="mode-launch-command">
          <p>{actionSummary}</p>
          <button className="button primary" disabled={!ready || checking || busy} onClick={onAction} type="button">{busy ? busyLabel : actionLabel}</button>
        </div>
      </aside>
    </section>
    {secondary ? <div className="mode-launch-secondary">{secondary}</div> : null}
  </main>;
}
