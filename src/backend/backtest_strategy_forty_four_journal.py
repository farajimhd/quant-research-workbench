"""Strategy 44 source buffer using the shared normalized V4 journal families.

The buffer is not durable. Callers must await the native publisher's fence
before broker effects. Certified producer references use normalized signal
source rows; no entry evidence is disguised as Strategy 1 BOS evidence.
"""
from __future__ import annotations

from uuid import UUID, NAMESPACE_URL, uuid5

from .backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.signals import StrategySignal
from src.trading_runtime.strategy_forty_four_coordinator import BatchState
from src.trading_runtime.strategy_forty_four_rules import (
    STRATEGY_ID, STRATEGY_NUMBER, entry_intents,
)


class StrategyFortyFourJournal(BacktestMemoryJournal):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._forty_four_sources = {}
        self._forty_four_locks = {}

    def append_batch(self, state: BatchState, *, source_fact_id: str):
        if type(state) is not BatchState or str(UUID(source_fact_id)) != source_fact_id:
            raise ValueError("Strategy 44 batch needs its exact certified producer fact")
        batch = state.batch
        key = (state.session_date, batch.account_id, batch.facts.ticker)
        if key in self._forty_four_locks:
            raise ValueError("Strategy 44 batch was already submitted in this session")
        intents = entry_intents(batch, session_date=state.session_date)
        if tuple(leg.intent_id for leg in state.legs) != tuple(row.intent_id for row in intents):
            raise ValueError("Strategy 44 batch state changed its immutable entry intents")
        signal = StrategySignal(
            str(uuid5(NAMESPACE_URL, f"strategy-44-batch:{self.run_id}:{intents[0].intent_id}")),
            "strategy_forty_four_batch_lock", batch.facts.ticker,
            intents[0].event_time, "enter_long", "bullish", 1., 1.,
            "strategy_forty_four_first_signal_batch", (source_fact_id, batch.facts.source_token),
            "1s", None, {})
        with self._lock:
            # One atomic bounded buffer append. An uncertain durable fence
            # poisons the coordinator; the source lock is never rolled back.
            records = self.append_many([dict(run_id=self.run_id, category="strategy_decision",
                entity_type="signal", entity_id=signal.signal_id,
                account_id=batch.account_id, event_time=signal.event_time,
                payload={**signal.payload(), "strategy_id": STRATEGY_ID,
                         "strategy_revision": STRATEGY_NUMBER})] + [dict(
                run_id=self.run_id, category="strategy", entity_type="strategy_intent",
                entity_id=intent.intent_id, account_id=batch.account_id,
                event_time=intent.event_time,
                payload={**intent.payload(), "strategy_id": STRATEGY_ID,
                         "strategy_revision": STRATEGY_NUMBER,
                         "causation_id": signal.signal_id}) for intent in intents])
            self._forty_four_locks[key] = signal.signal_id
            for record, intent in zip(records[1:], intents):
                self._forty_four_sources[record.record_id] = (record.sequence, intent)
                self._entry_assignments[intent.intent_id] = batch.assignment_id
            return tuple(records)

    def append_stop(self, source, *, source_fact_id):
        from src.trading_runtime.strategy_forty_four_oms import LegStopAmendment
        if (type(source) is not LegStopAmendment or source.intent.metadata
                or source.intent.action != "replace_protective_stop"
                or source.intent.reason != "strategy_forty_four_adaptive_stop"
                or str(UUID(source_fact_id)) != source_fact_id
                or self.assignment_for_intent(source.source_entry_intent_id) != source.assignment_id):
            raise ValueError("Strategy 44 stop lacks its typed leg and producer source")
        with self._lock:
            record = self.append(run_id=self.run_id, category="strategy",
                entity_type="strategy_intent", entity_id=source.intent.intent_id,
                account_id=source.account_id, event_time=source.intent.event_time,
                payload={**source.intent.payload(), "strategy_id": STRATEGY_ID,
                         "strategy_revision": STRATEGY_NUMBER,
                         "causation_id": source_fact_id,
                         "correlation_id": source.source_entry_intent_id})
            self._forty_four_sources[record.record_id] = (record.sequence, source.intent)
            return record

    def append_leg_exit(self, *, intent, account_id, assignment_id,
                        source_entry_intent_id, source_fact_id):
        if (intent.metadata or intent.action != "exit"
                or intent.reason != "strategy_forty_four_session_liquidation"
                or self.assignment_for_intent(source_entry_intent_id) != assignment_id
                or str(UUID(source_fact_id)) != source_fact_id):
            raise ValueError("Strategy 44 exit lacks its native leg and producer source")
        with self._lock:
            record = self.append(run_id=self.run_id, category="strategy",
                entity_type="strategy_intent", entity_id=intent.intent_id,
                account_id=account_id, event_time=intent.event_time,
                payload={**intent.payload(), "strategy_id": STRATEGY_ID,
                         "strategy_revision": STRATEGY_NUMBER,
                         "causation_id": source_fact_id,
                         "correlation_id": source_entry_intent_id})
            self._forty_four_sources[record.record_id] = (record.sequence, intent)
            self._entry_assignments[intent.intent_id] = assignment_id
            return record

    def strategy_one_protection_for_record(self, record_id):
        """Native publisher compatibility for flat, metadata-free sources.

        This accessor does not generate any Strategy 1 source/evidence rows;
        the V4 publisher uses it to retain committed generic intent lineage.
        """
        row = self._forty_four_sources.get(record_id)
        return row[1] if row is not None else super().strategy_one_protection_for_record(record_id)

    def mark_fenced(self, sequence):
        super().mark_fenced(sequence)
        with self._lock:
            self._forty_four_sources = {key: row for key, row in self._forty_four_sources.items()
                                         if row[0] > sequence}
