"""Experimental hindsight action values in price units, without a fill model.

This separate producer never modifies certified teacher targets. A flat value
includes future trades; a held value includes its current trade's full price
change exactly once, at exit. Discount uses elapsed seconds, not row counts.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import json
import time
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from research.rl_trading.v6 import label_audit as audit
from research.mlops.manifest import write_run_manifest

VERSION = 'price-action-long-v1'
DAY = '2026-07-31'
TICKER = 'NVDA'
OUTPUT = Path('D:/TradingML/runtimes/rl-v6-price-action-long-v1/NVDA/2026-07-31-r2')


@dataclass(frozen=True)
class Config:
    timeframe_seconds: int = 1
    half_life_seconds: float = 30.
    stop_offset: float = .01

    def validate(self):
        if self.timeframe_seconds != 1:
            raise ValueError('This source adapter provides certified 1s MACD only')
        if not np.isfinite(self.half_life_seconds) or self.half_life_seconds <= 0:
            raise ValueError('Discount half-life must be positive and finite')
        if not np.isfinite(self.stop_offset) or self.stop_offset < 0:
            raise ValueError('Stop offset must be nonnegative and finite')


def episode_geometry(bars: pl.DataFrame, config=Config()):
    """Validate observed price candles and construct shared MACD S-to-L pairs."""
    config.validate()
    needed = ['time_us', 'open', 'high', 'low', 'close', 'macd_line', 'macd_signal']
    if not set(needed) <= set(bars.columns) or bars.height < 2:
        raise ValueError('Need at least two certified price/indicator candles')
    if bars.select(needed).null_count().sum_horizontal().item():
        raise ValueError('Null price/indicator input')
    times = bars['time_us'].to_numpy()
    price = bars['close'].to_numpy().astype(np.float64)
    if np.any(np.diff(times) <= 0) or not np.all(np.isfinite(bars.select(needed).to_numpy())):
        raise ValueError('Inputs must be finite, unique and time ordered')
    if bars.filter((pl.col('low') <= 0) | (pl.col('high') < pl.max_horizontal('open', 'close')) |
                   (pl.col('low') > pl.min_horizontal('open', 'close'))).height:
        raise ValueError('Invalid OHLC geometry')
    direction = np.where(bars['macd_line'].to_numpy() >= bars['macd_signal'].to_numpy(), 1, -1)
    episode = np.cumsum(np.r_[True, direction[1:] != direction[:-1]]).astype(np.int64)
    frame = bars.with_columns(pl.Series('episode_id', episode), pl.Series('direction', direction))
    episodes = frame.group_by('episode_id', maintain_order=True).agg(
        pl.col('direction').first(), pl.col('time_us').min().alias('start_us'),
        (pl.col('time_us').max()+1_000_000).alias('end_us'), pl.len().alias('candles'),
        pl.min_horizontal('open', 'close').min().alias('min_co'),
        pl.max_horizontal('open', 'close').max().alias('max_co'), pl.col('high').max().alias('max_high'))
    # A sign run ends at the next observed sign change, including intervening
    # missing-price seconds. No candles or labels are manufactured in the gap.
    episodes = episodes.with_columns(pl.col('start_us').shift(-1).fill_null(int(times[-1])+1_000_000).alias('end_us'))
    pairs = []
    previous = None
    for e in episodes.iter_rows(named=True):
        if e['direction'] == 1:
            short = previous if previous and previous['direction'] == -1 else None
            start_price = min(e['min_co'], short['min_co']) if short else e['min_co']
            pairs.append(dict(pair_id=len(pairs)+1, long_episode=e['episode_id'],
                short_episode=short['episode_id'] if short else None,
                start_us=short['start_us'] if short else e['start_us'], end_us=e['end_us'],
                best_start_price=start_price, best_end_price=e['max_co'],
                best_stop=start_price-config.stop_offset, best_target=e['max_high'],
                reference_range=e['max_co']-start_price))
        previous = e
    return frame, episodes, pairs


def calculate(bars: pl.DataFrame, config=Config()):
    """Return every observed candle, pair geometry and one consistent sequence.

    Input columns time_us/OHLC/MACD describe completed 1s candles. Flat arrays
    F[N+1], wait[N], enter[N], best_exit[N] are computed backward. For each
    entry i, candidate exits j>i score d(i,j)*(close[j]-close[i]+d(j,j+1)*F[j+1]).
    Numpy evaluates each future-exit vector without an N-by-N retained matrix.
    Complexity O(N^2) time and O(N) working memory; bounded to one RTH ticker.
    """
    frame, episodes, pairs = episode_geometry(bars, config)
    times = bars['time_us'].to_numpy()
    price = bars['close'].to_numpy().astype(np.float64)
    n = len(price)
    elapsed = (times-times[0]).astype(np.float64)/1e6  # [N], actual elapsed seconds
    discount = np.exp2(-np.diff(elapsed)/config.half_life_seconds)  # [N-1]
    flat = np.zeros(n+1)
    wait = np.zeros(n)
    enter = np.full(n, np.nan)
    continuation = np.zeros(n)
    best_exit = np.full(n, -1, dtype=np.int64)
    for i in range(n-2, -1, -1):
        wait[i] = continuation[i] = discount[i]*flat[i+1]
        future_discount = np.exp2(-(elapsed[i+1:]-elapsed[i])/config.half_life_seconds)
        candidates = future_discount*(price[i+1:]-price[i]+continuation[i+1:])
        enter[i] = candidates.max()
        # Prefer holding on exact exit ties, and waiting on entry ties.
        best_exit[i] = i+1+np.flatnonzero(candidates == enter[i])[-1]
        flat[i] = max(wait[i], enter[i])
    actions, exits, holds, basis, values, realized = [], [], [], [], [], []
    trades = []
    entry_index = None
    scheduled_exit = None
    for i in range(n):
        if entry_index is None:
            action = 'ENTRY' if i < n-1 and enter[i] > wait[i]+1e-10 else 'WAIT'
            exits.append(None); holds.append(None); basis.append(None); realized.append(None)
            values.append(float(enter[i] if action == 'ENTRY' else wait[i]))
            if action == 'ENTRY':
                entry_index, scheduled_exit = i, int(best_exit[i])
        else:
            e = price[entry_index]
            exit_value = float(price[i]-e+continuation[i])
            hold_value = None
            if i < n-1:
                d = np.exp2(-(elapsed[i+1:]-elapsed[i])/config.half_life_seconds)
                hold_value = float((d*(price[i+1:]-e+continuation[i+1:])).max())
            action = 'EXIT' if i == scheduled_exit else 'HOLD'
            exits.append(exit_value); holds.append(hold_value); basis.append(float(e))
            values.append(exit_value if action == 'EXIT' else hold_value)
            pnl = float(price[i]-e) if action == 'EXIT' else None
            realized.append(pnl)
            if action == 'EXIT':
                trades.append(dict(entry_us=int(times[entry_index]), exit_us=int(times[i]),
                    entry_price=float(e), exit_price=float(price[i]), price_pnl=pnl,
                    hold_seconds=float(elapsed[i]-elapsed[entry_index])))
                entry_index = scheduled_exit = None
        actions.append(action)
    assert entry_index is None, 'Terminal position must be closed'
    labels = frame.with_columns(pl.Series('action', actions),
        pl.Series('entry_value', [None if np.isnan(x) else float(x) for x in enter], dtype=pl.Float64),
        pl.Series('wait_value', wait), pl.Series('exit_value', exits, dtype=pl.Float64),
        pl.Series('hold_value', holds, dtype=pl.Float64), pl.Series('entry_basis', basis, dtype=pl.Float64),
        pl.Series('label_value', values, dtype=pl.Float64), pl.Series('realized_price_pnl', realized, dtype=pl.Float64))
    return labels, episodes, pl.DataFrame(pairs), pl.DataFrame(trades, schema=dict(
        entry_us=pl.Int64, exit_us=pl.Int64, entry_price=pl.Float64, exit_price=pl.Float64,
        price_pnl=pl.Float64, hold_seconds=pl.Float64))


def build(output=OUTPUT, config=Config()):
    """Compute the pinned, unsealed session once; save a separate runtime product."""
    config.validate()
    output = Path(output).resolve()
    if not output.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError('Experiment output must remain under the runtime root')
    if (output/'complete.json').exists():
        raise ValueError('Experiment already exists; choose a new runtime directory')
    started = time.perf_counter()
    _, root, _ = audit.sources(DAY)
    proof = json.loads((root/'complete.json').read_text())
    if audit.file_hash(root/'episodes.parquet') != proof['outputs']['episodes']['sha256']:
        raise ValueError('Ticker identity certificate mismatch')
    identity = pl.read_parquet(root/'episodes.parquet').filter(pl.col('ticker') == TICKER).select('listing_id').unique()
    if identity.height != 1:
        raise ValueError('Pinned liquid ticker identity is missing or ambiguous')
    listing = identity.item()
    paths = [root/'complete.json']+[root/'bank'/p for p in ['complete.json', 'close_us.npy', 'scalar.npy']]
    with audit.LOCK:
        manifest, clocks, scalar = audit._verified_bank(DAY, audit.fingerprint(paths))
    left, right = manifest['offsets'][listing]
    begin = int(datetime.fromisoformat(DAY+'T09:30:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1e6)
    finish = begin+23400_000_000
    selection = (clocks[left:right] > begin) & (clocks[left:right] <= finish)
    raw, times = scalar[left:right][selection], clocks[left:right][selection]
    valid = (raw[:,audit.SCALAR_NAMES.index('bar_price_valid')] == 1) & (raw[:,audit.SCALAR_NAMES.index('bar_extremes_valid')] == 1)
    if np.any(raw[valid, audit.SCALAR_NAMES.index('indicator_available')] != 1):
        raise ValueError('Valid-price RTH candles have unavailable indicators')
    data = {'time_us': times[valid]}
    for name in ['open', 'high', 'low', 'close']:
        data[name] = np.exp(raw[valid,audit.SCALAR_NAMES.index('log_'+name)].astype(np.float64))
    for name in ['line', 'signal']:
        data['macd_'+name] = raw[valid,audit.SCALAR_NAMES.index('macd_'+name+'_rel')].astype(np.float64)*data['close']
    labels, episodes, pairs, trades = calculate(pl.DataFrame(data), config)
    output.mkdir(parents=True, exist_ok=True)
    products = dict(labels=labels, episodes=episodes, pairs=pairs, trades=trades)
    files = {}
    for name, frame in products.items():
        path = output/(name+'.parquet'); frame.write_parquet(path)
        files[name] = dict(rows=frame.height, sha256=audit.file_hash(path))
    record_manifest(output, root, config)
    volume = float(np.expm1(raw[:,audit.SCALAR_NAMES.index('log_volume')].astype(np.float64)).sum())
    result = dict(version=VERSION, status='experimental_not_training_labels', day=DAY,
        ticker=TICKER, listing_id=listing, session='09:30-16:00 ET (candle starts)',
        begin_us=begin+1_000_000, finish_us=finish+1_000_000, config=asdict(config),
        price_source='SHA-verified V6 bank: decoded float32 log OHLC and saved 1s MACD',
        source_bank_certificate_sha256=audit.file_hash(root/'complete.json'),
        source_files_sha256={p: manifest['files_sha256'][p] for p in ['close_us.npy','scalar.npy']},
        consumed_activity_rows=len(times), omitted_invalid_price_rows=int((~valid).sum()),
        observed_price_candles=labels.height, absent_second_slots=23400-len(times),
        approximate_volume=volume, files=files,
        actions=labels.group_by('action').len().sort('action').to_dicts(),
        trades=trades.height, total_price_pnl=float(trades['price_pnl'].sum()),
        initial_discounted_value=float(labels['label_value'][0]), seconds=time.perf_counter()-started,
        semantics='Zero fees; close-price changes; final trade P&L discounted to decision time; stop/target references only; no fill model; full-session future continuation')
    (output/'complete.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def record_manifest(output, source_bank, config):
    """Record shared run provenance plus exact dirty-source file hashes."""
    repo = Path(__file__).resolve().parents[3]
    path = Path(output)/'manifest.json'
    write_run_manifest(path, repo_root=repo, model_family='rl_trading', version=VERSION,
        job_type='experimental_price_action_labels', run_name='NVDA-2026-07-31-long-price-action',
        args=dict(day=DAY,ticker=TICKER,**asdict(config)), config=asdict(config),
        data_roots=dict(certified_bank=str(source_bank)), output_root=Path(output), secret_keys=())
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['producer_files_sha256'] = {p.relative_to(repo).as_posix():audit.file_hash(p)
        for p in [Path(__file__), Path(audit.__file__)]}
    manifest['producer_working_tree_snapshot'] = True
    path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')


def product():
    if not (OUTPUT/'complete.json').exists():
        raise ValueError('Price-action experiment has not been generated')
    paths = [OUTPUT/'complete.json']+[OUTPUT/(p+'.parquet') for p in ['labels','episodes','pairs','trades']]
    return _product(audit.fingerprint(paths))


@audit.lru_cache(maxsize=1)
def _product(stamps):
    proof = json.loads((OUTPUT/'complete.json').read_text(encoding='utf-8'))
    if proof['version'] != VERSION or proof['day'] != DAY or proof['ticker'] != TICKER:
        raise ValueError('Experiment identity changed')
    frames = {}
    for name, binding in proof['files'].items():
        if name not in ('labels','episodes','pairs','trades'):
            raise ValueError('Unexpected experiment product')
        path = OUTPUT/(name+'.parquet')
        if audit.file_hash(path) != binding['sha256']:
            raise ValueError('Experiment product hash mismatch: '+name)
        frame = pl.read_parquet(path)
        if frame.height != binding['rows']:
            raise ValueError('Experiment row count mismatch')
        frames[name] = frame
    return proof, frames


def metadata():
    proof, frames = product()
    return {**proof, 'pairs': frames['pairs'].to_dicts()}


def chart(start_us=None, seconds=900):
    if not 60 <= seconds <= 3600:
        raise ValueError('Invalid chart window')
    proof, frames = product()
    begin, finish = proof['begin_us'], proof['finish_us']
    start = max(begin, min(start_us if start_us is not None else begin, finish-1))
    end = min(start+seconds*1_000_000, finish)
    selected = frames['labels'].filter((pl.col('time_us') >= start) & (pl.col('time_us') < end))
    candles = [dict(time=r['time_us']//1_000_000-1, endTime=r['time_us']//1_000_000,
        isClosed=True, **{p:r[p] for p in ['open','high','low','close']}) for r in selected.iter_rows(named=True)]
    oscillator = []
    for column, name, color in [('macd_line','MACD','var(--primary)'),('macd_signal','Signal','var(--warning)'),('macd_histogram','Histogram','var(--muted-foreground)')]:
        series = selected['macd_line']-selected['macd_signal'] if column == 'macd_histogram' else selected[column]
        oscillator.append(dict(column=column, label=name, paneKey='macd', style='histogram' if column == 'macd_histogram' else 'line', color=color, lineWidth=1,
            data=[dict(time=int(t)//1_000_000-1, value=float(v)) for t,v in zip(selected['time_us'],series)]))
    regions = [dict(start=e['start_us']//1_000_000-1, end=e['end_us']//1_000_000-1,
        color='var(--success)' if e['direction'] == 1 else 'var(--danger)', label='')
        for e in frames['episodes'].filter((pl.col('start_us') < end) & (pl.col('end_us') > start)).iter_rows(named=True)]
    return dict(ticker=TICKER, candles=candles, labels=selected.to_dicts(), oscillator_series=oscillator,
        regions=regions, start_us=start, end_us=end, previous_available=start>begin, next_available=end<finish)
