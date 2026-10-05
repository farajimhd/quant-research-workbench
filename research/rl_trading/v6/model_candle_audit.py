"""Bounded chart projection of the immutable model bank, never an indicator replay."""
from functools import lru_cache
from pathlib import Path

import numpy as np

from research.rl_trading.v1.common import digest
from research.rl_trading.v6.bank import open_bank
from research.rl_trading.v6.features import SCALAR_NAMES, LEVEL_NAMES


@lru_cache(maxsize=4)
def certified_bank(root, certificate_hash):
    from research.rl_trading.v6.saved_label_audit import read_json
    root = Path(root)
    proof = read_json(root/'complete.json', certificate_hash)
    plan = read_json(root/'plan.json')
    if plan['hash'] != proof['plan_hash'] or digest({k:v for k,v in plan.items() if k!='hash'}) != plan['hash']:
        raise ValueError('Model bank plan binding changed')
    # These immutable bytes were fully verified by generation/publication.
    # Open read-only maps; never transfer entire multi-GB banks to plot 240 rows.
    bank = open_bank(root/'bank', verify_hashes=False)
    if bank.manifest['source_hash'] != plan['hash'] or bank.manifest['files_sha256'] != proof['bank_file_hashes']:
        raise ValueError('Model bank byte receipts differ from publication')
    return bank


def select_window(current, previous=None, *, offset=0, start_us=None):
    """120 valid context + 120 valid session candles; also expose real input clocks.

    Input history counts all observed bank rows (including price-masked rows),
    strictly before each target. Plot counts deliberately count valid OHLC only.
    """
    def valid(item):
        return np.flatnonzero((item.scalar[:,35]==1)&(item.scalar[:,36]==1))
    indices = valid(current)
    if start_us is not None:
        offset = int(np.searchsorted(current.close_us[indices], start_us))
    offset = max(0, min(int(offset), max(0, len(indices)-1)))
    selected = indices[offset:offset+120]
    context = []
    for item, positions in ((previous, valid(previous) if previous is not None else []),
                            (current, indices[:offset])):
        context.extend((item,int(i)) for i in positions[-120:])
    context = context[-120:]
    plot = [(item,i,'context') for item,i in context]+[(current,int(i),'session') for i in selected]
    raw = []
    prior_clocks = previous.close_us[-120:] if previous is not None else np.array([], dtype=np.int64)
    for item,i,part in plot:
        history = np.concatenate((prior_clocks,current.close_us[max(0,i-120):i]))[-120:] if part=='session' else np.array([],dtype=np.int64)
        diagnostic = np.concatenate((prior_clocks,current.close_us[max(0,i-119):i+1]))[-120:] if part=='session' else np.array([],dtype=np.int64)
        raw.append(dict(time_us=int(item.close_us[i]),part=part,
            scalar=item.scalar[i].astype(float).tolist(),levels=item.levels[i].astype(float).tolist(),
            input_close_us=history.astype(int).tolist(),input_padding=120-len(history) if part=='session' else None,
            diagnostic_input_close_us=diagnostic.astype(int).tolist()))
    return raw, dict(mode='actual_candles',offset=offset,context_candles=len(context),
        session_candles=len(selected),total_session_candles=len(indices),
        previous_offset=max(0,offset-120),next_offset=offset+120,
        previous_available=offset>0,next_available=offset+120<len(indices),
        scalar_names=list(SCALAR_NAMES),level_names=list(LEVEL_NAMES),
        input_contract='Up to 120 actual bank rows with close_us < target close_us; invalid-price rows retain masks. Display uses valid-price candles.',
        diagnostic_contract='Existing ticker_resnet_data.prepare_windows includes the target candle (side=right); distinct from the main V6 strict-prior teacher path.',
        units='Stored float32 bank values before bps conversion and train-only normalization; plotted prices decode those stored values.')


def project(raw):
    candles=[]; overlay=[]; oscillator=[]
    decoded=[]
    for row in raw:
        s=row['scalar']; close=float(np.exp(s[3])); time=row['time_us']//1_000_000-1
        candles.append(dict(time=time,endTime=time+1,isClosed=True,
            **{name:float(np.exp(s[i])) for i,name in enumerate(('open','high','low','close'))}))
        decoded.append((row,s,close,time))
    for index,mask,column,label,color in ((4,6,'bar_vwap','Bar VWAP','var(--warning)'),(5,7,'session_vwap','Session VWAP','var(--info)')):
        overlay.append(dict(column=column,label=label,style='line',color=color,lineWidth=2,
            data=[dict(time=t,value=c*(1+s[index])) for r,s,c,t in decoded if s[mask]==1]))
    for side in range(2):
        for slot in range(5):
            for geometry,name in enumerate(('center','lower','upper')):
                column=f'v7_{side}_{slot}_{name}'
                overlay.append(dict(column=column,label=f'V7 {"below" if side==0 else "above"} {slot+1} {name}',
                    style='line',color='var(--success)' if side==0 else 'var(--danger)',lineWidth=1,
                    lineStyle='solid' if geometry==0 else 'dotted',defaultVisible=geometry==0,
                    data=[dict(time=t,value=c*(1+r['levels'][side][slot][geometry])) for r,s,c,t in decoded if r['levels'][side][slot][10]==1]))
    for index,column,label,color in ((14,'macd_line','MACD','var(--primary)'),(15,'macd_signal','Signal','var(--warning)'),(None,'macd_histogram','Histogram','var(--muted-foreground)')):
        oscillator.append(dict(column=column,label=label,paneKey='macd',style='histogram' if index is None else 'line',
            color=color,lineWidth=1,data=[dict(time=t,value=c*(s[14]-s[15] if index is None else s[index])) for r,s,c,t in decoded if s[20]==1]))
    return candles,overlay,oscillator
