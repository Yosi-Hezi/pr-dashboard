"""CLI commands for managing PR Dashboard sources (register, unregister, list)."""

from __future__ import annotations

import sys

from .ado_client import AdoApiError, AdoAuthError, AdoClient
from .data import PrDataStore
from .gh_client import GhClient
from rich.console import Console

console = Console()


async def cmd_sources(store: PrDataStore, show_all: bool = False) -> None:
    if show_all:
        # Discover all ADO orgs + check GitHub
        console.print("[dim]Discovering all ADO organizations...[/]", highlight=False)
        try:
            async with AdoClient() as client:
                orgs = await client.discover_orgs()
        except (AdoApiError, AdoAuthError) as exc:
            console.print(f"[red]ADO discovery failed:[/] {exc}")
            orgs = []

        gh = GhClient()
        gh_user = await gh.check_auth()

        if orgs:
            console.print("[bold]ADO Organizations:[/]")
            for org in sorted(orgs):
                console.print(f"  ado/{org}")
        else:
            console.print("[dim]No ADO organizations found.[/]")
        if gh_user:
            console.print(f"  github ({gh_user})")
        else:
            console.print("  [dim]GitHub: not authenticated (run 'gh auth login')[/]")
        return

    sources = store.get_sources()
    if not sources:
        console.print("[yellow]No sources registered.[/]")
        console.print(
            "[dim]Run 'register all' to auto-discover, or 'register ado <org>' to add manually.[/]"
        )
    else:
        console.print("[bold]Registered sources:[/]")
        for s in sources:
            console.print(f"  {s}")
    console.print(f"\n[dim]Config: {store.data_file}[/]")


async def cmd_register(
    store: PrDataStore, source_type: str, org: str | None = None
) -> None:
    if source_type == "all":
        # Auto-discover all ADO orgs + GitHub
        console.print("[dim]Discovering all sources...[/]", highlight=False)
        registered = 0

        try:
            async with AdoClient() as client:
                orgs = await client.discover_orgs()
            for o in orgs:
                if store.add_source(f"ado/{o}"):
                    console.print(f"  + Registered ado/{o}")
                    registered += 1
                else:
                    console.print(f"  [dim]ado/{o} (already registered)[/]")
        except (AdoApiError, AdoAuthError) as exc:
            console.print(f"[red]ADO discovery failed:[/] {exc}")

        gh = GhClient()
        gh_user = await gh.check_auth()
        if gh_user:
            if store.add_source("github"):
                console.print(f"  + Registered github ({gh_user})")
                registered += 1
            else:
                console.print("  [dim]github (already registered)[/]")
        else:
            console.print("  [dim]GitHub: not authenticated — skipping[/]")

        console.print(f"\nRegistered {registered} new source(s).")

    elif source_type == "ado":
        if not org:
            console.print("[red]Usage: register ado <org>[/]")
            sys.exit(1)
        source = f"ado/{org}"
        if store.add_source(source):
            console.print(f"+ Registered {source}")
        else:
            console.print(f"[dim]{source} already registered.[/]")

    elif source_type == "github":
        gh = GhClient()
        gh_user = await gh.check_auth()
        if not gh_user:
            console.print(
                "[red]GitHub not authenticated. Run 'gh auth login' first.[/]"
            )
            sys.exit(1)
        if store.add_source("github"):
            console.print(f"+ Registered github ({gh_user})")
        else:
            console.print("[dim]github already registered.[/]")
    else:
        console.print(
            f"[red]Unknown source type: {source_type}. Use 'ado' or 'github'.[/]"
        )
        sys.exit(1)


async def cmd_unregister(store: PrDataStore, source: str) -> None:
    if store.remove_source(source):
        console.print(f"Unregistered {source} (and removed its PRs).")
    else:
        console.print(f"[red]Source '{source}' not found.[/]", highlight=False)
        sys.exit(1)
