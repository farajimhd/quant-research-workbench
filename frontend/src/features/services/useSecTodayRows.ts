import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { usePollingTask } from "../../app/hooks/usePollingTask";
import { defaultMarketDayHistogramWindow, elapsedHistogramRows } from "./histogramWindow";
import type {
  SecDailyHistogramDatum,
  SecDailyHistogramState,
  SecTodayRow,
  SecTodayRowsPayload,
  SecTodayRowsState,
  SecTodaySort,
  SecTodaySummary,
} from "./secContracts";
import { numericMetric, stringArrayMetric, stringMetric } from "./metrics";
import { parseServiceTimestamp } from "./time";
import { isRecord } from "./workPresentation";

export function useSecTodayRows(enabled: boolean, sort: SecTodaySort): SecTodayRowsState {
  const [payload, setPayload] = useState<SecTodayRowsState>(() => defaultSecTodayRowsState(sort));
  useEffect(() => {
    if (!enabled) setPayload(defaultSecTodayRowsState(sort));
  }, [enabled, sort]);
  usePollingTask({
    enabled,
    initialDelayMs: 0,
    intervalMs: 30_000,
    restartKey: sort,
    task: async (signal) => {
      setPayload((current) => ({ ...current, loading: true }));
      try {
        const response = await api<SecTodayRowsPayload>(`/api/services/sec/today?limit=5000&sort=${sort}`, { signal });
        const rows = (response.rows || []).filter(isRecord).map(secTodayRowFromPayload);
        setPayload({
          error: "",
          histogram: secHistogramFromPayload(response.histogram),
          loading: false,
          rows,
          sort: response.sort === "asc" ? "asc" : "desc",
          summary: secTodaySummaryFromPayload(response.summary, rows),
          windowEndUtc: response.window_end_utc || "",
          windowStartUtc: response.window_start_utc || "",
        });
      } catch (exc) {
        if (signal.aborted) return;
        setPayload((current) => ({ ...current, error: exc instanceof Error ? exc.message : String(exc), loading: false }));
      }
    },
  });
  return payload;
}

export function defaultSecTodayRowsState(sort: SecTodaySort): SecTodayRowsState {
  return {
    error: "",
    histogram: defaultSecHistogramWindow(900),
    loading: false,
    rows: [],
    sort,
    summary: {
      documentRows: 0,
      feedParticipantRows: 0,
      feedRecentError: "",
      feedRecentRows: 0,
      latest: "",
      loadedRows: 0,
      textRows: 0,
      totalFilings: 0,
      withDocuments: 0,
      withText: 0,
      withXbrl: 0,
      xbrlFactRows: 0,
      xbrlFrameRows: 0,
    },
    windowEndUtc: "",
    windowStartUtc: "",
  };
}

export function defaultSecHistogramWindow(binSeconds: number): SecDailyHistogramState {
  return defaultMarketDayHistogramWindow(binSeconds, emptySecHistogramRow);
}

export function elapsedSecHistogramRows(rows: SecDailyHistogramDatum[], windowStartUtc: string, windowEndUtc: string, binSeconds: number) {
  return elapsedHistogramRows(
    rows,
    windowStartUtc,
    windowEndUtc,
    binSeconds,
    (row) => row.totalRows > 0 || row.filingOnlyRows > 0 || row.documentRows > 0 || row.textRows > 0 || row.xbrlRows > 0,
  );
}

function emptySecHistogramRow(bucketUtc: string): SecDailyHistogramDatum {
  return { bucketUtc, documentRows: 0, filingOnlyRows: 0, textRows: 0, totalRows: 0, xbrlRows: 0 };
}

function secHistogramFromPayload(payload: SecTodayRowsPayload["histogram"]): SecDailyHistogramState {
  const binSeconds = Number(payload?.bin_seconds || 900);
  const defaultWindow = defaultSecHistogramWindow(binSeconds);
  const windowStartUtc = payload?.window_start_utc || defaultWindow.windowStartUtc;
  const windowEndUtc = payload?.window_end_utc || defaultWindow.windowEndUtc;
  return {
    binSeconds,
    error: String(payload?.error || ""),
    rows: elapsedSecHistogramRows(
      (payload?.rows || []).map((row) => ({
        bucketUtc: String(row.bucket_utc || ""),
        documentRows: Number(row.document_rows || 0),
        filingOnlyRows: Number(row.filing_only_rows || 0),
        textRows: Number(row.text_rows || 0),
        totalRows: Number(row.total_rows || 0),
        xbrlRows: Number(row.xbrl_rows || 0),
      })).filter((row) => row.bucketUtc),
      windowStartUtc,
      windowEndUtc,
      binSeconds,
    ),
    windowEndUtc,
    windowStartUtc,
  };
}

function secTodayRowFromPayload(row: Record<string, unknown>): SecTodayRow {
  const number = (keys: string[]) => numericMetric(row, keys);
  const string = (keys: string[]) => stringMetric(row, keys);
  const strings = (keys: string[]) => stringArrayMetric(row, keys);
  return {
    acceptedAtUtc: string(["accepted_at_utc"]), acceptanceDatetimeRaw: string(["acceptance_datetime_raw"]),
    accessionNumber: string(["accession_number"]), accessionNumberCompact: string(["accession_number_compact"]),
    activityStatus: string(["activity_status"]) || "filing", ambiguityStatusSample: strings(["ambiguity_status_sample"]),
    bridgeIdSample: strings(["bridge_id_sample"]), cik: string(["cik"]), companyName: string(["company_name"]),
    documentIssueRows: number(["document_issue_rows"]), documentRows: number(["document_rows"]),
    documentTextReadyRows: number(["document_text_ready_rows"]), documentTypeSample: strings(["document_type_sample"]),
    exchangeCodeSample: strings(["exchange_code_sample"]), feedDocuments: number(["feed_documents"]), feedSkips: number(["feed_skips"]),
    feedStatus: string(["feed_status"]), feedTexts: number(["feed_texts"]), feedTitle: string(["feed_title"]),
    feedUpdatedAtUtc: string(["feed_updated_at_utc"]), feedXbrlFacts: number(["feed_xbrl_facts"]), fileExtensionSample: strings(["file_extension_sample"]),
    filingParentCik: string(["filing_parent_cik"]), filingDate: string(["filing_date"]), filingDetailUrl: string(["filing_detail_url"]),
    filingId: string(["filing_id"]), filingSize: number(["filing_size"]), formType: string(["form_type"]),
    identityBridgeCount: number(["identity_bridge_count"]), identityTickers: strings(["identity_tickers"]), items: strings(["items"]),
    issuerId: string(["issuer_id"]), issuerDomicileCountryCode: string(["issuer_domicile_country_code"]), issuerEntityType: string(["issuer_entity_type"]),
    issuerIndustry: string(["issuer_industry"]), issuerIndustryGroup: string(["issuer_industry_group"]), issuerLegalName: string(["issuer_legal_name"]),
    issuerName: string(["issuer_name"]), issuerSector: string(["issuer_sector"]), issuerSicCode: string(["issuer_sic_code"]),
    issuerSicDescription: string(["issuer_sic_description"]), issuerStateOfIncorporation: string(["issuer_state_of_incorporation"]),
    issuerStatus: string(["issuer_status"]), issuerWebsiteUrl: string(["issuer_website_url"]), listingIdSample: strings(["listing_id_sample"]),
    listingStatusSample: strings(["listing_status_sample"]), mappingStatusSample: strings(["mapping_status_sample"]), maxMappingConfidence: number(["max_mapping_confidence"]),
    primaryCurrencyCode: string(["primary_currency_code"]), primaryDocument: string(["primary_document"]), primaryDocumentRows: number(["primary_document_rows"]),
    primaryDocumentUrl: string(["primary_document_url"]), primaryExchangeCode: string(["primary_exchange_code"]), primaryIbkrConid: string(["primary_ibkr_conid"]),
    primaryTicker: string(["primary_ticker"]), qualityFlagSample: strings(["quality_flag_sample"]), reportDate: string(["report_date"]),
    securityAssetClass: string(["security_asset_class"]), securityIdSample: strings(["security_id_sample"]), securityInstrumentType: string(["security_instrument_type"]),
    securityName: string(["security_name"]), securityProductType: string(["security_product_type"]), securityStatus: string(["security_status"]),
    securityType: string(["security_type"]), sourceFileName: string(["source_file_name"]), symbolIdSample: strings(["symbol_id_sample"]),
    symbolSourceSample: strings(["symbol_source_sample"]), rowOrigin: string(["row_origin"]) || "canonical_parent", textChars: number(["text_chars"]),
    textKindSample: strings(["text_kind_sample"]), textRows: number(["text_rows"]), textStatus: string(["text_status"]),
    xbrlFactRows: number(["xbrl_fact_rows"]), xbrlFactTagSample: strings(["xbrl_fact_tag_sample"]), xbrlFactTags: number(["xbrl_fact_tags"]),
    xbrlFrameRows: number(["xbrl_frame_rows"]), xbrlFrameTagSample: strings(["xbrl_frame_tag_sample"]), xbrlFrameTags: number(["xbrl_frame_tags"]),
  };
}

function secTodaySummaryFromPayload(summaryPayload: unknown, rows: SecTodayRow[]): SecTodaySummary {
  const fallback = rows.reduce((summary, row) => ({
    documentRows: summary.documentRows + row.documentRows,
    feedParticipantRows: summary.feedParticipantRows + (row.rowOrigin === "sec_gateway_feed_participant" ? 1 : 0),
    feedRecentError: summary.feedRecentError, feedRecentRows: summary.feedRecentRows,
    latest: !summary.latest || parseServiceTimestamp(row.acceptedAtUtc) > parseServiceTimestamp(summary.latest) ? row.acceptedAtUtc : summary.latest,
    loadedRows: rows.length, textRows: summary.textRows + row.textRows, totalFilings: rows.length,
    withDocuments: summary.withDocuments + (row.documentRows > 0 ? 1 : 0), withText: summary.withText + (row.textRows > 0 ? 1 : 0),
    withXbrl: summary.withXbrl + (row.xbrlFactRows + row.xbrlFrameRows > 0 ? 1 : 0),
    xbrlFactRows: summary.xbrlFactRows + row.xbrlFactRows, xbrlFrameRows: summary.xbrlFrameRows + row.xbrlFrameRows,
  }), defaultSecTodayRowsState("desc").summary);
  if (!isRecord(summaryPayload)) return fallback;
  return {
    documentRows: numericMetric(summaryPayload, ["document_rows"]) || fallback.documentRows,
    feedParticipantRows: numericMetric(summaryPayload, ["feed_participant_rows"]) || fallback.feedParticipantRows,
    feedRecentError: stringMetric(summaryPayload, ["feed_recent_error"]) || fallback.feedRecentError,
    feedRecentRows: numericMetric(summaryPayload, ["feed_recent_rows"]) || fallback.feedRecentRows,
    latest: stringMetric(summaryPayload, ["latest_accepted_at_utc"]) || fallback.latest,
    loadedRows: numericMetric(summaryPayload, ["loaded_rows"]) || rows.length,
    textRows: numericMetric(summaryPayload, ["text_rows"]) || fallback.textRows,
    totalFilings: numericMetric(summaryPayload, ["total_filings"]) || fallback.totalFilings,
    withDocuments: numericMetric(summaryPayload, ["with_documents"]) || fallback.withDocuments,
    withText: numericMetric(summaryPayload, ["with_text"]) || fallback.withText,
    withXbrl: numericMetric(summaryPayload, ["with_xbrl"]) || fallback.withXbrl,
    xbrlFactRows: numericMetric(summaryPayload, ["xbrl_fact_rows"]) || fallback.xbrlFactRows,
    xbrlFrameRows: numericMetric(summaryPayload, ["xbrl_frame_rows"]) || fallback.xbrlFrameRows,
  };
}
