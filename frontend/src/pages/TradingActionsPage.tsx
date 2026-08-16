import { Copy, LockKeyhole, Search, Trash2, Workflow } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { InventoryFilterSelect } from "../app/components/InventoryFilterSelect";

export type TradingActionDefinition = {
  action_id: string;
  atomic: boolean;
  category: string;
  description: string;
  editable: boolean;
  kind: "intent" | "campaign_command";
  name: string;
  origin: "system" | "user";
  revision: number;
  runtime_action: string;
  sizing_modes: string[];
};

export type ActionPolicyDefinition = {
  action_id: string;
  atomic: boolean;
  authority: "manual" | "confirm" | "automatic";
  category: string;
  description: string;
  editable: boolean;
  enabled: boolean;
  maximum_uses: number;
  name: string;
  origin: "system" | "user";
  policy_id: string;
  quantity: { mode: string; value: number; minimum_remaining_quantity?: number };
  revision: number;
  settings: Record<string, boolean | number | string>;
  trigger: {
    mechanism_id?: string;
    operator?: "all" | "any";
    rule_set_ids: string[];
    summary: string;
    type: "rule_sets" | "strategy_mechanism";
  };
};

export type TradingActionsConfiguration = {
  definitions: TradingActionDefinition[];
  policies: ActionPolicyDefinition[];
};

type RuleSetOption = { description: string; name: string; rule_set_id: string };

export function TradingActionsPage({
  onChange,
  ruleSets,
  section,
}: {
  onChange: (value: TradingActionsConfiguration) => void;
  ruleSets: RuleSetOption[];
  section: TradingActionsConfiguration;
}) {
  const firstId = section.definitions[0]?.action_id ?? section.policies[0]?.policy_id ?? "";
  const [selectedId, setSelectedId] = useState(firstId);
  const [query, setQuery] = useState("");
  const selectedAction = section.definitions.find((row) => row.action_id === selectedId);
  const selectedPolicy = section.policies.find((row) => row.policy_id === selectedId);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleActions = section.definitions.filter((row) => matches(row, normalizedQuery));
  const visiblePolicies = section.policies.filter((row) => matches(row, normalizedQuery));

  function replacePolicy(next: ActionPolicyDefinition) {
    onChange({
      ...section,
      policies: section.policies.map((row) => row.policy_id === next.policy_id ? next : row),
    });
  }

  function duplicatePolicy(source: ActionPolicyDefinition) {
    const base = `${source.policy_id}-custom`;
    let policyId = base;
    let suffix = 2;
    while (section.policies.some((row) => row.policy_id === policyId)) policyId = `${base}-${suffix++}`;
    const next: ActionPolicyDefinition = {
      ...structuredClone(source),
      atomic: false,
      editable: true,
      name: `${source.name} custom`,
      origin: "user",
      policy_id: policyId,
      revision: 1,
    };
    onChange({ ...section, policies: [...section.policies, next] });
    setSelectedId(policyId);
  }

  function removePolicy(policyId: string) {
    const nextPolicies = section.policies.filter((row) => row.policy_id !== policyId);
    onChange({ ...section, policies: nextPolicies });
    setSelectedId(nextPolicies[0]?.policy_id ?? section.definitions[0]?.action_id ?? "");
  }

  return <div className="trading-actions-workbench">
    <aside className="trading-actions-catalog">
      <header><span>Registered behavior</span><strong>{section.definitions.length + section.policies.length} definitions</strong><p>Actions define intent. Policies define when a Strategy may use an action.</p></header>
      <label className="trading-actions-search"><Search aria-hidden="true" size={14} /><input aria-label="Search Trading Actions" onChange={(event) => setQuery(event.target.value)} placeholder="Search actions and policies" type="search" value={query} /></label>
      <div className="trading-actions-groups">
        <CatalogGroup count={visibleActions.length} label="Atomic actions">{visibleActions.map((row) => <CatalogButton description={`${readable(row.kind)} · ${readable(row.category)}`} id={row.action_id} key={row.action_id} name={row.name} onSelect={setSelectedId} selectedId={selectedId} />)}</CatalogGroup>
        <CatalogGroup count={visiblePolicies.length} label="Action policies">{visiblePolicies.map((row) => <CatalogButton description={`${readable(row.authority)} · ${row.trigger.type === "rule_sets" ? `${row.trigger.rule_set_ids.length} Rule Sets` : "Strategy mechanism"}`} id={row.policy_id} key={row.policy_id} name={row.name} onSelect={setSelectedId} selectedId={selectedId} />)}</CatalogGroup>
      </div>
    </aside>
    <main className="trading-actions-detail">
      {selectedAction ? <ActionDocument action={selectedAction} /> : null}
      {selectedPolicy ? <PolicyDocument actions={section.definitions} onDuplicate={() => duplicatePolicy(selectedPolicy)} onRemove={() => removePolicy(selectedPolicy.policy_id)} onReplace={replacePolicy} policy={selectedPolicy} ruleSets={ruleSets} /> : null}
      {!selectedAction && !selectedPolicy ? <div className="trading-actions-empty"><Workflow size={24} /><strong>Select an action or policy</strong><span>Choose a registered definition from the catalog.</span></div> : null}
    </main>
  </div>;
}

function ActionDocument({ action }: { action: TradingActionDefinition }) {
  return <article className="trading-action-document">
    <DocumentHeader eyebrow="Atomic Trading Action" id={action.action_id} name={action.name} summary={action.description} />
    <section className="action-contract-grid">
      <ContractCard label="Intent contract" rows={[["Action kind", readable(action.kind)], ["Runtime command", action.runtime_action], ["Category", readable(action.category)]]} />
      <ContractCard label="Allowed sizing" rows={action.sizing_modes.length ? action.sizing_modes.map((mode) => [readable(mode), "Available to a policy or lifecycle route"]) : [["No quantity", "Campaign control only"]]} />
    </section>
    <div className="atomic-definition-notice"><LockKeyhole size={14} /><span>Atomic actions are registered by the runtime and cannot be edited. Strategies, Action Policies, and Canvas controls reference this ID.</span></div>
  </article>;
}

function PolicyDocument({ actions, onDuplicate, onRemove, onReplace, policy, ruleSets }: {
  actions: TradingActionDefinition[];
  onDuplicate: () => void;
  onRemove: () => void;
  onReplace: (value: ActionPolicyDefinition) => void;
  policy: ActionPolicyDefinition;
  ruleSets: RuleSetOption[];
}) {
  const editable = policy.editable && !policy.atomic;
  const availableRuleSets = useMemo(() => ruleSets.filter((row) => !policy.trigger.rule_set_ids.includes(row.rule_set_id)), [policy.trigger.rule_set_ids, ruleSets]);
  const [ruleSetToAdd, setRuleSetToAdd] = useState("");
  return <article className="trading-action-document">
    <DocumentHeader actions={<><button className="button compact" onClick={onDuplicate} type="button"><Copy size={13} /> Duplicate policy</button>{editable ? <button className="button compact danger" onClick={onRemove} type="button"><Trash2 size={13} /> Remove</button> : null}</>} eyebrow={policy.atomic ? "Atomic Action Policy" : "Custom Action Policy"} id={policy.policy_id} name={policy.name} summary={policy.description} />
    <fieldset className="action-policy-fields" disabled={!editable}>
      <label><span>Name</span><input onChange={(event) => onReplace({ ...policy, name: event.target.value })} value={policy.name} /></label>
      <label><span>Description</span><textarea onChange={(event) => onReplace({ ...policy, description: event.target.value })} rows={2} value={policy.description} /></label>
      <div className="action-policy-field-grid">
        <label><span>Authority</span><select onChange={(event) => onReplace({ ...policy, authority: event.target.value as ActionPolicyDefinition["authority"] })} value={policy.authority}><option value="manual">Manual</option><option value="confirm">Confirm</option><option value="automatic">Automatic</option></select></label>
        <label><span>Trading Action</span><select onChange={(event) => onReplace({ ...policy, action_id: event.target.value })} value={policy.action_id}>{actions.filter((row) => row.kind === "intent").map((row) => <option key={row.action_id} value={row.action_id}>{row.name}</option>)}</select></label>
        <label><span>Quantity mode</span><select onChange={(event) => onReplace({ ...policy, quantity: { ...policy.quantity, mode: event.target.value } })} value={policy.quantity.mode}><option value="position_fraction">Position fraction</option><option value="initial_allocation_fraction">Initial allocation fraction</option><option value="mandate_fraction">Mandate fraction</option><option value="fixed_quantity">Fixed quantity</option></select></label>
        <label><span>Quantity value</span><input min="0" onChange={(event) => onReplace({ ...policy, quantity: { ...policy.quantity, value: Number(event.target.value) } })} step="0.01" type="number" value={policy.quantity.value} /></label>
      </div>
    </fieldset>
    <section className="action-policy-trigger">
      <header><div><span>Trigger</span><strong>{policy.trigger.type === "rule_sets" ? "Registered Rule Sets" : "Installed Strategy mechanism"}</strong></div></header>
      {policy.trigger.type === "rule_sets" ? <>
        {editable ? <div className="action-policy-rule-add"><InventoryFilterSelect ariaLabel="Rule Set to add" onChange={setRuleSetToAdd} options={availableRuleSets.map((row) => ({ description: row.description, group: "Rule Sets", label: row.name, subgroup: "Available", value: row.rule_set_id }))} placeholder="Choose a Rule Set" presentation="catalog" searchable searchPlaceholder="Search Rule Sets" showAllOnOpen value={ruleSetToAdd} /><button className="button compact" disabled={!ruleSetToAdd} onClick={() => { onReplace({ ...policy, trigger: { ...policy.trigger, rule_set_ids: [...policy.trigger.rule_set_ids, ruleSetToAdd] } }); setRuleSetToAdd(""); }} type="button">Add</button></div> : null}
        <div className="action-policy-rule-list">{policy.trigger.rule_set_ids.map((ruleSetId) => { const ruleSet = ruleSets.find((row) => row.rule_set_id === ruleSetId); return <div key={ruleSetId}><span><strong>{ruleSet?.name ?? ruleSetId}</strong><small>{ruleSet?.description ?? ruleSetId}</small></span>{editable ? <button aria-label={`Remove ${ruleSet?.name ?? ruleSetId}`} onClick={() => onReplace({ ...policy, trigger: { ...policy.trigger, rule_set_ids: policy.trigger.rule_set_ids.filter((value) => value !== ruleSetId) } })} type="button"><Trash2 size={13} /></button> : null}</div>; })}</div>
      </> : <div className="action-policy-mechanism"><code>{policy.trigger.mechanism_id}</code><span>{policy.trigger.summary}</span></div>}
    </section>
    {!editable ? <div className="atomic-definition-notice"><LockKeyhole size={14} /><span>This built-in policy is atomic. Duplicate it to create an editable, user-owned policy without changing the runtime default.</span></div> : null}
  </article>;
}

function DocumentHeader({ actions, eyebrow, id, name, summary }: { actions?: ReactNode; eyebrow: string; id: string; name: string; summary: string }) {
  return <header className="trading-action-document-header">
    <div className="trading-action-document-copy"><span>{eyebrow}</span><h2>{name}</h2><p>{summary}</p><code>{id}</code></div>
    {actions ? <div className="trading-action-document-actions">{actions}</div> : null}
  </header>;
}

function ContractCard({ label, rows }: { label: string; rows: string[][] }) {
  return <section><header>{label}</header>{rows.map(([name, value]) => <div key={`${name}-${value}`}><span>{name}</span><strong>{value}</strong></div>)}</section>;
}

function CatalogGroup({ children, count, label }: { children: ReactNode; count: number; label: string }) {
  return <section><header><strong>{label}</strong><span>{count}</span></header>{children}</section>;
}

function CatalogButton({ description, id, name, onSelect, selectedId }: { description: string; id: string; name: string; onSelect: (id: string) => void; selectedId: string }) {
  return <button aria-current={selectedId === id ? "true" : undefined} onClick={() => onSelect(id)} type="button"><span><strong>{name}</strong><small>{description}</small></span></button>;
}

function matches(row: { name: string; description: string }, query: string) {
  return !query || `${row.name} ${row.description}`.toLocaleLowerCase().includes(query);
}

function readable(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}
