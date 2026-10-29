"""Bounded independent session scheduling and complete per-session telemetry."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event,RLock
from time import time,perf_counter


class SessionProgress:
    def __init__(self,panel,days,population,generation,role):
        self.panel=panel;self.lock=RLock();self.started=time()
        self.rows={day:dict(day=day,status='queued',stage='Queued',total_candidates=population,completed_candidates=0) for day in days}
        self.generation=generation;self.role=role;self.population=population

    def publish(self):
        if hasattr(self.panel,'publish'):
            self.panel.publish(session_paths={d:dict(r) for d,r in self.rows.items()},
                active_sessions=[d for d,r in self.rows.items() if r['status']=='running'],
                completed_sessions=sum(r['status']=='complete' for r in self.rows.values()),
                replay_generation=self.generation,replay_role=self.role,replay_population=self.population,replay_started_epoch=self.started)

    def begin(self,day):
        with self.lock:
            self.rows[day].update(status='running',stage='Load session',session_started_epoch=time());self.publish()

    def event(self,event):
        with self.lock:
            row=self.rows.get(event['day'])
            if row is None or row['status']!='running':return
            row.update(event);self.publish()

    def finish(self,day,result):
        with self.lock:
            self.rows[day].update(status='complete',stage='Session complete',completed_candidates=self.population,
                completed_local_batches=len(result['timings']),completed_seconds=result['clocks'],total_seconds=result['clocks'],
                session_seconds=result['wall_seconds'],finished_epoch=time());self.publish()


def independent_sessions(days,workers,submit,progress):
    """Each lane advances immediately, preserving its day order and bounded work."""
    abort=Event()
    def lane_run(lane):
        results=[];assigned=days[lane::workers]
        try:
            for index,day in enumerate(assigned):
                if abort.is_set():break
                progress.begin(day)
                result=submit(lane,index,day,assigned[index+1] if index+1<len(assigned) else None).result()
                progress.finish(day,result);results.append(result)
            return results
        except BaseException:abort.set();raise
    with ThreadPoolExecutor(max_workers=workers) as dispatch:
        futures=[dispatch.submit(lane_run,lane) for lane in range(workers)]
        results=[result for future in futures for result in future.result()]
    by_day={r['day']:r for r in results}
    return [by_day[day] for day in days]


def finish_metrics(metrics,item,selection,keys):
    """CPU-only attribution; the next GPU replay may proceed independently."""
    from .financial import ticker_totals
    from .ticker_selection import ticker_scores
    started=perf_counter();metrics['ticker_pnl']=ticker_totals(metrics,item);metrics['ticker_score']=ticker_scores(metrics,item)
    return {keys[i]:{k:v[j] for k,v in metrics.items()} for j,i in enumerate(selection)},perf_counter()-started
