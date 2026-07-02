from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta

from services.reference_gateway.config import ReferenceGatewayConfig


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


def run_recent_publication_gap_fill(config: ReferenceGatewayConfig) -> PublicationMaintenanceResult:
    if not config.market_publication_gap_fill_enabled:
        return PublicationMaintenanceResult(False, None, "", "", "disabled", [], "", "")
    end_date = date.today() + timedelta(days=1)
    start_date = end_date - timedelta(days=max(1, config.market_publication_gap_fill_days))
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
        "finra_short_volume,sec_fails_to_deliver,massive_splits,massive_dividends,massive_ipos,massive_ticker_details,ibkr_borrow_availability",
        "--finra-venues",
        "CNMS",
        "--output-root-win",
        str(config.prepared_root_win / "reference_market_publications"),
        "--resume-from-coverage",
        "--execute",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return PublicationMaintenanceResult(
        attempted=True,
        returncode=completed.returncode,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        reason="recent_reference_publication_gap_fill",
        command=command,
        stdout_tail=tail(completed.stdout),
        stderr_tail=tail(completed.stderr),
    )


def tail(value: str, *, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]
