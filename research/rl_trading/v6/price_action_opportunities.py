"""V2: episode-local swing opportunity bands, separate from selected references.

ENTRY quality normalizes discounted future gain within the same S->L pair.
EXIT quality normalizes current gain from its reference entry by the best
subsequent L gain. Neither includes accumulated future trades. No fill model.
"""
from dataclasses import asdict, dataclass
from functools import lru_cache
import json
from pathlib import Path
import time

import numpy as np
import polars as pl

from research.mlops.manifest import write_run_manifest
from research.rl_trading.v6 import label_audit as audit
from research.rl_trading.v6 import price_action_labels as legacy

VERSION = 'price-action-long-opportunities-v2'
OUTPUT = Path('D:/TradingML/runtimes/rl-v6-price-action-long-v2/NVDA/2026-07-31-r2')
DAY, TICKER = legacy.DAY, legacy.TICKER


@dataclass(frozen=True)
class Config(legacy.Config):
    quality_threshold: float = .9

    def validate(self):
        super().validate()
        if not np.isfinite(self.quality_threshold) or not 0 < self.quality_threshold <= 1:
            raise ValueError('Opportunity threshold must be in (0, 1]')


def classify(frame, threshold=.9, view='combined'):
    """One marker per candle; preserve independent alternatives in source fields.

    Combined favors EXIT if both alternatives qualify, exposed as a conflict
    count. Flat and held views allow inspection without suppressing alternatives.
    HOLD applies only within the reference position, WAIT outside that context.
    """
    Config(quality_threshold=threshold).validate()
    if view not in ('combined','flat','held','reference'):
        raise ValueError('Invalid opportunity view')
    entry = (pl.col('entry_gain') > 0) & (pl.col('entry_quality') >= threshold)
    exit_ = (pl.col('exit_gain') > 0) & (pl.col('exit_quality') >= threshold)
    context = pl.col('in_reference_hold')
    if view == 'flat':
        action = pl.when(entry).then(pl.lit('ENTRY')).otherwise(pl.lit('WAIT'))
    elif view == 'held':
        action = pl.when(exit_).then(pl.lit('EXIT')).when(context).then(pl.lit('HOLD')).otherwise(pl.lit('WAIT'))
    elif view == 'reference':
        action = pl.col('reference_action')
    else:
        action = pl.when(exit_).then(pl.lit('EXIT')).when(entry).then(pl.lit('ENTRY')).when(context).then(pl.lit('HOLD')).otherwise(pl.lit('WAIT'))
    result = frame.with_columns(action.alias('action'), (entry & exit_).fill_null(False).alias('both_opportunities'))
    return result.with_columns(pl.when(pl.col('action') == 'ENTRY').then(pl.col('entry_quality'))
        .when(pl.col('action') == 'EXIT').then(pl.col('exit_quality'))
        .when(pl.col('action') == 'HOLD').then(1-pl.col('exit_quality').fill_null(0))
        .otherwise(1-pl.col('entry_quality')).alias('label_value'))


def calculate(bars, config=Config()):
    """O(sum(pair_length^2)) comparisons with O(N) retained arrays.

    Reference entry maximizes a later L close gain discounted to that entry.
    Its reference exit is the highest strictly subsequent L close. Every candle
    keeps both conditional scores; the reference ledger has <=1 trade per pair.
    """
    config.validate()
    frame, episodes, pairs = legacy.episode_geometry(bars,config)
    times, prices = frame['time_us'].to_numpy(), frame['close'].to_numpy()
    directions = frame['direction'].to_numpy()
    n = len(prices)
    pair_ids = np.zeros(n,dtype=np.int64)
    entry_gain, entry_quality = np.zeros(n), np.zeros(n)
    exit_gain, exit_quality = np.full(n,np.nan), np.full(n,np.nan)
    basis = np.full(n,np.nan)
    held = np.zeros(n,dtype=bool)
    reference_actions = np.full(n,'WAIT',dtype='<U5')
    trades = []
    for pair in pairs:
        left, right = np.searchsorted(times, [pair['start_us'], pair['end_us']])
        indexes = np.arange(left, right)
        long_indexes = indexes[directions[indexes] == 1]
        pair_ids[indexes] = pair['pair_id']
        discounted_exits = {}
        for i in indexes:
            future = long_indexes[long_indexes > i]
            if not len(future):
                continue
            scores = (prices[future]-prices[i])*np.exp2(-(times[future]-times[i])/1e6/config.half_life_seconds)
            value = float(scores.max())
            if value > 0:
                entry_gain[i] = value
                discounted_exits[i] = int(future[np.flatnonzero(scores == value)[-1]])
        best = float(entry_gain[indexes].max())
        pair['best_entry_gain'] = best
        pair['best_entry_gain_at_pair_start'] = float((entry_gain[indexes]*np.exp2(-(times[indexes]-pair['start_us'])/1e6/config.half_life_seconds)).max())
        pair['reference_entry_us'] = pair['reference_exit_us'] = None
        pair['reference_entry_price'] = pair['reference_exit_price'] = None
        pair['best_exit_gain'] = 0.
        pair['best_discounted_exit_us'] = None
        if best <= 0:
            continue
        entry_quality[indexes] = entry_gain[indexes]/best
        # Earliest equal best entry: deterministic plateau membership.
        i = int(indexes[np.flatnonzero(entry_gain[indexes] == best)[0]])
        future = long_indexes[long_indexes > i]
        j = int(future[np.argmax(prices[future])])
        pnl = float(prices[j]-prices[i])
        assert pnl > 0 and j > i
        valid_exits = indexes[(indexes > i) & (directions[indexes] == 1)]
        exit_gain[valid_exits] = prices[valid_exits]-prices[i]
        exit_quality[valid_exits] = np.clip(exit_gain[valid_exits]/pnl,0,1)
        basis[indexes[indexes > i]] = prices[i]
        held[i+1:j] = True
        reference_actions[i], reference_actions[j] = 'ENTRY','EXIT'
        pair.update(reference_entry_us=int(times[i]),reference_exit_us=int(times[j]),
            reference_entry_price=float(prices[i]),reference_exit_price=float(prices[j]),
            best_exit_gain=pnl,best_discounted_exit_us=int(times[discounted_exits[i]]))
        trades.append(dict(pair_id=pair['pair_id'],entry_us=int(times[i]),exit_us=int(times[j]),
            entry_price=float(prices[i]),exit_price=float(prices[j]),price_pnl=pnl,
            hold_seconds=float(times[j]-times[i])/1e6))
    reference_actions[(reference_actions == 'WAIT') & held] = 'HOLD'
    # Carry the next best opportunity backward for comparison, without adding
    # the sum of subsequent trades to any displayed local quality/value.
    carried = np.zeros(n)
    next_start, next_best = None, 0.
    for pair in reversed(pairs):
        left, right = np.searchsorted(times, [pair['start_us'], pair['end_us']])
        indexes = np.arange(left, right)
        if next_start is not None:
            carried[indexes] = next_best*np.exp2(-(next_start-times[indexes])/1e6/config.half_life_seconds)
        pair['carried_next_pair_value'] = float(carried[indexes[0]]) if len(indexes) else 0.
        pair['best_available_opportunity_value'] = max(pair['best_entry_gain_at_pair_start'],pair['carried_next_pair_value'])
        next_start, next_best = pair['start_us'], pair['best_available_opportunity_value']
    def nullable(values):
        return [None if np.isnan(v) else float(v) for v in values]
    labels = frame.with_columns(pl.Series('pair_id',pair_ids),pl.Series('entry_gain',entry_gain),
        pl.Series('entry_quality',entry_quality),pl.Series('exit_gain',nullable(exit_gain),dtype=pl.Float64),
        pl.Series('exit_quality',nullable(exit_quality),dtype=pl.Float64),
        pl.Series('entry_basis',nullable(basis),dtype=pl.Float64),pl.Series('in_reference_hold',held),
        pl.Series('reference_action',reference_actions),pl.Series('carried_next_pair_value',carried))
    labels = classify(labels,config.quality_threshold)
    trade_frame = pl.DataFrame(trades,schema=dict(pair_id=pl.Int64,entry_us=pl.Int64,exit_us=pl.Int64,
        entry_price=pl.Float64,exit_price=pl.Float64,price_pnl=pl.Float64,hold_seconds=pl.Float64))
    return labels,episodes,pl.DataFrame(pairs),trade_frame


def build(output=OUTPUT, config=Config()):
    config.validate()
    output = Path(output).resolve()
    if not output.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError('Experiment output must remain under runtime root')
    if (output/'complete.json').exists():
        raise ValueError('Completed product exists; choose a new output directory')
    started = time.perf_counter()
    parent, original = legacy.product()
    bars = original['labels'].select('time_us','open','high','low','close','macd_line','macd_signal')
    products = dict(zip(['labels','episodes','pairs','trades'],calculate(bars,config)))
    output.mkdir(parents=True,exist_ok=True)
    files = {}
    for name,frame in products.items():
        path = output/(name+'.parquet'); frame.write_parquet(path)
        files[name] = dict(rows=frame.height,sha256=audit.file_hash(path))
    repo = Path(__file__).resolve().parents[3]
    write_run_manifest(output/'manifest.json',repo_root=repo,model_family='rl_trading',version=VERSION,
        job_type='experimental_swing_opportunity_labels',run_name='NVDA-2026-07-31-long-opportunities',
        args=dict(day=DAY,ticker=TICKER,**asdict(config)),config=asdict(config),
        data_roots=dict(certified_decoded_parent=str(legacy.OUTPUT)),output_root=output,secret_keys=())
    manifest = json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    manifest['producer_files_sha256'] = {p.relative_to(repo).as_posix():audit.file_hash(p) for p in [Path(__file__),Path(legacy.__file__)]}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    labels,trades = products['labels'],products['trades']
    proof = {**{k:parent[k] for k in ['day','ticker','listing_id','session','begin_us','finish_us','price_source','source_bank_certificate_sha256','source_files_sha256','consumed_activity_rows','omitted_invalid_price_rows','observed_price_candles','absent_second_slots','approximate_volume']},
        'version':VERSION,'status':'experimental_not_training_labels','config':asdict(config),'files':files,
        'parent_product_sha256':audit.file_hash(legacy.OUTPUT/'complete.json'),
        'parent_labels_sha256':parent['files']['labels']['sha256'],
        'actions':labels.group_by('action').len().sort('action').to_dicts(),
        'trades':trades.height,'total_price_pnl':float(trades['price_pnl'].sum()),
        'both_opportunities':int(labels['both_opportunities'].sum()),'seconds':time.perf_counter()-started,
        'semantics':'Pair-local quality in [0,1]; >=threshold opportunity bands; <=one reference trade per S->L pair; zero fees/no fills; stop/target references; carried next opportunity separate'}
    (output/'complete.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    return proof


@lru_cache(maxsize=1)
def _product(stamps):
    proof = json.loads((OUTPUT/'complete.json').read_text(encoding='utf-8'))
    if proof['version'] != VERSION or proof['day'] != DAY or proof['ticker'] != TICKER:
        raise ValueError('Opportunity product identity changed')
    frames = {}
    for name in ['labels','episodes','pairs','trades']:
        path = OUTPUT/(name+'.parquet')
        if audit.file_hash(path) != proof['files'][name]['sha256']:
            raise ValueError('Opportunity product hash mismatch: '+name)
        frames[name] = pl.read_parquet(path)
        if frames[name].height != proof['files'][name]['rows']:
            raise ValueError('Opportunity product count mismatch')
    return proof,frames


def product():
    paths = [OUTPUT/'complete.json']+[OUTPUT/(n+'.parquet') for n in ['labels','episodes','pairs','trades']]
    return _product(audit.fingerprint(paths))


def metadata(threshold=.9):
    proof,frames = product()
    labels = classify(frames['labels'],threshold)
    return {**proof,'config':{**proof['config'],'quality_threshold':threshold},
        'actions':labels.group_by('action').len().sort('action').to_dicts(),
        'both_opportunities':int(labels['both_opportunities'].sum()),'pairs':frames['pairs'].to_dicts()}


def chart(start_us=None,seconds=900,threshold=.9,view='combined'):
    if not 60 <= seconds <= 3600:
        raise ValueError('Invalid chart window')
    proof,frames = product()
    begin,finish = proof['begin_us'],proof['finish_us']
    start = max(begin,min(start_us if start_us is not None else begin,finish-1))
    end = min(start+seconds*1_000_000,finish)
    rows = classify(frames['labels'].filter((pl.col('time_us') >= start) & (pl.col('time_us') < end)),threshold,view)
    candles = [dict(time=r['time_us']//1_000_000-1,endTime=r['time_us']//1_000_000,isClosed=True,
        **{p:r[p] for p in ['open','high','low','close']}) for r in rows.iter_rows(named=True)]
    oscillator = []
    for column,name,color in [('macd_line','MACD','var(--primary)'),('macd_signal','Signal','var(--warning)'),('macd_histogram','Histogram','var(--muted-foreground)')]:
        values = rows['macd_line']-rows['macd_signal'] if column=='macd_histogram' else rows[column]
        oscillator.append(dict(column=column,label=name,paneKey='macd',style='histogram' if column=='macd_histogram' else 'line',color=color,lineWidth=1,
            data=[dict(time=int(t)//1_000_000-1,value=float(v)) for t,v in zip(rows['time_us'],values)]))
    regions = [dict(start=r['start_us']//1_000_000-1,end=r['end_us']//1_000_000-1,color='var(--success)' if r['direction']==1 else 'var(--danger)',label='')
        for r in frames['episodes'].filter((pl.col('start_us') < end) & (pl.col('end_us') > start)).iter_rows(named=True)]
    return dict(ticker=TICKER,version=VERSION,candles=candles,labels=rows.to_dicts(),oscillator_series=oscillator,regions=regions,
        start_us=start,end_us=end,previous_available=start>begin,next_available=end<finish,view=view,quality_threshold=threshold)
