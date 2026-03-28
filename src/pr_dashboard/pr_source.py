"""Protocol defining the interface for PR source clients."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PrSourceClient(Protocol):
    """Common interface for PR source clients (ADO, GitHub, etc.)."""

    async def check_auth(self) -> str | None:
        """Return authenticated username, or None if not authenticated."""
        ...

    async def list_my_prs(self) -> list[dict]:
        """List open PRs authored by the current user."""
        ...

    async def enrich_pr(self, raw_pr: dict) -> dict:
        """Convert a raw PR dict to a normalized dashboard entry."""
        ...

    async def close(self) -> None:
        """Release any held resources."""
        ...
