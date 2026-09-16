from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_pivot_audit import PivotAudit
from src.market_engine.swing_structure import SwingSettings, SwingStructure


def test_repeated_pivot_records_fresh_witness_without_rewriting_anchor():
    audit = PivotAudit(SwingSettings())
    audit.clock = {1:100., 2:101., 3:200., 4:201.}
    detector = audit.detectors[0]
    audit._found(detector, (10., 1, .1), 'support', 2)
    original = deepcopy(audit.active[1])
    audit._found(detector, (10., 3, .1), 'support', 4)
    assert len(audit.active) == 1
    assert audit.active[1]['pivot_at'] == original['pivot_at'] == 1
    assert audit.active[1]['confirmed_at'] == original['confirmed_at'] == 2
    witness = audit.events[-1]
    assert witness['merged']
    assert witness['pivot_at'] == 200.
    assert witness['at'] == 201.
    assert witness['level_confirmed_at'] == 101.


def test_audit_preserves_base_engine_state_for_new_merged_and_opposite_levels():
    audit, baseline = PivotAudit(SwingSettings()), SwingStructure(SwingSettings())
    audit.clock = {i:100.+i for i in range(1, 9)}
    for engine in (audit, baseline):
        detector = engine.detectors[0]
        engine._found(detector, (10., 1, .1), 'support', 2)
        engine._found(detector, (10., 3, .1), 'support', 4)
        engine._found(detector, (10., 5, .1), 'resistance', 6)
        engine._found(detector, (11., 7, .1), 'support', 8)
    assert audit.active == baseline.active
    assert audit.segments == baseline.segments
    assert audit.counts == baseline.counts
    assert [e['merged'] for e in audit.events] == [False, True, False]
