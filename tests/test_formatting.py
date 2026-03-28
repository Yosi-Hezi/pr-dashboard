"""Tests for formatting.py — pure logic, no network."""

from datetime import UTC, datetime, timedelta


from pr_dashboard.formatting import (
    _derive_status,
    evaluate_pr_conditions,
    format_pin,
    format_reviews,
    format_time_ago,
    get_cell_value,
    pr_matches_filter,
    pr_row_style,
    shorten_repo,
    sort_prs,
    truncate,
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
        # Two approvals grouped = "✓2", excluding one = "✓"
        assert result_with == "✓2"
        assert result_without == "✓"


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

    def test_pinned_prs_not_reordered(self):
        """Pinned PRs stay in normal sort order (no pinned-first reordering)."""
        prs = [
            {
                "repoName": "ZZZ",
                "lastUpdated": "2025-01-01T00:00:00+00:00",
                "pinned": True,
            },
            {"repoName": "AAA", "lastUpdated": "2025-01-02T00:00:00+00:00"},
            {"repoName": "BBB", "lastUpdated": "2025-01-01T00:00:00+00:00"},
        ]
        result = sort_prs(prs)
        # Pinned PR stays at its natural position (sorted by repo)
        assert result[0]["repoName"] == "AAA"
        assert result[1]["repoName"] == "BBB"
        assert result[2]["repoName"] == "ZZZ"

    def test_pinned_prs_sorted_among_themselves(self):
        """Pinned PRs follow normal sort order (repo then updated)."""
        prs = [
            {
                "repoName": "BBB",
                "lastUpdated": "2025-01-01T00:00:00+00:00",
                "pinned": True,
            },
            {
                "repoName": "AAA",
                "lastUpdated": "2025-01-02T00:00:00+00:00",
                "pinned": True,
            },
            {"repoName": "CCC", "lastUpdated": "2025-01-01T00:00:00+00:00"},
        ]
        result = sort_prs(prs)
        assert result[0]["repoName"] == "AAA"
        assert result[1]["repoName"] == "BBB"
        assert result[2]["repoName"] == "CCC"

    def test_unpinned_pr_not_affected(self):
        prs = [
            {
                "repoName": "AAA",
                "lastUpdated": "2025-01-01T00:00:00+00:00",
                "pinned": False,
            },
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


# ── pr_row_style ─────────────────────────────────────────────────


class TestPrRowStyle:
    def test_approved_pr(self):
        pr = {"status": "active", "reviews": [{"vote": "Approved", "isRequired": True}]}
        style, rule = pr_row_style(pr)
        assert style is not None
        assert style.bgcolor.name == "#2d4a2d"

    def test_completed_pr(self):
        style, rule = pr_row_style({"status": "completed"})
        assert style is not None
        assert style.bgcolor.name == "#2d3a4a"
        assert style.strike is True

    def test_abandoned_pr(self):
        style, rule = pr_row_style({"status": "abandoned"})
        assert style is not None
        assert style.bgcolor.name == "#4a2d2d"
        assert style.strike is True

    def test_active_pr_no_style(self):
        pr = {"status": "active", "reviews": []}
        style, rule = pr_row_style(pr)
        assert style is None

    def test_draft_pr_no_style(self):
        pr = {"status": "active", "isDraft": True, "reviews": []}
        style, rule = pr_row_style(pr)
        assert style is None

    def test_merge_conflicts(self):
        pr = {"status": "active", "mergeStatus": "conflicts", "reviews": []}
        style, rule = pr_row_style(pr)
        assert style is not None
        assert style.bgcolor.name == "#4a2d2d"
        assert style.italic is True

    def test_author_active_comments(self):
        pr = {"status": "active", "role": "author", "commentsActive": 3, "commentsTotal": 5, "reviews": []}
        style, rule = pr_row_style(pr)
        assert style is not None
        assert style.bgcolor.name == "#4a3d1a"
        assert style.bold is True

    def test_reviewer_novote_required(self):
        pr = {"status": "active", "role": "reviewer", "myVote": "NoVote", "isRequiredReviewer": True, "reviews": []}
        style, rule = pr_row_style(pr)
        assert style is not None
        assert style.bgcolor.name == "#3d3a1a"

    def test_reviewer_novote_optional(self):
        pr = {"status": "active", "role": "reviewer", "myVote": "NoVote", "isRequiredReviewer": False, "reviews": []}
        style, rule = pr_row_style(pr)
        assert style is not None
        assert style.bgcolor.name == "#3a3a2a"


# ── truncate with suffix ─────────────────────────────────────────


class TestTruncateWithSuffix:
    def test_default_suffix(self):
        assert truncate("hello world", 8) == "hello .."

    def test_custom_suffix(self):
        assert truncate("hello world", 8, "…") == "hello w…"

    def test_no_truncation(self):
        assert truncate("hi", 10, "...") == "hi"


# ── pr_row_style configurable ───────────────────────────────────


class TestPrRowStyleConfigurable:
    def test_custom_rules(self):
        rules = [{"conditions": {"status": "Draft"}, "color": "#112233"}]
        pr = {"status": "active", "isDraft": True, "reviews": []}
        style, rule = pr_row_style(pr, rules=rules)
        assert style is not None
        assert style.bgcolor.name == "#112233"

    def test_merge_status_rule(self):
        rules = [{"conditions": {"mergeStatus": "conflicts"}, "color": "#443322"}]
        pr = {"status": "active", "mergeStatus": "conflicts", "reviews": []}
        style, rule = pr_row_style(pr, rules=rules)
        assert style is not None

    def test_no_match(self):
        rules = [{"conditions": {"status": "Completed"}, "color": "#aabbcc"}]
        pr = {"status": "active", "reviews": []}
        style, rule = pr_row_style(pr, rules=rules)
        assert style is None
        assert rule is None

    def test_empty_rules(self):
        style, rule = pr_row_style({"status": "active", "reviews": []}, rules=[])
        assert style is None
        assert rule is None

    def test_style_bold_italic(self):
        rules = [{"conditions": {"status": "Active"}, "color": "#111111", "bold": True, "italic": True}]
        pr = {"status": "active", "reviews": []}
        style, rule = pr_row_style(pr, rules=rules)
        assert style is not None
        assert style.bold is True
        assert style.italic is True

    def test_empty_conditions_skipped(self):
        rules = [{"conditions": {}, "color": "#111111"}, {"conditions": {"status": "Active"}, "color": "#222222"}]
        pr = {"status": "active", "reviews": []}
        style, rule = pr_row_style(pr, rules=rules)
        assert style is not None
        assert style.bgcolor.name == "#222222"

    def test_first_match_wins(self):
        rules = [
            {"conditions": {"status": "Active"}, "color": "#111111"},
            {"conditions": {"status": "Active"}, "color": "#222222"},
        ]
        pr = {"status": "active", "reviews": []}
        style, rule = pr_row_style(pr, rules=rules)
        assert style.bgcolor.name == "#111111"

    def test_multi_condition_match(self):
        rules = [{"conditions": {"role": "reviewer", "myVote": "NoVote"}, "color": "#333333"}]
        pr = {"status": "active", "role": "reviewer", "myVote": "NoVote", "reviews": []}
        style, rule = pr_row_style(pr, rules=rules)
        assert style is not None
        assert style.bgcolor.name == "#333333"

    def test_multi_condition_no_match(self):
        rules = [{"conditions": {"role": "reviewer", "myVote": "NoVote"}, "color": "#333333"}]
        pr = {"status": "active", "role": "author", "myVote": "NoVote", "reviews": []}
        style, rule = pr_row_style(pr, rules=rules)
        assert style is None
        assert rule is None

    def test_description_returned(self):
        rules = [{"conditions": {"status": "Active"}, "color": "#111111", "description": "Do something"}]
        pr = {"status": "active", "reviews": []}
        style, rule = pr_row_style(pr, rules=rules)
        assert style is not None
        assert rule["description"] == "Do something"

    def test_no_description(self):
        rules = [{"conditions": {"status": "Active"}, "color": "#111111"}]
        pr = {"status": "active", "reviews": []}
        style, rule = pr_row_style(pr, rules=rules)
        assert style is not None
        assert rule.get("description") is None


# ── evaluate_pr_conditions ──────────────────────────────────────


class TestEvaluatePrConditions:
    def test_basic_active_pr(self):
        pr = {"status": "active", "reviews": []}
        conds = evaluate_pr_conditions(pr)
        assert conds["status"] == "Active"
        assert conds["role"] == "author"
        assert conds["isDraft"] is False
        assert conds["hasActiveComments"] is False
        assert conds["allCommentsResolved"] is False
        assert conds["allRequiredApproved"] is False
        assert conds["checksPass"] is False
        assert conds["isPinned"] is False

    def test_has_active_comments(self):
        pr = {"status": "active", "commentsActive": 2, "commentsTotal": 5, "reviews": []}
        conds = evaluate_pr_conditions(pr)
        assert conds["hasActiveComments"] is True
        assert conds["allCommentsResolved"] is False

    def test_all_comments_resolved(self):
        pr = {"status": "active", "commentsActive": 0, "commentsTotal": 5, "reviews": []}
        conds = evaluate_pr_conditions(pr)
        assert conds["hasActiveComments"] is False
        assert conds["allCommentsResolved"] is True

    def test_all_required_approved(self):
        pr = {
            "status": "active",
            "reviews": [
                {"vote": "Approved", "isRequired": True},
                {"vote": "ApprovedWithSuggestions", "isRequired": True},
            ],
        }
        conds = evaluate_pr_conditions(pr)
        assert conds["allRequiredApproved"] is True

    def test_not_all_required_approved(self):
        pr = {
            "status": "active",
            "reviews": [
                {"vote": "Approved", "isRequired": True},
                {"vote": "NoVote", "isRequired": True},
            ],
        }
        conds = evaluate_pr_conditions(pr)
        assert conds["allRequiredApproved"] is False

    def test_checks_pass(self):
        pr = {"status": "active", "requiredPass": 3, "requiredTotal": 3, "reviews": []}
        conds = evaluate_pr_conditions(pr)
        assert conds["checksPass"] is True

    def test_checks_not_pass(self):
        pr = {"status": "active", "requiredPass": 2, "requiredTotal": 3, "reviews": []}
        conds = evaluate_pr_conditions(pr)
        assert conds["checksPass"] is False

    def test_pinned(self):
        pr = {"status": "active", "pinned": True, "reviews": []}
        conds = evaluate_pr_conditions(pr)
        assert conds["isPinned"] is True

    def test_reviewer_role(self):
        pr = {"status": "active", "role": "reviewer", "myVote": "Approved", "isRequiredReviewer": True, "reviews": []}
        conds = evaluate_pr_conditions(pr)
        assert conds["role"] == "reviewer"
        assert conds["myVote"] == "Approved"
        assert conds["isRequiredReviewer"] is True

    def test_my_comment_pending_basic(self):
        pr = {
            "status": "active", "reviews": [],
            "threads": [
                {"comments": [
                    {"author": "Me", "text": "fix this"},
                    {"author": "Author", "text": "done"},
                ]}
            ],
        }
        conds = evaluate_pr_conditions(pr, current_user="Me")
        assert conds["myCommentPending"] is True
        assert conds["myPendingThreads"] == 1

    def test_my_comment_pending_last_is_me(self):
        pr = {
            "status": "active", "reviews": [],
            "threads": [
                {"comments": [
                    {"author": "Author", "text": "question"},
                    {"author": "Me", "text": "answer"},
                ]}
            ],
        }
        conds = evaluate_pr_conditions(pr, current_user="Me")
        assert conds["myCommentPending"] is False
        assert conds["myPendingThreads"] == 0

    def test_my_comment_pending_no_current_user(self):
        pr = {
            "status": "active", "reviews": [],
            "threads": [{"comments": [{"author": "Me", "text": "x"}, {"author": "Other", "text": "y"}]}],
        }
        conds = evaluate_pr_conditions(pr)
        assert conds["myCommentPending"] is False
        assert conds["myPendingThreads"] == 0

    def test_my_comment_pending_multiple_threads(self):
        pr = {
            "status": "active", "reviews": [],
            "threads": [
                {"comments": [{"author": "Me", "text": "a"}, {"author": "Dev", "text": "b"}]},
                {"comments": [{"author": "Me", "text": "c"}, {"author": "Dev", "text": "d"}]},
                {"comments": [{"author": "Other", "text": "e"}]},
            ],
        }
        conds = evaluate_pr_conditions(pr, current_user="Me")
        assert conds["myPendingThreads"] == 2

    def test_my_comment_pending_case_insensitive(self):
        pr = {
            "status": "active", "reviews": [],
            "threads": [{"comments": [{"author": "me", "text": "x"}, {"author": "Other", "text": "y"}]}],
        }
        conds = evaluate_pr_conditions(pr, current_user="ME")
        assert conds["myCommentPending"] is True


# ── get_cell_value ───────────────────────────────────────────────


class TestGetCellValue:
    def test_pin_column(self):
        assert get_cell_value("pin", {"pinned": True}) == "★"
        assert get_cell_value("pin", {}) == ""

    def test_title_with_custom_width(self):
        display = {"column_widths": {"title": 10}, "truncation_suffix": "…"}
        val = get_cell_value(
            "title", {"title": "A very long title here"}, display=display
        )
        assert len(val) == 10
        assert val.endswith("…")

    def test_unknown_column(self):
        assert get_cell_value("nonexistent", {}) == ""

    def test_action_column_match(self):
        rules = [{"conditions": {"status": "Active"}, "color": "#111111", "action": "Do stuff"}]
        display = {"column_widths": {"action": 20}, "truncation_suffix": "..", "row_rules": rules}
        pr = {"status": "active", "reviews": []}
        assert get_cell_value("action", pr, display=display) == "Do stuff"

    def test_action_column_no_match(self):
        rules = [{"conditions": {"status": "Completed"}, "color": "#111111", "action": "Done"}]
        display = {"column_widths": {"action": 20}, "truncation_suffix": "..", "row_rules": rules}
        pr = {"status": "active", "reviews": []}
        assert get_cell_value("action", pr, display=display) == ""

    def test_sig_role_column(self):
        assert get_cell_value("sig_role", {"role": "reviewer"}) == "reviewer"
        assert get_cell_value("sig_role", {}) == "author"

    def test_sig_isDraft_column(self):
        assert get_cell_value("sig_isDraft", {"isDraft": True}) == "✓"
        assert get_cell_value("sig_isDraft", {"isDraft": False}) == ""

    def test_sig_isRequired_column(self):
        assert get_cell_value("sig_isRequired", {"isRequiredReviewer": True}) == "✓"
        assert get_cell_value("sig_isRequired", {}) == ""

    def test_sig_checksPass_column(self):
        assert get_cell_value("sig_checksPass", {"requiredPass": 3, "requiredTotal": 3}) == "✓"
        assert get_cell_value("sig_checksPass", {"requiredPass": 2, "requiredTotal": 3}) == ""

    def test_sig_myCommentPending_column(self):
        pr = {
            "status": "active", "reviews": [], "currentUserName": "Me",
            "threads": [{"comments": [{"author": "Me", "text": "x"}, {"author": "Other", "text": "y"}]}],
        }
        assert get_cell_value("sig_myCommentPending", pr) == "✓"
