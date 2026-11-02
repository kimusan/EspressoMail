"""DeepL Translation plugin for EspressoMail."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Sequence

from espresso import Cmd, Msg
from espresso.beans import ToastLevel
from espresso.crema import Style
from espressomail.models.entities import EmailMessage
from espressomail.plugins.base import BasePlugin, PluginMetadata
from espressomail.plugins.deepl.client import (
    COMMON_TARGET_LANGUAGES,
    DeepLClient,
)
from espressomail.plugins.hooks import (
    ComposeAction,
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeepLTranslationReadyMsg(Msg):
    """TEA Message emitted when background DeepL translation finishes."""

    message_id: str
    translated_subject: str
    translated_body: str
    detected_source_lang: str
    target_lang: str
    error: str = ""
    is_draft: bool = False


class DeepLPlugin(BasePlugin):
    """Integrates DeepL neural machine translation into EspressoMail."""

    metadata = PluginMetadata(
        id="deepl_translate",
        name="DeepL Translation",
        version="1.0.0",
        author="Kim Schulz",
        description="In-place email translation in reading pane and draft translation in composer via DeepL.",
        icon="🌐",
        settings_schema={
            "auth_key": {
                "type": "secret",
                "label": "DeepL Auth Key",
                "default": "",
                "description": "API Key from deepl.com (Free keys end with :fx)",
            },
            "target_lang": {
                "type": "choice",
                "label": "Default Target Language",
                "default": "EN-GB",
                "options": [code for code, _ in COMMON_TARGET_LANGUAGES],
                "description": "Target language for message and draft translation",
            },
            "formality": {
                "type": "choice",
                "label": "Formality Preference",
                "default": "default",
                "options": ["default", "prefer_more", "prefer_less"],
                "description": "Tone preference for supported languages",
            },
            "cache_days": {
                "type": "int",
                "label": "Remember translations (days)",
                "default": "30",
                "description": "Reopening a translated message uses cache and costs no quota",
            },
        },
    )

    def __init__(self, context: Any) -> None:
        super().__init__(context)
        # message_id -> (translated_subject, translated_body, detected_source_lang, target_lang)
        self._active_translations: dict[str, tuple[str, str, str, str]] = {}

    def get_client(self) -> DeepLClient:
        auth_key = self.context.get_setting("auth_key", "")
        target_lang = self.context.get_setting("target_lang", "EN-GB")
        formality = self.context.get_setting("formality", "default")
        return DeepLClient(auth_key=auth_key, target_lang=target_lang, formality=formality)

    @property
    def target_lang(self) -> str:
        return self.context.get_setting("target_lang", "EN-GB").upper()

    # ------------------------------------------------------------------
    # Plugin Hooks
    # ------------------------------------------------------------------

    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        return (
            PluginMenuItem(
                target_category="Mail",
                action_id="deepl_toggle_translate",
                label="Translate with DeepL",
                shortcut="T",
                order=50,
            ),
            PluginMenuItem(
                target_category="Tools",
                action_id="deepl_translate_draft",
                label="Translate Draft (DeepL)",
                shortcut="Ctrl+T",
                order=50,
            ),
        )

    def get_commands(self) -> Sequence[PluginCommand]:
        tgt = self.target_lang
        return (
            PluginCommand(
                action_id="deepl_toggle_translate",
                title=f"Translate: Toggle Translation to {tgt} (DeepL)",
                description=f"Translate current email in reading pane into {tgt} or revert to original",
                category="Plugins",
                default_key="T",
                pane_scope="reader",
            ),
            PluginCommand(
                action_id="deepl_translate_draft",
                title=f"Translate: Translate Draft to {tgt} (DeepL)",
                description=f"Translate draft subject and body into {tgt}",
                category="Plugins",
                default_key="ctrl+t",
                pane_scope="compose",
            ),
            PluginCommand(
                action_id="deepl_cycle_target_lang",
                title="Translate: Switch DeepL Target Language",
                description="Cycle through supported DeepL target languages (e.g. EN, DE, DA, FR, ES, JA)",
                category="Plugins",
                default_key="",
                pane_scope="global",
            ),
        )

    def get_keybindings(self) -> dict[str, str]:
        return {"t": "deepl_toggle_translate"}

    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        if not message:
            return ()
        if message.id in self._active_translations:
            _, _, src, tgt = self._active_translations[message.id]
            return (
                ReaderActionPill(
                    action_id="deepl_toggle_translate",
                    label="Show Original",
                    shortcut="T",
                    badge=f"🌐 {src} → {tgt}",
                    priority=5,
                ),
            )
        else:
            return (
                ReaderActionPill(
                    action_id="deepl_toggle_translate",
                    label=f"Translate ({self.target_lang})",
                    shortcut="T",
                    priority=5,
                ),
            )

    def get_compose_actions(self, compose_modal: Any) -> Sequence[ComposeAction]:
        return (
            ComposeAction(
                action_id="deepl_translate_draft",
                label=f"🌐 Translate ({self.target_lang})",
                shortcut="Ctrl+T",
                priority=5,
            ),
        )

    def transform_reader_content(
        self, message: EmailMessage, rendered_body: str, state: dict[str, Any]
    ) -> str:
        """Inject translated content and translation banner if message is active."""
        if not message or message.id not in self._active_translations:
            return rendered_body

        trans_subj, trans_body, src, tgt = self._active_translations[message.id]
        theme = state.get("theme")
        accent = theme.accent_primary if theme else "#d4a373"
        fg_muted = theme.fg_muted if theme else "#8c776b"
        banner = (
            Style().bold(True).foreground(accent).render(f"🌐 DeepL Translation: {src} → {tgt}")
            + " "
            + Style().foreground(fg_muted).render("(Press T to view original)")
        )

        divider = Style().foreground(fg_muted).render("─" * 60)
        return f"{banner}\n{divider}\n\n{trans_body}"

    # ------------------------------------------------------------------
    # Action Dispatcher & Execution
    # ------------------------------------------------------------------

    def on_action(
        self, action_id: str, app: Any, data: dict[str, Any]
    ) -> tuple[Any, Cmd | None]:
        if action_id == "deepl_cycle_target_lang":
            cur_lang = self.target_lang
            codes = [code for code, _ in COMMON_TARGET_LANGUAGES]
            try:
                idx = codes.index(cur_lang)
                next_code = codes[(idx + 1) % len(codes)]
            except ValueError:
                next_code = "EN-GB"
            self.context.set_setting("target_lang", next_code)
            app.status_message = f"✔ DeepL target language switched to {next_code}."
            if hasattr(app, "toasts"):
                app.toasts.add(f"DeepL target language: {next_code}", level=ToastLevel.INFO)
            return app, None

        if action_id == "deepl_toggle_translate":
            m = getattr(app, "selected_message", None)
            if not m:
                app.status_message = "No message selected to translate."
                return app, None

            # 1. If currently showing translated version, revert to original
            if m.id in self._active_translations:
                del self._active_translations[m.id]
                app.status_message = "Reverted to original email body."
                if hasattr(app, "toasts"):
                    app.toasts.add("Showing original email", level=ToastLevel.INFO)
                app._update_reader_viewport(force=True)
                return app, None

            # 2. Check local SQLite cache for 30-day translation
            tgt = self.target_lang
            cache_key = f"{m.id}:{tgt}"
            cached_raw = self.context.get_cache(cache_key)
            if cached_raw:
                try:
                    cached_data = json.loads(cached_raw)
                    self._active_translations[m.id] = (
                        cached_data.get("subject", m.subject),
                        cached_data.get("body", m.body_text),
                        cached_data.get("detected", "AUTO"),
                        tgt,
                    )
                    app.status_message = f"✔ Loaded {tgt} translation from local cache."
                    if hasattr(app, "toasts"):
                        app.toasts.add(f"Translated to {tgt} (cached)", level=ToastLevel.INFO)
                    app._update_reader_viewport(force=True)
                    return app, None
                except Exception as exc:
                    logger.warning("Invalid cached translation JSON for %s: %s", m.id, exc)

            # 3. Check client configuration
            client = self.get_client()
            if not client.is_configured:
                msg = "⚠ DeepL Auth Key not configured. Configure it in Preferences > Plugins."
                app.status_message = msg
                if hasattr(app, "toasts"):
                    app.toasts.add("DeepL Auth Key not configured", level=ToastLevel.WARNING)
                return app, None

            # 4. Dispatch background TEA command
            app.status_message = f"🌐 Translating message with DeepL ({tgt})..."
            if hasattr(app, "toasts"):
                app.toasts.add(f"Translating with DeepL ({tgt})...", level=ToastLevel.INFO)

            cmd = self._make_translate_message_cmd(m, tgt)
            return app, cmd

        if action_id == "deepl_translate_draft":
            compose_modal = getattr(app, "active_modal", None)
            if not compose_modal or not hasattr(compose_modal, "input_subject"):
                app.status_message = "No active compose draft to translate."
                return app, None

            subj = compose_modal.input_subject.value
            body_field = getattr(compose_modal, "editor", getattr(compose_modal, "input_body", None))
            body = body_field.value if hasattr(body_field, "value") else ""
            if not subj.strip() and not body.strip():
                app.status_message = "Compose draft is empty."
                return app, None

            client = self.get_client()
            if not client.is_configured:
                msg = "⚠ DeepL Auth Key not configured. Configure it in Preferences > Plugins."
                app.status_message = msg
                if hasattr(app, "toasts"):
                    app.toasts.add("DeepL Auth Key not configured", level=ToastLevel.WARNING)
                return app, None

            tgt = self.target_lang
            app.status_message = f"🌐 Translating draft with DeepL ({tgt})..."
            if hasattr(app, "toasts"):
                app.toasts.add(f"Translating draft ({tgt})...", level=ToastLevel.INFO)

            cmd = self._make_translate_draft_cmd(subj, body, tgt)
            return app, cmd

        return app, None

    def _make_translate_message_cmd(self, message: EmailMessage, target_lang: str) -> Cmd:
        client = self.get_client()
        cache_days_str = self.context.get_setting("cache_days", "30")
        try:
            cache_days = int(cache_days_str)
        except ValueError:
            cache_days = 30
        ttl_seconds = float(cache_days * 86400) if cache_days > 0 else 0.0

        def _runner() -> Msg:
            try:
                # Translate subject and body
                texts_to_trans = [message.subject, message.body_text]
                res = client.translate_texts(texts_to_trans, target_lang=target_lang)
                if not res.success or len(res.texts) < 2:
                    return DeepLTranslationReadyMsg(
                        message_id=message.id,
                        translated_subject="",
                        translated_body="",
                        detected_source_lang="",
                        target_lang=target_lang,
                        error=res.error or "Failed to translate message",
                    )

                trans_subj, trans_body = res.texts[0], res.texts[1]
                # Cache result in SQLite
                cache_key = f"{message.id}:{target_lang}"
                cache_payload = json.dumps({
                    "subject": trans_subj,
                    "body": trans_body,
                    "detected": res.detected_source_lang,
                })
                self.context.set_cache(cache_key, cache_payload, ttl_seconds=ttl_seconds)

                return DeepLTranslationReadyMsg(
                    message_id=message.id,
                    translated_subject=trans_subj,
                    translated_body=trans_body,
                    detected_source_lang=res.detected_source_lang or "UNKNOWN",
                    target_lang=target_lang,
                )
            except Exception as exc:
                return DeepLTranslationReadyMsg(
                    message_id=message.id,
                    translated_subject="",
                    translated_body="",
                    detected_source_lang="",
                    target_lang=target_lang,
                    error=str(exc),
                )

        return _runner

    def _make_translate_draft_cmd(self, subject: str, body: str, target_lang: str) -> Cmd:
        client = self.get_client()

        def _runner() -> Msg:
            try:
                res = client.translate_texts([subject, body], target_lang=target_lang)
                if not res.success or len(res.texts) < 2:
                    return DeepLTranslationReadyMsg(
                        message_id="",
                        translated_subject="",
                        translated_body="",
                        detected_source_lang="",
                        target_lang=target_lang,
                        error=res.error or "Failed to translate draft",
                        is_draft=True,
                    )

                return DeepLTranslationReadyMsg(
                    message_id="",
                    translated_subject=res.texts[0],
                    translated_body=res.texts[1],
                    detected_source_lang=res.detected_source_lang or "UNKNOWN",
                    target_lang=target_lang,
                    is_draft=True,
                )
            except Exception as exc:
                return DeepLTranslationReadyMsg(
                    message_id="",
                    translated_subject="",
                    translated_body="",
                    detected_source_lang="",
                    target_lang=target_lang,
                    error=str(exc),
                    is_draft=True,
                )

        return _runner

    def on_message(self, msg: Any, app: Any) -> Cmd | None:
        if isinstance(msg, DeepLTranslationReadyMsg):
            self.handle_translation_ready(msg, app)
        return None

    def handle_translation_ready(self, msg: DeepLTranslationReadyMsg, app: Any) -> None:
        """Handle background translation completion message in the host TEA app."""
        if msg.error:
            app.status_message = f"⚠ DeepL translation failed: {msg.error}"
            if hasattr(app, "toasts"):
                app.toasts.add(f"Translation failed: {msg.error}", level=ToastLevel.WARNING)
            return

        if msg.is_draft:
            compose_modal = getattr(app, "active_modal", None)
            if compose_modal and hasattr(compose_modal, "input_subject"):
                if msg.translated_subject:
                    compose_modal.input_subject.value = msg.translated_subject
                if msg.translated_body:
                    if hasattr(compose_modal, "editor"):
                        compose_modal.editor.value = msg.translated_body
                    elif hasattr(compose_modal, "input_body"):
                        if hasattr(compose_modal.input_body, "set_content"):
                            compose_modal.input_body.set_content(msg.translated_body)
                        else:
                            compose_modal.input_body.value = msg.translated_body
                app.status_message = f"✔ Draft translated to {msg.target_lang} (from {msg.detected_source_lang})."
                if hasattr(app, "toasts"):
                    app.toasts.add(f"Draft translated to {msg.target_lang}", level=ToastLevel.INFO)
            return

        # Reading pane message translation
        self._active_translations[msg.message_id] = (
            msg.translated_subject,
            msg.translated_body,
            msg.detected_source_lang,
            msg.target_lang,
        )
        app.status_message = (
            f"✔ Translated to {msg.target_lang} (detected: {msg.detected_source_lang}). Press T to revert."
        )
        if hasattr(app, "toasts"):
            app.toasts.add(f"Translated to {msg.target_lang} (from {msg.detected_source_lang})", level=ToastLevel.INFO)
        app._update_reader_viewport(force=True)
