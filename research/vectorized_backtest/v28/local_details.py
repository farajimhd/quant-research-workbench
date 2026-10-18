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
    import sys
    canvas.DetailObserver=DetailObserver
    canvas.rule_lines=rule_lines
    canvas.session_layout=session_layout
    if '--active' not in sys.argv:
        sys.argv.extend(['--active','D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v28/active-campaign.json'])
    return canvas.main()


if __name__=='__main__':raise SystemExit(main())
