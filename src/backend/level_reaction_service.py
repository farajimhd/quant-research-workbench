"""Read-only, as-of presentation of frozen ticker reaction models."""
from copy import deepcopy
from datetime import date, datetime, time
from hashlib import sha256
import json
from math import isfinite, prod
from pathlib import Path
from threading import Lock
from time import perf_counter
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from research.reaction_levels.v1.config import CONTRACT
from research.reaction_levels.v1.data import feature_rows
from src.market_engine.historical_level_checkpoint import digest

ROOT = Path(r'D:\TradingML\runtimes\reaction-level-model')
router = APIRouter(prefix='/api/research/level-reaction')
_busy = Lock()


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


@router.get('/models')
def models():
    result = []
    for path in sorted(ROOT.glob('*/manifest.json')):
        try:
            manifest = read(path)
            status = read(path.parent/'status.json')
            ready = (path.parent/'model-manifest.json').exists()
            dates = [p.stem for p in (path.parent/'partitions').glob('*.json')
                     if p.stem > manifest['calibration_days'][-1]] if ready else []
            result.append(dict(id=path.parent.name, ticker=manifest['ticker'],
                cutoff=manifest['calibration_days'][-1], dates=sorted(dates),
                ready=ready, status=status))
        except (OSError, ValueError, KeyError):
            continue  # In-progress atomic publication is not a usable model.
    return result


def prefix(inputs, stamp):
    """Future bars, quotes and full-day profiles never enter feature construction."""
    source = dict(inputs['source'])
    source['end'] = datetime.fromtimestamp(stamp, ZoneInfo('America/New_York')).isoformat()
    return dict(source=source, bars=[b for b in inputs['bars'] if b['t'] <= stamp],
                quotes=[q for q in inputs['quotes'] if q['t'] <= stamp])


def calculate(request):
    # Optional research dependencies must not prevent unrelated backend startup.
    import joblib
    import sklearn
    from threadpoolctl import threadpool_limits
    from research.reaction_levels.v1.model import predict
    started = perf_counter()
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
    clock = time.fromisoformat(request.time_et)
    if not time(4) < clock <= time(20):
        raise ValueError('Choose a completed second after 04:00:00 and through 20:00:00 ET')
    stamp = int(datetime.combine(request.session_date, clock, ZoneInfo('America/New_York')).timestamp())
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
    causal = prefix(inputs, stamp)
    if not causal['bars']:
        raise ValueError('No observed trades at the selected time')
    rows, features, _, _ = feature_rows(causal, effective)
    rows = rows[rows.t == stamp]
    if rows.empty:
        raise ValueError('No prediction at this second: stale price, warmup, or no neighboring historical levels')
    bundle = joblib.load(root/'model.joblib')
    if bundle['contract'] != CONTRACT or features != bundle['features'] or features != frozen['features']:
        raise ValueError('Model feature contract mismatch')
    with threadpool_limits(limits=4):
        probabilities = predict(bundle['model'], bundle['calibration'], rows[features].to_numpy(dtype='float32'))
    results = []
    for (_, row), probability in zip(rows.iterrows(), probabilities):
        if any(not isfinite(float(p)) or not 0 <= p <= 1 for p in probability) or abs(sum(probability)-1) > 1e-6:
            raise ValueError('Model returned invalid probabilities')
        level = effective['levels'][int(row.level_index)]
        upper = bool(row.target_upper)
        results.append(dict(side='upper' if upper else 'lower',
            level={key:level[key] for key in ('id','lower','upper','price','origin_session','strength_status')},
            probabilities=dict(zip(CONTRACT['labels'],map(float,probability))),
            up=float(probability[2 if upper else 1]), down=float(probability[1 if upper else 2])))
    return dict(model_id=request.model_id, ticker=manifest['ticker'], as_of=stamp,
        cutoff=manifest['calibration_days'][-1], horizon_seconds=CONTRACT['horizon_seconds'],
        price=float(rows.iloc[0].price), price_age=float(rows.iloc[0].price_age),
        model_hash=frozen['model_hash'], book_hash=book['checkpoint_hash'], book_session=book['session'],
        input_hash=inputs['content_hash'], split_factor=factor, price_basis='raw session prices',
        max_input_timestamp=max(b['t'] for b in causal['bars']), results=results,
        candles=[b for b in causal['bars'] if b['t'] > stamp-1800], seconds=perf_counter()-started)


@router.post('/predict')
def preview(request: PredictionRequest):
    if not _busy.acquire(False):
        raise HTTPException(429, 'A model prediction is already running; retry shortly')
    try:
        return calculate(request)
    except (ValueError, OSError, KeyError, IndexError, ImportError) as exc:
        raise HTTPException(422, f'Prediction unavailable: {exc}') from exc
    finally:
        _busy.release()
