"""Read-only V28 Phase 1 observer using the V27 detailed terminal canvas."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from pathlib import Path
from research.vectorized_backtest.v27 import local_details as canvas


class DetailObserver(canvas.DetailObserver):
    def refresh(self):
        super().refresh()
        with self.lock:
            data=self.data
            if data and not (Path(data['root'])/'status.json').exists():
                lane=data.get('session_progress',{})
                done=data.get('generation',0)
                data.update(status=lane.get('status','starting'),stage=lane.get('stage',''),
                            completed_sessions=int(done>=data.get('target',30)),
                            workers={data['day']:lane},updated_epoch=lane.get('updated_epoch'))


def rule_lines(payload,stage):
    if payload is None:return ['Network unavailable in the retained checkpoint.']
    from .features import CATALOG
    from .config import STATES
    inputs=len(CATALOG)+len(STATES)
    def name(source):
        if source<len(CATALOG):return CATALOG[source].name
        if source<inputs:return str(STATES[source-len(CATALOG)])
        return f'n{source-inputs}'
    network=payload['networks'][stage]
    lines=[]
    for i,node in enumerate(network['nodes']):
        terms=[f'{weight:.5g} * {name(source)}' for source,weight in zip(node['sources'],node['weights'])]
        lines.append(f"n{i} = tanh({' + '.join(terms)} + {node['bias']:.5g})")
    lines.append(f"SIGNAL: n{len(network['nodes'])-1} > {network['threshold']:.5g} | minimum age {payload.get('minimum_age',{}).get(stage,0)} seconds")
    return lines


_session_layout=canvas.session_layout
def session_layout(data,width,height):
    layout=_session_layout(data,width,height)
    from rich.text import Text
    header=layout['header'].renderable
    layout['header'].update(Text(header.plain.replace('V27 PHASE 1','V28 PHASE 1'),style='cyan'))
    return layout


def main():
    import argparse,time
    from rich.console import Console
    from contextlib import nullcontext
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--active',type=Path,default=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v28/active-campaign.json'))
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    canvas.rule_lines=rule_lines
    canvas.session_layout=session_layout
    console=Console();once=args.once or not console.is_terminal
    observer=DetailObserver(args.active).start()
    selection=0;mode='profit';view='financial';stage='entry';page=0
    # Keep window placement, focus, size and console hosting under user control.
    try:
        with nullcontext() if once else canvas.FixedTerminal(console) as live:
            while True:
                data=observer.read()
                if not data:time.sleep(.1);continue
                if once:
                    console.print(canvas.render(data,console.width,console.height,mode,selection,view,stage,page));break
                live.update(lambda w,h:canvas.render(data,w,h,mode,selection,view,stage,page))
                if os.name=='nt':
                    import msvcrt
                    while msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='q':return 0
                        if key in '1234':mode={'1':'profit','2':'score','3':'win','4':'risk'}[key];selection=0
                        elif key in ('j','k','n','b'):
                            step=1 if key in ('j','k') else canvas.ranking_size(live.dimensions()[1])
                            selection=max(0,min(max(0,len(data.get('rows',[]))-1),selection+(step if key in ('j','n') else -step)));page=0
                        elif key in ('f','o','p'):view={'f':'financial','o':'objective','p':'policy'}[key];page=0
                        elif key in 'exadt':view='rules';stage={'e':'entry','x':'exit','a':'add','d':'reduce','t':'trail'}[key];page=0
                        elif key in ('u','i'):page=max(0,page+(1 if key=='i' else -1))
                time.sleep(1)
    except KeyboardInterrupt:pass
    finally:observer.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
