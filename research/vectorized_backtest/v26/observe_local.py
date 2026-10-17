"""Read-only local-session monitor; follows the advertised V26 campaign."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,time,json
from pathlib import Path
import psutil
from rich.console import Console,Group
from rich.live import Live
from rich.table import Table
from rich.text import Text
from .progress_io import read_progress,shared_binary


def snapshot(root,cache=None):
    cache=cache if cache is not None else {}
    status=read_progress(root/'status.json');owner=read_progress(root/'active.json');running=False
    try:running=abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01
    except (psutil.NoSuchProcess,KeyError,TypeError):pass
    manifest=read_progress(root/'manifest.json');days=manifest.get('days',[]);rows=[]
    for day in days:
        folder=root/'local'/day;complete=read_progress(folder/'complete.json') if (folder/'complete.json').exists() else {}
        state=read_progress(folder/'status.json') if folder.exists() else {}
        sealed=complete.get('status')=='complete';experts=None;score=None
        if sealed:
            path=folder/'archive.json';stamp=path.stat().st_mtime_ns
            if cache.get(day,{}).get('stamp')!=stamp:
                try:
                    with shared_binary(path) as f:archive=json.load(f)
                    cache[day]=dict(stamp=stamp,experts=len(archive),score=max((r['score'] for r in archive),default=None))
                except OSError:pass
            experts=cache.get(day,{}).get('experts');score=cache.get(day,{}).get('score')
        rows.append(dict(day=day,completed=complete.get('generations',state.get('completed_generations',0)),
                         state='complete' if sealed else 'running' if running and state else 'stopped' if state else 'queued',
                         stage=state.get('stage',''),experts=experts,score=score,stale=state.get('progress_stale',False)))
    return dict(status=status.get('status','starting'),stage=status.get('stage',''),running=running,rows=rows,
                completed=sum(r['state']=='complete' for r in rows),total=len(days),target=manifest.get('local_generations',30))


def render(data,width=110,height=38,page=0):
    rows=data['rows'];size=max(1,height-10);pages=max(1,(len(rows)+size-1)//size);page=min(page,pages-1)
    heading=Text(f"V26 LOCAL SESSIONS | {'RUNNING' if data['running'] else 'OPTIMIZER STOPPED'}",style='bold green' if data['running'] else 'bold red')
    table=Table(expand=True);table.add_column('Session');table.add_column('Generations',justify='right');table.add_column('State');table.add_column('Experts',justify='right');table.add_column('Best score',justify='right')
    if width>=95:table.add_column('Current stage')
    for row in rows[page*size:(page+1)*size]:
        values=[row['day'],f"{row['completed']}/{data['target']}",row['state']+(' *' if row['stale'] else ''),str(row['experts']) if row['experts'] is not None else '-',f"{row['score']:,.1f}" if row['score'] is not None else '-']
        if width>=95:values.append(row['stage'])
        table.add_row(*values)
    return Group(heading,Text(f"Completed sessions: {data['completed']}/{data['total']} | Status: {data['status']}"),
                 Text(data['stage'],style='yellow' if not data['running'] else None),table,
                 Text(f'Read only | n/b: pages ({page+1}/{pages}) | q: close panel | * status read stale'))


def main():
    # Compatibility entry point; this module is never imported by the search.
    from .local_details import main as detailed
    return detailed()


if __name__=='__main__':raise SystemExit(main())
