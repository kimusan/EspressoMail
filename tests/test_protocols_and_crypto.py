"""Unit tests for MIME parsing/building, JWZ threading, Sieve scripts, and GPG/PGP cryptography."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from espressomail.crypto import PGPManager
from espressomail.models import AccountConfig, EmailMessage, PGPStatus, SieveFilterRule
from espressomail.protocols import (
    apply_sieve_rules_locally,
    build_mime_message,
    build_threads,
    compile_sieve_script,
    normalize_subject,
    parse_rfc5322_bytes,
    validate_sieve_script,
)
from espressomail.protocols.imap_client import IMAPClient
from espressomail.protocols.sieve import ManageSieveClient
from espressomail.storage import LocalStore


class TestProtocolsAndCrypto(unittest.TestCase):
    def setUp(self) -> None:
        self.account = AccountConfig(
            id="acc_test",
            name="Test Account",
            email="kim@schulz.dk",
            full_name="Kim Schulz",
            imap_host="imap.schulz.dk",
            smtp_host="smtp.schulz.dk",
        )

    def test_mime_roundtrip_with_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            att_file = Path(tmpdir) / "notes.txt"
            att_file.write_text("Espresso TUI architecture notes\n", encoding="utf-8")

            mime_msg, domain_msg = build_mime_message(
                account=self.account,
                recipients_to=["Alice <alice@example.com>"],
                recipients_cc=["bob@example.com"],
                subject="Re: [RFC] EspressoMail v0.1",
                body_text="Hello Alice,\n\nPlease see the attached file.",
                in_reply_to="<root-msg@example.com>",
                references=["<root-msg@example.com>"],
                attachment_paths=[att_file],
            )

            raw_bytes = mime_msg.as_bytes()
            parsed = parse_rfc5322_bytes(
                raw_bytes=raw_bytes,
                account_id=self.account.id,
                folder="INBOX",
                uid=55,
                imap_flags=["\\Seen", "\\Flagged"],
            )

            self.assertEqual(parsed.subject, "Re: [RFC] EspressoMail v0.1")
            self.assertEqual(parsed.sender_email, "kim@schulz.dk")
            self.assertEqual(parsed.in_reply_to, "<root-msg@example.com>")
            self.assertTrue(parsed.is_read)
            self.assertTrue(parsed.is_starred)
            self.assertEqual(len(parsed.attachments), 1)
            self.assertEqual(parsed.attachments[0].filename, "notes.txt")
            self.assertIn(b"Espresso TUI", parsed.attachments[0].data or b"")

    def test_jwz_conversation_threading(self) -> None:
        self.assertEqual(normalize_subject("Re: Fwd: [Dev] Hello World"), "[Dev] Hello World")

        m1 = EmailMessage(
            id="m1",
            account_id="acc1",
            message_id="<1@test>",
            subject="Project Kickoff",
            sender_name="Alice",
            timestamp=1000.0,
        )
        m2 = EmailMessage(
            id="m2",
            account_id="acc1",
            message_id="<2@test>",
            in_reply_to="<1@test>",
            references=["<1@test>"],
            subject="Re: Project Kickoff",
            sender_name="Bob",
            timestamp=2000.0,
        )
        m3 = EmailMessage(
            id="m3",
            account_id="acc1",
            message_id="<3@test>",
            in_reply_to="<2@test>",
            references=["<1@test>", "<2@test>"],
            subject="Re: Project Kickoff",
            sender_name="Charlie",
            timestamp=3000.0,
        )
        m_other = EmailMessage(
            id="m4",
            account_id="acc1",
            message_id="<4@test>",
            subject="Separate Topic",
            sender_name="Dave",
            timestamp=1500.0,
        )

        threads = build_threads([m1, m2, m3, m_other])
        self.assertEqual(len(threads), 2)
        top_thread = threads[0]
        self.assertEqual(top_thread.message_count, 3)
        self.assertEqual(top_thread.participants, ["Alice", "Bob", "Charlie"])
        self.assertEqual([n.depth for n in top_thread.nodes], [0, 1, 2])

    def test_sieve_compilation_validation_and_execution(self) -> None:
        rules = [
            SieveFilterRule(
                id="r1",
                account_id="acc_test",
                name="Filter CI Alerts",
                enabled=True,
                field="From",
                comparator="contains",
                pattern="ci@github.com",
                action="fileinto",
                action_arg="Archive",
                stop_processing=True,
            ),
            SieveFilterRule(
                id="r2",
                account_id="acc_test",
                name="Flag Urgent",
                enabled=True,
                field="Subject",
                comparator="contains",
                pattern="[URGENT]",
                action="flag",
                action_arg="\\Flagged",
                stop_processing=False,
            ),
        ]
        script = compile_sieve_script(rules)
        self.assertIn('require ["fileinto", "imap4flags"];', script)
        valid, errors = validate_sieve_script(script)
        self.assertTrue(valid, errors)

        bad_valid, bad_errors = validate_sieve_script('if header :contains "Subject" "test" { fileinto "Archive"; ')
        self.assertFalse(bad_valid)
        self.assertGreaterEqual(len(bad_errors), 1)

        msg = EmailMessage(
            id="m_ci",
            account_id="acc_test",
            sender_email="ci@github.com",
            subject="Build passed",
        )
        matched = apply_sieve_rules_locally(rules, [msg])
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][1].id, "r1")

    def test_pgp_sign_verify_encrypt_decrypt(self) -> None:
        pgp = PGPManager()
        new_key = pgp.generate_key("Kim Schulz", "kim@schulz.dk")
        self.assertTrue(new_key.key_id.startswith("0x"))

        # Cleartext sign & verify
        signed = pgp.clearsign_message("Release v0.1.0 checksum: deadbeef", signer_email="kim@schulz.dk")
        self.assertTrue(signed.ok)
        self.assertIn("-----BEGIN PGP SIGNED MESSAGE-----", signed.payload)

        verified = pgp.verify_message(signed.payload)
        self.assertTrue(verified.ok)
        self.assertEqual(verified.status, PGPStatus.SIGNED_VERIFIED)
        self.assertEqual(verified.payload, "Release v0.1.0 checksum: deadbeef")

        # Tamper detection
        tampered = signed.payload.replace("deadbeef", "cafebabe")
        bad_verify = pgp.verify_message(tampered)
        self.assertFalse(bad_verify.ok)
        self.assertEqual(bad_verify.status, PGPStatus.SIGNED_INVALID)

        # Encrypt & Decrypt
        encrypted = pgp.encrypt_message(
            "Confidential server credentials",
            recipients=["elena@terminalcraft.io"],
            sign=True,
            signer_email="kim@schulz.dk",
        )
        self.assertTrue(encrypted.ok)
        self.assertIn("-----BEGIN PGP MESSAGE-----", encrypted.payload)

        decrypted = pgp.decrypt_message(encrypted.payload)
        self.assertTrue(decrypted.ok)
        self.assertEqual(decrypted.status, PGPStatus.DECRYPTED_VERIFIED)
        self.assertEqual(decrypted.payload, "Confidential server credentials")

    def test_imap_list_parsing_and_sync_to_store(self) -> None:
        from espressomail.models.entities import FolderType
        from espressomail.protocols.imap_client import (
            IMAPClient,
            decode_imap_utf7,
            parse_imap_list_response,
        )
        from espressomail.storage import LocalStore

        # 1. Modified UTF-7 decoding
        self.assertEqual(decode_imap_utf7("Entw&APw-rfe"), "Entwürfe")
        self.assertEqual(decode_imap_utf7("Coffee & Tea"), "Coffee & Tea")

        # 2. Various IMAP LIST response formats (NIL delimiter, INBOX. prefix, spaces)
        self.assertEqual(
            parse_imap_list_response(b'(\\HasNoChildren) NIL INBOX'),
            ("\\HasNoChildren", "/", "INBOX"),
        )
        self.assertEqual(
            parse_imap_list_response(b'(\\HasNoChildren \\Sent) "." "INBOX.Sent Items"'),
            ("\\HasNoChildren \\Sent", ".", "INBOX.Sent Items"),
        )
        self.assertEqual(
            parse_imap_list_response(b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"'),
            ("\\HasNoChildren \\Trash", "/", "[Gmail]/Trash"),
        )

        # 3. Full IMAPClient.sync_to_store against a simulated IMAP4 connection
        mime_msg, _ = build_mime_message(
            account=self.account,
            recipients_to=["kim@schulz.dk"],
            subject="Live Synced Welcome Message",
            body_text="Synced from IMAP server!",
        )
        raw_rfc822 = mime_msg.as_bytes()

        class FakeIMAPConn:
            def list(self) -> tuple[str, list[bytes]]:
                return (
                    "OK",
                    [
                        b'(\\HasNoChildren) "." INBOX',
                        b'(\\HasNoChildren \\Sent) "." "INBOX.Sent"',
                        b'(\\HasNoChildren) "." "INBOX.Projects"',
                    ],
                )

            def select(self, mailbox: str, readonly: bool = True) -> tuple[str, list[bytes]]:
                return ("OK", [b"1"])

            def uid(self, command: str, *args: str) -> tuple[str, list[object]]:
                if command == "SEARCH":
                    return ("OK", [b"101"])
                if command == "FETCH":
                    uid_arg = args[0]
                    if "101" in uid_arg:
                        return (
                            "OK",
                            [
                                (
                                    b"1 (UID 101 FLAGS (\\Seen) BODY[] {"
                                    + str(len(raw_rfc822)).encode()
                                    + b"}",
                                    raw_rfc822,
                                ),
                                b")",
                            ],
                        )
                return ("OK", [])

        store = LocalStore(":memory:")
        store.save_account(self.account)
        client = IMAPClient(self.account)
        client._conn = FakeIMAPConn()  # type: ignore[assignment]

        synced_cnt = client.sync_to_store(store)
        self.assertGreaterEqual(synced_cnt, 1)

        folders = store.list_folders(self.account.id)
        folder_names = [f.name for f in folders]
        self.assertIn("INBOX", folder_names)
        self.assertIn("INBOX.Sent", folder_names)
        self.assertIn("INBOX.Projects", folder_names)

        inbox_msgs = store.list_messages(account_id=self.account.id, folder="INBOX")
        self.assertEqual(len(inbox_msgs), 1)
        self.assertEqual(inbox_msgs[0].subject, "Live Synced Welcome Message")
        self.assertTrue(inbox_msgs[0].is_read)

        # Unified Sent should include messages in INBOX.Sent because its folder_type is SENT
        sent_msgs = store.list_messages(account_id="*", folder="Sent")
        self.assertEqual(len(sent_msgs), 1)
        store.close()

    def test_imap_fetch_recent_messages_with_before_uid(self) -> None:
        """Verify fetch_recent_messages issues SEARCH UID 1:{before_uid - 1} when before_uid is given."""
        searched_criteria: list[str] = []

        raw_rfc822 = (
            b"From: Older Message <older@example.com>\r\n"
            b"To: Kim Schulz <kim@schulz.dk>\r\n"
            b"Subject: Older Email 99\r\n"
            b"Date: Mon, 12 Jan 2026 10:00:00 +0000\r\n"
            b"Message-ID: <older-99@example.com>\r\n"
            b"\r\n"
            b"Body of older email 99\r\n"
        )

        class MockIMAP:
            def select(self, mailbox: str, readonly: bool = True) -> tuple[str, list[bytes]]:
                return ("OK", [b"10"])

            def uid(self, command: str, *args: str) -> tuple[str, list[object]]:
                if command == "SEARCH":
                    criteria = args[-1]
                    searched_criteria.append(criteria)
                    # Return UIDs 90 through 99
                    return ("OK", [b"90 91 92 98 99"])
                if command == "FETCH":
                    return (
                        "OK",
                        [
                            (
                                b"1 (UID 99 FLAGS () BODY[] {" + str(len(raw_rfc822)).encode() + b"}",
                                raw_rfc822,
                            ),
                            b")",
                        ],
                    )
                return ("OK", [])

        client = IMAPClient(self.account)
        client._conn = MockIMAP()  # type: ignore[assignment]

        # Fetch with before_uid=100
        msgs = client.fetch_recent_messages(folder="INBOX", limit=2, before_uid=100)
        self.assertEqual(len(msgs), 1)
        self.assertIn("UID 1:99", searched_criteria)
        self.assertEqual(msgs[-1].subject, "Older Email 99")

    def test_manage_sieve_client_protocol_and_sync(self) -> None:
        """Verify ManageSieve client list_scripts, get_script, put_script, and LocalStore sync."""
        commands_sent: list[bytes] = []
        canned_responses: list[bytes] = [
            b'"default" ACTIVE\r\n"archive_rules"\r\nOK "LISTSCRIPTS done"\r\n',
            b"{45}\r\nrequire [\"fileinto\"];\r\nfileinto \"Trash\";\r\nOK\r\n",
            b"OK \"PUTSCRIPT done\"\r\n",
            b"OK \"SETSCRIPT done\"\r\n",
        ]

        class MockSocket:
            def sendall(self, data: bytes) -> None:
                commands_sent.append(data)

            def recv(self, bufsize: int = 4096) -> bytes:
                if canned_responses:
                    return canned_responses.pop(0)
                return b""

            def close(self) -> None:
                pass

        account = AccountConfig(
            id="acc_sieve",
            name="Sieve Account",
            email="user@example.com",
            full_name="User Sieve",
            sieve_host="sieve.example.com",
            sieve_port=4190,
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
        )
        client = ManageSieveClient(account)
        mock_sock = MockSocket()
        client._sock = mock_sock  # type: ignore[assignment]

        # 1. Test list_scripts
        scripts = client.list_scripts()
        self.assertEqual(len(scripts), 2)
        self.assertEqual(scripts[0], ("default", True))
        self.assertEqual(scripts[1], ("archive_rules", False))
        self.assertIn(b"LISTSCRIPTS\r\n", commands_sent)

        # 2. Test get_script
        content = client.get_script("default")
        self.assertIn('require ["fileinto"];', content)
        self.assertIn('fileinto "Trash";', content)

        # 3. Test sync_script_with_server: fetches remote active script into LocalStore
        store = LocalStore(":memory:")
        try:
            # Re-arm socket responses for a full sync run:
            # 1: list_scripts -> "default" active
            # 2: get_script("default")
            # 3: put_script (when local rules exist)
            # 4: setscript
            canned_responses.clear()
            canned_responses.extend([
                b'"default" ACTIVE\r\nOK "LISTSCRIPTS"\r\n',
                b"{45}\r\nrequire [\"fileinto\"];\r\nfileinto \"Trash\";\r\nOK\r\n",
                b"OK \"PUTSCRIPT done\"\r\n",
                b"OK \"SETSCRIPT done\"\r\n",
            ])

            # Save account to satisfy foreign key constraint
            store.save_account(account)

            # Add a local rule so put_script will also be exercised during sync
            local_rule = SieveFilterRule(
                id="rule_local_1",
                account_id=account.id,
                name="Move Notifications",
                field="Subject",
                comparator="contains",
                pattern="Alert",
                action="fileinto",
                action_arg="Alerts",
            )
            store.save_sieve_rule(local_rule)

            ok, msg = client.sync_script_with_server(store)
            self.assertTrue(ok)
            self.assertIn("updated 'default'", msg)

            # Verify saved scripts in store
            saved_scripts = store.list_sieve_scripts(account.id)
            self.assertTrue(any(s.name == "default" for s in saved_scripts))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()

