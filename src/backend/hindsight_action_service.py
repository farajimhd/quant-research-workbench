"""Bounded action-value research on canonical historical events."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from threading import Lock
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.backend.qmd_gateway_client import qmd_history_base_url
from src.market_engine.historical_source import QmdHistoricalEventSource
from src.market_engine.hindsight_actions import ActionGrid, solve_actions
from src.backend.hindsight_service import calculate as calculate_hindsight, HindsightRequest

router=APIRouter(prefix='/api/research/hindsight-actions',tags=['hindsight research'])
_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='hindsight-actions')
_lock=Lock()
_jobs={}
NY=ZoneInfo('America/New_York')


class ActionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    ticker:str=Field(min_length=1,max_length=20,pattern=r'^[A-Za-z0-9.\-]+$')
    session_date:date
    start_time:time=time(4)
    window_minutes:int=Field(default=30,ge=1,le=120)
    lookback_seconds:float=Field(default=2,ge=0,le=30,allow_inf_nan=False)
    cost_bps:float=Field(default=0,ge=0,le=100,allow_inf_nan=False)
    max_spread_bps:float=Field(default=150,ge=0,le=1000,allow_inf_nan=False)

    @model_validator(mode='after')
    def window(self):
        if self.start_time.tzinfo or self.start_time.second or self.start_time.microsecond:
            raise ValueError('Start time must be a whole minute in New York local time')
        start,end=self.bounds()
        if self.start_time<time(4) or end>datetime.combine(self.session_date,time(20),NY):
            raise ValueError('Research window must be within 04:00-20:00 New York')
        if datetime.combine(self.session_date,time(20),NY)>datetime.now(timezone.utc):raise ValueError('MACD hindsight requires a completed historical session')
        return self

    def bounds(self):
        start=datetime.combine(self.session_date,self.start_time,NY)
        return start,start+timedelta(minutes=self.window_minutes)


async def calculate_actions(request,progress=lambda **kwargs:None):
    start,end=request.bounds();started=monotonic()
    base=await calculate_hindsight(HindsightRequest(ticker=request.ticker,session_date=request.session_date,lookback_seconds=request.lookback_seconds),progress)
    targets=base['positions']
    # Reuse the actual base labels, including moves below the visual 5% filter.
    relevant=[]
    for side in ('long','short'):
        future=sorted((p for p in targets if p['direction']==side and p['exit_time']>start.timestamp()),key=lambda p:p['exit_time'])
        relevant.extend(p for p in future if p['exit_time']<=end.timestamp())
        after=next((p for p in future if p['exit_time']>end.timestamp()),None)
        if after:relevant.append(after)
    lookahead_end=datetime.fromtimestamp(max([end.timestamp()]+[p['exit_time'] for p in relevant]),NY)
    source=QmdHistoricalEventSource(qmd_history_base_url(),start=start-timedelta(seconds=10),
        end=lookahead_end+timedelta(microseconds=1),tickers=[request.ticker.upper()],batch_size=100000)
    sampler=ActionGrid(start.timestamp(),end.timestamp(),target_times=[p['exit_time'] for p in relevant])
    async for rows in source.stream_rows():
        for row in rows:sampler.observe(row)
        progress(stage='events',events=sampler.counts['events'],through=str(sampler.last),elapsed_seconds=monotonic()-started)
        if monotonic()-started>600:raise RuntimeError('Research time budget exceeded; no partial labels published')
    grid=sampler.finish()
    progress(stage='values',events=sampler.counts['events'])
    parameters=request.model_dump(exclude={'ticker','session_date','start_time','window_minutes','lookback_seconds'})
    result=await asyncio.to_thread(solve_actions,grid,targets=relevant,target_quotes=sampler.target_quotes,decision_end=end.timestamp(),**parameters)
    return dict(result,ticker=request.ticker.upper(),session_date=str(request.session_date),
        start=start.isoformat(),end=end.isoformat(),label_available_at=base['end'],source_end=lookahead_end.isoformat(),
        base_hindsight=dict(algorithm=base['algorithm'],parameters=base['parameters'],source_revision=base['source_revision'],macd_provenance=base['macd_provenance']),
        parameters=request.model_dump(mode='json'),source_revision=source.source_revision,
        source_counts=dict(sampler.counts),elapsed_seconds=monotonic()-started,
        limitations=['Observed NBBO sizes are not guaranteed fills; no queue or market-impact model.',
            'Fixed one-unit positions; no sizing, capital allocation or participation schedule.',
            'No market-relative rank: this result covers one ticker.',
            'Independent overlapping entry opportunities; profits must not be summed as portfolio returns.',
            'Gross profit includes quoted spread; displayed net profit also deducts the configured fees.',
            'No fixed holding cap: each direction uses its next base MACD hindsight exit.',
            'MACD targets use eligible trade extrema; action values use quotes at those exact targets.',
            'The base visual 5% filter does not remove targets from calculation.'])


def _run(job_id,request):
    def update(**kwargs):
        with _lock:_jobs[job_id].update(kwargs)
    update(status='running')
    try:update(status='completed',result=asyncio.run(calculate_actions(request,update)))
    except Exception as exc:update(status='failed',error=str(exc))


@router.post('')
def start_actions(request:ActionRequest):
    key=request.model_dump_json()
    with _lock:
        for job in _jobs.values():
            if job['key']==key and job['status'] in ('queued','running'):
                return {k:v for k,v in job.items() if k!='key'}
        if sum(j['status'] in ('queued','running') for j in _jobs.values())>=3:
            raise HTTPException(429,'Action-value queue is full')
        while len(_jobs)>=4:
            old=next((k for k,j in _jobs.items() if j['status'] in ('completed','failed')),None)
            if old is None:raise HTTPException(429,'Action-value queue is full')
            del _jobs[old]
        job_id=str(uuid4());job=dict(id=job_id,key=key,status='queued',events=0)
        _jobs[job_id]=job
        _pool.submit(_run,job_id,request)
        return {k:v for k,v in job.items() if k!='key'}


@router.get('/{job_id}')
def action_status(job_id:str):
    with _lock:
        if job_id not in _jobs:raise HTTPException(404,'Action-value job expired; generate again')
        return {k:v for k,v in _jobs[job_id].items() if k!='key'}
