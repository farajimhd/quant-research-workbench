import { displayName, formatCompactNumber } from "../../app/format";
import type { ServiceStatusPayload } from "./contracts";
import { NewsDailyHistogram } from "./NewsDailyHistogram";
import { numericMetric, serviceMetricsRecord, stringMetric } from "./metrics";
import type { NewsPollHistoryRow } from "./newsWorkContracts";
import {
  coverageStatusClass,
  coverageStatusLabel,
  formatSeconds,
  newsCoverageHistoryRows,
  newsEnrichmentHistoryRows,
  newsLiveBadge,
  newsPollHistorySummary,
  newsPublishHistoryRows,
} from "./newsWorkPresentation";
import type { ServiceWorkGroup } from "./serviceWorkContracts";
import { useNewsDailyHistogram } from "./useNewsDailyHistogram";
import { workStatusClass } from "./workPresentation";
import {
  NewsCoverageHistoryTable,
  NewsEnrichmentHistoryTable,
  NewsPollHistoryTable,
  NewsPublishHistoryTable,
} from "./NewsHistoryTables";

export function NewsBenzingaLiveCard({ group, history, service }: { group: ServiceWorkGroup; history: NewsPollHistoryRow[]; service: ServiceStatusPayload }) {
  const metrics = serviceMetricsRecord(service);
  const histogram = useNewsDailyHistogram(service.registry.id === "news");
  const histogramData = histogram.rows;
  const summary = newsPollHistorySummary(history);
  const backgroundPending = numericMetric(metrics, ["background_pending_articles", "publish_pending_rows", "background_queue_size"]);
  const liveBadge = newsLiveBadge(service, history);
  return (
    <section className={`service-work-responsibility-card news-live-card ${workStatusClass(group.status)}`}>
      <div className="service-work-responsibility-header news-live-card-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${liveBadge.className}`}>{liveBadge.label}</span>
      </div>
      <NewsDailyHistogram
        binSeconds={histogram.binSeconds}
        data={histogramData}
        error={histogram.error}
        windowEndUtc={histogram.windowEndUtc}
        windowStartUtc={histogram.windowStartUtc}
      />
      <div className="news-live-summary">
        <span><small>Polls</small><strong>{formatCompactNumber(numericMetric(metrics, ["poll_runs"]))}</strong></span>
        <span><small>Avg Fetched</small><strong>{formatCompactNumber(summary.avgProviderRows)}</strong></span>
        <span><small>Avg Unique</small><strong>{formatCompactNumber(summary.avgUniqueRows)}</strong></span>
        <span><small>Avg Duplicate</small><strong>{formatCompactNumber(summary.avgDuplicateRows)}</strong></span>
        <span><small>Avg Runtime</small><strong>{formatSeconds(summary.avgWallSeconds)}</strong></span>
        <span><small>Pending</small><strong>{formatCompactNumber(backgroundPending)}</strong></span>
      </div>
      <NewsPollHistoryTable rows={history} />
    </section>
  );
}

export function NewsDatabasePublishingCard({ group, service }: { group: ServiceWorkGroup; service: ServiceStatusPayload }) {
  const metrics = serviceMetricsRecord(service);
  const history = newsPublishHistoryRows(service);
  const status = String(metrics.publish_status || group.status || "idle");
  const insertedRows = numericMetric(metrics, ["written_rows"]);
  const tickerRows = numericMetric(metrics, ["ticker_rows_written"]);
  const skippedRows = numericMetric(metrics, ["skipped_existing"]);
  const failedJobs = numericMetric(metrics, ["publish_failed_jobs"]);
  return (
    <section className={`service-work-responsibility-card news-publish-card ${workStatusClass(status)}`}>
      <div className="service-work-responsibility-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${workStatusClass(status)}`}>{displayName(status)}</span>
      </div>
      <div className="news-live-summary news-publish-summary">
        <span><small>Active</small><strong>{formatCompactNumber(numericMetric(metrics, ["publish_active_jobs"]))}</strong></span>
        <span><small>Pending Rows</small><strong>{formatCompactNumber(numericMetric(metrics, ["publish_pending_rows"]))}</strong></span>
        <span className={insertedRows > 0 ? "metric-good" : ""}><small>Inserted</small><strong>{formatCompactNumber(insertedRows)}</strong></span>
        <span><small>Ticker Links</small><strong>{formatCompactNumber(tickerRows)}</strong></span>
        <span className={skippedRows > 0 ? "metric-warn" : ""}><small>Skipped</small><strong>{formatCompactNumber(skippedRows)}</strong></span>
        <span className={failedJobs > 0 ? "metric-bad" : ""}><small>Failed Jobs</small><strong>{formatCompactNumber(failedJobs)}</strong></span>
      </div>
      <NewsPublishHistoryTable rows={history} />
    </section>
  );
}

export function NewsEnrichmentCanonicalCard({ group, service }: { group: ServiceWorkGroup; service: ServiceStatusPayload }) {
  const metrics = serviceMetricsRecord(service);
  const history = newsEnrichmentHistoryRows(service);
  const pendingArticles = numericMetric(metrics, ["background_pending_articles"]);
  const activeBatches = numericMetric(metrics, ["background_active_batches"]);
  const completedArticles = numericMetric(metrics, ["background_completed_articles"]);
  const enrichedUrls = numericMetric(metrics, ["background_enriched_urls"]);
  const failedArticles = numericMetric(metrics, ["background_failed_articles"]);
  const fetchTasks = numericMetric(metrics, ["background_fetch_tasks"]);
  const status = failedArticles > 0 ? "warning" : activeBatches > 0 || pendingArticles > 0 ? "running" : completedArticles > 0 ? "complete" : group.status;
  return (
    <section className={`service-work-responsibility-card news-publish-card news-enrichment-card ${workStatusClass(status)}`}>
      <div className="service-work-responsibility-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${workStatusClass(status)}`}>{displayName(status || "idle")}</span>
      </div>
      <div className="news-live-summary news-publish-summary">
        <span className={pendingArticles > 0 ? "metric-warn" : ""}><small>Pending</small><strong>{formatCompactNumber(pendingArticles)}</strong></span>
        <span><small>Active</small><strong>{formatCompactNumber(activeBatches)}</strong></span>
        <span className={completedArticles > 0 ? "metric-good" : ""}><small>Done</small><strong>{formatCompactNumber(completedArticles)}</strong></span>
        <span className={enrichedUrls > 0 ? "metric-good" : ""}><small>URL Text</small><strong>{formatCompactNumber(enrichedUrls)}</strong></span>
        <span><small>Fetch Tasks</small><strong>{formatCompactNumber(fetchTasks)}</strong></span>
        <span className={failedArticles > 0 ? "metric-bad" : ""}><small>Failed</small><strong>{formatCompactNumber(failedArticles)}</strong></span>
      </div>
      <NewsEnrichmentHistoryTable rows={history} />
    </section>
  );
}

export function NewsCoverageGapCard({ group, service }: { group: ServiceWorkGroup; service: ServiceStatusPayload }) {
  const metrics = serviceMetricsRecord(service);
  const history = newsCoverageHistoryRows(service);
  const gapStatus = stringMetric(metrics, ["gap_status"]) || group.status || "idle";
  const totalChunks = numericMetric(metrics, ["gap_fill_total_chunks"]);
  const flushedChunks = numericMetric(metrics, ["gap_fill_flushed_chunks"]);
  const submittedChunks = numericMetric(metrics, ["gap_fill_submitted_chunks"]);
  const inFlightChunks = numericMetric(metrics, ["gap_fill_in_flight_chunks"]);
  const probeCompleted = numericMetric(metrics, ["bootstrap_probe_completed"]);
  const probeTotal = numericMetric(metrics, ["bootstrap_probe_total"]);
  const manualScript = stringMetric(metrics, ["manual_gap_fill_script_win"]);
  const statusClass = coverageStatusClass(gapStatus, { inFlightChunks, totalChunks });
  const latestGapCount = history.find((row) => row.gapCount > 0)?.gapCount ?? 0;
  const latestScript = manualScript || history.find((row) => row.script)?.script || "";
  const gapCount = numericMetric(metrics, ["gap_count", "gaps"]) || latestGapCount;
  return (
    <section className={`service-work-responsibility-card news-publish-card news-coverage-card ${statusClass}`}>
      <div className="service-work-responsibility-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${statusClass}`}>{coverageStatusLabel(gapStatus)}</span>
      </div>
      <div className="news-live-summary news-publish-summary news-coverage-summary">
        <span className={statusClass === "ok" ? "metric-good" : statusClass === "error" ? "metric-bad" : statusClass === "warn" ? "metric-warn" : ""}>
          <small>Status</small><strong>{coverageStatusLabel(gapStatus)}</strong>
        </span>
        <span><small>Gaps</small><strong>{formatCompactNumber(gapCount)}</strong></span>
        <span className={totalChunks > 0 && flushedChunks >= totalChunks ? "metric-good" : totalChunks > 0 ? "metric-warn" : ""}>
          <small>Chunks</small><strong>{totalChunks ? `${formatCompactNumber(flushedChunks)}/${formatCompactNumber(totalChunks)}` : "-"}</strong>
        </span>
        <span><small>In Flight</small><strong>{formatCompactNumber(inFlightChunks)}</strong></span>
        <span><small>Probes</small><strong>{probeTotal ? `${formatCompactNumber(probeCompleted)}/${formatCompactNumber(probeTotal)}` : "-"}</strong></span>
        <span className={latestScript ? "metric-warn" : ""}><small>Manual</small><strong>{latestScript ? "Ready" : "-"}</strong></span>
      </div>
      <NewsCoverageHistoryTable rows={history} />
    </section>
  );
}
