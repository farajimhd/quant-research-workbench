"""Research-only teacher audit endpoints; deliberately no optimizer or writers."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from research.rl_trading.v6 import label_audit
from research.rl_trading.v6 import saved_label_audit
from research.rl_trading.v6 import price_action_opportunities as price_action_labels

router = APIRouter(prefix='/api/research/models', tags=['research teacher audit'])


@router.get('/v6/saved-labels')
def saved_catalog():
    return read(saved_label_audit.catalog)


@router.get('/v6/saved-labels/listings')
def saved_listings(day: str):
    return read(saved_label_audit.listings, day)


@router.get('/v6/saved-labels/metadata')
def saved_metadata(day: str, listing_id: str):
    return read(saved_label_audit.metadata, day, listing_id)


@router.get('/v6/saved-labels/chart')
def saved_chart(day: str, listing_id: str, start_us: int | None = None,
                seconds: int = Query(900,ge=60,le=3600), view: Literal['combined','flat','held','reference']='combined',
                dataset_sha256: str | None = None, candle_offset: int | None = Query(None,ge=0)):
    result = read(saved_label_audit.chart,day,listing_id,start_us,seconds,view,candle_offset)
    if dataset_sha256 is not None and result['dataset_sha256'] != dataset_sha256:
        raise HTTPException(409, 'Published labels changed. Reload Research to use the current dataset.')
    return result


def read(call, *args):
    try:
        return call(*args)
    except (ValueError, OSError, KeyError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get('')
def models():
    return label_audit.catalog()


@router.get('/v6/price-action')
def price_action_metadata(quality_threshold: float = Query(.9, gt=0, le=1)):
    return read(price_action_labels.metadata, quality_threshold)


@router.get('/v6/price-action/chart')
def price_action_chart(start_us: int | None = None,
                       seconds: int = Query(900, ge=60, le=3600),
                       quality_threshold: float = Query(.9, gt=0, le=1),
                       view: Literal['combined','flat','held','reference'] = 'combined'):
    return read(price_action_labels.chart, start_us, seconds, quality_threshold, view)


@router.get('/v6/preflight')
def preflight(day: str):
    return read(label_audit.preflight, day)


@router.get('/v6/chart')
def chart(day: str, listing_id: str, episode_uid: str | None = None,
          branch: Literal['flat', 'held'] = 'flat', start_us: int | None = None,
          seconds: int = Query(900, ge=60, le=3600)):
    return read(label_audit.chart, day, listing_id, episode_uid, branch, start_us, seconds)
