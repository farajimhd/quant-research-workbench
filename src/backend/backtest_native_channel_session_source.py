"""Bounded installed packet selection and one-time causal sequence preparation."""
from dataclasses import dataclass, field
from types import MappingProxyType

import polars as pl

from .backtest_market_data import CertifiedMarketDayPlan, _literal, assert_select_only, verify_market_day_plan
from .backtest_native_channel_sequences import (
    NativeChannelSequencePolicy, PreparedNativeChannelSequence, prepare_installed_native_channel_sequence,
)
from .backtest_native_channel_store import storage_preflight
from pipelines.market_sip.events.completed_endpoint_return_producer import read_arrow
from src.market_engine.native_causal_channel_contract import (
    COVERAGE_TABLE, COVERAGE_SCHEMA, MAX_TICKERS, NativeChannelRequest,
    require_hash, require_uuid,
)
from src.market_engine.native_channel_insert_authority import NativeChannelInsertAuthority


@dataclass(frozen=True, slots=True)
class PreparedNativeChannelSession:
    """Compiled source evidence only; no entry, held-position or order authority."""
    tickers: tuple[str, ...]
    sequences: tuple
    _by_ticker: object = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if (type(self.tickers) is not tuple or not self.tickers
                or any(type(ticker) is not str for ticker in self.tickers)
                or self.tickers != tuple(sorted(set(self.tickers)))
                or type(self.sequences) is not tuple or not 1 <= len(self.sequences) <= 4096
                or any(type(sequence) is not PreparedNativeChannelSequence for sequence in self.sequences)):
            raise ValueError('Exact bounded compiled native session inventory required')
        selected = set(self.tickers)
        index = {}
        for sequence in self.sequences:
            for ticker in sequence.packet.request.tickers:
                if ticker in selected:
                    if ticker in index:
                        raise ValueError('Native session repeats prepared ticker ownership')
                    index[ticker] = sequence
        if set(index) != selected:
            raise ValueError('Native session lacks complete prepared ticker ownership')
        object.__setattr__(self, '_by_ticker', MappingProxyType(index))

    def sequence_for(self, ticker):
        if ticker not in self._by_ticker:
            raise ValueError('Native channel ticker is outside the mandatory session scope')
        return self._by_ticker[ticker]


def prepare_installed_native_channel_session(client, *, market, session_date, tickers,
        through_day_boundary_ms, policy, producer_source_hash, authority, max_packets):
    """Select exact installed identities, never newest attempts or runtime files.

    The full inventory of every selected packet is subsequently loaded and
    certified, including members outside the caller's mandatory ticker scope.
    Compilation uses the existing columnar sequence preparation. Future rows
    in the source panel cannot qualify a decision before their availability.
    """
    if (type(market) is not CertifiedMarketDayPlan or type(policy) is not NativeChannelSequencePolicy
            or type(authority) is not NativeChannelInsertAuthority
            or type(max_packets) is not int or not 1 <= max_packets <= 4096
            or type(tickers) is not tuple or not tickers or len(tickers) > MAX_TICKERS * max_packets
            or any(type(ticker) is not str for ticker in tickers)
            or tickers != tuple(sorted(set(tickers)))):
        raise ValueError('Exact bounded installed native session inputs required')
    policy.__post_init__()
    require_hash(producer_source_hash)
    # Validate the complete caller scope before any discovery query.
    for ticker in tickers:
        NativeChannelRequest(market, session_date, (ticker,),
            through_day_boundary_ms, policy.inputs).__post_init__()
    verify_market_day_plan(market, client)
    storage_preflight(client)
    columns = ','.join(f'toString({field.name}) AS {field.name}'
        if field.name.endswith('attempt_id') else field.name for field in COVERAGE_SCHEMA)
    def read(predicate, bound):
        sql = assert_select_only(f'SELECT {columns} FROM (SELECT {",".join(COVERAGE_SCHEMA.names)} '
            f'FROM {COVERAGE_TABLE} WHERE {predicate} ORDER BY feature_attempt_id,ticker,resolution_ms '
            f'LIMIT {bound + 1}) FORMAT ArrowStream')
        return pl.from_arrow(read_arrow(client, sql, COVERAGE_SCHEMA, bound))
    members = ','.join(_literal(ticker) for ticker in tickers)
    selected = read(f'build_id={_literal(market.build_id)} AND session_date={_literal(session_date)} '
        f'AND market_plan_token={_literal(market.token)} AND policy_digest={_literal(policy.inputs.digest)} '
        f'AND producer_source_hash={_literal(producer_source_hash)} '
        f'AND through_day_boundary_ms={through_day_boundary_ms} AND ticker IN ({members})',
        len(tickers) * len(policy.inputs.resolutions_ms))
    expected = pl.DataFrame({'ticker': tickers}).join(
        pl.DataFrame({'resolution_ms': pl.Series(policy.inputs.resolutions_ms, dtype=pl.UInt32)}), how='cross')
    keys = ['ticker', 'resolution_ms']
    if (selected.height != expected.height or selected.select(keys).n_unique() != selected.height
            or selected.select(keys).join(expected, on=keys, how='anti').height
            or expected.join(selected.select(keys), on=keys, how='anti').height):
        raise ValueError('Native session requires one exact installed attempt per ticker and resolution')
    attempts = sorted(selected['feature_attempt_id'].unique().to_list())
    if len(attempts) > max_packets:
        raise ValueError('Native session exceeds its declared packet bound')
    sequences = []
    for attempt in attempts:
        require_uuid(attempt)
        inventory = read(f'feature_attempt_id=toUUID({_literal(attempt)})',
            MAX_TICKERS * len(policy.inputs.resolutions_ms))
        packet_tickers = tuple(sorted(inventory['ticker'].unique().to_list()))
        request = NativeChannelRequest(market, session_date, packet_tickers,
            through_day_boundary_ms, policy.inputs)
        sequences.append(prepare_installed_native_channel_sequence(client, request, attempt,
            policy=policy, producer_source_hash=producer_source_hash, authority=authority))
    result = PreparedNativeChannelSession(tickers, tuple(sequences))
    return result


def prepare_and_bind_native_channel_session(owner, client, *, market, tickers,
        source_through_day_boundary_ms, execution_through_day_boundary_ms,
        producer_source_hash, authority, max_packets, max_positions):
    """Prepare the exact installed source before atomically binding its owner.

    Source horizon identifies certified producer packets. Execution horizon
    bounds this run's acquisition schedules without changing packet identity.
    Discovery, full certification and compilation happen before any mutation.
    """
    from .backtest_native_channel_management_owner import NativeChannelManagementOwner
    if (type(owner) is not NativeChannelManagementOwner or owner._feature_session is not None
            or owner._pending or type(execution_through_day_boundary_ms) is not int
            or type(source_through_day_boundary_ms) is not int
            or not 0 <= execution_through_day_boundary_ms <= source_through_day_boundary_ms
            or type(max_positions) is not int or max_positions <= 0):
        raise ValueError('Native preparation requires its unbound owner and ordered declared horizons')
    prepared = prepare_installed_native_channel_session(client, market=market,
        session_date=owner.session_date.isoformat(), tickers=tickers,
        through_day_boundary_ms=source_through_day_boundary_ms,
        policy=owner.policy.exit_policy.sequence, producer_source_hash=producer_source_hash,
        authority=authority, max_packets=max_packets)
    owner.bind_feature_session(prepared,
        through_day_boundary_ms=execution_through_day_boundary_ms, max_positions=max_positions)
    return prepared
