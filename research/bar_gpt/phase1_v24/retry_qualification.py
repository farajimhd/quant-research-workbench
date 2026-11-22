"""Preserve a failed pre-training2B qualification and its W&B identity for retry."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,shutil
from pathlib import Path
import psutil,torch
from ..v5.shards.phase1_contract import write_json


STOP_TEXT='Engineering pause: CUDA graph profiling spills into shared GPU memory. Preserve evidence and retry eager before main training.'


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True);a=p.parse_args();root=a.output.resolve()
    if os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T' or not root.is_relative_to(Path('D:/TradingML/runtimes/bar_gpt').resolve()):raise ValueError('Workstation runtime required')
    owner=json.loads((root/'process.json').read_text())
    if psutil.pid_exists(owner['pid']) and abs(psutil.Process(owner['pid']).create_time()-owner['created'])<.01:raise RuntimeError('Prior owner is still live')
    manifest=json.loads((root/'manifest.json').read_text())
    if not manifest.get('extreme_2b'):raise ValueError('Only the untrained extreme qualification is supported')
    arm=root/'e09-w4608-d11-s559'
    failure=json.loads((arm/'failure.json').read_text())
    if failure['step']!=0 or failure['seen']!=0 or (arm/'result.json').exists():raise ValueError('Training has started; preserve optimizer continuity instead')
    cp=arm/'checkpoint_last.pt'
    if cp.exists():
        saved=torch.load(cp,map_location='cpu',weights_only=False)
        if saved['step'] or saved['seen'] or saved['curve']:raise ValueError('Not a pre-training checkpoint')
        del saved
    old_proof=json.loads((root.parent/f"deployment-{manifest['source'][:9]}.json").read_text())
    new_proof=json.loads((root.parent/f'deployment-{a.revision[:9]}.json').read_text())
    if new_proof['revision']!=a.revision:raise ValueError('Deployment revision')
    allowed={'research/bar_gpt/phase1_v24/campaign.py','research/bar_gpt/phase1_v24/retry_qualification.py','research/bar_gpt/phase1_v24/README.md','research/bar_gpt/phase1_v24/DECISION.md'}
    changed={k for k in set(old_proof['files'])|set(new_proof['files']) if old_proof['files'].get(k)!=new_proof['files'].get(k)}
    if changed-allowed:raise ValueError('Scientific source changed: '+str(changed-allowed))
    stop=root/'STOP'
    if stop.exists() and stop.read_text()!=STOP_TEXT:raise ValueError('Unrelated STOP must remain in force')
    archive=root/'qualification-attempts'/manifest['source'][:9];archive.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(root/'manifest.json',archive/'campaign-manifest.json')
    for name in ('failure.json','checkpoint_last.pt','checkpoint_pending.pt','tiny.json','profile.json','lr-pilots.json','initial-parity.json','manifest.json'):
        source=arm/name
        if source.exists():source.rename(archive/name)
    if stop.exists():stop.rename(archive/'STOP')
    manifest['qualification_elapsed_seconds']=manifest.get('qualification_elapsed_seconds',0)+failure['seconds']
    receipt=dict(previous_source=manifest['source'],source=a.revision,reason='Identical scientific model/data/objective; eager backward replaces spilling CUDA graphs before any training',prior_seconds=failure['seconds'],wandb=json.loads((arm/'wandb.json').read_text()),changed_files=sorted(changed),time=time.time())
    manifest['source']=a.revision;manifest.setdefault('engineering_resumes',[]).append(receipt)
    write_json(archive/'retry-receipt.json',receipt);write_json(root/'manifest.json',manifest)
    print(json.dumps(receipt))


if __name__=='__main__':main()
