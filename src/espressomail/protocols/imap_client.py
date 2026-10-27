"""Multi-account IMAP4 client and RFC 5322 MIME parser for EspressoMail."""

from __future__ import annotations

import codecs
import email
import email.header
import email.utils
import imaplib
import re
import ssl
import uuid
from datetime import datetime, timezone
from email.message import Message
from typing import Any, Sequence

from espressomail.models.entities import (
    AccountConfig,
    Attachment,
    EmailMessage,
    Folder,
    FolderType,
    PGPStatus,
    SecurityType,
    classify_folder_type,
    decode_imap_utf7,
    split_folder_hierarchy,
)
from espressomail.storage.database import LocalStore


def decode_mime_header(raw_val: str | None) -> str:
    """Decode RFC 2047 encoded MIME header values into clean Unicode text."""
    if not raw_val:
        return ""
    try:
        parts = email.header.decode_header(str(raw_val))
        decoded: list[str] = []
        for content, charset in parts:
            if isinstance(content, bytes):
                enc = charset or "utf-8"
                if enc.lower() in ("unknown-8bit", "x-unknown"):
                    enc = "utf-8"
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
    try:
        clean_headers = [str(h) for h in headers if h]
        for name, addr in email.utils.getaddresses(clean_headers):
            clean_name = decode_mime_header(name)
            clean_addr = addr.strip()
            if not clean_addr:
                continue
            if clean_name:
                out.append(f"{clean_name} <{clean_addr}>")
            else:
                out.append(clean_addr)
    except Exception:
        pass
    return out


def parse_rfc5322_bytes(
    raw_bytes: bytes,
    account_id: str,
    folder: str = "INBOX",
    uid: int = 0,
    imap_flags: Sequence[str] = (),
) -> EmailMessage:
    """Parse raw RFC 5322 email bytes into an EmailMessage domain object.

    Uses the tolerant compat32 policy so real-world emails with non-standard headers
    never raise header-parsing exceptions during mailbox synchronization.
    """
    msg: Message = email.message_from_bytes(raw_bytes)

    msg_id_hdr = str(msg.get("Message-ID", "") or "").strip() or f"<{uuid.uuid4().hex}@local>"
    in_reply_to = str(msg.get("In-Reply-To", "") or "").strip()
    refs_raw = str(msg.get("References", "") or "").strip()
    references = [r for r in re.findall(r"<[^>]+>", refs_raw)] if refs_raw else []

    subject = decode_mime_header(msg.get("Subject")) or "(No Subject)"
    from_raw = str(msg.get("From", "") or "")
    sender_name, sender_email = email.utils.parseaddr(from_raw)
    sender_name = decode_mime_header(sender_name)

    to_headers = msg.get_all("To") or []
    cc_headers = msg.get_all("Cc") or []
    bcc_headers = msg.get_all("Bcc") or []

    date_hdr = str(msg.get("Date", "") or "").strip()
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
            disp_hdr = str(part.get("Content-Disposition", "") or "").lower()
            disp = "attachment" if "attachment" in disp_hdr else ("inline" if "inline" in disp_hdr else "")
            try:
                raw_filename = part.get_filename()
            except Exception:
                raw_filename = None
            filename = decode_mime_header(raw_filename)

            if ctype == "application/pgp-signature":
                pgp_status = PGPStatus.SIGNED_UNVERIFIED
                payload_bytes = part.get_payload(decode=True) or b""
                pgp_raw_payload = payload_bytes.decode("utf-8", errors="replace")
                continue
            if ctype == "application/pgp-encrypted":
                pgp_status = PGPStatus.ENCRYPTED
                continue

            try:
                payload_bytes = part.get_payload(decode=True)
            except Exception:
                payload_bytes = None
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
                        content_id=str(part.get("Content-ID", "") or "").strip("<>"),
                        is_inline=(disp == "inline"),
                        data=payload_bytes,
                    )
                )
            elif ctype == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                if charset.lower() in ("unknown-8bit", "x-unknown"):
                    charset = "utf-8"
                try:
                    text = payload_bytes.decode(charset, errors="replace")
                except LookupError:
                    text = payload_bytes.decode("utf-8", errors="replace")
                body_text_parts.append(text)
            elif ctype == "text/html":
                charset = part.get_content_charset() or "utf-8"
                if charset.lower() in ("unknown-8bit", "x-unknown"):
                    charset = "utf-8"
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
                        content_id=str(part.get("Content-ID", "") or "").strip("<>"),
                        is_inline=True,
                        data=payload_bytes,
                    )
                )
    else:
        try:
            payload_bytes = msg.get_payload(decode=True) or b""
        except Exception:
            payload_bytes = b""
        charset = msg.get_content_charset() or "utf-8"
        if charset.lower() in ("unknown-8bit", "x-unknown"):
            charset = "utf-8"
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
    folder_lower = folder.lower()
    is_read = "\\SEEN" in flags_upper
    is_starred = "\\FLAGGED" in flags_upper
    is_draft = "\\DRAFT" in flags_upper or "draft" in folder_lower
    is_trashed = "\\DELETED" in flags_upper or "trash" in folder_lower or "deleted" in folder_lower
    is_answered = "\\ANSWERED" in flags_upper

    raw_headers: dict[str, str] = {}
    for k, v in msg.items():
        try:
            raw_headers[str(k)] = decode_mime_header(str(v))
        except Exception:
            pass

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


def parse_imap_list_response(item: bytes | tuple[Any, ...]) -> tuple[str, str, str] | None:
    """Parse a single IMAP LIST response line into (flags, delimiter, mailbox_name)."""
    if isinstance(item, tuple):
        if len(item) >= 2 and isinstance(item[0], bytes) and isinstance(item[1], bytes):
            header_line = item[0].decode("utf-8", errors="replace")
            literal_name = item[1].decode("utf-8", errors="replace")
            m_lit = re.match(r'^\s*\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)', header_line, re.IGNORECASE)
            if m_lit:
                delim = m_lit.group("delim").strip('"')
                if delim.upper() == "NIL":
                    delim = "/"
                return m_lit.group("flags"), delim, literal_name.strip()
        return None

    if not isinstance(item, bytes):
        return None

    line = item.decode("utf-8", errors="replace").strip()
    if not line:
        return None

    pattern = re.compile(
        r'^\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:\\.|[^"\\])*"|NIL)\s+(?P<name>.+)$',
        re.IGNORECASE,
    )
    m = pattern.match(line)
    if not m:
        return None

    flags = m.group("flags").strip()
    delim_raw = m.group("delim").strip()
    delim = "/" if delim_raw.upper() == "NIL" else delim_raw.strip('"')

    name_raw = m.group("name").strip()
    if name_raw.startswith('"') and name_raw.endswith('"') and len(name_raw) >= 2:
        name_raw = name_raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")

    return flags, delim, name_raw


class IMAPClient:
    """Client for synchronizing folders, messages, and flags with an IMAP4 server."""

    def __init__(self, account: AccountConfig, timeout: float = 20.0) -> None:
        self.account = account
        self.timeout = timeout
        self._conn: imaplib.IMAP4 | None = None

    def connect(self) -> None:
        """Connect and authenticate to the configured IMAP server."""
        if not self.account.imap_host.strip():
            raise ValueError("IMAP host is required.")
        if not self.account.imap_username.strip() or not self.account.imap_password:
            raise ValueError("IMAP username and password are required.")

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

        self._conn.login(self.account.imap_username, self.account.imap_password)

    def verify_credentials(self) -> tuple[bool, str]:
        """Validate IMAP server reachability, TLS handshake, and user credentials."""
        try:
            self.connect()
            self.disconnect()
            return True, f"IMAP login verified ({self.account.imap_host}:{self.account.imap_port})"
        except imaplib.IMAP4.error as exc:
            self.disconnect()
            return False, f"IMAP authentication failed ({self.account.imap_host}:{self.account.imap_port}): {exc}"
        except Exception as exc:
            self.disconnect()
            return False, f"IMAP connection failed ({self.account.imap_host}:{self.account.imap_port}): {exc}"

    def disconnect(self) -> None:
        """Gracefully close and logout from the IMAP connection."""
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def _select_mailbox(self, folder: str, readonly: bool = True) -> bool:
        """Select an IMAP mailbox, handling both quoted and unquoted mailbox names across server types."""
        if self._conn is None:
            return False
        candidates: list[str] = []
        clean = folder.strip()
        if " " in clean or "\t" in clean or "(" in clean or ")" in clean:
            if not (clean.startswith('"') and clean.endswith('"')):
                candidates.append(f'"{clean}"')
            candidates.append(clean)
        else:
            candidates.append(clean)
            if not (clean.startswith('"') and clean.endswith('"')):
                candidates.append(f'"{clean}"')

        for candidate in candidates:
            try:
                status, _ = self._conn.select(candidate, readonly=readonly)
                if status == "OK":
                    return True
            except Exception:
                continue
        return False

    def list_remote_folders(self) -> list[Folder]:
        """Discover all selectable remote IMAP folders on the server."""
        if self._conn is None:
            raise RuntimeError("IMAPClient is not connected")
        status, data = self._conn.list()
        if status != "OK" or not data:
            return []

        folders: list[Folder] = []
        seen_names: set[str] = set()

        for item in data:
            parsed = parse_imap_list_response(item)
            if not parsed:
                continue
            flags_raw, delim, raw_name = parsed
            flags_lower = flags_raw.lower()

            # Skip non-selectable hierarchy containers
            if "\\noselect" in flags_lower or "\\nonexistent" in flags_lower:
                continue

            # Normalize INBOX capitalization
            name = "INBOX" if raw_name.upper() == "INBOX" else raw_name
            if name in seen_names:
                continue
            seen_names.add(name)

            parts = split_folder_hierarchy(name, delimiter=delim)
            if len(parts) == 1 and parts[0].upper() == "INBOX":
                display = "Inbox"
            else:
                display = parts[-1] if parts else name

            ftype = classify_folder_type(parts, flags_lower)
            folders.append(
                Folder(
                    id=f"{self.account.id}:{name}",
                    account_id=self.account.id,
                    name=name,
                    display_name=display or name,
                    folder_type=ftype,
                )
            )
        return folders

    def fetch_recent_messages(self, folder: str = "INBOX", limit: int = 50) -> list[EmailMessage]:
        """Fetch and parse the most recent messages from an IMAP mailbox folder."""
        if self._conn is None:
            raise RuntimeError("IMAPClient is not connected")
        if not self._select_mailbox(folder, readonly=True):
            return []

        status, search_data = self._conn.uid("SEARCH", "ALL")
        if status != "OK" or not search_data:
            status, search_data = self._conn.uid("SEARCH", None, "ALL")
        if status != "OK" or not search_data or not search_data[0]:
            return []

        raw_uids = search_data[0]
        if isinstance(raw_uids, bytes):
            uid_tokens = raw_uids.split()
        else:
            uid_tokens = str(raw_uids).encode("ascii", errors="ignore").split()

        uids = [int(u) for u in uid_tokens if u.isdigit()]
        if not uids:
            return []

        recent_uids = list(reversed(uids[-limit:]))
        messages: list[EmailMessage] = []

        # Fetch in batches of 10 UIDs to minimize round-trip latency while keeping memory bounded
        batch_size = 10
        for i in range(0, len(recent_uids), batch_size):
            batch = recent_uids[i : i + batch_size]
            uid_set = ",".join(str(u) for u in batch)
            try:
                f_status, f_data = self._conn.uid("FETCH", uid_set, "(UID FLAGS BODY.PEEK[])")
            except Exception:
                f_status, f_data = "ERR", []

            if f_status != "OK" or not f_data:
                # Fallback to individual UID fetch if batch fetch was rejected
                for single_uid in batch:
                    try:
                        s_status, s_data = self._conn.uid(
                            "FETCH", str(single_uid), "(UID FLAGS BODY.PEEK[])"
                        )
                        if s_status == "OK" and s_data:
                            self._extract_messages_from_fetch(
                                s_data, folder=folder, fallback_uid=single_uid, out=messages
                            )
                    except Exception:
                        continue
            else:
                self._extract_messages_from_fetch(
                    f_data, folder=folder, fallback_uid=batch[0], out=messages
                )

        return messages

    def _extract_messages_from_fetch(
        self,
        fetch_data: Sequence[Any],
        folder: str,
        fallback_uid: int,
        out: list[EmailMessage],
    ) -> None:
        """Extract and parse RFC 5322 messages from an imaplib FETCH response list."""
        for part in fetch_data:
            if not (isinstance(part, tuple) and len(part) >= 2):
                continue
            meta_bytes, raw_msg_bytes = part[0], part[1]
            if not isinstance(raw_msg_bytes, (bytes, bytearray)) or not raw_msg_bytes:
                continue
            meta_line = (
                meta_bytes.decode("utf-8", errors="replace")
                if isinstance(meta_bytes, (bytes, bytearray))
                else str(meta_bytes)
            )
            uid_match = re.search(r"UID\s+(\d+)", meta_line, re.IGNORECASE)
            msg_uid = int(uid_match.group(1)) if uid_match else fallback_uid

            flag_match = re.search(r"FLAGS\s*\(([^)]*)\)", meta_line, re.IGNORECASE)
            flags = flag_match.group(1).split() if flag_match else []

            try:
                parsed = parse_rfc5322_bytes(
                    raw_bytes=bytes(raw_msg_bytes),
                    account_id=self.account.id,
                    folder=folder,
                    uid=msg_uid,
                    imap_flags=flags,
                )
                out.append(parsed)
            except Exception:
                continue

    def set_flag(self, folder: str, uid: int, flag: str, enable: bool = True) -> bool:
        """Add or remove an IMAP flag (e.g. \\Seen, \\Flagged, \\Deleted) by message UID."""
        if self._conn is None or uid <= 0:
            return False
        if not self._select_mailbox(folder, readonly=False):
            return False
        cmd = "+FLAGS" if enable else "-FLAGS"
        res_status, _ = self._conn.uid("STORE", str(uid), cmd, f"({flag})")
        return res_status == "OK"

    def sync_to_store(
        self,
        store: LocalStore,
        folders: Sequence[str] | None = None,
        limit_per_folder: int = 50,
    ) -> int:
        """Discover remote IMAP folders and synchronize recent messages into LocalStore."""
        remote_folders: list[Folder] = []
        try:
            remote_folders = self.list_remote_folders()
        except Exception:
            remote_folders = []

        if remote_folders:
            store.sync_remote_folders(self.account.id, remote_folders)

        if folders is not None:
            target_folders = list(folders)
        elif remote_folders:
            # Put INBOX first, then top-level folders, then nested archive subfolders
            inbox_first = sorted(
                remote_folders,
                key=lambda f: (
                    0 if f.name.upper() == "INBOX" else 1,
                    len(f.path_parts),
                    f.name.lower(),
                ),
            )
            target_folders = [f.name for f in inbox_first]
        else:
            target_folders = ["INBOX", "Sent", "Drafts", "Archive", "Trash"]

        count = 0
        for folder_name in target_folders:
            try:
                fetched = self.fetch_recent_messages(folder=folder_name, limit=limit_per_folder)
                for msg in fetched:
                    store.save_message(msg)
                    count += 1
            except Exception:
                continue

        store.refresh_folder_counts()
        return count
