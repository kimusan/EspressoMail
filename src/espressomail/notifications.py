"""Cross-platform desktop notifications and sound alerts for EspressoMail."""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import sys
from typing import Sequence

from espressomail.models.entities import EmailMessage

logger = logging.getLogger(__name__)


def is_notification_supported() -> bool:
    """Return True if a desktop notification daemon/tool is available on the current OS."""
    system = platform.system().lower()
    if system in ("linux", "freebsd", "openbsd", "netbsd"):
        return bool(shutil.which("notify-send"))
    elif system == "darwin":
        return bool(shutil.which("osascript"))
    elif system == "windows":
        return bool(shutil.which("powershell.exe") or shutil.which("powershell"))
    return False


def play_alert_sound() -> bool:
    """Play a short system alert sound or trigger terminal bell."""
    try:
        # Check Linux sound utilities first
        if shutil.which("canberra-gtk-play"):
            subprocess.Popen(
                ["canberra-gtk-play", "-i", "message-new-email"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        elif shutil.which("paplay"):
            for sound_path in (
                "/usr/share/sounds/freedesktop/stereo/message-new-email.oga",
                "/usr/share/sounds/freedesktop/stereo/complete.oga",
                "/usr/share/sounds/sound-icons/prompt.wav",
            ):
                from pathlib import Path
                if Path(sound_path).exists():
                    subprocess.Popen(
                        ["paplay", sound_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return True

        # Fallback to terminal bell
        sys.stdout.write("\a")
        sys.stdout.flush()
        return True
    except Exception as exc:
        logger.debug("Could not play notification sound: %s", exc)
        return False


def send_desktop_notification(
    title: str,
    body: str,
    urgency: str = "normal",
    sound: bool = False,
    icon: str = "mail-message-new",
    app_name: str = "EspressoMail",
) -> bool:
    """Send a non-blocking desktop notification to the user's notification daemon.

    Parameters:
        title: Notification summary heading.
        body: Notification body text or email snippet.
        urgency: Urgency level ('low', 'normal', 'critical').
        sound: Whether to play an audible alert sound.
        icon: Icon name or path (e.g. 'mail-message-new').
        app_name: Application name reported to notification daemon.

    Returns:
        True if the notification command was spawned successfully.
    """
    if sound:
        play_alert_sound()

    system = platform.system().lower()

    # 1. Linux & BSD (via libnotify notify-send)
    if system in ("linux", "freebsd", "openbsd", "netbsd"):
        if not shutil.which("notify-send"):
            logger.debug("notify-send not found on system PATH; skipping desktop notification.")
            return False

        cmd = [
            "notify-send",
            "-a",
            app_name,
            "-u",
            urgency,
            "-i",
            icon,
            title,
            body,
        ]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as exc:
            logger.warning("Failed to invoke notify-send: %s", exc)
            return False

    # 2. macOS (via AppleScript osascript)
    elif system == "darwin":
        if not shutil.which("osascript"):
            return False

        safe_title = title.replace('"', '\\"').replace("'", "\\'")
        safe_body = body.replace('"', '\\"').replace("'", "\\'")
        sound_clause = ' sound name "Glass"' if sound else ""
        script = f'display notification "{safe_body}" with title "{app_name}" subtitle "{safe_title}"{sound_clause}'
        try:
            subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as exc:
            logger.warning("Failed to invoke osascript: %s", exc)
            return False

    # 3. Windows (via PowerShell toast)
    elif system == "windows":
        ps = shutil.which("powershell.exe") or shutil.which("powershell")
        if not ps:
            return False

        safe_title = title.replace('"', '`"').replace("'", "`'")
        safe_body = body.replace('"', '`"').replace("'", "`'")
        ps_cmd = (
            f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
            f"$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            f"$textNodes = $template.GetElementsByTagName('text'); "
            f"$textNodes.Item(0).AppendChild($template.CreateTextNode('{safe_title}')) > $null; "
            f"$textNodes.Item(1).AppendChild($template.CreateTextNode('{safe_body}')) > $null; "
            f"$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_name}').Show($toast);"
        )
        try:
            subprocess.Popen([ps, "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as exc:
            logger.warning("Failed to invoke Windows toast: %s", exc)
            return False

    return False


def notify_new_messages(
    messages: Sequence[EmailMessage],
    account_name: str = "",
    folder_name: str = "INBOX",
    sound: bool = False,
) -> bool:
    """Format and dispatch a desktop notification for newly arrived email messages."""
    if not messages:
        return False

    acc_label = f" ({account_name})" if account_name else ""

    if len(messages) == 1:
        m = messages[0]
        sender = m.sender_short
        title = f"New Email from {sender}{acc_label}"
        clean_snippet = " ".join(m.snippet.split()) if m.snippet else ""
        body = f"{m.subject}\n{clean_snippet}".strip() if clean_snippet else m.subject
    else:
        title = f"{len(messages)} New Emails in {folder_name}{acc_label}"
        senders = [m.sender_short for m in messages[:3]]
        sample_senders = ", ".join(senders)
        if len(messages) > 3:
            sample_senders += f" (+{len(messages) - 3} more)"
        top_subj = messages[0].subject
        body = f"From {sample_senders}\nLatest: {top_subj}"

    return send_desktop_notification(
        title=title,
        body=body,
        sound=sound,
        icon="mail-message-new",
    )
