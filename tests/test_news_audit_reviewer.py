from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from tools.news_audit_reviewer import core
from tools.news_audit_reviewer.core import ClickHouseReviewBackend, group_id


class FakeClient:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str) -> str:
        self.executed.append(sql)
        return ""

    def iter_json_each_row(self, sql: str):
        self.executed.append(sql)
        return iter(())


class DecisionBackend(ClickHouseReviewBackend):
    def article_detail(self, source_id: str) -> dict:
        if source_id != "source-1":
            raise KeyError("unknown source_id")
        return {"source_id": source_id, "gold_label": "ineligible"}


class ExistingGroupBackend(ClickHouseReviewBackend):
    def group_state(self, spec: dict) -> dict:
        return {
            "group_id": group_id(spec), "disposition": "mixed", "completed": 0,
            "note": "", "matched_rows": 12, "latest_revision": 10,
        }

    def rows(self, sql: str) -> list[dict]:
        raise AssertionError("completion of a saved group must not re-resolve dynamic membership")

    def summary(self) -> dict:
        return {"articles": 12, "reviewed_articles": 3}


def test_group_id_is_stable_and_sensitive_to_query() -> None:
    first = {"filters": {"q": "guidance"}, "selection": {"ticker": "AAPL"}}
    reordered = {"selection": {"ticker": "AAPL"}, "filters": {"q": "guidance"}}
    changed = {"filters": {"q": "guidance"}, "selection": {"ticker": "MSFT"}}
    assert group_id(first) == group_id(reordered)
    assert group_id(first) != group_id(changed)


def test_filter_builder_supports_search_grouping_and_escapes_input() -> None:
    backend = ClickHouseReviewBackend(FakeClient())
    where = backend._where(
        {
            "q": "CEO's outlook",
            "search_scope": "full_text",
            "ticker": "AAPL",
            "date_from": "2025-01-01",
            "review_status": "changed",
        },
        {"synthesis_path": "single_subject > report > issuer"},
    )
    assert "CEO\\'s outlook" in where
    assert "s.rendered_text" in where
    assert "has(s.tickers,'AAPL')" in where
    assert "l.operator_label!=s.gold_label" in where
    assert "single_subject > report > issuer" in where


def test_grouping_rejects_unsafe_or_cartesian_dimensions() -> None:
    backend = ClickHouseReviewBackend(FakeClient())
    assert backend.normalize_group_by([]) == core.DEFAULT_GROUP_BY
    with pytest.raises(ValueError, match="unsupported"):
        backend.normalize_group_by(["drop table"])
    with pytest.raises(ValueError, match="array-valued"):
        backend.normalize_group_by(["ticker", "channel"])


def test_mismatch_loader_excludes_holdout_and_rejects_non_mismatch() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "mismatches.jsonl"
        rows = [
            {
                "source_id": "training-1", "population_split": "training_development",
                "gold_label": "eligible", "synthesis_label": "ineligible",
            },
            {
                "source_id": "holdout-1", "population_split": "holdout_august_2026",
                "gold_label": "ineligible", "synthesis_label": "eligible",
            },
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        backend = ClickHouseReviewBackend(FakeClient(), evaluation_path=path)
        with patch.multiple(
            core,
            EXPECTED_ALL_MISMATCHES=2,
            EXPECTED_TRAINING_MISMATCHES=1,
            EXPECTED_HOLDOUT_MISMATCHES=1,
        ):
            training, splits = backend._load_mismatches()
        assert set(training) == {"training-1"}
        assert splits == {"training_development": 1, "holdout_august_2026": 1}


def test_assignment_loader_exposes_all_training_rows_but_never_holdout() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "assignments.csv"
        path.write_text(
            "source_id,population_split\ntraining-1,training_development\n"
            "training-2,training_development\nholdout-1,holdout_august_2026\n",
            encoding="utf-8",
        )
        backend = ClickHouseReviewBackend(FakeClient(), assignments_path=path)
        with patch.multiple(core, EXPECTED_ASSIGNMENTS=3, EXPECTED_TRAINING_ARTICLES=2):
            assignments = backend._load_assignments()
        assert set(assignments) == {"training-1", "training-2"}


def test_article_label_is_appended_with_original_gold_provenance() -> None:
    client = FakeClient()
    backend = DecisionBackend(client)
    decision = backend.set_article_label("source-1", "eligible", "direct issuer guidance", "owner")
    assert decision["original_gold_label"] == "ineligible"
    assert decision["operator_label"] == "eligible"
    assert decision["decision_source"] == "article"
    assert decision["reviewer"] == "owner"
    insert = client.executed[-1]
    assert "INSERT INTO `q_live`.`news_synthesis_v61_operator_label_history_v3`" in insert
    assert "direct issuer guidance" in insert


def test_saved_group_completion_uses_frozen_matched_count() -> None:
    client = FakeClient()
    backend = ExistingGroupBackend(client)
    result = backend.save_group(
        filters={"review_status": "unreviewed"},
        selection={"ticker": "AAPL"},
        disposition="mixed",
        completed=True,
        note="finished the original query population",
        reviewer="owner",
    )
    assert result["matched_rows"] == 12
    assert result["completed"] == 1
    assert "news_synthesis_v61_review_group_history_v3" in client.executed[-1]
