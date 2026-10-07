"""Read-only fixed-screen observer. Never touches campaign ownership or CUDA."""
import argparse,json,time,os
from pathlib import Path
from rich.console import Console
from rich.live import Live
from .staged_dashboard import render

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true')
    p.add_argument('--view',choices=('financial','objective','positions','performance'),default='financial');args=p.parse_args(argv)
    console=Console(no_color=bool(os.environ.get('NO_COLOR')));last={};rank=1;page=0
    def read():
        nonlocal last
        try:last=json.loads((args.output/'status.json').read_text())
        except (OSError,json.JSONDecodeError):last={**last,'error':'Snapshot unavailable; retaining last good values'}
        return render({**last,'_rank':rank,'_page':page},width=console.width,height=console.height,view=args.view)
    if args.once or not console.is_terminal:console.print(read());return 0
    with Live(read(),console=console,screen=True,refresh_per_second=1) as live:
        try:
            while True:
                if os.name=='nt':
                    import msvcrt
                    if msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='q':break
                        if key in '123':rank=int(key);page=0
                        if key=='n':page+=1
                        if key in 'fotpc':args.view={'f':'financial','o':'objective','c':'objective','t':'positions','p':'performance'}[key];page=0
                live.update(read());time.sleep(1)
        except KeyboardInterrupt:pass
    return 0

if __name__=='__main__':raise SystemExit(main())
