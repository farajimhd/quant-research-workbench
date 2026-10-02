"""Strategy 36 native publication and cold recovery without operational DDL."""
from uuid import UUID

import pytest

from test_arte_entry_activity_v4 import plan
from test_strategy_twenty_entry_recovery import prepared_entry, ExactBits
from tests.test_arte_journal_commit_v4 import attached_v4_client
from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
from src.backend.backtest_strategy_entry_activity_source import EntryActivityReadbackAuthority
from src.trading_runtime.arte_entry_activity_v4 import ENTRY_ACTIVITY
from src.trading_runtime.arte_journal_commit_v4 import publish_strategy_one_entry_batch_v4, load_verified_v4_prefix
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
from src.trading_runtime.arte_strategy_one_entry_journal import load_committed_strategy_one_entry_page
from src.trading_runtime.arte_journal_writer import typed_row


@pytest.mark.parametrize('compound', [False, True])
def test_complete_activity_source_survives_native_publish_and_cold_entry_recovery(compound):
    prepared = plan()
    activity = EntryActivityReadbackAuthority('thirty-six-cold', prepared)
    source = CertifiedPriceReadbackAuthority(activity.run_id, prepared.parent, activity)
    first, proposal, intent = prepared_entry(source, 1, 31000, str(UUID(int=0)), strategy_number=36)
    second, later, later_intent = prepared_entry(source, 2, 41000, first.base.batch_id, strategy_number=36)
    client = attached_v4_client(ExactBits())
    if compound:
        publish_compound_v4(client, coalesce_v4_units((first, second)))
    else:
        for unit in (first, second):
            publish_strategy_one_entry_batch_v4(client, unit.base,
                entry_evidence=unit.entry_evidence, momentum_evidence=unit.momentum_evidence,
                initial_momentum_evidence=unit.initial_momentum_evidence,
                first_price_evidence=unit.first_price_evidence,
                first_price_authorities=unit.first_price_authorities,
                entry_activity_evidence=unit.entry_activity_evidence, first_price_source=source)
    assert len(client.tables[ENTRY_ACTIVITY.name]) == 2
    prefix = load_verified_v4_prefix(client, source.run_id, first_price_source=source)
    page = load_committed_strategy_one_entry_page(client, prefix, first_price_source=source)
    assert tuple(row.proposal for row in page.entries) == (proposal, later)
    assert tuple(row.intent for row in page.entries) == (intent, later_intent)
    price_only = CertifiedPriceReadbackAuthority(source.run_id, source.plan)
    with pytest.raises(ValueError, match='independent certified run source'):
        load_verified_v4_prefix(client, source.run_id, first_price_source=price_only)
    with pytest.raises(ValueError, match='independent certified run source'):
        load_committed_strategy_one_entry_page(client, prefix, first_price_source=price_only)
    original = client.tables[ENTRY_ACTIVITY.name][0]
    mutated = {key: value for key, value in original.items() if key != 'content_hash'}
    mutated['candle_0_trade_count'] += 1
    client.tables[ENTRY_ACTIVITY.name][0] = typed_row(ENTRY_ACTIVITY.name, mutated)
    # Even an honestly rehashed stored row cannot replace source trade counts.
    with pytest.raises(ValueError, match='certified source evidence'):
        load_committed_strategy_one_entry_page(client, prefix, first_price_source=source)
