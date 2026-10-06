import json
import pytest
from research.rl_trading.v6.run_complete_head_generalization import admit_gate


def test_generalization_refuses_unpassed_or_mismatched_underfit(tmp_path):
    (tmp_path/'manifest.json').write_text(json.dumps(dict(panel_sha256='p',forecast_targets_sha256='t')))
    (tmp_path/'complete.json').write_text(json.dumps(dict(status='completed',generalization_evaluated=False)))
    (tmp_path/'tcn.json').write_text(json.dumps(dict(passed=False)))
    with pytest.raises(ValueError,match='underfit gate'):admit_gate(tmp_path,'p','t')
    with pytest.raises(ValueError,match='underfit gate'):admit_gate(tmp_path,'wrong','t')
