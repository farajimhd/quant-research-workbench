from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "ReplayTradingPage.tsx"
CANVAS_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "CanvasConfigurationPage.tsx"
SCREENER_CONTAINERS = REPO_ROOT / "frontend" / "src" / "app" / "components" / "MarketScreenerContainers.tsx"
TRADING_PRESENTATION = REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "tradingPresentation.tsx"
CHART_PRESENTATION = REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartPresentation.tsx"


def test_next_action_reports_backend_progress_while_canvas_stays_static() -> None:
    source = REPLAY_PAGE.read_text(encoding="utf-8")

    assert "Canvas held at" in source
    assert "Loading signal + Watchlist history in the backend" in source
    assert "navigationElapsedSeconds" in source
    assert "navigation_search?.scanned_events" in source
    assert "Causal strategy scan progress" in source


def test_trading_audits_do_not_truncate_canonical_rows_before_table_filtering() -> None:
    source = TRADING_PRESENTATION.read_text(encoding="utf-8")
    positions_source = source.split("function PositionsPreview", 1)[1].split("function PositionDetail", 1)[0]
    orders_source = source.split("function OrdersPreview", 1)[1].split("function OrderDetail", 1)[0]
    executions_source = source.split("function ExecutionsPreview", 1)[1].split("function ClosedTradesPreview", 1)[0]
    round_trips_source = source.split("function ClosedTradesPreview", 1)[1].split("function TradingTabs", 1)[0]

    assert "data.position_lifecycles" in source
    for audit_source in (positions_source, orders_source, executions_source, round_trips_source):
        assert ".slice(0, settings.limit)" not in audit_source


def test_completed_position_manager_reports_an_outdated_backend_contract() -> None:
    source = TRADING_PRESENTATION.read_text(encoding="utf-8")

    assert "lifecycleProjectionAvailable" in source
    assert "Position lifecycle data is unavailable from the running backend" in source
    assert "will not reinterpret FIFO execution fragments as positions" in source


def test_charts_quotes_scopes_trade_annotations_to_the_main_chart() -> None:
    canvas_source = CANVAS_PAGE.read_text(encoding="utf-8")
    chart_source = CHART_PRESENTATION.read_text(encoding="utf-8")

    assert "showTradeAnnotations = true" in chart_source
    assert chart_source.count("showTradeAnnotations ?") == 2
    assert canvas_source.count("showTradeAnnotations={false}") == 2


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

    assert "execution_annotations: showTradeAnnotations ? executionAnnotations(trading, linkContext.symbol) : []" in chart_source
    assert "ENTRY FILL" in chart_source
    assert "EXIT FILL" in chart_source
    assert "drawExecutionAnnotations" in renderer_source


def test_debug_page_can_open_a_durable_completed_backtest_review() -> None:
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "BacktestDebugPage.tsx").read_text(encoding="utf-8")

    assert 'value="review">Completed Backtest review' in source
    assert "/api/trading/backtest/runs" in source
    assert "/review`" in source
    assert 'runtimeWorkspaceId="completed-review"' in source


def test_completed_backtest_chart_uses_durable_trading_evidence_and_full_session_history() -> None:
    canvas_source = CANVAS_PAGE.read_text(encoding="utf-8")
    chart_data_source = (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartData.ts").read_text(encoding="utf-8")

    assert "durableTerminalReview" in canvas_source
    assert "historicalTradingContainers" in canvas_source
    assert 'readOnly={runMode !== "replay"}' in canvas_source
    assert 'const fullSession = runtimeMode === "backtest" || runtimeMode === "backtest_debug"' in canvas_source
    assert "full_session: fullSession" in chart_data_source
    assert "chartFullSessionPageSize" in chart_data_source
    assert "chartPanelRef.current?.fitFirstDay()" in (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartPresentation.tsx").read_text(encoding="utf-8")
