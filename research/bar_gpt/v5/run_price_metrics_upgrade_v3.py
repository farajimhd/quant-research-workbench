"""Owned, one-time checkpoint-boundary restart for additive validation metrics."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import torch

from .checkpoint_transition_v1 import state_hash
from .shards.contract import file_hash, write_json


def boundary_ready(saved, epoch):
    history = saved.get('history', [])
    return (saved.get('epoch') == epoch and saved.get('phase') == 'train'
            and saved.get('scheduler', {}).get('updates') == saved.get('step')
            and len(history) > 0 and history[-1].get('epoch') == epoch
            and history[-1].get('training', {}).get('complete_pass') is True
            and history[-1].get('validation', {}).get('complete_pass') is True
            and history[-1].get('learning_guard', {}).get('action', 'continue') == 'continue')


def resume_command(command, checkpoint, output, revision):
    result = list(command)
    for flag, value in (('--resume', checkpoint), ('--output', output), ('--source-revision', revision)):
        if flag in result:
            result[result.index(flag)+1] = str(value)
        else:
            result.extend((flag, str(value)))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup',type=Path,required=True)
    args=parser.parse_args(argv);setup=args.setup
    read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    config=read(setup/'deployment.json');parent=Path(config['parent']);output=Path(config['output']);source=Path(config['source'])
    def report(state,**fields):write_json(setup/'progress.json',dict(state=state,updated=time.time(),**fields))
    owner=config['owner'];old=psutil.Process(owner['pid'])
    def alive():
        try:return old.is_running() and abs(old.create_time()-owner['created'])<.01
        except psutil.NoSuchProcess:return False
    assert alive() and 'research.bar_gpt.v5.run_encoder_v3' in old.cmdline()
    assert old.cmdline()[old.cmdline().index('--output')+1]==str(parent)
    assert not output.exists()
    for name,sha in config['files'].items():
        assert file_hash(source/name)==sha,'Immutable snapshot drift: '+name
    checkpoint=parent/f"checkpoint_epoch_{config['epoch']:03d}.pt"
    write_json(setup/'owner.json',dict(pid=os.getpid(),created=psutil.Process().create_time()))
    try:
        while True:
            if (setup/'STOP').exists() or (parent/'STOP').exists():raise InterruptedError('STOP present; no handoff')
            if not alive():raise RuntimeError('Original trainer exited; no blind restart')
            status=read(parent/'progress.json')
            if status['state'] not in ('running',):raise RuntimeError('Trainer is terminal; no restart')
            report('waiting-complete-epoch',target_epoch=config['epoch'],epoch=status.get('epoch'),stage=status.get('stage'))
            if checkpoint.exists() and (parent/'results.json').exists():
                results=read(parent/'results.json')
                if results.get('history') and results['history'][-1]['epoch']>=config['epoch']:
                    saved=torch.load(checkpoint,map_location='cpu',weights_only=False)
                    if not boundary_ready(saved,config['epoch']):raise ValueError('Epoch is incomplete or learning guard stopped')
                    break
            time.sleep(.5)
        marker='OWNED_PRICE_RELATIVE_METRICS:'+config['revision']
        with (parent/'STOP').open('x',encoding='utf-8') as f:f.write(marker)
        sha=file_hash(checkpoint)
        report('draining-complete-epoch',epoch=saved['epoch'],step=saved['step'])
        deadline=time.monotonic()+180
        while alive() and time.monotonic()<deadline:time.sleep(.5)
        if alive():raise TimeoutError('Trainer did not drain; no signals sent')
        if (setup/'STOP').exists() or (parent/'STOP').read_text()!=marker:raise InterruptedError('STOP ownership changed')
        assert file_hash(checkpoint)==sha
        last=torch.load(parent/'checkpoint_last.pt',map_location='cpu',weights_only=False)
        assert state_hash(last)==state_hash(saved),'Newer/changed state exists; refuse to discard it'
        del last
        proof={key:state_hash(value) for key,value in saved.items()}
        command=resume_command(config['command'],checkpoint,output,config['revision'])
        write_json(setup/'boundary-proof.json',dict(parent_sha256=sha,state_hashes=proof,command=command,wandb=config['wandb']))
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(source))
        with (setup/'training.stdout.log').open('w') as stdout,(setup/'training.stderr.log').open('w') as stderr:
            child=subprocess.Popen(command,cwd=source,env=env,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
            write_json(setup/'child.json',dict(pid=child.pid,created=psutil.Process(child.pid).create_time(),command=command,output=str(output)))
            report('resuming',epoch=saved['epoch'],step=saved['step'],pid=child.pid)
            deadline=time.monotonic()+180
            while not (output/'checkpoint_last.pt').exists():
                if child.poll() is not None:raise RuntimeError('Resume exited; inspect preserved logs')
                if time.monotonic()>deadline:raise TimeoutError('Resume state verification timed out')
                time.sleep(.25)
            resumed=torch.load(output/'checkpoint_last.pt',map_location='cpu',weights_only=False)
            assert {k:state_hash(v) for k,v in resumed.items()}==proof,'Resume state differs'
            assert read(output/'wandb-identity.json')==config['wandb'],'W&B identity changed'
            assert file_hash(checkpoint)==sha
            write_json(setup/'resume-verification.json',dict(passed=True,state_hashes=proof,checkpoint_sha256=sha,wandb=config['wandb']))
            report('resumed-verified',epoch=saved['epoch'],step=saved['step'],pid=child.pid,output=str(output))
            del saved,resumed
            code=child.wait();write_json(setup/'exit.json',dict(exit_code=code,updated=time.time()))
    except BaseException as error:
        report('failed',error=str(error));raise


if __name__=='__main__':main()
