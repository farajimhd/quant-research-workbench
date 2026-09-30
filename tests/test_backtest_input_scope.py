import json

import pytest

from src.backend.backtest_input_scope import input_exclusions


def test_dated_policy_is_explicit_and_scoped(tmp_path, monkeypatch):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps([{"ticker": "ABCD", "start": "2026-07-30",
                                  "end": "2026-09-12", "reason": "operator approved missing levels"}]))
    monkeypatch.setenv("BACKTEST_INPUT_EXCLUSIONS_FILE", str(policy))
    assert input_exclusions("2026-07-30") == ("ABCD",)
    assert input_exclusions("2026-09-13") == ()
    policy.write_text(json.dumps([{"ticker": "ABCD"}]))
    with pytest.raises(ValueError, match="reason"):
        input_exclusions("2026-07-30")
