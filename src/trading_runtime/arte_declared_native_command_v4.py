"""Normalized own ENTRY source-equivalence packet, not installed admission.

No table installation, writer routing or historical-financial approval occurs
here. Producer witnesses are reloaded through the full managed declaration;
the requested committed predecessor remains a request until its native hook
independently attests Portfolio/broker state.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime, timezone
from decimal import Context, Decimal, localcontext
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from uuid import UUID, NAMESPACE_URL, uuid5

from .arte_journal_schema import TableContract
from .journal_contract import JournalRecord, canonical_json
from .arte_journal_writer import TypedJournalBatch
from .arte_intent_projection import strategy_intent_batch
from .declared_native_submission import DeclaredNativeSubmission, DeclaredSubmissionBinding, declared_entry_intent
from .arte_declared_native_fixed_sources import DeclaredHistoricalPredecessor
from .arte_declared_native_managed_sources import PreparedDeclaredManagedSourceResolver
from .declared_native_managed_execution import DeclaredNativeManagedExecutionSpec, verify_declared_managed_configuration
from src.backend.backtest_declared_native_fixed_entry import DeclaredNativeFixedEntryProposal, DeclaredEntryPreparation
from src.backend.backtest_declared_native_fixed_assignments import prepare_declared_managed_assignment_plan

from .arte_declared_native_entry_schema import (
    CONTRACT, _COMMON, _table, ENTRY, MOMENTUM, PRICE, ACTIVITY, CANDLE, TABLES
)


def _hash(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def _exact(left, right):
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(_exact(left[k], right[k]) for k in left)
    if type(left) is not type(right):
        return False
    if is_dataclass(left):
        return all(_exact(getattr(left,f.name),getattr(right,f.name)) for f in fields(left))
    if type(left) is tuple:
        return len(left) == len(right) and all(_exact(a,b) for a,b in zip(left,right))
    return left == right


def _scalar(value, kind):
    if kind.startswith('Nullable('):
        return None if value is None else _scalar(value, kind[9:-1])
    if kind.startswith('UInt') or kind.startswith('Int'):
        bits = int(kind.removeprefix('UInt').removeprefix('Int'))
        low, high = (0, (1 << bits)-1) if kind.startswith('UInt') else (-(1 << (bits-1)), (1 << (bits-1))-1)
        if type(value) is not int or not low <= value <= high:
            raise ValueError('Declared companion integer scalar differs')
    elif kind == 'Float64':
        if type(value) is not float or not isfinite(value):
            raise ValueError('Declared companion Float64 scalar differs')
    elif kind == 'Decimal(38,18)':
        if type(value) is not str:
            raise ValueError('Declared companion amount requires canonical lexical Decimal')
        with localcontext(Context(prec=50)):
            number = Decimal(value)
            if not number.is_finite() or abs(number) >= Decimal('1e20'):
                raise ValueError('Declared companion amount overflows')
            exact = number.quantize(Decimal('1e-18'))
            if exact != number or format(exact, 'f') != value:
                raise ValueError('Declared companion amount is offscale or noncanonical')
    else:
        if type(value) is not str:
            raise ValueError('Declared companion text scalar differs')
        if kind == 'UUID' and (str(UUID(value)) != value or not UUID(value).int):
            raise ValueError('Declared companion UUID differs')
        if kind == 'Date' and date.fromisoformat(value).isoformat() != value:
            raise ValueError('Declared companion date differs')
        if kind == 'FixedString(64)' and (len(value) != 64 or any(c not in '0123456789abcdef' for c in value)):
            raise ValueError('Declared companion digest differs')
    return value


def _money(value):
    if type(value) is not float or not isfinite(value):
        raise ValueError('Declared companion original amount differs')
    with localcontext(Context(prec=50)):
        number = Decimal(str(value))
        exact = number.quantize(Decimal('1e-18'))
        if exact != number:
            raise ValueError('Declared companion original amount cannot fit losslessly')
        return _scalar(format(exact,'f'), 'Decimal(38,18)')


def _seal(contract, row):
    if type(row) is not dict or set(row) != {name for name, _ in contract.columns} - {'content_hash'}:
        raise ValueError('Declared companion has missing or extra columns')
    result = {name: _scalar(row[name], kind) for name, kind in contract.columns if name != 'content_hash'}
    return MappingProxyType({**result, 'content_hash': _hash(result)})


@dataclass(frozen=True, slots=True)
class DeclaredEntryRows:
    base: TypedJournalBatch
    families: tuple

    def __post_init__(self):
        if type(self.base) is not TypedJournalBatch or type(self.families) is not tuple:
            raise ValueError('Declared entry rows need exact normalized packet')
        if tuple(name for name, _ in self.families) != tuple(t.name for t in TABLES):
            raise ValueError('Declared entry family ordering/coverage differs')
        normalized = []
        for contract, (_, rows) in zip(TABLES, self.families, strict=True):
            if type(rows) is not tuple:
                raise ValueError('Declared entry row ordering requires tuple')
            copied = []
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ValueError('Declared entry row is not scalar mapping')
                sealed = _seal(contract, {k:v for k,v in row.items() if k != 'content_hash'})
                if row.get('content_hash') != sealed['content_hash']:
                    raise ValueError('Declared companion row hash differs')
                copied.append(sealed)
            normalized.append((contract.name, tuple(copied)))
        object.__setattr__(self, 'families', tuple(normalized))


def _authority(resolver, spec, envelope, approval, market, proposal, predecessor):
    if (type(resolver) is not PreparedDeclaredManagedSourceResolver
            or type(spec) is not DeclaredNativeManagedExecutionSpec
            or type(predecessor) is not DeclaredHistoricalPredecessor):
        raise ValueError('Declared entry requires full managed source and typed predecessor request')
    predecessor.__post_init__()
    prefix = predecessor.prefix
    if (type(prefix.last_sequence) is not int or type(prefix.source_cursor) is not str
            or not prefix.source_cursor or type(prefix.batch_ids) is not tuple
            or len(set(prefix.batch_ids)) != len(prefix.batch_ids)):
        raise ValueError('Declared predecessor prefix scalar/coverage differs')
    for value in prefix.batch_ids:
        _scalar(value,'UUID')
    verify_declared_managed_configuration(resolver.client, spec, envelope, approval=approval)
    # Each independent source read checks the actual full child configuration.
    if (resolver.market != market or resolver.run_id != proposal.run_id
            or _hash(resolver._managed_spec.payload()) != _hash(spec.payload())):
        raise ValueError('Declared entry source/full specification differs')
    facts = resolver.reconstruct_entry_facts(proposal)
    assignments = prepare_declared_managed_assignment_plan(resolver.client, run_id=proposal.run_id,
        spec=spec, envelope=envelope, approval=approval, market=market)
    scope = assignments.resolve(proposal.assignment_id, account_id=proposal.account_id,
        ticker=proposal.ticker, strategy_id=proposal.strategy_id, revision=proposal.revision)
    if (facts.configuration_hash != envelope['payload_hash'] or assignments.configuration_hash != facts.configuration_hash
            or predecessor.run_id != proposal.run_id or predecessor.configuration_hash != facts.configuration_hash
            or predecessor.market_plan_token != market.token or predecessor.boundary_ms != proposal.boundary_ms):
        raise ValueError('Declared entry predecessor/configuration scope differs')
    return facts, assignments, scope


def _families(base, proposal, facts, assignments, scope, spec, market, predecessor):
    event, = base.events
    common = dict(parent_record_id=event['record_id'], run_id=base.run_id,
                  event_month=event['event_month'], batch_id=base.batch_id)
    def row(contract, key, value):
        return _seal(contract, dict(record_id=str(uuid5(NAMESPACE_URL, event['record_id']+':'+CONTRACT+':'+key)),
                                   **common, **value))
    parent = {f.name:getattr(proposal,f.name) for f in fields(proposal)}
    parent.pop('run_id'); parent.pop('source_token')
    for name in ('reference_ask','initial_stop','initial_target','frozen_gap'):
        parent[name] = _money(parent[name])
    growth = spec.execution.candidate.base.payload()['inherited']['optional_policies']['entry_momentum_growth_policy']
    spread = spec.execution.candidate.delta.spread
    if spread is None:
        inherited = spec.execution.candidate.base.payload()['inherited']['optional_policies']['entry_spread_risk_policy']
        if inherited is not None:
            from .entry_spread_risk import EntrySpreadRiskPolicy
            spread = EntrySpreadRiskPolicy(inherited['policy_id'], tuple(inherited['maximum_spread_original_risk']))
    units = {stage: tuple(u for u in market.units if u.ticker == proposal.ticker and u.stage == stage)
             for stage in ('bars','technical','broker_100ms')}
    if any(len(value) != 1 for value in units.values()):
        raise ValueError('Declared entry producer coverage is not unique')
    parent.update(companion_contract=CONTRACT, source_token=proposal.source_token,
        account_key=scope.account_key, conid=scope.conid, assignment_plan_token=assignments.token,
        session_date=market.sessions[0], configuration_hash=facts.configuration_hash,
        managed_spec_hash=_hash(spec.payload()), entry_request_policy_id=spec.execution.entry_request.policy_id,
        entry_request_hash=_hash(spec.execution.entry_request.payload()), quote_source_contract=spec.execution.entry_source.quote_source_contract,
        source_build_id=market.build_id, market_plan_token=market.token,
        candidate_plan_token=facts.activity.candidate_plan_token, entry_plan_token=facts.activity.entry_plan_token,
        selection_token=facts.activity.parent_selection_token, identity_token=assignments.identity_token,
        bars_attempt_id=units['bars'][0].attempt_id, technical_attempt_id=units['technical'][0].attempt_id,
        broker_attempt_id=units['broker_100ms'][0].attempt_id,
        first_numerator=growth['first_fraction'][0], first_denominator=growth['first_fraction'][1],
        current_numerator=growth['current_fraction'][0], current_denominator=growth['current_fraction'][1],
        spread_policy_id=None if spread is None else spread.policy_id,
        spread_numerator=None if spread is None else spread.maximum_spread_original_risk[0],
        spread_denominator=None if spread is None else spread.maximum_spread_original_risk[1],
        bid_int=facts.quote[0], ask_int=facts.quote[1], quote_timestamp_us=facts.quote[2], quote_valid=facts.quote[3],
        predecessor_batch_id=predecessor.prior_batch_id, predecessor_sequence=predecessor.prior_sequence,
        predecessor_cursor=predecessor.prefix.source_cursor, portfolio_state_hash=predecessor.portfolio_state_hash,
        broker_snapshot_hash=predecessor.broker_snapshot_hash)
    momentum = []
    for anchor, witness in (('current',facts.momentum), ('first',facts.initial.first_setup)):
        for observation in witness.observations:
            value = {f.name:getattr(observation,f.name) for f in fields(observation)}
            value.update(ordinal=len(momentum), anchor=anchor, ticker=witness.ticker,
                         boundary_ms=witness.boundary_ms, source_build_id=witness.source_build_id,
                         source_attempt_id=witness.source_attempt_id, market_plan_token=witness.market_plan_token)
            momentum.append(row(MOMENTUM, str(len(momentum)), value))
    price = {f.name:getattr(facts.first_price,f.name) for f in fields(facts.first_price)}
    for name in ('current_price_valid','prior_extremes_valid'):
        price[name] = int(price[name])
    activity = {f.name:getattr(facts.activity,f.name) for f in fields(facts.activity) if f.name != 'candles'}
    candles = tuple(row(CANDLE,str(i),dict(ordinal=i,observed=int(c is not None),
        boundary_ms=None if c is None else c.boundary_ms,trade_count=None if c is None else c.trade_count))
        for i,c in enumerate(facts.activity.candles))
    return ((ENTRY.name,(row(ENTRY,'entry',parent),)), (MOMENTUM.name,tuple(momentum)),
            (PRICE.name,(row(PRICE,'price',price),)), (ACTIVITY.name,(row(ACTIVITY,'activity',activity),)),
            (CANDLE.name,candles))


def project_declared_entry(record, submission, *, resolver, spec, envelope, approval, market,
                           predecessor, attempt_id, batch_id, run_month, source_cursor):
    """Validate exact own journal record, then normalize source-equivalent data."""
    if type(record) is not JournalRecord or type(submission) is not DeclaredNativeSubmission:
        raise ValueError('Declared entry projection needs exact own journal/submission')
    if (type(spec) is not DeclaredNativeManagedExecutionSpec
            or type(predecessor) is not DeclaredHistoricalPredecessor):
        raise ValueError('Declared entry projection needs full managed/predecessor types')
    p = submission.proposal
    if (type(record.sequence) is not int or type(record.event_time) is not datetime
            or type(record.recorded_at) is not datetime or record.event_time.tzinfo is None
            or record.recorded_at.tzinfo is None or type(run_month) is not date
            or run_month != submission.intent.event_time.astimezone(timezone.utc).date().replace(day=1)
            or type(source_cursor) is not str or source_cursor != predecessor.prefix.source_cursor):
        raise ValueError('Declared own original envelope scalar/clock differs')
    for value in (record.record_id,attempt_id,batch_id):
        _scalar(value,'UUID')
    submission.verify(run_id=p.run_id,strategy_id=p.strategy_id,strategy_revision=p.revision,
                      account_id=p.account_id,session_date=submission.binding.session_date)
    if (record.run_id != p.run_id or record.category != 'strategy' or record.entity_type != 'declared_native_intent'
            or record.entity_id != p.intent_id or record.account_id != p.account_id
            or record.event_time != submission.intent.event_time
            or canonical_json(record.payload) != canonical_json({**submission.intent.payload(),
                'strategy_id':p.strategy_id,'strategy_revision':p.revision})
            or submission.binding.configuration_hash != envelope['payload_hash']
            or submission.binding.entry_request != spec.execution.entry_request
            or submission.binding.execution_spec_token != _hash(spec.execution.payload())
            or record.sequence != predecessor.prior_sequence+1 or batch_id != predecessor.batch_id):
        raise ValueError('Declared entry original event/context differs')
    facts, assignments, scope = _authority(resolver,spec,envelope,approval,market,p,predecessor)
    base = strategy_intent_batch(submission.intent,run_id=p.run_id,run_month=run_month,account_id=p.account_id,
        attempt_id=attempt_id,batch_id=batch_id,prior_batch_id=predecessor.prior_batch_id,sequence=record.sequence,
        source_cursor=source_cursor,run_status='running',recorded_at=record.recorded_at,record_id=record.record_id,
        correlation_id="",causation_id="")
    event = dict(base.events[0],entity_type=record.entity_type)
    base = replace(base,events=(event,))
    return DeclaredEntryRows(base,_families(base,p,facts,assignments,scope,spec,market,predecessor))


def readback_declared_entry_source_equivalence(packet, *, resolver, spec, envelope, approval, market, predecessor):
    """Fresh producers and dated scopes, not financial admission or recovery."""
    if type(packet) is not DeclaredEntryRows:
        raise ValueError('Declared cold readback needs exact normalized packet')
    packet.__post_init__()
    _scalar(packet.base.attempt_id,'UUID')
    base = packet.base
    if (type(predecessor) is not DeclaredHistoricalPredecessor
            or type(base.first_sequence) is not int or base.first_sequence != predecessor.prior_sequence+1
            or base.last_sequence != base.first_sequence or base.batch_id != predecessor.batch_id
            or base.prior_batch_id != predecessor.prior_batch_id or base.source_cursor != predecessor.prefix.source_cursor
            or len(base.events) != 1 or type(base.run_month) is not date):
        raise ValueError('Declared cold predecessor/event envelope differs')
    rows = packet.families[0][1]
    if len(rows) != 1:
        raise ValueError('Declared cold entry parent coverage differs')
    row = rows[0]
    names = tuple(f.name for f in fields(DeclaredNativeFixedEntryProposal))
    values = {name:row[name] for name in names}
    for name in ('reference_ask','initial_stop','initial_target','frozen_gap'):
        values[name] = float(values[name])
    p = DeclaredNativeFixedEntryProposal(**values)
    facts, assignments, scope = _authority(resolver,spec,envelope,approval,market,p,predecessor)
    if packet.families != _families(packet.base,p,facts,assignments,scope,spec,market,predecessor):
        raise ValueError('Declared normalized witnesses differ from independently reloaded source')
    # Reproduce all request/protection scalar fields without copied financials.
    source, _, _, _ = resolver.reload_entry_plan()
    prep = DeclaredEntryPreparation(p.run_id,p.assignment_id,p.account_id,source)
    binding = DeclaredSubmissionBinding(prep,date.fromisoformat(market.sessions[0]),spec.execution.entry_request,
                                        envelope['payload_hash'],_hash(spec.execution.payload()))
    intent = declared_entry_intent(binding,p)
    if base.run_month != intent.event_time.astimezone(timezone.utc).date().replace(day=1):
        raise ValueError('Declared cold run month differs from original source')
    expected = strategy_intent_batch(intent,run_id=p.run_id,run_month=packet.base.run_month,account_id=p.account_id,
        attempt_id=packet.base.attempt_id,batch_id=packet.base.batch_id,prior_batch_id=predecessor.prior_batch_id,
        sequence=predecessor.prior_sequence+1,source_cursor=packet.base.source_cursor,run_status='running',
        recorded_at=datetime.fromisoformat(packet.base.events[0]['recorded_at']),record_id=packet.base.events[0]['record_id'],
        correlation_id="",causation_id="")
    expected = replace(expected,events=(dict(expected.events[0],entity_type='declared_native_intent'),))
    if not _exact(packet.base, expected):
        raise ValueError('Declared cold intent/event economics or envelope differs')
    return facts


def verify_declared_entry_financial_admission(packet, *, resolver, predecessor, **scope):
    """Never promote a source-equivalence receipt to historical cash authority."""
    readback_declared_entry_source_equivalence(packet,resolver=resolver,predecessor=predecessor,**scope)
    resolver.verify_historical_predecessor(predecessor)  # Existing native hook deliberately fails closed.
    raise RuntimeError('Declared own financial admission integration is not installed')
