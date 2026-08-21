import type { ServiceId } from "../../app/routes";
import type { ServiceResponsibilitySpec } from "./serviceWorkContracts";

export function serviceResponsibilitySpecs(serviceId: ServiceId): ServiceResponsibilitySpec[] {
  const common = {
    other: {
      description: "Additional reported work that does not map cleanly to a primary responsibility.",
      id: "other",
      match: [/./],
      title: "Other Reported Work",
    },
  } satisfies Record<string, ServiceResponsibilitySpec>;

  const specs: Record<ServiceId, ServiceResponsibilitySpec[]> = {
    "text-intelligence": [
      {
        description: "Live-session gating, ticker and price eligibility, canonical-news deduplication, and bounded semantic work queues.",
        id: "routing",
        match: [/session|market|eligible|filter|ticker|price|route|queue|dedup/],
        title: "Live Eligibility And Routing",
      },
      {
        description: "Fast structured semantic labeling with the audited taxonomy, strict output validation, and evidence preservation.",
        id: "semantics",
        match: [/semantic|label|taxonomy|model|prompt|validat|evidence|sentiment|classif/],
        title: "Semantic Labeling",
      },
      {
        description: "Durable label persistence, canonical-news reconciliation, retry recovery, and downstream News Hypothesis dispatch.",
        id: "persistence",
        match: [/persist|database|clickhouse|reconcile|retry|failed|canonical|dispatch|recover/],
        title: "Persistence And Recovery",
      },
      common.other,
    ],
    "model-gateway": [
      {
        description: "Named inference routes, provider selection, local-to-remote failover, retries, and circuit-breaker state.",
        id: "routing",
        match: [/route|provider|failover|retry|circuit|endpoint|model/],
        title: "Routing And Provider Health",
      },
      {
        description: "Schema enforcement, idempotency, concurrency limits, timeouts, and per-route cost budgets.",
        id: "guardrails",
        match: [/schema|budget|cost|idempoten|cache|concurr|timeout|guard|limit/],
        title: "Inference Guardrails",
      },
      {
        description: "Request audit metadata, provider usage, token counts, cost, and latency without retaining source prompts.",
        id: "audit",
        match: [/audit|usage|token|latency|request|response|status|error/],
        title: "Usage And Audit",
      },
      common.other,
    ],
    "news-hypothesis": [
      {
        description: "Frozen point-in-time QMD, SEC, fundamental, and market context assembled for each labeled news event.",
        id: "context",
        match: [/qmd|sec|fundamental|market|context|frozen|as.?of|snapshot/],
        title: "Point-In-Time Context",
      },
      {
        description: "Deep structured trade hypotheses with directional probabilities, expected return, excursions, uncertainty, and abstention.",
        id: "hypothesis",
        match: [/hypothesis|probabil|return|excursion|uncertainty|abstain|forecast|evidence|conflict/],
        title: "Contextual Hypotheses",
      },
      {
        description: "Bounded processing, durable hypothesis persistence, expiry, reconciliation, and failed-work recovery.",
        id: "persistence",
        match: [/queue|reconcile|persist|database|clickhouse|expire|failed|retry|recover/],
        title: "Persistence And Recovery",
      },
      common.other,
    ],
    "bar-gpt": [
      {
        description: "Checkpoint identity, device placement, context contracts, and full-prefix causal inference authority.",
        id: "models",
        match: [/model|checkpoint|device|dtype|context|contract|full.?prefix/],
        title: "Models And Contracts",
      },
      {
        description: "Mode- and run-scoped watchlists, bounded warm-up, causal ring buffers, queue depth, and GPU batching.",
        id: "serving",
        match: [/scope|watchlist|warm|cache|queue|batch|ticker|memory/],
        title: "Serving Runtime",
      },
      {
        description: "QMD ingestion, backend field publication, raw heads, decoded forecasts, and explicit failures.",
        id: "publication",
        match: [/qmd|backend|prediction|head|field|publish|failed|error/],
        title: "Data And Publication",
      },
      common.other,
    ],
    news: [
      {
        description: "Benzinga polling cadence, raw item intake, duplicate handling, and live news memory updates.",
        id: "live",
        match: [/poll|benzinga provider|provider rows|raw|duplicate|skip|live|latest/],
        title: "Live Benzinga Update",
      },
      {
        description: "Database publishing for normalized rows, ticker links, coverage rows, and runtime logs.",
        id: "publish",
        match: [/publish|publisher|insert|write|database|table|sink|clickhouse|persist/],
        title: "Database Publishing",
      },
      {
        description: "URL handling, external text/PDF enrichment, canonicalization, ticker links, and quality flags.",
        id: "processing",
        match: [/background|enrich|canonical|normaliz|url|pdf|extract|text|ticker|quality|process|article/],
        title: "Enrichment And Canonical Rows",
      },
      {
        description: "Coverage bootstrap, gap detection, gap fill, and historical catch-up for Benzinga news.",
        id: "coverage",
        match: [/coverage|manifest|gap|backfill|catch.?up|initial|bootstrap|historical/],
        title: "Coverage, Gap Fill, Backfill",
      },
      common.other,
    ],
    sec: [
      {
        description: "SEC current feed polling, rate-limit aware retries, filing discovery, and duplicate suppression.",
        id: "live",
        match: [/poll|feed|rss|current|live|filing|accession|duplicate|skip|sec/],
        title: "Live SEC Feed Update",
      },
      {
        description: "SEC coverage manifest, current-day gaps, historical archive backfill, and bulk catch-up state.",
        id: "coverage",
        match: [/coverage|manifest|gap|backfill|catch.?up|archive|bulk|submissions|companyfacts|initial|historical/],
        title: "Coverage, Gap Fill, Backfill",
      },
      {
        description: "Filing text extraction, document parsing, XBRL companyfacts/frames, and canonical filing rows.",
        id: "processing",
        match: [/xbrl|companyfact|frame|document|filing text|parse|extract|text|normaliz|canonical|process/],
        title: "Filing Text And XBRL Processing",
      },
      {
        description: "Database writes, audit checks, integrity warnings, and repair status for SEC tables.",
        id: "publish",
        match: [/publish|insert|write|database|table|audit|integrity|repair|orphan|persist/],
        title: "Database Publishing And Audit",
      },
      common.other,
    ],
    qmd: [
      {
        description: "Massive websocket subscriptions, trade/quote event intake, connection health, and live stream state.",
        id: "live",
        match: [/websocket|subscription|ingest|trade|quote|event|connection|disconnect|massive|live|luld/],
        title: "Live Market Event Ingest",
      },
      {
        description: "Recent q_live coverage, REST repair, current-session head/tail fill, and three-market-day gap repair.",
        id: "gap_fill",
        match: [/coverage|manifest|gap|repair|backfill|rest|recent|q_live|head|tail|maintenance/],
        title: "Recent Live Gap Repair",
      },
      {
        description: "Streaming bars, scanner state, market condition state, and downstream event publication.",
        id: "processing",
        match: [/bar|scanner|condition|halt|resume|state|publish|fanout|broadcast|compact/],
        title: "Bars, State, And Broadcast",
      },
      {
        description: "ClickHouse persistence for live market events and live bars, including writer queues and flush state.",
        id: "persist",
        match: [/clickhouse|persist|insert|write|database|table|writer|flush|sink/],
        title: "Database Persistence",
      },
      common.other,
    ],
    "qmd-history": [
      {
        description: "Read-only ClickHouse event-window queries, canonical decoding, and deterministic stream ordering.",
        id: "historical_events",
        match: [/historical|event|compact|clickhouse|query|window|stream|order|ordinal|timestamp/],
        title: "Historical Event Serving",
      },
      {
        description: "Event-derived bars calculated through the same shared Rust QMD core used by the live gateway.",
        id: "bars",
        match: [/bar|timeframe|indicator|enriched|aggregate|qmd_core|decoder/],
        title: "Event-Derived Bars",
      },
      {
        description: "Reference-token validation, source preflight, request limits, and read-only service health.",
        id: "integrity",
        match: [/health|preflight|reference|condition|tape|limit|config|ready|error/],
        title: "Source Integrity And Readiness",
      },
      common.other,
    ],
    reference: [
      {
        description: "Low-frequency provider sync for Massive, IBKR, FINRA, SEC-derived mappings, presentation assets, and publications.",
        id: "source_sync",
        match: [/source|sync|massive|ibkr|finra|sec|ticker|listing|issuer|exchange|asset|borrow|short|split|dividend|ipo/],
        title: "Reference Source Sync",
      },
      {
        description: "Integrity audit, issue detection, deterministic resolution, tradability blocking, and human-review queues.",
        id: "integrity",
        match: [/audit|issue|resolve|resolution|tradable|block|guard|integrity|warning|error|review/],
        title: "Integrity And Issue Resolution",
      },
      {
        description: "Derived scanner/tradability publications, alerts, and reference facts maintained from canonical source tables.",
        id: "publication",
        match: [/publication|publish|fact|alert|scanner|snapshot|view|bridge|sec_market_bridge/],
        title: "Publications, Facts, Alerts",
      },
      {
        description: "After-hours maintenance, schema checks, rebuilds, historical gap fill, and source-specific repair work.",
        id: "maintenance",
        match: [/maintenance|gap|backfill|historical|rebuild|schema|policy|after.?hours|repair/],
        title: "Maintenance And Gap Fill",
      },
      common.other,
    ],
    "text-embed": [
      {
        description: "Source coverage checks, lookback windows, pending text discovery, and historical gap scan.",
        id: "coverage",
        match: [/coverage|gap|lookback|source|scan|pending|historical|backfill|manifest/],
        title: "Source Coverage And Gap Scan",
      },
      {
        description: "Text extraction, chunking, tokenization, queue depth, batching, and model input preparation.",
        id: "processing",
        match: [/extract|chunk|token|queue|batch|pending|text|prepare|process/],
        title: "Extraction And Tokenization",
      },
      {
        description: "Embedding inference, vector writes, publication state, and downstream table persistence.",
        id: "embedding",
        match: [/embed|embedding|vector|model|gpu|vllm|inference|write|publish|insert|database|table/],
        title: "Embedding Inference And Writes",
      },
      {
        description: "Retry handling, stale work recovery, audit state, and failed-row repair.",
        id: "recovery",
        match: [/retry|error|failure|failed|repair|audit|warning|stale|recover/],
        title: "Recovery And Audit",
      },
      common.other,
    ],
    ibkr: [
      {
        description: "Client Portal authentication, brokerage session health, account discovery, and API reachability.",
        id: "session",
        match: [/auth|session|client portal|iserver|account|portfolio|broker|gateway|login|connected/],
        title: "Broker Session And Accounts",
      },
      {
        description: "Keepalive tickles, websocket or endpoint health, reconnect handling, and active failure recovery.",
        id: "connectivity",
        match: [/keepalive|tickle|connection|connect|disconnect|health|recover|retry|heartbeat/],
        title: "Connectivity And Recovery",
      },
      {
        description: "Contract lookup, conid validation, account routing readiness, and order-path guardrails.",
        id: "routing",
        match: [/contract|conid|route|routing|order|account|security|stock|secdef/],
        title: "Contract And Routing Readiness",
      },
      common.other,
    ],
  };
  return specs[serviceId];
}
