import { useEffect, useState } from "react";

const WALL_CLOCK_REFRESH_MS = 60_000;

/** Current time for freshness UI. Query/replay timestamps must not be used as recency clocks. */
export function useWallClock(refreshMs = WALL_CLOCK_REFRESH_MS, enabled = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), Math.max(250, refreshMs));
    return () => window.clearInterval(timer);
  }, [enabled, refreshMs]);
  return now;
}
