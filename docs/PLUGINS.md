# EspressoMail Plugin System

EspressoMail includes a modular, extensible plugin architecture designed for safe extensibility without compromising UI performance, stability, or The Elm Architecture (TEA) principles.

---

## 1. High-Level Architecture

Plugins integrate seamlessly into EspressoMail's UI lifecycle and state updates.

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        EspressoMailApp (TEA)                           │
│  ┌──────────────┐   ┌───────────────┐   ┌───────────────────────────┐  │
│  │   MenuBar    │   │Command Palette│   │ Reading Pane & Composer   │  │
│  └──────▲───────┘   └───────▲───────┘   └─────────────▲─────────────┘  │
└─────────┼───────────────────┼─────────────────────────┼────────────────┘
          │                   │                         │
          │                   │                         │
┌─────────┴───────────────────┴─────────────────────────┴────────────────┐
│                       PluginManager (Hooks Engine)                     │
│  ┌───────────────────────┐ ┌───────────────────┐ ┌──────────────────┐  │
│  │  Built-in Plugins     │ │ User Config Dir   │ │ Python Entry Pts │  │
│  │ (plugins/deepl, ...)  │ │ (~/.config/...)   │ │(pkg entry_points)│  │
│  └───────────────────────┘ └───────────────────┘ └──────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Fault Isolation (Crash-Resistant Hook Aggregation & Dispatch)     │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ PluginContext
                                    ▼
                 ┌──────────────────────────────────────┐
                 │ LocalStore (SQLite WAL Database)     │
                 │ - Plugin Settings                    │
                 │ - Plugin Cache with TTL Expiry       │
                 └──────────────────────────────────────┘
```

### Core Principles
1. **Unidirectional Data Flow & TEA Purity**: Plugins produce commands (`Cmd`) for long-running or network tasks (such as DeepL translation requests) that run in background worker threads and dispatch messages back through the main message loop.
2. **Crash & Fault Isolation**: If an individual plugin throws an unhandled exception during hook discovery, rendering, or action execution, the error is logged and caught safely without crashing EspressoMail or affecting other plugins.
3. **Multi-Location Discovery**: Plugins can be bundled as built-in packages, installed as standalone Python scripts or packages in `~/.config/espressomail/plugins`, or distributed as standard pip packages exposing entry points.

---

## 2. Plugin Discovery & Installation

The `PluginManager` discovers plugins from three locations on startup:

### 1. Built-in Plugins
Bundled directly within `espressomail.plugins.*`. Includes reference implementations like the DeepL Translation plugin.

### 2. User Directory Plugins
Located at `$XDG_CONFIG_HOME/espressomail/plugins/` (defaulting to `~/.config/espressomail/plugins/`).
- **Single file plugin**: A Python file, e.g. `~/.config/espressomail/plugins/hello_tool.py`.
- **Directory plugin**: A directory containing `__init__.py` or `plugin.py`, e.g. `~/.config/espressomail/plugins/my_extension/`.

### 3. Python Package Entry Points
Any installed pip package that registers an entry point under the group `espressomail.plugins` in its `pyproject.toml`:

```toml
[project.entry-points."espressomail.plugins"]
my_plugin = "my_package.plugin:MyCustomPlugin"
```

---

## 3. Creating a Plugin: Step-by-Step

A plugin subclasses `BasePlugin` and defines `PluginMetadata`.

### Minimal Example

Save the following as `~/.config/espressomail/plugins/reading_counter.py`:

```python
from __future__ import annotations
from typing import Any, Sequence
from espresso import Cmd
from espressomail.models.entities import EmailMessage
from espressomail.plugins.base import BasePlugin, PluginMetadata
from espressomail.plugins.hooks import PluginCommand, ReaderActionPill

class WordCounterPlugin(BasePlugin):
    metadata = PluginMetadata(
        id="word_counter",
        name="Reading Word Counter",
        version="1.0.0",
        author="Espresso Contributor",
        description="Displays word counts and estimated reading time in the reading pane.",
    )

    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        if not message:
            return ()
        words = len(message.body_text.split())
        est_minutes = max(1, words // 200)
        return (
            ReaderActionPill(
                action_id="word_count_info",
                label=f"{words} words (~{est_minutes} min read)",
                shortcut="",
                active=False,
            ),
        )

    def get_commands(self) -> Sequence[PluginCommand]:
        return (
            PluginCommand(
                action_id="word_count_summary",
                title="Word Count: Inspect Current Email Length",
                description="Show detailed statistics for current message",
                category="Tools",
                default_key="ctrl+w",
            ),
        )

    def on_action(self, action_id: str, app: Any, data: dict[str, Any]) -> tuple[Any, Cmd | None]:
        if action_id == "word_count_summary":
            msg = getattr(app, "selected_message", None)
            if msg:
                words = len(msg.body_text.split())
                app.status_message = f"ℹ Message contains {words} words."
            else:
                app.status_message = "No message selected."
        return app, None
```

---

## 4. Extension Hooks

Plugins can implement any subset of the following hook methods:

### Menu Items
Adds actions to top-level menu categories (such as `Mail`, `Edit`, `View`, `Tools`, `Help`).

```python
def get_menu_items(self) -> Sequence[PluginMenuItem]:
    return (
        PluginMenuItem(
            target_category="Tools",
            action_id="my_custom_action",
            label="Launch Custom Tool",
            shortcut="Ctrl+Shift+T",
        ),
    )
```

### Command Palette Items
Exposes searchable commands in the fuzzy command palette (`Ctrl+P`).

```python
def get_commands(self) -> Sequence[PluginCommand]:
    return (
        PluginCommand(
            action_id="my_custom_action",
            title="Custom Tool: Run Diagnostics",
            description="Execute diagnostic analysis on current mailbox",
            category="Tools",
            default_key="ctrl+shift+t",
        ),
    )
```

### Global Keybindings
Registers keyboard shortcuts routed directly to `on_action`.

```python
def get_keybindings(self) -> dict[str, str]:
    return {
        "ctrl+shift+t": "my_custom_action",
    }
```

### Reading Pane Action Pills
Displays clickable pills and shortcuts in the reading pane header:

```python
def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
    return (
        ReaderActionPill(
            action_id="toggle_summary",
            label="Summarize",
            shortcut="S",
            active=False,
        ),
    )
```

### Reading Pane Content Transformation
Transforms or injects content into the rendered email message body:

```python
def transform_reader_content(
    self,
    message: EmailMessage,
    rendered_body: str,
    state: dict[str, Any],
) -> str:
    # Prepend or format content
    return f"[Verified Header]\n{rendered_body}"
```

### Compose Modal Actions
Adds actions to the compose window, including status-bar shortcut badges:

```python
def get_compose_actions(self, compose_modal: Any) -> Sequence[ComposeAction]:
    return (
        ComposeAction(
            action_id="translate_draft",
            label="Translate Draft",
            shortcut="Ctrl+T",
        ),
    )
```

---

## 5. Storage, Settings & Caching (`PluginContext`)

Each plugin receives an instance of `PluginContext` providing isolated storage helpers:

### Settings Persistence
Settings are automatically scoped by plugin ID and saved in SQLite:
```python
# Get a user setting with default fallback
api_key = self.context.get_setting("api_key", default="")

# Persist a setting value
self.context.set_setting("api_key", "secret-token")
```

### SQLite Cache with TTL
Store expensive operation results (e.g. API responses, embeddings) in the `plugin_cache` SQLite table:
```python
# Cache translated text for 30 days (2,592,000 seconds)
self.context.set_cache(cache_key, json_result, ttl_seconds=30 * 86400)

# Retrieve cached string (returns None if expired or missing)
cached_val = self.context.get_cache(cache_key)

# Invalidate cache entry
self.context.clear_cache(cache_key)
```

### Dedicated Data Directory
Returns a plugin-specific local directory under `~/.local/share/espressomail/plugins/<plugin_id>`:
```python
data_dir = self.context.get_data_dir()
data_file = data_dir / "index.json"
```

---

## 6. Preferences & Settings UI

EspressoMail's Preferences modal (`PreferencesModal`, accessible via `,` or menu) includes **Tab 5: Plugins**.

### Defining Settings Schema
Plugins declare configurable settings in `metadata.settings_schema`:

```python
PluginMetadata(
    id="my_plugin",
    name="My Plugin",
    version="1.0.0",
    settings_schema=(
        PluginSettingField(
            key="api_key",
            label="API Key",
            field_type="password",
            description="Authentication key",
        ),
        PluginSettingField(
            key="format",
            label="Format Mode",
            field_type="choice",
            choices=("Plain", "Markdown", "HTML"),
            default="Markdown",
        ),
    ),
)
```

In the **5. Plugins** tab:
- Users can toggle any plugin on/off with `Space` or Enter.
- Active plugin settings can be edited and cycled directly in the TUI.
- Settings are committed to SQLite when saving preferences (`S` or click Save).

---

## 7. Reference Implementation: DeepL Translation

The built-in `espressomail.plugins.deepl` plugin demonstrates a full-featured real-world plugin:

- **Free vs. Pro API Routing**: Detects `:fx` auth key suffixes to route to `https://api-free.deepl.com/v2` versus `https://api.deepl.com/v2`.
- **Chunking Budget**: Implements `split_text_into_chunks` respecting DeepL's 128 KiB request payload limits by cleanly partitioning texts into <=100k chunks on paragraph and sentence boundaries.
- **In-Place Reading Pane Toggle**: Pressing `T` triggers an asynchronous background translation via TEA `Cmd`. Once received, the reading pane displays the translated body alongside a translation banner. Pressing `T` again reverts instantly to the original text.
- **Draft Translation**: In the composer modal, `Ctrl+T` translates the subject and body to the configured target language without leaving the editor.
- **30-Day SQLite Caching**: Translations are cached locally for 30 days, avoiding redundant API calls and quota consumption.
- **Target Language Cycling**: Instant cycle between common languages (EN-GB, EN-US, DA, DE, ES, FR, IT, JA, NL, PL, PT-PT, SV, UK, ZH) via command palette or shortcut.
