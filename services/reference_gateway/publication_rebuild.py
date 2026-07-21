from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from services.reference_gateway.config import ReferenceGatewayConfig


REPO_ROOT = Path(__file__).resolve().parents[2]
STEP_06_SCRIPT = REPO_ROOT / "pipelines" / "reference_data" / "migration" / "step_06_build_q_live_bridge_features.py"
SEC_ISSUER_RELATIONSHIP_SYNC_SCRIPT = REPO_ROOT / "pipelines" / "reference_data" / "sync_sec_issuer_relationships.py"


@dataclass(frozen=True, slots=True)
class PublicationRebuildResult:
    status: str
    reason: str
    command: list[str]
    returncode: int
    stdout_tail: str
    stderr_tail: str


def rebuild_sec_market_bridge(config: ReferenceGatewayConfig, *, reason: str, feature_date: date | None = None) -> PublicationRebuildResult:
    return _run_step_06_specs(config, reason=reason, feature_date=feature_date, specs=("sec_market_bridge",))


def rebuild_tradable_publications(config: ReferenceGatewayConfig, *, reason: str, feature_date: date | None = None) -> PublicationRebuildResult:
    return _run_step_06_specs(config, reason=reason, feature_date=feature_date, specs=("tradable_universe", "scanner_static"))


def _run_step_06_specs(
    config: ReferenceGatewayConfig,
    *,
    reason: str,
    feature_date: date | None,
    specs: tuple[str, ...],
) -> PublicationRebuildResult:
    if not config.execute:
        return PublicationRebuildResult("skipped", "execute_false", [], 0, "", "")
    if config.test_write_mode and not config.rebuild_tradable_in_test_mode:
        return PublicationRebuildResult(
            "skipped",
            "test_write_mode_requires_REFERENCE_GATEWAY_REBUILD_TRADABLE_IN_TEST_MODE",
            [],
            0,
            "",
            "",
        )
    feature_date = feature_date or datetime.now(UTC).date()
    output_name = "sec_bridge_syncs" if specs == ("sec_market_bridge",) else "tradable_rebuilds"
    output_root = config.prepared_root_win / "reference_gateway" / output_name
    relationship_stdout = ""
    if specs == ("sec_market_bridge",):
        relationship_command = [
            sys.executable,
            str(SEC_ISSUER_RELATIONSHIP_SYNC_SCRIPT),
            "--execute",
            "--database",
            config.clickhouse_write_database,
            "--output-root-win",
            str(config.prepared_root_win / "reference_gateway" / "sec_issuer_relationships"),
        ]
        relationship_completed = subprocess.run(
            relationship_command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if relationship_completed.returncode != 0:
            raise RuntimeError(
                "SEC issuer relationship publication failed: "
                + json.dumps(
                    {
                        "command": relationship_command,
                        "returncode": relationship_completed.returncode,
                        "stdout_tail": tail(relationship_completed.stdout),
                        "stderr_tail": tail(relationship_completed.stderr),
                    },
                    sort_keys=True,
                )
            )
        relationship_stdout = relationship_completed.stdout
    command = [
        sys.executable,
        str(STEP_06_SCRIPT),
        "--execute",
        "--allow-non-empty-targets",
        "--target-database",
        config.clickhouse_write_database,
        "--feature-date",
        feature_date.isoformat(),
        "--output-root-win",
        str(output_root),
        "--specs",
        ",".join(specs),
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    result = PublicationRebuildResult(
        status="completed" if completed.returncode == 0 else "failed",
        reason=reason,
        command=command,
        returncode=completed.returncode,
        stdout_tail=tail(relationship_stdout + "\n" + completed.stdout),
        stderr_tail=tail(completed.stderr),
    )
    if completed.returncode != 0:
        raise RuntimeError("Reference publication build failed: " + json.dumps(asdict(result), sort_keys=True))
    return result


def tail(value: str, *, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]
