"""Multi-account IMAP4 client and RFC 5322 MIME parser for EspressoMail."""

from __future__ import annotations

import email
import email.header
import email.policy
import email.utils
import imaplib
import re
import ssl
import uuid
from datetime import datetime, timezone
from email.message import Message
from typing import Sequence

from espressomail.models.entities import (
    AccountConfig,
    Attachment,
    EmailMessage,
    Folder,
    FolderType,
    PGPStatus,
    SecurityType,
)
from espressomail.storage.database import LocalStore


def decode_mime_header(raw_val: str | None) -> str:
    """Decode RFC 2047 encoded MIME header values into clean Unicode text."""
    if not raw_val:
        return ""
    try:
        parts = email.header.decode_header(raw_val)
        decoded: list[str] = []
        for content, charset in parts:
            if isinstance(content, bytes):
                enc = charset or "utf-8"
                try:
                    decoded.append(content.decode(enc, errors="replace"))
                except LookupError:
                    decoded.append(content.decode("utf-8", errors="replace"))
            else:
                decoded.append(str(content))
        return "".join(decoded).strip()
    except Exception:
        return str(raw_val).strip()


def parse_address_list(headers: Sequence[str]) -> list[str]:
    """Parse one or more RFC 5322 address header strings into formatted addresses."""
    out: list[str] = []
    for name, addr in email.utils.getaddresses(list(headers)):
        clean_name = decode_mime_header(name)
        clean_addr = addr.strip()
        if not clean_addr:
            continue
        if clean_name:
            out.append(f"{clean_name} <{clean_addr}>")
        else:
            out.append(clean_addr)
    return out


def parse_rfc5322_bytes(
    raw_bytes: bytes,
    account_id: str,
    folder: str = "INBOX",
    uid: int = 0,
    imap_flags: Sequence[str] = (),
) -> EmailMessage:
    """Parse raw RFC 5322 email bytes into an EmailMessage domain object."""
    msg: Message = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    msg_id_hdr = str(msg.get("Message-ID", "")).strip() or f"<{uuid.uuid4().hex}@local>"
    in_reply_to = str(msg.get("In-Reply-To", "")).strip()
    refs_raw = str(msg.get("References", "")).strip()
    references = [r for r in re.findall(r"<[^>]+>", refs_raw)] if refs_raw else []

    subject = decode_mime_header(msg.get("Subject")) or "(No Subject)"
    from_raw = str(msg.get("From", ""))
    sender_name, sender_email = email.utils.parseaddr(from_raw)
    sender_name = decode_mime_header(sender_name)

    to_headers = msg.get_all("To", [])
    cc_headers = msg.get_all("Cc", [])
    bcc_headers = msg.get_all("Bcc", [])

    date_hdr = str(msg.get("Date", "")).strip()
    timestamp = 0.0
    date_iso = ""
    if date_hdr:
        try:
            dt = email.utils.parsedate_to_datetime(date_hdr)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            timestamp = dt.timestamp()
            date_iso = dt.isoformat()
        except Exception:
            pass
    if not date_iso:
        now = datetime.now(tz=timezone.utc)
        timestamp = now.timestamp()
        date_iso = now.isoformat()

    local_id = f"{account_id}:{folder}:{uid or uuid.uuid4().hex[:8]}"
    body_text_parts: list[str] = []
    body_html_parts: list[str] = []
    attachments: list[Attachment] = []
    pgp_status = PGPStatus.NONE
    pgp_raw_payload = ""

    content_type_root = msg.get_content_type().lower()
    if content_type_root == "multipart/signed":
        pgp_status = PGPStatus.SIGNED_UNVERIFIED
    elif content_type_root == "multipart/encrypted":
        pgp_status = PGPStatus.ENCRYPTED

    if msg.is_multipart():
        for idx, part in enumerate(msg.walk()):
            if part.is_multipart():
                continue
            ctype = part.get_content_type().lower()
            disp = str(part.get_content_disposition() or "").lower()
            filename = decode_mime_header(part.get_filename())

            if ctype == "application/pgp-signature":
                pgp_status = PGPStatus.SIGNED_UNVERIFIED
                payload_bytes = part.get_payload(decode=True) or b""
                pgp_raw_payload = payload_bytes.decode("utf-8", errors="replace")
                continue
            if ctype == "application/pgp-encrypted":
                pgp_status = PGPStatus.ENCRYPTED
                continue

            payload_bytes = part.get_payload(decode=True)
            if payload_bytes is None:
                continue

            if disp == "attachment" or filename:
                fname = filename or f"attachment_{idx + 1}.bin"
                att_id = f"{local_id}:att:{idx}:{fname}"
                attachments.append(
                    Attachment(
                        id=att_id,
                        message_id=local_id,
                        filename=fname,
                        content_type=ctype,
                        size_bytes=len(payload_bytes),
                        content_id=str(part.get("Content-ID", "")).strip("<>"),
                        is_inline=(disp == "inline"),
                        data=payload_bytes,
                    )
                )
            elif ctype == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                try:
                    text = payload_bytes.decode(charset, errors="replace")
                except LookupError:
                    text = payload_bytes.decode("utf-8", errors="replace")
                body_text_parts.append(text)
            elif ctype == "text/html":
                charset = part.get_content_charset() or "utf-8"
                try:
                    html_str = payload_bytes.decode(charset, errors="replace")
                except LookupError:
                    html_str = payload_bytes.decode("utf-8", errors="replace")
                body_html_parts.append(html_str)
            elif ctype.startswith("image/"):
                fname = filename or f"inline_image_{idx + 1}.{ctype.split('/')[-1]}"
                att_id = f"{local_id}:att:{idx}:{fname}"
                attachments.append(
                    Attachment(
                        id=att_id,
                        message_id=local_id,
                        filename=fname,
                        content_type=ctype,
                        size_bytes=len(payload_bytes),
                        content_id=str(part.get("Content-ID", "")).strip("<>"),
                        is_inline=True,
                        data=payload_bytes,
                    )
                )
    else:
        payload_bytes = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            text = payload_bytes.decode(charset, errors="replace")
        except LookupError:
            text = payload_bytes.decode("utf-8", errors="replace")
        if content_type_root == "text/html":
            body_html_parts.append(text)
        else:
            body_text_parts.append(text)

    body_text = "\n".join(body_text_parts).strip()
    body_html = "\n".join(body_html_parts).strip()

    # Check inline OpenPGP armor markers
    if "-----BEGIN PGP SIGNED MESSAGE-----" in body_text:
        pgp_status = PGPStatus.SIGNED_UNVERIFIED
        pgp_raw_payload = body_text
    elif "-----BEGIN PGP MESSAGE-----" in body_text:
        pgp_status = PGPStatus.ENCRYPTED
        pgp_raw_payload = body_text

    flags_upper = {f.upper() for f in imap_flags}
    is_read = "\\SEEN" in flags_upper
    is_starred = "\\FLAGGED" in flags_upper
    is_draft = "\\DRAFT" in flags_upper or folder.lower() == "drafts"
    is_trashed = "\\DELETED" in flags_upper or folder.lower() == "trash"
    is_answered = "\\ANSWERED" in flags_upper

    raw_headers = {k: decode_mime_header(str(v)) for k, v in msg.items()}
    thread_id = in_reply_to or (references[0] if references else msg_id_hdr)

    return EmailMessage(
        id=local_id,
        account_id=account_id,
        folder=folder,
        uid=uid,
        message_id=msg_id_hdr,
        in_reply_to=in_reply_to,
        references=references,
        thread_id=thread_id,
        subject=subject,
        sender_name=sender_name,
        sender_email=sender_email,
        recipients_to=parse_address_list(to_headers),
        recipients_cc=parse_address_list(cc_headers),
        recipients_bcc=parse_address_list(bcc_headers),
        date_iso=date_iso,
        timestamp=timestamp,
        body_text=body_text,
        body_html=body_html,
        raw_headers=raw_headers,
        is_read=is_read,
        is_starred=is_starred,
        is_draft=is_draft,
        is_trashed=is_trashed,
        is_answered=is_answered,
        pgp_status=pgp_status,
        pgp_raw_payload=pgp_raw_payload,
        attachments=attachments,
    )


class IMAPClient:
    """Client for synchronizing folders, messages, and flags with an IMAP4 server."""

    def __init__(self, account: AccountConfig, timeout: float = 15.0) -> None:
        self.account = account
        self.timeout = timeout
        self._conn: imaplib.IMAP4 | None = None

    def connect(self) -> None:
        """Connect and authenticate to the configured IMAP server."""
        ctx = ssl.create_default_context()
        if self.account.imap_security == SecurityType.SSL_TLS:
            self._conn = imaplib.IMAP4_SSL(
                host=self.account.imap_host,
                port=self.account.imap_port,
                ssl_context=ctx,
                timeout=self.timeout,
            )
        else:
            self._conn = imaplib.IMAP4(
                host=self.account.imap_host,
                port=self.account.imap_port,
                timeout=self.timeout,
            )
            if self.account.imap_security == SecurityType.STARTTLS:
                self._conn.starttls(ssl_context=ctx)

        if self.account.imap_password:
            self._conn.login(self.account.imap_username, self.account.imap_password)

    def disconnect(self) -> None:
        """Gracefully close and logout from the IMAP connection."""
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def list_remote_folders(self) -> list[Folder]:
        """Discover remote IMAP folders on the server."""
        if self._conn is None:
            raise RuntimeError("IMAPClient is not connected")
        status, data = self._conn.list()
        if status != "OK" or not data:
            return []

        folders: list[Folder] = []
        pattern = re.compile(r'\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]+)"\s+"?(?P<name>[^"]+)"?')
        for item in data:
            if not isinstance(item, bytes):
                continue
            line = item.decode("utf-8", errors="replace")
            m = pattern.search(line)
            if not m:
                continue
            name = m.group("name").strip()
            flags = m.group("flags").lower()
            ftype = self._infer_folder_type(name, flags)
            folders.append(
                Folder(
                    id=f"{self.account.id}:{name}",
                    account_id=self.account.id,
                    name=name,
                    display_name=name.split("/")[-1],
                    folder_type=ftype,
                )
            )
        return folders

    @staticmethod
    def _infer_folder_type(name: str, flags: str) -> FolderType:
        n = name.lower()
        if n == "inbox":
            return FolderType.INBOX
        if "\\sent" in flags or "sent" in n:
            return FolderType.SENT
        if "\\drafts" in flags or "draft" in n:
            return FolderType.DRAFTS
        if "\\trash" in flags or "trash" in n or "deleted" in n:
            return FolderType.TRASH
        if "\\archive" in flags or "archive" in n:
            return FolderType.ARCHIVE
        if "\\junk" in flags or "spam" in n or "junk" in n:
            return FolderType.SPAM
        if "\\flagged" in flags or "star" in n:
            return FolderType.STARRED
        return FolderType.CUSTOM

    def fetch_recent_messages(self, folder: str = "INBOX", limit: int = 50) -> list[EmailMessage]:
        """Fetch and parse the most recent messages from an IMAP mailbox folder."""
        if self._conn is None:
            raise RuntimeError("IMAPClient is not connected")
        status, _ = self._conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            return []

        status, search_data = self._conn.uid("SEARCH", None, "ALL")
        if status != "OK" or not search_data or not search_data[0]:
            return []

        uids = [int(u) for u in search_data[0].split() if u.isdigit()]
        recent_uids = uids[-limit:]
        messages: list[EmailMessage] = []

        for uid in reversed(recent_uids):
            f_status, f_data = self._conn.uid("FETCH", str(uid), "(FLAGS BODY.PEEK[])")
            if f_status != "OK" or not f_data:
                continue
            for part in f_data:
                if isinstance(part, tuple) and len(part) >= 2:
                    meta_line = part[0].decode("utf-8", errors="replace")
                    raw_msg_bytes = part[1]
                    flag_match = re.search(r"FLAGS\s*\(([^)]*)\)", meta_line)
                    flags = flag_match.group(1).split() if flag_match else []
                    parsed = parse_rfc5322_bytes(
                        raw_bytes=raw_msg_bytes,
                        account_id=self.account.id,
                        folder=folder,
                        uid=uid,
                        imap_flags=flags,
                    )
                    messages.append(parsed)
        return messages

    def set_flag(self, folder: str, uid: int, flag: str, enable: bool = True) -> bool:
        """Add or remove an IMAP flag (e.g. \\Seen, \\Flagged, \\Deleted) by message UID."""
        if self._conn is None or uid <= 0:
            return False
        status, _ = self._conn.select(f'"{folder}"', readonly=False)
        if status != "OK":
            return False
        cmd = "+FLAGS" if enable else "-FLAGS"
        res_status, _ = self._conn.uid("STORE", str(uid), cmd, f"({flag})")
        return res_status == "OK"

    def sync_to_store(self, store: LocalStore, folders: Sequence[str] = ("INBOX", "Sent", "Drafts")) -> int:
        """Synchronize recent messages from specified remote folders into LocalStore."""
        count = 0
        for folder in folders:
            fetched = self.fetch_recent_messages(folder=folder, limit=50)
            for msg in fetched:
                store.save_message(msg)
                count += 1
        store.refresh_folder_counts()
        return count
