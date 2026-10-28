"""Top application menu bar and interactive drop-down menus for EspressoMail."""

from __future__ import annotations

from dataclasses import dataclass

from espresso import Cmd, KeyMsg, Model, MouseAction, MouseButton, MouseMsg, Msg
from espresso.crema import NORMAL_BORDER, Style, place_overlay, string_width

try:
    from espresso.beans import (
        MenuBar as CoreMenuBar,
        MenuCategory as CoreMenuCategory,
        MenuItem as CoreMenuItem,
        MenuSelectMsg,
    )
except ImportError:
    CoreMenuBar = None  # type: ignore[assignment, misc]
    CoreMenuCategory = None  # type: ignore[assignment, misc]
    CoreMenuItem = None  # type: ignore[assignment, misc]
    MenuSelectMsg = None  # type: ignore[assignment, misc]

from espressomail.themes.engine import Theme


@dataclass(frozen=True)
class MenuItem:
    """A single action item inside a drop-down menu."""

    action_id: str
    label: str
    shortcut: str = ""


@dataclass(frozen=True)
class MenuCategory:
    """A top-level menu category containing drop-down items."""

    title: str
    hotkey: str
    items: tuple[MenuItem, ...]


@dataclass(frozen=True)
class MenuActionMsg(Msg):
    """Message emitted when a user activates an item from a drop-down menu."""

    action_id: str


DEFAULT_MENUS: tuple[MenuCategory, ...] = (
    MenuCategory(
        title="File",
        hotkey="f",
        items=(
            MenuItem("sync_all", "Sync All Accounts", "S"),
            MenuItem("account_settings", "Edit Account Settings...", "A"),
            MenuItem("new_account", "Add New Account...", "O"),
            MenuItem("preferences", "Preferences & Configuration...", "p"),
            MenuItem("save_attachments", "Download Attachments...", "w"),
            MenuItem("empty_trash", "Empty Trash", "X"),
            MenuItem("quit_app", "Quit EspressoMail", "q"),
        ),
    ),
    MenuCategory(
        title="Mail",
        hotkey="m",
        items=(
            MenuItem("compose", "Compose New Message", "c"),
            MenuItem("reply", "Reply to Sender", "r"),
            MenuItem("reply_all", "Reply to All", "R"),
            MenuItem("forward", "Forward Message", "f"),
            MenuItem("rsvp_calendar", "RSVP to Calendar Invite...", "1/2/3"),
            MenuItem("toggle_star", "Toggle Star / Flag", "s"),
            MenuItem("toggle_read", "Toggle Read / Unread", "u"),
            MenuItem("add_sender_contact", "Add Sender to Contacts", "a"),
            MenuItem("toggle_folder_subscription", "Toggle Folder Subscription", "s"),
            MenuItem("fetch_more", "Fetch Older Messages from Server", "M"),
            MenuItem("archive_msg", "Archive Message", "e"),
            MenuItem("trash_msg", "Move to Trash / Delete", "d"),
        ),
    ),
    MenuCategory(
        title="View",
        hotkey="v",
        items=(
            MenuItem("unified_inbox", "Unified Inbox (All Accounts)", "U"),
            MenuItem("toggle_unread_filter", "Toggle Unread Only Filter", "Ctrl+U"),
            MenuItem("toggle_threading", "Toggle Message Threading", "t"),
            MenuItem("cycle_image_mode", "Cycle Image Renderer (Chafa/Sixel)", "i"),
            MenuItem("toggle_headers", "Toggle Raw RFC 5322 Headers", "H"),
            MenuItem("expand_reader", "Widen Reading Pane", "["),
            MenuItem("expand_list", "Widen Message List", "]"),
            MenuItem("reset_splitter", "Reset Splitter Width", ""),
        ),
    ),
    MenuCategory(
        title="Tools",
        hotkey="t",
        items=(
            MenuItem("fuzzy_search", "Fuzzy Search Local Database...", "/"),
            MenuItem("address_book", "Address Book & Contacts...", "B"),
            MenuItem("sieve_filters", "Configure Sieve Filters...", "F"),
            MenuItem("pgp_manager", "GPG / PGP Keyring & Crypto...", "G"),
            MenuItem("command_palette", "Open Command Palette...", "Ctrl+P"),
        ),
    ),
    MenuCategory(
        title="Help",
        hotkey="h",
        items=(
            MenuItem("show_shortcuts", "Keyboard & Mouse Shortcuts", "?"),
            MenuItem("replay_splash", "Show Startup Splash Screen", "F1"),
        ),
    ),
)


class MenuBar(Model):
    """Interactive top menu bar with mouse and keyboard drop-down menus."""

    def __init__(self, categories: tuple[MenuCategory, ...] = DEFAULT_MENUS) -> None:
        self.categories = categories
        self.is_open = False
        self.active_cat_idx = 0
        self.active_item_idx = 0
        # Store (start_x, end_x) for each category label in the top bar for mouse hit testing
        self._cat_bounds: list[tuple[int, int]] = []
        self._compute_bounds()

    def _compute_bounds(self) -> None:
        self._cat_bounds = []
        # Brand prefix " ☕ EspressoMail │ " is 18 cells wide
        x = 18
        for cat in self.categories:
            label = f" {cat.title} ▾ "
            w = string_width(label)
            self._cat_bounds.append((x, x + w))
            x += w + 1

    def open_category(self, index: int) -> None:
        """Open a specific menu category by index."""
        self.active_cat_idx = index % len(self.categories)
        self.active_item_idx = 0
        self.is_open = True

    def close(self) -> None:
        """Close the active drop-down menu."""
        self.is_open = False

    def init(self) -> Cmd | None:
        return None

    def update(self, msg: Msg) -> tuple[MenuBar, Cmd | None]:
        """Handle keyboard and mouse navigation when menu bar is interacted with."""
        if isinstance(msg, KeyMsg):
            k = str(msg.key)
            if not self.is_open:
                if k in ("f10", "M"):
                    self.open_category(0)
                return self, None

            cat = self.categories[self.active_cat_idx]
            if k in ("esc", "q", "f10"):
                self.is_open = False
                return self, None
            if k in ("left", "h"):
                self.active_cat_idx = (self.active_cat_idx - 1) % len(self.categories)
                self.active_item_idx = 0
                return self, None
            if k in ("right", "l", "tab"):
                self.active_cat_idx = (self.active_cat_idx + 1) % len(self.categories)
                self.active_item_idx = 0
                return self, None
            if k in ("up", "k"):
                self.active_item_idx = (self.active_item_idx - 1) % len(cat.items)
                return self, None
            if k in ("down", "j"):
                self.active_item_idx = (self.active_item_idx + 1) % len(cat.items)
                return self, None
            if k in ("enter", " ", "space"):
                chosen = cat.items[self.active_item_idx]
                self.is_open = False
                return self, lambda: MenuActionMsg(chosen.action_id)

        elif isinstance(msg, MouseMsg):
            if msg.action == MouseAction.PRESS and msg.button == MouseButton.LEFT:
                # Top bar row is y == 0
                if msg.y == 0:
                    for idx, (x0, x1) in enumerate(self._cat_bounds):
                        if x0 <= msg.x < x1:
                            if self.is_open and self.active_cat_idx == idx:
                                self.is_open = False
                            else:
                                self.open_category(idx)
                            return self, None
                    self.is_open = False
                    return self, None

                if self.is_open:
                    cat = self.categories[self.active_cat_idx]
                    drop_x = self._cat_bounds[self.active_cat_idx][0]
                    drop_w = 38
                    # Dropdown rows start at y=1 (top border), items at y=2..1+len(items)
                    item_row = msg.y - 2
                    if drop_x <= msg.x < drop_x + drop_w and 0 <= item_row < len(cat.items):
                        self.active_item_idx = item_row
                        chosen = cat.items[item_row]
                        self.is_open = False
                        return self, lambda: MenuActionMsg(chosen.action_id)
                    # Clicked outside dropdown -> close
                    self.is_open = False
                    return self, None

            elif msg.action == MouseAction.MOTION and self.is_open:
                if msg.y == 0:
                    for idx, (x0, x1) in enumerate(self._cat_bounds):
                        if x0 <= msg.x < x1 and idx != self.active_cat_idx:
                            self.open_category(idx)
                            return self, None
                else:
                    cat = self.categories[self.active_cat_idx]
                    drop_x = self._cat_bounds[self.active_cat_idx][0]
                    item_row = msg.y - 2
                    if drop_x <= msg.x < drop_x + 38 and 0 <= item_row < len(cat.items):
                        self.active_item_idx = item_row

        return self, None

    def view_bar(self, width: int, theme: Theme, right_status: str = "") -> str:
        """Render the single-line top application menu bar."""
        brand = (
            Style()
            .bold(True)
            .foreground(theme.bg_primary)
            .background(theme.accent_primary)
            .render(" ☕ EspressoMail ")
        )
        sep = Style().foreground(theme.fg_muted).background(theme.bg_menubar).render("│ ")

        cat_parts: list[str] = []
        for idx, cat in enumerate(self.categories):
            label = f" {cat.title} ▾ "
            if self.is_open and idx == self.active_cat_idx:
                part = (
                    Style()
                    .bold(True)
                    .foreground(theme.bg_primary)
                    .background(theme.accent_secondary)
                    .render(label)
                )
            else:
                part = (
                    Style()
                    .foreground(theme.fg_primary)
                    .background(theme.bg_menubar)
                    .render(label)
                )
            cat_parts.append(part)

        left_cluster = brand + sep + " ".join(cat_parts)
        left_w = string_width(left_cluster)

        right_text = f" {right_status}  [F10/m] Menu "
        right_rendered = (
            Style()
            .foreground(theme.fg_secondary)
            .background(theme.bg_menubar)
            .render(right_text)
        )
        right_w = string_width(right_rendered)

        pad_len = max(0, width - left_w - right_w)
        spacer = Style().background(theme.bg_menubar).render(" " * pad_len)
        return left_cluster + spacer + right_rendered

    def view_dropdown(self, theme: Theme) -> tuple[str, int, int]:
        """Render the currently open drop-down menu box and return (box_str, x, y)."""
        cat = self.categories[self.active_cat_idx]
        drop_x = self._cat_bounds[self.active_cat_idx][0] if self._cat_bounds else 18
        inner_w = 34
        rows: list[str] = []
        for idx, item in enumerate(cat.items):
            sc = f"[{item.shortcut}]" if item.shortcut else ""
            lbl_pad = max(1, inner_w - len(item.label) - len(sc) - 2)
            line_txt = f" {item.label}{' ' * lbl_pad}{sc} "
            if idx == self.active_item_idx:
                rows.append(
                    Style()
                    .bold(True)
                    .foreground(theme.bg_primary)
                    .background(theme.accent_primary)
                    .render(line_txt)
                )
            else:
                rows.append(
                    Style()
                    .foreground(theme.fg_primary)
                    .background(theme.bg_secondary)
                    .render(line_txt)
                )

        box = (
            Style()
            .border(NORMAL_BORDER)
            .border_foreground(theme.accent_primary)
            .background(theme.bg_secondary)
            .render("\n".join(rows))
        )
        return box, drop_x, 1

    def overlay_onto(self, base_view: str, theme: Theme) -> str:
        """Overlay the open drop-down menu onto the full-screen view at its column coordinates."""
        if not self.is_open:
            return base_view
        box, drop_x, drop_y = self.view_dropdown(theme)
        return place_overlay(
            base_view,
            box,
            x=drop_x,
            y=drop_y,
            center=False,
            dim_backdrop=False,
        )


def to_core_menubar(
    categories: tuple[MenuCategory, ...] = DEFAULT_MENUS,
    width: int = 80,
    brand: str = " ☕ EspressoMail ",
    right_status: str = "",
) -> CoreMenuBar | None:
    """Convert EspressoMail menu categories into an upstream espresso.beans.MenuBar instance."""
    if CoreMenuBar is None or CoreMenuCategory is None or CoreMenuItem is None:
        return None
    core_cats: list[CoreMenuCategory] = []
    for cat in categories:
        items = [
            CoreMenuItem(
                id=item.action_id,
                label=item.label,
                shortcut=item.shortcut,
            )
            for item in cat.items
        ]
        core_cats.append(
            CoreMenuCategory(
                id=cat.title.lower(),
                label=cat.title,
                mnemonic=cat.hotkey,
                items=items,
            )
        )
    return CoreMenuBar(
        categories=core_cats,
        width=width,
        brand=brand,
        right_status=right_status,
    )

