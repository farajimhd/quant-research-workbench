"""Typed automatic ladder parent and scalar source companion.

This envelope cannot be submitted as a generic journal batch. Publication must
first bind both independently reconstructed market source and native pre-entry
Portfolio/broker/OMS authority to the exact predecessor prefix.
"""
from dataclasses import dataclass
from types import MappingProxyType
from datetime import date


@dataclass(frozen=True, slots=True)
class V4AutomaticLadderBatch:
    base: object
    request: object
    evidence: object

    def __post_init__(self):
        from src.backend.backtest_squeeze_ladder_evidence import LadderEvidenceRows, project_ladder_evidence
        from src.backend.backtest_squeeze_ladder_journal_admission import verify_ladder_intent_parent
        from .arte_journal_writer import TypedJournalBatch
        from .squeeze_ladder_automatic import AutomaticLadderRequest
        if (type(self.base) is not TypedJournalBatch or type(self.request) is not AutomaticLadderRequest
                or type(self.evidence) is not LadderEvidenceRows
                or len(self.base.events) != 1 or len(self.base.intents) != 1):
            raise ValueError('Automatic ladder requires one exact typed source parent')
        context = self.request.market_context
        account = self.base.intents[0]['account_id']
        self.request.verify(run_id=self.base.run_id, account_id=account,
            session_date=context.session_date)
        verify_ladder_intent_parent(self.base, self.request.intent, account_id=account)
        expected = project_ladder_evidence(self.request.admission, run_id=self.base.run_id,
            batch_id=self.base.batch_id, parent_record_id=self.base.events[0]['record_id'],
            assignment_id=self.request.assignment_id, session_date=context.session_date)
        from src.backend.backtest_squeeze_ladder_evidence import verify_ladder_evidence_projection
        verify_ladder_evidence_projection(self.evidence, expected=expected)
        object.__setattr__(self, 'evidence', LadderEvidenceRows(
            MappingProxyType(dict(self.evidence.setup)),
            tuple(MappingProxyType(dict(row)) for row in self.evidence.targets)))

    @classmethod
    def from_request(cls, base, request):
        from src.backend.backtest_squeeze_ladder_evidence import project_ladder_evidence
        return cls(base, request, project_ladder_evidence(request.admission,
            run_id=base.run_id, batch_id=base.batch_id,
            parent_record_id=base.events[0]['record_id'], assignment_id=request.assignment_id,
            session_date=request.market_context.session_date))

    def prepare_families(self, *, verified_prior_prefix, native_financial):
        """Native financial must come from independent exact-prefix cold checks."""
        from src.backend.backtest_squeeze_ladder_journal_admission import prepare_ladder_journal_families
        # Revalidate shallow mutable input companions at the writer boundary.
        self.__post_init__()
        context = self.request.market_context
        return prepare_ladder_journal_families(self.base, self.evidence,
            verified_prior_prefix=verified_prior_prefix, observations=context.observations,
            market=context.market, v7=context.v7, pivots=context.pivots,
            financial=native_financial, groups=(), tick_int=context.tick_int,
            stop_buffer_ticks=context.stop_buffer_ticks,
            break_buffer_ticks=context.break_buffer_ticks, target_count=3, allocation='equal')


def verify_cold_automatic_ladder_families(client, *, related_rows, run_id, batch_id,
        prior_batch_id, verified_prior_prefix, sources, batch_metadata=None):
    """Reconstruct a saved parent without relying on an in-memory proposal."""
    from src.backend.backtest_squeeze_ladder_evidence import LadderEvidenceRows, SETUP, TARGET
    from src.backend.backtest_squeeze_ladder_readback import reconstruct_ladder_market_decision
    from src.backend.backtest_squeeze_ladder_admission import LadderAdmissionDecision, build_ladder_proposal_intent
    from src.backend.backtest_squeeze_ladder_journal_admission import verify_ladder_intent_parent
    from src.backend.backtest_ladder_entry_authority import NativeLadderMarketContext, verify_native_ladder_entry
    from .squeeze_ladder_automatic import AutomaticLadderPolicy, AutomaticLadderRequest
    from .arte_journal_writer import TypedJournalBatch, _canonical_typed_content
    from .journal_contract import canonical_json
    setups, targets = related_rows.get(SETUP.name, ()), related_rows.get(TARGET.name, ())
    if len(setups) != 1 or len(targets) != 3:
        raise ValueError('Cold automatic ladder requires complete one-parent/three-target evidence')
    matching = [source for source in sources if type(source) is NativeLadderMarketContext
                and source.run_id == run_id and source.observations.ticker == setups[0]['ticker']]
    if len(matching) != 1:
        raise ValueError('Cold automatic ladder lacks one independently bound source scope')
    source, = matching
    events = tuple(related_rows.get('trading_event_v1', ()))
    intents = tuple(related_rows.get('trading_strategy_intent_v1', ()))
    if len(events) != 1 or len(intents) != 1:
        raise ValueError('Cold automatic ladder requires an isolated original parent')
    event = events[0]
    if verified_prior_prefix is None:
        raise ValueError('Cold automatic ladder lacks an exact verified predecessor')
    if (not isinstance(batch_metadata, dict)
            or batch_metadata.get('first_sequence') != event['sequence']
            or batch_metadata.get('last_sequence') != event['sequence']
            or batch_metadata.get('status') != 'running'):
        raise ValueError('Cold automatic ladder lacks its exact original commit coordinates')
    batch = TypedJournalBatch(run_id, date.fromisoformat(batch_metadata['run_month']),
        batch_metadata['attempt_id'], batch_id, prior_batch_id,
        event['sequence'], event['sequence'], batch_metadata['source_cursor'],
        'running', events, intents=intents,
        intent_slices=tuple(related_rows.get('trading_intent_protection_slice_v1', ())))
    rows = LadderEvidenceRows(dict(setups[0]), tuple(dict(row) for row in targets))
    decision = reconstruct_ladder_market_decision(rows, observations=source.observations,
        market=source.market, v7=source.v7, pivots=source.pivots, tick_int=source.tick_int,
        stop_buffer_ticks=source.stop_buffer_ticks, break_buffer_ticks=source.break_buffer_ticks,
        target_count=3, allocation='equal')
    intent = build_ladder_proposal_intent(decision, session_date=source.session_date,
        assignment_id=setups[0]['assignment_id'], account_id=intents[0]['account_id'])
    verify_ladder_intent_parent(batch, intent, account_id=intents[0]['account_id'], stored_utc=True)
    request = AutomaticLadderRequest(LadderAdmissionDecision('capital_request_proposed', decision, intent),
        source, setups[0]['assignment_id'], AutomaticLadderPolicy())
    financial = verify_native_ladder_entry(client, verified_prior_prefix, request, batch)
    # Compare scalar contents using native UTC/date encodings. Generic row
    # hash checks already ran, but a valid stored hash grants no source proof.
    from src.backend.backtest_squeeze_ladder_evidence import project_ladder_evidence
    expected = project_ladder_evidence(request.admission, run_id=run_id, batch_id=batch_id,
        parent_record_id=events[0]['record_id'], assignment_id=request.assignment_id,
        session_date=source.session_date)
    for name, stored, proposed in ((SETUP.name, setups, (expected.setup,)),
                                   (TARGET.name, targets, expected.targets)):
        def content(row, stored_utc):
            return _canonical_typed_content(name, {k:v for k,v in row.items() if k != 'content_hash'},
                stored_utc=stored_utc)
        if (sorted(canonical_json(content(row, True)) for row in stored)
                != sorted(canonical_json(content(row, False)) for row in proposed)):
            raise ValueError('Cold ladder scalar evidence differs from independently reconstructed source')
    return financial


def publish_automatic_ladder_batch(client, unit, *, read_client):
    """Writer-lane publication; all authority checks precede the first INSERT."""
    from src.backend.backtest_ladder_entry_authority import verify_native_ladder_entry
    from .arte_journal_commit_v4 import load_verified_v4_prefix, verified_batch_predecessor, _publish_sealed_batch_v4
    from .arte_journal_writer import _sealed_families, typed_row
    if type(unit) is not V4AutomaticLadderBatch:
        raise ValueError('Automatic ladder writer requires its typed envelope')
    prefix = load_verified_v4_prefix(read_client, unit.base.run_id,
        automatic_ladder_sources=(unit.request.market_context,))
    if prefix is not None and unit.base.batch_id in prefix.batch_ids:
        prefix = verified_batch_predecessor(read_client, prefix, unit.base.batch_id)
    financial = verify_native_ladder_entry(read_client, prefix, unit.request, unit.base)
    companions = unit.prepare_families(verified_prior_prefix=prefix, native_financial=financial)
    base = _sealed_families(unit.base, journal_profile='backtest_v4')
    sealed = tuple((name, tuple(typed_row(name, {k:v for k,v in row.items() if k != 'content_hash'})
        for row in rows)) for name, rows in companions)
    return _publish_sealed_batch_v4(client, unit.base, base, (*base, *sealed),
        verified_prior_prefix=prefix, automatic_ladder_sources=(unit.request.market_context,),
        automatic_ladder_read_client=read_client)
