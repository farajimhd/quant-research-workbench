from datetime import date
import json

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


@pytest.mark.parametrize("case", ["valid", "absent", "duplicate", "changed", "oversize", "writable", "storage", "array", "duplicate_field"])
def test_cold_readback_is_bounded_and_fail_closed(monkeypatch, case):
    from src.trading_runtime.backtest_journal_price_contract import read_journal_price_contract
    from src.trading_runtime import arte_journal_schema as schema
    row = project_journal_price_contract(**SCOPE, price_codec=EXACT_PRICE_CODEC)
    response = json.dumps(row)
    if case == "absent":
        response = ""
    elif case == "duplicate":
        response += "\n" + response
    elif case == "changed":
        response = json.dumps({**row, "configuration_hash": "b" * 64})
    elif case == "oversize":
        response = " " * 16_385
    elif case == "array":
        response = json.dumps(list(row.items()))
    elif case == "duplicate_field":
        response = response[:-1] + ',"price_codec":"binary64-v1"}'
    calls = []
    def storage(client, *, tables):
        assert tables == (PRICE_CONTRACT,)
        calls.append("storage")
        if case == "storage":
            raise ValueError("misplaced parts")
    monkeypatch.setattr(schema, "storage_preflight", storage)
    class Client:
        def execute(self, sql):
            calls.append(sql)
            assert sql.startswith("SELECT ")
            if "getSetting" in sql:
                return "0" if case == "writable" else "1"
            assert "LIMIT 2 FORMAT JSONEachRow" in sql
            assert f"WHERE run_id='{RUN}'" in sql
            return response
    if case == "valid":
        actual = read_journal_price_contract(Client(), **SCOPE, content_hash=row["content_hash"])
        assert dict(actual.row) == row and "storage" in calls
    else:
        with pytest.raises((ValueError, RuntimeError)):
            read_journal_price_contract(Client(), **SCOPE, content_hash=row["content_hash"])
    if case in ("storage", "writable"):
        assert not any("FROM arte." in sql for sql in calls)
