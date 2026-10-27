"""Main 3-pane Espresso TEA application model for EspressoMail."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from espresso import (
    Cmd,
    KeyMsg,
    Model,
    MouseAction,
    MouseButton,
    MouseMsg,
    Msg,
    WindowSizeMsg,
    quit_app,
)
from espresso.beans import (
    CommandPalette,
    CommandPaletteCloseMsg,
    CommandPaletteSelectMsg,
    PaletteItem,
    StatusBar,
    StatusSection,
    ToastLevel,
    ToastManager,
    Viewport,
)
from espresso.crema import (
    Align,
    Style,
    join_horizontal,
    join_vertical,
    place_overlay,
    string_width,
    truncate_ansi,
)

from espressomail.crypto.pgp import PGPManager
from espressomail.models.entities import (
    AccountConfig,
    EmailMessage,
    EmailThread,
    Folder,
    FolderType,
    PGPStatus,
    classify_folder_type,
    split_folder_hierarchy,
)
from espressomail.protocols.imap_client import IMAPClient
from espressomail.protocols.smtp_client import (
    SMTPClient,
    build_mime_message,
    validate_account_credentials,
)
from espressomail.protocols.threading import build_threads
from espressomail.storage.database import LocalStore, default_data_dir
from espressomail.themes.engine import Theme, ThemeEngine
from espressomail.ui.formatter import format_message_for_reading_pane
from espressomail.ui.image_renderer import ImageRenderBackend, TerminalImageRenderer
from espressomail.ui.menus import MenuActionMsg, MenuBar
from espressomail.ui.modals import (
    AccountSettingsModal,
    AddressBookModal,
    ComposeModal,
    ComposeSubmittedMsg,
    ComposeToContactMsg,
    HelpModal,
    ModalClosedMsg,
    PGPModal,
    SearchModal,
    SearchSelectMessageMsg,
    SieveModal,
)
from espressomail.ui.onboarding import OnboardingCompleteMsg, OnboardingWizard
from espressomail.ui.splash import SplashDoneMsg, SplashScreen


@dataclass(frozen=True)
class FolderTreeRow:
    """Represents a navigable row in Pane 1 (Account / Folder Tree)."""

    account_id: str  # "*" for Unified folders
    folder_name: str
    label: str
    icon: str
    unread_count: int = 0
    total_count: int = 0
    is_header: bool = False
    badge_color: str = ""
    depth: int = 0
    has_children: bool = False
    is_expanded: bool = True
    is_last_sibling: bool = False
    tree_key: str = ""
    selectable_mailbox: bool = True


@dataclass
class _FolderTreeNode:
    """Internal hierarchy node used to construct collapsible folder trees per account."""

    segment: str
    path_parts: tuple[str, ...]
    folder: Folder | None = None
    children: dict[str, _FolderTreeNode] = field(default_factory=dict)


@dataclass(frozen=True)
class MessageListRow:
    """Represents a navigable row in Pane 2 (Message / Thread List)."""

    message: EmailMessage
    thread: EmailThread
    depth: int = 0
    is_thread_root: bool = True


@dataclass(frozen=True)
class SyncCompletedMsg(Msg):
    """Emitted when background IMAP synchronization completes."""

    synced_count: int
    summary: str


class EspressoMailApp(Model):
    """Root TEA Model orchestrating the 3-pane layout, splash, onboarding, menus, and modals."""

    def __init__(
        self,
        store: LocalStore | None = None,
        show_splash: bool = True,
        initial_theme: str = "",
        download_dir: str | Path | None = None,
        credential_validator: Callable[[AccountConfig], tuple[bool, str]] | None = None,
    ) -> None:
        self.store = store if store is not None else LocalStore()
        self.credential_validator = credential_validator or validate_account_credentials

        saved_theme = initial_theme or self.store.get_setting("theme", "espresso")
        self.theme_engine = ThemeEngine(saved_theme)

        saved_img_mode = self.store.get_setting("image_backend", "auto")
        self.image_renderer = TerminalImageRenderer(saved_img_mode)

        self.pgp = PGPManager()
        self.toasts = ToastManager()
        self.menu_bar = MenuBar()

        self.width = 120
        self.height = 34
        self.download_dir = (
            Path(download_dir).expanduser().resolve()
            if download_dir
            else (default_data_dir() / "downloads")
        )

        # Startup & Onboarding state
        self.show_splash = show_splash
        self.splash = SplashScreen(self.theme)
        self.show_onboarding = False
        self.onboarding = OnboardingWizard(
            self.theme,
            allow_cancel=False,
            credential_validator=self.credential_validator,
        )

        # Active 3-pane state
        self.focused_pane = "messages"  # "folders", "messages", "reader"
        self.threading_enabled = self.store.get_setting("threading", "1") == "1"
        self.show_raw_headers = False
        self.status_message = "Ready. Press ? for shortcuts, / to search, c to compose."

        self.folder_rows: list[FolderTreeRow] = []
        self.folder_cursor = 0
        self.folder_scroll = 0
        self.collapsed_folders: set[str] = set()
        self.active_account_id = "*"
        self.active_folder = "INBOX"

        self.message_rows: list[MessageListRow] = []
        self.message_cursor = 0
        self.message_scroll = 0

        self.reader_viewport = Viewport(width=50, height=24)
        self._last_rendered_msg_key = ""

        # Active modal overlay
        self.active_modal: Model | None = None

        # Command Palette (Ctrl+P)
        self.command_palette = self._build_command_palette()

        self.refresh_data()
        if not self.show_splash and not self.store.list_accounts():
            self.show_onboarding = True

    @property
    def theme(self) -> Theme:
        return self.theme_engine.current

    def _build_command_palette(self) -> CommandPalette:
        items = [
            PaletteItem("compose", "Mail: Compose New Message", "Open email composer", "c", "Mail"),
            PaletteItem("reply", "Mail: Reply to Sender", "Reply to selected message", "r", "Mail"),
            PaletteItem("reply_all", "Mail: Reply All", "Reply to all recipients", "R", "Mail"),
            PaletteItem("forward", "Mail: Forward Message", "Forward selected message", "f", "Mail"),
            PaletteItem("fuzzy_search", "Search: Fuzzy Search Local Store", "Instant search across all emails", "/", "Search"),
            PaletteItem("unified_inbox", "View: Unified Inbox", "Aggregate Inbox across all accounts", "U", "View"),
            PaletteItem("toggle_threading", "View: Toggle Message Threading", "Group conversations into threads", "t", "View"),
            PaletteItem("cycle_theme", "Theme: Cycle Color Scheme", "Switch terminal color palette", "T", "Theme"),
            PaletteItem("cycle_image_mode", "View: Cycle Image Renderer", "Switch Chafa / Sixel / Kitty / Half-Block", "i", "View"),
            PaletteItem("save_attachments", "Mail: Download Attachments", "Save attachments to downloads directory", "w", "Mail"),
            PaletteItem("address_book", "Tools: Local Address Book", "Manage contacts and PGP fingerprints", "B", "Tools"),
            PaletteItem("sieve_filters", "Tools: ManageSieve Filter Rules", "Configure RFC 5228 Sieve scripts", "F", "Tools"),
            PaletteItem("pgp_manager", "Security: GPG/PGP Keyring Manager", "Inspect keys, sign, verify, and decrypt", "G", "Security"),
            PaletteItem("account_settings", "Accounts: Edit Account Settings", "Update password, IMAP/SMTP hosts, ports, and TLS", "A", "Accounts"),
            PaletteItem("new_account", "Accounts: Add New Account", "Launch onboarding wizard for a new account", "O", "Accounts"),
            PaletteItem("sync_all", "Accounts: Sync IMAP Mailboxes", "Synchronize all enabled accounts", "S", "Accounts"),
            PaletteItem("empty_trash", "Mail: Empty Trash", "Permanently delete trashed messages", "X", "Mail"),
            PaletteItem("show_shortcuts", "Help: Keyboard & Mouse Shortcuts", "Display full hotkey guide", "?", "Help"),
            PaletteItem("quit_app", "Application: Quit EspressoMail", "Exit cleanly", "q", "Application"),
        ]
        return CommandPalette(
            items=items,
            width=68,
            max_height=14,
            placeholder="Type a command or action...",
            is_open=False,
            toggle_key="ctrl+p",
        )

    def init(self) -> Cmd | None:
        if self.show_splash:
            return self.splash.init()
        if any(acc.imap_password for acc in self.store.list_accounts(enabled_only=True)):
            self.status_message = "Synchronizing IMAP accounts..."
            return self._sync_accounts_cmd
        return None

    # ------------------------------------------------------------------
    # Data Refresh & Layout Calculations
    # ------------------------------------------------------------------

    def refresh_data(self) -> None:
        """Rebuild folder tree rows, message/thread rows, and reading pane content."""
        self._rebuild_folder_rows()
        self._rebuild_message_rows()
        self._update_reader_viewport(force=True)

    def _build_account_folder_tree_rows(
        self, account_id: str, folders: list[Folder]
    ) -> list[FolderTreeRow]:
        """Build a hierarchical, collapsible folder tree for a single account."""
        roots: dict[str, _FolderTreeNode] = {}

        for f in folders:
            parts = tuple(split_folder_hierarchy(f.name))
            if not parts:
                continue
            curr_map = roots
            curr_node: _FolderTreeNode | None = None
            for idx, seg in enumerate(parts):
                key = seg.lower()
                prefix = parts[: idx + 1]
                if key not in curr_map:
                    curr_map[key] = _FolderTreeNode(segment=seg, path_parts=prefix)
                curr_node = curr_map[key]
                curr_map = curr_node.children

            if curr_node is not None:
                if curr_node.folder is None or f.total_count > curr_node.folder.total_count:
                    curr_node.folder = f

        def _subtree_counts(node: _FolderTreeNode) -> tuple[int, int]:
            u = node.folder.unread_count if node.folder else 0
            t = node.folder.total_count if node.folder else 0
            for child in node.children.values():
                cu, ct = _subtree_counts(child)
                u += cu
                t += ct
            return u, t

        def _node_folder_type(node: _FolderTreeNode, depth: int) -> FolderType:
            if depth > 0:
                return FolderType.CUSTOM
            if node.folder is not None:
                return node.folder.folder_type
            return classify_folder_type(node.path_parts)

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

        def _child_sort_key(node: _FolderTreeNode) -> tuple[int, int, str]:
            seg = node.segment.strip()
            if seg.isdigit():
                return (0, -int(seg), "")
            if seg.lower() == "inbox":
                return (1, 0, "inbox")
            return (2, 0, seg.lower())

        out_rows: list[FolderTreeRow] = []

        def _emit(node: _FolderTreeNode, depth: int, is_last: bool) -> None:
            tree_key = f"{account_id}:{'/'.join(node.path_parts)}"
            has_children = bool(node.children)
            is_expanded = tree_key not in self.collapsed_folders
            sub_u, sub_t = _subtree_counts(node)

            if (
                node.folder is not None
                and is_expanded
                and (node.folder.unread_count > 0 or node.folder.total_count > 0 or not has_children)
            ):
                u_cnt, t_cnt = node.folder.unread_count, node.folder.total_count
            else:
                u_cnt, t_cnt = sub_u, sub_t

            if depth == 0:
                ftype = _node_folder_type(node, 0)
                if node.folder is not None:
                    label = node.folder.display_name
                    icon = node.folder.icon
                else:
                    label = "Inbox" if ftype == FolderType.INBOX else node.segment
                    icon = Folder(
                        id="",
                        account_id=account_id,
                        name=node.segment,
                        display_name=label,
                        folder_type=ftype,
                    ).icon
            else:
                label = node.segment
                icon = ("📂" if is_expanded else "📁") if has_children else "📁"

            folder_name = node.folder.name if node.folder is not None else "/".join(node.path_parts)
            selectable_mailbox = node.folder is not None

            out_rows.append(
                FolderTreeRow(
                    account_id=account_id,
                    folder_name=folder_name,
                    label=label,
                    icon=icon,
                    unread_count=u_cnt,
                    total_count=t_cnt,
                    depth=depth,
                    has_children=has_children,
                    is_expanded=is_expanded,
                    is_last_sibling=is_last,
                    tree_key=tree_key,
                    selectable_mailbox=selectable_mailbox,
                )
            )

            if has_children and is_expanded:
                sorted_children = sorted(node.children.values(), key=_child_sort_key)
                for idx, child in enumerate(sorted_children):
                    _emit(child, depth + 1, idx == len(sorted_children) - 1)

        sorted_roots = sorted(
            roots.values(),
            key=lambda n: (type_order.get(_node_folder_type(n, 0), 7), n.segment.lower()),
        )
        for idx, root_node in enumerate(sorted_roots):
            _emit(root_node, 0, idx == len(sorted_roots) - 1)

        return out_rows

    def _rebuild_folder_rows(self) -> None:
        rows: list[FolderTreeRow] = []
        unified_counts = self.store.get_unified_counts()

        rows.append(
            FolderTreeRow(
                account_id="*",
                folder_name="",
                label="UNIFIED MAILBOXES",
                icon="🌐",
                is_header=True,
            )
        )
        unified_defs = [
            ("INBOX", "Unified Inbox", "📥"),
            ("Starred", "All Starred", "⭐"),
            ("Drafts", "All Drafts", "📝"),
            ("Sent", "All Sent", "📤"),
            ("Archive", "All Archive", "📦"),
            ("Trash", "All Trash", "🗑️"),
        ]
        for fname, label, icon in unified_defs:
            u_cnt, t_cnt = unified_counts.get(fname, (0, 0))
            rows.append(
                FolderTreeRow(
                    account_id="*",
                    folder_name=fname,
                    label=label,
                    icon=icon,
                    unread_count=u_cnt,
                    total_count=t_cnt,
                )
            )

        accounts = self.store.list_accounts()
        for acc in accounts:
            rows.append(
                FolderTreeRow(
                    account_id=acc.id,
                    folder_name="",
                    label=acc.name.upper(),
                    icon="☕",
                    is_header=True,
                    badge_color=acc.color_badge,
                )
            )
            acc_folders = self.store.list_folders(acc.id)
            rows.extend(self._build_account_folder_tree_rows(acc.id, acc_folders))

        prev_row: FolderTreeRow | None = None
        if 0 <= self.folder_cursor < len(self.folder_rows):
            candidate = self.folder_rows[self.folder_cursor]
            if not candidate.is_header:
                prev_row = candidate

        self.folder_rows = rows

        # 1. If cursor was already on a non-header row, keep it on that row (or its collapsed parent)
        if prev_row is not None:
            for idx, r in enumerate(self.folder_rows):
                if (
                    not r.is_header
                    and r.account_id == prev_row.account_id
                    and (
                        (prev_row.tree_key and r.tree_key == prev_row.tree_key)
                        or (not prev_row.tree_key and r.folder_name == prev_row.folder_name)
                    )
                ):
                    self.folder_cursor = idx
                    return

        # 2. Otherwise align cursor with active_account_id / active_folder
        for idx, r in enumerate(self.folder_rows):
            if not r.is_header and r.account_id == self.active_account_id and r.folder_name == self.active_folder:
                self.folder_cursor = idx
                return

        # 3. Fallback to first selectable mailbox row
        for idx, r in enumerate(self.folder_rows):
            if not r.is_header and r.selectable_mailbox:
                self.folder_cursor = idx
                self.active_account_id = r.account_id
                self.active_folder = r.folder_name
                break

    def _rebuild_message_rows(self) -> None:
        raw_msgs = self.store.list_messages(
            account_id=self.active_account_id,
            folder=self.active_folder,
        )
        rows: list[MessageListRow] = []
        if self.threading_enabled:
            threads = build_threads(raw_msgs)
            for th in threads:
                for idx, node in enumerate(th.nodes):
                    rows.append(
                        MessageListRow(
                            message=node.message,
                            thread=th,
                            depth=node.depth,
                            is_thread_root=(idx == 0),
                        )
                    )
        else:
            for m in raw_msgs:
                single_thread = EmailThread(
                    thread_id=m.thread_id or m.id,
                    subject=m.subject,
                    messages=[m],
                )
                rows.append(
                    MessageListRow(
                        message=m,
                        thread=single_thread,
                        depth=0,
                        is_thread_root=True,
                    )
                )

        self.message_rows = rows
        if self.message_rows:
            self.message_cursor = min(self.message_cursor, len(self.message_rows) - 1)
        else:
            self.message_cursor = 0

    @property
    def selected_message_row(self) -> MessageListRow | None:
        if 0 <= self.message_cursor < len(self.message_rows):
            return self.message_rows[self.message_cursor]
        return None

    @property
    def selected_message(self) -> EmailMessage | None:
        row = self.selected_message_row
        return row.message if row else None

    def _pane_widths(self) -> tuple[int, int, int]:
        """Compute responsive widths for (folder_pane, list_pane, reader_pane)."""
        total_w = max(80, self.width)
        folder_w = max(24, min(30, int(total_w * 0.21)))
        list_w = max(32, min(46, int(total_w * 0.34)))
        reader_w = max(30, total_w - folder_w - list_w)
        return folder_w, list_w, reader_w

    def _pane_height(self) -> int:
        # Top menu bar (1 line) + Bottom status bar (1 line)
        return max(10, self.height - 2)

    def _update_reader_viewport(self, force: bool = False) -> None:
        _, _, reader_w = self._pane_widths()
        pane_h = self._pane_height()
        self.reader_viewport.width = max(20, reader_w - 4)
        self.reader_viewport.height = max(6, pane_h - 2)

        row = self.selected_message_row
        if row is None:
            self._last_rendered_msg_key = ""
            self.reader_viewport.set_content(
                Style()
                .italic(True)
                .foreground(self.theme.fg_muted)
                .render("\n  No email selected in this folder.")
            )
            return

        m = row.message
        prev_msg_id = self._last_rendered_msg_key.split("|", 1)[0] if self._last_rendered_msg_key else ""
        cache_key = (
            f"{m.id}|{m.is_read}:{m.is_starred}:{m.pgp_status.value}:"
            f"{self.theme.id}:{self.image_renderer.backend.value}:"
            f"{self.show_raw_headers}:{reader_w}"
        )
        if not force and cache_key == self._last_rendered_msg_key:
            return

        prev_y = self.reader_viewport.y_offset
        self._last_rendered_msg_key = cache_key
        rendered = format_message_for_reading_pane(
            msg=m,
            theme=self.theme,
            width=reader_w - 2,
            image_renderer=self.image_renderer,
            thread=row.thread if self.threading_enabled else None,
            show_raw_headers=self.show_raw_headers,
        )
        self.reader_viewport.set_content(rendered)
        if prev_msg_id == m.id:
            self.reader_viewport.y_offset = min(prev_y, self.reader_viewport.max_offset)
        else:
            self.reader_viewport.y_offset = 0

    # ------------------------------------------------------------------
    # TEA Update Loop
    # ------------------------------------------------------------------

    def update(self, msg: Msg) -> tuple[EspressoMailApp, Cmd | None]:
        """Process all TEA messages deterministically."""
        # 1. Window Resize
        if isinstance(msg, WindowSizeMsg):
            self.width = max(80, msg.width)
            self.height = max(22, msg.height)
            self._update_reader_viewport(force=True)
            return self, None

        # 2. Toast notifications auto-dismiss
        self.toasts, _ = self.toasts.update(msg)

        # 3. Splash screen lifecycle
        if self.show_splash:
            if isinstance(msg, SplashDoneMsg):
                self.show_splash = False
                if not self.store.list_accounts():
                    self.show_onboarding = True
                    self.onboarding = OnboardingWizard(
                        self.theme,
                        allow_cancel=False,
                        credential_validator=self.credential_validator,
                    )
                    return self, None
                if any(acc.imap_password for acc in self.store.list_accounts(enabled_only=True)):
                    self.status_message = "Synchronizing IMAP accounts..."
                    return self, self._sync_accounts_cmd
                return self, None
            self.splash, cmd = self.splash.update(msg)
            return self, cmd

        # 4. Onboarding wizard lifecycle
        if self.show_onboarding:
            if isinstance(msg, OnboardingCompleteMsg):
                self.show_onboarding = False
                if msg.account is not None:
                    if msg.generate_pgp:
                        k_info = self.pgp.generate_key(msg.account.full_name, msg.account.email)
                        msg.account.pgp_key_id = k_info.key_id
                    self.store.save_account(msg.account)
                    self.active_account_id = msg.account.id
                    self.active_folder = "INBOX"
                    self.status_message = (
                        f"✔ Verified and configured account {msg.account.email}! Synchronizing..."
                    )
                    self.refresh_data()
                    return self, self._sync_accounts_cmd
                self.refresh_data()
                return self, None
            self.onboarding, cmd = self.onboarding.update(msg)
            if cmd is not None and callable(cmd):
                emitted = cmd()
                if isinstance(emitted, OnboardingCompleteMsg):
                    return self.update(emitted)
            return self, cmd

        # 5. Command Palette (Ctrl+P)
        if self.command_palette.is_open or (isinstance(msg, KeyMsg) and str(msg.key) == "ctrl+p"):
            self.command_palette, cmd = self.command_palette.update(msg)
            if cmd is not None:
                res = cmd() if callable(cmd) else None
                if isinstance(res, CommandPaletteSelectMsg):
                    self.command_palette.is_open = False
                    return self._execute_action(res.item.id)
                if isinstance(res, CommandPaletteCloseMsg):
                    self.command_palette.is_open = False
                    return self, None
            if isinstance(msg, CommandPaletteSelectMsg):
                self.command_palette.is_open = False
                return self._execute_action(msg.item.id)
            return self, cmd

        # 6. Active Modal Dialogs
        if self.active_modal is not None:
            if isinstance(msg, ModalClosedMsg):
                self.active_modal = None
                if msg.status_note:
                    self.status_message = msg.status_note
                self.refresh_data()
                if not self.store.list_accounts():
                    self.show_onboarding = True
                    self.onboarding = OnboardingWizard(
                        self.theme,
                        allow_cancel=False,
                        credential_validator=self.credential_validator,
                    )
                    return self, None
                if msg.trigger_sync:
                    return self, self._sync_accounts_cmd
                return self, None
            if isinstance(msg, ComposeSubmittedMsg):
                return self._handle_compose_submitted(msg)
            if isinstance(msg, SearchSelectMessageMsg):
                self.active_modal = None
                return self._jump_to_message(msg.message)
            if isinstance(msg, ComposeToContactMsg):
                self.active_modal = ComposeModal(
                    accounts=self.store.list_accounts(),
                    contacts=self.store.list_contacts(),
                    theme=self.theme,
                    active_account_id=self.active_account_id,
                    to_initial=msg.recipient,
                )
                return self, None

            self.active_modal, cmd = self.active_modal.update(msg)
            if cmd is not None and callable(cmd):
                emitted = cmd()
                if isinstance(emitted, (ModalClosedMsg, ComposeSubmittedMsg, SearchSelectMessageMsg, ComposeToContactMsg)):
                    return self.update(emitted)
            return self, None

        # 7. Background Sync completion
        if isinstance(msg, SyncCompletedMsg):
            self.status_message = msg.summary
            self.refresh_data()
            return self, None

        # 8. Menu Bar Action Dispatch
        if isinstance(msg, MenuActionMsg):
            return self._execute_action(msg.action_id)

        # If menu bar dropdown is open, route keys & mouse to menu bar first
        if self.menu_bar.is_open:
            self.menu_bar, cmd = self.menu_bar.update(msg)
            if cmd is not None and callable(cmd):
                emitted = cmd()
                if isinstance(emitted, MenuActionMsg):
                    return self._execute_action(emitted.action_id)
            return self, None

        # 9. Mouse Interaction across Menu Bar & 3 Panes
        if isinstance(msg, MouseMsg):
            return self._handle_mouse(msg)

        # 10. Keyboard Shortcuts in 3-Pane Workspace
        if isinstance(msg, KeyMsg):
            return self._handle_key(msg)

        return self, None

    # ------------------------------------------------------------------
    # Keyboard & Mouse Handlers
    # ------------------------------------------------------------------

    def _handle_key(self, msg: KeyMsg) -> tuple[EspressoMailApp, Cmd | None]:
        k = str(msg.key)

        # Global shortcuts
        if k == "q":
            return self, quit_app
        if k in ("f10", "m"):
            self.menu_bar.open_category(0)
            return self, None
        if k == "f1":
            return self._execute_action("replay_splash")
        if k == "?":
            return self._execute_action("show_shortcuts")
        if k == "/":
            return self._execute_action("fuzzy_search")
        if k == "c":
            return self._execute_action("compose")
        if k == "r":
            return self._execute_action("reply")
        if k == "R":
            return self._execute_action("reply_all")
        if k == "f":
            return self._execute_action("forward")
        if k == "s":
            return self._execute_action("toggle_star")
        if k == "u":
            return self._execute_action("toggle_read")
        if k == "e":
            if self.focused_pane == "folders":
                return self._execute_action("account_settings")
            return self._execute_action("archive_msg")
        if k in ("d", "delete"):
            return self._execute_action("trash_msg")
        if k == "X":
            return self._execute_action("empty_trash")
        if k == "w":
            return self._execute_action("save_attachments")
        if k == "t":
            return self._execute_action("toggle_threading")
        if k == "T":
            return self._execute_action("cycle_theme")
        if k == "i":
            return self._execute_action("cycle_image_mode")
        if k == "H":
            return self._execute_action("toggle_headers")
        if k == "U":
            return self._execute_action("unified_inbox")
        if k == "B":
            return self._execute_action("address_book")
        if k == "F":
            return self._execute_action("sieve_filters")
        if k == "G":
            return self._execute_action("pgp_manager")
        if k == "A":
            return self._execute_action("account_settings")
        if k == "O":
            return self._execute_action("new_account")
        if k == "S":
            return self._execute_action("sync_all")

        # Pane focus cycling
        panes = ["folders", "messages", "reader"]
        if k == "tab":
            idx = panes.index(self.focused_pane)
            self.focused_pane = panes[(idx + 1) % len(panes)]
            return self, None
        if k == "shift+tab":
            idx = panes.index(self.focused_pane)
            self.focused_pane = panes[(idx - 1) % len(panes)]
            return self, None
        if k in ("left", "h") and self.focused_pane != "folders":
            idx = panes.index(self.focused_pane)
            self.focused_pane = panes[max(0, idx - 1)]
            return self, None

        # Pane-specific navigation
        if self.focused_pane == "folders":
            if k in ("down", "j"):
                self._step_folder_cursor(1)
                return self, None
            if k in ("up", "k"):
                self._step_folder_cursor(-1)
                return self, None
            if k in ("left", "h"):
                if 0 <= self.folder_cursor < len(self.folder_rows):
                    cur_row = self.folder_rows[self.folder_cursor]
                    if cur_row.has_children and cur_row.is_expanded and cur_row.tree_key:
                        self.collapsed_folders.add(cur_row.tree_key)
                        self._rebuild_folder_rows()
                        self.status_message = f"Collapsed '{cur_row.label}'."
                return self, None
            if k in ("right", "l"):
                if 0 <= self.folder_cursor < len(self.folder_rows):
                    cur_row = self.folder_rows[self.folder_cursor]
                    if cur_row.has_children and not cur_row.is_expanded and cur_row.tree_key:
                        self.collapsed_folders.discard(cur_row.tree_key)
                        self._rebuild_folder_rows()
                        self.status_message = f"Expanded '{cur_row.label}'."
                        return self, None
                self.focused_pane = "messages"
                return self, None
            if k in (" ", "space"):
                if 0 <= self.folder_cursor < len(self.folder_rows):
                    cur_row = self.folder_rows[self.folder_cursor]
                    if cur_row.has_children and cur_row.tree_key:
                        self._toggle_folder_expand(cur_row)
                        return self, None
                self._activate_selected_folder()
                return self, None
            if k == "enter":
                if 0 <= self.folder_cursor < len(self.folder_rows):
                    cur_row = self.folder_rows[self.folder_cursor]
                    if cur_row.has_children and not cur_row.selectable_mailbox:
                        self._toggle_folder_expand(cur_row)
                        return self, None
                    if cur_row.has_children and not cur_row.is_expanded and cur_row.tree_key:
                        self.collapsed_folders.discard(cur_row.tree_key)
                        self._rebuild_folder_rows()
                self._activate_selected_folder()
                self.focused_pane = "messages"
                return self, None
            return self, None

        if self.focused_pane == "messages":
            if k in ("down", "j") and self.message_rows:
                self.message_cursor = min(len(self.message_rows) - 1, self.message_cursor + 1)
                self._on_message_selection_changed()
            elif k in ("up", "k") and self.message_rows:
                self.message_cursor = max(0, self.message_cursor - 1)
                self._on_message_selection_changed()
            elif k in ("home", "g") and self.message_rows:
                self.message_cursor = 0
                self._on_message_selection_changed()
            elif k in ("end", "G") and self.message_rows:
                self.message_cursor = len(self.message_rows) - 1
                self._on_message_selection_changed()
            elif k in ("enter", " ", "space", "l", "right"):
                m = self.selected_message
                if m and m.is_draft:
                    return self._open_draft_in_composer(m)
                if m and not m.is_read:
                    self.store.mark_read(m.id, True)
                    self.refresh_data()
                self.focused_pane = "reader"
            return self, None

        if self.focused_pane == "reader":
            if k in ("v", "x") and self.selected_message:
                return self._execute_action("pgp_manager")
            self.reader_viewport, cmd = self.reader_viewport.update(msg)
            return self, cmd

        return self, None

    def _toggle_folder_expand(self, row: FolderTreeRow) -> None:
        if not row.tree_key:
            return
        if row.tree_key in self.collapsed_folders:
            self.collapsed_folders.discard(row.tree_key)
            self._rebuild_folder_rows()
            self.status_message = f"Expanded '{row.label}'."
        else:
            self.collapsed_folders.add(row.tree_key)
            self._rebuild_folder_rows()
            self.status_message = f"Collapsed '{row.label}'."

    def _handle_mouse(self, msg: MouseMsg) -> tuple[EspressoMailApp, Cmd | None]:
        # Top menu bar row (y == 0)
        if msg.y == 0 and msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
            self.menu_bar, cmd = self.menu_bar.update(msg)
            return self, cmd

        folder_w, list_w, _ = self._pane_widths()
        body_y = msg.y - 2  # Account for menu bar (1) + top pane border (1)

        # Determine which pane the mouse is over
        if msg.x < folder_w:
            target_pane = "folders"
        elif msg.x < folder_w + list_w:
            target_pane = "messages"
        else:
            target_pane = "reader"

        if msg.button in (MouseButton.WHEEL_UP, MouseButton.WHEEL_DOWN):
            delta = -1 if msg.button == MouseButton.WHEEL_UP else 1
            if target_pane == "folders":
                self._step_folder_cursor(delta)
            elif target_pane == "messages" and self.message_rows:
                self.message_cursor = max(0, min(len(self.message_rows) - 1, self.message_cursor + delta))
                self._on_message_selection_changed()
            else:
                self.reader_viewport, _ = self.reader_viewport.update(msg)
            return self, None

        if msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
            self.focused_pane = target_pane
            if target_pane == "folders":
                clicked_idx = self.folder_scroll + body_y
                if 0 <= clicked_idx < len(self.folder_rows) and not self.folder_rows[clicked_idx].is_header:
                    self.folder_cursor = clicked_idx
                    clicked_row = self.folder_rows[clicked_idx]
                    if clicked_row.has_children and (
                        not clicked_row.selectable_mailbox or msg.x <= 6 + clicked_row.depth * 2
                    ):
                        self._toggle_folder_expand(clicked_row)
                    else:
                        self._activate_selected_folder()
            elif target_pane == "messages":
                clicked_idx = self.message_scroll + body_y
                if 0 <= clicked_idx < len(self.message_rows):
                    self.message_cursor = clicked_idx
                    self._on_message_selection_changed()
            return self, None

        return self, None

    def _step_folder_cursor(self, direction: int) -> None:
        if not self.folder_rows:
            return
        idx = self.folder_cursor
        for _ in range(len(self.folder_rows)):
            idx = (idx + direction) % len(self.folder_rows)
            if not self.folder_rows[idx].is_header:
                self.folder_cursor = idx
                row = self.folder_rows[idx]
                if row.selectable_mailbox:
                    self._activate_selected_folder()
                else:
                    self.status_message = (
                        f"Folder group '{row.label}' ({row.total_count} messages — Space/Enter to toggle)"
                    )
                break

    def _activate_selected_folder(self) -> None:
        if 0 <= self.folder_cursor < len(self.folder_rows):
            row = self.folder_rows[self.folder_cursor]
            if not row.is_header and row.selectable_mailbox:
                self.active_account_id = row.account_id
                self.active_folder = row.folder_name
                self.message_cursor = 0
                self.message_scroll = 0
                self._rebuild_message_rows()
                self._update_reader_viewport(force=True)
                path_label = (
                    row.label
                    if row.account_id == "*"
                    else " / ".join(split_folder_hierarchy(row.folder_name))
                )
                self.status_message = f"Opened {path_label} ({len(self.message_rows)} items)"

    def _on_message_selection_changed(self) -> None:
        m = self.selected_message
        if m and not m.is_read:
            self.store.mark_read(m.id, True)
            m.is_read = True
            self._rebuild_folder_rows()
        self._update_reader_viewport(force=True)

    # ------------------------------------------------------------------
    # Application Actions
    # ------------------------------------------------------------------

    def _execute_action(self, action_id: str) -> tuple[EspressoMailApp, Cmd | None]:
        if action_id == "quit_app":
            return self, quit_app

        if action_id == "replay_splash":
            self.show_splash = True
            self.splash = SplashScreen(self.theme)
            return self, self.splash.init()

        if action_id == "show_shortcuts":
            self.active_modal = HelpModal(self.theme)
            return self, None

        if action_id == "command_palette":
            self.command_palette.is_open = True
            return self, None

        if action_id == "fuzzy_search":
            self.active_modal = SearchModal(self.store, self.theme)
            return self, None

        if action_id == "address_book":
            self.active_modal = AddressBookModal(self.store, self.theme)
            return self, None

        if action_id == "sieve_filters":
            self.active_modal = SieveModal(self.store, self.theme, self.active_account_id)
            return self, None

        if action_id == "pgp_manager":
            self.active_modal = PGPModal(
                self.pgp,
                self.store,
                self.theme,
                active_message=self.selected_message,
            )
            return self, None

        if action_id == "account_settings":
            self.active_modal = AccountSettingsModal(
                store=self.store,
                theme=self.theme,
                active_account_id=self.active_account_id,
                credential_validator=self.credential_validator,
            )
            return self, None

        if action_id == "new_account":
            self.show_onboarding = True
            self.onboarding = OnboardingWizard(
                self.theme,
                allow_cancel=True,
                credential_validator=self.credential_validator,
            )
            return self, None

        if action_id == "unified_inbox":
            self.active_account_id = "*"
            self.active_folder = "INBOX"
            self.message_cursor = 0
            self.refresh_data()
            self.status_message = "Switched to Unified Inbox (all accounts)."
            return self, None

        if action_id == "toggle_threading":
            self.threading_enabled = not self.threading_enabled
            self.store.set_setting("threading", "1" if self.threading_enabled else "0")
            self.refresh_data()
            state_str = "enabled" if self.threading_enabled else "disabled"
            self.status_message = f"Conversation threading {state_str}."
            return self, None

        if action_id == "cycle_theme":
            new_theme = self.theme_engine.next_theme()
            self.store.set_setting("theme", new_theme.id)
            self._update_reader_viewport(force=True)
            self.status_message = f"Theme switched to {new_theme.name}."
            return self, None

        if action_id == "cycle_image_mode":
            new_mode = self.image_renderer.cycle_backend()
            self.store.set_setting("image_backend", new_mode.value)
            self._update_reader_viewport(force=True)
            self.status_message = f"Terminal image renderer set to: {self.image_renderer.label}"
            return self, None

        if action_id == "toggle_headers":
            self.show_raw_headers = not self.show_raw_headers
            self._update_reader_viewport(force=True)
            self.status_message = (
                "Showing raw RFC 5322 headers." if self.show_raw_headers else "Hiding raw RFC 5322 headers."
            )
            return self, None

        if action_id == "compose":
            self.active_modal = ComposeModal(
                accounts=self.store.list_accounts(),
                contacts=self.store.list_contacts(),
                theme=self.theme,
                active_account_id=self.active_account_id,
            )
            return self, None

        if action_id in ("reply", "reply_all", "forward"):
            m = self.selected_message
            if not m:
                self.status_message = "No message selected."
                return self, None
            return self._open_reply_or_forward(m, mode=action_id)

        if action_id == "toggle_star":
            m = self.selected_message
            if m:
                new_star = self.store.toggle_star(m.id)
                self.refresh_data()
                self.status_message = "★ Starred message." if new_star else "Removed star."
            return self, None

        if action_id == "toggle_read":
            m = self.selected_message
            if m:
                new_read = not m.is_read
                self.store.mark_read(m.id, new_read)
                self.refresh_data()
                self.status_message = "Marked as read." if new_read else "Marked as unread."
            return self, None

        if action_id == "archive_msg":
            m = self.selected_message
            if m:
                self.store.move_to_folder(m.id, "Archive")
                self.refresh_data()
                self.status_message = f"Archived '{m.subject[:36]}'."
            return self, None

        if action_id == "trash_msg":
            m = self.selected_message
            if m:
                if m.is_trashed or m.folder == "Trash":
                    self.store.delete_message_permanently(m.id)
                    self.status_message = f"Permanently deleted '{m.subject[:36]}'."
                else:
                    self.store.move_to_trash(m.id)
                    self.status_message = f"Moved '{m.subject[:36]}' to Trash."
                self.refresh_data()
            return self, None

        if action_id == "empty_trash":
            deleted_cnt = self.store.empty_trash(self.active_account_id)
            self.refresh_data()
            self.status_message = f"Emptied Trash ({deleted_cnt} messages permanently removed)."
            return self, None

        if action_id == "save_attachments":
            m = self.selected_message
            if not m or not m.attachments:
                self.status_message = "Selected message has no attachments to download."
                return self, None
            saved_paths: list[str] = []
            for att in m.attachments:
                out_p = self.store.save_attachment_to_disk(att.id, self.download_dir)
                saved_paths.append(out_p.name)
            self.refresh_data()
            self.status_message = f"✔ Saved {len(saved_paths)} attachment(s) to {self.download_dir}: {', '.join(saved_paths)}"
            return self, None

        if action_id == "sync_all":
            self.status_message = "Synchronizing IMAP accounts..."
            return self, self._sync_accounts_cmd

        return self, None

    def _open_reply_or_forward(
        self, msg: EmailMessage, mode: str
    ) -> tuple[EspressoMailApp, Cmd | None]:
        quoted_lines = "\n".join(f"> {line}" for line in msg.body_text.splitlines())
        header_intro = f"\n\nOn {msg.date_iso or msg.short_date}, {msg.sender_display} wrote:\n{quoted_lines}"

        if mode == "forward":
            subj = msg.subject if msg.subject.lower().startswith("fwd:") else f"Fwd: {msg.subject}"
            to_init = ""
            cc_init = ""
        else:
            subj = msg.subject if msg.subject.lower().startswith("re:") else f"Re: {msg.subject}"
            to_init = msg.sender_display
            cc_init = ", ".join(msg.recipients_cc) if mode == "reply_all" else ""

        refs = list(msg.references)
        if msg.message_id and msg.message_id not in refs:
            refs.append(msg.message_id)

        self.active_modal = ComposeModal(
            accounts=self.store.list_accounts(),
            contacts=self.store.list_contacts(),
            theme=self.theme,
            active_account_id=msg.account_id,
            to_initial=to_init,
            cc_initial=cc_init,
            subject_initial=subj,
            body_initial=header_intro,
            in_reply_to=msg.message_id,
            references=refs,
        )
        return self, None

    def _open_draft_in_composer(self, draft: EmailMessage) -> tuple[EspressoMailApp, Cmd | None]:
        self.active_modal = ComposeModal(
            accounts=self.store.list_accounts(),
            contacts=self.store.list_contacts(),
            theme=self.theme,
            active_account_id=draft.account_id,
            to_initial=", ".join(draft.recipients_to),
            cc_initial=", ".join(draft.recipients_cc),
            subject_initial=draft.subject,
            body_initial=draft.body_text,
            in_reply_to=draft.in_reply_to,
            references=draft.references,
            draft_id=draft.id,
        )
        return self, None

    def _handle_compose_submitted(
        self, msg: ComposeSubmittedMsg
    ) -> tuple[EspressoMailApp, Cmd | None]:
        body_payload = msg.body_text
        pgp_status = PGPStatus.NONE
        pgp_fp = ""

        if not msg.is_draft:
            if msg.pgp_encrypt:
                enc_res = self.pgp.encrypt_message(
                    body_payload,
                    recipients=msg.to_addrs,
                    sign=msg.pgp_sign,
                    signer_email=msg.account.email,
                )
                body_payload = enc_res.payload
                pgp_status = PGPStatus.ENCRYPTED
                pgp_fp = enc_res.fingerprint
            elif msg.pgp_sign:
                sig_res = self.pgp.clearsign_message(
                    body_payload,
                    signer_email=msg.account.email,
                )
                body_payload = sig_res.payload
                pgp_status = PGPStatus.SIGNED_VERIFIED
                pgp_fp = sig_res.fingerprint

        mime_msg, domain_msg = build_mime_message(
            account=msg.account,
            recipients_to=msg.to_addrs,
            subject=msg.subject,
            body_text=body_payload,
            recipients_cc=msg.cc_addrs,
            in_reply_to=msg.in_reply_to,
            references=msg.references,
            attachment_paths=msg.attachments,
        )

        if msg.is_draft:
            domain_msg.id = msg.draft_id or f"{msg.account.id}:Drafts:{uuid.uuid4().hex[:8]}"
            domain_msg.folder = "Drafts"
            domain_msg.is_draft = True
            self.store.save_message(domain_msg)
            self.active_modal = None
            self.refresh_data()
            self.status_message = f"📝 Saved draft '{msg.subject}'."
            return self, None

        # Remove draft if this message was previously saved as a draft
        if msg.draft_id:
            self.store.delete_message_permanently(msg.draft_id)

        domain_msg.pgp_status = pgp_status
        domain_msg.pgp_key_fingerprint = pgp_fp
        domain_msg.pgp_signer = f"{msg.account.full_name} <{msg.account.email}>" if pgp_status != PGPStatus.NONE else ""

        # Attempt SMTP transmission if credentials are provided; always store in Sent
        smtp_note = "Saved to Sent"
        if msg.account.smtp_host and msg.account.smtp_password:
            try:
                client = SMTPClient(msg.account, timeout=5.0)
                client.send_mime_message(mime_msg)
                smtp_note = f"Sent via {msg.account.smtp_host}"
            except Exception as exc:
                smtp_note = f"Queued locally in Sent (SMTP offline: {type(exc).__name__})"

        self.store.save_message(domain_msg)
        self.active_modal = None
        self.refresh_data()
        self.status_message = f"📤 {smtp_note}: '{msg.subject}' to {', '.join(msg.to_addrs) or '(no recipient)'}"
        return self, None

    def _jump_to_message(self, target: EmailMessage) -> tuple[EspressoMailApp, Cmd | None]:
        self.active_account_id = target.account_id
        self.active_folder = target.folder
        self.refresh_data()
        for idx, row in enumerate(self.message_rows):
            if row.message.id == target.id:
                self.message_cursor = idx
                break
        self._on_message_selection_changed()
        self.focused_pane = "reader"
        self.status_message = f"Jumped to '{target.subject[:40]}'"
        return self, None

    def _sync_accounts_cmd(self) -> Msg:
        accounts = self.store.list_accounts(enabled_only=True)
        total_synced = 0
        total_folders = 0
        online_accounts = 0
        errors: list[str] = []
        for acc in accounts:
            if not acc.imap_password:
                continue
            try:
                client = IMAPClient(acc, timeout=20.0)
                client.connect()
                try:
                    total_synced += client.sync_to_store(self.store)
                finally:
                    client.disconnect()
                total_folders += len(self.store.list_folders(acc.id))
                online_accounts += 1
            except Exception as exc:
                errors.append(f"{acc.email}: {exc}")
        if online_accounts > 0:
            err_suffix = f" (Warnings: {'; '.join(errors)})" if errors else ""
            return SyncCompletedMsg(
                synced_count=total_synced,
                summary=(
                    f"✔ Synchronized {total_synced} message(s) across {total_folders} folder(s) "
                    f"({online_accounts} IMAP account(s)).{err_suffix}"
                ),
            )
        if errors:
            return SyncCompletedMsg(
                synced_count=0,
                summary=f"⚠ IMAP sync failed: {'; '.join(errors)}",
            )
        return SyncCompletedMsg(
            synced_count=0,
            summary=f"✔ Local SQLite store up to date ({len(accounts)} accounts cached for offline access).",
        )

    # ------------------------------------------------------------------
    # Pure View Rendering
    # ------------------------------------------------------------------

    def view(self) -> str:
        """Render the complete EspressoMail screen."""
        if self.show_splash:
            return self.splash.view(self.width, self.height)

        if self.show_onboarding:
            return self.onboarding.view(self.width, self.height)

        folder_w, list_w, reader_w = self._pane_widths()
        pane_h = self._pane_height()

        # 1. Top Menu Bar
        right_info = f"🎨 {self.theme.name} │ 🖼️ {self.image_renderer.backend.value}"
        top_bar = self.menu_bar.view_bar(self.width, self.theme, right_status=right_info)

        # 2. Left Pane: Account / Folder Tree
        left_pane = self._render_folder_pane(folder_w, pane_h)

        # 3. Center Pane: Message / Thread List
        center_pane = self._render_message_list_pane(list_w, pane_h)

        # 4. Right Pane: Reading Pane
        right_pane = self._render_reading_pane(reader_w, pane_h)

        main_three_panes = join_horizontal(Align.TOP, left_pane, center_pane, right_pane)

        # 5. Bottom Status Bar
        bottom_bar = self._render_status_bar()

        screen = join_vertical(Align.LEFT, top_bar, main_three_panes, bottom_bar)

        # 6. Overlay Open Drop-Down Menu (if active)
        if self.menu_bar.is_open:
            screen = self.menu_bar.overlay_onto(screen, self.theme)

        # 7. Overlay Active Modal Dialog (if active)
        if self.active_modal is not None:
            modal_str = self.active_modal.view(self.width)  # type: ignore[call-arg]
            screen = place_overlay(screen, modal_str, center=True, dim_backdrop=True)

        # 8. Overlay Command Palette (if open)
        if self.command_palette.is_open:
            screen = place_overlay(
                screen,
                self.command_palette.view(),
                center=True,
                dim_backdrop=True,
            )

        return screen

    def _render_folder_pane(self, width: int, height: int) -> str:
        inner_w = max(12, width - 4)
        inner_h = max(4, height - 2)
        lines: list[str] = []

        if self.folder_cursor < self.folder_scroll:
            self.folder_scroll = self.folder_cursor
        elif self.folder_cursor >= self.folder_scroll + inner_h:
            self.folder_scroll = self.folder_cursor - inner_h + 1

        visible_folders = self.folder_rows[self.folder_scroll : self.folder_scroll + inner_h]
        for offset, row in enumerate(visible_folders):
            idx = self.folder_scroll + offset
            if row.is_header:
                hdr_color = row.badge_color or self.theme.accent_secondary
                hdr_txt = truncate_ansi(f" {row.icon} {row.label}", inner_w)
                lines.append(Style().bold(True).foreground(hdr_color).render(hdr_txt))
                continue

            is_selected = idx == self.folder_cursor
            is_active = (
                row.account_id == self.active_account_id and row.folder_name == self.active_folder
            )
            marker = "▸" if is_selected else ("•" if is_active else " ")
            count_badge = f"({row.unread_count})" if row.unread_count > 0 else (f"{row.total_count}" if row.total_count > 0 else "")

            if row.depth == 0:
                branch = ("▾ " if row.is_expanded else "▸ ") if row.has_children else ""
                lbl_raw = f" {marker} {branch}{row.icon} {row.label}"
            else:
                indent = "  " * row.depth
                if row.has_children:
                    connector = "▾ " if row.is_expanded else "▸ "
                else:
                    connector = "╰─ " if row.is_last_sibling else "├─ "
                lbl_raw = f" {marker} {indent}{connector}{row.icon} {row.label}"

            pad = max(1, inner_w - string_width(lbl_raw) - len(count_badge) - 1)
            line_str = truncate_ansi(f"{lbl_raw}{' ' * pad}{count_badge} ", inner_w)

            if is_selected and self.focused_pane == "folders":
                lines.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.bg_primary)
                    .background(self.theme.accent_primary)
                    .width(inner_w)
                    .render(line_str)
                )
            elif is_active:
                lines.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.accent_secondary)
                    .width(inner_w)
                    .render(line_str)
                )
            elif row.unread_count > 0:
                lines.append(
                    Style().bold(True).foreground(self.theme.fg_primary).render(line_str)
                )
            else:
                lines.append(Style().foreground(self.theme.fg_secondary).render(line_str))

        return self.theme.pane_style(
            focused=(self.focused_pane == "folders"),
            width=width,
            height=height,
            title="📁 Mailboxes",
        ).render("\n".join(lines))

    def _render_message_list_pane(self, width: int, height: int) -> str:
        inner_w = max(18, width - 4)
        inner_h = max(4, height - 2)

        # Keep selected message visible in scroll window
        if self.message_cursor < self.message_scroll:
            self.message_scroll = self.message_cursor
        elif self.message_cursor >= self.message_scroll + inner_h:
            self.message_scroll = self.message_cursor - inner_h + 1

        visible = self.message_rows[self.message_scroll : self.message_scroll + inner_h]
        lines: list[str] = []

        if not visible:
            lines.append(
                Style().italic(True).foreground(self.theme.fg_muted).render("  (No messages in folder)")
            )
        else:
            for offset, row in enumerate(visible):
                abs_idx = self.message_scroll + offset
                m = row.message
                is_sel = abs_idx == self.message_cursor

                unread_ch = "●" if not m.is_read else " "
                star_ch = "★" if m.is_starred else " "
                att_ch = "📎" if m.has_attachments else "  "
                pgp_ch = "🔒" if m.pgp_status != PGPStatus.NONE else "  "

                tree_prefix = ""
                if self.threading_enabled and row.depth > 0:
                    tree_prefix = ("  " * (row.depth - 1)) + "╰─ "

                thread_cnt = (
                    f"[{row.thread.message_count}] "
                    if (self.threading_enabled and row.is_thread_root and row.thread.message_count > 1)
                    else ""
                )

                sender_col = truncate_ansi(f"{tree_prefix}{m.sender_short}", 14)
                date_col = f"{m.short_date:>6}"
                subj_avail = max(8, inner_w - 28)
                subj_col = truncate_ansi(f"{thread_cnt}{m.subject}", subj_avail)

                raw_line = f"{unread_ch}{star_ch} {sender_col:<14} {subj_col:<{subj_avail}} {att_ch}{pgp_ch}{date_col}"
                trimmed = truncate_ansi(raw_line, inner_w)

                if is_sel and self.focused_pane == "messages":
                    lines.append(
                        Style()
                        .bold(True)
                        .foreground(self.theme.bg_primary)
                        .background(self.theme.accent_primary)
                        .width(inner_w)
                        .render(trimmed)
                    )
                elif is_sel:
                    lines.append(
                        Style()
                        .bold(True)
                        .foreground(self.theme.accent_secondary)
                        .background(self.theme.bg_highlight)
                        .width(inner_w)
                        .render(trimmed)
                    )
                elif not m.is_read:
                    lines.append(
                        Style().bold(True).foreground(self.theme.unread_fg).render(trimmed)
                    )
                else:
                    lines.append(Style().foreground(self.theme.fg_primary).render(trimmed))

        folder_label = "Unified" if self.active_account_id == "*" else self.active_account_id
        folder_path = (
            self.active_folder
            if self.active_account_id == "*"
            else " / ".join(split_folder_hierarchy(self.active_folder))
        )
        pane_title = f"✉️ {folder_path} ({folder_label})"

        return self.theme.pane_style(
            focused=(self.focused_pane == "messages"),
            width=width,
            height=height,
            title=pane_title,
        ).render("\n".join(lines))

    def _render_reading_pane(self, width: int, height: int) -> str:
        m = self.selected_message
        title = "📖 Reading Pane"
        if m:
            scroll_pct = int(self.reader_viewport.scroll_percent * 100)
            title = f"📖 Reading Pane ({scroll_pct}%)"

        return self.theme.pane_style(
            focused=(self.focused_pane == "reader"),
            width=width,
            height=height,
            title=title,
        ).render(self.reader_viewport.view())

    def _render_status_bar(self) -> str:
        pane_badge = self.focused_pane.upper()
        bar = StatusBar(
            left=[
                StatusSection(
                    f" {pane_badge} ",
                    style=Style()
                    .bold(True)
                    .foreground(self.theme.bg_primary)
                    .background(self.theme.accent_primary),
                    priority=3,
                ),
                StatusSection(
                    f" {self.status_message} ",
                    style=Style().foreground(self.theme.fg_primary),
                    priority=2,
                ),
            ],
            center=[
                StatusSection(
                    "[c]Compose [/]Search [r]Reply [t]Threads [T]Theme [i]Img [?]Help",
                    style=Style().foreground(self.theme.fg_muted),
                    priority=1,
                )
            ],
            right=[
                StatusSection(
                    f"Msgs: {len(self.message_rows)}",
                    style=Style().foreground(self.theme.accent_secondary),
                    priority=3,
                ),
            ],
            width=self.width,
            background=self.theme.bg_statusbar,
        )
        return bar.view()
