from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TRADING_PRESENTATION = (
    REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "tradingPresentation.tsx"
)


def test_trading_journal_charts_preserve_text_and_candle_proportions() -> None:
    source = TRADING_PRESENTATION.read_text(encoding="utf-8")
    area_chart = source.split("function JournalAreaChart", 1)[1].split(
        "function JournalPnlCandleChart", 1
    )[0]
    candle_chart = source.split("function JournalPnlCandleChart", 1)[1].split(
        "function useResponsiveSvgWidth", 1
    )[0]

    assert "useResponsiveSvgWidth<SVGSVGElement>" in area_chart
    assert "useResponsiveSvgWidth<SVGSVGElement>" in candle_chart
    assert 'preserveAspectRatio="none"' not in area_chart
    assert 'preserveAspectRatio="none"' not in candle_chart
    assert 'preserveAspectRatio="xMinYMin meet"' in area_chart
    assert 'preserveAspectRatio="xMinYMin meet"' in candle_chart
    assert 'viewBox={`0 0 ${width} 154`}' in area_chart
    assert 'viewBox={`0 0 ${width} 232`}' in candle_chart

