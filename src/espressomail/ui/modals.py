"""Interactive modal dialogs for Compose, Fuzzy Search, Address Book, Sieve Filters, PGP, and Help."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from espresso import Cmd, KeyMsg, Model, MouseAction, MouseButton, MouseMsg, Msg
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
    string_width,
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
    SieveScript,
)
from espressomail.protocols.carddav import CardDAVClient
from espressomail.protocols.icalendar import CalendarEvent
from espressomail.protocols.sieve import (
    ManageSieveClient,
    apply_sieve_rules_locally,
    compile_sieve_script,
    validate_sieve_script,
)
from espressomail.protocols.smtp_client import validate_account_credentials
from espressomail.search.fuzzy import FuzzySearchEngine, SearchResult
from espressomail.storage.database import LocalStore
from espressomail.themes.engine import COLOR_MODES, THEME_FAMILIES, THEMES, Theme


# ---------------------------------------------------------------------------
# Modal Messages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModalClosedMsg(Msg):
    """Emitted when a modal dialog is dismissed."""

    status_note: str = ""
    trigger_sync: bool = False


@dataclass(frozen=True)
class RSVPSubmittedMsg(Msg):
    """Emitted when user submits an RSVP response to a calendar invitation."""

    account: AccountConfig
    orig_message: EmailMessage
    event: CalendarEvent
    partstat: str  # "ACCEPTED", "DECLINED", "TENTATIVE"
    comment: str = ""



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

        styles = self.theme.text_input_styles()
        self.input_to = TextInput(placeholder="recipient@example.com", prompt="To:      ", **styles)
        self.input_to.value = to_initial
        self.input_cc = TextInput(placeholder="cc@example.com (optional)", prompt="Cc:      ", **styles)
        self.input_cc.value = cc_initial
        self.input_subject = TextInput(placeholder="Subject line...", prompt="Subject: ", **styles)
        self.input_subject.value = subject_initial

        self.editor = TextArea(
            placeholder="Write your message here...",
            width=68,
            height=10,
            show_line_numbers=False,
            **self.theme.text_area_styles(),
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
        self.autocomplete_idx = 0
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

    def _current_recipient_query(self) -> str:
        if self.focus_idx == 1:
            val = self.input_to.value
        elif self.focus_idx == 2:
            val = self.input_cc.value
        else:
            return ""
        parts = val.split(",")
        return parts[-1].strip()

    def _get_autocomplete_matches(self) -> list[Contact]:
        if self.focus_idx not in (1, 2):
            return []
        query = self._current_recipient_query().lower()
        if not query:
            return []
        matches: list[Contact] = []
        for c in self.contacts:
            if (
                query in c.name.lower()
                or query in c.email.lower()
                or (c.organization and query in c.organization.lower())
            ):
                matches.append(c)
        matches.sort(key=lambda c: (not c.favorite, -c.frequency, c.name.lower()))
        return matches[:5]

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

            if self.focus_idx in (1, 2):
                matches = self._get_autocomplete_matches()
                if matches:
                    if k in ("down", "ctrl+n"):
                        self.autocomplete_idx = (self.autocomplete_idx + 1) % len(matches)
                        return self, None
                    if k in ("up", "ctrl+p"):
                        self.autocomplete_idx = (self.autocomplete_idx - 1) % len(matches)
                        return self, None
                    if k in ("tab", "enter"):
                        selected = matches[self.autocomplete_idx % len(matches)]
                        target = self.input_to if self.focus_idx == 1 else self.input_cc
                        parts = [p.strip() for p in target.value.split(",")[:-1] if p.strip()]
                        parts.append(selected.formatted)
                        target.value = ", ".join(parts) + ", "
                        self.autocomplete_idx = 0
                        return self, None

            if k == "enter" and self.focus_idx in (1, 2, 3):
                self.focus_idx = (self.focus_idx + 1) % 5
                self._sync_focus()
                return self, None
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

    def view(self, width: int = 80, height: int = 30) -> str:
        modal_w = min(114, max(72, width - 4))
        self.editor.width = max(50, modal_w - 6)
        self.editor.height = max(12, min(28, height - 14))
        self.file_picker.width = max(50, modal_w - 6)

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

        to_view = self.input_to.view()
        cc_view = self.input_cc.view()
        matches = self._get_autocomplete_matches()
        if matches:
            chips = []
            for idx, c in enumerate(matches):
                disp = c.formatted
                if idx == (self.autocomplete_idx % len(matches)):
                    chip = Style().bold(True).reverse(True).foreground(self.theme.accent_primary).render(f" {disp} ")
                else:
                    chip = Style().foreground(self.theme.fg_secondary).render(f"[{disp}]")
                chips.append(chip)
            sugg_str = Style().foreground(self.theme.fg_muted).render("         Suggestions (Tab/Enter): ") + " ".join(chips)
            if self.focus_idx == 1:
                to_view = f"{to_view}\n{sugg_str}"
            elif self.focus_idx == 2:
                cc_view = f"{cc_view}\n{sugg_str}"

        body_border_fg = (
            self.theme.border_focused if self.focus_idx == 4 else self.theme.border_unfocused
        )
        editor_box = (
            Style()
            .border(ROUNDED_BORDER)
            .border_foreground(body_border_fg)
            .border_background(self.theme.bg_primary)
            .background(self.theme.bg_primary)
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
            to_view,
            cc_view,
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
            **self.theme.text_input_styles(),
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
    """Local address book manager with vertical viewport scrolling, mouse navigation, and contact management."""

    def __init__(self, store: LocalStore, theme: Theme) -> None:
        self.store = store
        self.theme = theme
        self.contacts: list[Contact] = self.store.list_contacts()
        self.cursor = 0
        self.scroll = 0
        self.page_size = 10
        self.adding_mode = False
        self.status_msg = ""
        styles = self.theme.text_input_styles()
        self.input_name = TextInput(placeholder="Ada Lovelace", prompt="Name:  ", **styles)
        self.input_email = TextInput(placeholder="ada@analytical.org", prompt="Email: ", **styles)
        self.input_org = TextInput(placeholder="Organization", prompt="Org:   ", **styles)
        self.add_focus = 0
        self._adjust_scroll()

    def _adjust_scroll(self) -> None:
        if not self.contacts:
            self.cursor = 0
            self.scroll = 0
            return
        self.cursor = max(0, min(self.cursor, len(self.contacts) - 1))
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + self.page_size:
            self.scroll = self.cursor - self.page_size + 1
        max_scroll = max(0, len(self.contacts) - self.page_size)
        self.scroll = max(0, min(self.scroll, max_scroll))

    def _refresh(self) -> None:
        self.contacts = self.store.list_contacts()
        self.cursor = min(self.cursor, max(0, len(self.contacts) - 1))
        self._adjust_scroll()

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
                self._adjust_scroll()
                return self, None
            if k in ("up", "k") and self.contacts:
                self.cursor = (self.cursor - 1) % len(self.contacts)
                self._adjust_scroll()
                return self, None
            if k in ("page_down", "pgdown") and self.contacts:
                self.cursor = min(len(self.contacts) - 1, self.cursor + self.page_size)
                self._adjust_scroll()
                return self, None
            if k in ("page_up", "pgup") and self.contacts:
                self.cursor = max(0, self.cursor - self.page_size)
                self._adjust_scroll()
                return self, None
            if k == "home" and self.contacts:
                self.cursor = 0
                self._adjust_scroll()
                return self, None
            if k == "end" and self.contacts:
                self.cursor = max(0, len(self.contacts) - 1)
                self._adjust_scroll()
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
                self.status_msg = f"Deleted contact {target.name}."
                return self, None
            if k == "s":
                accounts = self.store.list_accounts()
                carddav_acc = next((a for a in accounts if a.carddav_url), None)
                if carddav_acc and carddav_acc.carddav_url:
                    try:
                        client = CardDAVClient(
                            url=carddav_acc.carddav_url,
                            username=carddav_acc.carddav_username or carddav_acc.email,
                            password=carddav_acc.carddav_password or carddav_acc.imap_password,
                        )
                        down, up, summary = client.sync_contacts(self.store)
                        self._refresh()
                        self.status_msg = summary
                    except Exception as exc:
                        self.status_msg = f"CardDAV sync failed: {exc}"
                else:
                    self.status_msg = "CardDAV server not configured. Set CardDAV URL in Account Settings."
                return self, None
            if k in ("enter", "c") and self.contacts:
                target = self.contacts[self.cursor]
                return self, lambda: ComposeToContactMsg(target.formatted)

        if isinstance(msg, MouseMsg) and self.contacts:
            if msg.button == MouseButton.WHEEL_UP:
                self.cursor = max(0, self.cursor - 1)
                self._adjust_scroll()
                return self, None
            if msg.button == MouseButton.WHEEL_DOWN:
                self.cursor = min(len(self.contacts) - 1, self.cursor + 1)
                self._adjust_scroll()
                return self, None

        return self, None

    def view(self, width: int = 80) -> str:
        self._adjust_scroll()
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
        visible = self.contacts[self.scroll : self.scroll + self.page_size]
        for rel_idx, c in enumerate(visible):
            abs_idx = self.scroll + rel_idx
            fav = "★ " if c.favorite else "  "
            pgp_badge = "🔑PGP" if c.pgp_fingerprint else "     "
            line = f" {fav}{c.name[:20]:<20} │ {c.email[:26]:<26} │ {c.organization[:14]:<14} {pgp_badge}"
            truncated = truncate_ansi(line, inner_w)
            if abs_idx == self.cursor:
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

        scroll_indicator = ""
        if len(self.contacts) > self.page_size:
            scroll_indicator = (
                f"Showing {self.scroll + 1}–{min(len(self.contacts), self.scroll + self.page_size)} of {len(self.contacts)} contacts"
            )

        footer_text = (
            "[↑/↓/PgUp/PgDn] Scroll   [s] Sync CardDAV   [Enter/c] Compose   [a] Add   [d] Delete   [Esc] Close"
        )
        footer_elements = [
            Style().foreground(self.theme.fg_muted).render(footer_text)
        ]
        if self.status_msg:
            footer_elements.insert(0, Style().bold(True).foreground(self.theme.pgp_verified_fg).render(self.status_msg))
        if scroll_indicator:
            footer_elements.insert(0, Style().foreground(self.theme.accent_secondary).render(scroll_indicator))

        title = f"📇 Local Address Book ({len(self.contacts)} contacts)" if self.contacts else "📇 Local Address Book"
        content = join_vertical(Align.LEFT, "\n".join(rows), "", *footer_elements)
        return self.theme.modal_style(modal_w, title).render(content)


# ---------------------------------------------------------------------------
# 4. Sieve Filters Manager Modal
# ---------------------------------------------------------------------------


class SieveModal(Model):
    """Interactive Sieve filter configurator, RFC 5228 compiler, editor, and local rule runner."""

    def __init__(self, store: LocalStore, theme: Theme, active_account_id: str = "acc_work") -> None:
        self.store = store
        self.theme = theme
        self.active_account_id = active_account_id if active_account_id != "*" else "acc_work"
        self.rules: list[SieveFilterRule] = self.store.list_sieve_rules()
        self.cursor = 0
        self.scroll = 0
        self.page_size = 5
        self.adding_rule = False
        self.editor_focused = True
        self.status_banner = "RFC 5228 Sieve Script valid and ready."
        styles = self.theme.text_input_styles()
        self.input_name = TextInput(placeholder="Filter Name", prompt="Rule Name:   ", **styles)
        self.input_field = TextInput(placeholder="From / Subject / X-Spam-Flag", prompt="Header/Field:", **styles)
        self.input_pattern = TextInput(placeholder="Match substring", prompt="Pattern:     ", **styles)
        self.input_folder = TextInput(placeholder="Archive / Trash / INBOX", prompt="Target Folder:", **styles)
        self.add_focus = 0

        self.script_editor = TextArea(
            placeholder="Write RFC 5228 Sieve script here...",
            width=100,
            height=12,
            show_line_numbers=True,
            **self.theme.text_area_styles(),
        )
        self.script_editor.focus()
        active_s = self.store.get_active_sieve_script(self.active_account_id)
        if active_s and active_s.content:
            self.script_editor.value = active_s.content.expandtabs(4)
        elif self.rules:
            self.script_editor.value = compile_sieve_script(self.rules).expandtabs(4)
        else:
            self.script_editor.value = (
                'require ["fileinto", "imap4flags"];\n\n'
                '# Sieve filter rules for EspressoMail\n'
                'if header :contains "Subject" "Alert" {\n'
                '    fileinto "Archive";\n'
                '    stop;\n'
                '}\n'
            )
        self._adjust_scroll()

    def _adjust_scroll(self) -> None:
        if not self.rules:
            self.cursor = 0
            self.scroll = 0
            return
        self.cursor = max(0, min(self.cursor, len(self.rules) - 1))
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + self.page_size:
            self.scroll = self.cursor - self.page_size + 1
        max_scroll = max(0, len(self.rules) - self.page_size)
        self.scroll = max(0, min(self.scroll, max_scroll))

    def _refresh(self) -> None:
        self.rules = self.store.list_sieve_rules()
        self.cursor = min(self.cursor, max(0, len(self.rules) - 1))
        self._adjust_scroll()
        active_s = self.store.get_active_sieve_script(self.active_account_id)
        if active_s and active_s.content:
            self.script_editor.value = active_s.content.expandtabs(4)

    def init(self) -> Cmd | None:
        return None

    def _save_active_script(self) -> None:
        expanded_content = self.script_editor.value.expandtabs(4)
        ok, errs = validate_sieve_script(expanded_content)
        if not ok:
            self.status_banner = f"✖ Sieve syntax error: {'; '.join(errs)}"
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        script_id = f"sieve:{self.active_account_id}:active"
        self.store.save_sieve_script(
            SieveScript(
                id=script_id,
                account_id=self.active_account_id,
                name="active.sieve",
                content=expanded_content,
                active=True,
                updated_at=now_iso,
            )
        )
        accounts = self.store.list_accounts()
        target_acc = next((a for a in accounts if a.id == self.active_account_id), None)
        if target_acc and target_acc.sieve_host:
            try:
                client = ManageSieveClient(target_acc)
                client.connect()
                client.put_script("active.sieve", expanded_content, activate=True)
                client.close()
                self.status_banner = "✔ Saved and uploaded active Sieve script to server."
            except Exception as ex:
                self.status_banner = f"Saved script locally (upload notice: {ex})"
        else:
            self.status_banner = "✔ Saved script locally."

    def _sync_from_server(self) -> None:
        accounts = self.store.list_accounts()
        target_acc = next((a for a in accounts if a.id == self.active_account_id), None)
        if not target_acc and accounts:
            target_acc = accounts[0]
        if target_acc and target_acc.sieve_host:
            try:
                client = ManageSieveClient(target_acc)
                client.connect()
                remote_scripts = client.list_scripts()
                active_name = ""
                for s_name, is_act in remote_scripts:
                    if is_act:
                        active_name = s_name
                        break
                if not active_name and remote_scripts:
                    active_name = remote_scripts[0][0]

                if active_name:
                    fetched_script = client.get_script(active_name)
                    now_iso = datetime.now(timezone.utc).isoformat()
                    self.store.save_sieve_script(
                        SieveScript(
                            id=f"sieve:{target_acc.id}:{active_name}",
                            account_id=target_acc.id,
                            name=active_name,
                            content=fetched_script,
                            active=True,
                            updated_at=now_iso,
                        )
                    )
                    self._refresh()
                    self.script_editor.value = fetched_script.expandtabs(4)
                    self.status_banner = (
                        f"✔ Synchronized script '{active_name}' ({len(fetched_script.splitlines())} lines) from server."
                    )
                else:
                    ok, msg_text = client.sync_script_with_server(self.store)
                    self._refresh()
                    self.status_banner = msg_text
                client.close()
            except Exception as ex:
                self.status_banner = f"ManageSieve sync failed: {ex}"
        else:
            self.status_banner = "No ManageSieve host configured for this account."

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
                        # Also update editor preview if rules compiled
                        self.script_editor.value = compile_sieve_script(self.rules)
                        self.status_banner = f"Added Sieve rule '{new_rule.name}'."
                    return self, None

            fields[self.add_focus], cmd = fields[self.add_focus].update(msg)
            return self, cmd

        if self.editor_focused:
            if isinstance(msg, KeyMsg):
                k = str(msg.key)
                if k in ("esc", "shift+tab"):
                    self.editor_focused = False
                    self.script_editor.blur()
                    return self, None
                if k == "ctrl+s":
                    self._save_active_script()
                    return self, None
                if k in ("ctrl+u", "f5"):
                    self._sync_from_server()
                    return self, None

            if isinstance(msg, MouseMsg):
                if msg.button in (MouseButton.WHEEL_UP, MouseButton.WHEEL_DOWN):
                    self.script_editor, cmd = self.script_editor.update(msg)
                    return self, cmd
                if msg.action in (MouseAction.PRESS, MouseAction.DOUBLE_CLICK):
                    rules_y0, rules_y1 = getattr(self, "_layout_rules_y", (2, 8))
                    if rules_y0 <= msg.y < rules_y1 and self.rules:
                        clicked_rel = msg.y - rules_y0
                        clicked_abs = self.scroll + clicked_rel
                        if 0 <= clicked_abs < len(self.rules):
                            self.cursor = clicked_abs
                            self.editor_focused = False
                            self.script_editor.blur()
                            return self, None

            self.script_editor, cmd = self.script_editor.update(msg)
            return self, cmd

        if isinstance(msg, MouseMsg):
            if msg.button == MouseButton.WHEEL_UP and self.rules:
                self.cursor = max(0, self.cursor - 1)
                self._adjust_scroll()
                return self, None
            if msg.button == MouseButton.WHEEL_DOWN and self.rules:
                self.cursor = min(len(self.rules) - 1, self.cursor + 1)
                self._adjust_scroll()
                return self, None
            if msg.action in (MouseAction.PRESS, MouseAction.DOUBLE_CLICK):
                rules_y0, rules_y1 = getattr(self, "_layout_rules_y", (2, 8))
                if rules_y0 <= msg.y < rules_y1 and self.rules:
                    clicked_rel = msg.y - rules_y0
                    clicked_abs = self.scroll + clicked_rel
                    if 0 <= clicked_abs < len(self.rules):
                        self.cursor = clicked_abs
                        self._adjust_scroll()
                        return self, None
                else:
                    self.editor_focused = True
                    self.script_editor.focus()
                    inner_x, inner_y = getattr(self, "_layout_editor_inner", (0, 0))
                    rel_msg = MouseMsg(
                        x=max(0, msg.x - inner_x),
                        y=max(0, msg.y - inner_y),
                        button=msg.button,
                        action=msg.action,
                        ctrl=msg.ctrl,
                        alt=msg.alt,
                        shift=msg.shift,
                    )
                    self.script_editor, cmd = self.script_editor.update(rel_msg)
                    return self, cmd

        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k in ("esc", "q"):
                return self, lambda: ModalClosedMsg(self.status_banner)
            if k == "ctrl+s":
                self._save_active_script()
                return self, None
            if k in ("tab", "e", "i", "enter"):
                self.editor_focused = True
                self.script_editor.focus()
                return self, None
            if k in ("down", "j") and self.rules:
                self.cursor = (self.cursor + 1) % len(self.rules)
                self._adjust_scroll()
                return self, None
            if k in ("up", "k") and self.rules:
                self.cursor = (self.cursor - 1) % len(self.rules)
                self._adjust_scroll()
                return self, None
            if k in ("page_down", "pgdown") and self.rules:
                self.cursor = min(len(self.rules) - 1, self.cursor + self.page_size)
                self._adjust_scroll()
                return self, None
            if k in ("page_up", "pgup") and self.rules:
                self.cursor = max(0, self.cursor - self.page_size)
                self._adjust_scroll()
                return self, None
            if k == "home" and self.rules:
                self.cursor = 0
                self._adjust_scroll()
                return self, None
            if k == "end" and self.rules:
                self.cursor = max(0, len(self.rules) - 1)
                self._adjust_scroll()
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
                script_to_val = self.script_editor.value or compile_sieve_script(self.rules)
                ok, errs = validate_sieve_script(script_to_val)
                self.status_banner = (
                    "✔ Sieve RFC 5228 syntax verified with 0 errors."
                    if ok
                    else f"✖ Validation error: {'; '.join(errs)}"
                )
                return self, None
            if k in ("u", "ctrl+u", "f5"):
                self._sync_from_server()
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

    def view(self, width: int = 80, height: int = 24) -> str:
        self._adjust_scroll()
        target_outer_w = min(114, max(68, width - 4))
        modal_w = target_outer_w - 6
        inner_w = modal_w
        content_editor_w = max(20, inner_w - 4)
        self.script_editor.width = content_editor_w

        rule_count = max(1, min(len(self.rules), self.page_size))
        total_h = rule_count + 21
        modal_top = max(0, (height - total_h) // 2)
        modal_left = max(0, (width - target_outer_w) // 2)
        self._layout_rules_y = (modal_top + 2, modal_top + 2 + rule_count)
        self._layout_editor_y = (modal_top + 3 + rule_count, modal_top + 3 + rule_count + 14)
        self._layout_editor_inner = (modal_left + 4, modal_top + 4 + rule_count)

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
        visible_rules = self.rules[self.scroll : self.scroll + self.page_size]
        for rel_idx, r in enumerate(visible_rules):
            abs_idx = self.scroll + rel_idx
            chk = "[x]" if r.enabled else "[ ]"
            desc = f"{chk} {r.name:<24} │ if {r.field} {r.comparator} \"{r.pattern}\" → {r.action} \"{r.action_arg}\""
            truncated = truncate_ansi(desc, inner_w)
            if abs_idx == self.cursor and not self.editor_focused:
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

        if not rule_rows:
            rule_rows.append(Style().italic(True).foreground(self.theme.fg_muted).render("  No Sieve filter rules configured."))

        editor_border_color = self.theme.border_focused if self.editor_focused else self.theme.border_unfocused
        editor_title_str = (
            Style().bold(True).foreground(self.theme.accent_primary).render(" Active Sieve Script Editor [Editing - Esc to unfocus] ")
            if self.editor_focused
            else Style().foreground(self.theme.fg_secondary).render(" Active Sieve Script Editor [Click or Tab to edit] ")
        )
        editor_box = (
            Style()
            .border(ROUNDED_BORDER)
            .border_foreground(editor_border_color)
            .border_background(self.theme.bg_primary)
            .background(self.theme.bg_primary)
            .border_title(editor_title_str, align=Align.LEFT)
            .width(inner_w - 2)
            .render(self.script_editor.view())
        )

        status_line = Style().bold(True).foreground(self.theme.pgp_verified_fg).render(
            truncate_ansi(self.status_banner, inner_w)
        )
        if inner_w >= 110:
            footer_text = "[Tab/Click] Edit Script  [Esc] Unfocus/Close  [Space] Toggle  [a] Add  [d] Delete  [v] Validate  [u] Sync  [Ctrl+S] Save"
        else:
            footer_text = "[Tab/Click] Edit  [Esc] Back  [Space] Toggle  [a] Add  [d] Del  [u] Sync  [Ctrl+S] Save"
        footer = Style().foreground(self.theme.fg_muted).render(truncate_ansi(footer_text, inner_w))

        content = join_vertical(
            Align.LEFT,
            "\n".join(rule_rows),
            "",
            editor_box,
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
        styles = self.theme.text_input_styles()
        self.input_account_name = TextInput(placeholder="Work / Personal", prompt="Account Name:  ", **styles)
        self.input_full_name = TextInput(placeholder="Jane Doe", prompt="Full Name:     ", **styles)
        self.input_email = TextInput(placeholder="jane@example.com", prompt="Email Address: ", **styles)
        self.input_username = TextInput(placeholder="Login username", prompt="Login User:    ", **styles)
        self.input_password = TextInput(
            placeholder="IMAP / SMTP Password",
            prompt="Password:      ",
            echo_mode=EchoMode.PASSWORD,
            **styles,
        )
        self.input_imap_host = TextInput(placeholder="imap.example.com", prompt="IMAP Host:     ", **styles)
        self.input_imap_port = TextInput(placeholder="993", prompt="IMAP Port:     ", **styles)
        self.imap_security: SecurityType = SecurityType.SSL_TLS
        self.input_smtp_host = TextInput(placeholder="smtp.example.com", prompt="SMTP Host:     ", **styles)
        self.input_smtp_port = TextInput(placeholder="587", prompt="SMTP Port:     ", **styles)
        self.smtp_security: SecurityType = SecurityType.STARTTLS
        self.input_sieve_host = TextInput(placeholder="sieve.example.com", prompt="Sieve Host:    ", **styles)
        self.input_sieve_port = TextInput(placeholder="4190", prompt="Sieve Port:    ", **styles)
        self.input_carddav_url = TextInput(placeholder="https://dav.example.com/addressbooks/...", prompt="CardDAV URL:   ", **styles)
        self.input_carddav_username = TextInput(placeholder="(Defaults to IMAP User)", prompt="CardDAV User:  ", **styles)
        self.input_carddav_password = TextInput(
            placeholder="(Defaults to IMAP Pass)",
            prompt="CardDAV Pass:  ",
            echo_mode=EchoMode.PASSWORD,
            **styles,
        )
        self.input_signature = TextArea(
            placeholder="Optional email signature...",
            height=3,
            width=54,
            show_line_numbers=False,
            **self.theme.text_area_styles(),
        )

        # Focus indices:
        # 0: Account selector
        # 1: Account Name, 2: Full Name, 3: Email, 4: Username, 5: Password
        # 6: IMAP Host, 7: IMAP Port, 8: IMAP Security
        # 9: SMTP Host, 10: SMTP Port, 11: SMTP Security
        # 12: Sieve Host, 13: Sieve Port, 14: CardDAV URL, 15: CardDAV User, 16: CardDAV Password
        # 17: Signature
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
        self.input_sieve_host.value = acc.sieve_host or acc.imap_host
        self.input_sieve_port.value = str(acc.sieve_port or 4190)
        self.input_carddav_url.value = acc.carddav_url
        self.input_carddav_username.value = acc.carddav_username or ""
        self.input_carddav_password.value = acc.carddav_password or ""
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
            12: self.input_sieve_host,
            13: self.input_sieve_port,
            14: self.input_carddav_url,
            15: self.input_carddav_username,
            16: self.input_carddav_password,
            17: self.input_signature,
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
            sieve_port = int(self.input_sieve_port.value.strip() or "4190")
            if not (1 <= imap_port <= 65535 and 1 <= smtp_port <= 65535 and 1 <= sieve_port <= 65535):
                raise ValueError
        except ValueError:
            return None, "IMAP, SMTP, and Sieve ports must be valid port numbers (1–65535)."

        username = self.input_username.value.strip() or email_val
        password = self.input_password.value
        if not password:
            return None, "Password cannot be empty."

        carddav_url = self.input_carddav_url.value.strip()
        if carddav_url and not carddav_url.startswith(("http://", "https://")):
            return None, "CardDAV URL must start with http:// or https://"
        carddav_username = self.input_carddav_username.value.strip() or acc.carddav_username or username
        carddav_password = self.input_carddav_password.value or acc.carddav_password or password

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
            sieve_host=self.input_sieve_host.value.strip() or imap_host,
            sieve_port=sieve_port,
            pgp_key_id=acc.pgp_key_id,
            signature=self.input_signature.value,
            color_badge=acc.color_badge,
            enabled=acc.enabled,
            carddav_url=carddav_url,
            carddav_username=carddav_username,
            carddav_password=carddav_password,
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
                mode = EchoMode.NORMAL if self.show_password else EchoMode.PASSWORD
                self.input_password.echo_mode = mode
                self.input_carddav_password.echo_mode = mode
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

            if k in ("ctrl+s", "ctrl+enter") or (k == "enter" and self.focus_idx != 17):
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
                self.focus_idx = (self.focus_idx + 1) % 18
                self._sync_focus()
                return self, None
            if k in ("shift+tab", "up"):
                self.focus_idx = (self.focus_idx - 1) % 18
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
            12: self.input_sieve_host,
            13: self.input_sieve_port,
            14: self.input_carddav_url,
            15: self.input_carddav_username,
            16: self.input_carddav_password,
            17: self.input_signature,
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
            self.input_sieve_host.view(),
            self.input_sieve_port.view(),
            self.input_carddav_url.view(),
            self.input_carddav_username.view(),
            f"{self.input_carddav_password.view()}   {Style().foreground(self.theme.fg_muted).render(f'[{pwd_mode}]')}",
            "",
            Style().bold(True).foreground(
                self.theme.accent_primary if self.focus_idx == 17 else self.theme.accent_secondary
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
            f"  {k_style.render('1/2/3')} Calendar RSVP (Accept/Tentative/Decline)",
            f"  {k_style.render('w')}  Save attachments      {k_style.render('M')}      Fetch older messages",
            f"  {k_style.render('S')}  Sync all accounts     {k_style.render('X')}      Empty Trash",
            "",
            h_style.render("Accounts, Search, Security, Filters & Settings"),
            f"  {k_style.render('A')}      Edit Account Settings (Password, IMAP/SMTP hosts, ports, TLS)",
            f"  {k_style.render('O')}      Add New Account (Onboarding Wizard)",
            f"  {k_style.render('p')}      General Preferences (Downloads folder, image renderer, light/dark mode)",
            f"  {k_style.render('/')}      Instant Fuzzy Search across local SQLite store",
            f"  {k_style.render('t')}      Toggle Conversation Threading view",
            f"  {k_style.render('i')}      Cycle Terminal Image Renderer (Chafa / Sixel / Kitty / Half-Block)",
            f"  {k_style.render('B')}      Local Address Book    {k_style.render('F')}  ManageSieve Filters",
            f"  {k_style.render('G')}      GPG/PGP Keyring       {k_style.render('Ctrl+P')} Command Palette",
            f"  {k_style.render('q')}      Quit EspressoMail",
        ]
        return self.theme.modal_style(modal_w, "⌨️ EspressoMail Keyboard & Mouse Shortcuts").render(
            "\n".join(lines)
        )


# ---------------------------------------------------------------------------
# 7b. Confirm Exit Modal Dialog
# ---------------------------------------------------------------------------


class ConfirmExitModal(Model):
    """Interactive modal asking for confirmation before exiting EspressoMail."""

    def __init__(self, theme: Theme) -> None:
        self.theme = theme

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[ConfirmExitModal, Cmd | None]:
        if isinstance(msg, KeyMsg):
            k = str(msg.key).lower()
            if k in ("y", "enter"):
                from espresso import quit_app
                return self, quit_app
            if k in ("n", "esc", "q"):
                return self, lambda: ModalClosedMsg("Exit cancelled.")
        return self, None

    def view(self, width: int = 80) -> str:
        modal_w = min(56, max(42, width - 10))
        msg_line = (
            Style()
            .bold(True)
            .foreground(self.theme.fg_primary)
            .render("Are you sure you want to exit EspressoMail?")
        )
        actions_line = (
            Style().bold(True).foreground(self.theme.accent_primary).render("[y / Enter]")
            + Style().foreground(self.theme.fg_secondary).render(" Exit    ")
            + Style().bold(True).foreground(self.theme.accent_secondary).render("[n / Esc]")
            + Style().foreground(self.theme.fg_secondary).render(" Stay in App")
        )
        content = join_vertical(Align.CENTER, msg_line, "", actions_line)
        return self.theme.modal_style(modal_w, "🚪 Confirm Exit").render(content)


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
        self.input_dir = TextInput(
            placeholder="Download directory path...",
            prompt="Target Folder: ",
            **self.theme.text_input_styles(),
        )
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
    THEME_FAMILIES: tuple[str, ...] = ("espresso", "catppuccin", "nord", "gruvbox", "dracula", "solarized")
    THEMES: tuple[str, ...] = ("espresso", "catppuccin", "nord", "gruvbox", "dracula", "solarized")
    COLOR_MODES: tuple[str, ...] = ("dark", "light")
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
        self.focus_idx = 0  # 0=download_dir, 1=image_backend, 2=theme, 3=color_mode, 4=threading, 5=raw_headers, 6=sync_startup, 7=remote_imgs, 8=count_mode, 9=batch_size, 10=buttons
        self.status_msg = ""
        self.error_msg = ""

        # Layout dimensions for mouse hit-testing
        self.last_app_w = 80
        self.last_app_h = 24
        self.last_modal_w = 68
        self.last_modal_h = 18
        self.last_pos_x = 6
        self.last_pos_y = 3
        self.buttons_row_offset = 11

        # Settings
        self.input_download_dir = TextInput(
            placeholder="Download folder path...",
            prompt="Download Folder:     ",
            **self.current_theme.text_input_styles(),
        )
        self.input_download_dir.value = str(download_dir)

        # Detect initial theme family and color mode
        cur_theme_id = current_theme.id.lower()
        cur_mode = self.store.get_setting("color_mode", "")
        if not cur_mode:
            cur_mode = "light" if "_light" in cur_theme_id or cur_theme_id == "light" else "dark"
        self.color_mode_idx = 1 if cur_mode.lower() == "light" else 0

        fam = cur_theme_id.replace("_light", "").replace("_dark", "")
        if fam not in THEME_FAMILIES:
            fam = "solarized" if fam == "light" else "espresso"
        self.theme_idx = THEME_FAMILIES.index(fam)

        backend_clean = current_image_backend.lower()
        self.backend_idx = (
            self.IMAGE_BACKENDS.index(backend_clean)
            if backend_clean in self.IMAGE_BACKENDS
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

    def _get_active_theme_key(self) -> str:
        fam = THEME_FAMILIES[self.theme_idx]
        mode = COLOR_MODES[self.color_mode_idx]
        return f"{fam}_{mode}" if mode == "light" else fam

    def _apply_theme_to_inputs(self) -> None:
        target_key = self._get_active_theme_key()
        self.current_theme = THEMES.get(target_key, THEMES.get(THEME_FAMILIES[self.theme_idx], self.current_theme))
        styles = self.current_theme.text_input_styles()
        self.input_download_dir.style = styles["style"]
        self.input_download_dir.placeholder_style = styles["placeholder_style"]
        self.input_download_dir.prompt_style = styles["prompt_style"]

    def _sync_focus(self) -> None:
        if self.focus_idx == 0:
            self.input_download_dir.focus()
        else:
            self.input_download_dir.blur()

    def _cycle_row(self, row_idx: int, delta: int = 1) -> None:
        """Cycle or toggle the setting on a specific row."""
        if row_idx == 0:
            self.focus_idx = 0
            self._sync_focus()
        elif row_idx == 1:
            self.backend_idx = (self.backend_idx + delta) % len(self.IMAGE_BACKENDS)
        elif row_idx == 2:
            self.theme_idx = (self.theme_idx + delta) % len(self.THEME_FAMILIES)
            self._apply_theme_to_inputs()
        elif row_idx == 3:
            self.color_mode_idx = (self.color_mode_idx + delta) % len(self.COLOR_MODES)
            self._apply_theme_to_inputs()
        elif row_idx == 4:
            self.threading_enabled = not self.threading_enabled
        elif row_idx == 5:
            self.show_raw_headers = not self.show_raw_headers
        elif row_idx == 6:
            self.sync_on_startup = not self.sync_on_startup
        elif row_idx == 7:
            self.allow_remote_images = not self.allow_remote_images
        elif row_idx == 8:
            self.count_mode_idx = (self.count_mode_idx + delta) % len(self.FOLDER_COUNT_MODES)
        elif row_idx == 9:
            self.batch_idx = (self.batch_idx + delta) % len(self.FETCH_BATCH_SIZES)

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[PreferencesModal, Cmd | None]:
        # 1. Mouse Interaction
        if isinstance(msg, MouseMsg):
            app_w = getattr(self, "last_app_w", 80)
            app_h = getattr(self, "last_app_h", 24)
            modal_w = getattr(self, "last_modal_w", min(74, max(56, app_w - 8)))
            modal_h = getattr(self, "last_modal_h", 18)
            pos_x = getattr(self, "last_pos_x", max(0, (app_w - modal_w) // 2))
            pos_y = getattr(self, "last_pos_y", max(0, (app_h - modal_h) // 2))

            # Check if mouse event is within modal bounds
            if pos_y <= msg.y < pos_y + modal_h and pos_x <= msg.x < pos_x + modal_w:
                rel_y = msg.y - pos_y
                rel_x = msg.x - pos_x
                # Content lines start at rel_y = 2 (border + padding)
                row_idx = rel_y - 2

                # Mouse Wheel cycling
                if msg.button in (MouseButton.WHEEL_UP, MouseButton.WHEEL_DOWN):
                    delta = -1 if msg.button == MouseButton.WHEEL_UP else 1
                    target_row = row_idx if 0 <= row_idx <= 9 else self.focus_idx
                    self._cycle_row(target_row, delta)
                    return self, None

                # Mouse Left Click
                if msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
                    # Check button row
                    btn_row = getattr(self, "buttons_row_offset", 11)
                    if row_idx == btn_row:
                        # Save button is on left half, Cancel on right half
                        if rel_x < modal_w // 2:
                            return self._save_preferences()
                        else:
                            return self, lambda: ModalClosedMsg("Closed preferences.")

                    # Check setting rows (0 to 9)
                    if 0 <= row_idx <= 9:
                        self.focus_idx = row_idx
                        self._sync_focus()
                        # If clicked on left arrow or left side of option value, cycle backwards; else forwards
                        delta = -1 if rel_x < 28 else 1
                        self._cycle_row(row_idx, delta)
                        return self, None

            return self, None

        # 2. Keyboard Interaction
        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if k == "esc":
                return self, lambda: ModalClosedMsg("Closed preferences.")

            if k in ("enter", "ctrl+s"):
                if self.focus_idx == 10:
                    return self._save_preferences()
                return self._save_preferences()

            if k in ("tab", "down"):
                self.focus_idx = (self.focus_idx + 1) % 11
                self._sync_focus()
                return self, None
            if k in ("shift+tab", "up"):
                self.focus_idx = (self.focus_idx - 1) % 11
                self._sync_focus()
                return self, None

            # Cycling / toggling options via arrows / space / h / l
            if 1 <= self.focus_idx <= 9 and k in ("left", "right", "h", "l", " ", "space"):
                delta = 1 if k in ("right", "l", " ", "space") else -1
                self._cycle_row(self.focus_idx, delta)
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

        fam = THEME_FAMILIES[self.theme_idx]
        mode = COLOR_MODES[self.color_mode_idx]
        theme_val = f"{fam}_{mode}" if mode == "light" else fam
        backend_val = self.IMAGE_BACKENDS[self.backend_idx]
        count_mode_val = self.FOLDER_COUNT_MODES[self.count_mode_idx]
        batch_val = self.FETCH_BATCH_SIZES[self.batch_idx]

        self.store.set_setting("download_dir", str(p))
        self.store.set_setting("image_backend", backend_val)
        self.store.set_setting("theme", theme_val)
        self.store.set_setting("color_mode", mode)
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

    def view(self, width: int = 80, height: int = 24) -> str:
        modal_w = min(74, max(56, width - 8))
        self.last_app_w = width
        self.last_app_h = height
        self.last_modal_w = modal_w

        rows: list[str] = []

        def _opt_style(idx: int, text: str) -> str:
            if idx == self.focus_idx:
                return Style().bold(True).foreground(self.current_theme.accent_primary).render(text)
            return Style().foreground(self.current_theme.fg_primary).render(text)

        # Row 0: Download folder
        rows.append(self.input_download_dir.view())

        # Row 1: Image Rendering Backend
        cur_b = self.IMAGE_BACKENDS[self.backend_idx].upper()
        rows.append(f"Image Rendering Mode: ◀ {_opt_style(1, cur_b)} ▶")

        # Row 2: Theme family
        cur_fam_label = self.THEME_FAMILIES[self.theme_idx].title()
        rows.append(f"Color Theme:          ◀ {_opt_style(2, cur_fam_label)} ▶")

        # Row 3: Color Mode
        cur_mode_str = "Dark" if self.COLOR_MODES[self.color_mode_idx] == "dark" else "Light"
        rows.append(f"Color Mode:           ◀ {_opt_style(3, cur_mode_str)} ▶")

        # Row 4: Message Threading
        th_val = "[x] Enabled" if self.threading_enabled else "[ ] Disabled"
        rows.append(f"Message Threading:    {_opt_style(4, th_val)}")

        # Row 5: Show Raw RFC Headers
        rh_val = "[x] Enabled" if self.show_raw_headers else "[ ] Disabled"
        rows.append(f"Show Raw RFC Headers: {_opt_style(5, rh_val)}")

        # Row 6: Sync On Startup
        su_val = "[x] Enabled" if self.sync_on_startup else "[ ] Disabled"
        rows.append(f"Sync On Startup:      {_opt_style(6, su_val)}")

        # Row 7: Remote Image Fetch
        ri_val = "[x] Allowed" if self.allow_remote_images else "[ ] Blocked"
        rows.append(f"Remote Image Fetch:   {_opt_style(7, ri_val)}")

        # Row 8: Folder Email Counts Mode
        cur_cm = "Unread Only" if self.FOLDER_COUNT_MODES[self.count_mode_idx] == "unread_only" else "Unread & Total"
        rows.append(f"Folder Email Counts:  ◀ {_opt_style(8, cur_cm)} ▶")

        # Row 9: Fetch Batch Size
        cur_bs = f"{self.FETCH_BATCH_SIZES[self.batch_idx]} emails"
        rows.append(f"Fetch Batch Size:     ◀ {_opt_style(9, cur_bs)} ▶")

        if self.error_msg:
            rows.append("")
            rows.append(
                Style().bold(True).foreground(self.current_theme.pgp_error_fg).render(f"⚠ {self.error_msg}")
            )

        rows.append("")
        self.buttons_row_offset = len(rows)

        # Row 10 (or buttons row)
        save_focused = (self.focus_idx == 10)
        save_btn = (
            Style().bold(True).background(self.current_theme.accent_primary).foreground(self.current_theme.bg_primary).render(" [ Save Preferences (Enter) ] ")
            if save_focused
            else Style().background(self.current_theme.bg_highlight).foreground(self.current_theme.accent_primary).render(" [ Save Preferences (Enter) ] ")
        )
        cancel_btn = Style().background(self.current_theme.bg_highlight).foreground(self.current_theme.fg_muted).render(" [ Cancel (Esc) ] ")
        rows.append(f"{save_btn}   {cancel_btn}")

        rows.append("")
        rows.append(
            Style()
            .foreground(self.current_theme.fg_muted)
            .render(
                "[Tab/Arrows] Navigate  [Space/Click] Toggle/Cycle  [Enter] Save  [Esc] Cancel"
            )
        )

        rendered = self.current_theme.modal_style(modal_w, "⚙️ General Preferences").render("\n".join(rows))
        modal_lines = rendered.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.last_modal_h = len(modal_lines)
        self.last_modal_w = max((string_width(l) for l in modal_lines), default=modal_w)
        self.last_pos_x = max(0, (width - self.last_modal_w) // 2)
        self.last_pos_y = max(0, (height - self.last_modal_h) // 2)
        return rendered


# ---------------------------------------------------------------------------
# 7. Calendar RSVP Modal
# ---------------------------------------------------------------------------


class RSVPModal(Model):
    """Interactive modal to review invitation details and send an iMIP RSVP response."""

    def __init__(
        self,
        account: AccountConfig,
        orig_message: EmailMessage,
        event: CalendarEvent,
        theme: Theme,
        initial_response: str = "ACCEPTED",
    ) -> None:
        self.account = account
        self.orig_message = orig_message
        self.event = event
        self.theme = theme

        self.selected_response = initial_response.upper()
        if self.selected_response not in ("ACCEPTED", "TENTATIVE", "DECLINED"):
            self.selected_response = "ACCEPTED"

        self.comment_input = TextInput(placeholder="Optional note / comment for organizer...")
        self.focused_field = "options"  # "options", "comment", "send_btn", "cancel_btn"

        # Hit-testing coordinates
        self.last_pos_x = 0
        self.last_pos_y = 0
        self.last_modal_w = 64
        self.last_modal_h = 16
        self.options_y_offset = 0
        self.comment_y_offset = 0
        self.buttons_y_offset = 0

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[RSVPModal, Cmd | None]:
        if isinstance(msg, KeyMsg):
            k = str(msg.key).lower()
            if k == "esc":
                return self, lambda: ModalClosedMsg("RSVP response cancelled.")

            if k == "1":
                self.selected_response = "ACCEPTED"
                return self, None
            if k == "2":
                self.selected_response = "TENTATIVE"
                return self, None
            if k == "3":
                self.selected_response = "DECLINED"
                return self, None

            if k in ("tab", "down"):
                order = ["options", "comment", "send_btn", "cancel_btn"]
                idx = order.index(self.focused_field)
                self.focused_field = order[(idx + 1) % len(order)]
                if self.focused_field == "comment":
                    self.comment_input.focus()
                else:
                    self.comment_input.blur()
                return self, None

            if k in ("shift+tab", "up"):
                order = ["options", "comment", "send_btn", "cancel_btn"]
                idx = order.index(self.focused_field)
                self.focused_field = order[(idx - 1) % len(order)]
                if self.focused_field == "comment":
                    self.comment_input.focus()
                else:
                    self.comment_input.blur()
                return self, None

            if self.focused_field == "options":
                if k in ("left", "h"):
                    opts = ["ACCEPTED", "TENTATIVE", "DECLINED"]
                    idx = opts.index(self.selected_response)
                    self.selected_response = opts[max(0, idx - 1)]
                    return self, None
                if k in ("right", "l"):
                    opts = ["ACCEPTED", "TENTATIVE", "DECLINED"]
                    idx = opts.index(self.selected_response)
                    self.selected_response = opts[min(len(opts) - 1, idx + 1)]
                    return self, None
                if k in ("enter", " "):
                    return self._submit()

            elif self.focused_field == "comment":
                if k == "enter":
                    return self._submit()
                self.comment_input, cmd = self.comment_input.update(msg)
                return self, cmd

            elif self.focused_field == "send_btn":
                if k in ("enter", " "):
                    return self._submit()

            elif self.focused_field == "cancel_btn":
                if k in ("enter", " "):
                    return self, lambda: ModalClosedMsg("RSVP response cancelled.")

        if isinstance(msg, MouseMsg) and msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
            rel_x = msg.x - self.last_pos_x
            rel_y = msg.y - self.last_pos_y
            if 0 <= rel_x < self.last_modal_w and 0 <= rel_y < self.last_modal_h:
                line_idx = rel_y - 1
                if line_idx == self.options_y_offset:
                    self.focused_field = "options"
                    self.comment_input.blur()
                    if rel_x < 20:
                        self.selected_response = "ACCEPTED"
                    elif rel_x < 36:
                        self.selected_response = "TENTATIVE"
                    else:
                        self.selected_response = "DECLINED"
                    return self, None
                elif line_idx == self.comment_y_offset:
                    self.focused_field = "comment"
                    self.comment_input.focus()
                    return self, None
                elif line_idx == self.buttons_y_offset:
                    if rel_x < 30:
                        self.focused_field = "send_btn"
                        return self._submit()
                    else:
                        return self, lambda: ModalClosedMsg("RSVP response cancelled.")

        return self, None

    def _submit(self) -> tuple[RSVPModal, Cmd]:
        cmd = lambda: RSVPSubmittedMsg(
            account=self.account,
            orig_message=self.orig_message,
            event=self.event,
            partstat=self.selected_response,
            comment=self.comment_input.value.strip(),
        )
        return self, cmd

    def view(self, width: int = 80, height: int = 24) -> str:
        modal_w = min(68, max(48, width - 8))
        lbl_style = Style().bold(True).foreground(self.theme.accent_primary)
        val_style = Style().foreground(self.theme.fg_primary)
        muted_style = Style().foreground(self.theme.fg_muted)

        rows: list[str] = [
            f"{lbl_style.render('Event:       ')} {val_style.render(truncate_ansi(self.event.summary, modal_w - 18))}",
            f"{lbl_style.render('When:        ')} {val_style.render(self.event.formatted_time_range())}",
        ]
        if self.event.location:
            rows.append(f"{lbl_style.render('Where:       ')} {val_style.render(truncate_ansi(self.event.location, modal_w - 18))}")
        if self.event.organizer_display:
            rows.append(f"{lbl_style.render('Organizer:   ')} {muted_style.render(truncate_ansi(self.event.organizer_display, modal_w - 18))}")

        rows.append("")
        rows.append(lbl_style.render("Your Response:"))
        self.options_y_offset = len(rows)

        # Radio options
        def _fmt_opt(key: str, label: str, shortcut: str, active_color: str) -> str:
            selected = self.selected_response == key
            marker = "●" if selected else "○"
            box = f"[{marker}] [{shortcut}] {label}"
            if selected:
                return Style().bold(True).foreground(active_color).render(box)
            return Style().foreground(self.theme.fg_muted).render(box)

        opt_accept = _fmt_opt("ACCEPTED", "Accept", "1", self.theme.unread_fg)
        opt_tentative = _fmt_opt("TENTATIVE", "Tentative", "2", self.theme.starred_fg)
        opt_decline = _fmt_opt("DECLINED", "Decline", "3", self.theme.pgp_error_fg)

        options_focus_highlight = " ◄" if self.focused_field == "options" else ""
        rows.append(f"  {opt_accept}    {opt_tentative}    {opt_decline}{options_focus_highlight}")

        rows.append("")
        comment_focus_str = " (focused)" if self.focused_field == "comment" else ""
        rows.append(lbl_style.render(f"Optional Note / Comment{comment_focus_str}:"))
        self.comment_y_offset = len(rows)
        comment_box = self.comment_input.view()
        rows.append(f"  {comment_box}")

        rows.append("")
        self.buttons_y_offset = len(rows)
        send_focus = self.focused_field == "send_btn"
        cancel_focus = self.focused_field == "cancel_btn"

        send_btn = (
            Style().bold(True).background(self.theme.accent_primary).foreground(self.theme.bg_primary).render(" [ Send Response (Enter) ] ")
            if send_focus
            else Style().background(self.theme.bg_highlight).foreground(self.theme.accent_primary).render(" [ Send Response (Enter) ] ")
        )
        cancel_btn = (
            Style().bold(True).background(self.theme.bg_highlight).foreground(self.theme.fg_primary).render(" [ Cancel (Esc) ] ")
            if cancel_focus
            else Style().background(self.theme.bg_highlight).foreground(self.theme.fg_muted).render(" [ Cancel (Esc) ] ")
        )
        rows.append(f"  {send_btn}   {cancel_btn}")

        rows.append("")
        rows.append(
            muted_style.render(
                "  [1/2/3] Quick response  [Tab/Arrows] Navigate  [Enter] Send  [Esc] Cancel"
            )
        )

        rendered = self.theme.modal_style(modal_w, "📅 RSVP to Calendar Invitation").render("\n".join(rows))
        modal_lines = rendered.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.last_modal_h = len(modal_lines)
        self.last_modal_w = max((string_width(l) for l in modal_lines), default=modal_w)
        self.last_pos_x = max(0, (width - self.last_modal_w) // 2)
        self.last_pos_y = max(0, (height - self.last_modal_h) // 2)
        return rendered



