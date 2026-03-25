"""Modal screens for the PR Dashboard TUI."""

from __future__ import annotations

import re

from .config import DEFAULT_KEYBINDINGS
from .formatting import source_label
from .logger import LOG_DIR, get_ring_buffer
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Markdown, Rule, Static


def _key_display(key: str) -> str:
    """Convert internal key name to display string."""
    _DISPLAY = {
        "question_mark": "?",
        "slash": "/",
        "space": "Space",
        "escape": "Esc",
        "tab": "Tab",
        "enter": "Enter",
        "backspace": "Bksp",
    }
    if key in _DISPLAY:
        return _DISPLAY[key]
    if "+" in key:
        mod, rest = key.split("+", 1)
        return f"{mod.capitalize()}+{_DISPLAY.get(rest, rest.upper())}"
    return key.upper() if len(key) == 1 else key


# Action name → human-readable description
_ACTION_DESCRIPTIONS: dict[str, str] = {
    "main.help": "Toggle this help",
    "main.toggle_view": "Toggle view: My PRs ↔ Reviews",
    "main.refresh": "Refresh all PRs",
    "main.sync": "Sync all registered sources",
    "main.remove": "Remove selected PR",
    "main.remove_done": "Remove all done PRs",
    "main.open": "Open selected PR in browser",
    "main.copy_url": "Copy PR URL to clipboard",
    "main.filter": "Filter PRs",
    "main.info": "View connected sources & accounts",
    "main.log": "View activity log",
    "main.peek": "Peek at PR description & comments",
    "main.pin": "Pin/unpin selected PR",
    "main.filter_pinned": "Toggle pinned-only filter",
    "main.quit": "Exit",
}


class HelpScreen(ModalScreen):
    BINDINGS = [
        Binding("question_mark", "dismiss", "Close"),
        Binding("escape", "dismiss", "Close"),
        Binding("tab", "noop", "", show=False, priority=True),
    ]

    def action_noop(self) -> None:
        """Consume keys that should not leak to the main app."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-dialog {
        width: 64;
        height: auto;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #help-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(self, keybindings: dict[str, str] | None = None, extensions: list[dict] | None = None) -> None:
        super().__init__()
        self._keybindings = keybindings or DEFAULT_KEYBINDINGS
        self._extensions = extensions or []

    def compose(self) -> ComposeResult:
        # Build keybinding lines from effective config
        lines = []
        for action, key in self._keybindings.items():
            desc = _ACTION_DESCRIPTIONS.get(action, action)
            display = _key_display(key)
            lines.append(f"[b]{display:<14}[/]{desc}")

        # Always add escape (not configurable)
        lines.append(f"[b]{'Esc':<14}[/]Clear filter / close modal")

        # Extension keybindings (rendered separately from built-ins)
        if self._extensions:
            lines.append("")
            lines.append("  ─── Extensions ───")
            for ext in self._extensions:
                display = _key_display(ext["key"])
                lines.append(f"[b]{display:<14}[/]{ext['name']}")

        kb_text = "\n".join(lines)

        with Vertical(id="help-dialog"):
            yield Label("Keyboard Shortcuts", id="help-title")
            yield Static(
                f"{kb_text}\n\n"
                "[dim]Status:   ○ Active   ↻ Waiting   ✓ Approved   ✎ Draft   » Auto-complete   ✓✓ Done   ∅ Abandoned   ⚠ Conflicts[/]\n"
                "[dim]Votes:    ✓ Approved   ↻ Changes requested   ✗ Rejected   ! Required pending[/]\n"
                "[dim]Checks:   ✓ Pass   ✗ Required fail   ~ Optional fail[/]\n"
                "[dim]Comments: ✓ All resolved   💬 Unresolved threads[/]\n"
                "[dim]Me:       Your vote (Reviews view only)[/]\n"
                "[dim]Pin:      ★ Pinned (sorted to top)[/]\n\n"
                f"[dim]Logs: {LOG_DIR}[/]"
            )


class InfoScreen(ModalScreen):
    """Show connected accounts and registered sources."""

    BINDINGS = [
        Binding("i", "dismiss", "Close"),
        Binding("escape", "dismiss", "Close"),
        Binding("tab", "noop", "", show=False, priority=True),
    ]

    def action_noop(self) -> None:
        """Consume keys that should not leak to the main app."""

    DEFAULT_CSS = """
    InfoScreen {
        align: center middle;
    }
    #info-dialog {
        width: 50;
        height: auto;
        max-height: 60%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #info-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(
        self, accounts: dict[str, str | None], sources: list[str]
    ) -> None:
        super().__init__()
        self._accounts = accounts
        self._sources = sources

    def compose(self) -> ComposeResult:
        lines = []

        # Accounts
        lines.append("[b]Accounts[/]")
        for provider, user in self._accounts.items():
            icon = "🟢" if user else "🔴"
            label = user or "disconnected"
            lines.append(f"  {icon} {provider}: {label}")

        lines.append("")

        # Sources
        lines.append(f"[b]Sources ({len(self._sources)})[/]")
        if self._sources:
            for src in sorted(self._sources):
                lines.append(f"  • {source_label(src)}")
        else:
            lines.append("  [dim]No sources registered[/]")

        with Vertical(id="info-dialog"):
            yield Label("Info", id="info-title")
            yield Static("\n".join(lines))


class LogScreen(ModalScreen):
    BINDINGS = [
        Binding("l", "dismiss", "Close"),
        Binding("escape", "dismiss", "Close"),
        Binding("tab", "noop", "", show=False, priority=True),
    ]

    def action_noop(self) -> None:
        """Consume keys that should not leak to the main app."""

    DEFAULT_CSS = """
    LogScreen {
        align: center middle;
    }
    #log-dialog {
        width: 90%;
        height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #log-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #log-content {
        height: 1fr;
        overflow-y: auto;
    }
    """

    def compose(self) -> ComposeResult:
        ring = get_ring_buffer()
        messages = ring.get_messages()
        content = "\n".join(messages) if messages else "[dim]No log messages yet.[/]"
        with Vertical(id="log-dialog"):
            yield Label("Activity Log", id="log-title")
            yield Static(f"[dim]{LOG_DIR}[/]")
            yield Static(content, id="log-content")


# ── Image sanitisation for markdown ───────────────────────────────────────

_IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_IMG_HTML_RE = re.compile(r"<img[^>]*?>", re.IGNORECASE)


def _sanitize_markdown(text: str) -> str:
    """Replace image references with placeholder text."""
    text = _IMG_MD_RE.sub(r"[📷 image: \1]", text)
    text = _IMG_HTML_RE.sub("[📷 image]", text)
    return text


class PeekScreen(ModalScreen):
    """Quick-peek modal: scrollable PR description + active comment threads."""

    BINDINGS = [
        Binding("v", "dismiss", "Close"),
        Binding("escape", "dismiss", "Close"),
        Binding("tab", "noop", "", show=False, priority=True),
    ]

    def action_noop(self) -> None:
        """Consume keys that should not leak to the main app."""

    DEFAULT_CSS = """
    PeekScreen {
        align: center middle;
    }
    #peek-dialog {
        width: 90%;
        height: 85%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #peek-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #peek-meta {
        margin-bottom: 1;
        color: $text-muted;
    }
    #peek-scroll {
        height: 1fr;
    }
    """

    def __init__(self, pr: dict) -> None:
        super().__init__()
        self._pr = pr

    def compose(self) -> ComposeResult:
        title = self._pr.get("title", "PR")
        pr_id = self._pr.get("id", "")

        # Active comment threads
        threads = self._pr.get("threads") or []
        active = self._pr.get("commentsActive", 0) or 0
        total = self._pr.get("commentsTotal", 0) or 0

        # Meta line
        if total == 0:
            meta = "No comments"
        elif active == 0:
            meta = f"All {total} comments resolved ✓"
        else:
            meta = f"{active} active / {total} total comments"

        # Description markdown
        desc = _sanitize_markdown(self._pr.get("description", "") or "")
        if not desc.strip():
            desc = "*No description provided.*"

        with Vertical(id="peek-dialog"):
            yield Label(f"#{pr_id} — {title}", id="peek-title")
            yield Static(f"[dim]{meta}  ·  v/Esc to close[/]", id="peek-meta")
            with VerticalScroll(id="peek-scroll"):
                # Description as its own Markdown widget
                yield Markdown(f"## 📝 Description\n\n{desc}")

                # Comment threads — each as a separate Markdown widget
                if total == 0:
                    yield Rule()
                    yield Markdown("*No comments.*")
                elif active == 0:
                    yield Rule()
                    yield Markdown(f"*All {total} comments resolved* ✓")
                elif threads:
                    for i, thread in enumerate(threads, 1):
                        yield Rule()
                        fp = thread.get("filePath") or ""
                        line = thread.get("line")
                        if fp:
                            loc = f"`{fp}"
                            if line:
                                loc += f":{line}"
                            loc += "`"
                        else:
                            loc = "General"

                        parts = [f"### 💬 {i}. {loc}\n"]
                        for c in thread.get("comments", []):
                            author = c.get("author", "unknown")
                            text = _sanitize_markdown(c.get("text", ""))
                            parts.append(f"**{author}:**\n{text}\n")
                        yield Markdown("\n".join(parts))
                else:
                    yield Rule()
                    yield Markdown(f"*{active} active / {total} total comments*")
