import {
  BarChart3,
  ExternalLink,
  Eye,
  FolderOpen,
  LayoutGrid,
  Maximize2,
  Minimize2,
  Move,
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

type WorkspaceWindowProps = {
  canvasTargets: WorkspaceCanvasTarget[];
  children: ReactNode;
  icon: ReactNode;
  id: WorkspaceWindowId;
  kind?: string;
  layout: WorkspaceWindowLayout;
  onClose: (id: WorkspaceWindowId) => void;
  onFocus: (id: WorkspaceWindowId) => void;
  onLayoutChange: (id: WorkspaceWindowId, patch: Partial<WorkspaceWindowLayout>) => void;
  onMoveToCanvas: (id: WorkspaceWindowId, canvasId: string) => void;
  onPopOut: (id: WorkspaceWindowId) => void;
  title: string;
};

const MIN_WINDOW_WIDTH = 320;
const MIN_WINDOW_HEIGHT = 240;
const KEYBOARD_MOVE_STEP = 10;
const KEYBOARD_MOVE_STEP_LARGE = 40;

export function WorkspaceWindow({
  canvasTargets,
  children,
  icon,
  id,
  kind,
  layout,
  onClose,
  onFocus,
  onLayoutChange,
  onMoveToCanvas,
  onPopOut,
  title,
}: WorkspaceWindowProps) {
  const style = layout.fullscreen
    ? { height: "calc(100% - 24px)", left: 12, top: 12, width: "calc(100% - 24px)", zIndex: 1000 + layout.z }
    : { height: layout.minimized ? 34 : layout.h, left: layout.x, top: layout.y, width: layout.w, zIndex: layout.z };

  function horizontalBounds(element: HTMLElement) {
    const workspace = element.closest<HTMLElement>("[data-workspace-canvas]");
    return workspace?.clientWidth ?? Number.POSITIVE_INFINITY;
  }

  function moveWindow(x: number, y: number, element: HTMLElement) {
    const canvasWidth = horizontalBounds(element);
    onLayoutChange(id, {
      x: Math.max(0, Math.min(x, Math.max(0, canvasWidth - layout.w))),
      y: Math.max(0, y),
    });
  }

  function resizeWindow(w: number, h: number, element: HTMLElement) {
    const canvasWidth = horizontalBounds(element);
    onLayoutChange(id, {
      h: Math.max(MIN_WINDOW_HEIGHT, h),
      w: Math.max(MIN_WINDOW_WIDTH, Math.min(w, Math.max(MIN_WINDOW_WIDTH, canvasWidth - layout.x))),
    });
  }

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    if (layout.fullscreen) return;
    const originX = event.clientX;
    const originY = event.clientY;
    const startX = layout.x;
    const startY = layout.y;
    event.currentTarget.setPointerCapture(event.pointerId);
    const target = event.currentTarget;
    const move = (moveEvent: globalThis.PointerEvent) => {
      moveWindow(startX + moveEvent.clientX - originX, startY + moveEvent.clientY - originY, target);
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
    moveWindow(layout.x + dx, layout.y + dy, event.currentTarget);
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
      resizeWindow(startW + moveEvent.clientX - originX, startH + moveEvent.clientY - originY, target);
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
    resizeWindow(layout.w + dw, layout.h + dh, event.currentTarget);
  }

  return (
    <section
      aria-label={title}
      className="workspace-window live-window"
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
          <Move aria-hidden="true" size={13} />
          {icon}
          <strong>{title}</strong>
        </div>
        <div className="workspace-window-actions live-window-actions" onPointerDown={(event) => event.stopPropagation()}>
          <CanvasTargetButtons canvasTargets={canvasTargets} onMove={(canvasId) => onMoveToCanvas(id, canvasId)} title={title} />
          <button aria-label={`Move ${title} to a new canvas`} className="toolbar-button compact" onClick={() => onPopOut(id)} title="Move to new child canvas" type="button">
            <ExternalLink size={12} />
          </button>
          <button aria-label={layout.minimized ? `Restore ${title}` : `Minimize ${title}`} className="toolbar-button compact" onClick={() => onLayoutChange(id, { minimized: !layout.minimized })} title={layout.minimized ? "Restore" : "Minimize"} type="button">
            <Minimize2 size={12} />
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
    const windowHeight = layout.minimized ? 34 : layout.h;
    return Math.max(height, layout.y + windowHeight + 24);
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
