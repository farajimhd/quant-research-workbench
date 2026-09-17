"""Deterministic browser exercises; fixtures never contact or mutate trading services."""
import copy
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse


def install_labeler_fixture(context):
    state = {"reviews": {}, "requests": [], "fail_next": False}
    base = int(datetime(2026, 9, 15, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
    durations = {"100ms": 100, "1s": 1000, "5s": 5000, "10s": 10000, "30s": 30000, "1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000}
    def route(request):
        path = urlparse(request.request.url).path
        params = {k: v[0] for k, v in parse_qs(urlparse(request.request.url).query).items()}
        ticker = params.get("ticker", "AAA")
        scope = {"session_date": params.get("session_date", "2026-09-15"), "ticker": ticker, "session": params.get("session", "regular"), "timeframe": params.get("timeframe", "1h"), "label_set": "default"}
        def fulfill(body, status=200):
            request.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        if path.endswith("/universe"):
            fulfill({"start": "2026-09-15T13:30:00.000Z", "end": "2026-09-15T20:00:00.000Z", "rows": [{"ticker": symbol, "float_shares": 2400000 if symbol == "AAA" else None, "float_quality": "reported", "change_pct": 8.2, "volume": 180000,
                                "review_status": state["reviews"].get(symbol, {}).get("status", "unreviewed"), "range_count": len(state["reviews"].get(symbol, {}).get("ranges", []))} for symbol in ("AAA", "BBB", "CCC")]})
        elif path.endswith("/chart"):
            state["requests"].append((ticker, scope["timeframe"]))
            step = durations[scope["timeframe"]]
            candles = [{"start_ms": base + i * step, "end_ms": base + (i + 1) * step, "open": 10 + i * .003, "high": 10.1 + i * .003, "low": 9.9 + i * .003, "close": 10.02 + i * .003, "volume": 1000 + i} for i in range(6 if step == 3600000 else 200)]
            fulfill({"candles": candles, "evidence_id": ticker + scope["timeframe"], "source_token": "fixture", "next_window": None, "window_count": 1})
        elif path.endswith("/review") and request.request.method == "PUT":
            body = request.request.post_data_json
            if state["fail_next"]:
                state["fail_next"] = False
                fulfill({"detail": "Deliberate fixture save failure"}, 503)
                return
            record = {**body, "revision": body["expected_revision"] + 1, "source_token": "fixture"}
            state["reviews"][body["scope"]["ticker"]] = record
            fulfill(record)
        elif path.endswith("/review"):
            fulfill(state["reviews"].get(ticker, {"scope": scope, "ranges": [], "revision": 0, "status": "unreviewed", "evidence_ids": []}))
        elif path.endswith("/export"):
            fulfill({"reviews": list(state["reviews"].values())})
        else:
            request.fallback()
    context.route("**/api/research/labeler/**", route)
    context.route("**/api/trading/canvas-context", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"session_date": "2026-09-15", "preview_time": "09:45"})))
    return state


def review_labeler(page, state, screenshot_path):
    page.locator('[data-window-kind="labeler"]').wait_for(state="visible")
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    assert state["requests"][0] == ("AAA", "1h"), state["requests"]
    page.get_by_role("button", name="No opportunity & next", exact=True).click()
    page.wait_for_function("document.querySelector('.labeler-status strong')?.textContent === 'BBB'")
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    assert state["reviews"]["AAA"]["status"] == "no_opportunity"
    assert state["requests"][-1] == ("BBB", "1h")
    page.get_by_label("View timeframe", exact=True).select_option("100ms")
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    page.wait_for_timeout(350)
    layer = page.locator(".labeler-chart-overlay")
    def mark(direction, left, right):
        page.get_by_role("button", name=f"{direction} range", exact=True).click()
        layer.scroll_into_view_if_needed()
        box = layer.bounding_box()
        y = max(box["y"] + 35, min(box["y"] + box["height"] * .2, page.viewport_size["height"] - 35))
        page.mouse.click(box["x"] + box["width"] * left, y)
        page.mouse.click(box["x"] + box["width"] * right, y)
        before = len(state["reviews"].get("BBB", {}).get("ranges", []))
        page.get_by_role("button", name="Close Labeler", exact=True).click()
        assert page.locator('.labeler-page').is_visible()
        assert len(state["reviews"].get("BBB", {}).get("ranges", [])) == before
        page.get_by_role("button", name="Submit range", exact=True).click()
        page.wait_for_function("document.querySelector('.labeler-status')?.textContent.includes('Saved · revision')")
    mark("Long", .18, .35)
    assert len(state["reviews"]["BBB"]["ranges"]) == 1
    page.get_by_role("button", name="Short range", exact=True).wait_for(state="visible")
    mark("Short", .55, .72)
    page.wait_for_function("document.querySelectorAll('.labeler-range-list button').length === 2")
    page.wait_for_timeout(150)
    saved = copy.deepcopy(state["reviews"]["BBB"]["ranges"])
    assert {r["direction"] for r in saved} == {"LONG", "SHORT"}
    assert all(r["annotation_timeframe"] == "100ms" for r in saved)
    page.get_by_role("button", name="Undo", exact=True).click()
    page.wait_for_function("document.querySelectorAll('.labeler-range-list button').length === 1 && document.querySelector('.labeler-status').textContent.includes('Saved · revision')")
    page.get_by_role("button", name="Redo", exact=True).click()
    page.wait_for_function("document.querySelectorAll('.labeler-range-list button').length === 2 && document.querySelector('.labeler-status').textContent.includes('Saved · revision')")
    page.get_by_label("View timeframe", exact=True).select_option("1s")
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    assert state["reviews"]["BBB"]["ranges"] == saved
    page.get_by_label("View timeframe", exact=True).select_option("100ms")
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    page.locator(".labeler-range-list button").first.click()
    state["fail_next"] = True
    page.get_by_role("button", name="Delete", exact=True).click()
    page.get_by_text("Save failed — draft retained", exact=True).wait_for()
    assert page.get_by_label("Session date", exact=True).is_disabled()
    page.get_by_role("button", name="Retry save", exact=True).click()
    page.wait_for_function("document.querySelector('.labeler-status').textContent.includes('Saved · revision')")
    page.get_by_role("button", name="Undo", exact=True).click()
    page.wait_for_function("document.querySelectorAll('.labeler-range-list button').length === 2 && document.querySelector('.labeler-status').textContent.includes('Saved · revision')")
    before = state["reviews"]["BBB"]["ranges"][0]["entry_timestamp"]
    handle = page.get_by_role("button", name="Drag long entry", exact=True)
    handle.scroll_into_view_if_needed()
    box = handle.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 40)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + 25, box["y"] + 40, steps=5)
    page.mouse.up()
    page.wait_for_function("document.querySelector('.labeler-status').textContent.includes('Saved · revision 7')")
    assert state["reviews"]["BBB"]["ranges"][0]["entry_timestamp"] != before
    page.get_by_role("button", name="Complete & next", exact=True).click()
    page.wait_for_function("document.querySelector('.labeler-status strong')?.textContent === 'CCC'")
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    assert state["requests"][-1] == ("CCC", "1h")
    page.get_by_role("button", name="BBB", exact=True).click()
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    page.get_by_label("View timeframe", exact=True).select_option("100ms")
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    assert state["reviews"]["BBB"]["status"] == "completed"
    final_ranges = copy.deepcopy(state["reviews"]["BBB"]["ranges"])
    page.reload()
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    page.get_by_role("button", name="BBB", exact=True).click()
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    assert state["requests"][-1] == ("BBB", "1h")
    assert state["reviews"]["BBB"]["ranges"] == final_ranges
    page.get_by_label("View timeframe", exact=True).select_option("100ms")
    page.locator(".labeler-status").get_by_text("Full session certified", exact=True).wait_for()
    page.wait_for_timeout(200)
    page.screenshot(path=str(screenshot_path), full_page=True)
