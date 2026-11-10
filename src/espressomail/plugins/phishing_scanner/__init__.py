"""Phishing & Suspicious Link Scanner / URL Defense plugin for EspressoMail."""

from __future__ import annotations

import html
import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import urlparse

from espresso import Cmd
from espresso.beans import ToastLevel
from espresso.crema import Style
from espressomail.models.entities import EmailMessage, sanitize_terminal_text
from espressomail.plugins.base import BasePlugin, PluginMetadata
from espressomail.plugins.hooks import (
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)
from espressomail.themes.engine import THEMES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhishingWarning:
    """A single suspicious or deceptive indicator found in an email."""

    rule_id: str
    severity: str  # "high" or "medium"
    summary: str
    detail: str = ""


def _extract_email_domain(addr: str) -> str:
    """Extract normalized lowercase domain from an email address or RFC 5322 string."""
    if not addr:
        return ""
    m = re.search(r"[\w.+-]+@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", addr)
    if m:
        return m.group(1).lower().strip(".")
    return ""


def _registrable_domain(host: str) -> str:
    """Approximate registrable domain (eTLD+1) to compare organizational domains."""
    if not host:
        return ""
    h = host.lower().strip().rstrip(".")
    if h.startswith("www."):
        h = h[4:]
    parts = h.split(".")
    if len(parts) <= 2:
        return h
    # Common two-part public suffixes
    multi_tlds = {"co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "co.jp", "co.nz", "com.br", "co.za"}
    last_two = f"{parts[-2]}.{parts[-1]}"
    if last_two in multi_tlds and len(parts) >= 3:
        return f"{parts[-3]}.{last_two}"
    return last_two


def _is_ip_host(host: str) -> bool:
    """Return True if host is an IPv4 or IPv6 literal."""
    if not host:
        return False
    clean = host.strip("[]")
    try:
        ipaddress.ip_address(clean)
        return True
    except ValueError:
        return False


def scan_message_for_phishing(message: EmailMessage) -> list[PhishingWarning]:
    """Analyze an EmailMessage for spoofing, homograph domains, and deceptive links."""
    warnings: list[PhishingWarning] = []
    if not message:
        return warnings

    sender_email = (message.sender_email or "").strip()
    sender_name = (message.sender_name or "").strip()
    sender_dom = _extract_email_domain(sender_email)
    headers = message.raw_headers or {}

    # 1. Display Name Spoofing: sender_name contains an email address whose domain != sender_email
    if sender_name and "@" in sender_name:
        spoof_dom = _extract_email_domain(sender_name)
        if spoof_dom and sender_dom and _registrable_domain(spoof_dom) != _registrable_domain(sender_dom):
            warnings.append(
                PhishingWarning(
                    rule_id="display_name_spoof",
                    severity="high",
                    summary=f"Display name contains '{spoof_dom}' but actual sender is '{sender_dom}'",
                    detail=f"From: {sanitize_terminal_text(sender_name, False)} <{sanitize_terminal_text(sender_email, False)}>",
                )
            )

    # 2. From vs Reply-To domain mismatch
    reply_to = ""
    for k, v in headers.items():
        if k.lower() == "reply-to":
            reply_to = str(v or "").strip()
            break
    if reply_to:
        rt_dom = _extract_email_domain(reply_to)
        if rt_dom and sender_dom and _registrable_domain(rt_dom) != _registrable_domain(sender_dom):
            warnings.append(
                PhishingWarning(
                    rule_id="reply_to_mismatch",
                    severity="medium",
                    summary=f"Reply-To domain ({rt_dom}) differs from Sender domain ({sender_dom})",
                    detail=f"Reply-To: {sanitize_terminal_text(reply_to, False)}",
                )
            )

    # 3. Authentication failure (DMARC/SPF/DKIM fail in Authentication-Results)
    for k, v in headers.items():
        if k.lower() == "authentication-results":
            ar_val = str(v or "").lower()
            if re.search(r"\bdmarc\s*=\s*(fail|permerror)", ar_val) or re.search(
                r"\bspf\s*=\s*(fail|hardfail)", ar_val
            ):
                warnings.append(
                    PhishingWarning(
                        rule_id="auth_failure",
                        severity="high",
                        summary="Sender failed SPF or DMARC authentication verification",
                        detail=sanitize_terminal_text(" ".join(str(v).split())[:100], False),
                    )
                )
            break

    # 4. Inspect HTML <a href="..."> links for deceptive anchor text, punycode, and raw IPs
    seen_urls: set[str] = set()
    body_html = message.body_html or ""
    if body_html:
        for m in re.finditer(
            r'<a\b[^>]*?\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            body_html,
            re.IGNORECASE | re.DOTALL,
        ):
            href = html.unescape(m.group(1).strip())
            anchor_raw = re.sub(r"<[^>]+>", "", m.group(2))
            anchor_text = html.unescape(" ".join(anchor_raw.split())).strip()
            if not href or href.lower().startswith(("mailto:", "cid:", "#")):
                continue

            try:
                parsed_href = urlparse(href)
                href_host = (parsed_href.hostname or "").lower()
            except Exception:
                href_host = ""

            if not href_host:
                continue

            seen_urls.add(href)

            # 4a. Anchor text looks like a URL or domain name that differs from href_host
            anchor_url_match = re.match(
                r"^(?:https?://)?([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)(?:/.*)?$",
                anchor_text,
            )
            if anchor_url_match:
                claimed_host = anchor_url_match.group(1).lower()
                if (
                    "." in claimed_host
                    and _registrable_domain(claimed_host) != _registrable_domain(href_host)
                ):
                    warnings.append(
                        PhishingWarning(
                            rule_id="mismatched_anchor_url",
                            severity="high",
                            summary=f"Deceptive link: text shows '{claimed_host}' but points to '{href_host}'",
                            detail=f"Target URL: {sanitize_terminal_text(href[:100], False)}",
                        )
                    )

            # 4b. Punycode IDN homograph domain (xn--)
            if "xn--" in href_host:
                warnings.append(
                    PhishingWarning(
                        rule_id="punycode_domain",
                        severity="high",
                        summary=f"Link uses Punycode/IDN lookalike domain: '{href_host}'",
                        detail=f"Target URL: {sanitize_terminal_text(href[:100], False)}",
                    )
                )

            # 4c. Raw IP address link
            if _is_ip_host(href_host):
                warnings.append(
                    PhishingWarning(
                        rule_id="raw_ip_url",
                        severity="medium",
                        summary=f"Link points directly to raw IP address: {href_host}",
                        detail=f"Target URL: {sanitize_terminal_text(href[:100], False)}",
                    )
                )

    # 5. Also scan plain-text URLs for punycode or raw IP hosts not already flagged
    body_text = message.body_text or ""
    if body_text:
        for m in re.finditer(r"https?://[^\s<>\"')\]]+", body_text, re.IGNORECASE):
            url = m.group(0)
            if url in seen_urls:
                continue
            try:
                parsed = urlparse(url)
                host = (parsed.hostname or "").lower()
            except Exception:
                host = ""
            if not host:
                continue
            seen_urls.add(url)
            if "xn--" in host:
                warnings.append(
                    PhishingWarning(
                        rule_id="punycode_domain",
                        severity="high",
                        summary=f"Link uses Punycode/IDN lookalike domain: '{host}'",
                        detail=f"Target URL: {sanitize_terminal_text(url[:100], False)}",
                    )
                )
            elif _is_ip_host(host):
                warnings.append(
                    PhishingWarning(
                        rule_id="raw_ip_url",
                        severity="medium",
                        summary=f"Link points directly to raw IP address: {host}",
                        detail=f"Target URL: {sanitize_terminal_text(url[:100], False)}",
                    )
                )

    return warnings


class PhishingScannerPlugin(BasePlugin):
    """Detects spoofing, deceptive links, punycode domains, and phishing indicators."""

    metadata = PluginMetadata(
        id="phishing_scanner",
        name="Phishing & Link Scanner",
        version="1.0.0",
        author="Kim Schulz",
        description="Detects display-name spoofing, Reply-To mismatches, deceptive HTML links, and Punycode domains.",
        icon="🚨",
        settings_schema={
            "auto_show_banner": {
                "type": "bool",
                "label": "Auto-Show Phishing Alerts",
                "default": "true",
                "description": "Automatically display a high-visibility warning banner when suspicious indicators are found",
            },
        },
    )

    def __init__(self, context: Any) -> None:
        super().__init__(context)
        self._dismissed_messages: set[str] = set()

    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        return (
            PluginMenuItem(
                target_category="Tools",
                action_id="phishing_scan_message",
                label="Scan Email for Phishing",
                shortcut="",
                order=56,
            ),
        )

    def get_commands(self) -> Sequence[PluginCommand]:
        return (
            PluginCommand(
                action_id="phishing_scan_message",
                title="Security: Scan Current Email for Phishing & Deceptive Links",
                description="Run URL defense and header spoofing checks on the selected email",
                category="Plugins",
                default_key="",
                pane_scope="reader",
            ),
        )

    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        if not message:
            return ()
        warnings = scan_message_for_phishing(message)
        if not warnings:
            return ()

        count = len(warnings)
        return (
            ReaderActionPill(
                action_id="phishing_scan_message",
                label="Security Alert" if message.id not in self._dismissed_messages else "Show Alert",
                shortcut="",
                badge=f"🚨 {count} Warning{'s' if count != 1 else ''}",
                priority=1,
            ),
        )

    def transform_reader_content(
        self, message: EmailMessage, rendered_body: str, state: dict[str, Any]
    ) -> str:
        if not message or message.id in self._dismissed_messages:
            return rendered_body

        auto_show = self.context.get_setting("auto_show_banner", "true").lower() in ("1", "true", "yes")
        if not auto_show:
            return rendered_body

        warnings = scan_message_for_phishing(message)
        if not warnings:
            return rendered_body

        theme = state.get("theme") or THEMES["espresso"]
        fg_err = theme.pgp_error_fg
        fg_warn = theme.pgp_warning_fg
        fg_muted = theme.fg_muted

        lines: list[str] = [
            Style()
            .bold(True)
            .foreground(fg_err)
            .render(f"🚨 SECURITY WARNING: {len(warnings)} Suspicious Indicator(s) Detected in This Email"),
        ]
        for w in warnings:
            color = fg_err if w.severity == "high" else fg_warn
            bullet = "❗" if w.severity == "high" else "⚠"
            lines.append(Style().bold(True).foreground(color).render(f"  {bullet} {w.summary}"))
            if w.detail:
                lines.append(Style().foreground(fg_muted).render(f"     {w.detail}"))

        alert_block = "\n".join(lines)
        div_match = re.search(r"━{10,}", rendered_body)
        if div_match:
            line_end = rendered_body.find("\n", div_match.end())
            line_end = len(rendered_body) if line_end == -1 else line_end
            header_block = rendered_body[:line_end]
            body_rest = rendered_body[line_end:].lstrip("\n")
            return f"{header_block}\n\n{alert_block}\n\n{body_rest}"

        return f"{alert_block}\n\n{rendered_body}"

    def on_action(
        self, action_id: str, app: Any, data: dict[str, Any]
    ) -> tuple[Any, Cmd | None]:
        if action_id == "phishing_scan_message":
            m = getattr(app, "selected_message", None)
            if not m:
                app.status_message = "No message selected to scan."
                return app, None

            warnings = scan_message_for_phishing(m)
            if not warnings:
                app.status_message = "✔ Security scan passed: no spoofing or deceptive links detected."
                if hasattr(app, "toasts"):
                    app.toasts.add("No phishing indicators found", level=ToastLevel.INFO)
                return app, None

            if m.id in self._dismissed_messages:
                self._dismissed_messages.remove(m.id)
                app.status_message = f"🚨 Showing {len(warnings)} phishing warning(s)."
            else:
                self._dismissed_messages.add(m.id)
                app.status_message = f"Dismissed phishing banner ({len(warnings)} warning(s))."

            app._update_reader_viewport(force=True)
            return app, None

        return app, None
