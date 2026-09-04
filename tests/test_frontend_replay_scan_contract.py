from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "ReplayTradingPage.tsx"
CANVAS_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "CanvasConfigurationPage.tsx"
SCREENER_CONTAINERS = REPO_ROOT / "frontend" / "src" / "app" / "components" / "MarketScreenerContainers.tsx"
TRADING_PRESENTATION = REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "tradingPresentation.tsx"
CHART_PRESENTATION = REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartPresentation.tsx"
TABLE_FILTERS = REPO_ROOT / "frontend" / "src" / "app" / "components" / "TableColumnFilters.tsx"
TRADING_WORKSPACE = REPO_ROOT / "frontend" / "src" / "app" / "components" / "TradingWorkspace.tsx"
MARKET_MICROSTRUCTURE = REPO_ROOT / "frontend" / "src" / "app" / "components" / "MarketMicrostructureContainers.tsx"


def test_next_action_reports_backend_progress_while_canvas_stays_static() -> None:
    source = REPLAY_PAGE.read_text(encoding="utf-8")

    assert "Canvas held at" in source
    assert "Loading signal + Watchlist history in the backend" in source
    assert "navigationElapsedSeconds" in source
    assert "navigation_search?.scanned_events" in source
    assert "Causal strategy scan progress" in source


def test_trading_audits_do_not_truncate_canonical_rows_before_table_filtering() -> None:
    source = TRADING_PRESENTATION.read_text(encoding="utf-8")
    positions_source = source.split("function PositionsPreview", 1)[1].split("function PositionLifecycleModal", 1)[0]
    orders_source = source.split("function OrdersPreview", 1)[1].split("function OrderDetail", 1)[0]
    executions_source = source.split("function ExecutionsPreview", 1)[1].split("function ClosedTradesPreview", 1)[0]
    round_trips_source = source.split("function ClosedTradesPreview", 1)[1].split("function TradingTabs", 1)[0]

    assert "data.position_lifecycles" in source
    for audit_source in (positions_source, orders_source, executions_source, round_trips_source):
        assert ".slice(0, settings.limit)" not in audit_source


def test_large_execution_audits_page_the_rendered_dom_without_truncating_evidence() -> None:
    source = TRADING_PRESENTATION.read_text(encoding="utf-8")

    assert "const pageSize = 100" in source
    assert "const pageRows = visibleRows.slice(activePage * pageSize, (activePage + 1) * pageSize)" in source
    assert "pageRows.map((row, index)" in source
    assert "{activePage + 1} / {pageCount}" in source


def test_historical_tape_request_matches_the_retained_point_in_time_window() -> None:
    source = MARKET_MICROSTRUCTURE.read_text(encoding="utf-8")

    assert "const MARKET_EVENT_SOURCE_LIMIT = MARKET_EVENT_HISTORY_LIMIT" in source
    assert ".slice(-MARKET_EVENT_SOURCE_LIMIT)" in source


def test_completed_position_manager_reports_an_outdated_backend_contract() -> None:
    source = TRADING_PRESENTATION.read_text(encoding="utf-8")

    assert "lifecycleProjectionAvailable" in source
    assert "Position lifecycle data is unavailable from the running backend" in source
    assert "will not reinterpret FIFO execution fragments as positions" in source


def test_charts_quotes_limits_position_presentation_to_intraday_charts() -> None:
    canvas_source = CANVAS_PAGE.read_text(encoding="utf-8")
    chart_source = CHART_PRESENTATION.read_text(encoding="utf-8")

    assert "showTradeAnnotations = true" in chart_source
    assert "showTradeAnnotations && supportsPositionPresentation(timeframe)" in chart_source
    assert "return !MACRO_TIMEFRAMES.has(timeframe)" in chart_source
    assert "strategyPresentationEnabled={strategyPresentationAvailable}" in chart_source
    assert "strategyPresentationAvailable && activePosition" in chart_source
    assert "execution_annotations: []" in chart_source
    assert "showTradeAnnotations={false}" not in canvas_source


def test_charts_share_causal_split_events_with_timeframe_defaults_and_controls() -> None:
    canvas_source = CANVAS_PAGE.read_text(encoding="utf-8")
    chart_source = CHART_PRESENTATION.read_text(encoding="utf-8")
    configuration_source = (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "configuration.ts").read_text(encoding="utf-8")
    settings_source = (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "settings.ts").read_text(encoding="utf-8")
    split_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "chartSplitEvents.ts").read_text(encoding="utf-8")
    renderer_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "ChartPanel.tsx").read_text(encoding="utf-8")
    live_window_source = (REPO_ROOT / "frontend" / "src" / "features" / "live-trading" / "LiveChartWindow.tsx").read_text(encoding="utf-8")
    live_charts_source = (REPO_ROOT / "frontend" / "src" / "features" / "live-trading" / "LiveChartsContainer.tsx").read_text(encoding="utf-8")

    assert "stockSplitEvents=" not in canvas_source
    assert "useStockSplitEvents(linkContext.symbol, Date.parse(changeAsOf), chartSettings.showSplitEvents)" in chart_source
    assert 'showSplitEvents: nextTimeframe === timeframe ? chartSettings.showSplitEvents : nextTimeframe === "1d"' in chart_source
    assert "onShowSplitEventsChange" in chart_source
    assert "showSplitEvents: true" in configuration_source
    assert configuration_source.count("showSplitEvents: false") >= 3
    assert 'typeof stored?.showSplitEvents === "boolean" ? stored.showSplitEvents : timeframe === "1d"' in settings_source
    assert "Show stock split events" in renderer_source
    assert "Date.parse(`${event.execution_date}T12:00:00Z`)" in split_source
    assert 'kind: "split"' in split_source
    assert 'splitEvents.error ? "Split events unavailable"' in chart_source
    assert "/ticker-facts/${encodeURIComponent(ticker)}/splits" in split_source
    assert "readSplitVisibility(chart.id, mainTimeframe)" in live_window_source
    assert "selectedTime === null ? Number.NaN : selectedTime * 1000" in live_window_source
    assert "timeline_events: visible ? [...existing" in live_window_source
    assert live_charts_source.count("onShowSplitEventsChange=") == 3
    assert "payload.timeline_events" in renderer_source
    assert ".map((event) => ({ time: event.time }))" in renderer_source
    assert "timeToCoordinate(event.time as Time)" in renderer_source
    assert "chart.timeScale().height() + 6" in renderer_source


def test_peak_unrealized_is_shared_by_performance_and_position_surfaces() -> None:
    performance_source = (REPO_ROOT / "frontend" / "src" / "features" / "trading-performance" / "TradingPerformance.tsx").read_text(encoding="utf-8")
    position_source = (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "tradingPresentation.tsx").read_text(encoding="utf-8")
    table_presentation_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "TablePresentation.tsx").read_text(encoding="utf-8")
    live_portfolio_source = (REPO_ROOT / "frontend" / "src" / "features" / "live-trading" / "LivePortfolioContainer.tsx").read_text(encoding="utf-8")
    live_metrics_source = (REPO_ROOT / "frontend" / "src" / "features" / "live-trading" / "liveMetrics.tsx").read_text(encoding="utf-8")

    assert 'metric("unrealized_pnl", "Open unrealized"' in performance_source
    assert 'metric("max_unrealized_pnl", "Peak unrealized"' in performance_source
    assert "headlineMetrics(snapshot)" in performance_source
    assert '["symbol", "open_unrealized", "peak_unrealized", "side"' in position_source
    assert 'unrealized(_|$)/.test(key)) presentationValueType = "money"' in table_presentation_source
    assert 'label="Open unrealized"' in position_source
    assert 'label="Peak unrealized"' in position_source
    assert "position.max_unrealized_pnl" in live_portfolio_source
    assert live_metrics_source.count('label: "Open Unrealized"') == 2
    assert live_metrics_source.count('label: "Peak Unrealized"') == 2


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
    styles_source = (REPO_ROOT / "frontend" / "src" / "app" / "styles.css").read_text(encoding="utf-8")
    theme_source = (REPO_ROOT / "frontend" / "src" / "app" / "theme.ts").read_text(encoding="utf-8")

    assert "() => strategyPresentationAvailable ? positionLifecycleAnnotations(chartTrading, linkContext.symbol) : []" in chart_source
    assert 'consequential_only: "true"' in chart_source
    assert 'if (runId) parameters.set("run_id", runId)' in chart_source
    assert 'ticker: linkContext.symbol' in chart_source
    assert "strategy_chart_activity: scopedStrategyActivity" in chart_source
    assert "trade_annotations: tradeAnnotations" in chart_source
    assert "trading?.position_lifecycles" in chart_source
    assert 'const status = String(row.status || "").toLowerCase() === "closed" ? "closed" : "open"' in chart_source
    assert 'const endTime = exitTime ?? asOfTime' in chart_source
    assert 'annotation.status !== "open"' in renderer_source
    assert 'trade.endTime ?? trade.exitTime ?? trade.entryTime' in renderer_source
    assert renderer_source.index("syncTradeAnnotationPrimitive(payload, timeline)") < renderer_source.index("syncRendererData(candleRef.current")
    assert "timeline: Array<{ time: number }>" in renderer_source
    assert "lowerBoundCandleTime(state.timeline" in renderer_source
    assert "tradeAutoscaleViewportRef.current !== viewportIdentity" in renderer_source
    assert "candleRef.current?.priceScale().applyOptions({ autoScale: true })" in renderer_source
    assert renderer_source.index('compactTradeLabel(annotation.exitLabelParts') < renderer_source.index('drawCanvasTradeGuide(context, guideSpan.left, guideSpan.right, y, stopColor')
    assert "trading?.strategy_chart_activity ?? trading?.strategy_activity" in chart_source
    assert "entryDecision?.row.chart_plan" in chart_source
    assert "guideStartTime: planStartTime" in chart_source
    assert "prior_snapshot_levels" in chart_source
    assert "combined_entry_boundary" in chart_source
    assert "levelPrices" in chart_source
    assert "targetPrices" in chart_source
    assert "lifecycleProtectionOrders" in chart_source
    assert 'orderType.includes("TRAIL")' in chart_source
    assert 'orderType.includes("STP")' in chart_source
    assert 'orderType.includes("LMT")' in chart_source
    assert 'side === "SHORT" ? Math.min(...brokerStops) : Math.max(...brokerStops)' in chart_source
    assert '"NO SL"' in chart_source
    assert '"NO TP"' in chart_source
    assert '"NO STRATEGY PLAN"' in chart_source
    assert 'label: `SL@${compactPrice(nextStop)}`' in chart_source
    assert 'label: `TP@${compactPrice(nextTarget)}`' in chart_source
    assert 'return `${name}${formatQuantity(quantity)}@${compactPrice(price)}`' in chart_source
    assert 'settings?.lineStyle === "dashed"' in renderer_source
    assert "execution_annotations: []" in chart_source
    assert "positionExecutionActions" in chart_source
    assert '"Long" : "Short"' not in chart_source
    assert '"Short" : "Long"' in chart_source
    assert "actions.slice(1, -1)" in chart_source
    assert 'profit_target: "TP"' in chart_source
    assert 'if (reason.includes("macd")) return "MACD exit"' in chart_source
    assert 'if (reason.includes("stop")) return "Stop exit"' in chart_source
    assert 'if (reason.includes("target") || fallbackKind === "profit_target") return "Target filled"' in chart_source
    assert 'String(row.exit_reason || "")' in chart_source
    assert '"Targets complete"' not in chart_source
    assert '"Trim"' not in chart_source
    assert 'execution_role' in chart_source
    assert '`${role}:${action.side}:${second}:${priceTick}`' in chart_source
    assert "const exitQuantity = exitAction?.quantity || quantity" in chart_source
    assert "current.time = Math.min(current.time, time)" in chart_source
    assert "closedTradeAnnotations" not in chart_source
    assert "class TradeAnnotationPrimitive implements ISeriesPrimitive<Time>" in renderer_source
    assert "candleSeries.attachPrimitive(tradeAnnotationPrimitive)" in renderer_source
    assert "tradeAnnotationPrimitiveRef.current?.setState" in renderer_source
    assert "tradeAnnotationPrimitiveRef.current?.setSettings(strategyPresentationSettings)" in renderer_source
    assert "settings: strategyPresentationSettingsRef.current" in renderer_source
    assert "drawTradeAnnotationPrimitiveGeometry" in renderer_source
    assert 'const defaultStrategyPresentationSettings: StrategyPresentationSettings' in renderer_source
    assert 'entryLine: strategyPresentationStyle("#3596FD", "solid", 2, 0.95' in renderer_source
    assert 'entryArrow: strategyPresentationStyle("", "solid", 2, 1, 10, 5, 1)' in renderer_source
    assert 'entryLabel: { ...strategyPresentationStyle("#64748B", "solid", 1, 1, 10, 7, 1), borderColor: "#007DFF"' in renderer_source
    assert 'entryDirectionPart:' in renderer_source
    assert 'entryShortDirectionPart:' in renderer_source
    assert 'entrySizePart:' in renderer_source
    assert 'entrySeparatorPart:' in renderer_source
    assert 'entryPricePart:' in renderer_source
    assert 'entryShortPricePart:' in renderer_source
    assert 'entryShortDirectionPart: { ...strategyPresentationStyle("#FF1744", "solid", 1, 1, 10, 7, 1)' in renderer_source
    assert 'entryPricePart: { ...strategyPresentationStyle("#FFFFFF", "solid", 1, 1, 10, 7, 1), fillColor: "#007DFF"' in renderer_source
    assert 'entryShortPricePart: { ...strategyPresentationStyle("#FFFFFF", "solid", 1, 1, 10, 7, 1), fillColor: "#FF1744"' in renderer_source
    assert 'title: "Long"' in renderer_source
    assert 'title: "Short"' in renderer_source
    assert 'title: "Long price"' in renderer_source
    assert 'title: "Short price"' in renderer_source
    assert 'exitLine: strategyPresentationStyle("#FF3D47"' in renderer_source
    assert 'exitLabel: { ...strategyPresentationStyle("#64748B", "solid", 1, 1, 10, 7, 0.45), borderColor: "#F75555"' in renderer_source
    assert 'exitReasonPart:' in renderer_source
    assert 'exitShortReasonPart:' in renderer_source
    assert 'exitSizePart:' in renderer_source
    assert 'exitSeparatorPart:' in renderer_source
    assert 'exitPricePart:' in renderer_source
    assert 'exitShortPricePart:' in renderer_source
    assert 'exitPnlPart:' in renderer_source
    assert 'exitPnlLossPart:' in renderer_source
    assert 'exitShortReasonPart: { ...strategyPresentationStyle("#007DFA", "solid", 1, 1, 10, 7, 0.18), fillBlur: 2, fillColor: "#FFFFFF"' in renderer_source
    assert 'exitPricePart: { ...strategyPresentationStyle("#FFFFFF", "solid", 1, 1, 10, 7, 1), fillColor: "#007DFF"' in renderer_source
    assert 'exitShortPricePart: { ...strategyPresentationStyle("#FFFFFF", "solid", 1, 1, 10, 7, 1), fillColor: "#FF1744"' in renderer_source
    assert 'exitPnlPart: { ...strategyPresentationStyle("#FFFFFF", "solid", 1, 1, 10, 7, 1), fillColor: "#00A846"' in renderer_source
    assert 'exitPnlLossPart: { ...strategyPresentationStyle("#FFFFFF", "solid", 1, 1, 10, 7, 1), fillColor: "#FF1744", fontWeight: 600, labelPaddingX: 5, labelPaddingY: 2 },' in renderer_source
    assert "legacyShortLabelStyleDefaults" in renderer_source
    assert "migrateUntouchedShortStyle ? undefined : configured" in renderer_source
    assert 'title: "Close long"' in renderer_source
    assert 'title: "Cover short"' in renderer_source
    assert 'title: "Profit"' in renderer_source
    assert 'title: "Loss"' in renderer_source
    assert 'levelLabel: { ...strategyPresentationStyle("", "solid", 1, 1, 8, 7, 1), borderWidth: 0, labelPaddingX: 2, labelPaddingY: 1 }' in renderer_source
    assert 'stopLine: strategyPresentationStyle("", "dashed", 1, 0.95' in renderer_source
    assert 'targetLine: strategyPresentationStyle("#008539", "dashed", 1, 1' in renderer_source
    assert 'stopLabel: { ...strategyPresentationStyle("", "dashed", 2, 1, 8, 7, 1), borderOpacity: 0.49, borderStyle: "solid", borderWidth: 0, labelPaddingX: 2, labelPaddingY: 2 }' in renderer_source
    assert 'targetLabel: { ...strategyPresentationStyle("", "dashed", 2, 1, 8, 7, 1), borderOpacity: 1, borderStyle: "solid", borderWidth: 0, labelPaddingX: 2, labelPaddingY: 1 }' in renderer_source
    assert 'adjustmentLabel: { ...strategyPresentationStyle("#8C6E96", "solid", 2, 1, 8, 7, 0.92), borderWidth: 0, labelPaddingX: 2, labelPaddingY: 1 }' in renderer_source
    assert 'connector: strategyPresentationStyle("", "dashed", 1, 0.7)' in renderer_source
    assert 'const strategyVisualElementDefinitions: StrategyVisualElementDefinition[]' in renderer_source
    assert 'className="strategy-presentation-element"' in renderer_source
    assert 'className="strategy-presentation-style-page"' in renderer_source
    assert 'className="strategy-presentation-style-page strategy-presentation-composite-label-page"' in renderer_source
    assert 'className="strategy-presentation-part-tabs"' in renderer_source
    assert 'className="strategy-presentation-label-preview"' in renderer_source
    definitions_source = renderer_source.split('const strategyVisualElementDefinitions', 1)[1].split('type StrategyCompositeLabelKey', 1)[0]
    assert 'entryDirectionPart' not in definitions_source
    assert 'exitReasonPart' not in definitions_source
    assert 'Customize ${definition.title} style' in renderer_source
    assert 'label="Text color"' in renderer_source
    assert 'label="Text opacity"' in renderer_source
    assert '<StrategyFontWeightSelect' in renderer_source
    assert 'aria-label="Text weight"' in renderer_source
    assert 'label="Fill color"' in renderer_source
    assert 'value && settings.fillOpacity === 0 ? { fillOpacity: 1 }' in renderer_source
    assert 'value && settings.borderOpacity === 0 ? { borderOpacity: 1 }' in renderer_source
    assert 'value && partSettings.fillOpacity === 0 ? { fillOpacity: 1 }' in renderer_source
    assert 'onInput={(event) => onChange(event.currentTarget.value)}' in renderer_source
    assert 'onBlur={(event) => { if (!value) onChange(event.currentTarget.value); }}' in renderer_source
    assert 'onFocus={() => { if (!value) onChange(displayedColor); }}' in renderer_source
    assert 'aria-label={`Apply ${displayedColor.toUpperCase()} to ${label}`}' in renderer_source
    assert 'label="Horizontal padding"' in renderer_source
    assert 'label="Vertical padding"' in renderer_source
    assert 'label="Edge color"' in renderer_source
    assert 'label="Edge opacity"' in renderer_source
    assert 'avoidLabelCollisions' in renderer_source
    assert 'connectorThreshold' in renderer_source
    assert 'settingsStorageKey}.strategy-presentation' in renderer_source
    assert '>Strategy Presentation</span>' in renderer_source
    assert 'drawCanvasTradeGuide(context, guideSpan.left, guideSpan.right, y, stopColor, "SL"' in renderer_source
    assert 'annotation.levelPrices?.slice(0, 3)' in renderer_source
    assert 'annotation.targetPrices?.forEach' in renderer_source
    assert "const exitLabelFallback = Number(annotation.pnl) > 0 ? successColor" in renderer_source
    assert '"--chart-strategy-entry": tokens.chartStrategyEntry' in theme_source
    assert '"--chart-strategy-stop": tokens.chartStrategyStop' in theme_source
    assert '"--chart-strategy-target": tokens.chartStrategyTarget' in theme_source
    assert 'fill.kind === "add"' in renderer_source
    assert 'fill.kind === "profit_target" || fill.kind === "target_change"' in renderer_source
    assert 'fill.kind === "protective_stop" || fill.kind === "trailing_stop" || fill.kind === "stop_change" || fill.kind === "protection_repair"' in renderer_source
    assert "const ratio = clampNumber((time - leftCandle.time) / duration" in renderer_source
    assert "return leftX + (rightX - leftX) * ratio" in renderer_source
    assert "The triangle tip is the exact event-time / execution-price coordinate" in renderer_source
    assert "const span = clippedTradeSpan(entryX, exitX, width)" in renderer_source
    assert "autoscaleInfo(startLogical: number, endLogical: number): AutoscaleInfo | null" in renderer_source
    assert "tradeAnnotationAutoscaleInfo(this.state, startLogical, endLogical)" in renderer_source
    assert "const minimumWidth = Math.min(56, width)" in renderer_source
    assert 'drawCanvasTradeLabel(context, label, (renderedLeft + renderedRight) / 2, y + 3' in renderer_source
    assert "canvasLabelBoxesOverlap" in renderer_source
    assert "drawCanvasCandleConnector" in renderer_source
    assert "context.setLineDash(canvasLineDash(settings.lineStyle, settings.lineWidth))" in renderer_source
    assert 'context.fillRect(renderedLeft - capRadius' in renderer_source
    assert 'entryLabelParts: [' in chart_source
    assert '{ text: formatQuantity(entryQuantity), tone: "size" }' in chart_source
    assert '{ text: "@", tone: "separator" }' in chart_source
    assert '{ text: entryPrice.toFixed(2), tone: side === "SHORT" ? "priceShort" : "priceLong" }' in chart_source
    assert 'exitLabelParts: status === "closed" ? [' in chart_source
    assert '{ text: exitLabel, tone: side === "SHORT" ? "exitShort" : "exitLong" }' in chart_source
    assert '{ text: formatQuantity(exitQuantity), tone: "size" }' in chart_source
    assert '{ text: Number(exitPrice).toFixed(2), tone: side === "SHORT" ? "exitPriceShort" : "exitPriceLong" }' in chart_source
    assert '{ text: signedMoneyShort(pnl), tone: pnl >= 0 ? "pnlWin" : "pnlLoss" }' in chart_source
    assert 'const entryLabelPartSettings: TradeLabelPartSettings' in renderer_source
    assert 'const exitLabelPartSettings: TradeLabelPartSettings' in renderer_source
    assert 'pnlLoss: elements.exitPnlLossPart' in renderer_source
    assert 'annotation.exitLabelParts, exitLabelPartSettings' in renderer_source
    assert 'parts.map((part)' in renderer_source
    assert 'partSettings?.[part.tone ?? "label"]' in renderer_source
    assert 'context.font = `${segment.settings.fontWeight} ${segment.settings.labelSize}px ${canvasInterfaceFont()}`' in renderer_source
    assert 'const fillColor = strategyPresentationColor(segment.settings.fillColor, background)' in renderer_source
    assert 'if (segment.settings.fillBlur > 0) context.filter = `blur(${segment.settings.fillBlur}px)`' in renderer_source
    assert 'const borderColor = strategyPresentationColor(settings.borderColor, color)' in renderer_source
    assert 'context.strokeStyle = rgbaFromHex(borderColor, settings.borderOpacity)' in renderer_source
    assert 'context.fillText(segment.text, segmentLeft + segment.settings.labelPaddingX' in renderer_source
    presentation_effect = renderer_source.split("// Presentation edits own only primitive paint state.", 1)[1].split("}, [strategyPresentationSettings]);", 1)[0]
    assert "setSettings(strategyPresentationSettings)" in presentation_effect
    assert "drawCurrentRegions" not in presentation_effect
    assert "fitTradeAnnotationPriceScale" not in presentation_effect
    autoscale_source = renderer_source.split("function tradeAnnotationAutoscaleInfo", 1)[1].split("function strategyPresentationColor", 1)[0]
    assert "state.settings" not in autoscale_source
    assert "strategyElementFamilyVisible" not in renderer_source
    strategy_styles = styles_source.split(".strategy-presentation-menu", 1)[1].split(".app-loading-state", 1)[0]
    assert "font-family: var(--font-body);" in strategy_styles
    assert "font-weight: 600;" in strategy_styles
    assert "font-weight: 500;" in strategy_styles
    assert "font-weight: 650;" not in strategy_styles
    assert "font-weight: 700;" not in strategy_styles
    assert renderer_source.index('drawCanvasTradeLine(context, span.left, span.right, entryY') < renderer_source.index('annotation.levelPrices?.slice(0, 3)')
    assert renderer_source.index('drawCanvasTradeArrow(context, entryX, entryY') < renderer_source.index('annotation.levelPrices?.slice(0, 3)')
    assert "const horizontalCandidates = [preferredLeft, preferredLeft - labelWidth / 2, preferredLeft + labelWidth / 2]" in renderer_source
    assert "const verticalOffsets = Array.from({ length: 33 }" in renderer_source
    assert "const candidateTop = top + offset" in renderer_source
    label_source = renderer_source.split("function drawCanvasTradeLabel", 1)[1].split("function canvasLabelBoxesOverlap", 1)[0]
    assert "clampNumber(candidateLeft" not in label_source
    assert "clampNumber(top + offset" not in label_source
    assert "box.right <= 0 || box.left >= width || box.bottom <= 0 || box.top >= height" in label_source
    assert 'const exitX = resolvedEndX' in renderer_source
    assert 'annotation.status === "open" ? width : resolvedEndX' not in renderer_source
    annotation_time_source = renderer_source.split("function xForAnnotationTime", 1)[1].split("function drawReferenceLine", 1)[0]
    assert "time < candles[0].time || time >= candles[candles.length - 1].time + candleDuration" in annotation_time_source
    assert "nearestCandleIndex" not in annotation_time_source
    assert "layout?.boxes.push(box)" in renderer_source
    assert "anchor - 3" in renderer_source
    draw_source = renderer_source.split("function drawRegions", 1)[1].split("function drawSessionRegions", 1)[0]
    assert "drawTradeAnnotations(" not in draw_source
    assert "drawExecutionAnnotations(" not in draw_source
    assert "drawSessionRegionPrimitiveGeometry(" in renderer_source
    assert 'zOrder: () => "bottom"' in renderer_source
    assert "context.fillStyle = sessionRegionColor(region, settings)" in renderer_source
    assert "clearOverlayLayer(layer)" in draw_source
    assert "drawSessionRegions(chart, layer" not in draw_source


def test_strategy_activity_loads_a_reviewable_history_window() -> None:
    container_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "MarketScreenerContainers.tsx").read_text(encoding="utf-8")
    configuration_source = (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "configuration.ts").read_text(encoding="utf-8")
    canvas_source = CANVAS_PAGE.read_text(encoding="utf-8")

    assert "Math.max(2_000, Math.min(settings.limit, 50_000))" in container_source
    assert "timeoutMs: runId ? 30000 : 10000" in container_source
    assert "!controller.signal.aborted && !runId" in container_source
    assert "runId ? 1_000 : 5_000" not in container_source
    assert "let historicalRetryAvailable = Boolean(runId);" in container_source
    assert "historicalRetryAvailable = false;" in container_source
    assert 'include_decision_evidence: "false"' in container_source
    assert 'record_id: exactRecordId' in container_source
    assert 'className="strategy-activity-inspect"' in container_source
    assert 'aria-label="Strategy event details"' in container_source
    assert "strategyEvidenceSections(snapshot)" in container_source
    assert 'aria-label="Resize event evidence"' in container_source
    assert "Inspect strategy event at" in container_source
    assert 'className="strategy-activity-evidence-cards"' in container_source
    assert "JSON.stringify(snapshot" not in container_source
    assert 'strategy_activity: { eventType: "", limit: 2_000' in configuration_source
    assert "if (historicalRows !== undefined) return;" in container_source
    assert "historicalRows={signalStreamRunId ? preview?.trading.strategy_activity ?? [] : undefined}" in canvas_source
    assert "historicalPage={signalStreamRunId ? preview?.trading.strategy_activity_page : undefined}" in canvas_source
    assert 'offset: String(Number(resolvedHistoricalPage.next_offset ?? 0))' in container_source
    assert 'Load next 2,000 older events' in container_source
    assert 'moreAvailable={!resolvedHistoricalPage.complete}' in container_source
    assert 'Load and open next page' in container_source
    assert "strategyActivityEvidenceSnapshot(row)" in container_source
    assert "recorded_event: eventEvidence" in container_source
    assert "limit: 50_000" not in canvas_source.split("function strategyReplayRegistry", 1)[1].split("function normalizeInheritedLayouts", 1)[0]


def test_market_time_filters_interpret_wall_clock_inputs_in_exchange_time() -> None:
    source = TABLE_FILTERS.read_text(encoding="utf-8")
    container_source = SCREENER_CONTAINERS.read_text(encoding="utf-8")

    assert 'import { dateInTimeZone } from "../timeZones"' in source
    assert "parseFilterDate(condition.value, timeZone)" in source
    assert "dateInTimeZone(date, time, timeZone).getTime()" in source
    assert 'column.timeZone === "America/New_York" ? " (ET)"' in source
    assert 'column?.timeZone === "America/New_York" ? " ET"' in source
    assert 'timeZone: temporal ? "America/New_York" : undefined' in container_source
    assert "tableTimeFiltersRequireOlderRows" in source
    assert "oldestLoaded > requiredFloor" in source
    assert "timeFilterNeedsOlderRows" in container_source
    assert "void onRequestMore();" in container_source
    assert "Loading older rows for ET filter" in container_source


def test_completed_strategy_review_opens_chart_performance_and_audit_surfaces() -> None:
    source = CANVAS_PAGE.read_text(encoding="utf-8")
    replay_layout = source.split("const STRATEGY_REPLAY_CONTAINER_IDS", 1)[1].split("function normalizeInheritedLayouts", 1)[0]

    assert ': WorkspaceContainerId[] = ["performance_journal", "strategy_activity", "positions", "orders", "fills"]' in replay_layout
    assert "if (state) return state;" in replay_layout
    assert "availableWorkspaceWidth() - margin * 2" in replay_layout
    assert "y: margin + index * (height + gap)" in replay_layout
    assert 'timeframe: "1s"' in replay_layout
    assert '"indicator.macd"' in replay_layout
    assert '"strategy.presentation"' not in replay_layout
    assert 'strategyPresentationEnabled={strategyPresentationAvailable}' in (REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "chartPresentation.tsx").read_text(encoding="utf-8")
    assert "run.tickers?.[0]" in replay_layout
    assert "registry.linkAssignments.charts_quotes" in replay_layout
    assert "[chartLinkGroup]: { ...registry.linkContexts[chartLinkGroup], symbol: ticker }" in replay_layout
    assert 'chart: { ...chartsQuotesSettings.chart, ...(ticker ? { symbol: ticker } : {}), timeframe: "1s"' in replay_layout
    assert "openIds.push(requiredKind)" not in replay_layout
    strategy_container_ids = replay_layout.split("= [", 1)[1].split("];", 1)[0]
    for expensive_detail in ('"charts_quotes"', '"closed_trades"', '"portfolio"', '"signal_stream"', '"watchlist"'):
        assert expensive_detail not in strategy_container_ids


def test_historical_workspace_layout_is_stable_across_runs_and_revisions() -> None:
    source = CANVAS_PAGE.read_text(encoding="utf-8")

    assert '`${runtimeMode}.${replayRun.execution_mode || "manual"}.${runtimeWorkspaceId || canvasId}`' in source
    assert 'durableHistoricalWorkspace ? "persistent-layout-v1" : runtimeRevision' in source
    runtime_scope = source.split("const durableHistoricalWorkspace", 1)[1].split("const [overlayEpoch", 1)[0]
    assert "replayRun.run_id" not in runtime_scope


def test_strategy_activity_row_selects_and_highlights_the_evidence_card() -> None:
    source = SCREENER_CONTAINERS.read_text(encoding="utf-8")

    assert 'onRowSelect={(row) => setSelectedRecordId(strategyActivityRowKey(row))}' in source
    assert "rowIdentity={strategyActivityRowKey}" in source
    assert 'data-selectable={selectable ? "true" : undefined}' in source
    assert 'aria-selected={selectable ? selected : undefined}' in source
    assert 'event.key !== "Enter" && event.key !== " "' in source


def test_backtest_workspaces_use_the_bounded_compact_viewport() -> None:
    routes_source = (REPO_ROOT / "frontend" / "src" / "app" / "routes.ts").read_text(encoding="utf-8")
    styles_source = (REPO_ROOT / "frontend" / "src" / "app" / "styles.css").read_text(encoding="utf-8")

    assert 'page === "backtest-trading"' in routes_source
    assert 'page === "backtest-debug"' in routes_source
    assert '.trading-workspace-shell[data-command-bar-visible="false"]:has(> .workspace-registry-warning)' in styles_source
    assert "grid-template-rows: auto minmax(0, 1fr);" in styles_source


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
    assert "probabilityLineRatio: holdProbability" in chart_source
    assert 'zone.annotationKind === "unified-structure-level"' in renderer_source
    assert "span.left + span.width * probability" in renderer_source
    projected_history = history_api_source.split("fn project_chart_snapshot", 1)[1].split("fn compact_projected_unified_structure_history", 1)[0]
    assert "snapshot.indicator_projection.take()" in projected_history
    assert "compact_projected_unified_structure_history(&mut projected)" in projected_history
    assert "fn unified_structure_projection" in history_cache_source
    assert 'object.insert("sources".to_string(), json!([]))' in history_cache_source
    assert "CacheProfile::Structure" in history_cache_source
    assert "matches!(profile, CacheProfile::Structure(_)).then_some(1)" in history_cache_source
    assert "execution VWAP is defined from" in history_cache_source
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
    assert "minimumHoldQualityScore" in renderer_source
    assert "minimumHoldObservations" in renderer_source
    assert "minimumHoldEvidenceReliability" in renderer_source
    assert "minimumPressureMagnitude" in renderer_source
    assert "minimumTickerRelativeQualityScore" in renderer_source
    assert "maximumBreakProbability" in renderer_source
    assert "minimumSalience" not in renderer_source
    assert "minimumReactionProbability" not in renderer_source
    assert "minimumReversalProbability" not in renderer_source
    assert "Evidence-quality filters" in renderer_source
    assert 'label="hold_quality_score"' in renderer_source
    assert 'label="ticker_relative_quality_score"' in renderer_source
    assert 'label="hold_observation_count"' in renderer_source
    assert 'label="hold_evidence_reliability"' in renderer_source
    assert 'label="break_probability maximum"' in renderer_source
    assert 'label="|pressure_bias|"' in renderer_source
    assert 'label="Minimum hold"' not in renderer_source
    assert 'label="Hold probability"' not in renderer_source
    assert "Changes apply immediately to loaded chart data" in renderer_source
    assert "showUnifiedSupport" in renderer_source
    assert "showUnifiedResistance" in renderer_source
    assert "showUnifiedActive" in renderer_source
    assert "showUnifiedBroken" in renderer_source
    assert "showUnifiedRoleFlipped" in renderer_source
    assert 'zone.latest ? settings.showUnifiedActive : settings.showUnifiedBroken' in renderer_source
    assert "legendSettingsRef.current" in renderer_source
    assert "const holdProbability = boundedUnit(level.hold_probability)" in chart_source
    assert "holdEvidenceReliability: holdReliability" in chart_source
    assert "holdObservationCount: holdObservations" in chart_source
    assert "tickerRelativeQualityScore: tickerRelativeQuality ?? undefined" in chart_source
    assert 'zone.tickerRelativeQualityStatus !== "available"' in renderer_source
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


def test_chart_memo_signature_includes_position_and_protection_evidence() -> None:
    source = CANVAS_PAGE.read_text(encoding="utf-8")

    signature_source = source.split("function tradingPositionSignature", 1)[1].split("function strategyDecisionEvents", 1)[0]
    assert "trading?.position_lifecycles" in signature_source
    assert "trading?.executions" in signature_source
    assert "trading?.orders" in signature_source
    assert "trading?.strategy_chart_activity ?? trading?.strategy_activity" in signature_source
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


def test_chart_crosshair_normalizes_pointer_coordinates_under_application_zoom() -> None:
    renderer_source = (REPO_ROOT / "frontend" / "src" / "app" / "components" / "ChartPanel.tsx").read_text(encoding="utf-8")

    assert "attachZoomNormalizedCrosshairInput(priceRef.current)" in renderer_source
    assert "bounds.width / eventTarget.offsetWidth" in renderer_source
    assert "bounds.height / eventTarget.offsetHeight" in renderer_source
    assert "bounds.left + (event.clientX - bounds.left) / scaleX" in renderer_source
    assert "bounds.top + (event.clientY - bounds.top) / scaleY" in renderer_source
    assert 'new MouseEvent("mousemove"' in renderer_source
