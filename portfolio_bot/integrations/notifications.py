from __future__ import annotations

import hashlib
import re
import smtplib
import subprocess
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from urllib.parse import quote

import requests

from ..config import BotConfig
from ..runtime import RuntimeStore, runtime_path


MAX_NOTIFICATION_REPEATS = 3
NOTIFICATION_REPEAT_WINDOW = timedelta(hours=24)


@dataclass(slots=True)
class NotificationResult:
    channel: str
    ok: bool
    detail: str = ""


class Notifier:
    def send(self, subject: str, body: str) -> NotificationResult:
        raise NotImplementedError


class DryRunNotifier(Notifier):
    def __init__(self, channel: str):
        self.channel = channel

    def send(self, subject: str, body: str) -> NotificationResult:
        body = dedupe_repeated_lines(body)
        preview = f"{subject}\n{body[:500]}"
        print(f"[dry-run:{self.channel}]\n{preview}")
        return NotificationResult(self.channel, True, "dry-run")


class IMessageNotifier(Notifier):
    def __init__(self, recipient: str, max_message_chars: int = 1600, config: BotConfig | None = None):
        self.recipient = recipient
        self.max_message_chars = max_message_chars
        self.config = config

    def send(self, subject: str, body: str) -> NotificationResult:
        if not self.recipient:
            return NotificationResult("imessage", False, "IMESSAGE_RECIPIENT is not configured")
        body = dedupe_repeated_lines(body)
        if self.config and not allow_notification_send(self.config, "imessage", subject, body):
            return NotificationResult("imessage", True, "skipped duplicate after 3 sends in 24h")
        message = f"{subject}\n{body}".strip()
        chunks = split_message(message, self.max_message_chars)
        errors: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            if len(chunks) > 1:
                chunk = f"{subject} ({index}/{len(chunks)})\n{chunk}"
            result = self._send_one(chunk)
            if not result.ok:
                errors.append(result.detail)
        if errors:
            return NotificationResult("imessage", False, "; ".join(errors))
        return NotificationResult("imessage", True, f"sent {len(chunks)} message(s)")

    def _send_one(self, message: str) -> NotificationResult:
        script = f'''
tell application "Messages"
  set targetService to 1st service whose service type = iMessage
  set targetBuddy to buddy "{_escape_applescript(self.recipient)}" of targetService
  send "{_escape_applescript(message)}" to targetBuddy
end tell
'''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            return NotificationResult("imessage", False, result.stderr.strip())
        return NotificationResult("imessage", True, "sent")


class EmailNotifier(Notifier):
    def __init__(self, config: BotConfig):
        self.config = config

    def send(self, subject: str, body: str) -> NotificationResult:
        if not self.config.email_username or not self.config.email_app_password:
            return NotificationResult("email", False, "EMAIL_USERNAME or EMAIL_APP_PASSWORD is not configured")
        body = dedupe_repeated_lines(body)
        if not allow_notification_send(self.config, "email", subject, body):
            return NotificationResult("email", True, "skipped duplicate after 3 sends in 24h")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.config.email_from or self.config.email_username
        msg["To"] = self.config.email_to
        msg.set_content(body)
        with smtplib.SMTP_SSL(self.config.email_smtp_host, self.config.email_smtp_port, timeout=20) as smtp:
            smtp.login(self.config.email_username, self.config.email_app_password)
            smtp.send_message(msg)
        return NotificationResult("email", True, "sent")


class AgentMailNotifier(Notifier):
    def __init__(self, config: BotConfig):
        self.config = config

    def send(self, subject: str, body: str) -> NotificationResult:
        if not self.config.agentmail_api_key:
            return NotificationResult("agentmail", False, "AGENTMAIL_API_KEY is not configured")
        if not self.config.email_to:
            return NotificationResult("agentmail", False, "EMAIL_TO is not configured")
        body = dedupe_repeated_lines(body)
        if not allow_notification_send(self.config, "agentmail", subject, body):
            return NotificationResult("agentmail", True, "skipped duplicate after 3 sends in 24h")
        try:
            inbox_id = self.config.agentmail_inbox_id or self._create_or_get_inbox()
            message_id = self._send_email(inbox_id, subject, body)
            return NotificationResult("agentmail", True, f"sent via {inbox_id}; message_id={message_id or 'unknown'}")
        except Exception as exc:
            return NotificationResult("agentmail", False, sanitize_notification_error(str(exc)))

    def _create_or_get_inbox(self) -> str:
        response = requests.post(
            f"{self.config.agentmail_base_url.rstrip('/')}/v0/inboxes",
            headers=self._headers(),
            json={
                "client_id": self.config.agentmail_client_id,
                "display_name": self.config.agentmail_display_name,
            },
            timeout=20,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"inbox create failed: HTTP {response.status_code} {response.text[:500]}")
        payload = response.json() if response.content else {}
        inbox_id = (
            payload.get("inbox_id")
            or payload.get("id")
            or payload.get("email")
            or payload.get("address")
            or payload.get("inbox", {}).get("inbox_id")
            or payload.get("inbox", {}).get("id")
            or payload.get("inbox", {}).get("email")
        )
        if not inbox_id:
            raise RuntimeError(f"inbox create response missing inbox id: {payload}")
        return str(inbox_id)

    def _send_email(self, inbox_id: str, subject: str, body: str) -> str:
        response = requests.post(
            f"{self.config.agentmail_base_url.rstrip('/')}/v0/inboxes/{quote(inbox_id, safe='')}/messages/send",
            headers=self._headers(),
            json={
                "to": [self.config.email_to],
                "subject": subject,
                "text": body,
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"send failed: HTTP {response.status_code} {response.text[:500]}")
        payload = response.json() if response.content else {}
        return str(payload.get("message_id") or payload.get("id") or payload.get("message", {}).get("id") or "")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.agentmail_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


class CompositeNotifier(Notifier):
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, subject: str, body: str) -> NotificationResult:
        body = dedupe_repeated_lines(body)
        selected = select_notifiers(self.notifiers, subject, body)
        results = [notifier.send(subject, body) for notifier in selected]
        if any(item.ok for item in results):
            return NotificationResult("composite", True, "; ".join(f"{r.channel}:{r.detail}" for r in results))
        return NotificationResult("composite", False, "; ".join(f"{r.channel}:{r.detail}" for r in results))


def build_notifier(config: BotConfig, dry_run: bool = False) -> CompositeNotifier:
    notifiers: list[Notifier] = []
    use_dry_run = dry_run or config.notifications.dry_run
    if config.notifications.imessage_enabled:
        notifiers.append(DryRunNotifier("imessage") if use_dry_run else IMessageNotifier(config.imessage_recipient, config=config))
    if config.notifications.email_enabled:
        notifiers.append(DryRunNotifier("email") if use_dry_run else EmailNotifier(config))
    if config.notifications.agentmail_enabled:
        notifiers.append(DryRunNotifier("agentmail") if use_dry_run else AgentMailNotifier(config))
    if not notifiers:
        notifiers.append(DryRunNotifier("none"))
    return CompositeNotifier(notifiers)


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def split_message(message: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        return [message]
    if len(message) <= max_chars:
        return [message]
    chunks: list[str] = []
    current = ""
    for paragraph in message.splitlines():
        candidate = paragraph if not current else current + "\n" + paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > max_chars:
            chunks.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks or [message]


def dedupe_repeated_lines(text: str, max_repeats: int = MAX_NOTIFICATION_REPEATS) -> str:
    if max_repeats <= 0:
        return ""
    counts: dict[str, int] = {}
    lines: list[str] = []
    for line in str(text or "").splitlines():
        key = " ".join(line.split()).strip().lower()
        if not key:
            lines.append(line)
            continue
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= max_repeats:
            lines.append(line)
    return "\n".join(lines)


def allow_notification_send(
    config: BotConfig,
    channel: str,
    subject: str,
    body: str,
    *,
    max_repeats: int = MAX_NOTIFICATION_REPEATS,
    window: timedelta = NOTIFICATION_REPEAT_WINDOW,
) -> bool:
    if getattr(config.notifications, "semantic_dedupe_enabled", True):
        digest = semantic_notification_key(channel, subject, body)
    else:
        normalized = "\n".join(
            [
                str(channel or "").strip().lower(),
                " ".join(str(subject or "").split()).strip().lower(),
                " ".join(str(body or "").split()).strip().lower(),
            ]
        )
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    try:
        runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        return runtime.check_and_touch_repeat_limit(f"notification:{digest}", window, max_repeats, commit=True)
    except Exception:
        return True


def semantic_notification_key(channel: str, subject: str, body: str) -> str:
    subject_text = " ".join(str(subject or "").split())
    body_text = " ".join(str(body or "").split())
    combined = f"{subject_text} {body_text}"
    symbols = sorted(extract_notification_symbols(combined))
    event_type = infer_notification_event_type(subject_text, body_text)
    normalized = normalize_notification_summary(body_text or subject_text)
    payload = "\n".join(
        [
            str(channel or "").strip().lower(),
            ",".join(symbols[:12]),
            event_type,
            normalized[:1000],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_notification_symbols(text: str) -> set[str]:
    stopwords = {
        "THE",
        "AND",
        "FOR",
        "WITH",
        "THIS",
        "THAT",
        "FROM",
        "NEWS",
        "REPORT",
        "DAILY",
        "PORTFOLIO",
        "BOT",
        "ETF",
        "API",
        "LLM",
    }
    symbols = set()
    for raw in re.findall(r"(?<![A-Za-z0-9])\$?([A-Z][A-Z0-9]{1,5})(?![A-Za-z0-9])", text or ""):
        if raw not in stopwords:
            symbols.add(raw)
    return symbols


def infer_notification_event_type(subject: str, body: str) -> str:
    text = f"{subject} {body}".lower()
    if "日报" in subject or "daily" in text or "report" in text:
        return "daily_report"
    if "paper" in text or "纸面订单" in text or "模拟" in text:
        return "paper_order"
    if "strategy" in text or "策略" in text or "signal" in text or "信号" in text:
        return "strategy_signal"
    if "news" in text or "新闻" in text or "digest" in text:
        return "news_digest"
    if re.search(r"[+-]\s*\d+(\.\d+)?%", text):
        return "major_move"
    return "generic"


def normalize_notification_summary(text: str) -> str:
    value = str(text or "").lower()
    value = re.sub(r"https?://\S+", " URL ", value)
    value = re.sub(r"\$?\d+(?:,\d{3})*(?:\.\d+)?%?", " NUM ", value)
    value = re.sub(r"\b20\d{2}-\d{2}-\d{2}\b", " DATE ", value)
    return " ".join(value.split())


def select_notifiers(notifiers: list[Notifier], subject: str, body: str) -> list[Notifier]:
    """Use exactly one AgentMail email for long content and keep short alerts on iMessage."""
    has_agentmail = any(is_agentmail_notifier(notifier) for notifier in notifiers)
    if not has_agentmail:
        return notifiers
    text = f"{subject}\n{body}"
    subject_lower = subject.lower()
    long_report = len(text) > 1800 or "report" in subject_lower or "日报" in subject or "deep analysis" in subject_lower
    if long_report:
        agentmail = [notifier for notifier in notifiers if is_agentmail_notifier(notifier)]
        return agentmail[:1] or notifiers[:1]
    realtime = [notifier for notifier in notifiers if is_imessage_notifier(notifier)]
    return realtime or [notifier for notifier in notifiers if not is_agentmail_notifier(notifier)] or notifiers[:1]


def is_agentmail_notifier(notifier: Notifier) -> bool:
    return isinstance(notifier, AgentMailNotifier) or isinstance(notifier, DryRunNotifier) and notifier.channel == "agentmail"


def is_imessage_notifier(notifier: Notifier) -> bool:
    return isinstance(notifier, IMessageNotifier) or isinstance(notifier, DryRunNotifier) and notifier.channel == "imessage"


def sanitize_notification_error(value: str) -> str:
    text = value
    if "Bearer " in text:
        text = text.split("Bearer ", 1)[0] + "Bearer [REDACTED]"
    for prefix in ("am_us_", "sk-"):
        idx = text.find(prefix)
        if idx >= 0:
            end = idx + len(prefix)
            while end < len(text) and text[end].isalnum():
                end += 1
            text = text[:idx] + prefix + "[REDACTED]" + text[end:]
    return text
