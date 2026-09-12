"""Shared, versioned historical inference for chart and strategy consumers.

Only prepared forward sessions are supported. No HTTP or chart is required.
Full-session feature caching is safe because every transform is backward-looking;
prediction and result access are restricted to completed requested seconds.
"""
from copy import deepcopy
from datetime import date, datetime, time
from hashlib import sha256
import json
from math import isfinite, prod
from pathlib import Path
from threading import RLock
from time import perf_counter
from collections import OrderedDict
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from research.reaction_levels.v1.config import CONTRACT
from research.reaction_levels.v1.data import feature_rows
from src.market_engine.historical_level_checkpoint import digest

ROOT = Path(r'D:\TradingML\runtimes\reaction-level-model')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def file_hash(path):
    return sha256(path.read_bytes()).hexdigest()


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    ticker: str = Field(pattern=r'^[A-Za-z0-9.\-]{1,20}$')
    session_date: date
    time_et: str = Field(pattern=r'^\d{2}:\d{2}:\d{2}$')


def prefix(inputs, stamp):
    """Future bars, quotes and full-day profiles never enter feature construction."""
    source = dict(inputs['source'])
    source['end'] = datetime.fromtimestamp(stamp, ZoneInfo('America/New_York')).isoformat()
    return dict(source=source, bars=[b for b in inputs['bars'] if b['t'] <= stamp],
                quotes=[q for q in inputs['quotes'] if q['t'] <= stamp])


def load_session(request):
    # Optional research dependencies must not prevent unrelated backend startup.
    import joblib
    import sklearn
    root = (ROOT/request.model_id).resolve()
    if not root.is_relative_to(ROOT.resolve()):
        raise ValueError('Invalid model path')
    manifest = read(root/'manifest.json')
    if sklearn.__version__ != manifest['sklearn_version']:
        raise ValueError('Install the model sklearn version from requirements.txt before serving')
    frozen = read(root/'model-manifest.json')
    day = request.session_date.isoformat()
    if request.ticker.upper() != manifest['ticker']:
        raise ValueError('Model ticker does not match the chart')
    if day <= manifest['calibration_days'][-1]:
        raise ValueError('Selected date precedes the frozen model data cutoff; forward inference is unavailable')
    if file_hash(root/'manifest.json') != frozen['manifest_hash'] or file_hash(root/'model.joblib') != frozen['model_hash']:
        raise ValueError('Frozen model integrity check failed')
    meta = read(root/'partitions'/f'{day}.json')
    # Partition provenance pins exactly the book used before this session.
    prior = sorted(p for p in (root/'books').glob('*.json') if p.stem < day)[-1]
    book = read(prior)
    if (book['checkpoint_hash'] != meta['prior_hash'] or
            digest({k:v for k,v in book.items() if k != 'checkpoint_hash'}) != book['checkpoint_hash']):
        raise ValueError('Prior-session book integrity check failed')
    inputs = read(root/'inputs'/f'{day}.json')
    if (inputs['content_hash'] != meta['input_hash'] or
            digest({k:v for k,v in inputs.items() if k != 'content_hash'}) != inputs['content_hash']):
        raise ValueError('Canonical input integrity check failed')
    effective = deepcopy(book)
    expected_actions = [s for s in manifest['splits'] if book['session'] < s['execution_date'] <= day]
    if meta['split_actions'] != expected_actions:
        raise ValueError('Partition split provenance does not match the frozen manifest')
    factor = prod(float(s['split_from'])/float(s['split_to']) for s in meta['split_actions'])
    if not isfinite(factor) or factor <= 0:
        raise ValueError('Invalid split price factor')
    for level in effective['levels']:
        for key in ('lower','upper','price'):
            level[key] *= factor
        for role in level.get('reaction_center',{}).get('roles',{}).values():
            for key in ('center','scale'):
                if role.get(key) is not None:
                    role[key] *= factor
    bundle = joblib.load(root/'model.joblib')
    # Prepared historical feature transforms are backward-looking at every row.
    # Cache features, never labels or future predictions. Prefix parity is tested.
    rows, features, _, _ = feature_rows({k: inputs[k] for k in ('source','bars','quotes')}, effective)
    if bundle['contract'] != CONTRACT or features != bundle['features'] or features != frozen['features']:
        raise ValueError('Model feature contract mismatch')
    return dict(root=root, manifest=manifest, frozen=frozen, book=book, effective=effective,
                inputs=inputs, factor=factor, bundle=bundle, rows=rows, features=features, predictions={})


_sessions = OrderedDict()
_lock = RLock()
MAX_SESSIONS = 2
TIMEFRAMES = {'1s': 1, '5s': 5, '10s': 10, '30s': 30, '1m': 60, '5m': 300, '1h': 3600}


def _signature(request):
    root = ROOT/request.model_id
    day = request.session_date.isoformat()
    prior = sorted(p for p in (root/'books').glob('*.json') if p.stem < day)[-1]
    paths = [root/'manifest.json', root/'model-manifest.json', root/'model.joblib',
             root/'partitions'/f'{day}.json', root/'inputs'/f'{day}.json', prior]
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


def winner(results):
    """Compare directional breaks, not rejection probabilities. Upper wins ties."""
    if not results:
        return None
    selected = max(results, key=lambda r: (r['probabilities']['broken'], r['side'] == 'upper'))
    return dict(direction='up' if selected['side'] == 'upper' else 'down',
                probability=selected['probabilities']['broken'], side=selected['side'],
                level_id=selected['level']['id'])


def predict_series(request, close_times, *, timeframe_seconds=1):
    """Return exact completed-candle records; never substitute a nearby result.

    Cache is bounded to two verified prepared sessions. Records are defensive
    copies so callers cannot mutate later chart/strategy results.
    """
    from research.reaction_levels.v1.model import predict
    from threadpoolctl import threadpool_limits
    request = PredictionRequest.model_validate(request.model_dump())
    clock = time.fromisoformat(request.time_et)
    if not time(4) < clock <= time(20):
        raise ValueError('Choose a completed second after 04:00:00 and through 20:00:00 ET')
    stamp = int(datetime.combine(request.session_date, clock, ZoneInfo('America/New_York')).timestamp())
    opening = int(datetime.combine(request.session_date, time(4), ZoneInfo('America/New_York')).timestamp())
    if timeframe_seconds not in TIMEFRAMES.values():
        raise ValueError('The 1s model supports completed intraday candles from 1s through 1h')
    times = sorted(set(close_times))
    if len(times) > 57600 or any(type(t) is not int or not opening < t <= stamp or
            (t-opening) % timeframe_seconds for t in times):
        raise ValueError('Candle closes must be aligned, completed seconds within the as-of session')
    started = perf_counter()
    with _lock:
        key = (str(ROOT), request.model_id, request.ticker.upper(), request.session_date)
        signature = _signature(request)
        cached = _sessions.get(key)
        if cached is None or cached[0] != signature:
            session = load_session(request)
            if _signature(request) != signature:
                raise ValueError('Model/session artifacts changed during loading; retry')
            _sessions[key] = (signature, session)
            while len(_sessions) > MAX_SESSIONS:
                _sessions.popitem(last=False)
        else:
            session = cached[1]
        _sessions.move_to_end(key)
        records = session['predictions']
        missing = [t for t in times if t not in records]
        rows = session['rows']
        selected = rows[rows.t.isin(missing)]
        grouped = {}
        if not selected.empty:
            bundle = session['bundle']
            with threadpool_limits(limits=4):
                probabilities = predict(bundle['model'], bundle['calibration'],
                    selected[session['features']].to_numpy(dtype='float32'))
            for (_, row), probability in zip(selected.iterrows(), probabilities):
                if any(not isfinite(float(p)) or not 0 <= p <= 1 for p in probability) or abs(sum(probability)-1) > 1e-6:
                    raise ValueError('Model returned invalid probabilities')
                level = session['effective']['levels'][int(row.level_index)]
                upper = bool(row.target_upper)
                result = dict(side='upper' if upper else 'lower',
                    level={k: level[k] for k in ('id','lower','upper','price','origin_session','strength_status')},
                    probabilities=dict(zip(CONTRACT['labels'], map(float, probability))),
                    up=float(probability[2 if upper else 1]), down=float(probability[1 if upper else 2]))
                t = int(row.t)
                group = grouped.setdefault(t, dict(price=float(row.price), price_age=float(row.price_age), results=[]))
                group['results'].append(result)
        for t in missing:
            group = grouped.get(t)
            records[t] = dict(as_of=t, available_at=t, status='ready' if group else 'unavailable',
                reason=None if group else 'stale_price_warmup_or_no_neighboring_level',
                **(group or dict(price=None, price_age=None, results=[])))
            records[t]['winner'] = winner(records[t]['results'])
        manifest, frozen, book = (session[k] for k in ('manifest','frozen','book'))
        common = dict(contract='level-reaction-v1', model_id=request.model_id, ticker=manifest['ticker'],
            session_date=request.session_date.isoformat(), cutoff=manifest['calibration_days'][-1],
            horizon_seconds=CONTRACT['horizon_seconds'], model_hash=frozen['model_hash'],
            book_hash=book['checkpoint_hash'], book_session=book['session'],
            input_hash=session['inputs']['content_hash'], split_factor=session['factor'],
            price_basis='raw session prices')
        output = [dict(records[t], candle_start=t-timeframe_seconds, candle_close=t) for t in times]
        return deepcopy(dict(common, as_of=stamp, timeframe_seconds=timeframe_seconds,
            records=output, requested=len(times), available=sum(r['status']=='ready' for r in output),
            seconds=perf_counter()-started))


def calculate(request):
    stamp = int(datetime.combine(request.session_date, time.fromisoformat(request.time_et),
                                 ZoneInfo('America/New_York')).timestamp())
    response = predict_series(request, [stamp])
    record = response.pop('records')[0]
    if record['status'] != 'ready':
        raise ValueError('No prediction at this second: stale price, warmup, or no neighboring historical levels')
    # Retained single-time endpoint compatibility; the new indicator uses /series.
    with _lock:
        inputs = _sessions[(str(ROOT), request.model_id, request.ticker.upper(), request.session_date)][1]['inputs']
        causal = prefix(inputs, stamp)
    return dict(response, **record, max_input_timestamp=max(b['t'] for b in causal['bars']),
                candles=[b for b in causal['bars'] if b['t'] > stamp-1800])
