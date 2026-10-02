"""Read-only Strategy 1 configuration release from normalized ARTE rows."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
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

    @property
    def strategy_number(self) -> int:
        return int(self.payload["strategy"]["strategy_number"])

    def revision(self) -> dict[str, Any]:
        """App-facing identity without consulting an old SQLite candidate."""
        number = self.strategy_number
        plan = dict(self.payload.get("run_plan") or {})
        return {
            "revision_id": f"strategy-one-{number}:{self.attempt_id}",
            "revision": number,
            "label": f"Strategy {number}",
            "release_state": "test_candidate",
            "content_hash": self.payload_hash,
            "run_plan_id": str(plan.get("run_plan_id") or ""),
            "available_run_plans": [{
                "run_plan_id": str(plan.get("run_plan_id") or ""),
                "name": f"Strategy {number}",
                "strategy_id": STRATEGY_ID,
                "strategy_revision": number,
                "profile_id": f"strategy-one-{number}",
            }],
            "payload": self.payload,
        }


def certify_numbered_configuration(client: Any, strategy_number: int = 1) -> CertifiedStrategyOneConfiguration:
    """Exactly one coverage-last release may own each supported immutable number."""
    if type(strategy_number) is not int or strategy_number not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37):
        raise ValueError("Unsupported numbered fixed strategy")
    releases = [json.loads(line) for line in client.execute(
        "SELECT release_attempt_id,strategy_id,source_candidate_id,"
        "source_candidate_hash,payload_hash,node_count,node_hash "
        f"FROM {RELEASE_TABLE} WHERE strategy_number={strategy_number} "
        "FORMAT JSONEachRow").splitlines() if line.strip()]
    if len(releases) != 1:
        raise RuntimeError(f"Strategy {strategy_number} needs exactly one immutable typed configuration release")
    release = releases[0]
    attempt = str(release.get("release_attempt_id") or "")
    if (release.get("strategy_id") != STRATEGY_ID
            or not re.fullmatch(r"[0-9a-fA-F-]{36}", attempt)
            or not release.get("source_candidate_id")
            or type(release.get("node_count")) is not int
            or not 1 <= release["node_count"] <= 10_000
            or any(not _HEX.fullmatch(str(release.get(key) or ""))
                   for key in ("source_candidate_hash", "payload_hash", "node_hash"))):
        raise RuntimeError(f"Strategy {strategy_number} typed release seal is invalid")
    rows = [json.loads(line) for line in client.execute(
        "SELECT node_id,parent_node_id,child_key,child_ordinal,value_kind,"
        "text_value,int_value,float_value,bool_value "
        f"FROM {NODE_TABLE} WHERE strategy_number={strategy_number} "
        f"AND release_attempt_id=toUUID('{attempt}') "
        "ORDER BY node_id FORMAT JSONEachRow").splitlines() if line.strip()]
    if len(rows) != release["node_count"] or node_hash(rows) != release["node_hash"]:
        raise RuntimeError(f"Strategy {strategy_number} typed nodes differ from release seal")
    payload = decode_nodes(rows)
    digest = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    strategy = dict(payload.get("strategy") or {})
    if (digest != release["payload_hash"]
            or strategy.get("strategy_id") != STRATEGY_ID
            or strategy.get("revision") != strategy_number
            or strategy.get("strategy_number") != strategy_number
            or strategy.get("execution_interval") != "100ms"):
        raise RuntimeError(f"Strategy {strategy_number} typed configuration is not the numbered 100ms contract")
    if strategy_number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37):
        is_numbered_fixed_configuration(payload)
        _validate_strategy_two_payload(payload)
        from src.trading_runtime.strategy_registry import numbered_strategy_parent
        source_number = numbered_strategy_parent(strategy_number)
        source = certify_numbered_configuration(client, source_number)
        if strategy_number == 2:
            from src.trading_runtime.strategy_two_release import derive_strategy_two_configuration as derive
        elif strategy_number == 3:
            from src.trading_runtime.strategy_three_release import derive_strategy_three_configuration as derive
        elif strategy_number == 4:
            from src.trading_runtime.strategy_four_release import derive_strategy_four_configuration as derive
        elif strategy_number == 5:
            from src.trading_runtime.strategy_five_release import derive_strategy_five_configuration as derive
        elif strategy_number == 6:
            from src.trading_runtime.strategy_six_release import derive_strategy_six_configuration as derive
        elif strategy_number == 7:
            from src.trading_runtime.strategy_seven_release import derive_strategy_seven_configuration as derive
        elif strategy_number == 8:
            from src.trading_runtime.strategy_eight_release import derive_strategy_eight_configuration as derive
        elif strategy_number == 9:
            from src.trading_runtime.strategy_nine_release import derive_strategy_nine_configuration as derive
        elif strategy_number == 10:
            from src.trading_runtime.strategy_ten_release import derive_strategy_ten_configuration as derive
        elif strategy_number == 11:
            from src.trading_runtime.strategy_eleven_release import derive_strategy_eleven_configuration as derive
        elif strategy_number == 12:
            from src.trading_runtime.strategy_twelve_release import derive_strategy_twelve_configuration as derive
        elif strategy_number == 13:
            from src.trading_runtime.strategy_thirteen_release import derive_strategy_thirteen_configuration as derive
        elif strategy_number == 14:
            from src.trading_runtime.strategy_fourteen_release import derive_strategy_fourteen_configuration as derive
        elif strategy_number == 15:
            from src.trading_runtime.strategy_fifteen_release import derive_strategy_fifteen_configuration as derive
        elif strategy_number == 16:
            from src.trading_runtime.strategy_sixteen_release import derive_strategy_sixteen_configuration as derive
        elif strategy_number == 17:
            from src.trading_runtime.strategy_seventeen_release import derive_strategy_seventeen_configuration as derive
        elif strategy_number == 18:
            from src.trading_runtime.strategy_eighteen_release import derive_strategy_eighteen_configuration as derive
        elif strategy_number == 19:
            from src.trading_runtime.strategy_nineteen_release import derive_strategy_nineteen_configuration as derive
        elif strategy_number == 20:
            from src.trading_runtime.strategy_twenty_release import derive_strategy_twenty_configuration as derive
        elif strategy_number == 21:
            from src.trading_runtime.strategy_twenty_one_release import derive_strategy_twenty_one_configuration as derive
        elif strategy_number == 22:
            from src.trading_runtime.strategy_twenty_two_release import derive_strategy_twenty_two_configuration as derive
        elif strategy_number == 23:
            from src.trading_runtime.strategy_twenty_three_release import derive_strategy_twenty_three_configuration as derive
        elif strategy_number == 24:
            from src.trading_runtime.strategy_twenty_four_release import derive_strategy_twenty_four_configuration as derive
        elif strategy_number == 25:
            from src.trading_runtime.strategy_twenty_five_release import derive_strategy_twenty_five_configuration as derive
        elif strategy_number == 26:
            from src.trading_runtime.strategy_twenty_six_release import derive_strategy_twenty_six_configuration as derive
        elif strategy_number == 27:
            from src.trading_runtime.strategy_twenty_seven_release import derive_strategy_twenty_seven_configuration as derive
        elif strategy_number == 28:
            from src.trading_runtime.strategy_twenty_eight_release import derive_strategy_twenty_eight_configuration as derive
        elif strategy_number == 29:
            from src.trading_runtime.strategy_twenty_nine_release import derive_strategy_twenty_nine_configuration as derive
        elif strategy_number == 30:
            from src.trading_runtime.strategy_thirty_release import derive_strategy_thirty_configuration as derive
        elif strategy_number == 31:
            from src.trading_runtime.strategy_thirty_one_release import derive_strategy_thirty_one_configuration as derive
        elif strategy_number == 32:
            from src.trading_runtime.strategy_thirty_two_release import derive_strategy_thirty_two_configuration as derive
        elif strategy_number == 33:
            from src.trading_runtime.strategy_thirty_three_release import derive_strategy_thirty_three_configuration as derive
        elif strategy_number == 34:
            from src.trading_runtime.strategy_thirty_four_release import derive_strategy_thirty_four_configuration as derive
        elif strategy_number == 35:
            from src.trading_runtime.strategy_thirty_five_release import derive_strategy_thirty_five_configuration as derive
        elif strategy_number == 36:
            from src.trading_runtime.strategy_thirty_six_release import derive_strategy_thirty_six_configuration as derive
        else:
            from src.trading_runtime.strategy_thirty_seven_release import derive_strategy_thirty_seven_configuration as derive
        manifest = strategy["numbered_release"]
        expected = derive(source,
            approved_code_commit=manifest["approved_code_commit"],
            approved_code_fingerprint=manifest["approved_code_fingerprint"],
            approval_reference=manifest["approval_reference"])
        if expected["payload"] != payload or release["source_candidate_id"] != expected["source_candidate_id"] or release["source_candidate_hash"] != expected["source_candidate_hash"]:
            raise RuntimeError(f"Strategy {strategy_number} differs from its certified inheritance and approved policy")
    token = sha256(canonical_json((strategy_number, attempt, digest,
                                   release["node_hash"])).encode("utf-8")).hexdigest()
    return CertifiedStrategyOneConfiguration(
        attempt, digest, release["node_hash"], release["source_candidate_id"],
        release["source_candidate_hash"], token, payload)


def selected_strategy_one_revision(*, revision_id: str = "",
                                   run_plan_id: str = "",
                                   client: Any | None = None) -> dict[str, Any]:
    """Resolve only the numbered ARTE release for app Backtest selection.

    A caller-supplied client is for read-only tests. Normal requests create
    the V3 SELECT-only Backtest principal, never a producer credential.
    """
    if client is None:
        from src.backend.backtest_market_data import readonly_clickhouse_client
        with closing(readonly_clickhouse_client(v3_read_principal=True)) as reader:
            return selected_strategy_one_revision(
                revision_id=revision_id, run_plan_id=run_plan_id, client=reader)
    certified = certify_strategy_one_configuration(client)
    revision = certified.revision()
    if revision_id and revision_id != revision["revision_id"]:
        raise ValueError("Only the immutable Strategy 1 configuration can Backtest")
    selected_plan = str(dict(certified.payload.get("run_plan") or {}).get(
        "run_plan_id") or "")
    if not selected_plan or run_plan_id and run_plan_id != selected_plan:
        raise ValueError("Backtest Run Plan differs from the Strategy 1 release")
    return revision


def certify_strategy_one_configuration(client: Any) -> CertifiedStrategyOneConfiguration:
    """Grandfathered Strategy 1 reader; its seal and identity remain unchanged."""
    return certify_numbered_configuration(client, 1)


def is_numbered_fixed_configuration(configuration: dict[str, Any]) -> bool:
    """Route legacy separately; later numbers require their complete installed seal."""
    strategy = dict(configuration.get("strategy") or {})
    number = strategy.get("strategy_number")
    if number is None:
        return False
    if type(number) is not int or number not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37):
        raise ValueError("Unknown numbered fixed strategy")
    if number == 1:
        return True  # Existing full-release boundaries retain exact identity checks.
    if number == 2:
        from src.trading_runtime.strategy_two_release import verify_strategy_two_manifest as verify
    elif number == 3:
        from src.trading_runtime.strategy_three_release import verify_strategy_three_manifest as verify
    elif number == 4:
        from src.trading_runtime.strategy_four_release import verify_strategy_four_manifest as verify
    elif number == 5:
        from src.trading_runtime.strategy_five_release import verify_strategy_five_manifest as verify
    elif number == 6:
        from src.trading_runtime.strategy_six_release import verify_strategy_six_manifest as verify
    elif number == 7:
        from src.trading_runtime.strategy_seven_release import verify_strategy_seven_manifest as verify
    elif number == 8:
        from src.trading_runtime.strategy_eight_release import verify_strategy_eight_manifest as verify
    elif number == 9:
        from src.trading_runtime.strategy_nine_release import verify_strategy_nine_manifest as verify
    elif number == 10:
        from src.trading_runtime.strategy_ten_release import verify_strategy_ten_manifest as verify
    elif number == 11:
        from src.trading_runtime.strategy_eleven_release import verify_strategy_eleven_manifest as verify
    elif number == 12:
        from src.trading_runtime.strategy_twelve_release import verify_strategy_twelve_manifest as verify
    elif number == 13:
        from src.trading_runtime.strategy_thirteen_release import verify_strategy_thirteen_manifest as verify
    elif number == 14:
        from src.trading_runtime.strategy_fourteen_release import verify_strategy_fourteen_manifest as verify
    elif number == 15:
        from src.trading_runtime.strategy_fifteen_release import verify_strategy_fifteen_manifest as verify
    elif number == 16:
        from src.trading_runtime.strategy_sixteen_release import verify_strategy_sixteen_manifest as verify
    elif number == 17:
        from src.trading_runtime.strategy_seventeen_release import verify_strategy_seventeen_manifest as verify
    elif number == 18:
        from src.trading_runtime.strategy_eighteen_release import verify_strategy_eighteen_manifest as verify
    elif number == 19:
        from src.trading_runtime.strategy_nineteen_release import verify_strategy_nineteen_manifest as verify
    elif number == 20:
        from src.trading_runtime.strategy_twenty_release import verify_strategy_twenty_manifest as verify
    elif number == 21:
        from src.trading_runtime.strategy_twenty_one_release import verify_strategy_twenty_one_manifest as verify
    elif number == 22:
        from src.trading_runtime.strategy_twenty_two_release import verify_strategy_twenty_two_manifest as verify
    elif number == 23:
        from src.trading_runtime.strategy_twenty_three_release import verify_strategy_twenty_three_manifest as verify
    elif number == 24:
        from src.trading_runtime.strategy_twenty_four_release import verify_strategy_twenty_four_manifest as verify
    elif number == 25:
        from src.trading_runtime.strategy_twenty_five_release import verify_strategy_twenty_five_manifest as verify
    elif number == 26:
        from src.trading_runtime.strategy_twenty_six_release import verify_strategy_twenty_six_manifest as verify
    elif number == 27:
        from src.trading_runtime.strategy_twenty_seven_release import verify_strategy_twenty_seven_manifest as verify
    elif number == 28:
        from src.trading_runtime.strategy_twenty_eight_release import verify_strategy_twenty_eight_manifest as verify
    elif number == 29:
        from src.trading_runtime.strategy_twenty_nine_release import verify_strategy_twenty_nine_manifest as verify
    elif number == 30:
        from src.trading_runtime.strategy_thirty_release import verify_strategy_thirty_manifest as verify
    elif number == 31:
        from src.trading_runtime.strategy_thirty_one_release import verify_strategy_thirty_one_manifest as verify
    elif number == 32:
        from src.trading_runtime.strategy_thirty_two_release import verify_strategy_thirty_two_manifest as verify
    elif number == 33:
        from src.trading_runtime.strategy_thirty_three_release import verify_strategy_thirty_three_manifest as verify
    elif number == 34:
        from src.trading_runtime.strategy_thirty_four_release import verify_strategy_thirty_four_manifest as verify
    elif number == 35:
        from src.trading_runtime.strategy_thirty_five_release import verify_strategy_thirty_five_manifest as verify
    elif number == 36:
        from src.trading_runtime.strategy_thirty_six_release import verify_strategy_thirty_six_manifest as verify
    else:
        from src.trading_runtime.strategy_thirty_seven_release import verify_strategy_thirty_seven_manifest as verify
    verify(strategy)
    return True


def _validate_strategy_two_payload(payload: dict[str, Any]) -> None:
    strategy = payload["strategy"]
    if payload.get("assignments"):
        raise ValueError("Numbered release must not embed mutable assignments")
    if set(strategy.get("parameters") or {}) != {"execution", "sizing"}:
        raise ValueError("Numbered parameters differ from inherited sealed inputs")
    behavior = payload.get("strategy_profile", {}).get("lifecycle", {}).get("trading_behavior", {})
    if behavior.get("eligible_sessions") != ["premarket", "afterhours"]:
        raise ValueError("Numbered profile differs from its sealed sessions")


def selected_numbered_revision(*, revision_id: str = "", run_plan_id: str = "",
                               client: Any | None = None) -> dict[str, Any]:
    if not revision_id or revision_id.startswith("strategy-one-1:"):
        kwargs = {"revision_id": revision_id, "run_plan_id": run_plan_id}
        if client is not None:
            kwargs["client"] = client
        return selected_strategy_one_revision(**kwargs)
    if not re.fullmatch(r"strategy-one-(?:[23456789]|10|11|12|13|14|15|16|17|18|19|20|21|22|23|24|25|26|27|28|29|30|31|32|33|34|35|36|37):[0-9a-fA-F-]{36}", revision_id):
        raise ValueError("Unknown immutable numbered configuration identity")
    if client is None:
        from src.backend.backtest_market_data import readonly_clickhouse_client
        with closing(readonly_clickhouse_client(v3_read_principal=True)) as reader:
            return selected_numbered_revision(revision_id=revision_id, run_plan_id=run_plan_id, client=reader)
    release = certify_numbered_configuration(client, int(revision_id.split(":")[0].rsplit("-", 1)[1]))
    revision = release.revision()
    if revision_id != revision["revision_id"] or (run_plan_id and run_plan_id != revision["run_plan_id"]):
        raise ValueError("Selected numbered Strategy differs from its immutable release")
    return revision


def numbered_configuration_options(client: Any | None = None) -> list[dict[str, Any]]:
    if client is None:
        from src.backend.backtest_market_data import readonly_clickhouse_client
        with closing(readonly_clickhouse_client(v3_read_principal=True)) as reader:
            return numbered_configuration_options(reader)
    numbers = [json.loads(line)["strategy_number"] for line in client.execute(
        f"SELECT DISTINCT strategy_number FROM {RELEASE_TABLE} ORDER BY strategy_number FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    return [certify_numbered_configuration(client, number).revision() for number in numbers]
