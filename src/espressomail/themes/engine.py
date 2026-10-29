"""Theme engine and customizable TrueColor palettes for EspressoMail."""

from __future__ import annotations

from dataclasses import dataclass

from espresso.crema import (
    DOUBLE_BORDER,
    NORMAL_BORDER,
    ROUNDED_BORDER,
    Border,
    Style,
    truncate_ansi,
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
        """Build a bordered Crema Style for floating modal dialogs with target total outer width."""
        modal_bg = bg or self.bg_secondary
        # Crema Style.width() specifies inner content width. Double border (+2)
        # and horizontal padding (2 left, 2 right = +4) add 6 cells to total width.
        # To make the outer dialog exactly match `width`, inner content width is width - 6.
        inner_content_w = max(18, width - 6)
        s = (
            Style()
            .border(DOUBLE_BORDER)
            .border_foreground(self.accent_primary)
            .border_background(modal_bg)
            .background(modal_bg)
            .foreground(self.fg_primary)
            .padding(1, 2)
            .width(inner_content_w)
        )
        if title:
            title_w = max(10, width - 6)
            title_cut = truncate_ansi(title, title_w)
            header = (
                Style()
                .bold(True)
                .foreground(self.accent_secondary)
                .background(modal_bg)
                .render(f" {title_cut} ")
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


THEME_FAMILIES: tuple[str, ...] = (
    "espresso",
    "catppuccin",
    "nord",
    "gruvbox",
    "dracula",
    "solarized",
)

COLOR_MODES: tuple[str, ...] = ("dark", "light")

THEMES: dict[str, Theme] = {
    # 1. Espresso (Dark & Light)
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
    "espresso_light": Theme(
        id="espresso_light",
        name="Espresso Light",
        description="Warm café latte light theme with creamy surfaces, rich coffee brown text, and amber gold accents",
        bg_primary="#FBF8F4",
        bg_secondary="#F0E9DF",
        bg_highlight="#E4D7C8",
        bg_menubar="#EFE6DB",
        bg_statusbar="#E5D9CC",
        fg_primary="#2B1E16",
        fg_secondary="#5C473A",
        fg_muted="#8C776B",
        accent_primary="#A05A2C",
        accent_secondary="#C67D3E",
        border_focused="#A05A2C",
        border_unfocused="#D5C2B2",
        unread_fg="#B45309",
        starred_fg="#D97706",
        attachment_fg="#0284C7",
        pgp_verified_fg="#16A34A",
        pgp_warning_fg="#D97706",
        pgp_error_fg="#DC2626",
        quote_level_1="#A05A2C",
        quote_level_2="#0284C7",
        quote_level_3="#7C3AED",
        gradient_start="#C67D3E",
        gradient_end="#A05A2C",
    ),

    # 2. Catppuccin (Mocha Dark & Latte Light)
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
    "catppuccin_light": Theme(
        id="catppuccin_light",
        name="Catppuccin Latte",
        description="Soothing pastel light palette with lavender, mauve, and soft warm crusts",
        bg_primary="#EFF1F5",
        bg_secondary="#E6E9EF",
        bg_highlight="#CCD0DA",
        bg_menubar="#E6E9EF",
        bg_statusbar="#DCE0E8",
        fg_primary="#4C4F69",
        fg_secondary="#5C5F77",
        fg_muted="#8C8FA1",
        accent_primary="#8839EF",
        accent_secondary="#FE640B",
        border_focused="#8839EF",
        border_unfocused="#BCC0CC",
        unread_fg="#1E66F5",
        starred_fg="#DF8E1D",
        attachment_fg="#179299",
        pgp_verified_fg="#40A02B",
        pgp_warning_fg="#DF8E1D",
        pgp_error_fg="#D20F39",
        quote_level_1="#1E66F5",
        quote_level_2="#8839EF",
        quote_level_3="#179299",
        gradient_start="#8839EF",
        gradient_end="#1E66F5",
    ),

    # 3. Nord (Polar Night Dark & Snow Storm Light)
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
    "nord_light": Theme(
        id="nord_light",
        name="Nord Snow Storm",
        description="Clean arctic light palette inspired by Scandinavian snowfields and glacial blues",
        bg_primary="#ECEFF4",
        bg_secondary="#E5E9F0",
        bg_highlight="#D8DEE9",
        bg_menubar="#E5E9F0",
        bg_statusbar="#D8DEE9",
        fg_primary="#2E3440",
        fg_secondary="#3B4252",
        fg_muted="#616E88",
        accent_primary="#5E81AC",
        accent_secondary="#88C0D0",
        border_focused="#5E81AC",
        border_unfocused="#B8C0D0",
        unread_fg="#0284C7",
        starred_fg="#D08770",
        attachment_fg="#5E81AC",
        pgp_verified_fg="#2E7D32",
        pgp_warning_fg="#EBCB8B",
        pgp_error_fg="#BF616A",
        quote_level_1="#5E81AC",
        quote_level_2="#2E7D32",
        quote_level_3="#B48EAD",
        gradient_start="#5E81AC",
        gradient_end="#88C0D0",
    ),

    # 4. Gruvbox (Dark & Light)
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
    "gruvbox_light": Theme(
        id="gruvbox_light",
        name="Gruvbox Light",
        description="Retro groove warm light theme with parchment canvas and earthy amber tones",
        bg_primary="#FBF1C7",
        bg_secondary="#EBDBB2",
        bg_highlight="#D5C4A1",
        bg_menubar="#EBDBB2",
        bg_statusbar="#D5C4A1",
        fg_primary="#282828",
        fg_secondary="#3C3836",
        fg_muted="#7C6F64",
        accent_primary="#AF3A03",
        accent_secondary="#B57614",
        border_focused="#AF3A03",
        border_unfocused="#BDAE93",
        unread_fg="#AF3A03",
        starred_fg="#B57614",
        attachment_fg="#076678",
        pgp_verified_fg="#79740E",
        pgp_warning_fg="#B57614",
        pgp_error_fg="#9D0006",
        quote_level_1="#427B58",
        quote_level_2="#076678",
        quote_level_3="#8F3F71",
        gradient_start="#AF3A03",
        gradient_end="#B57614",
    ),

    # 5. Dracula (Dark & Light)
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
    "dracula_light": Theme(
        id="dracula_light",
        name="Dracula Light",
        description="Crisp light counterpart to Dracula with gothic purple, bright magenta, and deep ink",
        bg_primary="#F8F8F2",
        bg_secondary="#EAEAE2",
        bg_highlight="#DADAD0",
        bg_menubar="#EAEAE2",
        bg_statusbar="#DFDFD7",
        fg_primary="#282A36",
        fg_secondary="#44475A",
        fg_muted="#6272A4",
        accent_primary="#6D28D9",
        accent_secondary="#DB2777",
        border_focused="#6D28D9",
        border_unfocused="#CBD5E1",
        unread_fg="#0284C7",
        starred_fg="#D97706",
        attachment_fg="#D97706",
        pgp_verified_fg="#16A34A",
        pgp_warning_fg="#D97706",
        pgp_error_fg="#DC2626",
        quote_level_1="#6D28D9",
        quote_level_2="#0284C7",
        quote_level_3="#DB2777",
        gradient_start="#DB2777",
        gradient_end="#6D28D9",
    ),

    # 6. Solarized (Dark & Light)
    "solarized": Theme(
        id="solarized",
        name="Solarized Dark",
        description="Precision color theme with deep solarized cyan-black canvas and balanced accents",
        bg_primary="#002B36",
        bg_secondary="#073642",
        bg_highlight="#0B4250",
        bg_menubar="#073642",
        bg_statusbar="#00212B",
        fg_primary="#839496",
        fg_secondary="#93A1A1",
        fg_muted="#586E75",
        accent_primary="#268BD2",
        accent_secondary="#2AA198",
        border_focused="#268BD2",
        border_unfocused="#073642",
        unread_fg="#B58900",
        starred_fg="#CB4B16",
        attachment_fg="#2AA198",
        pgp_verified_fg="#859900",
        pgp_warning_fg="#B58900",
        pgp_error_fg="#DC322F",
        quote_level_1="#268BD2",
        quote_level_2="#2AA198",
        quote_level_3="#6C71C4",
        gradient_start="#268BD2",
        gradient_end="#2AA198",
    ),
    "solarized_light": Theme(
        id="solarized_light",
        name="Solarized Light",
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

# Compatibility aliases
THEMES["espresso_dark"] = THEMES["espresso"]
THEMES["catppuccin_dark"] = THEMES["catppuccin"]
THEMES["nord_dark"] = THEMES["nord"]
THEMES["gruvbox_dark"] = THEMES["gruvbox"]
THEMES["dracula_dark"] = THEMES["dracula"]
THEMES["solarized_dark"] = THEMES["solarized"]
THEMES["light"] = THEMES["solarized_light"]

THEME_ORDER: list[str] = [
    "espresso",
    "espresso_light",
    "catppuccin",
    "catppuccin_light",
    "nord",
    "nord_light",
    "gruvbox",
    "gruvbox_light",
    "dracula",
    "dracula_light",
    "solarized",
    "solarized_light",
]


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

    @property
    def color_mode(self) -> str:
        """Return 'light' or 'dark' for the active theme."""
        return "light" if ("_light" in self._active_id or self._active_id == "light") else "dark"

    @property
    def theme_family(self) -> str:
        """Return the theme family without mode suffix."""
        fam = self._active_id.replace("_light", "").replace("_dark", "")
        if fam in THEME_FAMILIES:
            return fam
        if fam == "light":
            return "solarized"
        return "espresso"

    def set_theme(self, theme_id: str) -> Theme:
        """Activate a theme by ID (falling back to 'espresso' if unknown)."""
        if theme_id in THEMES:
            self._active_id = theme_id
        return self.current

    def set_family_and_mode(self, family: str, mode: str) -> Theme:
        """Activate a theme given a family (e.g. 'espresso') and mode ('dark' or 'light')."""
        fam = family.lower() if family.lower() in THEME_FAMILIES else "espresso"
        mod = mode.lower() if mode.lower() in COLOR_MODES else "dark"
        target_id = f"{fam}_{mod}" if mod == "light" else fam
        if target_id in THEMES:
            self._active_id = target_id
        elif fam in THEMES:
            self._active_id = fam
        return self.current

    def toggle_color_mode(self) -> Theme:
        """Toggle between dark and light mode for the current theme family."""
        new_mode = "dark" if self.color_mode == "light" else "light"
        return self.set_family_and_mode(self.theme_family, new_mode)

    def next_theme(self) -> Theme:
        """Cycle to the next available color scheme and return it."""
        idx = THEME_ORDER.index(self._active_id) if self._active_id in THEME_ORDER else 0
        self._active_id = THEME_ORDER[(idx + 1) % len(THEME_ORDER)]
        return self.current

    def prev_theme(self) -> Theme:
        """Cycle to the previous available color scheme and return it."""
        idx = THEME_ORDER.index(self._active_id) if self._active_id in THEME_ORDER else 0
        self._active_id = THEME_ORDER[(idx - 1) % len(THEME_ORDER)]
        return self.current

    def list_themes(self) -> list[Theme]:
        """Return all registered themes in display order."""
        return [THEMES[k] for k in THEME_ORDER]

