import { ChevronDown, ChevronUp } from "lucide-react";

import { DataTable } from "../../app/components/DataTable";
import { Tabs } from "../../app/components/Tabs";
import type { RealLivePortfolioPayload } from "./contracts";
import {
  buildProfitLossRows,
  portfolioBalanceRows,
  type OrderRow,
  type PositionRow,
  type TradeRow,
} from "./portfolio";
import { integer, money, percent } from "./liveTradingFormat";

type PortfolioContainerBaseProps = {
  detailsOpen: boolean;
  orders: OrderRow[];
  positions: PositionRow[];
  selectedTab: string;
  trades: TradeRow[];
  onTabChange: (tab: string) => void;
  onToggleDetails: () => void;
};

export type LivePortfolioContainerProps = PortfolioContainerBaseProps & (
  | { mode: "simulation"; portfolioSnapshot?: never }
  | { mode: "broker"; portfolioSnapshot: RealLivePortfolioPayload | null }
);

export function LivePortfolioContainer(props: LivePortfolioContainerProps) {
  const {
    detailsOpen,
    mode,
    onTabChange,
    onToggleDetails,
    orders,
    positions,
    selectedTab,
    trades,
  } = props;
  const brokerMode = mode === "broker";
  const portfolioSnapshot = brokerMode ? props.portfolioSnapshot : null;
  const tabs = brokerMode ? ["P/L", "Fills", "Orders", "Balances", "Errors"] : ["Open Positions", "P/L", "Trades", "Orders"];
  const activeTab = tabs.includes(selectedTab) ? selectedTab : tabs[0];
  return (
    <div className={detailsOpen ? "live-container-stack portfolio-expanded" : "live-container-stack"}>
      <PortfolioPositions positions={positions} showAccountContext={brokerMode} />
      <button className="live-portfolio-expand-button" onClick={onToggleDetails} title={detailsOpen ? "Hide tabs" : "Show tabs"} type="button">
        {detailsOpen ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {detailsOpen ? (
        <>
          <Tabs tabs={tabs} active={activeTab} onChange={onTabChange} />
          {brokerMode ? (
            <BrokerPortfolioDetails
              activeTab={activeTab}
              orders={orders}
              portfolioSnapshot={portfolioSnapshot}
              positions={positions}
              trades={trades}
            />
          ) : (
            <SimulationPortfolioDetails activeTab={activeTab} orders={orders} positions={positions} trades={trades} />
          )}
        </>
      ) : null}
    </div>
  );
}

function PortfolioPositions({ positions, showAccountContext }: { positions: PositionRow[]; showAccountContext: boolean }) {
  return (
    <section className="live-portfolio-positions" aria-label="Open positions">
      <div className="live-portfolio-positions-header">
        <span>Open Positions</span>
        <strong>{positions.length}</strong>
      </div>
      {positions.length ? (
        <div className="live-portfolio-position-list">
          {positions.map((position) => {
            const pnlTone = position.unrealized_pnl >= 0 ? "positive" : "negative";
            const key = showAccountContext ? `${position.account_key || "account"}-${position.conid || position.symbol}` : position.symbol;
            return (
              <article className={`live-portfolio-position-card ${pnlTone}`} key={key}>
                <div className="live-portfolio-position-main">
                  <strong>{position.symbol}</strong>
                  <span>{showAccountContext && position.account_label ? `${position.account_label} - ` : ""}{integer(position.quantity)} sh</span>
                </div>
                <div>
                  <span>Avg</span>
                  <strong>{money(position.avg_price)}</strong>
                </div>
                <div>
                  <span>Mark</span>
                  <strong>{money(position.mark)}</strong>
                </div>
                <div className="live-position-current-pnl">
                  <span>P/L</span>
                  <strong>{money(position.unrealized_pnl)}</strong>
                  <small>{percent(position.unrealized_pnl_pct)}</small>
                </div>
                <div className="live-position-peak-pnl">
                  <span>Peak P/L</span>
                  <strong>{money(position.max_unrealized_pnl)}</strong>
                  <small>Observed lifecycle max</small>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="live-empty-positions">No open positions.</div>
      )}
    </section>
  );
}

function SimulationPortfolioDetails({
  activeTab,
  orders,
  positions,
  trades,
}: {
  activeTab: string;
  orders: OrderRow[];
  positions: PositionRow[];
  trades: TradeRow[];
}) {
  return (
    <>
      {activeTab === "Open Positions" ? <DataTable rows={positions} empty="No open positions." /> : null}
      {activeTab === "P/L" ? <DataTable rows={buildSimulationProfitLossRows(positions, trades)} empty="No P/L rows." /> : null}
      {activeTab === "Trades" ? <DataTable rows={trades} empty="No completed trades yet." /> : null}
      {activeTab === "Orders" ? <DataTable rows={orders} empty="No staged orders." /> : null}
    </>
  );
}

function BrokerPortfolioDetails({
  activeTab,
  orders,
  portfolioSnapshot,
  positions,
  trades,
}: {
  activeTab: string;
  orders: OrderRow[];
  portfolioSnapshot: RealLivePortfolioPayload | null;
  positions: PositionRow[];
  trades: TradeRow[];
}) {
  const balanceRows = portfolioBalanceRows(portfolioSnapshot);
  const errorRows = portfolioSnapshot?.errors ?? [];
  return (
    <>
      {activeTab === "P/L" ? <DataTable rows={buildProfitLossRows(positions, trades, portfolioSnapshot)} empty="No broker P/L rows." /> : null}
      {activeTab === "Fills" ? <DataTable rows={trades} empty="No broker executions yet." /> : null}
      {activeTab === "Orders" ? <DataTable rows={orders} empty="No live orders." /> : null}
      {activeTab === "Balances" ? <DataTable rows={balanceRows} empty="No broker balance rows." /> : null}
      {activeTab === "Errors" ? <DataTable rows={errorRows} empty="No broker portfolio errors." /> : null}
    </>
  );
}

function buildSimulationProfitLossRows(positions: PositionRow[], trades: TradeRow[]) {
  return [
    ...positions.map((row) => ({
      avg_price: row.avg_price,
      mark: row.mark,
      max_unrealized_pnl: row.max_unrealized_pnl,
      pnl: row.unrealized_pnl,
      pnl_pct: row.unrealized_pnl_pct,
      quantity: row.quantity,
      status: "OPEN",
      symbol: row.symbol,
    })),
    ...trades.map((row) => ({
      entry_price: row.entry_price,
      exit_price: row.exit_price,
      pnl: row.gross_pnl,
      pnl_pct: row.gross_pnl_pct,
      quantity: row.quantity,
      status: "CLOSED",
      symbol: row.symbol,
    })),
  ];
}
