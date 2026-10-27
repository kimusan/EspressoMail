"""Formatted email reading pane renderer for HTML, Markdown, quotes, PGP banners, and attachments."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Sequence

from espresso.beans import CodeViewer, THEME_DRACULA, THEME_ESPRESSO
from espresso.crema import (
    ROUNDED_BORDER,
    Align,
    Style,
    string_width,
    truncate_ansi,
    wrap_ansi,
)

from espressomail.models.entities import (
    EmailMessage,
    EmailThread,
    PGPStatus,
)
from espressomail.themes.engine import Theme
from espressomail.ui.image_renderer import TerminalImageRenderer


class _HTMLToTerminalParser(HTMLParser):
    """Lightweight HTML-to-terminal text converter preserving structure and links."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self.links: list[str] = []
        self._in_script_or_style = False
        self._in_pre = False
        self._list_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "head"):
            self._in_script_or_style = True
            return
        if t in ("br", "hr"):
            self.chunks.append("\n")
        elif t in ("p", "div", "section", "article", "tr", "blockquote"):
            self.chunks.append("\n")
            if t == "blockquote":
                self.chunks.append("> ")
        elif t in ("h1", "h2", "h3", "h4"):
            level = int(t[1])
            self.chunks.append("\n" + ("#" * level) + " ")
        elif t in ("ul", "ol"):
            self._list_depth += 1
            self.chunks.append("\n")
        elif t == "li":
            indent = "  " * max(0, self._list_depth - 1)
            self.chunks.append(f"\n{indent}• ")
        elif t == "pre":
            self._in_pre = True
            self.chunks.append("\n```\n")
        elif t in ("b", "strong"):
            self.chunks.append("**")
        elif t in ("i", "em"):
            self.chunks.append("*")
        elif t == "code" and not self._in_pre:
            self.chunks.append("`")
        elif t == "a":
            href = dict(attrs).get("href")
            if href and href.startswith(("http://", "https://", "mailto:")):
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "head"):
            self._in_script_or_style = False
            return
        if t in ("p", "div", "h1", "h2", "h3", "h4", "blockquote"):
            self.chunks.append("\n")
        elif t in ("ul", "ol"):
            self._list_depth = max(0, self._list_depth - 1)
            self.chunks.append("\n")
        elif t == "pre":
            self._in_pre = False
            self.chunks.append("\n```\n")
        elif t in ("b", "strong"):
            self.chunks.append("**")
        elif t in ("i", "em"):
            self.chunks.append("*")
        elif t == "code" and not self._in_pre:
            self.chunks.append("`")
        elif t == "a" and self.links:
            idx = len(self.links)
            self.chunks.append(f" [{idx}]")

    def handle_data(self, data: str) -> None:
        if self._in_script_or_style:
            return
        if self._in_pre:
            self.chunks.append(data)
        else:
            collapsed = re.sub(r"[ \t\r\n]+", " ", data)
            self.chunks.append(collapsed)

    def get_text(self) -> str:
        raw = html.unescape("".join(self.chunks))
        lines = [line.strip() for line in raw.splitlines()]
        # Collapse 3+ consecutive blank lines
        cleaned: list[str] = []
        blank_run = 0
        for line in lines:
            if not line:
                blank_run += 1
                if blank_run <= 2:
                    cleaned.append("")
            else:
                blank_run = 0
                cleaned.append(line)
        if self.links:
            cleaned.append("")
            cleaned.append("Links:")
            for i, url in enumerate(self.links[:15], start=1):
                cleaned.append(f"  [{i}] {url}")
        return "\n".join(cleaned).strip()


def html_to_terminal_text(raw_html: str) -> str:
    """Convert HTML email content into clean Markdown-like terminal text."""
    if not raw_html.strip():
        return ""
    parser = _HTMLToTerminalParser()
    parser.feed(raw_html)
    return parser.get_text()


def format_email_body(
    text: str,
    theme: Theme,
    width: int,
) -> str:
    """Render an email plain-text/Markdown body with syntax highlighting, quotes, and inline styles."""
    wrap_w = max(24, width - 2)
    lines = text.splitlines()
    out_lines: list[str] = []

    in_code_block = False
    code_lang = "python"
    code_buffer: list[str] = []

    quote_styles = [
        Style().foreground(theme.quote_level_1).italic(True),
        Style().foreground(theme.quote_level_2).italic(True),
        Style().foreground(theme.quote_level_3).italic(True),
    ]

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lang = stripped[3:].strip() or "sh"
                code_buffer = []
            else:
                in_code_block = False
                code_str = "\n".join(code_buffer)
                syntax_theme = THEME_DRACULA if theme.id == "dracula" else THEME_ESPRESSO
                viewer = CodeViewer(
                    code=code_str,
                    filename=f"snippet.{code_lang}",
                    width=min(wrap_w, 72),
                    height=max(1, len(code_buffer)),
                    theme=syntax_theme,
                    show_line_numbers=True,
                )
                out_lines.append(viewer.view())
            continue

        if in_code_block:
            code_buffer.append(line)
            continue

        # Detect reply quote depth (`>`, `>>`, `>>>`)
        q_match = re.match(r"^(\s*>[\s>]*)(.*)$", line)
        if q_match:
            prefix_raw, content_raw = q_match.groups()
            depth = max(1, prefix_raw.count(">"))
            q_style = quote_styles[(depth - 1) % len(quote_styles)]
            bars = "│ " * min(4, depth)
            bar_prefix = q_style.bold(True).render(bars)
            avail = max(16, wrap_w - string_width(bars))
            wrapped_q = wrap_ansi(content_raw.strip(), width=avail)
            for qline in (wrapped_q.splitlines() or [""]):
                out_lines.append(f"{bar_prefix}{q_style.render(qline)}")
            continue

        # Headings (`# `, `## `, `### `)
        h_match = re.match(r"^(#{1,4})\s+(.*)$", line)
        if h_match:
            hashes, title_txt = h_match.groups()
            h_style = Style().bold(True).foreground(theme.accent_secondary)
            if len(hashes) == 1:
                h_style = h_style.underline(True)
            out_lines.append(h_style.render(title_txt))
            continue

        # Bullet lists
        b_match = re.match(r"^(\s*)([*•-])\s+(.*)$", line)
        if b_match:
            indent, _, item_txt = b_match.groups()
            bullet = Style().bold(True).foreground(theme.accent_primary).render(f"{indent}• ")
            styled_item = _apply_inline_formatting(item_txt, theme)
            wrapped_item = wrap_ansi(styled_item, width=max(16, wrap_w - len(indent) - 2))
            item_lines = wrapped_item.splitlines() or [""]
            out_lines.append(f"{bullet}{item_lines[0]}")
            pad = " " * (len(indent) + 2)
            for cont in item_lines[1:]:
                out_lines.append(f"{pad}{cont}")
            continue

        # Signature separator
        if line.rstrip() == "--" or stripped == "--":
            out_lines.append(Style().foreground(theme.fg_muted).render("─" * min(32, wrap_w)))
            continue

        styled_line = _apply_inline_formatting(line, theme)
        wrapped = wrap_ansi(styled_line, width=wrap_w)
        out_lines.extend(wrapped.splitlines() if wrapped else [""])

    return "\n".join(out_lines)


def _apply_inline_formatting(line: str, theme: Theme) -> str:
    """Highlight inline `code`, **bold**, and URLs within a text line."""
    # Inline `code`
    line = re.sub(
        r"`([^`]+)`",
        lambda m: Style().bold(True).foreground(theme.accent_secondary).render(m.group(1)),
        line,
    )
    # Inline **bold**
    line = re.sub(
        r"\*\*([^*]+)\*\*",
        lambda m: Style().bold(True).foreground(theme.fg_primary).render(m.group(1)),
        line,
    )
    # Highlight URLs
    line = re.sub(
        r"(https?://\S+)",
        lambda m: Style().underline(True).foreground(theme.attachment_fg).render(m.group(1)),
        line,
    )
    return line


def format_message_for_reading_pane(
    msg: EmailMessage,
    theme: Theme,
    width: int,
    image_renderer: TerminalImageRenderer,
    thread: EmailThread | None = None,
    show_raw_headers: bool = False,
) -> str:
    """Format a complete EmailMessage (or multi-message thread) for the Reading Pane viewport."""
    inner_w = max(30, width - 4)
    sections: list[str] = []

    # 1. Subject & Metadata Header Block
    lbl_style = Style().bold(True).foreground(theme.accent_primary)
    val_style = Style().foreground(theme.fg_primary)
    muted_style = Style().foreground(theme.fg_muted)

    star_badge = Style().foreground(theme.starred_fg).bold(True).render("★ ") if msg.is_starred else ""
    subj_rendered = (
        Style()
        .bold(True)
        .foreground(theme.accent_secondary)
        .render(wrap_ansi(msg.subject, width=inner_w))
    )
    sections.append(f"{star_badge}{subj_rendered}")

    header_rows = [
        f"{lbl_style.render('From:    ')} {val_style.render(msg.sender_display)}",
        f"{lbl_style.render('To:      ')} {val_style.render(', '.join(msg.recipients_to) or '(undisclosed)')}",
    ]
    if msg.recipients_cc:
        header_rows.append(
            f"{lbl_style.render('Cc:      ')} {val_style.render(', '.join(msg.recipients_cc))}"
        )
    header_rows.append(
        f"{lbl_style.render('Date:    ')} {muted_style.render(msg.date_iso or msg.short_date)}  "
        f"{muted_style.render(f'[{msg.account_id} / {msg.folder}]')}"
    )
    if thread and thread.message_count > 1:
        participants_str = " → ".join(thread.participants)
        header_rows.append(
            f"{lbl_style.render('Thread:  ')} "
            f"{Style().foreground(theme.unread_fg).render(f'{thread.message_count} messages')} "
            f"{muted_style.render(f'({participants_str})')}"
        )

    if show_raw_headers and msg.raw_headers:
        header_rows.append(muted_style.render("─" * min(inner_w, 50)))
        for hk, hv in list(msg.raw_headers.items())[:12]:
            header_rows.append(f"{muted_style.render(f'{hk}:')} {hv}")

    sections.append("\n".join(header_rows))

    # 2. Cryptographic PGP Status Banner
    if msg.pgp_status != PGPStatus.NONE:
        sections.append(_render_pgp_banner(msg, theme, inner_w))

    # 3. Attachments Bar
    if msg.attachments:
        att_chips: list[str] = []
        for idx, att in enumerate(msg.attachments, start=1):
            icon = "🖼️" if att.is_image else "📎"
            saved_mark = " ✔saved" if att.local_path else ""
            chip = (
                Style()
                .bold(True)
                .foreground(theme.attachment_fg)
                .render(f"[{idx}] {icon} {att.filename} ({att.human_size}{saved_mark})")
            )
            att_chips.append(chip)
        att_bar = (
            f"{lbl_style.render('Files:   ')} "
            + "  ".join(att_chips)
            + "\n"
            + muted_style.render(
                f"          [w] Save attachments to disk   [i] Image mode: {image_renderer.label}"
            )
        )
        sections.append(att_bar)

    sections.append(Style().foreground(theme.border_unfocused).render("━" * inner_w))

    # 4. Earlier messages in the conversation thread (if part of a multi-message thread)
    if thread and thread.message_count > 1:
        earlier = [m for m in thread.messages if m.id != msg.id and m.timestamp <= msg.timestamp]
        for prev in earlier:
            prev_hdr = (
                Style()
                .bold(True)
                .foreground(theme.fg_secondary)
                .render(f"╭─ 💬 {prev.sender_short} ({prev.short_date}) wrote:")
            )
            prev_body_raw = prev.body_text or html_to_terminal_text(prev.body_html)
            prev_formatted = format_email_body(prev_body_raw, theme, max(24, inner_w - 4))
            indented = "\n".join(
                f"{Style().foreground(theme.border_unfocused).render('│ ')}  {line}"
                for line in prev_formatted.splitlines()
            )
            sections.append(f"{prev_hdr}\n{indented}\n{Style().foreground(theme.border_unfocused).render('╰' + '─' * min(40, inner_w - 2))}")

    # 5. Primary Message Body
    body_source = msg.body_text.strip()
    if not body_source and msg.body_html:
        body_source = html_to_terminal_text(msg.body_html)
    sections.append(format_email_body(body_source or "(Empty message body)", theme, inner_w))

    # 6. Inline Terminal Image Previews for Image Attachments
    image_attachments = [a for a in msg.attachments if a.is_image]
    if image_attachments:
        sections.append("")
        for img_att in image_attachments:
            sections.append(
                image_renderer.render_attachment(
                    img_att,
                    theme=theme,
                    max_width=inner_w,
                    max_height=10,
                )
            )

    return "\n\n".join(sections)


def _render_pgp_banner(msg: EmailMessage, theme: Theme, width: int) -> str:
    """Render a color-coded OpenPGP verification/encryption banner."""
    fp_short = msg.pgp_key_fingerprint[-19:] if msg.pgp_key_fingerprint else "Unknown Key"
    signer = msg.pgp_signer or msg.sender_display

    if msg.pgp_status == PGPStatus.SIGNED_VERIFIED:
        color = theme.pgp_verified_fg
        badge = f"✔ PGP VERIFIED SIGNATURE — Signed by {signer} [{fp_short}]"
    elif msg.pgp_status == PGPStatus.DECRYPTED_VERIFIED:
        color = theme.pgp_verified_fg
        badge = f"🔒 PGP DECRYPTED & VERIFIED — Signed by {signer} [{fp_short}]"
    elif msg.pgp_status == PGPStatus.DECRYPTED:
        color = theme.pgp_verified_fg
        badge = "🔒 PGP DECRYPTED MESSAGE — Confidentiality verified"
    elif msg.pgp_status == PGPStatus.ENCRYPTED:
        color = theme.pgp_warning_fg
        badge = "🔐 PGP ENCRYPTED PAYLOAD — Press [x] to decrypt with local keyring"
    elif msg.pgp_status == PGPStatus.SIGNED_UNVERIFIED:
        color = theme.pgp_warning_fg
        badge = f"⚠ PGP SIGNATURE UNVERIFIED — Signer key [{fp_short}] not in trusted ring (Press [v] to verify)"
    else:
        color = theme.pgp_error_fg
        badge = f"✖ PGP INVALID SIGNATURE — Warning: Message may have been tampered with!"

    return (
        Style()
        .bold(True)
        .foreground(color)
        .render(truncate_ansi(badge, max(20, width)))
    )
