"""Async Azure DevOps REST client for PR Dashboard."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import httpx
from azure.identity import AzureCliCredential
from .logger import get_logger

log = get_logger()

ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"
API_VERSION = "7.0"
VSSPS_BASE = "https://app.vssps.visualstudio.com"

DEFAULT_ORG = os.environ.get("ADO_ORG", "msazure")
DEFAULT_PROJECT = os.environ.get("ADO_PROJECT", "One")

VOTE_LABELS = {
    10: "Approved",
    5: "ApprovedWithSuggestions",
    0: "NoVote",
    -5: "WaitingForAuthor",
    -10: "Rejected",
}


class AdoAuthError(Exception):
    """Raised when ADO authentication fails."""


class AdoApiError(Exception):
    """Raised when an ADO API call fails."""


class AdoClient:
    """Async Azure DevOps REST client."""

    def __init__(
        self,
        org: str = DEFAULT_ORG,
        project: str = DEFAULT_PROJECT,
        token: str | None = None,
    ) -> None:
        self.org = org
        self.project = project
        self.base_url = f"https://dev.azure.com/{org}"
        self._credential = AzureCliCredential()
        self._pre_token = token
        self._http: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._user_id: str | None = None
        self._user_email: str | None = None

    async def __aenter__(self) -> AdoClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            if self._http is None or self._http.is_closed:
                if self._pre_token:
                    bearer = self._pre_token
                else:
                    tok = await asyncio.to_thread(self._credential.get_token, ADO_SCOPE)
                    bearer = tok.token
                self._http = httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {bearer}"},
                    timeout=30.0,
                )
                log.debug("HTTP client created with Bearer token")
            return self._http

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        client = await self._get_client()
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.ConnectError as exc:
            log.error("Connection error: %s", exc)
            raise AdoApiError(f"Connection failed: {exc}") from exc
        except httpx.TimeoutException as exc:
            log.error("Timeout: %s %s", method, url)
            raise AdoApiError(f"Request timed out: {exc}") from exc

        if resp.status_code == 401:
            log.warning("Auth expired, refreshing token and retrying")
            async with self._client_lock:
                await self.close()
            client = await self._get_client()
            resp = await client.request(method, url, **kwargs)
            if resp.status_code == 401:
                raise AdoAuthError("Authentication failed — run 'az login'")

        if resp.status_code >= 400:
            log.error(
                "API error %d: %s %s → %s",
                resp.status_code,
                method,
                url,
                resp.text[:200],
            )
            raise AdoApiError(f"API returned {resp.status_code}: {resp.text[:200]}")

        return resp.json()

    async def _get(
        self, path: str, *, api_version: str = API_VERSION, **params
    ) -> dict:
        params["api-version"] = api_version
        url = f"{self.base_url}/{path.lstrip('/')}"
        log.debug("GET %s", url)
        return await self._request("GET", url, params=params)

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    # ── Identity ──────────────────────────────────────────────────────────

    async def get_current_user(self) -> tuple[str, str]:
        """Return (user_id, user_email) for the authenticated user."""
        if self._user_id and self._user_email:
            return self._user_id, self._user_email
        data = await self._get("_apis/connectionData", api_version="7.0-preview.1")
        self._user_id = data["authenticatedUser"]["id"]
        self._user_email = data["authenticatedUser"]["properties"]["Account"]["$value"]
        log.info("Authenticated as %s", self._user_email)
        return self._user_id, self._user_email

    async def get_az_username(self) -> str | None:
        """Return the email of the authenticated Azure user, or None."""
        try:
            user_id, email = await self.get_current_user()
            return email
        except Exception:
            return None

    async def discover_orgs(self) -> list[str]:
        """Discover all ADO organizations the user belongs to via VSSPS."""
        # Get global user ID from VSSPS
        resp = await self._request(
            "GET",
            f"{VSSPS_BASE}/_apis/connectionData",
            params={"api-version": "7.1-preview"},
        )
        global_id = resp["authenticatedUser"]["id"]

        # Discover orgs
        resp = await self._request(
            "GET",
            f"{VSSPS_BASE}/_apis/accounts",
            params={"memberId": global_id, "api-version": "7.1-preview"},
        )
        orgs = [a["accountName"] for a in resp.get("value", [])]
        log.info("Discovered %d ADO orgs: %s", len(orgs), ", ".join(orgs))
        return orgs

    # ── Pull Requests ─────────────────────────────────────────────────────

    async def get_pr(self, pr_id: int) -> dict:
        """Fetch a single PR by ID (org-level, no project needed)."""
        data = await self._get(f"_apis/git/pullrequests/{pr_id}")
        log.debug("Fetched PR #%d: %s", pr_id, data.get("title", "?")[:50])
        return data

    async def list_my_prs(self, status: str = "active", top: int = 200) -> list[dict]:
        """List PRs authored by the current user (org-level, all projects)."""
        user_id, _ = await self.get_current_user()
        data = await self._get(
            "_apis/git/pullrequests",
            **{
                "searchCriteria.creatorId": user_id,
                "searchCriteria.status": status,
                "$top": str(top),
            },
        )
        prs = data.get("value", [])
        log.info("[%s] Listed %d authored PRs (status=%s)", self.org, len(prs), status)
        return prs

    async def list_my_review_prs(
        self, status: str = "active", top: int = 200
    ) -> list[dict]:
        """List PRs where the current user is a reviewer (org-level)."""
        user_id, _ = await self.get_current_user()
        data = await self._get(
            "_apis/git/pullrequests",
            **{
                "searchCriteria.reviewerId": user_id,
                "searchCriteria.status": status,
                "$top": str(top),
            },
        )
        prs = data.get("value", [])
        log.info("[%s] Listed %d review PRs (status=%s)", self.org, len(prs), status)
        return prs

    async def get_policy_evaluations(
        self, pr_id: int, project_id: str, project_name: str | None = None
    ) -> dict:
        """Get policy/check evaluations for a PR. Returns counts + individual checks."""
        project = project_name or self.project
        artifact_id = f"vstfs:///CodeReview/CodeReviewId/{project_id}/{pr_id}"
        data = await self._get(
            f"{project}/_apis/policy/evaluations",
            api_version="7.0-preview.1",
            artifactId=artifact_id,
        )
        evaluations = data.get("value", [])

        # Filter to evaluated policies (non-null, non-notApplicable)
        evaluated = [
            e for e in evaluations if e.get("status") and e["status"] != "notApplicable"
        ]

        # Process policies(not checks) — exclude from counts and detail list
        _process_policies = {"Minimum number of reviewers", "Require a merge strategy"}

        # Individual check details — dedup by (name, isBlocking), keep worst status
        _status_priority = {"rejected": 0, "running": 1, "queued": 2, "approved": 3}
        seen: dict[tuple[str, bool], dict] = {}
        for e in evaluated:
            cfg = e.get("configuration", {})
            type_name = (cfg.get("type") or {}).get("displayName", "Unknown check")

            # Skip process policies (shown separately by ADO, not as checks)
            if type_name in _process_policies:
                continue

            # Use statusName for Status-type checks (e.g., "ComponentGovernance")
            settings = cfg.get("settings", {}) or {}
            if type_name == "Status" and settings.get("statusName"):
                name = settings["statusName"]
            elif settings.get("displayName"):
                name = settings["displayName"]
            else:
                name = type_name

            blocking = bool(cfg.get("isBlocking"))
            key = (name, blocking)
            status = e["status"]
            if key not in seen or _status_priority.get(
                status, 1
            ) < _status_priority.get(seen[key]["status"], 1):
                seen[key] = {"name": name, "status": status, "isBlocking": blocking}

        # Sort: required (blocking) first, then optional
        checks = sorted(seen.values(), key=lambda c: (not c["isBlocking"], c["name"]))

        # Recount after dedup
        deduped_required = [c for c in checks if c["isBlocking"]]
        deduped_optional = [c for c in checks if not c["isBlocking"]]

        result = {
            "checksPass": sum(1 for c in checks if c["status"] == "approved"),
            "checksTotal": len(checks),
            "requiredPass": sum(
                1 for c in deduped_required if c["status"] == "approved"
            ),
            "requiredTotal": len(deduped_required),
            "optionalPass": sum(
                1 for c in deduped_optional if c["status"] == "approved"
            ),
            "optionalTotal": len(deduped_optional),
            "checks": checks,
        }
        log.debug(
            "PR #%d policies: %d/%d required, %d/%d optional",
            pr_id,
            result["requiredPass"],
            result["requiredTotal"],
            result["optionalPass"],
            result["optionalTotal"],
        )
        return result

    async def get_threads(
        self, pr_id: int, repo_id: str, project_name: str | None = None
    ) -> dict:
        """Get comment thread stats and active thread content for a PR."""
        project = project_name or self.project
        data = await self._get(
            f"{project}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}/threads",
        )
        threads = data.get("value", [])

        # Only include actual comment threads (no CodeReviewThreadType, not deleted)
        user_threads = []
        for t in threads:
            if t.get("isDeleted"):
                continue
            thread_type = (
                (t.get("properties") or {}).get("CodeReviewThreadType") or {}
            ).get("$value", "")
            if not thread_type:
                user_threads.append(t)

        active = sum(
            1 for t in user_threads if t.get("status") in ("active", "pending")
        )
        last_date = None
        for t in user_threads:
            updated = t.get("lastUpdatedDate")
            if updated and (last_date is None or updated > last_date):
                last_date = updated

        # Extract active thread content for peek view
        active_threads = []
        for t in user_threads:
            if t.get("status") not in ("active", "pending"):
                continue
            comments = []
            for c in t.get("comments", []):
                if c.get("isDeleted"):
                    continue
                comments.append(
                    {
                        "author": c.get("author", {}).get("displayName", "Unknown"),
                        "text": c.get("content", ""),
                        "date": c.get("publishedDate") or c.get("lastUpdatedDate", ""),
                    }
                )
            if comments:
                ctx = t.get("threadContext") or {}
                active_threads.append(
                    {
                        "id": t.get("id"),
                        "status": t.get("status", "active"),
                        "filePath": ctx.get("filePath"),
                        "line": (ctx.get("rightFileStart") or {}).get("line"),
                        "comments": comments,
                    }
                )

        result = {
            "commentsActive": active,
            "commentsTotal": len(user_threads),
            "lastCommentDate": last_date,
            "threads": active_threads,
        }
        log.debug(
            "PR #%d threads: %d active / %d total", pr_id, active, len(user_threads)
        )
        return result

    async def get_work_items(
        self, pr_id: int, repo_id: str, project_name: str | None = None
    ) -> list[dict]:
        """Get linked work items for a PR. Returns list of {id, title, url}."""
        project = project_name or self.project
        data = await self._get(
            f"{project}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}/workitems",
        )
        refs = data.get("value", [])
        if not refs:
            return []

        # Fetch work item details (titles) in batch
        ids = [str(r.get("id", "")) for r in refs if r.get("id")]
        if not ids:
            return []
        wi_data = await self._get(
            "_apis/wit/workitems",
            ids=",".join(ids),
            fields="System.Title,System.WorkItemType",
        )
        items = wi_data.get("value", [])
        result = []
        for wi in items:
            fields = wi.get("fields", {})
            wi_id = wi.get("id", 0)
            result.append(
                {
                    "id": wi_id,
                    "title": fields.get("System.Title", ""),
                    "type": fields.get("System.WorkItemType", ""),
                    "url": f"{self.base_url}/{project}/_workitems/edit/{wi_id}",
                }
            )
        return result

    # ── High-level: enrich a PR ───────────────────────────────────────────

    async def enrich_pr(self, ado_pr: dict, role: str = "author") -> dict:
        """Convert raw ADO PR to a dashboard entry with policies + threads."""
        _, user_email = await self.get_current_user()
        pr_id = ado_pr["pullRequestId"]
        repo_id = ado_pr["repository"]["id"]
        project_name = (ado_pr.get("repository", {}).get("project") or {}).get(
            "name", self.project
        )
        project_id = (ado_pr.get("repository", {}).get("project") or {}).get("id", "")
        repo_name = ado_pr["repository"]["name"]
        author_email = ado_pr.get("createdBy", {}).get("uniqueName", "")

        # Build URLs
        repo_url = f"{self.base_url}/{project_name}/_git/{repo_name}"
        pr_url = f"{repo_url}/pullrequest/{pr_id}"

        # Reviews + extract current user's vote
        reviews = []
        my_vote = "NoVote"
        is_required_reviewer = False
        current_user_name = ""
        for r in ado_pr.get("reviewers", []):
            vote = int(r.get("vote", 0))
            vote_label = VOTE_LABELS.get(vote, "Unknown")
            required = bool(r.get("isRequired", False))
            reviews.append(
                {
                    "name": r.get("displayName", ""),
                    "vote": vote_label,
                    "isRequired": required,
                }
            )
            reviewer_email = r.get("uniqueName", "")
            if reviewer_email.lower() == user_email.lower():
                my_vote = vote_label
                is_required_reviewer = required
                current_user_name = r.get("displayName", "")

        # lastUpdated priority: closedDate > lastMergeSourceCommit.committer.date > creationDate
        last_updated = (
            ado_pr.get("closedDate")
            or (ado_pr.get("lastMergeSourceCommit", {}) or {})
            .get("committer", {})
            .get("date")
            or ado_pr.get("creationDate")
        )

        entry = {
            "source": f"ado/{self.org}",
            "id": pr_id,
            "role": role,
            "myVote": my_vote,
            "isRequiredReviewer": is_required_reviewer,
            "repoName": repo_name,
            "repoUrl": repo_url,
            "project": project_name,
            "title": ado_pr.get("title", ""),
            "url": pr_url,
            "author": ado_pr.get("createdBy", {}).get("displayName", ""),
            "authorEmail": author_email,
            "isDraft": bool(ado_pr.get("isDraft")),
            "autoCompleteSetBy": (
                ado_pr.get("autoCompleteSetBy", {}).get("displayName")
                if ado_pr.get("autoCompleteSetBy")
                else None
            ),
            "isMine": author_email.lower() == user_email.lower(),
            "status": ado_pr.get("status", ""),
            "mergeStatus": ado_pr.get("mergeStatus", ""),
            "description": ado_pr.get("description", ""),
            "reviews": reviews,
            "sourceBranch": (ado_pr.get("sourceRefName", "") or "").removeprefix(
                "refs/heads/"
            ),
            "targetBranch": (ado_pr.get("targetRefName", "") or "").removeprefix(
                "refs/heads/"
            ),
            "creationDate": ado_pr.get("creationDate"),
            "lastUpdated": last_updated,
            "lastLoaded": datetime.now(UTC).isoformat(),
            "currentUserName": current_user_name or ado_pr.get("createdBy", {}).get("displayName", ""),
        }

        # Enrich with policies, threads, and work items (parallel)
        try:
            policies, threads, work_items = await asyncio.gather(
                self.get_policy_evaluations(pr_id, project_id, project_name),
                self.get_threads(pr_id, repo_id, project_name),
                self.get_work_items(pr_id, repo_id, project_name),
            )
            entry.update(policies)
            entry.update(threads)
            entry["workItems"] = work_items
        except AdoApiError as exc:
            log.warning("Failed to enrich PR #%d: %s", pr_id, exc)
            entry.update(
                {
                    "checksPass": None,
                    "checksTotal": None,
                    "requiredPass": None,
                    "requiredTotal": None,
                    "optionalPass": None,
                    "optionalTotal": None,
                    "checks": [],
                    "commentsActive": None,
                    "commentsTotal": None,
                    "lastCommentDate": None,
                    "workItems": [],
                }
            )

        log.info("Enriched PR #%d: %s", pr_id, entry["title"][:50])
        return entry
