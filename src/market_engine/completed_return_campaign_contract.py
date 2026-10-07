"""Installed producer campaign authority, separate from sealed return policy@1."""
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid5

import pyarrow as pa

from src.market_engine.completed_endpoint_return_contract import (
    POLICY, canonical_source_hash, producer_implementation_hash, require_hash,
)
from src.trading_runtime.journal_contract import canonical_json

CERTIFICATE_TABLE = 'arte.completed_endpoint_return_packet_certification_v1'
CAMPAIGN_CONTRACT = 'completed-endpoint-return-installed-campaign@2'
CERTIFICATE_SCHEMA = pa.schema([
    ('build_id', pa.string()), ('session_date', pa.date32()),
    ('feature_attempt_id', pa.string()), ('population_token', pa.string()),
    ('decision_source_kind', pa.string()),
    ('decision_source_token', pa.string()), ('population_count', pa.uint32()),
    ('population_keys_hash', pa.string()), ('packet_index', pa.uint32()),
    ('packet_count', pa.uint32()), ('requested_count', pa.uint32()),
    ('producer_source_hash', pa.string()), ('campaign_source_hash', pa.string()),
    ('policy_digest', pa.string()), ('feature_hash', pa.string()),
    ('coverage_hash', pa.string()), ('projection_token', pa.string()),
])


class DecisionSourceKind(str, Enum):
    CERTIFIED_MACD_CANDIDATES = 'certified_macd_candidate_population@1'
    CERTIFIED_STRUCTURAL_DECISIONS = 'certified_early_squeeze_vwap_structural_population@1'


class MissingDecisionProducer(RuntimeError):
    """Required producer-owned decision authority is not installed/supported."""


def campaign_source_hash():
    root = Path(__file__).resolve().parents[2]
    names = ('src/market_engine/completed_return_campaign_contract.py',
             'pipelines/market_sip/events/completed_return_campaign.py',
             'src/backend/backtest_certified_completed_returns.py',
             'src/backend/backtest_completed_return_campaign_store.py',
             'src/market_engine/completed_return_insert_authority.py')
    return canonical_source_hash([(name, (root / name).read_bytes()) for name in names])


@dataclass(frozen=True, slots=True)
class NativeDecisionPopulation:
    """Whole certified producer candidate population, not economic outcomes.

    This version is explicitly the existing certified StrategyOne candidate
    product population, including its MACD qualification. It does not claim
    certified BranchB structural-only population authority.
    """
    market: object
    source_kind: DecisionSourceKind
    candidate_token: str
    keys: tuple[tuple[str, int], ...]
    producer_source_hash: str
    campaign_source_hash: str
    token: str

    def __post_init__(self):
        from src.backend.backtest_market_data import CertifiedMarketDayPlan
        if type(self.market) is not CertifiedMarketDayPlan or len(self.market.sessions) != 1:
            raise ValueError('Campaign requires a complete single certified source day')
        if type(self.source_kind) is not DecisionSourceKind:
            raise ValueError('Campaign requires an explicit typed decision source kind')
        for value in (self.market.token, self.candidate_token, self.producer_source_hash,
                      self.campaign_source_hash, self.token):
            require_hash(value)
        if type(self.keys) is not tuple or self.keys != tuple(sorted(set(self.keys))):
            raise ValueError('Campaign population keys must be complete, unique and ordered')
        if any(type(t) is not str or t not in self.market.tickers or type(b) is not int
               or not 0 < b <= 57600000 or b % 100 for t, b in self.keys):
            raise ValueError('Campaign population contains foreign native clocks')
        if self.token != population_token(self.market, self.candidate_token, self.keys,
                                         self.producer_source_hash, self.campaign_source_hash, self.source_kind):
            raise ValueError('Campaign population seal differs')

    @property
    def keys_hash(self):
        return sha256(canonical_json(self.keys).encode()).hexdigest()

    @property
    def packet_count(self):
        return (len(self.keys) + 511) // 512

    def packet(self, index):
        if type(index) is not int or not 0 <= index < self.packet_count:
            raise ValueError('Campaign packet index is outside complete declared scope')
        keys = self.keys[index * 512:(index + 1) * 512]
        attempt = str(uuid5(UUID('bb8709cb-d52d-4a61-8897-74e0d4d49f70'), self.token + ':' + str(index)))
        return keys, attempt


def population_token(market, candidate_token, keys, producer_hash, campaign_hash, source_kind):
    return sha256(canonical_json(dict(contract=CAMPAIGN_CONTRACT, market_token=market.token,
        candidate_token=candidate_token, keys=keys, producer_source_hash=producer_hash,
        campaign_source_hash=campaign_hash, policy_digest=POLICY.digest,
        decision_source_kind=source_kind.value)).encode()).hexdigest()


def certify_native_population(market, client, *, source_kind):
    """Recheck real source/candidate products; callers cannot choose arbitrary keys."""
    if type(source_kind) is not DecisionSourceKind:
        raise ValueError('An explicit declared decision source kind is mandatory')
    if source_kind is DecisionSourceKind.CERTIFIED_STRUCTURAL_DECISIONS:
        raise MissingDecisionProducer('No producer-owned normalized EarlySqueeze/native-VWAP/structural '
            'decision-scope product and installed certification verifier exist for this source kind; '
            'MACD candidate population cannot substitute for it')
    from src.backend.backtest_market_data import verify_market_day_plan
    from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan
    from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST
    verify_market_day_plan(market, client)
    candidates = certify_candidate_plan(market, candidate_rule_digest=RULE_DIGEST,
        through_boundary_ms=57600000, client=client, batch_size=512)
    keys = tuple(sorted((row.ticker, int(boundary)) for row in candidates.prepared
                        for boundary in row.boundary_ms))
    producer_hash, campaign_hash = producer_implementation_hash(), campaign_source_hash()
    return NativeDecisionPopulation(market, source_kind, candidates.token, keys, producer_hash, campaign_hash,
        population_token(market, candidates.token, keys, producer_hash, campaign_hash, source_kind))
