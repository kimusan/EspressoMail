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
Plugins declare configurable settings in `metadata.settings_schema` as a dictionary:

```python
PluginMetadata(
    id="my_plugin",
    name="My Plugin",
    version="1.0.0",
    settings_schema={
        "api_key": {
            "type": "secret",
            "label": "API Key",
            "default": "",
            "description": "Authentication key",
        },
        "format": {
            "type": "choice",
            "label": "Format Mode",
            "default": "Markdown",
            "options": ["Plain", "Markdown", "HTML"],
            "description": "Output format mode",
        },
    },
)
```

In the **5. Plugins** tab:
- Users can cycle through installed plugins on Row 1 (`Select Plugin: ◀ ... ▶`) and toggle any plugin on/off on Row 2.
- All settings defined in the selected plugin's `settings_schema` are dynamically rendered and editable directly in the TUI.
- Settings are committed to SQLite when saving preferences (`Enter` / `Ctrl+S`).

For a complete tutorial on building, testing, and packaging third-party plugins, see the **[Third-Party Plugin Development Guide](PLUGIN_DEVELOPMENT.md)**.

---

## 7. Built-in Plugins

EspressoMail ships with 7 built-in plugins under `src/espressomail/plugins/`:

### 7.1 DeepL Neural Translation (`deepl_translate`)
- **Free vs. Pro API Routing**: Detects `:fx` auth key suffixes to route to `https://api-free.deepl.com/v2` versus `https://api.deepl.com/v2`.
- **Chunking Budget**: Splits long email bodies into `<=100 KiB` chunks on paragraph and sentence boundaries.
- **In-Place Reading Pane Toggle (`Ctrl+T`)**: Translates the current email (plain text or HTML-to-terminal fallback) and displays a language banner. Pressing `Ctrl+T` again reverts to the original text.
- **Compose Draft Translation (`Ctrl+T`)**: Translates draft subject and body inside `ComposeModal`.
- **30-Day SQLite Caching**: Translations are cached locally for 30 days to conserve API quota.

### 7.2 DMARC / DKIM / SPF Inspector (`dmarc_inspector`)
- **Authentication Header Verification (`Ctrl+D`)**: Parses `Authentication-Results`, `ARC-Authentication-Results`, `Received-SPF`, and `DKIM-Signature` headers into a status badge (`✔ SPF ✔ DKIM ✔ DMARC`) and toggleable inspector banner.
- **DMARC Aggregate Report Parser**: Automatically detects and decompresses `.xml`, `.xml.gz`, `.gz`, and `.zip` DMARC aggregate reports attached to emails and renders a structured terminal table showing reporting organization, policy (`p=`, `sp=`, `pct=`), source IPs, message counts, and DKIM/SPF evaluation results.

### 7.3 Phishing & Suspicious Link Scanner (`phishing_scanner`)
- **Header Spoofing Detection**: Flags `From` vs `Reply-To` registrable domain mismatches, display names containing spoofed email addresses, and SPF/DMARC authentication failures.
- **URL Defense**: Scans HTML `<a href="...">` tags and plain-text links for deceptive anchor text (where visible link text claims one domain while `href` points to another), Punycode (`xn--`) IDN homograph domains, and raw IPv4/IPv6 URLs.
- **Security Alert Banner**: Injects a high-visibility warning banner above the reading pane body whenever suspicious indicators are found.

### 7.4 One-Click Unsubscribe (`unsubscribe`)
- **RFC 2369 & RFC 8058 Support (`Ctrl+K`)**: Parses `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers.
- **Background One-Click HTTP POST**: Dispatches a non-blocking HTTP POST (`List-Unsubscribe=One-Click`) or GET fallback in a background TEA `Cmd`, or opens a pre-filled `ComposeModal` when only a `mailto:` unsubscribe URI is present.

### 7.5 AI Email Assistant (`ai_assistant`)
- **Local & Cloud Providers**: Supports local **Ollama** (`http://localhost:11434`, privacy-first default), **OpenAI-compatible** APIs (`/v1/chat/completions`), and **Anthropic Claude** (`/v1/messages`).
- **Reading Pane Summarizer (`Ctrl+Y`)**: Generates a concise executive summary with **TL;DR**, **Key Points**, and **Action Items / Deadlines**, cached in SQLite for 30 days.
- **Compose Draft Polisher (`Ctrl+B`)**: Proofreads and improves the clarity and tone of drafts directly in `ComposeModal`.

### 7.6 LanguageTool Grammar & Spell Checker (`languagetool`)
- **Compose Grammar Check (`Ctrl+L`)**: Sends draft text to the LanguageTool API (`https://api.languagetool.org/v2/check` or a self-hosted instance) and automatically applies top spelling and grammar replacements from right to left while preserving character offsets.

### 7.7 URL Launcher & Link Picker (`url_launcher`)
- **Link Extraction & Deduplication (`Ctrl+O`)**: Extracts HTTP(S) links and anchor text labels from both `body_html` and `body_text` while filtering out 1x1 tracking pixels.
- **Interactive Link Table**: Toggles a numbered `[Links in this Email]` table at the bottom of the reading pane, cycles through links with `Ctrl+O`, and launches the selected URL in the system browser (`webbrowser.open`).

---

## 8. Future Plugin Roadmap

See [PLUGIN_ROADMAP.md](./PLUGIN_ROADMAP.md) for detailed specifications of upcoming Phase 2 plugins (Git Patch & Diff Viewer, Task Manager / Obsidian Note Exporter, Quick Templates, and Attachment Text Previewer).

