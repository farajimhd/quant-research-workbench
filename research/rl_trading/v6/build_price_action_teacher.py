"""Write hash-bound, candle-only V6 teacher actions and idealized ledger.

This is behavior-cloning supervision, not an executable profit certificate.
Only pinned one-second bars, qualified episode scores and the original
$10,000 teacher bankroll enter the trajectory. Quotes are reserved for the
separate model environment and replay.
"""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
import math
import os
from pathlib import Path

import polars as pl

from research.rl_trading.v6.build_price_action_brackets import VERSION as BRACKETS_VERSION
from research.rl_trading.v6.build_price_action_geometry import (
    VERSION as GEOMETRY_VERSION, source_positions)
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.split import role
from research.rl_trading.v6.teacher_data import VERSION as TEACHER_VERSION
from research.rl_trading.v6.teacher_trajectory import (
    VERSION as TRAJECTORY_VERSION, bind_intents, compile_trajectory)


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _decisions(rows) -> pl.DataFrame:
    return pl.DataFrame([{
        'close_us': item.close_us,
        'order_index': item.order_index,
        'token': item.token,
        'account_cash': float(item.account[0]),
        'account_equity': float(item.account[1]),
        'account_realized': float(item.account[2]),
        'account_exposure': float(item.account[3]),
        'seconds_since_action': float(item.account[4]),
        'pending_reserved_cash': float(item.account[5]),
        'pending_entry_count': float(item.account[6]),
        'held_listing_indices': item.held_index.tolist(),
        'held_features_flat': item.held_features.ravel().tolist(),
        'enter_allowed_indices': item.enter_allowed.nonzero()[0].tolist(),
        'exit_allowed': item.exit_allowed.tolist(),
        'stop_allowed': item.stop_allowed.tolist(),
        'target_allowed': item.target_allowed.tolist(),
        'size_fraction': item.size_fraction,
        'oracle_log_distance': item.oracle_log_distance,
    } for item in rows], infer_schema_length=None,
        schema_overrides={'size_fraction': pl.Float64,
                          'oracle_log_distance': pl.Float64})


def _outcomes(rows) -> pl.DataFrame:
    return pl.DataFrame([{
        'source_close_us': item.source_close_us,
        'source_order_index': item.source_order_index,
        'bucket_end_us': item.bucket_end_us,
        'action': item.action,
        'listing_index': item.listing_index,
        'requested_fraction': item.requested_fraction,
        'filled_fraction': item.filled_fraction,
        'realized_net_over_equity': item.realized_net_over_equity,
    } for item in rows], infer_schema_length=None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--previous-root', type=Path, required=True)
    parser.add_argument('--allocation-root', type=Path)
    parser.add_argument('--brackets-root', type=Path, required=True)
    parser.add_argument('--geometry-root', type=Path, required=True)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    runtime = Path(os.environ.get('QW_RUNTIME_ROOT', '')).resolve()
    roots = [args.source_root.resolve(), args.previous_root.resolve(),
             args.brackets_root.resolve(), args.geometry_root.resolve(),
             args.output.resolve()]
    allocation = (args.allocation_root.resolve()
                  if args.allocation_root else args.source_root.resolve())
    if (role(args.date) not in ('train', 'development') or
            not runtime.is_dir() or len(set(roots)) != len(roots) or
            args.output.resolve() == allocation or
            any(not path.is_relative_to(runtime) for path in
                (*roots, allocation))):
        raise ValueError('Teacher requires distinct certified forward runtime roots')
    source, previous, bracket_root, geometry_root, output = roots
    positions, _, allocation_hash = source_positions(source, args.date,
        allocation_root=allocation if allocation != source else None)
    allocation_path = allocation / 'intended_allocations.parquet'
    if _hash(allocation_path) != allocation_hash:
        raise ValueError('Teacher allocation changed after source binding')
    source_hash = _hash(source / 'complete.json')
    geometry_cert = json.loads((geometry_root / 'complete.json').read_text())
    geometry_hash = _hash(geometry_root / 'complete.json')
    if (geometry_cert.get('version') != GEOMETRY_VERSION or
            geometry_cert.get('status') != 'geometry_only_not_supervision' or
            geometry_cert.get('day') != str(args.date) or
            geometry_cert.get('source_certificate_sha256') != source_hash or
            geometry_cert.get('allocation_sha256') != allocation_hash or
            geometry_cert.get('execution_evidence') != 'none'):
        raise ValueError('Teacher geometry is not bound to candle bank')
    bracket_cert = json.loads((bracket_root / 'complete.json').read_text())
    bracket_file = bracket_root / 'oracle_brackets.parquet'
    if (bracket_cert.get('version') != BRACKETS_VERSION or
            bracket_cert.get('status') != 'labels_only_not_teacher' or
            bracket_cert.get('day') != str(args.date) or
            bracket_cert.get('geometry_certificate_sha256') != geometry_hash or
            bracket_cert.get('execution_evidence') != 'none' or
            bracket_cert.get('price_grid_source_type') !=
                'canonical_bar_precision' or
            bracket_cert.get('price_unit') != .0001 or
            bracket_cert.get('executable_market_tick_claim') is not False or
            _hash(bracket_file) != bracket_cert.get('brackets_sha256')):
        raise ValueError('Bracket labels are not certified price action')
    brackets = pl.read_parquet(bracket_file)
    if (brackets.height != positions.height or
            brackets.select('ticker', 'episode_uid').join(
                positions.select('ticker', 'episode_uid'),
                on=['ticker', 'episode_uid'], how='anti').height):
        raise ValueError('Bracket labels differ from allocated episodes')
    bracket_hash = _hash(bracket_root / 'complete.json')
    if output.exists() and any(output.iterdir()):
        complete = output / 'complete.json'
        if complete.is_file():
            saved = json.loads(complete.read_text())
            if (saved.get('version') == TEACHER_VERSION and
                    saved.get('bank_certificate_sha256') == source_hash and
                    saved.get('bracket_certificate_sha256') == bracket_hash and
                    all(_hash(output / name) == saved.get(key)
                        for name, key in (
                            ('decisions.parquet', 'decisions_sha256'),
                            ('outcomes.parquet', 'outcomes_sha256'),
                            ('positions.parquet', 'positions_sha256')))):
                print(json.dumps(saved, sort_keys=True), flush=True)
                return 0
        raise ValueError('Uncertified teacher output root exists')
    session = open_session(source, runtime_root=runtime,
                           previous_root=previous)
    if session.day != args.date:
        raise ValueError('Teacher bank belongs to another day')
    intentions = pl.read_parquet(allocation_path)
    intents, binding = bind_intents(intentions, brackets, session.listings)
    decisions, outcomes, report = compile_trajectory(session, intents)
    ledger = report.pop('ledger')
    if (not ledger or report['unresolved_positions'] or
            report['pending_entries'] or report['pending_exits'] or
            not math.isclose(sum(row['net_pnl'] for row in ledger),
                             report['modeled_net_pnl'], abs_tol=1e-6) or
            not math.isclose(report['ending_trading_cash']+
                             report['profit_bank'],
                             report['initial_cash']+
                             report['modeled_net_pnl'], abs_tol=1e-5)):
        raise ValueError('Hypothetical teacher account failed reconciliation')
    output.mkdir(parents=True, exist_ok=True)
    files = {'decisions.parquet': _decisions(decisions),
             'outcomes.parquet': _outcomes(outcomes),
             'positions.parquet': pl.DataFrame(ledger)}
    hashes = {}
    for name, frame in files.items():
        path = output / name
        frame.write_parquet(path)
        hashes[name.removesuffix('.parquet')+'_sha256'] = _hash(path)
    certificate = {**report, **binding,
        'version': TEACHER_VERSION,
        'trajectory_version': TRAJECTORY_VERSION,
        'status': 'audited_price_action_teacher',
        'day': str(args.date), 'split_role': session.role,
        'bank_certificate_sha256': source_hash,
        'bracket_certificate_sha256': bracket_hash,
        'geometry_certificate_sha256': geometry_hash,
        'allocation_sha256': allocation_hash,
        'price_grid_source_type': bracket_cert['price_grid_source_type'],
        'price_unit': bracket_cert['price_unit'],
        'executable_market_tick_claim': False,
        'label_evidence': ['certified_1s_candles', 'sparse_episode_scores'],
        'execution_evidence': 'none',
        'profit_scope': 'hypothetical_close_path_not_executable',
        **hashes}
    temporary = output / 'complete.json.tmp'
    temporary.write_text(json.dumps(certificate, sort_keys=True),
                         encoding='utf-8')
    temporary.replace(output / 'complete.json')
    print(json.dumps(certificate, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    raise SystemExit(main())
