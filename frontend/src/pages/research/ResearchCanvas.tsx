import { RefreshCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { WorkspaceWindow, type WorkspaceWindowLayout } from "../../app/components/WorkspaceCanvas";
import { useResearchState } from "./researchState";

export type ResearchWindowId = "architecture" | "analytics" | "chart";
const ids: ResearchWindowId[] = ["architecture", "analytics", "chart"];

/** Research-only Canvas shell; geometry persists independently for each path. */
export function ResearchCanvas({ storageKey, titles, icons, sources, toolbar, children }: {
  storageKey: string;
  titles: Record<ResearchWindowId, string>;
  icons: Record<ResearchWindowId, ReactNode>;
  sources: Record<ResearchWindowId, string>;
  toolbar: ReactNode;
  children: Record<ResearchWindowId, ReactNode>;
}) {
  const surface = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0), [height, setHeight] = useState(760);
  const [layouts, setLayouts] = useResearchState<Partial<Record<ResearchWindowId, WorkspaceWindowLayout>>>(`${storageKey}:layouts`, {}, true);
  const [closed, setClosed] = useResearchState<ResearchWindowId[]>(`${storageKey}:closed`, [], true);
  useEffect(() => {
    const element = surface.current;
    if (!element) return;
    const measure = () => {
      if (element.clientWidth === 0) return; // Keep last visible geometry while its route is hidden.
      setWidth(element.clientWidth);
      const zoom = Number(getComputedStyle(document.documentElement).getPropertyValue("--app-zoom")) || 1;
      setHeight(Math.max(400, window.innerHeight / zoom - 224));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element); window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, []);
  const defaults = useMemo(() => {
    const stacked = width < 1040, gap = 12, left = Math.max(320, Math.floor(width * .36));
    const make = (x: number, y: number, w: number, h: number, z: number): WorkspaceWindowLayout => ({ x, y, w, h, z, minimized: false, fullscreen: false });
    return { architecture: make(0, 0, stacked ? width : left, 210, 1),
      analytics: make(0, 222, stacked ? width : left, stacked ? 560 : height - 222, 2),
      chart: make(stacked ? 0 : left + gap, stacked ? 794 : 0, stacked ? width : width - left - gap, stacked ? 650 : height, 3) };
  }, [width, height]);
  const fullscreen = ids.some(id => !closed.includes(id) && layouts[id]?.fullscreen);
  const extent = fullscreen ? height : Math.max(400, ...ids.filter(id => !closed.includes(id)).map(id => {
    const l = layouts[id] ?? defaults[id]; return l.y + (l.minimized ? 24 : l.h) + 16;
  }));
  return <div className="research-page"><header className="research-toolbar">{toolbar}
    <button className="button secondary compact" onClick={() => { setLayouts({}); setClosed([]); }}><RefreshCcw size={14} />Reset containers</button>
  </header>{closed.length > 0 && <div className="research-controls">{closed.map(id => <button className="button secondary compact" key={id} onClick={() => setClosed(current => current.filter(item => item !== id))}>Restore {titles[id]}</button>)}</div>}
    <div className="research-canvas" ref={surface} style={{ height: extent }}>{ids.filter(id => !closed.includes(id)).map(id =>
      <div key={id} style={{ display: "contents", visibility: fullscreen && !layouts[id]?.fullscreen ? "hidden" : "visible" }}>
        <WorkspaceWindow id={id} title={titles[id]} compact icon={icons[id]} layout={layouts[id] ?? defaults[id]} canvasTargets={[]} canPopOut={false}
          meta={{ status: "ready", sourceLabel: sources[id] }} onClose={() => setClosed(current => [...current, id])} onFocus={() => {}}
          onLayoutChange={(_, patch) => setLayouts(current => ({ ...current, [id]: { ...(current[id] ?? defaults[id]), ...patch } }))}
          onMoveToCanvas={() => {}} onPopOut={() => {}}>{children[id]}</WorkspaceWindow>
      </div>)}</div></div>;
}
