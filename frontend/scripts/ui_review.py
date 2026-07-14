"""Capture deterministic frontend UX review matrices with Playwright.

Use the current Python when Playwright is installed. Otherwise re-execute
through the Conda environment named by UI_REVIEW_CONDA_ENV (default: ml4t).
This launcher never installs packages or browser binaries.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable


THEMES = (
    "light", "slate", "parchment", "dawn", "harbor",
    "dark", "forest", "graphite", "ember", "amethyst",
)
SCALES = (0.8, 0.9, 1.0, 1.1, 1.25)
PAGES = (
    "real-live-trading",
    "replay-trading",
    "backtest-trading",
    "canvas-configuration",
    "canvas-focus",
    "services-dashboard",
    "service-qmd",
    "service-qmd-history",
    "service-news",
    "service-sec",
    "service-text-embed",
    "service-reference",
    "service-ibkr",
)
VIEWPORTS = {
    "normal": {"width": 1600, "height": 1000},
    "compact": {"width": 1280, "height": 720},
}
REPRESENTATIVE_PAGES = ("real-live-trading", "replay-trading", "backtest-trading", "canvas-configuration", "canvas-focus", "services-dashboard")
REPRESENTATIVE_THEMES = ("light", "dark")
TARGETED_SCALES = (0.8, 1.0, 1.25)


def ensure_playwright() -> None:
    try:
        import playwright.sync_api  # noqa: F401
        return
    except ImportError:
        pass

    if os.environ.get("UI_REVIEW_CONDA_REEXEC") == "1":
        raise SystemExit(
            "Playwright is unavailable in both the original Python and the "
            "configured Conda environment."
        )
    conda = shutil.which("conda")
    if not conda:
        raise SystemExit(
            "Playwright is not installed in this Python and 'conda' was not found. "
            "Set UI_REVIEW_CONDA_ENV or use a Playwright-enabled Python."
        )

    environment = os.environ.get("UI_REVIEW_CONDA_ENV", "ml4t")
    child_env = os.environ.copy()
    child_env["UI_REVIEW_CONDA_REEXEC"] = "1"
    command = [
        conda, "run", "-n", environment, "python",
        str(Path(__file__).resolve()), *sys.argv[1:],
    ]
    raise SystemExit(subprocess.run(command, env=child_env, check=False).returncode)


def parse_viewport(value: str) -> tuple[str, dict[str, int]]:
    try:
        name, dimensions = value.split(":", 1)
        width, height = dimensions.lower().split("x", 1)
        viewport = {"width": int(width), "height": int(height)}
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "viewport must use NAME:WIDTHxHEIGHT, for example compact:1280x720"
        ) from exc
    if not name or viewport["width"] < 320 or viewport["height"] < 240:
        raise argparse.ArgumentTypeError("viewport name and usable dimensions are required")
    return name, viewport


def cartesian(
    pages: Iterable[str],
    themes: Iterable[str],
    scales: Iterable[float],
    viewports: dict[str, dict[str, int]],
) -> Iterable[dict[str, Any]]:
    for page in pages:
        for theme in themes:
            for scale in scales:
                for viewport_name, viewport in viewports.items():
                    yield {
                        "page": page,
                        "theme": theme,
                        "scale": scale,
                        "viewport_name": viewport_name,
                        "viewport": viewport,
                    }


def unique_scenarios(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (
            item["page"], item["theme"], item["scale"], item["viewport_name"],
            item["viewport"]["width"], item["viewport"]["height"],
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def build_scenarios(args: argparse.Namespace) -> list[dict[str, Any]]:
    viewports = dict(args.viewport or VIEWPORTS.items())
    requested_pages = tuple(args.page or ())
    requested_themes = tuple(args.theme or ())
    requested_scales = tuple(args.scale or ())

    if args.matrix == "exhaustive":
        return list(cartesian(
            requested_pages or PAGES,
            requested_themes or THEMES,
            requested_scales or SCALES,
            viewports,
        ))

    if args.mode == "targeted":
        return list(cartesian(
            requested_pages or ("real-live-trading",),
            requested_themes or REPRESENTATIVE_THEMES,
            requested_scales or TARGETED_SCALES,
            viewports,
        ))

    pages = requested_pages or PAGES
    themes = requested_themes or THEMES
    scales = requested_scales or SCALES
    baseline_theme = "light" if "light" in themes else themes[0]
    baseline_scale = 1.0 if 1.0 in scales else scales[0]
    representative_pages = tuple(page for page in REPRESENTATIVE_PAGES if page in pages)
    if not representative_pages:
        representative_pages = (pages[0],)
    representative_themes = tuple(
        theme for theme in REPRESENTATIVE_THEMES if theme in themes
    ) or (themes[0],)

    scenarios: list[dict[str, Any]] = []
    scenarios.extend(cartesian(pages, (baseline_theme,), (baseline_scale,), viewports))
    scenarios.extend(cartesian(
        representative_pages, themes, (baseline_scale,), viewports,
    ))
    scenarios.extend(cartesian(
        representative_pages, representative_themes, scales, viewports,
    ))
    return unique_scenarios(scenarios)


def slug_scale(scale: float) -> str:
    return str(scale).replace(".", "p")


def validate_canvas_interactions(
    page: Any,
    scenario: dict[str, Any],
    interaction_screenshot: Path | None = None,
) -> list[str]:
    """Exercise the Canvas behaviors that static screenshots cannot prove."""
    issues: list[str] = []
    if scenario["page"] == "canvas-focus":
        if page.locator(".sidebar").count():
            issues.append("focus canvas renders the application sidebar")
        chart = page.get_by_role("region", name="Chart", exact=True)
        if chart.count() != 1:
            issues.append("focus canvas does not render exactly one Chart container")
        else:
            bounds = chart.bounding_box()
            minimum_height = scenario["viewport"]["height"] - 92
            if not bounds or bounds["height"] < minimum_height:
                actual = round(bounds["height"]) if bounds else 0
                issues.append(
                    f"focus container does not fill the working page ({actual} < {minimum_height})"
                )
        return issues

    if not (
        scenario["page"] == "canvas-configuration"
        and scenario["theme"] == "light"
        and scenario["scale"] == 1.0
        and scenario["viewport_name"] == "normal"
    ):
        return issues

    chart = page.get_by_role("region", name="Chart", exact=True)
    if chart.count() != 1:
        return ["main canvas does not render exactly one Chart container"]
    try:
        title_bar = chart.locator(".workspace-window-header")
        link_button = title_bar.get_by_role("button", name="Link Chart")
        if link_button.count() != 1:
            issues.append("Chart link action is not in the container title bar")
        if "Blue" not in link_button.inner_text():
            issues.append("Chart does not expose its current link color at the point of use")
        scanner = page.get_by_role("region", name="Scanner", exact=True)
        portfolio = page.get_by_role("region", name="Portfolio", exact=True)
        news = page.get_by_role("region", name="News", exact=True)
        chart_tint = title_bar.evaluate("element => getComputedStyle(element).backgroundColor")
        scanner_tint = scanner.locator(".workspace-window-header").evaluate("element => getComputedStyle(element).backgroundColor")
        portfolio_tint = portfolio.locator(".workspace-window-header").evaluate("element => getComputedStyle(element).backgroundColor")
        if chart.get_attribute("data-linked") != "true":
            issues.append("single-symbol Chart does not expose its linked state")
        if chart_tint != scanner_tint or chart_tint != portfolio_tint:
            issues.append("link color leaks from the link control into the whole title bar")
        if scanner.get_attribute("data-linked") != "false" or scanner.get_by_role("button", name="Link Scanner").count():
            issues.append("multi-symbol Scanner incorrectly exposes linking")
        if news.get_attribute("data-linked") != "false" or news.get_by_role("button", name="Link News").count():
            issues.append("generic News incorrectly exposes linking")
        initial_link_border = link_button.evaluate("element => getComputedStyle(element).borderColor")
        link_button.click()
        if chart.get_by_label("Chart link configuration").count() != 1:
            issues.append("Chart link popover is not contained inside the Chart container")
        if page.locator(".canvas-config-drawer").count():
            issues.append("container configuration created a page-level drawer")
        if "Same color = linked" not in chart.get_by_label("Chart link configuration").inner_text():
            issues.append("Chart configuration does not explain the color-link model")
        color_picker = chart.get_by_label("Chart link color")
        if color_picker.locator(".canvas-link-color-choice").count() != 7:
            issues.append("Chart link picker does not expose exactly seven colors")
        link_configuration_text = chart.get_by_label("Chart link configuration").inner_text()
        if "Rows" in link_configuration_text:
            issues.append("Chart link popover contains unrelated row configuration")
        linked_list = chart.get_by_label("Chart linked containers")
        if "Chart" not in linked_list.inner_text() or "AAPL" not in linked_list.inner_text():
            issues.append("Chart link popover does not list the colored container and current ticker")
        if "Scanner" in linked_list.inner_text():
            issues.append("Chart link membership incorrectly includes multi-symbol Scanner")
        if interaction_screenshot:
            page.screenshot(path=str(interaction_screenshot), full_page=True)
        color_picker.get_by_role("button", name="Assign Chart to Violet").click()
        page.wait_for_timeout(100)
        violet_link_border = link_button.evaluate("element => getComputedStyle(element).borderColor")
        if violet_link_border == initial_link_border:
            issues.append("changing the Chart link color did not change its link-control accent")
        if title_bar.evaluate("element => getComputedStyle(element).backgroundColor") != chart_tint:
            issues.append("changing link color changed the whole Chart title bar")
        link_button.click()
        if "Violet" not in chart.get_by_role("button", name="Link Chart").inner_text():
            issues.append("changing a container link color did not update its title-bar state")
        link_button.click()
        chart.get_by_role("button", name="Unlink Chart").click()
        if chart.get_attribute("data-linked") != "false":
            issues.append("unlinking Chart did not remove its linked title-bar state")
        chart.get_by_role("button", name="Assign Chart to Violet").click()
        link_button.click()

        scanner.get_by_role("button", name="Configure Scanner").click()
        if scanner.get_by_label("Scanner settings").count() != 1 or "Rows" not in scanner.get_by_label("Scanner settings").inner_text():
            issues.append("Scanner row configuration is not separated into its internal settings popover")
        scanner.get_by_role("button", name="Configure Scanner").click()

        minimize = chart.get_by_role("button", name="Minimize Chart")
        if minimize.locator(".lucide-minus").count() != 1:
            issues.append("minimize action does not use the dedicated minus icon")
        minimize.click()
        if chart.get_by_role("button", name="Restore Chart").count() != 1:
            issues.append("Chart did not enter the minimized state")
        elif chart.get_by_role("button", name="Restore Chart").locator(".lucide-panel-top-open").count() != 1:
            issues.append("restore action does not use a distinct restore icon")
        chart.get_by_role("button", name="Restore Chart").click()
        chart.get_by_role("button", name="Fullscreen Chart").click()
        if chart.get_by_role("button", name="Exit fullscreen Chart").count() != 1:
            issues.append("Chart did not enter the maximized state")
        elif chart.get_by_role("button", name="Exit fullscreen Chart").locator(".lucide-minimize-2").count() != 1:
            issues.append("fullscreen exit does not use the inward-arrow icon")
        if chart.get_by_role("button", name="Minimize Chart").locator(".lucide-minus").count() != 1:
            issues.append("fullscreen and title-bar minimize actions are visually ambiguous")
        chart.get_by_role("button", name="Exit fullscreen Chart").click()
        chart.get_by_role("button", name="Reset Chart to its default layout").click()

        with page.expect_popup(timeout=5000) as blank_canvas_popup_info:
            page.get_by_role("button", name="New canvas", exact=True).click()
        blank_canvas_popup = blank_canvas_popup_info.value
        blank_canvas_popup.locator(".app-shell").wait_for(state="visible", timeout=5000)
        blank_canvas_popup.locator(".workspace-window").first.wait_for(state="visible", timeout=5000)
        if "#canvas-focus" not in blank_canvas_popup.url or blank_canvas_popup.locator(".sidebar").count():
            issues.append("new managed canvas did not open in a chromeless canvas page")
        if blank_canvas_popup.locator(".workspace-window").count() < 1:
            issues.append("new managed canvas opened without inheriting any containers")
        blank_canvas_popup.close()

        with page.expect_popup(timeout=5000) as popup_info:
            chart.get_by_role("button", name="Open linked Chart in a new canvas").click()
        popup = popup_info.value
        popup.locator(".app-shell").wait_for(state="visible", timeout=5000)
        if "#canvas-focus" not in popup.url or popup.locator(".sidebar").count():
            issues.append("linked container did not open in a chromeless focus canvas")
        if popup.get_by_role("region", name="Chart", exact=True).count() != 1:
            issues.append("linked focus canvas does not contain the source Chart")
        popup.close()
        if page.locator(".canvas-manager-items article").count() < 3:
            issues.append("main Canvas manager did not register managed and linked canvases")
        if page.locator(".canvas-manager-open").count() < 2:
            issues.append("registered canvases do not expose their names as open actions")
    except Exception as exc:
        issues.append(f"Canvas interaction check failed: {exc}")
    return issues


def capture(args: argparse.Namespace) -> int:
    from playwright.sync_api import sync_playwright

    scenarios = build_scenarios(args)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default_output = Path(tempfile.gettempdir()) / "quant-research-workbench-ui-review" / timestamp
    output_dir = Path(args.output_dir or default_output)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    capture_failures = 0
    objective_issues = 0
    base_url = args.url.rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        try:
            for index, scenario in enumerate(scenarios, start=1):
                context = browser.new_context(viewport=scenario["viewport"])
                scale_value = str(scenario["scale"]).rstrip("0").rstrip(".")
                context.add_init_script(
                    "localStorage.setItem('quant-research-workbench.theme', "
                    + json.dumps(scenario["theme"])
                    + "); localStorage.setItem('quant-research-workbench.ui-scale', "
                    + json.dumps(scale_value)
                    + ");"
                )
                if scenario["page"] == "canvas-focus":
                    focus_id = args.canvas_id or "review-focus"
                    focus_layout = {
                        "chart": {
                            "fullscreen": True,
                            "h": max(320, round(scenario["viewport"]["height"] / scenario["scale"]) - 62),
                            "minimized": False,
                            "w": max(680, round(scenario["viewport"]["width"] / scenario["scale"])),
                            "x": 0,
                            "y": 0,
                            "z": 1,
                        },
                    }
                    focus_state = {"layoutVersion": 3, "layouts": focus_layout, "openIds": ["chart"]}
                    focus_registry = {
                        "version": 1,
                        "canvases": [{"id": "main", "label": "Main"}, {"id": focus_id, "label": "Chart focus"}],
                        "linkAssignments": {"chart": "A"},
                        "linkContexts": {
                            "A": {"symbol": "AAPL", "timeframe": "1m"},
                            "B": {"symbol": "MSFT", "timeframe": "1m"},
                            "C": {"symbol": "NVDA", "timeframe": "5m"},
                        },
                    }
                    context.add_init_script(
                        "localStorage.setItem('quant-research-workbench.canvas.registry.v1', "
                        + json.dumps(json.dumps(focus_registry))
                        + "); localStorage.setItem("
                        + json.dumps(f"quant-research-workbench.trading-workspace.canvas.{focus_id}.v1")
                        + ", " + json.dumps(json.dumps(focus_state)) + ");"
                    )
                if args.seed_core_containers and args.canvas_id and scenario["page"] == "real-live-trading":
                    viewport_width = scenario["viewport"]["width"]
                    viewport_height = scenario["viewport"]["height"]
                    width = max(1180, viewport_width - 112)
                    height = max(780, viewport_height - 86)
                    content_top = 108
                    content_height = max(560, height - content_top - 12)
                    left_width = min(round(width * 0.44), max(480, round(width * 0.38)))
                    top_height = min(210, max(180, content_height - 290))
                    layouts = {
                        "portfolio": {"fullscreen": False, "h": top_height, "minimized": False, "w": left_width, "x": 12, "y": content_top, "z": 1},
                        "scanner": {"fullscreen": False, "h": max(280, content_height - top_height - 10), "minimized": False, "w": left_width, "x": 12, "y": content_top + top_height + 10, "z": 2},
                        "chart": {"fullscreen": False, "h": content_height, "minimized": False, "w": max(520, width - left_width - 34), "x": left_width + 22, "y": content_top, "z": 3},
                    }
                    storage_prefix = "quant-research-workbench.real-live-trading.layout"
                    storage_payload = {"chartWindows": [], "layoutVersion": 4, "layouts": layouts, "windows": ["portfolio", "scanner"]}
                    context.add_init_script(
                        "localStorage.setItem(" + json.dumps(f"{storage_prefix}.{args.canvas_id}") + ", " + json.dumps(json.dumps(storage_payload)) + ");"
                    )
                page = context.new_page()
                console_errors: list[str] = []
                page_errors: list[str] = []
                failed_requests: list[str] = []
                page.on(
                    "console",
                    lambda message: console_errors.append(message.text)
                    if message.type == "error" else None,
                )
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on(
                    "requestfailed",
                    lambda request: failed_requests.append(
                        f"{request.method} {request.url}: {request.failure}"
                    ),
                )

                filename = (
                    f"{scenario['page']}__{scenario['theme']}"
                    f"__s{slug_scale(scenario['scale'])}"
                    f"__{scenario['viewport_name']}.png"
                )
                screenshot_path = output_dir / filename
                canvas_query = ""
                if args.canvas_id and scenario["page"] == "real-live-trading":
                    canvas_query = f"?liveCanvas={args.canvas_id}"
                elif scenario["page"] == "canvas-focus":
                    canvas_query = f"?canvas={args.canvas_id or 'review-focus'}"
                elif args.seed_core_containers and scenario["page"] == "replay-trading":
                    canvas_query = "?historicalWorkspace=replay"
                elif args.seed_core_containers and scenario["page"] == "backtest-trading":
                    canvas_query = "?historicalWorkspace=backtest"
                result = {**scenario, "url": f"{base_url}/{canvas_query}#{scenario['page']}"}
                try:
                    page.goto(
                        result["url"], wait_until="domcontentloaded",
                        timeout=args.timeout_ms,
                    )
                    page.locator(".app-shell").wait_for(
                        state="visible", timeout=args.timeout_ms,
                    )
                    page.wait_for_timeout(args.settle_ms)
                    metrics = page.evaluate("""() => {
                        const root = document.documentElement;
                        const shell = document.querySelector('.app-shell');
                        const shellStyle = shell ? getComputedStyle(shell) : null;
                        return {
                            title: document.title,
                            bodyTextLength: (document.body.innerText || '').trim().length,
                            appShellPresent: Boolean(shell),
                            documentWidth: root.scrollWidth,
                            viewportWidth: root.clientWidth,
                            horizontalOverflow: root.scrollWidth > root.clientWidth + 1,
                            overflowingElements: Array.from(document.querySelectorAll('body *'))
                                .map((element) => {
                                    const rect = element.getBoundingClientRect();
                                    return { className: element.className || element.tagName, right: Math.round(rect.right), width: Math.round(rect.width) };
                                })
                                .filter((entry) => entry.right > root.clientWidth + 1)
                                .sort((a, b) => b.right - a.right)
                                .slice(0, 8),
                            scrollOverflowElements: Array.from(document.querySelectorAll('body *'))
                                .map((element) => ({ className: element.className || element.tagName, clientWidth: element.clientWidth, scrollWidth: element.scrollWidth }))
                                .filter((entry) => entry.scrollWidth > entry.clientWidth + 1)
                                .sort((a, b) => b.scrollWidth - a.scrollWidth)
                                .slice(0, 8),
                            resolvedTheme: [
                                'light', 'slate', 'parchment', 'dawn', 'harbor',
                                'dark', 'forest', 'graphite', 'ember', 'amethyst',
                            ].find((theme) => root.classList.contains(theme)) || null,
                            resolvedScale: shellStyle
                                ? shellStyle.getPropertyValue('--app-zoom').trim()
                                : null,
                            canvasGeometry: ['.focus-app-main', '.canvas-focus-page', '.trading-workspace-shell', '.trading-workspace-canvas', '.workspace-window[data-window-kind="chart"]']
                                .map((selector) => {
                                    const element = document.querySelector(selector);
                                    const rect = element?.getBoundingClientRect();
                                    const style = element ? getComputedStyle(element) : null;
                                    return {
                                        selector,
                                        height: rect ? Math.round(rect.height) : null,
                                        top: rect ? Math.round(rect.top) : null,
                                        computedHeight: style?.height || null,
                                        inlineStyle: element?.getAttribute('style') || null,
                                    };
                                }),
                        };
                    }""")
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    issues: list[str] = []
                    if not metrics["appShellPresent"]:
                        issues.append("app shell is missing")
                    if metrics["bodyTextLength"] < 20:
                        issues.append("rendered body is unexpectedly empty")
                    if metrics["resolvedTheme"] != scenario["theme"]:
                        issues.append(
                            f"theme resolved as {metrics['resolvedTheme']!r}, "
                            f"expected {scenario['theme']!r}"
                        )
                    expected_scale = float(scenario["scale"])
                    try:
                        resolved_scale = float(metrics["resolvedScale"])
                    except (TypeError, ValueError):
                        resolved_scale = None
                    if resolved_scale is None or abs(resolved_scale - expected_scale) > 0.001:
                        issues.append(
                            f"scale resolved as {metrics['resolvedScale']!r}, "
                            f"expected {expected_scale}"
                        )
                    if metrics["horizontalOverflow"]:
                        issues.append(
                            f"document overflows horizontally ({metrics['documentWidth']} > "
                            f"{metrics['viewportWidth']})"
                        )
                    interaction_screenshot = screenshot_path.with_name(
                        f"{screenshot_path.stem}__link-config.png"
                    ) if (
                        scenario["page"] == "canvas-configuration"
                        and scenario["theme"] == "light"
                        and scenario["scale"] == 1.0
                        and scenario["viewport_name"] == "normal"
                    ) else None
                    issues.extend(validate_canvas_interactions(
                        page, scenario, interaction_screenshot,
                    ))
                    objective_issues += len(issues)
                    result.update({
                        "status": "captured",
                        "screenshot": str(screenshot_path),
                        "interaction_screenshot": str(interaction_screenshot) if interaction_screenshot else None,
                        "metrics": metrics,
                        "issues": issues,
                    })
                except Exception as exc:
                    capture_failures += 1
                    result.update({
                        "status": "capture_failed", "error": str(exc), "issues": [],
                    })
                finally:
                    result["console_errors"] = console_errors
                    result["page_errors"] = page_errors
                    result["failed_requests"] = failed_requests
                    results.append(result)
                    context.close()
                print(f"[{index}/{len(scenarios)}] {result['status']}: {filename}", flush=True)
        finally:
            browser.close()

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "matrix": args.matrix,
        "base_url": base_url,
        "scenario_count": len(scenarios),
        "capture_failures": capture_failures,
        "objective_issue_count": objective_issues,
        "results": results,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Review evidence: {output_dir}")
    print(f"Manifest: {manifest_path}")
    print(
        f"Captured {len(scenarios) - capture_failures}/{len(scenarios)} scenarios; "
        f"objective issues: {objective_issues}."
    )
    return 1 if capture_failures or (args.strict and objective_issues) else 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Capture route, theme, scale, and viewport evidence for UX review."
    )
    result.add_argument("--url", default="http://127.0.0.1:5173")
    result.add_argument("--canvas-id", help="open trading routes directly in the named child canvas")
    result.add_argument("--seed-core-containers", action="store_true", help="seed portfolio and scanner containers for child-canvas review")
    result.add_argument("--mode", choices=("targeted", "full"), default="targeted")
    result.add_argument("--matrix", choices=("bounded", "exhaustive"), default="bounded")
    result.add_argument("--page", action="append", choices=PAGES)
    result.add_argument("--theme", action="append", choices=THEMES)
    result.add_argument("--scale", action="append", type=float, choices=SCALES)
    result.add_argument(
        "--viewport", action="append", type=parse_viewport,
        metavar="NAME:WIDTHxHEIGHT",
    )
    result.add_argument("--output-dir")
    result.add_argument("--settle-ms", type=int, default=1500)
    result.add_argument("--timeout-ms", type=int, default=15000)
    result.add_argument("--headed", action="store_true")
    result.add_argument(
        "--strict", action="store_true",
        help="return non-zero for objective layout, theme, scale, or blank-page issues",
    )
    return result


def main() -> int:
    ensure_playwright()
    return capture(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
