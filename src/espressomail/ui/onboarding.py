"""Guided, interactive account configuration wizard for EspressoMail."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from espresso import Cmd, KeyMsg, Model, MouseAction, MouseButton, MouseMsg, Msg
from espresso.beans import EchoMode, SelectPrompt, SelectSubmitMsg, TextInput
from espresso.crema import DOUBLE_BORDER, Align, Style, join_vertical, place

from espressomail.models.entities import AccountConfig, SecurityType
from espressomail.protocols.sieve import ManageSieveClient
from espressomail.protocols.smtp_client import validate_account_credentials
from espressomail.themes.engine import Theme

SECURITY_CYCLE: tuple[SecurityType, ...] = (
    SecurityType.SSL_TLS,
    SecurityType.STARTTLS,
    SecurityType.NONE,
)


@dataclass(frozen=True)
class ProviderPreset:
    """Pre-configured IMAP, SMTP, and ManageSieve endpoints for email providers."""

    label: str
    imap_host: str
    imap_port: int
    imap_security: SecurityType
    smtp_host: str
    smtp_port: int
    smtp_security: SecurityType
    sieve_port: int = 4190
    is_custom: bool = False


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        label="Custom IMAP / SMTP Server",
        imap_host="",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        smtp_host="",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
        is_custom=True,
    ),
    ProviderPreset(
        label="Fastmail (IMAP SSL + SMTP STARTTLS + ManageSieve)",
        imap_host="imap.fastmail.com",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        smtp_host="smtp.fastmail.com",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
    ),
    ProviderPreset(
        label="Gmail / Google Workspace (App Password)",
        imap_host="imap.gmail.com",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
    ),
    ProviderPreset(
        label="Outlook / Microsoft 365",
        imap_host="outlook.office365.com",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        smtp_host="smtp.office365.com",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
    ),
    ProviderPreset(
        label="ProtonMail Bridge (Localhost 127.0.0.1)",
        imap_host="127.0.0.1",
        imap_port=1143,
        imap_security=SecurityType.STARTTLS,
        smtp_host="127.0.0.1",
        smtp_port=1025,
        smtp_security=SecurityType.STARTTLS,
    ),
    ProviderPreset(
        label="Apple iCloud Mail",
        imap_host="imap.mail.me.com",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        smtp_host="smtp.mail.me.com",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
    ),
)


@dataclass(frozen=True)
class OnboardingCompleteMsg(Msg):
    """Emitted when the onboarding wizard finishes validating and creating an account, or is cancelled."""

    account: AccountConfig | None
    generate_pgp: bool = True
    cancelled: bool = False
    validation_summary: str = ""


class OnboardingWizard(Model):
    """Guided 3-step interactive onboarding flow with live IMAP/SMTP credential validation."""

    def __init__(
        self,
        theme: Theme,
        allow_cancel: bool = False,
        credential_validator: Callable[[AccountConfig], tuple[bool, str]] | None = None,
        store: Any | None = None,
    ) -> None:
        self.theme = theme
        self.allow_cancel = allow_cancel
        self.store = store
        self.credential_validator: Callable[[AccountConfig], tuple[bool, str]] = (
            credential_validator
            or (lambda a: validate_account_credentials(a, store=self.store))
        )
        self.step = 1  # 1: Provider Preset, 2: Identity & Credentials, 3: Server Endpoints & Validation
        self.selected_preset: ProviderPreset = PROVIDER_PRESETS[0]
        self.error_msg = ""
        self.status_msg = ""
        self.generate_pgp = True
        self.show_password = False

        self.provider_prompt = SelectPrompt(
            question="Select your email server type or provider preset:",
            options=[p.label for p in PROVIDER_PRESETS],
            default_index=0,
        )

        styles = self.theme.text_input_styles()
        # Step 2 inputs
        self.input_account_name = TextInput(placeholder="Work / Personal", prompt="Account Name:  ", **styles)
        self.input_full_name = TextInput(placeholder="Jane Doe", prompt="Full Name:     ", **styles)
        self.input_email = TextInput(placeholder="jane@example.com", prompt="Email Address: ", **styles)
        self.input_username = TextInput(
            placeholder="(Optional — defaults to Email Address)",
            prompt="Login User:    ",
            **styles,
        )
        self.input_password = TextInput(
            placeholder="IMAP / SMTP Password or App Password",
            prompt="Password:      ",
            echo_mode=EchoMode.PASSWORD,
            **styles,
        )
        self.step2_focus = 0

        # Step 3 inputs & security selectors
        self.input_imap_host = TextInput(placeholder="imap.example.com", prompt="IMAP Host:     ", **styles)
        self.input_imap_port = TextInput(placeholder="993", prompt="IMAP Port:     ", **styles)
        self.imap_security: SecurityType = SecurityType.SSL_TLS
        self.input_smtp_host = TextInput(placeholder="smtp.example.com", prompt="SMTP Host:     ", **styles)
        self.input_smtp_port = TextInput(placeholder="587", prompt="SMTP Port:     ", **styles)
        self.smtp_security: SecurityType = SecurityType.STARTTLS
        self.input_sieve_host = TextInput(placeholder="sieve.example.com", prompt="Sieve Host:    ", **styles)
        self.input_sieve_port = TextInput(placeholder="4190", prompt="Sieve Port:    ", **styles)
        # Focus indices on Step 3:
        # 0=IMAP Host, 1=IMAP Port, 2=IMAP Security, 3=SMTP Host, 4=SMTP Port, 5=SMTP Security, 6=Sieve Host, 7=Sieve Port
        self.step3_focus = 0

        self._sync_focus()

    def _sync_focus(self) -> None:
        s2_fields = [
            self.input_account_name,
            self.input_full_name,
            self.input_email,
            self.input_username,
            self.input_password,
        ]
        for idx, f in enumerate(s2_fields):
            if self.step == 2 and idx == self.step2_focus:
                f.focus()
            else:
                f.blur()

        s3_input_map = {
            0: self.input_imap_host,
            1: self.input_imap_port,
            3: self.input_smtp_host,
            4: self.input_smtp_port,
            6: self.input_sieve_host,
            7: self.input_sieve_port,
        }
        for idx, f in s3_input_map.items():
            if self.step == 3 and idx == self.step3_focus:
                f.focus()
            else:
                f.blur()

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[OnboardingWizard, Cmd | None]:
        # 1. Mouse Interaction
        if isinstance(msg, MouseMsg):
            card_w = min(88, max(64, getattr(self, "last_width", 90) - 8))
            card_h = getattr(self, "last_height", 24)
            pos_x = max(0, (getattr(self, "last_width", 100) - card_w) // 2)
            pos_y = max(0, (getattr(self, "last_height", 30) - card_h) // 2)

            if pos_y <= msg.y < pos_y + card_h and pos_x <= msg.x < pos_x + card_w:
                rel_y = msg.y - pos_y

                if self.step == 1:
                    if msg.button in (MouseButton.WHEEL_UP, MouseButton.WHEEL_DOWN):
                        delta = -1 if msg.button == MouseButton.WHEEL_UP else 1
                        self.provider_prompt.cursor = max(
                            0, min(len(PROVIDER_PRESETS) - 1, self.provider_prompt.cursor + delta)
                        )
                        return self, None
                    if msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
                        # options start at rel_y = 6 (header, spacing, question line)
                        opt_idx = rel_y - 6
                        if 0 <= opt_idx < len(PROVIDER_PRESETS):
                            self.provider_prompt.cursor = opt_idx
                            return self._handle_preset_selected(opt_idx)

                elif self.step == 2:
                    if msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
                        field_idx = rel_y - 5
                        if 0 <= field_idx <= 4:
                            self.step2_focus = field_idx
                            self._sync_focus()
                            return self, None

                elif self.step == 3:
                    if msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
                        row_idx = rel_y - 5
                        if 0 <= row_idx <= 8:
                            self.step3_focus = row_idx
                            self._sync_focus()
                            if row_idx == 2:
                                cur_idx = SECURITY_CYCLE.index(self.imap_security)
                                self.imap_security = SECURITY_CYCLE[(cur_idx + 1) % len(SECURITY_CYCLE)]
                            elif row_idx == 5:
                                cur_idx = SECURITY_CYCLE.index(self.smtp_security)
                                self.smtp_security = SECURITY_CYCLE[(cur_idx + 1) % len(SECURITY_CYCLE)]
                            return self, None
                        elif row_idx >= 9:
                            return self._finalize_account()

            return self, None

        # 2. Keyboard Interaction
        if isinstance(msg, KeyMsg) and str(msg.key) == "esc":
            if self.step > 1:
                self.step -= 1
                self.error_msg = ""
                self.status_msg = ""
                if self.step == 1:
                    # Reset provider_prompt so all options are visible and navigable again
                    curr_idx = self.provider_prompt.cursor
                    self.provider_prompt = SelectPrompt(
                        question="Select your email server type or provider preset:",
                        options=[p.label for p in PROVIDER_PRESETS],
                        default_index=curr_idx,
                    )
                self._sync_focus()
                return self, None
            if self.allow_cancel:
                return self, lambda: OnboardingCompleteMsg(account=None, cancelled=True)

        if self.step == 1:
            self.provider_prompt, cmd = self.provider_prompt.update(msg)
            if cmd is not None:
                res = cmd() if callable(cmd) else None
                if isinstance(res, SelectSubmitMsg):
                    return self._handle_preset_selected(res.index)
            if isinstance(msg, SelectSubmitMsg):
                return self._handle_preset_selected(msg.index)
            return self, cmd

        if self.step == 2:
            s2_fields = [
                self.input_account_name,
                self.input_full_name,
                self.input_email,
                self.input_username,
                self.input_password,
            ]
            if isinstance(msg, KeyMsg):
                k = str(msg.key)
                if k == "ctrl+r":
                    self.show_password = not self.show_password
                    self.input_password.echo_mode = (
                        EchoMode.NORMAL if self.show_password else EchoMode.PASSWORD
                    )
                    return self, None
                if k in ("tab", "down"):
                    self.step2_focus = (self.step2_focus + 1) % len(s2_fields)
                    self._sync_focus()
                    return self, None
                if k in ("shift+tab", "up"):
                    self.step2_focus = (self.step2_focus - 1) % len(s2_fields)
                    self._sync_focus()
                    return self, None
                if k == "enter":
                    if self.step2_focus < len(s2_fields) - 1:
                        self.step2_focus += 1
                        self._sync_focus()
                        return self, None
                    return self._advance_to_step3()

            active_input = s2_fields[self.step2_focus]
            s2_fields[self.step2_focus], cmd = active_input.update(msg)
            return self, cmd

        if self.step == 3:
            if isinstance(msg, KeyMsg):
                k = str(msg.key)
                if k == "ctrl+g":
                    self.generate_pgp = not self.generate_pgp
                    return self, None
                if k in ("tab", "down"):
                    self.step3_focus = (self.step3_focus + 1) % 8
                    self._sync_focus()
                    return self, None
                if k in ("shift+tab", "up"):
                    self.step3_focus = (self.step3_focus - 1) % 8
                    self._sync_focus()
                    return self, None
                if self.step3_focus in (2, 5) and k in ("left", "right", "h", "l", " ", "space"):
                    delta = 1 if k in ("right", "l", " ", "space") else -1
                    if self.step3_focus == 2:
                        cur_idx = SECURITY_CYCLE.index(self.imap_security)
                        self.imap_security = SECURITY_CYCLE[(cur_idx + delta) % len(SECURITY_CYCLE)]
                        if self.imap_security == SecurityType.SSL_TLS and self.input_imap_port.value == "143":
                            self.input_imap_port.value = "993"
                        elif self.imap_security != SecurityType.SSL_TLS and self.input_imap_port.value == "993":
                            self.input_imap_port.value = "143"
                    else:
                        cur_idx = SECURITY_CYCLE.index(self.smtp_security)
                        self.smtp_security = SECURITY_CYCLE[(cur_idx + delta) % len(SECURITY_CYCLE)]
                        if self.smtp_security == SecurityType.SSL_TLS and self.input_smtp_port.value == "587":
                            self.input_smtp_port.value = "465"
                        elif self.smtp_security != SecurityType.SSL_TLS and self.input_smtp_port.value == "465":
                            self.input_smtp_port.value = "587"
                    return self, None
                if k == "enter":
                    return self._finalize_account()

            s3_input_map = {
                0: self.input_imap_host,
                1: self.input_imap_port,
                3: self.input_smtp_host,
                4: self.input_smtp_port,
                6: self.input_sieve_host,
                7: self.input_sieve_port,
            }
            if self.step3_focus in s3_input_map:
                target = s3_input_map[self.step3_focus]
                _, cmd = target.update(msg)
                return self, cmd

        return self, None

    def _handle_preset_selected(self, idx: int) -> tuple[OnboardingWizard, Cmd | None]:
        preset = PROVIDER_PRESETS[idx % len(PROVIDER_PRESETS)]
        self.selected_preset = preset
        self.input_imap_host.value = preset.imap_host
        self.input_imap_port.value = str(preset.imap_port)
        self.imap_security = preset.imap_security
        self.input_smtp_host.value = preset.smtp_host
        self.input_smtp_port.value = str(preset.smtp_port)
        self.smtp_security = preset.smtp_security
        self.input_sieve_host.value = preset.imap_host
        self.input_sieve_port.value = str(preset.sieve_port)
        if not preset.is_custom and not self.input_account_name.value:
            self.input_account_name.value = preset.label.split("(")[0].strip()[:20]
        self.step = 2
        self.step2_focus = 0
        self.error_msg = ""
        self.status_msg = ""
        self._sync_focus()
        return self, None

    def _advance_to_step3(self) -> tuple[OnboardingWizard, Cmd | None]:
        email_val = self.input_email.value.strip()
        if not email_val or "@" not in email_val:
            self.error_msg = "Please enter a valid email address containing '@'."
            return self, None
        if not self.input_password.value:
            self.error_msg = "Password is required to authenticate with the mail server."
            return self, None

        domain = email_val.split("@")[-1]
        if not self.input_full_name.value.strip():
            self.input_full_name.value = email_val.split("@")[0].replace(".", " ").title()
        if not self.input_account_name.value.strip():
            self.input_account_name.value = domain
        if not self.input_username.value.strip():
            self.input_username.value = email_val

        # If Custom IMAP/SMTP was selected and hosts are still blank, pre-fill sensible domain defaults
        if not self.input_imap_host.value.strip():
            self.input_imap_host.value = f"imap.{domain}"
        if not self.input_smtp_host.value.strip():
            self.input_smtp_host.value = f"smtp.{domain}"
        if not self.input_sieve_host.value.strip():
            self.input_sieve_host.value = f"sieve.{domain}"
        if not self.input_sieve_port.value.strip():
            self.input_sieve_port.value = "4190"

        self.step = 3
        self.step3_focus = 0
        self.error_msg = ""
        self.status_msg = ""
        self._sync_focus()
        return self, None

    def _finalize_account(self) -> tuple[OnboardingWizard, Cmd | None]:
        imap_host = self.input_imap_host.value.strip()
        smtp_host = self.input_smtp_host.value.strip()
        if not imap_host or not smtp_host:
            self.error_msg = "Both IMAP Host and SMTP Host are required."
            return self, None
        try:
            imap_port = int(self.input_imap_port.value.strip() or "993")
            smtp_port = int(self.input_smtp_port.value.strip() or "587")
            if not (1 <= imap_port <= 65535 and 1 <= smtp_port <= 65535):
                raise ValueError
        except ValueError:
            self.error_msg = "IMAP and SMTP ports must be valid port numbers (1–65535)."
            return self, None

        sieve_host = self.input_sieve_host.value.strip()
        sieve_port_str = self.input_sieve_port.value.strip() or "4190"
        try:
            sieve_port = int(sieve_port_str)
            if not (1 <= sieve_port <= 65535):
                raise ValueError
        except ValueError:
            self.error_msg = "Sieve port must be a valid port number (1–65535)."
            return self, None

        email_addr = self.input_email.value.strip()
        username = self.input_username.value.strip() or email_addr
        password = self.input_password.value
        if not password:
            self.error_msg = "Password is required to validate account credentials."
            return self, None

        acc = AccountConfig(
            id=f"acc_{uuid.uuid4().hex[:8]}",
            name=self.input_account_name.value.strip() or email_addr,
            email=email_addr,
            full_name=self.input_full_name.value.strip() or email_addr,
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
            sieve_host=sieve_host,
            sieve_port=sieve_port,
        )

        # Validate IMAP and SMTP credentials before completing onboarding
        ok, summary = self.credential_validator(acc)
        if not ok:
            self.error_msg = summary
            self.status_msg = ""
            return self, None

        sieve_note = ""
        if acc.sieve_host and self.store is not None:
            try:
                client = ManageSieveClient(acc, timeout=3.0)
                client.connect()
                s_ok, s_msg = client.sync_script_with_server(self.store)
                client.close()
                sieve_note = f" Sieve: {s_msg}"
            except Exception as e:
                sieve_note = f" (Sieve notice: {e})"

        self.error_msg = ""
        self.status_msg = f"{summary}{sieve_note}"
        return self, lambda: OnboardingCompleteMsg(
            account=acc,
            generate_pgp=self.generate_pgp,
            validation_summary=self.status_msg,
        )

    def view(self, width: int = 100, height: int = 30) -> str:
        """Render the guided onboarding wizard card."""
        self.last_width = width
        self.last_height = height
        step_badges = []
        for idx, title in enumerate(
            ("1. Server Type", "2. Identity & Auth", "3. Endpoints & Verify"), start=1
        ):
            if idx == self.step:
                step_badges.append(
                    Style()
                    .bold(True)
                    .foreground(self.theme.bg_primary)
                    .background(self.theme.accent_primary)
                    .render(f" {title} ")
                )
            elif idx < self.step:
                step_badges.append(
                    Style().foreground(self.theme.pgp_verified_fg).render(f" ✔ {title} ")
                )
            else:
                step_badges.append(Style().foreground(self.theme.fg_muted).render(f" {title} "))

        header_line = "  →  ".join(step_badges)

        if self.step == 1:
            body = join_vertical(
                Align.LEFT,
                Style()
                .foreground(self.theme.fg_secondary)
                .render("Welcome to EspressoMail! Configure your IMAP & SMTP email account:"),
                "",
                self.provider_prompt.view(),
                "",
                Style()
                .foreground(self.theme.fg_muted)
                .render(
                    "[↑/↓] Navigate   [Enter] Select Option"
                    + ("   [Esc] Cancel" if self.allow_cancel else "")
                ),
            )
        elif self.step == 2:
            pwd_hint = "Plaintext (Ctrl+R to mask)" if self.show_password else "Masked (Ctrl+R to reveal)"
            body = join_vertical(
                Align.LEFT,
                Style()
                .bold(True)
                .foreground(self.theme.accent_secondary)
                .render(f"Selected: {self.selected_preset.label}"),
                "",
                self.input_account_name.view(),
                self.input_full_name.view(),
                self.input_email.view(),
                self.input_username.view(),
                self.input_password.view(),
                Style().foreground(self.theme.fg_muted).render(f"  Password mode: {pwd_hint}"),
                "",
                Style()
                .foreground(self.theme.fg_muted)
                .render("[Tab/↑/↓] Next/Prev Field   [Ctrl+R] Show/Hide Password   [Enter] Next   [Esc] Back"),
            )
        else:
            imap_sec_style = (
                Style().bold(True).foreground(self.theme.accent_primary)
                if self.step3_focus == 2
                else Style().foreground(self.theme.fg_primary)
            )
            smtp_sec_style = (
                Style().bold(True).foreground(self.theme.accent_primary)
                if self.step3_focus == 5
                else Style().foreground(self.theme.fg_primary)
            )
            imap_sec_row = f"IMAP Security: ◀ {imap_sec_style.render(self.imap_security.value.upper())} ▶"
            smtp_sec_row = f"SMTP Security: ◀ {smtp_sec_style.render(self.smtp_security.value.upper())} ▶"
            pgp_mark = "[x]" if self.generate_pgp else "[ ]"
            body = join_vertical(
                Align.LEFT,
                Style()
                .bold(True)
                .foreground(self.theme.accent_secondary)
                .render(f"Endpoints & TLS for {self.input_email.value}:"),
                "",
                self.input_imap_host.view(),
                self.input_imap_port.view(),
                imap_sec_row,
                self.input_smtp_host.view(),
                self.input_smtp_port.view(),
                smtp_sec_row,
                self.input_sieve_host.view(),
                self.input_sieve_port.view(),
                "",
                Style()
                .foreground(self.theme.pgp_verified_fg if self.generate_pgp else self.theme.fg_muted)
                .render(f"{pgp_mark} Auto-generate Ed25519 OpenPGP Key Pair for this account (Ctrl+G to toggle)"),
                "",
                Style()
                .foreground(self.theme.fg_muted)
                .render(
                    "[Tab/↑/↓] Field   [Space/←/→] Cycle TLS   [Ctrl+G] PGP   [Enter] Validate & Finish   [Esc] Back"
                ),
            )

        err_block = (
            "\n" + Style().bold(True).foreground(self.theme.pgp_error_fg).render(f"⚠ {self.error_msg}")
            if self.error_msg
            else ""
        )
        ok_block = (
            "\n" + Style().bold(True).foreground(self.theme.pgp_verified_fg).render(f"✔ {self.status_msg}")
            if self.status_msg
            else ""
        )

        card_content = f"{header_line}\n\n{body}{err_block}{ok_block}"
        card = (
            Style()
            .border(DOUBLE_BORDER)
            .border_foreground(self.theme.accent_primary)
            .border_background(self.theme.bg_secondary)
            .background(self.theme.bg_secondary)
            .foreground(self.theme.fg_primary)
            .border_title(
                Style()
                .bold(True)
                .foreground(self.theme.accent_secondary)
                .background(self.theme.bg_secondary)
                .render(" [ ☕ EspressoMail Account Onboarding ] "),
                align=Align.CENTER,
            )
            .padding(1, 3)
            .width(min(82, max(58, width - 6)))
            .render(card_content)
        )

        return place(width, height, Align.CENTER, Align.CENTER, card)
