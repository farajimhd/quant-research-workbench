"""Native QMD feature preparation from certified database bars and references.

No optimization shard, flatfile, strategy callback, fill or portfolio input is
accepted. The caller supplies the existing cold-certified source population and
an explicit continuous-indicator origin. I/O belongs to the producer funnel.
"""
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
import numpy as np
import polars as pl

from src.backend.backtest_market_data import CertifiedMarketDayPlan, _verify_market_batch
from src.backend.qmd_columnar_history import _rows
from src.backend.qmd_feature_source_plan import FeatureSourcePlanCache
from src.trading_runtime.arte_market_day_cold_preflight import MarketDayColdAudit
from src.market_engine.feature_source_plan import FeatureDependency, FeatureSourcePlan
from src.market_engine.opening_price_reference import content_hash
from src.market_engine.causal_market_context import prepare_market_context
from src.market_engine.columnar_history_product import prepare_dense_inputs, session_clock_anchor_us
from src.market_engine.causal_volume_history import elapsed_volume_history
from src.market_engine.qmd_feature_projection import project_listing
from src.market_engine.feature_share_basis import price_factor
from src.market_engine.continuous_indicator_features import initial_state, calculate, ContinuousFeatureProjection
from .market_day_sql import literal
from .qmd_feature_reference import read_reference, storage_check
from .opening_price_reference_producer import CertifiedOpeningInput

BAR_COLUMNS=('resolution_ms','bucket_index','open_int','high_int','low_int','close_int',
             'notional','volume','trade_count','price_valid','extremes_valid')
INDICATORS=('bucket_index','macd_line','macd_signal','rsi_14','atr_14','ema_7','ema_26')


def source_audit_ready(audit):
    # Input publication requires certified sources, not permission to launch a
    # Backtest. The latter's active-cutover gate remains owned by Backtest.
    return (type(audit) is MarketDayColdAudit and audit.certificate_parts_on_ssd
        and audit.source_authority_verified and audit.source_storage_verified
        and audit.market_products_verified)


def algorithm_hash():
    from src.market_engine import qmd_feature_catalog, causal_context_features, causal_market_context
    from src.market_engine import continuous_indicator_features, candle_feature_projection, qmd_feature_projection
    from . import qmd_feature_reference
    from src.market_engine import causal_market_history, columnar_history_product, causal_volume_history, feature_share_basis
    modules={m.__name__:sha256(Path(m.__file__).read_bytes()).hexdigest() for m in
        (qmd_feature_catalog,causal_context_features,causal_market_context,continuous_indicator_features,
         candle_feature_projection,qmd_feature_projection,qmd_feature_reference,causal_market_history,
         columnar_history_product,causal_volume_history,feature_share_basis)}
    modules[__name__]=sha256(Path(__file__).read_bytes()).hexdigest()
    return content_hash(modules)


@dataclass(frozen=True)
class QmdFeaturePreparationRequest:
    opening_source: CertifiedOpeningInput
    audit: MarketDayColdAudit
    continuity: tuple[tuple[CertifiedMarketDayPlan, MarketDayColdAudit], ...]
    prior_volume: tuple[CertifiedMarketDayPlan, MarketDayColdAudit]
    tickers: tuple[str, ...]
    first_bucket: int
    stop_bucket: int
    maximum_source_bytes: int
    maximum_context_bytes: int

    def __post_init__(self):
        if (type(self.opening_source) is not CertifiedOpeningInput
                or not source_audit_ready(self.audit)
                or not self.continuity or type(self.continuity) is not tuple
                or any(type(p) is not CertifiedMarketDayPlan or not source_audit_ready(a)
                       for p,a in self.continuity)
                or self.continuity[-1][0]!=self.opening_source.market
                or self.continuity[-1][1]!=self.audit
                or tuple(sorted(set(self.tickers)))!=self.tickers or not self.tickers
                or set(self.tickers)-set(self.opening_source.market.tickers)
                or self.first_bucket != 14400 or not self.first_bucket < self.stop_bucket <= 72000
                or any(type(n) is not int or n<1 for n in
                       (self.maximum_source_bytes,self.maximum_context_bytes))):
            raise ValueError('Feature preparation requires native source, continuity and compute admission')
        days=[p.sessions for p,a in self.continuity]
        if any(len(v)!=1 for v in days) or days!=sorted(set(days)):
            raise ValueError('Continuous feature source sessions must be unique and ordered')
        for p,a in self.continuity:
            if (p.build_id!=a.certificate.build_id or p.definition_hash!=a.certificate.definition_hash
                    or any((u.session_date,u.ticker,u.stage,u.attempt_id,u.output_rows,u.output_hash)
                           not in a.certificate.stages for u in p.units)):
                raise ValueError('Feature source plan differs from its cold certificate')
        from src.market_engine.opening_price_reference import previous_regular_session
        p,a=self.prior_volume
        if (type(p) is not CertifiedMarketDayPlan or not source_audit_ready(a)
                or p.build_id!=a.certificate.build_id
                or p.sessions!=(previous_regular_session(date.fromisoformat(days[-1][0])).day.isoformat(),)
                or any((u.session_date,u.ticker,u.stage,u.attempt_id,u.output_rows,u.output_hash)
                       not in a.certificate.stages for u in p.units)):
            raise ValueError('Feature RVOL requires its separately certified prior session')
        for previous,current in zip(days,days[1:]):
            if previous_regular_session(date.fromisoformat(current[0])).day.isoformat()!=previous[0]:
                raise ValueError('Continuous feature chain skips a certified exchange session')


def _placement(reader):
    required={'arte':('bars_v1','indicators_v1','liquidity_100ms_v1'),
              'q_live':('market_security_float_v1','market_stock_split_v1',
                        'id_symbol_interval_v1',
                        'feature_tradable_universe_snapshot_v2','feature_tradable_universe_snapshot_coverage_v2')}
    storage_check(reader)
    for database,tables in required.items():
        names=','.join(literal(n) for n in tables)
        rows=_rows(reader,f'SELECT name,storage_policy FROM system.tables WHERE database={literal(database)} AND name IN ({names})',len(tables))
        if {r['name'] for r in rows}!=set(tables) or any(r['storage_policy']!='live_market_ssd' for r in rows):
            raise ValueError('QMD feature sources require explicit SSD storage')
        if _rows(reader,f'SELECT table,disk_name FROM system.parts WHERE database={literal(database)} AND table IN ({names}) AND active AND disk_name!=\'live_market_ssd\' LIMIT 1',1):
            raise ValueError('QMD feature source parts are outside live_market_ssd')


def _population(reader, request):
    day=request.opening_source.market.sessions[0]
    population=[p for p in request.audit.source_plan['population'] if p['session_date']==day]
    if len(population)!=1:raise ValueError('Feature context lacks its certified preopen population')
    saved=population[0];certificate=saved['certificate'];snapshot=certificate['snapshot_id']
    utc=lambda v: datetime.fromisoformat(str(v).replace('Z','+00:00')).replace(tzinfo=timezone.utc) if datetime.fromisoformat(str(v).replace('Z','+00:00')).tzinfo is None else datetime.fromisoformat(str(v).replace('Z','+00:00'))
    cutoff=datetime.combine(date.fromisoformat(day),time(4),ZoneInfo('America/New_York'))
    if not utc(certificate['captured_at_utc'])<=utc(certificate['available_at_utc'])<cutoff or utc(certificate['cutoff_utc'])!=cutoff:
        raise ValueError('Feature universe snapshot is not known before session opening')
    where=f'session_date=toDate({literal(day)}) AND snapshot_id={literal(snapshot)}'
    summary=_rows(reader,'SELECT count() AS n,countIf(is_tradable) AS tradable,'
        'toString(sum(cityHash64(tuple(ticker,symbol_id,listing_id,security_id,is_tradable,exclusion_reason,source_run_id,captured_at_utc)))) AS hash '
        f'FROM q_live.feature_tradable_universe_snapshot_v2 WHERE {where}',1)
    if (len(summary)!=1 or int(summary[0]['n'])!=certificate['row_count']
            or int(summary[0]['tradable'])!=certificate['tradable_count']
            or str(summary[0]['hash'])!=str(certificate['source_hash'])):
        raise ValueError('Pinned feature universe snapshot changed')
    rows=_rows(reader,f'SELECT listing_id,ticker FROM q_live.feature_tradable_universe_snapshot_v2 '
        f'WHERE {where} AND is_tradable=1 ORDER BY ticker,listing_id LIMIT 25001',25000)
    universe=tuple(sorted({r['ticker'] for r in rows}))
    available=set(request.opening_source.market.tickers)
    if (available-set(universe) or len(universe)!=saved['tradable_tickers']
            or len(available)!=saved['selected_ticker_days']
            or len(set(universe)-available)!=saved['tradable_without_canonical_events']):
        raise ValueError('Feature context differs from its complete preopen census')
    return universe,tuple(sorted(set(universe)-available)),content_hash(dict(rows=rows,certificate=certificate)),snapshot


def _read_unit(reader, plan, ticker, stage, columns, *, stop_bucket):
    units=[u for u in plan.units if u.ticker==ticker and u.stage==stage]
    if len(units)!=1:raise ValueError('Missing or ambiguous QMD feature source unit')
    unit=units[0]
    table={'bars':'bars_v1','technical':'indicators_v1','broker_100ms':'liquidity_100ms_v1'}[stage]
    _verify_market_batch(plan,stage,table,unit.session_date,(unit,),reader)
    records=[]
    for first in range(14400,stop_bucket,2048):
        where=(f'build_id={literal(plan.build_id)} AND session_date=toDate({literal(unit.session_date)}) '
            f'AND ticker={literal(ticker)} AND attempt_id=toUUID({literal(unit.attempt_id)}) '
            f'AND bucket_index>={first} AND bucket_index<{min(first+2048,stop_bucket)}')
        if stage!='broker_100ms':where+=' AND resolution_ms=1000'
        rows=_rows(reader,f'SELECT {",".join(columns)} FROM arte.{table} WHERE {where} ORDER BY bucket_index LIMIT 2049',2049)
        if len(rows)>2048:raise ValueError('Feature source contains duplicate or excessive clocks')
        records.extend(rows)
    schema={n:pl.Float64 if n in ('notional','volume','macd_line','macd_signal','rsi_14','atr_14','ema_7','ema_26') else pl.Int64 for n in columns}
    frame=pl.DataFrame(records,schema=schema)
    if frame['bucket_index'].n_unique()!=frame.height:raise ValueError('Duplicate feature source clocks')
    return frame,unit


def _listing_identity(reader, listing, day):
    rows=_rows(reader,'SELECT ticker_normalized,symbol_interval_id,source_content_sha256 '
        f'FROM q_live.id_symbol_interval_v1 FINAL WHERE listing_id={literal(listing.listing_id)} '
        f'AND security_id={literal(listing.security_id)} AND mapping_status=\'mapped\' AND is_deleted=0 '
        f'AND valid_from_date<=toDate({literal(day)}) AND (valid_to_date_exclusive IS NULL '
        f'OR valid_to_date_exclusive>toDate({literal(day)})) ORDER BY symbol_interval_id LIMIT 3',3)
    if len(rows)>1:raise ValueError('Ambiguous feature listing identity')
    return rows


@dataclass(frozen=True)
class PreparedQmdFeatureDay:
    request: QmdFeaturePreparationRequest
    plan: FeatureSourcePlan
    bars: object
    indicators: object
    references: object
    prior_bars: object
    continuous: object
    contexts: object
    quotes: object
    activity: object
    _plan_cache: object = field(default_factory=FeatureSourcePlanCache,repr=False,compare=False)

    def verify_sources(self, reader, *, ownership):
        ownership.assert_owner();_placement(reader)
        universe,unavailable,observed,_=_population(reader,self.request)
        expected=next(v.content_hash for v in self.plan.dependencies if v.source_kind=='universe')
        if observed!=expected:raise ValueError('Feature universe changed during preparation')
        seen=set()
        for market,audit in self.request.continuity+(self.request.prior_volume,):
            key=(market.build_id,market.sessions)
            if key in seen:continue
            seen.add(key)
            for stage,table in (('bars','bars_v1'),('technical','indicators_v1'),('broker_100ms','liquidity_100ms_v1')):
                dependencies={(v.session_date.isoformat(),v.ticker,v.source_identity) for v in self.plan.dependencies
                              if v.source_database=='arte' and v.source_table==table}
                units=tuple(u for u in market.units if u.stage==stage
                            and (u.session_date,u.ticker,u.attempt_id) in dependencies)
                for first in range(0,len(units),64):
                    ownership.assert_owner()
                    _verify_market_batch(market,stage,table,market.sessions[0],units[first:first+64],reader)
        listings={v.ticker:v for v in self.request.opening_source.listings}
        for dependency in self.plan.dependencies:
            if dependency.source_kind in ('identity','prior_identity'):
                actual=_listing_identity(reader,listings[dependency.ticker],dependency.session_date)
                if content_hash(actual)!=dependency.content_hash:
                    raise ValueError('Feature source listing identity changed during preparation')
        for ticker,expected_reference in self.references.items():
            row=listings[ticker]
            actual=read_reference(reader,self.plan.session_date,dict(ticker=ticker,
                listing_id=row.listing_id,symbol_id=row.symbol_id,security_id=row.security_id))
            if actual!=expected_reference:
                raise ValueError('Feature point-in-time reference changed during preparation')
        if algorithm_hash()!=self.plan.algorithm_hash:
            raise ValueError('Feature calculation code changed during preparation')
        ownership.assert_owner()

    def verify(self, reader):
        if self._plan_cache.read(reader,self.plan.plan_hash)!=self.plan:
            raise ValueError('Prepared features differ from their sealed source plan')
        if algorithm_hash()!=self.plan.algorithm_hash:
            raise ValueError('Prepared feature calculation code changed')

    def project(self, source, scope, bars, liquidity):
        if (source.opening_source!=self.request.opening_source or source.ticker not in self.request.tickers
                or (scope.first_bucket,scope.stop_bucket)!=(self.plan.first_bucket,self.plan.stop_bucket)):
            raise ValueError('Feature materialization differs from its certified session scope')
        ticker=source.ticker
        dense=elapsed_volume_history(prepare_dense_inputs(scope,bars,liquidity),source.volume_window_seconds)
        seed,splits,fundamentals,_=self.references[ticker]
        return project_listing(scope,dense,bars=self.bars[ticker],indicators=self.indicators[ticker],
            prior_bars=self.prior_bars[ticker],seed=seed,splits=splits,fundamentals=fundamentals,
            quotes=self.quotes[ticker],activity=self.activity[ticker],
            continuous_history=self.continuous[ticker],context=self.contexts[ticker],
            opening_splits=self.references[ticker][3].get('splits',[]),
            prior_volume_day=date.fromisoformat(self.request.prior_volume[0].sessions[0]))


def prepare_feature_day(reader, request, *, ownership, progress=None, stop_requested=None):
    if type(request) is not QmdFeaturePreparationRequest:raise TypeError('Native feature request required')
    if (progress is not None and not callable(progress)) or (stop_requested is not None and not callable(stop_requested)):
        raise ValueError('Feature preparation requires callable progress and cooperative control')
    def report(completed,active=1,**extra):
        if progress is not None:
            progress(dict(stage='qmd_feature_sources',active=active,queued=len(request.opening_source.market.tickers)-completed-active,
                completed=completed,skipped=0,retried=0,failed=0,**extra))
    ownership.assert_owner();_placement(reader)
    universe,unavailable,universe_hash,snapshot=_population(reader,request)
    market=request.opening_source.market;day=date.fromisoformat(market.sessions[0])
    # Source frames and output context are separately admitted; no hidden spill
    # or research-bank fallback is permitted.
    expected=sum(min(u.output_rows,request.stop_bucket-14400) for u in market.units if u.stage=='bars')*40
    if expected>request.maximum_source_bytes:raise MemoryError('Certified source frames exceed feature admission')
    dependencies=[FeatureDependency('universe','q_live','feature_tradable_universe_snapshot_v2',day,'',snapshot,len(universe),universe_hash)]
    bars={};indicators={};references={};prior={};continuous={};peers={};quotes={};activity={}
    listing={v.ticker:v for v in request.opening_source.listings}
    anchor=int(datetime.combine(day,time(4),ZoneInfo('America/New_York')).timestamp())-14400
    for index,ticker in enumerate(market.tickers):
        if stop_requested is not None and stop_requested():
            report(index,active=0,stopped=True)
            raise InterruptedError('QMD feature preparation stopped before a source unit')
        report(index,ticker=ticker)
        ownership.assert_owner()
        current,unit=_read_unit(reader,market,ticker,'bars',BAR_COLUMNS,stop_bucket=request.stop_bucket)
        dependencies.append(FeatureDependency('bars','arte','bars_v1',day,ticker,unit.attempt_id,unit.output_rows,content_hash(asdict(unit))))
        peers[ticker]=current.select(pl.Series('second',anchor+current['bucket_index'].to_numpy()+1),
            (pl.col('close_int')/10000.).alias('close'),'notional','price_valid')
        if ticker not in request.tickers:continue
        bars[ticker]=current
        technical,unit=_read_unit(reader,market,ticker,'technical',INDICATORS,stop_bucket=request.stop_bucket)
        indicators[ticker]=technical
        dependencies.append(FeatureDependency('technical','arte','indicators_v1',day,ticker,unit.attempt_id,unit.output_rows,content_hash(asdict(unit))))
        row=listing[ticker]
        references[ticker]=read_reference(reader,day,dict(ticker=ticker,listing_id=row.listing_id,symbol_id=row.symbol_id,security_id=row.security_id))
        evidence=references[ticker][3]
        dependencies.extend((
            FeatureDependency('float','q_live','market_security_float_v1',day,ticker,row.listing_id,
                int(evidence.get('float') is not None),content_hash(evidence.get('float'))),
            FeatureDependency('split','q_live','market_stock_split_v1',day,ticker,row.listing_id,
                len(evidence.get('splits',[])),content_hash(evidence.get('splits',[]))),
            FeatureDependency('structure','arte','structural_level_coverage_v7',day,ticker,row.listing_id,
                int(references[ticker][0] is not None),content_hash(references[ticker][0]))))
        last_day=None;state,counts=initial_state();continuous_values=None
        for p,a in request.continuity:
            source_day=date.fromisoformat(p.sessions[0])
            identity=_listing_identity(reader,row,source_day)
            dependencies.append(FeatureDependency('identity','q_live','id_symbol_interval_v1',source_day,
                ticker,row.listing_id,len(identity),content_hash(identity)))
            source_ticker=identity[0]['ticker_normalized'] if identity else None
            if source_ticker not in p.tickers:
                continue
            candles,unit=_read_unit(reader,p,source_ticker,'bars',BAR_COLUMNS,stop_bucket=request.stop_bucket if source_day==day else 72000)
            if source_day!=day:
                dependencies.append(FeatureDependency('continuity','arte','bars_v1',source_day,source_ticker,unit.attempt_id,unit.output_rows,content_hash(asdict(unit))))
            factor=1.
            factor=price_factor(evidence.get('splits',[]),last_day,source_day) if last_day is not None else 1.
            emit=np.full(candles.height,source_day==day,dtype=np.bool_)
            values,state,counts=calculate(candles['close_int'].to_numpy()/10000.,candles['high_int'].to_numpy()/10000.,
                candles['low_int'].to_numpy()/10000.,candles['price_valid'].to_numpy()!=0,
                candles['extremes_valid'].to_numpy()!=0,state,counts,factor,emit)
            if source_day==day:continuous_values=values
            last_day=source_day
        if continuous_values is None:raise ValueError('Current listing lacks effective continuous feature identity')
        continuous[ticker]=ContinuousFeatureProjection(day,current['bucket_index'].to_numpy(),continuous_values)
        prior_plan=request.prior_volume[0]
        previous=pl.DataFrame(schema={'bucket_index':pl.Int64,'volume':pl.Float64})
        prior_day=date.fromisoformat(prior_plan.sessions[0])
        old_identity=_listing_identity(reader,row,prior_day)
        dependencies.append(FeatureDependency('prior_identity','q_live','id_symbol_interval_v1',prior_day,
            ticker,row.listing_id,len(old_identity),content_hash(old_identity)))
        old_ticker=old_identity[0]['ticker_normalized'] if old_identity else None
        if old_ticker in prior_plan.tickers:
            previous,prior_unit=_read_unit(reader,prior_plan,old_ticker,'bars',('bucket_index','volume'),stop_bucket=72000)
            dependencies.append(FeatureDependency('prior_volume','arte','bars_v1',date.fromisoformat(prior_plan.sessions[0]),
                old_ticker,prior_unit.attempt_id,prior_unit.output_rows,content_hash(asdict(prior_unit))))
        prior[ticker]=previous.select('bucket_index','volume')
        # Quote/activity source is independently pinned and read in bounded 1s
        # projections of the native 100ms product.
        broker=[u for u in market.units if u.ticker==ticker and u.stage=='broker_100ms']
        if len(broker)!=1:raise ValueError('Missing certified quote/activity source')
        unit=broker[0];_verify_market_batch(market,'broker_100ms','liquidity_100ms_v1',str(day),(unit,),reader)
        dependencies.append(FeatureDependency('quotes','arte','liquidity_100ms_v1',day,ticker,unit.attempt_id,unit.output_rows,content_hash(asdict(unit))))
        quote_rows=[]
        for first in range(request.first_bucket,request.stop_bucket,1024):
            where=(f'build_id={literal(market.build_id)} AND session_date=toDate({literal(day)}) AND ticker={literal(ticker)} '
                f'AND attempt_id=toUUID({literal(unit.attempt_id)}) AND bucket_index>={first*10} AND bucket_index<{min(first+1024,request.stop_bucket)*10}')
            good=f'quote_valid=1 AND bid_int>0 AND ask_int>=bid_int AND quote_timestamp_us>0 AND quote_timestamp_us<=last_event_us AND last_event_us<{anchor}*1000000+(toInt64(bucket_index)+1)*100000'
            quote_rows.extend(_rows(reader,'SELECT intDiv(bucket_index,10) AS bucket,'
                f'argMaxIf(bid_int,bucket_index,{good})/10000. AS bid,argMaxIf(ask_int,bucket_index,{good})/10000. AS ask,'
                f'toFloat64(argMaxIf(quote_timestamp_us,bucket_index,{good})) AS quote_us,sum(execution_volume) AS volume,'
                'sum(execution_notional) AS notional '
                f'FROM arte.liquidity_100ms_v1 WHERE {where} GROUP BY bucket ORDER BY bucket LIMIT 1025',1025))
        q=pl.DataFrame(quote_rows,schema={'bucket':pl.Int64,'bid':pl.Float64,'ask':pl.Float64,'quote_us':pl.Float64,'volume':pl.Float64,'notional':pl.Float64})
        q=q.with_columns((pl.col('bucket')+anchor+1).alias('second'))
        quotes[ticker]=q.filter(pl.col('quote_us')>0).select('second','bid','ask','quote_us')
        trade_counts=current.select((pl.col('bucket_index')+anchor+1).alias('second'),pl.col('trade_count').cast(pl.Float64))
        activity[ticker]=q.select('second','volume','notional').join(trade_counts,on='second',how='full',coalesce=True,validate='1:1').with_columns(
            pl.col('volume','notional','trade_count').fill_null(0.))
        retained=(sum(v.estimated_size() for mapping in (peers,bars,indicators,prior,quotes,activity) for v in mapping.values())
            +sum(v.values.nbytes+v.buckets.nbytes for v in continuous.values()))
        if retained>request.maximum_source_bytes:raise MemoryError('Prepared QMD feature source retention exceeds admission')
    contexts=prepare_market_context(peers,universe=universe,unavailable=unavailable,
        first_second=anchor+request.first_bucket+1,stop_second=anchor+request.stop_bucket+1,
        maximum_bytes=request.maximum_context_bytes,targets=request.tickers)
    plan=FeatureSourcePlan(market.token,algorithm_hash(),day,request.first_bucket,request.stop_bucket,len(universe),
        tuple(sorted(dependencies,key=lambda v:(v.source_kind,v.session_date,v.ticker,v.source_identity))))
    ownership.assert_owner()
    report(len(market.tickers),active=0)
    return PreparedQmdFeatureDay(request,plan,*(MappingProxyType(v) for v in
        (bars,indicators,references,prior,continuous,contexts,quotes,activity)))
