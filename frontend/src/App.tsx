import { useEffect, useState } from "react";

import { api } from "./api/client";
import { Layout, type PageKey } from "./app/components/Layout";
import { MarketDataBuildPage } from "./pages/MarketDataBuildPage";
import { MarketDataReviewPage } from "./pages/MarketDataReviewPage";
import { StrategyPage } from "./pages/StrategyPage";

const validPages: PageKey[] = ["strategy", "build-data", "review-data"];
const strategyName = "orb_5m_momentum";

type StrategyMetadata = {
  name: string;
  versions?: string[];
  default_version?: string;
};

export function App() {
  const [page, setPage] = useState<PageKey>(() => {
    const hash = window.location.hash.replace("#", "") as PageKey;
    return validPages.includes(hash) ? hash : "build-data";
  });
  const [strategyVersions, setStrategyVersions] = useState<string[]>(["v1", "v2"]);
  const [strategyVersion, setStrategyVersion] = useState("v2");

  useEffect(() => {
    window.location.hash = page;
  }, [page]);

  useEffect(() => {
    api<{ strategies: StrategyMetadata[] }>("/api/strategies").then((payload) => {
      const strategy = payload.strategies.find((item) => item.name === strategyName);
      const versions = strategy?.versions?.length ? strategy.versions : ["v1", "v2"];
      setStrategyVersions(versions);
      setStrategyVersion((current) => (versions.includes(current) ? current : strategy?.default_version ?? versions[0] ?? "v2"));
    });
  }, []);

  return (
    <Layout
      onPageChange={setPage}
      onStrategyVersionChange={setStrategyVersion}
      page={page}
      selectedStrategyVersion={strategyVersion}
      strategyVersions={strategyVersions}
    >
      {page === "strategy" ? <StrategyPage selectedVersion={strategyVersion} versions={strategyVersions} /> : null}
      {page === "build-data" ? <MarketDataBuildPage /> : null}
      {page === "review-data" ? <MarketDataReviewPage /> : null}
    </Layout>
  );
}
