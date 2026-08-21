import { displayName, formatCell, formatCompactNumber } from "../../app/format";
import type { ServiceRuntimeLogRow, ServiceStatusPayload } from "./contracts";
import { numericMetric, stringArrayMetric, stringMetric, uniqueStringSample } from "./metrics";
import type { NewsEnrichmentArticleRow, NewsEnrichmentHistoryRow } from "./newsWorkContracts";
import { publishTickerLabel, shortPollId } from "./newsPublishHistory";
import { isRecord } from "./workPresentation";

export function newsEnrichmentHistoryRows(service: ServiceStatusPayload): NewsEnrichmentHistoryRow[] {
  return (service.logs?.rows ?? [])
    .filter((row) => isNewsEnrichmentLogEvent(row.event || ""))
    .map(newsEnrichmentHistoryRow)
    .sort((a, b) => (Date.parse(b.time) || 0) - (Date.parse(a.time) || 0))
    .slice(0, 50);
}

export function isNewsEnrichmentLogEvent(event: string) {
  return event === "background_batch_queued"
    || event === "background_batch_started"
    || event === "background_batch_completed"
    || event === "background_article_enrichment_failed"
    || event === "background_batch_failed_uncaught"
    || event === "live_url_download_not_downloaded"
    || event === "shutdown_waiting_for_background_news"
    || event === "shutdown_background_drained"
    || event === "shutdown_background_timeout";
}

export function newsEnrichmentHistoryRow(logRow: ServiceRuntimeLogRow): NewsEnrichmentHistoryRow {
  const fields = isRecord(logRow.fields) ? logRow.fields : {};
  const event = logRow.event || "background";
  const status = enrichmentEventVisualStatus(event);
  const articleCount = numericMetric(fields, ["article_count", "processed_rows", "pending_articles"]);
  const failedArticles = numericMetric(fields, ["article_failures", "failed_articles"]);
  const enrichedUrls = numericMetric(fields, ["enriched_urls"]);
  const fetchTasks = numericMetric(fields, ["fetch_task_count", "url_tasks"]);
  const queueSize = numericMetric(fields, ["queue_size", "pending_batches"]);
  const pollId = stringMetric(fields, ["poll_id"]);
  const items = newsEnrichmentArticleRows(fields);
  const itemTitles = items.map((item) => item.title).filter(Boolean);
  const itemUrls = items.flatMap((item) => item.urlSample).filter(Boolean);
  const itemDomains = items.flatMap((item) => item.domainSample).filter(Boolean);
  const titleSample = uniqueStringSample([
    ...stringArrayMetric(fields, ["enrichment_title_sample", "title_sample"]),
    ...itemTitles,
  ], 8);
  const urlSample = uniqueStringSample([
    ...stringArrayMetric(fields, ["enrichment_url_sample", "url_sample"]),
    ...itemUrls,
  ], 12);
  const domainSample = uniqueStringSample([
    ...stringArrayMetric(fields, ["enrichment_domain_sample", "domain_sample"]),
    ...itemDomains,
  ], 8);
  return {
    articleCount,
    detail: enrichmentEventDetail(event, fields),
    domainSample,
    enrichedUrls,
    event,
    failedArticles,
    fetchTasks,
    mode: stringMetric(fields, ["coverage_mode"]),
    pollId,
    providerArticleId: stringMetric(fields, ["provider_article_id"]),
    queueSize,
    status,
    time: logRow.ts_utc || "",
    title: enrichmentEventTitle(event, fields),
    titleSample,
    items,
    urlSample,
    wallSeconds: numericMetric(fields, ["wall_seconds"]),
    worker: stringMetric(fields, ["worker_index"]),
  };
}

export function enrichmentUrlLabel(row: NewsEnrichmentHistoryRow) {
  const itemWithUrl = row.items.find((item) => item.domainSample.length || item.urlSample.length);
  if (itemWithUrl) return newsEnrichmentArticleUrlLabel(itemWithUrl);
  if (row.domainSample.length) {
    const label = row.domainSample.slice(0, 2).join(", ");
    const extra = Math.max(0, row.domainSample.length - 2);
    return extra ? `${label} +${extra}` : label;
  }
  if (row.urlSample.length) {
    const label = row.urlSample[0].replace(/^https?:\/\//i, "").replace(/^www\./i, "");
    return label.length > 34 ? `${label.slice(0, 31)}...` : label;
  }
  return row.fetchTasks ? `${formatCompactNumber(row.fetchTasks)} tasks` : "-";
}

export function newsEnrichmentArticleRows(fields: Record<string, unknown>): NewsEnrichmentArticleRow[] {
  const rawItems = Array.isArray(fields.items) ? fields.items.filter(isRecord) : [];
  return rawItems
    .map(newsEnrichmentArticleRow)
    .filter((item) => item.title || item.urlSample.length || item.domainSample.length || item.providerArticleId || item.canonicalNewsId);
}

export function newsEnrichmentArticleRow(item: Record<string, unknown>): NewsEnrichmentArticleRow {
  const urlSample = uniqueStringSample(stringArrayMetric(item, ["url_sample", "enrichment_url_sample", "source_url", "url"]), 8);
  const domainSample = uniqueStringSample(stringArrayMetric(item, ["domain_sample", "enrichment_domain_sample"]), 8);
  return {
    canonicalNewsId: stringMetric(item, ["canonical_news_id"]),
    domainSample,
    externalFetchStatus: stringMetric(item, ["external_fetch_status", "source_text_status"]),
    hasPdf: Boolean(item.has_pdf),
    preEnrichedRow: isRecord(item.pre_enriched_row) ? item.pre_enriched_row : {},
    providerArticleId: stringMetric(item, ["provider_article_id"]),
    providerPayload: isRecord(item.provider_payload) ? item.provider_payload : {},
    publishedAt: stringMetric(item, ["published_at_utc", "published_utc", "published"]),
    requiresEnrichment: Boolean(item.requires_enrichment),
    tickers: publishTickerLabel({}, item),
    title: stringMetric(item, ["title", "headline"]),
    urlCount: numericMetric(item, ["url_count"]) || urlSample.length,
    urlResolution: isRecord(item.url_resolution) ? item.url_resolution : {},
    urlSample,
  };
}

export function newsEnrichmentArticleUrlLabel(item: NewsEnrichmentArticleRow) {
  if (item.domainSample.length) {
    const label = item.domainSample.slice(0, 2).join(", ");
    const extra = Math.max(0, item.domainSample.length - 2);
    return extra ? `${label} +${extra}` : label;
  }
  if (item.urlSample.length) {
    const label = item.urlSample[0].replace(/^https?:\/\//i, "").replace(/^www\./i, "");
    return label.length > 42 ? `${label.slice(0, 39)}...` : label;
  }
  return item.urlCount ? `${formatCompactNumber(item.urlCount)} URLs` : "-";
}

export function enrichmentEventVisualStatus(event: string) {
  if (event.includes("failed") || event.includes("timeout") || event.includes("not_downloaded")) return "failed";
  if (event.includes("started") || event.includes("waiting")) return "running";
  if (event.includes("queued")) return "queued";
  if (event.includes("completed") || event.includes("drained")) return "complete";
  return "observed";
}

export function enrichmentEventTitle(event: string, fields: Record<string, unknown>) {
  if (event === "background_batch_queued") return "queued batch";
  if (event === "background_batch_started") return `worker ${stringMetric(fields, ["worker_index"]) || "-"} active`;
  if (event === "background_batch_completed") return "completed batch";
  if (event === "background_article_enrichment_failed") return "article failed";
  if (event === "background_batch_failed_uncaught") return "batch failed";
  if (event === "live_url_download_not_downloaded") return "url not downloaded";
  if (event === "shutdown_waiting_for_background_news") return "shutdown drain";
  if (event === "shutdown_background_drained") return "queue drained";
  if (event === "shutdown_background_timeout") return "drain timeout";
  return displayName(event);
}

export function enrichmentEventDetail(event: string, fields: Record<string, unknown>) {
  if (event === "background_batch_completed") {
    return [
      `articles=${formatCompactNumber(numericMetric(fields, ["article_count"]))}`,
      `inserted=${formatCompactNumber(numericMetric(fields, ["normalized_rows_inserted"]))}`,
      `skipped=${formatCompactNumber(numericMetric(fields, ["skipped_existing"]))}`,
      `ticker_links=${formatCompactNumber(numericMetric(fields, ["ticker_rows_inserted"]))}`,
      `text_urls=${formatCompactNumber(numericMetric(fields, ["enriched_urls"]))}`,
    ].join("; ");
  }
  if (event === "background_batch_started") {
    return [
      `poll=${shortPollId(stringMetric(fields, ["poll_id"]))}`,
      `articles=${formatCompactNumber(numericMetric(fields, ["article_count"]))}`,
      `queue=${formatCompactNumber(numericMetric(fields, ["queue_size"]))}`,
    ].join("; ");
  }
  if (event === "background_batch_queued") {
    return [
      `poll=${shortPollId(stringMetric(fields, ["poll_id"]))}`,
      `articles=${formatCompactNumber(numericMetric(fields, ["article_count"]))}`,
      `url_tasks=${formatCompactNumber(numericMetric(fields, ["fetch_task_count"]))}`,
      `queue=${formatCompactNumber(numericMetric(fields, ["queue_size"]))}`,
    ].join("; ");
  }
  if (event === "background_article_enrichment_failed") {
    return [
      `poll=${shortPollId(stringMetric(fields, ["poll_id"]))}`,
      `provider_article_id=${stringMetric(fields, ["provider_article_id"]) || "-"}`,
      `canonical=${shortPollId(stringMetric(fields, ["canonical_news_id"]))}`,
    ].join("; ");
  }
  return Object.entries(fields)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .slice(0, 5)
    .map(([key, value]) => `${displayName(key)}=${formatCell(key, value)}`)
    .join("; ");
}
