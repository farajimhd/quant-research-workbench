"""HTTP projection only; causal book construction lives in market_engine."""
from datetime import date,datetime
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from src.backend.qmd_gateway_client import qmd_level_book_v7,qmd_history_get_json,qmd_history_post_json,qmd_post_json
from src.backend.swing_book_source import NY

router=APIRouter(prefix='/api/research/level-book-v7')


class BookRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    book_id:Literal['level-book-v7']='level-book-v7'
    ticker:str=Field(pattern=r'^[A-Z0-9.\- ]{1,30}$')
    session_date:date
    time_et:str=Field(pattern=r'^\d{2}:\d{2}:\d{2}$')
    mode:Literal['history','live']='history'


@router.get('/catalog')
def books():
    try:return qmd_history_get_json('/level-book-v7/catalog',timeout=30)
    except Exception as exc:raise HTTPException(503,str(exc)) from exc


@router.post('/book')
def snapshot(request:BookRequest):
    try:
        at=datetime.fromisoformat(request.session_date.isoformat()+'T'+request.time_et).replace(tzinfo=NY)
        return qmd_level_book_v7(request.ticker,at,mode=request.mode,include_segments=True)
    except Exception as exc:raise HTTPException(503,str(exc)) from exc


@router.post('/chart-checkpoint')
def chart_checkpoint(request:BookRequest):
    try:
        at=datetime.fromisoformat(request.session_date.isoformat()+'T'+request.time_et).replace(tzinfo=NY)
        fetch=qmd_history_post_json if request.mode=='history' else qmd_post_json
        return fetch('/level-book-v7/chart-checkpoint',dict(ticker=request.ticker,as_of=at.isoformat()),timeout=180)
    except Exception as exc:raise HTTPException(503,str(exc)) from exc
