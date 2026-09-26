"""A V4 launch cannot infer journal coverage from a quiet sample run."""
from __future__ import annotations

import pytest

from src.backend.backtest_fixed_v4_certification import (
    certify_strategy_one_v4_projection,
)


def test_v4_certificate_binds_direct_and_indirect_sources(tmp_path):
    controller = tmp_path / "controller.py"
    controller.write_text(
        'self._journal.append(category="checkpoint", entity_type="market_boundary")',
        encoding="utf-8")
    oms = tmp_path / "order_management.py"
    oms.write_text(
        'self._record("broker", "order_acknowledgement", "1", "A", at, {})\n'
        'self._record("order_management", "protection_reconciliation", '
        '"1", "A", at, {})\n', encoding="utf-8")
    kwargs = {"controller_source": controller, "indirect_sources": (oms,)}
    first = certify_strategy_one_v4_projection(**kwargs)
    assert len(first) == 64
    assert certify_strategy_one_v4_projection(**kwargs) == first
    oms.write_text(oms.read_text(encoding="utf-8") + "# source revision\n",
                   encoding="utf-8")
    assert certify_strategy_one_v4_projection(**kwargs) != first


def test_v4_certificate_rejects_unprojected_indirect_family(tmp_path):
    controller = tmp_path / "controller.py"
    controller.write_text(
        'self._journal.append(category="checkpoint", entity_type="market_boundary")',
        encoding="utf-8")
    oms = tmp_path / "order_management.py"
    oms.write_text('self._record("broker", "unknown", "1", "A", at, {})',
                   encoding="utf-8")
    with pytest.raises(ValueError, match="V4 indirect emitters lack typed projection"):
        certify_strategy_one_v4_projection(
            controller_source=controller, indirect_sources=(oms,))


def test_current_runtime_is_not_yet_v4_certified():
    with pytest.raises(ValueError, match="order_cancel_requested"):
        certify_strategy_one_v4_projection()
