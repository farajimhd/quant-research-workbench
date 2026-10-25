"""Read-only V49 owned-ticker and all-ticker score explorer."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from pathlib import Path
from research.vectorized_backtest.v42 import local_details as ui
from .observer_model import Observer,SORTS
from .progress_view import render,visible,page_size
canvas=ui.canvas

def rule_lines(payload,stage):
    if payload and payload.get('version')=='v49-whole-v1':
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

class Controls:
    def __init__(self):
        self.settings=dict(case=None,sort='score',eligible=False,active=False,grouped=False,reverse=False)
        self.selection=0;self.detail='financial';self.stage='entry';self.page=0;self.quit=False

    def key(self,key,data,height=38):
        key=key.lower();settings=self.settings
        if key=='q':self.quit=True;return
        if key in SORTS:settings['sort']=SORTS[key][0]
        elif key=='0':settings['case']=None
        elif key=='g':settings['grouped']=not settings['grouped'] if data.get('phase')==0 else False
        elif key=='z':settings['eligible']=not settings['eligible']
        elif key=='c':settings['active']=not settings['active']
        elif key=='v':settings['reverse']=not settings['reverse']
        elif key in '[]':
            cases=sorted({r['case_owner'] for r in data.get('rows',[]) if r['case_owner'] is not None},key=lambda c:(data.get('case_labels',{}).get(c,c),c))
            if cases:
                index=cases.index(settings['case']) if settings['case'] in cases else (-1 if key==']' else 0)
                settings['case']=cases[(index+(1 if key==']' else -1))%len(cases)];settings['grouped']=False
        elif key in ('\r','\n') and settings['grouped']:
            rows=visible(data,settings)
            if rows:settings['case']=rows[min(self.selection,len(rows)-1)]['case_owner'];settings['grouped']=False
        elif key in ('j','k','n','b'):
            rows=visible(data,settings);step=1 if key in ('j','k') else page_size(height)
            self.selection=max(0,min(max(0,len(rows)-1),self.selection+(step if key in ('j','n') else -step)));return
        elif key in ('f','o','p'):self.detail={'f':'financial','o':'objective','p':'policy'}[key]
        elif key in 'exadt':self.detail='rules';self.stage={'e':'entry','x':'exit','a':'add','d':'reduce','t':'trail'}[key]
        elif key in ('u','i'):self.page=max(0,self.page+(1 if key=='i' else -1));return
        else:return
        self.selection=0;self.page=0


def main(argv=None):
    import argparse,time
    from rich.console import Console
    from contextlib import nullcontext
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--active',type=Path,default=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v49/active-test.json'))
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--width',type=int,help='Fixed width for a redirected snapshot')
    parser.add_argument('--height',type=int,help='Fixed height for a redirected snapshot')
    parser.add_argument('--ticker',help='Initial owned ticker symbol, such as XHG')
    parser.add_argument('--groups',action='store_true',help='Initial per-ticker summary')
    parser.add_argument('--sort',choices=[x[0] for x in SORTS.values()],default='score')
    args=parser.parse_args(argv)
    console=Console(width=args.width,height=args.height);once=args.once or not console.is_terminal
    observer=Observer(args.active).start();controls=Controls();controls.settings.update(grouped=args.groups,sort=args.sort)
    phase=None;initial_ticker=args.ticker;started=time.monotonic()
    try:
        with nullcontext() if once else canvas.FixedTerminal(console) as live:
            while True:
                data=observer.read()
                if not data or 'phase' not in data:time.sleep(.1);continue
                if once and not data.get('display_ready',False):
                    if time.monotonic()-started>30:raise RuntimeError(data.get('display_error','Display did not finish loading'))
                    time.sleep(.1);continue
                if phase is not None and phase!=data['phase']:
                    controls.settings.update(case=None,grouped=False);controls.selection=0
                phase=data['phase']
                if initial_ticker:
                    matches=[case for case,ticker in data.get('case_labels',{}).items() if initial_ticker.upper() in ticker.upper().split('/')]
                    if matches:controls.settings['case']=sorted(matches)[0]
                    elif once:raise ValueError('Ticker not found in the certified session map')
                    initial_ticker=None
                draw=lambda w,h:render(data,w,h,controls.settings,controls.selection,controls.detail,controls.stage,controls.page,rule_lines)
                if once:console.print(draw(console.width,console.height));break
                live.update(draw)
                if os.name=='nt':
                    import msvcrt
                    while msvcrt.kbhit():
                        key=msvcrt.getwch()
                        if key in ('\x00','\xe0'):
                            if msvcrt.kbhit():msvcrt.getwch()
                            continue
                        controls.key(key,data,live.dimensions()[1])
                if controls.quit:break
                time.sleep(.5)
    except KeyboardInterrupt:pass
    finally:observer.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
