from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "ReplayTradingPage.tsx"


def test_next_action_keeps_canvas_static_during_backend_scan() -> None:
    source = REPLAY_PAGE.read_text(encoding="utf-8")

    assert "Backend scan in progress · Canvas held at" in source
    assert "Loading signal + Watchlist history in the backend" in source
    assert "navigationElapsedSeconds" not in source
    assert "navigation_search?.scanned_events" not in source
