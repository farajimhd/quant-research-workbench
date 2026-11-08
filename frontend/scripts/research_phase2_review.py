"""Bounded real-data acceptance for the phase-two Research route and chart."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Two representative captures after a focused UI correction",
    )
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if not output.is_relative_to(Path("D:/TradingML/runtimes").resolve()):
        raise ValueError("Review evidence must remain under the runtime root")
    output.mkdir(parents=True, exist_ok=True)
    errors, captures, results = [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport=dict(width=1600, height=1050))
            page.on("pageerror", lambda error: errors.append(str(error)))
            url = "http://127.0.0.1:5173/?research=phase2-shards#research-workspace"
            page.goto(url)
            page.get_by_role(
                "heading", name="BarGPT phase 2 data", exact=True
            ).wait_for()
            page.screenshot(path=str(output / "initial-empty.png"))
            page.get_by_label("Phase 2 ticker search", exact=True).fill("EVAX")
            page.get_by_label("Phase 2 year", exact=True).select_option("2025")
            key = "54cab2fdca816a10b60e6df9"
            selector = page.get_by_label("Phase 2 ticker / year", exact=True)
            selector.locator(f'option[value="{key}"]').wait_for(
                state="attached", timeout=60000
            )
            selector.select_option(key)
            page.get_by_label("Phase 2 saved unit", exact=True).locator(
                'option[value="0"]'
            ).wait_for(state="attached", timeout=60000)
            page.get_by_label("Phase 2 saved unit", exact=True).select_option("0")

            def response_for(action):
                with page.expect_response(
                    lambda response: "/bargpt/phase2/sample?" in response.url,
                    timeout=120000,
                ) as captured:
                    action()
                assert captured.value.ok, captured.value.text()
                result = captured.value.json()
                page.wait_for_function(
                    "() => document.querySelector('.phase2-shard-research')?.getAttribute('aria-busy') === 'false'"
                )
                return result

            result = response_for(
                lambda: page.get_by_role(
                    "button", name="Load block", exact=True
                ).click()
            )
            for frame in range(5):
                if frame:
                    result = response_for(
                        lambda: page.get_by_label(
                            "Phase 2 timeframe", exact=True
                        ).select_option(str(frame))
                    )
                assert len(result["bars"]) == 64
                assert (
                    len(result["channels"]) == 18 and len(result["time_channels"]) == 9
                )
                assert all(
                    bar["end_us"] <= result["origin_us"] for bar in result["bars"]
                )
                assert (
                    page.locator(".phase2-shard-research svg g[role=button]").count()
                    == 64
                )
                page.get_by_role("button", name="Inspect bar 2", exact=True).click()
                page.get_by_role("heading", name="Bar 2 / 64", exact=True).wait_for()
                assert (
                    page.locator(
                        ".phase2-shard-research .bar-shard-fields tbody tr"
                    ).count()
                    == 27
                )
                results.append(
                    dict(
                        frame=frame,
                        origin=result["origin_us"],
                        last_bar=result["bars"][-1]["end_us"],
                    )
                )
            # Earlier overlapping views and decision origins must retain exact causality.
            result = response_for(
                lambda: page.get_by_label("64-bar view", exact=True).select_option("0")
            )
            assert len(result["bars"]) == 64
            result = response_for(
                lambda: page.get_by_label("Decision origin", exact=True).select_option(
                    "128"
                )
            )
            assert result["decision"] == 128
            page.get_by_label("Phase 2 block", exact=True).fill("1")
            result = response_for(
                lambda: page.get_by_role(
                    "button", name="Go to block", exact=True
                ).click()
            )
            assert result["block"] == 1 and result["decision"] == 128
            assert (
                "Shaded slots"
                not in page.locator(
                    ".phase2-shard-research .bar-shard-legend"
                ).inner_text()
            )
            page.get_by_label("Chart basis", exact=True).select_option("raw")
            page.get_by_label("Chart basis", exact=True).select_option("adjusted")
            page.get_by_label("Phase 2 price series", exact=True).select_option("bid")
            page.get_by_label("Phase 2 price series", exact=True).select_option("all")
            page.get_by_text("Corporate actions and provenance", exact=True).click()
            page.screenshot(path=str(output / "provenance.png"), full_page=True)
            for theme in ("light", "dark"):
                for scale in (0.8, 1.0, 1.25):
                    for viewport, width, height in (
                        ("normal", 1600, 1050),
                        ("compact", 1000, 760),
                    ):
                        if args.quick and (theme, scale, viewport) not in (
                            ("light", 1.0, "normal"),
                            ("dark", 1.25, "compact"),
                        ):
                            continue
                        page.evaluate(
                            "([theme,scale])=>{localStorage.setItem('quant-research-workbench.theme',theme);localStorage.setItem('quant-research-workbench.ui-scale',String(scale));}",
                            [theme, scale],
                        )
                        page.set_viewport_size(dict(width=width, height=height))
                        page.reload()
                        page.get_by_role(
                            "heading", name="BarGPT phase 2 data", exact=True
                        ).wait_for()
                        page.get_by_label("Phase 2 ticker search", exact=True).fill(
                            "EVAX"
                        )
                        page.get_by_label("Phase 2 year", exact=True).select_option(
                            "2025"
                        )
                        page.get_by_label("Phase 2 ticker / year", exact=True).locator(
                            f'option[value="{key}"]'
                        ).wait_for(state="attached", timeout=60000)
                        page.get_by_label(
                            "Phase 2 ticker / year", exact=True
                        ).select_option(key)
                        page.get_by_label("Phase 2 saved unit", exact=True).locator(
                            'option[value="0"]'
                        ).wait_for(state="attached", timeout=60000)
                        page.get_by_label(
                            "Phase 2 saved unit", exact=True
                        ).select_option("0")
                        response_for(
                            lambda: page.get_by_role(
                                "button", name="Load block", exact=True
                            ).click()
                        )
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth + 2"
                        )
                        page.screenshot(
                            path=str(output / f"{theme}-{scale}-{viewport}.png"),
                            full_page=True,
                        )
                        captures.append(
                            dict(theme=theme, scale=scale, viewport=viewport)
                        )
            # Exercise bounded random discovery through actual published data.
            result = response_for(
                lambda: page.get_by_role(
                    "button", name="Random block", exact=True
                ).click()
            )
            assert result["ticker"] == "EVAX"
            assert not errors, errors
            report = dict(
                passed=True, timeframes=results, captures=captures, errors=errors
            )
            (output / "report.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(dict(passed=True, frames=5, captures=len(captures))))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
