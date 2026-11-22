"""Outgoing email: a welcome message on sign-up and "your job is ready" notices.

Configured from the environment: ``SMTP_HOST`` (required to send anything), ``SMTP_PORT`` (587), ``SMTP_USER``,
``SMTP_PASSWORD``, ``SMTP_FROM`` (defaults to ``SMTP_USER``), ``SMTP_STARTTLS`` (true; port 465 uses implicit TLS instead) and
``VTT_PUBLIC_URL`` (base URL for links in messages). Without ``SMTP_HOST`` every send is skipped with a log line and
nothing fails. Sending happens off the request path, in a thread, and failures are logged, never raised.

There is no verify-your-address flow: the sign-up email is only a notification.
"""

import asyncio
import html
import logging
import os
import smtplib
import ssl
from collections.abc import Callable
from contextlib import AbstractContextManager
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task[Any]] = set()


def _truthy(value: str | None, *, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def smtp_settings() -> dict[str, Any] | None:
    """SMTP settings from the environment, or ``None`` when email is not configured."""
    host = (os.getenv("SMTP_HOST") or "").strip()
    if not host:
        return None
    try:
        port = int(os.getenv("SMTP_PORT") or 587)
    except ValueError:
        port = 587
    user = os.getenv("SMTP_USER") or None
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": os.getenv("SMTP_PASSWORD") or None,
        "sender": os.getenv("SMTP_FROM") or user or "vtt-transcribe@localhost",
        "starttls": _truthy(os.getenv("SMTP_STARTTLS"), default=True),
    }


def email_configured() -> bool:
    return smtp_settings() is not None


def public_url() -> str:
    return (os.getenv("VTT_PUBLIC_URL") or "").strip().rstrip("/")


def _smtp_connect(settings: dict[str, Any]) -> AbstractContextManager[Any]:
    """The real transport; tests replace :data:`transport_factory` with a fake (no network)."""
    if settings["port"] == 465:
        return smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=15, context=ssl.create_default_context())
    return smtplib.SMTP(settings["host"], settings["port"], timeout=15)


transport_factory: Callable[[dict[str, Any]], AbstractContextManager[Any]] = _smtp_connect


def _clean(value: str) -> str:
    """Header values must be one line."""
    return " ".join(value.split())


def build_message(settings: dict[str, Any], to: str, subject: str, text: str, html_body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _clean(settings["sender"])
    msg["To"] = _clean(to)
    msg["Subject"] = _clean(subject)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    return msg


def send_email_sync(to: str, subject: str, text: str, html_body: str) -> bool:
    """Send one message now (blocking). Returns False when skipped or failed; never raises."""
    settings = smtp_settings()
    if settings is None:
        logger.info("Email skipped: SMTP is not configured", extra={"subject": subject})
        return False
    try:
        msg = build_message(settings, to, subject, text, html_body)
        with transport_factory(settings) as smtp:
            if settings["starttls"] and settings["port"] != 465:
                smtp.starttls(context=ssl.create_default_context())
            if settings["user"] and settings["password"]:
                smtp.login(settings["user"], settings["password"])
            smtp.send_message(msg)
    except Exception:
        logger.exception("Email could not be sent", extra={"subject": subject})
        return False
    return True


async def send_email(to: str, subject: str, text: str, html_body: str) -> bool:
    return await asyncio.to_thread(send_email_sync, to, subject, text, html_body)


def _spawn(coro: Any) -> None:
    """Run a coroutine in the background (strong reference kept until done). Outside a loop: skipped."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _link(path: str = "") -> str:
    base = public_url()
    return f"{base}/{path}" if base else ""


def welcome_message(username: str) -> tuple[str, str, str]:
    """(subject, plain text, html)."""
    link = _link()
    text = f"Hi {username},\n\nYour VTT Transcribe account has been created."
    body = f"<p>Hi {html.escape(username)},</p><p>Your VTT Transcribe account has been created.</p>"
    if link:
        text += f"\n\nSign in: {link}"
        body += f'<p><a href="{html.escape(link)}">Sign in</a></p>'
    text += "\n\nIf this was not you, you can ignore this message."
    body += "<p>If this was not you, you can ignore this message.</p>"
    return "Welcome to VTT Transcribe", text, body


def job_message(username: str, job: dict[str, Any]) -> tuple[str, str, str]:
    name = job.get("filename") or "your file"
    link = _link(f"?job={job['job_id']}") if job.get("job_id") else ""
    if job.get("status") == "completed":
        subject = f"Your transcript is ready: {name}"
        lead = f"Your transcript for {name} is ready."
    else:
        subject = f"Transcription failed: {name}"
        lead = f"Your job for {name} did not finish: {job.get('error') or 'unknown error'}."
    text = f"Hi {username},\n\n{lead}"
    body = f"<p>Hi {html.escape(username)},</p><p>{html.escape(lead)}</p>"
    if link:
        text += f"\n\nOpen the job: {link}"
        body += f'<p><a href="{html.escape(link)}">Open the job</a></p>'
    if job.get("expires_at"):
        text += f"\n\nIt will be deleted on {job['expires_at'][:10]}."
        body += f"<p>It will be deleted on {html.escape(job['expires_at'][:10])}.</p>"
    return subject, text, body


def notify_welcome(email: str, username: str) -> None:
    """Fire-and-forget welcome email."""
    if not email_configured():
        logger.info("Welcome email skipped: SMTP is not configured")
        return
    subject, text, body = welcome_message(username)
    _spawn(send_email(email, subject, text, body))


def notify_job_finished(job: dict[str, Any]) -> None:
    """Fire-and-forget "job finished" email to the owner, unless they opted out. Never raises."""
    if not email_configured():
        logger.info("Job email skipped: SMTP is not configured", extra={"job_id": job.get("job_id")})
        return
    _spawn(_notify_job(dict(job)))


async def _notify_job(job: dict[str, Any]) -> None:
    try:
        from sqlalchemy import select

        from vtt_server.jobstore import store
        from vtt_server.models import User

        factory = store.factory()
        if factory is None or job.get("user_id") is None:
            return
        async with factory() as db:
            user = (await db.execute(select(User).where(User.id == job["user_id"]))).scalar_one_or_none()
        if user is None or not user.notify_job_ready or not user.email:
            return
        subject, text, body = job_message(user.username, job)
        await send_email(user.email, subject, text, body)
    except Exception:
        logger.exception("Job email failed", extra={"job_id": job.get("job_id")})
