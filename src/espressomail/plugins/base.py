"""Base class, metadata, and context interface for EspressoMail plugins."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from espresso import Cmd
from espressomail.models.entities import EmailMessage
from espressomail.plugins.hooks import (
    ComposeAction,
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)
from espressomail.storage.database import LocalStore, default_data_dir

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginMetadata:
    """Metadata describing a plugin, its author, and configurable settings schema."""

    id: str
    name: str
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    icon: str = "🧩"
    settings_schema: dict[str, dict[str, Any]] = field(default_factory=dict)


class PluginContext:
    """Safe execution context and isolation layer passed to plugins."""

    def __init__(self, plugin_id: str, store: LocalStore) -> None:
        self.plugin_id = plugin_id
        self.store = store
        self._data_dir: Path | None = None

    def get_setting(self, key: str, default: str = "") -> str:
        """Retrieve a persistent setting namespaced to this plugin."""
        return self.store.get_plugin_setting(self.plugin_id, key, default)

    def set_setting(self, key: str, value: str) -> None:
        """Store a persistent setting namespaced to this plugin."""
        self.store.set_plugin_setting(self.plugin_id, key, value)

    def get_cache(self, key: str) -> str | None:
        """Retrieve an unexpired cached value for this plugin."""
        return self.store.get_plugin_cache(self.plugin_id, key)

    def set_cache(self, key: str, value: str, ttl_seconds: float = 0.0) -> None:
        """Cache a value with an optional time-to-live in seconds."""
        self.store.set_plugin_cache(self.plugin_id, key, value, ttl_seconds)

    def clear_cache(self, key: str | None = None) -> None:
        """Clear cache entries for this plugin."""
        self.store.clear_plugin_cache(self.plugin_id, key)

    def get_data_dir(self) -> Path:
        """Return and ensure an isolated data directory for this plugin."""
        if self._data_dir is None:
            self._data_dir = default_data_dir() / "plugins" / self.plugin_id
            self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir


class BasePlugin:
    """Base class that all EspressoMail plugins inherit from."""

    metadata: PluginMetadata

    def __init__(self, context: PluginContext) -> None:
        self.context = context

    def on_load(self) -> None:
        """Lifecycle hook invoked once when the plugin is loaded into memory."""
        pass

    def on_enable(self) -> None:
        """Lifecycle hook invoked when the plugin is enabled by the user."""
        pass

    def on_disable(self) -> None:
        """Lifecycle hook invoked when the plugin is disabled by the user."""
        pass

    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        """Return custom menu items to inject into the top menu bar."""
        return ()

    def get_commands(self) -> Sequence[PluginCommand]:
        """Return commands to register in the Command Palette (Ctrl+P)."""
        return ()

    def get_keybindings(self) -> dict[str, str]:
        """Return a mapping of hotkeys to action IDs (e.g. {'t': 'deepl_translate'})."""
        return {}

    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        """Return action buttons/pills to render in the reading pane header."""
        return ()

    def transform_reader_content(
        self, message: EmailMessage, rendered_body: str, state: dict[str, Any]
    ) -> str:
        """Optionally transform or append to reading pane formatted text."""
        return rendered_body

    def get_compose_actions(self, compose_modal: Any) -> Sequence[ComposeAction]:
        """Return action buttons available within the compose modal."""
        return ()

    def handles_action(self, action_id: str) -> bool:
        """Return True if this plugin handles the given action_id."""
        for cmd in self.get_commands():
            if cmd.action_id == action_id:
                return True
        for item in self.get_menu_items():
            if item.action_id == action_id:
                return True
        for pill in self.get_reader_actions(None):
            if pill.action_id == action_id:
                return True
        for act in self.get_compose_actions(None):
            if act.action_id == action_id:
                return True
        return action_id.startswith(self.metadata.id)

    def on_action(
        self, action_id: str, app: Any, data: dict[str, Any]
    ) -> tuple[Any, Cmd | None]:
        """Handle a plugin action invoked from menu, button, hotkey, or palette.

        Returns updated app model and an optional TEA Cmd.
        """
        return app, None

    def on_message(self, msg: Any, app: Any) -> Cmd | None:
        """Handle custom background TEA messages emitted by this plugin."""
        return None
