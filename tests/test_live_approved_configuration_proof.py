from unittest.mock import patch

import pytest

from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.backend.live_approved_configuration_proof import cold_approved_configuration_proof


def _release() -> CertifiedStrategyOneConfiguration:
    return CertifiedStrategyOneConfiguration(
        attempt_id="00000000-0000-0000-0000-000000000001",
        payload_hash="a" * 64, node_hash="b" * 64,
        source_candidate_id="candidate", source_candidate_hash="c" * 64,
        token="token", payload={"run_plan": {"run_plan_id": "plan"}},
    )


class _ApprovalReader:
    def read_head(self, mode):
        return mode


def test_proof_rechecks_configuration_and_approval_heads():
    release = _release()
    with patch("src.backend.live_approved_configuration_proof.certify_strategy_one_configuration",
               return_value=release) as certify, patch(
        "src.backend.live_approved_configuration_proof.verify_selected_approval",
        return_value={"content_hash": "d" * 64}) as approval:
        proof = cold_approved_configuration_proof(object(), _ApprovalReader(), mode="live")
        assert proof.identity == release.revision()["revision_id"]
        assert proof.content_hash == release.payload_hash
        proof.assert_current()
        assert certify.call_count == 3
        assert approval.call_count == 3


def test_proof_rejects_revoked_approval_or_changed_release():
    release = _release()
    with patch("src.backend.live_approved_configuration_proof.certify_strategy_one_configuration",
               return_value=release), patch(
        "src.backend.live_approved_configuration_proof.verify_selected_approval",
        side_effect=[{"content_hash": "d" * 64}] * 2 +
                    [{"content_hash": "e" * 64}]):
        proof = cold_approved_configuration_proof(object(), _ApprovalReader(), mode="live")
        with pytest.raises(RuntimeError, match="selected approval changed"):
            proof.assert_current()
    with patch("src.backend.live_approved_configuration_proof.certify_strategy_one_configuration",
               side_effect=[release, release, None]), patch(
        "src.backend.live_approved_configuration_proof.verify_selected_approval",
        return_value={"content_hash": "d" * 64}):
        proof = cold_approved_configuration_proof(object(), _ApprovalReader(), mode="live")
        with pytest.raises(RuntimeError, match="configuration release changed"):
            proof.assert_current()
