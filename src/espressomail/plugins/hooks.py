"""Hook definitions and extension points for EspressoMail plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from espresso import Msg


@dataclass(frozen=True)
class PluginMenuItem:
    """A menu item injected into the EspressoMail top menu bar."""

    target_category: str  # e.g. "Message", "Edit", "Tools", "File", "Help", or custom category
    action_id: str
    label: str
    shortcut: str = ""
    position: str = "bottom"  # "top" or "bottom"
    order: int = 100


@dataclass(frozen=True)
class PluginCommand:
    """A command registered with the EspressoMail Command Palette (Ctrl+P) and keymaps."""

    action_id: str
    title: str
    description: str
    category: str = "Plugins"
    default_key: str = ""
    pane_scope: str = "global"  # "global", "reader", "messages", "folders", "compose"


@dataclass(frozen=True)
class ReaderActionPill:
    """An interactive action button or badge displayed in the reading pane header."""

    action_id: str
    label: str
    shortcut: str = ""
    badge: str = ""
    priority: int = 10


@dataclass(frozen=True)
class ComposeAction:
    """An action button or tool available in the compose modal."""

    action_id: str
    label: str
    shortcut: str = ""
    priority: int = 10


@dataclass(frozen=True)
class PluginActionMsg(Msg):
    """TEA Message emitted when an action provided by a plugin is triggered."""

    plugin_id: str
    action_id: str
    data: dict[str, Any] = field(default_factory=dict)
