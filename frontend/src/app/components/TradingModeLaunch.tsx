import { CheckCircle2, CircleStop, RefreshCcw, TriangleAlert, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export type TradingLaunchCheck = {
  action?: { hash?: string; label?: string };
  evidence?: string;
  id: string;
  label: string;
  required?: boolean;
  status: string;
  summary?: string;
};

export function TradingModeLaunch({
  actionLabel,
  actionSummary,
  busy,
  checking,
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
  checking?: boolean;
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
  return <main className="mode-launch-page">
    <header className="mode-launch-heading">
      <div className="mode-launch-heading-icon"><Icon aria-hidden="true" size={20} /></div>
      <div><span>{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>
    </header>

    {error ? <div className="mode-launch-error" role="alert"><TriangleAlert aria-hidden="true" size={17} /><div><strong>Unable to prepare this mode</strong><span>{error}</span></div></div> : null}

    <section className="mode-launch-surface">
      <div className="mode-launch-definition">
        <div className="mode-launch-section-heading">
          <div><span>{setupEyebrow}</span><strong>{setupTitle}</strong></div>
          {onRefresh ? <button aria-label="Check readiness again" className="button secondary compact" disabled={checking} onClick={onRefresh} type="button"><RefreshCcw aria-hidden="true" size={14} /> Check again</button> : null}
        </div>
        <div className="mode-launch-fields">{children}</div>
      </div>

      <aside className="mode-launch-readiness" data-ready={ready ? "true" : "false"}>
        <header>
          <div className="mode-launch-state-icon">{ready ? <CheckCircle2 aria-hidden="true" size={19} /> : <CircleStop aria-hidden="true" size={19} />}</div>
          <div><span>Readiness</span><strong>{checking ? "Checking services" : ready ? "Ready to open" : "Action required"}</strong></div>
          <small>{readyCount}/{requiredChecks.length || checks.length} required</small>
        </header>
        <div className="mode-launch-checks">
          {checks.map((check) => <article data-status={check.status} key={check.id}>
            <span>{check.status === "ready" ? <CheckCircle2 aria-hidden="true" size={15} /> : <TriangleAlert aria-hidden="true" size={15} />}</span>
            <div><strong>{check.label}</strong>{check.status !== "ready" && check.summary ? <p>{check.summary}</p> : null}{check.status !== "ready" && check.evidence && check.evidence !== check.summary ? <small>{check.evidence}</small> : null}</div>
            {check.action?.hash ? <button className="button secondary compact" onClick={() => { window.location.hash = check.action?.hash || "#revision-configuration"; }} type="button">{check.action.label || "Resolve"}</button> : null}
          </article>)}
          {!checks.length && checking ? <div className="mode-launch-checking"><span className="loading-spinner" aria-hidden="true" /> Resolving required services and configuration…</div> : null}
        </div>
        <div className="mode-launch-command">
          <p>{actionSummary}</p>
          <button className="button primary" disabled={!ready || checking || busy} onClick={onAction} type="button">{busy ? "Preparing Canvas…" : actionLabel}</button>
        </div>
      </aside>
    </section>
    {secondary ? <div className="mode-launch-secondary">{secondary}</div> : null}
  </main>;
}
