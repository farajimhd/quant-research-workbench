import type { ServiceStatusPayload } from "./contracts";
import { fillHistogramWindow } from "./histogramWindow";
import type { SecDailyHistogramDatum, SecLiveFeedRow } from "./secContracts";
import { serviceRecentSourceRows } from "./ServiceActivityPanel";
import { EXCHANGE_TIME_ZONE, VANCOUVER_TIME_ZONE, formatUtcDateTime, formatZoneDateTime } from "./time";
import { firstString, firstTimestamp } from "./workPresentation";

export function secHistogramSummary(rows: SecDailyHistogramDatum[]) {
  return rows.reduce((summary, row) => {
    summary.documents += row.documentRows;
    summary.filingOnly += row.filingOnlyRows;
    summary.text += row.textRows;
    summary.total += row.totalRows;
    summary.xbrl += row.xbrlRows;
    return summary;
  }, { documents: 0, filingOnly: 0, text: 0, total: 0, xbrl: 0 });
}

export function secLiveFeedRows(service: ServiceStatusPayload): SecLiveFeedRow[] {
  const rows = serviceRecentSourceRows(service).map(secLiveFeedRow).filter((row): row is SecLiveFeedRow => Boolean(row));
  const byKey = new Map<string, SecLiveFeedRow>();
  for (const row of rows) {
    const key = [row.accession || row.title, row.time, row.status].join("|");
    if (!byKey.has(key)) byKey.set(key, row);
  }
  return Array.from(byKey.values()).sort((left, right) => (right.timeMs ?? 0) - (left.timeMs ?? 0)).slice(0, 50);
}

export function secHistogramHover(row: SecDailyHistogramDatum) {
  const bucketDate = new Date(Date.parse(row.bucketUtc));
  return {
    documents: row.documentRows,
    et: formatZoneDateTime(bucketDate, EXCHANGE_TIME_ZONE),
    filingOnly: row.filingOnlyRows,
    text: row.textRows,
    total: row.totalRows,
    utc: formatUtcDateTime(row.bucketUtc),
    van: formatZoneDateTime(bucketDate, VANCOUVER_TIME_ZONE),
    xbrl: row.xbrlRows,
  };
}

export function secHistogramFullWindowRows(rows: SecDailyHistogramDatum[], windowStartUtc: string, windowEndUtc: string, binSeconds: number) {
  return fillHistogramWindow(
    rows,
    windowStartUtc,
    windowEndUtc,
    binSeconds,
    (bucketUtc) => ({ bucketUtc, documentRows: 0, filingOnlyRows: 0, textRows: 0, totalRows: 0, xbrlRows: 0 }),
  );
}

function secLiveFeedRow(row: Record<string, unknown>): SecLiveFeedRow | null {
  const accession = firstString(row, ["accession_number", "accession", "accessionNumber"]);
  const cik = firstString(row, ["cik", "central_index_key"]);
  const form = firstString(row, ["form_type", "form", "type"]);
  const title = firstString(row, ["title", "company_name", "issuer_name", "filer_name"]);
  if (!accession && !cik && !form && !title) return null;
  const timestamp = firstTimestamp(row);
  const documentRows = firstString(row, ["documents", "document_rows", "docs"]);
  const textRows = firstString(row, ["texts", "text_rows", "text_count"]);
  const factRows = firstString(row, ["xbrl_facts", "xbrl_fact_rows", "facts_written"]);
  const frameRows = firstString(row, ["xbrl_frames", "xbrl_frame_rows", "frames_written"]);
  return {
    accession,
    cik,
    company: firstString(row, ["company_name", "issuer_name", "filer_name"]),
    documents: [documentRows ? `${documentRows} docs` : "", textRows ? `${textRows} text` : ""].filter(Boolean).join(" / "),
    form,
    raw: row,
    status: firstString(row, ["status", "state", "result", "level"]) || "observed",
    time: timestamp.label,
    timeMs: timestamp.value,
    title,
    xbrl: [factRows ? `${factRows} facts` : "", frameRows ? `${frameRows} frames` : ""].filter(Boolean).join(" / "),
  };
}
