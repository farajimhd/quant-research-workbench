const EXCHANGE_TIME_ZONE = "America/New_York";
const VANCOUVER_TIME_ZONE = "America/Vancouver";

export function MarketTime({ className = "", dateStyle = "full", includeDate = false, value }: { className?: string; dateStyle?: "full" | "short"; includeDate?: boolean; value: string | number | Date }) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return <span className={`market-time${className ? ` ${className}` : ""}`}>—</span>;
  const exchangeTime = formatTime(date, EXCHANGE_TIME_ZONE);
  const vancouverTime = formatTime(date, VANCOUVER_TIME_ZONE);
  const exchangeDate = includeDate ? formatDate(date, EXCHANGE_TIME_ZONE, dateStyle) : "";
  const label = `${exchangeDate ? `${exchangeDate}, ` : ""}${exchangeTime} ET; ${vancouverTime} Vancouver`;
  return <time aria-label={label} className={`market-time${className ? ` ${className}` : ""}`} dateTime={date.toISOString()}>
    <span>{exchangeDate ? <b>{exchangeDate}</b> : null}<strong>{exchangeTime} ET</strong></span>
    <small>VAN {vancouverTime}</small>
  </time>;
}

function formatTime(value: Date, timeZone: string) {
  return new Intl.DateTimeFormat("en-US", { hour: "2-digit", hour12: false, minute: "2-digit", timeZone }).format(value);
}

function formatDate(value: Date, timeZone: string, style: "full" | "short") {
  return new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", timeZone, ...(style === "full" ? { year: "numeric" } : {}) }).format(value);
}
