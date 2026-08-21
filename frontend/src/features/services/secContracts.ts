export type SecTodaySort = "asc" | "desc";

export type SecDailyHistogramDatum = {
  bucketUtc: string;
  documentRows: number;
  filingOnlyRows: number;
  textRows: number;
  totalRows: number;
  xbrlRows: number;
};

export type SecDailyHistogramState = {
  binSeconds: number;
  error: string;
  rows: SecDailyHistogramDatum[];
  windowEndUtc: string;
  windowStartUtc: string;
};

export type SecLiveFeedRow = {
  accession: string;
  cik: string;
  company: string;
  documents: string;
  form: string;
  raw: Record<string, unknown>;
  status: string;
  time: string;
  timeMs?: number;
  title: string;
  xbrl: string;
};

export type SecTodayRow = {
  acceptedAtUtc: string;
  acceptanceDatetimeRaw: string;
  accessionNumber: string;
  accessionNumberCompact: string;
  activityStatus: string;
  ambiguityStatusSample: string[];
  bridgeIdSample: string[];
  cik: string;
  companyName: string;
  documentIssueRows: number;
  documentRows: number;
  documentTextReadyRows: number;
  documentTypeSample: string[];
  exchangeCodeSample: string[];
  feedDocuments: number;
  feedSkips: number;
  feedStatus: string;
  feedTexts: number;
  feedTitle: string;
  feedUpdatedAtUtc: string;
  feedXbrlFacts: number;
  fileExtensionSample: string[];
  filingParentCik: string;
  filingDate: string;
  filingDetailUrl: string;
  filingId: string;
  filingSize: number;
  formType: string;
  identityBridgeCount: number;
  identityTickers: string[];
  items: string[];
  issuerId: string;
  issuerDomicileCountryCode: string;
  issuerEntityType: string;
  issuerIndustry: string;
  issuerIndustryGroup: string;
  issuerLegalName: string;
  issuerName: string;
  issuerSector: string;
  issuerSicCode: string;
  issuerSicDescription: string;
  issuerStateOfIncorporation: string;
  issuerStatus: string;
  issuerWebsiteUrl: string;
  listingIdSample: string[];
  listingStatusSample: string[];
  mappingStatusSample: string[];
  maxMappingConfidence: number;
  primaryCurrencyCode: string;
  primaryDocument: string;
  primaryDocumentRows: number;
  primaryDocumentUrl: string;
  primaryExchangeCode: string;
  primaryIbkrConid: string;
  primaryTicker: string;
  qualityFlagSample: string[];
  reportDate: string;
  securityAssetClass: string;
  securityIdSample: string[];
  securityInstrumentType: string;
  securityName: string;
  securityProductType: string;
  securityStatus: string;
  securityType: string;
  sourceFileName: string;
  symbolIdSample: string[];
  symbolSourceSample: string[];
  rowOrigin: string;
  textChars: number;
  textKindSample: string[];
  textRows: number;
  textStatus: string;
  xbrlFactRows: number;
  xbrlFactTagSample: string[];
  xbrlFactTags: number;
  xbrlFrameRows: number;
  xbrlFrameTagSample: string[];
  xbrlFrameTags: number;
};

export type SecTodayRowsPayload = {
  database?: string;
  document_table?: string;
  filing_table?: string;
  histogram?: {
    bin_seconds?: number;
    error?: string;
    rows?: Array<{
      bucket_utc?: string;
      document_rows?: number;
      filing_only_rows?: number;
      text_rows?: number;
      total_rows?: number;
      xbrl_rows?: number;
    }>;
    window_end_utc?: string;
    window_start_utc?: string;
  };
  limit?: number;
  rows?: Array<Record<string, unknown>>;
  sort?: string;
  summary?: Record<string, unknown>;
  text_table?: string;
  company_fact_table?: string;
  frame_table?: string;
  window_end_utc?: string;
  window_start_utc?: string;
};

export type SecTodayRowsState = {
  error: string;
  histogram: SecDailyHistogramState;
  loading: boolean;
  rows: SecTodayRow[];
  sort: SecTodaySort;
  summary: SecTodaySummary;
  windowEndUtc: string;
  windowStartUtc: string;
};

export type SecTodaySummary = {
  documentRows: number;
  feedParticipantRows: number;
  feedRecentError: string;
  feedRecentRows: number;
  latest: string;
  loadedRows: number;
  textRows: number;
  totalFilings: number;
  withDocuments: number;
  withText: number;
  withXbrl: number;
  xbrlFactRows: number;
  xbrlFrameRows: number;
};

export type SecDetailPayload = {
  accession_number?: string;
  cik?: string;
  company_fact_rows?: Array<Record<string, unknown>>;
  company_fact_table?: string;
  database?: string;
  detail_errors?: Array<Record<string, unknown>>;
  document_rows?: Array<Record<string, unknown>>;
  document_table?: string;
  filing_row?: Record<string, unknown>;
  filing_table?: string;
  frame_rows?: Array<Record<string, unknown>>;
  frame_table?: string;
  identity_rows?: Array<Record<string, unknown>>;
  identity_summary?: Record<string, unknown>;
  text_rows?: Array<Record<string, unknown>>;
  text_table?: string;
};
