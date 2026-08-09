from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from .certification import (
    _load_source_articles,
    _prepare_certified_document,
    certify_documents,
    default_certification_config,
    validate_certified_document,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _entity_id_for_ticker(document: Mapping[str, Any], ticker: str) -> str:
    matches = [
        str(entity["entity_id"])
        for entity in document.get("entities", ())
        if str(entity.get("ticker", "")).upper() == ticker.upper()
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one entity for ticker {ticker}, found {matches}")
    return matches[0]


def _set_envelope(document: dict[str, Any], changes: Mapping[str, str]) -> None:
    for field, value in changes.items():
        if field not in document["envelope"]:
            raise RuntimeError(f"Unknown envelope field: {field}")
        document["envelope"][field]["value"] = value


def _set_statement_fields(document: dict[str, Any], changes: list[Mapping[str, Any]]) -> None:
    statements = {row["statement_id"]: row for row in document.get("statements", ())}
    allowed = {"statement_kind", "epistemic_status", "time_relation", "concept_leaf"}
    for change in changes:
        statement_id = str(change["statement_id"])
        if statement_id not in statements:
            raise RuntimeError(f"Missing statement {statement_id}")
        fields = dict(change.get("fields", {}))
        unknown = set(fields) - allowed
        if unknown:
            raise RuntimeError(f"Unsupported statement fields for {statement_id}: {sorted(unknown)}")
        statements[statement_id].update(fields)


def _copy_prediction_evidence(
    document: dict[str, Any],
    prediction: Mapping[str, Any],
    copies: list[Mapping[str, Any]],
) -> None:
    prediction_statements = {row["statement_id"]: row for row in prediction.get("statements", ())}
    prediction_parts = list(prediction.get("participations", ()))
    next_number = max(
        [int(str(row["statement_id"])[1:]) for row in document.get("statements", ()) if str(row["statement_id"]).startswith("S")]
        or [0]
    )
    for spec in copies:
        source_id = str(spec["prediction_statement_id"])
        if source_id not in prediction_statements:
            raise RuntimeError(f"Missing prediction statement {source_id}")
        next_number += 1
        target_id = f"S{next_number:04d}"
        statement = copy.deepcopy(prediction_statements[source_id])
        statement["statement_id"] = target_id
        document["statements"].append(statement)
        document["synthesis"]["document_statement_ids"].append(target_id)
        for ticker in spec["tickers"]:
            target_entity_id = _entity_id_for_ticker(document, str(ticker))
            source_part = next(
                (
                    row
                    for row in prediction_parts
                    if row["statement_id"] == source_id
                    and str(next(
                        (entity.get("ticker") for entity in prediction.get("entities", ()) if entity["entity_id"] == row["entity_id"]),
                        "",
                    )).upper() == str(ticker).upper()
                ),
                None,
            )
            if source_part is None:
                raise RuntimeError(f"Prediction statement {source_id} has no participation for {ticker}")
            participation = copy.deepcopy(source_part)
            participation["statement_id"] = target_id
            participation["entity_id"] = target_entity_id
            document["participations"].append(participation)


def _set_eligibility(document: dict[str, Any], changes: Mapping[str, bool]) -> None:
    rows = {
        row["entity_id"]: row
        for row in document.get("eligibility", ())
        if row.get("product") == "forecast_trigger"
    }
    for ticker, eligible in changes.items():
        entity_id = _entity_id_for_ticker(document, ticker)
        if entity_id not in rows:
            raise RuntimeError(f"Missing forecast-trigger row for {ticker}")
        row = rows[entity_id]
        row["eligible"] = bool(eligible)
        row["reasons"] = (
            ["eligible_under:forecast_trigger"]
            if eligible
            else ["manual_policy_review:not_forecast_trigger_eligible"]
        )
        row["blocking_flags"] = []


def prepare_amendments(
    plan: Mapping[str, Any],
    *,
    certified_root: Path,
    prediction_root: Path,
    sources: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reviews: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for amendment in plan.get("amendments", ()):
        sample_id = str(amendment["sample_id"])
        if sample_id in seen:
            raise RuntimeError(f"Duplicate amendment for {sample_id}")
        seen.add(sample_id)
        path = certified_root / f"{sample_id}.json"
        document = load_json(path)
        before = copy.deepcopy(document)
        _set_envelope(document, amendment.get("envelope", {}))
        _set_statement_fields(document, list(amendment.get("statements", ())))
        copies = list(amendment.get("copy_prediction_evidence", ()))
        if copies:
            prediction = load_json(prediction_root / f"{sample_id}.json")
            _copy_prediction_evidence(document, prediction, copies)
        _set_eligibility(document, amendment.get("eligibility", {}))
        try:
            clean = _prepare_certified_document(
                sample_id,
                document,
                reviewer=str(plan["reviewer"]),
                review_notes=str(amendment["review_notes"]),
                source=sources.get(sample_id),
            )
        except Exception as exc:
            raise RuntimeError(f"Invalid amendment for {sample_id}: {exc}") from exc
        reviews.append({"sample_id": sample_id, "document": document, "review_notes": amendment["review_notes"]})
        changes.append(
            {
                "sample_id": sample_id,
                "before_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
                "prepared_sha256": hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest(),
                "eligibility_changes": dict(amendment.get("eligibility", {})),
                "review_notes": amendment["review_notes"],
            }
        )
    return reviews, changes


def run(plan_path: Path, *, apply: bool) -> dict[str, Any]:
    plan = load_json(plan_path)
    config = default_certification_config()
    artifact_root = Path(plan["artifact_root"])
    prediction_root = Path(plan["prediction_root"])
    sources = _load_source_articles(config)
    reviews, changes = prepare_amendments(
        plan,
        certified_root=config.output_root / "certified_labels",
        prediction_root=prediction_root,
        sources=sources,
    )
    result = {
        "version": "news_synthesis_manual_certification_amendment_v1",
        "mode": "apply" if apply else "dry_run",
        "plan_sha256": sha256_file(plan_path),
        "amended_articles": len(reviews),
        "eligibility_units": sum(len(row["eligibility_changes"]) for row in changes),
        "changes": changes,
    }
    if not apply:
        return result
    if artifact_root.exists():
        raise RuntimeError(f"Refusing to overwrite amendment artifact: {artifact_root}")
    backup_root = artifact_root / "authority_backup"
    backup_root.mkdir(parents=True)
    shutil.copy2(plan_path, artifact_root / "amendment_plan.json")
    for review in reviews:
        source = config.output_root / "certified_labels" / f"{review['sample_id']}.json"
        shutil.copy2(source, backup_root / source.name)
    certify_documents(config, reviews, reviewer=str(plan["reviewer"]))
    for review in reviews:
        stored = load_json(config.output_root / "certified_labels" / f"{review['sample_id']}.json")
        validate_certified_document(stored, sources[review["sample_id"]])
    result["certification_manifest_sha256"] = sha256_file(config.output_root / "manifest.json")
    result["stored_documents"] = [
        {
            "sample_id": review["sample_id"],
            "sha256": sha256_file(config.output_root / "certified_labels" / f"{review['sample_id']}.json"),
        }
        for review in reviews
    ]
    (artifact_root / "amendment_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and apply evidence-bound manual certification amendments.")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.plan, apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
