"""Read-only, size-aware operational dashboard for Trade Reporting Repair."""
from collections import OrderedDict
from datetime import datetime,timezone
import json
from pathlib import Path
import re


def recent_events(path, limit=8):
    """Bounded tail; collapse repeated polls and keep byte ticks out of the feed."""
    try:
        with Path(path).open('rb') as stream:
            size=stream.seek(0,2);stream.seek(max(0,size-65536))
            lines=stream.read().decode('utf-8',errors='replace').splitlines()
    except FileNotFoundError:return []
    grouped=OrderedDict()
    for line in lines:
        stamp,sep,message=line.partition(' ')
        if not sep or 'T' not in stamp:continue
        if 'certified receipt reused' in message:continue
        if re.search(r'COPY[_-]\d+.*bytes \(',message):continue
        category=('COPY' if message.startswith(('Copy ','COPY')) else
                  'REPAIR' if 'mutat' in message.lower() else
                  'SQL' if message.startswith(('SQL','Retained')) else 'RUN')
        severity='error' if any(x in message.lower() for x in ('failed','error','exception')) else 'info'
        key=(category,message)
        old=grouped.pop(key,None)
        grouped[key]=dict(time=stamp[11:19],category=category,message=message,severity=severity,
                          repeats=(old['repeats']+1 if old else 1))
    return list(grouped.values())[-limit:]


def snapshot(root):
    root=Path(root)
    state=json.loads((root/'status.json').read_text(encoding='utf-8'))
    state['recent_events']=recent_events(root/'events.log')
    return state


def duration(seconds):
    seconds=max(0,int(seconds or 0))
    if seconds>=3600:return f'{seconds//3600}h {seconds%3600//60:02d}m'
    if seconds>=60:return f'{seconds//60}m {seconds%60:02d}s'
    return f'{seconds}s'


def worker_view(name,row):
    """Accept old snapshots while upgrading the monitor independently."""
    row=dict(row)
    detail=row.get('detail','')
    if name=='SQL-1' and row.get('stage')=='Repair' and '| mutating |' in detail:
        row.update(stage='Waiting on mutation',day=detail.split(' | ')[0].split()[-1],
                   detail=detail.split(' | ')[-1],kind='mutation')
    return row


def render(state,width=110,height=30):
    from rich.console import Group
    from rich.panel import Panel
    from rich.progress_bar import ProgressBar
    from rich.table import Table
    from rich.text import Text
    from rich import box
    def bar_lines(bar,label):
        result=Table.grid(expand=True)
        result.add_column(ratio=1)
        result.add_row(bar);result.add_row(label)
        return result
    width=max(40,width);height=max(12,height)
    now=datetime.now(timezone.utc)
    try:age=max(0,(now-datetime.fromisoformat(state.get('updated_at',''))).total_seconds())
    except (ValueError,TypeError):age=0
    stale=age>15 and state.get('status') not in ('complete','failed','interrupted')
    status=str(state.get('status','starting')).upper()
    color='red' if status=='FAILED' else 'yellow' if stale or status=='INTERRUPTED' else 'cyan'
    workers={k:worker_view(k,v) for k,v in state.get('workers',{}).items()}
    mutation=next((r for k,r in workers.items() if r.get('kind')=='mutation' or k=='MUTATE'),None)
    total=int(state.get('total',0));reused=int(state.get('reused',0));completed=int(state.get('completed',0))
    certified=min(total,reused+completed)
    title=Text('TRADE REPORTING REPAIR',style='bold')
    title.append('  '+('STALE' if stale else status),style=color)
    scope=Text(f'{state.get("start", "")} to {state.get("end", "")}  |  Backfill  |  PID {state.get("pid", "-")}',style='dim',no_wrap=True,overflow='ellipsis')
    summary=Table.grid(expand=True,padding=(0,1))
    summary.add_column(width=16);summary.add_column(ratio=1);summary.add_column(width=18,justify='right')
    summary.add_row('Certified days',ProgressBar(total=max(1,total),completed=certified,width=None,
        complete_style='green',finished_style='green'),f'{certified}/{total}  {certified/max(total,1):.1%}')
    counters=Text(f'New {completed}   Reused {reused}   Remaining {max(0,total-certified)}   '
                  f'Failed {state.get("failed",0)}   Retried {state.get("retried",0)}',no_wrap=True,overflow='ellipsis')
    parts=[title,scope,summary,counters]
    if 'prepared' in state:
        parts.append(Text(f'Prepared CSV days {state["prepared"]}  |  SQL workers {state.get("sql_workers",1)}  |  '
                          f'Copy workers {state.get("copy_workers",2)}  |  Awaiting certification',style='dim',no_wrap=True,overflow='ellipsis'))
    if mutation:
        text=f'FLAG UPDATE  {mutation.get("day", "")}  |  {mutation.get("stage", "")}  |  {mutation.get("detail", "")}'
        parts.append(Text(text,style='yellow',overflow='ellipsis',no_wrap=True))
    if state.get('mutation_parts') and height>=24:
        rewrites=Table.grid(expand=True,padding=(0,1))
        rewrites.add_column(width=24);rewrites.add_column(ratio=1);rewrites.add_column(width=15,justify='right')
        for row in state['mutation_parts'][:2]:
            fraction=max(0,min(1,float(row['progress'])))
            rewrites.add_row('Part rewrite '+row['result_part_name'].split('_')[0],
                ProgressBar(total=1,completed=fraction,width=None,complete_style='yellow'),
                f'{fraction:.1%} / {duration(row["elapsed"])}')
        parts.append(rewrites)
    if stale:parts.append(Text(f'No fresh producer snapshot for {duration(age)}. Last values retained.',style='bold yellow'))

    table=Table(box=box.SIMPLE_HEAD,expand=True,padding=(0,1),show_edge=False)
    table.add_column('Worker',width=7,min_width=7,no_wrap=True)
    table.add_column('Day',width=10,min_width=10,no_wrap=True)
    table.add_column('Stage',width=19 if width>=95 else 14,no_wrap=True,overflow='ellipsis')
    table.add_column('Progress',ratio=1)
    if width>=105:table.add_column('Rate / ETA',width=21,no_wrap=True)
    if width>=130:table.add_column('Elapsed',width=9,no_wrap=True)
    visible=[(k,r) for k,r in sorted(workers.items()) if k!='QUERY-1' and r is not mutation and r.get('kind')!='mutation' and k!='MUTATE']
    compact=height<24 or width<75
    maximum=max(1,(height-len(parts)-5)//(1 if compact else 2))
    for name,row in visible[:maximum]:
        done,total_work=row.get('done'),row.get('total')
        stage=row.get('stage','Starting');detail=row.get('detail','')
        if total_work and done is not None:
            byte_units=row.get('unit','bytes')=='bytes'
            label=(f'{done/1024**3:.2f}/{total_work/1024**3:.2f} GiB' if byte_units else f'{done}/{total_work} {row.get("unit","units")}')
            progress=bar_lines(ProgressBar(total=total_work,completed=min(done,total_work),width=None,
                complete_style='green' if done>=total_work else 'cyan'),Text(f'{label}  {done/total_work:.0%}',style='dim'))
        elif stage.lower() in ('idle','waiting for work','waiting for ssd','wait source parts','failed','interrupted') or status in ('FAILED','INTERRUPTED','COMPLETE'):
            progress=Text(detail or 'Waiting for next admitted day',style='dim',overflow='ellipsis',no_wrap=True)
        elif stage=='Prepared':
            progress=bar_lines(ProgressBar(total=1,completed=1,width=None,finished_style='green'),Text('Evidence saved; awaiting flag update',style='dim',no_wrap=True,overflow='ellipsis'))
        else:
            # A pulse shows activity, never a fabricated completion percentage.
            progress=bar_lines(ProgressBar(total=None,pulse=True,width=None,pulse_style='cyan'),
                           Text(row.get('query_detail') or detail or 'Working; total not yet known',style='dim',overflow='ellipsis',no_wrap=True))
        if compact:
            progress=Text((f'{done/total_work:.0%} '+('━'*int(10*done/total_work)).ljust(10,'─')
                           if total_work and done is not None else
                           'Saved' if stage=='Prepared' else row.get('query_detail') or detail or 'Waiting'),
                          no_wrap=True,overflow='ellipsis')
        cells=[name,row.get('day',''),stage,progress]
        if width>=105:
            rate=row.get('rate_bytes_second')
            cells.append((f'{rate/1024**2:.1f} MiB/s / '+duration((total_work-done)/rate)
                          if rate and total_work and done is not None and done<total_work else '-'))
        if width>=130:cells.append(duration(row.get('elapsed_seconds')))
        table.add_row(*cells)
    parts.append(table)
    if len(visible)>maximum:parts.append(Text(f'{len(visible)-maximum} additional worker rows; enlarge the terminal to show all.',style='yellow'))
    query=workers.get('QUERY-1',{})
    if query.get('stage') not in (None,'Idle'):
        parts.append(Text('QUERY  '+query.get('detail',''),style='dim',overflow='fold'))
    # Recent events get the remaining vertical space; important state stays above.
    logs=state.get('recent_events',[])
    log_count=max(0,min(5,height-(len(parts)+4+(1 if compact else 2)*min(len(visible),maximum)+3)))
    if logs and log_count:
        events=Table.grid(expand=True,padding=(0,1))
        events.add_column(width=8,no_wrap=True);events.add_column(width=6,no_wrap=True);events.add_column(ratio=1)
        for event in logs[-log_count:]:
            message=event['message']+(f' (observed {event["repeats"]}x)' if event.get('repeats',1)>1 else '')
            events.add_row(event['time'],event['category'],Text(message,
                style='red' if event.get('severity')=='error' else 'dim',overflow='ellipsis',no_wrap=True))
        parts.append(Panel(events,title='Recent events · UTC',box=box.SIMPLE,padding=(0,0)))
    if status=='FAILED':parts.append(Text(state.get('message','Inspect events.log'),style='red',no_wrap=True,overflow='ellipsis'))
    parts.append(Text(f'Snapshot age {age:.0f}s | Ctrl+C: close monitor | Log: events.log',style='dim',overflow='ellipsis',no_wrap=True))
    return Group(*parts)
