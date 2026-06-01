import { useEffect, useState, type ReactNode } from "react";

import { Layout, type PageKey } from "./app/components/Layout";
import { LiveTradingPage } from "./pages/LiveTradingPage";
import { MarketDataBuildPage } from "./pages/MarketDataBuildPage";
import { MarketDataReviewPage } from "./pages/MarketDataReviewPage";
import { RealLiveTradingPage } from "./pages/RealLiveTradingPage";
import { ResearchRunsPage } from "./pages/ResearchRunsPage";
import { StrategyPage } from "./pages/StrategyPage";

const validPages: PageKey[] = ["strategy", "research-runs", "build-data", "review-data", "live-trading", "real-live-trading"];

export function App() {
  const [page, setPage] = useState<PageKey>(() => {
    const hash = window.location.hash.replace("#", "") as PageKey;
    return validPages.includes(hash) ? hash : "build-data";
  });
  const [visitedPages, setVisitedPages] = useState<Set<PageKey>>(() => new Set([page]));
  const [topbarCenter, setTopbarCenter] = useState<ReactNode>(null);

  useEffect(() => {
    window.location.hash = page;
    setVisitedPages((current) => {
      if (current.has(page)) return current;
      return new Set([...current, page]);
    });
  }, [page]);

  return (
    <Layout page={page} onPageChange={setPage} topbarCenter={page === "live-trading" ? topbarCenter : null}>
      <div aria-hidden={page !== "strategy"} className={page === "strategy" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "strategy" || visitedPages.has("strategy") ? <StrategyPage /> : null}
      </div>
      <div aria-hidden={page !== "research-runs"} className={page === "research-runs" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "research-runs" || visitedPages.has("research-runs") ? <ResearchRunsPage /> : null}
      </div>
      <div aria-hidden={page !== "build-data"} className={page === "build-data" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "build-data" || visitedPages.has("build-data") ? <MarketDataBuildPage /> : null}
      </div>
      <div aria-hidden={page !== "review-data"} className={page === "review-data" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "review-data" || visitedPages.has("review-data") ? <MarketDataReviewPage /> : null}
      </div>
      <div aria-hidden={page !== "live-trading"} className={page === "live-trading" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "live-trading" || visitedPages.has("live-trading") ? <LiveTradingPage onTopbarCenterChange={setTopbarCenter} /> : null}
      </div>
      <div aria-hidden={page !== "real-live-trading"} className={page === "real-live-trading" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "real-live-trading" || visitedPages.has("real-live-trading") ? <RealLiveTradingPage /> : null}
      </div>
    </Layout>
  );
}
