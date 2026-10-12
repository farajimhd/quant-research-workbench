"""Read-only V8 terminal with training and display-only validation views."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
from rich.console import Console
from .fixed_terminal import FixedTerminal
from .terminal import render, ranking_page_size
from research.vectorized_backtest.v6.torch_backtest.staged_dashboard import navigate


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true')
    p.add_argument('--view',choices=('financial','objective','positions','performance','validation'),default='financial')
    args=p.parse_args(argv);console=Console(no_color=bool(os.environ.get('NO_COLOR')))
    if console.is_terminal and not args.once and os.name=='nt':
        import ctypes
        ctypes.windll.kernel32.GetConsoleWindow.restype=ctypes.c_void_p
        window=ctypes.windll.kernel32.GetConsoleWindow()
        if window:ctypes.windll.user32.ShowWindow(ctypes.c_void_p(window),3)
    last={};rank=1;page=0;rank_page=0
    def read(width=None,height=None):
        nonlocal last
        try:
            candidate=json.loads((args.output/'status.json').read_text(encoding='utf-8'))
            if candidate.get('validation_feedback') is not False:raise ValueError('Invalid training snapshot')
            last=candidate
        except (OSError,ValueError):last={**last,'error':'Snapshot unavailable; retaining last good values'}
        return render({**last,'_rank':rank,'_page':page,'_rank_page':rank_page},width=width or console.width,height=height or console.height,view=args.view)
    if args.once or not console.is_terminal:console.print(read());return 0
    with FixedTerminal(console) as live:
        try:
            while True:
                if os.name=='nt':
                    import msvcrt
                    if msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='q':break
                        rows=last.get('validation_ranking' if args.view=='validation' else 'top_strategies',[])
                        page_size=ranking_page_size(console.height,last)
                        if key in ('n','b'):rank_page=max(0,min(max(0,(len(rows)-1)//page_size),rank_page+(1 if key=='n' else -1)))
                        elif key in ('j','k'):rank=max(1,min(max(1,len(rows)),rank+(1 if key=='j' else -1)))
                        if key in 'fotpv':args.view={'f':'financial','o':'objective','t':'positions','p':'performance','v':'validation'}[key];page=0;rank_page=0;rank=1
                live.update(read);time.sleep(1)
        except KeyboardInterrupt:pass
    return 0


if __name__=='__main__':raise SystemExit(main())
