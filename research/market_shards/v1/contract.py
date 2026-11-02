"""Storage and availability contract, independent of optimizer versions."""
from datetime import datetime, time
from zoneinfo import ZoneInfo
from pathlib import Path
import hashlib
import json
import os

VERSION = 'arte-premarket-shards-v1'
TRADE_WINDOWS = {1000: 60, 10000: 30, 60000: 30, 3600000: 16, 86400000: 20}
QUOTE_WINDOWS = {1000: 60}
CONTRACT = dict(version=VERSION, scanner=dict(window_seconds=60, top_n=5,
    measure='trade_count', minimum_close=.8, maximum_close=50.,prior_close='price-valid regular 1m close; XNYS boundary',
    ties='source ticker ascending', positive_only=True), trade_windows=TRADE_WINDOWS,
    quote_windows=QUOTE_WINDOWS, decision_hours=['04:00', '09:30'],
    execution_hours=['04:00', '20:00'], timezone='America/New_York',
    excluded=['v7.*', 'log_float_shares', 'float_present', 'log_shares_outstanding', 'shares_present'],
    availability='completed bars only; sparse intervals masked; no future fill',
    identity='source approved_retrospective; historical_membership_certified=false',
    split='opening-known source snapshot; historical values adjusted at read time',
    storage='bars once per source session; context referenced', train_fraction=.8,
    holdout=False, execution='v60-causal-displayed-book-no-age-v2')


def digest(value):
    # JSON object keys are strings on disk; normalize before sorting so integer
    # timeframe keys hash identically before and after a restart.
    normalized=json.loads(json.dumps(value,allow_nan=False))
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(2**20), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.partial')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def origin_us(day):
    return int(datetime.combine(datetime.fromisoformat(day).date(), time(), ZoneInfo('America/New_York')).timestamp()) * 1000000


def require_runtime(path):
    path = Path(path).resolve()
    runtime = Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not path.is_relative_to(runtime):
        raise ValueError('Output must be under available D:/TradingML/runtimes')
    path.mkdir(parents=True, exist_ok=True)
    return path


def split_days(days):
    days = sorted(days)
    if len(days) < 2 or len(days) != len(set(days)):
        raise ValueError('At least two unique complete sessions required')
    cut = max(1, min(len(days)-1, int(len(days)*.8)))
    return dict(training=days[:cut], validation=days[cut:])
