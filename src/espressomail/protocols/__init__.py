"""Email protocol implementations (IMAP, SMTP, Sieve, Threading)."""

from espressomail.protocols.imap_client import (
    IMAPClient,
    decode_mime_header,
    parse_address_list,
    parse_rfc5322_bytes,
)
from espressomail.protocols.sieve import (
    ManageSieveClient,
    apply_sieve_rules_locally,
    compile_sieve_script,
    validate_sieve_script,
)
from espressomail.protocols.smtp_client import (
    SMTPClient,
    build_mime_message,
    validate_account_credentials,
)
from espressomail.protocols.threading import build_threads, normalize_subject

__all__ = [
    "IMAPClient",
    "decode_mime_header",
    "parse_address_list",
    "parse_rfc5322_bytes",
    "SMTPClient",
    "build_mime_message",
    "validate_account_credentials",
    "build_threads",
    "normalize_subject",
    "ManageSieveClient",
    "compile_sieve_script",
    "validate_sieve_script",
    "apply_sieve_rules_locally",
]
