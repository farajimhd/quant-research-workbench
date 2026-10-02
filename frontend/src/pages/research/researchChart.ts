/** Resolve research episode colors through the app's semantic theme tokens. */
export function researchBandColor(color: string) {
  const token = color === "var(--danger)" ? "--danger" : "--success";
  const resolved = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  return /^#[0-9a-f]{6}$/i.test(resolved) ? `${resolved}1a` : resolved;
}

export const researchClock = (timeUs: number) => new Date(timeUs / 1000).toLocaleTimeString("en-US", {
  timeZone: "America/New_York", hour12: false,
});
