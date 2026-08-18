import {
  Activity,
  BarChart3,
  Boxes,
  BriefcaseBusiness,
  Braces,
  Cable,
  Columns3,
  Database,
  GitBranch,
  LayoutDashboard,
  ListChecks,
  ListFilter,
  Network,
  Package,
  RadioTower,
  Route,
  ScanSearch,
  Server,
  Send,
  ShieldCheck,
  Sigma,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { useRegistryPresentation } from "./DefinitionRegistry";

export type AbstractionKind =
  | "field"
  | "source"
  | "processing_step"
  | "derivation"
  | "signal"
  | "signal_stream"
  | "event_schema"
  | "product"
  | "query_plan"
  | "column"
  | "condition"
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

const ICON_RENDERERS: Record<string, LucideIcon> = {
  activity: Activity,
  bar_chart: BarChart3,
  boxes: Boxes,
  braces: Braces,
  briefcase: BriefcaseBusiness,
  cable: Cable,
  columns: Columns3,
  database: Database,
  git_branch: GitBranch,
  layout_dashboard: LayoutDashboard,
  list_checks: ListChecks,
  list_filter: ListFilter,
  network: Network,
  package: Package,
  radio_tower: RadioTower,
  route: Route,
  scan_search: ScanSearch,
  send: Send,
  server: Server,
  shield_check: ShieldCheck,
  sigma: Sigma,
  sliders: SlidersHorizontal,
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
  registryId?: string;
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
  registryId,
  selected = false,
  status,
  title,
  unavailable = false,
}: AbstractionCardProps) {
  const presentation = useRegistryPresentation(kind, registryId);
  const Icon = ICON_RENDERERS[presentation.icon];
  if (!Icon) throw new Error(`No icon renderer is registered for ${presentation.icon}`);
  const body = <>
    <span aria-hidden="true" className="abstraction-card-icon"><Icon size={compact ? 14 : 16} /></span>
    <div className="abstraction-card-main">
      <header className="abstraction-card-header">
        <div className="abstraction-card-heading">
          <span className="abstraction-card-kind">{presentation.kindLabel}</span>
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
    "data-kind": presentation.kind,
    "data-accent": presentation.accent,
    "data-configurable": presentation.configurable ? "true" : "false",
    "data-configuration-mode": presentation.configurationMode,
    "data-selected": selected ? "true" : "false",
    "data-selectable": control ? "true" : "false",
    "data-unavailable": unavailable ? "true" : "false",
  } as const;
  return control
    ? <label className={classes} {...shared}>{ordinal ? <span className="abstraction-card-ordinal">{ordinal}</span> : null}{body}</label>
    : <article className={classes} {...shared}>{ordinal ? <span className="abstraction-card-ordinal">{ordinal}</span> : null}{body}</article>;
}
