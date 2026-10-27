"""Espresso TEA user interface components, screens, and views."""

from espressomail.ui.app import (
    AutoSyncTickMsg,
    EspressoMailApp,
    FolderTreeRow,
    MessageImagesLoadedMsg,
    MessageListRow,
    SyncCompletedMsg,
    SyncProgressMsg,
)
from espressomail.ui.formatter import (
    format_email_body,
    format_message_for_reading_pane,
    html_to_terminal_text,
)
from espressomail.ui.image_renderer import (
    ImageRenderBackend,
    TerminalImageRenderer,
    clear_image_caches,
)
from espressomail.ui.menus import DEFAULT_MENUS, MenuActionMsg, MenuBar, MenuCategory, MenuItem
from espressomail.ui.modals import (
    AccountSettingsModal,
    AddressBookModal,
    ComposeModal,
    ComposeSubmittedMsg,
    DownloadAttachmentsModal,
    HelpModal,
    ModalClosedMsg,
    PGPModal,
    PreferencesModal,
    PreferencesSavedMsg,
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
    "SyncProgressMsg",
    "AutoSyncTickMsg",
    "MessageImagesLoadedMsg",
    "clear_image_caches",
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
    "AccountSettingsModal",
    "ComposeModal",
    "ComposeSubmittedMsg",
    "SearchModal",
    "AddressBookModal",
    "SieveModal",
    "PGPModal",
    "HelpModal",
    "ModalClosedMsg",
    "DownloadAttachmentsModal",
    "PreferencesModal",
    "PreferencesSavedMsg",
]
