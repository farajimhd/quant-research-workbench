export type NewsPollHistoryRow = {
  checkedAt: string;
  duplicateRows: number;
  failedRows: number;
  pollAt: string;
  pollRun: number;
  processedRows: number;
  providerRows: number;
  signature: string;
  skippedExisting: number;
  status: string;
  uniqueRows: number;
  wallSeconds: number;
  writtenRows: number;
};

export type NewsPublishHistoryRow = {
  activeJobs: number;
  canonicalNewsId: string;
  coverageMode: string;
  enrichment: string;
  event: string;
  insertedRows: number;
  pendingRows: number;
  pollId: string;
  providerArticleId: string;
  publishedAt: string;
  processedRows: number;
  qualityFlags: string[];
  skippedRows: number;
  status: string;
  tickerRows: number;
  tickers: string;
  title: string;
  time: string;
  wallSeconds?: number;
};

export type NewsEnrichmentArticleRow = {
  canonicalNewsId: string;
  domainSample: string[];
  externalFetchStatus: string;
  hasPdf: boolean;
  preEnrichedRow: Record<string, unknown>;
  providerArticleId: string;
  providerPayload: Record<string, unknown>;
  publishedAt: string;
  requiresEnrichment: boolean;
  tickers: string;
  title: string;
  urlCount: number;
  urlResolution: Record<string, unknown>;
  urlSample: string[];
};

export type NewsEnrichmentHistoryRow = {
  articleCount: number;
  detail: string;
  domainSample: string[];
  enrichedUrls: number;
  event: string;
  failedArticles: number;
  fetchTasks: number;
  mode: string;
  pollId: string;
  providerArticleId: string;
  queueSize: number;
  status: string;
  time: string;
  title: string;
  titleSample: string[];
  items: NewsEnrichmentArticleRow[];
  urlSample: string[];
  wallSeconds: number;
  worker: string;
};

export type NewsCoverageHistoryRow = {
  chunkCount: number;
  coverageId: string;
  detail: string;
  endUtc: string;
  event: string;
  gapCount: number;
  inFlight: number;
  progress: string;
  rows: number;
  script: string;
  stage: string;
  startUtc: string;
  status: string;
  time: string;
  totalChunks: number;
  window: string;
};
