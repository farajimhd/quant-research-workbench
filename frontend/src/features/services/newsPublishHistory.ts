import { formatCompactNumber } from "../../app/format";
import type { ServiceRuntimeLogRow, ServiceStatusPayload } from "./contracts";
import { numericMetric, stringMetric } from "./metrics";
import type { NewsPublishHistoryRow } from "./newsWorkContracts";
import { isRecord } from "./workPresentation";

export function newsPublishHistoryRows(service: ServiceStatusPayload): NewsPublishHistoryRow[] {
  const rows: NewsPublishHistoryRow[] = [];
  for (const logRow of service.logs?.rows ?? []) {
    if (!isNewsPublishLogEvent(logRow.event || "")) continue;
    const fields = isRecord(logRow.fields) ? logRow.fields : {};
    const items = Array.isArray(fields.items) ? fields.items.filter(isRecord) : [];
    if (items.length) {
      items.forEach((item, index) => rows.push(newsPublishItemHistoryRow(logRow, fields, item, index)));
      continue;
    }
    const fallback = newsPublishBatchFallbackRow(logRow, fields);
    if (fallback) rows.push(fallback);
  }
  return rows
    .sort((a, b) => (Date.parse(b.time) || 0) - (Date.parse(a.time) || 0))
    .slice(0, 50);
}

export function isNewsPublishLogEvent(event: string) {
  return event === "publish_completed"
    || event === "publish_failed";
}

export function newsPublishItemHistoryRow(logRow: ServiceRuntimeLogRow, fields: Record<string, unknown>, item: Record<string, unknown>, index: number): NewsPublishHistoryRow {
  const event = logRow.event || "publish";
  const publishStatus = publishItemStatus(event, item);
  return {
    activeJobs: numericMetric(fields, ["active_jobs"]),
    canonicalNewsId: stringMetric(item, ["canonical_news_id"]),
    coverageMode: stringMetric(fields, ["coverage_mode"]),
    enrichment: publishEnrichmentLabel(fields, item),
    event: publishStatus,
    insertedRows: numericMetric(item, ["inserted_rows"]),
    pendingRows: publishStatus === "pending" ? 1 : 0,
    pollId: `${stringMetric(fields, ["poll_id"])}:${index}`,
    providerArticleId: stringMetric(item, ["provider_article_id"]),
    processedRows: 1,
    publishedAt: stringMetric(item, ["published_at_utc"]) || stringMetric(fields, ["published_at_start_utc"]),
    qualityFlags: Array.isArray(item.quality_flags) ? item.quality_flags.map(String).filter(Boolean) : [],
    skippedRows: numericMetric(item, ["skipped_rows"]),
    status: publishItemVisualStatus(publishStatus),
    tickerRows: Array.isArray(item.tickers) ? item.tickers.length : 0,
    tickers: publishTickerLabel(fields, item),
    title: stringMetric(item, ["title"]) || publishTitleLabel(event, fields, item),
    time: logRow.ts_utc || "",
  };
}

export function newsPublishBatchFallbackRow(logRow: ServiceRuntimeLogRow, fields: Record<string, unknown>): NewsPublishHistoryRow | null {
  const processedRows = numericMetric(fields, ["processed_rows", "article_count"]);
  const insertedRows = numericMetric(fields, ["normalized_rows_inserted"]);
  const tickerRows = numericMetric(fields, ["ticker_rows_inserted", "ticker_count"]);
  const skippedRows = numericMetric(fields, ["skipped_existing"]);
  const providerRows = numericMetric(fields, ["provider_rows"]);
  const hasUsefulPublishWork = providerRows > 0 || processedRows > 0 || insertedRows > 0 || tickerRows > 0 || skippedRows > 0;
  if (!hasUsefulPublishWork) return null;
  const event = logRow.event || "publish";
  const publishStatus = event.includes("failed") ? "failed" : event.includes("started") ? "pending" : "batch_summary";
  return {
    activeJobs: numericMetric(fields, ["active_jobs"]),
    canonicalNewsId: "",
    coverageMode: stringMetric(fields, ["coverage_mode"]),
    enrichment: publishEnrichmentLabel(fields, {}),
    event: publishStatus,
    insertedRows,
    pendingRows: numericMetric(fields, ["pending_rows"]),
    pollId: stringMetric(fields, ["poll_id"]),
    providerArticleId: "",
    processedRows,
    publishedAt: stringMetric(fields, ["published_at_start_utc"]),
    qualityFlags: [],
    skippedRows,
    status: publishItemVisualStatus(publishStatus),
    tickerRows,
    tickers: publishTickerLabel(fields, {}),
    title: `${formatCompactNumber(processedRows)} row batch; restart News Gateway for per-row publish status.`,
    time: logRow.ts_utc || "",
  };
}

export function publishItemStatus(event: string, item: Record<string, unknown>) {
  const explicit = stringMetric(item, ["publish_status"]);
  if (explicit) return explicit;
  if (event.includes("failed")) return "failed";
  if (event.includes("started")) return "pending";
  if (event.includes("completed")) return "unknown";
  return event || "unknown";
}

export function publishItemVisualStatus(status: string) {
  const normalized = status.toLowerCase();
  if (normalized.includes("failed")) return "failed";
  if (normalized.includes("pending")) return "running";
  if (normalized.includes("inserted") || normalized.includes("dry_run")) return "complete";
  if (normalized.includes("skipped") || normalized.includes("duplicate") || normalized.includes("summary")) return "idle";
  return "waiting";
}

export function publishTickerLabel(fields: Record<string, unknown>, firstItem: Record<string, unknown>) {
  const candidate = firstItem.tickers ?? fields.ticker_sample;
  if (Array.isArray(candidate)) {
    const labels = candidate.map((item) => String(item || "").trim()).filter(Boolean);
    return labels.length ? labels.slice(0, 5).join(", ") : "-";
  }
  return stringMetric(firstItem, ["ticker", "symbol"]) || "-";
}

export function publishEnrichmentLabel(fields: Record<string, unknown>, firstItem: Record<string, unknown>) {
  const status = stringMetric(firstItem, ["external_fetch_status", "enrichment_status"]) || stringMetric(fields, ["external_fetch_status"]);
  const needs = Boolean(firstItem.requires_enrichment ?? fields.requires_enrichment_count);
  const hasPdf = Boolean(firstItem.has_pdf ?? fields.pdf_count);
  const flags = Array.isArray(firstItem.quality_flags) ? firstItem.quality_flags.map(String).filter(Boolean).slice(0, 2) : [];
  const enrichedUrls = numericMetric(fields, ["enriched_urls"]);
  const parts = [needs ? "needs" : "inline", status || "", hasPdf ? "pdf" : "", enrichedUrls ? `${formatCompactNumber(enrichedUrls)} urls` : "", ...flags].filter(Boolean);
  return parts.length ? parts.join(" / ") : "-";
}

export function publishTitleLabel(event: string, fields: Record<string, unknown>, firstItem: Record<string, unknown>) {
  const title = stringMetric(firstItem, ["title"]) || stringMetric(fields, ["title_sample"]);
  if (title) return title;
  if (event === "poll_completed") return `poll ${shortPollId(stringMetric(fields, ["poll_id"]))}`;
  if (event === "background_batch_completed") return `${formatCompactNumber(numericMetric(fields, ["article_count"]))} enriched article rows`;
  return shortPollId(stringMetric(fields, ["poll_id"]));
}

export function shortPollId(value: string) {
  if (!value) return "-";
  return value.length > 18 ? `${value.slice(0, 8)}...${value.slice(-6)}` : value;
}
