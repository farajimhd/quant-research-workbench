"""SELECT-only full-session preflight for explicitly declared ladder consumers."""
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
from types import SimpleNamespace

from src.trading_runtime.journal_contract import canonical_json
from .backtest_ladder_source_authority import declared_ladder_policy, declared_source_end


def automatic_policy(configuration):
    return declared_ladder_policy(SimpleNamespace(payload=configuration))


@dataclass(frozen=True, slots=True)
class CertifiedGeometryScopes:
    scopes: tuple[tuple[tuple[str, ...], str, int, int], ...]
    ticker_count: int
    token: str


class _UncachedGeometryReader:
    """Delegate SELECT reads without retaining geometry in the process cache."""
    def __init__(self, reader):
        self.reader = reader

    def __getattr__(self, name):
        return getattr(self.reader, name)


def _geometry_scopes(market, seeds, reader, certify_pivots, certify_intervals):
    pivot_scopes, interval_scopes = [], []
    uncached = _UncachedGeometryReader(reader)
    for offset in range(0, len(market.tickers), 8):
        tickers = market.tickers[offset:offset + 8]
        pivots = certify_pivots(market, session_date=market.sessions[0],
            candidate_tickers=tickers, client=uncached, batch_size=8)
        pivot_scopes.append((tickers, pivots.token, len(pivots.coverage),
                            sum(len(values) for _, values in pivots.intervals)))
        del pivots
        intervals = certify_intervals(market, seeds, session_date=market.sessions[0],
            candidate_tickers=tickers, client=uncached)
        interval_scopes.append((tickers, intervals.token, len(intervals.coverage),
                               sum(len(values) for _, values in intervals.intervals)))
        del intervals
    def seal(scopes, kind):
        if sum(row[2] for row in scopes) != len(market.tickers):
            raise ValueError('Declared ladder geometry omits frozen population')
        token = sha256(canonical_json(dict(kind=kind, market=market.token,
            scopes=scopes)).encode()).hexdigest()
        return CertifiedGeometryScopes(tuple(scopes), len(market.tickers), token)
    return seal(pivot_scopes, 'pivots'), seal(interval_scopes, 'v7_intervals')


@dataclass(frozen=True, slots=True)
class DeclaredLadderPlans:
    market: object
    execution_market: object
    prices: object
    identities: object
    seeds: object
    pivots: object
    intervals: object
    scan: dict
    source_end: int
    token: str

    def pins(self):
        return dict(automatic_ladder_plan_token=self.token,
            strategy_one_identity_token=self.identities.token,
            automatic_ladder_pivot_token=self.pivots.token,
            automatic_ladder_interval_token=self.intervals.token,
            automatic_ladder_scan_hash=self.scan['authority']['content_hash'],
            automatic_ladder_source_end=self.source_end)


def certify_declared_ladder_plans(market, prices, *, configuration, definition,
                                  client_factory, market_pins=None, v7_pins=None):
    from .backtest_market_data import CertifiedMarketDayPlan
    from .backtest_liquidity_price import PriceLevelPlan
    from .backtest_strategy_one_identity import certify_identity_plan
    from .structural_v7_seed import certified_seed_plan
    from .backtest_declared_ladder_seed import verify_declared_ladder_seed_plan
    from .backtest_strategy_one_pivot_store import certify_pivot_plan
    from .backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
    from .fixed_bar_signal import canonical_stream_activation, load_first_squeeze_occurrences
    if (automatic_policy(configuration) is None or not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(prices, PriceLevelPlan) or len(market.sessions)!=1
            or not market.tickers or market.execution_interval.milliseconds!=100
            or prices.source_build_id!=market.build_id):
        raise ValueError('Declared ladder needs complete fixed market and eligible price authority')
    if prices.projected(market) != prices:
        raise ValueError('Declared ladder eligible prices differ from full frozen market scope')
    policy=configuration['strategy']['numbered_release']['automatic_market_policy']
    end=declared_source_end(policy,definition)
    with closing(client_factory()) as reader:
        if reader.execute("SELECT getSetting('readonly')").strip()!='1':
            raise ValueError('Declared ladder preflight requires SELECT-only principal')
        identities=certify_identity_plan(market,client=reader)
        seeds=certified_seed_plan(market,reader)
        verify_declared_ladder_seed_plan(market,seeds)
        pivots,intervals=_geometry_scopes(market,seeds,reader,
            certify_pivot_plan,certify_v7_interval_plan)
        stream,activation=canonical_stream_activation()
        scan=load_first_squeeze_occurrences(market,stream=stream,activation=activation,
            through_boundary_ms=end,client=reader)
    token=sha256(canonical_json(dict(market=market.token,prices=prices.token,
        identities=identities.token,seeds=seeds.token,pivots=pivots.token,intervals=intervals.token,
        scan=scan['authority'],source_end=end,policy=policy)).encode()).hexdigest()
    result=DeclaredLadderPlans(market,market,prices,identities,seeds,pivots,intervals,scan,end,token)
    if market_pins is not None:
        if (market_pins.get('token')!=market.token
                or market_pins.get('price_level_plan_token')!=prices.token
                or any(market_pins.get(k)!=v for k,v in result.pins().items())):
            raise ValueError('Declared ladder source changed after preflight')
    if v7_pins is not None and dict(v7_pins)!=seeds.payload():
        raise ValueError('Declared ladder prior V7 authority changed after preflight')
    return result
