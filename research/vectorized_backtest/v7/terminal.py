"""V6 terminal layout with V7's measured, durable progress authority."""
import copy
import math
import threading
import time
from collections import deque
from rich.console import Console
from .fixed_terminal import FixedTerminal
from rich.table import Column
from rich.text import Text
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn
from research.vectorized_backtest.v6.torch_backtest.staged_dashboard import render as v6_render, duration
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json


def render(status, *, width=110, height=38, now=None, view='financial'):
    now=time.time() if now is None else now
    extra=max(0,2*len(status.get('session_paths',{}))-2)+1
    layout=v6_render(status,width=width,height=height-extra if height>=30 else height,now=now,view=view)
    config=status.get('config',{});state=status.get('status','starting')
    end=status.get('finished_epoch',now);age=max(0,end-status.get('updated_epoch',end))
    layout['header'].update(Text(f"V7 {state.upper():<11} | {status.get('stage','Preparing'):<38.38} | updated {age:>4.0f}s ago\n"
        f"Population {str(config.get('population','—')):>6} | {status.get('focus',''):<23.23} | validation SEALED",
        style='red' if state=='failed' else 'yellow' if age>30 else 'cyan'))
    bars=Progress(TextColumn('{task.description}',table_column=Column(width=20,no_wrap=True)),
        BarColumn(bar_width=None),TaskProgressColumn(table_column=Column(width=4)),
        TextColumn('{task.fields[units]}',table_column=Column(width=32,no_wrap=True)),expand=True)
    cursor=status.get('progress',{})
    tasks=[('Generations',status.get('completed_generations',0),config.get('generations',0)),
           ('Generation sessions',status.get('completed_sessions',0),config.get('training_sessions',0)),
           ('Candidate batches',status.get('completed_batches',0),status.get('total_batches',0)),
           ('Backtest timestamps',cursor.get('completed_seconds',0),cursor.get('total_seconds',0)),
           ('Rule tiles queued',status.get('rule_tiles',0),status.get('total_rule_tiles',0))]
    lanes=status.get('session_paths',{})
    if lanes:
        tasks=tasks[:3]
        for day,lane in lanes.items():
            tasks.extend([(f'{day} rules',lane.get('rule_tiles',0),lane.get('total_rule_tiles',0)),
                          (f'{day} backtest',lane.get('completed_seconds',0),lane.get('total_seconds',0))])
    for label,done,total in tasks:
        if total or lanes:
            units=f'{done:6,.0f}/{total:6,.0f}' if total else 'waiting'.ljust(13)
            if lanes and label.endswith('backtest'):
                lane=lanes[label[:-len(' backtest')]]
                units+=f" ETA {duration(lane.get('replay_eta')):>9}"
            bars.add_task(label,total=total or None,completed=done,units=units)
    layout['progress'].size=max(5,len(tasks))
    layout['progress'].update(bars)
    leaders_panel=layout['leaders'].renderable
    tail_count=max(1,math.ceil(config.get('training_sessions',30)*.2))
    names={'Win %':'Position\nwin %','Ex-best $':'P&L excluding\nbest day $',
           'Tail day $':f'Worst {tail_count} days\navg P&L $','Days +%':'Profitable\ndays %'}
    leaders_panel.renderable.padding=(0,0)
    for column in leaders_panel.renderable.columns:
        original=str(column.header);column.header=names.get(original,original)
        column.width=max(max(map(len,str(column.header).splitlines())),3 if original=='Rank' else 9)
        column.min_width=column.width
    if not status.get('top_strategies'):leaders_panel.title='Rankings | awaiting completed generation'
    grid=layout['metrics'].renderable.renderable
    grid.box=None;grid.show_edge=False
    layout['timing'].update(Text(f"Elapsed {duration(end-status.get('started_epoch',end)):>9} | replay ETA {duration(status.get('replay_eta')):>9} | generation ETA {duration(status.get('generation_eta')):>9}\n"
        f"Campaign ETA {duration(status.get('campaign_eta')):>9} | avg batch {duration(status.get('average_batch_seconds')):>9} | estimated"))
    # Keep the same V6 regions and controls within compact terminal bounds.
    if height<30:
        layout['leaders'].size=6;layout['metrics'].size=max(1,height-19-max(0,len(tasks)-5))
    else:layout['leaders'].size+=1
    return layout


class CampaignProgress:
    """One-second publication thread; compute callbacks never perform disk I/O."""
    def __init__(self,output,contract,console=None,clock=time.time):
        self.output=output;self.clock=clock;self.lock=threading.RLock();self.stop=threading.Event()
        self.error=None;self.thread=None;self.live=None;self.events=deque();self.samples=deque(maxlen=30)
        self.generation_samples=[];self.batch_started=None;self.replay_started=None
        self.console=console or Console();now=clock()
        self.state=dict(status='running',stage='Verify campaign',started_epoch=now,updated_epoch=now,
            config=dict(population=contract['population'],generations=contract['generations'],training_sessions=len(contract['input_receipts'])),
            completed_generations=0,completed_sessions=0,completed_batches=0,
            total_batches=len(contract['input_receipts'])*math.ceil(contract['population']/contract['batch_size']),
            validation_opened=False,validation_status='SEALED',top_strategies=[],messages=[],progress={},
            evaluation_basis='No completed all30 ranking yet',focus='',batch_size=contract['batch_size'],
            session_workers=contract['session_workers'],execution=contract['execution'])
        if contract.get('session_execution')=='independent':
            self.state['session_paths']={f'Session {i+1}':{} for i in range(contract['session_workers'])}

    def update(self,**values):
        if self.error is not None:raise RuntimeError('Terminal status publication failed') from self.error
        with self.lock:
            if values.get('stage')!=self.state.get('stage') and 'stage' in values:
                message=dict(timestamp=time.strftime('%H:%M:%S',time.localtime(self.clock())),text=values['stage'])
                self.events.append(message);self.state['messages']=(self.state['messages']+[message])[-3:]
            self.state.update(values)

    def begin_generation(self,generation):
        self.generation_started=self.clock()
        self.batch_started=None;self.replay_started=None
        self.update(generation=generation,completed_generations=generation-1,completed_sessions=0,completed_batches=0,
                    stage='Prepare session cohort',progress={},rule_tiles=0,total_rule_tiles=0)

    def begin_batch(self,cohort,offset,clocks):
        self.batch_started=self.clock();self.replay_started=None
        self.update(stage='Evaluate causal rules',focus=', '.join(cohort),sessions=cohort,candidate_offset=offset,
            progress=dict(completed_seconds=0,total_seconds=clocks),rule_tiles=0,total_rule_tiles=0)

    def begin_replay(self):
        self.replay_started=self.clock();self.update(stage='Compile / replay positions')

    def replay(self,done,total):
        self.update(stage='Replay positions',progress=dict(completed_seconds=done,total_seconds=total))

    def rules(self,event):
        self.update(rule_tiles=event['tiles'],total_rule_tiles=event['total_tiles'])

    def begin_sessions(self,cohort):
        with self.lock:
            self.batch_started=self.clock();self.cohort_base=self.state['completed_batches']
            self.update(stage='Concurrent session rules / backtests',focus=', '.join(cohort),
                session_paths={day:dict(stage='Queued',completed_local_batches=0,candidate_offset=0,
                    rule_tiles=0,total_rule_tiles=0,completed_seconds=0,total_seconds=0) for day in cohort})

    def session_progress(self,event):
        with self.lock:
            lane=self.state.get('session_paths',{}).get(event['day'])
            if lane is None:return
            if lane.get('stage')=='Session complete':return
            if event.get('candidate_offset',lane.get('candidate_offset',0))<lane.get('candidate_offset',0):return
            lane.update(event)
            self.state['completed_batches']=self.cohort_base+sum(v.get('completed_local_batches',0) for v in self.state['session_paths'].values())

    def complete_sessions(self,count,seconds):
        with self.lock:
            batches=count*math.ceil(self.state['config']['population']/self.state['batch_size'])
            self.samples.append(seconds/batches)
            self.update(completed_sessions=self.state['completed_sessions']+count,
                        completed_batches=self.cohort_base+batches)
            self.batch_started=None

    def complete_batch(self,sessions,seconds):
        with self.lock:
            self.samples.append(seconds/sessions)
            self.update(completed_batches=self.state['completed_batches']+sessions,stage='Publish completed batch')
            self.batch_started=None;self.replay_started=None

    def snapshot(self):
        with self.lock:
            s=copy.deepcopy(self.state);now=self.clock();s['updated_epoch']=now
            mean=sum(self.samples)/len(self.samples) if self.samples else None
            active=max(0,now-self.batch_started) if self.batch_started is not None else 0
            remain=s['total_batches']-s['completed_batches']
            eta=max(1.,mean*remain-active) if mean is not None and remain else None
            s['average_batch_seconds']=mean;s['generation_eta']=eta
            future=s['config']['generations']-s.get('generation',1)
            per_generation=sum(self.generation_samples)/len(self.generation_samples) if self.generation_samples else (mean*s['total_batches'] if mean is not None else None)
            s['campaign_eta']=eta+max(0,future)*per_generation if eta is not None and per_generation is not None else None
            progress=s['progress'];done=progress.get('completed_seconds',0);total=progress.get('total_seconds',0)
            s['replay_eta']=(now-self.replay_started)*(total-done)/done if self.replay_started is not None and done else None
            for lane in s.get('session_paths',{}).values():
                done=lane.get('completed_seconds',0);total=lane.get('total_seconds',0)
                start=lane.get('replay_started')
                lane['replay_eta']=(now-start)*(total-done)/done if start is not None and done else None
            if s.get('session_paths'):
                estimates=[v['replay_eta'] for v in s['session_paths'].values() if v['replay_eta'] is not None]
                s['replay_eta']=max(estimates) if estimates else None
            if s['status']=='complete':s['generation_eta']=s['campaign_eta']=s['replay_eta']=0.
            elif s['status'] in ('failed','interrupted'):s['generation_eta']=s['campaign_eta']=s['replay_eta']=None
            return s

    def publish(self):
        snapshot=self.snapshot();write_json(self.output/'status.json',snapshot)
        if self.live is not None:self.live.update(render(snapshot,width=self.console.width,height=self.console.height))
        with self.lock:events=list(self.events);self.events.clear()
        if events:
            import json
            with (self.output/'events.jsonl').open('a',encoding='utf-8') as stream:
                for event in events:stream.write(json.dumps(event)+'\n')
            if not self.console.is_terminal:
                self.console.print(f"V7 {snapshot['status']} | {snapshot['stage']} | generations {snapshot['completed_generations']}/{snapshot['config']['generations']} | sessions {snapshot['completed_sessions']}/{snapshot['config']['training_sessions']} | batches {snapshot['completed_batches']}/{snapshot['total_batches']} | campaign ETA {duration(snapshot['campaign_eta'])}")

    def pump(self):
        try:
            while not self.stop.wait(1.):self.publish()
        except BaseException as error:self.error=error

    def __enter__(self):
        self.publish()
        if self.console.is_terminal:
            self.live=FixedTerminal(self.console);self.live.__enter__()
            self.live.update(render(self.snapshot(),width=self.console.width,height=self.console.height))
        self.thread=threading.Thread(target=self.pump,name='v7-terminal',daemon=True);self.thread.start();return self

    def __exit__(self,kind,error,traceback):
        self.stop.set();self.thread.join()
        if error is not None:
            self.state.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',stage=str(error),error=str(error),finished_epoch=self.clock())
        self.publish()
        if self.live is not None:self.live.__exit__(kind,error,traceback)
        if self.error is not None and error is None:raise RuntimeError('Terminal status publication failed') from self.error
