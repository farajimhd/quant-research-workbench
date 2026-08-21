export type NewsTodayRow = {
  articleUrl: string;
  author: string;
  bodyChars: number;
  canonicalNewsId: string;
  channels: string[];
  contentQualityFlags: string[];
  downloadedAtUtc: string;
  externalChars: number;
  externalFetchStatus: string;
  fullTextChars: number;
  hasBody: boolean;
  hasExternalText: boolean;
  hasPdf: boolean;
  isTitleOnly: boolean;
  normalizedTitle: string;
  pdfChars: number;
  pdfExtractStatus: string;
  providerArticleId: string;
  providerTags: string[];
  publishedAtUtc: string;
  textPreview: string;
  tickerLinkCount: number;
  tickerLinkSample: string[];
  tickers: string[];
  title: string;
  urlDomain: string;
};

export type NewsTodayRowsPayload = {
  database?: string;
  error?: string;
  limit?: number;
  normalized_table?: string;
  rows?: Array<Record<string, unknown>>;
  sort?: string;
  summary?: Record<string, unknown>;
  ticker_table?: string;
  window_end_utc?: string;
  window_start_utc?: string;
};

export type NewsDetailPayload = {
  canonical_news_id?: string;
  database?: string;
  normalized_table?: string;
  row?: Record<string, unknown>;
  ticker_rows?: Array<Record<string, unknown>>;
  ticker_table?: string;
};

export type NewsTodayRowsState = {
  error: string;
  loading: boolean;
  rows: NewsTodayRow[];
  sort: NewsTodaySort;
  summary: NewsTodaySummary;
  windowEndUtc: string;
  windowStartUtc: string;
};

export type NewsTodaySort = "asc" | "desc";

export type NewsTodaySummary = {
  externalText: number;
  latest: string;
  loadedRows: number;
  multiTickerRows: number;
  noTickerRows: number;
  oneTickerRows: number;
  pdfRows: number;
  totalRows: number;
  withTicker: number;
};
