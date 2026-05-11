import type { ReactNode } from "react";
import { Activity, BarChart3, ChevronLeft, ChevronRight, Hammer, LineChart, Palette } from "lucide-react";
import { useState } from "react";

import { buildMenuItemButtonClassName } from "../selectionStyles";

export type PageKey = "strategy" | "build-data" | "review-data";

type LayoutProps = {
  page: PageKey;
  onPageChange: (page: PageKey) => void;
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

export function Layout({ page, onPageChange, children }: LayoutProps) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className={collapsed ? "app-shell sidebar-collapsed" : "app-shell"}>
      <header className="topbar">
        <div className="topbar-brand">
          <Activity size={24} />
          <h1>Quant Research Workbench</h1>
        </div>
        <div className="topbar-actions">
          <button className="icon-button" type="button" aria-label="Theme">
            <Palette size={18} />
          </button>
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
