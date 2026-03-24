"""CLI entry point for PR Dashboard — headless mode with rich output."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .ado_client import AdoApiError, AdoAuthError
from .cli_sources import cmd_register, cmd_sources, cmd_unregister
from .data import PrDataStore
from .formatting import (
    VOTE_EMOJI,
    esc,
    format_checks,
    format_comments,
    format_my_vote,
    format_reviews,
    format_status,
    format_status_label,
    format_source,
    format_time_ago,
    shorten_repo,
    sort_prs,
    truncate,
)
from .logger import get_logger
from rich.console import Console
from rich.table import Table

log = get_logger()
console = Console()


def _pr_table(
    prs: list[dict], title: str | None = None, role: str = ""
) -> Table:
    """Build a rich Table from a list of PR dicts."""
    prs = sort_prs(prs)
    is_reviews = role == "reviewer"
    table = Table(title=title, show_lines=False, pad_edge=False)
    table.add_column("St", width=4)
    table.add_column("Src", max_width=12)
    table.add_column("ID", style="dim")
    table.add_column("Title", max_width=50)
    table.add_column("Author", max_width=18)
    table.add_column("Repo", max_width=20)
    if is_reviews:
        table.add_column("Me", width=4)
    table.add_column("Votes")
    table.add_column("Checks")
    table.add_column("Cmts")
    table.add_column("Updated")
    for pr in prs:
        title_str = truncate(pr.get("title", ""), 50)
        author = truncate(pr.get("author", ""), 14)
        row: list[str] = [
            format_status(pr.get("status", ""), pr),
            format_source(pr.get("source", "")),
            str(pr.get("id", "")),
            title_str,
            author,
            shorten_repo(pr.get("repoName", "")),
        ]
        if is_reviews:
            row.append(
                format_my_vote(
                    pr.get("myVote", ""),
                    pr.get("isRequiredReviewer", False),
                )
            )
            row.append(
                format_reviews(
                    pr.get("reviews", []),
                    exclude_vote=pr.get("myVote", ""),
                )
            )
        else:
            row.append(format_reviews(pr.get("reviews", [])))
        row.extend(
            [
                format_checks(pr),
                format_comments(pr),
                format_time_ago(pr.get("lastUpdated")),
            ]
        )
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
            r for r in reviews
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
            console.print(
                f"[bold]Reviewers:[/] {'  '.join(_fmt(r) for r in visible)}"
            )
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
        req_failed = [c for c in checks if c.get("isBlocking") and c["status"] != "approved"]
        opt_failed = [c for c in checks if not c.get("isBlocking") and c["status"] != "approved"]
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


# ── Commands ──────────────────────────────────────────────────────────────


async def cmd_sync(store: PrDataStore, as_json: bool) -> None:
    sources = store.get_sources()
    if not sources:
        console.print(
            "[yellow]No sources registered. Run 'register all' or 'register ado <org>'.[/]"
        )
        return
    console.print(
        f"[dim]Syncing PRs from {len(sources)} source(s)...[/]", highlight=False
    )
    prs = await store.sync()
    if as_json:
        print(json.dumps(prs, indent=2, ensure_ascii=False))
    else:
        console.print(_pr_table(prs, title=f"Synced {len(prs)} PR(s)"))


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
        entry = await store.add_pr_by_url(url)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]", highlight=False)
        sys.exit(1)
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


async def cmd_exclude(store: PrDataStore, repo: str) -> None:
    if store.exclude_repo(repo):
        console.print(f"Excluded [bold]{repo}[/] from CR sync.")
    else:
        console.print(f"[yellow]{repo} is already excluded.[/]", highlight=False)


async def cmd_include(store: PrDataStore, repo: str) -> None:
    if store.include_repo(repo):
        console.print(f"Included [bold]{repo}[/] back in CR sync.")
    else:
        console.print(f"[red]{repo} is not excluded.[/]", highlight=False)
        sys.exit(1)


async def cmd_excluded(store: PrDataStore) -> None:
    excluded = store.get_excluded_repos()
    if not excluded:
        console.print("[dim]No repos excluded. All repos are included in CR sync.[/]")
        return
    console.print(f"[bold]{len(excluded)} excluded repo(s):[/]")
    for repo in sorted(excluded):
        console.print(f"  • {repo}")


async def cmd_config() -> None:
    from .config import CONFIG_DIR, CONFIG_FILE

    if not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text("{}\n", encoding="utf-8")
        console.print(f"[green]Created:[/] {CONFIG_FILE}")
    else:
        console.print(f"[bold]Config file:[/] {CONFIG_FILE}")
    console.print(f"[bold]Config dir:[/]  {CONFIG_DIR}")


# ── Entry point ───────────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> None:
    store = PrDataStore()
    as_json = getattr(args, "json", False)

    try:
        match args.command:
            case "sync":
                await cmd_sync(store, as_json)
            case "list":
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
            case "refresh":
                if args.all:
                    await cmd_refresh_all(store, as_json)
                else:
                    if not args.pr_id:
                        console.print("[red]Error: provide a PR ID or use --all[/]")
                        sys.exit(1)
                    await cmd_refresh(store, args.pr_id, as_json)
            case "remove":
                await cmd_remove(store, args.pr_id)
            case "clean":
                await cmd_clean(store)
            case "add":
                await cmd_add(store, args.url, as_json)
            case "exclude":
                await cmd_exclude(store, args.repo)
            case "include":
                await cmd_include(store, args.repo)
            case "excluded":
                await cmd_excluded(store)
            case "config":
                await cmd_config()
            case "sources":
                await cmd_sources(store, show_all=getattr(args, "all", False))
            case "register":
                await cmd_register(
                    store, args.source_type, org=getattr(args, "org", None)
                )
            case "unregister":
                await cmd_unregister(store, args.source)
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
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="Fetch PRs from all registered sources")
    list_p = sub.add_parser("list", help="List tracked PRs (local data)")
    list_p.add_argument("--urls", action="store_true", help="Compact view with URLs")
    list_grp = list_p.add_mutually_exclusive_group()
    list_grp.add_argument("--mine", action="store_true", help="Show only authored PRs")
    list_grp.add_argument(
        "--reviews", action="store_true", help="Show only PRs you're reviewing"
    )

    show_p = sub.add_parser("show", help="Show detailed info for a PR")
    show_p.add_argument("pr_id", type=int, help="PR ID to show")

    refresh_p = sub.add_parser("refresh", help="Refresh a PR or all PRs")
    refresh_p.add_argument("pr_id", nargs="?", type=int, help="PR ID to refresh")
    refresh_p.add_argument("--all", action="store_true", help="Refresh all tracked PRs")

    remove_p = sub.add_parser("remove", help="Remove a PR from tracking")
    remove_p.add_argument("pr_id", type=int, help="PR ID to remove")

    sub.add_parser("clean", help="Remove completed/abandoned PRs")

    add_p = sub.add_parser("add", help="Add a PR by URL (ADO or GitHub)")
    add_p.add_argument("url", help="PR URL to add")

    exclude_p = sub.add_parser(
        "exclude", help="Exclude a repo from CR sync (reviews only)"
    )
    exclude_p.add_argument("repo", help="Repo name to exclude")

    include_p = sub.add_parser("include", help="Re-include an excluded repo in CR sync")
    include_p.add_argument("repo", help="Repo name to include")

    sub.add_parser("excluded", help="List excluded repos")
    sub.add_parser("config", help="Show config file location")

    sources_p = sub.add_parser("sources", help="List registered sources")
    sources_p.add_argument(
        "all", nargs="?", default=None, help="Show all discoverable sources"
    )

    register_p = sub.add_parser(
        "register", help="Register a source (ado <org>, github, or all)"
    )
    register_p.add_argument(
        "source_type", choices=["ado", "github", "all"], help="Source type"
    )
    register_p.add_argument("org", nargs="?", help="ADO org name (for 'ado' type)")

    unregister_p = sub.add_parser("unregister", help="Unregister a source")
    unregister_p.add_argument(
        "source", help="Source to remove (e.g., ado/msazure, github)"
    )

    args = parser.parse_args()

    # Handle `sources all` positional argument
    if args.command == "sources" and args.all == "all":
        args.all = True
    elif args.command == "sources":
        args.all = False

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
