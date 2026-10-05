"""Research-only teacher audit and isolated 1b preview; no training/publication."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from research.rl_trading.v6 import label_audit
from research.rl_trading.v6 import saved_label_audit
from research.rl_trading.v6 import market_teacher_preview as market_preview
from pydantic import BaseModel
from research.rl_trading.v6 import price_action_opportunities as price_action_labels

router = APIRouter(prefix='/api/research/models', tags=['research teacher audit'])


class PreviewRequest(BaseModel):
    day: str
    fee_per_share: float = .005
    threshold_mode: Literal['fee_multiple', 'return'] = 'fee_multiple'
    minimum_net_fee_multiple: float = 2.
    minimum_return: float = .01
    grouping_seconds: float = 30.
    maximum_group_seconds: int = 300


@router.post('/v6/market-preview')
def preview_start(request: PreviewRequest):
    fields = request.model_dump()
    day = fields.pop('day')
    return read(market_preview.start, day, market_preview.Config(**fields))


@router.get('/v6/market-preview/status')
def preview_status(job_id: str):
    return read(market_preview.status, job_id)


@router.get('/v6/market-preview/result')
def preview_result(job_id: str, group_id: int | None = None, search: str = '', offset: int = Query(0, ge=0), selection: Literal['all','selected','rejected'] = 'all'):
    return read(market_preview.result, job_id, group_id, search, offset, selection)


@router.get('/v6/market-preview/chart')
def preview_chart(job_id: str, listing_id: str, start_us: int):
    return read(market_preview.chart, job_id, listing_id, start_us)


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
