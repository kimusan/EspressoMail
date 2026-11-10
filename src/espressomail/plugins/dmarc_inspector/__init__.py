"""DMARC / DKIM / SPF Inspector & Aggregate Report Parser plugin for EspressoMail."""

from __future__ import annotations

import gzip
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from espresso import Cmd
from espresso.beans import ToastLevel
from espresso.crema import Style
from espressomail.models.entities import Attachment, EmailMessage, sanitize_terminal_text
from espressomail.plugins.base import BasePlugin, PluginMetadata
from espressomail.plugins.hooks import (
    PluginCommand,
    PluginMenuItem,
    ReaderActionPill,
)
from espressomail.themes.engine import THEMES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthResultsSummary:
    """Parsed email authentication results from message headers."""

    spf: str = "none"
    dkim: str = "none"
    dmarc: str = "none"
    spf_detail: str = ""
    dkim_domain: str = ""
    dmarc_policy: str = ""
    authserv_id: str = ""
    raw_auth_results: str = ""
    raw_received_spf: str = ""
    raw_dkim_signature: str = ""

    @property
    def has_any(self) -> bool:
        return any(x != "none" for x in (self.spf, self.dkim, self.dmarc)) or bool(
            self.raw_auth_results or self.raw_received_spf or self.raw_dkim_signature
        )

    @property
    def overall_badge(self) -> str:
        parts: list[str] = []
        for label, val in (("SPF", self.spf), ("DKIM", self.dkim), ("DMARC", self.dmarc)):
            v = val.lower()
            if v == "pass":
                parts.append(f"✔ {label}")
            elif v in ("fail", "hardfail", "permerror"):
                parts.append(f"✖ {label}")
            elif v in ("softfail", "neutral", "temperror", "policy"):
                parts.append(f"⚠ {label}")
        if not parts and self.raw_dkim_signature:
            parts.append("🔑 DKIM-Sig")
        return " ".join(parts) if parts else "🛡️ Auth: n/a"


@dataclass(frozen=True)
class DMARCRecordRow:
    """Single IP record row inside a DMARC aggregate XML report."""

    source_ip: str
    count: int
    disposition: str
    dkim_eval: str
    spf_eval: str
    header_from: str = ""
    dkim_domain: str = ""
    spf_domain: str = ""


@dataclass(frozen=True)
class DMARCAggregateReport:
    """Parsed RFC 7489 DMARC aggregate XML report."""

    filename: str
    org_name: str
    report_id: str
    date_begin: str
    date_end: str
    domain: str
    policy_p: str
    policy_sp: str = ""
    policy_pct: str = "100"
    records: list[DMARCRecordRow] = field(default_factory=list)

    @property
    def total_messages(self) -> int:
        return sum(r.count for r in self.records)

    @property
    def passed_messages(self) -> int:
        return sum(
            r.count
            for r in self.records
            if r.dkim_eval.lower() == "pass" or r.spf_eval.lower() == "pass"
        )


def _get_header_ci(headers: dict[str, str], name: str) -> str:
    """Case-insensitive lookup in raw_headers dictionary."""
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return str(v or "")
    return ""


def parse_auth_headers(headers: dict[str, str]) -> AuthResultsSummary:
    """Parse Authentication-Results, Received-SPF, and DKIM-Signature headers."""
    if not headers:
        return AuthResultsSummary()

    auth_res = _get_header_ci(headers, "Authentication-Results")
    if not auth_res:
        auth_res = _get_header_ci(headers, "ARC-Authentication-Results")
    rec_spf = _get_header_ci(headers, "Received-SPF")
    dkim_sig = _get_header_ci(headers, "DKIM-Signature")

    spf = "none"
    dkim = "none"
    dmarc = "none"
    spf_detail = ""
    dkim_domain = ""
    dmarc_policy = ""
    authserv_id = ""

    if auth_res:
        clean_ar = " ".join(auth_res.split())
        first_semi = clean_ar.find(";")
        if first_semi > 0:
            candidate = clean_ar[:first_semi].strip()
            if "=" not in candidate:
                authserv_id = candidate

        m_spf = re.search(r"\bspf\s*=\s*([a-zA-Z0-9_-]+)", clean_ar, re.IGNORECASE)
        if m_spf:
            spf = m_spf.group(1).lower()

        m_dkim = re.search(r"\bdkim\s*=\s*([a-zA-Z0-9_-]+)", clean_ar, re.IGNORECASE)
        if m_dkim:
            dkim = m_dkim.group(1).lower()

        m_dmarc = re.search(r"\bdmarc\s*=\s*([a-zA-Z0-9_-]+)", clean_ar, re.IGNORECASE)
        if m_dmarc:
            dmarc = m_dmarc.group(1).lower()

        m_hd = re.search(r"header\.(?:d|i)\s*=\s*@?([a-zA-Z0-9._-]+)", clean_ar, re.IGNORECASE)
        if m_hd:
            dkim_domain = m_hd.group(1)

        m_pol = re.search(r"\b(?:p|policy\.published-domain-policy)\s*=\s*([a-zA-Z0-9_-]+)", clean_ar, re.IGNORECASE)
        if m_pol:
            dmarc_policy = m_pol.group(1).lower()

    if rec_spf:
        clean_spf = " ".join(rec_spf.split())
        spf_detail = clean_spf
        if spf == "none":
            m_rspf = re.match(r"^\s*([a-zA-Z0-9_-]+)", clean_spf)
            if m_rspf:
                spf = m_rspf.group(1).lower()

    if dkim_sig and not dkim_domain:
        m_sig_d = re.search(r"\bd\s*=\s*([a-zA-Z0-9._-]+)", dkim_sig)
        if m_sig_d:
            dkim_domain = m_sig_d.group(1)

    return AuthResultsSummary(
        spf=spf,
        dkim=dkim,
        dmarc=dmarc,
        spf_detail=spf_detail,
        dkim_domain=dkim_domain,
        dmarc_policy=dmarc_policy,
        authserv_id=authserv_id,
        raw_auth_results=" ".join(auth_res.split()) if auth_res else "",
        raw_received_spf=" ".join(rec_spf.split()) if rec_spf else "",
        raw_dkim_signature=" ".join(dkim_sig.split()) if dkim_sig else "",
    )


def _strip_ns(tag: str) -> str:
    """Strip XML namespace URI prefix from tag name."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_child_text(elem: ET.Element | None, path: Sequence[str], default: str = "") -> str:
    """Namespace-agnostic child element text lookup."""
    cur = elem
    for step in path:
        if cur is None:
            return default
        nxt = None
        for ch in cur:
            if _strip_ns(ch.tag) == step:
                nxt = ch
                break
        cur = nxt
    if cur is not None and cur.text:
        return cur.text.strip()
    return default


def parse_dmarc_xml_bytes(xml_bytes: bytes, filename: str = "report.xml") -> DMARCAggregateReport | None:
    """Parse raw DMARC aggregate XML bytes into a structured DMARCAggregateReport."""
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as exc:
        logger.debug("Not a valid XML document (%s): %s", filename, exc)
        return None

    if _strip_ns(root.tag) != "feedback":
        return None

    org_name = _find_child_text(root, ("report_metadata", "org_name"), "Unknown Org")
    report_id = _find_child_text(root, ("report_metadata", "report_id"), "")
    begin_ts = _find_child_text(root, ("report_metadata", "date_range", "begin"), "")
    end_ts = _find_child_text(root, ("report_metadata", "date_range", "end"), "")

    def _fmt_epoch(ts_str: str) -> str:
        try:
            dt = datetime.fromtimestamp(int(ts_str), tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return ts_str

    date_begin = _fmt_epoch(begin_ts) if begin_ts else ""
    date_end = _fmt_epoch(end_ts) if end_ts else ""

    domain = _find_child_text(root, ("policy_published", "domain"), "")
    policy_p = _find_child_text(root, ("policy_published", "p"), "none")
    policy_sp = _find_child_text(root, ("policy_published", "sp"), "")
    policy_pct = _find_child_text(root, ("policy_published", "pct"), "100")

    records: list[DMARCRecordRow] = []
    for child in root:
        if _strip_ns(child.tag) != "record":
            continue
        source_ip = _find_child_text(child, ("row", "source_ip"), "0.0.0.0")
        count_str = _find_child_text(child, ("row", "count"), "1")
        try:
            count = int(count_str)
        except ValueError:
            count = 1
        disposition = _find_child_text(child, ("row", "policy_evaluated", "disposition"), "none")
        dkim_eval = _find_child_text(child, ("row", "policy_evaluated", "dkim"), "none")
        spf_eval = _find_child_text(child, ("row", "policy_evaluated", "spf"), "none")
        header_from = _find_child_text(child, ("identifiers", "header_from"), domain)
        dkim_dom = _find_child_text(child, ("auth_results", "dkim", "domain"), "")
        spf_dom = _find_child_text(child, ("auth_results", "spf", "domain"), "")

        records.append(
            DMARCRecordRow(
                source_ip=sanitize_terminal_text(source_ip, multiline=False),
                count=count,
                disposition=sanitize_terminal_text(disposition, multiline=False),
                dkim_eval=sanitize_terminal_text(dkim_eval, multiline=False),
                spf_eval=sanitize_terminal_text(spf_eval, multiline=False),
                header_from=sanitize_terminal_text(header_from, multiline=False),
                dkim_domain=sanitize_terminal_text(dkim_dom, multiline=False),
                spf_domain=sanitize_terminal_text(spf_dom, multiline=False),
            )
        )

    return DMARCAggregateReport(
        filename=sanitize_terminal_text(filename, multiline=False),
        org_name=sanitize_terminal_text(org_name, multiline=False),
        report_id=sanitize_terminal_text(report_id, multiline=False),
        date_begin=date_begin,
        date_end=date_end,
        domain=sanitize_terminal_text(domain, multiline=False),
        policy_p=sanitize_terminal_text(policy_p, multiline=False),
        policy_sp=sanitize_terminal_text(policy_sp, multiline=False),
        policy_pct=sanitize_terminal_text(policy_pct, multiline=False),
        records=records,
    )


def extract_dmarc_reports_from_attachment(att: Attachment) -> list[DMARCAggregateReport]:
    """Inspect an Attachment (.xml, .gz, .xml.gz, .zip) and parse any DMARC reports inside."""
    fname = (att.filename or "").lower()
    ctype = (att.content_type or "").lower()
    is_candidate = (
        fname.endswith((".xml", ".xml.gz", ".gz", ".zip"))
        or "xml" in ctype
        or "gzip" in ctype
        or "zip" in ctype
    )
    if not is_candidate:
        return []

    raw_bytes = att.data
    if not raw_bytes and att.local_path:
        try:
            p = Path(att.local_path)
            if p.exists():
                raw_bytes = p.read_bytes()
        except Exception:
            raw_bytes = None

    if not raw_bytes:
        return []

    reports: list[DMARCAggregateReport] = []

    # 1. ZIP archive
    if fname.endswith(".zip") or raw_bytes.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                for member in zf.infolist():
                    if member.is_dir() or member.file_size > 10 * 1024 * 1024:
                        continue
                    if member.filename.lower().endswith(".xml"):
                        xml_data = zf.read(member.filename)
                        rep = parse_dmarc_xml_bytes(xml_data, filename=f"{att.filename}:{member.filename}")
                        if rep:
                            reports.append(rep)
        except Exception as exc:
            logger.debug("Failed reading zip attachment %s for DMARC: %s", att.filename, exc)
        return reports

    # 2. GZIP compressed XML
    if fname.endswith(".gz") or raw_bytes.startswith(b"\x1f\x8b"):
        try:
            xml_data = gzip.decompress(raw_bytes)
            rep = parse_dmarc_xml_bytes(xml_data, filename=att.filename)
            if rep:
                reports.append(rep)
        except Exception as exc:
            logger.debug("Failed decompressing gzip attachment %s for DMARC: %s", att.filename, exc)
        return reports

    # 3. Plain XML
    if fname.endswith(".xml") or b"<feedback" in raw_bytes[:1024]:
        rep = parse_dmarc_xml_bytes(raw_bytes, filename=att.filename)
        if rep:
            reports.append(rep)

    return reports


class DMARCInspectorPlugin(BasePlugin):
    """Inspects SPF/DKIM/DMARC authentication headers and parses DMARC aggregate XML reports."""

    metadata = PluginMetadata(
        id="dmarc_inspector",
        name="DMARC & Auth Inspector",
        version="1.0.0",
        author="Kim Schulz",
        description="Parses SPF/DKIM/DMARC authentication headers and renders DMARC XML/GZ/ZIP aggregate reports.",
        icon="🛡️",
        settings_schema={
            "auto_expand_dmarc_reports": {
                "type": "bool",
                "label": "Auto-Render DMARC Reports",
                "default": "true",
                "description": "Automatically unpack and display DMARC XML/GZ/ZIP reports in the reading pane",
            },
            "always_show_auth_banner": {
                "type": "bool",
                "label": "Always Show Auth Details",
                "default": "false",
                "description": "Always display detailed SPF/DKIM/DMARC authentication box without pressing Ctrl+D",
            },
        },
    )

    def __init__(self, context: Any) -> None:
        super().__init__(context)
        self._expanded_messages: set[str] = set()

    def get_menu_items(self) -> Sequence[PluginMenuItem]:
        return (
            PluginMenuItem(
                target_category="Tools",
                action_id="dmarc_toggle_details",
                label="Inspect SPF/DKIM/DMARC Auth",
                shortcut="Ctrl+D",
                order=55,
            ),
        )

    def get_commands(self) -> Sequence[PluginCommand]:
        return (
            PluginCommand(
                action_id="dmarc_toggle_details",
                title="Security: Toggle SPF / DKIM / DMARC Inspector Details",
                description="Show or hide full email authentication headers and DMARC aggregate report tables",
                category="Plugins",
                default_key="ctrl+d",
                pane_scope="reader",
            ),
        )

    def get_keybindings(self) -> dict[str, str]:
        return {"ctrl+d": "dmarc_toggle_details"}

    def get_reader_actions(self, message: EmailMessage | None) -> Sequence[ReaderActionPill]:
        if not message:
            return ()

        auth = parse_auth_headers(message.raw_headers)
        reports: list[DMARCAggregateReport] = []
        for att in message.attachments:
            reports.extend(extract_dmarc_reports_from_attachment(att))

        if not auth.has_any and not reports:
            return ()

        badge = auth.overall_badge
        if reports:
            total_msgs = sum(r.total_messages for r in reports)
            badge = f"📊 DMARC Report ({total_msgs} msgs)"

        label = "Hide Auth" if message.id in self._expanded_messages else "Auth Details"
        return (
            ReaderActionPill(
                action_id="dmarc_toggle_details",
                label=label,
                shortcut="Ctrl+D",
                badge=badge,
                priority=10,
            ),
        )

    def transform_reader_content(
        self, message: EmailMessage, rendered_body: str, state: dict[str, Any]
    ) -> str:
        if not message:
            return rendered_body

        auth = parse_auth_headers(message.raw_headers)
        reports: list[DMARCAggregateReport] = []
        for att in message.attachments:
            reports.extend(extract_dmarc_reports_from_attachment(att))

        always_show = self.context.get_setting("always_show_auth_banner", "false").lower() in ("1", "true", "yes")
        auto_dmarc = self.context.get_setting("auto_expand_dmarc_reports", "true").lower() in ("1", "true", "yes")
        is_expanded = message.id in self._expanded_messages

        show_auth_box = is_expanded or always_show
        show_dmarc_box = bool(reports) and (auto_dmarc or is_expanded)

        if not show_auth_box and not show_dmarc_box:
            return rendered_body

        theme = state.get("theme") or THEMES["espresso"]
        width = max(50, int(state.get("width", 78)))
        fg_ok = theme.pgp_verified_fg
        fg_err = theme.pgp_error_fg
        fg_warn = theme.pgp_warning_fg
        fg_muted = theme.fg_muted
        accent = theme.accent_primary

        def _status_styled(val: str) -> str:
            v = val.lower()
            if v == "pass":
                return Style().bold(True).foreground(fg_ok).render(f"✔ PASS ({val})")
            if v in ("fail", "hardfail", "permerror", "reject", "quarantine"):
                return Style().bold(True).foreground(fg_err).render(f"✖ {val.upper()}")
            if v == "none":
                return Style().foreground(fg_muted).render("— none")
            return Style().bold(True).foreground(fg_warn).render(f"⚠ {val.upper()}")

        blocks: list[str] = []

        if show_auth_box:
            lines: list[str] = [
                Style().bold(True).foreground(accent).render("🛡️ Email Authentication Inspector (SPF / DKIM / DMARC)")
                + " "
                + Style().foreground(fg_muted).render("(Ctrl+D to toggle)"),
                f"  • SPF:   {_status_styled(auth.spf)}"
                + (f"  [{auth.spf_detail[:55]}]" if auth.spf_detail else ""),
                f"  • DKIM:  {_status_styled(auth.dkim)}"
                + (f"  (domain: {auth.dkim_domain})" if auth.dkim_domain else ""),
                f"  • DMARC: {_status_styled(auth.dmarc)}"
                + (f"  (policy: {auth.dmarc_policy})" if auth.dmarc_policy else ""),
            ]
            if auth.authserv_id:
                lines.append(Style().foreground(fg_muted).render(f"  • Auth Server: {auth.authserv_id}"))
            if auth.raw_auth_results:
                lines.append(
                    Style().foreground(fg_muted).render(f"  • Header: {auth.raw_auth_results[: max(30, width - 14)]}")
                )
            blocks.append("\n".join(lines))

        if show_dmarc_box:
            for rep in reports:
                rep_lines: list[str] = [
                    Style()
                    .bold(True)
                    .foreground(accent)
                    .render(f"📊 DMARC Aggregate Report: {rep.domain or rep.filename} (from {rep.org_name})"),
                    Style()
                    .foreground(fg_muted)
                    .render(
                        f"  Report ID: {rep.report_id}  │  Period: {rep.date_begin} → {rep.date_end}"
                    ),
                    Style()
                    .foreground(fg_muted)
                    .render(
                        f"  Policy: p={rep.policy_p}, sp={rep.policy_sp or rep.policy_p}, pct={rep.policy_pct}%  │  "
                        f"Passed: {rep.passed_messages}/{rep.total_messages} messages"
                    ),
                    "",
                    Style()
                    .bold(True)
                    .foreground(theme.fg_secondary)
                    .render(f"  {'Source IP':<18} │ {'Msgs':>5} │ {'Disposition':<11} │ {'DKIM':<8} │ {'SPF':<8} │ {'Header From'}"),
                    Style().foreground(fg_muted).render("  " + ("─" * min(width - 4, 76))),
                ]
                for r in rep.records:
                    rep_lines.append(
                        f"  {r.source_ip:<18} │ {r.count:>5} │ {r.disposition:<11} │ "
                        f"{r.dkim_eval:<8} │ {r.spf_eval:<8} │ {r.header_from}"
                    )
                blocks.append("\n".join(rep_lines))

        injected = "\n\n".join(blocks)
        div_match = re.search(r"━{10,}", rendered_body)
        if div_match:
            line_end = rendered_body.find("\n", div_match.end())
            line_end = len(rendered_body) if line_end == -1 else line_end
            header_block = rendered_body[:line_end]
            body_rest = rendered_body[line_end:].lstrip("\n")
            return f"{header_block}\n\n{injected}\n\n{body_rest}"

        return f"{injected}\n\n{rendered_body}"

    def on_action(
        self, action_id: str, app: Any, data: dict[str, Any]
    ) -> tuple[Any, Cmd | None]:
        if action_id == "dmarc_toggle_details":
            m = getattr(app, "selected_message", None)
            if not m:
                app.status_message = "No message selected to inspect."
                return app, None

            if m.id in self._expanded_messages:
                self._expanded_messages.remove(m.id)
                app.status_message = "Hidden SPF/DKIM/DMARC inspector details."
            else:
                self._expanded_messages.add(m.id)
                auth = parse_auth_headers(m.raw_headers)
                app.status_message = f"🛡️ Auth Inspector: SPF={auth.spf.upper()}, DKIM={auth.dkim.upper()}, DMARC={auth.dmarc.upper()}"
                if hasattr(app, "toasts"):
                    app.toasts.add(auth.overall_badge, level=ToastLevel.INFO)

            app._update_reader_viewport(force=True)
            return app, None

        return app, None
