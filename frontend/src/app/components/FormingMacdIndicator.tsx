import { useEffect, useMemo, useRef, useState } from 'react';
import { usePollingTask } from '../hooks/usePollingTask';
import { Modal } from './Modal';
import { Button } from './Button';
import { MACD_DIFFERENCE_ID, MACD_DIFFERENCE_PANE, MACD_TIMEFRAMES, loadMacdSource, projectFormingMacd,
  type MacdSample, type MacdSource, type MacdTimeframe } from '../../features/canvas/formingMacd';
import './formingMacd.css';

type Settings = { enabled: boolean; timeframes: MacdTimeframe[] };
const defaults: Settings = { enabled: false, timeframes: ['1m', '5m'] };
type Loaded = { source?: MacdSource; error?: string; retryAfter?: number; at: number };
const palette = ['blue', 'green', 'amber', 'violet', 'rose', 'cyan', 'orange'];

export function useFormingMacd(storageKey: string, symbol: string, chartTimeframe: string, chartSeconds: number | null,
  candles: MacdSample[], asOf?: string, payloadTimeframe?: string, splitAdjusted = false) {
  const [stored, setStored] = useState({ key: storageKey, ...defaults });
  const [open, setOpen] = useState(false);
  useEffect(() => {
    try {
      const value = JSON.parse(localStorage.getItem(`${storageKey}.forming-macd`) || '{}');
      const timeframes = MACD_TIMEFRAMES.filter(tf => Array.isArray(value.timeframes) && value.timeframes.includes(tf));
      setStored({ key: storageKey, enabled: value.enabled === true, timeframes: timeframes.length ? timeframes : defaults.timeframes });
    } catch { setStored({ key: storageKey, ...defaults }); }
  }, [storageKey]);
  const settings = stored.key === storageKey ? stored : { key: storageKey, ...defaults };
  const change = (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch }; setStored(next);
    try { localStorage.setItem(`${storageKey}.forming-macd`, JSON.stringify(next)); } catch { /* Retain in-memory configuration. */ }
  };
  const first = candles[0]?.time;
  const tail = candles.at(-1);
  const cursor = asOf ? Date.parse(asOf) / 1000 : Date.now() / 1000;
  const cutoff = Math.min(cursor, Date.now() / 1000, tail?.endTime ?? (tail && chartSeconds !== null ? tail.time + chartSeconds : cursor));
  const selection = settings.timeframes.join(',');
  const identity = `${storageKey}|${symbol}|${chartTimeframe}|${first}|${selection}|${splitAdjusted}`;
  const [loaded, setLoaded] = useState<{ key: string; values: Partial<Record<MacdTimeframe, Loaded>> }>({ key: '', values: {} });
  const cache = useRef(loaded);
  const matchingPayload = !payloadTimeframe || payloadTimeframe === chartTimeframe;
  usePollingTask({ enabled: settings.enabled && first !== undefined && matchingPayload && Number.isFinite(cutoff) && !splitAdjusted,
    restartKey: identity, intervalMs: 1000, initialDelayMs: 0,
    task: async signal => {
      for (const tf of settings.timeframes) {
        const existing = cache.current.key === identity ? cache.current.values[tf] : undefined;
        if (existing?.source && existing.at <= cutoff && existing.source.through >= cutoff) continue;
        if (existing?.error && (existing.retryAfter ?? 0) > Date.now()) continue;
        // Back off failed reads; one active request per chart avoids a
        // request fan-out across all selected timeframes during recovery.
        let result: Loaded;
        try { result = { source: await loadMacdSource(symbol, tf, first!, cutoff, signal), at: cutoff }; }
        catch (error) {
          if (signal.aborted) return;
          result = { error: error instanceof Error ? error.message : String(error), retryAfter: Date.now() + 15000, at: cutoff };
        }
        if (signal.aborted) return;
        const next = { key: identity, values: { ...(cache.current.key === identity ? cache.current.values : {}), [tf]: result } };
        cache.current = next; setLoaded(next);
      }
    },
  });
  const values = loaded.key === identity ? loaded.values : {};
  const projected = useMemo(() => settings.timeframes.map(tf => {
    const item = loaded.key === identity ? loaded.values[tf] : undefined;
    const result = item?.source && matchingPayload && !splitAdjusted
      ? projectFormingMacd(candles, item.source, chartSeconds, cutoff) : { points: [], missing: 0 };
    return { tf, ...result };
  }), [selection, loaded, identity, candles, chartSeconds, cutoff, matchingPayload, splitAdjusted]);
  const status = (tf: MacdTimeframe) => {
    if (splitAdjusted) return 'Unavailable: adjusted chart prices cannot be mixed with unadjusted intraday MACD.';
    if (!matchingPayload) return 'Waiting for chart timeframe';
    if (values[tf]?.error) return values[tf]!.error!;
    if (!values[tf]?.source) return 'Loading canonical MACD…';
    const result = projected.find(row => row.tf === tf)!;
    return result.missing ? `${result.points.length - result.missing} points · ${result.missing} unavailable (seed or source cutoff)` : `${result.points.length} points`;
  };
  const series = useMemo(() => settings.enabled ? projected.map(({ tf, points }) => ({
    column: `forming_macd_difference_${tf}`, displayItemId: MACD_DIFFERENCE_ID,
    label: `MACD − signal · ${tf}`, axisTitle: `MACD Δ ${tf}`, paneKey: MACD_DIFFERENCE_PANE,
    chartRole: 'forming-macd-difference', style: 'line' as const, lineWidth: 2,
    emptyMessage: splitAdjusted || values[tf]?.error ? 'Unavailable' : values[tf]?.source ? 'Seed unavailable' : 'Loading…',
    color: `var(--canvas-link-${palette[MACD_TIMEFRAMES.indexOf(tf) % palette.length]})`,
    lineStyle: MACD_TIMEFRAMES.indexOf(tf) >= palette.length ? 'dashed' as const : 'solid' as const,
    data: points,
  })) : [], [settings.enabled, projected]);
  const editor = <div className="forming-macd-settings">
    <p>Select multiple source timeframes to plot together. The chart timeframe controls the sampling axis; MACD uses 12/26/9 source candles.</p>
    <fieldset><legend>MACD source timeframes</legend><div className="forming-macd-timeframes">
      {MACD_TIMEFRAMES.map(tf => <label key={tf}><input type="checkbox" aria-label={`MACD source ${tf}`}
        checked={settings.timeframes.includes(tf)} disabled={settings.timeframes.length === 1 && settings.timeframes.includes(tf)}
        onChange={event => change({ timeframes: MACD_TIMEFRAMES.filter(candidate => candidate === tf ? event.target.checked : settings.timeframes.includes(candidate)) })} />{tf}</label>)}
    </div></fieldset>
    <p>Each line is forming MACD minus forming signal in price units, sampled at the chart candle’s close or current cursor. Previews never compound or borrow a future candle close. QMD’s completed MACD history supplies the seed.</p>
    <p>Daily and longer source MACD is currently unavailable from QMD. Split-adjusted chart prices require a matching MACD authority.</p>
    {settings.enabled && <ul className="forming-macd-status" aria-live="polite">{settings.timeframes.map(tf => <li key={tf}><strong>{tf}</strong>: {status(tf)}</li>)}</ul>}
    <div><Button type="button" onClick={() => { change({ enabled: false }); setOpen(false); }}>Remove oscillator</Button></div>
  </div>;
  return { enabled: settings.enabled, series, remove: () => change({ enabled: false }),
    checkbox: <div className="forming-macd-menu-row"><label className="chart-setting-row"><span>Multi-timeframe MACD difference</span>
      <input type="checkbox" aria-label="Multi-timeframe MACD difference" checked={settings.enabled} onChange={event => change({ enabled: event.target.checked })} /></label>
      <button className="toolbar-button" type="button" onClick={() => setOpen(true)} aria-label="Configure multi-timeframe MACD">Configure</button></div>,
    controls: <>{settings.enabled && <button className="toolbar-button" type="button" onClick={() => setOpen(true)} title={settings.timeframes.map(tf => `${tf}: ${status(tf)}`).join('\n')}>MACD difference · {selection}{settings.timeframes.some(tf => values[tf]?.error) || splitAdjusted ? ' · Unavailable' : settings.timeframes.some(tf => !values[tf]?.source) ? ' · Loading' : ''}</button>}
      {open && <Modal title="Multi-timeframe MACD difference" onClose={() => setOpen(false)}>{editor}</Modal>}</>,
  };
}
