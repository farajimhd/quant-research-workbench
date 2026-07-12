"""Render TASK_HISTORY.md from the canonical TASK_HISTORY.csv ledger."""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "TASK_HISTORY.csv"
MARKDOWN_PATH = ROOT / "TASK_HISTORY.md"

EXPECTED_COLUMNS = [
    "id",
    "scope",
    "area",
    "status",
    "task",
    "current_focus",
    "started",
    "last_updated",
    "completed",
    "description",
    "progress",
    "final_result_or_next_dependency",
    "program_contribution",
]

DISPLAY_COLUMNS = [column for column in EXPECTED_COLUMNS if column != "current_focus"]
DISPLAY_LABELS = {
    "id": "ID",
    "scope": "Scope",
    "area": "Area",
    "status": "Status",
    "task": "Task",
    "started": "Started",
    "last_updated": "Last updated",
    "completed": "Completed",
    "description": "Description",
    "progress": "Progress / what happened",
    "final_result_or_next_dependency": "Final result or next dependency",
    "program_contribution": "Program contribution",
}


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def read_rows() -> list[dict[str, str]]:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise ValueError(
                f"Unexpected task history columns: {reader.fieldnames!r}; "
                f"expected {EXPECTED_COLUMNS!r}"
            )
        rows = list(reader)

    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("TASK_HISTORY.csv contains duplicate task IDs")
    return rows


def render(rows: list[dict[str, str]]) -> str:
    focus_rows = [row for row in rows if row["current_focus"].strip().lower() == "true"]
    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    lines = [
        "# Task History",
        "",
        "This is the chat-independent view of durable user-requested outcomes across",
        "the user's 2026 Codex work. `TASK_HISTORY.csv` is the canonical editable",
        "ledger; the table below is generated from it by",
        "`python scripts/render_task_history.py` and must not be edited by hand.",
        "",
        "## Current Focus",
        "",
    ]

    if focus_rows:
        for row in focus_rows:
            dependency = row["final_result_or_next_dependency"].strip()
            suffix = f" Next: {dependency}" if dependency else ""
            lines.append(f"- **{row['id']} - {row['task']}**.{suffix}")
    else:
        lines.append("- No task is currently marked as a focus in `TASK_HISTORY.csv`.")

    lines.extend(
        [
            "",
            "## Overall Direction",
            "",
            "The work is converging on a local-first, explainable quantitative research",
            "and trading platform: reliable historical/live data and identity layers feed",
            "reusable research, scanner, backtest, and representation-learning systems;",
            "those systems are validated through operator-facing service, semi-automatic,",
            "and live-trading workflows before they influence real execution.",
            "",
            "The laptop repository remains the code source of truth. The workstation",
            "provides heavy storage, ingestion, profiling, and training capacity.",
            "",
            "## Ledger Summary",
            "",
            f"- Durable tasks: {len(rows)}",
        ]
    )
    for status in ["Designing", "In progress", "Blocked", "Completed", "Cancelled", "Superseded"]:
        if status in status_counts:
            lines.append(f"- {status}: {status_counts[status]}")

    lines.extend(
        [
            "",
            "Times use the Vancouver offset when an exact request timestamp is available.",
            "Date-only values identify consolidated historical boundaries where false",
            "timestamp precision would be misleading.",
            "",
            "## Imported Task Table",
            "",
            "<!-- GENERATED FROM TASK_HISTORY.csv; DO NOT EDIT THIS TABLE DIRECTLY. -->",
            "| " + " | ".join(DISPLAY_LABELS[column] for column in DISPLAY_COLUMNS) + " |",
            "|" + "|".join("---" for _ in DISPLAY_COLUMNS) + "|",
        ]
    )
    for row in rows:
        lines.append(
            "| " + " | ".join(markdown_cell(row[column]) for column in DISPLAY_COLUMNS) + " |"
        )

    return "\n".join(lines) + "\n"


def main() -> None:
    rows = read_rows()
    MARKDOWN_PATH.write_text(render(rows), encoding="utf-8", newline="\n")
    print(f"Rendered {len(rows)} tasks from {CSV_PATH.name} into {MARKDOWN_PATH.name}")


if __name__ == "__main__":
    main()
