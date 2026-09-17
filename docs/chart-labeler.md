# Chart Labeler

Open **Trading Workspaces → Labeler** (`#labeler`). This opens a dedicated, persisted **Labeler Canvas**, containing the registered **Labeler** container. It uses the shared Canvas renderer, layout, management, fullscreen, and container library. The same container can be added to other canvases. Its scanner is on the left and chart on the right; compact views stack when necessary.

The initial date comes from the existing QMD covered-session authority, rather than assuming yesterday is available. A selected date persists per container. Select regular or extended hours. Every ticker selection starts with the **1h** view. Use **No opportunity & next** above the chart to finish a negative review, or switch to a finer view (including **100ms**) and select **Long range** or **Short range**. Click the entry candle, then the exit candle, then **Submit range**. The selected open and close are reference prices, not executable fills.

The historical table uses the point-in-time tradable reference universe. Float retains the reference authority's reported/estimated/missing status. **Session %** is last trade versus first trade within the selected session; volume covers that same session. Neither is an earlier intraday feature. Missing facts remain unavailable. The table is searchable, sortable, filtered by review status, and paged in groups of 100.

## Annotation contract

- A range stores instrument identity, direction, exact UTC entry/exit timestamps, annotation timeframe, reference prices, source evidence, and revision. Timestamp precision is milliseconds; candle indexes are never persisted.
- Entry is the first candle's opening boundary. Exit is the last candle's ending boundary. Exposure is half-open `[entry, exit)`. A single candle can contain both boundary events.
- Multiple nonoverlapping long/short ranges are allowed. Adjacent intervals are allowed. Session-crossing endpoints are rejected; use a finer candle at a partial hourly boundary.
- Changing the view never resamples an existing range. Endpoint edits require its original annotation timeframe. Drag a handle or use **Choose new entry/exit** and click a candle. Escape cancels unfinished selection.
- Review identity is label set, historical ticker, session date, and session scope—not display timeframe. One hourly negative review covers the session. Each range retains its own annotation timeframe.
- Review states: unreviewed, in progress, completed with ranges, and no opportunity. Edits reopen completed reviews. Unfinished reviews never create negative examples.

## Persistence and coverage

The backend stores `chart-labeler/labels.sqlite3` under `project_runtime_root()` (normally `D:\TradingML\runtimes\quant-research-workbench`). **Every Submit range request commits immediately**, before session completion. `label_ranges` contains one current row per interval, keyed by review identity and range ID. This projection and its review snapshot are updated in one SQLite transaction; immutable `revisions` retain edits/deletions, and content-addressed `evidence` retains chart provenance. Existing review-only data is migrated without changing timestamps or revisions. Optimistic revisions reject concurrent overwrites; an identical retry is idempotent. Failed saves retain the draft and block navigation/container closing until retry or explicit discard. No label data belongs in the source repository.

Market-event reads go through QMD History and the canonical imported authority. Minute/hour views fit in one request; finer charts load in 30-minute windows, each capped at 20,000 rows. Certified windows become visible and editable as they arrive; the viewport stays put while later windows load. A source revision is checked before and after each window. Truncation, missing provenance, changed source, invalid candle boundaries, or missing instrument identity block labeling. Completion requires all session windows and at least one observed candle. Zero-volume synthetic bars are excluded and counted. Existing annotations whose source changed remain preserved and blocked pending explicit migration.

NYSE calendar boundaries include DST and regular-session early closes. Extended review scope is explicitly 04:00–20:00 New York. Universe membership and annotation do not certify liquidity or short borrow.

## Training export

**Export completed** downloads a versioned JSON dataset containing only completed reviews, original intervals, exact ENTER_LONG/ENTER_SHORT and EXIT_LONG/EXIT_SHORT events, and WAIT/LONG/SHORT exposure segments. It includes a content hash, review revisions, instrument/source identity, and evidence IDs. Incomplete review counts are reported. LONG/SHORT between boundary events can be interpreted as hold during training.

Exports are explicitly hindsight annotations. Training must mask unavailable observations and nontradable decision instants, preserve time-based splits, and use features strictly before an entry boundary. A positive interval is expert preference, not an execution or profitability certificate. Different decision clocks must use an explicit alignment policy without changing the stored timestamps.

## Validation

Use repository launchers with `PYTHONDONTWRITEBYTECODE=1`:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:\Users\g835l\miniconda3\python.exe -m unittest tests.test_chart_labeler -v
& C:\Users\g835l\miniconda3\python.exe scripts/run_frontend.py build
& C:\Users\g835l\miniconda3\python.exe scripts/run_frontend.py ui:review -- --page labeler --labeler --theme light --theme dark --scale 0.8 --scale 1 --scale 1.25 --viewport normal:1600x1000 --viewport compact:1280x720 --strict
```

The browser flag uses isolated deterministic label fixtures inside the real Canvas renderer. It exercises hourly-first screening, explicit 100ms long/short submission, timestamp preservation across views, undo/redo, deletion, failed-save recovery, endpoint dragging, guarded container closing, completion, next ticker, and reload. It does not create production labels or place trades.

For real-QMD acceptance, set `LABELER_ACCEPTANCE_API=http://127.0.0.1:8000` and run `python -m unittest tests.test_chart_labeler_live -v`. This fetches a bounded certified SUGP 2026-08-21 100ms window and submits two intervals through the API into a temporary runtime database, reopening between submissions. It does not write labels into the operational database. Activate backend registry/persistence changes through the managed lifecycle only when active trading runs can be safely interrupted.

The Canvas uses a fixed 20% scanner column and an 80% fill-height chart. Identity and float load independently of full-market session statistics; market-column sorting becomes available after those statistics finish. Charts use the QMD certified prices stage without indicator warmup and load bounded, candle-aligned windows progressively. Session completion stays disabled until every window is certified.
