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
    "services-dashboard",
    "service-qmd",
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
REPRESENTATIVE_PAGES = ("real-live-trading", "services-dashboard")
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
                result = {**scenario, "url": f"{base_url}/#{scenario['page']}"}
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
                            resolvedTheme: [
                                'light', 'slate', 'parchment', 'dawn', 'harbor',
                                'dark', 'forest', 'graphite', 'ember', 'amethyst',
                            ].find((theme) => root.classList.contains(theme)) || null,
                            resolvedScale: shellStyle
                                ? shellStyle.getPropertyValue('--app-zoom').trim()
                                : null,
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
                    objective_issues += len(issues)
                    result.update({
                        "status": "captured",
                        "screenshot": str(screenshot_path),
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
