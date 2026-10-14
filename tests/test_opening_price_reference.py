from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from src.market_engine import opening_price_reference as c


def listing():
    return c.OpeningListing("listing", "ABC", "symbol", "security", "interval", "a" * 64,
                            date(2025, 1, 1), None)


def split(**updates):
    item = c.OpeningSplit("symbol", "listing", "security", date(2026, 8, 3), 1., 2.,
                         datetime(2026, 8, 3, 7, tzinfo=timezone.utc), "b" * 64)
    return replace(item, **updates)


def project(**updates):
    arguments = dict(build_id="current", day=date(2026, 8, 3),
        attempt_id="00000000-0000-0000-0000-000000000001", listing=listing(),
        regular=c.previous_regular_session(date(2026, 8, 3)), prior_build_id="prior",
        prior_attempt_id="00000000-0000-0000-0000-000000000002", prior_unit_hash="d" * 64,
        prior_close_int=200_000, splits=(split(),))
    arguments.update(updates)
    return c.project_opening_reference(**arguments)


def test_no_split_float_schema_is_canonical_before_row_hashing():
    row, children = project(splits=())
    assert not children
    assert type(row['split_price_factor']) is float
    restored = c.restore_record(c.REFERENCE, row)
    assert restored['content_hash'] == c.content_hash({k:v for k,v in restored.items() if k!='content_hash'})


def test_opening_known_split_adjusts_prior_regular_close_and_preserves_evidence():
    row, children = project()
    assert row["prior_session_date"] == date(2026, 7, 31)
    assert row["prior_regular_open_at"].hour == 13
    assert row["prior_regular_close_at"].hour == 20
    assert row["available_at"] == datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
    assert row["adjusted_prior_close"] == 10.
    assert row["split_price_factor"] == .5
    assert row["split_count"] == len(children) == 1
    assert row["split_content_hash"] == c.content_hash(children)
    assert row["content_hash"] == c.content_hash({k: v for k, v in row.items() if k != "content_hash"})


def test_certified_empty_close_is_explicit_and_never_becomes_a_price():
    row, _ = project(prior_close_int=None, splits=())
    assert row["status"] == "covered_empty"
    assert row["adjusted_prior_close"] is None
    assert row["prior_close_int"] is None


@pytest.mark.parametrize("bad", [
    split(observed_at=datetime(2026, 8, 3, 8, 0, 0, 1, tzinfo=timezone.utc)),
    split(execution_date=date(2026, 8, 4)), split(execution_date=date(2026, 7, 31)),
    split(listing_id="other"), split(symbol_id="other"), split(security_id="other"),
])
def test_future_or_incompatible_split_evidence_fails_closed(bad):
    with pytest.raises(ValueError, match="Split identity"):
        project(splits=(bad,))


def test_conflicting_and_duplicate_split_evidence_fails_closed():
    with pytest.raises(ValueError, match="Conflicting"):
        project(splits=(split(), split(split_to=3., source_content_hash="c" * 64)))
    with pytest.raises(ValueError, match="Duplicate"):
        project(splits=(split(), split()))


def test_noncontinuous_identity_has_an_explicit_exclusion_not_a_price():
    row, children = project(listing=replace(listing(), valid_from=date(2026, 8, 3)),
        prior_close_int=None, prior_attempt_id=None, prior_unit_hash=None, splits=())
    assert row["status"] == "excluded_identity"
    assert row["adjusted_prior_close"] is None and not children
    assert row["identity_valid_from"] == date(2026, 8, 3)
    with pytest.raises(ValueError, match="Noncontinuous"):
        project(listing=replace(listing(), valid_from=date(2026, 8, 3)))
    with pytest.raises(ValueError, match="current session"):
        project(listing=replace(listing(), valid_to_exclusive=date(2026, 8, 3)))


def test_calendar_handles_half_days_dst_and_closed_days():
    session = c.previous_regular_session(date(2026, 11, 30))
    assert session.day == date(2026, 11, 27)
    assert session.closed_at == datetime(2026, 11, 27, 18, tzinfo=timezone.utc)
    assert session.opened_at == datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="not an exchange session"):
        c.previous_regular_session(date(2026, 11, 29))


def test_schema_has_explicit_ssd_policy_and_no_opaque_fields():
    for table in c.TABLES:
        ddl = table.ddl()
        assert "storage_policy = 'live_market_ssd'" in ddl
        assert not any(token in ddl.lower() for token in ("json", "blob", "payload", "array("))
