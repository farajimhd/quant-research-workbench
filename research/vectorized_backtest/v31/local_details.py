"""Read-only V31 Phase 1 observer using the V27 detailed terminal canvas."""
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
    revised=payload.get('version','').startswith('v31-')
    market_inputs=(3 if revised else 1)*len(CATALOG)
    inputs=market_inputs+len(STATES)
    def name(source):
        if source<0:return f'n{-source-1}[previous clock]'
        if source<market_inputs:
            prefix=('absolute','cross_sectional','validity')[source//len(CATALOG)] if revised else 'legacy_scaled'
            return prefix+':'+CATALOG[source%len(CATALOG)].name
        if source<inputs:return str(STATES[source-market_inputs])
        return f'n{source-inputs}'
    network=payload['networks'][stage]
    lines=[]
    for i,node in enumerate(network['nodes']):
        terms=[f'{weight:.5g} * {name(source)}' for source,weight in zip(node['sources'],node['weights'])]
        op=node.get('op','tanh');b=f"{node['bias']:.5g}"
        if op in ('tanh','conv'):expression=f"tanh({b} + {' + '.join(terms)})"
        elif op=='identity':expression=f"clip({b} + {' + '.join(terms)}, -1, 1)"
        elif op=='gate':expression=f"sigmoid({terms[0]}) * tanh({b} + {' + '.join(terms[1:]) or '0'})"
        elif op=='attention':expression=f"tanh({b} + attention(query={terms[0]}; key,value pairs={'; '.join(', '.join(terms[j:j+2]) for j in range(1,len(terms),2))}))"
        else:expression=f"tanh({b} + {op}({', '.join(terms)}))"
        lines.append(f"n{i} = {expression}")
    lines.append(f"SIGNAL: n{len(network['nodes'])-1} > {network['threshold']:.5g} | minimum age {payload.get('minimum_age',{}).get(stage,0)} seconds")
    if stage in ('entry','add','reduce'):
        head=network.get('size_source',-1);head=head if head>=0 else len(network['nodes'])-1
        lines.append(f"SIZE FRACTION = (tanh({network.get('size_weight',1):.5g} * n{head} + {network.get('size_bias',0):.5g}) + 1) / 2")
    return lines


_session_layout=canvas.session_layout
_detail_lines=canvas.detail_lines

def detail_lines(row,payload,view,stage):
    lines=_detail_lines(row,payload,view,stage)
    if row is not None and payload is not None and view=='policy':
        lines[1]=f"Target: +{100*payload['policy']['target_fraction']:.3f}% | target-hit reduction {100*payload['policy']['reduce_fraction']:.3f}%"
        lines[2]=f"Entry: neural fraction x $1000 cap | add: neural fraction x $200 cap, maximum {payload['policy']['maximum_adds']} additions"
        lines.append('Signal-driven reduction: independent neural fraction. Prior requests and actual fills feed the next clock.')
    return lines
def session_layout(data,width,height):
    layout=_session_layout(data,width,height)
    from rich.text import Text
    header=layout['header'].renderable
    layout['header'].update(Text(header.plain.replace('V27 PHASE 1','V31 PHASE 1'),style='cyan'))
    return layout


def main(argv=None):
    import argparse,time
    from rich.console import Console
    from contextlib import nullcontext
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--active',type=Path,default=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v31/active-campaign.json'))
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
                    console.print(canvas.render(data,console.width,console.height,mode,selection,view,stage,page));break
                live.update(lambda w,h:canvas.render(data,w,h,mode,selection,view,stage,page))
                if os.name=='nt':
                    import msvcrt
                    while msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='q':return 0
                        if key in '[]sw' and data.get('days'):
                            days=data['days'];index=days.index(data['day']);observer.select(days[(index+(1 if key in ']s' else -1))%len(days)]);selection=0;page=0
                        elif key=='v':observer.select(None);selection=0;page=0
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
    if switch:
        from .observe import main as global_panel
        return global_panel(['--output',str(switch)]+(['--once'] if once else []))
    return 0


if __name__=='__main__':raise SystemExit(main())
