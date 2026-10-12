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
