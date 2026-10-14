"""Explicit versioned empty state at a selected certified rebuild origin."""
from datetime import date,datetime,time,timedelta
import re

from .contracts import digest,TRADE_POLICY
from .checkpoints import NY
from .recipe import verify_definition,verify_engine
from . import v7
from src.market_engine.streaming_level_book import StreamingLevelBook,EXTRACTION_VERSION
from src.market_engine.historical_level_checkpoint import digest as book_digest
from src.market_engine.historical_session_levels import Settings
from src.market_engine.reaction_band import CONFIG

VERSION='explicit-causal-rebuild-origin-v1'


def binding(listing,ticker,origin,recipe_hash,source_hash):
    if (type(listing) is not int or not 0<listing<2**64 or type(ticker) is not str
            or not re.fullmatch(r'[A-Z0-9._-]{1,32}',ticker) or type(origin) is not date or origin<date(2019,1,2)):
        raise ValueError('Invalid explicit rebuild origin')
    for value in (recipe_hash,source_hash):
        if type(value) is not str or not re.fullmatch('[0-9a-f]{64}',value):
            raise ValueError('Rebuild origin requires frozen recipe/source hashes')
    value=dict(version=VERSION,listing_key=listing,ticker=ticker,origin=str(origin),recipe_hash=recipe_hash,
        source_hash=source_hash,trade_policy=TRADE_POLICY)
    # The causal empty-state predecessor excludes the full source-day digest:
    # an AH correction must not change an otherwise identical PM state.
    origin_hash=digest({k:v for k,v in value.items() if k!='source_hash'})
    bound=dict(value,origin_hash=origin_hash)
    return dict(bound,certificate_hash=digest(bound))


def verify(row,definition,rules):
    expected=binding(row['listing_key'],row['ticker'],date.fromisoformat(row['origin']),
        verify_definition(definition,rules),row['source_hash'])
    if row!=expected:raise ValueError('Explicit rebuild origin differs from its binding')
    return expected


def engine(row,definition,rules):
    verify(row,definition,rules)
    day=date.fromisoformat(row['origin']);params=definition['v7_parameters']
    start=datetime.combine(day,time(4),NY).timestamp();end=datetime.combine(day,time(20),NY).timestamp()
    prior=dict(ticker=row['ticker'],session=str(day-timedelta(days=1)),available_at=start,
        source_extraction_version=EXTRACTION_VERSION,band_config={**CONFIG,'coverage':params['coverage']},
        input_policy=TRADE_POLICY,levels=[])
    prior['checkpoint_hash']=book_digest(prior)
    value=StreamingLevelBook(prior,ticker=row['ticker'],session=str(day),start=start,end=end,
        settings=Settings(**params['settings']),discovery_prominence=params['discovery_prominence'],coverage=params['coverage'])
    verify_engine(value,definition)
    authority=v7.StartAuthority.genesis(value,row['origin_hash'])
    return v7.StartAuthority(authority.engine,authority.predecessor_hash,authority.initial_state_hash,row['origin_hash'])
