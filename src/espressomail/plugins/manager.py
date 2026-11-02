"""Plugin discovery, lifecycle management, and hook aggregation for EspressoMail."""

from __future__ import annotations

import importlib.util
import inspect
import logging
import os
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Sequence

from espresso import Cmd
from espressomail.models.entities import EmailMessage
from espressomail.plugins.base import BasePlugin, PluginContext, PluginMetadata
from espressomail.plugins.hooks import (
    ComposeAction,
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)
from espressomail.storage.database import LocalStore

logger = logging.getLogger(__name__)


def default_user_plugins_dir() -> Path:
    """Return the user configuration directory where custom plugins are installed."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        base = Path(xdg_config).expanduser().resolve()
    else:
        base = Path.home() / ".config"
    p = base / "espressomail" / "plugins"
    return p


class PluginManager:
    """Orchestrates plugin discovery, loading, lifecycle, and hook dispatching."""

    def __init__(
        self,
        store: LocalStore,
        user_plugins_dir: Path | str | None = None,
        auto_discover: bool = True,
    ) -> None:
        self.store = store
        self.user_plugins_dir = (
            Path(user_plugins_dir).expanduser().resolve()
            if user_plugins_dir is not None
            else default_user_plugins_dir()
        )
        self.plugins: dict[str, BasePlugin] = {}
        self.failed_plugins: dict[str, str] = {}
        if auto_discover:
            self.discover_and_load()

    def discover_and_load(self) -> None:
        """Scan and instantiate all plugins from built-ins, entry points, and user dir."""
        self._discover_builtin_plugins()
        self._discover_entry_point_plugins()
        self._discover_user_dir_plugins()

    def register_plugin_class(self, plugin_cls: type[BasePlugin]) -> BasePlugin | None:
        """Instantiate and register a plugin class."""
        try:
            meta = getattr(plugin_cls, "metadata", None)
            if not meta or not isinstance(meta, PluginMetadata):
                logger.warning("Plugin class %s missing valid metadata attribute", plugin_cls)
                return None

            plugin_id = meta.id
            if plugin_id in self.plugins:
                return self.plugins[plugin_id]

            ctx = PluginContext(plugin_id=plugin_id, store=self.store)
            inst = plugin_cls(ctx)
            inst.on_load()
            self.plugins[plugin_id] = inst

            if self.is_enabled(plugin_id):
                try:
                    inst.on_enable()
                except Exception as exc:
                    logger.exception("Plugin %s crashed on on_enable: %s", plugin_id, exc)

            logger.info("Registered plugin: %s v%s (%s)", meta.name, meta.version, plugin_id)
            return inst
        except Exception as exc:
            cls_name = getattr(plugin_cls, "__name__", str(plugin_cls))
            self.failed_plugins[cls_name] = str(exc)
            logger.exception("Failed to instantiate plugin class %s: %s", cls_name, exc)
            return None

    def is_enabled(self, plugin_id: str) -> bool:
        """Check if a plugin is currently enabled."""
        val = self.store.get_setting(f"plugin:{plugin_id}:enabled", "1")
        return val == "1"

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Enable or disable a plugin and call lifecycle hooks."""
        current = self.is_enabled(plugin_id)
        self.store.set_setting(f"plugin:{plugin_id}:enabled", "1" if enabled else "0")
        inst = self.plugins.get(plugin_id)
        if inst and current != enabled:
            try:
                if enabled:
                    inst.on_enable()
                else:
                    inst.on_disable()
            except Exception as exc:
                logger.exception("Plugin %s error toggling enabled=%s: %s", plugin_id, enabled, exc)

    def _discover_builtin_plugins(self) -> None:
        """Scan built-in plugins bundled under espressomail.plugins.*."""
        pkg_dir = Path(__file__).resolve().parent
        for item in pkg_dir.iterdir():
            if item.is_dir() and (item / "__init__.py").exists() and item.name not in ("__pycache__",):
                mod_name = f"espressomail.plugins.{item.name}"
                try:
                    import importlib
                    module = importlib.import_module(mod_name)
                    found = False
                    for _, obj in inspect.getmembers(module, inspect.isclass):
                        if issubclass(obj, BasePlugin) and obj is not BasePlugin:
                            self.register_plugin_class(obj)
                            found = True
                    if not found:
                        self._load_plugin_from_directory(item)
                except Exception as exc:
                    logger.warning("Failed importing built-in plugin %s: %s, falling back to file load", mod_name, exc)
                    self._load_plugin_from_directory(item)

    def _discover_entry_point_plugins(self) -> None:
        """Discover plugins registered via Python package entry points."""
        try:
            try:
                eps = entry_points(group="espressomail.plugins")
            except TypeError:
                all_eps = entry_points()
                if isinstance(all_eps, dict):
                    eps = all_eps.get("espressomail.plugins", [])
                else:
                    eps = all_eps.select(group="espressomail.plugins")

            for ep in eps:
                try:
                    plugin_cls = ep.load()
                    if inspect.isclass(plugin_cls) and issubclass(plugin_cls, BasePlugin):
                        self.register_plugin_class(plugin_cls)
                except Exception as exc:
                    logger.warning("Failed to load plugin entry point %s: %s", getattr(ep, "name", str(ep)), exc)
        except Exception as exc:
            logger.debug("Entry points plugin scanning skipped or failed: %s", exc)

    def _discover_user_dir_plugins(self) -> None:
        """Scan ~/.config/espressomail/plugins/ for single files and directories."""
        if not self.user_plugins_dir.exists() or not self.user_plugins_dir.is_dir():
            return

        for item in self.user_plugins_dir.iterdir():
            if item.name.startswith((".", "_")):
                continue
            if item.is_dir():
                self._load_plugin_from_directory(item)
            elif item.is_file() and item.suffix == ".py":
                self._load_plugin_from_file(item)

    def _load_plugin_from_directory(self, dir_path: Path) -> None:
        init_file = dir_path / "__init__.py"
        plugin_file = dir_path / "plugin.py"
        target_file = init_file if init_file.exists() else (plugin_file if plugin_file.exists() else None)
        if target_file:
            self._load_plugin_from_file(target_file, module_name=f"espressomail_plugin_{dir_path.name}")

    def _load_plugin_from_file(self, file_path: Path, module_name: str | None = None) -> None:
        mod_name = module_name or f"espressomail_plugin_{file_path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)

                # Find any subclass of BasePlugin inside the module
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BasePlugin) and obj is not BasePlugin:
                        self.register_plugin_class(obj)
        except Exception as exc:
            self.failed_plugins[file_path.name] = str(exc)
            logger.exception("Failed to load plugin file %s: %s", file_path, exc)

    # ------------------------------------------------------------------
    # Hook Aggregators
    # ------------------------------------------------------------------

    def get_menu_items(self) -> list[PluginMenuItem]:
        """Collect all menu items registered by active plugins."""
        items: list[PluginMenuItem] = []
        for plugin_id, plugin in self.plugins.items():
            if not self.is_enabled(plugin_id):
                continue
            try:
                for item in plugin.get_menu_items():
                    items.append(item)
            except Exception as exc:
                logger.exception("Error collecting menu items from plugin %s: %s", plugin_id, exc)
        return sorted(items, key=lambda x: x.order)

    def get_commands(self) -> list[PluginCommand]:
        """Collect all command palette items registered by active plugins."""
        commands: list[PluginCommand] = []
        for plugin_id, plugin in self.plugins.items():
            if not self.is_enabled(plugin_id):
                continue
            try:
                for cmd in plugin.get_commands():
                    commands.append(cmd)
            except Exception as exc:
                logger.exception("Error collecting commands from plugin %s: %s", plugin_id, exc)
        return commands

    def get_keybindings(self) -> dict[str, tuple[str, str]]:
        """Collect hotkey mappings: key -> (plugin_id, action_id)."""
        bindings: dict[str, tuple[str, str]] = {}
        for plugin_id, plugin in self.plugins.items():
            if not self.is_enabled(plugin_id):
                continue
            try:
                for key, action_id in plugin.get_keybindings().items():
                    bindings[key.lower()] = (plugin_id, action_id)
            except Exception as exc:
                logger.exception("Error collecting keybindings from plugin %s: %s", plugin_id, exc)
        return bindings

    def get_reader_actions(self, message: EmailMessage | None) -> list[ReaderActionPill]:
        """Collect action buttons to display in reading pane header."""
        actions: list[ReaderActionPill] = []
        for plugin_id, plugin in self.plugins.items():
            if not self.is_enabled(plugin_id):
                continue
            try:
                for action in plugin.get_reader_actions(message):
                    actions.append(action)
            except Exception as exc:
                logger.exception("Error collecting reader actions from plugin %s: %s", plugin_id, exc)
        return sorted(actions, key=lambda x: x.priority)

    def transform_reader_content(
        self, message: EmailMessage, rendered_body: str, state: dict[str, Any]
    ) -> str:
        """Run the reading pane body through active plugin content transformers."""
        current_text = rendered_body
        for plugin_id, plugin in self.plugins.items():
            if not self.is_enabled(plugin_id):
                continue
            try:
                current_text = plugin.transform_reader_content(message, current_text, state)
            except Exception as exc:
                logger.exception("Plugin %s crashed in transform_reader_content: %s", plugin_id, exc)
        return current_text

    def get_compose_actions(self, compose_modal: Any) -> list[ComposeAction]:
        """Collect action buttons available in the compose modal."""
        actions: list[ComposeAction] = []
        for plugin_id, plugin in self.plugins.items():
            if not self.is_enabled(plugin_id):
                continue
            try:
                for action in plugin.get_compose_actions(compose_modal):
                    actions.append(action)
            except Exception as exc:
                logger.exception("Error collecting compose actions from plugin %s: %s", plugin_id, exc)
        return sorted(actions, key=lambda x: x.priority)

    def get_plugin_setting(self, plugin_id: str, key: str, default: str = "") -> str:
        """Helper to get a setting for a plugin directly via LocalStore."""
        return self.store.get_plugin_setting(plugin_id, key, default)

    def set_plugin_setting(self, plugin_id: str, key: str, value: str) -> None:
        """Helper to save a setting for a plugin directly via LocalStore."""
        self.store.set_plugin_setting(plugin_id, key, value)

    def dispatch_action(
        self,
        action_id: str,
        app: Any,
        plugin_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> tuple[Any, Cmd | None]:
        """Dispatch a triggered action to its owning plugin or scan active plugins."""
        data = data or {}
        if plugin_id:
            plugin = self.plugins.get(plugin_id)
            if plugin and self.is_enabled(plugin_id):
                try:
                    return plugin.on_action(action_id, app, data)
                except Exception as exc:
                    logger.exception("Plugin %s crashed handling action %s: %s", plugin_id, action_id, exc)
            return app, None

        # Look up plugin by action_id
        for pid, plugin in self.plugins.items():
            if not self.is_enabled(pid):
                continue
            if plugin.handles_action(action_id):
                try:
                    return plugin.on_action(action_id, app, data)
                except Exception as exc:
                    logger.exception("Plugin %s crashed handling action %s: %s", pid, action_id, exc)
                    return app, None
        return app, None

    def on_message(self, msg: Any, app: Any) -> tuple[Any, Cmd | None]:
        """Route incoming TEA messages to active plugins or handle PluginActionMsg."""
        from espressomail.plugins.hooks import PluginActionMsg

        if isinstance(msg, PluginActionMsg):
            return self.dispatch_action(
                action_id=msg.action_id,
                app=app,
                plugin_id=msg.plugin_id,
                data=msg.data,
            )

        for plugin_id, plugin in self.plugins.items():
            if not self.is_enabled(plugin_id):
                continue
            try:
                cmd = plugin.on_message(msg, app)
                if cmd is not None:
                    return app, cmd
            except Exception as exc:
                logger.exception("Plugin %s failed in on_message: %s", plugin_id, exc)
        return app, None
