# Contributing to EspressoMail

Thank you for your interest in contributing to **EspressoMail**! This document outlines the development setup, architectural guidelines, testing requirements, and commit message conventions.

---

## 1. Development Environment Setup

EspressoMail requires **Python 3.10+** and is built on the **[Espresso TUI](https://github.com/kimusan/espresso)** framework.

```bash
git clone https://github.com/kimusan/EspressoMail.git
cd EspressoMail
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,images]"
```

---

## 2. Running the Test Suite

All changes must be accompanied by deterministic unit or integration tests in `tests/`.

```bash
python3 -m unittest discover -s tests -v
```

### Testing TEA Models Deterministically
Because EspressoMail follows **The Elm Architecture (TEA)**, you can test any UI interaction without a live terminal by instantiating `EspressoMailApp` (with `LocalStore(":memory:")`) and passing `KeyMsg` or `MouseMsg` instances directly to `app.update(msg)`:

```python
from espresso import KeyMsg, WindowSizeMsg
from espressomail.storage import LocalStore
from espressomail.ui import EspressoMailApp

store = LocalStore(":memory:")
app = EspressoMailApp(store=store, show_splash=False, demo_mode=True)
app, _ = app.update(WindowSizeMsg(120, 34))
app, _ = app.update(KeyMsg("T"))  # Cycle color scheme
assert app.theme.id == "catppuccin"
```

---

## 3. Version Control & Conventional Commits

We strictly follow the **[Conventional Commits](https://www.conventionalcommits.org/)** specification:

1. **Format**:
   ```text
   <type>(<optional scope>): <concise summary in imperative mood>

   <2 to 3 lines of body text explaining the specific changes made
   and the rationale behind the implementation.>
   ```
2. **Standard Type Prefixes**:
   - `feat`: A new user-facing feature or protocol capability
   - `fix`: A bug fix
   - `docs`: Documentation additions or updates
   - `test`: Adding or refactoring unit/integration tests
   - `refactor`: Code restructuring without changing external behavior
   - `chore`: Build scripts, packaging (`pyproject.toml`), `.gitignore`, or tooling
3. **Clean Repository Policy**:
   - Make small, logical feature commits.
   - **Never** commit AI-related configuration files, logs, or chat histories (`.claude/`, `CLAUDE.md`, `.cursor/`, `.gemini/`, `.aider*`, `AGENTS.md`, etc.) or local `.db` / `.sqlite` files.

---

## 4. Adding a New Color Theme

To add a new color scheme, register a `Theme` entry inside `THEMES` in `src/espressomail/themes/engine.py`:

```python
"my_theme": Theme(
    id="my_theme",
    name="My Custom Theme",
    description="Short description of the color palette",
    bg_primary="#...",
    ...
)
```

It will automatically become available via the `T` theme shortcut, the **View → Switch Color Scheme** menu, the Command Palette (`Ctrl+P`), and the `--theme` CLI flag.
