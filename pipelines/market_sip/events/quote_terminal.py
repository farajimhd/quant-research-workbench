"""Compact snapshot renderer for a durable quote-bar campaign."""
from datetime import datetime,timezone
from rich.console import Group
from rich.text import Text
from rich.progress_bar import ProgressBar
from rich.table import Table


def snapshot(state,width=100,height=35):
    total=int(state.get('total',0));new=int(state.get('completed',0));reuse=int(state.get('reused',0))
    failed=int(state.get('failed',0));done=new+reuse
    status=state.get('status','planning');age=None
    try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(state['updated_at'])).total_seconds()
    except (KeyError,ValueError):pass
    stale=age is not None and age>15 and status=='running'
    title=Text(f'BID / ASK OHLC  |  {status.upper()}',style='red' if failed else 'yellow' if stale else 'bold cyan')
    parts=[title,Text('1s -> 5s -> 10s -> 30s | canonical events | live_market_ssd'),
        ProgressBar(total=max(1,total),completed=done,width=max(12,width-4)),
        Text(f'Certified {done:,}/{total:,} ticker-days  |  New {new:,}  Reused {reuse:,}  Failed {failed:,}')]
    eta=state.get('eta_seconds');elapsed=float(state.get('elapsed',0))
    parts.append(Text(f"Elapsed {elapsed/3600:.2f}h | Rate {state.get('ticker_days_per_hour',0):,.0f} units/h | ETA "+(f'{eta/3600:.2f}h' if eta is not None else 'sampling')))
    workers=state.get('workers',{});active=[(k,v) for k,v in workers.items() if v.get('phase',v.get('stage','')) not in ('Idle','Done','')]
    table=Table(expand=True);table.add_column('Worker',width=12);table.add_column('Day',width=10);table.add_column('Activity')
    for name,row in active[:max(1,height-13)]:
        table.add_row(name,str(row.get('day','')),str(row.get('phase',row.get('stage','')))+'  '+str(row.get('detail','')))
    parts.extend([Text(f'Active {len(active)} | Queued {state.get("queued",0):,}'),table])
    if len(active)>max(1,height-13):parts.append(Text('Additional workers active; enlarge terminal for more rows.'))
    if state.get('message'):parts.append(Text(str(state['message'])[:max(40,width*2)],style='yellow'))
    if state.get('signals'):parts.append(Text(str(state['signals']),style='yellow',overflow='fold'))
    parts.append(Text(f"Updated {state.get('updated_at','pending')}"+(' | STALE' if stale else ''),style='yellow' if stale else 'dim'))
    return Group(*parts)
