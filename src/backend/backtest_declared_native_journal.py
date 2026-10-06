"""Own typed command buffer; no installed V4 durability or cold authority."""
from .backtest_journal_memory import BacktestMemoryJournal
from types import MappingProxyType
from src.trading_runtime.declared_native_submission import DeclaredNativeSubmission


class DeclaredNativeJournal(BacktestMemoryJournal):
    def __init__(self, *, binding=None, bindings=None, **kwargs):
        from src.trading_runtime.declared_native_submission import DeclaredSubmissionBinding
        if (binding is None) == (bindings is None):
            raise ValueError('Declared journal requires one binding or an immutable binding cohort')
        cohort = (binding,) if bindings is None else bindings
        if type(cohort) is not tuple or not cohort:
            raise ValueError('Declared journal binding cohort must be a nonempty tuple')
        if any(type(row) is not DeclaredSubmissionBinding for row in cohort):
            raise ValueError('Declared journal requires exact prepared binding')
        anchor = cohort[0]
        registry = {}
        for row in cohort:
            row.__post_init__()
            if kwargs.get('run_id') != row.preparation.run_id:
                raise ValueError('Declared journal run differs')
            if (row.preparation.source is not anchor.preparation.source
                    or row.session_date != anchor.session_date
                    or row.configuration_hash != anchor.configuration_hash
                    or row.execution_spec_token != anchor.execution_spec_token
                    or row.entry_request != anchor.entry_request):
                raise ValueError('Declared journal cohort source or configuration differs')
            key = (row.preparation.account_id, row.preparation.assignment_id)
            if key in registry:
                raise ValueError('Declared journal cohort has a duplicate assignment')
            registry[key] = row
        super().__init__(**kwargs)
        self._bindings = cohort
        self._binding_registry = MappingProxyType(registry)
        self._declared_records, self._declared_intents = {}, {}
        self._declared_management_records, self._declared_management_intents = {}, {}

    @property
    def binding(self):
        return self._bindings[0]

    @property
    def bindings(self):
        return self._bindings

    def _owns_binding(self, binding):
        from src.trading_runtime.declared_native_submission import DeclaredSubmissionBinding
        return (type(binding) is DeclaredSubmissionBinding
                and self._binding_registry.get((binding.preparation.account_id,
                    binding.preparation.assignment_id)) is binding)

    def append_declared_native_intent(self, *, submission, intent, account_id, strategy_id, strategy_revision):
        if type(submission) is not DeclaredNativeSubmission or not self._owns_binding(submission.binding):
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

    def append_declared_native_management(self, *, submission, account_id, strategy_id, strategy_revision):
        from src.trading_runtime.declared_native_management_submission import DeclaredNativeManagementSubmission
        if type(submission) is not DeclaredNativeManagementSubmission or not self._owns_binding(submission.binding):
            raise ValueError('Declared management journal binding differs')
        submission.verify(run_id=self.run_id, strategy_id=strategy_id, strategy_revision=strategy_revision,
            account_id=account_id, session_date=self.binding.session_date)
        with self._lock:
            self._require_open()
            origin = self._declared_intents.get(submission.command.context.source.intent_id)
            if origin is None or origin[1].proposal != submission.command.context.source:
                raise ValueError('Declared management lacks the exact original own journal entry')
            cached = tuple(self._declared_management_intents.get(intent.intent_id) for intent in submission.intents)
            if any(row is not None for row in cached):
                if any(row is None or row[1] != submission for row in cached):
                    raise ValueError('Declared management retry changed immutable complete command')
                return tuple(row[0] for row in cached)
            records = self.append_many(tuple(dict(run_id=self.run_id, category='strategy',
                entity_type='declared_native_management_intent', entity_id=intent.intent_id,
                account_id=account_id, event_time=intent.event_time,
                payload={**intent.payload(), 'strategy_id':strategy_id, 'strategy_revision':strategy_revision})
                for intent in submission.intents))
            for record, intent in zip(records, submission.intents, strict=True):
                self._declared_management_records[record.record_id] = submission
                self._declared_management_intents[intent.intent_id] = (record, submission)
            return tuple(records)

    def declared_management_for_record(self, record_id):
        with self._lock:
            return self._declared_management_records.get(record_id)

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
                self._declared_management_records.pop(record_id, None)

    def close(self):
        with self._lock:
            super().close()
            self._declared_records.clear()
            self._declared_intents.clear()
            self._declared_management_records.clear()
            self._declared_management_intents.clear()
