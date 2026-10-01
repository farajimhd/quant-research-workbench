"""Transport checks only: Strategy 20 publication remains gated separately."""
from dataclasses import replace
from uuid import UUID

import pytest

from test_arte_first_price_entry_v4 import graph
from test_arte_journal_compound_v4 import _base
from src.trading_runtime.arte_journal_writer import V4StrategyOneEntryBatch
from src.trading_runtime.arte_journal_compound_v4 import (
    coalesce_v4_units, _publication_kwargs,
)
from src.trading_runtime import arte_journal_commit_v4 as commit


def acquisition():
    rows, entries, _, _, authorities = graph()
    base = _base(1, 11, 0, kind=("strategy", "strategy_intent"))
    parent = base.events[0]["record_id"]
    price = dict(rows[0], parent_record_id=parent, run_id=base.run_id,
                 batch_id=base.batch_id)
    entry = dict(entries[0], record_id=str(UUID(int=202)), parent_record_id=parent, run_id=base.run_id,
                 batch_id=base.batch_id)
    authority = replace(authorities[0], parent_record_id=parent)
    return V4StrategyOneEntryBatch(base, (entry,),
        first_price_evidence=(price,), first_price_authorities=(authority,))


def test_compound_rekeys_price_rows_and_preserves_independent_authority():
    unit = acquisition()
    later = _base(2, 12, 11)
    compound = coalesce_v4_units((unit, later))
    row = compound.children["first_price_evidence"][0]
    assert row["batch_id"] == later.batch_id
    assert row["parent_record_id"] == unit.base.events[0]["record_id"]
    assert unit.first_price_evidence[0]["batch_id"] == unit.base.batch_id
    kwargs = _publication_kwargs(unit)
    assert kwargs["first_price_authorities"] == unit.first_price_authorities
    assert kwargs["first_price_rows"] == unit.first_price_evidence
    with pytest.raises(TypeError):
        unit.first_price_evidence[0]["current_close_int"] = 0


def test_direct_publisher_forwards_price_rows_and_source_authority(monkeypatch):
    unit = acquisition()
    captured = {}
    def capture(client, batch, **kwargs):
        captured.update(kwargs)
        return "transport-only"
    monkeypatch.setattr(commit, "_publish_typed_batch_v4", capture)
    assert commit.publish_strategy_one_entry_batch_v4(
        object(), unit.base, entry_evidence=unit.entry_evidence,
        first_price_evidence=unit.first_price_evidence,
        first_price_authorities=unit.first_price_authorities) == "transport-only"
    assert captured["first_price_rows"] == unit.first_price_evidence
    assert captured["first_price_authorities"] == unit.first_price_authorities


@pytest.mark.parametrize("authorities", [[], (object(),), ({},)])
def test_untyped_source_authorities_rejected(authorities):
    with pytest.raises(ValueError, match="exact certified authorities"):
        replace(acquisition(), first_price_authorities=authorities)
