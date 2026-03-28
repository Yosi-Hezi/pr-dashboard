"""PR data store — manages persistent PR data backed by SQLite."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .ado_client import AdoApiError, AdoAuthError, AdoClient
from .db import Database, get_database
from .gh_client import GhApiError, GhClient
from .logger import get_logger

log = get_logger()


# ── Backward-compatible helpers (used by tests) ──────────────────────────


def _repo_entry(source: str, repo: str) -> dict:
    """Create a qualified repo entry."""
    return {"source": source, "repo": repo}


def _repo_in_list(source: str, repo: str, lst: list[dict]) -> bool:
    """Check if a qualified repo exists in a list."""
    return any(r.get("source") == source and r.get("repo") == repo for r in lst)


def _remove_repo_from_list(source: str, repo: str, lst: list[dict]) -> bool:
    """Remove a qualified repo from a list. Returns True if found."""
    for i, r in enumerate(lst):
        if r.get("source") == source and r.get("repo") == repo:
            lst.pop(i)
            return True
    return False


def _pr_key(pr: dict) -> tuple[str, int]:
    return (pr.get("source", ""), pr.get("id", 0))


class PrDataStore:
    """Manages persistent PR data backed by SQLite."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db: Database = get_database(db_path)
        self._lock = asyncio.Lock()

    @property
    def db(self) -> Database:
        return self._db

    # ── Read ──────────────────────────────────────────────────────────────

    def load_prs(self) -> list[dict]:
        """Load all PRs."""
        return self._db.load_prs()

    # ── Pin ───────────────────────────────────────────────────────────────

    def toggle_pin(self, pr_id: int, source: str = "") -> bool | None:
        """Toggle pinned state for a PR. Returns new pinned state, or None if not found."""
        return self._db.toggle_pin(source, pr_id)

    # ── Source management ─────────────────────────────────────────────────

    def get_active_sources(self) -> list[str]:
        """Return sources that should be synced: (discovered ∪ include) - exclude."""
        discovered = set(self._db.get_sources("discovered"))
        include = set(self._db.get_sources("include"))
        exclude = set(self._db.get_sources("exclude"))
        return sorted((discovered | include) - exclude)

    def get_sources_for_manage(self) -> list[tuple[str, bool]]:
        """Return all known sources with active status for TUI."""
        all_pairs = self._db.get_all_sources()
        all_names: set[str] = set()
        exclude_set: set[str] = set()
        for name, list_type in all_pairs:
            all_names.add(name)
            if list_type == "exclude":
                exclude_set.add(name)
        return [(s, s not in exclude_set) for s in sorted(all_names)]

    def include_source(self, source: str) -> bool:
        """Add a source to the include list. Returns True if newly added."""
        added = self._db.add_source(source, "include")
        if not added:
            return False
        self._db.remove_source(source, "exclude")
        log.info("Included source: %s", source)
        return True

    def exclude_source(self, source: str) -> bool:
        """Add a source to the exclude list. Idempotent. Returns True if newly excluded."""
        added = self._db.add_source(source, "exclude")
        if not added:
            return False
        log.info("Excluded source: %s", source)
        return True

    def toggle_source(self, source: str) -> str | None:
        """Toggle source active/excluded. Returns new state or None if deleted."""
        discovered = self._db.get_sources("discovered")
        include = self._db.get_sources("include")
        exclude = self._db.get_sources("exclude")

        if source in exclude:
            self._db.remove_source(source, "exclude")
            log.info("Activated source: %s", source)
            return "active"
        else:
            if source in discovered:
                self._db.add_source(source, "exclude")
                self._db.remove_source(source, "include")
                log.info("Excluded source: %s", source)
                return "excluded"
            elif source in include:
                self._db.remove_source(source, "include")
                log.info("Removed included source: %s", source)
                return None
            return None

    # ── Repo management ──────────────────────────────────────────────────

    def get_repos_for_manage(self) -> list[tuple[dict, bool]]:
        """Return all known repos with active status for TUI."""
        all_triples = self._db.get_all_repos()
        seen: set[tuple[str, str]] = set()
        all_repos: list[dict] = []
        exclude_keys: set[tuple[str, str]] = set()

        for source, repo, list_type in all_triples:
            k = (source, repo)
            if list_type == "exclude":
                exclude_keys.add(k)
            if k not in seen and repo:
                seen.add(k)
                all_repos.append({"source": source, "repo": repo})

        all_repos.sort(key=lambda r: (r["source"], r["repo"]))
        return [(r, (r["source"], r["repo"]) not in exclude_keys) for r in all_repos]

    def include_repo(self, source: str, repo: str) -> bool:
        """Add a repo to repos.include. Returns True if newly added."""
        added = self._db.add_repo(source, repo, "include")
        if not added:
            return False
        self._db.remove_repo(source, repo, "exclude")
        log.info("Included repo: %s :: %s", source, repo)
        return True

    def exclude_repo(self, source: str, repo: str) -> bool:
        """Add a repo to repos.exclude. Returns True if newly excluded."""
        added = self._db.add_repo(source, repo, "exclude")
        if not added:
            return False
        removed = self._db.remove_reviewer_prs_for_repo(source, repo)
        log.info(
            "Excluded repo %s :: %s (removed %d review PRs)", source, repo, removed
        )
        return True

    def toggle_repo(self, source: str, repo: str) -> str | None:
        """Toggle repo active/excluded. Returns new state or None if deleted."""
        if self._db.repo_in_list(source, repo, "exclude"):
            self._db.remove_repo(source, repo, "exclude")
            log.info("Activated repo: %s :: %s", source, repo)
            return "active"
        else:
            if self._db.repo_in_list(source, repo, "discovered"):
                self._db.add_repo(source, repo, "exclude")
                self._db.remove_repo(source, repo, "include")
                removed = self._db.remove_reviewer_prs_for_repo(source, repo)
                log.info(
                    "Excluded repo %s :: %s (removed %d review PRs)",
                    source,
                    repo,
                    removed,
                )
                return "excluded"
            elif self._db.repo_in_list(source, repo, "include"):
                self._db.remove_repo(source, repo, "include")
                log.info("Removed included repo: %s :: %s", source, repo)
                return None
            return None

    # ── Commands ──────────────────────────────────────────────────────────

    async def _sync_source(
        self,
        source: str,
        ado_clients: dict[str, AdoClient] | None,
        gh_client: GhClient | None,
        excluded_repo_keys: set[tuple[str, str]],
        included_repos_only: set[str] | None = None,
    ) -> tuple[list[dict], str | None, list[dict]]:
        """Sync a single source. Returns (entries, current_user_email, repo_discoveries)."""
        log.info(
            "[%s] Starting sync (included_repos=%s)...",
            source,
            included_repos_only,
        )
        entries: list[dict] = []
        repo_discoveries: list[dict] = []
        user_email: str | None = None

        try:
            if source.startswith("ado/"):
                org = source.removeprefix("ado/")
                shared = (ado_clients or {}).get(org)
                client = shared or AdoClient(org=org)
                try:
                    _, user_email = await client.get_current_user()

                    authored_raw, review_raw = await asyncio.gather(
                        client.list_my_prs(status="active"),
                        client.list_my_review_prs(status="active"),
                    )

                    authored_ids = {pr["pullRequestId"] for pr in authored_raw}
                    review_only = [
                        pr
                        for pr in review_raw
                        if pr["pullRequestId"] not in authored_ids
                        and not pr.get("isDraft", False)
                        and (
                            source,
                            pr.get("repository", {}).get("name", ""),
                        )
                        not in excluded_repo_keys
                    ]

                    if included_repos_only is not None:
                        authored_raw = [
                            pr
                            for pr in authored_raw
                            if pr.get("repository", {}).get("name", "")
                            in included_repos_only
                        ]
                        review_only = [
                            pr
                            for pr in review_only
                            if pr.get("repository", {}).get("name", "")
                            in included_repos_only
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
                            repo_name = entry.get("repoName", "")
                            if repo_name:
                                repo_discoveries.append(_repo_entry(source, repo_name))
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
                await gh.get_username()

                authored_prs, review_prs = await asyncio.gather(
                    gh.list_my_prs(),
                    gh.list_my_review_prs(),
                )

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
                    and not p.get("isDraft", False)
                    and (
                        source,
                        p.get("repository", {}).get("name", ""),
                    )
                    not in excluded_repo_keys
                ]

                if included_repos_only is not None:
                    authored_prs = [
                        p
                        for p in authored_prs
                        if p.get("repository", {}).get("name", "")
                        in included_repos_only
                    ]
                    review_only = [
                        p
                        for p in review_only
                        if p.get("repository", {}).get("name", "")
                        in included_repos_only
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
                        repo_name = entry.get("repoName", "")
                        if repo_name:
                            repo_discoveries.append(_repo_entry(source, repo_name))
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

        return entries, user_email, repo_discoveries

    async def sync(
        self,
        ado_clients: dict[str, AdoClient] | None = None,
        gh_client: GhClient | None = None,
    ) -> list[dict]:
        """Fetch PRs from active sources and save. Returns PR list."""
        async with self._lock:
            # ── Phase 1: Discover sources ──────────────────────────────
            discovered_sources: list[str] = []

            discovery_client = None
            try:
                discovery_client = AdoClient()
                orgs = await discovery_client.discover_orgs()
                discovered_sources.extend(f"ado/{org}" for org in orgs)
            except Exception as exc:
                log.warning("Source discovery (ADO) failed: %s", exc)
            finally:
                if discovery_client:
                    await discovery_client.close()

            gh_disc = gh_client or GhClient()
            gh_disc_is_local = gh_client is None
            try:
                gh_user = await gh_disc.check_auth()
                if gh_user:
                    discovered_sources.append("github")
            except Exception as exc:
                log.warning("Source discovery (GitHub) failed: %s", exc)
            finally:
                if gh_disc_is_local:
                    await gh_disc.close()

            self._db.set_sources("discovered", discovered_sources)
            log.info(
                "Discovered %d sources: %s",
                len(discovered_sources),
                discovered_sources,
            )

            # ── Phase 2: Determine what to fetch ──────────────────────
            include_sources = set(self._db.get_sources("include"))
            exclude_sources = set(self._db.get_sources("exclude"))
            active_set = (set(discovered_sources) | include_sources) - exclude_sources

            include_repos = self._db.get_repos("include")
            exclude_repos = self._db.get_repos("exclude")

            included_repo_by_source: dict[str, set[str]] = {}
            for r in include_repos:
                rs = r.get("source", "")
                if rs and rs not in active_set:
                    included_repo_by_source.setdefault(rs, set()).add(r.get("repo", ""))

            excluded_repo_keys = {
                (r.get("source", ""), r.get("repo", "")) for r in exclude_repos
            }

            all_sources_to_fetch = sorted(
                active_set | set(included_repo_by_source.keys())
            )

            if not all_sources_to_fetch:
                log.warning("No active sources — nothing to sync")
                return self._db.load_prs()

            # ── Phase 3: Fetch PRs from each source ───────────────────
            all_entries: list[dict] = []
            all_repo_discoveries: list[dict] = []

            ado_token: str | None = None
            ado_orgs = {
                s.removeprefix("ado/")
                for s in all_sources_to_fetch
                if s.startswith("ado/")
            }
            if ado_orgs:
                try:
                    from azure.identity import AzureCliCredential

                    tok = await asyncio.to_thread(
                        AzureCliCredential().get_token,
                        "499b84ac-1321-427f-aa17-267ca6975798/.default",
                    )
                    ado_token = tok.token
                except Exception as exc:
                    log.warning("Failed to pre-fetch ADO token: %s", exc)

            shared_ado: dict[str, AdoClient] = {
                org: AdoClient(org=org, token=ado_token) for org in ado_orgs
            }

            try:
                source_tasks = []
                for source in all_sources_to_fetch:
                    is_active_source = source in active_set
                    included_repos_only = (
                        included_repo_by_source.get(source)
                        if not is_active_source
                        else None
                    )
                    source_tasks.append(
                        self._sync_source(
                            source,
                            shared_ado,
                            gh_client,
                            excluded_repo_keys,
                            included_repos_only,
                        )
                    )
                results = await asyncio.gather(*source_tasks, return_exceptions=True)
            finally:
                for client in shared_ado.values():
                    await client.close()

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    log.error(
                        "[%s] Unexpected error: %s",
                        all_sources_to_fetch[i],
                        result,
                    )
                elif isinstance(result, tuple):
                    entries, user_email, repo_disc = result
                    all_entries.extend(entries)
                    all_repo_discoveries.extend(repo_disc)
                    if user_email:
                        self._db.set_meta("currentUser", user_email)

            # ── Phase 4: Update repos.discovered (dedup) ──────────────
            seen_repos: set[tuple[str, str]] = set()
            unique_repos: list[dict] = []
            for r in all_repo_discoveries:
                k = (r["source"], r["repo"])
                if k not in seen_repos:
                    seen_repos.add(k)
                    unique_repos.append(r)
            self._db.set_repos("discovered", unique_repos)

            # ── Phase 5: Clean up stale excludes ──────────────────────
            all_known_srcs = set(discovered_sources) | include_sources
            current_exclude_repos = self._db.get_repos("exclude")
            valid_exclude_repos = [
                r for r in current_exclude_repos
                if r.get("source", "") in all_known_srcs
            ]
            if len(valid_exclude_repos) < len(current_exclude_repos):
                self._db.set_repos("exclude", valid_exclude_repos)

            current_exclude_sources = self._db.get_sources("exclude")
            valid_exclude_sources = [
                s for s in current_exclude_sources if s in all_known_srcs
            ]
            if len(valid_exclude_sources) < len(current_exclude_sources):
                self._db.set_sources("exclude", valid_exclude_sources)

            # ── Phase 6: Upsert + purge ───────────────────────────────
            self._db.upsert_prs_batch(all_entries)
            self._db.purge_reviewer_prs(excluded_repo_keys)

            prs = self._db.load_prs()
            log.info("Synced %d PRs total", len(prs))
            return prs

    async def refresh(
        self,
        pr_id: int,
        source: str = "",
        ado_clients: dict[str, AdoClient] | None = None,
    ) -> dict | None:
        """Refresh a single tracked PR. Returns updated entry or None."""
        async with self._lock:
            pr = self._db.find_pr_by_id(pr_id, source)
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
                url = pr.get("url", "")
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
                log.warning("PR #%d has unknown source '%s', cannot refresh", pr_id, pr_source)
                return pr

            self._db.upsert_pr(entry)
            log.info("Refreshed PR #%d", pr_id)
            return entry

    async def refresh_all(
        self, ado_clients: dict[str, AdoClient] | None = None
    ) -> list[dict]:
        """Refresh all tracked PRs. Returns updated PR list."""
        async with self._lock:
            prs = self._db.load_prs()
            if not prs:
                log.info("No PRs to refresh")
                return []

            by_source: dict[str, list[dict]] = {}
            for pr in prs:
                src = pr.get("source", "ado/msazure")
                by_source.setdefault(src, []).append(pr)

            async def _refresh_one(pr: dict, client: AdoClient) -> dict:
                try:
                    ado_pr = await client.get_pr(pr["id"])
                    return await client.enrich_pr(ado_pr, role=pr.get("role", "author"))
                except (AdoApiError, AdoAuthError) as exc:
                    log.warning("Failed to refresh PR #%d: %s", pr["id"], exc)
                    return pr

            new_entries: list[dict] = []
            for src, src_prs in by_source.items():
                if src.startswith("ado/"):
                    org = src.removeprefix("ado/")
                    shared = (ado_clients or {}).get(org)
                    client = shared or AdoClient(org=org)
                    try:
                        entries = await asyncio.gather(
                            *(_refresh_one(pr, client) for pr in src_prs)
                        )
                        new_entries.extend(entries)
                    finally:
                        if not shared:
                            await client.close()
                elif src == "github":
                    new_entries.extend(src_prs)
                else:
                    new_entries.extend(src_prs)

            self._db.replace_all_prs(new_entries)
            log.info("Refreshed all %d PRs", len(new_entries))
            return new_entries

    def remove(self, pr_id: int, source: str = "") -> bool:
        """Remove a PR from tracking."""
        return self._db.remove_pr(pr_id, source)

    def clean(self) -> int:
        """Remove completed/abandoned PRs. Returns count removed."""
        return self._db.clean_non_active()

    async def add_pr_by_url(
        self, url: str, role: str = "reviewer"
    ) -> tuple[dict, bool]:
        """Add a PR by URL (ADO or GitHub).

        Returns (enriched_entry, already_existed).
        If the PR is already tracked as 'author', the role is preserved.
        """
        async with self._lock:

            def _effective_role(source: str, pr_id: int) -> str:
                existing = self._db.get_pr(source, pr_id)
                if existing and existing.get("role") == "author":
                    return "author"
                return role

            # Try ADO URL
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
                existed = self._db.upsert_pr(entry)
                log.info("Added ADO PR #%d from %s", pr_id, url)
                return entry, existed

            # Try GitHub URL
            gh_match = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)", url)
            if gh_match:
                owner_repo = gh_match.group(1)
                number = int(gh_match.group(2))
                actual_role = _effective_role("github", number)
                gh = GhClient()
                gh_pr = await gh.get_pr(owner_repo, number)
                entry = await gh.enrich_pr(gh_pr, role=actual_role)
                existed = self._db.upsert_pr(entry)
                log.info("Added GitHub PR #%d from %s", number, url)
                return entry, existed

            raise ValueError(f"Unsupported PR URL format: {url}")
