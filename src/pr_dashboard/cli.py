"""CLI entry point for PR Dashboard — headless mode with rich output."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from .ado_client import AdoApiError, AdoAuthError
from .cli_sources import (
    cmd_repos,
    cmd_repos_exclude,
    cmd_repos_include,
    cmd_sources,
    cmd_sources_exclude,
    cmd_sources_include,
)
from .config import COLUMN_DEFS, get_display_config, get_sync_config
from .data import PrDataStore
from .formatting import (
    VOTE_EMOJI,
    esc,
    format_checks,
    format_status,
    format_status_label,
    format_time_ago,
    get_cell_value,
    shorten_repo,
    sort_prs,
    truncate,
)
from .logger import get_logger
from rich.console import Console
from rich.table import Table

log = get_logger()
console = Console()


def _pr_table(prs: list[dict], title: str | None = None, role: str = "") -> Table:
    """Build a rich Table from a list of PR dicts."""
    display = get_display_config()
    prs = sort_prs(prs)
    is_reviews = role == "reviewer"
    view = "reviews" if is_reviews else "mine"
    columns = display.get("columns", {}).get(view, [])

    table = Table(title=title, show_lines=False, pad_edge=False)
    for col in columns:
        col_def = COLUMN_DEFS.get(col, {})
        header = col_def.get("header", col)
        table.add_column(header)

    for pr in prs:
        row = [
            get_cell_value(c, pr, is_reviews=is_reviews, display=display)
            for c in columns
        ]
        table.add_row(*row)
    return table


def _show_pr_detail(pr: dict) -> None:
    """Print detailed info for a single PR."""
    status_text = format_status_label(pr.get("status", ""), pr)
    src = pr.get("source", "")
    source = esc(pr.get("sourceBranch", "?"))
    target = esc(pr.get("targetBranch", "?"))

    console.print()
    console.print(
        f"{status_text} {src} [bold]#{pr['id']}[/]  "
        f"[dim]{target}[/] ← [cyan]{source}[/]  "
        f"[dim]{esc(shorten_repo(pr.get('repoName', '')))}[/]"
    )
    console.print(f"[bold]{esc(pr.get('title', ''))}[/]")
    console.print(f"[dim]{pr.get('url', '')}[/]")
    console.print()

    # Reviewers — single line (hide optional no-votes)
    reviews = pr.get("reviews", [])
    if reviews:
        visible = [
            r
            for r in reviews
            if r.get("isRequired") or (r.get("vote") and r["vote"] != "NoVote")
        ]

        def _fmt(r: dict) -> str:
            vote = r.get("vote", "NoVote") or "NoVote"
            if vote in VOTE_EMOJI:
                symbol = VOTE_EMOJI[vote]
            else:
                symbol = "!" if r.get("isRequired") else "·"
            return f"{symbol} {esc(r['name'])}"

        if visible:
            console.print(f"[bold]Reviewers:[/] {'  '.join(_fmt(r) for r in visible)}")
        else:
            console.print("[bold]Reviewers:[/] [dim]none[/]")
    else:
        console.print("[bold]Reviewers:[/] [dim]none[/]")

    # Checks — Option B: verdict + category counts + failing names
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
            c for c in checks if not c.get("isBlocking") and c["status"] != "approved"
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
        console.print("   ".join(check_parts))
    else:
        console.print(f"[bold]Checks:[/] {format_checks(pr)}")

    # Comments
    active = pr.get("commentsActive", 0) or 0
    total = pr.get("commentsTotal", 0) or 0
    last_comment = format_time_ago(pr.get("lastCommentDate"))
    console.print(
        f"[bold]Comments:[/] {active} active / {total} total (last {last_comment})"
    )

    # Timestamps
    console.print()
    console.print(
        f"[dim]Created {format_time_ago(pr.get('creationDate'))} · "
        f"Updated {format_time_ago(pr.get('lastUpdated'))} · "
        f"Fetched {format_time_ago(pr.get('lastLoaded'))}[/]"
    )

    # Work items — inline with URL
    work_items = pr.get("workItems", [])
    if work_items:
        console.print()
        for idx, wi in enumerate(work_items):
            wi_text = (
                f"[dim]{esc(wi.get('type', ''))}[/] "
                f"#{wi['id']} {esc(wi.get('title', ''))}"
            )
            if wi.get("url"):
                wi_text += f"  [dim]{wi['url']}[/]"
            if idx == 0:
                console.print(f"[bold]Work Items:[/] {wi_text}")
            else:
                console.print(f"            {wi_text}")


def _pr_url_table(prs: list[dict], title: str | None = None) -> Table:
    """Build a compact table: St, Title, URL — sorted by project then activity."""
    prs = sort_prs(prs)
    table = Table(title=title, show_lines=False, pad_edge=False)
    table.add_column("St", width=4)
    table.add_column("Title", max_width=50)
    table.add_column("URL", no_wrap=True)
    for pr in prs:
        title_str = truncate(pr.get("title", ""), 50)
        table.add_row(
            format_status(pr.get("status", ""), pr),
            title_str,
            pr.get("url", ""),
        )
    return table


# ── Auto-sync helpers ─────────────────────────────────────────────────────


def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO timestamp, return None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


async def _auto_sync_if_stale(store: PrDataStore) -> None:
    """Run sync or refresh before list if data is stale per config intervals."""
    cfg = get_sync_config()
    now = datetime.now(UTC)

    last_sync = _parse_iso(store.db.get_meta("last_sync_time"))
    last_refresh = _parse_iso(store.db.get_meta("last_refresh_time"))
    latest = max(filter(None, [last_sync, last_refresh]), default=None)

    # Full sync if stale — checked FIRST to prevent refresh+sync double-run.
    # If sync triggers, it also updates last_refresh_time and returns early.
    if cfg["auto_sync_enabled"] and last_sync is not None:
        age_min = (now - last_sync).total_seconds() / 60
        if age_min >= cfg["auto_sync_interval"]:
            console.print(
                f"[dim]Auto-syncing (last sync {int(age_min)}m ago)...[/]",
                highlight=False,
            )
            await store.sync()
            ts = datetime.now(UTC).isoformat()
            store.db.set_meta("last_sync_time", ts)
            store.db.set_meta("last_refresh_time", ts)
            return

    # Refresh if stale (use latest of sync/refresh timestamps)
    if cfg["auto_refresh_enabled"] and latest is not None:
        age_min = (now - latest).total_seconds() / 60
        if age_min >= cfg["auto_refresh_interval"]:
            console.print(
                f"[dim]Auto-refreshing (last update {int(age_min)}m ago)...[/]",
                highlight=False,
            )
            await store.refresh_all()
            store.db.set_meta("last_refresh_time", datetime.now(UTC).isoformat())
            return


# ── Commands ──────────────────────────────────────────────────────────────


async def cmd_sync(store: PrDataStore, as_json: bool) -> None:
    console.print("[dim]Discovering sources and syncing PRs...[/]", highlight=False)
    prs = await store.sync()
    now = datetime.now(UTC).isoformat()
    store.db.set_meta("last_sync_time", now)
    store.db.set_meta("last_refresh_time", now)
    if as_json:
        print(json.dumps(prs, indent=2, ensure_ascii=False))
    else:
        sources = store.get_active_sources()
        console.print(
            _pr_table(
                prs,
                title=f"Synced {len(prs)} PR(s) from {len(sources)} source(s)",
            )
        )


async def cmd_list(
    store: PrDataStore, as_json: bool, urls: bool = False, role: str = ""
) -> None:
    prs = store.load_prs()
    if role:
        prs = [p for p in prs if p.get("role", "author") == role]
    label = {"author": "authored", "reviewer": "review"}.get(role, "tracked")
    if not prs:
        console.print(f"[yellow]No {label} PRs. Run 'sync' first.[/]")
        return
    if as_json:
        print(json.dumps(sort_prs(prs), indent=2, ensure_ascii=False))
    elif urls:
        console.print(_pr_url_table(prs, title=f"{len(prs)} {label} PR(s)"))
    else:
        console.print(_pr_table(prs, title=f"{len(prs)} {label} PR(s)", role=role))


async def cmd_add(store: PrDataStore, url: str, as_json: bool) -> None:
    console.print(f"[dim]Adding PR from {url}...[/]", highlight=False)
    try:
        entry, existed = await store.add_pr_by_url(url)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]", highlight=False)
        sys.exit(1)
    verb = "Updated" if existed else "Added"
    console.print(f"[green]{verb} PR #{entry.get('id', '?')}[/]")
    if as_json:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
    else:
        _show_pr_detail(entry)


async def cmd_show(store: PrDataStore, pr_id: int, as_json: bool) -> None:
    prs = store.load_prs()
    matches = [p for p in prs if p["id"] == pr_id]
    if not matches:
        console.print(
            f"[red]PR #{pr_id} not found. Run 'sync' first.[/]", highlight=False
        )
        sys.exit(1)
    if len(matches) > 1:
        console.print(
            f"[red]PR #{pr_id} exists in {len(matches)} source(s).[/]",
            highlight=False,
        )
        console.print(_pr_table(matches, title=f"Matching PRs for #{pr_id}"))
        sys.exit(1)
    pr = matches[0]
    if as_json:
        print(json.dumps(pr, indent=2, ensure_ascii=False))
    else:
        _show_pr_detail(pr)


async def cmd_refresh(store: PrDataStore, pr_id: int, as_json: bool) -> None:
    # Find matching PR to get source for disambiguation
    prs = store.load_prs()
    matches = [p for p in prs if p["id"] == pr_id]
    if not matches:
        console.print(f"[red]PR #{pr_id} not found.[/]", highlight=False)
        sys.exit(1)
    if len(matches) > 1:
        console.print(
            f"[red]PR #{pr_id} exists in {len(matches)} source(s). Cannot refresh ambiguously.[/]",
            highlight=False,
        )
        console.print(_pr_table(matches, title=f"Matching PRs for #{pr_id}"))
        sys.exit(1)
    source = matches[0].get("source", "")
    console.print(f"[dim]Refreshing PR #{pr_id}...[/]", highlight=False)
    entry = await store.refresh(pr_id, source=source)
    if entry is None:
        console.print(f"[red]PR #{pr_id} not found.[/]", highlight=False)
        sys.exit(1)
    if as_json:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
    else:
        _show_pr_detail(entry)


async def cmd_refresh_all(store: PrDataStore, as_json: bool) -> None:
    console.print("[dim]Refreshing all tracked PRs...[/]", highlight=False)
    prs = await store.refresh_all()
    store.db.set_meta("last_refresh_time", datetime.now(UTC).isoformat())
    if as_json:
        print(json.dumps(prs, indent=2, ensure_ascii=False))
    else:
        console.print(_pr_table(prs, title=f"Refreshed {len(prs)} PR(s)"))


async def cmd_remove(store: PrDataStore, pr_id: int) -> None:
    # Find matching PR to get source for disambiguation
    prs = store.load_prs()
    matches = [p for p in prs if p["id"] == pr_id]
    if len(matches) > 1:
        console.print(
            f"[red]PR #{pr_id} exists in {len(matches)} source(s). Cannot remove ambiguously.[/]",
            highlight=False,
        )
        console.print(_pr_table(matches, title=f"Matching PRs for #{pr_id}"))
        sys.exit(1)
    source = matches[0].get("source", "") if matches else ""
    if store.remove(pr_id, source=source):
        console.print(f"Removed PR #{pr_id}.")
    else:
        console.print(f"[red]PR #{pr_id} not found.[/]", highlight=False)
        sys.exit(1)


async def cmd_clean(store: PrDataStore) -> None:
    removed = store.clean()
    console.print(f"Cleaned {removed} non-active PR(s).")


async def cmd_exclude(store: PrDataStore, source: str, repo: str) -> None:
    if store.exclude_repo(source, repo):
        console.print(f"Excluded [bold]{source} :: {repo}[/] from sync.")
    else:
        console.print(f"[dim]{source} :: {repo} already excluded.[/]", highlight=False)


async def cmd_include(store: PrDataStore, source: str, repo: str) -> None:
    if store.include_repo(source, repo):
        console.print(f"Included [bold]{source} :: {repo}[/] in sync.")
    else:
        console.print(f"[dim]{source} :: {repo} already included.[/]", highlight=False)


async def cmd_config(action: str | None = None) -> None:
    from .config import CONFIG_DIR, CONFIG_FILE, get_full_defaults, load_config

    match action:
        case "show":
            # Print effective merged config
            from .config import get_extensions, get_keybindings

            effective = {
                "keybindings": get_keybindings(),
                "display": get_display_config(),
                "sync": get_sync_config(),
                "extensions": get_extensions(),
                "theme": load_config().get("theme", "textual-dark"),
            }
            console.print_json(json.dumps(effective, indent=2, default=str))
        case "defaults":
            # Print default config as reference
            console.print_json(json.dumps(get_full_defaults(), indent=2, default=str))
        case "reset":
            if CONFIG_FILE.exists():
                CONFIG_FILE.unlink()
                console.print(f"[green]Deleted:[/] {CONFIG_FILE}")
                console.print("[dim]All settings reset to defaults.[/]")
            else:
                console.print("[dim]No config file found — already using defaults.[/]")
        case "edit":
            import os
            import subprocess

            if not CONFIG_FILE.exists():
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                CONFIG_FILE.write_text("{}\n", encoding="utf-8")
                console.print(f"[green]Created:[/] {CONFIG_FILE}")
            editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
            if editor:
                subprocess.run([editor, str(CONFIG_FILE)])
            else:
                # Windows: use default file association
                os.startfile(str(CONFIG_FILE))
        case _:
            # Default: show location (backward compatible)
            if not CONFIG_FILE.exists():
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                CONFIG_FILE.write_text("{}\n", encoding="utf-8")
                console.print(f"[green]Created:[/] {CONFIG_FILE}")
            else:
                console.print(f"[bold]Config file:[/] {CONFIG_FILE}")
            console.print(f"[bold]Config dir:[/]  {CONFIG_DIR}")
            console.print(
                "\n[dim]Subcommands: config show · config defaults · "
                "config edit · config reset[/]"
            )


# ── Entry point ───────────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> None:
    store = PrDataStore()
    as_json = getattr(args, "json", False)

    try:
        match args.command:
            case "sync":
                pr_id = getattr(args, "pr_id", None)
                refresh = getattr(args, "refresh", False)
                if pr_id:
                    await cmd_refresh(store, pr_id, as_json)
                elif refresh:
                    await cmd_refresh_all(store, as_json)
                else:
                    await cmd_sync(store, as_json)
            case "list":
                if getattr(args, "sync", False):
                    console.print("[dim]Refreshing tracked PRs...[/]", highlight=False)
                    await store.refresh_all()
                    store.db.set_meta("last_refresh_time", datetime.now(UTC).isoformat())
                else:
                    await _auto_sync_if_stale(store)
                role = ""
                if getattr(args, "mine", False):
                    role = "author"
                elif getattr(args, "reviews", False):
                    role = "reviewer"
                await cmd_list(
                    store, as_json, urls=getattr(args, "urls", False), role=role
                )
            case "show":
                await cmd_show(store, args.pr_id, as_json)
            case "remove":
                await cmd_remove(store, args.pr_id)
            case "clean":
                await cmd_clean(store)
            case "add":
                await cmd_add(store, args.url, as_json)
            case "exclude":
                await cmd_exclude(store, args.source, args.repo)
            case "include":
                await cmd_include(store, args.source, args.repo)
            case "config":
                await cmd_config(getattr(args, "config_action", None))
            case "sources":
                action = getattr(args, "action", None)
                if action == "include":
                    if not args.source:
                        console.print("[red]Usage: sources include <source>[/]")
                        sys.exit(1)
                    await cmd_sources_include(store, args.source)
                elif action == "exclude":
                    if not args.source:
                        console.print("[red]Usage: sources exclude <source>[/]")
                        sys.exit(1)
                    await cmd_sources_exclude(store, args.source)
                else:
                    await cmd_sources(store)
            case "repos":
                action = getattr(args, "action", None)
                if action == "include":
                    if not args.source or not args.repo:
                        console.print("[red]Usage: repos include <source> <repo>[/]")
                        sys.exit(1)
                    await cmd_repos_include(store, args.source, args.repo)
                elif action == "exclude":
                    if not args.source or not args.repo:
                        console.print("[red]Usage: repos exclude <source> <repo>[/]")
                        sys.exit(1)
                    await cmd_repos_exclude(store, args.source, args.repo)
                else:
                    await cmd_repos(store)
    except AdoAuthError as exc:
        log.error("Auth error: %s", exc)
        console.print(f"[red]Authentication failed:[/] {exc}")
        sys.exit(1)
    except AdoApiError as exc:
        log.error("API error: %s", exc)
        console.print(f"[red]API error:[/] {exc}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pr-dashboard", description="Azure DevOps PR Dashboard CLI"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    sub = parser.add_subparsers(dest="command")

    sync_p = sub.add_parser(
        "sync",
        help="Sync PRs (discover+fetch, or refresh tracked)",
        description="Discover sources and fetch PRs. Use --refresh to re-fetch tracked PRs without discovery, or pass a PR ID to refresh a single PR.",
    )
    sync_p.add_argument("pr_id", nargs="?", type=int, metavar="ID", help="Refresh a specific tracked PR by ID")
    sync_p.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh all tracked PRs without source discovery",
    )
    list_p = sub.add_parser("list", help="List tracked PRs (local data)")
    list_p.add_argument("--urls", action="store_true", help="Compact view with URLs")
    list_p.add_argument("--sync", action="store_true", help="Refresh tracked PRs before listing")
    list_grp = list_p.add_mutually_exclusive_group()
    list_grp.add_argument("--mine", action="store_true", help="Show only authored PRs")
    list_grp.add_argument(
        "--reviews", action="store_true", help="Show only PRs you're reviewing"
    )

    show_p = sub.add_parser("show", help="Show detailed info for a PR")
    show_p.add_argument("pr_id", type=int, help="PR ID to show")

    remove_p = sub.add_parser("remove", help="Remove a PR from tracking")
    remove_p.add_argument("pr_id", type=int, help="PR ID to remove")

    sub.add_parser("clean", help="Remove completed/abandoned PRs")

    add_p = sub.add_parser("add", help="Add a PR by URL (ADO or GitHub)")
    add_p.add_argument("url", help="PR URL to add")

    exclude_p = sub.add_parser(
        "exclude", help="Exclude a repo from sync (source + repo)"
    )
    exclude_p.add_argument("source", help="Source (e.g., ado/msazure, github)")
    exclude_p.add_argument("repo", help="Repo name to exclude")

    include_p = sub.add_parser("include", help="Include a repo in sync (source + repo)")
    include_p.add_argument("source", help="Source (e.g., ado/msazure, github)")
    include_p.add_argument("repo", help="Repo name to include")

    config_p = sub.add_parser("config", help="Show/manage config (show, defaults, edit, reset)")
    config_p.add_argument(
        "config_action",
        nargs="?",
        choices=["show", "defaults", "edit", "reset"],
        help="Action: show (effective), defaults (reference), edit (open editor), reset (delete config)",
    )

    sources_p = sub.add_parser("sources", help="List/manage sources")
    sources_p.add_argument(
        "action",
        nargs="?",
        choices=["include", "exclude"],
        help="Action: include or exclude",
    )
    sources_p.add_argument(
        "source", nargs="?", help="Source (e.g., ado/msazure, github)"
    )

    repos_p = sub.add_parser("repos", help="List/manage repos")
    repos_p.add_argument(
        "action",
        nargs="?",
        choices=["include", "exclude"],
        help="Action: include or exclude",
    )
    repos_p.add_argument("source", nargs="?", help="Source (e.g., ado/msazure, github)")
    repos_p.add_argument("repo", nargs="?", help="Repo name")

    args = parser.parse_args()

    if args.command is None:
        from .app import PRDashboard

        PRDashboard().run()
        return

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
