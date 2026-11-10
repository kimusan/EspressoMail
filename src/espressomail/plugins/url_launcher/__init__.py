"""URL Launcher & Link Picker (urlscan / urlview style) plugin for EspressoMail."""

from __future__ import annotations

import html
import logging
import re
import webbrowser
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import urlparse

from espresso import Cmd
from espresso.beans import ToastLevel
from espresso.crema import Style
from espressomail.models.entities import EmailMessage, sanitize_terminal_text
from espressomail.plugins.base import BasePlugin, PluginMetadata
from espressomail.plugins.hooks import (
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)
from espressomail.themes.engine import THEMES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractedLink:
    """Represents a deduplicated hyperlink extracted from an email body."""

    index: int
    url: str
    label: str = ""
    domain: str = ""


_TRACKING_PATTERNS = (
    "open.convertkit",
    "/track/open",
    "pixel.gif",
    "spacer.gif",
    "1x1.gif",
    "1x1.png",
    "wf/open",
)


def _is_likely_tracking_pixel(url: str) -> bool:
    ul = url.lower()
    if any(pat in ul for pat in _TRACKING_PATTERNS):
        return True
    parsed = urlparse(ul)
    path = parsed.path or ""
    if path.endswith((".gif", ".png")) and ("pixel" in path or "track" in path or "open" in path):
        return True
    return False


def extract_links_from_message(message: EmailMessage | None) -> list[ExtractedLink]:
    """Extract and deduplicate HTTP(S) links and labels from an EmailMessage."""
    if not message:
        return []

    seen: dict[str, str] = {}  # url -> best label
    order: list[str] = []

    def _add(raw_url: str, raw_label: str = "") -> None:
        u = html.unescape(raw_url.strip()).rstrip(".,;)>]\"'")
        if not u or not u.lower().startswith(("http://", "https://")):
            return
        if _is_likely_tracking_pixel(u):
            return
        lbl = sanitize_terminal_text(html.unescape(" ".join(raw_label.split())).strip(), multiline=False)
        if lbl == u:
            lbl = ""
        if u not in seen:
            seen[u] = lbl
            order.append(u)
        elif lbl and not seen[u]:
            seen[u] = lbl

    # 1. Extract from HTML <a href="...">label</a>
    body_html = message.body_html or ""
    if body_html:
        for m in re.finditer(
            r'<a\b[^>]*?\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            body_html,
            re.IGNORECASE | re.DOTALL,
        ):
            href = m.group(1)
            inner_text = re.sub(r"<[^>]+>", "", m.group(2))
            _add(href, inner_text)

    # 2. Extract Markdown links [label](url) and raw URLs from body_text
    body_text = message.body_text or ""
    if body_text:
        for m in re.finditer(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", body_text):
            _add(m.group(2), m.group(1))
        for m in re.finditer(r"https?://[^\s<>\"')\]]+", body_text):
            _add(m.group(0), "")

    # 3. Also scan raw URLs in HTML if any weren't inside <a> tags
    if body_html and not order:
        plain_from_html = re.sub(r"<[^>]+>", " ", body_html)
        for m in re.finditer(r"https?://[^\s<>\"')\]]+", plain_from_html):
            _add(m.group(0), "")

    results: list[ExtractedLink] = []
    for idx, url in enumerate(order, start=1):
        try:
            dom = (urlparse(url).hostname or "").lower()
        except Exception:
            dom = ""
        results.append(
            ExtractedLink(
                index=idx,
                url=sanitize_terminal_text(url, multiline=False),
                label=seen.get(url, ""),
                domain=dom,
            )
        )
    return results


class URLLauncherPlugin(BasePlugin):
    """Extracts links from emails, renders a numbered link table, and opens URLs in the browser."""

    metadata = PluginMetadata(
        id="url_launcher",
        name="URL Launcher & Link Picker",
        version="1.0.0",
        author="Kim Schulz",
        description="Extracts URLs from emails into a numbered reference list (Ctrl+O) and launches links in your browser.",
        icon="🔗",
        settings_schema={
            "always_show_links_table": {
                "type": "bool",
                "label": "Always Show Links in Reader",
                "default": "false",
                "description": "Automatically append the numbered links reference table at the bottom of emails",
            },
            "open_on_toggle_single": {
                "type": "bool",
                "label": "Auto-Open Single Link Emails",
                "default": "false",
                "description": "Immediately open the link in system browser when pressing Ctrl+O on a 1-link email",
            },
        },
    )

    def __init__(self, context: Any) -> None:
        super().__init__(context)
        self._active_link_tables: set[str] = set()
        self._cursor_by_message: dict[str, int] = {}

    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        return (
            PluginMenuItem(
                target_category="Tools",
                action_id="url_launcher_toggle",
                label="Toggle Email Links List",
                shortcut="Ctrl+O",
                order=54,
            ),
            PluginMenuItem(
                target_category="Tools",
                action_id="url_launcher_open_selected",
                label="Open Selected Email Link in Browser",
                shortcut="",
                order=54,
            ),
        )

    def get_commands(self) -> Sequence[PluginCommand]:
        return (
            PluginCommand(
                action_id="url_launcher_toggle",
                title="Links: Toggle Numbered URL List / Cycle Link Selection",
                description="Show all hyperlinks in the current email or cycle the selected link",
                category="Plugins",
                default_key="ctrl+o",
                pane_scope="reader",
            ),
            PluginCommand(
                action_id="url_launcher_open_selected",
                title="Links: Open Active Email Link in System Browser",
                description="Open the currently highlighted email link in your default web browser",
                category="Plugins",
                default_key="",
                pane_scope="reader",
            ),
        )

    def get_keybindings(self) -> dict[str, str]:
        return {"ctrl+o": "url_launcher_toggle"}

    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        if not message:
            return ()
        links = extract_links_from_message(message)
        if not links:
            return ()

        count = len(links)
        if message.id in self._active_link_tables:
            cur_idx = self._cursor_by_message.get(message.id, 0) % count
            return (
                ReaderActionPill(
                    action_id="url_launcher_toggle",
                    label=f"Next Link [{cur_idx + 1}/{count}]",
                    shortcut="Ctrl+O",
                    badge=f"🔗 {count}",
                    priority=7,
                ),
                ReaderActionPill(
                    action_id="url_launcher_open_selected",
                    label=f"Open #{cur_idx + 1}",
                    shortcut="",
                    priority=7,
                ),
            )

        return (
            ReaderActionPill(
                action_id="url_launcher_toggle",
                label="Links",
                shortcut="Ctrl+O",
                badge=f"🔗 {count}",
                priority=7,
            ),
        )

    def transform_reader_content(
        self, message: EmailMessage, rendered_body: str, state: dict[str, Any]
    ) -> str:
        if not message:
            return rendered_body

        always_show = self.context.get_setting("always_show_links_table", "false").lower() in ("1", "true", "yes")
        is_active = message.id in self._active_link_tables
        if not is_active and not always_show:
            return rendered_body

        links = extract_links_from_message(message)
        if not links:
            return rendered_body

        theme = state.get("theme") or THEMES["espresso"]
        width = max(50, int(state.get("width", 78)))
        accent = theme.accent_primary
        accent2 = theme.accent_secondary
        fg_muted = theme.fg_muted
        fg_primary = theme.fg_primary

        cur_idx = self._cursor_by_message.get(message.id, 0) % len(links)
        divider = Style().foreground(fg_muted).render("─" * min(width, 74))

        lines: list[str] = [
            divider,
            Style().bold(True).foreground(accent).render(f"🔗 Extracted Links ({len(links)})")
            + " "
            + Style()
            .foreground(fg_muted)
            .render("(Press Ctrl+O to cycle/hide, or click 'Open #N' / Command Palette to launch)"),
        ]

        for i, item in enumerate(links):
            is_sel = i == cur_idx and is_active
            prefix = "▸" if is_sel else " "
            num_badge = f"[{item.index:2d}]"
            label_part = f" {item.label[:32]} — " if item.label else " "
            url_str = item.url[: max(30, width - len(label_part) - 10)]
            row_text = f" {prefix} {num_badge}{label_part}{url_str}"
            if is_sel:
                lines.append(Style().bold(True).foreground(accent2).render(row_text))
            else:
                lines.append(Style().foreground(fg_primary).render(row_text))

        links_block = "\n".join(lines)
        return f"{rendered_body}\n\n{links_block}"

    def on_action(
        self, action_id: str, app: Any, data: dict[str, Any]
    ) -> tuple[Any, Cmd | None]:
        m = getattr(app, "selected_message", None)
        if not m:
            app.status_message = "No message selected."
            return app, None

        links = extract_links_from_message(m)
        if not links:
            app.status_message = "No HTTP(S) links found in this email."
            if hasattr(app, "toasts"):
                app.toasts.add("No links found in this email", level=ToastLevel.INFO)
            return app, None

        if action_id == "url_launcher_toggle":
            open_single = self.context.get_setting("open_on_toggle_single", "false").lower() in ("1", "true", "yes")
            if len(links) == 1 and open_single:
                return self._open_link(links[0], app)

            if m.id not in self._active_link_tables:
                self._active_link_tables.add(m.id)
                self._cursor_by_message[m.id] = 0
                sel = links[0]
                app.status_message = f"🔗 Showing {len(links)} link(s). Selected [1/{len(links)}]: {sel.url[:60]}"
            else:
                cur = self._cursor_by_message.get(m.id, 0)
                if cur + 1 < len(links):
                    nxt = cur + 1
                    self._cursor_by_message[m.id] = nxt
                    sel = links[nxt]
                    app.status_message = f"🔗 Selected link [{nxt + 1}/{len(links)}]: {sel.url[:60]}"
                else:
                    self._active_link_tables.remove(m.id)
                    self._cursor_by_message[m.id] = 0
                    app.status_message = "Hidden email links table."

            if hasattr(app, "_update_reader_viewport"):
                app._update_reader_viewport(force=True)
            return app, None

        if action_id == "url_launcher_open_selected":
            idx = self._cursor_by_message.get(m.id, 0) % len(links)
            return self._open_link(links[idx], app)

        return app, None

    def _open_link(self, link: ExtractedLink, app: Any) -> tuple[Any, Cmd | None]:
        try:
            webbrowser.open(link.url)
            app.status_message = f"🔗 Opened link #{link.index} in browser: {link.url[:65]}"
            if hasattr(app, "toasts"):
                app.toasts.add(f"Opened {link.domain or link.url[:40]}", level=ToastLevel.INFO)
        except Exception as exc:
            app.status_message = f"⚠ Failed to open browser: {exc}"
            if hasattr(app, "toasts"):
                app.toasts.add(f"Browser launch failed: {exc}", level=ToastLevel.WARNING)
        return app, None
