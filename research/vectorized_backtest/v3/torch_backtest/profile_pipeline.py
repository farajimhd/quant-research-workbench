"""Real cached-input sequential-versus-overlapped pipeline qualification.

Both paths recertify and hash-load the same immutable training datasets. Compare
every financial metric and the full ledgers, and report compilation separately.
This measures cached-input startup/transfer overlap, not cold SQL/V7 preparation.
"""

import gc
import json
from datetime import datetime
from pathlib import Path
from time import perf_counter

import numpy as np
import torch

from .encoding.clickhouse import certify_source
from .encoding.config import Session
from .genome import StrategySpace
from .prepared_cache import import_prepared, load_prepared
from .runtime import code_hash, file_hash, write_json
from .session_pipeline import PreparedSessions
from .session_pool import SessionPool


def profile(items, args, output, panel):
    if not args.reuse_prepared or len(items) < 2:
        raise ValueError("Pipeline qualification requires two sealed TRAINING datasets")
    items = items[:2]
    space = StrategySpace()
    rows = space.sample(np.random.default_rng(args.seed), args.population)
    rows[0] = space.default  # Diagnostic only; GA initialization stays random.
    preparation = {}

    def loader(index, mode):
        started = perf_counter()
        item = items[index]
        session = Session(Path(item['manifest']), Path(item['ledger']),
                          args.runtime / 'source_cache',
                          datetime.fromisoformat(item['start']), datetime.fromisoformat(item['end']),
                          max_prepared_gib=args.maximum_tape_gib)
        certificate = certify_source(session)
        identity = dict(code_hash=code_hash(), session=item,
                        manifest_sha256=file_hash(session.manifest),
                        build_id=certificate.source['build_id'])
        destination = output / mode / 'inputs' / f'training_{index:03d}'
        value = load_prepared(destination, identity)
        if value is None:
            origin = Path(args.reuse_prepared) / 'inputs' / f'training_{index:03d}'
            value = import_prepared(origin, destination, identity, space.manifest())
        preparation[mode, index] = perf_counter() - started
        return value

    options = dict(device='cuda', backend='compiled_graph', resident_gib=0,
                   maximum_host_gib=args.maximum_host_gib,
                   maximum_state_gib=args.maximum_state_gib,
                   maximum_fills=args.maximum_fills, graph_steps=args.graph_steps,
                   ledger_mode='inplace')
    references, ledgers, measurements = [], [], []
    width = None
    for mode in ('sequential', 'overlapped'):
        panel.emit(dict(status='profiling', stage=f'{mode} cached-input pipeline',
                        focus='Two training sessions; no selection or validation'))
        started = perf_counter()
        supplier = None
        if mode == 'sequential':
            tapes = [loader(i, mode) for i in range(2)]
            width = ((max(len(t.tickers) for t in tapes) + 63) // 64) * 64
            pool = SessionPool(tapes, space, args.population, capacity=width, prefetch=False, **options)
            data_wait = sum(preparation[mode, i] for i in range(2))
        else:
            supplier = PreparedSessions(
                2, lambda i: loader(i, mode), workers=args.preparation_workers,
                lookahead=args.preparation_lookahead,
                maximum_host_gib=args.maximum_host_gib,
                maximum_tape_gib=args.maximum_tape_gib, emit=panel.emit,
            )
            pool = SessionPool([], space, args.population, supplier=supplier, capacity=width, **options)
            data_wait = None
        results = []
        try:
            for i in range(2):
                panel.emit(dict(focus=f'{mode}: training session {i + 1}/2'))
                pool.progress = lambda event: panel.emit(dict(progress=event))
                result = pool.evaluate(i, rows)
                results.append(result)
                evaluator = next(iter(pool.evaluators.values()))
                if len(evaluator.runners) != 1:
                    raise RuntimeError('Qualification requires the fixed common strategy layout')
                runner = next(iter(evaluator.runners.values()))
                ledger = runner.ledger.detach().cpu()
                if mode == 'sequential':
                    references.append(result)
                    ledgers.append(ledger.clone())
                else:
                    for name, values in result.items():
                        if isinstance(values, list):
                            np.testing.assert_allclose(values, references[i][name], rtol=0, atol=1e-7)
                    torch.testing.assert_close(ledger, ledgers[i], rtol=0, atol=1e-7)
            elapsed = perf_counter() - started
            if supplier:
                data_wait = supplier.wait_seconds
            compile_time = sum(r['compile_seconds'] for r in results)
            measurements.append(dict(
                mode=mode, end_to_end_seconds=elapsed,
                end_to_end_without_compile_seconds=elapsed - compile_time,
                data_wait_seconds=data_wait,
                preparation_seconds=[preparation[mode, i] for i in range(2)],
                compile_seconds=compile_time,
                replay_seconds=[r['replay_seconds'] for r in results],
                bind_seconds=sum(r['bind_seconds'] for r in results),
                **pool.residency,
            ))
            write_json(output / 'pipeline_measurements.json', measurements)
        finally:
            pool.close()
        del pool
        if mode == 'sequential':
            del tapes
        gc.collect()
        torch.cuda.empty_cache()
    return dict(
        passed=True, code_hash=code_hash(), population=args.population,
        graph_steps=args.graph_steps, precompute_rules=False, ledger_mode='inplace',
        full_ledger_parity=True, sessions=[v['day'] for v in items],
        padded_tickers=width, slots_per_session=19800,
        optimized_replay_seconds=float(np.mean(measurements[1]['replay_seconds'])),
        optimized_objective_seconds=float(np.mean(measurements[1]['replay_seconds'])),
        measurements=measurements,
        qualification_scope='real sealed cached-input pipeline; not cold SQL/V7 preparation',
        approximate_broker_participation=space.settings.participation,
    )
