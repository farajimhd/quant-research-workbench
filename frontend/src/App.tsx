import { useEffect, useState, type ReactNode } from "react";

import { Layout, type PageKey, type UiScale } from "./app/components/Layout";
import { RealLiveTradingPage } from "./pages/RealLiveTradingPage";
import { ServicesPage, type ServicePageMode } from "./pages/ServicesPage";

const validPages: PageKey[] = ["real-live-trading", "services-dashboard", "service-qmd", "service-news", "service-sec", "service-text-embed", "service-reference", "service-ibkr"];

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
    if (page !== "real-live-trading") {
      setTopbarCenter(null);
      setRealLiveScale(undefined);
    }
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
