import { BriefcaseBusiness, ChevronDown, ChevronRight, FileInput, GitBranch, PencilLine, Plus, Search, Send, ShieldCheck, Target, Trash2 } from "lucide-react";
import { useState } from "react";

import { InventoryFilterSelect } from "../../../app/components/InventoryFilterSelect";
import { formatSemanticNumber } from "../../../app/format";
import {
  BooleanField,
  ConfigurationNarrative,
  EmptyState,
  FieldHelp,
  NumberField,
  SelectField,
  TextField,
  readableLabel,
  round,
} from "../components/ConfigurationFields";
import type {
  AddStep,
  CapitalRequestConfig,
  Draft,
  EntryRules,
  ExecutionPolicyConfig,
  OrderIntentConfig,
  Primitive,
  ProtectionProfileConfig,
  RuleCondition,
  RuleExpression,
  RuleGroup,
  RuleSetDefinition,
  RuleStage,
  StrategyInput,
} from "../contracts";
import { deepClone, field, uniqueId } from "../utilities";
export const RULE_STAGE_META = {
  opportunity: {
    label: "Opportunity conditions",
    summary: "Evidence that identifies a possible initial entry.",
  },
  confirmation: {
    label: "Confirmation requirements",
    summary: "Each rule set owns its own condition logic and optional required score.",
  },
  blockers: {
    label: "Entry blockers",
    summary: "A passing blocker prevents a new position even when opportunity and confirmation pass.",
  },
} as const;

export const RULE_STAGE_STORY: Record<keyof EntryRules, string[]> = {
  opportunity: [
    "Opportunity determines whether a ticker becomes an entry candidate. Each rule set is an independent detection path. Stage logic selects whether any path or every path must pass before confirmation is evaluated.",
  ],
  confirmation: [
    "Confirmation determines whether the detected opportunity is actionable now. Each rule set combines its own conditions and optional required score; confirmation settings do not create a shared global score.",
  ],
  blockers: [
    "A passing blocker prevents new exposure even when opportunity and confirmation pass. Use blockers for strategy-level invalidation such as stale evidence or an incompatible regime; account exposure and loss limits belong to Portfolio.",
  ],
};

export function ruleSetLookupOptions(ruleSets: RuleSetDefinition[]) {
  return ruleSets.map((ruleSet) => {
    const activeConditions = ruleSet.conditions.filter((condition) => condition.enabled).length;
    const custom = ruleSet.atomic !== true;
    return {
      description: ruleSet.description.trim() || `${activeConditions} active condition${activeConditions === 1 ? "" : "s"} · ${readableLabel(ruleSet.operator)} logic`,
      group: custom ? "Custom Rule Sets" : "Built-in Rule Sets",
      label: ruleSet.name,
      subgroup: custom
        ? ruleSet.publication_status === "published" ? "Published" : "Drafts"
        : "Built-in definitions",
      value: ruleSet.rule_set_id,
    };
  });
}

export function isEditableCustomRuleSet(ruleSet: RuleSetDefinition | undefined) {
  return Boolean(ruleSet?.atomic === false && ruleSet.origin === "user" && ruleSet.editable !== false);
}

export const COMPARATOR_OPTIONS = [
  { label: "Is above by", value: "above_by_bps" },
  { label: "Is at least", value: "greater_or_equal" },
  { label: "Is greater than", value: "greater_than" },
  { label: "Is at most", value: "less_or_equal" },
  { label: "Is less than", value: "less_than" },
  { label: "Equals", value: "equals" },
  { label: "Is true", value: "is_true" },
];

export function DecisionRulesEditor({ catalog = [], onChange, onRuleSetEdit = () => undefined, ruleSetCatalog = [], rules, stageName, summary, title }: {
  catalog?: StrategyInput[];
  importRules?: EntryRules;
  onChange: (value: EntryRules) => void;
  onRuleSetEdit?: (ruleSetId: string, created?: RuleSetDefinition) => void;
  ruleSetCatalog?: RuleSetDefinition[];
  rules: EntryRules;
  stageName?: keyof EntryRules;
  summary: string;
  title: string;
}) {
  const [selectedRuleSetId, setSelectedRuleSetId] = useState("");
  const stageNames = stageName ? [stageName] : (Object.keys(RULE_STAGE_META) as Array<keyof EntryRules>);
  function replaceStage(name: keyof EntryRules, stage: RuleStage) { onChange({ ...rules, [name]: stage }); }
  return <div className={`strategy-rule-editor${stageName ? " strategy-entry-rule-editor" : ""}`}>
    {!stageName ? <div className="strategy-source-legend"><GitBranch size={18} /><div><strong>{title}</strong><p>{summary}</p></div></div> : null}
    {stageNames.map((name) => {
      const stage = rules[name];
      return <section className="strategy-entry-rule-page strategy-rule-composition-page" key={name}>
        <header className="strategy-rule-composition-toolbar"><div><span>Registered rule sets</span><strong>{RULE_STAGE_META[name].label}</strong><small>Select reusable definitions from the Rule Set Library. Create or edit definitions in that authoritative catalog.</small></div><div><InventoryFilterSelect ariaLabel="Rule set to add" className="configuration-lookup-button strategy-rule-set-lookup" onChange={setSelectedRuleSetId} optionLimit={0} options={ruleSetLookupOptions(ruleSetCatalog)} placeholder="Choose a rule set" presentation="catalog" searchable searchPlaceholder="Search Rule Sets" showAllOnOpen value={selectedRuleSetId} /><button className="button compact" disabled={!selectedRuleSetId} onClick={() => replaceStage(name, { expression: appendRuleExpression(stage.expression, { kind: "rule_set", rule_set_id: selectedRuleSetId }) })} type="button"><Plus size={14} /> Add</button><a className="button compact secondary" href="#rule-set-configuration"><Plus size={14} /> Create in Rule Sets</a></div></header>
        {stage.expression ? <RuleExpressionEditor catalog={catalog} expression={stage.expression} onChange={(expression) => replaceStage(name, { expression })} onEditRuleSet={onRuleSetEdit} ruleSets={ruleSetCatalog} /> : <EmptyState detail="Add a predefined rule set to compose this decision." title="No rule-set expression" />}
        {stage.expression ? <div className="strategy-rule-expression-summary"><span>Final logic</span><strong>{formatRuleExpression(stage.expression, ruleSetCatalog)}</strong></div> : null}
      </section>;
    })}
  </div>;
}

export function appendRuleExpression(expression: RuleExpression | undefined, child: RuleExpression): RuleExpression {
  if (!expression) return { children: [child], kind: "operator", operator: "and" };
  if (expression.kind === "operator") return { ...expression, children: [...expression.children, child] };
  return { children: [expression, child], kind: "operator", operator: "and" };
}

export function formatRuleExpression(expression: RuleExpression, ruleSets: RuleSetDefinition[]): string {
  if (expression.kind === "rule_set") return ruleSets.find((row) => row.rule_set_id === expression.rule_set_id)?.name ?? "Missing rule set";
  return `(${expression.children.map((child) => formatRuleExpression(child, ruleSets)).join(` ${expression.operator.toUpperCase()} `)})`;
}

export function formatRuleCondition(condition: RuleCondition, catalog: StrategyInput[]): string {
  const left = inputSource(catalog, condition.left_source_id);
  const right = condition.right_source_id ? inputSource(catalog, condition.right_source_id) : null;
  const sourceReference = (source: StrategyInput | undefined | null, sourceId: string, timeframe: string) => `${source?.label ?? readableLabel(sourceId)}${timeframe ? ` (${timeframe})` : ""}`;
  const leftReference = sourceReference(left, condition.left_source_id, condition.left_timeframe);
  const rightReference = condition.right_source_id
    ? sourceReference(right, condition.right_source_id, condition.right_timeframe)
    : condition.value === null || condition.value === undefined ? "an unset threshold" : formatRuleThreshold(condition.value, left);
  if (condition.comparator === "is_true") return `${leftReference} is true`;
  if (condition.comparator === "above_by_bps") return `${leftReference} is ${formatSemanticNumber(condition.value ?? 0)} bps above ${rightReference}`;
  const comparator = {
    equals: "equals",
    greater_or_equal: "is at least",
    greater_than: "is greater than",
    less_or_equal: "is at most",
    less_than: "is less than",
  }[condition.comparator] ?? readableLabel(condition.comparator).toLocaleLowerCase();
  return `${leftReference} ${comparator} ${rightReference}`;
}

export function formatRuleThreshold(value: unknown, source: StrategyInput | undefined | null): string {
  return formatSemanticNumber(value, source?.unit);
}

export function ruleSetMeaning(ruleSet: Pick<RuleSetDefinition, "conditions" | "enabled" | "operator" | "required_score">, catalog: StrategyInput[]): string {
  const enabledConditions = ruleSet.conditions.filter((condition) => condition.enabled);
  if (!enabledConditions.length) return "No enabled conditions are configured, so this rule set cannot pass.";
  const conditions = enabledConditions.map((condition) => formatRuleCondition(condition, catalog));
  let meaning: string;
  if (ruleSet.operator === "score") {
    const score = ruleSet.required_score <= 1 ? `${Math.round(ruleSet.required_score * 100)}%` : String(ruleSet.required_score);
    meaning = `At least ${score} of these conditions must pass: ${conditions.join("; ")}.`;
  } else {
    meaning = `${conditions.join(ruleSet.operator === "all" ? " AND " : " OR ")}.`;
  }
  const disabledCount = ruleSet.conditions.length - enabledConditions.length;
  return `${meaning}${disabledCount ? ` ${disabledCount} disabled condition${disabledCount === 1 ? " is" : "s are"} excluded.` : ""}`;
}

export function RuleEvidenceOperand({ catalog, sourceId, timeframe }: { catalog: StrategyInput[]; sourceId: string; timeframe: string }) {
  const source = inputSource(catalog, sourceId);
  return <span className="strategy-rule-evidence-operand"><strong>{source?.label ?? readableLabel(sourceId)}</strong>{timeframe ? <small>{timeframe}</small> : null}</span>;
}

export function RuleConditionMeaning({ catalog, condition }: { catalog: StrategyInput[]; condition: RuleCondition }) {
  const relation = condition.comparator === "above_by_bps"
    ? `${formatSemanticNumber(condition.value ?? 0)} bps above`
    : ({
        equals: "equals",
        greater_or_equal: "is at least",
        greater_than: "is greater than",
        is_true: "is true",
        less_or_equal: "is at most",
        less_than: "is less than",
      }[condition.comparator] ?? readableLabel(condition.comparator).toLocaleLowerCase());
  const showTarget = condition.comparator !== "is_true";
  return <div aria-hidden="true" className="strategy-rule-evidence-expression">
    <RuleEvidenceOperand catalog={catalog} sourceId={condition.left_source_id} timeframe={condition.left_timeframe} />
    <span className="strategy-rule-evidence-relation">{relation}</span>
    {showTarget ? condition.right_source_id
      ? <RuleEvidenceOperand catalog={catalog} sourceId={condition.right_source_id} timeframe={condition.right_timeframe} />
      : <strong className="strategy-rule-evidence-value">{condition.value === null || condition.value === undefined ? "Unset" : formatRuleThreshold(condition.value, inputSource(catalog, condition.left_source_id))}</strong> : null}
  </div>;
}

export function RuleSetMeaning({ catalog, ruleSet }: { catalog: StrategyInput[]; ruleSet: Pick<RuleSetDefinition, "conditions" | "enabled" | "operator" | "required_score"> }) {
  const enabledConditions = ruleSet.conditions.filter((condition) => condition.enabled);
  const disabledCount = ruleSet.conditions.length - enabledConditions.length;
  const score = ruleSet.required_score <= 1 ? `${Math.round(ruleSet.required_score * 100)}%` : String(ruleSet.required_score);
  const logic = ruleSet.operator === "score" ? `Score ≥ ${score}` : ruleSet.operator.toLocaleUpperCase();
  return <div className="strategy-rule-set-meaning" data-enabled={ruleSet.enabled ? "true" : "false"}>
    <span className="sr-only">{ruleSetMeaning(ruleSet, catalog)}</span>
    <header aria-hidden="true"><span>{ruleSet.enabled ? "Passes when" : "If enabled"}</span><strong>{logic}</strong></header>
    {enabledConditions.length ? <div className="strategy-rule-evidence-list">
      {enabledConditions.map((condition, index) => <div className="strategy-rule-evidence-clause" key={condition.condition_id}>{index ? <span className="strategy-rule-evidence-logic">{ruleSet.operator === "all" ? "AND" : ruleSet.operator === "any" ? "OR" : "PLUS"}</span> : null}<RuleConditionMeaning catalog={catalog} condition={condition} /></div>)}
    </div> : <p className="strategy-rule-evidence-empty">No enabled conditions are configured.</p>}
    {disabledCount ? <small className="strategy-rule-evidence-disabled">{disabledCount} disabled condition{disabledCount === 1 ? "" : "s"} excluded</small> : null}
  </div>;
}

export function RuleExpressionEditor({ catalog, expression, onChange, onEditRuleSet, ruleSets }: { catalog: StrategyInput[]; expression: RuleExpression; onChange: (value: RuleExpression) => void; onEditRuleSet: (ruleSetId: string) => void; ruleSets: RuleSetDefinition[] }) {
  if (expression.kind === "rule_set") {
    const ruleSet = ruleSets.find((row) => row.rule_set_id === expression.rule_set_id);
    return <article className="strategy-rule-expression-leaf"><GitBranch size={16} /><div className="strategy-rule-expression-copy"><strong>{ruleSet?.name ?? "Missing rule set"}</strong><small>{ruleSet ? `${ruleSet.conditions.filter((condition) => condition.enabled).length} active condition${ruleSet.conditions.filter((condition) => condition.enabled).length === 1 ? "" : "s"} · ${readableLabel(ruleSet.operator)}` : "The referenced catalog definition is unavailable."}</small>{ruleSet ? <RuleSetMeaning catalog={catalog} ruleSet={ruleSet} /> : null}</div>{isEditableCustomRuleSet(ruleSet) ? <button className="button compact" onClick={() => onEditRuleSet(expression.rule_set_id)} type="button"><PencilLine size={13} /> Modify</button> : null}</article>;
  }
  const fallbackRuleSet = ruleSets[0];
  return <section className="strategy-rule-expression-group"><header><span className="strategy-rule-parenthesis">(</span><div role="group" aria-label="Expression operator"><button aria-pressed={expression.operator === "and"} onClick={() => onChange({ ...expression, operator: "and" })} type="button">AND</button><button aria-pressed={expression.operator === "or"} onClick={() => onChange({ ...expression, operator: "or" })} type="button">OR</button></div><button className="button compact secondary" disabled={!fallbackRuleSet} onClick={() => fallbackRuleSet && onChange({ ...expression, children: [...expression.children, { children: [{ kind: "rule_set", rule_set_id: fallbackRuleSet.rule_set_id }], kind: "operator", operator: expression.operator === "and" ? "or" : "and" }] })} type="button">( ) Add group</button></header><div>{expression.children.map((child, index) => <div className="strategy-rule-expression-child" key={`${child.kind}-${index}`}><RuleExpressionEditor catalog={catalog} expression={child} onChange={(next) => onChange({ ...expression, children: expression.children.map((row, childIndex) => childIndex === index ? next : row) })} onEditRuleSet={onEditRuleSet} ruleSets={ruleSets} /><button aria-label="Remove from expression" className="button compact danger" disabled={expression.children.length === 1} onClick={() => onChange({ ...expression, children: expression.children.filter((_, childIndex) => childIndex !== index) })} type="button"><Trash2 size={13} /></button>{index < expression.children.length - 1 ? <span className="strategy-rule-expression-operator">{expression.operator.toUpperCase()}</span> : null}</div>)}</div><span className="strategy-rule-parenthesis">)</span></section>;
}

export function RuleStageComposition({ catalog, label, onChange, onEditRuleSet, ruleSets, stage }: { catalog: StrategyInput[]; label: string; onChange: (value: RuleStage) => void; onEditRuleSet: (ruleSetId: string) => void; ruleSets: RuleSetDefinition[]; stage: RuleStage }) {
  const [selectedRuleSetId, setSelectedRuleSetId] = useState("");
  return <section className="strategy-rule-composition-page"><header className="strategy-rule-composition-toolbar"><div><span>Predefined rule sets</span><strong>{label}</strong><small>Choose catalog definitions, then combine them with nested AND and OR groups.</small></div><div><InventoryFilterSelect ariaLabel={`${label} Rule Set to add`} className="configuration-lookup-button strategy-rule-set-lookup" onChange={setSelectedRuleSetId} optionLimit={0} options={ruleSetLookupOptions(ruleSets)} placeholder="Choose a rule set" presentation="catalog" searchable searchPlaceholder="Search Rule Sets" showAllOnOpen value={selectedRuleSetId} /><button className="button compact" disabled={!selectedRuleSetId} onClick={() => onChange({ expression: appendRuleExpression(stage.expression, { kind: "rule_set", rule_set_id: selectedRuleSetId }) })} type="button"><Plus size={14} /> Add</button></div></header>{stage.expression ? <RuleExpressionEditor catalog={catalog} expression={stage.expression} onChange={(expression) => onChange({ expression })} onEditRuleSet={onEditRuleSet} ruleSets={ruleSets} /> : <EmptyState detail="Add a catalog rule set to define this lifecycle decision." title="No rule sets selected" />}{stage.expression ? <div className="strategy-rule-expression-summary"><span>Final logic</span><strong>{formatRuleExpression(stage.expression, ruleSets)}</strong></div> : null}</section>;
}

export type LegacyEntryRules = Record<keyof EntryRules, RuleStage & { groups: RuleGroup[]; operator: "all" | "any" }>;

export function LegacyDecisionRulesEditor({ catalog, importRules, onChange, rules, stageName, summary, title }: {
  catalog: StrategyInput[];
  importRules?: LegacyEntryRules;
  onChange: (value: LegacyEntryRules) => void;
  rules: LegacyEntryRules;
  stageName?: keyof EntryRules;
  summary: string;
  title: string;
}) {
  const [openedGroupIds, setOpenedGroupIds] = useState<Set<string>>(new Set());
  if (!rules) return <EmptyState title="Decision rules unavailable" detail="Reload the configuration session to receive the typed source model." />;

  function replaceStage(stageName: keyof EntryRules, stage: RuleStage) {
    onChange({ ...rules, [stageName]: stage });
  }

  function replaceGroup(stageName: keyof EntryRules, groupId: string, group: RuleGroup) {
    const stage = rules[stageName];
    replaceStage(stageName, { ...stage, groups: stage.groups.map((row) => row.group_id === groupId ? group : row) });
  }

  function addGroup(stageName: keyof EntryRules) {
    const stage = rules[stageName];
    const source = catalog[0];
    const groupId = uniqueId(`${stageName}-rule`, stage.groups.map((row) => row.group_id));
    const condition: RuleCondition = {
      comparator: source.value_type === "boolean" ? "is_true" : "greater_or_equal",
      condition_id: `${groupId}-condition`,
      enabled: true,
      left_source_id: source.source_id,
      left_timeframe: source.timeframes[0],
      right_source_id: "",
      right_timeframe: "",
      value: source.value_type === "boolean" ? null : 0,
    };
    replaceStage(stageName, {
      ...stage,
      groups: [{
        conditions: [condition],
        enabled: true,
        group_id: groupId,
        label: "New rule set",
        operator: "all",
        required_score: 1,
      }, ...stage.groups],
    });
    setOpenedGroupIds((current) => new Set(current).add(groupId));
  }

  function importStage(stageName: keyof EntryRules) {
    const sourceGroups = importRules?.[stageName]?.groups ?? [];
    const takenIds = rules[stageName].groups.map((row) => row.group_id);
    const imported = sourceGroups.map((group) => {
      const groupId = uniqueId(`${group.group_id}-copy`, takenIds);
      takenIds.push(groupId);
      return {
        ...deepClone(group),
        group_id: groupId,
        label: `${group.label} · imported`,
        conditions: group.conditions.map((condition, index) => ({
          ...condition,
          condition_id: `${groupId}-condition-${index + 1}`,
        })),
      };
    });
    replaceStage(stageName, {
      ...rules[stageName],
      groups: [...imported, ...rules[stageName].groups],
    });
    setOpenedGroupIds((current) => new Set([...current, ...imported.map((row) => row.group_id)]));
  }

  const stageNames = stageName ? [stageName] : (Object.keys(RULE_STAGE_META) as Array<keyof EntryRules>);

  return (
    <div className={`strategy-rule-editor${stageName ? " strategy-entry-rule-editor" : ""}`}>
      {!stageName ? <div className="strategy-source-legend">
        <GitBranch size={18} />
        <div>
          <strong>{title}</strong>
          <p>{summary}</p>
        </div>
      </div> : null}
      {stageNames.map((currentStageName) => {
        const stage = rules[currentStageName];
        const meta = RULE_STAGE_META[currentStageName];
        if (stageName) return <section className="strategy-entry-rule-page" key={currentStageName}>
          <header className="strategy-entry-rule-toolbar">
            <SelectField
              help={{ role: "Combines the enabled rule sets on this page.", values: { "Any rule set": "The page passes when one enabled rule set passes.", "All rule sets": "Every enabled rule set must pass." } }}
              label="Rule-set logic"
              onChange={(operator) => replaceStage(currentStageName, { ...stage, operator: operator as "all" | "any" })}
              options={[{ label: "Any rule set", value: "any" }, { label: "All rule sets", value: "all" }]}
              value={stage.operator}
            />
            <button className="button compact" onClick={() => addGroup(currentStageName)} type="button"><Plus size={14} /> Add rule set</button>
          </header>
          <div className="strategy-rule-groups">
            {stage.groups.map((group) => <RuleGroupEditor catalog={catalog} defaultOpen={openedGroupIds.has(group.group_id)} group={group} key={group.group_id} onChange={(next) => replaceGroup(currentStageName, group.group_id, next)} onRemove={() => replaceStage(currentStageName, { ...stage, groups: stage.groups.filter((row) => row.group_id !== group.group_id) })} removable={stage.groups.length > 1} />)}
            {!stage.groups.length ? <EmptyState detail={`Add the first ${readableLabel(currentStageName)} rule set to define this part of entry evidence.`} title="No rule sets configured" /> : null}
          </div>
        </section>;
        return (
          <section className="strategy-rule-stage-chapter" key={currentStageName}>
          <ConfigurationNarrative heading={meta.label} paragraphs={RULE_STAGE_STORY[currentStageName]} />
          <details className="strategy-rule-stage" data-stage={currentStageName}>
            <summary><div><span>{currentStageName}</span><strong>{meta.label}</strong><p>{meta.summary}</p></div><span>{stage.groups.length} rule sets</span><ChevronDown size={16} /></summary>
            <div className="strategy-rule-stage-body">
            <header>
              <div><span>{currentStageName}</span><strong>{meta.label}</strong><p>{meta.summary}</p></div>
              <div className="strategy-stage-controls">
                  <SelectField
                    help={{ role: "Combines the enabled rule sets in this phase group.", values: { "Any rule set": "The phase group passes when one enabled rule set passes.", "All rule sets": "Every enabled rule set must pass." } }}
                    label="Stage logic"
                    onChange={(operator) => replaceStage(currentStageName, { ...stage, operator: operator as "all" | "any" })}
                    options={[{ label: "Any rule set", value: "any" }, { label: "All rule sets", value: "all" }]}
                    value={stage.operator}
                  />
                <button className="button compact" onClick={() => addGroup(currentStageName)} type="button"><Plus size={14} /> Add rule set</button>
                {importRules?.[currentStageName]?.groups?.length ? (
                  <button className="button compact secondary" onClick={() => importStage(currentStageName)} type="button"><FileInput size={14} /> Add initial rules</button>
                ) : null}
              </div>
            </header>
            <div className="strategy-rule-groups">
              {stage.groups.map((group) => (
                <RuleGroupEditor
                  catalog={catalog}
                  group={group}
                  key={group.group_id}
                  defaultOpen={openedGroupIds.has(group.group_id)}
                  onChange={(next) => replaceGroup(currentStageName, group.group_id, next)}
                  onRemove={() => replaceStage(currentStageName, { ...stage, groups: stage.groups.filter((row) => row.group_id !== group.group_id) })}
                  removable={stage.groups.length > 1}
                />
              ))}
            </div>
            </div>
          </details>
          </section>
        );
      })}
    </div>
  );
}

export function RuleGroupEditor({ catalog, defaultOpen = false, group, hideName = false, onChange, onRemove, removable }: {
  catalog: StrategyInput[];
  defaultOpen?: boolean;
  group: RuleGroup;
  hideName?: boolean;
  onChange: (value: RuleGroup) => void;
  onRemove: () => void;
  removable: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  function replaceCondition(conditionId: string, condition: RuleCondition) {
    onChange({ ...group, conditions: group.conditions.map((row) => row.condition_id === conditionId ? condition : row) });
  }

  function addCondition() {
    const source = catalog[0];
    const comparator = source.filter_operators?.[0] ?? (source.value_type === "boolean" ? "is_true" : "greater_or_equal");
    const conditionId = uniqueId(`${group.group_id}-condition`, group.conditions.map((row) => row.condition_id));
    onChange({
      ...group,
      conditions: [...group.conditions, {
        comparator,
        condition_id: conditionId,
        enabled: true,
        left_source_id: source.source_id,
        left_timeframe: source.timeframes[0],
        right_source_id: "",
        right_timeframe: "",
        value: comparator === "is_true" ? null : 0,
      }],
    });
  }

  return (
    <details className="strategy-rule-group" data-enabled={group.enabled ? "true" : "false"} onToggle={(event) => setOpen(event.currentTarget.open)} open={open}>
      <summary>
        <span className="strategy-rule-state" />
        <div><strong>{group.label}</strong><small>{group.conditions.length} conditions · {group.operator === "all" ? "all required" : "any may pass"}</small></div>
        <span>{group.enabled ? "Enabled" : "Disabled"}</span>
        <ChevronDown size={16} />
      </summary>
      <div className="strategy-rule-group-body">
      <ConfigurationNarrative heading={group.label} paragraphs={[
        "This rule set combines enabled conditions into one result. Condition logic selects all, any, or a required passing fraction. Each condition compares a causal source and timeframe with a constant or another source using only data available at evaluation time.",
      ]} />
      <header className="strategy-rule-toolbar">
        <div className="strategy-rule-toolbar-heading"><span>Rule set controls</span><p>Name the evidence bundle, choose how its conditions combine, and decide whether it participates in evaluation.</p></div>
        <div className="strategy-rule-toolbar-fields">
          {!hideName ? <label className="strategy-rule-name"><span>Rule set name</span><input onChange={(event) => onChange({ ...group, label: event.target.value })} value={group.label} /></label> : null}
          <label><span>Condition logic <FieldHelp title="Condition logic" content={{ role: "Defines how this rule set converts its enabled conditions into one pass or fail result.", values: { "All must pass": "Every enabled condition must be true.", "Any may pass": "One enabled condition is enough.", "Required score": "The fraction of enabled conditions that pass must meet this rule set's own score." }, note: "The score is local to this rule set. There is no global confirmation score." }} /></span><select onChange={(event) => onChange({ ...group, operator: event.target.value as RuleGroup["operator"] })} value={group.operator}><option value="all">All must pass</option><option value="any">Any may pass</option><option value="score">Required score</option></select></label>
          {group.operator === "score" ? <label><span>Required score <FieldHelp title="Required score" content={{ role: "Minimum fraction of this rule set's enabled conditions that must pass.", values: { "1.0": "Every condition must pass.", "0.75": "At least three quarters must pass.", "0.5": "At least half must pass." }, note: "This value belongs only to this rule set; changing it does not affect any other confirmation or phase." }} /></span><input max={1} min={0.01} onChange={(event) => onChange({ ...group, required_score: Number(event.target.value) })} step={0.05} type="number" value={group.required_score} /></label> : null}
          <div className="strategy-rule-toolbar-actions">
            <label className="strategy-rule-enabled"><span><strong>{group.enabled ? "Enabled" : "Disabled"}</strong><small>{group.enabled ? "Included in evaluation" : "Ignored by runtime"}</small></span><span className="configuration-switch"><input checked={group.enabled} onChange={(event) => onChange({ ...group, enabled: event.target.checked })} type="checkbox" /><span /></span></label>
            {removable ? <button aria-label={`Delete ${group.label}`} className="button compact danger" onClick={onRemove} type="button"><Trash2 size={14} /> Delete</button> : null}
          </div>
        </div>
      </header>
      <div className="strategy-rule-conditions">
        {group.conditions.map((condition, index) => (
          <RuleConditionEditor
            catalog={catalog}
            condition={condition}
            index={index}
            key={condition.condition_id}
            onChange={(next) => replaceCondition(condition.condition_id, next)}
            onRemove={() => onChange({ ...group, conditions: group.conditions.filter((row) => row.condition_id !== condition.condition_id) })}
            removable={group.conditions.length > 1}
          />
        ))}
      </div>
      <button className="configuration-inline-action" onClick={addCondition} type="button"><Plus size={13} /> Add condition to this rule set</button>
      </div>
    </details>
  );
}

export function RuleConditionEditor({ catalog, condition, index, onChange, onRemove, removable }: {
  catalog: StrategyInput[];
  condition: RuleCondition;
  index: number;
  onChange: (value: RuleCondition) => void;
  onRemove: () => void;
  removable: boolean;
}) {
  const left = inputSource(catalog, condition.left_source_id);
  const targetMode = condition.right_source_id ? "source" : "constant";
  const right = condition.right_source_id ? inputSource(catalog, condition.right_source_id) : null;
  const comparatorOptions = left?.filter_operators?.length
    ? COMPARATOR_OPTIONS.filter((row) => left.filter_operators?.includes(row.value))
    : left?.value_type === "boolean"
      ? COMPARATOR_OPTIONS.filter((row) => row.value === "is_true" || row.value === "equals")
      : COMPARATOR_OPTIONS.filter((row) => row.value !== "is_true");

  function selectLeft(sourceId: string) {
    const source = inputSource(catalog, sourceId) ?? catalog[0];
    const allowedComparators = source.filter_operators?.length
      ? source.filter_operators
      : COMPARATOR_OPTIONS.filter((row) => source.value_type === "boolean" ? ["is_true", "equals"].includes(row.value) : row.value !== "is_true").map((row) => row.value);
    const comparator = allowedComparators.includes(condition.comparator)
      ? condition.comparator
      : allowedComparators[0] ?? "equals";
    onChange({
      ...condition,
      comparator,
      left_source_id: source.source_id,
      left_timeframe: source.timeframes[0],
      value: comparator === "is_true" ? null : condition.value ?? 0,
    });
  }

  function selectTargetMode(mode: string) {
    if (mode === "source") {
      const source = catalog.find((row) => row.value_type !== "boolean") ?? catalog[0];
      onChange({ ...condition, right_source_id: source.source_id, right_timeframe: source.timeframes[0], value: condition.comparator === "above_by_bps" ? 0 : null });
    } else {
      onChange({ ...condition, right_source_id: "", right_timeframe: "", value: 0 });
    }
  }

  return (
    <div className="strategy-rule-condition">
      <span className="strategy-condition-index">{index + 1}</span>
      <label><span>Data source</span><select onChange={(event) => selectLeft(event.target.value)} value={condition.left_source_id}>{sourceOptions(catalog)}</select></label>
      <label><span>Timeframe</span><select onChange={(event) => onChange({ ...condition, left_timeframe: event.target.value })} value={condition.left_timeframe}>{left?.timeframes.map((timeframe) => <option key={timeframe}>{timeframe}</option>)}</select></label>
      <label><span>Comparison</span><select onChange={(event) => {
        const comparator = event.target.value;
        if (comparator === "above_by_bps" && !condition.right_source_id) {
          const source = catalog.find((row) => row.value_type === left?.value_type && row.source_id !== condition.left_source_id) ?? catalog[0];
          onChange({ ...condition, comparator, right_source_id: source.source_id, right_timeframe: source.timeframes[0], value: 0 });
        } else {
          onChange({ ...condition, comparator });
        }
      }} value={condition.comparator}>{comparatorOptions.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}</select></label>
      {condition.comparator !== "is_true" ? (
        <>
          <label><span>Compare with</span><select disabled={condition.comparator === "above_by_bps"} onChange={(event) => selectTargetMode(event.target.value)} value={targetMode}><option value="constant">Fixed value</option><option value="source">Another source</option></select></label>
          {targetMode === "source" ? (
            <>
              <label><span>Target source</span><select onChange={(event) => {
                const source = inputSource(catalog, event.target.value) ?? catalog[0];
                onChange({ ...condition, right_source_id: source.source_id, right_timeframe: source.timeframes[0] });
              }} value={condition.right_source_id}>{sourceOptions(catalog, left?.value_type)}</select></label>
              <label><span>Target timeframe</span><select onChange={(event) => onChange({ ...condition, right_timeframe: event.target.value })} value={condition.right_timeframe}>{right?.timeframes.map((timeframe) => <option key={timeframe}>{timeframe}</option>)}</select></label>
            </>
          ) : (
            <label><span>Threshold</span><input onChange={(event) => onChange({ ...condition, value: Number(event.target.value) })} step="any" type="number" value={Number(condition.value ?? 0)} /></label>
          )}
          {condition.comparator === "above_by_bps" && targetMode === "source" ? <label><span>Buffer (bps)</span><input min={0} onChange={(event) => onChange({ ...condition, value: Number(event.target.value) })} step={0.5} type="number" value={Number(condition.value ?? 0)} /></label> : null}
        </>
      ) : null}
      <button aria-label={`Delete condition ${index + 1}`} className="button compact danger" disabled={!removable} onClick={onRemove} type="button"><Trash2 size={13} /></button>
      <div className="strategy-source-detail">
        <strong>{left?.provider ?? "Unknown provider"}</strong>
        <span>{left?.category} · {left?.parameter} · runtime field {left?.runtime_field}</span>
        <p>{left?.summary}</p>
      </div>
    </div>
  );
}

export function RuleStageEditor({ catalog, intent, label, onChange, stage }: {
  catalog: StrategyInput[];
  intent: "add" | "entry" | "exit" | "reentry";
  label: string;
  onChange: (value: RuleStage) => void;
  stage: RuleStage;
}) {
  const [openedId, setOpenedId] = useState("");
  const groups = stage.groups ?? [];
  const context = {
    add: { eyebrow: "Add evidence", noun: "add action", text: "These rule sets are evaluated while a position is open before Strategy may request more exposure." },
    entry: { eyebrow: "Entry evidence", noun: "entry stage", text: "These rule sets are evaluated before Strategy may emit its configured initial-entry request." },
    exit: { eyebrow: "Exit evidence", noun: "exit route", text: "These rule sets are evaluated before Strategy may emit its configured exit request." },
    reentry: { eyebrow: "Reentry evidence", noun: "reentry stage", text: "These rule sets are evaluated after the campaign is flat before Strategy may emit a new reentry request." },
  }[intent];
  function addGroup() {
    const groupId = uniqueId("new-rule", groups.map((row) => row.group_id));
    const source = catalog[0];
    onChange({
      ...stage,
      groups: [{
        conditions: [{
          comparator: source.value_type === "boolean" ? "is_true" : "greater_or_equal",
          condition_id: `${groupId}-condition`,
          enabled: true,
          left_source_id: source.source_id,
          left_timeframe: source.timeframes[0],
          right_source_id: "",
          right_timeframe: "",
          value: source.value_type === "boolean" ? null : 0,
        }],
        enabled: true,
        group_id: groupId,
        label: "New rule set",
        operator: "all",
        required_score: 1,
      }, ...groups],
    });
    setOpenedId(groupId);
  }
  return (
    <section className="strategy-rule-stage compact" data-stage={intent}>
      <header>
        <div><span>{context.eyebrow}</span><strong>{label}</strong><p>{context.text}</p></div>
        <div className="strategy-stage-controls">
          <SelectField
            help={{ role: `Combines this ${context.noun}'s rule sets.`, values: { "Any rule set": `The ${context.noun} passes when at least one enabled rule set passes.`, "All rule sets": "Every enabled rule set must pass." } }}
            label="Rule-set logic"
            onChange={(operator) => onChange({ ...stage, operator: operator as "all" | "any" })}
            options={[{ label: "Any rule set", value: "any" }, { label: "All rule sets", value: "all" }]}
            value={stage.operator ?? "any"}
          />
          <button className="button compact" onClick={addGroup} type="button"><Plus size={14} /> Add rule set</button>
        </div>
      </header>
      <ConfigurationNarrative heading={label} paragraphs={[
        `${context.text} Rule-set logic selects whether any path or every path must pass; each path applies its own condition logic and required score.`,
      ]} />
      <div className="strategy-rule-groups">
        {groups.map((group) => (
          <RuleGroupEditor
            catalog={catalog}
            defaultOpen={group.group_id === openedId}
            group={group}
            key={group.group_id}
            onChange={(next) => onChange({ ...stage, groups: groups.map((row) => row.group_id === group.group_id ? next : row) })}
            onRemove={() => onChange({ ...stage, groups: groups.filter((row) => row.group_id !== group.group_id) })}
            removable={groups.length > 1}
          />
        ))}
      </div>
    </section>
  );
}

export function PhaseOrderEditor({ capitalRequest, eligibleSessions, executionPolicies, onCapitalRequest, onOrderIntent, orderIntent, protectionProfiles, title }: {
  capitalRequest: CapitalRequestConfig;
  eligibleSessions: string[];
  executionPolicies: ExecutionPolicyConfig[];
  onCapitalRequest: (value: CapitalRequestConfig) => void;
  onOrderIntent: (value: OrderIntentConfig) => void;
  orderIntent: OrderIntentConfig;
  protectionProfiles: ProtectionProfileConfig[];
  title: string;
}) {
  return (
    <section className="strategy-order-request">
      <header>
        <div><span>Portfolio + OMS handoff</span><strong>{title}</strong><p>The strategy describes intent. Portfolio resolves the approved account quantity, then OMS chooses session-safe broker instructions and manages the order.</p></div>
        <div className="strategy-handoff-flow" aria-label="Order handoff sequence"><span>Strategy request</span><ChevronRight size={14} /><span>Portfolio approval</span><ChevronRight size={14} /><span>OMS execution</span></div>
      </header>
      <ConfigurationNarrative heading={title} paragraphs={[
        "This request contains relative capital demand and broker-neutral execution preferences. Portfolio returns an approved account quantity or rejection. OMS may resolve broker mechanics for the approval but cannot increase its quantity or risk envelope.",
      ]} />
      <div className="strategy-handoff-grid">
        <CapitalRequestEditor onChange={onCapitalRequest} value={capitalRequest} />
        <OrderIntentEditor eligibleSessions={eligibleSessions} executionPolicies={executionPolicies} protectionProfiles={protectionProfiles} onChange={onOrderIntent} value={orderIntent} />
      </div>
    </section>
  );
}

export function CapitalRequestEditor({ onChange, value }: {
  onChange: (value: CapitalRequestConfig) => void;
  value: CapitalRequestConfig;
}) {
  const requestHelp = {
    fixed_quantity: { label: "Shares requested", unit: "shares", maximum: undefined },
    mandate_fraction: { label: "Mandate capacity", unit: "fraction", maximum: 1 },
    risk_fraction: { label: "Risk budget", unit: "fraction", maximum: 1 },
    all_available: { label: "", unit: "", maximum: undefined },
  }[value.mode];
  return (
    <article className="strategy-handoff-card strategy-capital-request">
      <header>
        <BriefcaseBusiness size={18} />
        <div><span>Step 1 · Portfolio</span><strong>Capital request</strong><p>Ask for capital in relative terms. Portfolio applies the Run Plan mandate, buying power, current positions, risk limits, and competing requests before approving shares.</p></div>
      </header>
      <ConfigurationNarrative heading="Capital request" paragraphs={[
        "Mode determines whether demand is expressed as shares, mandate capacity, planned-risk budget, or remaining mandate capacity. Value sets the amount in that unit. Replacement only permits Portfolio to evaluate displacement under the mandate threshold; it does not close another position directly.",
      ]} />
      <div className="configuration-field-grid">
      <SelectField
        help={{
          role: "Describes the strategy's relative capital request. Portfolio converts it into an account-specific quantity after applying mandates, current positions, buying power, and risk.",
          values: {
            "Fixed quantity": "Request an explicit number of shares. Portfolio may approve less.",
            "Mandate fraction": "Request a percentage of this strategy's approved cash capacity on the account.",
            "Risk fraction": "Request a percentage of the account mandate's planned-risk budget.",
            "All available": "Request all remaining capacity allowed by the account mandate; this is not all account cash.",
          },
          parameters: {
            "Request value": "Shares for fixed quantity, or a fraction for mandate and risk modes. All available has no independent value.",
            "Allow replacement": "Permits Portfolio to propose releasing a weaker position when the new request materially improves the account plan.",
          },
        }}
        label="Capital request"
        onChange={(mode) => onChange({ ...value, mode: mode as CapitalRequestConfig["mode"], value: mode === "all_available" ? 1 : mode === "fixed_quantity" ? 100 : 0.2 })}
        options={["fixed_quantity", "mandate_fraction", "risk_fraction", "all_available"].map((mode) => ({ label: readableLabel(mode), value: mode }))}
        value={value.mode}
      />
      {value.mode !== "all_available" ? (
        <NumberField
          help={{
            role: value.mode === "fixed_quantity" ? "The share quantity requested before Portfolio approval." : `The fraction of the ${value.mode === "mandate_fraction" ? "strategy-account cash mandate" : "planned-risk budget"} requested by this trigger.`,
            note: "This value is local to this entry, reentry, or add trigger. It is not a strategy-wide position ceiling.",
          }}
          label={requestHelp.label}
          maximum={requestHelp.maximum}
          minimum={value.mode === "fixed_quantity" ? 1 : 0.01}
          onChange={(requestValue) => onChange({ ...value, value: requestValue })}
          step={value.mode === "fixed_quantity" ? 1 : 0.05}
          unit={requestHelp.unit}
          value={value.value}
        />
      ) : (
        <div className="configuration-context-value"><span>Request value</span><strong>Portfolio resolves remaining mandate capacity</strong><small>No stale quantity field is retained.</small></div>
      )}
        <BooleanField help={{ role: "Allows Portfolio to propose funding this request by reducing or closing a weaker position.", parameters: { "Replacement threshold": "Configured on the Portfolio mandate and must show sufficient improvement before displacement is allowed." }, note: "The strategy grants permission; Portfolio decides whether replacement is safe and beneficial." }} label="Allow replacement" onChange={(allow_replacement) => onChange({ ...value, allow_replacement })} value={value.allow_replacement} />
      </div>
      <div className="strategy-handoff-result"><span>Portfolio output</span><strong>Approved quantity and account allocation</strong><small>The approved result may be smaller than requested or rejected with a reason.</small></div>
    </article>
  );
}

export function OrderIntentEditor({ eligibleSessions, executionPolicies, onChange, protectionProfiles, value }: {
  eligibleSessions: string[];
  executionPolicies: ExecutionPolicyConfig[];
  onChange: (value: OrderIntentConfig) => void;
  value: OrderIntentConfig;
  protectionProfiles: ProtectionProfileConfig[];
}) {
  const usesExtendedHours = eligibleSessions.some((session) => session === "premarket" || session === "after_hours");
  return (
    <article className="strategy-handoff-card strategy-order-intent">
      <header>
        <Send size={18} />
        <div><span>Step 2 · OMS</span><strong>Execution policy</strong><p>Choose urgency and fill behavior, not broker-specific flags. OMS converts this intent into the fastest compatible order for the selected sessions, account, venue, and broker.</p></div>
      </header>
      <ConfigurationNarrative heading="Execution intent" paragraphs={[
        "Execution policy controls urgency, repricing, and price boundaries. Protection attaches to actual fills. Partial-fill behavior controls whether OMS completes, accepts, or cancels the unfilled remainder. Session routing is derived from eligible sessions and broker capabilities.",
      ]} />
      <div className="configuration-field-grid">
      <SelectField
        help={{
          role: "Selects the broker-neutral execution policy sent to OMS after this trigger passes.",
          values: {
            Passive: "Prioritizes price improvement and avoids crossing. Use only when a missed or slow fill is acceptable.",
            Midpoint: "Starts near the spread midpoint. It can improve price but may miss a fast move or remain unfilled in a wide market.",
            "Adaptive patient": "Works slowly inside the permitted price envelope. Best for price quality when the opportunity can wait.",
            "Adaptive regular": "Balances fill probability and price improvement. This is the normal default for non-emergency orders.",
            "Adaptive urgent": "Reprices quickly toward executable liquidity. It improves fill probability but may pay more spread or slippage.",
            "Adaptive very urgent": "Uses the fastest bounded repricing for protection or time-critical exits. Expect the highest execution cost within the approved envelope.",
            "Immediate with limit": "Submits immediately with a hard price boundary. It can remain unfilled beyond that limit.",
            "IBKR native adaptive": "Uses IBKR's adaptive algorithm only when the broker and account support it; unsupported combinations must be rejected or safely mapped by OMS.",
            "Cancel if not filled": "Stops working the remainder at the deadline. Use where a partial or absent fill is safer than chasing an expiring opportunity.",
          },
          note: "The strategy never emits raw broker orders. OMS remains the only authority that creates, modifies, cancels, and reconciles orders.",
        }}
        label="Execution policy"
        onChange={(execution_policy) => onChange({ ...value, execution_policy })}
        options={executionPolicies.map((policy) => ({ label: `${readableLabel(policy.name)} · v${policy.revision}`, value: policy.policy_id }))}
        searchable={false}
        value={value.execution_policy}
      />
      <SelectField help="Selects the independently versioned stop, target, and trailing plan used for a filled entry or add." label="Protection profile" onChange={(protection_profile) => onChange({ ...value, protection_profile })} options={protectionProfiles.map((profile) => ({ label: `${profile.name} · v${profile.revision}`, value: profile.profile_id }))} value={value.protection_profile} />
      <SelectField help={{ role: "Determines how OMS handles an incomplete fill.", values: { "Complete remainder": "Continue working the unfilled quantity under the selected policy.", "Accept partial": "Keep the fill received and stop requesting the remainder.", "Cancel remainder": "Cancel any remainder after the first partial fill." } }} label="Partial fill" onChange={(partial_fill_policy) => onChange({ ...value, partial_fill_policy: partial_fill_policy as OrderIntentConfig["partial_fill_policy"] })} options={["complete_remainder", "accept_partial", "cancel_remainder"].map((item) => ({ label: readableLabel(item), value: item }))} value={value.partial_fill_policy} />
      </div>
      <div className="strategy-smart-session">
        <ShieldCheck size={17} />
        <div><span>Smart session routing</span><strong>{eligibleSessions.map(readableLabel).join(", ") || "No eligible session selected"}</strong><p>{usesExtendedHours ? "OMS enables eligible extended-session routing and selects compatible broker instructions after account, venue, and order-type checks." : "OMS keeps the request in the regular session and chooses compatible broker instructions automatically."}</p></div>
        <FieldHelp content={{ role: "Session routing is derived from Trading Behavior so entry, reentry, and exit requests cannot contradict the strategy's eligible sessions.", parameters: { "Eligible sessions": "Selected once in Trading Behavior.", "Time in force": "Chosen by OMS for the broker, venue, session, and execution method.", "Outside regular hours": "Enabled by OMS only when premarket or after-hours is selected and the broker path supports it." }, note: "Change session eligibility in Trading Behavior. Strategy phases intentionally do not expose raw time-in-force or outside-hours switches." }} />
      </div>
    </article>
  );
}

export function AddStepsEditor({ catalog, eligibleSessions, executionPolicies, onChange, onRuleSetEdit = () => undefined, protectionProfiles, ruleSets = [], steps }: {
  catalog: StrategyInput[];
  eligibleSessions: string[];
  executionPolicies: ExecutionPolicyConfig[];
  onChange: (value: AddStep[]) => void;
  onRuleSetEdit?: (ruleSetId: string) => void;
  protectionProfiles: ProtectionProfileConfig[];
  ruleSets?: RuleSetDefinition[];
  steps: AddStep[];
}) {
  function addStep() {
    const stepId = uniqueId("position-add", steps.map((row) => row.step_id));
    const evidenceRuleSet = ruleSets[0];
    onChange([{
      action_id: "position.add_long",
      capital_request: { allow_replacement: false, mode: "mandate_fraction", value: 0.1 },
      enabled: true,
      maximum_uses: 1,
      name: "New position add",
      order_intent: { deadline_ms: 750, execution_policy: "adaptive_urgent", partial_fill_policy: "complete_remainder", protection_profile: "hybrid-single" },
      rules: { expression: { children: evidenceRuleSet ? [{ kind: "rule_set", rule_set_id: evidenceRuleSet.rule_set_id }] : [], kind: "operator", operator: "and" } },
      step_id: stepId,
    }, ...steps]);
  }
  return (
    <section className="strategy-add-plan">
      <header><div><span>Position construction</span><strong>Conditional add requests</strong><p>Each add owns its evidence, relative capital request, order policy, and usage limit. Newly added steps appear first.</p></div><button className="button compact" onClick={addStep} type="button"><Plus size={14} /> Add position step</button></header>
      <ConfigurationNarrative heading="Position construction" paragraphs={[
        "Each add step defines evidence, maximum successful uses, capital demand, execution, and protection for increasing an open position. Every passing step creates a new request; Portfolio re-evaluates current account state, so initial-entry approval does not guarantee add approval.",
      ]} />
      <div>
        {steps.map((step) => (
          <details className="strategy-add-step" key={step.step_id}>
            <summary><span className="strategy-rule-state" /><div><strong>{step.name}</strong><small>{readableLabel(step.capital_request.mode)} · {step.maximum_uses} maximum uses</small></div><ChevronDown size={16} /></summary>
            <div className="strategy-add-step-body">
              <ConfigurationNarrative heading={step.name} paragraphs={[
                "Enabled determines whether this step participates. Maximum uses counts successful executions in one campaign. Passing evidence sends a new request through Run Plan authority, Portfolio sizing, and OMS execution; rejected requests do not consume a successful use.",
              ]} />
              <div className="configuration-field-grid">
                <TextField help="Operator-facing name for this ordered position-building step." label="Step name" onChange={(name) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, name } : row))} value={step.name} />
                <NumberField help="Maximum successful executions of this add step during one campaign." label="Maximum uses" minimum={1} onChange={(maximum_uses) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, maximum_uses } : row))} step={1} unit="fills" value={step.maximum_uses} />
                <BooleanField help="Disabled steps remain configured but cannot emit a capital request." label="Enabled" onChange={(enabled) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, enabled } : row))} value={step.enabled} />
                <button className="button compact danger" onClick={() => onChange(steps.filter((row) => row.step_id !== step.step_id))} type="button"><Trash2 size={14} /> Remove step</button>
              </div>
              <RuleStageComposition catalog={catalog} label={`${step.name} rules`} onChange={(rules) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, rules } : row))} onEditRuleSet={onRuleSetEdit} ruleSets={ruleSets} stage={step.rules} />
              <PhaseOrderEditor capitalRequest={step.capital_request} eligibleSessions={eligibleSessions} executionPolicies={executionPolicies} protectionProfiles={protectionProfiles} orderIntent={step.order_intent} title={`${step.name} request`} onCapitalRequest={(capital_request) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, capital_request } : row))} onOrderIntent={(order_intent) => onChange(steps.map((row) => row.step_id === step.step_id ? { ...row, order_intent } : row))} />
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}


function inputSource(catalog: StrategyInput[], sourceId: string) {
  return catalog.find((row) => row.source_id === sourceId);
}

function sourceOptions(catalog: StrategyInput[], valueType?: string) {
  const categories = [...new Set(catalog.map((row) => row.category))];
  return categories.map((category) => (
    <optgroup key={category} label={category}>
      {catalog.filter((row) => row.category === category && (!valueType || row.value_type === valueType)).map((source) => (
        <option key={source.source_id} value={source.source_id}>{source.label} · {source.parameter}</option>
      ))}
    </optgroup>
  ));
}
