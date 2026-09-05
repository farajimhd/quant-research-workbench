import { timestampFraction } from "../timeZones";

const EXCHANGE_TIME_ZONE = "America/New_York";
const VANCOUVER_TIME_ZONE = "America/Vancouver";

export function MarketTime({ className = "", dateStyle = "full", includeDate = false, includeSeconds = false, includeSubseconds = false, layout = "stacked", value }: { className?: string; dateStyle?: "full" | "short"; includeDate?: boolean; includeSeconds?: boolean; includeSubseconds?: boolean; layout?: "inline" | "stacked"; value: string | number | Date }) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return <span className={`market-time${className ? ` ${className}` : ""}`}>—</span>;
  const fraction = includeSubseconds ? `.${timestampFraction(value)}` : "";
  const exchangeTime = formatTime(date, EXCHANGE_TIME_ZONE, includeSeconds || includeSubseconds) + fraction;
  const vancouverTime = formatTime(date, VANCOUVER_TIME_ZONE, includeSeconds || includeSubseconds) + fraction;
  const exchangeDate = includeDate ? formatDate(date, EXCHANGE_TIME_ZONE, dateStyle) : "";
  const label = `${exchangeDate ? `${exchangeDate}, ` : ""}${exchangeTime} ET; ${vancouverTime} Vancouver`;
  return <time aria-label={label} className={`market-time market-time-${layout}${className ? ` ${className}` : ""}`} dateTime={typeof value === "string" ? value : date.toISOString()}>
    <span className="market-time-primary">{exchangeDate ? <b className="market-time-date">{exchangeDate}</b> : null}<strong>{exchangeTime} <span className="market-time-zone">ET</span></strong></span>
    <small className="market-time-secondary">VAN {vancouverTime}</small>
  </time>;
}

function formatTime(value: Date, timeZone: string, includeSeconds: boolean) {
  return new Intl.DateTimeFormat("en-US", { hour: "2-digit", hour12: false, minute: "2-digit", ...(includeSeconds ? { second: "2-digit" } : {}), timeZone }).format(value);
}

function formatDate(value: Date, timeZone: string, style: "full" | "short") {
  return new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", timeZone, ...(style === "full" ? { year: "numeric" } : {}) }).format(value);
}
