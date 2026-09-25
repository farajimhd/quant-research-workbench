"""Compile fractional greedy action labels from one completed Phase 1 dataset.

build: compile shared coefficients and long/short/combined flat-state tables.
evaluate: score arbitrary joint share changes for an explicit portfolio state.
example: reproduce the user's B/D example, without market services.
"""
import os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("POLARS_MAX_THREADS", "2")
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import argparse
from datetime import date
from hashlib import sha256
import math
import signal
from time import monotonic, sleep

import polars as pl
import pyarrow.parquet as pq
import duckdb
from rich.console import Console
from rich.table import Table

from research.rl_trading.v1.common import exclusive, bounds, digest
from research.rl_trading.v1.phase2_values import VERSION, MODES, Position, ActionTable, coefficients, discount_policy
from research.rl_trading.v1.market_values import MarketValues
from src.market_engine.level_book_store import read, write
from src.runtime_paths import runtime_root
from src.market_engine.hindsight_batch import ordered_jobs, worker_budget

STOP = False


def write_progress(path, value):
    """Retry the brief Windows sharing race with the campaign progress reader."""
    for attempt in range(20):
        try:
            write(path,value,immutable=False)
            return
        except PermissionError:
            if attempt == 19:
                raise
            sleep(.05)


def file_hash(path):
    result = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify_files(folder, files):
    for name, expected in files.items():
        path = (folder / name).resolve()
        if path.parent != folder.resolve() or not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"Artifact integrity failure: {path}")


def parquet(path, frame):
    temporary = path.with_suffix(".parquet.tmp")
    frame.write_parquet(temporary, compression="zstd", statistics=True)
    temporary.replace(path)


def summarize(frame, mode):
    part = frame.filter(pl.col("side").is_in(MODES[mode])).with_columns(
        (pl.col("ticker") + pl.lit(":") + pl.col("side")).alias("key"))
    grouped = part.group_by("time_us", maintain_order=True).agg(
        pl.col("capital_per_share").filter(pl.col("can_open")).max().fill_null(0).alias("max_new_price"),
        pl.col("can_open").sum().alias("priced_candidates"),
        pl.col("value_available").sum().alias("valued_candidates"),
        pl.col("open_value_per_dollar").max().alias("best_score"),
        pl.col("key").filter(pl.col("open_value_per_dollar") == pl.col("open_value_per_dollar").max()).min().alias("best_key"),
    )
    return grouped.join(part.select("time_us", pl.col("key").alias("best_key"),
                                    pl.col("capital_per_share").alias("best_capital")),
                        on=["time_us", "best_key"], how="left", validate="1:1", maintain_order="left")


def merge_summary(previous, current):
    if previous is None:
        return current
    if not previous["time_us"].equals(current["time_us"]):
        raise ValueError("Market summary time grids differ")
    # Both grids have identical order: no per-listing sort or join is needed.
    combined = previous.hstack(current.drop("time_us").rename({c: c+"_new" for c in current.columns if c != "time_us"}))
    choose = ((pl.col("best_score_new").is_not_null() & pl.col("best_score").is_null())
              | (pl.col("best_score_new") > pl.col("best_score"))
              | ((pl.col("best_score_new") == pl.col("best_score"))
                 & (pl.col("best_key_new") < pl.col("best_key")))).fill_null(False)
    return combined.select(
        "time_us", pl.max_horizontal("max_new_price", "max_new_price_new").alias("max_new_price"),
        (pl.col("priced_candidates")+pl.col("priced_candidates_new")).alias("priced_candidates"),
        (pl.col("valued_candidates")+pl.col("valued_candidates_new")).alias("valued_candidates"),
        *(pl.when(choose).then(pl.col(c+"_new")).otherwise(pl.col(c)).alias(c)
          for c in ("best_score", "best_key", "best_capital")),
    )


def summarize_listing(frame, mode):
    """One sorted ticker: direct columns avoid group-by and join per mode."""
    result=None
    for side in MODES[mode]:
        part=frame.filter(pl.col('side')==side)
        current=part.select('time_us',
            pl.when(pl.col('can_open')).then(pl.col('capital_per_share')).otherwise(0.).alias('max_new_price'),
            pl.col('can_open').cast(pl.UInt64).alias('priced_candidates'),
            pl.col('value_available').cast(pl.UInt64).alias('valued_candidates'),
            pl.col('open_value_per_dollar').alias('best_score'),
            pl.when(pl.col('open_value_per_dollar').is_not_null()).then(pl.col('ticker')+pl.lit(':'+side)).alias('best_key'),
            pl.when(pl.col('open_value_per_dollar').is_not_null()).then(pl.col('capital_per_share')).alias('best_capital'))
        result=merge_summary(result,current)
    return result


def flat_policy(summary):
    # A known winner is not a global label if another eligible value is missing.
    complete = pl.col("priced_candidates") == pl.col("valued_candidates")
    enter = complete & (pl.col("best_score") > 0).fill_null(False)
    return summary.with_columns(
        pl.when(~complete).then(pl.lit("unavailable_candidate_values"))
        .otherwise(pl.lit("available")).alias("status"),
        pl.when(~complete).then(pl.lit(None, dtype=pl.String))
        .when(enter).then(pl.col("best_key")).otherwise(pl.lit("wait")).alias("chosen_action"),
        pl.when(enter).then(pl.col("max_new_price") / pl.col("best_capital"))
        .when(complete).then(0.0).alias("change_shares"),
        pl.when(enter).then(pl.col("max_new_price") * pl.col("best_score"))
        .when(complete).then(0.0).alias("discounted_value"),
    )


def phase1_plan(root):
    plan = read(root / "plan.json")
    if plan["plan_hash"] != digest({k: v for k, v in plan.items() if k != "plan_hash"}):
        raise ValueError("Phase 1 plan hash mismatch")
    complete = read(root / "complete.json")
    if complete["plan_hash"] != plan["plan_hash"] or complete["listing_count"] != len(plan["selected"]):
        raise ValueError("Phase 1 dataset is not complete for its declared scope")
    if len({r["ticker"] for r in plan["selected"]}) != len(plan["selected"]):
        raise ValueError("Duplicate Phase 1 ticker")
    if not plan["selected"] or complete["rows"] != 57601 * len(plan["selected"]):
        raise ValueError("Empty or incomplete Phase 1 decision grid")
    if plan["version"] != "hindsight-phase1-arte-price-action-v4":
        raise ValueError("RL trading Phase 2 requires arte price-action Phase 1 V4")
    expected_basis = 'price_action' if plan['version'] in ('hindsight-phase1-arte-price-action-v2','hindsight-phase1-arte-price-action-v3','hindsight-phase1-arte-price-action-v4') else 'quotes'
    if plan.get('valuation_basis','quotes') != expected_basis:
        raise ValueError('Phase 1 valuation basis does not match its version')
    if plan['version'] in ('hindsight-phase1-arte-price-action-v3','hindsight-phase1-arte-price-action-v4'):
        if plan.get('liquidation_us') != bounds(date.fromisoformat(plan['date']))[1]-120_000_000:
            raise ValueError('Phase 1 liquidation boundary must be 19:58 ET')
    return plan


def compile_listing(listing, source, root, plan):
    started=monotonic()
    directory=digest(listing)[:20]
    incoming=source/"listings"/directory
    output=root/"listings"/directory
    output.mkdir(parents=True,exist_ok=True)
    ready = read(incoming / "ready.json")
    if ready["plan_hash"] != plan["phase1_plan_hash"] or ready["listing"] != listing:
        raise ValueError("Phase 1 listing provenance mismatch")
    if ready["rows"] != 57601 or "opportunities.parquet" not in ready["files"]:
        raise ValueError("Phase 1 listing is missing required rows or file")
    verify_files(incoming, ready["files"])
    pin = file_hash(incoming / "ready.json")
    if (output / "ready.json").exists():
        published = read(output / "ready.json")
        if published["source_ready_hash"] != pin or published["plan_hash"] != plan["plan_hash"]:
            raise ValueError("Checkpoint input/plan changed")
        verify_files(output, published["files"])
        status = "reused"
    else:
        frame = pl.read_parquet(incoming / "opportunities.parquet")
        left, right = bounds(date.fromisoformat(plan["date"]))
        if (frame.height != 57601 or not frame["time_us"].equals(pl.Series("time_us", range(left, right+1, 1_000_000), dtype=pl.Int64))
            or frame["ticker"].unique().to_list() != [listing["ticker"]]
            or frame["listing_id"].unique().to_list() != [listing["listing_id"]]):
            raise ValueError("Phase 1 grid or identity mismatch")
        if plan['phase1_version'] == 'hindsight-phase1-arte-price-action-v4':
            for side in ('long','short'):
                required = {f'{side}_entry_us',f'{side}_target_id',f'{side}_status'}
                if not required <= set(frame.columns):
                    raise ValueError('Phase 1 V4 entry eligibility fields missing')
                invalid = frame.filter(
                    ((pl.col(f'{side}_status') == 'available') &
                     ((pl.col(f'{side}_entry_us') > pl.col('time_us')) | (pl.col(f'{side}_target_id') <= 0))) |
                    ((pl.col(f'{side}_status') == 'waiting_for_macd_entry') &
                     (pl.col(f'{side}_entry_us') <= pl.col('time_us'))))
                if invalid.height:
                    raise ValueError('Phase 1 V4 entry eligibility disagrees with target entry')
        cutoff = plan.get('liquidation_us')
        if cutoff is not None:
            if not frame['session_terminal'].equals(frame['time_us'] >= cutoff) or any(
                    frame.filter(pl.col(side+'_target_us') > cutoff).height for side in ('long','short')):
                raise ValueError('Phase 1 terminal state or target exceeds the liquidation boundary')
        if plan['liquidity_filter']['min_volume_60s'] or plan['liquidity_filter']['min_trades_60s']:
            if not {'volume','trades'} <= set(frame.columns):
                raise ValueError('Phase 1 completed one-second activity missing')
            if frame.filter((pl.col('volume') < 0) | ~pl.col('volume').is_finite() |
                            (pl.col('trades') < 0) | pl.col('volume').is_null() |
                            pl.col('trades').is_null()).height:
                raise ValueError('Invalid Phase 1 completed activity')
            frame = frame.with_columns(
                pl.col('volume').rolling_sum(60,min_samples=1).alias('volume_60s'),
                pl.col('trades').rolling_sum(60,min_samples=1).alias('trades_60s'))
        values = coefficients(frame, plan["gamma_per_second"], plan["cost_per_share_per_transaction"],
                              valuation_basis=plan.get('valuation_basis','quotes'),
                              **plan['liquidity_filter'])
        parquet(output / "coefficients.parquet", values)
        for mode in MODES:
            parquet(output / f"{mode}.parquet", summarize_listing(values, mode))
        files = {name: file_hash(output / name) for name in
                 ("coefficients.parquet", "long.parquet", "short.parquet", "long_short.parquet")}
        if file_hash(incoming / "ready.json") != pin:
            raise ValueError("Phase 1 changed during compilation")
        write(output / "ready.json", dict(plan_hash=plan["plan_hash"], source_ready_hash=pin,
                                           files=files, rows=values.height))
        status = "completed"
    return dict(ticker=listing["ticker"],status=status,directory=directory,elapsed_seconds=monotonic()-started)


def publish_market_values(root, plan):
    """Publish a complete holding grid and sparse opening alternatives."""
    holding_path = root / 'market_hold_values.parquet'
    opening_path = root / 'market_open_values.parquet'
    temporary = root / 'market_coefficients.parquet.tmp'
    expected_rows = 2 * 57601 * len(plan['selected'])
    writer = None
    rows = 0
    opening_rows = 0
    liquidity_rejected_rows = 0
    try:
        for index, listing in enumerate(plan['selected']):
            if STOP or (root/'STOP').exists():
                raise InterruptedError('Market tensor publication interrupted')
            folder = root/'listings'/digest(listing)[:20]
            ready = read(folder/'ready.json')
            if ready['plan_hash'] != plan['plan_hash'] or ready['rows'] != 115202:
                raise ValueError('Market tensor listing certificate mismatch')
            verify_files(folder, ready['files'])
            frame = pl.read_parquet(folder/'coefficients.parquet')
            if (frame.height != 115202 or frame['ticker'].unique().to_list() != [listing['ticker']]
                or frame['listing_id'].unique().to_list() != [listing['listing_id']]
                or frame['side'].to_list()[:57601] != ['long']*57601
                or frame['side'].to_list()[57601:] != ['short']*57601
                or not frame['time_us'].head(57601).equals(frame['time_us'].tail(57601))
                or frame['time_us'].n_unique() != 57601):
                raise ValueError('Market tensor axes or listing identity mismatch')
            frame = frame.with_columns(
                pl.lit(index,dtype=pl.UInt32).alias('listing_index'),
                pl.lit(plan['discount_policy']['macd_resolution_seconds']).alias('macd_resolution_seconds'))
            batch = frame.to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(temporary, batch.schema, compression='zstd',
                    write_statistics=True)
            writer.write_table(batch, row_group_size=57601)
            rows += frame.height
            opening_rows += frame['can_open'].sum()
            liquidity_rejected_rows += (frame['status'] == 'liquidity_below_threshold').sum()
        if writer is None or rows != expected_rows:
            raise ValueError('Market tensor row count mismatch')
    finally:
        if writer is not None:
            writer.close()
    # Keep every holding/closing state, including unavailable values and terminal
    # rows. Only opening rows are sparse; absence means entry is prohibited.
    rows_per_second = 2 * len(plan['selected'])
    group_rows = min(360_000,rows_per_second*30)
    opening_fields = ('can_open','value_available','open_value_available','entry_price',
        'capital_per_share','open_profit_per_share','open_value_per_share',
        'open_value_per_dollar')
    keys = ('time_us','listing_index','side')
    source = pl.scan_parquet(temporary)
    holding_fields = [c for c in source.collect_schema().names() if c not in opening_fields]
    products = ((holding_path,holding_fields,None,expected_rows),
        (opening_path,[*keys,*opening_fields],'can_open',opening_rows))
    artifacts = {}
    spill = root/'tensor-sort-spill'
    spill.mkdir(exist_ok=True)
    database = duckdb.connect(database=':memory:')
    sort_policy = plan.get('tensor_sort',{})
    memory_gb = int(sort_policy.get('memory_gb',8))
    sort_threads = int(sort_policy.get('threads',4))
    if not 1 <= memory_gb <= 256 or not 1 <= sort_threads <= 64:
        raise ValueError('Invalid bounded tensor sort budget')
    database.execute(f"SET memory_limit='{memory_gb}GB'")
    database.execute(f'SET threads={sort_threads}')
    def quoted(path):
        return "'" + str(path.as_posix()).replace("'", "''") + "'"
    database.execute(f'SET temp_directory={quoted(spill)}')
    try:
        for path, fields, predicate, expected in products:
            ordered = path.with_suffix('.parquet.ordered.tmp')
            projection = ','.join('"'+field+'"' for field in fields)
            condition = f' WHERE {predicate}' if predicate else ''
            database.execute(f'COPY (SELECT {projection} FROM read_parquet({quoted(temporary)})'
                f'{condition} ORDER BY time_us,listing_index,side) TO {quoted(ordered)} '
                f'(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {group_rows})')
            if STOP or (root/'STOP').exists():
                raise InterruptedError('Market tensor publication interrupted')
            actual = pq.ParquetFile(ordered).metadata.num_rows
            if actual != expected:
                raise ValueError('Market tensor row count mismatch')
            if path.exists():
                if file_hash(path) != file_hash(ordered):
                    raise ValueError('Existing market tensor differs from rebuilt output')
                ordered.unlink()
            else:
                ordered.replace(path)
            artifacts[path.name] = dict(file=path.name,rows=actual,file_hash=file_hash(path))
    finally:
        database.close()
        temporary.unlink(missing_ok=True)
        for path,_,_,_ in products:
            path.with_suffix('.parquet.ordered.tmp').unlink(missing_ok=True)
    return dict(rows=rows, holding=artifacts[holding_path.name],
        opening=artifacts[opening_path.name],listing_count=len(plan['selected']),
        liquidity_rejected_rows=liquidity_rejected_rows,
        time_count=57601, side_count=2, resolution_count=1,
        axis_order=['macd_resolution_seconds','time_us','listing_index','side'],
        physical_order=['time_us','listing_index','side'],row_group_target_rows=group_rows,
        sort_engine='duckdb_external',sort_memory_limit=f'{memory_gb}GB',
        sort_threads=sort_threads,duckdb_version=duckdb.__version__,
        missing_opening='can_open_false',listing_index_source='plan.selected order')


def run_build(args, console):
    global STOP
    STOP = False
    source = args.phase1.resolve()
    original = phase1_plan(source)
    # Validate configuration even if source is empty or all outputs are reused.
    discount = discount_policy(original.get('macd_resolution_seconds',1.),
        half_life_bars=getattr(args,'half_life_bars',None),gamma=args.gamma)
    if not math.isfinite(args.cost_per_share) or args.cost_per_share < 0:
        raise ValueError("cost-per-share must be finite and nonnegative")
    if not math.isfinite(args.min_volume_60s) or args.min_volume_60s < 0 or args.min_trades_60s < 0:
        raise ValueError('Liquidity thresholds must be finite and nonnegative')
    if (args.min_volume_60s or args.min_trades_60s) and original.get('valuation_basis') != 'price_action':
        raise ValueError('Liquidity filter requires arte price-action activity')
    if not 1 <= args.sort_memory_gb <= 256 or not 1 <= args.sort_threads <= 64:
        raise ValueError('Tensor sort budget must be within supported bounds')
    runtime = required_runtime()
    os.environ['POLARS_TEMP_DIR'] = str(runtime)
    plan = dict(version=VERSION, phase1_root=str(source), phase1_plan_hash=original["plan_hash"],
                phase1_version=original['version'],
                valuation_basis=original.get('valuation_basis','quotes'),
                liquidation_us=original.get('liquidation_us'),
                date=original["date"], scope=original["scope"], selected=original["selected"],
                gamma_per_second=discount['gamma_per_second'], discount_policy=discount,
                cost_per_share_per_transaction=args.cost_per_share,
                liquidity_filter=dict(min_volume_60s=args.min_volume_60s,
                    min_trades_60s=args.min_trades_60s),
                sizes="fractional", modes=list(MODES),
                short_policy="100% synthetic reserve; proceeds locked; no broker margin claim",
                semantics="Local greedy values; no future reallocations; exact size coefficients",
                market_tensor='market_hold_values.parquet full grid plus market_open_values.parquet sparse entries',
                polars_version=pl.__version__,
                tensor_sort=dict(engine='duckdb_external',version=duckdb.__version__,
                    memory_gb=args.sort_memory_gb,threads=args.sort_threads),
                code_hashes={p: sha256((REPO / p).read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()
                             for p in ("research/rl_trading/v1/build_phase2.py", "research/rl_trading/v1/phase2_values.py", "research/rl_trading/v1/market_values.py", "research/rl_trading/v1/common.py", "src/market_engine/hindsight_batch.py")})
    plan["plan_hash"] = digest(plan)
    root = runtime / "hindsight-greedy" / plan["date"] / plan["plan_hash"][:16]
    root.mkdir(parents=True, exist_ok=True)
    if getattr(args,'result_file',None):
        result_file=args.result_file.resolve()
        if not result_file.is_relative_to(runtime.resolve()):raise ValueError('result-file must be under runtime root')
        write(result_file,dict(root=str(root),plan_hash=plan['plan_hash']),immutable=False)
    console.print(f"Greedy labels | {plan['date']} | {len(plan['selected']):,} listings | {plan['scope']}")
    console.print(f"Discount: {discount['mode']} | half-life {discount['half_life_seconds']} seconds | gamma={discount['gamma_per_second']:.9g}/second")
    console.print(f"Opening liquidity: completed 60s volume >= {args.min_volume_60s:g} shares; trades >= {args.min_trades_60s}")
    console.print("Output: " + str(root), soft_wrap=True)
    workers=worker_budget(getattr(args,"workers",None))
    console.print(f"Workers: {workers}; bounded compilation, deterministic market reduction")
    started = monotonic()
    previous_handler = signal.signal(signal.SIGINT, lambda *_: request_stop())
    try:
        with exclusive(root / "run.lock"):
            write(root / "plan.json", plan)
            (root / "complete.json").unlink(missing_ok=True)
            counts = dict(completed=0, reused=0, failed=0)
            summaries = {mode: None for mode in MODES}
            results = []
            last_log = started
            def heartbeat(submitted,active,waiting):
                write_progress(root/'progress.json',dict(counts=counts,active=active,awaiting_reduction=waiting,
                    queued=len(plan['selected'])-submitted,retries=0))
            jobs=ordered_jobs(plan["selected"],lambda listing:compile_listing(listing,source,root,plan),workers,
                              lambda:STOP or (root/"STOP").exists(),heartbeat)
            for index, (listing,result) in enumerate(jobs):
                if isinstance(result, Exception):
                    counts["failed"] += 1
                    results.append(dict(ticker=listing["ticker"],status="failed",error=str(result)))
                    console.print(f"Failed {listing['ticker']}: {str(result)[:200]}",markup=False)
                else:
                    output=root/"listings"/result["directory"]
                    for mode in MODES:
                        summaries[mode]=merge_summary(summaries[mode],pl.read_parquet(output/f"{mode}.parquet"))
                    counts[result["status"]]+=1
                    results.append(result)
                if monotonic()-last_log > 5 or index+1 == len(plan["selected"]):
                    console.print(f"Completed {counts['completed']} | reused {counts['reused']} | failed {counts['failed']} | queued {len(plan['selected'])-index-1} | {monotonic()-started:.2f}s")
                    last_log = monotonic()
            success = len(results) == len(plan["selected"]) and not counts["failed"]
            if success:
                if phase1_plan(source)["plan_hash"] != plan["phase1_plan_hash"]:
                    raise ValueError("Phase 1 completion changed")
                tensor = publish_market_values(root,plan)
                for mode in MODES:
                    policy = flat_policy(summaries[mode])
                    if plan.get('liquidation_us') is not None:
                        policy = policy.with_columns((pl.col('time_us') >= plan['liquidation_us']).alias('session_terminal'))
                    parquet(root / f"{mode}.parquet", policy)
                completion = dict(plan_hash=plan["plan_hash"], listing_count=len(results),
                    tensor=tensor,files={**{f"{mode}.parquet": file_hash(root / f"{mode}.parquet") for mode in MODES},
                        tensor['holding']['file']:tensor['holding']['file_hash'],
                        tensor['opening']['file']:tensor['opening']['file_hash']})
            state = "complete" if success else "failed" if counts["failed"] else "interrupted"
            write(root / "summary.json", dict(status=state, counts=counts, results=results,
                                               market_tensor=tensor if success else None,
                                               elapsed_seconds=monotonic()-started), immutable=False)
            write_progress(root / "progress.json", dict(status=state, counts=counts, active=0,
                queued=len(plan["selected"])-len(results), retries=0))
            if success:
                write(root / "complete.json", completion)
                console.print(f"Market values: {tensor['holding']['rows']:,} holding rows + {tensor['opening']['rows']:,} opening rows across {tensor['listing_count']:,} listings; liquidity-gated {tensor['liquidity_rejected_rows']:,} rows")
            console.print(f"Result: {state}. Rerun to reuse verified listings; failures are never skipped.")
            return 0 if success else 2
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def request_stop():
    global STOP
    STOP = True


def required_runtime():
    root = runtime_root()
    if not root.is_dir():
        raise ValueError(f"Required runtime root unavailable: {root}")
    return root


def report_table(report):
    table = Table(title=f"Greedy action values | {report['state']['mode']}")
    for title in ("Action", "Share changes", "Future $", "Discounted $", "vs Hold $"):
        table.add_column(title)
    settlements = []
    for item in report["actions"]:
        result = item["result"]
        def amount(key):
            value = result[key]
            return f"{value:.4f}" if value is not None else result["value_status"]
        changes = ", ".join(f"{k} {v:+.4g}" for k, v in item["changes"].items()) or "hold / wait"
        table.add_row(item["name"], changes, amount("undiscounted_future_profit"),
                      amount("discounted_future_value"), amount("delta_vs_hold"))
        if result.get('settlement_status') == 'insolvent':
            settlements.append(f"{item['name']}: insolvent after liquidation; cash deficit ${result['cash_deficit']:.4f}")
    if settlements:
        table.caption = '; '.join(settlements)
    return table


def run_evaluate(args, console):
    root = args.dataset.resolve()
    plan = read(root / "plan.json")
    if plan.get('version') != VERSION or plan.get('phase1_version') != 'hindsight-phase1-arte-price-action-v4' or plan.get('valuation_basis') != 'price_action':
        raise ValueError('RL trading evaluation requires arte price-action Phase 2 V6')
    if digest({k: v for k, v in plan.items() if k != "plan_hash"}) != plan["plan_hash"]:
        raise ValueError("Greedy plan integrity failure")
    complete = read(root / "complete.json")
    if complete["plan_hash"] != plan["plan_hash"] or complete["listing_count"] != len(plan["selected"]):
        raise ValueError("Incomplete greedy dataset")
    verify_files(root, complete["files"])
    request = read(args.request)
    with MarketValues(root) as values:
        rows = values.at(request['time_us']).to_dicts()
    state = ActionTable(rows, [Position(**p) for p in request.get("positions", [])], mode=args.mode)
    report = dict(plan_hash=plan["plan_hash"], request=request, state=state.describe(), actions=[
        dict(name=a["name"], changes=a["changes"], result=state.evaluate(a["changes"]))
        for a in request["actions"]])
    path = required_runtime() / "hindsight-greedy" / "evaluations" / (digest(report) + ".json")
    write(path, report)
    console.print(report_table(report))
    console.print("Full values, feasibility and transaction sizes: " + str(path), soft_wrap=True)
    return 0


def illustrative_row(ticker, price, profit, seconds, gamma, side="long"):
    return dict(time_us=3_000_000, ticker=ticker, listing_id=ticker, side=side,
                can_open=True, can_close=True, value_available=True, entry_price=price,
                close_price=price, capital_per_share=price, target_price=price+profit*(1 if side=="long" else -1),
                hold_seconds=seconds, discount=gamma**seconds,
                open_profit_per_share=profit, hold_profit_per_share=profit,
                open_value_per_share=profit*gamma**seconds, hold_value_per_share=profit*gamma**seconds)


def run_example(args, console):
    rows = [illustrative_row("B", 10, 6, 3, args.gamma), illustrative_row("D", 55, 55, 4, args.gamma)]
    state = ActionTable(rows, [Position("B", "long", 5, 10, 10)], mode="long")
    actions = [("Hold", {}), ("Add B", {"B:long": .5}), ("Buy D with cash", {"D:long": 1/11}),
               ("Reduce B / buy D", {"B:long": -2.5, "D:long": 6/11}),
               ("Exit B / buy D", {"B:long": -5, "D:long": 1}),
               ("Reduce B", {"B:long": -1}), ("Exit B", {"B:long": -5})]
    report = dict(gamma=args.gamma, state=state.describe(), actions=[
        dict(name=name, changes=changes, result=state.evaluate(changes)) for name, changes in actions])
    path = required_runtime() / "hindsight-greedy" / "examples" / (digest(report)+".json")
    write(path, report)
    console.print("Illustrative B/D: $50 open cost + $5 cash; fractional shares; current B price $10.")
    console.print(report_table(report))
    console.print("Full values: " + str(path), soft_wrap=True)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Compile a completed Phase 1 dataset; rerun to resume")
    build.add_argument("--phase1", required=True, type=Path)
    build.add_argument("--workers",type=int,default=None)
    build.add_argument('--result-file',type=Path)
    discount = build.add_mutually_exclusive_group()
    discount.add_argument('--half-life-bars',type=float,help='Discount half-life in MACD bars; default 30')
    discount.add_argument("--gamma", type=float, default=None, help="Explicit per-second discount override")
    build.add_argument("--cost-per-share", type=float, default=0, help="Per transaction, included in prices; default 0")
    build.add_argument('--min-volume-60s',type=float,default=20_000.,
        help='Minimum completed one-minute share volume for new entries; default 20000')
    build.add_argument('--min-trades-60s',type=int,default=11,
        help='Minimum completed one-minute trade count for new entries; default 11 (>10)')
    build.add_argument('--sort-memory-gb',type=int,default=8,
        help='DuckDB external sort memory cap in GiB; default 8')
    build.add_argument('--sort-threads',type=int,default=4,
        help='DuckDB external sort threads; default 4')
    evaluate = commands.add_parser("evaluate", help="Score explicit joint actions from a state/request JSON")
    evaluate.add_argument("--dataset", required=True, type=Path)
    evaluate.add_argument("--request", required=True, type=Path)
    evaluate.add_argument("--mode", choices=MODES, default="long_short")
    example = commands.add_parser("example", help="Reproduce the B/D action-size table offline")
    example.add_argument("--gamma", type=float, default=.99)
    args = parser.parse_args(argv)
    if getattr(args, 'gamma', None) is not None and (not math.isfinite(args.gamma) or not 0 < args.gamma <= 1):
        parser.error("gamma must be finite and in (0, 1]")
    return {"build": run_build, "evaluate": run_evaluate, "example": run_example}[args.command](args, Console())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Greedy labels failed: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
