"""Low-priority statistics process and nonblocking UI snapshot delivery."""
import json,os,threading,time,multiprocessing
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor


def initialize():
    os.environ.update(PYTHONDONTWRITEBYTECODE='1',POLARS_MAX_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',CUDA_VISIBLE_DEVICES='')
    import psutil
    p=psutil.Process()
    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name=='nt' else 10)


class Service:
    def __init__(self,job,asynchronous=True):
        self.job={k:job.get(k) for k in ('campaign','training_days','validation_days','execution','workstation_share')}
        self.cache=Path(job['campaign'])/'observer-quality-v1'
        self.index={};self.view={};self.detail={};self.error=None;self.requested=None;self.delivered=None;self.detail_request=None;self.detail_delivered=None
        self.stop=threading.Event();self.asynchronous=asynchronous
        if asynchronous:
            self.pool=ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn'),initializer=initialize)
            self.thread=threading.Thread(target=self.run,name='quality-snapshots',daemon=True);self.thread.start()
        else:
            p=self.cache/'index.json'
            if p.exists():self.index=json.loads(p.read_bytes())
    def request(self,generation,role,group=None,cohort='all'):
        key=(generation,role,group,cohort);self.requested=key
        if not self.asynchronous:
            from .quality_worker import read_view
            self.view=read_view(str(self.cache),*key);self.delivered=key
        return self.view if self.delivered==key else {}
    def candidate(self,generation,role,serial):
        key=(generation,role,serial);self.detail_request=key
        if not self.asynchronous:
            from .quality_worker import read_detail
            self.detail=read_detail(str(self.cache),*key);self.detail_delivered=key
        return self.detail if self.detail_delivered==key else {}
    def run(self):
        from .quality_worker import refresh,read_view,read_detail
        last=0.;loaded=None;detail=None
        while not self.stop.is_set():
            try:
                if self.requested is not None and self.requested!=loaded:
                    key=self.requested;value=self.pool.submit(read_view,str(self.cache),*key).result()
                    self.view=value;self.delivered=loaded=key
                if self.detail_request is not None and self.detail_request!=detail:
                    key=self.detail_request;value=self.pool.submit(read_detail,str(self.cache),*key).result()
                    self.detail=value;self.detail_delivered=detail=key
                if time.monotonic()-last>5:
                    value=self.pool.submit(refresh,self.job,str(self.cache)).result()
                    if [(r['generation'],r['updated']) for r in value.get('history',[])]!=[(r['generation'],r['updated']) for r in self.index.get('history',[])]:loaded=None;detail=None
                    self.index=value;self.error=None;last=time.monotonic()
            except Exception as exc:self.error=str(exc);last=time.monotonic()
            self.stop.wait(.1)
    def close(self):
        self.stop.set()
        if self.asynchronous:
            self.thread.join();self.pool.shutdown(wait=True,cancel_futures=True)
