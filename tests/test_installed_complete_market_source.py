"""Session ownership rejection; positive native admission is a separate gate."""
from types import SimpleNamespace

import pytest

from src.backend.backtest_installed_complete_market_source import installed_complete_market_source
from src.backend.backtest_fixed_structural_lot_execution import PreparedFixedStructuralLotSession


def test_absent_session_retains_original_transport():
    assert installed_complete_market_source(None, None, prices=None,
        through_boundary_ms=100, client_factory=None) is None


@pytest.mark.parametrize('session', [SimpleNamespace(), PreparedFixedStructuralLotSession(None, (), None)])
def test_constructor_or_duck_type_cannot_select_reader(session):
    with pytest.raises(ValueError, match='exact native session|exact factory-issued source preparation'):
        installed_complete_market_source(session, None, prices=None,
            through_boundary_ms=100, client_factory=None)
