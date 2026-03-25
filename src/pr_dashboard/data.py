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

DATA_VERSION = 3


def _empty_data() -> dict:
    return {
        "version": DATA_VERSION,
        "currentUser": "",
        "sources": {"discovered": [], "include": [], "exclude": []},
        "repos": {"discovered": [], "include": [], "exclude": []},
        "prs": [],
    }


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
    """Manages persistent PR data backed by prs.json."""

    def __init__(self) -> None:
        self.data_file = DATA_FILE
        self._lock = asyncio.Lock()

    def load(self) -> dict:
        """Load data from disk. Returns empty structure if file missing/incompatible."""
        if not self.data_file.exists():
            return _empty_data()
        try:
            data = json.loads(self.data_file.read_text(encoding="utf-8"))
            if data.get("version") != DATA_VERSION:
                return _empty_data()
            return data
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

    def _upsert_pr(self, data: dict, entry: dict) -> bool:
        """Insert or update a PR entry using composite (source, id) key.

        Preserves user-local fields (pinned) across API refreshes.
        Returns True if the PR already existed (update), False if new (insert).
        """
        key = _pr_key(entry)
        idx = next((i for i, p in enumerate(data["prs"]) if _pr_key(p) == key), None)
        if idx is not None:
            # Carry over user-local fields not present in API response
            if data["prs"][idx].get("pinned"):
                entry.setdefault("pinned", data["prs"][idx]["pinned"])
            data["prs"][idx] = entry
            return True
        else:
            data["prs"].append(entry)
            return False

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

    def get_active_sources(self) -> list[str]:
        """Return sources that should be synced: (discovered ∪ include) - exclude."""
        data = self.load()
        src = data.get("sources", {})
        active = set(src.get("discovered", [])) | set(src.get("include", []))
        return sorted(active - set(src.get("exclude", [])))

    def get_sources_for_manage(self) -> list[tuple[str, bool]]:
        """Return all known sources with active status for TUI.

        Returns list of (source_str, is_active) sorted alphabetically.
        """
        data = self.load()
        src = data.get("sources", {})
        all_sources = (
            set(src.get("discovered", []))
            | set(src.get("include", []))
            | set(src.get("exclude", []))
        )
        exclude_set = set(src.get("exclude", []))
        return [(s, s not in exclude_set) for s in sorted(all_sources)]

    def include_source(self, source: str) -> bool:
        """Add a source to the include list. Returns True if newly added."""
        data = self.load()
        src = data.setdefault(
            "sources", {"discovered": [], "include": [], "exclude": []}
        )
        include = src.setdefault("include", [])
        if source in include:
            return False
        include.append(source)
        # Also remove from exclude if present
        exclude = src.setdefault("exclude", [])
        if source in exclude:
            exclude.remove(source)
        self.save(data)
        log.info("Included source: %s", source)
        return True

    def exclude_source(self, source: str) -> bool:
        """Add a source to the exclude list. Idempotent. Returns True if newly excluded."""
        data = self.load()
        src = data.setdefault(
            "sources", {"discovered": [], "include": [], "exclude": []}
        )
        exclude = src.setdefault("exclude", [])
        if source in exclude:
            return False
        exclude.append(source)
        self.save(data)
        log.info("Excluded source: %s", source)
        return True

    def toggle_source(self, source: str) -> str | None:
        """Toggle source active/excluded. Returns new state or None if deleted.

        - Active & in discovered (or both): add to exclude → 'excluded'
        - Active & ONLY in include: delete from include → None
        - Excluded: remove from exclude → 'active'
        """
        data = self.load()
        src = data.setdefault(
            "sources", {"discovered": [], "include": [], "exclude": []}
        )
        discovered = src.setdefault("discovered", [])
        include = src.setdefault("include", [])
        exclude = src.setdefault("exclude", [])

        if source in exclude:
            exclude.remove(source)
            self.save(data)
            log.info("Activated source: %s", source)
            return "active"
        else:
            if source in discovered:
                exclude.append(source)
                # Clean from include if also present
                if source in include:
                    include.remove(source)
                self.save(data)
                log.info("Excluded source: %s", source)
                return "excluded"
            elif source in include:
                include.remove(source)
                self.save(data)
                log.info("Removed included source: %s", source)
                return None
            return None

    # ── Repo management ──────────────────────────────────────────────────

    def get_repos_for_manage(self) -> list[tuple[dict, bool]]:
        """Return all known repos with active status for TUI.

        Returns list of ({"source": str, "repo": str}, is_active) sorted.
        """
        data = self.load()
        rp = data.get("repos", {})

        seen: set[tuple[str, str]] = set()
        all_repos: list[dict] = []
        for lst in [
            rp.get("discovered", []),
            rp.get("include", []),
            rp.get("exclude", []),
        ]:
            for r in lst:
                k = (r.get("source", ""), r.get("repo", ""))
                if k not in seen and k[1]:
                    seen.add(k)
                    all_repos.append({"source": k[0], "repo": k[1]})
        all_repos.sort(key=lambda r: (r["source"], r["repo"]))

        exclude_keys = {
            (r.get("source", ""), r.get("repo", "")) for r in rp.get("exclude", [])
        }
        return [(r, (r["source"], r["repo"]) not in exclude_keys) for r in all_repos]

    def include_repo(self, source: str, repo: str) -> bool:
        """Add a repo to repos.include. Returns True if newly added."""
        data = self.load()
        rp = data.setdefault("repos", {"discovered": [], "include": [], "exclude": []})
        include = rp.setdefault("include", [])
        if _repo_in_list(source, repo, include):
            return False
        include.append(_repo_entry(source, repo))
        _remove_repo_from_list(source, repo, rp.setdefault("exclude", []))
        self.save(data)
        log.info("Included repo: %s :: %s", source, repo)
        return True

    def exclude_repo(self, source: str, repo: str) -> bool:
        """Add a repo to repos.exclude. Idempotent. Returns True if newly excluded."""
        data = self.load()
        rp = data.setdefault("repos", {"discovered": [], "include": [], "exclude": []})
        exclude = rp.setdefault("exclude", [])
        if _repo_in_list(source, repo, exclude):
            return False
        exclude.append(_repo_entry(source, repo))
        # Remove reviewer PRs from this repo
        before = len(data["prs"])
        data["prs"] = [
            p
            for p in data["prs"]
            if not (
                p.get("role") == "reviewer"
                and p.get("source") == source
                and p.get("repoName") == repo
            )
        ]
        removed = before - len(data["prs"])
        self.save(data)
        log.info(
            "Excluded repo %s :: %s (removed %d review PRs)", source, repo, removed
        )
        return True

    def toggle_repo(self, source: str, repo: str) -> str | None:
        """Toggle repo active/excluded. Returns new state or None if deleted.

        - Active & in discovered (or both): add to exclude → 'excluded'
        - Active & ONLY in include: delete from include → None
        - Excluded: remove from exclude → 'active'
        """
        data = self.load()
        rp = data.setdefault("repos", {"discovered": [], "include": [], "exclude": []})
        discovered = rp.setdefault("discovered", [])
        include = rp.setdefault("include", [])
        exclude = rp.setdefault("exclude", [])

        if _repo_in_list(source, repo, exclude):
            _remove_repo_from_list(source, repo, exclude)
            self.save(data)
            log.info("Activated repo: %s :: %s", source, repo)
            return "active"
        else:
            if _repo_in_list(source, repo, discovered):
                exclude.append(_repo_entry(source, repo))
                # Clean from include if also present
                _remove_repo_from_list(source, repo, include)
                # Remove reviewer PRs from this repo
                before = len(data["prs"])
                data["prs"] = [
                    p
                    for p in data["prs"]
                    if not (
                        p.get("role") == "reviewer"
                        and p.get("source") == source
                        and p.get("repoName") == repo
                    )
                ]
                removed = before - len(data["prs"])
                self.save(data)
                log.info(
                    "Excluded repo %s :: %s (removed %d review PRs)",
                    source,
                    repo,
                    removed,
                )
                return "excluded"
            elif _repo_in_list(source, repo, include):
                _remove_repo_from_list(source, repo, include)
                self.save(data)
                log.info("Removed included repo: %s :: %s", source, repo)
                return None
            return None

    # ── Commands ──────────────────────────────────────────────────────────

    async def sync(
        self,
        ado_clients: dict[str, AdoClient] | None = None,
        gh_client: GhClient | None = None,
    ) -> list[dict]:
        """Fetch PRs from active sources and save. Returns PR list.

        Two-step process:
        1. Discover sources → update sources.discovered
        2. Fetch PRs from active sources + included repos → update repos.discovered
        """
        async with self._lock:
            data = self.load()
            sources = data.setdefault(
                "sources", {"discovered": [], "include": [], "exclude": []}
            )
            repos = data.setdefault(
                "repos", {"discovered": [], "include": [], "exclude": []}
            )

            # ── Phase 1: Discover sources ──────────────────────────────
            discovered_sources: list[str] = []

            # ADO: discover orgs
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

            # GitHub
            gh_disc = gh_client or GhClient()
            try:
                gh_user = await gh_disc.check_auth()
                if gh_user:
                    discovered_sources.append("github")
            except Exception as exc:
                log.warning("Source discovery (GitHub) failed: %s", exc)

            sources["discovered"] = discovered_sources
            log.info(
                "Discovered %d sources: %s",
                len(discovered_sources),
                discovered_sources,
            )

            # ── Phase 2: Determine what to fetch ──────────────────────
            active_set = (
                set(sources.get("discovered", [])) | set(sources.get("include", []))
            ) - set(sources.get("exclude", []))

            # Sources with included repos that aren't in the active set
            included_repo_by_source: dict[str, set[str]] = {}
            for r in repos.get("include", []):
                rs = r.get("source", "")
                if rs and rs not in active_set:
                    included_repo_by_source.setdefault(rs, set()).add(r.get("repo", ""))

            excluded_repo_keys = {
                (r.get("source", ""), r.get("repo", ""))
                for r in repos.get("exclude", [])
            }

            all_sources_to_fetch = sorted(
                active_set | set(included_repo_by_source.keys())
            )

            if not all_sources_to_fetch:
                log.warning("No active sources — nothing to sync")
                self.save(data)
                return data.get("prs", [])

            # ── Phase 3: Fetch PRs from each source ───────────────────
            all_entries: list[dict] = []
            all_repo_discoveries: list[dict] = []

            for source in all_sources_to_fetch:
                is_active_source = source in active_set
                included_repos_only = (
                    included_repo_by_source.get(source)
                    if not is_active_source
                    else None
                )
                log.info(
                    "[%s] Syncing (active=%s, included_repos=%s)...",
                    source,
                    is_active_source,
                    included_repos_only,
                )
                try:
                    if source.startswith("ado/"):
                        org = source.removeprefix("ado/")
                        shared = (ado_clients or {}).get(org)
                        client = shared or AdoClient(org=org)
                        try:
                            _, user_email = await client.get_current_user()
                            data["currentUser"] = user_email

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

                            # If only fetching for included repos, filter both
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

                            entries = await asyncio.gather(
                                *(
                                    client.enrich_pr(pr, role="author")
                                    for pr in authored_raw
                                ),
                                *(
                                    client.enrich_pr(pr, role="reviewer")
                                    for pr in review_only
                                ),
                                return_exceptions=True,
                            )
                            for entry in entries:
                                if isinstance(entry, Exception):
                                    log.error(
                                        "[%s] Failed to enrich PR: %s",
                                        source,
                                        entry,
                                    )
                                    continue
                                if isinstance(entry, dict):
                                    repo_name = entry.get("repoName", "")
                                    if repo_name:
                                        all_repo_discoveries.append(
                                            _repo_entry(source, repo_name)
                                        )
                                    all_entries.append(entry)
                            ok = len(
                                [e for e in entries if not isinstance(e, Exception)]
                            )
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

                        # If only fetching for included repos
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

                        entries = await asyncio.gather(
                            *(gh.enrich_pr(pr, role="author") for pr in authored_prs),
                            *(gh.enrich_pr(pr, role="reviewer") for pr in review_only),
                            return_exceptions=True,
                        )
                        for entry in entries:
                            if isinstance(entry, Exception):
                                log.error("[github] Failed to enrich PR: %s", entry)
                                continue
                            if isinstance(entry, dict):
                                repo_name = entry.get("repoName", "")
                                if repo_name:
                                    all_repo_discoveries.append(
                                        _repo_entry(source, repo_name)
                                    )
                                all_entries.append(entry)
                        ok = len([e for e in entries if not isinstance(e, Exception)])
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

            # ── Phase 4: Update repos.discovered (dedup) ──────────────
            seen_repos: set[tuple[str, str]] = set()
            unique_repos: list[dict] = []
            for r in all_repo_discoveries:
                k = (r["source"], r["repo"])
                if k not in seen_repos:
                    seen_repos.add(k)
                    unique_repos.append(r)
            repos["discovered"] = unique_repos

            # ── Phase 5: Clean up stale excludes ──────────────────────
            all_known_repos = seen_repos | {
                (r.get("source", ""), r.get("repo", ""))
                for r in repos.get("include", [])
            }
            repos["exclude"] = [
                r
                for r in repos.get("exclude", [])
                if (r.get("source", ""), r.get("repo", "")) in all_known_repos
            ]
            all_known_srcs = set(sources["discovered"]) | set(
                sources.get("include", [])
            )
            sources["exclude"] = [
                s for s in sources.get("exclude", []) if s in all_known_srcs
            ]

            # ── Phase 6: Upsert + purge ───────────────────────────────
            for entry in all_entries:
                self._upsert_pr(data, entry)

            # Purge reviewer PRs: excluded repos, drafts, or own
            data["prs"] = [
                p
                for p in data["prs"]
                if p.get("role") != "reviewer"
                or (
                    (p.get("source", ""), p.get("repoName", ""))
                    not in excluded_repo_keys
                    and not p.get("isDraft", False)
                    and not p.get("isMine", False)
                )
            ]

            self.save(data)
            log.info("Synced %d PRs total", len(data["prs"]))
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

    async def add_pr_by_url(
        self, url: str, role: str = "reviewer"
    ) -> tuple[dict, bool]:
        """Add a PR by URL (ADO or GitHub).

        Returns (enriched_entry, already_existed).
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
                existed = self._upsert_pr(data, entry)
                self.save(data)
                log.info("Added ADO PR #%d from %s", pr_id, url)
                return entry, existed

            # Try GitHub URL: https://github.com/{owner}/{repo}/pull/{number}
            gh_match = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)", url)
            if gh_match:
                owner_repo = gh_match.group(1)
                number = int(gh_match.group(2))
                actual_role = _effective_role("github", number)
                gh = GhClient()
                gh_pr = await gh.get_pr(owner_repo, number)
                entry = await gh.enrich_pr(gh_pr, role=actual_role)
                existed = self._upsert_pr(data, entry)
                self.save(data)
                log.info("Added GitHub PR #%d from %s", number, url)
                return entry, existed

            raise ValueError(f"Unsupported PR URL format: {url}")
