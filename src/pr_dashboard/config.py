"""User configuration — loads config.json with keybinding overrides."""

from __future__ import annotations

import json
import re
from pathlib import Path

from platformdirs import user_data_dir

from .logger import get_logger

log = get_logger()

CONFIG_DIR = Path(user_data_dir("pr-dashboard", ensure_exists=True))
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_KEYBINDINGS: dict[str, str] = {
    "main.help": "question_mark",
    "main.toggle_view": "tab",
    "main.refresh": "r",
    "main.refresh_all": "ctrl+r",
    "main.sync": "ctrl+s",
    "main.remove": "d",
    "main.remove_done": "shift+d",
    "main.open": "o",
    "main.copy_url": "c",
    "main.filter": "slash",
    "main.info": "i",
    "main.log": "l",
    "main.peek": "v",
    "main.pin": "p",
    "main.quit": "ctrl+c",
}

_SPECIAL_KEYS = frozenset({
    "tab", "escape", "slash", "question_mark", "space", "enter", "backspace",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    "delete", "insert",
    *(f"f{n}" for n in range(1, 13)),
})

_MODIFIER_RE = re.compile(r"^(ctrl|alt|shift)\+(.+)$")
_SINGLE_CHAR_RE = re.compile(r"^[a-z0-9]$")


def _validate_key(key: str) -> bool:
    """Check if a key string is valid.

    Valid forms: single char (a-z, 0-9), modifier+key (ctrl/alt/shift),
    or a recognised special key name.
    """
    if _SINGLE_CHAR_RE.match(key):
        return True
    if key in _SPECIAL_KEYS:
        return True
    m = _MODIFIER_RE.match(key)
    if m:
        inner = m.group(2)
        return _SINGLE_CHAR_RE.match(inner) is not None or inner in _SPECIAL_KEYS
    return False


def _validate_keybindings(bindings: dict) -> dict[str, str]:
    """Validate and return clean keybindings. Log warnings for issues."""
    clean: dict[str, str] = {}

    for action, key in bindings.items():
        if action not in DEFAULT_KEYBINDINGS:
            log.warning("config: unknown action %r in keybindings (typo?)", action)
            continue
        if not isinstance(key, str) or not key:
            log.warning("config: invalid key value for %r: %r", action, key)
            continue
        if not _validate_key(key):
            log.warning("config: invalid key string %r for action %r", key, action)
            continue
        clean[action] = key

    # Check for duplicate keys (two actions mapped to the same key)
    seen: dict[str, str] = {}
    for action, key in clean.items():
        if key in seen:
            log.warning(
                "config: duplicate key %r bound to both %r and %r",
                key, seen[key], action,
            )
        else:
            seen[key] = action

    return clean


def load_config() -> dict:
    """Load config.json. Returns empty-dict sections if file missing/invalid."""
    if not CONFIG_FILE.exists():
        return {"keybindings": {}, "extensions": []}

    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("config: failed to read %s: %s", CONFIG_FILE, exc)
        return {"keybindings": {}, "extensions": []}

    if not isinstance(data, dict):
        log.warning("config: expected top-level object in %s", CONFIG_FILE)
        return {"keybindings": {}, "extensions": []}

    return {
        "keybindings": data.get("keybindings", {}),
        "theme": data.get("theme", "textual-dark"),
        "extensions": data.get("extensions", []),
    }


def get_keybindings() -> dict[str, str]:
    """Return effective keybindings (defaults merged with user overrides)."""
    config = load_config()
    overrides = config.get("keybindings", {})

    if not isinstance(overrides, dict):
        log.warning("config: keybindings section is not an object, using defaults")
        return dict(DEFAULT_KEYBINDINGS)

    validated = _validate_keybindings(overrides)
    merged = {**DEFAULT_KEYBINDINGS, **validated}

    # Final duplicate-key check on the merged result
    seen: dict[str, str] = {}
    for action, key in merged.items():
        if key in seen:
            log.warning(
                "config: after merge, key %r is used by both %r and %r",
                key, seen[key], action,
            )
        else:
            seen[key] = action

    return merged


_REQUIRED_EXT_FIELDS = ("key", "name", "command")


def _validate_extensions(extensions: list) -> list[dict]:
    """Validate extension definitions. Skip invalid or conflicting entries."""
    if not isinstance(extensions, list):
        log.warning("config: extensions section is not a list, ignoring")
        return []

    builtin_keys = set(get_keybindings().values())
    valid: list[dict] = []
    seen_keys: dict[str, str] = {}

    for idx, ext in enumerate(extensions):
        if not isinstance(ext, dict):
            log.warning("config: extension #%d is not an object, skipping", idx)
            continue

        # Check required fields are present and non-empty strings
        missing = [f for f in _REQUIRED_EXT_FIELDS
                    if not isinstance(ext.get(f), str) or not ext[f].strip()]
        if missing:
            log.warning("config: extension #%d missing/empty fields: %s", idx, missing)
            continue

        key = ext["key"]
        if not _validate_key(key):
            log.warning("config: extension %r has invalid key %r", ext["name"], key)
            continue

        if key in builtin_keys:
            log.warning(
                "config: extension %r key %r conflicts with a built-in keybinding, skipping",
                ext["name"], key,
            )
            continue

        if key in seen_keys:
            log.warning(
                "config: extension %r duplicates key %r already used by %r, skipping",
                ext["name"], key, seen_keys[key],
            )
            continue

        seen_keys[key] = ext["name"]
        valid.append({"key": key, "name": ext["name"], "command": ext["command"]})

    return valid


def get_extensions() -> list[dict]:
    """Return validated extension definitions from config."""
    config = load_config()
    return _validate_extensions(config.get("extensions", []))
