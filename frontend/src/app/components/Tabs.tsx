import { useCallback, useId, useState, type KeyboardEvent, type ReactNode } from "react";

export function Tabs({
  active,
  ariaLabel = "Workspace sections",
  onChange,
  tabs,
}: {
  active: string;
  ariaLabel?: string;
  onChange: (tab: string) => void;
  tabs: readonly string[];
}) {
  const generatedId = useId();

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, tab: string) {
    const currentIndex = tabs.indexOf(tab);
    if (currentIndex < 0) return;
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabs.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = tabs[nextIndex];
    onChange(nextTab);
    const tabList = event.currentTarget.closest<HTMLElement>("[role='tablist']");
    tabList?.querySelectorAll<HTMLElement>("[role='tab']")[nextIndex]?.focus();
  }

  return (
    <div aria-label={ariaLabel} className="tabs" role="tablist">
      {tabs.map((tab, index) => {
        const selected = tab === active;
        return (
          <button
            aria-selected={selected}
            className={selected ? "tab active" : "tab"}
            id={`${generatedId}-tab-${index}`}
            key={tab}
            onClick={() => onChange(tab)}
            onKeyDown={(event) => handleKeyDown(event, tab)}
            role="tab"
            tabIndex={selected ? 0 : -1}
            type="button"
          >
            {tab}
          </button>
        );
      })}
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
    <div aria-hidden={!active} className={active ? "tab-cache-panel active" : "tab-cache-panel"} hidden={!active} inert={!active} role="tabpanel">
      {children}
    </div>
  );
}
