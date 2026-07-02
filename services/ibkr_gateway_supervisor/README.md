# IBKR Gateway Supervisor

Supervises a local IBKR Client Portal Gateway session for the paper account first.

The service:

- starts `bin/run.bat root/conf.yaml` when the gateway is not reachable
- checks `/iserver/auth/status`
- calls `/iserver/auth/ssodh/init` when the existing session can be reopened
- runs the Playwright login helper automatically when fresh login is required
- calls `/tickle` on a fixed cadence
- alerts when automatic login repeatedly fails
- provides an explicit Playwright login helper for troubleshooting

It does not bypass IBKR authentication. If IBKR changes the login page or requires
additional verification, the login helper fails visibly and the supervisor alerts.

## Required env

```text
IBKR_CLIENT_LIBRARY_LOCAL_PATH="D:\IBKR Web API\v1\clientportal.gw"
IBKR_PAPER_ACCOUNT_ID=...
IBKR_PAPER_USER_NAME=...
IBKR_PAPER_PASSWORD=...
```

Optional:

```text
IBKR_CPAPI_BASE_URL=https://localhost:5000/v1/api
IBKR_GATEWAY_AUTO_LOGIN=true
IBKR_GATEWAY_MAX_LOGIN_ATTEMPTS=3
IBKR_GATEWAY_LOGIN_RETRY_SECONDS=60
IBKR_GATEWAY_TICKLE_SECONDS=60
IBKR_GATEWAY_STATUS_SECONDS=15
IBKR_GATEWAY_MAX_AUTH_FAILURES=3
IBKR_GATEWAY_ALERT_EMAIL_TO=you@example.com
IBKR_GATEWAY_ALERT_EMAIL_FROM=alerts@example.com
IBKR_GATEWAY_ALERT_SMTP_HOST=smtp.example.com
IBKR_GATEWAY_ALERT_SMTP_PORT=587
IBKR_GATEWAY_ALERT_SMTP_USER=alerts@example.com
IBKR_GATEWAY_ALERT_SMTP_PASSWORD=...
```

## Commands

Check once:

```powershell
python -m services.ibkr_gateway_supervisor.main --account paper --check-only
```

Run daemon. This starts the gateway, logs in when unauthenticated, and keeps the
session alive:

```powershell
python -m services.ibkr_gateway_supervisor.main --account paper
```

Explicit one-off login helper for troubleshooting:

```powershell
python -m services.ibkr_gateway_supervisor.main --account paper --login-once
```

Playwright is needed for daemon auto-login and `--login-once`:

```powershell
pip install playwright
python -m playwright install chromium
```
