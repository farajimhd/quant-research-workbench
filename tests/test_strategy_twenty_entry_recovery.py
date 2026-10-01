"""Staged20 typed journal round trips; no release or operational DDL is installed."""
from dataclasses import replace
from datetime import date
import json
import re
import struct
from uuid import UUID

import pytest

from test_backtest_strategy_first_price_source import authority, Bars
from test_strategy_one_intent import _proposal
from tests.test_arte_journal_writer import MemoryClient
from tests.test_arte_journal_commit_v4 import attached_v4_client
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import (
    compile_certified_price_break_plan, bind_certified_price_break_proposal,
    certified_price_entry_intent, project_certified_price_entry, CertifiedPriceReadbackAuthority,
)
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_initial_momentum_entry_v4 import INITIAL_MOMENTUM, VALUES, project_initial_momentum_entry
from src.trading_runtime.arte_rising_momentum_entry_v4 import MOMENTUM, project_rising_momentum_entry
from src.trading_runtime.arte_first_price_entry_v4 import FIRST_PRICE
from src.trading_runtime.arte_strategy_one_entry_journal import (
    project_strategy_one_entry_evidence, load_committed_strategy_one_entry_page,
    load_committed_strategy_one_source,
)
from src.trading_runtime.arte_journal_writer import V4StrategyOneEntryBatch, typed_row
from src.trading_runtime.arte_journal_commit_v4 import publish_strategy_one_entry_batch_v4, load_verified_v4_prefix
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent


class ExactBits(MemoryClient):
    def execute(self, sql):
        for contract in (MOMENTUM, INITIAL_MOMENTUM):
            if f'FROM arte.{contract.name} ' in sql and 'reinterpretAsUInt64' in sql:
                raw = super().execute(re.sub(r'SELECT .*? FROM arte\.',
                    'SELECT ' + ','.join(name for name, _ in contract.columns) + ' FROM arte.', sql, count=1))
                rows = [json.loads(line) for line in raw.splitlines() if line]
                for row in rows:
                    for name in VALUES:
                        row[name + '_bits'] = (None if row[name] is None else
                            int.from_bytes(struct.pack('>d', row[name]), 'big'))
                return '\n'.join(json.dumps(row) for row in rows)
        return super().execute(sql)


def prepared_entry(source, sequence, boundary, prior):
    plan = source.plan
    original = replace(_proposal(), strategy_number=19, boundary_ms=boundary,
        momentum=plan.momentum.lookup('AAA', boundary),
        initial_momentum=plan.source.parent.selection_witness('AAA', boundary))
    proposal = bind_certified_price_break_proposal(plan, original)
    intent = certified_price_entry_intent(plan, proposal, session_date=date(2026, 8, 18))
    inherited = strategy_one_entry_intent(original, session_date=date(2026, 8, 18))
    assert replace(intent, intent_id=inherited.intent_id) == inherited
    base = strategy_intent_batch(intent, run_id=source.run_id, run_month=date(2026, 8, 1),
        account_id=proposal.account_id, attempt_id=str(UUID(int=51)), batch_id=str(UUID(int=60 + sequence)),
        prior_batch_id=prior, sequence=sequence, source_cursor=f'2026-08-18:{boundary}',
        run_status='running', recorded_at=intent.event_time)
    scope = dict(run_id=source.run_id, batch_id=base.batch_id, parent_record_id=base.intents[0]['record_id'])
    evidence = project_strategy_one_entry_evidence(proposal, intent, session_date=date(2026, 8, 18),
        first_price_source=source, **scope)
    price = project_certified_price_entry(plan, proposal, event_month='2026-08-01', **scope)
    unit = V4StrategyOneEntryBatch(base, (evidence,),
        momentum_evidence=project_rising_momentum_entry(proposal, event_month='2026-08-01', **scope),
        initial_momentum_evidence=project_initial_momentum_entry(proposal, proposal.initial_momentum,
            event_month='2026-08-01', **scope),
        first_price_evidence=price.rows, first_price_authorities=(price.authority,))
    return unit, proposal, intent


@pytest.mark.parametrize('compound', [False, True])
def test_staged_twenty_typed_publication_and_cold_entry_roundtrip(compound):
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    source = CertifiedPriceReadbackAuthority('twenty-cold', plan)
    first, proposal, intent = prepared_entry(source, 1, 31000, str(UUID(int=0)))
    second, second_proposal, second_intent = prepared_entry(source, 2, 41000, first.base.batch_id)
    client = attached_v4_client(ExactBits())
    if compound:
        publish_compound_v4(client, coalesce_v4_units((first, second)))
    else:
        for unit in (first, second):
            publish_strategy_one_entry_batch_v4(client, unit.base, entry_evidence=unit.entry_evidence,
                momentum_evidence=unit.momentum_evidence, initial_momentum_evidence=unit.initial_momentum_evidence,
                first_price_evidence=unit.first_price_evidence, first_price_authorities=unit.first_price_authorities)
    assert len(client.tables[FIRST_PRICE.name]) == 2
    prefix = load_verified_v4_prefix(client, source.run_id, first_price_source=source)
    page = load_committed_strategy_one_entry_page(client, prefix, first_price_source=source)
    assert tuple(entry.proposal for entry in page.entries) == (proposal, second_proposal)
    assert tuple(entry.intent for entry in page.entries) == (intent, second_intent)
    if not compound:
        rebuilt, recovered = load_committed_strategy_one_source(client, prefix, page.entries[0], first_price_source=source)
        assert rebuilt == first.base and recovered == intent
    with pytest.raises((ValueError, RuntimeError)):
        load_verified_v4_prefix(client, source.run_id)
    with pytest.raises((ValueError, RuntimeError)):
        load_committed_strategy_one_entry_page(client, prefix)
    row = client.tables[FIRST_PRICE.name][0]
    forged = typed_row(FIRST_PRICE.name,
        dict({key: value for key, value in row.items() if key != 'content_hash'}, current_close_int=102))
    client.tables[FIRST_PRICE.name][0] = forged
    with pytest.raises(RuntimeError) as rejected:
        load_verified_v4_prefix(client, source.run_id, first_price_source=source)
    assert 'certified source authority' in str(rejected.value.__cause__)
    with pytest.raises(ValueError, match='certified source authority'):
        load_committed_strategy_one_entry_page(client, prefix, first_price_source=source)
