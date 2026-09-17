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
    cost_bps:float=Field(default=5,ge=0,le=100,allow_inf_nan=False)
    max_spread_bps:float=Field(default=150,ge=0,le=1000,allow_inf_nan=False)
    risk_bps_per_second:float=Field(default=.01,ge=0,le=10,allow_inf_nan=False)

    @model_validator(mode='after')
    def window(self):
        if self.start_time.tzinfo or self.start_time.second or self.start_time.microsecond:
            raise ValueError('Start time must be a whole minute in New York local time')
        start,end=self.bounds()
        if self.start_time<time(4) or end>datetime.combine(self.session_date,time(20),NY):
            raise ValueError('Research window must be within 04:00-20:00 New York')
        if end>datetime.now(timezone.utc):raise ValueError('Select a completed historical window')
        return self

    def bounds(self):
        start=datetime.combine(self.session_date,self.start_time,NY)
        return start,start+timedelta(minutes=self.window_minutes)


async def calculate_actions(request,progress=lambda **kwargs:None):
    start,end=request.bounds();started=monotonic()
    # Ten-second pre-roll supplies trailing activity at the first decision.
    source=QmdHistoricalEventSource(qmd_history_base_url(),start=start-timedelta(seconds=10),
        end=end+timedelta(microseconds=1),tickers=[request.ticker.upper()],batch_size=100000)
    sampler=ActionGrid(start.timestamp(),end.timestamp())
    async for rows in source.stream_rows():
        for row in rows:sampler.observe(row)
        progress(stage='events',events=sampler.counts['events'],through=str(sampler.last),elapsed_seconds=monotonic()-started)
        if monotonic()-started>600:raise RuntimeError('Research time budget exceeded; no partial labels published')
    grid=sampler.finish()
    progress(stage='values',events=sampler.counts['events'])
    parameters=request.model_dump(exclude={'ticker','session_date','start_time','window_minutes'})
    result=await asyncio.to_thread(solve_actions,grid,**parameters)
    return dict(result,ticker=request.ticker.upper(),session_date=str(request.session_date),
        start=start.isoformat(),end=end.isoformat(),label_available_at=end.isoformat(),
        parameters=request.model_dump(mode='json'),source_revision=source.source_revision,
        source_counts=dict(sampler.counts),elapsed_seconds=monotonic()-started,
        limitations=['Observed NBBO sizes are not guaranteed fills; no queue or market-impact model.',
            'Fixed one-unit positions; no sizing, capital allocation or participation schedule.',
            'No market-relative rank: this result covers one ticker.',
            'Each state/action value assumes optimal future decisions with perfect hindsight.',
            'No stop-loss policy; fixed one-unit exposure and holding cost define risk here.'])


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
