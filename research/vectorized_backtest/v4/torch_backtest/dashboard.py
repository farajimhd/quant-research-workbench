"""Single-owner operational view. Completed metrics never borrow active values."""
import time
from rich.console import Group
from rich.table import Table,Column
from rich.text import Text
from rich.layout import Layout
from rich.panel import Panel
from rich.progress import Progress,BarColumn,TextColumn,TaskProgressColumn

def number(v,fmt=',.2f'):
    return '—' if v is None else format(v,fmt)

def duration(value):
    if value is None:return '—'
    seconds=max(0,int(value));hours,seconds=divmod(seconds,3600);minutes,seconds=divmod(seconds,60)
    return f'{hours:d}:{minutes:02d}:{seconds:02d}'

def render(status,*,width=110,height=38,now=None,view='financial'):
    now=time.time() if now is None else now
    age=max(0,now-status.get('updated_epoch',now));state=status.get('status','starting')
    severity='red' if state in ('failed','interrupted') else 'yellow' if age>30 else 'cyan'
    head=Text(f"V4  {state.upper()}  |  {status.get('stage','Preflight')}  |  updated {age:.0f}s ago",style='bold '+severity)
    config=status.get('config',{});cursor=status.get('progress',{})
    profiling=status.get('mode')=='profile'
    progress=Progress(TextColumn('{task.description}',table_column=Column(min_width=15,no_wrap=True)),BarColumn(bar_width=None),TaskProgressColumn(),TextColumn('{task.completed:,.0f}/{task.total:,.0f}',table_column=Column(no_wrap=True)),expand=True)
    for label,done,total in (
        ('Generations',status.get('completed_generations',0),0 if profiling else config.get('generations',0)),
        ('Profile session' if profiling else 'Session',status.get('completed_sessions',0),1 if profiling else config.get('training_sessions',0)),
        ('Backtest s',cursor.get('completed_seconds',0),cursor.get('total_seconds',0)),
        ('Preparation',status.get('prepared_sessions',0),1 if profiling else config.get('training_sessions',0))):
        if total:progress.add_task(label,total=total,completed=done)
    if status.get('stage')=='Compile lifecycle rules' and status.get('total_tickers'):
        progress.add_task('Rule compile',total=status['total_tickers'],completed=status.get('completed_tickers',0))
    best=status.get('best_metrics') or status.get('closest_metrics') or {};timing=status.get('timing',{})
    title='COMPLETED TRAINING LEADER — not holdout evidence' if status.get('best_metrics') else 'CLOSEST COMPLETED CANDIDATE — INFEASIBLE' if status.get('closest_metrics') else 'COMPLETED TRAINING METRICS — no completed leader yet'
    if profiling:title='SESSION PROFILE — no generation selection or validation'
    grid=Table(title=title,expand=True,padding=(0,1))
    grid.add_column('Metric');grid.add_column('Value',justify='right')
    if width>=100:grid.add_column('Metric');grid.add_column('Value',justify='right')
    metrics=[('Objective',number(status.get('best_score') if status.get('best_score') is not None else status.get('closest_score'),'.6f')),('Feasible / population',f"{status.get('feasible_candidates','—')} / {config.get('population','—')}"),
        ('Net P&L $',number(best.get('total_pnl'))),('Median daily return %',number(None if best.get('median_return') is None else 100*best['median_return'])),
        ('P&L excluding best day $',number(best.get('other_days_pnl'))),('Best day P&L $',number(best.get('best_day_pnl'))),
        ('Profitable days %',number(None if best.get('profitable_day_fraction') is None else 100*best['profitable_day_fraction'])),('Worst-tail loss %',number(None if best.get('tail_loss') is None else 100*best['tail_loss'])),
        ('Worst drawdown $',number(best.get('worst_drawdown'))),('Worst day P&L $',number(best.get('worst_pnl'))),
        ('Acquisition batches',number(best.get('batches'),',.0f')),('Positions / fills',f"{number(best.get('positions'),',.0f')} / {number(best.get('fills'),',.0f')}"),
        ('Open positions',number(best.get('open'),',.0f')),('Weighted hold s',number(best.get('mean_hold_seconds'))),
        ('Stop-risk hours (sum)',number(best.get('stop_risk_hours'),'.4f')),('Capital hours (sum)',number(best.get('capital_hours'),'.4f')),
        ('Active nodes (all stages)',number(best.get('active_nodes'),',.0f')),
        ('Sharpe (daily)',number(best.get('sharpe_daily'),'.3f')),
        ('Sharpe (annualized estimate)',number(best.get('sharpe_annualized_estimate'),'.3f')),
        ('Sharpe ex-best (ann. estimate)',number(best.get('sharpe_ex_best_annualized_estimate'),'.3f')),
        ('Validation',status.get('validation_status','SEALED'))]
    metrics.extend([('Closed-position win rate %',number(None if best.get('position_win_rate') is None else 100*best['position_win_rate'])),
                    ('Profit factor',number(best.get('profit_factor'))),('Closed / winning / losing',f"{number(best.get('closed_positions'),',.0f')} / {number(best.get('winning_positions'),',.0f')} / {number(best.get('losing_positions'),',.0f')}" )])
    metrics.extend([('Closed hold min / max s',f"{number(best.get('closed_hold_min_seconds'))} / {number(best.get('closed_hold_max_seconds'))}"),
                    ('Closed hold mean s',number(best.get('closed_hold_mean_seconds'))),
                    ('Closed hold median / P90 s',f"{number(best.get('closed_hold_median_seconds'))} / {number(best.get('closed_hold_p90_seconds'))}")])
    if height<26:metrics=metrics[:7]+[('Win rate %',number(None if best.get('position_win_rate') is None else 100*best['position_win_rate'])),('Closed hold mean / P90 s',f"{number(best.get('closed_hold_mean_seconds'))} / {number(best.get('closed_hold_p90_seconds'))}"),('Validation',status.get('validation_status','SEALED'))]
    if profiling:
        active=status.get('active_session') or {}
        metrics=[('Population',str(config.get('population','—'))),
                 ('Provisional P&L median $',number(active.get('pnl_median'))),
                 ('Provisional P&L minimum $',number(active.get('pnl_min'))),
                 ('Provisional P&L maximum $',number(active.get('pnl_max'))),
                 ('Largest drawdown $',number(active.get('drawdown_max'))),
                 ('Most open positions',number(active.get('open_positions_max'),',.0f')),
                 ('Most fills',number(active.get('fills_max'),',.0f')),
                 ('Financial error candidates',number(active.get('financial_error_candidates'),',.0f')),
                 ('Ledger overflow candidates',number(active.get('overflow_candidates'),',.0f')),
                 ('Validation',status.get('validation_status','SEALED'))]
        trade_metrics=[('Pooled closed-position win %',number(None if active.get('position_win_rate') is None else 100*active['position_win_rate'])),
                       ('Pooled profit factor',number(active.get('profit_factor'))),
                       ('Closed / winning / losing',f"{active.get('closed_positions','—')} / {active.get('winning_positions','—')} / {active.get('losing_positions','—')}")]
        position_metrics=[('Closed hold min / max s',f"{number(active.get('closed_hold_min_seconds'))} / {number(active.get('closed_hold_max_seconds'))}"),
                          ('Closed hold mean s',number(active.get('closed_hold_mean_seconds'))),
                          ('Closed hold median / P90 s',f"{number(active.get('closed_hold_median_seconds'))} / {number(active.get('closed_hold_p90_seconds'))}"),
                          ('Share-weighted hold s',number(active.get('weighted_hold_seconds'))),
                          ('Open age mean / max s',f"{number(active.get('open_age_mean_seconds'))} / {number(active.get('open_age_max_seconds'))}")]
        if height<26:metrics=metrics[:5]+trade_metrics+[metrics[-2],metrics[-1]]
        else:metrics.extend(trade_metrics+position_metrics)
    if view=='positions':
        source=(status.get('active_session') or {}) if profiling else best
        active=status.get('active_session') or {}
        metrics=[('Closed hold '+label+' s',number(source.get('closed_hold_'+field+'_seconds'))) for label,field in (('minimum','min'),('mean','mean'),('median','median'),('P90','p90'),('maximum','max'))]
        metrics.extend([('Share-weighted hold s',number(source.get('weighted_hold_seconds') if profiling else source.get('mean_hold_seconds'))),
                        ('Live open age mean s',number(active.get('open_age_mean_seconds'))),
                        ('Live open age maximum s',number(active.get('open_age_max_seconds'))),
                        ('Timing basis','First fill → final fill; real seconds')])
        grid.title='POSITION TIMING · actual elapsed seconds'
    if height>=30:
        import math
        capacity=max(1,height-26)*(2 if width>=100 else 1)
        pages=max(1,math.ceil(len(metrics)/capacity));page=status.get('_financial_page',0)%pages
        metrics=metrics[page*capacity:(page+1)*capacity]
        if pages>1:grid.title=f'{title} | F: page {page+1}/{pages}'
    if width>=100:
        for i in range(0,len(metrics),2):
            other=metrics[i+1] if i+1<len(metrics) else ('','');grid.add_row(*metrics[i],*other)
    else:
        for row in metrics:grid.add_row(*row)
    performance=Table(title='PERFORMANCE / OWNERSHIP',expand=True,padding=(0,1));performance.add_column('Stage');performance.add_column('Last s',justify='right');performance.add_column('Average s',justify='right')
    for key in ('load','transfer','rule_prepare','compile','replay','end_to_end'):
        performance.add_row(key.replace('_',' ').title(),number(timing.get(key)),number(status.get('average_timing',{}).get(key)))
    terminal=state in ('completed','failed','interrupted','no_feasible_winner','profile_complete','awaiting_validation_inputs')
    elapsed_end=status.get('updated_epoch',now) if terminal else now
    clock=Text(f"Elapsed {duration(elapsed_end-status['started_epoch']) if status.get('started_epoch') else '—'} | replay ETA {duration(status.get('replay_eta'))} | campaign ETA {duration(status.get('campaign_eta'))}")
    average=status.get('average_timing',{}).get('end_to_end')
    clock.append(f" | avg session {duration(average)} ({status.get('timed_sessions',0)} measured)")
    footer=Text(f"GPU {number(status.get('gpu_gib'),'.1f')} GiB | host {number(status.get('host_gib'),'.1f')} GiB | prefetched {status.get('prefetched_sessions','—')} | worker {status.get('worker_pid','—')}")
    active=status.get('active_session')
    if active:
        live=Text(f"LIVE session median P&L ${number(active.get('pnl_median'))} | DD ${number(active.get('drawdown_max'))} | open {active.get('open_positions_max','—')} | fills {active.get('fills_max','—')}",style='yellow')
        if height<26 and view in ('financial','positions','objective'):footer=live
        else:footer=Group(Text('ACTIVE SESSION POPULATION — provisional marked equity',style='yellow'),live,footer)
    issue=Text(status.get('error') or status.get('waiting_reason') or 'No reported failure',style='red' if status.get('error') else 'dim')
    if active and (active.get('financial_error_candidates') or active.get('overflow_candidates')):
        issue=Text(f"FINANCIAL ERRORS {active.get('financial_error_candidates',0)} | LEDGER OVERFLOW {active.get('overflow_candidates',0)} candidates",style='bold red')
    events=status.get('messages',[])
    messages=Text('\n'.join(f"{item['timestamp']}  {item['text']}" for item in events[-4:]) or 'No recorded events yet')
    if height>=30:
        # Fixed region sizes keep metrics in place when phases and messages change.
        layout=Layout()
        layout.split_column(Layout(name='header',size=3),Layout(name='progress',size=6),
                            Layout(name='clock',size=2),Layout(name='metrics'),
                            Layout(name='ownership',size=2),Layout(name='messages',size=6),Layout(name='keys',size=1))
        layout['header'].update(Group(head,Text(status.get('focus','')),issue))
        layout['progress'].update(progress);layout['clock'].update(clock)
        layout['metrics'].update(performance if view=='performance' else components_table(best,status.get('objective'),profiling=profiling,wide=width>=100) if view=='objective' else
                                  Panel(Text('\n'.join(f"{item['timestamp']}  {item['text']}" for item in events[-max(1,height-24):])),title='Message history · older entries retained in events.jsonl') if view=='messages' else grid)
        layout['ownership'].update(Text(f"GPU {number(status.get('gpu_gib'),'.1f')} GiB | worker {status.get('worker_pid','—')} | provisional metrics until session completes"))
        layout['messages'].update(Panel(messages,title='MESSAGE CENTER · UTC · chronological · retained in events.jsonl'))
        layout['keys'].update(Text('F financial/pages | T position timing | P performance | C objective | M messages | Q close'))
        return layout
    if height<26 and view=='financial':return Group(head,progress,grid,clock,issue,Text('F financial | P performance | C objective; full metrics: status.json'))
    components=components_table(best,status.get('objective'),profiling=profiling,wide=width>=100)
    if view=='performance':return Group(head,progress,clock,performance,footer,issue,Text('F financial | P performance | C objective'))
    if view=='objective':return Group(head,progress,clock,components,footer,issue,Text('F financial | P performance | C objective'))
    if height<50:return Group(head,Text(status.get('focus','')),progress,clock,grid,footer,issue,Text('F financial | T timing | P performance | C objective | M messages | Q close'))
    return Group(head,Text(status.get('focus','')),progress,clock,grid,performance,components,footer,issue)

def components_table(best,objective=None,*,profiling=False,wide=True):
    objective=objective or {};values=best.get('objective_components') or {}
    table=Table(title='OBJECTIVE COMPONENTS',expand=True)
    table.add_column('Component');table.add_column('Weight',justify='right')
    if wide:table.add_column('Input / formula')
    table.add_column('Signed contribution',justify='right')
    rows=(('median_reward','Median reward','median_weight',1,'Median daily return'),
          ('ex_best_reward','Ex-best reward','ex_best_weight',1,'Mean daily return excluding best day'),
          ('tail_penalty','Tail loss','cvar_weight',-1,'Mean loss in worst configured tail'),
          ('drawdown_penalty','Drawdown','drawdown_weight',-1,'Mean daily drawdown / initial cash'),
          ('stop_risk_penalty','Stop-risk time','stop_risk_weight',-1,'Mean risk dollar-seconds / cash / 3600'),
          ('capital_time_penalty','Capital time','capital_time_weight',-1,'Mean capital dollar-seconds / cash / 3600'),
          ('complexity_penalty','Complexity','complexity_weight',-1,'Active nodes / configured maximum nodes'))
    for key,label,weight,sign,formula in rows:
        configured=objective.get(weight)
        cells=[label,number(None if configured is None else sign*configured,'.3f')]
        if wide:cells.append(formula)
        cells.append(number(None if key not in values else sign*values[key],'.6f'))
        table.add_row(*cells)
    total=sum(values[key]*sign for key,_,_,sign,_ in rows) if all(key in values for key,_,_,_,_ in rows) else None
    table.add_row('Total score','',*(['Sum of signed contributions'] if wide else []),number(total,'.6f'))
    table.caption=('Profile: full 30-day objective is not computed.' if profiling else
                   'Pending: all 30 training sessions must finish before scoring.' if not values else
                   'Calculated from a completed all-30-session training leader.')
    if objective:
        gates=f"Batches {objective.get('minimum_batches','?')}–{objective.get('maximum_batches','?')}/day; every session flat"
        if objective.get('require_positive_ex_best'):gates+='; ex-best mean > 0'
        table.caption+='\nFeasibility: '+gates
    return table
