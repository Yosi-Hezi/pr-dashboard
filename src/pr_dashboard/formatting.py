"""Shared formatting helpers for PR Dashboard (TUI + CLI)."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.markup import escape
from rich.style import Style


def pr_key(pr: dict) -> str:
    """Composite key for a PR: 'source:id'. Unique across sources."""
    return f"{pr.get('source', '')}:{pr.get('id', 0)}"


def truncate(text: str, max_len: int, suffix: str = "..") -> str:
    """Truncate text with suffix if it exceeds max_len."""
    if len(text) <= max_len:
        return text
    if max_len <= len(suffix):
        return suffix[:max_len]
    return text[: max_len - len(suffix)] + suffix


def esc(text: str) -> str:
    """Escape Rich markup in API-sourced strings."""
    return escape(str(text))


def format_time_ago(iso_date: str | None) -> str:
    if not iso_date:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        diff = datetime.now(UTC) - dt
        minutes = diff.total_seconds() / 60
        if minutes < 1:
            return "just now"
        if minutes < 60:
            return f"{int(minutes)}m ago"
        hours = minutes / 60
        if hours < 24:
            return f"{int(hours)}h ago"
        days = hours / 24
        if days < 7:
            return f"{int(days)}d ago"
        return f"{int(days / 7)}w ago"
    except Exception:
        return "?"


VOTE_EMOJI = {
    "Approved": "✓",
    "ApprovedWithSuggestions": "✓",
    "WaitingForAuthor": "↻",
    "Rejected": "✗",
}


def format_my_vote(my_vote: str, is_required: bool) -> str:
    """Format user's own vote for the Me column (Reviews view only)."""
    if my_vote and my_vote in VOTE_EMOJI:
        return VOTE_EMOJI[my_vote]
    return "!" if is_required else ""


def format_reviews(reviews: list, exclude_vote: str = "") -> str:
    """Format reviewer votes as individual symbols.

    Required reviewer pending = !, optional pending = hidden.
    exclude_vote: skip one instance of this vote type (Me column dedup).
    """
    if not reviews:
        return ""

    parts = []
    vote_skipped = False
    for r in reviews:
        vote = r.get("vote", "NoVote") or "NoVote"
        req = r.get("isRequired", False)

        # Skip one entry matching user's vote (shown in Me column)
        if exclude_vote and not vote_skipped and vote == exclude_vote:
            vote_skipped = True
            continue

        if vote not in VOTE_EMOJI:
            if req:
                parts.append("!")
            # Optional no-vote → hidden
        else:
            parts.append(VOTE_EMOJI[vote])

    return "  ".join(parts) if parts else ""


def format_checks(pr: dict) -> str:
    rt = pr.get("requiredTotal")
    if rt is not None:
        rp = pr.get("requiredPass", 0)
        ot = pr.get("optionalTotal", 0)
        op = pr.get("optionalPass", 0)
        if rp < rt:
            return f"✗ {rp}/{rt}"
        if ot > 0 and op < ot:
            return f"~ {op}/{ot}"
        return "✓"
    p, t = pr.get("checksPass"), pr.get("checksTotal")
    if t is None:
        return "?"
    icon = "✓" if p == t else "✗"
    return f"{icon} {p}/{t}"


def format_comments(pr: dict) -> str:
    a, t = pr.get("commentsActive"), pr.get("commentsTotal")
    if t is None:
        return "?"
    if a == 0:
        return f"✓ {t}"
    return f"💬 {a}/{t}"


def shorten_repo(name: str) -> str:
    for prefix in ("AzNet-ApplicationSecurity-", "AzNet-"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _derive_status(status: str, pr: dict | None = None) -> tuple[str, str]:
    """Derive (symbol, label) for a PR status.

    Priority for active PRs: WaitingForAuthor > Approved > Draft > AutoComplete > Active.
    Merge conflicts: appended as ⚠ suffix via format_status().
    """
    if pr and status == "active":
        reviews = pr.get("reviews", [])
        # Any reviewer waiting for author?
        if any(r.get("vote") == "WaitingForAuthor" for r in reviews):
            return "↻", "Waiting for Author"
        # All required reviewers approved? (ADO only — GitHub has no isRequired)
        required = [r for r in reviews if r.get("isRequired")]
        if required and all(
            r.get("vote") in ("Approved", "ApprovedWithSuggestions") for r in required
        ):
            return "✓", "Approved"
        # GitHub: all reviewers approved (no isRequired concept)
        if (
            not required
            and reviews
            and all(
                r.get("vote") in ("Approved", "ApprovedWithSuggestions")
                for r in reviews
            )
        ):
            return "✓", "Approved"
        if pr.get("isDraft"):
            return "✎", "Draft"
        if pr.get("autoCompleteSetBy"):
            return "»", "Auto-complete"
        return "○", "Active"
    if status == "completed":
        return "✓✓", "Completed"
    if status == "abandoned":
        return "∅", "Abandoned"
    if status == "active":
        return "○", "Active"
    # Fallback for non-active with isDraft / autoComplete (shouldn't happen)
    if pr and pr.get("isDraft"):
        return "✎", "Draft"
    if pr and pr.get("autoCompleteSetBy"):
        return "»", "Auto-complete"
    return "?", "Unknown"


def _has_merge_conflicts(pr: dict | None) -> bool:
    """Check if a PR has merge conflicts based on mergeStatus field."""
    if not pr:
        return False
    ms = pr.get("mergeStatus", "")
    # ADO: "conflicts"; GitHub: mapped from "dirty"/"behind"
    return ms in ("conflicts", "dirty", "behind")


def format_status(status: str, pr: dict | None = None) -> str:
    """Format PR status as symbol indicator, with ⚠ suffix for merge conflicts."""
    symbol, _ = _derive_status(status, pr)
    if _has_merge_conflicts(pr):
        return f"{symbol} ⚠"
    return symbol


def format_status_label(status: str, pr: dict | None = None) -> str:
    """Format PR status as 'symbol Label' for detail panels."""
    symbol, label = _derive_status(status, pr)
    if _has_merge_conflicts(pr):
        return f"{symbol} {label} · ⚠ Merge Conflicts"
    return f"{symbol} {label}"


def pr_row_style(pr: dict, rules: list[dict] | None = None) -> Style | None:
    """Return row background style based on configurable rules.

    Each rule: optional 'status' (derived label), optional 'mergeStatus', required 'color'.
    Missing fields = wildcard. First matching rule wins.
    """
    if rules is None:
        from .config import DEFAULT_DISPLAY

        rules = DEFAULT_DISPLAY["row_colors"]

    status = pr.get("status", "")
    _, label = _derive_status(status, pr)
    merge_status = pr.get("mergeStatus", "")

    for rule in rules:
        rule_status = rule.get("status", "")
        rule_merge = rule.get("mergeStatus", "")
        if rule_status and rule_status != label:
            continue
        if rule_merge and rule_merge != merge_status:
            continue
        color = rule.get("color", "")
        if color:
            return Style(bgcolor=color)
    return None


def format_source(source: str) -> str:
    """Return the source identifier, capped at 10 chars."""
    return truncate(source, 10)


def source_label(source: str) -> str:
    """Human-readable label: 'ado/msazure' → 'ADO msazure', 'github' → 'GitHub'."""
    if source == "github":
        return "GitHub"
    if source.startswith("ado/"):
        return f"ADO {source.removeprefix('ado/')}"
    return source


def format_pin(pr: dict) -> str:
    """Return ★ for pinned PRs, empty string otherwise."""
    return "★" if pr.get("pinned") else ""


def get_cell_value(
    col_id: str, pr: dict, *, is_reviews: bool = False, display: dict | None = None
) -> str:
    """Get formatted cell value for a column ID."""
    if display is None:
        from .config import DEFAULT_DISPLAY

        display = DEFAULT_DISPLAY
    widths = display.get("column_widths", {})
    suffix = display.get("truncation_suffix", "..")

    match col_id:
        case "pin":
            return format_pin(pr)
        case "status":
            return format_status(pr.get("status", ""), pr)
        case "author":
            return truncate(pr.get("author", ""), widths.get("author", 14), suffix)
        case "repo":
            return shorten_repo(pr.get("repoName", ""))
        case "id":
            return str(pr.get("id", ""))
        case "title":
            return truncate(pr.get("title", ""), widths.get("title", 50), suffix)
        case "my_vote":
            return format_my_vote(
                pr.get("myVote", ""), pr.get("isRequiredReviewer", False)
            )
        case "votes":
            if is_reviews:
                return format_reviews(
                    pr.get("reviews", []), exclude_vote=pr.get("myVote", "")
                )
            return format_reviews(pr.get("reviews", []))
        case "checks":
            return format_checks(pr)
        case "comments":
            return format_comments(pr)
        case "updated":
            return format_time_ago(pr.get("lastUpdated"))
        case "fetched":
            return format_time_ago(pr.get("lastLoaded"))
        case "source":
            return format_source(pr.get("source", ""))
        case _:
            return ""


def sort_prs(prs: list[dict]) -> list[dict]:
    """Sort PRs by repo ascending, then lastUpdated descending."""
    from datetime import datetime

    def _sort_key(pr: dict):
        repo = shorten_repo(pr.get("repoName", "")).lower()
        updated = pr.get("lastUpdated") or ""
        try:
            dt = datetime.fromisoformat(updated)
            ts = dt.timestamp()
        except Exception:
            ts = 0.0
        return (repo, -ts)

    return sorted(prs, key=_sort_key)


def pr_matches_filter(pr: dict, query: str) -> bool:
    query = query.lower()
    searchable = " ".join(
        [
            pr.get("title", ""),
            pr.get("author", ""),
            pr.get("repoName", ""),
            str(pr.get("id", "")),
            pr.get("status", ""),
            pr.get("source", ""),
        ]
    ).lower()
    return all(term in searchable for term in query.split())
