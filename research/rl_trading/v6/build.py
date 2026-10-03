"""Restart-safe bounded compiler for actual candles and sparse opportunities.

Usage (from an immutable workstation snapshot)::

    python -B -m research.rl_trading.v6.build --help

Set PYTHONDONTWRITEBYTECODE=1 and POLARS_MAX_THREADS=1 in the launcher. This
command reads only pinned ARTE/q_live products and writes under the supplied
runtime root. It does not start the portfolio teacher or train a model.
"""
from __future__ import annotations

import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['POLARS_MAX_THREADS'] = '1'

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from hashlib import sha256
import json
from multiprocessing import get_context
from pathlib import Path
from time import perf_counter

import numpy as np
import polars as pl
import psutil

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from research.rl_trading.v1 import arte_source, reference_features
from research.rl_trading.v1.common import digest, exclusive, file_hash
from research.rl_trading.v6.bank import write_bank
from research.rl_trading.v6.allocation import (first_eligible,
                                               intended_budgets,
                                               window_scores)
from research.rl_trading.v6.census import one_second_counts
from research.rl_trading.v6.config import worker_plan
from research.rl_trading.v6.features import (CandleFeatures, LEVEL_NAMES,
                                             SCALAR_NAMES, VERSION, encode)
from research.rl_trading.v6.opportunity import compile_ticker
from research.rl_trading.v6.source import read_candles, read_previous_volume, require_reporting_coverage
from research.rl_trading.v6.reference import read_reference
from research.rl_trading.v6.split import role
from research.rl_trading.v6.universe_scope import scope_population


_READER = None


def _init_worker() -> None:
    global _READER
    _READER = arte_source.reader(threads=1)


def _empty_features() -> CandleFeatures:
    return CandleFeatures(np.empty(0, dtype=np.int64),
        np.empty((0, len(SCALAR_NAMES)), dtype=np.float32),
        np.empty((0, 2, 5, len(LEVEL_NAMES)), dtype=np.float32))


def _work(packet):
    day, previous_day, listing, current, prior = packet
    ticker = listing['ticker']
    identity = listing['listing_id']
    began = perf_counter()
    bars, indicators = read_candles(_READER, current, day, ticker)
    if bars.is_empty():
        return identity, _empty_features(), pl.DataFrame(), pl.DataFrame(), {
            'ticker': ticker, 'listing_id': identity, 'candles': 0,
            'episodes': 0, 'candidates': 0,
            'elapsed_seconds': perf_counter()-began,
            'source_units': current['units'][str(day)][ticker],
            'excluded_reason': 'no_persisted_one_second_candle'}
    previous = (read_previous_volume(_READER, prior, previous_day, ticker)
                if prior is not None and
                ticker in prior['units'][str(previous_day)] else
                pl.DataFrame(schema={'bucket_index': pl.Int64,
                                     'volume': pl.Float64}))
    seed, splits, fundamentals, evidence = read_reference(
        _READER, day, listing)
    if previous_day is None:
        episodes, candidates = pl.DataFrame(), pl.DataFrame()
        report = {'ticker': ticker, 'episodes': 0, 'candidates': 0,
                  'context_only': True}
    else:
        episodes, candidates, report = compile_ticker(
            day, ticker, identity, bars, indicators)
    features = encode(
        day, bars, indicators, previous, seed, splits, fundamentals)
    report = {**report, 'listing_id': identity, 'candles': len(features.close_us),
              'prior_candles': previous.height,
              'source_units': current['units'][str(day)][ticker],
              'reference_hash': evidence['hash'],
              'elapsed_seconds': perf_counter()-began}
    return identity, features, episodes, candidates, report


def _ordered_bounded(pool, packets, max_in_flight):
    """Keep at most two worker-width results/futures resident at once."""
    items = iter(packets)
    pending = []
    for _ in range(max_in_flight):
        try:
            pending.append(pool.submit(_work, next(items)))
        except StopIteration:
            break
    while pending:
        current = pending.pop(0)
        yield current.result()
        try:
            pending.append(pool.submit(_work, next(items)))
        except StopIteration:
            pass


def _fragment_name(identity: str) -> str:
    return sha256(identity.encode()).hexdigest()[:24]


def _packet(day, previous_day, listing, current, prior):
    ticker = listing['ticker']
    current_slice = {'build_id': current['build_id'], 'units': {
        str(day): {ticker: current['units'][str(day)][ticker]}}}
    if prior is None:
        return day, None, listing, current_slice, None
    old = prior['units'][str(previous_day)]
    prior_slice = {'build_id': prior['build_id'], 'units': {
        str(previous_day): {ticker: old[ticker]} if ticker in old else {}}}
    return day, previous_day, listing, current_slice, prior_slice


def _save_fragment(root: Path, identity: str, episodes: pl.DataFrame,
                   candidates: pl.DataFrame, report: dict) -> None:
    folder = root / 'fragments' / _fragment_name(identity)
    if (folder / 'complete.json').exists():
        saved = json.loads((folder / 'complete.json').read_text(encoding='utf-8'))
        if saved['listing_id'] != identity:
            raise ValueError('Listing fragment hash collision')
        return
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for label, data in (('episodes', episodes), ('candidates', candidates)):
        if data.height:
            path = folder / f'{label}.parquet'
            data.write_parquet(path)
            files[label] = {'rows': data.height, 'sha256': file_hash(path)}
        else:
            files[label] = {'rows': 0, 'sha256': None}
    complete = {'listing_id': identity, 'ticker': report['ticker'],
                'files': files, 'report': report}
    temporary = folder / 'complete.json.tmp'
    temporary.write_text(json.dumps(complete, sort_keys=True, default=str),
                         encoding='utf-8')
    temporary.replace(folder / 'complete.json')


def _combine_fragments(root: Path, identities: list[str]) -> dict:
    files = {'episodes': [], 'candidates': []}
    totals = {'candles': 0, 'episodes': 0, 'candidates': 0,
              'zero_candle_listings': 0}
    for identity in identities:
        folder = root / 'fragments' / _fragment_name(identity)
        proof = json.loads((folder / 'complete.json').read_text(encoding='utf-8'))
        if proof['listing_id'] != identity:
            raise ValueError('Missing or displaced listing fragment')
        totals['candles'] += int(proof['report']['candles'])
        totals['zero_candle_listings'] += int(proof['report']['candles'] == 0)
        for label in files:
            entry = proof['files'][label]
            totals[label] += int(entry['rows'])
            if entry['rows']:
                path = folder / f'{label}.parquet'
                if file_hash(path) != entry['sha256']:
                    raise ValueError('Listing opportunity fragment hash mismatch')
                files[label].append(path)
    output = {}
    for label, paths in files.items():
        if paths:
            frame = pl.concat([pl.read_parquet(path) for path in paths],
                              how='vertical').sort(
                ['time_us', 'ticker', 'episode_id'] if label == 'candidates'
                else ['ticker', 'episode_id'])
            path = root / f'{label}.parquet'
            frame.write_parquet(path)
            output[label] = {'rows': frame.height,
                             'sha256': file_hash(path)}
        else:
            output[label] = {'rows': 0, 'sha256': None}
    if any(totals[label] != output[label]['rows'] for label in files):
        raise ValueError('Sparse output count failed reconciliation')
    if output['candidates']['rows']:
        eligible = first_eligible(pl.read_parquet(root / 'candidates.parquet'))
        planned = intended_budgets(eligible, window_scores(eligible))
        path = root / 'intended_allocations.parquet'
        planned.write_parquet(path)
        output['intended_allocations'] = {'rows': planned.height,
                                          'sha256': file_hash(path),
                                          'status': 'planning_only_not_fills'}
    else:
        output['intended_allocations'] = {'rows': 0, 'sha256': None,
                                          'status': 'planning_only_not_fills'}
    return {'totals': totals, 'outputs': output}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--previous-manifest', type=Path)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--previous-date', type=date.fromisoformat)
    parser.add_argument('--context-only', action='store_true')
    parser.add_argument('--workers', type=int, default=64)
    parser.add_argument('--ticker', action='append', dest='tickers')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    split_role = role(args.date)
    if (args.context_only != (split_role == 'context_only') or
            (args.previous_date is None) != args.context_only):
        raise ValueError('Context-only split needs --context-only and no prior day')
    runtime = Path(os.environ.get('QW_RUNTIME_ROOT', '')).resolve()
    output = args.output.resolve()
    if (not runtime.is_dir() or not output.is_relative_to(runtime) or
            (args.previous_date is not None and args.previous_date >= args.date)):
        raise ValueError('Use chronological dates and an available runtime output root')
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    tickers = sorted(set(t.upper() for t in args.tickers)) if args.tickers else None
    current = arte_source.load_build(args.manifest, args.ledger,
                                     [args.date], tickers)
    prior = (arte_source.load_build(args.previous_manifest or args.manifest,
                                   args.ledger, [args.previous_date])
             if args.previous_date is not None else None)
    require_reporting_coverage(current, args.date)
    if prior is not None:
        require_reporting_coverage(prior, args.previous_date)
    reader = arte_source.reader(threads=1)
    try:
        arte_source.storage_check(reader)
        reference_features.storage_check(reader)
        population, population_proof = arte_source.population(
            reader, current, args.date)
        population, scope_proof = scope_population(reader, population)
        counts = one_second_counts(reader, current, args.date,
                                   [row['ticker'] for row in population])
    finally:
        reader.close()
    budget = worker_plan(requested=min(args.workers, len(population)),
        available_gib=psutil.virtual_memory().available / 2**30)
    by_identity = {row['listing_id']: row for row in population}
    identities = sorted(by_identity)
    lengths = {identity: counts[by_identity[identity]['ticker']]
               for identity in identities}
    plan = {'version': VERSION, 'day': str(args.date),
            'split_role': split_role,
            'previous_day': str(args.previous_date) if prior else None,
            'source_build_id': current['build_id'],
            'previous_build_id': prior['build_id'] if prior else None,
            'source_definition_hash': current['definition_hash'],
            'prior_definition_hash': prior['definition_hash'] if prior else None,
            'population_snapshot_hash': population_proof['snapshot_hash'],
            'universe_scope': scope_proof,
            'source_units_hash': digest(current['units']),
            'previous_units_hash': digest(prior['units']) if prior else None,
            'reporting_coverage': {
                'current': current['definition']['trade_eligibility']['verified_coverage'],
                'previous': prior['definition']['trade_eligibility']['verified_coverage'] if prior else None},
            'census': lengths}
    plan['hash'] = digest(plan)
    output.mkdir(parents=True, exist_ok=True)
    with exclusive(output / 'run.lock'):
        plan_path = output / 'plan.json'
        if plan_path.exists():
            if json.loads(plan_path.read_text(encoding='utf-8')) != plan:
                raise ValueError('Runtime root belongs to a different V6 plan')
        else:
            plan_path.write_text(json.dumps(plan, sort_keys=True), encoding='utf-8')
        if (output / 'complete.json').exists():
            print(f'Certified V6 day already complete: {output}', flush=True)
            return 0
        progress_path = output / 'bank' / 'progress.json'
        done = (set(json.loads(progress_path.read_text(encoding='utf-8')))
                if progress_path.exists() else set())
        packets = (_packet(args.date, args.previous_date,
                           by_identity[identity], current, prior)
                   for identity in identities if identity not in done)
        started = perf_counter()
        with ProcessPoolExecutor(max_workers=budget.listing_workers,
                                 mp_context=get_context('spawn'),
                                 initializer=_init_worker) as pool:
            results = _ordered_bounded(pool, packets, budget.max_in_flight)

            def rows():
                completed = len(done)
                for identity, features, episodes, candidates, report in results:
                    if len(features.close_us) != lengths[identity]:
                        raise ValueError('Pinned one-second census changed during compilation')
                    _save_fragment(output, identity, episodes, candidates, report)
                    completed += 1
                    if completed % 25 == 0 or completed == len(identities):
                        print(f'{args.date}: {completed}/{len(identities)} listings '
                              f'completed; elapsed {perf_counter()-started:.1f}s',
                              flush=True)
                    yield identity, features

            bank = write_bank(output / 'bank', lengths, rows(),
                              source_hash=plan['hash'])
        combined = _combine_fragments(output, identities)
        if combined['totals']['candles'] != bank['candle_count']:
            raise ValueError('Packed feature and sparse label counts disagree')
        certificate = {'version': VERSION, 'plan_hash': plan['hash'],
                       'bank_file_hashes': bank['files_sha256'], **combined,
                       'listing_workers': budget.listing_workers,
                       'query_threads_each': budget.query_threads_each,
                       'elapsed_seconds': perf_counter()-started,
                       'status': 'complete'}
        temporary = output / 'complete.json.tmp'
        temporary.write_text(json.dumps(certificate, sort_keys=True),
                             encoding='utf-8')
        temporary.replace(output / 'complete.json')
        print(json.dumps(certificate, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
