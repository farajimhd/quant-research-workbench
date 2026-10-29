"""Read-only V57 global-pilot progress and sealed financial rankings."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,time
from pathlib import Path
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import Progress,BarColumn,TextColumn,TaskProgressColumn
from research.vectorized_backtest.v27.fixed_terminal import FixedTerminal
from research.vectorized_backtest.v42.distributed_bus import read

SORTS={'1':('score',False),'2':('net_pnl',False),'3':('drawdown',True),
       '4':('gross_losses',True),'5':('win_rate',False),'6':('closed_positions',False)}

def render(status,width,height,sort='score',reverse=False,eligible=True,page=0,validation=False):
    layout=Layout();layout.split_column(Layout(name='head',size=3),Layout(name='bars',size=3),Layout(name='rows'),Layout(name='keys',size=3))
    progress=status.get('worker_progress',{});stage=progress.get('stage',status.get('stage','Awaiting pilot'))
    full=status.get('run_kind')=='full';scope='24 train / 12 validation | workstation' if full else 'Aug13 | direct phase2'
    if status.get('status')!='running':stage=status.get('stage',stage)
    if full and status.get('current_day'):stage=f"{status.get('evaluation_split','training')} {status['current_day']} | {stage}"
    objective=status.get('objective',dict(loss_weight=0.,drawdown_weight=0.))
    cost=f"Score = net profit - {objective['loss_weight']:g}*gross losses - {objective['drawdown_weight']:g}*equity DD"
    layout['head'].update(Text(f"V57 | {scope} | {status.get('status','starting').upper()}\n{cost}\n{stage}",style='cyan'))
    bars=Progress(TextColumn('{task.description}'),BarColumn(bar_width=None,complete_style='cyan',finished_style='green'),TaskProgressColumn(),TextColumn('{task.fields[count]}'),expand=True)
    done=status.get('completed_generations',0);target=status.get('total_generations',50);bars.add_task('Sealed generations',total=target,completed=done,count=f'{done}/{target}')
    clocks=progress.get('completed_seconds',0);total=progress.get('total_seconds',0) or max(clocks,1)
    bars.add_task('Current replay',total=total,completed=clocks,count=f'{clocks}/{total}' if progress else 'Initializing')
    if full:
        done_sessions=status.get('completed_sessions',0);total_sessions=status.get('total_sessions',24)
        bars.add_task('Sessions this pass',total=total_sessions,completed=done_sessions,count=f'{done_sessions}/{total_sessions}')
    layout['bars'].update(bars);rows=[]
    for original in status.get('validation_ranking' if validation else 'training_ranking',[]):
        row=dict(original);row['win_rate']=row['winning_positions']/row['closed_positions'] if row['closed_positions'] else None
        if not eligible or row['eligible']:rows.append(row)
    ascending=next(v[1] for v in SORTS.values() if v[0]==sort)
    direction=1 if ascending!=reverse else -1
    rows.sort(key=lambda r:(r[sort] is None,direction*r[sort] if r[sort] is not None else 0,r['serial']))
    count=max(1,height-12);start=min(page*count,max(0,(len(rows)-1)//count*count));table=Table(expand=True,padding=(0,0))
    for label in ('#','Serial','Score $','Net $','Equity DD $','Loss $','Positions','Win %','E'):table.add_column(label,justify='right',no_wrap=True)
    for index,row in enumerate(rows[start:start+count],start+1):
        table.add_row(str(index),str(row['serial']),f"{row['score']:,.2f}",f"{row['net_pnl']:,.2f}",f"{row['drawdown']:,.2f}",f"{row['gross_losses']:,.2f}",str(int(row['closed_positions'])),f"{100*row['win_rate']:.1f}" if row['win_rate'] is not None else '-', 'Y' if row['eligible'] else 'N')
    layout['rows'].update(Panel(table,title=f"{'VALIDATION: DISPLAY ONLY' if validation else 'TRAINING'} | Last sealed generation {status.get('ranking_generation',0)} | {len(rows)} {'eligible' if eligible else 'all'} candidates | sort {sort}",padding=0))
    validation_note='Fresh 6,144 -> 1,024. Full score sums session equity DD; validation display only.' if full else 'Fresh 6,144 -> 1,024. Validation remains unopened.'
    layout['keys'].update(Text(f'1 score | 2 net | 3 DD | 4 loss | 5 win | 6 positions | V reverse\nN/B page | Z eligibility | T training/validation | Q closes observer only\n{validation_note}'))
    return layout

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();console=Console();state={};sort='score';reverse=False;eligible=True;page=0;validation=False
    from contextlib import nullcontext
    once=a.once or not console.is_terminal
    with nullcontext() if once else FixedTerminal(console) as live:
        while True:
            try:state=read(a.output/'full-status.json')
            except (OSError,ValueError):pass
            draw=lambda w,h:render(state,w,h,sort,reverse,eligible,page,validation)
            if once:console.print(draw(console.width,console.height));return
            live.update(draw)
            if os.name=='nt':
                import msvcrt
                while msvcrt.kbhit():
                    key=msvcrt.getwch().lower()
                    if key=='q':return
                    if key in SORTS:sort=SORTS[key][0];page=0
                    elif key=='v':reverse=not reverse
                    elif key=='z':eligible=not eligible;page=0
                    elif key=='t':validation=not validation;page=0
                    elif key=='n':page+=1
                    elif key=='b':page=max(0,page-1)
            time.sleep(.5)

if __name__=='__main__':main()
