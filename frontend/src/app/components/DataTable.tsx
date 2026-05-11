import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  BarChart3,
  Columns3,
  EyeOff,
  Filter,
  Search,
} from "lucide-react";
import { useMemo, useState } from "react";

import { displayName, formatCell } from "../format";

type DataRow = Record<string, unknown>;
type SortDirection = "asc" | "desc";
type SortState = { column: string; direction: SortDirection } | null;
type ColumnKind = "numeric" | "datetime" | "categorical" | "boolean" | "text";

type ColumnProfile = {
  average?: number;
  distinct: number;
  kind: ColumnKind;
  max?: number | string;
  min?: number | string;
  nonEmpty: number;
  stddev?: number;
  topValues: Array<{ count: number; value: string }>;
  total?: number;
};

type DataTableProps = {
  columns?: string[];
  empty?: string;
  rows: DataRow[];
  title?: string;
};

export function DataTable({ columns, empty = "No rows.", rows, title }: DataTableProps) {
  const resolvedColumns = useMemo(() => {
    if (columns?.length) return columns;
    return Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
  }, [columns, rows]);

  const [activeValueFiltersByColumn, setActiveValueFiltersByColumn] = useState<Record<string, string[]>>({});
  const [columnsMenuOpen, setColumnsMenuOpen] = useState(false);
  const [filterColumn, setFilterColumn] = useState<string | null>(null);
  const [hiddenColumns, setHiddenColumns] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortState>(null);
  const [statsColumn, setStatsColumn] = useState<string | null>(null);

  const profilesByColumn = useMemo(() => {
    return Object.fromEntries(resolvedColumns.map((column) => [column, buildColumnProfile(rows, column)]));
  }, [resolvedColumns, rows]);

  const visibleColumns = resolvedColumns.filter((column) => !hiddenColumns.includes(column));
  const usableColumns = visibleColumns.length ? visibleColumns : resolvedColumns.slice(0, 1);
  const effectiveSort = sort ?? (resolvedColumns[0] ? { column: resolvedColumns[0], direction: "asc" as const } : null);
  const activeFilterCount = Object.values(activeValueFiltersByColumn).reduce((count, values) => count + values.length, 0);

  const filteredRows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (query) {
        const matchesQuery = resolvedColumns.some((column) =>
          formatCell(column, row[column]).toLowerCase().includes(query),
        );
        if (!matchesQuery) return false;
      }

      return Object.entries(activeValueFiltersByColumn).every(([column, selectedValues]) => {
        if (!selectedValues.length) return true;
        return selectedValues.includes(formatFilterValue(row[column]));
      });
    });
  }, [activeValueFiltersByColumn, resolvedColumns, rows, search]);

  const sortedRows = useMemo(() => {
    if (!effectiveSort) return filteredRows;
    const directionMultiplier = effectiveSort.direction === "asc" ? 1 : -1;
    return [...filteredRows].sort((left, right) => {
      const result = compareCells(left[effectiveSort.column], right[effectiveSort.column]);
      return result * directionMultiplier;
    });
  }, [effectiveSort, filteredRows]);

  const numericColumnCount = resolvedColumns.filter((column) => profilesByColumn[column]?.kind === "numeric").length;
  const activeSortLabel = effectiveSort ? `${displayName(effectiveSort.column)} ${effectiveSort.direction}` : "None";

  const toggleSort = (column: string) => {
    setSort((current) => {
      if (!current || current.column !== column) return { column, direction: "asc" };
      if (current.direction === "asc") return { column, direction: "desc" };
      return null;
    });
  };

  const toggleValueFilter = (column: string, value: string) => {
    setActiveValueFiltersByColumn((current) => {
      const selected = new Set(current[column] ?? []);
      if (selected.has(value)) selected.delete(value);
      else selected.add(value);

      const next = { ...current };
      if (selected.size) next[column] = Array.from(selected);
      else delete next[column];
      return next;
    });
  };

  const toggleColumnVisibility = (column: string) => {
    setHiddenColumns((current) => {
      if (current.includes(column)) return current.filter((item) => item !== column);
      return [...current, column];
    });
  };

  const resetTable = () => {
    setActiveValueFiltersByColumn({});
    setColumnsMenuOpen(false);
    setFilterColumn(null);
    setHiddenColumns([]);
    setSearch("");
    setSort(null);
    setStatsColumn(null);
  };

  return (
    <div className="data-table-shell">
      <div className="data-table-toolbar">
        <div className="data-table-toolbar-left">
          {title ? <div className="data-table-title">{title}</div> : null}
          <label className="data-table-search" aria-label="Search table">
            <Search size={14} />
            <input
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search"
              type="search"
              value={search}
            />
          </label>
          <div className="data-table-stat-strip" aria-label="Table stats">
            <span>{formatInteger(sortedRows.length)} rows</span>
            <span>{formatInteger(usableColumns.length)} cols</span>
            <span>{formatInteger(numericColumnCount)} numeric</span>
            <span>{formatInteger(activeFilterCount)} filters</span>
          </div>
        </div>
        <div className="data-table-toolbar-actions">
          <span className="data-table-sort-chip">Sort: {activeSortLabel}</span>
          <div className="data-table-action-menu">
            <button
              className="table-icon-button"
              onClick={() => setColumnsMenuOpen((current) => !current)}
              title="Columns"
              type="button"
            >
              <Columns3 size={15} />
            </button>
            {columnsMenuOpen ? (
              <div className="data-table-popover data-table-columns-popover">
                <div className="data-table-popover-title">Columns</div>
                {resolvedColumns.map((column) => (
                  <label className="data-table-check-row" key={column}>
                    <input
                      checked={!hiddenColumns.includes(column)}
                      onChange={() => toggleColumnVisibility(column)}
                      type="checkbox"
                    />
                    <span>{displayName(column)}</span>
                  </label>
                ))}
              </div>
            ) : null}
          </div>
          <button className="table-text-button" onClick={resetTable} type="button">
            Reset
          </button>
        </div>
      </div>

      <div className="data-table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              {usableColumns.map((column) => {
                const profile = profilesByColumn[column];
                const isSorted = effectiveSort?.column === column;
                const sortIcon = isSorted ? (
                  effectiveSort.direction === "asc" ? (
                    <ArrowUp size={13} />
                  ) : (
                    <ArrowDown size={13} />
                  )
                ) : (
                  <ArrowUpDown size={13} />
                );
                const filterActive = Boolean(activeValueFiltersByColumn[column]?.length);

                return (
                  <th key={column}>
                    <div className="data-table-header-cell">
                      <button className="data-table-header-sort" onClick={() => toggleSort(column)} type="button">
                        <span>{displayName(column)}</span>
                        {sortIcon}
                      </button>
                      <div className="data-table-header-actions">
                        <button
                          className={filterActive ? "table-icon-button active" : "table-icon-button"}
                          onClick={() => {
                            setFilterColumn(filterColumn === column ? null : column);
                            setStatsColumn(null);
                          }}
                          title={`Filter ${displayName(column)}`}
                          type="button"
                        >
                          <Filter size={13} />
                        </button>
                        <button
                          className="table-icon-button"
                          onClick={() => {
                            setStatsColumn(statsColumn === column ? null : column);
                            setFilterColumn(null);
                          }}
                          title={`Stats for ${displayName(column)}`}
                          type="button"
                        >
                          <BarChart3 size={13} />
                        </button>
                      </div>
                      {filterColumn === column ? (
                        <ColumnFilterPopover
                          column={column}
                          onHideColumn={() => {
                            toggleColumnVisibility(column);
                            setFilterColumn(null);
                          }}
                          onToggleValue={toggleValueFilter}
                          profile={profile}
                          selectedValues={activeValueFiltersByColumn[column] ?? []}
                        />
                      ) : null}
                      {statsColumn === column ? <ColumnStatsPopover profile={profile} /> : null}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sortedRows.length ? (
              sortedRows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {usableColumns.map((column) => (
                    <td className={cellClassName(row[column], column)} key={column}>
                      {formatCell(column, row[column])}
                    </td>
                  ))}
                </tr>
              ))
            ) : (
              <tr>
                <td className="data-table-empty" colSpan={Math.max(usableColumns.length, 1)}>
                  {empty}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ColumnFilterPopover({
  column,
  onHideColumn,
  onToggleValue,
  profile,
  selectedValues,
}: {
  column: string;
  onHideColumn: () => void;
  onToggleValue: (column: string, value: string) => void;
  profile: ColumnProfile;
  selectedValues: string[];
}) {
  const values = profile.topValues.length ? profile.topValues : [{ count: profile.nonEmpty, value: "(all)" }];
  return (
    <div className="data-table-popover data-table-filter-popover">
      <div className="data-table-popover-title">Filter values</div>
      <div className="data-table-filter-list">
        {values.map((item) => {
          const selected = selectedValues.includes(item.value);
          return (
            <label className="data-table-check-row" key={item.value}>
              <input checked={selected} onChange={() => onToggleValue(column, item.value)} type="checkbox" />
              <span>{item.value}</span>
              <small>{formatInteger(item.count)}</small>
            </label>
          );
        })}
      </div>
      <button className="table-text-button danger" onClick={onHideColumn} type="button">
        <EyeOff size={13} />
        Hide column
      </button>
    </div>
  );
}

function ColumnStatsPopover({ profile }: { profile: ColumnProfile }) {
  return (
    <div className="data-table-popover data-table-stats-popover">
      <div className="data-table-popover-title">Column stats</div>
      <dl>
        <div>
          <dt>Type</dt>
          <dd>{profile.kind}</dd>
        </div>
        <div>
          <dt>Non-empty</dt>
          <dd>{formatInteger(profile.nonEmpty)}</dd>
        </div>
        <div>
          <dt>Distinct</dt>
          <dd>{formatInteger(profile.distinct)}</dd>
        </div>
        {profile.min !== undefined ? (
          <div>
            <dt>Min</dt>
            <dd>{formatStat(profile.min)}</dd>
          </div>
        ) : null}
        {profile.max !== undefined ? (
          <div>
            <dt>Max</dt>
            <dd>{formatStat(profile.max)}</dd>
          </div>
        ) : null}
        {profile.average !== undefined ? (
          <div>
            <dt>Avg</dt>
            <dd>{formatNumber(profile.average)}</dd>
          </div>
        ) : null}
        {profile.total !== undefined ? (
          <div>
            <dt>Total</dt>
            <dd>{formatNumber(profile.total)}</dd>
          </div>
        ) : null}
        {profile.stddev !== undefined ? (
          <div>
            <dt>Std</dt>
            <dd>{formatNumber(profile.stddev)}</dd>
          </div>
        ) : null}
      </dl>
      {profile.topValues.length ? (
        <div className="data-table-top-values">
          {profile.topValues.slice(0, 6).map((item) => (
            <span key={item.value}>
              {item.value} <b>{formatInteger(item.count)}</b>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function buildColumnProfile(rows: DataRow[], column: string): ColumnProfile {
  const values = rows.map((row) => row[column]).filter((value) => !isBlank(value));
  const topValues = countTopValues(values);
  const distinct = topValues.length ? new Set(values.map(formatFilterValue)).size : 0;
  const numericValues = values.map(coerceNumber).filter((value): value is number => Number.isFinite(value));
  const booleanValues = values.filter((value) => typeof value === "boolean");
  const dateValues = values.map(coerceDate).filter((value): value is number => Number.isFinite(value));

  if (values.length && numericValues.length === values.length) {
    const total = numericValues.reduce((sum, value) => sum + value, 0);
    const average = total / numericValues.length;
    const variance =
      numericValues.reduce((sum, value) => sum + Math.pow(value - average, 2), 0) / Math.max(numericValues.length, 1);
    return {
      average,
      distinct,
      kind: "numeric",
      max: Math.max(...numericValues),
      min: Math.min(...numericValues),
      nonEmpty: values.length,
      stddev: Math.sqrt(variance),
      topValues,
      total,
    };
  }

  if (values.length && booleanValues.length === values.length) {
    return { distinct, kind: "boolean", nonEmpty: values.length, topValues };
  }

  if (values.length && dateValues.length === values.length && looksLikeTimeColumn(column)) {
    return {
      distinct,
      kind: "datetime",
      max: new Date(Math.max(...dateValues)).toISOString(),
      min: new Date(Math.min(...dateValues)).toISOString(),
      nonEmpty: values.length,
      topValues,
    };
  }

  return {
    distinct,
    kind: distinct <= Math.max(20, rows.length * 0.15) ? "categorical" : "text",
    nonEmpty: values.length,
    topValues,
  };
}

function countTopValues(values: unknown[]) {
  const counts = new Map<string, number>();
  values.forEach((value) => {
    const formatted = formatFilterValue(value);
    counts.set(formatted, (counts.get(formatted) ?? 0) + 1);
  });
  return Array.from(counts.entries())
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, 30)
    .map(([value, count]) => ({ count, value }));
}

function compareCells(left: unknown, right: unknown) {
  const leftComparable = comparableValue(left);
  const rightComparable = comparableValue(right);
  if (leftComparable === rightComparable) return 0;
  if (leftComparable === null) return 1;
  if (rightComparable === null) return -1;
  if (typeof leftComparable === "number" && typeof rightComparable === "number") return leftComparable - rightComparable;
  return String(leftComparable).localeCompare(String(rightComparable), undefined, { numeric: true, sensitivity: "base" });
}

function comparableValue(value: unknown): number | string | null {
  if (isBlank(value)) return null;
  const numeric = coerceNumber(value);
  if (Number.isFinite(numeric)) return numeric;
  const date = coerceDate(value);
  if (Number.isFinite(date)) return date;
  return String(value);
}

function coerceDate(value: unknown) {
  if (value instanceof Date) return value.getTime();
  if (typeof value !== "string") return Number.NaN;
  if (!/\d{4}-\d{2}-\d{2}/.test(value)) return Number.NaN;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

function coerceNumber(value: unknown) {
  if (typeof value === "number") return value;
  if (typeof value !== "string") return Number.NaN;
  const normalized = value.replace(/,/g, "").trim();
  if (!normalized) return Number.NaN;
  return Number(normalized);
}

function isBlank(value: unknown) {
  return value === null || value === undefined || value === "";
}

function looksLikeTimeColumn(column: string) {
  const normalized = column.toLowerCase();
  return normalized.includes("date") || normalized.includes("time") || normalized.endsWith("_at");
}

function formatFilterValue(value: unknown) {
  if (isBlank(value)) return "(blank)";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function formatInteger(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 }).format(value);
}

function formatStat(value: number | string) {
  if (typeof value === "number") return formatNumber(value);
  return value;
}

function cellClassName(value: unknown, column: string) {
  const normalized = column.toLowerCase();
  const numeric = coerceNumber(value);
  if (!Number.isFinite(numeric)) return "data-table-cell";
  if (normalized.includes("pnl") || normalized.includes("return") || normalized.includes("change") || normalized.includes("pct")) {
    if (numeric > 0) return "data-table-cell positive";
    if (numeric < 0) return "data-table-cell negative";
  }
  return "data-table-cell";
}
