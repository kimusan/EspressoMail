"""Interactive modal dialogs for Compose, Fuzzy Search, Address Book, Sieve Filters, PGP, and Help."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from espresso import Cmd, KeyMsg, Model, Msg
from espresso.beans import (
    EchoMode,
    FilePicker,
    FileSelectMsg,
    TextArea,
    TextInput,
    format_file_size,
)
from espresso.crema import (
    DOUBLE_BORDER,
    ROUNDED_BORDER,
    Align,
    Style,
    join_vertical,
    truncate_ansi,
)

from espressomail.crypto.pgp import PGPManager
from espressomail.models.entities import (
    AccountConfig,
    Attachment,
    Contact,
    EmailMessage,
    PGPStatus,
    SecurityType,
    SieveFilterRule,
)
from espressomail.protocols.sieve import (
    apply_sieve_rules_locally,
    compile_sieve_script,
    validate_sieve_script,
)
from espressomail.protocols.smtp_client import validate_account_credentials
from espressomail.search.fuzzy import FuzzySearchEngine, SearchResult
from espressomail.storage.database import LocalStore
from espressomail.themes.engine import Theme


# ---------------------------------------------------------------------------
# Modal Messages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModalClosedMsg(Msg):
    """Emitted when a modal dialog is dismissed."""

    status_note: str = ""
    trigger_sync: bool = False


@dataclass(frozen=True)
class ComposeSubmittedMsg(Msg):
    """Emitted when the user sends or saves a draft from ComposeModal."""

    account: AccountConfig
    to_addrs: list[str]
    cc_addrs: list[str]
    subject: str
    body_text: str
    attachments: list[str]
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    pgp_sign: bool = False
    pgp_encrypt: bool = False
    is_draft: bool = False
    draft_id: str = ""


@dataclass(frozen=True)
class SearchSelectMessageMsg(Msg):
    """Emitted when a message is chosen from the Fuzzy Search modal."""

    message: EmailMessage


@dataclass(frozen=True)
class ComposeToContactMsg(Msg):
    """Emitted when the user presses Enter/c on a contact in the Address Book."""

    recipient: str


@dataclass(frozen=True)
class PreferencesSavedMsg(Msg):
    """Emitted when user saves updated settings from PreferencesModal."""

    download_dir: str
    image_backend: str
    theme: str
    threading: bool
    show_raw_headers: bool
    sync_on_startup: bool
    allow_remote_images: bool
    folder_count_mode: str = "unread_only"
    fetch_batch_size: int = 50


# ---------------------------------------------------------------------------
# 1. Compose / Reply / Forward / Draft Modal
# ---------------------------------------------------------------------------


class ComposeModal(Model):
    """Full-featured email composer with multi-account sender, attachments, drafts, and PGP."""

    def __init__(
        self,
        accounts: Sequence[AccountConfig],
        contacts: Sequence[Contact],
        theme: Theme,
        active_account_id: str = "",
        to_initial: str = "",
        cc_initial: str = "",
        subject_initial: str = "",
        body_initial: str = "",
        in_reply_to: str = "",
        references: Sequence[str] = (),
        draft_id: str = "",
    ) -> None:
        self.accounts = list(accounts)
        self.contacts = list(contacts)
        self.theme = theme
        self.account_idx = 0
        for idx, acc in enumerate(self.accounts):
            if acc.id == active_account_id:
                self.account_idx = idx
                break

        self.in_reply_to = in_reply_to
        self.references = list(references)
        self.draft_id = draft_id

        self.input_to = TextInput(placeholder="recipient@example.com", prompt="To:      ")
        self.input_to.value = to_initial
        self.input_cc = TextInput(placeholder="cc@example.com (optional)", prompt="Cc:      ")
        self.input_cc.value = cc_initial
        self.input_subject = TextInput(placeholder="Subject line...", prompt="Subject: ")
        self.input_subject.value = subject_initial

        self.editor = TextArea(
            placeholder="Write your message here...",
            width=68,
            height=10,
            show_line_numbers=False,
        )
        if body_initial:
            self.editor.value = body_initial
        elif self.active_account and self.active_account.signature:
            self.editor.value = f"\n\n{self.active_account.signature}"

        self.attachments: list[str] = []
        self.pgp_sign = bool(self.active_account and self.active_account.pgp_key_id)
        self.pgp_encrypt = False
        self.show_file_picker = False
        self.file_picker = FilePicker(
            directory=".",
            show_hidden=False,
            file_allowed=True,
            dir_allowed=False,
            height=8,
            width=60,
        )
        # Focus indices: 0=From Account, 1=To, 2=Cc, 3=Subject, 4=Body Editor
        self.focus_idx = 4 if (to_initial and subject_initial) else 1
        self._sync_focus()

    @property
    def active_account(self) -> AccountConfig | None:
        if not self.accounts:
            return None
        return self.accounts[self.account_idx % len(self.accounts)]

    def _sync_focus(self) -> None:
        inputs = [self.input_to, self.input_cc, self.input_subject]
        for idx, inp in enumerate(inputs, start=1):
            if self.focus_idx == idx:
                inp.focus()
            else:
                inp.blur()

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[ComposeModal, Cmd | None]:
        if self.show_file_picker:
            if isinstance(msg, KeyMsg) and str(msg.key) == "esc":
                self.show_file_picker = False
                return self, None
            self.file_picker, cmd = self.file_picker.update(msg)
            if cmd is not None:
                res = cmd() if callable(cmd) else None
                if isinstance(res, FileSelectMsg) and not res.is_dir:
                    if str(res.path) not in self.attachments:
                        self.attachments.append(str(res.path))
                    self.show_file_picker = False
                    return self, None
            if isinstance(msg, FileSelectMsg) and not msg.is_dir:
                if str(msg.path) not in self.attachments:
                    self.attachments.append(str(msg.path))
                self.show_file_picker = False
                return self, None
            return self, cmd

        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k == "esc":
                return self, lambda: ModalClosedMsg("Compose cancelled.")
            if k == "ctrl+a":
                self.show_file_picker = True
                return self, None
            if k == "ctrl+g":
                self.pgp_sign = not self.pgp_sign
                return self, None
            if k == "ctrl+e":
                self.pgp_encrypt = not self.pgp_encrypt
                return self, None
            if k == "ctrl+s":
                return self._emit_submit(is_draft=True)
            if k in ("f5", "ctrl+enter", "ctrl+x"):
                return self._emit_submit(is_draft=False)
            if k == "tab" and self.focus_idx < 4:
                self.focus_idx = (self.focus_idx + 1) % 5
                self._sync_focus()
                return self, None
            if k == "shift+tab":
                self.focus_idx = (self.focus_idx - 1) % 5
                self._sync_focus()
                return self, None
            if self.focus_idx == 0 and k in ("left", "right", "h", "l", "space"):
                if self.accounts:
                    step = 1 if k in ("right", "l", "space") else -1
                    self.account_idx = (self.account_idx + step) % len(self.accounts)
                return self, None

        if self.focus_idx == 1:
            self.input_to, cmd = self.input_to.update(msg)
            return self, cmd
        if self.focus_idx == 2:
            self.input_cc, cmd = self.input_cc.update(msg)
            return self, cmd
        if self.focus_idx == 3:
            self.input_subject, cmd = self.input_subject.update(msg)
            return self, cmd
        if self.focus_idx == 4:
            self.editor, cmd = self.editor.update(msg)
            return self, cmd

        return self, None

    def _emit_submit(self, is_draft: bool) -> tuple[ComposeModal, Cmd | None]:
        acc = self.active_account
        if acc is None:
            return self, lambda: ModalClosedMsg("No account configured.")
        to_list = [x.strip() for x in self.input_to.value.split(",") if x.strip()]
        cc_list = [x.strip() for x in self.input_cc.value.split(",") if x.strip()]
        subj = self.input_subject.value.strip() or "(No Subject)"
        body = self.editor.value
        return self, lambda: ComposeSubmittedMsg(
            account=acc,
            to_addrs=to_list,
            cc_addrs=cc_list,
            subject=subj,
            body_text=body,
            attachments=list(self.attachments),
            in_reply_to=self.in_reply_to,
            references=list(self.references),
            pgp_sign=self.pgp_sign,
            pgp_encrypt=self.pgp_encrypt,
            is_draft=is_draft,
            draft_id=self.draft_id,
        )

    def view(self, width: int = 80) -> str:
        modal_w = min(78, max(56, width - 8))
        self.editor.width = max(40, modal_w - 6)

        if self.show_file_picker:
            picker_content = join_vertical(
                Align.LEFT,
                Style()
                .bold(True)
                .foreground(self.theme.accent_secondary)
                .render("📎 Select File to Attach:"),
                "",
                self.file_picker.view(),
                "",
                Style()
                .foreground(self.theme.fg_muted)
                .render("[↑/↓] Navigate   [Enter] Select File / Open Dir   [Backspace] Parent   [Esc] Cancel"),
            )
            return self.theme.modal_style(modal_w, "Attach File").render(picker_content)

        acc = self.active_account
        acc_label = f"{acc.name} <{acc.email}>" if acc else "None"
        from_style = (
            Style().bold(True).foreground(self.theme.accent_secondary)
            if self.focus_idx == 0
            else Style().foreground(self.theme.fg_primary)
        )
        from_row = f"From:    ◀ {from_style.render(acc_label)} ▶"

        sign_badge = (
            Style().bold(True).foreground(self.theme.pgp_verified_fg).render("[x] PGP Sign (Ctrl+G)")
            if self.pgp_sign
            else Style().foreground(self.theme.fg_muted).render("[ ] PGP Sign (Ctrl+G)")
        )
        enc_badge = (
            Style().bold(True).foreground(self.theme.pgp_warning_fg).render("[x] PGP Encrypt (Ctrl+E)")
            if self.pgp_encrypt
            else Style().foreground(self.theme.fg_muted).render("[ ] PGP Encrypt (Ctrl+E)")
        )

        att_names: list[str] = []
        for p_str in self.attachments:
            p = Path(p_str)
            sz = format_file_size(p.stat().st_size) if p.exists() else "0 B"
            att_names.append(f"📎 {p.name} ({sz})")
        att_line = (
            Style().foreground(self.theme.attachment_fg).render("  ".join(att_names))
            if att_names
            else Style().foreground(self.theme.fg_muted).render("(No attachments — Ctrl+A to attach file)")
        )

        body_border_fg = (
            self.theme.border_focused if self.focus_idx == 4 else self.theme.border_unfocused
        )
        editor_box = (
            Style()
            .border(ROUNDED_BORDER)
            .border_foreground(body_border_fg)
            .border_title(" Message Body ", align=Align.LEFT)
            .width(modal_w - 6)
            .render(self.editor.view())
        )

        footer = (
            Style()
            .foreground(self.theme.fg_muted)
            .render(
                "[F5/Ctrl+X] Send   [Ctrl+S] Save Draft   [Ctrl+A] Attach   [Tab/Shift+Tab] Focus   [Esc] Close"
            )
        )

        content = join_vertical(
            Align.LEFT,
            from_row,
            self.input_to.view(),
            self.input_cc.view(),
            self.input_subject.view(),
            f"{sign_badge}    {enc_badge}",
            f"Files:   {att_line}",
            editor_box,
            footer,
        )
        return self.theme.modal_style(modal_w, "✉ Compose Email").render(content)


# ---------------------------------------------------------------------------
# 2. Fast Fuzzy Search Modal
# ---------------------------------------------------------------------------


class SearchModal(Model):
    """Interactive live fuzzy search modal across all local SQLite messages."""

    def __init__(self, store: LocalStore, theme: Theme, initial_query: str = "") -> None:
        self.store = store
        self.engine = FuzzySearchEngine(store)
        self.theme = theme
        self.query_input = TextInput(
            placeholder="Type fuzzy query or operators (from:elena has:attachment is:unread)...",
            prompt="🔍 Search: ",
        )
        self.query_input.value = initial_query
        self.query_input.focus()
        self.results: list[SearchResult] = []
        self.cursor = 0
        self._refresh_results()

    def _refresh_results(self) -> None:
        self.results = self.engine.search(self.query_input.value, limit=25)
        self.cursor = min(self.cursor, max(0, len(self.results) - 1))

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[SearchModal, Cmd | None]:
        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k == "esc":
                return self, lambda: ModalClosedMsg()
            if k == "down":
                if self.results:
                    self.cursor = (self.cursor + 1) % len(self.results)
                return self, None
            if k == "up":
                if self.results:
                    self.cursor = (self.cursor - 1) % len(self.results)
                return self, None
            if k == "enter":
                if self.results and 0 <= self.cursor < len(self.results):
                    chosen = self.results[self.cursor].message
                    return self, lambda: SearchSelectMessageMsg(chosen)
                return self, None

        prev_val = self.query_input.value
        self.query_input, cmd = self.query_input.update(msg)
        if self.query_input.value != prev_val:
            self._refresh_results()
        return self, cmd

    def view(self, width: int = 80) -> str:
        modal_w = min(82, max(58, width - 6))
        inner_w = modal_w - 6

        tips = (
            Style()
            .foreground(self.theme.fg_muted)
            .render("Operators: from:<name>  to:<addr>  subject:<word>  has:attachment  is:unread  is:starred  is:pgp")
        )

        rows: list[str] = []
        visible_slice = self.results[:10]
        if not visible_slice:
            rows.append(
                Style()
                .italic(True)
                .foreground(self.theme.fg_muted)
                .render("  No matching emails found in local SQLite database.")
            )
        else:
            for idx, hit in enumerate(visible_slice):
                m = hit.message
                marker = "▸ " if idx == self.cursor else "  "
                score_badge = f"[{int(hit.score)}%]"
                fields_str = ",".join(hit.matched_fields[:2])
                line = f"{marker}{m.short_date:<6} │ {m.sender_short[:16]:<16} │ {m.subject[:32]:<32} {score_badge} ({fields_str})"
                truncated = truncate_ansi(line, inner_w)
                if idx == self.cursor:
                    rows.append(
                        Style()
                        .bold(True)
                        .foreground(self.theme.bg_primary)
                        .background(self.theme.accent_primary)
                        .width(inner_w)
                        .render(truncated)
                    )
                else:
                    rows.append(Style().foreground(self.theme.fg_primary).render(truncated))

        footer = (
            Style()
            .foreground(self.theme.fg_muted)
            .render(f"{len(self.results)} matches   [↑/↓] Navigate   [Enter] Open Email   [Esc] Close")
        )

        content = join_vertical(
            Align.LEFT,
            self.query_input.view(),
            tips,
            "",
            "\n".join(rows),
            "",
            footer,
        )
        return self.theme.modal_style(modal_w, "🔍 Instant Fuzzy Database Search").render(content)


# ---------------------------------------------------------------------------
# 3. Local Address Book Modal
# ---------------------------------------------------------------------------


class AddressBookModal(Model):
    """Local address book manager for viewing, adding, deleting, and emailing contacts."""

    def __init__(self, store: LocalStore, theme: Theme) -> None:
        self.store = store
        self.theme = theme
        self.contacts: list[Contact] = self.store.list_contacts()
        self.cursor = 0
        self.adding_mode = False
        self.input_name = TextInput(placeholder="Ada Lovelace", prompt="Name:  ")
        self.input_email = TextInput(placeholder="ada@analytical.org", prompt="Email: ")
        self.input_org = TextInput(placeholder="Organization", prompt="Org:   ")
        self.add_focus = 0

    def _refresh(self) -> None:
        self.contacts = self.store.list_contacts()
        self.cursor = min(self.cursor, max(0, len(self.contacts) - 1))

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[AddressBookModal, Cmd | None]:
        if self.adding_mode:
            fields = [self.input_name, self.input_email, self.input_org]
            if isinstance(msg, KeyMsg):
                k = str(msg.key)
                if k == "esc":
                    self.adding_mode = False
                    return self, None
                if k in ("tab", "down"):
                    self.add_focus = (self.add_focus + 1) % 3
                    for i, f in enumerate(fields):
                        f.focus() if i == self.add_focus else f.blur()
                    return self, None
                if k == "enter":
                    if self.input_email.value.strip() and "@" in self.input_email.value:
                        c = Contact(
                            id=f"contact:{uuid.uuid4().hex[:8]}",
                            name=self.input_name.value.strip() or self.input_email.value.strip(),
                            email=self.input_email.value.strip(),
                            organization=self.input_org.value.strip(),
                            favorite=True,
                            frequency=1,
                        )
                        self.store.save_contact(c)
                        self.adding_mode = False
                        self._refresh()
                    return self, None

            fields[self.add_focus], cmd = fields[self.add_focus].update(msg)
            return self, cmd

        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k in ("esc", "q"):
                return self, lambda: ModalClosedMsg()
            if k in ("down", "j") and self.contacts:
                self.cursor = (self.cursor + 1) % len(self.contacts)
                return self, None
            if k in ("up", "k") and self.contacts:
                self.cursor = (self.cursor - 1) % len(self.contacts)
                return self, None
            if k == "a":
                self.adding_mode = True
                self.add_focus = 0
                self.input_name.value = ""
                self.input_email.value = ""
                self.input_org.value = ""
                self.input_name.focus()
                self.input_email.blur()
                self.input_org.blur()
                return self, None
            if k == "d" and self.contacts:
                target = self.contacts[self.cursor]
                self.store.delete_contact(target.id)
                self._refresh()
                return self, None
            if k in ("enter", "c") and self.contacts:
                target = self.contacts[self.cursor]
                return self, lambda: ComposeToContactMsg(target.formatted)

        return self, None

    def view(self, width: int = 80) -> str:
        modal_w = min(78, max(54, width - 8))
        inner_w = modal_w - 6

        if self.adding_mode:
            form_view = join_vertical(
                Align.LEFT,
                Style().bold(True).foreground(self.theme.accent_secondary).render("Add New Contact:"),
                "",
                self.input_name.view(),
                self.input_email.view(),
                self.input_org.view(),
                "",
                Style().foreground(self.theme.fg_muted).render("[Tab] Next Field   [Enter] Save Contact   [Esc] Cancel"),
            )
            return self.theme.modal_style(modal_w, "📇 Local Address Book").render(form_view)

        rows: list[str] = []
        for idx, c in enumerate(self.contacts[:12]):
            fav = "★ " if c.favorite else "  "
            pgp_badge = "🔑PGP" if c.pgp_fingerprint else "     "
            line = f" {fav}{c.name[:20]:<20} │ {c.email[:26]:<26} │ {c.organization[:14]:<14} {pgp_badge}"
            truncated = truncate_ansi(line, inner_w)
            if idx == self.cursor:
                rows.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.bg_primary)
                    .background(self.theme.accent_primary)
                    .width(inner_w)
                    .render(truncated)
                )
            else:
                rows.append(Style().foreground(self.theme.fg_primary).render(truncated))

        if not rows:
            rows.append(Style().italic(True).foreground(self.theme.fg_muted).render("  Address book is empty."))

        footer = (
            Style()
            .foreground(self.theme.fg_muted)
            .render("[Enter/c] Compose to Contact   [a] Add Contact   [d] Delete   [Esc] Close")
        )
        content = join_vertical(Align.LEFT, "\n".join(rows), "", footer)
        return self.theme.modal_style(modal_w, "📇 Local Address Book").render(content)


# ---------------------------------------------------------------------------
# 4. Sieve Filters Manager Modal
# ---------------------------------------------------------------------------


class SieveModal(Model):
    """Interactive Sieve filter configurator, RFC 5228 compiler, and local rule runner."""

    def __init__(self, store: LocalStore, theme: Theme, active_account_id: str = "acc_work") -> None:
        self.store = store
        self.theme = theme
        self.active_account_id = active_account_id if active_account_id != "*" else "acc_work"
        self.rules: list[SieveFilterRule] = self.store.list_sieve_rules()
        self.cursor = 0
        self.adding_rule = False
        self.status_banner = "RFC 5228 Sieve Script valid and ready."
        self.input_name = TextInput(placeholder="Filter Name", prompt="Rule Name:   ")
        self.input_field = TextInput(placeholder="From / Subject / X-Spam-Flag", prompt="Header/Field:")
        self.input_pattern = TextInput(placeholder="Match substring", prompt="Pattern:     ")
        self.input_folder = TextInput(placeholder="Archive / Trash / INBOX", prompt="Target Folder:")
        self.add_focus = 0

    def _refresh(self) -> None:
        self.rules = self.store.list_sieve_rules()
        self.cursor = min(self.cursor, max(0, len(self.rules) - 1))

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[SieveModal, Cmd | None]:
        if self.adding_rule:
            fields = [self.input_name, self.input_field, self.input_pattern, self.input_folder]
            if isinstance(msg, KeyMsg):
                k = str(msg.key)
                if k == "esc":
                    self.adding_rule = False
                    return self, None
                if k in ("tab", "down"):
                    self.add_focus = (self.add_focus + 1) % len(fields)
                    for i, f in enumerate(fields):
                        f.focus() if i == self.add_focus else f.blur()
                    return self, None
                if k == "enter":
                    pat = self.input_pattern.value.strip()
                    if pat:
                        new_rule = SieveFilterRule(
                            id=f"rule_{uuid.uuid4().hex[:8]}",
                            account_id=self.active_account_id,
                            name=self.input_name.value.strip() or f"Filter {pat}",
                            enabled=True,
                            field=self.input_field.value.strip() or "From",
                            comparator="contains",
                            pattern=pat,
                            action="fileinto",
                            action_arg=self.input_folder.value.strip() or "Archive",
                            stop_processing=True,
                        )
                        self.store.save_sieve_rule(new_rule)
                        self.adding_rule = False
                        self._refresh()
                        self.status_banner = f"Added Sieve rule '{new_rule.name}'."
                    return self, None

            fields[self.add_focus], cmd = fields[self.add_focus].update(msg)
            return self, cmd

        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k in ("esc", "q"):
                return self, lambda: ModalClosedMsg(self.status_banner)
            if k in ("down", "j") and self.rules:
                self.cursor = (self.cursor + 1) % len(self.rules)
                return self, None
            if k in ("up", "k") and self.rules:
                self.cursor = (self.cursor - 1) % len(self.rules)
                return self, None
            if k in (" ", "space") and self.rules:
                r = self.rules[self.cursor]
                r.enabled = not r.enabled
                self.store.save_sieve_rule(r)
                self._refresh()
                return self, None
            if k == "d" and self.rules:
                r = self.rules[self.cursor]
                self.store.delete_sieve_rule(r.id)
                self._refresh()
                self.status_banner = f"Deleted rule '{r.name}'."
                return self, None
            if k == "a":
                self.adding_rule = True
                self.add_focus = 0
                self.input_name.value = ""
                self.input_field.value = "From"
                self.input_pattern.value = ""
                self.input_folder.value = "Archive"
                self.input_name.focus()
                self.input_field.blur()
                self.input_pattern.blur()
                self.input_folder.blur()
                return self, None
            if k == "v":
                compiled = compile_sieve_script(self.rules)
                ok, errs = validate_sieve_script(compiled)
                self.status_banner = (
                    "✔ Sieve RFC 5228 syntax verified with 0 errors."
                    if ok
                    else f"✖ Validation error: {'; '.join(errs)}"
                )
                return self, None
            if k == "r":
                # Run active Sieve rules against local INBOX messages
                inbox_msgs = self.store.list_messages("*", "INBOX")
                matched = apply_sieve_rules_locally(self.rules, inbox_msgs)
                for m, rule in matched:
                    if rule.action == "fileinto" and rule.action_arg:
                        self.store.move_to_folder(m.id, rule.action_arg)
                    elif rule.action == "flag":
                        if not m.is_starred:
                            self.store.toggle_star(m.id)
                self.status_banner = f"Executed Sieve rules across Inbox: {len(matched)} message(s) processed."
                return self, None

        return self, None

    def view(self, width: int = 80) -> str:
        modal_w = min(82, max(58, width - 6))
        inner_w = modal_w - 6

        if self.adding_rule:
            form_view = join_vertical(
                Align.LEFT,
                Style().bold(True).foreground(self.theme.accent_secondary).render("Create New Sieve Filter Rule:"),
                "",
                self.input_name.view(),
                self.input_field.view(),
                self.input_pattern.view(),
                self.input_folder.view(),
                "",
                Style().foreground(self.theme.fg_muted).render("[Tab] Next Field   [Enter] Save Rule   [Esc] Cancel"),
            )
            return self.theme.modal_style(modal_w, "⚙️ ManageSieve & Filter Rules").render(form_view)

        rule_rows: list[str] = []
        for idx, r in enumerate(self.rules[:7]):
            chk = "[x]" if r.enabled else "[ ]"
            desc = f"{chk} {r.name:<24} │ if {r.field} {r.comparator} \"{r.pattern}\" → {r.action} \"{r.action_arg}\""
            truncated = truncate_ansi(desc, inner_w)
            if idx == self.cursor:
                rule_rows.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.bg_primary)
                    .background(self.theme.accent_primary)
                    .width(inner_w)
                    .render(truncated)
                )
            else:
                rule_rows.append(Style().foreground(self.theme.fg_primary).render(truncated))

        compiled_preview = "\n".join(compile_sieve_script(self.rules).splitlines()[:10])
        preview_box = (
            Style()
            .border(ROUNDED_BORDER)
            .border_foreground(self.theme.border_unfocused)
            .border_title(" Compiled RFC 5228 Sieve Script Preview ", align=Align.LEFT)
            .width(inner_w)
            .foreground(self.theme.fg_secondary)
            .render(compiled_preview)
        )

        status_line = Style().bold(True).foreground(self.theme.pgp_verified_fg).render(self.status_banner)
        footer = (
            Style()
            .foreground(self.theme.fg_muted)
            .render("[Space] Toggle   [a] Add Rule   [d] Delete   [v] Validate   [r] Run Filters Now   [Esc] Close")
        )

        content = join_vertical(
            Align.LEFT,
            "\n".join(rule_rows),
            "",
            preview_box,
            status_line,
            footer,
        )
        return self.theme.modal_style(modal_w, "⚙️ ManageSieve & Filter Rules").render(content)


# ---------------------------------------------------------------------------
# 5. GPG / PGP Keyring & Security Modal
# ---------------------------------------------------------------------------


class PGPModal(Model):
    """Interactive OpenPGP keyring inspector, key generator, and message verifier/decryptor."""

    def __init__(
        self,
        pgp: PGPManager,
        store: LocalStore,
        theme: Theme,
        active_message: EmailMessage | None = None,
    ) -> None:
        self.pgp = pgp
        self.store = store
        self.theme = theme
        self.active_message = active_message
        self.keys = self.pgp.list_keys()
        self.cursor = 0
        self.status_note = "OpenPGP keyring loaded."

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[PGPModal, Cmd | None]:
        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k in ("esc", "q"):
                return self, lambda: ModalClosedMsg(self.status_note)
            if k in ("down", "j") and self.keys:
                self.cursor = (self.cursor + 1) % len(self.keys)
                return self, None
            if k in ("up", "k") and self.keys:
                self.cursor = (self.cursor - 1) % len(self.keys)
                return self, None
            if k == "g":
                accs = self.store.list_accounts()
                acc = accs[0] if accs else None
                name = acc.full_name if acc else "Kim Schulz"
                email_addr = acc.email if acc else "kim@schulz.dk"
                new_key = self.pgp.generate_key(name, email_addr)
                self.keys = self.pgp.list_keys()
                self.status_note = f"✔ Generated Ed25519 key {new_key.key_id} for {email_addr}"
                return self, None
            if k == "v" and self.active_message:
                if self.active_message.pgp_raw_payload:
                    res = self.pgp.verify_message(self.active_message.pgp_raw_payload)
                    self.active_message.pgp_status = res.status
                    self.store.save_message(self.active_message)
                    self.status_note = res.summary
                elif self.active_message.pgp_status in (PGPStatus.SIGNED_VERIFIED, PGPStatus.DECRYPTED_VERIFIED):
                    self.status_note = f"✔ Verified signature from {self.active_message.pgp_signer or self.active_message.sender_email}"
                else:
                    self.status_note = "Active message does not contain an OpenPGP signature."
                return self, None
            if k == "x" and self.active_message:
                if self.active_message.pgp_raw_payload and "BEGIN PGP MESSAGE" in self.active_message.pgp_raw_payload:
                    res = self.pgp.decrypt_message(self.active_message.pgp_raw_payload)
                    if res.ok:
                        self.active_message.body_text = res.payload
                        self.active_message.pgp_status = res.status
                        self.store.save_message(self.active_message)
                    self.status_note = res.summary
                else:
                    self.status_note = "✔ Message payload is already decrypted in reading pane."
                return self, None

        return self, None

    def view(self, width: int = 80) -> str:
        modal_w = min(82, max(58, width - 6))
        inner_w = modal_w - 6

        rows: list[str] = []
        for idx, k in enumerate(self.keys[:8]):
            kind = "🔐 SEC/PUB" if k.has_secret else "🔑 PUB    "
            line = f" {kind} │ {k.key_id:<18} │ {k.uid[:28]:<28} │ {k.algorithm}"
            truncated = truncate_ansi(line, inner_w)
            if idx == self.cursor:
                rows.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.bg_primary)
                    .background(self.theme.accent_primary)
                    .width(inner_w)
                    .render(truncated)
                )
            else:
                rows.append(Style().foreground(self.theme.fg_primary).render(truncated))

        selected_fp = self.keys[self.cursor].fingerprint if self.keys else "None"
        fp_line = Style().foreground(self.theme.accent_secondary).render(f"Fingerprint: {selected_fp}")
        status_line = Style().bold(True).foreground(self.theme.pgp_verified_fg).render(self.status_note)
        footer = (
            Style()
            .foreground(self.theme.fg_muted)
            .render("[g] Generate Key   [v] Verify Active Email   [x] Decrypt Active Email   [Esc] Close")
        )

        content = join_vertical(
            Align.LEFT,
            "\n".join(rows),
            "",
            fp_line,
            status_line,
            footer,
        )
        return self.theme.modal_style(modal_w, "🔐 OpenPGP / GnuPG Keyring Manager").render(content)


# ---------------------------------------------------------------------------
# 6. Account Settings Modal (Edit Existing Accounts)
# ---------------------------------------------------------------------------

_MODAL_SECURITY_CYCLE: tuple[SecurityType, ...] = (
    SecurityType.SSL_TLS,
    SecurityType.STARTTLS,
    SecurityType.NONE,
)


class AccountSettingsModal(Model):
    """Modal for editing existing account settings (password, servers, ports, TLS, username) or deleting an account."""

    def __init__(
        self,
        store: LocalStore,
        theme: Theme,
        active_account_id: str = "",
        credential_validator: Callable[[AccountConfig], tuple[bool, str]] | None = None,
    ) -> None:
        self.store = store
        self.theme = theme
        self.credential_validator: Callable[[AccountConfig], tuple[bool, str]] = (
            credential_validator
            or (lambda a: validate_account_credentials(a, store=self.store))
        )
        self.accounts: list[AccountConfig] = self.store.list_accounts()
        self.account_idx = 0
        for idx, acc in enumerate(self.accounts):
            if acc.id == active_account_id:
                self.account_idx = idx
                break

        self.show_password = False
        self.error_msg = ""
        self.status_msg = ""

        # Form inputs
        self.input_account_name = TextInput(placeholder="Work / Personal", prompt="Account Name:  ")
        self.input_full_name = TextInput(placeholder="Jane Doe", prompt="Full Name:     ")
        self.input_email = TextInput(placeholder="jane@example.com", prompt="Email Address: ")
        self.input_username = TextInput(placeholder="Login username", prompt="Login User:    ")
        self.input_password = TextInput(
            placeholder="IMAP / SMTP Password",
            prompt="Password:      ",
            echo_mode=EchoMode.PASSWORD,
        )
        self.input_imap_host = TextInput(placeholder="imap.example.com", prompt="IMAP Host:     ")
        self.input_imap_port = TextInput(placeholder="993", prompt="IMAP Port:     ")
        self.imap_security: SecurityType = SecurityType.SSL_TLS
        self.input_smtp_host = TextInput(placeholder="smtp.example.com", prompt="SMTP Host:     ")
        self.input_smtp_port = TextInput(placeholder="587", prompt="SMTP Port:     ")
        self.smtp_security: SecurityType = SecurityType.STARTTLS
        self.input_signature = TextArea(
            placeholder="Optional email signature...",
            height=3,
            width=54,
            show_line_numbers=False,
        )

        # Focus indices:
        # 0: Account selector
        # 1: Account Name, 2: Full Name, 3: Email, 4: Username, 5: Password
        # 6: IMAP Host, 7: IMAP Port, 8: IMAP Security
        # 9: SMTP Host, 10: SMTP Port, 11: SMTP Security
        # 12: Signature
        self.focus_idx = 5 if self.accounts else 0  # Default focus on Password field for quick password updates
        self._load_selected_account()
        self._sync_focus()

    @property
    def current_account(self) -> AccountConfig | None:
        if not self.accounts:
            return None
        return self.accounts[self.account_idx % len(self.accounts)]

    def _load_selected_account(self) -> None:
        acc = self.current_account
        if acc is None:
            return
        self.input_account_name.value = acc.name
        self.input_full_name.value = acc.full_name
        self.input_email.value = acc.email
        self.input_username.value = acc.imap_username or acc.email
        self.input_password.value = acc.imap_password
        self.input_imap_host.value = acc.imap_host
        self.input_imap_port.value = str(acc.imap_port)
        self.imap_security = acc.imap_security
        self.input_smtp_host.value = acc.smtp_host
        self.input_smtp_port.value = str(acc.smtp_port)
        self.smtp_security = acc.smtp_security
        self.input_signature.value = acc.signature

    def _sync_focus(self) -> None:
        input_map = {
            1: self.input_account_name,
            2: self.input_full_name,
            3: self.input_email,
            4: self.input_username,
            5: self.input_password,
            6: self.input_imap_host,
            7: self.input_imap_port,
            9: self.input_smtp_host,
            10: self.input_smtp_port,
            12: self.input_signature,
        }
        for idx, inp in input_map.items():
            if idx == self.focus_idx:
                inp.focus()
            else:
                inp.blur()

    def init(self) -> Cmd | None:
        return None

    def _build_updated_account(self) -> tuple[AccountConfig | None, str]:
        acc = self.current_account
        if acc is None:
            return None, "No account selected."
        email_val = self.input_email.value.strip()
        if not email_val or "@" not in email_val:
            return None, "Email address must contain '@'."
        imap_host = self.input_imap_host.value.strip()
        smtp_host = self.input_smtp_host.value.strip()
        if not imap_host or not smtp_host:
            return None, "Both IMAP Host and SMTP Host are required."
        try:
            imap_port = int(self.input_imap_port.value.strip() or "993")
            smtp_port = int(self.input_smtp_port.value.strip() or "587")
            if not (1 <= imap_port <= 65535 and 1 <= smtp_port <= 65535):
                raise ValueError
        except ValueError:
            return None, "IMAP and SMTP ports must be valid port numbers (1–65535)."

        username = self.input_username.value.strip() or email_val
        password = self.input_password.value
        if not password:
            return None, "Password cannot be empty."

        updated = AccountConfig(
            id=acc.id,
            name=self.input_account_name.value.strip() or email_val,
            email=email_val,
            full_name=self.input_full_name.value.strip() or email_val,
            imap_host=imap_host,
            imap_port=imap_port,
            imap_security=self.imap_security,
            imap_username=username,
            imap_password=password,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_security=self.smtp_security,
            smtp_username=username,
            smtp_password=password,
            sieve_host=imap_host,
            sieve_port=acc.sieve_port,
            pgp_key_id=acc.pgp_key_id,
            signature=self.input_signature.value,
            color_badge=acc.color_badge,
            enabled=acc.enabled,
        )
        return updated, ""

    def update(self, msg: Msg) -> tuple[AccountSettingsModal, Cmd | None]:
        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k == "esc":
                return self, lambda: ModalClosedMsg(self.status_msg or "Closed account settings.")
            if not self.accounts:
                return self, lambda: ModalClosedMsg("No accounts configured.")

            if k == "ctrl+r":
                self.show_password = not self.show_password
                self.input_password.echo_mode = (
                    EchoMode.NORMAL if self.show_password else EchoMode.PASSWORD
                )
                return self, None

            if k == "ctrl+d":
                acc = self.current_account
                if acc is not None:
                    self.store.delete_account(acc.id)
                    self.accounts = self.store.list_accounts()
                    if not self.accounts:
                        return self, lambda: ModalClosedMsg(f"Deleted account {acc.email}.")
                    self.account_idx = self.account_idx % len(self.accounts)
                    self._load_selected_account()
                    self.status_msg = f"Deleted account {acc.email}."
                    self.error_msg = ""
                return self, None

            if k == "ctrl+t":
                updated, err = self._build_updated_account()
                if updated is None:
                    self.error_msg = err
                    self.status_msg = ""
                    return self, None
                ok, summary = self.credential_validator(updated)
                if ok:
                    self.status_msg = summary
                    self.error_msg = ""
                else:
                    self.error_msg = summary
                    self.status_msg = ""
                return self, None

            if k == "ctrl+f":
                updated, err = self._build_updated_account()
                if updated is None:
                    self.error_msg = err
                    self.status_msg = ""
                    return self, None
                self.store.save_account(updated)
                self.accounts = self.store.list_accounts()
                return self, lambda: ModalClosedMsg(
                    f"✔ Updated settings for {updated.email}. Synchronizing...",
                    trigger_sync=True,
                )

            if k in ("ctrl+s", "ctrl+enter") or (k == "enter" and self.focus_idx != 12):
                updated, err = self._build_updated_account()
                if updated is None:
                    self.error_msg = err
                    self.status_msg = ""
                    return self, None
                ok, summary = self.credential_validator(updated)
                if not ok:
                    self.error_msg = f"{summary} (Press Ctrl+F to save without live server check)"
                    self.status_msg = ""
                    return self, None
                self.store.save_account(updated)
                self.accounts = self.store.list_accounts()
                return self, lambda: ModalClosedMsg(
                    f"✔ Verified and saved settings for {updated.email}. Synchronizing...",
                    trigger_sync=True,
                )

            if k in ("tab", "down"):
                self.focus_idx = (self.focus_idx + 1) % 13
                self._sync_focus()
                return self, None
            if k in ("shift+tab", "up"):
                self.focus_idx = (self.focus_idx - 1) % 13
                self._sync_focus()
                return self, None

            if self.focus_idx == 0 and k in ("left", "right", "h", "l", " ", "space"):
                delta = 1 if k in ("right", "l", " ", "space") else -1
                self.account_idx = (self.account_idx + delta) % len(self.accounts)
                self._load_selected_account()
                self.error_msg = ""
                self.status_msg = ""
                return self, None

            if self.focus_idx in (8, 11) and k in ("left", "right", "h", "l", " ", "space"):
                delta = 1 if k in ("right", "l", " ", "space") else -1
                if self.focus_idx == 8:
                    cur = _MODAL_SECURITY_CYCLE.index(self.imap_security)
                    self.imap_security = _MODAL_SECURITY_CYCLE[(cur + delta) % len(_MODAL_SECURITY_CYCLE)]
                else:
                    cur = _MODAL_SECURITY_CYCLE.index(self.smtp_security)
                    self.smtp_security = _MODAL_SECURITY_CYCLE[(cur + delta) % len(_MODAL_SECURITY_CYCLE)]
                return self, None

        input_map = {
            1: self.input_account_name,
            2: self.input_full_name,
            3: self.input_email,
            4: self.input_username,
            5: self.input_password,
            6: self.input_imap_host,
            7: self.input_imap_port,
            9: self.input_smtp_host,
            10: self.input_smtp_port,
            12: self.input_signature,
        }
        if self.focus_idx in input_map:
            target = input_map[self.focus_idx]
            _, cmd = target.update(msg)
            return self, cmd

        return self, None

    def view(self, width: int = 80) -> str:
        modal_w = min(84, max(58, width - 6))
        acc = self.current_account
        if acc is None:
            return self.theme.modal_style(modal_w, "⚙️ Account Settings").render(
                "No accounts configured. Press Esc to close or O to add an account."
            )

        sel_style = (
            Style().bold(True).foreground(self.theme.accent_primary)
            if self.focus_idx == 0
            else Style().bold(True).foreground(self.theme.accent_secondary)
        )
        acc_selector = (
            f"Account ({self.account_idx + 1}/{len(self.accounts)}): "
            f"◀ {sel_style.render(f'{acc.name} <{acc.email}>')} ▶"
        )

        imap_sec_style = (
            Style().bold(True).foreground(self.theme.accent_primary)
            if self.focus_idx == 8
            else Style().foreground(self.theme.fg_primary)
        )
        smtp_sec_style = (
            Style().bold(True).foreground(self.theme.accent_primary)
            if self.focus_idx == 11
            else Style().foreground(self.theme.fg_primary)
        )
        pwd_mode = "Plaintext (Ctrl+R to mask)" if self.show_password else "Masked (Ctrl+R to reveal)"

        rows = [
            acc_selector,
            "",
            self.input_account_name.view(),
            self.input_full_name.view(),
            self.input_email.view(),
            self.input_username.view(),
            f"{self.input_password.view()}   {Style().foreground(self.theme.fg_muted).render(f'[{pwd_mode}]')}",
            self.input_imap_host.view(),
            self.input_imap_port.view(),
            f"IMAP Security: ◀ {imap_sec_style.render(self.imap_security.value.upper())} ▶",
            self.input_smtp_host.view(),
            self.input_smtp_port.view(),
            f"SMTP Security: ◀ {smtp_sec_style.render(self.smtp_security.value.upper())} ▶",
            "",
            Style().bold(True).foreground(
                self.theme.accent_primary if self.focus_idx == 12 else self.theme.accent_secondary
            ).render("Email Signature (appended to messages):"),
            self.input_signature.view(),
        ]

        if self.error_msg:
            rows.append(Style().bold(True).foreground(self.theme.pgp_error_fg).render(f"⚠ {self.error_msg}"))
        if self.status_msg:
            rows.append(Style().bold(True).foreground(self.theme.pgp_verified_fg).render(f"✔ {self.status_msg}"))

        rows.append("")
        rows.append(
            Style()
            .foreground(self.theme.fg_muted)
            .render(
                "[Enter/Ctrl+S] Validate & Save  [Ctrl+T] Test Auth  [Ctrl+F] Save Offline  [Ctrl+D] Delete  [Esc] Close"
            )
        )

        return self.theme.modal_style(modal_w, "⚙️ Account Settings & Credentials").render(
            join_vertical(Align.LEFT, *rows)
        )


# ---------------------------------------------------------------------------
# 7. Keyboard & Mouse Shortcuts Help Modal
# ---------------------------------------------------------------------------


class HelpModal(Model):
    """Comprehensive keyboard & mouse shortcut reference card."""

    def __init__(self, theme: Theme) -> None:
        self.theme = theme

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[HelpModal, Cmd | None]:
        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k in ("esc", "q", "?", "enter", " "):
                return self, lambda: ModalClosedMsg()
        return self, None

    def view(self, width: int = 80) -> str:
        modal_w = min(78, max(54, width - 8))
        h_style = Style().bold(True).foreground(self.theme.accent_secondary)
        k_style = Style().bold(True).foreground(self.theme.accent_primary)

        lines = [
            h_style.render("Navigation & Layout"),
            f"  {k_style.render('Tab / Shift+Tab')}   Cycle focus across Folders, Message List, and Reading Pane",
            f"  {k_style.render('j / k / ↑ / ↓')}     Navigate folders, threads, or scroll reading pane",
            f"  {k_style.render('Enter / Space')}     Select folder, open message, or expand/collapse thread",
            f"  {k_style.render('[ / ]')}             Resize splitter (widen reading pane / message list; or drag with mouse)",
            f"  {k_style.render('U')}                 Jump directly to Unified Inbox (all accounts)",
            f"  {k_style.render('F10 / m')}           Open interactive top drop-down Menu Bar",
            "",
            h_style.render("Mail Actions"),
            f"  {k_style.render('c')}  Compose new mail      {k_style.render('r / R')}  Reply / Reply All",
            f"  {k_style.render('f')}  Forward message       {k_style.render('s')}      Toggle Star / Flag",
            f"  {k_style.render('w')}  Save attachments      {k_style.render('M')}      Fetch older messages",
            f"  {k_style.render('S')}  Sync all accounts     {k_style.render('X')}      Empty Trash",
            "",
            h_style.render("Accounts, Search, Security, Filters & Themes"),
            f"  {k_style.render('A')}      Edit Account Settings (Password, IMAP/SMTP hosts, ports, TLS)",
            f"  {k_style.render('O')}      Add New Account (Onboarding Wizard)",
            f"  {k_style.render('p')}      General Preferences (Downloads folder, image renderer, theme)",
            f"  {k_style.render('/')}      Instant Fuzzy Search across local SQLite store",
            f"  {k_style.render('t')}      Toggle Conversation Threading view",
            f"  {k_style.render('T')}      Cycle Color Scheme / Theme ({self.theme.name})",
            f"  {k_style.render('i')}      Cycle Terminal Image Renderer (Chafa / Sixel / Kitty / Half-Block)",
            f"  {k_style.render('B')}      Local Address Book    {k_style.render('F')}  ManageSieve Filters",
            f"  {k_style.render('G')}      GPG/PGP Keyring       {k_style.render('Ctrl+P')} Command Palette",
            f"  {k_style.render('q')}      Quit EspressoMail",
        ]
        return self.theme.modal_style(modal_w, "⌨️ EspressoMail Keyboard & Mouse Shortcuts").render(
            "\n".join(lines)
        )


# ---------------------------------------------------------------------------
# 8. Download Attachments Selection Modal
# ---------------------------------------------------------------------------


class DownloadAttachmentsModal(Model):
    """Interactive modal allowing users to selectively choose which attachments to save and customize target directory."""

    def __init__(
        self,
        attachments: list[Attachment],
        download_dir: Path | str,
        theme: Theme,
        store: LocalStore,
    ) -> None:
        self.attachments = attachments
        self.download_dir_path = Path(download_dir).expanduser()
        self.theme = theme
        self.store = store
        self.selected_indices: set[int] = set(range(len(attachments)))
        self.cursor_idx: int = 0
        self.focus_target: str = "list"  # "list" or "dir"
        self.input_dir = TextInput(placeholder="Download directory path...", prompt="Target Folder: ")
        self.input_dir.value = str(self.download_dir_path)
        self.error_msg = ""

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[DownloadAttachmentsModal, Cmd | None]:
        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k == "esc":
                return self, lambda: ModalClosedMsg("Download cancelled.")

            if k in ("tab", "shift+tab"):
                self.focus_target = "dir" if self.focus_target == "list" else "list"
                if self.focus_target == "dir":
                    self.input_dir.focus()
                else:
                    self.input_dir.blur()
                return self, None

            if self.focus_target == "list":
                if k in ("up", "k"):
                    if self.attachments:
                        self.cursor_idx = (self.cursor_idx - 1) % len(self.attachments)
                    return self, None
                if k in ("down", "j"):
                    if self.attachments:
                        self.cursor_idx = (self.cursor_idx + 1) % len(self.attachments)
                    return self, None
                if k in (" ", "space"):
                    if self.cursor_idx in self.selected_indices:
                        self.selected_indices.remove(self.cursor_idx)
                    else:
                        self.selected_indices.add(self.cursor_idx)
                    return self, None
                if k == "a":
                    if len(self.selected_indices) == len(self.attachments):
                        self.selected_indices.clear()
                    else:
                        self.selected_indices = set(range(len(self.attachments)))
                    return self, None
                if k in ("enter", "d", "ctrl+s"):
                    return self._execute_download()

            elif self.focus_target == "dir":
                if k in ("enter", "ctrl+s"):
                    return self._execute_download()

        if self.focus_target == "dir":
            self.input_dir, cmd = self.input_dir.update(msg)
            return self, cmd

        return self, None

    def _execute_download(self) -> tuple[DownloadAttachmentsModal, Cmd | None]:
        if not self.selected_indices:
            self.error_msg = "Please select at least one attachment to download."
            return self, None

        target_str = self.input_dir.value.strip() or str(self.download_dir_path)
        target_dir = Path(target_str).expanduser()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.error_msg = f"Cannot create folder: {e}"
            return self, None

        saved_files: list[str] = []
        for idx in sorted(self.selected_indices):
            att = self.attachments[idx]
            out_p = self.store.save_attachment_to_disk(att.id, target_dir)
            saved_files.append(out_p.name)

        note = f"✔ Saved {len(saved_files)} attachment(s) to {target_dir}: {', '.join(saved_files)}"
        return self, lambda: ModalClosedMsg(note)

    def view(self, width: int = 80) -> str:
        modal_w = min(76, max(54, width - 8))
        rows: list[str] = []

        rows.append(
            Style()
            .bold(True)
            .foreground(self.theme.accent_secondary)
            .render("Select attachments to save to disk:")
        )
        rows.append("")

        for idx, att in enumerate(self.attachments):
            is_cur = idx == self.cursor_idx and self.focus_target == "list"
            is_chk = idx in self.selected_indices
            chk_str = "[x]" if is_chk else "[ ]"
            icon = "🖼️" if att.is_image else "📎"
            line_str = f" {chk_str} {icon} {att.filename}  ({att.human_size})"
            if is_cur:
                rows.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.bg_primary)
                    .background(self.theme.accent_primary)
                    .width(modal_w - 4)
                    .render(line_str)
                )
            elif is_chk:
                rows.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.attachment_fg)
                    .render(line_str)
                )
            else:
                rows.append(
                    Style()
                    .foreground(self.theme.fg_muted)
                    .render(line_str)
                )

        rows.append("")
        rows.append(self.input_dir.view())

        if self.error_msg:
            rows.append("")
            rows.append(
                Style()
                .bold(True)
                .foreground(self.theme.pgp_error_fg)
                .render(f"⚠ {self.error_msg}")
            )

        rows.append("")
        rows.append(
            Style()
            .foreground(self.theme.fg_muted)
            .render(
                "[Space] Toggle  [a] Select All  [Tab] Edit Folder  [Enter/d] Download  [Esc] Cancel"
            )
        )

        return self.theme.modal_style(modal_w, "💾 Download Attachments").render(
            join_vertical(Align.LEFT, *rows)
        )


# ---------------------------------------------------------------------------
# 9. General Application Preferences & Configuration Modal
# ---------------------------------------------------------------------------


class PreferencesModal(Model):
    """General application settings & preferences dialog for configuring downloads, image renderer, theme, and behavior."""

    IMAGE_BACKENDS: tuple[str, ...] = ("auto", "chafa", "half_block", "braille", "sixel", "kitty", "off")
    THEMES: tuple[str, ...] = (
        "espresso",
        "dracula",
        "gruvbox",
        "nord",
        "tokyonight",
        "monokai",
        "solarized-dark",
        "solarized-light",
    )
    FOLDER_COUNT_MODES: tuple[str, ...] = ("unread_only", "unread_and_total")
    FETCH_BATCH_SIZES: tuple[int, ...] = (25, 50, 100, 200)

    def __init__(
        self,
        store: LocalStore,
        current_theme: Theme,
        download_dir: Path | str,
        current_image_backend: str = "auto",
        threading_enabled: bool = True,
        show_raw_headers: bool = False,
    ) -> None:
        self.store = store
        self.current_theme = current_theme
        self.focus_idx = 0  # 0=download_dir, 1=image_backend, 2=theme, 3=threading, 4=raw_headers, 5=sync_startup, 6=remote_imgs, 7=count_mode, 8=batch_size
        self.status_msg = ""
        self.error_msg = ""

        # Settings
        self.input_download_dir = TextInput(placeholder="Download folder path...", prompt="Download Folder:     ")
        self.input_download_dir.value = str(download_dir)

        backend_clean = current_image_backend.lower()
        self.backend_idx = (
            self.IMAGE_BACKENDS.index(backend_clean)
            if backend_clean in self.IMAGE_BACKENDS
            else 0
        )

        theme_clean = current_theme.id.lower()
        self.theme_idx = (
            self.THEMES.index(theme_clean)
            if theme_clean in self.THEMES
            else 0
        )

        self.threading_enabled = threading_enabled
        self.show_raw_headers = show_raw_headers
        self.sync_on_startup = self.store.get_setting("sync_on_startup", "1") == "1"
        self.allow_remote_images = self.store.get_setting("allow_remote_images", "1") == "1"

        cur_count_mode = self.store.get_setting("folder_count_mode", "unread_only")
        self.count_mode_idx = (
            self.FOLDER_COUNT_MODES.index(cur_count_mode)
            if cur_count_mode in self.FOLDER_COUNT_MODES
            else 0
        )

        try:
            cur_batch = int(self.store.get_setting("fetch_batch_size", "50"))
        except Exception:
            cur_batch = 50
        self.batch_idx = (
            self.FETCH_BATCH_SIZES.index(cur_batch)
            if cur_batch in self.FETCH_BATCH_SIZES
            else 1
        )

        self._sync_focus()

    def _sync_focus(self) -> None:
        if self.focus_idx == 0:
            self.input_download_dir.focus()
        else:
            self.input_download_dir.blur()

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[PreferencesModal, Cmd | None]:
        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k == "esc":
                return self, lambda: ModalClosedMsg("Closed preferences.")

            if k in ("enter", "ctrl+s"):
                return self._save_preferences()

            if k in ("tab", "down"):
                self.focus_idx = (self.focus_idx + 1) % 9
                self._sync_focus()
                return self, None
            if k in ("shift+tab", "up"):
                self.focus_idx = (self.focus_idx - 1) % 9
                self._sync_focus()
                return self, None

            # Cycling / toggling options
            if self.focus_idx == 1 and k in ("left", "right", "h", "l", " ", "space"):
                delta = 1 if k in ("right", "l", " ", "space") else -1
                self.backend_idx = (self.backend_idx + delta) % len(self.IMAGE_BACKENDS)
                return self, None

            if self.focus_idx == 2 and k in ("left", "right", "h", "l", " ", "space"):
                delta = 1 if k in ("right", "l", " ", "space") else -1
                self.theme_idx = (self.theme_idx + delta) % len(self.THEMES)
                return self, None

            if self.focus_idx == 3 and k in (" ", "space", "left", "right"):
                self.threading_enabled = not self.threading_enabled
                return self, None

            if self.focus_idx == 4 and k in (" ", "space", "left", "right"):
                self.show_raw_headers = not self.show_raw_headers
                return self, None

            if self.focus_idx == 5 and k in (" ", "space", "left", "right"):
                self.sync_on_startup = not self.sync_on_startup
                return self, None

            if self.focus_idx == 6 and k in (" ", "space", "left", "right"):
                self.allow_remote_images = not self.allow_remote_images
                return self, None

            if self.focus_idx == 7 and k in ("left", "right", "h", "l", " ", "space"):
                delta = 1 if k in ("right", "l", " ", "space") else -1
                self.count_mode_idx = (self.count_mode_idx + delta) % len(self.FOLDER_COUNT_MODES)
                return self, None

            if self.focus_idx == 8 and k in ("left", "right", "h", "l", " ", "space"):
                delta = 1 if k in ("right", "l", " ", "space") else -1
                self.batch_idx = (self.batch_idx + delta) % len(self.FETCH_BATCH_SIZES)
                return self, None

        if self.focus_idx == 0:
            self.input_download_dir, cmd = self.input_download_dir.update(msg)
            return self, cmd

        return self, None

    def _save_preferences(self) -> tuple[PreferencesModal, Cmd | None]:
        dir_val = self.input_download_dir.value.strip()
        if not dir_val:
            self.error_msg = "Download folder cannot be empty."
            return self, None

        try:
            p = Path(dir_val).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.error_msg = f"Invalid download directory: {e}"
            return self, None

        backend_val = self.IMAGE_BACKENDS[self.backend_idx]
        theme_val = self.THEMES[self.theme_idx]
        count_mode_val = self.FOLDER_COUNT_MODES[self.count_mode_idx]
        batch_val = self.FETCH_BATCH_SIZES[self.batch_idx]

        self.store.set_setting("download_dir", str(p))
        self.store.set_setting("image_backend", backend_val)
        self.store.set_setting("theme", theme_val)
        self.store.set_setting("threading", "1" if self.threading_enabled else "0")
        self.store.set_setting("show_raw_headers", "1" if self.show_raw_headers else "0")
        self.store.set_setting("sync_on_startup", "1" if self.sync_on_startup else "0")
        self.store.set_setting("allow_remote_images", "1" if self.allow_remote_images else "0")
        self.store.set_setting("folder_count_mode", count_mode_val)
        self.store.set_setting("fetch_batch_size", str(batch_val))

        return self, lambda: PreferencesSavedMsg(
            download_dir=str(p),
            image_backend=backend_val,
            theme=theme_val,
            threading=self.threading_enabled,
            show_raw_headers=self.show_raw_headers,
            sync_on_startup=self.sync_on_startup,
            allow_remote_images=self.allow_remote_images,
            folder_count_mode=count_mode_val,
            fetch_batch_size=batch_val,
        )

    def view(self, width: int = 80) -> str:
        modal_w = min(74, max(56, width - 8))
        rows: list[str] = []

        def _opt_style(idx: int, text: str) -> str:
            if idx == self.focus_idx:
                return Style().bold(True).foreground(self.current_theme.accent_primary).render(text)
            return Style().foreground(self.current_theme.fg_primary).render(text)

        rows.append(self.input_download_dir.view())

        cur_b = self.IMAGE_BACKENDS[self.backend_idx].upper()
        rows.append(
            f"Image Rendering Mode: ◀ {_opt_style(1, cur_b)} ▶"
        )

        cur_t = self.THEMES[self.theme_idx].capitalize()
        rows.append(
            f"Terminal Theme:       ◀ {_opt_style(2, cur_t)} ▶"
        )

        th_val = "[x] Enabled" if self.threading_enabled else "[ ] Disabled"
        rows.append(
            f"Message Threading:    {_opt_style(3, th_val)}"
        )

        rh_val = "[x] Enabled" if self.show_raw_headers else "[ ] Disabled"
        rows.append(
            f"Show Raw RFC Headers: {_opt_style(4, rh_val)}"
        )

        su_val = "[x] Enabled" if self.sync_on_startup else "[ ] Disabled"
        rows.append(
            f"Sync On Startup:      {_opt_style(5, su_val)}"
        )

        ri_val = "[x] Allowed" if self.allow_remote_images else "[ ] Blocked"
        rows.append(
            f"Remote Image Fetch:   {_opt_style(6, ri_val)}"
        )

        cur_cm = "Unread Only" if self.FOLDER_COUNT_MODES[self.count_mode_idx] == "unread_only" else "Unread & Total"
        rows.append(
            f"Folder Email Counts:  ◀ {_opt_style(7, cur_cm)} ▶"
        )

        cur_bs = f"{self.FETCH_BATCH_SIZES[self.batch_idx]} emails"
        rows.append(
            f"Fetch Batch Size:     ◀ {_opt_style(8, cur_bs)} ▶"
        )

        if self.error_msg:
            rows.append("")
            rows.append(
                Style().bold(True).foreground(self.current_theme.pgp_error_fg).render(f"⚠ {self.error_msg}")
            )

        rows.append("")
        rows.append(
            Style()
            .foreground(self.current_theme.fg_muted)
            .render(
                "[Tab/Arrows] Navigate  [Space/Left/Right] Change/Toggle  [Enter/Ctrl+S] Save  [Esc] Cancel"
            )
        )

        return self.current_theme.modal_style(modal_w, "⚙️ General Preferences").render(
            join_vertical(Align.LEFT, *rows)
        )

