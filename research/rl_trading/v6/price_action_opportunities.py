"""Episode-local swing opportunities with one time-aware exit cluster.

ENTRY quality normalizes discounted future gain within the same S->L pair.
EXIT compares current gain with discounted continuation from its reference
entry. Only the first contiguous dominant cluster supervises EXIT. No fills.
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

VERSION = 'price-action-long-opportunities-v6'
OUTPUT = Path('D:/TradingML/runtimes/rl-v6-price-action-long-v6/NVDA/2026-07-31-reporting-repaired')
DAY, TICKER = legacy.DAY, legacy.TICKER


@dataclass(frozen=True)
class Config(legacy.Config):
    quality_threshold: float = .9
    minimum_position_seconds: float = 5.

    def validate(self):
        super().validate()
        if not np.isfinite(self.minimum_position_seconds) or self.minimum_position_seconds <= 0:
            raise ValueError('Minimum position duration must be positive')
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
    exit_ = pl.col('in_exit_cluster') & (pl.col('exit_gain') > 0) & (pl.col('exit_quality') >= threshold)
    # Conditional held supervision remains inspectable, but the combined
    # chart must not imply a surviving position after the reference exit.
    context = pl.col('exit_gain').is_not_null() if view == 'held' else pl.col('in_reference_hold')
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
    Its reference exit is the first strictly subsequent L close dominating
    discounted continuation. Every candle
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
    exit_cluster = np.zeros(n,dtype=bool)
    hold_discounted = np.full(n,np.nan)
    liquidation_quality = np.full(n,np.nan)
    entry_targets = [None]*n
    hold_targets = [None]*n
    hold_target_gains = [None]*n
    reference_actions = np.full(n,'WAIT',dtype='<U5')
    trades = []
    for pair in pairs:
        left, right = np.searchsorted(times, [pair['start_us'], pair['end_us']])
        indexes = np.arange(left, right)
        long_indexes = indexes[directions[indexes] == 1]
        pair_ids[indexes] = pair['pair_id']
        discounted_exits = {}
        for i in indexes:
            future = long_indexes[(long_indexes > i) & (times[long_indexes]-times[i] >= config.minimum_position_seconds*1e6)]
            if not len(future):
                continue
            scores = (prices[future]-prices[i])*np.exp2(-(times[future]-times[i])/1e6/config.half_life_seconds)
            value = float(scores.max())
            if value > 0:
                entry_gain[i] = value
                discounted_exits[i] = int(future[np.flatnonzero(scores == value)[0]])
                entry_targets[i] = int(times[discounted_exits[i]])
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
        valid_exits = indexes[(indexes > i) & (directions[indexes] == 1)]
        exit_gain[valid_exits] = prices[valid_exits]-prices[i]
        basis[indexes[indexes > i]] = prices[i]
        # Compare liquidation now with continuation discounted to now.
        # Future target witnesses this value, with earliest equal maxima.
        for current in valid_exits:
            later = long_indexes[long_indexes > current]
            hold_discounted[current] = 0.
            if len(later):
                scores = (prices[later]-prices[i])*np.exp2(-(times[later]-times[current])/1e6/config.half_life_seconds)
                target = int(later[np.argmax(scores)])
                hold_discounted[current] = max(0.,float(scores.max()))
                if scores.max() > 0:
                    hold_targets[current] = int(times[target])
                    hold_target_gains[current] = float(prices[target]-prices[i])
            now = max(0.,float(exit_gain[current]))
            exit_quality[current] = now/max(now,hold_discounted[current]) if now > 0 else 0.
        optimal = (exit_gain[valid_exits] > 0) & (exit_quality[valid_exits] == 1.) & (times[valid_exits]-times[i] >= config.minimum_position_seconds*1e6)
        first = int(np.flatnonzero(optimal)[0])
        j = int(valid_exits[first])
        late_entries = indexes[times[indexes] > times[j]-config.minimum_position_seconds*1e6]
        entry_gain[late_entries] = 0.
        entry_quality[late_entries] = 0.
        for late in late_entries:
            entry_targets[late] = None
        end = first
        while end < len(valid_exits) and optimal[end]:
            exit_cluster[valid_exits[end]] = True
            end += 1
        liquidation_quality[valid_exits] = exit_quality[valid_exits]
        # Later hypothetical held rows remain HOLD, never reopen an exit cluster.
        exit_quality[valid_exits[~exit_cluster[valid_exits]]] = 0.
        pnl = float(prices[j]-prices[i])
        assert pnl > 0 and j > i
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
        pl.Series('entry_target_us',entry_targets,dtype=pl.Int64),
        pl.Series('entry_horizon_seconds',[None if t is None else (t-int(times[i]))/1e6 for i,t in enumerate(entry_targets)],dtype=pl.Float64),
        pl.Series('hold_target_us',hold_targets,dtype=pl.Int64),
        pl.Series('hold_horizon_seconds',[None if t is None else (t-int(times[i]))/1e6 for i,t in enumerate(hold_targets)],dtype=pl.Float64),
        pl.Series('hold_target_gain',hold_target_gains,dtype=pl.Float64),
        pl.Series('hold_discounted_gain',nullable(hold_discounted),dtype=pl.Float64),
        pl.Series('liquidation_quality',nullable(liquidation_quality),dtype=pl.Float64),
        pl.Series('in_exit_cluster',exit_cluster),
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


def build_bank_preview(bank_root, output=OUTPUT, config=Config()):
    """Bounded NVDA RTH audit preview from reporting-certified repaired bank.

    Separate experimental product; never publishes the teacher registry.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from research.rl_trading.v6.bank import open_bank
    from research.rl_trading.v6.opportunity_dataset import decoded_bars, reporting_plan
    root, output = Path(bank_root), Path(output)
    if not output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError('External runtime output required')
    if (output/'complete.json').exists():
        raise ValueError('Choose a fresh immutable preview output')
    reporting_plan(root, DAY)
    identities=pl.read_parquet(root/'episodes.parquet').filter(pl.col('ticker')==TICKER)['listing_id'].unique().to_list()
    if len(identities)!=1: raise ValueError('Preview ticker identity ambiguous')
    bank=open_bank(root/'bank')
    bars, rejected=decoded_bars(bank.listing(identities[0]))
    begin=int(datetime.fromisoformat(DAY+'T09:30:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1e6)+1_000_000
    finish=begin+int(6.5*3600e6)
    bars=bars.filter((pl.col('time_us')>=begin)&(pl.col('time_us')<finish))
    products=dict(zip(['labels','episodes','pairs','trades'],calculate(bars,config)))
    output.mkdir(parents=True,exist_ok=True); files={}
    for name, frame in products.items():
        path=output/(name+'.parquet');frame.write_parquet(path)
        files[name]=dict(rows=frame.height,sha256=audit.file_hash(path))
    labels,trades=products['labels'],products['trades']
    proof=dict(day=DAY,ticker=TICKER,listing_id=identities[0],session='Repaired NVDA RTH preview',
        begin_us=begin,finish_us=finish,version=VERSION,status='experimental_not_training_labels',
        config=asdict(config),files=files,price_source=str(root/'bank'),
        source_bank_certificate_sha256=audit.file_hash(root/'complete.json'),
        observed_price_candles=bars.height,absent_second_slots=23400-bars.height,
        consumed_activity_rows=None,omitted_invalid_price_rows=None,approximate_volume=None,
        actions=labels.group_by('action').len().sort('action').to_dicts(),
        trades=trades.height,total_price_pnl=float(trades['price_pnl'].sum()),
        both_opportunities=int(labels['both_opportunities'].sum()),
        semantics='Reporting-certified corrected source; single time-aware EXIT cluster; HOLD discounted-continuation horizon; raw current gain preserved',
        sealed_test_accessed=False)
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
    proof,frames = product()
    return chart_frames(proof, frames, start_us, seconds, threshold, view)


def chart_frames(proof, frames, start_us=None, seconds=900, threshold=.9, view='combined'):
    """Render saved labels without recalculating their numerical targets."""
    if not 60 <= seconds <= 3600:
        raise ValueError('Invalid chart window')
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
    return dict(ticker=proof['ticker'],version=VERSION,candles=candles,labels=rows.to_dicts(),oscillator_series=oscillator,regions=regions,
        start_us=start,end_us=end,previous_available=start>begin,next_available=end<finish,view=view,quality_threshold=threshold)
