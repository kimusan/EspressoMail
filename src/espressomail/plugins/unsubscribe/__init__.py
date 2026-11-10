"""RFC 2369 / RFC 8058 One-Click Unsubscribe plugin for EspressoMail."""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from typing import Any, Sequence

from espresso import Cmd, Msg
from espresso.beans import ToastLevel
from espressomail.models.entities import EmailMessage, sanitize_terminal_text
from espressomail.plugins.base import BasePlugin, PluginMetadata
from espressomail.plugins.hooks import (
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnsubscribeTarget:
    """Parsed List-Unsubscribe endpoints from message headers."""

    http_url: str = ""
    mailto_address: str = ""
    mailto_subject: str = "Unsubscribe"
    mailto_body: str = "Please unsubscribe me from this mailing list."
    one_click_post: bool = False

    @property
    def is_available(self) -> bool:
        return bool(self.http_url or self.mailto_address)


@dataclass(frozen=True)
class UnsubscribeCompletedMsg(Msg):
    """TEA Message emitted when background HTTP One-Click Unsubscribe finishes."""

    message_id: str
    url: str
    success: bool
    status_code: int = 0
    error: str = ""


def parse_unsubscribe_headers(headers: dict[str, str]) -> UnsubscribeTarget:
    """Parse RFC 2369 List-Unsubscribe and RFC 8058 List-Unsubscribe-Post headers."""
    if not headers:
        return UnsubscribeTarget()

    raw_unsub = ""
    raw_post = ""
    for k, v in headers.items():
        kl = k.lower()
        if kl == "list-unsubscribe":
            raw_unsub = str(v or "").strip()
        elif kl == "list-unsubscribe-post":
            raw_post = str(v or "").strip()

    if not raw_unsub:
        return UnsubscribeTarget()

    # Extract bracketed <...> URIs or fallback to comma-separated tokens
    uris = re.findall(r"<([^>]+)>", raw_unsub)
    if not uris:
        uris = [u.strip() for u in raw_unsub.split(",") if u.strip()]

    http_url = ""
    mailto_addr = ""
    mailto_subj = "Unsubscribe"
    mailto_body = "Please unsubscribe me from this mailing list."

    for u in uris:
        ul = u.lower()
        if ul.startswith(("https://", "http://")) and not http_url:
            http_url = u.strip()
        elif ul.startswith("mailto:") and not mailto_addr:
            parsed = urllib.parse.urlparse(u.strip())
            mailto_addr = urllib.parse.unquote(parsed.path).strip()
            qs = urllib.parse.parse_qs(parsed.query)
            if "subject" in qs and qs["subject"]:
                mailto_subj = qs["subject"][0]
            if "body" in qs and qs["body"]:
                mailto_body = qs["body"][0]

    one_click = "list-unsubscribe=one-click" in raw_post.lower()
    return UnsubscribeTarget(
        http_url=http_url,
        mailto_address=mailto_addr,
        mailto_subject=mailto_subj,
        mailto_body=mailto_body,
        one_click_post=one_click,
    )


def execute_http_unsubscribe(url: str, one_click_post: bool = True, timeout: float = 12.0) -> tuple[bool, int, str]:
    """Perform an RFC 8058 HTTP POST (or GET fallback) to an unsubscribe endpoint."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return False, 0, "Invalid HTTP(S) unsubscribe URL"

    headers = {
        "User-Agent": "EspressoMail/1.0 (RFC8058 One-Click Unsubscribe)",
    }
    try:
        if one_click_post:
            data = b"List-Unsubscribe=One-Click"
            req = urllib.request.Request(
                url,
                data=data,
                headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
        else:
            req = urllib.request.Request(url, headers=headers, method="GET")

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
            return (200 <= code < 400), code, ""
    except urllib.error.HTTPError as he:
        # Some servers return 200/202/204 or require GET if POST is 405
        if one_click_post and he.code == 405:
            try:
                req_get = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req_get, timeout=timeout) as resp2:
                    code2 = getattr(resp2, "status", 200)
                    return (200 <= code2 < 400), code2, ""
            except Exception as exc2:
                return False, he.code, str(exc2)
        return False, he.code, f"HTTP {he.code}: {he.reason}"
    except Exception as exc:
        return False, 0, str(exc)


class UnsubscribePlugin(BasePlugin):
    """Provides RFC 8058 One-Click Unsubscribe and List-Unsubscribe handling."""

    metadata = PluginMetadata(
        id="unsubscribe",
        name="One-Click Unsubscribe",
        version="1.0.0",
        author="Kim Schulz",
        description="Detects List-Unsubscribe headers and executes RFC 8058 One-Click HTTP POST or mailto unsubscribe.",
        icon="🔕",
        settings_schema={
            "open_browser_fallback": {
                "type": "bool",
                "label": "Open Non-One-Click in Browser",
                "default": "false",
                "description": "Open non-RFC-8058 HTTP unsubscribe links in system browser instead of background GET",
            },
        },
    )

    def __init__(self, context: Any) -> None:
        super().__init__(context)
        self._unsubscribed_ids: set[str] = set()

    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        return (
            PluginMenuItem(
                target_category="Mail",
                action_id="unsubscribe_current_email",
                label="Unsubscribe from Mailing List",
                shortcut="Ctrl+K",
                order=52,
            ),
        )

    def get_commands(self) -> Sequence[PluginCommand]:
        return (
            PluginCommand(
                action_id="unsubscribe_current_email",
                title="Mail: One-Click Unsubscribe from Mailing List",
                description="Unsubscribe using RFC 8058 List-Unsubscribe-Post or List-Unsubscribe header",
                category="Plugins",
                default_key="ctrl+k",
                pane_scope="reader",
            ),
        )

    def get_keybindings(self) -> dict[str, str]:
        return {"ctrl+k": "unsubscribe_current_email"}

    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        if not message:
            return ()

        target = parse_unsubscribe_headers(message.raw_headers)
        if not target.is_available:
            return ()

        if message.id in self._unsubscribed_ids:
            return (
                ReaderActionPill(
                    action_id="unsubscribe_current_email",
                    label="Unsubscribed",
                    shortcut="Ctrl+K",
                    badge="✔ Unsubscribed",
                    priority=8,
                ),
            )

        badge = "⚡ 1-Click" if target.one_click_post else "🔕 List"
        return (
            ReaderActionPill(
                action_id="unsubscribe_current_email",
                label="Unsubscribe",
                shortcut="Ctrl+K",
                badge=badge,
                priority=8,
            ),
        )

    def on_action(
        self, action_id: str, app: Any, data: dict[str, Any]
    ) -> tuple[Any, Cmd | None]:
        if action_id == "unsubscribe_current_email":
            m = getattr(app, "selected_message", None)
            if not m:
                app.status_message = "No message selected to unsubscribe."
                return app, None

            target = parse_unsubscribe_headers(m.raw_headers)
            if not target.is_available:
                app.status_message = "This email does not contain a List-Unsubscribe header."
                if hasattr(app, "toasts"):
                    app.toasts.add("No List-Unsubscribe header found", level=ToastLevel.WARNING)
                return app, None

            # Prefer HTTP(S) One-Click or HTTP URL if available
            if target.http_url:
                open_browser = self.context.get_setting("open_browser_fallback", "false").lower() in ("1", "true", "yes")
                if open_browser and not target.one_click_post:
                    try:
                        webbrowser.open(target.http_url)
                        self._unsubscribed_ids.add(m.id)
                        app.status_message = f"🔕 Opened unsubscribe page in browser: {target.http_url[:60]}"
                        if hasattr(app, "toasts"):
                            app.toasts.add("Opened unsubscribe link in browser", level=ToastLevel.INFO)
                    except Exception as exc:
                        app.status_message = f"⚠ Could not open browser: {exc}"
                    return app, None

                app.status_message = f"🔕 Sending unsubscribe request to {sanitize_terminal_text(target.http_url[:50], False)}..."
                if hasattr(app, "toasts"):
                    app.toasts.add("Sending unsubscribe request...", level=ToastLevel.INFO)

                msg_id = m.id
                url = target.http_url
                is_post = target.one_click_post or True  # Try POST first with GET fallback on 405

                def _cmd() -> Msg:
                    ok, code, err = execute_http_unsubscribe(url, one_click_post=is_post)
                    return UnsubscribeCompletedMsg(
                        message_id=msg_id,
                        url=url,
                        success=ok,
                        status_code=code,
                        error=err,
                    )

                return app, _cmd

            # Fallback to mailto: compose modal
            if target.mailto_address:
                from espressomail.ui.modals import ComposeModal

                accounts = app.store.list_accounts() if hasattr(app, "store") else []
                contacts = app.store.list_contacts() if hasattr(app, "store") else []
                app.active_modal = ComposeModal(
                    accounts=accounts,
                    contacts=contacts,
                    theme=app.theme,
                    active_account_id=getattr(app, "active_account_id", ""),
                    to_initial=target.mailto_address,
                    subject_initial=target.mailto_subject,
                    body_initial=target.mailto_body,
                    external_editor=getattr(app, "external_editor", "auto"),
                    plugin_manager=getattr(app, "plugin_manager", None),
                )
                self._unsubscribed_ids.add(m.id)
                app.status_message = f"🔕 Opened unsubscribe email draft to {target.mailto_address} (Press F5 to send)."
                return app, None

        return app, None

    def on_message(self, msg: Any, app: Any) -> Cmd | None:
        if isinstance(msg, UnsubscribeCompletedMsg):
            if msg.success:
                self._unsubscribed_ids.add(msg.message_id)
                app.status_message = f"✔ Successfully unsubscribed via {sanitize_terminal_text(msg.url[:55], False)} (HTTP {msg.status_code})."
                if hasattr(app, "toasts"):
                    app.toasts.add("Successfully unsubscribed!", level=ToastLevel.INFO)
                if hasattr(app, "_update_reader_viewport"):
                    app._update_reader_viewport(force=True)
            else:
                app.status_message = f"⚠ Unsubscribe request failed: {msg.error}"
                if hasattr(app, "toasts"):
                    app.toasts.add(f"Unsubscribe failed: {msg.error}", level=ToastLevel.WARNING)
        return None
