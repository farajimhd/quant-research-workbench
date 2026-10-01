"""Native compound entry/profit recovery; checkpoint attestation mocked only."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from src.backend.backtest_typed_publisher import _committed_intent_source
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_commit_v4 import (
    publish_strategy_one_entry_batch_v4, publish_base_typed_batch_v4, load_verified_v4_prefix,
    load_verified_commit_v4, _load_verified_details_v4,
)
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
from src.trading_runtime.arte_journal_writer import typed_row
from src.trading_runtime.arte_profit_giveback_v4 import project_profit_giveback, V4ProfitGivebackBatch
from src.trading_runtime.arte_strategy_one_entry_journal import (
    load_committed_strategy_one_entry_page, load_committed_strategy_one_source,
)
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_profit_giveback import ProfitGivebackInput, profit_giveback
from src.trading_runtime.strategy_profit_giveback_exit import profit_giveback_exit_intent
from test_strategy_profit_giveback_exit import financial
from test_strategy_twenty_entry_recovery import (
    ExactBits, prepared_entry, Bars, load_first_price_source,
    compile_certified_price_break_plan, CertifiedPriceReadbackAuthority,
)
from test_backtest_strategy_ten_percent_price_source import authority
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_arte_journal_writer import batch


@pytest.mark.parametrize('number', [31, 32, 33])
def test_entry_sharing_profit_commit_recovers_exact_source_and_preceding_proof(monkeypatch, number):
    from src.trading_runtime import strategy_profit_giveback_source as checkpoints
    from src.trading_runtime import arte_strategy_one_entry_journal as entries
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    source = CertifiedPriceReadbackAuthority('profit-entry-compound', plan)
    original, proposal, entry_intent = prepared_entry(source, 1, 31000,
        str(UUID(int=0)), strategy_number=number)
    client = attached_v4_client(ExactBits())
    publish_strategy_one_entry_batch_v4(client, original.base,
        entry_evidence=original.entry_evidence, momentum_evidence=original.momentum_evidence,
        initial_momentum_evidence=original.initial_momentum_evidence,
        first_price_evidence=original.first_price_evidence,
        first_price_authorities=original.first_price_authorities)
    seed = batch()
    bridge_id = str(UUID(int=200))
    events = tuple(typed_row('trading_event_v1', {
        **{key: value for key, value in seed.events[0].items() if key != 'content_hash'},
        'run_id': source.run_id, 'attempt_id': original.base.attempt_id,
        'batch_id': bridge_id, 'sequence': sequence, 'record_id': str(UUID(int=200+sequence)),
    }) for sequence in range(2, 10))
    bridge = replace(seed, run_id=source.run_id, attempt_id=original.base.attempt_id,
        batch_id=bridge_id, prior_batch_id=original.base.batch_id,
        first_sequence=2, last_sequence=9, events=events)
    publish_base_typed_batch_v4(client, bridge)
    prefix = load_verified_v4_prefix(client, source.run_id, first_price_source=source)
    held = replace(financial(), account_id=proposal.account_id,
        assignment_id=proposal.assignment_id, ticker=proposal.ticker)
    ask, stop = Decimal(str(proposal.reference_ask)), Decimal(str(proposal.initial_stop))
    floor = ask + (ask-stop)/2
    high = int((2*ask-stop)*10000)
    witness = profit_giveback(ProfitGivebackInput(FollowThroughFailureInput(
        40000, 31200, proposal.reference_ask, proposal.initial_stop,
        40000, int(floor*10000), True, .01, .02, float(floor), float(floor)+.01,
        1000, held.position_quantity, False), high, 39900))
    intent = profit_giveback_exit_intent(witness, held, session_date=date(2026, 8, 18),
        source_entry_intent_id=entry_intent.intent_id, strategy_number=number)
    profit_base = strategy_intent_batch(intent, run_id=source.run_id,
        run_month=original.base.run_month, account_id=held.account_id,
        attempt_id=original.base.attempt_id, batch_id=str(UUID(int=300)),
        prior_batch_id=bridge_id, sequence=10, source_cursor='2026-08-18:40000',
        run_status='running', recorded_at=intent.event_time)
    row = project_profit_giveback(witness, intent, held, session_date=date(2026, 8, 18),
        source_entry_intent_id=entry_intent.intent_id, run_id=source.run_id,
        batch_id=profit_base.batch_id, parent_record_id=profit_base.events[0]['record_id'],
        source_manager_snapshot_id=str(UUID(int=400)), source_manager_checkpoint_sequence=7,
        strategy_number=number)
    subsequent, subsequent_proposal, _ = prepared_entry(source, 11, 41000,
        profit_base.batch_id, strategy_number=number)
    confirmations = []
    def attest(actual_client, proof, actual_row, actual_financial, **kwargs):
        assert actual_client is client and proof == prefix
        assert actual_row['source_entry_intent_id'] == entry_intent.intent_id
        assert actual_financial.ticker == proposal.ticker
        confirmations.append(proof)
    monkeypatch.setattr(checkpoints, 'load_profit_giveback_checkpoint', attest)
    merged = coalesce_v4_units((V4ProfitGivebackBatch(profit_base, row), subsequent))
    publish_compound_v4(client, merged, verified_prior_prefix=prefix, first_price_source=source)
    final = load_verified_v4_prefix(client, source.run_id, first_price_source=source)
    from src.trading_runtime.arte_profit_giveback_reader_v4 import load_committed_profit_giveback
    recovered_profit = load_committed_profit_giveback(client, final,
        profit_base.events[0]['record_id'], first_price_source=source)
    assert recovered_profit['source_entry_intent_id'] == entry_intent.intent_id
    assert recovered_profit['batch_id'] == merged.base.batch_id
    assert recovered_profit['source_manager_checkpoint_sequence'] == 7
    with pytest.raises(ValueError, match='native source authority'):
        load_committed_profit_giveback(client, final,
            profit_base.events[0]['record_id'], first_price_source=object())
    with pytest.raises(RuntimeError, match='unique committed witness'):
        load_committed_profit_giveback(client, final, str(UUID(int=9999)),
            first_price_source=source)
    from src.trading_runtime import arte_profit_giveback_reader_v4 as profit_reader
    with monkeypatch.context() as cold_patch:
        quoted = {**recovered_profit, **{name: str(recovered_profit[name])
            for name in ('boundary_ms', 'first_held_boundary_ms', 'completed_close_int',
                         'quote_age_us', 'prior_high_int', 'prior_high_through_boundary_ms',
                         'source_manager_checkpoint_sequence')}}
        cold_patch.setattr(profit_reader, '_rows', lambda *args: [quoted])
        assert load_committed_profit_giveback(client, final,
            profit_base.events[0]['record_id'], first_price_source=source) == recovered_profit
        for changed, message in (
                ([recovered_profit, recovered_profit], 'unique committed witness'),
                ([{**recovered_profit, 'batch_id': str(UUID(int=9999))}], 'outside'),
                ([{**recovered_profit, 'bid': float(recovered_profit['bid']) + .01}], 'typed hash')):
            cold_patch.setattr(profit_reader, '_rows', lambda *args, result=changed: result)
            with pytest.raises(RuntimeError, match=message):
                load_committed_profit_giveback(client, final,
                    profit_base.events[0]['record_id'], first_price_source=source)
    page = load_committed_strategy_one_entry_page(client, final, first_price_source=source)
    assert [entry.proposal for entry in page.entries] == [proposal, subsequent_proposal]
    observed = []
    real_verify = entries.load_verified_commit_v4
    def verify(*args, **kwargs):
        observed.append(kwargs.get('verified_prior_prefix'))
        return real_verify(*args, **kwargs)
    monkeypatch.setattr(entries, 'load_verified_commit_v4', verify)
    rebuilt, actual_intent = load_committed_strategy_one_source(
        client, final, page.entries[1], first_price_source=source)
    assert observed == [prefix]
    assert rebuilt == _committed_intent_source(merged.base, subsequent.base.events[0]['record_id'])
    assert actual_intent == page.entries[1].intent and confirmations
    with pytest.raises(RuntimeError, match='committed detail'):
        load_committed_strategy_one_source(client, final,
            replace(page.entries[1], intent=replace(actual_intent, profit_target_price=13.)),
            first_price_source=source)
    _, families = load_verified_commit_v4(client, run_id=source.run_id,
        batch_id=merged.base.batch_id, verified_prior_prefix=prefix, first_price_source=source)
    authority_ = subsequent.first_price_authorities[0]
    inserted = tuple(client.inserts)
    for invalid in ((authority_, authority_), (object(),),
                    (replace(authority_, strategy_number=30),),
                    (replace(authority_, price_source_token='f'*64),)):
        with pytest.raises(RuntimeError) as rejected:
            _load_verified_details_v4(client, run_id=source.run_id,
                batch_id=merged.base.batch_id, family_rows=families,
                max_rows_per_family=65536, prior_batch_id=bridge_id,
                first_price_authorities=invalid, first_price_source=source,
                verified_prior_prefix=prefix)
        assert 'differs from certified readback source' in str(rejected.value.__cause__)
        assert tuple(client.inserts) == inserted
