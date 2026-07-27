from __future__ import annotations

import os
from pathlib import Path


PROJECT_RUNTIME_NAME = "quant-research-workbench"
LAPTOP_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes")
WORKSTATION_NAME = "DESKTOP-SAAI85T"
WORKSTATION_RUNTIME_ROOT = Path(r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes")


def runtime_root() -> Path:
    configured = os.environ.get("QW_RUNTIME_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.environ.get("COMPUTERNAME", "").strip().upper() == WORKSTATION_NAME:
        return WORKSTATION_RUNTIME_ROOT
    return LAPTOP_RUNTIME_ROOT


def project_runtime_root() -> Path:
    return runtime_root() / PROJECT_RUNTIME_NAME


def frontend_runtime_root() -> Path:
    configured = os.environ.get("QW_FRONTEND_RUNTIME_ROOT", "").strip()
    return Path(configured).expanduser() if configured else project_runtime_root() / "frontend"


def frontend_dist_root() -> Path:
    configured = os.environ.get("QW_FRONTEND_DIST", "").strip()
    return Path(configured).expanduser() if configured else frontend_runtime_root() / "dist"


def frontend_review_root() -> Path:
    configured = os.environ.get("QW_FRONTEND_REVIEW_ROOT", "").strip()
    return Path(configured).expanduser() if configured else project_runtime_root() / "frontend-ui-review"


def ibkr_gateway_log_root() -> Path:
    configured = os.environ.get("IBKR_GATEWAY_LOG_ROOT", "").strip()
    return Path(configured).expanduser() if configured else project_runtime_root() / "ibkr_gateway_supervisor"
