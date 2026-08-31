from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "ReplayTradingPage.tsx"
CANVAS_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "CanvasConfigurationPage.tsx"
SCREENER_CONTAINERS = REPO_ROOT / "frontend" / "src" / "app" / "components" / "MarketScreenerContainers.tsx"
TRADING_PRESENTATION = REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "tradingPresentation.tsx"
CHART_PRESENTATION = REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartPresentation.tsx"
TRADING_WORKSPACE = REPO_ROOT / "frontend" / "src" / "app" / "components" / "TradingWorkspace.tsx"


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
    assert chart_source.count("showTradeAnnotations ?") == 1
    assert "execution_annotations: []" in chart_source
    assert canvas_source.count("showTradeAnnotations={false}") == 2


def test_strategy_activity_pins_the_navigation_stop_record() -> None:
    canvas_source = CANVAS_PAGE.read_text(encoding="utf-8")
    container_source = SCREENER_CONTAINERS.read_text(encoding="utf-8")

    assert "strategyActivityFocusSequence={replayRun?.navigation_action?.sequence}" in canvas_source
    assert "focusSequence={strategyActivityFocusSequence}" in canvas_source
    assert "pinnedSequence={focusSequence}" in container_source
    assert "Number(row.sequence) === pinnedSequence" in container_source


def test_chart_projects_position_lifecycles_with_compact_position_actions() -> None:
    chart_source = (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartPresentation.tsx").read_text(encoding="utf-8")
    renderer_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "ChartPanel.tsx").read_text(encoding="utf-8")

    assert "trade_annotations: showTradeAnnotations ? positionLifecycleAnnotations(trading, linkContext.symbol) : []" in chart_source
    assert "trading?.position_lifecycles" in chart_source
    assert "execution_annotations: []" in chart_source
    assert "positionExecutionActions" in chart_source
    assert '"Long" : "Short"' not in chart_source
    assert '"Short" : "Long"' in chart_source
    assert "actions.slice(1, -1)" in chart_source
    assert 'profit_target: "Target filled"' in chart_source
    assert 'if (reason.includes("macd")) return "MACD exit"' in chart_source
    assert 'if (reason.includes("stop")) return "Stop exit"' in chart_source
    assert 'if (reason.includes("target") || fallbackKind === "profit_target") return "Target filled"' in chart_source
    assert 'String(row.exit_reason || "")' in chart_source
    assert '"Targets complete"' not in chart_source
    assert '"Trim"' not in chart_source
    assert 'execution_role' in chart_source
    assert '`${role}:${action.side}:${second}:${priceTick}`' in chart_source
    assert "closedTradeAnnotations" not in chart_source
    assert "class TradeAnnotationPrimitive implements ISeriesPrimitive<Time>" in renderer_source
    assert "candleSeries.attachPrimitive(tradeAnnotationPrimitive)" in renderer_source
    assert "tradeAnnotationPrimitiveRef.current?.setState" in renderer_source
    assert "drawTradeAnnotationPrimitiveGeometry" in renderer_source
    assert "const ratio = clampNumber((time - leftCandle.time) / duration" in renderer_source
    assert "return leftX + (rightX - leftX) * ratio" in renderer_source
    assert "The triangle tip is the exact event-time / execution-price coordinate" in renderer_source
    assert "const span = clippedTradeSpan(entryX, exitX, width)" in renderer_source
    assert "anchor - 3" in renderer_source
    draw_source = renderer_source.split("function drawRegions", 1)[1].split("function drawSessionRegions", 1)[0]
    assert "drawTradeAnnotations(" not in draw_source
    assert "drawExecutionAnnotations(" not in draw_source


def test_structural_history_can_span_all_loaded_chart_bars() -> None:
    renderer_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "ChartPanel.tsx").read_text(encoding="utf-8")
    styles_source = (REPO_ROOT / "frontend" / "src" / "app" / "styles.css").read_text(encoding="utf-8")
    chart_source = CHART_PRESENTATION.read_text(encoding="utf-8")
    chart_data_source = (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartData.ts").read_text(encoding="utf-8")
    history_api_source = (REPO_ROOT / "services" / "qmd_history_gateway" / "src" / "api.rs").read_text(encoding="utf-8")
    history_cache_source = (REPO_ROOT / "services" / "qmd_history_gateway" / "src" / "cache.rs").read_text(encoding="utf-8")
    structure_checkpoint_source = (REPO_ROOT / "services" / "qmd_history_gateway" / "src" / "structure_checkpoint.rs").read_text(encoding="utf-8")

    assert "show on all loaded bars" in renderer_source
    assert "historyBars: event.target.checked ? 0 : 200" in renderer_source
    assert "if (historyBars === 0) return Number.NEGATIVE_INFINITY" in renderer_source
    assert "unifiedStructureSegments(rows, chartEnd)" in chart_source
    assert "row.qmd_structure_unified_level_delta" in chart_source
    assert "delta.removed" in chart_source
    assert "delta.upserts" in chart_source
    assert '`${level.unified_level_id}:${level.side}`' in chart_source
    assert "active.delete(key)" in chart_source
    assert "existing.level = level" in chart_source
    assert "Evidence changes reinforce the same causal level episode" in chart_source
    assert "const causalStart" in chart_source
    assert "start: causalStart" in chart_source
    assert "start: Math.max(time" not in chart_source
    assert "unifiedEvidenceSignature" not in chart_source
    assert "historyBarsDefault: 0" in chart_source
    assert 'annotationKind: "unified-structure-level"' in chart_source
    assert "borderOpacity: 0" in chart_source
    assert "probabilityLineRatio: reactionProbability" in chart_source
    assert 'zone.annotationKind === "unified-structure-level"' in renderer_source
    assert "span.left + span.width * probability" in renderer_source
    projected_history = history_api_source.split("fn project_chart_snapshot", 1)[1].split("fn compact_projected_unified_structure_history", 1)[0]
    assert "snapshot.indicator_projection.take()" in projected_history
    assert "compact_projected_unified_structure_history(&mut projected)" in projected_history
    assert "fn unified_structure_projection" in history_cache_source
    assert 'object.insert("sources".to_string(), json!([]))' in history_cache_source
    assert "CacheProfile::Structure" in history_cache_source
    assert "(bars_only || structure_only).then_some(1)" in history_cache_source
    assert "rebuild_trade_structure_checkpoint" in history_cache_source
    assert "rebuild_structure_checkpoint_inner(config, source, request, Some(1))" in structure_checkpoint_source
    assert "source.stream_structure_ordered_filtered(" in structure_checkpoint_source
    assert "CacheProfile::Bars(timeframe.clone())" in history_cache_source
    assert "(CacheProfile::Bars(_), Some(timeframe)) => vec![timeframe.clone()]" in history_cache_source
    assert 'column !== "qmd_structure_unified_levels"' in chart_data_source
    assert 'const unifiedStructureColumns = "bar_start,qmd_structure_unified_levels"' in chart_data_source
    assert 'const unifiedStructureAsPrimary' not in chart_data_source
    assert 'indicator_columns: baseIndicatorColumns, stage: "bars"' in chart_data_source
    assert "const unifiedStructureRequest = progressive && unifiedStructureSelected" in chart_data_source
    assert 'current.historyNotice === "Loading requested indicators..."' in chart_data_source
    assert "!standardIndicatorsRequested" in chart_data_source
    assert "indicator_columns: unifiedStructureColumns" in chart_data_source
    assert "full_session: true" in chart_data_source
    assert "stage: \"full\", timeframe: UNIFIED_STRUCTURE_TIMEFRAME" in chart_data_source
    assert 'const UNIFIED_STRUCTURE_TIMEFRAME: CanvasChartTimeframe = "1s"' in chart_data_source
    assert "unifiedStructureProjectionRows(payload.indicators, cutoffMs)" in chart_data_source
    assert "admittedTimes.has(barStartTime(row)) || isUnifiedStructureProjectionRow(row)" in chart_data_source
    standard_page_handler = chart_data_source.split("void standardIndicatorPage?.then((payload) => {", 1)[1].split("}, [auxiliaryProjection", 1)[0]
    assert "closedRowsAtCutoff(payload.indicators, timeframe, cutoffMs)" in standard_page_handler
    assert "const unifiedStructurePage" not in chart_data_source
    assert "function chartIdentityKey(ticker: string, sessionDate: string" in chart_data_source
    assert "function chartIdentityKey(ticker: string, indicatorColumns: string" not in chart_data_source
    assert "mergeIndicatorRowsByTime(current.indicators, rows)" in chart_data_source
    assert "function limitIndicatorRowsToLatest" in chart_data_source
    assert "const ordinaryRows = normalized.filter((row) => !isUnifiedStructureProjectionRow(row))" in chart_data_source
    assert "isUnifiedStructureProjectionRow(row) || retainedOrdinaryTimes.has(barStartTime(row))" in chart_data_source
    assert "const indicators = limitIndicatorRowsToLatest(" in chart_data_source
    assert "hasUnifiedStructureProjection(replacement)" in chart_data_source
    assert "delete next.qmd_structure_unified_levels" in chart_data_source
    assert "delete next.qmd_structure_unified_level_delta" in chart_data_source
    assert "if (!Array.isArray(snapshot) && delta)" in chart_source
    assert "structure_projection: Vec<Value>" in history_cache_source
    assert "prepared_structure_projection(artifact" in history_cache_source
    assert "let structure_only = matches!(&profile, CacheProfile::Structure(_))" in history_cache_source
    assert "let bars_only = matches!(&profile, CacheProfile::Bars(_))" in history_cache_source
    assert "StructureProjectionBuilder" in history_cache_source
    assert "apply_event_without_snapshot" in history_cache_source
    assert "statusMessage={liveChart.historyNotice}" not in chart_source
    assert "statusMessage?: string" not in renderer_source
    assert "chart-update-status" not in renderer_source
    assert ".chart-update-status" not in styles_source
    assert "Loading ${timeframe} chart…" in chart_data_source
    assert "macroCoverageNotice(payload)" not in chart_data_source
    assert "Daily authority is stale" not in chart_data_source
    assert "prepared_bar_cache_root" in history_cache_source
    assert "load_prepared_bar_cache" in history_cache_source
    assert "store_prepared_bar_cache" in history_cache_source
    structure_followup = chart_data_source.split(".then((payload) => {", 2)[2].split(".catch((reason) => {", 1)[0]
    assert 'historyError: ""' not in structure_followup


def test_frontend_loading_states_use_shared_centered_treatment() -> None:
    loading_state = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "LoadingState.tsx").read_text(encoding="utf-8")
    styles = (REPO_ROOT / "frontend" / "src" / "app" / "styles.css").read_text(encoding="utf-8")
    canvas_source = CANVAS_PAGE.read_text(encoding="utf-8")

    assert 'className={classes}' in loading_state
    assert 'className="loading-spinner"' in loading_state
    assert 'role="status"' in loading_state
    assert ".app-loading-state" in styles
    assert "justify-content: center" in styles
    assert "Opening Replay focus canvas" not in canvas_source
    assert "Restoring the selected container against the active run clock" not in canvas_source
    assert "LoadingState fill" in canvas_source


def test_canvas_registry_retries_transient_failures_before_blocking_container_adds() -> None:
    source = TRADING_WORKSPACE.read_text(encoding="utf-8")

    assert "const loadRegistry = (attempt: number)" in source
    assert "if (attempt < 2)" in source
    assert "loadRegistry(attempt + 1)" in source
    assert 'setRegistryError("")' in source


def test_unified_structure_scores_can_filter_loaded_levels_without_a_history_request() -> None:
    renderer_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "ChartPanel.tsx").read_text(encoding="utf-8")
    styles_source = (REPO_ROOT / "frontend" / "src" / "app" / "styles.css").read_text(encoding="utf-8")
    chart_source = CHART_PRESENTATION.read_text(encoding="utf-8")

    assert "priceZoneMeetsUnifiedFilters(zone, settings)" in renderer_source
    assert "minimumSalience" in renderer_source
    assert "minimumReactionProbability" in renderer_source
    assert "minimumHoldProbability" in renderer_source
    assert "minimumConfidence" in renderer_source
    assert "Levels must meet every enabled evidence threshold" in renderer_source
    assert "Changes apply immediately to loaded chart data" in renderer_source
    assert "showUnifiedSupport" in renderer_source
    assert "showUnifiedResistance" in renderer_source
    assert "showUnifiedActive" in renderer_source
    assert "showUnifiedBroken" in renderer_source
    assert "showUnifiedRoleFlipped" in renderer_source
    assert 'zone.latest ? settings.showUnifiedActive : settings.showUnifiedBroken' in renderer_source
    assert "legendSettingsRef.current" in renderer_source
    assert "holdProbability: boundedUnit(level.hold_probability)" in chart_source
    assert "roleFlipCount: Number(level.role_flip_count ?? 0)" in chart_source
    assert 'className="legend-configure-button"' in renderer_source
    assert 'className="legend-label" title={item.label}' in renderer_source
    assert '`${selectedZones.length.toLocaleString("en-US")} / ${presetZoneCount.toLocaleString("en-US")}`' in renderer_source
    assert "const allHistoryBars = (item.historyBars ?? 20) === 0;" in renderer_source
    assert "disabled={allHistoryBars}" in renderer_source
    assert 'allHistoryBars ? "All"' in renderer_source
    assert 'const supportsUnifiedFilters = itemZones.some((zone) => zone.annotationKind === "unified-structure-level");' in renderer_source
    assert "supportsHistoryWindow: supportsUnifiedFilters || itemZones.some((zone) => !zone.latest)" in renderer_source
    assert "supportsSemanticColorEditing: itemZones.some" in renderer_source
    assert "supportsStroke: !itemZones.some" in renderer_source
    assert "supportsHistoryWindow: selectedZones.some" not in renderer_source
    assert ".legend-row-actions > button:not(.legend-configure-button)" in styles_source
    assert ".chart-legend-row:hover .legend-row-actions > button" in styles_source
    assert "width: min(340px, calc(100% - 20px));" in styles_source
    assert ".legend-label {\n  overflow: hidden;\n  min-width: 0;\n  flex: 0 1 auto;" in styles_source
    assert ".legend-value {\n  overflow: hidden;\n  max-width: 88px;\n  min-width: 0;\n  color: var(--foreground);\n  flex: 0 0 auto;" in styles_source
    assert ".legend-row-actions {\n  display: inline-flex;\n  flex: 0 0 auto;" in styles_source
    assert "margin-left: 0;" in styles_source
    assert ".chart-legend-editor .legend-history-control legend" in styles_source


def test_debug_page_can_open_a_durable_completed_backtest_review() -> None:
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "BacktestDebugPage.tsx").read_text(encoding="utf-8")

    assert 'value="review">Completed Backtest review' in source
    assert "/api/trading/backtest/runs" in source
    assert "/review`" in source
    assert 'runtimeWorkspaceId="completed-review"' in source


def test_completed_backtest_focus_rehydrates_after_backend_restart() -> None:
    source = CANVAS_PAGE.read_text(encoding="utf-8")

    focus_source = source.split("function ReplayCanvasFocusPage", 1)[1].split("function ReplayFocusTransportStatus", 1)[0]
    assert 'runMode !== "backtest" || status !== 404' in focus_source
    assert '/api/trading/backtest/runs/${encodeURIComponent(runId)}/review' in focus_source
    assert 'method: "POST"' in focus_source


def test_chart_memo_signature_includes_completed_position_evidence() -> None:
    source = CANVAS_PAGE.read_text(encoding="utf-8")

    signature_source = source.split("function tradingPositionSignature", 1)[1].split("function strategyDecisionEvents", 1)[0]
    assert "trading?.position_lifecycles" in signature_source
    assert "trading?.executions" in signature_source
    assert "row.lifecycle_id" in signature_source


def test_completed_backtest_chart_uses_durable_trading_evidence_and_full_session_history() -> None:
    canvas_source = CANVAS_PAGE.read_text(encoding="utf-8")
    chart_data_source = (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartData.ts").read_text(encoding="utf-8")

    assert "durableTerminalReview" in canvas_source
    assert "historicalTradingContainers" in canvas_source
    assert 'readOnly={runMode !== "replay"}' in canvas_source
    assert 'const fullSession = runtimeMode === "backtest" || runtimeMode === "backtest_debug"' in canvas_source
    assert "full_session: fullSession" in chart_data_source
    assert "chartFullSessionPageSize" in chart_data_source
    chart_presentation_source = (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartPresentation.tsx").read_text(encoding="utf-8")
    renderer_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "ChartPanel.tsx").read_text(encoding="utf-8")
    assert "deferInitialFitUntilLoaded={fullSessionReview}" in chart_presentation_source
    assert "chartPanelRef.current?.fitFirstDay()" not in chart_presentation_source
    assert "userViewportClaimedRef.current" in renderer_source
    assert "shouldAutoFit && deferInitialFitUntilLoaded && loading" in renderer_source
    explicit_command = renderer_source.index("function executeViewportCommand")
    assert "userViewportClaimedRef.current = true;" in renderer_source[explicit_command:explicit_command + 500]


def test_chart_does_not_autoscale_on_transient_coordinate_read_failure() -> None:
    renderer_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "ChartPanel.tsx").read_text(encoding="utf-8")

    transient_failure = renderer_source.index("Lightweight Charts can reject coordinate reads")
    invalid_transform = renderer_source.index("if (invalidTransform)", transient_failure)
    assert "return { recovered: false, retry: true };" in renderer_source[transient_failure:invalid_transform]
    assert "applyOptions({ autoScale: true })" not in renderer_source[transient_failure:invalid_transform]
