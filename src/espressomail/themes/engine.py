"""Theme engine and customizable TrueColor palettes for EspressoMail."""

from __future__ import annotations

from dataclasses import dataclass

from espresso.crema import (
    DOUBLE_BORDER,
    NORMAL_BORDER,
    ROUNDED_BORDER,
    Border,
    Style,
)


@dataclass(frozen=True)
class Theme:
    """Color palette and styling rules for the EspressoMail TUI."""

    id: str
    name: str
    description: str
    # Base surfaces
    bg_primary: str
    bg_secondary: str
    bg_highlight: str
    bg_menubar: str
    bg_statusbar: str
    # Foreground text
    fg_primary: str
    fg_secondary: str
    fg_muted: str
    # Accents & semantic colors
    accent_primary: str
    accent_secondary: str
    border_focused: str
    border_unfocused: str
    unread_fg: str
    starred_fg: str
    attachment_fg: str
    pgp_verified_fg: str
    pgp_warning_fg: str
    pgp_error_fg: str
    quote_level_1: str
    quote_level_2: str
    quote_level_3: str
    gradient_start: str
    gradient_end: str

    @property
    def is_light(self) -> bool:
        """Return True if this theme uses a light background color scheme."""
        return self.id in ("solarized_light", "light") or "light" in self.id.lower()

    def pane_style(
        self,
        focused: bool,
        width: int,
        height: int,
        title: str = "",
        border: Border = ROUNDED_BORDER,
        bg: str | None = None,
    ) -> Style:
        """Build a bordered Crema Style for one of the 3 layout panes."""
        b_color = self.border_focused if focused else self.border_unfocused
        pane_bg = bg or self.bg_secondary
        s = (
            Style()
            .border(border)
            .border_foreground(b_color)
            .border_background(pane_bg)
            .background(pane_bg)
            .foreground(self.fg_primary)
            .width(max(10, width - 2))
            .height(max(3, height))
        )
        if title:
            title_style = (
                Style().bold(True).foreground(self.accent_primary).background(pane_bg)
                if focused
                else Style().foreground(self.fg_secondary).background(pane_bg)
            )
            s = s.border_title(title_style.render(f" {title} "))
        return s

    def modal_style(self, width: int, title: str = "", bg: str | None = None) -> Style:
        """Build a bordered Crema Style for floating modal dialogs."""
        modal_bg = bg or self.bg_secondary
        s = (
            Style()
            .border(DOUBLE_BORDER)
            .border_foreground(self.accent_primary)
            .border_background(modal_bg)
            .background(modal_bg)
            .foreground(self.fg_primary)
            .padding(1, 2)
            .width(max(24, width))
        )
        if title:
            header = (
                Style()
                .bold(True)
                .foreground(self.accent_secondary)
                .background(modal_bg)
                .render(f" {title} ")
            )
            s = s.border_title(header)
        return s

    def text_input_styles(self) -> dict[str, Style]:
        """Return styled configurations for Espresso TextInput components."""
        return {
            "style": Style().foreground(self.fg_primary),
            "placeholder_style": Style().foreground(self.fg_muted),
            "prompt_style": Style().bold(True).foreground(self.accent_primary),
        }

    def text_area_styles(self) -> dict[str, Style]:
        """Return styled configurations for Espresso TextArea components."""
        return {
            "style": Style().foreground(self.fg_primary),
            "line_number_style": Style().foreground(self.fg_muted),
            "cursor_line_number_style": Style().bold(True).foreground(self.accent_primary),
            "placeholder_style": Style().foreground(self.fg_muted),
        }

    def menu_bar_style(self, width: int) -> Style:
        """Style for top application menu bar."""
        return Style().background(self.bg_menubar).foreground(self.fg_primary).width(width)

    def dropdown_style(self, width: int = 30) -> Style:
        """Style for open drop-down menu box."""
        return (
            Style()
            .border(NORMAL_BORDER)
            .border_foreground(self.accent_primary)
            .border_background(self.bg_secondary)
            .background(self.bg_secondary)
            .foreground(self.fg_primary)
            .width(width)
        )


THEMES: dict[str, Theme] = {
    "espresso": Theme(
        id="espresso",
        name="Espresso Dark",
        description="Warm roasted coffee bean dark theme with crema gold and amber accents",
        bg_primary="#181311",
        bg_secondary="#241C19",
        bg_highlight="#3A2B24",
        bg_menubar="#261D19",
        bg_statusbar="#201815",
        fg_primary="#F5EBE6",
        fg_secondary="#D5C3B8",
        fg_muted="#8C776B",
        accent_primary="#D4A373",
        accent_secondary="#FF9E64",
        border_focused="#FF9E64",
        border_unfocused="#523F35",
        unread_fg="#FFD166",
        starred_fg="#FFB703",
        attachment_fg="#4CC9F0",
        pgp_verified_fg="#06D6A0",
        pgp_warning_fg="#FFD166",
        pgp_error_fg="#EF476F",
        quote_level_1="#D4A373",
        quote_level_2="#4CC9F0",
        quote_level_3="#B5838D",
        gradient_start="#FF9E64",
        gradient_end="#D4A373",
    ),
    "catppuccin": Theme(
        id="catppuccin",
        name="Catppuccin Mocha",
        description="Soothing pastel dark palette with mauve, lavender, and peach highlights",
        bg_primary="#1E1E2E",
        bg_secondary="#181825",
        bg_highlight="#313244",
        bg_menubar="#181825",
        bg_statusbar="#11111B",
        fg_primary="#CDD6F4",
        fg_secondary="#BAC2DE",
        fg_muted="#6C7086",
        accent_primary="#CBA6F7",
        accent_secondary="#FAB387",
        border_focused="#CBA6F7",
        border_unfocused="#45475A",
        unread_fg="#89B4FA",
        starred_fg="#F9E2AF",
        attachment_fg="#94E2D5",
        pgp_verified_fg="#A6E3A1",
        pgp_warning_fg="#F9E2AF",
        pgp_error_fg="#F38BA8",
        quote_level_1="#89B4FA",
        quote_level_2="#CBA6F7",
        quote_level_3="#94E2D5",
        gradient_start="#CBA6F7",
        gradient_end="#89B4FA",
    ),
    "dracula": Theme(
        id="dracula",
        name="Dracula",
        description="High-contrast dark vampire palette with vibrant purple, pink, and cyan",
        bg_primary="#282A36",
        bg_secondary="#21222C",
        bg_highlight="#44475A",
        bg_menubar="#191A21",
        bg_statusbar="#191A21",
        fg_primary="#F8F8F2",
        fg_secondary="#E2E2DC",
        fg_muted="#6272A4",
        accent_primary="#BD93F9",
        accent_secondary="#FF79C6",
        border_focused="#FF79C6",
        border_unfocused="#44475A",
        unread_fg="#8BE9FD",
        starred_fg="#F1FA8C",
        attachment_fg="#FFB86C",
        pgp_verified_fg="#50FA7B",
        pgp_warning_fg="#FFB86C",
        pgp_error_fg="#FF5555",
        quote_level_1="#BD93F9",
        quote_level_2="#8BE9FD",
        quote_level_3="#FF79C6",
        gradient_start="#FF79C6",
        gradient_end="#BD93F9",
    ),
    "nord": Theme(
        id="nord",
        name="Nord Arctic",
        description="Clean arctic bluish palette inspired by polar night and aurora borealis",
        bg_primary="#2E3440",
        bg_secondary="#3B4252",
        bg_highlight="#434C5E",
        bg_menubar="#242933",
        bg_statusbar="#242933",
        fg_primary="#ECEFF4",
        fg_secondary="#D8DEE9",
        fg_muted="#7B88A1",
        accent_primary="#88C0D0",
        accent_secondary="#81A1C1",
        border_focused="#88C0D0",
        border_unfocused="#4C566A",
        unread_fg="#8FBCBB",
        starred_fg="#EBCB8B",
        attachment_fg="#81A1C1",
        pgp_verified_fg="#A3BE8C",
        pgp_warning_fg="#EBCB8B",
        pgp_error_fg="#BF616A",
        quote_level_1="#88C0D0",
        quote_level_2="#A3BE8C",
        quote_level_3="#B48EAD",
        gradient_start="#88C0D0",
        gradient_end="#5E81AC",
    ),
    "gruvbox": Theme(
        id="gruvbox",
        name="Gruvbox Dark",
        description="Retro groove warm dark theme with earthy amber, olive, and aqua tones",
        bg_primary="#282828",
        bg_secondary="#1D2021",
        bg_highlight="#3C3836",
        bg_menubar="#1D2021",
        bg_statusbar="#1D2021",
        fg_primary="#EBDBB2",
        fg_secondary="#D5C4A1",
        fg_muted="#928374",
        accent_primary="#FE8019",
        accent_secondary="#FABD2F",
        border_focused="#FE8019",
        border_unfocused="#504945",
        unread_fg="#FABD2F",
        starred_fg="#FE8019",
        attachment_fg="#83A598",
        pgp_verified_fg="#B8BB26",
        pgp_warning_fg="#FABD2F",
        pgp_error_fg="#FB4934",
        quote_level_1="#8EC07C",
        quote_level_2="#83A598",
        quote_level_3="#D3869B",
        gradient_start="#FE8019",
        gradient_end="#FABD2F",
    ),
    "solarized_light": Theme(
        id="solarized_light",
        name="Light",
        description="Clean, modern daylight theme with crisp white canvas, soft light surfaces, and vibrant contrast",
        bg_primary="#FFFFFF",
        bg_secondary="#F4F6F8",
        bg_highlight="#E2E8F0",
        bg_menubar="#F4F6F8",
        bg_statusbar="#E9ECEF",
        fg_primary="#18181B",
        fg_secondary="#495057",
        fg_muted="#6C757D",
        accent_primary="#2563EB",
        accent_secondary="#EA580C",
        border_focused="#2563EB",
        border_unfocused="#CBD5E1",
        unread_fg="#DC2626",
        starred_fg="#D97706",
        attachment_fg="#0D9488",
        pgp_verified_fg="#16A34A",
        pgp_warning_fg="#D97706",
        pgp_error_fg="#DC2626",
        quote_level_1="#2563EB",
        quote_level_2="#0D9488",
        quote_level_3="#7C3AED",
        gradient_start="#2563EB",
        gradient_end="#0D9488",
    ),
}

THEME_ORDER: list[str] = list(THEMES.keys())
THEMES["light"] = THEMES["solarized_light"]


class ThemeEngine:
    """Manages active color scheme selection and runtime theme cycling."""

    def __init__(self, initial_theme: str = "espresso") -> None:
        self._active_id = initial_theme if initial_theme in THEMES else "espresso"

    @property
    def current(self) -> Theme:
        """Return the currently active Theme."""
        return THEMES[self._active_id]

    @property
    def active_id(self) -> str:
        return self._active_id

    def set_theme(self, theme_id: str) -> Theme:
        """Activate a theme by ID (falling back to 'espresso' if unknown)."""
        if theme_id in THEMES:
            self._active_id = theme_id
        return self.current

    def next_theme(self) -> Theme:
        """Cycle to the next available color scheme and return it."""
        idx = THEME_ORDER.index(self._active_id)
        self._active_id = THEME_ORDER[(idx + 1) % len(THEME_ORDER)]
        return self.current

    def prev_theme(self) -> Theme:
        """Cycle to the previous available color scheme and return it."""
        idx = THEME_ORDER.index(self._active_id)
        self._active_id = THEME_ORDER[(idx - 1) % len(THEME_ORDER)]
        return self.current

    def list_themes(self) -> list[Theme]:
        """Return all registered themes in display order."""
        return [THEMES[k] for k in THEME_ORDER]
