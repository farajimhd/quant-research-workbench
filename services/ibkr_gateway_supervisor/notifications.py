from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from services.ibkr_gateway_supervisor.config import IbkrGatewayConfig


class Notifier:
    def __init__(self, config: IbkrGatewayConfig) -> None:
        self.config = config
        self._sent_keys: set[str] = set()

    def notify_once(self, key: str, subject: str, body: str) -> None:
        if key in self._sent_keys:
            return
        self._sent_keys.add(key)
        self.notify(subject, body)

    def notify(self, subject: str, body: str) -> None:
        if self.config.notification_email_to and self.config.notification_smtp_host:
            self._send_email(subject, body)
            return
        print(f"ALERT {subject}: {body}", flush=True)

    def _send_email(self, subject: str, body: str) -> None:
        sender = self.config.notification_email_from or self.config.notification_smtp_user
        if not sender:
            print(f"ALERT email skipped, sender missing: {subject}: {body}", flush=True)
            return
        message = EmailMessage()
        message["From"] = sender
        message["To"] = self.config.notification_email_to
        message["Subject"] = subject
        message.set_content(body)
        password = os.environ.get("IBKR_GATEWAY_ALERT_SMTP_PASSWORD") or os.environ.get("ALERT_SMTP_PASSWORD") or ""
        with smtplib.SMTP(self.config.notification_smtp_host, self.config.notification_smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if self.config.notification_smtp_user:
                smtp.login(self.config.notification_smtp_user, password)
            smtp.send_message(message)
