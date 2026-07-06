from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


WORKSTATION_NAME = "DESKTOP-SAAI85T"
WORKSTATION_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")


@dataclass(frozen=True, slots=True)
class StorageRoots:
    data_root_win: Path
    prepared_root_win: Path
    log_root_win: Path
    is_workstation: bool


def is_workstation_host() -> bool:
    return os.environ.get("COMPUTERNAME", "").strip().upper() == WORKSTATION_NAME


def resolve_workstation_data_root(*, env_name: str, default_prepared_subdir: str, default_log_subdir: str) -> StorageRoots:
    explicit = os.environ.get(env_name, "").strip()
    if explicit:
        data_root = Path(explicit)
    elif is_workstation_host():
        data_root = WORKSTATION_DATA_ROOT_WIN
    else:
        data_root = WORKSTATION_SHARE_DATA_ROOT_WIN
    if not data_root.exists():
        raise RuntimeError(f"Workstation data root is not available: {data_root}")
    prepared = data_root / "prepared" / default_prepared_subdir
    logs = data_root / "prepared" / default_log_subdir / "logs"
    return StorageRoots(data_root_win=data_root, prepared_root_win=prepared, log_root_win=logs, is_workstation=is_workstation_host())

