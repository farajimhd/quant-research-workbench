from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from services.reference_gateway.config import ReferenceGatewayConfig


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class PublicationMaintenanceResult:
    attempted: bool
    returncode: int | None
    start_date: str
    end_date: str
    reason: str
    command: list[str]
    stdout_tail: str
    stderr_tail: str


def run_recent_publication_gap_fill(
    config: ReferenceGatewayConfig,
    *,
    on_progress: Callable[[str], None] | None = None,
    deep: bool = False,
) -> PublicationMaintenanceResult:
    if not config.market_publication_gap_fill_enabled:
        return PublicationMaintenanceResult(False, None, "", "", "disabled", [], "", "")
    end_date = date.today() + timedelta(days=1)
    if deep and config.market_publication_deep_backfill_enabled:
        start_date = date.fromisoformat(config.market_publication_deep_backfill_start_date)
        reason = "deep_reference_publication_gap_fill"
    else:
        start_date = end_date - timedelta(days=max(1, config.market_publication_gap_fill_days))
        reason = "recent_reference_publication_gap_fill"
    command = [
        sys.executable,
        "pipelines/reference_data/market_publications_historical_gap_fill.py",
        "--start-date",
        start_date.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--read-database",
        config.clickhouse_read_database,
        "--write-database",
        config.clickhouse_write_database,
        "--sources",
        (
            "finra_short_volume,massive_short_interest,sec_fails_to_deliver,reg_sho_threshold,"
            "massive_splits,massive_dividends,massive_ipos,massive_ticker_details,"
            "massive_presentation_assets,ibkr_borrow_availability,sec_country_assertions"
        ),
        "--finra-venues",
        "CNMS",
        "--sec-ftd-link-mode",
        "html",
        "--output-root-win",
        str(config.prepared_root_win / "reference_market_publications"),
        "--resume-from-coverage",
        "--execute",
    ]
    lines: list[str] = []
    process = subprocess.Popen(  # noqa: S603
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if not line:
            continue
        lines.append(line)
        if on_progress is not None:
            on_progress(line)
    returncode = process.wait()
    stdout = "\n".join(lines)
    return PublicationMaintenanceResult(
        attempted=True,
        returncode=returncode,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        reason=reason,
        command=command,
        stdout_tail=tail(stdout),
        stderr_tail="",
    )


def tail(value: str, *, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]
