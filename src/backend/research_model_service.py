"""Research-only teacher audit endpoints; deliberately no optimizer or writers."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from research.rl_trading.v6 import label_audit

router = APIRouter(prefix='/api/research/models', tags=['research teacher audit'])


def read(call, *args):
    try:
        return call(*args)
    except (ValueError, OSError, KeyError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get('')
def models():
    return label_audit.catalog()


@router.get('/v6/preflight')
def preflight(day: str):
    return read(label_audit.preflight, day)


@router.get('/v6/chart')
def chart(day: str, listing_id: str, episode_uid: str | None = None,
          branch: Literal['flat', 'held'] = 'flat', start_us: int | None = None,
          seconds: int = Query(900, ge=60, le=3600)):
    return read(label_audit.chart, day, listing_id, episode_uid, branch, start_us, seconds)
