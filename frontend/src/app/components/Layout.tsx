import type { ReactNode } from "react";
import { Activity, BarChart3, Check, ChevronLeft, ChevronRight, Hammer, LineChart, Palette } from "lucide-react";
import { useEffect, useState } from "react";

import { buildMenuItemButtonClassName, buildThemeMenuItemButtonClassName } from "../selectionStyles";
import { APP_THEMES, DEFAULT_THEME_ID, applyThemeDefinition, isAppThemeId, type AppThemeDefinition, type AppThemeId } from "../theme";

export type PageKey = "strategy" | "build-data" | "review-data";

type LayoutProps = {
  page: PageKey;
  onPageChange: (page: PageKey) => void;
  onStrategyVersionChange: (version: string) => void;
  selectedStrategyVersion: string;
  strategyVersions: string[];
  children: ReactNode;
};

const navGroups = [
  {
    label: "Strategies",
    items: [{ key: "strategy" as PageKey, label: "ORB 5M Momentum", icon: BarChart3 }]
  },
  {
    label: "Market Data",
    items: [
      { key: "build-data" as PageKey, label: "Build Data", icon: Hammer },
      { key: "review-data" as PageKey, label: "Review Data", icon: LineChart }
    ]
  }
];

const THEME_STORAGE_KEY = "quant-research-workbench.theme";

export function Layout({
  children,
  onPageChange,
  onStrategyVersionChange,
  page,
  selectedStrategyVersion,
  strategyVersions
}: LayoutProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [themeMenuOpen, setThemeMenuOpen] = useState(false);
  const [themeId, setThemeId] = useState<AppThemeId>(() => {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored && isAppThemeId(stored) ? stored : DEFAULT_THEME_ID;
  });
  const lightThemes = APP_THEMES.filter((theme) => theme.tone === "light");
  const darkThemes = APP_THEMES.filter((theme) => theme.tone === "dark");

  useEffect(() => {
    applyThemeDefinition(document.documentElement, themeId);
    window.localStorage.setItem(THEME_STORAGE_KEY, themeId);
  }, [themeId]);

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
        <div className="topbar-actions">
          <div className="theme-picker">
            <button className="icon-button" type="button" aria-label="Change theme" onClick={() => setThemeMenuOpen((value) => !value)}>
              <Palette size={18} />
            </button>
            {themeMenuOpen ? (
              <div className="theme-menu" role="menu">
                <div className="theme-menu-title">Select Theme</div>
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
                      <div className="nav-item-wrap" key={item.key}>
                        <button
                          className={buildMenuItemButtonClassName(page === item.key)}
                          onClick={() => onPageChange(item.key)}
                          type="button"
                          title={item.label}
                        >
                          <Icon size={17} />
                          {!collapsed ? <span>{item.label}</span> : null}
                        </button>
                        {item.key === "strategy" && !collapsed && strategyVersions.length ? (
                          <div className="strategy-version-list">
                            {strategyVersions.map((version) => (
                              <button
                                className={version === selectedStrategyVersion ? "strategy-version-item active" : "strategy-version-item"}
                                key={version}
                                onClick={() => {
                                  onStrategyVersionChange(version);
                                  onPageChange("strategy");
                                }}
                                type="button"
                              >
                                {version}
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>
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
