"""Read-only V48 progress, with independent global profit and case-score sorting."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from pathlib import Path
from research.vectorized_backtest.v42 import local_details as ui
from .progress_view import install
install(ui)
canvas=ui.canvas
DetailObserver=ui.DetailObserver
def rule_lines(payload,stage):
    if payload and payload.get('version')=='v48-whole-v1':
        payload=dict(payload,version='v42-recurrent-v1')
    lines=ui.rule_lines(payload,stage)
    if payload:
        from .genome import INPUTS
        for i,node in enumerate(payload['networks'][stage]['nodes']):
            op=node.get('op');refs=[f'n{x-INPUTS}' for x in node['sources']]
            if op=='decision':lines[i]=f"n{i} = known({refs[0]}) AND {refs[0]} > {node['weights'][0]:.5g} AND age >= {round(node['bias']*3600)}s"
            elif op=='linear':lines[i]=f"n{i} = {node['weights'][0]:.5g} * {refs[0]} + {node['bias']:.5g}"
            elif op in ('whole_or','whole_and','whole_xor'):lines[i]=f"n{i} = ({refs[0]} > 0.5) {op[6:].upper()} ({refs[1]} > 0.5)"
            elif op=='whole_size':lines[i]=f"n{i} = sole firing branch size logit; max({refs[2]}, {refs[3]}) if both fire"
    return lines
detail_lines=ui.detail_lines
render=ui.render
_original_layout=ui.session_layout

def session_layout(data,width,height):
    from rich.text import Text
    layout=_original_layout(data,width,height)
    p=data.get('session_progress',{})
    layout['header'].update(Text(f"V48 | AUG 13 | PHASE {p.get('phase','?')} | GENERATION {p.get('generation',data.get('generation',0))}/{data.get('target',30)} | {data.get('generation',0)} SEALED\n{p.get('stage','')} | BID/ASK | 25% WHOLE-NETWORK | LOSS 0.5 | read-only",style='cyan'))
    return layout

def main(argv=None):
    import argparse,time
    from rich.console import Console
    from contextlib import nullcontext
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--active',type=Path,default=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v48/active-test.json'))
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args(argv)
    canvas.rule_lines=rule_lines
    canvas.detail_lines=detail_lines
    canvas.session_layout=session_layout
    console=Console();once=args.once or not console.is_terminal
    observer=DetailObserver(args.active).start()
    selection=0;mode='profit';view='financial';stage='entry';page=0;switch=None
    # Keep window placement, focus, size and console hosting under user control.
    try:
        with nullcontext() if once else canvas.FixedTerminal(console) as live:
            while True:
                data=observer.read()
                if not data:time.sleep(.1);continue
                if data.get('global_output'):
                    switch=Path(data['global_output']);break
                if once:
                    console.print(render(data,console.width,console.height,mode,selection,view,stage,page));break
                live.update(lambda w,h:render(data,w,h,mode,selection,view,stage,page))
                if os.name=='nt':
                    import msvcrt
                    while msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='q':return 0
                        if key=='h':view={'financial':'families_initial','families_initial':'families_current','families_current':'financial'}.get(view,'families_initial')
                        if key in '[]sw' and data.get('days'):
                            days=data['days'];index=days.index(data['day']);observer.select(days[(index+(1 if key in ']s' else -1))%len(days)]);selection=0;page=0
                        elif key=='v':observer.select(None);selection=0;page=0
                        if key in '12345':mode={'1':'profit','2':'score','3':'win','4':'risk','5':'specialist'}[key];selection=0
                        elif key in ('j','k','n','b'):
                            step=1 if key in ('j','k') else canvas.ranking_size(live.dimensions()[1])
                            selection=max(0,min(max(0,len(data.get('rows',[]))-1),selection+(step if key in ('j','n') else -step)));page=0
                        elif key in ('f','o','p'):view={'f':'financial','o':'objective','p':'policy'}[key];page=0
                        elif key in 'exadt':view='rules';stage={'e':'entry','x':'exit','a':'add','d':'reduce','t':'trail'}[key];page=0
                        elif key in ('u','i'):page=max(0,page+(1 if key=='i' else -1))
                time.sleep(1)
    except KeyboardInterrupt:pass
    finally:observer.close()
    if switch:
        from .observe import main as global_panel
        return global_panel(['--output',str(switch)]+(['--once'] if once else []))
    return 0


if __name__=='__main__':raise SystemExit(main())
