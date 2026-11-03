"""Comprehensive test suite for the EspressoMail plugin system and DeepL translation plugin."""

from __future__ import annotations

import json
import tempfile
import urllib.error
from pathlib import Path
from typing import Any, Sequence
from unittest.mock import MagicMock, patch

import unittest

from espresso import Cmd, KeyMsg, Msg
from espressomail.models.entities import EmailMessage
from espressomail.plugins.base import BasePlugin, PluginContext, PluginMetadata
from espressomail.plugins.deepl import DeepLPlugin, DeepLTranslationReadyMsg
from espressomail.plugins.deepl.client import DeepLClient, split_text_into_chunks
from espressomail.plugins.hooks import (
    ComposeAction,
    PluginActionMsg,
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)
from espressomail.plugins.manager import PluginManager
from espressomail.storage import LocalStore
from espressomail.themes.engine import THEMES, Theme
from espressomail.ui.app import EspressoMailApp
from espressomail.ui.menus import build_menu_categories
from espressomail.ui.modals import ComposeModal, PreferencesModal
from tests.test_storage_and_search import populate_test_store


class CrashingPlugin(BasePlugin):
    """Test plugin that deliberately throws in hooks to test fault isolation."""

    metadata = PluginMetadata(
        id="crasher",
        name="Crashing Plugin",
        version="0.1.0",
        description="Throws in all hooks",
    )

    def on_load(self) -> None:
        raise RuntimeError("Crasher failed in on_load")

    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        raise RuntimeError("Crasher failed in get_menu_items")

    def get_commands(self) -> Sequence[PluginCommand]:
        raise RuntimeError("Crasher failed in get_commands")

    def get_keybindings(self) -> dict[str, str]:
        raise RuntimeError("Crasher failed in get_keybindings")

    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        raise RuntimeError("Crasher failed in get_reader_actions")

    def transform_reader_content(self, message: EmailMessage, rendered_body: str, state: dict[str, Any]) -> str:
        raise RuntimeError("Crasher failed in transform_reader_content")

    def get_compose_actions(self, compose_modal: Any) -> Sequence[ComposeAction]:
        raise RuntimeError("Crasher failed in get_compose_actions")

    def on_action(self, action_id: str, app: Any, data: dict[str, Any]) -> tuple[Any, Cmd | None]:
        raise RuntimeError("Crasher failed in on_action")


class DummyPlugin(BasePlugin):
    """Simple plugin providing custom menu, command, reader action, and compose action."""

    metadata = PluginMetadata(
        id="dummy",
        name="Dummy Test Plugin",
        version="1.2.3",
        author="Tester",
        description="Sample plugin for unit testing",
    )

    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        return (
            PluginMenuItem(target_category="Tools", action_id="dummy_tool", label="Dummy Tool Action"),
        )

    def get_commands(self) -> Sequence[PluginCommand]:
        return (
            PluginCommand(
                action_id="dummy_cmd",
                title="Dummy: Run Dummy Command",
                description="Performs dummy test operation",
                category="Testing",
                default_key="ctrl+d",
            ),
        )

    def get_keybindings(self) -> dict[str, str]:
        return {"ctrl+d": "dummy_cmd"}

    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        return (
            ReaderActionPill(action_id="dummy_pill", label="Dummy Pill", shortcut="D"),
        )

    def transform_reader_content(self, message: EmailMessage, rendered_body: str, state: dict[str, Any]) -> str:
        return f"[DUMMY HEADER]\n{rendered_body}"

    def get_compose_actions(self, compose_modal: Any) -> Sequence[ComposeAction]:
        return (
            ComposeAction(action_id="dummy_compose", label="Dummy Draft Action", shortcut="Ctrl+K"),
        )

    def on_action(self, action_id: str, app: Any, data: dict[str, Any]) -> tuple[Any, Cmd | None]:
        if action_id == "dummy_cmd":
            app.status_message = "✔ Dummy command executed successfully!"
            return app, None
class BaseTestCase(unittest.TestCase):
    """Base test case initializing an in-memory LocalStore."""

    def setUp(self) -> None:
        self.store = LocalStore(":memory:")

    def tearDown(self) -> None:
        self.store.close()


class TestPluginSystemCore(BaseTestCase):
    """Test core plugin registration, discovery, persistence, and isolation."""

    def test_plugin_discovery_and_registration(self) -> None:
        manager = PluginManager(store=self.store)
        self.assertIn("deepl_translate", manager.plugins)
        deepl = manager.plugins["deepl_translate"]
        self.assertEqual(deepl.metadata.name, "DeepL Translation")
        self.assertTrue(manager.is_enabled("deepl_translate"))

    def test_enable_disable_persistence(self) -> None:
        manager = PluginManager(store=self.store)
        self.assertTrue(manager.is_enabled("deepl_translate"))

        manager.set_enabled("deepl_translate", False)
        self.assertFalse(manager.is_enabled("deepl_translate"))
        # Verify persistence in SQLite store
        self.assertEqual(self.store.get_setting("plugin:deepl_translate:enabled"), "0")

        # New manager instance reading the same store
        new_manager = PluginManager(store=self.store)
        self.assertFalse(new_manager.is_enabled("deepl_translate"))

        new_manager.set_enabled("deepl_translate", True)
        self.assertTrue(new_manager.is_enabled("deepl_translate"))
        self.assertEqual(self.store.get_setting("plugin:deepl_translate:enabled"), "1")

    def test_user_plugins_directory_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_user_dir:
            user_dir = Path(tmp_user_dir)
            custom_plugin_file = user_dir / "my_custom_plugin.py"
            custom_plugin_file.write_text(
                """
from espressomail.plugins.base import BasePlugin, PluginMetadata

class CustomUserPlugin(BasePlugin):
    metadata = PluginMetadata(id="user_custom", name="User Custom Plugin")
""",
                encoding="utf-8",
            )
            manager = PluginManager(store=self.store, user_plugins_dir=user_dir)
            self.assertIn("user_custom", manager.plugins)
            self.assertEqual(manager.plugins["user_custom"].metadata.name, "User Custom Plugin")

    def test_plugin_crash_isolation(self) -> None:
        manager = PluginManager(store=self.store)
        manager.register_plugin_class(CrashingPlugin)
        manager.register_plugin_class(DummyPlugin)

        # Hook aggregators should skip crashing plugin and return items from healthy plugins
        menu_items = manager.get_menu_items()
        self.assertTrue(any(item.action_id == "dummy_tool" for item in menu_items))

        commands = manager.get_commands()
        self.assertTrue(any(cmd.action_id == "dummy_cmd" for cmd in commands))

        keys = manager.get_keybindings()
        self.assertIn("ctrl+d", keys)

        # Reader content transform should not crash
        dummy_msg = EmailMessage(id="msg_1", account_id="acc_1", folder="INBOX", subject="Hi", body_text="Hello")
        out_text = manager.transform_reader_content(dummy_msg, "Body text", {})
        self.assertIn("[DUMMY HEADER]", out_text)

        # Dispatch action to crashing plugin should not crash
        app_mock = MagicMock()
        res_app, cmd = manager.dispatch_action("crasher_action", app_mock, plugin_id="crasher")
        self.assertEqual(res_app, app_mock)
        self.assertIsNone(cmd)

    def test_plugin_context_settings_and_cache(self) -> None:
        manager = PluginManager(store=self.store)
        ctx = PluginContext("test_p", self.store)

        # Settings
        self.assertEqual(ctx.get_setting("foo", "default"), "default")
        ctx.set_setting("foo", "bar")
        self.assertEqual(ctx.get_setting("foo"), "bar")

        # Cache with TTL
        ctx.set_cache("c_key", "c_val", ttl_seconds=3600.0)
        self.assertEqual(ctx.get_cache("c_key"), "c_val")
        ctx.clear_cache("c_key")
        self.assertIsNone(ctx.get_cache("c_key"))

        # Data directory
        data_dir = ctx.get_data_dir()
        self.assertTrue(data_dir.exists())
        self.assertTrue(data_dir.is_dir())


class TestDeepLClient(BaseTestCase):
    """Test DeepL API client routing, chunking, and error handling."""

    def test_api_endpoint_routing(self) -> None:
        free_client = DeepLClient(auth_key="abcd1234-5678:fx")
        self.assertTrue(free_client.is_free_tier)
        self.assertEqual(free_client.api_base_url, "https://api-free.deepl.com/v2")

        pro_client = DeepLClient(auth_key="abcd1234-5678-pro")
        self.assertFalse(pro_client.is_free_tier)
        self.assertEqual(pro_client.api_base_url, "https://api.deepl.com/v2")

    def test_split_text_into_chunks(self) -> None:
        short_text = "This is a brief message that fits well within a single chunk."
        chunks = split_text_into_chunks(short_text, max_chunk_bytes=1000)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], short_text)

        # Long text with multiple paragraphs
        para = "Lorem ipsum dolor sit amet, consectetur adipiscing elit."
        long_text = "\n\n".join([para] * 12)
        chunks = split_text_into_chunks(long_text, max_chunk_bytes=200)
        self.assertGreater(len(chunks), 1)
        # Verify joined text preserves content
        reconstructed = "\n\n".join(chunks)
        self.assertEqual(reconstructed.strip(), long_text.strip())

    @patch("urllib.request.urlopen")
    def test_translate_texts_success(self, mock_urlopen: MagicMock) -> None:
        client = DeepLClient(auth_key="test-key:fx", target_lang="EN-GB")
        api_response = {
            "translations": [
                {"detected_source_language": "ES", "text": "Hello world"},
                {"detected_source_language": "ES", "text": "This is a test message"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(api_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = client.translate_texts(["Hola mundo", "Este es un mensaje de prueba"], target_lang="EN-GB")
        self.assertTrue(res.success)
        self.assertEqual(res.detected_source_lang, "ES")
        self.assertEqual(len(res.texts), 2)
        self.assertEqual(res.texts[0], "Hello world")
        self.assertEqual(res.texts[1], "This is a test message")

    @patch("urllib.request.urlopen")
    def test_translate_texts_http_errors(self, mock_urlopen: MagicMock) -> None:
        client = DeepLClient(auth_key="bad-key:fx", target_lang="DE")

        # 403 Forbidden / Invalid Key
        err403 = urllib.error.HTTPError("https://api-free.deepl.com/v2/translate", 403, "Forbidden", {}, None)  # type: ignore[arg-type]
        mock_urlopen.side_effect = err403
        res = client.translate_texts(["Test"])
        self.assertFalse(res.success)
        self.assertIn("Invalid DeepL Auth Key", res.error)

        # 456 Quota Exceeded
        err456 = urllib.error.HTTPError("https://api-free.deepl.com/v2/translate", 456, "Quota Exceeded", {}, None)  # type: ignore[arg-type]
        mock_urlopen.side_effect = err456
        res = client.translate_texts(["Test"])
        self.assertFalse(res.success)
        self.assertIn("Quota exceeded", res.error)

        # 429 Too Many Requests
        err429 = urllib.error.HTTPError("https://api-free.deepl.com/v2/translate", 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]
        mock_urlopen.side_effect = err429
        res = client.translate_texts(["Test"])
        self.assertFalse(res.success)
        self.assertIn("Rate limit reached", res.error)


class TestDeepLPlugin(BaseTestCase):
    """Test DeepLPlugin lifecycle, reading pane translation, caching, and draft translation."""

    def test_deepl_unconfigured_auth_key_warning(self) -> None:
        manager = PluginManager(store=self.store)
        plugin: DeepLPlugin = manager.plugins["deepl_translate"]  # type: ignore[assignment]
        plugin.context.set_setting("auth_key", "")

        app_mock = MagicMock()
        app_mock.selected_message = EmailMessage(
            id="msg_1", account_id="acc_1", folder="INBOX", subject="S", body_text="B"
        )
        res_app, cmd = plugin.on_action("deepl_toggle_translate", app_mock, {})
        self.assertIsNone(cmd)
        self.assertIn("Auth Key not configured", app_mock.status_message)

    @patch("urllib.request.urlopen")
    def test_deepl_reading_pane_translate_and_revert_flow(self, mock_urlopen: MagicMock) -> None:
        manager = PluginManager(store=self.store)
        plugin: DeepLPlugin = manager.plugins["deepl_translate"]  # type: ignore[assignment]
        plugin.context.set_setting("auth_key", "test-key:fx")
        plugin.context.set_setting("target_lang", "EN-GB")

        msg = EmailMessage(
            id="msg_esp_1",
            account_id="acc_1",
            folder="INBOX",
            subject="Reunión del equipo",
            body_text="Hola a todos, la reunión es mañana a las diez.",
        )
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.selected_message_override = msg  # type: ignore[attr-defined]
        app.message_rows = [MagicMock(message=msg, thread=None)]
        app.message_cursor = 0

        # Mock DeepL API response
        api_response = {
            "translations": [
                {"detected_source_language": "ES", "text": "Team Meeting"},
                {"detected_source_language": "ES", "text": "Hello everyone, the meeting is tomorrow at ten."},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(api_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        # 1. Trigger translation via action
        app, cmd = plugin.on_action("deepl_toggle_translate", app, {})
        self.assertIsNotNone(cmd)
        self.assertTrue(callable(cmd))

        # 2. Execute background TEA command
        tea_msg = cmd()
        self.assertIsInstance(tea_msg, DeepLTranslationReadyMsg)
        self.assertEqual(tea_msg.detected_source_lang, "ES")
        self.assertEqual(tea_msg.translated_subject, "Team Meeting")

        # 3. Deliver tea_msg to app / plugin
        plugin.handle_translation_ready(tea_msg, app)
        self.assertIn("Translated to EN-GB", app.status_message)

        # 4. Verify reader action pill shows 'Show Original'
        pills = plugin.get_reader_actions(msg)
        self.assertTrue(any(p.label == "Show Original" for p in pills))

        # 5. Verify transform_reader_content displays translation banner and body
        transformed = plugin.transform_reader_content(msg, msg.body_text, {})
        self.assertIn("DeepL Translation: ES → EN-GB", transformed)
        self.assertIn("Hello everyone, the meeting is tomorrow at ten.", transformed)

        # 6. Verify 30-day cache was stored in SQLite
        cache_val = plugin.context.get_cache("msg_esp_1:EN-GB")
        self.assertIsNotNone(cache_val)
        cached_dict = json.loads(cache_val)  # type: ignore[arg-type]
        self.assertEqual(cached_dict["subject"], "Team Meeting")

        # 7. Pressing T again reverts to original
        app, revert_cmd = plugin.on_action("deepl_toggle_translate", app, {})
        self.assertIsNone(revert_cmd)
        self.assertIn("Reverted to original", app.status_message)
        reverted_text = plugin.transform_reader_content(msg, msg.body_text, {})
        self.assertEqual(reverted_text, msg.body_text)

        # 8. Translating again hits SQLite cache directly without network request
        mock_urlopen.reset_mock()
        app, cache_cmd = plugin.on_action("deepl_toggle_translate", app, {})
        self.assertIsNone(cache_cmd)  # Cache hit produces instant in-place switch!
        self.assertIn("local cache", app.status_message)
        mock_urlopen.assert_not_called()
        cached_transformed = plugin.transform_reader_content(msg, msg.body_text, {})
        self.assertIn("DeepL Translation: ES → EN-GB", cached_transformed)

    @patch("urllib.request.urlopen")
    def test_deepl_draft_translation_in_composer(self, mock_urlopen: MagicMock) -> None:
        manager = PluginManager(store=self.store)
        plugin: DeepLPlugin = manager.plugins["deepl_translate"]  # type: ignore[assignment]
        plugin.context.set_setting("auth_key", "test-key:fx")
        plugin.context.set_setting("target_lang", "DE")

        app = EspressoMailApp(store=self.store, show_splash=False)
        modal = ComposeModal(
            accounts=self.store.list_accounts(),
            contacts=self.store.list_contacts(),
            theme=app.theme,
            subject_initial="Good morning",
            body_initial="Please send me the quarterly report as soon as possible.",
        )
        app.active_modal = modal

        api_response = {
            "translations": [
                {"detected_source_language": "EN", "text": "Guten Morgen"},
                {"detected_source_language": "EN", "text": "Bitte senden Sie mir den Quartalsbericht so bald wie möglich."},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(api_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        # Trigger draft translation
        app, cmd = plugin.on_action("deepl_translate_draft", app, {})
        self.assertIsNotNone(cmd)
        tea_msg = cmd()  # type: ignore[misc]
        self.assertIsInstance(tea_msg, DeepLTranslationReadyMsg)
        self.assertTrue(tea_msg.is_draft)

        plugin.handle_translation_ready(tea_msg, app)
        self.assertEqual(modal.input_subject.value, "Guten Morgen")
        self.assertEqual(modal.editor.value, "Bitte senden Sie mir den Quartalsbericht so bald wie möglich.")

    def test_deepl_cycle_target_lang(self) -> None:
        manager = PluginManager(store=self.store)
        plugin: DeepLPlugin = manager.plugins["deepl_translate"]  # type: ignore[assignment]
        plugin.context.set_setting("target_lang", "EN-GB")

        app_mock = MagicMock()
        plugin.on_action("deepl_cycle_target_lang", app_mock, {})
        self.assertNotEqual(plugin.target_lang, "EN-GB")
        self.assertIn("DeepL target language switched", app_mock.status_message)

    @patch("urllib.request.urlopen")
    def test_deepl_translates_html_email_when_body_text_is_empty(self, mock_urlopen: MagicMock) -> None:
        """Verify HTML-only email (empty body_text) is properly extracted and translated."""
        app = EspressoMailApp(store=self.store, show_splash=False)
        plugin: DeepLPlugin = app.plugin_manager.plugins["deepl_translate"]  # type: ignore[assignment]
        plugin.context.set_setting("auth_key", "test-key:fx")
        plugin.context.set_setting("target_lang", "DA")
        html_msg = EmailMessage(
            id="msg_html_1",
            account_id="acc_1",
            folder="INBOX",
            subject="Backup failed",
            body_text="",  # Empty text body
            body_html="<p>Data backup task on Kvisten80nas failed.</p><p>Go to Log.</p>",
        )
        app.selected_message_override = html_msg  # type: ignore[attr-defined]
        app.message_rows = [MagicMock(message=html_msg, thread=None)]
        app.message_cursor = 0

        api_response = {
            "translations": [
                {"detected_source_language": "EN", "text": "Sikkerhedskopi mislykkedes"},
                {"detected_source_language": "EN", "text": "Sikkerhedskopieringsopgaven på Kvisten80nas mislykkedes.\n\nGå til log."},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(api_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        # Trigger translation
        app, cmd = plugin.on_action("deepl_toggle_translate", app, {})
        self.assertIsNotNone(cmd)
        tea_msg = cmd()  # type: ignore[misc]

        self.assertIsInstance(tea_msg, DeepLTranslationReadyMsg)
        self.assertEqual(tea_msg.translated_subject, "Sikkerhedskopi mislykkedes")
        self.assertIn("Sikkerhedskopieringsopgaven på Kvisten80nas mislykkedes", tea_msg.translated_body)

        # Deliver message and verify viewport content
        plugin.handle_translation_ready(tea_msg, app)
        self.assertIn("Translated to DA", app.status_message)

        rendered_reading_pane = "\n".join(app.reader_viewport.lines)
        self.assertIn("Sikkerhedskopi mislykkedes", rendered_reading_pane)
        self.assertIn("Sikkerhedskopieringsopgaven på Kvisten80nas", rendered_reading_pane)
        self.assertIn("Gå til log.", rendered_reading_pane)

    def test_ctrl_t_shortcut_and_no_collision_with_theme_toggle(self) -> None:
        """Verify Ctrl+T triggers translation and T continues to cycle theme without collision."""
        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)

        # 1. T key cycles theme
        init_theme = app.theme.id
        app, _ = app.update(KeyMsg("T"))
        self.assertNotEqual(app.theme.id, init_theme)
        self.assertIn("Theme switched to", app.status_message)

        # 2. Ctrl+T key triggers DeepL translation action
        init_status = app.status_message
        app, cmd = app.update(KeyMsg("ctrl+t"))
        # Unconfigured DeepL key warns in status_message without crash
        self.assertTrue(
            "DeepL" in app.status_message
            or "Translate" in app.status_message
            or "Auth Key" in app.status_message
        )



class TestPluginUIIntegration(BaseTestCase):
    """Test MenuBar, CommandPalette, ReadingPane, and Preferences integration."""

    def test_menu_bar_merges_plugin_items(self) -> None:
        manager = PluginManager(store=self.store)
        manager.register_plugin_class(DummyPlugin)

        categories = build_menu_categories(manager)
        tools_cat = next((c for c in categories if c.title == "Tools"), None)
        self.assertIsNotNone(tools_cat)
        action_ids = [item.action_id for item in tools_cat.items]  # type: ignore[union-attr]
        self.assertIn("dummy_tool", action_ids)
        self.assertIn("deepl_translate_draft", action_ids)

        mail_cat = next((c for c in categories if c.title == "Mail"), None)
        self.assertIsNotNone(mail_cat)
        mail_action_ids = [item.action_id for item in mail_cat.items]  # type: ignore[union-attr]
        self.assertIn("deepl_toggle_translate", mail_action_ids)

    def test_command_palette_includes_plugin_commands(self) -> None:
        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        palette = app.command_palette
        palette_ids = [item.id for item in palette.items]

        self.assertIn("deepl_toggle_translate", palette_ids)
        self.assertIn("deepl_translate_draft", palette_ids)
        self.assertIn("deepl_cycle_target_lang", palette_ids)

    def test_preferences_modal_tab_5_plugins_and_save(self) -> None:
        populate_test_store(self.store)
        manager = PluginManager(store=self.store)
        modal = PreferencesModal(
            store=self.store,
            current_theme=THEMES["espresso"],
            download_dir="/tmp/dl",
            plugin_manager=manager,
        )

        # Switch to tab 5 (Plugins)
        modal, _ = modal.update(KeyMsg("5"))
        self.assertEqual(modal.active_tab, 4)
        view_text = modal.view(80, 24)
        self.assertIn("5. Plugins", view_text)
        self.assertIn("DeepL Translation", view_text)
        self.assertIn("Default Target Langua:", view_text)
        self.assertIn("API Auth Key:", view_text)

        # Toggle enabled state (row 2)
        initial_en = modal.plugin_enabled.get("deepl_translate", True)
        modal._cycle_row(2, delta=1)
        self.assertNotEqual(modal.plugin_enabled["deepl_translate"], initial_en)

        # Cycle target language (row 4)
        init_lang_idx = modal.deepl_target_lang_idx
        modal._cycle_row(4, delta=1)
        self.assertNotEqual(modal.deepl_target_lang_idx, init_lang_idx)

        # Enter DeepL auth key
        modal.input_plugin_auth_key.value = "my-secret-key:fx"

        # Cycle row 1 to switch to AI Email Assistant plugin
        while modal.plugin_list[modal.plugin_idx].metadata.id != "ai_assistant":
            modal._cycle_row(1, delta=1)

        ai_view = modal.view(80, 24)
        self.assertIn("AI Email Assistant", ai_view)
        self.assertIn("AI Provider:", ai_view)
        self.assertIn("API Base URL:", ai_view)
        self.assertIn("Model Name:", ai_view)
        self.assertIn("API Key (OpenAI / Ant:", ai_view)
        self.assertNotIn("Default Target Langua:", ai_view)

        # Cycle AI provider (row 3: ollama -> openai) and set AI API key (row 6)
        modal._cycle_row(3, delta=1)
        modal.plugin_text_inputs[("ai_assistant", "api_key")].value = "sk-test-ai-key"
        modal.plugin_text_inputs[("ai_assistant", "model")].value = "gpt-4o-mini"

        # Cycle row 1 to switch to LanguageTool plugin
        while modal.plugin_list[modal.plugin_idx].metadata.id != "languagetool":
            modal._cycle_row(1, delta=1)

        lt_view = modal.view(80, 24)
        self.assertIn("LanguageTool Grammar Check", lt_view)
        self.assertIn("LanguageTool API Endp:", lt_view)
        self.assertIn("Language Code:", lt_view)
        self.assertIn("API Key (Optional / P:", lt_view)
        modal.plugin_text_inputs[("languagetool", "api_key")].value = "lt-plus-key-99"
        modal.plugin_text_inputs[("languagetool", "language")].value = "en-GB"

        # Save preferences
        modal, cmd = modal._save_preferences()
        self.assertIsNotNone(cmd)
        saved_msg = cmd()  # type: ignore[misc]

        # Verify persistence in SQLite store across all edited plugins
        self.assertEqual(self.store.get_plugin_setting("deepl_translate", "auth_key"), "my-secret-key:fx")
        expected_lang = modal.DEEPL_TARGET_LANGS[modal.deepl_target_lang_idx][0]
        self.assertEqual(self.store.get_plugin_setting("deepl_translate", "target_lang"), expected_lang)
        self.assertEqual(
            self.store.get_setting("plugin:deepl_translate:enabled"),
            "1" if modal.plugin_enabled["deepl_translate"] else "0",
        )
        self.assertEqual(self.store.get_plugin_setting("ai_assistant", "provider"), "openai")
        self.assertEqual(self.store.get_plugin_setting("ai_assistant", "api_key"), "sk-test-ai-key")
        self.assertEqual(self.store.get_plugin_setting("ai_assistant", "model"), "gpt-4o-mini")
        self.assertEqual(self.store.get_plugin_setting("languagetool", "api_key"), "lt-plus-key-99")
        self.assertEqual(self.store.get_plugin_setting("languagetool", "language"), "en-GB")

    def test_compose_modal_plugin_integration(self) -> None:
        manager = PluginManager(store=self.store)
        modal = ComposeModal(
            accounts=self.store.list_accounts(),
            contacts=self.store.list_contacts(),
            theme=THEMES["espresso"],
            plugin_manager=manager,
        )
        view_out = modal.view(80, 24)
        self.assertIn("[Ctrl+T] Translate", view_out)

        # Pressing Ctrl+T emits PluginActionMsg
        modal, cmd = modal.update(KeyMsg("ctrl+t"))
        self.assertIsNotNone(cmd)
        emitted = cmd()  # type: ignore[misc]
        self.assertIsInstance(emitted, PluginActionMsg)
        self.assertEqual(emitted.action_id, "deepl_translate_draft")

        # Pressing Ctrl+B emits AI Polish action
        modal, cmd_b = modal.update(KeyMsg("ctrl+b"))
        self.assertIsNotNone(cmd_b)
        emitted_b = cmd_b()  # type: ignore[misc]
        self.assertIsInstance(emitted_b, PluginActionMsg)
        self.assertEqual(emitted_b.action_id, "ai_polish_draft")

        # Pressing Ctrl+L emits LanguageTool check action
        modal, cmd_l = modal.update(KeyMsg("ctrl+l"))
        self.assertIsNotNone(cmd_l)
        emitted_l = cmd_l()  # type: ignore[misc]
        self.assertIsInstance(emitted_l, PluginActionMsg)
        self.assertEqual(emitted_l.action_id, "languagetool_check_draft")


class TestSecurityAndUtilityPlugins(BaseTestCase):
    """Test DMARC Inspector, Phishing Scanner, Unsubscribe, AI Assistant, LanguageTool, and URL Launcher plugins."""

    def test_all_builtin_plugins_discovered(self) -> None:
        manager = PluginManager(store=self.store)
        expected = {
            "deepl_translate",
            "dmarc_inspector",
            "phishing_scanner",
            "unsubscribe",
            "ai_assistant",
            "languagetool",
            "url_launcher",
        }
        self.assertTrue(expected.issubset(set(manager.plugins.keys())))

    def test_dmarc_inspector_headers_and_xml_gz_zip_reports(self) -> None:
        import gzip
        import io
        import zipfile
        from espressomail.models.entities import Attachment
        from espressomail.plugins.dmarc_inspector import (
            DMARCInspectorPlugin,
            extract_dmarc_reports_from_attachment,
            parse_auth_headers,
        )

        headers = {
            "Authentication-Results": "mx.google.com; dkim=pass header.i=@example.com; spf=pass; dmarc=pass (p=REJECT)",
            "Received-SPF": "pass (google.com: domain of alice@example.com designates 192.0.2.1 as permitted sender)",
        }
        auth = parse_auth_headers(headers)
        self.assertEqual(auth.spf, "pass")
        self.assertEqual(auth.dkim, "pass")
        self.assertEqual(auth.dmarc, "pass")
        self.assertEqual(auth.dkim_domain, "example.com")
        self.assertIn("✔ SPF", auth.overall_badge)

        sample_xml = b"""<?xml version="1.0" encoding="UTF-8" ?>
        <feedback>
          <report_metadata>
            <org_name>google.com</org_name>
            <report_id>123456789</report_id>
            <date_range><begin>1700000000</begin><end>1700086400</end></date_range>
          </report_metadata>
          <policy_published>
            <domain>example.com</domain>
            <p>reject</p>
            <sp>reject</sp>
            <pct>100</pct>
          </policy_published>
          <record>
            <row>
              <source_ip>192.0.2.44</source_ip>
              <count>42</count>
              <policy_evaluated>
                <disposition>none</disposition>
                <dkim>pass</dkim>
                <spf>pass</spf>
              </policy_evaluated>
            </row>
            <identifiers><header_from>example.com</header_from></identifiers>
          </record>
        </feedback>"""

        # Test .xml.gz decompression
        gz_bytes = gzip.compress(sample_xml)
        att_gz = Attachment(
            id="att_gz",
            message_id="m1",
            filename="google.com!example.com!1700000000!1700086400.xml.gz",
            content_type="application/gzip",
            data=gz_bytes,
        )
        reports_gz = extract_dmarc_reports_from_attachment(att_gz)
        self.assertEqual(len(reports_gz), 1)
        self.assertEqual(reports_gz[0].org_name, "google.com")
        self.assertEqual(reports_gz[0].domain, "example.com")
        self.assertEqual(reports_gz[0].total_messages, 42)
        self.assertEqual(reports_gz[0].passed_messages, 42)

        # Test .zip archive extraction
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("dmarc_report.xml", sample_xml)
        att_zip = Attachment(
            id="att_zip",
            message_id="m1",
            filename="report.zip",
            content_type="application/zip",
            data=zip_buf.getvalue(),
        )
        reports_zip = extract_dmarc_reports_from_attachment(att_zip)
        self.assertEqual(len(reports_zip), 1)
        self.assertEqual(reports_zip[0].records[0].source_ip, "192.0.2.44")

        # Verify plugin rendering in reading pane
        manager = PluginManager(store=self.store)
        plugin: DMARCInspectorPlugin = manager.plugins["dmarc_inspector"]  # type: ignore[assignment]
        msg = EmailMessage(
            id="m_dmarc",
            account_id="acc_1",
            subject="Report Domain: example.com",
            body_text="DMARC report attached.",
            raw_headers=headers,
            attachments=[att_gz],
        )
        transformed = plugin.transform_reader_content(msg, "DMARC report attached.", {})
        self.assertIn("DMARC Aggregate Report: example.com", transformed)
        self.assertIn("192.0.2.44", transformed)

    def test_phishing_scanner_detection_rules(self) -> None:
        from espressomail.plugins.phishing_scanner import (
            PhishingScannerPlugin,
            scan_message_for_phishing,
        )

        phish_msg = EmailMessage(
            id="m_phish",
            account_id="acc_1",
            sender_name="support@paypal.com",
            sender_email="attacker@evil-domain.ru",
            subject="Urgent Account Verification",
            raw_headers={
                "Reply-To": "harvest@another-shady-site.org",
                "Authentication-Results": "mx.example.com; spf=fail; dmarc=fail",
            },
            body_html=(
                '<p>Click <a href="http://198.51.100.77/login">https://www.paypal.com/signin</a> '
                'or <a href="https://xn--80ak6aa92e.com/verify">Verify Now</a></p>'
            ),
        )
        warnings = scan_message_for_phishing(phish_msg)
        rule_ids = {w.rule_id for w in warnings}
        self.assertIn("display_name_spoof", rule_ids)
        self.assertIn("reply_to_mismatch", rule_ids)
        self.assertIn("auth_failure", rule_ids)
        self.assertIn("mismatched_anchor_url", rule_ids)
        self.assertIn("raw_ip_url", rule_ids)
        self.assertIn("punycode_domain", rule_ids)

        manager = PluginManager(store=self.store)
        plugin: PhishingScannerPlugin = manager.plugins["phishing_scanner"]  # type: ignore[assignment]
        pills = plugin.get_reader_actions(phish_msg)
        self.assertEqual(len(pills), 1)
        self.assertIn("Warning", pills[0].badge)

        rendered = plugin.transform_reader_content(phish_msg, "Body content", {})
        self.assertIn("SECURITY WARNING", rendered)
        self.assertIn("Deceptive link", rendered)

    @patch("urllib.request.urlopen")
    def test_unsubscribe_plugin_rfc8058_and_mailto(self, mock_urlopen: MagicMock) -> None:
        from espressomail.plugins.unsubscribe import (
            UnsubscribeCompletedMsg,
            UnsubscribePlugin,
            parse_unsubscribe_headers,
        )

        headers = {
            "List-Unsubscribe": "<mailto:unsub@news.example.com?subject=leave>, <https://news.example.com/unsub?id=99>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
        target = parse_unsubscribe_headers(headers)
        self.assertTrue(target.is_available)
        self.assertTrue(target.one_click_post)
        self.assertEqual(target.http_url, "https://news.example.com/unsub?id=99")
        self.assertEqual(target.mailto_address, "unsub@news.example.com")
        self.assertEqual(target.mailto_subject, "leave")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        manager = PluginManager(store=self.store)
        plugin: UnsubscribePlugin = manager.plugins["unsubscribe"]  # type: ignore[assignment]
        app = EspressoMailApp(store=self.store, show_splash=False)
        msg = EmailMessage(
            id="m_unsub",
            account_id="acc_1",
            subject="Weekly Newsletter",
            raw_headers=headers,
        )
        app.message_rows = [MagicMock(message=msg, thread=None)]
        app.message_cursor = 0

        app, cmd = plugin.on_action("unsubscribe_current_email", app, {})
        self.assertIsNotNone(cmd)
        res_msg = cmd()  # type: ignore[misc]
        self.assertIsInstance(res_msg, UnsubscribeCompletedMsg)
        self.assertTrue(res_msg.success)

        plugin.on_message(res_msg, app)
        self.assertIn("Successfully unsubscribed", app.status_message)

    @patch("urllib.request.urlopen")
    def test_ai_assistant_summarize_and_polish_draft(self, mock_urlopen: MagicMock) -> None:
        from espressomail.plugins.ai_assistant import AIAssistantPlugin, AISummaryReadyMsg

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"message": {"content": "• TL;DR: Q4 budget review on Friday.\n• Action Items: Send slides by Thursday."}}
        ).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        manager = PluginManager(store=self.store)
        plugin: AIAssistantPlugin = manager.plugins["ai_assistant"]  # type: ignore[assignment]
        app = EspressoMailApp(store=self.store, show_splash=False)
        msg = EmailMessage(
            id="m_ai_1",
            account_id="acc_1",
            subject="Q4 Budget Review",
            body_text="Hi team, let us meet Friday to finalize the Q4 budget. Please send your slides by Thursday.",
        )
        app.message_rows = [MagicMock(message=msg, thread=None)]
        app.message_cursor = 0

        app, cmd = plugin.on_action("ai_summarize_email", app, {})
        self.assertIsNotNone(cmd)
        ai_msg = cmd()  # type: ignore[misc]
        self.assertIsInstance(ai_msg, AISummaryReadyMsg)
        self.assertIn("TL;DR", ai_msg.summary_text)

        plugin.on_message(ai_msg, app)
        rendered = plugin.transform_reader_content(msg, msg.body_text, {})
        self.assertIn("AI Summary & Action Items", rendered)
        self.assertIn("Q4 budget review on Friday", rendered)

    @patch("urllib.request.urlopen")
    def test_languagetool_plugin_grammar_check(self, mock_urlopen: MagicMock) -> None:
        from espressomail.plugins.languagetool import (
            GrammarMatch,
            LanguageToolPlugin,
            LanguageToolResultMsg,
            apply_languagetool_matches,
        )

        # Direct offset replacement test
        sample = "This are a Singel test."
        matches = [
            GrammarMatch(offset=5, length=3, message="Use 'is'", replacement="is"),
            GrammarMatch(offset=11, length=6, message="Spelling error", replacement="single"),
        ]
        fixed, count = apply_languagetool_matches(sample, matches)
        self.assertEqual(count, 2)
        self.assertEqual(fixed, "This is a single test.")

        # Full plugin flow in ComposeModal
        api_payload = {
            "language": {"code": "en-US"},
            "matches": [
                {
                    "offset": 5,
                    "length": 3,
                    "message": "Subject-verb agreement",
                    "replacements": [{"value": "is"}],
                    "rule": {"id": "EN_AGREEMENT"},
                }
            ],
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        manager = PluginManager(store=self.store)
        plugin: LanguageToolPlugin = manager.plugins["languagetool"]  # type: ignore[assignment]
        app = EspressoMailApp(store=self.store, show_splash=False)
        modal = ComposeModal(
            accounts=self.store.list_accounts(),
            contacts=self.store.list_contacts(),
            theme=app.theme,
            body_initial="This are a draft.",
            plugin_manager=manager,
        )
        app.active_modal = modal

        app, cmd = plugin.on_action("languagetool_check_draft", app, {})
        self.assertIsNotNone(cmd)
        lt_msg = cmd()  # type: ignore[misc]
        self.assertIsInstance(lt_msg, LanguageToolResultMsg)
        self.assertEqual(lt_msg.applied_count, 1)

        plugin.on_message(lt_msg, app)
        self.assertEqual(modal.editor.value, "This is a draft.")

    @patch("webbrowser.open")
    def test_url_launcher_extraction_and_open(self, mock_wb_open: MagicMock) -> None:
        from espressomail.plugins.url_launcher import (
            URLLauncherPlugin,
            extract_links_from_message,
        )

        msg = EmailMessage(
            id="m_links",
            account_id="acc_1",
            subject="Project Links",
            body_text="Docs: https://docs.example.com/guide and repo https://github.com/kimusan/espressomail.",
            body_html=(
                '<a href="https://docs.example.com/guide">Documentation Guide</a> '
                '<img src="https://track.example.com/pixel.gif" />'
            ),
        )
        links = extract_links_from_message(msg)
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0].url, "https://docs.example.com/guide")
        self.assertEqual(links[0].label, "Documentation Guide")
        self.assertEqual(links[1].url, "https://github.com/kimusan/espressomail")

        manager = PluginManager(store=self.store)
        plugin: URLLauncherPlugin = manager.plugins["url_launcher"]  # type: ignore[assignment]
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.message_rows = [MagicMock(message=msg, thread=None)]
        app.message_cursor = 0

        # Toggle links table on
        app, _ = plugin.on_action("url_launcher_toggle", app, {})
        rendered = plugin.transform_reader_content(msg, msg.body_text, {})
        self.assertIn("Extracted Links (2)", rendered)
        self.assertIn("Documentation Guide", rendered)

        # Open selected link #1
        app, _ = plugin.on_action("url_launcher_open_selected", app, {})
        mock_wb_open.assert_called_once_with("https://docs.example.com/guide")

