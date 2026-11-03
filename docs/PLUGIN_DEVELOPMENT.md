# EspressoMail Plugin Development Guide & Tutorial

This guide walks you step-by-step through developing, testing, configuring, and packaging third-party plugins for **EspressoMail**.

EspressoMail's plugin architecture is built on **The Elm Architecture (TEA)** and provides crash-isolated extension hooks across the menu bar, command palette, keyboard shortcuts, reading pane, and email composer.

---

## Table of Contents

1. [How Plugins Work](#1-how-plugins-work)
2. [Where Plugins Live (Discovery Locations)](#2-where-plugins-live-discovery-locations)
3. [Quickstart Tutorial: Build Your First Plugin in 5 Minutes](#3-quickstart-tutorial-build-your-first-plugin-in-5-minutes)
4. [Adding Configurable Settings (`settings_schema`)](#4-adding-configurable-settings-settings_schema)
5. [Running Asynchronous / Network Tasks (`Cmd` & `Msg`)](#5-running-asynchronous--network-tasks-cmd--msg)
6. [Complete Hook Reference](#6-complete-hook-reference)
7. [Storage, Caching, and Data Directories (`PluginContext`)](#7-storage-caching-and-data-directories-plugincontext)
8. [Interacting with the Host App (`app` API)](#8-interacting-with-the-host-app-app-api)
9. [Packaging & Distributing a Third-Party Plugin (`pyproject.toml`)](#9-packaging--distributing-a-third-party-plugin-pyprojecttoml)
10. [Writing Unit Tests for Your Plugin](#10-writing-unit-tests-for-your-plugin)
11. [Security & Terminal Sanitization Best Practices](#11-security--terminal-sanitization-best-practices)
12. [Built-in Plugins Reference](#12-built-in-plugins-reference)

---

## 1. How Plugins Work

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        EspressoMailApp (TEA)                           │
│  ┌──────────────┐   ┌───────────────┐   ┌───────────────────────────┐  │
│  │   MenuBar    │   │Command Palette│   │ Reading Pane & Composer   │  │
│  └──────▲───────┘   └───────▲───────┘   └─────────────▲─────────────┘  │
└─────────┼───────────────────┼─────────────────────────┼────────────────┘
          │                   │                         │
┌─────────┴───────────────────┴─────────────────────────┴────────────────┐
│                       PluginManager (Hooks Engine)                     │
│  • Discovers plugins from Built-ins, ~/.config/..., and Entry Points   │
│  • Wraps every hook call in try/except fault isolation                 │
│  • Routes PluginActionMsg and background TEA Msg events                │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ PluginContext
                                    ▼
                 ┌──────────────────────────────────────┐
                 │ LocalStore (SQLite WAL Database)     │
                 │ • Namespaced Plugin Settings         │
                 │ • Persistent Cache with TTL Expiry   │
                 └──────────────────────────────────────┘
```

### Key Design Guarantees
- **Zero-Crash Fault Isolation**: Every plugin hook (`on_load`, `get_menu_items`, `transform_reader_content`, `on_action`, `on_message`) is wrapped in an exception guard. If a third-party plugin raises an unhandled exception, `PluginManager` logs the traceback and continues running EspressoMail normally.
- **Non-Blocking UI (TEA Purity)**: Slow operations (HTTP API calls, subprocesses, file I/O) are returned as a `Cmd` callable from `on_action`. EspressoMail runs the `Cmd` on a background worker thread and delivers the resulting `Msg` back to `on_message(msg, app)` on the main UI thread.
- **Automatic Settings UI**: Any setting declared in `metadata.settings_schema` is automatically rendered inside **Preferences (`P`) → 5. Plugins**, complete with text inputs, choice selectors, and boolean toggles.

---

## 2. Where Plugins Live (Discovery Locations)

On startup, `PluginManager` automatically scans three locations in order:

### A. User Plugin Directory (`~/.config/espressomail/plugins/`)
Ideal for personal scripts, local extensions, and rapid prototyping. Respects `$XDG_CONFIG_HOME/espressomail/plugins/` if set.

You can drop either:
1. **A single Python file**:
   ```text
   ~/.config/espressomail/plugins/markdown_export.py
   ```
2. **A multi-file package directory** containing `__init__.py` (or `plugin.py`):
   ```text
   ~/.config/espressomail/plugins/my_plugin/
   ├── __init__.py
   └── helpers.py
   ```

### B. Python Package Entry Points (`pip` / `pipx` installable)
Ideal for publishing and sharing third-party plugins on PyPI or GitHub. Any installed Python package declaring an entry point in the `espressomail.plugins` group is loaded automatically (see [Section 9](#9-packaging--distributing-a-third-party-plugin-pyprojecttoml)).

### C. Built-in Plugins (`src/espressomail/plugins/`)
Bundled directly with EspressoMail (`deepl`, `dmarc_inspector`, `phishing_scanner`, `unsubscribe`, `ai_assistant`, `languagetool`, `url_launcher`).

---

## 3. Quickstart Tutorial: Build Your First Plugin in 5 Minutes

Let's build a practical **"Markdown Note Exporter"** plugin that:
1. Shows a `[📝 Export MD]` button pill in the reading pane header.
2. Registers a `Ctrl+Shift+M` shortcut and an entry in the **Tools** menu and **Command Palette (`Ctrl+P`)**.
3. Exports the currently selected email to a Markdown file with YAML frontmatter.

### Step 1: Create the plugin file

Create `~/.config/espressomail/plugins/markdown_exporter.py`:

```python
"""Export the selected email to a Markdown note with YAML frontmatter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

from espresso import Cmd
from espresso.beans import ToastLevel
from espressomail.models.entities import EmailMessage
from espressomail.plugins.base import BasePlugin, PluginMetadata
from espressomail.plugins.hooks import (
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)


class MarkdownExporterPlugin(BasePlugin):
    """Exports emails to local Markdown files."""

    metadata = PluginMetadata(
        id="markdown_exporter",
        name="Markdown Note Exporter",
        version="1.0.0",
        author="Your Name",
        description="Export the active email to a Markdown file with YAML frontmatter.",
        icon="📝",
        settings_schema={
            "export_dir": {
                "type": "str",
                "label": "Notes Export Folder",
                "default": "~/Documents/EmailNotes",
                "description": "Directory where exported Markdown notes are saved",
            },
            "include_headers": {
                "type": "bool",
                "label": "Include YAML Frontmatter",
                "default": "true",
                "description": "Prepend YAML metadata block to exported Markdown files",
            },
        },
    )

    # 1. Add an item to the top 'Tools' menu
    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        return (
            PluginMenuItem(
                target_category="Tools",
                action_id="md_export_current_email",
                label="Export Email to Markdown",
                shortcut="Ctrl+N",
                order=60,
            ),
        )

    # 2. Register in the Command Palette (Ctrl+P)
    def get_commands(self) -> Sequence[PluginCommand]:
        return (
            PluginCommand(
                action_id="md_export_current_email",
                title="Notes: Export Current Email to Markdown File",
                description="Save the selected email as a .md note in your notes folder",
                category="Plugins",
                default_key="ctrl+n",
                pane_scope="reader",
            ),
        )

    # 3. Bind a global keyboard shortcut
    def get_keybindings(self) -> dict[str, str]:
        return {"ctrl+n": "md_export_current_email"}

    # 4. Show an interactive action pill in the Reading Pane header
    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        if not message:
            return ()
        return (
            ReaderActionPill(
                action_id="md_export_current_email",
                label="Export MD",
                shortcut="Ctrl+N",
                badge="📝 Note",
                priority=15,
            ),
        )

    # 5. Handle the action when triggered by shortcut, menu, pill, or palette
    def on_action(
        self, action_id: str, app: Any, data: dict[str, Any]
    ) -> tuple[Any, Cmd | None]:
        if action_id != "md_export_current_email":
            return app, None

        msg: EmailMessage | None = getattr(app, "selected_message", None)
        if not msg:
            app.status_message = "No email selected to export."
            return app, None

        export_dir_raw = self.context.get_setting("export_dir", "~/Documents/EmailNotes")
        include_fm = self.context.get_setting("include_headers", "true").lower() in ("1", "true", "yes")

        target_dir = Path(export_dir_raw).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (msg.subject or "untitled").lower()).strip("-")[:50]
        filename = f"{msg.short_date.replace(' ', '-')}-{slug or 'email'}.md"
        out_path = target_dir / filename

        lines: list[str] = []
        if include_fm:
            lines.extend([
                "---",
                f'subject: "{msg.subject}"',
                f'from: "{msg.sender_display}"',
                f'date: "{msg.date_iso}"',
                f'message_id: "{msg.message_id}"',
                "---",
                "",
            ])
        lines.append(f"# {msg.subject}\n")
        lines.append(msg.body_text or "(No plain-text body)")

        out_path.write_text("\n".join(lines), encoding="utf-8")

        app.status_message = f"✔ Exported note to {out_path}"
        if hasattr(app, "toasts"):
            app.toasts.add(f"Saved {out_path.name}", level=ToastLevel.INFO)

        return app, None
```

### Step 2: Launch EspressoMail and test it

1. Run `espressomail`.
2. Open **Preferences (`P`)** and press **`5`** to switch to **5. Plugins**.
3. Use `◀ / ▶` on **Select Plugin** until you see `📝 Markdown Note Exporter v1.0.0`.
4. You can edit **Notes Export Folder** and toggle **Include YAML Frontmatter** right in the modal.
5. Select any email and press `Ctrl+N` (or open `Ctrl+P` Command Palette and type `Markdown`).

---

## 4. Adding Configurable Settings (`settings_schema`)

Every plugin can define a `settings_schema` dictionary inside `PluginMetadata`. EspressoMail's `PreferencesModal` (Tab 5: Plugins) reads this schema dynamically and builds the appropriate UI controls for each setting.

```python
metadata = PluginMetadata(
    id="my_plugin",
    name="My Custom Plugin",
    version="1.0.0",
    author="Jane Doe",
    description="Short one-line description shown in Preferences.",
    icon="⚡",
    settings_schema={
        "api_key": {
            "type": "secret",
            "label": "Service API Key",
            "default": "",
            "description": "Enter your API token...",
        },
        "endpoint_url": {
            "type": "str",
            "label": "Server URL",
            "default": "https://api.example.com/v1",
            "description": "Base URL for self-hosted or cloud API",
        },
        "mode": {
            "type": "choice",
            "label": "Operation Mode",
            "default": "fast",
            "options": ["fast", "balanced", "thorough"],
            "description": "Select processing mode",
        },
        "auto_run": {
            "type": "bool",
            "label": "Run Automatically",
            "default": "true",
            "description": "Automatically process emails when opened",
        },
        "cache_days": {
            "type": "int",
            "label": "Cache Retention",
            "default": "30",
            "options": [7, 14, 30, 60, 90],
            "description": "Days to keep cached entries in SQLite",
        },
    },
)
```

### Supported Setting Types

| `type` | UI Control in `PreferencesModal` | Stored Value Type |
| :--- | :--- | :--- |
| `"str"` / `"text"` | Interactive `TextInput` field | `str` |
| `"secret"` / `"password"` | Interactive `TextInput` field for keys/tokens | `str` |
| `"choice"` | Cycleable selector `◀ option ▶` (uses `"options"` list) | `str` |
| `"bool"` | Checkbox toggle `[x] Enabled` / `[ ] Disabled` | `"true"` or `"false"` |
| `"int"` | Cycleable numeric selector `◀ 30 ▶` (uses `"options"` list) | `str` (parse with `int()`) |

Reading and writing settings inside your plugin:

```python
# Read setting (falls back to default if not yet saved)
api_key = self.context.get_setting("api_key", default="")
mode = self.context.get_setting("mode", default="fast")
auto_run = self.context.get_setting("auto_run", "true").lower() in ("1", "true", "yes")

# Programmatically update a setting
self.context.set_setting("mode", "thorough")
```

---

## 5. Running Asynchronous / Network Tasks (`Cmd` & `Msg`)

Because EspressoMail uses **The Elm Architecture (TEA)**, `on_action` runs on the main UI thread and must return immediately. Never perform blocking `urllib` requests or slow `subprocess` calls directly inside `on_action`.

Instead:
1. Define a frozen dataclass inheriting from `espresso.Msg`.
2. Return a zero-argument function (`Cmd`) from `on_action` that performs the work and returns your `Msg`.
3. Handle the completed `Msg` inside `on_message(self, msg, app)`.

### Full Async Pattern Example

```python
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any

from espresso import Cmd, Msg
from espresso.beans import ToastLevel
from espressomail.plugins.base import BasePlugin, PluginMetadata


@dataclass(frozen=True)
class WebhookFinishedMsg(Msg):
    """Emitted when our background HTTP request completes."""

    message_id: str
    success: bool
    response_summary: str = ""
    error: str = ""


class WebhookPlugin(BasePlugin):
    metadata = PluginMetadata(
        id="webhook_sender",
        name="Webhook Forwarder",
        version="1.0.0",
        description="Post email metadata to a custom HTTP webhook.",
        icon="🪝",
        settings_schema={
            "webhook_url": {
                "type": "str",
                "label": "Webhook URL",
                "default": "https://httpbin.org/post",
                "description": "Target HTTP POST endpoint",
            },
        },
    )

    def on_action(
        self, action_id: str, app: Any, data: dict[str, Any]
    ) -> tuple[Any, Cmd | None]:
        if action_id != "send_to_webhook":
            return app, None

        m = getattr(app, "selected_message", None)
        if not m:
            app.status_message = "No email selected."
            return app, None

        url = self.context.get_setting("webhook_url", "https://httpbin.org/post")
        app.status_message = f"🪝 Sending email metadata to {url}..."

        # Capture immutable values for the background thread closure
        msg_id = m.id
        payload = {
            "id": m.id,
            "subject": m.subject,
            "from": m.sender_email,
            "date": m.date_iso,
        }

        def _background_cmd() -> Msg:
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    status = getattr(resp, "status", 200)
                return WebhookFinishedMsg(
                    message_id=msg_id,
                    success=(200 <= status < 300),
                    response_summary=f"HTTP {status}",
                )
            except Exception as exc:
                return WebhookFinishedMsg(
                    message_id=msg_id,
                    success=False,
                    error=str(exc),
                )

        # Return updated app and the background Cmd
        return app, _background_cmd

    def on_message(self, msg: Any, app: Any) -> Cmd | None:
        if isinstance(msg, WebhookFinishedMsg):
            if msg.success:
                app.status_message = f"✔ Webhook delivered ({msg.response_summary})."
                if hasattr(app, "toasts"):
                    app.toasts.add("Webhook delivered!", level=ToastLevel.INFO)
            else:
                app.status_message = f"⚠ Webhook failed: {msg.error}"
                if hasattr(app, "toasts"):
                    app.toasts.add(f"Webhook error: {msg.error}", level=ToastLevel.WARNING)
        return None
```

---

## 6. Complete Hook Reference

All hooks are methods on [`BasePlugin`](../src/espressomail/plugins/base.py) and data types from [`espressomail.plugins.hooks`](../src/espressomail/plugins/hooks.py).

### Lifecycle Hooks

| Method | When It Is Called |
| :--- | :--- |
| `on_load(self) -> None` | Called once when `PluginManager` instantiates the plugin. |
| `on_enable(self) -> None` | Called on startup if the plugin is enabled, or when toggled ON in Preferences. |
| `on_disable(self) -> None` | Called when the user disables the plugin in Preferences. |

### UI & Action Hooks

#### 1. `get_menu_items(self) -> Sequence[PluginMenuItem]`
Injects items into the top drop-down menu bar (`File`, `Mail`, `View`, `Tools`, `Help`).

```python
PluginMenuItem(
    target_category="Tools",       # "File", "Mail", "View", "Tools", or "Help"
    action_id="my_plugin_action",  # Unique action ID routed to on_action()
    label="Run My Plugin",         # Menu item label
    shortcut="Ctrl+Y",             # Displayed shortcut hint
    position="bottom",             # "top" or "bottom"
    order=50,                      # Sort order among plugin menu items
)
```

#### 2. `get_commands(self) -> Sequence[PluginCommand]`
Adds searchable commands to the `Ctrl+P` Command Palette.

```python
PluginCommand(
    action_id="my_plugin_action",
    title="MyPlugin: Do Something Useful",
    description="Detailed explanation shown in the Command Palette",
    category="Plugins",
    default_key="ctrl+y",
    pane_scope="reader",  # "global", "reader", "messages", "folders", or "compose"
)
```

#### 3. `get_keybindings(self) -> dict[str, str]`
Maps lowercase key names to `action_id` strings in the 3-pane workspace.

```python
def get_keybindings(self) -> dict[str, str]:
    return {"ctrl+y": "my_plugin_action"}
```

> **Tip: Conflict-Free Shortcuts**
> Single-letter keys (`a`–`z`, `A`–`Z`) and `1`–`3` are heavily used for inbox triaging and navigation. Prefer `ctrl+<key>` or `alt+<key>` combinations, or expose less-frequent actions via the Command Palette (`Ctrl+P`).

#### 4. `get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]`
Renders action pills/badges in the reading pane header for the currently selected `EmailMessage`. Return `()` if your action does not apply to `message` (for example, `UnsubscribePlugin` returns `()` unless `List-Unsubscribe` is present in `message.raw_headers`).

```python
ReaderActionPill(
    action_id="my_plugin_action",
    label="Inspect",
    shortcut="Ctrl+D",
    badge="🛡️ Verified",  # Optional status badge prefix
    priority=10,          # Lower numbers appear further left (1 = highest priority)
)
```

#### 5. `transform_reader_content(self, message: EmailMessage, rendered_body: str, state: dict[str, Any]) -> str`
Transforms or augments the ANSI-formatted reading pane content.
- `state` contains `{"theme": Theme, "width": int}`.
- Note that `rendered_body` includes the formatted header block (`From:`, `To:`, `Date:`, `Subject:`) separated from the message body by a horizontal `━━━━━━━━━━` divider line.
- To insert a banner **below** the email headers and **above** the email body, split on the `━{10,}` divider:

```python
import re
from espresso.crema import Style
from espressomail.themes.engine import THEMES

def transform_reader_content(
    self, message: EmailMessage, rendered_body: str, state: dict[str, Any]
) -> str:
    theme = state.get("theme") or THEMES["espresso"]
    banner = Style().bold(True).foreground(theme.accent_primary).render("✨ Custom Plugin Banner")

    div_match = re.search(r"━{10,}", rendered_body)
    if div_match:
        line_end = rendered_body.find("\n", div_match.end())
        line_end = len(rendered_body) if line_end == -1 else line_end
        header_block = rendered_body[:line_end]
        body_rest = rendered_body[line_end:].lstrip("\n")
        return f"{header_block}\n\n{banner}\n\n{body_rest}"

    return f"{banner}\n\n{rendered_body}"
```

#### 6. `get_compose_actions(self, compose_modal: Any) -> Sequence[ComposeAction]`
Registers actions available inside `ComposeModal` when writing or replying to an email. Any `shortcut` specified here (e.g. `"Ctrl+L"`) is automatically bound inside `ComposeModal.update`.

```python
ComposeAction(
    action_id="my_compose_helper",
    label="✨ Improve Draft",
    shortcut="Ctrl+B",
    priority=10,
)
```

---

## 7. Storage, Caching, and Data Directories (`PluginContext`)

Every `BasePlugin` instance has `self.context` ([`PluginContext`](../src/espressomail/plugins/base.py)):

### 1. Persistent Settings
Stored in SQLite `settings` table under `plugin:<plugin_id>:setting:<key>`:
```python
val = self.context.get_setting("key_name", default="fallback")
self.context.set_setting("key_name", "new_value")
```

### 2. SQLite Cache with TTL Expiry
Stored in the dedicated `plugin_cache` table in SQLite. Useful for caching expensive API responses (translations, AI summaries, threat lookups):
```python
# Store JSON or text for 24 hours (86,400 seconds)
self.context.set_cache(f"msg:{message.id}", json.dumps(data), ttl_seconds=86400.0)

# Retrieve cached value (returns None if missing or expired)
cached_str = self.context.get_cache(f"msg:{message.id}")

# Clear a specific key or all cache entries for this plugin
self.context.clear_cache(f"msg:{message.id}")
self.context.clear_cache()  # Clears all keys for this plugin
```

### 3. Isolated Plugin Data Directory
Returns `~/.local/share/espressomail/plugins/<plugin_id>/` (creating it if needed):
```python
plugin_dir: Path = self.context.get_data_dir()
templates_file = plugin_dir / "templates.json"
```

---

## 8. Interacting with the Host App (`app` API)

Inside `on_action(self, action_id, app, data)` and `on_message(self, msg, app)`, you can inspect and update the host `EspressoMailApp` state:

| Attribute / Method | Description |
| :--- | :--- |
| `app.selected_message` | Currently highlighted `EmailMessage | None`. |
| `app.active_modal` | Currently open modal dialog (e.g. `ComposeModal` when composing). |
| `app.status_message = "..."` | Updates the bottom status bar text. |
| `app.toasts.add("...", level=ToastLevel.INFO)` | Shows a non-intrusive toast notification (`INFO`, `WARNING`, `ERROR`). |
| `app._update_reader_viewport(force=True)` | Forces the reading pane to re-run `transform_reader_content` and redraw. |
| `app.store` | Access to `LocalStore` (accounts, contacts, messages, folders). |
| `app.theme` | Active `Theme` object with semantic colors (`accent_primary`, `fg_primary`, `fg_muted`, `pgp_verified_fg`, `pgp_error_fg`, `pgp_warning_fg`). |

### Reading & Modifying a Draft in `ComposeModal`
When your plugin runs a `compose` action:
```python
compose_modal = getattr(app, "active_modal", None)
if compose_modal and hasattr(compose_modal, "editor"):
    current_subject = compose_modal.input_subject.value
    current_body = compose_modal.editor.value

    # Update draft content in place
    compose_modal.input_subject.value = f"[Reviewed] {current_subject}"
    compose_modal.editor.value = current_body + "\n\nBest regards,\nTeam"
    compose_modal.editor_status_msg = "✔ Draft updated by plugin."
```

---

## 9. Packaging & Distributing a Third-Party Plugin (`pyproject.toml`)

To share your plugin as a standard `pip`-installable package (e.g. `espressomail-plugin-jira`), create the following directory structure:

```text
espressomail-plugin-jira/
├── pyproject.toml
├── README.md
└── src/
    └── espressomail_jira/
        └── __init__.py
```

In `pyproject.toml`, register the entry point under `[project.entry-points."espressomail.plugins"]`:

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "espressomail-plugin-jira"
version = "0.1.0"
description = "Create Jira issues directly from emails in EspressoMail"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "espressomail>=0.3.0",
]

[project.entry-points."espressomail.plugins"]
jira_exporter = "espressomail_jira:JiraPlugin"
```

When a user installs your package in the same environment as EspressoMail:
```bash
pip install espressomail-plugin-jira
```
`PluginManager` will automatically discover `JiraPlugin` via `importlib.metadata.entry_points(group="espressomail.plugins")` on startup.

---

## 10. Writing Unit Tests for Your Plugin

You can test your plugin in isolation using an in-memory `LocalStore(":memory:")` without needing an IMAP server or terminal TTY:

```python
import unittest
from espressomail.models.entities import EmailMessage
from espressomail.plugins.manager import PluginManager
from espressomail.storage import LocalStore
from espressomail.ui.app import EspressoMailApp

from my_plugin import MarkdownExporterPlugin


class TestMarkdownExporterPlugin(unittest.TestCase):
    def setUp(self) -> None:
        self.store = LocalStore(":memory:")
        self.manager = PluginManager(store=self.store, auto_discover=False)
        self.plugin = self.manager.register_plugin_class(MarkdownExporterPlugin)

    def tearDown(self) -> None:
        self.store.close()

    def test_reader_pill_and_action(self) -> None:
        msg = EmailMessage(
            id="msg_1",
            account_id="acc_1",
            subject="Release Plan",
            sender_name="Ada Lovelace",
            sender_email="ada@analytical.org",
            body_text="Let's ship v1.0 on Monday.",
        )
        pills = self.plugin.get_reader_actions(msg)
        self.assertEqual(len(pills), 1)
        self.assertEqual(pills[0].action_id, "md_export_current_email")
```

---

## 11. Security & Terminal Sanitization Best Practices

When building plugins that parse untrusted email headers, bodies, or external API responses, always sanitize strings before rendering them to the terminal:

```python
from espressomail.models.entities import sanitize_terminal_text

safe_str = sanitize_terminal_text(untrusted_input, multiline=False)
```

`sanitize_terminal_text` strips raw ANSI escape sequences, OSC sequences, and ASCII/C1 control characters (such as `0x0e` Shift Out that could corrupt DEC terminal character sets) while preserving Unicode text and emojis.

---

## 12. Built-in Plugins Reference

For real-world reference implementations, inspect the built-in plugins inside [`src/espressomail/plugins/`](../src/espressomail/plugins/):

- **[`deepl`](../src/espressomail/plugins/deepl/__init__.py)**: Background translation `Cmd`, HTML fallback extraction, chunking, 30-day SQLite cache, and composer draft translation.
- **[`dmarc_inspector`](../src/espressomail/plugins/dmarc_inspector/__init__.py)**: Parsing `Authentication-Results` headers and decompressing `.xml`, `.gz`, and `.zip` DMARC aggregate reports into terminal tables.
- **[`phishing_scanner`](../src/espressomail/plugins/phishing_scanner/__init__.py)**: Inspecting headers and HTML `<a href>` tags for spoofing, Punycode homographs, and deceptive links.
- **[`unsubscribe`](../src/espressomail/plugins/unsubscribe/__init__.py)**: RFC 8058 One-Click HTTP POST background command and `mailto:` `ComposeModal` fallback.
- **[`ai_assistant`](../src/espressomail/plugins/ai_assistant/__init__.py)**: Multi-provider LLM client (Ollama, OpenAI, Anthropic) for reading pane TL;DR summaries and draft polishing.
- **[`languagetool`](../src/espressomail/plugins/languagetool/__init__.py)**: Right-to-left character offset replacement in `ComposeModal`.
- **[`url_launcher`](../src/espressomail/plugins/url_launcher/__init__.py)**: Extracting and deduplicating URLs and launching the system browser.
