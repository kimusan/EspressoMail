"""Fast local fuzzy and structured search engine for EspressoMail."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from espressomail.models.entities import EmailMessage, PGPStatus
from espressomail.storage.database import LocalStore


@dataclass
class SearchQuery:
    """Parsed search query combining structured filters and free-text fuzzy terms."""

    raw: str
    terms: list[str] = field(default_factory=list)
    from_filter: str = ""
    to_filter: str = ""
    subject_filter: str = ""
    body_filter: str = ""
    attachment_filter: str = ""
    folder_filter: str = ""
    account_filter: str = ""
    has_attachment: bool | None = None
    is_unread: bool | None = None
    is_starred: bool | None = None
    is_pgp: bool | None = None


@dataclass
class SearchResult:
    """Ranked search hit for an EmailMessage from SQLite FTS5 or fuzzy matching."""

    message: EmailMessage
    score: float
    matched_fields: list[str] = field(default_factory=list)
    snippet: str = ""


_TOKEN_RE = re.compile(r'(\w+):(?:"([^"]+)"|(\S+))|"([^"]+)"|(\S+)')


def escape_fts5_token(token: str) -> str:
    """Escape or quote a token for safe inclusion in an SQLite FTS5 query."""
    t = token.strip()
    if not t:
        return ""
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        t = t[1:-1].strip()
    clean = t.replace('"', '""')
    if clean.endswith("*"):
        base = clean[:-1]
        return f'"{base}"*' if not base.isalnum() else f"{base}*"
    return f'"{clean}"*' if not clean.isalnum() else f"{clean}*"


def build_fts5_query(sq: SearchQuery) -> str:
    """Convert a parsed SearchQuery into a valid, safe SQLite FTS5 MATCH expression."""
    clauses: list[str] = []

    for term in sq.terms:
        if " " in term:
            clean_phrase = term.replace('"', '""')
            clauses.append(f'"{clean_phrase}"')
        else:
            token = escape_fts5_token(term)
            if token:
                clauses.append(token)

    if sq.subject_filter:
        escaped = escape_fts5_token(sq.subject_filter)
        if escaped:
            clauses.append(f"subject : {escaped}")

    if sq.from_filter:
        escaped = escape_fts5_token(sq.from_filter)
        if escaped:
            clauses.append(f"{{sender_name sender_email}} : {escaped}")

    if sq.to_filter:
        escaped = escape_fts5_token(sq.to_filter)
        if escaped:
            clauses.append(f"recipients : {escaped}")

    if sq.body_filter:
        escaped = escape_fts5_token(sq.body_filter)
        if escaped:
            clauses.append(f"body_text : {escaped}")

    if sq.attachment_filter:
        escaped = escape_fts5_token(sq.attachment_filter)
        if escaped:
            clauses.append(f"attachment_names : {escaped}")

    return " AND ".join(clauses)


def parse_search_query(raw_query: str) -> SearchQuery:
    """Parse a user search query into structured qualifiers and fuzzy text tokens."""
    sq = SearchQuery(raw=raw_query)
    for match in _TOKEN_RE.finditer(raw_query.strip()):
        key, quoted_val, bare_val, phrase, word = match.groups()
        if key:
            k = key.lower()
            val = (quoted_val or bare_val or "").strip().lower()
            if k == "from":
                sq.from_filter = val
            elif k == "to":
                sq.to_filter = val
            elif k in ("subject", "subj"):
                sq.subject_filter = val
            elif k in ("body", "b"):
                sq.body_filter = val
            elif k in ("attachment", "att", "file"):
                sq.attachment_filter = val
            elif k in ("folder", "in"):
                sq.folder_filter = val
            elif k in ("account", "acc"):
                sq.account_filter = val
            elif k == "has" and val in ("attachment", "att", "file", "image"):
                sq.has_attachment = True
            elif k == "is":
                if val == "unread":
                    sq.is_unread = True
                elif val == "read":
                    sq.is_unread = False
                elif val in ("starred", "flagged", "star"):
                    sq.is_starred = True
                elif val in ("pgp", "encrypted", "signed"):
                    sq.is_pgp = True
            else:
                sq.terms.append(f"{k}:{val}")
        else:
            token = (phrase or word or "").strip()
            if token:
                sq.terms.append(token)
    return sq


def fuzzy_score(needle: str, haystack: str) -> float:
    """Compute a fast fuzzy match score (0.0 to 100.0) of needle against haystack.

    Combines exact substring, word-prefix bonus, character subsequence gap penalty,
    and character bigram Dice similarity for typo tolerance.
    """
    if not needle or not haystack:
        return 0.0

    n = needle.lower()
    h = haystack.lower()

    if n == h:
        return 100.0

    # 1. Exact contiguous substring match
    idx = h.find(n)
    if idx != -1:
        # Bonus if match starts at beginning of string or word boundary
        boundary_bonus = 15.0 if (idx == 0 or not h[idx - 1].isalnum()) else 0.0
        length_ratio = min(10.0, (len(n) / max(1, len(h))) * 20.0)
        return min(100.0, 75.0 + boundary_bonus + length_ratio)

    # 2. Subsequence match (characters appear in order, e.g. "esptui" -> "EspressoTUI")
    n_len = len(n)
    h_len = len(h)
    n_idx = 0
    first_pos = -1
    last_pos = -1
    consecutive = 0
    max_consecutive = 0
    prev_match = -2

    for h_idx, ch in enumerate(h):
        if n_idx < n_len and ch == n[n_idx]:
            if first_pos == -1:
                first_pos = h_idx
            last_pos = h_idx
            if h_idx == prev_match + 1:
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                consecutive = 1
            prev_match = h_idx
            n_idx += 1
            if n_idx == n_len:
                break

    if n_idx == n_len and first_pos != -1:
        span = max(1, last_pos - first_pos + 1)
        density = n_len / span
        if density >= 0.35 or max_consecutive >= max(2, n_len - 1):
            return min(74.0, 35.0 + density * 30.0 + (max_consecutive / n_len) * 10.0)

    # 3. Typo-tolerant character bigram overlap (for words >= 4 chars)
    if n_len >= 4:
        n_bigrams = {n[i : i + 2] for i in range(n_len - 1)}
        # Check against individual words in haystack to avoid dilution
        best_dice = 0.0
        for word in re.findall(r"\w+", h):
            if len(word) < 3 or abs(len(word) - n_len) > 3:
                continue
            w_bigrams = {word[i : i + 2] for i in range(len(word) - 1)}
            overlap = len(n_bigrams & w_bigrams)
            if overlap:
                dice = (2.0 * overlap) / (len(n_bigrams) + len(w_bigrams))
                if dice > best_dice:
                    best_dice = dice
        if best_dice >= 0.65:
            return best_dice * 55.0

    return 0.0


class FuzzySearchEngine:
    """Fast local email search combining SQLite FTS5 full-text indexing, structured filters, and fuzzy ranking."""

    def __init__(self, store: LocalStore) -> None:
        self.store = store

    def search(
        self,
        raw_query: str,
        account_id: str = "*",
        include_trashed: bool = False,
        limit: int = 100,
    ) -> list[SearchResult]:
        """Search messages across the local SQLite database using FTS5 and fuzzy ranking."""
        parsed = parse_search_query(raw_query)
        target_account = parsed.account_filter or account_id
        target_folder = parsed.folder_filter or "ALL"

        results: list[SearchResult] = []
        seen_ids: set[str] = set()

        # 1. Try SQLite FTS5 Full-Text Search if enabled and query has text or field qualifiers
        fts_query = build_fts5_query(parsed)
        if hasattr(self.store, "is_fts_enabled") and self.store.is_fts_enabled() and fts_query:
            try:
                fts_hits = self.store.search_messages_fts(
                    fts_query,
                    account_id=target_account,
                    folder=target_folder,
                    include_trashed=include_trashed,
                    limit=limit,
                )
                for msg, score, snippet in fts_hits:
                    # Apply any structured boolean/pgp filters
                    if parsed.has_attachment is True and not msg.has_attachments:
                        continue
                    if parsed.is_unread is True and msg.is_read:
                        continue
                    if parsed.is_unread is False and not msg.is_read:
                        continue
                    if parsed.is_starred is True and not msg.is_starred:
                        continue
                    if parsed.is_pgp is True and msg.pgp_status == PGPStatus.NONE:
                        continue

                    seen_ids.add(msg.id)
                    results.append(
                        SearchResult(
                            message=msg,
                            score=score,
                            matched_fields=["fts"],
                            snippet=snippet,
                        )
                    )
            except Exception:
                pass

        # If FTS satisfies the query and limit, return results immediately
        if len(results) >= limit:
            return results[:limit]

        # 2. Candidate-based Fuzzy Search (as fallback, typo-tolerance, or structured-only filter)
        candidates = self.store.list_messages(
            account_id=target_account,
            folder=target_folder,
            include_trashed=include_trashed,
            limit=1000,
        )

        for msg in candidates:
            if msg.id in seen_ids:
                continue

            # Apply structured filters first
            if parsed.account_filter and parsed.account_filter not in msg.account_id.lower():
                continue
            if parsed.folder_filter and parsed.folder_filter != msg.folder.lower():
                continue
            if parsed.has_attachment is True and not msg.has_attachments:
                continue
            if parsed.is_unread is True and msg.is_read:
                continue
            if parsed.is_unread is False and not msg.is_read:
                continue
            if parsed.is_starred is True and not msg.is_starred:
                continue
            if parsed.is_pgp is True and msg.pgp_status == PGPStatus.NONE:
                continue
            if parsed.from_filter:
                sender_blob = f"{msg.sender_name} {msg.sender_email}".lower()
                if parsed.from_filter not in sender_blob and fuzzy_score(parsed.from_filter, sender_blob) < 45.0:
                    continue
            if parsed.to_filter:
                recip_blob = " ".join(msg.recipients_to + msg.recipients_cc).lower()
                if parsed.to_filter not in recip_blob and fuzzy_score(parsed.to_filter, recip_blob) < 45.0:
                    continue
            if parsed.subject_filter:
                if (
                    parsed.subject_filter not in msg.subject.lower()
                    and fuzzy_score(parsed.subject_filter, msg.subject) < 45.0
                ):
                    continue

            # If only structured filters were provided without free-text terms
            if not parsed.terms and not fts_query:
                seen_ids.add(msg.id)
                results.append(SearchResult(message=msg, score=100.0, matched_fields=["filter"]))
                continue

            if not parsed.terms:
                continue

            total_score = 0.0
            matched_fields: set[str] = set()
            all_terms_matched = True

            for term in parsed.terms:
                subj_s = fuzzy_score(term, msg.subject) * 1.4
                from_s = fuzzy_score(term, f"{msg.sender_name} {msg.sender_email}") * 1.25
                to_s = fuzzy_score(term, " ".join(msg.recipients_to + msg.recipients_cc)) * 1.0
                att_s = (
                    max((fuzzy_score(term, a.filename) for a in msg.attachments), default=0.0) * 1.2
                )
                body_s = fuzzy_score(term, msg.body_text[:4000]) * 0.9

                best_term_score = max(subj_s, from_s, to_s, att_s, body_s)
                if best_term_score < 28.0:
                    all_terms_matched = False
                    break

                total_score += best_term_score
                if subj_s >= 28.0:
                    matched_fields.add("subject")
                if from_s >= 28.0:
                    matched_fields.add("sender")
                if to_s >= 28.0:
                    matched_fields.add("recipient")
                if att_s >= 28.0:
                    matched_fields.add("attachment")
                if body_s >= 28.0:
                    matched_fields.add("body")

            if all_terms_matched:
                avg_score = total_score / len(parsed.terms)
                seen_ids.add(msg.id)
                results.append(
                    SearchResult(
                        message=msg,
                        score=round(avg_score, 2),
                        matched_fields=sorted(matched_fields),
                    )
                )

        # Sort by relevance score descending, then timestamp descending
        results.sort(key=lambda r: (r.score, r.message.timestamp), reverse=True)
        return results[:limit]
