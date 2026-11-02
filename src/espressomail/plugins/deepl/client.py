"""DeepL REST API v2 client with text budget splitting, formality, and caching."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)

DEEPL_FREE_API: str = "https://api-free.deepl.com"
DEEPL_PRO_API: str = "https://api.deepl.com"

# DeepL requests return 413 over 128 KiB. We budget 100,000 characters per chunk.
TEXT_BUDGET: int = 100000

COMMON_TARGET_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("EN-GB", "English (British)"),
    ("EN-US", "English (American)"),
    ("DA", "Danish"),
    ("DE", "German"),
    ("ES", "Spanish"),
    ("FR", "French"),
    ("IT", "Italian"),
    ("JA", "Japanese"),
    ("NL", "Dutch"),
    ("PL", "Polish"),
    ("PT-PT", "Portuguese"),
    ("SV", "Swedish"),
    ("UK", "Ukrainian"),
    ("ZH", "Chinese (Simplified)"),
)


@dataclass(frozen=True)
class DeepLTranslationResult:
    """Result of a translation request."""

    texts: tuple[str, ...]
    detected_source_lang: str = ""
    error: str = ""
    success: bool = True


def split_text_into_chunks(
    text: str,
    budget: int = TEXT_BUDGET,
    max_chunk_bytes: int | None = None,
) -> list[str]:
    """Split a long text into chunks smaller than budget, cleanly cutting on paragraph/word breaks."""
    effective_budget = max_chunk_bytes if max_chunk_bytes is not None else budget
    if len(text) <= effective_budget:
        return [text]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > effective_budget:
        # Try cutting on double newline (paragraph boundary)
        cut = remaining.rfind("\n\n", 0, effective_budget)
        if cut == -1 or cut < effective_budget // 2:
            # Fall back to single newline
            cut = remaining.rfind("\n", 0, effective_budget)
        if cut == -1 or cut < effective_budget // 2:
            # Fall back to space boundary
            cut = remaining.rfind(" ", 0, effective_budget)
        if cut <= 0:
            cut = effective_budget

        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()

    if remaining:
        chunks.append(remaining)

    return [c for c in chunks if c]


class DeepLClient:
    """Direct client for DeepL REST API v2."""

    def __init__(
        self,
        auth_key: str,
        target_lang: str = "EN-GB",
        formality: str = "default",
        timeout: float = 15.0,
    ) -> None:
        self.auth_key = auth_key.strip()
        self.target_lang = target_lang.strip().upper()
        self.formality = formality.strip().lower()
        self.timeout = timeout

    @property
    def is_free_tier(self) -> bool:
        return self.auth_key.endswith(":fx")

    @property
    def base_url(self) -> str:
        return DEEPL_FREE_API if self.is_free_tier else DEEPL_PRO_API

    @property
    def api_base_url(self) -> str:
        return f"{self.base_url}/v2"

    @property
    def is_configured(self) -> bool:
        return bool(self.auth_key)

    def translate_texts(
        self,
        texts: Sequence[str],
        target_lang: str | None = None,
        is_html: bool = False,
    ) -> DeepLTranslationResult:
        """Translate a sequence of text strings into the target language."""
        if not self.auth_key:
            return DeepLTranslationResult(
                texts=(), error="DeepL Auth Key is not configured. Set your key in Preferences > Plugins.", success=False
            )

        valid_texts = [t for t in texts if t is not None]
        if not valid_texts:
            return DeepLTranslationResult(texts=(), success=True)

        target = (target_lang or self.target_lang).upper()
        # Ensure country variant for English (DeepL requires EN-GB or EN-US)
        if target == "EN":
            target = "EN-GB"

        # Split texts that exceed budget
        split_requests: list[str] = []
        text_indices: list[tuple[int, int]] = []  # mapping back to original text index
        for idx, t in enumerate(valid_texts):
            chunks = split_text_into_chunks(t, TEXT_BUDGET)
            start_pos = len(split_requests)
            split_requests.extend(chunks)
            text_indices.append((start_pos, len(split_requests)))

        payload: dict[str, Any] = {
            "text": split_requests,
            "target_lang": target,
        }
        if is_html:
            payload["tag_handling"] = "html"
        if self.formality in ("prefer_more", "prefer_less"):
            payload["formality"] = self.formality

        url = f"{self.api_base_url}/translate"
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Authorization": f"DeepL-Auth-Key {self.auth_key}",
                "Content-Type": "application/json",
                "User-Agent": "EspressoMail/DeepL-Plugin",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                translations = resp_data.get("translations", [])
                if not translations:
                    return DeepLTranslationResult(texts=(), error="Empty translation response from DeepL", success=False)

                translated_chunks = [item.get("text", "") for item in translations]
                detected_lang = translations[0].get("detected_source_language", "")

                # Recombine split chunks for each original text
                recombined: list[str] = []
                for start_pos, end_pos in text_indices:
                    parts = translated_chunks[start_pos:end_pos]
                    recombined.append("\n\n".join(parts) if len(parts) > 1 else (parts[0] if parts else ""))

                return DeepLTranslationResult(
                    texts=tuple(recombined),
                    detected_source_lang=detected_lang,
                    success=True,
                )

        except urllib.error.HTTPError as err:
            if err.code == 403:
                msg = "DeepL API returned 403 Forbidden. Invalid DeepL Auth Key. Please verify your Auth Key in Preferences."
            elif err.code == 456:
                msg = "DeepL translation Quota exceeded for your account plan."
            elif err.code == 429:
                msg = "Too many requests to DeepL API. Rate limit reached. Please wait a moment and try again."
            elif err.code in (413, 414):
                msg = "Request body too large for DeepL API."
            else:
                msg = f"DeepL translation failed with HTTP {err.code}: {err.reason}"
            logger.warning("DeepL HTTP error: %s", msg)
            return DeepLTranslationResult(texts=(), error=msg, success=False)
        except urllib.error.URLError as err:
            msg = f"Network connection to DeepL failed: {err.reason}"
            logger.warning("DeepL URLError: %s", msg)
            return DeepLTranslationResult(texts=(), error=msg, success=False)
        except Exception as exc:
            msg = f"Unexpected error communicating with DeepL: {exc}"
            logger.exception("DeepL translation error: %s", msg)
            return DeepLTranslationResult(texts=(), error=msg, success=False)
