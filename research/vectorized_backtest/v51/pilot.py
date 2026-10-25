"""Direct phase2 evolution on the single authorized training session."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):
    os.environ.setdefault(key,'1')
import argparse,json,queue,threading,time,traceback
from pathlib import Path
import psutil
from .full_controller import Controller
from .full_common import check_job,config
from .identity import digest
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v42.distributed_assets import ensure_day,sha
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run


class Pilot(Controller):
    def __init__(self,job):
        self.job=job;self.root=Path(job['campaign']);self.stage='Starting direct phase2 pilot'
        self.owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time())
        self.view={};self.status='running';self.lock=threading.RLock();self.worker=None
        self.events=queue.Queue(maxsize=1);self.stop=threading.Event()

    def publish(self,**extra):
        with self.lock:
            self.view.update(extra)
            write_json(self.root/'full-status.json',dict(status=self.status,stage=self.stage,
                owner=self.owner,source_sha256=self.job['source_sha256'],updated_epoch=time.time(),**self.view))

    def pump(self):
        while not self.stop.wait(1):
            try:event=self.events.get_nowait()
            except queue.Empty:self.publish();continue
            self.publish(worker_progress=event)

    def evaluate(self,role,days,members,generation):
        if role!='training' or days!=self.job['training_days']:
            raise ValueError('This pilot cannot open another session or validation data')
        if (self.root/'STOP').exists():raise InterruptedError('V51 pilot stop requested')
        payload=[m.payload() for m in members];token=digest(payload);day=days[0]
        self.publish(phase=2,generation=generation,completed_generations=generation-1,
                     evaluated_population=len(members),completed_sessions=0,total_sessions=1)
        width=len(json.loads((Path(self.worker.config['history'])/day/'complete.json').read_bytes())['listing_ids'])
        self.worker.run(day,None,payload,generation,token,width)
        receipt_path=Path(self.worker.config['output'])/f'generation-{generation:04d}'/day/'receipt.json'
        receipt=json.loads(receipt_path.read_bytes())
        if (receipt['population_sha256']!=token or not receipt['full_session']
            or receipt['validation_opened'] or receipt['execution']!=self.job['execution']
            or any(x is not None for x in receipt['case_owners'])):
            raise ValueError('Pilot financial receipt changed')
        return [receipt['metrics']],{day:dict(receipt=str(receipt_path),receipt_sha256=sha(receipt_path))}

    def run(self):
        check_job(self.job)
        with owned_run(self.root,version='v51-phase2-pilot-v1'):
            write_json(self.root/'owner.json',self.owner)
            cfg=config(self.job,'laptop');cfg.update(output=str(self.root/'replay'),
                boundary_stop=str(self.root),compile_cache_root=str(self.root/'compiler-cache'))
            import torch
            from .session_lanes import Worker
            cfg['lane_memory_bytes']=torch.cuda.mem_get_info()[0]*.8
            self.worker=Worker(cfg,self.events,0)
            original_replay=self.worker.replay
            def interruptible_replay(*args,**kwargs):
                progress=kwargs.get('progress')
                def report(done,total):
                    if (self.root/'STOP').exists():raise InterruptedError('V51 pilot stop requested')
                    if progress:progress(done,total)
                return original_replay(*args,**dict(kwargs,progress=report))
            self.worker.replay=interruptible_replay
            thread=threading.Thread(target=self.pump,daemon=True);thread.start()
            try:
                self.stage='Verify/stage certified Aug13 training inputs';self.publish()
                ensure_day(self.job,'laptop','training',self.job['training_days'][0])
                self.global_run({},self.job['training_days'],[])
                write_json(self.root/'complete.json',dict(status='complete',generations=self.job['phase2_generations'],
                    global_complete_sha256=sha(self.root/'global/complete.json'),validation_opened=False))
                self.status='complete';self.stage='Pilot complete';self.publish()
            except BaseException as error:
                self.status='interrupted' if (self.root/'STOP').exists() else 'failed'
                self.stage=str(error);self.publish()
                write_json(self.root/'failure.json',dict(traceback=traceback.format_exc(),epoch=time.time()))
                raise
            finally:
                self.stop.set();thread.join(5);self.worker.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args()
    from research.vectorized_backtest.v42.windows_process import quiet_compilers
    quiet_compilers()
    os.environ.update(V42_CPU_RESERVE_GIB='12',V42_CPU_ADMISSION_GIB='8',V42_REPRODUCTION_WORKERS='1',V51_MASK_QUOTES='0')
    Pilot(json.loads(a.job.read_bytes())).run()


if __name__=='__main__':main()
