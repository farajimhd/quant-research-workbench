from pathlib import Path
import pytest

from src.backend import backtest_native_channel_participant_successor_certification as cert
from src.backend.backtest_native_channel_participant_successor_compatibility import (
    RESTORATIONS, restore_native_channel_participant_successor_parent_source,
)


@pytest.mark.parametrize('relative', sorted(RESTORATIONS))
def test_complete_frozen_parent_restoration_and_unknown_delta_rejection(relative):
    source = (Path(cert.__file__).resolve().parents[2] / relative).read_text(encoding='utf-8')
    retained = restore_native_channel_participant_successor_parent_source(source, relative)
    assert cert._digest(retained) == cert.PARENT_SOURCE_AST[relative]
    with pytest.raises(ValueError, match='exact reviewed'):
        restore_native_channel_participant_successor_parent_source(source + '\nunknown = 1\n', relative)


def test_complete_source_certificate_rejects_unknown_executing_delta(monkeypatch):
    original = cert._read
    def changed(root, relative):
        source = original(root, relative)
        return source + '\nunknown = 1\n' if relative.endswith('backtest_fixed_journal_bootstrap.py') else source
    monkeypatch.setattr(cert, '_read', changed)
    with pytest.raises(ValueError, match='executing source differs'):
        cert.certify_native_channel_participant_successor_source()


def test_current_successor_source_is_certified():
    assert len(cert.certify_native_channel_participant_successor_source()) == 64
