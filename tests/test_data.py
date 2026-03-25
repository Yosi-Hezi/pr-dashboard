"""Tests for data.py — pure logic, no network."""

import re


from pr_dashboard.data import (
    PrDataStore,
    _pr_key,
    _repo_entry,
    _repo_in_list,
    _remove_repo_from_list,
)


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


# ── Upsert pin preservation ─────────────────────────────────────


class TestUpsertPinPreservation:
    def _make_data(self, prs=None):
        return {
            "version": 3,
            "currentUser": "",
            "sources": {"discovered": [], "include": [], "exclude": []},
            "repos": {"discovered": [], "include": [], "exclude": []},
            "prs": prs or [],
        }

    def test_pinned_state_preserved_on_upsert(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"

        data = self._make_data(
            [{"source": "ado/msazure", "id": 42, "title": "Old", "pinned": True}]
        )
        new_entry = {"source": "ado/msazure", "id": 42, "title": "New"}
        store._upsert_pr(data, new_entry)

        assert data["prs"][0]["title"] == "New"
        assert data["prs"][0]["pinned"] is True

    def test_unpinned_pr_stays_unpinned(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"

        data = self._make_data(
            [{"source": "ado/msazure", "id": 42, "title": "Old", "pinned": False}]
        )
        new_entry = {"source": "ado/msazure", "id": 42, "title": "New"}
        store._upsert_pr(data, new_entry)

        assert (
            data["prs"][0].get("pinned") is None
            or data["prs"][0].get("pinned") is False
        )

    def test_new_pr_has_no_pinned(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"

        data = self._make_data()
        new_entry = {"source": "ado/msazure", "id": 42, "title": "New"}
        store._upsert_pr(data, new_entry)

        assert data["prs"][0].get("pinned") is None


# ── toggle_pin ───────────────────────────────────────────────────


class TestTogglePin:
    def _make_data(self, prs=None):
        return {
            "version": 3,
            "currentUser": "",
            "sources": {"discovered": [], "include": [], "exclude": []},
            "repos": {"discovered": [], "include": [], "exclude": []},
            "prs": prs or [],
        }

    def test_pin_and_unpin(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"

        data = self._make_data([{"source": "ado/msazure", "id": 42, "title": "Test"}])
        store.save(data)

        result = store.toggle_pin(42, source="ado/msazure")
        assert result is True

        loaded = store.load()
        assert loaded["prs"][0]["pinned"] is True

        result = store.toggle_pin(42, source="ado/msazure")
        assert result is False

        loaded = store.load()
        assert loaded["prs"][0]["pinned"] is False

    def test_toggle_nonexistent_pr(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"

        data = self._make_data()
        store.save(data)

        result = store.toggle_pin(999, source="ado/msazure")
        assert result is None


# ── Source management ────────────────────────────────────────────


class TestSourceManagement:
    def _make_store(self, tmp_path, sources=None):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"
        data = {
            "version": 3,
            "currentUser": "",
            "sources": sources or {"discovered": [], "include": [], "exclude": []},
            "repos": {"discovered": [], "include": [], "exclude": []},
            "prs": [],
        }
        store.save(data)
        return store

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
    def _make_store(self, tmp_path, repos=None):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"
        data = {
            "version": 3,
            "currentUser": "",
            "sources": {"discovered": [], "include": [], "exclude": []},
            "repos": repos or {"discovered": [], "include": [], "exclude": []},
            "prs": [],
        }
        store.save(data)
        return store

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
        data = store.load()
        assert data["repos"]["include"] == []

    def test_exclude_repo_removes_reviewer_prs(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"
        data = {
            "version": 3,
            "currentUser": "",
            "sources": {"discovered": [], "include": [], "exclude": []},
            "repos": {
                "discovered": [{"source": "ado/msazure", "repo": "Noisy"}],
                "include": [],
                "exclude": [],
            },
            "prs": [
                {
                    "source": "ado/msazure",
                    "id": 1,
                    "role": "reviewer",
                    "repoName": "Noisy",
                },
                {
                    "source": "ado/msazure",
                    "id": 2,
                    "role": "author",
                    "repoName": "Noisy",
                },
                {
                    "source": "ado/msazure",
                    "id": 3,
                    "role": "reviewer",
                    "repoName": "Other",
                },
            ],
        }
        store.save(data)
        store.toggle_repo("ado/msazure", "Noisy")
        prs = store.load_prs()
        assert len(prs) == 2  # reviewer PR from Noisy removed, author kept
        assert {p["id"] for p in prs} == {2, 3}


# ── Version check ────────────────────────────────────────────────


class TestVersionCheck:
    def test_old_version_returns_empty(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"
        old_data = {"version": 2, "sources": ["ado/msazure"], "prs": [{"id": 1}]}
        store.data_file.write_text(__import__("json").dumps(old_data), encoding="utf-8")
        loaded = store.load()
        assert loaded["version"] == 3
        assert loaded["prs"] == []

    def test_v3_loads_correctly(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"
        v3_data = {
            "version": 3,
            "currentUser": "test",
            "sources": {"discovered": ["ado/msazure"], "include": [], "exclude": []},
            "repos": {"discovered": [], "include": [], "exclude": []},
            "prs": [{"id": 1, "source": "ado/msazure"}],
        }
        store.data_file.write_text(__import__("json").dumps(v3_data), encoding="utf-8")
        loaded = store.load()
        assert loaded["version"] == 3
        assert len(loaded["prs"]) == 1
