"""SQLite local email store for offline access, caching, drafts, contacts, and settings."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Sequence

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
    classify_folder_type,
    split_folder_hierarchy,
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
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        import tempfile

        fallback = Path(tempfile.gettempdir()) / "espressomail"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


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

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._fts_enabled = False
        self._initialize_schema()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
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
                enabled INTEGER NOT NULL DEFAULT 1,
                carddav_url TEXT NOT NULL DEFAULT '',
                carddav_username TEXT NOT NULL DEFAULT '',
                carddav_password TEXT NOT NULL DEFAULT ''
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
                selectable INTEGER NOT NULL DEFAULT 1,
                subscribed INTEGER NOT NULL DEFAULT 1,
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
            CREATE INDEX IF NOT EXISTS idx_messages_unread
                ON messages(is_trashed, is_draft, is_read);
            CREATE INDEX IF NOT EXISTS idx_folders_acc_name
                ON folders(account_id, name);
            CREATE INDEX IF NOT EXISTS idx_folders_type
                ON folders(folder_type);

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

        try:
            cur.execute("ALTER TABLE folders ADD COLUMN selectable INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError:
            pass

        try:
            cur.execute("ALTER TABLE folders ADD COLUMN subscribed INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError:
            pass

        for col_def in (
            "carddav_url TEXT NOT NULL DEFAULT ''",
            "carddav_username TEXT NOT NULL DEFAULT ''",
            "carddav_password TEXT NOT NULL DEFAULT ''",
        ):
            try:
                cur.execute(f"ALTER TABLE accounts ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass

        # Purge any legacy unauthenticated demo accounts if present from an earlier session
        cur.execute(
            "DELETE FROM accounts WHERE id IN ('acc_work', 'acc_oss') AND imap_password = ''"
        )
        if self._fts_enabled:
            cur.execute(
                "DELETE FROM messages_fts WHERE msg_id NOT IN (SELECT id FROM messages)"
            )

        self._normalize_existing_folders()
        self._conn.commit()

    def _normalize_existing_folders(self) -> None:
        """Ensure nested subfolders (e.g. Archives/2025/Sent) have FolderType.CUSTOM and clean display names."""
        rows = self._conn.execute("SELECT id, account_id, name, display_name, folder_type FROM folders").fetchall()
        for r in rows:
            fname = r["name"]
            parts = split_folder_hierarchy(fname)
            if len(parts) > 1:
                new_type = FolderType.CUSTOM.value
                new_display = parts[-1]
            elif fname == "Starred":
                new_type = FolderType.STARRED.value
                new_display = "Starred"
            elif parts and parts[0].upper() == "INBOX":
                new_type = FolderType.INBOX.value
                new_display = "Inbox"
            else:
                inferred = classify_folder_type(parts)
                new_type = inferred.value if inferred != FolderType.CUSTOM else r["folder_type"]
                new_display = parts[-1] if parts else r["display_name"]

            if new_type != r["folder_type"] or new_display != r["display_name"]:
                self._conn.execute(
                    "UPDATE folders SET display_name = ?, folder_type = ? WHERE id = ?",
                    (new_display, new_type, r["id"]),
                )

        # Deduplicate empty default placeholders when another folder of the same system type exists
        placeholder_names = {"Sent", "Drafts", "Archive", "Trash"}
        updated_rows = self._conn.execute(
            "SELECT id, account_id, name, folder_type FROM folders"
        ).fetchall()
        by_acc_type: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for r in updated_rows:
            if r["folder_type"] in ("sent", "drafts", "archive", "trash"):
                by_acc_type.setdefault((r["account_id"], r["folder_type"]), []).append(r)

        for (acc_id, _ftype), group in by_acc_type.items():
            if len(group) <= 1:
                continue
            remaining = len(group)
            for r in group:
                if remaining <= 1:
                    break
                if r["name"] in placeholder_names:
                    cnt_row = self._conn.execute(
                        "SELECT COUNT(*) AS c FROM messages WHERE account_id = ? AND folder = ?",
                        (acc_id, r["name"]),
                    ).fetchone()
                    if int(cnt_row["c"] or 0) == 0:
                        self._conn.execute("DELETE FROM folders WHERE id = ?", (r["id"],))
                        remaining -= 1

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
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO accounts (
                    id, name, email, full_name,
                    imap_host, imap_port, imap_security, imap_username, imap_password,
                    smtp_host, smtp_port, smtp_security, smtp_username, smtp_password,
                    sieve_host, sieve_port, pgp_key_id, signature, color_badge, enabled,
                    carddav_url, carddav_username, carddav_password
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    enabled = excluded.enabled,
                    carddav_url = excluded.carddav_url,
                    carddav_username = excluded.carddav_username,
                    carddav_password = excluded.carddav_password
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
                    account.carddav_url,
                    account.carddav_username,
                    account.carddav_password,
                ),
            )
            self._ensure_default_folders(account.id)
            self._conn.commit()

    def _ensure_default_folders(self, account_id: str) -> None:
        """Create standard mailbox folders for an account if no folder of that type already exists."""
        existing_types = {
            r["folder_type"]
            for r in self._conn.execute(
                "SELECT folder_type FROM folders WHERE account_id = ?", (account_id,)
            ).fetchall()
        }
        defaults = [
            ("INBOX", "Inbox", FolderType.INBOX),
            ("Starred", "Starred", FolderType.STARRED),
            ("Sent", "Sent", FolderType.SENT),
            ("Drafts", "Drafts", FolderType.DRAFTS),
            ("Archive", "Archive", FolderType.ARCHIVE),
            ("Trash", "Trash", FolderType.TRASH),
        ]
        for name, display, ftype in defaults:
            if ftype.value in existing_types and name not in ("INBOX", "Starred"):
                continue
            fid = f"{account_id}:{name}"
            self._conn.execute(
                """
                INSERT OR IGNORE INTO folders (id, account_id, name, display_name, folder_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fid, account_id, name, display, ftype.value),
            )

    def sync_remote_folders(self, account_id: str, remote_folders: Sequence[Folder]) -> None:
        """Synchronize folders discovered from a remote IMAP server into the local store."""
        if not remote_folders:
            return
        with self._lock:
            remote_names = {f.name for f in remote_folders}
            for f in remote_folders:
                fid = f"{account_id}:{f.name}"
                self._conn.execute(
                    "DELETE FROM folders WHERE account_id = ? AND name = ? AND id != ?",
                    (account_id, f.name, fid),
                )
                self._conn.execute(
                    """
                    INSERT INTO folders (id, account_id, name, display_name, folder_type, selectable, subscribed)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        display_name = excluded.display_name,
                        folder_type = excluded.folder_type,
                        selectable = excluded.selectable
                    """,
                    (fid, account_id, f.name, f.display_name, f.folder_type.value, int(f.selectable), int(f.subscribed)),
                )

            # Remove empty offline-placeholder folders that do not exist on the remote server
            existing_rows = self._conn.execute(
                "SELECT id, name FROM folders WHERE account_id = ?",
                (account_id,),
            ).fetchall()
            for r in existing_rows:
                fname = r["name"]
                if fname in ("INBOX", "Starred") or fname in remote_names:
                    continue
                cnt_row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM messages WHERE account_id = ? AND folder = ?",
                    (account_id, fname),
                ).fetchone()
                if int(cnt_row["c"] or 0) == 0:
                    self._conn.execute("DELETE FROM folders WHERE id = ?", (r["id"],))
            self._normalize_existing_folders()
            self._conn.commit()
            self.refresh_folder_counts()

    def list_accounts(self, enabled_only: bool = False) -> list[AccountConfig]:
        """Return all configured email accounts."""
        with self._lock:
            query = "SELECT * FROM accounts"
            if enabled_only:
                query += " WHERE enabled = 1"
            query += " ORDER BY name ASC"
            rows = self._conn.execute(query).fetchall()
            return [self._row_to_account(r) for r in rows]

    def get_account(self, account_id: str) -> AccountConfig | None:
        """Return a specific account by ID."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            return self._row_to_account(row) if row else None

    def delete_account(self, account_id: str) -> None:
        """Remove an account and all associated messages/folders."""
        with self._lock:
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
            carddav_url=str(row["carddav_url"] if "carddav_url" in row.keys() else ""),
            carddav_username=str(row["carddav_username"] if "carddav_username" in row.keys() else ""),
            carddav_password=str(row["carddav_password"] if "carddav_password" in row.keys() else ""),
        )

    def list_folders(self, account_id: str | None = None) -> list[Folder]:
        """List folders with live unread and total message counts."""
        with self._lock:
            self.refresh_folder_counts()
            if account_id:
                rows = self._conn.execute(
                    "SELECT * FROM folders WHERE account_id = ? ORDER BY rowid ASC",
                    (account_id,),
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM folders ORDER BY account_id, rowid ASC").fetchall()
            folders = [
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
                    selectable=bool(r["selectable"]) if "selectable" in r.keys() else True,
                    subscribed=bool(r["subscribed"]) if "subscribed" in r.keys() else True,
                )
                for r in rows
            ]
            type_order = {
                FolderType.INBOX: 0,
                FolderType.STARRED: 1,
                FolderType.SENT: 2,
                FolderType.DRAFTS: 3,
                FolderType.ARCHIVE: 4,
                FolderType.SPAM: 5,
                FolderType.TRASH: 6,
                FolderType.CUSTOM: 7,
            }
            folders.sort(
                key=lambda f: (
                    f.account_id,
                    type_order.get(f.folder_type, 7),
                    0 if f.name.upper() == "INBOX" else 1,
                )
            )
            return folders

    def get_folder(self, account_id: str, name: str) -> Folder | None:
        """Retrieve a specific folder for an account."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM folders WHERE account_id = ? AND name = ?",
                (account_id, name),
            ).fetchone()
            if not row:
                return None
            return Folder(
                id=row["id"],
                account_id=row["account_id"],
                name=row["name"],
                display_name=row["display_name"],
                folder_type=FolderType(row["folder_type"]),
                unread_count=int(row["unread_count"]),
                total_count=int(row["total_count"]),
                uid_validity=int(row["uid_validity"]),
                last_uid=int(row["last_uid"]),
                selectable=bool(row["selectable"]) if "selectable" in row.keys() else True,
                subscribed=bool(row["subscribed"]) if "subscribed" in row.keys() else True,
            )

    def set_folder_subscribed(self, account_id: str, folder_name: str, subscribed: bool) -> None:
        """Update the subscribed state of a folder."""
        with self._lock:
            self._conn.execute(
                "UPDATE folders SET subscribed = ? WHERE account_id = ? AND name = ?",
                (int(subscribed), account_id, folder_name),
            )
            self._conn.commit()

    def toggle_folder_subscription(self, account_id: str, folder_name: str) -> bool:
        """Toggle folder subscription state and return the new state."""
        with self._lock:
            row = self._conn.execute(
                "SELECT subscribed FROM folders WHERE account_id = ? AND name = ?",
                (account_id, folder_name),
            ).fetchone()
            if not row:
                return True
            curr = bool(row["subscribed"]) if "subscribed" in row.keys() else True
            new_val = not curr
            self._conn.execute(
                "UPDATE folders SET subscribed = ? WHERE account_id = ? AND name = ?",
                (int(new_val), account_id, folder_name),
            )
            self._conn.commit()
            return new_val

    def get_unified_counts(self) -> dict[str, tuple[int, int]]:
        """Return (unread_count, total_count) for Unified virtual folders across all accounts."""
        with self._lock:
            result: dict[str, tuple[int, int]] = {}
            # Unified Inbox
            row = self._conn.execute(
                "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
                "FROM messages WHERE (folder = 'INBOX' OR (account_id, folder) IN "
                "(SELECT account_id, name FROM folders WHERE folder_type = 'inbox')) "
                "AND is_trashed = 0 AND is_draft = 0"
            ).fetchone()
            result["INBOX"] = (int(row["u"] or 0), int(row["t"] or 0))

            # Unified All Unread
            row = self._conn.execute(
                "SELECT COUNT(*) AS u FROM messages WHERE is_read = 0 AND is_trashed = 0 AND is_draft = 0"
            ).fetchone()
            u_all = int(row["u"] or 0)
            result["All Unread"] = (u_all, u_all)

            # Unified Starred
            row = self._conn.execute(
                "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
                "FROM messages WHERE is_starred = 1 AND is_trashed = 0"
            ).fetchone()
            result["Starred"] = (int(row["u"] or 0), int(row["t"] or 0))

            # Unified Drafts
            row = self._conn.execute(
                "SELECT COUNT(*) AS t FROM messages WHERE (is_draft = 1 OR folder = 'Drafts' "
                "OR (account_id, folder) IN (SELECT account_id, name FROM folders WHERE folder_type = 'drafts')) "
                "AND is_trashed = 0"
            ).fetchone()
            draft_cnt = int(row["t"] or 0)
            result["Drafts"] = (draft_cnt, draft_cnt)

            # Unified Sent
            row = self._conn.execute(
                "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
                "FROM messages WHERE (folder = 'Sent' OR (account_id, folder) IN "
                "(SELECT account_id, name FROM folders WHERE folder_type = 'sent')) "
                "AND is_trashed = 0"
            ).fetchone()
            result["Sent"] = (int(row["u"] or 0), int(row["t"] or 0))

            # Unified Archive
            row = self._conn.execute(
                "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
                "FROM messages WHERE (folder = 'Archive' OR (account_id, folder) IN "
                "(SELECT account_id, name FROM folders WHERE folder_type = 'archive')) "
                "AND is_trashed = 0"
            ).fetchone()
            result["Archive"] = (int(row["u"] or 0), int(row["t"] or 0))

            # Unified Trash
            row = self._conn.execute(
                "SELECT SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS u, COUNT(*) AS t "
                "FROM messages WHERE is_trashed = 1 OR folder = 'Trash' "
                "OR (account_id, folder) IN (SELECT account_id, name FROM folders WHERE folder_type = 'trash')"
            ).fetchone()
            result["Trash"] = (int(row["u"] or 0), int(row["t"] or 0))

            return result

    def refresh_folder_counts(self, account_id: str | None = None, folder_name: str | None = None) -> None:
        """Recalculate unread and total message counts for all folders or a specific folder."""
        with self._lock:
            with self._conn:
                if account_id and folder_name:
                    folders = self._conn.execute(
                        "SELECT id, account_id, name, folder_type FROM folders WHERE account_id = ? AND name = ?",
                        (account_id, folder_name),
                    ).fetchall()
                elif account_id:
                    folders = self._conn.execute(
                        "SELECT id, account_id, name, folder_type FROM folders WHERE account_id = ?",
                        (account_id,),
                    ).fetchall()
                else:
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
                            "FROM messages WHERE account_id = ? AND (is_trashed = 1 OR folder = ?)",
                            (acc_id, fname),
                        ).fetchone()
                    elif ftype == FolderType.DRAFTS.value or fname == "Drafts":
                        row = self._conn.execute(
                            "SELECT COUNT(*) AS u, COUNT(*) AS t "
                            "FROM messages WHERE account_id = ? AND (is_draft = 1 OR folder = ?) AND is_trashed = 0",
                            (acc_id, fname),
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

    def get_folder_min_uid(self, account_id: str, folder: str) -> int:
        """Return the minimum IMAP UID stored locally for an account and folder, or 0 if none."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(uid) AS min_u FROM messages WHERE account_id = ? AND folder = ? AND uid > 0 AND is_trashed = 0",
                (account_id, folder),
            ).fetchone()
            if row and row["min_u"] is not None:
                return int(row["min_u"])
            return 0

    # ------------------------------------------------------------------
    # Messages & Attachments CRUD
    # ------------------------------------------------------------------

    def _save_message_in_tx(self, msg: EmailMessage, harvest_contacts: bool = True) -> None:
        """Internal helper to insert or update an EmailMessage and its attachments in active transaction."""
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

    def save_message(self, msg: EmailMessage, harvest_contacts: bool = True) -> None:
        """Insert or update an EmailMessage and its attachments in the local store."""
        with self._lock:
            self._save_message_in_tx(msg, harvest_contacts=harvest_contacts)
            self._conn.commit()

    def save_messages(self, messages: Sequence[EmailMessage], harvest_contacts: bool = True) -> int:
        """Insert or update a batch of EmailMessages within a single transaction."""
        if not messages:
            return 0
        with self._lock:
            with self._conn:
                for msg in messages:
                    self._save_message_in_tx(msg, harvest_contacts=harvest_contacts)
            return len(messages)

    def get_message(self, msg_id: str) -> EmailMessage | None:
        """Retrieve a single EmailMessage (including attachments) by ID."""
        with self._lock:
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
        with self._lock:
            params: list[Any] = []
            where_clauses: list[str] = []

            is_unified = not account_id or account_id == "*"
            if not is_unified:
                where_clauses.append("account_id = ?")
                params.append(account_id)

            folder_upper = folder.upper()
            if folder_upper in ("UNREAD", "ALL_UNREAD", "ALL UNREAD"):
                where_clauses.append("is_read = 0")
                where_clauses.append("is_trashed = 0")
                where_clauses.append("is_draft = 0")
            elif folder_upper == "STARRED":
                where_clauses.append("is_starred = 1")
                where_clauses.append("is_trashed = 0")
            elif folder_upper == "ALL":
                if not include_trashed:
                    where_clauses.append("is_trashed = 0")
            elif is_unified:
                if folder_upper == "INBOX":
                    where_clauses.append(
                        "(folder = 'INBOX' OR (account_id, folder) IN "
                        "(SELECT account_id, name FROM folders WHERE folder_type = 'inbox'))"
                    )
                    where_clauses.append("is_trashed = 0")
                elif folder_upper == "TRASH":
                    where_clauses.append(
                        "(is_trashed = 1 OR folder = 'Trash' OR (account_id, folder) IN "
                        "(SELECT account_id, name FROM folders WHERE folder_type = 'trash'))"
                    )
                elif folder_upper == "DRAFTS":
                    where_clauses.append(
                        "(is_draft = 1 OR folder = 'Drafts' OR (account_id, folder) IN "
                        "(SELECT account_id, name FROM folders WHERE folder_type = 'drafts'))"
                    )
                    where_clauses.append("is_trashed = 0")
                elif folder_upper == "SENT":
                    where_clauses.append(
                        "(folder = 'Sent' OR (account_id, folder) IN "
                        "(SELECT account_id, name FROM folders WHERE folder_type = 'sent'))"
                    )
                    where_clauses.append("is_trashed = 0")
                elif folder_upper == "ARCHIVE":
                    where_clauses.append(
                        "(folder = 'Archive' OR (account_id, folder) IN "
                        "(SELECT account_id, name FROM folders WHERE folder_type = 'archive'))"
                    )
                    where_clauses.append("is_trashed = 0")
                else:
                    where_clauses.append("folder = ?")
                    params.append(folder)
                    if not include_trashed:
                        where_clauses.append("is_trashed = 0")
            else:
                f_row = self._conn.execute(
                    "SELECT folder_type FROM folders WHERE account_id = ? AND name = ?",
                    (account_id, folder),
                ).fetchone()
                ftype = f_row["folder_type"] if f_row else ""
                if folder_upper == "TRASH" or ftype == FolderType.TRASH.value:
                    where_clauses.append("(is_trashed = 1 OR folder = ?)")
                    params.append(folder)
                elif folder_upper == "DRAFTS" or ftype == FolderType.DRAFTS.value:
                    where_clauses.append("(is_draft = 1 OR folder = ?)")
                    params.append(folder)
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
        with self._lock:
            row = self._conn.execute(
                "SELECT account_id, folder, is_read FROM messages WHERE id = ?",
                (msg_id,),
            ).fetchone()
            if not row:
                return
            if bool(row["is_read"]) == is_read:
                return
            self._conn.execute("UPDATE messages SET is_read = ? WHERE id = ?", (int(is_read), msg_id))
            acc_id = row["account_id"]
            fname = row["folder"]
            diff = -1 if is_read else 1
            self._conn.execute(
                "UPDATE folders SET unread_count = MAX(0, unread_count + ?) WHERE account_id = ? AND name = ?",
                (diff, acc_id, fname),
            )
            self._conn.commit()

    def toggle_star(self, msg_id: str) -> bool:
        """Toggle the starred flag on a message and return the new state."""
        with self._lock:
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

    def get_contact_by_email(self, email: str) -> Contact | None:
        """Find a contact by email address."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM contacts WHERE lower(email) = ?", (email.strip().lower(),)
            ).fetchone()
            if not row:
                return None
            return Contact(
                id=row["id"],
                name=row["name"],
                email=row["email"],
                organization=row["organization"],
                notes=row["notes"],
                pgp_fingerprint=row["pgp_fingerprint"],
                favorite=bool(row["favorite"]),
                frequency=int(row["frequency"]),
            )

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
        with self._lock:
            if script.active:
                self._conn.execute(
                    "UPDATE sieve_scripts SET active = 0 WHERE account_id = ?",
                    (script.account_id,),
                )
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
        with self._lock:
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

    def get_active_sieve_script(self, account_id: str | None = None) -> SieveScript | None:
        """Return the active Sieve script for an account, or the first available script."""
        with self._lock:
            if account_id and account_id != "*":
                row = self._conn.execute(
                    "SELECT * FROM sieve_scripts WHERE account_id = ? AND active = 1 ORDER BY rowid DESC LIMIT 1",
                    (account_id,),
                ).fetchone()
                if not row:
                    row = self._conn.execute(
                        "SELECT * FROM sieve_scripts WHERE account_id = ? ORDER BY rowid DESC LIMIT 1",
                        (account_id,),
                    ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM sieve_scripts WHERE active = 1 ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                if not row:
                    row = self._conn.execute(
                        "SELECT * FROM sieve_scripts ORDER BY rowid DESC LIMIT 1"
                    ).fetchone()
            if not row:
                return None
            return SieveScript(
                id=row["id"],
                account_id=row["account_id"],
                name=row["name"],
                content=row["content"],
                active=bool(row["active"]),
                updated_at=row["updated_at"],
            )

