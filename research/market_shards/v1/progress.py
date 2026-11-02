"""Independent progress observer; never queries ClickHouse or market files."""
import argparse
import json
import threading
import time
from pathlib import Path
from .contract import write_json


class Progress:
    def __init__(self, root, workers):
        self.root = root; self.lock = threading.RLock(); self.started = time.time()
        self.state = dict(status='running', stage='source preflight', started=self.started,
            completed=0, reused=0, failed=0, blocked=0, queued=0, total=0,
            workers={str(i): dict(state='idle', stage='waiting', rows=0, bytes=0) for i in range(workers)}, errors=[])
        self.stop = threading.Event()
        self.weights={};self.weight_done=0.;self.weight_reused=0.;self.weight_total=0.
        self.thread = threading.Thread(target=self._publish, name='shard-status', daemon=True)
        self.thread.start()

    def _publish(self):
        while not self.stop.is_set():
            self.flush()
            self.stop.wait(1)

    def flush(self):
        with self.lock:
            now = time.time(); value = dict(self.state, heartbeat=now, elapsed=now-self.started)
            done = value['completed']+value['reused']+value['failed']
            measured = self.state.get('measured', 0)
            active = sum(w['state']=='active' for w in value['workers'].values())
            value['active'] = active; value['queued'] = max(0,value['total']-done-active)
            elapsed = now-self.state.get('phase_started', now)
            # Reused files don't inflate the throughput estimate.
            completed_work=self.weight_done-self.weight_reused
            remaining=max(0.,self.weight_total-self.weight_done)
            ready=measured>=3 and completed_work>=.05*max(1.,self.weight_total-self.weight_reused)
            value['phase_eta_seconds'] = remaining*elapsed/completed_work if ready else (0. if remaining==0 and done==value['total'] else None)
            value['eta_scope'] = 'current phase; next phase unmeasured' if value['stage']=='scanner' else 'remaining extraction'
            write_json(self.root/'status.json', value)

    def phase(self, name, total, blocked=0):
        with self.lock:
            self.weights={};self.weight_done=0.;self.weight_reused=0.;self.weight_total=float(total)
            if name=='extract':
                plan=json.loads((self.root/'extraction-plan.json').read_text())
                # Context-only daily files must not count as full execution days
                # when estimating throughput. Units approximate projected rows.
                self.weights={day:max(1.,len(s['decision'])*(19800*(1+.1+1/60+2)+57600)+len(s['hourly'])*16+len(s['daily'])) for day,s in plan['scopes'].items()}
                self.weight_total=sum(self.weights.values())
            self.state.update(stage=name, total=total, completed=0, reused=0, failed=0,
                blocked=blocked, phase_started=time.time(), measured=0)

    def worker(self, slot, **values):
        with self.lock:
            self.state['workers'][str(slot)].update(values)

    def finish(self, slot, reused=False, error=None):
        with self.lock:
            key = 'failed' if error else 'reused' if reused else 'completed'
            self.state[key] += 1
            weight=self.weights.get(self.state['workers'][str(slot)].get('day'),1.)
            self.weight_done+=weight
            if reused or error:self.weight_reused+=weight
            if not error and not reused:
                self.state['measured'] += 1
            self.state['workers'][str(slot)]['state'] = 'failed' if error else 'idle'
            if error:
                self.state['errors'].append(str(error))

    def close(self, status):
        self.stop.set(); self.thread.join()
        self.state['status'] = status
        self.flush()


def duration(seconds):
    if seconds is None:
        return 'measuring'
    seconds = max(0,int(seconds))
    return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'


def render(state, width=120):
    from rich.table import Table
    from rich.console import Group
    from rich.text import Text
    now = time.time(); stale = now-state['heartbeat']>10 and state['status']=='running'
    title = Text(f"ARTE SHARDS  {state['status'].upper()}  |  {state['stage']}" + ('  STALE HEARTBEAT' if stale else ''), style='bold red' if stale else 'bold cyan')
    summary = Text(f"Done {state['completed']+state['reused']}/{state['total']}  Active {state.get('active',0)}  Queued {state.get('queued',0)}  Reused {state['reused']}  Blocked {state['blocked']}  Failed {state['failed']}")
    timing = Text(f"Elapsed {duration(state['elapsed'])}  ETA {duration(state.get('phase_eta_seconds'))} ({state.get('eta_scope','')})")
    table = Table(expand=True, box=None, pad_edge=False)
    for name in ('Worker','Session','State','Stage','Rows','MiB','Elapsed','ETA'):
        table.add_column(name, justify='right' if name in ('Rows','MiB','Elapsed') else 'left', no_wrap=True)
    for slot, w in sorted(state['workers'].items(),key=lambda item:int(item[0])):
        table.add_row(slot, w.get('day','-'), w['state'], w['stage'], f"{w.get('rows',0):,}", f"{w.get('bytes',0)/2**20:.1f}", duration(now-w['started']) if w.get('state')=='active' and 'started' in w else '-', duration(w.get('eta_seconds')) if w['state']=='active' else '-')
    errors = [Text(e,style='red') for e in state.get('errors',[])[-3:]]
    return Group(title,summary,timing,table,*errors)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true')
    a=p.parse_args(argv)
    from rich.console import Console
    from rich.live import Live
    console=Console()
    def read():
        return json.loads((a.output/'status.json').read_text())
    if a.once or not console.is_terminal:
        console.print(render(read(),console.width));return
    with Live(console=console,refresh_per_second=1) as live:
        while True:
            try:
                state=read();live.update(render(state,console.width))
            except (FileNotFoundError,json.JSONDecodeError):
                live.update('Waiting for first durable status…');time.sleep(1);continue
            if state['status'] != 'running':break
            time.sleep(1)


if __name__=='__main__':main()
