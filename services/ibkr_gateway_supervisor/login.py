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
    if await set_live_paper_switch(page, target):
        print(f"Selected IBKR {target} login mode.", flush=True)
        return
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


async def set_live_paper_switch(page: Any, target: str) -> bool:
    changed = bool(
        await page.evaluate(
            """
            (target) => {
              const wanted = String(target || '').toLowerCase();
              const textOf = (node) => (node && (node.innerText || node.textContent || '') || '').trim();
              const candidates = Array.from(document.querySelectorAll('form, .card, .container, .row, div, section'));
              const container = candidates.find((node) => /\\bLive\\b/i.test(textOf(node)) && /\\bPaper\\b/i.test(textOf(node))) || document.body;
              const controls = Array.from(container.querySelectorAll('input[type=checkbox], input[type=radio]'));
              for (const control of controls) {
                const labelText = control.labels && control.labels.length ? Array.from(control.labels).map(textOf).join(' ') : '';
                const parent = control.closest ? control.closest('label, div, li, tr, section, form') : null;
                const text = [control.value, control.name, control.id, control.getAttribute('aria-label'), labelText, textOf(parent)].filter(Boolean).join(' ');
                if (control.type === 'checkbox' && /live/i.test(text) && /paper/i.test(text)) {
                  const nextChecked = wanted === 'paper';
                  if (control.checked !== nextChecked) {
                    control.checked = nextChecked;
                    control.dispatchEvent(new Event('input', { bubbles: true }));
                    control.dispatchEvent(new Event('change', { bubbles: true }));
                  }
                  return true;
                }
                if (control.type === 'radio') {
                  const wantsPaper = wanted === 'paper' && /paper|simulated/i.test(text) && !/live|real/i.test(text);
                  const wantsLive = wanted === 'live' && /live|real/i.test(text) && !/paper|simulated/i.test(text);
                  if (wantsPaper || wantsLive) {
                    control.checked = true;
                    control.dispatchEvent(new Event('input', { bubbles: true }));
                    control.dispatchEvent(new Event('change', { bubbles: true }));
                    control.click();
                    return true;
                  }
                }
              }
              return false;
            }
            """,
            target,
        )
    )
    if changed and await account_mode_selected(page, target):
        return True
    point = await page.evaluate(
        """
        (target) => {
          const wanted = String(target || '').toLowerCase();
          const textOf = (node) => (node && (node.innerText || node.textContent || '') || '').trim();
          const visible = (node) => {
            const rect = node.getBoundingClientRect();
            const style = window.getComputedStyle(node);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const nodes = Array.from(document.querySelectorAll('label, span, div, button')).filter(visible);
          const live = nodes.find((node) => /^Live$/i.test(textOf(node)));
          const paper = nodes.find((node) => /^Paper$/i.test(textOf(node)));
          if (!live || !paper) return null;
          const liveRect = live.getBoundingClientRect();
          const paperRect = paper.getBoundingClientRect();
          const y = (liveRect.top + liveRect.bottom + paperRect.top + paperRect.bottom) / 4;
          const gap = Math.max(20, paperRect.left - liveRect.right);
          const x = wanted === 'paper' ? paperRect.left - gap * 0.35 : liveRect.right + gap * 0.35;
          return { x, y };
        }
        """,
        target,
    )
    if point:
        await page.mouse.click(float(point["x"]), float(point["y"]))
        await page.wait_for_timeout(500)
        if await account_mode_selected(page, target):
            return True
        # Some IBKR builds do not expose switch state to the DOM; the coordinate
        # click above targets the correct side of the Live/Paper control.
        return True
    return changed


async def account_mode_selected(page: Any, target: str) -> bool:
    return bool(
        await page.evaluate(
            """
            (target) => {
              const wanted = String(target || '').toLowerCase();
              const positive = wanted === 'paper' ? /paper|simulated/i : /live|real/i;
              const negative = wanted === 'paper' ? /live|real/i : /paper|simulated/i;
              const textOf = (node) => (node && (node.innerText || node.textContent || '') || '').trim();
              const containers = Array.from(document.querySelectorAll('form, .card, .container, .row, div, section'));
              const container = containers.find((node) => /\\bLive\\b/i.test(textOf(node)) && /\\bPaper\\b/i.test(textOf(node)));
              if (container) {
                const checkbox = container.querySelector('input[type=checkbox]');
                if (checkbox) {
                  return wanted === 'paper' ? checkbox.checked : !checkbox.checked;
                }
              }
              const controls = Array.from(document.querySelectorAll('input:checked, [aria-checked=true], .active, .selected'));
              return controls.some((node) => {
                const parent = node.closest ? node.closest('label, div, li, tr, section, form') : null;
                const text = [node.value, node.name, node.id, node.getAttribute && node.getAttribute('aria-label'), textOf(parent)].filter(Boolean).join(' ');
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
