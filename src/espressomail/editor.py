"""External editor integration ($EDITOR / $VISUAL) for EspressoMail.

Allows composing emails, drafts, and editing text in external terminal editors
(e.g., nano, vim, nvim, emacs, micro) with safe terminal mode suspension.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Generator

COMMON_EDITORS: tuple[str, ...] = ("auto", "nano", "vim", "nvim", "micro", "emacs")


def get_default_editor() -> str:
    """Return the detected default system editor from environment or standard fallbacks."""
    for env_var in ("VISUAL", "EDITOR"):
        val = os.environ.get(env_var, "").strip()
        if val:
            return val
    # Fallback search for installed editors
    for candidate in ("nano", "vim", "nvim", "vi", "micro", "emacs"):
        if subprocess.run(["which", candidate], capture_output=True).returncode == 0:
            return candidate
    return "nano"


def resolve_editor_command(configured_editor: str = "") -> str:
    """Resolve the effective editor command, respecting user settings and environment."""
    configured = (configured_editor or "").strip()
    if configured and configured.lower() != "auto":
        return configured
    return get_default_editor()


@contextlib.contextmanager
def suspend_terminal() -> Generator[None, None, None]:
    """Temporarily restore canonical terminal mode and screen buffer for external interactive CLI tools."""
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    orig_termios = None

    if is_tty:
        try:
            import termios

            fd = sys.stdin.fileno()
            orig_termios = termios.tcgetattr(fd)

            # Exit alternate screen, show cursor, disable mouse and bracketed paste
            sys.stdout.write("\x1b[?25h\x1b[?1000l\x1b[?1002l\x1b[?1006l\x1b[?2004l\x1b[?1049l\x1b[2J\x1b[H")
            sys.stdout.flush()

            # Restore original terminal mode
            termios.tcsetattr(fd, termios.TCSADRAIN, orig_termios)
        except Exception:
            pass

    try:
        yield
    finally:
        if is_tty and orig_termios is not None:
            try:
                import termios

                fd = sys.stdin.fileno()
                mode = list(orig_termios)
                # iflag: Disable flow control (XON/XOFF) and CR-to-NL translation
                mode[0] = mode[0] & ~(termios.IXON | termios.ICRNL | termios.BRKINT | termios.INPCK | termios.ISTRIP)
                # oflag: keep OPOST and ONLCR enabled for clean output
                mode[1] = mode[1] | (termios.OPOST | termios.ONLCR)
                # cflag: 8-bit characters
                mode[2] = (mode[2] & ~(termios.CSIZE | termios.PARENB)) | termios.CS8
                # lflag: Disable canonical mode, local echo, signals, and extended input
                mode[3] = mode[3] & ~(termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN)
                # vmin = 1, vtime = 0: read blocks until byte available
                mode[6][termios.VMIN] = 1
                mode[6][termios.VTIME] = 0

                termios.tcsetattr(fd, termios.TCSADRAIN, mode)

                # Re-enter alternate screen, hide cursor, re-enable mouse and bracketed paste
                sys.stdout.write("\x1b[?1049h\x1b[2J\x1b[H\x1b[?25l\x1b[?1000h\x1b[?1002h\x1b[?1006h\x1b[?2004h")
                sys.stdout.flush()
            except Exception:
                pass


def edit_text_externally(
    initial_text: str,
    editor_cmd: str = "",
    file_extension: str = ".md",
) -> tuple[str, str | None]:
    """Open initial_text in an external editor and return (updated_text, error_message).

    If the editor exits normally, returns the contents of the edited file and None.
    If an error occurs (editor not found, exited with error code), returns the initial_text
    and an explanatory error message.
    """
    editor = resolve_editor_command(editor_cmd)
    try:
        cmd_args = shlex.split(editor)
    except Exception as e:
        return initial_text, f"Invalid editor command '{editor}': {e}"

    if not cmd_args:
        return initial_text, "No editor configured."

    tmp_file = tempfile.NamedTemporaryFile(
        mode="w+",
        suffix=file_extension,
        prefix="espressomail_draft_",
        delete=False,
        encoding="utf-8",
    )
    tmp_path = Path(tmp_file.name)

    try:
        tmp_file.write(initial_text)
        tmp_file.flush()
        tmp_file.close()

        with suspend_terminal():
            res = subprocess.run(cmd_args + [str(tmp_path)])
            if res.returncode != 0:
                return initial_text, f"Editor '{editor}' exited with non-zero code {res.returncode}."

        updated_text = tmp_path.read_text(encoding="utf-8")
        return updated_text, None

    except FileNotFoundError:
        return initial_text, f"Editor command '{cmd_args[0]}' not found on system PATH."
    except Exception as exc:
        return initial_text, f"External editor failed: {exc}"
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
