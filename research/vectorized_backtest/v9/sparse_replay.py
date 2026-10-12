"""Verify V7 training inputs or explicitly versioned V9 validation inputs."""
import json
from pathlib import Path
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
from research.vectorized_backtest.v6.torch_backtest.sparse_replay import verify_sparse_receipt as legacy_verify


def verify_sparse_receipt(root):
    root=Path(root);record=json.loads((root/'complete.json').read_text(encoding='utf-8-sig'))
    if record.get('identity',{}).get('version')=='v6-sparse-compact-input-v1':
        return legacy_verify(root)
    if (record.get('identity',{}).get('version')!='v8-validation-sparse-compact-input-v1'
            or record.get('split_role')!='validation' or record.get('validation_opened') is not True
            or record.get('status')!='complete' or not record.get('ready_for_replay')):
        raise ValueError('Certified validation input contract required')
    for name,checksum in record['files'].items():
        path=(root/name).resolve()
        if path.parent!=root.resolve() or file_hash(path)!=checksum:raise ValueError('Validation input bytes changed')
    return record
