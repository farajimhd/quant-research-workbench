"""Independent Strategy 45 projection with shared native fencing and receipts."""
from .backtest_typed_publisher import BacktestTypedJournalPublisher, _coalesce_v4_units
from .backtest_strategy_forty_five_projection import project_prefix


class StrategyFortyFivePublisher(BacktestTypedJournalPublisher):
    def _prepare_batches(self, through_sequence):
        if (self.writer.journal_profile != "backtest_v4"
                or not self._sequence < through_sequence <= self._sequence + self.batch_size):
            raise ValueError("Strategy 45 publication requires a bounded native V4 prefix")
        return _coalesce_v4_units(project_prefix(self.journal,
            attempt_id=self.attempt_id, run_month=self.run_month,
            prior_sequence=self._sequence, prior_batch_id=self._batch_id,
            source_cursor=self._source_cursor, expected_config=self.expected_config,
            fixed_market_parent_plan=self.fixed_market_parent_plan,
            fixed_market_execution_plan=self.fixed_market_execution_plan,
            expected_market_start=self.expected_market_start,
            published_sources=dict(self._committed_strategy_intents),
            committed_order_lineage=dict(self._committed_order_lineage),
            committed_order_lineage_proofs=dict(self._committed_order_lineage_proofs),
            committed_order_lineage_oms_records=dict(self._committed_order_lineage_oms_records),
            through_sequence=through_sequence), max_events=self.batch_size)
