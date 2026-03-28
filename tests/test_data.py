"""Tests for data.py — pure logic, no network."""

import re

import pytest

from pr_dashboard.data import (
    PrDataStore,
    _pr_key,
    _repo_entry,
    _repo_in_list,
    _remove_repo_from_list,
)
from pr_dashboard.db import Database


@pytest.fixture()
def store(tmp_path):
    """Create a PrDataStore backed by a temporary SQLite database."""
    return PrDataStore(db_path=tmp_path / "test.db")


@pytest.fixture()
def db(tmp_path):
    """Create a bare Database for low-level tests."""
    return Database(db_path=tmp_path / "test.db")


# ── _pr_key ──────────────────────────────────────────────────────


class TestPrKey:
    def test_composite_key(self):
        pr = {"source": "ado/msazure", "id": 42}
        assert _pr_key(pr) == ("ado/msazure", 42)

    def test_missing_fields(self):
        assert _pr_key({}) == ("", 0)


# ── Repo helpers ────────────────────────────────────────────────


class TestRepoHelpers:
    def test_repo_entry(self):
        assert _repo_entry("ado/msazure", "MyRepo") == {
            "source": "ado/msazure",
            "repo": "MyRepo",
        }

    def test_repo_in_list_found(self):
        lst = [
            {"source": "ado/msazure", "repo": "A"},
            {"source": "github", "repo": "B"},
        ]
        assert _repo_in_list("ado/msazure", "A", lst) is True

    def test_repo_in_list_not_found(self):
        lst = [{"source": "ado/msazure", "repo": "A"}]
        assert _repo_in_list("ado/msazure", "B", lst) is False

    def test_remove_repo_from_list(self):
        lst = [
            {"source": "ado/msazure", "repo": "A"},
            {"source": "github", "repo": "B"},
        ]
        assert _remove_repo_from_list("ado/msazure", "A", lst) is True
        assert len(lst) == 1
        assert lst[0]["repo"] == "B"

    def test_remove_repo_not_found(self):
        lst = [{"source": "ado/msazure", "repo": "A"}]
        assert _remove_repo_from_list("ado/msazure", "X", lst) is False
        assert len(lst) == 1


# ── URL regex patterns from add_pr_by_url ────────────────────────


ADO_URL_RE = re.compile(
    r"https://dev\.azure\.com/([^/]+)/[^/]+/_git/[^/]+/pullrequest/(\d+)"
)
GH_URL_RE = re.compile(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)")


class TestAdoUrlRegex:
    def test_valid_ado_url(self):
        url = "https://dev.azure.com/msazure/MyProj/_git/MyRepo/pullrequest/12345"
        m = ADO_URL_RE.match(url)
        assert m is not None
        assert m.group(1) == "msazure"
        assert m.group(2) == "12345"

    def test_ado_url_different_org(self):
        url = "https://dev.azure.com/contoso/Proj/_git/Repo/pullrequest/99"
        m = ADO_URL_RE.match(url)
        assert m is not None
        assert m.group(1) == "contoso"

    def test_invalid_url_no_match(self):
        assert ADO_URL_RE.match("https://github.com/foo/bar/pull/1") is None


class TestGhUrlRegex:
    def test_valid_gh_url(self):
        url = "https://github.com/owner/repo/pull/42"
        m = GH_URL_RE.match(url)
        assert m is not None
        assert m.group(1) == "owner/repo"
        assert m.group(2) == "42"

    def test_gh_url_no_match_on_ado(self):
        url = "https://dev.azure.com/org/proj/_git/repo/pullrequest/1"
        assert GH_URL_RE.match(url) is None

    def test_invalid_url(self):
        assert GH_URL_RE.match("https://example.com/foo") is None


# ── Database: upsert pin preservation ────────────────────────────


class TestUpsertPinPreservation:
    def test_pinned_state_preserved_on_upsert(self, db):
        db.upsert_pr({"source": "ado/msazure", "id": 42, "title": "Old", "pinned": True})
        db.upsert_pr({"source": "ado/msazure", "id": 42, "title": "New"})
        pr = db.get_pr("ado/msazure", 42)
        assert pr["title"] == "New"
        assert pr["pinned"] is True

    def test_unpinned_pr_stays_unpinned(self, db):
        db.upsert_pr({"source": "ado/msazure", "id": 42, "title": "Old", "pinned": False})
        db.upsert_pr({"source": "ado/msazure", "id": 42, "title": "New"})
        pr = db.get_pr("ado/msazure", 42)
        assert pr["title"] == "New"
        assert not pr.get("pinned")

    def test_new_pr_has_no_pinned(self, db):
        db.upsert_pr({"source": "ado/msazure", "id": 42, "title": "New"})
        pr = db.get_pr("ado/msazure", 42)
        assert not pr.get("pinned")


# ── toggle_pin ───────────────────────────────────────────────────


class TestTogglePin:
    def test_pin_and_unpin(self, store):
        store.db.upsert_pr({"source": "ado/msazure", "id": 42, "title": "Test"})

        result = store.toggle_pin(42, source="ado/msazure")
        assert result is True

        prs = store.load_prs()
        assert prs[0]["pinned"] is True

        result = store.toggle_pin(42, source="ado/msazure")
        assert result is False

        prs = store.load_prs()
        assert prs[0]["pinned"] is False

    def test_toggle_nonexistent_pr(self, store):
        result = store.toggle_pin(999, source="ado/msazure")
        assert result is None


# ── Source management ────────────────────────────────────────────


class TestSourceManagement:
    def _make_store(self, tmp_path, sources=None):
        s = PrDataStore(db_path=tmp_path / "test.db")
        if sources:
            for lt in ("discovered", "include", "exclude"):
                names = sources.get(lt, [])
                if names:
                    s.db.set_sources(lt, names)
        return s

    def test_get_active_sources(self, tmp_path):
        store = self._make_store(
            tmp_path,
            {
                "discovered": ["ado/msazure", "ado/contoso"],
                "include": ["ado/custom"],
                "exclude": ["ado/contoso"],
            },
        )
        active = store.get_active_sources()
        assert "ado/msazure" in active
        assert "ado/custom" in active
        assert "ado/contoso" not in active

    def test_get_sources_for_manage(self, tmp_path):
        store = self._make_store(
            tmp_path,
            {
                "discovered": ["ado/msazure"],
                "include": ["ado/custom"],
                "exclude": ["ado/contoso"],
            },
        )
        items = store.get_sources_for_manage()
        sources = {s for s, _ in items}
        assert sources == {"ado/msazure", "ado/custom", "ado/contoso"}

    def test_include_source(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.include_source("ado/neworg") is True
        assert store.include_source("ado/neworg") is False  # duplicate
        assert "ado/neworg" in store.get_active_sources()

    def test_include_source_removes_from_exclude(self, tmp_path):
        store = self._make_store(
            tmp_path,
            {"discovered": ["ado/msazure"], "include": [], "exclude": ["ado/msazure"]},
        )
        assert "ado/msazure" not in store.get_active_sources()
        store.include_source("ado/msazure")
        assert "ado/msazure" in store.get_active_sources()

    def test_toggle_source_discovered_to_excluded(self, tmp_path):
        store = self._make_store(
            tmp_path,
            {"discovered": ["ado/msazure"], "include": [], "exclude": []},
        )
        result = store.toggle_source("ado/msazure")
        assert result == "excluded"
        assert "ado/msazure" not in store.get_active_sources()

    def test_toggle_source_excluded_to_active(self, tmp_path):
        store = self._make_store(
            tmp_path,
            {"discovered": ["ado/msazure"], "include": [], "exclude": ["ado/msazure"]},
        )
        result = store.toggle_source("ado/msazure")
        assert result == "active"
        assert "ado/msazure" in store.get_active_sources()

    def test_toggle_source_include_only_deletes(self, tmp_path):
        store = self._make_store(
            tmp_path,
            {"discovered": [], "include": ["ado/custom"], "exclude": []},
        )
        result = store.toggle_source("ado/custom")
        assert result is None  # deleted
        assert store.get_sources_for_manage() == []


# ── Repo management ──────────────────────────────────────────────


class TestRepoManagement:
    def _make_store(self, tmp_path, repos=None, prs=None):
        s = PrDataStore(db_path=tmp_path / "test.db")
        if repos:
            for lt in ("discovered", "include", "exclude"):
                repo_list = repos.get(lt, [])
                if repo_list:
                    s.db.set_repos(lt, repo_list)
        if prs:
            s.db.upsert_prs_batch(prs)
        return s

    def test_include_repo(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.include_repo("ado/msazure", "MyRepo") is True
        assert store.include_repo("ado/msazure", "MyRepo") is False
        items = store.get_repos_for_manage()
        assert len(items) == 1
        assert items[0][0] == {"source": "ado/msazure", "repo": "MyRepo"}
        assert items[0][1] is True

    def test_toggle_repo_discovered_to_excluded(self, tmp_path):
        store = self._make_store(
            tmp_path,
            {
                "discovered": [{"source": "ado/msazure", "repo": "Repo1"}],
                "include": [],
                "exclude": [],
            },
        )
        result = store.toggle_repo("ado/msazure", "Repo1")
        assert result == "excluded"
        items = store.get_repos_for_manage()
        assert items[0][1] is False  # excluded

    def test_toggle_repo_excluded_to_active(self, tmp_path):
        store = self._make_store(
            tmp_path,
            {
                "discovered": [{"source": "ado/msazure", "repo": "Repo1"}],
                "include": [],
                "exclude": [{"source": "ado/msazure", "repo": "Repo1"}],
            },
        )
        result = store.toggle_repo("ado/msazure", "Repo1")
        assert result == "active"
        items = store.get_repos_for_manage()
        assert items[0][1] is True

    def test_toggle_repo_include_only_deletes(self, tmp_path):
        store = self._make_store(
            tmp_path,
            {
                "discovered": [],
                "include": [{"source": "ado/custom", "repo": "Special"}],
                "exclude": [],
            },
        )
        result = store.toggle_repo("ado/custom", "Special")
        assert result is None
        assert store.get_repos_for_manage() == []

    def test_toggle_repo_discovered_and_included_goes_to_excluded(self, tmp_path):
        """Repo in both discovered and include → toggle to excluded + cleaned from include."""
        store = self._make_store(
            tmp_path,
            {
                "discovered": [{"source": "ado/msazure", "repo": "Shared"}],
                "include": [{"source": "ado/msazure", "repo": "Shared"}],
                "exclude": [],
            },
        )
        result = store.toggle_repo("ado/msazure", "Shared")
        assert result == "excluded"
        items = store.get_repos_for_manage()
        assert len(items) == 1
        assert items[0][1] is False  # excluded, not deleted
        # Verify include list was cleaned
        include_repos = store.db.get_repos("include")
        assert include_repos == []

    def test_exclude_repo_removes_reviewer_prs(self, tmp_path):
        store = self._make_store(
            tmp_path,
            repos={
                "discovered": [{"source": "ado/msazure", "repo": "Noisy"}],
                "include": [],
                "exclude": [],
            },
            prs=[
                {"source": "ado/msazure", "id": 1, "role": "reviewer", "repoName": "Noisy"},
                {"source": "ado/msazure", "id": 2, "role": "author", "repoName": "Noisy"},
                {"source": "ado/msazure", "id": 3, "role": "reviewer", "repoName": "Other"},
            ],
        )
        store.toggle_repo("ado/msazure", "Noisy")
        prs = store.load_prs()
        assert len(prs) == 2  # reviewer PR from Noisy removed, author kept
        assert {p["id"] for p in prs} == {2, 3}


# ── Database: migration from legacy JSON ─────────────────────────


class TestLegacyMigration:
    def test_migrate_from_json(self, tmp_path):
        import json

        json_file = tmp_path / "prs.json"
        json_data = {
            "version": 3,
            "currentUser": "test@example.com",
            "sources": {
                "discovered": ["ado/msazure"],
                "include": ["ado/custom"],
                "exclude": ["ado/contoso"],
            },
            "repos": {
                "discovered": [{"source": "ado/msazure", "repo": "Repo1"}],
                "include": [],
                "exclude": [{"source": "ado/msazure", "repo": "Noisy"}],
            },
            "prs": [
                {"source": "ado/msazure", "id": 42, "title": "Test PR", "status": "active"},
            ],
        }
        json_file.write_text(json.dumps(json_data), encoding="utf-8")

        db = Database.from_legacy_json(json_file, tmp_path / "test.db")

        assert db.get_meta("currentUser") == "test@example.com"
        assert db.get_sources("discovered") == ["ado/msazure"]
        assert db.get_sources("include") == ["ado/custom"]
        assert db.get_sources("exclude") == ["ado/contoso"]
        assert len(db.get_repos("discovered")) == 1
        assert len(db.get_repos("exclude")) == 1
        prs = db.load_prs()
        assert len(prs) == 1
        assert prs[0]["title"] == "Test PR"
        # JSON file should be renamed
        assert not json_file.exists()
        assert (tmp_path / "prs.json.bak").exists()

    def test_skip_wrong_version(self, tmp_path):
        import json

        json_file = tmp_path / "prs.json"
        json_file.write_text(json.dumps({"version": 2, "prs": [{"id": 1}]}))
        db = Database.from_legacy_json(json_file, tmp_path / "test.db")
        assert db.load_prs() == []


# ── Database: clean, remove ──────────────────────────────────────


class TestDatabaseOps:
    def test_clean_non_active(self, db):
        db.upsert_pr({"source": "ado/msazure", "id": 1, "status": "active"})
        db.upsert_pr({"source": "ado/msazure", "id": 2, "status": "completed"})
        db.upsert_pr({"source": "ado/msazure", "id": 3, "status": "abandoned"})
        removed = db.clean_non_active()
        assert removed == 2
        prs = db.load_prs()
        assert len(prs) == 1
        assert prs[0]["id"] == 1

    def test_remove_pr_by_source(self, db):
        db.upsert_pr({"source": "ado/msazure", "id": 42})
        assert db.remove_pr(42, "ado/msazure") is True
        assert db.load_prs() == []

    def test_remove_pr_not_found(self, db):
        assert db.remove_pr(999) is False

    def test_batch_upsert(self, db):
        prs = [
            {"source": "ado/msazure", "id": i, "title": f"PR {i}"}
            for i in range(10)
        ]
        db.upsert_prs_batch(prs)
        assert db.pr_count() == 10

    def test_batch_upsert_preserves_pins(self, db):
        db.upsert_pr({"source": "ado/msazure", "id": 1, "title": "Old", "pinned": True})
        db.upsert_prs_batch([
            {"source": "ado/msazure", "id": 1, "title": "New"},
            {"source": "ado/msazure", "id": 2, "title": "Brand new"},
        ])
        pr1 = db.get_pr("ado/msazure", 1)
        assert pr1["title"] == "New"
        assert pr1["pinned"] is True
        pr2 = db.get_pr("ado/msazure", 2)
        assert not pr2.get("pinned")
