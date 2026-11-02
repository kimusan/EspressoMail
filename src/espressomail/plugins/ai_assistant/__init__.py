"""AI Email Assistant plugin (Summarizer, Action Items & Draft Polisher) for EspressoMail."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Sequence

from espresso import Cmd, Msg
from espresso.beans import ToastLevel
from espresso.crema import Style
from espressomail.models.entities import EmailMessage, sanitize_terminal_text
from espressomail.plugins.base import BasePlugin, PluginMetadata
from espressomail.plugins.hooks import (
    ComposeAction,
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)
from espressomail.themes.engine import THEMES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AISummaryReadyMsg(Msg):
    """TEA Message emitted when background AI email summarization or draft polishing finishes."""

    message_id: str
    summary_text: str
    provider: str
    model: str
    error: str = ""
    is_draft: bool = False


class AIClient:
    """Zero-dependency HTTP client supporting Ollama, OpenAI-compatible, and Anthropic APIs."""

    def __init__(
        self,
        provider: str = "ollama",
        api_url: str = "",
        api_key: str = "",
        model: str = "llama3.2",
        timeout: float = 30.0,
    ) -> None:
        self.provider = (provider or "ollama").strip().lower()
        self.api_key = (api_key or "").strip()
        self.model = (model or "llama3.2").strip()
        self.timeout = timeout

        default_urls = {
            "ollama": "http://localhost:11434",
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com/v1",
        }
        raw_url = (api_url or "").strip() or default_urls.get(self.provider, "http://localhost:11434")
        self.api_url = raw_url.rstrip("/")

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[bool, str, str]:
        """Run a chat completion and return (success, text_response, error_message)."""
        try:
            if self.provider == "ollama":
                return self._call_ollama(system_prompt, user_prompt)
            if self.provider == "anthropic":
                return self._call_anthropic(system_prompt, user_prompt)
            return self._call_openai(system_prompt, user_prompt)
        except urllib.error.HTTPError as he:
            try:
                err_body = he.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                err_body = he.reason
            return False, "", f"HTTP {he.code}: {err_body}"
        except urllib.error.URLError as ue:
            return False, "", f"Connection error ({self.api_url}): {ue.reason}"
        except Exception as exc:
            return False, "", str(exc)

    def _call_ollama(self, system_prompt: str, user_prompt: str) -> tuple[bool, str, str]:
        endpoint = (
            self.api_url
            if self.api_url.endswith(("/api/chat", "/api/generate"))
            else f"{self.api_url}/api/chat"
        )
        payload = {
            "model": self.model or "llama3.2",
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        msg_obj = data.get("message", {})
        content = msg_obj.get("content", "") if isinstance(msg_obj, dict) else data.get("response", "")
        content = str(content or "").strip()
        if not content:
            return False, "", "Empty response from Ollama"
        return True, content, ""

    def _call_openai(self, system_prompt: str, user_prompt: str) -> tuple[bool, str, str]:
        if not self.api_key and "localhost" not in self.api_url and "127.0.0.1" not in self.api_url:
            return False, "", "OpenAI API Key is not configured in Preferences > Plugins."
        endpoint = (
            self.api_url
            if self.api_url.endswith("/chat/completions")
            else f"{self.api_url}/chat/completions"
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        choices = data.get("choices", [])
        if not choices:
            return False, "", "No choices returned by OpenAI endpoint"
        content = str(choices[0].get("message", {}).get("content", "")).strip()
        return (True, content, "") if content else (False, "", "Empty completion content")

    def _call_anthropic(self, system_prompt: str, user_prompt: str) -> tuple[bool, str, str]:
        if not self.api_key:
            return False, "", "Anthropic API Key is not configured in Preferences > Plugins."
        endpoint = (
            self.api_url if self.api_url.endswith("/messages") else f"{self.api_url}/messages"
        )
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self.model or "claude-3-5-haiku-latest",
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        blocks = data.get("content", [])
        texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        content = "\n".join(texts).strip()
        return (True, content, "") if content else (False, "", "Empty response from Anthropic")


class AIAssistantPlugin(BasePlugin):
    """Provides local/cloud AI email summarization, action-item extraction, and draft polishing."""

    metadata = PluginMetadata(
        id="ai_assistant",
        name="AI Email Assistant",
        version="1.0.0",
        author="Kim Schulz",
        description="Summarizes emails into TL;DR & action items (Ctrl+Y) and polishes drafts (Ctrl+B) via Ollama, OpenAI, or Claude.",
        icon="🤖",
        settings_schema={
            "provider": {
                "type": "choice",
                "label": "AI Provider",
                "default": "ollama",
                "options": ["ollama", "openai", "anthropic"],
                "description": "Local Ollama (privacy-first) or OpenAI / Anthropic cloud API",
            },
            "api_url": {
                "type": "str",
                "label": "API Base URL",
                "default": "http://localhost:11434",
                "description": "Base endpoint URL (e.g. http://localhost:11434 for Ollama)",
            },
            "model": {
                "type": "str",
                "label": "Model Name",
                "default": "llama3.2",
                "description": "Model identifier (e.g. llama3.2, qwen2.5, gpt-4o-mini, claude-3-5-haiku-latest)",
            },
            "api_key": {
                "type": "secret",
                "label": "API Key (OpenAI / Anthropic)",
                "default": "",
                "description": "Optional API key for cloud providers (not needed for local Ollama)",
            },
        },
    )

    def __init__(self, context: Any) -> None:
        super().__init__(context)
        # message_id -> (summary_text, provider, model)
        self._active_summaries: dict[str, tuple[str, str, str]] = {}

    def get_client(self) -> AIClient:
        provider = self.context.get_setting("provider", "ollama")
        api_url = self.context.get_setting("api_url", "http://localhost:11434")
        api_key = self.context.get_setting("api_key", "")
        model = self.context.get_setting("model", "llama3.2")
        return AIClient(provider=provider, api_url=api_url, api_key=api_key, model=model)

    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        return (
            PluginMenuItem(
                target_category="Mail",
                action_id="ai_summarize_email",
                label="AI Summarize & Action Items",
                shortcut="Ctrl+Y",
                order=51,
            ),
            PluginMenuItem(
                target_category="Tools",
                action_id="ai_polish_draft",
                label="AI Polish Compose Draft",
                shortcut="Ctrl+B",
                order=51,
            ),
        )

    def get_commands(self) -> Sequence[PluginCommand]:
        return (
            PluginCommand(
                action_id="ai_summarize_email",
                title="AI: Toggle Email TL;DR Summary & Action Items",
                description="Generate a concise summary and extract action items for the current email",
                category="Plugins",
                default_key="ctrl+y",
                pane_scope="reader",
            ),
            PluginCommand(
                action_id="ai_polish_draft",
                title="AI: Polish & Proofread Compose Draft",
                description="Improve clarity, grammar, and tone of the current email draft",
                category="Plugins",
                default_key="ctrl+b",
                pane_scope="compose",
            ),
        )

    def get_keybindings(self) -> dict[str, str]:
        return {"ctrl+y": "ai_summarize_email"}

    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        if not message:
            return ()
        if message.id in self._active_summaries:
            _, prov, mod = self._active_summaries[message.id]
            return (
                ReaderActionPill(
                    action_id="ai_summarize_email",
                    label="Hide Summary",
                    shortcut="Ctrl+Y",
                    badge=f"🤖 {mod or prov}",
                    priority=6,
                ),
            )
        return (
            ReaderActionPill(
                action_id="ai_summarize_email",
                label="AI Summary",
                shortcut="Ctrl+Y",
                priority=6,
            ),
        )

    def get_compose_actions(self, compose_modal: Any) -> Sequence[ComposeAction]:
        return (
            ComposeAction(
                action_id="ai_polish_draft",
                label="🤖 Polish Draft",
                shortcut="Ctrl+B",
                priority=6,
            ),
        )

    def transform_reader_content(
        self, message: EmailMessage, rendered_body: str, state: dict[str, Any]
    ) -> str:
        if not message or message.id not in self._active_summaries:
            return rendered_body

        summary_text, provider, model = self._active_summaries[message.id]
        theme = state.get("theme") or THEMES["espresso"]
        accent = theme.accent_secondary
        fg_muted = theme.fg_muted
        fg_primary = theme.fg_primary

        lines: list[str] = [
            Style().bold(True).foreground(accent).render(f"🤖 AI Summary & Action Items ({provider}/{model})")
            + " "
            + Style().foreground(fg_muted).render("(Press Ctrl+Y to hide)"),
        ]
        for raw_line in sanitize_terminal_text(summary_text).splitlines():
            clean = raw_line.strip()
            if not clean:
                continue
            lines.append(Style().foreground(fg_primary).render(f"  {clean}"))

        summary_block = "\n".join(lines)
        div_match = re.search(r"━{10,}", rendered_body)
        if div_match:
            line_end = rendered_body.find("\n", div_match.end())
            line_end = len(rendered_body) if line_end == -1 else line_end
            header_block = rendered_body[:line_end]
            body_rest = rendered_body[line_end:].lstrip("\n")
            return f"{header_block}\n\n{summary_block}\n\n{body_rest}"

        return f"{summary_block}\n\n{rendered_body}"

    def on_action(
        self, action_id: str, app: Any, data: dict[str, Any]
    ) -> tuple[Any, Cmd | None]:
        if action_id == "ai_summarize_email":
            m = getattr(app, "selected_message", None)
            if not m:
                app.status_message = "No message selected to summarize."
                return app, None

            if m.id in self._active_summaries:
                del self._active_summaries[m.id]
                app.status_message = "Hidden AI summary."
                app._update_reader_viewport(force=True)
                return app, None

            client = self.get_client()
            cache_key = f"summary:{m.id}:{client.provider}:{client.model}"
            cached = self.context.get_cache(cache_key)
            if cached and cached.strip():
                self._active_summaries[m.id] = (cached, client.provider, client.model)
                app.status_message = f"✔ Loaded AI summary from cache ({client.model})."
                if hasattr(app, "toasts"):
                    app.toasts.add("Loaded AI summary (cached)", level=ToastLevel.INFO)
                app._update_reader_viewport(force=True)
                return app, None

            app.status_message = f"🤖 Generating AI summary via {client.provider} ({client.model})..."
            if hasattr(app, "toasts"):
                app.toasts.add(f"Summarizing with {client.model}...", level=ToastLevel.INFO)

            return app, self._make_summarize_cmd(m, client, cache_key)

        if action_id == "ai_polish_draft":
            compose_modal = getattr(app, "active_modal", None)
            if not compose_modal or not hasattr(compose_modal, "editor"):
                app.status_message = "Open the Compose modal first to polish a draft."
                return app, None

            draft_text = (compose_modal.editor.value or "").strip()
            if not draft_text:
                app.status_message = "Compose draft body is empty."
                return app, None

            client = self.get_client()
            app.status_message = f"🤖 Polishing draft via {client.provider} ({client.model})..."
            if hasattr(app, "toasts"):
                app.toasts.add(f"Polishing draft ({client.model})...", level=ToastLevel.INFO)

            return app, self._make_polish_draft_cmd(draft_text, client)

        return app, None

    def _make_summarize_cmd(self, message: EmailMessage, client: AIClient, cache_key: str) -> Cmd:
        def _runner() -> Msg:
            body_source = (message.body_text or "").strip()
            if not body_source and message.body_html:
                from espressomail.ui.formatter import html_to_terminal_text

                body_source = html_to_terminal_text(message.body_html, include_image_markers=False).strip()

            if not body_source and not (message.subject or "").strip():
                return AISummaryReadyMsg(
                    message_id=message.id,
                    summary_text="",
                    provider=client.provider,
                    model=client.model,
                    error="Email has no text content to summarize.",
                )

            sys_prompt = (
                "You are a concise executive email assistant. Summarize the email into:\n"
                "• TL;DR: (1-2 sentences)\n"
                "• Key Points: (up to 3 bullet points)\n"
                "• Action Items / Deadlines: (bullet points, or 'None')\n"
                "Keep output under 8 lines total. Plain text only."
            )
            user_prompt = (
                f"From: {message.sender_display}\n"
                f"Subject: {message.subject}\n\n"
                f"{body_source[:6000]}"
            )
            ok, out_text, err = client.complete(sys_prompt, user_prompt)
            if not ok:
                return AISummaryReadyMsg(
                    message_id=message.id,
                    summary_text="",
                    provider=client.provider,
                    model=client.model,
                    error=err or "AI completion failed",
                )

            self.context.set_cache(cache_key, out_text, ttl_seconds=30 * 86400.0)
            return AISummaryReadyMsg(
                message_id=message.id,
                summary_text=out_text,
                provider=client.provider,
                model=client.model,
            )

        return _runner

    def _make_polish_draft_cmd(self, draft_text: str, client: AIClient) -> Cmd:
        def _runner() -> Msg:
            sys_prompt = (
                "You are an expert email editor. Improve the grammar, clarity, and professional tone "
                "of the user's email draft while preserving its original meaning, language, and sign-off. "
                "Return ONLY the revised email body text with no preamble or commentary."
            )
            ok, out_text, err = client.complete(sys_prompt, draft_text[:6000])
            if not ok:
                return AISummaryReadyMsg(
                    message_id="",
                    summary_text="",
                    provider=client.provider,
                    model=client.model,
                    error=err or "Failed to polish draft",
                    is_draft=True,
                )
            return AISummaryReadyMsg(
                message_id="",
                summary_text=out_text,
                provider=client.provider,
                model=client.model,
                is_draft=True,
            )

        return _runner

    def on_message(self, msg: Any, app: Any) -> Cmd | None:
        if isinstance(msg, AISummaryReadyMsg):
            if msg.error:
                app.status_message = f"⚠ AI Assistant error: {msg.error}"
                if hasattr(app, "toasts"):
                    app.toasts.add(f"AI Assistant failed: {msg.error}", level=ToastLevel.WARNING)
                return None

            if msg.is_draft:
                compose_modal = getattr(app, "active_modal", None)
                if compose_modal and hasattr(compose_modal, "editor") and msg.summary_text:
                    compose_modal.editor.value = msg.summary_text
                    compose_modal.editor_status_msg = f"✔ Draft polished by {msg.model}."
                    app.status_message = f"✔ Draft polished via {msg.provider} ({msg.model})."
                    if hasattr(app, "toasts"):
                        app.toasts.add(f"Draft polished ({msg.model})", level=ToastLevel.INFO)
                return None

            self._active_summaries[msg.message_id] = (msg.summary_text, msg.provider, msg.model)
            app.status_message = f"✔ AI Summary ready ({msg.model}). Press Ctrl+Y to hide."
            if hasattr(app, "toasts"):
                app.toasts.add(f"AI Summary ready ({msg.model})", level=ToastLevel.INFO)
            if hasattr(app, "_update_reader_viewport"):
                app._update_reader_viewport(force=True)
        return None
