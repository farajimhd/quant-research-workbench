import type { ReactNode } from "react";
import { Activity, BadgeCheck, BriefcaseBusiness, Bug, Check, ChevronLeft, ChevronRight, Database, Flame, FlaskConical, GitBranch, History, Microscope, Network, PanelsTopLeft, Palette, ScanSearch, Send, ServerCog, ShieldCheck, Type, UsersRound, Wifi } from "lucide-react";
import { useEffect, useState } from "react";

import { buildMenuItemButtonClassName, buildThemeMenuItemButtonClassName } from "../selectionStyles";
import { configurationToneForPage, NAVIGATION_GROUPS, type NavigationIcon, type PageKey } from "../routes";
import { APP_THEMES, DEFAULT_THEME_ID, applyThemeDefinition, isAppThemeId, type AppThemeDefinition, type AppThemeId } from "../theme";
import { NewsIconGuide } from "./NewsIntelligenceIcon";

export type UiScale = 0.8 | 0.9 | 1 | 1.1 | 1.25;

type LayoutProps = {
  chromeless?: boolean;
  compactContent?: boolean;
  page: PageKey;
  onPageChange: (page: PageKey) => void;
  children: ReactNode;
  topbarCenter?: ReactNode;
  topbarStatus?: ReactNode;
};

const NAVIGATION_ICONS = {
  accounts: UsersRound,
  backtest: FlaskConical,
  canvas: PanelsTopLeft,
  data: Database,
  debug: Bug,
  live: Wifi,
  "market-discovery": ScanSearch,
  oms: ShieldCheck,
  portfolio: BriefcaseBusiness,
  releases: BadgeCheck,
  replay: History,
  research: Microscope,
  "run-plans": Network,
  services: ServerCog,
  strategy: GitBranch,
  "trading-actions": Send,
  typography: Type,
} satisfies Record<NavigationIcon, typeof Activity>;

const THEME_STORAGE_KEY = "quant-research-workbench.theme";
const UI_SCALE_STORAGE_KEY = "quant-research-workbench.ui-scale";
const UI_SCALE_OPTIONS = [0.8, 0.9, 1, 1.1, 1.25] as const satisfies readonly UiScale[];

export function Layout({
  children,
  chromeless = false,
  compactContent = false,
  onPageChange,
  page,
  topbarCenter,
  topbarStatus
}: LayoutProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [themeMenuOpen, setThemeMenuOpen] = useState(false);
  const [newsGuideOpen, setNewsGuideOpen] = useState(false);
  const [themeId, setThemeId] = useState<AppThemeId>(() => {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored && isAppThemeId(stored) ? stored : DEFAULT_THEME_ID;
  });
  const [uiScale, setUiScale] = useState(() => readStoredUiScale());
  const lightThemes = APP_THEMES.filter((theme) => theme.tone === "light");
  const darkThemes = APP_THEMES.filter((theme) => theme.tone === "dark");

  useEffect(() => {
    applyThemeDefinition(document.documentElement, themeId);
    window.localStorage.setItem(THEME_STORAGE_KEY, themeId);
  }, [themeId]);

  useEffect(() => {
    const readableScale = uiScale < 1 ? 1 / uiScale : 1;
    document.documentElement.style.setProperty("--app-zoom", String(uiScale));
    document.documentElement.style.setProperty("--app-zoom-inverse", String(1 / uiScale));
    document.documentElement.style.setProperty("--app-zoomed-viewport-height", `${100 / uiScale}vh`);
    document.documentElement.style.setProperty("--app-zoomed-viewport-width", `${100 / uiScale}vw`);
    document.documentElement.style.setProperty("--app-readable-scale", String(readableScale));
    document.documentElement.style.setProperty("--app-overlay-scale", String(uiScale * readableScale));
    window.localStorage.setItem(UI_SCALE_STORAGE_KEY, String(uiScale));
    window.setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
  }, [uiScale]);

  useEffect(() => {
    if (page === "real-live-trading") setCollapsed(true);
  }, [page]);

  function selectTheme(nextThemeId: AppThemeId) {
    setThemeId(nextThemeId);
    setThemeMenuOpen(false);
  }

  if (chromeless) {
    return (
      <div className="app-shell focus-app-shell">
        <a className="skip-link" href="#main-content">Skip to main content</a>
        <main className="focus-app-main" id="main-content" tabIndex={-1}>{children}</main>
      </div>
    );
  }

  return (
    <div className={collapsed ? "app-shell sidebar-collapsed" : "app-shell"}>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="topbar">
        <div className="topbar-brand">
          <Activity size={24} />
          <h1>Quant Research Workbench</h1>
        </div>
        {topbarCenter ? <div className="topbar-center">{topbarCenter}</div> : null}
        <div className="topbar-actions">
          {topbarStatus}
          <div className="theme-picker">
            <button
              aria-controls="appearance-dialog"
              aria-expanded={themeMenuOpen}
              aria-haspopup="dialog"
              className="icon-button"
              type="button"
              aria-label="Change appearance"
              onClick={() => { setNewsGuideOpen(false); setThemeMenuOpen((value) => !value); }}
            >
              <Palette size={18} />
            </button>
            {themeMenuOpen ? (
              <div aria-label="Appearance" className="theme-menu" id="appearance-dialog" role="dialog">
                <div className="theme-menu-title">Appearance</div>
                <div className="theme-scale-group" aria-label="Interface scale">
                  <div className="theme-menu-group-label">UI Scale</div>
                  <div className="theme-scale-options">
                    {UI_SCALE_OPTIONS.map((scale) => (
                      <button
                        className={scale === uiScale ? "theme-scale-button active" : "theme-scale-button"}
                        aria-pressed={scale === uiScale}
                        key={scale}
                        onClick={() => setUiScale(scale)}
                        type="button"
                      >
                        {Math.round(scale * 100)}%
                      </button>
                    ))}
                  </div>
                </div>
                <ThemeMenuGroup activeThemeId={themeId} label="Light themes" themes={lightThemes} onSelect={selectTheme} />
                <div className="theme-menu-divider" />
                <ThemeMenuGroup activeThemeId={themeId} label="Dark themes" themes={darkThemes} onSelect={selectTheme} />
              </div>
            ) : null}
          </div>
          <div className="news-guide-picker">
            <button aria-controls="news-marker-guide" aria-expanded={newsGuideOpen} aria-haspopup="dialog" aria-label="Explain news markers" className="icon-button news-guide-button" onClick={() => { setThemeMenuOpen(false); setNewsGuideOpen((value) => !value); }} title="News marker guide" type="button"><Flame size={18} /></button>
            {newsGuideOpen ? <div aria-label="News marker guide" className="news-guide-menu" id="news-marker-guide" role="dialog"><NewsIconGuide /></div> : null}
          </div>
          <div className="account-pill">Local</div>
        </div>
      </header>
      <div className="shell-body">
        <aside className="sidebar">
          <button className="collapse-button" onClick={() => setCollapsed((value) => !value)} type="button" aria-label="Toggle sidebar">
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
          <nav className="sidebar-nav">
            {NAVIGATION_GROUPS.map((group) => (
              <div className="nav-group" key={group.label}>
                {!collapsed ? <div className="nav-group-label">{group.label}</div> : null}
                <div className="nav-group-items">
                  {group.items.map((item) => {
                    const Icon = NAVIGATION_ICONS[item.icon];
                    return (
                      <a
                        aria-current={page === item.key ? "page" : undefined}
                        className={buildMenuItemButtonClassName(page === item.key)}
                        data-config-tone={configurationToneForPage(item.key)}
                        href={`#${item.key}`}
                        key={item.key}
                        onClick={(event) => {
                          event.preventDefault();
                          onPageChange(item.key);
                        }}
                        title={item.label}
                      >
                        <Icon size={17} />
                        {!collapsed ? <span>{item.label}</span> : null}
                      </a>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>
        </aside>
        <main className={compactContent ? "main compact-main" : "main"} id="main-content" tabIndex={-1}>
          <div className={compactContent ? "shell-content-inner compact-content" : "shell-content-inner"}>{children}</div>
        </main>
      </div>
    </div>
  );
}

function readStoredUiScale() {
  const stored = Number(window.localStorage.getItem(UI_SCALE_STORAGE_KEY));
  const nearest = UI_SCALE_OPTIONS.find((scale) => Math.abs(scale - stored) < 0.001);
  return nearest ?? 1;
}

function ThemeMenuGroup({
  activeThemeId,
  label,
  onSelect,
  themes
}: {
  activeThemeId: AppThemeId;
  label: string;
  onSelect: (themeId: AppThemeId) => void;
  themes: readonly AppThemeDefinition[];
}) {
  return (
    <div className="theme-menu-group">
      <div className="theme-menu-group-label">{label}</div>
      <div className="theme-menu-items">
        {themes.map((theme) => (
          <button
            aria-pressed={activeThemeId === theme.themeId}
            className={buildThemeMenuItemButtonClassName(activeThemeId === theme.themeId)}
            key={theme.themeId}
            onClick={() => onSelect(theme.themeId)}
            title={theme.description}
            type="button"
          >
            <span>{theme.label}</span>
            {activeThemeId === theme.themeId ? <Check size={15} /> : null}
          </button>
        ))}
      </div>
    </div>
  );
}
