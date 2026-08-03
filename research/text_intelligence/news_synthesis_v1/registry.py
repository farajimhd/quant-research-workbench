from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REGISTRY_PATH = Path(__file__).with_name("concept_registry.json")


@dataclass(frozen=True, slots=True)
class ConceptLeaf:
    concept_id: str
    parent: str
    definition: str
    aliases: tuple[str, ...]


class ConceptRegistry:
    def __init__(self, version: str, fallback_leaf: str, leaves: Iterable[ConceptLeaf]) -> None:
        self.version = version
        self.fallback_leaf = fallback_leaf
        self._leaves = {leaf.concept_id: leaf for leaf in leaves}
        self._aliases: dict[str, str] = {}
        for leaf in self._leaves.values():
            self._aliases[_key(leaf.concept_id)] = leaf.concept_id
            for alias in leaf.aliases:
                self._aliases[_key(alias)] = leaf.concept_id
        if fallback_leaf not in self._leaves:
            raise ValueError(f"Fallback leaf is not registered: {fallback_leaf}")

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> "ConceptRegistry":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            str(payload["registry_version"]),
            str(payload["fallback_leaf"]),
            (
                ConceptLeaf(
                    concept_id=str(row["id"]),
                    parent=str(row["parent"]),
                    definition=str(row["definition"]),
                    aliases=tuple(str(value) for value in row.get("aliases", [])),
                )
                for row in payload["leaves"]
            ),
        )

    def resolve(self, legacy_concept: str) -> tuple[str, str]:
        key = _key(legacy_concept)
        exact = self._aliases.get(key)
        if exact:
            return exact, "exact_alias"
        tokens = set(key.split("_"))
        candidates = (
            ({"price", "move", "shares", "stock", "rally", "decline"}, "market.price_move_observed"),
            ({"volume", "trading", "attention"}, "market.volume_move_observed"),
            ({"short", "interest", "float", "cover"}, "market.short_interest_observed"),
            ({"analyst", "rating", "upgrade", "downgrade"}, "analyst.rating_action"),
            ({"target", "price"}, "analyst.price_target_action"),
            ({"earnings", "eps", "revenue", "quarter"}, "earnings.performance"),
            ({"guidance", "outlook", "forecast"}, "guidance.issued"),
            ({"asset", "sale", "divestiture", "disposal"}, "corporate_transaction.asset_sale"),
            ({"acquisition", "merger", "takeover", "transaction"}, "corporate_transaction.acquisition"),
            ({"offering", "financing", "placement", "debt", "dilution"}, "capital.financing"),
            ({"buyback", "repurchase", "dividend"}, "capital.return"),
            ({"regulatory", "sec", "halt", "exchange"}, "regulatory.action"),
            ({"lawsuit", "litigation", "investigation", "settlement", "bribery"}, "legal.proceeding"),
            ({"fda", "clinical", "trial", "drug"}, "clinical.regulatory_milestone"),
            ({"contract", "order", "award", "customer"}, "commercial.contract"),
            ({"product", "launch", "recall"}, "product.milestone"),
            ({"executive", "management", "board", "ceo", "cfo"}, "governance.management_change"),
            ({"operations", "business", "restructuring", "layoff", "facility"}, "operations.business_update"),
            ({"listing", "delisting", "compliance", "split", "ipo"}, "listing.market_structure"),
            ({"partnership", "collaboration", "licensing", "distribution"}, "commercial.partnership"),
            ({"margin"}, "financial.margin"),
            ({"ownership", "insider", "stake"}, "ownership.position_change"),
            ({"liquidity", "cash"}, "financial.liquidity"),
            ({"index", "inclusion", "removal"}, "index.membership"),
        )
        scored = [(len(tokens & clues), leaf) for clues, leaf in candidates]
        score, leaf = max(scored, default=(0, self.fallback_leaf))
        return (leaf, "heuristic") if score else (self.fallback_leaf, "fallback")

    def contains(self, concept_id: str) -> bool:
        return concept_id in self._leaves


def _key(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_")
