from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def write_audit(
    root: Path,
    sample: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    report_title: str = "gpt-oss news semantic labeling v1 audit",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    sample_by_id = {row["canonical_news_id"]: row for row in sample}
    valid = [row for row in results if row.get("status") == "completed"]
    failures = [row for row in results if row.get("status") != "completed"]
    distributions: dict[str, Counter[str]] = defaultdict(Counter)
    for row in valid:
        label = row["label"]
        distributions["origin"][label["source"]["origin"]] += 1
        distributions["role"][label["source"]["role"]] += 1
        distributions["issuer_relationship"][label["source"]["issuer_relationship"]] += 1
        distributions["overall_sentiment"][label["sentiment"]["overall"]] += 1
        distributions["novelty"][label["novelty"]["class"]] += 1
        for event in label["events"]:
            distributions["event_family"][event["family"]] += 1

    report = [
        f"# {report_title}",
        "",
        f"- Sample: **{len(sample):,}**",
        f"- Completed: **{len(valid):,}**",
        f"- Failed validation/inference: **{len(failures):,}**",
        "",
        "These are research labels for taxonomy and prompt review. They are not a production authority.",
        "",
        "## Distributions",
        "",
    ]
    for name in sorted(distributions):
        report.extend((f"### {name.replace('_', ' ').title()}", "", "| Label | Count |", "|---|---:|"))
        report.extend(f"| {key} | {count:,} |" for key, count in distributions[name].most_common())
        report.append("")

    report.extend(("## Samples", ""))
    samples_root = root / "samples"
    samples_root.mkdir(exist_ok=True)
    for index, result in enumerate(valid, start=1):
        article = sample_by_id[result["canonical_news_id"]]
        filename = f"{index:03d}-{article['canonical_news_id']}.md"
        (samples_root / filename).write_text(_sample_markdown(article, result), encoding="utf-8")
        report.append(
            f"- [{article.get('title') or article['canonical_news_id']}](samples/{filename})"
        )
    if failures:
        report.extend(("", "## Failures", "", "```json"))
        report.append(json.dumps(failures, ensure_ascii=False, indent=2))
        report.extend(("```", ""))
    path = root / "AUDIT.md"
    path.write_text("\n".join(report), encoding="utf-8")
    return path


def _sample_markdown(article: dict[str, Any], result: dict[str, Any]) -> str:
    return "\n".join((
        f"# {article.get('title') or article['canonical_news_id']}",
        "",
        f"- Canonical ID: `{article['canonical_news_id']}`",
        f"- Published UTC: `{article['published_at_utc']}`",
        f"- Tickers: `{', '.join(article.get('tickers') or []) or 'none'}`",
        f"- Source: `{article.get('author') or 'unknown'} / {article.get('url_domain') or 'unknown'}`",
        f"- Existing deterministic label: `{article.get('deterministic', {}).get('kind', 'unknown')}`",
        "",
        "## Model label",
        "",
        "```json",
        json.dumps(result["label"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Certified rendered article",
        "",
        "````text",
        str(article.get("rendered_text") or "").replace("````", "&#96;&#96;&#96;&#96;"),
        "````",
        "",
    ))
