"""LanguageTool Grammar & Spell Checker plugin for EspressoMail."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Sequence

from espresso import Cmd, Msg
from espresso.beans import ToastLevel
from espressomail.plugins.base import BasePlugin, PluginMetadata
from espressomail.plugins.hooks import (
    ComposeAction,
    PluginCommand,
    PluginMenuItem,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GrammarMatch:
    """Single grammar or spelling issue returned by LanguageTool."""

    offset: int
    length: int
    message: str
    replacement: str = ""
    rule_id: str = ""
    context_text: str = ""


@dataclass(frozen=True)
class LanguageToolResultMsg(Msg):
    """TEA Message emitted when background LanguageTool grammar check completes."""

    corrected_text: str
    matches_count: int
    applied_count: int
    detected_lang: str = ""
    issues_summary: list[str] = field(default_factory=list)
    error: str = ""


def apply_languagetool_matches(text: str, matches: Sequence[GrammarMatch]) -> tuple[str, int]:
    """Apply top replacement suggestions from right to left so character offsets remain valid."""
    if not text or not matches:
        return text, 0

    # Filter matches that have a valid non-empty replacement and valid bounds
    valid = [
        m
        for m in matches
        if m.replacement
        and 0 <= m.offset <= len(text)
        and m.length > 0
        and (m.offset + m.length) <= len(text)
    ]
    # Sort descending by offset, avoiding overlapping spans
    valid.sort(key=lambda m: m.offset, reverse=True)

    out = text
    applied = 0
    last_start = len(text) + 1
    for m in valid:
        end = m.offset + m.length
        if end > last_start:
            continue
        out = out[: m.offset] + m.replacement + out[end:]
        last_start = m.offset
        applied += 1

    return out, applied


def check_text_with_languagetool(
    text: str,
    api_url: str = "https://api.languagetool.org/v2/check",
    language: str = "auto",
    api_key: str = "",
    timeout: float = 15.0,
) -> tuple[bool, list[GrammarMatch], str, str]:
    """Query LanguageTool HTTP API and return (success, matches, detected_lang, error)."""
    if not text or not text.strip():
        return True, [], "", ""

    endpoint = (api_url or "https://api.languagetool.org/v2/check").strip()
    if not endpoint.endswith("/check"):
        endpoint = endpoint.rstrip("/") + "/v2/check" if "/v2" not in endpoint else endpoint.rstrip("/") + "/check"

    params: dict[str, str] = {
        "text": text,
        "language": (language or "auto").strip(),
    }
    if api_key and api_key.strip():
        params["apiKey"] = api_key.strip()
    form_data = urllib.parse.urlencode(params).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=form_data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "EspressoMail/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as he:
        return False, [], "", f"HTTP {he.code}: {he.reason}"
    except urllib.error.URLError as ue:
        return False, [], "", f"Connection error: {ue.reason}"
    except Exception as exc:
        return False, [], "", str(exc)

    lang_obj = payload.get("language", {})
    detected = ""
    if isinstance(lang_obj, dict):
        det_obj = lang_obj.get("detectedLanguage", {})
        if isinstance(det_obj, dict):
            detected = str(det_obj.get("code") or det_obj.get("name") or "")
        if not detected:
            detected = str(lang_obj.get("code") or "")

    matches: list[GrammarMatch] = []
    for raw_m in payload.get("matches", []):
        if not isinstance(raw_m, dict):
            continue
        offset = int(raw_m.get("offset", 0))
        length = int(raw_m.get("length", 0))
        msg_str = str(raw_m.get("message", ""))
        repls = raw_m.get("replacements", [])
        top_repl = ""
        if isinstance(repls, list) and repls:
            first = repls[0]
            if isinstance(first, dict):
                top_repl = str(first.get("value", ""))
        rule_id = ""
        if isinstance(raw_m.get("rule"), dict):
            rule_id = str(raw_m["rule"].get("id", ""))
        matches.append(
            GrammarMatch(
                offset=offset,
                length=length,
                message=msg_str,
                replacement=top_repl,
                rule_id=rule_id,
            )
        )

    return True, matches, detected, ""


class LanguageToolPlugin(BasePlugin):
    """Grammar, style, and spell checking for email drafts via LanguageTool."""

    metadata = PluginMetadata(
        id="languagetool",
        name="LanguageTool Grammar Check",
        version="1.0.0",
        author="Kim Schulz",
        description="Checks and fixes grammar, spelling, and punctuation in Compose drafts (Ctrl+L) via LanguageTool.",
        icon="✍️",
        settings_schema={
            "api_url": {
                "type": "str",
                "label": "LanguageTool API Endpoint",
                "default": "https://api.languagetool.org/v2/check",
                "description": "Public LanguageTool endpoint or self-hosted server URL",
            },
            "language": {
                "type": "str",
                "label": "Language Code",
                "default": "auto",
                "description": "Language code (e.g. auto, en-US, en-GB, de-DE, da-DK, fr)",
            },
            "api_key": {
                "type": "secret",
                "label": "API Key (Optional / Plus)",
                "default": "",
                "description": "Optional API key for LanguageTool Plus or authenticated self-hosted servers",
            },
            "auto_apply": {
                "type": "bool",
                "label": "Auto-Apply Top Corrections",
                "default": "true",
                "description": "Automatically apply the top suggested replacement for each detected error",
            },
        },
    )

    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        return (
            PluginMenuItem(
                target_category="Tools",
                action_id="languagetool_check_draft",
                label="Check Grammar (LanguageTool)",
                shortcut="Ctrl+L",
                order=53,
            ),
        )

    def get_commands(self) -> Sequence[PluginCommand]:
        return (
            PluginCommand(
                action_id="languagetool_check_draft",
                title="Grammar: Check & Fix Compose Draft (LanguageTool)",
                description="Run LanguageTool grammar and spell checker on the current email draft",
                category="Plugins",
                default_key="ctrl+l",
                pane_scope="compose",
            ),
        )

    def get_compose_actions(self, compose_modal: Any) -> Sequence[ComposeAction]:
        return (
            ComposeAction(
                action_id="languagetool_check_draft",
                label="✍️ Grammar Check",
                shortcut="Ctrl+L",
                priority=7,
            ),
        )

    def on_action(
        self, action_id: str, app: Any, data: dict[str, Any]
    ) -> tuple[Any, Cmd | None]:
        if action_id == "languagetool_check_draft":
            compose_modal = getattr(app, "active_modal", None)
            if not compose_modal or not hasattr(compose_modal, "editor"):
                app.status_message = "Open the Compose modal first to check grammar."
                return app, None

            draft_text = compose_modal.editor.value or ""
            if not draft_text.strip():
                app.status_message = "Compose draft body is empty."
                return app, None

            api_url = self.context.get_setting("api_url", "https://api.languagetool.org/v2/check")
            language = self.context.get_setting("language", "auto")
            api_key = self.context.get_setting("api_key", "")
            auto_apply = self.context.get_setting("auto_apply", "true").lower() in ("1", "true", "yes")

            app.status_message = "✍️ Checking grammar and spelling with LanguageTool..."
            if hasattr(app, "toasts"):
                app.toasts.add("Checking grammar (LanguageTool)...", level=ToastLevel.INFO)

            def _cmd() -> Msg:
                ok, matches, det_lang, err = check_text_with_languagetool(
                    draft_text, api_url=api_url, language=language, api_key=api_key
                )
                if not ok:
                    return LanguageToolResultMsg(
                        corrected_text="",
                        matches_count=0,
                        applied_count=0,
                        error=err or "LanguageTool request failed",
                    )

                summaries = [
                    f"{m.message}" + (f" → '{m.replacement}'" if m.replacement else "")
                    for m in matches[:5]
                ]
                if auto_apply and matches:
                    fixed_text, applied = apply_languagetool_matches(draft_text, matches)
                else:
                    fixed_text, applied = draft_text, 0

                return LanguageToolResultMsg(
                    corrected_text=fixed_text,
                    matches_count=len(matches),
                    applied_count=applied,
                    detected_lang=det_lang,
                    issues_summary=summaries,
                )

            return app, _cmd

        return app, None

    def on_message(self, msg: Any, app: Any) -> Cmd | None:
        if isinstance(msg, LanguageToolResultMsg):
            if msg.error:
                app.status_message = f"⚠ LanguageTool error: {msg.error}"
                if hasattr(app, "toasts"):
                    app.toasts.add(f"LanguageTool failed: {msg.error}", level=ToastLevel.WARNING)
                return None

            compose_modal = getattr(app, "active_modal", None)
            if msg.matches_count == 0:
                note = f"✔ LanguageTool: No grammar or spelling issues found ({msg.detected_lang or 'auto'})."
                app.status_message = note
                if compose_modal and hasattr(compose_modal, "editor_status_msg"):
                    compose_modal.editor_status_msg = note
                if hasattr(app, "toasts"):
                    app.toasts.add("No grammar issues found!", level=ToastLevel.INFO)
                return None

            if compose_modal and hasattr(compose_modal, "editor") and msg.applied_count > 0:
                compose_modal.editor.value = msg.corrected_text
                note = f"✔ LanguageTool fixed {msg.applied_count} of {msg.matches_count} issue(s)."
                compose_modal.editor_status_msg = note
                app.status_message = note
                if hasattr(app, "toasts"):
                    app.toasts.add(f"Fixed {msg.applied_count} grammar/spelling issue(s)", level=ToastLevel.INFO)
            else:
                first_issue = msg.issues_summary[0] if msg.issues_summary else ""
                note = f"✍️ LanguageTool found {msg.matches_count} issue(s): {first_issue}"
                if compose_modal and hasattr(compose_modal, "editor_status_msg"):
                    compose_modal.editor_status_msg = note
                app.status_message = note
                if hasattr(app, "toasts"):
                    app.toasts.add(f"Found {msg.matches_count} grammar issue(s)", level=ToastLevel.INFO)

        return None
