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
from textual.widgets import Input, Label, Markdown, OptionList, Rule, Static
from textual.widgets.option_list import Option


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
    return key if len(key) == 1 else key


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
    "main.add_pr": "Add PR by URL",
    "main.manage_sources": "Manage sources (include/exclude)",
    "main.manage_repos": "Manage repos (include/exclude)",
    "main.row_rules": "View row highlighting rules",
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
        width: 90;
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
    #help-scroll {
        height: auto;
        max-height: 100%;
    }
    """

    def __init__(
        self,
        keybindings: dict[str, str] | None = None,
        extensions: list[dict] | None = None,
    ) -> None:
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

        # Extension keybindings
        if self._extensions:
            lines.append("")
            lines.append("[b]  ─── Extensions ───[/]")
            for ext in self._extensions:
                display = _key_display(ext["key"])
                lines.append(f"[b]{display:<14}[/]{ext['name']}")

        kb_text = "\n".join(lines)

        # Symbols legend
        legend = (
            "\n[b]  ─── Symbols ───[/]\n"
            "[dim]Status:     ○ Active   ↻ Waiting   ✓ Approved   ✎ Draft   » Auto-complete   ✓✓ Done   ∅ Abandoned   ⚠ Conflicts[/]\n"
            "[dim]Votes:      ✓ Approved   ↻ Changes requested   ✗ Rejected   ! Required pending[/]\n"
            "[dim]Checks:     ✓ Pass   ✗ Required fail   ~ Optional fail[/]\n"
            "[dim]Comments:   ✓ All resolved   💬 Unresolved threads[/]\n"
            "[dim]Me:         Your vote (Reviews view only)[/]\n"
            "[dim]Pin:        ★ Pinned[/]"
        )

        with Vertical(id="help-dialog"):
            yield Label("PR Dashboard — Help", id="help-title")
            with VerticalScroll(id="help-scroll"):
                yield Static(
                    f"{kb_text}"
                    f"{legend}"
                    f"\n\n[dim]Logs: {LOG_DIR}[/]"
                )


class RowRulesScreen(ModalScreen):
    """Show row highlighting rules with conditions, styles, and descriptions."""

    BINDINGS = [
        Binding("R", "dismiss", "Close", priority=True),
        Binding("escape", "dismiss", "Close"),
        Binding("tab", "noop", "", show=False, priority=True),
    ]

    def action_noop(self) -> None:
        """Consume keys that should not leak to the main app."""

    DEFAULT_CSS = """
    RowRulesScreen {
        align: center middle;
    }
    #rules-dialog {
        width: 96;
        height: auto;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #rules-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #rules-scroll {
        height: auto;
        max-height: 100%;
    }
    """

    def __init__(self, row_rules: list[dict] | None = None) -> None:
        super().__init__()
        self._row_rules = row_rules or []

    def compose(self) -> ComposeResult:
        lines: list[str] = []
        if not self._row_rules:
            lines.append("[dim]No row rules configured.[/]")
        else:
            lines.append("[dim]First matching rule wins. Configure in config.json → display.row_rules[/]")
            lines.append("")
            for i, rule in enumerate(self._row_rules, 1):
                conds = rule.get("conditions", {})
                cond_str = ", ".join(f"{k}={v}" for k, v in conds.items())

                style_parts = []
                if rule.get("color"):
                    style_parts.append(f"bg:{rule['color']}")
                if rule.get("bold"):
                    style_parts.append("bold")
                if rule.get("italic"):
                    style_parts.append("italic")
                if rule.get("strikethrough"):
                    style_parts.append("strike")
                style_str = " + ".join(style_parts) if style_parts else "default"

                # Color swatch using the rule's bgcolor
                color = rule.get("color", "")
                swatch = f"[on {color}]    [/] " if color else "     "

                lines.append(f"{swatch}[b]{i:>2}.[/] {cond_str:<50} → {style_str}")

                desc = rule.get("description", "")
                if desc:
                    lines.append(f"      [bold yellow]→ {desc}[/]")
                action = rule.get("action", "")
                if action:
                    lines.append(f"      [dim]Column: {action}[/]")

            lines.append("")
            lines.append(
                "[dim]Available conditions: role, status, isDraft, mergeStatus, myVote,\n"
                "isRequiredReviewer, hasActiveComments, allCommentsResolved,\n"
                "allRequiredApproved, checksPass, isPinned, myCommentPending[/]"
            )

        with Vertical(id="rules-dialog"):
            yield Label("Row Highlighting Rules", id="rules-title")
            with VerticalScroll(id="rules-scroll"):
                yield Static("\n".join(lines))


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
    #info-scroll {
        height: auto;
        max-height: 100%;
    }
    """

    def __init__(self, accounts: dict[str, str | None], sources: list[str]) -> None:
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
            with VerticalScroll(id="info-scroll"):
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
    #log-scroll {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        ring = get_ring_buffer()
        messages = ring.get_messages()
        content = "\n".join(messages) if messages else "[dim]No log messages yet.[/]"
        with Vertical(id="log-dialog"):
            yield Label("Activity Log", id="log-title")
            yield Static(f"[dim]{LOG_DIR}[/]")
            with VerticalScroll(id="log-scroll"):
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


class AddPrScreen(ModalScreen):
    """Add a PR by URL (Azure DevOps or GitHub)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab", "noop", "", show=False, priority=True),
    ]

    def action_noop(self) -> None:
        """Consume keys that should not leak to the main app."""

    DEFAULT_CSS = """
    AddPrScreen {
        align: center middle;
    }
    #add-pr-dialog {
        width: 76;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #add-pr-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="add-pr-dialog"):
            yield Label("Add PR", id="add-pr-title")
            yield Static("Enter a PR URL (Azure DevOps or GitHub):")
            yield Input(
                placeholder="https://dev.azure.com/.../pullrequest/N  or  https://github.com/.../pull/N",
                id="pr-url-input",
            )
            yield Static("[dim]Enter: Add · Escape: Cancel[/]")

    def on_mount(self) -> None:
        self.query_one("#pr-url-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "pr-url-input":
            url = event.value.strip()
            if url:
                self.dismiss(url)

    def action_cancel(self) -> None:
        self.dismiss("")


class ManageSourcesScreen(ModalScreen):
    """Include and exclude PR sources from the TUI."""

    BINDINGS = [
        Binding("escape", "handle_escape", "Close", priority=True),
        Binding("S", "close", "Close"),
        Binding("space", "toggle_source", "Toggle"),
        Binding("a", "show_add_ado", "Add ADO"),
        Binding("tab", "noop", "", show=False, priority=True),
    ]

    def action_noop(self) -> None:
        """Consume keys that should not leak to the main app."""

    DEFAULT_CSS = """
    ManageSourcesScreen {
        align: center middle;
    }
    #sources-dialog {
        width: 58;
        height: auto;
        max-height: 70%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #sources-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #sources-list {
        height: auto;
        max-height: 14;
    }
    .sources-hidden {
        display: none;
    }
    """

    def __init__(self, store) -> None:
        super().__init__()
        self._store = store
        self._items: list[tuple[str, bool]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="sources-dialog"):
            yield Label("Manage Sources", id="sources-title")
            yield OptionList(id="sources-list")
            yield Input(
                placeholder="ADO org name (Enter to add, Escape to cancel)",
                id="ado-org-input",
                classes="sources-hidden",
            )
            yield Static("[dim]Space: Toggle · a: Add ADO · S/Esc: Close[/]")

    def on_mount(self) -> None:
        self._refresh_list()
        self.query_one("#sources-list", OptionList).focus()

    def _refresh_list(self) -> None:
        self._items = self._store.get_sources_for_manage()
        ol = self.query_one("#sources-list", OptionList)
        ol.clear_options()
        if self._items:
            for src, is_active in self._items:
                icon = "✓" if is_active else "✗"
                label = f"{icon} {source_label(src)}"
                if not is_active:
                    label += " (excluded)"
                ol.add_option(Option(label, id=src))
        else:
            ol.add_option(Option("No sources — sync to discover", disabled=True))

    def action_close(self) -> None:
        self.dismiss(None)

    def action_handle_escape(self) -> None:
        inp = self.query_one("#ado-org-input", Input)
        if inp.has_class("sources-hidden"):
            self.dismiss(None)
        else:
            inp.add_class("sources-hidden")
            inp.value = ""
            self.query_one("#sources-list", OptionList).focus()

    def action_toggle_source(self) -> None:
        ol = self.query_one("#sources-list", OptionList)
        idx = ol.highlighted
        if idx is not None and idx < len(self._items):
            source, _ = self._items[idx]
            self._store.toggle_source(source)
            self._refresh_list()
            new_count = len(self._items)
            if new_count > 0:
                ol.highlighted = min(idx, new_count - 1)

    def action_show_add_ado(self) -> None:
        inp = self.query_one("#ado-org-input", Input)
        inp.remove_class("sources-hidden")
        inp.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "ado-org-input":
            org = event.value.strip()
            if org:
                self._store.include_source(f"ado/{org}")
                self._refresh_list()
            event.input.value = ""
            event.input.add_class("sources-hidden")
            self.query_one("#sources-list", OptionList).focus()


class ManageReposScreen(ModalScreen):
    """Include or exclude repos from review sync."""

    BINDINGS = [
        Binding("escape", "handle_escape", "Close", priority=True),
        Binding("m", "close", "Close"),
        Binding("space", "toggle_repo", "Toggle"),
        Binding("a", "show_add_repo", "Add repo"),
        Binding("tab", "noop", "", show=False, priority=True),
    ]

    def action_noop(self) -> None:
        """Consume keys that should not leak to the main app."""

    DEFAULT_CSS = """
    ManageReposScreen {
        align: center middle;
    }
    #repos-dialog {
        width: 72;
        height: auto;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #repos-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #repos-list {
        height: auto;
        max-height: 22;
    }
    .repos-hidden {
        display: none;
    }
    """

    def __init__(self, store) -> None:
        super().__init__()
        self._store = store
        self._items: list[tuple[dict, bool]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="repos-dialog"):
            yield Label("Manage Repos", id="repos-title")
            yield Static("[dim]Toggle repos to include/exclude from sync[/]")
            yield OptionList(id="repos-list")
            yield Input(
                placeholder="source repo (e.g. ado/msazure Networking-nrp)",
                id="repo-add-input",
                classes="repos-hidden",
            )
            yield Static("[dim]Space: Toggle · a: Add repo · m/Esc: Close[/]")

    def on_mount(self) -> None:
        self._refresh_list()
        self.query_one("#repos-list", OptionList).focus()

    def _refresh_list(self) -> None:
        self._items = self._store.get_repos_for_manage()
        ol = self.query_one("#repos-list", OptionList)
        ol.clear_options()
        if self._items:
            for repo_entry, is_active in self._items:
                src = repo_entry["source"]
                repo = repo_entry["repo"]
                icon = "✓" if is_active else "✗"
                label = f"{icon} {source_label(src)} :: {repo}"
                if not is_active:
                    label += " (excluded)"
                ol.add_option(Option(label, id=f"{src}|{repo}"))
        else:
            ol.add_option(Option("No repos — sync to discover", disabled=True))

    def action_close(self) -> None:
        self.dismiss(None)

    def action_handle_escape(self) -> None:
        inp = self.query_one("#repo-add-input", Input)
        if inp.has_class("repos-hidden"):
            self.dismiss(None)
        else:
            inp.add_class("repos-hidden")
            inp.value = ""
            self.query_one("#repos-list", OptionList).focus()

    def action_toggle_repo(self) -> None:
        ol = self.query_one("#repos-list", OptionList)
        idx = ol.highlighted
        if idx is not None and idx < len(self._items):
            repo_entry, _ = self._items[idx]
            self._store.toggle_repo(repo_entry["source"], repo_entry["repo"])
            self._refresh_list()
            new_count = len(self._items)
            if new_count > 0:
                ol.highlighted = min(idx, new_count - 1)

    def action_show_add_repo(self) -> None:
        inp = self.query_one("#repo-add-input", Input)
        inp.remove_class("repos-hidden")
        inp.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "repo-add-input":
            text = event.value.strip()
            if text:
                parts = text.split(maxsplit=1)
                if len(parts) == 2:
                    source, repo = parts
                    self._store.include_repo(source, repo)
                    self._refresh_list()
            event.input.value = ""
            event.input.add_class("repos-hidden")
            self.query_one("#repos-list", OptionList).focus()
