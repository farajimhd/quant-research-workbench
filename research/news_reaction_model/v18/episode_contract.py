from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Sequence

import numpy as np


class NodeRole(IntEnum):
    ROOT = 0
    MATERIAL_UPDATE = 1
    ANALYSIS = 2
    REACTIVE = 3
    DUPLICATE = 4


class RootFamily(IntEnum):
    COMPANY = 0
    REGULATORY = 1
    EDITORIAL = 2
    ANALYST = 3
    OTHER = 4


ROLE_NAMES = tuple(value.name.lower() for value in NodeRole)
ROOT_FAMILY_NAMES = tuple(value.name.lower() for value in RootFamily)
CONTEXT_SIZE = 8

# Static context is intentionally compact. Completed prior response evidence is
# added by the loader with an explicit validity bit.
CONTEXT_STATIC_NAMES = (
    *(f"role_{name}" for name in ROLE_NAMES),
    "log_gap_minutes",
    "root_age_sessions",
    "node_distance",
    "same_publication_session",
    "intervening_unembedded_count",
)
CONTEXT_TARGET_NAMES = (
    "target_valid",
    "high_return_pct",
    "low_return_pct",
    "terminal_return_pct",
    "vwap_return_pct",
    "buy_notional_share",
    "sell_notional_share",
)
CONTEXT_FEATURE_DIM = len(CONTEXT_STATIC_NAMES) + len(CONTEXT_TARGET_NAMES)

CURRENT_EPISODE_FEATURE_NAMES = (
    *(f"role_{name}" for name in ROLE_NAMES),
    *(f"root_family_{name}" for name in ROOT_FAMILY_NAMES),
    "normalized_node_position",
    "root_age_sessions",
    "minutes_since_material_update",
    "same_session_as_root",
    "unembedded_nodes_before",
)
CURRENT_EPISODE_FEATURE_DIM = len(CURRENT_EPISODE_FEATURE_NAMES)

REACTIVE_MARKERS = {
    "movers",
    "why it is moving",
    "why it's moving",
    "trading ideas",
    "premarket",
    "after hours",
    "after-hours",
    "midday movers",
    "stock moving",
    "shares are trading",
}
ANALYST_MARKERS = {
    "analyst",
    "price target",
    "upgrade",
    "downgrade",
    "initiates coverage",
    "maintains",
    "reiterates",
}
REGULATORY_FAMILIES = {
    "regulatory_clinical",
    "clinical",
    "fda",
    "legal_compliance",
}
ANALYST_FAMILIES = {"analyst_action", "analyst"}
COMPANY_FAMILIES = {
    "earnings",
    "guidance",
    "capital_allocation",
    "financing",
    "mergers_acquisitions",
    "contracts_orders",
    "products_commercial",
    "management_governance",
    "operations",
    "credit_solvency",
}
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ArticleSignals:
    title: str
    author: str
    channels: tuple[str, ...]
    tags: tuple[str, ...]
    semantic_families: tuple[str, ...]
    relevance_class: str
    text_hash: str
    has_body: bool


@dataclass(frozen=True, slots=True)
class Classification:
    role: NodeRole
    root_family: RootFamily
    material: bool
    root_eligible: bool
    reactive: bool
    duplicate_key: str


def _normalized_terms(values: Iterable[str]) -> set[str]:
    return {
        term
        for value in values
        for term in _TOKEN.findall(str(value).strip().lower())
    }


def classify_article(signals: ArticleSignals) -> Classification:
    title = signals.title.strip().lower()
    channels = tuple(value.strip().lower() for value in signals.channels)
    tags = tuple(value.strip().lower() for value in signals.tags)
    families = {value.strip().lower() for value in signals.semantic_families}
    vocabulary = " ".join((title, *channels, *tags))
    reactive = any(marker in vocabulary for marker in REACTIVE_MARKERS)
    analyst = bool(families & ANALYST_FAMILIES) or any(
        marker in vocabulary for marker in ANALYST_MARKERS
    )
    regulatory = bool(families & REGULATORY_FAMILIES) or "fda" in vocabulary
    company = (
        signals.relevance_class.strip().lower() == "company_specific"
        and not analyst
        and not reactive
    ) or bool(families & COMPANY_FAMILIES)
    editorial = (
        signals.has_body
        and bool(signals.author.strip())
        and not regulatory
        and not analyst
        and not company
        and not reactive
        and signals.relevance_class.strip().lower() != "not_relevant"
    )
    if regulatory:
        family = RootFamily.REGULATORY
    elif company:
        family = RootFamily.COMPANY
    elif editorial:
        family = RootFamily.EDITORIAL
    elif analyst:
        family = RootFamily.ANALYST
    else:
        family = RootFamily.OTHER
    root_eligible = family is not RootFamily.OTHER and not reactive
    material = root_eligible
    role = (
        NodeRole.REACTIVE
        if reactive
        else NodeRole.ANALYSIS
        if analyst
        else NodeRole.MATERIAL_UPDATE
        if material
        else NodeRole.REACTIVE
    )
    duplicate_key = signals.text_hash.strip() or hashlib.sha256(
        " ".join(_TOKEN.findall(title)).encode("utf-8")
    ).hexdigest()
    return Classification(role, family, material, root_eligible, reactive, duplicate_key)


def related_material_update(
    current: ArticleSignals,
    previous: ArticleSignals,
    *,
    current_family: RootFamily,
    previous_family: RootFamily,
) -> bool:
    current_semantic = set(current.semantic_families)
    previous_semantic = set(previous.semantic_families)
    if current_semantic & previous_semantic:
        return True
    left = _normalized_terms((current.title,))
    right = _normalized_terms((previous.title,))
    union = left | right
    # A broad family match (for example, two unrelated company events) is not
    # sufficient to merge episodes. The article must share a semantic event
    # family or enough title entities/terms to be a plausible continuation.
    return bool(union) and len(left & right) / len(union) >= 0.35


def role_one_hot(role: NodeRole) -> list[float]:
    return [float(role is candidate) for candidate in NodeRole]


def root_family_one_hot(family: RootFamily) -> list[float]:
    return [float(family is candidate) for candidate in RootFamily]


def current_episode_features(
    *,
    role: NodeRole,
    root_family: RootFamily,
    node_position: int,
    root_age_sessions: int,
    minutes_since_material: float,
    same_session_as_root: bool,
    unembedded_nodes_before: int,
) -> np.ndarray:
    values = np.asarray(
        [
            *role_one_hot(role),
            *root_family_one_hot(root_family),
            min(max(node_position, 0), 32) / 32.0,
            min(max(root_age_sessions, 0), 5) / 5.0,
            math.log1p(max(minutes_since_material, 0.0)) / math.log1p(2 * 24 * 60),
            float(same_session_as_root),
            min(max(unembedded_nodes_before, 0), 32) / 32.0,
        ],
        dtype=np.float32,
    )
    if values.shape != (CURRENT_EPISODE_FEATURE_DIM,):
        raise AssertionError(values.shape)
    return values


def context_static_features(
    *,
    role: NodeRole,
    gap_minutes: float,
    root_age_sessions: int,
    node_distance: int,
    same_publication_session: bool,
    intervening_unembedded_count: int,
) -> np.ndarray:
    values = np.asarray(
        [
            *role_one_hot(role),
            math.log1p(max(gap_minutes, 0.0)) / math.log1p(2 * 24 * 60),
            min(max(root_age_sessions, 0), 5) / 5.0,
            min(max(node_distance, 0), 32) / 32.0,
            float(same_publication_session),
            min(max(intervening_unembedded_count, 0), 32) / 32.0,
        ],
        dtype=np.float32,
    )
    if values.shape != (len(CONTEXT_STATIC_NAMES),):
        raise AssertionError(values.shape)
    return values


def episode_id(ticker: str, canonical_news_id: str, published_at_utc: str) -> str:
    return hashlib.sha256(
        f"{ticker}\x1f{canonical_news_id}\x1f{published_at_utc}".encode("utf-8")
    ).hexdigest()
