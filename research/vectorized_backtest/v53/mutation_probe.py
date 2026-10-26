"""Bounded paired mutation replay, independent of selection and its RNG."""
import json
from pathlib import Path
import numpy as np
from .identity import digest
from .composition import mutation
from .lineage import program_key
from .full_common import aggregate
from .objectives import Objective
from research.vectorized_backtest.v42.publication import write_json


def run_probe(controller, folder, members, ids, records, panel, generation):
    frozen = digest([m.payload() for m in members])
    # Stable hash ordering avoids choosing only the first scheduled operator.
    candidates = [i for i, r in enumerate(records) if r['generation'] == generation
                  and r['operation'] == 'crossover']
    candidates.sort(key=lambda i: digest(dict(seed=controller.job['seed'],
                                             generation=generation, serial=int(ids[i]))))
    candidates = candidates[:controller.job['mutation_probe_size']]
    rng = np.random.default_rng(np.random.SeedSequence([controller.job['seed'], generation, 53, 991]))
    mutants, pairs, rejected = [], [], []
    for i in candidates:
        for attempt in range(8):
            try:
                child, fields = mutation(rng, members[i], 1 + i % 5, True)
                if program_key(child) == program_key(members[i]):
                    raise ValueError('Mutation did not change the program')
                break
            except (ValueError, RuntimeError) as error:
                reason = str(error)
        else:
            rejected.append(dict(serial=int(ids[i]), reason=reason))
            continue
        mutants.append(child)
        pairs.append(dict(serial=int(ids[i]), operator=('whole:' + records[i]['whole_mode'])
                          if records[i].get('whole_network') else 'mask',
                          baseline={k:bool(v[i]) if k == 'eligible' else float(v[i])
                                    for k, v in panel.items()}, mutation_fields=fields))
    receipts = {}
    if mutants:
        # Reuse read-only market residency, but never overwrite main replay receipts
        # or admit diagnostic results into its resident result cache.
        worker = controller.worker
        output, resident = worker.config['output'], worker.resident_results
        try:
            worker.config['output'] = str(controller.root / 'mutation-probe-replay')
            worker.resident_results = None
            ordered, receipts = controller.evaluate('training', controller.job['training_days'],
                                                    mutants, generation)
            result = aggregate(ordered, Objective(**controller.job['objective']))
            for j, pair in enumerate(pairs):
                pair['mutated'] = {k:bool(v[j]) if k == 'eligible' else float(v[j])
                                   for k, v in result.items()}
        finally:
            worker.config['output'], worker.resident_results = output, resident
    if digest([m.payload() for m in members]) != frozen:
        raise ValueError('Mutation diagnostic changed the training cohort')
    write_json(folder / 'mutation-probe.json', dict(generation=generation,
        diagnostic_only=True, selection_updated=False, training_rng_used=False,
        requested=len(candidates), evaluated=len(pairs), rejected=rejected,
        population_sha256=frozen, receipts=receipts, pairs=pairs))
