"""Formatted email reading pane renderer for HTML, Markdown, quotes, PGP banners, and images."""

from __future__ import annotations

import hashlib
import html
import re
from html.parser import HTMLParser

from espresso.beans import CodeViewer, THEME_DRACULA, THEME_ESPRESSO
from espresso.crema import (
    ROUNDED_BORDER,
    Style,
    string_width,
    truncate_ansi,
    wrap_ansi,
)

from espressomail.models.entities import (
    Attachment,
    EmailMessage,
    EmailThread,
    PGPStatus,
)
from espressomail.protocols.icalendar import CalendarEvent, find_calendar_event
from espressomail.themes.engine import Theme
from espressomail.ui.image_renderer import (
    ImageRenderBackend,
    TerminalImageRenderer,
    is_likely_tracking_pixel_url,
    resolve_html_image_src,
)


class _HTMLToTerminalParser(HTMLParser):
    """Lightweight HTML-to-terminal text converter preserving structure, links, and <img> tags."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self.links: list[str] = []
        self.images: list[tuple[str, str]] = []  # (src, alt)
        self._seen_img_srcs: set[str] = set()
        self._in_script_or_style = False
        self._in_pre = False
        self._list_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "head"):
            self._in_script_or_style = True
            return
        if t == "img":
            attr_map = {k.lower(): (v or "") for k, v in attrs}
            src = attr_map.get("src", "").strip()
            alt = (attr_map.get("alt") or attr_map.get("title") or "").strip()
            w_attr = attr_map.get("width", "")
            h_attr = attr_map.get("height", "")
            if src and not is_likely_tracking_pixel_url(src, w_attr, h_attr):
                if src not in self._seen_img_srcs and len(self.images) < 8:
                    self._seen_img_srcs.add(src)
                    self.images.append((src, alt))
                    img_idx = len(self.images)
                    self.chunks.append(f"\n[[IMAGE:{img_idx}]]\n")
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

    def get_text(self, include_image_markers: bool = False) -> str:
        raw = html.unescape("".join(self.chunks))
        if not include_image_markers:
            raw = re.sub(r"\[\[IMAGE:\d+\]\]", "", raw)
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


def html_to_terminal_text(raw_html: str, include_image_markers: bool = False) -> str:
    """Convert HTML email content into clean Markdown-like terminal text."""
    if not raw_html.strip():
        return ""
    parser = _HTMLToTerminalParser()
    try:
        parser.feed(raw_html)
    except Exception:
        pass
    return parser.get_text(include_image_markers=include_image_markers)


def extract_html_images(raw_html: str) -> list[tuple[str, str]]:
    """Extract non-tracking `(src, alt)` image references from an HTML email body."""
    if not raw_html or not raw_html.strip():
        return []
    parser = _HTMLToTerminalParser()
    try:
        parser.feed(raw_html)
    except Exception:
        pass
    return parser.images


def _collect_message_images(
    msg: EmailMessage,
    allow_remote_fetch: bool = True,
    allow_blocking_fetch: bool = False,
) -> list[Attachment]:
    """Gather all renderable images from MIME attachments and HTML `<img>` tags (`cid:`, `data:`, `http(s):`)."""
    collected: list[Attachment] = []
    seen_hashes: set[str] = set()
    seen_ids: set[str] = set()

    def _add_att(att: Attachment | None) -> None:
        if att is None or not att.is_image:
            return
        if att.id and att.id in seen_ids:
            return
        if att.data:
            digest = hashlib.sha256(att.data).hexdigest()
            if digest in seen_hashes:
                return
            seen_hashes.add(digest)
        if att.id:
            seen_ids.add(att.id)
        collected.append(att)

    # 1. Resolve HTML <img> tags in reading order first (cid:, data:image/..., https://...)
    if msg.body_html:
        html_imgs = extract_html_images(msg.body_html)
        for idx, (src, alt) in enumerate(html_imgs, start=1):
            resolved = resolve_html_image_src(
                src=src,
                alt=alt,
                attachments=msg.attachments,
                message_id=msg.id,
                idx=idx,
                allow_remote_fetch=allow_remote_fetch,
                allow_blocking_fetch=allow_blocking_fetch,
            )
            _add_att(resolved)

    # 2. Add any remaining MIME image attachments not already referenced via HTML
    for att in msg.attachments:
        if att.is_image:
            _add_att(att)

    # 3. Check plain text body for direct image URLs (e.g. https://example.com/photo.png)
    if not collected and msg.body_text:
        url_matches = re.findall(
            r"(https?://[^\s<>()\"']+\.(?:png|jpe?g|gif|webp|bmp)(?:\?[^\s<>()\"']*)?)",
            msg.body_text,
            flags=re.IGNORECASE,
        )
        for idx, url in enumerate(url_matches[:4], start=1):
            if not is_likely_tracking_pixel_url(url):
                resolved = resolve_html_image_src(
                    src=url,
                    alt="",
                    attachments=msg.attachments,
                    message_id=msg.id,
                    idx=idx,
                    allow_remote_fetch=allow_remote_fetch,
                    allow_blocking_fetch=allow_blocking_fetch,
                )
                _add_att(resolved)

    return collected


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

        # Image marker pass-through (preserved for inline placement)
        if re.match(r"^\[\[IMAGE:\d+\]\]$", stripped):
            out_lines.append(stripped)
            continue

        styled_line = _apply_inline_formatting(line, theme)
        wrapped = wrap_ansi(styled_line, width=wrap_w)
        out_lines.extend(wrapped.splitlines() if wrapped else [""])

    return "\n".join(out_lines)


def osc8_link(url: str, text: str) -> str:
    """Wrap text in an OSC 8 terminal hyperlink sequence."""
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def _apply_inline_formatting(line: str, theme: Theme) -> str:
    """Highlight inline `code`, **bold**, and clickable OSC 8 URLs within a text line."""
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
    # Markdown links [text](url) -> clickable OSC 8 hyperlink
    def _md_link(m: re.Match) -> str:
        label = m.group(1)
        url = m.group(2)
        styled_label = Style().underline(True).foreground(theme.attachment_fg).render(label)
        return osc8_link(url, styled_label)

    line = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+|mailto:[^\s\)]+)\)", _md_link, line)

    # Bare URLs -> clickable OSC 8 hyperlink
    def _url_replacer(m: re.Match) -> str:
        url = m.group(1)
        trail = ""
        while url and url[-1] in ".,;:!?)>'\"]":
            trail = url[-1] + trail
            url = url[:-1]
        styled = Style().underline(True).foreground(theme.attachment_fg).render(url)
        return osc8_link(url, styled) + trail

    line = re.sub(r"(?<!\x1b\]8;;)(https?://[^\s<>\"']+|mailto:[^\s<>\"']+)", _url_replacer, line)
    return line


def format_message_for_reading_pane(
    msg: EmailMessage,
    theme: Theme,
    width: int,
    image_renderer: TerminalImageRenderer,
    thread: EmailThread | None = None,
    show_raw_headers: bool = False,
    account: AccountConfig | None = None,
    allow_blocking_fetch: bool = False,
    icon_mode: str = "emoji",
) -> str:
    """Format a complete EmailMessage (or multi-message thread) for the Reading Pane viewport."""
    from espressomail.ui.icons import get_badge, normalize_icon_mode

    m_mode = normalize_icon_mode(icon_mode)
    inner_w = max(30, width - 4)
    sections: list[str] = []

    # Collect all MIME and inline HTML images (`cid:`, `data:image/...`, `https://...`)
    all_images = _collect_message_images(
        msg,
        allow_remote_fetch=(image_renderer.backend != ImageRenderBackend.OFF),
        allow_blocking_fetch=allow_blocking_fetch,
    )
    # Also count HTML <img> tags when backend is OFF so the user still sees that images exist
    html_img_tags = extract_html_images(msg.body_html) if msg.body_html else []

    # 1. Subject & Metadata Header Block
    lbl_style = Style().bold(True).foreground(theme.accent_primary)
    val_style = Style().foreground(theme.fg_primary)
    muted_style = Style().foreground(theme.fg_muted)

    star_badge = (
        Style().foreground(theme.starred_fg).bold(True).render(get_badge("star", m_mode))
        if msg.is_starred
        else ""
    )
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

    acc_name = account.name if account else msg.account_id
    acc_color = (account.color_badge if (account and account.color_badge) else theme.fg_muted)
    acc_badge = Style().foreground(acc_color).bold(True).render(f"● {acc_name}")
    header_rows.append(
        f"{lbl_style.render('Date:    ')} {muted_style.render(msg.date_iso or msg.short_date)}  "
        f"{acc_badge} {muted_style.render(f'[{msg.folder}]')}"
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

    sections.append("\n".join(truncate_ansi(row, inner_w) for row in header_rows))

    # 2. Calendar Event / Invitation Banner
    cal_event = find_calendar_event(msg)
    if cal_event:
        sections.append(_render_calendar_banner(cal_event, theme, inner_w, icon_mode=m_mode))

    # 3. Cryptographic PGP Status Banner
    if msg.pgp_status != PGPStatus.NONE:
        sections.append(_render_pgp_banner(msg, theme, inner_w, icon_mode=m_mode))

    # 4. Attachments & Images Bar
    display_attachments = list(msg.attachments)
    existing_att_ids = {a.id for a in display_attachments}
    for img_att in all_images:
        if img_att.id not in existing_att_ids:
            display_attachments.append(img_att)
            existing_att_ids.add(img_att.id)

    if display_attachments or html_img_tags:
        att_chips: list[str] = []
        for idx, att in enumerate(display_attachments, start=1):
            is_ics = att.filename.lower().endswith(".ics") or "calendar" in att.content_type.lower()
            if m_mode == "emoji":
                icon = "🖼️" if att.is_image else ("📅\ufe0f" if is_ics else "📎\ufe0f")
            elif m_mode == "unicode":
                icon = "▣" if att.is_image else ("📅" if is_ics else "@")
            else:
                icon = "[img]" if att.is_image else ("[cal]" if is_ics else "@")
            saved_mark = " ✔saved" if att.local_path else ""
            chip = (
                Style()
                .bold(True)
                .foreground(theme.attachment_fg)
                .render(f"[{idx}] {icon} {att.filename} ({att.human_size}{saved_mark})")
            )
            att_chips.append(chip)
        if not att_chips and html_img_tags:
            img_prefix = "🖼️ " if m_mode == "emoji" else ("▣ " if m_mode == "unicode" else "")
            chip = (
                Style()
                .bold(True)
                .foreground(theme.attachment_fg)
                .render(f"{img_prefix}{len(html_img_tags)} embedded HTML image(s)")
            )
            att_chips.append(chip)
        media_line = f"{lbl_style.render('Media:   ')} " + "  ".join(att_chips)
        actions_line = muted_style.render(
            f"          [w] Save attachments to disk   [i] Image mode: {image_renderer.label}"
        )
        att_bar = f"{truncate_ansi(media_line, inner_w)}\n{truncate_ansi(actions_line, inner_w)}"
        sections.append(att_bar)

    sections.append(Style().foreground(theme.border_unfocused).render("━" * inner_w))

    # 4. Map HTML inline images to [[IMAGE:{idx}]] markers
    rendered_marker_blocks: dict[str, str] = {}
    used_image_ids: set[str] = set()

    for idx, (src, alt) in enumerate(html_img_tags, start=1):
        marker = f"[[IMAGE:{idx}]]"
        target_att = None
        for img in all_images:
            if (
                img.id in (
                    f"{msg.id}:web_img:{idx}",
                    f"{msg.id}:web_img_pending:{idx}",
                    f"{msg.id}:data_img:{idx}",
                    f"html_img_{msg.id}_{idx}",
                )
                or (img.filename and alt and alt in img.filename)
            ):
                target_att = img
                break
        if target_att is None and idx - 1 < len(all_images):
            target_att = all_images[idx - 1]

        if target_att:
            used_image_ids.add(target_att.id)
            box = image_renderer.render_attachment(
                target_att,
                theme=theme,
                max_width=inner_w,
                max_height=12,
                skip_tiny_trackers=True,
            )
            if box:
                rendered_marker_blocks[marker] = box
        elif image_renderer.backend == ImageRenderBackend.OFF:
            rendered_marker_blocks[marker] = (
                Style()
                .foreground(theme.fg_muted)
                .italic(True)
                .render(
                    f"  [Image preview disabled ({alt or f'Image {idx}'}) — press 'i' to enable]"
                )
            )

    # 5. Earlier messages in the conversation thread (if part of a multi-message thread)
    if thread and thread.message_count > 1:
        earlier = [m for m in thread.messages if m.id != msg.id and m.timestamp <= msg.timestamp]
        for prev in earlier:
            q_icon = "💬\ufe0f " if m_mode == "emoji" else ("» " if m_mode == "unicode" else "")
            prev_hdr = (
                Style()
                .bold(True)
                .foreground(theme.fg_secondary)
                .render(f"╭─ {q_icon}{prev.sender_short} ({prev.short_date}) wrote:")
            )
            prev_body_raw = prev.body_text or html_to_terminal_text(prev.body_html)
            prev_formatted = format_email_body(prev_body_raw, theme, max(24, inner_w - 4))
            indented = "\n".join(
                f"{Style().foreground(theme.border_unfocused).render('│ ')}  {line}"
                for line in prev_formatted.splitlines()
            )
            sections.append(
                f"{prev_hdr}\n{indented}\n"
                f"{Style().foreground(theme.border_unfocused).render('╰' + '─' * min(40, inner_w - 2))}"
            )

    # 6. Primary Message Body (images rendered inline in the flow)
    if msg.body_html:
        body_source = html_to_terminal_text(msg.body_html, include_image_markers=True)
    else:
        body_source = msg.body_text.strip()

    body_formatted = format_email_body(body_source or "(Empty message body)", theme, inner_w)
    for marker, box in rendered_marker_blocks.items():
        if marker in body_formatted:
            body_formatted = body_formatted.replace(marker, f"\n{box}\n")
    body_formatted = re.sub(r"\n*\[\[IMAGE:\d+\]\]\n*", "\n", body_formatted)
    sections.append(body_formatted)

    # 7. Leftover image attachments not placed inline in the text
    leftover_images = [img for img in all_images if img.id not in used_image_ids]
    if leftover_images:
        leftover_boxes: list[str] = []
        for img in leftover_images:
            box = image_renderer.render_attachment(
                img,
                theme=theme,
                max_width=inner_w,
                max_height=12,
                skip_tiny_trackers=True,
            )
            if box:
                leftover_boxes.append(box)
        if leftover_boxes:
            att_hdr = Style().bold(True).foreground(theme.accent_secondary).render("── Image Attachments ──")
            sections.append(f"{att_hdr}\n\n" + "\n\n".join(leftover_boxes))

    return "\n\n".join(sections)


def _render_pgp_banner(msg: EmailMessage, theme: Theme, width: int, icon_mode: str = "emoji") -> str:
    """Render a color-coded OpenPGP verification/encryption banner."""
    from espressomail.ui.icons import normalize_icon_mode

    m = normalize_icon_mode(icon_mode)
    fp_short = msg.pgp_key_fingerprint[-19:] if msg.pgp_key_fingerprint else "Unknown Key"
    signer = msg.pgp_signer or msg.sender_display

    if m == "emoji":
        lock_icon = "🔒\ufe0f "
        key_icon = "🔐\ufe0f "
        warn_icon = "⚠ "
        err_icon = "✖ "
        ok_icon = "✔ "
    elif m == "unicode":
        lock_icon = "§ "
        key_icon = "§ "
        warn_icon = "! "
        err_icon = "✕ "
        ok_icon = "✓ "
    else:
        lock_icon = ""
        key_icon = ""
        warn_icon = ""
        err_icon = ""
        ok_icon = ""

    if msg.pgp_status == PGPStatus.SIGNED_VERIFIED:
        color = theme.pgp_verified_fg
        badge = f"{ok_icon}PGP VERIFIED SIGNATURE — Signed by {signer} [{fp_short}]"
    elif msg.pgp_status == PGPStatus.DECRYPTED_VERIFIED:
        color = theme.pgp_verified_fg
        badge = f"{lock_icon}PGP DECRYPTED & VERIFIED — Signed by {signer} [{fp_short}]"
    elif msg.pgp_status == PGPStatus.DECRYPTED:
        color = theme.pgp_verified_fg
        badge = f"{lock_icon}PGP DECRYPTED MESSAGE — Confidentiality verified"
    elif msg.pgp_status == PGPStatus.ENCRYPTED:
        color = theme.pgp_warning_fg
        badge = f"{key_icon}PGP ENCRYPTED PAYLOAD — Press [x] to decrypt with local keyring"
    elif msg.pgp_status == PGPStatus.SIGNED_UNVERIFIED:
        color = theme.pgp_warning_fg
        badge = f"{warn_icon}PGP SIGNATURE UNVERIFIED — Signer key [{fp_short}] not in trusted ring (Press [v] to verify)"
    else:
        color = theme.pgp_error_fg
        badge = f"{err_icon}PGP INVALID SIGNATURE — Warning: Message may have been tampered with!"

    return (
        Style()
        .bold(True)
        .foreground(color)
        .render(truncate_ansi(badge.strip(), max(20, width)))
    )


def _render_calendar_banner(event: CalendarEvent, theme: Theme, width: int, icon_mode: str = "emoji") -> str:
    """Render a visual, interactive iCalendar event invitation / cancellation banner."""
    from espressomail.ui.icons import normalize_icon_mode

    m = normalize_icon_mode(icon_mode)
    box_w = max(24, width)
    b_color = theme.accent_primary

    if m == "emoji":
        title_icon = "📅\ufe0f "
        cancel_icon = "🚫\ufe0f "
        reply_icon = "📨\ufe0f "
    elif m == "unicode":
        title_icon = "📅 "
        cancel_icon = "✕ "
        reply_icon = "▲ "
    else:
        title_icon = ""
        cancel_icon = ""
        reply_icon = ""

    is_cancelled = event.method == "CANCEL" or event.status == "CANCELLED"
    is_reply = event.method == "REPLY"

    if is_cancelled:
        b_color = theme.pgp_error_fg
        icon = cancel_icon
        title_text = "Event Cancelled"
    elif is_reply:
        b_color = theme.accent_secondary
        icon = reply_icon
        title_text = "Event RSVP Response"
    elif event.method in ("REQUEST", "PUBLISH"):
        b_color = theme.accent_primary
        icon = title_icon
        title_text = "Calendar Invitation"
    else:
        b_color = theme.accent_primary
        icon = title_icon
        title_text = "Calendar Event"

    lbl_style = Style().bold(True).foreground(theme.accent_primary)
    val_style = Style().foreground(theme.fg_primary)
    muted_style = Style().foreground(theme.fg_muted)

    lead = f"{icon}" if icon else ""
    top_title = f" {lead}{title_text}: {event.summary} "
    top_title_cut = truncate_ansi(top_title, box_w - 6)

    rows: list[str] = []

    # Time range
    time_str = event.formatted_time_range()
    rows.append(f"{lbl_style.render('When:        ')} {val_style.render(time_str)}")

    # Location
    if event.location:
        loc_formatted = _apply_inline_formatting(event.location, theme)
        rows.append(f"{lbl_style.render('Where:       ')} {loc_formatted}")

    # Organizer
    if event.organizer_display:
        rows.append(f"{lbl_style.render('Organizer:   ')} {val_style.render(event.organizer_display)}")

    # Status
    if is_cancelled:
        rows.append(
            f"{lbl_style.render('Status:      ')} "
            f"{Style().bold(True).foreground(theme.pgp_error_fg).render('Cancelled by Organizer')}"
        )
    elif is_reply:
        first_att = event.attendees[0] if event.attendees else None
        reply_stat = first_att.partstat if first_att else "Replied"
        rows.append(
            f"{lbl_style.render('Status:      ')} "
            f"{Style().bold(True).foreground(theme.accent_secondary).render(reply_stat)}"
        )
    else:
        rows.append(f"{lbl_style.render('Status:      ')} {muted_style.render(event.status or 'Confirmed')}")

    # Attendees snippet
    if event.attendees:
        att_names = [a.cn or a.email for a in event.attendees[:6]]
        att_str = ", ".join(att_names)
        if len(event.attendees) > 6:
            att_str += f" (+{len(event.attendees) - 6} more)"
        rows.append(f"{lbl_style.render('Attendees:   ')} {muted_style.render(att_str)}")

    # Description (if present)
    if event.description:
        desc_line = " ".join(event.description.splitlines())
        if desc_line.strip():
            desc_cut = desc_line[:120] + ("…" if len(desc_line) > 120 else "")
            rows.append(f"{lbl_style.render('Details:     ')} {val_style.render(desc_cut)}")

    # RSVP Action options
    if event.method in ("REQUEST", "PUBLISH") and not is_cancelled:
        rows.append("")
        btn_accept = Style().bold(True).foreground(theme.unread_fg).render("[1] Accept")
        btn_tentative = Style().bold(True).foreground(theme.starred_fg).render("[2] Tentative")
        btn_decline = Style().bold(True).foreground(theme.pgp_error_fg).render("[3] Decline")
        hint = muted_style.render("(Press '1', '2', or '3' to respond)")
        rows.append(f"{btn_accept}   {btn_tentative}   {btn_decline}")
        rows.append(hint)

    inner_content_w = max(20, box_w - 4)
    content = "\n".join(truncate_ansi(r, inner_content_w) for r in rows)
    banner_style = (
        Style()
        .border(ROUNDED_BORDER)
        .border_foreground(b_color)
        .border_title(top_title_cut)
        .width(inner_content_w)
        .padding(0, 1)
    )
    return banner_style.render(content)

