"""Current-runtime proof types cannot impersonate historical qualification."""
import asyncio
from types import SimpleNamespace

import pytest

from src.backend.program_runtime_decision import (
    ProgramRuntimeDecisionQualification, capture_program_runtime_decision,
    qualify_program_runtime_decision, require_program_runtime_qualification,
)
from src.trading_runtime.rule_decision_publication import _check_rule_decision_qualification


def test_current_runtime_capture_rejects_callback_or_mode_label_without_native_assembly():
    with pytest.raises(ValueError, match='actual native execution'):
        asyncio.run(capture_program_runtime_decision(SimpleNamespace(mode='live'), object(), object(), object(),
            assignment_id='assignment', ordinal=1, portfolio_authority=object()))


def test_raw_capture_or_public_qualification_fields_cannot_create_writer_authority():
    with pytest.raises(ValueError, match='native preselection capture'):
        qualify_program_runtime_decision(object(), {}, (), (),
            execution_policy=None, protection_profile=None, outside_rth=False)
    forged = ProgramRuntimeDecisionQualification('run', 1, 'a' * 64, None, object())
    with pytest.raises(ValueError, match='exact native runtime qualification'):
        require_program_runtime_qualification(SimpleNamespace(), forged)
    with pytest.raises(ValueError, match='exact typed unit'):
        _check_rule_decision_qualification(SimpleNamespace(), forged)


def test_foreign_proof_does_not_enter_existing_historical_qualification_branch():
    with pytest.raises(ValueError, match='matching native qualification'):
        _check_rule_decision_qualification(object(), SimpleNamespace(_proof=object()))
