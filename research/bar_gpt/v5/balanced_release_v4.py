"""Require an explicit audited balanced cohort; never freeze a partial extractor."""

import json
from pathlib import Path
from .cohort_v1 import members
from .shards.contract import digest, file_hash


def require_release(dataset, split_manifest=None):
    root = Path(dataset).resolve()
    path = root / 'balanced-release.json'
    if not path.is_file():
        raise ValueError('V4 requires the completed audited balanced release; extraction is not a release')
    release = json.loads(path.read_text())
    body = {k: v for k, v in release.items() if k != 'hash'}
    if release.get('hash') != digest(body) or body.get('version') != 'bargpt-balanced-release-v4':
        raise ValueError('Invalid balanced release seal')
    if body.get('training_ready') is not True:
        raise ValueError('Balanced dataset has not passed its release audit')
    cohort_hash, components = members(root, require_all=True)
    if not cohort_hash or cohort_hash != body.get('cohort_hash'):
        raise ValueError('Balanced cohort identity differs')
    source = root.parent / 'phase1-coarse-supplement-v1-r2'
    result = source / 'result.json'
    if not result.is_file() or file_hash(result) != body.get('supplement_result_sha256'):
        raise ValueError('Balanced release must bind the completed r2 extraction')
    status = json.loads(result.read_text())
    if status.get('failed') or status.get('state') not in {'complete', 'inventory_exhausted', 'quotas_reached', 'budget_reached'}:
        raise ValueError('Coarse supplement is incomplete or failed')
    bindings = body.get('member_contracts', {})
    if len(components) < 3:
        raise ValueError('V4 requires corrected base, liquid supplement and new coarse component')
    if set(bindings) != {name for name, _ in components}:
        raise ValueError('Balanced member inventory differs')
    for name, member in components:
        if file_hash(member / 'contract.json') != bindings[name]:
            raise ValueError('Balanced member contract changed')
    if split_manifest is None:
        raise ValueError('V4 requires the audited balanced split manifest')
    if file_hash(Path(split_manifest)) != body.get('split_sha256'):
        raise ValueError('V4 split is not the audited balanced split')
    split = json.loads(Path(split_manifest).read_text())
    if Path(split['dataset']).resolve() != root or split.get('cohort_hash') != cohort_hash:
        raise ValueError('V4 split belongs to another dataset')
    return release
