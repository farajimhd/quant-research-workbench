import json
from pathlib import Path
from .runtime import file_hash
VERSION="v6-sparse-compact-input-v1"
def legacy_verify(root):
    """Verify the immutable boundary without allocating feature/device arrays."""
    root=Path(root)
    receipt=json.loads((root/'complete.json').read_text())
    if receipt.get('identity',{}).get('version')!=VERSION or not receipt.get('ready_for_replay') or receipt.get('validation_opened'):
        raise ValueError('Require a completed training-only sparse input certificate')
    for name,checksum in receipt['files'].items():
        path=(root/name).resolve()
        if path.parent!=root.resolve() or file_hash(path)!=checksum:
            raise ValueError('Sparse input file identity/hash changed')
    return receipt

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
