"""Polished animated startup splash screen for EspressoMail."""

from __future__ import annotations

from dataclasses import dataclass

from espresso import Cmd, KeyMsg, Model, MouseAction, MouseMsg, Msg, tick
from espresso.beans import COFFEE, Progress, Spinner
from espresso.crema import (
     DOUBLE_BORDER,
    ROUNDED_BORDER,
    Align,
    Style,
    join_vertical,
    linear_gradient,
    place,
)

from espressomail import __version__
from espressomail.themes.engine import Theme

STEAM_FRAMES: list[tuple[str, str, str]] = [
    (
        "          (  )   (   )  )          ",
        "           ) (   )  (  (           ",
        "           ( )  (    ) )           ",
    ),
    (
        "           ) (   )  (  (           ",
        "          (  )   (   )  )          ",
        "           ) (  (    ) (           ",
    ),
    (
        "          ( )   (  )   (           ",
        "           (  )  ) (   ) )         ",
        "          ) (   (   ) (            ",
    ),
]

CUP_AND_ENVELOPE_ART: list[str] = [
    "         ___________________         ",
    "        <___________________> ___    ",
    "        |  ╔═════════════╗  |/ _ \\   ",
    "        |  ║ ✉  ESPRESSO ║  | | | |  ",
    "        |  ║ ╲  ╱  MAIL  ║  |_| |_|  ",
    "        |  ╚══╲╱═════════╝  |\\___/   ",
    "        |___________________|        ",
    "         \\_________________/         ",
]

LOGO_LINES: list[str] = [
    "███████╗███████╗██████╗ ██████╗ ███████╗███████╗███████╗ ██████╗ ",
    "██╔════╝██╔════╝██╔══██╗██╔══██╗██╔════╝██╔════╝██╔════╝██╔═══██╗",
    "█████╗  ███████╗██████╔╝██████╔╝█████╗  ███████╗███████╗██║   ██║",
    "██╔══╝  ╚════██║██╔═══╝ ██╔══██╗██╔══╝  ╚════██║╚════██║██║   ██║",
    "███████╗███████║██║     ██║  ██║███████╗███████║███████║╚██████╔╝",
    "╚══════╝╚══════╝╚═╝     ╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝ ╚═════╝ ",
    "              M  A  I  L     T  U  I     C  L  I  E  N  T        ",
]

STARTUP_STAGES: list[str] = [
    "Initializing SQLite WAL local store & FTS5 search index...",
    "Loading OpenPGP keyring & cryptographic verification engine...",
    "Compiling RFC 5228 Sieve filters & ManageSieve profiles...",
    "Aggregating multi-account Unified Inbox & conversation threads...",
    "Brewing fresh crema — Welcome to EspressoMail!",
]


@dataclass(frozen=True)
class SplashTickMsg(Msg):
    """Animation tick message for the startup splash screen."""


@dataclass(frozen=True)
class SplashDoneMsg(Msg):
    """Emitted when the startup splash screen finishes or is skipped."""


class SplashScreen(Model):
    """Animated startup splash screen with steaming coffee art, gradients, and progress bar."""

    def __init__(self, theme: Theme, total_ticks: int = 12, tick_interval: float = 0.12) -> None:
        self.theme = theme
        self.total_ticks = max(4, total_ticks)
        self.tick_interval = tick_interval
        self.current_tick = 0
        self.done = False
        self.spinner = Spinner(frames=COFFEE, fps=8.0)

    def init(self) -> Cmd | None:
        """Start the splash animation timer."""
        return tick(self.tick_interval, SplashTickMsg)

    def update(self, msg: Msg) -> tuple[SplashScreen, Cmd | None]:
        """Advance splash animation or skip immediately on user input."""
        if isinstance(msg, KeyMsg):
            self.done = True
            return self, lambda: SplashDoneMsg()

        if isinstance(msg, MouseMsg) and msg.action == MouseAction.PRESS:
            self.done = True
            return self, lambda: SplashDoneMsg()

        if isinstance(msg, SplashTickMsg):
            self.current_tick += 1
            if self.current_tick >= self.total_ticks:
                self.done = True
                return self, lambda: SplashDoneMsg()
            return self, tick(self.tick_interval, SplashTickMsg)

        return self, None

    @property
    def progress_ratio(self) -> float:
        return min(1.0, self.current_tick / max(1, self.total_ticks))

    @property
    def current_stage_text(self) -> str:
        idx = min(
            len(STARTUP_STAGES) - 1,
            int(self.progress_ratio * len(STARTUP_STAGES)),
        )
        return STARTUP_STAGES[idx]

    def view(self, width: int = 100, height: int = 30) -> str:
        """Render the centered splash card inside the terminal viewport."""
        steam = STEAM_FRAMES[self.current_tick % len(STEAM_FRAMES)]
        steam_styled = [
            Style().foreground(self.theme.accent_secondary).bold(True).render(line)
            for line in steam
        ]
        cup_styled = [
            Style().foreground(self.theme.accent_primary).render(line)
            for line in CUP_AND_ENVELOPE_ART
        ]

        logo_styled = [
            linear_gradient(line, self.theme.gradient_start, self.theme.gradient_end)
            for line in LOGO_LINES
        ]

        tagline = (
            Style()
            .italic(True)
            .foreground(self.theme.fg_secondary)
            .render("Fast, Declarative Terminal Email • IMAP/SMTP • Unified Inbox • PGP • Sieve")
        )

        bar_width = min(52, max(28, width - 28))
        prog = Progress(
            width=bar_width,
            percent=self.progress_ratio,
            fill_style=Style().foreground(self.theme.accent_secondary).bold(True),
        )

        stage_line = (
            Style()
            .foreground(self.theme.fg_primary)
            .render(f"• {self.current_stage_text}")
        )

        badges = (
            Style()
            .foreground(self.theme.fg_muted)
            .render(
                f"v{__version__}  •  Theme: {self.theme.name}  •  Press any key or click to continue"
            )
        )

        inner = join_vertical(
            Align.CENTER,
            "\n".join(steam_styled),
            "\n".join(cup_styled),
            "",
            "\n".join(logo_styled),
            "",
            tagline,
            "",
            prog.view(),
            stage_line,
            "",
            badges,
        )

        card_width = min(max(70, bar_width + 16), max(70, width - 8))
        card = (
            Style()
            .border(DOUBLE_BORDER)
            .border_foreground(self.theme.accent_primary)
            .border_title(
                Style()
                .bold(True)
                .foreground(self.theme.accent_secondary)
                .render(" [ EspressoMail TUI ] "),
                align=Align.CENTER,
            )
            .padding(1, 3)
            .width(card_width - 8)
            .align(Align.CENTER)
            .render(inner)
        )

        return place(width, height, Align.CENTER, Align.CENTER, card)
