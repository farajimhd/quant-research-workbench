export type TradingWorkspaceMode = "live" | "paper" | "replay" | "backtest" | "backtest_debug";

export type WorkspaceContainerId =
  | "chart"
  | "facts"
  | "microstructure"
  | "scanner"
  | "strategy"
  | "portfolio"
  | "positions"
  | "orders"
  | "fills"
  | "closed_trades"
  | "activity"
  | "news"
  | "ticker_news"
  | "news_detail"
  | "sec"
  | "ticker_sec"
  | "sec_detail"
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
  groupedTitle?: string;
  id: WorkspaceContainerId;
  linkScope?: "single-symbol";
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
  description: "Persisted company facts constrained by filing date and the active symbol at the workspace clock.",
  id: "xbrl-history",
  label: "XBRL History",
  timeBasis: "point-in-time",
  updateModel: "request",
};

const liveBroker: WorkspaceSourceLayer = {
  authority: "Canonical trading domain v2 / IBKR Client Portal adapter",
  description: "Lossless IBKR evidence normalized into broker-neutral account, order, execution, position, ledger, and portfolio contracts.",
  id: "ibkr-live",
  label: "IBKR",
  timeBasis: "exchange-clock",
  updateModel: "poll",
};

const referenceFacts: WorkspaceSourceLayer = {
  authority: "q_live canonical reference, market publication, and SEC fact tables",
  description: "Point-in-time issuer identity, listing, shares, short positioning, borrow, corporate actions, and reported fundamentals.",
  id: "reference-facts",
  label: "Reference Facts",
  timeBasis: "point-in-time",
  updateModel: "request",
};

const simulatedBroker: WorkspaceSourceLayer = {
  authority: "src/trading_runtime/domain.py / simulated_broker.py",
  description: "Deterministic broker events projected through the same canonical contracts used by live IBKR.",
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
    groupedTitle: "Price chart",
    description: "Event-derived price, quote, volume, indicator, and execution context for the active symbol.",
    linkScope: "single-symbol",
    modes: allModes,
    defaultOpen: { live: true, paper: true, replay: true, backtest_debug: true },
    sourceByMode: marketSourceByMode,
  },
  {
    id: "facts",
    title: "Stock Facts",
    groupedTitle: "Stock facts",
    description: "Auditable issuer, security, listing, capitalization, share supply, volume, short positioning, IBKR borrow, identifiers, corporate actions, and SEC-reported fundamentals for the linked symbol.",
    linkScope: "single-symbol",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, historicalBinding("Point-in-time stock facts from canonical reference authorities", [referenceFacts, mode === "live" || mode === "paper" ? qmdLive : qmdHistory])])),
  },
  {
    id: "microstructure",
    title: "Quotes & Tape",
    groupedTitle: "Quotes & tape",
    description: "One synchronized market-microstructure surface for consolidated NBBO liquidity, interpreted quote changes, time-and-sales prints, trade conditions, comparative charts, and the canonical QMD decision architecture.",
    linkScope: "single-symbol",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: marketSourceByMode,
  },
  {
    id: "scanner",
    title: "Scanner",
    groupedTitle: "Market scanner",
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
    groupedTitle: "Strategy decisions",
    description: "Selected immutable revision, decisions, risk checks, state, and control availability.",
    modes: allModes,
    defaultOpen: { backtest: true, backtest_debug: true },
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, runtimeBinding("Central strategy and risk authority", [strategyRuntime])])),
  },
  {
    id: "portfolio",
    title: "Portfolio",
    groupedTitle: "Portfolio",
    description: "Account capital, liquidity, margin capacity, P&L, exposure, ledger currencies, freshness, and reconciliation state.",
    modes: allModes,
    defaultOpen: { live: true, paper: true, replay: true, backtest: true, backtest_debug: true },
    sourceByMode: brokerSourceByMode,
  },
  {
    id: "positions",
    title: "Position Manager",
    groupedTitle: "Positions",
    description: "One position workflow for open inventory, linked working orders and fills, closed round trips, and lifecycle history, with chart-linked symbols and broker snapshot freshness.",
    modes: allModes,
    defaultOpen: { live: true, paper: true, replay: true, backtest: true, backtest_debug: true },
    sourceByMode: brokerSourceByMode,
  },
  {
    id: "orders",
    title: "Orders & Fills",
    groupedTitle: "Orders & fills",
    description: "Primary order workflow with working and historical states, broker status, fill progress, expandable execution evidence, and a consolidated fills view.",
    modes: allModes,
    defaultOpen: { live: true, paper: true, replay: true, backtest: true, backtest_debug: true },
    sourceByMode: brokerSourceByMode,
  },
  {
    id: "fills",
    title: "Execution Audit",
    groupedTitle: "Execution audit",
    description: "Advanced immutable fill evidence for reconciliation and debugging. Routine execution review lives inside Orders & Fills.",
    modes: allModes,
    defaultOpen: { backtest_debug: true },
    sourceByMode: brokerSourceByMode,
  },
  {
    id: "closed_trades",
    title: "Round-trip Audit",
    groupedTitle: "Round-trip audit",
    description: "Advanced FIFO-derived entry-to-exit evidence. Normal closed-position review lives in Position Manager and remains separate from IBKR tax lots.",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: brokerSourceByMode,
  },
  {
    id: "activity",
    title: "Trading Activity",
    groupedTitle: "Trading activity",
    description: "Correlated order-command, warning, status, execution, commission, position, account, and reconciliation evidence.",
    modes: allModes,
    defaultOpen: { backtest_debug: true },
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, runtimeBinding("Canonical immutable broker and runtime evidence", [mode === "live" || mode === "paper" ? liveBroker : simulatedBroker, tradingJournal])])),
  },
  {
    id: "news",
    title: "All News",
    groupedTitle: "Market news",
    description: "Searchable point-in-time news inventory with database-backed filters and article selection.",
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
    id: "ticker_news",
    title: "Ticker News",
    groupedTitle: "Ticker news",
    description: "Recent, hot, and developing news for the linked symbol at the workspace clock.",
    linkScope: "single-symbol",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: {
      live: hybridBinding("Linked-symbol news from the gateway and persisted history", [newsLive, newsHistory]),
      paper: hybridBinding("Linked-symbol news from the gateway and persisted history", [newsLive, newsHistory]),
      replay: historicalBinding("Linked-symbol news available at the replay clock", [newsHistory]),
      backtest: historicalBinding("Linked-symbol news available at each backtest event time", [newsHistory]),
      backtest_debug: historicalBinding("Linked-symbol news available at the debug cursor", [newsHistory]),
    },
  },
  {
    id: "news_detail",
    title: "News Detail",
    groupedTitle: "News article",
    description: "Readable article text, metadata, security links, and source provenance for the selected story.",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, historicalBinding("Canonical persisted news article", [newsHistory])])),
  },
  {
    id: "sec",
    title: "All SEC",
    groupedTitle: "SEC filings",
    description: "Searchable point-in-time filing inventory with form labels, content coverage, and filing selection.",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, historicalBinding("Persisted filings accepted by the workspace clock", [secHistory])])),
  },
  {
    id: "ticker_sec",
    title: "Ticker SEC",
    groupedTitle: "SEC filings",
    description: "Recent hot, cold, and older SEC disclosures for the linked symbol at the workspace clock.",
    linkScope: "single-symbol",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, historicalBinding("Persisted linked-symbol filings accepted by the workspace clock", [secHistory])])),
  },
  {
    id: "sec_detail",
    title: "SEC Detail",
    groupedTitle: "SEC filing",
    description: "Rendered and original filing documents, XBRL facts, entity relationships, provenance, and label evidence for the selected filing.",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, historicalBinding("Canonical persisted filing, rendered and original document text, and fact inventory", [secHistory, xbrlHistory])])),
  },
  {
    id: "xbrl",
    title: "XBRL Facts",
    groupedTitle: "XBRL facts",
    description: "Company facts, periods, units, and filing provenance for the linked symbol at the workspace clock.",
    linkScope: "single-symbol",
    modes: allModes,
    defaultOpen: {},
    sourceByMode: Object.fromEntries(allModes.map((mode) => [mode, historicalBinding("Point-in-time persisted XBRL facts", [xbrlHistory])])),
  },
  {
    id: "journal",
    title: "Run Journal",
    groupedTitle: "Run journal",
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

export function containerSupportsSymbolLink(containerId: WorkspaceContainerId): boolean {
  return TRADING_WORKSPACE_CONTAINERS.some((definition) => definition.id === containerId && definition.linkScope === "single-symbol");
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
