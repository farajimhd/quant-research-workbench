import { useEffect, useRef, useState } from "react";
import type { IChartApi, Time } from "lightweight-charts";

export type LabelCandle = { time: number; endTime?: number; open: number; close: number };
export type LabelRange = { id: string; direction: "LONG" | "SHORT"; annotation_timeframe: string; entry_timestamp: string; exit_timestamp: string; entry_price: number; exit_price: number };
export type ChartLabeling = {
  ranges: LabelRange[]; active: boolean; selected: string | null; pendingTime?: number;
  onPick: (candle: LabelCandle) => void;
  onSelect: (id: string) => void;
  onMove: (id: string, boundary: "entry" | "exit", candle: LabelCandle) => void;
};

/** Optional editor layer; absent on every non-labeling chart. */
export function ChartLabelOverlay({ chart, candles, editor }: { chart: IChartApi; candles: LabelCandle[]; editor: ChartLabeling }) {
  const root = useRef<SVGSVGElement>(null);
  const [, redraw] = useState(0);
  const drag = useRef<{ id: string; boundary: "entry" | "exit" } | null>(null);
  useEffect(() => {
    const update = () => redraw(value => value + 1);
    chart.timeScale().subscribeVisibleLogicalRangeChange(update);
    const observer = new ResizeObserver(update);
    if (root.current) observer.observe(root.current);
    return () => { observer.disconnect(); chart.timeScale().unsubscribeVisibleLogicalRangeChange(update); };
  }, [chart]);
  const x = (ms: number) => {
    const direct = chart.timeScale().timeToCoordinate((ms / 1000) as Time);
    if (direct !== null) return Number(direct);
    const candle = candles.find(c => c.time * 1000 <= ms && (c.endTime ?? c.time) * 1000 >= ms);
    if (!candle) return null;
    const position = chart.timeScale().timeToCoordinate(candle.time as Time);
    const duration = (candle.endTime ?? candle.time) - candle.time;
    return position === null || !duration ? null : Number(position) + chart.timeScale().options().barSpacing * (ms / 1000 - candle.time) / duration;
  };
  const pick = (clientX: number) => {
    const rect = root.current?.getBoundingClientRect();
    const localX = rect ? (clientX - rect.left) * (root.current!.clientWidth / rect.width) : 0;
    const time = chart.timeScale().coordinateToTime(localX);
    return typeof time === "number" ? candles.find(c => c.time === time) : undefined;
  };
  return <svg ref={root} className={`labeler-chart-overlay ${editor.active ? "drawing" : ""}`} aria-label="Position annotation layer"
    onPointerDown={event => { if (editor.active && event.target === event.currentTarget) { const candle = pick(event.clientX); if (candle) editor.onPick(candle); } }}
    onPointerUp={event => { const current = drag.current; drag.current = null; if (current) { const candle = pick(event.clientX); if (candle) editor.onMove(current.id, current.boundary, candle); } }}
    onPointerCancel={() => { drag.current = null; }}>
    {editor.ranges.map(range => {
      const a = x(Date.parse(range.entry_timestamp)), b = x(Date.parse(range.exit_timestamp));
      if (a === null || b === null) return null;
      return <g key={range.id} className={`labeler-range ${range.direction.toLowerCase()} ${editor.selected === range.id ? "selected" : ""}`}>
        <rect x={a} width={Math.max(2, b - a)} y={28} height="82%" onClick={() => editor.onSelect(range.id)} />
        <text x={a + 4} y={24}>{range.direction}</text>
        {([['entry', a], ['exit', b]] as const).map(([boundary, coordinate]) => <line key={boundary} x1={coordinate} x2={coordinate} y1={30} y2="92%"
          role="button" aria-label={`Drag ${range.direction.toLowerCase()} ${boundary}`} tabIndex={0}
          onPointerDown={event => { if (editor.active) return; event.stopPropagation(); editor.onSelect(range.id); drag.current = { id: range.id, boundary }; event.currentTarget.setPointerCapture(event.pointerId); }} />)}
      </g>;
    })}
    {editor.pendingTime !== undefined && x(editor.pendingTime) !== null ? <line className="labeler-pending" x1={x(editor.pendingTime)!} x2={x(editor.pendingTime)!} y1="0" y2="100%" /> : null}
  </svg>;
}
