"""Calculation fingerprints independent of campaign dates and resource budgets."""
from dataclasses import asdict,fields
from hashlib import sha256
from importlib.metadata import version
from functools import lru_cache
from math import isfinite
import ast,inspect,textwrap,re

from pipelines.market_sip.events import market_day_sql as core
from src.market_engine import streaming_level_book as book,reaction_band,reaction_center,derived_trade_policy,historical_level_checkpoint
from src.market_engine.historical_session_levels import Settings
from . import indicators,checkpoints,v7,source
from .contracts import digest,TRADE_POLICY,REPORTING_REVISION


@lru_cache(maxsize=64)
def code_hash(target):
    """Hash executable structure, ignoring locations, formatting and docstrings."""
    tree=ast.parse(textwrap.dedent(inspect.getsource(target)))
    for node in ast.walk(tree):
        if isinstance(node,(ast.Module,ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
                node.body.pop(0)
    return sha256(ast.dump(tree,include_attributes=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def dependency_versions():
    return tuple((name,version(name)) for name in ('numpy','scipy','msgpack','pandas_market_calendars'))


def parameters(settings=Settings(),*,discovery_prominence=None,coverage=.8):
    if not isinstance(settings,Settings):raise ValueError('Declared V7 settings are required')
    values=asdict(settings)
    if any(field.type is int and type(values[field.name]) is not int for field in fields(settings)):
        raise ValueError('Integer V7 settings require exact integers')
    if (any(type(v) not in (int,float) or not isfinite(v) or v<0 for v in values.values())
            or values['tick']<=0 or values['maximum_candidates']<1
            or type(coverage) not in (int,float) or not isfinite(coverage) or not 0<coverage<1
            or (discovery_prominence is not None and (type(discovery_prominence) not in (int,float)
                or not isfinite(discovery_prominence) or discovery_prominence<=0))):
        raise ValueError('Invalid V7 calculation parameters')
    return dict(settings=values,discovery_prominence=discovery_prominence,coverage=coverage)


def market_recipe(config,rules,*,settings=Settings(),discovery_prominence=None,coverage=.8):
    """Freeze concrete algorithms, input rules, numerical dependencies and state parameters.

    Rolling/short-window selections do not alter EMA/Wilder/V7 state. They
    retain their own product recipes. Dates, workers and memory never alter
    this state recipe. The launcher still must verify source and predecessor
    evidence independently; a matching recipe is not a coverage certificate.
    """
    if tuple(config.resolutions_ms)!=core.FRAMES:
        raise ValueError('Selected-frame execution is unfinished; state recipe requires all supported frames')
    fields=('token_id','modifier_int','update_high_low','update_last','update_volume')
    normalized=[]
    for row in rules:
        if set(row)!=set(fields):raise ValueError('Unexpected or missing canonical condition-rule fields')
        if any(type(row[key]) not in (int,str) or not re.fullmatch(r'-?[0-9]+',str(row[key])) for key in fields):
            raise ValueError('Condition rules must contain exact integers')
        item={key:int(row[key]) for key in fields}
        if (not 0<=item['token_id']<=255 or item['modifier_int']<0
                or any(item[k] not in (0,1) for k in fields[2:])):
            raise ValueError('Invalid canonical condition rule')
        normalized.append(item)
    normalized.sort(key=lambda r:r['token_id'])
    if not normalized or len({r['token_id'] for r in normalized})!=len(normalized):
        raise ValueError('Missing or ambiguous condition rules')
    targets=dict(market_sql=core,aggregate=core.broker_sql,base=core.base_sql,rollup=core.rollup_sql,
        ema=core.ema,continuous_indicators=indicators.technical_sql,wilder_validation=indicators.validate_state,
        v7_engine=book.StreamingLevelBook,causal_boundaries=v7.generate,day_rollover=v7.next_day_engine,
        input_origin=v7.input_origin_hash,boundaries=checkpoints.boundaries,encode=checkpoints.encode,
        restore=checkpoints.restore,reaction_band=reaction_band,reaction_center=reaction_center,
        checkpoint_digest=historical_level_checkpoint.digest,exchange_sessions=source.exchange_sessions,
        eligible_completed_second=derived_trade_policy.eligible_completed_second)
    definition=dict(version='certified-market-state-recipe-v1',trade_policy=TRADE_POLICY,
        reporting_revision=REPORTING_REVISION,frames=list(core.FRAMES),ema_periods=list(core.EMAS),
        v7_version=book.VERSION,extraction_version=book.EXTRACTION_VERSION,
        band_config=dict(reaction_band.CONFIG),input_policy=derived_trade_policy.POLICY,
        checkpoint_codec=checkpoints.CODEC_VERSION,rules=normalized,
        v7_parameters=parameters(settings,discovery_prominence=discovery_prominence,coverage=coverage),
        algorithms={key:code_hash(value) for key,value in targets.items()},
        dependencies=dict(dependency_versions()))
    return dict(hash=digest(definition),definition=definition)


def verify_engine(engine,definition):
    expected=definition['v7_parameters']
    actual=dict(settings=dict(engine.settings),discovery_prominence=engine.discovery_prominence,coverage=engine.coverage)
    if actual!=expected:raise ValueError('V7 engine parameters differ from the frozen state recipe')


def verify_definition(definition,rules):
    from datetime import date
    from .contracts import CampaignConfig
    params=definition['v7_parameters']
    config=CampaignConfig(date(2026,1,1),date(2026,1,1),resolutions_ms=tuple(definition['frames']))
    current=market_recipe(config,rules,settings=Settings(**params['settings']),
        discovery_prominence=params['discovery_prominence'],coverage=params['coverage'])
    if current['definition']!=definition:raise ValueError('Market state recipe differs from loaded algorithms, rules or numerical dependencies')
    return current['hash']
