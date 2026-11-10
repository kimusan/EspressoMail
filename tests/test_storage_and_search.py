"""Unit tests for SQLite LocalStore, Unified Inbox, attachments, contacts, and FuzzySearchEngine."""

from __future__ import annotations

import tempfile
import unittest

from espressomail.models import (
    AccountConfig,
    Attachment,
    Contact,
    EmailMessage,
    PGPStatus,
    SecurityType,
)
from espressomail.search import FuzzySearchEngine, fuzzy_score, parse_search_query
from espressomail.storage import LocalStore, create_sample_ppm_image


def populate_test_store(store: LocalStore) -> None:
    """Populate an in-memory LocalStore with test accounts, messages, attachments, and contacts."""
    acc1 = AccountConfig(
        id="acc_work",
        name="Schulz Engineering",
        email="kim@schulz.dk",
        full_name="Kim Schulz",
        imap_host="imap.schulz.dk",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        imap_username="kim@schulz.dk",
        imap_password="initial-password-1",
        smtp_host="smtp.schulz.dk",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
        smtp_username="kim@schulz.dk",
        smtp_password="initial-password-1",
    )
    acc2 = AccountConfig(
        id="acc_oss",
        name="Espresso Open Source",
        email="maintainers@espressotui.dev",
        full_name="Kim Schulz",
        imap_host="imap.fastmail.com",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        imap_username="maintainers@espressotui.dev",
        imap_password="initial-password-2",
        smtp_host="smtp.fastmail.com",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
        smtp_username="maintainers@espressotui.dev",
        smtp_password="initial-password-2",
    )
    store.save_account(acc1)
    store.save_account(acc2)

    ppm_bytes = create_sample_ppm_image(24, 14)

    msgs = [
        EmailMessage(
            id="msg_thread1_1",
            account_id="acc_oss",
            folder="INBOX",
            uid=101,
            message_id="<release-1.2-plan@espressotui.dev>",
            thread_id="thread_espresso_12",
            subject="[EspressoTUI] Release v1.2.0 checklist & terminal graphics benchmark",
            sender_name="Elena Rostova",
            sender_email="elena@terminalcraft.io",
            recipients_to=["maintainers@espressotui.dev"],
            date_iso="2026-10-27T09:15:00+00:00",
            is_read=True,
            is_starred=True,
            pgp_status=PGPStatus.SIGNED_VERIFIED,
            body_text="Sixel and Kitty rasterization benchmarks completed.",
            attachments=[
                Attachment(
                    id="att_mascot_ppm",
                    message_id="msg_thread1_1",
                    filename="espresso_mascot.ppm",
                    content_type="image/x-portable-pixmap",
                    size_bytes=len(ppm_bytes),
                    is_inline=True,
                    data=ppm_bytes,
                ),
                Attachment(
                    id="att_bench_json",
                    message_id="msg_thread1_1",
                    filename="graphics_benchmark_v1.2.json",
                    content_type="application/json",
                    size_bytes=64,
                    data=b'{"sixel_ms": 2.8, "kitty_ms": 1.4}\n',
                ),
            ],
        ),
        EmailMessage(
            id="msg_thread1_3",
            account_id="acc_oss",
            folder="INBOX",
            uid=103,
            message_id="<release-1.2-reply2@espressotui.dev>",
            in_reply_to="<release-1.2-plan@espressotui.dev>",
            references=["<release-1.2-plan@espressotui.dev>"],
            thread_id="thread_espresso_12",
            subject="Re: [EspressoTUI] Release v1.2.0 checklist & terminal graphics benchmark",
            sender_name="Elena Rostova",
            sender_email="elena@terminalcraft.io",
            recipients_to=["maintainers@espressotui.dev"],
            date_iso="2026-10-27T11:30:00+00:00",
            is_read=False,
            is_starred=True,
            body_text="Verified attachment download and SQLite persistence.",
        ),
        EmailMessage(
            id="msg_work_pgp",
            account_id="acc_work",
            folder="INBOX",
            uid=201,
            message_id="<sec-audit-2026@schulz.dk>",
            thread_id="thread_sec_audit",
            subject="[PGP-ENCRYPTED] Production WireGuard & Nftables Mesh Key Rotation",
            sender_name="Marcus Vance",
            sender_email="marcus@cyberguard.dk",
            recipients_to=["kim@schulz.dk"],
            date_iso="2026-10-27T12:45:00+00:00",
            is_read=False,
            is_starred=True,
            pgp_status=PGPStatus.DECRYPTED_VERIFIED,
            body_text="All edge nodes rotated WireGuard preshared keys.",
        ),
    ]
    for m in msgs:
        store.save_message(m, harvest_contacts=True)


class TestStorageAndSearch(unittest.TestCase):
    def setUp(self) -> None:
        self.store = LocalStore(":memory:")
        populate_test_store(self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_accounts_and_unified_counts(self) -> None:
        accounts = self.store.list_accounts()
        self.assertEqual(len(accounts), 2)
        counts = self.store.get_unified_counts()
        unread_inbox, total_inbox = counts["INBOX"]
        self.assertEqual(total_inbox, 3)
        self.assertEqual(unread_inbox, 2)

    def test_message_flags_and_trash_lifecycle(self) -> None:
        msg = self.store.get_message("msg_thread1_3")
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertFalse(msg.is_read)

        self.store.mark_read(msg.id, True)
        updated = self.store.get_message(msg.id)
        assert updated is not None
        self.assertTrue(updated.is_read)

        # Toggle star
        initial_star = updated.is_starred
        new_star = self.store.toggle_star(msg.id)
        self.assertEqual(new_star, not initial_star)

        # Move to Trash -> Restore -> Move to Trash -> Empty Trash
        self.store.move_to_trash(msg.id)
        trashed = self.store.get_message(msg.id)
        assert trashed is not None
        self.assertTrue(trashed.is_trashed)
        self.assertEqual(trashed.folder, "Trash")

        self.store.restore_from_trash(msg.id, "INBOX")
        restored = self.store.get_message(msg.id)
        assert restored is not None
        self.assertFalse(restored.is_trashed)
        self.assertEqual(restored.folder, "INBOX")

        self.store.move_to_trash(msg.id)
        removed_count = self.store.empty_trash("*")
        self.assertGreaterEqual(removed_count, 1)
        self.assertIsNone(self.store.get_message(msg.id))

    def test_save_attachment_to_disk(self) -> None:
        msg = self.store.get_message("msg_thread1_1")
        assert msg is not None
        self.assertEqual(len(msg.attachments), 2)
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = self.store.save_attachment_to_disk(msg.attachments[0].id, tmpdir)
            self.assertTrue(out_path.exists())
            self.assertGreater(out_path.stat().st_size, 100)

    def test_contacts_address_book(self) -> None:
        contacts = self.store.list_contacts()
        self.assertGreaterEqual(len(contacts), 2)
        new_contact = Contact(
            id="contact_test",
            name="Linus Torvalds",
            email="torvalds@linux-foundation.org",
            organization="Linux Foundation",
            favorite=True,
        )
        self.store.save_contact(new_contact)
        found = self.store.list_contacts("torvalds")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].email, "torvalds@linux-foundation.org")
        self.store.delete_contact(found[0].id)
        self.assertEqual(len(self.store.list_contacts("torvalds")), 0)

    def test_fuzzy_search_and_operators(self) -> None:
        parsed = parse_search_query('from:elena has:attachment is:starred "benchmark"')
        self.assertEqual(parsed.from_filter, "elena")
        self.assertTrue(parsed.has_attachment)
        self.assertTrue(parsed.is_starred)
        self.assertIn("benchmark", parsed.terms)

        # Subsequence and typo scoring
        self.assertGreater(fuzzy_score("esptui", "EspressoTUI"), 40.0)
        self.assertGreater(fuzzy_score("wiregard", "WireGuard"), 30.0)

        engine = FuzzySearchEngine(self.store)
        hits = engine.search("from:elena sixel")
        self.assertGreaterEqual(len(hits), 1)
        self.assertIn("Elena", hits[0].message.sender_name)

        pgp_hits = engine.search("is:pgp WireGuard")
        self.assertEqual(len(pgp_hits), 1)
        self.assertEqual(pgp_hits[0].message.id, "msg_work_pgp")

    def test_unified_all_unread_and_folder_subscription(self) -> None:
        """Verify All Unread unified folder queries and folder subscription storage."""
        counts = self.store.get_unified_counts()
        self.assertIn("All Unread", counts)
        unread_cnt, _ = counts["All Unread"]
        self.assertGreaterEqual(unread_cnt, 1)

        unread_msgs = self.store.list_messages(account_id="*", folder="All Unread")
        self.assertEqual(len(unread_msgs), unread_cnt)
        for m in unread_msgs:
            self.assertFalse(m.is_read)
            self.assertFalse(m.is_trashed)

        # Folder subscription toggle & persistence
        folders = self.store.list_folders()
        self.assertTrue(len(folders) > 0)
        test_folder = folders[0]
        self.assertTrue(test_folder.subscribed)

        new_state = self.store.toggle_folder_subscription(test_folder.account_id, test_folder.name)
        self.assertFalse(new_state)
        f_updated = self.store.get_folder(test_folder.account_id, test_folder.name)
        self.assertIsNotNone(f_updated)
        self.assertFalse(f_updated.subscribed)

        self.store.set_folder_subscribed(test_folder.account_id, test_folder.name, True)
        self.assertTrue(self.store.get_folder(test_folder.account_id, test_folder.name).subscribed)

        # Contact by email lookup
        contact = self.store.get_contact_by_email("elena@terminalcraft.io")
        self.assertIsNotNone(contact)
        self.assertEqual(contact.name, "Elena Rostova")

    def test_sqlite_fts5_full_text_search_and_rebuild(self) -> None:
        """Verify SQLite FTS5 index creation, rebuild, BM25 ranking, snippet generation, and stemming."""
        from espressomail.search import build_fts5_query, parse_search_query

        self.assertTrue(self.store.is_fts_enabled())

        # Rebuild index and verify count
        indexed_count = self.store.rebuild_fts_index()
        self.assertEqual(indexed_count, 3)

        # 1. Porter stemming match: 'benchmarks' matches 'benchmark'
        stemmed_hits = self.store.search_messages_fts("benchmarks")
        self.assertGreaterEqual(len(stemmed_hits), 1)
        hit_msg, hit_score, snippet = stemmed_hits[0]
        self.assertIn("benchmark", hit_msg.subject.lower())
        self.assertIn("«", snippet)
        self.assertIn("»", snippet)
        self.assertGreaterEqual(hit_score, 50.0)

        # 2. Body text search: 'rasterization' only appears in msg_thread1_1 body
        body_hits = self.store.search_messages_fts("rasterization")
        self.assertEqual(len(body_hits), 1)
        self.assertEqual(body_hits[0][0].id, "msg_thread1_1")
        self.assertIn("rasterization", body_hits[0][2].lower())

        # 3. Attachment filename indexing: 'mascot' matches espresso_mascot.ppm
        att_hits = self.store.search_messages_fts("mascot")
        self.assertEqual(len(att_hits), 1)
        self.assertEqual(att_hits[0][0].id, "msg_thread1_1")

        # 4. Sender search: 'Vance' matches Marcus Vance
        sender_hits = self.store.search_messages_fts("Vance")
        self.assertEqual(len(sender_hits), 1)
        self.assertEqual(sender_hits[0][0].id, "msg_work_pgp")

        # 5. Column filter query building
        sq = parse_search_query("body:rasterization has:attachment")
        self.assertEqual(sq.body_filter, "rasterization")
        self.assertTrue(sq.has_attachment)
        built = build_fts5_query(sq)
        self.assertIn("body_text :", built)

        # 6. Integrated FuzzySearchEngine using FTS5 with BM25 and fallback
        engine = FuzzySearchEngine(self.store)

        # Fast FTS5 hit with snippet
        fts_res = engine.search("rasterization")
        self.assertGreaterEqual(len(fts_res), 1)
        self.assertIn("fts", fts_res[0].matched_fields)
        self.assertTrue(bool(fts_res[0].snippet))

        # Typo tolerance fallback: 'wiregard' (misspelled) falls back to fuzzy match 'WireGuard'
        typo_res = engine.search("wiregard")
        self.assertGreaterEqual(len(typo_res), 1)
        self.assertEqual(typo_res[0].message.id, "msg_work_pgp")

        # Prefix search: 'extra*' or 'wireg*'
        prefix_res = engine.search("wireg*")
        self.assertGreaterEqual(len(prefix_res), 1)
        self.assertEqual(prefix_res[0].message.id, "msg_work_pgp")

    def test_fts5_search_modal_view_and_snippet_display(self) -> None:
        """Verify SearchModal renders FTS5 title, operator hints, and contextual snippet preview."""
        from espressomail.themes.engine import THEMES
        from espressomail.ui.modals import SearchModal
        from espresso import KeyMsg

        theme = THEMES["espresso"]
        modal = SearchModal(self.store, theme, initial_query="rasterization")

        # View rendering includes FTS5 modal header
        view_out = modal.view(80)
        self.assertIn("FTS5", view_out)
        self.assertIn("body:", view_out)
        self.assertIn("att:", view_out)

        # The selected row displays contextual matched snippet preview
        self.assertIn("↳", view_out)
        self.assertIn("rasterization", view_out.lower())

    def test_search_modal_fixed_size_and_scrollable_list(self) -> None:
        """Verify SearchModal maintains strictly fixed dimensions and supports smooth scrolling."""
        from espresso import KeyMsg, MouseAction, MouseButton, MouseMsg
        from espressomail.models.entities import EmailMessage
        from espressomail.themes.engine import THEMES
        from espressomail.ui.modals import SearchModal, SearchSelectMessageMsg

        theme = THEMES["espresso"]

        # Insert 30 test messages to ensure plenty of searchable items
        for i in range(30):
            msg = EmailMessage(
                id=f"msg_scroll_test_{i:02d}",
                account_id="acc_work",
                folder="INBOX",
                sender_email="team@example.com",
                sender_name=f"Colleague {i}",
                subject=f"Project sprint update item {i:02d}",
                body_text=f"Detailed status report for workstream number {i:02d} with lots of text.",
                date_iso=f"2026-11-{(i % 28) + 1:02d}T10:00:00Z",
                snippet=f"Status report for workstream {i:02d}",
            )
            self.store.save_message(msg)

        modal = SearchModal(self.store, theme, initial_query="sprint update")
        self.assertGreaterEqual(len(modal.results), 20)

        # 1. Verify fixed height across different states (80x24 terminal -> modal_h = 20)
        view_default = modal.view(80, 24)
        lines_default = view_default.split("\n")
        self.assertEqual(len(lines_default), 20, "Modal height must be exactly 20 lines")

        # Outer dimensions must remain fixed even with 0 results
        modal.query_input.value = "nonexistentquerystringxyz123"
        modal._refresh_results(reset_cursor=True)
        self.assertEqual(len(modal.results), 0)
        view_empty = modal.view(80, 24)
        self.assertEqual(len(view_empty.split("\n")), 20, "Modal height must be exactly 20 lines for 0 results")
        self.assertIn("No matching emails found", view_empty)

        # Date picker open must also maintain fixed height
        modal, _ = modal.update(KeyMsg("ctrl+d"))
        self.assertTrue(modal.show_date_picker)
        view_dp = modal.view(80, 24)
        self.assertEqual(len(view_dp.split("\n")), 20, "Modal height must remain exactly 20 lines with DatePicker open")

        # Close date picker
        modal, _ = modal.update(KeyMsg("esc"))
        self.assertFalse(modal.show_date_picker)

        # 2. Reset query to have 30 matches
        modal.query_input.value = "sprint update"
        modal._refresh_results(reset_cursor=True)
        self.assertEqual(modal.cursor, 0)
        self.assertEqual(modal.scroll_offset, 0)
        self.assertGreater(len(modal.results), modal.page_size)

        # View rendering must display scrollbar thumb and track
        view_matches = modal.view(80, 24)
        self.assertEqual(len(view_matches.split("\n")), 20)
        self.assertIn("█", view_matches, "Scrollbar thumb must be rendered")
        self.assertIn("│", view_matches, "Scrollbar track must be rendered")
        self.assertIn("Showing 1–", view_matches, "Footer must show scroll indicator")

        # 3. Keyboard navigation & scrolling
        # Press Down until scrolling triggers
        initial_scroll = modal.scroll_offset
        for _ in range(modal.page_size + 2):
            modal, _ = modal.update(KeyMsg("down"))
        self.assertGreater(modal.scroll_offset, initial_scroll, "Scroll offset must advance when cursor moves past page_size")
        self.assertGreater(modal.cursor, 0)

        # Press Page Down
        cur_before_pgdn = modal.cursor
        modal, _ = modal.update(KeyMsg("pagedown"))
        self.assertGreater(modal.cursor, cur_before_pgdn)

        # Press Ctrl+End to go to last item
        modal, _ = modal.update(KeyMsg("ctrl+end"))
        self.assertEqual(modal.cursor, len(modal.results) - 1)
        self.assertEqual(modal.scroll_offset, len(modal.results) - modal.page_size)

        # Press Ctrl+Home to return to top
        modal, _ = modal.update(KeyMsg("ctrl+home"))
        self.assertEqual(modal.cursor, 0)
        self.assertEqual(modal.scroll_offset, 0)

        # 4. Mouse wheel scrolling
        modal, _ = modal.update(MouseMsg(0, 0, button=MouseButton.WHEEL_DOWN, action=MouseAction.PRESS))
        self.assertEqual(modal.cursor, 1)

        modal, _ = modal.update(MouseMsg(0, 0, button=MouseButton.WHEEL_UP, action=MouseAction.PRESS))
        self.assertEqual(modal.cursor, 0)

        # 5. Mouse click selection & opening
        modal.view(80, 24)
        self.assertIn(modal.cursor, modal.result_line_to_index.values())

        # Find line of a non-cursor item in result_line_to_index
        target_line = None
        target_idx = None
        for line_idx, res_idx in modal.result_line_to_index.items():
            if res_idx != modal.cursor:
                target_line = line_idx
                target_idx = res_idx
                break
        self.assertIsNotNone(target_line)

        # Absolute screen Y = pos_y + top_border(1) + top_padding(1) + line_idx = modal.last_pos_y + 2 + target_line
        click_y = modal.last_pos_y + 2 + target_line
        click_x = modal.last_pos_x + 5

        # Single click selects item
        modal, cmd = modal.update(MouseMsg(click_x, click_y, button=MouseButton.LEFT, action=MouseAction.PRESS))
        self.assertIsNone(cmd)
        self.assertEqual(modal.cursor, target_idx)

        # Double click opens item
        modal, cmd = modal.update(MouseMsg(click_x, click_y, button=MouseButton.LEFT, action=MouseAction.DOUBLE_CLICK))
        self.assertIsNotNone(cmd)
        selected_msg = cmd()
        self.assertIsInstance(selected_msg, SearchSelectMessageMsg)
        self.assertEqual(selected_msg.message.id, modal.results[target_idx].message.id)

        # 6. Screen height adaptability
        view_30 = modal.view(80, 30)
        self.assertEqual(len(view_30.split("\n")), 24, "Modal height in 30-line terminal must be 24 lines")


if __name__ == "__main__":
    unittest.main()
