from copy import deepcopy
from uuid import uuid4
import json
import pytest

from src.backend.backtest_strategy_forty_five_configuration import compile_configuration, verify_envelope, certify_configuration
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes


def release():
    return compile_configuration(approved_code_commit="a" * 40,
        approved_code_fingerprint="b" * 64, approval_reference="user-approved-app-comparison")


def test_real_launch_definition_uses_strategy45_sources_without_strategy1_tokens():
    from datetime import date, time
    from src.backend.replay_run_service import ReplayRunDefinition
    from src.trading_runtime.runtime import RunMode
    market = dict(token="a" * 64, execution_interval=dict(milliseconds=100))
    for name in ("history", "identity", "structure", "price", "liquidity"):
        market[f"strategy_forty_five_{name}_token"] = "b" * 64
    definition = ReplayRunDefinition(session_date=date(2026,9,3), start_time=time(4),
        end_time=time(9,30), initial_cash=10000, mode=RunMode.BACKTEST,
        execution_interval="100ms", market_data_plan=market,
        configuration_revision=dict(revision_id="strategy-one-45:"+str(uuid4()),payload=release()["payload"]))
    assert definition.configuration_revision["payload"]["strategy"]["revision"] == 45


def test_independent_configuration_roundtrips_all_normalized_nodes():
    envelope = release()
    payload, nodes = verify_envelope(envelope)
    assert payload["strategy"]["strategy_id"] == "squeeze-grid-strategy"
    assert payload["assignments"] == []
    assert payload["run_plan"]["initial_cash"] == 10_000.
    assert len(nodes) == envelope["node_count"]
    attempt = str(uuid4())
    class Reader:
        def execute(self, query):
            assert query.startswith("SELECT ")
            if "FROM arte.strategy_one_configuration_release" in query:
                return json.dumps(dict(release_attempt_id=attempt, strategy_id="squeeze-grid-strategy",
                    **{k:v for k,v in envelope.items() if k != "payload"}))
            return "\n".join(json.dumps(row) for row in nodes)
    certificate = certify_configuration(Reader())
    assert certificate.revision()["label"] == "Strategy 45"
    assert certificate.revision()["available_run_plans"][0]["strategy_id"] == "squeeze-grid-strategy"


@pytest.mark.parametrize("field", ["policy", "cash", "mode", "source"])
def test_policy_and_source_changes_cannot_hide_behind_new_envelope_hashes(field):
    envelope = deepcopy(release())
    if field == "policy":
        envelope["payload"]["portfolio"]["policies"][0]["entry_fee_buffer_bps"] = 50.
    elif field == "cash":
        envelope["payload"]["run_plan"]["initial_cash"] = 100_000.
    elif field == "mode":
        envelope["payload"]["strategy"]["numbered_release"]["publication_mode"] = "live"
    else:
        envelope["source_candidate_hash"] = "c" * 64
    with pytest.raises(ValueError):
        verify_envelope(envelope)
