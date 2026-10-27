"""Guided, interactive first-time account configuration wizard for EspressoMail."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from espresso import Cmd, KeyMsg, Model, Msg
from espresso.beans import EchoMode, SelectPrompt, SelectSubmitMsg, TextInput
from espresso.crema import DOUBLE_BORDER, Align, Style, join_vertical, place

from espressomail.models.entities import AccountConfig, SecurityType
from espressomail.themes.engine import Theme


@dataclass(frozen=True)
class ProviderPreset:
    """Pre-configured IMAP, SMTP, and ManageSieve endpoints for popular email providers."""

    label: str
    imap_host: str
    imap_port: int
    imap_security: SecurityType
    smtp_host: str
    smtp_port: int
    smtp_security: SecurityType
    sieve_port: int = 4190
    is_demo: bool = False


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        label="☕ Multi-Account Demo Workspace (Instant Offline Tour)",
        imap_host="imap.schulz.dk",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        smtp_host="smtp.schulz.dk",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
        is_demo=True,
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
    ProviderPreset(
        label="Custom Self-Hosted IMAP & SMTP Server (Dovecot / Postfix)",
        imap_host="mail.example.com",
        imap_port=993,
        imap_security=SecurityType.SSL_TLS,
        smtp_host="mail.example.com",
        smtp_port=587,
        smtp_security=SecurityType.STARTTLS,
    ),
)


@dataclass(frozen=True)
class OnboardingCompleteMsg(Msg):
    """Emitted when the onboarding wizard finishes or is cancelled."""

    account: AccountConfig | None
    use_demo: bool = False
    generate_pgp: bool = True
    cancelled: bool = False


class OnboardingWizard(Model):
    """Guided 3-step interactive onboarding flow for configuring email accounts."""

    def __init__(self, theme: Theme, allow_cancel: bool = False) -> None:
        self.theme = theme
        self.allow_cancel = allow_cancel
        self.step = 1  # 1: Provider Preset, 2: Identity & Credentials, 3: Server Endpoints & PGP
        self.selected_preset: ProviderPreset = PROVIDER_PRESETS[0]
        self.error_msg = ""
        self.generate_pgp = True

        self.provider_prompt = SelectPrompt(
            question="Select your email provider or launch the Demo Workspace:",
            options=[p.label for p in PROVIDER_PRESETS],
            default_index=0,
        )

        # Step 2 inputs
        self.input_account_name = TextInput(placeholder="Work / Personal", prompt="Account Name:  ")
        self.input_full_name = TextInput(placeholder="Jane Doe", prompt="Full Name:     ")
        self.input_email = TextInput(placeholder="jane@example.com", prompt="Email Address: ")
        self.input_password = TextInput(
            placeholder="App Password or IMAP Password",
            prompt="Password:      ",
            echo_mode=EchoMode.PASSWORD,
        )
        self.step2_focus = 0

        # Step 3 inputs
        self.input_imap_host = TextInput(placeholder="imap.example.com", prompt="IMAP Host:     ")
        self.input_imap_port = TextInput(placeholder="993", prompt="IMAP Port:     ")
        self.input_smtp_host = TextInput(placeholder="smtp.example.com", prompt="SMTP Host:     ")
        self.input_smtp_port = TextInput(placeholder="587", prompt="SMTP Port:     ")
        self.step3_focus = 0

        self._sync_focus()

    def _sync_focus(self) -> None:
        s2_fields = [
            self.input_account_name,
            self.input_full_name,
            self.input_email,
            self.input_password,
        ]
        for idx, f in enumerate(s2_fields):
            if self.step == 2 and idx == self.step2_focus:
                f.focus()
            else:
                f.blur()

        s3_fields = [
            self.input_imap_host,
            self.input_imap_port,
            self.input_smtp_host,
            self.input_smtp_port,
        ]
        for idx, f in enumerate(s3_fields):
            if self.step == 3 and idx == self.step3_focus:
                f.focus()
            else:
                f.blur()

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[OnboardingWizard, Cmd | None]:
        """Handle multi-step navigation, form validation, and submission."""
        if isinstance(msg, KeyMsg) and str(msg.key) == "esc":
            if self.step > 1:
                self.step -= 1
                self.error_msg = ""
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
                self.input_password,
            ]
            if isinstance(msg, KeyMsg):
                k = str(msg.key)
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
            s3_fields = [
                self.input_imap_host,
                self.input_imap_port,
                self.input_smtp_host,
                self.input_smtp_port,
            ]
            if isinstance(msg, KeyMsg):
                k = str(msg.key)
                if k == "ctrl+g":
                    self.generate_pgp = not self.generate_pgp
                    return self, None
                if k in ("tab", "down"):
                    self.step3_focus = (self.step3_focus + 1) % len(s3_fields)
                    self._sync_focus()
                    return self, None
                if k in ("shift+tab", "up"):
                    self.step3_focus = (self.step3_focus - 1) % len(s3_fields)
                    self._sync_focus()
                    return self, None
                if k == "enter":
                    return self._finalize_account()

            active_input = s3_fields[self.step3_focus]
            s3_fields[self.step3_focus], cmd = active_input.update(msg)
            return self, cmd

        return self, None

    def _handle_preset_selected(self, idx: int) -> tuple[OnboardingWizard, Cmd | None]:
        preset = PROVIDER_PRESETS[idx % len(PROVIDER_PRESETS)]
        self.selected_preset = preset
        if preset.is_demo:
            return self, lambda: OnboardingCompleteMsg(account=None, use_demo=True)

        self.input_imap_host.value = preset.imap_host
        self.input_imap_port.value = str(preset.imap_port)
        self.input_smtp_host.value = preset.smtp_host
        self.input_smtp_port.value = str(preset.smtp_port)
        if not self.input_account_name.value:
            self.input_account_name.value = preset.label.split("(")[0].strip()[:20]
        self.step = 2
        self.step2_focus = 0
        self.error_msg = ""
        self._sync_focus()
        return self, None

    def _advance_to_step3(self) -> tuple[OnboardingWizard, Cmd | None]:
        email_val = self.input_email.value.strip()
        if not email_val or "@" not in email_val:
            self.error_msg = "Please enter a valid email address containing '@'."
            return self, None
        if not self.input_full_name.value.strip():
            self.input_full_name.value = email_val.split("@")[0].replace(".", " ").title()
        if not self.input_account_name.value.strip():
            self.input_account_name.value = email_val.split("@")[-1]
        self.step = 3
        self.step3_focus = 0
        self.error_msg = ""
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
        except ValueError:
            self.error_msg = "IMAP and SMTP ports must be valid numbers."
            return self, None

        acc = AccountConfig(
            id=f"acc_{uuid.uuid4().hex[:8]}",
            name=self.input_account_name.value.strip(),
            email=self.input_email.value.strip(),
            full_name=self.input_full_name.value.strip(),
            imap_host=imap_host,
            imap_port=imap_port,
            imap_security=self.selected_preset.imap_security,
            imap_username=self.input_email.value.strip(),
            imap_password=self.input_password.value,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_security=self.selected_preset.smtp_security,
            smtp_username=self.input_email.value.strip(),
            smtp_password=self.input_password.value,
            sieve_host=imap_host,
            sieve_port=self.selected_preset.sieve_port,
        )
        return self, lambda: OnboardingCompleteMsg(
            account=acc,
            use_demo=False,
            generate_pgp=self.generate_pgp,
        )

    def view(self, width: int = 100, height: int = 30) -> str:
        """Render the guided onboarding wizard card."""
        step_badges = []
        for idx, title in enumerate(
            ("1. Provider Preset", "2. Identity & Auth", "3. Server & PGP"), start=1
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
                .render("Welcome to EspressoMail! Configure your first IMAP/SMTP account or explore the Demo Workspace:"),
                "",
                self.provider_prompt.view(),
                "",
                Style()
                .foreground(self.theme.fg_muted)
                .render("[↑/↓] Navigate   [Enter] Select Provider" + ("   [Esc] Cancel" if self.allow_cancel else "")),
            )
        elif self.step == 2:
            body = join_vertical(
                Align.LEFT,
                Style()
                .bold(True)
                .foreground(self.theme.accent_secondary)
                .render(f"Provider: {self.selected_preset.label}"),
                "",
                self.input_account_name.view(),
                self.input_full_name.view(),
                self.input_email.view(),
                self.input_password.view(),
                "",
                Style()
                .foreground(self.theme.fg_muted)
                .render("[Tab/↑/↓] Next/Prev Field   [Enter] Continue to Step 3   [Esc] Back"),
            )
        else:
            pgp_mark = "[x]" if self.generate_pgp else "[ ]"
            body = join_vertical(
                Align.LEFT,
                Style()
                .bold(True)
                .foreground(self.theme.accent_secondary)
                .render(f"Endpoints for {self.input_email.value}:"),
                "",
                self.input_imap_host.view(),
                self.input_imap_port.view(),
                self.input_smtp_host.view(),
                self.input_smtp_port.view(),
                "",
                Style()
                .foreground(self.theme.pgp_verified_fg if self.generate_pgp else self.theme.fg_muted)
                .render(f"{pgp_mark} Auto-generate Ed25519 OpenPGP Key Pair for this account (Ctrl+G to toggle)"),
                "",
                Style()
                .foreground(self.theme.fg_muted)
                .render("[Tab/↑/↓] Next/Prev Field   [Ctrl+G] Toggle PGP   [Enter] Finish Setup   [Esc] Back"),
            )

        err_block = (
            "\n" + Style().bold(True).foreground(self.theme.pgp_error_fg).render(f"⚠ {self.error_msg}")
            if self.error_msg
            else ""
        )

        card_content = f"{header_line}\n\n{body}{err_block}"
        card = (
            Style()
            .border(DOUBLE_BORDER)
            .border_foreground(self.theme.accent_primary)
            .border_title(
                Style()
                .bold(True)
                .foreground(self.theme.accent_secondary)
                .render(" [ ☕ EspressoMail Account Onboarding ] "),
                align=Align.CENTER,
            )
            .padding(1, 3)
            .width(min(78, max(56, width - 8)))
            .render(card_content)
        )

        return place(width, height, Align.CENTER, Align.CENTER, card)
