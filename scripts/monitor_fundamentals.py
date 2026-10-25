"""Read-only, stable progress terminal for the fundamentals controller."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,shutil,time
from pipelines.fundamentals.progress import render


def snapshot(root,width,height):
    """A transient status read failure must not terminate the read-only panel."""
    try:
        state=json.loads((root/'status.json').read_text(encoding='utf-8'))
        if state.get('child_run_dir'):
            child=Path(state['child_run_dir']).resolve()
            if not child.is_relative_to(root.resolve()):raise ValueError('Recovery monitor child escapes parent runtime')
            state=json.loads((child/'status.json').read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as error:
        return ('Fundamentals | STATUS UNAVAILABLE\n'
            'Controller health is unknown; retrying status read.\n'
            f'{type(error).__name__}: {error}\n'
            'No completion or ETA is assumed.')
    return render(state,width,height)


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
                size=shutil.get_terminal_size((100,28));text=snapshot(selected,size.columns,size.lines-3)
                if heading:text=heading[:size.columns]+'\n'+text+'\n'+footer[:size.columns]
            except (OSError,json.JSONDecodeError) as error:
                text=(heading+'\n' if heading else '')+f'Fundamentals | STATUS UNAVAILABLE: {type(error).__name__}\nNo completion or ETA is assumed.'
            if interactive:
                sys.stdout.write('\x1b[H'+text+'\x1b[J');sys.stdout.flush()
            elif text!=last:print(text,flush=True)
            last=text
            if a.once:return
            time.sleep(2 if interactive else 10)
    except KeyboardInterrupt:return


if __name__=='__main__':main()
