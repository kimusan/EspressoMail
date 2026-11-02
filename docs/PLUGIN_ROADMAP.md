# EspressoMail Plugin Roadmap & Future Plugin Specifications

This document outlines the architecture, hook integrations, settings schemas, and implementation plans for upcoming plugins in the EspressoMail ecosystem.

---

## 1. Currently Shipped Built-In Plugins

The following plugins are built into EspressoMail (`src/espressomail/plugins/`):

1. **DeepL Neural Translation (`deepl_translate`)**: In-place reading pane translation (`Ctrl+T`), draft translation in `ComposeModal`, language cycling, and 30-day SQLite translation caching.
2. **DMARC / DKIM / SPF Inspector (`dmarc_inspector`)**: Parses `Authentication-Results`, `Received-SPF`, and `DKIM-Signature` headers into authentication badges (`Ctrl+D`), and automatically decompresses and renders `.xml`, `.xml.gz`, `.gz`, and `.zip` DMARC aggregate XML reports into structured terminal tables.
3. **Phishing & Suspicious Link Scanner (`phishing_scanner`)**: Scans incoming emails for `From` vs `Reply-To` domain mismatches, display-name spoofing, deceptive HTML anchor text vs `href` domains, Punycode (`xn--`) homograph domains, and raw IP URLs.
4. **One-Click Unsubscribe (`unsubscribe`)**: Parses `List-Unsubscribe` (RFC 2369) and `List-Unsubscribe-Post` (RFC 8058) headers (`Ctrl+K`), executing background one-click HTTP POST requests or pre-filling a `mailto:` unsubscribe draft.
5. **AI Email Assistant (`ai_assistant`)**: Connects to local **Ollama** (`http://localhost:11434`), **OpenAI-compatible**, or **Anthropic Claude** endpoints to generate reading pane TL;DR + Action Item summaries (`Ctrl+Y`) and polish/proofread drafts in the composer (`Ctrl+A` / `Alt+G` or Command Palette).
6. **LanguageTool Grammar & Spell Checker (`languagetool`)**: Checks draft text in `ComposeModal` (`Ctrl+L`) against the public or self-hosted LanguageTool API (`/v2/check`) and applies or previews grammar and spelling fixes.
7. **URL Launcher & Link Picker (`url_launcher`)**: Extracts and deduplicates all URLs and anchor labels from `body_html` and `body_text` (`Ctrl+O`), toggles a numbered URL reference table in the reading pane, and opens links in the system browser (`xdg-open` / `webbrowser`).

---

## 2. Planned Plugins (Phase 2 Roadmap)

### 2.1 Developer & SysAdmin Workflows

#### A. Git Patch & Diff Viewer (`git_patch`)
Designed for developers working with mailing-list patches (`git format-patch`, Linux kernel/Git mailing lists, GitHub/GitLab diff emails).

- **Plugin ID**: `git_patch`
- **Target Hooks**:
  - `get_reader_actions(message)`: Detects unified diffs (`diff --git`, `--- a/`, `+++ b/`, `@@ -...,+... @@`) in `body_text` or `.patch` / `.diff` attachments and displays a `[🔀 Patch Detected]` action pill.
  - `transform_reader_content(message, rendered_body, state)`: Highlights unified diff blocks with theme-aware ANSI colors (`+` additions in green, `-` deletions in red, `@@` hunk headers in cyan, diff stats summary table).
  - `get_commands()` & `on_action()`:
    - `git_patch_export`: Save patch series to a local directory.
    - `git_patch_apply`: Apply the patch directly to a configured local Git repository via `git am` or `git apply --check` in a background `Cmd`.
- **Settings Schema**:
  - `default_repo_path` (`str`): Default local Git repository path for `git am`.
  - `highlight_diffs` (`bool`, default `true`): Automatically syntax-highlight unified diffs in the reading pane.

---

### 2.2 Productivity & External Integrations

#### B. Task Manager & Note Exporter (`task_note_exporter`)
Allows turning actionable emails into tasks or knowledge-base notes with a single shortcut, complete with `message_id` deep links and sender metadata.

- **Plugin ID**: `task_note_exporter`
- **Target Hooks**:
  - `get_reader_actions(message)`: Displays `[✅ Create Task]` and `[📝 Save to Notes]` pills.
  - `get_menu_items()`: Adds *"Export Email to Task"* and *"Export Email to Markdown Note"* under the `Mail` and `Tools` menus.
  - `on_action()`:
    - **Obsidian / Local Markdown**: Writes a frontmatter-enriched Markdown file (`YYYY-MM-DD-subject.md`) into the user's configured Obsidian vault or notes folder with `From`, `Date`, `Subject`, `Message-ID`, and clean Markdown body.
    - **Taskwarrior (`task add`)**: Executes `task add` locally with project/tag metadata and an annotation referencing the email subject and sender.
    - **Todoist / Linear / GitHub Issues**: Dispatches a background REST API `Cmd` to create a task with due date and email summary.
- **Settings Schema**:
  - `notes_dir` (`str`, default `~/Documents/Notes/Emails`): Target directory or Obsidian vault folder for Markdown exports.
  - `task_backend` (`choice`: `markdown`, `taskwarrior`, `todoist`, `github_issues`).
  - `todoist_api_token` (`secret`): Optional API token for Todoist REST v2.

---

#### C. Quick Templates / Canned Responses (`quick_templates`)
Speeds up repetitive replies (support responses, hiring updates, scheduling confirmations, status reports) with variable interpolation.

- **Plugin ID**: `quick_templates`
- **Target Hooks**:
  - `get_compose_actions(compose_modal)`: Adds `[📋 Insert Template]` action pill to `ComposeModal`.
  - `get_commands()`: Adds commands to insert, save current draft as template, or manage templates from the Command Palette (`Ctrl+P`).
  - **Variable Interpolation**: Automatically expands placeholders using the active account and recipient context:
    - `{{sender_first_name}}` / `{{recipient_name}}`
    - `{{subject}}`
    - `{{date}}` / `{{weekday}}`
    - `{{my_name}}` / `{{my_email}}`
- **Storage**: Stores JSON or Markdown templates inside `context.get_data_dir() / "templates"` so users can version-control or edit them with `$EDITOR`.

---

#### D. Attachment Text Previewer (`attachment_previewer`)
Renders inline terminal previews of common non-image document and archive attachments directly inside the reading pane without leaving EspressoMail.

- **Plugin ID**: `attachment_previewer`
- **Supported Formats**:
  - **Archives (`.zip`, `.tar.gz`, `.tgz`, `.tar.bz2`)**: Lists archive file tree, sizes, and compression ratios using Python's standard `zipfile` and `tarfile` modules.
  - **Tabular Data (`.csv`, `.tsv`, `.json`)**: Renders a formatted ASCII/Unicode preview table of the first N rows and column summaries.
  - **PDF Documents (`.pdf`)**: Extracts text via `pdftotext` (if available on PATH) or `pypdf`.
  - **Word / ODF Documents (`.docx`, `.odt`)**: Extracts paragraph text from `word/document.xml` inside `.docx` zip containers using standard-library `zipfile` + `xml.etree.ElementTree` (zero external dependencies).
- **Target Hooks**:
  - `get_reader_actions(message)`: Shows `[👁 Preview Attachments (N)]` when supported non-image attachments are present.
  - `transform_reader_content(message, rendered_body, state)`: Appends collapsible terminal preview boxes below the email body when toggled.
- **Settings Schema**:
  - `auto_preview_archives` (`bool`, default `true`): Automatically list contents of `.zip` and `.tar.*` attachments.
  - `max_preview_lines` (`int`, default `40`): Maximum lines rendered per attachment preview.
