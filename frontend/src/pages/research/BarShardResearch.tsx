import { useEffect, useRef, useState } from "react";
import { query } from "../../api/client";
import { researchApi } from "./researchApi";
import "./BarShardResearch.css";

type Job = { job: string; ticker: string; year: number };
type File = { shard: string; file: string; frames: Record<string, number> };
type Bar = { index: number; end_us: number | null; gap_us: number | null; padding: boolean; raw: number[]; valid: boolean[]; values: (number | null)[] };
type Sample = Job & { shard: string; file: string; row: number; timeframe: number; observed: number; bars: Bar[]; channels: string[]; sha256: string; basis: string; reporting_counts: string | null; samples: { row: number; timeframe: number; first_us: number; last_us: number }[] };
type Catalog = { jobs: Job[]; total: number; years: number[]; progress: { state: string; samples: number; bytes: number } };
const endpoint = "/api/research/models/bargpt/shards";
const frames = [[100000, "100ms"], [1000000, "1s"], [10000000, "10s"], [60000000, "1min"], [3600000000, "1hour"], [86400000000, "Daily"]] as const;
const frameName = (value: number) => frames.find(([n]) => n === value)?.[1] ?? String(value);
const number = (value: number | null) => value === null ? "Missing" : value.toLocaleString("en-US", { maximumFractionDigits: 8 });
const clock = (us: number | null) => us === null ? "Padding" : new Date(us / 1000).toISOString().replace("T", " ").replace("Z", "") + String(us % 1000).padStart(3, "0") + " UTC";

/** Receipt-backed raw sample inspector; no chart-side aggregation or imputation. */
export function BarShardResearch() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [search, setSearch] = useState(""); const [year, setYear] = useState(0); const [offset, setOffset] = useState(0);
  const [job, setJob] = useState(""); const [files, setFiles] = useState<File[]>([]); const [fileKey, setFileKey] = useState("");
  const [timeframe, setTimeframe] = useState(0); const [sample, setSample] = useState<Sample | null>(null);
  const [selected, setSelected] = useState(0); const [family, setFamily] = useState("all");
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const serial = useRef(0);
  useEffect(() => {
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      researchApi<Catalog>(endpoint + "/catalog" + query({ search, year, offset }), { signal: abort.signal })
        .then(setCatalog).catch(e => { if (!abort.signal.aborted) setError(String(e.message)); });
    }, 200);
    return () => { window.clearTimeout(timer); abort.abort(); };
  }, [search, year, offset]);
  useEffect(() => {
    if (!job) return;
    const abort = new AbortController(); setFiles([]);
    researchApi<{ shards: File[] }>(endpoint + "/files" + query({ job }), { signal: abort.signal })
      .then(r => setFiles(r.shards)).catch(e => { if (!abort.signal.aborted) setError(e.message); });
    return () => abort.abort();
  }, [job]);
  async function load(randomize: boolean, row?: number, randomJob = false) {
    const request = ++serial.current; setBusy(true); setError("");
    const [shard, file] = fileKey.split("/");
    try {
      const result = await researchApi<Sample>(endpoint + "/sample" + query({ job: randomJob ? "" : job,
        shard, file, row: row ?? 0, timeframe, randomize, search, year }), { timeoutMs: 120000 });
      if (request !== serial.current) return;
      setSample(result); setSelected(0); setJob(result.job); setFileKey(`${result.shard}/${result.file}`);
    } catch (e) { if (request === serial.current) setError((e as Error).message); }
    finally { if (request === serial.current) setBusy(false); }
  }
  const chosen = sample?.bars[selected];
  const matchingFiles = files.filter(f => !timeframe || f.frames[String(timeframe)] > 0);
  const selectionMatches = sample?.job === job && `${sample.shard}/${sample.file}` === fileKey && (!timeframe || sample.timeframe === timeframe);
  return <div className="research-page bar-shard-research">
    <header className="research-heading"><div><h1>BarGPT shard inspection</h1><p>Published v5 trade-driven samples · raw stored values, evidence masks and actual clocks</p></div>
      {catalog && <span className="research-muted">{catalog.progress.state} · {(catalog.progress.bytes / 1e9).toFixed(2)} GB · {catalog.progress.samples.toLocaleString()} samples</span>}
    </header>
    <section className="research-selection" aria-label="Shard selection">
      <div className="research-controls">
        <label>Ticker search<input aria-label="Ticker search" disabled={busy} value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} placeholder="Any ticker" /></label>
        <label>Year<select aria-label="Year" disabled={busy} value={year} onChange={e => { setYear(Number(e.target.value)); setOffset(0); }}><option value={0}>All years</option>{catalog?.years.map(y => <option key={y}>{y}</option>)}</select></label>
        <label>Timeframe<select aria-label="Timeframe" disabled={busy} value={timeframe} onChange={e => { setTimeframe(Number(e.target.value)); setFileKey(""); }}><option value={0}>All timeframes</option>{frames.map(([n, label]) => <option key={n} value={n}>{label}</option>)}</select></label>
        <button className="button primary" disabled={busy} onClick={() => void load(true, undefined, true)}>{busy ? "Loading…" : "Random sample"}</button>
      </div>
      <div className="research-controls">
        <label>Ticker / year<select aria-label="Ticker / year" disabled={busy} value={job} onChange={e => { setJob(e.target.value); setFileKey(""); }}><option value="">Select a ticker-year</option>{job && !catalog?.jobs.some(j => j.job === job) && <option value={job}>{sample?.ticker ?? job} · {sample?.year}</option>}{catalog?.jobs.map(j => <option key={j.job} value={j.job}>{j.ticker} · {j.year}</option>)}</select></label>
        <label>Published shard<select aria-label="Published shard" disabled={busy} value={fileKey} onChange={e => setFileKey(e.target.value)}><option value="">Select a shard file</option>{fileKey && !matchingFiles.some(f => `${f.shard}/${f.file}` === fileKey) && <option value={fileKey}>{fileKey}</option>}{matchingFiles.map(f => <option key={`${f.shard}/${f.file}`} value={`${f.shard}/${f.file}`}>{f.shard} / {f.file}</option>)}</select></label>
        <button className="button secondary" disabled={busy || !fileKey} onClick={() => void load(true)}>Load shard</button>
        <button className="button secondary compact" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous tickers</button>
        <button className="button secondary compact" disabled={!catalog || offset + 100 >= catalog.total} onClick={() => setOffset(offset + 100)}>Next tickers</button>
      </div>
      {job && files.length > 0 && !matchingFiles.length && <p className="research-muted">No published files for this timeframe yet. Hourly/daily files appear when the ticker-year completes.</p>}
      <p className="research-muted">{catalog?.total.toLocaleString() ?? "Loading"} materialized ticker-years. Random selection chooses a matching published file and row; it is not a uniform draw over the entire corpus.</p>
    </section>
    {error && <p role="alert" className="bar-shard-error">{error}</p>}
    {!sample ? <div className="research-empty">Choose a shard or load a random sample to inspect its 64 bar slots.</div> : <>
      <section className="bar-shard-chart-panel" aria-label="Shard chart">
        <div className="research-controls"><h2>{sample.ticker} · {sample.year} · {frameName(sample.timeframe)}</h2>
          <label>Sample row<select aria-label="Sample row" value={sample.row} disabled={busy || !selectionMatches} onChange={e => void load(false, Number(e.target.value))}>{sample.samples.map(r => <option key={r.row} value={r.row}>#{r.row} · {frameName(r.timeframe)} · {clock(r.first_us)}</option>)}</select></label>
          <label>Price series<select aria-label="Price series" value={family} onChange={e => setFamily(e.target.value)}><option value="all">Trades + bid + ask</option><option value="trade">Trades</option><option value="bid">Bid</option><option value="ask">Ask</option></select></label>
        </div>
        <p className="research-muted">{sample.observed} observed bars · {64 - sample.observed} padded · {sample.basis}. Horizontal axis: bar index; actual time gaps appear below. Chart values are not aggregated.</p>
        <ShardCandles bars={sample.bars} family={family} selected={selected} onSelect={setSelected} />
      </section>
      {chosen && <section className="bar-shard-inspector" aria-label="Bar details"><div><h2>Bar {selected + 1} / 64 {chosen.padding ? "· Padding" : ""}</h2><p>{clock(chosen.end_us)}</p><p>Stored end_us: {chosen.end_us ?? "0 (padding)"} · Gap: {chosen.gap_us === null ? "No predecessor" : `${number(chosen.gap_us / 1e6)} seconds`}</p>
        <p className="research-muted">Missing evidence is distinct from an observed zero. No price is invented for a missing channel.</p>
        <details><summary>Publication evidence</summary><p className="research-source">{sample.job}/{sample.shard}/{sample.file} · row {sample.row}</p><p className="research-source">SHA-256: {sample.sha256}</p><p>Source trade counts (total, unknown-status, delayed-status, structurally valid; status is diagnostic only): {sample.reporting_counts ?? "See session receipts for this cross-session context"}</p></details>
      </div><div className="bar-shard-fields"><table><thead><tr><th>Channel</th><th>Value</th><th>Valid</th><th>Stored value</th></tr></thead><tbody>{sample.channels.map((name, i) => <tr key={name}><th scope="row">{name}</th><td>{number(chosen.values[i])}</td><td>{chosen.valid[i] ? "Yes" : "No"}</td><td>{String(chosen.raw[i])}</td></tr>)}</tbody></table></div></section>}
      <section className="bar-shard-rows" aria-label="All bar clocks"><h2>Bar clocks and closing prices</h2><div className="bar-shard-table-scroll"><table><thead><tr><th>Bar</th><th>Actual end time</th><th>Gap (s)</th><th>Trade close</th><th>Bid close</th><th>Ask close</th><th>Halt open / close / starts</th></tr></thead><tbody>{sample.bars.map(b => <tr key={b.index} aria-selected={b.index === selected}><td><button className="research-ticker-button" onClick={() => setSelected(b.index)}>{b.index + 1}</button></td><td>{clock(b.end_us)}</td><td>{b.gap_us === null ? "—" : number(b.gap_us / 1e6)}</td><td>{number(b.values[3])}</td><td>{number(b.values[9])}</td><td>{number(b.values[15])}</td><td>{b.values.slice(18).map(number).join(" / ")}</td></tr>)}</tbody></table></div></section>
    </>}
  </div>;
}

/** Draw only available OHLC fields; missing bodies never become synthetic candles. */
function ShardCandles({ bars, family, selected, onSelect }: { bars: Bar[]; family: string; selected: number; onSelect: (index: number) => void }) {
  const families = [{ name: "trade", offset: 0 }, { name: "bid", offset: 6 }, { name: "ask", offset: 12 }].filter(f => family === "all" || family === f.name);
  const prices = bars.flatMap(b => families.flatMap(f => b.values.slice(f.offset, f.offset + 4).filter((v): v is number => v !== null)));
  const low = Math.min(...prices), high = Math.max(...prices); const range = Math.max(high - low, high * .0001);
  const y = (v: number) => 300 - (v - low) / range * 260;
  return <><div className="bar-shard-legend">{families.map(f => <span key={f.name} className={`shard-${f.name}`}>● {f.name}</span>)}<span>Shaded slots: padding · click a bar for exact values</span></div>
    {!prices.length && <p className="research-muted">No valid prices in the selected series. Raw masks remain available below.</p>}
    <svg className="bar-shard-chart" viewBox="0 0 1200 350" role="group" aria-label="64-bar candlestick inspection">
      {prices.length > 0 && [0, 1, 2, 3, 4].map(i => { const value = low + range * i / 4; return <g key={i} className="bar-shard-grid"><line x1={65} x2={1190} y1={y(value)} y2={y(value)} /><text x={60} y={y(value) + 4} textAnchor="end">{value.toLocaleString("en-US", { maximumSignificantDigits: 6 })}</text></g>; })}
      {bars.map(b => { const x = 75 + b.index * 17.4; return <g key={b.index} role="button" tabIndex={0} aria-label={`Inspect bar ${b.index + 1}${b.padding ? ", padding" : ""}`} aria-pressed={selected === b.index} onClick={() => onSelect(b.index)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(b.index); } }}>
        <title>{`Bar ${b.index + 1} · ${clock(b.end_us)}`}</title><rect x={x - 8} y={20} width={17} height={292} className={`bar-shard-hit${b.padding ? " padded" : ""}${b.index === selected ? " selected" : ""}`} />
        {prices.length > 0 && families.map((f, j) => { const [o, h, l, c] = b.values.slice(f.offset, f.offset + 4); const center = x + (j - (families.length - 1) / 2) * 4; return <g key={f.name} className={`shard-${f.name}`} pointerEvents="none">{h !== null && l !== null && <line x1={center} x2={center} y1={y(h)} y2={y(l)} />}{o !== null && c !== null ? <rect x={center - 1.5} width={3} y={Math.min(y(o), y(c))} height={Math.max(1, Math.abs(y(o) - y(c)))} /> : c !== null && <circle cx={center} cy={y(c)} r={1.5} />}</g>; })}
        {(b.index % 8 === 0 || b.index === 63) && <text x={x} y={335} textAnchor="middle" className="bar-shard-axis">{b.index + 1}</text>}
      </g>; })}
    </svg></>;
}
