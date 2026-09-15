"""Plot canonical quotes and actual before/after fills for audited position windows."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
os.environ.setdefault('MPLCONFIGDIR','D:/TradingML/runtimes/tests/mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as md

NY=ZoneInfo('America/New_York')


def plot(root,variant):
    root=root.resolve();root.relative_to(Path('D:/TradingML/runtimes').resolve())
    study=Path('D:/TradingML/runtimes/analysis/strategy-222-parameter-study-20260914')
    ledger=json.loads((study/'quote-ledger.json').read_text())
    original=json.loads((study/'positions.json').read_text())
    trials=json.loads((root/'comparison.json').read_text())
    out=root/'plots'/variant;out.mkdir(parents=True,exist_ok=True)
    index=['# Before/after price-action review','',
           'Orange markers are the original Strategy 222 entries. Blue markers are actual candidate buys; red crosses are actual candidate sells. The gray band is the recorded bid–ask spread. Each chart is a frozen audit window, not a future-dependent strategy input.','']
    completed=0
    for key,item in ledger.items():
        window=item['window'];symbol=window['symbol']
        start=datetime.fromisoformat(window['start']);end=datetime.fromisoformat(window['end'])
        selected=[t for t in trials if t['name']==variant and t['symbol']==symbol and
            datetime.fromisoformat('2026-08-21T'+t['end']).replace(tzinfo=NY)>=end]
        if not selected:continue
        trial=selected[-1]
        data=np.load(study/f'quotes-{key}.npz')['data']
        # Display at most one quote per 100 ms; actual fill markers are never sampled.
        keep=np.r_[True,np.diff((data[:,0]/100000).astype(np.int64))!=0]
        data=data[keep]
        x=md.date2num([datetime.fromtimestamp(t/1e6,NY) for t in data[:,0]])
        fig,ax=plt.subplots(figsize=(15,5.5),layout='constrained')
        ax.fill_between(x,data[:,1],data[:,2],color='#64748b',alpha=.15,label='Bid–ask spread')
        ax.plot(x,(data[:,1]+data[:,2])/2,color='#334155',linewidth=.8,label='Quote midpoint')
        positions=[p for p in original if p['symbol']==symbol and start<=datetime.fromisoformat(p['at'])<=end]
        for i,p in enumerate(positions):
            at=md.date2num(datetime.fromisoformat(p['at']))
            ax.scatter([at],[p['entry']],s=65,color='#ea580c',marker='o',zorder=5,
                       label='Original entry' if i==0 else None)
            ax.annotate(f"#{p['n']}",(at,p['entry']),xytext=(3,13+12*(i%2)),textcoords='offset points',fontsize=8,color='#9a3412')
        fills=[f for e in trial['episodes'] for f in e['fills'] if start<=datetime.fromisoformat(f['time'])<=end]
        for side,marker,color,label in [('B','^','#2563eb','Candidate buy'),('S','x','#dc2626','Candidate sell')]:
            matching=[f for f in fills if f['side']==side]
            ax.scatter([md.date2num(datetime.fromisoformat(f['time'])) for f in matching],
                       [f['price'] for f in matching],s=32,marker=marker,color=color,zorder=6,label=label)
        ax.xaxis.set_major_locator(md.AutoDateLocator(minticks=5,maxticks=10))
        ax.xaxis.set_major_formatter(md.DateFormatter('%H:%M:%S',tz=NY))
        ax.set(xlabel='New York time',ylabel='Price ($)',title=f"{symbol} | {variant} | Original positions {', '.join('#'+str(p['n']) for p in positions)}")
        ax.grid(alpha=.18);ax.legend(loc='best',fontsize=8,ncol=4)
        path=out/f'{key}.png';fig.savefig(path,dpi=140);plt.close(fig)
        index.extend([f'## {symbol}: {start.astimezone(NY):%H:%M:%S}–{end.astimezone(NY):%H:%M:%S}','',
                      f"Run `{trial['run_id']}`. Original positions: {', '.join(str(p['n']) for p in positions)}.",'',
                      f'![{symbol} before and after]({path.as_posix()})',''])
        completed+=1
        print(f'Charts {completed}/{len(ledger)} windows: {symbol}',flush=True)
    (root/f'price-action-{variant}.md').write_text('\n'.join(index)+'\n',encoding='utf-8')
    print(f'Completed {completed} comparable windows; {len(ledger)-completed} await replay coverage',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,default=Path('D:/TradingML/runtimes/analysis/strategy-222-refinement'))
    parser.add_argument('--variant',default='full-v6')
    args=parser.parse_args();plot(args.runtime,args.variant)
