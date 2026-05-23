import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  BarChart3,
  Check,
  Columns3,
  Database,
  EyeOff,
  Filter,
  GripVertical,
  MoreHorizontal,
  Plus,
  RotateCcw,
  Rows3,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { type DragEvent, type MouseEvent, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { displayName, formatCell } from "../format";
import { Modal } from "./Modal";

type DataRow = Record<string, unknown>;
export type SortDirection = "asc" | "desc";
type SortState = { column: string; direction: SortDirection } | null;
type ColumnKind = "numeric" | "datetime" | "categorical" | "boolean" | "text";
type TableDensityMode = "comfortable" | "compact" | "wide";
type TableLayoutMode = "fit_header" | "fit_data";
type TableVisualTone = "amber" | "emerald" | "neutral" | "sky" | "violet";
export type BackendQueryMatchMode = "all" | "any";
export type BackendQueryOperator =
  | "between"
  | "contains"
  | "ends_with"
  | "eq"
  | "gt"
  | "gte"
  | "is_not_null"
  | "is_null"
  | "lt"
  | "lte"
  | "ne"
  | "starts_with";

export type BackendQueryCondition = {
  column: string;
  id: string;
  operator: BackendQueryOperator;
  value: string;
  valueSecondary?: string;
};

export type BackendTableQuery = {
  conditions: BackendQueryCondition[];
  matchMode?: BackendQueryMatchMode;
  sortColumn?: string;
  sortDirection?: SortDirection;
};

export type BackendQueryPreset = {
  id: string;
  label: string;
  query: BackendTableQuery;
};

type BackendQueryConfig = {
  columns: string[];
  loading?: boolean;
  onChange: (query: BackendTableQuery) => void;
  onDeletePreset?: (id: string) => void;
  onNameChange?: (name: string) => void;
  onSavePreset?: (name: string, query: BackendTableQuery) => void;
  presets?: BackendQueryPreset[];
  queryName?: string;
  value: BackendTableQuery;
};

type HistogramBin = {
  count: number;
  label: string;
};

type ValueCount = {
  count: number;
  value: string;
};

type ColumnManualFilterState = {
  caseSensitive: boolean;
  operator: string;
  presetLabel?: string;
  timeZoneName: string;
  valueText: string;
  valueTextSecondary: string;
};
export type DataTableManualFilterState = Partial<ColumnManualFilterState> & { operator: string };
export type DataTableFilterPreset = {
  filters: Record<string, DataTableManualFilterState>;
  label: string;
  title?: string;
};

type ColumnProfile = {
  average?: number;
  blankCount: number;
  column: string;
  distinct: number;
  histogramBins: HistogramBin[];
  kind: ColumnKind;
  max?: number | string;
  median?: number;
  min?: number | string;
  nonEmpty: number;
  p25?: number;
  p75?: number;
  stddev?: number;
  topValues: ValueCount[];
  total?: number;
  totalRows: number;
  temporalUnit?: "date" | "datetime";
  timeZoneName?: string;
  typeLabel: string;
};

type HeaderPopoverState = {
  column: string;
  kind: "filter" | "stats";
  left: number;
  top: number;
};
type RowActionConfig = {
  isAvailable?: (row: DataRow) => boolean;
  label: string;
  onSelect: (row: DataRow) => void;
};
type RowMenuState = {
  left: number;
  row: DataRow;
  top: number;
};
type ColumnOptionMeta = {
  column: string;
  label: string;
  searchText: string;
  typeLabel: string;
};

type ColumnOrderStorageKeys = {
  legacy: string[];
  primary: string;
};

type DataTableProps = {
  backendQuery?: BackendQueryConfig;
  columns?: string[];
  defaultFilterPreset?: DataTableFilterPreset;
  defaultSort?: SortState;
  empty?: string;
  fitToContent?: boolean;
  filterPresets?: DataTableFilterPreset[];
  isRowSelected?: (row: DataRow) => boolean;
  onRowClick?: (row: DataRow) => void;
  preserveFiltersOnDataChange?: boolean;
  rowAction?: RowActionConfig;
  rows: DataRow[];
  title?: string;
  transposeHelper?: boolean;
};

const TABLE_DENSITY_MODES = ["compact", "comfortable", "wide"] as const;
const TRANSPOSE_MAX_SOURCE_ROWS = 80;
const BACKEND_QUERY_OPERATORS: Array<{ label: string; needsSecondValue?: boolean; needsValue?: boolean; value: BackendQueryOperator }> = [
  { label: "Contains", needsValue: true, value: "contains" },
  { label: "Equals", needsValue: true, value: "eq" },
  { label: "Not equal", needsValue: true, value: "ne" },
  { label: "Greater than", needsValue: true, value: "gt" },
  { label: "Greater or equal", needsValue: true, value: "gte" },
  { label: "Less than", needsValue: true, value: "lt" },
  { label: "Less or equal", needsValue: true, value: "lte" },
  { label: "Between", needsSecondValue: true, needsValue: true, value: "between" },
  { label: "Starts with", needsValue: true, value: "starts_with" },
  { label: "Ends with", needsValue: true, value: "ends_with" },
  { label: "Is blank", value: "is_null" },
  { label: "Is not blank", value: "is_not_null" },
];
let backendQueryConditionSequence = 0;

export function DataTable({ backendQuery, columns, defaultFilterPreset, defaultSort, empty = "No rows.", fitToContent = false, filterPresets = [], isRowSelected, onRowClick, preserveFiltersOnDataChange = false, rowAction, rows, title, transposeHelper = false }: DataTableProps) {
  const baseColumns = useMemo(() => {
    if (columns?.length) return columns;
    return Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
  }, [columns, rows]);
  const columnOrderStorageKeys = useMemo(() => buildColumnOrderStorageKeys(title, baseColumns), [baseColumns, title]);

  const [activeValueFiltersByColumn, setActiveValueFiltersByColumn] = useState<Record<string, string[]>>({});
  const [backendQueryOpen, setBackendQueryOpen] = useState(false);
  const [backendQueryDraft, setBackendQueryDraft] = useState<BackendTableQuery>(() => normalizeBackendQuery(backendQuery?.value));
  const [columnsMenuOpen, setColumnsMenuOpen] = useState(false);
  const [columnsSearch, setColumnsSearch] = useState("");
  const deferredColumnsSearch = useDeferredValue(columnsSearch);
  const [columnOrder, setColumnOrder] = useState<string[]>([]);
  const [draggedColumn, setDraggedColumn] = useState<string | null>(null);
  const [dragOverColumn, setDragOverColumn] = useState<string | null>(null);
  const [densityMode, setDensityMode] = useState<TableDensityMode>("compact");
  const [hiddenColumns, setHiddenColumns] = useState<string[]>([]);
  const [layoutMode, setLayoutMode] = useState<TableLayoutMode>("fit_data");
  const resolvedColumns = useMemo(() => applyColumnOrder(baseColumns, columnOrder), [baseColumns, columnOrder]);
  const [manualFiltersByColumn, setManualFiltersByColumn] = useState<Record<string, ColumnManualFilterState>>(
    () => filterPresetForColumns(defaultFilterPreset, resolvedColumns)
  );
  const [openPopover, setOpenPopover] = useState<HeaderPopoverState | null>(null);
  const [rowMenu, setRowMenu] = useState<RowMenuState | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortState>(null);
  const [toolbarMenuOpen, setToolbarMenuOpen] = useState(false);
  const [transposeOpen, setTransposeOpen] = useState(false);
  const [selectedTransposeColumn, setSelectedTransposeColumn] = useState<string | null>(null);
  const tableIdentityRef = useRef<string | null>(null);

  const profilesByColumn = useMemo<Record<string, ColumnProfile>>(() => {
    return Object.fromEntries(baseColumns.map((column) => [column, buildColumnProfile(rows, column)]));
  }, [baseColumns, rows]);

  const hiddenColumnsSet = useMemo(() => new Set(hiddenColumns), [hiddenColumns]);
  const visibleColumns = resolvedColumns.filter((column) => !hiddenColumnsSet.has(column));
  const usableColumns = visibleColumns.length ? visibleColumns : resolvedColumns.slice(0, 1);
  const columnIndexByName = useMemo(() => Object.fromEntries(resolvedColumns.map((column, index) => [column, index])), [resolvedColumns]);
  const columnOptionMetaByName = useMemo<Record<string, ColumnOptionMeta>>(() => {
    return Object.fromEntries(
      baseColumns.map((column) => {
        const label = displayName(column);
        const typeLabel = profilesByColumn[column]?.typeLabel ?? "Column";
        return [column, { column, label, searchText: `${column} ${label} ${typeLabel}`.toLowerCase(), typeLabel }];
      }),
    );
  }, [baseColumns, profilesByColumn]);
  const orderedColumnOptions = useMemo(
    () => resolvedColumns.map((column) => columnOptionMetaByName[column]).filter((item): item is ColumnOptionMeta => Boolean(item)),
    [columnOptionMetaByName, resolvedColumns],
  );
  const filteredColumnOptions = useMemo(() => {
    const query = deferredColumnsSearch.trim().toLowerCase();
    if (!query) return orderedColumnOptions;
    return orderedColumnOptions.filter((item) => item.searchText.includes(query));
  }, [deferredColumnsSearch, orderedColumnOptions]);
  const effectiveSort = sort ?? defaultSort ?? (resolvedColumns[0] ? { column: resolvedColumns[0], direction: "asc" as const } : null);
  const activeFilterCount =
    Object.values(activeValueFiltersByColumn).reduce((count, values) => count + values.length, 0) +
    Object.keys(manualFiltersByColumn).length;
  const tableIdentityKey = useMemo(() => buildTableIdentityKey(rows, baseColumns), [baseColumns, rows]);
  const activeFilterChips = useMemo(() => {
    return resolvedColumns.flatMap((column) => {
      const chips: Array<{ column: string; key: string; summary: string; type: "manual" | "value" }> = [];
      const valueFilters = activeValueFiltersByColumn[column]?.filter(Boolean) ?? [];
      if (valueFilters.length) {
        chips.push({
          column,
          key: `value:${column}`,
          summary: formatValueFilterSummary(valueFilters),
          type: "value",
        });
      }
      const manualFilter = manualFiltersByColumn[column];
      if (manualFilter) {
        chips.push({
          column,
          key: `manual:${column}`,
          summary: formatManualFilterSummary(manualFilter, profilesByColumn[column]),
          type: "manual",
        });
      }
      return chips;
    });
  }, [activeValueFiltersByColumn, manualFiltersByColumn, profilesByColumn, resolvedColumns]);

  const filteredRows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (query) {
        const matchesQuery = resolvedColumns.some((column) =>
          formatCell(column, row[column]).toLowerCase().includes(query),
        );
        if (!matchesQuery) return false;
      }

      const matchesValueFilters = Object.entries(activeValueFiltersByColumn).every(([column, selectedValues]) => {
        if (!selectedValues.length) return true;
        return selectedValues.includes(formatFilterValue(row[column]));
      });
      if (!matchesValueFilters) return false;

      return Object.entries(manualFiltersByColumn).every(([column, filter]) =>
        rowMatchesManualFilter(row[column], profilesByColumn[column], filter),
      );
    });
  }, [activeValueFiltersByColumn, manualFiltersByColumn, profilesByColumn, resolvedColumns, rows, search]);

  const sortedRows = useMemo(() => {
    if (!effectiveSort) return filteredRows;
    return [...filteredRows].sort((left, right) => {
      return compareCellsForSort(left[effectiveSort.column], right[effectiveSort.column], effectiveSort.direction);
    });
  }, [effectiveSort, filteredRows]);
  const transposeView = useMemo(
    () => buildTransposeView(sortedRows, resolvedColumns),
    [resolvedColumns, sortedRows],
  );

  const numericColumnCount = resolvedColumns.filter((column) => profilesByColumn[column]?.kind === "numeric").length;
  const activeSortLabel = effectiveSort ? `${displayName(effectiveSort.column)} ${effectiveSort.direction}` : "None";
  const openProfile = openPopover ? profilesByColumn[openPopover.column] : null;
  const backendQueryColumns = backendQuery?.columns.length ? backendQuery.columns : resolvedColumns;
  const backendQueryActiveCount = backendQuery ? countBackendQueryClauses(backendQuery.value) : 0;
  const backendQueryChips = useMemo(() => backendQuery ? backendQueryConditionChips(backendQuery.value.conditions ?? []) : [], [backendQuery]);
  const totalActiveFilterCount = activeFilterCount + backendQueryActiveCount;
  const columnWidthsByName = useMemo(
    () => buildColumnWidthsByName({ densityMode, layoutMode, rows: sortedRows, visibleColumns: usableColumns }),
    [densityMode, layoutMode, sortedRows, usableColumns],
  );
  const tableContentWidth = usableColumns.reduce((total, column) => total + (columnWidthsByName[column] ?? 120), 0);

  useEffect(() => {
    setBackendQueryDraft(normalizeBackendQuery(backendQuery?.value));
  }, [backendQuery?.value]);

  useEffect(() => {
    setColumnOrder(readColumnOrderPreference(columnOrderStorageKeys, baseColumns));
    setDraggedColumn(null);
    setDragOverColumn(null);
  }, [baseColumns, columnOrderStorageKeys]);

  useEffect(() => {
    if (!openPopover) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target?.closest(".data-table-floating-popover") ||
        target?.closest("[data-table-popover-trigger='true']")
      ) {
        return;
      }
      setOpenPopover(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenPopover(null);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [openPopover]);

  useEffect(() => {
    if (!rowMenu) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".data-table-row-menu")) return;
      setRowMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setRowMenu(null);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [rowMenu]);

  useEffect(() => {
    if (!backendQueryOpen && !columnsMenuOpen && !toolbarMenuOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target?.closest(".data-table-popover") ||
        target?.closest("[data-table-menu-trigger='true']")
      ) {
        return;
      }
      setBackendQueryOpen(false);
      setColumnsMenuOpen(false);
      setToolbarMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setBackendQueryOpen(false);
        setColumnsMenuOpen(false);
        setToolbarMenuOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [backendQueryOpen, columnsMenuOpen, toolbarMenuOpen]);

  useEffect(() => {
    if (tableIdentityRef.current === null) {
      tableIdentityRef.current = tableIdentityKey;
      return;
    }
    if (tableIdentityRef.current === tableIdentityKey) return;
    tableIdentityRef.current = tableIdentityKey;
    if (preserveFiltersOnDataChange) {
      const validColumns = new Set(resolvedColumns);
      setActiveValueFiltersByColumn((current) => keepExistingColumnFilters(current, validColumns));
      setManualFiltersByColumn((current) => keepExistingColumnFilters(current, validColumns));
      setOpenPopover(null);
      setRowMenu(null);
      return;
    }
    setActiveValueFiltersByColumn({});
    setBackendQueryOpen(false);
    setColumnsSearch("");
    setManualFiltersByColumn(filterPresetForColumns(defaultFilterPreset, resolvedColumns));
    setOpenPopover(null);
    setRowMenu(null);
  }, [defaultFilterPreset, preserveFiltersOnDataChange, resolvedColumns, tableIdentityKey]);

  const toggleSort = (column: string) => {
    setSort((current) => {
      if (!current || current.column !== column) return { column, direction: "asc" };
      if (current.direction === "asc") return { column, direction: "desc" };
      return null;
    });
  };

  const applyValueFilter = (column: string, values: string[]) => {
    setActiveValueFiltersByColumn((current) => {
      const next = { ...current };
      if (values.length) next[column] = values;
      else delete next[column];
      return next;
    });
  };

  const applyManualFilter = (column: string, filter: ColumnManualFilterState | null) => {
    setManualFiltersByColumn((current) => {
      const next = { ...current };
      if (filter) next[column] = filter;
      else delete next[column];
      return next;
    });
  };

  const applyFilterPreset = (preset: DataTableFilterPreset) => {
    const presetFilters = filterPresetForColumns(preset, resolvedColumns);
    if (!Object.keys(presetFilters).length) return;
    setManualFiltersByColumn((current) => ({ ...current, ...presetFilters }));
    setActiveValueFiltersByColumn((current) => {
      const next = { ...current };
      Object.keys(presetFilters).forEach((column) => delete next[column]);
      return next;
    });
    setOpenPopover(null);
    setToolbarMenuOpen(false);
  };

  const clearActiveFilters = () => {
    setActiveValueFiltersByColumn({});
    setManualFiltersByColumn({});
    setOpenPopover(null);
    if (backendQuery) backendQuery.onChange({ ...normalizeBackendQuery(backendQuery.value), conditions: [] });
  };

  const removeActiveFilter = (chip: { column: string; type: "manual" | "value" }) => {
    if (chip.type === "manual") applyManualFilter(chip.column, null);
    else applyValueFilter(chip.column, []);
  };

  const toggleColumnVisibility = (column: string) => {
    setHiddenColumns((current) => {
      if (current.includes(column)) return current.filter((item) => item !== column);
      return [...current, column];
    });
  };

  const saveColumnOrder = (nextOrder: string[]) => {
    const normalized = normalizeColumnOrder(nextOrder, baseColumns);
    setColumnOrder(normalized);
    writeColumnOrderPreference(columnOrderStorageKeys.primary, normalized);
  };

  const moveColumnBefore = (sourceColumn: string, targetColumn: string) => {
    if (sourceColumn === targetColumn) return;
    const currentOrder = applyColumnOrder(baseColumns, columnOrder);
    const sourceIndex = currentOrder.indexOf(sourceColumn);
    const targetIndex = currentOrder.indexOf(targetColumn);
    if (sourceIndex < 0 || targetIndex < 0) return;
    const nextOrder = [...currentOrder];
    const [source] = nextOrder.splice(sourceIndex, 1);
    const adjustedTargetIndex = nextOrder.indexOf(targetColumn);
    nextOrder.splice(adjustedTargetIndex < 0 ? targetIndex : adjustedTargetIndex, 0, source);
    saveColumnOrder(nextOrder);
  };

  const moveColumnByOffset = (column: string, offset: number) => {
    const currentOrder = applyColumnOrder(baseColumns, columnOrder);
    const sourceIndex = currentOrder.indexOf(column);
    const targetIndex = sourceIndex + offset;
    if (sourceIndex < 0 || targetIndex < 0 || targetIndex >= currentOrder.length) return;
    const nextOrder = [...currentOrder];
    const [source] = nextOrder.splice(sourceIndex, 1);
    nextOrder.splice(targetIndex, 0, source);
    saveColumnOrder(nextOrder);
  };

  const resetColumnOrder = () => {
    setColumnOrder([]);
    removeColumnOrderPreference(columnOrderStorageKeys);
    setDraggedColumn(null);
    setDragOverColumn(null);
  };

  const handleColumnDragStart = (event: DragEvent<HTMLElement>, column: string) => {
    setDraggedColumn(column);
    setDragOverColumn(column);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", column);
  };

  const handleColumnDragOver = (event: DragEvent<HTMLElement>, column: string) => {
    if (!draggedColumn || draggedColumn === column) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setDragOverColumn(column);
  };

  const handleColumnDrop = (event: DragEvent<HTMLElement>, column: string) => {
    event.preventDefault();
    const sourceColumn = draggedColumn ?? event.dataTransfer.getData("text/plain");
    if (sourceColumn) moveColumnBefore(sourceColumn, column);
    setDraggedColumn(null);
    setDragOverColumn(null);
  };

  const handleColumnDragEnd = () => {
    setDraggedColumn(null);
    setDragOverColumn(null);
  };

  const toggleHeaderPopover = (kind: HeaderPopoverState["kind"], column: string, target: HTMLElement) => {
    setOpenPopover((current) => {
      if (current?.kind === kind && current.column === column) return null;
      const rect = target.getBoundingClientRect();
      const width = Math.min(kind === "filter" ? 760 : 640, window.innerWidth - 24);
      const left = Math.min(Math.max(12, rect.right - width), Math.max(12, window.innerWidth - width - 12));
      return { column, kind, left, top: rect.bottom + 8 };
    });
  };

  const resetTable = () => {
    setActiveValueFiltersByColumn({});
    setBackendQueryOpen(false);
    setColumnsMenuOpen(false);
    setDensityMode("compact");
    setHiddenColumns([]);
    setLayoutMode("fit_data");
    setManualFiltersByColumn({});
    setOpenPopover(null);
    setRowMenu(null);
    setSearch("");
    setSort(null);
    setToolbarMenuOpen(false);
    setTransposeOpen(false);
    setSelectedTransposeColumn(null);
    resetColumnOrder();
    if (backendQuery) {
      const emptyQuery = emptyBackendTableQuery();
      setBackendQueryDraft(emptyQuery);
      backendQuery.onChange(emptyQuery);
    }
  };

  const openRowActionMenu = (event: MouseEvent<HTMLTableRowElement>, row: DataRow) => {
    if (!rowAction || rowAction.isAvailable?.(row) === false) return;
    const menuWidth = 210;
    const menuHeight = 46;
    setRowMenu({
      left: Math.min(Math.max(12, event.clientX), Math.max(12, window.innerWidth - menuWidth - 12)),
      row,
      top: Math.min(Math.max(12, event.clientY + 8), Math.max(12, window.innerHeight - menuHeight - 12)),
    });
  };

  const renderDensityControls = (buttonClassName = "table-segment-button") =>
    TABLE_DENSITY_MODES.map((candidateDensityMode) => (
      <button
        className={densityMode === candidateDensityMode ? `${buttonClassName} active` : buttonClassName}
        key={candidateDensityMode}
        onClick={() => setDensityMode(candidateDensityMode)}
        type="button"
      >
        {candidateDensityMode}
      </button>
    ));

  const renderLayoutControls = (buttonClassName = "table-fit-button") => (
    <>
      <button
        className={layoutMode === "fit_header" ? `${buttonClassName} active` : buttonClassName}
        onClick={() => setLayoutMode("fit_header")}
        type="button"
      >
        Fit header
      </button>
      <button
        className={layoutMode === "fit_data" ? `${buttonClassName} active` : buttonClassName}
        onClick={() => setLayoutMode("fit_data")}
        type="button"
      >
        Fit data
      </button>
    </>
  );

  const renderFilterPresetButtons = () =>
    filterPresets.map((preset) => (
      <button
        className="table-text-button"
        key={preset.label}
        onClick={() => applyFilterPreset(preset)}
        title={preset.title ?? preset.label}
        type="button"
      >
        <Filter size={13} />
        {preset.label}
      </button>
    ));

  const renderTransposeButton = () =>
    transposeHelper ? (
      <button
        className="table-text-button"
        disabled={!sortedRows.length || !resolvedColumns.length}
        onClick={() => setTransposeOpen(true)}
        title="Open a transposed view of the current filtered and sorted table"
        type="button"
      >
        <Rows3 size={13} />
        Transpose
      </button>
    ) : null;

  const renderColumnToggles = () =>
    filteredColumnOptions.length ? (
      filteredColumnOptions.map((option) => {
        const column = option.column;
        const visible = !hiddenColumnsSet.has(column);
        const columnIndex = columnIndexByName[column] ?? -1;
        const className = [
          "data-table-column-toggle",
          visible ? "selected" : "",
          draggedColumn === column ? "dragging" : "",
          dragOverColumn === column && draggedColumn !== column ? "drag-over" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <div
            className={className}
            key={column}
            onDragOver={(event) => handleColumnDragOver(event, column)}
            onDrop={(event) => handleColumnDrop(event, column)}
          >
            <span
              className="data-table-column-drag-handle"
              draggable
              onDragEnd={handleColumnDragEnd}
              onDragStart={(event) => handleColumnDragStart(event, column)}
              title="Drag to reorder column"
            >
              <GripVertical size={13} />
            </span>
            <button className="data-table-column-toggle-visibility" onClick={() => toggleColumnVisibility(column)} type="button">
              <span className="data-table-column-toggle-mark">{visible ? <Check size={12} /> : null}</span>
              <span className="data-table-column-toggle-text">
                <span>{option.label}</span>
                <small>{option.typeLabel}</small>
              </span>
            </button>
            <span className="data-table-column-order-buttons" aria-label={`Move ${option.label}`}>
              <button
                className="data-table-column-order-button"
                disabled={columnIndex <= 0}
                onClick={() => moveColumnByOffset(column, -1)}
                title="Move column up"
                type="button"
              >
                <ArrowUp size={12} />
              </button>
              <button
                className="data-table-column-order-button"
                disabled={columnIndex < 0 || columnIndex >= resolvedColumns.length - 1}
                onClick={() => moveColumnByOffset(column, 1)}
                title="Move column down"
                type="button"
              >
                <ArrowDown size={12} />
              </button>
            </span>
          </div>
        );
      })
    ) : (
      <div className="data-table-columns-empty">No columns match the search.</div>
    );

  const renderColumnsPanel = (menu = false) => (
    <>
      <div className="data-table-columns-header">
        <div>
          <div className="data-table-popover-title">Columns</div>
          <span>{formatInteger(usableColumns.length)} of {formatInteger(resolvedColumns.length)} visible</span>
        </div>
        <label className="data-table-columns-search" aria-label="Search columns">
          <Search size={13} />
          <input onChange={(event) => setColumnsSearch(event.target.value)} placeholder="Find column" type="search" value={columnsSearch} />
        </label>
      </div>
      <div className={menu ? "data-table-columns-list menu-columns" : "data-table-columns-list"}>{renderColumnToggles()}</div>
      <div className="data-table-columns-actions">
        <button className="table-text-button data-table-show-all-button" onClick={() => setHiddenColumns([])} type="button">
          Show all columns
        </button>
        <button className="table-text-button data-table-reset-order-button" disabled={!columnOrder.length} onClick={resetColumnOrder} type="button">
          <RotateCcw size={13} />
          Reset order
        </button>
      </div>
    </>
  );

  const addBackendCondition = () => {
    const condition = buildBackendQueryCondition(backendQueryColumns);
    setBackendQueryDraft((current) => ({
      ...normalizeBackendQuery(current),
      conditions: [...normalizeBackendQuery(current).conditions, condition],
    }));
  };

  const updateBackendCondition = (id: string, patch: Partial<BackendQueryCondition>) => {
    setBackendQueryDraft((current) => ({
      ...normalizeBackendQuery(current),
      conditions: normalizeBackendQuery(current).conditions.map((condition) =>
        condition.id === id ? { ...condition, ...patch } : condition,
      ),
    }));
  };

  const removeBackendCondition = (id: string) => {
    setBackendQueryDraft((current) => ({
      ...normalizeBackendQuery(current),
      conditions: normalizeBackendQuery(current).conditions.filter((condition) => condition.id !== id),
    }));
  };

  const applyBackendQuery = () => {
    if (!backendQuery) return;
    const cleanedQuery = cleanBackendQuery(backendQueryDraft, backendQueryColumns);
    setBackendQueryDraft(cleanedQuery);
    backendQuery.onChange(cleanedQuery);
    setBackendQueryOpen(false);
    setToolbarMenuOpen(false);
  };

  const clearBackendQuery = () => {
    if (!backendQuery) return;
    const emptyQuery = emptyBackendTableQuery();
    setBackendQueryDraft(emptyQuery);
    backendQuery.onChange(emptyQuery);
  };

  const saveBackendQueryPreset = () => {
    if (!backendQuery?.onSavePreset) return;
    const cleanedQuery = cleanBackendQuery(backendQueryDraft, backendQueryColumns);
    backendQuery.onSavePreset((backendQuery.queryName ?? "").trim() || "Scanner Query", cleanedQuery);
    setBackendQueryDraft(cleanedQuery);
  };

  const applyBackendQueryPreset = (preset: BackendQueryPreset) => {
    if (!backendQuery) return;
    const cleanedQuery = cleanBackendQuery(preset.query, backendQueryColumns);
    setBackendQueryDraft(cleanedQuery);
    backendQuery.onNameChange?.(preset.label);
    backendQuery.onChange(cleanedQuery);
    setBackendQueryOpen(false);
    setToolbarMenuOpen(false);
  };

  const removeBackendQueryConditionFromActive = (conditionId: string) => {
    if (!backendQuery) return;
    const nextQuery = {
      ...normalizeBackendQuery(backendQuery.value),
      conditions: normalizeBackendQuery(backendQuery.value).conditions.filter((condition) => condition.id !== conditionId),
    };
    backendQuery.onChange(nextQuery);
    setBackendQueryDraft(nextQuery);
  };

  const renderBackendQueryPanel = () => {
    if (!backendQuery) return null;
    const draft = normalizeBackendQuery(backendQueryDraft);
    return (
        <div className="data-table-query-panel">
          <div className="data-table-query-header">
            <div>
              <div className="data-table-popover-title">Backend Query</div>
            </div>
            {backendQuery.loading ? <span className="data-table-query-loading">Running</span> : null}
          </div>
          <div className="data-table-query-section">
            <div className="table-popover-section-title">Name</div>
            <input
              aria-label="Backend query name"
              className="data-table-query-name-input"
              onChange={(event) => backendQuery.onNameChange?.(event.target.value)}
              placeholder="Query name"
              value={backendQuery.queryName ?? ""}
            />
          </div>
          {backendQuery.presets?.length ? (
            <div className="data-table-query-section">
              <div className="table-popover-section-title">Saved Queries</div>
              <div className="data-table-query-preset-list">
                {backendQuery.presets.map((preset) => (
                  <span className="data-table-query-preset" key={preset.id}>
                    <button className="table-text-button" onClick={() => applyBackendQueryPreset(preset)} type="button">
                      <Filter size={13} />
                      {preset.label}
                    </button>
                    {backendQuery.onDeletePreset ? (
                      <button className="table-icon-button danger" onClick={() => backendQuery.onDeletePreset?.(preset.id)} title={`Delete ${preset.label}`} type="button">
                        <Trash2 size={13} />
                      </button>
                    ) : null}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
          <div className="data-table-query-body">
          <div className="data-table-query-section">
            <div className="table-popover-section-title">Where</div>
            <div className="data-table-query-mode" aria-label="Backend query match mode" role="group">
              <button
                className={draft.matchMode === "all" ? "table-segment-button active" : "table-segment-button"}
                onClick={() => setBackendQueryDraft((current) => ({ ...normalizeBackendQuery(current), matchMode: "all" }))}
                type="button"
              >
                Match all
              </button>
              <button
                className={draft.matchMode === "any" ? "table-segment-button active" : "table-segment-button"}
                onClick={() => setBackendQueryDraft((current) => ({ ...normalizeBackendQuery(current), matchMode: "any" }))}
                type="button"
              >
                Match any
              </button>
            </div>
            {draft.conditions.length ? (
              draft.conditions.map((condition) => {
                const operator = BACKEND_QUERY_OPERATORS.find((item) => item.value === condition.operator) ?? BACKEND_QUERY_OPERATORS[0];
                return (
                  <div className="data-table-query-row" key={condition.id}>
                    <select
                      aria-label="Query column"
                      onChange={(event) => updateBackendCondition(condition.id, { column: event.target.value })}
                      value={condition.column}
                    >
                      {backendQueryColumns.map((column) => (
                        <option key={column} value={column}>
                          {displayName(column)}
                        </option>
                      ))}
                    </select>
                    <select
                      aria-label="Query operator"
                      onChange={(event) => updateBackendCondition(condition.id, { operator: event.target.value as BackendQueryOperator })}
                      value={condition.operator}
                    >
                      {BACKEND_QUERY_OPERATORS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    {operator.needsValue ? (
                      <input
                        aria-label="Query value"
                        onChange={(event) => updateBackendCondition(condition.id, { value: event.target.value })}
                        placeholder="Value"
                        value={condition.value}
                      />
                    ) : null}
                    {operator.needsSecondValue ? (
                      <input
                        aria-label="Query second value"
                        onChange={(event) => updateBackendCondition(condition.id, { valueSecondary: event.target.value })}
                        placeholder="And"
                        value={condition.valueSecondary ?? ""}
                      />
                    ) : null}
                    <button className="table-icon-button danger" onClick={() => removeBackendCondition(condition.id)} title="Remove condition" type="button">
                      <Trash2 size={13} />
                    </button>
                  </div>
                );
              })
            ) : (
              <div className="data-table-query-empty">No backend filters.</div>
            )}
            <button className="table-text-button data-table-query-add" onClick={addBackendCondition} type="button">
              <Plus size={13} />
              Add condition
            </button>
          </div>
          <div className="data-table-query-section">
            <div className="table-popover-section-title">Sort before load</div>
            <div className="data-table-query-sort-row">
              <select
                aria-label="Backend sort column"
                onChange={(event) => setBackendQueryDraft((current) => ({ ...normalizeBackendQuery(current), sortColumn: event.target.value || undefined }))}
                value={draft.sortColumn ?? ""}
              >
                <option value="">No backend sort</option>
                {backendQueryColumns.map((column) => (
                  <option key={column} value={column}>
                    {displayName(column)}
                  </option>
                ))}
              </select>
              <select
                aria-label="Backend sort direction"
                disabled={!draft.sortColumn}
                onChange={(event) => setBackendQueryDraft((current) => ({ ...normalizeBackendQuery(current), sortDirection: event.target.value as SortDirection }))}
                value={draft.sortDirection ?? "asc"}
              >
                <option value="asc">Ascending</option>
                <option value="desc">Descending</option>
              </select>
            </div>
          </div>
        </div>
        <div className="data-table-query-actions">
          <button className="table-text-button" onClick={clearBackendQuery} type="button">
            Clear query
          </button>
          {backendQuery.onSavePreset ? (
            <button className="table-text-button" onClick={saveBackendQueryPreset} type="button">
              Save query
            </button>
          ) : null}
          <button className="table-text-button primary" disabled={backendQuery.loading} onClick={applyBackendQuery} type="button">
            Apply query
          </button>
        </div>
      </div>
    );
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
            <span>{formatInteger(totalActiveFilterCount)} filters</span>
          </div>
        </div>
        <div className="data-table-toolbar-actions data-table-toolbar-actions-wide">
          {renderFilterPresetButtons()}
          {backendQuery?.presets?.map((preset) => (
            <button className="table-text-button" key={preset.id} onClick={() => applyBackendQueryPreset(preset)} title={`Apply ${preset.label}`} type="button">
              <Database size={13} />
              {preset.label}
            </button>
          ))}
          {renderTransposeButton()}
          <span className="data-table-sort-chip">Sort: {activeSortLabel}</span>
          <div className="data-table-toolbar-control" aria-label="Table density">
            {renderDensityControls()}
          </div>
          <div className="data-table-toolbar-control" aria-label="Table layout">
            {renderLayoutControls()}
          </div>
          {backendQuery ? (
            <div className="data-table-action-menu">
              <button
                className={backendQueryOpen ? "table-icon-button active" : "table-icon-button"}
                data-table-menu-trigger="true"
                onClick={() => setBackendQueryOpen((current) => !current)}
                title="Backend query"
                type="button"
              >
                <Database size={15} />
                {backendQueryActiveCount ? <span className="table-icon-button-badge">{backendQueryActiveCount}</span> : null}
              </button>
              {backendQueryOpen ? (
                <div className="data-table-popover data-table-query-popover table-popover-divided">
                  {renderBackendQueryPanel()}
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="data-table-action-menu">
            <button
              className={columnsMenuOpen ? "table-icon-button active" : "table-icon-button"}
              data-table-menu-trigger="true"
              onClick={() => setColumnsMenuOpen((current) => !current)}
              title="Columns"
              type="button"
            >
              <Columns3 size={15} />
            </button>
            {columnsMenuOpen ? (
              <div className="data-table-popover data-table-columns-popover table-popover-divided">
                {renderColumnsPanel()}
              </div>
            ) : null}
          </div>
          <button className="table-text-button" onClick={resetTable} type="button">
            Reset
          </button>
        </div>
        <div className="data-table-toolbar-overflow">
          <button
            className={toolbarMenuOpen ? "table-icon-button active" : "table-icon-button"}
            data-table-menu-trigger="true"
            onClick={() => setToolbarMenuOpen((current) => !current)}
            title="Table options"
            type="button"
          >
            <MoreHorizontal size={16} />
          </button>
          {toolbarMenuOpen ? (
            <div className="data-table-popover data-table-toolbar-menu table-popover-divided">
              <div className="data-table-toolbar-menu-header">
                <div className="data-table-popover-title">Table options</div>
                <span className="data-table-sort-chip">Sort: {activeSortLabel}</span>
              </div>
              {filterPresets.length ? (
                <div className="data-table-toolbar-menu-section actions">
                  {renderFilterPresetButtons()}
                </div>
              ) : null}
              {transposeHelper ? (
                <div className="data-table-toolbar-menu-section actions">
                  {renderTransposeButton()}
                </div>
              ) : null}
              <div className="data-table-toolbar-menu-section">
                <div className="table-popover-section-title">Density</div>
                <div className="data-table-toolbar-control menu-control" aria-label="Table density">
                  {renderDensityControls()}
                </div>
              </div>
              <div className="data-table-toolbar-menu-section">
                <div className="table-popover-section-title">Layout</div>
                <div className="data-table-toolbar-control menu-control" aria-label="Table layout">
                  {renderLayoutControls()}
                </div>
              </div>
              {backendQuery ? (
                <div className="data-table-toolbar-menu-section">
                  {renderBackendQueryPanel()}
                </div>
              ) : null}
              <div className="data-table-toolbar-menu-section">
                {renderColumnsPanel(true)}
              </div>
              <div className="data-table-toolbar-menu-section actions">
                <button className="table-text-button" onClick={resetTable} type="button">
                  Reset table
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {activeFilterChips.length || backendQueryChips.length ? (
        <div className="data-table-active-filters" aria-label="Active filters">
          <span className="data-table-active-filters-label">Filters</span>
          <div className="data-table-active-filter-list">
            {backendQueryChips.map((chip) => (
              <span className="data-table-active-filter-chip" key={`backend:${chip.id}`}>
                <span className="data-table-active-filter-column">{displayName(chip.column)}</span>
                <span className="data-table-active-filter-summary">{chip.summary}</span>
                <button
                  aria-label={`Remove ${displayName(chip.column)} query filter`}
                  onClick={() => removeBackendQueryConditionFromActive(chip.id)}
                  title={`Remove ${displayName(chip.column)} query filter`}
                  type="button"
                >
                  <X size={12} />
                </button>
              </span>
            ))}
            {activeFilterChips.map((chip) => (
              <span className="data-table-active-filter-chip" key={chip.key}>
                <span className="data-table-active-filter-column">{displayName(chip.column)}</span>
                <span className="data-table-active-filter-summary">{chip.summary}</span>
                <button
                  aria-label={`Remove ${displayName(chip.column)} filter`}
                  onClick={() => removeActiveFilter(chip)}
                  title={`Remove ${displayName(chip.column)} filter`}
                  type="button"
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
          <button className="table-text-button data-table-clear-filters-button" onClick={clearActiveFilters} type="button">
            Clear all
          </button>
        </div>
      ) : null}

      <div className="data-table-scroll">
        <table
          className={`data-table ${densityMode} ${layoutMode === "fit_header" ? "fit-header" : "fit-data"}${fitToContent ? " fit-content-width" : ""}`}
          style={{ width: `${tableContentWidth}px` }}
        >
          <colgroup>
            {usableColumns.map((column) => (
              <col key={column} style={{ width: `${columnWidthsByName[column] ?? 120}px` }} />
            ))}
          </colgroup>
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
                const filterActive =
                  Boolean(activeValueFiltersByColumn[column]?.length) || Boolean(manualFiltersByColumn[column]);

                return (
                  <th key={column}>
                    <div className="data-table-header-cell">
                      <button className="data-table-header-sort" onClick={() => toggleSort(column)} type="button">
                        <span title={displayName(column)}>{columnHeaderLabel(column, densityMode)}</span>
                        {sortIcon}
                      </button>
                      <div className="data-table-header-actions">
                        {profile.kind !== "text" ? (
                          <button
                            className="table-icon-button"
                            data-table-popover-trigger="true"
                            onClick={(event: MouseEvent<HTMLButtonElement>) =>
                              toggleHeaderPopover("stats", column, event.currentTarget)
                            }
                            title={`Stats for ${displayName(column)}`}
                            type="button"
                          >
                            <BarChart3 size={13} />
                          </button>
                        ) : null}
                        <button
                          className={filterActive ? "table-icon-button active" : "table-icon-button"}
                          data-table-popover-trigger="true"
                          onClick={(event: MouseEvent<HTMLButtonElement>) =>
                            toggleHeaderPopover("filter", column, event.currentTarget)
                          }
                          title={`Filter ${displayName(column)}`}
                          type="button"
                        >
                          <Filter size={13} />
                        </button>
                      </div>
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sortedRows.length ? (
              sortedRows.map((row, rowIndex) => {
                const rowActionAvailable = Boolean(rowAction && rowAction.isAvailable?.(row) !== false);
                const rowClickable = Boolean(onRowClick) || rowActionAvailable;
                const selected = Boolean(isRowSelected?.(row));
                return (
                  <tr
                    className={[
                      rowClickable ? "data-table-row-actionable" : "",
                      onRowClick ? "data-table-row-clickable" : "",
                      selected ? "data-table-row-selected" : ""
                    ].filter(Boolean).join(" ") || undefined}
                    key={rowIndex}
                    onClick={rowClickable ? (event) => (onRowClick ? onRowClick(row) : openRowActionMenu(event, row)) : undefined}
                  >
                    {usableColumns.map((column) => (
                      <td className={cellClassName(row[column], column)} key={column}>
                        {formatCell(column, row[column])}
                      </td>
                    ))}
                  </tr>
                );
              })
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

      {transposeOpen ? (
        <Modal
          className="data-table-transpose-modal-panel"
          onClose={() => {
            setTransposeOpen(false);
            setSelectedTransposeColumn(null);
          }}
          title="Transposed Table"
        >
          <div className="data-table-transpose-summary">
            <span>{formatInteger(transposeView.rows.length)} fields</span>
            <span>{formatInteger(transposeView.sourceRowCount)} rows shown</span>
            {transposeView.truncated ? <span>Showing first {formatInteger(TRANSPOSE_MAX_SOURCE_ROWS)} filtered rows</span> : null}
          </div>
          <div className="data-table-transpose-scroll">
            <table className="data-table-transpose">
              <thead>
                <tr>
                  <th>Column</th>
                  {transposeView.sourceRowLabels.map((label, index) => (
                    <th key={`${label}:${index}`}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {transposeView.rows.map((row) => (
                  <tr
                    aria-selected={selectedTransposeColumn === row.column}
                    className={selectedTransposeColumn === row.column ? "selected" : undefined}
                    key={row.column}
                    onClick={() => setSelectedTransposeColumn((selected) => (selected === row.column ? null : row.column))}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelectedTransposeColumn((selected) => (selected === row.column ? null : row.column));
                      }
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <th>
                      <span>{row.label}</span>
                      <small>{row.column}</small>
                    </th>
                    {row.values.map((value, index) => (
                      <td key={`${row.column}:${index}`}>{value}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Modal>
      ) : null}

      {openPopover && openProfile && typeof document !== "undefined"
        ? createPortal(
            <div
              className={`data-table-floating-popover ${openPopover.kind === "filter" ? "filter" : "stats"}`}
              style={{ left: openPopover.left, top: openPopover.top }}
            >
              {openPopover.kind === "filter" ? (
                <ColumnFilterPopover
                  activeFilterValues={activeValueFiltersByColumn[openPopover.column] ?? []}
                  manualFilter={manualFiltersByColumn[openPopover.column] ?? null}
                  onApplyManualFilter={(filter) => applyManualFilter(openPopover.column, filter)}
                  onApplyValueFilter={(values) => applyValueFilter(openPopover.column, values)}
                  onHideColumn={() => {
                    toggleColumnVisibility(openPopover.column);
                    setOpenPopover(null);
                  }}
                  profile={openProfile}
                />
              ) : (
                <ColumnStatsPopover profile={openProfile} />
              )}
            </div>,
            document.body,
          )
        : null}
      {rowMenu && rowAction && typeof document !== "undefined"
        ? createPortal(
            <div className="data-table-row-menu" style={{ left: rowMenu.left, top: rowMenu.top }}>
              <button
                onClick={() => {
                  rowAction.onSelect(rowMenu.row);
                  setRowMenu(null);
                }}
                type="button"
              >
                <BarChart3 size={14} />
                {rowAction.label}
              </button>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

function emptyBackendTableQuery(): BackendTableQuery {
  return { conditions: [], matchMode: "all", sortDirection: "asc" };
}

type TransposeView = {
  rows: Array<{ column: string; label: string; values: string[] }>;
  sourceRowCount: number;
  sourceRowLabels: string[];
  truncated: boolean;
};

function buildTransposeView(rows: DataRow[], columns: string[]): TransposeView {
  const sourceRows = rows.slice(0, TRANSPOSE_MAX_SOURCE_ROWS);
  return {
    rows: columns.map((column) => ({
      column,
      label: displayName(column),
      values: sourceRows.map((row) => formatCell(column, row[column])),
    })),
    sourceRowCount: sourceRows.length,
    sourceRowLabels: sourceRows.map((row, index) => transposeSourceRowLabel(row, index)),
    truncated: rows.length > sourceRows.length,
  };
}

function buildColumnOrderStorageKeys(title: string | undefined, columns: string[]): ColumnOrderStorageKeys {
  const path = typeof window === "undefined" ? "" : window.location.pathname;
  const titlePart = (title ?? "").trim().toLowerCase() || "auto";
  const primaryIdentity = JSON.stringify({
    anchorColumns: columns.slice(0, 8),
    path,
    title: titlePart,
  });
  const legacyIdentity = JSON.stringify({
    columns: [...columns].sort(),
    path,
    title: titlePart,
  });
  return {
    legacy: [`qrw.dataTable.columnOrder.${hashString(legacyIdentity)}`],
    primary: `qrw.dataTable.columnOrder.${hashString(primaryIdentity)}`,
  };
}

function applyColumnOrder(columns: string[], storedOrder: string[]) {
  if (!storedOrder.length) return columns;
  const availableColumns = new Set(columns);
  const orderedColumns = storedOrder.filter((column) => availableColumns.has(column));
  const orderedSet = new Set(orderedColumns);
  return [...orderedColumns, ...columns.filter((column) => !orderedSet.has(column))];
}

function normalizeColumnOrder(order: string[], columns: string[]) {
  const availableColumns = new Set(columns);
  const normalized = Array.from(new Set(order)).filter((column) => availableColumns.has(column));
  const normalizedSet = new Set(normalized);
  return [...normalized, ...columns.filter((column) => !normalizedSet.has(column))];
}

function readColumnOrderPreference(storageKeys: ColumnOrderStorageKeys, columns: string[]) {
  if (typeof window === "undefined") return [];
  for (const storageKey of [storageKeys.primary, ...storageKeys.legacy]) {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(storageKey) ?? "[]");
      const normalized = Array.isArray(parsed)
        ? normalizeColumnOrder(parsed.filter((item): item is string => typeof item === "string"), columns)
        : [];
      if (normalized.length) {
        if (storageKey !== storageKeys.primary) {
          writeColumnOrderPreference(storageKeys.primary, normalized);
        }
        return normalized;
      }
    } catch {
      // Column order is a convenience preference; ignore malformed stored values.
    }
  }
  return [];
}

function writeColumnOrderPreference(storageKey: string, order: string[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(order));
  } catch {
    // Column order is a convenience preference; ignore storage failures.
  }
}

function removeColumnOrderPreference(storageKeys: ColumnOrderStorageKeys) {
  if (typeof window === "undefined") return;
  for (const storageKey of [storageKeys.primary, ...storageKeys.legacy]) {
    try {
      window.localStorage.removeItem(storageKey);
    } catch {
      // Column order is a convenience preference; ignore storage failures.
    }
  }
}

function hashString(value: string) {
  let hash = 5381;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) + hash) ^ value.charCodeAt(index);
  }
  return (hash >>> 0).toString(36);
}

function transposeSourceRowLabel(row: DataRow, index: number): string {
  const ticker = stringValue(row.ticker || row.symbol);
  const rank = stringValue(row.entry_rank || row.rank);
  if (ticker && rank) return `${index + 1}. ${ticker} #${rank}`;
  if (ticker) return `${index + 1}. ${ticker}`;
  if (rank) return `Row ${index + 1} #${rank}`;
  return `Row ${index + 1}`;
}

function stringValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function keepExistingColumnFilters<T>(filters: Record<string, T>, validColumns: Set<string>): Record<string, T> {
  let changed = false;
  const next: Record<string, T> = {};
  Object.entries(filters).forEach(([column, filter]) => {
    if (validColumns.has(column)) {
      next[column] = filter;
    } else {
      changed = true;
    }
  });
  return changed ? next : filters;
}

function normalizeBackendQuery(query?: BackendTableQuery): BackendTableQuery {
  return {
    conditions: (query?.conditions ?? []).map((condition) => ({
      column: condition.column,
      id: condition.id || nextBackendQueryConditionId(),
      operator: condition.operator || "contains",
      value: condition.value ?? "",
      valueSecondary: condition.valueSecondary ?? "",
    })),
    matchMode: query?.matchMode === "any" ? "any" : "all",
    sortColumn: query?.sortColumn,
    sortDirection: query?.sortDirection ?? "asc",
  };
}

function cleanBackendQuery(query: BackendTableQuery, columns: string[]): BackendTableQuery {
  const allowedColumns = new Set(columns);
  const conditions = normalizeBackendQuery(query).conditions.filter((condition) => {
    if (!allowedColumns.has(condition.column)) return false;
    const operator = BACKEND_QUERY_OPERATORS.find((item) => item.value === condition.operator);
    if (!operator) return false;
    if (!operator.needsValue) return true;
    if (!condition.value.trim()) return false;
    if (operator.needsSecondValue && !condition.valueSecondary?.trim()) return false;
    return true;
  });
  const sortColumn = query.sortColumn && allowedColumns.has(query.sortColumn) ? query.sortColumn : undefined;
  return {
    conditions,
    matchMode: query.matchMode === "any" ? "any" : "all",
    sortColumn,
    sortDirection: query.sortDirection ?? "asc",
  };
}

function countBackendQueryClauses(query: BackendTableQuery): number {
  const cleaned = normalizeBackendQuery(query);
  const conditions = cleaned.conditions.filter((condition) => {
    const operator = BACKEND_QUERY_OPERATORS.find((item) => item.value === condition.operator);
    if (!operator) return false;
    if (!operator.needsValue) return true;
    if (!condition.value.trim()) return false;
    if (operator.needsSecondValue && !condition.valueSecondary?.trim()) return false;
    return true;
  }).length;
  return conditions + (cleaned.sortColumn ? 1 : 0);
}

function backendQueryConditionChips(conditions: BackendQueryCondition[]) {
  return conditions
    .filter((condition) => condition.column && condition.operator)
    .map((condition) => ({
      column: condition.column,
      id: condition.id,
      summary: formatBackendConditionSummary(condition),
    }));
}

function formatBackendConditionSummary(condition: BackendQueryCondition) {
  if (condition.operator === "between") return `between ${condition.value} and ${condition.valueSecondary ?? ""}`;
  if (condition.operator === "is_null") return "is blank";
  if (condition.operator === "is_not_null") return "has value";
  return `${condition.operator.replaceAll("_", " ")} ${condition.value}`;
}

function buildBackendQueryCondition(columns: string[]): BackendQueryCondition {
  return {
    column: columns[0] ?? "",
    id: nextBackendQueryConditionId(),
    operator: "contains",
    value: "",
    valueSecondary: "",
  };
}

function nextBackendQueryConditionId(): string {
  backendQueryConditionSequence += 1;
  return `query-condition-${Date.now()}-${backendQueryConditionSequence}`;
}

function ColumnFilterPopover({
  activeFilterValues,
  manualFilter,
  onApplyManualFilter,
  onApplyValueFilter,
  onHideColumn,
  profile,
}: {
  activeFilterValues: string[];
  manualFilter: ColumnManualFilterState | null;
  onApplyManualFilter: (filter: ColumnManualFilterState | null) => void;
  onApplyValueFilter: (values: string[]) => void;
  onHideColumn: () => void;
  profile: ColumnProfile;
}) {
  const [mode, setMode] = useState<"values" | "custom">(manualFilter ? "custom" : "values");
  const [searchText, setSearchText] = useState("");
  const [draft, setDraft] = useState<ColumnManualFilterState>(manualFilter ?? defaultManualFilter(profile));

  useEffect(() => {
    setDraft(manualFilter ?? defaultManualFilter(profile));
  }, [manualFilter, profile]);

  useEffect(() => {
    setMode(manualFilter ? "custom" : "values");
    setSearchText("");
  }, [manualFilter, profile.column]);

  const filteredOptions = profile.topValues.filter((option) =>
    searchText.trim()
      ? option.value.toLowerCase().includes(searchText.trim().toLowerCase())
      : true,
  );
  const presetFilters = buildPresetFilters(profile);
  const tone = resolveTableVisualTone(profile);
  const optionTotal = filteredOptions.reduce((total, option) => total + option.count, 0);

  return (
    <div className="table-popover-panel table-popover-divided">
      <div className="table-popover-header">
        <div>
          <div className="table-popover-heading">{displayName(profile.column)}</div>
          <div className="table-popover-subheading">{profile.typeLabel}</div>
        </div>
        <div className="table-popover-mode-switch">
          <button className={mode === "values" ? "active" : ""} onClick={() => setMode("values")} type="button">
            Values
          </button>
          <button className={mode === "custom" ? "active" : ""} onClick={() => setMode("custom")} type="button">
            Manual
          </button>
        </div>
      </div>

      {mode === "custom" ? (
        <div className="table-popover-section">
          <div className="table-popover-field-grid">
            <label className="table-popover-field">
              <span>Operator</span>
              <select
                className="table-popover-control"
                onChange={(event) =>
                  setDraft((current) => ({ ...current, operator: event.target.value, presetLabel: undefined }))
                }
                value={draft.operator}
              >
                {buildManualFilterOperators(profile.kind).map((operator) => (
                  <option key={operator} value={operator}>
                    {operator}
                  </option>
                ))}
              </select>
            </label>
            <label className="table-popover-field">
              <span>Value</span>
              <div className="table-popover-input-row">
                <input
                  className="table-popover-control"
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, presetLabel: undefined, valueText: event.target.value }))
                  }
                  placeholder={buildManualFilterPlaceholder(profile.kind)}
                  type={resolveManualFilterInputType(profile)}
                  value={draft.valueText}
                />
                {supportsTextCaseSensitivity(profile) ? (
                  <button
                    className={draft.caseSensitive ? "table-badge-toggle active" : "table-badge-toggle"}
                    onClick={() => setDraft((current) => ({ ...current, caseSensitive: !current.caseSensitive }))}
                    type="button"
                  >
                    Aa
                  </button>
                ) : null}
              </div>
            </label>
          </div>
          {profile.kind === "datetime" ? (
            <label className="table-popover-field">
              <span>Input timezone</span>
              <select
                className="table-popover-control"
                onChange={(event) => setDraft((current) => ({ ...current, timeZoneName: event.target.value }))}
                value={draft.timeZoneName}
              >
                <option value="UTC">UTC</option>
                <option value="America/New_York">America/New York</option>
                <option value="America/Vancouver">America/Vancouver</option>
              </select>
            </label>
          ) : null}
          {draft.operator === "between" ? (
            <label className="table-popover-field">
              <span>Second value</span>
              <input
                className="table-popover-control"
                onChange={(event) =>
                  setDraft((current) => ({ ...current, presetLabel: undefined, valueTextSecondary: event.target.value }))
                }
                placeholder={buildManualFilterPlaceholder(profile.kind)}
                type={resolveManualFilterInputType(profile)}
                value={draft.valueTextSecondary}
              />
            </label>
          ) : null}
          <div className="table-popover-action-row separated">
            <button className="table-text-button" onClick={() => onApplyManualFilter(null)} type="button">
              Clear
            </button>
            <button className="table-text-button" onClick={() => setMode("values")} type="button">
              Values
            </button>
            <button
              className="table-text-button primary"
              onClick={() => onApplyManualFilter(buildNormalizedManualFilter(draft))}
              type="button"
            >
              Apply filter
            </button>
          </div>
        </div>
      ) : (
        <div className="table-popover-divided">
          {presetFilters.length ? (
            <div className="table-popover-section">
              <div className="table-popover-section-title">Presets</div>
              <div className="table-option-chip-row">
                {presetFilters.map((preset) => (
                  <button
                    className={isPresetFilterActive(manualFilter, preset.filter) ? "table-option-chip active" : "table-option-chip"}
                    key={preset.label}
                    onClick={() => onApplyManualFilter(preset.filter)}
                    type="button"
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {profile.histogramBins.length ? (
            <div className="table-popover-section">
              <HistogramPanel histogramBins={profile.histogramBins} title="Distribution" tone={tone} />
            </div>
          ) : null}

          <div className="table-popover-section">
            <div className="table-popover-section-title">Value list</div>
            <input
              className="table-popover-control"
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="Filter values..."
              value={searchText}
            />
            {filteredOptions.length ? (
              <div className="table-overlay-list">
                {filteredOptions.map((option) => {
                  const selected = activeFilterValues.includes(option.value);
                  return (
                    <button
                      className="table-overlay-button"
                      key={option.value}
                      onClick={() =>
                        onApplyValueFilter(
                          selected
                            ? activeFilterValues.filter((value) => value !== option.value)
                            : [...activeFilterValues, option.value],
                        )
                      }
                      type="button"
                    >
                      <OverlayBar
                        emphasized={selected}
                        label={option.value}
                        tone={tone}
                        totalValue={optionTotal}
                        value={option.count}
                      />
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="table-popover-empty">No current value list is available for this column.</div>
            )}
          </div>

          <div className="table-popover-section">
            <div className="table-popover-action-row">
              <button
                className="table-text-button"
                disabled={activeFilterValues.length === 0 && manualFilter === null}
                onClick={() => {
                  onApplyValueFilter([]);
                  onApplyManualFilter(null);
                }}
                type="button"
              >
                Clear
              </button>
              <button className="table-text-button" onClick={() => setMode("custom")} type="button">
                Manual filter
              </button>
              <button className="table-text-button" onClick={onHideColumn} type="button">
                <EyeOff size={13} />
                Hide column
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ColumnStatsPopover({ profile }: { profile: ColumnProfile }) {
  const hasBlankValues = profile.blankCount > 0;
  return (
    <div className="table-popover-panel stats table-popover-divided">
      <div className="table-popover-section">
        <div className="table-popover-title-block">
          <div className="table-popover-heading">{displayName(profile.column)}</div>
          <div className="table-popover-subheading">{profile.typeLabel}</div>
        </div>
        <div className="table-stat-pill-grid">
          <StatPill label="Non-null" tone="neutral" value={formatInteger(profile.nonEmpty)} />
          <StatPill label="Null" tone={hasBlankValues ? "warning" : "neutral"} value={formatInteger(profile.blankCount)} />
          <StatPill label="Distinct" tone="neutral" value={formatInteger(profile.distinct)} />
        </div>
      </div>
      {profile.kind === "numeric" || profile.kind === "datetime" ? (
        <div className="table-popover-section">
          <div className="table-stat-line-grid">
            {profile.kind === "numeric" ? (
              <>
                <StatLine label="Min" value={formatProfileMetric(profile.min)} />
                <StatLine label="P25" value={formatProfileMetric(profile.p25)} />
                <StatLine label="Median" value={formatProfileMetric(profile.median)} />
                <StatLine label="Average" value={formatProfileMetric(profile.average)} />
                <StatLine label="P75" value={formatProfileMetric(profile.p75)} />
                <StatLine label="Max" value={formatProfileMetric(profile.max)} />
                <StatLine label="Std. dev." value={formatProfileMetric(profile.stddev)} />
                <StatLine label="Total" value={formatProfileMetric(profile.total)} />
              </>
            ) : null}
            {profile.kind === "datetime" ? (
              <>
                <StatLine label="Earliest" value={formatDateTimeMetric(profile.min, profile.temporalUnit, profile.timeZoneName)} />
                <StatLine label="Latest" value={formatDateTimeMetric(profile.max, profile.temporalUnit, profile.timeZoneName)} />
              </>
            ) : null}
          </div>
        </div>
      ) : null}
      {profile.histogramBins.length ? (
        <div className="table-popover-section">
          <HistogramPanel histogramBins={profile.histogramBins} title="Distribution" tone={resolveTableVisualTone(profile)} />
        </div>
      ) : null}
      {profile.topValues.length ? (
        <div className="table-popover-section">
          <div className="table-popover-section-title">Top values</div>
          <div className="table-overlay-list compact">
            {profile.topValues.slice(0, 8).map((topValue) => (
              <OverlayBar
                emphasized={false}
                key={topValue.value}
                label={topValue.value}
                tone={resolveTableVisualTone(profile)}
                totalValue={profile.topValues.reduce((count, value) => count + value.count, 0)}
                value={topValue.count}
              />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function HistogramPanel({
  histogramBins,
  title,
  tone,
}: {
  histogramBins: HistogramBin[];
  title: string;
  tone: TableVisualTone;
}) {
  const totalRowCount = histogramBins.reduce((currentTotal, histogramBin) => currentTotal + histogramBin.count, 0);
  return (
    <div>
      <div className="table-popover-section-title">{title}</div>
      <div className="table-overlay-list compact">
        {histogramBins.map((histogramBin) => (
          <OverlayBar
            emphasized={false}
            key={`${histogramBin.label}:${histogramBin.count}`}
            label={histogramBin.label}
            tone={tone}
            totalValue={totalRowCount}
            value={histogramBin.count}
          />
        ))}
      </div>
    </div>
  );
}

function OverlayBar({
  emphasized = false,
  label,
  tone,
  totalValue,
  value,
}: {
  emphasized?: boolean;
  label: string;
  tone: TableVisualTone;
  totalValue: number;
  value: number;
}) {
  return (
    <div className={`table-overlay-row ${tone} ${emphasized ? "emphasized" : ""}`}>
      <span className="table-overlay-label">{label}</span>
      <div className="table-overlay-track">
        <span className="table-overlay-fill" style={{ width: `${buildHistogramBarWidth(value, totalValue)}%` }} />
        <span className="table-overlay-value">{formatInteger(value)}</span>
      </div>
    </div>
  );
}

function StatPill({ label, tone, value }: { label: string; tone: "neutral" | "warning"; value: string }) {
  return (
    <div className={tone === "warning" ? "table-stat-pill warning" : "table-stat-pill"}>
      <div>{label}</div>
      <b>{value}</b>
    </div>
  );
}

function StatLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="table-stat-line">
      <div>{label}</div>
      <b>{value}</b>
    </div>
  );
}

function buildColumnWidthsByName({
  densityMode,
  layoutMode,
  rows,
  visibleColumns,
}: {
  densityMode: TableDensityMode;
  layoutMode: TableLayoutMode;
  rows: DataRow[];
  visibleColumns: string[];
}) {
  return Object.fromEntries(
    visibleColumns.map((column) => {
      const headerWidth = estimateHeaderColumnWidth(column, densityMode);
      if (layoutMode === "fit_header") return [column, headerWidth];
      const dataWidth = estimateDataColumnWidth(column, rows, densityMode);
      return [column, Math.max(headerWidth, dataWidth)];
    }),
  );
}

function estimateHeaderColumnWidth(column: string, densityMode: TableDensityMode) {
  const label = columnHeaderLabel(column, densityMode);
  const chromeWidth = densityMode === "compact" ? 42 : 74;
  const minWidth = densityMode === "compact" ? 72 : 108;
  const maxWidth = densityMode === "compact" ? 180 : 260;
  return clamp(estimateTextWidth(label) + chromeWidth, minWidth, maxWidth);
}

function estimateDataColumnWidth(column: string, rows: DataRow[], densityMode: TableDensityMode) {
  const sampledRows = rows.slice(0, 80);
  const maxTextWidth = sampledRows.reduce((currentMax, row) => {
    return Math.max(currentMax, estimateTextWidth(formatCell(column, row[column])));
  }, 0);
  const padding = densityMode === "compact" ? 18 : 28;
  const minWidth = densityMode === "compact" ? 64 : 108;
  const maxWidth = densityMode === "compact" ? 360 : 460;
  return clamp(maxTextWidth + padding, minWidth, maxWidth);
}

function columnHeaderLabel(column: string, densityMode: TableDensityMode) {
  const fullName = displayName(column);
  if (densityMode !== "compact") return fullName;
  const direct = COMPACT_COLUMN_LABELS[column];
  if (direct) return direct;
  return fullName
    .replace(/\bCurrent\b/g, "Cur")
    .replace(/\bTransactions\b/g, "Tx")
    .replace(/\bTransaction\b/g, "Tx")
    .replace(/\bVolume\b/g, "Vol")
    .replace(/\bDivergence\b/g, "Div")
    .replace(/\bBearish\b/g, "Bear")
    .replace(/\bDouble Timeframe\b/g, "2x")
    .replace(/\bDay\b/g, "Day")
    .replace(/\bSo Far\b/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

const COMPACT_COLUMN_LABELS: Record<string, string> = {
  current_open: "Open",
  last_bearish_volume_divergence_score: "BVD",
  last_close: "Close",
  last_day_current_change_pct: "Cur Chg",
  last_day_dollar_volume_so_far: "$Vol",
  last_day_high_so_far: "Day High",
  last_day_low_so_far: "Day Low",
  last_day_max_change_pct: "Max Chg",
  last_day_open: "Day Open",
  last_day_volume_so_far: "Day Vol",
  last_double_timeframe_bearish_volume_divergence_score: "2x BVD",
  last_gap_pct: "Gap",
  last_return_5: "5m Ret",
  last_transactions: "Tx",
  last_transactions_vs_prior_3: "Tx/3",
  last_volume: "Vol",
  last_vwap: "VWAP",
  live_bias: "Bias",
  live_reasons: "Reasons",
  live_risks: "Risks",
  live_signal_query: "Query",
  live_signal_time: "Signal",
  open_vs_vwap_pct: "Open/VWAP",
  spread_bps_abs: "Spread",
};

function estimateTextWidth(value: string) {
  return value.split("").reduce((width, character) => {
    if (character === " ") return width + 4;
    if ("MW@#%&".includes(character)) return width + 9;
    if ("il.,'|".includes(character)) return width + 3.5;
    if (character === character.toUpperCase() && character !== character.toLowerCase()) return width + 7.5;
    return width + 6.5;
  }, 0);
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function buildColumnProfile(rows: DataRow[], column: string): ColumnProfile {
  const allValues = rows.map((row) => row[column]);
  const values = allValues.filter((value) => !isBlank(value));
  const topValues = countTopValues(allValues);
  const distinct = values.length ? new Set(values.map(formatFilterValue)).size : 0;
  const numericValues = values.map(coerceNumber).filter((value): value is number => Number.isFinite(value));
  const booleanValues = values.filter((value) => typeof value === "boolean");
  const dateValues = values.map((value) => coerceDate(value, looksLikeTimeColumn(column))).filter((value): value is number => Number.isFinite(value));
  const blankCount = rows.length - values.length;

  if (values.length && isDateTimeProfile(column, values.length, dateValues.length)) {
    const sorted = [...dateValues].sort((left, right) => left - right);
    const temporalUnit = inferTemporalUnit(column, values);
    const timeZoneName = temporalUnit === "datetime" ? inferTimeZoneName(column) : "UTC";
    return {
      ...baseProfile({ blankCount, column, distinct, kind: "datetime", rows, topValues, values }),
      histogramBins: buildDatetimeHistogram(sorted, temporalUnit, timeZoneName),
      max: new Date(sorted[sorted.length - 1]).toISOString(),
      min: new Date(sorted[0]).toISOString(),
      temporalUnit,
      timeZoneName,
      typeLabel: temporalUnit === "date" ? "date" : `date/time${formatTimeZoneLabel(timeZoneName)}`,
    };
  }

  if (values.length && numericValues.length === values.length) {
    const sorted = [...numericValues].sort((left, right) => left - right);
    const total = numericValues.reduce((sum, value) => sum + value, 0);
    const average = total / numericValues.length;
    const variance =
      numericValues.reduce((sum, value) => sum + Math.pow(value - average, 2), 0) / Math.max(numericValues.length, 1);
    return {
      average,
      blankCount,
      column,
      distinct,
      histogramBins: buildNumericHistogram(sorted),
      kind: "numeric",
      max: sorted[sorted.length - 1],
      median: percentile(sorted, 0.5),
      min: sorted[0],
      nonEmpty: values.length,
      p25: percentile(sorted, 0.25),
      p75: percentile(sorted, 0.75),
      stddev: Math.sqrt(variance),
      topValues,
      total,
      totalRows: rows.length,
      typeLabel: "numeric",
    };
  }

  if (values.length && booleanValues.length === values.length) {
    return baseProfile({ blankCount, column, distinct, kind: "boolean", rows, topValues, values });
  }

  return baseProfile({
    blankCount,
    column,
    distinct,
    kind: distinct <= Math.max(20, rows.length * 0.15) ? "categorical" : "text",
    rows,
    topValues,
    values,
  });
}

function baseProfile({
  blankCount,
  column,
  distinct,
  kind,
  rows,
  topValues,
  values,
}: {
  blankCount: number;
  column: string;
  distinct: number;
  kind: ColumnKind;
  rows: DataRow[];
  topValues: ValueCount[];
  values: unknown[];
}): ColumnProfile {
  return {
    blankCount,
    column,
    distinct,
    histogramBins: [],
    kind,
    nonEmpty: values.length,
    topValues,
    totalRows: rows.length,
    typeLabel: kind,
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

function buildTableIdentityKey(rows: DataRow[], columns: string[]) {
  const sampledColumns = columns.slice(0, 16);
  const rowCount = rows.length;
  const candidateIndexes = [0, 1, 2, Math.floor(rowCount / 2), rowCount - 3, rowCount - 2, rowCount - 1];
  const sampledIndexes = Array.from(new Set(candidateIndexes)).filter((index) => index >= 0 && index < rowCount);
  const sample = sampledIndexes.map((rowIndex) =>
    sampledColumns.map((column) => formatFilterValue(rows[rowIndex]?.[column])),
  );
  return JSON.stringify({ columns: sampledColumns, rowCount, sample });
}

function formatValueFilterSummary(values: string[]) {
  const preview = values.slice(0, 2).join(", ");
  return values.length <= 2 ? preview : `${preview} +${values.length - 2}`;
}

function formatManualFilterSummary(filter: ColumnManualFilterState, profile?: ColumnProfile) {
  if (filter.presetLabel) return filter.presetLabel;
  if (filter.operator === "is_null") return "is empty";
  if (filter.operator === "not_null") return "has value";
  const operatorLabel = formatFilterOperator(filter.operator);
  const value = formatManualFilterValue(filter.valueText, profile);
  if (filter.operator === "between") {
    return `${operatorLabel} ${value} and ${formatManualFilterValue(filter.valueTextSecondary, profile)}`;
  }
  return `${operatorLabel} ${value}`;
}

function formatFilterOperator(operator: string) {
  if (operator === "gte") return ">=";
  if (operator === "lte") return "<=";
  if (operator === "gt") return ">";
  if (operator === "lt") return "<";
  if (operator === "eq") return "=";
  if (operator === "neq") return "!=";
  if (operator === "contains") return "contains";
  if (operator === "between") return "between";
  return operator;
}

function formatManualFilterValue(value: string, profile?: ColumnProfile) {
  if (!value) return "-";
  if (profile?.kind === "datetime") {
    const parsed = coerceDate(value);
    if (Number.isFinite(parsed)) return formatDateTimeMetric(parsed, profile.temporalUnit, profile.timeZoneName);
  }
  return value;
}

function compareCellsForSort(left: unknown, right: unknown, direction: SortDirection) {
  const leftComparable = comparableValue(left);
  const rightComparable = comparableValue(right);
  if (leftComparable === rightComparable) return 0;
  if (leftComparable === null) return 1;
  if (rightComparable === null) return -1;
  const result =
    typeof leftComparable === "number" && typeof rightComparable === "number"
      ? leftComparable - rightComparable
      : String(leftComparable).localeCompare(String(rightComparable), undefined, { numeric: true, sensitivity: "base" });
  return direction === "asc" ? result : -result;
}

function comparableValue(value: unknown): number | string | null {
  if (isBlank(value)) return null;
  const numeric = coerceNumber(value);
  if (Number.isFinite(numeric)) return numeric;
  const date = coerceDate(value);
  if (Number.isFinite(date)) return date;
  return String(value);
}

function coerceDate(value: unknown, allowNumericTimestamp = false) {
  if (value instanceof Date) return value.getTime();
  if (allowNumericTimestamp && typeof value === "number") return coerceNumericTimestamp(value);
  if (typeof value !== "string") return Number.NaN;
  if (allowNumericTimestamp && /^-?\d+(?:\.\d+)?$/.test(value.trim())) return coerceNumericTimestamp(Number(value));
  if (!/\d{4}-\d{2}-\d{2}/.test(value)) return Number.NaN;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

function coerceNumericTimestamp(value: number) {
  const magnitude = Math.abs(value);
  if (!Number.isFinite(value) || magnitude < 1_000_000_000) return Number.NaN;
  if (magnitude > 100_000_000_000_000_000) return value / 1_000_000;
  if (magnitude > 100_000_000_000_000) return value / 1_000;
  if (magnitude > 100_000_000_000) return value;
  return value * 1_000;
}

function coerceNumber(value: unknown) {
  if (typeof value === "number") return value;
  if (typeof value !== "string") return Number.NaN;
  const normalized = value.replace(/,/g, "").trim();
  if (!normalized) return Number.NaN;
  return Number(normalized);
}

function rowMatchesManualFilter(value: unknown, profile: ColumnProfile | undefined, filter: ColumnManualFilterState) {
  if (filter.operator === "is_null") return isBlank(value);
  if (filter.operator === "not_null") return !isBlank(value);
  if (isBlank(value) || !profile) return false;

  if (profile.kind === "numeric") {
    return compareManualNumbers(coerceNumber(value), filter);
  }
  if (profile.kind === "datetime") {
    return compareManualDates(coerceDate(value, looksLikeTimeColumn(profile.column)), filter);
  }
  return compareManualText(value, filter);
}

function compareManualNumbers(value: number, filter: ColumnManualFilterState) {
  if (!Number.isFinite(value)) return false;
  const target = coerceNumber(filter.valueText);
  const secondary = coerceNumber(filter.valueTextSecondary);
  if (filter.operator === "between") return Number.isFinite(target) && Number.isFinite(secondary) && value >= target && value <= secondary;
  if (!Number.isFinite(target)) return false;
  if (filter.operator === "gte") return value >= target;
  if (filter.operator === "lte") return value <= target;
  if (filter.operator === "gt") return value > target;
  if (filter.operator === "lt") return value < target;
  if (filter.operator === "eq") return value === target;
  if (filter.operator === "neq") return value !== target;
  return false;
}

function compareManualDates(value: number, filter: ColumnManualFilterState) {
  if (!Number.isFinite(value)) return false;
  const target = coerceDate(filter.valueText);
  const secondary = coerceDate(filter.valueTextSecondary);
  if (filter.operator === "between") return Number.isFinite(target) && Number.isFinite(secondary) && value >= target && value <= secondary;
  if (!Number.isFinite(target)) return false;
  if (filter.operator === "gte") return value >= target;
  if (filter.operator === "lte") return value <= target;
  if (filter.operator === "gt") return value > target;
  if (filter.operator === "lt") return value < target;
  if (filter.operator === "eq") return value === target;
  if (filter.operator === "neq") return value !== target;
  return false;
}

function compareManualText(value: unknown, filter: ColumnManualFilterState) {
  const source = filter.caseSensitive ? String(value) : String(value).toLowerCase();
  const target = filter.caseSensitive ? filter.valueText : filter.valueText.toLowerCase();
  if (filter.operator === "contains") return source.includes(target);
  if (filter.operator === "eq") return source === target;
  if (filter.operator === "neq") return source !== target;
  return false;
}

function defaultManualFilter(profile: ColumnProfile): ColumnManualFilterState {
  return {
    caseSensitive: false,
    operator: buildDefaultManualFilterOperator(profile.kind),
    timeZoneName: profile.timeZoneName ?? "UTC",
    valueText: "",
    valueTextSecondary: "",
  };
}

function buildManualFilterOperators(kind: ColumnKind) {
  if (kind === "numeric" || kind === "datetime") {
    return ["between", "gte", "lte", "gt", "lt", "eq", "neq", "is_null", "not_null"];
  }
  if (kind === "boolean" || kind === "categorical") {
    return ["eq", "neq", "contains", "is_null", "not_null"];
  }
  return ["contains", "eq", "neq", "is_null", "not_null"];
}

function buildDefaultManualFilterOperator(kind: ColumnKind) {
  return kind === "numeric" || kind === "datetime" ? "between" : "contains";
}

function buildManualFilterPlaceholder(kind: ColumnKind) {
  if (kind === "datetime") return "Select a date or time";
  if (kind === "numeric") return "Enter a number";
  return "Enter a value";
}

function resolveManualFilterInputType(profile: ColumnProfile): "date" | "datetime-local" | "number" | "text" {
  if (profile.kind === "numeric") return "number";
  if (profile.kind === "datetime") {
    return profile.temporalUnit === "date" ? "date" : "datetime-local";
  }
  return "text";
}

function buildPresetFilters(profile: ColumnProfile) {
  if (profile.kind === "numeric") {
    return [
      { label: "Positive", filter: presetFilter("Positive", "gt", "0") },
      { label: "Negative", filter: presetFilter("Negative", "lt", "0") },
      { label: "Zero", filter: presetFilter("Zero", "eq", "0") },
      ...(profile.p75 === undefined ? [] : [{ label: "Top quartile", filter: presetFilter("Top quartile", "gte", String(profile.p75)) }]),
      ...(profile.p25 === undefined ? [] : [{ label: "Bottom quartile", filter: presetFilter("Bottom quartile", "lte", String(profile.p25)) }]),
    ];
  }
  if (profile.kind === "datetime") {
    const today = formatDateValue(new Date());
    const tomorrow = formatDateValue(addDays(new Date(), 1));
    const yesterday = formatDateValue(addDays(new Date(), -1));
    const lastWeek = formatDateValue(addDays(new Date(), -6));
    const lastMonth = formatDateValue(addDays(new Date(), -29));
    const startOfWeek = formatDateValue(addDays(new Date(), -((new Date().getDay() + 6) % 7)));
    const startOfMonth = formatDateValue(new Date(new Date().getFullYear(), new Date().getMonth(), 1));
    const startOfYear = formatDateValue(new Date(new Date().getFullYear(), 0, 1));
    return [
      { label: "Today", filter: presetFilter("Today", "between", today, tomorrow) },
      { label: "From today", filter: presetFilter("From today", "gte", today) },
      { label: "Yesterday", filter: presetFilter("Yesterday", "between", yesterday, today) },
      { label: "This week", filter: presetFilter("This week", "between", startOfWeek, tomorrow) },
      { label: "Last 7 days", filter: presetFilter("Last 7 days", "between", lastWeek, tomorrow) },
      { label: "Last 30 days", filter: presetFilter("Last 30 days", "between", lastMonth, tomorrow) },
      { label: "This month", filter: presetFilter("This month", "between", startOfMonth, tomorrow) },
      { label: "Year to date", filter: presetFilter("Year to date", "between", startOfYear, tomorrow) },
    ];
  }
  if (profile.kind === "boolean") {
    return [
      { label: "Is true", filter: presetFilter("Is true", "eq", "true") },
      { label: "Is false", filter: presetFilter("Is false", "eq", "false") },
      { label: "Has value", filter: presetFilter("Has value", "not_null") },
    ];
  }
  return [
    { label: "Has value", filter: presetFilter("Has value", "not_null") },
    { label: "Is empty", filter: presetFilter("Is empty", "is_null") },
  ];
}

function presetFilter(label: string, operator: string, valueText = "", valueTextSecondary = ""): ColumnManualFilterState {
  return {
    caseSensitive: false,
    operator,
    presetLabel: label,
    timeZoneName: "UTC",
    valueText,
    valueTextSecondary,
  };
}

function normalizeManualFilterPreset(filter: DataTableManualFilterState): ColumnManualFilterState {
  return {
    caseSensitive: filter.caseSensitive ?? false,
    operator: filter.operator,
    presetLabel: filter.presetLabel,
    timeZoneName: filter.timeZoneName ?? "UTC",
    valueText: filter.valueText ?? "",
    valueTextSecondary: filter.valueTextSecondary ?? "",
  };
}

function filterPresetForColumns(preset: DataTableFilterPreset | undefined, columns: string[]): Record<string, ColumnManualFilterState> {
  if (!preset) return {};
  const validColumns = new Set(columns);
  return Object.fromEntries(
    Object.entries(preset.filters)
      .filter(([column]) => validColumns.has(column))
      .map(([column, filter]) => [column, normalizeManualFilterPreset(filter)]),
  );
}

function buildNormalizedManualFilter(draft: ColumnManualFilterState): ColumnManualFilterState | null {
  if (draft.operator === "is_null" || draft.operator === "not_null") {
    return { ...draft, valueText: "", valueTextSecondary: "" };
  }
  if (!draft.valueText.trim()) return null;
  if (draft.operator === "between" && !draft.valueTextSecondary.trim()) return null;
  return {
    ...draft,
    valueText: draft.valueText.trim(),
    valueTextSecondary: draft.valueTextSecondary.trim(),
  };
}

function isPresetFilterActive(manualFilter: ColumnManualFilterState | null, presetFilterValue: ColumnManualFilterState) {
  if (!manualFilter) return false;
  return (
    manualFilter.presetLabel === presetFilterValue.presetLabel &&
    manualFilter.operator === presetFilterValue.operator &&
    manualFilter.valueText === presetFilterValue.valueText &&
    manualFilter.valueTextSecondary === presetFilterValue.valueTextSecondary
  );
}

function supportsTextCaseSensitivity(profile: ColumnProfile) {
  return profile.kind === "text" || profile.kind === "categorical";
}

function buildNumericHistogram(sortedValues: number[]) {
  const formatter = sortedValues.every((value) => Number.isInteger(value))
    ? (value: number) => formatProfileMetric(Math.round(value))
    : formatProfileMetric;
  return buildStatisticalHistogram(sortedValues, formatter);
}

function buildDatetimeHistogram(sortedValues: number[], temporalUnit: ColumnProfile["temporalUnit"], timeZoneName?: string) {
  return buildStatisticalHistogram(sortedValues, (value) => formatDateTimeMetric(value, temporalUnit, timeZoneName));
}

function buildStatisticalHistogram(sortedValues: number[], formatter: (value: number) => string) {
  const values = sortedValues.filter((value) => Number.isFinite(value));
  if (!values.length) return [];
  const uniqueCount = countUniqueSorted(values);
  if (uniqueCount <= 10) return buildExactValueHistogram(values, formatter);

  const stats = buildDistributionStats(values);
  const fdEdges = buildFreedmanDiaconisEdges(stats);
  const fdBins = buildHistogramBinsFromEdges(values, fdEdges, formatter);
  if (!shouldUseQuantileBins(stats, fdBins, fdEdges.length - 1)) return fdBins;

  const quantileEdges = buildQuantileEdges(values, clampInteger(Math.ceil(Math.log2(values.length)) + 1, 4, 10));
  const quantileBins = buildHistogramBinsFromEdges(values, quantileEdges, formatter);
  return quantileBins.length > 1 ? quantileBins : fdBins;
}

function buildDistributionStats(sortedValues: number[]) {
  const min = sortedValues[0];
  const max = sortedValues[sortedValues.length - 1];
  const q1 = percentile(sortedValues, 0.25) ?? min;
  const median = percentile(sortedValues, 0.5) ?? min;
  const q3 = percentile(sortedValues, 0.75) ?? max;
  return {
    iqr: q3 - q1,
    max,
    median,
    min,
    q1,
    q3,
    range: max - min,
    rowCount: sortedValues.length,
  };
}

function buildFreedmanDiaconisEdges(stats: ReturnType<typeof buildDistributionStats>) {
  if (stats.range <= 0) return [stats.min, stats.max];
  const fdWidth = stats.iqr > 0 ? (2 * stats.iqr) / Math.cbrt(stats.rowCount) : Number.NaN;
  const fdBinCount = Number.isFinite(fdWidth) && fdWidth > 0 ? Math.ceil(stats.range / fdWidth) : 0;
  const sturgesBinCount = Math.ceil(Math.log2(stats.rowCount)) + 1;
  const binCount = clampInteger(fdBinCount || sturgesBinCount, 4, 10);
  return buildEqualWidthEdges(stats.min, stats.max, binCount);
}

function shouldUseQuantileBins(
  stats: ReturnType<typeof buildDistributionStats>,
  bins: HistogramBin[],
  candidateBinCount: number,
) {
  if (stats.rowCount < 20 || stats.iqr <= 0 || stats.range <= 0 || candidateBinCount <= 1) return false;
  const largestBinShare = Math.max(...bins.map((bin) => bin.count)) / stats.rowCount;
  const nonEmptyBinShare = bins.length / candidateBinCount;
  const iqrRangeShare = stats.iqr / stats.range;
  const robustSkew = (stats.q3 + stats.q1 - 2 * stats.median) / stats.iqr;
  return largestBinShare > 0.55 || nonEmptyBinShare < 0.65 || iqrRangeShare < 0.08 || Math.abs(robustSkew) > 0.65;
}

function buildEqualWidthEdges(min: number, max: number, binCount: number) {
  const width = (max - min) / binCount;
  return Array.from({ length: binCount + 1 }, (_, index) => (index === binCount ? max : min + width * index));
}

function buildQuantileEdges(sortedValues: number[], binCount: number) {
  const min = sortedValues[0];
  const max = sortedValues[sortedValues.length - 1];
  const edges = [min];
  for (let index = 1; index < binCount; index += 1) {
    const edge = percentile(sortedValues, index / binCount);
    if (edge !== undefined && edge > edges[edges.length - 1]) edges.push(edge);
  }
  if (max > edges[edges.length - 1]) edges.push(max);
  return edges.length > 2 ? edges : buildEqualWidthEdges(min, max, Math.min(4, binCount));
}

function buildHistogramBinsFromEdges(
  sortedValues: number[],
  edges: number[],
  formatter: (value: number) => string,
) {
  if (edges.length < 2) return buildExactValueHistogram(sortedValues, formatter);
  const bins: HistogramBin[] = [];
  let cursor = 0;
  for (let index = 0; index < edges.length - 1; index += 1) {
    const start = edges[index];
    const end = edges[index + 1];
    const startIndex = cursor;
    while (
      cursor < sortedValues.length &&
      (index === edges.length - 2 ? sortedValues[cursor] <= end : sortedValues[cursor] < end)
    ) {
      cursor += 1;
    }
    const count = cursor - startIndex;
    if (count > 0) bins.push({ count, label: formatDistributionRange(start, end, formatter) });
  }
  return bins;
}

function buildExactValueHistogram(sortedValues: number[], formatter: (value: number) => string) {
  const bins: HistogramBin[] = [];
  let currentValue = sortedValues[0];
  let currentCount = 0;
  sortedValues.forEach((value) => {
    if (value !== currentValue) {
      bins.push({ count: currentCount, label: formatter(currentValue) });
      currentValue = value;
      currentCount = 0;
    }
    currentCount += 1;
  });
  bins.push({ count: currentCount, label: formatter(currentValue) });
  return bins;
}

function countUniqueSorted(sortedValues: number[]) {
  return sortedValues.reduce((count, value, index) => count + (index === 0 || value !== sortedValues[index - 1] ? 1 : 0), 0);
}

function clampInteger(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, Math.round(value)));
}

function formatDistributionRange(start: number, end: number, formatter: (value: number) => string) {
  if (start === end) return formatter(start);
  return `${formatter(start)} - ${formatter(end)}`;
}

function percentile(sortedValues: number[], p: number) {
  if (!sortedValues.length) return undefined;
  const index = (sortedValues.length - 1) * p;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return sortedValues[lower];
  const weight = index - lower;
  return sortedValues[lower] * (1 - weight) + sortedValues[upper] * weight;
}

function buildHistogramBarWidth(rowCount: number, totalRowCount: number) {
  if (totalRowCount <= 0) return 0;
  return Math.max(2, (rowCount / totalRowCount) * 100);
}

function resolveTableVisualTone(profile: ColumnProfile): TableVisualTone {
  if (profile.kind === "numeric") return "sky";
  if (profile.kind === "datetime") return "amber";
  if (profile.kind === "boolean") return "violet";
  if (profile.kind === "categorical") return "emerald";
  return "neutral";
}

function isBlank(value: unknown) {
  return value === null || value === undefined || value === "";
}

function looksLikeTimeColumn(column: string) {
  const normalized = column.toLowerCase();
  return normalized.includes("date") || normalized.includes("time") || normalized.includes("timestamp") || normalized === "window_start" || normalized === "window_end" || normalized.endsWith("_at");
}

function isDateTimeProfile(column: string, valueCount: number, dateValueCount: number) {
  if (!dateValueCount) return false;
  if (looksLikeTimeColumn(column)) return dateValueCount >= Math.ceil(valueCount * 0.8);
  return dateValueCount === valueCount;
}

function inferTemporalUnit(column: string, values: unknown[]): "date" | "datetime" {
  return values.some((value) => value instanceof Date || typeof value === "number" || (looksLikeTimeColumn(column) && typeof value === "string" && /^-?\d+(?:\.\d+)?$/.test(value.trim())) || (typeof value === "string" && /(?:T|\s)\d{1,2}:\d{2}/.test(value))) ? "datetime" : "date";
}

function inferTimeZoneName(column: string) {
  const normalized = column.toLowerCase();
  if (normalized.includes("market")) return "America/New_York";
  if (normalized.includes("utc") || normalized === "window_start" || normalized === "window_end" || normalized.includes("timestamp")) return "UTC";
  return undefined;
}

function formatTimeZoneLabel(timeZoneName?: string) {
  if (timeZoneName === "UTC") return " UTC";
  if (timeZoneName === "America/New_York") return " ET";
  return "";
}

function formatFilterValue(value: unknown) {
  if (isBlank(value)) return "(blank)";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function formatInteger(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}

function formatProfileMetric(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? value.toLocaleString(undefined, { maximumFractionDigits: 0 })
      : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  return String(value);
}

function formatDateTimeMetric(value: unknown, temporalUnit: ColumnProfile["temporalUnit"] = "datetime", timeZoneName?: string) {
  const timestamp = typeof value === "number" ? value : coerceDate(value);
  if (!Number.isFinite(timestamp)) return formatProfileMetric(value);
  const timeZone = temporalUnit === "date" ? "UTC" : timeZoneName;
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    ...(temporalUnit === "datetime" ? { hour: "2-digit" as const, minute: "2-digit" as const } : {}),
    ...(timeZone ? { timeZone } : {}),
    year: "numeric",
  }).format(new Date(timestamp));
}

function addDays(value: Date, dayOffset: number) {
  const nextValue = new Date(value);
  nextValue.setDate(nextValue.getDate() + dayOffset);
  return nextValue;
}

function formatDateValue(value: Date) {
  const year = value.getFullYear();
  const month = `${value.getMonth() + 1}`.padStart(2, "0");
  const day = `${value.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
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
