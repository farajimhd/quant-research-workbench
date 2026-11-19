"""Narrow, audited instrumentation upgrade; scientific contract stays exact."""
import json
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.contract import digest,write_json

ORIGINAL='6815d06388aec7ca6cdf2a4f207bcf0c19c9effa'
ALLOWED={f'research/bar_gpt/phase2_v3/{name}' for name in (
    'metrics.py','horizon_metrics.py','train.py','run_train.py','resume_metrics.py',
    'panel.py','README.md','test_horizon_metrics.py','full_pass.py','profile_horizons.py')}


def changed_files(before,after):
    changed=sorted(k for k in before.keys()|after.keys() if before.get(k)!=after.get(k))
    if set(changed)-ALLOWED:raise ValueError('Instrumentation resume changes non-metric source: '+str(set(changed)-ALLOWED))
    return changed


def preserve_contract(args,contract):
    if not args.resume:return contract
    # Verify complete checkpoint/proof before accepting any contract transition.
    import torch
    saved=torch.load(args.resume,map_location='cpu',weights_only=False)
    saved=load_checkpoint(args.resume,saved['contract'])
    original=saved['contract']
    if original['source']!=ORIGINAL:raise ValueError('Unsupported instrumentation ancestor')
    candidate=dict(contract,source=original['source'])
    if digest(candidate)!=digest(original):raise ValueError('Scientific or numerical configuration changed')
    root=args.output.parent
    before=json.loads((root/f'deployment-{ORIGINAL[:9]}.json').read_text())
    after=json.loads((root/f'deployment-{args.source_revision[:9]}.json').read_text())
    if before['revision']!=ORIGINAL or after['revision']!=args.source_revision:raise ValueError('Deployment identity differs')
    changes=changed_files(before['files'],after['files'])
    write_json(args.output/f'metrics-resume-{args.source_revision[:9]}-{saved["step"]}.json',dict(
        original_source=ORIGINAL,running_source=args.source_revision,changed_files=changes,
        step=saved['step'],cursor=saved['cursor'],contract_sha256=digest(original),
        policy='Only validation metrics, reporting and audited resume support changed; model/optimizer/data/scheduler/RNG unchanged'))
    return candidate
