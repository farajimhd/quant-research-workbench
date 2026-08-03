from __future__ import annotations

from pathlib import Path
from typing import Callable

from research.mlops.clickhouse import ClickHouseHttpClient

from .fresh_acceptance_v2 import (
    AcceptanceBuildResult,
    AcceptanceRoundContract,
    build_acceptance_round,
)


ACCEPTANCE_VERSION = "news_semantic_fresh_acceptance_v5"
ACCEPTANCE_SEED = "news-fresh-acceptance-500-v5-20260802"
SAMPLE_ID_START = 1_501
SAMPLE_SIZE = 500
LOCKED_SPLIT = "fresh_acceptance_v5_untouched"
SESSION_QUOTAS = (
    ("premarket", 125),
    ("regular", 200),
    ("after_hours", 125),
    ("overnight", 50),
)


def build_acceptance_sample(
    client: ClickHouseHttpClient,
    root: Path,
    *,
    human_1300_root: Path,
    reviewed_200_root: Path,
    teacher_root: Path,
    report: Callable[[str], None] | None = None,
) -> AcceptanceBuildResult:
    """Build N1501-N2000 disjoint from every prior human and teacher item."""
    contract = AcceptanceRoundContract(
        collection_version=ACCEPTANCE_VERSION,
        sampling_seed=ACCEPTANCE_SEED,
        sample_id_start=SAMPLE_ID_START,
        locked_split=LOCKED_SPLIT,
        reviewer_label="Fifth fresh acceptance review",
        prior_human_authorities=(
            (human_1300_root, 1_300, "human-1300"),
            (reviewed_200_root, 200, "human-reviewed-200-v4"),
        ),
        teacher_root=teacher_root,
        sample_size=SAMPLE_SIZE,
        session_quotas=SESSION_QUOTAS,
    )
    return build_acceptance_round(client, root, contract=contract, report=report)
