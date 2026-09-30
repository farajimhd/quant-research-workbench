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


@pytest.mark.parametrize("candidate_count,effective", [(0, ()), (1, ("ABCD",))])
def test_operational_scope_preserves_zero_candidate_parent_seal(monkeypatch, candidate_count, effective):
    from types import SimpleNamespace
    from src.backend import backtest_input_scope as subject
    full = SimpleNamespace(coverage=(SimpleNamespace(ticker="ABCD", candidate_count=candidate_count),))
    monkeypatch.setattr(subject, "certify_candidate_plan", lambda *_a, **_k: full)
    monkeypatch.setattr(subject, "input_exclusions", lambda _day: ("ABCD",))
    observed = []
    monkeypatch.setattr(subject, "exclude_candidate_tickers",
                        lambda parent, excluded: observed.append((parent, excluded)) or parent)
    assert subject.certify_scoped_candidate_plan(SimpleNamespace(sessions=("2026-07-30",))) is full
    assert observed == [(full, effective)]
