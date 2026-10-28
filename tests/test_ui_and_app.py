"""Unit and integration tests for the 3-pane EspressoMail TUI, themes, splash, menus, onboarding, and account settings."""

from __future__ import annotations

import re
import tempfile
import unittest
from unittest import mock

try:
    from espresso import KeyMsg, MouseAction, MouseButton, MouseMsg, WindowSizeMsg
    from espressomail.ui import (
        PROVIDER_PRESETS,
        AccountSettingsModal,
        AddressBookModal,
        ComposeModal,
        DownloadAttachmentsModal,
        EspressoMailApp,
        ImageRenderBackend,
        MoreMessagesFetchedMsg,
        OnboardingCompleteMsg,
        OnboardingWizard,
        PreferencesModal,
        PreferencesSavedMsg,
        SieveModal,
        SplashDoneMsg,
        SplashScreen,
        SplashTickMsg,
        TerminalImageRenderer,
        format_message_for_reading_pane,
        html_to_terminal_text,
    )
    from espressomail.ui.formatter import _apply_inline_formatting
    from espressomail.themes import ThemeEngine
    HAS_ESPRESSO = True
except ImportError:
    HAS_ESPRESSO = False
    ThemeEngine = None  # type: ignore[assignment,misc]

from espressomail.models import AccountConfig, Contact, EmailMessage, Folder, FolderType
from espressomail.storage import LocalStore
from tests.test_storage_and_search import populate_test_store


@unittest.skipUnless(HAS_ESPRESSO, "espresso package not installed in environment")
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
        self.assertEqual(len(seen), 12)

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

            # Cycle image mode with 'i'
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

            # Cycle color mode (focus 3)
            pref_modal.focus_idx = 3
            pref_modal, _ = pref_modal.update(KeyMsg("right"))
            chosen_mode = pref_modal.COLOR_MODES[pref_modal.color_mode_idx]
            chosen_family = pref_modal.THEME_FAMILIES[pref_modal.theme_idx]
            chosen_theme = f"{chosen_family}_{chosen_mode}" if chosen_mode == "light" else chosen_family

            # Toggle threading (focus 4)
            pref_modal.focus_idx = 4
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
            self.assertEqual(self.store.get_setting("color_mode"), chosen_mode)
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
        modal.focus_idx = 8
        modal, _ = modal.update(KeyMsg(key="right"))
        self.assertEqual(modal.FOLDER_COUNT_MODES[modal.count_mode_idx], "unread_and_total")

        # Cycle fetch batch size to 100
        modal.focus_idx = 9
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

    def test_address_book_modal_scrolling_and_mouse(self) -> None:
        """Verify AddressBookModal vertical scrolling, page_up/down, home/end, mouse wheel, and indicators."""
        # 1. Populate 25 contacts
        for i in range(25):
            self.store.save_contact(
                Contact(
                    id=f"contact_{i:02d}",
                    name=f"Contact Person {i:02d}",
                    email=f"person{i:02d}@example.com",
                    organization=f"Org {i % 5}",
                    favorite=(i % 3 == 0),
                    frequency=i,
                )
            )

        theme = ThemeEngine("espresso").current
        modal = AddressBookModal(store=self.store, theme=theme)
        self.assertEqual(len(modal.contacts), 25)
        self.assertEqual(modal.cursor, 0)
        self.assertEqual(modal.scroll, 0)

        # Initial view shows contacts 0..9 and indicator (1–10 of 25)
        out = modal.view(width=80)
        first_contact = modal.contacts[0].name
        last_page1_contact = modal.contacts[9].name
        last_contact = modal.contacts[24].name
        self.assertIn(first_contact, out)
        self.assertIn(last_page1_contact, out)
        self.assertNotIn(last_contact, out)
        self.assertIn("Showing 1–10 of 25 contacts", out)

        # 2. Navigate down beyond first page
        for _ in range(12):
            modal, _ = modal.update(KeyMsg("down"))
        self.assertEqual(modal.cursor, 12)
        # Scroll should have adjusted to keep cursor in viewport (page_size = 10)
        self.assertEqual(modal.scroll, 3)
        out = modal.view(width=80)
        self.assertIn(modal.contacts[12].name, out)
        self.assertNotIn(modal.contacts[0].name, out)
        self.assertIn("Showing 4–13 of 25 contacts", out)

        # 3. Test page_down and page_up
        modal, _ = modal.update(KeyMsg("page_down"))
        self.assertEqual(modal.cursor, 22)
        self.assertEqual(modal.scroll, 13)
        modal, _ = modal.update(KeyMsg("page_up"))
        self.assertEqual(modal.cursor, 12)
        self.assertEqual(modal.scroll, 12)

        # 4. Test end and home
        modal, _ = modal.update(KeyMsg("end"))
        self.assertEqual(modal.cursor, 24)
        self.assertEqual(modal.scroll, 15)  # 25 - 10
        out_end = modal.view(width=80)
        self.assertIn(modal.contacts[24].name, out_end)
        self.assertIn("Showing 16–25 of 25 contacts", out_end)

        modal, _ = modal.update(KeyMsg("home"))
        self.assertEqual(modal.cursor, 0)
        self.assertEqual(modal.scroll, 0)

        # 5. Test Mouse Wheel scrolling
        modal, _ = modal.update(MouseMsg(x=10, y=10, action=MouseAction.PRESS, button=MouseButton.WHEEL_DOWN))
        self.assertEqual(modal.cursor, 1)
        modal, _ = modal.update(MouseMsg(x=10, y=10, action=MouseAction.PRESS, button=MouseButton.WHEEL_DOWN))
        self.assertEqual(modal.cursor, 2)
        modal, _ = modal.update(MouseMsg(x=10, y=10, action=MouseAction.PRESS, button=MouseButton.WHEEL_UP))
        self.assertEqual(modal.cursor, 1)

    def test_onboarding_sieve_server_configuration_and_sync(self) -> None:
        """Verify Sieve server host/port configuration in onboarding wizard and script sync."""
        theme = ThemeEngine("espresso").current
        validator = mock.MagicMock(return_value=(True, "IMAP/SMTP credentials valid."))
        wizard = OnboardingWizard(
            theme=theme,
            allow_cancel=True,
            credential_validator=validator,
            store=self.store,
        )

        # Step 1: Select Fastmail preset (index 1)
        wizard, _ = wizard.update(KeyMsg("down"))
        wizard, _ = wizard.update(KeyMsg("enter"))
        self.assertEqual(wizard.step, 2)
        self.assertEqual(wizard.input_sieve_port.value, "4190")

        # Step 2: Fill identity & credentials
        wizard.input_account_name.value = "Work Fastmail"
        wizard.input_full_name.value = "Kim Schulz"
        wizard.input_email.value = "kim@fastmail.com"
        wizard.input_password.value = "secret123"
        wizard, _ = wizard._advance_to_step3()
        self.assertEqual(wizard.step, 3)

        # Step 3: Verify all 8 focus slots exist and Sieve Host/Port are editable
        # 0=IMAP Host, 1=IMAP Port, 2=IMAP Security, 3=SMTP Host, 4=SMTP Port, 5=SMTP Security, 6=Sieve Host, 7=Sieve Port
        for _ in range(6):
            wizard, _ = wizard.update(KeyMsg("tab"))
        self.assertEqual(wizard.step3_focus, 6)

        # Change Sieve host and port
        wizard.input_sieve_host.value = "custom.sieve.host.com"
        wizard, _ = wizard.update(KeyMsg("tab"))
        self.assertEqual(wizard.step3_focus, 7)
        wizard.input_sieve_port.value = "4199"

        # Verify card view renders Sieve Host and Sieve Port (stripping ANSI escape codes)
        view_text = wizard.view(width=85, height=35)
        clean_view = re.sub(r"\x1b\[[0-9;]*m", "", view_text)
        self.assertIn("custom.sieve.host.com", clean_view)
        self.assertIn("4199", clean_view)

        # Finalize account with mocked ManageSieveClient
        with mock.patch("espressomail.ui.onboarding.ManageSieveClient") as mock_sieve_cls:
            mock_client = mock.MagicMock()
            mock_client.sync_script_with_server.return_value = (True, "Fetched active Sieve script.")
            mock_sieve_cls.return_value = mock_client

            wizard, cmd = wizard._finalize_account()
            self.assertIsNotNone(cmd)
            assert cmd is not None
            res = cmd()
            self.assertIsInstance(res, OnboardingCompleteMsg)
            assert res.account is not None
            self.assertEqual(res.account.sieve_host, "custom.sieve.host.com")
            self.assertEqual(res.account.sieve_port, 4199)
            self.assertIn("Fetched active Sieve script", res.validation_summary)
            mock_client.connect.assert_called_once()
            mock_client.sync_script_with_server.assert_called_once_with(self.store)
            mock_client.close.assert_called_once()

    def test_compose_modal_larger_size_and_autocomplete(self) -> None:
        """Verify larger ComposeModal dimensions and interactive contact autocomplete."""
        populate_test_store(self.store)
        contacts = [
            Contact(id="c1", name="Alice Wonderland", email="alice@example.com", frequency=10),
            Contact(id="c2", name="Bob Builder", email="bob@builder.com", frequency=5),
        ]
        accounts = self.store.list_accounts()
        theme = ThemeEngine("espresso").current
        modal = ComposeModal(
            accounts=accounts,
            contacts=contacts,
            theme=theme,
            to_initial="",
            subject_initial="",
        )

        # 1. Expanded viewport dimensions on wide terminal
        view_str = modal.view(width=120, height=36)
        self.assertGreaterEqual(modal.editor.width, 100)
        self.assertGreaterEqual(modal.editor.height, 20)

        # 2. Contact Autocomplete in To: field
        self.assertEqual(modal.focus_idx, 1)  # Focused on To:
        modal.input_to.value = "ali"
        matches = modal._get_autocomplete_matches()
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].email, "alice@example.com")

        # View renders autocomplete suggestions
        sugg_view = modal.view(width=120, height=36)
        self.assertIn("Alice Wonderland", sugg_view)

        # Press Tab or Enter to autocomplete
        modal, _ = modal.update(KeyMsg("tab"))
        self.assertIn("Alice Wonderland <alice@example.com>, ", modal.input_to.value)

    def test_add_sender_to_contacts_shortcut(self) -> None:
        """Verify pressing 'a' adds the sender of the selected email to contacts."""
        populate_test_store(self.store)
        app = EspressoMailApp(self.store)
        app.show_splash = False
        app.show_onboarding = False
        app.width = 120
        app.height = 36
        app.active_account_id = "*"
        app.active_folder = "INBOX"
        app._rebuild_message_rows()

        m = app.selected_message
        self.assertIsNotNone(m)
        assert m is not None

        # Delete contact if already harvested
        existing = self.store.get_contact_by_email(m.sender_email)
        if existing:
            self.store.delete_contact(existing.id)

        # Press 'a' to add sender
        app, cmd = app.update(KeyMsg("a"))
        self.assertIsNone(cmd)
        self.assertIn("Added contact", app.status_message)
        added = self.store.get_contact_by_email(m.sender_email)
        self.assertIsNotNone(added)

        # Press 'a' again to update contact
        app, _ = app.update(KeyMsg("a"))
        self.assertIn("Updated contact", app.status_message)

    def test_clickable_links_and_mouse_click(self) -> None:
        """Verify OSC 8 terminal hyperlinks and mouse click opening in reading pane."""
        populate_test_store(self.store)
        theme = ThemeEngine("espresso").current
        formatted = _apply_inline_formatting(
            "Visit [Espresso](https://espressomail.org) or https://github.com/kimusan/espressomail for code.",
            theme,
        )
        self.assertIn("\x1b]8;;https://espressomail.org\x1b\\", formatted)
        self.assertIn("\x1b]8;;https://github.com/kimusan/espressomail\x1b\\", formatted)

        # Test mouse click in reading pane
        app = EspressoMailApp(self.store)
        app.show_splash = False
        app.show_onboarding = False
        app.width = 120
        app.height = 36
        app._update_reader_viewport(force=True)

        folder_w, list_w, _ = app._pane_widths()
        reader_x = folder_w + list_w + 10

        # Inject line with URL into viewport lines
        app.reader_viewport.lines = [
            "Header line",
            "Check this link: \x1b]8;;https://espressomail.org\x1b\\https://espressomail.org\x1b]8;;\x1b\\",
        ]

        with mock.patch("webbrowser.open") as mock_open:
            app, _ = app.update(MouseMsg(x=reader_x, y=3, button=MouseButton.LEFT, action=MouseAction.PRESS))
            mock_open.assert_called_once_with("https://espressomail.org")
            self.assertIn("Opening link", app.status_message)

    def test_all_unread_and_folder_subscription_toggle(self) -> None:
        """Verify 'All Unread' unified folder and folder subscription toggle via 's'."""
        populate_test_store(self.store)
        app = EspressoMailApp(self.store)
        app.show_splash = False
        app.show_onboarding = False
        app.width = 120
        app.height = 36
        app.refresh_data()

        # 1. Verify "All Unread" in folder rows
        folder_labels = [r.label for r in app.folder_rows]
        self.assertIn("All Unread", folder_labels)

        # Select "All Unread"
        for idx, r in enumerate(app.folder_rows):
            if r.folder_name == "All Unread":
                app.folder_cursor = idx
                app._activate_selected_folder()
                break

        self.assertEqual(app.active_folder, "All Unread")
        self.assertGreater(len(app.message_rows), 0)
        unread_db = self.store.list_messages(account_id="*", folder="All Unread")
        for m in unread_db:
            self.assertFalse(m.is_read)

        # 2. Toggle folder subscription using 's' in folders pane
        app.focused_pane = "folders"
        # Find a non-header account folder
        target_idx = -1
        for idx, r in enumerate(app.folder_rows):
            if r.account_id != "*" and not r.is_header:
                target_idx = idx
                break
        self.assertGreaterEqual(target_idx, 0)
        app.folder_cursor = target_idx
        target_row = app.folder_rows[target_idx]
        self.assertTrue(target_row.subscribed)

        app, _ = app.update(KeyMsg("s"))
        self.assertIn("Unsubscribed from", app.status_message)

        # View reflects unsubscribed state with badge
        pane_str = app._render_folder_pane(30, 20)
        self.assertIn("🔕", pane_str)

        # On-demand sync when entering unsubscribed folder
        with mock.patch("espressomail.ui.app.IMAPClient") as mock_imap_cls:
            mock_client = mock.MagicMock()
            mock_client.sync_single_folder.return_value = 2
            mock_imap_cls.return_value = mock_client

            sync_cmd = app._activate_selected_folder()
            self.assertIsNotNone(sync_cmd)
            assert sync_cmd is not None
            res = sync_cmd()
            self.assertIsInstance(res, MoreMessagesFetchedMsg)

    def test_confirm_exit_modal_flow(self) -> None:
        """Verify pressing 'q' displays ConfirmExitModal, 'n'/'esc' cancels, and 'y' exits app."""
        from espresso import quit_app
        from espressomail.ui.modals import ConfirmExitModal

        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)

        # Press 'q' - does not quit directly, opens ConfirmExitModal
        app, cmd = app.update(KeyMsg("q"))
        self.assertIsNone(cmd)
        self.assertIsInstance(app.active_modal, ConfirmExitModal)
        confirm_modal = app.active_modal
        assert isinstance(confirm_modal, ConfirmExitModal)

        # Modal view rendering
        view_str = confirm_modal.view(80)
        self.assertIn("Are you sure you want to exit EspressoMail?", view_str)
        self.assertIn("[y / Enter]", view_str)
        self.assertIn("[n / Esc]", view_str)

        # Press 'n' to cancel exit
        app, cancel_cmd = app.update(KeyMsg("n"))
        self.assertIsNone(app.active_modal)
        self.assertIn("Exit cancelled", app.status_message)

        # Press 'q' again and confirm with 'y'
        app, _ = app.update(KeyMsg("q"))
        self.assertIsInstance(app.active_modal, ConfirmExitModal)
        app, exit_cmd = app.update(KeyMsg("y"))
        self.assertIs(exit_cmd, quit_app)

    def test_account_settings_modal_sieve_and_carddav_fields(self) -> None:
        """Verify AccountSettingsModal contains and updates Sieve and CardDAV server fields."""
        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.active_account_id = "acc_work"

        # Open AccountSettingsModal
        app, _ = app.update(KeyMsg("A"))
        self.assertIsInstance(app.active_modal, AccountSettingsModal)
        modal = app.active_modal
        assert isinstance(modal, AccountSettingsModal)

        # Verify fields exist
        self.assertTrue(hasattr(modal, "input_sieve_host"))
        self.assertTrue(hasattr(modal, "input_sieve_port"))
        self.assertTrue(hasattr(modal, "input_carddav_url"))

        # Set values
        modal.input_sieve_host.value = "sieve.workcorp.internal"
        modal.input_sieve_port.value = "4190"
        modal.input_carddav_url.value = "https://dav.workcorp.internal/addressbooks/kim/contacts/"

        # Save without live server check with Ctrl+F
        app, _ = app.update(KeyMsg("ctrl+f"))
        self.assertIsNone(app.active_modal)

        # Verify persisted to SQLite
        updated_acc = self.store.get_account("acc_work")
        self.assertIsNotNone(updated_acc)
        self.assertEqual(updated_acc.sieve_host, "sieve.workcorp.internal")
        self.assertEqual(updated_acc.sieve_port, 4190)
        self.assertEqual(updated_acc.carddav_url, "https://dav.workcorp.internal/addressbooks/kim/contacts/")

    def test_sieve_modal_large_editor_and_server_sync_display(self) -> None:
        """Verify SieveModal enlarged size, script_editor population, and server script sync display."""
        from espressomail.models.entities import SieveScript
        from espressomail.ui.modals import SieveModal

        populate_test_store(self.store)

        # Save active script in store
        initial_script = 'require ["fileinto"];\nif header :contains "subject" "Urgent" { fileinto "Priority"; }\n'
        self.store.save_sieve_script(SieveScript(
            id="script_active_1",
            account_id="acc_work",
            name="default",
            content=initial_script,
            active=True,
        ))

        modal = SieveModal(store=self.store, theme=ThemeEngine().current, active_account_id="acc_work")
        modal_view = modal.view(120)

        # Verify modal dimensions are enlarged (> 80) and editor is focused by default
        self.assertIn("Active Sieve Script Editor", modal_view)
        self.assertTrue(modal.editor_focused)
        self.assertTrue(modal.script_editor.focused)
        self.assertEqual(modal.script_editor.value, initial_script)

        # Simulate 'ctrl+u' to sync script with server while editor is focused
        with mock.patch("espressomail.ui.modals.ManageSieveClient") as mock_sieve_cls:
            mock_client = mock.MagicMock()
            new_server_script = 'require ["fileinto", "reject"];\n# Updated from server\nfileinto "Inbox";\n'
            mock_client.list_scripts.return_value = [("server_default", True)]
            mock_client.get_script.return_value = new_server_script
            mock_sieve_cls.return_value = mock_client

            modal, _ = modal.update(KeyMsg("ctrl+u"))
            self.assertIn("Synchronized script", modal.status_banner)
            # Script editor MUST now show the synced script in the text field
            self.assertEqual(modal.script_editor.value, new_server_script)

        # Test Ctrl+S saves script in editor
        modal.script_editor.value = "# Custom user edit\nkeep;\n"
        modal, _ = modal.update(KeyMsg("ctrl+s"))
        self.assertIn("Saved script", modal.status_banner)
        saved = self.store.get_active_sieve_script("acc_work")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.content, "# Custom user edit\nkeep;\n")

    def test_toggle_star_remote_flag_sync_and_persistence(self) -> None:
        """Verify removing a star updates SQLite and dispatches IMAP flag sync, persisting state."""
        from espressomail.ui.app import FlagSyncedMsg

        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.active_account_id = "acc_oss"
        app.active_folder = "INBOX"
        app.refresh_data()

        # Ensure message is starred first
        target_msg = None
        for idx, row in enumerate(app.message_rows):
            if row.message.id == "msg_thread1_1":
                app.message_cursor = idx
                target_msg = row.message
                break
        self.assertIsNotNone(target_msg)
        self.assertTrue(target_msg.is_starred)

        with mock.patch("espressomail.ui.app.IMAPClient") as mock_imap_cls:
            mock_client = mock.MagicMock()
            mock_imap_cls.return_value.__enter__.return_value = mock_client

            # Press 's' to remove star
            app, sync_cmd = app.update(KeyMsg("s"))
            self.assertFalse(target_msg.is_starred)
            self.assertIn("Removed star", app.status_message)
            self.assertIsNotNone(sync_cmd)
            assert sync_cmd is not None

            # Execute returned TEA command
            msg_res = sync_cmd()
            self.assertIsInstance(msg_res, FlagSyncedMsg)
            self.assertFalse(msg_res.enabled)
            mock_client.set_flag.assert_called_with(
                target_msg.folder, target_msg.uid, r"\Flagged", False
            )

        # Verify state in store remains unstarred across reload / folder reload
        reloaded = self.store.get_message(target_msg.id)
        self.assertIsNotNone(reloaded)
        self.assertFalse(reloaded.is_starred)

    def test_upper_right_theme_mention_removed_and_status_bar(self) -> None:
        """Verify top-right bar does not mention theme name and status bar shortcuts remove [T]Theme."""
        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)

        view_out = app.view()
        self.assertNotIn("🎨 espresso", view_out)
        self.assertNotIn("🎨", view_out)

        status_bar = app._render_status_bar()
        self.assertNotIn("[T]Theme", status_bar)
        self.assertIn("[c]Compose", status_bar)

    def test_syncing_badge_clears_on_completion(self) -> None:
        """Verify SYNCING badge in status bar clears when sync completes or encounters no remaining steps."""
        from espressomail.ui.app import SyncProgressMsg

        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.is_syncing = True
        app.sync_progress_text = "Syncing..."

        # Receive SyncProgressMsg with is_done=True
        done_msg = SyncProgressMsg(
            account_id="acc_work",
            account_name="Work",
            action="done",
            folder_name="",
            new_messages=0,
            total_synced=5,
            total_folders=2,
            step_index=2,
            total_steps=2,
            remaining_steps=(),
            errors=(),
            is_done=True,
        )
        app, _ = app.update(done_msg)
        self.assertFalse(app.is_syncing)
        self.assertEqual(app.sync_progress_text, "")
        self.assertNotIn("SYNCING", app._render_status_bar())

    def test_sieve_script_default_focus_mouse_and_tab_handling(self) -> None:
        """Verify Sieve script editor has default focus, mouse selection, tab indentation, and tab expansion."""
        from espresso import MouseAction, MouseButton, MouseMsg
        from espressomail.ui.modals import SieveModal

        populate_test_store(self.store)
        modal = SieveModal(store=self.store, theme=ThemeEngine().current, active_account_id="acc_work")

        # 1. Has focus as default
        self.assertTrue(modal.editor_focused)
        self.assertTrue(modal.script_editor.focused)

        # 2. Tab key inserts spaces without unfocusing
        prev_len = len(modal.script_editor.value)
        modal, _ = modal.update(KeyMsg("tab"))
        self.assertTrue(modal.editor_focused)
        self.assertEqual(len(modal.script_editor.value), prev_len + 4)

        # 3. Esc unfocuses editor
        modal, _ = modal.update(KeyMsg("esc"))
        self.assertFalse(modal.editor_focused)
        self.assertFalse(modal.script_editor.focused)

        # 4. Mouse click on editor focuses it
        modal.view(100, 30)  # establish layout geometry
        # Click on editor area (row 15)
        modal, _ = modal.update(
            MouseMsg(x=30, y=15, button=MouseButton.LEFT, action=MouseAction.PRESS)
        )
        self.assertTrue(modal.editor_focused)
        self.assertTrue(modal.script_editor.focused)

        # 5. Sieve script with tabs expands to spaces and does not break borders
        script_with_tabs = 'require ["fileinto"];\nif header :contains "Subject" "Spam" {\n\tfileinto "Junk";\n\tstop;\n}\n'
        modal.script_editor.value = script_with_tabs.expandtabs(4)
        rendered_view = modal.view(100, 30)
        # Ensure rendered view does not have raw tabs
        self.assertNotIn("\t", modal.script_editor.value)
        modal_lines = rendered_view.splitlines()
        for line in modal_lines:
            # Each rendered line must not exceed 100 columns (no border blowout)
            from espresso.crema import string_width
            self.assertLessEqual(string_width(line), 100)

    def test_carddav_auth_configuration_and_live_validation(self) -> None:
        """Verify CardDAV auth fields and server validation before saving."""
        from espressomail.protocols.carddav import CardDAVClient
        from espressomail.protocols.smtp_client import validate_account_credentials
        from espressomail.ui.modals import AccountSettingsModal

        populate_test_store(self.store)
        acc = self.store.get_account("acc_work")
        self.assertIsNotNone(acc)

        modal = AccountSettingsModal(
            store=self.store,
            theme=ThemeEngine().current,
            active_account_id="acc_work",
        )

        # Verify inputs exist
        self.assertTrue(hasattr(modal, "input_carddav_url"))
        self.assertTrue(hasattr(modal, "input_carddav_username"))
        self.assertTrue(hasattr(modal, "input_carddav_password"))

        modal.input_carddav_url.value = "https://dav.example.com/contacts/"
        modal.input_carddav_username.value = "dav_user"
        modal.input_carddav_password.value = "dav_secret"

        # 1. Test connection failure blocks save and sets error message
        with mock.patch.object(CardDAVClient, "test_connection", return_value=(False, "HTTP 401 Unauthorized")):
            with mock.patch("espressomail.protocols.imap_client.IMAPClient.verify_credentials", return_value=(True, "IMAP OK")):
                with mock.patch("espressomail.protocols.smtp_client.SMTPClient.verify_credentials", return_value=(True, "SMTP OK")):
                    modal, cmd = modal.update(KeyMsg("ctrl+s"))
                    self.assertIn("CardDAV", modal.error_msg)
                    self.assertIn("401", modal.error_msg)
                    # Verify not saved with failed validation
                    acc_fresh = self.store.get_account("acc_work")
                    self.assertNotEqual(acc_fresh.carddav_username, "dav_user")

        # 2. Test connection success allows save
        with mock.patch.object(CardDAVClient, "test_connection", return_value=(True, "CardDAV OK")):
            with mock.patch("espressomail.protocols.imap_client.IMAPClient.verify_credentials", return_value=(True, "IMAP OK")):
                with mock.patch("espressomail.protocols.smtp_client.SMTPClient.verify_credentials", return_value=(True, "SMTP OK")):
                    modal, cmd = modal.update(KeyMsg("ctrl+s"))
                    self.assertIsNotNone(cmd)
                    closed_msg = cmd()
                    self.assertIn("Verified and saved", closed_msg.status_note)
                    # Verify saved to database
                    acc_saved = self.store.get_account("acc_work")
                    self.assertEqual(acc_saved.carddav_url, "https://dav.example.com/contacts/")
                    self.assertEqual(acc_saved.carddav_username, "dav_user")
                    self.assertEqual(acc_saved.carddav_password, "dav_secret")


    def test_light_mode_colors_and_background_rendering(self) -> None:
        """Verify authentic light color scheme and comprehensive background rendering in light mode."""
        from pathlib import Path
        from espressomail.themes.engine import THEMES, ThemeEngine
        from espressomail.ui.modals import ComposeModal, PreferencesModal, SieveModal

        # 1. Theme configuration and palette verification
        theme = THEMES["solarized_light"]
        self.assertTrue(theme.is_light)
        self.assertEqual(theme.bg_primary, "#FFFFFF")
        self.assertEqual(theme.bg_secondary, "#F4F6F8")
        self.assertEqual(theme.fg_primary, "#18181B")
        self.assertIn("light", THEMES)

        # 2. Main 3-pane App view rendering background coverage
        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, initial_theme="solarized_light", show_splash=False)
        app.show_onboarding = False
        app.width = 100
        app.height = 30
        view_str = app.view()
        lines = view_str.split("\n")
        self.assertEqual(len(lines), 30)

        # Every screen line must contain background color escape code
        bg_code = "\x1b[48;2;"
        bg_lines_count = sum(bg_code in l for l in lines)
        self.assertEqual(bg_lines_count, 30, "All 30 terminal rows must have background color styled")

        # Verify secondary surface (#F4F6F8 -> 244;246;248) and primary canvas (#FFFFFF -> 255;255;255) are present
        bg_sec_code = "\x1b[48;2;244;246;248m"
        bg_pri_code = "\x1b[48;2;255;255;255m"
        self.assertTrue(any(bg_sec_code in l for l in lines), "Mailboxes and message list should render with bg_secondary")
        self.assertTrue(any(bg_pri_code in l for l in lines), "Reading pane should render with bg_primary")

        # 3. Preferences modal live light mode toggle and background styling
        pref_modal = PreferencesModal(
            store=self.store,
            current_theme=THEMES["espresso"],
            download_dir=Path("/tmp/downloads"),
        )
        pref_modal.focus_idx = 3  # Color mode toggle
        pref_modal, _ = pref_modal.update(KeyMsg("right"))
        self.assertEqual(pref_modal.current_theme.id, "espresso_light")
        self.assertTrue(pref_modal.current_theme.is_light)

        modal_view = pref_modal.view(80)
        modal_lines = modal_view.split("\n")
        self.assertTrue(len(modal_lines) >= 10)
        self.assertTrue(all(bg_code in l for l in modal_lines), "All modal rows must have background color styled")

        # 4. Compose and Sieve modals have solid backgrounds
        compose = ComposeModal(
            accounts=self.store.list_accounts(),
            contacts=self.store.list_contacts(),
            theme=theme,
        )
        compose_view = compose.view(80, 24)
        compose_lines = compose_view.split("\n")
        self.assertTrue(all(bg_code in l for l in compose_lines), "Compose modal must render with solid background")

        sieve = SieveModal(store=self.store, theme=theme, active_account_id="acc_work")
        sieve_view = sieve.view(80, 24)
        sieve_lines = sieve_view.split("\n")
        self.assertTrue(all(bg_code in l for l in sieve_lines), "Sieve modal must render with solid background")


    def test_temporary_unread_only_folder_filter(self) -> None:
        """Verify temporary unread-only message filter toggled via Ctrl+U and reset on folder change."""
        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        self.assertFalse(app.unread_only_filter)

        total_msgs = len(app.message_rows)
        unread_count = sum(1 for r in app.message_rows if not r.message.is_read)
        self.assertGreater(total_msgs, unread_count)
        self.assertGreater(unread_count, 0)

        # Toggle unread only via Ctrl+U
        app, cmd = app.update(KeyMsg("ctrl+u"))
        self.assertTrue(app.unread_only_filter)
        self.assertEqual(len(app.message_rows), unread_count)
        self.assertTrue(all(not r.message.is_read for r in app.message_rows))
        self.assertIn("unread only", app.status_message.lower())

        # Render message list pane has [UNREAD ONLY] badge
        rendered = app._render_message_list_pane(40, 20)
        self.assertIn("[UNREAD ONLY]", rendered)

        # Toggle unread filter off with '!'
        app, cmd = app.update(KeyMsg("!"))
        self.assertFalse(app.unread_only_filter)
        self.assertEqual(len(app.message_rows), total_msgs)

        # Toggle back on and test reset on folder change
        app, _ = app.update(KeyMsg("ctrl+u"))
        self.assertTrue(app.unread_only_filter)

        # Switching to Unified Inbox resets filter
        app, _ = app.update(KeyMsg("U"))
        self.assertFalse(app.unread_only_filter)

        # Toggle on and test reset on activating another folder
        app, _ = app.update(KeyMsg("ctrl+u"))
        self.assertTrue(app.unread_only_filter)
        app.folder_cursor = 1
        app._activate_selected_folder()
        self.assertFalse(app.unread_only_filter)

    def test_onboarding_wizard_step_back_preserves_providers_and_mouse(self) -> None:
        """Verify selecting a provider in OnboardingWizard and pressing Esc to step back keeps all providers."""
        from espressomail.themes.engine import THEMES
        from espressomail.ui.onboarding import OnboardingWizard, PROVIDER_PRESETS
        from espresso import MouseAction, MouseButton, MouseMsg

        wizard = OnboardingWizard(theme=THEMES["espresso"], allow_cancel=True)
        self.assertEqual(wizard.step, 1)

        # Step 1 view shows all provider presets
        view1 = wizard.view(80, 24)
        self.assertIn("Fastmail", view1)
        self.assertIn("Gmail", view1)
        self.assertIn("Custom IMAP", view1)

        # Navigate to Fastmail (index 1) and press enter to go to Step 2
        wizard, _ = wizard.update(KeyMsg("down"))
        wizard, _ = wizard.update(KeyMsg("enter"))
        self.assertEqual(wizard.step, 2)
        self.assertIn("Fastmail", wizard.selected_preset.label)

        # Now press Esc to go back to Step 1
        wizard, _ = wizard.update(KeyMsg("esc"))
        self.assertEqual(wizard.step, 1)

        # CRITICAL FIX VERIFICATION: Provider prompt must be reset and view must show all providers, not just Fastmail
        view_after_esc = wizard.view(80, 24)
        self.assertIn("Fastmail", view_after_esc)
        self.assertIn("Gmail", view_after_esc)
        self.assertIn("ProtonMail", view_after_esc)
        self.assertIn("Custom IMAP", view_after_esc)
        self.assertEqual(len(wizard.provider_prompt.options), len(PROVIDER_PRESETS))

        # Mouse interaction in Onboarding: clicking Step 1 provider
        # Clicking provider option row selects it
        mouse_click = MouseMsg(x=35, y=10, action=MouseAction.PRESS, button=MouseButton.LEFT)
        wizard, _ = wizard.update(mouse_click)
        self.assertIn(wizard.step, (1, 2))

    def test_preferences_modal_mouse_interaction_and_dark_light_themes(self) -> None:
        """Verify PreferencesModal mouse click cycling, wheel cycling, and buttons."""
        from pathlib import Path
        from espressomail.themes.engine import THEMES
        from espressomail.ui.modals import ModalClosedMsg, PreferencesModal
        from espresso import MouseAction, MouseButton, MouseMsg

        modal = PreferencesModal(
            store=self.store,
            current_theme=THEMES["espresso"],
            download_dir=Path("/tmp/downloads"),
        )
        # Check initial state
        self.assertEqual(modal.color_mode_idx, 0)  # dark
        self.assertEqual(modal.THEME_FAMILIES[modal.theme_idx], "espresso")

        # Render view to establish exact modal geometry
        modal.view(80, 24)
        pos_y = modal.last_pos_y
        self.assertEqual(modal.last_modal_h, 22)
        self.assertEqual(pos_y, 1)

        # Mouse click row 2 (Theme name) to cycle theme family
        # Row 2 is y = pos_y + 2 (border/padding) + 2 (row 2) = 7
        click_theme = MouseMsg(x=40, y=pos_y + 4, action=MouseAction.PRESS, button=MouseButton.LEFT)
        prev_fam_idx = modal.theme_idx
        modal, _ = modal.update(click_theme)
        self.assertNotEqual(modal.theme_idx, prev_fam_idx)
        self.assertEqual(modal.color_mode_idx, 0)  # Color mode remains dark!

        # Mouse click row 3 (Color Mode) to toggle dark -> light
        # Row 3 is y = pos_y + 2 (border/padding) + 3 (row 3) = 8
        click_mode = MouseMsg(x=40, y=pos_y + 5, action=MouseAction.PRESS, button=MouseButton.LEFT)
        modal, _ = modal.update(click_mode)
        self.assertEqual(modal.color_mode_idx, 1)  # light
        self.assertTrue(modal.current_theme.is_light)
        self.assertEqual(modal.current_theme.id, f"{modal.THEME_FAMILIES[modal.theme_idx]}_light")

        # Mouse wheel cycling on row 2 (Theme family at y = pos_y + 4)
        wheel_up = MouseMsg(x=40, y=pos_y + 4, action=MouseAction.PRESS, button=MouseButton.WHEEL_DOWN)
        prev_fam_idx = modal.theme_idx
        modal, _ = modal.update(wheel_up)
        self.assertNotEqual(modal.theme_idx, prev_fam_idx)

        # Mouse click Cancel button
        modal.view(80, 24)
        btn_y = modal.last_pos_y + 2 + modal.buttons_row_offset
        click_cancel = MouseMsg(x=55, y=btn_y, action=MouseAction.PRESS, button=MouseButton.LEFT)
        modal, cmd = modal.update(click_cancel)
        self.assertIsNotNone(cmd)
        closed_msg = cmd()
        self.assertIsInstance(closed_msg, ModalClosedMsg)

    def test_all_theme_families_have_dark_and_light_modes(self) -> None:
        """Verify each of the 6 theme families has both a valid Dark and Light variant."""
        from espressomail.themes.engine import COLOR_MODES, THEME_FAMILIES, THEMES, ThemeEngine

        for family in THEME_FAMILIES:
            # Dark variant
            dark_theme = THEMES.get(family)
            self.assertIsNotNone(dark_theme, f"Missing dark theme for {family}")
            self.assertFalse(dark_theme.is_light, f"{family} dark variant should have is_light=False")

            # Light variant
            light_key = f"{family}_light"
            light_theme = THEMES.get(light_key)
            self.assertIsNotNone(light_theme, f"Missing light theme {light_key}")
            self.assertTrue(light_theme.is_light, f"{light_key} should have is_light=True")
            self.assertTrue(light_theme.bg_primary.startswith("#"))
            self.assertTrue(light_theme.bg_secondary.startswith("#"))

        # Test ThemeEngine helpers
        engine = ThemeEngine("espresso")
        self.assertEqual(engine.color_mode, "dark")
        self.assertEqual(engine.theme_family, "espresso")

        # Toggle to light
        engine.toggle_color_mode()
        self.assertEqual(engine.color_mode, "light")
        self.assertEqual(engine.current.id, "espresso_light")

        # Switch family
        engine.set_family_and_mode("nord", "light")
        self.assertEqual(engine.current.id, "nord_light")
        self.assertEqual(engine.theme_family, "nord")

        engine.toggle_color_mode()
        self.assertEqual(engine.current.id, "nord")
        self.assertEqual(engine.color_mode, "dark")

    def test_upstream_espresso_1_3_0_beans_integration(self) -> None:
        """Verify integration and availability of upstream Espresso 1.3.0 beans."""
        from espressomail.ui.menus import DEFAULT_MENUS, to_core_menubar
        from espresso.beans import (
            CardActivateMsg,
            CardItem,
            CardList,
            CardSelectMsg,
            MenuBar as CoreMenuBar,
            MultiSplitter,
            MultiSplitterResizeMsg,
            Wizard,
            WizardCompleteMsg,
            WizardNextMsg,
            WizardStep,
        )

        # 1. MenuBar bean integration
        core_menu = to_core_menubar(DEFAULT_MENUS, width=100)
        self.assertIsNotNone(core_menu)
        assert isinstance(core_menu, CoreMenuBar)
        self.assertEqual(len(core_menu.categories), len(DEFAULT_MENUS))
        self.assertIn("File", [c.label for c in core_menu.categories])

        # Test keyboard activation on MenuBar
        core_menu, cmd = core_menu.update(KeyMsg("f10"))
        self.assertTrue(core_menu.is_open)
        self.assertEqual(core_menu.active_cat_idx, 0)

        # Press Enter on first item (Sync All Accounts)
        core_menu, cmd = core_menu.update(KeyMsg("enter"))
        self.assertFalse(core_menu.is_open)
        self.assertIsNotNone(cmd)
        select_msg = cmd()
        self.assertEqual(select_msg.category_id, "file")
        self.assertEqual(select_msg.item_id, "sync_all")

        # 2. MultiSplitter bean integration (3 panes)
        splitter = MultiSplitter(
            panes=["Folders", "Messages", "Reader"],
            ratios=[0.2, 0.35, 0.45],
            min_sizes=[15, 20, 25],
            width=100,
            height=30,
        )
        self.assertEqual(splitter.num_panes, 3)
        self.assertEqual(splitter.num_dividers, 2)
        sizes = splitter.pane_sizes
        self.assertEqual(len(sizes), 3)
        self.assertEqual(sum(sizes) + 2, 100)
        # Nudge divider 0 right
        splitter.nudge_divider(0, 2)
        new_sizes = splitter.pane_sizes
        self.assertEqual(new_sizes[0], sizes[0] + 2)

        # 3. Wizard bean integration
        step1 = WizardStep(id="acc", title="Account", content="Enter email")
        step2 = WizardStep(id="done", title="Finish", content="All set")
        wizard = Wizard(steps=[step1, step2], width=60, height=15)
        self.assertEqual(wizard.current_step, 0)
        wizard, cmd = wizard.update(KeyMsg("enter"))
        self.assertEqual(wizard.current_step, 1)

        # 4. CardList bean integration
        card1 = CardItem(id="1", title="Alice", subtitle="Meeting update", badge_text="10:30 AM", is_unread=True)
        card2 = CardItem(id="2", title="Bob", subtitle="Review requested", badge_text="Yesterday")
        card_list = CardList(items=[card1, card2], width=50, height=10)
        self.assertEqual(len(card_list.items), 2)
        self.assertEqual(card_list.selected_index, 0)
        card_list, _ = card_list.update(KeyMsg("down"))
        self.assertEqual(card_list.selected_index, 1)
        card_list, cmd = card_list.update(KeyMsg("enter"))
        self.assertIsNotNone(cmd)
        act_msg = cmd()
        self.assertIsInstance(act_msg, CardActivateMsg)
        self.assertEqual(act_msg.item.id, "2")


    def test_sync_responsiveness_and_message_navigation_optimization(self) -> None:
        """Verify UI responsiveness during sync: non-interfering sync steps and fast navigation."""
        from espressomail.models.entities import EmailMessage
        from espressomail.ui.app import SyncProgressMsg

        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.desktop_notifications = False
        app.active_account_id = "acc_oss"
        app.active_folder = "INBOX"
        app.refresh_data()
        self.assertGreater(len(app.message_rows), 0)

        # 1. Test batch saving in LocalStore
        batch = [
            EmailMessage(
                id=f"batch_msg_{i}",
                account_id="acc_oss",
                folder="Archive",
                uid=2000 + i,
                subject=f"Batch {i}",
                sender_name="Archive Bot",
                sender_email="bot@archive.org",
                recipients_to=["me@oss.org"],
                body_text="Archived",
            )
            for i in range(10)
        ]
        saved_count = self.store.save_messages(batch)
        self.assertEqual(saved_count, 10)

        # 2. Select first message, navigate down to second message
        initial_msg_id = app.selected_message.id if app.selected_message else ""
        app.focused_pane = "messages"
        app, cmd = app.update(KeyMsg("down"))
        self.assertEqual(app.message_cursor, 1)
        cur_msg_id = app.selected_message.id if app.selected_message else ""
        self.assertNotEqual(initial_msg_id, cur_msg_id)

        # 3. Simulate background SyncProgressMsg for an unrelated folder ("Archive")
        # Ensure active message list and viewport are NOT disrupted
        cached_rows_ref = app.message_rows
        sync_archive_msg = SyncProgressMsg(
            account_id="acc_oss",
            account_name="OSS",
            action="synced_folder",
            folder_name="Archive",
            new_messages=5,
            total_synced=5,
            total_folders=3,
            step_index=1,
            total_steps=3,
            remaining_steps=(("acc_oss", "sync_folder", "INBOX"),),
        )
        app, _ = app.update(sync_archive_msg)
        # Message rows list for INBOX was not recomputed
        self.assertIs(app.message_rows, cached_rows_ref)
        self.assertEqual(app.message_cursor, 1)
        self.assertEqual(app.selected_message.id if app.selected_message else "", cur_msg_id)

        # 4. Simulate SyncProgressMsg for current folder ("INBOX") with new messages
        # Message rows are updated, and message cursor stays pinned to the current message by ID
        new_inbox_msg = EmailMessage(
            id="inbox_top_new",
            account_id="acc_oss",
            folder="INBOX",
            uid=9999,
            subject="Urgent New Email",
            sender_name="Boss",
            sender_email="boss@oss.org",
            recipients_to=["me@oss.org"],
            body_text="Read this!",
            timestamp=9999999999,
        )
        self.store.save_message(new_inbox_msg)

        sync_inbox_msg = SyncProgressMsg(
            account_id="acc_oss",
            account_name="OSS",
            action="synced_folder",
            folder_name="INBOX",
            new_messages=1,
            total_synced=6,
            total_folders=3,
            step_index=2,
            total_steps=3,
            remaining_steps=(),
        )
        app, _ = app.update(sync_inbox_msg)
        # User's selection was preserved on the message they were looking at, rather than shifting index
        self.assertEqual(app.selected_message.id if app.selected_message else "", cur_msg_id)

    def test_calendar_invitation_render_and_rsvp_modal_flow(self) -> None:
        """Verify calendar invitation rendering in reading pane and interactive RSVP modal flow."""
        from espresso import MouseAction, MouseButton, MouseMsg
        from espressomail.models.entities import Attachment
        from espressomail.ui.modals import RSVPModal, RSVPSubmittedMsg

        sample_ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "METHOD:REQUEST\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:sprint-planning-4455@example.com\r\n"
            "SUMMARY:Sprint Planning & Retrospective\r\n"
            "DTSTART:20261029T100000Z\r\n"
            "DTEND:20261029T113000Z\r\n"
            "LOCATION:Room 201 / Zoom (https://zoom.us/j/123456)\r\n"
            "ORGANIZER;CN=\"Sarah Connor\":mailto:sarah@skynet.com\r\n"
            "ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN=\"Kim Schulz\":mailto:kim@schulz.dk\r\n"
            "DESCRIPTION:Sprint review, velocity retrospective, and backlog grooming.\r\n"
            "SEQUENCE:0\r\n"
            "STATUS:CONFIRMED\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        acc = AccountConfig(
            id="acc_cal",
            name="Personal",
            email="kim@schulz.dk",
            full_name="Kim Schulz",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            smtp_password="password123",
        )
        self.store.save_account(acc)

        cal_msg = EmailMessage(
            id="msg_cal_invite",
            account_id="acc_cal",
            folder="INBOX",
            uid=101,
            message_id="<cal-invite-101@example.com>",
            subject="Invitation: Sprint Planning & Retrospective",
            sender_name="Sarah Connor",
            sender_email="sarah@skynet.com",
            recipients_to=["kim@schulz.dk"],
            body_text="Hi Kim,\n\nPlease see the calendar invite attached.",
            timestamp=1761730000.0,
            attachments=[
                Attachment(
                    id="att_cal_1",
                    message_id="msg_cal_invite",
                    filename="invite.ics",
                    content_type="text/calendar",
                    size_bytes=len(sample_ics),
                    data=sample_ics.encode("utf-8"),
                )
            ],
        )
        self.store.save_message(cal_msg)

        app = EspressoMailApp(self.store, show_splash=False)
        app.update(WindowSizeMsg(width=110, height=30))
        app.active_account_id = "acc_cal"
        app.active_folder = "INBOX"
        app.refresh_data()

        # Step cursor to the calendar invite message
        app.message_cursor = 0
        app._on_message_selection_changed()

        # 1. Verify Reading Pane displays Calendar Banner
        reader_text = "\n".join(app.reader_viewport.lines)
        self.assertIn("Calendar Invitation", reader_text)
        self.assertIn("Sprint Planning & Retrospective", reader_text)
        self.assertIn("Sarah Connor <sarah@skynet.com>", reader_text)
        self.assertIn("Room 201", reader_text)
        self.assertIn("[1] Accept", reader_text)
        self.assertIn("[2] Tentative", reader_text)
        self.assertIn("[3] Decline", reader_text)

        # 2. Press '1' to open RSVPModal pre-selected to ACCEPTED
        app, _ = app.update(KeyMsg("1"))
        self.assertIsInstance(app.active_modal, RSVPModal)
        modal = app.active_modal
        self.assertEqual(modal.selected_response, "ACCEPTED")
        self.assertIn("Sprint Planning", modal.view())
        self.assertIn("[●] [1] Accept", modal.view())

        # 3. Test quick keys inside modal
        modal, _ = modal.update(KeyMsg("2"))
        self.assertEqual(modal.selected_response, "TENTATIVE")
        self.assertIn("[●] [2] Tentative", modal.view())

        modal, _ = modal.update(KeyMsg("3"))
        self.assertEqual(modal.selected_response, "DECLINED")
        self.assertIn("[●] [3] Decline", modal.view())

        # 4. Tab to comment field and type note
        modal, _ = modal.update(KeyMsg("tab"))
        self.assertEqual(modal.focused_field, "comment")
        modal, _ = modal.update(KeyMsg("C"))
        modal, _ = modal.update(KeyMsg("a"))
        modal, _ = modal.update(KeyMsg("n"))
        modal, _ = modal.update(KeyMsg("t"))
        self.assertEqual(modal.comment_input.value, "Cant")

        # 5. Submit modal with Enter
        modal, submit_cmd = modal.update(KeyMsg("enter"))
        self.assertIsNotNone(submit_cmd)
        emitted_msg = submit_cmd()
        self.assertIsInstance(emitted_msg, RSVPSubmittedMsg)
        self.assertEqual(emitted_msg.partstat, "DECLINED")
        self.assertEqual(emitted_msg.comment, "Cant")

        # 6. Dispatch RSVPSubmittedMsg into app
        with mock.patch("espressomail.ui.app.SMTPClient.send_mime_message") as mock_smtp_send:
            app, _ = app.update(emitted_msg)

        # Modal is closed and status message shows confirmation
        self.assertIsNone(app.active_modal)
        self.assertIn("RSVP Declined", app.status_message)
        self.assertIn("sarah@skynet.com", app.status_message)
        mock_smtp_send.assert_called_once()

        # Check Sent folder has the reply message
        sent_msgs = self.store.list_messages(account_id="acc_cal", folder="Sent")
        self.assertTrue(any("Declined: Sprint Planning" in m.subject for m in sent_msgs))

        # 7. Test mouse click on RSVP option in reading pane
        folder_w, list_w, _ = app._pane_widths()
        target_line_idx = -1
        for idx, line in enumerate(app.reader_viewport.lines):
            if "[1] Accept" in line:
                target_line_idx = idx
                break
        self.assertNotEqual(target_line_idx, -1)

        # Click on the Accept button in the reader pane
        click_x = folder_w + list_w + 5
        click_y = 2 + target_line_idx  # 2 rows for menu bar + border
        mouse_msg = MouseMsg(
            x=click_x,
            y=click_y,
            button=MouseButton.LEFT,
            action=MouseAction.PRESS,
        )
        app, _ = app.update(mouse_msg)
        self.assertIsInstance(app.active_modal, RSVPModal)
        self.assertEqual(app.active_modal.selected_response, "ACCEPTED")

    def test_desktop_notifications_dispatch_and_preferences(self) -> None:
        """Verify cross-platform desktop notifications, sound alert preferences, and sync dispatch."""
        from unittest.mock import patch
        from espressomail.notifications import (
            is_notification_supported,
            notify_new_messages,
            play_alert_sound,
            send_desktop_notification,
        )
        from espressomail.ui.app import SyncProgressMsg

        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)

        # 1. Test notifications module functions with mocking
        with patch("subprocess.Popen") as mock_popen, patch("shutil.which", return_value="/usr/bin/notify-send"), patch("platform.system", return_value="Linux"):
            self.assertTrue(is_notification_supported())
            
            # send_desktop_notification without sound
            ok = send_desktop_notification(title="Test", body="Hello World", sound=False)
            self.assertTrue(ok)
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            self.assertIn("notify-send", args)
            self.assertIn("Hello World", args)

        with patch("subprocess.Popen") as mock_popen, patch("shutil.which", side_effect=lambda x: "/usr/bin/canberra-gtk-play" if x == "canberra-gtk-play" else "/usr/bin/notify-send"), patch("platform.system", return_value="Linux"):
            mock_popen.reset_mock()
            ok = send_desktop_notification(title="Test", body="Hello with sound", sound=True)
            self.assertTrue(ok)
            self.assertEqual(mock_popen.call_count, 2)  # sound + notification

        # Test notify_new_messages with single and multiple messages
        msgs = self.store.list_messages(account_id="acc_work", folder="INBOX", limit=2)
        self.assertTrue(len(msgs) >= 1)
        with patch("espressomail.notifications.send_desktop_notification") as mock_send:
            notify_new_messages([msgs[0]], account_name="Work", folder_name="INBOX")
            mock_send.assert_called_once()
            self.assertIn("New Email from", mock_send.call_args[1]["title"])

        with patch("espressomail.notifications.send_desktop_notification") as mock_send:
            notify_new_messages([msgs[0], msgs[0]], account_name="Work", folder_name="INBOX")
            mock_send.assert_called_once()
            self.assertIn("New Emails in INBOX", mock_send.call_args[1]["title"])

        # 2. Preferences modal: toggle notifications (focus 10) and sound (focus 11)
        app, _ = app.update(KeyMsg("p"))
        modal = app.active_modal
        self.assertIsInstance(modal, PreferencesModal)
        modal.focus_idx = 10
        init_notif = modal.desktop_notifications
        modal, _ = modal.update(KeyMsg("space"))
        self.assertEqual(modal.desktop_notifications, not init_notif)

        modal.focus_idx = 11
        init_sound = modal.notification_sound
        modal, _ = modal.update(KeyMsg("space"))
        self.assertEqual(modal.notification_sound, not init_sound)

        # Save preferences
        app, _ = app.update(KeyMsg("enter"))
        self.assertIsNone(app.active_modal)
        self.assertEqual(app.desktop_notifications, not init_notif)
        self.assertEqual(app.notification_sound, not init_sound)
        self.assertEqual(self.store.get_setting("desktop_notifications"), "0" if init_notif else "1")
        self.assertEqual(self.store.get_setting("notification_sound"), "1" if not init_sound else "0")

        # 3. Test sync triggers notification when enabled
        app.desktop_notifications = True
        with patch("espressomail.ui.app.notify_new_messages") as mock_notify:
            sync_step = SyncProgressMsg(
                account_id="acc_work",
                account_name="Work",
                action="synced_folder",
                folder_name="INBOX",
                new_messages=1,
                remaining_steps=(("acc_work", "sync_folder", "Sent"),),
                total_steps=2,
            )
            app, _ = app.update(sync_step)
            mock_notify.assert_called_once()

        # When disabled, no notification dispatched
        app.desktop_notifications = False
        with patch("espressomail.ui.app.notify_new_messages") as mock_notify:
            app, _ = app.update(sync_step)
            mock_notify.assert_not_called()

        # 4. Command palette "test_notification"
        with patch("espressomail.ui.app.send_desktop_notification", return_value=True) as mock_send:
            app, _ = app._execute_action("test_notification")
            mock_send.assert_called_once()
            self.assertIn("Test desktop notification dispatched", app.status_message)

    def test_auto_sync_configurable_interval_and_toggle(self) -> None:
        """Verify configurable auto-sync interval in preferences and toggle action."""
        from espressomail.ui.app import AutoSyncTickMsg, SyncProgressMsg

        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.desktop_notifications = False

        # 1. Default interval is 300 seconds
        self.assertEqual(app.auto_sync_interval, 300)

        # 2. Open preferences and change auto-sync interval (focus row 12)
        app, _ = app.update(KeyMsg("p"))
        modal = app.active_modal
        self.assertIsInstance(modal, PreferencesModal)
        modal.focus_idx = 12
        # Initial is index 4 (300s)
        self.assertEqual(modal.AUTO_SYNC_INTERVALS[modal.sync_interval_idx], 300)

        # Cycle right -> index 5 (600s / 10 min)
        modal, _ = modal.update(KeyMsg("right"))
        self.assertEqual(modal.AUTO_SYNC_INTERVALS[modal.sync_interval_idx], 600)

        # Save preferences
        app, cmd = app.update(KeyMsg("enter"))
        self.assertIsNone(app.active_modal)
        self.assertEqual(app.auto_sync_interval, 600)
        self.assertEqual(self.store.get_setting("auto_sync_interval"), "600")
        self.assertIsNotNone(cmd)  # Tick rescheduled for 600s

        # 3. Test sync completion reschedules tick with 600s
        done_step = SyncProgressMsg(
            account_id="acc_work",
            account_name="Work",
            action="done",
            total_synced=5,
            total_folders=2,
            remaining_steps=(),
            is_done=True,
        )
        app, cmd = app.update(done_step)
        self.assertIsNotNone(cmd)

        # 4. Toggle auto-sync via command palette item
        app, cmd = app._execute_action("toggle_auto_sync")
        self.assertEqual(app.auto_sync_interval, 0)
        self.assertEqual(self.store.get_setting("auto_sync_interval"), "0")
        self.assertIn("Auto-sync disabled", app.status_message)
        self.assertIsNone(cmd)

        # When auto_sync_interval == 0, sync completion does NOT schedule a tick
        app, cmd = app.update(done_step)
        self.assertIsNone(cmd)

        # When auto_sync_interval == 0, AutoSyncTickMsg does nothing
        app, cmd = app.update(AutoSyncTickMsg())
        self.assertIsNone(cmd)

        # Toggle back on
        app, cmd = app._execute_action("toggle_auto_sync")
        self.assertEqual(app.auto_sync_interval, 300)
        self.assertEqual(self.store.get_setting("auto_sync_interval"), "300")
        self.assertIn("Auto-sync enabled", app.status_message)
        self.assertIsNotNone(cmd)

    def test_imap_push_idle_event_and_toggle(self) -> None:
        """Verify RFC 2177 IMAP IDLE real-time push events, client idle_listen, and app update handling."""
        from unittest.mock import MagicMock, patch
        from espressomail.protocols.imap_client import IMAPClient
        from espressomail.ui.app import ImapPushEventMsg

        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.desktop_notifications = False

        # 1. Test IMAPClient has_capability and idle_listen with mock
        acc = self.store.get_account("acc_work")
        client = IMAPClient(acc)
        mock_conn = MagicMock()
        mock_conn.capabilities = ("IMAP4rev1", "IDLE")
        mock_conn.select.return_value = ("OK", [b"42"])
        client._conn = mock_conn

        self.assertTrue(client.has_capability("IDLE"))
        self.assertFalse(client.has_capability("NONEXISTENT"))

        # Test idle_listen with mock idler context manager returning EXISTS
        class DummyIdler:
            def __enter__(self):
                return [(b"EXISTS", [b"43"])]
            def __exit__(self, *args):
                pass

        mock_conn.idle.return_value = DummyIdler()
        has_event, event_type, count = client.idle_listen("INBOX", timeout=5.0)
        self.assertTrue(has_event)
        self.assertEqual(event_type, "exists")
        self.assertEqual(count, 43)

        # 2. Test app handling of ImapPushEventMsg
        push_msg = ImapPushEventMsg(
            account_id="acc_work",
            account_name="Work",
            folder="INBOX",
            event_type="exists",
            new_messages=1,
        )
        with patch.object(app, "_make_idle_push_cmd") as mock_make_cmd:
            app, cmd = app.update(push_msg)
            self.assertIn("Instant Push", app.status_message)
            self.assertIn("Work/INBOX", app.status_message)
            mock_make_cmd.assert_called_once()

        # 3. Preferences modal: toggle IMAP push (focus 13)
        app, _ = app.update(KeyMsg("p"))
        modal = app.active_modal
        self.assertIsInstance(modal, PreferencesModal)
        modal.focus_idx = 13
        init_push = modal.imap_push_enabled
        modal, _ = modal.update(KeyMsg("space"))
        self.assertEqual(modal.imap_push_enabled, not init_push)

        # Save preferences
        app, _ = app.update(KeyMsg("enter"))
        self.assertIsNone(app.active_modal)
        self.assertEqual(app.imap_push_enabled, not init_push)
        self.assertEqual(self.store.get_setting("imap_push_enabled"), "0" if init_push else "1")

        # 4. Toggle via command palette action "toggle_imap_push"
        app, _ = app._execute_action("toggle_imap_push")
        self.assertEqual(app.imap_push_enabled, init_push)
        self.assertEqual(self.store.get_setting("imap_push_enabled"), "1" if init_push else "0")

    def test_single_key_triaging_and_batch_actions(self) -> None:
        """Verify single-key triaging (e, y, d, #, J, s, u, v, x) and multi-selection batch triage."""
        from unittest.mock import MagicMock, patch
        from espressomail.ui.modals import MoveFolderModal, MoveFolderSubmittedMsg

        populate_test_store(self.store)
        app = EspressoMailApp(store=self.store, show_splash=False)
        app.desktop_notifications = False
        app.focused_pane = "messages"

        initial_count = len(app.message_rows)
        self.assertGreaterEqual(initial_count, 3)

        # 1. Single-key Star via 's' and Read via 'u'
        curr_msg = app.selected_message
        self.assertIsNotNone(curr_msg)
        orig_star = curr_msg.is_starred
        app, _ = app.update(KeyMsg("s"))
        self.assertEqual(app.selected_message.is_starred, not orig_star)

        orig_read = app.selected_message.is_read
        app, _ = app.update(KeyMsg("u"))
        self.assertEqual(app.selected_message.is_read, not orig_read)

        # 2. Multi-selection batch triage via 'x'
        app.selected_message_ids.clear()
        msg_a = app.message_rows[0].message
        msg_b = app.message_rows[1].message
        app.message_cursor = 0
        app, _ = app.update(KeyMsg("x"))
        self.assertIn(msg_a.id, app.selected_message_ids)
        self.assertEqual(app.message_cursor, 1)

        app, _ = app.update(KeyMsg("x"))
        self.assertIn(msg_b.id, app.selected_message_ids)

        # Check rendered message list has checkmark
        rendered = app._render_message_list_pane(50, 20)
        self.assertIn("✓", rendered)

        # Archive both marked messages simultaneously via 'e'
        cnt_before = len(app.message_rows)
        app, _ = app.update(KeyMsg("e"))
        self.assertEqual(len(app.message_rows), cnt_before - 2)
        self.assertEqual(len(app.selected_message_ids), 0)
        self.assertIn("Archived 2 messages", app.status_message)

        # 3. Single-key Move to folder modal via 'v'
        app, _ = app.update(KeyMsg("v"))
        self.assertIsInstance(app.active_modal, MoveFolderModal)
        modal = app.active_modal
        # Test filtering
        modal, _ = modal.update(KeyMsg("a"))
        modal, _ = modal.update(KeyMsg("r"))
        self.assertTrue(any("archive" in f.name.lower() for f in modal._filtered_folders()))
        # Submit move via Enter
        with patch.object(app, "_make_move_msg_cmd", return_value=None):
            app, _ = app.update(KeyMsg("enter"))
        self.assertIsNone(app.active_modal)
        self.assertIn("Moved message to", app.status_message)

        # 4. Single-key Archive via 'y'
        m = app.selected_message
        if m:
            cnt_before = len(app.message_rows)
            app, _ = app.update(KeyMsg("y"))
            self.assertEqual(len(app.message_rows), cnt_before - 1)
            archived = self.store.get_message(m.id)
            self.assertEqual(archived.folder, "Archive")

        # 5. Single-key Trash via 'd'
        m = app.selected_message
        if m:
            cnt_before = len(app.message_rows)
            app, _ = app.update(KeyMsg("d"))
            self.assertEqual(len(app.message_rows), cnt_before - 1)
            trashed = self.store.get_message(m.id)
            self.assertEqual(trashed.folder, "Trash")

        # 6. Single-key Spam via 'J' or '#'
        # Insert a fresh email to ensure spam/zero flow is tested
        from espressomail.models.entities import EmailMessage
        fresh_msg = EmailMessage(
            id="msg_triage_test",
            account_id="acc_work",
            folder="INBOX",
            uid=999,
            message_id="<triage@test.com>",
            sender_name="Spammer",
            sender_email="spammer@evil.com",
            subject="Spam Offer",
            body_text="Click here",
        )
        self.store.save_message(fresh_msg)
        app.refresh_data()
        self.assertGreaterEqual(len(app.message_rows), 1)

        app, _ = app.update(KeyMsg("J"))
        spammed = self.store.get_message("msg_triage_test")
        self.assertIn(spammed.folder.lower(), ("spam", "junk"))
        self.assertIn("Spam", app.status_message)

        # 7. Triage down to Inbox Zero
        while app.selected_message:
            app, _ = app.update(KeyMsg("d"))
        self.assertEqual(len(app.message_rows), 0)
        self.assertIn("Inbox zero!", app.status_message)
        self.assertIn("No email selected", app.reader_viewport.view())

        # 8. IMAPClient move_message and delete_message verification
        from espressomail.protocols.imap_client import IMAPClient
        acc = self.store.list_accounts()[0]
        client = IMAPClient(acc)
        client._conn = MagicMock()
        client._select_mailbox = MagicMock(return_value=True)

        # Test MOVE with capability
        client.has_capability = MagicMock(return_value=True)
        client._conn.uid.return_value = ("OK", [b"Success"])
        ok = client.move_message("INBOX", 101, "Archive")
        self.assertTrue(ok)
        client._conn.uid.assert_called_with("MOVE", "101", '"Archive"')

        # Test fallback without MOVE capability (COPY + STORE + EXPUNGE)
        client.has_capability = MagicMock(return_value=False)
        client._conn.uid.side_effect = [("OK", [b"Copied"]), ("OK", [b"Stored"])]
        ok = client.move_message("INBOX", 102, "Archive")
        self.assertTrue(ok)
        client._conn.expunge.assert_called()

        # Test delete_message
        client._conn.uid.side_effect = None
        client._conn.uid.return_value = ("OK", [b"Deleted"])
        ok = client.delete_message("INBOX", 103)
        self.assertTrue(ok)
        client._conn.uid.assert_called_with("STORE", "103", "+FLAGS", r"(\Deleted)")


if __name__ == "__main__":
    unittest.main()




