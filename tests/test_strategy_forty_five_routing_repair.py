from hashlib import sha256
from pathlib import Path
import pytest
from pipelines.strategy_one import strategy_forty_five_routing_repair as proof
from src.backend.historical_runtime_versions import _fingerprint


def test_exact_repair_preserves_parent_seal_and_unrelated_changes_do_not(tmp_path):
    h = sha256(Path(proof.__file__).read_bytes().replace(b"\r\n",b"\n")).hexdigest()
    directory = tmp_path / "src/backend"
    directory.mkdir(parents=True)
    route = directory / "replay_run_service.py"
    preflight = directory / "backtest_strategy_forty_five_preflight.py"
    route.write_text(proof.OLD_BRANCH+"\n",encoding="utf-8")
    preflight.write_text(proof.OLD_CHECK+"\n",encoding="utf-8")
    original = _fingerprint(tmp_path,[route,preflight])
    route.write_text(proof.NEW_BRANCH+"\n",encoding="utf-8")
    preflight.write_text(proof.IMPORT+proof.NEW_CHECK.format(digest=h)+"\n",encoding="utf-8")
    assert proof.qualified_source_fingerprint(proof_digest=h,root=tmp_path) == original
    route.write_text(route.read_text()+"unrelated_change = True\n",encoding="utf-8")
    assert proof.qualified_source_fingerprint(proof_digest=h,root=tmp_path) != original
    with pytest.raises(RuntimeError,match="proof source changed"):
        proof.qualified_source_fingerprint(proof_digest="0"*64,root=tmp_path)
