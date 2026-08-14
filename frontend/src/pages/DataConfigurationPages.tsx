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
    return definitions.filter((row) => !needle || [row.label, row.description, row.registry_id, row.owner, row.kind, ...row.tags].some((value) => value.toLowerCase().includes(needle)));
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
            <div>{rows.map((row) => <button aria-current={selected?.registry_id === row.registry_id ? "true" : undefined} key={row.registry_id} onClick={() => setSelectedId(row.registry_id)} type="button"><span><strong>{row.label}</strong><small>{row.registry_id}</small></span><ChevronRight size={13} /></button>)}</div>
          </details>)}
        </details>)}
      </div>
    </aside>
    <main className="data-library-detail">{selected ? <DataDefinitionDetail definition={selected} onNavigate={setSelectedId} registry={registry} /> : <div className="data-library-empty"><Database size={22} /><span>No registered definition matches this search.</span></div>}</main>
  </div>;
}

function DataDefinitionDetail({ definition, onNavigate, registry }: { definition: RegistryDefinition; onNavigate: (id: string) => void; registry: InformationRegistry }) {
  const relationships = Object.entries(definition.relationships ?? {});
  return <article className="data-definition-document">
    <header><span>{definition.kind} · {definition.status}</span><h2>{definition.label}</h2><p>{definition.description}</p><div><code>{definition.registry_id}</code><button aria-label="Copy definition ID" onClick={() => void navigator.clipboard.writeText(definition.registry_id)} type="button"><Copy size={13} /> Copy ID</button></div></header>
    <section className="data-definition-contract"><h3>Semantic contract</h3><dl><div><dt>Definition type</dt><dd>{definition.presentation.kind_label}</dd></div><div><dt>Authority / producer</dt><dd>{definition.producer_id || definition.owner}</dd></div><div><dt>Version</dt><dd>{definition.version}</dd></div><div><dt>Configuration</dt><dd>{definition.configuration_mode}</dd></div><div><dt>Execution scopes</dt><dd>{definition.execution_scopes?.join(", ") || "Producer-owned"}</dd></div><div><dt>Tags</dt><dd>{definition.tags.join(", ") || "None"}</dd></div></dl></section>
    {definition.input_field_ids?.length || definition.output_field_ids?.length ? <section><h3>Data flow</h3><dl><div><dt>Inputs</dt><dd>{definition.input_field_ids?.join(", ") || "Source-owned"}</dd></div><div><dt>Outputs</dt><dd>{definition.output_field_ids?.join(", ") || definition.registry_id}</dd></div></dl></section> : null}
    {definition.parameters?.length ? <section><h3>Registered parameters</h3><div className="data-definition-parameters">{definition.parameters.map((parameter) => <div key={parameter.name}><strong>{parameter.label || parameter.name}</strong><span>{parameter.description || parameter.type || "Parameter"}</span><code>{parameter.default === undefined ? "No default" : String(parameter.default)}{parameter.unit ? ` ${parameter.unit}` : ""}</code></div>)}</div></section> : null}
    {relationships.length ? <section><h3>Relationships</h3>{relationships.map(([label, ids]) => <div className="data-definition-relations" key={label}><strong>{readable(label)}</strong><div>{ids.map((id) => registry.definitions.some((row) => row.registry_id === id) ? <button key={id} onClick={() => onNavigate(id)} type="button">{id}</button> : <span key={id}>{id}</span>)}</div></div>)}</section> : null}
    <footer><LockKeyhole size={14} /><span>This catalog documents registered authority. Definitions are changed in source registries and activated automatically when a downstream configuration references them.</span></footer>
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
  const valueFields = fields.filter((row) => row.kind === "field");
  function addCondition() { const source = valueFields[0]?.registry_id ?? ""; onChange({ ...ruleSet, conditions: [...ruleSet.conditions, { comparator: "greater_than", condition_id: `${ruleSet.rule_set_id}-condition-${ruleSet.conditions.length + 1}`, enabled: true, left_source_id: source, left_timeframe: "1d", right_source_id: "", right_timeframe: "", value: 0 }] }); }
  return <article className="rule-set-document"><header><span>{locked ? "Atomic rule set" : "Editable rule set"} · revision {ruleSet.revision ?? 1}</span><input aria-label="Rule set name" disabled={locked} onChange={(event) => onChange({ ...ruleSet, name: event.target.value })} value={ruleSet.name} /><textarea aria-label="Rule set description" disabled={locked} onChange={(event) => onChange({ ...ruleSet, description: event.target.value })} value={ruleSet.description} /><div><code>{ruleSet.rule_set_id}</code>{locked ? <button onClick={onDuplicate} type="button"><Copy size={13} /> Duplicate as custom</button> : <button className="danger" onClick={onDelete} type="button"><Trash2 size={13} /> Remove rule set</button>}</div></header><section className="rule-set-logic"><label><span>Condition logic</span><select disabled={locked} onChange={(event) => onChange({ ...ruleSet, operator: event.target.value as DataRuleSet["operator"] })} value={ruleSet.operator}><option value="all">All conditions</option><option value="any">Any condition</option><option value="score">Required score</option></select></label><span>{ruleSet.conditions.length} conditions</span></section><section className="rule-condition-list">{ruleSet.conditions.map((condition, index) => { const registered = valueFields.some((field) => field.registry_id === condition.left_source_id); return <div className="rule-condition-row" key={condition.condition_id}><span>{index + 1}</span><select aria-label={`Condition ${index + 1} field`} disabled={locked} onChange={(event) => onChange({ ...ruleSet, conditions: ruleSet.conditions.map((row) => row.condition_id === condition.condition_id ? { ...row, left_source_id: event.target.value } : row) })} value={condition.left_source_id}>{!registered && condition.left_source_id ? <option value={condition.left_source_id}>{condition.left_source_id}</option> : null}{valueFields.map((field) => <option key={field.registry_id} value={field.registry_id}>{field.label}</option>)}</select><select aria-label={`Condition ${index + 1} comparator`} disabled={locked} onChange={(event) => onChange({ ...ruleSet, conditions: ruleSet.conditions.map((row) => row.condition_id === condition.condition_id ? { ...row, comparator: event.target.value } : row) })} value={condition.comparator}><option value="greater_than">is greater than</option><option value="greater_than_or_equal">is at least</option><option value="less_than">is less than</option><option value="less_than_or_equal">is at most</option><option value="equal">equals</option><option value="not_equal">does not equal</option></select><input aria-label={`Condition ${index + 1} value`} disabled={locked} onChange={(event) => { const parsed = Number(event.target.value); onChange({ ...ruleSet, conditions: ruleSet.conditions.map((row) => row.condition_id === condition.condition_id ? { ...row, value: Number.isNaN(parsed) ? event.target.value : parsed } : row) }); }} value={String(condition.value ?? "")} />{!locked ? <button aria-label={`Remove condition ${index + 1}`} onClick={() => onChange({ ...ruleSet, conditions: ruleSet.conditions.filter((row) => row.condition_id !== condition.condition_id) })} type="button"><Trash2 size={13} /></button> : null}</div>; })}</section>{!locked ? <button className="data-library-add-condition" onClick={addCondition} type="button"><Plus size={14} /> Add condition</button> : <footer><LockKeyhole size={14} /><span>Built-in rule sets are atomic and cannot be edited. Duplicate this definition to create an editable custom rule set.</span></footer>}</article>;
}

function groupDefinitions(definitions: RegistryDefinition[]) {
  const groups = new Map<string, Map<string, RegistryDefinition[]>>();
  definitions.forEach((row) => {
    const group = semanticGroup(row);
    const subgroup = row.kind === "field" ? readable(row.owner) : row.kind === "derivation" ? "Derived fields" : "Event signals";
    const subgroups = groups.get(group) ?? new Map<string, RegistryDefinition[]>();
    subgroups.set(subgroup, [...(subgroups.get(subgroup) ?? []), row].sort((a, b) => a.label.localeCompare(b.label)));
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
function uniqueRuleSetId(base: string, rows: DataRuleSet[]) { let value = base; let index = 2; const ids = new Set(rows.map((row) => row.rule_set_id)); while (ids.has(value)) value = `${base}-${index++}`; return value; }
