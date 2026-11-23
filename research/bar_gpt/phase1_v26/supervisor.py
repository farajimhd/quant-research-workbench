"""Finite in-session supervisor; restart only checkpointed W&B transport failures."""
import json,os,subprocess,sys,time
from pathlib import Path
import psutil
from ..v5.shards.phase1_contract import write_json


RETRY_EXIT=75


def supervise(arguments,console_log,limit=3,delay=30):
    if not 0<=limit<=3 or not 0<=delay<=60:raise ValueError('Bounded restart policy')
    if '--output' not in arguments:raise ValueError('Run output required')
    root=Path(arguments[arguments.index('--output')+1]).resolve()
    if not root.is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime authority')
    if not root.parent.is_dir():raise FileNotFoundError('Runtime parent must exist')
    command=[sys.executable,'-B','-u','-m','research.bar_gpt.phase1_v26.full_launch','--console-log',str(console_log),*arguments]
    attempts=[]
    for attempt in range(limit+1):
        if (root/'STOP').exists() or (root.parent/'STOP').exists():return 0
        child=subprocess.Popen(command,stdin=subprocess.DEVNULL);owner=psutil.Process(child.pid)
        descendants={};row=dict(attempt=attempt,pid=child.pid,created=owner.create_time(),started=time.time());attempts.append(row)
        published=root.exists()
        initial=root/'supervisor.json' if published else root.parent/f'supervisor-{root.name}.json'
        write_json(initial,dict(pid=os.getpid(),attempts=attempts,restart_limit=limit))
        while child.poll() is None:
            if not published and root.is_dir():
                write_json(root/'supervisor.json',dict(pid=os.getpid(),attempts=attempts,restart_limit=limit));published=True
            try:
                for p in owner.children(recursive=True):descendants[(p.pid,p.create_time())]=p
            except psutil.NoSuchProcess:pass
            time.sleep(1)
        row.update(exit_code=child.returncode,ended=time.time())
        alive=[p for p in descendants.values() if p.is_running()]
        if alive:_,alive=psutil.wait_procs(alive,timeout=30)
        row['remaining_owned_pids']=[p.pid for p in alive]
        write_json(root/'supervisor.json' if root.is_dir() else initial,dict(pid=os.getpid(),attempts=attempts,restart_limit=limit))
        if alive:raise RuntimeError('Owned descendants still live; refusing overlapping restart')
        if child.returncode!=RETRY_EXIT or attempt==limit:return child.returncode
        # Only the trainer emits this receipt AFTER a complete checkpoint save.
        receipt=json.loads((root/'reporting-retry.json').read_text())
        proof=json.loads((root/'checkpoint_last.proof.json').read_text())
        if receipt['pid']!=child.pid or receipt['checkpoint_sha']!=proof['sha256']:raise RuntimeError('Retry receipt does not belong to this child/checkpoint')
        if '--resume' in command:command[command.index('--resume')+1]=str(root/'checkpoint_last.pt')
        else:command+=['--resume',str(root/'checkpoint_last.pt')]
        time.sleep(delay)
    return 1
