import pytest
from src.backend import backtest_native_channel_successor_certification as cert


def test_complete_current_source_and_inherited_restoration_are_certified():
    assert len(cert.PARENT_SOURCE_AST)==620
    assert set(cert.PARENT_SOURCE_AST)<=set(cert.REQUIRED_SOURCE_AST)
    assert 'src/backend/backtest_native_channel_position_index.py' in cert.REQUIRED_SOURCE_AST
    assert 'src/backend/backtest_native_channel_successor_compatibility.py' in cert.REQUIRED_SOURCE_AST
    assert len(cert.certify_native_channel_successor_source())==64


def test_unknown_executing_source_change_is_rejected(monkeypatch):
    read=cert._read
    target=next(iter(cert.REQUIRED_SOURCE_AST))
    def changed(root,relative):
        text=read(root,relative)
        return text+'\nUNKNOWN_CHANGE = True\n' if relative==target else text
    monkeypatch.setattr(cert,'_read',changed)
    with pytest.raises(ValueError,match='executing source differs'):
        cert.certify_native_channel_successor_source()


def test_source_changed_during_certification_is_rejected(monkeypatch):
    read=cert._read
    target=next(iter(cert.REQUIRED_SOURCE_AST)); seen=0
    def changed(root,relative):
        nonlocal seen
        text=read(root,relative)
        if relative==target:
            seen+=1
            if seen>1:return text+'\n# changed during certification\n'
        return text
    monkeypatch.setattr(cert,'_read',changed)
    with pytest.raises(ValueError,match='changed during certification'):
        cert.certify_native_channel_successor_source()
