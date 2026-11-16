import logging
import smtplib
from email.message import EmailMessage

from .config import settings

log = logging.getLogger("auth.email")

# Messages captured by the "memory" backend (tests).
outbox: list[dict] = []


class EmailDeliveryError(Exception):
    pass


def send_email(to: str, subject: str, body: str) -> None:
    backend = settings.email_backend
    if backend == "memory":
        outbox.append({"to": to, "subject": subject, "body": body})
        return
    if backend == "console":
        # Development only: the message (including any code) goes to the service log.
        log.warning("email (console backend) to=%s subject=%s\n%s", to, subject, body)
        return

    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.error("email delivery failed: %s", exc)
        raise EmailDeliveryError(str(exc)) from exc


def send_otp(to: str, code: str, purpose: str = "sign in") -> None:
    minutes = settings.mfa_challenge_ttl // 60
    send_email(
        to,
        f"Your {settings.mfa_issuer} verification code",
        f"Your verification code to {purpose} is: {code}\n\n"
        f"It expires in {minutes} minutes. If you did not request it, change your password.\n",
    )
