"""Read the *saved run's* certified Strategy 1 products, without any writers.

The numbered candidate product is a frozen computation funnel. It is suitable
for the release-parity experiment; it is not an unrestricted strategy-search
universe. Changing upstream admission rules requires a new input envelope.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import polars as pl

from src.backend.backtest_input_scope import input_exclusions
from src.backend.backtest_liquidity_price import certify_price_level_plan
from src.backend.backtest_market_data import (
    market_day_source_sqls,
    project_market_day_plan,
    readonly_clickhouse_client,
)
from src.backend.backtest_strategy_one_activation import project_activation_plan
from src.backend.backtest_strategy_one_candidate_store import (
    certify_candidate_plan,
    exclude_candidate_tickers,
    project_candidate_plan,
)
from src.backend.backtest_strategy_one_configuration import (
    certify_numbered_configuration,
)
from src.backend.backtest_strategy_one_entry_store import certify_entry_evidence_plan
from src.backend.backtest_strategy_one_hod_store import certify_hod_plan
from src.backend.backtest_strategy_one_identity import certify_identity_plan
from src.backend.backtest_strategy_one_plan import (
    StrategyOneFixedPlans,
    certify_independent_strategy_one_products,
)
from src.backend.backtest_strategy_one_preparation import strategy_one_v7_tickers
from src.backend.backtest_strategy_one_static_gate import (
    compile_static_entry_gate,
    project_static_survivors,
)
from src.backend.backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
from src.backend.backtest_v4_chart import certified_saved_run_plan
from src.backend.structural_v7_seed import certified_seed_plan
from src.trading_runtime.arte_backtest_definition import load_backtest_definition
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST


def source_reader():
    """Use the market read principal, including Arrow/streaming SELECT support."""
    return readonly_clickhouse_client(market_stream=True, v3_read_principal=True)


def load_saved_inputs(journal, market_client, run_id, *, progress=print):
    """Cold-certify parent and all child attempts before projecting any tape.

    Return typed immutable plans plus separately measured setup stages. Saved
    review authority cannot authorize app execution or publication. This new
    research simulator consumes the certified products without mutating them.
    """
    timings = {}
    started = perf_counter()
    release = certify_numbered_configuration(market_client, 1)
    session, context, cursor, market = certified_saved_run_plan(
        journal, market_client, run_id=run_id
    )
    saved = load_backtest_definition(journal, run_id, run_context=context)
    timings["saved_run_and_market_certificate"] = perf_counter() - started
    progress(f"Saved market verified: {market.token}; {len(market.tickers):,} tickers")

    # Strategy 1 is grandfathered. The source-reconstruction helper explicitly
    # requires number >=20, so use the Strategy 1 saved-chart population proof.
    # An exclusion is accepted ONLY when it reproduces the immutable seed token.
    started = perf_counter()
    with closing(source_reader()) as reader:
        full = certify_candidate_plan(
            market,
            candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=57_600_000,
            client=reader,
        )
        alternatives = [full]
        excluded = input_exclusions(session.isoformat())
        if excluded:
            alternatives.append(exclude_candidate_tickers(full, excluded))
        matched = []
        scopes = set()
        for candidates in alternatives:
            selected = strategy_one_v7_tickers(candidates.prepared)
            # Excluding a non-candidate can leave exactly the same V7 scope.
            # It is one population proof, not an ambiguous second population.
            if selected in scopes:
                continue
            scopes.add(selected)
            execution = project_market_day_plan(market, selected)
            try:
                seeds = certified_seed_plan(execution, reader)
            except (ValueError, RuntimeError):
                continue
            progress(f"Candidate scope: {len(selected):,} tickers; seed {seeds.token}")
            if seeds.token == saved["definition"]["causal_v7_plan_token"]:
                matched.append((candidates, selected, execution, seeds))
        if len(matched) != 1:
            raise RuntimeError("Saved Strategy 1 population has no unique seed proof")
    candidates, selected, execution, seeds = matched[0]
    timings["candidate_population_and_saved_seed"] = perf_counter() - started
    progress(f"Saved seed verified: {len(selected):,} candidate tickers")

    def read(operation):
        with closing(source_reader()) as reader:
            return operation(reader)

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="torch-s1-seals") as pool:
        prices_future = pool.submit(
            read,
            lambda reader: certify_price_level_plan(
                execution, reader, read_client_factory=source_reader
            ),
        )
        identities_future = pool.submit(
            read, lambda reader: certify_identity_plan(market, client=reader)
        )
        pivots, activations, checked_seeds = certify_independent_strategy_one_products(
            market,
            candidates,
            selected,
            execution,
            client_factory=source_reader,
            pool=pool,
        )
        if checked_seeds.token != seeds.token:
            raise RuntimeError("Saved seed changed during child certification")
        intervals_future = pool.submit(
            read,
            lambda reader: certify_v7_interval_plan(
                execution,
                seeds,
                session_date=session.isoformat(),
                candidate_tickers=selected,
                client=reader,
            ),
        )
        hod = read(
            lambda reader: certify_hod_plan(market, candidates, seeds, client=reader)
        )
        entry = read(
            lambda reader: certify_entry_evidence_plan(
                market, candidates, activations, pivots, hod, seeds, client=reader
            )
        )
        fixed = StrategyOneFixedPlans(
            market,
            identities_future.result(),
            execution,
            prices_future.result(),
            candidates,
            activations,
            pivots,
            seeds,
            intervals_future.result(),
            hod,
            entry,
        )
    timings["fixed_product_certificates"] = perf_counter() - started
    progress(
        f"Fixed products verified: {len(fixed.candidates.prepared):,} candidate tickers"
    )
    return fixed, release, saved, context, cursor, timings


def load_replay_frames(fixed, *, through_ms, output, workers=8, progress=print):
    """Fetch only static survivors, using bounded independent Arrow readers.

    Every surviving ticker needs its whole causal market tape: a future entry
    can leave a position/order active beyond the scanner's five-minute episode.
    Reading that tape in advance does not make it visible early to the policy.
    Generated parquet is immutable research evidence under the runtime root.
    The price-specific liquidity lists are preserved without truncation.
    """
    output = Path(output).resolve()
    root = Path("D:/TradingML/runtimes").resolve()
    if not root.is_dir() or not output.is_relative_to(root):
        raise ValueError("Replay artifacts require the configured runtime root")
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError("Arrow read concurrency must be 1..16")
    visible = project_candidate_plan(fixed.candidates, through_boundary_ms=through_ms)
    gate = compile_static_entry_gate(visible, fixed.entry)
    activations = project_activation_plan(
        fixed.activations, fixed.candidates, through_boundary_ms=through_ms
    )
    survivors, _ = project_static_survivors(visible, activations, gate)
    tickers = tuple(item.ticker for item in survivors.prepared)
    facts = tuple(gate.facts[int(i)] for i in gate.eligible_indices)
    progress(f"Static funnel: {len(facts):,} rows; {len(tickers):,} replay tickers")
    output.mkdir(parents=True, exist_ok=True)

    def fetch(ticker):
        started = perf_counter()
        plan = project_market_day_plan(fixed.market, (ticker,))
        prices = fixed.prices.projected(plan)
        frames = []
        with closing(source_reader()) as reader:
            for sql in market_day_source_sqls(
                plan, through_boundary_ms=through_ms, price_plan=prices
            ):
                for batch in reader.iter_arrow_record_batches(
                    sql.replace("FORMAT JSONEachRow", "FORMAT ArrowStream")
                ):
                    frames.append(pl.from_arrow(batch))
        if not frames:
            raise RuntimeError(
                f"Certified surviving ticker has no market rows: {ticker}"
            )
        frame = pl.concat(frames, how="diagonal_relaxed").sort(
            "boundary_ms", "resolution_ms"
        )
        if (
            frame.select(pl.struct("boundary_ms", "resolution_ms").n_unique()).item()
            != frame.height
        ):
            raise RuntimeError(f"Market tape has duplicate completed keys: {ticker}")
        path = output / f"{ticker}.parquet"
        if path.exists():
            raise FileExistsError("Do not replace immutable replay evidence")
        frame.write_parquet(path)
        result = {
            "ticker": ticker,
            "rows": frame.height,
            "seconds": perf_counter() - started,
            "path": str(path),
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
        progress(f"Fetched {ticker}: {frame.height:,} rows in {result['seconds']:.3f}s")
        return result

    started = perf_counter()
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="torch-s1-arrow"
    ) as pool:
        files = tuple(pool.map(fetch, tickers))
    elapsed = perf_counter() - started
    selected = set(tickers)
    # Only producer facts are cached. No reference fills/orders enter the tape.
    (output / "facts.json").write_text(
        json.dumps(
            {
                "candidates": [asdict(row) for row in facts],
                "activations": [
                    asdict(row)
                    for row in fixed.entry.activations
                    if row.ticker in selected
                ],
                "v7_clocks": [
                    (ticker, clocks)
                    for ticker, clocks in fixed.v7_intervals.valid_seconds
                    if ticker in selected
                ],
                "v7_intervals": [
                    (ticker, [asdict(row) for row in rows])
                    for ticker, rows in fixed.v7_intervals.intervals
                    if ticker in selected
                ],
                "seed_policy": [
                    (row.ticker, row.seed_input_policy)
                    for row in fixed.v7_intervals.coverage
                    if row.ticker in selected
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "version": "strategy-one-certified-market-tape-v1",
                "market_token": fixed.market.token,
                "seed_token": fixed.seeds.token,
                "candidate_token": fixed.candidates.token,
                "entry_token": fixed.entry.token,
                "interval_token": fixed.v7_intervals.token,
                "price_token": fixed.prices.token,
                "session_date": fixed.market.sessions[0],
                "facts_sha256": sha256(
                    (output / "facts.json").read_bytes()
                ).hexdigest(),
                "through_ms": through_ms,
                "workers": workers,
                "files": files,
                "fetch_seconds": elapsed,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return tickers, facts, files, elapsed
