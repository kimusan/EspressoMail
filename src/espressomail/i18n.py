"""Internationalization (i18n) and localization support for EspressoMail.

Loads translations from external JSON files in:
1. Built-in `src/espressomail/locales/<lang>.json`
2. Optional user-contributed `~/.espressomail/locales/<lang>.json` (or custom directories)

Any string not matched in the selected language's JSON file automatically falls back
to English (`en.json`, and then to the original source string).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("espressomail.i18n")

BUILTIN_LOCALES_DIR: Path = Path(__file__).resolve().parent / "locales"
USER_LOCALES_DIR: Path = Path.home() / ".espressomail" / "locales"

DEFAULT_LANGUAGE_LABELS: dict[str, str] = {
    "en": "English",
    "da": "Dansk (Danish)",
    "de": "Deutsch (German)",
    "es": "Español (Spanish)",
}

# Maps lang_code -> { english_key: translated_string }
_CATALOGS: dict[str, dict[str, str]] = {}
LANGUAGE_LABELS: dict[str, str] = dict(DEFAULT_LANGUAGE_LABELS)
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "da", "de", "es")
_CURRENT_LANGUAGE: str = "en"


def _parse_locale_file(path: Path) -> tuple[str, str | None, dict[str, str]]:
    """Read a JSON locale file and return (lang_code, display_name, strings_dict)."""
    code = path.stem.strip().lower()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return code, None, {}

    meta = raw.get("_meta")
    display_name: str | None = None
    if isinstance(meta, dict):
        if isinstance(meta.get("code"), str) and meta["code"].strip():
            code = meta["code"].strip().lower()
        if isinstance(meta.get("name"), str) and meta["name"].strip():
            display_name = meta["name"].strip()

    strings_obj = raw.get("strings", raw)
    strings: dict[str, str] = {}
    if isinstance(strings_obj, dict):
        for k, v in strings_obj.items():
            if k == "_meta":
                continue
            if isinstance(k, str) and isinstance(v, str):
                strings[k] = v

    return code, display_name, strings


def reload_locales(extra_dirs: list[Path] | None = None) -> tuple[str, ...]:
    """Load or reload all `<lang>.json` locale files from built-in and user directories.

    Returns the tuple of discovered language codes (with `'en'` first).
    """
    global SUPPORTED_LANGUAGES

    catalogs: dict[str, dict[str, str]] = {}
    labels: dict[str, str] = dict(DEFAULT_LANGUAGE_LABELS)

    search_dirs: list[Path] = [BUILTIN_LOCALES_DIR, USER_LOCALES_DIR]
    if extra_dirs:
        for d in extra_dirs:
            if d not in search_dirs:
                search_dirs.append(d)

    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for json_file in sorted(directory.glob("*.json")):
            try:
                code, display_name, strings = _parse_locale_file(json_file)
            except Exception as exc:
                logger.warning("Failed to load locale file %s: %s", json_file, exc)
                continue
            if not code:
                continue
            catalogs.setdefault(code, {}).update(strings)
            if display_name:
                labels[code] = display_name
            elif code not in labels:
                labels[code] = code.upper()

    _CATALOGS.clear()
    _CATALOGS.update(catalogs)

    LANGUAGE_LABELS.clear()
    LANGUAGE_LABELS.update(labels)

    ordered: list[str] = ["en", "da", "de", "es"]
    for code in sorted(catalogs.keys()):
        if code not in ordered:
            ordered.append(code)
    SUPPORTED_LANGUAGES = tuple(ordered)
    return SUPPORTED_LANGUAGES


def get_available_languages() -> tuple[str, ...]:
    """Return all currently loaded language codes."""
    if not _CATALOGS:
        reload_locales()
    return SUPPORTED_LANGUAGES


def get_language_label(lang: str) -> str:
    """Return the human-readable display label for a language code."""
    if not _CATALOGS:
        reload_locales()
    code = normalize_language(lang)
    return LANGUAGE_LABELS.get(code, code.upper())


def normalize_language(lang: str | None) -> str:
    """Validate and return normalized language code, defaulting to system locale or 'en'."""
    if not _CATALOGS:
        reload_locales()
    if not lang or lang == "auto":
        try:
            from espresso.i18n import detect_system_locale

            sys_code = detect_system_locale()
            if sys_code in _CATALOGS or sys_code in SUPPORTED_LANGUAGES:
                return sys_code
            if sys_code and sys_code[:2] in SUPPORTED_LANGUAGES:
                return sys_code[:2]
        except Exception:
            pass
        return "en"
    clean = lang.strip().lower()
    if clean in _CATALOGS or clean in SUPPORTED_LANGUAGES:
        return clean
    short = clean[:2]
    if short in _CATALOGS or short in SUPPORTED_LANGUAGES:
        return short
    return "en"


def validate_catalogs(locales_dir: Path | str | None = None) -> list[str]:
    """Validate catalog files using espresso.i18n.check_catalogs."""
    try:
        from espresso.i18n import check_catalogs

        target_dir = Path(locales_dir) if locales_dir else BUILTIN_LOCALES_DIR
        issues = check_catalogs(target_dir, base_locale="en")
        return [str(issue) for issue in issues]
    except Exception as exc:
        return [f"check_catalogs failed: {exc}"]


def get_language() -> str:
    """Return the active UI language code."""
    return _CURRENT_LANGUAGE


def set_language(lang: str | None) -> str:
    """Set the active UI language code and return the normalized code."""
    global _CURRENT_LANGUAGE
    _CURRENT_LANGUAGE = normalize_language(lang)
    return _CURRENT_LANGUAGE


def tr(text: str, lang: str | None = None, **kwargs: Any) -> str:
    """Translate a UI string into the active (or specified) language.

    Looks up `text` in `<lang>.json`; if not found, falls back to `en.json`
    and then to the original `text` string.
    """
    if not _CATALOGS:
        reload_locales()
    active = normalize_language(lang) if lang is not None else _CURRENT_LANGUAGE
    en_catalog = _CATALOGS.get("en", {})
    default_en = en_catalog.get(text, text)
    if active == "en":
        res = default_en
    else:
        lang_catalog = _CATALOGS.get(active, {})
        res = lang_catalog.get(text, default_en)
    if kwargs:
        try:
            return res.format(**kwargs)
        except Exception:
            return res
    return res


# Initialize built-in and user locales on import
reload_locales()
