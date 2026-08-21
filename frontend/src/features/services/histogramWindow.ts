import { formatCompactNumber } from "../../app/format";
import {
  EXCHANGE_TIME_ZONE,
  exchangeDateParts,
  nextCalendarDate,
  zonedDateTimeToUtc,
} from "./time";

export type ServiceHistogramWindow<Row> = {
  binSeconds: number;
  error: string;
  rows: Row[];
  windowEndUtc: string;
  windowStartUtc: string;
};

export function defaultMarketDayHistogramWindow<Row>(
  binSeconds: number,
  createRow: (bucketUtc: string) => Row,
): ServiceHistogramWindow<Row> {
  const { day, month, year } = exchangeDateParts(new Date());
  const start = zonedDateTimeToUtc(year, month, day, 0, 0, EXCHANGE_TIME_ZONE);
  const nextDay = nextCalendarDate(year, month, day);
  const end = zonedDateTimeToUtc(nextDay.year, nextDay.month, nextDay.day, 0, 0, EXCHANGE_TIME_ZONE);
  const totalBins = Math.max(0, Math.ceil((end.getTime() - start.getTime()) / (binSeconds * 1000)) + 1);
  const elapsedBins = Math.max(0, Math.min(totalBins, Math.ceil((Date.now() - start.getTime()) / (binSeconds * 1000)) + 1));
  const rows = Array.from({ length: elapsedBins }, (_, index) => (
    createRow(new Date(start.getTime() + index * binSeconds * 1000).toISOString())
  ));
  return {
    binSeconds,
    error: "",
    rows,
    windowEndUtc: end.toISOString(),
    windowStartUtc: start.toISOString(),
  };
}

export function elapsedHistogramRows<Row extends { bucketUtc: string }>(
  rows: Row[],
  windowStartUtc: string,
  windowEndUtc: string,
  binSeconds: number,
  hasData: (row: Row) => boolean,
) {
  const start = Date.parse(windowStartUtc);
  const end = Date.parse(windowEndUtc);
  const cutoff = Math.min(Number.isFinite(end) ? end : Date.now(), Date.now());
  const halfBinMs = Math.max(0, binSeconds * 500);
  return rows.filter((row) => {
    const bucket = Date.parse(row.bucketUtc);
    if (!Number.isFinite(bucket)) return false;
    if (Number.isFinite(start) && bucket < start) return false;
    if (Number.isFinite(end) && bucket >= end) return false;
    if (bucket - halfBinMs >= cutoff) return false;
    return hasData(row);
  });
}

export function fillHistogramWindow<Row extends { bucketUtc: string }>(
  rows: Row[],
  windowStartUtc: string,
  windowEndUtc: string,
  binSeconds: number,
  createRow: (bucketUtc: string) => Row,
) {
  const start = Date.parse(windowStartUtc);
  const end = Date.parse(windowEndUtc);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || binSeconds <= 0) return rows;
  const byTime = new Map<number, Row>();
  for (const row of rows) {
    const timestamp = Date.parse(row.bucketUtc);
    if (Number.isFinite(timestamp)) byTime.set(timestamp, row);
  }
  const totalBins = Math.max(1, Math.ceil((end - start) / (binSeconds * 1000)) + 1);
  return Array.from({ length: totalBins }, (_, index) => {
    const timestamp = start + index * binSeconds * 1000;
    return byTime.get(timestamp) ?? createRow(new Date(timestamp).toISOString());
  });
}

export function histogramBinDurationLabel(binSeconds: number) {
  if (binSeconds > 0 && binSeconds % 60 === 0) {
    const minutes = binSeconds / 60;
    return `${formatCompactNumber(minutes)} minute${minutes === 1 ? "" : "s"}`;
  }
  return `${formatCompactNumber(binSeconds)} second${binSeconds === 1 ? "" : "s"}`;
}
