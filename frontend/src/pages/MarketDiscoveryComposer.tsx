import { ArrowRight, Check, Columns3, ListFilter, Plus, ScanSearch, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { InventoryFilterSelect, type InventoryFilterOption } from "../app/components/InventoryFilterSelect";

type RuleSet = { atomic?: boolean; description: string; name: string; rule_set_id: string };
type ColumnDefinition = { column_id: string; description: string; name: string; source_id: string; source_kind?: "data_definition" | "rule_set"; semantic_type: string; value_type: string };
type DataDefinition = { description: string; name: string; sortable: boolean; source_id: string; source_kind?: string; semantic_type: string };
type Composition = { columns: string[]; description: string; inclusion_operator: "all" | "any"; inclusion_rule_sets: string[]; maximum_size: number; name: string; ranking_direction: "ascending" | "descending"; ranking_field: string; refresh_interval_ms: number };
type CoreScan = Composition & { published: boolean; scan_id: string };
type Watchlist = Composition & { availability?: string; availability_detail?: string; enabled: boolean; manual_exclusions: string[]; manual_inclusions: string[]; membership_expiry: "end_of_trading_day" | "time_to_live" | "never"; membership_ttl_ms: number; origin?: string; source_scan_id: string; template?: boolean; watchlist_id: string };
export type MarketDiscoveryConfiguration = { calculation_catalog?: unknown[]; classifications?: unknown[]; column_catalog: ColumnDefinition[]; core_scan: CoreScan; field_catalog: DataDefinition[]; rule_sets: RuleSet[]; security_universe: { description: string; name: string }; watchlists: Watchlist[] };

export function MarketDiscoveryComposer({ onChange, section }: { onChange: (value: MarketDiscoveryConfiguration) => void; section: MarketDiscoveryConfiguration }) {
  const [selectedId, setSelectedId] = useState("core");
  const [query, setQuery] = useState("");
  const selectedWatchlist = section.watchlists.find((row) => row.watchlist_id === selectedId);
  const selected: Composition = selectedWatchlist ?? section.core_scan;
  const visibleWatchlists = section.watchlists.filter((row) => !query.trim() || [row.name, row.description, row.watchlist_id].some((value) => value.toLowerCase().includes(query.trim().toLowerCase())));
  const fieldById = useMemo(() => new Map(section.field_catalog.map((row) => [row.source_id, row])), [section.field_catalog]);

  function replace(next: Composition) {
    if (selectedWatchlist) onChange({ ...section, watchlists: section.watchlists.map((row) => row.watchlist_id === selectedWatchlist.watchlist_id ? { ...row, ...next } : row) });
    else onChange({ ...section, core_scan: { ...section.core_scan, ...next } });
  }

  function createWatchlist() {
    const base = "watchlist";
    let suffix = 1;
    while (section.watchlists.some((row) => row.watchlist_id === `${base}-${suffix}`)) suffix += 1;
    const watchlist_id = `${base}-${suffix}`;
    const next: Watchlist = { availability: "available", columns: [...section.core_scan.columns], description: "Compose candidate membership from registered Rule Sets and Data Definitions.", enabled: true, inclusion_operator: "all", inclusion_rule_sets: [], manual_exclusions: [], manual_inclusions: [], maximum_size: 10, membership_expiry: "end_of_trading_day", membership_ttl_ms: 300000, name: "Untitled Watchlist", origin: "user", ranking_direction: section.core_scan.ranking_direction, ranking_field: section.core_scan.ranking_field, refresh_interval_ms: section.core_scan.refresh_interval_ms, source_scan_id: section.core_scan.scan_id, template: false, watchlist_id };
    onChange({ ...section, watchlists: [...section.watchlists, next] });
    setSelectedId(watchlist_id);
  }

  return <div className="market-discovery-composer">
    <aside className="market-discovery-library">
      <header><span>Discovery definitions</span><strong>Scanner and Watchlists</strong><p>Compose higher-level discovery from registered Data Definitions and Rule Sets.</p></header>
      <label className="market-discovery-search"><Search size={15} /><input aria-label="Search Market Discovery definitions" onChange={(event) => setQuery(event.target.value)} placeholder="Search scanner and Watchlists" type="search" value={query} /></label>
      <div className="market-discovery-tree">
        <section><header><span>Core Scan</span><em>1</em></header><button aria-current={selectedId === "core"} onClick={() => setSelectedId("core")} type="button"><ScanSearch size={15} /><span><strong>{section.core_scan.name}</strong><small>{section.core_scan.columns.length} columns · {section.core_scan.inclusion_rule_sets.length} rule sets</small></span><ArrowRight size={13} /></button></section>
        <section><header><span>Watchlists</span><em>{visibleWatchlists.length}</em></header><div>{visibleWatchlists.map((row) => <button aria-current={selectedId === row.watchlist_id} key={row.watchlist_id} onClick={() => setSelectedId(row.watchlist_id)} type="button"><ListFilter size={15} /><span><strong>{row.name}</strong><small>{row.columns.length} columns · {row.inclusion_rule_sets.length} rule sets</small></span><ArrowRight size={13} /></button>)}</div></section>
      </div>
      <button className="market-discovery-create" onClick={createWatchlist} type="button"><Plus size={14} /> Create Watchlist</button>
    </aside>

    <main className="market-discovery-definition">
      <header className="market-discovery-definition-header"><div><span>{selectedWatchlist ? selectedWatchlist.template ? "Built-in Watchlist" : "Custom Watchlist" : "QMD Core Scanner"}</span><input aria-label="Discovery definition name" onChange={(event) => replace({ ...selected, name: event.target.value })} value={selected.name} /><textarea aria-label="Discovery definition description" onChange={(event) => replace({ ...selected, description: event.target.value })} rows={2} value={selected.description} /></div><div className="market-discovery-identity"><code>{selectedWatchlist?.watchlist_id ?? section.core_scan.scan_id}</code><span><Check size={13} /> Reference composition</span></div></header>

      {!selectedWatchlist ? <section className="market-discovery-source"><span>Population</span><strong>{section.security_universe.name}</strong><p>{section.security_universe.description}</p></section> : <section className="market-discovery-source"><span>Source scanner</span><strong>{section.core_scan.name}</strong><p>Membership is resolved from this scanner's candidate population.</p></section>}

      <ReferenceSection
        description="Candidates must satisfy the selected reusable Rule Sets. Removing a card removes only this reference."
        empty="No Rule Sets selected; all source candidates remain eligible."
        kind="Rule Set"
        onChange={(inclusion_rule_sets) => replace({ ...selected, inclusion_rule_sets })}
        options={ruleSetOptions(section.rule_sets, selected.inclusion_rule_sets)}
        selected={selected.inclusion_rule_sets}
        title="Eligibility rules"
      />

      <section className="market-discovery-settings">
        <header><span>Ranking and limits</span><p>Sort passing candidates with one registered Data Definition, then apply the row limit.</p></header>
        <div>
          <label><span>Ranking data definition</span><InventoryFilterSelect ariaLabel="Ranking data definition" onChange={(ranking_field) => replace({ ...selected, ranking_field })} optionLimit={0} options={rankingOptions(section.field_catalog)} presentation="catalog" searchable searchPlaceholder="Search data definitions…" showAllOnOpen value={selected.ranking_field} /></label>
          <label><span>Direction</span><select onChange={(event) => replace({ ...selected, ranking_direction: event.target.value as Composition["ranking_direction"] })} value={selected.ranking_direction}><option value="descending">Highest first</option><option value="ascending">Lowest first</option></select></label>
          <label><span>Maximum rows</span><input min="1" onChange={(event) => replace({ ...selected, maximum_size: Math.max(1, Number(event.target.value)) })} type="number" value={selected.maximum_size} /></label>
          <label><span>Refresh interval</span><div className="market-discovery-unit-input"><input min="1" onChange={(event) => replace({ ...selected, refresh_interval_ms: Math.max(1, Number(event.target.value)) })} type="number" value={selected.refresh_interval_ms} /><em>ms</em></div></label>
        </div>
      </section>

      <ReferenceSection
        description="A column can present a registered Data Definition or the boolean result of a registered Rule Set. Computation is resolved from these references."
        empty="Add at least one column to make this definition presentable in Canvas."
        kind="Column"
        onChange={(columns) => replace({ ...selected, columns })}
        options={columnOptions(section.column_catalog, selected.columns)}
        selected={selected.columns}
        title="Table columns"
      />

      {selectedWatchlist ? <section className="market-discovery-settings market-discovery-membership"><header><span>Membership lifecycle</span><p>These controls govern membership persistence; they do not create data or calculations.</p></header><div><label><span>Expiry</span><select onChange={(event) => onChange({ ...section, watchlists: section.watchlists.map((row) => row.watchlist_id === selectedWatchlist.watchlist_id ? { ...row, membership_expiry: event.target.value as Watchlist["membership_expiry"] } : row) })} value={selectedWatchlist.membership_expiry}><option value="end_of_trading_day">End of trading day</option><option value="time_to_live">Time to live</option><option value="never">No automatic expiry</option></select></label>{selectedWatchlist.membership_expiry === "time_to_live" ? <label><span>Time to live</span><div className="market-discovery-unit-input"><input min="1" onChange={(event) => onChange({ ...section, watchlists: section.watchlists.map((row) => row.watchlist_id === selectedWatchlist.watchlist_id ? { ...row, membership_ttl_ms: Math.max(1, Number(event.target.value)) } : row) })} type="number" value={selectedWatchlist.membership_ttl_ms} /><em>ms</em></div></label> : null}<label className="market-discovery-toggle"><span>Runtime enabled</span><input checked={selectedWatchlist.enabled} disabled={selectedWatchlist.availability === "integration_pending"} onChange={(event) => onChange({ ...section, watchlists: section.watchlists.map((row) => row.watchlist_id === selectedWatchlist.watchlist_id ? { ...row, enabled: event.target.checked } : row) })} type="checkbox" /></label></div>{selectedWatchlist.availability_detail ? <p className="market-discovery-availability">{selectedWatchlist.availability_detail}</p> : null}</section> : null}

      <aside className="market-discovery-resolution"><Columns3 size={17} /><span><strong>Resolved automatically</strong><small>{selected.inclusion_rule_sets.length} Rule Set reference{selected.inclusion_rule_sets.length === 1 ? "" : "s"}, {selected.columns.length} Column reference{selected.columns.length === 1 ? "" : "s"}, ranking by {fieldById.get(selected.ranking_field)?.name ?? selected.ranking_field}. QMD derives the required fields, derivations, signals, clocks, and source products from this graph.</small></span></aside>
    </main>
  </div>;
}

function ReferenceSection({ description, empty, kind, onChange, options, selected, title }: { description: string; empty: string; kind: string; onChange: (ids: string[]) => void; options: InventoryFilterOption[]; selected: string[]; title: string }) {
  const [candidate, setCandidate] = useState("");
  const available = options.filter((row) => !selected.includes(row.value));
  const byId = new Map(options.map((row) => [row.value, row]));
  return <section className="market-discovery-references"><header><div><span>{title}</span><p>{description}</p></div><div><InventoryFilterSelect ariaLabel={`${kind} to add`} onChange={setCandidate} optionLimit={0} options={available.length ? available : [{ description: `Every available ${kind.toLowerCase()} is already selected.`, label: `No available ${kind.toLowerCase()}s`, value: "" }]} placeholder={`Choose ${kind.toLowerCase()}`} presentation="catalog" searchable searchPlaceholder={`Search ${kind.toLowerCase()}s…`} showAllOnOpen value={candidate} /><button disabled={!candidate} onClick={() => { if (!candidate || selected.includes(candidate)) return; onChange([...selected, candidate]); setCandidate(""); }} type="button"><Plus size={14} /> Add</button></div></header>{selected.length ? <div className="market-discovery-reference-list">{selected.map((id) => { const option = byId.get(id); return <article key={id}><span><strong>{option?.label ?? id}</strong><small>{option?.description || id}</small><code>{id}</code></span><button aria-label={`Remove ${option?.label ?? id}`} onClick={() => onChange(selected.filter((value) => value !== id))} type="button"><Trash2 size={14} /></button></article>; })}</div> : <p className="market-discovery-empty-reference">{empty}</p>}</section>;
}

function ruleSetOptions(ruleSets: RuleSet[], selected: string[]): InventoryFilterOption[] { return ruleSets.map((row) => ({ description: row.description, group: row.atomic ? "Built-in Rule Sets" : "Custom Rule Sets", label: row.name, subgroup: row.atomic ? "Atomic definitions" : "User definitions", value: row.rule_set_id })).sort((a, b) => Number(selected.includes(b.value)) - Number(selected.includes(a.value)) || a.label.localeCompare(b.label)); }
function columnOptions(columns: ColumnDefinition[], selected: string[]): InventoryFilterOption[] { return columns.map((row) => ({ description: row.description, group: row.source_kind === "rule_set" ? "Rule Set Results" : "Data Definitions", label: row.name, subgroup: row.source_kind === "rule_set" ? "Boolean results" : readable(row.semantic_type), value: row.column_id })).sort((a, b) => Number(selected.includes(b.value)) - Number(selected.includes(a.value)) || a.label.localeCompare(b.label)); }
function rankingOptions(fields: DataDefinition[]): InventoryFilterOption[] { return fields.filter((row) => row.sortable && row.source_kind !== "rule_set").map((row) => ({ description: row.description, group: "Data Definitions", label: row.name, subgroup: readable(row.semantic_type), value: row.source_id })).sort((a, b) => a.label.localeCompare(b.label)); }
function readable(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
