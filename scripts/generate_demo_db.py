"""Generate a realistic, self-contained demo SQLite database for EspressoMail recordings and previews."""

from __future__ import annotations

import os
from pathlib import Path

from espressomail.models.entities import (
    AccountConfig,
    Attachment,
    Contact,
    EmailMessage,
    Folder,
    FolderType,
    PGPStatus,
    SecurityType,
)
from espressomail.storage.database import LocalStore


def create_demo_database(db_path: str | Path) -> LocalStore:
    """Populate a complete, realistic demo database for EspressoMail."""
    p = Path(db_path)
    if p.exists():
        p.unlink()
    p.parent.mkdir(parents=True, exist_ok=True)

    store = LocalStore(db_path=str(p))

    # 1. Accounts
    acc_work = AccountConfig(
        id="acc_work",
        name="Acme Corp (Work)",
        email="kim@acme.corp",
        full_name="Kim Schulz",
        imap_host="imap.acme.corp",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        imap_username="kim@acme.corp",
        imap_password="demo-password-1",
        smtp_host="smtp.acme.corp",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
        smtp_username="kim@acme.corp",
        smtp_password="demo-password-1",
        signature="Best regards,\nKim Schulz\nPrincipal Systems Architect | Acme Corp",
        color_badge="#70A5FD",
    )
    acc_personal = AccountConfig(
        id="acc_personal",
        name="Schulz.dk (Personal)",
        email="kim@schulz.dk",
        full_name="Kim Schulz",
        imap_host="imap.schulz.dk",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        imap_username="kim@schulz.dk",
        imap_password="demo-password-2",
        smtp_host="smtp.schulz.dk",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
        signature="Cheers,\nKim",
        color_badge="#F6C177",
    )
    store.save_account(acc_work)
    store.save_account(acc_personal)

    # 2. Folders
    work_folders = [
        Folder(
            id="acc_work:INBOX",
            account_id="acc_work",
            name="INBOX",
            display_name="Inbox",
            folder_type=FolderType.INBOX,
        ),
        Folder(
            id="acc_work:Projects",
            account_id="acc_work",
            name="Projects",
            display_name="Projects",
            folder_type=FolderType.CUSTOM,
            selectable=False,
        ),
        Folder(
            id="acc_work:Projects/EspressoMail",
            account_id="acc_work",
            name="Projects/EspressoMail",
            display_name="EspressoMail",
            folder_type=FolderType.CUSTOM,
        ),
        Folder(
            id="acc_work:Archive",
            account_id="acc_work",
            name="Archive",
            display_name="Archive",
            folder_type=FolderType.ARCHIVE,
        ),
        Folder(
            id="acc_work:Sent",
            account_id="acc_work",
            name="Sent",
            display_name="Sent",
            folder_type=FolderType.SENT,
        ),
    ]
    personal_folders = [
        Folder(
            id="acc_personal:INBOX",
            account_id="acc_personal",
            name="INBOX",
            display_name="Inbox",
            folder_type=FolderType.INBOX,
        ),
        Folder(
            id="acc_personal:Newsletters",
            account_id="acc_personal",
            name="Newsletters",
            display_name="Newsletters",
            folder_type=FolderType.CUSTOM,
        ),
        Folder(
            id="acc_personal:Archive",
            account_id="acc_personal",
            name="Archive",
            display_name="Archive",
            folder_type=FolderType.ARCHIVE,
        ),
    ]
    store.sync_remote_folders("acc_work", work_folders)
    store.sync_remote_folders("acc_personal", personal_folders)

    # 3. Calendar Invite ICS Payload
    sample_ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Acme Corp//EspressoMail Calendar//EN\r\n"
        "METHOD:REQUEST\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:event-q4-strategy-2026@acme.corp\r\n"
        "SEQUENCE:0\r\n"
        "STATUS:CONFIRMED\r\n"
        "SUMMARY:Q4 Product Strategy & Architecture Sync\r\n"
        "DESCRIPTION:Quarterly alignment on EspressoMail 1.4 deliverables, TUI ergonomics, and IMAP performance.\r\n"
        "LOCATION:Terminal Room 4B / Meet\r\n"
        "DTSTART:20261030T140000Z\r\n"
        "DTEND:20261030T150000Z\r\n"
        "ORGANIZER;CN=Marcus Vance:mailto:marcus@acme.corp\r\n"
        "ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN=Kim Schulz:mailto:kim@acme.corp\r\n"
        "ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN=Elena Rostova:mailto:elena@terminalcraft.io\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode("utf-8")

    bench_json = (
        b'{\n'
        b'  "engine": "sqlite-fts5",\n'
        b'  "bm25_latency_ms": 1.84,\n'
        b'  "ranking_accuracy": 0.994,\n'
        b'  "records_indexed": 10000\n'
        b'}\n'
    )

    # 4. Realistic Emails
    messages = [
        EmailMessage(
            id="msg_roadmap",
            account_id="acc_work",
            folder="INBOX",
            uid=201,
            message_id="<roadmap-2026-q4@terminalcraft.io>",
            thread_id="thread_roadmap",
            subject="EspressoMail 1.4 Architecture & Terminal Graphics Roadmap",
            sender_name="Elena Rostova",
            sender_email="elena@terminalcraft.io",
            recipients_to=["kim@acme.corp", "team@acme.corp"],
            date_iso="2026-10-29T08:30:00+00:00",
            timestamp=1793262600.0,
            is_read=True,
            is_starred=True,
            pgp_status=PGPStatus.SIGNED_VERIFIED,
            pgp_signer="Elena Rostova <elena@terminalcraft.io>",
            body_text=(
                "# EspressoMail 1.4 Architecture & Milestones\n\n"
                "Hi Kim,\n\n"
                "Here is the finalized release roadmap for our upcoming version:\n\n"
                "### Completed Capabilities\n"
                "* [x] **Full-Text Search (SQLite FTS5)** with BM25 ranking and porter stemming\n"
                "* [x] **RFC 7377 MULTISEARCH** across mailboxes with ESEARCH fallback\n"
                "* [x] **Single-key triaging** (e, y, d, #, J, s, u, v, x) for inbox zero\n"
                "* [x] **Terminal graphics rendering** via Kitty, Sixel, and Chafa half-blocks\n"
                "* [x] **RFC 5545 iCalendar & RSVP** response flow\n"
                "* [x] **Configurable keymaps** (default, vim, gmail, pine)\n"
                "* [x] **External editor ($EDITOR)** with clean terminal suspension\n\n"
                "```python\n"
                "# FTS5 BM25 search benchmark\n"
                "results = store.search_messages_fts('roadmap')\n"
                "for r in results:\n"
                "    print(f'Match: {r.subject} (rank={r.bm25_rank:.2f})')\n"
                "```\n\n"
                "> The Elm Architecture (TEA) in Espresso keeps memory footprint under 25MB\n"
                "> even with 50k messages cached in local SQLite WAL mode.\n\n"
                "Let's sync up on the demo recording!\n\n"
                "-- \n"
                "Elena Rostova\n"
                "VP Engineering | TerminalCraft"
            ),
            attachments=[
                Attachment(
                    id="att_roadmap_json",
                    message_id="msg_roadmap",
                    filename="benchmarks_v1.4.json",
                    content_type="application/json",
                    size_bytes=len(bench_json),
                    data=bench_json,
                ),
            ],
        ),
        EmailMessage(
            id="msg_invite",
            account_id="acc_work",
            folder="INBOX",
            uid=202,
            message_id="<calendar-invite-q4@acme.corp>",
            thread_id="thread_invite",
            subject="Product Strategy & Architecture Sync (RSVP)",
            sender_name="Marcus Vance",
            sender_email="marcus@acme.corp",
            recipients_to=["kim@acme.corp"],
            date_iso="2026-10-29T09:15:00+00:00",
            timestamp=1793265300.0,
            is_read=True,
            is_starred=True,
            body_text=(
                "Hi Kim,\n\n"
                "Please review the calendar invitation below for tomorrow's strategy sync.\n\n"
                "Agenda:\n"
                "1. TUI ergonomics & single-key triage workflow\n"
                "2. IMAP IDLE push performance on high-volume accounts\n"
                "3. GitHub README & PyPI release presentation\n\n"
                "Looking forward to speaking tomorrow.\n\n"
                "-- \n"
                "Marcus Vance\n"
                "Chief Product Officer"
            ),
            attachments=[
                Attachment(
                    id="att_invite_ics",
                    message_id="msg_invite",
                    filename="strategy_sync.ics",
                    content_type="text/calendar",
                    size_bytes=len(sample_ics),
                    data=sample_ics,
                ),
            ],
        ),
        EmailMessage(
            id="msg_security",
            account_id="acc_work",
            folder="INBOX",
            uid=203,
            message_id="<security-audit-ed25519@acme.corp>",
            thread_id="thread_security",
            subject="Cryptographic Audit: Ed25519 & OpenPGP Implementation",
            sender_name="Dr. Aris Thorne",
            sender_email="security@acme.corp",
            recipients_to=["kim@acme.corp"],
            date_iso="2026-10-28T16:45:00+00:00",
            timestamp=1793205900.0,
            is_read=True,
            pgp_status=PGPStatus.SIGNED_VERIFIED,
            pgp_signer="Dr. Aris Thorne <security@acme.corp>",
            body_text=(
                "Security Audit Summary: Clean Pass\n\n"
                "We completed the cryptographic review of EspressoMail's OpenPGP implementation:\n\n"
                "- Ed25519 signature verification: Verified\n"
                "- Recipient encryption with AES-256: Verified\n"
                "- Memory hygiene during decryption: Verified\n\n"
                "No vulnerabilities detected. Ready for v1.4 production deployment.\n\n"
                "Dr. Aris Thorne\n"
                "Head of Information Security"
            ),
        ),
        EmailMessage(
            id="msg_benchmarks",
            account_id="acc_work",
            folder="Projects/EspressoMail",
            uid=204,
            message_id="<sqlite-fts5-benchmarks@kerneldb.io>",
            thread_id="thread_benchmarks",
            subject="SQLite WAL Mode & FTS5 BM25 Performance Benchmarks",
            sender_name="Sophia Chen",
            sender_email="sophia.chen@kerneldb.io",
            recipients_to=["kim@acme.corp"],
            date_iso="2026-10-28T14:20:00+00:00",
            timestamp=1793197200.0,
            is_read=True,
            body_text=(
                "Hey Kim,\n\n"
                "Here are the SQLite performance numbers across 50,000 indexed emails:\n\n"
                "| Operation | Dataset | Latency | Memory |\n"
                "|---|---|---|---|\n"
                "| Full-text BM25 query | 50,000 records | 2.4 ms | < 12 MB |\n"
                "| Porter token indexing | 1,000 batch | 38 ms | 15 MB |\n"
                "| Subfolder hierarchy build | 120 folders | 0.8 ms | < 2 MB |\n\n"
                "FTS5 combined with WAL mode yields exceptional responsiveness in the TUI.\n\n"
                "Sophia Chen\n"
                "Staff Database Engineer"
            ),
        ),
        EmailMessage(
            id="msg_digest",
            account_id="acc_personal",
            folder="INBOX",
            uid=301,
            message_id="<terminal-renaissance-2026@dailyhacker.org>",
            thread_id="thread_digest",
            subject="Terminal Emulators in 2026: TrueColor, Sixel & Modern TUI",
            sender_name="The Daily Hacker",
            sender_email="news@dailyhacker.org",
            recipients_to=["kim@schulz.dk"],
            date_iso="2026-10-29T07:00:00+00:00",
            timestamp=1793257200.0,
            is_read=True,
            body_text=(
                "# The Terminal Renaissance in 2026\n\n"
                "In this week's issue:\n"
                "- How modern terminal applications leverage 24-bit TrueColor and Sixel graphics.\n"
                "- The Elm Architecture (TEA) in CLI tools: Why declarative state management wins.\n"
                "- Keyboard ergonomics: How single-key triage helps developers conquer Inbox Zero.\n\n"
                "Read full analysis on dailyhacker.org\n"
            ),
        ),
        EmailMessage(
            id="msg_fastmail_sync",
            account_id="acc_personal",
            folder="INBOX",
            uid=302,
            message_id="<synced-folder-note@schulz.dk>",
            thread_id="thread_fastmail",
            subject="Backup & Archival Routine Scheduled",
            sender_name="System Daemon",
            sender_email="daemon@schulz.dk",
            recipients_to=["kim@schulz.dk"],
            date_iso="2026-10-27T10:00:00+00:00",
            timestamp=1793095200.0,
            is_read=True,
            body_text="Automated weekly IMAP folder backup completed successfully to encrypted storage.",
        ),
    ]
    store.save_messages(messages)

    # 5. Contacts
    contacts = [
        Contact(id="cnt_elena", name="Elena Rostova", email="elena@terminalcraft.io"),
        Contact(id="cnt_marcus", name="Marcus Vance", email="marcus@acme.corp"),
        Contact(id="cnt_aris", name="Dr. Aris Thorne", email="security@acme.corp"),
        Contact(id="cnt_sophia", name="Sophia Chen", email="sophia.chen@kerneldb.io"),
    ]
    for c in contacts:
        store.save_contact(c)

    # 6. Default Settings
    store.set_setting("theme", "espresso")
    store.set_setting("keymap_preset", "default")
    store.set_setting("folder_count_mode", "unread_and_total")
    store.set_setting("external_editor", "auto")

    return store


if __name__ == "__main__":
    target = os.environ.get("DEMO_DB_PATH", "/tmp/espressomail_demo.db")
    print(f"Creating demo database at {target}...")
    s = create_demo_database(target)
    print(f"Demo database ready: {len(s.list_accounts())} accounts, {len(s.list_messages())} messages.")
    s.close()
