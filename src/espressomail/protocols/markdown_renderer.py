"""RFC 2822 / MIME Markdown to HTML email compiler and renderer.

Translates CommonMark / GitHub Flavored Markdown into self-contained, inline-styled
rich HTML email markup suitable for modern email clients (Gmail, Apple Mail, Outlook)
alongside fallback plain text in multipart/alternative MIME messages.
"""

from __future__ import annotations

import html
import re
from typing import Sequence

try:
    import markdown as py_markdown

    _HAS_PY_MARKDOWN = True
except ImportError:
    py_markdown = None
    _HAS_PY_MARKDOWN = False


EMAIL_INLINE_STYLES = {
    "body": (
        "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; "
        "font-size: 15px; line-height: 1.6; color: #24292e; background-color: #ffffff; "
        "margin: 0; padding: 20px; max-width: 680px;"
    ),
    "h1": (
        "font-size: 22px; font-weight: 700; color: #111827; margin-top: 1.2em; margin-bottom: 0.6em; "
        "border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; line-height: 1.3;"
    ),
    "h2": (
        "font-size: 18px; font-weight: 600; color: #1f2937; margin-top: 1.1em; margin-bottom: 0.5em; "
        "border-bottom: 1px solid #f3f4f6; padding-bottom: 4px; line-height: 1.35;"
    ),
    "h3": "font-size: 16px; font-weight: 600; color: #374151; margin-top: 1em; margin-bottom: 0.4em; line-height: 1.4;",
    "h4": "font-size: 15px; font-weight: 600; color: #4b5563; margin-top: 0.8em; margin-bottom: 0.3em;",
    "p": "margin-top: 0; margin-bottom: 1em; line-height: 1.6;",
    "blockquote": (
        "border-left: 4px solid #6366f1; margin: 0 0 1em 0; padding: 6px 16px; "
        "color: #4b5563; background-color: #f8fafc; font-style: italic;"
    ),
    "ul": "margin-top: 0; margin-bottom: 1em; padding-left: 24px;",
    "ol": "margin-top: 0; margin-bottom: 1em; padding-left: 24px;",
    "li": "margin-bottom: 0.35em;",
    "code_inline": (
        "font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; "
        "font-size: 13px; background-color: #f1f5f9; color: #0f172a; padding: 2px 5px; "
        "border-radius: 4px; border: 1px solid #e2e8f0;"
    ),
    "pre": (
        "font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; "
        "font-size: 13px; line-height: 1.45; background-color: #f8fafc; color: #1e293b; "
        "border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; overflow-x: auto; "
        "margin-top: 0; margin-bottom: 1.2em;"
    ),
    "a": "color: #4f46e5; text-decoration: underline; font-weight: 500;",
    "hr": "border: 0; border-top: 1px solid #e5e7eb; margin: 1.8em 0;",
    "table": "border-collapse: collapse; width: 100%; margin-top: 0; margin-bottom: 1.2em; font-size: 14px;",
    "th": "border: 1px solid #e2e8f0; background-color: #f8fafc; padding: 8px 12px; font-weight: 600; text-align: left;",
    "td": "border: 1px solid #e2e8f0; padding: 8px 12px; text-align: left;",
    "img": "max-width: 100%; height: auto; border-radius: 4px; margin: 8px 0;",
}


def _inline_style_html(raw_html: str) -> str:
    """Inject robust inline styles into HTML tags for optimal email client rendering."""
    # Headings
    for h in ("h1", "h2", "h3", "h4"):
        raw_html = re.sub(
            rf"<{h}(\s*[^>]*)>",
            rf'<{h}\1 style="{EMAIL_INLINE_STYLES[h]}">',
            raw_html,
            flags=re.IGNORECASE,
        )

    # Paragraphs
    raw_html = re.sub(
        r"<p(\s*[^>]*)>",
        rf'<p\1 style="{EMAIL_INLINE_STYLES["p"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )

    # Blockquotes
    raw_html = re.sub(
        r"<blockquote(\s*[^>]*)>",
        rf'<blockquote\1 style="{EMAIL_INLINE_STYLES["blockquote"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )

    # Lists
    raw_html = re.sub(
        r"<ul(\s*[^>]*)>",
        rf'<ul\1 style="{EMAIL_INLINE_STYLES["ul"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )
    raw_html = re.sub(
        r"<ol(\s*[^>]*)>",
        rf'<ol\1 style="{EMAIL_INLINE_STYLES["ol"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )
    raw_html = re.sub(
        r"<li(\s*[^>]*)>",
        rf'<li\1 style="{EMAIL_INLINE_STYLES["li"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )

    # Pre & Code blocks
    raw_html = re.sub(
        r"<pre(\s*[^>]*)>",
        rf'<pre\1 style="{EMAIL_INLINE_STYLES["pre"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )

    # Links
    raw_html = re.sub(
        r"<a(\s+href=[^>]*)>",
        rf'<a\1 style="{EMAIL_INLINE_STYLES["a"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )

    # Tables
    raw_html = re.sub(
        r"<table(\s*[^>]*)>",
        rf'<table\1 style="{EMAIL_INLINE_STYLES["table"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )
    raw_html = re.sub(
        r"<th(\s*[^>]*)>",
        rf'<th\1 style="{EMAIL_INLINE_STYLES["th"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )
    raw_html = re.sub(
        r"<td(\s*[^>]*)>",
        rf'<td\1 style="{EMAIL_INLINE_STYLES["td"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )

    # Horizontal Rules
    raw_html = re.sub(
        r"<hr(\s*\/?)>",
        rf'<hr\1 style="{EMAIL_INLINE_STYLES["hr"]}">',
        raw_html,
        flags=re.IGNORECASE,
    )

    # Inline Code
    def _style_code(match: re.Match[str]) -> str:
        attrs = match.group(1) or ""
        content = match.group(2)
        return f'<code{attrs} style="{EMAIL_INLINE_STYLES["code_inline"]}">{content}</code>'

    raw_html = re.sub(r"<code([^>]*)>(.*?)</code>", _style_code, raw_html, flags=re.DOTALL)
    # Ensure code tags inside <pre> have clean styling without box borders
    raw_html = re.sub(
        r"(<pre[^>]*>.*?<code[^>]*style=\")[^\"]*(\"[^>]*>)",
        r"\1background-color: transparent; border: none; padding: 0;\2",
        raw_html,
        flags=re.DOTALL,
    )

    return raw_html


def _pure_python_markdown_to_html(text: str) -> str:
    """Zero-dependency pure-Python Markdown to HTML compiler."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []

    in_code_block = False
    code_lines: list[str] = []
    in_list = ""  # "ul" or "ol"
    in_quote = False
    quote_lines: list[str] = []
    table_lines: list[str] = []

    def _flush_list() -> None:
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = ""

    def _flush_quote() -> None:
        nonlocal in_quote, quote_lines
        if in_quote:
            inner_html = _pure_python_markdown_to_html("\n".join(quote_lines))
            out.append(f"<blockquote>{inner_html}</blockquote>")
            in_quote = False
            quote_lines = []

    def _flush_table() -> None:
        nonlocal table_lines
        if not table_lines:
            return
        rows: list[list[str]] = []
        for tl in table_lines:
            cells = [c.strip() for c in tl.strip().strip("|").split("|")]
            rows.append(cells)
        table_lines = []
        if len(rows) >= 2 and all(re.match(r"^:?-+:?$", c) for c in rows[1]):
            # Header row + divider
            headers = rows[0]
            data_rows = rows[2:]
            t_buf = ["<table><thead><tr>"]
            for h in headers:
                t_buf.append(f"<th>{_format_inlines(h)}</th>")
            t_buf.append("</tr></thead><tbody>")
            for dr in data_rows:
                t_buf.append("<tr>")
                for cell in dr:
                    t_buf.append(f"<td>{_format_inlines(cell)}</td>")
                t_buf.append("</tr>")
            t_buf.append("</tbody></table>")
            out.append("".join(t_buf))
        else:
            for r in rows:
                out.append(f"<p>{_format_inlines(' | '.join(r))}</p>")

    def _format_inlines(line_text: str) -> str:
        s = html.escape(line_text)
        # Inline code `code`
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        # Bold **text** or __text__
        s = re.sub(r"(\*\*|__)(.*?)\1", r"<strong>\2</strong>", s)
        # Italic *text* or _text_
        s = re.sub(r"(\*|_)(.*?)\1", r"<em>\2</em>", s)
        # Strikethrough ~~text~~
        s = re.sub(r"~~(.*?)~~", r"<del>\1</del>", s)
        # Links [text](url)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+|mailto:[^\s\)]+)\)", r'<a href="\2">\1</a>', s)
        return s

    for line in lines:
        stripped = line.strip()

        # Code block fence ```
        if stripped.startswith("```"):
            if in_code_block:
                in_code_block = False
                escaped_code = html.escape("\n".join(code_lines))
                out.append(f"<pre><code>{escaped_code}</code></pre>")
                code_lines = []
            else:
                _flush_list()
                _flush_quote()
                _flush_table()
                in_code_block = True
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Table rows (| a | b |)
        if "|" in line and (line.strip().startswith("|") or len(line.split("|")) >= 3):
            _flush_list()
            _flush_quote()
            table_lines.append(line)
            continue
        elif table_lines:
            _flush_table()

        # Blockquote (> text)
        if stripped.startswith(">"):
            _flush_list()
            in_quote = True
            quote_lines.append(stripped.lstrip(">").strip())
            continue
        elif in_quote:
            _flush_quote()

        # Empty line
        if not stripped:
            _flush_list()
            _flush_quote()
            _flush_table()
            continue

        # Horizontal rule (--- or ***)
        if re.match(r"^(\-{3,}|\*{3,}|_{3,})$", stripped):
            _flush_list()
            out.append("<hr>")
            continue

        # Headings (# .. ####)
        h_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if h_match:
            _flush_list()
            lvl = len(h_match.group(1))
            h_text = _format_inlines(h_match.group(2))
            out.append(f"<h{lvl}>{h_text}</h{lvl}>")
            continue

        # Unordered list (- or * or +)
        ul_match = re.match(r"^(\*|-|\+)\s+(.*)$", stripped)
        if ul_match:
            if in_list != "ul":
                _flush_list()
                in_list = "ul"
                out.append("<ul>")
            li_text = _format_inlines(ul_match.group(2))
            out.append(f"<li>{li_text}</li>")
            continue

        # Ordered list (1. 2.)
        ol_match = re.match(r"^\d+\.\s+(.*)$", stripped)
        if ol_match:
            if in_list != "ol":
                _flush_list()
                in_list = "ol"
                out.append("<ol>")
            li_text = _format_inlines(ol_match.group(1))
            out.append(f"<li>{li_text}</li>")
            continue

        # Normal paragraph line
        _flush_list()
        out.append(f"<p>{_format_inlines(stripped)}</p>")

    if in_code_block:
        out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    _flush_list()
    _flush_quote()
    _flush_table()

    return "\n".join(out)


def markdown_to_html_email(markdown_text: str, title: str = "") -> str:
    """Convert Markdown into a standalone, email-safe HTML document with inline styles."""
    if not markdown_text or not markdown_text.strip():
        return ""

    if _HAS_PY_MARKDOWN and py_markdown is not None:
        try:
            raw_html = py_markdown.markdown(
                markdown_text,
                extensions=["extra", "sane_lists", "nl2br"],
            )
        except Exception:
            raw_html = _pure_python_markdown_to_html(markdown_text)
    else:
        raw_html = _pure_python_markdown_to_html(markdown_text)

    styled_body = _inline_style_html(raw_html)

    doc_title = html.escape(title) if title else "Message"
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"  <title>{doc_title}</title>\n"
        "</head>\n"
        f'<body style="{EMAIL_INLINE_STYLES["body"]}">\n'
        f"{styled_body}\n"
        "</body>\n</html>"
    )


def is_markdown_content(text: str) -> bool:
    """Detect if a text contains CommonMark or GitHub Flavored Markdown constructs."""
    if not text:
        return False
    patterns = [
        r"^#{1,6}\s+\S+",             # Headings
        r"```[a-zA-Z0-9_-]*\n",       # Fenced code blocks
        r"`[^`\n]+`",                  # Inline code
        r"^\s*[-*+]\s+\S+",           # Unordered lists
        r"^\s*\d+\.\s+\S+",           # Ordered lists
        r"^\s*>\s+\S+",               # Blockquotes
        r"\[[^\]]+\]\([^\)]+\)",      # Links
        r"\*\*[^*]+\*\*",             # Bold
        r"~~[^~]+~~",                 # Strikethrough
        r"\|.+\|.+\|",                # Tables
        r"^---+$",                     # Horizontal rules
    ]
    for p in patterns:
        if re.search(p, text, flags=re.MULTILINE):
            return True
    return False
