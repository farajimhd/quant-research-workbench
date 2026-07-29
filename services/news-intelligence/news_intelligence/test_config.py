from __future__ import annotations

import os
import unittest
from unittest import mock

from .config import IntelligenceConfig


class IntelligenceConfigTests(unittest.TestCase):
    def test_optional_models_and_llm_are_disabled_by_default(self) -> None:
        with (
            mock.patch(
                "news_intelligence.config.load_repo_dotenv"
            ),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            config = IntelligenceConfig.from_env()

        self.assertFalse(config.enable_models)
        self.assertFalse(config.enable_llm)

    def test_optional_inference_requires_explicit_opt_in(self) -> None:
        with (
            mock.patch(
                "news_intelligence.config.load_repo_dotenv"
            ),
            mock.patch.dict(
                os.environ,
                {
                    "NEWS_INTELLIGENCE_ENABLE_MODELS": "true",
                    "NEWS_INTELLIGENCE_ENABLE_LLM": "true",
                },
                clear=True,
            ),
        ):
            config = IntelligenceConfig.from_env()

        self.assertTrue(config.enable_models)
        self.assertTrue(config.enable_llm)


if __name__ == "__main__":
    unittest.main()
