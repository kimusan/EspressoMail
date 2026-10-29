# ☕ EspressoMail

> A fast, declarative, multi-account terminal email client built on the **[Espresso TUI](https://github.com/kimusan/espresso)** framework (The Elm Architecture).

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Framework: Espresso TUI](https://img.shields.io/badge/TUI-Espresso-orange.svg)](https://github.com/kimusan/espresso)
[![Architecture: TEA](https://img.shields.io/badge/architecture-Elm-brightgreen.svg)](docs/architecture.md)

<p align="center">
  <img src="https://raw.githubusercontent.com/kimusan/EspressoMail/main/docs/images/demo.gif" alt="EspressoMail Terminal Email Client Demo" width="100%" />
</p>

---

## ✨ Highlights

### 🖥️ UI & Interaction
* **Responsive 3-Pane Layout**:
  1. **Mailboxes Pane**: Unified Mailboxes (`Unified Inbox`, `All Starred`, `All Drafts`, `All Sent`, `All Archive`, `All Trash`) plus per-account IMAP folder trees with live unread/total badges. Adjust splitter width with `{` / `}` or mouse dragging.
  2. **Message & Thread List**: Conversation threading (`├`, `╰─`) with message counts (`[3]`), unread indicators (`●`), stars (`★`), attachment icons (`📎`), and PGP badges (`🔒`). Adjust reading pane splitter with `[` / `]`.
  3. **Reading Pane**: Formatted headers, color-coded multi-depth reply quote bars (`│`), syntax-highlighted code blocks, HTML-to-terminal conversion, PGP security banners, iCalendar invite cards, and inline image rendering.
* **Single-Key Inbox Triaging & Batch Actions**: Rapidly triage incoming mail with archive (`e`), trash (`d`), spam (`J`), move to folder (`v`), star (`s`), read/unread (`u`), or multi-select (`x`) to batch apply actions.
* **Unread-Only Folder Filter (`Ctrl+U` / `V`)**: Instant single-key filter to isolate unread messages in any folder.
* **Animated Startup Splash Screen**: Steaming espresso cup art, TrueColor linear gradients, progress bar, and instant key/mouse skip.
* **Interactive Drop-Down Menus**: Full top menu bar (**File**, **Mail**, **View**, **Tools**, **Help**) accessible via mouse click/hover or `F10` / `m`.
* **Full Mouse & Keyboard Support**: Click panes to focus, click folders/messages to open, scroll any pane with the mouse wheel, or drive the entire client via keyboard shortcuts and `Ctrl+P` Command Palette.
* **Configurable Keymaps**: Choose from built-in presets (**Default**, **Gmail**, **Vim**, **Pine/Mutt**) or define custom keybindings in the Preferences modal (`P`).
* **Terminal Image Rendering**: View inline image attachments directly in the reading pane using **`chafa`**, **DEC Sixel** (`img2sixel` or native Sixel encoder), **Kitty Graphics Protocol**, **24-bit Unicode Half-Blocks (`▀`)**, or **Braille (`⣿`)** — cycleable on the fly with `i`.
* **Theme Engine & Dark/Light Modes**: 6 TrueColor theme families with both Dark and Light variants (**Espresso**, **Catppuccin**, **Dracula**, **Nord**, **Gruvbox**, and **Solarized**), switchable via `T` or Preferences (`P`).

### 📬 Core Email & Advanced Features
* **Multi-Account IMAP & SMTP**: Connect multiple work and personal accounts simultaneously over SSL/TLS or STARTTLS.
* **Real-Time IMAP Push (RFC 2177 IDLE) & Auto-Sync**: Instant delivery of incoming emails via background IMAP IDLE connections, supplemented by a configurable periodic auto-sync timer (1m, 5m, 15m, etc.).
* **Desktop Notifications & Sound Alerts**: Cross-platform system notifications via `notify-send`, macOS `osascript`, and Windows PowerShell toast, with optional audio bell cues.
* **Full-Text SQLite FTS5 & Remote IMAP Search (`/`)**: Blazing fast local search powered by SQLite FTS5 with BM25 ranking, snippet highlighting, and structured operators (`from:`, `to:`, `subject:`, `folder:`, `has:attachment`, `is:unread`), plus remote IMAP server search (`Ctrl+R` / `F5`) supporting MULTISEARCH and ESEARCH.
* **Markdown Email Composing & Live Preview**: Write outgoing messages in Markdown with side-by-side or toggled live terminal preview (`F4`), automatically generating dual-part plain text and styled HTML MIME emails (`Ctrl+M`).
* **External Editor Integration (`Ctrl+O`)**: Seamlessly edit drafts in your preferred terminal editor (`$EDITOR` / `$VISUAL`, nano, vim, nvim, micro, emacs) with automatic terminal mode suspension and resumption.
* **RFC 5545 iCalendar & RFC 6047 iMIP RSVP (`1`/`2`/`3`)**: Inline calendar invite rendering displaying event date, time, location, organizer, and attendees, with 1-key RSVP responses (`1` Accept, `2` Tentative, `3` Decline) sent via SMTP.
* **Guided Onboarding Wizard (`O`)**: Interactive 3-step setup with **Custom IMAP / SMTP Server** as the default alongside presets for **Fastmail**, **Gmail**, **Outlook / Microsoft 365**, **ProtonMail Bridge**, and **iCloud**, with credential verification, backdrop dismiss, and clean exit shortcuts (`Esc` to skip, `q` or `Ctrl+Q` to quit).
* **Account Settings Editor (`A`)**: Modify existing account settings at any time (password, login username, IMAP/SMTP hosts, ports, SSL/TLS or STARTTLS security, display name, signature, or account deletion) with live connection testing (`Ctrl+T`).
* **ManageSieve Filter Manager (`F`)**: Visual rule builder, RFC 5228 Sieve compiler, static syntax validator, local rule runner, and RFC 5804 ManageSieve uploader.
* **CardDAV Address Book & Contact Autocomplete (`B`)**: Address book synchronization, sender contact harvesting, and autocomplete inside the email composer.
* **GPG / OpenPGP Integration (`G`)**: Cleartext signing, signature verification, recipient encryption, decryption, and Ed25519 key generation.

---

## 🚀 Installation

### Standalone Binaries (Linux & Windows)
Pre-built standalone single-binary executables with zero external dependencies are attached to every [GitHub Release](https://github.com/kimusan/EspressoMail/releases):
- **Linux x86_64**: `espressomail-linux-x86_64`
- **Windows x86_64**: `espressomail-windows-x86_64.exe`

### Native Linux Packages (.deb, .rpm, Arch Linux)
Pre-built native packages are available on the [Releases page](https://github.com/kimusan/EspressoMail/releases):
```bash
# Debian / Ubuntu / Mint (.deb)
sudo dpkg -i espressomail_<version>_amd64.deb

# Fedora / RHEL / openSUSE (.rpm)
sudo rpm -i espressomail-<version>-1.x86_64.rpm

# Arch Linux (.pkg.tar.zst)
sudo pacman -U espressomail-<version>-1-x86_64.pkg.tar.zst
```

### From PyPI
```bash
pip install espressomail
```
Or install in an isolated environment with `pipx`:
```bash
pipx install espressomail
```

### Optional Image Dependencies
For additional PNG/JPEG decoding when `chafa` is not installed on your system:
```bash
pip install "espressomail[images]"
```

### From Source (Development)
```bash
git clone https://github.com/kimusan/EspressoMail.git
cd EspressoMail
pip install -e ".[dev,images]"
```

---

## 🛠️ Quick Start & CLI Usage

```bash
# Launch EspressoMail (starts onboarding wizard on first run)
espressomail

# Launch with a specific color theme and Sixel image rendering, skipping the splash screen
espressomail --no-splash --theme catppuccin --image-backend sixel

# Use a custom SQLite database file path
espressomail --db ~/mail/custom_store.db
```

---

## ⌨️ Keyboard & Mouse Shortcuts

| Key / Action | Description |
| :--- | :--- |
| **`Tab` / `Shift+Tab`** | Cycle focus between Mailboxes, Message List, and Reading Pane |
| **`j` / `k` / `↑` / `↓`** | Navigate folders, message rows, or scroll the reading pane |
| **`Enter` / `Space`** | Open selected folder, read message, or edit draft in composer |
| **`F10` / `m`** | Open top interactive Drop-Down Menu Bar |
| **`Ctrl+P`** | Open fuzzy Command Palette |
| **`c`** | Compose new email message |
| **`r` / `R`** | Reply to sender / Reply to all recipients |
| **`f`** | Forward selected message |
| **`s`** | Toggle Star / Flag (`★`) on selected message |
| **`u`** | Toggle Read / Unread (`●`) status |
| **`Ctrl+U` / `V`** | Toggle Unread-only filter in active folder |
| **`x`** | Toggle message multi-selection for batch actions |
| **`e` / `y`** | Archive selected message(s) (or edit account if Mailboxes focused) |
| **`d` / `Delete`** | Move selected message(s) to Trash (or delete permanently if in Trash) |
| **`J`** | Move selected message(s) to Spam / Junk |
| **`v`** | Move selected message to specific folder modal |
| **`1` / `2` / `3`** | RSVP to calendar invitation (`1` Accept, `2` Tentative, `3` Decline) |
| **`X`** | Empty Trash permanently |
| **`w`** | Download / save incoming attachments from selected email to disk |
| **`/`** | Open Full-Text Search modal (SQLite FTS5 with BM25 ranking and snippets) |
| **`t`** | Toggle conversation threading view (`Threaded` vs `Flat`) |
| **`T`** | Cycle active theme (`Espresso`, `Catppuccin`, `Dracula`, `Nord`, `Gruvbox`, `Solarized`) |
| **`i`** | Cycle terminal image renderer (`Auto`, `Chafa`, `Sixel`, `Kitty`, `Half-Block`, `Braille`, `Off`) |
| **`[` / `]`** | Widen Reading Pane (`[`) or widen Message List (`]`) |
| **`{` / `}`** | Narrow (`{`) or widen (`}`) Folder List pane |
| **`H`** | Toggle full RFC 5322 headers in Reading Pane |
| **`U`** | Jump directly to Unified Inbox |
| **`B`** | Open Local Address Book & Contacts Manager |
| **`F`** | Open Sieve Filter Rules & RFC 5228 Script Manager |
| **`G`** | Open GPG / OpenPGP Keyring & Security Manager |
| **`A`** | Edit Existing Account Settings (Password, IMAP/SMTP hosts, ports, TLS) |
| **`P` / `p`** | Open Preferences & Configuration modal |
| **`O`** | Launch Guided Account Onboarding Wizard (Add New Account) |
| **`Ctrl+R` / `F5`** | Synchronize active folder with IMAP server (or `r` in Mailboxes pane) |
| **`S`** | Synchronize all enabled IMAP accounts in background |
| **`?`** | Show Keyboard & Mouse Shortcuts Help Modal |
| **`q`** | Quit EspressoMail |

### Inside the Search Modal (`/`)
* **`Type query`**: Instant FTS5 full-text match with BM25 ranking and snippets
* **`↑` / `↓`**: Navigate search result list
* **`Enter`**: Open selected email in the main reading view
* **`Ctrl+R` / `F5`**: Perform remote IMAP server search (MULTISEARCH / ESEARCH)
* **`Esc`**: Close search modal

### Inside the Compose Modal (`c` / `r` / `f`)
* **`Tab` / `Shift+Tab`**: Move between `From` account selector, `To`, `Cc`, `Subject`, and `Body` editor
* **`Ctrl+A`**: Open interactive `FilePicker` to attach files
* **`Ctrl+M`**: Toggle Markdown to HTML rendering on send
* **`F4` / `Ctrl+V`**: Toggle Markdown live terminal preview
* **`Ctrl+O` / `F6`**: Open draft in external editor (`$EDITOR` / nano / vim / emacs)
* **`Ctrl+G`**: Toggle OpenPGP Cleartext Signature (`[x] PGP Sign`)
* **`Ctrl+E`**: Toggle OpenPGP Encryption (`[x] PGP Encrypt`)
* **`Ctrl+S`**: Save message to `Drafts`
* **`F5` / `Ctrl+Enter` / `Ctrl+X`**: Send message via SMTP (and archive to `Sent`)
* **`Esc`**: Close composer (prompts to save draft if modified)

---

## 📚 Documentation

* **[Architecture & TEA Design](docs/architecture.md)**: How EspressoMail structures state, commands, SQLite persistence, JWZ threading, and protocol clients.
* **[User Guide](docs/user_guide.md)**: Detailed walkthrough of account onboarding, fuzzy search operators, terminal image rendering (`chafa` / Sixel), Sieve filters, and PGP encryption.
* **[Contributing Guide](CONTRIBUTING.md)**: Development workflow, running unit tests, and Conventional Commits guidelines.

---

## 🧪 Running Tests
 
```bash
python3 -m unittest discover -s tests -v
```

---

## 📦 Release Automation

EspressoMail includes an automated release script:

```bash
# Dry run preflight checks and version calculation
python3 scripts/release.py patch --dry-run

# Release with title summary and full markdown release notes
python3 scripts/release.py minor \
    -m "Release v0.2.0: Contacts & Filter Enhancements" \
    -n "### Highlights\n- CardDAV address book sync\n- ManageSieve live filter editor"

# Release using an external notes markdown file (or stdin pipe)
python3 scripts/release.py 0.2.0 --notes-file release_notes.md
cat release_notes.md | python3 scripts/release.py patch --notes-file -
```

Pushing a tag `vX.Y.Z` triggers the GitHub Actions release workflow:
1. Builds sdist and wheel and publishes to **PyPI**.
2. Compiles standalone PyInstaller single-binary executables for **Linux** (`x86_64`) and **Windows** (`x86_64.exe`).
3. Generates native Linux packages: **Debian** (`.deb`), **Red Hat / Fedora** (`.rpm`), and **Arch Linux** (`.pkg.tar.zst`).
4. Generates SHA256 checksums and publishes the GitHub Release with all artifacts attached.

---

## 📄 License

MIT License. Copyright (c) 2026 Kim Schulz. See [LICENSE](LICENSE) for details.
