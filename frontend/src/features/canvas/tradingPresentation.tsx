import {
  Activity,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  BadgeDollarSign,
  BarChart3,
  BookOpen,
  BriefcaseBusiness,
  ChevronDown,
  ChevronRight,
  CircleDollarSign,
  ExternalLink,
  Filter,
  Gauge,
  HelpCircle,
  Landmark,
  Search,
  RefreshCcw,
  Save,
  ShieldCheck,
  Target,
  WalletCards,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "../../api/client";
import type { CanvasLinkContext, CanvasLinkGroupId } from "../../app/canvasWorkspace";
import { MarketTime } from "../../app/components/MarketTime";
import { PresentedValue, SecurityIdentityCell, tableCellClass } from "../../app/components/TablePresentation";
import { useTickerPresentations } from "../../app/components/TickerIdentity";
import type { WorkspaceContainerId } from "../../app/tradingWorkspace";
import type {
  CanonicalTradingPreview,
  CanvasPreview,
  ContainerSettings,
  PerformanceJournalReport,
  PnlCandle,
  PnlCandleTimeframe,
  PreviewRow,
} from "./contracts";
import {
  basisPoints,
  cellTone,
  compactDuration,
  formatCell,
  formatJournalDate,
  formatMoneyAxis,
  formatPnlCandleTime,
  formatQuantity,
  labelFor,
  metricThresholdTone,
  money,
  nestedValue,
  previewRowKey,
  ratioNumber,
  ratioPct,
  slippageTone,
} from "./presentationFormat";

export type TradingContainerPreviewProps = {
  id: WorkspaceContainerId;
  linkGroup: CanvasLinkGroupId;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  onTickerSelect?: (ticker: string) => void;
  preview: CanvasPreview | null;
  settings: ContainerSettings;
};

export function TradingContainerPreview({
  id,
  linkGroup,
  onLinkContextChange,
  onTickerSelect,
  preview,
  settings,
}: TradingContainerPreviewProps) {
  if (!preview) return <EmptyState label="No preview data" />;
  if (id === "portfolio") return <PortfolioPreview data={preview.trading} settings={settings.portfolio} />;
  const selectSymbol = onTickerSelect ?? (linkGroup === "none" ? undefined : (symbol: string) => onLinkContextChange({ symbol }));
  if (id === "positions") return <PositionsPreview data={preview.trading} onSymbolSelect={selectSymbol} settings={settings.positions} />;
  if (id === "orders") return <OrdersPreview data={preview.trading} onSymbolSelect={selectSymbol} settings={settings.orders} />;
  if (id === "fills") return <ExecutionsPreview data={preview.trading} onSymbolSelect={selectSymbol} settings={settings.fills} />;
  if (id === "closed_trades") return <ClosedTradesPreview data={preview.trading} onSymbolSelect={selectSymbol} settings={settings.closed_trades} />;
  if (id === "activity") return <ActivityPreview data={preview.trading} settings={settings.activity} />;
  if (id === "performance_journal") return <TradingJournalPreview data={preview.trading} settings={settings.performance_journal} />;
  if (id === "strategy") return <StrategyPreview data={preview.strategy} showSignals={settings.strategy.showSignals} />;
  return <EmptyState label="This diagnostic surface has no preview renderer." />;
}

function EmptyState({ label }: { label: string }) {
  return <div className="canvas-preview-empty">{label}</div>;
}

function PreviewTable({ columns, onSymbolSelect, rows }: { columns: string[]; onSymbolSelect?: (symbol: string) => void; rows: PreviewRow[] }) {
  const tickerColumns = columns.filter(isPreviewTickerColumn);
  const presentations = useTickerPresentations(rows.flatMap((row) => tickerColumns.map((column) => String(row[column] || ""))));
  if (!rows.length) return <EmptyState label="No point-in-time rows" />;
  const visibleColumns = columns.filter((column) => column !== "logo" && column !== "company_name");
  return <div className="canvas-preview-table-wrap"><table className="canvas-preview-table"><thead><tr>{visibleColumns.map((column) => <th key={column}>{labelFor(column)}</th>)}</tr></thead><tbody>{rows.map((row, index) => {
    return <tr key={previewRowKey(row, visibleColumns, index)}>{visibleColumns.map((column) => <td className={`${tableCellClass(column)} preview-cell-${column.replace(/[^a-z0-9_-]/gi, "-")}`} data-tone={cellTone(row[column], column)} key={column}><PreviewCell column={column} onSymbolSelect={onSymbolSelect} presentations={presentations} row={row} /></td>)}</tr>;
  })}</tbody></table></div>;
}

type TradingDataTableProps = {
  columns: string[];
  defaultSort?: string;
  filterColumn?: string;
  filterLabel?: string;
  onSymbolSelect?: (symbol: string) => void;
  renderExpanded?: (row: PreviewRow) => ReactNode;
  rows: PreviewRow[];
  searchPlaceholder: string;
};

function TradingDataTable({ columns, defaultSort, filterColumn, filterLabel = "All", onSymbolSelect, renderExpanded, rows, searchPlaceholder }: TradingDataTableProps) {
  const visibleColumns = useMemo(() => columns.filter((column) => column !== "logo" && column !== "company_name"), [columns]);
  const [queryText, setQueryText] = useState("");
  const [filterValue, setFilterValue] = useState("all");
  const [sortColumn, setSortColumn] = useState(defaultSort || columns[0] || "");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [expandedKey, setExpandedKey] = useState("");
  const tickerColumns = columns.filter(isPreviewTickerColumn);
  const presentations = useTickerPresentations(rows.flatMap((row) => tickerColumns.map((column) => String(row[column] || ""))));
  const filterOptions = useMemo(() => filterColumn ? Array.from(new Set(rows.map((row) => String(row[filterColumn] ?? "").trim()).filter(Boolean))).sort((left, right) => left.localeCompare(right)) : [], [filterColumn, rows]);
  const visibleRows = useMemo(() => {
    const queryValue = queryText.trim().toLowerCase();
    const filtered = rows.filter((row) => {
      if (filterColumn && filterValue !== "all" && String(row[filterColumn] ?? "") !== filterValue) return false;
      if (!queryValue) return true;
      return visibleColumns.some((column) => searchableValue(row[column]).includes(queryValue));
    });
    return [...filtered].sort((left, right) => compareTradingValues(left[sortColumn], right[sortColumn]) * (sortDirection === "asc" ? 1 : -1));
  }, [filterColumn, filterValue, queryText, rows, sortColumn, sortDirection, visibleColumns]);
  function changeSort(column: string) {
    if (sortColumn === column) setSortDirection((current) => current === "asc" ? "desc" : "asc");
    else { setSortColumn(column); setSortDirection("desc"); }
  }
  return <div className="trading-table-shell">
    <div className="trading-table-toolbar">
      <label className="trading-table-search"><Search aria-hidden="true" size={14} /><input aria-label={searchPlaceholder} onChange={(event) => setQueryText(event.target.value)} placeholder={searchPlaceholder} value={queryText} /></label>
      {filterColumn ? <label className="trading-table-filter"><Filter aria-hidden="true" size={13} /><select aria-label={`Filter by ${filterLabel}`} onChange={(event) => setFilterValue(event.target.value)} value={filterValue}><option value="all">{filterLabel}</option>{filterOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select></label> : null}
      <span className="trading-table-count">{visibleRows.length} of {rows.length}</span>
    </div>
    {!visibleRows.length ? <EmptyState label={rows.length ? "No rows match the active search and filter" : "No point-in-time rows"} /> : <div className="canvas-preview-table-wrap"><table className="canvas-preview-table trading-data-table"><thead><tr>{renderExpanded ? <th aria-label="Expand row" className="trading-expand-column" /> : null}{visibleColumns.map((column) => <th aria-sort={sortColumn === column ? (sortDirection === "asc" ? "ascending" : "descending") : "none"} key={column}><button onClick={() => changeSort(column)} type="button"><span>{labelFor(column)}</span>{sortColumn === column ? sortDirection === "asc" ? <ArrowUp size={11} /> : <ArrowDown size={11} /> : <ArrowUpDown size={11} />}</button></th>)}</tr></thead><tbody>{visibleRows.map((row, index) => {
      const key = previewRowKey(row, visibleColumns, index);
      const expanded = expandedKey === key;
      return <FragmentRow columns={visibleColumns} expanded={expanded} key={key} onExpand={renderExpanded ? () => setExpandedKey(expanded ? "" : key) : undefined} onSymbolSelect={onSymbolSelect} presentations={presentations} renderExpanded={renderExpanded} row={row} />;
    })}</tbody></table></div>}
  </div>;
}

function FragmentRow({ columns, expanded, onExpand, onSymbolSelect, presentations, renderExpanded, row }: { columns: string[]; expanded: boolean; onExpand?: () => void; onSymbolSelect?: (symbol: string) => void; presentations: ReturnType<typeof useTickerPresentations>; renderExpanded?: (row: PreviewRow) => ReactNode; row: PreviewRow }) {
  return <>{<tr className={expanded ? "is-expanded" : undefined}>{renderExpanded ? <td className="trading-expand-column"><button aria-label={expanded ? "Collapse row" : "Expand row"} aria-expanded={expanded} onClick={onExpand} type="button">{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button></td> : null}{columns.map((column) => <td className={`${tableCellClass(column)} preview-cell-${column.replace(/[^a-z0-9_-]/gi, "-")}`} data-tone={cellTone(row[column], column)} key={column}><PreviewCell column={column} onSymbolSelect={onSymbolSelect} presentations={presentations} row={row} /></td>)}</tr>}{expanded && renderExpanded ? <tr className="trading-expanded-row"><td colSpan={columns.length + 1}>{renderExpanded(row)}</td></tr> : null}</>;
}

function searchableValue(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value).toLowerCase();
  return String(value).toLowerCase();
}

function compareTradingValues(left: unknown, right: unknown) {
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  if (left !== "" && right !== "" && Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber;
  const leftDate = Date.parse(String(left || ""));
  const rightDate = Date.parse(String(right || ""));
  if (Number.isFinite(leftDate) && Number.isFinite(rightDate)) return leftDate - rightDate;
  return String(left ?? "").localeCompare(String(right ?? ""), undefined, { numeric: true, sensitivity: "base" });
}

function PreviewCell({ column, onSymbolSelect, presentations, row }: { column: string; onSymbolSelect?: (symbol: string) => void; presentations: ReturnType<typeof useTickerPresentations>; row: PreviewRow }) {
  if (isPreviewTickerColumn(column)) {
    const ticker = String(row[column] || "").trim().toUpperCase();
    return <SecurityIdentityCell companyName={String(row.company_name ?? row.issuer_name ?? presentations[ticker]?.issuer_name ?? "")} country={String(row.country ?? row.company_country_code ?? presentations[ticker]?.country ?? "")} halted={row.market_is_halted ?? row.is_halted ?? row.trading_status} logoUrl={String(row.logo_url ?? presentations[ticker]?.logo_url ?? "")} newsRecency={row.live_news_recency} onTickerSelect={onSymbolSelect} secCount={row.sec_count ?? presentations[ticker]?.sec_count} secLabels={row.sec_labels ?? presentations[ticker]?.sec_labels} secRecency={row.sec_recency ?? presentations[ticker]?.sec_recency} secReviewDirection={row.sec_review_fundamental_direction ?? presentations[ticker]?.sec_review_fundamental_direction} secReviewStatus={row.sec_review_status ?? presentations[ticker]?.sec_review_status} secSynthesisCount={row.sec_synthesis_count ?? presentations[ticker]?.sec_synthesis_count} secSynthesisDirection={row.sec_synthesis_direction ?? presentations[ticker]?.sec_synthesis_direction} ticker={ticker} />;
  }
  if (isPreviewTimeColumn(column)) return <MarketTime includeSeconds value={String(row[column] || "")} />;
  return <PresentedValue column={column} value={row[column]} />;
}

function isPreviewTickerColumn(column: string) { return ["symbol", "ticker", "candidate_massive_ticker"].includes(column.toLowerCase()); }
function isPreviewTimeColumn(column: string) { const normalized = column.toLowerCase(); return normalized === "time" || normalized.endsWith("_time") || normalized.endsWith("_at") || normalized.endsWith("_at_utc"); }

function PortfolioPreview({ data, settings }: { data: CanonicalTradingPreview; settings: ContainerSettings["portfolio"] }) {
  const metrics = data.portfolio.metrics;
  const exposure = data.portfolio.exposure;
  const ledgerRows = data.ledger.map((row) => ({ account: row.account_id, currency: row.currency, cash: nestedValue(row, "values", "cashbalance", "cashBalance"), settled: nestedValue(row, "values", "settledcash", "settledCash"), net_liquidation: nestedValue(row, "values", "netliquidationvalue", "netLiquidationValue") }));
  return <section className="trading-preview trading-portfolio-preview">
    <TradingFreshness data={data} />
    <div className="trading-primary-metrics">
      <TradingMetric label="Net liquidation" value={money(metrics.net_liquidation)} tone="primary" />
      <TradingMetric label="Available funds" value={money(metrics.available_funds)} tone="positive" />
      <TradingMetric label="Excess liquidity" value={money(metrics.excess_liquidity)} tone="positive" />
      <TradingMetric label="Buying power" value={money(metrics.buying_power)} />
      {settings.showPnl ? <TradingMetric label="Unrealized P&L" value={signedMoney(metrics.unrealized_pnl)} tone={numberTone(metrics.unrealized_pnl)} /> : null}
      {settings.showPnl ? <TradingMetric label="Realized P&L" value={signedMoney(metrics.realized_pnl)} tone={numberTone(metrics.realized_pnl)} /> : null}
    </div>
    {settings.showExposure ? <div className="trading-exposure-grid"><TradingMetric label="Long exposure" value={money(exposure.long_value)} tone="positive" /><TradingMetric label="Short exposure" value={money(exposure.short_value)} tone="negative" /><TradingMetric label="Net exposure" value={signedMoney(exposure.net_value)} tone={numberTone(exposure.net_value)} /><TradingMetric label="Gross exposure" value={money(exposure.gross_value)} /></div> : null}
    <div className="trading-secondary-heading"><strong>Cash ledger</strong><span>Every broker currency; BASE is not substituted for local balances</span></div>
    <PreviewTable columns={["account", "currency", "cash", "settled", "net_liquidation"]} rows={ledgerRows} />
    {data.portfolio.management ? <PortfolioManagementPreview data={data} management={data.portfolio.management} /> : null}
  </section>;
}

function PortfolioManagementPreview({ data, management }: { data: CanonicalTradingPreview; management: NonNullable<CanonicalTradingPreview["portfolio"]["management"]> }) {
  const [accounts, setAccounts] = useState(management.accounts);
  const [operationalMetrics, setOperationalMetrics] = useState(management.operational_metrics);
  const [pending, setPending] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    setAccounts(management.accounts);
    setOperationalMetrics(management.operational_metrics);
  }, [management]);
  const operational = data.mode === "live" || data.mode === "paper";
  const command = async (
    accountKey: string,
    value: "pause_entries" | "resume_entries" | "reduce_only" | "reconcile" | "select_policy" | "disable_strategy" | "enable_strategy" | "kill_entries" | "emergency_flatten",
    detail: Record<string, string> = {},
  ) => {
    const commandKey = `${accountKey}:${value}`;
    setPending(commandKey);
    setMessage("");
    try {
      const result = await api<{
        control_mode?: string;
        disabled_strategy_allocations?: string[];
        execution_required?: boolean;
        policy?: Record<string, unknown> & { identity?: string };
        portfolio_management?: typeof management;
      }>(
        `/api/trading/portfolio-management/${encodeURIComponent(accountKey)}/commands`,
        {
          body: JSON.stringify({ account_keys: accountKey, account_type: data.mode, command: value, detail, reason: "Canvas operator command" }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      if (result.portfolio_management) {
        setAccounts(result.portfolio_management.accounts);
        setOperationalMetrics(result.portfolio_management.operational_metrics);
      }
      else setAccounts((current) => current.map((row) => row.account_key === accountKey ? {
        ...row,
        ...(result.control_mode ? { control_mode: result.control_mode } : {}),
        ...(result.policy ? { policy: result.policy } : {}),
        ...(result.disabled_strategy_allocations ? { disabled_strategy_allocations: result.disabled_strategy_allocations } : {}),
      } : row));
      setMessage(
        result.execution_required
          ? "Command queued for fresh validation by the authenticated trading runtime."
          : value === "reconcile"
          ? "Broker reconciliation completed."
          : "Portfolio control updated.",
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPending("");
    }
  };
  return <section className="portfolio-management-preview" aria-label="Portfolio management">
    <div className="trading-secondary-heading"><strong>Portfolio management</strong><span>IBKR-authoritative state · account-specific policy · portfolio approval before OMS</span></div>
    {management.stale ? <div className="trading-disclosure" data-tone="negative">Entries blocked: {management.stale_reason || "broker state is stale"}</div> : null}
    {message ? <div className="trading-disclosure" role="status">{message}</div> : null}
    {operationalMetrics ? <div className="portfolio-management-metrics" aria-label="Portfolio and OMS operational metrics">
      <TradingMetric label="Decisions" value={String(operationalMetrics.portfolio.decision_count)} />
      <TradingMetric label="Rejected" value={String(operationalMetrics.portfolio.disposition_counts.rejected || 0)} tone={operationalMetrics.portfolio.disposition_counts.rejected ? "negative" : "positive"} />
      <TradingMetric label="Active reservations" value={String(operationalMetrics.portfolio.active_reservation_count)} />
      <TradingMetric label="Reserved notional" value={money(operationalMetrics.portfolio.active_reserved_notional)} />
      <TradingMetric label="OMS groups" value={String(operationalMetrics.oms.managed_group_count)} />
      <TradingMetric label="Unknown outcome" value={String(operationalMetrics.oms.outcome_unknown_count)} tone={operationalMetrics.oms.outcome_unknown_count ? "negative" : "positive"} />
      <TradingMetric label="Reconcile failures" value={String(operationalMetrics.oms.reconciliation_failure_count)} tone={operationalMetrics.oms.reconciliation_failure_count ? "negative" : "positive"} />
      <TradingMetric label="Unprotected quantity" value={formatQuantity(operationalMetrics.oms.unprotected_quantity)} tone={operationalMetrics.oms.unprotected_quantity ? "negative" : "positive"} />
    </div> : null}
    <div className="portfolio-management-account-list">
      {accounts.map((account) => {
        const metrics = account.metrics;
        const isPending = pending.startsWith(`${account.account_key}:`);
        const riskState = String(account.continuous_risk?.state || "");
        const activeGroups = (account.managed_order_groups ?? []).filter((row) => !["filled", "cancelled", "rejected", "policy_blocked"].includes(String(row.state.state || "")));
        const protectionDeficits = activeGroups.filter((row) =>
          Number(row.state.protection_required_quantity || 0) > Number(row.state.protection_coverage_quantity || 0));
        return <article className="portfolio-management-account" data-sync={account.sync_state} key={account.account_key}>
          <header>
            <div><strong>{account.account_key}</strong><span>{account.account_class} · {String(account.policy.identity || "unversioned policy")}</span></div>
            <div className="portfolio-management-status"><span data-state={account.sync_state}>{labelFor(account.sync_state)}</span><span data-state={account.control_mode}>{labelFor(account.control_mode)}</span>{riskState ? <span data-state={riskState}>{labelFor(riskState)}</span> : null}</div>
          </header>
          <div className="portfolio-management-metrics">
            <TradingMetric label="Eligible equity" value={money(metrics.eligible_equity)} />
            <TradingMetric label="Gross headroom" value={money(metrics.gross_headroom)} tone="positive" />
            <TradingMetric label="Reserved" value={money(metrics.reserved_notional)} />
            <TradingMetric label="Risk headroom" value={money(metrics.planned_risk_headroom)} />
            <TradingMetric label="Positions" value={String(account.position_count)} />
            <TradingMetric label="Working orders" value={String(account.working_order_count)} />
            <TradingMetric label="Daily loss" value={money(metrics.daily_loss || 0)} tone={Number(metrics.daily_loss || 0) > 0 ? "negative" : undefined} />
            <TradingMetric label="Drawdown" value={money(metrics.drawdown || 0)} tone={Number(metrics.drawdown || 0) > 0 ? "negative" : undefined} />
          </div>
          <div className="portfolio-management-evidence">
            <span>{(account.reservations ?? []).length} reservations</span>
            <span>{(account.allocations ?? []).length} allocations</span>
            <span data-tone={(account.reconciliation ?? []).length ? "negative" : "positive"}>{(account.reconciliation ?? []).length} reconciliation differences</span>
            <span data-tone={protectionDeficits.length ? "negative" : "positive"}>{protectionDeficits.length ? `${protectionDeficits.length} protection deficits` : "Protection reconciled"}</span>
            <span>{activeGroups.length} managed order groups</span>
            <span>{(account.pending_operational_commands ?? []).filter((row) => row.status === "pending").length} pending operator commands</span>
            <span>{account.observed_at ? <>As of <MarketTime value={account.observed_at} /></> : "No broker watermark"}</span>
          </div>
          {operational ? <div className="portfolio-management-controls">
            <label className="portfolio-policy-select">
              <span>Policy revision</span>
              <select
                aria-label={`Policy revision for ${account.account_key}`}
                disabled={isPending}
                onChange={(event) => void command(account.account_key, "select_policy", { policy_identity: event.target.value })}
                value={String(account.policy.identity || "")}
              >
                {(account.available_policies ?? []).map((policy) => <option key={policy.identity} value={policy.identity}>{policy.identity}</option>)}
              </select>
            </label>
            {account.control_mode === "enabled"
              ? <button className="button secondary compact" disabled={isPending} onClick={() => void command(account.account_key, "pause_entries")} type="button">Pause entries</button>
              : <button className="button secondary compact" disabled={isPending} onClick={() => void command(account.account_key, "resume_entries")} type="button">Resume entries</button>}
            <button className="button secondary compact" disabled={isPending || account.control_mode === "reduce_only"} onClick={() => void command(account.account_key, "reduce_only")} type="button">Reduce only</button>
            <button className="button secondary compact" disabled={isPending} onClick={() => void command(account.account_key, "reconcile")} type="button"><RefreshCcw size={12} /> Reconcile</button>
            <button className="button secondary compact" data-tone="negative" disabled={isPending} onClick={() => void command(account.account_key, "kill_entries")} type="button">Kill entries</button>
            <button
              className="button secondary compact"
              data-tone="negative"
              disabled={isPending}
              onClick={() => {
                if (window.confirm(`Emergency flatten ${account.account_key}? This queues bounded liquidation for every confirmed position in the account.`)) {
                  void command(account.account_key, "emergency_flatten");
                }
              }}
              type="button"
            >
              Emergency flatten
            </button>
            {Object.entries(account.strategy_allocations).map(([strategyId, fraction]) => {
              const disabled = (account.disabled_strategy_allocations ?? []).includes(strategyId);
              return <button
                className="button secondary compact portfolio-strategy-control"
                data-disabled={disabled || undefined}
                disabled={isPending}
                key={strategyId}
                onClick={() => void command(account.account_key, disabled ? "enable_strategy" : "disable_strategy", { strategy_id: strategyId })}
                title={`${disabled ? "Enable" : "Disable"} ${strategyId} entries for this account`}
                type="button"
              >
                {strategyId} {Math.round(Number(fraction) * 100)}% · {disabled ? "Disabled" : "Enabled"}
              </button>;
            })}
          </div> : <div className="trading-disclosure">Replay and Backtest use the same policy evidence with a simulated broker; operational controls are available only in Live and Paper.</div>}
          {activeGroups.length ? <details className="trading-disclosure">
            <summary>Adaptive execution and protection evidence</summary>
            <PreviewTable
              columns={["ticker", "state", "execution_policy", "protection_profile", "current_limit", "protection"]}
              rows={activeGroups.map((row) => ({
                ticker: String(row.state.intent?.ticker || ""),
                state: String(row.state.state || ""),
                execution_policy: String((row.state.intent?.execution_policy as PreviewRow | undefined)?.policy_id || "legacy"),
                protection_profile: String((row.state.intent?.protection_profile as PreviewRow | undefined)?.profile_id || "legacy"),
                current_limit: row.state.current_limit_price ?? "",
                protection: `${Number(row.state.protection_coverage_quantity || 0)} / ${Number(row.state.protection_required_quantity || 0)}`,
              }))}
            />
          </details> : null}
        </article>;
      })}
    </div>
    {management.groups.length ? <><div className="trading-secondary-heading"><strong>Aggregate groups</strong><span>Cross-account caps without implicit routing or mirrored orders</span></div><PreviewTable columns={["group_id", "gross_exposure", "gross_headroom", "sync_state"]} rows={management.groups} /></> : null}
  </section>;
}

function PositionsPreview({ data, onSymbolSelect, settings }: { data: CanonicalTradingPreview; onSymbolSelect?: (symbol: string) => void; settings: ContainerSettings["positions"] }) {
  const [view, setView] = useState<"open" | "closed" | "timeline">("open");
  const openRows = data.positions.map((row) => {
    const symbol = nestedValue(row, "instrument", "symbol");
    const account = String(row.account_id || "");
    const quantity = Number(row.quantity || 0);
    const averagePrice = Number(row.average_price || 0);
    const mark = Number(row.market_price || 0);
    const returnPct = averagePrice > 0 ? ((mark - averagePrice) / averagePrice) * 100 * (quantity < 0 ? -1 : 1) : 0;
    const relatedOrders = data.orders.filter((order) => String(order.account_id || "") === account && nestedValue(order, "instrument", "symbol") === symbol && !terminalOrderState(String(order.lifecycle_state || "")));
    const relatedExecutions = data.executions.filter((execution) => String(execution.account_id || "") === account && nestedValue(execution, "instrument", "symbol") === symbol);
    return { account, symbol, side: quantity > 0 ? "Long" : quantity < 0 ? "Short" : "Flat", quantity, average_price: row.average_price, mark: row.market_price, return_pct: returnPct, market_value: row.market_value, unrealized_pnl: row.unrealized_pnl, realized_pnl: row.realized_pnl, working_orders: relatedOrders.length, fills: relatedExecutions.length, updated_at: row.source_event_time, _position: row, _orders: relatedOrders, _executions: relatedExecutions };
  }).filter((row) => row.quantity !== 0);
  const closedRows = data.closed_trades.map((row) => ({ closed_at: row.closed_at, symbol: nestedValue(row, "instrument", "symbol"), side: row.side, quantity: row.quantity, entry_price: row.entry_price, exit_price: row.exit_price, gross_pnl: row.gross_pnl, fees: row.fees, net_pnl: row.net_pnl, account: row.account_id, _trade: row }));
  const timelineRows = data.activity.filter((row) => ["position_observed", "position_snapshot_completed", "execution_reported", "commission_reported"].includes(String(row.event_type || ""))).map((row) => ({ time: row.source_event_time, event: row.event_type, account: row.account_id, order_id: row.broker_order_id, execution_id: row.execution_id, provider: row.provider }));
  const netPnl = openRows.reduce((total, row) => total + Number(row.unrealized_pnl || 0), 0);
  const grossValue = openRows.reduce((total, row) => total + Math.abs(Number(row.market_value || 0)), 0);
  const winners = openRows.filter((row) => Number(row.unrealized_pnl || 0) > 0).length;
  const openColumns = settings.showPnl ? ["symbol", "side", "quantity", "average_price", "mark", "return_pct", "market_value", "unrealized_pnl", "working_orders", "fills", "account", "updated_at"] : ["symbol", "side", "quantity", "average_price", "mark", "market_value", "working_orders", "fills", "account", "updated_at"];
  return <section className="trading-preview trading-position-manager"><TradingFreshness data={data} />
    <div className="trading-summary-strip"><TradingMetric label="Open positions" value={String(openRows.length)} /><TradingMetric label="Winning" value={`${winners}/${openRows.length}`} tone={winners ? "positive" : "neutral"} /><TradingMetric label="Open P&L" value={signedMoney(netPnl)} tone={numberTone(netPnl)} /><TradingMetric label="Gross exposure" value={money(grossValue)} /></div>
    <TradingTabs active={view} onChange={(value) => setView(value as typeof view)} tabs={[{ id: "open", label: "Open", count: openRows.length }, { id: "closed", label: "Closed", count: closedRows.length }, { id: "timeline", label: "Timeline", count: timelineRows.length }]} />
    {view === "open" ? <TradingDataTable columns={openColumns} defaultSort="market_value" filterColumn="side" filterLabel="All directions" onSymbolSelect={onSymbolSelect} renderExpanded={(row) => <PositionDetail row={row} />} rows={openRows.slice(0, settings.limit)} searchPlaceholder="Search symbol, account, side…" /> : null}
    {view === "closed" ? <><div className="trading-disclosure">{data.closed_trades_note}</div><TradingDataTable columns={settings.showPnl ? ["closed_at", "symbol", "side", "quantity", "entry_price", "exit_price", "gross_pnl", "fees", "net_pnl", "account"] : ["closed_at", "symbol", "side", "quantity", "entry_price", "exit_price", "account"]} defaultSort="closed_at" filterColumn="side" filterLabel="All directions" onSymbolSelect={onSymbolSelect} rows={closedRows.slice(0, settings.limit)} searchPlaceholder="Search closed positions…" /></> : null}
    {view === "timeline" ? <TradingDataTable columns={["time", "event", "account", "order_id", "execution_id", "provider"]} defaultSort="time" filterColumn="event" filterLabel="All events" rows={timelineRows.slice(0, settings.limit)} searchPlaceholder="Search position history…" /> : null}
  </section>;
}

function PositionDetail({ row }: { row: PreviewRow }) {
  const orders = (row._orders as PreviewRow[] | undefined) ?? [];
  const executions = (row._executions as PreviewRow[] | undefined) ?? [];
  const position = (row._position as PreviewRow | undefined) ?? {};
  const orderRows = orders.map(orderTableRow);
  const executionRows = executions.map(executionTableRow);
  return <div className="trading-row-detail"><div className="trading-detail-facts"><span><small>Contract</small><strong>{String(nestedValue(position, "instrument", "conid") || "—")}</strong></span><span><small>Asset / currency</small><strong>{String(nestedValue(position, "instrument", "security_type") || "—")} · {String(nestedValue(position, "instrument", "currency") || "—")}</strong></span><span><small>Model</small><strong>{String(position.model || "Default")}</strong></span><span><small>Snapshot</small><strong>{String(position.snapshot_id || "—")}</strong></span></div><div className="trading-related-grid"><section><header><strong>Working orders</strong><span>{orders.length}</span></header>{orders.length ? <PreviewTable columns={["status", "side", "remaining", "type", "limit", "stop", "order_id"]} rows={orderRows} /> : <p>No working orders for this position.</p>}</section><section><header><strong>Recent fills</strong><span>{executions.length}</span></header>{executions.length ? <PreviewTable columns={["time", "side", "quantity", "price", "exchange", "commission"]} rows={executionRows} /> : <p>No execution evidence in the loaded window.</p>}</section></div></div>;
}

function OrdersPreview({ data, onSymbolSelect, settings }: { data: CanonicalTradingPreview; onSymbolSelect?: (symbol: string) => void; settings: ContainerSettings["orders"] }) {
  const [view, setView] = useState<"working" | "all" | "fills">("working");
  const orderRows: PreviewRow[] = data.orders.map((row) => ({ ...orderTableRow(row), _order: row, _executions: data.executions.filter((execution) => String(execution.account_id || "") === String(row.account_id || "") && String(execution.broker_order_id || "") === String(row.broker_order_id || "")) }));
  const workingRows = orderRows.filter((row) => !terminalOrderState(String(row.status || "")));
  const executionRows = data.executions.map(executionTableRow);
  const filledCount = orderRows.filter((row) => String(row.status) === "filled").length;
  const rejectedCount = orderRows.filter((row) => String(row.status) === "rejected").length;
  const columns = settings.showOrderIds ? ["status", "broker_status", "symbol", "side", "progress", "remaining", "type", "limit", "stop", "tif", "account", "order_id", "updated_at"] : ["status", "symbol", "side", "progress", "remaining", "type", "limit", "stop", "tif", "account", "updated_at"];
  const activeRows = view === "working" ? workingRows : orderRows;
  return <section className="trading-preview trading-order-manager"><TradingFreshness data={data} />
    <div className="trading-summary-strip"><TradingMetric label="Working" value={String(workingRows.length)} tone={workingRows.length ? "primary" : "neutral"} /><TradingMetric label="Filled" value={String(filledCount)} tone={filledCount ? "positive" : "neutral"} /><TradingMetric label="Rejected" value={String(rejectedCount)} tone={rejectedCount ? "negative" : "neutral"} /><TradingMetric label="Executions" value={String(executionRows.length)} /></div>
    <TradingTabs active={view} onChange={(value) => setView(value as typeof view)} tabs={[{ id: "working", label: "Working", count: workingRows.length }, { id: "all", label: "All orders", count: orderRows.length }, { id: "fills", label: "Fills", count: executionRows.length }]} />
    {view !== "fills" ? <TradingDataTable columns={columns} defaultSort="updated_at" filterColumn="status" filterLabel="All statuses" onSymbolSelect={onSymbolSelect} renderExpanded={(row) => <OrderDetail row={row} />} rows={activeRows.slice(0, settings.limit)} searchPlaceholder="Search orders, symbols, IDs…" /> : <TradingDataTable columns={["time", "symbol", "side", "quantity", "price", "exchange", "commission", "fee_state", "account", "order_id", "execution_id"]} defaultSort="time" filterColumn="side" filterLabel="All sides" onSymbolSelect={onSymbolSelect} rows={executionRows.slice(0, settings.limit)} searchPlaceholder="Search fills, venues, order IDs…" />}
  </section>;
}

function OrderDetail({ row }: { row: PreviewRow }) {
  const order = (row._order as PreviewRow | undefined) ?? {};
  const executions = ((row._executions as PreviewRow[] | undefined) ?? []).map(executionTableRow);
  return <div className="trading-row-detail"><div className="trading-detail-facts"><span><small>Client order</small><strong>{String(order.client_order_id || "—")}</strong></span><span><small>Command</small><strong>{String(order.command_id || "—")}</strong></span><span><small>Parent</small><strong>{String(order.parent_order_id || "—")}</strong></span><span><small>Broker message</small><strong>{String(order.warning || order.rejection_reason || "None")}</strong></span></div><section className="trading-fill-evidence"><header><strong>Execution evidence</strong><span>{executions.length} fill{executions.length === 1 ? "" : "s"}</span></header>{executions.length ? <PreviewTable columns={["time", "execution_id", "side", "quantity", "price", "exchange", "commission", "fee_state"]} rows={executions} /> : <p>This order has no fills in the loaded execution window.</p>}</section></div>;
}

function ExecutionsPreview({ data, onSymbolSelect, settings }: { data: CanonicalTradingPreview; onSymbolSelect?: (symbol: string) => void; settings: ContainerSettings["fills"] }) {
  const rows = data.executions.map(executionTableRow);
  const columns = settings.showCommission ? ["time", "symbol", "side", "quantity", "price", "exchange", "commission", "fee_state", "net_amount", "account", "order_id", "execution_id"] : ["time", "symbol", "side", "quantity", "price", "exchange", "account", "order_id", "execution_id"];
  return <section className="trading-preview"><TradingFreshness data={data} /><div className="trading-disclosure">Advanced immutable execution audit. For routine management, use Orders &amp; Fills where each order expands into its related executions.</div><TradingDataTable columns={columns} defaultSort="time" filterColumn="side" filterLabel="All sides" onSymbolSelect={onSymbolSelect} rows={rows.slice(0, settings.limit)} searchPlaceholder="Search immutable execution evidence…" /></section>;
}

function ClosedTradesPreview({ data, onSymbolSelect, settings }: { data: CanonicalTradingPreview; onSymbolSelect?: (symbol: string) => void; settings: ContainerSettings["closed_trades"] }) {
  const rows = data.closed_trades.map((row) => ({ closed_at: row.closed_at, symbol: nestedValue(row, "instrument", "symbol"), side: row.side, quantity: row.quantity, entry_price: row.entry_price, exit_price: row.exit_price, gross_pnl: row.gross_pnl, fees: row.fees, net_pnl: row.net_pnl, account: row.account_id }));
  const columns = settings.showFees ? ["closed_at", "symbol", "side", "quantity", "entry_price", "exit_price", "gross_pnl", "fees", "net_pnl", "account"] : ["closed_at", "symbol", "side", "quantity", "entry_price", "exit_price", "gross_pnl", "net_pnl", "account"];
  return <section className="trading-preview"><div className="trading-disclosure">Advanced derived round-trip audit. The Position Manager provides the normal open, closed, and lifecycle workflow. {data.closed_trades_note}</div><TradingDataTable columns={columns} defaultSort="closed_at" filterColumn="side" filterLabel="All sides" onSymbolSelect={onSymbolSelect} rows={rows.slice(0, settings.limit)} searchPlaceholder="Search derived round trips…" /></section>;
}

function TradingTabs({ active, onChange, tabs }: { active: string; onChange: (id: string) => void; tabs: Array<{ count: number; id: string; label: string }> }) {
  return <div aria-label="Trading view" className="trading-view-tabs" role="tablist">{tabs.map((tab) => <button aria-selected={active === tab.id} className={active === tab.id ? "active" : undefined} key={tab.id} onClick={() => onChange(tab.id)} role="tab" type="button"><span>{tab.label}</span><strong>{tab.count}</strong></button>)}</div>;
}

function orderTableRow(row: PreviewRow): PreviewRow {
  const filled = Number(row.filled_quantity || 0);
  const total = Number(row.total_quantity || 0);
  return { status: row.lifecycle_state, broker_status: row.broker_status_raw, symbol: nestedValue(row, "instrument", "symbol"), side: row.side, progress: `${filled}/${total}`, filled, total, remaining: row.remaining_quantity, type: row.order_type, limit: row.limit_price, stop: row.stop_price, tif: row.time_in_force, account: row.account_id, order_id: row.broker_order_id, client_id: row.client_order_id, updated_at: row.source_event_time };
}

function executionTableRow(row: PreviewRow): PreviewRow {
  return { time: row.source_event_time, execution_id: row.execution_id, symbol: nestedValue(row, "instrument", "symbol"), side: row.side, quantity: row.quantity, price: row.price, exchange: row.exchange, commission: row.commission, fee_state: row.commission_status, net_amount: row.net_amount, account: row.account_id, order_id: row.broker_order_id };
}

function terminalOrderState(status: string) { return ["filled", "cancelled", "rejected", "expired", "inactive"].includes(status.toLowerCase()); }

function ActivityPreview({ data, settings }: { data: CanonicalTradingPreview; settings: ContainerSettings["activity"] }) {
  const rows = data.activity.map((row) => ({ time: row.source_event_time, event: row.event_type, account: row.account_id, order_id: row.broker_order_id, client_id: row.client_order_id, execution_id: row.execution_id, provider: row.provider, correlation: row.correlation_id }));
  return <section className="trading-preview"><TradingFreshness data={data} /><PreviewTable columns={["time", "event", "account", "order_id", "client_id", "execution_id", "provider", "correlation"]} rows={rows.slice(0, settings.limit)} /></section>;
}

function TradingJournalPreview({ data, settings }: { data: CanonicalTradingPreview; settings: ContainerSettings["performance_journal"] }) {
  const [view, setView] = useState<"overview" | "strategies" | "trades" | "execution" | "risk">("overview");
  const [pnlTimeframe, setPnlTimeframe] = useState<PnlCandleTimeframe>("30m");
  const [guideOpen, setGuideOpen] = useState(false);
  const report = data.performance_journal;
  const summary = report?.summary ?? {};
  const scope = report?.scope ?? {};
  const risk = report?.risk ?? {};
  const execution = report?.execution ?? {};
  const episodes = (report?.episodes ?? []).slice(0, settings.limit).map((row) => ({
    closed_at: row.closed_at,
    symbol: nestedValue(row, "instrument", "symbol"),
    side: row.side,
    strategy: row.strategy_id || "Unattributed",
    revision: Number(row.strategy_revision || 0) ? `v${row.strategy_revision}` : "—",
    setup: row.setup || "—",
    quantity: row.quantity,
    entry_price: row.entry_price,
    exit_price: row.exit_price,
    net_pnl: row.net_pnl,
    risk_multiple: row.risk_multiple,
    duration: compactDuration(Number(row.duration_seconds || 0)),
    exit_reason: row.exit_reason || "—",
    _episode: row,
  }));
  const strategyRows = (report?.strategies ?? []).map((row) => ({
    strategy: row.strategy_id,
    revision: Number(row.strategy_revision || 0) ? `v${row.strategy_revision}` : "—",
    trades: row.episode_count,
    net_pnl: row.net_pnl,
    win_rate_pct: ratioPct(row.win_rate),
    expectancy: row.expectancy,
    profit_factor: row.profit_factor,
    payoff_ratio: row.payoff_ratio,
    max_drawdown: row.maximum_drawdown,
  }));
  const tabs = [
    { id: "overview", label: "Overview", count: Number(summary.episode_count || 0) },
    { id: "strategies", label: "Strategies", count: strategyRows.length },
    { id: "trades", label: "Trades", count: episodes.length },
    { id: "execution", label: "Execution", count: Number(execution.fill_count || 0) },
    { id: "risk", label: "Risk", count: Number(summary.loss_count || 0) },
  ];
  if (!report) return <section className="trading-preview"><TradingFreshness data={data} /><EmptyState label="Performance journal is unavailable for this trading state" /></section>;
  return <section className="trading-preview performance-journal">
    <header className="performance-journal-header">
      <div><span>Decision record</span><strong>Trading performance</strong><small>Flat-to-flat episodes · net of available fees</small></div>
      <div className="performance-journal-scope"><span>{Number(scope.episode_count || 0)} episodes</span><span>{ratioPct(scope.attribution_coverage)} attributed</span><button onClick={() => setGuideOpen(true)} type="button"><HelpCircle size={14} /> Guide</button></div>
    </header>
    <TradingFreshness data={data} />
    <div className="performance-kpi-grid">
      <JournalMetric detail="Closed episode profit after recorded commissions and fees." label="Net P&L" tone={numberTone(summary.net_pnl)} value={signedMoney(summary.net_pnl)} />
      <JournalMetric detail="Average expected dollars per closed trade episode." label="Expectancy" tone={numberTone(summary.expectancy)} value={signedMoney(summary.expectancy)} />
      <JournalMetric detail="Gross winning dollars divided by gross losing dollars." label="Profit factor" tone={metricThresholdTone(summary.profit_factor, 1)} value={ratioNumber(summary.profit_factor)} />
      <JournalMetric detail="Winning episodes divided by all closed episodes." label="Win rate" tone={metricThresholdTone(summary.win_rate, 0.5)} value={ratioPct(summary.win_rate)} />
      <JournalMetric detail="Average winning episode divided by average losing episode." label="Payoff" tone={metricThresholdTone(summary.payoff_ratio, 1)} value={ratioNumber(summary.payoff_ratio)} />
      <JournalMetric detail="Largest peak-to-trough decline in cumulative closed P&L." label="Max drawdown" tone={Number(summary.maximum_drawdown || 0) > 0 ? "negative" : "neutral"} value={money(summary.maximum_drawdown)} />
    </div>
    <TradingTabs active={view} onChange={(value) => setView(value as typeof view)} tabs={tabs} />
    {view === "overview" ? <div className="performance-overview-stack"><div className="performance-overview-grid"><section className="performance-chart-card"><header><div><strong>Net P&L trajectory</strong><span>Cumulative closed-episode P&L</span></div><b data-tone={numberTone(summary.net_pnl)}>{signedMoney(summary.net_pnl)}</b></header><JournalAreaChart rows={report.equity_curve} /></section><section className="performance-diagnosis"><header><strong>Edge snapshot</strong><span>Read together, never from win rate alone</span></header><div><JournalFact label="Average win" tone="positive" value={money(summary.average_win)} /><JournalFact label="Average loss" tone="negative" value={money(summary.average_loss)} /><JournalFact label="Largest win" tone="positive" value={money(summary.largest_win)} /><JournalFact label="Largest loss" tone="negative" value={money(summary.largest_loss)} /><JournalFact label="Average hold" value={compactDuration(Number(summary.average_duration_seconds || 0))} /><JournalFact label="Fees" tone={Number(summary.total_fees || 0) > 0 ? "negative" : "neutral"} value={money(summary.total_fees)} /></div></section></div><JournalPnlCandleChart candles={report.pnl_candles?.[pnlTimeframe] ?? []} onTimeframeChange={setPnlTimeframe} timeframe={pnlTimeframe} /></div> : null}
    {view === "strategies" ? <div className="performance-strategy-view"><StrategyComparisonChart rows={strategyRows} /><TradingDataTable columns={["strategy", "revision", "trades", "net_pnl", "win_rate_pct", "expectancy", "profit_factor", "payoff_ratio", "max_drawdown"]} defaultSort="net_pnl" filterColumn="strategy" filterLabel="All strategies" rows={strategyRows} searchPlaceholder="Search strategies and revisions…" /></div> : null}
    {view === "trades" ? <TradingDataTable columns={settings.showRiskMultiple ? ["closed_at", "symbol", "side", "strategy", "revision", "setup", "quantity", "entry_price", "exit_price", "net_pnl", "risk_multiple", "duration", "exit_reason"] : ["closed_at", "symbol", "side", "strategy", "revision", "setup", "quantity", "entry_price", "exit_price", "net_pnl", "duration", "exit_reason"]} defaultSort="closed_at" filterColumn="strategy" filterLabel="All strategies" renderExpanded={(row) => <JournalEpisodeDetail row={row} />} rows={episodes} searchPlaceholder="Search trades, symbols, setups, exits…" /> : null}
    {view === "execution" ? <ExecutionJournalView execution={execution} /> : null}
    {view === "risk" ? <RiskJournalView risk={risk} summary={summary} /> : null}
    {guideOpen ? <TradingJournalGuide onClose={() => setGuideOpen(false)} /> : null}
  </section>;
}

function JournalMetric({ detail, label, tone, value }: { detail: string; label: string; tone: "negative" | "neutral" | "positive"; value: string }) {
  return <div className={`journal-metric tone-${tone}`} title={detail}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function JournalFact({ label, tone = "neutral", value }: { label: string; tone?: "negative" | "neutral" | "positive"; value: string }) {
  return <span className={`journal-fact tone-${tone}`}><small>{label}</small><strong>{value}</strong></span>;
}

function JournalAreaChart({ rows }: { rows: Array<{ time: string; value: string | number; drawdown: string | number }> }) {
  if (!rows.length) return <EmptyState label="Close at least one flat-to-flat episode to build the performance curve" />;
  const values = rows.map((row) => Number(row.value || 0));
  const { maximum, minimum, ticks } = journalChartDomain(values, true);
  const plot = { bottom: 132, left: 52, right: 424, top: 14 };
  const x = (index: number) => rows.length === 1 ? (plot.left + plot.right) / 2 : plot.left + (index / (rows.length - 1)) * (plot.right - plot.left);
  const y = (value: number) => plot.top + ((maximum - value) / (maximum - minimum)) * (plot.bottom - plot.top);
  const points = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const zeroY = y(0);
  const area = `${x(0)},${zeroY} ${points} ${x(rows.length - 1)},${zeroY}`;
  const lineColor = values[values.length - 1] >= 0 ? "var(--success)" : "var(--danger)";
  return <svg aria-label="Cumulative net profit and loss with dollar axis" className="journal-area-chart" preserveAspectRatio="none" role="img" viewBox="0 0 440 154"><defs><linearGradient id="journal-equity-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={lineColor} stopOpacity="0.28" /><stop offset="1" stopColor={lineColor} stopOpacity="0.02" /></linearGradient></defs>{ticks.map((tick) => <g className="journal-chart-grid" key={tick}><line x1={plot.left} x2={plot.right} y1={y(tick)} y2={y(tick)} /><text textAnchor="end" x={plot.left - 7} y={y(tick) + 3}>{formatMoneyAxis(tick)}</text></g>)}<line className="journal-chart-zero" x1={plot.left} x2={plot.right} y1={zeroY} y2={zeroY} /><polygon fill="url(#journal-equity-fill)" points={area} /><polyline fill="none" points={points} stroke={lineColor} strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" /><text x={plot.left} y="151">{formatJournalDate(rows[0].time)}</text><text textAnchor="end" x={plot.right} y="151">{formatJournalDate(rows[rows.length - 1].time)}</text></svg>;
}

function JournalPnlCandleChart({ candles, onTimeframeChange, timeframe }: { candles: PnlCandle[]; onTimeframeChange: (value: PnlCandleTimeframe) => void; timeframe: PnlCandleTimeframe }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const rows = candles.slice(-120);
  const selectedIndex = hoveredIndex !== null && hoveredIndex < rows.length ? hoveredIndex : rows.length - 1;
  const selected = rows[selectedIndex];
  const values = rows.flatMap((row) => [Number(row.low), Number(row.high)]);
  const { maximum, minimum, ticks } = journalChartDomain(values, false);
  const plot = { bottom: 204, left: 58, right: 782, top: 20 };
  const times = rows.map((row) => new Date(row.bucket_start).getTime());
  const firstTime = times.length ? Math.min(...times) : 0;
  const lastTime = times.length ? Math.max(...times) : firstTime;
  const x = (index: number) => rows.length === 1 ? (plot.left + plot.right) / 2 : plot.left + ((times[index] - firstTime) / Math.max(1, lastTime - firstTime)) * (plot.right - plot.left);
  const y = (value: number) => plot.top + ((maximum - value) / (maximum - minimum)) * (plot.bottom - plot.top);
  const bodyWidth = Math.max(4, Math.min(14, (plot.right - plot.left) / Math.max(8, rows.length * 1.8)));
  const timeframes: Array<{ id: PnlCandleTimeframe; label: string; title: string }> = [{ id: "30m", label: "30m", title: "30 minutes" }, { id: "1h", label: "1h", title: "1 hour" }, { id: "1d", label: "1D", title: "1 day" }, { id: "1M", label: "1M", title: "1 month" }];
  function selectTimeframe(value: PnlCandleTimeframe) {
    setHoveredIndex(null);
    onTimeframeChange(value);
  }
  return <section className="performance-candle-card"><header><div><strong>Realized P&L candles</strong><span>Cumulative net P&L OHLC after each closed trade episode</span></div><div aria-label="P&L candle timeframe" className="journal-timeframe-tabs" role="group">{timeframes.map((option) => <button aria-pressed={timeframe === option.id} className={timeframe === option.id ? "is-active" : undefined} key={option.id} onClick={() => selectTimeframe(option.id)} title={option.title} type="button">{option.label}</button>)}</div></header>{selected ? <div className="journal-candle-readout"><span>{formatPnlCandleTime(selected.bucket_start, timeframe)}</span><span>O <b>{money(selected.open)}</b></span><span>H <b>{money(selected.high)}</b></span><span>L <b>{money(selected.low)}</b></span><span>C <b data-tone={numberTone(selected.close)}>{money(selected.close)}</b></span><span>Change <b data-tone={numberTone(selected.net_change)}>{signedMoney(selected.net_change)}</b></span><span>{selected.episode_count} {selected.episode_count === 1 ? "episode" : "episodes"}</span></div> : null}{rows.length ? <div className="journal-candle-scroll"><svg aria-label={`${timeframe} cumulative realized profit and loss candles`} className="journal-candle-chart" onMouseLeave={() => setHoveredIndex(null)} preserveAspectRatio="none" role="img" style={{ minWidth: `${Math.max(700, rows.length * 8)}px` }} viewBox="0 0 800 232">{ticks.map((tick) => <g className="journal-chart-grid" key={tick}><line x1={plot.left} x2={plot.right} y1={y(tick)} y2={y(tick)} /><text textAnchor="end" x={plot.left - 8} y={y(tick) + 3}>{formatMoneyAxis(tick)}</text></g>)}{rows.map((row, index) => { const open = Number(row.open); const close = Number(row.close); const high = Number(row.high); const low = Number(row.low); const up = close >= open; const center = x(index); const bodyTop = Math.min(y(open), y(close)); const bodyHeight = Math.max(2, Math.abs(y(open) - y(close))); return <g aria-label={`${formatPnlCandleTime(row.bucket_start, timeframe)} open ${money(open)}, high ${money(high)}, low ${money(low)}, close ${money(close)}`} className={`${up ? "is-up" : "is-down"}${selectedIndex === index ? " is-selected" : ""}`} key={row.bucket_start} onFocus={() => setHoveredIndex(index)} onMouseEnter={() => setHoveredIndex(index)} role="img" tabIndex={0}><line className="journal-candle-wick" x1={center} x2={center} y1={y(high)} y2={y(low)} /><rect className="journal-candle-body" height={bodyHeight} width={bodyWidth} x={center - bodyWidth / 2} y={bodyTop} /></g>; })}{rows.length === 1 ? <text textAnchor="middle" x={(plot.left + plot.right) / 2} y="226">{formatPnlCandleTime(rows[0].bucket_start, timeframe)}</text> : <><text x={plot.left} y="226">{formatPnlCandleTime(rows[0].bucket_start, timeframe)}</text>{rows.length > 2 ? <text textAnchor="middle" x={(plot.left + plot.right) / 2} y="226">{formatPnlCandleTime(rows[Math.floor(rows.length / 2)].bucket_start, timeframe)}</text> : null}<text textAnchor="end" x={plot.right} y="226">{formatPnlCandleTime(rows[rows.length - 1].bucket_start, timeframe)}</text></>}</svg></div> : <EmptyState label={`No closed episodes are available for ${timeframe} P&L candles`} />}</section>;
}

function journalChartDomain(values: number[], includeZero: boolean) {
  const finite = values.filter(Number.isFinite);
  const rawMinimum = finite.length ? Math.min(...finite, ...(includeZero ? [0] : [])) : 0;
  const rawMaximum = finite.length ? Math.max(...finite, ...(includeZero ? [0] : [])) : 1;
  const rawSpan = rawMaximum - rawMinimum || Math.max(1, Math.abs(rawMaximum) * 0.1);
  const minimum = rawMinimum - rawSpan * 0.08;
  const maximum = rawMaximum + rawSpan * 0.08;
  return { maximum, minimum, ticks: Array.from({ length: 5 }, (_, index) => maximum - ((maximum - minimum) * index) / 4) };
}

function StrategyComparisonChart({ rows }: { rows: PreviewRow[] }) {
  if (!rows.length) return <EmptyState label="No attributed or unattributed strategy episodes in this scope" />;
  const maximum = Math.max(1, ...rows.map((row) => Math.abs(Number(row.net_pnl || 0))));
  return <section className="strategy-comparison-chart"><header><strong>Net result by strategy revision</strong><span>Width is relative net P&L; use expectancy and sample size before ranking.</span></header>{rows.slice(0, 8).map((row) => { const value = Number(row.net_pnl || 0); return <div key={`${row.strategy}-${row.revision}`}><span>{String(row.strategy)} <small>{String(row.revision)}</small></span><i><b data-tone={numberTone(value)} style={{ width: `${Math.max(2, Math.abs(value) / maximum * 100)}%` }} /></i><strong data-tone={numberTone(value)}>{signedMoney(value)}</strong></div>; })}</section>;
}

function JournalEpisodeDetail({ row }: { row: PreviewRow }) {
  const episode = (row._episode as PreviewRow | undefined) ?? {};
  const episodeId = String(episode.episode_id || "");
  const [annotation, setAnnotation] = useState({ note: "", tags: [] as string[], review_status: "unreviewed", setup_override: "" });
  const [annotationState, setAnnotationState] = useState<"idle" | "loading" | "saving" | "saved" | "error">("loading");
  useEffect(() => {
    let active = true;
    setAnnotationState("loading");
    api<{ note?: string; tags?: string[]; review_status?: string; setup_override?: string }>(`/api/trading/journal/episodes/${encodeURIComponent(episodeId)}/annotation`)
      .then((payload) => { if (active) { setAnnotation({ note: payload.note ?? "", tags: payload.tags ?? [], review_status: payload.review_status ?? "unreviewed", setup_override: payload.setup_override ?? "" }); setAnnotationState("idle"); } })
      .catch(() => { if (active) setAnnotationState("error"); });
    return () => { active = false; };
  }, [episodeId]);
  async function saveAnnotation() {
    setAnnotationState("saving");
    try {
      const saved = await api<typeof annotation>(`/api/trading/journal/episodes/${encodeURIComponent(episodeId)}/annotation`, { method: "PUT", body: JSON.stringify(annotation) });
      setAnnotation(saved);
      setAnnotationState("saved");
    } catch { setAnnotationState("error"); }
  }
  return <div className="trading-row-detail journal-episode-detail"><div className="trading-detail-facts"><span><small>Episode ID</small><strong>{episodeId || "—"}</strong></span><span><small>Run</small><strong>{String(episode.run_id || "Unattributed")}</strong></span><span><small>Execution IDs</small><strong>{Array.isArray(episode.execution_ids) ? episode.execution_ids.join(", ") : "—"}</strong></span><span><small>Order IDs</small><strong>{Array.isArray(episode.order_ids) ? episode.order_ids.join(", ") : "—"}</strong></span></div><p>One episode begins when the position leaves flat and ends when it returns to flat. Scale-ins and partial exits remain one strategy decision.</p><section className="journal-review-editor"><header><div><strong>Review record</strong><span>Stored durably against this deterministic episode ID</span></div><em data-state={annotationState}>{annotationState === "loading" ? "Loading…" : annotationState === "saving" ? "Saving…" : annotationState === "saved" ? "Saved" : annotationState === "error" ? "Could not save" : "Ready"}</em></header><div><label><span>Status</span><select onChange={(event) => setAnnotation((current) => ({ ...current, review_status: event.target.value }))} value={annotation.review_status}><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option><option value="follow_up">Follow up</option></select></label><label><span>Setup override</span><input onChange={(event) => setAnnotation((current) => ({ ...current, setup_override: event.target.value }))} placeholder={String(episode.setup || "Optional reviewed setup")} value={annotation.setup_override} /></label><label className="journal-review-tags"><span>Tags</span><input onChange={(event) => setAnnotation((current) => ({ ...current, tags: event.target.value.split(",").map((tag) => tag.trim()).filter(Boolean) }))} placeholder="A+, followed plan, late entry" value={annotation.tags.join(", ")} /></label><label className="journal-review-note"><span>Review note</span><textarea onChange={(event) => setAnnotation((current) => ({ ...current, note: event.target.value }))} placeholder="What was planned, what happened, and what should be repeated or changed?" value={annotation.note} /></label></div><button disabled={!episodeId || annotationState === "saving" || annotationState === "loading"} onClick={saveAnnotation} type="button"><Save size={13} /> Save review</button></section></div>;
}

function ExecutionJournalView({ execution }: { execution: Record<string, unknown> }) {
  const venues = (execution.venues as PreviewRow[] | undefined) ?? [];
  return <div className="execution-journal-view"><div className="trading-summary-strip"><TradingMetric label="Fill notional" value={money(execution.fill_notional)} tone="primary" /><TradingMetric label="Recorded fees" value={money(execution.total_fees)} tone={Number(execution.total_fees || 0) > 0 ? "negative" : "neutral"} /><TradingMetric label="Average fill" value={formatQuantity(execution.average_fill_size)} /><TradingMetric label="Pending fees" value={String(execution.pending_fee_count || 0)} tone={Number(execution.pending_fee_count || 0) ? "negative" : "neutral"} /></div><section className="execution-quality-card"><header><strong>Execution quality</strong><span>Positive slippage is adverse to the trade direction.</span></header><div><JournalFact label="Signal slippage" tone={slippageTone(execution.average_signal_slippage)} value={basisPoints(execution.average_signal_slippage)} /><JournalFact label="Arrival slippage" tone={slippageTone(execution.average_arrival_slippage)} value={basisPoints(execution.average_arrival_slippage)} /><JournalFact label="Slippage coverage" value={ratioPct(execution.slippage_coverage)} /><JournalFact label="Rejected orders" tone={Number(execution.rejected_order_count || 0) ? "negative" : "neutral"} value={String(execution.rejected_order_count || 0)} /></div></section><TradingDataTable columns={["venue", "notional", "share_pct"]} defaultSort="notional" rows={venues.map((row) => ({ ...row, share_pct: ratioPct(row.share) }))} searchPlaceholder="Search execution venues…" /></div>;
}

function RiskJournalView({ risk, summary }: { risk: Record<string, string | number | null>; summary: Record<string, string | number | null> }) {
  return <div className="risk-journal-view"><section><header><ShieldCheck size={16} /><div><strong>Risk discipline</strong><span>Coverage states are shown explicitly; missing plans are never treated as zero risk.</span></div></header><div className="risk-journal-grid"><JournalFact label="Max drawdown" tone={Number(risk.maximum_drawdown || 0) ? "negative" : "neutral"} value={money(risk.maximum_drawdown)} /><JournalFact label="Loss streak" tone={Number(risk.maximum_losing_streak || 0) > 2 ? "negative" : "neutral"} value={String(risk.maximum_losing_streak || 0)} /><JournalFact label="Win streak" tone="positive" value={String(risk.maximum_winning_streak || 0)} /><JournalFact label="Planned-risk coverage" value={ratioPct(risk.planned_risk_coverage)} /><JournalFact label="Average R" tone={numberTone(risk.average_r_multiple)} value={ratioNumber(risk.average_r_multiple)} /><JournalFact label="Average hold" value={compactDuration(Number(summary.average_duration_seconds || 0))} /></div></section><section className="risk-coverage"><header><Target size={16} /><div><strong>Excursion evidence</strong><span>MAE and MFE require price-path observations while the episode is open.</span></div></header><div><JournalFact label="MAE coverage" value={ratioPct(risk.mae_coverage)} /><JournalFact label="Average MAE" tone="negative" value={money(risk.average_mae)} /><JournalFact label="MFE coverage" value={ratioPct(risk.mfe_coverage)} /><JournalFact label="Average MFE" tone="positive" value={money(risk.average_mfe)} /></div></section></div>;
}

function TradingJournalGuide({ onClose }: { onClose: () => void }) {
  return <div className="journal-guide-backdrop" role="presentation"><section aria-label="Trading journal guide" aria-modal="true" className="journal-guide-modal" role="dialog"><header><div><BookOpen size={20} /><span><strong>How to read the Trading Journal</strong><small>Performance evidence, not a broker confirmation or tax-lot statement</small></span></div><button aria-label="Close guide" onClick={onClose} type="button"><X size={18} /></button></header><div className="journal-guide-grid"><article><Gauge size={17} /><strong>Trade episode</strong><p>One account, instrument, and strategy position from flat to flat. Scale-ins and partial exits stay together so win rate counts decisions rather than FIFO fragments.</p></article><article><Activity size={17} /><strong>Expectancy</strong><p>Win rate × average win minus loss rate × average loss. Positive expectancy after fees is more important than win rate by itself.</p></article><article><BarChart3 size={17} /><strong>Profit factor and payoff</strong><p>Profit factor compares all winning dollars with all losing dollars. Payoff compares the average winner with the average loser.</p></article><article><BarChart3 size={17} /><strong>Realized P&amp;L candles</strong><p>Each candle is cumulative closed-episode net P&amp;L: open is the prior cumulative result; high and low are the best and worst levels reached inside the bucket; close is its final level. Choose 30 minutes, 1 hour, 1 day, or 1 month. Buckets use New York time and empty buckets are omitted. This is realized trading performance, not account equity or open-position P&amp;L.</p></article><article><ShieldCheck size={17} /><strong>Drawdown and R</strong><p>Drawdown measures peak-to-trough closed P&amp;L decline. R-multiple divides net P&amp;L by the risk planned before entry and is unavailable when no plan was recorded.</p></article><article><Target size={17} /><strong>MAE and MFE</strong><p>Maximum adverse and favorable excursion describe the worst and best open-trade path. Coverage is shown because broker fills alone cannot reconstruct the entire price path.</p></article><article><BookOpen size={17} /><strong>Attribution</strong><p>Strategy reports require strategy ID and revision on the opening execution. Manual or older broker activity remains explicitly Unattributed instead of being guessed.</p></article></div></section></div>;
}

function TradingFreshness({ data }: { data: CanonicalTradingPreview }) {
  return <div className={`trading-freshness ${data.stale ? "is-stale" : "is-current"}`}><strong>{data.complete && !data.stale ? "Complete broker state" : data.stale ? "Stale or partial state" : "Snapshot assembling"}</strong><span>{data.provider.replaceAll("_", " ")} · {data.mode} · <MarketTime value={data.as_of} /></span>{data.stale_reason ? <em>{data.stale_reason}</em> : null}</div>;
}

function TradingMetric({ label, tone = "neutral", value }: { label: string; tone?: "neutral" | "negative" | "positive" | "primary"; value: string }) {
  return <div className={`trading-metric tone-${tone}`}><span>{label}</span><strong>{value}</strong></div>;
}

function signedMoney(value: unknown) { const number = Number(value || 0); return `${number > 0 ? "+" : ""}${money(number)}`; }
function numberTone(value: unknown): "negative" | "positive" | "neutral" { const number = Number(value || 0); return number > 0 ? "positive" : number < 0 ? "negative" : "neutral"; }

export function StrategyOrderEntry({ marketSnapshot, runtimeMode, strategy, symbol, trading }: { marketSnapshot?: Record<string, unknown> | null; runtimeMode?: string; strategy?: CanvasPreview["strategy"]; symbol: string; trading?: CanonicalTradingPreview }) {
  const initialAssignment = strategy?.assignment ?? null;
  const [assignment, setAssignment] = useState<PreviewRow | null>(initialAssignment);
  const [accountId, setAccountId] = useState(String(initialAssignment?.account_id || trading?.accounts[0]?.alias || trading?.accounts[0]?.account_id || ""));
  const linkedPosition = trading?.positions.find((row) => String(nestedValue(row, "instrument", "symbol") || row.ticker || "").toUpperCase() === symbol);
  const [conid, setConid] = useState(String(initialAssignment?.conid || nestedValue(linkedPosition ?? {}, "instrument", "conid") || linkedPosition?.conid || ""));
  const [mode, setMode] = useState<"manage" | "request" | "automatic">("request");
  const [reenter, setReenter] = useState(Boolean((initialAssignment?.permissions as PreviewRow | undefined)?.reenter));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [proposalAuthority, setProposalAuthority] = useState<"manual" | "semi_automatic">("manual");
  const [proposalQuantity, setProposalQuantity] = useState(1);
  const [proposalAction, setProposalAction] = useState("position.enter_long");
  const [proposalStop, setProposalStop] = useState("");
  const [proposalTarget, setProposalTarget] = useState("");
  const actionDefinitions = strategy?.action_definitions ?? [];
  const configuredPolicies = strategy?.action_policies ?? [];
  const intentActions = actionDefinitions.filter((action) => action.kind === "intent");
  const campaignActions = actionDefinitions.filter((action) => action.kind === "campaign_command");
  const readOnlyBacktest = strategy?.runtime_mode === "backtest" || strategy?.runtime_mode === "backtest_debug";
  const runId = strategy?.run_id || "";
  const interactiveReplay = Boolean(runId && !readOnlyBacktest);
  const interactiveLive = runtimeMode === "live" || runtimeMode === "paper";

  useEffect(() => {
    setAssignment(strategy?.assignment ?? null);
  }, [strategy?.assignment]);

  useEffect(() => {
    if (intentActions.some((action) => action.action_id === proposalAction)) return;
    setProposalAction(intentActions.find((action) => action.category === "enter")?.action_id ?? intentActions[0]?.action_id ?? "position.enter_long");
  }, [intentActions, proposalAction]);

  async function createAssignment() {
    if (readOnlyBacktest) {
      setMessage("Backtest assignments are pinned by the Run Plan and are read-only in Canvas.");
      return;
    }
    if (!accountId.trim() || !Number(conid)) {
      setMessage("Account and IBKR conid are required.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const assignmentEndpoint = interactiveReplay
        ? `/api/trading/replay/runs/${encodeURIComponent(runId)}/assignments`
        : "/api/trading/strategy-assignments";
      const created = await api<PreviewRow>(assignmentEndpoint, {
        body: JSON.stringify({
          account_id: accountId.trim(),
          conid: Number(conid),
          permissions: {
            add: true,
            enter: mode !== "manage",
            exit: true,
            observe: true,
            reduce: true,
            reenter: mode !== "manage" && reenter,
          },
          source: "canvas_order_entry",
          strategy_id: strategy?.strategy_id || "long-momentum-campaign",
          strategy_revision: strategy?.revision || 1,
          ticker: symbol,
        }),
        method: "POST",
      });
      setAssignment(created);
      setMessage(mode === "manage" ? "Management will attach after a confirmed fill." : "Strategy assignment armed.");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function command(commandName: string) {
    if (readOnlyBacktest) {
      setMessage("Backtest state is immutable inspection evidence; rerun with a new approved configuration to change it.");
      return;
    }
    const assignmentId = String(assignment?.assignment_id || "");
    if (!assignmentId) return;
    setBusy(true);
    setMessage("");
    try {
      const commandEndpoint = interactiveReplay
        ? `/api/trading/replay/runs/${encodeURIComponent(runId)}/assignments/${encodeURIComponent(assignmentId)}/commands`
        : `/api/trading/strategy-assignments/${encodeURIComponent(assignmentId)}/commands`;
      const updated = await api<PreviewRow>(commandEndpoint, {
        body: JSON.stringify({ command: commandName }),
        method: "POST",
      });
      setAssignment(updated);
      setMessage(commandName.replaceAll("_", " "));
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitTradeProposal() {
    if (!interactiveReplay && !interactiveLive) {
      setMessage(readOnlyBacktest ? "Backtest proposals are immutable run evidence and cannot be submitted after the fact." : "Trade proposals require a Replay, Paper, or Live runtime workspace.");
      return;
    }
    if (!accountId.trim() || !Number(conid) || !marketSnapshot || marketSnapshot.freshness !== "ready") {
      setMessage("A simulated account, point-in-time conid, and ready chart snapshot are required.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const proposalEndpoint = interactiveReplay
        ? `/api/trading/replay/runs/${encodeURIComponent(runId)}/trade-proposals`
        : `/api/trading/${runtimeMode}/trade-proposals`;
      const result = await api<{ decision?: { status: string }; proposal_id: string; status?: string }>(proposalEndpoint, {
        body: JSON.stringify({
          account_id: accountId.trim(),
          action: actionDefinitions.find((action) => action.action_id === proposalAction)?.runtime_action ?? proposalAction.replace("position.", ""),
          action_id: proposalAction,
          authority: proposalAuthority,
          conid: Number(conid),
          invalidation_price: proposalStop ? Number(proposalStop) : null,
          market_snapshot: marketSnapshot,
          profit_target_price: proposalTarget ? Number(proposalTarget) : null,
          quantity: proposalQuantity,
          reason: "Confirmed from the Canvas chart order-entry panel",
          ticker: symbol,
        }),
        method: "POST",
      });
      setMessage(result.decision
        ? `Proposal ${result.proposal_id.slice(0, 8)} · Portfolio ${result.decision.status}`
        : `Proposal ${result.proposal_id.slice(0, 8)} · ${String(result.status || "validated").replaceAll("_", " ")}`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const status = String(assignment?.status || "not assigned");
  return <section className="strategy-order-entry">
    <header><span><strong>Order entry</strong><small>{strategy?.name || "Long Momentum Campaign"}</small></span><em data-state={status}>{status.replaceAll("_", " ")}</em></header>
    {configuredPolicies.length ? <div className="strategy-order-capabilities">
      <span>Action policies</span>
      {configuredPolicies.map((policy) => <div key={policy.policy_id}><strong>{policy.name}</strong><small>{policy.authority.replaceAll("_", " ")} · {actionDefinitions.find((action) => action.action_id === policy.action_id)?.name ?? policy.action_id}</small></div>)}
    </div> : null}
    {!assignment ? <>
      <label><span>Account</span><input onChange={(event) => setAccountId(event.target.value)} placeholder="IBKR account" value={accountId} /></label>
      <label><span>Conid</span><input inputMode="numeric" onChange={(event) => setConid(event.target.value.replace(/\D/g, ""))} placeholder="Contract ID" value={conid} /></label>
      <label><span>Authority</span><select onChange={(event) => setMode(event.target.value as typeof mode)} value={mode}><option value="request">Strategy entry</option><option value="manage">Manage after fill</option><option value="automatic">Fully automatic</option></select></label>
      <label className="strategy-order-check"><input checked={reenter} disabled={mode === "manage"} onChange={(event) => setReenter(event.target.checked)} type="checkbox" /><span>Allow re-entry</span></label>
      <button disabled={busy || readOnlyBacktest} onClick={createAssignment} type="button">{busy ? "Saving…" : readOnlyBacktest ? "Pinned by Run Plan" : mode === "manage" ? "Attach plan" : "Arm strategy"}</button>
    </> : <>
      <div className="strategy-order-summary"><span><small>Symbol</small><strong>{symbol}</strong></span><span><small>Account</small><strong>{String(assignment.account_id)}</strong></span></div>
      <div className="strategy-order-actions">
        {campaignActions.filter((action) => action.runtime_action !== "resume" || status === "paused").map((action) => <button className={action.runtime_action === "disable_after_exit" ? "danger" : undefined} disabled={busy || readOnlyBacktest || (status === "paused" && !["resume", "disable_after_exit"].includes(action.runtime_action))} key={action.action_id} onClick={() => command(action.runtime_action)} title={action.description} type="button">{action.name}</button>)}
      </div>
      <div className="strategy-order-proposal">
        <span><strong>Chart trade proposal</strong><small>Snapshot is revalidated by the run, then Portfolio and OMS retain exclusive authority.</small></span>
        <label><span>Trading Action</span><select onChange={(event) => setProposalAction(event.target.value)} value={proposalAction}>{intentActions.map((action) => <option key={action.action_id} value={action.action_id}>{action.name}</option>)}</select></label>
        <label><span>Authority</span><select onChange={(event) => setProposalAuthority(event.target.value as typeof proposalAuthority)} value={proposalAuthority}><option value="manual">Manual confirm</option><option value="semi_automatic">Semi-automatic</option></select></label>
        <label><span>Quantity</span><input min={1} onChange={(event) => setProposalQuantity(Math.max(1, Number(event.target.value) || 1))} type="number" value={proposalQuantity} /></label>
        <label><span>Stop price</span><input min={0.01} onChange={(event) => setProposalStop(event.target.value)} placeholder="Optional" step="0.01" type="number" value={proposalStop} /></label>
        <label><span>Target price</span><input min={0.01} onChange={(event) => setProposalTarget(event.target.value)} placeholder="Optional" step="0.01" type="number" value={proposalTarget} /></label>
        <button disabled={busy || (!interactiveReplay && !interactiveLive) || !marketSnapshot || marketSnapshot.freshness !== "ready"} onClick={submitTradeProposal} type="button">Confirm proposal</button>
      </div>
      <small className="strategy-order-disclosure">Commands are persisted here. Orders are placed only by the shared runtime after causal evaluation and risk validation.</small>
    </>}
    {message ? <p role="status">{message}</p> : null}
  </section>;
}

function StrategyPreview({ data, showSignals }: { data: CanvasPreview["strategy"]; showSignals: boolean }) {
  const config = data.definition?.config;
  const parameters = flattenStrategyParameters(config?.parameters ?? {});
  const searchSpace = flattenStrategyParameters(config?.parameter_space ?? {});
  const inputs = [...(data.taxonomy?.indicators ?? []).map((row) => ({ ...row, input_kind: "Indicator" })), ...(data.taxonomy?.signals ?? []).map((row) => ({ ...row, input_kind: "Signal" }))];
  return <div className="canvas-strategy-preview">
    <header className="strategy-definition-header"><div><span>Strategy definition</span><strong>{data.name || data.definition?.name || data.strategy_id}</strong><small>{config?.direction === "long_only" ? "Long only" : String(config?.direction || "")} · immutable v{data.revision}</small></div><em data-state={data.state}>{data.state.replaceAll("_", " ")}</em></header>
    <section className="strategy-definition-section"><header><strong>Evidence contract</strong><span>Each input keeps its own timeframe, role, freshness, score, and confidence requirements.</span></header><PreviewTable columns={["input_kind", "key", "timeframe", "role", "maximum_age_ms", "weight", "minimum_score", "minimum_confidence"]} rows={inputs} /></section>
    <section className="strategy-definition-grid"><div><header><strong>Resolved revision</strong><span>Exact values used by replay and live</span></header><PreviewTable columns={["parameter", "value"]} rows={parameters} /></div><div><header><strong>Hyperparameter space</strong><span>Candidate values; never passed unresolved to live execution</span></header><PreviewTable columns={["parameter", "value"]} rows={searchSpace} /></div></section>
    {showSignals ? <section className="strategy-definition-section"><header><strong>Saved decisions</strong><span>Only durable records at or before the Canvas clock are drawn on charts.</span></header><PreviewTable columns={["effective_at", "ticker", "action", "reason", "score", "confidence", "reference_price", "invalidation_price"]} rows={data.signals} /></section> : null}
    <section className="strategy-definition-section"><header><strong>Order management</strong><span>Durable broker commands, policy decisions, state transitions, and measured local submission latency.</span></header><PreviewTable columns={["event_time", "state", "event", "action", "entity_type", "decision_to_submit_ms", "message_ids", "confirmed", "rejection_reason"]} rows={data.order_management ?? []} /></section>
  </div>;
}

function flattenStrategyParameters(value: Record<string, unknown>, prefix = ""): PreviewRow[] {
  return Object.entries(value).flatMap(([key, item]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === "object" && !Array.isArray(item)) return flattenStrategyParameters(item as Record<string, unknown>, path);
    return [{ parameter: path, value: Array.isArray(item) ? item.join(", ") : String(item ?? "—") }];
  });
}
