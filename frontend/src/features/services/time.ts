export function parseServiceTimestamp(value: string) {
  const trimmed = String(value || "").trim();
  if (!trimmed) return Number.NaN;
  const clickHouseUtc = trimmed.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d+)?$/);
  if (clickHouseUtc) {
    const fraction = clickHouseUtc[3] ? clickHouseUtc[3].slice(0, 4).padEnd(4, "0") : "";
    return Date.parse(`${clickHouseUtc[1]}T${clickHouseUtc[2]}${fraction}Z`);
  }
  return Date.parse(trimmed);
}

export function formatServiceTime(value: string) {
  const parsed = parseServiceTimestamp(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(parsed));
}

export function formatLogTime(value: string) {
  const parsed = parseServiceTimestamp(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(parsed));
}

export function formatNewsTableDate(value: string) {
  const parsed = parseServiceTimestamp(value);
  if (!Number.isFinite(parsed)) return value || "-";
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", year: "numeric" }).format(new Date(parsed));
}

export function parseLogTime(value: string) {
  const parsed = parseServiceTimestamp(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function tableTimestampMs(value: string, explicitMs?: number) {
  if (Number.isFinite(explicitMs)) return Number(explicitMs);
  return parseServiceTimestamp(value);
}

export function tableRowRecencyClass(value: string | number | undefined) {
  const timestamp = typeof value === "number" ? value : tableTimestampMs(String(value || ""));
  if (!Number.isFinite(timestamp)) return "row-age-unknown";
  const ageMinutes = (Date.now() - timestamp) / 60000;
  if (ageMinutes < -1) return "row-age-future";
  if (ageMinutes <= 1) return "row-age-now";
  if (ageMinutes <= 5) return "row-age-1m";
  if (ageMinutes <= 10) return "row-age-5m";
  if (ageMinutes <= 15) return "row-age-10m";
  if (ageMinutes <= 30) return "row-age-15m";
  if (ageMinutes <= 60) return "row-age-30m";
  return "row-age-old";
}

export function tableTimeTitle(value: string, timestamp: number) {
  if (!Number.isFinite(timestamp)) return value || "-";
  return [
    `ET ${formatReadableDateTime(new Date(timestamp).toISOString(), EXCHANGE_TIME_ZONE)}`,
    `VAN ${formatReadableDateTime(new Date(timestamp).toISOString(), VANCOUVER_TIME_ZONE)}`,
  ].join(" | ");
}

export function formatTableZoneTime(timestamp: number, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone }).format(new Date(timestamp));
}

export function formatTableZoneDate(timestamp: number, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", year: "numeric", timeZone }).format(new Date(timestamp));
}

export function formatZoneTime(value: Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone }).format(value);
}

export function formatZoneDate(value: Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "2-digit", year: "numeric", timeZone }).format(value);
}

export function formatZoneDateTime(value: Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone }).format(value);
}

export function formatReadableDateTime(value: string, timeZone: string) {
  const parsed = parseServiceTimestamp(value);
  if (!Number.isFinite(parsed)) return value || "-";
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    second: "2-digit",
    timeZone,
    timeZoneName: "short",
    weekday: "short",
    year: "numeric",
  }).format(new Date(parsed));
}

export function formatUtcDateTime(value: string) {
  const parsed = parseServiceTimestamp(value);
  if (!Number.isFinite(parsed)) return value || "-";
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" }).format(new Date(parsed));
}

export function nextCalendarDate(year: number, month: number, day: number) {
  const value = new Date(Date.UTC(year, month - 1, day + 1));
  return { day: value.getUTCDate(), month: value.getUTCMonth() + 1, year: value.getUTCFullYear() };
}

export function exchangeDateParts(value: Date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    day: "2-digit",
    month: "2-digit",
    timeZone: EXCHANGE_TIME_ZONE,
    year: "numeric",
  }).formatToParts(value);
  const part = (type: string) => Number(parts.find((item) => item.type === type)?.value || "0");
  return { day: part("day"), month: part("month"), year: part("year") };
}

export function zonedDateTimeToUtc(year: number, month: number, day: number, hour: number, minute: number, timeZone: string) {
  const date = `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  const time = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  return dateInTimeZone(date, time, timeZone);
}
import { dateInTimeZone } from "../../app/timeZones";

export const EXCHANGE_TIME_ZONE = "America/New_York";
export const VANCOUVER_TIME_ZONE = "America/Vancouver";
