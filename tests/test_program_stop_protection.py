from dataclasses import replace
from datetime import datetime, UTC

import pytest

from src.trading_runtime.program_stop_protection import ProgramStopProtection
from src.trading_runtime.columnar_program_coordinator import ProgramProposal
from src.trading_runtime.execution_policies import ExecutionPolicy, ProtectionProfile, ProtectionSlice, StopRule


def proposal():
    return ProgramProposal('00000000-0000-0000-0000-000000000001', 'add', 'add_long',
        datetime.now(UTC), 'ABC', 0., 10., 200., 8., 'program:add')


def test_layout_binds_final_add_stop_and_preserves_native_slice_layout():
    layout = ProgramStopProtection('approved-layout', 2, (.2, .3, .5))
    intent = proposal().to_intent(execution_policy=ExecutionPolicy(),
        protection_profile=layout, outside_rth=True)
    assert type(intent.protection_profile) is ProtectionProfile
    assert [row.quantity_fraction for row in intent.protection_profile.slices] == [.2, .3, .5]
    assert all(row.stop.price == 8. for row in intent.protection_profile.slices)
    assert intent.capital_request.value == 200.
    later = replace(proposal(), stop_price=9.)
    assert all(row.stop.price == 9. for row in layout.for_proposal(later).slices)
    assert layout.for_proposal(replace(later, stage='exit', action='exit')) is None


def test_static_profiles_still_cannot_change_the_selected_program_stop():
    profile = ProtectionProfile('old-stop', 1, (ProtectionSlice('one', 1., StopRule(price=7.)),))
    with pytest.raises(ValueError, match='fixed stop-only'):
        proposal().to_intent(execution_policy=ExecutionPolicy(), protection_profile=profile, outside_rth=True)


@pytest.mark.parametrize('fractions', [(), (.2, .3), (float('nan'),), (-1., 2.)])
def test_invalid_layouts_fail_closed(fractions):
    with pytest.raises(ValueError):
        ProgramStopProtection('layout', 1, fractions)
