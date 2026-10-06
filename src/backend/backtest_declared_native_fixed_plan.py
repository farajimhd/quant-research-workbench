"""Declared causal entry preparation; no installed executor or financial authority.

These separate adapters read the existing certified producer products. They do
not pass a future identity through a legacy numbered gate or fabricate a parent
proposal. Native installation and cold journal admission remain separate gates.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID

import numpy as np
import pyarrow as pa

from .backtest_market_data import CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, _literal, assert_select_only
from .backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from .backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from .backtest_strategy_one_static_gate import StrategyOneStaticGate
from .backtest_strategy_rising_momentum import CertifiedRisingMomentumPlan, _frozen
from src.trading_runtime.declared_native_fixed_capabilities import DeclaredNativeFixedCapabilities
from src.trading_runtime.entry_momentum_growth import EntryMomentumGrowthPolicy, growth_mask, declared_initial_entry
from src.trading_runtime.entry_spread_risk import EntrySpreadRiskPolicy, canonical_price_int, entry_spread_risk_mask, exact_epoch_us
from src.trading_runtime.strategy_initial_strong_momentum import initial_strong_momentum_entry_mask, InitialStrongMomentumWitness
from src.trading_runtime.strategy_initial_price_break import first_setup_price_break_mask, PREMARKET_END_MS, RESOLUTION_MS as PRICE_RESOLUTION_MS
from src.trading_runtime.strategy_entry_activity_fade import entry_activity_mask, AFTERHOURS_START_MS
from src.trading_runtime.strategy_episode_activity_veto import episode_activity_veto_mask
from .backtest_market_data import market_day_boundary
from datetime import date


def _token(parts, arrays=()):
    digest = sha256(json.dumps(parts, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    for array in arrays:
        digest.update(str(array.dtype).encode())
        digest.update(repr(array.shape).encode())
        digest.update(array.tobytes())
    return digest.hexdigest()


def _same_payload(left, right):
    return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(right, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _ticker_slices(keys):
    """Single source-ordered grouping; shared activity kernel requires one ticker."""
    starts = [i for i, key in enumerate(keys) if i == 0 or key[0] != keys[i-1][0]]
    return tuple((keys[start][0], slice(start, end)) for start, end in zip(starts, starts[1:] + [len(keys)]))


def _policy(capabilities):
    if type(capabilities) is not DeclaredNativeFixedCapabilities:
        raise ValueError("Declared entry needs exact typed capabilities")
    capabilities.__post_init__()
    value = capabilities.payload()["inherited"]["optional_policies"]["entry_momentum_growth_policy"]
    if type(value) is not dict:
        raise ValueError("Declared entry requires an explicit supported momentum policy")
    policy = EntryMomentumGrowthPolicy(value["policy_id"], tuple(value["first_fraction"]), tuple(value["current_fraction"]))
    if not _same_payload(policy.payload(), value):
        raise ValueError("Declared momentum policy payload differs")
    from src.trading_runtime.strategy_initial_price_break import initial_price_break_policy_payload
    from src.trading_runtime.strategy_entry_activity_fade import entry_activity_policy_payload
    from src.trading_runtime.strategy_episode_activity_veto import episode_activity_veto_policy_payload
    price = initial_price_break_policy_payload()
    price.update(first_momentum="declared_entry_momentum_growth_policy_first_fraction",
                 current_momentum="declared_entry_momentum_growth_policy_current_fraction",
                 afterhours_policy="unchanged_parent50_price_requirements")
    policies = capabilities.payload()["inherited"]["policies"]
    for name, expected in (("first_price_break_policy", price),
                           ("entry_activity_policy", entry_activity_policy_payload()),
                           ("episode_activity_policy", episode_activity_veto_policy_payload())):
        if not _same_payload(policies.get(name), expected):
            raise ValueError("Declared producer policy is unsupported: " + name)
    return policy


def _spread(parent, candidate):
    inherited = parent.capabilities.payload()["inherited"]["optional_policies"]["entry_spread_risk_policy"]
    policy = None
    if inherited is not None:
        policy = EntrySpreadRiskPolicy(inherited["policy_id"], tuple(inherited["maximum_spread_original_risk"]))
        if not _same_payload(policy.payload(), inherited):
            raise ValueError("Declared inherited spread policy differs")
    if candidate is not None:
        from src.trading_runtime.declared_native_fixed_candidate import NativeFixedCandidateSpec
        if type(candidate) is not NativeFixedCandidateSpec or candidate.base != parent.capabilities:
            raise ValueError("Declared candidate differs from source capabilities")
        candidate.__post_init__()
        if candidate.delta.spread is not None:
            policy = candidate.delta.spread
    return policy


def _attempts(market, candidates, keys, stage):
    if type(market) is not CertifiedMarketDayPlan or len(market.sessions) != 1:
        raise ValueError("Declared producer needs one exact certified market session")
    units = {(u.session_date, u.ticker): u for u in market.units if u.stage == stage}
    if len(units) != sum(u.stage == stage for u in market.units):
        raise ValueError("Declared producer attempts are ambiguous")
    coverage = {(u.session_date, u.ticker): u for u in candidates.coverage}
    if len(coverage) != len(candidates.coverage):
        raise ValueError("Declared candidate coverage is ambiguous")
    attempts = []
    for ticker, _ in keys:
        unit = units.get((market.sessions[0], ticker))
        candidate = coverage.get((market.sessions[0], ticker))
        if (unit is None or candidate is None or unit.build_id != market.build_id
                or (stage == "bars" and unit.attempt_id != candidate.source_attempts[0])
                or (stage == "technical" and unit.attempt_id != candidate.source_attempts[1])
                or (stage == "broker_100ms" and unit.attempt_id != candidate.source_attempts[2])
                or str(UUID(unit.attempt_id)) != unit.attempt_id or not UUID(unit.attempt_id).int):
            raise ValueError("Declared producer attempt differs from certified candidate")
        attempts.append(unit.attempt_id)
    return tuple(attempts)


@dataclass(frozen=True, slots=True)
class DeclaredMomentumPlan:
    capabilities: DeclaredNativeFixedCapabilities
    market: CertifiedMarketDayPlan
    candidates: CertifiedCandidatePlan
    entry: CertifiedEntryEvidencePlan
    momentum: CertifiedRisingMomentumPlan
    base: StrategyOneStaticGate
    first_indices: np.ndarray
    eligible_mask: np.ndarray
    token: str

    def __post_init__(self):
        first, eligible, token = _momentum_selection(self.capabilities, self.market, self.candidates, self.entry, self.momentum, self.base)
        for name, expected in (("first_indices", first), ("eligible_mask", eligible)):
            actual = getattr(self, name)
            if type(actual) is not np.ndarray or actual.dtype != expected.dtype or not np.array_equal(actual, expected):
                raise ValueError("Declared first anchor or momentum selection differs")
            object.__setattr__(self, name, _frozen(actual))
        if self.token != token:
            raise ValueError("Declared momentum source seal differs")

    def index(self, ticker, boundary_ms):
        from bisect import bisect_left
        if type(ticker) is not str or type(boundary_ms) is not int:
            raise ValueError("Declared lookup needs exact typed keys")
        index = bisect_left(self.momentum.keys, (ticker, boundary_ms))
        if index == len(self.momentum.keys) or self.momentum.keys[index] != (ticker, boundary_ms):
            raise ValueError("Declared lookup is outside source keys")
        return index

    def initial(self, index):
        first = int(self.first_indices[index])
        if first < 0:
            raise ValueError("Declared candidate lacks original first anchor")
        return InitialStrongMomentumWitness(self.base.facts[index].episode_start_ms, self.momentum.lookup(*self.momentum.keys[first]))


def _momentum_selection(capabilities, market, candidates, entry, momentum, base):
    policy = _policy(capabilities)
    if (type(market) is not CertifiedMarketDayPlan or type(candidates) is not CertifiedCandidatePlan or type(entry) is not CertifiedEntryEvidencePlan
            or type(momentum) is not CertifiedRisingMomentumPlan or type(base) is not StrategyOneStaticGate
            or momentum.source_build_id != market.build_id or momentum.market_plan_token != market.token
            or momentum.source_build_id != candidates.source_build_id or momentum.candidate_plan_token != candidates.token):
        raise ValueError("Declared momentum source identity differs")
    from .backtest_declared_base_entry_gate import compile_declared_base_entry_gate
    expected = compile_declared_base_entry_gate(candidates, entry, capabilities=capabilities)
    if (base.facts != expected.facts or not np.array_equal(base.rejection_mask, expected.rejection_mask)
            or not np.array_equal(base.eligible_indices, expected.eligible_indices)):
        raise ValueError("Declared base gate differs from sealed policies")
    keys = tuple((fact.ticker, fact.boundary_ms) for fact in base.facts)
    if momentum.source_attempts != _attempts(market, candidates, keys, "technical"):
        raise ValueError("Declared momentum technical attempts differ")
    if keys != momentum.keys or np.any((base.rejection_mask == 0) & ~momentum.requested_mask):
        raise ValueError("Declared momentum omits original structural candidates")
    starts = np.asarray([fact.episode_start_ms for fact in base.facts], dtype=np.int64)
    boundaries = np.asarray([key[1] for key in keys], dtype=np.int64)
    _, groups = np.unique(np.asarray([key[0] for key in keys]), return_inverse=True)
    args = (momentum.current_boundaries_ms, momentum.prior_boundaries_ms, momentum.current_line, momentum.current_signal, momentum.prior_line, momentum.prior_signal)
    current = growth_mask(policy, policy.current_fraction, boundaries, *args)
    first_mask = growth_mask(policy, policy.first_fraction, boundaries, *args)
    first, _ = initial_strong_momentum_entry_mask(groups.astype(np.int64), starts, boundaries, base.rejection_mask == 0, current)
    eligible = (base.rejection_mask == 0) & current & (first >= 0)
    if len(first):
        eligible &= first_mask[np.maximum(first, 0)]
    token = _token([capabilities.payload(), market.token, candidates.token, entry.token, momentum.token], (first, eligible))
    return first, eligible, token


def compile_declared_momentum_plan(capabilities, market, candidates, entry, momentum):
    from .backtest_declared_base_entry_gate import compile_declared_base_entry_gate
    base = compile_declared_base_entry_gate(candidates, entry, capabilities=capabilities)
    return DeclaredMomentumPlan(capabilities, market, candidates, entry, momentum, base,
                                *_momentum_selection(capabilities, market, candidates, entry, momentum, base))


@dataclass(frozen=True, slots=True)
class DeclaredEntrySourcePlan:
    parent: DeclaredMomentumPlan
    price_columns: tuple[np.ndarray, ...]
    activity_columns: tuple[np.ndarray, ...]
    quote_columns: tuple[np.ndarray, ...]
    candidate: object | None
    source_attempts: tuple[tuple[str, ...], tuple[str, ...]]
    eligible_mask: np.ndarray
    token: str

    def __post_init__(self):
        if type(self.parent) is not DeclaredMomentumPlan:
            raise ValueError("Declared source requires exact momentum parent")
        n = len(self.parent.momentum.keys)
        specs = ((self.price_columns, (np.int64, np.int64, np.uint64, np.uint64, np.bool_, np.bool_), (n,)),
                 (self.activity_columns, (np.int64, np.uint64, np.bool_), (n, 4)),
                 (self.quote_columns, (np.int64,) * 4, (n,)))
        for arrays, dtypes, shape in specs:
            if type(arrays) is not tuple or len(arrays) != len(dtypes) or any(type(a) is not np.ndarray or a.dtype != dtype or a.shape != shape for a, dtype in zip(arrays, dtypes)):
                raise ValueError("Declared producer columns have wrong shape or type")
        attempts = (_attempts(self.parent.market, self.parent.candidates, self.parent.momentum.keys, "bars"),
                    _attempts(self.parent.market, self.parent.candidates, self.parent.momentum.keys, "broker_100ms"))
        if self.source_attempts != attempts:
            raise ValueError("Declared source attempts differ")
        spread = _spread(self.parent, self.candidate)
        expected = _source_selection(self.parent, self.price_columns, self.activity_columns, self.quote_columns, spread)
        token = _source_token(self.parent, attempts, self.candidate, self.price_columns, self.activity_columns, self.quote_columns, expected)
        if self.token != token or type(self.eligible_mask) is not np.ndarray or self.eligible_mask.dtype != np.bool_ or not np.array_equal(self.eligible_mask, expected):
            raise ValueError("Declared source selection seal differs")
        for name in ("price_columns", "activity_columns", "quote_columns"):
            object.__setattr__(self, name, tuple(_frozen(a) for a in getattr(self, name)))
        object.__setattr__(self, "eligible_mask", _frozen(self.eligible_mask))


def _before_quote_selection(parent, price, activity):
    n = len(parent.first_indices)
    boundaries = np.asarray([key[1] for key in parent.momentum.keys], dtype=np.int64)
    safe = np.maximum(parent.first_indices, 0)
    # Price evidence is selected on immutable original anchors, never survivors.
    price_ok = np.zeros(n, dtype=np.bool_)
    if n:
        price_ok = first_setup_price_break_mask(boundaries, *price)[safe]
    before = parent.eligible_mask & price_ok
    activity_ok = np.zeros(n, dtype=np.bool_)
    for _, scope in _ticker_slices(parent.momentum.keys):
        activity_ok[scope] = entry_activity_mask(boundaries[scope], *(a[scope] for a in activity))
    opening = np.where(boundaries >= AFTERHOURS_START_MS, AFTERHOURS_START_MS, 0)
    complete = activity[2].all(axis=1)
    history = parent.capabilities.payload()["inherited"]["policies"]["entry_activity_policy"]["history_activation_ms"]
    faded = before & (boundaries >= opening + history) & complete & ~activity_ok
    facts = parent.base.facts
    changed = np.asarray([i == 0 or (f.ticker, f.episode_start_ms) != (facts[i-1].ticker, facts[i-1].episode_start_ms) for i, f in enumerate(facts)], dtype=np.bool_)
    groups = np.cumsum(changed, dtype=np.int64) - 1
    eligible, _ = episode_activity_veto_mask(groups, before, before & activity_ok, faded)
    return eligible


def _source_token(parent, attempts, candidate, price, activity, quote, eligible):
    return _token([parent.token, attempts, None if candidate is None else candidate.payload()], (*price, *activity, *quote, eligible))


def _source_selection(parent, price, activity, quote, spread):
    eligible = _before_quote_selection(parent, price, activity)
    boundaries = np.asarray([key[1] for key in parent.momentum.keys], dtype=np.int64)
    bid, ask, timestamp, valid = quote
    now = np.asarray([exact_epoch_us(market_day_boundary(date.fromisoformat(parent.market.sessions[0]), int(b))) for b in boundaries], dtype=np.int64)
    if np.any((valid != 0) & (valid != 1)) or np.any(timestamp < 0) or np.any(timestamp > now):
        raise ValueError("Declared source has malformed or future quote")
    # Freshness remains solely in the unchanged common scalar financial kernel;
    # this preparation stage establishes exact quote identity/causal availability.
    eligible &= (valid == 1) & (timestamp > 0) & (bid > 0) & (bid <= ask)
    if spread is not None:
        stops = np.asarray([canonical_price_int(f.stop_price) if f.protection_valid else 0 for f in parent.base.facts], dtype=np.int64)
        eligible &= entry_spread_risk_mask(spread, bid, ask, stops)
    return eligible


def _arrow(client, query, names, types, requested):
    stream = client.iter_arrow_record_batches(assert_select_only(query))
    rows, seen = [], set()
    try:
        for batch in stream:
            if (not isinstance(batch, pa.RecordBatch) or tuple(batch.schema.names) != names or batch.nbytes > 1048576
                    or any(batch.schema.field(name).type != dtype or batch.column(name).null_count for name, dtype in zip(names, types))):
                raise ValueError("Declared bars violate exact bounded Arrow schema")
            columns = [batch.column(i).to_pylist() for i in range(len(names))]
            for row in zip(*columns):
                if row[0] not in requested or row[0] in seen:
                    raise ValueError("Declared bars contain foreign or duplicate keys")
                seen.add(row[0]); rows.append(row)
            if len(rows) > len(requested):
                raise ValueError("Declared bars exceed requested source")
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()
    return rows


def load_declared_entry_source_plan(parent, *, client, candidate=None, quote_source_contract="declared-entry-spread-risk-quote-source@2"):
    """Read existing certified bars/quote@2 using own selection, in512-key batches.

    Missing bars reject admission without carrying; missing exact quote coverage
    fails source preparation. This prepared object is not native execution proof.
    """
    if type(parent) is not DeclaredMomentumPlan or quote_source_contract != "declared-entry-spread-risk-quote-source@2":
        raise ValueError("Declared source contract or policy is unsupported")
    spread_policy = _spread(parent, candidate)
    market, keys = parent.market, parent.momentum.keys
    activity_policy = parent.capabilities.payload()["inherited"]["policies"]["entry_activity_policy"]
    resolution, history = activity_policy["resolution_ms"], activity_policy["history_activation_ms"]
    if not {PRICE_RESOLUTION_MS, resolution}.issubset(market.required_resolutions_ms):
        raise ValueError("Declared source lacks certified bar resolutions")
    bars = _attempts(market, parent.candidates, keys, "bars")
    broker = _attempts(market, parent.candidates, keys, "broker_100ms")
    n = len(keys)
    price = tuple(np.zeros(n, dtype=dtype) for dtype in (np.int64, np.int64, np.uint64, np.uint64, np.bool_, np.bool_))
    activity = (np.full((n, 4), -1, dtype=np.int64), np.zeros((n, 4), dtype=np.uint64), np.zeros((n, 4), dtype=np.bool_))
    quote = tuple(np.zeros(n, dtype=np.int64) for _ in range(4))
    for ticker, scope in _ticker_slices(keys):
        indices = range(scope.start, scope.stop)
        attempt = bars[indices[0]]
        def read_bars(resolution, targets, names, types):
            buckets = sorted({(b + SESSION_OPEN_OFFSET_MS)//resolution-1 for b in targets if b > 0})
            found = {}
            for offset in range(0, len(buckets), 512):
                chunk = buckets[offset:offset+512]
                sql = f"SELECT {','.join(names)} FROM arte.bars_v1 WHERE build_id={_literal(market.build_id)} AND session_date=toDate({_literal(market.sessions[0])}) AND ticker={_literal(ticker)} AND attempt_id=toUUID({_literal(attempt)}) AND resolution_ms={resolution} AND bucket_index IN ({','.join(map(str,chunk))}) ORDER BY bucket_index FORMAT ArrowStream"
                for row in _arrow(client, sql, names, types, set(chunk)):
                    found[(row[0]+1)*resolution-SESSION_OPEN_OFFSET_MS] = row[1:]
            return found
        anchors = {int(parent.first_indices[i]) for i in indices if parent.first_indices[i] >= 0 and parent.eligible_mask[i]}
        anchors = {i for i in anchors if keys[i][1] < PREMARKET_END_MS}
        targets = {b for i in anchors for b in (keys[i][1]//PRICE_RESOLUTION_MS*PRICE_RESOLUTION_MS, keys[i][1]//PRICE_RESOLUTION_MS*PRICE_RESOLUTION_MS-PRICE_RESOLUTION_MS)}
        values = read_bars(PRICE_RESOLUTION_MS, targets, ("bucket_index", "close_int", "high_int", "price_valid", "extremes_valid"), (pa.uint32(), pa.uint64(), pa.uint64(), pa.uint8(), pa.uint8()))
        for i in anchors:
            for side, b in enumerate((keys[i][1]//PRICE_RESOLUTION_MS*PRICE_RESOLUTION_MS, keys[i][1]//PRICE_RESOLUTION_MS*PRICE_RESOLUTION_MS-PRICE_RESOLUTION_MS)):
                if b in values:
                    close, high, valid, extremes = values[b]
                    if valid not in (0, 1) or extremes not in (0, 1):
                        raise ValueError("Declared price flags are malformed")
                    price[side][i] = b; price[side+2][i] = close if side == 0 else high; price[side+4][i] = bool(valid if side == 0 else extremes)
        selected = [i for i in indices if parent.eligible_mask[i] and keys[i][1] >= (AFTERHOURS_START_MS if keys[i][1] >= AFTERHOURS_START_MS else 0)+history]
        offsets = tuple(range(history-resolution, -1, -resolution))
        targets = {keys[i][1]//resolution*resolution-off for i in selected for off in offsets}
        values = read_bars(resolution, targets, ("bucket_index", "resolution_ms", "trade_count"), (pa.uint32(), pa.uint32(), pa.uint64()))
        for i in selected:
            for column, off in enumerate(offsets):
                b = keys[i][1]//resolution*resolution-off
                if b in values:
                    observed_resolution, count = values[b]
                    if observed_resolution != resolution:
                        raise ValueError("Declared activity resolution differs")
                    activity[0][i,column] = b; activity[1][i,column] = count; activity[2][i,column] = True
    # The new exact adapter retains quote@2 qualified UUID identity and aliases.
    selected = np.flatnonzero(_before_quote_selection(parent, price, activity)).tolist()
    for offset in range(0, len(selected), 512):
        batch = selected[offset:offset+512]
        requested = {(keys[i][0], (keys[i][1]+SESSION_OPEN_OFFSET_MS)//100-1, broker[i]):i for i in batch}
        scope = ','.join(f'({_literal(t)},{b},toUUID({_literal(a)}))' for t,b,a in requested)
        sql = assert_select_only("SELECT l.ticker AS ticker,l.bucket_index AS bucket_index,toString(l.attempt_id) AS liquidity_attempt_id,l.bid_int AS bid_int,l.ask_int AS ask_int,l.quote_timestamp_us AS quote_timestamp_us,l.quote_valid AS quote_valid FROM arte.liquidity_100ms_v1 AS l "
            f"WHERE l.build_id={_literal(market.build_id)} AND l.session_date=toDate({_literal(market.sessions[0])}) AND (l.ticker,l.bucket_index,l.attempt_id) IN ({scope}) SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow")
        seen = set()
        for line in client.execute(sql).splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            fields = {"ticker", "bucket_index", "liquidity_attempt_id", "bid_int", "ask_int", "quote_timestamp_us", "quote_valid"}
            if (type(row) is not dict or set(row) != fields or type(row["ticker"]) is not str
                    or type(row["bucket_index"]) is not int or type(row["liquidity_attempt_id"]) is not str):
                raise ValueError("Declared quote has wrong exact projection")
            key = (row["ticker"], row["bucket_index"], row["liquidity_attempt_id"])
            if key not in requested or key in seen or any(type(row[name]) is not int for name in ("bid_int", "ask_int", "quote_timestamp_us", "quote_valid")):
                raise ValueError("Declared quote has foreign, duplicate or malformed source")
            seen.add(key)
            for column, name in zip(quote, ("bid_int", "ask_int", "quote_timestamp_us", "quote_valid")):
                column[requested[key]] = row[name]
        if seen != set(requested):
            raise ValueError("Declared quote coverage is incomplete")
    eligible = _source_selection(parent, price, activity, quote, spread_policy)
    token = _source_token(parent, (bars, broker), candidate, price, activity, quote, eligible)
    return DeclaredEntrySourcePlan(parent, price, activity, quote, candidate, (bars, broker), eligible, token)
