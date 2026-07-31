from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .schema import ANNOTATION_VERSION
from .storage import annotation_directory, assert_runtime_root, read_json, write_json_atomic


def build_analyst_glossary(root: Path) -> dict[str, Any]:
    analysts: dict[str, dict[str, Any]] = {}
    firms: dict[str, dict[str, Any]] = {}
    relationships: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for path in sorted(annotation_directory(root, ANNOTATION_VERSION).glob("*.json")):
        annotation = read_json(path)
        timestamp = str(annotation.get("source_timestamp") or "")
        sample_id = str(annotation.get("sample_id") or "")
        for unit in annotation.get("issuer_units") or ():
            ticker = str(unit.get("ticker") or "")
            for opinion in unit.get("analyst_opinions") or ():
                analyst_name = str(opinion.get("analyst_name") or "").strip()
                firm_name = str(opinion.get("firm_name") or "").strip()
                if analyst_name:
                    entry = analysts.setdefault(
                        analyst_name,
                        {
                            "canonical_name": analyst_name,
                            "aliases": set(),
                            "first_observed_at": timestamp,
                            "last_observed_at": timestamp,
                            "sample_ids": set(),
                        },
                    )
                    entry["aliases"].update(opinion.get("analyst_aliases") or ())
                    entry["first_observed_at"] = min(entry["first_observed_at"], timestamp)
                    entry["last_observed_at"] = max(entry["last_observed_at"], timestamp)
                    entry["sample_ids"].add(sample_id)
                if firm_name:
                    entry = firms.setdefault(
                        firm_name,
                        {
                            "canonical_name": firm_name,
                            "aliases": set(),
                            "first_observed_at": timestamp,
                            "last_observed_at": timestamp,
                            "sample_ids": set(),
                        },
                    )
                    entry["aliases"].update(opinion.get("firm_aliases") or ())
                    entry["first_observed_at"] = min(entry["first_observed_at"], timestamp)
                    entry["last_observed_at"] = max(entry["last_observed_at"], timestamp)
                    entry["sample_ids"].add(sample_id)
                if analyst_name and firm_name:
                    relationships[(analyst_name, firm_name)].append(
                        {
                            "observed_at": timestamp,
                            "sample_id": sample_id,
                            "ticker": ticker,
                        }
                    )
    return {
        "glossary_version": "news_analyst_entity_glossary_v1",
        "source_annotation_version": ANNOTATION_VERSION,
        "semantics": (
            "Observed article attributions only. First/last observed timestamps do not "
            "assert an employment validity interval. No market reaction is joined."
        ),
        "analysts": [_serializable(entry) for entry in analysts.values()],
        "firms": [_serializable(entry) for entry in firms.values()],
        "observed_affiliations": [
            {
                "analyst_name": analyst_name,
                "firm_name": firm_name,
                "observations": sorted(observations, key=lambda value: value["observed_at"]),
            }
            for (analyst_name, firm_name), observations in sorted(relationships.items())
        ],
    }


def persist_analyst_glossary(root: Path) -> dict[str, Any]:
    assert_runtime_root(root)
    glossary = build_analyst_glossary(root)
    write_json_atomic(root / "analyst_entity_glossary_v1.json", glossary)
    return glossary


def _serializable(value: dict[str, Any]) -> dict[str, Any]:
    return {
        **value,
        "aliases": sorted(value["aliases"]),
        "sample_ids": sorted(value["sample_ids"]),
    }
