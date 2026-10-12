"""Exact-price typed row projection/readback through the shared journal codec.

No writer or commit admission is conferred by these functions. In particular,
this scalar contract cannot publish an OMS snapshot without its normal seals.
"""
from hashlib import sha256
from hmac import compare_digest

from .arte_journal_writer import _CONTRACTS, _canonical_typed_content, typed_row
from .journal_contract import canonical_json
from .trading_price_contracts import PRICE_FAMILIES, transform_price_fields


def exact_price_row(source, values):
    """Project raw native prices before any legacy decimal conversion."""
    target, _ = PRICE_FAMILIES[source]
    return typed_row(target, transform_price_fields(source, values, _CONTRACTS))


def restore_exact_price_row(source, row, *, stored_utc=False):
    """Verify persisted representation before restoring native price scalars."""
    if type(stored_utc) is not bool:
        raise ValueError("Stored UTC mode must be an explicit boolean")
    target, _ = PRICE_FAMILIES[source]
    content = {key: value for key, value in row.items() if key != "content_hash"}
    canonical = _canonical_typed_content(target, content, stored_utc=stored_utc)
    actual = sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    expected = row.get("content_hash")
    if not isinstance(expected, str) or not compare_digest(actual, expected):
        raise ValueError("Exact-price row content hash mismatch")
    return transform_price_fields(source, canonical, _CONTRACTS, restore=True)
