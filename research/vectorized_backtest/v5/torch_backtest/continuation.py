"""Explicit source revision before the first completed generation."""
import json
import shutil
from .runtime import file_hash, write_json


def import_first_generation(parent, output, identity, population_hash):
    previous=json.loads((parent/'identity.json').read_text())
    for key in ('sessions','split','objective','financial_settings','features',
                'searchable_feature_indices','policy_coordinates','program_maximum_nodes','stages','arguments'):
        if previous[key] != identity[key]:
            raise ValueError('Continuation changes campaign contract: '+key)
    status=json.loads((parent/'status.json').read_text())
    exit_status=json.loads((parent/'exit.json').read_text())
    if status['status'] != 'interrupted' or exit_status.get('exit') is None:
        raise ValueError('Continuation requires an explicitly stopped parent')
    import psutil
    if psutil.pid_exists(status['worker_pid']):
        raise ValueError('Parent worker PID still exists; verify ownership before continuation')
    if (parent/'checkpoint.json').exists() or (parent/'generation_000'/'generation.json').exists():
        raise ValueError('This source revision only supports the unfinished first generation')
    if (output/'continuation.json').exists():
        raise ValueError('Continuation already imported; use exact resume')
    receipts=[]
    for path in sorted((parent/'generation_000').glob('session_*/receipt.json')):
        receipt=json.loads(path.read_text())
        if receipt['population_sha256'] != population_hash:
            raise ValueError('Continuation population/RNG reconstruction mismatch')
        if file_hash(path.parent/'fills.pt') != receipt['ledger_sha256']:
            raise ValueError('Continuation ledger hash mismatch')
        destination=output/'generation_000'/path.parent.name
        if destination.exists():
            raise ValueError('Continuation destination must be empty')
        destination.mkdir(parents=True)
        for name in ('receipt.json','fills.pt'):
            shutil.copy2(path.parent/name,destination/name)
            if file_hash(path.parent/name) != file_hash(destination/name):
                raise ValueError('Continuation copy verification failed')
        receipts.append(dict(path=str(path),sha256=file_hash(path)))
    write_json(output/'continuation.json',dict(parent=str(parent),
        parent_identity_sha256=file_hash(parent/'identity.json'),
        previous_code_hash=previous['code_hash'],code_hash=identity['code_hash'],
        population_sha256=population_hash,receipts=receipts,
        rng='Initial seed reconstructed exactly; no completed generation',validation_opened=False))
