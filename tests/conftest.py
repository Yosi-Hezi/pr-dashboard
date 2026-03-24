"""Shared fixtures for pr-dashboard tests."""

import pytest


@pytest.fixture()
def sample_pr():
    """Minimal PR dict for testing."""
    return {
        "id": 12345,
        "title": "Fix auth flow",
        "author": "alice",
        "repoName": "AzNet-ApplicationSecurity-MyRepo",
        "status": "active",
        "source": "ado/msazure",
        "lastUpdated": "2025-01-15T10:00:00+00:00",
        "reviews": [],
    }
