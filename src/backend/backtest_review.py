"""Read-only saved-run views, independent of the execution/restart controller."""
import asyncio
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from src.trading_runtime.journal import TradingJournal


def _json_fields(path, *paths):
    # SQLite projects JSON before Python constructs objects. Legacy manifests
    # contain enormous detector/assignment collections that Review never needs.
    with sqlite3.connect(':memory:') as connection:
        arguments = ','.join('?' for _ in paths)
        value = connection.execute(f'SELECT json_extract(?,{arguments})',
            (path.read_text(encoding='utf-8'), *paths)).fetchone()[0]
    return json.loads(value)


class SavedBacktestReview:
    _monitoring = None
    _task = None

    def __init__(self, run_dir):
        from src.backend.replay_run_service import _durable_run_selection
        self.run_dir, self.run_id = run_dir, run_dir.name
        selection = _durable_run_selection(run_dir)
        if not selection or selection.get('status') not in {'completed', 'stopped', 'failed'}:
            raise ValueError('Only terminal Backtests can be opened for review')
        definition, accounts, sources, status = _json_fields(run_dir / 'manifest.json',
            '$.definition', '$.run.account_ids', '$.run.strategy_debug_sources', '$.run.status')
        if status not in {'completed', 'stopped', 'failed'}:
            raise ValueError('Only terminal Backtests can be opened for review')
        if definition.get('mode') != 'backtest':
            raise ValueError('Saved-run review accepts Backtest runs only')
        self._run = {**definition, **selection, 'account_ids': accounts or [],
            'strategy_debug_sources': sources or {}, 'runtime_ready': True, 'review_only': True}
        self.status = self._run['status']
        self.current_time = datetime.fromisoformat(self._run['current_time'])
        self.created_at = datetime.fromisoformat(self._run['created_at'])
        self.updated_at = datetime.fromisoformat(self._run['updated_at'])
        self.processed_events = self._run['processed_events']
        revision, content_hash = _json_fields(run_dir / 'approved-configuration.json', '$.revision_id', '$.content_hash')
        if revision != self._run['configuration_revision_id'] or content_hash != self._run['configuration_content_hash']:
            raise ValueError('Historical approved configuration identity changed')
        self._journal = TradingJournal(run_dir / 'journal.sqlite3', read_only=True)
        try:
            # Project only identity and financial state, never hydrate detector,
            # assignment, source-reader or strategy-observation restart state.
            row = self._journal._fetchone("""SELECT json_extract(state_json,
                '$.schema_version','$.complete','$.identity','$.broker',
                '$.controller.session_relative_volume_artifacts') AS projection
                FROM checkpoints WHERE run_id = ?""", (self.run_id,))
            version, complete, identity, broker, rvol_artifacts = json.loads(row['projection']) if row else (None, None, None, None, None)
            from src.backend.replay_run_service import RESTART_CHECKPOINT_SCHEMA_VERSION
            if version != RESTART_CHECKPOINT_SCHEMA_VERSION or not complete or not isinstance(broker, dict):
                raise ValueError('Saved Backtest has no complete review checkpoint')
            expected = dict(run_id=self.run_id, mode='backtest',
                configuration_revision_id=self._run['configuration_revision_id'],
                configuration_content_hash=self._run['configuration_content_hash'])
            if any(identity.get(key) != value for key, value in expected.items()):
                raise ValueError('Historical review checkpoint identity changed')
            self._broker_state = self._journal._hydrate(broker)
            self.session_relative_volume_artifacts = dict(rvol_artifacts or {})
            if list(identity.get('account_ids') or []) != self._broker_state['account_ids']:
                raise ValueError('Historical review checkpoint account identity changed')
            self.sequence = self._journal.latest_sequence(self.run_id)
        except BaseException:
            self._journal.close()
            raise
        self._run['presentation_sequence'] = self.sequence
        self._run['presentation_as_of'] = self.current_time.isoformat()
        self.definition = SimpleNamespace(**{key: definition.get(key, '') for key in
            ('session_date', 'experimental_structure_book', 'experimental_structure_fingerprint')},
            configuration_revision={'payload': {'strategy': {'strategy_id': selection.get('strategy_id'),
                'name': selection.get('strategy_name'), 'revision': selection.get('strategy_revision')}}})
        self._canvas_task = None
        self._subscribers = set()

    def snapshot(self, **options):
        return deepcopy(self._run)

    stream_snapshot = snapshot

    def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._subscribers.discard(queue)

    async def command(self, command):
        raise ValueError('Saved review is read-only; use Resume from checkpoint')

    def strategy_activity_snapshot(self, *, as_of=None, record_id='', limit=500, offset=0, through_sequence=None,
                                   include_decision_evidence=True, **filters):
        from src.backend.trading_runtime_service import strategy_activity_payload
        cutoff = min(as_of, self.current_time) if as_of else self.current_time
        limit = max(1, min(int(limit), 2000))
        fence = min(through_sequence, self.sequence) if through_sequence is not None else self.sequence
        records = self._journal.strategy_activity_records(run_id=self.run_id, as_of=cutoff,
            through_sequence=fence, record_id=record_id, limit=limit + 1, offset=offset,
            compact=not include_decision_evidence, **filters)
        result = strategy_activity_payload(as_of=cutoff, run_id=self.run_id, record_id=record_id,
            limit=limit, offset=offset, include_decision_evidence=include_decision_evidence,
            _records=records, **filters)
        return {**result, 'presentation_sequence': fence}

    def signal_stream_snapshot(self, *, as_of=None, **options):
        from src.backend.signal_stream_runtime_service import SIGNAL_STREAM_RUNTIME
        configuration = _json_fields(self.run_dir / 'approved-configuration.json', '$.payload')
        return SIGNAL_STREAM_RUNTIME.snapshot(self._journal, run_id=self.run_id,
            as_of=min(as_of, self.current_time) if as_of else self.current_time,
            configuration=configuration, **options)

    async def canvas_payload(self, symbol='AAPL', *, lazy=False, include_chart=True):
        if self._canvas_task is None:
            self._canvas_task = asyncio.create_task(asyncio.to_thread(self._build_financial_view))
        try:
            trading = await asyncio.shield(self._canvas_task)
        except Exception:
            self._canvas_task = None
            raise
        strategy = self.definition.configuration_revision['payload']['strategy']
        if not lazy:
            page = await asyncio.to_thread(self.strategy_activity_snapshot, limit=2000, include_decision_evidence=False)
            trading = {**trading, 'strategy_activity': page['rows'], 'strategy_activity_deferred': False,
                'strategy_activity_page': {'complete': page['complete'], 'next_offset': page.get('next_offset')}}
        if include_chart:
            from src.backend.replay_run_service import _compact_strategy_chart_activity_rows
            page = await asyncio.to_thread(self.strategy_activity_snapshot, ticker=symbol,
                limit=2000, include_decision_evidence=False, consequential_only=True)
            rows = list(page['rows'])
            while not page['complete']:
                page = await asyncio.to_thread(self.strategy_activity_snapshot, ticker=symbol,
                    limit=2000, offset=page['next_offset'], include_decision_evidence=False, consequential_only=True)
                rows.extend(page['rows'])
            trading = {**trading, 'strategy_chart_activity_symbol': symbol,
                'strategy_chart_activity': _compact_strategy_chart_activity_rows(rows)}
            configuration = await asyncio.to_thread(_json_fields, self.run_dir / 'approved-configuration.json', '$.payload.strategy')
            strategy = {**strategy, 'definition': {**configuration, 'config': {'parameters': configuration.get('parameters', {})}},
                'decisions': [r for r in rows if r.get('event_type') == 'decision'],
                'signals': [r for r in rows if r.get('event_type') == 'signal']}
        return dict(as_of=trading['as_of'], coverage={}, chart=dict(bars=[], indicators=[], symbol=symbol, timeframe='1m'),
            errors={}, fills=[], journal=[], news=[], orders=[], portfolio=trading.get('portfolio', {}),
            preview_kind='backtest_run', scanner=[], scanner_meta={'status': 'run_clock', 'row_count': 0},
            sec=[], strategy=strategy, trading=trading,
            xbrl=[], run=self.snapshot())

    def _build_financial_view(self):
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
        from src.trading_runtime.canonical_session import CanonicalBrokerSession
        from src.trading_runtime.domain import TradingMode, BrokerProvider
        from src.backend.canonical_trading_service import trading_state_payload
        broker = SimulatedBrokerAdapter(self._broker_state['account_ids'], SimulationConfig(initial_cash=float(self._run['initial_cash'])), mode=TradingMode.BACKTEST,
            initial_time=self.current_time)
        broker.restore_checkpoint_state(self._broker_state)
        session = CanonicalBrokerSession(broker, mode=TradingMode.BACKTEST, provider=BrokerProvider.SIMULATED)
        asyncio.run(session.bootstrap())
        snapshot = session.projector.snapshot()
        protections = tuple({**r.payload, 'sequence': r.sequence, 'event_time': r.event_time.isoformat(),
            'account_id': r.account_id} for r in self._journal.protection_records(self.run_id)
            if r.sequence <= self.sequence and r.event_time <= self.current_time)
        snapshot = replace(snapshot, protection_events=protections)
        trading = trading_state_payload(snapshot, include_strategy_activity=False,
            protection_as_of=self.current_time, performance_extrema=broker.performance_extrema())
        return {**trading, 'presentation_as_of': self.current_time.isoformat(),
            'presentation_sequence': self.sequence, 'strategy_activity': [],
            'strategy_activity_page': {'complete': False, 'next_offset': 0},
            'strategy_activity_deferred': True}
