import pytest

from research.rl_trading.v1 import build_phase2


def test_progress_publication_retries_transient_windows_sharing_error(monkeypatch):
    attempts = []

    def write(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError('progress reader holds the old file')

    monkeypatch.setattr(build_phase2,'write',write)
    monkeypatch.setattr(build_phase2,'sleep',lambda _:None)
    build_phase2.write_progress('progress.json',{'completed':1})
    assert len(attempts) == 2

    monkeypatch.setattr(build_phase2,'write',lambda *args, **kwargs:
        (_ for _ in ()).throw(PermissionError('persistent denial')))
    with pytest.raises(PermissionError,match='persistent denial'):
        build_phase2.write_progress('progress.json',{'completed':1})
