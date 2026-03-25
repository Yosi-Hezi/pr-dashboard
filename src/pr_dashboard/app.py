"""PR Dashboard — interactive TUI for Azure DevOps pull requests."""

import asyncio
import json
import subprocess
import tempfile
import webbrowser
from pathlib import Path

from .ado_client import AdoApiError, AdoAuthError, AdoClient
from .config import (
    ACTION_DEFS,
    COLUMN_DEFS,
    get_display_config,
    get_extensions,
    get_keybindings,
    load_config,
)
from .data import PrDataStore
from .formatting import (
    VOTE_EMOJI,
    esc,
    format_pin,
    format_status_label,
    format_time_ago,
    get_cell_value,
    pr_key,
    pr_matches_filter,
    pr_row_style,
    shorten_repo,
    sort_prs,
)
from .gh_client import GhClient
from .logger import get_logger
from .screens import HelpScreen, InfoScreen, LogScreen, PeekScreen
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.events import Key
from textual.widgets import DataTable, Footer, Header, Input, Static

log = get_logger()


class StyledDataTable(DataTable):
    """DataTable with per-row background color support."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._row_bg: dict[int, object] = {}

    def set_row_styles(self, styles: dict[int, object]) -> None:
        self._row_bg = styles

    def _get_row_style(self, row_index: int, base_style):
        style = super()._get_row_style(row_index, base_style)
        if row_index in self._row_bg:
            style += self._row_bg[row_index]
        return style


# ── Main app ──────────────────────────────────────────────────────────────


class PRDashboard(App):
    CSS = """
    Header {
        dock: top;
    }
    #filter-input {
        dock: top;
        display: none;
        height: 1;
        border: none;
        padding: 0 1;
    }
    #filter-input.visible {
        display: block;
    }
    DataTable {
        height: 1fr;
    }
    #detail-panel {
        height: auto;
        max-height: 10;
        padding: 0 1;
        background: $surface;
        border-top: solid $primary;
        overflow-y: auto;
    }
    #status-bar {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
        text-style: bold;
    }
    """

    TITLE = "PR Dashboard — My PRs"

    # Build BINDINGS at class level so Textual registers them.
    # Order follows display.footer_actions (footer items first, then hidden ones).
    _EFFECTIVE_KB = get_keybindings()
    _DISPLAY_CFG = get_display_config()
    _FOOTER_ACTIONS = _DISPLAY_CFG.get("footer_actions", [])
    _FOOTER_SET = set(_FOOTER_ACTIONS)
    # Footer actions in configured order first, then remaining actions
    _ORDERED_ACTIONS = list(_FOOTER_ACTIONS)
    for _a in _EFFECTIVE_KB:
        if _a not in _FOOTER_SET:
            _ORDERED_ACTIONS.append(_a)
    BINDINGS = []
    for _action in _ORDERED_ACTIONS:
        _key = _EFFECTIVE_KB.get(_action)
        _meta = ACTION_DEFS.get(_action)
        if _key and _meta:
            _desc, _method, _pri = _meta
            _show = _action in _FOOTER_SET
            BINDINGS.append(Binding(_key, _method, _desc, show=_show, priority=_pri))

    # Extension bindings
    _EXTENSIONS = get_extensions()
    for _idx, _ext in enumerate(_EXTENSIONS):
        _method_name = f"ext_{_idx}"
        BINDINGS.append(Binding(_ext["key"], _method_name, _ext["name"], show=True))

    def __init__(self) -> None:
        super().__init__()
        # Apply theme from config (default: textual-dark)
        cfg = load_config()
        self.theme = cfg.get("theme", "textual-dark")
        self.store = PrDataStore()
        self.prs: list[dict] = []
        self.filter_query: str = ""
        self._view_mode: str = "mine"  # "all", "mine", "reviews"
        self._filter_pinned: bool = False
        self._refreshing_all: bool = False
        self._removing_prs: set[str] = set()
        self._az_user: str | None = None
        self._gh_user: str | None = None
        self._display_cfg = get_display_config()

        # Store extensions for help screen
        self._extensions = get_extensions()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Disable priority bindings when a modal screen is active."""
        if len(self.screen_stack) > 1:
            if action == "toggle_view" or action.startswith("ext_"):
                return False
        return True

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(
            placeholder="Type to filter PRs... (Escape to clear)", id="filter-input"
        )
        yield StyledDataTable(id="pr-table", cursor_type="row")
        yield Static("Select a PR to see details", id="detail-panel")
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table_columns()
        table = self.query_one("#pr-table", StyledDataTable)
        table.focus()
        self.load_and_display()

        # Check auth status + first-run auto-discover
        self.run_worker(self._init_auth_and_sync())

    def _get_columns(self) -> list[str]:
        """Get column IDs for current view mode from display config."""
        view = "reviews" if self._view_mode == "reviews" else "mine"
        return self._display_cfg.get("columns", {}).get(view, [])

    def _setup_table_columns(self) -> None:
        """Set up table columns based on current view mode."""
        table = self.query_one("#pr-table", StyledDataTable)
        table.clear(columns=True)
        table.set_row_styles({})
        columns = self._get_columns()
        headers = [COLUMN_DEFS.get(c, {}).get("header", c) for c in columns]
        table.add_columns(*headers)

    async def _init_auth_and_sync(self) -> None:
        """Check auth status and auto-discover on first run."""

        self.notify("Checking authentication...", timeout=5)
        log.info("Startup: checking auth status")

        # Check ADO + GitHub auth in parallel, keep clients alive for reuse
        ado_client = AdoClient()
        gh_client = GhClient()

        async def _check_ado():
            try:
                return await ado_client.get_az_username()
            except Exception:
                return None

        async def _check_gh():
            try:
                return await gh_client.check_auth()
            except Exception:
                return None

        self._az_user, self._gh_user = await asyncio.gather(_check_ado(), _check_gh())
        self._update_status_bar_accounts()
        log.info("Startup: auth complete (az=%s, gh=%s)", self._az_user, self._gh_user)

        # First-run: auto-discover sources if none registered
        sources = self.store.get_sources()
        if not sources:
            self.notify("First run — discovering sources...", timeout=5)
            log.info("Startup: no sources registered, discovering orgs")
            try:
                orgs = await ado_client.discover_orgs()
                for org in orgs:
                    self.store.add_source(f"ado/{org}")
                if self._gh_user:
                    self.store.add_source("github")
                sources = self.store.get_sources()
                log.info("Auto-registered %d sources", len(sources))
            except Exception as exc:
                log.warning("Auto-discover failed: %s", exc)

        # Sync if no PRs loaded — reuse authenticated clients
        if not self.prs and sources:
            self._refreshing_all = True
            self.notify(f"Syncing {len(sources)} source(s)...", timeout=10)
            log.info("Startup: syncing %d sources", len(sources))
            try:
                # Build ado_clients dict from the single reusable client
                ado_clients: dict[str, AdoClient] = {}
                for src in sources:
                    if src.startswith("ado/"):
                        org = src.removeprefix("ado/")
                        if org == ado_client.org:
                            ado_clients[org] = ado_client
                await self.store.sync(
                    ado_clients=ado_clients,
                    gh_client=gh_client if self._gh_user else None,
                )
            except (AdoApiError, AdoAuthError) as exc:
                self._handle_error(exc, "Sync")
            except Exception as exc:
                self._handle_error(exc, "Sync")
            finally:
                self._refreshing_all = False

            # Schedule UI update on the main message loop so the screen
            # repaints even though no user event initiated this worker.
            def _finish_startup():
                self.load_and_display()
                self.notify(f"Ready — {len(self.prs)} PRs loaded", timeout=3)
                log.info("Startup: complete, %d PRs loaded", len(self.prs))

            self.call_later(_finish_startup)
        else:
            log.info("Startup: %d cached PRs, skipping sync", len(self.prs))

        # Clean up the reusable ADO client
        await ado_client.close()

    def _update_title(self) -> None:
        """Update app title to reflect current view mode."""
        labels = {"mine": "My PRs", "reviews": "Reviews"}
        self.title = f"PR Dashboard — {labels.get(self._view_mode, 'My PRs')}"

    def _update_status_bar_accounts(self) -> None:
        """Update status bar with connected account info."""
        parts = []
        if self._az_user:
            parts.append(f"🟢 az:{self._az_user}")
        else:
            parts.append("🔴 az:disconnected")
        if self._gh_user:
            parts.append(f"🟢 gh:{self._gh_user}")
        else:
            parts.append("🔴 gh:disconnected")

        sources = self.store.get_sources()
        parts.append(f"{len(sources)} sources")

        # Show view mode
        labels = {"mine": "Mine", "reviews": "Reviews"}
        view_label = labels.get(self._view_mode, "Mine")
        if self._filter_pinned:
            view_label += " ★"
        parts.append(f"📋 {view_label}")

        # Show PR count for current view
        view_prs = [
            p
            for p in self.prs
            if self._view_mode != "mine" or p.get("role", "author") == "author"
        ]
        view_prs = [
            p
            for p in view_prs
            if self._view_mode != "reviews" or p.get("role", "author") == "reviewer"
        ]
        if self.filter_query or self._filter_pinned:
            filtered = len(self.get_visible_prs())
            parts.append(f"🔍 {filtered}/{len(view_prs)}")
        else:
            parts.append(f"{len(view_prs)} PRs")

        self.query_one("#status-bar", Static).update(" · ".join(parts))

    def get_visible_prs(self) -> list[dict]:
        prs = sort_prs(self.prs)
        if self._view_mode == "mine":
            prs = [p for p in prs if p.get("role", "author") == "author"]
        elif self._view_mode == "reviews":
            prs = [p for p in prs if p.get("role", "author") == "reviewer"]
        if self._filter_pinned:
            prs = [p for p in prs if p.get("pinned")]
        if not self.filter_query:
            return prs
        return [p for p in prs if pr_matches_filter(p, self.filter_query)]

    def load_and_display(self) -> None:
        self.prs = self.store.load_prs()
        self.refresh_table()
        # Ensure detail panel is populated (row_highlighted may not fire
        # if cursor position didn't change, e.g. on first load)
        pr = self.get_selected_pr()
        if pr:
            self._update_detail_panel(pr)

    def refresh_table(self) -> None:
        table = self.query_one("#pr-table", StyledDataTable)
        prev_row = table.cursor_row
        self._setup_table_columns()
        visible = self.get_visible_prs()
        columns = self._get_columns()
        is_reviews = self._view_mode == "reviews"
        row_colors = self._display_cfg.get("row_colors", [])
        row_styles: dict[int, object] = {}
        for idx, pr in enumerate(visible):
            row_key = pr_key(pr)
            row_data = [
                get_cell_value(c, pr, is_reviews=is_reviews, display=self._display_cfg)
                for c in columns
            ]
            table.add_row(*row_data, key=row_key)
            style = pr_row_style(pr, rules=row_colors)
            if style:
                row_styles[idx] = style
        table.set_row_styles(row_styles)
        if table.row_count > 0:
            table.move_cursor(row=min(prev_row, table.row_count - 1))
        else:
            self.query_one("#detail-panel", Static).update("")
        self._update_status_bar_accounts()

    def _handle_error(self, exc: Exception, context: str) -> None:
        """Show error via toast + log."""
        msg = f"{context}: {exc}"
        log.error(msg)
        self.notify(str(exc), title=context, severity="error", timeout=8)

    def get_selected_pr(self) -> dict | None:
        table = self.query_one("#pr-table", StyledDataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        key_val = row_key.value
        return next((p for p in self.prs if pr_key(p) == key_val), None)

    # ── Extensions ────────────────────────────────────────────────────────

    def _run_extension(self, ext: dict) -> None:
        """Launch an extension script with the selected PR's data."""
        pr = self.get_selected_pr()
        if not pr:
            self.notify("No PR selected", severity="warning", timeout=3)
            return
        self.notify(f"Running {ext['name']}...", timeout=3)
        self.run_worker(self._run_extension_async(ext, pr))

    async def _run_extension_async(self, ext: dict, pr: dict) -> None:
        tmp = None
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", prefix="pr-dash-ext-", delete=False
            )
            json.dump(pr, tmp, default=str)
            tmp.close()
            cmd = ext["command"].replace("{json_file}", tmp.name)
            log.info("Extension %s: %s", ext["name"], cmd)
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            out = stdout.decode(errors="replace").strip()
            err = stderr.decode(errors="replace").strip()
            # Log all output for visibility in the log screen
            for line in out.splitlines():
                log.info("  [%s] %s", ext["name"], line)
            for line in err.splitlines():
                log.warning("  [%s] %s", ext["name"], line)
            if proc.returncode == 0:
                # Show last non-empty stdout line in toast
                last = out.splitlines()[-1] if out else ""
                msg = f"{ext['name']} ✓ — {last[:80]}" if last else f"{ext['name']} ✓"
                self.notify(msg, timeout=5)
            else:
                snippet = err[:200] or out[:200] or "unknown error"
                self.notify(
                    f"{ext['name']} failed: {snippet}", severity="error", timeout=8
                )
                log.error("Extension %s exit code %d", ext["name"], proc.returncode)
        except Exception as exc:
            self._handle_error(exc, f"Extension {ext['name']}")
        finally:
            if tmp:
                try:
                    Path(tmp.name).unlink(missing_ok=True)
                except OSError:
                    pass

    # ── Detail panel ───────────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key:
            key_val = event.row_key.value
            pr = next((p for p in self.prs if pr_key(p) == key_val), None)
            if pr:
                self._update_detail_panel(pr)

    def _update_detail_panel(self, pr: dict) -> None:
        panel = self.query_one("#detail-panel", Static)
        parts = []

        # Line 1: status label, branches, repo @ source
        status_text = format_status_label(pr.get("status", ""), pr)
        src = esc(pr.get("source", ""))
        source_branch = esc(pr.get("sourceBranch", "?"))
        target = esc(pr.get("targetBranch", "?"))
        repo_at_src = f"{esc(shorten_repo(pr.get('repoName', '')))} @ {src}"
        pin_indicator = "★ " if pr.get("pinned") else ""
        parts.append(
            f"{pin_indicator}{status_text} [bold]#{pr['id']}[/]  "
            f"[dim]{target}[/] ← [cyan]{source_branch}[/]   "
            f"[dim]{repo_at_src}[/]"
        )
        parts.append(f"[bold]{esc(pr.get('title', ''))}[/]")

        # Line 2: reviewers — single line (hide optional no-votes)
        reviews = pr.get("reviews", [])
        if reviews:
            visible = [
                r
                for r in reviews
                if r.get("isRequired") or (r.get("vote") and r["vote"] != "NoVote")
            ]

            def _fmt_reviewer(r: dict) -> str:
                vote = r.get("vote", "NoVote") or "NoVote"
                if vote in VOTE_EMOJI:
                    symbol = VOTE_EMOJI[vote]
                else:
                    symbol = "!" if r.get("isRequired") else "·"
                return f"{symbol} {esc(r['name'])}"

            if visible:
                parts.append(
                    f"[bold]Reviewers:[/] {'  '.join(_fmt_reviewer(r) for r in visible)}"
                )
            else:
                parts.append("[bold]Reviewers:[/] [dim]none[/]")
        else:
            parts.append("[bold]Reviewers:[/] [dim]none[/]")

        # Line 3: checks — Option B: verdict + category counts + failing names
        rp = pr.get("requiredPass")
        rt = pr.get("requiredTotal")
        op = pr.get("optionalPass", 0)
        ot = pr.get("optionalTotal", 0)
        checks = pr.get("checks", [])
        if rt is not None:
            verdict = "✓ PASSED" if rp >= rt else "✗ FAILED"
            req_failed = [
                c for c in checks if c.get("isBlocking") and c["status"] != "approved"
            ]
            opt_failed = [
                c
                for c in checks
                if not c.get("isBlocking") and c["status"] != "approved"
            ]
            check_parts = [f"[bold]Checks:[/] {verdict}"]
            if req_failed:
                names = " ".join(f"✗ {esc(c['name'])}" for c in req_failed)
                check_parts.append(f"Required {rp}/{rt}: {names}")
            else:
                check_parts.append(f"Required {rp}/{rt}")
            if ot > 0:
                if opt_failed:
                    names = " ".join(f"~ {esc(c['name'])}" for c in opt_failed)
                    check_parts.append(f"Optional {op}/{ot}: {names}")
                else:
                    check_parts.append(f"Optional {ot}/{ot}")
            parts.append("   ".join(check_parts))
        else:
            parts.append("[bold]Checks:[/] [dim]?[/]")

        # Line 4: comments + timestamps
        active = pr.get("commentsActive", 0) or 0
        total = pr.get("commentsTotal", 0) or 0
        last_comment = format_time_ago(pr.get("lastCommentDate"))
        created = format_time_ago(pr.get("creationDate"))
        updated = format_time_ago(pr.get("lastUpdated"))
        fetched = format_time_ago(pr.get("lastLoaded"))
        parts.append(
            f"[bold]Comments:[/] {active} active / {total} total (last {last_comment})  "
            f"[dim]│[/] Created {created} · Updated {updated} · Fetched {fetched}"
        )

        # Line 5+: work items (ADO only) — inline with URL
        work_items = pr.get("workItems", [])
        if work_items:
            wi_parts = []
            for wi in work_items:
                wi_text = f"[dim]{esc(wi.get('type', ''))}[/] #{wi['id']} {esc(wi.get('title', ''))}"
                if wi.get("url"):
                    wi_text += f"  [dim]{wi['url']}[/]"
                wi_parts.append(wi_text)
            parts.append(f"[bold]Work Items:[/] {wi_parts[0]}")
            for wp in wi_parts[1:]:
                parts.append(f"            {wp}")

        panel.update("\n".join(parts))

    # ── Filter ─────────────────────────────────────────────────────────────

    def action_show_filter(self) -> None:
        filter_input = self.query_one("#filter-input", Input)
        filter_input.add_class("visible")
        filter_input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self.filter_query = event.value
            self.refresh_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            self.query_one("#pr-table", StyledDataTable).focus()

    def on_key(self, event: Key) -> None:
        filter_input = self.query_one("#filter-input", Input)
        if event.key == "escape":
            if filter_input.has_class("visible"):
                filter_input.remove_class("visible")
                filter_input.value = ""
                self.filter_query = ""
                self.refresh_table()
                self.query_one("#pr-table", StyledDataTable).focus()
                event.prevent_default()

    # ── View toggle ──────────────────────────────────────────────────────────

    def action_toggle_view(self) -> None:
        """Toggle view mode: mine ↔ reviews."""
        self._view_mode = "reviews" if self._view_mode == "mine" else "mine"
        self._update_title()
        self.refresh_table()

    # ── Refresh ────────────────────────────────────────────────────────────

    def action_refresh_all(self) -> None:
        if self._refreshing_all:
            return
        self._refreshing_all = True
        self.notify("Refreshing all PRs...", timeout=5)
        self.run_worker(self._refresh_all())

    async def _refresh_all(self) -> None:
        try:
            await self.store.refresh_all()
        except (AdoApiError, AdoAuthError) as exc:
            self._handle_error(exc, "Refresh all")
            return
        finally:
            self._refreshing_all = False
        self.load_and_display()
        self.notify(f"All PRs refreshed — {len(self.prs)} loaded", timeout=3)

    # ── Sync ───────────────────────────────────────────────────────────────

    def action_sync(self) -> None:
        if self._refreshing_all:
            return
        self._refreshing_all = True
        sources = self.store.get_sources()
        self.notify(f"Syncing {len(sources)} source(s)...", timeout=5)
        self.run_worker(self._sync())

    async def _sync(self) -> None:
        try:
            await self.store.sync()
        except (AdoApiError, AdoAuthError) as exc:
            self._handle_error(exc, "Sync")
            return
        except Exception as exc:
            self._handle_error(exc, "Sync")
            return
        finally:
            self._refreshing_all = False
        self.load_and_display()
        self.notify(f"Synced — {len(self.prs)} PRs loaded", timeout=3)

    # ── Remove ─────────────────────────────────────────────────────────────

    def action_remove_selected(self) -> None:
        pr = self.get_selected_pr()
        if not pr:
            return
        pr_id = pr["id"]
        source = pr.get("source", "")
        key = pr_key(pr)
        if key in self._removing_prs:
            return
        self._removing_prs.add(key)
        self.notify(f"Removing PR #{pr_id}...", timeout=3)
        self.store.remove(pr_id, source=source)
        self._removing_prs.discard(key)
        self.load_and_display()
        self.notify(f"PR #{pr_id} removed", timeout=3)

    def action_remove_done(self) -> None:
        done_count = sum(
            1 for p in self.prs if p.get("status") in ("completed", "abandoned")
        )
        if done_count == 0:
            self.notify("No done PRs to remove", timeout=3)
            return
        removed = self.store.clean()
        self.load_and_display()
        self.notify(f"Removed {removed} done PRs", timeout=3)

    # ── Open ───────────────────────────────────────────────────────────────

    def action_open_selected(self) -> None:
        pr = self.get_selected_pr()
        if not pr or not pr.get("url"):
            return
        webbrowser.open(pr["url"])
        self.notify(f"Opened PR #{pr['id']} in browser", timeout=3)

    def action_copy_url(self) -> None:
        pr = self.get_selected_pr()
        if not pr or not pr.get("url"):
            return
        try:
            subprocess.run(
                ["clip.exe"],
                input=pr["url"].encode(),
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.notify(f"Copied URL for PR #{pr['id']}", timeout=3)
        except Exception as exc:
            self._handle_error(exc, "Copy URL")

    # ── Log, Help, Info & Peek ──────────────────────────────────────────────

    def action_show_log(self) -> None:
        self.push_screen(LogScreen())

    def action_toggle_help(self) -> None:
        self.push_screen(HelpScreen(self._EFFECTIVE_KB, self._extensions))

    def action_show_info(self) -> None:
        accounts = {
            "az": self._az_user,
            "gh": self._gh_user,
        }
        sources = self.store.get_sources()
        self.push_screen(InfoScreen(accounts, sources))

    def action_peek_selected(self) -> None:
        pr = self.get_selected_pr()
        if not pr:
            return
        self.push_screen(PeekScreen(pr))

    # ── Pin/Unpin ─────────────────────────────────────────────────────────

    def action_toggle_pin(self) -> None:
        pr = self.get_selected_pr()
        if not pr:
            return
        pr_id = pr["id"]
        source = pr.get("source", "")
        new_state = not pr.get("pinned", False)
        pr["pinned"] = new_state
        # Update just the ★ cell — no full rebuild
        table = self.query_one("#pr-table", StyledDataTable)
        columns = self._get_columns()
        if "pin" in columns:
            pin_col_idx = columns.index("pin")
            table.update_cell_at(
                Coordinate(table.cursor_row, pin_col_idx), format_pin(pr)
            )
        self.store.toggle_pin(pr_id, source=source)
        verb = "Pinned" if new_state else "Unpinned"
        self.notify(f"{verb} PR #{pr_id}", timeout=3)

    def action_toggle_filter_pinned(self) -> None:
        self._filter_pinned = not self._filter_pinned
        self.refresh_table()
        if self._filter_pinned:
            self.notify("Showing pinned PRs only", timeout=3)
        else:
            self.notify("Showing all PRs", timeout=3)


# Register extension action methods at class level so Textual finds them
for _idx, _ext in enumerate(PRDashboard._EXTENSIONS):

    def _make_ext_action(ext=_ext):
        def action(self):
            self._run_extension(ext)

        return action

    setattr(PRDashboard, f"action_ext_{_idx}", _make_ext_action())


def main():
    app = PRDashboard()
    app.run()


if __name__ == "__main__":
    main()
