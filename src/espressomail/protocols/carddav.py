"""CardDAV protocol client (RFC 6352) and vCard (RFC 6350 / RFC 2426) parser for contact synchronization."""

from __future__ import annotations

import base64
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from espressomail.models.entities import Contact


def parse_vcards(vcard_text: str) -> list[Contact]:
    """Parse one or multiple vCard (2.1/3.0/4.0) blocks from a raw string into Contact objects."""
    contacts: list[Contact] = []
    # Split text into individual VCARD blocks
    blocks = re.findall(r"BEGIN:VCARD.*?END:VCARD", vcard_text, re.DOTALL | re.IGNORECASE)
    if not blocks and ("FN:" in vcard_text or "EMAIL" in vcard_text):
        blocks = [vcard_text]

    for block in blocks:
        # Unfold lines (RFC 6350 folded lines start with space or tab)
        unfolded = re.sub(r"\r?\n[ \t]", "", block)
        lines = [line.strip() for line in unfolded.splitlines() if line.strip()]

        fn = ""
        n_family = ""
        n_given = ""
        email = ""
        org = ""
        uid = ""
        pgp = ""
        notes = ""

        for line in lines:
            if ":" not in line:
                continue
            prop_part, _, val_part = line.partition(":")
            prop_name = prop_part.split(";")[0].strip().upper()
            val = val_part.strip()

            if prop_name == "FN" and not fn:
                fn = val
            elif prop_name == "N":
                n_parts = val.split(";")
                if len(n_parts) >= 1:
                    n_family = n_parts[0].strip()
                if len(n_parts) >= 2:
                    n_given = n_parts[1].strip()
            elif prop_name == "EMAIL" and not email:
                email = val
            elif prop_name == "ORG" and not org:
                # Organizations can be hierarchy (e.g. Org;Dept)
                org = val.split(";")[0].strip()
            elif prop_name == "NOTE" and not notes:
                notes = val
            elif prop_name == "UID" and not uid:
                uid = val
            elif prop_name in ("KEY", "X-PGP-KEY", "X-OPENPGP-KEY") and not pgp:
                pgp = val

        # Reconstruct name if FN was not provided
        if not fn and (n_given or n_family):
            fn = f"{n_given} {n_family}".strip()
        if not fn and email:
            fn = email.split("@")[0]

        if email:
            c_id = uid if uid else f"contact:{uuid.uuid4().hex[:10]}"
            contacts.append(
                Contact(
                    id=c_id,
                    name=fn or email,
                    email=email,
                    organization=org,
                    notes=notes,
                    pgp_fingerprint=pgp,
                    favorite=False,
                    frequency=1,
                )
            )

    return contacts


def format_vcard(contact: Contact) -> str:
    """Format a Contact object as an RFC 6350 / RFC 2426 vCard 3.0 text block."""
    uid = contact.id if contact.id else f"contact:{uuid.uuid4().hex[:10]}"
    name = contact.name or contact.email
    parts = name.split(None, 1)
    given = parts[0] if parts else ""
    family = parts[1] if len(parts) > 1 else ""

    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"UID:{uid}",
        f"FN:{name}",
        f"N:{family};{given};;;",
        f"EMAIL;TYPE=INTERNET:{contact.email}",
    ]
    if contact.organization:
        lines.append(f"ORG:{contact.organization}")
    if contact.notes:
        lines.append(f"NOTE:{contact.notes}")
    if contact.pgp_fingerprint:
        lines.append(f"X-PGP-KEY:{contact.pgp_fingerprint}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


class CardDAVClient:
    """CardDAV protocol client for querying, downloading, and uploading contacts."""

    def __init__(
        self,
        url: str,
        username: str = "",
        password: str = "",
        timeout: float = 10.0,
    ) -> None:
        self.url = url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.timeout = timeout

    def _build_request(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        content_type: str = "text/xml; charset=utf-8",
        depth: str | None = None,
    ) -> urllib.request.Request:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", "EspressoMail/1.0 (TUI CardDAV)")
        if content_type:
            req.add_header("Content-Type", content_type)
        if depth is not None:
            req.add_header("Depth", depth)
        if self.username and self.password:
            auth_str = f"{self.username}:{self.password}"
            b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {b64_auth}")
        return req

    def test_connection(self) -> tuple[bool, str]:
        """Test connection and authentication against the CardDAV server."""
        if not self.url or not self.url.startswith(("http://", "https://")):
            return False, "Invalid CardDAV URL (must start with http:// or https://)."
        try:
            req = self._build_request("PROPFIND", self.url, depth="0")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                code = resp.getcode() if hasattr(resp, "getcode") else getattr(resp, "status", getattr(resp, "code", 200))
                if code in (200, 204, 207):
                    return True, f"✔ Successfully connected to CardDAV server (HTTP {code})."
                return False, f"Server returned unexpected HTTP status: {code}"
        except urllib.error.HTTPError as he:
            if he.code in (401, 403):
                return False, f"CardDAV authentication failed (HTTP {he.code} Unauthorized)."
            if he.code == 405:
                try:
                    opt_req = self._build_request("OPTIONS", self.url)
                    with urllib.request.urlopen(opt_req, timeout=self.timeout) as opt_resp:
                        opt_code = opt_resp.getcode() if hasattr(opt_resp, "getcode") else getattr(opt_resp, "status", getattr(opt_resp, "code", 200))
                        if opt_code in (200, 204, 207):
                            return True, f"✔ Successfully connected to CardDAV server (HTTP {opt_code})."
                except Exception:
                    pass
            return False, f"CardDAV HTTP error: {he.code} {he.reason}"
        except Exception as ex:
            return False, f"CardDAV connection failed: {ex}"

    def fetch_contacts(self) -> list[Contact]:
        """Query the CardDAV address book collection and return parsed Contact objects."""
        if not self.url.startswith(("http://", "https://")):
            return []

        # 1. First attempt: CardDAV REPORT addressbook-multiget or addressbook-query
        report_body = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<C:addressbook-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
            "  <D:prop>"
            "    <D:getetag/>"
            "    <C:address-data/>"
            "  </D:prop>"
            "</C:addressbook-query>"
        ).encode("utf-8")

        contacts: list[Contact] = []
        try:
            req = self._build_request(
                "REPORT",
                self.url,
                data=report_body,
                content_type="application/xml; charset=utf-8",
                depth="1",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_xml = resp.read().decode("utf-8", errors="replace")
                # Parse vCards from XML response
                # Look for address-data elements or raw VCARD blocks
                found_vcards = parse_vcards(raw_xml)
                if found_vcards:
                    return found_vcards
        except Exception:
            pass

        # 2. Second attempt: PROPFIND Depth: 1 to find individual .vcf resources
        propfind_body = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<D:propfind xmlns:D="DAV:">'
            "  <D:prop>"
            "    <D:getcontenttype/>"
            "    <D:resourcetype/>"
            "  </D:prop>"
            "</D:propfind>"
        ).encode("utf-8")

        vcf_urls: list[str] = []
        try:
            req = self._build_request(
                "PROPFIND",
                self.url,
                data=propfind_body,
                content_type="application/xml; charset=utf-8",
                depth="1",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_xml = resp.read().decode("utf-8", errors="replace")
                # Check for any inline vcards first
                inline = parse_vcards(raw_xml)
                if inline:
                    return inline

                # Extract hrefs ending with .vcf
                root = ET.fromstring(raw_xml)
                for elem in root.iter():
                    if elem.tag.endswith("href") and elem.text:
                        href = elem.text.strip()
                        if href.endswith(".vcf") or "/card/" in href:
                            full_url = urllib.parse.urljoin(self.url, href)
                            if full_url != self.url and full_url not in vcf_urls:
                                vcf_urls.append(full_url)
        except Exception:
            pass

        # 3. Fetch each discovered .vcf URL
        for v_url in vcf_urls[:100]:
            try:
                g_req = self._build_request("GET", v_url, content_type="")
                with urllib.request.urlopen(g_req, timeout=self.timeout) as g_resp:
                    v_text = g_resp.read().decode("utf-8", errors="replace")
                    contacts.extend(parse_vcards(v_text))
            except Exception:
                continue

        # 4. If direct collection URL itself returns text/vcard or text/plain
        if not contacts:
            try:
                d_req = self._build_request("GET", self.url, content_type="")
                with urllib.request.urlopen(d_req, timeout=self.timeout) as d_resp:
                    d_text = d_resp.read().decode("utf-8", errors="replace")
                    contacts = parse_vcards(d_text)
            except Exception:
                pass

        return contacts

    def upload_contact(self, contact: Contact) -> bool:
        """Upload or update a contact on the CardDAV server via HTTP PUT."""
        if not self.url.startswith(("http://", "https://")):
            return False
        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", contact.id or uuid.uuid4().hex[:10])
        vcf_url = urllib.parse.urljoin(self.url, f"{clean_id}.vcf")
        vcard_data = format_vcard(contact).encode("utf-8")

        try:
            req = self._build_request(
                "PUT",
                vcf_url,
                data=vcard_data,
                content_type="text/vcard; charset=utf-8",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.getcode() in (200, 201, 204)
        except Exception:
            return False

    def sync_contacts(self, store: Any) -> tuple[int, int, str]:
        """Perform bidirectional synchronization between LocalStore and CardDAV server.

        Returns (downloaded_count, uploaded_count, status_message).
        """
        try:
            remote_contacts = self.fetch_contacts()
        except Exception as exc:
            return 0, 0, f"CardDAV fetch failed: {exc}"

        downloaded = 0
        local_contacts = store.list_contacts()
        local_by_email = {c.email.lower(): c for c in local_contacts if c.email}

        # 1. Merge remote contacts into local store
        for rc in remote_contacts:
            if not rc.email:
                continue
            existing = local_by_email.get(rc.email.lower())
            if existing is None:
                store.save_contact(rc)
                downloaded += 1
            else:
                # Update name or org if remote has more detail
                updated = False
                if rc.name and rc.name != existing.email and existing.name == existing.email:
                    existing.name = rc.name
                    updated = True
                if rc.organization and not existing.organization:
                    existing.organization = rc.organization
                    updated = True
                if updated:
                    store.save_contact(existing)
                    downloaded += 1

        # 2. Upload local contacts that do not exist remotely
        remote_emails = {rc.email.lower() for rc in remote_contacts if rc.email}
        uploaded = 0
        for lc in local_contacts:
            if lc.email and lc.email.lower() not in remote_emails:
                ok = self.upload_contact(lc)
                if ok:
                    uploaded += 1

        summary = f"✔ CardDAV Sync: {downloaded} contact(s) imported, {uploaded} contact(s) uploaded."
        return downloaded, uploaded, summary
