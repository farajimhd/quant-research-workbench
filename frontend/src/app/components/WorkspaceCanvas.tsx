import {
  BarChart3,
  Check,
  ExternalLink,
  Eye,
  FolderOpen,
  LayoutGrid,
  Link2,
  Maximize2,
  Minus,
  Minimize2,
  PanelTopOpen,
  RotateCcw,
  Unlink,
  X,
} from "lucide-react";
import type {
  CSSProperties,
  KeyboardEvent,
  PointerEvent,
  ReactNode,
} from "react";

export type WorkspaceWindowId = string;

export type WorkspaceWindowLayout = {
  fullscreen: boolean;
  h: number;
  minimized: boolean;
  w: number;
  x: number;
  y: number;
  z: number;
};

export type WorkspaceWindowSummary = {
  fullscreen: boolean;
  id: WorkspaceWindowId;
  kind: string;
  minimized: boolean;
  title: string;
  z: number;
};

export type WorkspaceCanvasTarget = {
  color: string;
  id: string;
  isCurrent: boolean;
  label: string;
};

export type WorkspaceWindowStatus = "connecting" | "error" | "idle" | "ready" | "stale";

export type WorkspaceWindowMeta = {
  detail?: string;
  freshness?: string;
  sourceLabel: string;
  status: WorkspaceWindowStatus;
};

type WorkspaceWindowProps = {
  canPopOut?: boolean;
  canvasTargets: WorkspaceCanvasTarget[];
  children: ReactNode;
  compact?: boolean;
  icon: ReactNode;
  id: WorkspaceWindowId;
  kind?: string;
  layout: WorkspaceWindowLayout;
  linkColor?: string;
  titleBarActions?: ReactNode;
  linkLabel?: string;
  meta?: WorkspaceWindowMeta;
  fullscreenRightInset?: number | string;
  onClose: (id: WorkspaceWindowId) => void;
  onFocus: (id: WorkspaceWindowId) => void;
  onLayoutChange: (id: WorkspaceWindowId, patch: Partial<WorkspaceWindowLayout>) => void;
  onMoveToCanvas: (id: WorkspaceWindowId, canvasId: string) => void;
  onPopOut: (id: WorkspaceWindowId) => void;
  onReset?: (id: WorkspaceWindowId) => void;
  onSelectionToggle?: (id: WorkspaceWindowId) => void;
  selected?: boolean;
  title: string;
};

const MIN_WINDOW_WIDTH = 320;
const MIN_WINDOW_HEIGHT = 240;
const KEYBOARD_MOVE_STEP = 10;
const KEYBOARD_MOVE_STEP_LARGE = 40;

export function WorkspaceWindow({
  canPopOut = true,
  canvasTargets,
  children,
  compact = false,
  icon,
  id,
  kind,
  layout,
  linkColor,
  titleBarActions,
  linkLabel,
  meta,
  fullscreenRightInset = 0,
  onClose,
  onFocus,
  onLayoutChange,
  onMoveToCanvas,
  onPopOut,
  onReset,
  onSelectionToggle,
  selected = false,
  title,
}: WorkspaceWindowProps) {
  const edge = compact ? 0 : 12;
  const minimizedHeight = compact ? 24 : 44;
  const geometry = layout.fullscreen
    ? { bottom: edge, left: edge, right: fullscreenRightInset || edge, top: edge, zIndex: 1000 + layout.z }
    : { height: layout.minimized ? minimizedHeight : layout.h, left: layout.x, top: layout.y, width: layout.w, zIndex: layout.z };
  const style = {
    ...geometry,
    ...(linkColor ? { "--workspace-link-color": linkColor } : {}),
  } as CSSProperties;

  function moveWindow(x: number, y: number) {
    onLayoutChange(id, {
      x: Math.max(0, x),
      y: Math.max(0, y),
    });
  }

  function resizeWindow(w: number, h: number) {
    onLayoutChange(id, {
      h: Math.max(MIN_WINDOW_HEIGHT, h),
      w: Math.max(MIN_WINDOW_WIDTH, w),
    });
  }

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    if (layout.fullscreen) return;
    if ((event.target as HTMLElement).closest("button, summary, input, select, textarea, a, [role='menu']")) return;
    const originX = event.clientX;
    const originY = event.clientY;
    const startX = layout.x;
    const startY = layout.y;
    event.currentTarget.setPointerCapture(event.pointerId);
    const target = event.currentTarget;
    const move = (moveEvent: globalThis.PointerEvent) => {
      moveWindow(startX + moveEvent.clientX - originX, startY + moveEvent.clientY - originY);
    };
    const stop = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", stop);
      target.removeEventListener("pointercancel", stop);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", stop);
    target.addEventListener("pointercancel", stop);
  }

  function moveWithKeyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return;
    if (layout.fullscreen || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const step = event.shiftKey ? KEYBOARD_MOVE_STEP_LARGE : KEYBOARD_MOVE_STEP;
    const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
    const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
    moveWindow(layout.x + dx, layout.y + dy);
  }

  function startResize(event: PointerEvent<HTMLButtonElement>) {
    if (layout.fullscreen || layout.minimized) return;
    event.stopPropagation();
    const originX = event.clientX;
    const originY = event.clientY;
    const startW = layout.w;
    const startH = layout.h;
    event.currentTarget.setPointerCapture(event.pointerId);
    const target = event.currentTarget;
    const move = (moveEvent: globalThis.PointerEvent) => {
      resizeWindow(startW + moveEvent.clientX - originX, startH + moveEvent.clientY - originY);
    };
    const stop = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", stop);
      target.removeEventListener("pointercancel", stop);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", stop);
    target.addEventListener("pointercancel", stop);
  }

  function resizeWithKeyboard(event: KeyboardEvent<HTMLButtonElement>) {
    if (layout.fullscreen || layout.minimized || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const step = event.shiftKey ? KEYBOARD_MOVE_STEP_LARGE : KEYBOARD_MOVE_STEP;
    const dw = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
    const dh = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
    resizeWindow(layout.w + dw, layout.h + dh);
  }

  return (
    <section
      aria-label={title}
      className={compact ? "workspace-window live-window compact-window" : "workspace-window live-window"}
      data-linked={linkColor ? "true" : "false"}
      data-selected={selected ? "true" : "false"}
      data-window-kind={kind ?? (id.startsWith("chart-") ? "chart" : id)}
      style={style}
      onPointerDown={() => onFocus(id)}
    >
      <div
        aria-label={`Move ${title}. Use arrow keys to reposition; hold Shift for larger steps.`}
        className="workspace-window-header live-window-header"
        onKeyDown={moveWithKeyboard}
        onPointerDown={startDrag}
        role="toolbar"
        tabIndex={0}
      >
        <div className="workspace-window-title live-window-title">
          {icon}
          <div className="workspace-window-heading">
            <strong>{title}</strong>
            {meta ? <small title={meta.detail}>{meta.sourceLabel}{meta.freshness ? ` · ${meta.freshness}` : ""}</small> : null}
          </div>
          {linkLabel ? <span className="workspace-window-link" title={`Linked context ${linkLabel}`}><Link2 size={10} /> {linkLabel}</span> : null}
          {linkColor ? <span aria-label="Linked container color" className="workspace-window-link-marker" title="This container participates in the matching link color" /> : null}
        </div>
        <div className="workspace-window-actions live-window-actions" onPointerDown={(event) => event.stopPropagation()}>
          {onSelectionToggle ? <button aria-label={`${selected ? "Remove" : "Add"} ${title} ${selected ? "from" : "to"} group selection`} aria-pressed={selected} className="toolbar-button compact workspace-group-select" data-active={selected ? "true" : "false"} onClick={() => onSelectionToggle(id)} onPointerDown={(event) => event.stopPropagation()} title={selected ? "Remove from group selection" : "Select for grouping"} type="button">
            {selected ? <Check size={12} /> : <LayoutGrid size={12} />}
          </button> : null}
          {titleBarActions}
          {canvasTargets.length > 1 ? <CanvasTargetSelect canvasTargets={canvasTargets} onMove={(canvasId) => onMoveToCanvas(id, canvasId)} title={title} /> : null}
          {canPopOut ? (
            <button aria-label={`Open linked ${title} in a new canvas`} className="toolbar-button compact" onClick={() => onPopOut(id)} title="Open linked copy in a focus canvas" type="button">
              <ExternalLink size={12} />
            </button>
          ) : null}
          {onReset ? <button aria-label={`Reset ${title} to its default layout`} className="toolbar-button compact" onClick={() => onReset(id)} title="Reset container layout" type="button">
            <RotateCcw size={12} />
          </button> : null}
          <button aria-label={layout.minimized ? `Restore ${title}` : `Minimize ${title}`} className="toolbar-button compact" onClick={() => onLayoutChange(id, { minimized: !layout.minimized })} title={layout.minimized ? "Restore from title bar" : "Minimize to title bar"} type="button">
            {layout.minimized ? <PanelTopOpen size={12} /> : <Minus size={12} />}
          </button>
          <button aria-label={layout.fullscreen ? `Exit fullscreen ${title}` : `Fullscreen ${title}`} className="toolbar-button compact" onClick={() => onLayoutChange(id, { fullscreen: !layout.fullscreen, minimized: false })} title={layout.fullscreen ? "Exit fullscreen" : "Fullscreen"} type="button">
            {layout.fullscreen ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
          </button>
          <button aria-label={`Close ${title}`} className="toolbar-button compact" onClick={() => onClose(id)} title="Close" type="button">
            <X size={12} />
          </button>
        </div>
      </div>
      {!layout.minimized ? <div className="workspace-window-body live-window-body">{children}</div> : null}
      {!layout.minimized ? (
        <button
          aria-label={`Resize ${title}. Use arrow keys to resize; hold Shift for larger steps.`}
          className="workspace-window-resize live-window-resize"
          onKeyDown={resizeWithKeyboard}
          onPointerDown={startResize}
          type="button"
        />
      ) : null}
    </section>
  );
}

export type WorkspaceGroupMenuItem = {
  actions?: ReactNode;
  id: string;
  isGroup: boolean;
  kind?: string;
  title: string;
};

type WorkspaceGroupWindowProps = {
  canPopOut?: boolean;
  canvasTargets: WorkspaceCanvasTarget[];
  children: ReactNode;
  compact?: boolean;
  fullscreenRightInset?: number | string;
  id: WorkspaceWindowId;
  layout: WorkspaceWindowLayout;
  memberCount: number;
  menuItems: WorkspaceGroupMenuItem[];
  minHeight?: number;
  minWidth?: number;
  onCloseMember: (id: WorkspaceWindowId) => void;
  onDetachMember: (id: WorkspaceWindowId) => void;
  onFocus: (id: WorkspaceWindowId) => void;
  onLayoutChange: (id: WorkspaceWindowId, patch: Partial<WorkspaceWindowLayout>) => void;
  onMoveToCanvas: (id: WorkspaceWindowId, canvasId: string) => void;
  onPopOut: (id: WorkspaceWindowId) => void;
  onSelectionToggle: (id: WorkspaceWindowId) => void;
  onUngroup: (id: WorkspaceWindowId) => void;
  onUngroupMember: (id: WorkspaceWindowId) => void;
  selected?: boolean;
  title: string;
};

export function WorkspaceGroupWindow({
  canPopOut = true,
  canvasTargets,
  children,
  compact = false,
  fullscreenRightInset = 0,
  id,
  layout,
  memberCount,
  menuItems,
  minHeight = MIN_WINDOW_HEIGHT,
  minWidth = MIN_WINDOW_WIDTH,
  onCloseMember,
  onDetachMember,
  onFocus,
  onLayoutChange,
  onMoveToCanvas,
  onPopOut,
  onSelectionToggle,
  onUngroup,
  onUngroupMember,
  selected = false,
  title,
}: WorkspaceGroupWindowProps) {
  const edge = compact ? 0 : 12;
  const headerHeight = compact ? 24 : 44;
  const groupTop = Math.max(0, layout.y - headerHeight);
  const headerOffset = layout.y - groupTop;
  const geometry = layout.fullscreen
    ? { "--workspace-group-header-height": `${headerHeight}px`, bottom: edge, left: edge, right: fullscreenRightInset || edge, top: edge, zIndex: 1000 + layout.z }
    : {
      "--workspace-group-header-height": `${headerHeight}px`,
      height: layout.minimized ? headerHeight : layout.h + headerOffset,
      left: layout.x,
      top: groupTop,
      width: layout.w,
      zIndex: layout.z,
    };

  function moveGroup(x: number, y: number) {
    onLayoutChange(id, { x: Math.max(0, x), y: Math.max(0, y) });
  }

  function resizeGroup(w: number, h: number) {
    onLayoutChange(id, { h: Math.max(minHeight, h), w: Math.max(minWidth, w) });
  }

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    if (layout.fullscreen) return;
    if ((event.target as HTMLElement).closest("button, summary, input, select, textarea, a, [role='menu']")) return;
    const originX = event.clientX;
    const originY = event.clientY;
    const startX = layout.x;
    const startY = layout.y;
    event.currentTarget.setPointerCapture(event.pointerId);
    const target = event.currentTarget;
    const move = (moveEvent: globalThis.PointerEvent) => moveGroup(startX + moveEvent.clientX - originX, startY + moveEvent.clientY - originY);
    const stop = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", stop);
      target.removeEventListener("pointercancel", stop);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", stop);
    target.addEventListener("pointercancel", stop);
  }

  function moveWithKeyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget || layout.fullscreen || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const step = event.shiftKey ? KEYBOARD_MOVE_STEP_LARGE : KEYBOARD_MOVE_STEP;
    moveGroup(layout.x + (event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0), layout.y + (event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0));
  }

  function startResize(event: PointerEvent<HTMLButtonElement>) {
    if (layout.fullscreen || layout.minimized) return;
    event.stopPropagation();
    const originX = event.clientX;
    const originY = event.clientY;
    const startW = layout.w;
    const startH = layout.h;
    event.currentTarget.setPointerCapture(event.pointerId);
    const target = event.currentTarget;
    const move = (moveEvent: globalThis.PointerEvent) => resizeGroup(startW + moveEvent.clientX - originX, startH + moveEvent.clientY - originY);
    const stop = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", stop);
      target.removeEventListener("pointercancel", stop);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", stop);
    target.addEventListener("pointercancel", stop);
  }

  function resizeWithKeyboard(event: KeyboardEvent<HTMLButtonElement>) {
    if (layout.fullscreen || layout.minimized || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const step = event.shiftKey ? KEYBOARD_MOVE_STEP_LARGE : KEYBOARD_MOVE_STEP;
    resizeGroup(layout.w + (event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0), layout.h + (event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0));
  }

  return <section aria-label={title} className={compact ? "workspace-window workspace-group-window live-window compact-window" : "workspace-window workspace-group-window live-window"} data-selected={selected ? "true" : "false"} data-workspace-group={id} onPointerDown={() => onFocus(id)} style={geometry as CSSProperties}>
    <div aria-label={`Move ${title}. Use arrow keys to reposition; hold Shift for larger steps.`} className="workspace-window-header workspace-group-header live-window-header" onKeyDown={moveWithKeyboard} onPointerDown={startDrag} role="toolbar" tabIndex={0}>
      <div className="workspace-window-title live-window-title">
        <LayoutGrid aria-hidden="true" size={14} />
        <div className="workspace-window-heading"><strong>{title}</strong><small>{memberCount} containers · grouped layout</small></div>
      </div>
      <div className="workspace-window-actions live-window-actions" onPointerDown={(event) => event.stopPropagation()}>
        <button aria-label={`${selected ? "Remove" : "Add"} ${title} ${selected ? "from" : "to"} group selection`} aria-pressed={selected} className="toolbar-button compact workspace-group-select" data-active={selected ? "true" : "false"} onClick={() => onSelectionToggle(id)} onPointerDown={(event) => event.stopPropagation()} title={selected ? "Remove from group selection" : "Select group for grouping"} type="button">{selected ? <Check size={12} /> : <LayoutGrid size={12} />}</button>
        <details className="workspace-group-members">
          <summary aria-label={`Manage members of ${title}`} className="toolbar-button compact" title="Group members"><PanelTopOpen size={12} /></summary>
          <div className="workspace-group-member-menu">
            <header><strong>Group members</strong><small>Detach preserves position</small></header>
            {menuItems.map((item) => <div className="workspace-group-member-row" data-member-kind={item.kind ?? "group"} key={item.id}>
              <span><strong>{item.title}</strong><small>{item.isGroup ? "Nested group" : item.kind}</small></span>
              <div>{item.actions}<button aria-label={`Detach ${item.title} from ${title}`} className="toolbar-button compact" onClick={() => onDetachMember(item.id)} title="Detach from group" type="button"><ExternalLink size={11} /></button>{item.isGroup ? <button aria-label={`Ungroup nested ${item.title}`} className="toolbar-button compact" onClick={() => onUngroupMember(item.id)} title="Ungroup nested group one level" type="button"><Unlink size={11} /></button> : <button aria-label={`Close ${item.title}`} className="toolbar-button compact" onClick={() => onCloseMember(item.id)} title="Close container" type="button"><X size={11} /></button>}</div>
            </div>)}
          </div>
        </details>
        {canvasTargets.length > 1 ? <CanvasTargetSelect canvasTargets={canvasTargets} onMove={(canvasId) => onMoveToCanvas(id, canvasId)} title={title} /> : null}
        {canPopOut ? <button aria-label={`Open ${title} in a new canvas`} className="toolbar-button compact" onClick={() => onPopOut(id)} title="Move group to a new focus canvas" type="button"><ExternalLink size={12} /></button> : null}
        <button aria-label={`Ungroup ${title}`} className="toolbar-button compact" onClick={() => onUngroup(id)} title="Ungroup one level" type="button"><Unlink size={12} /></button>
        <button aria-label={layout.minimized ? `Restore ${title}` : `Minimize ${title}`} className="toolbar-button compact" onClick={() => onLayoutChange(id, { minimized: !layout.minimized })} title={layout.minimized ? "Restore group" : "Minimize group"} type="button">{layout.minimized ? <PanelTopOpen size={12} /> : <Minus size={12} />}</button>
        <button aria-label={layout.fullscreen ? `Exit fullscreen ${title}` : `Fullscreen ${title}`} className="toolbar-button compact" onClick={() => onLayoutChange(id, { fullscreen: !layout.fullscreen, minimized: false })} title={layout.fullscreen ? "Exit group fullscreen" : "Fullscreen group"} type="button">{layout.fullscreen ? <Minimize2 size={12} /> : <Maximize2 size={12} />}</button>
      </div>
    </div>
    {!layout.minimized ? <div className="workspace-group-body">{children}</div> : null}
    {!layout.minimized ? <button aria-label={`Resize ${title}. Use arrow keys to resize; hold Shift for larger steps.`} className="workspace-window-resize live-window-resize" onKeyDown={resizeWithKeyboard} onPointerDown={startResize} type="button" /> : null}
  </section>;
}

export function WorkspaceGroupedMember({
  bounds,
  children,
  groupBounds,
  id,
  kind,
  onFocus,
  title,
}: {
  bounds: Pick<WorkspaceWindowLayout, "h" | "w" | "x" | "y">;
  children: ReactNode;
  groupBounds: Pick<WorkspaceWindowLayout, "h" | "w" | "x" | "y">;
  id: string;
  kind: string;
  onFocus: () => void;
  title: string;
}) {
  const left = (bounds.x - groupBounds.x) / groupBounds.w * 100;
  const top = (bounds.y - groupBounds.y) / groupBounds.h * 100;
  const width = bounds.w / groupBounds.w * 100;
  const height = bounds.h / groupBounds.h * 100;
  const style = {
    height: `${height}%`,
    left: `${left}%`,
    top: `${top}%`,
    width: `${width}%`,
  } as CSSProperties;
  return <section aria-label={title} className="workspace-group-member" data-window-id={id} data-window-kind={kind} onPointerDown={(event) => { event.stopPropagation(); onFocus(); }} style={style}><div className="workspace-window-body live-window-body">{children}</div></section>;
}

export function WorkspaceWindowManager({
  canvasTargets,
  coreWindowLabel = "Core Containers",
  onClose,
  onFocus,
  onMinimize,
  onMoveToCanvas,
  onPopOut,
  onShowCoreWindows,
  windows,
}: {
  canvasTargets: WorkspaceCanvasTarget[];
  coreWindowLabel?: string;
  onClose: (id: WorkspaceWindowId) => void;
  onFocus: (id: WorkspaceWindowId) => void;
  onMinimize: (id: WorkspaceWindowId, minimized: boolean) => void;
  onMoveToCanvas: (id: WorkspaceWindowId, canvasId: string) => void;
  onPopOut: (id: WorkspaceWindowId) => void;
  onShowCoreWindows: () => void;
  windows: WorkspaceWindowSummary[];
}) {
  return (
    <section className="workspace-window-manager live-window-manager" aria-label="Open workspace containers">
      <div className="workspace-manager-heading live-window-manager-heading">
        <div>
          <span>Open Containers</span>
          <strong>{windows.length ? `${windows.length} active` : "No active containers"}</strong>
        </div>
        <button className="button secondary compact" onClick={onShowCoreWindows} type="button">
          <FolderOpen size={14} /> {coreWindowLabel}
        </button>
      </div>
      {windows.length ? (
        <div className="workspace-window-chip-grid live-window-chip-grid">
          {windows.map((windowItem) => (
            <article className="workspace-window-chip live-window-chip" data-type={windowItem.kind} key={windowItem.id}>
              <button className="workspace-window-chip-main live-window-chip-main" onClick={() => onFocus(windowItem.id)} type="button">
                {windowItem.kind === "chart" ? <BarChart3 size={14} /> : <LayoutGrid size={14} />}
                <span>{windowItem.title}</span>
                <small>{windowItem.minimized ? "Minimized" : windowItem.fullscreen ? "Fullscreen" : `Layer ${windowItem.z}`}</small>
              </button>
              <div className="workspace-window-chip-actions live-window-chip-actions">
                <CanvasTargetButtons canvasTargets={canvasTargets} onMove={(canvasId) => onMoveToCanvas(windowItem.id, canvasId)} title={windowItem.title} />
                <button aria-label={`Show ${windowItem.title}`} className="toolbar-button compact" onClick={() => onFocus(windowItem.id)} title="Show container" type="button"><Eye size={13} /></button>
                <button aria-label={windowItem.minimized ? `Restore ${windowItem.title}` : `Minimize ${windowItem.title}`} className="toolbar-button compact" onClick={() => onMinimize(windowItem.id, !windowItem.minimized)} title={windowItem.minimized ? "Restore container" : "Minimize container"} type="button">
                  {windowItem.minimized ? <Maximize2 size={13} /> : <Minimize2 size={13} />}
                </button>
                <button aria-label={`Move ${windowItem.title} to a new canvas`} className="toolbar-button compact" onClick={() => onPopOut(windowItem.id)} title="Move to new child canvas" type="button"><ExternalLink size={13} /></button>
                <button aria-label={`Close ${windowItem.title}`} className="toolbar-button compact" onClick={() => onClose(windowItem.id)} title="Close container" type="button"><X size={13} /></button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="live-empty-positions">No open containers on this canvas.</div>
      )}
    </section>
  );
}

export function WorkspaceCanvasManager({
  canvases,
  onCreate,
  onOpen,
  onRemove,
}: {
  canvases: WorkspaceCanvasTarget[];
  onCreate: () => void;
  onOpen: (canvasId: string) => void;
  onRemove: (canvasId: string) => void;
}) {
  return (
    <section className="workspace-canvas-manager live-canvas-manager" aria-label="Workspace canvases">
      <div className="workspace-manager-heading live-window-manager-heading">
        <div>
          <span>Canvases</span>
          <strong>{canvases.length} canvas{canvases.length === 1 ? "" : "es"}</strong>
        </div>
        <button className="button secondary compact" onClick={onCreate} type="button"><LayoutGrid size={14} /> New Canvas</button>
      </div>
      <div className="workspace-canvas-chip-grid live-canvas-chip-grid">
        {canvases.map((canvas) => (
          <article className={canvas.isCurrent ? "workspace-canvas-chip live-canvas-chip active" : "workspace-canvas-chip live-canvas-chip"} key={canvas.id} style={{ "--canvas-color": canvas.color } as CSSProperties}>
            <button className="workspace-canvas-chip-main live-canvas-chip-main" onClick={() => onOpen(canvas.id)} type="button" title={`Open ${canvas.label} in a new tab`}>
              <span>{canvas.label}</span>
              <small>{canvas.isCurrent ? "Current canvas" : canvas.id}</small>
            </button>
            <div className="workspace-window-chip-actions live-window-chip-actions">
              <button aria-label={`Open ${canvas.label} in a new tab`} className="toolbar-button compact" onClick={() => onOpen(canvas.id)} title="Open canvas in new tab" type="button"><ExternalLink size={13} /></button>
              <button
                aria-label={`Remove ${canvas.label}`}
                className="toolbar-button compact"
                disabled={canvas.id === "main" || canvas.isCurrent}
                onClick={() => onRemove(canvas.id)}
                title={canvas.id === "main" ? "Main canvas cannot be removed" : canvas.isCurrent ? "Current canvas cannot be removed from itself" : "Remove canvas"}
                type="button"
              ><X size={13} /></button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function CanvasTargetButtons({ canvasTargets, onMove, title }: { canvasTargets: WorkspaceCanvasTarget[]; onMove: (canvasId: string) => void; title: string }) {
  return (
    <div className="workspace-canvas-target-row live-canvas-target-row" aria-label={`Move ${title} to canvas`}>
      {canvasTargets.map((target) => (
        <button
          aria-label={target.isCurrent ? `${title} is on ${target.label}` : `Move ${title} to ${target.label}`}
          className={target.isCurrent ? "workspace-canvas-target live-canvas-target active" : "workspace-canvas-target live-canvas-target"}
          disabled={target.isCurrent}
          key={target.id}
          onClick={() => onMove(target.id)}
          style={{ "--canvas-color": target.color } as CSSProperties}
          title={target.isCurrent ? `Current: ${target.label}` : `Move to ${target.label}`}
          type="button"
        >
          {target.label.replace("Canvas ", "C").replace("Main", "M")}
        </button>
      ))}
    </div>
  );
}

function CanvasTargetSelect({ canvasTargets, onMove, title }: { canvasTargets: WorkspaceCanvasTarget[]; onMove: (canvasId: string) => void; title: string }) {
  const current = canvasTargets.find((target) => target.isCurrent);
  return (
    <label className="workspace-canvas-select" title={`Move ${title} to another canvas`}>
      <span className="sr-only">Move {title} to canvas</span>
      <select
        aria-label={`Move ${title} to canvas`}
        onChange={(event) => {
          if (event.target.value && event.target.value !== current?.id) onMove(event.target.value);
        }}
        value={current?.id ?? ""}
      >
        {canvasTargets.map((target) => <option key={target.id} value={target.id}>{target.label}</option>)}
      </select>
    </label>
  );
}

export function buildWorkspaceWindowSummaries(
  openWindows: WorkspaceWindowId[],
  layouts: Record<WorkspaceWindowId, WorkspaceWindowLayout>,
  describeWindow: (id: WorkspaceWindowId) => { kind: string; title: string },
): WorkspaceWindowSummary[] {
  return openWindows
    .map((id) => {
      const layout = layouts[id];
      const description = describeWindow(id);
      return {
        fullscreen: Boolean(layout?.fullscreen),
        id,
        kind: description.kind,
        minimized: Boolean(layout?.minimized),
        title: description.title,
        z: layout?.z ?? 0,
      };
    })
    .sort((a, b) => b.z - a.z);
}

export function workspaceMinHeight(
  openWindows: WorkspaceWindowId[],
  layouts: Record<WorkspaceWindowId, WorkspaceWindowLayout>,
  compact: boolean,
) {
  const viewportHeight = typeof window === "undefined" ? 1024 : window.innerHeight;
  const baseHeight = Math.max(viewportHeight, compact ? 960 : 900);
  return openWindows.reduce((height, id) => {
    const layout = layouts[id];
    if (!layout || layout.fullscreen) return height;
    const windowHeight = layout.minimized ? (compact ? 24 : 44) : layout.h;
    return Math.max(height, layout.y + windowHeight + (compact ? 2 : 24));
  }, baseHeight);
}

export function buildSplitWorkspaceLayouts({
  bottomId,
  primaryId,
  topHeight = 210,
  topId,
  topInset,
  viewportHeight,
  viewportWidth,
}: {
  bottomId: WorkspaceWindowId;
  primaryId: WorkspaceWindowId;
  topHeight?: number;
  topId: WorkspaceWindowId;
  topInset: number;
  viewportHeight: number;
  viewportWidth: number;
}): Record<WorkspaceWindowId, WorkspaceWindowLayout> {
  const margin = 12;
  const gap = 10;
  const width = Math.max(1180, viewportWidth - 112);
  const height = Math.max(780, viewportHeight - 86);
  const contentTop = margin + topInset + gap;
  const contentHeight = Math.max(560, height - contentTop - margin);
  const leftWidth = Math.min(Math.round(width * 0.44), Math.max(480, Math.round(width * 0.38)));
  const primaryWidth = Math.max(520, width - leftWidth - gap - margin * 2);
  const boundedTopHeight = Math.min(topHeight, Math.max(180, contentHeight - 280 - gap));

  return {
    [topId]: { fullscreen: false, h: boundedTopHeight, minimized: false, w: leftWidth, x: margin, y: contentTop, z: 1 },
    [bottomId]: { fullscreen: false, h: Math.max(280, contentHeight - boundedTopHeight - gap), minimized: false, w: leftWidth, x: margin, y: contentTop + boundedTopHeight + gap, z: 2 },
    [primaryId]: { fullscreen: false, h: contentHeight, minimized: false, w: primaryWidth, x: margin + leftWidth + gap, y: contentTop, z: 3 },
  };
}
