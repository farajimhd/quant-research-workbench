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


def test_chart_projects_completed_order_fills_as_execution_annotations() -> None:
    chart_source = (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartPresentation.tsx").read_text(encoding="utf-8")
    renderer_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "ChartPanel.tsx").read_text(encoding="utf-8")

    assert "execution_annotations: executionAnnotations(trading, linkContext.symbol)" in chart_source
    assert "ENTRY FILL" in chart_source
    assert "EXIT FILL" in chart_source
    assert "drawExecutionAnnotations" in renderer_source


def test_debug_page_can_open_a_durable_completed_backtest_review() -> None:
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "BacktestDebugPage.tsx").read_text(encoding="utf-8")

    assert 'value="review">Completed Backtest review' in source
    assert "/api/trading/backtest/runs" in source
    assert "/review`" in source
    assert 'runtimeWorkspaceId="completed-review"' in source
