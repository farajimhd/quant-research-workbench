"""Fixed regions for stage progress, three ranked strategies and timed messages."""
import time
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table,Column
from rich.text import Text
from rich.progress import Progress,TextColumn,BarColumn,TaskProgressColumn

def number(value,fmt=',.2f'):
    return '—' if value is None else format(value,fmt)

def duration(value):
    if value is None:return '—'
    minutes,seconds=divmod(max(0,int(value)),60);hours,minutes=divmod(minutes,60)
    return f'{hours}:{minutes:02d}:{seconds:02d}'

def render(status,*,width=110,height=38,now=None,view='financial'):
    now=time.time() if now is None else now
    config=status.get('config',{});cursor=status.get('progress',{})
    age=max(0,now-status.get('updated_epoch',now))
    layout=Layout()
    layout.split_column(Layout(name='header',size=2),Layout(name='progress',size=5),
                        Layout(name='leaders',size=6 if height>=30 else 3),Layout(name='metrics',ratio=1),
                        Layout(name='timing',size=2),Layout(name='messages',size=5 if height>=30 else 3),Layout(name='keys',size=1))
    layout['header'].update(Text(f"V5 {status.get('status','starting').upper()} | {status.get('stage','Preflight')} | updated {age:.0f}s ago\n{status.get('focus','')} | validation {status.get('validation_status','SEALED')}",style='cyan' if age<30 else 'yellow'))
    bars=Progress(TextColumn('{task.description}',table_column=Column(min_width=18,no_wrap=True)),BarColumn(bar_width=None),TaskProgressColumn(),TextColumn('{task.completed:,.0f}/{task.total:,.0f}'),expand=True)
    for label,done,total in [('Generations',status.get('completed_generations',0),config.get('generations',0)),
                              ('Sessions',status.get('completed_sessions',0),config.get('training_sessions',0)),
                              ('Candidate batches',status.get('completed_batches',0),status.get('total_batches',0)),
                              ('Backtest timestamps',cursor.get('completed_seconds',0),cursor.get('total_seconds',0)),
                              ('Rule listings',status.get('completed_tickers',0),status.get('total_tickers',0))]:
        if total:bars.add_task(label,total=total,completed=done)
    layout['progress'].update(bars)
    leaders=status.get('top_strategies',[]);selected=status.get('_rank',1)
    table=Table(expand=True,padding=(0,1));table.add_column('Rank');table.add_column('Score',justify='right');table.add_column('Net P&L $',justify='right');table.add_column('Win %',justify='right');table.add_column('Positions',justify='right')
    for rank in range(1,4):
        leader=next((v for v in leaders if v['rank']==rank),{});metrics=leader.get('metrics',{})
        table.add_row(f'{rank}'+(' *' if rank==selected else ''),number(leader.get('score'),'.6f'),number(metrics.get('total_pnl')),
                      number(None if metrics.get('position_win_rate') is None else metrics['position_win_rate']*100),number(metrics.get('positions'),',.0f'))
    layout['leaders'].update(Panel(table,title=status.get('evaluation_basis','No completed ranking yet'),padding=0))
    leader=next((v for v in leaders if v['rank']==selected),{});metrics=leader.get('metrics',{})
    active=status.get('active_session') or {}
    grid=Table(expand=True,padding=(0,1));grid.add_column('Metric');grid.add_column('Value',justify='right')
    if view=='objective':
        components=metrics.get('objective_components',{})
        rows=[(key.replace('_',' ').title(),number(value if key.endswith('reward') else -value,'.6f')) for key,value in components.items()]
        rows.append(('Total score',number(leader.get('score'),'.6f')))
    elif view=='positions':
        rows=[('Positions / fills',f"{number(metrics.get('positions'),',.0f')} / {number(metrics.get('fills'),',.0f')}"),
              ('Open positions',number(metrics.get('open'),',.0f')),
              ('Closed hold min / max s',f"{number(metrics.get('closed_hold_min_seconds'))} / {number(metrics.get('closed_hold_max_seconds'))}"),
              ('Closed hold mean / median / P90 s',' / '.join(number(metrics.get(k)) for k in ('closed_hold_mean_seconds','closed_hold_median_seconds','closed_hold_p90_seconds'))),
              ('Share-weighted hold s',number(metrics.get('mean_hold_seconds'))),
              ('Stop-risk / capital hours',f"{number(metrics.get('stop_risk_hours'))} / {number(metrics.get('capital_hours'))}")]
    elif view=='performance':
        timing=status.get('timing',{});rows=[(key.replace('_',' ').title()+' s',number(value)) for key,value in timing.items()]
        rows.extend([('GPU allocated GiB',number(status.get('gpu_gib'))),('Replay timestamps/s',number(status.get('replay_rate')))])
    else:
        rows=[('Net P&L $',number(metrics.get('total_pnl'))),('P&L excluding best day $',number(metrics.get('other_days_pnl'))),
              ('Best day P&L $',number(metrics.get('best_day_pnl'))),('Win rate %',number(None if metrics.get('position_win_rate') is None else 100*metrics['position_win_rate'])),
              ('Profit factor',number(metrics.get('profit_factor'))),('Worst drawdown $',number(metrics.get('worst_drawdown'))),
              ('Daily Sharpe / annualized estimate',f"{number(metrics.get('sharpe_daily'),'.3f')} / {number(metrics.get('sharpe_annualized_estimate'),'.3f')}"),
              ('Median return / worst-tail loss %',f"{number(None if metrics.get('median_return') is None else metrics['median_return']*100)} / {number(None if metrics.get('tail_loss') is None else metrics['tail_loss']*100)}"),
              ('Active-batch provisional median / max P&L $',f"{number(active.get('pnl_median'))} / {number(active.get('pnl_max'))}"),
              ('Active-batch financial errors / overflows',f"{active.get('financial_error_candidates','—')} / {active.get('overflow_candidates','—')}")]
    if not metrics and view!='performance':rows.insert(0,('Ranking','Pending completed panel; live batch values provisional'))
    page=status.get('_page',0);available=max(2,height-(24 if height>=30 else 19))
    pages=max(1,(len(rows)+available-1)//available);page%=pages
    for label,value in rows[page*available:(page+1)*available]:grid.add_row(label,value)
    layout['metrics'].update(Panel(grid,title=f'Rank {selected} | {view} | page {page+1}/{pages}',padding=0))
    averages=status.get('average_timing',{})
    layout['timing'].update(Text(f"Elapsed {duration(now-status.get('started_epoch',now))} | replay ETA {duration(status.get('replay_eta'))} | session ETA {duration(status.get('session_eta'))}\nAverage session {duration(averages.get('end_to_end'))} | average batch {duration(status.get('average_batch_seconds'))} | GPU {number(status.get('gpu_gib'))} GiB"))
    messages=status.get('messages',[])[-(3 if height>=30 else 1):]
    lines=[f"{m.get('timestamp','')} {m.get('text','')}" for m in messages]
    if status.get('error'):lines[-1:]=[str(status['error'])]
    layout['messages'].update(Panel(Text('\n'.join(lines)),title='Messages | full history in events.jsonl',padding=0))
    layout['keys'].update(Text('1/2/3 rank | F financial | O objective | T positions | P performance | N next page | Q close'))
    return layout
