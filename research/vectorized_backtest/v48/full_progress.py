"""Read-only full-campaign console, with session detail and global validation views."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import Progress,BarColumn,TextColumn,TaskProgressColumn
from research.vectorized_backtest.v42.distributed_assets import remote
from research.vectorized_backtest.v42.distributed_bus import read
from research.vectorized_backtest.v27.fixed_terminal import FixedTerminal


class SnapshotObserver:
    def __init__(self,path,node,day):self.path=path;self.full_node=node;self.day=day;self.last={}
    def read(self):
        try:self.last=read(self.path)
        except (OSError,ValueError):pass
        return dict(self.last) if self.last else dict(rows=[],case_labels={},phase=0,running=True,day=self.day,generation=0,target=50,session_progress={'stage':'Awaiting first sealed score panel'},payloads={},display_ready=False)
    def close(self):pass


def render(status,job,width,height,selection=0,validation=False):
    layout=Layout();layout.split_column(Layout(name='header',size=2),Layout(name='bars',size=3),Layout(name='table'),Layout(name='note',size=2),Layout(name='keys',size=2))
    global_view=status.get('phase')==2;split='VALIDATION: DISPLAY ONLY' if validation else 'TRAINING'
    layout['header'].update(Text(f"V48 FULL | lambda {job['objective']['loss_weight']:g} | P0 50 / P1 50 / P2 100\n{status.get('status','starting').upper()} | {status.get('stage','Waiting for coordinator')}" ,style='cyan'))
    bars=Progress(TextColumn('{task.description}'),BarColumn(bar_width=None,complete_style='cyan',finished_style='green'),TaskProgressColumn(),TextColumn('{task.fields[count]}'),expand=True)
    completed=sum(s.get('status')=='complete' for s in status.get('sessions',{}).values())
    for label,count,total in [('Session pairs',completed,24),('Global generations',status.get('completed_generations',0) if global_view else 0,100),('Current '+str(status.get('evaluation_split','local')),status.get('completed_sessions',0),status.get('total_sessions',24))]:
        bars.add_task(label,total=total,completed=count,count=f'{count}/{total}')
    layout['bars'].update(bars);table=Table(expand=True,padding=(0,0));size=max(1,height-13);start=selection//size*size
    if global_view:
        rows=status.get('validation_ranking' if validation else 'training_ranking',[])
        for name in ('#','Serial','Score $','Net $','Loss $','Real DD $','E'):table.add_column(name,no_wrap=True,justify='right')
        for i,r in enumerate(rows[start:start+size],start):table.add_row(str(i+1),str(r['serial']),f"{r['score']:,.2f}",f"{r['net_pnl']:,.2f}",f"{r['gross_losses']:,.2f}",f"{r['drawdown']:,.2f}",'Y' if r['eligible'] else 'N')
        title=f"{split} | last sealed ranking {status.get('ranking_generation','pending')} | rows {start+1 if rows else 0}-{min(start+size,len(rows))}"
    else:
        rows=sorted(status.get('sessions',{}).items())
        for name in ('Session','Host','Phase 0','Phase 1','Stage'):table.add_column(name,no_wrap=True)
        for i,(day,s) in enumerate(rows[start:start+size],start):
            phase=s.get('phase',0);done=s.get('completed_generations',0);finished=s.get('status')=='complete'
            p0='50/50' if phase==1 or finished else f'{done}/50';p1='50/50' if finished else f'{done}/50' if phase==1 else 'queued'
            table.add_row(day,s['node'],p0,p1,s.get('stage',s.get('status','queued')),style='bold cyan' if i==selection else 'green' if finished else None)
        title='SESSION PAIRS: phase 0 and phase 1 stay on the same host'
    layout['table'].update(Panel(table,title=title,padding=0))
    h=status.get('heartbeats',{});line=' | '.join(n+': '+h.get(n,{}).get('stage','awaiting heartbeat') for n in job['machines'])
    layout['note'].update(Text(line+'\n'+('Validation cannot affect reproduction or training RNG.' if global_view else 'Enter opens selected session detail. Esc/H returns to this full overview.')))
    layout['keys'].update(Text('J/K select | N/B page | Enter session details | V training/validation (phase 2)\nQ closes observer only; training continues.'))
    return layout


def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--node',choices=['laptop','workstation'],default='laptop');p.add_argument('--once',action='store_true');a=p.parse_args();job=read(a.job)
    root=Path(job['campaign']) if a.node=='workstation' else remote(job['campaign'],job['workstation_share'])
    console=Console();once=a.once or not console.is_terminal;selection=0;validation=False;last={};observer=None;controls=None
    from .progress import Controls,rule_lines
    from .progress_view import render as detail_render
    try:
        from contextlib import nullcontext
        with nullcontext() if once else FixedTerminal(console) as live:
            while True:
                try:last=read(root/'full-status.json')
                except (OSError,ValueError):last=dict(last,stage='Waiting for current coordinator status')
                if observer:
                    data=observer.read()
                    if data:
                        node=observer.full_node;hb=last.get('heartbeats',{}).get(node,{})
                        data['running']=hb.get('status')!='exited' and time.time()-hb.get('epoch',0)<180
                        current=last.get('sessions',{}).get(observer.day,{})
                        if current.get('phase')==data.get('phase'):data['session_progress']=current
                    draw=lambda w,h:detail_render(data,w,h,controls.settings,controls.selection,controls.detail,controls.stage,controls.page,rule_lines)
                else:draw=lambda w,h:render(last,job,w,h,selection,validation)
                if once:console.print(draw(console.width,console.height));break
                live.update(draw)
                if os.name=='nt':
                    import msvcrt
                    while msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='q':return
                        if observer:
                            if key in ('h','\x1b'):observer.close();observer=None;continue
                            controls.key(key,data,live.dimensions()[1]);continue
                        count=1024 if last.get('phase')==2 else 24
                        if key in ('j','k','n','b'):
                            step=1 if key in ('j','k') else max(1,live.dimensions()[1]-13);selection=max(0,min(count-1,selection+(step if key in ('j','n') else -step)))
                        elif key=='v':validation=not validation;selection=0
                        elif key=='\r' and last.get('phase')!=2:
                            days=sorted(last.get('sessions',{}))
                            if not days:continue
                            day=days[min(selection,len(days)-1)];node=last['sessions'][day]['node'];transport=Path(job['transport']) if a.node=='workstation' else remote(job['transport'],job['workstation_share'])
                            observer=SnapshotObserver(transport/'details'/node/(day+'.json'),node,day);controls=Controls()
                time.sleep(.5)
    except KeyboardInterrupt:pass
    finally:
        if observer:observer.close()


if __name__=='__main__':main()
