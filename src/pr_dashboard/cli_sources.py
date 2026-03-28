"""CLI commands for managing PR Dashboard sources and repos."""

from __future__ import annotations


from .data import PrDataStore
from .formatting import source_label
from rich.console import Console

console = Console()


async def cmd_sources(store: PrDataStore) -> None:
    """List all sources with their active/excluded status."""
    items = store.get_sources_for_manage()
    if not items:
        console.print("[yellow]No sources known. Run 'sync' to discover.[/]")
        return
    console.print("[bold]Sources:[/]")
    for src, is_active in items:
        icon = "✓" if is_active else "✗"
        label = source_label(src)
        suffix = "" if is_active else " (excluded)"
        console.print(f"  {icon} {label}{suffix}")
    console.print(f"\n[dim]Config: {store.data_file}[/]")


async def cmd_sources_include(store: PrDataStore, source: str) -> None:
    """Include a source."""
    if store.include_source(source):
        console.print(f"Included source [bold]{source}[/].")
    else:
        console.print(f"[dim]{source} already included.[/]")


async def cmd_sources_exclude(store: PrDataStore, source: str) -> None:
    """Exclude a source."""
    if store.exclude_source(source):
        console.print(f"Excluded source [bold]{source}[/].")
    else:
        console.print(f"[dim]{source} already excluded.[/]")


async def cmd_repos(store: PrDataStore) -> None:
    """List all repos with their active/excluded status."""
    items = store.get_repos_for_manage()
    if not items:
        console.print("[yellow]No repos known. Run 'sync' to discover.[/]")
        return
    console.print("[bold]Repos:[/]")
    for repo_entry, is_active in items:
        icon = "✓" if is_active else "✗"
        label = source_label(repo_entry["source"])
        suffix = "" if is_active else " (excluded)"
        console.print(f"  {icon} {label} :: {repo_entry['repo']}{suffix}")


async def cmd_repos_include(store: PrDataStore, source: str, repo: str) -> None:
    """Include a repo."""
    if store.include_repo(source, repo):
        console.print(f"Included repo [bold]{source} :: {repo}[/].")
    else:
        console.print(f"[dim]{source} :: {repo} already included.[/]")


async def cmd_repos_exclude(store: PrDataStore, source: str, repo: str) -> None:
    """Exclude a repo."""
    if store.exclude_repo(source, repo):
        console.print(f"Excluded repo [bold]{source} :: {repo}[/].")
    else:
        console.print(f"[dim]{source} :: {repo} already excluded.[/]")
