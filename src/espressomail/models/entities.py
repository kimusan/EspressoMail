"""Domain models and data structures for EspressoMail."""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence


class SecurityType(str, Enum):
    """Transport layer security mode for IMAP/SMTP/ManageSieve."""

    SSL_TLS = "ssl_tls"
    STARTTLS = "starttls"
    NONE = "none"


class FolderType(str, Enum):
    """Standard and virtual mailbox folder types."""

    UNIFIED = "unified"
    INBOX = "inbox"
    STARRED = "starred"
    SENT = "sent"
    DRAFTS = "drafts"
    ARCHIVE = "archive"
    TRASH = "trash"
    SPAM = "spam"
    CUSTOM = "custom"


class PGPStatus(str, Enum):
    """Cryptographic verification and encryption status of a message."""

    NONE = "none"
    SIGNED_VERIFIED = "signed_verified"
    SIGNED_UNVERIFIED = "signed_unverified"
    SIGNED_INVALID = "signed_invalid"
    ENCRYPTED = "encrypted"
    DECRYPTED = "decrypted"
    DECRYPTED_VERIFIED = "decrypted_verified"


def decode_imap_utf7(name: str) -> str:
    """Decode modified UTF-7 mailbox names (RFC 3501 Section 5.1.3) into Unicode."""
    if "&" not in name:
        return name
    out: list[str] = []
    i = 0
    n = len(name)
    while i < n:
        if name[i] == "&":
            end = name.find("-", i + 1)
            if end == -1:
                out.append(name[i:])
                break
            if end == i + 1:
                out.append("&")
            else:
                b64_part = name[i + 1 : end].replace(",", "/")
                pad = (4 - len(b64_part) % 4) % 4
                try:
                    raw = codecs.decode((b64_part + ("=" * pad)).encode("ascii"), "base64")
                    out.append(raw.decode("utf-16-be", errors="replace"))
                except Exception:
                    out.append(name[i : end + 1])
            i = end + 1
        else:
            out.append(name[i])
            i += 1
    return "".join(out)


def split_folder_hierarchy(raw_name: str, delimiter: str = "") -> list[str]:
    """Split an IMAP mailbox name into decoded hierarchy path segments.

    Strips personal namespace prefixes like 'INBOX.' / 'INBOX/' or '[Gmail]/'
    when nested segments follow, while preserving subfolder hierarchies
    such as 'Archives/2025/inbox' or 'INBOX.Archives.2025.Sent'.
    """
    decoded = decode_imap_utf7(raw_name.strip())
    if not decoded:
        return ["INBOX"]

    delim = delimiter
    if not delim:
        if "/" in decoded:
            delim = "/"
        elif "." in decoded:
            delim = "."
        else:
            delim = "/"

    parts = [p.strip() for p in decoded.split(delim) if p.strip()]
    if not parts:
        return [decoded]

    if len(parts) > 1 and parts[0].upper() in ("INBOX", "[GMAIL]", "[GOOGLE MAIL]"):
        parts = parts[1:]

    if len(parts) == 1 and parts[0].upper() == "INBOX":
        return ["INBOX"]

    return parts


def classify_folder_type(parts: Sequence[str], flags: str = "") -> FolderType:
    """Determine the FolderType for a mailbox given its hierarchy path segments and IMAP flags.

    Only top-level folders (len(parts) == 1) can be primary system mailboxes
    (INBOX, SENT, DRAFTS, TRASH, ARCHIVE, SPAM, STARRED); nested subfolders
    such as 'Archives/2025/Sent' or 'Archives/2025/inbox' are always FolderType.CUSTOM.
    """
    if not parts:
        return FolderType.CUSTOM
    if len(parts) > 1:
        return FolderType.CUSTOM

    leaf = parts[0].lower().strip()
    flags_l = flags.lower()
    if leaf == "inbox":
        return FolderType.INBOX
    if "\\sent" in flags_l or leaf in ("sent", "sent items", "sent messages", "sent mail"):
        return FolderType.SENT
    if "\\drafts" in flags_l or leaf in ("draft", "drafts"):
        return FolderType.DRAFTS
    if "\\trash" in flags_l or leaf in ("trash", "deleted", "deleted items", "deleted messages", "bin"):
        return FolderType.TRASH
    if "\\archive" in flags_l or "\\all" in flags_l or leaf in ("archive", "archives", "all mail"):
        return FolderType.ARCHIVE
    if "\\junk" in flags_l or leaf in ("spam", "junk", "junk e-mail", "junk email", "bulk mail"):
        return FolderType.SPAM
    if "\\flagged" in flags_l or leaf in ("starred", "flagged"):
        return FolderType.STARRED
    return FolderType.CUSTOM


@dataclass
class AccountConfig:
    """Configuration for an email account (IMAP + SMTP + optional ManageSieve & PGP)."""

    id: str
    name: str
    email: str
    full_name: str
    imap_host: str
    imap_port: int = 993
    imap_security: SecurityType = SecurityType.SSL_TLS
    imap_username: str = ""
    imap_password: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: SecurityType = SecurityType.STARTTLS
    smtp_username: str = ""
    smtp_password: str = ""
    sieve_host: str = ""
    sieve_port: int = 4190
    pgp_key_id: str = ""
    signature: str = ""
    color_badge: str = "#D4A373"
    enabled: bool = True
    carddav_url: str = ""
    carddav_username: str = ""
    carddav_password: str = ""

    def __post_init__(self) -> None:
        if not self.imap_username:
            self.imap_username = self.email
        if not self.smtp_username:
            self.smtp_username = self.email
        if not self.sieve_host and self.imap_host:
            self.sieve_host = self.imap_host
        if not self.carddav_username:
            self.carddav_username = self.email
        if isinstance(self.imap_security, str):
            self.imap_security = SecurityType(self.imap_security)
        if isinstance(self.smtp_security, str):
            self.smtp_security = SecurityType(self.smtp_security)

    def to_dict(self) -> dict[str, Any]:
        """Serialize account configuration to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "full_name": self.full_name,
            "imap_host": self.imap_host,
            "imap_port": self.imap_port,
            "imap_security": self.imap_security.value,
            "imap_username": self.imap_username,
            "imap_password": self.imap_password,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_security": self.smtp_security.value,
            "smtp_username": self.smtp_username,
            "smtp_password": self.smtp_password,
            "sieve_host": self.sieve_host,
            "sieve_port": self.sieve_port,
            "pgp_key_id": self.pgp_key_id,
            "signature": self.signature,
            "color_badge": self.color_badge,
            "enabled": self.enabled,
            "carddav_url": self.carddav_url,
            "carddav_username": self.carddav_username,
            "carddav_password": self.carddav_password,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccountConfig:
        """Deserialize account configuration from dictionary."""
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", data["email"])),
            email=str(data["email"]),
            full_name=str(data.get("full_name", data["email"])),
            imap_host=str(data.get("imap_host", "")),
            imap_port=int(data.get("imap_port", 993)),
            imap_security=SecurityType(data.get("imap_security", "ssl_tls")),
            imap_username=str(data.get("imap_username", data["email"])),
            imap_password=str(data.get("imap_password", "")),
            smtp_host=str(data.get("smtp_host", "")),
            smtp_port=int(data.get("smtp_port", 587)),
            smtp_security=SecurityType(data.get("smtp_security", "starttls")),
            smtp_username=str(data.get("smtp_username", data["email"])),
            smtp_password=str(data.get("smtp_password", "")),
            sieve_host=str(data.get("sieve_host", "")),
            sieve_port=int(data.get("sieve_port", 4190)),
            pgp_key_id=str(data.get("pgp_key_id", "")),
            signature=str(data.get("signature", "")),
            color_badge=str(data.get("color_badge", "#D4A373")),
            enabled=bool(data.get("enabled", True)),
            carddav_url=str(data.get("carddav_url", "")),
            carddav_username=str(data.get("carddav_username", "")),
            carddav_password=str(data.get("carddav_password", "")),
        )


@dataclass
class Folder:
    """Represents a mailbox folder (IMAP folder or virtual Unified folder)."""

    id: str
    account_id: str
    name: str
    display_name: str
    folder_type: FolderType = FolderType.CUSTOM
    unread_count: int = 0
    total_count: int = 0
    uid_validity: int = 0
    last_uid: int = 0
    selectable: bool = True
    subscribed: bool = True

    @property
    def path_parts(self) -> tuple[str, ...]:
        """Return decoded hierarchy path segments for this folder."""
        return tuple(split_folder_hierarchy(self.name))

    @property
    def icon(self) -> str:
        """Return an icon representing the folder type."""
        icons = {
            FolderType.UNIFIED: "📥",
            FolderType.INBOX: "📨",
            FolderType.STARRED: "⭐",
            FolderType.SENT: "📤",
            FolderType.DRAFTS: "📝",
            FolderType.ARCHIVE: "📦",
            FolderType.TRASH: "🗑️",
            FolderType.SPAM: "🚫",
            FolderType.CUSTOM: "📁",
        }
        return icons.get(self.folder_type, "📁")


def detect_image_mime_type(data: bytes | None) -> str:
    """Inspect binary magic bytes and return the image MIME type if recognized."""
    if not data or len(data) < 4:
        return ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith((b"P6", b"P3")):
        return "image/x-portable-pixmap"
    return ""


@dataclass
class Attachment:
    """Represents a MIME attachment on an incoming or outgoing email."""

    id: str
    message_id: str
    filename: str
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    content_id: str = ""
    is_inline: bool = False
    data: bytes | None = None
    local_path: str = ""

    @property
    def is_image(self) -> bool:
        """Return True if this attachment is an image."""
        ct = self.content_type.lower()
        ext = self.filename.lower()
        if ct.startswith("image/") or ext.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ppm", ".webp", ".sixel", ".kitty")
        ):
            return True
        return bool(detect_image_mime_type(self.data))

    @property
    def human_size(self) -> str:
        """Format size in human-readable units."""
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024.0 or unit == "GB":
                return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{int(self.size_bytes)} B"


@dataclass
class EmailMessage:
    """Represents an email message stored locally or synchronized via IMAP."""

    id: str
    account_id: str
    folder: str = "INBOX"
    uid: int = 0
    message_id: str = ""
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    thread_id: str = ""
    subject: str = "(No Subject)"
    sender_name: str = ""
    sender_email: str = ""
    recipients_to: list[str] = field(default_factory=list)
    recipients_cc: list[str] = field(default_factory=list)
    recipients_bcc: list[str] = field(default_factory=list)
    date_iso: str = ""
    timestamp: float = 0.0
    snippet: str = ""
    body_text: str = ""
    body_html: str = ""
    raw_headers: dict[str, str] = field(default_factory=dict)
    is_read: bool = False
    is_starred: bool = False
    is_draft: bool = False
    is_trashed: bool = False
    is_answered: bool = False
    pgp_status: PGPStatus = PGPStatus.NONE
    pgp_key_fingerprint: str = ""
    pgp_signer: str = ""
    pgp_raw_payload: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.pgp_status, str):
            self.pgp_status = PGPStatus(self.pgp_status)
        if not self.timestamp and self.date_iso:
            try:
                dt = datetime.fromisoformat(self.date_iso.replace("Z", "+00:00"))
                self.timestamp = dt.timestamp()
            except ValueError:
                self.timestamp = 0.0
        if not self.date_iso and self.timestamp:
            dt = datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
            self.date_iso = dt.isoformat()
        if not self.snippet and self.body_text:
            clean = " ".join(self.body_text.split())
            self.snippet = clean[:140]

    @property
    def sender_display(self) -> str:
        """Return formatted sender name or email."""
        if self.sender_name and self.sender_email:
            return f"{self.sender_name} <{self.sender_email}>"
        return self.sender_name or self.sender_email or "Unknown Sender"

    @property
    def sender_short(self) -> str:
        """Return compact sender label for list views."""
        return self.sender_name or self.sender_email or "Unknown"

    @property
    def short_date(self) -> str:
        """Format timestamp as compact human-readable date/time."""
        if not self.timestamp:
            return self.date_iso[:10] if self.date_iso else ""
        dt = datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        if dt.year == now.year:
            return dt.strftime("%d %b")
        return dt.strftime("%Y-%m-%d")

    @property
    def has_attachments(self) -> bool:
        """Return True if message has one or more attachments."""
        return len(self.attachments) > 0


@dataclass
class ThreadNode:
    """A node in a hierarchical conversation thread tree."""

    message: EmailMessage
    depth: int = 0
    children: list[ThreadNode] = field(default_factory=list)


@dataclass
class EmailThread:
    """Aggregated conversation thread containing one or more messages."""

    thread_id: str
    subject: str
    messages: list[EmailMessage] = field(default_factory=list)
    nodes: list[ThreadNode] = field(default_factory=list)
    expanded: bool = True

    @property
    def latest_message(self) -> EmailMessage:
        """Return the most recent message in the thread."""
        return max(self.messages, key=lambda m: m.timestamp) if self.messages else EmailMessage(id="", account_id="")

    @property
    def root_message(self) -> EmailMessage:
        """Return the earliest message in the thread."""
        return min(self.messages, key=lambda m: m.timestamp) if self.messages else EmailMessage(id="", account_id="")

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def unread_count(self) -> int:
        return sum(1 for m in self.messages if not m.is_read)

    @property
    def is_unread(self) -> bool:
        return self.unread_count > 0

    @property
    def is_starred(self) -> bool:
        return any(m.is_starred for m in self.messages)

    @property
    def has_attachments(self) -> bool:
        return any(m.has_attachments for m in self.messages)

    @property
    def has_pgp(self) -> bool:
        return any(m.pgp_status != PGPStatus.NONE for m in self.messages)

    @property
    def participants(self) -> list[str]:
        """Return deduplicated list of short sender names in chronological order."""
        seen: list[str] = []
        for m in sorted(self.messages, key=lambda msg: msg.timestamp):
            name = m.sender_short
            if name not in seen:
                seen.append(name)
        return seen


@dataclass
class Contact:
    """Address book contact entry."""

    id: str
    name: str
    email: str
    organization: str = ""
    notes: str = ""
    pgp_fingerprint: str = ""
    favorite: bool = False
    frequency: int = 0

    @property
    def formatted(self) -> str:
        """Return RFC 5322 formatted address string."""
        if self.name:
            return f"{self.name} <{self.email}>"
        return self.email


@dataclass
class SieveFilterRule:
    """Structured representation of a Sieve email filtering rule."""

    id: str
    account_id: str
    name: str
    enabled: bool = True
    match_all: bool = True  # allof vs anyof
    field: str = "From"  # From, To, Subject, List-Id, X-Spam-Flag, Size
    comparator: str = "contains"  # contains, is, matches, over, under
    pattern: str = ""
    action: str = "fileinto"  # fileinto, redirect, keep, discard, flag, reject
    action_arg: str = "Archive"
    stop_processing: bool = True


@dataclass
class SieveScript:
    """A ManageSieve script with structured rules and compiled Sieve source."""

    id: str
    account_id: str
    name: str
    content: str
    active: bool = True
    updated_at: str = ""
