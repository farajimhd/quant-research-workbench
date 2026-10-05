"""Native cold authority for the immutable-permission ladder baseline.

Market plans are certified separately. Entry permission comes from the exact
sealed run configuration; every committed control command invalidates this
baseline. Cash, reservations, positions and accepted orders come from native
as-of Portfolio/broker/OMS sources, never an unstored Strategy financial view.
"""
from dataclasses import dataclass, asdict, replace
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.journal_contract import canonical_json


@dataclass(frozen=True, slots=True)
class NativeLadderMarketContext:
    run_id: str
    session_date: date
    configuration: object
    observations: object
    market: object
    v7: object
    pivots: object
    tick_int: int
    stop_buffer_ticks: int
    break_buffer_ticks: int
    gate_policy: object
    source_through_boundary_ms: int

    def market_policy_payload(self):
        return {'gate': asdict(self.gate_policy), 'tick_int': self.tick_int,
            'stop_buffer_ticks': self.stop_buffer_ticks,
            'break_buffer_ticks': self.break_buffer_ticks,
            'source_through_boundary_ms': self.source_through_boundary_ms}

    def verify_policy(self, policy):
        from hashlib import sha256
        from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
        from src.backend.backtest_market_data import CertifiedMarketDayPlan
        from src.backend.backtest_squeeze_ladder_loader import PreparedLadderObservations
        from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan
        from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
        from src.trading_runtime.squeeze_ladder_columnar import LadderGatePolicy
        if (type(self.configuration) is not CertifiedStrategyOneConfiguration
                or not isinstance(self.market, CertifiedMarketDayPlan)
                or not isinstance(self.observations, PreparedLadderObservations)
                or not isinstance(self.v7, CertifiedV7IntervalPlan)
                or not isinstance(self.pivots, CertifiedPivotPlan)
                or type(self.gate_policy) is not LadderGatePolicy
                or type(self.source_through_boundary_ms) is not int
                or not 0 < self.source_through_boundary_ms <= 57_600_000
                or self.source_through_boundary_ms % 100
                or self.observations.gate.certified_history_through_ms != self.source_through_boundary_ms
                or self.market.sessions != (self.session_date.isoformat(),)
                or self.observations.market_plan_token != self.market.token
                or self.observations.source_build_id != self.market.build_id
                or self.v7.source_build_id != self.market.build_id
                or self.pivots.source_build_id != self.market.build_id
                or self.v7.session_date != self.session_date.isoformat()
                or self.pivots.session_date != self.session_date.isoformat()
                or sha256(canonical_json(self.configuration.payload).encode()).hexdigest()
                    != self.configuration.payload_hash
                or type(self.tick_int) is not int or self.tick_int <= 0
                or type(self.stop_buffer_ticks) is not int or self.stop_buffer_ticks < 0
                or type(self.break_buffer_ticks) is not int or self.break_buffer_ticks < 0
                or self.configuration.payload['strategy']['numbered_release'].get(
                    'automatic_entry_policy') != policy.payload()
                or canonical_json(self.configuration.payload['strategy']['numbered_release'].get(
                    'automatic_market_policy')) != canonical_json(self.market_policy_payload())):
            raise ValueError('Ladder source lacks its sealed declared automatic policy')


def reconstruct_native_ladder_market(context, *, client):
    """Independently read the pinned producer products, including gate operands."""
    from src.backend.backtest_market_data import verify_market_day_plan
    from src.backend.structural_v7_seed import certified_seed_plan
    from src.backend.backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
    from src.backend.backtest_strategy_one_pivot_store import certify_pivot_plan
    from src.backend.fixed_bar_signal import canonical_stream_activation, load_first_squeeze_occurrences
    from src.backend.backtest_squeeze_ladder_loader import load_ladder_observations
    verify_market_day_plan(context.market, client)
    seeds = certified_seed_plan(context.market, client)
    scope = (context.observations.ticker,)
    day = context.session_date.isoformat()
    v7 = certify_v7_interval_plan(context.market, seeds, session_date=day,
        candidate_tickers=scope, client=client)
    pivots = certify_pivot_plan(context.market, session_date=day,
        candidate_tickers=scope, client=client)
    if v7.token != context.v7.token or pivots.token != context.pivots.token:
        raise ValueError('Ladder cold structural sources differ from pinned attempts')
    stream, activation = canonical_stream_activation()
    scan = load_first_squeeze_occurrences(context.market, stream=stream, activation=activation,
        through_boundary_ms=context.source_through_boundary_ms, client=client)
    observations, = load_ladder_observations(context.market, session_date=day, tickers=scope,
        through_boundary_ms=context.source_through_boundary_ms, certified_scan=scan,
        policy=context.gate_policy, client=client)
    if observations.scan_content_hash != context.observations.scan_content_hash:
        raise ValueError('Ladder cold admission observations differ from certified source')
    return replace(context, observations=observations, v7=v7, pivots=pivots)


def verify_native_ladder_entry(client, prefix, request, batch):
    """Verify a source parent against the exact native pre-entry cursor.

    The caller independently verifies the V4 prefix and supplies a SELECT-only
    principal. This runs in the writer/cold lane, never in the 100ms loop.
    It requires freshly captured running snapshots at the decision boundary;
    missing snapshots stop publication and hence command dispatch.
    """
    from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
    from src.trading_runtime.arte_journal_writer import load_typed_run_context, _rows, _literal, _committed_batch_filter
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
    from src.trading_runtime.arte_portfolio_snapshot import load_portfolio_snapshot
    from src.trading_runtime.strategy_one_broker_match_snapshot import (
        load_unattested_broker_match_snapshot, verify_broker_match_snapshot, float64_from_bits,
    )
    from src.trading_runtime.arte_oms_projection import load_recovered_strategy_one_oms_lineage
    from src.trading_runtime.strategy_engine import AssignmentStatus
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.backend.backtest_squeeze_ladder_admission import admit_ladder_proposal
    context = request.market_context
    context.verify_policy(request.policy)
    if client.execute("SELECT getSetting('readonly')").strip() != '1':
        raise ValueError('Ladder native entry authority requires a SELECT-only client')
    if (type(prefix) is not V4CommittedPrefix or prefix.status != 'running'
            or prefix.run_id != context.run_id or prefix.run_id != batch.run_id
            or prefix.last_batch_id != batch.prior_batch_id
            or prefix.last_sequence + 1 != batch.first_sequence
            or not prefix.batch_ids or prefix.batch_ids[-1] != prefix.last_batch_id):
        raise ValueError('Ladder entry lacks its exact verified native predecessor')
    account = batch.intents[0]['account_id']
    decision = request.admission.market_decision
    request.verify(run_id=prefix.run_id, account_id=account, session_date=context.session_date)
    reconstructed = reconstruct_native_ladder_market(context, client=client)
    replace(request, market_context=reconstructed).verify(run_id=prefix.run_id,
        account_id=account, session_date=context.session_date)
    native = load_typed_run_context(client, prefix.run_id)
    config = certify_numbered_configuration(client, context.configuration.strategy_number)
    if (config.payload_hash != context.configuration.payload_hash
            or config.payload != context.configuration.payload
            or native.get('configuration_hash') != config.payload_hash
            or native.get('market_plan_token') != context.market.token
            or native.get('strategy_revision') != config.strategy_number
            or native.get('strategy_id') != config.payload['strategy']['strategy_id']
            or native.get('mode') != 'backtest'
            or native.get('session_date') != context.session_date.isoformat()
            or account not in native.get('account_ids', ())):
        raise ValueError('Ladder native entry differs from the exact sealed run configuration')
    controls = _rows(client,
        'SELECT count() AS count FROM arte.trading_strategy_assignment_command_v1 '
        f'WHERE run_id={_literal(prefix.run_id)} {_committed_batch_filter(prefix)} '
        'FORMAT JSONEachRow')
    if len(controls) != 1 or str(controls[0].get('count')) != '0':
        raise ValueError('Ladder immutable-permission baseline has a control intervention')
    cursor = load_latest_backtest_cursor(client, prefix)
    boundary = decision.boundary_ms
    if (not isinstance(cursor, dict) or cursor.get('event_sequence') != prefix.last_sequence
            or str(cursor.get('batch_id')) != prefix.last_batch_id
            or cursor.get('boundary_ms') != boundary
            or cursor.get('session_date') != context.session_date.isoformat()):
        raise ValueError('Ladder entry lacks the exact completed pre-entry market cursor')
    snapshot = load_portfolio_snapshot(client, run_id=prefix.run_id,
        account_id=account, state_revision=prefix.last_sequence)
    at = market_day_boundary(context.session_date, boundary).astimezone(timezone.utc)
    if (snapshot is None or snapshot.get('state_revision') != prefix.last_sequence
            or datetime.fromisoformat(snapshot.get('snapshot_at', '')) != at
            or snapshot.get('state', {}).get('pending_entry_requests')):
        raise ValueError('Ladder native Portfolio pre-entry image is missing or pending')
    broker = verify_broker_match_snapshot(load_unattested_broker_match_snapshot(
        client, run_id=prefix.run_id, checkpoint_sequence=prefix.last_sequence))
    if (broker.snapshot['run_id'] != prefix.run_id
            or broker.snapshot['checkpoint_sequence'] != prefix.last_sequence
            or broker.snapshot['boundary_ms'] != boundary
            or broker.snapshot['session_date'] != context.session_date.isoformat()
            or {row['account_id'] for row in broker.accounts} != set(native['account_ids'])):
        raise ValueError('Ladder broker image differs from its exact running cursor')
    ticker = request.intent.ticker
    if any(row['account_id'] == account and row['ticker'] == ticker
           and float64_from_bits(row['quantity_f64_bits'], 'quantity') != 0
           for row in broker.positions):
        raise ValueError('Ladder native broker already holds the proposed ticker')
    if any(row['account_id'] == account and row['ticker'] == ticker for row in broker.open_orders):
        raise ValueError('Ladder native broker has unresolved orders for the proposed ticker')
    lineage = load_recovered_strategy_one_oms_lineage(client, prefix,
        allowed_accounts=frozenset(native['account_ids']), strategy_number=config.strategy_number)
    session = 'premarket' if boundary < 19_800_000 else 'afterhours'
    for item in lineage:
        approved = item.approved_intent
        if approved is None or item.admission_reservation is None:
            raise ValueError('Ladder native OMS ownership is incomplete')
        if item.through_sequence != prefix.last_sequence:
            raise ValueError('Ladder native OMS ownership has a foreign cursor')
        if item.state.group['account_id'] != account or approved.ticker != ticker:
            continue
        for name in ('created_at', 'updated_at'):
            clock_value = datetime.fromisoformat(str(item.state.group[name]).replace('Z', '+00:00'))
            clock_value = clock_value.replace(tzinfo=timezone.utc) if clock_value.tzinfo is None else clock_value
            if clock_value > at:
                raise ValueError('Ladder native OMS has a future financial source clock')
        clock = approved.event_time.astimezone(ZoneInfo('America/New_York'))
        minute = clock.hour * 60 + clock.minute
        owned_session = 'premarket' if 240 <= minute < 570 else 'afterhours' if 960 <= minute < 1200 else None
        if clock.date() != context.session_date or owned_session != session:
            continue
        if approved.action in {'enter_long', 'add_long', 'exit', 'exit_long', 'reduce_long'}:
            # An accepted or unresolved acquisition consumes the ticker/session.
            # Known never-ACK rejections/cancellations may be retried. The native
            # lineage reader proves all bindings, reservations and source parents.
            bindings = item.state.broker_bindings
            if bindings or item.state.group['state'] not in {'rejected', 'cancelled'}:
                raise ValueError('Ladder native OMS has a consumed or unresolved ticker session')
    financial = StrategyOneFinancialView(request.assignment_id, account, ticker,
        AssignmentStatus.WATCHING, request.policy.permissions, 0., False, False, False, 0)
    admission = admit_ladder_proposal(decision, financial,
        session_date=context.session_date, groups=())
    if admission.intent != request.intent:
        raise ValueError('Ladder native pre-entry state cannot admit this source parent')
    return financial
