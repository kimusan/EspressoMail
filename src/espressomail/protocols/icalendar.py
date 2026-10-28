"""iCalendar (RFC 5545), iTIP (RFC 5546), and iMIP (RFC 6047) parser and invitation RSVP generator."""

from __future__ import annotations

import email.utils
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage as StdlibEmailMessage
from typing import Sequence

from espressomail.models.entities import (
    AccountConfig,
    Attachment,
    EmailMessage,
)


@dataclass
class CalendarAttendee:
    """An attendee for a calendar event."""

    email: str
    cn: str = ""
    role: str = ""  # REQ-PARTICIPANT, OPT-PARTICIPANT, CHAIR, etc.
    partstat: str = ""  # NEEDS-ACTION, ACCEPTED, DECLINED, TENTATIVE
    rsvp: bool = False

    @property
    def display_name(self) -> str:
        """Formatted attendee label."""
        if self.cn and self.email:
            return f"{self.cn} <{self.email}>"
        return self.cn or self.email or "Unknown Attendee"


@dataclass
class CalendarEvent:
    """Parsed iCalendar VEVENT structure."""

    uid: str
    summary: str = "(No Title)"
    description: str = ""
    location: str = ""
    dtstart: datetime | None = None
    dtend: datetime | None = None
    dtstart_raw: str = ""
    dtend_raw: str = ""
    is_all_day: bool = False
    organizer_email: str = ""
    organizer_cn: str = ""
    organizer_raw: str = ""
    status: str = "CONFIRMED"  # CONFIRMED, TENTATIVE, CANCELLED
    method: str = "REQUEST"  # REQUEST, REPLY, CANCEL, PUBLISH
    sequence: int = 0
    recurrence_rule: str = ""
    attendees: list[CalendarAttendee] = field(default_factory=list)
    raw_ics: str = ""

    @property
    def organizer_display(self) -> str:
        """Formatted organizer label."""
        if self.organizer_cn and self.organizer_email:
            return f"{self.organizer_cn} <{self.organizer_email}>"
        return self.organizer_cn or self.organizer_email or self.organizer_raw or "Unknown Organizer"

    def formatted_time_range(self) -> str:
        """Format the event time into a human-readable string."""
        if not self.dtstart:
            return self.dtstart_raw or "(Time not specified)"

        # Check if all-day event
        if self.is_all_day:
            start_str = self.dtstart.strftime("%A, %d %b %Y")
            if self.dtend and self.dtend.date() != self.dtstart.date():
                end_str = self.dtend.strftime("%A, %d %b %Y")
                return f"{start_str} – {end_str} (All Day)"
            return f"{start_str} (All Day)"

        # Timed event
        tz_name = "UTC" if self.dtstart.tzinfo else ""
        if self.dtstart.tzinfo and hasattr(self.dtstart.tzinfo, "key"):
            tz_name = getattr(self.dtstart.tzinfo, "key")
        elif self.dtstart.tzinfo:
            tz_name = self.dtstart.strftime("%Z") or "UTC"

        tz_suffix = f" {tz_name}" if tz_name else ""

        if self.dtend:
            same_day = (
                self.dtstart.year == self.dtend.year
                and self.dtstart.month == self.dtend.month
                and self.dtstart.day == self.dtend.day
            )
            if same_day:
                date_part = self.dtstart.strftime("%A, %d %b %Y")
                return f"{date_part}, {self.dtstart.strftime('%H:%M')} – {self.dtend.strftime('%H:%M')}{tz_suffix}"
            else:
                return (
                    f"{self.dtstart.strftime('%d %b %Y %H:%M')} – "
                    f"{self.dtend.strftime('%d %b %Y %H:%M')}{tz_suffix}"
                )

        return f"{self.dtstart.strftime('%A, %d %b %Y, %H:%M')}{tz_suffix}"


def unfold_ical_text(text: str) -> list[str]:
    """Unfold RFC 5545 content lines (remove CRLF followed by space/tab)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    unfolded: list[str] = []
    for line in text.split("\n"):
        if (line.startswith(" ") or line.startswith("\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def unescape_ical_value(val: str) -> str:
    """Unescape RFC 5545 escaped characters (\\,, \\;, \\n, \\N, \\\\)."""
    if "\\" not in val:
        return val
    out = []
    i = 0
    n = len(val)
    while i < n:
        if val[i] == "\\" and i + 1 < n:
            nxt = val[i + 1]
            if nxt in (",", ";"):
                out.append(nxt)
                i += 2
            elif nxt in ("n", "N"):
                out.append("\n")
                i += 2
            elif nxt == "\\":
                out.append("\\")
                i += 2
            else:
                out.append(nxt)
                i += 2
        else:
            out.append(val[i])
            i += 1
    return "".join(out)


def parse_ical_param_line(line: str) -> tuple[str, dict[str, str], str]:
    """Parse a single iCalendar line into (property_name, params_dict, value)."""
    # Find the separator ':' between property/params and value (outside double quotes)
    colon_idx = -1
    in_quotes = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            colon_idx = i
            break

    if colon_idx == -1:
        # Fallback if no colon
        return line.strip().upper(), {}, ""

    prop_part = line[:colon_idx].strip()
    val_part = line[colon_idx + 1 :]

    # Split prop_part into name and params by ';' outside quotes
    tokens: list[str] = []
    cur: list[str] = []
    in_quotes = False
    for ch in prop_part:
        if ch == '"':
            in_quotes = not in_quotes
            cur.append(ch)
        elif ch == ";" and not in_quotes:
            tokens.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append("".join(cur).strip())

    prop_name = tokens[0].upper() if tokens else ""
    params: dict[str, str] = {}
    for param_token in tokens[1:]:
        if "=" in param_token:
            p_k, p_v = param_token.split("=", 1)
            clean_v = p_v.strip().strip('"')
            params[p_k.strip().upper()] = clean_v

    return prop_name, params, val_part


def parse_ical_datetime(val: str, params: dict[str, str] | None = None) -> tuple[datetime | None, bool]:
    """Parse an RFC 5545 date or date-time string.

    Returns (datetime, is_all_day).
    """
    clean = val.strip()
    if not clean:
        return None, False

    # Check date-only value
    if (params and params.get("VALUE", "").upper() == "DATE") or (len(clean) == 8 and clean.isdigit()):
        try:
            dt = datetime.strptime(clean, "%Y%m%d").replace(tzinfo=timezone.utc)
            return dt, True
        except ValueError:
            return None, True

    # Check UTC datetime with 'Z' suffix (e.g. 20261028T140000Z)
    if clean.endswith("Z"):
        dt_str = clean[:-1]
        for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
            try:
                dt = datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
                return dt, False
            except ValueError:
                pass

    # Check timezone if specified in params (e.g. TZID=America/New_York)
    tz = None
    if params and "TZID" in params:
        tzid = params["TZID"].strip().strip('"')
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tzid)
        except Exception:
            tz = None

    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(clean, fmt)
            if tz:
                parsed = parsed.replace(tzinfo=tz)
            return parsed, False
        except ValueError:
            pass

    return None, False


def parse_ics(data: str | bytes) -> list[CalendarEvent]:
    """Parse an iCalendar (RFC 5545) payload into a list of CalendarEvent objects."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1", errors="replace")
    else:
        text = data

    unfolded = unfold_ical_text(text)
    events: list[CalendarEvent] = []

    calendar_method = "REQUEST"
    in_event = False
    cur_props: list[tuple[str, dict[str, str], str]] = []

    for line in unfolded:
        sline = line.strip()
        if not sline:
            continue
        prop_name, params, val = parse_ical_param_line(line)

        if prop_name == "METHOD":
            calendar_method = val.strip().upper() or "REQUEST"
            continue

        if prop_name == "BEGIN" and val.strip().upper() == "VEVENT":
            in_event = True
            cur_props = []
            continue

        if prop_name == "END" and val.strip().upper() == "VEVENT":
            if in_event and cur_props:
                ev = _build_event_from_props(cur_props, calendar_method, text)
                events.append(ev)
            in_event = False
            cur_props = []
            continue

        if in_event:
            cur_props.append((prop_name, params, val))

    return events


def _build_event_from_props(
    props: list[tuple[str, dict[str, str], str]],
    calendar_method: str,
    raw_ics: str,
) -> CalendarEvent:
    """Construct a CalendarEvent from a list of parsed property tuples."""
    uid = f"event-{uuid.uuid4().hex[:8]}"
    summary = "(No Title)"
    description = ""
    location = ""
    dtstart: datetime | None = None
    dtend: datetime | None = None
    dtstart_raw = ""
    dtend_raw = ""
    is_all_day = False
    organizer_email = ""
    organizer_cn = ""
    organizer_raw = ""
    status = "CONFIRMED"
    sequence = 0
    rrule = ""
    attendees: list[CalendarAttendee] = []

    for prop_name, params, val in props:
        if prop_name == "UID":
            uid = val.strip()
        elif prop_name == "SUMMARY":
            summary = unescape_ical_value(val.strip())
        elif prop_name == "DESCRIPTION":
            description = unescape_ical_value(val.strip())
        elif prop_name == "LOCATION":
            location = unescape_ical_value(val.strip())
        elif prop_name == "STATUS":
            status = val.strip().upper()
        elif prop_name == "SEQUENCE":
            try:
                sequence = int(val.strip())
            except ValueError:
                sequence = 0
        elif prop_name == "RRULE":
            rrule = val.strip()
        elif prop_name == "DTSTART":
            dtstart_raw = val.strip()
            dtstart, is_all_day = parse_ical_datetime(val, params)
        elif prop_name == "DTEND":
            dtend_raw = val.strip()
            dtend, _ = parse_ical_datetime(val, params)
        elif prop_name == "ORGANIZER":
            organizer_raw = val.strip()
            organizer_cn = params.get("CN", "").strip()
            clean_email = val.strip()
            if clean_email.lower().startswith("mailto:"):
                clean_email = clean_email[7:]
            organizer_email = clean_email.strip()
        elif prop_name == "ATTENDEE":
            raw_att = val.strip()
            att_email = raw_att[7:] if raw_att.lower().startswith("mailto:") else raw_att
            att_cn = params.get("CN", "").strip()
            att_role = params.get("ROLE", "REQ-PARTICIPANT").strip().upper()
            att_partstat = params.get("PARTSTAT", "NEEDS-ACTION").strip().upper()
            att_rsvp = params.get("RSVP", "FALSE").strip().upper() == "TRUE"
            attendees.append(
                CalendarAttendee(
                    email=att_email.strip(),
                    cn=att_cn,
                    role=att_role,
                    partstat=att_partstat,
                    rsvp=att_rsvp,
                )
            )

    return CalendarEvent(
        uid=uid,
        summary=summary,
        description=description,
        location=location,
        dtstart=dtstart,
        dtend=dtend,
        dtstart_raw=dtstart_raw,
        dtend_raw=dtend_raw,
        is_all_day=is_all_day,
        organizer_email=organizer_email,
        organizer_cn=organizer_cn,
        organizer_raw=organizer_raw,
        status=status,
        method=calendar_method,
        sequence=sequence,
        recurrence_rule=rrule,
        attendees=attendees,
        raw_ics=raw_ics,
    )


def find_calendar_event(msg: EmailMessage) -> CalendarEvent | None:
    """Inspect an EmailMessage's attachments and body to locate any calendar event."""
    # 1. Inspect attachments for text/calendar or .ics filename
    for att in msg.attachments:
        ctype = (att.content_type or "").lower()
        fname = (att.filename or "").lower()
        data = att.data
        if (
            ctype in ("text/calendar", "application/ics")
            or fname.endswith(".ics")
            or (data and b"BEGIN:VCALENDAR" in data)
        ):
            if data:
                evs = parse_ics(data)
                if evs:
                    return evs[0]
            elif att.local_path:
                try:
                    from pathlib import Path
                    p = Path(att.local_path)
                    if p.exists() and p.is_file():
                        evs = parse_ics(p.read_bytes())
                        if evs:
                            return evs[0]
                except Exception:
                    pass

    # 2. Inspect plain text or html body for embedded VCALENDAR
    for text_candidate in (msg.body_text, msg.body_html):
        if text_candidate and "BEGIN:VCALENDAR" in text_candidate:
            evs = parse_ics(text_candidate)
            if evs:
                return evs[0]

    return None


def build_ics_reply(
    event: CalendarEvent,
    user_email: str,
    user_name: str,
    partstat: str = "ACCEPTED",
    comment: str = "",
) -> str:
    """Generate an RFC 6047 (iMIP) compliant VCALENDAR reply payload.

    partstat should be one of: 'ACCEPTED', 'DECLINED', 'TENTATIVE'.
    """
    valid_partstat = partstat.upper()
    if valid_partstat not in ("ACCEPTED", "DECLINED", "TENTATIVE"):
        valid_partstat = "ACCEPTED"

    now_utc = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cn_param = f';CN="{user_name}"' if user_name else ""
    comment_line = f"COMMENT:{comment.strip()}\r\n" if comment.strip() else ""

    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//EspressoMail//EN",
        "VERSION:2.0",
        "METHOD:REPLY",
        "BEGIN:VEVENT",
        f"UID:{event.uid}",
        f"SEQUENCE:{event.sequence}",
        f"DTSTAMP:{now_utc}",
    ]
    if event.dtstart_raw:
        lines.append(f"DTSTART:{event.dtstart_raw}")
    if event.dtend_raw:
        lines.append(f"DTEND:{event.dtend_raw}")
    if event.summary:
        lines.append(f"SUMMARY:{event.summary}")
    if event.organizer_raw:
        lines.append(f"ORGANIZER:{event.organizer_raw}")
    elif event.organizer_email:
        lines.append(f"ORGANIZER:mailto:{event.organizer_email}")

    lines.append(f"ATTENDEE;PARTSTAT={valid_partstat}{cn_param}:mailto:{user_email}")
    if comment.strip():
        lines.append(f"COMMENT:{comment.strip()}")
    lines.extend([
        "STATUS:CONFIRMED",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ])

    return "\r\n".join(lines)


def build_rsvp_mime_message(
    account: AccountConfig,
    orig_msg: EmailMessage,
    event: CalendarEvent,
    partstat: str = "ACCEPTED",
    comment: str = "",
) -> tuple[StdlibEmailMessage, EmailMessage]:
    """Construct both standard library and domain EmailMessage for an iMIP RSVP reply."""
    status_label = {
        "ACCEPTED": "Accepted",
        "DECLINED": "Declined",
        "TENTATIVE": "Tentative",
    }.get(partstat.upper(), "Accepted")

    verb = {
        "ACCEPTED": "accepted",
        "DECLINED": "declined",
        "TENTATIVE": "tentatively accepted",
    }.get(partstat.upper(), "accepted")

    recipient = event.organizer_email or orig_msg.sender_email
    subject = f"{status_label}: {event.summary}"

    body_lines = [
        f"{account.full_name or account.email} has {verb} the invitation to: {event.summary}",
        "",
        f"When:  {event.formatted_time_range()}",
    ]
    if event.location:
        body_lines.append(f"Where: {event.location}")
    if comment.strip():
        body_lines.extend(["", f"Note: {comment.strip()}"])

    body_text = "\n".join(body_lines)
    reply_ics_str = build_ics_reply(
        event=event,
        user_email=account.email,
        user_name=account.full_name or account.email,
        partstat=partstat,
        comment=comment,
    )
    reply_ics_bytes = reply_ics_str.encode("utf-8")

    # Construct StdlibEmailMessage
    mime_msg = StdlibEmailMessage()
    domain = account.email.split("@")[-1] if "@" in account.email else "espressomail.local"
    msg_id = f"<{uuid.uuid4().hex}@{domain}>"
    now = datetime.now(tz=timezone.utc)

    sender_formatted = f"{account.full_name} <{account.email}>" if account.full_name else account.email
    mime_msg["From"] = sender_formatted
    mime_msg["To"] = recipient
    mime_msg["Subject"] = subject
    mime_msg["Date"] = email.utils.format_datetime(now)
    mime_msg["Message-ID"] = msg_id
    mime_msg["User-Agent"] = "EspressoMail/0.1.0 (Espresso TUI - iMIP)"

    if orig_msg.message_id:
        mime_msg["In-Reply-To"] = orig_msg.message_id
        mime_msg["References"] = orig_msg.message_id

    mime_msg.set_content(body_text)
    mime_msg.add_attachment(
        reply_ics_bytes,
        maintype="text",
        subtype="calendar",
        filename="invite.ics",
        params={"method": "REPLY", "charset": "UTF-8"},
    )

    local_msg_id = f"{account.id}:Sent:{uuid.uuid4().hex[:10]}"
    att = Attachment(
        id=f"{local_msg_id}:att:invite.ics",
        message_id=local_msg_id,
        filename="invite.ics",
        content_type="text/calendar; method=REPLY",
        size_bytes=len(reply_ics_bytes),
        data=reply_ics_bytes,
    )

    domain_msg = EmailMessage(
        id=local_msg_id,
        account_id=account.id,
        folder="Sent",
        message_id=msg_id,
        in_reply_to=orig_msg.message_id,
        references=[orig_msg.message_id] if orig_msg.message_id else [],
        subject=subject,
        sender_name=account.full_name,
        sender_email=account.email,
        recipients_to=[recipient],
        date_iso=now.isoformat(),
        timestamp=now.timestamp(),
        body_text=body_text,
        attachments=[att],
        is_read=True,
    )

    return mime_msg, domain_msg
