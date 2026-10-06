"""Own typed command buffer; no installed V4 durability or cold authority."""
from .backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.declared_native_submission import DeclaredNativeSubmission


class DeclaredNativeJournal(BacktestMemoryJournal):
    def __init__(self, *, binding, **kwargs):
        from src.trading_runtime.declared_native_submission import DeclaredSubmissionBinding
        if type(binding) is not DeclaredSubmissionBinding:
            raise ValueError('Declared journal requires exact prepared binding')
        binding.__post_init__()
        if kwargs.get('run_id') != binding.preparation.run_id:
            raise ValueError('Declared journal run differs')
        super().__init__(**kwargs)
        self.binding = binding
        self._declared_records, self._declared_intents = {}, {}

    def append_declared_native_intent(self, *, submission, intent, account_id, strategy_id, strategy_revision):
        if type(submission) is not DeclaredNativeSubmission or submission.binding is not self.binding:
            raise ValueError('Declared journal source binding differs')
        submission.verify(run_id=self.run_id,strategy_id=strategy_id,strategy_revision=strategy_revision,
            account_id=account_id,session_date=self.binding.session_date)
        if intent != submission.intent:
            raise ValueError('Declared journal intent changed source')
        with self._lock:
            self._require_open()
            prior = self._declared_intents.get(intent.intent_id)
            if prior is not None:
                record, source = prior
                if source != submission:
                    raise ValueError('Declared source retry changed immutable companion')
                return record
            record = self.append(run_id=self.run_id,category='strategy',entity_type='declared_native_intent',
                entity_id=intent.intent_id,account_id=account_id,event_time=intent.event_time,
                payload={**intent.payload(),'strategy_id':strategy_id,'strategy_revision':strategy_revision})
            self._declared_records[record.record_id] = submission
            self._declared_intents[intent.intent_id] = (record,submission)
            self._entry_assignments[intent.intent_id] = submission.assignment_id
            return record

    def declared_submission_for_record(self, record_id):
        with self._lock:
            return self._declared_records.get(record_id)

    def mark_fenced(self, sequence):
        """Retire pending companions only after the common fence succeeds.

        Original intent retries keep their immutable command/record cache;
        publishers no longer need a companion for an already fenced record.
        """
        if type(sequence) is not int:
            raise ValueError('Declared journal fence requires an exact integer sequence')
        with self._lock:
            retired = tuple(record.record_id for record in self._records[:sequence - self._base_sequence])
            super().mark_fenced(sequence)
            for record_id in retired:
                self._declared_records.pop(record_id, None)

    def close(self):
        with self._lock:
            super().close()
            self._declared_records.clear()
            self._declared_intents.clear()
