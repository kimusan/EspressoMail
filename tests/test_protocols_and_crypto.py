"""Unit tests for MIME parsing/building, JWZ threading, Sieve scripts, and GPG/PGP cryptography."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_carddav_vcard_parsing_formatting_and_sync(self) -> None:
        """Verify CardDAV vCard 3.0/4.0 parsing, vCard generation, and address book sync."""
        from espressomail.models.entities import Contact
        from espressomail.protocols.carddav import CardDAVClient, format_vcard, parse_vcards
        from espressomail.storage.database import LocalStore

        raw_vcards = (
            "BEGIN:VCARD\r\n"
            "VERSION:3.0\r\n"
            "FN:Alice Baker\r\n"
            "EMAIL;TYPE=INTERNET;TYPE=WORK:\r\n"
            " alice.baker@example.com\r\n"
            "NOTE:Engineering Lead\r\n"
            "UID:card-1001\r\n"
            "END:VCARD\r\n"
            "BEGIN:VCARD\r\n"
            "VERSION:3.0\r\n"
            "FN:Bob Martin\r\n"
            "EMAIL:bob@corp.org\r\n"
            "UID:card-1002\r\n"
            "END:VCARD\r\n"
        )
        contacts = parse_vcards(raw_vcards)
        self.assertEqual(len(contacts), 2)
        self.assertEqual(contacts[0].name, "Alice Baker")
        self.assertEqual(contacts[0].email, "alice.baker@example.com")
        self.assertEqual(contacts[0].notes, "Engineering Lead")
        self.assertEqual(contacts[1].name, "Bob Martin")
        self.assertEqual(contacts[1].email, "bob@corp.org")

        # Test format_vcard
        single_contact = Contact(
            id="c_test",
            name="Carol Danvers",
            email="carol@starforce.mil",
            notes="Pilot",
        )
        vcard_out = format_vcard(single_contact)
        self.assertIn("BEGIN:VCARD", vcard_out)
        self.assertIn("VERSION:3.0", vcard_out)
        self.assertIn("FN:Carol Danvers", vcard_out)
        self.assertIn("EMAIL;TYPE=INTERNET:carol@starforce.mil", vcard_out)
        self.assertIn("NOTE:Pilot", vcard_out)
        self.assertIn("END:VCARD", vcard_out)

        # Test CardDAVClient sync_contacts with mocked HTTP response
        store = LocalStore(":memory:")
        try:
            account = AccountConfig(
                id="acc_carddav",
                name="Work CardDAV",
                email="user@example.com",
                full_name="Test User",
                imap_host="imap.example.com",
                carddav_url="https://dav.example.com/addressbooks/user/contacts/",
                carddav_username="user",
                carddav_password="secretpassword",
            )
            store.save_account(account)

            multistatus_xml = f"""<?xml version="1.0" encoding="utf-8" ?>
            <d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
                <d:response>
                    <d:href>/addressbooks/user/contacts/1.vcf</d:href>
                    <d:propstat>
                        <d:prop>
                            <card:address-data>{raw_vcards}</card:address-data>
                        </d:prop>
                        <d:status>HTTP/1.1 200 OK</d:status>
                    </d:propstat>
                </d:response>
            </d:multistatus>"""

            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = multistatus_xml.encode("utf-8")
            mock_resp.status = 207
            mock_resp.__enter__.return_value = mock_resp

            client = CardDAVClient(
                url=account.carddav_url,
                username=account.carddav_username,
                password=account.carddav_password,
            )

            with mock.patch("urllib.request.urlopen", return_value=mock_resp):
                down_cnt, up_cnt, summary = client.sync_contacts(store)
                self.assertEqual(down_cnt, 2)
                self.assertIn("imported", summary)

                # Verify contacts were stored in LocalStore
                saved_alice = store.get_contact_by_email("alice.baker@example.com")
                self.assertIsNotNone(saved_alice)
                self.assertEqual(saved_alice.name, "Alice Baker")

                saved_bob = store.get_contact_by_email("bob@corp.org")
                self.assertIsNotNone(saved_bob)
                self.assertEqual(saved_bob.name, "Bob Martin")
        finally:
            store.close()

    def test_imap_client_context_manager(self) -> None:
        """Verify IMAPClient supports the context manager protocol (__enter__ and __exit__)."""
        client = IMAPClient(self.account)
        mock_conn = mock.MagicMock()

        with mock.patch.object(client, "connect", return_value=mock_conn) as mock_connect, \
             mock.patch.object(client, "disconnect") as mock_disconnect:
            with client as ctx:
                self.assertIs(ctx, client)
                mock_connect.assert_called_once()
                mock_disconnect.assert_not_called()
            mock_disconnect.assert_called_once()

    def test_imap_client_subscribe_and_unsubscribe(self) -> None:
        """Verify subscribe_folder and unsubscribe_folder send quoted IMAP commands."""
        client = IMAPClient(self.account)
        mock_conn = mock.MagicMock()
        mock_conn.subscribe.return_value = ("OK", [b"Completed"])
        mock_conn.unsubscribe.return_value = ("OK", [b"Completed"])
        client._conn = mock_conn

        self.assertTrue(client.subscribe_folder("INBOX.Work"))
        mock_conn.subscribe.assert_called_once_with('"INBOX.Work"')

        self.assertTrue(client.unsubscribe_folder("INBOX.Work"))
        mock_conn.unsubscribe.assert_called_once_with('"INBOX.Work"')

    def test_logging_setup(self) -> None:
        """Verify setup_logging configures file handler and logging level."""
        import logging
        import espressomail.logging_config as log_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test_run.log"
            orig_configured = log_mod._LOG_CONFIGURED
            log_mod._LOG_CONFIGURED = False
            root = logging.getLogger()
            old_handlers = list(root.handlers)
            root.handlers.clear()
            try:
                resolved = log_mod.setup_logging(debug=True, log_file=log_path)
                self.assertEqual(resolved, log_path.resolve())

                test_logger = logging.getLogger("espressomail.test")
                test_logger.debug("Debug probe message")
                test_logger.info("Info probe message")
                for h in root.handlers:
                    h.flush()

                content = log_path.read_text(encoding="utf-8")
                self.assertIn("Debug probe message", content)
                self.assertIn("Info probe message", content)
            finally:
                for h in list(root.handlers):
                    h.close()
                root.handlers = old_handlers
                log_mod._LOG_CONFIGURED = orig_configured

    def test_icalendar_parsing_and_rsvp_generation(self) -> None:
        """Verify RFC 5545 iCalendar parsing, line unfolding, and RFC 6047 iMIP RSVP generation."""
        from datetime import timezone
        from espressomail.models.entities import Attachment
        from espressomail.protocols.icalendar import (
            CalendarEvent,
            build_ics_reply,
            build_rsvp_mime_message,
            find_calendar_event,
            parse_ics,
            unfold_ical_text,
        )

        sample_ics = (
            "BEGIN:VCALENDAR\r\n"
            "PRODID:-//Google Inc//Google Calendar 70.9054//EN\r\n"
            "VERSION:2.0\r\n"
            "CALSCALE:GREGORIAN\r\n"
            "METHOD:REQUEST\r\n"
            "BEGIN:VEVENT\r\n"
            "DTSTART:20261029T140000Z\r\n"
            "DTEND:20261029T153000Z\r\n"
            "DTSTAMP:20261028T220000Z\r\n"
            "ORGANIZER;CN=\"Alice Smith\":mailto:alice@example.com\r\n"
            "UID:team-sync-9988@example.com\r\n"
            "ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN=\"Alice\r\n"
            "  Smith\";X-NUM-GUESTS=0:mailto:alice@example.com\r\n"
            "ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=\r\n"
            " TRUE;CN=\"Kim Schulz\";X-NUM-GUESTS=0:mailto:kim@schulz.dk\r\n"
            "SUMMARY:Q4 Engineering Roadmap \\, Planning \\; Sync\r\n"
            "DESCRIPTION:Quarterly engineering sync\\nDiscussing Q4 roadmap\\nJoin Meet: \r\n"
            " https://meet.jit.si/team-sync\r\n"
            "LOCATION:Room 404 / Video Call (https://meet.jit.si/team-sync)\r\n"
            "SEQUENCE:1\r\n"
            "STATUS:CONFIRMED\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        # 1. Test unfolding
        unfolded = unfold_ical_text(sample_ics)
        self.assertTrue(any("Kim Schulz" in line and "RSVP=TRUE" in line for line in unfolded))

        # 2. Test parse_ics
        events = parse_ics(sample_ics)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.uid, "team-sync-9988@example.com")
        self.assertEqual(ev.summary, "Q4 Engineering Roadmap , Planning ; Sync")
        self.assertIn("Quarterly engineering sync\nDiscussing Q4 roadmap", ev.description)
        self.assertEqual(ev.location, "Room 404 / Video Call (https://meet.jit.si/team-sync)")
        self.assertEqual(ev.organizer_email, "alice@example.com")
        self.assertEqual(ev.organizer_cn, "Alice Smith")
        self.assertEqual(ev.organizer_display, "Alice Smith <alice@example.com>")
        self.assertEqual(ev.status, "CONFIRMED")
        self.assertEqual(ev.method, "REQUEST")
        self.assertEqual(ev.sequence, 1)
        self.assertFalse(ev.is_all_day)
        self.assertIsNotNone(ev.dtstart)
        self.assertIsNotNone(ev.dtend)
        self.assertEqual(ev.dtstart.year, 2026)
        self.assertEqual(ev.dtstart.month, 10)
        self.assertEqual(ev.dtstart.day, 29)
        self.assertEqual(ev.dtstart.hour, 14)
        self.assertEqual(ev.dtend.hour, 15)
        self.assertEqual(ev.dtend.minute, 30)
        self.assertIn("14:00 – 15:30", ev.formatted_time_range())
        self.assertEqual(len(ev.attendees), 2)
        self.assertEqual(ev.attendees[1].email, "kim@schulz.dk")
        self.assertEqual(ev.attendees[1].cn, "Kim Schulz")
        self.assertTrue(ev.attendees[1].rsvp)

        # 3. Test all-day event
        all_day_ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:hackathon-2026@example.com\r\n"
            "SUMMARY:Global Hackathon\r\n"
            "DTSTART;VALUE=DATE:20261105\r\n"
            "DTEND;VALUE=DATE:20261107\r\n"
            "STATUS:CONFIRMED\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        all_day_evs = parse_ics(all_day_ics)
        self.assertEqual(len(all_day_evs), 1)
        self.assertTrue(all_day_evs[0].is_all_day)
        self.assertIn("All Day", all_day_evs[0].formatted_time_range())

        # 4. Test find_calendar_event via attachment
        msg = EmailMessage(
            id="msg_cal_1",
            account_id="acc_test",
            subject="Invitation: Q4 Roadmap",
            sender_email="alice@example.com",
            sender_name="Alice",
            attachments=[
                Attachment(
                    id="att_cal_1",
                    message_id="msg_cal_1",
                    filename="invite.ics",
                    content_type="text/calendar",
                    size_bytes=len(sample_ics),
                    data=sample_ics.encode("utf-8"),
                )
            ],
        )
        found = find_calendar_event(msg)
        self.assertIsNotNone(found)
        self.assertEqual(found.summary, ev.summary)

        # 5. Test find_calendar_event via body text
        msg_body = EmailMessage(
            id="msg_cal_2",
            account_id="acc_test",
            subject="Event Details",
            sender_email="alice@example.com",
            body_text=f"Please find the calendar payload below:\n{sample_ics}",
        )
        found_body = find_calendar_event(msg_body)
        self.assertIsNotNone(found_body)
        self.assertEqual(found_body.uid, "team-sync-9988@example.com")

        # 6. Test build_ics_reply
        reply_ics = build_ics_reply(
            event=ev,
            user_email="kim@schulz.dk",
            user_name="Kim Schulz",
            partstat="ACCEPTED",
            comment="Looking forward to it!",
        )
        self.assertIn("METHOD:REPLY", reply_ics)
        self.assertIn("ATTENDEE;PARTSTAT=ACCEPTED;CN=\"Kim Schulz\":mailto:kim@schulz.dk", reply_ics)
        self.assertIn("COMMENT:Looking forward to it!", reply_ics)
        self.assertIn("UID:team-sync-9988@example.com", reply_ics)

        # 7. Test build_rsvp_mime_message
        mime_msg, domain_msg = build_rsvp_mime_message(
            account=self.account,
            orig_msg=msg,
            event=ev,
            partstat="ACCEPTED",
            comment="Looking forward to it!",
        )
        self.assertEqual(mime_msg["To"], "alice@example.com")
        self.assertIn("Accepted: Q4 Engineering Roadmap", mime_msg["Subject"])
        self.assertEqual(domain_msg.folder, "Sent")
        self.assertEqual(len(domain_msg.attachments), 1)
        self.assertEqual(domain_msg.attachments[0].filename, "invite.ics")
        self.assertIn(b"PARTSTAT=ACCEPTED", domain_msg.attachments[0].data)

    def test_imap_multisearch_and_modern_search_features(self) -> None:
        """Verify RFC 7377 MULTISEARCH response parsing, criteria building, and client search methods."""
        from unittest.mock import MagicMock
        from espressomail.protocols.imap_client import (
            IMAPClient,
            build_imap_search_criteria,
            parse_multisearch_response,
        )
        from espressomail.storage.database import LocalStore

        # 1. Test parse_multisearch_response with individual UIDs and ranges
        lines = [
            '* ESEARCH (TAG "A01") MAILBOX "INBOX" UID 101 102 105:108',
            b'* ESEARCH (TAG "A01") MAILBOX "Sent" UID 201 202',
            b'* ESEARCH (TAG "A01") MAILBOX "Archives/2026" UID 300 305:307',
        ]
        parsed = parse_multisearch_response(lines)
        self.assertEqual(parsed["INBOX"], [101, 102, 105, 106, 107, 108])
        self.assertEqual(parsed["Sent"], [201, 202])
        self.assertEqual(parsed["Archives/2026"], [300, 305, 306, 307])

        # 2. Test build_imap_search_criteria
        crit = build_imap_search_criteria("from:elena subject:release is:unread espresso")
        self.assertIn("FROM", crit)
        self.assertIn("elena", crit)
        self.assertIn("SUBJECT", crit)
        self.assertIn("release", crit)
        self.assertIn("UNSEEN", crit)
        self.assertIn("TEXT", crit)
        self.assertIn("espresso", crit)

        # 3. Test IMAPClient with MULTISEARCH capability
        client = IMAPClient(self.account)
        mock_conn = MagicMock()
        mock_conn.capabilities = ("IMAP4REV1", "MULTISEARCH", "ESEARCH", "IDLE")
        client._conn = mock_conn

        # Mock MULTISEARCH response
        mock_conn._simple_command.return_value = (
            "OK",
            ['* ESEARCH (TAG "X") MAILBOX "INBOX" UID 50 51', '* ESEARCH (TAG "X") MAILBOX "Sent" UID 80'],
        )
        mock_conn.untagged_responses = {}

        res_uids = client.search_uids_remote(
            criteria="espresso",
            folders=["INBOX", "Sent"],
            limit=50,
        )
        self.assertEqual(res_uids["INBOX"], [50, 51])
        self.assertEqual(res_uids["Sent"], [80])
        # Verify MULTISEARCH command format was sent
        mock_conn._simple_command.assert_called_once()
        cmd_args = mock_conn._simple_command.call_args[0]
        self.assertEqual(cmd_args[0], "UID")
        self.assertEqual(cmd_args[1], "SEARCH")
        self.assertIn('IN (mailboxes "INBOX" "Sent")', cmd_args[2])

        # 4. Test IMAPClient fallback without MULTISEARCH capability (per-mailbox UID SEARCH)
        mock_conn_no_multi = MagicMock()
        mock_conn_no_multi.capabilities = ("IMAP4REV1", "IDLE")
        mock_conn_no_multi.select.return_value = ("OK", [b"100"])
        mock_conn_no_multi.uid.return_value = ("OK", [b"10 20 30"])
        client_no_multi = IMAPClient(self.account)
        client_no_multi._conn = mock_conn_no_multi

        fallback_res = client_no_multi.search_uids_remote(
            criteria="from:elena",
            folders=["INBOX"],
            limit=10,
        )
        self.assertIn("INBOX", fallback_res)
        self.assertEqual(fallback_res["INBOX"], [30, 20, 10])

        # 5. Test search_remote_messages with store caching
        sample_mime = (
            b"From: elena@example.com\r\n"
            b"To: kim@schulz.dk\r\n"
            b"Subject: Remote search hit\r\n"
            b"Date: Sun, 27 Oct 2026 10:00:00 +0000\r\n"
            b"\r\n"
            b"Matched remote search body payload."
        )
        mock_fetch_data = [
            (b"1 (BODY[] {%d}\r\n" % len(sample_mime), sample_mime),
            b" UID 30 FLAGS (\\Seen))",
        ]
        mock_conn_no_multi.uid.side_effect = [
            ("OK", [b"30"]),  # search result
            ("OK", mock_fetch_data),  # fetch result
        ]

        store = LocalStore(":memory:")
        store.save_account(self.account)
        matched_msgs = client_no_multi.search_remote_messages(
            query="from:elena",
            folders=["INBOX"],
            limit=5,
            store=store,
        )
        self.assertEqual(len(matched_msgs), 1)
        self.assertEqual(matched_msgs[0].subject, "Remote search hit")
        self.assertEqual(matched_msgs[0].folder, "INBOX")

        # Verify message was cached into store and is searchable in FTS5
        saved_msg = store.get_message(matched_msgs[0].id)
        self.assertIsNotNone(saved_msg)
        self.assertEqual(saved_msg.subject, "Remote search hit")
        fts_hits = store.search_messages_fts("Remote")
        self.assertEqual(len(fts_hits), 1)
        store.close()


if __name__ == "__main__":
    unittest.main()

