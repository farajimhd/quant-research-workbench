"""Prepare immutable Aug13 pilot or separately authorized 24/12 full campaign."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from .identity import source_hash
from .full_common import check_job
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json

def prepare(source,root,code,*,generations=50,comparison=None):
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
    root=require_runtime(root);code=Path(code)
    if (root/'pilot-job.json').exists():raise ValueError('Existing pilot must be audited, not recreated')
    original=json.loads(Path(source).read_bytes());probe=Path(original['probe_root'])/'2026-08-13.npz'
    job=dict(version='v58-pilot-v2',score_version='profit-only-trim-v2',source_sha256=source_hash(),source_job=str(source),source_job_sha256=sha(source),
        commit=json.loads((code/'deployment.json').read_bytes())['commit'],campaign=str(root),code=str(code),
        training=original['training'],training_sha256=original['training_sha256'],validation_sha256=original['validation_sha256'],
        training_days=['2026-08-13'],validation_days=[],initialization_probe=str(probe),initialization_probe_sha256=sha(probe),
        population=1024,initial_multiplier=6,seed=2021,phase2_generations=generations,mutation_probe_size=32,
        objective=dict(drawdown_weight=1.,loss_weight=1.),execution=dict(original['execution'],risk_limits_enabled=False),selection=original['selection'],batch_size=512,
        transport=str(root/'asset-bus'),workstation_share=original['workstation_share'],assets={'training':{'2026-08-13':original['assets']['training']['2026-08-13']}},asset_sha256=original['asset_sha256'],
        machines={'laptop':dict(original['machines']['laptop'],code=str(code),days=dict(training=['2026-08-13'],validation=[]))})
    if comparison is not None:raise ValueError('V58 starts fresh')
    job['selection_risk_caps']=False
    job['ticker_parent_fraction']=.25
    job['selection']=dict(job['selection'],profit_fresh_fraction=0.)
    job['machines']['laptop']['session_days']=['2026-08-13']
    check_job(job);root.mkdir(parents=True,exist_ok=True);write_json(root/'pilot-job.json',job)
    write_json(root/'manifest.json',dict(version='v58-pilot-v2',days=job['training_days'],generations=generations,population=1024,
        ticker_parent_fraction=.25,risk_limits_enabled=False,founder_market_replays_required=6144,financial_receipts_reused=False,initial_candidates=6144,initial_random=3072,initial_templates=3072,source_sha256=job['source_sha256'],commit=job['commit'],objective=job['objective'],validation_opened=False,global_only=True))
    return job

def prepare_full(*args,**kwargs):
    from .campaign_contract import prepare_full as prepare_campaign
    return prepare_campaign(*args,**kwargs)


def main():
    import sys
    if '--global-only' in sys.argv:
        p=argparse.ArgumentParser();p.add_argument('--global-only',action='store_true');p.add_argument('--source-job',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--code',type=Path,required=True);a=p.parse_args()
        from .global_contract import prepare as prepare_global
        print(prepare_global(a.source_job,a.output,a.code)['campaign']);return
    p=argparse.ArgumentParser();p.add_argument('--source-job',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--code',type=Path,required=True);p.add_argument('--full',action='store_true');p.add_argument('--workstation-code',type=Path);p.add_argument('--generations',type=int,default=50);p.add_argument('--comparison-source',type=Path);a=p.parse_args()
    if a.full and (a.workstation_code is None or a.generations!=50):p.error('Full campaign requires --workstation-code and 50 generations')
    j=(prepare_full(a.source_job,a.output,a.code,a.workstation_code) if a.full else prepare(a.source_job,a.output,a.code,generations=a.generations,comparison=a.comparison_source));print(j['campaign'])

if __name__=='__main__':main()
