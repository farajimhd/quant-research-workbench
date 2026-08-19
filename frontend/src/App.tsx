import { useEffect, useState, type ReactNode } from "react";

import { Layout, type PageKey } from "./app/components/Layout";
import { MarketStatusBadge, liveMarketStatus, type MarketStatus } from "./app/components/MarketStatusBadge";
import { CanvasConfigurationPage, CanvasFocusPage } from "./pages/CanvasConfigurationPage";
import { BacktestDebugPage } from "./pages/BacktestDebugPage";
import { HistoricalTradingPage } from "./pages/HistoricalTradingPage";
import { RealLiveTradingPage } from "./pages/RealLiveTradingPage";
import { ResearchWorkspacePage } from "./pages/ResearchWorkspacePage";
import { ReplayTradingPage } from "./pages/ReplayTradingPage";
import { ServicesPage, type ServicePageMode } from "./pages/ServicesPage";
import { TradingConfigurationPage, type TradingConfigurationSection } from "./pages/TradingConfigurationPage";
import { TypographyPublicSansPage } from "./pages/TypographySystemPage";

const validPages: PageKey[] = ["real-live-trading", "replay-trading", "backtest-trading", "backtest-debug", "research-workspace", "canvas-configuration", "data-catalog-configuration", "rule-set-configuration", "market-discovery-configuration", "typography-public-sans", "trading-action-configuration", "strategy-configuration", "assignment-configuration", "portfolio-configuration", "oms-configuration", "account-configuration", "revision-configuration", "canvas-focus", "services-dashboard", "service-qmd", "service-qmd-history", "service-news", "service-sec", "service-text-embed", "service-reference", "service-ibkr"];

export function App() {
  const [page, setPage] = useState<PageKey>(() => {
    return pageFromHash(window.location.hash) ?? "real-live-trading";
  });
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
    if (page !== "real-live-trading") {
      setTopbarCenter(null);
    }
  }, [page]);

  if (page === "canvas-focus") {
    return <Layout chromeless page={page} onPageChange={setPage}><CanvasFocusPage /></Layout>;
  }

  return (
    <Layout compactContent={page === "canvas-configuration" || page === "replay-trading"} page={page} onPageChange={setPage} topbarCenter={topbarCenter} topbarStatus={page === "real-live-trading" ? <MarketStatusBadge value={liveStatus} /> : null}>
      {page === "real-live-trading" ? <div className="page-cache-panel active"><RealLiveTradingPage onMarketStatusChange={setLiveStatus} onTopbarCenterChange={setTopbarCenter} /></div> : null}
      {page === "replay-trading" ? <div className="page-cache-panel active"><ReplayTradingPage /></div> : null}
      {page === "backtest-trading" ? <div className="page-cache-panel active"><HistoricalTradingPage mode="backtest" /></div> : null}
      {page === "backtest-debug" ? <div className="page-cache-panel active"><BacktestDebugPage /></div> : null}
      {page === "research-workspace" ? <div className="page-cache-panel active"><ResearchWorkspacePage /></div> : null}
      {page === "canvas-configuration" ? <div className="page-cache-panel active"><CanvasConfigurationPage /></div> : null}
      {configurationSection(page) ? (
        <div className="page-cache-panel active">
          <TradingConfigurationPage section={configurationSection(page) ?? "strategy"} />
        </div>
      ) : null}
      {page === "typography-public-sans" ? (
        <div className="page-cache-panel active">
          <TypographyPublicSansPage />
        </div>
      ) : null}
      {servicePageMode(page) ? (
        <div className="page-cache-panel active">
          <ServicesPage mode={servicePageMode(page) ?? "dashboard"} onNavigate={(mode) => setPage(pageForServiceMode(mode))} />
        </div>
      ) : null}
    </Layout>
  );
}

function pageFromHash(hash: string): PageKey | null {
  const hashPage = hash.replace(/^#/, "").split("?", 1)[0] as PageKey;
  return validPages.includes(hashPage) ? hashPage : null;
}

function configurationSection(page: PageKey): TradingConfigurationSection | null {
  if (page === "data-catalog-configuration") return "data_catalog";
  if (page === "rule-set-configuration") return "rule_sets";
  if (page === "market-discovery-configuration") return "discovery";
  if (page === "trading-action-configuration") return "actions";
  if (page === "strategy-configuration") return "strategy";
  if (page === "assignment-configuration") return "assignments";
  if (page === "portfolio-configuration") return "portfolio";
  if (page === "oms-configuration") return "oms";
  if (page === "account-configuration") return "accounts";
  if (page === "revision-configuration") return "revisions";
  return null;
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
