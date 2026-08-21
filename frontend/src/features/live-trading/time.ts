import { dateInTimeZone } from "../../app/timeZones";

const EXCHANGE_TIME_ZONE = "America/New_York";

export type TradingSession = {
  barTime: string;
  sessionDate: string;
};

export function previousSessionDate(sessions: string[], sessionDate: string, countBack: number) {
  const index = sessions.indexOf(sessionDate);
  if (index < 0) return dateOffset(sessionDate, -countBack);
  return sessions[Math.max(0, index - countBack)] ?? sessionDate;
}

export function dateOffset(value: string, days: number) {
  const [year, month, day] = value.split("-").map(Number);
  const instant = new Date(Date.UTC(year, month - 1, day) + days * 86_400_000);
  return instant.toISOString().slice(0, 10);
}

export function addClockMinutes(clock: string, minutes: number) {
  const [hourText, minuteText] = clock.split(":");
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return "";
  const total = hour * 60 + minute + minutes;
  const nextHour = Math.floor(total / 60);
  const nextMinute = total % 60;
  if (nextHour < 0 || nextHour > 23) return "";
  return `${String(nextHour).padStart(2, "0")}:${String(nextMinute).padStart(2, "0")}`;
}

export function isAfterClock(clock: string, cutoff: string) {
  return clockToMinutes(clock) > clockToMinutes(cutoff);
}

export function clockToMinutes(clock: string) {
  const [hourText, minuteText] = clock.split(":");
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return 0;
  return hour * 60 + minute;
}

export function rowTimestampSeconds(row: Record<string, unknown>, sessionDate: string, fallbackClock: string) {
  const raw = stringField(row, "bar_time_market");
  if (!raw) return clockTimestampSeconds(sessionDate, fallbackClock);
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

export function clockTimestampSeconds(sessionDate: string, clock: string) {
  if (!sessionDate || !clock) return null;
  const parsed = dateInTimeZone(sessionDate, clock, EXCHANGE_TIME_ZONE).getTime();
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

export function currentExchangeSession(now = new Date()): TradingSession {
  const parts = exchangeDateParts(now);
  return { barTime: `${parts.hour}:${parts.minute}`, sessionDate: `${parts.year}-${parts.month}-${parts.day}` };
}

export function formatExchangeClock(now = new Date()) {
  const parts = exchangeDateParts(now);
  return `${parts.hour}:${parts.minute}:${parts.second} ET`;
}

export function formatLocalClock(now = new Date()) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    second: "2-digit",
  }).format(now);
}

export function exchangeDateParts(now: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    month: "2-digit",
    second: "2-digit",
    timeZone: EXCHANGE_TIME_ZONE,
    year: "numeric",
  }).formatToParts(now);
  const value = (type: string) => parts.find((part) => part.type === type)?.value || "00";
  return {
    day: value("day"),
    hour: value("hour") === "24" ? "00" : value("hour"),
    minute: value("minute"),
    month: value("month"),
    second: value("second"),
    year: value("year"),
  };
}

function stringField(row: Record<string, unknown>, key: string) {
  const value = row[key];
  return value === null || value === undefined ? "" : String(value);
}
