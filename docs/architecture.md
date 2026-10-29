# EspressoMail Architecture

EspressoMail is built from the ground up using **[Espresso TUI](https://github.com/kimusan/espresso)** and follows **The Elm Architecture (TEA)** for strict unidirectional data flow, deterministic state transitions, and zero race conditions.

---

## 1. High-Level System Architecture

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                         Espresso Program Runtime                        │
│       (Raw Terminal Driver, Alt-Screen Diff Renderer, SGR Mouse)        │
└───────────────────┬─────────────────────────────────▲───────────────────┘
                    │ Msg (KeyMsg, MouseMsg, Custom)  │ view() -> ANSI str
                    ▼                                 │
┌─────────────────────────────────────────────────────────────────────────┐
│                        EspressoMailApp (TEA Model)                      │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ SplashScreen │  │  MenuBar &    │  │ 3-Pane View  │  │   Modals   │  │
│  │ & Onboarding │  │  Drop-Downs   │  │ & Splitters  │  │ & Palette  │  │
│  └──────────────┘  └───────────────┘  └──────────────┘  └────────────┘  │
└───┬─────────────┬──────────────┬─────────────┬────────────┬─────────┬───┘
    │             │              │             │            │         │
    ▼             ▼              ▼             ▼            ▼         ▼
┌──────────┐ ┌──────────┐ ┌────────────┐ ┌───────────┐ ┌─────────┐ ┌──────┐
│LocalStore│ │Protocols │ │  FTS5 &    │ │KeymapMgr  │ │External │ │System│
│ (SQLite  │ │  (IMAP,  │ │FuzzySearch │ │(Presets & │ │ Editor  │ │Notif │
│   WAL)   │ │SMTP,IDLE)│ │ (BM25 Rank)│ │ Actions)  │ │(Suspend)│ │(OS)  │
└──────────┘ └──────────┘ └────────────┘ └───────────┘ └─────────┘ └──────┘
```

---

## 2. Package Breakdown

```text
src/espressomail/
├── __init__.py               # Package version and metadata
├── cli.py                    # CLI argument parser, flags (--debug, --theme), and Program entrypoint
├── editor.py                 # External editor integration ($EDITOR) with terminal suspension
├── keymaps.py                # Configurable keymap presets (default, gmail, vim, pine) and key resolution
├── logging_config.py         # Rotating file logging and diagnostic configuration
├── notifications.py          # Cross-platform desktop notifications (notify-send, osascript, PowerShell)
├── models/
│   └── entities.py           # Dataclasses: AccountConfig, EmailMessage, EmailThread, Attachment, Contact, SieveFilterRule
├── storage/
│   └── database.py           # SQLite WAL database, FTS5 virtual table, BM25 ranking, Unified Inbox queries
├── search/
│   └── fuzzy.py              # Query parser (from:, has:attachment, is:unread), FTS5 queries, and fuzzy fallback
├── protocols/
│   ├── imap_client.py        # Multi-account IMAP4 client, RFC 2177 IDLE, MULTISEARCH, ESEARCH, MIME parser
│   ├── smtp_client.py        # Multi-account SMTP client & multipart MIME builder
│   ├── carddav.py            # CardDAV address book client & vCard parser
│   ├── icalendar.py          # RFC 5545 iCalendar parser and RFC 6047 iMIP RSVP responder
│   ├── markdown_renderer.py  # Markdown-to-HTML parser and live terminal preview generator
│   ├── threading.py          # JWZ-inspired RFC 5256 conversation threading engine
│   └── sieve.py              # RFC 5228 Sieve compiler, validator, local evaluator, and RFC 5804 client
├── crypto/
│   └── pgp.py                # GnuPG batch integration and RFC 4880 OpenPGP armor signing/encryption
├── themes/
│   └── engine.py             # ThemeEngine, 6 TrueColor theme families, Dark/Light modes, Crema styles
└── ui/
    ├── app.py                # Root 3-pane EspressoMailApp TEA Model, splitter management, event loops
    ├── splash.py             # Animated startup splash screen with cup art and progress bar
    ├── onboarding.py         # 3-step guided account onboarding wizard with provider presets and skip/quit
    ├── menus.py              # Top application menu bar & interactive drop-down menus
    ├── formatter.py          # Reading pane formatter (HTML-to-terminal, quotes, code blocks, PGP banners)
    ├── image_renderer.py     # Chafa, DEC Sixel, Kitty APC, Half-Block, and Braille image renderer
    └── modals.py             # Compose, Search, RSVP, Address Book, Sieve, PGP, Preferences, and Help modals
```

---

## 3. Core Subsystems

### 3.1 The Elm Architecture (TEA) State Machine
All UI state lives inside `EspressoMailApp` (`src/espressomail/ui/app.py`).
- **`update(msg: Msg) -> tuple[Model, Cmd | None]`**: Synchronously processes keyboard events (`KeyMsg`), SGR mouse events (`MouseMsg`), window resize events (`WindowSizeMsg`), background sync events (`SyncCompletedMsg`), IMAP IDLE push events (`IdleNewMailMsg`), and custom modal actions.
- **`view() -> str`**: Pure rendering function that composes the top `MenuBar`, the 3 horizontal panes (`join_horizontal`), and the bottom `StatusBar` (`join_vertical`), then composites any active drop-down menu, command palette, or modal dialog using `espresso.crema.place_overlay`.

### 3.2 Local SQLite Store (`LocalStore`) & Full-Text Search (FTS5)
`LocalStore` (`src/espressomail/storage/database.py`) operates in Write-Ahead Logging (`WAL`) mode with foreign key integrity:
- **`accounts`** and **`folders`**: Persist multi-account credentials and per-folder unread/total counters.
- **`messages`** and **`attachments`**: Cache parsed headers, plain-text/HTML bodies, thread IDs, IMAP flags, PGP status, and binary attachment payloads.
- **`messages_fts`**: SQLite FTS5 full-text index with Porter tokenization indexing subject, sender, recipients, and body text.
- **BM25 Relevance Ranking**: Results returned from `search()` order matches by relevance scores and extract contextual snippets.
- **`contacts`**: Local address book with automatic sender harvesting and frequency ranking.
- **`sieve_rules`** and **`sieve_scripts`**: Store structured filter rules and compiled RFC 5228 scripts.

### 3.3 IMAP Protocol, Remote Search & Push (RFC 2177 IDLE)
`IMAPClient` (`src/espressomail/protocols/imap_client.py`) provides resilient communication:
- **IDLE Push**: Background threads maintain long-lived IMAP IDLE loops, waking the TEA runtime when new messages arrive.
- **Remote Search**: Dispatches RFC 7377 `MULTISEARCH` or RFC 4731 `ESEARCH` commands directly to remote servers for non-cached queries.
- **Context Manager Protocol**: Safe connection recycling with automatic `LOGOUT` and socket cleanup.

### 3.4 Keymaps & Action Resolution (`keymaps.py`)
`KeymapManager` decouples raw keystrokes from application behavior:
- Built-in presets for **Default**, **Gmail**, **Vim**, and **Pine/Mutt**.
- Context-aware resolution allows keys like `e` to trigger archiving in the message list while editing account settings in the mailbox tree.
- Full customization with SQLite and JSON persistence.

### 3.5 External Editor Integration (`editor.py`)
`edit_text_externally` allows writing drafts in external terminal applications:
- Context manager `suspend_terminal()` restores canonical terminal mode and normal screen buffer.
- Launches `$EDITOR` (or configured editor) via standard subprocessing.
- Seamlessly catches signals, re-enables alternate screen and mouse tracking, and returns edited text to the TEA composer.

### 3.6 iCalendar & iMIP RSVP Responder (`protocols/icalendar.py`)
- Parses incoming `.ics` / `text/calendar` attachments compliant with RFC 5545.
- Computes start/end times, organizers, attendees, and RSVP status.
- Generates RFC 6047 iMIP response MIME parts with `method=REPLY` and dispatches via SMTP.

### 3.7 Markdown-to-HTML Rendering (`protocols/markdown_renderer.py`)
- Converts Markdown email bodies into semantic HTML with inline styling.
- Integrates with live preview viewports inside the TUI composer.
- Constructs multipart/alternative MIME emails ensuring high-fidelity rendering in modern desktop and webmail clients.
