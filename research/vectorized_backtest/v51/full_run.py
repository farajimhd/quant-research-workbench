"""Workstation-only global evolution over the certified24/12 split."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,gc,json,threading
from pathlib import Path
from .pilot import Pilot
from .full_common import check_job,config
from .identity import digest
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v42.distributed_assets import ensure_day,sha
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run


class FullRun(Pilot):
    def __init__(self,job):
        super().__init__(job);self.worker_role=None
        self.view.update(run_kind='full',total_generations=job['phase2_generations'],
            training_sessions=24,validation_sessions=12,node='workstation')

    def guard(self):
        if (self.root/'STOP').exists():raise InterruptedError('V51 full run stop requested')

    def get_worker(self,role):
        if self.worker_role==role:return self.worker
        if self.worker:self.worker.close();self.worker=None;gc.collect()
        import torch
        if torch.cuda.is_initialized():torch.cuda.empty_cache()
        from .session_lanes import Worker
        cfg=config(self.job,'workstation',role);cfg.update(output=str(self.root/'replay'/role),
            boundary_stop=str(self.root),compile_cache_root=str(self.root/'compiler-cache'))
        cfg['lane_memory_bytes']=torch.cuda.mem_get_info()[0]*.8
        self.worker=Worker(cfg,self.events,0);self.worker_role=role
        original=self.worker.replay
        def interruptible(*args,**kwargs):
            progress=kwargs.get('progress')
            def report(done,total):
                self.guard()
                if progress:progress(done,total)
            return original(*args,**dict(kwargs,progress=report))
        self.worker.replay=interruptible
        return self.worker

    def evaluate(self,role,days,members,generation):
        if role not in ('training','validation') or days!=self.job[role+'_days']:
            raise ValueError('Replay split/session list differs from sealed job')
        self.guard();worker=self.get_worker(role);payload=[m.payload() for m in members]
        token=digest(payload);ordered=[];receipts={}
        for count,day in enumerate(days):
            self.guard()
            self.publish(phase=2,generation=generation,completed_generations=generation-1,
                evaluated_population=len(members),evaluation_split=role,current_day=day,
                completed_sessions=count,total_sessions=len(days),worker_progress={})
            ensure_day(self.job,'workstation',role,day);self.guard()
            # Result identity changes with day. Immutable disk cache remains reusable.
            worker.resident_results=None
            width=len(json.loads((Path(worker.config['history'])/day/'complete.json').read_bytes())['listing_ids'])
            next_day=days[count+1] if count+1<len(days) else None
            marker=self.root/'data-ready/workstation'/role/((next_day or '')+'.json')
            worker.run(day,next_day if marker.exists() else None,payload,generation,token,width)
            p=Path(worker.config['output'])/f'generation-{generation:04d}'/day/'receipt.json'
            receipt=json.loads(p.read_bytes())
            if (receipt['population_sha256']!=token or not receipt['full_session']
                or receipt['split']!=role or receipt['validation_opened']!=(role=='validation')
                or receipt['execution']!=self.job['execution'] or any(x is not None for x in receipt['case_owners'])):
                raise ValueError('Financial replay contract changed')
            ordered.append(receipt['metrics']);receipts[day]=dict(receipt=str(p),receipt_sha256=sha(p))
            self.publish(completed_sessions=count+1,total_sessions=len(days))
        return ordered,receipts

    def run(self):
        check_job(self.job)
        with owned_run(self.root,version='v51-workstation-full-v1'):
            write_json(self.root/'owner.json',self.owner)
            thread=threading.Thread(target=self.pump,daemon=True);thread.start()
            try:
                self.global_run({},self.job['training_days'],self.job['validation_days'])
                write_json(self.root/'complete.json',dict(status='complete',generations=self.job['phase2_generations'],
                    global_complete_sha256=sha(self.root/'global/complete.json'),validation_display_only=True))
                self.status='complete';self.stage='Full run complete';self.publish()
            except BaseException as error:
                import time,traceback
                self.status='interrupted' if (self.root/'STOP').exists() else 'failed';self.stage=str(error);self.publish()
                write_json(self.root/'failure.json',dict(traceback=traceback.format_exc(),epoch=time.time()));raise
            finally:
                self.stop.set();thread.join(5)
                if self.worker:self.worker.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args()
    from research.vectorized_backtest.v42.windows_process import quiet_compilers
    quiet_compilers()
    os.environ.update(V42_CPU_RESERVE_GIB='128',V42_CPU_ADMISSION_GIB='32',V42_REPRODUCTION_WORKERS='1',V51_MASK_QUOTES='0')
    FullRun(json.loads(a.job.read_bytes())).run()

if __name__=='__main__':main()
