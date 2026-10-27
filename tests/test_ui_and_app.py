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
        import base64
        import io
        from PIL import Image as PILImage
        from espressomail.models.entities import Attachment, EmailMessage
        from espressomail.ui.formatter import format_message_for_reading_pane

        populate_test_store(self.store)
        msg = self.store.get_message("msg_thread1_1")
        assert msg is not None
        img_att = msg.attachments[0]
        theme = ThemeEngine("espresso").current
        renderer = TerminalImageRenderer(ImageRenderBackend.HALF_BLOCK)

        for mode in (
            ImageRenderBackend.AUTO,
            ImageRenderBackend.CHAFA,
            ImageRenderBackend.HALF_BLOCK,
            ImageRenderBackend.BRAILLE,
            ImageRenderBackend.SIXEL,
            ImageRenderBackend.KITTY,
            ImageRenderBackend.OFF,
        ):
            renderer.set_backend(mode)
            out = renderer.render_attachment(img_att, theme, max_width=40, max_height=8)
            self.assertTrue(len(out) > 10)
            if mode != ImageRenderBackend.OFF:
                self.assertTrue(
                    "▀" in out
                    or "▄" in out
                    or "\x1bPq" in out
                    or "\x1b_G" in out
                    or any(0x2800 <= ord(c) <= 0x28FF for c in out)
                )

        # Test multipart/alternative email where body_text is non-empty AND body_html has data:image + cid: + 1x1 tracker
        pil_img = PILImage.new("RGBA", (36, 18), (220, 90, 40, 255))
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        b64_png = base64.b64encode(buf.getvalue()).decode("ascii")

        html_email = EmailMessage(
            id="msg_html_img",
            account_id="acc_work",
            subject="Newsletter with Embedded Images",
            sender_name="Design Team",
            sender_email="design@schulz.dk",
            body_text="Plain text fallback without images.",
            body_html=(
                '<html><body>'
                f'<img src="data:image/png;base64,{b64_png}" alt="Hero Banner" />'
                '<img src="cid:inline-diagram@schulz.dk" alt="Architecture" />'
                '<img src="https://track.example.com/pixel.gif" width="1" height="1" />'
                '<p>Hello from HTML</p></body></html>'
            ),
            attachments=[
                Attachment(
                    id="att_cid_1",
                    message_id="msg_html_img",
                    filename="diagram.bin",
                    content_type="application/octet-stream",
                    size_bytes=len(buf.getvalue()),
                    content_id="<inline-diagram@schulz.dk>",
                    is_inline=True,
                    data=buf.getvalue(),
                )
            ],
        )
        renderer.set_backend(ImageRenderBackend.AUTO)
        pane_out = format_message_for_reading_pane(html_email, theme, width=72, image_renderer=renderer)
        self.assertIn("Hero_Banner.png", pane_out)
        self.assertIn("diagram.bin", pane_out)
        self.assertNotIn("pixel.gif", pane_out)
        self.assertIn("▀", pane_out)

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

    def test_hierarchical_folder_tree_and_archived_subfolders(self) -> None:
        from espressomail.models.entities import Folder, FolderType, classify_folder_type, split_folder_hierarchy

        # 1. Verify hierarchy splitting and classification of nested archive folders
        parts_top_sent = split_folder_hierarchy("INBOX.Sent", ".")
        self.assertEqual(parts_top_sent, ["Sent"])
        self.assertEqual(classify_folder_type(parts_top_sent, "\\Sent"), FolderType.SENT)

        parts_arch_inbox = split_folder_hierarchy("Archives/2025/inbox", "/")
        self.assertEqual(parts_arch_inbox, ["Archives", "2025", "inbox"])
        self.assertEqual(classify_folder_type(parts_arch_inbox, ""), FolderType.CUSTOM)

        parts_arch_sent = split_folder_hierarchy("Archives/2025/Sent", "/")
        self.assertEqual(parts_arch_sent, ["Archives", "2025", "Sent"])
        # Even if server flags it or it's named Sent, nested depth > 1 must be CUSTOM, not root SENT
        self.assertEqual(classify_folder_type(parts_arch_sent, "\\Sent"), FolderType.CUSTOM)

        # 2. Verify syncing remote nested folders creates a structured collapsible tree without duplicate root Sent
        acc = AccountConfig(
            id="acc_tree",
            name="Tree Account",
            email="kim@schulz.dk",
            full_name="Kim Schulz",
            imap_host="imap.schulz.dk",
            smtp_host="smtp.schulz.dk",
        )
        self.store.save_account(acc)
        self.store.sync_remote_folders(
            acc.id,
            [
                Folder(id="f_in", account_id=acc.id, name="INBOX", display_name="Inbox", folder_type=FolderType.INBOX),
                Folder(id="f_sent", account_id=acc.id, name="Sent", display_name="Sent", folder_type=FolderType.SENT),
                Folder(id="f_a25_in", account_id=acc.id, name="Archives/2025/inbox", display_name="inbox", folder_type=FolderType.CUSTOM),
                Folder(id="f_a25_sent", account_id=acc.id, name="Archives/2025/Sent", display_name="Sent", folder_type=FolderType.CUSTOM),
                Folder(id="f_a24_sent", account_id=acc.id, name="Archives/2024/Sent", display_name="Sent", folder_type=FolderType.CUSTOM),
            ],
        )

        app = EspressoMailApp(store=self.store, show_splash=False)
        acc_rows = [r for r in app.folder_rows if r.account_id == "acc_tree" and not r.is_header]

        # Root folders (depth 0) for acc_tree should only have Inbox, Sent, Drafts, Trash, and Archives
        depth0_labels = [r.label for r in acc_rows if r.depth == 0]
        self.assertEqual(depth0_labels.count("Sent"), 1)
        self.assertIn("Archives", depth0_labels)

        # Folders with children start collapsed by default
        archives_row = next(r for r in acc_rows if r.depth == 0 and r.label == "Archives")
        self.assertTrue(archives_row.has_children)
        self.assertFalse(archives_row.is_expanded)
        self.assertEqual([r for r in acc_rows if r.depth > 0], [])

        # 3. Expand "Archives" via Space key -> reveals depth 1 ("2025", "2024"), which also start collapsed
        app.focused_pane = "folders"
        for idx, r in enumerate(app.folder_rows):
            if r.account_id == "acc_tree" and r.depth == 0 and r.label == "Archives":
                app.folder_cursor = idx
                break
        app, _ = app.update(KeyMsg(" "))
        acc_rows = [r for r in app.folder_rows if r.account_id == "acc_tree" and not r.is_header]
        depth1_labels = [r.label for r in acc_rows if r.depth == 1]
        self.assertEqual(depth1_labels, ["2025", "2024"])
        self.assertEqual([r for r in acc_rows if r.depth == 2], [])

        # 4. Expand "2025" via 'l' -> reveals depth 2 ("inbox", "Sent")
        for idx, r in enumerate(app.folder_rows):
            if r.account_id == "acc_tree" and r.depth == 1 and r.label == "2025":
                app.folder_cursor = idx
                break
        app, _ = app.update(KeyMsg("l"))
        acc_rows = [r for r in app.folder_rows if r.account_id == "acc_tree" and not r.is_header]
        depth2_rows = [r for r in acc_rows if r.depth == 2]
        self.assertEqual(
            [(r.folder_name, r.label) for r in depth2_rows],
            [
                ("Archives/2025/inbox", "inbox"),
                ("Archives/2025/Sent", "Sent"),
            ],
        )

        # 5. Verify fold/unfold state is persisted across sessions in LocalStore
        app_new_session = EspressoMailApp(store=self.store, show_splash=False)
        session2_rows = [
            r for r in app_new_session.folder_rows if r.account_id == "acc_tree" and not r.is_header
        ]
        self.assertEqual(
            [(r.depth, r.label) for r in session2_rows if r.depth > 0],
            [(1, "2025"), (2, "inbox"), (2, "Sent"), (1, "2024")],
        )

        # 6. Collapse "Archives" with 'h' and verify children are hidden and persisted
        for idx, r in enumerate(app_new_session.folder_rows):
            if r.account_id == "acc_tree" and r.depth == 0 and r.label == "Archives":
                app_new_session.folder_cursor = idx
                break
        app_new_session.focused_pane = "folders"
        app_new_session, _ = app_new_session.update(KeyMsg("h"))
        collapsed_rows = [
            r for r in app_new_session.folder_rows if r.account_id == "acc_tree" and not r.is_header
        ]
        self.assertEqual([r.depth for r in collapsed_rows if r.depth > 0], [])


if __name__ == "__main__":
    unittest.main()

