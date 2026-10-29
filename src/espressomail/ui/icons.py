"""Icon and emoji presentation manager for EspressoMail.

Provides support for three icon presentation modes:
- 'emoji': Colorful graphical Unicode emojis (with VS16 \\uFE0F where applicable).
- 'unicode': Theme-adaptive monochrome Unicode text glyphs (dingbats, geometric shapes, VS15 \\uFE0E).
- 'none': Plain text only (clean minimalism, no emojis or graphical icons).
"""

from __future__ import annotations

from typing import Literal

IconMode = Literal["emoji", "unicode", "none"]

ICON_MODES: tuple[IconMode, ...] = ("emoji", "unicode", "none")

ICON_MODE_LABELS: dict[str, str] = {
    "emoji": "Emoji (Color)",
    "unicode": "Unicode (Monochrome)",
    "none": "None (Plain Text)",
}


def normalize_icon_mode(mode: str | None) -> IconMode:
    """Validate and return normalized IconMode, defaulting to 'emoji'."""
    if not mode:
        return "emoji"
    clean = mode.strip().lower()
    if clean in ICON_MODES:
        return clean  # type: ignore[return-value]
    return "emoji"


def get_folder_icon(
    folder_type_name: str,
    mode: str = "emoji",
    is_expanded: bool = False,
    has_children: bool = False,
) -> str:
    """Return the folder prefix icon according to the active icon mode."""
    m = normalize_icon_mode(mode)
    if m == "none":
        return ""

    ft = folder_type_name.upper()

    if m == "emoji":
        if ft in ("UNIFIED", "UNIFIED_INBOX"):
            return "📥\ufe0f"
        if ft in ("UNREAD", "ALL_UNREAD", "UNIFIED_UNREAD"):
            return "✉️"
        if ft in ("INBOX",):
            return "📨\ufe0f"
        if ft in ("STARRED", "FLAGGED"):
            return "⭐\ufe0f"
        if ft in ("DRAFTS",):
            return "📝\ufe0f"
        if ft in ("SENT", "SENT_MAIL"):
            return "📤\ufe0f"
        if ft in ("ARCHIVE", "ALL"):
            return "📦\ufe0f"
        if ft in ("TRASH", "DELETED"):
            return "🗑\ufe0f"
        if ft in ("SPAM", "JUNK"):
            return "🚫\ufe0f"
        if ft in ("ACCOUNT", "ROOT"):
            return "☕\ufe0f"
        # Custom or regular folder
        if has_children:
            return "📂\ufe0f" if is_expanded else "🗂️"
        return "📁\ufe0f"

    # Unicode monochrome mode: clean geometric and dingbat symbols
    if ft in ("UNIFIED", "UNIFIED_INBOX"):
        return "⤓ "
    if ft in ("UNREAD", "ALL_UNREAD", "UNIFIED_UNREAD"):
        return "✉ "
    if ft in ("INBOX",):
        return "▼ "
    if ft in ("STARRED", "FLAGGED"):
        return "★ "
    if ft in ("DRAFTS",):
        return "✎ "
    if ft in ("SENT", "SENT_MAIL"):
        return "▲ "
    if ft in ("ARCHIVE", "ALL"):
        return "⌸ "
    if ft in ("TRASH", "DELETED"):
        return "✕ "
    if ft in ("SPAM", "JUNK"):
        return "⊘ "
    if ft in ("ACCOUNT", "ROOT"):
        return "▪ "
    if has_children:
        return "⊟ " if is_expanded else "⊞ "
    return "▸ "


def get_pane_title(pane: str, mode: str = "emoji", extra: str = "") -> str:
    """Return the pane header title formatted according to active icon mode."""
    m = normalize_icon_mode(mode)
    p = pane.lower().strip()
    extra_str = f" {extra}".rstrip() if extra else ""

    if p in ("folders", "mailboxes"):
        if m == "emoji":
            return f"📁\ufe0f Mailboxes{extra_str}"
        if m == "unicode":
            return f"▪ Mailboxes{extra_str}"
        return f"Mailboxes{extra_str}"

    if p in ("messages", "list", "messagelist"):
        if m == "emoji":
            return f"✉\ufe0f Message List{extra_str}"
        if m == "unicode":
            return f"✉ Message List{extra_str}"
        return f"Message List{extra_str}"

    if p in ("reader", "readingpane"):
        if m == "emoji":
            return f"📖\ufe0f Reading Pane{extra_str}"
        if m == "unicode":
            return f"≡ Reading Pane{extra_str}"
        return f"Reading Pane{extra_str}"

    return pane


def get_brand_label(mode: str = "emoji") -> str:
    """Return top menu bar brand prefix."""
    m = normalize_icon_mode(mode)
    if m == "emoji":
        return " ☕\ufe0f EspressoMail │ "
    if m == "unicode":
        return " ☕︎ EspressoMail │ "
    return " EspressoMail │ "


def get_badge(badge_type: str, mode: str = "emoji") -> str:
    """Return status badges (attachments, PGP, unsubscribed, star, unread)."""
    m = normalize_icon_mode(mode)
    b = badge_type.lower().strip()

    if b == "attachment":
        if m == "emoji":
            return "📎\ufe0f "
        if m == "unicode":
            return "@ "
        return "@ "

    if b == "pgp":
        if m == "emoji":
            return "🔒\ufe0f "
        if m == "unicode":
            return "§ "
        return "P "

    if b == "unsubscribed":
        if m == "emoji":
            return " 🔕\ufe0f"
        if m == "unicode":
            return " ~"
        return " [unsub]"

    if b == "star":
        if m == "emoji":
            return "★ "
        if m == "unicode":
            return "★ "
        return "* "

    if b == "unread":
        if m == "emoji":
            return "● "
        if m == "unicode":
            return "● "
        return "* "

    return ""


def get_modal_title(modal_id: str, mode: str = "emoji", extra: str = "") -> str:
    """Return formatted modal window title string according to icon mode."""
    m = normalize_icon_mode(mode)
    mid = modal_id.lower().strip()
    extra_str = f" {extra}".rstrip() if extra else ""

    titles = {
        "compose": ("✉️ Compose Email", "✉ Compose Email", "Compose Email"),
        "search": (
            "🔍 Instant Full-Text & Fuzzy Database Search",
            "⌕ Instant Full-Text & Fuzzy Database Search",
            "Instant Full-Text & Fuzzy Database Search",
        ),
        "address_book": ("📇 Local Address Book", "≡ Local Address Book", "Local Address Book"),
        "sieve": (
            "⚙️ ManageSieve & Filter Rules",
            "⚙ ManageSieve & Filter Rules",
            "ManageSieve & Filter Rules",
        ),
        "pgp": (
            "🔐\ufe0f OpenPGP / GnuPG Keyring Manager",
            "§ OpenPGP / GnuPG Keyring Manager",
            "OpenPGP / GnuPG Keyring Manager",
        ),
        "account_settings": (
            "⚙️ Account Settings & Credentials",
            "⚙ Account Settings & Credentials",
            "Account Settings & Credentials",
        ),
        "preferences": (
            "⚙️ Preferences & Configuration",
            "⚙ Preferences & Configuration",
            "Preferences & Configuration",
        ),
        "confirm_exit": ("🚪 Confirm Exit", "✕ Confirm Exit", "Confirm Exit"),
        "download_attachments": (
            "💾 Download Attachments",
            "⤓ Download Attachments",
            "Download Attachments",
        ),
        "rsvp": (
            "📅\ufe0f RSVP to Calendar Invitation",
            "📅 RSVP to Calendar Invitation",
            "RSVP to Calendar Invitation",
        ),
        "move_folder": (
            "📁\ufe0f Move Message to Folder",
            "▸ Move Message to Folder",
            "Move Message to Folder",
        ),
        "preview": (
            "👁 Markdown Live Preview (F4 to edit)",
            "≡ Markdown Live Preview (F4 to edit)",
            "Markdown Live Preview (F4 to edit)",
        ),
    }

    if mid in titles:
        t_emoji, t_unicode, t_none = titles[mid]
        base = t_emoji if m == "emoji" else (t_unicode if m == "unicode" else t_none)
        return f"{base}{extra_str}"

    return f"{modal_id}{extra_str}"
