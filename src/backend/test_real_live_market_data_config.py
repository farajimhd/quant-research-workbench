import os
from pathlib import Path
import unittest
from unittest.mock import patch

from src.backend.real_live_market_data.config import market_gateway_config


class RealLiveMarketDataConfigTest(unittest.TestCase):
    def test_laptop_default_serves_reference_gateway_presentation_assets(self) -> None:
        with patch.dict(os.environ, {"COMPUTERNAME": "LAPTOP", "REAL_LIVE_LOGO_ARTIFACT_ROOT": "", "REFERENCE_GATEWAY_PRESENTATION_ASSET_ROOT_WIN": "", "TRADING_DASHBOARD_ARTIFACT_ROOT": ""}, clear=False):
            config = market_gateway_config()

        self.assertEqual(
            Path(config.logo_artifact_root),
            Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data\reference_gateway\artifacts\presentation_assets"),
        )

    def test_reference_gateway_override_is_shared_with_asset_writer(self) -> None:
        expected = r"X:\reference_gateway\presentation_assets"
        with patch.dict(os.environ, {"REAL_LIVE_LOGO_ARTIFACT_ROOT": "", "REFERENCE_GATEWAY_PRESENTATION_ASSET_ROOT_WIN": expected, "TRADING_DASHBOARD_ARTIFACT_ROOT": "legacy"}, clear=False):
            config = market_gateway_config()

        self.assertEqual(config.logo_artifact_root, expected)


if __name__ == "__main__":
    unittest.main()
