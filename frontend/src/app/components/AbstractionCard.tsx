import {
  Activity,
  BarChart3,
  Boxes,
  BriefcaseBusiness,
  Cable,
  Columns3,
  Database,
  GitBranch,
  LayoutDashboard,
  ListChecks,
  Network,
  RadioTower,
  ScanSearch,
  Send,
  ShieldCheck,
  Sigma,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

export type AbstractionKind =
  | "field"
  | "processing_step"
  | "derivation"
  | "signal"
  | "column"
  | "rule_set"
  | "watchlist"
  | "strategy"
  | "strategy_profile"
  | "account_binding"
  | "portfolio_policy"
  | "portfolio_mandate"
  | "portfolio_group"
  | "oms_profile"
  | "execution_policy"
  | "protection_profile"
  | "run_plan"
  | "canvas_profile";

type AbstractionPresentation = { icon: LucideIcon; label: string };

const PRESENTATIONS: Record<AbstractionKind, AbstractionPresentation> = {
  field: { icon: Database, label: "Field" },
  processing_step: { icon: Cable, label: "Processing step" },
  derivation: { icon: Sigma, label: "Derivation" },
  signal: { icon: Activity, label: "Signal" },
  column: { icon: Columns3, label: "Column" },
  rule_set: { icon: ListChecks, label: "Rule set" },
  watchlist: { icon: ScanSearch, label: "Watchlist" },
  strategy: { icon: GitBranch, label: "Strategy" },
  strategy_profile: { icon: SlidersHorizontal, label: "Strategy profile" },
  account_binding: { icon: Boxes, label: "Account binding" },
  portfolio_policy: { icon: BriefcaseBusiness, label: "Portfolio policy" },
  portfolio_mandate: { icon: Network, label: "Portfolio mandate" },
  portfolio_group: { icon: BarChart3, label: "Portfolio group" },
  oms_profile: { icon: Send, label: "OMS profile" },
  execution_policy: { icon: RadioTower, label: "Execution policy" },
  protection_profile: { icon: ShieldCheck, label: "Protection profile" },
  run_plan: { icon: Network, label: "Run Plan" },
  canvas_profile: { icon: LayoutDashboard, label: "Canvas profile" },
};

export type AbstractionCardMeta = { label: string; value: ReactNode };

type AbstractionCardProps = {
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  compact?: boolean;
  control?: ReactNode;
  description?: ReactNode;
  identity?: ReactNode;
  kind: AbstractionKind;
  metadata?: AbstractionCardMeta[];
  ordinal?: number;
  selected?: boolean;
  status?: ReactNode;
  title: ReactNode;
  unavailable?: boolean;
};

export function AbstractionCard({
  actions,
  children,
  className = "",
  compact = false,
  control,
  description,
  identity,
  kind,
  metadata = [],
  ordinal,
  selected = false,
  status,
  title,
  unavailable = false,
}: AbstractionCardProps) {
  const presentation = PRESENTATIONS[kind];
  const Icon = presentation.icon;
  const body = <>
    <span aria-hidden="true" className="abstraction-card-icon"><Icon size={compact ? 14 : 16} /></span>
    <div className="abstraction-card-main">
      <header className="abstraction-card-header">
        <div className="abstraction-card-heading">
          <span className="abstraction-card-kind">{presentation.label}</span>
          {status ? <span className="abstraction-card-status">{status}</span> : null}
        </div>
        <strong className="abstraction-card-title">{title}</strong>
        {identity ? <code className="abstraction-card-identity">{identity}</code> : null}
      </header>
      {description ? <div className="abstraction-card-description">{description}</div> : null}
      {metadata.length ? <dl className="abstraction-card-metadata">{metadata.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl> : null}
      {children ? <div className="abstraction-card-content">{children}</div> : null}
    </div>
    {actions || control ? <div className="abstraction-card-actions">{actions}{control}</div> : null}
  </>;
  const classes = ["abstraction-card", compact ? "compact" : "", className].filter(Boolean).join(" ");
  const shared = {
    "data-kind": kind,
    "data-selected": selected ? "true" : "false",
    "data-unavailable": unavailable ? "true" : "false",
  } as const;
  return control
    ? <label className={classes} {...shared}>{ordinal ? <span className="abstraction-card-ordinal">{ordinal}</span> : null}{body}</label>
    : <article className={classes} {...shared}>{ordinal ? <span className="abstraction-card-ordinal">{ordinal}</span> : null}{body}</article>;
}
