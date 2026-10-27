"""Unit and integration tests for the 3-pane EspressoMail TUI, themes, splash, menus, onboarding, and account settings."""

from __future__ import annotations

import re
import tempfile
import unittest
from unittest import mock

from espresso import KeyMsg, MouseAction, MouseButton, MouseMsg, WindowSizeMsg
from espressomail.models import AccountConfig, EmailMessage, Folder, FolderType
from espressomail.storage import LocalStore
from espressomail.themes import ThemeEngine
from espressomail.ui import (
    PROVIDER_PRESETS,
    AccountSettingsModal,
    DownloadAttachmentsModal,
    EspressoMailApp,
    ImageRenderBackend,
    OnboardingWizard,
    PreferencesModal,
    PreferencesSavedMsg,
    SplashDoneMsg,
    SplashScreen,
    SplashTickMsg,
    TerminalImageRenderer,
    format_message_for_reading_pane,
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

        # Test inline placement between paragraphs in message body flow
        inline_msg = EmailMessage(
            id="msg_inline",
            account_id="acc_work",
            subject="Inline Test",
            body_html=f'<p>Paragraph Above</p><img src="data:image/png;base64,{b64_png}" alt="Inline Pic" /><p>Paragraph Below</p>',
        )
        inline_out = format_message_for_reading_pane(inline_msg, theme, width=72, image_renderer=renderer)
        pos_above = inline_out.find("Paragraph Above")
        pos_img = inline_out.find("Inline_Pic.png", pos_above)
        pos_below = inline_out.find("Paragraph Below")
        self.assertTrue(pos_above < pos_img < pos_below, "Image must be placed inline between text paragraphs!")

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

            # Download attachments with 'w' (opens DownloadAttachmentsModal, then confirm with Enter)
            for idx, r in enumerate(app.message_rows):
                if r.message.id == "msg_thread1_1":
                    app.message_cursor = idx
                    break
            app, _ = app.update(KeyMsg("w"))
            self.assertIsInstance(app.active_modal, DownloadAttachmentsModal)
            app, _ = app.update(KeyMsg("enter"))
            self.assertIsNone(app.active_modal)
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

    def test_download_attachments_modal_selection_and_target_dir(self) -> None:
        from pathlib import Path
        from espressomail.models.entities import Attachment

        populate_test_store(self.store)
        msg = self.store.get_message("msg_thread1_1")
        assert msg is not None
        self.assertTrue(len(msg.attachments) >= 1)

        theme = ThemeEngine("espresso").current
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_download = Path(tmpdir) / "custom_downloads"
            modal = DownloadAttachmentsModal(
                attachments=msg.attachments,
                download_dir=custom_download,
                theme=theme,
                store=self.store,
            )
            # Default state: all attachments selected
            self.assertEqual(modal.selected_indices, set(range(len(msg.attachments))))
            view_str = modal.view(70)
            self.assertIn("Select attachments", view_str)
            self.assertIn(msg.attachments[0].filename, view_str)

            # Deselect with space
            modal, _ = modal.update(KeyMsg("space"))
            self.assertNotIn(0, modal.selected_indices)

            # Deselect all with 'a'
            modal.selected_indices.clear()

            # Try downloading with none selected -> shows error message
            modal, cmd = modal.update(KeyMsg("enter"))
            self.assertIn("select at least one", modal.error_msg)
            self.assertIsNone(cmd)

            # Re-select with 'a' (toggle all)
            modal, _ = modal.update(KeyMsg("a"))
            self.assertEqual(modal.selected_indices, set(range(len(msg.attachments))))

            # Execute download
            modal, cmd = modal.update(KeyMsg("enter"))
            self.assertIsNotNone(cmd)
            closed_msg = cmd()
            self.assertIn("Saved", closed_msg.status_note)
            self.assertTrue((custom_download / msg.attachments[0].filename).exists())

    def test_preferences_modal_configuration_and_persistence(self) -> None:
        from pathlib import Path

        populate_test_store(self.store)
        with tempfile.TemporaryDirectory() as tmpdir:
            initial_dl = Path(tmpdir) / "init_dl"
            app = EspressoMailApp(store=self.store, show_splash=False, download_dir=initial_dl)

            # Press 'p' to open PreferencesModal
            app, _ = app.update(KeyMsg("p"))
            self.assertIsInstance(app.active_modal, PreferencesModal)
            pref_modal = app.active_modal
            assert isinstance(pref_modal, PreferencesModal)

            # Cycle image mode (focus 1)
            pref_modal.focus_idx = 1
            pref_modal, _ = pref_modal.update(KeyMsg("right"))
            chosen_backend = pref_modal.IMAGE_BACKENDS[pref_modal.backend_idx]

            # Cycle theme (focus 2)
            pref_modal.focus_idx = 2
            pref_modal, _ = pref_modal.update(KeyMsg("right"))
            chosen_theme = pref_modal.THEMES[pref_modal.theme_idx]

            # Toggle threading (focus 3)
            pref_modal.focus_idx = 3
            initial_th = pref_modal.threading_enabled
            pref_modal, _ = pref_modal.update(KeyMsg("space"))
            self.assertNotEqual(pref_modal.threading_enabled, initial_th)

            # Change download folder
            new_dl = Path(tmpdir) / "new_preferences_dl"
            pref_modal.input_download_dir.value = str(new_dl)

            # Save preferences with Enter
            app, _ = app.update(KeyMsg("enter"))
            self.assertIsNone(app.active_modal)
            self.assertIn("Saved preferences", app.status_message)
            self.assertEqual(app.download_dir, new_dl.resolve())
            self.assertEqual(app.theme.id, chosen_theme)
            self.assertEqual(app.image_renderer.backend.value, chosen_backend)

            # Assert persisted to SQLite store
            self.assertEqual(self.store.get_setting("download_dir"), str(new_dl.resolve()))
            self.assertEqual(self.store.get_setting("image_backend"), chosen_backend)
            self.assertEqual(self.store.get_setting("theme"), chosen_theme)

    def test_account_signature_in_editor_and_replies(self) -> None:
        from espressomail.models.entities import EmailMessage
        from espressomail.ui.modals import ComposeModal

        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)

        # Open AccountSettingsModal on acc_work
        app.active_account_id = "acc_work"
        app, _ = app.update(KeyMsg("A"))
        self.assertIsInstance(app.active_modal, AccountSettingsModal)
        modal = app.active_modal
        assert isinstance(modal, AccountSettingsModal)

        # Set multi-line signature
        multi_sig = "Best regards,\nKim Schulz\nCTO & Architect"
        modal.input_signature.value = multi_sig
        # Save offline with Ctrl+F
        app, _ = app.update(KeyMsg("ctrl+f"))
        self.assertIsNone(app.active_modal)

        # Verify persisted in database
        acc = self.store.get_account("acc_work")
        assert acc is not None
        self.assertEqual(acc.signature, multi_sig)

        # 1. Test Compose Modal pre-populates signature for new message
        app, _ = app.update(KeyMsg("c"))
        self.assertIsInstance(app.active_modal, ComposeModal)
        comp = app.active_modal
        assert isinstance(comp, ComposeModal)
        self.assertIn(multi_sig, comp.editor.value)
        app, _ = app.update(KeyMsg("esc"))

        # 2. Test Reply modal includes signature before quoted message
        msg = self.store.get_message("msg_thread1_1")
        assert msg is not None
        for idx, r in enumerate(app.message_rows):
            if r.message.id == "msg_thread1_1":
                app.message_cursor = idx
                break
        app, _ = app.update(KeyMsg("r"))
        self.assertIsInstance(app.active_modal, ComposeModal)
        reply_comp = app.active_modal
        assert isinstance(reply_comp, ComposeModal)
        self.assertIn(multi_sig, reply_comp.editor.value)
        self.assertIn("wrote:", reply_comp.editor.value)

    def test_unified_inbox_account_identification(self) -> None:
        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)

        # Switch to Unified Inbox
        app, _ = app.update(KeyMsg("U"))
        self.assertEqual(app.active_account_id, "*")

        # Render message list pane
        list_output = app._render_message_list_pane(width=80, height=20)
        # Verify account badge tags (e.g. [Schulz], [Espres]) appear in the rendered list
        self.assertIn("[Schulz]", list_output)
        self.assertIn("[Espres]", list_output)

        # Check reading pane shows account badge
        m = app.selected_message
        assert m is not None
        acc = app._accounts_cache.get(m.account_id)
        assert acc is not None
        reader_out = format_message_for_reading_pane(
            msg=m,
            theme=app.theme,
            width=80,
            image_renderer=app.image_renderer,
            account=acc,
        )
        self.assertIn(f"● {acc.name}", reader_out)

    def test_two_line_message_list_rendering_and_mouse_click(self) -> None:
        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.active_account_id = "*"
        app.refresh_data()
        self.assertTrue(len(app.message_rows) >= 2)

        # 1. Render message list pane with width=60, height=14
        rendered = app._render_message_list_pane(width=60, height=14)
        # Verify that subject appears clearly with ample characters on line 2
        first_msg = app.message_rows[0].message
        self.assertIn(first_msg.subject[:25], rendered)

        # Verify Line 1 has sender / account tag and Line 2 has subject indented
        lines = [line for line in rendered.splitlines() if line.strip()]
        # The pane header is the first line; message rows follow inside
        inner_content = rendered
        self.assertIn("[Schulz]", inner_content)
        self.assertIn("Marcus Vance", inner_content)
        self.assertIn("Production WireGuard", inner_content)

        # 2. Test mouse click mapping:
        # Pane 1 (folders) is width ~20. Pane 2 (messages) is at x=25..65.
        # body_y=0 or 1 corresponds to item 0; body_y=2 or 3 corresponds to item 1
        folder_w, _, _ = app._pane_widths()
        click_x = folder_w + 5

        # Click on line 1 of second item (y=2 + 2 = 4, where body_y = 4 - 2 = 2 => item 1)
        app, _ = app.update(MouseMsg(x=click_x, y=4, button=MouseButton.LEFT, action=MouseAction.PRESS))
        self.assertEqual(app.focused_pane, "messages")
        self.assertEqual(app.message_cursor, 1)

        # Click on line 2 of second item (y=5, body_y=3 => item 1)
        app, _ = app.update(MouseMsg(x=click_x, y=5, button=MouseButton.LEFT, action=MouseAction.PRESS))
        self.assertEqual(app.message_cursor, 1)

        # Click on line 1 of first item (y=2, body_y=0 => item 0)
        app, _ = app.update(MouseMsg(x=click_x, y=2, button=MouseButton.LEFT, action=MouseAction.PRESS))
        self.assertEqual(app.message_cursor, 0)

    def test_splitter_pull_mouse_and_keyboard_resizing(self) -> None:
        """Test pulling the splitter between message list and message view via mouse drag and keyboard."""
        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.width = 120
        app.height = 34

        init_folder_w, init_list_w, init_reader_w = app._pane_widths()
        self.assertEqual(init_folder_w + init_list_w + init_reader_w, 120)
        self.assertIsNone(app.custom_list_w)

        split_x = init_folder_w + init_list_w

        # 1. Grab splitter with left mouse button
        app, _ = app.update(
            MouseMsg(x=split_x, y=10, button=MouseButton.LEFT, action=MouseAction.PRESS)
        )
        self.assertTrue(app.splitter_dragging)

        # 2. Pull splitter to the left (x - 6) to widen the reading pane
        target_drag_x = split_x - 6
        app, _ = app.update(
            MouseMsg(x=target_drag_x, y=10, button=MouseButton.LEFT, action=MouseAction.MOTION)
        )
        self.assertTrue(app.splitter_dragging)
        new_folder_w, new_list_w, new_reader_w = app._pane_widths()
        self.assertEqual(new_list_w, init_list_w - 6)
        self.assertEqual(new_reader_w, init_reader_w + 6)
        self.assertEqual(new_folder_w + new_list_w + new_reader_w, 120)

        # 3. Release mouse: splitter drag ends and custom_list_w is persisted
        app, _ = app.update(
            MouseMsg(x=target_drag_x, y=10, button=MouseButton.LEFT, action=MouseAction.RELEASE)
        )
        self.assertFalse(app.splitter_dragging)
        self.assertEqual(self.store.get_setting("custom_list_w"), str(new_list_w))

        # 4. Starting a new session restores the custom splitter width
        app_restored = EspressoMailApp(store=self.store, show_splash=False)
        app_restored.width = 120
        self.assertEqual(app_restored.custom_list_w, new_list_w)
        _, restored_list_w, restored_reader_w = app_restored._pane_widths()
        self.assertEqual(restored_list_w, new_list_w)
        self.assertEqual(restored_reader_w, new_reader_w)

        # 5. Keyboard resizing: '[' widens reader, ']' widens list
        prev_list_w = app_restored._pane_widths()[1]
        app_restored, _ = app_restored.update(KeyMsg(key="["))
        self.assertEqual(app_restored._pane_widths()[1], prev_list_w - 3)

        app_restored, _ = app_restored.update(KeyMsg(key="]"))
        self.assertEqual(app_restored._pane_widths()[1], prev_list_w)

        # 6. Clamping bounds: drag to extreme left and right
        # Extreme left clamp (min_list_w = 24)
        app_restored._apply_splitter_move(0)
        self.assertEqual(app_restored._pane_widths()[1], 24)
        self.assertEqual(sum(app_restored._pane_widths()), 120)

        # Extreme right clamp (reader must have at least min_reader_w = 26)
        app_restored._apply_splitter_move(500)
        _, clamped_max_list, clamped_reader = app_restored._pane_widths()
        self.assertGreaterEqual(clamped_reader, 26)
        self.assertEqual(sum(app_restored._pane_widths()), 120)

        # 7. Double-click splitter resets width to default
        cur_split_x = app_restored._pane_widths()[0] + app_restored._pane_widths()[1]
        app_restored, _ = app_restored.update(
            MouseMsg(x=cur_split_x, y=8, button=MouseButton.LEFT, action=MouseAction.DOUBLE_CLICK)
        )
        self.assertIsNone(app_restored.custom_list_w)
        self.assertEqual(self.store.get_setting("custom_list_w"), "")
        self.assertEqual(app_restored._pane_widths(), (init_folder_w, init_list_w, init_reader_w))

        # Verify UI view renders cleanly with resized pane
        view_str = app_restored.view()
        self.assertIn("Mailboxes", view_str)
        self.assertIn("Reading Pane", view_str)

    def test_tree_component_space_toggle_and_fetch_more_pagination(self) -> None:
        """Verify Espresso Tree component, space bar toggle, box-drawing branch alignment, and M fetch more."""
        from unittest import mock
        from espresso.beans.tree import Tree
        from espressomail.models.entities import EmailMessage, Folder, FolderType
        from espressomail.protocols.smtp_client import validate_account_credentials

        acc = AccountConfig(
            id="acc_fetch",
            name="Pagination Corp",
            email="paging@corp.org",
            full_name="Paging User",
            imap_host="imap.corp.org",
            imap_password="secret_pass",
        )
        self.store.save_account(acc)
        self.store.sync_remote_folders(
            acc.id,
            [
                Folder(id="f1", account_id=acc.id, name="INBOX", display_name="INBOX", folder_type=FolderType.INBOX),
                Folder(id="f2", account_id=acc.id, name="Projects", display_name="Projects", folder_type=FolderType.CUSTOM),
                Folder(id="f3", account_id=acc.id, name="Projects/2026", display_name="2026", folder_type=FolderType.CUSTOM),
            ],
        )

        app = EspressoMailApp(store=self.store, show_splash=False)
        self.assertIsInstance(app.folder_tree, Tree)

        # 1. Projects folder has children and starts collapsed
        projects_row = next(r for r in app.folder_rows if r.label == "Projects")
        self.assertTrue(projects_row.has_children)
        self.assertFalse(projects_row.is_expanded)

        # 2. Pressing Space on Projects toggles expanded to True
        app.focused_pane = "folders"
        for idx, r in enumerate(app.folder_rows):
            if r.label == "Projects":
                app.folder_cursor = idx
                break
        app, _ = app.update(KeyMsg(" "))
        projects_row_after = next(r for r in app.folder_rows if r.label == "Projects")
        self.assertTrue(projects_row_after.is_expanded)
        self.assertIn("2026", [r.label for r in app.folder_rows])

        # 3. Check box-drawing branch formatting in folder pane view
        pane_str = app._render_folder_pane(width=34, height=20)
        self.assertIn("├── ", pane_str)
        self.assertIn("▼ ", pane_str)
        self.assertIn("Projects", pane_str)

        # 4. Pressing Space on Projects again collapses it
        app, _ = app.update(KeyMsg(" "))
        projects_row_collapsed = next(r for r in app.folder_rows if r.label == "Projects")
        self.assertFalse(projects_row_collapsed.is_expanded)
        self.assertNotIn("2026", [r.label for r in app.folder_rows])

        # 5. Test 'M' shortcut triggers _fetch_more_messages_cmd
        app.active_account_id = acc.id
        app.active_folder = "INBOX"
        app, cmd = app.update(KeyMsg("M"))
        self.assertIsNotNone(cmd)
        self.assertIn("Fetching older messages", app.status_message)

        # Mock IMAP client fetch_recent_messages during command execution
        with mock.patch("espressomail.ui.app.IMAPClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.fetch_recent_messages.return_value = [
                EmailMessage(
                    id=f"{acc.id}:INBOX:42",
                    account_id=acc.id,
                    folder="INBOX",
                    subject="Earlier Message 42",
                    sender_name="Earlier Sender",
                    sender_email="earlier@corp.org",
                    body_text="Earlier content",
                    uid=42,
                )
            ]
            msg = cmd()
            self.assertEqual(msg.fetched_count, 1)
            app, _ = app.update(msg)
            self.assertIn("Fetched 1 older message(s)", app.status_message)
            self.assertEqual(len(app.message_rows), 1)
            self.assertEqual(app.message_rows[0].message.subject, "Earlier Message 42")

        # 6. Verify immediate folder discovery on onboarding credential validation
        new_acc = AccountConfig(
            id="acc_fresh",
            name="Fresh Account",
            email="fresh@corp.org",
            full_name="Fresh User",
            imap_host="imap.corp.org",
            imap_password="pass",
            smtp_host="smtp.corp.org",
            smtp_password="pass",
        )
        with mock.patch("espressomail.protocols.imap_client.IMAPClient") as mock_imap_cls, \
             mock.patch("espressomail.protocols.smtp_client.SMTPClient") as mock_smtp_cls:
            inst = mock_imap_cls.return_value
            inst.verify_credentials.return_value = (True, "IMAP login verified")
            inst.list_remote_folders.return_value = [
                Folder(id="", account_id=new_acc.id, name="INBOX", display_name="INBOX", folder_type=FolderType.INBOX),
                Folder(id="", account_id=new_acc.id, name="Work/Clients", display_name="Clients", folder_type=FolderType.CUSTOM),
            ]
            mock_smtp_cls.return_value.verify_credentials.return_value = (True, "SMTP login verified")
            ok, note = validate_account_credentials(new_acc, store=self.store)
            self.assertTrue(ok)
            # Folders should be immediately persisted in self.store
            fresh_folders = self.store.list_folders(new_acc.id)
            fresh_names = [f.name for f in fresh_folders]
            self.assertIn("INBOX", fresh_names)
            self.assertIn("Work/Clients", fresh_names)

    def test_folder_tree_space_optimization_and_account_collapse(self) -> None:
        """Verify no wasted horizontal space (no selection marker arrow, no expander column),
        distinct subfolder group icons (🗂️ / 📂), account folding/unfolding and persistence."""
        acc = AccountConfig(id="acc_main", name="Personal", email="me@example.com", full_name="My Name", imap_host="imap.example.com")
        self.store.save_account(acc)
        self.store.sync_remote_folders(
            acc.id,
            [
                Folder(id="f_w", account_id=acc.id, name="Work", display_name="Work", folder_type=FolderType.CUSTOM),
                Folder(id="f_wc", account_id=acc.id, name="Work/Clients", display_name="Clients", folder_type=FolderType.CUSTOM),
            ],
        )

        app = EspressoMailApp(self.store, show_splash=False)
        app.focused_pane = "folders"
        folder_view = app._render_folder_pane(30, 20)

        # 1. No marker arrows ('▸' or '•') wasting columns on the left
        self.assertNotIn("▸", folder_view)
        self.assertNotIn("•", folder_view)
        # No expander arrows ("▼ " or "▶ ") on leaf mailbox rows
        self.assertNotIn("  📥", folder_view)

        # 2. Subfolder group icons: Work has subfolder Work/Clients, so collapsed icon is 🗂️
        self.assertIn("🗂️ Work", folder_view)

        # Expand Work via Space/right and verify icon becomes 📂
        visible = app.folder_tree._flatten_visible()
        work_idx = next(i for i, (node, _, _, _) in enumerate(visible) if node.value and node.value.label == "Work")
        app.folder_cursor = work_idx
        app, _ = app.update(KeyMsg(key="right"))
        expanded_view = app._render_folder_pane(30, 20)
        self.assertIn("📂 Work", expanded_view)
        self.assertIn("Clients", expanded_view)

        # 3. Account collapsing: account header displays indicator '▼' when unfolded
        self.assertIn(f"▼ {acc.name.upper()}", folder_view)

        # Collapse account via Space/Enter or Left on header
        acc_idx = next(i for i, (node, _, _, _) in enumerate(app.folder_tree._flatten_visible()) if node.value and node.value.is_header and node.value.account_id == acc.id)
        app.folder_cursor = acc_idx
        app, _ = app.update(KeyMsg(key="space"))

        collapsed_view = app._render_folder_pane(30, 20)
        # Account header now displays '▶' when folded
        self.assertIn(f"▶ {acc.name.upper()}", collapsed_view)
        # None of the account's folders should be shown in the tree when collapsed!
        self.assertNotIn("Work", collapsed_view)
        self.assertNotIn("Clients", collapsed_view)

        # Verify state persistence across app re-instantiation
        app2 = EspressoMailApp(self.store, show_splash=False)
        app2_view = app2._render_folder_pane(30, 20)
        self.assertIn(f"▶ {acc.name.upper()}", app2_view)
        self.assertNotIn("Work", app2_view)

        # Unfold account again via Enter and verify folders reappear
        app2.focused_pane = "folders"
        acc2_idx = next(i for i, (node, _, _, _) in enumerate(app2.folder_tree._flatten_visible()) if node.value and node.value.is_header and node.value.account_id == acc.id)
        app2.folder_cursor = acc2_idx
        app2, _ = app2.update(KeyMsg(key="enter"))
        self.assertIn(f"▼ {acc.name.upper()}", app2._render_folder_pane(30, 20))
        self.assertIn("Work", app2._render_folder_pane(30, 20))

    def test_left_splitter_resizes_folder_against_reader(self) -> None:
        """Verify dragging the splitter between folder list and message list resizes folder width
        against the reading pane, while the message list width remains strictly UNCHANGED."""
        acc = AccountConfig(id="acc_main", name="Personal", email="me@example.com", full_name="My Name", imap_host="imap.example.com")
        self.store.save_account(acc)

        app = EspressoMailApp(self.store, show_splash=False)
        app.width = 120
        app.height = 30
        init_f_w, init_l_w, init_r_w = app._pane_widths()

        # 1. Mouse Drag on Splitter 1 (at x = init_f_w)
        # Press on splitter 1
        app, _ = app.update(MouseMsg(x=init_f_w, y=5, button=MouseButton.LEFT, action=MouseAction.PRESS))
        self.assertEqual(app.splitter_dragging, 1)

        # Move mouse right by +6 cols (widen folder pane to init_f_w + 6)
        target_f_w = init_f_w + 6
        app, _ = app.update(MouseMsg(x=target_f_w, y=5, button=MouseButton.LEFT, action=MouseAction.MOTION))
        new_f_w, new_l_w, new_r_w = app._pane_widths()
        self.assertEqual(new_f_w, target_f_w)
        # CRITICAL REQUIREMENT: Message list width MUST remain unchanged!
        self.assertEqual(new_l_w, init_l_w)
        # Space was taken from reader pane!
        self.assertEqual(new_r_w, init_r_w - 6)
        self.assertEqual(sum(app._pane_widths()), 120)

        # Release mouse button
        app, _ = app.update(MouseMsg(x=target_f_w, y=5, button=MouseButton.LEFT, action=MouseAction.RELEASE))
        self.assertEqual(app.splitter_dragging, 0)
        self.assertEqual(self.store.get_setting("custom_folder_w"), str(target_f_w))

        # 2. Persistence across sessions
        restored = EspressoMailApp(self.store, show_splash=False)
        restored.width = 120
        restored.height = 30
        self.assertEqual(restored._pane_widths()[0], target_f_w)
        self.assertEqual(restored._pane_widths()[1], init_l_w)

        # 3. Keyboard resizing: '{' narrows folder list, '}' widens folder list
        cur_f = restored._pane_widths()[0]
        restored, _ = restored.update(KeyMsg(key="{"))
        self.assertEqual(restored._pane_widths()[0], cur_f - 2)
        self.assertEqual(restored._pane_widths()[1], init_l_w)  # list unchanged!

        restored, _ = restored.update(KeyMsg(key="}"))
        self.assertEqual(restored._pane_widths()[0], cur_f)
        self.assertEqual(restored._pane_widths()[1], init_l_w)  # list unchanged!

        # 4. Double click on Splitter 1 resets width to default
        cur_f = restored._pane_widths()[0]
        restored, _ = restored.update(MouseMsg(x=cur_f, y=5, button=MouseButton.LEFT, action=MouseAction.DOUBLE_CLICK))
        self.assertIsNone(restored.custom_folder_w)
        self.assertEqual(self.store.get_setting("custom_folder_w"), "")
        self.assertEqual(restored._pane_widths(), (init_f_w, init_l_w, init_r_w))

    def test_folder_count_mode_and_fetch_batch_size_preferences(self) -> None:
        """Verify configurable folder_count_mode ('unread_only' vs 'unread_and_total')
        and configurable fetch_batch_size (25, 50, 100, 200)."""
        acc = AccountConfig(id="acc_main", name="Personal", email="me@example.com", full_name="My Name", imap_host="imap.example.com", imap_password="pass")
        self.store.save_account(acc)
        self.store.sync_remote_folders(
            acc.id,
            [
                Folder(id="f_test", account_id=acc.id, name="Testing", display_name="Testing", folder_type=FolderType.CUSTOM),
            ],
        )
        for i in range(18):
            self.store.save_message(
                EmailMessage(
                    id=f"msg_test_{i}",
                    account_id=acc.id,
                    folder="Testing",
                    subject=f"Subject {i}",
                    sender_email="test@example.com",
                    body_text="Body text",
                    is_read=True,
                )
            )

        app = EspressoMailApp(self.store, show_splash=False)

        # 1. Default mode: 'unread_only' - folder with 0 unread has NO count badge wasting columns
        app.folder_count_mode = "unread_only"
        pane_unread_only = app._render_folder_pane(30, 20)
        plain_unread = re.sub(r"\x1b\[[0-9;]*m", "", pane_unread_only)
        self.assertNotIn("18", plain_unread)

        # 2. Switch to 'unread_and_total' - total count is now displayed
        app.folder_count_mode = "unread_and_total"
        pane_both = app._render_folder_pane(30, 20)
        plain_both = re.sub(r"\x1b\[[0-9;]*m", "", pane_both)
        self.assertIn("18", plain_both)

        # 3. PreferencesModal configuration
        modal = PreferencesModal(
            store=self.store,
            current_theme=app.theme,
            download_dir=app.download_dir,
        )
        self.assertEqual(modal.FOLDER_COUNT_MODES[modal.count_mode_idx], "unread_only")
        # Toggle folder count mode to 'unread_and_total'
        modal.focus_idx = 7
        modal, _ = modal.update(KeyMsg(key="right"))
        self.assertEqual(modal.FOLDER_COUNT_MODES[modal.count_mode_idx], "unread_and_total")

        # Cycle fetch batch size to 100
        modal.focus_idx = 8
        modal, _ = modal.update(KeyMsg(key="right"))
        self.assertEqual(modal.FETCH_BATCH_SIZES[modal.batch_idx], 100)

        # Save preferences
        modal, cmd = modal.update(KeyMsg(key="ctrl+s"))
        self.assertIsNotNone(cmd)
        saved_msg = cmd()
        self.assertIsInstance(saved_msg, PreferencesSavedMsg)
        self.assertEqual(saved_msg.folder_count_mode, "unread_and_total")
        self.assertEqual(saved_msg.fetch_batch_size, 100)

        # Apply to app
        app, _ = app.update(saved_msg)
        self.assertEqual(app.folder_count_mode, "unread_and_total")
        self.assertEqual(app.fetch_batch_size, 100)
        self.assertEqual(self.store.get_setting("folder_count_mode"), "unread_and_total")
        self.assertEqual(self.store.get_setting("fetch_batch_size"), "100")

        # 4. Verify fetch_more_messages_cmd uses the configured batch size
        with mock.patch("espressomail.ui.app.IMAPClient") as mock_imap_cls:
            inst = mock_imap_cls.return_value
            inst.fetch_recent_messages.return_value = []
            app.active_account_id = acc.id
            app.active_folder = "Testing"
            cmd = app._fetch_more_messages_cmd
            cmd()
            # Assert limit was called with app.fetch_batch_size = 100
            inst.fetch_recent_messages.assert_called_with(
                folder="Testing",
                limit=100,
                before_uid=None,
            )

    def test_three_pane_bottom_border_alignment(self) -> None:
        """Verify the bottom border of the folder list aligns exactly with the message list and reading pane."""
        acc = AccountConfig(id="acc_main", name="Personal", email="me@example.com", full_name="My Name", imap_host="imap.example.com")
        self.store.save_account(acc)

        app = EspressoMailApp(self.store, show_splash=False)
        app.update(WindowSizeMsg(width=100, height=24))
        f_w, l_w, r_w = app._pane_widths()
        pane_h = app._pane_height()

        left_pane = app._render_folder_pane(f_w, pane_h)
        center_pane = app._render_message_list_pane(l_w, pane_h)
        right_pane = app._render_reading_pane(r_w, pane_h)

        # All 3 panes MUST have the exact same rendered line count equal to pane_h
        left_lines = left_pane.splitlines()
        center_lines = center_pane.splitlines()
        right_lines = right_pane.splitlines()

        self.assertEqual(len(left_lines), pane_h, f"Folder pane line count ({len(left_lines)}) != pane_h ({pane_h})")
        self.assertEqual(len(center_lines), pane_h, f"Message list pane line count ({len(center_lines)}) != pane_h ({pane_h})")
        self.assertEqual(len(right_lines), pane_h, f"Reading pane line count ({len(right_lines)}) != pane_h ({pane_h})")

        # Bottom borders align at the exact same row!
        self.assertEqual(len(left_lines), len(center_lines))
        self.assertEqual(len(center_lines), len(right_lines))

    def test_async_remote_image_loading_and_non_blocking_scrolling(self) -> None:
        """Verify remote image loading does not block UI, renders placeholder, and loads asynchronously via TEA Cmd."""
        from espressomail.ui import clear_image_caches, MessageImagesLoadedMsg

        clear_image_caches()
        acc = AccountConfig(id="acc_img", name="ImageTest", email="img@example.com", full_name="Img Tester", imap_host="imap.example.com")
        self.store.save_account(acc)

        # Message with a remote HTML image
        img_msg = EmailMessage(
            id="acc_img:INBOX:101",
            account_id="acc_img",
            folder="INBOX",
            subject="Email with remote image",
            sender_name="Sender",
            sender_email="sender@example.com",
            body_text="Here is a remote graphic.",
            body_html='<p>Look at this image: <img src="https://example.com/photo.png" alt="RemoteDiagram" width="200" height="100"></p>',
        )
        self.store.save_message(img_msg)

        # Message with plain text (no remote images)
        text_msg = EmailMessage(
            id="acc_img:INBOX:102",
            account_id="acc_img",
            folder="INBOX",
            subject="Plain text email",
            sender_name="Sender 2",
            sender_email="sender2@example.com",
            body_text="Plain message.",
        )
        self.store.save_message(text_msg)

        app = EspressoMailApp(self.store, show_splash=False)
        app.update(WindowSizeMsg(width=110, height=30))
        app.active_account_id = "acc_img"
        app.active_folder = "INBOX"
        app.refresh_data()

        # Step cursor to the message with the remote image (index 0)
        app.message_cursor = 0  # img_msg (101)
        cmd = app._on_message_selection_changed()

        # Reading pane must immediately have rendered the non-blocking pending placeholder
        rendered_initial = "\n".join(app.reader_viewport.lines)
        self.assertIn("Loading remote image in background", rendered_initial)
        self.assertIn("photo.png", rendered_initial)

        # A TEA background command is returned to download the remote image
        self.assertIsNotNone(cmd)
        self.assertTrue(callable(cmd))

        # Test scrolling down and up before network completes is 100% responsive and non-blocking
        app, cmd_down = app.update(KeyMsg("j"))
        self.assertEqual(app.selected_message.id, text_msg.id)
        self.assertNotIn("Loading remote image in background", "\n".join(app.reader_viewport.lines))

        app, cmd_up = app.update(KeyMsg("k"))
        self.assertEqual(app.selected_message.id, img_msg.id)
        self.assertIn("Loading remote image in background", "\n".join(app.reader_viewport.lines))

        # Now simulate background execution of the image download TEA command
        ppm_10x10 = b"P6\n10 10\n255\n" + (b"\xff\x00\x00" * 100)
        with mock.patch("espressomail.ui.app._fetch_remote_image_bytes", return_value=ppm_10x10):
            loaded_msg = cmd()

        self.assertIsInstance(loaded_msg, MessageImagesLoadedMsg)
        self.assertEqual(loaded_msg.message_id, img_msg.id)
        self.assertIn("https://example.com/photo.png", loaded_msg.loaded_urls)

        # Dispatch the loaded message back to TEA update loop
        app, post_cmd = app.update(loaded_msg)
        self.assertIsNone(post_cmd)

        # Reading pane now renders the actual image graphic and no longer shows the loading placeholder
        rendered_loaded = "\n".join(app.reader_viewport.lines)
        self.assertNotIn("Loading remote image in background", rendered_loaded)
        self.assertIn("photo.png", rendered_loaded)

    def test_non_blocking_tea_background_sync(self) -> None:
        """Verify step-by-step TEA email sync, status bar SYNCING badge, and auto-sync tick."""
        from espressomail.ui import AutoSyncTickMsg, SyncProgressMsg

        acc = AccountConfig(
            id="acc_sync_test",
            name="Work Mail",
            email="work@example.com",
            full_name="Work User",
            imap_host="imap.work.com",
            imap_password="secret_password",
        )
        self.store.save_account(acc)

        app = EspressoMailApp(self.store, show_splash=False)
        app.update(WindowSizeMsg(width=110, height=30))

        # Initial state: not syncing
        self.assertFalse(app.is_syncing)
        self.assertNotIn("SYNCING", app._render_status_bar())

        # User presses 'S' (sync_all action)
        app, sync_cmd = app.update(KeyMsg("S"))
        self.assertTrue(app.is_syncing)
        self.assertIn("SYNCING", app._render_status_bar())
        self.assertIn("Synchronizing", app.status_message)
        self.assertEqual(sync_cmd, app._sync_accounts_cmd)

        # Duplicate sync request while syncing is prevented
        app, dup_cmd = app.update(KeyMsg("S"))
        self.assertIsNone(dup_cmd)
        self.assertIn("already in progress", app.status_message)

        # Mock IMAP client responses for step-by-step discovery & folder sync
        discovered_remote = [
            Folder(id="acc_sync_test:INBOX", account_id=acc.id, name="INBOX", display_name="INBOX", selectable=True),
            Folder(id="acc_sync_test:Archive", account_id=acc.id, name="Archive", display_name="Archive", selectable=True),
        ]

        with mock.patch("espressomail.ui.app.IMAPClient") as mock_client_cls:
            mock_inst = mock.MagicMock()
            mock_client_cls.return_value = mock_inst
            mock_inst.list_remote_folders.return_value = discovered_remote
            mock_inst.sync_single_folder.return_value = 3

            # Step 1: Initial command runs discover on background thread
            step1_msg = sync_cmd()
            self.assertIsInstance(step1_msg, SyncProgressMsg)
            self.assertEqual(step1_msg.action, "discovered_folders")
            self.assertEqual(len(step1_msg.remaining_steps), 2)  # INBOX and Archive

            # Dispatch Step 1 to update loop: newly discovered folders immediately appear in UI!
            app, step2_cmd = app.update(step1_msg)
            self.assertTrue(app.is_syncing)
            self.assertIsNotNone(step2_cmd)
            # Store now has the discovered folders
            folder_names = [f.name for f in self.store.list_folders(acc.id)]
            self.assertIn("INBOX", folder_names)
            self.assertIn("Archive", folder_names)
            self.assertIn("Syncing", app.status_message)

            # Step 2: Sync INBOX
            step2_msg = step2_cmd()
            self.assertIsInstance(step2_msg, SyncProgressMsg)
            self.assertEqual(step2_msg.action, "synced_folder")
            self.assertEqual(step2_msg.folder_name, "INBOX")

            app, step3_cmd = app.update(step2_msg)
            self.assertTrue(app.is_syncing)
            self.assertIsNotNone(step3_cmd)

            # Step 3: Sync Archive
            step3_msg = step3_cmd()
            self.assertIsInstance(step3_msg, SyncProgressMsg)
            self.assertEqual(step3_msg.action, "synced_folder")
            self.assertEqual(step3_msg.folder_name, "Archive")

            # Final step completed
            app, final_cmd = app.update(step3_msg)
            self.assertFalse(app.is_syncing)
            self.assertNotIn("SYNCING", app._render_status_bar())
            self.assertIn("Synchronized 6 message(s)", app.status_message)
            self.assertIsNotNone(final_cmd)  # tick for auto-sync

        # Periodic auto-sync tick
        with mock.patch.object(app, "_sync_accounts_cmd", return_value=SyncProgressMsg(account_id="", account_name="", action="done")):
            app, auto_cmd = app.update(AutoSyncTickMsg())
            self.assertTrue(app.is_syncing)
            self.assertEqual(auto_cmd, app._sync_accounts_cmd)
            self.assertIn("auto-sync", app.status_message)


if __name__ == "__main__":
    unittest.main()


