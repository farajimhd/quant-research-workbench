from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


AUDIT_VERSION = "news_synthesis_v61_personal_mismatch_audit_v1"
NEWS_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1")
EVALUATION_ROOT = NEWS_ROOT / "news_synthesis_v61_consolidated_gold_v2_evaluation_v1"
ASSIGNMENTS = (
    Path(r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4")
    / "forecast_eligibility_sentiment_authority_v59_calibrated_reaudit_v2"
    / "EVALUATION_ASSIGNMENTS.csv"
)
DEFAULT_OUTPUT = NEWS_ROOT / AUDIT_VERSION

EXPECTED_ALL_MISMATCHES = 32_533
EXPECTED_TRAINING_MISMATCHES = 31_856
EXPECTED_HOLDOUT_MISMATCHES = 677
EXPECTED_PATHS = 37
EXPECTED_GROUPS = 402
EXPECTED_NONEMPTY_FILES = 480
EXPECTED_ASSIGNMENTS = 352_559

ALLOWED_GOLD_LABELS = {"eligible", "ineligible"}
PATH_PART_MEANINGS = {
    "single_subject": "The article was resolved as primarily about one subject.",
    "multi_subject_digest": "The article was resolved as a digest covering multiple subjects.",
    "market_overview": "The article was resolved as a market-level overview.",
    "reference_list": "The article was resolved as a reference or list-oriented item.",
    "report": "The language was interpreted as reporting an event or assertion.",
    "recap": "The language was interpreted as recapping prior or contextual information.",
    "analyze": "The language was interpreted as analysis rather than a new issuer event.",
    "preview": "The language was interpreted as previewing a possible or scheduled event.",
    "explain_move": "The language was interpreted as explaining a market-price move.",
    "issuer": "The asserted information was attributed primarily to the issuer.",
    "editorial": "The asserted information was attributed primarily to editorial context.",
    "analyst": "The asserted information was attributed primarily to an analyst.",
    "regulator": "The asserted information was attributed primarily to a regulator or exchange.",
    "mixed": "The article contained mixed or unresolved attribution.",
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
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{line_number}: {exc}") from exc


def write_json_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def write_jsonl_new(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")


def markdown_escape(value: Any) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def slug(value: str, *, limit: int = 52) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "unmatched"
    return clean[:limit].rstrip("_")


def directory_name(prefix: str, value: str) -> str:
    return f"{prefix}_{slug(value)}_{digest(value)[:8]}"


def split_pipe_list(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def load_assignments(path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "source_id",
            "population_split",
            "primary_pattern_id",
            "normalized_title_template",
            "author",
            "channels",
            "provider_tags",
        }
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("assignment schema is missing required audit fields")
        for row in reader:
            source_id = str(row["source_id"])
            if source_id in rows:
                raise ValueError(f"duplicate assignment source_id: {source_id}")
            rows[source_id] = row
    if len(rows) != EXPECTED_ASSIGNMENTS:
        raise ValueError(f"assignment authority changed: {len(rows):,} rows")
    return rows


def title_pattern_id(assignment: Mapping[str, Any]) -> str:
    return str(assignment.get("primary_pattern_id") or "unmatched").strip() or "unmatched"


def path_explanation(synthesis_path: str) -> list[str]:
    parts = [part.strip() for part in synthesis_path.split(">")]
    return [PATH_PART_MEANINGS.get(part, f"News Synthesis emitted the `{part}` path component.") for part in parts]


def pattern_explanation(pattern_id: str) -> str:
    if pattern_id == "unmatched":
        return (
            "No deterministic primary title-pattern rule matched. The path and downstream "
            "forecast policy still produced the displayed News Synthesis decision."
        )
    family, _, detail = pattern_id.partition(".")
    family_text = {
        "event": "an event pattern",
        "context": "a contextual or non-new-event pattern",
        "reaction": "a market-reaction pattern",
        "signal": "a structural language signal",
    }.get(family, "a deterministic title pattern")
    detail_text = (detail or family).replace("_", " ")
    return f"The title matched {family_text}: `{detail_text}` (`{pattern_id}`)."


def load_training_rows(
    *, mismatch_path: Path, assignments: Mapping[str, Mapping[str, str]]
) -> tuple[list[dict[str, Any]], Counter[str]]:
    result: list[dict[str, Any]] = []
    split_counts: Counter[str] = Counter()
    seen: set[str] = set()
    for mismatch in iter_jsonl(mismatch_path):
        source_id = str(mismatch["source_id"])
        if source_id in seen:
            raise ValueError(f"duplicate mismatch source_id: {source_id}")
        seen.add(source_id)
        assignment = assignments.get(source_id)
        if assignment is None:
            raise ValueError(f"mismatch missing assignment: {source_id}")
        split = str(assignment["population_split"])
        if split != str(mismatch["population_split"]):
            raise ValueError(f"split disagreement for {source_id}")
        split_counts[split] += 1
        if split != "training_development":
            continue
        gold_label = str(mismatch["gold_label"])
        synthesis_label = str(mismatch["synthesis_label"])
        if gold_label not in ALLOWED_GOLD_LABELS or synthesis_label not in ALLOWED_GOLD_LABELS:
            raise ValueError(f"nonbinary training mismatch: {source_id}")
        if gold_label == synthesis_label:
            raise ValueError(f"non-mismatch entered audit: {source_id}")
        synthesis_path = str(mismatch.get("synthesis_path") or "").strip()
        if not synthesis_path:
            raise ValueError(f"missing synthesis path: {source_id}")
        pattern_id = title_pattern_id(assignment)
        result.append(
            {
                "review_id": "V61P" + digest(f"{AUDIT_VERSION}|{source_id}")[:20],
                "source_id": source_id,
                "published_at_utc": str(mismatch["published_at_utc"]),
                "title": str(mismatch["title"]),
                "tickers": list(mismatch.get("tickers") or []),
                "author": str(assignment.get("author") or ""),
                "channels": split_pipe_list(assignment.get("channels")),
                "provider_tags": split_pipe_list(assignment.get("provider_tags")),
                "population_split": split,
                "gold_label": gold_label,
                "synthesis_label": synthesis_label,
                "confusion_cell": str(mismatch["confusion_cell"]),
                "synthesis_path": synthesis_path,
                "title_pattern_id": pattern_id,
                "normalized_title_template": str(
                    assignment.get("normalized_title_template") or ""
                ),
                "forecast_policy_ids": list(mismatch.get("forecast_policy_ids") or []),
                "forecast_reasons": list(mismatch.get("forecast_reasons") or []),
            }
        )
    if len(seen) != EXPECTED_ALL_MISMATCHES:
        raise ValueError(f"overall mismatch population changed: {len(seen):,}")
    if len(result) != EXPECTED_TRAINING_MISMATCHES:
        raise ValueError(f"training mismatch population changed: {len(result):,}")
    if split_counts != Counter(
        {
            "training_development": EXPECTED_TRAINING_MISMATCHES,
            "holdout_august_2026": EXPECTED_HOLDOUT_MISMATCHES,
        }
    ):
        raise ValueError(f"mismatch split population changed: {dict(split_counts)}")
    return result, split_counts


def render_audit_file(
    *, synthesis_path: str, pattern_id: str, gold_label: str, rows: list[dict[str, Any]]
) -> str:
    synthesis_labels = {str(row["synthesis_label"]) for row in rows}
    if len(synthesis_labels) != 1:
        raise ValueError("gold bucket does not have one synthesis label")
    synthesis_label = next(iter(synthesis_labels))
    if synthesis_label == gold_label:
        raise ValueError("audit file contains non-mismatch rows")
    reason_counts = Counter(
        str(reason) for row in rows for reason in row.get("forecast_reasons") or []
    )
    policy_counts = Counter(
        str(policy) for row in rows for policy in row.get("forecast_policy_ids") or []
    )
    lines = [
        f"# V61 mismatch audit: {pattern_id}",
        "",
        f"- Synthesis path: `{synthesis_path}`",
        f"- Title pattern: `{pattern_id}`",
        f"- News Synthesis label: `{synthesis_label}`",
        f"- Current gold-label bucket: `{gold_label}`",
        f"- Articles: `{len(rows):,}`",
        "- Scope: `training_development` only; August holdout rows are excluded.",
        "",
        "## Why News Synthesis chose its label",
        "",
    ]
    lines.extend(f"- {text}" for text in path_explanation(synthesis_path))
    lines.append(f"- {pattern_explanation(pattern_id)}")
    if reason_counts:
        lines.extend(["", "Most frequent decision reasons in this file:", ""])
        lines.extend(
            f"- `{markdown_escape(reason)}`: {count:,}"
            for reason, count in reason_counts.most_common()
        )
    if policy_counts:
        lines.extend(["", "Forecast policies represented in this file:", ""])
        lines.extend(
            f"- `{markdown_escape(policy)}`: {count:,}"
            for policy, count in policy_counts.most_common()
        )
    lines.extend(
        [
            "",
            "## File-level decision",
            "",
            "Select exactly one:",
            "",
            "- [ ] All articles should be eligible",
            "- [ ] All articles should be ineligible",
            "- [ ] Mixed — check `Wrong` for each row whose displayed gold label is wrong",
            "",
            "For `Mixed`, unchecked rows retain the displayed gold label. Because this file has "
            "one binary gold-label bucket, checking `Wrong` changes that row to the opposite label.",
            "",
            "| Wrong | Review ID | Gold label | Published (UTC) | Tickers | Title |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in sorted(rows, key=lambda item: (str(item["published_at_utc"]), str(item["source_id"]))):
        lines.append(
            f"| [ ] | `{row['review_id']}` | `{gold_label}` | "
            f"{markdown_escape(row['published_at_utc'])} | "
            f"{markdown_escape(','.join(row['tickers']) or '-')} | "
            f"{markdown_escape(row['title'])} |"
        )
    return "\n".join(lines) + "\n"


def prepare(
    *,
    evaluation_root: Path = EVALUATION_ROOT,
    assignments_path: Path = ASSIGNMENTS,
    output_root: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite immutable audit root: {output_root}")
    report_path = evaluation_root / "REPORT.json"
    mismatch_path = evaluation_root / "MISMATCHES.jsonl"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "passed" or int(report.get("mismatches", -1)) != EXPECTED_ALL_MISMATCHES:
        raise ValueError("V61 evaluation report is not the expected passed authority")
    assignments = load_assignments(assignments_path)
    rows, split_counts = load_training_rows(
        mismatch_path=mismatch_path, assignments=assignments
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["synthesis_path"]), str(row["title_pattern_id"]))].append(row)
    paths = {key[0] for key in groups}
    patterns = {key[1] for key in groups}
    if len(paths) != EXPECTED_PATHS or len(groups) != EXPECTED_GROUPS:
        raise ValueError(
            f"grouping authority changed: paths={len(paths)}, groups={len(groups)}"
        )

    output_root.mkdir(parents=True)
    controller_rows = sorted(rows, key=lambda row: str(row["source_id"]))
    controller_path = output_root / "controller" / "CONTROLLER.jsonl"
    write_jsonl_new(controller_path, controller_rows)

    group_ledger: list[dict[str, Any]] = []
    audit_files: list[Path] = []
    path_stats: dict[str, Counter[str]] = defaultdict(Counter)
    for (synthesis_path, pattern_id), group_rows in sorted(groups.items()):
        path_dir = output_root / "audit" / directory_name("path", synthesis_path)
        pattern_dir = path_dir / directory_name("title", pattern_id)
        gold_counts = Counter(str(row["gold_label"]) for row in group_rows)
        file_entries = []
        for gold_label in ("eligible", "ineligible"):
            bucket = [row for row in group_rows if row["gold_label"] == gold_label]
            if not bucket:
                continue
            file_path = pattern_dir / f"gold_{gold_label}.md"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                render_audit_file(
                    synthesis_path=synthesis_path,
                    pattern_id=pattern_id,
                    gold_label=gold_label,
                    rows=bucket,
                ),
                encoding="utf-8",
                newline="\n",
            )
            audit_files.append(file_path)
            relative_path = file_path.relative_to(output_root).as_posix()
            file_entries.append(
                {
                    "gold_label": gold_label,
                    "rows": len(bucket),
                    "path": relative_path,
                    "sha256": sha256_path(file_path),
                }
            )
            path_stats[synthesis_path][f"gold:{gold_label}"] += len(bucket)
            path_stats[synthesis_path]["files"] += 1
        path_stats[synthesis_path]["groups"] += 1
        path_stats[synthesis_path]["rows"] += len(group_rows)
        group_ledger.append(
            {
                "synthesis_path": synthesis_path,
                "title_pattern_id": pattern_id,
                "rows": len(group_rows),
                "gold_label_counts": dict(gold_counts),
                "files": file_entries,
            }
        )
    if len(audit_files) != EXPECTED_NONEMPTY_FILES:
        raise ValueError(f"nonempty audit file count changed: {len(audit_files)}")
    group_ledger_path = output_root / "controller" / "GROUPS.jsonl"
    write_jsonl_new(group_ledger_path, group_ledger)

    index_lines = [
        "# V61 personal mismatch audit",
        "",
        f"- Training mismatches: `{len(rows):,}`",
        f"- Synthesis-path folders: `{len(paths):,}`",
        f"- Path and title-pattern groups: `{len(groups):,}`",
        f"- Nonempty gold-label audit files: `{len(audit_files):,}`",
        "- Holdout rows included: `0`",
        "",
        "Review one file at a time. Select exactly one file-level decision. Use row-level "
        "`Wrong` checkboxes only when the file-level decision is `Mixed`.",
        "",
        "| Synthesis path | Title pattern | Articles | Gold eligible | Gold ineligible | Audit files |",
        "|---|---|---:|---:|---:|---|",
    ]
    for group in group_ledger:
        links = ", ".join(
            f"[{entry['gold_label']}]({entry['path']})" for entry in group["files"]
        )
        counts = group["gold_label_counts"]
        index_lines.append(
            f"| {markdown_escape(group['synthesis_path'])} | "
            f"`{markdown_escape(group['title_pattern_id'])}` | {group['rows']:,} | "
            f"{int(counts.get('eligible', 0)):,} | {int(counts.get('ineligible', 0)):,} | "
            f"{links} |"
        )
    index_path = output_root / "AUDIT_INDEX.md"
    index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8", newline="\n")

    report_value = {
        "audit_version": AUDIT_VERSION,
        "status": "generated",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "articles": len(rows),
        "gold_label_counts": dict(Counter(str(row["gold_label"]) for row in rows)),
        "synthesis_paths": len(paths),
        "unique_title_pattern_ids": len(patterns),
        "path_title_pattern_groups": len(groups),
        "nonempty_audit_files": len(audit_files),
        "omitted_empty_gold_files": 2 * len(groups) - len(audit_files),
        "split_counts": dict(split_counts),
        "unmatched_articles": sum(row["title_pattern_id"] == "unmatched" for row in rows),
        "path_stats": {
            path: dict(stats)
            for path, stats in sorted(path_stats.items(), key=lambda item: (-item[1]["rows"], item[0]))
        },
        "authority": {
            "evaluation_report": str(report_path),
            "evaluation_report_sha256": sha256_path(report_path),
            "mismatches": str(mismatch_path),
            "mismatches_sha256": sha256_path(mismatch_path),
            "assignments": str(assignments_path),
            "assignments_sha256": sha256_path(assignments_path),
        },
    }
    report_output = output_root / "REPORT.json"
    write_json_new(report_output, report_value)
    manifest = {
        "audit_version": AUDIT_VERSION,
        "status": "passed",
        "articles": len(rows),
        "synthesis_paths": len(paths),
        "path_title_pattern_groups": len(groups),
        "nonempty_audit_files": len(audit_files),
        "holdout_rows": 0,
        "files": {
            "AUDIT_INDEX.md": sha256_path(index_path),
            "REPORT.json": sha256_path(report_output),
            "controller/CONTROLLER.jsonl": sha256_path(controller_path),
            "controller/GROUPS.jsonl": sha256_path(group_ledger_path),
        },
    }
    write_json_new(output_root / "MANIFEST.json", manifest)
    print(
        "[audit] status=completed "
        f"articles={len(rows):,} paths={len(paths):,} groups={len(groups):,} "
        f"files={len(audit_files):,} holdout=0 output={output_root}",
        flush=True,
    )
    return manifest


def validate(output_root: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest_path = output_root / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "passed":
        raise ValueError("audit manifest is not passed")
    for relative, expected_hash in manifest["files"].items():
        path = output_root / relative
        if sha256_path(path) != expected_hash:
            raise ValueError(f"audit authority hash mismatch: {relative}")
    controller = list(iter_jsonl(output_root / "controller" / "CONTROLLER.jsonl"))
    groups = list(iter_jsonl(output_root / "controller" / "GROUPS.jsonl"))
    review_ids = [str(row["review_id"]) for row in controller]
    source_ids = [str(row["source_id"]) for row in controller]
    if (
        len(controller) != EXPECTED_TRAINING_MISMATCHES
        or len(set(review_ids)) != len(controller)
        or len(set(source_ids)) != len(controller)
    ):
        raise ValueError("controller coverage or uniqueness failed")
    if any(str(row["population_split"]) != "training_development" for row in controller):
        raise ValueError("holdout row leaked into controller")
    file_entries = [entry for group in groups for entry in group["files"]]
    if len(groups) != EXPECTED_GROUPS or len(file_entries) != EXPECTED_NONEMPTY_FILES:
        raise ValueError("group or audit-file coverage changed")
    if sum(int(entry["rows"]) for entry in file_entries) != EXPECTED_TRAINING_MISMATCHES:
        raise ValueError("audit-file rows do not reconcile")
    for entry in file_entries:
        path = output_root / str(entry["path"])
        if sha256_path(path) != str(entry["sha256"]):
            raise ValueError(f"audit file hash mismatch: {entry['path']}")
    result = {
        "status": "passed",
        "articles": len(controller),
        "groups": len(groups),
        "files": len(file_entries),
        "holdout_rows": 0,
    }
    print(
        "[validation] status=passed "
        f"articles={result['articles']:,} groups={result['groups']:,} "
        f"files={result['files']:,} holdout=0",
        flush=True,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate or validate the operator-facing V61 mismatch Markdown audit."
    )
    parser.add_argument("command", choices=("generate", "validate"))
    parser.add_argument("--evaluation-root", type=Path, default=EVALUATION_ROOT)
    parser.add_argument("--assignments", type=Path, default=ASSIGNMENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.command == "generate":
        prepare(
            evaluation_root=args.evaluation_root,
            assignments_path=args.assignments,
            output_root=args.output,
        )
    else:
        validate(args.output)


if __name__ == "__main__":
    main()
