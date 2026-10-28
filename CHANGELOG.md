# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/kimusan/EspressoMail/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/kimusan/EspressoMail/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kimusan/EspressoMail/releases/tag/v0.1.0
