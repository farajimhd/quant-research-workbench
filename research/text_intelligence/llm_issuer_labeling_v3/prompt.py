from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .schema import SCHEMA_VERSION


SYSTEM_PROMPT = """Extract every distinct issuer from the supplied financial news and return only JSON matching llm_issuer_news_labels_v3. Do not output reasoning, Markdown, comments, copied sentence text, or extra keys.

An issuer is a company, fund, or other security issuer. Do not treat a person, product, index, government, regulator, exchange, analyst, or generic phrase as an issuer. Merge aliases for the same issuer. Keep parents, subsidiaries, counterparties, and competitors separate. Infer identity only from the supplied sentences and metadata. Never invent a ticker; use null when unknown. Return one row per resolved issuer, sorted by issuer_name.

forecast_relevance_probability is the probability that: (1) the issuer has a security tradable at publication time; (2) trustworthy local evidence is specifically about it; (3) the article newly reports a current issuer event or new issuer guidance; (4) the event is material to fundamentals, financing, operations, assets, liabilities, legal/regulatory position, capital structure, or survival, even if its directional effect is neutral or uncertain; and (5) the text reports the event itself rather than only analyst opinion, a preview, recap, price observation/explanation, background, or a reference to another article. Use low probability when any requirement is absent.

positive_implication_probability and negative_implication_probability are independent issuer-specific probabilities. Both high means mixed. Both low means neutral or directionally uncertain. A neutral material event may still have high forecast relevance. Never borrow an event or direction from another issuer.

Allowed event_tags: acquisition, analyst_action, asset_sale, capital_return, capital_structure, clinical_trial, commercial_contract, earnings, financing, financial_condition, guidance, legal, listing, management_governance, market_observation, operations, ownership, partnership, product, regulatory, solvency, strategy, workforce, other_material.
Allowed issuer_roles: primary_subject, acquirer, target, buyer, seller, partner, customer, supplier, borrower, lender, investor, investee, plaintiff, defendant, regulatory_subject, competitor, mentioned_other.
Allowed identity_source: explicit_text, metadata, llm_inference. Allowed time_scope: current, forward, historical, mixed, unclear. Allowed claim_source: issuer, regulator, analyst, editorial, mixed, unknown.

For each issuer return one to three sorted evidence_sentence_ids that exist in the input and jointly support identity and labels. Sort and deduplicate tags and roles. All probabilities must be finite numbers in [0,1]. Use only information available at published_at_utc; never use later prices, events, or outcomes.
"""


def load_example_bank(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_system_prompt(
    example_bank: Mapping[str, Any], example_inputs: Mapping[str, Mapping[str, Any]]
) -> str:
    cards: list[str] = []
    for index, example in enumerate(example_bank["examples"], start=1):
        source_id = str(example["source_id"])
        sample = example_inputs[source_id]
        compact_input = {
            "published_at_utc": sample["published_at_utc"],
            "normalized_sentences": sample["normalized_sentences"],
            "metadata": sample["metadata"],
        }
        expected = {
            "schema_version": SCHEMA_VERSION,
            "issuers": example["issuers"],
            "unresolved_issuer_mentions": example["unresolved_issuer_mentions"],
        }
        cards.append(
            f"Example {index} input: "
            + json.dumps(compact_input, ensure_ascii=False, separators=(",", ":"))
            + "\nExample output: "
            + json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
        )
    return SYSTEM_PROMPT.rstrip() + "\n\nGold-aligned examples:\n" + "\n\n".join(cards)


def build_messages(system_prompt: str, sample: Mapping[str, Any]) -> list[dict[str, str]]:
    request_input = {
        "published_at_utc": sample["published_at_utc"],
        "normalized_sentences": sample["normalized_sentences"],
        "metadata": sample["metadata"],
    }
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": "Extract and label every issuer using llm_issuer_news_labels_v3:\n"
            + json.dumps(request_input, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def example_source_ids(example_bank: Mapping[str, Any]) -> set[str]:
    return {str(row["source_id"]) for row in example_bank["examples"]}
