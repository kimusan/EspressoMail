"""Multi-account SMTP submission client and outgoing MIME builder for EspressoMail."""

from __future__ import annotations

import email.utils
import mimetypes
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage as StdlibEmailMessage
from pathlib import Path
from typing import Sequence

from espressomail.models.entities import (
    AccountConfig,
    Attachment,
    EmailMessage,
    PGPStatus,
    SecurityType,
)


def build_mime_message(
    account: AccountConfig,
    recipients_to: Sequence[str],
    subject: str,
    body_text: str,
    recipients_cc: Sequence[str] = (),
    recipients_bcc: Sequence[str] = (),
    in_reply_to: str = "",
    references: Sequence[str] = (),
    attachment_paths: Sequence[str | Path] = (),
    extra_attachments: Sequence[Attachment] = (),
    body_html: str = "",
) -> tuple[StdlibEmailMessage, EmailMessage]:
    """Construct both a standard library MIME EmailMessage and a local domain EmailMessage."""
    mime_msg = StdlibEmailMessage()
    domain = account.email.split("@")[-1] if "@" in account.email else "espressomail.local"
    msg_id = f"<{uuid.uuid4().hex}@{domain}>"
    now = datetime.now(tz=timezone.utc)

    sender_formatted = (
        f"{account.full_name} <{account.email}>" if account.full_name else account.email
    )
    mime_msg["From"] = sender_formatted
    mime_msg["To"] = ", ".join(recipients_to)
    if recipients_cc:
        mime_msg["Cc"] = ", ".join(recipients_cc)
    mime_msg["Subject"] = subject
    mime_msg["Date"] = email.utils.format_datetime(now)
    mime_msg["Message-ID"] = msg_id
    mime_msg["User-Agent"] = "EspressoMail/0.1.0 (Espresso TUI)"

    if in_reply_to:
        mime_msg["In-Reply-To"] = in_reply_to
    if references:
        mime_msg["References"] = " ".join(references)

    mime_msg.set_content(body_text)
    if body_html:
        mime_msg.add_alternative(body_html, subtype="html")

    local_msg_id = f"{account.id}:Sent:{uuid.uuid4().hex[:10]}"
    domain_attachments: list[Attachment] = []

    for path_item in attachment_paths:
        p = Path(path_item).expanduser().resolve()
        if not p.exists() or not p.is_file():
            continue
        raw_data = p.read_bytes()
        guessed_type, _ = mimetypes.guess_type(p.name)
        ctype = guessed_type or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1) if "/" in ctype else ("application", "octet-stream")
        mime_msg.add_attachment(
            raw_data,
            maintype=maintype,
            subtype=subtype,
            filename=p.name,
        )
        domain_attachments.append(
            Attachment(
                id=f"{local_msg_id}:att:{p.name}",
                message_id=local_msg_id,
                filename=p.name,
                content_type=ctype,
                size_bytes=len(raw_data),
                data=raw_data,
                local_path=str(p),
            )
        )

    for att in extra_attachments:
        payload = att.data or (Path(att.local_path).read_bytes() if att.local_path and Path(att.local_path).exists() else b"")
        ctype = att.content_type or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1) if "/" in ctype else ("application", "octet-stream")
        mime_msg.add_attachment(
            payload,
            maintype=maintype,
            subtype=subtype,
            filename=att.filename,
        )
        domain_attachments.append(
            Attachment(
                id=att.id or f"{local_msg_id}:att:{att.filename}",
                message_id=local_msg_id,
                filename=att.filename,
                content_type=ctype,
                size_bytes=len(payload),
                data=payload,
                local_path=att.local_path,
            )
        )

    pgp_status = PGPStatus.NONE
    if "-----BEGIN PGP SIGNED MESSAGE-----" in body_text:
        pgp_status = PGPStatus.SIGNED_VERIFIED
    elif "-----BEGIN PGP MESSAGE-----" in body_text:
        pgp_status = PGPStatus.ENCRYPTED

    thread_id = in_reply_to or (references[0] if references else msg_id)

    domain_msg = EmailMessage(
        id=local_msg_id,
        account_id=account.id,
        folder="Sent",
        message_id=msg_id,
        in_reply_to=in_reply_to,
        references=list(references),
        thread_id=thread_id,
        subject=subject,
        sender_name=account.full_name,
        sender_email=account.email,
        recipients_to=list(recipients_to),
        recipients_cc=list(recipients_cc),
        recipients_bcc=list(recipients_bcc),
        date_iso=now.isoformat(),
        timestamp=now.timestamp(),
        body_text=body_text,
        body_html=body_html,
        is_read=True,
        pgp_status=pgp_status,
        attachments=domain_attachments,
    )
    return mime_msg, domain_msg


class SMTPClient:
    """SMTP submission client supporting STARTTLS, Implicit TLS, and MIME attachments."""

    def __init__(self, account: AccountConfig, timeout: float = 15.0) -> None:
        self.account = account
        self.timeout = timeout

    def _connect_and_authenticate(self) -> smtplib.SMTP:
        """Connect to the configured SMTP server, negotiate TLS, and authenticate."""
        if not self.account.smtp_host.strip():
            raise ValueError("SMTP host is required.")
        if not self.account.smtp_username.strip() or not self.account.smtp_password:
            raise ValueError("SMTP username and password are required.")

        ctx = ssl.create_default_context()
        if self.account.smtp_security == SecurityType.SSL_TLS:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                host=self.account.smtp_host,
                port=self.account.smtp_port,
                context=ctx,
                timeout=self.timeout,
            )
        else:
            server = smtplib.SMTP(
                host=self.account.smtp_host,
                port=self.account.smtp_port,
                timeout=self.timeout,
            )

        try:
            server.ehlo()
            if self.account.smtp_security == SecurityType.STARTTLS:
                server.starttls(context=ctx)
                server.ehlo()
            server.login(self.account.smtp_username, self.account.smtp_password)
            return server
        except Exception:
            try:
                server.quit()
            except Exception:
                pass
            raise

    def verify_credentials(self) -> tuple[bool, str]:
        """Validate SMTP server reachability, TLS handshake, and user credentials."""
        try:
            server = self._connect_and_authenticate()
            try:
                server.quit()
            except Exception:
                pass
            return True, f"SMTP login verified ({self.account.smtp_host}:{self.account.smtp_port})"
        except smtplib.SMTPAuthenticationError as exc:
            return False, f"SMTP authentication failed ({self.account.smtp_host}:{self.account.smtp_port}): {exc}"
        except Exception as exc:
            return False, f"SMTP connection failed ({self.account.smtp_host}:{self.account.smtp_port}): {exc}"

    def send_mime_message(
        self,
        mime_msg: StdlibEmailMessage,
        all_recipients: Sequence[str] | None = None,
    ) -> None:
        """Transmit an RFC 5322 EmailMessage via the account's configured SMTP server."""
        server = self._connect_and_authenticate()
        try:
            server.send_message(mime_msg, to_addrs=list(all_recipients) if all_recipients else None)
        finally:
            try:
                server.quit()
            except Exception:
                pass


def validate_account_credentials(
    account: AccountConfig,
    timeout: float = 8.0,
) -> tuple[bool, str]:
    """Verify both IMAP and SMTP server reachability and credentials for an account."""
    from espressomail.protocols.imap_client import IMAPClient

    if not account.imap_password or not account.smtp_password:
        return False, "Account password is required to authenticate with IMAP/SMTP."

    imap_ok, imap_msg = IMAPClient(account, timeout=timeout).verify_credentials()
    if not imap_ok:
        return False, imap_msg

    smtp_ok, smtp_msg = SMTPClient(account, timeout=timeout).verify_credentials()
    if not smtp_ok:
        return False, smtp_msg

    return True, f"Verified IMAP ({account.imap_host}:{account.imap_port}) & SMTP ({account.smtp_host}:{account.smtp_port})"

