from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from services.ibkr_gateway_supervisor.config import IbkrGatewayConfig
from src.runtime_paths import (
    LAPTOP_RUNTIME_ROOT,
    WORKSTATION_RUNTIME_ROOT,
    frontend_dist_root,
    ibkr_gateway_log_root,
)


class RuntimePathTests(unittest.TestCase):
    def test_laptop_defaults_stay_outside_repository(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                frontend_dist_root(),
                LAPTOP_RUNTIME_ROOT / "quant-research-workbench" / "frontend" / "dist",
            )
            self.assertEqual(
                ibkr_gateway_log_root(),
                LAPTOP_RUNTIME_ROOT / "quant-research-workbench" / "ibkr_gateway_supervisor",
            )
            self.assertEqual(IbkrGatewayConfig.from_env().log_root, ibkr_gateway_log_root())

    def test_workstation_uses_workstation_runtime_authority(self) -> None:
        with patch.dict(os.environ, {"COMPUTERNAME": "DESKTOP-SAAI85T"}, clear=True):
            self.assertEqual(
                ibkr_gateway_log_root(),
                WORKSTATION_RUNTIME_ROOT / "quant-research-workbench" / "ibkr_gateway_supervisor",
            )

    def test_specific_overrides_take_precedence(self) -> None:
        with patch.dict(
            os.environ,
            {
                "QW_FRONTEND_DIST": r"E:\runtime\frontend-dist",
                "IBKR_GATEWAY_LOG_ROOT": r"E:\runtime\ibkr",
            },
            clear=True,
        ):
            self.assertEqual(frontend_dist_root(), Path(r"E:\runtime\frontend-dist"))
            self.assertEqual(ibkr_gateway_log_root(), Path(r"E:\runtime\ibkr"))


if __name__ == "__main__":
    unittest.main()
