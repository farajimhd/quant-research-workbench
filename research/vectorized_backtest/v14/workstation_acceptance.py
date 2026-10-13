"""Wait for V7, then execute bounded V14 correctness and speed acceptance."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,subprocess,sys,time,traceback
from pathlib import Path
from .prepare_validation import require_finished,bind_manifest
from .run_search import source_hash
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('v7-campaign','sessions','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--validation-inputs',type=Path)
    p.add_argument('--wait',action='store_true')
    p.add_argument('--stopped-v7-receipt',type=Path)
    p.add_argument('--capture-gib',type=float,default=4.)
    a=p.parse_args()
    if not .25<=a.capture_gib<=16.:raise ValueError("Capture cache must be between 0.25 and 16 GiB per worker")
    root=require_runtime(a.output);code=source_hash()
    def status(stage,**extra):
        write_json(root/'status.json',dict(stage=stage,updated_epoch=time.time(),**{'source_sha256':code,**extra}))
        print(stage,flush=True)
    def command(module,arguments,log):
        if a.stopped_v7_receipt and module in ('qualify_workstation','prepare_validation'):
            arguments=[*arguments,'--stopped-v7-receipt',a.stopped_v7_receipt]
        if module in ('qualify_workstation','run_search'):arguments=[*arguments,'--capture-gib',a.capture_gib]
        argv=[sys.executable,'-B','-u','-m','research.vectorized_backtest.v14.'+module,*map(str,arguments)]
        status(log,status='running',arguments=argv)
        with (root/(log+'.log')).open('a',encoding='utf-8') as stream:
            subprocess.run(argv,stdout=stream,stderr=subprocess.STDOUT,check=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    try:
        while True:
            try:require_finished(a.v7_campaign,a.stopped_v7_receipt);break
            except RuntimeError as error:
                if not a.wait:raise
                state=json.loads((a.v7_campaign/'status.json').read_text(encoding='utf-8-sig'))
                if state.get('status') in ('failed','interrupted'):raise RuntimeError('V7 did not complete; workstation work remains blocked') from error
                status('Waiting for V7 completion and released ownership',status='waiting',v7_completed=state.get('completed_generations'))
                time.sleep(30)
        command('qualify_synthetic',['--output',root/'synthetic'],'synthetic')
        saved=json.loads((a.v7_campaign/'checkpoint.json').read_text(encoding='utf-8-sig'))
        days=list(saved['contract']['input_receipts'])
        command('qualify_workstation',['--campaign',a.v7_campaign,'--output',root/'two-session','--days',*days[:2]],'two-session')
        validation=a.validation_inputs or root/'validation-inputs'
        command('prepare_validation',['--v7-campaign',a.v7_campaign,'--sessions',a.sessions,'--output',validation,
            '--ranking-before-features'],'prepare-validation')
        command('qualify_workstation',['--campaign',a.v7_campaign,'--output',root/'all-training'],'all-training')
        active=json.loads((a.v7_campaign/'active.json').read_text(encoding='utf-8-sig'));args=active['arguments']
        get=lambda name:args[args.index('--'+name)+1]
        training=root/'training.json'
        bind_manifest(Path(get('inputs')),Path(get('history')),days,'training',training,Path(get('feature-cache')))
        command('run_search',['--training',training,'--validation',validation/'validation.json','--output',root/'full-generation',
            '--population',saved['contract']['population'],'--batch-size',saved['contract']['batch_size'],
            '--session-workers',saved['contract']['session_workers'],'--generations',2,
            '--initial-population',a.v7_campaign/'checkpoint.json'],'full-generation')
        old=json.loads((root/'all-training/v7/profile.json').read_text(encoding='utf-8-sig'))
        wall=json.loads((root/'full-generation/training/generation-0002/wall.json').read_text(encoding='utf-8-sig'))
        baseline=old['wall_seconds'][-1];current=wall['full_generation_seconds']
        accepted=current<baseline
        result=dict(status='passed' if accepted else 'full_generation_speed_gate_failed',source_sha256=code,
            v7_warm_training_seconds=baseline,v14_full_generation_seconds=current,speedup=baseline/current,
            includes_validation_selection_publication=True,validation_feedback=False,
            checkpoint_sha256=file_hash(a.v7_campaign/'checkpoint.json'),production_campaign_started=False)
        write_json(root/'acceptance.json',result);status('Acceptance complete',**result)
        return 0 if accepted else 2
    except BaseException as error:
        status('Acceptance failed',status='failed',error=str(error),traceback=traceback.format_exc())
        raise


if __name__=='__main__':raise SystemExit(main())
