from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.prepare_news_v59_calibrated_reaudit import policy_contract


SOURCE_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\news_v59_training_mismatch_blind_audit_v1\consolidated"
)
DEFAULT_OUTPUT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\news_v59_training_mismatch_calibrated_file_reaudit_v3"
)
AUDIT_VERSION = "news_v59_training_mismatch_calibrated_file_reaudit_v3"
WORKERS = ("worker_1", "worker_2", "worker_3")
EXPECTED_ROWS = 43_369
EXPECTED_FILES = 1_328
FILE_ROW_LIMIT = 100
PACKET_FILE_LIMIT = 12
PACKET_ROW_LIMIT = 400
ADJUDICATION_PACKET_LIMIT = 100
BODY_PACKET_ROW_LIMIT = 40
BODY_PACKET_CHARACTER_LIMIT = 80_000
PARENT_TRAINING_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_pattern_policy_final_v2"
)
FINAL_TRAINING_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_v59_calibrated_reaudit_v1"
)
POLICY_ASSIGNMENTS = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\news_title_pattern_policy_disagreement_audit_2025_2026_v1"
    r"\ARTICLE_POLICY_ASSIGNMENTS.csv"
)
ADJUDICATION_REVIEW_FIELDS = {
    "review_id",
    "label",
    "confidence",
    "policy_id",
    "decisive_evidence",
    "needs_article_body",
    "discovered_pattern",
    "justification",
}
BODY_REVIEW_FIELDS = {
    "review_id",
    "label",
    "confidence",
    "policy_id",
    "decisive_evidence",
    "source_sufficient",
    "discovered_pattern",
    "justification",
}
REVIEW_FIELDS = {
    "file_id",
    "label",
    "confidence",
    "policy_id",
    "pattern_verdict",
    "decisive_evidence",
    "exceptions",
    "needs_article_body_review_ids",
    "discovered_subpatterns",
    "justification",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_path(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{line_number}: {exc}") from exc


def write_jsonl_new(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")


def write_json_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def markdown_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def metadata_cell(row: Mapping[str, Any]) -> str:
    parts = [
        f"review_id={row['review_id']}",
        f"published={row['published_at_utc']}",
        f"author={row['author'] or '-'}",
        f"channels={','.join(row['channels']) or '-'}",
        f"tags={','.join(row['provider_tags']) or '-'}",
        f"tickers={','.join(row['tickers']) or '-'}",
        f"ticker_count={row['ticker_count']}",
        f"path={row['synthesis_path'] or '-'}",
    ]
    return " | ".join(parts)


def packetize_groups(groups: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    packets: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_rows = 0
    for group in groups:
        rows = int(group["rows"])
        if current and (
            len(current) >= PACKET_FILE_LIMIT or current_rows + rows > PACKET_ROW_LIMIT
        ):
            packets.append(current)
            current = []
            current_rows = 0
        current.append(group)
        current_rows += rows
    if current:
        packets.append(current)
    return packets


def prepare(source_root: Path, output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite {output_root}")
    source_path = source_root / "CONSOLIDATED_REVIEWS.jsonl"
    rows = list(iter_jsonl(source_path))
    if len(rows) != EXPECTED_ROWS:
        raise ValueError(f"source population changed: {len(rows):,}")
    if len({str(row["review_id"]) for row in rows}) != EXPECTED_ROWS:
        raise ValueError("source review IDs are not unique")
    if any(str(row["population_split"]) != "training_development" for row in rows):
        raise ValueError("holdout row leaked into training re-audit")

    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row["confusion_cell"]),
            str(row["gold_label"]),
            str(row["consensus_label"]),
            str(row["audit_policy"]),
            str(row["audit_title_pattern"]),
        )
        grouped.setdefault(key, []).append(row)

    output_root.mkdir(parents=True)
    group_controller = []
    blind_groups = []
    for key, group_rows in sorted(grouped.items()):
        group_rows.sort(key=lambda row: (str(row["published_at_utc"]), str(row["source_id"])))
        for part, offset in enumerate(range(0, len(group_rows), FILE_ROW_LIMIT), start=1):
            chunk = group_rows[offset:offset + FILE_ROW_LIMIT]
            file_id = "F59" + digest(f"{AUDIT_VERSION}|{'|'.join(key)}|{part}")[:22]
            file_path = output_root / "blind" / "files" / f"{file_id}.md"
            lines = [
                f"# Blind title-pattern audit {file_id}",
                "",
                f"- Pattern hint: `{markdown_escape(key[4])}`",
                f"- Rows: `{len(chunk)}`",
                "- Instruction: assign one dominant file label, then list every row-level exception or title-insufficient row explicitly.",
                "",
                "| Metadata | Title |",
                "|---|---|",
            ]
            lines.extend(
                f"| {markdown_escape(metadata_cell(row))} | {markdown_escape(row['title'])} |"
                for row in chunk
            )
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            review_ids = [str(row["review_id"]) for row in chunk]
            group_controller.append({
                "file_id": file_id,
                "part": part,
                "rows": len(chunk),
                "review_ids": review_ids,
                "confusion_cell": key[0],
                "gold_label": key[1],
                "prior_consensus_label": key[2],
                "prior_policy": key[3],
                "title_pattern": key[4],
                "blind_file_path": str(file_path),
                "blind_file_sha256": sha256_path(file_path),
            })
            blind_groups.append({
                "file_id": file_id,
                "rows": len(chunk),
                "pattern_hint": key[4],
                "file_path": str(file_path),
                "file_sha256": sha256_path(file_path),
            })
    if len(group_controller) != EXPECTED_FILES:
        raise ValueError(f"file population changed: {len(group_controller):,}")
    if sum(int(group["rows"]) for group in group_controller) != EXPECTED_ROWS:
        raise ValueError("file rows do not reconcile")
    write_jsonl_new(output_root / "controller" / "GROUP_CONTROLLER.jsonl", group_controller)

    policy = policy_contract()
    policy["file_level_review"] = {
        "dominant_label": "Label the homogeneous title-pattern file eligible, ineligible, or uncertain.",
        "mandatory_scan": "Read every title. A dominant file label never silently overrides an exception.",
        "exceptions": "For every exception provide review_id, label, policy_id, reason, and needs_article_body.",
        "body_escalation": "List every title-insufficient review_id in needs_article_body_review_ids.",
        "mixed_file": "If exceptions are numerous or no dominant label exists, use uncertain and enumerate resolved exceptions; unresolved rows require body review.",
    }
    policy["file_review_output_fields"] = sorted(REVIEW_FIELDS)
    policy_path = output_root / "blind" / "REVIEW_POLICY.json"
    write_json_new(policy_path, policy)

    ordered_pass_one = sorted(
        blind_groups,
        key=lambda group: digest(f"{AUDIT_VERSION}|pass-1|{group['file_id']}"),
    )
    pass_one_lanes = {worker: [] for worker in WORKERS}
    pass_one_rows: Counter[str] = Counter()
    pass_one_owner: dict[str, str] = {}
    for group in ordered_pass_one:
        worker = min(WORKERS, key=lambda value: (pass_one_rows[value], value))
        pass_one_lanes[worker].append(group)
        pass_one_rows[worker] += int(group["rows"])
        pass_one_owner[str(group["file_id"])] = worker

    ordered_pass_two = sorted(
        blind_groups,
        key=lambda group: digest(f"{AUDIT_VERSION}|pass-2|{group['file_id']}"),
    )
    pass_two_lanes = {worker: [] for worker in WORKERS}
    pass_two_rows: Counter[str] = Counter()
    for group in ordered_pass_two:
        eligible = [worker for worker in WORKERS if worker != pass_one_owner[str(group["file_id"])]]
        worker = min(eligible, key=lambda value: (pass_two_rows[value], value))
        pass_two_lanes[worker].append(group)
        pass_two_rows[worker] += int(group["rows"])

    packet_ledger = []
    for pass_number, lanes in ((1, pass_one_lanes), (2, pass_two_lanes)):
        for worker in WORKERS:
            for packet_number, packet in enumerate(packetize_groups(lanes[worker]), start=1):
                packet_id = f"G{pass_number}-{worker[-1]}-{packet_number:03d}"
                packet_path = output_root / f"pass_{pass_number}" / "blind" / worker / f"{packet_id}.jsonl"
                output_path = output_root / f"pass_{pass_number}" / "reviews" / worker / f"{packet_id}.jsonl"
                write_jsonl_new(packet_path, packet)
                packet_ledger.append({
                    "pass": pass_number,
                    "worker": worker,
                    "packet_id": packet_id,
                    "files": len(packet),
                    "rows": sum(int(group["rows"]) for group in packet),
                    "packet_path": str(packet_path),
                    "packet_sha256": sha256_path(packet_path),
                    "output_path": str(output_path),
                })
    ledger_path = output_root / "blind" / "PACKET_LEDGER.jsonl"
    write_jsonl_new(ledger_path, packet_ledger)
    manifest = {
        "audit_version": AUDIT_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_path": str(source_path),
        "source_sha256": sha256_path(source_path),
        "training_rows": EXPECTED_ROWS,
        "training_files": EXPECTED_FILES,
        "excluded_holdout_rows": 626,
        "passes": 2,
        "packets": len(packet_ledger),
        "pass_one_worker_rows": dict(pass_one_rows),
        "pass_two_worker_rows": dict(pass_two_rows),
        "same_reviewer_files": sum(
            1
            for worker, groups in pass_two_lanes.items()
            for group in groups
            if pass_one_owner[str(group["file_id"])] == worker
        ),
        "gold_prediction_direction_and_prior_consensus_hidden": True,
        "review_policy_sha256": sha256_path(policy_path),
        "packet_ledger_sha256": sha256_path(ledger_path),
    }
    if manifest["same_reviewer_files"] != 0:
        raise ValueError("reviewer independence failed")
    write_json_new(output_root / "MANIFEST.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def validate_review_row(row: Mapping[str, Any], group: Mapping[str, Any], context: str) -> None:
    if set(row) != REVIEW_FIELDS:
        raise ValueError(f"review schema drift in {context}: {sorted(row)}")
    if row["file_id"] != group["file_id"]:
        raise ValueError(f"file identity mismatch in {context}")
    if row["label"] not in {"eligible", "ineligible", "uncertain"}:
        raise ValueError(f"invalid label in {context}: {row['label']}")
    if row["confidence"] not in {"high", "medium", "low"}:
        raise ValueError(f"invalid confidence in {context}: {row['confidence']}")
    for field in REVIEW_FIELDS - {
        "decisive_evidence",
        "exceptions",
        "needs_article_body_review_ids",
        "discovered_subpatterns",
    }:
        if not str(row[field]).strip():
            raise ValueError(f"blank {field} in {context}")
    evidence = row["decisive_evidence"]
    if isinstance(evidence, str):
        if not evidence.strip():
            raise ValueError(f"blank decisive_evidence in {context}")
    elif isinstance(evidence, Mapping):
        if not evidence:
            raise ValueError(f"invalid decisive_evidence in {context}")
        for key, value in evidence.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"invalid decisive_evidence in {context}")
            if isinstance(value, str):
                valid_value = bool(value.strip())
            elif isinstance(value, list):
                valid_value = bool(value) and all(
                    isinstance(item, str) and bool(item.strip()) for item in value
                )
            else:
                valid_value = False
            if not valid_value:
                raise ValueError(f"invalid decisive_evidence in {context}")
    elif not isinstance(evidence, list) or not evidence or any(
        not isinstance(value, str) or not value.strip() for value in evidence
    ):
        raise ValueError(f"invalid decisive_evidence in {context}")
    if not isinstance(row["exceptions"], list):
        raise ValueError(f"exceptions is not a list in {context}")
    if not isinstance(row["needs_article_body_review_ids"], list):
        raise ValueError(f"needs_article_body_review_ids is not a list in {context}")
    if not isinstance(row["discovered_subpatterns"], list):
        raise ValueError(f"discovered_subpatterns is not a list in {context}")
    allowed_ids = set(group["review_ids"])
    body_values = row["needs_article_body_review_ids"]
    if any(not isinstance(value, str) or not value.strip() for value in body_values):
        raise ValueError(f"invalid body-review ID in {context}")
    body_ids = set(body_values)
    if len(body_ids) != len(body_values):
        raise ValueError(f"duplicate body-review ID in {context}")
    if not body_ids <= allowed_ids:
        raise ValueError(f"unknown body-review ID in {context}")
    subpatterns = row["discovered_subpatterns"]
    if any(not isinstance(value, str) or not value.strip() for value in subpatterns):
        raise ValueError(f"invalid discovered subpattern in {context}")
    exception_ids = set()
    exception_body_ids = set()
    for exception in row["exceptions"]:
        required = {"review_id", "label", "policy_id", "reason", "needs_article_body"}
        if set(exception) != required:
            raise ValueError(f"exception schema drift in {context}")
        review_id = str(exception["review_id"])
        if review_id not in allowed_ids or review_id in exception_ids:
            raise ValueError(f"invalid exception identity in {context}: {review_id}")
        exception_ids.add(review_id)
        if exception["label"] not in {"eligible", "ineligible", "uncertain"}:
            raise ValueError(f"invalid exception label in {context}")
        if not str(exception["policy_id"]).strip() or not str(exception["reason"]).strip():
            raise ValueError(f"blank exception evidence in {context}: {review_id}")
        if not isinstance(exception["needs_article_body"], bool):
            raise ValueError(f"exception body flag invalid in {context}")
        if exception["needs_article_body"]:
            exception_body_ids.add(review_id)
    if exception_body_ids != body_ids & exception_ids:
        raise ValueError(f"exception/body-review parity mismatch in {context}")


def validate_evidence(value: Any, context: str) -> None:
    if isinstance(value, str):
        valid = bool(value.strip())
    elif isinstance(value, list):
        valid = bool(value) and all(
            isinstance(item, str) and bool(item.strip()) for item in value
        )
    elif isinstance(value, Mapping):
        valid = bool(value)
        for key, item in value.items():
            valid = valid and isinstance(key, str) and bool(key.strip())
            if isinstance(item, str):
                valid = valid and bool(item.strip())
            elif isinstance(item, list):
                valid = valid and bool(item) and all(
                    isinstance(part, str) and bool(part.strip()) for part in item
                )
            else:
                valid = False
    else:
        valid = False
    if not valid:
        raise ValueError(f"invalid decisive_evidence in {context}")


def validate_reviews(output_root: Path) -> dict[str, Any]:
    manifest = json.loads((output_root / "MANIFEST.json").read_text(encoding="utf-8"))
    ledger_path = output_root / "blind" / "PACKET_LEDGER.jsonl"
    if sha256_path(ledger_path) != manifest["packet_ledger_sha256"]:
        raise ValueError("packet ledger hash changed")
    controller = {
        str(row["file_id"]): row
        for row in iter_jsonl(output_root / "controller" / "GROUP_CONTROLLER.jsonl")
    }
    summaries = {1: Counter(), 2: Counter()}
    reviewed = {1: set(), 2: set()}
    complete = True
    for packet in iter_jsonl(ledger_path):
        packet_path = Path(packet["packet_path"])
        if sha256_path(packet_path) != packet["packet_sha256"]:
            raise ValueError(f"packet hash changed: {packet['packet_id']}")
        expected_groups = list(iter_jsonl(packet_path))
        expected = {str(group["file_id"]) for group in expected_groups}
        output_path = Path(packet["output_path"])
        if not output_path.exists():
            complete = False
            continue
        rows = list(iter_jsonl(output_path))
        actual = {str(row["file_id"]) for row in rows}
        if actual != expected or len(actual) != len(rows):
            raise ValueError(f"packet membership mismatch: {packet['packet_id']}")
        for row in rows:
            validate_review_row(row, controller[str(row["file_id"])], str(packet["packet_id"]))
            summaries[int(packet["pass"])][f"label:{row['label']}"] += 1
            summaries[int(packet["pass"])]["exceptions"] += len(row["exceptions"])
            summaries[int(packet["pass"])]["body_review_ids"] += len(row["needs_article_body_review_ids"])
        reviewed[int(packet["pass"])] |= actual
    result = {
        f"pass_{pass_number}": {
            "reviewed_files": len(reviewed[pass_number]),
            "remaining_files": EXPECTED_FILES - len(reviewed[pass_number]),
            **dict(summaries[pass_number]),
        }
        for pass_number in (1, 2)
    }
    result["complete"] = complete and all(len(value) == EXPECTED_FILES for value in reviewed.values())
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def effective_row_review(file_review: Mapping[str, Any], review_id: str) -> dict[str, Any]:
    exception = next(
        (
            value
            for value in file_review["exceptions"]
            if str(value["review_id"]) == review_id
        ),
        None,
    )
    return {
        "label": exception["label"] if exception else file_review["label"],
        "policy_id": exception["policy_id"] if exception else file_review["policy_id"],
        "reason": exception["reason"] if exception else file_review["justification"],
        "needs_article_body": review_id in file_review["needs_article_body_review_ids"],
    }


def reconcile_reviews(output_root: Path, source_root: Path) -> dict[str, Any]:
    validation = validate_reviews(output_root)
    if not validation["complete"]:
        raise ValueError("both review passes must be complete before reconciliation")

    controller_rows = list(iter_jsonl(output_root / "controller" / "GROUP_CONTROLLER.jsonl"))
    source_rows = {
        str(row["review_id"]): row
        for row in iter_jsonl(source_root / "CONSOLIDATED_REVIEWS.jsonl")
    }
    pass_reviews: dict[int, dict[str, dict[str, Any]]] = {1: {}, 2: {}}
    for pass_number in (1, 2):
        for path in (output_root / f"pass_{pass_number}" / "reviews").rglob("*.jsonl"):
            for review in iter_jsonl(path):
                pass_reviews[pass_number][str(review["file_id"])] = review

    reconciled = []
    adjudication = []
    agreements = []
    for group in controller_rows:
        file_id = str(group["file_id"])
        first = pass_reviews[1][file_id]
        second = pass_reviews[2][file_id]
        for review_id_value in group["review_ids"]:
            review_id = str(review_id_value)
            source = source_rows[review_id]
            pass_one = effective_row_review(first, review_id)
            pass_two = effective_row_review(second, review_id)
            requires_adjudication = (
                pass_one["label"] != pass_two["label"]
                or pass_one["label"] == "uncertain"
                or pass_one["needs_article_body"]
                or pass_two["needs_article_body"]
            )
            row = {
                "review_id": review_id,
                "file_id": file_id,
                "title_pattern": group["title_pattern"],
                "gold_label": source["gold_label"],
                "v59_label": source["v59_label"],
                "confusion_cell": source["confusion_cell"],
                "pass_1": pass_one,
                "pass_2": pass_two,
                "status": "adjudication_required" if requires_adjudication else "agreement",
                "agreed_label": None if requires_adjudication else pass_one["label"],
                "proposed_gold_correction": (
                    False
                    if requires_adjudication
                    else pass_one["label"] != source["gold_label"]
                ),
            }
            reconciled.append(row)
            if requires_adjudication:
                adjudication.append(
                    {
                        "review_id": review_id,
                        "file_id": file_id,
                        "metadata": {
                            "author": source.get("author"),
                            "channels": source.get("channels", []),
                            "provider_tags": source.get("provider_tags", []),
                            "tickers": source.get("tickers", []),
                            "ticker_count": source.get("ticker_count"),
                            "published_at_utc": source.get("published_at_utc"),
                            "synthesis_path": source.get("synthesis_path"),
                        },
                        "title": source["title"],
                        "pass_1": pass_one,
                        "pass_2": pass_two,
                    }
                )
            else:
                agreements.append(row)

    reconciliation_root = output_root / "reconciliation"
    write_jsonl_new(reconciliation_root / "ROW_RECONCILIATION.jsonl", reconciled)
    write_jsonl_new(reconciliation_root / "AUTO_AGREEMENTS.jsonl", agreements)
    write_jsonl_new(
        reconciliation_root / "blind" / "ADJUDICATION_REQUIRED.jsonl", adjudication
    )
    summary = {
        "rows": len(reconciled),
        "agreements": len(agreements),
        "adjudication_required": len(adjudication),
        "agreement_corrections": sum(
            bool(row["proposed_gold_correction"]) for row in agreements
        ),
        "holdout_rows_excluded": 626,
        "row_reconciliation_sha256": sha256_path(
            reconciliation_root / "ROW_RECONCILIATION.jsonl"
        ),
        "blind_adjudication_sha256": sha256_path(
            reconciliation_root / "blind" / "ADJUDICATION_REQUIRED.jsonl"
        ),
    }
    write_json_new(reconciliation_root / "MANIFEST.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def adjudication_lane(pass_two_packet_id: str) -> str:
    if pass_two_packet_id.startswith("G2-1-"):
        return "lane_a"
    if pass_two_packet_id == "G2-2-043":
        return "lane_a"
    if pass_two_packet_id.startswith("G2-2-"):
        return "lane_b"
    if pass_two_packet_id.startswith("G2-3-"):
        return "lane_c"
    raise ValueError(f"unknown pass-2 packet: {pass_two_packet_id}")


def prepare_adjudication(output_root: Path, source_root: Path) -> dict[str, Any]:
    reconciliation_root = output_root / "reconciliation"
    input_path = reconciliation_root / "blind" / "ADJUDICATION_REQUIRED.jsonl"
    if not input_path.exists():
        raise FileNotFoundError("run reconcile before preparing adjudication")
    adjudication_root = reconciliation_root / "adjudication"
    if adjudication_root.exists():
        raise FileExistsError(f"refusing to overwrite adjudication root: {adjudication_root}")

    source_rows = {
        str(row["review_id"]): row
        for row in iter_jsonl(source_root / "CONSOLIDATED_REVIEWS.jsonl")
    }
    group_rows = {
        str(row["file_id"]): row
        for row in iter_jsonl(output_root / "controller" / "GROUP_CONTROLLER.jsonl")
    }
    file_packet: dict[str, str] = {}
    for packet in iter_jsonl(output_root / "blind" / "PACKET_LEDGER.jsonl"):
        if int(packet["pass"]) != 2:
            continue
        for group in iter_jsonl(Path(packet["packet_path"])):
            file_id = str(group["file_id"])
            if file_id in file_packet:
                raise ValueError(f"duplicate pass-2 file assignment: {file_id}")
            file_packet[file_id] = str(packet["packet_id"])

    lanes: dict[str, list[dict[str, Any]]] = {
        "lane_a": [],
        "lane_b": [],
        "lane_c": [],
    }
    controller = []
    seen = set()
    for row in iter_jsonl(input_path):
        review_id = str(row["review_id"])
        if review_id in seen:
            raise ValueError(f"duplicate adjudication review ID: {review_id}")
        seen.add(review_id)
        source = source_rows[review_id]
        file_id = str(row["file_id"])
        packet_id = file_packet[file_id]
        lane = adjudication_lane(packet_id)
        blind = {
            "review_id": review_id,
            "file_id": file_id,
            "title_pattern": str(group_rows[file_id]["title_pattern"]),
            "metadata": row["metadata"],
            "title": str(row["title"]),
        }
        lanes[lane].append(blind)
        controller.append(
            {
                **blind,
                "source_id": str(source["source_id"]),
                "gold_label": str(source["gold_label"]),
                "v59_label": str(source["v59_label"]),
                "confusion_cell": str(source["confusion_cell"]),
                "pass_1": row["pass_1"],
                "pass_2": row["pass_2"],
                "pass_2_packet_id": packet_id,
                "adjudication_lane": lane,
            }
        )

    controller.sort(key=lambda row: str(row["review_id"]))
    write_jsonl_new(adjudication_root / "controller" / "CONTROLLER.jsonl", controller)
    ledger = []
    for lane, rows in lanes.items():
        rows.sort(key=lambda row: digest(f"{AUDIT_VERSION}|adjudication|{row['review_id']}"))
        for index in range(0, len(rows), ADJUDICATION_PACKET_LIMIT):
            packet = rows[index:index + ADJUDICATION_PACKET_LIMIT]
            packet_id = f"A3-{lane[-1].upper()}-{index // ADJUDICATION_PACKET_LIMIT + 1:03d}"
            packet_path = adjudication_root / "blind" / lane / f"{packet_id}.jsonl"
            output_path = adjudication_root / "reviews" / lane / f"{packet_id}.jsonl"
            write_jsonl_new(packet_path, packet)
            ledger.append(
                {
                    "packet_id": packet_id,
                    "lane": lane,
                    "rows": len(packet),
                    "packet_path": str(packet_path),
                    "packet_sha256": sha256_path(packet_path),
                    "output_path": str(output_path),
                }
            )
    ledger_path = adjudication_root / "blind" / "PACKET_LEDGER.jsonl"
    write_jsonl_new(ledger_path, ledger)
    policy_path = output_root / "blind" / "REVIEW_POLICY.json"
    manifest = {
        "rows": len(controller),
        "packets": len(ledger),
        "lane_rows": {lane: len(rows) for lane, rows in lanes.items()},
        "lane_packets": Counter(row["lane"] for row in ledger),
        "input_sha256": sha256_path(input_path),
        "controller_sha256": sha256_path(
            adjudication_root / "controller" / "CONTROLLER.jsonl"
        ),
        "packet_ledger_sha256": sha256_path(ledger_path),
        "review_policy_sha256": sha256_path(policy_path),
        "gold_v59_prior_reviews_hidden": True,
    }
    write_json_new(adjudication_root / "MANIFEST.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def validate_adjudication_row(
    row: Mapping[str, Any], expected_review_id: str, context: str
) -> None:
    if set(row) != ADJUDICATION_REVIEW_FIELDS:
        raise ValueError(f"adjudication schema drift in {context}: {sorted(row)}")
    if str(row["review_id"]) != expected_review_id:
        raise ValueError(f"adjudication identity mismatch in {context}")
    if row["label"] not in {"eligible", "ineligible", "uncertain"}:
        raise ValueError(f"invalid adjudication label in {context}: {row['label']}")
    if row["confidence"] not in {"high", "medium", "low"}:
        raise ValueError(f"invalid adjudication confidence in {context}: {row['confidence']}")
    for field in ("policy_id", "discovered_pattern", "justification"):
        if not isinstance(row[field], str) or not row[field].strip():
            raise ValueError(f"blank adjudication {field} in {context}")
    if not isinstance(row["needs_article_body"], bool):
        raise ValueError(f"invalid adjudication body flag in {context}")
    if row["label"] == "uncertain" and not row["needs_article_body"]:
        raise ValueError(f"uncertain adjudication without body request in {context}")
    validate_evidence(row["decisive_evidence"], context)


def validate_adjudication(output_root: Path) -> dict[str, Any]:
    adjudication_root = output_root / "reconciliation" / "adjudication"
    manifest = json.loads((adjudication_root / "MANIFEST.json").read_text(encoding="utf-8"))
    ledger_path = adjudication_root / "blind" / "PACKET_LEDGER.jsonl"
    if sha256_path(ledger_path) != manifest["packet_ledger_sha256"]:
        raise ValueError("adjudication packet ledger hash changed")
    reviewed = set()
    summary = Counter()
    complete = True
    for packet in iter_jsonl(ledger_path):
        packet_path = Path(packet["packet_path"])
        if sha256_path(packet_path) != packet["packet_sha256"]:
            raise ValueError(f"adjudication packet hash changed: {packet['packet_id']}")
        expected_rows = list(iter_jsonl(packet_path))
        expected_ids = [str(row["review_id"]) for row in expected_rows]
        output_path = Path(packet["output_path"])
        if not output_path.exists():
            complete = False
            continue
        rows = list(iter_jsonl(output_path))
        actual_ids = [str(row["review_id"]) for row in rows]
        if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
            raise ValueError(f"adjudication membership/order mismatch: {packet['packet_id']}")
        for row, review_id in zip(rows, expected_ids, strict=True):
            validate_adjudication_row(row, review_id, str(packet["packet_id"]))
            summary[f"label:{row['label']}"] += 1
            summary["body_review_ids"] += bool(row["needs_article_body"])
        reviewed.update(actual_ids)
    result = {
        "complete": complete and len(reviewed) == int(manifest["rows"]),
        "reviewed_rows": len(reviewed),
        "remaining_rows": int(manifest["rows"]) - len(reviewed),
        **dict(summary),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def reconcile_adjudication(output_root: Path) -> dict[str, Any]:
    validation = validate_adjudication(output_root)
    if not validation["complete"]:
        raise ValueError("third-pass adjudication must be complete before reconciliation")
    adjudication_root = output_root / "reconciliation" / "adjudication"
    controller = {
        str(row["review_id"]): row
        for row in iter_jsonl(adjudication_root / "controller" / "CONTROLLER.jsonl")
    }
    third_reviews = {}
    for path in (adjudication_root / "reviews").rglob("*.jsonl"):
        for row in iter_jsonl(path):
            review_id = str(row["review_id"])
            if review_id in third_reviews:
                raise ValueError(f"duplicate third-pass review: {review_id}")
            third_reviews[review_id] = row
    if set(third_reviews) != set(controller):
        raise ValueError("third-pass review population does not match controller")

    reconciled = []
    title_decisions = []
    body_required = []
    for review_id, source in controller.items():
        third = third_reviews[review_id]
        labels = [source["pass_1"]["label"], source["pass_2"]["label"], third["label"]]
        counts = Counter(label for label in labels if label in {"eligible", "ineligible"})
        majority_label = next(
            (label for label in ("eligible", "ineligible") if counts[label] >= 2),
            None,
        )
        needs_body = (
            bool(source["pass_1"]["needs_article_body"])
            or bool(source["pass_2"]["needs_article_body"])
            or bool(third["needs_article_body"])
            or "uncertain" in labels
            or majority_label is None
        )
        row = {
            **source,
            "pass_3": third,
            "title_vote_labels": labels,
            "title_majority_label": majority_label,
            "status": "body_review_required" if needs_body else "title_majority_decision",
            "final_label": None if needs_body else majority_label,
            "proposed_gold_correction": (
                False if needs_body else majority_label != source["gold_label"]
            ),
        }
        reconciled.append(row)
        if needs_body:
            body_required.append(
                {
                    "review_id": review_id,
                    "file_id": source["file_id"],
                    "title_pattern": source["title_pattern"],
                    "metadata": source["metadata"],
                    "title": source["title"],
                }
            )
        else:
            title_decisions.append(row)

    result_root = adjudication_root / "reconciliation"
    write_jsonl_new(result_root / "THIRD_PASS_RECONCILIATION.jsonl", reconciled)
    write_jsonl_new(result_root / "TITLE_MAJORITY_DECISIONS.jsonl", title_decisions)
    write_jsonl_new(result_root / "blind" / "BODY_REVIEW_REQUIRED.jsonl", body_required)
    summary = {
        "rows": len(reconciled),
        "title_majority_decisions": len(title_decisions),
        "body_review_required": len(body_required),
        "title_majority_corrections": sum(
            bool(row["proposed_gold_correction"]) for row in title_decisions
        ),
        "third_pass_reconciliation_sha256": sha256_path(
            result_root / "THIRD_PASS_RECONCILIATION.jsonl"
        ),
        "body_review_required_sha256": sha256_path(
            result_root / "blind" / "BODY_REVIEW_REQUIRED.jsonl"
        ),
    }
    write_json_new(result_root / "MANIFEST.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def export_body_review(output_root: Path) -> dict[str, Any]:
    from research.mlops.clickhouse import (
        ClickHouseHttpClient,
        discover_clickhouse_env_files,
        default_clickhouse_password,
        default_clickhouse_url,
        default_clickhouse_user,
        sql_string,
    )
    from research.mlops.env import load_env_files

    adjudication_root = output_root / "reconciliation" / "adjudication"
    queue_path = adjudication_root / "reconciliation" / "blind" / "BODY_REVIEW_REQUIRED.jsonl"
    if not queue_path.exists():
        raise FileNotFoundError("run reconcile-adjudication before exporting article bodies")
    body_root = adjudication_root / "body_review"
    if body_root.exists():
        raise FileExistsError(f"refusing to overwrite body-review root: {body_root}")
    controller = {
        str(row["review_id"]): row
        for row in iter_jsonl(adjudication_root / "controller" / "CONTROLLER.jsonl")
    }
    queue = list(iter_jsonl(queue_path))
    source_by_review = {
        str(row["review_id"]): str(controller[str(row["review_id"])]["source_id"])
        for row in queue
    }
    identifiers = sorted(set(source_by_review.values()))
    articles: dict[str, dict[str, Any]] = {}
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=120,
    )
    try:
        for index in range(0, len(identifiers), 100):
            values = ",".join(sql_string(value) for value in identifiers[index:index + 100])
            query = f"""
SELECT canonical_news_id,title,normalized_full_text,text_hash,
       content_quality_flags,has_body,is_title_only,has_external_text,has_pdf
FROM q_live.benzinga_news_normalized_v1 FINAL
WHERE canonical_news_id IN ({values})
FORMAT JSONEachRow
"""
            for article in client.iter_json_each_row(query):
                source_id = str(article["canonical_news_id"])
                if source_id in articles:
                    raise ValueError(f"duplicate normalized article: {source_id}")
                articles[source_id] = article
    finally:
        client.close()
    missing = set(identifiers) - set(articles)
    if missing:
        client = ClickHouseHttpClient(
            default_clickhouse_url(),
            default_clickhouse_user(),
            default_clickhouse_password(),
            timeout_seconds=120,
        )
        try:
            missing_ids = sorted(missing)
            events: dict[str, dict[str, Any]] = {}
            for index in range(0, len(missing_ids), 100):
                values = ",".join(
                    sql_string(value) for value in missing_ids[index:index + 100]
                )
                query = f"""
SELECT canonical_news_id,title,toString(published_date) published_date,
       provider_article_id,source_revision_key,content_quality_flags
FROM q_live.benzinga_news_event_v2 FINAL
PREWHERE canonical_news_id IN ({values})
FORMAT JSONEachRow
"""
                for event in client.iter_json_each_row(query):
                    source_id = str(event["canonical_news_id"])
                    if source_id in events:
                        raise ValueError(f"duplicate fallback event: {source_id}")
                    events[source_id] = event
            render_keys = [
                (
                    str(event["published_date"]),
                    str(event["provider_article_id"]),
                    str(event["source_revision_key"]),
                )
                for event in events.values()
            ]
            rendered: dict[tuple[str, str, str], dict[str, Any]] = {}
            for index in range(0, len(render_keys), 100):
                tuples = ",".join(
                    f"(toDate({sql_string(day)}),{sql_string(provider_id)},{sql_string(revision)})"
                    for day, provider_id, revision in render_keys[index:index + 100]
                )
                query = f"""
SELECT toString(published_date) published_date,provider_article_id,
       source_revision_key,rendered_text,rendered_text_hash,quality_flags,source_count
FROM q_live.benzinga_news_rendered_v2 FINAL
PREWHERE (published_date,provider_article_id,source_revision_key) IN ({tuples})
FORMAT JSONEachRow
"""
                for row in client.iter_json_each_row(query):
                    key = (
                        str(row["published_date"]),
                        str(row["provider_article_id"]),
                        str(row["source_revision_key"]),
                    )
                    if key in rendered:
                        raise ValueError(f"duplicate fallback render: {key}")
                    rendered[key] = row
            for source_id, event in events.items():
                key = (
                    str(event["published_date"]),
                    str(event["provider_article_id"]),
                    str(event["source_revision_key"]),
                )
                render = rendered.get(key)
                rendered_text = str(render.get("rendered_text") or "") if render else ""
                articles[source_id] = {
                    "canonical_news_id": source_id,
                    "title": str(event.get("title") or ""),
                    "normalized_full_text": rendered_text or str(event.get("title") or ""),
                    "text_hash": (
                        str(render.get("rendered_text_hash") or "") if render else ""
                    ),
                    "content_quality_flags": list(event.get("content_quality_flags") or [])
                    + (list(render.get("quality_flags") or []) if render else []),
                    "has_body": bool(render and int(render.get("source_count") or 0) > 0),
                    "is_title_only": not render or int(render.get("source_count") or 0) == 0,
                    "has_external_text": False,
                    "has_pdf": False,
                }
        finally:
            client.close()
        missing = set(identifiers) - set(articles)
    if missing:
        raise ValueError(f"normalized article text unavailable for {len(missing)} rows")

    blind = []
    body_controller = []
    lengths = []
    for row in queue:
        review_id = str(row["review_id"])
        source_id = source_by_review[review_id]
        article = articles[source_id]
        full_text = str(article.get("normalized_full_text") or "").strip()
        lengths.append(len(full_text))
        blind_row = {
            **row,
            "article_text": full_text,
            "text_hash": str(article.get("text_hash") or ""),
            "content_quality_flags": list(article.get("content_quality_flags") or []),
            "has_body": bool(article.get("has_body")),
            "is_title_only": bool(article.get("is_title_only")),
            "has_external_text": bool(article.get("has_external_text")),
            "has_pdf": bool(article.get("has_pdf")),
        }
        blind.append(blind_row)
        body_controller.append({**blind_row, "source_id": source_id})
    write_jsonl_new(body_root / "blind" / "BODY_REVIEW_SOURCE.jsonl", blind)
    write_jsonl_new(body_root / "controller" / "BODY_CONTROLLER.jsonl", body_controller)
    summary = {
        "rows": len(blind),
        "unique_source_ids": len(identifiers),
        "empty_article_text": sum(length == 0 for length in lengths),
        "total_article_characters": sum(lengths),
        "max_article_characters": max(lengths, default=0),
        "mean_article_characters": (sum(lengths) / len(lengths)) if lengths else 0,
        "title_only": sum(bool(row["is_title_only"]) for row in blind),
        "body_review_source_sha256": sha256_path(
            body_root / "blind" / "BODY_REVIEW_SOURCE.jsonl"
        ),
        "controller_sha256": sha256_path(
            body_root / "controller" / "BODY_CONTROLLER.jsonl"
        ),
    }
    write_json_new(body_root / "MANIFEST.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def packetize_body_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    packets: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    characters = 0
    for row in rows:
        row_characters = len(str(row.get("article_text") or ""))
        if current and (
            len(current) >= BODY_PACKET_ROW_LIMIT
            or characters + row_characters > BODY_PACKET_CHARACTER_LIMIT
        ):
            packets.append(current)
            current = []
            characters = 0
        current.append(row)
        characters += row_characters
    if current:
        packets.append(current)
    return packets


def prepare_body_packets(output_root: Path) -> dict[str, Any]:
    body_root = output_root / "reconciliation" / "adjudication" / "body_review"
    source_path = body_root / "blind" / "BODY_REVIEW_SOURCE.jsonl"
    packet_root = body_root / "packets"
    if not source_path.exists():
        raise FileNotFoundError("run export-body-review before preparing body packets")
    if packet_root.exists():
        raise FileExistsError(f"refusing to overwrite body packets: {packet_root}")
    rows = list(iter_jsonl(source_path))
    rows.sort(key=lambda row: digest(f"{AUDIT_VERSION}|body|{row['review_id']}"))
    packets = packetize_body_rows(rows)
    ledger = []
    for index, packet in enumerate(packets, start=1):
        packet_id = f"B4-{index:03d}"
        packet_path = packet_root / "blind" / f"{packet_id}.jsonl"
        output_path = packet_root / "reviews" / f"{packet_id}.jsonl"
        write_jsonl_new(packet_path, packet)
        ledger.append(
            {
                "packet_id": packet_id,
                "rows": len(packet),
                "article_characters": sum(
                    len(str(row.get("article_text") or "")) for row in packet
                ),
                "packet_path": str(packet_path),
                "packet_sha256": sha256_path(packet_path),
                "output_path": str(output_path),
            }
        )
    ledger_path = packet_root / "blind" / "PACKET_LEDGER.jsonl"
    write_jsonl_new(ledger_path, ledger)
    summary = {
        "rows": len(rows),
        "packets": len(ledger),
        "max_packet_rows": max((row["rows"] for row in ledger), default=0),
        "max_packet_characters": max(
            (row["article_characters"] for row in ledger), default=0
        ),
        "packet_ledger_sha256": sha256_path(ledger_path),
        "body_review_source_sha256": sha256_path(source_path),
    }
    write_json_new(packet_root / "MANIFEST.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def validate_body_review_row(
    row: Mapping[str, Any], expected_review_id: str, context: str
) -> None:
    if set(row) != BODY_REVIEW_FIELDS:
        raise ValueError(f"body-review schema drift in {context}: {sorted(row)}")
    if str(row["review_id"]) != expected_review_id:
        raise ValueError(f"body-review identity mismatch in {context}")
    if row["label"] not in {"eligible", "ineligible", "uncertain"}:
        raise ValueError(f"invalid body-review label in {context}: {row['label']}")
    if row["confidence"] not in {"high", "medium", "low"}:
        raise ValueError(f"invalid body-review confidence in {context}")
    if not isinstance(row["source_sufficient"], bool):
        raise ValueError(f"invalid source_sufficient in {context}")
    if row["label"] == "uncertain" and row["source_sufficient"]:
        raise ValueError(f"uncertain body review marked source-sufficient in {context}")
    for field in ("policy_id", "discovered_pattern", "justification"):
        if not isinstance(row[field], str) or not row[field].strip():
            raise ValueError(f"blank body-review {field} in {context}")
    validate_evidence(row["decisive_evidence"], context)


def validate_body_reviews(output_root: Path) -> dict[str, Any]:
    packet_root = (
        output_root / "reconciliation" / "adjudication" / "body_review" / "packets"
    )
    manifest = json.loads((packet_root / "MANIFEST.json").read_text(encoding="utf-8"))
    ledger_path = packet_root / "blind" / "PACKET_LEDGER.jsonl"
    if sha256_path(ledger_path) != manifest["packet_ledger_sha256"]:
        raise ValueError("body-review packet ledger hash changed")
    reviewed = set()
    summary = Counter()
    complete = True
    for packet in iter_jsonl(ledger_path):
        packet_path = Path(packet["packet_path"])
        if sha256_path(packet_path) != packet["packet_sha256"]:
            raise ValueError(f"body-review packet hash changed: {packet['packet_id']}")
        expected = list(iter_jsonl(packet_path))
        expected_ids = [str(row["review_id"]) for row in expected]
        output_path = Path(packet["output_path"])
        if not output_path.exists():
            complete = False
            continue
        rows = list(iter_jsonl(output_path))
        actual_ids = [str(row["review_id"]) for row in rows]
        if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
            raise ValueError(f"body-review membership/order mismatch: {packet['packet_id']}")
        for row, review_id in zip(rows, expected_ids, strict=True):
            validate_body_review_row(row, review_id, str(packet["packet_id"]))
            summary[f"label:{row['label']}"] += 1
            summary["source_insufficient"] += not bool(row["source_sufficient"])
        reviewed.update(actual_ids)
    result = {
        "complete": complete and len(reviewed) == int(manifest["rows"]),
        "reviewed_rows": len(reviewed),
        "remaining_rows": int(manifest["rows"]) - len(reviewed),
        **dict(summary),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def markdown_escape(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def finalize_gold_standard(output_root: Path, source_root: Path) -> dict[str, Any]:
    validation = validate_body_reviews(output_root)
    if not validation["complete"]:
        raise ValueError("body review must be complete before finalization")
    final_root = output_root / "reconciliation" / "final"
    if final_root.exists():
        raise FileExistsError(f"refusing to overwrite final reconciliation: {final_root}")
    if FINAL_TRAINING_AUTHORITY.exists():
        raise FileExistsError(
            f"refusing to overwrite final training authority: {FINAL_TRAINING_AUTHORITY}"
        )

    assignment_gold_by_source: dict[str, str] = {}
    training_assignment_ids: set[str] = set()
    assignment_source_rows = 0
    assignment_source_splits = Counter()
    with POLICY_ASSIGNMENTS.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("policy assignments have no header")
        for row in reader:
            assignment_source_rows += 1
            population_split = str(row["population_split"])
            assignment_source_splits[population_split] += 1
            source_id = str(row["source_id"])
            if source_id in assignment_gold_by_source:
                raise ValueError(f"duplicate assignment source ID: {source_id}")
            assignment_gold_by_source[source_id] = str(row["gold_label"])
            if population_split == "training_development":
                training_assignment_ids.add(source_id)
    expected_assignment_splits = Counter(
        {"training_development": 347_515, "holdout_august_2026": 5_044}
    )
    if (
        assignment_source_rows != 352_559
        or assignment_source_splits != expected_assignment_splits
        or len(assignment_gold_by_source) != 352_559
        or len(training_assignment_ids) != 347_515
    ):
        raise ValueError(
            "assignment source coverage changed: "
            f"rows={assignment_source_rows}, splits={assignment_source_splits}, "
            f"assignment_ids={len(assignment_gold_by_source)}, "
            f"training_ids={len(training_assignment_ids)}"
        )

    source_rows = {
        str(row["review_id"]): row
        for row in iter_jsonl(source_root / "CONSOLIDATED_REVIEWS.jsonl")
    }
    auto = {
        str(row["review_id"]): row
        for row in iter_jsonl(output_root / "reconciliation" / "AUTO_AGREEMENTS.jsonl")
    }
    title = {
        str(row["review_id"]): row
        for row in iter_jsonl(
            output_root
            / "reconciliation"
            / "adjudication"
            / "reconciliation"
            / "TITLE_MAJORITY_DECISIONS.jsonl"
        )
    }
    body_root = output_root / "reconciliation" / "adjudication" / "body_review"
    body_controller = {
        str(row["review_id"]): row
        for row in iter_jsonl(body_root / "controller" / "BODY_CONTROLLER.jsonl")
    }
    body_reviews: dict[str, dict[str, Any]] = {}
    for path in (body_root / "packets" / "reviews").glob("*.jsonl"):
        for row in iter_jsonl(path):
            review_id = str(row["review_id"])
            if review_id in body_reviews:
                raise ValueError(f"duplicate body review: {review_id}")
            body_reviews[review_id] = row
    if set(body_reviews) != set(body_controller):
        raise ValueError("body review/controller population mismatch")

    pattern_by_review: dict[str, str] = {}
    for group in iter_jsonl(output_root / "controller" / "GROUP_CONTROLLER.jsonl"):
        for review_id_value in group["review_ids"]:
            review_id = str(review_id_value)
            if review_id in pattern_by_review:
                raise ValueError(f"duplicate grouped review ID: {review_id}")
            pattern_by_review[review_id] = str(group["title_pattern"])
    populations = [set(auto), set(title), set(body_reviews)]
    if any(populations[left] & populations[right] for left in range(3) for right in range(left + 1, 3)):
        raise ValueError("final decision populations overlap")
    if set().union(*populations) != set(source_rows):
        raise ValueError("final decision populations do not cover all mismatch rows")

    ledger = []
    counts = Counter()
    for review_id, source in source_rows.items():
        old_label = str(source["gold_label"])
        if review_id in auto:
            decision = auto[review_id]
            audited_label = str(decision["agreed_label"])
            final_label = audited_label
            decision_path = "two_pass_agreement"
            policy_ids = [decision["pass_1"]["policy_id"], decision["pass_2"]["policy_id"]]
            unresolved = False
        elif review_id in title:
            decision = title[review_id]
            audited_label = str(decision["final_label"])
            final_label = audited_label
            decision_path = "three_pass_title_majority"
            policy_ids = [
                decision["pass_1"]["policy_id"],
                decision["pass_2"]["policy_id"],
                decision["pass_3"]["policy_id"],
            ]
            unresolved = False
        else:
            decision = body_reviews[review_id]
            unresolved = str(decision["label"]) == "uncertain"
            audited_label = None if unresolved else str(decision["label"])
            final_label = old_label if unresolved else str(decision["label"])
            decision_path = (
                "source_insufficient_retained_gold" if unresolved else "blind_full_body_review"
            )
            policy_ids = [str(decision["policy_id"])]
        if final_label not in {"eligible", "ineligible"}:
            raise ValueError(f"nonbinary final label: {review_id}")
        correction = final_label != old_label
        row = {
            "review_id": review_id,
            "source_id": str(source["source_id"]),
            "published_at_utc": str(source["published_at_utc"]),
            "title": str(source["title"]),
            "title_pattern": pattern_by_review[review_id],
            "old_gold_label": old_label,
            "v59_label": str(source["v59_label"]),
            "audited_label": audited_label,
            "final_label": final_label,
            "decision_path": decision_path,
            "policy_ids": policy_ids,
            "unresolved": unresolved,
            "correction_applied": correction,
        }
        ledger.append(row)
        counts[f"decision_path:{decision_path}"] += 1
        counts[f"final_label:{final_label}"] += 1
        counts[f"change:{old_label}->{final_label}"] += correction
        counts["corrections"] += correction
        counts["unresolved"] += unresolved
    ledger.sort(key=lambda row: str(row["source_id"]))
    write_jsonl_new(final_root / "FINAL_CORRECTION_LEDGER.jsonl", ledger)

    broad_pattern = "title lacks qualifying prospective issuer event"
    broad_rows = [row for row in ledger if row["title_pattern"] == broad_pattern]
    write_jsonl_new(final_root / "BROAD_PATTERN_AUDIT.jsonl", broad_rows)
    broad_lines = [
        "# Broad title-pattern audit",
        "",
        f"Pattern: `{broad_pattern}`",
        "",
        f"Rows: **{len(broad_rows):,}**",
        "",
        "| Metadata | Gold | Final | Decision | Title |",
        "|---|---|---|---|---|",
    ]
    for row in broad_rows:
        metadata = f"id={row['source_id']} / published={row['published_at_utc']}"
        broad_lines.append(
            f"| {markdown_escape(metadata)} | {row['old_gold_label']} | {row['final_label']} | "
            f"{row['decision_path']} | {markdown_escape(row['title'])} |"
        )
    broad_path = final_root / "BROAD_PATTERN_AUDIT.md"
    broad_path.write_text("\n".join(broad_lines) + "\n", encoding="utf-8", newline="\n")

    decisions_by_source = {str(row["source_id"]): row for row in ledger}
    missing_assignment_decisions = set(decisions_by_source) - training_assignment_ids
    if missing_assignment_decisions:
        raise ValueError(
            f"audited rows missing from training assignments: {len(missing_assignment_decisions)}"
        )
    expected_authority_labels: dict[str, str] = {}
    FINAL_TRAINING_AUTHORITY.mkdir(parents=True)
    parent_labels = PARENT_TRAINING_AUTHORITY / "article_forecast_eligibility_labels.jsonl"
    final_labels_path = FINAL_TRAINING_AUTHORITY / "article_forecast_eligibility_labels.jsonl"
    authority_counts = Counter()
    authority_seen: set[str] = set()
    parent_assignment_alignment_changes = 0
    scoped = 0
    with final_labels_path.open("x", encoding="utf-8", newline="\n") as handle:
        for original in iter_jsonl(parent_labels):
            row = dict(original)
            source_id = str(row["source_id"])
            if source_id in authority_seen:
                raise ValueError(f"duplicate parent authority source ID: {source_id}")
            authority_seen.add(source_id)
            parent_label = str(row["forecast_eligibility_label"])
            assignment_label = assignment_gold_by_source.get(source_id)
            if assignment_label is not None and parent_label != assignment_label:
                parent_assignment_alignment_changes += 1
            decision = decisions_by_source.get(source_id)
            if decision:
                operator_protected = (
                    bool(row.get("human_certified"))
                    or str(row.get("authority_class") or "").startswith("operator_reviewed_")
                    or "human_policy_adjudicated" in str(row.get("usage_policy") or "")
                )
                if operator_protected and decision["final_label"] != parent_label:
                    raise ValueError(
                        "subagent decision conflicts with operator-protected parent label; "
                        "use build_news_v59_consolidated_gold_v2.py for explicit precedence: "
                        f"{source_id}"
                    )
                scoped += 1
                row.update(
                    {
                        "forecast_eligibility_label": decision["final_label"],
                        "forecast_eligible": decision["final_label"] == "eligible",
                        "audit_authority_version": FINAL_TRAINING_AUTHORITY.name,
                        "audit_review_id": decision["review_id"],
                        "audit_decision_path": decision["decision_path"],
                        "audit_unresolved": bool(decision["unresolved"]),
                        "source_dataset": FINAL_TRAINING_AUTHORITY.name,
                    }
                )
                if decision["unresolved"]:
                    row.update(
                        {
                            "decisive": False,
                            "authority_class": "codex_reaudit_unresolved_retained_parent",
                            "authority_detail": AUDIT_VERSION,
                            "certification_level": "source_insufficient",
                            "human_certified": False,
                            "usage_policy": "model_development_exclude_unresolved",
                        }
                    )
                else:
                    row.update(
                        {
                            "decisive": True,
                            "authority_class": "codex_blind_multi_pass_policy_adjudication",
                            "authority_detail": AUDIT_VERSION,
                            "certification_level": "codex_correction_grade_blind_reaudit",
                            "human_certified": False,
                            "usage_policy": "model_development_codex_adjudicated",
                        }
                    )
                if decision["correction_applied"]:
                    row["superseded_forecast_eligibility_label"] = decision["old_gold_label"]
            else:
                # The assignment CSV describes the frozen audit population; its gold_label
                # is not a successor authority.  Retain the already-corrected parent for
                # every row outside this audit instead of resurrecting older labels.
                target_label = parent_label
                row["forecast_eligibility_label"] = target_label
                row["forecast_eligible"] = target_label == "eligible"
            expected_authority_labels[source_id] = str(row["forecast_eligibility_label"])
            authority_counts[str(row["forecast_eligibility_label"])] += 1
            handle.write(canonical_json(row) + "\n")
    if scoped != EXPECTED_ROWS:
        raise ValueError(f"training correction coverage changed: {scoped}")
    missing_assignments = training_assignment_ids - authority_seen
    if missing_assignments:
        raise ValueError(
            f"assignment rows missing from successor authority: {len(missing_assignments)}"
        )
    for row in iter_jsonl(final_labels_path):
        source_id = str(row["source_id"])
        if str(row["forecast_eligibility_label"]) != expected_authority_labels[source_id]:
            raise ValueError(f"successor authority label mismatch: {source_id}")
    sentiment_source = PARENT_TRAINING_AUTHORITY / "gold_issuer_sentiment_labels.jsonl"
    sentiment_target = FINAL_TRAINING_AUTHORITY / "gold_issuer_sentiment_labels.jsonl"
    shutil.copyfile(sentiment_source, sentiment_target)
    write_jsonl_new(FINAL_TRAINING_AUTHORITY / "CORRECTION_LEDGER.jsonl", ledger)

    assignments_path = final_root / "ARTICLE_POLICY_ASSIGNMENTS_REAUDITED.csv"
    assignment_rows = 0
    assignment_splits = Counter()
    assignment_scoped = 0
    assignment_input_drifts = []
    with POLICY_ASSIGNMENTS.open("r", encoding="utf-8-sig", newline="") as source_handle:
        reader = csv.DictReader(source_handle)
        if not reader.fieldnames:
            raise ValueError("policy assignments have no header")
        with assignments_path.open("x", encoding="utf-8", newline="") as target_handle:
            writer = csv.DictWriter(target_handle, fieldnames=reader.fieldnames, lineterminator="\n")
            writer.writeheader()
            for original in reader:
                row = dict(original)
                decision = decisions_by_source.get(str(row["source_id"]))
                if decision:
                    assignment_scoped += 1
                    if str(row["population_split"]) != "training_development":
                        raise ValueError("holdout row entered training correction scope")
                    if str(row["gold_label"]) != decision["old_gold_label"]:
                        assignment_input_drifts.append(
                            {
                                "source_id": str(row["source_id"]),
                                "frozen_audit_gold_label": decision["old_gold_label"],
                                "mutable_assignment_gold_label": str(row["gold_label"]),
                                "final_label": decision["final_label"],
                            }
                        )
                    row["gold_label"] = decision["final_label"]
                assignment_rows += 1
                assignment_splits[str(row["population_split"])] += 1
                writer.writerow(row)
    if assignment_rows != 352_559 or assignment_scoped != EXPECTED_ROWS:
        raise ValueError(
            f"assignment coverage changed: rows={assignment_rows}, scoped={assignment_scoped}"
        )
    if assignment_splits != expected_assignment_splits:
        raise ValueError(f"assignment split drift: {assignment_splits}")
    write_jsonl_new(final_root / "ASSIGNMENT_INPUT_DRIFT.jsonl", assignment_input_drifts)

    broad_summary = {
        "rows": len(broad_rows),
        "final_labels": dict(Counter(row["final_label"] for row in broad_rows)),
        "old_gold_labels": dict(Counter(row["old_gold_label"] for row in broad_rows)),
        "corrections": sum(bool(row["correction_applied"]) for row in broad_rows),
        "unresolved": sum(bool(row["unresolved"]) for row in broad_rows),
    }
    summary = {
        "rows": len(ledger),
        "counts": dict(counts),
        "authority_label_counts": dict(authority_counts),
        "holdout_rows_changed": 0,
        "mutable_assignment_input_drift_rows": len(assignment_input_drifts),
        "parent_assignment_alignment_changes": parent_assignment_alignment_changes,
        "broad_pattern": broad_summary,
        "final_ledger_sha256": sha256_path(final_root / "FINAL_CORRECTION_LEDGER.jsonl"),
        "broad_pattern_audit_sha256": sha256_path(final_root / "BROAD_PATTERN_AUDIT.jsonl"),
        "assignments_sha256": sha256_path(assignments_path),
        "assignment_input_drift_sha256": sha256_path(
            final_root / "ASSIGNMENT_INPUT_DRIFT.jsonl"
        ),
        "training_labels_sha256": sha256_path(final_labels_path),
        "sentiment_labels_sha256": sha256_path(sentiment_target),
    }
    write_json_new(final_root / "MANIFEST.json", summary)
    write_json_new(FINAL_TRAINING_AUTHORITY / "MANIFEST.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "validate-reviews",
            "reconcile",
            "prepare-adjudication",
            "validate-adjudication",
            "reconcile-adjudication",
            "export-body-review",
            "prepare-body-packets",
            "validate-body-reviews",
            "finalize-gold-standard",
        ),
    )
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.source_root, args.output_root)
    elif args.command == "validate-reviews":
        validate_reviews(args.output_root)
    elif args.command == "reconcile":
        reconcile_reviews(args.output_root, args.source_root)
    elif args.command == "prepare-adjudication":
        prepare_adjudication(args.output_root, args.source_root)
    elif args.command == "validate-adjudication":
        validate_adjudication(args.output_root)
    elif args.command == "reconcile-adjudication":
        reconcile_adjudication(args.output_root)
    elif args.command == "export-body-review":
        export_body_review(args.output_root)
    elif args.command == "prepare-body-packets":
        prepare_body_packets(args.output_root)
    elif args.command == "validate-body-reviews":
        validate_body_reviews(args.output_root)
    else:
        finalize_gold_standard(args.output_root, args.source_root)


if __name__ == "__main__":
    main()
