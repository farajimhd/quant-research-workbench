import { formatCompactNumber } from "../../app/format";
import type { ServiceRuntimeLogRow, ServiceStatusPayload } from "./contracts";
import { numericMetric, serviceMetricsRecord, stringMetric } from "./metrics";
import type { NewsPollHistoryRow } from "./newsWorkContracts";
import { isRecord } from "./workPresentation";

export function newsPollHistorySummary(rows: NewsPollHistoryRow[]) {
  const count = Math.max(1, rows.length);
  const sum = rows.reduce(
    (totals, row) => ({
      providerRows: totals.providerRows + row.providerRows,
      uniqueRows: totals.uniqueRows + row.uniqueRows,
      duplicateRows: totals.duplicateRows + row.duplicateRows,
      wallSeconds: totals.wallSeconds + row.wallSeconds,
    }),
    { duplicateRows: 0, providerRows: 0, uniqueRows: 0, wallSeconds: 0 },
  );
  return {
    avgDuplicateRows: sum.duplicateRows / count,
    avgProviderRows: sum.providerRows / count,
    avgUniqueRows: sum.uniqueRows / count,
    avgWallSeconds: sum.wallSeconds / count,
  };
}

export function newsLiveBadge(service: ServiceStatusPayload, history: NewsPollHistoryRow[]) {
  if (!service.online) return { className: "error", label: "offline" };
  const metrics = serviceMetricsRecord(service);
  const latest = history[0];
  const failed = latest?.failedRows ?? numericMetric(metrics, ["last_cycle_failed_rows"]);
  if (failed > 0) return { className: "warn", label: "poll issues" };
  const fetched = latest?.providerRows ?? numericMetric(metrics, ["last_cycle_provider_rows"]);
  if (fetched > 0) return { className: "active", label: "polling" };
  return { className: "idle", label: "idle" };
}

export function newsPollHistoryRowsFromLogs(service: ServiceStatusPayload): NewsPollHistoryRow[] {
  return (service.logs?.rows ?? [])
    .filter((row) => row.event === "poll_completed" && isRecord(row.fields))
    .map((row) => newsPollHistoryRowFromLog(row, service.checked_at_utc))
    .filter((row): row is NewsPollHistoryRow => Boolean(row));
}

export function newsPollHistoryRowFromLog(row: ServiceRuntimeLogRow, checkedAt: string): NewsPollHistoryRow | null {
  const fields = row.fields;
  if (!isRecord(fields)) return null;
  const pollId = stringMetric(fields, ["poll_id"]);
  const pollRunMatch = pollId.match(/(\d+)$/);
  const pollRun = pollRunMatch ? Number(pollRunMatch[1]) : 0;
  const pollAt = row.ts_utc || stringMetric(fields, ["start_utc"]) || checkedAt;
  const providerRows = numericMetric(fields, ["provider_rows"]);
  const processedRows = numericMetric(fields, ["processed_rows"]);
  const uniqueRows = numericMetric(fields, ["unique_news_rows"]);
  const duplicateRows = numericMetric(fields, ["duplicate_news_rows", "input_duplicate_ids_total"]);
  const writtenRows = numericMetric(fields, ["normalized_rows_inserted"]);
  const skippedExisting = numericMetric(fields, ["skipped_existing"]);
  const failedRows = numericMetric(fields, ["failed_rows"]);
  const wallSeconds = numericMetric(fields, ["wall_seconds"]);
  const status = stringMetric(fields, ["status"]) || row.level || "observed";
  const signature = [
    pollId || pollRun,
    pollAt,
    providerRows,
    processedRows,
    uniqueRows,
    writtenRows,
    skippedExisting,
    failedRows,
    status,
  ].join("|");
  return {
    checkedAt,
    duplicateRows,
    failedRows,
    pollAt,
    pollRun,
    processedRows,
    providerRows,
    signature,
    skippedExisting,
    status,
    uniqueRows,
    wallSeconds,
    writtenRows,
  };
}

export function mergeNewsPollHistory(...sets: NewsPollHistoryRow[][]) {
  const bySignature = new Map<string, NewsPollHistoryRow>();
  for (const rows of sets) {
    for (const row of rows) bySignature.set(row.signature, row);
  }
  return Array.from(bySignature.values())
    .sort((a, b) => (Date.parse(b.pollAt) || 0) - (Date.parse(a.pollAt) || 0))
    .slice(0, 50);
}

export function historiesEqual(left: NewsPollHistoryRow[], right: NewsPollHistoryRow[]) {
  if (left.length !== right.length) return false;
  return left.every((row, index) => row.signature === right[index]?.signature);
}

export function newsPollHistoryRow(service: ServiceStatusPayload): NewsPollHistoryRow | null {
  const metrics = serviceMetricsRecord(service);
  const pollRun = numericMetric(metrics, ["poll_runs"]);
  if (!pollRun) return null;
  const pollAt = stringMetric(metrics, ["last_poll_at_utc"]) || service.checked_at_utc;
  const providerRows = numericMetric(metrics, ["last_cycle_provider_rows"]);
  const processedRows = numericMetric(metrics, ["last_cycle_processed_rows"]);
  const uniqueRows = numericMetric(metrics, ["last_cycle_unique_news_rows"]);
  const duplicateRows = numericMetric(metrics, ["last_cycle_duplicate_news_rows"]);
  const writtenRows = numericMetric(metrics, ["last_cycle_written_rows"]);
  const skippedExisting = numericMetric(metrics, ["last_cycle_skipped_existing"]);
  const failedRows = numericMetric(metrics, ["last_cycle_failed_rows"]);
  const wallSeconds = numericMetric(metrics, ["last_cycle_wall_seconds"]);
  const status = stringMetric(metrics, ["last_cycle_status"]) || "observed";
  const signature = [
    pollRun,
    pollAt,
    providerRows,
    processedRows,
    uniqueRows,
    writtenRows,
    skippedExisting,
    failedRows,
    status,
  ].join("|");
  return {
    checkedAt: service.checked_at_utc,
    duplicateRows,
    failedRows,
    pollAt,
    pollRun,
    processedRows,
    providerRows,
    signature,
    skippedExisting,
    status,
    uniqueRows,
    wallSeconds,
    writtenRows,
  };
}

export function formatSeconds(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "-";
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  return `${Math.round(seconds)}s`;
}
