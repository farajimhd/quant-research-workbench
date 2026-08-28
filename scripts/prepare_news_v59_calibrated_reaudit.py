from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


SOURCE_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\news_v59_training_mismatch_blind_audit_v1"
)
DEFAULT_OUTPUT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\news_v59_training_mismatch_calibrated_reaudit_v2"
)
AUDIT_VERSION = "news_v59_training_mismatch_calibrated_reaudit_v2"
WORKERS = ("worker_1", "worker_2", "worker_3")
PACKET_LIMIT = 100
EXPECTED_ROWS = 43_369
EXPECTED_CELLS = {"fp": 6_443, "fn": 36_926}
BLIND_FIELDS = {
    "review_id",
    "title",
    "published_at_utc",
    "author",
    "channels",
    "provider_tags",
    "tickers",
    "ticker_count",
    "synthesis_path",
}
REVIEW_FIELDS = {
    "review_id",
    "label",
    "policy_id",
    "qualifying_event",
    "title_evidence",
    "metadata_evidence",
    "precedence",
    "confidence",
    "needs_article_body",
    "discovered_pattern",
}


# Human-selected real rows covering positive, negative, and precedence boundaries.
# The key remains controller-only; agents receive only the blind projections.
CALIBRATION_CASES = {
    # Eligible.
    "0e799d645e01ba24db9ed36313376357": ("eligible", "earnings_call_transcript"),
    "0e04e6884c0c09c77248a4e5e3146347": ("eligible", "issuer_guidance"),
    "0877a5ad82f436d37471d62bf8120c43": ("eligible", "issuer_guidance"),
    "0128e276db34ba0de1956f5696b3c741": ("eligible", "clinical_regulatory_event"),
    "01adcbd68b4d0c94cecd4c8fe44bae72": ("eligible", "clinical_regulatory_event"),
    "02a1ff22c2c02ac8bc9af52f2ab088af": ("eligible", "clinical_conference_preview"),
    "0449b2627721e2c9c11f3de6f442465b": ("eligible", "definitive_merger_acquisition"),
    "053646e6d198bae545c0dbc52ecc6447": ("eligible", "definitive_merger_acquisition"),
    "006e2dea7d58ede2298bec777c3303be": ("eligible", "material_contract_order"),
    "04f4f86009d09741bb25af459c728bd7": ("eligible", "material_contract_order"),
    "01692999786bdbb03589b92ce0091b4a": ("eligible", "listing_corporate_action"),
    "1ff071b069548f4ae5433d8937cf4f9f": ("eligible", "material_ownership"),
    "50c8046f931ddd3ada2ee8cce0d6de8c": ("eligible", "material_ownership"),
    "014b4b5aca588e2f62072f47be0dcbf0": ("eligible", "product_operations_event"),
    "48ada9a8d7ea96e31c8d7dfb265a502c": ("eligible", "product_operations_event"),
    "4c3941a2f962c8add4cf7c35e4aaf115": ("eligible", "completed_or_priced_financing"),
    "043292ec08daba5cad7262e98b6630de": ("eligible", "new_or_increased_capital_return"),
    "03b1a873ff55cc9354c318d109d77ff3": ("eligible", "senior_management_change"),
    # Ineligible.
    "00644b88d9793a4968fded95d9396ae2": ("ineligible", "multi_subject_roundup"),
    "0740189ae5b945d98434a61405199bd7": ("ineligible", "multi_subject_roundup"),
    "c17bf8bd23ffcc43a49d5f50fb2fc53f": ("ineligible", "price_reaction_wrapper"),
    "0fc54c317cf9770b2e6f228231aa83fc": ("ineligible", "earnings_results_recap"),
    "140a747cfabe867a34edd505a5ec1d95": ("ineligible", "analyst_research_forecast"),
    "ff8d76f5608610cc8c49704c87d5aa78": ("ineligible", "analyst_research_forecast"),
    "05089df91d0b6542548e306011cf5d57": ("ineligible", "reported_earlier_followup"),
    "021140db5ffac9471171025657d81bb2": ("ineligible", "atm_shelf_registration"),
    "d313728b4bef094772cde652b220b411": ("ineligible", "selling_holder_no_issuer_proceeds"),
    "0c4566807b7ec5baca6021f3cccc9e92": ("ineligible", "ipo_opening_price_reaction"),
    "0aae9bc7c1be84bc9771489b31ddf5f4": ("ineligible", "routine_insider_trade"),
    "a9283467fee3b8d0603bd5c92676dde1": ("ineligible", "routine_unchanged_dividend"),
    "016033b08adf0b3ce847f85ec0bd7f27": ("ineligible", "generic_partnership_marketing"),
    "5e1b5656a59f756eb377e9fb686c7cde": ("ineligible", "generic_partnership_marketing"),
    "01e0e1a4a1143f2c2b588be56a419abd": ("ineligible", "nondefinitive_merger_interest"),
    "00ce2b05598bd2dfcf06797c9b22d549": ("ineligible", "nondefinitive_partnership_interest"),
    "03f08c6de3bc90bbdbb63f8539a4cd71": ("ineligible", "legal_regulatory_action"),
    "665d54cbf0a2dcb099efea6b4099486f": ("ineligible", "generic_partnership_marketing"),
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


def blind_projection(row: Mapping[str, Any], review_id: str) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "title": row["title"],
        "published_at_utc": row["published_at_utc"],
        "author": row["author"],
        "channels": row["channels"],
        "provider_tags": row["provider_tags"],
        "tickers": row["tickers"],
        "ticker_count": row["ticker_count"],
        "synthesis_path": row["synthesis_path"],
    }


def policy_contract() -> dict[str, Any]:
    return {
        "objective": "Assign forecast eligibility from title and supplied metadata under the operator-approved precedence rules.",
        "blindness": [
            "Never open controller, key, prior-review, gold, v59, mismatch-direction, or other-worker files.",
            "Do not infer a label from packet location, review ID, ticker identity, or prior model behavior.",
        ],
        "core_definition": {
            "eligible": "A current, concrete issuer event that creates genuinely new forward-looking issuer state.",
            "ineligible": "Editorial analysis, reaction, recap, routine disclosure, nondefinitive interest, or a mechanically repeated format without a qualifying new issuer event.",
            "uncertain": "Use only when title plus metadata cannot establish whether a mixed category crosses the material-event boundary; set needs_article_body=true.",
        },
        "precedence": [
            "Complete earnings-call transcript and live-broadcast transcript are eligible.",
            "Explicit new issuer guidance is eligible, but analyst forecasts are ineligible.",
            "A price-reaction or why-moving wrapper is ineligible even when it mentions the underlying event; label the article format presented by this title.",
            "Clinical/regulatory milestones and single-issuer clinical-conference previews are eligible.",
            "Earnings results, beats, misses, summaries, recaps, and preliminary historical results are ineligible unless the title is principally an explicit new issuer-guidance announcement without a reaction wrapper.",
            "A priced/completed issuer financing is eligible. ATM/shelf/prospectus capacity, continuous/equity-line facilities, selling-holder offerings with no issuer proceeds, and IPO opening/indication stories are ineligible.",
            "A new/increased dividend, special dividend, or newly authorized buyback is eligible. An unchanged routine dividend is ineligible.",
            "Definitive M&A or completed asset sale is eligible. Rumor, consideration, exploration, may/mulls/seeks, and nonbinding LOI are ineligible.",
            "Material 13D/13G or activist ownership is eligible. Routine 13F, portfolio, insider, and Form 4 trades are ineligible.",
            "An explicit Schedule 13G ownership percentage is eligible regardless of whether the filer is described as institutional. An explicitly activist investor building a large stake is eligible even when attributed to WSJ, Bloomberg, Reuters, or FT; these are approved exceptions to the general third-party rule.",
            "Quantified/material contract, order, exclusive license, or economically substantive partnership is eligible. Generic collaboration, marketing, sponsorship, reseller, charity, or brand partnership without material terms is ineligible.",
            "Develop, deliver, integrate, or collaborate language alone does not make a partnership material. Without value, duration, exclusivity, committed order, named customer deployment, license economics, capacity, or revenue terms, classify the collaboration as ineligible.",
            "Actual product launch, operational deployment, capacity expansion, or completed material milestone can be eligible. Promotion, vision, investor-awareness campaign, scheduling, or a plan without committed action is ineligible.",
            "Actual listing/delisting, compliance restoration, or stock split is eligible. Trading-halt templates and IPO opening-price stories are ineligible.",
            "Senior issuer management changes can be eligible. Government appointments, advisory-board promotion, routine director trades, and commentary are ineligible.",
            "Legal/regulatory actions, reported-earlier follow-ups, multi-subject titles, market/macro recaps, analyst research, opinion, technical/options ideas, valuation comparisons, and recommendation/listicle formats are ineligible.",
            "Third-party attribution alone is not eligibility. If it reports a definitive material large-cap issuer event, use uncertain and request article body unless the title itself establishes the event conclusively.",
        ],
        "examples": [
            {"eligible": "Issuer raises FY2026 revenue guidance", "ineligible": "Analyst raises issuer FY2026 revenue forecast"},
            {"eligible": "Issuer prices $50M registered direct offering", "ineligible": "Issuer files $100M ATM shelf prospectus"},
            {"eligible": "Activist files Schedule 13D for 8.7% stake", "ineligible": "Fund reports routine quarterly 13F stake"},
            {"eligible": "Issuer wins five-year $200M supply contract", "ineligible": "Issuer partners on brand-awareness campaign; terms undisclosed"},
            {"eligible": "Issuer enters definitive agreement to acquire target", "ineligible": "Issuer signs nonbinding LOI or reportedly mulls acquisition"},
            {"eligible": "Transcript: Issuer Q2 earnings conference call", "ineligible": "Issuer earnings recap: what investors need to know"},
            {"eligible": "Issuer reports Phase 3 endpoint met", "ineligible": "Issuer stock jumps after Phase 3 update: here is why"},
            {"eligible": "Issuer executes reverse stock split", "ineligible": "IPO shares open for trade above issue price"},
        ],
        "required_output_fields": sorted(REVIEW_FIELDS),
        "allowed_labels": ["eligible", "ineligible", "uncertain"],
        "allowed_confidence": ["high", "medium", "low"],
        "field_rules": {
            "policy_id": "Use one stable snake_case family from the precedence rule applied.",
            "qualifying_event": "Concise issuer event, or none.",
            "title_evidence": "Shortest decisive title phrase.",
            "metadata_evidence": "Only supplied metadata; use none when it adds no decision evidence.",
            "precedence": "State which conflict rule won; use direct when no conflict exists.",
            "needs_article_body": "True only when title and metadata cannot resolve a material mixed category.",
            "discovered_pattern": "Reusable normalized title pattern, never issuer-specific prose.",
        },
    }


def packetize(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [rows[index:index + PACKET_LIMIT] for index in range(0, len(rows), PACKET_LIMIT)]


def prepare(source_root: Path, output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite {output_root}")
    source_path = source_root / "controller" / "CONTROLLER.jsonl"
    source_rows = list(iter_jsonl(source_path))
    if len(source_rows) != EXPECTED_ROWS:
        raise ValueError(f"source population changed: {len(source_rows):,}")
    cells = Counter(str(row["confusion_cell"]) for row in source_rows)
    if dict(cells) != EXPECTED_CELLS:
        raise ValueError(f"source confusion cells changed: {dict(cells)}")
    by_source = {str(row["source_id"]): row for row in source_rows}
    missing = sorted(set(CALIBRATION_CASES) - set(by_source))
    if missing:
        raise ValueError(f"calibration source rows missing: {missing}")

    output_root.mkdir(parents=True)
    controller_rows: list[dict[str, Any]] = []
    blind_rows: list[dict[str, Any]] = []
    for row in source_rows:
        source_id = str(row["source_id"])
        review_id = "R59" + digest(f"{AUDIT_VERSION}|{source_id}")[:22]
        controller_rows.append({**row, "reaudit_review_id": review_id})
        blind_rows.append(blind_projection(row, review_id))
    controller_rows.sort(key=lambda row: str(row["source_id"]))
    write_jsonl_new(output_root / "controller" / "CONTROLLER.jsonl", controller_rows)

    calibration_key = []
    calibration_blind = []
    for source_id, (label, policy_id) in CALIBRATION_CASES.items():
        row = by_source[source_id]
        review_id = "C59" + digest(f"{AUDIT_VERSION}|calibration|{source_id}")[:22]
        calibration_blind.append(blind_projection(row, review_id))
        calibration_key.append({
            "review_id": review_id,
            "source_id": source_id,
            "expected_label": label,
            "expected_policy_id": policy_id,
        })
    calibration_blind.sort(key=lambda row: digest(f"{AUDIT_VERSION}|cal-order|{row['review_id']}"))
    calibration_key.sort(key=lambda row: row["review_id"])
    write_jsonl_new(output_root / "calibration" / "CALIBRATION_PACKET.jsonl", calibration_blind)
    write_jsonl_new(output_root / "controller" / "CALIBRATION_KEY.jsonl", calibration_key)

    policy_path = output_root / "blind" / "REVIEW_POLICY.json"
    write_json_new(policy_path, policy_contract())
    packet_ledger = []
    pass_one_owner: dict[str, str] = {}
    for pass_number in (1, 2):
        ordered = sorted(
            blind_rows,
            key=lambda row: digest(f"{AUDIT_VERSION}|pass-{pass_number}|{row['review_id']}"),
        )
        lanes = {worker: [] for worker in WORKERS}
        loads: Counter[str] = Counter()
        for index, row in enumerate(ordered):
            if pass_number == 1:
                worker = WORKERS[index % len(WORKERS)]
                pass_one_owner[str(row["review_id"])] = worker
            else:
                eligible_workers = [
                    worker for worker in WORKERS
                    if worker != pass_one_owner[str(row["review_id"])]
                ]
                worker = min(eligible_workers, key=lambda value: (loads[value], value))
            lanes[worker].append(row)
            loads[worker] += 1
        for worker in WORKERS:
            for packet_number, packet in enumerate(packetize(lanes[worker]), start=1):
                packet_id = f"P{pass_number}-{worker[-1]}-{packet_number:03d}"
                packet_path = output_root / f"pass_{pass_number}" / "blind" / worker / f"{packet_id}.jsonl"
                output_path = output_root / f"pass_{pass_number}" / "reviews" / worker / f"{packet_id}.jsonl"
                write_jsonl_new(packet_path, packet)
                packet_ledger.append({
                    "pass": pass_number,
                    "packet_id": packet_id,
                    "worker": worker,
                    "articles": len(packet),
                    "packet_path": str(packet_path),
                    "packet_sha256": sha256_path(packet_path),
                    "output_path": str(output_path),
                })
    ledger_path = output_root / "blind" / "PACKET_LEDGER.jsonl"
    write_jsonl_new(ledger_path, packet_ledger)
    manifest = {
        "audit_version": AUDIT_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_controller": str(source_path),
        "source_controller_sha256": sha256_path(source_path),
        "training_rows": EXPECTED_ROWS,
        "training_cells": EXPECTED_CELLS,
        "excluded_holdout_rows": 626,
        "calibration_rows": len(CALIBRATION_CASES),
        "passes": 2,
        "packets": len(packet_ledger),
        "packet_ledger_path": str(ledger_path),
        "packet_ledger_sha256": sha256_path(ledger_path),
        "packet_limit": PACKET_LIMIT,
        "review_policy_sha256": sha256_path(policy_path),
        "gold_prediction_and_direction_hidden": True,
    }
    write_json_new(output_root / "MANIFEST.json", manifest)
    return manifest


def validate_review_row(row: Mapping[str, Any], context: str) -> None:
    if set(row) != REVIEW_FIELDS:
        raise ValueError(f"review schema drift in {context}: {sorted(row)}")
    if row["label"] not in {"eligible", "ineligible", "uncertain"}:
        raise ValueError(f"invalid label in {context}: {row['label']}")
    if row["confidence"] not in {"high", "medium", "low"}:
        raise ValueError(f"invalid confidence in {context}: {row['confidence']}")
    for field in REVIEW_FIELDS - {"needs_article_body"}:
        if not str(row[field]).strip():
            raise ValueError(f"blank {field} in {context}")
    if not isinstance(row["needs_article_body"], bool):
        raise ValueError(f"needs_article_body is not boolean in {context}")


def score_calibration(output_root: Path, calibration_round: int = 1) -> dict[str, Any]:
    key = {str(row["review_id"]): row for row in iter_jsonl(output_root / "controller" / "CALIBRATION_KEY.jsonl")}
    results = {}
    review_root = (
        output_root / "calibration" / "reviews"
        if calibration_round == 1
        else output_root / "calibration" / f"round_{calibration_round}" / "reviews"
    )
    for worker in WORKERS:
        path = review_root / f"{worker}.jsonl"
        rows = list(iter_jsonl(path))
        actual = {str(row["review_id"]): row for row in rows}
        if set(actual) != set(key) or len(rows) != len(key):
            raise ValueError(f"calibration membership mismatch for {worker}")
        for row in rows:
            validate_review_row(row, f"calibration/{worker}")
        correct = sum(actual[review_id]["label"] == expected["expected_label"] for review_id, expected in key.items())
        wrong = [
            {
                "review_id": review_id,
                "expected": expected["expected_label"],
                "actual": actual[review_id]["label"],
                "expected_policy_id": expected["expected_policy_id"],
                "actual_policy_id": actual[review_id]["policy_id"],
            }
            for review_id, expected in key.items()
            if actual[review_id]["label"] != expected["expected_label"]
        ]
        results[worker] = {
            "rows": len(key),
            "correct": correct,
            "accuracy": correct / len(key),
            "wrong": wrong,
            "passed": correct == len(key),
        }
    report = {
        "calibration_round": calibration_round,
        "workers": results,
        "all_passed": all(value["passed"] for value in results.values()),
    }
    report_path = (
        output_root / "calibration" / "CALIBRATION_REPORT.json"
        if calibration_round == 1
        else output_root / "calibration" / f"round_{calibration_round}" / "CALIBRATION_REPORT.json"
    )
    write_json_new(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def validate_passes(output_root: Path) -> dict[str, Any]:
    manifest = json.loads((output_root / "MANIFEST.json").read_text(encoding="utf-8"))
    ledger_path = Path(manifest.get(
        "packet_ledger_path", output_root / "blind" / "PACKET_LEDGER.jsonl"
    ))
    if manifest.get("packet_ledger_sha256") and sha256_path(ledger_path) != manifest["packet_ledger_sha256"]:
        raise ValueError("active packet ledger hash changed")
    ledger = list(iter_jsonl(ledger_path))
    summary: dict[str, Any] = {}
    pass_membership: dict[int, set[str]] = {1: set(), 2: set()}
    pass_labels: dict[int, Counter[str]] = {1: Counter(), 2: Counter()}
    complete = True
    for packet in ledger:
        source_path = Path(packet["packet_path"])
        if sha256_path(source_path) != packet["packet_sha256"]:
            raise ValueError(f"packet hash changed: {packet['packet_id']}")
        source_rows = list(iter_jsonl(source_path))
        expected = {str(row["review_id"]) for row in source_rows}
        output_path = Path(packet["output_path"])
        if not output_path.exists():
            complete = False
            continue
        output_rows = list(iter_jsonl(output_path))
        actual = {str(row["review_id"]) for row in output_rows}
        if actual != expected or len(actual) != len(output_rows):
            raise ValueError(f"packet membership mismatch: {packet['packet_id']}")
        for row in output_rows:
            validate_review_row(row, str(packet["packet_id"]))
            pass_labels[int(packet["pass"])][str(row["label"])] += 1
        if pass_membership[int(packet["pass"])] & actual:
            raise ValueError(f"duplicate pass membership: {packet['packet_id']}")
        pass_membership[int(packet["pass"])] |= actual
    for pass_number in (1, 2):
        summary[f"pass_{pass_number}"] = {
            "reviewed": len(pass_membership[pass_number]),
            "remaining": EXPECTED_ROWS - len(pass_membership[pass_number]),
            "labels": dict(pass_labels[pass_number]),
        }
    summary["complete"] = complete and all(len(values) == EXPECTED_ROWS for values in pass_membership.values())
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def rebuild_pass_two(output_root: Path) -> dict[str, Any]:
    if any((output_root / "pass_1" / "reviews").rglob("*.jsonl")) or any(
        (output_root / "pass_2" / "reviews").rglob("*.jsonl")
    ):
        raise ValueError("refusing to rebuild reviewer assignments after full-pass reviews exist")
    original_ledger_path = output_root / "blind" / "PACKET_LEDGER.jsonl"
    original_ledger = list(iter_jsonl(original_ledger_path))
    pass_one = [row for row in original_ledger if int(row["pass"]) == 1]
    pass_one_owner: dict[str, str] = {}
    blind_by_id: dict[str, dict[str, Any]] = {}
    for packet in pass_one:
        rows = list(iter_jsonl(Path(packet["packet_path"])))
        for row in rows:
            review_id = str(row["review_id"])
            if review_id in pass_one_owner:
                raise ValueError(f"duplicate pass-one identity: {review_id}")
            pass_one_owner[review_id] = str(packet["worker"])
            blind_by_id[review_id] = row
    if len(pass_one_owner) != EXPECTED_ROWS:
        raise ValueError(f"pass-one population changed: {len(pass_one_owner):,}")

    ordered = sorted(
        blind_by_id.values(),
        key=lambda row: digest(f"{AUDIT_VERSION}|pass-2-revised|{row['review_id']}"),
    )
    lanes = {worker: [] for worker in WORKERS}
    loads: Counter[str] = Counter()
    for row in ordered:
        review_id = str(row["review_id"])
        eligible_workers = [worker for worker in WORKERS if worker != pass_one_owner[review_id]]
        worker = min(eligible_workers, key=lambda value: (loads[value], value))
        lanes[worker].append(row)
        loads[worker] += 1

    revised_pass_two = []
    for worker in WORKERS:
        for packet_number, packet in enumerate(packetize(lanes[worker]), start=1):
            packet_id = f"P2R-{worker[-1]}-{packet_number:03d}"
            packet_path = output_root / "pass_2_revised" / "blind" / worker / f"{packet_id}.jsonl"
            output_path = output_root / "pass_2_revised" / "reviews" / worker / f"{packet_id}.jsonl"
            write_jsonl_new(packet_path, packet)
            revised_pass_two.append({
                "pass": 2,
                "packet_id": packet_id,
                "worker": worker,
                "articles": len(packet),
                "packet_path": str(packet_path),
                "packet_sha256": sha256_path(packet_path),
                "output_path": str(output_path),
            })
    revised_ledger = pass_one + revised_pass_two
    revised_ledger_path = output_root / "blind" / "PACKET_LEDGER_REV2.jsonl"
    write_jsonl_new(revised_ledger_path, revised_ledger)

    manifest_path = output_root / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["packet_ledger_path"] = str(revised_ledger_path)
    manifest["packet_ledger_sha256"] = sha256_path(revised_ledger_path)
    manifest["reviewer_independence"] = "enforced_per_review_id"
    manifest["superseded_pass_two_retained_unreviewed"] = True
    manifest["reviewer_assignment_revised_at_utc"] = datetime.now(UTC).isoformat()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    report = {
        "pass_one_rows": len(pass_one_owner),
        "pass_two_rows": sum(len(rows) for rows in lanes.values()),
        "pass_two_worker_loads": dict(loads),
        "same_reviewer_rows": sum(
            1
            for worker, rows in lanes.items()
            for row in rows
            if pass_one_owner[str(row["review_id"])] == worker
        ),
        "active_packet_ledger": str(revised_ledger_path),
        "active_packet_ledger_sha256": manifest["packet_ledger_sha256"],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def refresh_policy(output_root: Path) -> dict[str, Any]:
    if any((output_root / "pass_1" / "reviews").rglob("*.jsonl")) or any(
        (output_root / "pass_2" / "reviews").rglob("*.jsonl")
    ):
        raise ValueError("refusing to revise policy after full-pass reviews exist")
    policy_path = output_root / "blind" / "REVIEW_POLICY.json"
    policy_path.write_text(
        json.dumps(policy_contract(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest_path = output_root / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["review_policy_sha256"] = sha256_path(policy_path)
    manifest["policy_revision"] = 2
    manifest["policy_revised_at_utc"] = datetime.now(UTC).isoformat()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "policy_revision": 2,
        "review_policy_sha256": manifest["review_policy_sha256"],
    }, indent=2, sort_keys=True))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "prepare", "refresh-policy", "rebuild-pass-two",
            "score-calibration", "validate-passes",
        ),
    )
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--calibration-round", type=int, default=1)
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps(prepare(args.source_root, args.output_root), indent=2, sort_keys=True))
    elif args.command == "refresh-policy":
        refresh_policy(args.output_root)
    elif args.command == "rebuild-pass-two":
        rebuild_pass_two(args.output_root)
    elif args.command == "score-calibration":
        score_calibration(args.output_root, args.calibration_round)
    else:
        validate_passes(args.output_root)


if __name__ == "__main__":
    main()
