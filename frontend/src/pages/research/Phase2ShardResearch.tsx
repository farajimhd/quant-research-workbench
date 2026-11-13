import { useEffect, useRef, useState } from "react";
import { query } from "../../api/client";
import { researchApi } from "./researchApi";
import { Phase2GroundTruth } from "./Phase2GroundTruth";
import { ShardCandles } from "./BarShardResearch";
import "./BarShardResearch.css";
import "./Phase2ShardResearch.css";

type Stream = { stream: string; ticker: string; year: number; liquid: boolean };
type Unit = {
  unit: number;
  blocks: number;
  begin: string;
  end: string;
  storage: string;
};
type View = { view: number; available: boolean; last_index: number };
type Summary = {
  frame: number;
  timeframe: number;
  context_bars: number;
  views: View[];
};
type Bar = {
  index: number;
  end_us: number;
  gap_us: number | null;
  padding: boolean;
  raw: number[];
  valid: boolean[];
  values: (number | null)[];
  time_values: number[];
};
type Sample = Stream & {
  unit: number;
  block: number;
  block_count: number;
  decision: number;
  decisions: number[];
  origin_us: number;
  frame: number;
  view: number | null;
  summaries: Summary[];
  channels: string[];
  time_channels: string[];
  bars: Bar[];
  block_bars: Bar[];
  block_origin_start_us: number;
  block_origin_end_us: number;
  split: number;
  actions: {
    execution_date: string;
    split_from: number;
    split_to: number;
    effective_by_origin: boolean;
  }[];
  receipt_hash: string;
  plan_hash: string;
  producer_revision: string;
  view_version: string;
};
type Catalog = {
  streams: Stream[];
  total: number;
  years: number[];
  progress: { state: string; bytes: number; units: number; updated: number };
};
const endpoint = "/api/research/models/bargpt/phase2";
const frameCodes = ["1s", "10s", "1m", "1h", "1d"];
const frames = ["1s", "10s", "1min", "1hour", "Daily"];
const clock = (us: number) =>
  new Date(us / 1000).toLocaleString("en-CA", { timeZone: "America/New_York", hour12: false }) + " NY";
const number = (value: number | null) =>
  value === null
    ? "Missing"
    : value.toLocaleString("en-US", { maximumFractionDigits: 8 });

/** Inspect one saved causal decision at a time; all transformations stay in the backend. */
export function Phase2ShardResearch() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [search, setSearch] = useState("");
  const [year, setYear] = useState(0);
  const [liquid, setLiquid] = useState(-1);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [stream, setStream] = useState("");
  const [units, setUnits] = useState<Unit[]>([]);
  const [unit, setUnit] = useState(-1);
  const [sample, setSample] = useState<Sample | null>(null);
  const [blockDraft, setBlockDraft] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(63);
  const [family, setFamily] = useState("all");
  const [truthFrame, setTruthFrame] = useState("1s");
  const truthSelection = useRef(0);
  const serial = useRef(0);

  // Debounced discovery never recursively scans payloads or polls the training process.
  useEffect(() => {
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      researchApi<Catalog>(
        endpoint + "/catalog" + query({ search, year, liquid, offset }),
        { signal: abort.signal },
      )
        .then(setCatalog)
        .catch((e) => {
          if (!abort.signal.aborted) setError(e.message);
        });
    }, 200);
    return () => {
      window.clearTimeout(timer);
      abort.abort();
    };
  }, [search, year, liquid, offset, refresh]);
  useEffect(() => {
    if (!stream) return;
    const abort = new AbortController();
    setUnits([]);
    researchApi<{ units: Unit[] }>(endpoint + "/units" + query({ stream }), {
      signal: abort.signal,
    })
      .then((result) => setUnits(result.units))
      .catch((e) => {
        if (!abort.signal.aborted) setError(e.message);
      });
    return () => abort.abort();
  }, [stream]);

  async function load(
    randomize = false,
    overrides: Partial<{
      stream: string;
      unit: number;
      block: number;
      decision: number;
      frame: number;
      view: number;
    }> = {},
  ) {
    const request = ++serial.current;
    const truthRequest = truthSelection.current;
    setBusy(true);
    setError("");
    try {
      const result = await researchApi<Sample>(
        endpoint +
          "/sample" +
          query({
            stream,
            unit,
            block: 0,
            decision: 4095,
            frame: 0,
            view: -1,
            search,
            year,
            liquid,
            ...overrides,
            randomize,
          }),
        { timeoutMs: 120000 },
      );
      if (request !== serial.current) return;
      setSample(result);
      if (truthRequest === truthSelection.current) setTruthFrame(frameCodes[result.frame]);
      setStream(result.stream);
      setUnit(result.unit);
      setSelected(result.block_bars.length - 1);
      setBlockDraft(result.block);
    } catch (exception) {
      if (request === serial.current) setError((exception as Error).message);
    } finally {
      if (request === serial.current) setBusy(false);
    }
  }
  // Preserve the current block/origin while switching timeframe or overlapping view.
  function inspect(
    overrides: Partial<{
      block: number;
      decision: number;
      frame: number;
      view: number;
    }>,
  ) {
    if (overrides.frame !== undefined) {
      truthSelection.current++;
      setTruthFrame(frameCodes[overrides.frame]);
    }
    if (sample)
      void load(false, {
        stream: sample.stream,
        unit: sample.unit,
        block: sample.block,
        decision: sample.decision,
        frame: sample.frame,
        view: sample.view ?? -1,
        ...overrides,
      });
  }
  const chosen = sample?.block_bars[selected];
  const chartBars = sample?.block_bars ?? [];
  const selectionMatches = sample?.stream === stream && sample?.unit === unit;
  return (
    <div
      className="research-page bar-shard-research phase2-shard-research"
      aria-busy={busy}
    >
      <header className="research-heading">
        <div>
          <h1>BarGPT phase 2 data</h1>
          <p>
            Saved bars and canonical events
          </p>
        </div>
        {catalog && (
          <span className="research-muted">
            {catalog.progress.state} ·{" "}
            {(catalog.progress.bytes / 1e9).toFixed(2)} GB ·{" "}
            {catalog.progress.units.toLocaleString()} sealed units
          </span>
        )}
      </header>
      <section className="research-selection" aria-label="Phase 2 selection">
        <div className="research-controls">
          <label>
            Ticker search
            <input
              aria-label="Phase 2 ticker search"
              value={search}
              disabled={busy}
              placeholder="Any ticker"
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
            />
          </label>
          <label>
            Year
            <select
              aria-label="Phase 2 year"
              value={year}
              disabled={busy}
              onChange={(e) => {
                setYear(Number(e.target.value));
                setOffset(0);
              }}
            >
              <option value={0}>All years</option>
              {catalog?.years.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Liquidity
            <select
              aria-label="Phase 2 liquidity"
              value={liquid}
              disabled={busy}
              onChange={(e) => {
                setLiquid(Number(e.target.value));
                setOffset(0);
              }}
            >
              <option value={-1}>Both groups</option>
              <option value={1}>Liquid</option>
              <option value={0}>Less liquid</option>
            </select>
          </label>
          <button
            className="button primary"
            disabled={busy}
            onClick={() => void load(true, { stream: "", unit: -1 })}
          >
            {busy ? "Loading…" : "Random block"}
          </button>
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => setRefresh((value) => value + 1)}
          >
            Refresh catalog
          </button>
        </div>
        <div className="research-controls">
          <label>
            Ticker / year
            <select
              aria-label="Phase 2 ticker / year"
              value={stream}
              disabled={busy}
              onChange={(e) => {
                setStream(e.target.value);
                setUnit(-1);
              }}
            >
              <option value="">Select a ticker-year</option>
              {stream &&
                !catalog?.streams.some((row) => row.stream === stream) && (
                  <option value={stream}>
                    {sample?.ticker ?? stream} · {sample?.year}
                  </option>
                )}
              {catalog?.streams.map((row) => (
                <option key={row.stream} value={row.stream}>
                  {row.ticker} · {row.year} ·{" "}
                  {row.liquid ? "liquid" : "less liquid"}
                </option>
              ))}
            </select>
          </label>
          <label>
            Saved unit
            <select
              aria-label="Phase 2 saved unit"
              value={unit}
              disabled={busy}
              onChange={(e) => setUnit(Number(e.target.value))}
            >
              <option value={-1}>Any sealed unit</option>
              {units.map((row) => (
                <option key={row.unit} value={row.unit}>
                  {row.begin} · {row.blocks} blocks
                </option>
              ))}
            </select>
          </label>
          <button
            className="button secondary"
            disabled={busy || !stream}
            onClick={() => void load()}
          >
            Load block
          </button>
          <button
            className="button secondary compact"
            disabled={busy || offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 100))}
          >
            Previous tickers
          </button>
          <button
            className="button secondary compact"
            disabled={busy || !catalog || offset + 100 >= catalog.total}
            onClick={() => setOffset(offset + 100)}
          >
            Next tickers
          </button>
        </div>
      </section>
      {error && (
        <p className="bar-shard-error" role="alert">
          {error}
        </p>
      )}
      {!sample ? (
        <div className="research-empty">
          Choose a saved block or load a random block to review phase-two data.
        </div>
      ) : (
        <>
          <Phase2GroundTruth stream={sample.stream} unit={sample.unit} ticker={sample.ticker}
            timeframe={truthFrame} origin={sample.origin_us} selectedEnd={chosen?.end_us} selectedIndex={selected} selectionFrame={frameCodes[sample.frame]}
            onTimeframeChange={value => { truthSelection.current++; setTruthFrame(value); const frame = frameCodes.indexOf(value); if (frame >= 0) inspect({ frame, view: -1 }); }} />
          <section
            className="bar-shard-chart-panel"
            aria-label="Phase 2 decision"
          >
            <div className="research-controls">
              <h2>
                Saved block · {frames[sample.frame]} · {sample.ticker} · {sample.unit}
              </h2>
              <label>
                Block
                <input
                  aria-label="Phase 2 block"
                  type="number"
                  min={0}
                  max={sample.block_count - 1}
                  value={blockDraft}
                  disabled={busy || !selectionMatches}
                  onChange={(e) => setBlockDraft(Number(e.target.value))}
                />
              </label>
              <button
                className="button secondary"
                disabled={
                  busy ||
                  !selectionMatches ||
                  !Number.isInteger(blockDraft) ||
                  blockDraft < 0 ||
                  blockDraft >= sample.block_count
                }
                onClick={() => inspect({ block: blockDraft })}
              >
                Go to block
              </button>
              <label>
                Decision origin
                <select
                  aria-label="Decision origin"
                  value={sample.decision}
                  disabled={busy}
                  onChange={(e) =>
                    inspect({ decision: Number(e.target.value), view: -1 })
                  }
                >
                  {sample.decisions.map((value, i) => (
                    <option key={i} value={i}>
                      #{i + 1} · {clock(value)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className="phase2-origin">
              <strong>
                1s close / causal cutoff: {clock(sample.origin_us)}
              </strong>
            </p>
            <div className="research-controls">
              <label>
                Timeframe
                <select
                  aria-label="Phase 2 timeframe"
                  value={sample.frame}
                  disabled={busy}
                  onChange={(e) =>
                    inspect({ frame: Number(e.target.value), view: -1 })
                  }
                >
                  {sample.summaries.map((row) => (
                    <option key={row.frame} value={row.frame}>
                      {frames[row.frame]} ·{" "}
                      {row.views.filter((view) => view.available).length}/
                      {row.views.length} views
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Price series
                <select
                  aria-label="Phase 2 price series"
                  value={family}
                  onChange={(e) => setFamily(e.target.value)}
                >
                  <option value="all">Bid · Ask · Trade</option>
                  <option value="trade">Trades</option>
                  <option value="bid">Bid</option>
                  <option value="ask">Ask</option>
                </select>
              </label>
            </div>
            {sample.bars.length ? (
              <>
                <p className="research-muted">
                  {frames[sample.frame]} · {clock(chartBars[0].end_us)} →{" "}
                  {clock(chartBars[chartBars.length - 1].end_us)} · {chartBars.length.toLocaleString()} stored bars · 4,096 origins · context before {clock(sample.block_origin_start_us)}. Scroll horizontally to inspect the whole block.
                </p>
                <div style={{ overflowX: "auto", maxWidth: "100%" }}><ShardCandles
                  triplets
                  bars={chartBars}
                  family={family}
                  selected={selected}
                  onSelect={index => { setSelected(index); truthSelection.current++; setTruthFrame(frameCodes[sample.frame]); }}
                /></div>
              </>
            ) : (
              <div className="research-empty">
                This decision has insufficient history for a full 64-bar view at
                this timeframe. No bars are invented.
              </div>
            )}
          </section>
          <details className="phase2-details"><summary>Values, masks and provenance</summary>
          <p className="research-muted">{sample.split === 0 ? "Training-era candidate" : sample.split === 1 ? "Validation-era candidate" : "Purged: context crosses split cutoff"} · {sample.liquid ? "Liquid" : "Less liquid"} · candidate status does not imply balanced-release inclusion.</p>
          {chosen && (
            <section
              className="bar-shard-inspector"
              aria-label="Phase 2 bar details"
            >
              <div>
                <h2>Bar {selected + 1} / {chartBars.length}</h2>
                <p>{clock(chosen.end_us)}</p>
                <p>
                  Gap:{" "}
                  {chosen.gap_us === null
                    ? "No predecessor in this window"
                    : `${number(chosen.gap_us / 1e6)} seconds`}
                </p>
                <p>
                  End ≤ origin:{" "}
                  {chosen.end_us <= sample.origin_us ? "Yes" : "NO — invalid"}
                </p>
                <p className="research-muted">
                  Market values are shown before training scaling. Invalid
                  evidence is missing, never an observed zero.
                </p>
                <details>
                  <summary>Corporate actions and provenance</summary>
                  <p className="research-source">
                    Stream {sample.stream} · block {sample.block} · decision{" "}
                    {sample.decision}
                  </p>
                  <p className="research-source">
                    Receipt SHA-256: {sample.receipt_hash}
                  </p>
                  <p className="research-source">Plan: {sample.plan_hash}</p>
                  <p>
                    {sample.view_version} · producer {sample.producer_revision}
                  </p>
                  {sample.actions.length ? (
                    sample.actions.map((action, i) => (
                      <p key={i}>
                        {action.execution_date} · {action.split_from}:
                        {action.split_to} ·{" "}
                        {action.effective_by_origin
                          ? "effective; rebases older bars"
                          : "future; not applied"}
                      </p>
                    ))
                  ) : (
                    <p>No split actions in the frozen ticker catalog.</p>
                  )}
                </details>
              </div>
              <div className="bar-shard-fields">
                <table>
                  <thead>
                    <tr>
                      <th>Market channel</th>
                      <th>Stored · session basis</th>
                      <th>Valid value</th>
                      <th>Valid</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sample.channels.map((name, i) => (
                      <tr key={name}>
                        <th scope="row">{name}</th>
                        <td>{number(chosen.raw[i])}</td>
                        <td>{number(chosen.values[i])}</td>
                        <td>{chosen.valid[i] ? "Yes" : "No"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <h3>Derived UTC inputs</h3>
                <table>
                  <thead>
                    <tr>
                      <th>Time channel</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sample.time_channels.map((name, i) => (
                      <tr key={name}>
                        <th scope="row">{name}</th>
                        <td>{number(chosen.time_values[i])}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          <section className="bar-shard-rows" aria-label="Phase 2 bar clocks">
            <h2>Window clocks and closing prices</h2>
            <div className="bar-shard-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Bar</th>
                    <th>Close (NY)</th>
                    <th>Gap (s)</th>
                    <th>Trade close</th>
                    <th>Bid close</th>
                    <th>Ask close</th>
                  </tr>
                </thead>
                <tbody>
                  {chartBars.map((bar) => (
                    <tr key={bar.index}>
                      <td>
                        <button
                          className="button secondary compact"
                          onClick={() => { setSelected(bar.index); truthSelection.current++; setTruthFrame(frameCodes[sample.frame]); }}
                        >
                          {bar.index + 1}
                        </button>
                      </td>
                      <td>{clock(bar.end_us)}</td>
                      <td>
                        {bar.gap_us === null ? "—" : number(bar.gap_us / 1e6)}
                      </td>
                      <td>{number(bar.values[3])}</td>
                      <td>{number(bar.values[9])}</td>
                      <td>{number(bar.values[15])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          </details>
        </>
      )}
    </div>
  );
}
