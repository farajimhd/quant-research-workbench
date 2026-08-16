import { Activity, ArrowRight, Check, Columns3, ListFilter, Pencil, Plus, ScanSearch, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { InventoryFilterSelect, type InventoryFilterOption } from "../app/components/InventoryFilterSelect";

type RuleSet = { atomic?: boolean; description: string; name: string; rule_set_id: string };
type ColumnDefinition = { column_id: string; description: string; name: string; source_id: string; source_kind?: "data_definition" | "rule_set"; semantic_type: string; value_type: string };
type DataDefinition = { description: string; name: string; sortable: boolean; source_id: string; source_kind?: string; semantic_type: string };
type BaseComposition = { columns: string[]; description: string; inclusion_operator: "all" | "any"; inclusion_rule_sets: string[]; name: string; refresh_interval_ms: number };
type Composition = BaseComposition & { maximum_size: number; ranking_direction: "ascending" | "descending"; ranking_field: string };
type CoreScan = Composition & { published: boolean; scan_id: string };
type Watchlist = Composition & { availability?: string; availability_detail?: string; enabled: boolean; manual_exclusions: string[]; manual_inclusions: string[]; membership_expiry: "end_of_trading_day" | "time_to_live" | "never"; membership_ttl_ms: number; origin?: string; source_scan_id: string; template?: boolean; watchlist_id: string };
type SignalRoute = { membership_expiry: "end_of_trading_day" | "time_to_live" | "never"; membership_ttl_ms: number; watchlist_id: string };
type SignalStream = BaseComposition & { cooldown_ms: number; enabled: boolean; maximum_events: number; origin?: string; rearm_policy: "after_false" | "after_cooldown"; revision: number; signal_stream_id: string; source_scan_id: string; trigger_policy: "false_to_true"; watchlist_routes: SignalRoute[] };
export type MarketDiscoveryConfiguration = { calculation_catalog?: unknown[]; classifications?: unknown[]; column_catalog: ColumnDefinition[]; core_scan: CoreScan; field_catalog: DataDefinition[]; rule_sets: RuleSet[]; security_universe: { description: string; name: string }; signal_streams: SignalStream[]; watchlists: Watchlist[] };

export function MarketDiscoveryComposer({ onChange, section }: { onChange: (value: MarketDiscoveryConfiguration) => void; section: MarketDiscoveryConfiguration }) {
  const [selectedId, setSelectedId] = useState("core");
  const [query, setQuery] = useState("");
  const treeRef = useRef<HTMLDivElement>(null);
  const scrollToNewDefinitionRef = useRef(false);
  const selectedWatchlist = section.watchlists.find((row) => `watchlist:${row.watchlist_id}` === selectedId);
  const selectedSignalStream = section.signal_streams.find((row) => `signal:${row.signal_stream_id}` === selectedId);
  const selected: BaseComposition = selectedSignalStream ?? selectedWatchlist ?? section.core_scan;
  const visibleWatchlists = section.watchlists.filter((row) => !query.trim() || [row.name, row.description, row.watchlist_id].some((value) => value.toLowerCase().includes(query.trim().toLowerCase())));
  const visibleSignalStreams = section.signal_streams.filter((row) => !query.trim() || [row.name, row.description, row.signal_stream_id].some((value) => value.toLowerCase().includes(query.trim().toLowerCase())));
  const fieldById = useMemo(() => new Map(section.field_catalog.map((row) => [row.source_id, row])), [section.field_catalog]);

  useEffect(() => {
    if (!scrollToNewDefinitionRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      treeRef.current?.scrollTo({ behavior: "smooth", top: treeRef.current.scrollHeight });
      scrollToNewDefinitionRef.current = false;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [section.signal_streams.length, section.watchlists.length, selectedId]);

  function replace(next: BaseComposition & Partial<Composition>) {
    if (selectedWatchlist) onChange({ ...section, watchlists: section.watchlists.map((row) => row.watchlist_id === selectedWatchlist.watchlist_id ? { ...row, ...next } : row) });
    else if (selectedSignalStream) onChange({ ...section, signal_streams: section.signal_streams.map((row) => row.signal_stream_id === selectedSignalStream.signal_stream_id ? { ...row, ...next } : row) });
    else onChange({ ...section, core_scan: { ...section.core_scan, ...next } });
  }

  function createWatchlist() {
    const base = "watchlist";
    let suffix = 1;
    while (section.watchlists.some((row) => row.watchlist_id === `${base}-${suffix}`)) suffix += 1;
    const watchlist_id = `${base}-${suffix}`;
    const next: Watchlist = { availability: "available", columns: [...section.core_scan.columns], description: "Compose candidate membership from registered Rule Sets and Data Definitions.", enabled: true, inclusion_operator: "all", inclusion_rule_sets: [], manual_exclusions: [], manual_inclusions: [], maximum_size: 10, membership_expiry: "end_of_trading_day", membership_ttl_ms: 300000, name: "Untitled Watchlist", origin: "user", ranking_direction: section.core_scan.ranking_direction, ranking_field: section.core_scan.ranking_field, refresh_interval_ms: section.core_scan.refresh_interval_ms, source_scan_id: section.core_scan.scan_id, template: false, watchlist_id };
    scrollToNewDefinitionRef.current = true;
    setQuery("");
    onChange({ ...section, watchlists: [...section.watchlists, next] });
    setSelectedId(`watchlist:${watchlist_id}`);
  }

  function createSignalStream() {
    const base = "signal-stream";
    let suffix = 1;
    while (section.signal_streams.some((row) => row.signal_stream_id === `${base}-${suffix}`)) suffix += 1;
    const signal_stream_id = `${base}-${suffix}`;
    const next: SignalStream = { columns: [...section.core_scan.columns], cooldown_ms: 0, description: "Capture immutable occurrences when the selected Rule Sets transition from not matching to matching.", enabled: true, inclusion_operator: "all", inclusion_rule_sets: [], maximum_events: 5000, name: "Untitled Signal Stream", origin: "user", rearm_policy: "after_false", refresh_interval_ms: section.core_scan.refresh_interval_ms, revision: 1, signal_stream_id, source_scan_id: section.core_scan.scan_id, trigger_policy: "false_to_true", watchlist_routes: [] };
    scrollToNewDefinitionRef.current = true;
    setQuery("");
    onChange({ ...section, signal_streams: [...section.signal_streams, next] });
    setSelectedId(`signal:${signal_stream_id}`);
  }

  function removeWatchlist() {
    if (!selectedWatchlist || selectedWatchlist.template || selectedWatchlist.origin !== "user") return;
    if (!window.confirm(`Remove “${selectedWatchlist.name}” from this Market Discovery configuration?`)) return;
    onChange({ ...section, watchlists: section.watchlists.filter((row) => row.watchlist_id !== selectedWatchlist.watchlist_id) });
    setSelectedId("core");
  }

  function removeSignalStream() {
    if (!selectedSignalStream || selectedSignalStream.origin !== "user") return;
    if (!window.confirm(`Remove “${selectedSignalStream.name}” from this Market Discovery configuration? Existing recorded occurrences remain immutable.`)) return;
    onChange({ ...section, signal_streams: section.signal_streams.filter((row) => row.signal_stream_id !== selectedSignalStream.signal_stream_id) });
    setSelectedId("core");
  }

  function replaceSignalStream(patch: Partial<SignalStream>) {
    if (!selectedSignalStream) return;
    onChange({ ...section, signal_streams: section.signal_streams.map((row) => row.signal_stream_id === selectedSignalStream.signal_stream_id ? { ...row, ...patch } : row) });
  }

  return <div className="market-discovery-composer">
    <aside className="market-discovery-library">
      <header><span>Discovery definitions</span><strong>Scanner, Watchlists, and Signal Stream</strong><p>Watchlists show who qualifies now. Signal Stream records what happened and when.</p></header>
      <label className="market-discovery-search"><Search size={15} /><input aria-label="Search Market Discovery definitions" onChange={(event) => setQuery(event.target.value)} placeholder="Search discovery definitions" type="search" value={query} /></label>
      <div className="market-discovery-tree" ref={treeRef}>
        <section><header><span>Core Scan</span><em>1</em></header><button aria-current={selectedId === "core"} data-discovery-id="core" onClick={() => setSelectedId("core")} type="button"><ScanSearch size={15} /><span><strong>{section.core_scan.name}</strong><small>{section.core_scan.columns.length} columns · {section.core_scan.inclusion_rule_sets.length} rule sets</small></span><ArrowRight size={13} /></button></section>
        <section><header><span>Signal Stream</span><em>{visibleSignalStreams.length}</em></header><div>{visibleSignalStreams.map((row) => <button aria-current={selectedId === `signal:${row.signal_stream_id}`} data-discovery-id={row.signal_stream_id} key={row.signal_stream_id} onClick={() => setSelectedId(`signal:${row.signal_stream_id}`)} type="button"><Activity size={15} /><span><strong>{row.name}</strong><small>{row.columns.length} event columns · {row.inclusion_rule_sets.length} rule sets</small></span><ArrowRight size={13} /></button>)}</div></section>
        <section><header><span>Watchlists</span><em>{visibleWatchlists.length}</em></header><div>{visibleWatchlists.map((row) => <button aria-current={selectedId === `watchlist:${row.watchlist_id}`} data-discovery-id={row.watchlist_id} key={row.watchlist_id} onClick={() => setSelectedId(`watchlist:${row.watchlist_id}`)} type="button"><ListFilter size={15} /><span><strong>{row.name}</strong><small>{row.columns.length} columns · {row.inclusion_rule_sets.length} rule sets</small></span><ArrowRight size={13} /></button>)}</div></section>
      </div>
      <div className="market-discovery-create-stack"><button className="market-discovery-create" onClick={createWatchlist} type="button"><Plus size={14} /> Create Watchlist</button><button className="market-discovery-create" onClick={createSignalStream} type="button"><Activity size={14} /> Create Signal Stream</button></div>
    </aside>

    <main className="market-discovery-definition">
      <header className="market-discovery-definition-header"><div className="market-discovery-editable-copy"><span>{selectedSignalStream ? "Signal Stream" : selectedWatchlist ? selectedWatchlist.template ? "Built-in Watchlist" : "Custom Watchlist" : "QMD Core Scanner"}</span><em className="market-discovery-edit-hint"><Pencil size={12} /> Editable</em><input aria-label="Discovery definition name" onChange={(event) => replace({ ...selected, name: event.target.value })} value={selected.name} /><textarea aria-label="Discovery definition description" onChange={(event) => replace({ ...selected, description: event.target.value })} rows={2} value={selected.description} /></div><div className="market-discovery-identity"><code>{selectedSignalStream?.signal_stream_id ?? selectedWatchlist?.watchlist_id ?? section.core_scan.scan_id}</code><span><Check size={13} /> Reference composition</span>{selectedWatchlist?.origin === "user" && !selectedWatchlist.template ? <button aria-label={`Remove ${selectedWatchlist.name}`} className="button compact danger" onClick={removeWatchlist} type="button"><Trash2 size={13} /> Remove Watchlist</button> : null}{selectedSignalStream?.origin === "user" ? <button aria-label={`Remove ${selectedSignalStream.name}`} className="button compact danger" onClick={removeSignalStream} type="button"><Trash2 size={13} /> Remove Signal Stream</button> : null}</div></header>

      {!selectedWatchlist && !selectedSignalStream ? <section className="market-discovery-source"><span>Population</span><strong>{section.security_universe.name}</strong><p>{section.security_universe.description}</p></section> : <section className="market-discovery-source"><span>Source scanner</span><strong>{section.core_scan.name}</strong><p>{selectedSignalStream ? "Every candidate is evaluated for a transition into the configured signal state." : "Membership is resolved from this scanner's candidate population."}</p></section>}

      <ReferenceSection
        description={selectedSignalStream ? "An occurrence is appended when these reusable Rule Sets transition from not matching to matching." : "Candidates must satisfy the selected reusable Rule Sets. Removing a card removes only this reference."}
        empty={selectedSignalStream ? "Select at least one Rule Set before this stream can emit occurrences." : "No Rule Sets selected; all source candidates remain eligible."}
        kind="Rule Set"
        onChange={(inclusion_rule_sets) => replace({ ...selected, inclusion_rule_sets })}
        options={ruleSetOptions(section.rule_sets, selected.inclusion_rule_sets)}
        selected={selected.inclusion_rule_sets}
        title={selectedSignalStream ? "Signal rules" : "Eligibility rules"}
      />

      {!selectedSignalStream ? <section className="market-discovery-settings">
        <header><span>Ranking and limits</span><p>Sort passing candidates with one registered Data Definition, then apply the row limit.</p></header>
        <div>
          <label><span>Ranking data definition</span><InventoryFilterSelect ariaLabel="Ranking data definition" onChange={(ranking_field) => replace({ ...(selected as Composition), ranking_field })} optionLimit={0} options={rankingOptions(section.field_catalog)} presentation="catalog" searchable searchPlaceholder="Search data definitions…" showAllOnOpen value={(selected as Composition).ranking_field} /></label>
          <label><span>Direction</span><select onChange={(event) => replace({ ...(selected as Composition), ranking_direction: event.target.value as Composition["ranking_direction"] })} value={(selected as Composition).ranking_direction}><option value="descending">Highest first</option><option value="ascending">Lowest first</option></select></label>
          <label><span>Maximum rows</span><input min="1" onChange={(event) => replace({ ...(selected as Composition), maximum_size: Math.max(1, Number(event.target.value)) })} type="number" value={(selected as Composition).maximum_size} /></label>
          <label><span>Refresh interval</span><div className="market-discovery-unit-input"><input min="1" onChange={(event) => replace({ ...selected, refresh_interval_ms: Math.max(1, Number(event.target.value)) })} type="number" value={selected.refresh_interval_ms} /><em>ms</em></div></label>
        </div>
      </section> : null}

      <ReferenceSection
        description={selectedSignalStream ? "These values are frozen into each occurrence at trigger time and become the configured Canvas columns." : "A column can present a registered Data Definition or the boolean result of a registered Rule Set. Computation is resolved from these references."}
        empty={selectedSignalStream ? "Add event columns to preserve the relevant trigger-time evidence." : "Add at least one column to make this definition presentable in Canvas."}
        kind="Column"
        onChange={(columns) => replace({ ...selected, columns })}
        options={columnOptions(section.column_catalog, selected.columns)}
        selected={selected.columns}
        title="Table columns"
      />

      {selectedWatchlist ? <section className="market-discovery-settings market-discovery-membership"><header><span>Membership lifecycle</span><p>These controls govern membership persistence; they do not create data or calculations.</p></header><div><label><span>Expiry</span><select onChange={(event) => onChange({ ...section, watchlists: section.watchlists.map((row) => row.watchlist_id === selectedWatchlist.watchlist_id ? { ...row, membership_expiry: event.target.value as Watchlist["membership_expiry"] } : row) })} value={selectedWatchlist.membership_expiry}><option value="end_of_trading_day">End of trading day</option><option value="time_to_live">Time to live</option><option value="never">No automatic expiry</option></select></label>{selectedWatchlist.membership_expiry === "time_to_live" ? <label><span>Time to live</span><div className="market-discovery-unit-input"><input min="1" onChange={(event) => onChange({ ...section, watchlists: section.watchlists.map((row) => row.watchlist_id === selectedWatchlist.watchlist_id ? { ...row, membership_ttl_ms: Math.max(1, Number(event.target.value)) } : row) })} type="number" value={selectedWatchlist.membership_ttl_ms} /><em>ms</em></div></label> : null}<label className="market-discovery-toggle"><span>Runtime enabled</span><input checked={selectedWatchlist.enabled} disabled={selectedWatchlist.availability === "integration_pending"} onChange={(event) => onChange({ ...section, watchlists: section.watchlists.map((row) => row.watchlist_id === selectedWatchlist.watchlist_id ? { ...row, enabled: event.target.checked } : row) })} type="checkbox" /></label></div>{selectedWatchlist.availability_detail ? <p className="market-discovery-availability">{selectedWatchlist.availability_detail}</p> : null}</section> : null}

      {selectedSignalStream ? <>
        <section className="market-discovery-settings market-discovery-membership"><header><span>Occurrence behavior</span><p>Occurrences are append-only. These controls determine when a matching symbol may emit again.</p></header><div><label><span>Rearm</span><select onChange={(event) => replaceSignalStream({ rearm_policy: event.target.value as SignalStream["rearm_policy"] })} value={selectedSignalStream.rearm_policy}><option value="after_false">After rules become false</option><option value="after_cooldown">After cooldown while still true</option></select></label>{selectedSignalStream.rearm_policy === "after_cooldown" ? <label><span>Cooldown</span><div className="market-discovery-unit-input"><input min="1" onChange={(event) => replaceSignalStream({ cooldown_ms: Math.max(1, Number(event.target.value)) })} type="number" value={selectedSignalStream.cooldown_ms} /><em>ms</em></div></label> : null}<label><span>Canvas event limit</span><input min="1" onChange={(event) => replaceSignalStream({ maximum_events: Math.max(1, Number(event.target.value)) })} type="number" value={selectedSignalStream.maximum_events} /></label><label className="market-discovery-toggle"><span>Runtime enabled</span><input checked={selectedSignalStream.enabled} onChange={(event) => replaceSignalStream({ enabled: event.target.checked })} type="checkbox" /></label></div></section>
        <ReferenceSection description="Each occurrence may admit its symbol into selected Watchlists. Membership remains mutable; the occurrence remains immutable." empty="No Watchlist routing configured." kind="Watchlist" onChange={(ids) => replaceSignalStream({ watchlist_routes: ids.map((watchlist_id) => selectedSignalStream.watchlist_routes.find((route) => route.watchlist_id === watchlist_id) ?? { membership_expiry: "end_of_trading_day", membership_ttl_ms: 300000, watchlist_id }) })} options={watchlistOptions(section.watchlists, selectedSignalStream.watchlist_routes.map((route) => route.watchlist_id))} selected={selectedSignalStream.watchlist_routes.map((route) => route.watchlist_id)} title="Watchlist routing" />
      </> : null}

      <aside className="market-discovery-resolution"><Columns3 size={17} /><span><strong>Resolved automatically</strong><small>{selected.inclusion_rule_sets.length} Rule Set reference{selected.inclusion_rule_sets.length === 1 ? "" : "s"}, {selected.columns.length} Column reference{selected.columns.length === 1 ? "" : "s"}{selectedSignalStream ? ", append-only trigger-time evidence" : `, ranking by ${fieldById.get((selected as Composition).ranking_field)?.name ?? (selected as Composition).ranking_field}`}. QMD-derived fields and clocks remain the observation authority.</small></span></aside>
    </main>
  </div>;
}

function ReferenceSection({ description, empty, kind, onChange, options, selected, title }: { description: string; empty: string; kind: string; onChange: (ids: string[]) => void; options: InventoryFilterOption[]; selected: string[]; title: string }) {
  const [candidate, setCandidate] = useState("");
  const available = options.filter((row) => !selected.includes(row.value));
  const byId = new Map(options.map((row) => [row.value, row]));
  const addLabel = kind === "Rule Set" ? "Add rule set" : kind === "Watchlist" ? "Add Watchlist" : "Add column";
  return <section className="market-discovery-references">
    <header>
      <div><span>{title}</span><p>{description}</p></div>
      <small>{selected.length} selected</small>
    </header>
    <div className="market-discovery-reference-toolbar">
      <InventoryFilterSelect
        ariaLabel={`${kind} to add`}
        className="configuration-lookup-button market-discovery-reference-picker"
        onChange={setCandidate}
        optionLimit={0}
        options={available.length ? available : [{ description: `Every available ${kind.toLowerCase()} is already selected.`, label: `No available ${kind.toLowerCase()}s`, value: "" }]}
        placeholder={`Choose ${kind.toLowerCase()}`}
        presentation="catalog"
        searchable
        searchPlaceholder={`Search ${kind.toLowerCase()}s…`}
        showAllOnOpen
        value={candidate}
      />
      <button className="button compact market-discovery-reference-add" disabled={!candidate} onClick={() => { if (!candidate || selected.includes(candidate)) return; onChange([...selected, candidate]); setCandidate(""); }} type="button"><Plus size={14} /> {addLabel}</button>
    </div>
    {selected.length ? <div className="market-discovery-reference-list">{selected.map((id) => { const option = byId.get(id); return <article key={id}><span><strong>{option?.label ?? id}</strong><small>{option?.description || id}</small><code>{id}</code></span><button aria-label={`Remove ${option?.label ?? id}`} onClick={() => onChange(selected.filter((value) => value !== id))} type="button"><Trash2 size={14} /></button></article>; })}</div> : <p className="market-discovery-empty-reference">{empty}</p>}
  </section>;
}

function ruleSetOptions(ruleSets: RuleSet[], selected: string[]): InventoryFilterOption[] { return ruleSets.map((row) => ({ description: row.description, group: row.atomic ? "Built-in Rule Sets" : "Custom Rule Sets", label: row.name, subgroup: row.atomic ? "Atomic definitions" : "User definitions", value: row.rule_set_id })).sort((a, b) => Number(selected.includes(b.value)) - Number(selected.includes(a.value)) || a.label.localeCompare(b.label)); }
function watchlistOptions(watchlists: Watchlist[], selected: string[]): InventoryFilterOption[] { return watchlists.map((row) => ({ description: row.description, group: "Watchlists", label: row.name, subgroup: row.availability === "integration_pending" ? "Integration pending" : "Signal admission", value: row.watchlist_id })).sort((a, b) => Number(selected.includes(b.value)) - Number(selected.includes(a.value)) || a.label.localeCompare(b.label)); }
function columnOptions(columns: ColumnDefinition[], selected: string[]): InventoryFilterOption[] { return columns.map((row) => ({ description: row.description, group: row.source_kind === "rule_set" ? "Rule Set Results" : "Data Definitions", label: row.name, subgroup: row.source_kind === "rule_set" ? "Boolean results" : readable(row.semantic_type), value: row.column_id })).sort((a, b) => Number(selected.includes(b.value)) - Number(selected.includes(a.value)) || a.label.localeCompare(b.label)); }
function rankingOptions(fields: DataDefinition[]): InventoryFilterOption[] { return fields.filter((row) => row.sortable && row.source_kind !== "rule_set").map((row) => ({ description: row.description, group: "Data Definitions", label: row.name, subgroup: readable(row.semantic_type), value: row.source_id })).sort((a, b) => a.label.localeCompare(b.label)); }
function readable(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
