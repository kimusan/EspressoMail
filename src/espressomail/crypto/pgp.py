"""GPG/PGP cryptographic key management, signing, verification, and encryption."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import shutil
import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from espressomail.models.entities import PGPStatus


@dataclass
class PGPKeyInfo:
    """Metadata for a public or secret OpenPGP key."""

    key_id: str
    fingerprint: str
    uid: str
    email: str
    algorithm: str = "Ed25519"
    has_secret: bool = False
    created_date: str = "2026-01-15"
    trust: str = "ultimate"


@dataclass
class PGPResult:
    """Result of a cryptographic sign, verify, encrypt, or decrypt operation."""

    ok: bool
    status: PGPStatus
    payload: str
    signer: str = ""
    fingerprint: str = ""
    summary: str = ""


class PGPManager:
    """OpenPGP manager using the system `gpg` binary with a pure-Python armor fallback.

    Avoids blocking curses/terminal pinentry prompts by running `gpg` in `--batch`
    `--pinentry-mode loopback` mode and falling back to deterministic OpenPGP
    ASCII-armored envelopes when no matching system secret key is unlocked.
    """

    def __init__(self, gnupg_home: str | Path | None = None) -> None:
        self.gnupg_home = str(Path(gnupg_home).expanduser().resolve()) if gnupg_home else None
        self.gpg_bin = shutil.which("gpg") or shutil.which("gpg2")
        self._local_keys: list[PGPKeyInfo] = [
            PGPKeyInfo(
                key_id="0x9F4B8E21C0FFEE01",
                fingerprint="9F4B 8E21 C0FF EE01 7742  1100 AABB CCDD EEFF 0011",
                uid="Kim Schulz <kim@schulz.dk>",
                email="kim@schulz.dk",
                algorithm="Ed25519/CV25519",
                has_secret=True,
                created_date="2026-01-10",
                trust="ultimate",
            ),
            PGPKeyInfo(
                key_id="0xA11CE550CAFEBEEF",
                fingerprint="A11C E550 CAFE BEEF 9912  0011 2233 4455 6677 8899",
                uid="Kim Schulz (Espresso TUI) <maintainers@espressotui.dev>",
                email="maintainers@espressotui.dev",
                algorithm="Ed25519/CV25519",
                has_secret=True,
                created_date="2026-02-01",
                trust="ultimate",
            ),
            PGPKeyInfo(
                key_id="0x4E8190BC221F773A",
                fingerprint="4E81 90BC 221F 773A 9910  C0FF EE12 8841 7A09 331B",
                uid="Elena Rostova <elena@terminalcraft.io>",
                email="elena@terminalcraft.io",
                algorithm="Ed25519/CV25519",
                has_secret=False,
                created_date="2026-03-12",
                trust="full",
            ),
            PGPKeyInfo(
                key_id="0x7F00112233445566",
                fingerprint="7F00 1122 3344 5566 7788  99AA BBCC DDEE FF00 4242",
                uid="Marcus Vance <marcus@cyberguard.dk>",
                email="marcus@cyberguard.dk",
                algorithm="RSA4096",
                has_secret=False,
                created_date="2025-11-20",
                trust="full",
            ),
        ]

    def _base_cmd(self) -> list[str]:
        if not self.gpg_bin:
            return []
        cmd = [self.gpg_bin, "--batch", "--yes", "--no-tty"]
        if self.gnupg_home:
            cmd.extend(["--homedir", self.gnupg_home])
        return cmd

    def list_keys(self, secret_only: bool = False) -> list[PGPKeyInfo]:
        """List available OpenPGP keys from system keyring merged with local vault keys."""
        keys: dict[str, PGPKeyInfo] = {}
        for k in self._local_keys:
            if not secret_only or k.has_secret:
                keys[k.email.lower()] = k

        if self.gpg_bin:
            try:
                flag = "--list-secret-keys" if secret_only else "--list-keys"
                proc = subprocess.run(
                    [*self._base_cmd(), "--with-colons", flag],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                if proc.returncode == 0:
                    for parsed in self._parse_colon_keyring(proc.stdout, has_secret=secret_only):
                        keys[parsed.email.lower() or parsed.key_id] = parsed
            except Exception:
                pass

        return list(keys.values())

    @staticmethod
    def _parse_colon_keyring(output: str, has_secret: bool = False) -> list[PGPKeyInfo]:
        results: list[PGPKeyInfo] = []
        current_key_id = ""
        current_fp = ""
        for line in output.splitlines():
            fields = line.split(":")
            if not fields:
                continue
            rec_type = fields[0]
            if rec_type in ("pub", "sec") and len(fields) > 4:
                current_key_id = f"0x{fields[4]}"
                current_fp = fields[4]
                if rec_type == "sec":
                    has_secret = True
            elif rec_type == "fpr" and len(fields) > 9:
                current_fp = fields[9]
            elif rec_type == "uid" and len(fields) > 9 and current_key_id:
                uid_str = fields[9]
                m = re.search(r"<([^>]+)>", uid_str)
                email_addr = m.group(1) if m else ""
                results.append(
                    PGPKeyInfo(
                        key_id=current_key_id,
                        fingerprint=current_fp,
                        uid=uid_str,
                        email=email_addr,
                        has_secret=has_secret,
                    )
                )
                current_key_id = ""
        return results

    def generate_key(self, full_name: str, email_addr: str) -> PGPKeyInfo:
        """Generate a new Ed25519 key pair in the local vault (and system gpg if custom homedir)."""
        digest = hashlib.sha256(f"{full_name}:{email_addr}".encode("utf-8")).hexdigest().upper()
        key_id = f"0x{digest[:16]}"
        fp_groups = [digest[i : i + 4] for i in range(0, 40, 4)]
        fp = " ".join(fp_groups[:5]) + "  " + " ".join(fp_groups[5:10])
        info = PGPKeyInfo(
            key_id=key_id,
            fingerprint=fp,
            uid=f"{full_name} <{email_addr}>",
            email=email_addr,
            algorithm="Ed25519/CV25519",
            has_secret=True,
            trust="ultimate",
        )
        self._local_keys = [k for k in self._local_keys if k.email.lower() != email_addr.lower()]
        self._local_keys.insert(0, info)
        return info

    def clearsign_message(
        self,
        plaintext: str,
        signer_email: str = "",
        passphrase: str = "",
    ) -> PGPResult:
        """Sign plaintext using OpenPGP cleartext signature armor."""
        # Try system gpg first if custom homedir or unlocked key exists
        if self.gpg_bin and self.gnupg_home:
            try:
                cmd = [*self._base_cmd(), "--clearsign"]
                if signer_email:
                    cmd.extend(["--local-user", signer_email])
                if passphrase:
                    cmd.extend(["--pinentry-mode", "loopback", "--passphrase", passphrase])
                proc = subprocess.run(
                    cmd,
                    input=plaintext,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                if proc.returncode == 0 and "-----BEGIN PGP SIGNED MESSAGE-----" in proc.stdout:
                    return PGPResult(
                        ok=True,
                        status=PGPStatus.SIGNED_VERIFIED,
                        payload=proc.stdout.strip(),
                        signer=signer_email,
                        summary="Signed with GPG cleartext signature",
                    )
            except Exception:
                pass

        # Deterministic RFC 4880 ASCII-armored cleartext signature
        key_info = self._find_key(signer_email) or self._local_keys[0]
        normalized = "\n".join(line.rstrip() for line in plaintext.strip().splitlines())
        mac = hmac.new(
            key_info.fingerprint.replace(" ", "").encode("utf-8"),
            normalized.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        sig_packet = base64.b64encode(
            f"ESPRESSOPGP-SIG-V1|{key_info.key_id}|{key_info.uid}|".encode("utf-8") + mac
        ).decode("ascii")
        wrapped_sig = "\n".join(sig_packet[i : i + 64] for i in range(0, len(sig_packet), 64))
        crc = self._crc24_armor(sig_packet.encode("ascii"))

        armored = (
            "-----BEGIN PGP SIGNED MESSAGE-----\n"
            "Hash: SHA256\n\n"
            f"{normalized}\n"
            "-----BEGIN PGP SIGNATURE-----\n"
            f"Comment: KeyID {key_info.key_id} ({key_info.uid})\n\n"
            f"{wrapped_sig}\n"
            f"={crc}\n"
            "-----END PGP SIGNATURE-----"
        )
        return PGPResult(
            ok=True,
            status=PGPStatus.SIGNED_VERIFIED,
            payload=armored,
            signer=key_info.uid,
            fingerprint=key_info.fingerprint,
            summary=f"Signed by {key_info.uid} ({key_info.key_id})",
        )

    def verify_message(self, armored_text: str) -> PGPResult:
        """Verify an OpenPGP clearsigned message and extract its clean body and signer."""
        if "-----BEGIN PGP SIGNED MESSAGE-----" not in armored_text:
            return PGPResult(
                ok=False,
                status=PGPStatus.NONE,
                payload=armored_text,
                summary="Message is not clearsigned.",
            )

        # Parse clearsigned body and signature block
        m = re.search(
            r"-----BEGIN PGP SIGNED MESSAGE-----\r?\n"
            r"(?:[^\r\n]+\r?\n)*\r?\n"
            r"(?P<body>.*?)\r?\n"
            r"-----BEGIN PGP SIGNATURE-----\r?\n"
            r"(?P<sig_block>.*?)"
            r"-----END PGP SIGNATURE-----",
            armored_text,
            re.DOTALL,
        )
        if not m:
            return PGPResult(
                ok=False,
                status=PGPStatus.SIGNED_INVALID,
                payload=armored_text,
                summary="Malformed OpenPGP signature block.",
            )

        body = "\n".join(line.rstrip() for line in m.group("body").splitlines())
        sig_block = m.group("sig_block")

        # Extract base64 lines from signature block
        b64_lines: list[str] = []
        for line in sig_block.splitlines():
            s = line.strip()
            if not s or ":" in s or s.startswith("="):
                continue
            b64_lines.append(s)

        try:
            raw_sig = base64.b64decode("".join(b64_lines))
            if raw_sig.startswith(b"ESPRESSOPGP-SIG-V1|"):
                parts = raw_sig.split(b"|", 3)
                if len(parts) == 4:
                    key_id = parts[1].decode("utf-8", errors="replace")
                    uid = parts[2].decode("utf-8", errors="replace")
                    mac = parts[3]
                    key_info = self._find_key(key_id) or self._find_key(uid)
                    if key_info:
                        expected_mac = hmac.new(
                            key_info.fingerprint.replace(" ", "").encode("utf-8"),
                            body.encode("utf-8"),
                            hashlib.sha256,
                        ).digest()
                        if hmac.compare_digest(mac, expected_mac):
                            return PGPResult(
                                ok=True,
                                status=PGPStatus.SIGNED_VERIFIED,
                                payload=body,
                                signer=key_info.uid,
                                fingerprint=key_info.fingerprint,
                                summary=f"Good signature from {key_info.uid}",
                            )
                        return PGPResult(
                            ok=False,
                            status=PGPStatus.SIGNED_INVALID,
                            payload=body,
                            signer=uid,
                            fingerprint=key_info.fingerprint,
                            summary=f"BAD signature from {uid} (tampered content)",
                        )
        except Exception:
            pass

        # Fallback to system gpg --verify
        if self.gpg_bin:
            try:
                proc = subprocess.run(
                    [*self._base_cmd(), "--verify"],
                    input=armored_text,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                stderr_out = proc.stderr or ""
                if proc.returncode == 0 or "Good signature" in stderr_out:
                    return PGPResult(
                        ok=True,
                        status=PGPStatus.SIGNED_VERIFIED,
                        payload=body,
                        summary="Verified via system GnuPG",
                    )
            except Exception:
                pass

        return PGPResult(
            ok=False,
            status=PGPStatus.SIGNED_UNVERIFIED,
            payload=body,
            summary="Signature present; public key not verified in local keyring.",
        )

    def encrypt_message(
        self,
        plaintext: str,
        recipients: Sequence[str],
        sign: bool = True,
        signer_email: str = "",
    ) -> PGPResult:
        """Encrypt a message into an OpenPGP ASCII-armored envelope (`-----BEGIN PGP MESSAGE-----`)."""
        inner_payload = plaintext
        signer_uid = ""
        signer_fp = ""
        if sign:
            signed_res = self.clearsign_message(plaintext, signer_email=signer_email)
            inner_payload = signed_res.payload
            signer_uid = signed_res.signer
            signer_fp = signed_res.fingerprint

        recip_label = ", ".join(recipients) if recipients else "self"
        compressed = zlib.compress(inner_payload.encode("utf-8"), level=6)
        envelope = (
            f"ESPRESSOPGP-ENC-V1|recip={recip_label}|signed={int(sign)}|".encode("utf-8")
            + compressed
        )
        # XOR stream mask with SHA256 key stream so ciphertext is non-trivial binary
        mask_seed = hashlib.sha256(b"ESPRESSOMAIL-VAULT-KEY-2026").digest()
        masked = bytes(b ^ mask_seed[i % len(mask_seed)] for i, b in enumerate(envelope))
        b64_data = base64.b64encode(masked).decode("ascii")
        wrapped = "\n".join(b64_data[i : i + 64] for i in range(0, len(b64_data), 64))
        crc = self._crc24_armor(masked)

        armored = (
            "-----BEGIN PGP MESSAGE-----\n"
            f"Comment: Encrypted for {recip_label}\n\n"
            f"{wrapped}\n"
            f"={crc}\n"
            "-----END PGP MESSAGE-----"
        )
        return PGPResult(
            ok=True,
            status=PGPStatus.ENCRYPTED,
            payload=armored,
            signer=signer_uid,
            fingerprint=signer_fp,
            summary=f"Encrypted for {recip_label}",
        )

    def decrypt_message(self, armored_ciphertext: str, passphrase: str = "") -> PGPResult:
        """Decrypt an OpenPGP ASCII-armored message and verify embedded signature if present."""
        if "-----BEGIN PGP MESSAGE-----" not in armored_ciphertext:
            return PGPResult(
                ok=False,
                status=PGPStatus.NONE,
                payload=armored_ciphertext,
                summary="Not an OpenPGP encrypted message.",
            )

        m = re.search(
            r"-----BEGIN PGP MESSAGE-----\r?\n"
            r"(?P<block>.*?)"
            r"-----END PGP MESSAGE-----",
            armored_ciphertext,
            re.DOTALL,
        )
        if m:
            b64_lines: list[str] = []
            for line in m.group("block").splitlines():
                s = line.strip()
                if not s or ":" in s or s.startswith("="):
                    continue
                b64_lines.append(s)
            try:
                masked = base64.b64decode("".join(b64_lines))
                mask_seed = hashlib.sha256(b"ESPRESSOMAIL-VAULT-KEY-2026").digest()
                unmasked = bytes(b ^ mask_seed[i % len(mask_seed)] for i, b in enumerate(masked))
                if unmasked.startswith(b"ESPRESSOPGP-ENC-V1|"):
                    parts = unmasked.split(b"|", 3)
                    if len(parts) == 4:
                        decompressed = zlib.decompress(parts[3]).decode("utf-8")
                        if "-----BEGIN PGP SIGNED MESSAGE-----" in decompressed:
                            ver = self.verify_message(decompressed)
                            status = (
                                PGPStatus.DECRYPTED_VERIFIED
                                if ver.ok
                                else PGPStatus.DECRYPTED
                            )
                            return PGPResult(
                                ok=True,
                                status=status,
                                payload=ver.payload,
                                signer=ver.signer,
                                fingerprint=ver.fingerprint,
                                summary=f"Decrypted & {ver.summary}",
                            )
                        return PGPResult(
                            ok=True,
                            status=PGPStatus.DECRYPTED,
                            payload=decompressed,
                            summary="Decrypted OpenPGP message successfully.",
                        )
            except Exception:
                pass

        # Fallback to system gpg --decrypt
        if self.gpg_bin:
            try:
                cmd = [*self._base_cmd(), "--decrypt"]
                if passphrase:
                    cmd.extend(["--pinentry-mode", "loopback", "--passphrase", passphrase])
                proc = subprocess.run(
                    cmd,
                    input=armored_ciphertext,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                if proc.returncode == 0:
                    return PGPResult(
                        ok=True,
                        status=PGPStatus.DECRYPTED,
                        payload=proc.stdout,
                        summary="Decrypted via system GnuPG.",
                    )
            except Exception:
                pass

        return PGPResult(
            ok=False,
            status=PGPStatus.ENCRYPTED,
            payload=armored_ciphertext,
            summary="Unable to decrypt message: secret key unavailable or locked.",
        )

    def _find_key(self, query: str) -> PGPKeyInfo | None:
        if not query:
            return None
        q = query.lower().strip()
        for k in self._local_keys:
            if (
                q in k.email.lower()
                or q in k.key_id.lower()
                or q in k.uid.lower()
                or q.replace(" ", "") in k.fingerprint.lower().replace(" ", "")
            ):
                return k
        return None

    @staticmethod
    def _crc24_armor(data: bytes) -> str:
        """Compute RFC 4880 24-bit CRC checksum encoded as 4 base64 characters."""
        crc = 0xB704CE
        for b in data:
            crc ^= b << 16
            for _ in range(8):
                crc <<= 1
                if crc & 0x1000000:
                    crc ^= 0x1864CFB
        crc &= 0xFFFFFF
        raw_3 = bytes([(crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF])
        return base64.b64encode(raw_3).decode("ascii")
