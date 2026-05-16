import { useCallback, useState, type ReactNode } from "react";

export function Tabs({
  tabs,
  active,
  onChange
}: {
  tabs: string[];
  active: string;
  onChange: (tab: string) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button className={tab === active ? "tab active" : "tab"} key={tab} onClick={() => onChange(tab)} type="button">
          {tab}
        </button>
      ))}
    </div>
  );
}

export function useCachedTabState(initialTab: string) {
  const [activeTab, setActiveTabRaw] = useState(initialTab);
  const [visitedTabs, setVisitedTabs] = useState<Set<string>>(() => new Set([initialTab]));

  const setActiveTab = useCallback((tab: string) => {
    setActiveTabRaw(tab);
    setVisitedTabs((current) => {
      if (current.has(tab)) return current;
      return new Set([...current, tab]);
    });
    window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  }, []);

  const isTabMounted = useCallback((tab: string) => visitedTabs.has(tab), [visitedTabs]);

  return { activeTab, isTabMounted, setActiveTab };
}

export function CachedTabPanel({
  active,
  children,
  mounted,
}: {
  active: boolean;
  children: ReactNode;
  mounted: boolean;
}) {
  if (!mounted) return null;
  return (
    <div aria-hidden={!active} className={active ? "tab-cache-panel active" : "tab-cache-panel"}>
      {children}
    </div>
  );
}
