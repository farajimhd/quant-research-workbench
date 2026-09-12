"""HTTP projection only; causal book construction lives in market_engine."""
from datetime import date
from threading import Lock
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from src.market_engine.level_book_feed import book_at,catalog

router=APIRouter(prefix='/api/research/level-book-v7')
_busy=Lock()


class BookRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    book_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    ticker:str=Field(pattern=r'^[A-Z0-9.\-]{1,20}$')
    session_date:date
    time_et:str=Field(pattern=r'^\d{2}:\d{2}:\d{2}$')


@router.get('/catalog')
def books():
    return catalog()


@router.post('/book')
def snapshot(request:BookRequest):
    if not _busy.acquire(False):raise HTTPException(429,'V7 book is updating; retry shortly')
    try:return book_at(request.book_id,request.ticker,request.session_date.isoformat(),request.time_et)
    except (ValueError,OSError,KeyError,IndexError) as exc:raise HTTPException(422,f'V7 book unavailable: {exc}') from exc
    finally:_busy.release()
