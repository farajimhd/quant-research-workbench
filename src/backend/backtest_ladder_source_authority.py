"""Bounded cold source authority for an explicitly declared automatic ladder.

Only fenced native definitions and sealed configuration select sources. Context
objects supplied by a proposal never select the saved-run market authority.
Verified parents are retained only while auditing a monotone committed prefix.
"""
from collections import OrderedDict
from dataclasses import fields
from datetime import date
from hashlib import sha256
import re

from src.trading_runtime.journal_contract import canonical_json


EXTENDED_ENDS = {'premarket': 19_800_000, 'afterhours': 57_600_000}


def declared_numbered_session_exit_reasons():
    """Select cold exit guards from installed automatic strategy contracts."""
    from src.trading_runtime.strategy_registry import installed_numbered_fixed_strategy_numbers
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy, numbered_session_exit_reason
    return frozenset(numbered_session_exit_reason(number)
        for number in installed_numbered_fixed_strategy_numbers()
        if getattr(numbered_fixed_strategy(number), 'automatic_entry_policy', None) is not None)


def declared_population_exclusions(market_policy):
    """Validate the sealed optional selector without normalizing its identity."""
    if 'population_exclusions' not in market_policy:
        return ()
    values = market_policy['population_exclusions']
    if (type(values) is not list or len(values) > 100
            or any(type(value) is not str or re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,15}', value) is None
                   for value in values)
            or values != sorted(set(values))):
        raise ValueError('Ladder population exclusions require sorted unique canonical tickers bounded to 100')
    return tuple(values)


def declared_ladder_policy(configuration):
    from src.trading_runtime.squeeze_ladder_automatic import AutomaticLadderPolicy
    release = getattr(configuration, 'payload', {}).get('strategy', {}).get('numbered_release', {})
    declared = release.get('automatic_entry_policy')
    if declared is None:
        return None
    policy = AutomaticLadderPolicy()
    if declared != policy.payload():
        raise ValueError('Unknown declared automatic ladder policy')
    return policy


def declared_source_end(market_policy, definition):
    """Bind the complete declared session to the fenced execution window."""
    from src.trading_runtime.squeeze_ladder_columnar import LadderGatePolicy
    keys = {'gate', 'tick_int', 'stop_buffer_ticks', 'break_buffer_ticks',
            'source_through_boundary_rule', 'source_through_boundary_ms_by_session'}
    if (type(market_policy) is not dict or not keys <= set(market_policy)
            or set(market_policy) - keys - {'population_exclusions', 'geometry_binding_policy'}
            or market_policy['source_through_boundary_rule'] != 'extended_session_end'
            or market_policy['source_through_boundary_ms_by_session'] != EXTENDED_ENDS
            or market_policy['tick_int'] != 100
            or market_policy['stop_buffer_ticks'] != 1
            or market_policy['break_buffer_ticks'] != 1
            or set(market_policy['gate']) != {field.name for field in fields(LadderGatePolicy)}):
        raise ValueError('Ladder executable source needs its exact declared extended-session policy')
    declared_population_exclusions(market_policy)
    from src.trading_runtime.squeeze_ladder_geometry import declared_geometry_binding_policy
    declared_geometry_binding_policy(market_policy)
    window = (definition['start_local_ms'], definition['end_local_ms'])
    ends = {(14_400_000, 34_200_000): EXTENDED_ENDS['premarket'],
            (57_600_000, 72_000_000): EXTENDED_ENDS['afterhours']}
    if window not in ends:
        raise ValueError('Ladder source requires a fenced full PM or AH execution window')
    return ends[window]


class DeclaredLadderSourceAuthority:
    """One SELECT-only audit: lazy bounded market contexts and proved parents."""
    def __init__(self, client, run_id, configuration, context, saved, market,
                 *, max_contexts=8, max_parents=4096, max_context_bytes=512 * 1024 * 1024):
        from src.trading_runtime.squeeze_ladder_columnar import LadderGatePolicy
        self.policy = declared_ladder_policy(configuration)
        if self.policy is None:
            raise ValueError('Run has no declared automatic ladder contract')
        if (client.execute("SELECT getSetting('readonly')").strip() != '1'
                or type(max_contexts) is not int or not 1 <= max_contexts <= 8
                or type(max_parents) is not int or not 1 <= max_parents <= 4096
                or type(max_context_bytes) is not int or not 1 <= max_context_bytes <= 512 * 1024 * 1024
                or context['run_id'] != run_id or context['mode'] != 'backtest'
                or context['configuration_hash'] != configuration.payload_hash
                or context['strategy_revision'] != configuration.strategy_number
                or context['strategy_id'] != configuration.payload['strategy']['strategy_id']
                or context['evaluation_interval_ms'] != 100
                or market.token != context['market_plan_token']
                or market.sessions != (context['session_date'],)
                or saved['definition']['final_session_date'] != context['session_date']):
            raise ValueError('Ladder cold source differs from sealed native run authority')
        saved_symbols = tuple(row['ticker'] for row in saved['tickers'])
        population = saved['definition'].get('ticker_population_mode')
        if population == 'market_plan' and not saved_symbols:
            symbols = tuple(market.tickers)
        elif population == 'explicit' and saved_symbols:
            symbols = saved_symbols
        else:
            raise ValueError('Ladder ticker population differs from its fenced selection mode')
        if not set(symbols) <= set(market.tickers):
            raise ValueError('Ladder fenced ticker membership exceeds the certified market plan')
        if not symbols or len(symbols) > 8192 or len(set(symbols)) != len(symbols):
            raise ValueError('Ladder saved ticker membership exceeds its exact bounded scope')
        self.client, self.run_id, self.configuration = client, run_id, configuration
        self.native, self.market = context, market
        self.session_date = date.fromisoformat(context['session_date'])
        self.tickers = frozenset(symbols)
        self.market_policy = configuration.payload['strategy']['numbered_release']['automatic_market_policy']
        self.source_end = declared_source_end(self.market_policy, saved['definition'])
        if set(declared_population_exclusions(self.market_policy)).intersection(market.tickers):
            raise ValueError('Ladder certified market membership contains a declared population exclusion')
        gate = dict(self.market_policy['gate'])
        gate['acquisition_windows'] = tuple(tuple(window) for window in gate['acquisition_windows'])
        self.gate_policy = LadderGatePolicy(**gate)
        from src.trading_runtime.squeeze_ladder_geometry import declared_geometry_binding_policy
        self.geometry_policy = declared_geometry_binding_policy(self.market_policy)
        self.max_contexts, self.max_parents = max_contexts, max_parents
        self.max_context_bytes, self._context_bytes = max_context_bytes, 0
        self._context_sizes = {}
        self._contexts, self._parents = OrderedDict(), {}
        self._scan = None
        self._last_verified_sequence = 0

    @classmethod
    def from_run(cls, client, run_id):
        return cls.from_native(client, run_id)

    @classmethod
    def from_native(cls, client, run_id, *, configuration=None, market=None):
        """Reuse a certified plan only after independently binding saved authority."""
        from src.trading_runtime.arte_journal_writer import load_typed_run_context
        from src.trading_runtime.arte_backtest_definition import load_backtest_definition
        from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
        from src.backend.backtest_market_data import certified_market_plan_from_arte, configuration_tickers
        context = load_typed_run_context(client, run_id)
        sealed = certify_numbered_configuration(client, int(context['strategy_revision']))
        if configuration is not None and (configuration.payload_hash != sealed.payload_hash
                or configuration.payload != sealed.payload):
            raise ValueError('Prepared ladder configuration differs from sealed native run')
        configuration = sealed
        saved = load_backtest_definition(client, run_id, run_context=context)
        if saved['definition']['configuration_revision_id'] != configuration.revision()['revision_id']:
            raise ValueError('Ladder fenced definition differs from sealed configuration revision')
        if market is None:
            market = certified_market_plan_from_arte(sessions=(context['session_date'],),
                tickers=configuration_tickers(configuration.payload, (r['ticker'] for r in saved['tickers'])),
                configuration=configuration.payload)
        else:
            from src.backend.backtest_market_data import verify_market_day_plan
            verify_market_day_plan(market, client)
        return cls(client, run_id, configuration, context, saved, market)

    @property
    def certified_scan(self):
        if self._scan is None:
            from src.backend.fixed_bar_signal import canonical_stream_activation, load_first_squeeze_occurrences
            stream, activation = canonical_stream_activation()
            self._scan = load_first_squeeze_occurrences(self.market, stream=stream,
                activation=activation, through_boundary_ms=self.source_end, client=self.client)
        return self._scan

    def contexts_for(self, tickers):
        if (type(tickers) is not tuple or not 1 <= len(tickers) <= 8
                or tuple(sorted(set(tickers))) != tickers):
            raise ValueError('Ladder context batch requires one to eight sorted unique tickers')
        return tuple(self.context_for(ticker) for ticker in tickers)

    def context_for(self, ticker):
        from src.backend.backtest_ladder_entry_authority import NativeLadderMarketContext
        from src.backend.structural_v7_seed import certified_seed_plan
        from src.backend.backtest_declared_ladder_seed import verify_declared_ladder_seed_plan
        from src.backend.backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
        from src.backend.backtest_strategy_one_pivot_store import certify_pivot_plan
        from src.backend.fixed_bar_signal import canonical_stream_activation, load_first_squeeze_occurrences
        from src.backend.backtest_squeeze_ladder_loader import load_ladder_observations
        if type(ticker) is not str or ticker not in self.tickers:
            raise ValueError('Ladder ticker is outside fenced saved membership')
        if ticker in self._contexts:
            self._contexts.move_to_end(ticker)
            return self._contexts[ticker]
        scan = self.certified_scan
        day, scope = self.session_date.isoformat(), (ticker,)
        seeds = certified_seed_plan(self.market, self.client)
        verify_declared_ladder_seed_plan(self.market, seeds)
        v7 = certify_v7_interval_plan(self.market, seeds, session_date=day,
            candidate_tickers=scope, client=self.client)
        pivots = certify_pivot_plan(self.market, session_date=day,
            candidate_tickers=scope, client=self.client)
        observations, = load_ladder_observations(self.market, session_date=day,
            tickers=scope, through_boundary_ms=self.source_end, certified_scan=scan,
            policy=self.gate_policy, client=self.client)
        context = NativeLadderMarketContext(self.run_id, self.session_date,
            self.configuration, observations, self.market, v7, pivots,
            self.market_policy['tick_int'], self.market_policy['stop_buffer_ticks'],
            self.market_policy['break_buffer_ticks'], self.gate_policy, self.source_end,
            self.geometry_policy)
        context.verify_policy(self.policy)
        size = observations.completed_source.nbytes
        for product in (observations.gate, v7, pivots):
            size += sum(getattr(getattr(product, field.name), 'nbytes', 0)
                        for field in fields(product))
        if size > self.max_context_bytes:
            raise RuntimeError('Ladder source context exceeds its cold memory budget')
        while self._contexts and (len(self._contexts) >= self.max_contexts
                or self._context_bytes + size > self.max_context_bytes):
            expired, _ = self._contexts.popitem(last=False)
            self._context_bytes -= self._context_sizes.pop(expired)
        self._contexts[ticker] = context
        self._context_sizes[ticker] = size
        self._context_bytes += size
        return context

    def record_verified_parent(self, prefix, batch, intent):
        """Called only after full source/financial/scalar verification succeeds."""
        if (prefix.run_id != self.run_id or prefix.last_sequence + 1 != batch.first_sequence
                or prefix.last_batch_id != batch.prior_batch_id):
            raise ValueError('Ladder parent proof must advance its exact monotone predecessor')
        record = batch.events[0]['record_id']
        value = (batch, intent, len(prefix.batch_ids))
        if record in self._parents and self._parents[record] != value:
            raise ValueError('Ladder parent proof changed within this cold audit')
        if record not in self._parents and batch.first_sequence < self._last_verified_sequence:
            raise ValueError('Ladder parent proof regressed its monotone cold audit')
        if record not in self._parents and len(self._parents) >= self.max_parents:
            raise RuntimeError('Ladder cold parent proof budget exhausted')
        self._parents[record] = value
        self._last_verified_sequence = max(self._last_verified_sequence, batch.last_sequence)

    def verify_session_exit_families(self, related_rows, verified_prior_prefix, batch_metadata, *, stored_utc=True, record=True):
        """Cold certify a generic numbered liquidation at its exact predecessor."""
        from datetime import datetime, timezone
        from dataclasses import replace
        from src.trading_runtime.arte_intent_projection import (
            ProjectedIntent, restore_strategy_intent, _stored_instant,
        )
        from src.trading_runtime.arte_journal_writer import TypedJournalBatch, _canonical_typed_content
        from src.trading_runtime.numbered_session_exit import numbered_session_exit_intent
        from src.backend.backtest_market_data import market_day_boundary
        from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
        from src.trading_runtime.arte_portfolio_snapshot import load_portfolio_snapshot
        from src.trading_runtime.strategy_one_broker_match_snapshot import (
            load_unattested_broker_match_snapshot, verify_broker_match_snapshot, float64_from_bits,
        )
        prefix = verified_prior_prefix
        events, parents = related_rows.get('trading_event_v1', ()), related_rows.get('trading_strategy_intent_v1', ())
        if (prefix is None or prefix.run_id != self.run_id or prefix.status != 'running'
                or len(events) != 1 or len(parents) != 1
                or related_rows.get('trading_intent_protection_slice_v1')
                or type(batch_metadata) is not dict
                or batch_metadata['first_sequence'] != prefix.last_sequence + 1
                or batch_metadata['last_sequence'] != batch_metadata['first_sequence']
                or str(batch_metadata.get('prior_batch_id', prefix.last_batch_id)) != prefix.last_batch_id
                or batch_metadata['status'] != 'running'):
            raise ValueError('Ladder session exit lacks its exact historical predecessor')
        event, parent = events[0], parents[0]
        core = _canonical_typed_content('trading_strategy_intent_v1',
            {k:v for k,v in parent.items() if k != 'content_hash'}, stored_utc=stored_utc)
        core = {k:v for k,v in core.items() if k not in
                {'record_id','run_id','event_month','batch_id','account_id'}}
        core['event_time'] = (_stored_instant(event['event_time']) if stored_utc
                              else event['event_time'])
        intent = restore_strategy_intent(ProjectedIntent(core, ()))
        context = self.context_for(intent.ticker)
        origin = market_day_boundary(self.session_date, 0)
        at = intent.event_time.astimezone(timezone.utc)
        boundary = int((at - origin).total_seconds() * 1000)
        if at != market_day_boundary(self.session_date, boundary).astimezone(timezone.utc):
            raise ValueError('Ladder session exit clock is not an exact completed bucket')
        account = parent['account_id']
        assignment = f'strategy-{self.configuration.strategy_number}:{account}:{intent.ticker}'
        cursor = load_latest_backtest_cursor(self.client, prefix)
        if (account not in self.native['account_ids'] or not isinstance(cursor, dict)
                or cursor.get('event_sequence') != prefix.last_sequence
                or str(cursor.get('batch_id')) != prefix.last_batch_id
                or cursor.get('session_date') != self.session_date.isoformat()
                or cursor.get('boundary_ms') != boundary):
            raise ValueError('Ladder session exit lacks exact native pre-exit cursor')
        snapshot = load_portfolio_snapshot(self.client, run_id=self.run_id,
            account_id=account, state_revision=prefix.last_sequence)
        if (snapshot is None or snapshot.get('state_revision') != prefix.last_sequence
                or datetime.fromisoformat(snapshot.get('snapshot_at', '')) != at
                or snapshot.get('state', {}).get('pending_entry_requests')):
            raise ValueError('Ladder session exit lacks actual native Portfolio image')
        broker = verify_broker_match_snapshot(load_unattested_broker_match_snapshot(
            self.client, run_id=self.run_id, checkpoint_sequence=prefix.last_sequence))
        if (broker.snapshot['run_id'] != self.run_id
                or broker.snapshot['checkpoint_sequence'] != prefix.last_sequence
                or broker.snapshot['boundary_ms'] != boundary
                or broker.snapshot['session_date'] != self.session_date.isoformat()
                or {r['account_id'] for r in broker.accounts} != set(self.native['account_ids'])):
            raise ValueError('Ladder session exit lacks actual exact broker image')
        held = [r for r in broker.positions if r['account_id'] == account and r['ticker'] == intent.ticker]
        if len(held) != 1:
            raise ValueError('Ladder session exit lacks one exact held ticker position')
        quantity = float64_from_bits(held[0]['quantity_f64_bits'], 'held quantity')
        from src.trading_runtime.arte_oms_projection import load_recovered_strategy_one_oms_lineage
        lineage = load_recovered_strategy_one_oms_lineage(self.client, prefix,
            allowed_accounts=frozenset(self.native['account_ids']),
            strategy_number=self.configuration.strategy_number, automatic_ladder_sources=self)
        terminal = {'filled', 'cancelled', 'rejected', 'policy_blocked'}
        owners = []
        for item in lineage:
            if item.state.group['account_id'] != account or item.approved_intent.ticker != intent.ticker:
                continue
            action, state = item.approved_intent.action, item.state.group['state']
            if action == 'enter_long' and float(item.state.group['filled_quantity']) > 0:
                owners.append(item)
            if ((action == 'enter_long' and state not in terminal
                    and float(item.state.group['remaining_quantity']) != 0)
                    or (action == 'exit' and state not in terminal)):
                raise ValueError('Ladder session exit has unresolved native acquisition/exit state')
        if (len(owners) != 1 or quantity <= 0
                or quantity > float(owners[0].state.group['filled_quantity'])
                or any(order.conid != held[0]['conid'] for order in owners[0].orders)):
            raise ValueError('Ladder held quantity lacks one exact filled entry owner')
        import pyarrow.compute as pc
        rows = context.observations.completed_source.filter(
            pc.equal(context.observations.completed_source['boundary_ms'], boundary)).to_pylist()
        if (len(rows) != 1 or rows[0]['ticker'] != intent.ticker
                or rows[0].get('quote_valid') != 1
                or type(rows[0].get('quote_timestamp_us')) is not int
                or not 0 <= int(at.timestamp() * 1_000_000) - rows[0]['quote_timestamp_us'] <= 1_000_000
                or type(rows[0].get('bid_int')) is not int or type(rows[0].get('ask_int')) is not int
                or not 0 < rows[0]['bid_int'] <= rows[0]['ask_int']):
            raise ValueError('Ladder session exit lacks independently loaded fresh canonical bid')
        expected = numbered_session_exit_intent(session_date=self.session_date,
            account_id=account, assignment_id=assignment, ticker=intent.ticker,
            boundary_ms=boundary, quantity=quantity, bid=rows[0]['bid_int'] / 10_000,
            strategy_number=self.configuration.strategy_number)
        if intent != expected:
            raise ValueError('Ladder session exit differs from exact native quantity/quote/policy')
        batch = TypedJournalBatch(self.run_id, date.fromisoformat(batch_metadata['run_month']),
            batch_metadata['attempt_id'], str(batch_metadata.get('batch_id', event['batch_id'])), prefix.last_batch_id,
            batch_metadata['first_sequence'], batch_metadata['last_sequence'],
            batch_metadata['source_cursor'], 'running', tuple(events), intents=tuple(parents))
        self.verify_immutable_prefix(prefix)
        if record:
            self.record_verified_parent(prefix, batch, intent)
        return intent

    def verify_immutable_prefix(self, prefix):
        from src.trading_runtime.arte_journal_writer import _rows, _literal, _committed_batch_filter
        if prefix.run_id != self.run_id:
            raise ValueError('Ladder immutable source has a foreign committed run')
        rows = _rows(self.client, 'SELECT count() AS count FROM arte.trading_strategy_assignment_command_v1 '
            f'WHERE run_id={_literal(self.run_id)} {_committed_batch_filter(prefix)} FORMAT JSONEachRow')
        if len(rows) != 1 or str(rows[0].get('count')) != '0':
            raise ValueError('Ladder immutable-permission baseline has a control intervention')

    def source_parent(self, source, prefix):
        value = self._parents.get(source.record_id)
        if value is None:
            raise ValueError('Ladder OMS source parent has not been cold certified in this audit')
        batch, intent, parent_index = value
        if (prefix.run_id != self.run_id or source.intent != intent
                or source.batch_id != batch.batch_id or source.sequence != batch.first_sequence
                or batch.batch_id not in prefix.batch_ids
                or parent_index >= len(prefix.batch_ids)
                or prefix.batch_ids[parent_index] != batch.batch_id
                or parent_index < 1 or prefix.batch_ids[parent_index - 1] != batch.prior_batch_id
                or source.account_id != batch.intents[0]['account_id']):
            raise ValueError('Ladder OMS source parent differs from its proved committed prefix')
        from dataclasses import replace
        return replace(source, source_batch=batch)


def verify_fixed_ladder_order_history(state, intent, history):
    """Allow fixed-lot lifecycle facts, reject changed prices or amendments."""
    profile = intent.protection_profile
    if (profile is None or len(profile.slices) != 3
            or len(state.order_slice_ids) != len(state.orders)):
        raise ValueError('Ladder OMS lacks exact independently protected lot ownership')
    legs = {leg.slice_id: leg for leg in profile.slices}
    if len(legs) != 3 or any(identity not in legs for identity in state.order_slice_ids):
        raise ValueError('Ladder OMS order has foreign fixed-lot ownership')
    order_by_id = {}
    for order, identity in zip(state.orders, state.order_slice_ids, strict=True):
        leg = legs[identity]
        kind = order.orderType.upper()
        if order.side == 'BUY' and not order.parentId and kind == 'LMT':
            envelope = intent.execution_policy.envelope
            if order.price is None or not 0 < order.price <= envelope.maximum_buy_price:
                raise ValueError('Ladder entry order exceeds its declared executable limit')
        elif order.side == 'SELL' and kind == 'LMT':
            if order.price != leg.profit_target_price:
                raise ValueError('Ladder target differs from its frozen structural lot')
        elif order.side == 'SELL' and kind in {'STP', 'STOP_LIMIT'}:
            if order.auxPrice != leg.stop.price:
                raise ValueError('Ladder stop differs from its frozen confirmed swing low')
        else:
            raise ValueError('Ladder OMS order has an undeclared fixed-lot role')
        order_by_id[order.cOID] = (order, leg)
    for row in history.records:
        if (row.account_id != state.group['account_id']
                or row.payload.get('order_group_id') != state.group['group_id']):
            continue
        payload = row.payload
        if (payload.get('action') not in {None, 'enter_long'}
                or payload.get('intent_id') not in {None, intent.intent_id}):
            raise ValueError('Fixed ladder recovery rejects protection interventions')
        pair = order_by_id.get(payload.get('client_order_id'))
        if (pair is None or payload.get('source_intent_id') != intent.intent_id
                or payload.get('kind') not in {'stop', 'target'}
                or payload.get('price') != (pair[1].stop.price if payload['kind'] == 'stop'
                                            else pair[1].profit_target_price)):
            raise ValueError('Ladder protection lifecycle differs from fixed-lot source')
