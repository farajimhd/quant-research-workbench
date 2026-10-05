from dataclasses import replace
from datetime import date
from hashlib import sha256
from uuid import uuid4

import pytest

from src.backend.backtest_squeeze_ladder_admission import admit_ladder_proposal
from src.backend.backtest_squeeze_ladder_evidence import (
    SETUP, TARGET, project_ladder_evidence, verify_ladder_evidence_projection,
)
from src.trading_runtime.journal_contract import canonical_json
from tests.test_backtest_squeeze_ladder_admission import proposal, financial, DAY


def evidence():
    admission = admit_ladder_proposal(proposal(), financial(), session_date=DAY, groups=())
    rows = project_ladder_evidence(admission, run_id='prepared-evidence', batch_id=str(uuid4()),
        parent_record_id=str(uuid4()), assignment_id='assignment-1', session_date=DAY)
    return admission, rows


def test_named_scalar_families_preserve_setup_and_all_target_lots():
    _, rows = evidence()
    assert set(rows.setup) == {name for name, _ in SETUP.columns}
    assert all(set(row) == {name for name, _ in TARGET.columns} for row in rows.targets)
    assert rows.setup['qualification_boundary_ms'] == 65000
    assert rows.setup['boundary_ms'] == 65100
    assert rows.setup['stop_int'] == 97900
    assert [row['level_id'] for row in rows.targets] == ['R2','R3','R4']
    assert len({row['record_id'] for row in rows.targets}) == 3
    assert all(row['parent_record_id'] == rows.setup['parent_record_id'] for row in rows.targets)
    verify_ladder_evidence_projection(rows, expected=rows)


def test_rehashed_wrong_target_cannot_authorize_itself_and_missing_lots_fail():
    _, rows = evidence()
    changed = dict(rows.targets[0], level_id='convenient-other-level')
    changed['content_hash'] = sha256(canonical_json({name:value for name,value in changed.items()
                                                   if name != 'content_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError, match='reconstructed'):
        verify_ladder_evidence_projection(replace(rows, targets=(changed,*rows.targets[1:])), expected=rows)
    with pytest.raises(ValueError, match='incomplete'):
        verify_ladder_evidence_projection(replace(rows, targets=rows.targets[:-1]), expected=rows)


def test_foreign_session_cannot_be_projected():
    admission, rows = evidence()
    with pytest.raises(ValueError, match='session'):
        project_ladder_evidence(admission, run_id='prepared-evidence', batch_id=rows.setup['batch_id'],
            parent_record_id=rows.setup['parent_record_id'], assignment_id='assignment-1',
            session_date=date(2026,8,19))


def test_shared_native_typed_encoding_matches_hashes_and_rejects_invalid_fields():
    from src.trading_runtime.arte_journal_writer import typed_row, _canonical_typed_content
    _, rows = evidence()
    for table, row in ((SETUP, rows.setup), *((TARGET, row) for row in rows.targets)):
        content = {name:value for name,value in row.items() if name != 'content_hash'}
        assert typed_row(table.name, content) == row
        stored = dict(content)
        for name, kind in table.columns:
            if kind.startswith('UInt') and name in stored:
                stored[name] = str(stored[name])
        assert _canonical_typed_content(table.name, stored, stored_utc=True) == content
    content = {name:value for name,value in rows.setup.items() if name != 'content_hash'}
    with pytest.raises(ValueError):
        typed_row(SETUP.name, dict(content, target_count=256))
    with pytest.raises(ValueError):
        typed_row(SETUP.name, dict(content, scan_content_hash='not-a-hash'))


@pytest.mark.parametrize('value', ['a'*63, 'a'*65, 'é'*31, 'é'*33])
def test_native_fixed_string_checks_byte_width_before_hashing(value):
    from src.trading_runtime.arte_journal_writer import typed_row
    _, rows = evidence()
    content = {name:item for name,item in rows.setup.items() if name != 'content_hash'}
    with pytest.raises(ValueError, match='64 UTF-8 bytes'):
        typed_row(SETUP.name, dict(content, scan_content_hash=value))
