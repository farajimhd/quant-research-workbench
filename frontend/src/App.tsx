import { useEffect, useState, type ReactNode } from "react";

import { Layout, type PageKey } from "./app/components/Layout";
import { CanvasConfigurationPage, CanvasFocusPage } from "./pages/CanvasConfigurationPage";
import { HistoricalTradingPage } from "./pages/HistoricalTradingPage";
import { RealLiveTradingPage } from "./pages/RealLiveTradingPage";
import { ServicesPage, type ServicePageMode } from "./pages/ServicesPage";

const validPages: PageKey[] = ["real-live-trading", "replay-trading", "backtest-trading", "canvas-configuration", "canvas-focus", "services-dashboard", "service-qmd", "service-qmd-history", "service-news", "service-sec", "service-text-embed", "service-reference", "service-ibkr"];

export function App() {
  const [page, setPage] = useState<PageKey>(() => {
    const hash = window.location.hash.replace("#", "") as PageKey;
    return validPages.includes(hash) ? hash : "real-live-trading";
  });
  const [visitedPages, setVisitedPages] = useState<Set<PageKey>>(() => new Set([page]));
  const [topbarCenter, setTopbarCenter] = useState<ReactNode>(null);

  useEffect(() => {
    const syncPageFromHash = () => {
      const hashPage = window.location.hash.replace("#", "") as PageKey;
      if (validPages.includes(hashPage)) setPage(hashPage);
    };
    window.addEventListener("hashchange", syncPageFromHash);
    return () => window.removeEventListener("hashchange", syncPageFromHash);
  }, []);

  useEffect(() => {
    if (window.location.hash !== `#${page}`) window.location.hash = page;
    if (page !== "real-live-trading") {
      setTopbarCenter(null);
    }
    setVisitedPages((current) => {
      if (current.has(page)) return current;
      return new Set([...current, page]);
    });
  }, [page]);

  if (page === "canvas-focus") {
    return <Layout chromeless page={page} onPageChange={setPage}><CanvasFocusPage /></Layout>;
  }

  return (
    <Layout compactContent={page === "canvas-configuration"} page={page} onPageChange={setPage} topbarCenter={topbarCenter}>
      <div aria-hidden={page !== "real-live-trading"} className={page === "real-live-trading" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "real-live-trading" || visitedPages.has("real-live-trading") ? <RealLiveTradingPage onTopbarCenterChange={page === "real-live-trading" ? setTopbarCenter : undefined} /> : null}
      </div>
      <div aria-hidden={page !== "replay-trading"} className={page === "replay-trading" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "replay-trading" || visitedPages.has("replay-trading") ? <HistoricalTradingPage mode="replay" /> : null}
      </div>
      <div aria-hidden={page !== "backtest-trading"} className={page === "backtest-trading" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "backtest-trading" || visitedPages.has("backtest-trading") ? <HistoricalTradingPage mode="backtest" /> : null}
      </div>
      <div aria-hidden={page !== "canvas-configuration"} className={page === "canvas-configuration" ? "page-cache-panel active" : "page-cache-panel"}>
        {page === "canvas-configuration" || visitedPages.has("canvas-configuration") ? <CanvasConfigurationPage /> : null}
      </div>
      {servicePageMode(page) ? (
        <div className="page-cache-panel active">
          <ServicesPage mode={servicePageMode(page) ?? "dashboard"} onNavigate={(mode) => setPage(pageForServiceMode(mode))} />
        </div>
      ) : null}
    </Layout>
  );
}

function servicePageMode(page: PageKey): ServicePageMode | null {
  if (page === "services-dashboard") return "dashboard";
  if (page === "service-qmd") return "qmd";
  if (page === "service-qmd-history") return "qmd-history";
  if (page === "service-news") return "news";
  if (page === "service-sec") return "sec";
  if (page === "service-text-embed") return "text-embed";
  if (page === "service-reference") return "reference";
  if (page === "service-ibkr") return "ibkr";
  return null;
}

function pageForServiceMode(mode: ServicePageMode): PageKey {
  if (mode === "dashboard") return "services-dashboard";
  return `service-${mode}` as PageKey;
}
