"""V7-style terminal with separate training and display-only validation rankings."""
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


def ranking_page_size(height, status=None):
    lanes=len((status or {}).get('session_paths',{}))
    progress=max(5,3+2*lanes) if lanes else 5
    detail=10 if height>=36 else 5
    messages=5 if height>=30 else 3
    return max(1,min(50,height-(2+progress+6+detail+2+messages+1)))


def render(status, *, width=110, height=38, now=None, view='financial'):
    from research.vectorized_backtest.v7.terminal import render as baseline
    from rich.table import Table
    from rich.panel import Panel
    validation=view=='validation'
    session_view=status.get('_display_sort') in ('session_score','session_profit')
    metric_width=width-40 if session_view else width
    visible=dict(status)
    visible['config']={**status.get('config',{}),'training_sessions':status.get('evaluation_sessions',status.get('config',{}).get('training_sessions',0))}
    if validation:visible['top_strategies']=status.get('validation_ranking',[])
    if '_display_rows' in status:visible['top_strategies']=status['_display_rows']
    layout=baseline(visible,width=width,height=height,now=now,view='financial' if validation else view)
    layout['header'].update(Text(
        f"V27 {status.get('status','starting').upper()} | Validation display only: NO selection feedback\n"
        f"Generation {status.get('generation',0)} | {status.get('evaluation_split','training')} | "
        f"{status.get('stage','Preparing')} | {status.get('validation_status','pending')}",style='cyan'))
    table=Table(expand=True,padding=(0,0),show_edge=False)
    columns=[('Validation\nnet profit\nrank',10),('Candidate ID',29),('Training\nnet profit\nrank',10),('Training\nnet profit $',12),('Training\nscore rank',10)] if validation else [('Training\nnet profit\nrank',10),('Candidate ID',29)]
    if session_view:columns=[('Session',10),('Session\nnet profit\nrank',10),('Session\nscore $',10),('Session\nprofit $',10)]+columns
    columns += [('Global\nscore $',10),('Net profit\n$',10),('Robust base\nprofit $',10)]
    if metric_width>=125:columns += [('Concentration\ndeduction $',11),('Profitable\nsessions %',10),('Active\nsessions',8),('Complexity\nnodes',10)]
    if metric_width>=175:columns += [('Mean session\nprofit $',13),('Completed\npositions',10),('Profitable\npositions %',10)]
    if metric_width>=215:columns += [('Zero-trade\nsessions',10),('Best session\nprofit $',12),('Best ticker\nprofit $',12)]
    if metric_width>=265:columns += [('Turnover\n$',12),('Worst position\nprofit $',14),('Profitable active\nsessions %',17)]
    for label,w in columns:
        if label=='Candidate ID':w=min(w,max(16,width-68))
        table.add_column(label,justify='left' if label=='Candidate ID' else 'right',no_wrap=True,width=w,min_width=w)
    rows=visible.get('top_strategies',[]);page=max(0,int(status.get('_rank_page',0)))
    page_size=ranking_page_size(height,status);page=min(page,max(0,(len(rows)-1)//page_size));begin=page*page_size
    for row in rows[begin:begin+page_size]:
        m=row['metrics'];values=[str(row['rank']),row.get('candidate_id',str(row['candidate']))]
        if validation:values.extend((str(row.get('training_rank','—')),f"{row['training_net_profit']:,.1f}" if 'training_net_profit' in row else '—',str(row.get('training_score_rank','—'))))
        if session_view:values=[row.get('session','—'),str(row.get('session_rank','—')),f"{row.get('session_score',0):,.1f}",f"{row.get('session_profit',0):,.1f}"]+values
        values += [f"{row['score']:,.1f}",f"{m.get('total_pnl',0):,.1f}",f"{m.get('trimmed_profit',0):,.1f}"]
        if metric_width>=125:values += [f"{m.get('concentration_deduction',0):,.2f}",f"{100*m.get('profitable_day_fraction',0):.1f}",str(int(m.get('active_sessions',0))),str(int(m.get('complexity',0)))]
        if metric_width>=175:values += [f"{m.get('mean_session_profit',0):,.2f}",str(int(m.get('closed_positions',0))),
            '—' if m.get('position_win_rate') is None else f"{100*m['position_win_rate']:.1f}"]
        if metric_width>=215:values += [str(int(m.get('zero_trade_sessions',0))),f"{m.get('best_day_pnl',0):,.2f}",f"{m.get('best_ticker_pnl',0):,.2f}"]
        if metric_width>=265:values += [f"{m.get('turnover',0):,.2f}",f"{m.get('worst_position_pnl',0):,.2f}",f"{100*m.get('profitable_active_fraction',0):.1f}"]
        table.add_row(*values)
    label=f"Validation ranking — generation {status.get('validation_generation','pending')} — display only" if validation else f"Training ranking — generation {status.get('_display_generation',status.get('training_ranking_generation','pending'))}"
    label+=' | sort: '+{'session_score':'session / score','net_profit':'net profit','session_profit':'session profit','training_rank':'training net rank'}.get(status.get('_display_sort'),'net profit')
    if status.get('display_error'):label+=' | STALE: '+status['display_error']
    if not rows:label+=' | awaiting complete panel'
    else:label+=f' | rows {begin+1}–{min(len(rows),begin+page_size)} of {len(rows)}'
    layout['leaders'].update(Panel(table,title=label))
    layout['leaders'].size=len(rows[begin:begin+page_size])+6
    if rows:
        selected=rows[min(len(rows)-1,max(0,int(status.get('_rank',1))-1))]
        m=selected['metrics'];details=Table.grid(expand=True,padding=(0,2))
        details.add_column();details.add_column()
        if view=='objective':
            components=m.get('objective_components',{})
            counts=status.get('rule_quality_rejections',{})
            if height>=36:
                for name in ('net_profit','concentration_penalty','drawdown_penalty'):
                    if name in components:details.add_row(name.replace('_',' '),f'{components[name]:,.2f}')
                details.add_row('Session / ticker trim amounts',f"{m.get('global_session_trim_removed',0):,.2f} / {m.get('ticker_trim_removed',0):,.2f}")
                details.add_row('Selection','Score only; accounting and hard risk checks')
                details.add_row('Rejected rules / ineffective edits',f"{sum(v for k,v in counts.items() if k!='ineffective_edit'):,} / {counts.get('ineffective_edit',0):,}")
            else:
                details.add_row('Net profit / concentration deduction',f"{m.get('total_pnl',0):,.1f} / {components.get('concentration_penalty',0):,.1f}")
                details.add_row('Selection','Score only; no performance gate')
                details.add_row('Rejected rules / ineffective edits',f"{sum(v for k,v in counts.items() if k!='ineffective_edit'):,} / {counts.get('ineffective_edit',0):,}")
        else:
            fields=[('Active / zero-trade sessions',f"{int(m.get('active_sessions',0))} / {int(m.get('zero_trade_sessions',0))}"),
                ('Profitable active sessions',f"{100*m.get('profitable_active_fraction',0):.1f}%"),
                ('Best session / best listing P&L',f"{m.get('best_day_pnl',0):,.2f} / {m.get('best_ticker_pnl',0):,.2f}"),
                ('Turnover / worst position',f"{m.get('turnover',0):,.2f} / {m.get('worst_position_pnl',0):,.2f}"),
                ('Complexity nodes',str(int(m.get('complexity',0)))),
                ('Ticker / session trim amounts $',f"{m.get('ticker_trim_removed',0):,.2f} / {m.get('global_session_trim_removed',0):,.2f}"),
                ('Largest-session share of positive profit',f"{100*m.get('best_day_gross_positive_share',0):.1f}%")]
            if validation:fields.append(('Sessions with exact cached results',str(int(m.get('reused_sessions',0)))))
            for name,value in (fields if height>=36 else fields[:3]):details.add_row(name,value)
        layout['metrics'].update(Panel(details,title=selected.get('candidate_id','Candidate diagnostics')))
        layout['metrics'].size=10 if height>=36 else 5
    layout['keys'].update(Text('F train | V validation | 1 session/score | 2 net profit | 3 session profit | 4 training rank | N/B pages | O objective | Q exit'))
    return layout


class CampaignProgress:
    """One-second publication thread; compute callbacks never perform disk I/O."""
    def __init__(self,output,contract,console=None,clock=time.time):
        self.output=output;self.clock=clock;self.lock=threading.RLock();self.publish_lock=threading.Lock();self.stop=threading.Event()
        self.error=None;self.thread=None;self.live=None;self.events=deque();self.samples=deque(maxlen=30)
        self.generation_samples=[];self.batch_started=None;self.replay_started=None
        self.console=console or Console();now=clock()
        self.state=dict(status='running',stage='Verify campaign',started_epoch=now,updated_epoch=now,
            config=dict(population=contract['population'],generations=contract['generations'],training_sessions=len(contract['input_receipts'])),
            completed_generations=0,completed_sessions=0,completed_batches=0,
            total_batches=len(contract['input_receipts'])*math.ceil(contract['population']/contract['batch_size']),
            validation_feedback=False,validation_opened=False,validation_ranking=[],validation_status='Pending display-only evaluation',top_strategies=[],messages=[],progress={},
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

    def begin_evaluation(self,role,sessions,population):
        self.update(evaluation_split=role,evaluation_sessions=sessions,evaluation_population=population,
            total_batches=sessions*math.ceil(population/self.state['batch_size']),completed_sessions=0,completed_batches=0)

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
            batches=count*math.ceil(self.state.get('evaluation_population',self.state['config']['population'])/self.state['batch_size'])
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
        with self.publish_lock:self._publish()

    def _publish(self):
        snapshot=self.snapshot();write_json(self.output/'status.json',snapshot)
        if self.live is not None:self.live.update(lambda width,height:render(snapshot,width=width,height=height))
        with self.lock:events=list(self.events);self.events.clear()
        if events:
            import json
            with (self.output/'events.jsonl').open('a',encoding='utf-8') as stream:
                for event in events:stream.write(json.dumps(event)+'\n')
            if not self.console.is_terminal:
                self.console.print(f"V27 {snapshot['status']} | {snapshot['stage']} | generations {snapshot['completed_generations']}/{snapshot['config']['generations']} | sessions {snapshot['completed_sessions']}/{snapshot['config']['training_sessions']} | batches {snapshot['completed_batches']}/{snapshot['total_batches']} | campaign ETA {duration(snapshot['campaign_eta'])}")

    def pump(self):
        try:
            while not self.stop.wait(1.):self.publish()
        except BaseException as error:self.error=error

    def __enter__(self):
        self.publish()
        if self.console.is_terminal:
            self.live=FixedTerminal(self.console);self.live.__enter__()
            self.live.update(lambda width,height:render(self.snapshot(),width=width,height=height))
        self.thread=threading.Thread(target=self.pump,name='v18-terminal',daemon=True);self.thread.start();return self

    def __exit__(self,kind,error,traceback):
        self.stop.set();self.thread.join()
        if error is not None:
            self.state.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',stage=str(error),error=str(error),finished_epoch=self.clock())
        self.publish()
        if self.live is not None:self.live.__exit__(kind,error,traceback)
        if self.error is not None and error is None:raise RuntimeError('Terminal status publication failed') from self.error
