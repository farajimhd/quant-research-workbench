from __future__ import annotations

import datetime as dt
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import ClickHouseHttpClient
from research.text_intelligence.scoped_labeling_v1.news_identity import (
    load_news_issuer_resolver,
)

from .annotation_template import annotation_template
from .sampling import (
    build_blinded_item,
    hydrate_complete_v5_units,
    hydrate_event_metadata,
    hydrate_text_products,
    json_rows,
    merge_candidates,
    sealed_v5_summary,
)
from .schema import ANNOTATION_VERSION_V3, SAMPLE_VERSION, stable_json_hash
from .sol_teacher_corpus import (
    END_YEAR,
    START_YEAR,
    distribute_quota,
    round_robin_teacher_strata,
    source_year,
    teacher_distribution,
    teacher_selection_stratum,
)
from .storage import assert_runtime_root, read_json, write_json_atomic


ACCEPTANCE_VERSION = "news_semantic_fresh_acceptance_v2"
ACCEPTANCE_SEED = "news-fresh-acceptance-100-v2-20260802"
DEFAULT_SAMPLE_SIZE = 100
DEFAULT_SAMPLE_ID_START = 1_101
SESSION_QUOTAS: Mapping[str, int] = {
    "premarket": 25,
    "regular": 40,
    "after_hours": 25,
    "overnight": 10,
}
SESSION_ORDER = tuple(SESSION_QUOTAS)
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class AcceptanceBuildResult:
    root: Path
    sample_count: int
    excluded_count: int
    manifest_hash: str


@dataclass(frozen=True, slots=True)
class AcceptanceRoundContract:
    collection_version: str
    sampling_seed: str
    sample_id_start: int
    locked_split: str
    reviewer_label: str
    prior_human_authorities: tuple[tuple[Path, int, str], ...]
    teacher_root: Path
    teacher_expected: int = 10_000
    sample_size: int = DEFAULT_SAMPLE_SIZE


def v2_round_contract(*, human_root: Path, teacher_root: Path) -> AcceptanceRoundContract:
    return AcceptanceRoundContract(
        collection_version=ACCEPTANCE_VERSION,
        sampling_seed=ACCEPTANCE_SEED,
        sample_id_start=DEFAULT_SAMPLE_ID_START,
        locked_split="fresh_acceptance_v2",
        reviewer_label="Second fresh acceptance review",
        prior_human_authorities=((human_root, 1_100, "human-1100"),),
        teacher_root=teacher_root,
    )


def market_session(timestamp: str) -> str:
    value = timestamp.strip().replace(" ", "T", 1)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    local = parsed.astimezone(NEW_YORK)
    minutes = local.hour * 60 + local.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "premarket"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "after_hours"
    return "overnight"


def build_acceptance_sample(
    client: ClickHouseHttpClient,
    root: Path,
    *,
    human_root: Path,
    teacher_root: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    report: Callable[[str], None] | None = None,
) -> AcceptanceBuildResult:
    """Build the second prediction-blind acceptance set.

    V5 fields are used only to diversify the sealed sample. Reviewers see the
    original provider evidence, rendered text, publication time, and
    point-in-time issuer candidates, but no deterministic/model label or price
    reaction until every annotation is frozen.
    """
    return build_acceptance_round(
        client,
        root,
        contract=v2_round_contract(human_root=human_root, teacher_root=teacher_root),
        report=report,
    )


def build_acceptance_round(
    client: ClickHouseHttpClient,
    root: Path,
    *,
    contract: AcceptanceRoundContract,
    report: Callable[[str], None] | None = None,
) -> AcceptanceBuildResult:
    """Build one immutable prediction-blind acceptance round from an explicit contract."""
    emit = report or (lambda _message: None)
    assert_runtime_root(root)
    if contract.sample_size != DEFAULT_SAMPLE_SIZE:
        raise ValueError("fresh acceptance rounds are frozen at exactly 100 articles")
    exclusions, exclusion_contract = load_supervision_exclusion(
        human_authorities=contract.prior_human_authorities,
        teacher_root=contract.teacher_root,
        teacher_expected=contract.teacher_expected,
    )
    manifest_path = root / "sample_manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        _validate_manifest(
            manifest,
            contract=contract,
            exclusion_contract=exclusion_contract,
        )
        return AcceptanceBuildResult(
            root=root,
            sample_count=int(manifest["sample_count"]),
            excluded_count=int(manifest["prior_supervision_exclusion"]["source_count"]),
            manifest_hash=str(manifest["sample_manifest_sha256"]),
        )
    emit("CANDIDATES | reading bounded year/session-diverse canonical pool")
    baseline = fetch_session_candidates(client, sampling_seed=contract.sampling_seed)
    candidates = merge_candidates((), baseline)
    for source_id in exclusions:
        candidates.pop(source_id, None)
    emit(
        f"EXCLUSION | prior_supervision={len(exclusions):,} "
        f"remaining_candidates={len(candidates):,}"
    )
    # Every bounded baseline row already contains canonical event metadata.
    # Hydrating a much larger label-first pool before selecting 100 articles is
    # both unnecessary and memory-inefficient.
    hydrate_event_metadata(client, candidates, report=emit)
    hydrate_complete_v5_units(client, candidates, report=emit)
    eligible = [
        row
        for row in candidates.values()
        if row.get("event")
        and START_YEAR <= source_year(row) <= END_YEAR
        and str(row["source_id"]) not in exclusions
    ]
    selected, allocation = select_session_balanced_candidates(
        eligible, sampling_seed=contract.sampling_seed
    )
    selected_ids = {str(row["source_id"]) for row in selected}
    if selected_ids & exclusions:
        raise RuntimeError("fresh acceptance round overlaps prior supervision")
    if len(selected_ids) != contract.sample_size:
        raise RuntimeError("fresh acceptance identities are missing or duplicated")

    emit(f"SELECTED | articles={len(selected):,}; loading complete text products")
    hydrate_text_products(client, selected, report=emit)
    emit("IDENTITY | loading point-in-time issuer authority")
    resolver = load_news_issuer_resolver(client)
    ordered = sorted(
        selected,
        key=lambda row: stable_json_hash(
            [contract.sampling_seed, "review-order", row["source_id"]]
        ),
    )
    manifest_items: list[dict[str, Any]] = []
    sealed_items: list[dict[str, Any]] = []
    for offset, row in enumerate(ordered):
        sample_id = f"N{contract.sample_id_start + offset:04d}"
        blinded = build_blinded_item(sample_id, row, resolver=resolver, pilot=False)
        blinded["sample_version"] = SAMPLE_VERSION
        blinded["reviewer_warning"] = (
            f"{contract.reviewer_label}: do not consult V5/V9/V10/Sol output, "
            "subsequent reaction, or sealed selection metadata before locking."
        )
        blinded_hash = stable_json_hash(blinded)
        blinded["blinded_item_sha256"] = blinded_hash
        write_json_atomic(root / "blinded_articles" / f"{sample_id}.json", blinded)
        write_json_atomic(
            root / "annotation_templates" / f"{sample_id}.json",
            annotation_template(blinded),
        )
        session = market_session(str(row["source_timestamp"]))
        manifest_items.append(
            {
                "sample_id": sample_id,
                "source_id": row["source_id"],
                "source_timestamp": row["source_timestamp"],
                "publication_session_et": session,
                "source_text_sha256": blinded["source_text_sha256"],
                "blinded_item_sha256": blinded_hash,
                "pilot": False,
            }
        )
        sealed_items.append(
            {
                "sample_id": sample_id,
                "source_id": row["source_id"],
                "locked_split": contract.locked_split,
                "selection_year": source_year(row),
                "publication_session_et": session,
                "selection_stratum": teacher_selection_stratum(row),
                "v5_diversification_hint": sealed_v5_summary(row),
            }
        )
        if (offset + 1) % 20 == 0:
            emit(f"WRITE | {contract.locked_split} blinded items={offset + 1:,}/{contract.sample_size:,}")

    selection_ids = [str(row["source_id"]) for row in ordered]
    manifest = {
        "sample_version": SAMPLE_VERSION,
        "collection_version": contract.collection_version,
        "sampling_seed": contract.sampling_seed,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sample_count": contract.sample_size,
        "pilot_count": 0,
        "selection_method": (
            "exact_bipartite_year_by_et_session_quota_then_deterministic_"
            "diversification_over_scope_role_origin_direction_eligibility_concept"
        ),
        "selection_sha256": stable_json_hash(selection_ids),
        "prior_supervision_exclusion": exclusion_contract,
        "required_session_distribution_et": dict(SESSION_QUOTAS),
        "year_session_allocation": allocation,
        "distribution": {
            **teacher_distribution(selected),
            "publication_session_et": _counts(
                market_session(str(row["source_timestamp"])) for row in selected
            ),
        },
        "blinding": {
            "visible": [
                "source and rendered text",
                "publication time",
                "provider metadata and ticker links",
                "point-in-time issuer identity matches",
            ],
            "sealed": [
                "V5 labels and diversification hints",
                "V9 and V10 predictions",
                "Sol labels",
                "subsequent price reaction",
            ],
        },
        "items": manifest_items,
    }
    manifest["sample_manifest_sha256"] = stable_json_hash(manifest)
    write_json_atomic(manifest_path, manifest)
    sealed = {
        "sample_version": SAMPLE_VERSION,
        "collection_version": contract.collection_version,
        "selection_sha256": manifest["selection_sha256"],
        "warning": "Do not open before all 100 manual annotations are frozen.",
        "items": sealed_items,
    }
    sealed["sealed_comparison_sha256"] = stable_json_hash(sealed)
    write_json_atomic(root / "sealed" / "v5_comparison_and_splits.json", sealed)
    write_json_atomic(
        root / "annotation_state_v3.json",
        {
            "annotation_version": ANNOTATION_VERSION_V3,
            "sample_manifest_sha256": manifest["sample_manifest_sha256"],
            "expected": contract.sample_size,
            "completed": 0,
            "remaining": contract.sample_size,
            "unexpected": [],
        },
    )
    emit(
        f"READY | {contract.locked_split}={contract.sample_size:,} excluded={len(exclusions):,} overlap=0 "
        f"sessions={dict(SESSION_QUOTAS)}"
    )
    return AcceptanceBuildResult(
        root=root,
        sample_count=contract.sample_size,
        excluded_count=len(exclusions),
        manifest_hash=str(manifest["sample_manifest_sha256"]),
    )


def select_session_balanced_candidates(
    candidates: Sequence[dict[str, Any]],
    *,
    sampling_seed: str = ACCEPTANCE_SEED,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    years = tuple(range(START_YEAR, END_YEAR + 1))
    year_quotas = {
        int(key): value
        for key, value in distribute_quota(
            DEFAULT_SAMPLE_SIZE, tuple(str(year) for year in years)
        ).items()
    }
    cells: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        cells[(source_year(row), market_session(str(row["source_timestamp"])))].append(row)
    allocation = _allocate_year_session(year_quotas, SESSION_QUOTAS, cells)
    selected: list[dict[str, Any]] = []
    for year in years:
        for session in SESSION_ORDER:
            target = allocation[year][session]
            if not target:
                continue
            selected.extend(
                round_robin_teacher_strata(
                    cells[(year, session)],
                    target=target,
                    sampling_seed=f"{sampling_seed}|{year}|{session}",
                )
            )
    if len(selected) != DEFAULT_SAMPLE_SIZE:
        raise RuntimeError(f"session-balanced selection returned {len(selected)} rows")
    return selected, {str(year): dict(allocation[year]) for year in years}


def fetch_session_candidates(
    client: ClickHouseHttpClient,
    *,
    per_year_session_limit: int = 96,
    sampling_seed: str = ACCEPTANCE_SEED,
) -> list[dict[str, Any]]:
    """Fetch a small canonical pool with explicit DST-aware ET session lanes."""
    local_minutes = (
        "toHour(toTimeZone(published_at_utc, 'America/New_York')) * 60 + "
        "toMinute(toTimeZone(published_at_utc, 'America/New_York'))"
    )
    session = (
        f"multiIf({local_minutes} >= 240 AND {local_minutes} < 570, 'premarket', "
        f"{local_minutes} >= 570 AND {local_minutes} < 960, 'regular', "
        f"{local_minutes} >= 960 AND {local_minutes} < 1200, 'after_hours', "
        "'overnight')"
    )
    sql = f"""
SELECT
 canonical_news_id AS source_id,
 toString(published_at_utc) AS source_timestamp,
 source_revision_key,
 provider_article_id,
 provider,
 title,
 teaser,
 author,
 article_url,
 url_domain,
 tickers,
 channels,
 provider_tags,
 content_quality_flags,
 raw_artifact_path,
 raw_payload_hash,
 {session} AS publication_session_et
FROM q_live.benzinga_news_event_v2 FINAL
WHERE published_at_utc >= toDateTime64('2010-01-01 00:00:00', 9, 'UTC')
  AND published_at_utc < toDateTime64('2027-01-01 00:00:00', 9, 'UTC')
ORDER BY cityHash64(concat(canonical_news_id, '{sampling_seed}'))
LIMIT {int(per_year_session_limit)} BY
 toYear(published_at_utc), publication_session_et
FORMAT JSONEachRow
"""
    return json_rows(client.execute(sql))


def _allocate_year_session(
    year_quotas: Mapping[int, int],
    session_quotas: Mapping[str, int],
    cells: Mapping[tuple[int, str], Sequence[Mapping[str, Any]]],
) -> dict[int, dict[str, int]]:
    """Solve exact year/session marginals with a deterministic max flow."""
    source = "source"
    sink = "sink"
    capacity: dict[tuple[str, str], int] = {}
    adjacency: dict[str, list[str]] = defaultdict(list)

    def edge(left: str, right: str, value: int) -> None:
        capacity[(left, right)] = value
        capacity.setdefault((right, left), 0)
        adjacency[left].append(right)
        adjacency[right].append(left)

    for year, quota in sorted(year_quotas.items()):
        year_node = f"year:{year}"
        edge(source, year_node, quota)
        for session in SESSION_ORDER:
            available = len(cells.get((year, session), ()))
            if available:
                edge(year_node, f"session:{session}", available)
    for session, quota in session_quotas.items():
        edge(f"session:{session}", sink, quota)

    flow: dict[tuple[str, str], int] = defaultdict(int)
    total = 0
    required = sum(year_quotas.values())
    while total < required:
        parent: dict[str, str | None] = {source: None}
        queue: deque[str] = deque([source])
        while queue and sink not in parent:
            node = queue.popleft()
            for neighbor in adjacency[node]:
                if neighbor in parent:
                    continue
                if capacity[(node, neighbor)] - flow[(node, neighbor)] <= 0:
                    continue
                parent[neighbor] = node
                queue.append(neighbor)
        if sink not in parent:
            raise RuntimeError(
                "candidate pool cannot satisfy exact year/session quotas; "
                f"allocated={total} required={required}"
            )
        increment = required - total
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            increment = min(
                increment,
                capacity[(previous, node)] - flow[(previous, node)],
            )
            node = previous
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            flow[(previous, node)] += increment
            flow[(node, previous)] -= increment
            node = previous
        total += increment

    allocation = {
        year: {
            session: flow[(f"year:{year}", f"session:{session}")]
            for session in SESSION_ORDER
        }
        for year in year_quotas
    }
    if any(sum(row.values()) != year_quotas[year] for year, row in allocation.items()):
        raise RuntimeError("year/session allocation violates year quotas")
    if any(
        sum(allocation[year][session] for year in allocation) != session_quotas[session]
        for session in SESSION_ORDER
    ):
        raise RuntimeError("year/session allocation violates session quotas")
    return allocation


def load_prior_supervision_exclusion(
    *, human_root: Path, teacher_root: Path
) -> tuple[set[str], dict[str, Any]]:
    return load_supervision_exclusion(
        human_authorities=((human_root, 1_100, "human-1100"),),
        teacher_root=teacher_root,
        teacher_expected=10_000,
    )


def load_supervision_exclusion(
    *,
    human_authorities: Sequence[tuple[Path, int, str]],
    teacher_root: Path,
    teacher_expected: int,
) -> tuple[set[str], dict[str, Any]]:
    human_ids: set[str] = set()
    human_contracts: list[dict[str, Any]] = []
    for root, expected, name in human_authorities:
        manifest = read_json(root / "sample_manifest.json")
        values = _manifest_source_ids(manifest, expected=expected, name=name)
        overlap = human_ids & values
        if overlap:
            raise RuntimeError(f"human authorities overlap at {name}: {len(overlap):,}")
        human_ids |= values
        human_contracts.append({
            "name": name,
            "manifest_sha256": str(manifest.get("sample_manifest_sha256") or ""),
            "source_count": len(values),
        })
    teacher = read_json(teacher_root / "sample_manifest.json")
    teacher_ids = _manifest_source_ids(
        teacher, expected=teacher_expected, name="Sol teacher"
    )
    teacher_ids = _manifest_source_ids(teacher, expected=10_000, name="Sol teacher")
    overlap = human_ids & teacher_ids
    if overlap:
        raise RuntimeError(f"existing human and teacher authorities overlap: {len(overlap):,}")
    source_ids = human_ids | teacher_ids
    return source_ids, {
        "human_authorities": human_contracts,
        "human_source_count": len(human_ids),
        "teacher_manifest_sha256": str(teacher.get("sample_manifest_sha256") or ""),
        "teacher_source_count": len(teacher_ids),
        "source_count": len(source_ids),
        "source_ids_sha256": stable_json_hash(sorted(source_ids)),
        "overlap_allowed": False,
    }


def _manifest_source_ids(
    manifest: Mapping[str, Any], *, expected: int, name: str
) -> set[str]:
    rows = manifest.get("items") or ()
    values = [str(row.get("source_id") or "") for row in rows]
    if int(manifest.get("sample_count") or 0) != expected or len(values) != expected:
        raise RuntimeError(f"{name} manifest must contain exactly {expected:,} rows")
    if any(not value for value in values) or len(set(values)) != expected:
        raise RuntimeError(f"{name} source identities are missing or duplicated")
    return set(values)


def _validate_manifest(
    manifest: Mapping[str, Any],
    *,
    contract: AcceptanceRoundContract,
    exclusion_contract: Mapping[str, Any],
) -> None:
    if manifest.get("sample_version") != SAMPLE_VERSION:
        raise RuntimeError("existing fresh acceptance v2 sample-contract drift")
    if manifest.get("collection_version") != contract.collection_version:
        raise RuntimeError("existing fresh acceptance collection drift")
    if manifest.get("sampling_seed") != contract.sampling_seed:
        raise RuntimeError("existing fresh acceptance sampling-seed drift")
    if int(manifest.get("sample_count") or 0) != contract.sample_size:
        raise RuntimeError("existing fresh acceptance sample-size drift")
    actual_exclusion = manifest.get("prior_supervision_exclusion")
    if actual_exclusion != exclusion_contract and not _legacy_v2_exclusion_matches(
        actual_exclusion, exclusion_contract
    ):
        raise RuntimeError("existing fresh acceptance exclusion drift")
    actual = manifest.get("distribution", {}).get("publication_session_et")
    if actual != dict(SESSION_QUOTAS):
        raise RuntimeError("existing fresh acceptance v2 session distribution drift")
    ids = [str(row.get("source_id") or "") for row in manifest.get("items") or ()]
    if len(ids) != contract.sample_size or len(set(ids)) != contract.sample_size:
        raise RuntimeError("existing fresh acceptance v2 identities are missing or duplicated")


def _legacy_v2_exclusion_matches(actual: Any, expected: Mapping[str, Any]) -> bool:
    """Accept the already-certified V2 manifest after the authority-list refactor."""
    authorities = expected.get("human_authorities") or ()
    if not isinstance(actual, Mapping) or len(authorities) != 1:
        return False
    authority = authorities[0]
    return all(
        (
            actual.get("human_manifest_sha256") == authority.get("manifest_sha256"),
            actual.get("human_source_count") == authority.get("source_count"),
            actual.get("teacher_manifest_sha256") == expected.get("teacher_manifest_sha256"),
            actual.get("teacher_source_count") == expected.get("teacher_source_count"),
            actual.get("source_count") == expected.get("source_count"),
            actual.get("source_ids_sha256") == expected.get("source_ids_sha256"),
            actual.get("overlap_allowed") is False,
        )
    )


def _counts(values: Iterable[str]) -> dict[str, int]:
    output = {key: 0 for key in SESSION_ORDER}
    for value in values:
        output[value] = output.get(value, 0) + 1
    return output
