"""Read-only fixed-screen observer. Never touches campaign ownership or CUDA."""
import argparse,json,time,os
from pathlib import Path
from rich.console import Console
from rich.live import Live
from .staged_dashboard import render,navigate
from concurrent.futures import ThreadPoolExecutor
from .ranking_diagnostics import diagnostics

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true')
    p.add_argument('--view',choices=('financial','objective','positions','performance'),default='financial');args=p.parse_args(argv)
    console=Console(no_color=bool(os.environ.get('NO_COLOR')));last={};rank=1;page=0;rank_page=0
    pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='receipt-diagnostics')
    future=None;requested_key=None;cache={};diagnostic_error=None
    def read():
        nonlocal last,future,requested_key,diagnostic_error
        try:last=json.loads((args.output/'status.json').read_text())
        except (OSError,json.JSONDecodeError):last={**last,'error':'Snapshot unavailable; retaining last good values'}
        display=dict(last)
        generation=last.get('completed_generations',0)
        if generation and last.get('evaluation_basis','').endswith('search ranking'):
            leaders=sorted(last.get('top_strategies',[]),key=lambda row:row['rank'])
            candidates=tuple(row['candidate'] for row in leaders[rank_page*50:(rank_page+1)*50])
            key=(generation,candidates)
            if future is not None and future.done():
                try:
                    cache[requested_key]=future.result();diagnostic_error=None
                    while len(cache)>4:cache.pop(next(iter(cache)))
                except (OSError,ValueError,KeyError,RuntimeError) as error:diagnostic_error=f'Receipt diagnostics unavailable: {type(error).__name__}'
                future=None
            if candidates and key not in cache and future is None and requested_key!=key:
                requested_key=key;future=pool.submit(diagnostics,args.output,generation,candidates)
            derived=cache.get(key,{})
            display['top_strategies']=[{**row,'metrics':{**row.get('metrics',{}),**derived.get(row['candidate'],{})}} for row in leaders]
            if diagnostic_error:display['error']=diagnostic_error
        if last.get('mode')=='profile' or last.get('status')=='qualifying':
            try:
                schedule=json.loads((args.output/'schedule.json').read_text())
                display['queued_campaign']=dict(generations=schedule[-1]['end_generation'],sessions=schedule[0]['sessions'],population=schedule[0]['population'])
            except (OSError,json.JSONDecodeError,IndexError,KeyError):pass
        return render({**display,'_rank':rank,'_page':page,'_rank_page':rank_page},width=console.width,height=console.height,view=args.view)
    if args.once or not console.is_terminal:
        try:
            read()
            if future is not None:
                try:future.result()
                except (OSError,ValueError,KeyError,RuntimeError):pass
            console.print(read());return 0
        finally:pool.shutdown(wait=True,cancel_futures=True)
    with Live(read(),console=console,screen=True,refresh_per_second=1) as live:
        try:
            while True:
                if os.name=='nt':
                    import msvcrt
                    if msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='q':break
                        rank,rank_page,page=navigate(last,key,rank=rank,rank_page=rank_page,detail_page=page,height=console.height)
                        if key in 'fotpc':args.view={'f':'financial','o':'objective','c':'objective','t':'positions','p':'performance'}[key];page=0
                live.update(read());time.sleep(1)
        except KeyboardInterrupt:pass
        finally:pool.shutdown(wait=True,cancel_futures=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
