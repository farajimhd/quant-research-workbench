import { useMemo } from "react";

import { formatCompactNumber } from "../../app/format";
import { histogramBinDurationLabel } from "./histogramWindow";
import type { NewsDailyHistogramDatum } from "./newsContracts";
import {
  defaultNewsHistogramWindow,
  elapsedNewsHistogramRows,
  newsHistogramFullWindowRows,
  newsHistogramHover,
} from "./newsHistogramPresentation";
import { ServiceHistogram } from "./ServiceHistogram";
import { EXCHANGE_TIME_ZONE, formatZoneDateTime } from "./time";

export function NewsDailyHistogram({
  binSeconds,
  data,
  error,
  windowEndUtc,
  windowStartUtc,
}: {
  binSeconds: number;
  data: NewsDailyHistogramDatum[];
  error: string;
  windowEndUtc: string;
  windowStartUtc: string;
}) {
  const defaultWindow = useMemo(() => defaultNewsHistogramWindow(binSeconds), [binSeconds]);
  const effectiveWindowStartUtc = windowStartUtc || defaultWindow.windowStartUtc;
  const effectiveWindowEndUtc = windowEndUtc || defaultWindow.windowEndUtc;
  const effectiveData = useMemo(
    () => data.length
      ? elapsedNewsHistogramRows(data, effectiveWindowStartUtc, effectiveWindowEndUtc, binSeconds)
      : defaultWindow.rows,
    [binSeconds, data, defaultWindow.rows, effectiveWindowEndUtc, effectiveWindowStartUtc],
  );
  const displayData = useMemo(
    () => newsHistogramFullWindowRows(effectiveData, effectiveWindowStartUtc, effectiveWindowEndUtc, binSeconds),
    [binSeconds, effectiveData, effectiveWindowEndUtc, effectiveWindowStartUtc],
  );
  const singleTotal = effectiveData.reduce((sum, row) => sum + row.singleTickerRows, 0);
  const broadTotal = effectiveData.reduce((sum, row) => sum + row.broadOrNoneRows, 0);
  const total = singleTotal + broadTotal;
  return (
    <ServiceHistogram
      ariaLabel={(row) => `${formatZoneDateTime(new Date(Date.parse(row.bucketUtc)), EXCHANGE_TIME_ZONE)}: ${row.singleTickerRows} one-ticker, ${row.broadOrNoneRows} broad`}
      error={error}
      getHover={newsHistogramHover}
      getKey={(row) => row.bucketUtc}
      getSegments={(row) => [
        { className: "broad", value: row.broadOrNoneRows },
        { className: "single", value: row.singleTickerRows },
      ]}
      getTotal={(row) => row.totalRows}
      label={`Today from DB / ${histogramBinDurationLabel(binSeconds)} bins`}
      legend={<>
        <span className="single">1 ticker <strong>{formatCompactNumber(singleTotal)}</strong></span>
        <span className="broad">0 or 2+ tickers <strong>{formatCompactNumber(broadTotal)}</strong></span>
        <span>total <strong>{formatCompactNumber(total)}</strong></span>
      </>}
      renderHover={(hover) => <>
        <strong>{hover.et}</strong>
        <span>VAN {hover.van}</span>
        <span>UTC {hover.utc}</span>
        <span>1 ticker {formatCompactNumber(hover.single)}</span>
        <span>0 or 2+ {formatCompactNumber(hover.broad)}</span>
      </>}
      rows={displayData}
    />
  );
}
