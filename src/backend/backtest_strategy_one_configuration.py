"""Read-only Strategy 1 configuration release from normalized ARTE rows."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any

from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import (
    NODE_TABLE, RELEASE_TABLE, decode_nodes, node_hash,
)
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


_HEX = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class CertifiedStrategyOneConfiguration:
    attempt_id: str
    payload_hash: str
    node_hash: str
    source_candidate_id: str
    source_candidate_hash: str
    token: str
    payload: dict[str, Any]

    def revision(self) -> dict[str, Any]:
        """App-facing identity without consulting an old SQLite candidate."""
        return {
            "revision_id": f"strategy-one-{STRATEGY_NUMBER}:{self.attempt_id}",
            "revision": STRATEGY_NUMBER,
            "label": f"Strategy {STRATEGY_NUMBER}",
            "release_state": "test_candidate",
            "content_hash": self.payload_hash,
            "payload": self.payload,
        }


def certify_strategy_one_configuration(client: Any) -> CertifiedStrategyOneConfiguration:
    """Exactly one coverage-last release may own immutable Strategy number 1."""
    releases = [json.loads(line) for line in client.execute(
        "SELECT release_attempt_id,strategy_id,source_candidate_id,"
        "source_candidate_hash,payload_hash,node_count,node_hash "
        f"FROM {RELEASE_TABLE} WHERE strategy_number={STRATEGY_NUMBER} "
        "FORMAT JSONEachRow").splitlines() if line.strip()]
    if len(releases) != 1:
        raise RuntimeError("Strategy 1 needs exactly one immutable typed configuration release")
    release = releases[0]
    attempt = str(release.get("release_attempt_id") or "")
    if (release.get("strategy_id") != STRATEGY_ID
            or not re.fullmatch(r"[0-9a-fA-F-]{36}", attempt)
            or not release.get("source_candidate_id")
            or type(release.get("node_count")) is not int
            or not 1 <= release["node_count"] <= 10_000
            or any(not _HEX.fullmatch(str(release.get(key) or ""))
                   for key in ("source_candidate_hash", "payload_hash", "node_hash"))):
        raise RuntimeError("Strategy 1 typed release seal is invalid")
    rows = [json.loads(line) for line in client.execute(
        "SELECT node_id,parent_node_id,child_key,child_ordinal,value_kind,"
        "text_value,int_value,float_value,bool_value "
        f"FROM {NODE_TABLE} WHERE strategy_number={STRATEGY_NUMBER} "
        f"AND release_attempt_id=toUUID('{attempt}') "
        "ORDER BY node_id FORMAT JSONEachRow").splitlines() if line.strip()]
    if len(rows) != release["node_count"] or node_hash(rows) != release["node_hash"]:
        raise RuntimeError("Strategy 1 typed nodes differ from release seal")
    payload = decode_nodes(rows)
    digest = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    strategy = dict(payload.get("strategy") or {})
    if (digest != release["payload_hash"]
            or strategy.get("strategy_id") != STRATEGY_ID
            or strategy.get("revision") != STRATEGY_NUMBER
            or strategy.get("strategy_number") != STRATEGY_NUMBER
            or strategy.get("execution_interval") != "100ms"):
        raise RuntimeError("Strategy 1 typed configuration is not the numbered 100ms contract")
    token = sha256(canonical_json((STRATEGY_NUMBER, attempt, digest,
                                   release["node_hash"])).encode("utf-8")).hexdigest()
    return CertifiedStrategyOneConfiguration(
        attempt, digest, release["node_hash"], release["source_candidate_id"],
        release["source_candidate_hash"], token, payload)
