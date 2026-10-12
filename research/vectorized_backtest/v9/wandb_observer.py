"""Independent, optional W&B uploader; never imported or awaited by search."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
from pathlib import Path
import time
from .wandb_metrics import packet,read,checksum


def require_runtime(path):
    # Avoid importing the legacy package initializer (which loads Torch/compiler).
    root=Path('D:/TradingML/runtimes').resolve();path=Path(path).resolve()
    if not root.is_dir() or not path.is_relative_to(root) or path==root:
        raise ValueError('Required output is beneath D:/TradingML/runtimes')
    path.mkdir(parents=True,exist_ok=True)
    return path


def write_json(path,value):
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def settings(wandb):
    # Explicit overrides: an account setting must not enable extra uploads.
    return wandb.Settings(console='off',disable_code=True,disable_git=True,save_code=False,
        disable_job_creation=True,x_disable_stats=True,x_disable_meta=True,
        x_disable_machine_info=True,x_save_requirements=False,quiet=True,init_timeout=30,login_timeout=15,
        ignore_globs=['*.log','*.patch','requirements.txt','wandb-metadata.json'])


def reporter_heartbeat(run, last_sent, *, now=None):
    """Keep sparse scalar runs live without adding history rows or system data.

    Empty SDK filestream requests may not advance the cloud heartbeat. A tiny
    summary update describes reporter liveness only, not optimizer health.
    """
    now=time.monotonic() if now is None else now
    if run is not None and now-last_sent>=60:
        run.summary['reporter/heartbeat_unix']=time.time()
        return now
    return last_sent


def upload(campaign,output,*,project='optimization-v9',entity=None,mode='online',follow=False,poll=30):
    """Network waits are confined to this independently launched process.

    SDK history is the resume cursor, not a local enqueue acknowledgement.
    Local summaries are immutable and can always be regenerated from receipts.
    """
    campaign=Path(campaign).resolve();output=require_runtime(output)
    if output==campaign or campaign.is_relative_to(output):raise ValueError('Separate observer output required')
    if poll<5:raise ValueError('Minimum polling interval is five seconds')
    import psutil
    lock=output/'observer.lock'
    if lock.exists():
        owner=read(lock)
        try:
            if abs(psutil.Process(owner['pid']).create_time()-owner['created'])<.01:
                raise RuntimeError('Observer already running')
        except psutil.NoSuchProcess:pass
        lock.unlink()
    with lock.open('x',encoding='utf-8') as stream:
        json.dump(dict(pid=os.getpid(),created=psutil.Process().create_time()),stream)
    run=None
    try:
        contract=dict(campaign=str(campaign),project=project,entity=entity,mode=mode,
                      version='v9-scalar-observer-v1')
        identity=output/'identity.json'
        if identity.exists() and read(identity)!=contract:raise ValueError('Observer destination contract changed')
        write_json(identity,contract)
        run_id='v9-'+checksum(contract)[:20]
        summaries=require_runtime(output/'summaries');sdk=require_runtime(output/'sdk')
        def status(state,**fields):
            write_json(output/'status.json',dict(status=state,updated_epoch=time.time(),run_id=run_id,**fields))
        # Do not put SDK files in the campaign or repository; no broad directory sync.
        os.environ.update(WANDB_DISABLE_CODE='true',WANDB_CONSOLE='off',
                          WANDB_DATA_DIR=str(output/'data'),WANDB_CACHE_DIR=str(output/'cache'),
                          WANDB_CONFIG_DIR=str(output/'config'))
        import wandb
        next_generation=1
        last_heartbeat=float('-inf')
        while True:
            if (output/'STOP').exists():status('stopped');break
            value=packet(campaign,next_generation)
            if value is not None:
                target=summaries/f'generation-{next_generation:04d}.json'
                if target.exists() and read(target)!=value:raise ValueError('Published generation summary changed')
                write_json(target,value)
                try:
                    if run is None:
                        run=wandb.init(project=project,entity=entity,id=run_id,resume='allow',
                            name=campaign.name,mode=mode,dir=str(sdk),save_code=False,
                            config={'optimizer_version':'v9','validation_feedback':False},settings=settings(wandb))
                        for prefix in ('train','validation','leader','time','selection'):
                            run.define_metric(prefix+'/*',step_metric='generation')
                        # Resume from server-acknowledged history, not local queued rows.
                        if mode=='online' and run.step>next_generation:
                            next_generation=run.step
                            continue
                    run.log(value,step=next_generation,commit=True)
                    status('queued_to_sdk',generation=next_generation,url=run.url)
                    next_generation+=1
                except Exception as error:
                    # Never persist SDK exception text: it may include credentials/headers.
                    status('upload_retry',generation=next_generation,error_type=type(error).__name__)
                    if run is not None:
                        try:run.finish(exit_code=1)
                        except Exception:pass
                    run=None
                    if not follow:raise RuntimeError('W&B upload failed; local summaries retained') from None
                    time.sleep(poll)
                    continue
                continue
            current=read(campaign/'status.json') if (campaign/'status.json').exists() else {}
            if current.get('status') in ('failed','interrupted'):
                status('upstream_stopped',last_generation=next_generation-1)
                break
            finished=current.get('status')=='complete' and next_generation>current.get('completed_generations',0)
            if finished or not follow:
                if run is not None:run.finish();run=None
                status('complete' if finished else 'caught_up',last_generation=next_generation-1)
                break
            status('waiting_for_generation',next_generation=next_generation)
            try:
                last_heartbeat=reporter_heartbeat(run,last_heartbeat)
            except Exception as error:
                status('heartbeat_retry',next_generation=next_generation,error_type=type(error).__name__)
            time.sleep(poll)
    finally:
        try:
            if run is not None:run.finish()
        finally:lock.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--project',default='optimization-v9');p.add_argument('--entity')
    p.add_argument('--mode',choices=('online','offline'),default='online')
    p.add_argument('--follow',action='store_true');p.add_argument('--poll',type=int,default=30)
    p.add_argument('--env-file',type=Path)
    a=p.parse_args()
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3],a.env_file),verbose=False)
    a.entity=a.entity or os.environ.get('WANDB_ENTITY') or None
    del a.env_file
    upload(**vars(a))


if __name__=='__main__':main()
