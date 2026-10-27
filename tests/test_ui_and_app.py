"""Unit and integration tests for the 3-pane EspressoMail TUI, themes, splash, menus, onboarding, and modals."""

from __future__ import annotations

import tempfile
import unittest

from espresso import KeyMsg, MouseAction, MouseButton, MouseMsg, WindowSizeMsg
from espressomail.storage import LocalStore
from espressomail.themes import ThemeEngine
from espressomail.ui import (
    EspressoMailApp,
    ImageRenderBackend,
    OnboardingWizard,
    SplashDoneMsg,
    SplashScreen,
    SplashTickMsg,
    TerminalImageRenderer,
    html_to_terminal_text,
)


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

    def test_onboarding_wizard_account_creation(self) -> None:
        theme = ThemeEngine("espresso").current
        wiz = OnboardingWizard(theme, allow_cancel=True)
        # Select Fastmail (index 1)
        wiz, _ = wiz.update(KeyMsg("down"))
        wiz, _ = wiz.update(KeyMsg("enter"))
        self.assertEqual(wiz.step, 2)

        # Fill Step 2: Account Name -> Full Name -> Email -> Password
        wiz.input_account_name.value = "Fastmail Personal"
        wiz.input_full_name.value = "Kim Schulz"
        wiz.input_email.value = "kim@fastmail.com"
        wiz.input_password.value = "app-secret-123"
        wiz.step2_focus = 3
        wiz, _ = wiz.update(KeyMsg("enter"))
        self.assertEqual(wiz.step, 3)

        # Finalize Step 3
        wiz, cmd = wiz.update(KeyMsg("enter"))
        self.assertIsNotNone(cmd)
        assert cmd is not None
        done_msg = cmd()
        self.assertIsNotNone(done_msg.account)
        self.assertEqual(done_msg.account.email, "kim@fastmail.com")
        self.assertEqual(done_msg.account.imap_host, "imap.fastmail.com")

    def test_image_renderer_backends_and_html_formatter(self) -> None:
        self.store.seed_demo_data()
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
        with tempfile.TemporaryDirectory() as tmpdir:
            app = EspressoMailApp(
                store=self.store,
                show_splash=False,
                demo_mode=True,
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

            # Download attachments with 'w'
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
