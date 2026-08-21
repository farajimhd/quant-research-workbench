import { Filter, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ChangeEvent, type CSSProperties } from "react";
import { createPortal } from "react-dom";

export type TableFilterKind = "boolean" | "category" | "datetime" | "number" | "text";
export type TableFilterMatchMode = "all" | "any";
export type TableFilterOperator = "between" | "contains" | "eq" | "gt" | "gte" | "is_null" | "lt" | "lte" | "neq" | "not_null";

export type TableFilterColumn = {
  description?: string;
  key: string;
  kind: TableFilterKind;
  label: string;
  temporalUnit?: "date" | "datetime";
};

export type TableFilterCondition = {
  column: string;
  id: string;
  operator: TableFilterOperator;
  value: string;
  valueSecondary: string;
};

let tableFilterSequence = 0;
type FilterPopoverPlacement = CSSProperties & { maxHeight: number; width: number };

export function TableColumnFilterControl({ columns, conditions, matchMode, onChange, onMatchModeChange, open, onOpenChange, rows, title }: {
  columns: TableFilterColumn[];
  conditions: TableFilterCondition[];
  matchMode: TableFilterMatchMode;
  onChange: (conditions: TableFilterCondition[]) => void;
  onMatchModeChange: (mode: TableFilterMatchMode) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rows: Array<Record<string, unknown>>;
  title: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLElement>(null);
  const [placement, setPlacement] = useState<FilterPopoverPlacement>({ left: 8, maxHeight: 460, top: 48, width: 680 });
  const activeConditions = validTableFilters(conditions);
  const columnByKey = useMemo(() => new Map(columns.map((column) => [column.key, column])), [columns]);

  const placePopover = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const rootStyle = getComputedStyle(document.documentElement);
    const scale = Number.parseFloat(rootStyle.getPropertyValue("--app-overlay-scale")) || 1;
    const margin = 8;
    const width = Math.min(window.innerWidth - margin * 2, 680 * scale);
    const desiredHeight = Math.min(popoverRef.current?.scrollHeight || 460 * scale, 460 * scale);
    const availableBelow = window.innerHeight - rect.bottom - margin;
    const availableAbove = rect.top - margin;
    const openAbove = availableBelow < Math.min(desiredHeight, 220 * scale) && availableAbove > availableBelow;
    const maxHeight = Math.max(180 * scale, Math.min(desiredHeight, openAbove ? availableAbove : availableBelow));
    const renderedHeight = Math.min(desiredHeight, maxHeight);
    const preferredLeft = rect.left + width <= window.innerWidth - margin ? rect.left : rect.right - width;
    setPlacement({
      left: Math.max(margin, Math.min(preferredLeft, window.innerWidth - width - margin)),
      maxHeight,
      top: openAbove ? Math.max(margin, rect.top - renderedHeight - 5) : rect.bottom + 5,
      width,
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    placePopover();
    const frame = window.requestAnimationFrame(placePopover);
    window.addEventListener("resize", placePopover);
    window.addEventListener("scroll", placePopover, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", placePopover);
      window.removeEventListener("scroll", placePopover, true);
    };
  }, [open, placePopover]);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!rootRef.current?.contains(target) && !popoverRef.current?.contains(target)) onOpenChange(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("pointerdown", dismiss, true);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss, true);
      document.removeEventListener("keydown", escape);
    };
  }, [onOpenChange, open]);

  function addCondition() {
    const column = columns.find((candidate) => !conditions.some((condition) => condition.column === candidate.key)) ?? columns[0];
    if (!column) return;
    onChange([...conditions, defaultTableFilter(column)]);
  }

  function replaceCondition(id: string, patch: Partial<TableFilterCondition>) {
    onChange(conditions.map((condition) => condition.id === id ? { ...condition, ...patch } : condition));
  }

  function selectColumn(condition: TableFilterCondition, columnKey: string) {
    const column = columnByKey.get(columnKey);
    if (!column) return;
    replaceCondition(condition.id, {
      column: column.key,
      operator: defaultOperator(column.kind),
      value: "",
      valueSecondary: "",
    });
  }

  return <div className="market-table-filter-control" ref={rootRef}>
    <button aria-expanded={open} aria-haspopup="dialog" className="market-table-filter-trigger" data-active={activeConditions.length ? "true" : undefined} onClick={() => onOpenChange(!open)} ref={triggerRef} type="button">
      <Filter aria-hidden="true" size={13} />
      <span>Filters</span>
      {activeConditions.length ? <b>{activeConditions.length}</b> : null}
    </button>
    {open ? createPortal(<section aria-label={`Filter ${title}`} className="market-table-filter-popover" ref={popoverRef} role="dialog" style={placement}>
      <header>
        <div><strong>Filter rows</strong><span>Build conditions from the columns visible in this table.</span></div>
        <button aria-label="Close filters" onClick={() => onOpenChange(false)} type="button"><X size={14} /></button>
      </header>
      {conditions.length > 1 ? <div className="market-table-filter-match"><span>Match</span><div role="group" aria-label="Filter matching mode"><button aria-pressed={matchMode === "all"} onClick={() => onMatchModeChange("all")} type="button">All</button><button aria-pressed={matchMode === "any"} onClick={() => onMatchModeChange("any")} type="button">Any</button></div><small>{matchMode === "all" ? "Every condition must match" : "At least one condition must match"}</small></div> : null}
      <div className="market-table-filter-list">
        {conditions.length ? conditions.map((condition, index) => {
          const column = columnByKey.get(condition.column) ?? columns[0];
          if (!column) return null;
          const operators = operatorsForKind(column.kind);
          const needsValue = operatorNeedsValue(condition.operator);
          const suggestions = filterValueSuggestions(rows, condition.column);
          return <div className="market-table-filter-row" key={condition.id}>
            <span className="market-table-filter-index">{index + 1}</span>
            <label><span>Column</span><select aria-label={`Filter ${index + 1} column`} onChange={(event) => selectColumn(condition, event.target.value)} value={condition.column}>{columns.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}</select></label>
            <label><span>Condition</span><select aria-label={`Filter ${index + 1} condition`} onChange={(event) => replaceCondition(condition.id, { operator: event.target.value as TableFilterOperator, valueSecondary: "" })} value={condition.operator}>{operators.map((operator) => <option key={operator} value={operator}>{operatorLabel(operator)}</option>)}</select></label>
            {needsValue ? <FilterValueEditor column={column} condition={condition} index={index} onChange={(patch) => replaceCondition(condition.id, patch)} suggestions={suggestions} /> : <div className="market-table-filter-value-summary"><span>Value</span><strong>{condition.operator === "is_null" ? "Blank" : "Present"}</strong></div>}
            <button aria-label={`Remove filter ${index + 1}`} className="market-table-filter-remove" onClick={() => onChange(conditions.filter((item) => item.id !== condition.id))} title="Remove filter" type="button"><Trash2 size={13} /></button>
          </div>;
        }) : <div className="market-table-filter-empty"><Filter aria-hidden="true" size={18} /><strong>No column filters</strong><span>Add a condition to narrow the current table without changing Market Discovery.</span></div>}
      </div>
      <footer><button className="market-table-filter-add" disabled={!columns.length} onClick={addCondition} type="button"><Plus size={13} /> Add condition</button>{conditions.length ? <button onClick={() => onChange([])} type="button">Clear all</button> : null}<span>{activeConditions.length ? `${activeConditions.length} active` : "Changes apply immediately"}</span></footer>
    </section>, document.body) : null}
  </div>;
}

export function TableActiveFilterBar({ columns, conditions, onChange }: { columns: TableFilterColumn[]; conditions: TableFilterCondition[]; onChange: (conditions: TableFilterCondition[]) => void }) {
  const active = validTableFilters(conditions);
  if (!active.length) return null;
  const columnByKey = new Map(columns.map((column) => [column.key, column]));
  return <div aria-label="Active column filters" className="market-table-active-filters"><span>Filters</span><div>{active.map((condition) => <span className="market-table-filter-chip" key={condition.id}><strong>{columnByKey.get(condition.column)?.label ?? condition.column}</strong><em>{filterSummary(condition)}</em><button aria-label={`Remove ${columnByKey.get(condition.column)?.label ?? condition.column} filter`} onClick={() => onChange(conditions.filter((item) => item.id !== condition.id))} type="button"><X size={11} /></button></span>)}</div><button onClick={() => onChange([])} type="button">Clear all</button></div>;
}

export function filterRowsByConditions<T extends Record<string, unknown>>(rows: T[], conditions: TableFilterCondition[], columns: TableFilterColumn[], matchMode: TableFilterMatchMode): T[] {
  const active = validTableFilters(conditions);
  if (!active.length) return rows;
  const columnByKey = new Map(columns.map((column) => [column.key, column]));
  return rows.filter((row) => {
    const matches = active.map((condition) => matchesTableFilter(row[condition.column], condition, columnByKey.get(condition.column)?.kind ?? "text"));
    return matchMode === "any" ? matches.some(Boolean) : matches.every(Boolean);
  });
}

export function validTableFilters(conditions: TableFilterCondition[]): TableFilterCondition[] {
  return conditions.filter((condition) => !operatorNeedsValue(condition.operator) || (condition.value.trim() && (condition.operator !== "between" || condition.valueSecondary.trim())));
}

function FilterValueEditor({ column, condition, index, onChange, suggestions }: { column: TableFilterColumn; condition: TableFilterCondition; index: number; onChange: (patch: Partial<TableFilterCondition>) => void; suggestions: string[] }) {
  const inputType = column.kind === "number" ? "number" : column.kind === "datetime" ? column.temporalUnit === "date" ? "date" : "datetime-local" : "text";
  const listId = `market-filter-values-${condition.id}`;
  const changeValue = (event: ChangeEvent<HTMLInputElement>) => onChange({ value: event.target.value });
  return <label className="market-table-filter-value"><span>Value</span><div><input aria-label={`Filter ${index + 1} value`} list={column.kind === "category" || column.kind === "boolean" || column.kind === "text" ? listId : undefined} onChange={changeValue} placeholder={valuePlaceholder(column.kind)} step={column.kind === "number" ? "any" : undefined} type={inputType} value={condition.value} />{condition.operator === "between" ? <><span>to</span><input aria-label={`Filter ${index + 1} upper value`} onChange={(event) => onChange({ valueSecondary: event.target.value })} step={column.kind === "number" ? "any" : undefined} type={inputType} value={condition.valueSecondary} /></> : null}</div>{suggestions.length && ["category", "boolean", "text"].includes(column.kind) ? <datalist id={listId}>{suggestions.map((value) => <option key={value} value={value} />)}</datalist> : null}</label>;
}

function defaultTableFilter(column: TableFilterColumn): TableFilterCondition {
  tableFilterSequence += 1;
  return { column: column.key, id: `table-filter-${Date.now()}-${tableFilterSequence}`, operator: defaultOperator(column.kind), value: "", valueSecondary: "" };
}

function defaultOperator(kind: TableFilterKind): TableFilterOperator {
  if (kind === "number" || kind === "datetime") return "gte";
  if (kind === "category" || kind === "boolean") return "eq";
  return "contains";
}

function operatorsForKind(kind: TableFilterKind): TableFilterOperator[] {
  if (kind === "number" || kind === "datetime") return ["gte", "lte", "gt", "lt", "between", "eq", "neq", "is_null", "not_null"];
  if (kind === "category" || kind === "boolean") return ["eq", "neq", "contains", "is_null", "not_null"];
  return ["contains", "eq", "neq", "is_null", "not_null"];
}

function operatorNeedsValue(operator: TableFilterOperator) {
  return operator !== "is_null" && operator !== "not_null";
}

function matchesTableFilter(value: unknown, condition: TableFilterCondition, kind: TableFilterKind) {
  if (condition.operator === "is_null") return isBlank(value);
  if (condition.operator === "not_null") return !isBlank(value);
  if (isBlank(value)) return false;
  if (kind === "number") return compareValues(Number(value), Number(condition.value), Number(condition.valueSecondary), condition.operator);
  if (kind === "datetime") return compareValues(Date.parse(String(value)), Date.parse(condition.value), Date.parse(condition.valueSecondary), condition.operator);
  const source = String(value).toLocaleLowerCase();
  const target = condition.value.trim().toLocaleLowerCase();
  if (condition.operator === "contains") return source.includes(target);
  if (condition.operator === "eq") return source === target;
  if (condition.operator === "neq") return source !== target;
  return false;
}

function compareValues(value: number, target: number, secondary: number, operator: TableFilterOperator) {
  if (!Number.isFinite(value)) return false;
  if (operator === "between") return Number.isFinite(target) && Number.isFinite(secondary) && value >= Math.min(target, secondary) && value <= Math.max(target, secondary);
  if (!Number.isFinite(target)) return false;
  if (operator === "gte") return value >= target;
  if (operator === "lte") return value <= target;
  if (operator === "gt") return value > target;
  if (operator === "lt") return value < target;
  if (operator === "eq") return value === target;
  if (operator === "neq") return value !== target;
  return false;
}

function isBlank(value: unknown) {
  return value === null || value === undefined || String(value).trim() === "";
}

function filterValueSuggestions(rows: Array<Record<string, unknown>>, column: string) {
  const counts = new Map<string, number>();
  rows.forEach((row) => {
    const value = row[column];
    if (isBlank(value)) return;
    const normalized = String(value).trim();
    counts.set(normalized, (counts.get(normalized) ?? 0) + 1);
  });
  return [...counts.entries()].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], undefined, { numeric: true })).slice(0, 40).map(([value]) => value);
}

function operatorLabel(operator: TableFilterOperator) {
  return ({ between: "Between", contains: "Contains", eq: "Equals", gt: "Greater than", gte: "At least", is_null: "Is blank", lt: "Less than", lte: "At most", neq: "Does not equal", not_null: "Has a value" } satisfies Record<TableFilterOperator, string>)[operator];
}

function filterSummary(condition: TableFilterCondition) {
  if (condition.operator === "is_null" || condition.operator === "not_null") return operatorLabel(condition.operator);
  if (condition.operator === "between") return `${operatorLabel(condition.operator)} ${condition.value} and ${condition.valueSecondary}`;
  return `${operatorLabel(condition.operator)} ${condition.value}`;
}

function valuePlaceholder(kind: TableFilterKind) {
  if (kind === "number") return "Enter number";
  if (kind === "datetime") return "Select date or time";
  return "Enter or choose value";
}
