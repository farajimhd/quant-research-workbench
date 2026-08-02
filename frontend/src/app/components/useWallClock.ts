import { useEffect, useState } from "react";

const WALL_CLOCK_REFRESH_MS = 60_000;

/** Current time for freshness UI. Query/replay timestamps must not be used as recency clocks. */
export function useWallClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), WALL_CLOCK_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}
