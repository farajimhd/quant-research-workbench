import { lazy, Suspense, useEffect, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";

import { Layout } from "./app/components/Layout";
import { MarketStatusBadge, liveMarketStatus, type MarketStatus } from "./app/components/MarketStatusBadge";
import {
  configurationSectionForPage,
  isCompactContentPage,
  pageForServiceMode,
  pageFromHash,
  servicePageModeForPage,
  type PageKey,
} from "./app/routes";

const BacktestDebugPage = lazy(() => import("./pages/BacktestDebugPage").then((module) => ({ default: module.BacktestDebugPage })));
const CanvasConfigurationPage = lazy(() => import("./pages/CanvasConfigurationPage").then((module) => ({ default: module.CanvasConfigurationPage })));
const CanvasFocusPage = lazy(() => import("./pages/CanvasConfigurationPage").then((module) => ({ default: module.CanvasFocusPage })));
const HistoricalTradingPage = lazy(() => import("./pages/HistoricalTradingPage").then((module) => ({ default: module.HistoricalTradingPage })));
const RealLiveTradingPage = lazy(() => import("./pages/RealLiveTradingPage").then((module) => ({ default: module.RealLiveTradingPage })));
const ReplayTradingPage = lazy(() => import("./pages/ReplayTradingPage").then((module) => ({ default: module.ReplayTradingPage })));
const ResearchWorkspacePage = lazy(() => import("./pages/ResearchWorkspacePage").then((module) => ({ default: module.ResearchWorkspacePage })));
const ServicesPage = lazy(() => import("./pages/ServicesPage").then((module) => ({ default: module.ServicesPage })));
const TradingConfigurationPage = lazy(() => import("./pages/TradingConfigurationPage").then((module) => ({ default: module.TradingConfigurationPage })));
const TypographyPublicSansPage = lazy(() => import("./pages/TypographySystemPage").then((module) => ({ default: module.TypographyPublicSansPage })));

export function App() {
  const [page, setPage] = useState<PageKey>(() => pageFromHash(window.location.hash) ?? "real-live-trading");
  const [topbarCenter, setTopbarCenter] = useState<ReactNode>(null);
  const [liveStatus, setLiveStatus] = useState<MarketStatus>(() => liveMarketStatus(null));

  useEffect(() => {
    const syncPageFromHash = () => {
      const hashPage = pageFromHash(window.location.hash);
      if (hashPage) setPage(hashPage);
    };
    window.addEventListener("hashchange", syncPageFromHash);
    return () => window.removeEventListener("hashchange", syncPageFromHash);
  }, []);

  useEffect(() => {
    if (pageFromHash(window.location.hash) !== page) window.location.hash = page;
    if (page !== "real-live-trading") setTopbarCenter(null);
  }, [page]);

  if (page === "canvas-focus") {
    return <Layout chromeless page={page} onPageChange={setPage}><PageSuspense><CanvasFocusPage /></PageSuspense></Layout>;
  }

  return (
    <Layout
      compactContent={isCompactContentPage(page)}
      page={page}
      onPageChange={setPage}
      topbarCenter={topbarCenter}
      topbarStatus={page === "real-live-trading" ? <MarketStatusBadge value={liveStatus} /> : null}
    >
      <PageSuspense>
        <RouteContent
          page={page}
          onMarketStatusChange={setLiveStatus}
          onPageChange={setPage}
          onTopbarCenterChange={setTopbarCenter}
        />
      </PageSuspense>
    </Layout>
  );
}

function PageSuspense({ children }: { children: ReactNode }) {
  return <Suspense fallback={<div aria-live="polite" className="page-route-loading" role="status">Loading workspace…</div>}>{children}</Suspense>;
}

function RouteContent({ onMarketStatusChange, onPageChange, onTopbarCenterChange, page }: {
  onMarketStatusChange: Dispatch<SetStateAction<MarketStatus>>;
  onPageChange: (page: PageKey) => void;
  onTopbarCenterChange: Dispatch<SetStateAction<ReactNode>>;
  page: PageKey;
}) {
  if (page === "real-live-trading") return <ActivePage><RealLiveTradingPage onMarketStatusChange={onMarketStatusChange} onTopbarCenterChange={onTopbarCenterChange} /></ActivePage>;
  if (page === "replay-trading") return <ActivePage><ReplayTradingPage /></ActivePage>;
  if (page === "backtest-trading") return <ActivePage><HistoricalTradingPage mode="backtest" /></ActivePage>;
  if (page === "backtest-debug") return <ActivePage><BacktestDebugPage /></ActivePage>;
  if (page === "research-workspace") return <ActivePage><ResearchWorkspacePage /></ActivePage>;
  if (page === "canvas-configuration") return <ActivePage><CanvasConfigurationPage /></ActivePage>;

  const configurationSection = configurationSectionForPage(page);
  if (configurationSection) return <ActivePage><TradingConfigurationPage section={configurationSection} /></ActivePage>;
  if (page === "typography-public-sans") return <ActivePage><TypographyPublicSansPage /></ActivePage>;

  const serviceMode = servicePageModeForPage(page);
  if (serviceMode) return <ActivePage><ServicesPage mode={serviceMode} onNavigate={(mode) => onPageChange(pageForServiceMode(mode))} /></ActivePage>;
  return null;
}

function ActivePage({ children }: { children: ReactNode }) {
  return <div className="page-cache-panel active">{children}</div>;
}
