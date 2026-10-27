"""Unit tests for SQLite LocalStore, Unified Inbox, attachments, contacts, and FuzzySearchEngine."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from espressomail.models import AccountConfig, Attachment, Contact, EmailMessage, PGPStatus
from espressomail.search import FuzzySearchEngine, fuzzy_score, parse_search_query
from espressomail.storage import LocalStore


class TestStorageAndSearch(unittest.TestCase):
    def setUp(self) -> None:
        self.store = LocalStore(":memory:")
        self.store.seed_demo_data()

    def tearDown(self) -> None:
        self.store.close()

    def test_seeded_accounts_and_unified_counts(self) -> None:
        accounts = self.store.list_accounts()
        self.assertEqual(len(accounts), 2)
        counts = self.store.get_unified_counts()
        unread_inbox, total_inbox = counts["INBOX"]
        self.assertGreaterEqual(total_inbox, 4)
        self.assertGreaterEqual(unread_inbox, 1)

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
        self.assertGreaterEqual(len(contacts), 3)
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


if __name__ == "__main__":
    unittest.main()
