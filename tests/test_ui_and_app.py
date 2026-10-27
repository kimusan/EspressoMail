"""Unit and integration tests for the 3-pane EspressoMail TUI, themes, splash, menus, onboarding, and account settings."""

from __future__ import annotations

import tempfile
import unittest

from espresso import KeyMsg, MouseAction, MouseButton, MouseMsg, WindowSizeMsg
from espressomail.models import AccountConfig
from espressomail.storage import LocalStore
from espressomail.themes import ThemeEngine
from espressomail.ui import (
     PROVIDER_PRESETS,
    AccountSettingsModal,
    EspressoMailApp,
    ImageRenderBackend,
    OnboardingWizard,
    SplashDoneMsg,
    SplashScreen,
    SplashTickMsg,
    TerminalImageRenderer,
    html_to_terminal_text,
)
from tests.test_storage_and_search import populate_test_store


class TestUIAndApp(unittest.TestCase):
    def setUp(self) -> None:
        self.store = LocalStore(":memory:")

    def tearDown(self) -> None:
        self.store.close()

    def test_theme_engine_cycling(self) -> None:
        engine = ThemeEngine("espresso")
        self.assertEqual(engine.current.id, "espresso")
        seen = {engine.current.id}
        for _ in range(len(engine.list_themes())):
            seen.add(engine.next_theme().id)
        self.assertEqual(len(seen), 6)

    def test_splash_screen_progression(self) -> None:
        theme = ThemeEngine("espresso").current
        splash = SplashScreen(theme, total_ticks=4)
        for _ in range(3):
            splash, cmd = splash.update(SplashTickMsg())
            self.assertFalse(splash.done)
        splash, cmd = splash.update(SplashTickMsg())
        self.assertTrue(splash.done)
        assert cmd is not None
        self.assertIsInstance(cmd(), SplashDoneMsg)

    def test_onboarding_custom_imap_first_and_credential_validation(self) -> None:
        # Verify "Custom IMAP / SMTP Server" is the very first option and there are no demo presets
        self.assertTrue(PROVIDER_PRESETS[0].is_custom)
        self.assertIn("Custom IMAP", PROVIDER_PRESETS[0].label)
        for preset in PROVIDER_PRESETS:
            self.assertNotIn("Demo", preset.label)

        theme = ThemeEngine("espresso").current

        # Validator that rejects bad-password and accepts valid-password
        def fake_validator(acc: AccountConfig) -> tuple[bool, str]:
            if acc.imap_password != "valid-secret-99":
                return False, "IMAP authentication failed: Invalid credentials"
            return True, "Verified IMAP & SMTP credentials"

        wiz = OnboardingWizard(theme, allow_cancel=True, credential_validator=fake_validator)

        # Press Enter immediately on Step 1 -> selects first option ("Custom IMAP / SMTP Server")
        wiz, _ = wiz.update(KeyMsg("enter"))
        self.assertEqual(wiz.step, 2)
        self.assertTrue(wiz.selected_preset.is_custom)

        # Fill Step 2 with wrong password first
        wiz.input_account_name.value = "My Self-Hosted Mail"
        wiz.input_full_name.value = "Kim Schulz"
        wiz.input_email.value = "kim@schulz.dk"
        wiz.input_username.value = "kim@schulz.dk"
        wiz.input_password.value = "wrong-password"
        wiz.step2_focus = 4
        wiz, _ = wiz.update(KeyMsg("enter"))
        self.assertEqual(wiz.step, 3)
        # Sensible defaults derived from domain when Custom IMAP is used
        self.assertEqual(wiz.input_imap_host.value, "imap.schulz.dk")
        self.assertEqual(wiz.input_smtp_host.value, "smtp.schulz.dk")

        # Try to finish Step 3 with invalid password -> must fail validation and NOT finish
        wiz, cmd = wiz.update(KeyMsg("enter"))
        self.assertIsNone(cmd)
        self.assertIn("Invalid credentials", wiz.error_msg)

        # Go back to Step 2 with Esc, update password, advance to Step 3, and finish
        wiz, _ = wiz.update(KeyMsg("esc"))
        self.assertEqual(wiz.step, 2)
        wiz.input_password.value = "valid-secret-99"
        wiz.step2_focus = 4
        wiz, _ = wiz.update(KeyMsg("enter"))
        self.assertEqual(wiz.step, 3)

        wiz, cmd = wiz.update(KeyMsg("enter"))
        self.assertIsNotNone(cmd)
        assert cmd is not None
        done_msg = cmd()
        self.assertIsNotNone(done_msg.account)
        self.assertEqual(done_msg.account.email, "kim@schulz.dk")
        self.assertEqual(done_msg.account.imap_password, "valid-secret-99")

    def test_account_settings_modal_edit_password_and_servers(self) -> None:
        populate_test_store(self.store)
        app = EspressoMailApp(
            store=self.store,
            show_splash=False,
            credential_validator=lambda acc: (
                (True, "Verified") if acc.imap_password == "new-updated-pass!" else (False, "Bad password")
            ),
        )
        # Select acc_work and open AccountSettingsModal with 'A'
        app.active_account_id = "acc_work"
        app, _ = app.update(KeyMsg("A"))
        self.assertIsInstance(app.active_modal, AccountSettingsModal)
        modal = app.active_modal
        assert isinstance(modal, AccountSettingsModal)
        self.assertEqual(modal.current_account.id, "acc_work")

        # Try saving with invalid password -> stays open and shows validation error
        modal.input_password.value = "wrong"
        app, _ = app.update(KeyMsg("ctrl+s"))
        self.assertIsInstance(app.active_modal, AccountSettingsModal)
        self.assertIn("Bad password", modal.error_msg)

        # Update password and IMAP host, then save with Ctrl+S -> validates, persists to store, closes modal, and triggers IMAP sync
        modal.input_password.value = "new-updated-pass!"
        modal.input_imap_host.value = "mail2.schulz.dk"
        app, sync_cmd = app.update(KeyMsg("ctrl+s"))
        self.assertIsNone(app.active_modal)
        self.assertEqual(sync_cmd, app._sync_accounts_cmd)
        self.assertIn("Synchronizing", app.status_message)

        updated_acc = self.store.get_account("acc_work")
        assert updated_acc is not None
        self.assertEqual(updated_acc.imap_password, "new-updated-pass!")
        self.assertEqual(updated_acc.smtp_password, "new-updated-pass!")
        self.assertEqual(updated_acc.imap_host, "mail2.schulz.dk")

    def test_image_renderer_backends_and_html_formatter(self) -> None:
        populate_test_store(self.store)
        msg = self.store.get_message("msg_thread1_1")
        assert msg is not None
        img_att = msg.attachments[0]
        theme = ThemeEngine("espresso").current
        renderer = TerminalImageRenderer(ImageRenderBackend.HALF_BLOCK)

        for mode in (
            ImageRenderBackend.HALF_BLOCK,
            ImageRenderBackend.BRAILLE,
            ImageRenderBackend.SIXEL,
            ImageRenderBackend.KITTY,
            ImageRenderBackend.OFF,
        ):
            renderer.set_backend(mode)
            out = renderer.render_attachment(img_att, theme, max_width=40, max_height=8)
            self.assertTrue(len(out) > 10)

        converted = html_to_terminal_text("<h2>Release Notes</h2><ul><li>Item A</li></ul>")
        self.assertIn("## Release Notes", converted)
        self.assertIn("• Item A", converted)

    def test_3pane_app_keyboard_mouse_and_modals(self) -> None:
        populate_test_store(self.store)
        with tempfile.TemporaryDirectory() as tmpdir:
            app = EspressoMailApp(
                store=self.store,
                show_splash=False,
                download_dir=tmpdir,
            )
            app, _ = app.update(WindowSizeMsg(125, 36))

            # Verify 3-pane initial state
            view = app.view()
            self.assertIn("Mailboxes", view)
            self.assertIn("Reading Pane", view)

            # Cycle theme with 'T' and image mode with 'i'
            app, _ = app.update(KeyMsg("T"))
            self.assertEqual(app.theme.id, "catppuccin")
            app, _ = app.update(KeyMsg("i"))
            self.assertEqual(app.image_renderer.backend, ImageRenderBackend.CHAFA)

            # Toggle threading with 't'
            app, _ = app.update(KeyMsg("t"))
            self.assertFalse(app.threading_enabled)
            app, _ = app.update(KeyMsg("t"))
            self.assertTrue(app.threading_enabled)

            # Download attachments with 'w' (select msg_thread1_1 which has attachments)
            for idx, r in enumerate(app.message_rows):
                if r.message.id == "msg_thread1_1":
                    app.message_cursor = idx
                    break
            app, _ = app.update(KeyMsg("w"))
            self.assertIn("Saved", app.status_message)

            # Open Reply modal with 'r', then save as Draft with 'ctrl+s'
            app, _ = app.update(KeyMsg("r"))
            self.assertIsNotNone(app.active_modal)
            app, _ = app.update(KeyMsg("ctrl+s"))
            self.assertIsNone(app.active_modal)
            self.assertIn("Saved draft", app.status_message)

            # Mouse interaction: click top menu bar to open File menu
            app, _ = app.update(MouseMsg(x=20, y=0, button=MouseButton.LEFT, action=MouseAction.PRESS))
            self.assertTrue(app.menu_bar.is_open)
            # Click outside to close
            app, _ = app.update(MouseMsg(x=80, y=15, button=MouseButton.LEFT, action=MouseAction.PRESS))
            self.assertFalse(app.menu_bar.is_open)

            # Mouse click in Folder pane (left) and Reading pane (right)
            app, _ = app.update(MouseMsg(x=5, y=4, button=MouseButton.LEFT, action=MouseAction.PRESS))
            self.assertEqual(app.focused_pane, "folders")
            app, _ = app.update(MouseMsg(x=95, y=10, button=MouseButton.LEFT, action=MouseAction.PRESS))
            self.assertEqual(app.focused_pane, "reader")


if __name__ == "__main__":
    unittest.main()
