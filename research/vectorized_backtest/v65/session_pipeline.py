"""Bounded independent session scheduling and complete per-session telemetry."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event,RLock,BoundedSemaphore
from time import time,perf_counter


class SessionProgress:
    def __init__(self,panel,days,population,generation,role):
        self.panel=panel;self.lock=RLock();self.started=time()
        self.rows={day:dict(day=day,status='queued',stage='Queued',total_candidates=population,completed_candidates=0) for day in days}
        self.generation=generation;self.role=role;self.population=population

    def publish(self):
        if hasattr(self.panel,'publish'):
            self.panel.publish(session_paths={d:dict(r) for d,r in self.rows.items()},
                active_sessions=[d for d,r in self.rows.items() if r['status'] in ('running','preparing')],
                completed_sessions=sum(r['status']=='complete' for r in self.rows.values()),
                replay_generation=self.generation,replay_role=self.role,replay_population=self.population,replay_started_epoch=self.started)

    def begin(self,day):
        with self.lock:
            self.rows[day].update(status='running',stage='Load session',session_started_epoch=time());self.publish()

    def event(self,event):
        with self.lock:
            row=self.rows.get(event['day'])
            if row is not None and event.get('stage') in ('Prepare resident RAM','Resident RAM ready'):
                if row['status'] not in ('queued','preparing'):return
                row.update(event)
                row['status']='preparing' if event['stage']=='Prepare resident RAM' else 'queued'
                self.publish();return
            if row is None or row['status']!='running':return
            # Dispatch begins the operator's clock; worker cache/load events must
            # not reset it and hide time spent preparing the session.
            started=row['session_started_epoch']
            row.update(event);row['session_started_epoch']=started;self.publish()

    def finish(self,day,result):
        callback=getattr(self.panel,'session_result',None)
        if callback is not None:callback(day,result,self.role,self.generation)
        with self.lock:
            self.rows[day].update(status='complete',stage='Reused sealed replay' if result.get('restored') else 'Session complete',restored=result.get('restored',False),completed_candidates=self.population,
                completed_local_batches=len(result['timings']),completed_seconds=result['clocks'],total_seconds=result['clocks'],
                worker_seconds=result['wall_seconds'],session_seconds=time()-self.rows[day]['session_started_epoch'],finished_epoch=time());self.publish()

    def fail(self,day,error):
        with self.lock:
            self.rows[day].update(status='failed',stage='Failed',error=str(error),finished_epoch=time(),
                session_seconds=time()-self.rows[day]['session_started_epoch']);self.publish()


def independent_sessions(days,workers,submit,progress,*,choose=None):
    """Each lane advances immediately, preserving its day order and bounded work."""
    abort=Event()
    # Retain at most two completed result packets per replay lane while CPU
    # attribution/publication dispatch runs independently of the CUDA owner.
    capacity=BoundedSemaphore(2*workers);callbacks=[];callback_lock=RLock()
    completion=ThreadPoolExecutor(max_workers=min(4,workers),thread_name_prefix='v65-result-handoff')
    def finish(day,result):
        try:progress.finish(day,result)
        except BaseException as error:
            abort.set();progress.fail(day,error);raise
        finally:capacity.release()
    def lane_run(lane):
        results=[];assigned=days[lane::workers]
        try:
            remaining=list(assigned)
            for index in range(len(assigned)):
                if abort.is_set():break
                day=remaining[0]
                if choose:day=choose(lane,remaining)
                if day not in remaining:raise ValueError('Ready scheduler returned an unassigned session')
                remaining.remove(day)
                progress.begin(day)
                result=submit(lane,index,day,remaining[0] if remaining else None).result()
                capacity.acquire()
                try:future=completion.submit(finish,day,result)
                except BaseException:capacity.release();raise
                with callback_lock:callbacks.append(future)
                results.append(result)
            return results
        except BaseException as error:
            abort.set();progress.fail(day,error);raise
    try:
        with ThreadPoolExecutor(max_workers=workers) as dispatch:
            futures=[dispatch.submit(lane_run,lane) for lane in range(workers)]
            results=[result for future in futures for result in future.result()]
        for future in callbacks:future.result()
    finally:completion.shutdown(wait=True,cancel_futures=False)
    by_day={r['day']:r for r in results}
    return [by_day[day] for day in days]


def finish_metrics(metrics,item,selection,keys):
    """CPU-only attribution; the next GPU replay may proceed independently."""
    from .financial import ticker_totals
    from .ticker_selection import ticker_scores
    started=perf_counter();metrics['ticker_pnl']=ticker_totals(metrics,item);metrics['ticker_score']=ticker_scores(metrics,item)
    return {keys[i]:{k:v[j] for k,v in metrics.items()} for j,i in enumerate(selection)},perf_counter()-started
