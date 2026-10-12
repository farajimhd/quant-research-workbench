"""Shared normalized run selection for new exact-price Backtests.

Projection and recovery do not install, publish or admit a run. The ordinary
control plane must publish this fact and verify its pinned hash before launch.
"""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from hmac import compare_digest
from types import MappingProxyType
import re
import json
from collections.abc import Mapping
from uuid import UUID

from .arte_journal_schema import TableContract
from .journal_contract import canonical_json


PRICE_CONTRACT = TableContract(
    "trading_backtest_journal_price_contract_v1",
    (("run_id", "String"), ("run_month", "Date"),
     ("configuration_hash", "FixedString(64)"),
     ("price_codec", "LowCardinality(String)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(run_month)", "run_id")
EXACT_PRICE_CODEC = "binary64-v1"


def _scope(run_id, run_month, configuration_hash):
    if (type(run_id) is not str or str(UUID(run_id)) != run_id
            or type(run_month) is not date or run_month.day != 1
            or type(configuration_hash) is not str
            or re.fullmatch(r"[0-9a-f]{64}", configuration_hash) is None):
        raise ValueError("Journal price contract needs its canonical run scope")


def project_journal_price_contract(*, run_id, run_month, configuration_hash, price_codec):
    _scope(run_id, run_month, configuration_hash)
    if type(price_codec) is not str or price_codec != EXACT_PRICE_CODEC:
        raise ValueError("New programme runs require the exact native price codec")
    row = dict(run_id=run_id, run_month=run_month.isoformat(),
        configuration_hash=configuration_hash, price_codec=price_codec)
    row["content_hash"] = sha256(canonical_json(row).encode("utf-8")).hexdigest()
    return row


@dataclass(frozen=True, slots=True)
class BacktestJournalPriceContract:
    """Validated immutable scalar content; not proof of database publication."""
    row: object

    def __post_init__(self):
        if not isinstance(self.row, Mapping):
            raise ValueError("Journal price contract must be a named scalar row")
        row = dict(self.row)
        if set(row) != {name for name, _ in PRICE_CONTRACT.columns}:
            raise ValueError("Journal price contract columns differ")
        month = row["run_month"]
        if type(month) is str:
            month = date.fromisoformat(month)
        canonical = project_journal_price_contract(run_id=row["run_id"], run_month=month,
            configuration_hash=row["configuration_hash"], price_codec=row["price_codec"])
        if (type(row["content_hash"]) is not str
                or not compare_digest(row["content_hash"], canonical["content_hash"])):
            raise ValueError("Journal price contract content hash differs")
        object.__setattr__(self, "row", MappingProxyType(canonical))

    def require_scope(self, *, run_id, run_month, configuration_hash, content_hash):
        _scope(run_id, run_month, configuration_hash)
        if (self.row["run_id"] != run_id or self.row["run_month"] != run_month.isoformat()
                or self.row["configuration_hash"] != configuration_hash
                or type(content_hash) is not str
                or not compare_digest(self.row["content_hash"], content_hash)):
            raise ValueError("Journal price contract differs from pinned run settings")
        return True


def read_journal_price_contract(client, *, run_id, run_month, configuration_hash, content_hash):
    """Bounded SELECT-only readback; caller owns this synchronous connection."""
    from .arte_journal_schema import storage_preflight
    _scope(run_id, run_month, configuration_hash)
    if type(content_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
        raise ValueError("Journal price contract needs an externally pinned hash")
    if client.execute("SELECT getSetting('readonly')").strip() != "1":
        raise RuntimeError("Journal price contract requires a server-enforced cold reader")
    storage_preflight(client, tables=(PRICE_CONTRACT,))
    columns = ",".join(name for name, _ in PRICE_CONTRACT.columns)
    response = client.execute(f"SELECT {columns} FROM arte.{PRICE_CONTRACT.name} "
        f"WHERE run_id='{run_id}' ORDER BY run_id LIMIT 2 FORMAT JSONEachRow")
    if type(response) is not str or len(response.encode("utf-8")) > 16_384:
        raise ValueError("Journal price contract readback exceeds its bounded response")
    lines = [line for line in response.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("Journal price contract is absent or duplicated")
    def unique_fields(pairs):
        row = {}
        for name, value in pairs:
            if name in row:
                raise ValueError("Journal price contract has duplicate named fields")
            row[name] = value
        return row
    contract = BacktestJournalPriceContract(json.loads(lines[0], object_pairs_hook=unique_fields))
    contract.require_scope(run_id=run_id, run_month=run_month,
        configuration_hash=configuration_hash, content_hash=content_hash)
    return contract
