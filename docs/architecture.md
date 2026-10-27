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
│  │ & Onboarding │  │  Drop-Downs   │  │ & Viewports  │  │ & Palette  │  │
│  └──────────────┘  └───────────────┘  └──────────────┘  └────────────┘  │
└───────┬────────────────────┬──────────────────┬─────────────────┬───────┘
        │                    │                  │                 │
        ▼                    ▼                  ▼                 ▼
┌───────────────┐  ┌──────────────────┐  ┌─────────────┐  ┌───────────────┐
│  LocalStore   │  │ Protocol Layer   │  │ FuzzySearch │  │  PGPManager   │
│ (SQLite WAL + │  │ (IMAP4, SMTP,    │  │   Engine    │  │ (GnuPG CLI +  │
│  FTS5 Index)  │  │  JWZ, Sieve)     │  │             │  │  RFC 4880)    │
└───────────────┘  └──────────────────┘  └─────────────┘  └───────────────┘
```

---

## 2. Package Breakdown

```text
src/espressomail/
├── __init__.py          # Package version and metadata
├── cli.py               # CLI argument parser and Program entrypoint
├── models/
│   └── entities.py      # Dataclasses: AccountConfig, EmailMessage, EmailThread, Attachment, Contact, SieveFilterRule
├── storage/
│   └── database.py      # SQLite WAL database, FTS5 full-text virtual table, Unified Inbox queries, demo seeder
├── search/
│   └── fuzzy.py         # Query parser (from:, has:attachment, is:unread) and typo-tolerant fuzzy scorer
├── protocols/
│   ├── imap_client.py   # Multi-account IMAP4 client & RFC 5322 / MIME parser
│   ├── smtp_client.py   # Multi-account SMTP client & multipart MIME builder
│   ├── threading.py     # JWZ-inspired RFC 5256 conversation threading engine
│   └── sieve.py         # RFC 5228 Sieve compiler, validator, local evaluator, and RFC 5804 ManageSieve client
├── crypto/
│   └── pgp.py           # GnuPG batch integration and RFC 4880 OpenPGP armor signing/encryption
├── themes/
│   └── engine.py        # ThemeEngine and 6 TrueColor palettes with Crema Style builders
└── ui/
    ├── app.py           # Root 3-pane EspressoMailApp TEA Model
    ├── splash.py        # Animated startup splash screen
    ├── onboarding.py    # 3-step guided account onboarding wizard
    ├── menus.py         # Top application menu bar & drop-down menus
    ├── formatter.py     # Reading pane formatter (HTML-to-terminal, quotes, code blocks, PGP banners)
    ├── image_renderer.py# Chafa, DEC Sixel, Kitty APC, Half-Block, and Braille image renderer
    └── modals.py        # Compose, Search, Address Book, Sieve, PGP, and Help modal overlays
```

---

## 3. Core Subsystems

### 3.1 The Elm Architecture (TEA) State Machine
All UI state lives inside `EspressoMailApp` (`src/espressomail/ui/app.py`).
- **`update(msg: Msg) -> tuple[Model, Cmd | None]`**: Synchronously processes keyboard events (`KeyMsg`), SGR mouse events (`MouseMsg`), window resize events (`WindowSizeMsg`), and custom lifecycle messages (`SplashDoneMsg`, `OnboardingCompleteMsg`, `MenuActionMsg`, `ComposeSubmittedMsg`, `SyncCompletedMsg`).
- **`view() -> str`**: Pure rendering function that composes the top `MenuBar`, the 3 horizontal panes (`join_horizontal`), and the bottom `StatusBar` (`join_vertical`), then composites any active drop-down menu or modal dialog using `espresso.crema.place_overlay`.

### 3.2 Local SQLite Store (`LocalStore`)
`LocalStore` (`src/espressomail/storage/database.py`) uses SQLite in Write-Ahead Logging (`WAL`) mode:
- **`accounts`** and **`folders`**: Persist multi-account credentials and per-folder unread/total counters.
- **`messages`** and **`attachments`**: Cache parsed headers, plain-text/HTML bodies, thread IDs, IMAP flags, PGP status, and binary attachment payloads.
- **`messages_fts`**: SQLite FTS5 virtual table indexing subject, sender, recipients, body, and folder.
- **`contacts`**: Local address book with automatic sender harvesting and frequency ranking.
- **`sieve_rules`** and **`sieve_scripts`**: Store structured filter rules and compiled RFC 5228 scripts.

### 3.3 Conversation Threading (`protocols/threading.py`)
`build_threads()` implements a union-find + hierarchical tree reconstruction algorithm inspired by Jamie Zawinski's (JWZ) threading algorithm (RFC 5256):
1. Links messages sharing `Message-ID`, `In-Reply-To`, `References`, or `thread_id`.
2. Merges conversations with identical normalized subjects (`Re:`, `Fwd:` stripped) within the same account.
3. Constructs a depth-annotated `ThreadNode` tree so replies render with visual indentation (`╰─`).

### 3.4 Terminal Image Rendering (`ui/image_renderer.py`)
`TerminalImageRenderer` bridges external CLI tools and Espresso's native `ImageViewer`:
1. **`chafa`**: When `chafa` is installed on `PATH`, invokes `chafa --size=WxH --format=symbols` for high-speed symbol rasterization.
2. **DEC Sixel (`sixel`)**: Uses `img2sixel` or Espresso's pure-Python VT340 Sixel encoder (`encode_sixel`).
3. **Kitty Graphics (`kitty`)**: Emits native Kitty APC base64 chunks (`encode_kitty`).
4. **24-bit Half-Blocks (`half_block`) & Braille (`braille`)**: Universal TrueColor cell rendering that works in every modern terminal emulator and inside `tmux`.
