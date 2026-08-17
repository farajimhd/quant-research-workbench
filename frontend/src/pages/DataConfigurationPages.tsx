import { ChevronRight, Copy, Database, LockKeyhole, Plus, Search, TimerReset, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import type { InformationRegistry, RegistryDefinition } from "../app/components/DefinitionRegistry";
import { InventoryFilterSelect, type InventoryFilterOption } from "../app/components/InventoryFilterSelect";
import { IntervalSelect, intervalLabel, preferredInterval, type IntervalSpec, type IntervalValue } from "../app/components/IntervalSelect";

export type DataRuleCondition = {
  comparator: string;
  condition_id: string;
  enabled: boolean;
  left_source_id: string;
  left_field_ref?: string;
  left_interval?: IntervalValue;
  left_aggregation?: string;
  right_source_id: string;
  right_field_ref?: string;
  right_interval?: IntervalValue;
  right_aggregation?: string;
  value: boolean | number | string | null;
};

export type AtomicField = {
  available_at?: string;
  atomic_field_id: string;
  calculation_summary?: string;
  description: string;
  entity_grain?: string;
  event_at?: string;
  group: string;
  historical_support?: string;
  known_values?: KnownValue[];
  modes?: string[];
  name: string;
  null_reasons?: string[];
  owner: string;
  provenance?: string;
  query_plan_id?: string;
  source_columns?: string[];
  source_path: string;
  source_summary?: string;
  status?: string;
  update_cadence?: string;
  value_type: string;
  unit: string;
};

type KnownValue = { description: string; label: string; value: boolean | number | string };
type ValueDomain = { allowed_values?: KnownValue[]; closed: boolean; kind: "boolean" | "date" | "enum" | "number" | "structured" | "text" | "time" | "timestamp"; unit?: string };

export type DataFieldOutput = {
  column_presentations: Array<{ default_visible?: boolean; label: string; presentation_id: string }>;
  chart_presentations: Array<{ default_visible?: boolean; label: string; presentation_id: string; render_type: string }>;
  field_ref: string;
  name: string;
  output_id: string;
  source_id: string;
  value_type: string;
  unit: string;
  value_domain?: ValueDomain;
};

export type DataFieldDefinition = {
  calculation?: { documentation_status?: string; formula?: string; kind?: string; summary?: string };
  category?: string;
  configurable: boolean;
  context: {
    allowed_scopes?: string[];
    anchor?: string;
    as_of?: string;
    available_intervals?: string[];
    dimension_kind: "anchored" | "as_of" | "interval" | "point_in_time" | "rolling_window";
    execution_scope: string;
    interval?: string;
    update_cadence?: string;
    window?: string;
    window_configurable?: boolean;
    interval_semantics?: "bar_timeframe" | "event_window" | string;
    aggregation?: { allowed?: string[]; default?: string; function?: string; mode?: "intrinsic" | "none" | "required" };
  };
  data_field_id: string;
  description: string;
  enabled: boolean;
  execution?: { aggregation_runtime_fields?: Record<string, string>; market_discovery_supported?: boolean; producer_intervals?: string[] };
  inputs: string[];
  name: string;
  known_values?: KnownValue[];
  owner?: string;
  outputs: DataFieldOutput[];
  parameters: Record<string, unknown>;
  recipe_id: string;
  revision: number;
  source?: { available_at?: string; location?: string; owner?: string; query_plan_id?: string; source_fields?: string[]; summary?: string };
  implementation_status?: string;
  historical_support?: boolean;
  live_support?: boolean;
  policies?: { gaps?: string; late_events?: string; missing?: string; warm_up_bars?: number | null };
};

export type DataRuleSet = {
  atomic?: boolean;
  conditions: DataRuleCondition[];
  description: string;
  editable?: boolean;
  enabled: boolean;
  name: string;
  operator: "all" | "any" | "score";
  origin?: string;
  protected?: boolean;
  publication_status?: string;
  required_score: number;
  revision?: number;
  rule_set_id: string;
  scope?: "shared" | "strategy" | "watchlist";
};

const DATA_KINDS = new Set(["field", "derivation", "signal"]);
export type RuleFieldDefinition = RegistryDefinition & { data_field_context?: DataFieldDefinition["context"]; field_ref?: string; source_id?: string; value_domain?: ValueDomain };

export function dataFieldRuleDefinitions(dataFields: DataFieldDefinition[], enabledOnly = true): RuleFieldDefinition[] {
  return dataFields.filter((dataField) => !enabledOnly || dataField.enabled).flatMap((dataField) => dataField.outputs.map((output) => ({
    configurable: dataField.configurable,
    configuration_mode: dataField.configurable ? "editable" : "reference",
    description: `${dataField.description} Output: ${output.name}.`,
    documentation: {
      available_when: "When the registered Data Field context is satisfied.",
      calculation_summary: dataField.description,
      documentation_status: "complete" as const,
      entity_grain: "security_at_market_clock",
      freshness_summary: dataField.context.update_cadence || "Producer cadence",
      input_field_ids: dataField.inputs,
      null_behavior: "Unavailable values remain explicit.",
      source_summary: dataField.recipe_id,
      timeframes: dataField.context.available_intervals ?? [],
      unit: output.unit,
      update_cadence: dataField.context.update_cadence || "Producer cadence",
      value_type: output.value_type,
    },
    field_ref: output.field_ref,
    data_field_context: dataField.context,
    kind: output.value_type === "boolean" ? "signal" : "field",
    label: output.name,
    owner: "data_field_registry",
    presentation: { accent: "teal", icon: "database", kind_label: "Data Field output" },
    presentation_label: output.name,
    registry_id: output.field_ref,
    source_id: output.source_id,
    status: dataField.enabled ? "implemented" : "disabled",
    tags: [dataField.category || "Data Field", dataField.recipe_id, ...fieldDimensionTags(dataField.context)],
    value_domain: output.value_domain ?? inferValueDomain(output.value_type, output.unit, dataField.known_values ?? []),
    version: dataField.revision,
  })));
}

export function DataCatalogPage({ atomicFields = [], dataFields = [], onDataFieldsChange, registry }: { atomicFields?: AtomicField[]; dataFields?: DataFieldDefinition[]; onDataFieldsChange?: (value: DataFieldDefinition[]) => void; registry: InformationRegistry }) {
  if (atomicFields.length || dataFields.length) return <DataFieldCatalog atomicFields={atomicFields} dataFields={dataFields} />;
  return <LegacyDataCatalog registry={registry} />;
}

function LegacyDataCatalog({ registry }: { registry: InformationRegistry }) {
  const definitions = useMemo(() => registry.definitions.filter((row) => DATA_KINDS.has(row.kind)), [registry.definitions]);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(() => definitions[0]?.registry_id ?? "");
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return definitions.filter((row) => !needle || [displayLabel(row), row.label, row.description, row.registry_id, row.owner, row.kind, ...row.tags].some((value) => value.toLowerCase().includes(needle)));
  }, [definitions, query]);
  const groups = useMemo(() => groupDefinitions(visible), [visible]);
  const selected = definitions.find((row) => row.registry_id === selectedId) ?? visible[0] ?? definitions[0];

  return <div className="data-library-workbench">
    <aside className="data-library-catalog">
      <header><span>Registered data definitions</span><strong>{visible.length} of {definitions.length}</strong><p>Read-only semantic authority used by rules, discovery, strategies, tables, and charts.</p></header>
      <label className="data-library-search"><Search size={15} /><input aria-label="Search data definitions" onChange={(event) => setQuery(event.target.value)} placeholder="Search names, IDs, types, and uses" type="search" value={query} /></label>
      <div className="data-library-tree">
        {[...groups.entries()].map(([groupName, subgroups]) => <details key={groupName} open>
          <summary><span>{groupName}</span><em>{[...subgroups.values()].reduce((sum, rows) => sum + rows.length, 0)}</em></summary>
          {[...subgroups.entries()].map(([subgroupName, rows]) => <details className="data-library-subgroup" key={subgroupName} open>
            <summary><span>{subgroupName}</span><em>{rows.length}</em></summary>
            <div>{rows.map((row) => <button aria-current={selected?.registry_id === row.registry_id ? "true" : undefined} key={row.registry_id} onClick={() => setSelectedId(row.registry_id)} type="button"><span><strong>{displayLabel(row)}</strong><small>{row.registry_id}</small></span><ChevronRight size={13} /></button>)}</div>
          </details>)}
        </details>)}
      </div>
    </aside>
    <main className="data-library-detail">{selected ? <DataDefinitionDetail definition={selected} onNavigate={setSelectedId} registry={registry} /> : <div className="data-library-empty"><Database size={22} /><span>No registered definition matches this search.</span></div>}</main>
  </div>;
}

function DataFieldCatalog({ atomicFields, dataFields }: { atomicFields: AtomicField[]; dataFields: DataFieldDefinition[] }) {
  const [kind, setKind] = useState<"atomic" | "data">("data");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const dataFamilies = dataFieldFamilies(dataFields);
  const conceptualDataFields = [...dataFamilies.values()].map(preferredDataFieldVariant);
  const rows: Array<AtomicField | DataFieldDefinition> = kind === "atomic" ? atomicFields : conceptualDataFields;
  const needle = query.trim().toLowerCase();
  const visible = rows.filter((row) => !needle || catalogMatchesSearch(row, "data_field_id" in row ? dataFamilies.get(row.outputs[0]?.source_id ?? "") ?? [row] : [row], needle));
  const definitionByRef = new Map(dataFieldRuleDefinitions(dataFields, false).map((definition) => [definition.registry_id, definition]));
  const groups = groupCatalogFields(visible, definitionByRef);
  const selectedBase = visible.find((row) => {
    if ("atomic_field_id" in row) return row.atomic_field_id === selectedId;
    return row.data_field_id === selectedId || dataFamilies.get(row.outputs[0]?.source_id ?? "")?.some((variant) => variant.data_field_id === selectedId);
  }) ?? [...groups.values()][0]?.values().next().value?.[0];
  const selectedVariants = selectedBase && "data_field_id" in selectedBase ? dataFamilies.get(selectedBase.outputs[0]?.source_id ?? "") ?? [selectedBase] : [];
  const selected = selectedBase && "data_field_id" in selectedBase
    ? selectedVariants.find((variant) => variant.data_field_id === selectedId) ?? preferredDataFieldVariant(selectedVariants)
    : selectedBase;

  return <div className="data-library-workbench data-field-catalog">
    <aside className="data-library-catalog">
      <header>
        <span>Data Catalog</span>
        <strong>{visible.length} of {rows.length}</strong>
        <p>Atomic Fields are source-owned observations. Data Fields expose only the dimensions that change their meaning.</p>
      </header>
      <div className="data-catalog-kind-switch" role="tablist">
        <button aria-selected={kind === "atomic"} onClick={() => { setKind("atomic"); setSelectedId(""); }} role="tab" type="button"><span>Atomic Fields</span><em>{atomicFields.length}</em></button>
        <button aria-selected={kind === "data"} onClick={() => { setKind("data"); setSelectedId(""); }} role="tab" type="button"><span>Data Fields</span><em>{conceptualDataFields.length}</em></button>
      </div>
      <label className="data-library-search"><Search size={15} /><input aria-label="Search Data Catalog" onChange={(event) => setQuery(event.target.value)} placeholder="Search names, IDs, owners, recipes" type="search" value={query} /></label>
      <div className="data-library-tree">
        {[...groups.entries()].map(([group, subgroups]) => <details key={group} open>
          <summary><span>{group}</span><em>{[...subgroups.values()].reduce((count, rows) => count + rows.length, 0)}</em></summary>
          {[...subgroups.entries()].map(([subgroup, groupRows]) => <details className="data-library-subgroup" key={`${group}:${subgroup}`} open>
          <summary><span>{subgroup}</span><em>{groupRows.length}</em></summary>
          <div className="data-library-entry-list">{groupRows.map((row) => {
            const id = "atomic_field_id" in row ? row.atomic_field_id : row.data_field_id;
            const context = "data_field_id" in row
              ? [row.category, fieldDimensionSummary(row.context, (dataFamilies.get(row.outputs[0]?.source_id ?? "") ?? []).length)].filter(Boolean).join(" · ")
              : `${readable(row.group)} · ${readable(row.owner)}`;
            const active = "atomic_field_id" in row
              ? selected === row
              : Boolean(selected && "data_field_id" in selected && selected.outputs[0]?.source_id === row.outputs[0]?.source_id);
            return <button aria-current={active ? "true" : undefined} key={id} onClick={() => setSelectedId(id)} type="button">
              <span><strong>{"data_field_id" in row ? row.outputs[0]?.name || row.name : row.name}</strong><small>{context}</small><code>{"data_field_id" in row ? row.outputs[0]?.source_id || id : id}</code></span><ChevronRight size={13} />
            </button>;
          })}</div></details>)}
        </details>)}
      </div>
    </aside>
    <main className="data-library-detail">{selected ? "atomic_field_id" in selected
      ? <AtomicFieldDetail field={selected} />
      : <DataFieldDetail field={selected} />
      : <div className="data-library-empty"><Database size={22} /><span>No catalog entry matches this search.</span></div>}</main>
  </div>;
}

function catalogSearchText(row: AtomicField | DataFieldDefinition, variants: Array<AtomicField | DataFieldDefinition>) {
  const values = variants.flatMap((variant) => "atomic_field_id" in variant
    ? [variant.atomic_field_id, variant.name, variant.description, variant.group, variant.owner, variant.source_path, variant.source_summary, variant.calculation_summary, variant.value_type, variant.unit, ...(variant.source_columns ?? []), ...(variant.known_values ?? []).flatMap((item) => [String(item.value), item.label, item.description])]
    : [variant.data_field_id, variant.name, variant.description, variant.category, variant.owner, variant.recipe_id, variant.implementation_status, variant.source?.owner, variant.source?.location, variant.source?.query_plan_id, variant.source?.summary, variant.calculation?.kind, variant.calculation?.summary, variant.calculation?.formula, ...variant.inputs, ...(variant.source?.source_fields ?? []), ...variant.outputs.flatMap((output) => [output.field_ref, output.source_id, output.name, output.value_type, output.unit]), ...(variant.known_values ?? []).flatMap((item) => [String(item.value), item.label, item.description])]);
  return values.filter((value) => value !== undefined && value !== null).join(" ").toLowerCase();
}

function catalogMatchesSearch(row: AtomicField | DataFieldDefinition, variants: Array<AtomicField | DataFieldDefinition>, query: string) {
  const words = catalogSearchText(row, variants).split(/[^a-z0-9]+/).filter(Boolean);
  const terms = query.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  return terms.every((term) => words.some((word) => word.startsWith(term)));
}

function AtomicFieldDetail({ field }: { field: AtomicField }) {
  return <article className="data-definition-document data-field-document">
    <header><span>Atomic Field · source authority</span><h2>{field.name}</h2><p>{field.description}</p><div className="data-definition-identity"><code>{field.atomic_field_id}</code><em>{field.value_type} · {field.unit}</em><em>{readable(field.status || "implemented")}</em></div></header>
    <section className="data-definition-method data-field-method">
      <article className="data-definition-source"><span>Source</span><h3 title={field.source_path}>{field.source_path}</h3><p>{field.source_summary || `Published by ${readable(field.owner)}.`}</p><dl className="data-field-method-facts"><div><dt>Owner</dt><dd>{readable(field.owner)}</dd></div><div><dt>Query plan</dt><dd><code>{field.query_plan_id || "Source-owned"}</code></dd></div>{field.source_columns?.length ? <div><dt>Source columns</dt><dd>{field.source_columns.join(", ")}</dd></div> : null}</dl></article>
      <article className="data-definition-operation"><span>Source handling</span><p className="data-field-calculation-summary">{field.calculation_summary || "No application-side calculation is applied to this source-owned observation."}</p><dl className="data-field-method-facts"><div><dt>Event clock</dt><dd>{field.event_at || "Source effective timestamp"}</dd></div><div><dt>Available at</dt><dd>{field.available_at || "Source publication timestamp"}</dd></div><div><dt>Entity grain</dt><dd>{readable(field.entity_grain || "source grain")}</dd></div></dl></article>
    </section>
    <section><h3>Availability and quality</h3><div className="data-field-context-grid"><FieldFact label="Update cadence" value={readable(field.update_cadence || "source cadence")} detail="How often the source may publish a new observation." /><FieldFact label="Historical support" value={readable(field.historical_support || "source policy")} detail="The registered point-in-time history contract." /><FieldFact label="Null reasons" value={String(field.null_reasons?.length ?? 0)} detail={field.null_reasons?.map(readable).join(", ") || "No additional reasons registered."} /></div></section>
    <KnownValues values={field.known_values ?? []} />
  </article>;
}

function DataFieldDetail({ field }: { field: DataFieldDefinition }) {
  const source = field.source ?? {};
  const calculation = field.calculation ?? {};
  return <article className="data-definition-document data-field-document">
    <header><span>Data Field · revision {field.revision}</span><h2>{field.outputs[0]?.name || field.name}</h2><p>{field.description}</p><div className="data-definition-identity"><code>{field.data_field_id}</code><em>{field.recipe_id}</em><em>{readable(field.implementation_status || "unknown")}</em></div></header>
    <section className="data-definition-method data-field-method">
      <article className="data-definition-source"><span>Source</span><h3 title={source.location || readable(source.owner || field.owner || "unknown")}>{source.location || readable(source.owner || field.owner || "Registered producer")}</h3><p>{source.summary || "Registered producer source."}</p><dl className="data-field-method-facts"><div><dt>Owner</dt><dd>{readable(source.owner || field.owner || "unknown")}</dd></div><div><dt>Query plan</dt><dd><code>{source.query_plan_id || "Producer-owned"}</code></dd></div><div><dt>Source fields</dt><dd>{source.source_fields?.join(", ") || field.inputs.join(", ") || "Producer-defined"}</dd></div><div><dt>Available at</dt><dd>{source.available_at || "Producer publication clock"}</dd></div></dl></article>
      <article className="data-definition-operation"><span>Calculation</span><p className="data-field-calculation-summary">{calculation.summary || field.description}</p>{calculation.formula ? <div className="data-definition-formula"><span>Expression</span><code>{calculation.formula}</code></div> : null}<dl className="data-field-method-facts"><div><dt>Operation</dt><dd>{readable(calculation.kind || "registered calculation")}</dd></div><div><dt>Documentation</dt><dd>{readable(calculation.documentation_status || "complete")}</dd></div></dl></article>
    </section>
    <section><h3>Computation contract</h3><div className="data-field-context-grid"><FieldFact label="Atomic inputs" value={String(field.inputs.length)} detail={field.inputs.join(", ") || "No additional atomic input."} /><FieldFact label="Execution scope" value={readable(field.context.execution_scope)} detail={field.context.allowed_scopes?.map(readable).join(", ") || "Exact registered scope"} />{fieldContextCards(field.context).map((card) => <FieldFact detail={card.detail} key={card.label} label={card.label} value={card.value} />)}{field.context.aggregation?.mode === "required" ? <FieldFact label="Window aggregation" value="Selected in Rule Set" detail={(field.context.aggregation.allowed ?? []).map(aggregationLabel).join(", ")} /> : field.context.aggregation?.mode === "intrinsic" ? <FieldFact label="Bar aggregation" value={aggregationLabel(field.context.aggregation.function || "")} detail="Intrinsic to this bar output; the Rule Set selects only the bar timeframe." /> : null}<FieldFact label="Rule Set support" value={field.execution?.market_discovery_supported === false ? "Catalog only" : "Available"} detail={field.execution?.market_discovery_supported === false ? "The source is registered, but no Scanner/Watchlist execution projection is registered yet; it is not offered in Rule Sets." : "This Data Field has an executable Market Discovery projection and can be instantiated in Rule Sets."} /><FieldFact label="Runtime support" value={`${field.live_support === false ? "No live" : "Live"} · ${field.historical_support === false ? "No history" : "History"}`} detail={`Missing values: ${readable(field.policies?.missing || "unavailable")}; gaps: ${readable(field.policies?.gaps || "preserve")}.`} /></div></section>
    <section><div className="data-field-section-heading"><div><h3>Outputs and presentations</h3><p>Rule Sets address the immutable output identity. Canvas uses its registered column or chart presentation.</p></div><em>{field.outputs.length} output{field.outputs.length === 1 ? "" : "s"}</em></div><div className="data-field-output-list">{field.outputs.map((output) => <article key={output.field_ref}><header><div><strong>{output.name}</strong><span>{output.value_type} · {output.unit}</span></div><div><em>{output.column_presentations.length} column</em><em>{output.chart_presentations.length} chart</em></div></header><dl><div><dt>Rule Set reference</dt><dd><code>{output.field_ref}</code></dd></div><div><dt>Producer output</dt><dd><code>{output.source_id}</code></dd></div></dl></article>)}</div></section>
    <KnownValues values={field.known_values ?? []} />
  </article>;
}

function FieldFact({ detail, label, value }: { detail: string; label: string; value: string }) { return <article><span>{label}</span><strong>{value}</strong><p>{detail}</p></article>; }

function KnownValues({ values }: { values: KnownValue[] }) {
  if (!values.length) return null;
  return <section className="data-field-known-values"><div className="data-field-section-heading"><div><h3>Known values</h3><p>Registered categorical values and their operational meaning.</p></div><em>{values.length} values</em></div><table><thead><tr><th>Value</th><th>Label</th><th>Meaning</th></tr></thead><tbody>{values.map((item) => <tr key={String(item.value)}><td><code>{String(item.value)}</code></td><td><strong>{item.label}</strong></td><td>{item.description}</td></tr>)}</tbody></table></section>;
}

function dataFieldFamilies(dataFields: DataFieldDefinition[]) {
  const families = new Map<string, DataFieldDefinition[]>();
  dataFields.forEach((field) => {
    const sourceId = field.outputs[0]?.source_id || field.data_field_id;
    const current = families.get(sourceId) ?? [];
    if (!current.some((row) => row.data_field_id === field.data_field_id)) families.set(sourceId, [...current, field]);
  });
  return families;
}

function preferredDataFieldVariant(variants: DataFieldDefinition[]) {
  return variants[0];
}

function fieldDimensionTags(context?: DataFieldDefinition["context"]) {
  if (!context) return [];
  return [...(context.available_intervals ?? []), context.window, context.anchor, context.as_of].filter((value): value is string => Boolean(value));
}

function fieldDimensionSummary(context: DataFieldDefinition["context"], variantCount = 1) {
  if (context.dimension_kind === "interval") return context.interval_semantics === "event_window" ? context.aggregation?.mode === "required" ? "Window + aggregation assigned when used" : "Event member · aggregation not registered" : "Bar timeframe assigned when used";
  if (context.dimension_kind === "rolling_window") return `Window ${intervalLabel(context.window || "")}`;
  if (context.dimension_kind === "anchored") return context.anchor === "market_session" ? "Session anchored" : `${readable(context.anchor || "anchor")} anchored`;
  if (context.dimension_kind === "as_of") return context.as_of === "evaluation_clock" ? "Current at evaluation" : readable(context.as_of || "latest available");
  return "Current value";
}

function fieldContextCards(context: DataFieldDefinition["context"]) {
  const cards: Array<{ detail: string; label: string; value: string }> = [];
  if (context.dimension_kind === "interval") cards.push(context.interval_semantics === "event_window"
    ? context.aggregation?.mode === "required"
      ? { label: "Use parameters", value: "Window + function", detail: "The Rule Set binds a window value/unit and one compatible event aggregation; QMD compiles both into the executable identity." }
      : { label: "Use parameters", value: "Not executable", detail: "This raw event member is cataloged, but no aggregation-safe QMD projection is registered yet." }
    : { label: "Use parameter", value: "Bar timeframe", detail: "The Rule Set selects the bar timeframe; the output's aggregation is intrinsic and cannot be overridden." });
  if (context.window) cards.push({ label: "Rolling window", value: intervalLabel(context.window), detail: context.window_configurable ? "Configurable lookback window." : "Fixed by this field definition." });
  if (context.anchor) cards.push({ label: "Anchor", value: readable(context.anchor), detail: "The value accumulates or resets at this boundary." });
  if (context.as_of) cards.push({ label: "As of", value: context.as_of === "evaluation_clock" ? "Current at evaluation" : readable(context.as_of), detail: "Latest causally available value at this clock." });
  cards.push({ label: "Update cadence", value: readable(context.update_cadence || "producer cadence"), detail: "How often the producer may publish a new value." });
  return cards;
}

function intervalOrder(value?: string) {
  const order = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1mo"];
  const index = order.indexOf(value || "");
  return index < 0 ? Number.MAX_SAFE_INTEGER : index;
}

function groupCatalogFields(rows: Array<AtomicField | DataFieldDefinition>, definitionByRef: Map<string, RuleFieldDefinition>) {
  const grouped = new Map<string, Map<string, Array<AtomicField | DataFieldDefinition>>>();
  rows.forEach((row) => {
    const { group, subgroup } = catalogFieldLocation(row, definitionByRef);
    const subgroups = grouped.get(group) ?? new Map<string, Array<AtomicField | DataFieldDefinition>>();
    subgroups.set(subgroup, [...(subgroups.get(subgroup) ?? []), row]);
    grouped.set(group, subgroups);
  });
  const ordered = new Map<string, Map<string, Array<AtomicField | DataFieldDefinition>>>();
  DATA_CATALOG_GROUPS.forEach((group) => {
    const subgroups = grouped.get(group);
    if (subgroups) ordered.set(group, new Map([...subgroups.entries()].sort(([left], [right]) => catalogSubgroupOrder(group, left) - catalogSubgroupOrder(group, right) || left.localeCompare(right)).map(([subgroup, groupRows]) => [subgroup, groupRows.sort((left, right) => left.name.localeCompare(right.name))])));
  });
  [...grouped.entries()].filter(([group]) => !ordered.has(group)).sort(([left], [right]) => left.localeCompare(right)).forEach(([group, subgroups]) => ordered.set(group, subgroups));
  return ordered;
}

function catalogFieldLocation(row: AtomicField | DataFieldDefinition, definitionByRef: Map<string, RuleFieldDefinition>) {
  if ("data_field_id" in row) {
    if (row.data_field_id.toLowerCase().includes("qmd.signal.")) return { group: "Signals", subgroup: "Signal Definitions" };
    const definition = definitionByRef.get(row.outputs[0]?.field_ref ?? "");
    return definition ? dataCatalogLocation(definition) : { group: "Other Registered Data", subgroup: "Numeric Values" };
  }
  if (row.group === "qmd_trade_events") return { group: "Market Data", subgroup: "Trade Events" };
  if (row.group === "qmd_quote_events") return { group: "Market Data", subgroup: "Quote Events" };
  if (["market_clock", "market_reference", "qmd_scanner", "tradability"].includes(row.group)) return { group: "Market Data", subgroup: "Session & Market State" };
  if (["identity", "listing", "country", "corporate_event", "presentation"].includes(row.group)) return { group: "Company & Security", subgroup: "Reference Classifications" };
  if (["fundamental", "sec"].includes(row.group)) return { group: "Fundamentals & Filings", subgroup: "Financial Statements" };
  if (row.group === "news") return { group: "News & Intelligence", subgroup: "News Content" };
  if (row.group === "quality_and_coverage") return { group: "Data Quality & Operations", subgroup: "Quality & Mapping" };
  return { group: "Other Registered Data", subgroup: readable(row.group || "Registered fields") };
}

function DataDefinitionDetail({ definition, onNavigate, registry }: { definition: RegistryDefinition; onNavigate: (id: string) => void; registry: InformationRegistry }) {
  const documentation = definition.documentation;
  const inputIds = documentation?.input_field_ids?.length ? documentation.input_field_ids : definition.input_field_ids ?? [];
  const sourceFields = documentation?.source_fields?.filter(Boolean) ?? [];
  const linkedInputs = inputIds.filter((id) => !sourceFields.includes(id));
  const operationSteps = documentation?.operation_steps?.filter(Boolean) ?? [];
  const bands = documentation?.classification_bands ?? [];
  const methodMissing = documentation?.documentation_status === "partial";
  return <article className="data-definition-document">
    <header><span>{definition.presentation.kind_label} · {readable(definition.status)}</span><h2>{displayLabel(definition)}</h2><div className="data-definition-identity"><code>{definition.registry_id}</code><button aria-label="Copy definition ID" onClick={() => void navigator.clipboard.writeText(definition.registry_id)} type="button"><Copy size={13} /> Copy ID</button>{definition.tags.slice(0, 4).map((tag) => <em key={tag}>{readable(tag)}</em>)}</div></header>
    <section className="data-definition-method">
      <article className="data-definition-source"><span>Source</span><h3 title={documentation?.source_location || readable(definition.owner)}>{documentation?.source_location || readable(definition.owner)}</h3>{sourceFields.length ? <div className="data-definition-source-fields">{sourceFields.map((field) => { const sourceDefinition = registry.definitions.find((row) => row.registry_id === field); return sourceDefinition ? <button key={field} onClick={() => onNavigate(field)} type="button">{displayLabel(sourceDefinition)}<small>{field}</small><ChevronRight size={12} /></button> : <code key={field}>{field}</code>; })}</div> : null}{linkedInputs.length ? <div className="data-definition-source-links">{linkedInputs.map((id) => { const input = registry.definitions.find((row) => row.registry_id === id); return input ? <button key={id} onClick={() => onNavigate(id)} type="button">{displayLabel(input)}<small>{id}</small><ChevronRight size={12} /></button> : null; })}</div> : null}{isInformativeSourceSummary(documentation?.source_summary) ? <p>{documentation!.source_summary}</p> : null}</article>
      <article className="data-definition-operation"><span>Operation</span>{operationSteps.length ? <ol>{operationSteps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol> : <p className="data-definition-method-missing">The producer has not registered the exact operation for this value.</p>}{documentation?.formula && !operationSteps.includes(documentation.formula) ? <div className="data-definition-formula"><span>Formula</span><code>{documentation.formula}</code></div> : null}{methodMissing ? <p className="data-definition-method-warning">Method documentation is incomplete. Do not infer an unregistered formula.</p> : null}</article>
    </section>
    {bands.length ? <section className="data-definition-bands"><header><span>Classification output</span><h3>Thresholds</h3></header><table><thead><tr><th>Result</th><th>Input range</th></tr></thead><tbody>{bands.map((band) => <tr key={band.band_id}><td><strong>{band.label}</strong><small>{band.band_id}</small></td><td>{formatBand(band)}</td></tr>)}</tbody></table></section> : null}
    {definition.parameters?.length ? <section><h3>Calculation parameters</h3><div className="data-definition-parameters">{definition.parameters.map((parameter) => <div key={parameter.name}><strong>{parameter.label || parameter.name}</strong><span>{parameter.description || parameter.type || "Parameter"}</span><code>{parameter.default === undefined ? "Required" : String(parameter.default)}{parameter.unit ? ` ${parameter.unit}` : ""}</code></div>)}</div></section> : null}
  </article>;
}

export function RuleSetLibraryPage({ fields, onChange, ruleSets }: { fields: RuleFieldDefinition[]; onChange: (value: DataRuleSet[]) => void; ruleSets: DataRuleSet[] }) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(() => {
    const requestedId = ruleSetIdFromHash();
    return ruleSets.some((row) => row.rule_set_id === requestedId) ? requestedId : ruleSets[0]?.rule_set_id ?? "";
  });
  const selected = ruleSets.find((row) => row.rule_set_id === selectedId) ?? ruleSets[0];
  const visible = ruleSets.filter((row) => !query.trim() || [row.name, row.description, row.rule_set_id].some((value) => value.toLowerCase().includes(query.trim().toLowerCase())));
  const grouped = new Map<string, Map<string, DataRuleSet[]>>();
  visible.forEach((row) => {
    const { group, subgroup } = ruleSetLibraryLocation(row);
    const subgroups = grouped.get(group) ?? new Map<string, DataRuleSet[]>();
    subgroups.set(subgroup, [...(subgroups.get(subgroup) ?? []), row]);
    grouped.set(group, subgroups);
  });

  function select(ruleSetId: string) { setSelectedId(ruleSetId); replaceRuleSetHash(ruleSetId); }
  function replace(next: DataRuleSet) { onChange(ruleSets.map((row) => row.rule_set_id === next.rule_set_id ? next : row)); }
  function create(source?: DataRuleSet) {
    const rule_set_id = uniqueRuleSetId(source ? `${source.rule_set_id}-copy` : "rule-set", ruleSets);
    const next: DataRuleSet = { atomic: false, conditions: source?.conditions.map((row, index) => ({ ...row, condition_id: `${rule_set_id}-condition-${index + 1}` })) ?? [], description: source ? `Custom copy of ${source.name}.` : "Describe the reusable decision represented by this rule set.", editable: true, enabled: true, name: source ? `${source.name} Copy` : "Untitled Rule Set", operator: source?.operator ?? "all", origin: "user", protected: false, publication_status: "draft", required_score: source?.required_score ?? 1, revision: 1, rule_set_id, scope: source?.scope ?? "shared" };
    onChange([...ruleSets, next]); select(rule_set_id);
  }

  return <div className="data-library-workbench rule-set-library">
    <aside className="data-library-catalog"><header><span>Registered rule sets</span><strong>{visible.length} of {ruleSets.length}</strong><p>Built-in defaults are read-only. Custom rule sets compare exact registered Data Field outputs.</p></header><label className="data-library-search"><Search size={15} /><input aria-label="Search rule sets" onChange={(event) => setQuery(event.target.value)} placeholder="Search rule sets" type="search" value={query} /></label><button className="data-library-create" onClick={() => create()} type="button"><Plus size={14} /> Create rule set</button><div className="data-library-tree">{[...grouped.entries()].map(([group, subgroups]) => <details key={group} open><summary><span>{group}</span><em>{[...subgroups.values()].reduce((sum, rows) => sum + rows.length, 0)}</em></summary>{[...subgroups.entries()].map(([subgroup, rows]) => <details className="data-library-subgroup" key={subgroup} open><summary><span>{subgroup}</span><em>{rows.length}</em></summary><div>{rows.map((row) => <button aria-current={selected?.rule_set_id === row.rule_set_id ? "true" : undefined} key={row.rule_set_id} onClick={() => select(row.rule_set_id)} type="button"><span><strong>{row.name}</strong><small>{row.description}</small></span>{row.protected || row.origin === "system" ? <LockKeyhole size={12} /> : <ChevronRight size={13} />}</button>)}</div></details>)}</details>)}</div></aside>
    <main className="data-library-detail">{selected ? <RuleSetDetail fields={fields} onDelete={() => { const remaining = ruleSets.filter((row) => row.rule_set_id !== selected.rule_set_id); onChange(remaining); select(remaining[0]?.rule_set_id ?? ""); }} onDuplicate={() => create(selected)} onChange={replace} ruleSet={selected} /> : <div className="data-library-empty"><span>Create a rule set to begin.</span></div>}</main>
  </div>;
}

function RuleSetDetail({ fields, onChange, onDelete, onDuplicate, ruleSet }: { fields: RuleFieldDefinition[]; onChange: (value: DataRuleSet) => void; onDelete: () => void; onDuplicate: () => void; ruleSet: DataRuleSet }) {
  const locked = Boolean(ruleSet.protected || ruleSet.origin === "system" || ruleSet.editable === false);
  const definitions = fields.filter((row) => DATA_KINDS.has(row.kind));
  const definitionById = new Map<string, RuleFieldDefinition>();
  definitions.forEach((row) => {
    [row.registry_id, row.field_ref, row.source_id].filter(Boolean).forEach((key) => definitionById.set(String(key), row));
  });
  const definitionFamilies = ruleDefinitionFamilies(definitions);
  const definitionChoices = [...definitionFamilies.values()].map(preferredRuleVariant);
  const definitionOptions = dataDefinitionLookupOptions(definitionChoices).map((option) => ({ ...option, value: definitionById.get(option.value)?.source_id || option.value }));
  const evaluationContexts = ruleSetEvaluationContexts(ruleSet, definitionById);
  function replaceCondition(conditionId: string, next: DataRuleCondition) {
    onChange({ ...ruleSet, conditions: ruleSet.conditions.map((row) => row.condition_id === conditionId ? next : row) });
  }
  function addCondition() {
    if (!definitions.length) return;
    onChange({ ...ruleSet, conditions: [...ruleSet.conditions, { comparator: "equals", condition_id: `${ruleSet.rule_set_id}-condition-${ruleSet.conditions.length + 1}`, enabled: true, left_source_id: "", left_interval: "", right_source_id: "", right_interval: "", value: 0 }] });
  }
  return <article className="rule-set-document">
    <header><span>{locked ? "Built-in rule set" : "Editable rule set"} · revision {ruleSet.revision ?? 1}</span><input aria-label="Rule set name" disabled={locked} onChange={(event) => onChange({ ...ruleSet, name: event.target.value })} value={ruleSet.name} /><textarea aria-label="Rule set description" disabled={locked} onChange={(event) => onChange({ ...ruleSet, description: event.target.value })} value={ruleSet.description} /><div><code>{ruleSet.rule_set_id}</code>{locked ? <button onClick={onDuplicate} type="button"><Copy size={13} /> Duplicate as custom</button> : <button className="danger" onClick={onDelete} type="button"><Trash2 size={13} /> Remove rule set</button>}</div></header>
    <section className="rule-set-logic"><label><span>Condition logic</span><select disabled={locked} onChange={(event) => onChange({ ...ruleSet, operator: event.target.value as DataRuleSet["operator"] })} value={ruleSet.operator}><option value="all">All conditions</option><option value="any">Any condition</option><option value="score">Required score</option></select></label><span>{ruleSet.conditions.length} condition{ruleSet.conditions.length === 1 ? "" : "s"}</span></section>
    <section className="rule-set-evaluation-context"><TimerReset aria-hidden="true" size={16} /><header><span>Operand context</span><strong>{evaluationContexts.some((context) => context.kind === "interval") ? "Explicit time dimensions" : "Field-owned context"}</strong><p>{evaluationContexts.some((context) => context.kind === "interval") ? "Bar fields bind a timeframe; event fields bind a window and compatible aggregation." : "Anchored and point-in-time fields use their registered causal context."}</p></header><dl>{evaluationContexts.map((context) => <div className={`rule-context-${context.kind}`} key={`${context.kind}:${context.label}`}><dt>{context.kind === "interval" ? "Time dimension" : "Context"}</dt><dd>{context.label}</dd></div>)}</dl></section>
    <section className="rule-condition-list">{ruleSet.conditions.map((condition, index) => {
      const source = definitionById.get(condition.left_field_ref || condition.left_source_id);
      const target = condition.right_field_ref ? definitionById.get(condition.right_field_ref) : condition.right_source_id ? definitionById.get(condition.right_source_id) : undefined;
      if (locked) return <RuleConditionStatement condition={condition} index={index} key={condition.condition_id} source={source} target={target} />;
      const comparesToField = Boolean(condition.right_field_ref || condition.right_source_id);
      const compatibleTargets = compatibleRuleTargets(source, definitionChoices);
      const compatibleTargetOptions = dataDefinitionLookupOptions(compatibleTargets).map((option) => ({ ...option, value: definitionById.get(option.value)?.source_id || option.value }));
      const comparators = ruleComparators(source, condition.comparator, comparesToField);
      return <div className="rule-condition-row rule-condition-editable" key={condition.condition_id}>
        <header className="rule-condition-card-header"><span>{index + 1}</span><div><strong>Condition {index + 1}</strong><small>Evaluate one typed market relationship</small></div><button aria-label={`Remove condition ${index + 1}`} onClick={() => onChange({ ...ruleSet, conditions: ruleSet.conditions.filter((row) => row.condition_id !== condition.condition_id) })} type="button"><Trash2 size={13} /></button></header>
        <div className="rule-condition-expression">
        <section className="rule-condition-side rule-condition-source"><header><span>Observe</span><small>Left Data Field</small></header><div className="rule-condition-definition"><InventoryFilterSelect ariaLabel={`Condition ${index + 1} Data Field`} className="rule-condition-definition-lookup" onChange={(value) => {
          const nextSource = preferredRuleVariant(definitionFamilies.get(value) ?? []);
          if (!nextSource) return;
          const targetStillCompatible = !comparesToField || compatibleRuleTargets(nextSource, target ? [target] : []).length > 0;
          const allowed = ruleComparators(nextSource, "", comparesToField && targetStillCompatible);
          const comparator = allowed.some((row) => row.value === condition.comparator) ? condition.comparator : defaultRuleComparator(nextSource);
          replaceCondition(condition.condition_id, { ...condition, comparator, left_field_ref: nextSource.field_ref || nextSource.registry_id, left_source_id: nextSource.source_id || nextSource.registry_id, left_aggregation: preferredRuleAggregation(nextSource), left_interval: preferredRuleInterval(nextSource), right_field_ref: comparator === "is_true" || !targetStillCompatible ? "" : condition.right_field_ref, right_source_id: comparator === "is_true" || !targetStillCompatible ? "" : condition.right_source_id, right_aggregation: comparator === "is_true" || !targetStillCompatible ? "" : condition.right_aggregation, right_interval: comparator === "is_true" || !targetStillCompatible ? "" : condition.right_interval, value: comparator === "is_true" ? null : defaultRuleConstant(nextSource, condition.value) });
        }} optionLimit={0} options={!source && condition.left_source_id ? [{ description: "Unregistered Data Field referenced by this draft.", label: condition.left_source_id, value: condition.left_source_id }, ...definitionOptions] : definitionOptions} placeholder="Choose Data Field" presentation="catalog" searchable searchPlaceholder="Search Data Fields…" showAllOnOpen value={source?.source_id || condition.left_source_id} />{source ? <RuleDimensionControl aggregation={condition.left_aggregation} definition={source} label={`Condition ${index + 1}`} onChange={(left_interval, left_aggregation) => replaceCondition(condition.condition_id, { ...condition, left_aggregation, left_interval })} value={condition.left_interval || ""} /> : <p>Choose the market value or computed output to evaluate.</p>}</div></section>
        <label className="rule-condition-comparator"><small>Relationship</small><select aria-label={`Condition ${index + 1} comparator`} disabled={!source} onChange={(event) => { const comparator = event.target.value; replaceCondition(condition.condition_id, { ...condition, comparator, right_aggregation: comparator === "is_true" ? "" : condition.right_aggregation, right_field_ref: comparator === "is_true" ? "" : condition.right_field_ref, right_source_id: comparator === "is_true" ? "" : condition.right_source_id, right_interval: comparator === "is_true" ? "" : condition.right_interval, value: comparator === "is_true" ? null : defaultRuleConstant(source, condition.value) }); }} value={condition.comparator}>{comparators.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}</select></label>
        <section className="rule-condition-side rule-condition-target"><header><span>Compare against</span>{condition.comparator !== "is_true" ? <div aria-label={`Condition ${index + 1} comparison target`} className="rule-operand-mode" role="group"><button aria-pressed={!comparesToField} onClick={() => replaceCondition(condition.condition_id, { ...condition, comparator: condition.comparator === "above_by_bps" ? defaultRuleComparator(source) : condition.comparator, right_aggregation: "", right_field_ref: "", right_source_id: "", right_interval: "", value: defaultRuleConstant(source, condition.value) })} type="button">Value</button><button aria-pressed={comparesToField} disabled={!compatibleTargets.length} onClick={() => { const nextTarget = target && compatibleRuleTargets(source, [target]).length ? target : compatibleTargets[0]; if (!nextTarget) return; replaceCondition(condition.condition_id, { ...condition, right_aggregation: preferredRuleAggregation(nextTarget), right_field_ref: nextTarget.field_ref || nextTarget.registry_id, right_source_id: nextTarget.source_id || nextTarget.registry_id, right_interval: preferredRuleInterval(nextTarget) }); }} type="button">Data Field</button></div> : null}</header>{condition.comparator === "is_true" ? <div className="rule-condition-boolean"><small>Required state</small><strong>True</strong></div> : comparesToField ? <div className="rule-condition-definition"><InventoryFilterSelect ariaLabel={`Condition ${index + 1} target Data Field`} className="rule-condition-definition-lookup" onChange={(value) => { const nextTarget = preferredRuleVariant(definitionFamilies.get(value) ?? []); replaceCondition(condition.condition_id, { ...condition, right_aggregation: preferredRuleAggregation(nextTarget), right_field_ref: nextTarget?.field_ref || value, right_source_id: nextTarget?.source_id || value, right_interval: preferredRuleInterval(nextTarget) }); }} optionLimit={0} options={!target && condition.right_source_id ? [{ description: "Unregistered Data Field referenced by this draft.", label: condition.right_source_id, value: condition.right_source_id }, ...compatibleTargetOptions] : compatibleTargetOptions} presentation="catalog" searchable searchPlaceholder="Search compatible Data Fields…" showAllOnOpen value={target?.source_id || condition.right_source_id} />{target ? <RuleDimensionControl aggregation={condition.right_aggregation} definition={target} label={`Condition ${index + 1} target`} onChange={(right_interval, right_aggregation) => replaceCondition(condition.condition_id, { ...condition, right_aggregation, right_interval })} value={condition.right_interval || ""} /> : null}{condition.comparator === "above_by_bps" ? <label className="rule-bps-buffer"><small>Buffer (bps)</small><input aria-label={`Condition ${index + 1} basis point buffer`} onChange={(event) => replaceCondition(condition.condition_id, { ...condition, value: Number(event.target.value) })} step="any" type="number" value={Number(condition.value ?? 0)} /></label> : null}</div> : <RuleConstantEditor condition={condition} index={index} onChange={(value) => replaceCondition(condition.condition_id, { ...condition, value })} source={source} />}</section>
        </div>
      </div>;
    })}</section>
    {!locked ? <button className="data-library-add-condition" onClick={addCondition} type="button"><Plus size={14} /> Add condition</button> : <footer><LockKeyhole size={14} /><span>Built-in rule sets are protected and cannot be edited. Duplicate this definition to create an editable custom rule set.</span></footer>}
  </article>;
}

function ruleSetLibraryLocation(ruleSet: DataRuleSet) {
  if (!(ruleSet.protected || ruleSet.origin === "system")) return { group: "Custom rule sets", subgroup: ruleSet.publication_status === "published" ? "Published definitions" : "Draft definitions" };
  const id = ruleSet.rule_set_id;
  if (id.startsWith("initial-entry-opportunity")) return { group: "Strategy decisions", subgroup: "Entry opportunities" };
  if (id.startsWith("initial-entry-confirmation")) return { group: "Strategy decisions", subgroup: "Entry confirmations" };
  if (id.startsWith("initial-entry-blockers")) return { group: "Strategy decisions", subgroup: "Entry blockers" };
  if (id.startsWith("add-")) return { group: "Strategy decisions", subgroup: "Position additions" };
  if (id.startsWith("exit-")) return { group: "Strategy decisions", subgroup: "Exit decisions" };
  if (/watchlist-float-/.test(id)) return { group: "Market Discovery filters", subgroup: "Float classifications" };
  if (/watchlist-(penny|small-caps|mid-caps|large-caps)/.test(id)) return { group: "Market Discovery filters", subgroup: "Price and market-cap classifications" };
  if (/watchlist-(news|sec|fundamental)/.test(id)) return { group: "Market Discovery filters", subgroup: "Intelligence filters" };
  if (/watchlist-(ipo|split)/.test(id)) return { group: "Market Discovery filters", subgroup: "Corporate-event windows" };
  return { group: "Market Discovery filters", subgroup: "Market activity filters" };
}

const RULE_LIBRARY_COMPARATORS = [
  { label: "is at least", value: "greater_or_equal" },
  { label: "is greater than", value: "greater_than" },
  { label: "is at most", value: "less_or_equal" },
  { label: "is less than", value: "less_than" },
  { label: "equals", value: "equals" },
  { label: "does not equal", value: "not_equals" },
  { label: "is true", value: "is_true" },
  { label: "is above by", value: "above_by_bps" },
];

function RuleConditionStatement({ condition, index, source, target }: { condition: DataRuleCondition; index: number; source?: RegistryDefinition; target?: RegistryDefinition }) {
  const relation = ruleComparatorLabel(condition.comparator, condition.value);
  const showTarget = condition.comparator !== "is_true";
  return <div className="rule-condition-row rule-condition-readonly">
    <span>{index + 1}</span>
    <div className="rule-condition-operand" title={condition.left_field_ref || condition.left_source_id}><small className="rule-condition-operand-label">Observe</small><strong>{source ? displayLabel(source) : condition.left_source_id}</strong><RuleOperandContext aggregation={condition.left_aggregation} definition={source as RuleFieldDefinition | undefined} interval={condition.left_interval} /></div>
    <div className="rule-condition-relation"><small>Relationship</small><strong>{relation}</strong></div>
    {showTarget ? <div className="rule-condition-operand rule-condition-target" title={condition.right_field_ref || condition.right_source_id}><small className="rule-condition-operand-label">Compare against</small><strong>{target ? displayLabel(target) : formatRuleConstant(condition.value, source)}</strong>{target ? <RuleOperandContext aggregation={condition.right_aggregation} definition={target as RuleFieldDefinition} interval={condition.right_interval} /> : <small className="rule-value-context">{ruleValueContext(source)}</small>}</div> : <div className="rule-condition-boolean"><small className="rule-condition-operand-label">Required state</small><strong>True</strong><small>Boolean event state</small></div>}
  </div>;
}

function RuleOperandContext({ aggregation, definition, interval }: { aggregation?: string; definition?: RuleFieldDefinition; interval: IntervalValue }) {
  const isInterval = definition?.data_field_context?.dimension_kind === "interval";
  const missing = isInterval && !interval;
  return <span className={`rule-operand-context${missing ? " rule-operand-context-missing" : ""}`}><span><small>{definition?.data_field_context?.interval_semantics === "event_window" ? "Window" : isInterval ? "Bar timeframe" : "Evaluation context"}</small><strong>{missing ? "Required interval missing" : ruleFieldContext(definition, interval)}</strong></span>{aggregation ? <span><small>Aggregation</small><strong>{aggregationLabel(aggregation)}</strong></span> : null}</span>;
}

function ruleSetEvaluationContexts(ruleSet: DataRuleSet, definitions: Map<string, RuleFieldDefinition>) {
  const contexts = new Map<string, { kind: "context" | "interval"; label: string }>();
  ruleSet.conditions.forEach((condition) => {
    for (const side of ["left", "right"] as const) {
      const sourceId = condition[`${side}_source_id`];
      const fieldRef = condition[`${side}_field_ref`];
      if (!sourceId && !fieldRef) continue;
      const definition = definitions.get(fieldRef || sourceId);
      const interval = condition[`${side}_interval`];
      const kind = definition?.data_field_context?.dimension_kind === "interval" ? "interval" : "context";
      const label = ruleFieldContext(definition, interval);
      contexts.set(`${kind}:${label}`, { kind, label });
    }
  });
  return [...contexts.values()];
}

function ruleDefinitionFamilies(definitions: RuleFieldDefinition[]) {
  const families = new Map<string, RuleFieldDefinition[]>();
  definitions.forEach((definition) => {
    const key = definition.source_id || definition.registry_id;
    const current = families.get(key) ?? [];
    if (!current.some((row) => row.registry_id === definition.registry_id)) families.set(key, [...current, definition]);
  });
  return families;
}

function preferredRuleVariant(variants: RuleFieldDefinition[]) {
  return variants[0];
}

function RuleDimensionControl({ aggregation, definition, label, onChange, value }: { aggregation?: string; definition: RuleFieldDefinition; label: string; onChange: (interval: IntervalSpec, aggregation?: string) => void; value: IntervalValue }) {
  const intervals = definition.data_field_context?.available_intervals ?? [];
  const aggregationContract = definition.data_field_context?.aggregation;
  const allowed = aggregationContract?.mode === "required" ? aggregationContract.allowed ?? [] : [];
  return <div className="rule-field-timing">{definition.data_field_context?.dimension_kind === "interval" && intervals.length ? <IntervalSelect ariaLabel={`${label} ${definition.data_field_context.interval_semantics === "event_window" ? "window" : "bar timeframe"}`} className="rule-field-dimension" intervals={intervals} onChange={(interval) => onChange(interval, aggregation)} value={value} /> : <span className="rule-field-dimension-summary"><small>Evaluation context</small><strong>{ruleFieldContext(definition)}</strong></span>}{allowed.length ? <InventoryFilterSelect ariaLabel={`${label} aggregation`} className="rule-field-aggregation" onChange={(next) => onChange(value as IntervalSpec, next)} options={allowed.map((value) => ({ description: aggregationDescription(value), label: aggregationLabel(value), value }))} presentation="catalog" showAllOnOpen value={aggregation || aggregationContract?.default || allowed[0]} /> : null}</div>;
}

function RuleConstantEditor({ condition, index, onChange, source }: { condition: DataRuleCondition; index: number; onChange: (value: DataRuleCondition["value"]) => void; source?: RuleFieldDefinition }) {
  const domain = ruleValueDomain(source);
  const allowed = domain.allowed_values ?? [];
  if (domain.kind === "enum" && allowed.length) {
    const options = allowed.map((row) => ({ description: row.description, label: row.label, value: encodeRuleValue(row.value) }));
    return <label className="rule-constant-editor rule-constant-lookup"><small>Value</small><InventoryFilterSelect ariaLabel={`Condition ${index + 1} value`} onChange={(key) => onChange(allowed.find((row) => encodeRuleValue(row.value) === key)?.value ?? null)} options={options} presentation="catalog" searchable={options.length > 6} searchPlaceholder="Search allowed values…" showAllOnOpen value={encodeRuleValue(defaultRuleConstant(source, condition.value))} /></label>;
  }
  if (domain.kind === "boolean") {
    return <label className="rule-constant-editor rule-constant-lookup"><small>Value</small><InventoryFilterSelect ariaLabel={`Condition ${index + 1} value`} onChange={(value) => onChange(value === "true")} options={[{ description: "The field must be true.", label: "True", value: "true" }, { description: "The field must be false.", label: "False", value: "false" }]} presentation="catalog" showAllOnOpen value={condition.value === false ? "false" : "true"} /></label>;
  }
  const inputType = domain.kind === "number" ? "number" : domain.kind === "date" ? "date" : domain.kind === "time" ? "time" : "text";
  const placeholder = domain.kind === "timestamp" ? "YYYY-MM-DDTHH:MM:SSZ" : domain.kind === "text" ? "Enter value" : undefined;
  return <label className="rule-constant-editor"><small>{domain.kind === "timestamp" ? "ISO timestamp" : "Value"}</small><input aria-label={`Condition ${index + 1} value`} disabled={!source || domain.kind === "structured"} onChange={(event) => onChange(domain.kind === "number" ? Number(event.target.value) : event.target.value)} placeholder={placeholder} step={domain.kind === "time" ? "1" : domain.kind === "number" ? "any" : undefined} type={inputType} value={String(condition.value ?? "")} />{domain.unit && !["scalar", "producer_defined", "string"].includes(domain.unit.toLowerCase()) ? <em>{readable(domain.unit)}</em> : null}</label>;
}

function ruleFieldContext(definition?: RuleFieldDefinition, interval: IntervalValue = "") {
  if (!definition?.data_field_context) return "Registered Data Field";
  if (definition.data_field_context.dimension_kind === "interval") return interval ? `${definition.data_field_context.interval_semantics === "event_window" ? "Window" : "Bar"} ${intervalLabel(interval)}` : "Interval required";
  return fieldDimensionSummary(definition.data_field_context);
}

function preferredRuleInterval(definition?: RuleFieldDefinition) { return preferredInterval(definition?.data_field_context?.available_intervals ?? []); }
function preferredRuleAggregation(definition?: RuleFieldDefinition) { const contract = definition?.data_field_context?.aggregation; return contract?.mode === "required" ? contract.default || contract.allowed?.[0] || "" : ""; }
function aggregationLabel(value: string) { return ({ first: "First", last: "Last", min: "Minimum", max: "Maximum", sum: "Sum", mean: "Average", median: "Median", count: "Count", volume_weighted_mean: "Volume-weighted average" } as Record<string, string>)[value] || value; }
function aggregationDescription(value: string) { return `${aggregationLabel(value)} of the source events inside the selected window.`; }
function ruleComparators(source: RegistryDefinition | undefined, current: string, comparesToField = false) {
  const values = isBooleanRuleDefinition(source)
    ? ["is_true", "equals", "not_equals"]
    : isNumericRuleDefinition(source)
      ? ["greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals", "not_equals", ...(comparesToField ? ["above_by_bps"] : [])]
      : ["equals", "not_equals"];
  if (current && !values.includes(current)) values.push(current);
  return RULE_LIBRARY_COMPARATORS.filter((row) => values.includes(row.value));
}

function compatibleRuleTargets(source: RegistryDefinition | undefined, definitions: RuleFieldDefinition[]) {
  if (!source) return [];
  const sourceType = source.documentation?.value_type?.toLowerCase() ?? "";
  const sourceUnit = source.documentation?.unit?.toLowerCase() ?? "";
  return definitions.filter((target) => {
    const targetType = target.documentation?.value_type?.toLowerCase() ?? "";
    const targetUnit = target.documentation?.unit?.toLowerCase() ?? "";
    if (isNumericRuleDefinition(source) !== isNumericRuleDefinition(target)) return false;
    if (isBooleanRuleDefinition(source) !== isBooleanRuleDefinition(target)) return false;
    const unitFamily = (unit: string) => ({ price: "price", currency: "price", usd: "price" } as Record<string, string>)[unit] || unit;
    return sourceUnit && targetUnit ? unitFamily(sourceUnit) === unitFamily(targetUnit) : sourceType === targetType;
  });
}

function defaultRuleComparator(source?: RegistryDefinition) { return isBooleanRuleDefinition(source) ? "is_true" : isNumericRuleDefinition(source) ? "greater_or_equal" : "equals"; }
function isBooleanRuleDefinition(source?: RegistryDefinition) { const valueType = source?.documentation?.value_type?.toLowerCase(); return valueType === "boolean" || (source?.kind === "signal" && valueType === "event"); }
function isNumericRuleDefinition(source?: RegistryDefinition) { return /number|integer|float|score|decimal|currency|price|percent|ratio|basis/.test(source?.documentation?.value_type?.toLowerCase() ?? ""); }
function ruleComparatorLabel(comparator: string, value: DataRuleCondition["value"]) { if (comparator === "above_by_bps") return `is ${formatCompactNumber(Number(value ?? 0))} bps above`; return RULE_LIBRARY_COMPARATORS.find((row) => row.value === comparator)?.label ?? readable(comparator).toLowerCase(); }
function ruleValueContext(source?: RegistryDefinition) { const domain = ruleValueDomain(source as RuleFieldDefinition | undefined); if (domain.closed) return "Registered category"; const unit = source?.documentation?.unit; return unit && unit !== "scalar" && unit !== "producer_defined" ? readable(unit) : readable(domain.kind); }
function formatRuleConstant(value: DataRuleCondition["value"], source?: RegistryDefinition) {
  if (value === null || value === undefined || value === "") return "Missing value";
  if (typeof value === "boolean") return value ? "True" : "False";
  const known = ruleValueDomain(source as RuleFieldDefinition | undefined).allowed_values?.find((row) => row.value === value);
  if (known) return known.label;
  if (typeof value !== "number") return String(value);
  const unit = source?.documentation?.unit?.toLowerCase() ?? "";
  if (unit === "currency" || unit === "usd") return `$${formatCompactNumber(value)}`;
  if (unit.includes("percent")) return `${formatCompactNumber(value)}%`;
  if (unit.includes("share")) return `${formatCompactNumber(value)} shares`;
  return formatCompactNumber(value);
}
function inferValueDomain(valueType: string, unit: string, knownValues: KnownValue[]): ValueDomain {
  const normalized = valueType.toLowerCase();
  if (knownValues.length) return { allowed_values: knownValues, closed: true, kind: "enum", unit };
  if (normalized === "boolean") return { closed: false, kind: "boolean", unit };
  if (/number|integer|float|score|decimal|currency|price|percent|ratio|basis/.test(normalized)) return { closed: false, kind: "number", unit };
  if (["date", "time", "timestamp", "datetime"].includes(normalized) || ["date", "time", "timestamp", "datetime"].includes(unit.toLowerCase())) { const temporal = ["date", "time", "timestamp", "datetime"].includes(normalized) ? normalized : unit.toLowerCase(); return { closed: false, kind: temporal === "datetime" ? "timestamp" : (temporal as ValueDomain["kind"]), unit }; }
  if (["json", "vector", "record", "object", "array"].includes(normalized)) return { closed: false, kind: "structured", unit };
  return { closed: false, kind: "text", unit };
}
function ruleValueDomain(source?: RuleFieldDefinition): ValueDomain { return source?.value_domain ?? inferValueDomain(source?.documentation?.value_type ?? "string", source?.documentation?.unit ?? "", []); }
function encodeRuleValue(value: DataRuleCondition["value"]) { return JSON.stringify(value); }
function defaultRuleConstant(source?: RuleFieldDefinition, current?: DataRuleCondition["value"]): DataRuleCondition["value"] {
  const domain = ruleValueDomain(source);
  const allowed = domain.allowed_values ?? [];
  if (domain.closed) return allowed.some((row) => row.value === current) ? current ?? null : allowed[0]?.value ?? null;
  if (domain.kind === "boolean") return typeof current === "boolean" ? current : true;
  if (domain.kind === "number") return typeof current === "number" && Number.isFinite(current) ? current : 0;
  if (["date", "time", "timestamp", "text"].includes(domain.kind)) return typeof current === "string" ? current : "";
  return null;
}
function formatCompactNumber(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 4, notation: Math.abs(value) >= 1_000 ? "compact" : "standard" }).format(value); }

function groupDefinitions(definitions: RegistryDefinition[]) {
  const grouped = new Map<string, Map<string, RegistryDefinition[]>>();
  definitions.forEach((row) => {
    const { group, subgroup } = dataCatalogLocation(row);
    const subgroups = grouped.get(group) ?? new Map<string, RegistryDefinition[]>();
    subgroups.set(subgroup, [...(subgroups.get(subgroup) ?? []), row].sort((a, b) => displayLabel(a).localeCompare(displayLabel(b))));
    grouped.set(group, subgroups);
  });
  const groups = new Map<string, Map<string, RegistryDefinition[]>>();
  DATA_CATALOG_GROUPS.forEach((group) => {
    const subgroups = grouped.get(group);
    if (!subgroups) return;
    groups.set(group, new Map([...subgroups.entries()].sort(([left], [right]) => catalogSubgroupOrder(group, left) - catalogSubgroupOrder(group, right) || left.localeCompare(right))));
  });
  [...grouped.entries()].filter(([group]) => !groups.has(group)).sort(([left], [right]) => left.localeCompare(right)).forEach(([group, subgroups]) => groups.set(group, subgroups));
  return groups;
}

const DATA_CATALOG_GROUPS = ["Market Data", "Technical Analysis", "Company & Security", "Fundamentals & Filings", "News & Intelligence", "Signals", "Models & Context", "Trading & Portfolio", "Data Quality & Operations", "Other Registered Data"] as const;
const DATA_CATALOG_SUBGROUPS: Record<string, string[]> = {
  "Market Data": ["Trade Events", "Quote Events", "Price & Returns", "Quotes & Spreads", "Volume & Activity", "Liquidity", "Order Flow & Microstructure", "Session & Market State", "Tradability"],
  "Technical Analysis": ["Trend & Moving Averages", "Momentum & Oscillators", "Volatility & Risk", "Price Action & Patterns", "Market Structure", "Statistics & Cycles", "Technical Collections"],
  "Company & Security": ["Security Identity", "Listing & Venue", "Market Cap & Float", "Short Interest & Borrow", "Industry & Geography", "Corporate Events", "Reference Classifications"],
  "Fundamentals & Filings": ["Financial Statements", "Profitability & Margins", "Growth & Valuation", "Capital & Shares", "Fundamental Scores", "SEC Filing Data", "XBRL Quality"],
  "News & Intelligence": ["News Content", "News Scoring & Sentiment", "News Timing & Eligibility", "Embeddings & Model Context"],
  Signals: ["Signal Definitions", "Signal Identity", "Signal Timing & State", "Signal Scores & Evidence", "External Intelligence Signals", "Market Signal Outputs"],
  "Models & Context": ["Embeddings", "Model Inputs & Outputs"],
  "Trading & Portfolio": ["Orders & Fills", "Positions & Exposure", "Profit & Loss", "Broker References"],
  "Data Quality & Operations": ["Coverage & Availability", "Quality & Mapping", "Freshness & Degradation", "Schedules", "Ingest & Processing", "Provenance & Lineage"],
  "Other Registered Data": ["Numeric Values", "Boolean Values", "Text Values", "Event Values", "Structured Values"],
};

function catalogSubgroupOrder(group: string, subgroup: string) {
  const index = DATA_CATALOG_SUBGROUPS[group]?.indexOf(subgroup) ?? -1;
  return index < 0 ? Number.MAX_SAFE_INTEGER : index;
}

function dataCatalogLocation(row: RegistryDefinition): { group: string; subgroup: string } {
  const id = ((row as RuleFieldDefinition).source_id || row.registry_id).toLowerCase();
  const tags = row.tags.join(" ").toLowerCase();
  const documentation = row.documentation;
  const text = `${id} ${displayLabel(row)} ${row.label} ${row.description} ${tags} ${documentation?.entity_grain ?? ""} ${documentation?.operation_kind ?? ""}`.toLowerCase();

  if (row.kind === "signal" || id.includes("qmd.signal.") || /^signal\./.test(id) || /\bqmd\.signal\.|\bsignal\./.test(text)) {
    if (row.kind === "signal") return { group: "Signals", subgroup: /^(signal\.(company_news|sec_filing)|.*\b(news|sec)_signal\b)/.test(text) ? "External Intelligence Signals" : "Signal Definitions" };
    if (/\.(signal_id|signal_key|signal_version|schema_version|engine_version|event_id|producer|domain|ticker|working_timeframe)$/.test(id)) return { group: "Signals", subgroup: "Signal Identity" };
    if (/\.(clock|effective_at|observed_at|expires_at|state|resolution_reason)$/.test(id)) return { group: "Signals", subgroup: "Signal Timing & State" };
    if (/\.(confidence|direction|evidence|invalidation_price|rank_score|reference_price|score|trigger_reason)$/.test(id)) return { group: "Signals", subgroup: "Signal Scores & Evidence" };
    if (/^signal\.(company_news|sec_filing|news_labeled|sec_labeled)/.test(id)) return { group: "Signals", subgroup: "External Intelligence Signals" };
    return { group: "Signals", subgroup: "Market Signal Outputs" };
  }

  if (/^embedding\./.test(id)) return { group: "Models & Context", subgroup: "Embeddings" };
  if (/^model\./.test(id)) return { group: "Models & Context", subgroup: "Model Inputs & Outputs" };

  if (/^news\.|company_news|news_labeled|qmd\.field\.news_flag/.test(id) || /\bnews\b|text.intelligence/.test(tags)) {
    if (/latest_at|expires_at|recency|eligible/.test(id)) return { group: "News & Intelligence", subgroup: "News Timing & Eligibility" };
    if (/confidence|direction|impact|score|uncertainty/.test(id)) return { group: "News & Intelligence", subgroup: "News Scoring & Sentiment" };
    return { group: "News & Intelligence", subgroup: "News Content" };
  }

  if (/^fundamental\.|^sec\.|^sec-events$|^xbrl\./.test(id) || /\bfundamental\b|\bxbrl\b/.test(tags)) {
    if (/^sec\.|^sec-events$/.test(id)) return { group: "Fundamentals & Filings", subgroup: "SEC Filing Data" };
    if (/^xbrl\.|quality_score|quality_label|quality_coverage/.test(id)) return { group: "Fundamentals & Filings", subgroup: "XBRL Quality" };
    if (/margin|return_on|current_ratio|debt_to_equity|interest_coverage|cash_conversion|research_intensity|tax_rate/.test(id)) return { group: "Fundamentals & Filings", subgroup: "Profitability & Margins" };
    if (/growth|valuation|trajectory|earnings|revenue.*change|dilution/.test(id)) return { group: "Fundamentals & Filings", subgroup: "Growth & Valuation" };
    if (/share|stock|equity|debt|dividend/.test(id)) return { group: "Fundamentals & Filings", subgroup: "Capital & Shares" };
    if (/score|label/.test(id)) return { group: "Fundamentals & Filings", subgroup: "Fundamental Scores" };
    return { group: "Fundamentals & Filings", subgroup: "Financial Statements" };
  }

  if (/^event\./.test(id)) return { group: "Company & Security", subgroup: "Corporate Events" };
  if (/^relationship\./.test(id)) return { group: "Company & Security", subgroup: "Security Identity" };
  if (/^identity\.|^instrument-identity$/.test(id)) return { group: "Company & Security", subgroup: "Security Identity" };
  if (/^listing\.|^presentation\./.test(id)) return { group: "Company & Security", subgroup: "Listing & Venue" };
  if (/^country\.|classification\.(industry|sector)/.test(id)) return { group: "Company & Security", subgroup: "Industry & Geography" };
  if (/^reference\.(borrow|days_to_cover|fails_to_deliver|ftd|reg_sho|short)|classification\.short_pressure/.test(id)) return { group: "Company & Security", subgroup: "Short Interest & Borrow" };
  if (/^reference\.(float|market_cap|shares_outstanding)|classification\.(float|market_cap)/.test(id)) return { group: "Company & Security", subgroup: "Market Cap & Float" };
  if (/qmd\.field\.(float_bucket|market_cap_bucket)/.test(id)) return { group: "Company & Security", subgroup: "Market Cap & Float" };
  if (/qmd\.field\.(industry|sector)/.test(id)) return { group: "Company & Security", subgroup: "Industry & Geography" };
  if (/qmd\.field\.(short_pressure_label|short_squeeze_likelihood)/.test(id)) return { group: "Company & Security", subgroup: "Short Interest & Borrow" };
  if (/^reference\.|^classification\.|reference_context/.test(id)) return { group: "Company & Security", subgroup: "Reference Classifications" };

  if (/^quality\.|^market-quality$/.test(id)) return { group: "Data Quality & Operations", subgroup: "Quality & Mapping" };
  if (/^coverage\./.test(id)) return { group: "Data Quality & Operations", subgroup: "Coverage & Availability" };
  if (/membership-history/.test(id)) return { group: "Data Quality & Operations", subgroup: "Audit and History" };
  if (/^schedule\./.test(id)) return { group: "Data Quality & Operations", subgroup: "Schedules" };
  if (/fresh|degradation|quality.state|quality.flags|event_age/.test(id)) return { group: "Data Quality & Operations", subgroup: "Freshness & Degradation" };
  if (/^qmd\.primitive\.|accepted compact|aggregation rules|arrival timestamp|canonical compact|canonical quotes|canonical trades|completed_daily_bars|condition and exchange references|continuation cursor|coverage checkpoint|coverage update|eligible_trades|event timestamp|live event notification|ordered canonical|ordered event|q_live event row|rejection reason|sequence gap|sip timestamp|trade_aggregation_rules/.test(id)) return { group: "Data Quality & Operations", subgroup: "Ingest & Processing" };
  if (/source quote|source sequence|source ticker|stable source identity|identity intervals|identity validity evidence|broker_reference|clickhouse_reference|massive_rest/.test(id)) return { group: "Data Quality & Operations", subgroup: "Provenance & Lineage" };

  if (/\borders\b|\bfills\b/.test(id)) return { group: "Trading & Portfolio", subgroup: "Orders & Fills" };
  if (/qmd\.field\.(portfolio|exposure)$/.test(id)) return { group: "Trading & Portfolio", subgroup: "Positions & Exposure" };
  if (/realized_pnl|unrealized_pnl/.test(id)) return { group: "Trading & Portfolio", subgroup: "Profit & Loss" };
  if (/ibkr_conid/.test(id)) return { group: "Trading & Portfolio", subgroup: "Broker References" };

  if (isTechnicalDefinition(row, text)) return { group: "Technical Analysis", subgroup: technicalSubgroup(text) };

  if (/^trade\./.test(id)) return { group: "Market Data", subgroup: "Trade Events" };
  if (/^quote\./.test(id)) return { group: "Market Data", subgroup: "Quote Events" };
  if (/tradability|halt_flag|ssr_flag|estimated_luld/.test(id)) return { group: "Market Data", subgroup: "Tradability" };
  if (/^market\.change_(actual|pct)$/.test(id)) return { group: "Market Data", subgroup: "Price & Returns" };
  if (/^clock\.|session|market clock|market state|market\.status|market\.is_|market\.luld|market\.feed|market\.event_at|minute_of_day|previous_day_context|daily_context/.test(id)) return { group: "Market Data", subgroup: "Session & Market State" };
  if (/microstructure|pressure|imbalance|aggress|signed_volume|cumulative_delta|large_trade|tape_/.test(id)) return { group: "Market Data", subgroup: "Order Flow & Microstructure" };
  if (/liquidity|dry_up|slippage/.test(id)) return { group: "Market Data", subgroup: "Liquidity" };
  if (/quote|spread|nbbo|bid_|ask_|mid_/.test(id)) return { group: "Market Data", subgroup: "Quotes & Spreads" };
  if (/volume|trade_count|trade_rate|trade_accel|avg_trade|max_trade|median_trade|dollar_volume|qmd\.field\.(trades|tick_indicators)/.test(text)) return { group: "Market Data", subgroup: "Volume & Activity" };
  if (/^qmd\.family\.core_bars|price|open|high|low|close|vwap|return|gap_from|market\.change_pct|last eligible trade/.test(id)) return { group: "Market Data", subgroup: "Price & Returns" };
  if (/qmd\.field\.bars/.test(id)) return { group: "Market Data", subgroup: "Session & Market State" };

  const valueType = documentation?.value_type?.toLowerCase() ?? "";
  if (valueType === "boolean") return { group: "Other Registered Data", subgroup: "Boolean Values" };
  if (valueType === "event") return { group: "Other Registered Data", subgroup: "Event Values" };
  if (/json|vector|object|array/.test(valueType)) return { group: "Other Registered Data", subgroup: "Structured Values" };
  if (/number|integer|float|decimal/.test(valueType)) return { group: "Other Registered Data", subgroup: "Numeric Values" };
  return { group: "Other Registered Data", subgroup: "Text Values" };
}

function isTechnicalDefinition(row: RegistryDefinition, text: string) {
  return (row.kind === "derivation" && row.owner !== "data_field_registry") || /\bindicator\b|\bcandles\b|\bmomentum\b|\bvolatility\b|\bmarket structure\b|\bprice action\b|\bcross timeframe\b|\bcycles\b|\bstatistics\b|\btrend overlap\b|qmd\.field\.(ad|adosc|adx|alma|apo|atr|autocorrelation|awesome_oscillator|beta|body_|bollinger|cci|cdl_|chop|cmf|cmo|correlation|covariance|dema|doji|donchian|drawdown|ema_|engulfing|entropy|eom|evening_star|force_index|gap_shock|garman|hammer|harami|higher_high|historical_volatility|hma|ht_|hurst|ichimoku|indicators|inside_bar|kama|keltner|kst|kvo|linear_regression|log_return|lower_low|ma_ribbon|macd|mfi|minus_|mom|morning_star|multi_tf_|natr|nvi|obv|opening_range|outside_bar|parkinson|plus_|ppo|psar|pvi|pvt|qmd_generic_structure|qmd_structure_|range_|realized_volatility|roc|rolling_|rsi|rvi|rvol_|sharpe|shooting_star|sma|sortino|stoch|supertrend|t3|tema|three_|trend_alignment|trix|true_range|tsi|ultimate_oscillator|upper_wick|volatility|volume_ema|volume_sma|vwma|williams|wma|yang_zhang|zlema|zscore)/.test(text);
}

function technicalSubgroup(text: string) {
  if (/momentum|oscillator|rsi|stoch|cci|cmo|mfi|roc|apo|ppo|trix|tsi|williams|awesome|force_index/.test(text)) return "Momentum & Oscillators";
  if (/volatility|atr|true_range|bollinger|keltner|donchian|natr|beta|drawdown|sharpe|sortino|risk|range_(compression|expansion)/.test(text)) return "Volatility & Risk";
  if (/candlestick|price.action|body_|wick|doji|engulfing|hammer|harami|morning_star|shooting_star|three_black|three_white|inside_bar|outside_bar/.test(text)) return "Price Action & Patterns";
  if (/structure|swing|support|resistance|opening_range|breakout|higher_high|lower_low|flow_structure/.test(text)) return "Market Structure";
  if (/statistics|cycle|correlation|covariance|autocorrelation|entropy|hurst|zscore|skew|kurtosis|rolling_(mean|std)|ht_/.test(text)) return "Statistics & Cycles";
  if (/trend|moving_average|sma|ema|dema|tema|wma|hma|alma|kama|zlema|vwap|macd|adx|directional|ichimoku|psar|supertrend|ma_ribbon/.test(text)) return "Trend & Moving Averages";
  if (/qmd\.derivation\./.test(text)) return "Technical Collections";
  return "Technical Collections";
}

function dataDefinitionLookupOptions(definitions: RegistryDefinition[]): InventoryFilterOption[] {
  return [...groupDefinitions(definitions).entries()].flatMap(([group, subgroups]) => [...subgroups.entries()].flatMap(([subgroup, rows]) => rows.map((row) => ({
    description: row.description || `${row.presentation.kind_label}${row.documentation?.value_type ? ` · ${readable(row.documentation.value_type)}` : ""}`,
    group,
    label: displayLabel(row),
    subgroup,
    value: row.registry_id,
  }))));
}

function readable(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function displayLabel(definition: RegistryDefinition) { return definition.presentation_label?.trim() || definition.label; }
function isInformativeSourceSummary(value?: string) { const text = value?.trim() ?? ""; return Boolean(text) && !text.startsWith("A registered backend derivation") && !text.startsWith("Output published by the registered QMD producer") && !text.startsWith("Registered value published by"); }
function formatBand(band: { maximum: number | null; maximum_inclusive: boolean; minimum: number | null; minimum_inclusive: boolean; unit: string }) {
  const unit = band.unit === "usd" ? "$" : band.unit === "shares" ? " shares" : band.unit ? ` ${band.unit}` : "";
  const value = (boundary: number) => band.unit === "usd" ? `$${compactNumber(boundary)}` : `${compactNumber(boundary)}${unit}`;
  if (band.minimum == null && band.maximum == null) return "All values";
  if (band.minimum == null) return `${band.maximum_inclusive ? "At most" : "Below"} ${value(band.maximum!)}`;
  if (band.maximum == null) return `${band.minimum_inclusive ? "At least" : "Above"} ${value(band.minimum)}`;
  return `${value(band.minimum)} ${band.minimum_inclusive ? "≤" : "<"} value ${band.maximum_inclusive ? "≤" : "<"} ${value(band.maximum)}`;
}
function compactNumber(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2, notation: Math.abs(value) >= 1_000 ? "compact" : "standard" }).format(value); }
function uniqueRuleSetId(base: string, rows: DataRuleSet[]) { let value = base; let index = 2; const ids = new Set(rows.map((row) => row.rule_set_id)); while (ids.has(value)) value = `${base}-${index++}`; return value; }
function ruleSetIdFromHash() { const query = window.location.hash.split("?", 2)[1] ?? ""; return new URLSearchParams(query).get("rule_set_id") ?? ""; }
function replaceRuleSetHash(ruleSetId: string) { window.history.replaceState(null, "", ruleSetId ? `#rule-set-configuration?rule_set_id=${encodeURIComponent(ruleSetId)}` : "#rule-set-configuration"); }
