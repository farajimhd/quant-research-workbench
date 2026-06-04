import type { ReactNode } from "react";
import { Activity, BarChart3, Check, ChevronLeft, ChevronRight, GitCompareArrows, Hammer, LineChart, Palette, RadioTower, Wifi } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { buildMenuItemButtonClassName, buildThemeMenuItemButtonClassName } from "../selectionStyles";
import { APP_THEMES, DEFAULT_THEME_ID, applyThemeDefinition, isAppThemeId, type AppThemeDefinition, type AppThemeId } from "../theme";

export type PageKey = "strategy" | "research-runs" | "build-data" | "review-data" | "live-trading" | "real-live-trading";
export type UiScale = 0.8 | 0.9 | 1 | 1.1 | 1.25;

type LayoutProps = {
  page: PageKey;
  onPageChange: (page: PageKey) => void;
  children: ReactNode;
  scaleOverride?: UiScale;
  topbarCenter?: ReactNode;
};

const navGroups = [
  {
    label: "Research",
    items: [
      { key: "strategy" as PageKey, label: "Backtest", icon: BarChart3 },
      { key: "research-runs" as PageKey, label: "Run Comparison", icon: GitCompareArrows }
    ]
  },
  {
    label: "Market Data",
    items: [
      { key: "build-data" as PageKey, label: "Build Data", icon: Hammer },
      { key: "review-data" as PageKey, label: "Review Data", icon: LineChart }
    ]
  },
  {
    label: "Live Trading",
    items: [
      { key: "live-trading" as PageKey, label: "Semi-Auto", icon: RadioTower },
      { key: "real-live-trading" as PageKey, label: "Live", icon: Wifi }
    ]
  }
];

const THEME_STORAGE_KEY = "quant-research-workbench.theme";
const UI_SCALE_STORAGE_KEY = "quant-research-workbench.ui-scale";
const UI_SCALE_OPTIONS = [0.8, 0.9, 1, 1.1, 1.25] as const satisfies readonly UiScale[];

export function Layout({
  children,
  onPageChange,
  page,
  scaleOverride,
  topbarCenter
}: LayoutProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [themeMenuOpen, setThemeMenuOpen] = useState(false);
  const lastAppliedScaleOverrideRef = useRef<UiScale | undefined>(undefined);
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
    if (scaleOverride === undefined) {
      lastAppliedScaleOverrideRef.current = undefined;
      return;
    }
    if (scaleOverride === lastAppliedScaleOverrideRef.current) return;
    lastAppliedScaleOverrideRef.current = scaleOverride;
    setUiScale(scaleOverride);
  }, [scaleOverride]);

  useEffect(() => {
    document.documentElement.style.setProperty("--app-zoom", String(uiScale));
    document.documentElement.style.setProperty("--app-zoom-inverse", String(1 / uiScale));
    document.documentElement.style.setProperty("--app-zoomed-viewport-height", `${100 / uiScale}vh`);
    document.documentElement.style.setProperty("--app-zoomed-viewport-width", `${100 / uiScale}vw`);
    document.documentElement.style.setProperty("--app-readable-scale", String(uiScale < 1 ? 1 / uiScale : 1));
    window.localStorage.setItem(UI_SCALE_STORAGE_KEY, String(uiScale));
    window.setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
  }, [uiScale]);

  useEffect(() => {
    if (page === "live-trading" || page === "real-live-trading") setCollapsed(true);
  }, [page]);

  function selectTheme(nextThemeId: AppThemeId) {
    setThemeId(nextThemeId);
    setThemeMenuOpen(false);
  }

  return (
    <div className={collapsed ? "app-shell sidebar-collapsed" : "app-shell"}>
      <header className="topbar">
        <div className="topbar-brand">
          <Activity size={24} />
          <h1>Quant Research Workbench</h1>
        </div>
        {topbarCenter ? <div className="topbar-center">{topbarCenter}</div> : null}
        <div className="topbar-actions">
          <div className="theme-picker">
            <button className="icon-button" type="button" aria-label="Change theme" onClick={() => setThemeMenuOpen((value) => !value)}>
              <Palette size={18} />
            </button>
            {themeMenuOpen ? (
              <div className="theme-menu" role="menu">
                <div className="theme-menu-title">Appearance</div>
                <div className="theme-scale-group" aria-label="Interface scale">
                  <div className="theme-menu-group-label">UI Scale</div>
                  <div className="theme-scale-options">
                    {UI_SCALE_OPTIONS.map((scale) => (
                      <button
                        className={scale === uiScale ? "theme-scale-button active" : "theme-scale-button"}
                        key={scale}
                        onClick={() => setUiScale(scale)}
                        type="button"
                      >
                        {Math.round(scale * 100)}%
                      </button>
                    ))}
                  </div>
                </div>
                <div className="theme-menu-divider" />
                <ThemeMenuGroup activeThemeId={themeId} label="Light themes" themes={lightThemes} onSelect={selectTheme} />
                <div className="theme-menu-divider" />
                <ThemeMenuGroup activeThemeId={themeId} label="Dark themes" themes={darkThemes} onSelect={selectTheme} />
              </div>
            ) : null}
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
            {navGroups.map((group) => (
              <div className="nav-group" key={group.label}>
                {!collapsed ? <div className="nav-group-label">{group.label}</div> : null}
                <div className="nav-group-items">
                  {group.items.map((item) => {
                    const Icon = item.icon;
                    return (
                      <button
                        className={buildMenuItemButtonClassName(page === item.key)}
                        key={item.key}
                        onClick={() => onPageChange(item.key)}
                        type="button"
                        title={item.label}
                      >
                        <Icon size={17} />
                        {!collapsed ? <span>{item.label}</span> : null}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>
        </aside>
        <main className="main">
          <div className="shell-content-inner">{children}</div>
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
