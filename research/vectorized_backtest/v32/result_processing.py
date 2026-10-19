"""CPU-only result reduction and durable publication for the shared GPU service."""
import json
from hashlib import sha256
import numpy as np
from .publication import write_json


def process_metrics(snapshot, listing_ids, listings):
    metrics = {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in snapshot.items()}
    metrics['ticker_pnl'] = []
    for profits, indices in zip(metrics['episode_profits'], metrics['episode_listings']):
        totals = np.bincount(np.asarray(indices, dtype=np.int64), weights=profits, minlength=len(listing_ids))
        used = np.unique(np.asarray(indices, dtype=np.int64))
        metrics['ticker_pnl'].append({listings[int(listing_ids[j])]['listing_id']: float(totals[j]) for j in used})
    return metrics


def publish_results(cache_path, root, identity, cached, keys, missing, day, data_identity,
                    token, split, execution, listing_ids, timings):
    selected = {key: cached[key] for key in keys}
    raw = json.dumps(selected, sort_keys=True, separators=(',', ':'), allow_nan=False)
    write_json(cache_path, dict(identity=identity, results=selected, results_sha256=sha256(raw.encode()).hexdigest()))
    merged = {k: [cached[key][k] for key in keys] for k in cached[keys[0]]}
    missing_set = set(missing)
    merged['result_reused'] = [i not in missing_set for i in range(len(keys))]
    write_json(root/'receipt.json', dict(day=day, metrics=merged, identity=data_identity,
        population_sha256=token, full_session=True, split=split, validation_opened=split=='validation',
        execution=execution, listing_ids=listing_ids, reused_candidates=len(keys)-len(missing), evaluated_candidates=len(missing)))
    write_json(root/'timings.json', timings)
    return selected
