"""HTTP projection of the shared causal reaction-model feed."""
from threading import Lock
from fastapi import APIRouter, HTTPException
from pydantic import Field
from research.reaction_levels.v1.inference import (CONTRACT, ROOT, PredictionRequest,
    read, prefix, calculate, predict_series, calculate_book)
router = APIRouter(prefix='/api/research/level-reaction')
_busy = Lock()


@router.post('/book')
def book(request: PredictionRequest):
    if not _busy.acquire(False):
        raise HTTPException(429, 'A model/book request is already running; retry shortly')
    try:
        return calculate_book(request)
    except (ValueError, OSError, KeyError, IndexError, ImportError, RuntimeError) as exc:
        raise HTTPException(422, f'Reaction book unavailable: {exc}') from exc
    finally:
        _busy.release()


class SeriesRequest(PredictionRequest):
    close_times: list[int] = Field(max_length=57600)
    timeframe_seconds: int = 1


@router.post('/series')
def series(request: SeriesRequest):
    if not _busy.acquire(False):
        raise HTTPException(429, 'A model prediction is already running; retry shortly')
    try:
        base = PredictionRequest(**request.model_dump(exclude={'close_times', 'timeframe_seconds'}))
        return predict_series(base, request.close_times, timeframe_seconds=request.timeframe_seconds)
    except (ValueError, OSError, KeyError, IndexError, ImportError) as exc:
        raise HTTPException(422, f'Prediction unavailable: {exc}') from exc
    finally:
        _busy.release()

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
