"""Read-only terminal dashboard: one renderer, no experiment writes."""
import argparse,json,time,os
from pathlib import Path
from rich.console import Console
from rich.live import Live
from .dashboard import render

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true');p.add_argument('--view',choices=('financial','performance','objective','messages'),default='financial');args=p.parse_args(argv)
    console=Console(no_color=bool(os.environ.get('NO_COLOR')));last={};error=None;financial_page=0
    identity_path=args.output/'identity.json'
    # Older immutable workers do not emit mode. Derive display scope from their
    # retained launch identity without editing their status or experiment.
    profile=False
    if identity_path.exists():
        profile=bool(json.loads(identity_path.read_text(encoding='utf-8')).get('arguments',{}).get('profile',False))
    def read():
        nonlocal last,error
        try:last=json.loads((args.output/'status.json').read_text(encoding='utf-8'));error=None
        except (FileNotFoundError,PermissionError,json.JSONDecodeError) as e:error=f'Snapshot unavailable: {type(e).__name__}; retaining last good view'
        if error:last={**last,'waiting_reason':error}
        if profile:
            last={**last,'mode':'profile','focus':'Single training-session profile; validation SEALED'}
            if last.get('status')=='training':last['status']='profiling'
        return render({**last,'_financial_page':financial_page},width=console.width,height=console.height,view=args.view)
    if args.once or not console.is_terminal:
        console.print(read());return 0
    with Live(read(),console=console,refresh_per_second=1,screen=True) as live:
        try:
            while True:
                if os.name=='nt':
                    import msvcrt
                    if msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='f':financial_page=financial_page+1 if args.view=='financial' else 0
                        args.view={'f':'financial','p':'performance','c':'objective','m':'messages'}.get(key,args.view)
                live.update(read());time.sleep(1)
                if last.get('status') in ('completed','failed','interrupted','no_feasible_winner','profile_complete','awaiting_validation_inputs'):break
        except KeyboardInterrupt:pass
    return 0

if __name__=='__main__':raise SystemExit(main())
