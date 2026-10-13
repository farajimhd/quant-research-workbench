"""Runnable bounded market producer: one aggregate, independent consumers.

The controller supplies frozen source validation and certified predecessor
state. Successful SQL alone cannot publish a complete unit; readback and
ownership checks are mandatory before every receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime,timezone
from uuid import uuid4

from . import market,v7,schema,indicators,indicator_state as state_publication
from .contracts import digest,identifier,literal
from .execution import Unit,execute
from .publication import Publisher,Scope
from .checkpoints import CheckpointBinding


@dataclass(frozen=True)
class MarketRequest:
    unit: market.MarketUnit
    dataset_hash: str
    source_hash: str
    rules: tuple
    prior_indicators: dict
    prior_indicator_hash: str
    prior_v7_hash: str
    regular_close: datetime
    bootstrap_indicators: bool=False
    recipe_definition: dict|None=None
    adjustment_hash: str=''

    def __post_init__(self):
        for value in (self.dataset_hash,self.source_hash,self.prior_indicator_hash,self.prior_v7_hash):
            if len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('Invalid producer dependency hash')
        if self.regular_close.tzinfo is None or not self.rules:raise ValueError('Calendar close/rules are required')
        if not self.prior_indicators and not self.bootstrap_indicators:
            raise ValueError('Indicator predecessor is absent; explicit bootstrap proof is required')
        if self.adjustment_hash and (len(self.adjustment_hash)!=64 or any(c not in '0123456789abcdef' for c in self.adjustment_hash)):
            raise ValueError('Invalid corporate adjustment dependency hash')

    def parent(self,product,parents=()):
        version='research-market-v7-prefix-v2' if product=='v7' else 'research-market-continuous-wilder-v2' if product=='technical' else 'research-market-producer-v1'
        return digest(dict(version=version,dataset_hash=self.dataset_hash,
            source=self.source_hash,rules=digest(self.rules),product=product,
            predecessor=self.prior_indicator_hash if product=='technical' else self.prior_v7_hash if product=='v7' else '',
            parents=parents,regular_close=self.regular_close.astimezone(timezone.utc).isoformat() if product=='v7' else '',
            adjustment=self.adjustment_hash if product in ('technical','v7') else ''))


def indicator_state(query,database,unit,attempt,prior,*,bar_attempt):
    """Price-empty frames carry the verified predecessor; no fabricated rows."""
    from pipelines.market_sip.events.market_day_sql import FRAMES
    names=indicators.STATE_FIELDS
    persisted=tuple(n for n in names if n!='close')
    rows=query(f"SELECT i.resolution_ms resolution_ms,{','.join('argMax(i.'+n+',i.bucket_index) AS '+n for n in persisted)},"
               'argMax(b.close_int/10000.,i.bucket_index) close FROM '
               f'{identifier(database)}.research_bars_v1 b INNER JOIN '
               f'{identifier(database)}.research_indicators_v1 i USING '
               '(dataset_id,session_date,listing_key,resolution_ms,bucket_index) '
               f"WHERE b.dataset_id=toUUID({literal(unit.dataset_id)}) AND b.session_date=toDate({literal(unit.day)}) "
               f'AND b.listing_key={unit.listing_key} AND i.attempt_id=toUUID({literal(attempt)}) '
               f'AND b.attempt_id=toUUID({literal(bar_attempt)}) GROUP BY i.resolution_ms')
    result={int(r['resolution_ms']):{n:int(r[n]) if n=='sample_count' else float(r[n]) for n in names} for r in rows}
    for frame in FRAMES:
        if frame not in result and frame in prior:result[frame]=dict(prior[frame])
    if result and set(result)!=set(FRAMES):raise ValueError('Current/prior indicator state lacks a required frame')
    return result


def validate_recipe(request,database,*,product=None):
    if product in ('technical','v7') and not request.adjustment_hash and not database.startswith('certified_research_test_'):
        raise ValueError('Production state continuation requires certified adjustment lineage')
    if request.recipe_definition is None:
        if not database.startswith('certified_research_test_'):
            raise ValueError('Production market execution requires the frozen concrete state recipe')
    else:
        from .recipe import verify_definition
        if verify_definition(request.recipe_definition,request.rules)!=request.dataset_hash:
            raise ValueError('Market request hash differs from its frozen state recipe')


def market_execution(request: MarketRequest,client_factory,claim,source_guard,engine_factory,*,database='arte',
                     query_memory_bytes=2*1024**3,fixture_source=None):
    """Expose stages to either the bounded pilot or the campaign's global pool."""
    identifier(database);validate_recipe(request,database)
    if fixture_source is not None and not database.startswith('certified_research_test_'):
        raise ValueError('Fixture source overrides are forbidden outside isolated test namespaces')
    base=request.unit
    def handler(u,parents,stop):
        client=client_factory()
        try:
            query=client.query
            claim.check();source_guard()
            validate_recipe(request,database,product=u.product)
            if stop.is_set():raise InterruptedError('Producer stopped before starting a stage')
            publisher=Publisher(query,database,claim)
            schema.verify_auxiliary_storage(query,database,tuple(market.STAGING.values()))
            if u.product=='aggregate':
                # If both products have receipts, no canonical scan is needed.
                found={}
                for product in ('broker_100ms','bars'):
                    receipt=publisher.receipt(Scope(base.dataset_id,base.day,base.listing_key,product,request.parent(product)))
                    if receipt:found[product]=receipt
                if len(found)==2:return dict(reused=True,receipts=found,attempt=base.attempt_id)
                unit=market.MarketUnit(base.dataset_id,base.day,base.ticker,base.listing_key,str(uuid4()))
                query(market.aggregate_sql(unit,request.rules,database,source=fixture_source),read=False)
                for sql in market.bar_queries(unit,database):
                    claim.check();query(sql,read=False)
                claim.check();source_guard()
                return dict(reused=False,unit=unit,receipts=found,attempt=unit.attempt_id)
            if u.product in ('bars','broker_100ms'):
                stage=parents['aggregate']
                if u.product in stage['receipts']:return stage['receipts'][u.product]
                unit=stage['unit'];scope=Scope(base.dataset_id,base.day,base.listing_key,u.product,request.parent(u.product))
                expected={k:int(v) for k,v in query(market.expected_evidence_sql(unit,u.product,database))[0].items()}
                query(market.normalized_insert(unit,u.product,database),read=False)
                source_guard();claim.check()
                return publisher.publish(scope,unit.attempt_id,expected=expected,
                                         empty_proof=digest(dict(source=request.source_hash,product=u.product)))
            bar_receipt=parents['bars'];parent=request.parent(u.product,(bar_receipt['output_hash'],))
            scope=Scope(base.dataset_id,base.day,base.listing_key,u.product,parent)
            old=publisher.receipt(scope)
            if old:
                if u.product=='technical':return state_publication.publish(request,bar_receipt,old,query,database,claim,source_guard)
                return old
            unit=market.MarketUnit(base.dataset_id,base.day,base.ticker,base.listing_key,str(uuid4()))
            if u.product=='technical':
                source=(f'SELECT session_date,resolution_ms,bucket_index,close_int,high_int,low_int '
                    f'FROM {database}.research_bars_v1 WHERE '
                    f'dataset_id=toUUID({literal(base.dataset_id)}) AND session_date=toDate({literal(base.day)}) '
                    f'AND listing_key={base.listing_key} AND attempt_id=toUUID({literal(bar_receipt["attempt_id"])}) '
                    'AND price_valid=1 AND extremes_valid=1')
                query(indicators.technical_sql(database,unit,source,request.prior_indicators),read=False)
                expected={k:int(v) for k,v in query(market.expected_evidence_sql(unit,'technical',database))[0].items()}
                query(market.normalized_insert(unit,'technical',database),read=False)
            elif u.product=='v7':
                engine=engine_factory()
                if isinstance(engine,v7.StartAuthority):engine=engine.check(request.prior_v7_hash,adjustment_hash=request.adjustment_hash)
                elif not database.startswith('certified_research_test_'):
                    raise ValueError('V7 production requires certified start-state authority')
                if request.recipe_definition is not None:
                    from .recipe import verify_engine
                    verify_engine(engine,request.recipe_definition)
                rows=query(v7.bars_sql(base.dataset_id,bar_receipt['attempt_id'],base.listing_key,base.day,database))
                origin=v7.input_origin_hash(request.dataset_hash,base.ticker,base.listing_key,base.day)
                binding=CheckpointBinding(request.dataset_hash,origin,request.prior_v7_hash,base.ticker,base.day)
                outputs,_=v7.generate(engine,v7.decode_bars(rows),binding,regular_close=request.regular_close)
                expected={k:int(v) for k,v in query(v7.expected_evidence_sql(outputs,base.dataset_id,unit.attempt_id,
                    base.listing_key,base.day,regular_close=request.regular_close))[0].items()}
                query(v7.insert_sql(outputs,base.dataset_id,unit.attempt_id,base.listing_key,base.day,database,
                                    regular_close=request.regular_close),read=False)
            else:raise ValueError('Unexpected market producer product')
            source_guard();claim.check()
            receipt=publisher.publish(scope,unit.attempt_id,expected=expected,
                                     empty_proof=digest(dict(source=request.source_hash,bar=bar_receipt['output_hash'],product=u.product)))
            if u.product=='technical':return state_publication.publish(request,bar_receipt,receipt,query,database,claim,source_guard)
            return receipt
        finally:client.close()
    units=(Unit('aggregate','aggregate',memory_bytes=query_memory_bytes),
           Unit('bars','bars',('aggregate',),query_memory_bytes),
           Unit('liquidity','broker_100ms',('aggregate',),query_memory_bytes),
           Unit('technical','technical',('bars',),query_memory_bytes),
           Unit('v7','v7',('bars',),query_memory_bytes))
    return units,handler


def run_market(request: MarketRequest,client_factory,claim,source_guard,engine_factory,*,database='arte',
               workers=2,memory_bytes=4*1024**3,query_memory_bytes=2*1024**3,stop=None,progress=None,
               fixture_source=None):
    """Bounded standalone pilot using the exact campaign stage implementations."""
    units,handler=market_execution(request,client_factory,claim,source_guard,engine_factory,database=database,
        query_memory_bytes=query_memory_bytes,fixture_source=fixture_source)
    return execute(units,{p:handler for p in ('aggregate','bars','broker_100ms','technical','v7')},
                   workers=workers,memory_bytes=memory_bytes,stop=stop,progress=progress)
