from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research.mlops.sync import copy_family_runtime_modules, copy_runtime_module


class RuntimeSyncTests(unittest.TestCase):
    def test_family_runtime_modules_include_shared_helpers_but_not_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "__init__.py").write_text("", encoding="utf-8")
            (source / "shared_helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            (source / "test_shared_helper.py").write_text("raise AssertionError\n", encoding="utf-8")
            (source / "v11").mkdir()
            (source / "v11" / "model.py").write_text("", encoding="utf-8")

            copy_family_runtime_modules(source, destination)

            self.assertTrue((destination / "__init__.py").exists())
            self.assertTrue((destination / "shared_helper.py").exists())
            self.assertFalse((destination / "test_shared_helper.py").exists())
            self.assertFalse((destination / "v11").exists())

    def test_shared_runtime_module_includes_package_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            module = source / "pipelines" / "market_sip" / "events" / "contract.py"
            module.parent.mkdir(parents=True)
            for package in (source / "pipelines", source / "pipelines" / "market_sip", module.parent):
                (package / "__init__.py").write_text("", encoding="utf-8")
            module.write_text("VALUE = 1\n", encoding="utf-8")

            copy_runtime_module(source, destination, Path("pipelines/market_sip/events/contract.py"))

            self.assertEqual((destination / "pipelines/market_sip/events/contract.py").read_text(encoding="utf-8"), "VALUE = 1\n")
            self.assertTrue((destination / "pipelines/__init__.py").is_file())
            self.assertTrue((destination / "pipelines/market_sip/__init__.py").is_file())
            self.assertTrue((destination / "pipelines/market_sip/events/__init__.py").is_file())


if __name__ == "__main__":
    unittest.main()
