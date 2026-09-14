"""Latest-only backtest monitoring. Readers never execute engine work.

The engine supplies a frozen boundary; a separate process performs journal
reads, hydration and rendering. At most one render and one replacement boundary
are retained. Slow/disconnected readers cannot backpressure the engine.
"""
import asyncio
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context


class BacktestPublication:
    def __init__(self, render=None):
        self.render = render or render_publication
        self.pool = None
        self.boundary = None
        self.pending = None
        self.task = None
        self.symbols = OrderedDict()
        self.results = {}
        self.wire = {}
        self.changed = asyncio.Event()
        self.error = None
        self.closed = False

    def publish(self, boundary):
        if self.closed:
            return
        self.boundary = boundary
        if self.symbols:
            self.pending = boundary
            self._start()

    def _start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())
            self.task.add_done_callback(self._completed)

    def _completed(self, task):
        if self.pending is not None and not self.closed:
            self._start()

    async def _run(self):
        while self.pending is not None and not self.closed:
            boundary, self.pending = self.pending, None
            symbols = tuple(symbol for symbol in self.symbols if boundary.get('assignments_complete',True)
                or symbol in boundary.get('assignment_symbols',()))
            if not symbols:
                continue
            # Assignment dataclasses and broker snapshot rows are immutable
            # engine publications. Only requested tickers cross the process pipe.
            packet = {k: v for k, v in boundary.items() if k != "assignments"}
            packet["assignments"] = tuple(a for a in boundary["assignments"] if a.ticker in symbols)
            packet["symbols"] = symbols
            try:
                if self.pool is None:
                    self.pool = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))
                result = await asyncio.get_running_loop().run_in_executor(self.pool, self.render, packet)
                self.results.update({k: v for k, v in result['payloads'].items() if k in self.symbols})
                self.wire.update({k: v for k, v in result['wire'].items() if k in self.symbols})
                self.error = None
            except Exception as exc:
                self.error = str(exc)
            self.changed.set()
        if self.boundary is not None and self.boundary['run']['status'] in {'completed', 'failed', 'stopped'} and self.pool is not None:
            pool, self.pool = self.pool, None
            await asyncio.to_thread(pool.shutdown, wait=True, cancel_futures=True)

    async def get(self, symbol):
        self.symbols[symbol] = None
        self.symbols.move_to_end(symbol)
        while len(self.symbols) > 8:
            old, _ = self.symbols.popitem(last=False)
            self.results.pop(old, None)
            self.wire.pop(old, None)
        if symbol not in self.results and self.boundary is not None and not self.closed:
            if self.task is None or self.task.done():
                self.pending = self.boundary
                self._start()
            elif symbol not in self.results:
                self.pending = self.boundary
        def pending_terminal():
            return (self.boundary is not None and self.boundary['run']['status'] in {'completed', 'failed', 'stopped'}
                and self.results.get(symbol, {}).get('run', {}).get('updated_at') != self.boundary['run']['updated_at'])
        while symbol not in self.results or pending_terminal():
            if symbol not in self.symbols:
                raise ValueError("Backtest monitoring supports eight concurrent symbols; retry this symbol")
            if self.error:
                raise ValueError(f"Backtest monitoring unavailable: {self.error}")
            if self.closed:
                raise ValueError("Backtest monitoring has closed")
            self.changed.clear()
            await self.changed.wait()
        return self.results[symbol]

    async def encoded(self, symbol):
        await self.get(symbol)
        return self.wire[symbol]

    async def close(self):
        self.closed = True
        self.pending = None
        self.changed.set()
        if self.task is not None:
            await self.task
        if self.pool is not None:
            await asyncio.to_thread(self.pool.shutdown, wait=True, cancel_futures=True)
            self.pool = None


def render_publication(packet):
    """Process worker: only frozen input and sequence-fenced read-only SQL."""
    from pathlib import Path
    import json
    from datetime import datetime
    from src.trading_runtime.journal import TradingJournal
    from src.backend.canonical_trading_service import trading_state_payload
    from src.backend.trading_runtime_service import strategy_activity_payload
    from src.backend.replay_run_service import _compact_strategy_chart_activity_rows, _compact_strategy_chart_activity_row
    run = packet["run"]
    cutoff = datetime.fromisoformat(run["current_time"])
    journal = TradingJournal(Path(packet["journal_path"]), read_only=True)
    try:
        def activity(**options):
            records = journal.strategy_activity_records(run_id=run["run_id"], as_of=cutoff,
                through_sequence=packet["sequence"], compact=True, **options)
            return strategy_activity_payload(as_of=cutoff, run_id=run["run_id"],
                include_decision_evidence=False, _records=records, **options)
        trading = trading_state_payload(packet["snapshot"], include_strategy_activity=False,
            protection_as_of=cutoff, performance_extrema=packet["performance_extrema"])
        # Fetch one extra record for the canonical pagination contract.
        records = journal.strategy_activity_records(run_id=run["run_id"], as_of=cutoff,
            through_sequence=packet["sequence"], compact=True, limit=2001)
        page = strategy_activity_payload(as_of=cutoff, run_id=run["run_id"], limit=2000,
            include_decision_evidence=False, _records=records)
        trading.update(strategy_activity=page["rows"],
            strategy_activity_page={"complete": page["complete"], "next_offset": page.get("next_offset")},
            presentation_as_of=cutoff.isoformat(), presentation_sequence=packet["sequence"])
        configuration = packet["configuration"]
        strategy_configuration = configuration.get("strategy") or {}
        definition = {**strategy_configuration, "config": {"parameters": strategy_configuration.get("parameters") or {}}}
        results = {}
        for ticker in packet["symbols"]:
            assignments = [a.payload() for a in packet["assignments"] if a.ticker == ticker]
            chart = activity(ticker=ticker, limit=50_000, consequential_only=True)["rows"]
            strategy = dict(fixture=False, run_id=run["run_id"], runtime_mode=run["mode"],
                strategy_id=strategy_configuration.get("strategy_id", ""),
                name=strategy_configuration.get("name", "Manual trading"), revision=strategy_configuration.get("revision", 0),
                profile_id=strategy_configuration.get("profile_id"), profile_revision=strategy_configuration.get("profile_revision"),
                deployment=configuration.get("deployment") or {}, action_definitions=strategy_configuration.get("action_definitions") or [],
                action_policies=strategy_configuration.get("action_policies") or [], automatic=packet["automatic"],
                state=assignments[0]["status"] if assignments else "not_assigned", definition=definition,
                assignment=assignments[0] if assignments else None, assignments=assignments,
                taxonomy=definition.get("taxonomy"), historical_source=f'{run["mode"]}_run_journal_only')
            for name, event in (("signals", "signal"), ("decisions", "decision"), ("order_management", "order")):
                strategy[name] = [_compact_strategy_chart_activity_row(r, include_chart_plan=False)
                    for r in page["rows"] if r.get("event_type") == event and (event == "order" or r.get("ticker", "").upper() == ticker)]
            results[ticker] = dict(as_of=trading["as_of"], coverage={},
                chart={"bars": [], "indicators": [], "symbol": ticker, "timeframe": "1m"}, errors={},
                fills=[], journal=[], news=[], orders=[], portfolio=trading.get("portfolio", {}),
                preview_kind=f'{run["mode"]}_run', scanner=[], scanner_meta={"status": "run_clock", "row_count": 0}, sec=[],
                strategy=strategy, trading={**trading, "strategy_chart_activity_symbol": ticker,
                    "strategy_chart_activity": _compact_strategy_chart_activity_rows(chart)}, xbrl=[], run=run)
        return dict(payloads=results, wire={symbol: json.dumps(payload, allow_nan=False,
            separators=(',', ':')).encode() for symbol, payload in results.items()})
    finally:
        journal.close()
