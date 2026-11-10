# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-11-10


> Release v0.4.0: Polyglot Baristas, Priority Roasts, and Flag Telemetry

Fresh pull, hotter queue, zero `&APg-` ghosts. v0.4.0 teaches EspressoMail four languages, puts the folder you actually clicked at the front of the sync line, syncs read/star state both ways without hammering IMAP, and packs fresh `espressoTUI 1.4.0` beans.

### Added
- **External JSON Internationalization (i18n)**: Your terminal email client finally speaks more than fluent ASCII. Added external JSON locale catalogs (`en`, `da`, `de`, `es`) with automatic system locale detection, transparent English fallback for missing keys, catalog validation, user overrides in `~/.espressomail/locales/`, a Language switcher in Preferences, and translated UI, help, and plugin strings across the app.
- **`espressoTUI 1.4.0` Beans & Ergonomics**:
  - **Tabbed Preferences**: Adopted the new `Tabs` bean in Settings, fixed left-skewed tab click hit-testing so the full width of every tab actually works, and eliminated truncation on plugin setting labels (like DMARC's *"Always Show Auth Details"*).
  - **Live Markdown Compose Preview**: Toggle rendered Markdown in `ComposeModal` with `F4` before unleashing raw asterisks on innocent recipients.
  - **Interactive Search DatePicker & Highlighting**: Press `Ctrl+D` or `F3` inside `SearchModal` for an interactive `DatePicker` (`since:` / `before:` filtering), backed by `highlight_matches_ansi` match highlighting.
  - **Single-Keystroke Clipboard Copy**: Added `copy_to_clipboard` bindings (`Y` / `Ctrl+Y`, plus `y` in the reader pane) so copying text no longer requires terminal mouse gymnastics.
- **VIP Folder Sync Queue**: Entering a folder during an active sync—or pressing `Ctrl+R` / `r` on another folder—promotes it straight to the front of `sync_queue`. No more waiting behind ten years of `Archive` to read today's mail.

### Changed
- **Bidirectional `\Seen` & Lightweight Flag Telemetry**: Opening an unread mail (`Enter`, `Space`, `l`, `Right`) now pushes `\Seen` back to the IMAP server alongside batch read toggling (`u`). Background sync fetches compact `(UID FLAGS)` across all cached UIDs (even beyond `fetch_batch_size`) so mails read or starred on another device update locally without re-downloading `BODY.PEEK[]`.
- **Fixed-Size Scrollable `SearchModal`**: Locked modal dimensions regardless of 0 hits, 100 hits, or an open `DatePicker`. Added an ANSI-styled scrollable results viewport (`█`/`│`), a `Showing X–Y of Z matches` counter, keyboard navigation (`Up`/`Down`/`PgUp`/`PgDn`/`Ctrl+Home`/`Ctrl+End`), mouse wheel scrolling, and click/double-click selection.
- **Calmer Notifications & Toast Dispatch**: Excluded Sent, Drafts, Archive, Trash, Spam, Outbox, Templates, and self-sent messages from desktop notifications, and routed UI confirmations (like saved preferences) to non-blocking toasts instead of overwriting the status bar.

### Fixed
- **Human-Readable IMAP Folder Names**: Decoded Modified UTF-7, UTF-8, and CP1252 IMAP folder names so folders like `Inkøb` show up as `Inkøb` instead of eldritch `Ink&APg-b` sequences in footer sync progress and status texts.
- **Release Tooling Environment Selection**: Updated `scripts/release.py` to select the right Python environment per task (`PIL` + `espresso` for tests; `build` + `setuptools` for packaging without `build/` directory shadowing).
## [0.3.0] - 2026-11-03


> Release v0.3.0: Plugins, Phishing Shields, and AI Baristas

### Highlights: EspressoMail Now Has Extra Shots (Plugins!)

We realized a terminal email client isn't truly complete until you can bolt arbitrary Python scripts onto it like a caffeinated mad scientist. **v0.3.0** introduces a full-blown, crash-isolated **Plugin Architecture** along with **7 built-in plugins** so you can translate, summarize, grammar-check, and deflect phishing attacks without ever touching your mouse.

#### 1. The Extensible Plugin System
- **Three Ways to Brew**: Load built-in plugins, drop custom scripts into `~/.config/espressomail/plugins/`, or install 3rd-party packages via `pip` entry points (`espressomail.plugins`).
- **Crash-Isolated Hooks**: If a 3rd-party plugin divides by zero while inspecting your spam folder, EspressoMail catches it, logs it, and keeps your inbox running smoothly.
- **Full Developer Guide**: Want to build your own plugin? Check out the new step-by-step tutorial in `docs/PLUGIN_DEVELOPMENT.md`.

#### 2. Seven Built-In Plugins Ready to Pour
- **DeepL Translation (`Ctrl+T`)**: Effortlessly translate foreign emails in the reading pane or translate your own outgoing drafts before sending. Now you can finally look fluent in 16 languages without opening a browser tab.
- **AI Email Assistant (`Ctrl+Y` / `Ctrl+B`)**: Hook up your local **Ollama** instance (or OpenAI / Anthropic Claude) to turn 15-paragraph corporate monologues into a 3-line **TL;DR + Action Items** (`Ctrl+Y`), or transform your sleep-deprived drafts into polite, professional prose (`Ctrl+B`).
- **Phishing & Suspicious Link Scanner**: Automatically guards your inbox at 7:00 AM before the coffee kicks in. Detects display-name spoofing (`"PayPal Support" <totally-legit@gmail.com>`), sneaky `Reply-To` traps, Punycode (`xn--`) homograph attacks, and raw IP links.
- **DMARC / DKIM / SPF Security Inspector (`Ctrl+D`)**: Parses `Authentication-Results` headers into a clean security badge and automatically unpacks `.xml`, `.xml.gz`, and `.zip` DMARC aggregate reports into human-readable terminal tables—because life is too short to read raw XML.
- **One-Click Unsubscribe (`Ctrl+K`)**: Detects `List-Unsubscribe` headers and fires RFC 8058 One-Click `POST` requests in the background. Banish marketing newsletters to the shadow realm without ever opening a browser.
- **LanguageTool Grammar & Spell Checker (`Ctrl+L`)**: Catches `teh` typos and questionable grammar right inside the composer before you accidentally email your entire department.
- **URL Launcher & Link Picker (`Ctrl+O`)**: Extracts every link from the current email into a numbered list (cleverly ignoring 1x1 tracking pixels) so you can launch the exact URL you want in one keystroke.

#### 3. Quality-of-Life & Under-the-Hood Goodies
- **Single-Point Version & Startup Update Checker**: EspressoMail now knows its own version from a single source of truth and quietly checks GitHub on startup to let you know (via toast or modal) when a fresher roast is available.
- **Sync Current Folder (`Ctrl+R` / `F5` / `r`)**: Impatiently waiting for a 2FA code? Refresh just the active folder on demand without syncing your entire archive.
- **Dynamic Plugin Preferences UI**: Configure API keys, models, and toggles for every installed plugin directly from `Preferences -> 5. Plugins`.
## [0.2.0] - 2026-10-29


> EspressoMail 0.2.0: Full-text search, external editor, keymaps, iCalendar RSVP, and real-time push

### Summary
EspressoMail 0.2.0 introduces a major set of features, performance improvements, and usability enhancements across search, composing, inbox triaging, notifications, calendar interactions, and keyboard navigation.

### Added
- SQLite FTS5 Full-Text Search: Blazing fast message searching with BM25 relevance ranking, contextual snippet highlighting, and structured search operators (from, to, subject, body, att, folder, has:attachment, is:unread, is:starred, is:pgp).
- Remote IMAP Search: Query remote mail servers directly from the search modal using RFC 7377 MULTISEARCH and RFC 4731 ESEARCH fallbacks (Ctrl+R / F5).
- External Editor Integration: Suspend terminal raw mode and edit draft messages seamlessly in your preferred system editor ($EDITOR / $VISUAL, nano, vim, nvim, micro, emacs) via Ctrl+O or F6.
- Configurable Keymaps: Built-in presets for Default (Espresso), Gmail, Vim, and Pine/Mutt, with custom keybinding overrides and persistence via Preferences (P).
- Markdown Email Composing & Live Preview: Write emails using Markdown with live terminal preview (F4 / Ctrl+V) and automatic generation of multipart HTML emails upon send (Ctrl+M).
- Single-Key Inbox Triaging & Batch Selection: Archive (e), move to trash (d), mark as spam (J), or move to folder (v). Multi-select messages (x) to perform bulk triage actions in a single step.
- Unread-Only Folder Filter: Single-key toggle (Ctrl+U / V) to filter the current folder to unread messages only.
- RFC 5545 iCalendar & RFC 6047 iMIP RSVP: Inline calendar invitation rendering with event title, organizer, dates, and location. Quick one-key RSVP responses (1 Accept, 2 Tentative, 3 Decline) dispatched via SMTP.
- Real-Time IMAP Push (RFC 2177 IDLE): Instant incoming email notifications via persistent background IMAP IDLE connections.
- Configurable Auto-Sync & Desktop Notifications: Configurable periodic background sync interval timer, plus native cross-platform desktop notifications (notify-send, osascript, PowerShell) and audible bell cues.
- Adjustable Pane Splitters: Adjust folder list width ({ / }) and message list / reading pane widths ([ / ]) or resize splitters via mouse dragging.
- Dark and Light Modes: Full Dark and Light mode variants for all six TrueColor theme palettes (Espresso, Catppuccin, Dracula, Nord, Gruvbox, Solarized).

### Changed & Improved
- Upgraded espressoTUI dependency to 1.3.0 and integrated upstream beans bridge.
- Onboarding Wizard: Added ability to skip onboarding on first run via Esc or backdrop click, and exit cleanly via q or Ctrl+Q.
- Folder sync performance: Optimized batch fetching and ensured background operations do not block message navigation.
- Unicode and visual cell width alignment: Fixed border alignments across 3-pane layouts and modal dialogs when rendering emojis and multibyte characters.

### Fixed
- IMAP Client: Resolved discover_folders method attribute error and ensured subscription states synchronize cleanly.
- Mouse hit-testing: Corrected row alignment and coordinates in Preferences modal.
- Keymap test portability: Handled Windows temporary directory path differences in test suite.
## [0.1.1] - 2026-10-28


> Fix IMAP flag synchronization and add debug logging

### Bug Fixes and Enhancements in v0.1.1

- IMAP Client Context Manager: Fixed a runtime error where flag synchronization failed due to IMAPClient lacking the context manager protocol.
- Debug Logging: Added a --debug (-d) CLI flag and persistent rotating file logging (defaults to ~/.local/share/espressomail/espressomail.log).
- Folder Subscription Sync: Ensured startup and 'sync all' only synchronize subscribed folders, while unsubscribed folders are fetched on-demand when selected and entered.
- Folder Tree Controls: Remote IMAP SUBSCRIBE and UNSUBSCRIBE commands are now automatically dispatched when toggling folder subscription (s).
## [0.1.0] - 2026-10-28

> EspressoMail 0.1.0: First release - fresh terminal email brewed with the Espresso framework.

Welcome to the debut release of EspressoMail (v0.1.0)!

If you have ever wanted to check, triage, and send emails from the cozy comfort of your terminal without sacrificing modern conveniences, this first release is freshly brewed for you. EspressoMail brings multi-account IMAP/SMTP, an offline SQLite cache with snappy fuzzy search, message threading, GPG/PGP signing and encryption, ManageSieve filter rules, CardDAV contact synchronization, and inline image rendering straight into your command line.

A massive shoutout to the Espresso TUI framework (espressoTUI) and The Elm Architecture (TEA). Building a responsive, multi-pane, interactive terminal application with splitters, modals, drop-down menus, and non-blocking background async jobs could have been a headache, but Espresso made designing this user interface an absolute joy.

### Added
- Core Architecture: Modern terminal email client built on espressoTUI using The Elm Architecture (TEA) with non-blocking async background tasks.
- Protocols and Accounts: Multi-account IMAP (idle, fetch, flag sync), SMTP (STARTTLS, SSL/TLS, authentication), Sieve (ManageSieve protocol), and CardDAV (address book synchronization).
- Offline Storage and Search: SQLite-backed local cache with full-text message search, sender/recipient queries, subject indexing, and fuzzy keyword matching.
- Three-Pane UI: Responsive terminal interface featuring hierarchical folder tree, two-line message list, and reading pane with interactive splitter resizing.
- Unified Mailboxes: Unified Inbox, All Unread, Starred, Sent, Drafts, and Trash across all configured accounts.
- Folder Subscriptions: Selective folder sync for subscribed mailboxes on regular schedule, with on-demand background fetch for unsubscribed folders.
- Sieve Filter Manager: In-app ManageSieve filter editor with syntax checking, server script synchronization, default focus, and tab navigation.
- CardDAV Address Book: Address book modal with smooth scrolling, mouse support, credential validation, contact autocomplete in Compose, and shortcut to add email senders to contacts.
- Security and Cryptography: PGP/GPG integration for signing, signature verification, message encryption, and decryption via standard GnuPG.
- Inline Image Attachments: Multi-backend terminal image rendering supporting Chafa, Sixel, Kitty graphics protocol, half-block Unicode, and Braille patterns.
- Themes and Color Modes: Comprehensive Dark and Light modes (espresso, catppuccin, dracula, nord, gruvbox, solarized_light) with complete background coverage.
- Terminal Hyperlinks: Clickable URLs in reading pane using standard OSC 8 terminal hyperlink escapes.
- Modals and Dialogs: Full-screen and modal overlays for Compose, Preferences, Account Setup & Editing, Attachment Downloader, and Exit Confirmation.
- Packaging and CI/CD: Standalone single-binary executables (Linux x86_64 and Windows x86_64.exe), native Linux packages (Debian .deb, RPM, Arch Linux), PyPI publication, and automated release management.

[Unreleased]: https://github.com/kimusan/EspressoMail/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/kimusan/EspressoMail/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/kimusan/EspressoMail/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kimusan/EspressoMail/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/kimusan/EspressoMail/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kimusan/EspressoMail/releases/tag/v0.1.0
