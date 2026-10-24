"""Reuse sealed V42 financial results, never its objective scores or offspring."""
import json
from pathlib import Path
from research.vectorized_backtest.v42.objectives import local_panel
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
from research.vectorized_backtest.v42.publication import write_json


def import_receipt(manifest, day, contract, token, size, output, identity, source_token=None):
    if manifest['version'] != 'v44-v42-initial-reuse-v1' or manifest['validation_opened'] is not False:
        raise ValueError('Invalid V44 initial reuse manifest')
    entry = manifest['sessions'][day]
    prior = entry['contract']
    for key in ('day', 'seed', 'population', 'initial_multiplier', 'dataset', 'execution',
                'feature_semantics', 'backend', 'device', 'batch_size'):
        if prior[key] != contract[key]:
            raise ValueError('Fair comparison contract changed: ' + key)
    if size != 6144 or token != entry['population_sha256']:
        raise ValueError('Exact original 6144 candidates required')
    if file_hash(entry['seal']) != entry['seal_sha256'] or file_hash(entry['receipt']) != entry['receipt_sha256']:
        raise ValueError('Sealed initial evidence changed')
    record = json.loads(Path(entry['receipt']).read_bytes())
    if (record['identity'] != identity or record['population_sha256'] != token
            or record['execution'] != contract['execution'] or record['day'] != day
            or record['split'] != 'training' or record['validation_opened'] is not False
            or not record['full_session'] or len(record['metrics']['net_pnl']) != size):
        raise ValueError('Initial financial receipt identity changed')
    local_panel([record['metrics']])
    record['reuse_provenance'] = dict(version=manifest['version'], source_receipt=entry['receipt'],
        source_receipt_sha256=entry['receipt_sha256'], source_seal_sha256=entry['seal_sha256'],
        source_contract=prior, old_scores_imported=False, offspring_imported=False,
        exact_initial_population_sha256=token)
    write_json(Path(output) / 'generation-0001' / day / 'receipt.json', record)
    return True
