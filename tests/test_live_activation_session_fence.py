import pytest

from src.backend.live_activation_session_fence import KeeperActivationSessionFence
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_signal_completion_keeper import FakeKazoo


def _fence():
    client = FakeKazoo()
    client.add_listener = lambda _listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    return KeeperActivationSessionFence(session), client


def test_activation_session_epoch_excludes_cold_reader_and_rejects_stale_owner():
    fence, _ = _fence()
    assert fence.acquire("2026-09-24", owner_id="writer") == 1
    assert fence.acquire("2026-09-24", owner_id="recovery") is None
    assert fence.is_current("2026-09-24", owner_id="writer", epoch=1)
    assert fence.release("2026-09-24", owner_id="writer", epoch=1)
    assert fence.acquire("2026-09-24", owner_id="recovery") == 2
    assert not fence.is_current("2026-09-24", owner_id="writer", epoch=1)


def test_activation_session_fence_rejects_malformed_session():
    fence, _ = _fence()
    with pytest.raises(ValueError, match="session key"):
        fence.acquire("2026-99-99", owner_id="writer")
