"""Single-owner operational view. Completed metrics never borrow active values."""
import time
from rich.console import Group
from rich.table import Table
from rich.text import Text
from rich.progress import Progress,BarColumn,TextColumn,TaskProgressColumn

def number(v,fmt=',.2f'):
    return '—' if v is None else format(v,fmt)

def render(status,*,width=110,height=38,now=None,view='financial'):
    now=time.time() if now is None else now
    age=max(0,now-status.get('updated_epoch',now));state=status.get('status','starting')
    severity='red' if state in ('failed','interrupted') else 'yellow' if age>30 else 'cyan'
    head=Text(f"V4  {state.upper()}  |  {status.get('stage','Preflight')}  |  updated {age:.0f}s ago",style='bold '+severity)
    config=status.get('config',{});cursor=status.get('progress',{})
    progress=Progress(TextColumn('{task.description:<14}'),BarColumn(bar_width=max(8,min(36,width-45))),TaskProgressColumn(),TextColumn('{task.completed:,.0f}/{task.total:,.0f}'),expand=False)
    for label,done,total in (
        ('Generations',status.get('completed_generations',0),config.get('generations',0)),
        ('Session',status.get('completed_sessions',0),config.get('training_sessions',0)),
        ('Backtest s',cursor.get('completed_seconds',0),cursor.get('total_seconds',0)),
        ('Preparation',status.get('prepared_sessions',0),config.get('training_sessions',0))):
        if total:progress.add_task(label,total=total,completed=done)
    if status.get('stage')=='Compile lifecycle rules' and status.get('total_tickers'):
        progress.add_task('Rule compile',total=status['total_tickers'],completed=status.get('completed_tickers',0))
    best=status.get('best_metrics') or status.get('closest_metrics') or {};timing=status.get('timing',{})
    title='COMPLETED TRAINING LEADER — not holdout evidence' if status.get('best_metrics') else 'CLOSEST COMPLETED CANDIDATE — INFEASIBLE' if status.get('closest_metrics') else 'COMPLETED TRAINING METRICS — no completed leader yet'
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
    if height<26:metrics=metrics[:8]+[metrics[12],metrics[-1]]
    if width>=100:
        for i in range(0,len(metrics),2):
            other=metrics[i+1] if i+1<len(metrics) else ('','');grid.add_row(*metrics[i],*other)
    else:
        for row in metrics:grid.add_row(*row)
    performance=Table(title='PERFORMANCE / OWNERSHIP',expand=True,padding=(0,1));performance.add_column('Stage');performance.add_column('Seconds',justify='right')
    for key in ('load','transfer','rule_prepare','compile','replay','end_to_end'):
        performance.add_row(key.replace('_',' ').title(),number(timing.get(key)))
    footer=Text(f"GPU {number(status.get('gpu_gib'),'.1f')} GiB | host {number(status.get('host_gib'),'.1f')} GiB | prefetched {status.get('prefetched_sessions','—')} | worker {status.get('worker_pid','—')}")
    issue=Text(status.get('error') or status.get('waiting_reason') or 'No reported failure',style='red' if status.get('error') else 'dim')
    if height<26 and view=='financial':return Group(head,progress,grid,footer,issue,Text('F financial | P performance | C objective; full metrics: status.json'))
    components=Table(title='OBJECTIVE COMPONENTS',expand=True);components.add_column('Component');components.add_column('Contribution',justify='right')
    for key,value in (best.get('objective_components') or {}).items():components.add_row(key.replace('_',' '),number(value,'.6f'))
    if view=='performance':return Group(head,progress,performance,footer,issue,Text('F financial | P performance | C objective'))
    if view=='objective':return Group(head,progress,components,footer,issue,Text('F financial | P performance | C objective'))
    if height<50:return Group(head,Text(status.get('focus','')),progress,grid,footer,issue,Text('F financial | P performance | C objective'))
    return Group(head,Text(status.get('focus','')),progress,grid,performance,components,footer,issue)
