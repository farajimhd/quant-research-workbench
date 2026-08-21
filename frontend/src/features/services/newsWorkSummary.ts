import { formatCompactNumber } from "../../app/format";
import type { ServiceStatusPayload } from "./contracts";
import { numericMetric, serviceMetricsRecord } from "./metrics";
import { newsCoverageHistoryRows } from "./newsCoverageHistory";
import type { WorkPlanSummaryMetric } from "./serviceWorkContracts";

export function newsWorkPlanSummaryItems(service: ServiceStatusPayload): WorkPlanSummaryMetric[] {
  const metrics = serviceMetricsRecord(service);
  const polledRows = numericMetric(metrics, ["provider_rows", "processed_rows", "raw_saved"]);
  const processedRows = numericMetric(metrics, ["processed_rows", "provider_rows", "raw_saved"]);
  const duplicateRows = numericMetric(metrics, ["duplicate_news_rows"]);
  const uniqueNews = numericMetric(metrics, ["unique_news_rows"]) || Math.max(0, processedRows - duplicateRows);
  const enrichedUrls = numericMetric(metrics, ["background_enriched_urls"]);
  const requiredDownloads = numericMetric(metrics, ["background_fetch_tasks"]);
  const insertedRows = numericMetric(metrics, ["written_rows"]);
  const gapFilled = numericMetric(metrics, ["gap_fill_flushed_chunks"]);
  const gapTotal = numericMetric(metrics, ["gap_fill_total_chunks"]);
  const coverageRows = newsCoverageHistoryRows(service).filter((row) => row.coverageId || row.event.includes("coverage") || row.event.includes("gap_fill"));
  const coverageJobs = coverageRows.length;
  return [
    {
      label: "Unique / Polled",
      title: "Distinct Benzinga news items received by the live path divided by all rows returned by polling lookbacks.",
      tone: uniqueNews > 0 ? "active" : undefined,
      value: `${formatCompactNumber(uniqueNews)} / ${formatCompactNumber(polledRows)}`,
    },
    {
      label: "Enriched / Required",
      title: "External URL/PDF downloads that produced text compared with total required fetch tasks.",
      tone: requiredDownloads > 0 && enrichedUrls >= requiredDownloads ? "ok" : requiredDownloads > 0 ? "warn" : undefined,
      value: `${formatCompactNumber(enrichedUrls)} / ${formatCompactNumber(requiredDownloads)}`,
    },
    {
      label: "Inserted",
      title: "Total normalized news rows inserted into ClickHouse by this service run.",
      tone: insertedRows > 0 ? "ok" : undefined,
      value: formatCompactNumber(insertedRows),
    },
    {
      label: "Coverage Filled",
      title: "Coverage or gap-fill work completed in this service run. Shows chunks when a chunked fill ran; otherwise coverage jobs.",
      tone: gapTotal > 0 && gapFilled >= gapTotal ? "ok" : gapTotal > 0 ? "active" : coverageJobs > 0 ? "ok" : undefined,
      value: gapTotal > 0 ? `${formatCompactNumber(gapFilled)} / ${formatCompactNumber(gapTotal)}` : formatCompactNumber(coverageJobs),
    },
  ];
}
