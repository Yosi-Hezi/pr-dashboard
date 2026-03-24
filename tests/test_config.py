"""Tests for config.py — pure validation logic, no network."""

import json
import logging

import pytest

from pr_dashboard.config import (
    DEFAULT_KEYBINDINGS,
    _validate_extensions,
    _validate_key,
    _validate_keybindings,
    get_keybindings,
)

LOGGER_NAME = "pr-dashboard"


@pytest.fixture(autouse=True)
def _propagate_logger():
    """Enable propagation so caplog can capture log records."""
    logger = logging.getLogger(LOGGER_NAME)
    orig = logger.propagate
    logger.propagate = True
    yield
    logger.propagate = orig


# ── _validate_key ────────────────────────────────────────────────


class TestValidateKey:
    @pytest.mark.parametrize("key", ["a", "z", "0", "9"])
    def test_single_char_valid(self, key):
        assert _validate_key(key)

    @pytest.mark.parametrize("key", ["ctrl+a", "alt+z", "shift+0"])
    def test_modifier_char_valid(self, key):
        assert _validate_key(key)

    @pytest.mark.parametrize("key", ["tab", "escape", "f1", "f12", "space"])
    def test_special_keys_valid(self, key):
        assert _validate_key(key)

    @pytest.mark.parametrize("key", ["ctrl+tab", "shift+f1", "alt+space"])
    def test_modifier_special_valid(self, key):
        assert _validate_key(key)

    @pytest.mark.parametrize("key", ["", "AB", "meta+x", "ctrl+", "ctrl+AB"])
    def test_invalid_keys(self, key):
        assert not _validate_key(key)


# ── _validate_keybindings ────────────────────────────────────────


class TestValidateKeybindings:
    def test_unknown_action_skipped(self, caplog):
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _validate_keybindings({"main.nonexistent": "a"})
        assert result == {}
        assert any("unknown action" in r.message for r in caplog.records)

    def test_invalid_key_value(self, caplog):
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _validate_keybindings({"main.help": ""})
        assert "main.help" not in result

    def test_duplicate_keys_warn(self, caplog):
        bindings = {"main.help": "x", "main.quit": "x"}
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _validate_keybindings(bindings)
        assert any("duplicate key" in r.message for r in caplog.records)

    def test_valid_override(self):
        result = _validate_keybindings({"main.help": "h"})
        assert result == {"main.help": "h"}


# ── _validate_extensions ────────────────────────────────────────


class TestValidateExtensions:
    def test_missing_fields(self, caplog):
        exts = [{"key": "x"}]  # missing name and command
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _validate_extensions(exts)
        assert result == []

    def test_key_conflicts_with_builtin(self, caplog):
        builtin_key = list(DEFAULT_KEYBINDINGS.values())[0]
        ext = {"key": builtin_key, "name": "Test", "command": "echo hi"}
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _validate_extensions([ext])
        assert result == []

    def test_duplicate_extension_keys(self, caplog):
        exts = [
            {"key": "f5", "name": "Ext1", "command": "echo 1"},
            {"key": "f5", "name": "Ext2", "command": "echo 2"},
        ]
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _validate_extensions(exts)
        assert len(result) == 1
        assert result[0]["name"] == "Ext1"

    def test_valid_extension(self):
        ext = {"key": "f5", "name": "Run Tests", "command": "pytest"}
        result = _validate_extensions([ext])
        assert len(result) == 1
        assert result[0]["name"] == "Run Tests"


# ── get_keybindings ──────────────────────────────────────────────


class TestGetKeybindings:
    def test_returns_defaults_no_config(self, monkeypatch, tmp_path):
        fake_config = tmp_path / "config.json"
        import pr_dashboard.config as config

        monkeypatch.setattr(config, "CONFIG_FILE", fake_config)
        result = get_keybindings()
        assert result == DEFAULT_KEYBINDINGS
