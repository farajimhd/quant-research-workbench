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

