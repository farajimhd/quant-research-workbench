from __future__ import annotations

import os
import unittest
from unittest import mock

from .config import IntelligenceConfig


class IntelligenceConfigTests(unittest.TestCase):
    def test_optional_models_and_llm_are_disabled_by_default(self) -> None:
        with (
            mock.patch(
                "text_intelligence.config.load_repo_dotenv"
            ),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            config = IntelligenceConfig.from_env()

        self.assertFalse(config.enable_models)
        self.assertFalse(config.enable_llm)
        self.assertFalse(config.enable_live_ai)
        self.assertFalse(config.terminal_rich_enabled)
        self.assertTrue(config.terminal_screen_enabled)

    def test_optional_inference_requires_explicit_opt_in(self) -> None:
        with (
            mock.patch(
                "text_intelligence.config.load_repo_dotenv"
            ),
            mock.patch.dict(
                os.environ,
                {
                    "TEXT_INTELLIGENCE_ENABLE_MODELS": "true",
                    "TEXT_INTELLIGENCE_ENABLE_LLM": "true",
                    "TEXT_INTELLIGENCE_ENABLE_LIVE_AI": "true",
                },
                clear=True,
            ),
        ):
            config = IntelligenceConfig.from_env()

        self.assertTrue(config.enable_models)
        self.assertTrue(config.enable_llm)
        self.assertTrue(config.enable_live_ai)

    def test_terminal_can_be_explicitly_enabled_by_launcher(self) -> None:
        with (
            mock.patch("text_intelligence.config.load_repo_dotenv"),
            mock.patch.dict(
                os.environ,
                {
                    "TEXT_INTELLIGENCE_TERMINAL_RICH_ENABLED": "true",
                    "TEXT_INTELLIGENCE_TERMINAL_SCREEN_ENABLED": "false",
                    "TEXT_INTELLIGENCE_TERMINAL_REFRESH_SECONDS": "0.5",
                },
                clear=True,
            ),
        ):
            config = IntelligenceConfig.from_env()

        self.assertTrue(config.terminal_rich_enabled)
        self.assertFalse(config.terminal_screen_enabled)
        self.assertEqual(config.terminal_refresh_seconds, 0.5)

    def test_legacy_news_service_names_remain_transitional_aliases(self) -> None:
        with (
            mock.patch("text_intelligence.config.load_repo_dotenv"),
            mock.patch.dict(
                os.environ,
                {
                    "NEWS_INTELLIGENCE_BIND": "127.0.0.1:18804",
                    "NEWS_INTELLIGENCE_ENABLE_MODELS": "true",
                },
                clear=True,
            ),
        ):
            config = IntelligenceConfig.from_env()

        self.assertEqual(config.bind, "127.0.0.1:18804")
        self.assertTrue(config.enable_models)

    def test_text_service_names_override_legacy_aliases(self) -> None:
        with (
            mock.patch("text_intelligence.config.load_repo_dotenv"),
            mock.patch.dict(
                os.environ,
                {
                    "TEXT_INTELLIGENCE_BIND": "127.0.0.1:28804",
                    "NEWS_INTELLIGENCE_BIND": "127.0.0.1:18804",
                    "TEXT_INTELLIGENCE_ENABLE_MODELS": "false",
                    "NEWS_INTELLIGENCE_ENABLE_MODELS": "true",
                },
                clear=True,
            ),
        ):
            config = IntelligenceConfig.from_env()

        self.assertEqual(config.bind, "127.0.0.1:28804")
        self.assertFalse(config.enable_models)


if __name__ == "__main__":
    unittest.main()
