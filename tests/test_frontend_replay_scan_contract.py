from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "ReplayTradingPage.tsx"
CANVAS_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "CanvasConfigurationPage.tsx"
SCREENER_CONTAINERS = REPO_ROOT / "frontend" / "src" / "app" / "components" / "MarketScreenerContainers.tsx"


def test_next_action_reports_backend_progress_while_canvas_stays_static() -> None:
    source = REPLAY_PAGE.read_text(encoding="utf-8")

    assert "Canvas held at" in source
    assert "Loading signal + Watchlist history in the backend" in source
    assert "navigationElapsedSeconds" in source
    assert "navigation_search?.scanned_events" in source
    assert "Causal strategy scan progress" in source


def test_strategy_activity_pins_the_navigation_stop_record() -> None:
    canvas_source = CANVAS_PAGE.read_text(encoding="utf-8")
    container_source = SCREENER_CONTAINERS.read_text(encoding="utf-8")

    assert "strategyActivityFocusSequence={replayRun?.navigation_action?.sequence}" in canvas_source
    assert "focusSequence={strategyActivityFocusSequence}" in canvas_source
    assert "pinnedSequence={focusSequence}" in container_source
    assert "Number(row.sequence) === pinnedSequence" in container_source
