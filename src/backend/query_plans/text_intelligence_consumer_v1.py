from __future__ import annotations

import re
from typing import Callable, Iterable


PLAN_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def scoped_labels(
    corpus: str,
    source_ids: Iterable[str],
    *,
    labeling_version: str,
    quote: Callable[[str], str],
    source_end: str = "",
    source_start: str = "",
    ticker: str = "",
) -> str:
    identities = sorted({value.strip() for value in source_ids if value.strip()})
    if not identities:
        raise ValueError("scoped-label query requires at least one source ID")
    conditions = [f"corpus={quote(corpus)}"]
    if ticker.strip():
        conditions.append(f"ticker={quote(ticker.strip().upper())}")
    if source_start.strip():
        conditions.append(
            "source_timestamp >= parseDateTime64BestEffort("
            f"{quote(source_start.strip())})"
        )
    if source_end.strip():
        conditions.append(
            "source_timestamp <= parseDateTime64BestEffort("
            f"{quote(source_end.strip())})"
        )
    prewhere = " AND ".join(conditions)
    values = ",".join(quote(value) for value in identities)
    return f"""
SELECT source_id,unit_id,ticker,unit_role,event_id,event_tickers,issuer_role,
       evidence_scope,semantic_evidence_text,content_role,source_origin,
       event_concepts,semantic_direction,semantic_score,
       forecast_trigger_eligible,reaction_evaluation_eligible,
       issuer_history_context_eligible,classification_json,labeling_version
FROM
(
    SELECT source_id,unit_id,ticker,unit_role,event_id,event_tickers,issuer_role,
           evidence_scope,semantic_evidence_text,content_role,source_origin,
           event_concepts,semantic_direction,semantic_score,
           forecast_trigger_eligible,reaction_evaluation_eligible,
           issuer_history_context_eligible,classification_json,labeling_version
    FROM q_live.scoped_text_labels_v5
    PREWHERE {prewhere}
    WHERE labeling_version={quote(labeling_version)}
      AND source_id IN ({values})
    ORDER BY updated_at_utc DESC
    LIMIT 1 BY corpus,ticker,source_timestamp,source_id,unit_id,labeling_version
)
ORDER BY source_id,forecast_trigger_eligible DESC,
         abs(semantic_score) DESC,ticker,unit_id
FORMAT JSONEachRow
"""


def news_synthesis_by_id(
    source_ids: Iterable[str],
    *,
    engine_version: str,
    synthesis_table: str,
    quote: Callable[[str], str],
) -> str:
    identities = sorted({value.strip() for value in source_ids if value.strip()})
    if not identities:
        raise ValueError("news-synthesis query requires at least one source ID")
    if not _IDENTIFIER.fullmatch(synthesis_table):
        raise ValueError("news-synthesis table is not a valid identifier")
    values = ",".join(quote(value) for value in identities)
    return f"""
SELECT canonical_news_id,synthesis_json
FROM q_live.{synthesis_table} FINAL
WHERE engine_version={quote(engine_version)}
  AND canonical_news_id IN ({values})
ORDER BY updated_at_utc DESC
LIMIT 1 BY canonical_news_id,engine_version
FORMAT JSONEachRow"""
