"""Plugin framework and extension point system for EspressoMail."""

from espressomail.plugins.base import BasePlugin, PluginContext, PluginMetadata
from espressomail.plugins.hooks import (
    ComposeAction,
    PluginActionMsg,
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)
from espressomail.plugins.manager import PluginManager, default_user_plugins_dir

__all__ = [
    "BasePlugin",
    "PluginMetadata",
    "PluginContext",
    "PluginMenuItem",
    "PluginCommand",
    "ReaderActionPill",
    "ComposeAction",
    "PluginActionMsg",
    "PluginManager",
    "default_user_plugins_dir",
]
