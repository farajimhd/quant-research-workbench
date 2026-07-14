import {
  BarChart3,
  BriefcaseBusiness,
  Building2,
  FileSearch,
  ListChecks,
  Newspaper,
  PanelTopOpen,
  RefreshCcw,
  ScanSearch,
  ScrollText,
  Settings2,
  ShoppingCart,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import type { CanvasWorkspaceState } from "../canvasWorkspace";
import {
  containersForMode,
  defaultContainersForMode,
  sourceBindingForContainer,
  type TradingWorkspaceMode,
  type WorkspaceContainerDefinition,
  type WorkspaceContainerId,
} from "../tradingWorkspace";
import {
  WorkspaceWindow,
  workspaceMinHeight,
  type WorkspaceCanvasTarget,
  type WorkspaceWindowLayout,
  type WorkspaceWindowMeta,
} from "./WorkspaceCanvas";

type TradingWorkspaceProps = {
  clockLabel: string;
  canPopOut?: boolean;
  canvasTargets?: WorkspaceCanvasTarget[];
  compact?: boolean;
  defaultOpenIds?: WorkspaceContainerId[];
  defaultStateOverride?: CanvasWorkspaceState | null;
  definitionsOverride?: readonly WorkspaceContainerDefinition[];
  historicalSourceReady: boolean;
  layoutPreset?: "focus" | "global" | "mode";
  linkLabelForContainer?: (definition: WorkspaceContainerDefinition) => string | undefined;
  metaForContainer?: (definition: WorkspaceContainerDefinition) => WorkspaceWindowMeta;
  mode: TradingWorkspaceMode;
  onMoveContainerToCanvas?: (id: WorkspaceContainerId, canvasId: string, layout: WorkspaceWindowLayout) => void;
  onPopOutContainer?: (id: WorkspaceContainerId, layout: WorkspaceWindowLayout) => void;
  onStateChange?: (state: CanvasWorkspaceState) => void;
  renderContainer?: (definition: WorkspaceContainerDefinition) => ReactNode;
  runLabel: string;
  runStatus: "completed" | "idle" | "running" | "unavailable";
  sourceLabel?: string;
  showHealth?: boolean;
  statusLabel?: string;
  storageKeyOverride?: string;
  workspaceBadge?: string;
};

const DEFAULT_CANVAS_TARGETS: WorkspaceCanvasTarget[] = [{ color: "var(--primary)", id: "main", isCurrent: true, label: "Main" }];
export const TRADING_WORKSPACE_LAYOUT_VERSION = 3;

export function TradingWorkspace({
  clockLabel,
  canPopOut = false,
  canvasTargets = DEFAULT_CANVAS_TARGETS,
  compact = false,
  defaultOpenIds,
  defaultStateOverride,
  definitionsOverride,
  historicalSourceReady,
  layoutPreset = "mode",
  linkLabelForContainer,
  metaForContainer,
  mode,
  onMoveContainerToCanvas,
  onPopOutContainer,
  onStateChange,
  renderContainer,
  runLabel,
  runStatus,
  sourceLabel = "QMD History",
  showHealth = true,
  statusLabel,
  storageKeyOverride,
  workspaceBadge,
}: TradingWorkspaceProps) {
  const definitions = useMemo(() => [...(definitionsOverride ?? containersForMode(mode))], [definitionsOverride, mode]);
  const definitionById = useMemo(() => new Map(definitions.map((definition) => [definition.id, definition])), [definitions]);
  const storageKey = storageKeyOverride ?? `quant-research-workbench.trading-workspace.${mode}`;
  const initial = useMemo(
    () => readWorkspaceState(storageKey, mode, definitions, defaultOpenIds, layoutPreset),
    [defaultOpenIds, definitions, layoutPreset, mode, storageKey],
  );
  const [openIds, setOpenIds] = useState<WorkspaceContainerId[]>(initial.openIds);
  const [layouts, setLayouts] = useState<Record<string, WorkspaceWindowLayout>>(initial.layouts);
  const [libraryOpen, setLibraryOpen] = useState(false);

  useEffect(() => {
    const state = { layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts, openIds };
    window.localStorage.setItem(storageKey, JSON.stringify(state));
    onStateChange?.(state);
  }, [layouts, onStateChange, openIds, storageKey]);

  useEffect(() => {
    const syncStoredState = (event: StorageEvent) => {
      if (event.key !== storageKey || !event.newValue) return;
      const next = parseWorkspaceState(event.newValue, definitions);
      if (!next) return;
      setOpenIds(next.openIds);
      setLayouts(next.layouts);
    };
    window.addEventListener("storage", syncStoredState);
    return () => window.removeEventListener("storage", syncStoredState);
  }, [definitions, storageKey]);

  function focusContainer(id: WorkspaceContainerId) {
    const highest = Math.max(0, ...Object.values(layouts).map((layout) => layout.z));
    setLayouts((current) => ({ ...current, [id]: { ...current[id], minimized: false, z: highest + 1 } }));
  }

  function addContainer(id: WorkspaceContainerId) {
    if (openIds.includes(id)) {
      focusContainer(id);
      setLibraryOpen(false);
      return;
    }
    const nextIds = [...openIds, id];
    setOpenIds(nextIds);
    setLayouts((current) => layoutPreset === "focus"
      ? createFocusLayouts(nextIds)
      : { ...current, [id]: createAddedLayout(current, openIds.length) });
    setLibraryOpen(false);
  }

  function updateLayout(id: string, patch: Partial<WorkspaceWindowLayout>) {
    setLayouts((current) => ({ ...current, [id]: { ...current[id], ...patch } }));
  }

  function resetLayout() {
    if (defaultStateOverride) {
      setOpenIds([...defaultStateOverride.openIds]);
      setLayouts(cloneLayouts(defaultStateOverride.layouts));
      return;
    }
    const nextIds = defaultOpenIds ?? defaultContainersForMode(mode);
    setOpenIds(nextIds);
    setLayouts(createLayoutsForPreset(layoutPreset, mode, nextIds));
  }

  function resetContainer(id: WorkspaceContainerId) {
    const defaultLayout = defaultStateOverride?.layouts[id]
      ?? createLayoutsForPreset(layoutPreset, mode, [id])[id]
      ?? createAddedLayout({}, 0, layoutPreset === "focus");
    setOpenIds((current) => current.includes(id) ? current : [...current, id]);
    setLayouts((current) => ({ ...current, [id]: { ...defaultLayout, z: Math.max(1, defaultLayout.z) } }));
  }

  function moveContainer(id: WorkspaceContainerId, canvasId: string) {
    const layout = layouts[id];
    if (!layout || !onMoveContainerToCanvas) return;
    onMoveContainerToCanvas(id, canvasId, layout);
    setOpenIds((current) => current.filter((candidate) => candidate !== id));
  }

  const minHeight = workspaceMinHeight(openIds, layouts, compact);

  return (
    <div className="trading-workspace-shell" data-library-open={libraryOpen ? "true" : "false"} data-workspace-mode={mode}>
      <section className="trading-workspace-command" aria-label="Workspace context and controls">
        <div className="trading-workspace-identity">
          <span className="trading-mode-badge" data-mode={mode}>{workspaceBadge ?? modeLabel(mode)}</span>
          <div>
            <strong>{runLabel}</strong>
            <small>{clockLabel}</small>
          </div>
        </div>
        {showHealth ? <div className="trading-workspace-health">
          <span className="workspace-health-item" data-status={historicalSourceReady ? "ready" : "error"}>
            <i aria-hidden="true" /> {sourceLabel} {historicalSourceReady ? "ready" : "offline"}
          </span>
          <span className="workspace-health-item" data-status={runStatus === "running" ? "ready" : "idle"}>
            <i aria-hidden="true" /> {statusLabel ?? (runStatus === "running" ? "Run active" : "No active run")}
          </span>
        </div> : null}
        <div className="trading-workspace-actions">
          <button className="button secondary compact" onClick={() => setLibraryOpen((value) => !value)} type="button">
            {libraryOpen ? <X size={14} /> : <PanelTopOpen size={14} />} Containers
          </button>
          <button className="button secondary compact" onClick={resetLayout} type="button">
            <RefreshCcw size={14} /> Reset layout
          </button>
        </div>
      </section>

      {libraryOpen ? (
        <WorkspaceContainerLibrary definitions={definitions} mode={mode} openIds={openIds} onAdd={addContainer} />
      ) : null}

      <section className="trading-workspace-canvas live-workspace" data-workspace-canvas style={{ minHeight }}>
        <div className="trading-workspace-watermark" aria-hidden="true">
          <span>{workspaceBadge ?? modeLabel(mode)}</span>
          <small>container workspace</small>
        </div>
        {openIds.map((id) => {
          const definition = definitionById.get(id);
          const layout = layouts[id];
          if (!definition || !layout) return null;
          const meta = metaForContainer?.(definition) ?? containerMeta(definition, mode, historicalSourceReady, runStatus);
          return (
            <WorkspaceWindow
              canPopOut={canPopOut}
              canvasTargets={canvasTargets}
              compact={compact}
              icon={containerIcon(id)}
              id={id}
              key={id}
              kind={id}
              layout={layout}
              linkLabel={linkLabelForContainer?.(definition)}
              meta={meta}
              onClose={() => setOpenIds((current) => current.filter((candidate) => candidate !== id))}
              onFocus={() => focusContainer(id)}
              onLayoutChange={updateLayout}
              onMoveToCanvas={(windowId, targetCanvasId) => moveContainer(windowId as WorkspaceContainerId, targetCanvasId)}
              onPopOut={() => onPopOutContainer?.(id, layout)}
              onReset={() => resetContainer(id)}
              title={definition.title}
            >
              {renderContainer ? renderContainer(definition) : <ContainerStandby definition={definition} meta={meta} mode={mode} />}
            </WorkspaceWindow>
          );
        })}
      </section>
    </div>
  );
}

function WorkspaceContainerLibrary({
  definitions,
  mode,
  onAdd,
  openIds,
}: {
  definitions: WorkspaceContainerDefinition[];
  mode: TradingWorkspaceMode;
  onAdd: (id: WorkspaceContainerId) => void;
  openIds: WorkspaceContainerId[];
}) {
  return (
    <section className="workspace-container-library" aria-label="Container library">
      <header>
        <div>
          <span>Container library</span>
          <strong>One container contract, mode-specific sources</strong>
        </div>
        <small>{definitions.length} compatible with {modeLabel(mode)}</small>
      </header>
      <div className="workspace-container-library-grid">
        {definitions.map((definition) => {
          const binding = sourceBindingForContainer(definition, mode);
          const isOpen = openIds.includes(definition.id);
          return (
            <article key={definition.id}>
              <div className="workspace-library-icon">{containerIcon(definition.id)}</div>
              <div className="workspace-library-copy">
                <strong>{definition.title}</strong>
                <p>{definition.description}</p>
                <small>{binding.summary}</small>
              </div>
              <button className="button secondary compact" onClick={() => onAdd(definition.id)} type="button">
                {isOpen ? "Focus" : "Add"}
              </button>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ContainerStandby({ definition, meta, mode }: { definition: WorkspaceContainerDefinition; meta: WorkspaceWindowMeta; mode: TradingWorkspaceMode }) {
  const binding = sourceBindingForContainer(definition, mode);
  return (
    <div className="workspace-container-standby">
      <div className="workspace-container-standby-icon">{containerIcon(definition.id)}</div>
      <strong>{definition.title} is ready for a run</strong>
      <p>{definition.description}</p>
      <div className="workspace-source-stack">
        {binding.layers.map((layer, index) => (
          <div key={layer.id}>
            <span>{index === 0 ? "Primary" : "Supporting"}</span>
            <strong>{layer.label}</strong>
            <small>{layer.updateModel.replace("-", " ")} · {layer.timeBasis.replaceAll("-", " ")}</small>
          </div>
        ))}
      </div>
      <small className="workspace-standby-status">{meta.status === "error" ? "The required source is unavailable." : "Content begins at the run clock and remains stable while the container refreshes."}</small>
    </div>
  );
}

function containerMeta(
  definition: WorkspaceContainerDefinition,
  mode: TradingWorkspaceMode,
  historicalSourceReady: boolean,
  runStatus: TradingWorkspaceProps["runStatus"],
): WorkspaceWindowMeta {
  const binding = sourceBindingForContainer(definition, mode);
  const dependsOnHistory = binding.layers.some((layer) => layer.id === "qmd-history");
  const status = dependsOnHistory && !historicalSourceReady ? "error" : runStatus === "running" ? "ready" : "idle";
  return {
    detail: binding.layers.map((layer) => `${layer.label}: ${layer.description}`).join("\n"),
    freshness: runStatus === "running" ? "at run clock" : "waiting for run",
    sourceLabel: binding.layers.map((layer) => layer.label).join(" + "),
    status,
  };
}

function readWorkspaceState(
  storageKey: string,
  mode: TradingWorkspaceMode,
  definitions: WorkspaceContainerDefinition[],
  defaultOpenIds: WorkspaceContainerId[] | undefined,
  layoutPreset: "focus" | "global" | "mode",
): CanvasWorkspaceState {
  const defaultIds = defaultOpenIds ?? defaultContainersForMode(mode);
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) throw new Error("no saved layout");
    const parsed = parseWorkspaceState(raw, definitions);
    if (!parsed) throw new Error("stale layout");
    return parsed;
  } catch {
    return { layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts: createLayoutsForPreset(layoutPreset, mode, defaultIds), openIds: defaultIds };
  }
}

function parseWorkspaceState(raw: string, definitions: WorkspaceContainerDefinition[]): CanvasWorkspaceState | null {
  try {
    const parsed = JSON.parse(raw) as Partial<CanvasWorkspaceState>;
    if (parsed.layoutVersion !== TRADING_WORKSPACE_LAYOUT_VERSION || !parsed.layouts || !Array.isArray(parsed.openIds)) return null;
    const openIds = parsed.openIds.filter((id) => definitions.some((definition) => definition.id === id));
    const layouts = Object.fromEntries(openIds.flatMap((id) => parsed.layouts?.[id] ? [[id, parsed.layouts[id]]] : []));
    return { layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts, openIds };
  } catch {
    return null;
  }
}

function createLayoutsForPreset(preset: "focus" | "global" | "mode", mode: TradingWorkspaceMode, ids: WorkspaceContainerId[]) {
  if (preset === "focus") return createFocusLayouts(ids);
  return preset === "global" ? createGlobalLayouts(ids) : createHistoricalLayouts(mode, ids);
}

function createGlobalLayouts(ids: WorkspaceContainerId[]): Record<string, WorkspaceWindowLayout> {
  const width = availableWorkspaceWidth();
  const margin = 0;
  const gap = 2;
  const columnWidth = Math.floor((width - margin * 2 - gap) / 2);
  const placements: Record<WorkspaceContainerId, Omit<WorkspaceWindowLayout, "fullscreen" | "minimized" | "z">> = {
    scanner: { h: 250, w: columnWidth, x: margin, y: 0 },
    chart: { h: 410, w: columnWidth, x: margin + columnWidth + gap, y: 0 },
    portfolio: { h: 230, w: columnWidth, x: margin, y: 252 },
    orders: { h: 230, w: columnWidth, x: margin + columnWidth + gap, y: 412 },
    fills: { h: 220, w: columnWidth, x: margin, y: 484 },
    strategy: { h: 220, w: columnWidth, x: margin + columnWidth + gap, y: 644 },
    news: { h: 290, w: columnWidth, x: margin, y: 706 },
    sec: { h: 290, w: columnWidth, x: margin + columnWidth + gap, y: 866 },
    xbrl: { h: 290, w: columnWidth, x: margin, y: 998 },
    journal: { h: 290, w: columnWidth, x: margin + columnWidth + gap, y: 1158 },
  };
  return Object.fromEntries(ids.map((id, index) => [id, { ...placements[id], fullscreen: false, minimized: false, z: index + 1 }]));
}

export function createFocusLayouts(ids: WorkspaceContainerId[]): Record<string, WorkspaceWindowLayout> {
  const width = availableWorkspaceWidth(true);
  const height = Math.max(320, availableWorkspaceHeight() - 62);
  if (ids.length === 1) return { [ids[0]]: { fullscreen: true, h: height, minimized: false, w: width, x: 0, y: 0, z: 1 } };
  const gap = 2;
  const columnWidth = Math.floor((width - gap) / 2);
  return Object.fromEntries(ids.map((id, index) => [id, {
    fullscreen: false,
    h: Math.max(280, Math.floor((height - gap) / Math.ceil(ids.length / 2))),
    minimized: false,
    w: columnWidth,
    x: index % 2 === 0 ? 0 : columnWidth + gap,
    y: Math.floor(index / 2) * (Math.max(280, Math.floor((height - gap) / Math.ceil(ids.length / 2))) + gap),
    z: index + 1,
  }]));
}

function createHistoricalLayouts(mode: TradingWorkspaceMode, ids: WorkspaceContainerId[]): Record<string, WorkspaceWindowLayout> {
  const width = availableWorkspaceWidth();
  const margin = 12;
  const gap = 10;
  const leftWidth = Math.round((width - margin * 2 - gap) * 0.4);
  const rightWidth = width - margin * 2 - gap - leftWidth;
  const rightX = margin + leftWidth + gap;
  const placements: Partial<Record<WorkspaceContainerId, Omit<WorkspaceWindowLayout, "fullscreen" | "minimized" | "z">>> = mode === "backtest"
    ? {
        strategy: { h: 270, w: leftWidth, x: margin, y: 84 },
        portfolio: { h: 260, w: leftWidth, x: margin, y: 364 },
        orders: { h: 270, w: rightWidth, x: rightX, y: 84 },
        fills: { h: 260, w: rightWidth, x: rightX, y: 364 },
        journal: { h: 290, w: width - margin * 2, x: margin, y: 634 },
      }
    : {
        scanner: { h: 330, w: leftWidth, x: margin, y: 84 },
        portfolio: { h: 230, w: leftWidth, x: margin, y: 424 },
        chart: { h: 570, w: rightWidth, x: rightX, y: 84 },
        news: { h: 280, w: leftWidth, x: margin, y: 664 },
        orders: { h: 280, w: rightWidth, x: rightX, y: 664 },
      };
  return Object.fromEntries(ids.map((id, index) => {
    const placement = placements[id] ?? createAddedLayout({}, index);
    return [id, { ...placement, fullscreen: false, minimized: false, z: index + 1 }];
  }));
}

function createAddedLayout(layouts: Record<string, WorkspaceWindowLayout>, index: number, focus = false): WorkspaceWindowLayout {
  const highest = Math.max(0, ...Object.values(layouts).map((layout) => layout.z));
  const offset = (index % 5) * 18;
  const width = availableWorkspaceWidth(focus);
  return focus
    ? { fullscreen: true, h: Math.max(320, availableWorkspaceHeight() - 62), minimized: false, w: width, x: 0, y: 0, z: highest + 1 }
    : { fullscreen: false, h: 320, minimized: false, w: Math.min(560, width - 36), x: 18 + offset, y: 18 + offset, z: highest + 1 };
}

function availableWorkspaceWidth(noSidebar = false) {
  if (typeof window === "undefined") return 1180;
  const storedScale = Number(window.localStorage.getItem("quant-research-workbench.ui-scale"));
  const scale = Number.isFinite(storedScale) && storedScale > 0 ? storedScale : 1;
  const scaledViewportWidth = window.innerWidth / scale;
  const shellWidth = noSidebar ? 0 : 256;
  const contentPadding = noSidebar ? 0 : 48;
  return Math.max(680, Math.floor(scaledViewportWidth - shellWidth - contentPadding));
}

function availableWorkspaceHeight() {
  if (typeof window === "undefined") return 900;
  const storedScale = Number(window.localStorage.getItem("quant-research-workbench.ui-scale"));
  const scale = Number.isFinite(storedScale) && storedScale > 0 ? storedScale : 1;
  return Math.floor(window.innerHeight / scale);
}

function cloneLayouts(layouts: Record<string, WorkspaceWindowLayout>) {
  return Object.fromEntries(Object.entries(layouts).map(([id, layout]) => [id, { ...layout }]));
}

function containerIcon(id: WorkspaceContainerId) {
  const icons = {
    chart: BarChart3,
    fills: ListChecks,
    journal: ScrollText,
    news: Newspaper,
    orders: ShoppingCart,
    portfolio: BriefcaseBusiness,
    scanner: ScanSearch,
    sec: FileSearch,
    strategy: Settings2,
    xbrl: Building2,
  } satisfies Record<WorkspaceContainerId, typeof BarChart3>;
  const Icon = icons[id];
  return <Icon aria-hidden="true" size={14} />;
}

function modeLabel(mode: TradingWorkspaceMode) {
  if (mode === "backtest_debug") return "Backtest debug";
  return mode.charAt(0).toUpperCase() + mode.slice(1);
}
