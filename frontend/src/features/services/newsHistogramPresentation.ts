import type { NewsDailyHistogramDatum, NewsDailyHistogramState } from "./newsContracts";
import {
  defaultMarketDayHistogramWindow,
  elapsedHistogramRows,
  fillHistogramWindow,
} from "./histogramWindow";
import {
  EXCHANGE_TIME_ZONE,
  VANCOUVER_TIME_ZONE,
  formatUtcDateTime,
  formatZoneDateTime,
} from "./time";

export function defaultNewsHistogramWindow(binSeconds: number): NewsDailyHistogramState {
  return defaultMarketDayHistogramWindow(binSeconds, emptyNewsHistogramRow);
}

export function elapsedNewsHistogramRows(
  rows: NewsDailyHistogramDatum[],
  windowStartUtc: string,
  windowEndUtc: string,
  binSeconds: number,
) {
  return elapsedHistogramRows(
    rows,
    windowStartUtc,
    windowEndUtc,
    binSeconds,
    (row) => row.totalRows > 0 || row.singleTickerRows > 0 || row.broadOrNoneRows > 0,
  );
}

export function newsHistogramFullWindowRows(
  rows: NewsDailyHistogramDatum[],
  windowStartUtc: string,
  windowEndUtc: string,
  binSeconds: number,
) {
  return fillHistogramWindow(rows, windowStartUtc, windowEndUtc, binSeconds, emptyNewsHistogramRow);
}

export function newsHistogramHover(row: NewsDailyHistogramDatum) {
  const bucketDate = new Date(Date.parse(row.bucketUtc));
  return {
    broad: row.broadOrNoneRows,
    et: formatZoneDateTime(bucketDate, EXCHANGE_TIME_ZONE),
    single: row.singleTickerRows,
    utc: formatUtcDateTime(row.bucketUtc),
    van: formatZoneDateTime(bucketDate, VANCOUVER_TIME_ZONE),
  };
}

function emptyNewsHistogramRow(bucketUtc: string): NewsDailyHistogramDatum {
  return { broadOrNoneRows: 0, bucketUtc, singleTickerRows: 0, totalRows: 0 };
}
