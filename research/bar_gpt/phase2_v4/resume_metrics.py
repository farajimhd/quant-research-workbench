"""Audited metric-only resume preserving the exact scientific/W&B contract."""
import json
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.contract import digest,write_json,file_hash

ORIGINAL='95cad7fe0d4cdf1735d1b1c5de85467b632a5971'
ALLOWED={f'research/bar_gpt/phase2_v4/{name}' for name in (
    'metrics.py','forecast_metrics.py','tokenizer.py','run_train.py','resume_metrics.py',
    'README.md','test_forecast_metrics.py','audit_forecast.py')}
# Previously committed removal of V4 profiling injection from V3; not a train import.
ALLOWED.add('research/bar_gpt/phase2_v3/profile.py')


def changed_files(before,after):
    changed=sorted(k for k in before.keys()|after.keys() if before.get(k)!=after.get(k))
    if set(changed)-ALLOWED:raise ValueError('Non-instrumentation source changed: '+str(set(changed)-ALLOWED))
    return changed


def preserve_contract(args,contract):
    if not args.resume:return contract
    import torch
    saved=torch.load(args.resume,map_location='cpu',weights_only=False)
    saved=load_checkpoint(args.resume,saved['contract']);original=saved['contract']
    if original['source']!=ORIGINAL:raise ValueError('Unsupported metric ancestor')
    candidate=dict(contract,source=ORIGINAL)
    if digest(candidate)!=digest(original):raise ValueError('Scientific/numerical contract changed')
    root=args.output.parent
    before=json.loads((root/f'deployment-{ORIGINAL[:9]}.json').read_text())
    after=json.loads((root/f'deployment-{args.source_revision[:9]}.json').read_text())
    if before['revision']!=ORIGINAL or after['revision']!=args.source_revision:raise ValueError('Deployment differs')
    changes=changed_files(before['files'],after['files'])
    write_json(args.output/f'metrics-resume-{args.source_revision[:9]}-{saved["step"]}.json',dict(
        original_source=ORIGINAL,running_source=args.source_revision,changed_files=changes,
        step=saved['step'],cursor=saved['cursor'],contract_sha256=digest(original),
        policy='Evaluation diagnostics and last_raw reporting metadata only; training model/loss/optimizer/schedule/traversal/RNG unchanged'))
    return candidate


def upload_audit(args,contract,reporter):
    if not args.forecast_audit:return
    if not args.resume:raise ValueError('Audit upload requires exact checkpoint resume')
    report=json.loads(args.forecast_audit.read_text())
    if (report['state']!='complete' or report['checkpoint_sha256']!=file_hash(args.resume)
        or report['contract_sha256']!=digest(contract) or report['source']!=args.source_revision):
        raise ValueError('Audit not bound to this checkpoint/source/contract')
    # Supplement the historical row; do not overwrite existing controls whose
    # training-interval calibration may already have reset at this checkpoint.
    reporter.log({f'validation_{k}':v for k,v in report['metrics'].items() if k.startswith('forecast/')},report['step'])
    reporter.run.config.update({'forecast_metrics_version':report['version'],
        'forecast_metrics_audit_sha256':file_hash(args.forecast_audit)},allow_val_change=True)
