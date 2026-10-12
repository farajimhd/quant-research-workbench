from datetime import date

import pytest

from src.trading_runtime.backtest_journal_price_contract import (
    PRICE_CONTRACT, EXACT_PRICE_CODEC, BacktestJournalPriceContract,
    project_journal_price_contract)


RUN = "00000000-0000-0000-0000-000000000001"
SCOPE = dict(run_id=RUN, run_month=date(2026, 8, 1), configuration_hash="a" * 64)


def test_scalar_run_contract_roundtrip_and_immutable_scope():
    row = project_journal_price_contract(**SCOPE, price_codec=EXACT_PRICE_CODEC)
    recovered = BacktestJournalPriceContract(row)
    assert recovered.require_scope(**SCOPE, content_hash=row["content_hash"])
    with pytest.raises(TypeError):
        recovered.row["price_codec"] = "decimal"
    row["price_codec"] = "decimal"
    assert recovered.row["price_codec"] == EXACT_PRICE_CODEC
    assert "live_market_ssd" in PRICE_CONTRACT.ddl()
    assert not any("JSON" in kind for _, kind in PRICE_CONTRACT.columns)


@pytest.mark.parametrize("field,value", [
    ("run_id", "00000000-0000-0000-0000-000000000002"),
    ("run_month", "2026-09-01"), ("configuration_hash", "b" * 64),
    ("content_hash", "c" * 64), ("price_codec", "decimal"), ("unknown", "value")])
def test_recovery_rejects_tampered_fields(field, value):
    row = project_journal_price_contract(**SCOPE, price_codec=EXACT_PRICE_CODEC)
    row[field] = value
    with pytest.raises(ValueError):
        BacktestJournalPriceContract(row)


@pytest.mark.parametrize("field,value", [
    ("run_id", "RUN"), ("run_month", date(2026, 8, 2)),
    ("configuration_hash", "A" * 64), ("price_codec", True)])
def test_projection_rejects_invalid_selection(field, value):
    with pytest.raises(ValueError):
        project_journal_price_contract(**{**SCOPE, "price_codec": EXACT_PRICE_CODEC, field: value})


def test_valid_rehashed_other_run_cannot_replace_pinned_contract():
    row = project_journal_price_contract(**SCOPE, price_codec=EXACT_PRICE_CODEC)
    other = BacktestJournalPriceContract(project_journal_price_contract(
        **{**SCOPE, "configuration_hash": "b" * 64}, price_codec=EXACT_PRICE_CODEC))
    with pytest.raises(ValueError, match="pinned run"):
        other.require_scope(**SCOPE, content_hash=row["content_hash"])
