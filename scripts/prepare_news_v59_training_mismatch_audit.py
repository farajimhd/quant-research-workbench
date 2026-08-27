from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


AUDIT_VERSION = "news_v59_training_mismatch_blind_audit_v1"
NEWS_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1")
EVALUATION_ROOT = NEWS_ROOT / "news_synthesis_v59_aligned_title_event_policy_gold_evaluation_v1"
ASSIGNMENTS = (
    NEWS_ROOT
    / "news_title_pattern_policy_disagreement_audit_2025_2026_v1"
    / "ARTICLE_POLICY_ASSIGNMENTS.csv"
)
DEFAULT_OUTPUT = NEWS_ROOT / AUDIT_VERSION
EXPECTED_EVALUATION_MISMATCHES = 43_995
EXPECTED_TRAINING = {"fp": 6_443, "fn": 36_926}
EXPECTED_HOLDOUT = {"fp": 111, "fn": 515}
PACKET_LIMIT = 100
WORKERS = ("worker_1", "worker_2", "worker_3")
ALLOWED_LABELS = {"eligible", "ineligible", "uncertain"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
BLIND_FIELDS = {
    "review_id", "title", "published_at_utc", "author", "channels",
    "provider_tags", "tickers", "ticker_count", "synthesis_path",
}
REVIEW_FIELDS = {
    "review_id", "label", "confidence", "policy", "title_pattern",
    "justification", "exception_flag",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    # PowerShell's UTF-8 writer may emit a BOM; utf-8-sig accepts both forms.
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


def load_assignments(path: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id = str(row["source_id"])
            if source_id in result:
                raise ValueError(f"duplicate assignment source_id: {source_id}")
            result[source_id] = row
    if len(result) != 352_559:
        raise ValueError(f"assignment authority changed: {len(result):,} rows")
    return result


def split_csv_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def packetize(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [rows[index:index + PACKET_LIMIT] for index in range(0, len(rows), PACKET_LIMIT)]


def validate_review_row(row: Mapping[str, Any], *, context: str) -> None:
    if set(row) != REVIEW_FIELDS:
        raise ValueError(f"review schema drift in {context}: {sorted(row)}")
    label = str(row.get("label") or "")
    confidence = str(row.get("confidence") or "")
    if label not in ALLOWED_LABELS:
        raise ValueError(f"invalid label in {context}: {label}")
    if confidence not in ALLOWED_CONFIDENCE:
        raise ValueError(f"invalid confidence in {context}: {confidence}")
    for field in ("policy", "title_pattern", "justification"):
        if not str(row.get(field) or "").strip():
            raise ValueError(f"missing {field} in {context}")
    if not isinstance(row.get("exception_flag"), bool):
        raise ValueError(f"exception_flag is not boolean in {context}")


def prepare(
    *,
    evaluation_root: Path = EVALUATION_ROOT,
    assignments_path: Path = ASSIGNMENTS,
    output_root: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite audit root: {output_root}")
    output_root.mkdir(parents=True)

    mismatch_path = evaluation_root / "MISMATCHES.jsonl"
    report_path = evaluation_root / "REPORT.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if int(report.get("mismatches", -1)) != EXPECTED_EVALUATION_MISMATCHES:
        raise ValueError("v59 evaluation mismatch count changed")
    if report.get("confusion") != {"fn": 37_441, "fp": 6_554, "tn": 264_675, "tp": 42_428}:
        raise ValueError(f"v59 confusion authority changed: {report.get('confusion')}")

    assignments = load_assignments(assignments_path)
    controller: list[dict[str, Any]] = []
    blind: list[dict[str, Any]] = []
    split_counts: Counter[tuple[str, str]] = Counter()
    seen_source_ids: set[str] = set()
    seen_review_ids: set[str] = set()

    for mismatch in iter_jsonl(mismatch_path):
        source_id = str(mismatch["source_id"])
        if source_id in seen_source_ids:
            raise ValueError(f"duplicate mismatch source_id: {source_id}")
        seen_source_ids.add(source_id)
        assignment = assignments.get(source_id)
        if assignment is None:
            raise ValueError(f"mismatch missing from assignment authority: {source_id}")
        split = str(assignment["population_split"])
        cell = str(mismatch["confusion_cell"])
        if cell not in {"fp", "fn"}:
            raise ValueError(f"non-binary mismatch cell: {cell}")
        split_counts[(split, cell)] += 1
        if split != "training_development":
            continue
        gold_label = str(mismatch["gold_label"])
        synthesis_label = str(mismatch["synthesis_label"])
        if (cell, gold_label, synthesis_label) not in {
            ("fp", "ineligible", "eligible"),
            ("fn", "eligible", "ineligible"),
        }:
            raise ValueError(f"invalid confusion labels for {source_id}")
        review_id = "V59" + digest(f"{AUDIT_VERSION}|{source_id}")[:21]
        if review_id in seen_review_ids:
            raise ValueError(f"duplicate review_id: {review_id}")
        seen_review_ids.add(review_id)
        tickers = list(mismatch.get("tickers") or split_csv_list(assignment.get("tickers", "")))
        metadata = {
            "published_at_utc": str(mismatch["published_at_utc"]),
            "author": str(assignment.get("author") or ""),
            "channels": split_csv_list(assignment.get("channels", "")),
            "provider_tags": split_csv_list(assignment.get("provider_tags", "")),
            "tickers": tickers,
            "ticker_count": len(tickers),
            "synthesis_path": str(mismatch.get("synthesis_path") or ""),
        }
        controller.append({
            "review_id": review_id,
            "source_id": source_id,
            "population_split": split,
            "gold_label": gold_label,
            "v59_label": synthesis_label,
            "confusion_cell": cell,
            "title": str(mismatch["title"]),
            "normalized_title_template": str(assignment.get("normalized_title_template") or ""),
            "primary_pattern_id": str(assignment.get("primary_pattern_id") or ""),
            "eligible_policy_patterns": split_csv_list(assignment.get("eligible_policy_patterns", "")),
            "ineligible_policy_patterns": split_csv_list(assignment.get("ineligible_policy_patterns", "")),
            "mixed_patterns": split_csv_list(assignment.get("mixed_patterns", "")),
            **metadata,
        })
        blind.append({
            "review_id": review_id,
            "title": str(mismatch["title"]),
            **metadata,
        })

    actual_training = {cell: split_counts[("training_development", cell)] for cell in ("fp", "fn")}
    actual_holdout = {cell: split_counts[("holdout_august_2026", cell)] for cell in ("fp", "fn")}
    if actual_training != EXPECTED_TRAINING:
        raise ValueError(f"training mismatch population changed: {actual_training}")
    if actual_holdout != EXPECTED_HOLDOUT:
        raise ValueError(f"holdout mismatch population changed: {actual_holdout}")
    if len(controller) != sum(EXPECTED_TRAINING.values()):
        raise ValueError("training controller population is incomplete")

    controller.sort(key=lambda row: str(row["source_id"]))
    controller_path = output_root / "controller" / "CONTROLLER.jsonl"
    write_jsonl_new(controller_path, controller)

    blind.sort(key=lambda row: digest(f"{AUDIT_VERSION}|blind-order|{row['review_id']}"))
    lanes: dict[str, list[dict[str, Any]]] = {worker: [] for worker in WORKERS}
    for index, row in enumerate(blind):
        lanes[WORKERS[index % len(WORKERS)]].append(row)

    packet_ledger: list[dict[str, Any]] = []
    worker_loads: dict[str, int] = {}
    for worker in WORKERS:
        packets = packetize(lanes[worker])
        worker_loads[worker] = len(lanes[worker])
        for index, packet in enumerate(packets, start=1):
            packet_id = f"{worker[-1]}-{index:03d}"
            packet_path = output_root / "blind" / worker / "packets" / f"packet_{packet_id}.jsonl"
            write_jsonl_new(packet_path, packet)
            packet_ledger.append({
                "packet_id": packet_id,
                "worker": worker,
                "articles": len(packet),
                "packet_path": str(packet_path),
                "packet_sha256": sha256_path(packet_path),
                "output_path": str(output_root / "reviews" / worker / f"packet_{packet_id}.jsonl"),
            })

    ledger_path = output_root / "blind" / "PACKET_LEDGER.jsonl"
    write_jsonl_new(ledger_path, packet_ledger)
    policy = {
        "objective": "Independently classify each article from only its title and supplied metadata, while discovering repeated title patterns.",
        "blindness": [
            "Gold labels, v59 labels, and FP/FN direction are intentionally absent.",
            "Do not open controller files, evaluation mismatch files, assignment authorities, prior labels, or other workers' reviews.",
        ],
        "eligible_policy": [
            "A new current issuer event with plausible prospective forecast relevance.",
            "Earnings-call transcripts and live-broadcast transcripts are eligible; an earnings recap is not a transcript.",
            "Explicit new issuer guidance/outlook is eligible, including when an earnings title independently states that new guidance.",
            "Clinical/regulatory issuer events and single-issuer clinical-conference previews are eligible.",
            "Material contracts, partnerships, orders, financing/capital return, listing changes, management/governance, M&A/asset sales, ownership, and product/operations/capacity events can be eligible when the title establishes a new material issuer action.",
            "Material 13D/13G or activist ownership can be eligible.",
        ],
        "ineligible_policy": [
            "Analyst rating/research and routine analyst forecast revisions.",
            "Earnings results, EPS/sales beats or misses, earnings summaries, and earnings analysis/recaps without explicit new issuer guidance.",
            "Price reaction and why-moving forms such as X trades higher/lower, stock jumps, shares are trading, why is X moving, what is going on, and here's why.",
            "Market/macro recaps, movers/roundups/reference lists, recommendation listicles, need-to-know articles, and publisher recurring formats.",
            "Opinion/quote/prediction, options whale activity, technical trading ideas, valuation comparisons, portfolio trades, and routine 13F/hedge-fund holdings.",
            "Previews/schedules/expectations other than the stated single-issuer clinical-conference exception.",
            "Reported-earlier/follow-up articles, trading-halt status templates, legal/regulatory actions, generic question/hypothesis titles, and multi-subject compound titles.",
            "Third-party report attribution is generally ineligible; use uncertain only for a genuinely material large-cap issuer event that the title independently establishes.",
        ],
        "required_output_fields": [
            "review_id", "label", "confidence", "policy", "title_pattern", "justification", "exception_flag"
        ],
        "allowed_labels": sorted(ALLOWED_LABELS),
        "allowed_confidence": sorted(ALLOWED_CONFIDENCE),
        "field_rules": {
            "policy": "A stable snake_case policy family, not free-form prose.",
            "title_pattern": "A reusable normalized title template in concise plain text; use no_pattern only when none exists.",
            "justification": "One concise sentence grounded only in title and metadata.",
            "exception_flag": "true when the row is an exception to its dominant title pattern or needs full-text confirmation.",
        },
    }
    policy_path = output_root / "blind" / "REVIEW_POLICY.json"
    write_json_new(policy_path, policy)
    manifest = {
        "audit_version": AUDIT_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "prepared",
        "source_authority": {
            "evaluation_root": str(evaluation_root),
            "report_sha256": sha256_path(report_path),
            "mismatches_sha256": sha256_path(mismatch_path),
            "assignments_path": str(assignments_path),
            "assignments_sha256": sha256_path(assignments_path),
        },
        "population": {
            "training_fp": EXPECTED_TRAINING["fp"],
            "training_fn": EXPECTED_TRAINING["fn"],
            "training_total": sum(EXPECTED_TRAINING.values()),
            "excluded_holdout_fp": EXPECTED_HOLDOUT["fp"],
            "excluded_holdout_fn": EXPECTED_HOLDOUT["fn"],
            "excluded_holdout_total": sum(EXPECTED_HOLDOUT.values()),
        },
        "worker_loads": worker_loads,
        "packets": len(packet_ledger),
        "packet_limit": PACKET_LIMIT,
        "controller_sha256": sha256_path(controller_path),
        "packet_ledger_sha256": sha256_path(ledger_path),
        "review_policy_sha256": sha256_path(policy_path),
        "holdout_included_in_packets": False,
        "gold_or_prediction_in_blind_packets": False,
    }
    write_json_new(output_root / "MANIFEST.json", manifest)
    return manifest


def validate_reviews(*, output_root: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    ledger = list(iter_jsonl(output_root / "blind" / "PACKET_LEDGER.jsonl"))
    expected_ids: set[str] = set()
    reviewed_ids: set[str] = set()
    labels: Counter[str] = Counter()
    patterns: Counter[str] = Counter()
    rows = 0
    for packet in ledger:
        source = Path(str(packet["packet_path"]))
        if sha256_path(source) != str(packet["packet_sha256"]):
            raise ValueError(f"blind packet hash changed: {packet['packet_id']}")
        source_rows = list(iter_jsonl(source))
        for row in source_rows:
            if set(row) != BLIND_FIELDS:
                raise ValueError(f"blind packet schema drift: {packet['packet_id']}")
        packet_ids = {str(row["review_id"]) for row in source_rows}
        if len(packet_ids) != int(packet["articles"]):
            raise ValueError(f"blind packet membership changed: {packet['packet_id']}")
        if expected_ids & packet_ids:
            raise ValueError(f"duplicate blind membership: {packet['packet_id']}")
        expected_ids |= packet_ids
        output_path = Path(str(packet["output_path"]))
        if not output_path.exists():
            continue
        output_rows = list(iter_jsonl(output_path))
        output_ids = {str(row.get("review_id") or "") for row in output_rows}
        if len(output_ids) != len(output_rows) or output_ids != packet_ids:
            raise ValueError(f"review membership mismatch: {packet['packet_id']}")
        for row in output_rows:
            validate_review_row(row, context=str(packet["packet_id"]))
            label = str(row["label"])
            labels[label] += 1
            patterns[str(row["title_pattern"])] += 1
        reviewed_ids |= output_ids
        rows += len(output_rows)
    result = {
        "expected_articles": len(expected_ids),
        "reviewed_articles": rows,
        "remaining_articles": len(expected_ids - reviewed_ids),
        "completed_packets": sum(Path(str(packet["output_path"])).exists() for packet in ledger),
        "total_packets": len(ledger),
        "labels": dict(labels),
        "unique_title_patterns": len(patterns),
        "blind_schema_fields": sorted(BLIND_FIELDS),
        "controller_membership_matches_blind": expected_ids == set(load_controller(output_root)),
    }
    if not result["controller_membership_matches_blind"]:
        raise ValueError("controller/blind membership mismatch")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def load_primary_reviews(output_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    ledger = list(iter_jsonl(output_root / "blind" / "PACKET_LEDGER.jsonl"))
    reviews: dict[str, dict[str, Any]] = {}
    owners: dict[str, str] = {}
    for packet in ledger:
        output_path = Path(str(packet["output_path"]))
        if not output_path.exists():
            raise FileNotFoundError(f"primary review is incomplete: {output_path}")
        expected = {str(row["review_id"]) for row in iter_jsonl(Path(str(packet["packet_path"])))}
        actual_rows = list(iter_jsonl(output_path))
        actual = {str(row.get("review_id") or "") for row in actual_rows}
        if actual != expected or len(actual) != len(actual_rows):
            raise ValueError(f"primary review membership mismatch: {packet['packet_id']}")
        for row in actual_rows:
            review_id = str(row["review_id"])
            if review_id in reviews:
                raise ValueError(f"duplicate primary review: {review_id}")
            reviews[review_id] = row
            owners[review_id] = str(packet["worker"])
    return reviews, owners


def primary_review_authority(output_root: Path) -> dict[str, Any]:
    ledger = list(iter_jsonl(output_root / "blind" / "PACKET_LEDGER.jsonl"))
    files = []
    for packet in ledger:
        output_path = Path(str(packet["output_path"]))
        if not output_path.exists():
            raise FileNotFoundError(f"primary review is incomplete: {output_path}")
        files.append({
            "packet_id": str(packet["packet_id"]),
            "output_path": str(output_path),
            "sha256": sha256_path(output_path),
        })
    files.sort(key=lambda row: row["packet_id"])
    return {
        "files": files,
        "aggregate_sha256": digest(canonical_json(files)),
    }


def load_controller(output_root: Path) -> dict[str, dict[str, Any]]:
    rows = {
        str(row["review_id"]): row
        for row in iter_jsonl(output_root / "controller" / "CONTROLLER.jsonl")
    }
    if len(rows) != sum(EXPECTED_TRAINING.values()):
        raise ValueError(f"controller population changed: {len(rows):,}")
    return rows


def blind_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "review_id": row["review_id"],
        "title": row["title"],
        "published_at_utc": row["published_at_utc"],
        "author": row["author"],
        "channels": row["channels"],
        "provider_tags": row["provider_tags"],
        "tickers": row["tickers"],
        "ticker_count": row["ticker_count"],
        "synthesis_path": row["synthesis_path"],
    }


def prepare_second_review(*, output_root: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    second_root = output_root / "second_review"
    if second_root.exists():
        raise FileExistsError(f"refusing to overwrite second review: {second_root}")
    validate_reviews(output_root=output_root)
    primary, owners = load_primary_reviews(output_root)
    primary_authority = primary_review_authority(output_root)
    controller = load_controller(output_root)
    if set(primary) != set(controller):
        raise ValueError("primary/controller membership mismatch")

    selected: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for review_id, review in primary.items():
        source = controller[review_id]
        reasons: list[str] = []
        if str(review["label"]) == "uncertain":
            reasons.append("primary_uncertain")
        elif str(review["label"]) != str(source["gold_label"]):
            reasons.append("proposed_gold_correction")
        if bool(review["exception_flag"]):
            reasons.append("pattern_exception")
        if source.get("mixed_patterns"):
            reasons.append("preexisting_mixed_pattern")
        # Deterministic 5% QA sample of rows that otherwise confirm gold.
        if not reasons and int(digest(f"{AUDIT_VERSION}|qa|{review_id}")[:8], 16) % 20 == 0:
            reasons.append("gold_confirmation_qa_sample")
        if not reasons:
            continue
        for reason in reasons:
            reason_counts[reason] += 1
        selected.append({
            **blind_projection(source),
            "controller_selection_reasons": reasons,
            "primary_worker": owners[review_id],
        })

    selected.sort(key=lambda row: digest(f"{AUDIT_VERSION}|second-order|{row['review_id']}"))
    lane_rows: dict[str, list[dict[str, Any]]] = {worker: [] for worker in WORKERS}
    loads: Counter[str] = Counter()
    for row in selected:
        eligible_workers = [worker for worker in WORKERS if worker != row["primary_worker"]]
        reviewer = min(eligible_workers, key=lambda worker: (loads[worker], worker))
        loads[reviewer] += 1
        public_row = dict(row)
        public_row.pop("controller_selection_reasons")
        public_row.pop("primary_worker")
        lane_rows[reviewer].append(public_row)

    ledger: list[dict[str, Any]] = []
    for worker in WORKERS:
        for index, packet in enumerate(packetize(lane_rows[worker]), start=1):
            packet_id = f"S{worker[-1]}-{index:03d}"
            packet_path = second_root / "blind" / worker / "packets" / f"packet_{packet_id}.jsonl"
            write_jsonl_new(packet_path, packet)
            ledger.append({
                "packet_id": packet_id,
                "worker": worker,
                "articles": len(packet),
                "packet_path": str(packet_path),
                "packet_sha256": sha256_path(packet_path),
                "output_path": str(second_root / "reviews" / worker / f"packet_{packet_id}.jsonl"),
            })
    ledger_path = second_root / "blind" / "PACKET_LEDGER.jsonl"
    write_jsonl_new(ledger_path, ledger)
    selection_path = second_root / "controller" / "SELECTION.jsonl"
    write_jsonl_new(selection_path, selected)
    primary_authority_path = second_root / "controller" / "PRIMARY_REVIEW_AUTHORITY.json"
    write_json_new(primary_authority_path, primary_authority)
    shutil.copyfile(output_root / "blind" / "REVIEW_POLICY.json", second_root / "blind" / "REVIEW_POLICY.json")
    manifest = {
        "audit_version": f"{AUDIT_VERSION}:second_review_v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "selected_articles": len(selected),
        "selection_reasons": dict(reason_counts),
        "worker_loads": dict(loads),
        "packets": len(ledger),
        "reviewer_differs_from_primary": True,
        "gold_or_prediction_in_blind_packets": False,
        "selection_sha256": sha256_path(selection_path),
        "packet_ledger_sha256": sha256_path(ledger_path),
        "primary_review_aggregate_sha256": primary_authority["aggregate_sha256"],
        "primary_review_authority_sha256": sha256_path(primary_authority_path),
    }
    write_json_new(second_root / "MANIFEST.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def load_second_reviews(output_root: Path) -> dict[str, dict[str, Any]]:
    second_root = output_root / "second_review"
    ledger = list(iter_jsonl(second_root / "blind" / "PACKET_LEDGER.jsonl"))
    result: dict[str, dict[str, Any]] = {}
    for packet in ledger:
        source_path = Path(str(packet["packet_path"]))
        if sha256_path(source_path) != str(packet["packet_sha256"]):
            raise ValueError(f"second-review packet hash changed: {packet['packet_id']}")
        source_rows = list(iter_jsonl(source_path))
        for row in source_rows:
            if set(row) != BLIND_FIELDS:
                raise ValueError(f"second-review blind schema drift: {packet['packet_id']}")
        expected = {str(row["review_id"]) for row in source_rows}
        output_path = Path(str(packet["output_path"]))
        if not output_path.exists():
            raise FileNotFoundError(f"second review is incomplete: {output_path}")
        actual_rows = list(iter_jsonl(output_path))
        actual = {str(row.get("review_id") or "") for row in actual_rows}
        if actual != expected or len(actual) != len(actual_rows):
            raise ValueError(f"second review membership mismatch: {packet['packet_id']}")
        for row in actual_rows:
            validate_review_row(row, context=str(packet["packet_id"]))
            review_id = str(row["review_id"])
            if review_id in result:
                raise ValueError(f"duplicate second review: {review_id}")
            result[review_id] = row
    return result


def validate_second_reviews(*, output_root: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    selection_rows = list(iter_jsonl(
        output_root / "second_review" / "controller" / "SELECTION.jsonl"
    ))
    selection = {str(row["review_id"]) for row in selection_rows}
    reviews = load_second_reviews(output_root)
    if set(reviews) != selection:
        raise ValueError("second review does not cover its selection")
    assigned_workers: dict[str, str] = {}
    for packet in iter_jsonl(output_root / "second_review" / "blind" / "PACKET_LEDGER.jsonl"):
        for row in iter_jsonl(Path(str(packet["packet_path"]))):
            assigned_workers[str(row["review_id"])] = str(packet["worker"])
    for row in selection_rows:
        review_id = str(row["review_id"])
        if assigned_workers[review_id] == str(row["primary_worker"]):
            raise ValueError(f"same worker assigned primary and second review: {review_id}")
    labels = Counter(str(row["label"]) for row in reviews.values())
    result = {
        "selected_articles": len(selection),
        "reviewed_articles": len(reviews),
        "labels": dict(labels),
        "all_second_reviewers_differ_from_primary": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def slug(value: str, *, limit: int = 80) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "unclassified"
    return clean[:limit].rstrip("_")


def markdown_escape(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def metadata_cell(row: Mapping[str, Any]) -> str:
    parts = [
        f"source_id={row['source_id']}",
        f"date={str(row['published_at_utc'])[:10]}",
        f"author={row.get('author') or '-'}",
        f"channels={','.join(row.get('channels') or ()) or '-'}",
        f"tags={','.join(row.get('provider_tags') or ()) or '-'}",
        f"tickers={','.join(row.get('tickers') or ()) or '-'}",
        f"ticker_count={row.get('ticker_count', 0)}",
        f"path={row.get('synthesis_path') or '-'}",
    ]
    return " | ".join(parts)


def consolidate(*, output_root: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    final_root = output_root / "consolidated"
    if final_root.exists():
        raise FileExistsError(f"refusing to overwrite consolidated output: {final_root}")
    expected_primary_authority = json.loads(
        (output_root / "second_review" / "controller" / "PRIMARY_REVIEW_AUTHORITY.json").read_text(
            encoding="utf-8"
        )
    )
    current_primary_authority = primary_review_authority(output_root)
    if current_primary_authority["aggregate_sha256"] != expected_primary_authority["aggregate_sha256"]:
        raise ValueError("primary reviews changed after second-review selection")
    primary, owners = load_primary_reviews(output_root)
    secondary = load_second_reviews(output_root)
    controller = load_controller(output_root)
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    pattern_counts: dict[tuple[str, str], Counter[str]] = {}
    for review_id, source in controller.items():
        first = primary[review_id]
        second = secondary.get(review_id)
        first_label = str(first["label"])
        second_label = str(second["label"]) if second else ""
        if second is None:
            consensus = first_label
            consensus_status = "single_review"
        elif first_label == second_label:
            consensus = first_label
            consensus_status = "double_agreement"
        else:
            consensus = "uncertain"
            consensus_status = "reviewer_disagreement"
        policy = str(first["policy"])
        title_pattern = str(first["title_pattern"])
        if second and first_label == second_label and str(second["policy"]) != policy:
            policy = f"{policy}__or__{second['policy']}"
        row = {
            **source,
            "primary_worker": owners[review_id],
            "primary_label": first_label,
            "primary_confidence": str(first["confidence"]),
            "primary_policy": str(first["policy"]),
            "primary_title_pattern": title_pattern,
            "primary_justification": str(first["justification"]),
            "primary_exception_flag": bool(first["exception_flag"]),
            "secondary_label": second_label or None,
            "secondary_confidence": str(second["confidence"]) if second else None,
            "secondary_policy": str(second["policy"]) if second else None,
            "secondary_title_pattern": str(second["title_pattern"]) if second else None,
            "secondary_justification": str(second["justification"]) if second else None,
            "secondary_exception_flag": bool(second["exception_flag"]) if second else None,
            "consensus_label": consensus,
            "consensus_status": consensus_status,
            "audit_policy": policy,
            "audit_title_pattern": title_pattern,
            "agrees_with_gold": consensus == str(source["gold_label"]),
            "agrees_with_v59": consensus == str(source["v59_label"]),
            "proposed_gold_correction": consensus in {"eligible", "ineligible"} and consensus != str(source["gold_label"]),
        }
        rows.append(row)
        counts[f"consensus:{consensus}"] += 1
        counts[f"status:{consensus_status}"] += 1
        counts[f"cell:{source['confusion_cell']}|consensus:{consensus}"] += 1
        counts["proposed_gold_corrections" if row["proposed_gold_correction"] else "not_proposed_gold_corrections"] += 1
        key = (policy, title_pattern)
        bucket = pattern_counts.setdefault(key, Counter())
        bucket["total"] += 1
        bucket[f"cell:{source['confusion_cell']}"] += 1
        bucket[f"gold:{source['gold_label']}"] += 1
        bucket[f"v59:{source['v59_label']}"] += 1
        bucket[f"consensus:{consensus}"] += 1
        bucket[f"cell:{source['confusion_cell']}|consensus:{consensus}"] += 1
        bucket[f"gold_agreement:{row['agrees_with_gold']}"] += 1
        bucket[f"v59_agreement:{row['agrees_with_v59']}"] += 1
        bucket["proposed_gold_corrections" if row["proposed_gold_correction"] else "not_proposed_gold_corrections"] += 1

    rows.sort(key=lambda row: str(row["source_id"]))
    final_root.mkdir(parents=True)
    consolidated_path = final_root / "CONSOLIDATED_REVIEWS.jsonl"
    write_jsonl_new(consolidated_path, rows)
    pattern_rows = []
    examples: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        key = (str(row["audit_policy"]), str(row["audit_title_pattern"]))
        examples.setdefault(key, [])
        if len(examples[key]) < 5:
            examples[key].append(str(row["title"]))
    for (policy, title_pattern), bucket in sorted(
        pattern_counts.items(), key=lambda item: (-item[1]["total"], item[0])
    ):
        pattern_rows.append({
            "policy": policy,
            "title_pattern": title_pattern,
            **dict(bucket),
            "examples": examples[(policy, title_pattern)],
        })
    pattern_path = final_root / "TITLE_PATTERN_DICTIONARY.jsonl"
    write_jsonl_new(pattern_path, pattern_rows)

    audit_root = final_root / "audit_tables"
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row["confusion_cell"]), str(row["gold_label"]), str(row["consensus_label"]),
            str(row["audit_policy"]), str(row["audit_title_pattern"]),
        )
        grouped.setdefault(key, []).append(row)
    markdown_files = 0
    audit_index_rows: list[dict[str, Any]] = []
    for key, group in sorted(grouped.items()):
        cell, gold, consensus, policy, title_pattern = key
        group.sort(key=lambda row: (str(row["published_at_utc"]), str(row["source_id"])))
        for part, offset in enumerate(range(0, len(group), PACKET_LIMIT), start=1):
            chunk = group[offset:offset + PACKET_LIMIT]
            policy_dir = f"p_{slug(policy, limit=24)}_{digest(policy)[:8]}"
            directory = audit_root / cell / f"g_{gold[0]}" / f"a_{consensus[0]}" / policy_dir
            directory.mkdir(parents=True, exist_ok=True)
            group_hash = digest("|".join(key))[:10]
            path = directory / f"t_{slug(title_pattern, limit=32)}_{group_hash}_{part:03d}.md"
            if path.exists():
                raise FileExistsError(f"audit table collision: {path}")
            lines = [
                f"# v59 training mismatch audit: {title_pattern}",
                "",
                f"- Gold label: `{gold}`",
                f"- v59 mismatch direction: `{cell}`",
                f"- Agent consensus: `{consensus}`",
                f"- Policy: `{policy}`",
                f"- Title pattern: `{title_pattern}`",
                f"- Rows: `{len(chunk)}`",
                "",
                "| Metadata | Title |",
                "|---|---|",
            ]
            lines.extend(
                f"| {markdown_escape(metadata_cell(row))} | {markdown_escape(row['title'])} |"
                for row in chunk
            )
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            markdown_files += 1
            audit_index_rows.append({
                "path": path.relative_to(final_root).as_posix(),
                "rows": len(chunk),
                "cell": cell,
                "gold": gold,
                "consensus": consensus,
                "policy": policy,
                "title_pattern": title_pattern,
            })

    report = {
        "audit_version": AUDIT_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "articles": len(rows),
        "counts": dict(counts),
        "unique_policy_pattern_pairs": len(pattern_rows),
        "markdown_files": markdown_files,
        "holdout_articles": 0,
        "gold_changed": False,
        "news_synthesis_changed": False,
        "consolidated_reviews_sha256": sha256_path(consolidated_path),
        "title_pattern_dictionary_sha256": sha256_path(pattern_path),
    }
    write_json_new(final_root / "REPORT.json", report)
    top_patterns = pattern_rows[:100]
    report_lines = [
        "# v59 training mismatch blind audit",
        "",
        f"- Training mismatches audited: `{len(rows):,}`",
        "- Holdout mismatches included: `0` (all 626 remain separate and untouched)",
        f"- Proposed gold corrections: `{counts['proposed_gold_corrections']:,}`",
        f"- Consensus eligible: `{counts['consensus:eligible']:,}`",
        f"- Consensus ineligible: `{counts['consensus:ineligible']:,}`",
        f"- Consensus uncertain or reviewer disagreement: `{counts['consensus:uncertain']:,}`",
        f"- Double-reviewed agreement rows: `{counts['status:double_agreement']:,}`",
        f"- Reviewer disagreements: `{counts['status:reviewer_disagreement']:,}`",
        f"- Human audit Markdown files: `{markdown_files:,}`",
        "- Gold labels changed by this audit: `no`",
        "- News Synthesis changed by this audit: `no`",
        "",
        "## Most prevalent discovered policy and title patterns",
        "",
        "| Rank | Policy | Title pattern | Total | Gold eligible | Gold ineligible | Audit eligible | Audit ineligible | Audit uncertain | Proposed gold corrections |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(top_patterns, start=1):
        report_lines.append(
            "| " + " | ".join([
                str(rank), markdown_escape(row["policy"]), markdown_escape(row["title_pattern"]),
                str(row.get("total", 0)), str(row.get("gold:eligible", 0)),
                str(row.get("gold:ineligible", 0)), str(row.get("consensus:eligible", 0)),
                str(row.get("consensus:ineligible", 0)), str(row.get("consensus:uncertain", 0)),
                str(row.get("proposed_gold_corrections", 0)),
            ]) + " |"
        )
    ranking_sections = (
        ("Patterns with the most proposed gold corrections", "proposed_gold_corrections"),
        ("Patterns where the audit most often confirms gold and rejects v59", "gold_agreement:True"),
        ("Patterns with the most unresolved rows", "consensus:uncertain"),
    )
    for heading, metric in ranking_sections:
        ranked = sorted(pattern_rows, key=lambda row: (-int(row.get(metric, 0)), -int(row["total"]), row["policy"]))
        report_lines.extend([
            "",
            f"## {heading}",
            "",
            "| Rank | Policy | Title pattern | Rows |",
            "|---:|---|---|---:|",
        ])
        rank = 0
        for row in ranked:
            value = int(row.get(metric, 0))
            if value == 0 or rank >= 50:
                break
            rank += 1
            report_lines.append(
                f"| {rank} | {markdown_escape(row['policy'])} | "
                f"{markdown_escape(row['title_pattern'])} | {value} |"
            )
    (final_root / "REPORT.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    index_lines = [
        "# Human audit table index",
        "",
        "Every linked file contains no more than 100 rows and exactly two table columns: `Metadata` and `Title`. Each file is homogeneous by gold label, mismatch direction, agent consensus, policy, and title pattern.",
        "",
        "| File | Rows | Direction | Gold | Audit | Policy | Title pattern |",
        "|---|---:|---|---|---|---|---|",
    ]
    for item in audit_index_rows:
        index_lines.append(
            "| " + " | ".join([
                f"[{markdown_escape(Path(item['path']).name)}]({item['path']})",
                str(item["rows"]), str(item["cell"]), str(item["gold"]),
                str(item["consensus"]), markdown_escape(item["policy"]),
                markdown_escape(item["title_pattern"]),
            ]) + " |"
        )
    (final_root / "AUDIT_INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def validate_consolidated(*, output_root: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    final_root = output_root / "consolidated"
    report = json.loads((final_root / "REPORT.json").read_text(encoding="utf-8"))
    consolidated_path = final_root / "CONSOLIDATED_REVIEWS.jsonl"
    pattern_path = final_root / "TITLE_PATTERN_DICTIONARY.jsonl"
    if sha256_path(consolidated_path) != str(report["consolidated_reviews_sha256"]):
        raise ValueError("consolidated review hash mismatch")
    if sha256_path(pattern_path) != str(report["title_pattern_dictionary_sha256"]):
        raise ValueError("title pattern dictionary hash mismatch")
    rows = list(iter_jsonl(consolidated_path))
    source_ids = [str(row["source_id"]) for row in rows]
    if len(rows) != sum(EXPECTED_TRAINING.values()) or len(set(source_ids)) != len(rows):
        raise ValueError("consolidated population is incomplete or duplicated")
    if any(str(row["population_split"]) != "training_development" for row in rows):
        raise ValueError("holdout or unknown split leaked into consolidated reviews")
    patterns = list(iter_jsonl(pattern_path))
    if sum(int(row["total"]) for row in patterns) != len(rows):
        raise ValueError("title pattern dictionary does not reconcile to the population")

    markdown_paths = sorted((final_root / "audit_tables").rglob("*.md"))
    if len(markdown_paths) != int(report["markdown_files"]):
        raise ValueError("Markdown audit file count mismatch")
    markdown_ids: list[str] = []
    for path in markdown_paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        if "| Metadata | Title |" not in lines or "|---|---|" not in lines:
            raise ValueError(f"invalid Markdown table header: {path}")
        data_lines = [line for line in lines if line.startswith("| source_id=")]
        if not 1 <= len(data_lines) <= PACKET_LIMIT:
            raise ValueError(f"invalid Markdown table row count: {path} ({len(data_lines)})")
        declared = next((line for line in lines if line.startswith("- Rows: `")), "")
        if declared != f"- Rows: `{len(data_lines)}`":
            raise ValueError(f"Markdown declared row count mismatch: {path}")
        for line in data_lines:
            match = re.match(r"^\| source_id=([^ ]+) \\\|", line)
            if not match:
                raise ValueError(f"cannot parse Markdown source_id: {path}")
            markdown_ids.append(match.group(1))
    if len(markdown_ids) != len(rows) or set(markdown_ids) != set(source_ids):
        raise ValueError("Markdown tables do not cover each consolidated article exactly once")

    index_text = (final_root / "AUDIT_INDEX.md").read_text(encoding="utf-8")
    if index_text.count("](") != len(markdown_paths):
        raise ValueError("audit index link count mismatch")
    result = {
        "status": "passed",
        "articles": len(rows),
        "unique_source_ids": len(set(source_ids)),
        "holdout_articles": 0,
        "pattern_rows": len(patterns),
        "pattern_article_sum": sum(int(row["total"]) for row in patterns),
        "markdown_files": len(markdown_paths),
        "markdown_rows": len(markdown_ids),
        "markdown_max_rows_per_file": PACKET_LIMIT,
        "table_columns": ["Metadata", "Title"],
        "all_second_reviewers_differ_from_primary": True,
        "primary_review_aggregate_sha256": primary_review_authority(output_root)["aggregate_sha256"],
        "consolidated_reviews_sha256": sha256_path(consolidated_path),
        "title_pattern_dictionary_sha256": sha256_path(pattern_path),
    }
    validation_path = final_root / "VALIDATION.json"
    if validation_path.exists():
        raise FileExistsError(f"refusing to overwrite validation: {validation_path}")
    write_json_new(validation_path, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare and validate the blind v59 training mismatch audit.")
    parser.add_argument(
        "command",
        choices=(
            "prepare", "validate-reviews", "prepare-second-review",
            "validate-second-review", "consolidate",
            "validate-consolidated",
        ),
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--evaluation-root", type=Path, default=EVALUATION_ROOT)
    parser.add_argument("--assignments", type=Path, default=ASSIGNMENTS)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare(
            evaluation_root=args.evaluation_root,
            assignments_path=args.assignments,
            output_root=args.output_root,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "validate-reviews":
        validate_reviews(output_root=args.output_root)
    elif args.command == "prepare-second-review":
        prepare_second_review(output_root=args.output_root)
    elif args.command == "validate-second-review":
        validate_second_reviews(output_root=args.output_root)
    elif args.command == "validate-consolidated":
        validate_consolidated(output_root=args.output_root)
    else:
        consolidate(output_root=args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
