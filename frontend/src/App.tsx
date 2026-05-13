import { useEffect, useState } from "react";

import { Layout, type PageKey } from "./app/components/Layout";
import { MarketDataBuildPage } from "./pages/MarketDataBuildPage";
import { MarketDataReviewPage } from "./pages/MarketDataReviewPage";
import { StrategyPage } from "./pages/StrategyPage";

const validPages: PageKey[] = ["strategy", "build-data", "review-data"];

export function App() {
  const [page, setPage] = useState<PageKey>(() => {
    const hash = window.location.hash.replace("#", "") as PageKey;
    return validPages.includes(hash) ? hash : "build-data";
  });
  const [visitedPages, setVisitedPages] = useState<Set<PageKey>>(() => new Set([page]));

  useEffect(() => {
    window.location.hash = page;
    setVisitedPages((current) => {
      if (current.has(page)) return current;
      return new Set([...current, page]);
    });
  }, [page]);

  return (
    <Layout page={page} onPageChange={setPage}>
      <div aria-hidden={page !== "strategy"} className={page === "strategy" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "strategy" || visitedPages.has("strategy") ? <StrategyPage /> : null}
      </div>
      <div aria-hidden={page !== "build-data"} className={page === "build-data" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "build-data" || visitedPages.has("build-data") ? <MarketDataBuildPage /> : null}
      </div>
      <div aria-hidden={page !== "review-data"} className={page === "review-data" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "review-data" || visitedPages.has("review-data") ? <MarketDataReviewPage /> : null}
      </div>
    </Layout>
  );
}
