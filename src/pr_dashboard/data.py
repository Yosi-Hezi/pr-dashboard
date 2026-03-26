"""PR data store — manages prs.json with platformdirs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .ado_client import AdoApiError, AdoAuthError, AdoClient
from .gh_client import GhApiError, GhClient
from .logger import get_logger
from platformdirs import user_data_dir

log = get_logger()

DATA_DIR = Path(user_data_dir("pr-dashboard", ensure_exists=True))
DATA_FILE = DATA_DIR / "prs.json"

DATA_VERSION = 2


def _empty_data() -> dict:
    return {"version": DATA_VERSION, "sources": [], "currentUser": "", "prs": []}


def _migrate_v1_to_v2(data: dict) -> dict:
    """Add source field to v1 data, default to ado/msazure."""
    if data.get("version", 1) >= 2:
        return data
    for pr in data.get("prs", []):
        if "source" not in pr:
            pr["source"] = "ado/msazure"
    if "sources" not in data:
        data["sources"] = ["ado/msazure"] if data.get("prs") else []
    data["version"] = DATA_VERSION
    log.info("Migrated data from v1 to v2 (%d PRs)", len(data.get("prs", [])))
    return data


def _fix_stale_roles(data: dict) -> dict:
    """Fix PRs with isMine=False but role=author (stale pre-role data)."""
    fixed = 0
    for pr in data.get("prs", []):
        if pr.get("role") == "author" and pr.get("isMine") is False:
            pr["role"] = "reviewer"
            fixed += 1
    if fixed:
        log.info("Fixed %d stale PRs: role author → reviewer (isMine=False)", fixed)
    return data


def _pr_key(pr: dict) -> tuple[str, int]:
    return (pr.get("source", ""), pr.get("id", 0))


class PrDataStore:
    """Manages persistent PR data backed by prs.json."""

    def __init__(self) -> None:
        self.data_file = DATA_FILE
        self._lock = asyncio.Lock()

    def load(self) -> dict:
        """Load data from disk. Returns empty structure if file missing."""
        if not self.data_file.exists():
            return _empty_data()
        try:
            data = json.loads(self.data_file.read_text(encoding="utf-8"))
            return _fix_stale_roles(_migrate_v1_to_v2(data))
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Failed to read %s: %s", self.data_file, exc)
            return _empty_data()

    def load_prs(self) -> list[dict]:
        """Load just the PR list."""
        return self.load().get("prs", [])

    def save(self, data: dict) -> None:
        """Write data to disk atomically (write tmp → rename)."""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        import tempfile

        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            dir=self.data_file.parent,
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            tmp.write(json.dumps(data, indent=2, ensure_ascii=False))
            tmp.close()
            Path(tmp.name).replace(self.data_file)
        except BaseException:
            Path(tmp.name).unlink(missing_ok=True)
            raise
        log.debug("Saved %d PRs to %s", len(data.get("prs", [])), self.data_file)

    def _upsert_pr(self, data: dict, entry: dict) -> None:
        """Insert or update a PR entry using composite (source, id) key.

        Preserves user-local fields (pinned) across API refreshes.
        """
        key = _pr_key(entry)
        idx = next((i for i, p in enumerate(data["prs"]) if _pr_key(p) == key), None)
        if idx is not None:
            # Carry over user-local fields not present in API response
            if data["prs"][idx].get("pinned"):
                entry.setdefault("pinned", data["prs"][idx]["pinned"])
            data["prs"][idx] = entry
        else:
            data["prs"].append(entry)

    def toggle_pin(self, pr_id: int, source: str = "") -> bool | None:
        """Toggle pinned state for a PR. Returns new pinned state, or None if not found."""
        data = self.load()
        key = (source, pr_id)
        for pr in data["prs"]:
            if _pr_key(pr) == key:
                new_state = not pr.get("pinned", False)
                pr["pinned"] = new_state
                self.save(data)
                log.info("PR #%d pinned=%s", pr_id, new_state)
                return new_state
        return None

    # ── Source management ─────────────────────────────────────────────────

    def get_sources(self) -> list[str]:
        return self.load().get("sources", [])

    def add_source(self, source: str) -> bool:
        """Register a source. Returns True if newly added."""
        data = self.load()
        if source in data.get("sources", []):
            return False
        data.setdefault("sources", []).append(source)
        self.save(data)
        log.info("Registered source: %s", source)
        return True

    def remove_source(self, source: str) -> bool:
        """Unregister a source and remove its PRs. Returns True if found."""
        data = self.load()
        if source not in data.get("sources", []):
            return False
        data["sources"].remove(source)
        before = len(data["prs"])
        data["prs"] = [p for p in data["prs"] if p.get("source") != source]
        removed_prs = before - len(data["prs"])
        self.save(data)
        log.info("Unregistered source %s (removed %d PRs)", source, removed_prs)
        return True

    # ── Excluded repos (for CR sync) ─────────────────────────────────────

    def get_excluded_repos(self) -> list[str]:
        """Return list of repo names excluded from CR sync."""
        return self.load().get("excludedRepos", [])

    def exclude_repo(self, repo: str) -> bool:
        """Exclude a repo from CR sync. Returns True if newly added."""
        data = self.load()
        excluded = data.setdefault("excludedRepos", [])
        if repo in excluded:
            return False
        excluded.append(repo)
        # Also remove existing reviewer PRs from this repo
        before = len(data["prs"])
        data["prs"] = [
            p
            for p in data["prs"]
            if not (p.get("role") == "reviewer" and p.get("repoName") == repo)
        ]
        removed = before - len(data["prs"])
        self.save(data)
        log.info("Excluded repo %s (removed %d review PRs)", repo, removed)
        return True

    def include_repo(self, repo: str) -> bool:
        """Remove a repo from the exclusion list. Returns True if found."""
        data = self.load()
        excluded = data.get("excludedRepos", [])
        if repo not in excluded:
            return False
        excluded.remove(repo)
        self.save(data)
        log.info("Included repo %s", repo)
        return True

    # ── Commands ──────────────────────────────────────────────────────────

    async def _sync_source(
        self,
        source: str,
        ado_clients: dict[str, AdoClient] | None,
        gh_client: GhClient | None,
        excluded_repos: set[str],
    ) -> tuple[list[dict], str | None]:
        """Sync a single source. Returns (entries, current_user_email)."""
        log.info("[%s] Starting sync...", source)
        entries: list[dict] = []
        user_email: str | None = None

        try:
            if source.startswith("ado/"):
                org = source.removeprefix("ado/")
                shared = (ado_clients or {}).get(org)
                client = shared or AdoClient(org=org)
                try:
                    _, user_email = await client.get_current_user()

                    # Fetch authored + reviewer PRs in parallel
                    authored_raw, review_raw = await asyncio.gather(
                        client.list_my_prs(status="active"),
                        client.list_my_review_prs(status="active"),
                    )

                    # Dedup: if PR appears in both, keep as author
                    authored_ids = {pr["pullRequestId"] for pr in authored_raw}
                    review_only = [
                        pr
                        for pr in review_raw
                        if pr["pullRequestId"] not in authored_ids
                        and pr.get("repository", {}).get("name", "")
                        not in excluded_repos
                        and not pr.get("isDraft", False)
                    ]

                    enriched = await asyncio.gather(
                        *(client.enrich_pr(pr, role="author") for pr in authored_raw),
                        *(client.enrich_pr(pr, role="reviewer") for pr in review_only),
                        return_exceptions=True,
                    )
                    for entry in enriched:
                        if isinstance(entry, Exception):
                            log.error("[%s] Failed to enrich PR: %s", source, entry)
                            continue
                        if isinstance(entry, dict):
                            entries.append(entry)
                    ok = len([e for e in enriched if not isinstance(e, Exception)])
                    log.info(
                        "[%s] Synced %d PRs (%d authored, %d reviews)",
                        source,
                        ok,
                        len(authored_raw),
                        len(review_only),
                    )
                finally:
                    if not shared:
                        await client.close()

            elif source == "github":
                gh = gh_client or GhClient()

                # Prefetch username to avoid N redundant auth calls
                await gh.get_username()

                # Fetch authored + reviewer PRs in parallel
                authored_prs, review_prs = await asyncio.gather(
                    gh.list_my_prs(),
                    gh.list_my_review_prs(),
                )

                # Dedup: if PR appears in both, keep as author
                authored_keys = {
                    (
                        p.get("repository", {}).get("nameWithOwner", ""),
                        p["number"],
                    )
                    for p in authored_prs
                }
                review_only = [
                    p
                    for p in review_prs
                    if (
                        p.get("repository", {}).get("nameWithOwner", ""),
                        p["number"],
                    )
                    not in authored_keys
                    and p.get("repository", {}).get("name", "") not in excluded_repos
                    and not p.get("isDraft", False)
                ]

                enriched = await asyncio.gather(
                    *(gh.enrich_pr(pr, role="author") for pr in authored_prs),
                    *(gh.enrich_pr(pr, role="reviewer") for pr in review_only),
                    return_exceptions=True,
                )
                for entry in enriched:
                    if isinstance(entry, Exception):
                        log.error("[github] Failed to enrich PR: %s", entry)
                        continue
                    if isinstance(entry, dict):
                        entries.append(entry)
                ok = len([e for e in enriched if not isinstance(e, Exception)])
                log.info(
                    "[github] Synced %d PRs (%d authored, %d reviews)",
                    ok,
                    len(authored_prs),
                    len(review_only),
                )

        except (AdoApiError, AdoAuthError) as exc:
            log.error("[%s] Sync failed: %s", source, exc)
        except GhApiError as exc:
            log.error("[%s] GitHub sync failed: %s", source, exc)
        except Exception as exc:
            log.error("[%s] Unexpected error: %s", source, exc)

        return entries, user_email

    async def sync(
        self,
        ado_clients: dict[str, AdoClient] | None = None,
        gh_client: GhClient | None = None,
    ) -> list[dict]:
        """Fetch PRs from ALL registered sources and save. Returns PR list."""
        async with self._lock:
            data = self.load()
            sources = data.get("sources", [])
            excluded_repos = set(data.get("excludedRepos", []))

            if not sources:
                log.warning("No sources registered — nothing to sync")
                return data.get("prs", [])

            # Gather PRs from all sources
            all_entries: list[dict] = []

            # Sync all sources concurrently
            source_tasks = []
            for source in sources:
                source_tasks.append(
                    self._sync_source(source, ado_clients, gh_client, excluded_repos)
                )
            results = await asyncio.gather(*source_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    log.error("[%s] Unexpected error: %s", sources[i], result)
                elif isinstance(result, tuple):
                    entries, user_email = result
                    all_entries.extend(entries)
                    if user_email:
                        data["currentUser"] = user_email

            # Upsert all fetched entries
            for entry in all_entries:
                self._upsert_pr(data, entry)

            # Purge reviewer PRs: excluded repos, drafts, or user-authored
            data["prs"] = [
                p
                for p in data["prs"]
                if p.get("role") != "reviewer"
                or (
                    p.get("repoName", "") not in excluded_repos
                    and not p.get("isDraft", False)
                    and not p.get("isMine", False)
                )
            ]

            self.save(data)
            log.info(
                "Synced %d PRs total from %d sources", len(data["prs"]), len(sources)
            )
            return data["prs"]

    async def refresh(
        self,
        pr_id: int,
        source: str = "",
        ado_clients: dict[str, AdoClient] | None = None,
    ) -> dict | None:
        """Refresh a single tracked PR. Returns updated entry or None."""
        async with self._lock:
            data = self.load()

            # Find the PR
            pr = None
            for p in data["prs"]:
                if p["id"] == pr_id and (not source or p.get("source") == source):
                    pr = p
                    break
            if pr is None:
                log.warning("PR #%d not found for refresh", pr_id)
                return None

            pr_source = pr.get("source", "")
            existing_role = pr.get("role", "author")

            if pr_source.startswith("ado/"):
                org = pr_source.removeprefix("ado/")
                shared = (ado_clients or {}).get(org)
                client = shared or AdoClient(org=org)
                try:
                    ado_pr = await client.get_pr(pr_id)
                    entry = await client.enrich_pr(ado_pr, role=existing_role)
                finally:
                    if not shared:
                        await client.close()
            elif pr_source == "github":
                # Parse owner_repo from stored URL
                url = pr.get("url", "")
                # URL format: https://github.com/{owner}/{repo}/pull/{number}
                parts = url.replace("https://github.com/", "").split("/")
                if len(parts) >= 4:
                    owner_repo = f"{parts[0]}/{parts[1]}"
                    gh = GhClient()
                    gh_pr = await gh.get_pr(owner_repo, pr_id)
                    entry = await gh.enrich_pr(gh_pr, role=existing_role)
                else:
                    log.warning("Cannot parse GitHub URL for refresh: %s", url)
                    return pr
            else:
                # Legacy: try default org
                async with AdoClient() as client:
                    ado_pr = await client.get_pr(pr_id)
                    entry = await client.enrich_pr(ado_pr, role=existing_role)

            self._upsert_pr(data, entry)
            self.save(data)
            log.info("Refreshed PR #%d", pr_id)
            return entry

    async def refresh_all(
        self, ado_clients: dict[str, AdoClient] | None = None
    ) -> list[dict]:
        """Refresh all tracked PRs. Returns updated PR list."""
        async with self._lock:
            data = self.load()
            if not data["prs"]:
                log.info("No PRs to refresh")
                return []

            # Group PRs by source to reuse clients
            by_source: dict[str, list[dict]] = {}
            for pr in data["prs"]:
                src = pr.get("source", "ado/msazure")
                by_source.setdefault(src, []).append(pr)

            async def _refresh_one(pr: dict, client: AdoClient) -> dict:
                try:
                    ado_pr = await client.get_pr(pr["id"])
                    return await client.enrich_pr(ado_pr, role=pr.get("role", "author"))
                except (AdoApiError, AdoAuthError) as exc:
                    log.warning("Failed to refresh PR #%d: %s", pr["id"], exc)
                    return pr

            new_entries = []
            for src, prs in by_source.items():
                if src.startswith("ado/"):
                    org = src.removeprefix("ado/")
                    shared = (ado_clients or {}).get(org)
                    client = shared or AdoClient(org=org)
                    try:
                        entries = await asyncio.gather(
                            *(_refresh_one(pr, client) for pr in prs)
                        )
                        new_entries.extend(entries)
                    finally:
                        if not shared:
                            await client.close()
                elif src == "github":
                    # Keep existing GitHub PRs (suggest full sync for update)
                    new_entries.extend(prs)
                else:
                    new_entries.extend(prs)

            data["prs"] = list(new_entries)
            self.save(data)
            log.info("Refreshed all %d PRs", len(data["prs"]))
            return data["prs"]

    def remove(self, pr_id: int, source: str = "") -> bool:
        """Remove a PR from tracking. Source should be provided to avoid cross-source removal."""
        data = self.load()
        before = len(data["prs"])
        if source:
            data["prs"] = [
                p
                for p in data["prs"]
                if not (p["id"] == pr_id and p.get("source") == source)
            ]
        else:
            # Fallback: remove first match only (not all with same ID)
            idx = next((i for i, p in enumerate(data["prs"]) if p["id"] == pr_id), None)
            if idx is not None:
                data["prs"].pop(idx)
        if len(data["prs"]) < before:
            self.save(data)
            log.info("Removed PR #%d", pr_id)
            return True
        log.warning("PR #%d not found for removal", pr_id)
        return False

    def clean(self) -> int:
        """Remove completed/abandoned PRs. Returns count removed."""
        data = self.load()
        before = len(data["prs"])
        data["prs"] = [p for p in data["prs"] if p.get("status") == "active"]
        removed = before - len(data["prs"])
        if removed > 0:
            self.save(data)
            log.info("Cleaned %d non-active PRs", removed)
        return removed

    async def add_pr_by_url(self, url: str, role: str = "reviewer") -> dict:
        """Add a PR by URL (ADO or GitHub). Returns the enriched entry.

        If the PR is already tracked as 'author', the role is preserved.
        """
        import re

        async with self._lock:
            data = self.load()

            # Check if PR already exists — preserve author role
            def _effective_role(source: str, pr_id: int) -> str:
                for p in data.get("prs", []):
                    if p.get("source") == source and p.get("id") == pr_id:
                        existing = p.get("role", "author")
                        if existing == "author":
                            return "author"
                return role

            # Try ADO URL: https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{id}
            ado_match = re.match(
                r"https://dev\.azure\.com/([^/]+)/[^/]+/_git/[^/]+/pullrequest/(\d+)",
                url,
            )
            if ado_match:
                org = ado_match.group(1)
                pr_id = int(ado_match.group(2))
                actual_role = _effective_role(f"ado/{org}", pr_id)
                async with AdoClient(org=org) as client:
                    ado_pr = await client.get_pr(pr_id)
                    entry = await client.enrich_pr(ado_pr, role=actual_role)
                self._upsert_pr(data, entry)
                self.save(data)
                log.info("Added ADO PR #%d from %s", pr_id, url)
                return entry

            # Try GitHub URL: https://github.com/{owner}/{repo}/pull/{number}
            gh_match = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)", url)
            if gh_match:
                owner_repo = gh_match.group(1)
                number = int(gh_match.group(2))
                actual_role = _effective_role("github", number)
                gh = GhClient()
                gh_pr = await gh.get_pr(owner_repo, number)
                entry = await gh.enrich_pr(gh_pr, role=actual_role)
                self._upsert_pr(data, entry)
                self.save(data)
                log.info("Added GitHub PR #%d from %s", number, url)
                return entry

            raise ValueError(f"Unsupported PR URL format: {url}")
