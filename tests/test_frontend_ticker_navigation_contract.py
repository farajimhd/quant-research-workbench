from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = REPO_ROOT / "frontend" / "src"


def source(relative_path: str) -> str:
    return (FRONTEND / relative_path).read_text(encoding="utf-8")


def test_shared_table_rows_are_not_navigation_controls() -> None:
    table_sources = [
        source("app/components/DataTable.tsx"),
        source("app/components/MarketScreenerContainers.tsx"),
        source("features/canvas/tradingPresentation.tsx"),
    ]

    for table_source in table_sources:
        assert "onRowClick" not in table_source
        assert "data-selectable" not in table_source
        assert "Open ${ticker} Charts & Quotes" not in table_source


def test_ticker_identity_is_the_explicit_charts_quotes_control() -> None:
    identity_source = source("app/components/TablePresentation.tsx")
    navigation_source = source("app/tickerNavigation.ts")
    canvas_source = source("pages/CanvasConfigurationPage.tsx")
    styles_source = source("app/styles.css")

    assert "ticker-charts-quotes-link" in identity_source
    assert "Open ${symbol} Charts & Quotes in a new tab" in identity_source
    assert 'window.open(url, "_blank")' in navigation_source
    assert "writeCanvasFocusHandoff" in navigation_source
    assert "writeReplayCanvasFocusHandoff" in navigation_source
    assert 'replayRun={run} runtimeWorkspaceId={focusToken} transient' in canvas_source
    assert 'replayRun?.execution_mode === "strategy" && !requestedInstanceId && !transient' in canvas_source
    assert "text-decoration: underline" not in styles_source[styles_source.index(".ticker-charts-quotes-link"):styles_source.index(".ticker-charts-quotes-link:focus-visible")]


def test_service_tables_use_explicit_cell_controls_instead_of_interactive_rows() -> None:
    service_tables = [
        source("features/services/NewsHistoryTables.tsx"),
        source("features/services/NewsTodayRowsPanel.tsx"),
        source("features/services/SecTodayRowsPanel.tsx"),
        source("features/services/ServiceActivityPanel.tsx"),
        source("features/services/ServiceDatabaseTableState.tsx"),
        source("features/services/ServiceErrorLogPanel.tsx"),
    ]

    for table_source in service_tables:
        assert "<tr" in table_source
        assert "tabIndex={0}" not in table_source
        assert "role=\"button\"" not in table_source
    assert any("ticker-charts-quotes-link" in table_source for table_source in service_tables)
    assert all("table-primary-link" in table_source for table_source in service_tables)
