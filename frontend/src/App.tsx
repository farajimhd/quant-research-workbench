import { useEffect, useState, type ReactNode } from "react";

import { Layout, type PageKey, type UiScale } from "./app/components/Layout";
import { RealLiveTradingPage } from "./pages/RealLiveTradingPage";

const validPages: PageKey[] = ["real-live-trading"];

export function App() {
  const [page, setPage] = useState<PageKey>(() => {
    const hash = window.location.hash.replace("#", "") as PageKey;
    return validPages.includes(hash) ? hash : "real-live-trading";
  });
  const [visitedPages, setVisitedPages] = useState<Set<PageKey>>(() => new Set([page]));
  const [topbarCenter, setTopbarCenter] = useState<ReactNode>(null);
  const [realLiveScale, setRealLiveScale] = useState<UiScale | undefined>(undefined);

  useEffect(() => {
    window.location.hash = page;
    setVisitedPages((current) => {
      if (current.has(page)) return current;
      return new Set([...current, page]);
    });
  }, [page]);

  return (
    <Layout page={page} onPageChange={setPage} scaleOverride={realLiveScale} topbarCenter={topbarCenter}>
      <div aria-hidden={page !== "real-live-trading"} className={page === "real-live-trading" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "real-live-trading" || visitedPages.has("real-live-trading") ? <RealLiveTradingPage onScalePreferenceChange={page === "real-live-trading" ? setRealLiveScale : undefined} onTopbarCenterChange={page === "real-live-trading" ? setTopbarCenter : undefined} /> : null}
      </div>
    </Layout>
  );
}
