"""Read-only V13 terminal with training and display-only validation views."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
from rich.console import Console
from .fixed_terminal import FixedTerminal
from .terminal import render, ranking_page_size
from research.vectorized_backtest.v6.torch_backtest.staged_dashboard import navigate


def maximize_window():
    """Target this observer's visible host, including Windows Terminal hosts."""
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.windll.kernel32;user=ctypes.windll.user32
    title=f'V13 optimization observer [{os.getpid()}]'
    kernel.SetConsoleTitleW(title)
    kernel.GetConsoleWindow.restype=wintypes.HWND
    user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
    user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.IsWindowVisible.argtypes=[wintypes.HWND]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    @callback_type
    def visit(window,param):
        text=ctypes.create_unicode_buffer(1024)
        user.GetWindowTextW(window,text,len(text))
        if title in text.value and user.IsWindowVisible(window):user.ShowWindow(window,3)
        return True
    window=kernel.GetConsoleWindow()
    if window:user.ShowWindow(window,3)
    # The host applies console titles asynchronously.
    for _ in range(5):
        user.EnumWindows(visit,0);time.sleep(.1)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true')
    p.add_argument('--view',choices=('financial','objective','positions','performance','validation'),default='financial')
    args=p.parse_args(argv);console=Console(no_color=bool(os.environ.get('NO_COLOR')))
    if console.is_terminal and not args.once and os.name=='nt':
        maximize_window()
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
                        rows=last.get('validation_ranking' if args.view=='validation' else 'session_ranking',[])
                        page_size=ranking_page_size(console.height,last)
                        if key in ('n','b'):rank_page=max(0,min(max(0,(len(rows)-1)//page_size),rank_page+(1 if key=='n' else -1)))
                        elif key in ('j','k'):rank=max(1,min(max(1,len(rows)),rank+(1 if key=='j' else -1)))
                        if key in 'fotpv':args.view={'f':'financial','o':'objective','t':'positions','p':'performance','v':'validation'}[key];page=0;rank_page=0;rank=1
                live.update(read);time.sleep(1)
        except KeyboardInterrupt:pass
    return 0


if __name__=='__main__':raise SystemExit(main())
