# ☕ EspressoMail

> A fast, declarative, multi-account terminal email client built on the **[Espresso TUI](https://github.com/kimusan/espresso)** framework (The Elm Architecture).

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Framework: Espresso TUI](https://img.shields.io/badge/TUI-Espresso-orange.svg)](https://github.com/kimusan/espresso)
[![Architecture: TEA](https://img.shields.io/badge/architecture-Elm-brightgreen.svg)](docs/architecture.md)

```text
          (  )   (   )  )
           ) (   )  (  (
           ( )  (    ) )
         ___________________
        <___________________> ___
        |  ╔═════════════╗  |/ _ \
        |  ║ ✉  ESPRESSO ║  | | | |
        |  ║ ╲  ╱  MAIL  ║  |_| |_|
        |  ╚══╲╱═════════╝  |\___/
        |___________________|
         \_________________/
```

---

## ✨ Highlights

### 🖥️ UI & Interaction
* **Responsive 3-Pane Layout**:
  1. **Mailboxes Pane**: Unified Mailboxes (`Unified Inbox`, `All Starred`, `All Drafts`, `All Sent`, `All Archive`, `All Trash`) plus per-account IMAP folder trees with live unread/total badges.
  2. **Message & Thread List**: Conversation threading (`├`, `╰─`) with message counts (`[3]`), unread indicators (`●`), stars (`★`), attachment icons (`📎`), and PGP badges (`🔒`).
  3. **Reading Pane**: Formatted headers, color-coded multi-depth reply quote bars (`│`), syntax-highlighted code blocks, HTML-to-terminal conversion, PGP security banners, and inline image rendering.
* **Animated Startup Splash Screen**: Steaming espresso cup art, TrueColor linear gradients, progress bar, and instant key/mouse skip.
* **Interactive Drop-Down Menus**: Full top menu bar (**File**, **Mail**, **View**, **Tools**, **Help**) accessible via mouse click/hover or `F10` / `m`.
* **Full Mouse & Keyboard Support**: Click panes to focus, click folders/messages to open, scroll any pane with the mouse wheel, or drive the entire client via Vim/Mutt-inspired keyboard shortcuts and `Ctrl+P` Command Palette.
* **Terminal Image Rendering**: View inline image attachments directly in the reading pane using **`chafa`**, **DEC Sixel** (`img2sixel` or native Sixel encoder), **Kitty Graphics Protocol**, **24-bit Unicode Half-Blocks (`▀`)**, or **Braille (`⣿`)** — cycleable on the fly with `i`.
* **Theme Engine**: Swap between 6 built-in TrueColor themes at runtime (`T`): **Espresso Dark**, **Catppuccin Mocha**, **Dracula**, **Nord Arctic**, **Gruvbox Dark**, and **Solarized Light**.

### 📬 Core Email & Advanced Features
* **Multi-Account IMAP & SMTP**: Connect multiple work and personal accounts simultaneously over SSL/TLS or STARTTLS.
* **Guided Onboarding Wizard**: Interactive 3-step setup flow with built-in presets for **Fastmail**, **Gmail**, **Outlook / Microsoft 365**, **ProtonMail Bridge**, **iCloud**, **Custom Dovecot/Postfix**, and an instant **Offline Demo Workspace**.
* **Local SQLite Store & Offline Access**: WAL-mode SQLite database caching accounts, folders, messages, MIME attachments, drafts, contacts, and Sieve scripts.
* **Instant Fuzzy Search (`/`)**: Typo-tolerant multi-field fuzzy ranking combined with structured search operators (`from:`, `to:`, `subject:`, `folder:`, `has:attachment`, `is:unread`, `is:starred`, `is:pgp`).
* **ManageSieve Filter Manager (`F`)**: Visual rule builder, RFC 5228 Sieve compiler, static syntax validator, local rule runner, and RFC 5804 ManageSieve uploader.
* **GPG / OpenPGP Integration (`G`)**: Cleartext signing, signature verification, recipient encryption, decryption, and Ed25519 key generation.

---

## 🚀 Installation

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

# Launch with pre-populated multi-account demo emails, threads, PGP messages, and Sieve filters
espressomail --demo

# Launch with a specific color theme and Sixel image rendering, skipping the splash screen
espressomail --demo --no-splash --theme catppuccin --image-backend sixel

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
| **`e`** | Archive selected message |
| **`d` / `Delete`** | Move message to Trash (or permanently delete if already in Trash) |
| **`X`** | Empty Trash permanently |
| **`w`** | Download / save incoming attachments from selected email to disk |
| **`/`** | Open Instant Fuzzy Search across local SQLite database |
| **`t`** | Toggle conversation threading view (`Threaded` vs `Flat`) |
| **`T`** | Cycle active color scheme (`Espresso`, `Catppuccin`, `Dracula`, `Nord`, `Gruvbox`, `Solarized`) |
| **`i`** | Cycle terminal image renderer (`Auto`, `Chafa`, `Sixel`, `Kitty`, `Half-Block`, `Braille`, `Off`) |
| **`H`** | Toggle full RFC 5322 headers in Reading Pane |
| **`U`** | Jump directly to Unified Inbox |
| **`B`** | Open Local Address Book & Contacts Manager |
| **`F`** | Open Sieve Filter Rules & RFC 5228 Script Manager |
| **`G`** | Open GPG / OpenPGP Keyring & Security Manager |
| **`O`** | Launch Guided Account Onboarding Wizard |
| **`S`** | Synchronize all enabled IMAP accounts |
| **`?`** | Show Keyboard & Mouse Shortcuts Help Modal |
| **`q`** | Quit EspressoMail |

### Inside the Compose Modal (`c` / `r` / `f`)
* **`Tab` / `Shift+Tab`**: Move between `From` account selector, `To`, `Cc`, `Subject`, and `Body` editor
* **`Ctrl+A`**: Open interactive `FilePicker` to attach files
* **`Ctrl+G`**: Toggle OpenPGP Cleartext Signature (`[x] PGP Sign`)
* **`Ctrl+E`**: Toggle OpenPGP Encryption (`[x] PGP Encrypt`)
* **`Ctrl+S`**: Save message to `Drafts`
* **`F5` / `Ctrl+X`**: Send message via SMTP (and archive to `Sent`)

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

## 📄 License

MIT License. Copyright (c) 2026 Kim Schulz. See [LICENSE](LICENSE) for details.
