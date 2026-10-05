"""Immutable canonical V7 checkpoint namespace; no database writer.

The producer must independently certify each receipt against canonical SIP,
rules, reporting coverage and split evidence before sealing this contract.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import re
from types import MappingProxyType

from src.backend.swing_book_source import HISTORICAL_POLICY, session_bounds
from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.reaction_band import CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION, StreamingLevelBook
from pipelines.market_sip.events.trade_reporting_flags import REVISION

VERSION = 'canonical-v7-checkpoint-namespace-v1'
TABLE = 'arte.canonical_v7_checkpoint_v1'
STORAGE_POLICY = 'live_market_ssd'
MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024
PLAN_KEYS = {'version', 'source_policy', 'input_policy', 'reporting_revision',
    'source_extraction_version', 'band_config_hash', 'authority_start', 'through',
    'tickers', 'canonical_source_plan_hash', 'condition_rules_hash',
    'reporting_coverage_hash', 'split_manifest_hash', 'producer_hash', 'numerical_kernel_hash',
    'canonical_history_start', 'genesis_proofs', 'sessions'}


def hash_value(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None or value == '0' * 64:
        raise ValueError('Canonical V7 requires a nonzero content hash')
    return value


@lru_cache(maxsize=128)
def _session_dates(start, through):
    from src.data_provider.calendar import mcal, MARKET_CALENDAR
    return tuple(stamp.date().isoformat() for stamp in mcal.get_calendar(MARKET_CALENDAR).schedule(
        start_date=start, end_date=through).index)


@dataclass(frozen=True, slots=True)
class CanonicalV7Selector:
    """A release supplies the full reviewed plan and its content hash."""
    plan_json: str
    source_plan_hash: str

    def plan(self):
        return _cached_plan(self.plan_json, self.source_plan_hash)

    def _validated_plan(self):
        import json
        from src.trading_runtime.journal_contract import canonical_json
        if type(self.plan_json) is not str or len(self.plan_json.encode('utf-8')) > 16*1024*1024:
            raise ValueError('Canonical V7 frozen plan exceeds its bounded payload')
        payload = json.loads(self.plan_json)
        if (type(payload) is not dict or set(payload) != PLAN_KEYS
                or self.plan_json != canonical_json(payload)
                or digest(payload) != hash_value(self.source_plan_hash)
                or payload['version'] != VERSION or payload['source_policy'] != HISTORICAL_POLICY
                or payload['input_policy'] != POLICY or payload['reporting_revision'] != REVISION
                or payload['source_extraction_version'] != EXTRACTION_VERSION
                or payload['band_config_hash'] != digest({**CONFIG, 'coverage': .8})):
            raise ValueError('Canonical V7 selector differs from its sealed plan')
        names = payload['tickers']
        if (type(names) is not list or not 1 <= len(names) <= 8192
                or any(type(name) is not str or re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,15}', name) is None for name in names)
                or names != sorted(set(names))
                or date.fromisoformat(payload['authority_start']) > date.fromisoformat(payload['through'])):
            raise ValueError('Canonical V7 plan has invalid frozen population or dates')
        for key in PLAN_KEYS:
            if key.endswith('_hash'):
                hash_value(payload[key])
        if date.fromisoformat(payload['canonical_history_start']) > date.fromisoformat(payload['authority_start']):
            raise ValueError('Canonical genesis cannot omit earlier certified authority')
        sessions = list(_session_dates(payload['authority_start'],payload['through']))
        if not sessions or payload['sessions'] != sessions or sessions[0] != payload['authority_start'] or sessions[-1] != payload['through']:
            raise ValueError('Canonical source plan must cover every NYSE session from genesis')
        proofs = payload['genesis_proofs']
        if type(proofs) is not dict or set(proofs) != set(names):
            raise ValueError('Canonical source plan needs exact per-ticker genesis proofs')
        for proof in proofs.values():
            if (type(proof) is not dict or set(proof) != {'prior_canonical_event_count','canonical_absence_hash'}
                    or type(proof['prior_canonical_event_count']) is not int or proof['prior_canonical_event_count'] != 0):
                raise ValueError('Canonical genesis requires certified absence of all earlier events')
            hash_value(proof['canonical_absence_hash'])
        return _freeze(payload)

    def payload(self):
        import json
        self.plan()
        return {'namespace':VERSION, 'source_plan_hash':self.source_plan_hash,
                'plan':json.loads(self.plan_json)}


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key:_freeze(child) for key,child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    return value


@lru_cache(maxsize=4)
def _cached_plan(plan_json, source_plan_hash):
    return CanonicalV7Selector(plan_json,source_plan_hash)._validated_plan()


def ddl():
    """Declaration only. Provisioning requires separately reviewed SSD preflight."""
    return f"""CREATE TABLE {TABLE} (
        source_plan_hash FixedString(64), session_date Date, ticker LowCardinality(String),
        record_json String CODEC(ZSTD(3)), record_hash FixedString(64)
    ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
    ORDER BY (source_plan_hash,session_date,ticker)
    SETTINGS storage_policy='{STORAGE_POLICY}'"""


RECEIPT_KEYS = {'ticker', 'session_date', 'source_plan_hash', 'source_receipt_hash',
    'input_hash', 'prefix_proof_hash', 'price_seconds', 'prefix_price_seconds',
    'parent_checkpoint_hash', 'kind', 'split_factor', 'split_evidence'}


def validate_receipt(selector, receipt):
    plan = selector.plan()
    if (type(receipt) is not dict or set(receipt) != RECEIPT_KEYS
            or receipt['source_plan_hash'] != selector.source_plan_hash
            or receipt['ticker'] not in plan['tickers']
            or receipt['session_date'] not in plan['sessions']
            or receipt['kind'] not in {'observed', 'empty', 'carry'}
            or type(receipt['price_seconds']) is not int or not 0 <= receipt['price_seconds'] <= 57600
            or type(receipt['prefix_price_seconds']) is not int
            or receipt['prefix_price_seconds'] < receipt['price_seconds']):
        raise ValueError('Canonical V7 source receipt differs from frozen authority')
    for key in ('source_receipt_hash', 'input_hash', 'prefix_proof_hash'):
        hash_value(receipt[key])
    if receipt['parent_checkpoint_hash'] is not None:
        hash_value(receipt['parent_checkpoint_hash'])
    elif receipt['session_date'] != plan['authority_start']:
        raise ValueError('Canonical state cannot restart empty after its certified genesis')
    if receipt['kind'] != 'observed' and (receipt['price_seconds'] != 0
            or receipt['split_factor'] != 1. or receipt['split_evidence'] != []):
        raise ValueError('Empty/carry V7 source needs zero price seconds and no unapplied split')
    if receipt['kind'] == 'empty' and (receipt['prefix_price_seconds'] != 0
            or receipt['parent_checkpoint_hash'] is not None):
        raise ValueError('Initial empty V7 state requires certified zero full prefix')
    if receipt['kind'] == 'carry' and receipt['parent_checkpoint_hash'] is None:
        raise ValueError('Carry V7 state requires an exact predecessor')
    if receipt['kind'] == 'observed' and receipt['price_seconds'] == 0:
        raise ValueError('Observed V7 checkpoint requires positive completed price seconds')
    return plan


def validate_checkpoint_identity(selector, receipt, checkpoint):
    validate_receipt(selector, receipt)
    _, end = session_bounds(receipt['session_date'])
    if (checkpoint.get('checkpoint_hash') != digest({key:value for key,value in checkpoint.items() if key != 'checkpoint_hash'})
            or checkpoint.get('ticker') != receipt['ticker']
            or checkpoint.get('session') != receipt['session_date']
            or checkpoint.get('available_at') != end.timestamp()
            or checkpoint.get('version') != 'historical-level-mle-book-1'
            or checkpoint.get('source_extraction_version') != EXTRACTION_VERSION
            or checkpoint.get('input_policy') != POLICY
            or checkpoint.get('band_config') != {**CONFIG, 'coverage':.8}
            or checkpoint.get('input_hash') != receipt['input_hash']
            or checkpoint.get('split_factor') != receipt['split_factor']
            or checkpoint.get('split_evidence') != receipt['split_evidence']
            or checkpoint.get('retrospective') is not True):
        raise ValueError('Canonical V7 checkpoint differs from source identity or engine output')


def checkpoint_record(selector, receipt, checkpoint, *, predecessor=None):
    """Seal real engine output; hashes are integrity, not a substitute for SIP certification."""
    validate_checkpoint_identity(selector, receipt, checkpoint)
    if receipt['kind'] == 'empty':
        expected = empty_or_carry_checkpoint(selector, receipt)
        if checkpoint != expected:
            raise ValueError('Canonical empty checkpoint differs from real engine state')
    elif receipt['parent_checkpoint_hash'] is None:
        if (receipt['kind'] != 'observed' or receipt['prefix_price_seconds'] != receipt['price_seconds']
                or checkpoint['prior_checkpoint_hash'] != _initial_prior(selector, receipt)['checkpoint_hash']):
            raise ValueError('First observed V7 checkpoint lacks proven zero preceding prefix')
    else:
        if (predecessor is None or predecessor.get('checkpoint_hash') != receipt['parent_checkpoint_hash']
                or predecessor['checkpoint_hash'] != digest({key:value for key,value in predecessor.items() if key != 'checkpoint_hash'})
                or predecessor.get('ticker') != receipt['ticker'] or predecessor.get('input_policy') != POLICY
                or predecessor.get('source_extraction_version') != EXTRACTION_VERSION
                or predecessor.get('band_config') != {**CONFIG, 'coverage':.8}
                or predecessor['session'] >= receipt['session_date']
                or checkpoint['prior_checkpoint_hash'] != predecessor['checkpoint_hash']):
            raise ValueError('Canonical checkpoint lacks exact historical predecessor')
        if receipt['kind'] == 'carry' and checkpoint != empty_or_carry_checkpoint(selector, receipt, predecessor=predecessor):
            raise ValueError('Canonical carry checkpoint changed its predecessor state')
    levels = checkpoint['levels']
    if type(levels) is not list or len({row['id'] for row in levels}) != len(levels):
        raise ValueError('Canonical checkpoint level identity duplicates')
    record = {'version':VERSION, 'receipt':deepcopy(receipt), 'checkpoint':deepcopy(checkpoint),
              'level_count':len(levels), 'observation_count':sum(len(row['observations']) for row in levels)}
    record['record_hash'] = digest(record)
    return record


def _initial_prior(selector, receipt):
    begin, _ = session_bounds(receipt['session_date'])
    prior = {'ticker':receipt['ticker'], 'session':'0001-01-01', 'available_at':begin.timestamp(),
             'levels':[], 'source_extraction_version':EXTRACTION_VERSION,
             'band_config':{**CONFIG, 'coverage':.8}, 'input_policy':POLICY,
             'canonical_genesis_proof':dict(selector.plan()['genesis_proofs'][receipt['ticker']])}
    prior['checkpoint_hash'] = digest(prior)
    return prior


def initial_canonical_prior(selector, receipt):
    """Producer genesis is allowed only at the proven full-history boundary."""
    validate_receipt(selector, receipt)
    if (receipt['parent_checkpoint_hash'] is not None
            or receipt['prefix_price_seconds'] != receipt['price_seconds']):
        raise ValueError('Canonical genesis cannot discard an earlier price prefix')
    return _initial_prior(selector, receipt)


def empty_or_carry_checkpoint(selector, receipt, *, predecessor=None):
    """An explicit certified zero-input day still advances actual engine state."""
    validate_receipt(selector, receipt)
    if receipt['kind'] not in {'empty', 'carry'}:
        raise ValueError('Only zero-input source evidence can create empty/carry state')
    begin, end = session_bounds(receipt['session_date'])
    if receipt['kind'] == 'empty':
        prior = _initial_prior(selector, receipt)
    else:
        if (predecessor is None or predecessor.get('checkpoint_hash') != receipt['parent_checkpoint_hash']
                or predecessor.get('input_policy') != POLICY or predecessor.get('ticker') != receipt['ticker']):
            raise ValueError('Carry V7 state lacks its certified canonical predecessor')
        prior = predecessor
    engine = StreamingLevelBook(prior, ticker=receipt['ticker'], session=receipt['session_date'],
        start=begin.timestamp(), end=end.timestamp())
    return engine.historical_checkpoint(receipt['input_hash'])
