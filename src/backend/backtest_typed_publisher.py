"""Bounded asynchronous publication of a typed fixed-Backtest journal prefix.

This adapter is intentionally not wired into launch until terminal recovery
and the fixed-market execution path are validated end to end.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import (
    NIL_BATCH_ID, project_pending_backtest_prefix, project_pending_backtest_v3_prefix,
)
from src.trading_runtime.arte_journal_writer import (
    ArteJournalWriter, TypedJournalBatch, V3SqueezeBatch,
    V4StrategyOneEntryBatch, _coalesce_unpublished,
)
from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot


@dataclass(frozen=True, slots=True)
class TypedBacktestReceipt:
    last_sequence: int
    last_batch_id: str
    source_cursor: str


class BacktestTypedJournalPublisher:
    """One exclusive writer lane; engine enqueues, a task finalizes receipts."""

    def __init__(
        self, journal: BacktestMemoryJournal, writer: ArteJournalWriter, *,
        attempt_id: str, run_month: date, batch_size: int = 512,
        initial_sequence: int = 0, prior_batch_id: str = NIL_BATCH_ID,
        source_cursor: str = "start", expected_config: dict[str, Any] | None = None,
        fixed_market_parent_plan: object | None = None,
        fixed_market_execution_plan: object | None = None,
        expected_market_start: datetime | None = None,
        expected_market_plan_token: str | None = None,
        expected_query_sha256: str | None = None,
    ) -> None:
        attempt = str(UUID(attempt_id))
        prior = str(UUID(prior_batch_id))
        if (writer.run_id != journal.run_id or writer.run_mode != "backtest"
                or writer.journal_profile not in {"backtest_v2", "backtest_v3", "backtest_v4"}
                or writer.coalesce_batches
                or type(batch_size) is not int or not 1 <= batch_size <= writer.max_events_per_commit
                or type(initial_sequence) is not int or initial_sequence < 0
                or (initial_sequence == 0 and prior != NIL_BATCH_ID)
                or (initial_sequence > 0 and prior == NIL_BATCH_ID)
                or journal.latest_sequence(journal.run_id) < initial_sequence
                or run_month.day != 1 or not source_cursor):
            raise ValueError("Typed Backtest publisher lacks an exclusive bounded prefix")
        if writer.journal_profile == "backtest_v3":
            import re
            if (re.fullmatch(r"[0-9a-f]{64}", expected_market_plan_token or "") is None
                    or re.fullmatch(r"[0-9a-f]{64}", expected_query_sha256 or "") is None):
                raise ValueError("V3 publisher needs pinned squeeze authority")
        self.journal = journal
        self.writer = writer
        self.attempt_id = attempt
        self.run_month = run_month
        self.batch_size = batch_size
        self.expected_config = expected_config
        self.fixed_market_parent_plan = fixed_market_parent_plan
        self.fixed_market_execution_plan = fixed_market_execution_plan
        self.expected_market_start = expected_market_start
        self.expected_market_plan_token = expected_market_plan_token
        self.expected_query_sha256 = expected_query_sha256
        self._sequence = initial_sequence
        self._batch_id = prior
        self._source_cursor = source_cursor
        self._task: asyncio.Task[TypedBacktestReceipt] | None = None
        self._terminal_task: asyncio.Task[TypedBacktestReceipt] | None = None
        self._error: BaseException | None = None
        self._checkpoint_waiters: list[tuple[int, str, asyncio.Future[TypedBacktestReceipt]]] = []

    @property
    def fenced_sequence(self) -> int:
        return self._sequence

    @property
    def checkpoint_pending(self) -> bool:
        return bool(self._checkpoint_waiters)

    def enqueue_pending(self) -> asyncio.Task[TypedBacktestReceipt]:
        """Schedule projection and persistence without hot-path work or I/O.

        An active submission owns the current prefix. The caller may enqueue
        the next prefix after its checkpoint fence settles. Journal capacity
        bounds new event admission meanwhile; no disk fallback is allowed.
        """
        if self._error is not None:
            raise RuntimeError("Typed Backtest publication failed") from self._error
        if self._terminal_task is not None:
            raise RuntimeError("Typed Backtest terminal publication owns the suffix")
        if self._task is not None and not self._task.done():
            return self._task
        if self._task is not None:
            self._task.result()  # Surface an earlier failed receipt.
        target_sequence = (self._checkpoint_waiters[0][0]
                           if self._checkpoint_waiters else
                           self.journal.latest_sequence(self.journal.run_id))
        self._task = asyncio.create_task(self._drain(target_sequence=target_sequence))
        return self._task

    def _prepare_batches(self, through_sequence: int) -> tuple[
            TypedJournalBatch | V3SqueezeBatch | V4StrategyOneEntryBatch, ...]:
        """Project at most one commit-sized prefix outside the event loop."""
        if not self._sequence < through_sequence <= self._sequence + self.batch_size:
            raise ValueError("Typed Backtest projection exceeds one commit budget")
        if self.writer.journal_profile == "backtest_v3":
            from src.backend.backtest_squeeze_episode_v3 import coalesce_squeeze_units_v3
            units = project_pending_backtest_v3_prefix(
                self.journal, attempt_id=self.attempt_id,
                run_month=self.run_month, prior_sequence=self._sequence,
                prior_batch_id=self._batch_id, source_cursor=self._source_cursor,
                expected_config=self.expected_config,
                fixed_market_parent_plan=self.fixed_market_parent_plan,
                fixed_market_execution_plan=self.fixed_market_execution_plan,
                expected_market_start=self.expected_market_start,
                expected_market_plan_token=self.expected_market_plan_token,
                expected_query_sha256=self.expected_query_sha256,
                through_sequence=through_sequence)
            return tuple(coalesce_squeeze_units_v3(
                units[offset:offset + self.batch_size])
                for offset in range(0, len(units), self.batch_size))
        prefix = project_pending_backtest_prefix(
            self.journal, attempt_id=self.attempt_id,
            run_month=self.run_month, prior_sequence=self._sequence,
            prior_batch_id=self._batch_id, source_cursor=self._source_cursor,
            expected_config=self.expected_config,
            fixed_market_parent_plan=self.fixed_market_parent_plan,
            fixed_market_execution_plan=self.fixed_market_execution_plan,
            expected_market_start=self.expected_market_start,
            through_sequence=through_sequence,
        )
        batches = tuple(_coalesce_unpublished(
            prefix.batches[offset:offset + self.batch_size])
            for offset in range(0, len(prefix.batches), self.batch_size))
        if self.writer.journal_profile != "backtest_v4":
            return batches
        from src.trading_runtime.arte_strategy_one_entry_journal import (
            project_strategy_one_entry_evidence,
        )
        from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent

        units = []
        for batch in batches:
            children = []
            records = self.journal.unfenced_records(
                after_sequence=batch.first_sequence - 1,
                through_sequence=batch.last_sequence)
            for record in records:
                sidecar = self.journal.strategy_one_entry_for_record(record.record_id)
                if sidecar is None:
                    if ((record.category, record.entity_type)
                            == ("strategy", "strategy_intent")
                            and record.payload.get("reason") == "strategy_one_entry"):
                        raise RuntimeError("Strategy 1 journal intent lacks normalized evidence")
                    continue
                proposal, session_date = sidecar
                intent = strategy_one_entry_intent(proposal, session_date=session_date)
                children.append(project_strategy_one_entry_evidence(
                    proposal, intent, session_date=session_date,
                    run_id=batch.run_id, batch_id=batch.batch_id,
                    parent_record_id=record.record_id))
            units.append(V4StrategyOneEntryBatch(batch, tuple(children))
                         if children else batch)
        return tuple(units)

    async def _drain(self, *, target_sequence: int | None = None) -> TypedBacktestReceipt:
        try:
            if target_sequence is None:
                target_sequence = (self._checkpoint_waiters[0][0]
                                   if self._checkpoint_waiters else
                                   self.journal.latest_sequence(self.journal.run_id))
            if target_sequence < self._sequence:
                raise ValueError("Typed Backtest drain target precedes its fence")
            while self._sequence < target_sequence:
                through_sequence = min(target_sequence,
                                       self._sequence + self.batch_size)
                batches = await asyncio.to_thread(self._prepare_batches,
                                                  through_sequence)
                if len(batches) != 1:
                    raise RuntimeError("Typed Backtest projector changed the bounded prefix")
                unit = batches[0]
                batch = unit.base if isinstance(
                    unit, (V3SqueezeBatch, V4StrategyOneEntryBatch)) else unit
                receipt = (self.writer.submit_strategy_one_entry_v4(unit)
                           if isinstance(unit, V4StrategyOneEntryBatch)
                           else self.writer.submit_squeeze_v3(unit)
                           if isinstance(unit, V3SqueezeBatch)
                           else self.writer.submit_base_v4(batch)
                           if self.writer.journal_profile == "backtest_v4"
                           else self.writer.submit(batch))
                committed = await asyncio.wrap_future(receipt)
                if str(UUID(str(committed))) != batch.batch_id:
                    raise RuntimeError("Typed Backtest writer changed an exclusive batch ID")
                self.journal.mark_fenced(batch.last_sequence)
                self._sequence = batch.last_sequence
                self._batch_id = batch.batch_id
                self._source_cursor = batch.source_cursor
                current = TypedBacktestReceipt(self._sequence, self._batch_id,
                                               self._source_cursor)
                remaining = []
                for sequence, cursor, waiter in self._checkpoint_waiters:
                    if sequence <= self._sequence:
                        if not waiter.done():
                            if (sequence != self._sequence or cursor != self._source_cursor):
                                waiter.set_exception(RuntimeError(
                                    "Typed Backtest checkpoint cursor differs from committed prefix"))
                            else:
                                waiter.set_result(current)
                    else:
                        remaining.append((sequence, cursor, waiter))
                self._checkpoint_waiters = remaining
            return TypedBacktestReceipt(self._sequence, self._batch_id,
                                        self._source_cursor)
        except BaseException as exc:
            self._error = exc
            for _, _, waiter in self._checkpoint_waiters:
                if not waiter.done():
                    waiter.set_exception(exc)
            self._checkpoint_waiters.clear()
            raise

    async def _drain_after_active(
        self, active: asyncio.Task[TypedBacktestReceipt],
        waiter: asyncio.Future[TypedBacktestReceipt],
    ) -> TypedBacktestReceipt:
        """Chain a checkpoint after an in-flight prefix without waiting in engine."""
        await asyncio.shield(active)
        if waiter.done():
            return waiter.result()
        return await self._drain()

    def enqueue_checkpoint(self, *, boundary_id: str,
                           status: str = "running") -> asyncio.Future[TypedBacktestReceipt]:
        """Return immediately; resolve only after this exact cursor is durable."""
        if status != "running":
            raise ValueError("Terminal Backtest needs lifecycle-last typed account captures")
        if self._terminal_task is not None:
            raise RuntimeError("Terminal publication owns the Backtest suffix")
        if self._checkpoint_waiters:
            raise RuntimeError("A typed Backtest checkpoint is already pending")
        active = self._task if self._task is not None and not self._task.done() else None
        pending = self.journal.unfenced_records()
        if (not pending or not boundary_id
                or (pending[-1].category, pending[-1].entity_type,
                    pending[-1].entity_id) !=
                   ("checkpoint", "market_boundary", boundary_id)):
            raise ValueError("Typed Backtest checkpoint needs the last normalized cursor")
        waiter: asyncio.Future[TypedBacktestReceipt] = asyncio.get_running_loop().create_future()
        self._checkpoint_waiters.append((pending[-1].sequence, boundary_id, waiter))
        try:
            if active is None:
                self.enqueue_pending()
            else:
                self._task = asyncio.create_task(
                    self._drain_after_active(active, waiter))
        except BaseException:
            self._checkpoint_waiters.pop()
            waiter.cancel()
            raise
        return waiter

    async def await_fence(self) -> TypedBacktestReceipt:
        """Wait once at a checkpoint boundary; never once per journal event."""
        if self._task is None:
            self.enqueue_pending()
        assert self._task is not None
        return await asyncio.shield(self._task)

    async def fence_checkpoint(
        self, *, boundary_id: str, status: str = "running",
    ) -> TypedBacktestReceipt:
        """Fence one normalized completed cursor, never an opaque state map.

        Terminal publication must instead place the lifecycle event last and
        attach every typed account capture to that exact event batch. The
        current controller ordering does not yet meet that contract.
        """
        return await asyncio.shield(self.enqueue_checkpoint(
            boundary_id=boundary_id, status=status))

    def enqueue_terminal(
        self, captures: tuple[CapturedPortfolioSnapshot, ...],
    ) -> asyncio.Task[TypedBacktestReceipt]:
        """Hand a lifecycle-last V4 suffix to the worker without network I/O."""
        if (self.writer.journal_profile != "backtest_v4"
                or self._terminal_task is not None or self._error is not None
                or not isinstance(captures, tuple) or not captures
                or any(type(row) is not CapturedPortfolioSnapshot
                       or row.run_id != self.journal.run_id for row in captures)):
            raise ValueError("V4 terminal needs unique typed account captures")
        records = self.journal.unfenced_records()
        if (not records or (records[-1].category, records[-1].entity_type,
                            records[-1].entity_id) !=
                ("lifecycle", "run", self.journal.run_id)
                or records[-1].payload.get("status") not in
                {"completed", "stopped", "failed"}):
            raise ValueError("V4 terminal needs the last lifecycle record")
        terminal_sequence = records[-1].sequence
        self._terminal_task = asyncio.create_task(
            self._publish_terminal_v4(terminal_sequence, captures))
        return self._terminal_task

    async def _publish_terminal_v4(
        self, sequence: int, captures: tuple[CapturedPortfolioSnapshot, ...],
    ) -> TypedBacktestReceipt:
        try:
            if self._task is not None:
                await asyncio.shield(self._task)
            if self._sequence < sequence - 1:
                await self._drain(target_sequence=sequence - 1)
            if self._sequence != sequence - 1:
                raise RuntimeError("V4 terminal predecessor is not fully fenced")
            prefix = await asyncio.to_thread(
                project_pending_backtest_prefix, self.journal,
                attempt_id=self.attempt_id, run_month=self.run_month,
                prior_sequence=self._sequence, prior_batch_id=self._batch_id,
                source_cursor=self._source_cursor,
                expected_config=self.expected_config,
                fixed_market_parent_plan=self.fixed_market_parent_plan,
                fixed_market_execution_plan=self.fixed_market_execution_plan,
                expected_market_start=self.expected_market_start,
                through_sequence=sequence)
            if (len(prefix.batches) != 1
                    or prefix.batches[0].status not in
                    {"completed", "stopped", "failed"}):
                raise RuntimeError("V4 terminal projection is not one lifecycle batch")
            batch = prefix.batches[0]
            committed = await asyncio.wrap_future(
                self.writer.submit_terminal_backtest(batch, captures))
            if str(UUID(str(committed))) != batch.batch_id:
                raise RuntimeError("V4 terminal writer changed the batch ID")
            self.journal.mark_fenced(sequence)
            self._sequence = sequence
            self._batch_id = batch.batch_id
            self._source_cursor = batch.source_cursor
            return TypedBacktestReceipt(sequence, batch.batch_id,
                                        batch.source_cursor)
        except BaseException as exc:
            self._error = exc
            raise
