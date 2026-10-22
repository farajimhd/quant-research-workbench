"""Read-only, stable progress terminal for the fundamentals controller."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,shutil,time
from pipelines.fundamentals.progress import render


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',required=True)
    p.add_argument('--once',action='store_true')
    p.add_argument('--campaign',help='Runtime JSON with ordered scope directories; one terminal follows every pass')
    a=p.parse_args();root=Path(a.run_dir).resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):raise ValueError('Runtime root unavailable or invalid')
    interactive=sys.stdout.isatty();last=None
    if interactive and os.name=='nt':
        import ctypes
        kernel=ctypes.windll.kernel32
        kernel.GetStdHandle.restype=ctypes.c_void_p
        handle=kernel.GetStdHandle(-11);mode=ctypes.c_ulong()
        interactive=bool(kernel.GetConsoleMode(ctypes.c_void_p(handle),ctypes.byref(mode))
            and kernel.SetConsoleMode(ctypes.c_void_p(handle),mode.value|4))
    if interactive:sys.stdout.write('\x1b[2J\x1b[H')
    try:
        while True:
            try:
                selected=root;heading='';footer=''
                if a.campaign:
                    manifest=Path(a.campaign).resolve()
                    if not manifest.is_relative_to(runtime):raise ValueError('Campaign manifest must be in runtime root')
                    campaign=json.loads(manifest.read_text());scopes=campaign['scopes']
                    if not 1<=len(scopes)<=8:raise ValueError('Campaign must contain 1..8 bounded scopes')
                    for index,scope in enumerate(scopes):
                        selected=Path(scope['run_dir']).resolve()
                        if not selected.is_relative_to(runtime):raise ValueError('Campaign scope escapes runtime root')
                        prior=json.loads((selected/'status.json').read_text()) if (selected/'status.json').exists() else {}
                        heading=f"Scope {index+1}/{len(scopes)}: {scope['label']} (ETA is for this scope)"
                        if prior.get('state')!='complete':break
                    footer=campaign.get('qualification_status','Qualification not yet established')
                state=json.loads((selected/'status.json').read_text(encoding='utf-8'))
                size=shutil.get_terminal_size((100,28));text=render(state,size.columns,size.lines-3)
                if heading:text=heading[:size.columns]+'\n'+text+'\n'+footer[:size.columns]
            except (FileNotFoundError,json.JSONDecodeError):
                text=(heading+'\n' if heading else '')+'Fundamentals | awaiting controller status\nNo completion or ETA is assumed.'
            if interactive:
                sys.stdout.write('\x1b[H'+text+'\x1b[J');sys.stdout.flush()
            elif text!=last:print(text,flush=True)
            last=text
            if a.once:return
            time.sleep(2 if interactive else 10)
    except KeyboardInterrupt:return


if __name__=='__main__':main()
