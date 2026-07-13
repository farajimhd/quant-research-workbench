export type TradingWorkspaceMode = "live" | "paper" | "replay" | "backtest" | "backtest_debug";

export type WorkspaceContainerId =
  | "chart"
  | "scanner"
  | "strategy"
  | "portfolio"
  | "orders"
  | "fills"
  | "news"
  | "sec"
  | "xbrl"
  | "journal";

export type WorkspaceSourceLayer = {
  authority: string;
  description: string;
  id: string;
  label: string;
  timeBasis: "exchange-clock" | "point-in-time" | "run-clock";
  updateModel: "event-stream" | "poll" | "request" | "runtime-events";
};

export type WorkspaceSourceBinding = {
  layers: WorkspaceSourceLayer[];
  policy: "historical" | "hybrid" | "live" | "runtime";
  summary: string;
};

export type WorkspaceContainerDefinition = {
  defaultOpen: Partial<Record<TradingWorkspaceMode, boolean>>;
  description: string;
  id: WorkspaceContainerId;
  modes: TradingWorkspaceMode[];
  sourceByMode: Partial<Record<TradingWorkspaceMode, WorkspaceSourceBinding>>;
  title: string;
};

const qmdLive: WorkspaceSourceLayer = {
  authority: "services/qmd-gateway",
  description: "Live canonical quotes, trades, event-derived bars, indicators, and scanner state.",
  id: "qmd-live",
  label: "QMD Live",
  timeBasis: "exchange-clock",
  updateModel: "event-stream",
};

const qmdHistory: WorkspaceSourceLayer = {
  authority: "services/qmd_history_gateway",
  description: "Read-only canonical historical events and event-derived bars from the shared Rust QMD core.",
  id: "qmd-history",
  label: "QMD History",
  timeBasis: "run-clock",
  updateModel: "event-stream",
};

const newsLive: WorkspaceSourceLayer = {
  authority: "services/news_gateway",
  description: "Latest normalized news delivered by the live news gateway.",
  id: "news-live",
  label: "News Gateway",
  timeBasis: "exchange-clock",
  updateModel: "poll",
};

const newsHistory: WorkspaceSourceLayer = {
  authority: "q_live.benzinga_news_normalized_v1",
  description: "Persisted news filtered to the active symbol and the workspace clock.",
  id: "news-history",
  label: "News History",
  timeBasis: "point-in-time",
  updateModel: "request",
};

const secLive: WorkspaceSourceLayer = {
  authority: "services/sec_gateway",
  description: "New filing and filing-processing state from the SEC gateway.",
  id: "sec-live",
  label: "SEC Gateway",
  timeBasis: "exchange-clock",
  updateModel: "poll",
};

const secHistory: WorkspaceSourceLayer = {
  authority: "q_live.sec_filing_v3",
  description: "Persisted filings filtered by accepted time and point-in-time security identity.",
  id: "sec-history",
  label: "SEC History",
  timeBasis: "point-in-time",
  updateModel: "request",
};

const xbrlHistory: WorkspaceSourceLayer = {
  authority: "q_live.sec_xbrl_company_fact_v3",
  description: "Persisted company facts and frames constrained to information available at the workspace clock.",
  id: "xbrl-history",
  label: "XBRL History",
  timeBasis: "point-in-time",
  updateModel: "request",
};

const liveBroker: WorkspaceSourceLayer = {
  authority: "IBKR Client Portal API",
  description: "Broker-authoritative accounts, orders, executions, positions, ledger, and portfolio state.",
  id: "ibkr-live",
  label: "IBKR",
  timeBasis: "exchange-clock",
  updateModel: "poll",
};

const simulatedBroker: WorkspaceSourceLayer = {
  authority: "src/trading_runtime/simulated_broker.py",
  description: "Deterministic IBKR-shaped account, order, execution, position, and portfolio simulation.",
  id: "simulated-broker",
  label: "Simulated IBKR",
  timeBasis: "run-clock",
  updateModel: "runtime-events",
};

const strategyRuntime: WorkspaceSourceLayer = {
  authority: "src/trading_runtime/runtime.py",
  description: "Strategy revision, signals, decisions, risk validation, and runtime lifecycle.",
  id: "strategy-runtime",
  label: "Strategy Runtime",
  timeBasis: "run-clock",
  updateModel: "runtime-events",
};

const tradingJournal: WorkspaceSourceLayer = {
  authority: "q_live.tr_* / TradingJournal",
  description: "Crash-safe run journal, checkpoints, reconciliation, and durable audit records.",
  id: "trading-journal",
  label: "Trading Journal",
  timeBasis: "run-clock",
  updateModel: "runtime-events",
};

const historicalModes: TradingWorkspaceMode[] = ["replay", "backtest", "backtest_debug"];
const allModes: TradingWorkspaceMode[] = ["live", "paper", ...historicalModes];

const marketSourceByMode = sourceMap(
  liveBinding("Live event stream from QMD", [qmdLive]),
  historicalBinding("Historical event stream from QMD History", [qmdHistory]),
);

const brokerSourceByMode = sourceMap(
  liveBinding("Broker-authoritative IBKR state", [liveBroker]),
  runtimeBinding("Deterministic simulated broker state", [simulatedBroker]),
);

export const TRADING_WORKSPACE_CONTAINERS: readonly WorkspaceContainerDefinition[] = [
  {
    id: "chart",
    title: "Chart",
    description: "Event-derived price, quote, volume, indicator, and execution context for the active symbol.",
    modes: allModes,
    defaultOpen: { live: true, paper: true, replay: true, backtest_debug: true },
    sourceByMode: marketSourceByMode,
  },
  {
    id: "scanner",
    title: "Scanner",
    description: "Stable ranked universe and strategy candidates evaluated at the active workspace clock.",
    modes: allModes,
    defaultOpen: { live: true, paper: true, replay: true, backtest_debug: true },
    sourceByMode: {
      live: liveBinding("Live scanner state from QMD and strategy evaluation", [qmdLive, strategyRuntime]),
      paper: liveBinding("Live scanner state with paper execution", [qmdLive, strategyRuntime]),
      replay: runtimeBinding("Scanner state reconstructed from historical events", [qmdHistory, strategyRuntime]),
      backtest: runtimeBinding("Scanner decisions persisted by the backtest runtime", [qmdHistory, strategyRuntime]),
      backtest_debug: runtimeBinding("Scanner state at the debug event cursor", [qmdHistory, strategyRuntime]),
    },
  },
  {
    id: "strategy",
    title: "Strategy",
    description: "Selected immutable revision, decisions, risk checks, state, and control availability.",
    modes: allModes,
    defaultOpen: { backtest: true, backtest_debug: true },
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, runtimeBinding("Central strategy and risk authority", [strategyRuntime])])),
  },
  {
    id: "portfolio",
    title: "Portfolio",
    description: "Account summaries, cash, equity, P&L, positions, and exposure using IBKR-shaped resources.",
    modes: allModes,
    defaultOpen: { live: true, paper: true, replay: true, backtest: true, backtest_debug: true },
    sourceByMode: brokerSourceByMode,
  },
  {
    id: "orders",
    title: "Orders",
    description: "Order lifecycle, modifications, cancellations, parent/child relationships, and broker responses.",
    modes: allModes,
    defaultOpen: { replay: true, backtest: true, backtest_debug: true },
    sourceByMode: brokerSourceByMode,
  },
  {
    id: "fills",
    title: "Executions & Fills",
    description: "Partial and complete executions, commissions, liquidity evidence, and account attribution.",
    modes: allModes,
    defaultOpen: { backtest: true, backtest_debug: true },
    sourceByMode: brokerSourceByMode,
  },
  {
    id: "news",
    title: "News",
    description: "Symbol and market news ordered against the workspace clock with a configurable recent-item limit.",
    modes: allModes,
    defaultOpen: { live: true, paper: true, replay: true, backtest_debug: true },
    sourceByMode: {
      live: hybridBinding("Latest gateway news plus persisted recent history", [newsLive, newsHistory]),
      paper: hybridBinding("Latest gateway news plus persisted recent history", [newsLive, newsHistory]),
      replay: historicalBinding("Persisted news available at the replay clock", [newsHistory]),
      backtest: historicalBinding("Persisted news available at each backtest event time", [newsHistory]),
      backtest_debug: historicalBinding("Persisted news available at the debug cursor", [newsHistory]),
    },
  },
  {
    id: "sec",
    title: "SEC Filings",
    description: "Point-in-time filings, documents, and readable filing text for the active security.",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: {
      live: hybridBinding("Latest SEC processing plus persisted filing history", [secLive, secHistory]),
      paper: hybridBinding("Latest SEC processing plus persisted filing history", [secLive, secHistory]),
      replay: historicalBinding("Persisted filings accepted by the replay clock", [secHistory]),
      backtest: historicalBinding("Persisted filings accepted by each backtest event time", [secHistory]),
      backtest_debug: historicalBinding("Persisted filings accepted by the debug cursor", [secHistory]),
    },
  },
  {
    id: "xbrl",
    title: "XBRL Facts",
    description: "Company facts, frames, periods, units, and filing provenance constrained point in time.",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, historicalBinding("Point-in-time persisted XBRL facts", [xbrlHistory])])),
  },
  {
    id: "journal",
    title: "Run Journal",
    description: "Ordered lifecycle, command, signal, broker, execution, snapshot, and checkpoint evidence.",
    modes: allModes,
    defaultOpen: { backtest: true, backtest_debug: true },
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, runtimeBinding("Durable run audit trail", [tradingJournal])])),
  },
];

export function containersForMode(mode: TradingWorkspaceMode): WorkspaceContainerDefinition[] {
  return TRADING_WORKSPACE_CONTAINERS.filter((definition) => definition.modes.includes(mode));
}

export function defaultContainersForMode(mode: TradingWorkspaceMode): WorkspaceContainerId[] {
  return containersForMode(mode).filter((definition) => definition.defaultOpen[mode]).map((definition) => definition.id);
}

export function sourceBindingForContainer(definition: WorkspaceContainerDefinition, mode: TradingWorkspaceMode): WorkspaceSourceBinding {
  const binding = definition.sourceByMode[mode];
  if (!binding) throw new Error(`Container '${definition.id}' has no source binding for mode '${mode}'.`);
  return binding;
}

function sourceMap(live: WorkspaceSourceBinding, historical: WorkspaceSourceBinding): Partial<Record<TradingWorkspaceMode, WorkspaceSourceBinding>> {
  return {
    live,
    paper: live,
    replay: historical,
    backtest: historical,
    backtest_debug: historical,
  };
}

function liveBinding(summary: string, layers: WorkspaceSourceLayer[]): WorkspaceSourceBinding {
  return { layers, policy: "live", summary };
}

function historicalBinding(summary: string, layers: WorkspaceSourceLayer[]): WorkspaceSourceBinding {
  return { layers, policy: "historical", summary };
}

function hybridBinding(summary: string, layers: WorkspaceSourceLayer[]): WorkspaceSourceBinding {
  return { layers, policy: "hybrid", summary };
}

function runtimeBinding(summary: string, layers: WorkspaceSourceLayer[]): WorkspaceSourceBinding {
  return { layers, policy: "runtime", summary };
}
