"""GitHub CLI wrapper for PR Dashboard — uses subprocess `gh`."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime

from .logger import get_logger

log = get_logger()

GH_STATUS_MAP = {"open": "active", "closed": "completed", "merged": "completed"}

REVIEW_STATE_MAP = {
    "APPROVED": "Approved",
    "CHANGES_REQUESTED": "WaitingForAuthor",
    "COMMENTED": "NoVote",
    "DISMISSED": "NoVote",
    "PENDING": "NoVote",
}


class GhApiError(Exception):
    """Raised when a GitHub CLI call fails unexpectedly."""


class GhClient:
    """GitHub CLI subprocess wrapper for PR queries."""

    def __init__(self) -> None:
        self._username: str | None = None

    async def close(self) -> None:
        """No-op — GhClient has no persistent connections."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    @staticmethod
    def is_available() -> bool:
        """Check if `gh` CLI is installed."""
        return shutil.which("gh") is not None

    @staticmethod
    async def _run(args: list[str]) -> tuple[int, str, str]:
        """Run a `gh` command and return (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        rc = proc.returncode if proc.returncode is not None else -1
        return rc, stdout.decode(), stderr.decode()

    async def check_auth(self) -> str | None:
        """Return GitHub username if authenticated, else None."""
        if self._username:
            return self._username
        if not self.is_available():
            log.debug("gh CLI not found")
            return None
        rc, stdout, stderr = await self._run(
            ["auth", "status", "--hostname", "github.com"]
        )
        if rc != 0:
            log.debug("gh not authenticated: %s", stderr.strip())
            return None
        # Parse username from "Logged in to github.com account <user>"
        for line in (stdout + stderr).splitlines():
            if "account" in line.lower():
                parts = line.strip().split()
                for i, w in enumerate(parts):
                    if w.lower() == "account" and i + 1 < len(parts):
                        self._username = parts[i + 1].strip("()")
                        log.info("gh authenticated as %s", self._username)
                        return self._username
        # Fallback: get username via gh api
        rc2, stdout2, _ = await self._run(["api", "user", "--jq", ".login"])
        if rc2 == 0 and stdout2.strip():
            self._username = stdout2.strip()
            log.info("gh authenticated as %s", self._username)
            return self._username
        return None

    async def get_username(self) -> str:
        """Return cached GitHub username, or fetch it."""
        if self._username:
            return self._username
        username = await self.check_auth()
        return username or ""

    async def get_pr(self, owner_repo: str, number: int) -> dict:
        """Fetch a single PR in the same shape as list_my_prs entries."""
        rc, stdout, stderr = await self._run(
            [
                "api",
                f"repos/{owner_repo}/pulls/{number}",
                "--jq",
                (
                    "{title: .title, number: .number, url: .html_url, "
                    "createdAt: .created_at, state: .state, isDraft: .draft, "
                    "repository: {name: .base.repo.name, "
                    "nameWithOwner: .base.repo.full_name}}"
                ),
            ]
        )
        if rc != 0:
            raise GhApiError(
                f"Failed to fetch PR {owner_repo}#{number}: {stderr.strip()}"
            )
        try:
            return json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as exc:
            raise GhApiError(f"Failed to parse PR response: {exc}") from exc

    async def list_my_prs(self) -> list[dict]:
        """List open PRs authored by the current user across all repos."""
        if not self.is_available():
            return []
        rc, stdout, stderr = await self._run(
            [
                "search",
                "prs",
                "--author",
                "@me",
                "--state",
                "open",
                "--json",
                "title,repository,url,createdAt,number,state,isDraft",
                "--limit",
                "50",
            ]
        )
        if rc != 0:
            log.error("gh search prs failed: %s", stderr.strip())
            raise GhApiError(f"gh search prs failed: {stderr.strip()}")
        try:
            return json.loads(stdout) if stdout.strip() else []
        except json.JSONDecodeError as exc:
            log.error("Failed to parse gh output: %s", stdout[:200])
            raise GhApiError(f"Failed to parse gh output: {exc}") from exc

    async def list_my_review_prs(self) -> list[dict]:
        """List open PRs where the current user is a requested reviewer."""
        if not self.is_available():
            return []
        rc, stdout, stderr = await self._run(
            [
                "search",
                "prs",
                "--review-requested",
                "@me",
                "--state",
                "open",
                "--json",
                "title,repository,url,createdAt,number,state,isDraft",
                "--limit",
                "50",
            ]
        )
        if rc != 0:
            log.error("gh search review prs failed: %s", stderr.strip())
            raise GhApiError(f"gh search review prs failed: {stderr.strip()}")
        try:
            return json.loads(stdout) if stdout.strip() else []
        except json.JSONDecodeError as exc:
            log.error("Failed to parse gh review output: %s", stdout[:200])
            raise GhApiError(f"Failed to parse gh output: {exc}") from exc

    async def get_reviews(self, owner_repo: str, number: int) -> list[dict]:
        """Get reviews for a specific PR."""
        rc, stdout, stderr = await self._run(
            [
                "api",
                f"repos/{owner_repo}/pulls/{number}/reviews",
                "--jq",
                "[.[] | {state: .state, user: .user.login}]",
            ]
        )
        if rc != 0:
            log.debug(
                "Failed to get reviews for %s#%d: %s",
                owner_repo,
                number,
                stderr.strip(),
            )
            return []
        try:
            return json.loads(stdout) if stdout.strip() else []
        except json.JSONDecodeError:
            return []

    async def get_comments(self, owner_repo: str, number: int) -> dict:
        """Get review thread stats and active thread content via GraphQL."""
        owner, repo = (
            owner_repo.split("/", 1) if "/" in owner_repo else (owner_repo, "")
        )
        query = (
            '{ repository(owner: "'
            + owner
            + '", name: "'
            + repo
            + '") { pullRequest(number: '
            + str(number)
            + ") { "
            "reviewThreads(first: 100) { nodes { isResolved path line "
            "comments(first: 50) { nodes { author { login } body "
            "createdAt updatedAt } } } } } } }"
        )
        rc, stdout, stderr = await self._run(["api", "graphql", "-f", f"query={query}"])
        if rc != 0:
            log.debug(
                "GraphQL reviewThreads failed for %s#%d: %s",
                owner_repo,
                number,
                stderr.strip(),
            )
            return {
                "commentsActive": None,
                "commentsTotal": None,
                "lastCommentDate": None,
                "threads": [],
            }
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return {
                "commentsActive": None,
                "commentsTotal": None,
                "lastCommentDate": None,
                "threads": [],
            }

        threads = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )

        total = len(threads)
        active = sum(1 for t in threads if not t.get("isResolved"))

        last_date = None
        for t in threads:
            for c in t.get("comments", {}).get("nodes", []):
                d = c.get("updatedAt") or c.get("createdAt")
                if d and (last_date is None or d > last_date):
                    last_date = d

        # Extract active thread content for peek view
        active_threads = []
        for t in threads:
            if t.get("isResolved"):
                continue
            comments = []
            for c in t.get("comments", {}).get("nodes", []):
                comments.append({
                    "author": (c.get("author") or {}).get("login", "unknown"),
                    "text": c.get("body", ""),
                    "date": c.get("updatedAt") or c.get("createdAt", ""),
                })
            if comments:
                active_threads.append({
                    "id": None,
                    "status": "active",
                    "filePath": t.get("path"),
                    "line": t.get("line"),
                    "comments": comments,
                })

        return {
            "commentsActive": active,
            "commentsTotal": total,
            "lastCommentDate": last_date,
            "threads": active_threads,
        }

    async def get_pr_detail(self, owner_repo: str, number: int) -> dict:
        """Get full PR details (branches, updated date, head SHA)."""
        rc, stdout, stderr = await self._run(
            [
                "api",
                f"repos/{owner_repo}/pulls/{number}",
                "--jq",
                "{head: .head.ref, base: .base.ref, updated_at: .updated_at, user: .user.login, head_sha: .head.sha, mergeable_state: .mergeable_state, body: .body}",
            ]
        )
        if rc != 0:
            return {}
        try:
            return json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return {}

    async def get_check_runs(self, owner_repo: str, sha: str) -> dict:
        """Get check run stats for a commit SHA."""
        rc, stdout, stderr = await self._run(
            [
                "api",
                f"repos/{owner_repo}/commits/{sha}/check-runs",
                "--jq",
                "[.check_runs[] | {name: .name, status: .status, conclusion: .conclusion}]",
            ]
        )
        if rc != 0:
            return {
                "checksPass": 0,
                "checksTotal": 0,
                "requiredPass": 0,
                "requiredTotal": 0,
                "optionalPass": 0,
                "optionalTotal": 0,
                "checks": [],
            }
        try:
            runs = json.loads(stdout) if stdout.strip() else []
        except json.JSONDecodeError:
            runs = []

        if not runs:
            return {
                "checksPass": 0,
                "checksTotal": 0,
                "requiredPass": 0,
                "requiredTotal": 0,
                "optionalPass": 0,
                "optionalTotal": 0,
                "checks": [],
            }

        checks = []
        passed = 0
        for r in runs:
            conclusion = r.get("conclusion") or r.get("status", "pending")
            is_success = conclusion in ("success", "skipped", "neutral")
            if is_success:
                passed += 1
            status = "approved" if is_success else conclusion
            checks.append({"name": r["name"], "status": status, "isBlocking": True})

        return {
            "checksPass": passed,
            "checksTotal": len(runs),
            "requiredPass": passed,
            "requiredTotal": len(runs),
            "optionalPass": 0,
            "optionalTotal": 0,
            "checks": checks,
        }

    async def enrich_pr(self, gh_pr: dict, role: str = "author") -> dict:
        """Convert a GitHub PR dict to normalized dashboard entry."""
        repo = gh_pr.get("repository", {})
        owner_repo = repo.get("nameWithOwner", "")
        repo_name = repo.get(
            "name", owner_repo.split("/")[-1] if "/" in owner_repo else owner_repo
        )
        number = gh_pr.get("number", 0)
        url = gh_pr.get("url", "")

        # Fetch reviews, comments, and PR detail in parallel
        raw_reviews, comment_stats, detail, username = await asyncio.gather(
            self.get_reviews(owner_repo, number),
            self.get_comments(owner_repo, number),
            self.get_pr_detail(owner_repo, number),
            self.get_username(),
        )

        # Fetch check runs (needs head_sha from detail)
        head_sha = detail.get("head_sha", "")
        if head_sha:
            check_stats = await self.get_check_runs(owner_repo, head_sha)
        else:
            check_stats = {
                "checksPass": 0,
                "checksTotal": 0,
                "requiredPass": 0,
                "requiredTotal": 0,
                "optionalPass": 0,
                "optionalTotal": 0,
                "checks": [],
            }

        # Deduplicate reviews: keep latest per user
        latest_by_user: dict[str, dict] = {}
        for r in raw_reviews:
            user = r.get("user", "")
            latest_by_user[user] = r
        reviews = [
            {"name": r["user"], "vote": REVIEW_STATE_MAP.get(r["state"], "NoVote")}
            for r in latest_by_user.values()
        ]

        # Extract current user's vote
        my_vote = "NoVote"
        if username:
            for r in latest_by_user.values():
                if r.get("user", "").lower() == username.lower():
                    my_vote = REVIEW_STATE_MAP.get(r["state"], "NoVote")
                    break

        status = GH_STATUS_MAP.get(gh_pr.get("state", "open"), "active")
        author = detail.get("user", "")

        entry = {
            "source": "github",
            "id": number,
            "role": role,
            "myVote": my_vote,
            "repoName": repo_name,
            "repoUrl": f"https://github.com/{owner_repo}",
            "project": owner_repo.split("/")[0] if "/" in owner_repo else "",
            "title": gh_pr.get("title", ""),
            "url": url,
            "author": author,
            "authorEmail": "",
            "isDraft": bool(gh_pr.get("isDraft")),
            "isMine": (author.lower() == username.lower()) if username else True,
            "status": status,
            "reviews": reviews,
            "sourceBranch": detail.get("head", ""),
            "targetBranch": detail.get("base", ""),
            "creationDate": gh_pr.get("createdAt"),
            "lastUpdated": detail.get("updated_at") or gh_pr.get("createdAt"),
            "mergeStatus": detail.get("mergeable_state", ""),
            "description": detail.get("body", ""),
            "lastLoaded": datetime.now(UTC).isoformat(),
            **check_stats,
            **comment_stats,
        }

        log.info(
            "Enriched GitHub PR %s#%d: %s", owner_repo, number, entry["title"][:50]
        )
        return entry
