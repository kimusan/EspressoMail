"""Espresso TEA user interface components, screens, and views."""

from espressomail.ui.app import EspressoMailApp, FolderTreeRow, MessageListRow, SyncCompletedMsg
from espressomail.ui.formatter import (
    format_email_body,
    format_message_for_reading_pane,
    html_to_terminal_text,
)
from espressomail.ui.image_renderer import ImageRenderBackend, TerminalImageRenderer
from espressomail.ui.menus import DEFAULT_MENUS, MenuActionMsg, MenuBar, MenuCategory, MenuItem
from espressomail.ui.modals import (
    AddressBookModal,
    ComposeModal,
    ComposeSubmittedMsg,
    HelpModal,
    ModalClosedMsg,
    PGPModal,
    SearchModal,
    SieveModal,
)
from espressomail.ui.onboarding import OnboardingCompleteMsg, OnboardingWizard, PROVIDER_PRESETS
from espressomail.ui.splash import SplashDoneMsg, SplashScreen, SplashTickMsg

__all__ = [
    "EspressoMailApp",
    "FolderTreeRow",
    "MessageListRow",
    "SyncCompletedMsg",
    "SplashScreen",
    "SplashTickMsg",
    "SplashDoneMsg",
    "OnboardingWizard",
    "OnboardingCompleteMsg",
    "PROVIDER_PRESETS",
    "MenuBar",
    "MenuCategory",
    "MenuItem",
    "MenuActionMsg",
    "DEFAULT_MENUS",
    "TerminalImageRenderer",
    "ImageRenderBackend",
    "format_email_body",
    "format_message_for_reading_pane",
    "html_to_terminal_text",
    "ComposeModal",
    "ComposeSubmittedMsg",
    "SearchModal",
    "AddressBookModal",
    "SieveModal",
    "PGPModal",
    "HelpModal",
    "ModalClosedMsg",
]
