import { useMemo } from "react";

import { formatCompactNumber } from "../../app/format";
import { histogramBinDurationLabel } from "./histogramWindow";
import type { SecDailyHistogramDatum } from "./secContracts";
import {
  secHistogramFullWindowRows,
  secHistogramHover,
  secHistogramSummary,
} from "./secHistogramPresentation";
import { ServiceHistogram } from "./ServiceHistogram";
import { EXCHANGE_TIME_ZONE, formatZoneDateTime } from "./time";
import { defaultSecHistogramWindow, elapsedSecHistogramRows } from "./useSecTodayRows";

export function SecDailyHistogram({
  binSeconds,
  data,
  error,
  windowEndUtc,
  windowStartUtc,
}: {
  binSeconds: number;
  data: SecDailyHistogramDatum[];
  error: string;
  windowEndUtc: string;
  windowStartUtc: string;
}) {
  const defaultWindow = useMemo(() => defaultSecHistogramWindow(binSeconds), [binSeconds]);
  const effectiveWindowStartUtc = windowStartUtc || defaultWindow.windowStartUtc;
  const effectiveWindowEndUtc = windowEndUtc || defaultWindow.windowEndUtc;
  const effectiveData = useMemo(
    () => data.length
      ? elapsedSecHistogramRows(data, effectiveWindowStartUtc, effectiveWindowEndUtc, binSeconds)
      : defaultWindow.rows,
    [binSeconds, data, defaultWindow.rows, effectiveWindowEndUtc, effectiveWindowStartUtc],
  );
  const displayData = useMemo(
    () => secHistogramFullWindowRows(effectiveData, effectiveWindowStartUtc, effectiveWindowEndUtc, binSeconds),
    [binSeconds, effectiveData, effectiveWindowEndUtc, effectiveWindowStartUtc],
  );
  const summary = secHistogramSummary(effectiveData);
  return (
    <ServiceHistogram
      ariaLabel={(row) => `${formatZoneDateTime(new Date(Date.parse(row.bucketUtc)), EXCHANGE_TIME_ZONE)}: ${row.totalRows} SEC filings`}
      className="sec-live-histogram"
      error={error}
      getHover={secHistogramHover}
      getKey={(row) => row.bucketUtc}
      getSegments={(row) => [
        { className: "filing", value: row.filingOnlyRows },
        { className: "documents", value: row.documentRows },
        { className: "text", value: row.textRows },
        { className: "xbrl", value: row.xbrlRows },
      ]}
      getTotal={(row) => row.totalRows}
      hoverClassName="sec-live-histogram-hover"
      label={`Today from DB / ${histogramBinDurationLabel(binSeconds)} bins`}
      legend={<>
        <span className="xbrl">XBRL <strong>{formatCompactNumber(summary.xbrl)}</strong></span>
        <span className="text">text <strong>{formatCompactNumber(summary.text)}</strong></span>
        <span className="documents">docs <strong>{formatCompactNumber(summary.documents)}</strong></span>
        <span className="filing">filing only <strong>{formatCompactNumber(summary.filingOnly)}</strong></span>
        <span>total <strong>{formatCompactNumber(summary.total)}</strong></span>
      </>}
      legendClassName="sec-live-histogram-legend"
      renderHover={(hover) => <>
        <strong>{hover.et}</strong>
        <span>VAN {hover.van}</span>
        <span>UTC {hover.utc}</span>
        <span>XBRL {formatCompactNumber(hover.xbrl)}</span>
        <span>text {formatCompactNumber(hover.text)}</span>
        <span>docs {formatCompactNumber(hover.documents)}</span>
        <span>filing only {formatCompactNumber(hover.filingOnly)}</span>
      </>}
      rows={displayData}
    />
  );
}
