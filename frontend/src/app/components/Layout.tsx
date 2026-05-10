import type { ReactNode } from "react";
import { BarChart3, Database, Hammer, LineChart, Menu, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useState } from "react";

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
      <aside className="sidebar">
        <div className="sidebar-top">
          <Database size={18} />
          {!collapsed ? <span>Quant Research Workbench</span> : null}
        </div>
        <button className="collapse-button" onClick={() => setCollapsed((value) => !value)} type="button" aria-label="Toggle sidebar">
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
        {navGroups.map((group) => (
          <div className="nav-group" key={group.label}>
            {!collapsed ? <div className="nav-group-label">{group.label}</div> : null}
            {group.items.map((item) => {
              const Icon = item.icon;
              return (
                <button className={page === item.key ? "nav-item active" : "nav-item"} key={item.key} onClick={() => onPageChange(item.key)} type="button" title={item.label}>
                  <Icon size={17} />
                  {!collapsed ? <span>{item.label}</span> : null}
                </button>
              );
            })}
          </div>
        ))}
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">
            <Menu size={16} />
            <span>Research workspace</span>
          </div>
          <div className="topbar-meta">Backend-owned data and workflow state</div>
        </header>
        <div className="page">{children}</div>
      </main>
    </div>
  );
}

