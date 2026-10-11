"""Select exact parent-projected source packets, never latest attempts."""
from dataclasses import dataclass

import polars as pl

from .backtest_market_data import CertifiedMarketDayPlan, project_market_day_plan
from .backtest_native_channel_sequences import NativeChannelSequencePolicy
from src.market_engine.native_causal_channel_contract import (
    NativeChannelRequest, MAX_TICKERS, require_hash, require_uuid,
)


@dataclass(frozen=True, slots=True)
class NativePacketInventory:
    requests: tuple
    excluded_packet_identities: tuple

    @property
    def horizon(self):
        return self.requests[0][1].through_day_boundary_ms


def select_parent_projected_native_packets(market, session_date, tickers, policy,
        producer_source_hash, inventories):
    """Check complete packet inventories before certifying their feature rows.

    Different parent projections are explicitly excluded with source identities.
    Two accepted attempts covering the same mandatory key remain an error even
    if their values appear equal. Full row hashes and Keeper producer fences
    are still required by the installed loader after this discovery step.
    """
    if (type(market) is not CertifiedMarketDayPlan
            or type(policy) is not NativeChannelSequencePolicy
            or type(inventories) is not tuple or not 1 <= len(inventories) <= 4096
            or type(tickers) is not tuple or not tickers
            or tickers != tuple(sorted(set(tickers)))):
        raise ValueError('Exact bounded parent-projected native inventory required')
    require_hash(producer_source_hash)
    policy.__post_init__()
    for ticker in tickers:
        NativeChannelRequest(market, session_date, (ticker,), 0, policy.inputs)
    selected, excluded, key_frames, attempts = [], [], [], set()
    for rows in inventories:
        if type(rows) is not pl.DataFrame or not rows.height:
            raise ValueError('Native packet inventory is empty or untyped')
        for column, value in (('build_id', market.build_id), ('session_date', session_date),
                ('policy_digest', policy.inputs.digest), ('producer_source_hash', producer_source_hash)):
            if rows[column].null_count() or rows[column].unique().to_list() != [value]:
                raise ValueError('Native packet inventory has mixed or foreign source identity')
        if any(rows[column].null_count() or rows[column].n_unique() != 1
                for column in ('feature_attempt_id', 'market_plan_token', 'through_day_boundary_ms')):
            raise ValueError('Native packet inventory has mixed attempt, projection or horizon')
        attempt, token = rows['feature_attempt_id'][0], rows['market_plan_token'][0]
        require_uuid(attempt)
        require_hash(token)
        if attempt in attempts:
            raise ValueError('Native packet inventory repeats an attempt')
        attempts.add(attempt)
        members = tuple(sorted(rows['ticker'].unique().to_list()))
        if (not 1 <= len(members) <= MAX_TICKERS
                or any(type(value) is not str for value in members)):
            raise ValueError('Native packet inventory exceeds its complete member bound')
        expected = pl.DataFrame({'ticker': members}).join(pl.DataFrame({
            'resolution_ms': pl.Series(policy.inputs.resolutions_ms, dtype=pl.UInt32)}), how='cross')
        keys = ['ticker', 'resolution_ms']
        if (rows.height != expected.height or rows.select(keys).n_unique() != rows.height
                or rows.select(keys).join(expected, on=keys, how='anti').height
                or expected.join(rows.select(keys), on=keys, how='anti').height):
            raise ValueError('Native packet requires complete unique member-resolution coverage')
        if not set(members).issubset(market.tickers):
            excluded.append((attempt, token, 'outside_selected_parent_scope'))
            continue
        projected = (market if members == market.tickers and token == market.token
            else project_market_day_plan(market, members))
        if token != projected.token:
            excluded.append((attempt, token, 'different_certified_parent_projection'))
            continue
        request = NativeChannelRequest(projected, session_date, members,
            int(rows['through_day_boundary_ms'][0]), policy.inputs)
        owned = rows.filter(pl.col('ticker').is_in(tickers)).select(
            *keys, 'through_day_boundary_ms')
        if not owned.height:
            excluded.append((attempt, token, 'outside_mandatory_ticker_scope'))
            continue
        selected.append((attempt, request))
        key_frames.append(owned)
    if not selected:
        raise ValueError('Native inventory lacks a packet from the exact certified parent')
    actual = pl.concat(key_frames)
    expected = pl.DataFrame({'ticker': tickers}).join(pl.DataFrame({
        'resolution_ms': pl.Series(policy.inputs.resolutions_ms, dtype=pl.UInt32)}), how='cross')
    if (actual.height != expected.height or actual.select(keys).n_unique() != actual.height
            or actual.select(keys).join(expected, on=keys, how='anti').height
            or expected.join(actual.select(keys), on=keys, how='anti').height
            or actual['through_day_boundary_ms'].n_unique() != 1):
        raise ValueError('Native inventory requires complete unambiguous parent-projected ownership and horizon')
    return NativePacketInventory(tuple(sorted(selected, key=lambda value: value[0])),
        tuple(sorted(excluded)))
