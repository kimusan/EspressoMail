"""Configurable keyboard shortcuts and keymap presets for EspressoMail.

Provides built-in presets (default, gmail, vim, pine) and customizable user keybindings
with support for persistence via SQLite settings and optional JSON files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from espressomail.storage import LocalStore

KEYMAP_PRESETS: tuple[str, ...] = ("default", "gmail", "vim", "pine")

ACTION_DESCRIPTIONS: dict[str, str] = {
    "compose": "Compose new mail",
    "reply": "Reply to sender",
    "reply_all": "Reply all recipients",
    "forward": "Forward message",
    "archive_msg": "Archive message (single-key triage)",
    "trash_msg": "Move message to Trash",
    "spam_msg": "Move message to Spam/Junk",
    "move_to_folder": "Move message to specific folder",
    "toggle_star": "Toggle Star/Flag on message",
    "toggle_read": "Toggle Read/Unread status",
    "toggle_unread_filter": "Toggle Unread-only message filter",
    "select_message_toggle": "Multi-select toggle message for batch actions",
    "fetch_more": "Fetch older messages from IMAP",
    "sync_folder": "Synchronize active folder / mailbox with IMAP server",
    "sync_all": "Synchronize all accounts with IMAP server",
    "fuzzy_search": "Instant fuzzy search across emails",
    "empty_trash": "Permanently empty trash folder",
    "save_attachments": "Save message attachments to disk",
    "toggle_threading": "Toggle conversation threading mode",
    "cycle_image_mode": "Cycle terminal image renderer",
    "toggle_headers": "Toggle full raw email headers",
    "unified_inbox": "Jump to Unified Inbox across all accounts",
    "address_book": "Open local address book contacts",
    "sieve_filters": "Manage Sieve mail filtering rules",
    "pgp_manager": "Open PGP keyring and key manager",
    "account_settings": "Open account settings and credentials",
    "preferences": "Open general preferences and configuration",
    "new_account": "Add new email account wizard",
    "show_shortcuts": "Show keyboard and mouse shortcut reference",
    "quit_app": "Quit EspressoMail",
    "open_menu": "Open top drop-down menu bar",
    "replay_splash": "Replay startup splash screen",
    "toggle_folder_subscription": "Toggle folder IMAP subscription",
    "narrow_folder_list": "Narrow folder list pane width",
    "widen_folder_list": "Widen folder list pane width",
    "expand_reader": "Expand reading pane",
    "expand_list": "Expand message list pane",
    "next_pane": "Focus next pane (Folders -> Messages -> Reader)",
    "prev_pane": "Focus previous pane",
}

# Default EspressoMail keybindings
DEFAULT_BINDINGS: dict[str, str] = {
    "c": "compose",
    "r": "reply",
    "R": "reply_all",
    "f": "forward",
    "a": "add_sender_contact",
    "e": "archive_msg",
    "y": "archive_msg",
    "d": "trash_msg",
    "#": "trash_msg",
    "delete": "trash_msg",
    "J": "spam_msg",
    "v": "move_to_folder",
    "x": "select_message_toggle",
    "s": "toggle_star",
    "u": "toggle_read",
    "ctrl+u": "toggle_unread_filter",
    "!": "toggle_unread_filter",
    "V": "toggle_unread_filter",
    "/": "fuzzy_search",
    "S": "sync_all",
    "ctrl+r": "sync_folder",
    "f5": "sync_folder",
    "M": "fetch_more",
    "ctrl+m": "fetch_more",
    "X": "empty_trash",
    "w": "save_attachments",
    "t": "toggle_threading",
    "i": "cycle_image_mode",
    "H": "toggle_headers",
    "U": "unified_inbox",
    "B": "address_book",
    "F": "sieve_filters",
    "G": "pgp_manager",
    "A": "account_settings",
    "p": "preferences",
    "P": "preferences",
    "O": "new_account",
    "?": "show_shortcuts",
    "q": "quit_app",
    "f1": "replay_splash",
    "f10": "open_menu",
    "m": "open_menu",
    "tab": "next_pane",
    "shift+tab": "prev_pane",
    "{": "narrow_folder_list",
    "}": "widen_folder_list",
    "[": "expand_reader",
    "]": "expand_list",
}

# Gmail style preset
GMAIL_BINDINGS: dict[str, str] = {
    "c": "compose",
    "r": "reply",
    "a": "reply_all",
    "f": "forward",
    "e": "archive_msg",
    "y": "archive_msg",
    "#": "trash_msg",
    "!": "spam_msg",
    "v": "move_to_folder",
    "x": "select_message_toggle",
    "s": "toggle_star",
    "shift+u": "toggle_read",
    "shift+i": "toggle_read",
    "u": "toggle_read",
    "/": "fuzzy_search",
    "U": "unified_inbox",
    "S": "sync_all",
    "ctrl+r": "sync_folder",
    "f5": "sync_folder",
    "q": "quit_app",
    "?": "show_shortcuts",
    "tab": "next_pane",
    "shift+tab": "prev_pane",
}

# Vim style preset
VIM_BINDINGS: dict[str, str] = {
    "c": "compose",
    "r": "reply",
    "R": "reply_all",
    "f": "forward",
    "y": "archive_msg",
    "d": "trash_msg",
    "x": "select_message_toggle",
    "m": "move_to_folder",
    "J": "spam_msg",
    "*": "toggle_star",
    "s": "toggle_star",
    "u": "toggle_read",
    "/": "fuzzy_search",
    "S": "sync_all",
    "ctrl+r": "sync_folder",
    "f5": "sync_folder",
    "q": "quit_app",
    "?": "show_shortcuts",
    "tab": "next_pane",
    "shift+tab": "prev_pane",
    "[": "expand_reader",
    "]": "expand_list",
}

# Pine / Mutt style preset
PINE_BINDINGS: dict[str, str] = {
    "c": "compose",
    "r": "reply",
    "g": "reply_all",
    "f": "forward",
    "d": "trash_msg",
    "a": "archive_msg",
    "s": "move_to_folder",
    "*": "toggle_star",
    "u": "toggle_read",
    "x": "select_message_toggle",
    "w": "fuzzy_search",
    "/": "fuzzy_search",
    "S": "sync_all",
    "ctrl+r": "sync_folder",
    "f5": "sync_folder",
    "$": "sync_folder",
    "q": "quit_app",
    "?": "show_shortcuts",
    "tab": "next_pane",
    "shift+tab": "prev_pane",
}

PRESET_MAPS: dict[str, dict[str, str]] = {
    "default": DEFAULT_BINDINGS,
    "gmail": GMAIL_BINDINGS,
    "vim": VIM_BINDINGS,
    "pine": PINE_BINDINGS,
}


class KeymapManager:
    """Manages active keybindings, presets, and custom user overrides."""

    def __init__(
        self,
        preset: str = "default",
        custom_bindings: dict[str, str] | None = None,
    ) -> None:
        self.preset = preset if preset in PRESET_MAPS else "default"
        self.custom_bindings: dict[str, str] = dict(custom_bindings or {})

    def resolve_key(self, key: str, pane: str = "") -> str | None:
        """Resolve a key string to an action_id, respecting context-aware pane overrides."""
        # Check custom bindings first
        if key in self.custom_bindings:
            action = self.custom_bindings[key]
            return self._apply_context(action, pane)

        # Check active preset bindings
        active_preset = PRESET_MAPS.get(self.preset, DEFAULT_BINDINGS)
        if key in active_preset:
            action = active_preset[key]
            return self._apply_context(action, pane)

        # Fallback to default preset if preset is missing a binding
        if self.preset != "default" and key in DEFAULT_BINDINGS:
            action = DEFAULT_BINDINGS[key]
            return self._apply_context(action, pane)

        return None

    def _apply_context(self, action: str, pane: str) -> str:
        """Handle pane-specific context for overloaded keys like 's', 'e', '[', ']'."""
        if pane == "folders":
            if action == "toggle_star":
                return "toggle_folder_subscription"
            if action == "archive_msg":
                return "account_settings"
            if action == "expand_reader":
                return "narrow_folder_list"
            if action == "expand_list":
                return "widen_folder_list"
            if action == "reply":
                return "sync_folder"
        return action

    def get_keys_for_action(self, action_id: str) -> list[str]:
        """Return list of keys triggering an action under current configuration."""
        keys: list[str] = []
        # Custom bindings
        for k, a in self.custom_bindings.items():
            if a == action_id and k not in keys:
                keys.append(k)

        # Active preset
        active_preset = PRESET_MAPS.get(self.preset, DEFAULT_BINDINGS)
        for k, a in active_preset.items():
            if a == action_id and k not in keys:
                keys.append(k)

        # Default fallback
        if not keys and self.preset != "default":
            for k, a in DEFAULT_BINDINGS.items():
                if a == action_id and k not in keys:
                    keys.append(k)

        return keys

    def get_display_key(self, action_id: str, default: str = "") -> str:
        """Return primary display key for an action (e.g. for status bars and help)."""
        keys = self.get_keys_for_action(action_id)
        if not keys:
            return default
        # Return first non-modifier or cleanest key
        return keys[0]

    def set_binding(self, key: str, action_id: str) -> None:
        """Set or override a key binding."""
        self.custom_bindings[key] = action_id

    def remove_binding(self, key: str) -> None:
        """Remove a custom key binding."""
        self.custom_bindings.pop(key, None)

    def set_preset(self, preset: str) -> None:
        """Change the active preset."""
        if preset in PRESET_MAPS:
            self.preset = preset

    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration to a JSON-compatible dictionary."""
        return {
            "preset": self.preset,
            "bindings": dict(self.custom_bindings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeymapManager:
        """Deserialize configuration from a dictionary."""
        preset = str(data.get("preset", "default"))
        bindings = data.get("bindings") or {}
        if not isinstance(bindings, dict):
            bindings = {}
        return cls(preset=preset, custom_bindings=bindings)

    def save_to_file(self, path: Path | str) -> None:
        """Save keymap configuration to a JSON file."""
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load_from_file(cls, path: Path | str) -> KeymapManager:
        """Load keymap configuration from a JSON file, or default if missing."""
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return cls.from_dict(data)
        except Exception:
            pass
        return cls()

    @classmethod
    def load_from_store(cls, store: LocalStore | None) -> KeymapManager:
        """Load keymap from SQLite store settings, with fallback to config file or defaults."""
        if store is None:
            return cls()
        preset = store.get_setting("keymap_preset", "default")
        custom_json = store.get_setting("keymap_custom_json", "{}")
        custom_bindings: dict[str, str] = {}
        if custom_json:
            try:
                parsed = json.loads(custom_json)
                if isinstance(parsed, dict):
                    custom_bindings = parsed
            except Exception:
                pass

        # Optional check for ~/.config/espressomail/keymaps.json if no custom settings in SQLite
        if not custom_bindings and preset == "default":
            config_file = Path.home() / ".config" / "espressomail" / "keymaps.json"
            if config_file.exists():
                return cls.load_from_file(config_file)

        return cls(preset=preset, custom_bindings=custom_bindings)

    def save_to_store(self, store: LocalStore) -> None:
        """Persist current keymap preset and custom bindings to SQLite store settings."""
        store.set_setting("keymap_preset", self.preset)
        store.set_setting("keymap_custom_json", json.dumps(self.custom_bindings))
