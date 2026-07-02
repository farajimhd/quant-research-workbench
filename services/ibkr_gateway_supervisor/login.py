from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from services.ibkr_gateway_supervisor.client import IbkrClientPortalClient, account_ids, is_authenticated
from services.ibkr_gateway_supervisor.config import IbkrGatewayConfig


async def run_playwright_login(config: IbkrGatewayConfig) -> bool:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Install it with `pip install playwright` and run `python -m playwright install chromium`.") from exc

    password = os.environ.get("IBKR_PAPER_PASSWORD" if config.account_key == "paper" else f"IBKR_{config.account_key.upper().replace('-', '_')}_PASSWORD", "")
    if not config.username or not password:
        raise RuntimeError(f"Missing username/password env vars for IBKR {config.account_key} login.")

    client = IbkrClientPortalClient(base_url=config.base_url, timeout_seconds=config.request_timeout_seconds)
    status = client.auth_status()
    if status.ok and is_authenticated(status.payload):
        print("IBKR Client Portal is already authenticated.", flush=True)
        return verify_account(client, config)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=config.login_headless)
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        await page.goto(config.login_url, wait_until="domcontentloaded", timeout=int(config.login_timeout_seconds * 1000))
        await fill_login_form(page, config.username, password)
        await choose_account_mode(page, config.account_key)
        await submit_login(page)
        print("Submitted IBKR login form. Waiting for authenticated Client Portal session.", flush=True)
        deadline = time.monotonic() + config.login_timeout_seconds
        authenticated = False
        while time.monotonic() < deadline:
            status = client.auth_status()
            if status.ok and is_authenticated(status.payload):
                authenticated = True
                break
            await asyncio.sleep(2.0)
        if not authenticated:
            title = ""
            try:
                title = await page.title()
            except PlaywrightTimeoutError:
                title = ""
            await browser.close()
            raise RuntimeError(f"IBKR login did not authenticate before timeout. Last page title={title!r}")
        await browser.close()
    return verify_account(client, config)


async def fill_login_form(page: Any, username: str, password: str) -> None:
    username_locator = await first_visible(
        page,
        [
            "input[name='username']",
            "input[name='user_name']",
            "input[name='user']",
            "input[id*='user' i]",
            "input[type='text']",
            "input:not([type])",
        ],
    )
    password_locator = await first_visible(
        page,
        [
            "input[name='password']",
            "input[id*='password' i]",
            "input[type='password']",
        ],
    )
    await username_locator.fill(username)
    await password_locator.fill(password)


async def choose_account_mode(page: Any, account_key: str) -> None:
    target = "paper" if account_key == "paper" else "live"
    labels = ["Paper", "Paper Trading", "Simulated Trading"] if target == "paper" else ["Live", "Live Trading", "Real"]
    for text in labels:
        locators = [
            page.get_by_label(text, exact=False),
            page.get_by_role("radio", name=text, exact=False),
            page.get_by_role("checkbox", name=text, exact=False),
            page.get_by_text(text, exact=False),
        ]
        for locator in locators:
            try:
                if await locator.count() > 0:
                    await locator.first.click(timeout=1_500)
                    if await account_mode_selected(page, target):
                        print(f"Selected IBKR {target} login mode.", flush=True)
                        return
            except Exception:  # noqa: BLE001
                continue
    selected = await page.evaluate(
        """
        (target) => {
          const wanted = String(target || '').toLowerCase();
          const negative = wanted === 'paper' ? /live|real/i : /paper|simulated/i;
          const positive = wanted === 'paper' ? /paper|simulated/i : /live|real/i;
          const visibleText = (node) => {
            if (!node) return '';
            const label = node.labels && node.labels.length ? Array.from(node.labels).map((item) => item.innerText || item.textContent || '').join(' ') : '';
            const parent = node.closest ? node.closest('label, div, li, tr, section, form') : null;
            return [node.value, node.name, node.id, node.getAttribute && node.getAttribute('aria-label'), label, parent && (parent.innerText || parent.textContent)].filter(Boolean).join(' ');
          };
          const controls = Array.from(document.querySelectorAll('input[type=radio], input[type=checkbox], button, [role=radio], [role=switch], [role=checkbox]'));
          for (const control of controls) {
            const text = visibleText(control);
            if (positive.test(text) && !negative.test(text)) {
              control.click();
              return true;
            }
          }
          const labels = Array.from(document.querySelectorAll('label, button, div, span'));
          for (const label of labels) {
            const text = label.innerText || label.textContent || '';
            if (positive.test(text) && !negative.test(text)) {
              label.click();
              return true;
            }
          }
          return false;
        }
        """,
        target,
    )
    if selected:
        print(f"Selected IBKR {target} login mode.", flush=True)
        return
    raise RuntimeError(f"Could not select IBKR {target} login mode.")


async def account_mode_selected(page: Any, target: str) -> bool:
    return bool(
        await page.evaluate(
            """
            (target) => {
              const wanted = String(target || '').toLowerCase();
              const positive = wanted === 'paper' ? /paper|simulated/i : /live|real/i;
              const negative = wanted === 'paper' ? /live|real/i : /paper|simulated/i;
              const controls = Array.from(document.querySelectorAll('input:checked, [aria-checked=true], .active, .selected'));
              return controls.some((node) => {
                const parent = node.closest ? node.closest('label, div, li, tr, section, form') : null;
                const text = [node.value, node.name, node.id, node.getAttribute && node.getAttribute('aria-label'), parent && (parent.innerText || parent.textContent)].filter(Boolean).join(' ');
                return positive.test(text) && !negative.test(text);
              });
            }
            """,
            target,
        )
    )


async def submit_login(page: Any) -> None:
    candidates = [
        page.get_by_role("button", name="Login"),
        page.get_by_role("button", name="Log In"),
        page.get_by_role("button", name="Submit"),
        page.locator("button[type='submit']"),
        page.locator("input[type='submit']"),
    ]
    for locator in candidates:
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=3_000)
                return
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("Could not find IBKR login submit button.")


async def first_visible(page: Any, selectors: list[str]) -> Any:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=5_000)
            return locator
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("Could not find required IBKR login field.")


def verify_account(client: IbkrClientPortalClient, config: IbkrGatewayConfig) -> bool:
    if not config.account_id:
        print("IBKR account id is not configured; auth succeeded but account access was not verified.", flush=True)
        return True
    result = client.accounts()
    ids = account_ids(result.payload)
    if config.account_id not in ids:
        raise RuntimeError(f"Authenticated IBKR session did not return configured account id. available={mask_ids(ids)}")
    print(f"IBKR {config.account_key} account verified.", flush=True)
    return True


def mask_ids(ids: list[str]) -> list[str]:
    return [("*" * max(0, len(item) - 4)) + item[-4:] for item in ids]
