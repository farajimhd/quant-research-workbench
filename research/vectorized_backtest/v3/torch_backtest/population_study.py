"""Matched-budget population study. All comparison data is TRAINING only.

Time is measured on the same full tape and shared candidate prefix. Search
quality is stochastic, not classification accuracy: three seeds, 1536 proposed
candidate evaluations per seed/population, three fixed training dates. Freeze
each winner before the remaining training dates are scored. Never read the six
evaluation tapes, tune from finalist results, or silently reduce a large batch.
"""

import gc
import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

from .encoding.clickhouse import certify_source
from .encoding.config import Session
from .genome import StrategySpace
from .grid import Settings
from .optimize import phase
from .prepared_cache import import_prepared, load_prepared
from .runtime import code_hash, file_hash, write_json
from .search_objective import score
from .session_pool import SessionPool

POPULATIONS = (64, 128, 192, 256, 512)
SEEDS = (20261004, 20261005, 20261006)
BUDGET = 1536  # Divisible by EVERY population, including192.
SEARCH_DATES = ('2026-07-30', '2026-08-18', '2026-09-03')
WEIGHTS = dict(initial_cash=10000, drawdown_weight=.25, dispersion_weight=.25,
               stop_risk_weight=.10, capital_time_weight=.002,
               excess_activity_weight=0., long_hold_weight=0.)


def run(spec, args, output, panel):
    if not args.reuse_prepared:
        raise ValueError('Study requires explicit sealed training-input origin')
    space = StrategySpace(replace(Settings(),
        maximum_stop_risk_fraction=args.maximum_stop_risk_fraction,
        maximum_position_hold_seconds=args.maximum_position_hold_seconds))
    weights = dict(WEIGHTS, stop_risk_weight=args.stop_risk_weight,
                   capital_time_weight=args.capital_time_weight)
    identity = dict(code_hash=code_hash(), grammar=space.manifest(), weights=weights,
                    populations=POPULATIONS, seeds=SEEDS, budget=BUDGET,
                    search_dates=SEARCH_DATES, training=spec['training'],
                    validation_read=False, origin=args.reuse_prepared)
    identity = json.loads(json.dumps(identity))
    receipt = output / 'study_identity.json'
    if receipt.exists() and json.loads(receipt.read_text()) != identity:
        raise ValueError('Study resume identity differs')
    write_json(receipt, identity)
    tapes = {}

    def tape(index):
        if index in tapes:
            return tapes[index]
        item = spec['training'][index]
        session = Session(Path(item['manifest']), Path(item['ledger']), args.runtime / 'source_cache',
                          datetime.fromisoformat(item['start']), datetime.fromisoformat(item['end']),
                          max_prepared_gib=args.maximum_tape_gib)
        certificate = certify_source(session)
        request = dict(code_hash=code_hash(), session=item, manifest_sha256=file_hash(session.manifest),
                       build_id=certificate.source['build_id'])
        path = output / 'inputs' / f'training_{index:03d}'
        value = load_prepared(path, request)
        if value is None:
            value = import_prepared(Path(args.reuse_prepared) / 'inputs' / f'training_{index:03d}',
                                    path, request, space.manifest(), execution_contract_only=True)
        tapes[index] = value
        if sum(t.bytes for t in tapes.values()) > args.maximum_host_gib * 1024**3:
            raise MemoryError('Study host envelope exceeded')
        return value

    def pool(inputs, batch):
        return SessionPool(inputs, space, batch, backend='compiled_graph', resident_gib=0,
                           maximum_host_gib=args.maximum_host_gib,
                           maximum_state_gib=args.maximum_state_gib,
                           maximum_fills=args.maximum_fills, graph_steps=args.graph_steps,
                           ledger_mode='inplace')

    indices = [next(i for i, v in enumerate(spec['training']) if v['day'] == day) for day in SEARCH_DATES]
    inputs = [tape(i) for i in indices]
    shared = space.sample(np.random.default_rng(SEEDS[0]), max(POPULATIONS))
    timing = output / 'timing'
    timing.mkdir(exist_ok=True)
    reference = None
    for batch in POPULATIONS:
        path = timing / f'batch_{batch}.json'
        if path.exists():
            value = json.loads(path.read_text())
            if batch == min(POPULATIONS) and value['reference_ledger_sha256'] != file_hash(timing / 'reference_ledger.pt'):
                raise ValueError('Profile reference ledger hash mismatch')
            reference_ledger = torch.load(timing / 'reference_ledger.pt', weights_only=True) if reference is None else reference_ledger
        else:
            panel.emit(dict(status='profiling', stage=f'Population{batch} time/parity',
                            focus='Full September3 premarket; identical candidate prefix'))
            holder = pool([inputs[-1]], batch)
            evaluator = runner = None
            holder.progress = lambda event: panel.emit(dict(progress=event))
            try:
                observations = [holder.evaluate(0, shared[:batch]) for _ in range(3)]
                evaluator = next(iter(holder.evaluators.values()))
                runner = next(iter(evaluator.runners.values()))
                ledger = runner.ledger[:min(POPULATIONS)].detach().cpu()
                value = dict(batch=batch, results=observations,
                             median_replay_seconds=float(np.median([v['replay_seconds'] for v in observations])),
                             compile_seconds=sum(v['compile_seconds'] for v in observations),
                             peak_gpu_allocated_gib=torch.cuda.max_memory_allocated()/1024**3,
                             peak_gpu_reserved_gib=torch.cuda.max_memory_reserved()/1024**3)
                # Account outputs must not depend on neighboring candidate count.
                if reference is not None:
                    for name, data in observations[-1].items():
                        if isinstance(data, list):
                            np.testing.assert_allclose(data[:min(POPULATIONS)], reference[name][:min(POPULATIONS)], rtol=0, atol=1e-7)
                    torch.testing.assert_close(ledger, reference_ledger, rtol=0, atol=1e-7)
                else:
                    reference_ledger = ledger.clone()
                    torch.save(reference_ledger, timing / 'reference_ledger.pt')
                    value['reference_ledger_sha256'] = file_hash(timing / 'reference_ledger.pt')
                value['shared_prefix_full_ledger_parity'] = True
                write_json(path, value)
            finally:
                holder.close()
                evaluator = runner = None
                del holder
                gc.collect()
                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats()
        if reference is None:
            reference = value['results'][-1]
    write_json(output / 'timing_summary.json', [json.loads((timing / f'batch_{b}.json').read_text()) for b in POPULATIONS])

    finalists, labels = [], []
    for seed in SEEDS:
        initial = space.sample(np.random.default_rng(seed), max(POPULATIONS))
        for batch in POPULATIONS:
            destination = output / f'search_{seed}_{batch}'
            destination.mkdir(exist_ok=True)
            winner_path = destination / 'winner.json'
            if winner_path.exists():
                frozen = json.loads(winner_path.read_text())
                winner = frozen['genome']
                space.validate([winner])
                if frozen['genome_sha256'] != space.identity(winner) or frozen['decoded'] != json.loads(json.dumps(asdict(space.decode([winner])[0]))):
                    raise ValueError('Frozen study winner hash/decode mismatch')
            else:
                holder = pool(inputs, batch)
                options = SimpleNamespace(population=batch, generations=BUDGET//batch, seed=seed,
                                          weights=weights, minimum_training_entries=1)
                checkpoint = destination / 'checkpoint.json'
                saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else None
                panel.emit(dict(config=dict(population=batch, generations=BUDGET//batch, training_sessions=3,
                                seed=seed, maximum_position_hold_seconds=args.maximum_position_hold_seconds,
                                maximum_stop_risk_fraction=args.maximum_stop_risk_fraction),
                                status='training', stage='Matched-budget population study',
                                message=f'Seed{seed}; B{batch}; {BUDGET} candidate evaluations; training-only'))
                try:
                    winner = phase(holder.objectives(), space, options, destination,
                                   checkpoint=saved, panel=panel, initial_rows=initial[:batch])
                finally:
                    holder.close()
                    del holder
                    gc.collect()
                    torch.cuda.empty_cache()
            finalists.append(winner)
            labels.append(dict(seed=seed, population=batch, selection_dates=SEARCH_DATES))
    # Freeze ALL15 policies before inspecting transfer to other training days.
    write_json(output / 'frozen_finalists.json', dict(labels=labels, genomes=finalists,
                                                   fingerprints=[space.identity(v) for v in finalists]))
    holder = pool([tape(i) for i in range(len(spec['training']))], 64)
    comparisons = []
    try:
        for i in range(len(spec['training'])):
            path = output / f'finalist_training_{i:03d}.json'
            value = json.loads(path.read_text()) if path.exists() else holder.evaluate(i, finalists)
            write_json(path, value)
            comparisons.append(value)
    finally:
        holder.close()
    scores, reasons = score(comparisons, **weights, minimum_training_entries=1)
    best_observed = max((v for v in scores if v is not None), default=None)
    quality = []
    for batch in POPULATIONS:
        values = [scores[i] for i, label in enumerate(labels) if label['population'] == batch and scores[i] is not None]
        quality.append(dict(population=batch, feasible_finalists=len(values), seeds=len(SEEDS),
                            median_objective=float(np.median(values)) if values else None,
                            best_objective=max(values) if values else None,
                            worst_objective=min(values) if values else None,
                            gap_to_best_observed=best_observed-max(values) if values else None))
    write_json(output / 'study_report.json', dict(labels=labels, results=comparisons,
                                               scores=scores, reasons=reasons,
                                               quality=quality,
                                               validation_read=False,
                                               scope='training search-quality comparison; no global-optimum accuracy claim'))
    panel.emit(dict(status='completed', stage='Population comparison saved',
                    message='All15 frozen finalists scored on30 training days; validation untouched'))
    return 0
