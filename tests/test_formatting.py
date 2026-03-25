"""Tests for formatting.py — pure logic, no network."""

from datetime import UTC, datetime, timedelta

import pytest

from pr_dashboard.formatting import (
    _derive_status,
    format_pin,
    format_reviews,
    format_time_ago,
    pr_matches_filter,
    shorten_repo,
    sort_prs,
)


# ── format_time_ago ──────────────────────────────────────────────


class TestFormatTimeAgo:
    def test_none_returns_question(self):
        assert format_time_ago(None) == "?"

    def test_empty_returns_question(self):
        assert format_time_ago("") == "?"

    def test_invalid_string(self):
        assert format_time_ago("not-a-date") == "?"

    def test_just_now(self):
        now = datetime.now(UTC).isoformat()
        assert format_time_ago(now) == "just now"

    def test_minutes_ago(self):
        t = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        assert format_time_ago(t) == "5m ago"

    def test_hours_ago(self):
        t = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        assert format_time_ago(t) == "2h ago"

    def test_days_ago(self):
        t = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        assert format_time_ago(t) == "3d ago"

    def test_weeks_ago(self):
        t = (datetime.now(UTC) - timedelta(weeks=1)).isoformat()
        assert format_time_ago(t) == "1w ago"


# ── _derive_status ───────────────────────────────────────────────


class TestDeriveStatus:
    def test_active_no_pr(self):
        symbol, label = _derive_status("active")
        assert symbol == "○"
        assert label == "Active"

    def test_active_plain(self):
        symbol, label = _derive_status("active", {"reviews": []})
        assert symbol == "○"
        assert label == "Active"

    def test_completed(self):
        symbol, label = _derive_status("completed")
        assert symbol == "✓✓"
        assert label == "Completed"

    def test_abandoned(self):
        symbol, label = _derive_status("abandoned")
        assert symbol == "∅"
        assert label == "Abandoned"

    def test_draft(self):
        pr = {"isDraft": True, "reviews": []}
        symbol, label = _derive_status("active", pr)
        assert symbol == "✎"
        assert label == "Draft"

    def test_autocomplete(self):
        pr = {"autoCompleteSetBy": "user", "reviews": []}
        symbol, label = _derive_status("active", pr)
        assert symbol == "»"
        assert label == "Auto-complete"

    def test_waiting_for_author(self):
        pr = {"reviews": [{"vote": "WaitingForAuthor"}]}
        symbol, label = _derive_status("active", pr)
        assert symbol == "↻"
        assert label == "Waiting for Author"

    def test_approved_required(self):
        pr = {"reviews": [{"vote": "Approved", "isRequired": True}]}
        symbol, label = _derive_status("active", pr)
        assert symbol == "✓"
        assert label == "Approved"


# ── format_reviews ───────────────────────────────────────────────


class TestFormatReviews:
    def test_empty_list(self):
        assert format_reviews([]) == ""

    def test_single_approval(self):
        reviews = [{"vote": "Approved"}]
        assert "✓" in format_reviews(reviews)

    def test_mix_of_votes(self):
        reviews = [
            {"vote": "Approved"},
            {"vote": "Rejected"},
        ]
        result = format_reviews(reviews)
        assert "✓" in result
        assert "✗" in result

    def test_required_no_vote(self):
        reviews = [{"vote": "NoVote", "isRequired": True}]
        assert "!" in format_reviews(reviews)

    def test_optional_no_vote_hidden(self):
        reviews = [{"vote": "NoVote", "isRequired": False}]
        assert format_reviews(reviews) == ""

    def test_exclude_vote(self):
        reviews = [
            {"vote": "Approved"},
            {"vote": "Approved"},
        ]
        result_without = format_reviews(reviews, exclude_vote="Approved")
        result_with = format_reviews(reviews)
        # One fewer approval symbol when excluding
        assert result_without.count("✓") == result_with.count("✓") - 1


# ── pr_matches_filter ────────────────────────────────────────────


class TestPrMatchesFilter:
    def test_match_title(self, sample_pr):
        assert pr_matches_filter(sample_pr, "auth")

    def test_match_author(self, sample_pr):
        assert pr_matches_filter(sample_pr, "alice")

    def test_match_repo(self, sample_pr):
        assert pr_matches_filter(sample_pr, "MyRepo")

    def test_match_id(self, sample_pr):
        assert pr_matches_filter(sample_pr, "12345")

    def test_case_insensitive(self, sample_pr):
        assert pr_matches_filter(sample_pr, "FIX AUTH")

    def test_no_match(self, sample_pr):
        assert not pr_matches_filter(sample_pr, "nonexistent")


# ── shorten_repo ─────────────────────────────────────────────────


class TestShortenRepo:
    def test_aznet_appsec_prefix(self):
        assert shorten_repo("AzNet-ApplicationSecurity-Foo") == "Foo"

    def test_aznet_prefix(self):
        assert shorten_repo("AzNet-Bar") == "Bar"

    def test_no_prefix(self):
        assert shorten_repo("SomeRepo") == "SomeRepo"


# ── sort_prs ─────────────────────────────────────────────────────


class TestSortPrs:
    def test_sort_by_repo_then_updated(self):
        prs = [
            {"repoName": "BBB", "lastUpdated": "2025-01-01T00:00:00+00:00"},
            {"repoName": "AAA", "lastUpdated": "2025-01-01T00:00:00+00:00"},
            {"repoName": "AAA", "lastUpdated": "2025-01-02T00:00:00+00:00"},
        ]
        result = sort_prs(prs)
        assert result[0]["repoName"] == "AAA"
        assert result[1]["repoName"] == "AAA"
        # More recent first within same repo
        assert result[0]["lastUpdated"] > result[1]["lastUpdated"]
        assert result[2]["repoName"] == "BBB"

    def test_pinned_prs_sort_first(self):
        prs = [
            {"repoName": "ZZZ", "lastUpdated": "2025-01-01T00:00:00+00:00", "pinned": True},
            {"repoName": "AAA", "lastUpdated": "2025-01-02T00:00:00+00:00"},
            {"repoName": "BBB", "lastUpdated": "2025-01-01T00:00:00+00:00"},
        ]
        result = sort_prs(prs)
        # Pinned PR comes first despite repo name ZZZ
        assert result[0].get("pinned") is True
        assert result[0]["repoName"] == "ZZZ"
        # Unpinned follow normal sort order
        assert result[1]["repoName"] == "AAA"
        assert result[2]["repoName"] == "BBB"

    def test_pinned_prs_sorted_among_themselves(self):
        prs = [
            {"repoName": "BBB", "lastUpdated": "2025-01-01T00:00:00+00:00", "pinned": True},
            {"repoName": "AAA", "lastUpdated": "2025-01-02T00:00:00+00:00", "pinned": True},
            {"repoName": "CCC", "lastUpdated": "2025-01-01T00:00:00+00:00"},
        ]
        result = sort_prs(prs)
        # Both pinned come first, sorted by repo
        assert result[0]["repoName"] == "AAA"
        assert result[1]["repoName"] == "BBB"
        assert result[2]["repoName"] == "CCC"

    def test_unpinned_pr_not_affected(self):
        prs = [
            {"repoName": "AAA", "lastUpdated": "2025-01-01T00:00:00+00:00", "pinned": False},
            {"repoName": "BBB", "lastUpdated": "2025-01-01T00:00:00+00:00"},
        ]
        result = sort_prs(prs)
        # pinned=False is same as not pinned
        assert result[0]["repoName"] == "AAA"
        assert result[1]["repoName"] == "BBB"


# ── format_pin ───────────────────────────────────────────────────


class TestFormatPin:
    def test_pinned_pr(self):
        assert format_pin({"pinned": True}) == "★"

    def test_unpinned_pr(self):
        assert format_pin({"pinned": False}) == ""

    def test_no_pinned_field(self):
        assert format_pin({}) == ""
