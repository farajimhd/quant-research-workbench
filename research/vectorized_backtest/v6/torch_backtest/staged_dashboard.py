"""Fixed regions for stage progress, paginated top-100 rankings and timed messages."""
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

def ranked_rows(status):
    """Use authoritative ranks from a completed, common evaluation panel."""
    return sorted(status.get('top_strategies',[]),key=lambda row:row['rank'])


def ranking_page_size(height):
    return 50


def ranking_viewport_size(height):
    return max(1,min(50,height-26))


def navigate(status,key,*,rank=1,rank_page=0,detail_page=0,height=38):
    rows=ranked_rows(status);size=ranking_page_size(height)
    pages=max(1,(len(rows)+size-1)//size)
    rank_page=min(max(0,rank_page),pages-1)
    if key in ('n','b'):
        rank_page=(rank_page+(1 if key=='n' else -1))%pages
        if rows:rank=rows[rank_page*size]['rank']
        detail_page=0
    elif key in ('j','k') and rows:
        index=next((i for i,row in enumerate(rows) if row['rank']==rank),0)
        index=(index+(1 if key=='j' else -1))%len(rows)
        rank=rows[index]['rank'];rank_page=index//size;detail_page=0
    elif key==']':detail_page+=1
    elif key=='[':detail_page=max(0,detail_page-1)
    return rank,rank_page,detail_page


def render(status,*,width=110,height=38,now=None,view='financial'):
    now=time.time() if now is None else now
    config=status.get('config',{});cursor=status.get('progress') or {}
    age=max(0,now-status.get('updated_epoch',now))
    queued=status.get('queued_campaign') or {}
    progress_height=max(7 if queued else 5,2+len(status.get('process_progress',[])))
    page_size=ranking_page_size(height)
    visible_size=ranking_viewport_size(height)
    leader_height=visible_size+4
    layout=Layout()
    layout.split_column(Layout(name='header',size=2),Layout(name='progress',size=progress_height),
                        Layout(name='leaders',size=leader_height),Layout(name='metrics',ratio=1),
                        Layout(name='timing',size=2),Layout(name='messages',size=5 if height>=30 else 3),Layout(name='keys',size=1))
    population=queued.get('population') if queued else config.get('population')
    population_label=('Queued population' if queued else 'Population')
    layout['header'].update(Text(f"V6 {status.get('status','starting').upper()} | {status.get('stage','Preflight')} | updated {age:.0f}s ago\n{population_label} {number(population,',.0f')} | {status.get('focus','')} | validation {status.get('validation_status','SEALED')}",style='cyan' if age<30 else 'yellow'))
    bars=Progress(TextColumn('{task.description}',table_column=Column(min_width=18,no_wrap=True)),BarColumn(bar_width=None),TaskProgressColumn(),TextColumn('{task.completed:,.0f}/{task.total:,.0f}'),expand=True)
    if queued:
        bars.add_task('Campaign generations (queued)',total=queued['generations'],completed=0)
        bars.add_task('Generation 1 sessions (queued)',total=queued['sessions'],completed=0)
    for task in status.get('process_progress',[]):
        bars.add_task(task['label'],total=task['total'],completed=task['done'])
    for label,done,total in [('Qualification passes' if status.get('mode')=='profile' else 'Generations',
                               max(0,status.get('execution_pass',1)-1) if status.get('mode')=='profile' else status.get('completed_generations',0),
                               status.get('execution_passes',0) if status.get('mode')=='profile' else config.get('generations',0)),
                              ('Qualification sessions' if queued else 'Generation sessions',status.get('completed_sessions',0),config.get('training_sessions',0)),
                              ('Candidate batches',status.get('completed_batches',0),status.get('total_batches',0)),
                              ('Backtest timestamps',cursor.get('completed_seconds',0),cursor.get('total_seconds',0)),
                              ('Rule listings',status.get('completed_tickers',0),status.get('total_tickers',0))]:
        if total and not (status.get('process_progress') and label=='Candidate batches'):bars.add_task(label,total=total,completed=done)
    layout['progress'].update(bars)
    leaders=ranked_rows(status);selected=status.get('_rank',1)
    rank_pages=max(1,(len(leaders)+page_size-1)//page_size)
    rank_page=min(max(0,status.get('_rank_page',0)),rank_pages-1)
    table=Table(expand=True,padding=(0,1),show_edge=False)
    for label in ('Rank','Score','Net P&L $','Win %','Positions'):
        table.add_column(label,justify='right',no_wrap=True)
    if width>=110:
        table.add_column('Ex-best $',justify='right',no_wrap=True)
        table.add_column('Tail day $',justify='right',no_wrap=True)
        table.add_column('Days +%',justify='right',no_wrap=True)
        table.add_column('Drawdown $',justify='right',no_wrap=True)
    if width>=160:
        for label in ('Best day $','Worst day $','Position tail $','PF'):
            table.add_column(label,justify='right',no_wrap=True)
    page_rows=leaders[rank_page*page_size:(rank_page+1)*page_size]
    selected_index=next((i for i,row in enumerate(page_rows) if row['rank']==selected),0)
    viewport=min(max(0,selected_index-visible_size+1),max(0,len(page_rows)-visible_size))
    for leader in page_rows[viewport:viewport+visible_size]:
        metrics=leader.get('metrics',{});rank=leader['rank']
        values=[f'{rank}'+(' *' if rank==selected else ''),number(leader.get('score'),',.2f') if leader.get('valid',True) else 'INVALID',number(metrics.get('total_pnl')),
                number(None if metrics.get('position_win_rate') is None else metrics['position_win_rate']*100),number(metrics.get('positions'),',.0f')]
        if width>=110:values.extend([number(metrics.get('other_days_pnl')),number(metrics.get('session_tail_mean_pnl')),
            number(None if metrics.get('profitable_day_fraction') is None else 100*metrics['profitable_day_fraction']),number(metrics.get('worst_drawdown'))])
        if width>=160:values.extend([number(metrics.get('best_day_pnl')),number(metrics.get('worst_pnl')),number(metrics.get('position_tail_mean_pnl')),number(metrics.get('profit_factor'))])
        table.add_row(*values,style='bold cyan' if rank==selected else '')
    if not leaders:table.add_row('--','Pending completed ranking',*(['--']*(len(table.columns)-2)))
    basis=status.get('evaluation_basis','No completed ranking yet')
    layout['leaders'].update(Panel(table,title=f'Ranked {len(leaders)} | page {rank_page+1}/{rank_pages} | 50 rows | view {viewport+1}–{min(viewport+visible_size,len(page_rows))}',subtitle=basis,padding=0))
    leader=next((v for v in leaders if v['rank']==selected),{});metrics=leader.get('metrics',{})
    active=status.get('active_session') or {}
    grid=Table(expand=True,padding=(0,1),show_header=height>=30);grid.add_column('Metric');grid.add_column('Value',justify='right')
    if view=='objective':
        components=metrics.get('objective_components',{})
        rows=[(key.replace('_',' ').title(),number(value if key.endswith('reward') or key=='total_profit' else -value,'.6f')) for key,value in components.items()]
        rows.append(('Total score',number(leader.get('score'),',.2f') if leader.get('valid',True) else 'INVALID'))
    elif view=='positions':
        rows=[('Positions / fills',f"{number(metrics.get('positions'),',.0f')} / {number(metrics.get('fills'),',.0f')}"),
              ('Open positions',number(metrics.get('open'),',.0f')),
              ('Closed hold min / max s',f"{number(metrics.get('closed_hold_min_seconds'))} / {number(metrics.get('closed_hold_max_seconds'))}"),
              ('Closed hold mean / median / P90 s',' / '.join(number(metrics.get(k)) for k in ('closed_hold_mean_seconds','closed_hold_median_seconds','closed_hold_p90_seconds'))),
              ('Share-weighted hold s',number(metrics.get('mean_hold_seconds'))),
              ('Worst 20% closed-position mean P&L $',number(metrics.get('position_tail_mean_pnl'))),
              ('Position tail count / closed',f"{number(metrics.get('position_tail_count'),',.0f')} / {number(metrics.get('closed_positions'),',.0f')}"),
              ('Stop-risk / capital hours',f"{number(metrics.get('stop_risk_hours'))} / {number(metrics.get('capital_hours'))}")]
    elif view=='performance':
        timing=status.get('timing') or {};rows=[('Allocated lots / stock',number(status.get('execution_lot_capacity'),',.0f')),
            ('Rule lookahead','Enabled' if status.get('rule_prefetch_enabled') else 'Off'),
            ('Rule producer',str(status.get('rule_prefetch_backend','—')).upper()),
            ('Next rule batch',str(status['rule_prefetch_batch']+1) if status.get('rule_prefetch_batch') is not None else '—'),
            ('Next rules ready','Yes' if status.get('rule_prefetch_ready') else 'No'),
            ('Receipt writer','Pending' if status.get('receipt_writer_pending') else 'Idle')]
        rows.extend((key.replace('_',' ').title()+' s',number(value)) for key,value in timing.items())
        rows.extend([('GPU allocated GiB',number(status.get('gpu_gib'))),('Replay timestamps/s',number(status.get('replay_rate')))])
    else:
        rows=[('Net P&L $',number(metrics.get('total_pnl'))),('P&L excluding best day $',number(metrics.get('other_days_pnl'))),
              ('Best day P&L $',number(metrics.get('best_day_pnl'))),('Win rate %',number(None if metrics.get('position_win_rate') is None else 100*metrics['position_win_rate'])),
              ('Profitable sessions %',number(None if metrics.get('profitable_day_fraction') is None else 100*metrics['profitable_day_fraction'])),
              ('Worst session P&L $',number(metrics.get('worst_pnl'))),
              ('Worst 20% position mean P&L $',number(metrics.get('position_tail_mean_pnl'))),
              ('Profit factor',number(metrics.get('profit_factor'))),('Worst drawdown $',number(metrics.get('worst_drawdown'))),
              ('Daily Sharpe / annualized estimate',f"{number(metrics.get('sharpe_daily'),'.3f')} / {number(metrics.get('sharpe_annualized_estimate'),'.3f')}"),
              ('Median return / worst-tail loss %',f"{number(None if metrics.get('median_return') is None else metrics['median_return']*100)} / {number(None if metrics.get('tail_loss') is None else metrics['tail_loss']*100)}"),
              ('Active-batch provisional median / max P&L $',f"{number(active.get('pnl_median'))} / {number(active.get('pnl_max'))}"),
              ('Active-batch financial errors / overflows',f"{active.get('financial_error_candidates','—')} / {active.get('overflow_candidates','—')}")]
    if not metrics and view=='financial':
        rows=[('Current activity',status.get('stage','Waiting for worker snapshot')),
              ('Ranking','Pending completed common panel'),
              ('Live metrics scope','Active batch; provisional marked equity'),
              ('Batch P&L min / median / max $',' / '.join(number(active.get(k)) for k in ('pnl_min','pnl_median','pnl_max'))),
              ('Closed-position win rate %',number(None if active.get('position_win_rate') is None else active['position_win_rate']*100)),
              ('Profit factor',number(active.get('profit_factor'))),
              ('Largest drawdown $',number(active.get('drawdown_max'))),
              ('Closed positions / most open',f"{number(active.get('closed_positions'),',.0f')} / {number(active.get('open_positions_max'),',.0f')}"),
              ('Most fills',number(active.get('fills_max'),',.0f')),
              ('Closed hold mean / median / P90 s',' / '.join(number(active.get(k)) for k in ('closed_hold_mean_seconds','closed_hold_median_seconds','closed_hold_p90_seconds'))),
              ('Financial errors / overflows',f"{active.get('financial_error_candidates','--')} / {active.get('overflow_candidates','--')}")]
        if not active:rows.insert(1,('Backtest metrics','Not available until replay begins'))
    elif not metrics and view!='performance':rows.insert(0,('Ranking','Pending completed panel; live batch values provisional'))
    page=status.get('_page',0)
    metrics_height=height-(5+progress_height+leader_height+(5 if height>=30 else 3))
    available=max(1,metrics_height-(6 if height>=30 else 4))
    pages=max(1,(len(rows)+available-1)//available);page%=pages
    for label,value in rows[page*available:(page+1)*available]:grid.add_row(label,value)
    layout['metrics'].update(Panel(grid,title=f'{"Live batch" if not metrics else "Rank "+str(selected)} | {view} | page {page+1}/{pages}',padding=0))
    averages=status.get('average_timing',{})
    layout['timing'].update(Text(f"Elapsed {duration(now-status.get('started_epoch',now))} | replay ETA {duration(status.get('replay_eta'))} | session ETA {duration(status.get('session_eta'))}\nAverage session {duration(averages.get('end_to_end'))} | average batch {duration(status.get('average_batch_seconds'))} | GPU {number(status.get('gpu_gib'))} GiB"))
    messages=status.get('messages',[])[-(3 if height>=30 else 1):]
    lines=[f"{m.get('timestamp','')} {m.get('text','')}" for m in messages]
    if status.get('error'):lines[-1:]=[str(status['error'])]
    layout['messages'].update(Panel(Text('\n'.join(lines)),title='Messages | full history in events.jsonl',padding=0))
    layout['keys'].update(Text('N/B rankings page | J/K select | [/] detail page | F/O/T/P view | Q close'))
    return layout
