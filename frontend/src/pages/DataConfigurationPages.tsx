import { ChevronRight, Copy, Database, LockKeyhole, Plus, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import type { InformationRegistry, RegistryDefinition } from "../app/components/DefinitionRegistry";

export type DataRuleCondition = {
  comparator: string;
  condition_id: string;
  enabled: boolean;
  left_source_id: string;
  left_timeframe: string;
  right_source_id: string;
  right_timeframe: string;
  value: boolean | number | string | null;
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

export function DataCatalogPage({ registry }: { registry: InformationRegistry }) {
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
      <label className="data-library-search"><Search size={15} /><input aria-label="Search data definitions" onChange={(event) => setQuery(event.target.value)} placeholder="Search names, IDs, producers" type="search" value={query} /></label>
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

export function RuleSetLibraryPage({ fields, onChange, ruleSets }: { fields: RegistryDefinition[]; onChange: (value: DataRuleSet[]) => void; ruleSets: DataRuleSet[] }) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(() => ruleSets[0]?.rule_set_id ?? "");
  const selected = ruleSets.find((row) => row.rule_set_id === selectedId) ?? ruleSets[0];
  const visible = ruleSets.filter((row) => !query.trim() || [row.name, row.description, row.rule_set_id].some((value) => value.toLowerCase().includes(query.trim().toLowerCase())));
  const grouped = new Map<string, DataRuleSet[]>();
  visible.forEach((row) => { const group = row.atomic ? "Built-in defaults" : row.publication_status === "published" ? "Published custom" : "Custom drafts"; grouped.set(group, [...(grouped.get(group) ?? []), row]); });

  function replace(next: DataRuleSet) { onChange(ruleSets.map((row) => row.rule_set_id === next.rule_set_id ? next : row)); }
  function create(source?: DataRuleSet) {
    const rule_set_id = uniqueRuleSetId(source ? `${source.rule_set_id}-copy` : "rule-set", ruleSets);
    const next: DataRuleSet = { atomic: false, conditions: source?.conditions.map((row, index) => ({ ...row, condition_id: `${rule_set_id}-condition-${index + 1}` })) ?? [], description: source ? `Custom copy of ${source.name}.` : "Describe the reusable decision represented by this rule set.", editable: true, enabled: true, name: source ? `${source.name} Copy` : "Untitled Rule Set", operator: source?.operator ?? "all", origin: "user", protected: false, publication_status: "draft", required_score: source?.required_score ?? 1, revision: 1, rule_set_id, scope: source?.scope ?? "shared" };
    onChange([...ruleSets, next]); setSelectedId(rule_set_id);
  }

  return <div className="data-library-workbench rule-set-library">
    <aside className="data-library-catalog"><header><span>Registered rule sets</span><strong>{visible.length} of {ruleSets.length}</strong><p>Atomic defaults are documentation-only. Custom rule sets can be composed from registered data definitions.</p></header><label className="data-library-search"><Search size={15} /><input aria-label="Search rule sets" onChange={(event) => setQuery(event.target.value)} placeholder="Search rule sets" type="search" value={query} /></label><button className="data-library-create" onClick={() => create()} type="button"><Plus size={14} /> Create rule set</button><div className="data-library-tree">{[...grouped.entries()].map(([group, rows]) => <details key={group} open><summary><span>{group}</span><em>{rows.length}</em></summary><details className="data-library-subgroup" open><summary><span>{group === "Built-in defaults" ? "Atomic definitions" : "User definitions"}</span><em>{rows.length}</em></summary><div>{rows.map((row) => <button aria-current={selected?.rule_set_id === row.rule_set_id ? "true" : undefined} key={row.rule_set_id} onClick={() => setSelectedId(row.rule_set_id)} type="button"><span><strong>{row.name}</strong><small>{row.description}</small></span>{row.atomic ? <LockKeyhole size={12} /> : <ChevronRight size={13} />}</button>)}</div></details></details>)}</div></aside>
    <main className="data-library-detail">{selected ? <RuleSetDetail fields={fields} onDelete={() => { onChange(ruleSets.filter((row) => row.rule_set_id !== selected.rule_set_id)); setSelectedId(ruleSets.find((row) => row.rule_set_id !== selected.rule_set_id)?.rule_set_id ?? ""); }} onDuplicate={() => create(selected)} onChange={replace} ruleSet={selected} /> : <div className="data-library-empty"><span>Create a rule set to begin.</span></div>}</main>
  </div>;
}

function RuleSetDetail({ fields, onChange, onDelete, onDuplicate, ruleSet }: { fields: RegistryDefinition[]; onChange: (value: DataRuleSet) => void; onDelete: () => void; onDuplicate: () => void; ruleSet: DataRuleSet }) {
  const locked = Boolean(ruleSet.atomic || ruleSet.editable === false);
  const definitions = fields.filter((row) => DATA_KINDS.has(row.kind));
  const definitionById = new Map(definitions.map((row) => [row.registry_id, row]));
  function replaceCondition(conditionId: string, next: DataRuleCondition) {
    onChange({ ...ruleSet, conditions: ruleSet.conditions.map((row) => row.condition_id === conditionId ? next : row) });
  }
  function addCondition() {
    const source = definitions[0];
    if (!source) return;
    const comparator = defaultRuleComparator(source);
    onChange({ ...ruleSet, conditions: [...ruleSet.conditions, { comparator, condition_id: `${ruleSet.rule_set_id}-condition-${ruleSet.conditions.length + 1}`, enabled: true, left_source_id: source.registry_id, left_timeframe: source.documentation?.timeframes?.[0] ?? "", right_source_id: "", right_timeframe: "", value: comparator === "is_true" ? null : 0 }] });
  }
  return <article className="rule-set-document">
    <header><span>{locked ? "Atomic rule set" : "Editable rule set"} · revision {ruleSet.revision ?? 1}</span><input aria-label="Rule set name" disabled={locked} onChange={(event) => onChange({ ...ruleSet, name: event.target.value })} value={ruleSet.name} /><textarea aria-label="Rule set description" disabled={locked} onChange={(event) => onChange({ ...ruleSet, description: event.target.value })} value={ruleSet.description} /><div><code>{ruleSet.rule_set_id}</code>{locked ? <button onClick={onDuplicate} type="button"><Copy size={13} /> Duplicate as custom</button> : <button className="danger" onClick={onDelete} type="button"><Trash2 size={13} /> Remove rule set</button>}</div></header>
    <section className="rule-set-logic"><label><span>Condition logic</span><select disabled={locked} onChange={(event) => onChange({ ...ruleSet, operator: event.target.value as DataRuleSet["operator"] })} value={ruleSet.operator}><option value="all">All conditions</option><option value="any">Any condition</option><option value="score">Required score</option></select></label><span>{ruleSet.conditions.length} condition{ruleSet.conditions.length === 1 ? "" : "s"}</span></section>
    <section className="rule-condition-list">{ruleSet.conditions.map((condition, index) => {
      const source = definitionById.get(condition.left_source_id);
      const target = condition.right_source_id ? definitionById.get(condition.right_source_id) : undefined;
      if (locked) return <RuleConditionStatement condition={condition} index={index} key={condition.condition_id} source={source} target={target} />;
      const comparators = ruleComparators(source, condition.comparator);
      return <div className="rule-condition-row rule-condition-editable" key={condition.condition_id}>
        <span>{index + 1}</span>
        <label><small>Data definition</small><select aria-label={`Condition ${index + 1} field`} onChange={(event) => {
          const nextSource = definitionById.get(event.target.value);
          if (!nextSource) return;
          const allowed = ruleComparators(nextSource, "");
          const comparator = allowed.some((row) => row.value === condition.comparator) ? condition.comparator : defaultRuleComparator(nextSource);
          replaceCondition(condition.condition_id, { ...condition, comparator, left_source_id: nextSource.registry_id, left_timeframe: nextSource.documentation?.timeframes?.[0] ?? "", right_source_id: comparator === "is_true" ? "" : condition.right_source_id, right_timeframe: comparator === "is_true" ? "" : condition.right_timeframe, value: comparator === "is_true" ? null : condition.value ?? 0 });
        }} value={condition.left_source_id}>{!source && condition.left_source_id ? <option value={condition.left_source_id}>{condition.left_source_id}</option> : null}{definitions.map((field) => <option key={field.registry_id} value={field.registry_id}>{displayLabel(field)}</option>)}</select><em>{condition.left_source_id}{condition.left_timeframe ? ` · ${condition.left_timeframe}` : ""}</em></label>
        <label><small>Comparison</small><select aria-label={`Condition ${index + 1} comparator`} onChange={(event) => { const comparator = event.target.value; replaceCondition(condition.condition_id, { ...condition, comparator, right_source_id: comparator === "is_true" ? "" : condition.right_source_id, right_timeframe: comparator === "is_true" ? "" : condition.right_timeframe, value: comparator === "is_true" ? null : condition.value ?? 0 }); }} value={condition.comparator}>{comparators.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}</select></label>
        {condition.comparator === "is_true" ? <div className="rule-condition-boolean"><small>Required state</small><strong>True</strong></div> : condition.right_source_id ? <label><small>Target definition</small><select aria-label={`Condition ${index + 1} target field`} onChange={(event) => replaceCondition(condition.condition_id, { ...condition, right_source_id: event.target.value })} value={condition.right_source_id}>{!target ? <option value={condition.right_source_id}>{condition.right_source_id}</option> : null}{definitions.map((field) => <option key={field.registry_id} value={field.registry_id}>{displayLabel(field)}</option>)}</select>{condition.comparator === "above_by_bps" ? <input aria-label={`Condition ${index + 1} basis point buffer`} onChange={(event) => replaceCondition(condition.condition_id, { ...condition, value: Number(event.target.value) })} step="any" type="number" value={Number(condition.value ?? 0)} /> : null}</label> : <label><small>Threshold</small><input aria-label={`Condition ${index + 1} value`} onChange={(event) => { const parsed = Number(event.target.value); replaceCondition(condition.condition_id, { ...condition, value: Number.isNaN(parsed) ? event.target.value : parsed }); }} step="any" type={isNumericRuleDefinition(source) ? "number" : "text"} value={String(condition.value ?? "")} /></label>}
        <button aria-label={`Remove condition ${index + 1}`} onClick={() => onChange({ ...ruleSet, conditions: ruleSet.conditions.filter((row) => row.condition_id !== condition.condition_id) })} type="button"><Trash2 size={13} /></button>
      </div>;
    })}</section>
    {!locked ? <button className="data-library-add-condition" onClick={addCondition} type="button"><Plus size={14} /> Add condition</button> : <footer><LockKeyhole size={14} /><span>Built-in rule sets are atomic and cannot be edited. Duplicate this definition to create an editable custom rule set.</span></footer>}
  </article>;
}

const RULE_LIBRARY_COMPARATORS = [
  { label: "is at least", value: "greater_or_equal" },
  { label: "is greater than", value: "greater_than" },
  { label: "is at most", value: "less_or_equal" },
  { label: "is less than", value: "less_than" },
  { label: "equals", value: "equals" },
  { label: "is true", value: "is_true" },
  { label: "is above by", value: "above_by_bps" },
];

function RuleConditionStatement({ condition, index, source, target }: { condition: DataRuleCondition; index: number; source?: RegistryDefinition; target?: RegistryDefinition }) {
  const relation = ruleComparatorLabel(condition.comparator, condition.value);
  const showTarget = condition.comparator !== "is_true";
  return <div className="rule-condition-row rule-condition-readonly">
    <span>{index + 1}</span>
    <div className="rule-condition-operand"><strong>{source ? displayLabel(source) : condition.left_source_id}</strong><small>{condition.left_source_id}{condition.left_timeframe ? ` · ${condition.left_timeframe}` : ""}</small></div>
    <em>{relation}</em>
    {showTarget ? <div className="rule-condition-operand rule-condition-target"><strong>{target ? displayLabel(target) : formatRuleConstant(condition.value, source)}</strong><small>{target ? `${condition.right_source_id}${condition.right_timeframe ? ` · ${condition.right_timeframe}` : ""}` : ruleValueContext(source)}</small></div> : <div className="rule-condition-boolean"><strong>True</strong><small>Boolean event state</small></div>}
  </div>;
}

function ruleComparators(source: RegistryDefinition | undefined, current: string) {
  const values = isBooleanRuleDefinition(source)
    ? ["is_true"]
    : isNumericRuleDefinition(source)
      ? ["greater_or_equal", "greater_than", "less_or_equal", "less_than", "equals"]
      : ["equals"];
  if (current && !values.includes(current)) values.push(current);
  return RULE_LIBRARY_COMPARATORS.filter((row) => values.includes(row.value));
}

function defaultRuleComparator(source?: RegistryDefinition) { return isBooleanRuleDefinition(source) ? "is_true" : isNumericRuleDefinition(source) ? "greater_or_equal" : "equals"; }
function isBooleanRuleDefinition(source?: RegistryDefinition) { const valueType = source?.documentation?.value_type?.toLowerCase(); return valueType === "boolean" || (source?.kind === "signal" && valueType === "event"); }
function isNumericRuleDefinition(source?: RegistryDefinition) { return /number|integer|float|score|decimal|currency|percent|ratio|basis/.test(source?.documentation?.value_type?.toLowerCase() ?? ""); }
function ruleComparatorLabel(comparator: string, value: DataRuleCondition["value"]) { if (comparator === "above_by_bps") return `is ${formatCompactNumber(Number(value ?? 0))} bps above`; return RULE_LIBRARY_COMPARATORS.find((row) => row.value === comparator)?.label ?? readable(comparator).toLowerCase(); }
function ruleValueContext(source?: RegistryDefinition) { const unit = source?.documentation?.unit; return unit && unit !== "scalar" && unit !== "producer_defined" ? readable(unit) : "Fixed value"; }
function formatRuleConstant(value: DataRuleCondition["value"], source?: RegistryDefinition) {
  if (value === null || value === undefined || value === "") return "Missing value";
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value !== "number") return String(value);
  const unit = source?.documentation?.unit?.toLowerCase() ?? "";
  if (unit === "currency" || unit === "usd") return `$${formatCompactNumber(value)}`;
  if (unit.includes("percent")) return `${formatCompactNumber(value)}%`;
  if (unit.includes("share")) return `${formatCompactNumber(value)} shares`;
  return formatCompactNumber(value);
}
function formatCompactNumber(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 4, notation: Math.abs(value) >= 1_000 ? "compact" : "standard" }).format(value); }

function groupDefinitions(definitions: RegistryDefinition[]) {
  const groups = new Map<string, Map<string, RegistryDefinition[]>>();
  definitions.forEach((row) => {
    const group = semanticGroup(row);
    const subgroup = row.kind === "field" ? readable(row.owner) : row.kind === "derivation" ? "Derived fields" : "Event signals";
    const subgroups = groups.get(group) ?? new Map<string, RegistryDefinition[]>();
    subgroups.set(subgroup, [...(subgroups.get(subgroup) ?? []), row].sort((a, b) => displayLabel(a).localeCompare(displayLabel(b))));
    groups.set(group, subgroups);
  });
  return groups;
}

function semanticGroup(row: RegistryDefinition) {
  const text = `${row.registry_id} ${row.owner} ${row.tags.join(" ")}`.toLowerCase();
  if (row.kind === "signal") return "Signals & Events";
  if (row.kind === "derivation") return "Derived Analytics";
  if (/news|text.intelligence/.test(text)) return "News & Text Intelligence";
  if (/sec|fundamental|xbrl/.test(text)) return "Fundamentals & SEC";
  if (/reference|identity|company|symbol|listing/.test(text)) return "Reference & Identity";
  if (/quality|coverage|fresh|stale|null/.test(text)) return "Quality & Coverage";
  return "Market & Tape";
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
