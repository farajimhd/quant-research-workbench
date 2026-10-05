"""Certified companion quotes and a reduction after unchanged native anchors."""
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import numpy as np

from .backtest_market_data import CertifiedMarketDayPlan, market_day_boundary, verify_market_day_plan, _literal
from .backtest_strategy_episode_activity_gate import EpisodeActivityStaticGate
from .backtest_strategy_one_static_gate import StrategyOneStaticGate
from .backtest_strategy_rising_momentum import _frozen
from src.trading_runtime.entry_spread_risk import EntrySpreadRiskPolicy, canonical_price_int, entry_spread_risk_mask, entry_spread_risk_allowed, exact_epoch_us

ENTRY_SPREAD_ORIGINAL_RISK_EXCEEDED = 1 << 11
ENTRY_SPREAD_QUOTE_UNAVAILABLE = 1 << 12


@dataclass(frozen=True, slots=True)
class EntrySpreadRiskWitness:
    ticker: str
    session_date: str
    boundary_ms: int
    source_build_id: str
    liquidity_attempt_id: str
    market_plan_token: str
    parent_gate_token: str
    source_plan_token: str
    policy: EntrySpreadRiskPolicy
    bid_int: int
    ask_int: int
    original_stop_int: int
    quote_timestamp_us: int


@dataclass(frozen=True, slots=True)
class CertifiedEntrySpreadRiskPlan:
    market: CertifiedMarketDayPlan
    parent: EpisodeActivityStaticGate
    policy: EntrySpreadRiskPolicy
    bid_int: np.ndarray
    ask_int: np.ndarray
    original_stop_int: np.ndarray
    quote_timestamp_us: np.ndarray
    quote_valid: np.ndarray
    liquidity_attempts: tuple[str, ...]
    token: str

    def __post_init__(self):
        from uuid import UUID
        if (type(self.market) is not CertifiedMarketDayPlan
                or type(self.parent) is not EpisodeActivityStaticGate
                or type(self.policy) is not EntrySpreadRiskPolicy
                or len(self.market.sessions) != 1
                or self.market.token != self.parent.activity.market.token):
            raise ValueError('Entry cost companion lacks exact market and inherited full gate')
        n = len(self.parent.facts)
        arrays = (self.bid_int, self.ask_int, self.original_stop_int, self.quote_timestamp_us, self.quote_valid)
        if (any(type(v) is not np.ndarray or v.dtype != np.int64 or v.shape != (n,) for v in arrays)
                or type(self.liquidity_attempts) is not tuple or len(self.liquidity_attempts) != n):
            raise ValueError('Entry cost companion columns differ from complete parent keys')
        requested = self.parent.rejection_mask == 0
        if any(np.any(v[~requested] != 0) for v in arrays):
            raise ValueError('Unrequested entry cost keys carry invented observations')
        units = {(u.session_date, u.ticker): u for u in self.market.units if u.stage == 'broker_100ms'}
        for i in np.flatnonzero(requested):
            fact = self.parent.facts[int(i)]
            unit = units.get((self.market.sessions[0], fact.ticker))
            attempt = self.liquidity_attempts[int(i)]
            now = exact_epoch_us(market_day_boundary(date.fromisoformat(self.market.sessions[0]), fact.boundary_ms))
            if (unit is None or str(UUID(attempt)) != attempt or not UUID(attempt).int
                    or attempt != unit.attempt_id or not fact.protection_valid
                    or self.original_stop_int[i] != canonical_price_int(fact.stop_price)
                    or self.quote_valid[i] not in (0, 1)
                    or min(self.bid_int[i], self.ask_int[i], self.quote_timestamp_us[i]) < 0
                    or (self.quote_valid[i] == 1 and not 0 < self.bid_int[i] <= self.ask_int[i])
                    or self.quote_timestamp_us[i] > now):
                raise ValueError('Entry cost quote is missing, foreign, stale, future or changes original stop')
        if any(self.liquidity_attempts[i] for i in np.flatnonzero(~requested)):
            raise ValueError('Unrequested cost key has a source attempt')
        expected = _token(self.market, self.parent, self.policy, arrays, self.liquidity_attempts)
        if self.token != expected:
            raise ValueError('Entry cost companion content seal differs')
        entry_spread_risk_mask(self.policy, *arrays[:3])
        for name, value in zip(('bid_int', 'ask_int', 'original_stop_int', 'quote_timestamp_us', 'quote_valid'), arrays):
            object.__setattr__(self, name, _frozen(value))

    def index(self, ticker, boundary_ms):
        keys = self.parent.activity.parent.momentum.keys
        key = (ticker, boundary_ms)
        i = bisect_left(keys, key)
        if (type(ticker) is not str or type(boundary_ms) is not int
                or i == len(keys) or keys[i] != key or self.parent.rejection_mask[i] != 0):
            raise ValueError('Entry cost lookup is outside unchanged admitted parent keys')
        return i

    def witness(self, ticker, boundary_ms):
        i = self.index(ticker, boundary_ms)
        now = exact_epoch_us(market_day_boundary(date.fromisoformat(self.market.sessions[0]), boundary_ms))
        if self.quote_valid[i] != 1 or not 0 <= now - self.quote_timestamp_us[i] <= 1_000_000:
            raise ValueError('Declared entry quote is explicitly unavailable or stale')
        if not entry_spread_risk_allowed(self.policy, bid_int=int(self.bid_int[i]),
                ask_int=int(self.ask_int[i]), original_stop_int=int(self.original_stop_int[i])):
            raise ValueError('Declared entry spread original risk cap rejected this key')
        return EntrySpreadRiskWitness(ticker, self.market.sessions[0], boundary_ms,
            self.market.build_id, self.liquidity_attempts[i], self.market.token,
            self.parent.token, self.token, self.policy, *(int(v[i]) for v in
            (self.bid_int, self.ask_int, self.original_stop_int, self.quote_timestamp_us)))


def _token(market, parent, policy, arrays, attempts):
    digest = sha256(json.dumps((market.token, parent.token, policy.payload(), attempts),
                             sort_keys=True, separators=(',', ':')).encode())
    for value in arrays:
        digest.update(value.tobytes())
    return digest.hexdigest()


def load_entry_spread_risk_plan(market, parent, policy, *, client, batch_size=512):
    """Read only exact completed proposal quotes; no journal or future carry."""
    if (type(market) is not CertifiedMarketDayPlan or type(parent) is not EpisodeActivityStaticGate
            or type(policy) is not EntrySpreadRiskPolicy or type(batch_size) is not int
            or not 1 <= batch_size <= 1024):
        raise ValueError('Entry cost loader needs exact declared full-prefix inputs')
    verify_market_day_plan(market, client=client)
    arrays = tuple(np.zeros(len(parent.facts), dtype=np.int64) for _ in range(5))
    attempts = [''] * len(parent.facts)
    units = {(u.session_date, u.ticker): u for u in market.units if u.stage == 'broker_100ms'}
    selected = np.flatnonzero(parent.rejection_mask == 0).tolist()
    from .backtest_market_data import SESSION_OPEN_OFFSET_MS
    for start in range(0, len(selected), batch_size):
        indices = selected[start:start + batch_size]
        keys = {}
        for index in indices:
            fact = parent.facts[index]
            unit = units.get((market.sessions[0], fact.ticker))
            if unit is None:
                raise ValueError('Entry cost source omits a pinned liquidity attempt')
            key = (fact.ticker, (fact.boundary_ms + SESSION_OPEN_OFFSET_MS) // 100 - 1, unit.attempt_id)
            if key in keys:
                raise ValueError('Entry cost source keys duplicate')
            keys[key] = index
        scope = ','.join(f'({_literal(ticker)},{bucket},toUUID({_literal(attempt)}))' for ticker, bucket, attempt in keys)
        sql = ("SELECT ticker,bucket_index,toString(attempt_id) AS attempt_id,bid_int,ask_int,"
               "quote_timestamp_us,quote_valid FROM arte.liquidity_100ms_v1 "
               f"WHERE build_id={_literal(market.build_id)} AND session_date=toDate({_literal(market.sessions[0])}) "
               f"AND (ticker,bucket_index,attempt_id) IN ({scope}) "
               "SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow")
        rows = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
        seen = set()
        for row in rows:
            key = (row['ticker'], row['bucket_index'], row['attempt_id'])
            if (key not in keys or key in seen
                    or type(row['quote_valid']) is not int or row['quote_valid'] not in (0, 1)
                    or any(type(row[v]) is not int for v in ('bid_int', 'ask_int', 'quote_timestamp_us'))):
                raise ValueError('Entry cost source has foreign, duplicate or unavailable quote')
            seen.add(key)
            index = keys[key]
            fact = parent.facts[index]
            values = (row['bid_int'], row['ask_int'], canonical_price_int(fact.stop_price), row['quote_timestamp_us'], row['quote_valid'])
            for column, value in zip(arrays, values):
                column[index] = value
            attempts[index] = row['attempt_id']
        if seen != set(keys):
            raise ValueError(f'Entry cost source is missing exact proposal coverage: expected={len(keys)} observed={len(seen)}')
    return CertifiedEntrySpreadRiskPlan(market, parent, policy, *arrays, tuple(attempts),
        _token(market, parent, policy, arrays, tuple(attempts)))


@dataclass(frozen=True)
class EntrySpreadRiskStaticGate(StrategyOneStaticGate):
    plan: CertifiedEntrySpreadRiskPlan

    def __post_init__(self):
        if type(self.plan) is not CertifiedEntrySpreadRiskPlan:
            raise ValueError('Entry cost gate lacks independent source plan')
        expected = _reasons(self.plan)
        if (self.facts != self.plan.parent.facts or self.rejection_mask.dtype != np.uint16
                or not np.array_equal(self.rejection_mask, expected)
                or not np.array_equal(self.eligible_indices, np.flatnonzero(expected == 0))):
            raise ValueError('Entry cost gate differs from unchanged parent reduction')
        object.__setattr__(self, 'rejection_mask', _frozen(self.rejection_mask))
        object.__setattr__(self, 'eligible_indices', _frozen(self.eligible_indices))


def _reasons(plan):
    reasons = plan.parent.rejection_mask.copy()
    eligible = entry_spread_risk_mask(plan.policy, plan.bid_int, plan.ask_int, plan.original_stop_int)
    origin_us = exact_epoch_us(market_day_boundary(plan.market.sessions[0], 0))
    boundaries_ms = np.fromiter((fact.boundary_ms for fact in plan.parent.facts), dtype=np.int64)
    now = origin_us + boundaries_ms * 1_000
    available = (plan.quote_valid == 1) & (now - plan.quote_timestamp_us <= 1_000_000)
    parent_eligible = reasons == 0
    reasons[parent_eligible & ~available] |= np.uint16(ENTRY_SPREAD_QUOTE_UNAVAILABLE)
    reasons[parent_eligible & available & ~eligible] |= np.uint16(ENTRY_SPREAD_ORIGINAL_RISK_EXCEEDED)
    return reasons


def compile_entry_spread_risk_gate(plan):
    reasons = _reasons(plan)
    return EntrySpreadRiskStaticGate(plan.parent.facts, reasons,
                                   np.flatnonzero(reasons == 0).astype(np.int64), plan)


@dataclass(frozen=True, slots=True)
class EntrySpreadRiskReadbackAuthority:
    run_id: str
    plan: CertifiedEntrySpreadRiskPlan
    strategy_number: int

    def __post_init__(self):
        from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
        if (type(self.run_id) is not str or not self.run_id
                or type(self.plan) is not CertifiedEntrySpreadRiskPlan
                or numbered_fixed_strategy(self.strategy_number).entry_spread_risk_policy != self.plan.policy):
            raise ValueError('Entry cost readback differs from independently declared release')

    def witness(self, ticker, boundary_ms):
        return self.plan.witness(ticker, boundary_ms)

    def check_proposal(self, proposal):
        witness = self.witness(proposal.ticker, proposal.boundary_ms)
        if (proposal.strategy_number != self.strategy_number
                or canonical_price_int(proposal.reference_ask) != witness.ask_int
                or canonical_price_int(proposal.initial_stop) != witness.original_stop_int):
            raise ValueError('Entry cost proposal changes its original quote or stop')
        return witness
