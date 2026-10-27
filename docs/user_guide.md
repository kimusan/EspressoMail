# EspressoMail User Guide

Welcome to **EspressoMail**! This guide covers setting up your accounts, navigating the 3-pane interface, searching your mail, rendering inline images, configuring Sieve filters, and using OpenPGP security.

---

## 1. First-Time Account Setup (Onboarding Wizard)

When you launch `espressomail` for the first time (or press **`O`** / **File → Add / Configure Account...** at any time), the guided 3-step Onboarding Wizard opens:

1. **Step 1 — Choose Provider Preset**:
   - **Multi-Account Demo Workspace**: Immediately populates two sample accounts (`Schulz Engineering` and `Espresso Open Source`) with threaded conversations, PGP messages, attachments, contacts, and Sieve rules so you can test every feature offline.
   - **Fastmail**: Pre-fills `imap.fastmail.com:993` (SSL/TLS), `smtp.fastmail.com:587` (STARTTLS), and ManageSieve port `4190`.
   - **Gmail / Google Workspace**: Pre-fills `imap.gmail.com:993` and `smtp.gmail.com:587` (use a 16-character Google App Password).
   - **Outlook / Microsoft 365**: Pre-fills `outlook.office365.com:993` and `smtp.office365.com:587`.
   - **ProtonMail Bridge**: Pre-fills `127.0.0.1:1143` (IMAP STARTTLS) and `127.0.0.1:1025` (SMTP STARTTLS).
   - **Apple iCloud Mail** & **Custom Self-Hosted Server**.
2. **Step 2 — Identity & Credentials**:
   - Enter your Account Label, Full Name, Email Address, and Password. Use `Tab` / `Shift+Tab` to move between fields and `Enter` to advance.
3. **Step 3 — Server Endpoints & OpenPGP**:
   - Verify the IMAP/SMTP hostnames and ports.
   - Leave `[x] Auto-generate Ed25519 OpenPGP Key Pair` enabled (or toggle with `Ctrl+G`) to automatically provision a signing/encryption key for the account.

---

## 2. Navigating the 3-Pane Interface

EspressoMail divides your terminal into three responsive panes:

* **Left Pane (`📁 Mailboxes`)**:
  - Top section: **Unified Mailboxes** (`Unified Inbox`, `All Starred`, `All Drafts`, `All Sent`, `All Archive`, `All Trash`) aggregating mail across every configured account.
  - Below: Individual account sections with per-account folders and live unread badges.
  - Press **`U`** at any time to jump straight to the **Unified Inbox**.
* **Center Pane (`✉️ Message List`)**:
  - Displays messages or conversation threads in the active mailbox.
  - Press **`t`** to toggle between **Threaded** mode (showing conversation depth `╰─` and message count `[3]`) and **Flat** chronological mode.
* **Right Pane (`📖 Reading Pane`)**:
  - Displays the formatted email header, PGP verification status banner, attachment chips, conversation history, formatted body text, and inline image previews.
  - Scroll with `j`/`k`, `↑`/`↓`, `PageUp`/`PageDown`, or the **Mouse Wheel**.

---

## 3. Fast Fuzzy Search (`/` or `Tools → Fuzzy Search`)

Press **`/`** to open the live Fuzzy Search modal. Results update on every keystroke and rank matches across subject, sender, recipients, attachment filenames, and message body.

### Supported Search Operators
You can combine free-text fuzzy words with structured qualifiers:
* `from:elena` — Match sender name or email
* `to:maintainers` — Match recipient addresses
* `subject:release` — Match subject line
* `folder:archive` — Restrict to a specific folder
* `has:attachment` — Only messages with file or image attachments
* `is:unread` / `is:read` — Filter by read status
* `is:starred` — Only starred (`★`) messages
* `is:pgp` — Only PGP-signed or PGP-encrypted messages

Example query:
```text
from:elena has:attachment is:starred sixel
```

---

## 4. Terminal Image Rendering (`i`)

When an email contains an image attachment (`.ppm`, `.bmp`, `.png`, `.jpg`, `.sixel`), EspressoMail renders an inline preview at the bottom of the Reading Pane.

Press **`i`** (or select **View → Cycle Image Renderer**) to cycle through the available rendering backends:
1. **`auto`**: Uses `chafa` if installed on `PATH`, falling back to 24-bit Unicode half-blocks (`▀`).
2. **`chafa`**: Calls the external `chafa` CLI tool (`--format=symbols`).
3. **`sixel`**: Hardware DEC Sixel graphics via `img2sixel` or Espresso's built-in VT340 Sixel encoder.
4. **`kitty`**: Native Kitty Graphics Protocol (APC) for Kitty, Ghostty, and WezTerm.
5. **`half_block`**: Universal 24-bit TrueColor upper-half blocks (`▀`).
6. **`braille`**: High-density 2×4 Unicode Braille dot matrix (`⣿`).
7. **`off`**: Disables inline image previews.

---

## 5. Attachments & Drafts

* **Downloading Incoming Attachments**: Press **`w`** while viewing an email with attachments. All attachments are exported to `~/.local/share/espressomail/downloads/` (or your custom download directory) and marked `✔saved`.
* **Attaching Files to Outgoing Mail**: Inside the Compose modal (`c`, `r`, `f`), press **`Ctrl+A`** to open the interactive directory browser (`FilePicker`) and select files to attach.
* **Saving & Resuming Drafts**: Press **`Ctrl+S`** inside the Compose modal to save your work to the `Drafts` folder. Select any message in `Drafts` and press `Enter` to resume editing and send (`F5` or `Ctrl+X`).

---

## 6. Sieve Filters (`F`)

Press **`F`** to open the **ManageSieve & Filter Rules** modal:
* **`Space`**: Enable or disable the highlighted filter rule.
* **`a`**: Create a new filter rule (Name, Header Field, Match Pattern, Target Folder).
* **`d`**: Delete the selected rule.
* **`v`**: Validate the compiled RFC 5228 Sieve script syntax.
* **`r`**: Execute all active Sieve rules immediately against your local Inbox.

---

## 7. GPG / OpenPGP Security (`G`)

* **Viewing & Generating Keys**: Press **`G`** to open the OpenPGP Keyring Manager. Press **`g`** to generate a new Ed25519 key pair for your active account.
* **Verifying & Decrypting Mail**: Press **`v`** inside the PGP Manager to re-verify the active message's signature, or **`x`** to decrypt an encrypted payload.
* **Signing & Encrypting Outgoing Mail**: In the Compose modal, press **`Ctrl+G`** to toggle PGP signing and **`Ctrl+E`** to toggle PGP encryption before sending.
