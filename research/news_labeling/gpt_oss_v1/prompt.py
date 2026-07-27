from __future__ import annotations

import json
from typing import Any

from .taxonomy import taxonomy_summary


DEVELOPER_PROMPT = """You are the semantic labeling authority for financial news.
Label only what the supplied article and source metadata support.

Hard rules:
1. Never infer future price reaction, trading outcome, or facts absent from the input.
2. Ticker count defines scope only. A one-ticker editorial story is not automatically a company announcement.
3. company_announcement=true only for a direct issuer announcement or a report of a concrete issuer-originated event.
4. Preserve simultaneous positive and negative evidence. Use mixed where appropriate.
5. Separate reported market reaction from the event's own language sentiment.
6. Resolve negation, comparisons, historical versus forward statements, and confirmed versus proposed/opinion language.
7. Evidence quotes must be short, verbatim substrings of title or rendered_article.
8. Use only enumerated values and valid family-specific subtypes. Do not add commentary or reasoning.
9. Existing deterministic labels are evidence, not unquestionable semantic truth. Identity and timestamps are immutable.
10. If evidence is inadequate, use unknown/other and the appropriate quality flag.
"""

DEVELOPER_PROMPT_WITH_TAXONOMY = (
    DEVELOPER_PROMPT
    + "\nFIXED TAXONOMY:\n"
    + json.dumps(taxonomy_summary(), ensure_ascii=False, separators=(",", ":"))
)


def build_messages(article: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "identity": {
            "canonical_news_id": article["canonical_news_id"],
            "published_at_utc": article["published_at_utc"],
        },
        "source_metadata": {
            "author": article.get("author", ""),
            "url_domain": article.get("url_domain", ""),
            "tickers": article.get("tickers", []),
            "channels": article.get("channels", []),
            "provider_tags": article.get("provider_tags", []),
            "quality_flags": article.get("quality_flags", []),
        },
        "deterministic_evidence": article.get("deterministic", {}),
        "title": article.get("title", ""),
        "rendered_article": article.get("rendered_text", ""),
    }
    return [
        # The large invariant taxonomy is deliberately in the common prefix so
        # vLLM prefix caching can reuse it across the complete sample.
        {"role": "developer", "content": DEVELOPER_PROMPT_WITH_TAXONOMY},
        {
            "role": "user",
            "content": "Return one JSON object matching the supplied schema.\nINPUT:\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]
