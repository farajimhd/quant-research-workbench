"""Prepare only the authorized Aug13 direct-global pilot; never all sessions."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from .identity import source_hash
from .full_common import check_job
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json

def prepare(source,root,code):
    root=Path(root);code=Path(code)
    if (root/'pilot-job.json').exists():raise ValueError('Existing pilot must be audited, not recreated')
    original=json.loads(Path(source).read_bytes());probe=Path(original['probe_root'])/'2026-08-13.npz'
    job=dict(version='v52-pilot-v1',source_sha256=source_hash(),source_job=str(source),source_job_sha256=sha(source),
        commit=json.loads((code/'deployment.json').read_bytes())['commit'],campaign=str(root),code=str(code),
        training=original['training'],training_sha256=original['training_sha256'],validation_sha256=original['validation_sha256'],
        training_days=['2026-08-13'],validation_days=[],initialization_probe=str(probe),initialization_probe_sha256=sha(probe),
        population=1024,initial_multiplier=6,seed=2021,phase2_generations=50,
        objective=dict(drawdown_weight=0.,loss_weight=0.),execution=original['execution'],selection=original['selection'],batch_size=1024,
        transport=str(root/'asset-bus'),workstation_share=original['workstation_share'],assets={'training':{'2026-08-13':original['assets']['training']['2026-08-13']}},asset_sha256=original['asset_sha256'],
        machines={'laptop':dict(original['machines']['laptop'],code=str(code),days=dict(training=['2026-08-13'],validation=[]))})
    check_job(job);root.mkdir(parents=True,exist_ok=True);write_json(root/'pilot-job.json',job)
    write_json(root/'manifest.json',dict(version='v52-pilot-v1',days=job['training_days'],generations=50,population=1024,
        initial_candidates=6144,initial_random=3072,initial_templates=3072,source_sha256=job['source_sha256'],commit=job['commit'],objective=job['objective'],validation_opened=False,global_only=True))
    return job

def prepare_full(source,root,code):
    """Fresh workstation-only job; the pilot checkpoint is never an input."""
    root=Path(root);code=Path(code)
    if (root/'full-job.json').exists():raise ValueError('Existing campaign must be audited, not recreated')
    original=json.loads(Path(source).read_bytes())
    train=json.loads(Path(original['training']).read_bytes())['days']
    valid=json.loads(Path(original['validation']).read_bytes())['days']
    from research.vectorized_backtest.v42.probes import global_probe
    probe=global_probe(original['training'],train,original['probe_root'])
    job=dict(version='v52-full-v1',source_sha256=source_hash(),source_job=str(source),source_job_sha256=sha(source),
        commit=json.loads((code/'deployment.json').read_bytes())['commit'],campaign=str(root),code=str(code),
        training=original['training'],training_sha256=original['training_sha256'],
        validation=original['validation'],validation_sha256=original['validation_sha256'],
        training_days=train,validation_days=valid,initialization_probe=str(probe),initialization_probe_sha256=sha(probe),
        population=1024,initial_multiplier=6,seed=2021,phase2_generations=100,
        objective=dict(drawdown_weight=0.,loss_weight=0.),execution=original['execution'],selection=original['selection'],batch_size=1024,
        transport=str(root/'asset-bus'),workstation_share=original['workstation_share'],assets=original['assets'],asset_sha256=original['asset_sha256'],
        machines={'workstation':dict(original['machines']['workstation'],code=str(code),days=dict(training=train,validation=valid))})
    check_job(job);root.mkdir(parents=True,exist_ok=True);write_json(root/'full-job.json',job)
    write_json(root/'manifest.json',dict(version='v52-full-v1',training_days=train,validation_days=valid,
        generations=100,population=1024,initial_candidates=6144,source_sha256=job['source_sha256'],commit=job['commit'],
        objective=job['objective'],validation_display_only=True,global_only=True,node='workstation'))
    return job

def main():
    p=argparse.ArgumentParser();p.add_argument('--source-job',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--code',type=Path,required=True);p.add_argument('--full',action='store_true');a=p.parse_args()
    j=(prepare_full if a.full else prepare)(a.source_job,a.output,a.code);print(j['campaign'])

if __name__=='__main__':main()
