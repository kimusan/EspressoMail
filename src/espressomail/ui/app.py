"""Main 3-pane Espresso TEA application model for EspressoMail."""

from __future__ import annotations

import json
import re
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
    tick,
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
    Tree,
    TreeNode,
    TreeNodeSelectMsg,
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
    Contact,
    EmailMessage,
    EmailThread,
    Folder,
    FolderType,
    PGPStatus,
    classify_folder_type,
    split_folder_hierarchy,
)
from espressomail.protocols.imap_client import IMAPClient
from espressomail.protocols.sieve import ManageSieveClient
from espressomail.protocols.smtp_client import (
    SMTPClient,
    build_mime_message,
    validate_account_credentials,
)
from espressomail.protocols.threading import build_threads
from espressomail.storage.database import LocalStore, default_data_dir
from espressomail.themes.engine import Theme, ThemeEngine
from espressomail.ui.formatter import extract_html_images, format_message_for_reading_pane
from espressomail.ui.image_renderer import (
    ImageRenderBackend,
    TerminalImageRenderer,
    _REMOTE_IMAGE_CACHE,
    _fetch_remote_image_bytes,
)
from espressomail.ui.menus import MenuActionMsg, MenuBar
from espressomail.ui.modals import (
    AccountSettingsModal,
    AddressBookModal,
    ComposeModal,
    ComposeSubmittedMsg,
    ComposeToContactMsg,
    ConfirmExitModal,
    DownloadAttachmentsModal,
    HelpModal,
    ModalClosedMsg,
    PGPModal,
    PreferencesModal,
    PreferencesSavedMsg,
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
    subscribed: bool = True


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


@dataclass(frozen=True)
class MessageImagesLoadedMsg(Msg):
    """Emitted when background remote image downloading completes for a message."""

    message_id: str
    loaded_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncProgressMsg(Msg):
    """Emitted during background IMAP synchronization progress."""

    account_id: str
    account_name: str
    action: str  # "discovered_folders", "synced_folder", "sync_done"
    folder_name: str = ""
    new_messages: int = 0
    total_synced: int = 0
    total_folders: int = 0
    step_index: int = 0
    total_steps: int = 0
    remaining_steps: tuple[tuple[str, str, str], ...] = ()
    errors: tuple[str, ...] = ()
    is_done: bool = False
    summary: str = ""


@dataclass(frozen=True)
class MoreMessagesFetchedMsg(Msg):
    """Emitted when fetching older messages from server completes."""

    account_id: str
    folder: str
    fetched_count: int
    message: str


@dataclass(frozen=True)
class AutoSyncTickMsg(Msg):
    """Periodic auto-sync timer tick."""


@dataclass(frozen=True)
class FlagSyncedMsg(Msg):
    r"""Emitted when an IMAP flag (e.g. \Flagged or \Seen) has been synced to the server."""

    account_id: str
    folder: str
    uid: int
    flag: str
    enabled: bool
    success: bool
    error: str = ""


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
            store=self.store,
        )

        # Active 3-pane state
        self.focused_pane = "messages"  # "folders", "messages", "reader"
        self.threading_enabled = self.store.get_setting("threading", "1") == "1"
        self.show_raw_headers = False
        self.status_message = "Ready. Press ? for shortcuts, / to search, c to compose."

        self.folder_tree: Tree = Tree(nodes=[])
        self.folder_scroll = 0
        self.expanded_folders: set[str] = self._load_expanded_folders()
        self.active_account_id = "*"
        self.active_folder = "INBOX"

        self.message_rows: list[MessageListRow] = []
        self.message_cursor = 0
        self.message_scroll = 0

        self.reader_viewport = Viewport(width=50, height=24)
        self._last_rendered_msg_key = ""
        self._accounts_cache: dict[str, AccountConfig] = {}

        # Splitter resize state
        saved_folder_w = self.store.get_setting("custom_folder_w", "")
        self.custom_folder_w: int | None = int(saved_folder_w) if saved_folder_w.isdigit() else None
        saved_list_w = self.store.get_setting("custom_list_w", "")
        self.custom_list_w: int | None = int(saved_list_w) if saved_list_w.isdigit() else None
        self.splitter_dragging: int = 0  # 0=none, 1=folder/list splitter, 2=list/reader splitter

        # Account collapsing and folder display preferences
        self.collapsed_accounts: set[str] = self._load_collapsed_accounts()
        self.folder_count_mode: str = self.store.get_setting("folder_count_mode", "unread_only")
        cur_batch = self.store.get_setting("fetch_batch_size", "50")
        self.fetch_batch_size: int = int(cur_batch) if cur_batch.isdigit() else 50

        # Active modal overlay
        self.active_modal: Model | None = None

        # Background sync state
        self.is_syncing: bool = False
        self.sync_progress_text: str = ""

        # Command Palette (Ctrl+P)
        self.command_palette = self._build_command_palette()

        self.refresh_data()
        if not self.show_splash and not self.store.list_accounts():
            self.show_onboarding = True

    @property
    def folder_rows(self) -> list[FolderTreeRow]:
        visible = self.folder_tree._flatten_visible()
        rows: list[FolderTreeRow] = []
        for node, depth, prefix, parent in visible:
            r: FolderTreeRow | None = node.value
            if r is None:
                continue
            if r.is_header or r.account_id == "*":
                row_depth = 0
            else:
                row_depth = max(0, depth - 1)

            rows.append(
                FolderTreeRow(
                    account_id=r.account_id,
                    folder_name=r.folder_name,
                    label=r.label,
                    icon=r.icon,
                    unread_count=r.unread_count,
                    total_count=r.total_count,
                    is_header=r.is_header,
                    badge_color=r.badge_color,
                    depth=row_depth,
                    has_children=not node.is_leaf,
                    is_expanded=node.expanded,
                    is_last_sibling=False,
                    tree_key=r.tree_key,
                    selectable_mailbox=r.selectable_mailbox,
                    subscribed=getattr(r, "subscribed", True),
                )
            )
        return rows

    @folder_rows.setter
    def folder_rows(self, value: list[FolderTreeRow]) -> None:
        pass

    @property
    def folder_cursor(self) -> int:
        return self.folder_tree.cursor

    @folder_cursor.setter
    def folder_cursor(self, val: int) -> None:
        self.folder_tree.cursor = max(0, val)

    def _load_expanded_folders(self) -> set[str]:
        """Load persisted expanded folder tree keys from the SQLite settings table."""
        raw = self.store.get_setting("expanded_folders", "[]")
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {str(k) for k in parsed if k}
        except Exception:
            pass
        return set()

    def _save_expanded_folders(self) -> None:
        """Persist expanded folder tree keys to the SQLite settings table across sessions."""
        expanded: set[str] = set()

        def _collect(nodes: list[TreeNode]) -> None:
            for n in nodes:
                r: FolderTreeRow | None = n.value
                if n.expanded and r is not None and r.tree_key:
                    expanded.add(r.tree_key)
                if n.children:
                    _collect(n.children)

        _collect(self.folder_tree.nodes)
        self.expanded_folders = expanded
        try:
            self.store.set_setting(
                "expanded_folders",
                json.dumps(sorted(self.expanded_folders)),
            )
        except Exception:
            pass

    def _load_collapsed_accounts(self) -> set[str]:
        """Load persisted collapsed account IDs from SQLite settings table."""
        raw = self.store.get_setting("collapsed_accounts", "[]")
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {str(k) for k in parsed if k}
        except Exception:
            pass
        return set()

    def _save_collapsed_accounts(self) -> None:
        """Persist collapsed account IDs to the SQLite settings table."""
        try:
            self.store.set_setting(
                "collapsed_accounts",
                json.dumps(sorted(self.collapsed_accounts)),
            )
        except Exception:
            pass

    def _toggle_account_collapse(self, acc_id: str) -> None:
        """Toggle collapsed/expanded state of an account header and persist across sessions."""
        if acc_id in self.collapsed_accounts:
            self.collapsed_accounts.remove(acc_id)
            action = "Expanded"
        else:
            self.collapsed_accounts.add(acc_id)
            action = "Collapsed"
        self._save_collapsed_accounts()
        self.refresh_data()
        acc = self._accounts_cache.get(acc_id)
        name = acc.name if acc else ("Unified Mailboxes" if acc_id == "*" else acc_id)
        self.status_message = f"{action} account '{name}'."

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
            PaletteItem("cycle_image_mode", "View: Cycle Image Renderer", "Switch Chafa / Sixel / Kitty / Half-Block", "i", "View"),
            PaletteItem("expand_reader", "View: Widen Reading Pane", "Move splitter left to widen reading pane", "[", "View"),
            PaletteItem("expand_list", "View: Widen Message List", "Move splitter right to widen message list", "]", "View"),
            PaletteItem("widen_folder_list", "View: Widen Folder List", "Expand folder list width (+2 cols)", "}", "View"),
            PaletteItem("narrow_folder_list", "View: Narrow Folder List", "Narrow folder list width (-2 cols)", "{", "View"),
            PaletteItem("reset_splitter", "View: Reset Splitters", "Reset all pane widths to responsive defaults", "", "View"),
            PaletteItem("save_attachments", "Mail: Download Attachments", "Save attachments to downloads directory", "w", "Mail"),
            PaletteItem("fetch_more", "Mail: Fetch Older Messages", "Download earlier emails from IMAP folder", "M", "Mail"),
            PaletteItem("address_book", "Tools: Local Address Book", "Manage contacts and PGP fingerprints", "B", "Tools"),
            PaletteItem("sieve_filters", "Tools: ManageSieve Filter Rules", "Configure RFC 5228 Sieve scripts", "F", "Tools"),
            PaletteItem("pgp_manager", "Security: GPG/PGP Keyring Manager", "Inspect keys, sign, verify, and decrypt", "G", "Security"),
            PaletteItem("account_settings", "Accounts: Edit Account Settings", "Update password, IMAP/SMTP hosts, ports, and TLS", "A", "Accounts"),
            PaletteItem("preferences", "Settings: General Preferences", "Configure download folder, image renderer, theme, threading", "p", "Settings"),
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

    def _build_account_folder_tree_nodes(
        self, account_id: str, folders: list[Folder]
    ) -> list[TreeNode]:
        """Build a hierarchical, collapsible tree of TreeNodes for a single account."""
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

        def _to_tree_node(node: _FolderTreeNode, depth: int) -> TreeNode:
            tree_key = f"{account_id}:{'/'.join(node.path_parts)}"
            has_children = bool(node.children)
            is_expanded = tree_key in self.expanded_folders
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
            selectable_mailbox = node.folder is not None and node.folder.selectable

            child_nodes: list[TreeNode] = []
            if has_children:
                sorted_children = sorted(node.children.values(), key=_child_sort_key)
                for child in sorted_children:
                    child_nodes.append(_to_tree_node(child, depth + 1))

            subscribed = node.folder.subscribed if node.folder is not None else True

            row = FolderTreeRow(
                account_id=account_id,
                folder_name=folder_name,
                label=label,
                icon=icon,
                unread_count=u_cnt,
                total_count=t_cnt,
                depth=depth,
                has_children=has_children,
                is_expanded=is_expanded,
                is_last_sibling=False,
                tree_key=tree_key,
                selectable_mailbox=selectable_mailbox,
                subscribed=subscribed,
            )

            return TreeNode(
                label=label,
                value=row,
                children=child_nodes,
                expanded=is_expanded,
            )

        sorted_roots = sorted(
            roots.values(),
            key=lambda n: (type_order.get(_node_folder_type(n, 0), 7), n.segment.lower()),
        )
        return [_to_tree_node(r, 0) for r in sorted_roots]

    def _rebuild_folder_rows(self) -> None:
        top_nodes: list[TreeNode] = []
        unified_counts = self.store.get_unified_counts()

        unified_defs = [
            ("INBOX", "Unified Inbox", "📥"),
            ("All Unread", "All Unread", "✉️"),
            ("Starred", "All Starred", "⭐"),
            ("Drafts", "All Drafts", "📝"),
            ("Sent", "All Sent", "📤"),
            ("Archive", "All Archive", "📦"),
            ("Trash", "All Trash", "🗑️"),
        ]
        unified_children: list[TreeNode] = []
        for fname, label, icon in unified_defs:
            u_cnt, t_cnt = unified_counts.get(fname, (0, 0))
            unified_children.append(
                TreeNode(
                    label=label,
                    value=FolderTreeRow(
                        account_id="*",
                        folder_name=fname,
                        label=label,
                        icon=icon,
                        unread_count=u_cnt,
                        total_count=t_cnt,
                        depth=0,
                        has_children=False,
                        is_expanded=False,
                        selectable_mailbox=True,
                    ),
                    children=[],
                    expanded=False,
                )
            )
        is_unified_expanded = "*" not in self.collapsed_accounts
        top_nodes.append(
            TreeNode(
                label="UNIFIED MAILBOXES",
                value=FolderTreeRow(
                    account_id="*",
                    folder_name="",
                    label="UNIFIED MAILBOXES",
                    icon="🌐",
                    is_header=True,
                    has_children=True,
                    is_expanded=is_unified_expanded,
                    tree_key="acc:*",
                ),
                children=unified_children,
                expanded=is_unified_expanded,
            )
        )

        accounts = self.store.list_accounts()
        self._accounts_cache = {acc.id: acc for acc in accounts}
        for acc in accounts:
            acc_folders = self.store.list_folders(acc.id)
            acc_tree_children = self._build_account_folder_tree_nodes(acc.id, acc_folders)
            is_acc_expanded = acc.id not in self.collapsed_accounts
            top_nodes.append(
                TreeNode(
                    label=acc.name.upper(),
                    value=FolderTreeRow(
                        account_id=acc.id,
                        folder_name="",
                        label=acc.name.upper(),
                        icon="☕",
                        is_header=True,
                        badge_color=acc.color_badge,
                        has_children=bool(acc_tree_children),
                        is_expanded=is_acc_expanded,
                        tree_key=f"acc:{acc.id}",
                    ),
                    children=acc_tree_children,
                    expanded=is_acc_expanded,
                )
            )

        prev_row: FolderTreeRow | None = None
        if hasattr(self, "folder_tree"):
            visible_before = self.folder_tree._flatten_visible()
            if 0 <= self.folder_tree.cursor < len(visible_before):
                cand = visible_before[self.folder_tree.cursor][0].value
                if cand:
                    prev_row = cand

        self.folder_tree = Tree(nodes=top_nodes)

        visible_after = self.folder_tree._flatten_visible()
        # 1. If cursor was already on a row (including headers), keep it on that row
        if prev_row is not None:
            for idx, (node, _, _, _) in enumerate(visible_after):
                r = node.value
                if (
                    r
                    and r.account_id == prev_row.account_id
                    and (
                        (r.is_header and prev_row.is_header)
                        or (prev_row.tree_key and r.tree_key == prev_row.tree_key)
                        or (not prev_row.tree_key and r.folder_name == prev_row.folder_name)
                    )
                ):
                    self.folder_tree.cursor = idx
                    return
            # If previous folder became hidden under a collapsed account, land on the account header
            for idx, (node, _, _, _) in enumerate(visible_after):
                r = node.value
                if r and r.is_header and r.account_id == prev_row.account_id:
                    self.folder_tree.cursor = idx
                    return

        # 2. Otherwise align cursor with active_account_id / active_folder
        for idx, (node, _, _, _) in enumerate(visible_after):
            r = node.value
            if r and not r.is_header and r.account_id == self.active_account_id and r.folder_name == self.active_folder:
                self.folder_tree.cursor = idx
                return

        # 3. Fallback to first selectable mailbox row
        for idx, (node, _, _, _) in enumerate(visible_after):
            r = node.value
            if r and not r.is_header and r.selectable_mailbox:
                self.folder_tree.cursor = idx
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
        min_folder_w = 16
        min_list_w = 24
        min_reader_w = 26

        default_folder_w = max(22, min(30, int(total_w * 0.21)))
        folder_w = self.custom_folder_w if self.custom_folder_w is not None else default_folder_w

        default_list_w = max(32, min(46, int(total_w * 0.34)))
        list_w = self.custom_list_w if self.custom_list_w is not None else default_list_w

        folder_w = max(min_folder_w, min(total_w - min_list_w - min_reader_w, folder_w))
        max_list_w = max(min_list_w, total_w - folder_w - min_reader_w)
        list_w = max(min_list_w, min(max_list_w, list_w))
        reader_w = max(min_reader_w, total_w - folder_w - list_w)
        return folder_w, list_w, reader_w

    def _apply_folder_splitter_move(self, mouse_x: int) -> None:
        """Apply dynamic splitter movement to adjust folder list width against reader pane (list width unchanged)."""
        total_w = max(80, self.width)
        min_folder_w = 16
        min_reader_w = 26
        _, current_list_w, _ = self._pane_widths()
        max_folder_w = max(min_folder_w, total_w - current_list_w - min_reader_w)
        desired_folder_w = mouse_x
        self.custom_folder_w = max(min_folder_w, min(max_folder_w, desired_folder_w))
        self._update_reader_viewport(force=True)
        current_folder_w, _, current_reader_w = self._pane_widths()
        self.status_message = (
            f"Resizing folder pane: folders = {current_folder_w} cols, reader = {current_reader_w} cols (list unchanged = {current_list_w})"
        )

    def _apply_splitter_move(self, mouse_x: int) -> None:
        """Apply dynamic splitter movement to adjust list and reader pane widths."""
        total_w = max(80, self.width)
        folder_w, _, _ = self._pane_widths()
        min_list_w = 24
        min_reader_w = 26
        max_list_w = max(min_list_w, total_w - folder_w - min_reader_w)
        desired_list_w = mouse_x - folder_w
        self.custom_list_w = max(min_list_w, min(max_list_w, desired_list_w))
        self._update_reader_viewport(force=True)
        _, current_list_w, current_reader_w = self._pane_widths()
        self.status_message = (
            f"Resizing pane: list = {current_list_w} cols, reader = {current_reader_w} cols"
        )


    def _pane_height(self) -> int:
        # Top menu bar (1 line) + Bottom status bar (1 line)
        return max(10, self.height - 2)

    def _update_reader_viewport(self, force: bool = False) -> None:
        _, _, reader_w = self._pane_widths()
        pane_h = self._pane_height()
        self.reader_viewport.width = max(20, reader_w - 4)
        self.reader_viewport.height = max(6, pane_h - 2)
        self.reader_viewport.style = (
            Style().background(self.theme.bg_primary).foreground(self.theme.fg_primary)
        )

        row = self.selected_message_row
        if row is None:
            self._last_rendered_msg_key = ""
            self.reader_viewport.set_content(
                Style()
                .italic(True)
                .foreground(self.theme.fg_muted)
                .background(self.theme.bg_primary)
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
        acc = self._accounts_cache.get(m.account_id)
        rendered = format_message_for_reading_pane(
            msg=m,
            theme=self.theme,
            width=reader_w - 2,
            image_renderer=self.image_renderer,
            thread=row.thread if self.threading_enabled else None,
            show_raw_headers=self.show_raw_headers,
            account=acc,
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
                        store=self.store,
                    )
                    return self, None
                if any(acc.imap_password for acc in self.store.list_accounts(enabled_only=True)):
                    self.is_syncing = True
                    self.sync_progress_text = "Starting sync..."
                    self.status_message = "⟳ Synchronizing IMAP accounts in background..."
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
                    if msg.account.sieve_host:
                        try:
                            sieve_client = ManageSieveClient(msg.account, timeout=3.0)
                            sieve_client.connect()
                            sieve_client.sync_script_with_server(self.store)
                            sieve_client.close()
                        except Exception:
                            pass
                    self.is_syncing = True
                    self.sync_progress_text = "Starting sync..."
                    self.status_message = (
                        f"✔ Verified and configured account {msg.account.email}! ⟳ Synchronizing in background..."
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

        if isinstance(msg, PreferencesSavedMsg):
            self.active_modal = None
            self.download_dir = Path(msg.download_dir).expanduser().resolve()
            self.theme_engine.set_theme(msg.theme)
            self.image_renderer.set_backend(ImageRenderBackend(msg.image_backend))
            self.threading_enabled = msg.threading
            self.show_raw_headers = msg.show_raw_headers
            self.folder_count_mode = msg.folder_count_mode
            self.fetch_batch_size = msg.fetch_batch_size
            self.refresh_data()
            self.status_message = (
                f"✔ Saved preferences: theme={msg.theme}, renderer={msg.image_backend}, "
                f"batch={msg.fetch_batch_size}, counts={msg.folder_count_mode}"
            )
            return self, None

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
                        store=self.store,
                    )
                    return self, None
                if msg.trigger_sync:
                    self.is_syncing = True
                    self.sync_progress_text = "Starting sync..."
                    self.status_message = "⟳ Synchronizing IMAP accounts in background..."
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
                if isinstance(emitted, (ModalClosedMsg, ComposeSubmittedMsg, SearchSelectMessageMsg, ComposeToContactMsg, PreferencesSavedMsg)):
                    return self.update(emitted)
                if cmd is quit_app or (hasattr(emitted, "__class__") and emitted.__class__.__name__ == "QuitMsg"):
                    return self, quit_app
            return self, cmd

        # Message background remote images loaded
        if isinstance(msg, MessageImagesLoadedMsg):
            if (
                self.selected_message
                and self.selected_message.id == msg.message_id
                and msg.loaded_urls
            ):
                self._update_reader_viewport(force=True)
            return self, None

        if isinstance(msg, FlagSyncedMsg):
            if not msg.success and msg.error:
                self.status_message = f"Warning: could not sync flag to server: {msg.error}"
            return self, None

        # 7. Background Sync progress & steps
        if isinstance(msg, SyncProgressMsg):
            if msg.is_done or not msg.remaining_steps:
                self.is_syncing = False
                self.sync_progress_text = ""
                err_suffix = f" (Warnings: {'; '.join(msg.errors)})" if msg.errors else ""
                summary = (
                    msg.summary
                    or f"✔ Synchronized {msg.total_synced} message(s) across {msg.total_folders} folder(s).{err_suffix}"
                )
                self.status_message = summary
                self.refresh_data()
                return self, tick(180.0, AutoSyncTickMsg())

            self.is_syncing = True
            self.refresh_data()
            next_step = msg.remaining_steps[0]
            remaining = msg.remaining_steps[1:]
            self.sync_progress_text = f"Syncing {msg.account_name} / {next_step[2] or 'folders'}"
            step_idx = msg.step_index + 1
            self.status_message = (
                f"⟳ [{step_idx}/{msg.total_steps}] Syncing {msg.account_name} "
                f"({next_step[2] or 'folders'})..."
            )
            cmd = self._make_sync_step_cmd(
                next_step=next_step,
                remaining_steps=remaining,
                total_synced=msg.total_synced,
                total_folders=msg.total_folders,
                errors=msg.errors,
                step_index=step_idx,
                total_steps=msg.total_steps,
            )
            return self, cmd

        # 7b. Background Sync completion
        if isinstance(msg, SyncCompletedMsg):
            self.is_syncing = False
            self.sync_progress_text = ""
            self.status_message = msg.summary
            self.refresh_data()
            return self, tick(180.0, AutoSyncTickMsg())

        # 7c. Auto-sync periodic tick
        if isinstance(msg, AutoSyncTickMsg):
            if (
                not self.is_syncing
                and not self.show_splash
                and not self.show_onboarding
                and any(acc.imap_password for acc in self.store.list_accounts(enabled_only=True))
            ):
                self.is_syncing = True
                self.sync_progress_text = "Background auto-sync..."
                self.status_message = "⟳ Background auto-sync started..."
                return self, self._sync_accounts_cmd
            return self, tick(180.0, AutoSyncTickMsg())

        # 7b. More messages fetched
        if isinstance(msg, MoreMessagesFetchedMsg):
            self.status_message = msg.message
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
            self.active_modal = ConfirmExitModal(self.theme)
            return self, None
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
        if k == "a":
            return self._execute_action("add_sender_contact")
        if k == "s":
            if self.focused_pane == "folders":
                return self._execute_action("toggle_folder_subscription")
            return self._execute_action("toggle_star")
        if k == "u":
            return self._execute_action("toggle_read")
        if k in ("M", "ctrl+m"):
            return self._execute_action("fetch_more")
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
        if k in ("p", "P"):
            return self._execute_action("preferences")
        if k == "O":
            return self._execute_action("new_account")
        if k == "S":
            return self._execute_action("sync_all")
        if k == "{":
            return self._execute_action("narrow_folder_list")
        if k == "}":
            return self._execute_action("widen_folder_list")
        if k == "[":
            if self.focused_pane == "folders":
                return self._execute_action("narrow_folder_list")
            return self._execute_action("expand_reader")
        if k == "]":
            if self.focused_pane == "folders":
                return self._execute_action("widen_folder_list")
            return self._execute_action("expand_list")

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
            visible = self.folder_tree._flatten_visible()
            if 0 <= self.folder_cursor < len(visible):
                node, _, _, parent = visible[self.folder_cursor]
                row: FolderTreeRow | None = node.value
            else:
                node, parent, row = None, None, None

            if k in ("down", "j"):
                cmd = self._step_folder_cursor(1)
                return self, cmd
            if k in ("up", "k"):
                cmd = self._step_folder_cursor(-1)
                return self, cmd
            if k in ("left", "h"):
                if node is not None and row is not None:
                    if row.is_header:
                        if node.expanded:
                            self._toggle_account_collapse(row.account_id)
                        return self, None
                    if not node.is_leaf and node.expanded:
                        node.expanded = False
                        self._save_expanded_folders()
                        self.status_message = f"Collapsed '{row.label}'."
                    elif parent is not None:
                        for p_idx, (vis_node, _, _, _) in enumerate(visible):
                            if vis_node is parent:
                                self.folder_cursor = p_idx
                                break
                return self, None
            if k in ("right", "l"):
                if node is not None and row is not None:
                    if row.is_header:
                        if not node.expanded:
                            self._toggle_account_collapse(row.account_id)
                        return self, None
                    if not node.is_leaf and not node.expanded:
                        node.expanded = True
                        self._save_expanded_folders()
                        self.status_message = f"Expanded '{row.label}'."
                        return self, None
                self.focused_pane = "messages"
                return self, None
            if k in (" ", "space"):
                if node is not None and row is not None:
                    if row.is_header:
                        self._toggle_account_collapse(row.account_id)
                        return self, None
                    if not node.is_leaf:
                        node.expanded = not node.expanded
                        self._save_expanded_folders()
                        action_str = "Expanded" if node.expanded else "Collapsed"
                        self.status_message = f"{action_str} '{row.label}'."
                        return self, None
                cmd = self._activate_selected_folder()
                return self, cmd
            if k == "enter":
                if node is not None and row is not None:
                    if row.is_header:
                        self._toggle_account_collapse(row.account_id)
                        return self, None
                    if not node.is_leaf and not row.selectable_mailbox:
                        node.expanded = not node.expanded
                        self._save_expanded_folders()
                        action_str = "Expanded" if node.expanded else "Collapsed"
                        self.status_message = f"{action_str} '{row.label}'."
                        return self, None
                    if not node.is_leaf and not node.expanded:
                        node.expanded = True
                        self._save_expanded_folders()
                cmd = self._activate_selected_folder()
                self.focused_pane = "messages"
                return self, cmd
            return self, None

        if self.focused_pane == "messages":
            if k in ("down", "j") and self.message_rows:
                if self.message_cursor >= len(self.message_rows) - 1:
                    self.status_message = "Reached end of messages. Press 'M' to fetch older messages from server."
                    return self, None
                else:
                    self.message_cursor += 1
                    cmd = self._on_message_selection_changed()
                    return self, cmd
            elif k in ("up", "k") and self.message_rows:
                self.message_cursor = max(0, self.message_cursor - 1)
                cmd = self._on_message_selection_changed()
                return self, cmd
            elif k in ("home", "g") and self.message_rows:
                self.message_cursor = 0
                cmd = self._on_message_selection_changed()
                return self, cmd
            elif k in ("end", "G") and self.message_rows:
                self.message_cursor = len(self.message_rows) - 1
                cmd = self._on_message_selection_changed()
                return self, cmd
            elif k in ("enter", " ", "space", "l", "right"):
                m = self.selected_message
                if m and m.is_draft:
                    return self._open_draft_in_composer(m)
                if m and not m.is_read:
                    self.store.mark_read(m.id, True)
                    self.refresh_data()
                self.focused_pane = "reader"
                return self, None
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
        visible = self.folder_tree._flatten_visible()
        for node, _, _, _ in visible:
            r = node.value
            if r is not None and r.tree_key and r.tree_key == row.tree_key:
                node.expanded = not node.expanded
                self._save_expanded_folders()
                action_str = "Expanded" if node.expanded else "Collapsed"
                self.status_message = f"{action_str} '{row.label}'."
                return

    def _handle_mouse(self, msg: MouseMsg) -> tuple[EspressoMailApp, Cmd | None]:
        # Top menu bar row (y == 0)
        if msg.y == 0 and msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
            self.menu_bar, cmd = self.menu_bar.update(msg)
            return self, cmd

        folder_w, list_w, _ = self._pane_widths()
        split_x_1 = folder_w
        split_x_2 = folder_w + list_w
        body_y = msg.y - 2  # Account for menu bar (1) + top pane border (1)

        # 1. Splitter Drag & Double-Click interactions
        if (
            msg.action == MouseAction.PRESS
            and msg.button == MouseButton.LEFT
            and 1 <= msg.y < self.height - 1
        ):
            if abs(msg.x - split_x_1) <= 1:
                self.splitter_dragging = 1
                self._apply_folder_splitter_move(msg.x)
                return self, None
            if abs(msg.x - split_x_2) <= 1:
                self.splitter_dragging = 2
                self._apply_splitter_move(msg.x)
                return self, None

        if msg.action == MouseAction.MOTION:
            if self.splitter_dragging == 1:
                self._apply_folder_splitter_move(msg.x)
                return self, None
            if self.splitter_dragging == 2:
                self._apply_splitter_move(msg.x)
                return self, None
            return self, None

        if msg.action == MouseAction.RELEASE:
            if self.splitter_dragging == 1:
                self.splitter_dragging = 0
                if self.custom_folder_w is not None:
                    self.store.set_setting("custom_folder_w", str(self.custom_folder_w))
                    self.status_message = (
                        f"Splitter resized: folder list width = {self.custom_folder_w} cols."
                    )
                return self, None
            if self.splitter_dragging == 2:
                self.splitter_dragging = 0
                if self.custom_list_w is not None:
                    self.store.set_setting("custom_list_w", str(self.custom_list_w))
                    self.status_message = (
                        f"Splitter resized: message list width = {self.custom_list_w} cols."
                    )
                return self, None
            return self, None

        if (
            msg.action == MouseAction.DOUBLE_CLICK
            and 1 <= msg.y < self.height - 1
        ):
            if abs(msg.x - split_x_1) <= 1:
                self.splitter_dragging = 0
                self.custom_folder_w = None
                self.store.set_setting("custom_folder_w", "")
                self._update_reader_viewport(force=True)
                self.status_message = "Folder list splitter reset to default responsive width."
                return self, None
            if abs(msg.x - split_x_2) <= 1:
                self.splitter_dragging = 0
                self.custom_list_w = None
                self.store.set_setting("custom_list_w", "")
                self._update_reader_viewport(force=True)
                self.status_message = "Message list splitter reset to default responsive width."
                return self, None

        if msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
            self.splitter_dragging = 0

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
                cmd = self._step_folder_cursor(delta)
                return self, cmd
            elif target_pane == "messages" and self.message_rows:
                self.message_cursor = max(0, min(len(self.message_rows) - 1, self.message_cursor + delta))
                cmd = self._on_message_selection_changed()
                return self, cmd
            else:
                self.reader_viewport, _ = self.reader_viewport.update(msg)
            return self, None

        if msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
            self.focused_pane = target_pane
            if target_pane == "folders":
                clicked_idx = self.folder_scroll + body_y
                visible = self.folder_tree._flatten_visible()
                if 0 <= clicked_idx < len(visible):
                    self.folder_cursor = clicked_idx
                    node = visible[clicked_idx][0]
                    clicked_row: FolderTreeRow | None = node.value
                    if clicked_row is not None:
                        if clicked_row.is_header:
                            self._toggle_account_collapse(clicked_row.account_id)
                        elif not node.is_leaf and (
                            not clicked_row.selectable_mailbox or msg.x <= 8 + clicked_row.depth * 4
                        ):
                            self._toggle_folder_expand(clicked_row)
                        else:
                            cmd = self._activate_selected_folder()
                            return self, cmd
            elif target_pane == "messages":
                if body_y >= 0:
                    clicked_idx = self.message_scroll + (body_y // 2)
                    if 0 <= clicked_idx < len(self.message_rows):
                        self.message_cursor = clicked_idx
                        cmd = self._on_message_selection_changed()
                        return self, cmd
            elif target_pane == "reader":
                if body_y >= 0:
                    line_idx = self.reader_viewport.y_offset + body_y
                    all_lines = self.reader_viewport.lines
                    if 0 <= line_idx < len(all_lines):
                        clicked_line = all_lines[line_idx]
                        osc_match = re.search(r"\x1b\]8;;(https?://[^\x1b]+|mailto:[^\x1b]+)\x1b\\", clicked_line)
                        raw_match = re.search(r"(https?://[^\s<>\"']+|mailto:[^\s<>\"']+)", clicked_line)
                        url_to_open = osc_match.group(1) if osc_match else (raw_match.group(1) if raw_match else None)
                        if url_to_open:
                            try:
                                import webbrowser
                                webbrowser.open(url_to_open)
                                self.status_message = f"Opening link: {url_to_open}"
                            except Exception as exc:
                                self.status_message = f"Failed to open link: {exc}"
                            return self, None
            return self, None

        return self, None

    def _step_folder_cursor(self, direction: int) -> Cmd | None:
        visible = self.folder_tree._flatten_visible()
        if not visible:
            return None
        self.folder_cursor = (self.folder_cursor + direction) % len(visible)
        node = visible[self.folder_cursor][0]
        row: FolderTreeRow | None = node.value
        if row:
            if row.is_header:
                state_str = "unfolded" if node.expanded else "folded"
                self.status_message = f"Account '{row.label}' ({state_str} — Space/Enter to toggle)"
                return None
            elif row.selectable_mailbox:
                return self._activate_selected_folder()
            else:
                self.status_message = (
                    f"Folder group '{row.label}' ({row.total_count} messages — Space/Enter to toggle)"
                )
        return None

    def _make_single_folder_sync_cmd(self, acc: AccountConfig, folder_name: str) -> Cmd:
        def _runner() -> Msg:
            try:
                client = IMAPClient(acc, timeout=20.0)
                client.connect()
                try:
                    count = client.sync_single_folder(
                        self.store,
                        folder_name=folder_name,
                        limit=self.fetch_batch_size,
                    )
                    return MoreMessagesFetchedMsg(
                        account_id=acc.id,
                        folder=folder_name,
                        fetched_count=count,
                        message=f"Synced folder {folder_name} ({count} new messages)",
                    )
                finally:
                    client.disconnect()
            except Exception as exc:
                return MoreMessagesFetchedMsg(
                    account_id=acc.id,
                    folder=folder_name,
                    fetched_count=0,
                    message=f"Could not sync {folder_name}: {exc}",
                )

        return _runner

    def _activate_selected_folder(self) -> Cmd | None:
        if 0 <= self.folder_cursor < len(self.folder_rows):
            row = self.folder_rows[self.folder_cursor]
            if not row.is_header and row.selectable_mailbox:
                self.active_account_id = row.account_id
                self.active_folder = row.folder_name
                self.message_cursor = 0
                self.message_scroll = 0
                self._rebuild_message_rows()
                cmd = self._on_message_selection_changed()
                path_label = (
                    row.label
                    if row.account_id == "*"
                    else " / ".join(split_folder_hierarchy(row.folder_name))
                )
                self.status_message = f"Opened {path_label} ({len(self.message_rows)} items)"
                if row.account_id != "*" and not row.subscribed:
                    acc = self.store.get_account(row.account_id)
                    if acc and acc.imap_password:
                        sync_cmd = self._make_single_folder_sync_cmd(acc, row.folder_name)
                        if cmd is not None:
                            def _combined_cmd() -> Msg:
                                c_res = cmd() if callable(cmd) else None
                                s_res = sync_cmd() if callable(sync_cmd) else None
                                return s_res or c_res

                            return _combined_cmd
                        return sync_cmd
                return cmd
        return None

    def _get_pending_image_urls(self, msg: EmailMessage | None) -> list[str]:
        """Extract remote image URLs from message HTML body that are not yet cached in memory."""
        if not msg or not msg.body_html or self.image_renderer.backend == ImageRenderBackend.OFF:
            return []
        images = extract_html_images(msg.body_html)
        urls: list[str] = []
        for src, _ in images:
            clean = re.sub(r"[\r\n\t]+", "", (src or "").strip())
            if clean.startswith("//"):
                clean = "https:" + clean
            if clean.lower().startswith(("http://", "https://")):
                if clean not in _REMOTE_IMAGE_CACHE:
                    urls.append(clean)
        return urls

    def _load_message_images_cmd(self, message_id: str, urls: list[str]) -> Cmd:
        """Create a background TEA command to download remote image bytes into _REMOTE_IMAGE_CACHE."""
        def _worker() -> Msg:
            loaded: list[str] = []
            for u in urls:
                if u not in _REMOTE_IMAGE_CACHE:
                    data = _fetch_remote_image_bytes(u)
                    _REMOTE_IMAGE_CACHE[u] = data
                    if data:
                        loaded.append(u)
            return MessageImagesLoadedMsg(message_id=message_id, loaded_urls=tuple(loaded))

        return _worker

    def _make_flag_sync_cmd(
        self, account_id: str, folder: str, uid: int, flag: str, enable: bool
    ) -> Cmd | None:
        r"""Create a background TEA command to sync an IMAP flag (\Seen or \Flagged) to the remote server."""
        if not account_id or account_id == "*" or uid <= 0:
            return None
        acc = self.store.get_account(account_id)
        if not acc or not acc.imap_password:
            return None

        def _runner() -> Msg:
            try:
                with IMAPClient(acc, timeout=15.0) as client:
                    client.set_flag(folder, uid, flag, enable)
                return FlagSyncedMsg(
                    account_id=account_id,
                    folder=folder,
                    uid=uid,
                    flag=flag,
                    enabled=enable,
                    success=True,
                )
            except Exception as exc:
                return FlagSyncedMsg(
                    account_id=account_id,
                    folder=folder,
                    uid=uid,
                    flag=flag,
                    enabled=enable,
                    success=False,
                    error=str(exc),
                )

        return _runner

    def _on_message_selection_changed(self) -> Cmd | None:
        m = self.selected_message
        sync_cmd = None
        if m and not m.is_read:
            self.store.mark_read(m.id, True)
            m.is_read = True
            self._rebuild_folder_rows()
            sync_cmd = self._make_flag_sync_cmd(m.account_id, m.folder, m.uid, r"\Seen", True)
        self._update_reader_viewport(force=True)
        if m:
            pending = self._get_pending_image_urls(m)
            if pending:
                img_cmd = self._load_message_images_cmd(m.id, pending)
                if sync_cmd is not None:
                    def _combined() -> Msg:
                        sync_cmd()
                        return img_cmd()
                    return _combined
                return img_cmd
        return sync_cmd

    # ------------------------------------------------------------------
    # Application Actions
    # ------------------------------------------------------------------

    def _execute_action(self, action_id: str) -> tuple[EspressoMailApp, Cmd | None]:
        if action_id == "quit_app":
            self.active_modal = ConfirmExitModal(self.theme)
            return self, None

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

        if action_id == "preferences":
            self.active_modal = PreferencesModal(
                store=self.store,
                current_theme=self.theme,
                download_dir=self.download_dir,
                current_image_backend=self.image_renderer.backend.value,
                threading_enabled=self.threading_enabled,
                show_raw_headers=self.show_raw_headers,
            )
            return self, None

        if action_id == "new_account":
            self.show_onboarding = True
            self.onboarding = OnboardingWizard(
                self.theme,
                allow_cancel=True,
                credential_validator=self.credential_validator,
                store=self.store,
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

        if action_id == "widen_folder_list":
            total_w = max(80, self.width)
            folder_w, list_w, _ = self._pane_widths()
            min_reader_w = 26
            max_folder_w = max(16, total_w - list_w - min_reader_w)
            new_folder_w = min(max_folder_w, folder_w + 2)
            self.custom_folder_w = new_folder_w
            self.store.set_setting("custom_folder_w", str(self.custom_folder_w))
            self._update_reader_viewport(force=True)
            self.status_message = f"Splitter moved: folder list widened to {new_folder_w} cols (space taken from reader)."
            return self, None

        if action_id == "narrow_folder_list":
            folder_w, _, _ = self._pane_widths()
            min_folder_w = 16
            new_folder_w = max(min_folder_w, folder_w - 2)
            self.custom_folder_w = new_folder_w
            self.store.set_setting("custom_folder_w", str(self.custom_folder_w))
            self._update_reader_viewport(force=True)
            self.status_message = f"Splitter moved: folder list narrowed to {new_folder_w} cols (space given to reader)."
            return self, None

        if action_id == "expand_reader":
            folder_w, list_w, _ = self._pane_widths()
            min_list_w = 24
            new_list_w = max(min_list_w, list_w - 3)
            self.custom_list_w = new_list_w
            self.store.set_setting("custom_list_w", str(self.custom_list_w))
            self._update_reader_viewport(force=True)
            _, _, new_reader_w = self._pane_widths()
            self.status_message = f"Splitter moved: reading pane expanded to {new_reader_w} cols ([ / ] to adjust)."
            return self, None

        if action_id == "expand_list":
            total_w = max(80, self.width)
            folder_w, list_w, _ = self._pane_widths()
            min_list_w = 24
            min_reader_w = 24
            max_list_w = max(min_list_w, total_w - folder_w - min_reader_w)
            new_list_w = min(max_list_w, list_w + 3)
            self.custom_list_w = new_list_w
            self.store.set_setting("custom_list_w", str(self.custom_list_w))
            self._update_reader_viewport(force=True)
            self.status_message = f"Splitter moved: message list expanded to {new_list_w} cols ([ / ] to adjust)."
            return self, None

        if action_id == "reset_splitter":
            self.custom_list_w = None
            self.custom_folder_w = None
            self.store.set_setting("custom_list_w", "")
            self.store.set_setting("custom_folder_w", "")
            self._update_reader_viewport(force=True)
            self.status_message = "Splitters reset to default responsive widths."
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
                m.is_starred = new_star
                self.refresh_data()
                self.status_message = "★ Starred message." if new_star else "Removed star."
                cmd = self._make_flag_sync_cmd(m.account_id, m.folder, m.uid, r"\Flagged", new_star)
                return self, cmd
            return self, None

        if action_id == "add_sender_contact":
            m = self.selected_message
            if not m or not m.sender_email:
                self.status_message = "No message selected or sender has no email."
                return self, None
            existing = self.store.get_contact_by_email(m.sender_email)
            if existing:
                if m.sender_name and not existing.name:
                    existing.name = m.sender_name
                existing.frequency += 1
                self.store.save_contact(existing)
                self.status_message = f"Updated contact: {existing.formatted}"
            else:
                new_contact = Contact(
                    id=f"contact:{uuid.uuid4().hex[:10]}",
                    name=m.sender_name or m.sender_email.split("@")[0],
                    email=m.sender_email,
                    frequency=1,
                )
                self.store.save_contact(new_contact)
                self.status_message = f"Added contact: {new_contact.formatted}"
            return self, None

        if action_id == "toggle_folder_subscription":
            if 0 <= self.folder_cursor < len(self.folder_rows):
                row = self.folder_rows[self.folder_cursor]
                if row.account_id != "*" and not row.is_header:
                    new_sub = self.store.toggle_folder_subscription(row.account_id, row.folder_name)
                    self.refresh_data()
                    state_str = "Subscribed to" if new_sub else "Unsubscribed from"
                    self.status_message = f"{state_str} '{row.label}'"
                else:
                    self.status_message = "Cannot toggle subscription for this item."
            return self, None

        if action_id == "toggle_read":
            m = self.selected_message
            if m:
                new_read = not m.is_read
                self.store.mark_read(m.id, new_read)
                m.is_read = new_read
                self.refresh_data()
                self.status_message = "Marked as read." if new_read else "Marked as unread."
                cmd = self._make_flag_sync_cmd(m.account_id, m.folder, m.uid, r"\Seen", new_read)
                return self, cmd
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
            self.active_modal = DownloadAttachmentsModal(
                attachments=m.attachments,
                download_dir=self.download_dir,
                theme=self.theme,
                store=self.store,
            )
            return self, None

        if action_id == "fetch_more":
            self.status_message = "Fetching older messages from server..."
            return self, self._fetch_more_messages_cmd

        if action_id == "sync_all":
            if self.is_syncing:
                self.status_message = "⟳ Synchronization already in progress in background."
                return self, None
            self.is_syncing = True
            self.sync_progress_text = "Starting sync..."
            self.status_message = "⟳ Synchronizing IMAP accounts in background..."
            return self, self._sync_accounts_cmd

        return self, None

    def _open_reply_or_forward(
        self, msg: EmailMessage, mode: str
    ) -> tuple[EspressoMailApp, Cmd | None]:
        quoted_lines = "\n".join(f"> {line}" for line in msg.body_text.splitlines())
        header_intro = f"\n\nOn {msg.date_iso or msg.short_date}, {msg.sender_display} wrote:\n{quoted_lines}"

        acc = self.store.get_account(msg.account_id)
        sig = f"\n\n-- \n{acc.signature}" if acc and acc.signature else ""
        body_init = f"\n{sig}\n{header_intro}" if sig else header_intro

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
            body_initial=body_init,
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
        cmd = self._on_message_selection_changed()
        self.focused_pane = "reader"
        self.status_message = f"Jumped to '{target.subject[:40]}'"
        return self, cmd

    def _fetch_more_messages_cmd(self) -> Msg:
        accounts = self.store.list_accounts(enabled_only=True)
        if self.active_account_id == "*":
            target_accounts = [acc for acc in accounts if acc.imap_password]
            target_folder = "INBOX"
        else:
            acc = self.store.get_account(self.active_account_id)
            target_accounts = [acc] if acc and acc.imap_password else []
            target_folder = self.active_folder

        if not target_accounts:
            return MoreMessagesFetchedMsg(
                account_id=self.active_account_id,
                folder=self.active_folder,
                fetched_count=0,
                message="⚠ Cannot fetch messages: account has no IMAP password configured.",
            )

        total_fetched = 0
        errors: list[str] = []
        for acc in target_accounts:
            try:
                min_uid = self.store.get_folder_min_uid(acc.id, target_folder)
                client = IMAPClient(acc, timeout=20.0)
                client.connect()
                try:
                    fetched = client.fetch_recent_messages(
                        folder=target_folder,
                        limit=self.fetch_batch_size,
                        before_uid=min_uid if min_uid > 0 else None,
                    )
                    if fetched:
                        for m in fetched:
                            self.store.save_message(m)
                        self.store.refresh_folder_counts()
                        total_fetched += len(fetched)
                finally:
                    client.disconnect()
            except Exception as exc:
                errors.append(f"{acc.email}: {exc}")

        err_suffix = f" (Errors: {'; '.join(errors)})" if errors else ""
        if total_fetched > 0:
            return MoreMessagesFetchedMsg(
                account_id=self.active_account_id,
                folder=self.active_folder,
                fetched_count=total_fetched,
                message=f"✔ Fetched {total_fetched} older message(s) from server.{err_suffix}",
            )
        return MoreMessagesFetchedMsg(
            account_id=self.active_account_id,
            folder=self.active_folder,
            fetched_count=0,
            message=f"No older messages found on server for {target_folder}.{err_suffix}",
        )

    def _make_sync_step_cmd(
        self,
        next_step: tuple[str, str, str],
        remaining_steps: tuple[tuple[str, str, str], ...],
        total_synced: int,
        total_folders: int,
        errors: tuple[str, ...],
        step_index: int,
        total_steps: int,
    ) -> Cmd:
        def _sync_step_runner() -> Msg:
            try:
                acc_id, action, folder_name = next_step
                acc = self.store.get_account(acc_id)
                if not acc or not acc.imap_password:
                    if remaining_steps:
                        next_s = remaining_steps[0]
                        rem_s = remaining_steps[1:]
                        return self._make_sync_step_cmd(
                            next_step=next_s,
                            remaining_steps=rem_s,
                            total_synced=total_synced,
                            total_folders=total_folders,
                            errors=errors,
                            step_index=step_index + 1,
                            total_steps=total_steps,
                        )()
                    return SyncProgressMsg(
                        account_id=acc_id,
                        account_name=acc.name if acc else acc_id,
                        action=action,
                        folder_name=folder_name,
                        new_messages=0,
                        total_synced=total_synced,
                        total_folders=total_folders,
                        step_index=step_index,
                        total_steps=total_steps,
                        remaining_steps=(),
                        errors=errors,
                        is_done=True,
                    )

                new_errors = list(errors)
                step_synced = 0
                discovered_count = 0
                new_remaining = list(remaining_steps)
                new_total_steps = total_steps

                if action == "discover":
                    try:
                        client = IMAPClient(acc, timeout=20.0)
                        client.connect()
                        try:
                            remote_folders = client.list_remote_folders()
                            if remote_folders:
                                self.store.sync_remote_folders(acc.id, remote_folders)
                                discovered_count = len(remote_folders)
                            if remote_folders:
                                local_map = {f.name: f for f in self.store.list_folders(acc.id)}
                                cand_folders = [local_map.get(f.name, f) for f in remote_folders]
                            else:
                                cand_folders = self.store.list_folders(acc.id)
                            selectable = [f for f in cand_folders if f.selectable and f.subscribed]
                            sorted_folders = sorted(
                                selectable,
                                key=lambda f: (
                                    0 if f.name.upper() == "INBOX" else 1,
                                    len(f.path_parts),
                                    f.name.lower(),
                                ),
                            )
                            folder_steps = [(acc.id, "sync_folder", f.name) for f in sorted_folders]
                            new_remaining = folder_steps + new_remaining
                            new_total_steps = total_steps + len(folder_steps)
                        finally:
                            client.disconnect()
                    except Exception as exc:
                        new_errors.append(f"{acc.email}: {exc}")

                    return SyncProgressMsg(
                        account_id=acc.id,
                        account_name=acc.name,
                        action="discovered_folders",
                        folder_name="",
                        new_messages=0,
                        total_synced=total_synced,
                        total_folders=total_folders + discovered_count,
                        step_index=step_index,
                        total_steps=new_total_steps,
                        remaining_steps=tuple(new_remaining),
                        errors=tuple(new_errors),
                    )

                elif action == "sync_folder":
                    try:
                        client = IMAPClient(acc, timeout=20.0)
                        client.connect()
                        try:
                            step_synced = client.sync_single_folder(
                                self.store,
                                folder_name=folder_name,
                                limit=self.fetch_batch_size,
                            )
                        finally:
                            client.disconnect()
                    except Exception as exc:
                        new_errors.append(f"{acc.email} [{folder_name}]: {exc}")

                    return SyncProgressMsg(
                        account_id=acc.id,
                        account_name=acc.name,
                        action="synced_folder",
                        folder_name=folder_name,
                        new_messages=step_synced,
                        total_synced=total_synced + step_synced,
                        total_folders=total_folders,
                        step_index=step_index,
                        total_steps=total_steps,
                        remaining_steps=tuple(new_remaining),
                        errors=tuple(new_errors),
                    )

                return SyncProgressMsg(
                    account_id=acc.id,
                    account_name=acc.name,
                    action="done",
                    remaining_steps=(),
                    total_synced=total_synced,
                    total_folders=total_folders,
                    errors=tuple(new_errors),
                    is_done=True,
                )
            except Exception as exc:
                return SyncCompletedMsg(
                    synced_count=total_synced,
                    summary=f"Sync finished with notice: {exc}",
                )

        return _sync_step_runner

    def _sync_accounts_cmd(self) -> Msg:
        try:
            accounts = [acc for acc in self.store.list_accounts(enabled_only=True) if acc.imap_password]
            if not accounts:
                return SyncCompletedMsg(
                    synced_count=0,
                    summary=f"✔ Local SQLite store up to date ({len(self.store.list_accounts())} accounts cached for offline access).",
                )

            steps = tuple((acc.id, "discover", "") for acc in accounts)
            first_step = steps[0]
            remaining = steps[1:]
            step_cmd = self._make_sync_step_cmd(
                next_step=first_step,
                remaining_steps=remaining,
                total_synced=0,
                total_folders=0,
                errors=(),
                step_index=1,
                total_steps=len(steps),
            )
            return step_cmd()
        except Exception as exc:
            return SyncCompletedMsg(
                synced_count=0,
                summary=f"Sync finished: {exc}",
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
        right_info = f"🖼️ {self.image_renderer.backend.value}"
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
            try:
                modal_str = self.active_modal.view(self.width, self.height)  # type: ignore[call-arg]
            except TypeError:
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

        visible = self.folder_tree._flatten_visible()

        if self.folder_cursor < self.folder_scroll:
            self.folder_scroll = self.folder_cursor
        elif self.folder_cursor >= self.folder_scroll + inner_h:
            self.folder_scroll = self.folder_cursor - inner_h + 1

        visible_folders = visible[self.folder_scroll : self.folder_scroll + inner_h]
        for offset, (node, depth, prefix, _) in enumerate(visible_folders):
            idx = self.folder_scroll + offset
            row: FolderTreeRow | None = node.value
            if row is None:
                continue

            if row.is_header:
                hdr_color = row.badge_color or self.theme.accent_secondary
                is_selected = idx == self.folder_cursor
                ind = "▼" if node.expanded else "▶"
                hdr_txt = truncate_ansi(f" {ind} {row.label}", inner_w)
                if is_selected and self.focused_pane == "folders":
                    lines.append(
                        Style()
                        .bold(True)
                        .foreground(self.theme.bg_primary)
                        .background(self.theme.accent_primary)
                        .width(inner_w)
                        .render(hdr_txt)
                    )
                elif is_selected:
                    lines.append(
                        Style()
                        .bold(True)
                        .foreground(hdr_color)
                        .background(self.theme.bg_highlight)
                        .width(inner_w)
                        .render(hdr_txt)
                    )
                else:
                    lines.append(
                        Style()
                        .bold(True)
                        .foreground(hdr_color)
                        .background(self.theme.bg_secondary)
                        .width(inner_w)
                        .render(hdr_txt)
                    )
                continue

            is_selected = idx == self.folder_cursor
            is_active = (
                row.account_id == self.active_account_id and row.folder_name == self.active_folder
            )

            if self.folder_count_mode == "unread_only":
                count_badge = f"({row.unread_count})" if row.unread_count > 0 else ""
            else:
                if row.unread_count > 0:
                    count_badge = (
                        f"({row.unread_count}) {row.total_count}"
                        if row.total_count > 0
                        else f"({row.unread_count})"
                    )
                else:
                    count_badge = f"{row.total_count}" if row.total_count > 0 else ""

            # Standard box-drawing branch prefix from espresso.beans.tree.Tree
            branch_prefix = prefix[4:] if depth > 0 else prefix
            tree_prefix = Style().foreground(self.theme.fg_muted).render(branch_prefix)

            # Folders with subfolders use 🗂️ when closed, 📂 when open; leaf folders use their own icon
            if not node.is_leaf:
                folder_icon = "📂" if node.expanded else "🗂️"
            else:
                folder_icon = row.icon

            unsub_badge = " 🔕" if (not getattr(row, "subscribed", True) and row.account_id != "*") else ""
            lbl_raw = f" {tree_prefix}{folder_icon} {row.label}{unsub_badge}"
            lbl_display = f" {branch_prefix}{folder_icon} {row.label}{unsub_badge}"

            pad = max(1, inner_w - string_width(lbl_display) - len(count_badge) - 1)
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
            elif is_selected:
                lines.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.accent_secondary)
                    .background(self.theme.bg_highlight)
                    .width(inner_w)
                    .render(line_str)
                )
            elif is_active:
                lines.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.accent_secondary)
                    .background(self.theme.bg_secondary)
                    .width(inner_w)
                    .render(line_str)
                )
            elif row.unread_count > 0:
                lines.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.fg_primary)
                    .background(self.theme.bg_secondary)
                    .width(inner_w)
                    .render(line_str)
                )
            else:
                lines.append(
                    Style()
                    .foreground(self.theme.fg_secondary)
                    .background(self.theme.bg_secondary)
                    .width(inner_w)
                    .render(line_str)
                )

        return self.theme.pane_style(
            focused=(self.focused_pane == "folders"),
            width=width,
            height=height,
            title="📁 Mailboxes",
        ).render("\n".join(lines))

    def _render_message_list_pane(self, width: int, height: int) -> str:
        inner_w = max(18, width - 4)
        inner_h = max(4, height - 2)

        # Keep selected message visible in scroll window (2 lines per message)
        items_per_page = max(1, inner_h // 2)
        if self.message_cursor < self.message_scroll:
            self.message_scroll = self.message_cursor
        elif self.message_cursor >= self.message_scroll + items_per_page:
            self.message_scroll = self.message_cursor - items_per_page + 1

        visible = self.message_rows[self.message_scroll : self.message_scroll + items_per_page]
        lines: list[str] = []

        if not visible:
            lines.append(
                Style()
                .italic(True)
                .foreground(self.theme.fg_muted)
                .background(self.theme.bg_secondary)
                .width(inner_w)
                .render("  (No messages in folder)")
            )
        else:
            is_unified = self.active_account_id == "*"
            for offset, row in enumerate(visible):
                abs_idx = self.message_scroll + offset
                m = row.message
                is_sel = abs_idx == self.message_cursor

                unread_ch = "●" if not m.is_read else " "
                star_ch = "★" if m.is_starred else " "
                att_ch = "📎 " if m.has_attachments else ""
                pgp_ch = "🔒 " if m.pgp_status != PGPStatus.NONE else ""

                tree_prefix = ""
                if self.threading_enabled and row.depth > 0:
                    tree_prefix = ("  " * (row.depth - 1)) + "╰─ "

                thread_cnt = (
                    f"[{row.thread.message_count}] "
                    if (self.threading_enabled and row.is_thread_root and row.thread.message_count > 1)
                    else ""
                )

                acc_prefix = ""
                acc_prefix_len = 0
                if is_unified:
                    acc = self._accounts_cache.get(m.account_id)
                    b_color = (
                        acc.color_badge
                        if acc and acc.color_badge
                        else self.theme.accent_secondary
                    )
                    acc_short = (acc.name if acc else m.account_id)[:6]
                    acc_prefix = Style().bold(True).foreground(b_color).render(f"[{acc_short}] ")
                    acc_prefix_len = len(f"[{acc_short}] ")

                right_meta = f"{thread_cnt}{att_ch}{pgp_ch}{m.short_date}".strip()
                right_w = string_width(right_meta)

                fixed_left_w = 3 + acc_prefix_len + len(tree_prefix)
                avail_sender = max(6, inner_w - right_w - fixed_left_w - 1)
                sender_txt = truncate_ansi(m.sender_short, avail_sender)
                left_line = f"{unread_ch}{star_ch} {acc_prefix}{tree_prefix}{sender_txt}"

                space_count = max(1, inner_w - string_width(left_line) - right_w)
                line1_raw = f"{left_line}{' ' * space_count}{right_meta}"
                line1 = truncate_ansi(line1_raw, inner_w)

                # Line 2: Subject indented cleanly with maximum available width
                tree_indent = " " * min(4, row.depth * 2) if self.threading_enabled else ""
                subj_indent = f"   {tree_indent}"
                subj_avail = max(6, inner_w - len(subj_indent))
                subj_txt = truncate_ansi(m.subject or "(No Subject)", subj_avail)
                line2_raw = f"{subj_indent}{subj_txt}"
                line2 = truncate_ansi(line2_raw, inner_w)

                if is_sel and self.focused_pane == "messages":
                    lines.append(
                        Style()
                        .bold(True)
                        .foreground(self.theme.bg_primary)
                        .background(self.theme.accent_primary)
                        .width(inner_w)
                        .render(line1)
                    )
                    lines.append(
                        Style()
                        .foreground(self.theme.bg_primary)
                        .background(self.theme.accent_primary)
                        .width(inner_w)
                        .render(line2)
                    )
                elif is_sel:
                    lines.append(
                        Style()
                        .bold(True)
                        .foreground(self.theme.accent_secondary)
                        .background(self.theme.bg_highlight)
                        .width(inner_w)
                        .render(line1)
                    )
                    lines.append(
                        Style()
                        .foreground(self.theme.fg_primary)
                        .background(self.theme.bg_highlight)
                        .width(inner_w)
                        .render(line2)
                    )
                elif not m.is_read:
                    lines.append(
                        Style()
                        .bold(True)
                        .foreground(self.theme.unread_fg)
                        .background(self.theme.bg_secondary)
                        .width(inner_w)
                        .render(line1)
                    )
                    lines.append(
                        Style()
                        .bold(True)
                        .foreground(self.theme.fg_primary)
                        .background(self.theme.bg_secondary)
                        .width(inner_w)
                        .render(line2)
                    )
                else:
                    lines.append(
                        Style()
                        .foreground(self.theme.fg_primary)
                        .background(self.theme.bg_secondary)
                        .width(inner_w)
                        .render(line1)
                    )
                    lines.append(
                        Style()
                        .foreground(self.theme.fg_muted)
                        .background(self.theme.bg_secondary)
                        .width(inner_w)
                        .render(line2)
                    )

            if len(lines) < inner_h:
                fetch_hint = truncate_ansi("  ─── [M] Fetch more from server ───", inner_w)
                lines.append(
                    Style()
                    .foreground(self.theme.fg_muted)
                    .background(self.theme.bg_secondary)
                    .width(inner_w)
                    .render(fetch_hint)
                )

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
        inner_h = max(4, height - 2)
        inner_w = max(18, width - 4)
        if self.reader_viewport.height != inner_h or self.reader_viewport.width != inner_w:
            self.reader_viewport.height = inner_h
            self.reader_viewport.width = inner_w
        self.reader_viewport.style = (
            Style().background(self.theme.bg_primary).foreground(self.theme.fg_primary)
        )
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
            bg=self.theme.bg_primary,
        ).render(self.reader_viewport.view())

    def _render_status_bar(self) -> str:
        pane_badge = self.focused_pane.upper()
        left_sections = [
            StatusSection(
                f" {pane_badge} ",
                style=Style()
                .bold(True)
                .foreground(self.theme.bg_primary)
                .background(self.theme.accent_primary),
                priority=3,
            ),
        ]
        if self.is_syncing:
            left_sections.append(
                StatusSection(
                    " ⟳ SYNCING ",
                    style=Style()
                    .bold(True)
                    .foreground(self.theme.bg_primary)
                    .background(self.theme.accent_secondary),
                    priority=4,
                )
            )
        left_sections.append(
            StatusSection(
                f" {self.status_message} ",
                style=Style().foreground(self.theme.fg_primary),
                priority=2,
            )
        )
        bar = StatusBar(
            left=left_sections,
            center=[
                StatusSection(
                    "[c]Compose [/]Search [r]Reply [t]Threads [i]Img [?]Help",
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
