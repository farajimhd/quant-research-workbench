"""Reproducible human-readable summaries from immutable diagnostic sums."""
import argparse,json
from pathlib import Path
import numpy as np
from ..v5.shards.phase1_contract import CHANNELS


def report(path):
    result=json.loads(path.read_text());lines=['# Phase1 reconstruction audit','',
        f"Complete: {result['complete']}; windows: {result['windows']:,}; seconds: {result['seconds']:.1f}",'',
        'Targets are the same input bars, not future forecasts. Direction uses exact zero as its third class. Masked pair metrics require both endpoints hidden. Oracle results replace only the reference with true visible-input references; diagnostic only.','']
    summary={}
    for mode,r in result['metrics'].items():
        lines += [f'## {mode}','', '| Timeframe | Close MAE $ | Oracle MAE $ | Signed close error % | Signed reference error % | Reference absolute error % | Last-close signed error % |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        raw=np.array(r['raw']);ref=np.array(r['references']);last=np.array(r['moves']['last_close_offset']).sum(2)
        for f,name in enumerate(r['timeframes']):
            q=raw[f,3];z=ref[f,0];offset=last[f,0]
            def div(a,b):return float(a/b) if b else None
            summary[f'{mode}/{name}']=dict(close_mae=div(q[2],q[0]),oracle_close_mae=div(q[3],q[0]),signed_close_pct=div(q[4],q[5]),
                signed_reference_pct=div(z[1],z[0]),reference_mae_pct=div(z[2],z[0]),last_signed_pct=div(offset[5],offset[0]))
            vals=summary[f'{mode}/{name}'];lines.append('| '+name+' | '+' | '.join(f'{x:.6f}' if x is not None else 'N/A' for x in vals.values())+' |')
        lines+=['','### Direction and move-range classification','',
                '| Timeframe | Family | Definition | Count | Direction % | Moving-only direction % | Signed-bin accuracy % | Magnitude-bin accuracy % | Move/range MAE pp |','|---|---|---|---:|---:|---:|---:|---:|---:|']
        for definition,table in r['moves'].items():
            if 'DIAGNOSTIC' in definition or definition=='last_close_offset':continue
            table=np.array(table);conf=np.array(r['confusion'][definition])
            mag=np.array([0,1,2,3,4,5,1,2,3,4,5]);sign=np.array([0,-1,-1,-1,-1,-1,1,1,1,1,1])
            for f,name in enumerate(r['timeframes']):
                for c,family in enumerate(r['families']):
                    t=table[f,c];m=conf[f,c];n=t[:,0].sum();moving=t[1:,0].sum()
                    if not n:continue
                    direction=m[sign[:,None]==sign[None,:]].sum()/n*100
                    mov=t[1:,3].sum()/moving*100 if moving else None
                    accuracy=np.trace(m)/n*100;mag_accuracy=m[mag[:,None]==mag[None,:]].sum()/n*100
                    mae=t[:,1].sum()/n
                    summary[f'{mode}/{name}/{family}/{definition}']=dict(count=int(n),direction_pct=direction,moving_direction_pct=mov,signed_bin_accuracy_pct=accuracy,magnitude_bin_accuracy_pct=mag_accuracy,mae_percentage_points=mae)
                    lines.append(f'| {name} | {family} | {definition} | {int(n)} | {direction:.3f} | '+(f'{mov:.3f}' if mov is not None else 'N/A')+f' | {accuracy:.3f} | {mag_accuracy:.3f} | {mae:.6f} |')
        lines+=['','### All18 native-unit MAE','', '| Timeframe | Channel | Count | MAE | Signed error | Oracle MAE |','|---|---|---:|---:|---:|---:|']
        for f,name in enumerate(r['timeframes']):
            for c,channel in enumerate(CHANNELS):
                q=raw[f,c]
                if q[0]:lines.append(f'| {name} | {channel} | {int(q[0])} | {q[2]/q[0]:.6f} | {q[1]/q[0]:.6f} | {q[3]/q[0]:.6f} |')
    path.with_name('summary.json').write_text(json.dumps(summary,indent=2))
    path.with_name('report.md').write_text('\n'.join(lines)+'\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('results',type=Path);a=p.parse_args();report(a.results)
