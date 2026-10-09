import json
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.tests.test_sparse_runner import fixture
from research.vectorized_backtest.v6.torch_backtest.sparse_runner import SparseProgramRunner
from research.vectorized_backtest.v6.torch_backtest.profile_sparse import audit_profile, profile_metrics
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


@pytest.mark.parametrize('full_session', [False, True])
def test_profile_audits_real_fills_and_preserves_prefix_eligibility(tmp_path, full_session):
    _, inputs, space, member, gates = fixture()
    runner = SparseProgramRunner(inputs, space, [member], gates, maximum_fills=512)
    metrics = runner.run(steps=None if full_session else 20)
    count = int(runner.fill_count.max())
    assert count > 0
    torch.save(dict(ledger=runner.ledger[:, :count].clone(), counts=runner.fill_count.clone()), tmp_path/'fills.pt')
    serial = {k: v.tolist() if isinstance(v, torch.Tensor) else v for k, v in metrics.items()}
    eligibility = list(serial['terminal_valid'])
    checksum = audit_profile(tmp_path, serial, full_session=full_session)
    record = json.loads((tmp_path/'financial-audit.json').read_text())
    assert checksum == file_hash(tmp_path/'financial-audit.json')
    assert record['status'] == 'passed'
    assert record['terminal_eligibility_qualified'] == full_session
    assert serial['terminal_valid'] == eligibility
    saved = torch.load(tmp_path/'fills.pt', weights_only=True)
    saved['ledger'][0, 0, 6] += 1
    torch.save(saved, tmp_path/'fills.pt')
    with pytest.raises(ValueError, match='arithmetic mismatch'):
        audit_profile(tmp_path, serial, full_session=full_session)


def test_undefined_diagnostics_are_null_but_nonfinite_finances_fail_closed():
    _, inputs, space, member, gates = fixture()
    runner = SparseProgramRunner(inputs, space, [member], gates, maximum_fills=512)
    metrics = runner.run()
    metrics['undefined_diagnostic'] = torch.tensor([float('nan')])
    serial = profile_metrics(metrics)
    assert serial['undefined_diagnostic'] == [None]
    json.dumps(serial, allow_nan=False)
    metrics['net_pnl'][0] = float('nan')
    with pytest.raises(ValueError, match='Non-finite profiling financial metric'):
        profile_metrics(metrics)
