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


if __name__ == "__main__":
    unittest.main()
