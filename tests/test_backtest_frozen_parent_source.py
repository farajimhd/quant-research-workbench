"""External process transport controls; these do not substitute a native proof."""
import json
from types import SimpleNamespace

import pytest

from src.backend import backtest_frozen_parent_source as source


def response():
    return dict(schema=1, strategy_number=75, commit=source._PARENT_COMMIT,
                clean=True, fingerprint=source._PARENT_FINGERPRINT,
                native_proof=source._PARENT_PROOF)


def transport(monkeypatch, tmp_path, *, value=None, stdout=None, returncode=0):
    monkeypatch.setattr(source, "_LAPTOP_ROOT", tmp_path)
    monkeypatch.setattr(source.platform, "node", lambda: "fixture-laptop")
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=returncode,
            stdout=stdout if stdout is not None else json.dumps(value or response()).encode())
    monkeypatch.setattr(source.subprocess, "run", run)
    return calls


def test_fixed_fresh_isolated_source_only_transport(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", "foreign")
    monkeypatch.setenv("PYTHONHOME", "foreign")
    calls = transport(monkeypatch, tmp_path)
    first = source.certify_frozen_performance_parent_source()
    second = source.certify_frozen_performance_parent_source()
    assert first == second and len(calls) == 2  # no approval cache
    argv, kwargs = calls[0]
    assert argv[1:4] == ["-I", "-B", "-c"]
    assert argv[-3:] == [source._PARENT_COMMIT, source._PARENT_FINGERPRINT, source._PARENT_PROOF]
    assert "PYTHONPATH" not in kwargs["env"] and "PYTHONHOME" not in kwargs["env"]
    assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert kwargs["cwd"] == tmp_path
    assert "certify_numbered_fixed_v4_projection(75)" in argv[4]
    assert "after != before" in argv[4]
    assert "--untracked-files=all" in argv[4]


@pytest.mark.parametrize("key,value", [("schema",True), ("strategy_number",75.0),
    ("clean",1), ("commit","a"*40), ("fingerprint","a"*64),
    ("native_proof","a"*64), ("extra",True)])
def test_catalog_shape_and_authority_drift_fail_closed(monkeypatch, tmp_path, key, value):
    payload = response(); payload[key] = value
    transport(monkeypatch, tmp_path, value=payload)
    with pytest.raises(ValueError, match="differs from catalog authority"):
        source.certify_frozen_performance_parent_source()


@pytest.mark.parametrize("stdout", [b"not json", b"[]", b"null", b'{"schema":1,"schema":1}', b"x"*4097])
def test_malformed_duplicate_or_oversized_response_rejected(monkeypatch, tmp_path, stdout):
    transport(monkeypatch, tmp_path, stdout=stdout)
    with pytest.raises((ValueError, RuntimeError)):
        source.certify_frozen_performance_parent_source()


def test_failed_parent_proof_does_not_forward_external_output(monkeypatch, tmp_path):
    transport(monkeypatch, tmp_path, returncode=1, stdout=b"private external detail")
    with pytest.raises(RuntimeError, match="^Frozen parent source-only proof failed$"):
        source.certify_frozen_performance_parent_source()

@pytest.mark.parametrize('field', ['commit','fingerprint','native_proof'])
def test_real_frozen_parent_program_rejects_wrong_expected_authority(field):
    # Actual isolated clean parent/native certificate; only the expected control
    # differs. No files, parent source, database or credential values modified.
    import os, subprocess, sys
    args=[str(source._LAPTOP_ROOT),source._PARENT_COMMIT,source._PARENT_FINGERPRINT,source._PARENT_PROOF]
    index={'commit':1,'fingerprint':2,'native_proof':3}[field]
    args[index]='0'*len(args[index])
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
    result=subprocess.run([sys.executable,'-I','-B','-c',source._PROGRAM,*args],
        cwd=source._LAPTOP_ROOT,env=env,capture_output=True,timeout=300)
    assert result.returncode != 0
    assert result.stdout == b''
    assert b'Frozen parent source identity' in result.stderr
