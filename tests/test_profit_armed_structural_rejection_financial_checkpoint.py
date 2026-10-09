"""Financial publication boundary qualification; persisted readers are seams."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.trading_runtime import profit_armed_structural_rejection_financial_checkpoint as financial


def issued():
    publication,client,session=object(),object(),object()
    capture=financial.StructuralRejectionFinancialCapture(object(),object(),9,'{}')
    receipt=financial.StructuralRejectionFinancialVerification(publication,capture,'broker','oms')
    financial._VERIFIED[receipt]=(publication,capture,client,session,'broker','oms')
    return receipt,dict(publication=publication,client=client,session=session)


def test_publication_boundary_reverifies_complete_financial_capture(monkeypatch):
    receipt,kwargs=issued()
    calls=[]
    def verify(capture,publication,*,client,session):
        calls.append((capture,publication,client,session))
        return SimpleNamespace(broker_head='broker',oms_head='oms')
    monkeypatch.setattr(financial,'verify_financial_capture',verify)
    assert financial.require_financial_verification(receipt,**kwargs) is receipt
    assert calls==[(receipt.capture,kwargs['publication'],kwargs['client'],kwargs['session'])]


@pytest.mark.parametrize('reason',('journal cursor changed','Portfolio inventory changed','OMS inventory changed'))
def test_old_receipt_does_not_bypass_fresh_inventory_failure(monkeypatch,reason):
    receipt,kwargs=issued()
    def verify(*args,**kw):
        raise ValueError(reason)
    monkeypatch.setattr(financial,'verify_financial_capture',verify)
    with pytest.raises(ValueError,match=reason):
        financial.require_financial_verification(receipt,**kwargs)


@pytest.mark.parametrize('field',('broker_head','oms_head'))
def test_fresh_head_must_match_original_receipt(monkeypatch,field):
    receipt,kwargs=issued()
    fresh=SimpleNamespace(broker_head='broker',oms_head='oms')
    setattr(fresh,field,'changed')
    monkeypatch.setattr(financial,'verify_financial_capture',lambda *args,**kw:fresh)
    with pytest.raises(ValueError,match='heads changed'):
        financial.require_financial_verification(receipt,**kwargs)


def test_copied_receipt_cannot_authorize_publication():
    receipt,kwargs=issued()
    with pytest.raises(ValueError,match='Unissued'):
        financial.require_financial_verification(replace(receipt),**kwargs)
