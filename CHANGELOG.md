# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/kimusan/EspressoMail/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kimusan/EspressoMail/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/kimusan/EspressoMail/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kimusan/EspressoMail/releases/tag/v0.1.0
