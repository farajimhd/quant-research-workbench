from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipelines.news.benzinga.core.contracts import UrlPolicyEntry
from pipelines.news.benzinga.news_benzinga_normalize import BENZINGA_PROVIDER, stable_hash
from pipelines.news.benzinga.news_benzinga_url_fetch_plan import DEFAULT_DOMAIN_POLICY


POLICY_TABLE_COLUMNS = [
    "policy_id",
    "policy_version",
    "provider",
    "match_type",
    "match_value",
    "action",
    "priority",
    "enabled",
    "reason",
    "source",
    "created_at_utc",
    "updated_at_utc",
]

VALID_ACTIONS = {"ignore", "metadata_only", "fetch_html", "fetch_pdf", "resolve_redirect", "sec_handler", "review"}
MATCH_PRIORITY = {
    "url_regex": 100,
    "path_regex": 90,
    "content_type": 80,
    "exact_domain": 70,
    "registered_domain": 60,
    "domain": 50,
}


def load_policy(policy_json: str | Path | None = None) -> dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_DOMAIN_POLICY))
    if not policy_json:
        return policy
    override = json.loads(Path(policy_json).read_text(encoding="utf-8"))
    policy["exact_domain_actions"].update(override.get("exact_domain_actions") or {})
    policy["registered_domain_actions"].update(override.get("registered_domain_actions") or {})
    policy["domain_actions"].update(override.get("domain_actions") or {})
    policy["version"] = str(override.get("version") or policy["version"])
    return policy


def policy_version(policy: dict[str, Any]) -> str:
    return str(policy.get("version") or "benzinga-url-domain-policy-v1")


def policy_to_entries(policy: dict[str, Any], *, provider: str = BENZINGA_PROVIDER, source: str = "default") -> list[UrlPolicyEntry]:
    version = policy_version(policy)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    entries: list[UrlPolicyEntry] = []
    for match_type, key in [
        ("exact_domain", "exact_domain_actions"),
        ("registered_domain", "registered_domain_actions"),
        ("domain", "domain_actions"),
    ]:
        actions = policy.get(key) or {}
        for match_value, action in sorted(actions.items()):
            action_text = str(action or "").strip()
            if action_text not in VALID_ACTIONS:
                raise ValueError(f"invalid policy action={action_text!r} match_type={match_type} match_value={match_value}")
            policy_id = stable_hash("|".join([provider, version, match_type, str(match_value), action_text]))
            entries.append(
                UrlPolicyEntry(
                    policy_id=policy_id,
                    policy_version=version,
                    provider=provider,
                    match_type=match_type,
                    match_value=str(match_value),
                    action=action_text,
                    priority=MATCH_PRIORITY.get(match_type, 0),
                    enabled=1,
                    reason=f"{source}:{match_type}",
                    source=source,
                    created_at_utc=now,
                    updated_at_utc=now,
                )
            )
    return entries


def entries_to_policy(entries: list[UrlPolicyEntry]) -> dict[str, Any]:
    version = entries[0].policy_version if entries else "benzinga-url-domain-policy-v1"
    policy = {
        "version": version,
        "exact_domain_actions": {},
        "registered_domain_actions": {},
        "domain_actions": {},
    }
    for entry in sorted(entries, key=lambda item: (-item.priority, item.match_type, item.match_value)):
        if not entry.enabled:
            continue
        if entry.match_type == "exact_domain":
            policy["exact_domain_actions"][entry.match_value] = entry.action
        elif entry.match_type == "registered_domain":
            policy["registered_domain_actions"][entry.match_value] = entry.action
        elif entry.match_type == "domain":
            policy["domain_actions"][entry.match_value] = entry.action
    return policy


def policy_counts(entries: list[UrlPolicyEntry]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for entry in entries:
        counts[f"match_type:{entry.match_type}"] += 1
        counts[f"action:{entry.action}"] += 1
        if entry.enabled:
            counts["enabled"] += 1
    counts["rows"] = len(entries)
    return dict(counts)


def create_policy_table_sql(database: str, table: str, *, storage_policy: str = "") -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        escaped = storage_policy.strip().replace("\\", "\\\\").replace("'", "\\'")
        settings.append(f"storage_policy = '{escaped}'")
    return f"""
CREATE TABLE IF NOT EXISTS `{database}`.`{table}`
(
    policy_id String,
    policy_version LowCardinality(String),
    provider LowCardinality(String),
    match_type LowCardinality(String),
    match_value String,
    action LowCardinality(String),
    priority Int32,
    enabled UInt8,
    reason String,
    source LowCardinality(String),
    created_at_utc DateTime64(9, 'UTC'),
    updated_at_utc DateTime64(9, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY (provider, policy_version, match_type, match_value, policy_id)
SETTINGS {", ".join(settings)}
"""
