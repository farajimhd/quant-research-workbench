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
        await choose_paper_mode(page)
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


async def choose_paper_mode(page: Any) -> None:
    candidates = [
        page.get_by_label("Paper"),
        page.get_by_text("Paper", exact=False),
        page.get_by_label("Paper Trading"),
        page.get_by_text("Paper Trading", exact=False),
    ]
    for locator in candidates:
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=1_500)
                return
        except Exception:  # noqa: BLE001
            continue
    await page.evaluate(
        """
        () => {
          const labels = Array.from(document.querySelectorAll('label'));
          const label = labels.find((item) => /paper/i.test(item.innerText || item.textContent || ''));
          if (label) {
            const id = label.getAttribute('for');
            const input = id ? document.getElementById(id) : label.querySelector('input');
            if (input) { input.click(); return; }
            label.click(); return;
          }
          const radios = Array.from(document.querySelectorAll('input[type=radio]'));
          const paperRadio = radios.find((radio) => /paper/i.test((radio.value || '') + ' ' + (radio.name || '') + ' ' + (radio.id || '')));
          if (paperRadio) { paperRadio.click(); }
        }
        """
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
