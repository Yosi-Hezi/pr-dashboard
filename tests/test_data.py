"""Tests for data.py — pure logic, no network."""

import re


from pr_dashboard.data import PrDataStore, _migrate_v1_to_v2, _pr_key


# ── _migrate_v1_to_v2 ───────────────────────────────────────────


class TestMigrateV1ToV2:
    def test_adds_version(self):
        v1 = {"prs": [{"id": 1, "title": "Fix"}]}
        result = _migrate_v1_to_v2(v1)
        assert result["version"] == 2

    def test_adds_source_field(self):
        v1 = {"prs": [{"id": 1}]}
        result = _migrate_v1_to_v2(v1)
        assert result["prs"][0]["source"] == "ado/msazure"

    def test_adds_sources_list(self):
        v1 = {"prs": [{"id": 1}]}
        result = _migrate_v1_to_v2(v1)
        assert "ado/msazure" in result["sources"]

    def test_empty_prs_sources(self):
        v1 = {"prs": []}
        result = _migrate_v1_to_v2(v1)
        assert result["sources"] == []

    def test_already_v2_unchanged(self):
        v2 = {
            "version": 2,
            "sources": ["github"],
            "prs": [{"id": 1, "source": "github"}],
        }
        result = _migrate_v1_to_v2(v2)
        assert result["prs"][0]["source"] == "github"


# ── _pr_key ──────────────────────────────────────────────────────


class TestPrKey:
    def test_composite_key(self):
        pr = {"source": "ado/msazure", "id": 42}
        assert _pr_key(pr) == ("ado/msazure", 42)

    def test_missing_fields(self):
        assert _pr_key({}) == ("", 0)


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
    def test_pinned_state_preserved_on_upsert(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"

        # Create initial data with a pinned PR
        data = {
            "version": 2,
            "sources": ["ado/msazure"],
            "currentUser": "",
            "prs": [
                {"source": "ado/msazure", "id": 42, "title": "Old", "pinned": True}
            ],
        }

        # Upsert with new entry (no pinned field, simulating API refresh)
        new_entry = {"source": "ado/msazure", "id": 42, "title": "New"}
        store._upsert_pr(data, new_entry)

        assert data["prs"][0]["title"] == "New"
        assert data["prs"][0]["pinned"] is True

    def test_unpinned_pr_stays_unpinned(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"

        data = {
            "version": 2,
            "sources": [],
            "currentUser": "",
            "prs": [
                {"source": "ado/msazure", "id": 42, "title": "Old", "pinned": False}
            ],
        }

        new_entry = {"source": "ado/msazure", "id": 42, "title": "New"}
        store._upsert_pr(data, new_entry)

        # pinned=False should NOT be carried over (only truthy values)
        assert (
            data["prs"][0].get("pinned") is None
            or data["prs"][0].get("pinned") is False
        )

    def test_new_pr_has_no_pinned(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"

        data = {"version": 2, "sources": [], "currentUser": "", "prs": []}
        new_entry = {"source": "ado/msazure", "id": 42, "title": "New"}
        store._upsert_pr(data, new_entry)

        assert data["prs"][0].get("pinned") is None


# ── toggle_pin ───────────────────────────────────────────────────


class TestTogglePin:
    def test_pin_and_unpin(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"

        data = {
            "version": 2,
            "sources": ["ado/msazure"],
            "currentUser": "",
            "prs": [{"source": "ado/msazure", "id": 42, "title": "Test"}],
        }
        store.save(data)

        # First toggle: pin
        result = store.toggle_pin(42, source="ado/msazure")
        assert result is True

        # Verify persisted
        loaded = store.load()
        assert loaded["prs"][0]["pinned"] is True

        # Second toggle: unpin
        result = store.toggle_pin(42, source="ado/msazure")
        assert result is False

        loaded = store.load()
        assert loaded["prs"][0]["pinned"] is False

    def test_toggle_nonexistent_pr(self, tmp_path):
        store = PrDataStore()
        store.data_file = tmp_path / "prs.json"

        data = {"version": 2, "sources": [], "currentUser": "", "prs": []}
        store.save(data)

        result = store.toggle_pin(999, source="ado/msazure")
        assert result is None
