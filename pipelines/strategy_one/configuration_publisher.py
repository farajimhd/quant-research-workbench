"""Coverage-last publication of one immutable, normalized Strategy 1 release.

This module is producer-owned. Backtest and live runtimes only call the
separate SELECT-only release reader. JSON is an HTTP transport format here;
no JSON value or blob is inserted into the typed ClickHouse tables.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import uuid4

from src.backend.backtest_strategy_one_configuration import (
    certify_strategy_one_configuration, certify_numbered_configuration,
    is_numbered_fixed_configuration, _validate_strategy_two_payload,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import (
    NODE_TABLE, RELEASE_TABLE, decode_nodes, encode_nodes, node_hash,
    verify_tables,
)
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


_HEX = re.compile(r"[0-9a-f]{64}\Z")


def publication_envelope(source: Mapping[str, Any]) -> dict[str, Any]:
    """Compile the legacy candidate once without altering its disk record."""
    from pipelines.strategy_one.configuration_migration import (
        compile_strategy_one_configuration,
    )

    payload, payload_hash, nodes_hash, count = compile_strategy_one_configuration(source)
    return {
        "source_candidate_id": source["revision_id"],
        "source_candidate_hash": source["content_hash"],
        "payload_hash": payload_hash,
        "node_hash": nodes_hash,
        "node_count": count,
        "payload": payload,
    }


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(
        sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _verified_envelope(envelope: Mapping[str, Any]) -> tuple[dict, tuple[dict, ...]]:
    from pipelines.strategy_one.configuration_migration import (
        SOURCE_CANDIDATE_HASH, SOURCE_CANDIDATE_ID,
    )

    if (not isinstance(envelope, Mapping)
            or set(envelope) != {"source_candidate_id", "source_candidate_hash",
                                    "payload_hash", "node_hash", "node_count", "payload"}
            or envelope["source_candidate_id"] != SOURCE_CANDIDATE_ID
            or envelope["source_candidate_hash"] != SOURCE_CANDIDATE_HASH
            or any(not isinstance(envelope[key], str)
                   or _HEX.fullmatch(envelope[key]) is None
                   for key in ("payload_hash", "node_hash"))
            or not isinstance(envelope["payload"], dict)):
        raise ValueError("Strategy 1 transfer envelope has a foreign source or shape")
    payload = envelope["payload"]
    strategy = dict(payload.get("strategy") or {})
    if (strategy.get("strategy_id") != STRATEGY_ID
            or strategy.get("strategy_number") != STRATEGY_NUMBER
            or strategy.get("revision") != STRATEGY_NUMBER
            or strategy.get("execution_interval") != "100ms"):
        raise ValueError("Strategy 1 transfer has a foreign numbered contract")
    nodes = encode_nodes(payload)
    digest = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    if (digest != envelope["payload_hash"]
            or node_hash(nodes) != envelope["node_hash"]
            or len(nodes) != envelope["node_count"]):
        raise ValueError("Strategy 1 transfer does not match its typed content seal")
    # The source snapshot itself is read only on the laptop; SSH authentication
    # and the transfer seal bind that trusted emitter to this receiver.
    return payload, nodes


def _insert_rows(client: Any, table: str, columns: tuple[str, ...],
                 rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("Strategy 1 publication cannot insert an empty batch")
    body = "\n".join(json.dumps(row, separators=(",", ":"),
                                 ensure_ascii=True, allow_nan=False)
                     for row in rows)
    client.execute(f"INSERT INTO {table} ({','.join(columns)}) "
                   "FORMAT JSONEachRow\n" + body)


def publish_configuration(client: Any, keeper: Any,
                          envelope: Mapping[str, Any]) -> str:
    """Publish nodes, verify their typed readback, then publish one release.

    An ambiguous node INSERT leaves an invisible orphan attempt. An ambiguous
    release INSERT must be resolved by the release reader, never blind retry.
    """
    number = dict(dict(envelope.get("payload") or {}).get("strategy") or {}).get("strategy_number")
    if number == 1:
        payload, nodes = _verified_envelope(envelope)
    elif type(number) is int and number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24):
        payload, nodes = _verified_numbered_envelope(envelope)
        if number == 2:
            from pipelines.strategy_one.strategy_two_configuration import compile_strategy_two_configuration as compile_configuration
        elif number == 3:
            from pipelines.strategy_one.strategy_three_configuration import compile_strategy_three_configuration as compile_configuration
        elif number == 4:
            from pipelines.strategy_one.strategy_four_configuration import compile_strategy_four_configuration as compile_configuration
        elif number == 5:
            from pipelines.strategy_one.strategy_five_configuration import compile_strategy_five_configuration as compile_configuration
        elif number == 6:
            from pipelines.strategy_one.strategy_six_configuration import compile_strategy_six_configuration as compile_configuration
        elif number == 7:
            from pipelines.strategy_one.strategy_seven_configuration import compile_strategy_seven_configuration as compile_configuration
        elif number == 8:
            from pipelines.strategy_one.strategy_eight_configuration import compile_strategy_eight_configuration as compile_configuration
        elif number == 9:
            from pipelines.strategy_one.strategy_nine_configuration import compile_strategy_nine_configuration as compile_configuration
        elif number == 10:
            from pipelines.strategy_one.strategy_ten_configuration import compile_strategy_ten_configuration as compile_configuration
        elif number == 11:
            from pipelines.strategy_one.strategy_eleven_configuration import compile_strategy_eleven_configuration as compile_configuration
        elif number == 12:
            from pipelines.strategy_one.strategy_twelve_configuration import compile_strategy_twelve_configuration as compile_configuration
        elif number == 13:
            from pipelines.strategy_one.strategy_thirteen_configuration import compile_strategy_thirteen_configuration as compile_configuration
        elif number == 14:
            from pipelines.strategy_one.strategy_fourteen_configuration import compile_strategy_fourteen_configuration as compile_configuration
        elif number == 15:
            from pipelines.strategy_one.strategy_fifteen_configuration import compile_strategy_fifteen_configuration as compile_configuration
        elif number == 16:
            from pipelines.strategy_one.strategy_sixteen_configuration import compile_strategy_sixteen_configuration as compile_configuration
        elif number == 17:
            from pipelines.strategy_one.strategy_seventeen_configuration import compile_strategy_seventeen_configuration as compile_configuration
        elif number == 18:
            from pipelines.strategy_one.strategy_eighteen_configuration import compile_strategy_eighteen_configuration as compile_configuration
        elif number == 19:
            from pipelines.strategy_one.strategy_nineteen_configuration import compile_strategy_nineteen_configuration as compile_configuration
        elif number == 20:
            from pipelines.strategy_one.strategy_twenty_configuration import compile_strategy_twenty_configuration as compile_configuration
        elif number == 21:
            from pipelines.strategy_one.strategy_twenty_one_configuration import compile_strategy_twenty_one_configuration as compile_configuration
        elif number == 22:
            from pipelines.strategy_one.strategy_twenty_two_configuration import compile_strategy_twenty_two_configuration as compile_configuration
        elif number == 23:
            from pipelines.strategy_one.strategy_twenty_three_configuration import compile_strategy_twenty_three_configuration as compile_configuration
        else:
            from pipelines.strategy_one.strategy_twenty_four_configuration import compile_strategy_twenty_four_configuration as compile_configuration
        from src.trading_runtime.strategy_registry import numbered_strategy_parent
        source = certify_numbered_configuration(client, numbered_strategy_parent(number))
        manifest = payload["strategy"]["numbered_release"]
        expected = compile_configuration(source,
            approved_code_commit=manifest["approved_code_commit"],
            approved_code_fingerprint=manifest["approved_code_fingerprint"],
            approval_reference=manifest["approval_reference"])
        if dict(envelope) != expected:
            raise ValueError("Numbered publication differs from certified inheritance")
    else:
        raise ValueError("Unknown numbered configuration publication")
    try:
        verify_tables(client)
    except Exception as exc:
        raise PublicationStageError("layout", exc) from exc
    lock_path = f"/trading/ownership/v1/strategy_one_configuration/{number}"
    try:
        keeper.create(lock_path, uuid4().hex.encode("ascii"),
                      ephemeral=True, makepath=True)
    except Exception as exc:
        if type(exc).__name__ == "NodeExistsError":
            raise RuntimeError(f"Another Strategy {number} configuration publisher owns the release") from exc
        raise
    stage = "existing_release"
    try:
        existing = _rows(client, "SELECT release_attempt_id,payload_hash "
                         f"FROM {RELEASE_TABLE} WHERE strategy_number={number}")
        if existing:
            if len(existing) == 1 and existing[0]["payload_hash"] == envelope["payload_hash"]:
                certified = certify_numbered_configuration(client, number)
                if certified.source_candidate_hash != envelope["source_candidate_hash"]:
                    raise RuntimeError(f"Strategy {number} existing source differs")
                return certified.token
            raise RuntimeError(f"Strategy {number} already has a different immutable release")
        attempt = str(uuid4())
        columns = ("strategy_number", "release_attempt_id", "node_id",
                   "parent_node_id", "child_key", "child_ordinal", "value_kind",
                   "text_value", "int_value", "float_value", "bool_value")
        for start in range(0, len(nodes), 500):
            stage = "node_insert"
            if not keeper.connected:
                raise RuntimeError(f"Strategy {number} Keeper claim was lost")
            batch = [dict(strategy_number=number,
                          release_attempt_id=attempt, **row)
                     for row in nodes[start:start + 500]]
            _insert_rows(client, NODE_TABLE, columns, batch)
        stage = "node_readback"
        readback = _rows(client,
            "SELECT node_id,parent_node_id,child_key,child_ordinal,value_kind,"
            "text_value,int_value,float_value,bool_value "
            f"FROM {NODE_TABLE} WHERE strategy_number={number} "
            f"AND release_attempt_id=toUUID('{attempt}') ORDER BY node_id")
        if len(readback) != len(nodes):
            raise RuntimeError(
                f"node_count:{len(readback)}:expected:{len(nodes)}")
        actual_hash = node_hash(readback)
        if actual_hash != envelope["node_hash"]:
            same_values = all(
                all(actual[key] == expected[key] for key in expected)
                for actual, expected in zip(readback, nodes))
            raise RuntimeError(
                f"node_hash:{actual_hash}:expected:{envelope['node_hash']}"
                f":equal_values:{int(same_values)}")
        if decode_nodes(readback) != payload:
            raise RuntimeError("node_payload_mismatch")
        if not keeper.connected:
            raise RuntimeError(f"Strategy {number} Keeper claim was lost before release")
        release = {
            "strategy_number": number,
            "release_attempt_id": attempt,
            "strategy_id": STRATEGY_ID,
            "source_candidate_id": envelope["source_candidate_id"],
            "source_candidate_hash": envelope["source_candidate_hash"],
            "payload_hash": envelope["payload_hash"],
            "node_count": len(nodes), "node_hash": envelope["node_hash"],
            "published_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S.%f"),
        }
        stage = "release_insert"
        _insert_rows(client, RELEASE_TABLE, tuple(release), [release])
        stage = "release_readback"
        certified = certify_numbered_configuration(client, number)
        if certified.attempt_id != attempt or certified.payload != payload:
            raise RuntimeError(f"Strategy {number} released rows differ from typed transfer")
        return certified.token
    except Exception as exc:
        if isinstance(exc, PublicationStageError):
            raise
        raise PublicationStageError(stage, exc) from exc
    finally:
        try:
            keeper.delete(lock_path)
        except Exception:
            pass


class PublicationStageError(RuntimeError):
    """Sanitized stage and numeric server codes; never retain response text."""

    def __init__(self, stage: str, cause: Exception) -> None:
        status = getattr(cause, "status_code", None)
        code_match = re.search(r"Code: ([0-9]{1,4})\b", str(cause))
        code = code_match.group(1) if code_match else "unknown"
        self.safe_diagnostic = (
            f"{stage}:HTTP{status if type(status) is int else 'unknown'}:CH{code}"
            f":{type(cause).__name__}"
            + (f":{str(cause)}" if isinstance(cause, RuntimeError)
               and str(cause).startswith(("node_count:", "node_hash:",
                                           "node_payload_mismatch")) else ""))
        super().__init__(self.safe_diagnostic)


def _verified_numbered_envelope(envelope: Mapping[str, Any]) -> tuple[dict, tuple[dict, ...]]:
    """Later numbers have independent approval and source identity; no Candidate 350 reuse."""
    if set(envelope) != {"source_candidate_id", "source_candidate_hash", "payload_hash", "node_hash", "node_count", "payload"}:
        raise ValueError("Numbered configuration envelope shape differs")
    payload = envelope["payload"]
    if not is_numbered_fixed_configuration(payload) or payload["strategy"]["strategy_number"] not in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24):
        raise ValueError("Numbered publisher requires sealed Strategy 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 or 12")
    _validate_strategy_two_payload(payload)
    manifest = payload["strategy"]["numbered_release"]
    source_prefix = {2: "strategy-two-from", 3: "strategy-three-from", 4: "strategy-four-from", 5: "strategy-five-from", 6: "strategy-six-from", 7: "strategy-seven-from", 8: "strategy-eight-from", 9: "strategy-nine-from", 10: "strategy-ten-from", 11: "strategy-eleven-from", 12: "strategy-twelve-from", 13: "strategy-thirteen-from", 14: "strategy-fourteen-from", 15: "strategy-fifteen-from", 16: "strategy-sixteen-from", 17: "strategy-seventeen-from", 18: "strategy-eighteen-from", 19: "strategy-nineteen-from", 20: "strategy-twenty-from", 21: "strategy-twenty-one-from", 22: "strategy-twenty-two-from", 23: "strategy-twenty-three-from", 24: "strategy-twenty-four-from"}[payload["strategy"]["strategy_number"]]
    if (envelope["source_candidate_id"] != f"{source_prefix}:{manifest['source_revision_id']}"
            or envelope["source_candidate_hash"] != manifest["source_payload_hash"]):
        raise ValueError("Numbered source provenance differs")
    nodes = encode_nodes(payload)
    if (sha256(canonical_json(payload).encode()).hexdigest() != envelope["payload_hash"]
            or node_hash(nodes) != envelope["node_hash"] or len(nodes) != envelope["node_count"]):
        raise ValueError("Numbered typed content seal differs")
    return payload, nodes
