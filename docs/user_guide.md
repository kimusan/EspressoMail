# EspressoMail User Guide

Welcome to **EspressoMail**! This guide covers setting up your accounts, navigating the 3-pane interface, triaging mail, searching with FTS5 and remote IMAP, composing in Markdown and external editors, handling calendar invites, configuring Sieve filters, and managing OpenPGP security.

---

## 1. Account Setup & Editing Account Settings

### First-Time Account Setup (`O` / Onboarding Wizard)
When you launch `espressomail` for the first time on an empty database (or press **`O`** / **File → Add New Account...** at any time), the guided 3-step Onboarding Wizard opens:

1. **Step 1 — Choose Server Type or Preset**:
   - **Custom IMAP / SMTP Server** *(Default first option)*: Configure standard self-hosted or custom IMAP and SMTP servers (Dovecot, Postfix, Stalwart, Cyrus, etc.).
   - **Fastmail**: Pre-fills `imap.fastmail.com:993` (SSL/TLS), `smtp.fastmail.com:587` (STARTTLS), and ManageSieve port `4190`.
   - **Gmail / Google Workspace**: Pre-fills `imap.gmail.com:993` and `smtp.gmail.com:587` (use a 16-character Google App Password).
   - **Outlook / Microsoft 365**: Pre-fills `outlook.office365.com:993` and `smtp.office365.com:587`.
   - **ProtonMail Bridge**: Pre-fills `127.0.0.1:1143` (IMAP STARTTLS) and `127.0.0.1:1025` (SMTP STARTTLS).
   - **Apple iCloud Mail**: Pre-fills `imap.mail.me.com:993` and `smtp.mail.me.com:587`.
   - *Skipping or Quitting*: Press **`Esc`** (or click the backdrop outside the modal) to skip onboarding and explore the client immediately. Press **`q`** on Step 1 (or **`Ctrl+Q`** / **`Ctrl+C`** at any step) to quit the application cleanly.
2. **Step 2 — Identity & Credentials**:
   - Enter your Account Label, Full Name, Email Address, Login Username (optional, defaults to Email Address), and Password (press `Ctrl+R` to toggle password visibility). Use `Tab` / `Shift+Tab` to move between fields and `Enter` to advance.
3. **Step 3 — Server Endpoints, TLS Security & Credential Validation**:
   - Verify or customize the IMAP/SMTP hostnames, ports, and TLS modes (`SSL_TLS`, `STARTTLS`, or `NONE` using `Space` or `←`/`→`).
   - Leave `[x] Auto-generate Ed25519 OpenPGP Key Pair` enabled (or toggle with `Ctrl+G`) to automatically provision a signing/encryption key for the account.
   - Press **`Enter`** to validate both IMAP and SMTP credentials against the remote servers before finishing onboarding. If authentication fails, the wizard displays the server error so you can adjust your password or endpoints immediately.

### Changing Existing Account Settings (`A` / `File → Edit Account Settings...`)
Press **`A`** anywhere in the main workspace (or press **`e`** while focused on the `📁 Mailboxes` pane) to open the **Account Settings & Credentials** modal:
* Switch between configured accounts at the top using `←` / `→`.
* Update your **Password** (press `Ctrl+R` to reveal/mask), **Login Username**, **Account Name**, **Full Name**, **IMAP/SMTP Hosts & Ports**, **TLS Security**, or **Signature**.
* Press **`Enter` / `Ctrl+S`** to validate the updated credentials and save, **`Ctrl+T`** to test authentication live, **`Ctrl+F`** to save offline without a live server check, or **`Ctrl+D`** to delete the account.

---

## 2. Navigating the 3-Pane Interface & Inbox Triaging

EspressoMail divides your terminal into three responsive panes:

* **Left Pane (`📁 Mailboxes`)**:
  - Top section: **Unified Mailboxes** (`Unified Inbox`, `All Starred`, `All Drafts`, `All Sent`, `All Archive`, `All Trash`) aggregating mail across every configured account.
  - Below: Individual account sections with per-account folders and live unread badges.
  - Press **`U`** at any time to jump straight to the **Unified Inbox**.
  - Adjust folder list width with **`{`** (narrow) and **`}`** (widen), or drag the vertical border with the mouse.
* **Center Pane (`✉️ Message List`)**:
  - Displays messages or conversation threads in the active mailbox.
  - Press **`t`** to toggle between **Threaded** mode (showing conversation depth `╰─` and message count `[3]`) and **Flat** chronological mode.
  - Press **`Ctrl+U`** or **`V`** to toggle the **Unread-only filter** to focus exclusively on unread mail in the current folder.
  - Adjust the message list / reader splitter with **`[`** (expand reader) and **`]`** (expand message list), or drag the border with the mouse. Double-click the splitter to reset to default responsive widths.
* **Right Pane (`📖 Reading Pane`)**:
  - Displays the formatted email header, PGP verification status banner, calendar invitations, attachment chips, conversation history, formatted body text, and inline image previews.
  - Scroll with `j`/`k`, `↑`/`↓`, `PageUp`/`PageDown`, or the **Mouse Wheel**.

### Rapid Inbox Triaging
EspressoMail supports single-key triage workflows to achieve Inbox Zero:
* **`e` / `y`**: Archive selected message (moves from Inbox to Archive).
* **`d` / `Delete`**: Move message to Trash (or permanently delete if already in Trash).
* **`J`**: Mark message as Spam and move to Junk folder.
* **`v`**: Open the **Move to Folder** modal to relocate the message to any folder.
* **`s`**: Toggle Star / Flag (`★`).
* **`u`**: Toggle Read / Unread (`●`) status.
* **`x`**: Multi-select toggle. Check multiple messages, then press `e`, `d`, or `J` to batch archive, trash, or mark as spam in a single operation.

---

## 3. Full-Text Search (SQLite FTS5) & Remote IMAP Search (`/`)

Press **`/`** to open the Search modal. EspressoMail features a dual-layer search system:

### 3.1 Local SQLite FTS5 Full-Text Search
As you type, queries are evaluated in real time against SQLite's Full-Text Search (`messages_fts`) virtual table with **BM25 relevance ranking**:
* Snippet previews highlight matching keywords in context.
* Multi-field matching scans across subjects, senders, recipients, body text, and attachment filenames.

### 3.2 Structured Search Operators
Combine free-text search terms with structured filter operators:
* `from:elena` — Match sender name or email address
* `to:team` — Match recipient addresses
* `subject:release` — Match subject line
* `body:deadline` — Match content in email message body
* `folder:archive` — Restrict results to a specific folder
* `att:invoice` — Match attachment filenames
* `has:attachment` — Filter to messages with file or image attachments
* `is:unread` / `is:read` — Filter by read status
* `is:starred` — Only starred (`★`) messages
* `is:pgp` — Only PGP-signed or PGP-encrypted messages

Example query:
```text
from:elena has:attachment is:starred sixel
```

### 3.3 Remote IMAP Server Search (`Ctrl+R` / `F5`)
When searching for older messages that haven't yet been synced to your local cache, press **`Ctrl+R`** or **`F5`** inside the search modal. EspressoMail dispatches an IMAP `MULTISEARCH` (RFC 7377) or `ESEARCH` query directly to the remote server and displays remote matches immediately.

---

## 4. Composing Emails, Markdown & External Editor Integration

Press **`c`** to compose a new message, **`r`** to reply, **`R`** to reply all, or **`f`** to forward.

### Markdown Composing & Live Preview
* **Markdown to HTML (`Ctrl+M`)**: Toggle Markdown rendering. When enabled, your Markdown body is automatically compiled into a clean, modern HTML alternative part alongside the plain-text representation when sent.
* **Live Terminal Preview (`F4` / `Ctrl+V`)**: Toggle interactive side-by-side or full live terminal preview to see how your Markdown headings, lists, tables, links, and code blocks look before hitting send.

### External Editor Integration (`Ctrl+O` / `F6`)
Prefer writing emails in your favorite terminal editor?
* Press **`Ctrl+O`** or **`F6`** inside the Compose modal.
* EspressoMail smoothly suspends terminal raw mode and launches your preferred editor (`$EDITOR` / `$VISUAL`, nano, vim, nvim, micro, or emacs).
* Save and exit the editor; EspressoMail instantly restores the full-screen TUI and populates your composer with the updated text.
* Configure your preferred editor command in **Preferences (`P`)**.

### Attachments & Drafts
* **Attaching Files**: Press **`Ctrl+A`** inside the Compose modal to open the interactive directory file picker.
* **Saving Drafts**: Press **`Ctrl+S`** to save your draft to SQLite and the remote `Drafts` folder. Open any draft in the `Drafts` mailbox and press `Enter` to resume editing.
* **Sending**: Press **`F5`**, **`Ctrl+Enter`**, or **`Ctrl+X`** to send via SMTP.

---

## 5. Calendar Invitations & RFC 6047 iMIP RSVP (`1`/`2`/`3`)

When an email contains an iCalendar invitation (`.ics` or `text/calendar`), EspressoMail parses the RFC 5545 payload and displays a formatted calendar card in the Reading Pane:
* Shows Event Title, Organizer, Date, Time, Duration, Location, Attendees, and current RSVP status.
* **One-Key RSVP Response**:
  - Press **`1`**: Accept invitation
  - Press **`2`**: Tentative
  - Press **`3`**: Decline
* Pressing `1`, `2`, or `3` opens the RSVP dialog with optional comments and dispatches a standard RFC 6047 iMIP email reply via SMTP to the organizer, updating your status seamlessly.

---

## 6. Real-Time IMAP Push (RFC 2177 IDLE), Auto-Sync & Notifications

EspressoMail keeps your mailboxes synchronized without manual intervention:

* **Real-Time IMAP Push (IDLE)**:
  - Enabled by default for supported servers. EspressoMail maintains a low-resource background connection using IMAP IDLE.
  - New incoming emails appear in your message list instantly without polling delays.
* **Periodic Auto-Sync**:
  - Configure background synchronization intervals (Off, 1m, 2m, 5m, 15m, 30m, 60m) in Preferences (`P`).
  - Press **`S`** at any time to trigger an immediate manual sync across all configured accounts.
* **Desktop Notifications & Sound Alerts**:
  - Displays desktop notifications on new emails using native system notification daemons (`notify-send` on Linux, `osascript` on macOS, and Windows PowerShell toast).
  - Optional audible bell/cue can be enabled in Preferences.

---

## 7. Preferences & Keymap Presets (`P` / `p`)

Press **`P`** or select **File → Preferences & Configuration...** to open the comprehensive settings modal:
* **Keymap Presets**: Switch between **Default (Espresso)**, **Gmail**, **Vim**, and **Pine/Mutt** keyboard mappings to match your muscle memory.
* **Theme & Color Mode**: Choose from 6 theme families (**Espresso**, **Catppuccin**, **Dracula**, **Nord**, **Gruvbox**, **Solarized**) and toggle between **Dark** and **Light** modes.
* **Image Renderer**: Set default backend for image attachments (`Auto`, `Chafa`, `Sixel`, `Kitty`, `Half-Block`, `Braille`, `Off`).
* **External Editor**: Select `auto` (`$EDITOR`), `nano`, `vim`, `nvim`, `micro`, `emacs`, or a custom command.
* **Download Directory**: Specify the destination path for saved attachments (`w`).
* **Threading & Headers**: Toggle default JWZ conversation threading and raw RFC 5322 header displays.
* **Sync & Notifications**: Configure startup sync, IMAP IDLE push, auto-sync intervals, and desktop alerts.

---

## 8. Terminal Image Rendering (`i`)

When an email contains image attachments (`.png`, `.jpg`, `.bmp`, `.ppm`, `.sixel`), EspressoMail renders an inline preview at the bottom of the Reading Pane.

Press **`i`** (or select **View → Cycle Image Renderer**) to cycle through available backends:
1. **`auto`**: Uses `chafa` if installed on `PATH`, falling back to 24-bit Unicode half-blocks (`▀`).
2. **`chafa`**: High-performance rasterization using the external `chafa` CLI tool (`--format=symbols`).
3. **`sixel`**: Hardware DEC Sixel graphics via `img2sixel` or Espresso's built-in VT340 Sixel encoder.
4. **`kitty`**: Native Kitty Graphics Protocol (APC) for Kitty, Ghostty, and WezTerm.
5. **`half_block`**: Universal 24-bit TrueColor upper-half blocks (`▀`) compatible with all terminals and `tmux`.
6. **`braille`**: High-density 2×4 Unicode Braille dot matrix (`⣿`).
7. **`off`**: Disables inline image previews.

---

## 9. Sieve Filters (`F`)

Press **`F`** to open the **ManageSieve & Filter Rules** modal:
* **`Space`**: Enable or disable the highlighted filter rule.
* **`a`**: Create a new filter rule (Name, Header Field, Match Pattern, Target Folder).
* **`d`**: Delete the selected rule.
* **`v`**: Validate the compiled RFC 5228 Sieve script syntax.
* **`r`**: Execute all active Sieve rules immediately against your local Inbox.

---

## 10. GPG / OpenPGP Security (`G`)

* **Viewing & Generating Keys**: Press **`G`** to open the OpenPGP Keyring Manager. Press **`g`** to generate a new Ed25519 key pair for your active account.
* **Verifying & Decrypting Mail**: Press **`v`** inside the PGP Manager to re-verify the active message's signature, or **`x`** to decrypt an encrypted payload.
* **Signing & Encrypting Outgoing Mail**: In the Compose modal, press **`Ctrl+G`** to toggle PGP signing and **`Ctrl+E`** to toggle PGP encryption before sending.
