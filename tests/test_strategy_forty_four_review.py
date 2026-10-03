from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5, uuid4

import pytest

import src.backend.backtest_strategy_forty_four_review as review
from src.trading_runtime.strategy_forty_four_rules import propose_batch, entry_intents
from tests.test_strategy_forty_four_rules import facts


def test_cold_review_checks_complete_owned_batch_and_actual_causal_geometry(monkeypatch):
    import src.trading_runtime.arte_backtest_definition as definitions
    import src.trading_runtime.arte_intent_projection as intents
    run_id = str(uuid4())
    admission = facts()
    assignment = str(uuid5(NAMESPACE_URL, f"strategy44-assignment:{run_id}:TEST"))
    batch = propose_batch(admission, account_id="A", assignment_id=assignment,
        free_cash_after_reservations=10_000, already_submitted=False)
    rows = [SimpleNamespace(account_id="A", intent=intent, sequence=index+1)
            for index, intent in enumerate(entry_intents(batch, session_date=date(2026,9,3)))]
    class Reader:
        def close(self):
            pass
    monkeypatch.setattr(review, "reader_factory", Reader)
    monkeypatch.setattr(review, "certify_configuration", lambda _: SimpleNamespace(payload_hash="sealed", payload={}))
    plan = SimpleNamespace(market=SimpleNamespace(token="market"), session_end_ms=19_800_000)
    monkeypatch.setattr(review, "certify_inputs", lambda *_: (plan, SimpleNamespace(token="history"), None,
        SimpleNamespace(token="prices", units=(1,))))
    monkeypatch.setattr(review, "load_admission_facts", lambda *_a, **_k: (admission,))
    monkeypatch.setattr(definitions, "load_backtest_definition", lambda *_a, **_k: dict(
        definition=dict(start_local_ms=14_400_000,end_local_ms=34_200_000,initial_cash=10_000),
        tickers=(),assignments=(),price_plan=dict(price_plan_token="prices",unit_count=1)))
    monkeypatch.setattr(intents, "load_committed_strategy_intent_page",
        lambda *_a, **k: tuple(rows) if k["after_sequence"] == 0 else ())
    context = dict(strategy_id="squeeze-grid-strategy",strategy_revision=44,configuration_hash="sealed",
                   session_date="2026-09-03",market_plan_token="market",account_ids=("A",))
    prefix = SimpleNamespace(run_id=run_id)
    result = review.audit_terminal_source(None, prefix, context)
    assert result["entry_intents"] == 15 and result["ticker_batches"] == 1
    rows[0].intent = replace(rows[0].intent, profit_target_price=rows[0].intent.profit_target_price + .01)
    with pytest.raises(ValueError, match="causal target"):
        review.audit_terminal_source(None, prefix, context)
