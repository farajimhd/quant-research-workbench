/** Convert a wall-clock date/time in an IANA zone into its UTC instant. */
export function dateInTimeZone(date: string, time: string, timeZone: string) {
  const [year, month, day] = date.split("-").map(Number);
  const [hour, minute, second = 0] = time.split(":").map(Number);
  const desiredUtc = Date.UTC(year, month - 1, day, hour, minute, second);
  let instant = new Date(desiredUtc);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      day: "2-digit",
      hour: "2-digit",
      hourCycle: "h23",
      minute: "2-digit",
      month: "2-digit",
      second: "2-digit",
      timeZone,
      year: "numeric",
    }).formatToParts(instant).filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)]));
    const representedUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
    instant = new Date(instant.getTime() + desiredUtc - representedUtc);
  }
  return instant;
}
