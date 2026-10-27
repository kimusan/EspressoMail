"""SQLite local email store for offline access, caching, drafts, contacts, and settings."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from espressomail.models.entities import (
    AccountConfig,
    Attachment,
    Contact,
    EmailMessage,
    Folder,
    FolderType,
    PGPStatus,
    SecurityType,
    SieveFilterRule,
    SieveScript,
)


def default_data_dir() -> Path:
    """Return the default XDG-compliant data directory for EspressoMail."""
    env_dir = os.environ.get("ESPRESSOMAIL_HOME")
    if env_dir:
        path = Path(env_dir).expanduser().resolve()
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            path = Path(xdg_data).expanduser().resolve() / "espressomail"
        else:
            path = Path.home() / ".local" / "share" / "espressomail"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_sample_ppm_image(width: int = 32, height: int = 20) -> bytes:
    """Generate a compact Netpbm P6 PPM image (coffee cup & warm sunset gradient)."""
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            # Cup body in center
            in_cup = (9 <= x <= 22) and (8 <= y <= 16)
            in_rim = (8 <= x <= 23) and (y == 7)
            in_saucer = (6 <= x <= 25) and (y == 17)
            in_handle = (23 <= x <= 25) and (10 <= y <= 14) and not (x == 24 and 11 <= y <= 13)
            in_steam = (x in (12, 16, 19)) and (2 <= y <= 5) and ((x + y) % 2 == 0)

            if in_rim or in_saucer:
                pixels.extend((245, 235, 220))
            elif in_cup or in_handle:
                if y == 8:
                    # Crema foam layer
                    pixels.extend((212, 163, 115))
                else:
                    pixels.extend((230, 210, 190))
            elif in_steam:
                pixels.extend((255, 220, 180))
            else:
                # Warm espresso background gradient
                r = int(35 + (x / max(1, width - 1)) * 75)
                g = int(22 + (y / max(1, height - 1)) * 45)
                b = int(45 + ((width - x) / max(1, width - 1)) * 65)
                pixels.extend((r, g, b))
    return header + bytes(pixels)


class LocalStore:
    """SQLite-backed local email database supporting multi-account offline operation."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            self.db_path = str(default_data_dir() / "espressomail.db")
        else:
            self.db_path = str(db_path)

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._fts_enabled = False
        self._initialize_schema()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def _initialize_schema(self) -> None:
        """Create tables, indices, and optional FTS5 virtual table."""
        cur = self._conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            cur.execute("PRAGMA journal_mode = WAL;")

        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                imap_host TEXT NOT NULL,
                imap_port INTEGER NOT NULL DEFAULT 993,
                imap_security TEXT NOT NULL DEFAULT 'ssl_tls',
                imap_username TEXT NOT NULL,
                imap_password TEXT NOT NULL DEFAULT '',
                smtp_host TEXT NOT NULL,
                smtp_port INTEGER NOT NULL DEFAULT 587,
                smtp_security TEXT NOT NULL DEFAULT 'starttls',
                smtp_username TEXT NOT NULL,
                smtp_password TEXT NOT NULL DEFAULT '',
                sieve_host TEXT NOT NULL DEFAULT '',
                sieve_port INTEGER NOT NULL DEFAULT 4190,
                pgp_key_id TEXT NOT NULL DEFAULT '',
                signature TEXT NOT NULL DEFAULT '',
                color_badge TEXT NOT NULL DEFAULT '#D4A373',
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS folders (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                folder_type TEXT NOT NULL DEFAULT 'custom',
                unread_count INTEGER NOT NULL DEFAULT 0,
                total_count INTEGER NOT NULL DEFAULT 0,
                uid_validity INTEGER NOT NULL DEFAULT 0,
                last_uid INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                folder TEXT NOT NULL DEFAULT 'INBOX',
                uid INTEGER NOT NULL DEFAULT 0,
                message_id TEXT NOT NULL DEFAULT '',
                in_reply_to TEXT NOT NULL DEFAULT '',
                references_json TEXT NOT NULL DEFAULT '[]',
                thread_id TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                sender_name TEXT NOT NULL DEFAULT '',
                sender_email TEXT NOT NULL DEFAULT '',
                recipients_to_json TEXT NOT NULL DEFAULT '[]',
                recipients_cc_json TEXT NOT NULL DEFAULT '[]',
                recipients_bcc_json TEXT NOT NULL DEFAULT '[]',
                date_iso TEXT NOT NULL DEFAULT '',
                timestamp REAL NOT NULL DEFAULT 0.0,
                snippet TEXT NOT NULL DEFAULT '',
                body_text TEXT NOT NULL DEFAULT '',
                body_html TEXT NOT NULL DEFAULT '',
                raw_headers_json TEXT NOT NULL DEFAULT '{}',
                is_read INTEGER NOT NULL DEFAULT 0,
                is_starred INTEGER NOT NULL DEFAULT 0,
                is_draft INTEGER NOT NULL DEFAULT 0,
                is_trashed INTEGER NOT NULL DEFAULT 0,
                is_answered INTEGER NOT NULL DEFAULT 0,
                pgp_status TEXT NOT NULL DEFAULT 'none',
                pgp_key_fingerprint TEXT NOT NULL DEFAULT '',
                pgp_signer TEXT NOT NULL DEFAULT '',
                pgp_raw_payload TEXT NOT NULL DEFAULT '',
                labels_json TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_acc_folder_ts
                ON messages(account_id, folder, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_thread
                ON messages(thread_id);
            CREATE INDEX IF NOT EXISTS idx_messages_flags
                ON messages(is_trashed, is_draft, is_starred, is_read);

            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                content_id TEXT NOT NULL DEFAULT '',
                is_inline INTEGER NOT NULL DEFAULT 0,
                data BLOB,
                local_path TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_attachments_msg
                ON attachments(message_id);

            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                organization TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                pgp_fingerprint TEXT NOT NULL DEFAULT '',
                favorite INTEGER NOT NULL DEFAULT 0,
                frequency INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sieve_rules (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                match_all INTEGER NOT NULL DEFAULT 1,
                field TEXT NOT NULL DEFAULT 'From',
                comparator TEXT NOT NULL DEFAULT 'contains',
                pattern TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT 'fileinto',
                action_arg TEXT NOT NULL DEFAULT 'Archive',
                stop_processing INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sieve_scripts (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            """
        )

        try:
            cur.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    msg_id UNINDEXED,
                    subject,
                    sender_name,
                    sender_email,
                    recipients,
                    body_text,
                    folder
                );
                """
            )
            self._fts_enabled = True
        except sqlite3.OperationalError:
            self._fts_enabled = False

        self._conn.commit()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_setting(self, key: str, default: str = "") -> str:
        """Retrieve a persistent setting by key."""
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Store a persistent setting key-value pair."""
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Accounts & Folders
    # ------------------------------------------------------------------

    def save_account(self, account: AccountConfig) -> None:
        """Insert or update an email account and ensure default folders exist."""
        self._conn.execute(
            """
            INSERT INTO accounts (
                id, name, email, full_name,
                imap_host, imap_port, imap_security, imap_username, imap_password,
                smtp_host, smtp_port, smtp_security, smtp_username, smtp_password,
                sieve_host, sieve_port, pgp_key_id, signature, color_badge, enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                email = excluded.email,
                full_name = excluded.full_name,
                imap_host = excluded.imap_host,
                imap_port = excluded.imap_port,
                imap_security = excluded.imap_security,
                imap_username = excluded.imap_username,
                imap_password = excluded.imap_password,
                smtp_host = excluded.smtp_host,
                smtp_port = excluded.smtp_port,
                smtp_security = excluded.smtp_security,
                smtp_username = excluded.smtp_username,
                smtp_password = excluded.smtp_password,
                sieve_host = excluded.sieve_host,
                sieve_port = excluded.sieve_port,
                pgp_key_id = excluded.pgp_key_id,
                signature = excluded.signature,
                color_badge = excluded.color_badge,
                enabled = excluded.enabled
            """,
            (
                account.id,
                account.name,
                account.email,
                account.full_name,
                account.imap_host,
                account.imap_port,
                account.imap_security.value,
                account.imap_username,
                account.imap_password,
                account.smtp_host,
                account.smtp_port,
                account.smtp_security.value,
                account.smtp_username,
                account.smtp_password,
                account.sieve_host,
                account.sieve_port,
                account.pgp_key_id,
                account.signature,
                account.color_badge,
                int(account.enabled),
            ),
        )
        self._ensure_default_folders(account.id)
        self._conn.commit()

    def _ensure_default_folders(self, account_id: str) -> None:
        """Create standard mailbox folders for an account if not already present."""
        defaults = [
            ("INBOX", "Inbox", FolderType.INBOX),
            ("Starred", "Starred", FolderType.STARRED),
            ("Sent", "Sent", FolderType.SENT),
            ("Drafts", "Drafts", FolderType.DRAFTS),
            ("Archive", "Archive", FolderType.ARCHIVE),
            ("Trash", "Trash", FolderType.TRASH),
        ]
        for name, display, ftype in defaults:
            fid = f"{account_id}:{name}"
            self._conn.execute(
                """
                INSERT OR IGNORE INTO folders (id, account_id, name, display_name, folder_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fid, account_id, name, display, ftype.value),
            )

    def list_accounts(self, enabled_only: bool = False) -> list[AccountConfig]:
        """Return all configured email accounts."""
        query = "SELECT * FROM accounts"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name ASC"
        rows = self._conn.execute(query).fetchall()
        return [self._row_to_account(r) for r in rows]

    def get_account(self, account_id: str) -> AccountConfig | None:
        """Return a specific account by ID."""
        row = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return self._row_to_account(row) if row else None

    def delete_account(self, account_id: str) -> None:
        """Remove an account and all associated messages/folders."""
        if self._fts_enabled:
            msg_ids = [
                r["id"]
                for r in self._conn.execute(
                    "SELECT id FROM messages WHERE account_id = ?", (account_id,)
                ).fetchall()
            ]
            for mid in msg_ids:
                self._conn.execute("DELETE FROM messages_fts WHERE msg_id = ?", (mid,))
        self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        self._conn.commit()

    def _row_to_account(self, row: sqlite3.Row) -> AccountConfig:
        return AccountConfig(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            full_name=row["full_name"],
            imap_host=row["imap_host"],
            imap_port=int(row["imap_port"]),
            imap_security=SecurityType(row["imap_security"]),
            imap_username=row["imap_username"],
            imap_password=row["imap_password"],
            smtp_host=row["smtp_host"],
            smtp_port=int(row["smtp_port"]),
            smtp_security=SecurityType(row["smtp_security"]),
            smtp_username=row["smtp_username"],
            smtp_password=row["smtp_password"],
            sieve_host=row["sieve_host"],
            sieve_port=int(row["sieve_port"]),
            pgp_key_id=row["pgp_key_id"],
            signature=row["signature"],
            color_badge=row["color_badge"],
            enabled=bool(row["enabled"]),
        )

    def list_folders(self, account_id: str | None = None) -> list[Folder]:
        """List folders with live unread and total message counts."""
        self.refresh_folder_counts()
        if account_id:
            rows = self._conn.execute(
                "SELECT * FROM folders WHERE account_id = ? ORDER BY rowid ASC",
                (account_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM folders ORDER BY account_id, rowid ASC").fetchall()
        return [
            Folder(
                id=r["id"],
                account_id=r["account_id"],
                name=r["name"],
                display_name=r["display_name"],
                folder_type=FolderType(r["folder_type"]),
                unread_count=int(r["unread_count"]),
                total_count=int(r["total_count"]),
                uid_validity=int(r["uid_validity"]),
                last_uid=int(r["last_uid"]),
            )
            for r in rows
        ]

    def get_unified_counts(self) -> dict[str, tuple[int, int]]:
        """Return (unread_count, total_count) for Unified virtual folders across all accounts."""
        result: dict[str, tuple[int, int]] = {}
        # Unified Inbox
        row = self._conn.execute(
            "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
            "FROM messages WHERE folder = 'INBOX' AND is_trashed = 0 AND is_draft = 0"
        ).fetchone()
        result["INBOX"] = (int(row["u"] or 0), int(row["t"] or 0))

        # Unified Starred
        row = self._conn.execute(
            "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
            "FROM messages WHERE is_starred = 1 AND is_trashed = 0"
        ).fetchone()
        result["Starred"] = (int(row["u"] or 0), int(row["t"] or 0))

        # Unified Drafts
        row = self._conn.execute(
            "SELECT COUNT(*) AS t FROM messages WHERE (is_draft = 1 OR folder = 'Drafts') AND is_trashed = 0"
        ).fetchone()
        draft_cnt = int(row["t"] or 0)
        result["Drafts"] = (draft_cnt, draft_cnt)

        # Unified Sent
        row = self._conn.execute(
            "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
            "FROM messages WHERE folder = 'Sent' AND is_trashed = 0"
        ).fetchone()
        result["Sent"] = (int(row["u"] or 0), int(row["t"] or 0))

        # Unified Archive
        row = self._conn.execute(
            "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
            "FROM messages WHERE folder = 'Archive' AND is_trashed = 0"
        ).fetchone()
        result["Archive"] = (int(row["u"] or 0), int(row["t"] or 0))

        # Unified Trash
        row = self._conn.execute(
            "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
            "FROM messages WHERE is_trashed = 1 OR folder = 'Trash'"
        ).fetchone()
        result["Trash"] = (int(row["u"] or 0), int(row["t"] or 0))

        return result

    def refresh_folder_counts(self) -> None:
        """Recalculate unread and total message counts for all account folders."""
        folders = self._conn.execute("SELECT id, account_id, name, folder_type FROM folders").fetchall()
        for f in folders:
            acc_id = f["account_id"]
            fname = f["name"]
            ftype = f["folder_type"]
            if ftype == FolderType.STARRED.value or fname == "Starred":
                row = self._conn.execute(
                    "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
                    "FROM messages WHERE account_id = ? AND is_starred = 1 AND is_trashed = 0",
                    (acc_id,),
                ).fetchone()
            elif ftype == FolderType.TRASH.value or fname == "Trash":
                row = self._conn.execute(
                    "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
                    "FROM messages WHERE account_id = ? AND (is_trashed = 1 OR folder = 'Trash')",
                    (acc_id,),
                ).fetchone()
            elif ftype == FolderType.DRAFTS.value or fname == "Drafts":
                row = self._conn.execute(
                    "SELECT COUNT(*) AS u, COUNT(*) AS t "
                    "FROM messages WHERE account_id = ? AND (is_draft = 1 OR folder = 'Drafts') AND is_trashed = 0",
                    (acc_id,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
                    "FROM messages WHERE account_id = ? AND folder = ? AND is_trashed = 0",
                    (acc_id, fname),
                ).fetchone()
            u = int(row["u"] or 0)
            t = int(row["t"] or 0)
            self._conn.execute(
                "UPDATE folders SET unread_count = ?, total_count = ? WHERE id = ?",
                (u, t, f["id"]),
            )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Messages & Attachments CRUD
    # ------------------------------------------------------------------

    def save_message(self, msg: EmailMessage, harvest_contacts: bool = True) -> None:
        """Insert or update an EmailMessage and its attachments in the local store."""
        if not msg.thread_id:
            msg.thread_id = msg.in_reply_to or (msg.references[0] if msg.references else msg.message_id) or msg.id

        self._conn.execute(
            """
            INSERT INTO messages (
                id, account_id, folder, uid, message_id, in_reply_to, references_json,
                thread_id, subject, sender_name, sender_email,
                recipients_to_json, recipients_cc_json, recipients_bcc_json,
                date_iso, timestamp, snippet, body_text, body_html, raw_headers_json,
                is_read, is_starred, is_draft, is_trashed, is_answered,
                pgp_status, pgp_key_fingerprint, pgp_signer, pgp_raw_payload, labels_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(id) DO UPDATE SET
                folder = excluded.folder,
                uid = excluded.uid,
                message_id = excluded.message_id,
                in_reply_to = excluded.in_reply_to,
                references_json = excluded.references_json,
                thread_id = excluded.thread_id,
                subject = excluded.subject,
                sender_name = excluded.sender_name,
                sender_email = excluded.sender_email,
                recipients_to_json = excluded.recipients_to_json,
                recipients_cc_json = excluded.recipients_cc_json,
                recipients_bcc_json = excluded.recipients_bcc_json,
                date_iso = excluded.date_iso,
                timestamp = excluded.timestamp,
                snippet = excluded.snippet,
                body_text = excluded.body_text,
                body_html = excluded.body_html,
                raw_headers_json = excluded.raw_headers_json,
                is_read = excluded.is_read,
                is_starred = excluded.is_starred,
                is_draft = excluded.is_draft,
                is_trashed = excluded.is_trashed,
                is_answered = excluded.is_answered,
                pgp_status = excluded.pgp_status,
                pgp_key_fingerprint = excluded.pgp_key_fingerprint,
                pgp_signer = excluded.pgp_signer,
                pgp_raw_payload = excluded.pgp_raw_payload,
                labels_json = excluded.labels_json
            """,
            (
                msg.id,
                msg.account_id,
                msg.folder,
                msg.uid,
                msg.message_id,
                msg.in_reply_to,
                json.dumps(msg.references),
                msg.thread_id,
                msg.subject,
                msg.sender_name,
                msg.sender_email,
                json.dumps(msg.recipients_to),
                json.dumps(msg.recipients_cc),
                json.dumps(msg.recipients_bcc),
                msg.date_iso,
                msg.timestamp,
                msg.snippet,
                msg.body_text,
                msg.body_html,
                json.dumps(msg.raw_headers),
                int(msg.is_read),
                int(msg.is_starred),
                int(msg.is_draft),
                int(msg.is_trashed),
                int(msg.is_answered),
                msg.pgp_status.value,
                msg.pgp_key_fingerprint,
                msg.pgp_signer,
                msg.pgp_raw_payload,
                json.dumps(msg.labels),
            ),
        )

        # Save attachments
        self._conn.execute("DELETE FROM attachments WHERE message_id = ?", (msg.id,))
        for att in msg.attachments:
            att_id = att.id or f"{msg.id}:att:{att.filename}"
            self._conn.execute(
                """
                INSERT INTO attachments (
                    id, message_id, filename, content_type, size_bytes,
                    content_id, is_inline, data, local_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    att_id,
                    msg.id,
                    att.filename,
                    att.content_type,
                    att.size_bytes or (len(att.data) if att.data else 0),
                    att.content_id,
                    int(att.is_inline),
                    att.data,
                    att.local_path,
                ),
            )

        # Update FTS5 index
        if self._fts_enabled:
            self._conn.execute("DELETE FROM messages_fts WHERE msg_id = ?", (msg.id,))
            recipients_flat = " ".join(msg.recipients_to + msg.recipients_cc)
            self._conn.execute(
                """
                INSERT INTO messages_fts (
                    msg_id, subject, sender_name, sender_email, recipients, body_text, folder
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.id,
                    msg.subject,
                    msg.sender_name,
                    msg.sender_email,
                    recipients_flat,
                    msg.body_text,
                    msg.folder,
                ),
            )

        if harvest_contacts and msg.sender_email:
            self._harvest_contact(msg.sender_name, msg.sender_email)

        self._conn.commit()

    def get_message(self, msg_id: str) -> EmailMessage | None:
        """Retrieve a single EmailMessage (including attachments) by ID."""
        row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return None
        return self._row_to_message(row, include_attachments=True)

    def list_messages(
        self,
        account_id: str = "*",
        folder: str = "INBOX",
        include_trashed: bool = False,
        limit: int = 500,
    ) -> list[EmailMessage]:
        """Query messages for a specific account/folder or across all accounts (Unified)."""
        params: list[Any] = []
        where_clauses: list[str] = []

        if account_id and account_id != "*":
            where_clauses.append("account_id = ?")
            params.append(account_id)

        folder_upper = folder.upper()
        if folder_upper == "STARRED":
            where_clauses.append("is_starred = 1")
            where_clauses.append("is_trashed = 0")
        elif folder_upper == "TRASH":
            where_clauses.append("(is_trashed = 1 OR folder = 'Trash')")
        elif folder_upper == "DRAFTS":
            where_clauses.append("(is_draft = 1 OR folder = 'Drafts')")
            where_clauses.append("is_trashed = 0")
        elif folder_upper == "ALL":
            if not include_trashed:
                where_clauses.append("is_trashed = 0")
        else:
            where_clauses.append("folder = ?")
            params.append(folder)
            if not include_trashed:
                where_clauses.append("is_trashed = 0")

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        query = f"SELECT * FROM messages WHERE {where_sql} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_message(r, include_attachments=True) for r in rows]

    def mark_read(self, msg_id: str, is_read: bool = True) -> None:
        """Set or unset the read status of a message."""
        self._conn.execute("UPDATE messages SET is_read = ? WHERE id = ?", (int(is_read), msg_id))
        self._conn.commit()

    def toggle_star(self, msg_id: str) -> bool:
        """Toggle the starred flag on a message and return the new state."""
        row = self._conn.execute("SELECT is_starred FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return False
        new_val = 0 if row["is_starred"] else 1
        self._conn.execute("UPDATE messages SET is_starred = ? WHERE id = ?", (new_val, msg_id))
        self._conn.commit()
        return bool(new_val)

    def move_to_folder(self, msg_id: str, target_folder: str) -> None:
        """Move a message to another folder (e.g. Archive, INBOX)."""
        is_trash = 1 if target_folder.lower() == "trash" else 0
        is_draft = 1 if target_folder.lower() == "drafts" else 0
        self._conn.execute(
            "UPDATE messages SET folder = ?, is_trashed = ?, is_draft = ? WHERE id = ?",
            (target_folder, is_trash, is_draft, msg_id),
        )
        if self._fts_enabled:
            self._conn.execute(
                "UPDATE messages_fts SET folder = ? WHERE msg_id = ?",
                (target_folder, msg_id),
            )
        self._conn.commit()

    def move_to_trash(self, msg_id: str) -> None:
        """Move a message to Trash (or permanently delete if already in Trash)."""
        row = self._conn.execute(
            "SELECT is_trashed, folder FROM messages WHERE id = ?", (msg_id,)
        ).fetchone()
        if not row:
            return
        if row["is_trashed"] or row["folder"] == "Trash":
            self.delete_message_permanently(msg_id)
        else:
            self._conn.execute(
                "UPDATE messages SET is_trashed = 1, folder = 'Trash' WHERE id = ?",
                (msg_id,),
            )
            self._conn.commit()

    def restore_from_trash(self, msg_id: str, target_folder: str = "INBOX") -> None:
        """Restore a trashed message back to INBOX or specified folder."""
        self._conn.execute(
            "UPDATE messages SET is_trashed = 0, folder = ? WHERE id = ?",
            (target_folder, msg_id),
        )
        self._conn.commit()

    def empty_trash(self, account_id: str = "*") -> int:
        """Permanently delete all messages in Trash. Returns count of deleted messages."""
        if account_id and account_id != "*":
            rows = self._conn.execute(
                "SELECT id FROM messages WHERE account_id = ? AND (is_trashed = 1 OR folder = 'Trash')",
                (account_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id FROM messages WHERE is_trashed = 1 OR folder = 'Trash'"
            ).fetchall()
        ids = [r["id"] for r in rows]
        for mid in ids:
            self.delete_message_permanently(mid)
        return len(ids)

    def delete_message_permanently(self, msg_id: str) -> None:
        """Permanently remove a message and its attachments from the database."""
        if self._fts_enabled:
            self._conn.execute("DELETE FROM messages_fts WHERE msg_id = ?", (msg_id,))
        self._conn.execute("DELETE FROM attachments WHERE message_id = ?", (msg_id,))
        self._conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        self._conn.commit()

    def save_attachment_to_disk(self, attachment_id: str, dest_dir: str | Path) -> Path:
        """Export an attachment's binary payload to the target directory on disk."""
        row = self._conn.execute(
            "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Attachment not found: {attachment_id}")
        target_folder = Path(dest_dir).expanduser().resolve()
        target_folder.mkdir(parents=True, exist_ok=True)
        out_path = target_folder / row["filename"]
        data = row["data"]
        if data is None and row["local_path"] and Path(row["local_path"]).exists():
            data = Path(row["local_path"]).read_bytes()
        if data is None:
            data = b""
        out_path.write_bytes(data)
        self._conn.execute(
            "UPDATE attachments SET local_path = ? WHERE id = ?",
            (str(out_path), attachment_id),
        )
        self._conn.commit()
        return out_path

    def _row_to_message(self, row: sqlite3.Row, include_attachments: bool = True) -> EmailMessage:
        attachments: list[Attachment] = []
        if include_attachments:
            att_rows = self._conn.execute(
                "SELECT * FROM attachments WHERE message_id = ? ORDER BY rowid ASC",
                (row["id"],),
            ).fetchall()
            for a in att_rows:
                attachments.append(
                    Attachment(
                        id=a["id"],
                        message_id=a["message_id"],
                        filename=a["filename"],
                        content_type=a["content_type"],
                        size_bytes=int(a["size_bytes"]),
                        content_id=a["content_id"],
                        is_inline=bool(a["is_inline"]),
                        data=a["data"],
                        local_path=a["local_path"],
                    )
                )

        return EmailMessage(
            id=row["id"],
            account_id=row["account_id"],
            folder=row["folder"],
            uid=int(row["uid"]),
            message_id=row["message_id"],
            in_reply_to=row["in_reply_to"],
            references=json.loads(row["references_json"] or "[]"),
            thread_id=row["thread_id"],
            subject=row["subject"],
            sender_name=row["sender_name"],
            sender_email=row["sender_email"],
            recipients_to=json.loads(row["recipients_to_json"] or "[]"),
            recipients_cc=json.loads(row["recipients_cc_json"] or "[]"),
            recipients_bcc=json.loads(row["recipients_bcc_json"] or "[]"),
            date_iso=row["date_iso"],
            timestamp=float(row["timestamp"]),
            snippet=row["snippet"],
            body_text=row["body_text"],
            body_html=row["body_html"],
            raw_headers=json.loads(row["raw_headers_json"] or "{}"),
            is_read=bool(row["is_read"]),
            is_starred=bool(row["is_starred"]),
            is_draft=bool(row["is_draft"]),
            is_trashed=bool(row["is_trashed"]),
            is_answered=bool(row["is_answered"]),
            pgp_status=PGPStatus(row["pgp_status"]),
            pgp_key_fingerprint=row["pgp_key_fingerprint"],
            pgp_signer=row["pgp_signer"],
            pgp_raw_payload=row["pgp_raw_payload"],
            attachments=attachments,
            labels=json.loads(row["labels_json"] or "[]"),
        )

    # ------------------------------------------------------------------
    # Address Book / Contacts
    # ------------------------------------------------------------------

    def _harvest_contact(self, name: str, email_addr: str) -> None:
        """Automatically add or increment frequency of a sender in the address book."""
        email_clean = email_addr.strip().lower()
        if not email_clean or "@" not in email_clean:
            return
        row = self._conn.execute(
            "SELECT id, name, frequency FROM contacts WHERE lower(email) = ?",
            (email_clean,),
        ).fetchone()
        if row:
            updated_name = row["name"] or name.strip()
            self._conn.execute(
                "UPDATE contacts SET name = ?, frequency = ? WHERE id = ?",
                (updated_name, int(row["frequency"]) + 1, row["id"]),
            )
        else:
            cid = f"contact:{uuid.uuid4().hex[:10]}"
            self._conn.execute(
                """
                INSERT INTO contacts (id, name, email, frequency)
                VALUES (?, ?, ?, 1)
                """,
                (cid, name.strip() or email_clean.split("@")[0], email_clean),
            )

    def save_contact(self, contact: Contact) -> None:
        """Insert or update a contact in the local address book."""
        cid = contact.id or f"contact:{uuid.uuid4().hex[:10]}"
        self._conn.execute(
            """
            INSERT INTO contacts (
                id, name, email, organization, notes, pgp_fingerprint, favorite, frequency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name = excluded.name,
                organization = excluded.organization,
                notes = excluded.notes,
                pgp_fingerprint = excluded.pgp_fingerprint,
                favorite = excluded.favorite,
                frequency = MAX(contacts.frequency, excluded.frequency)
            """,
            (
                cid,
                contact.name,
                contact.email.strip().lower(),
                contact.organization,
                contact.notes,
                contact.pgp_fingerprint,
                int(contact.favorite),
                contact.frequency,
            ),
        )
        self._conn.commit()

    def list_contacts(self, query: str = "") -> list[Contact]:
        """List contacts ordered by favorite status, frequency, and name."""
        if query.strip():
            q = f"%{query.strip().lower()}%"
            rows = self._conn.execute(
                """
                SELECT * FROM contacts
                WHERE lower(name) LIKE ? OR lower(email) LIKE ? OR lower(organization) LIKE ?
                ORDER BY favorite DESC, frequency DESC, name ASC
                """,
                (q, q, q),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM contacts ORDER BY favorite DESC, frequency DESC, name ASC"
            ).fetchall()
        return [
            Contact(
                id=r["id"],
                name=r["name"],
                email=r["email"],
                organization=r["organization"],
                notes=r["notes"],
                pgp_fingerprint=r["pgp_fingerprint"],
                favorite=bool(r["favorite"]),
                frequency=int(r["frequency"]),
            )
            for r in rows
        ]

    def delete_contact(self, contact_id: str) -> None:
        """Delete a contact from the address book."""
        self._conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Sieve Rules & Scripts
    # ------------------------------------------------------------------

    def save_sieve_rule(self, rule: SieveFilterRule) -> None:
        """Insert or update a Sieve filter rule."""
        self._conn.execute(
            """
            INSERT INTO sieve_rules (
                id, account_id, name, enabled, match_all, field,
                comparator, pattern, action, action_arg, stop_processing
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                enabled = excluded.enabled,
                match_all = excluded.match_all,
                field = excluded.field,
                comparator = excluded.comparator,
                pattern = excluded.pattern,
                action = excluded.action,
                action_arg = excluded.action_arg,
                stop_processing = excluded.stop_processing
            """,
            (
                rule.id,
                rule.account_id,
                rule.name,
                int(rule.enabled),
                int(rule.match_all),
                rule.field,
                rule.comparator,
                rule.pattern,
                rule.action,
                rule.action_arg,
                int(rule.stop_processing),
            ),
        )
        self._conn.commit()

    def list_sieve_rules(self, account_id: str | None = None) -> list[SieveFilterRule]:
        """List Sieve filter rules for an account (or all accounts)."""
        if account_id and account_id != "*":
            rows = self._conn.execute(
                "SELECT * FROM sieve_rules WHERE account_id = ? ORDER BY rowid ASC",
                (account_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM sieve_rules ORDER BY rowid ASC").fetchall()
        return [
            SieveFilterRule(
                id=r["id"],
                account_id=r["account_id"],
                name=r["name"],
                enabled=bool(r["enabled"]),
                match_all=bool(r["match_all"]),
                field=r["field"],
                comparator=r["comparator"],
                pattern=r["pattern"],
                action=r["action"],
                action_arg=r["action_arg"],
                stop_processing=bool(r["stop_processing"]),
            )
            for r in rows
        ]

    def delete_sieve_rule(self, rule_id: str) -> None:
        """Delete a Sieve filter rule by ID."""
        self._conn.execute("DELETE FROM sieve_rules WHERE id = ?", (rule_id,))
        self._conn.commit()

    def save_sieve_script(self, script: SieveScript) -> None:
        """Save a compiled or custom Sieve script."""
        self._conn.execute(
            """
            INSERT INTO sieve_scripts (id, account_id, name, content, active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                content = excluded.content,
                active = excluded.active,
                updated_at = excluded.updated_at
            """,
            (
                script.id,
                script.account_id,
                script.name,
                script.content,
                int(script.active),
                script.updated_at,
            ),
        )
        self._conn.commit()

    def list_sieve_scripts(self, account_id: str | None = None) -> list[SieveScript]:
        """Return stored Sieve scripts."""
        if account_id and account_id != "*":
            rows = self._conn.execute(
                "SELECT * FROM sieve_scripts WHERE account_id = ? ORDER BY rowid ASC",
                (account_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM sieve_scripts ORDER BY rowid ASC").fetchall()
        return [
            SieveScript(
                id=r["id"],
                account_id=r["account_id"],
                name=r["name"],
                content=r["content"],
                active=bool(r["active"]),
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Demo / Seed Data Initialization
    # ------------------------------------------------------------------

    def seed_demo_data(self) -> None:
        """Populate the local store with realistic multi-account sample emails, threads, and filters."""
        if self.list_accounts():
            return

        work_acc = AccountConfig(
            id="acc_work",
            name="Schulz Engineering",
            email="kim@schulz.dk",
            full_name="Kim Schulz",
            imap_host="imap.schulz.dk",
            imap_port=993,
            imap_security=SecurityType.SSL_TLS,
            smtp_host="smtp.schulz.dk",
            smtp_port=587,
            smtp_security=SecurityType.STARTTLS,
            sieve_host="imap.schulz.dk",
            sieve_port=4190,
            pgp_key_id="0x9F4B8E21C0FFEE01",
            signature="-- \nKim Schulz | Principal Systems Architect\nhttps://schulz.dk",
            color_badge="#00E676",
        )
        oss_acc = AccountConfig(
            id="acc_oss",
            name="Espresso Open Source",
            email="maintainers@espressotui.dev",
            full_name="Kim Schulz (Espresso TUI)",
            imap_host="imap.fastmail.com",
            imap_port=993,
            imap_security=SecurityType.SSL_TLS,
            smtp_host="smtp.fastmail.com",
            smtp_port=587,
            smtp_security=SecurityType.STARTTLS,
            sieve_host="imap.fastmail.com",
            sieve_port=4190,
            pgp_key_id="0xA11CE550CAFEBEEF",
            signature="-- \nEspresso TUI Core Team ☕\nhttps://github.com/kimusan/espresso",
            color_badge="#FF9E64",
        )
        self.save_account(work_acc)
        self.save_account(oss_acc)

        sample_ppm = create_sample_ppm_image(36, 20)

        sample_messages = [
            # Thread 1: 3-message conversation on Espresso v1.2 Release & Sixel Rendering
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
                recipients_cc=["dev@espressotui.dev"],
                date_iso="2026-10-27T09:15:00+00:00",
                is_read=True,
                is_starred=True,
                pgp_status=PGPStatus.SIGNED_VERIFIED,
                pgp_key_fingerprint="4E81 90BC 221F 773A 9910  C0FF EE12 8841 7A09 331B",
                pgp_signer="Elena Rostova <elena@terminalcraft.io>",
                body_text=(
                    "Hi Kim,\n\n"
                    "I finished running the Sixel, Kitty, and Chafa rasterization benchmarks across "
                    "Ghostty, Kitty, WezTerm, and Foot. Frame latency stayed below 4.2ms even on 4K viewports.\n\n"
                    "Key highlights from the benchmark suite:\n"
                    "  * DEC Sixel quantization: 2.8ms average frame encode\n"
                    "  * Kitty APC base64 chunks: 1.4ms zero-flicker placement\n"
                    "  * 24-bit Unicode half-block fallback: 0.6ms pure ANSI\n\n"
                    "I have attached the coffee mascot raster preview (.ppm) and benchmark report.\n\n"
                    "Best regards,\n"
                    "Elena"
                ),
                attachments=[
                    Attachment(
                        id="att_mascot_ppm",
                        message_id="msg_thread1_1",
                        filename="espresso_mascot.ppm",
                        content_type="image/x-portable-pixmap",
                        size_bytes=len(sample_ppm),
                        is_inline=True,
                        data=sample_ppm,
                    ),
                    Attachment(
                        id="att_bench_json",
                        message_id="msg_thread1_1",
                        filename="graphics_benchmark_v1.2.json",
                        content_type="application/json",
                        size_bytes=248,
                        data=b'{"sixel_ms": 2.8, "kitty_ms": 1.4, "half_block_ms": 0.6, "fps_cap": 60}\n',
                    ),
                ],
            ),
            EmailMessage(
                id="msg_thread1_2",
                account_id="acc_oss",
                folder="Sent",
                uid=102,
                message_id="<release-1.2-reply1@espressotui.dev>",
                in_reply_to="<release-1.2-plan@espressotui.dev>",
                references=["<release-1.2-plan@espressotui.dev>"],
                thread_id="thread_espresso_12",
                subject="Re: [EspressoTUI] Release v1.2.0 checklist & terminal graphics benchmark",
                sender_name="Kim Schulz",
                sender_email="maintainers@espressotui.dev",
                recipients_to=["elena@terminalcraft.io"],
                recipients_cc=["dev@espressotui.dev"],
                date_iso="2026-10-27T10:02:00+00:00",
                is_read=True,
                pgp_status=PGPStatus.SIGNED_VERIFIED,
                pgp_key_fingerprint="A11C E550 CAFE BEEF 9912  0011 2233 4455 6677 8899",
                pgp_signer="Kim Schulz <maintainers@espressotui.dev>",
                body_text=(
                    "> I finished running the Sixel, Kitty, and Chafa rasterization benchmarks across\n"
                    "> Ghostty, Kitty, WezTerm, and Foot. Frame latency stayed below 4.2ms.\n\n"
                    "Awesome work Elena! Those numbers look rock solid.\n\n"
                    "Let's make sure EspressoMail's reading pane lets users toggle between Chafa, Sixel, "
                    "Kitty, Half-Block, and Braille on the fly with `i` so everyone gets crisp inline previews."
                ),
            ),
            EmailMessage(
                id="msg_thread1_3",
                account_id="acc_oss",
                folder="INBOX",
                uid=103,
                message_id="<release-1.2-reply2@espressotui.dev>",
                in_reply_to="<release-1.2-reply1@espressotui.dev>",
                references=[
                    "<release-1.2-plan@espressotui.dev>",
                    "<release-1.2-reply1@espressotui.dev>",
                ],
                thread_id="thread_espresso_12",
                subject="Re: [EspressoTUI] Release v1.2.0 checklist & terminal graphics benchmark",
                sender_name="Elena Rostova",
                sender_email="elena@terminalcraft.io",
                recipients_to=["maintainers@espressotui.dev"],
                date_iso="2026-10-27T11:30:00+00:00",
                is_read=False,
                is_starred=True,
                pgp_status=PGPStatus.SIGNED_VERIFIED,
                pgp_key_fingerprint="4E81 90BC 221F 773A 9910  C0FF EE12 8841 7A09 331B",
                pgp_signer="Elena Rostova <elena@terminalcraft.io>",
                body_text=(
                    "> Let's make sure EspressoMail's reading pane lets users toggle between Chafa, Sixel,\n"
                    "> Kitty, Half-Block, and Braille on the fly with `i`.\n\n"
                    "Done! I also verified that pressing `w` saves attachments straight to ~/Downloads "
                    "and updates the local SQLite store metadata."
                ),
            ),
            # Thread 2: Work account security & PGP encrypted infrastructure audit
            EmailMessage(
                id="msg_work_pgp",
                account_id="acc_work",
                folder="INBOX",
                uid=201,
                message_id="<sec-audit-2026@schulz.dk>",
                thread_id="thread_sec_audit",
                subject="[PGP-ENCRYPTED] Production WireGuard & Nftables Mesh Key Rotation",
                sender_name="Marcus Vance (SecOps)",
                sender_email="marcus@cyberguard.dk",
                recipients_to=["kim@schulz.dk"],
                date_iso="2026-10-27T12:45:00+00:00",
                is_read=False,
                is_starred=True,
                pgp_status=PGPStatus.DECRYPTED_VERIFIED,
                pgp_key_fingerprint="7F00 1122 3344 5566 7788  99AA BBCC DDEE FF00 4242",
                pgp_signer="Marcus Vance <marcus@cyberguard.dk>",
                body_text=(
                    "# Production Mesh Key Rotation Summary\n\n"
                    "All 14 edge nodes in the Copenhagen and Frankfurt clusters have rotated their "
                    "WireGuard preshared keys and updated `nftables` ingress sets.\n\n"
                    "```bash\n"
                    "nft list ruleset | grep -A 5 'chain inbound_mesh'\n"
                    "wg show wg0 latest-handshakes\n"
                    "```\n\n"
                    "**Action Required:**\n"
                    "1. Verify the detached PGP signature on the attached `mesh_peers_2026.conf`.\n"
                    "2. Approve the Sieve filter rule routing `[SecOps]` alerts to high-priority."
                ),
                attachments=[
                    Attachment(
                        id="att_wg_conf",
                        message_id="msg_work_pgp",
                        filename="mesh_peers_2026.conf",
                        content_type="text/plain",
                        size_bytes=168,
                        data=b"[Interface]\nListenPort = 51820\n\n[Peer]\nPublicKey = dGhpcyBpcyBhIHNhbXBsZSB3aXJlZ3VhcmQga2V5Cg==\nAllowedIPs = 10.42.0.0/16\n",
                    )
                ],
            ),
            # Thread 3: Work account ManageSieve & IMAP server migration note
            EmailMessage(
                id="msg_work_sieve",
                account_id="acc_work",
                folder="INBOX",
                uid=202,
                message_id="<dovecot-sieve-upgrade@schulz.dk>",
                thread_id="thread_sieve_upgrade",
                subject="Dovecot ManageSieve RFC 5804 endpoint live on port 4190",
                sender_name="Søren Lindqvist",
                sender_email="soren@infra.schulz.dk",
                recipients_to=["kim@schulz.dk"],
                date_iso="2026-10-26T17:20:00+00:00",
                is_read=True,
                is_starred=False,
                body_text=(
                    "Hey Kim,\n\n"
                    "The Dovecot Pigeonhole ManageSieve service is now active on `imap.schulz.dk:4190` "
                    "with STARTTLS and SASL PLAIN authentication.\n\n"
                    "You can manage your active filter rules directly from EspressoMail using the "
                    "Sieve Filters manager (`F` shortcut or Tools -> Sieve Filters)."
                ),
            ),
            # Thread 4: PyPI Packaging notification
            EmailMessage(
                id="msg_oss_pypi",
                account_id="acc_oss",
                folder="INBOX",
                uid=104,
                message_id="<pypi-publish-notify@pypi.org>",
                thread_id="thread_pypi_notify",
                subject="[PyPI] espressoTUI 1.2.0 published successfully",
                sender_name="PyPI Package Index",
                sender_email="noreply@pypi.org",
                recipients_to=["maintainers@espressotui.dev"],
                date_iso="2026-10-25T19:05:00+00:00",
                is_read=True,
                is_starred=False,
                body_text=(
                    "Your package `espressoTUI` version `1.2.0` has been published to PyPI.\n\n"
                    "Install command:\n"
                    "  pip install espressoTUI==1.2.0\n\n"
                    "SHA256 Wheel Digest: 8f9d2a1c4b7e6f0392a1b4c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8"
                ),
            ),
            # Draft message
            EmailMessage(
                id="msg_draft_1",
                account_id="acc_work",
                folder="Drafts",
                uid=301,
                message_id="<draft-arch-notes@schulz.dk>",
                thread_id="thread_draft_arch",
                subject="[Draft] Q4 Terminal Tooling & Zero-Dependency Architecture",
                sender_name="Kim Schulz",
                sender_email="kim@schulz.dk",
                recipients_to=["team@schulz.dk"],
                date_iso="2026-10-27T13:10:00+00:00",
                is_read=True,
                is_draft=True,
                body_text=(
                    "Team,\n\n"
                    "Here are the proposed milestones for Q4:\n"
                    "- Finalize EspressoMail 3-pane TUI & Unified Inbox\n"
                    "- Roll out automated GPG signing across all release tags\n"
                    "- Publish contributor documentation\n"
                ),
            ),
            # Archived message
            EmailMessage(
                id="msg_archive_1",
                account_id="acc_work",
                folder="Archive",
                uid=401,
                message_id="<archive-kernel-notes@schulz.dk>",
                thread_id="thread_archive_kernel",
                subject="Linux 6.12 LTS Deployment Sign-off",
                sender_name="Hannah Berg",
                sender_email="hannah@infra.schulz.dk",
                recipients_to=["kim@schulz.dk"],
                date_iso="2026-10-20T14:00:00+00:00",
                is_read=True,
                body_text="All production hypervisors have completed reboot into 6.12 LTS with zero regressions.",
            ),
        ]

        for m in sample_messages:
            self.save_message(m, harvest_contacts=True)

        # Enrich contacts with organizations and PGP fingerprints
        contacts = [
            Contact(
                id="contact_elena",
                name="Elena Rostova",
                email="elena@terminalcraft.io",
                organization="TerminalCraft Labs",
                notes="Core graphics & Sixel contributor",
                pgp_fingerprint="4E8190BC221F773A9910C0FFEE1288417A09331B",
                favorite=True,
                frequency=15,
            ),
            Contact(
                id="contact_marcus",
                name="Marcus Vance",
                email="marcus@cyberguard.dk",
                organization="CyberGuard Security",
                notes="Lead SecOps engineer",
                pgp_fingerprint="7F00112233445566778899AABBCCDDEEFF004242",
                favorite=True,
                frequency=9,
            ),
            Contact(
                id="contact_soren",
                name="Søren Lindqvist",
                email="soren@infra.schulz.dk",
                organization="Schulz Engineering",
                notes="Postmaster & Dovecot/Postfix admin",
                favorite=False,
                frequency=6,
            ),
        ]
        for c in contacts:
            self.save_contact(c)

        # Seed sample Sieve rules
        rules = [
            SieveFilterRule(
                id="rule_github_ci",
                account_id="acc_oss",
                name="Route GitHub Notifications",
                enabled=True,
                field="From",
                comparator="contains",
                pattern="notifications@github.com",
                action="fileinto",
                action_arg="Archive",
                stop_processing=True,
            ),
            SieveFilterRule(
                id="rule_secops_flag",
                account_id="acc_work",
                name="Flag SecOps Encrypted Alerts",
                enabled=True,
                field="Subject",
                comparator="contains",
                pattern="[PGP-ENCRYPTED]",
                action="flag",
                action_arg="\\Flagged",
                stop_processing=False,
            ),
            SieveFilterRule(
                id="rule_spam_discard",
                account_id="acc_work",
                name="Move Spam to Trash",
                enabled=True,
                field="X-Spam-Flag",
                comparator="is",
                pattern="YES",
                action="fileinto",
                action_arg="Trash",
                stop_processing=True,
            ),
        ]
        for r in rules:
            self.save_sieve_rule(r)

        self.refresh_folder_counts()
