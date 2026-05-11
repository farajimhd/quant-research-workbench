import { useEffect, useState } from "react";

import { Layout, type PageKey } from "./app/components/Layout";
import { applyThemeDefinition } from "./app/theme";
import { MarketDataBuildPage } from "./pages/MarketDataBuildPage";
import { MarketDataReviewPage } from "./pages/MarketDataReviewPage";
import { StrategyPage } from "./pages/StrategyPage";

const validPages: PageKey[] = ["strategy", "build-data", "review-data"];

export function App() {
  const [page, setPage] = useState<PageKey>(() => {
    const hash = window.location.hash.replace("#", "") as PageKey;
    return validPages.includes(hash) ? hash : "build-data";
  });

  useEffect(() => {
    window.location.hash = page;
  }, [page]);

  useEffect(() => {
    applyThemeDefinition();
  }, []);

  return (
    <Layout page={page} onPageChange={setPage}>
      {page === "strategy" ? <StrategyPage /> : null}
      {page === "build-data" ? <MarketDataBuildPage /> : null}
      {page === "review-data" ? <MarketDataReviewPage /> : null}
    </Layout>
  );
}
