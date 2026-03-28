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

# Reserved keys — do NOT use as hotkeys:
#   ctrl+c (SIGINT), ctrl+m (Enter), ctrl+i (Tab), ctrl+[ (Escape),
#   ctrl+h (Backspace), ctrl+j (LF), ctrl+z (suspend/EOF),
#   ctrl+q (Textual quit), tab/shift+tab (focus), escape (modal dismiss)
DEFAULT_KEYBINDINGS: dict[str, str] = {
    "main.help": "question_mark",
    "main.toggle_view": "tab",
    "main.refresh": "s",
    "main.sync": "S",
    "main.remove": "d",
    "main.remove_done": "D",
    "main.copy_url": "o",
    "main.open": "O",
    "main.filter": "slash",
    "main.info": "i",
    "main.log": "l",
    "main.peek": "v",
    "main.pin": "space",
    "main.filter_pinned": "f",
    "main.add_pr": "a",
    "main.manage_repos": "m",
    "main.manage_sources": "M",
    "main.row_rules": "R",
    "main.quit": "ctrl+c",
}

_SPECIAL_KEYS = frozenset(
    {
        "tab",
        "escape",
        "slash",
        "question_mark",
        "space",
        "enter",
        "backspace",
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
        "pageup",
        "pagedown",
        "delete",
        "insert",
        *(f"f{n}" for n in range(1, 13)),
    }
)

_MODIFIER_RE = re.compile(r"^(ctrl|alt|shift)\+(.+)$")
_SINGLE_CHAR_RE = re.compile(r"^[a-zA-Z0-9]$")


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
                key,
                seen[key],
                action,
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
        "display": data.get("display", {}),
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
                key,
                seen[key],
                action,
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
        missing = [
            f
            for f in _REQUIRED_EXT_FIELDS
            if not isinstance(ext.get(f), str) or not ext[f].strip()
        ]
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
                ext["name"],
                key,
            )
            continue

        if key in seen_keys:
            log.warning(
                "config: extension %r duplicates key %r already used by %r, skipping",
                ext["name"],
                key,
                seen_keys[key],
            )
            continue

        seen_keys[key] = ext["name"]
        valid.append({"key": key, "name": ext["name"], "command": ext["command"]})

    return valid


# ── Display configuration ────────────────────────────────────────────────

# ── Action definitions ────────────────────────────────────────────────────

# Action name → (description for footer, Textual method name, priority binding)
ACTION_DEFS: dict[str, tuple[str, str, bool]] = {
    "main.help": ("Help", "toggle_help", False),
    "main.toggle_view": ("PRs↔CRs", "toggle_view", True),
    "main.refresh": ("Refresh", "refresh_all", False),
    "main.sync": ("Sync", "sync", True),
    "main.remove": ("Remove", "remove_selected", False),
    "main.remove_done": ("Remove done", "remove_done", False),
    "main.open": ("Open", "open_selected", False),
    "main.copy_url": ("Copy URL", "copy_url", False),
    "main.filter": ("Filter", "show_filter", False),
    "main.info": ("Info", "show_info", False),
    "main.log": ("Log", "show_log", False),
    "main.peek": ("Peek", "peek_selected", False),
    "main.pin": ("Pin", "toggle_pin", False),
    "main.filter_pinned": ("★ Filter", "toggle_filter_pinned", False),
    "main.add_pr": ("Add PR", "add_pr", False),
    "main.manage_sources": ("Sources", "manage_sources", False),
    "main.manage_repos": ("Repos", "manage_repos", False),
    "main.row_rules": ("Rules", "show_row_rules", False),
    "main.quit": ("Exit", "quit", False),
}

DEFAULT_FOOTER_ACTIONS: list[str] = [
    "main.help",
    "main.toggle_view",
    "main.refresh",
    "main.sync",
    "main.remove",
    "main.remove_done",
    "main.open",
    "main.filter",
    "main.info",
    "main.peek",
    "main.pin",
    "main.filter_pinned",
    "main.quit",
]


# ── Column definitions ───────────────────────────────────────────────────

COLUMN_DEFS: dict[str, dict] = {
    "pin": {"header": "★"},
    "status": {"header": "St"},
    "author": {"header": "Author"},
    "repo": {"header": "Repo"},
    "id": {"header": "ID"},
    "title": {"header": "Title"},
    "my_vote": {"header": "Me"},
    "votes": {"header": "Votes"},
    "checks": {"header": "Checks"},
    "comments": {"header": "Cmts"},
    "updated": {"header": "Updated"},
    "fetched": {"header": "Fetched"},
    "source": {"header": "Src"},
    "action": {"header": "Action"},
    "sig_role": {"header": "Role"},
    "sig_isDraft": {"header": "Drft"},
    "sig_mergeStatus": {"header": "Merge"},
    "sig_myVote": {"header": "Vote"},
    "sig_isRequired": {"header": "Req"},
    "sig_hasActiveComments": {"header": "💬?"},
    "sig_allCommentsResolved": {"header": "💬✓"},
    "sig_allRequiredApproved": {"header": "Appr"},
    "sig_checksPass": {"header": "Chk✓"},
    "sig_myCommentPending": {"header": "📩"},
    "sig_myPendingThreads": {"header": "📩#"},
}

DEFAULT_DISPLAY: dict = {
    "columns": {
        "mine": [
            "pin",
            "status",
            "author",
            "repo",
            "id",
            "title",
            "votes",
            "checks",
            "comments",
            "updated",
            "fetched",
            "action",
        ],
        "reviews": [
            "pin",
            "status",
            "author",
            "repo",
            "id",
            "title",
            "my_vote",
            "votes",
            "checks",
            "comments",
            "updated",
            "fetched",
            "action",
        ],
    },
    "column_widths": {
        "title": 50,
        "author": 14,
        "action": 20,
    },
    "truncation_suffix": "..",
    "row_rules": [
        {"conditions": {"mergeStatus": "conflicts"}, "color": "#4a2d2d", "italic": True, "description": "Recommended: resolve merge conflicts before continuing", "action": "Fix conflicts"},
        {"conditions": {"role": "author", "hasActiveComments": True}, "color": "#4a3d1a", "bold": True, "description": "Recommended: address active review comments", "action": "Address comments"},
        {"conditions": {"role": "reviewer", "myVote": "NoVote", "isRequiredReviewer": True}, "color": "#3d3a1a", "description": "Recommended: review and vote — you are a required reviewer", "action": "Review (required)"},
        {"conditions": {"role": "reviewer", "myCommentPending": True}, "color": "#2d3a4a", "italic": True, "description": "Recommended: re-review — author replied to your comments", "action": "Re-review"},
        {"conditions": {"role": "reviewer", "myVote": "WaitingForAuthor", "allCommentsResolved": True}, "color": "#2d3a4a", "italic": True, "description": "Recommended: re-review — author has resolved all comments", "action": "Re-review"},
        {"conditions": {"status": "Approved"}, "color": "#2d4a2d"},
        {"conditions": {"status": "Completed"}, "color": "#2d3a4a", "strikethrough": True},
        {"conditions": {"status": "Abandoned"}, "color": "#4a2d2d", "strikethrough": True},
        {"conditions": {"role": "reviewer", "myVote": "NoVote"}, "color": "#3a3a2a", "action": "Review"},
    ],
    "footer_actions": list(DEFAULT_FOOTER_ACTIONS),
}


def get_display_config() -> dict:
    """Return effective display config (defaults merged with user overrides)."""
    config = load_config()
    user_display = config.get("display", {})
    if not isinstance(user_display, dict):
        log.warning("config: display section is not an object, using defaults")
        return dict(DEFAULT_DISPLAY)

    result = {}

    # Columns: merge per-view, validate column IDs
    user_cols = user_display.get("columns", {})
    result["columns"] = {}
    for view in ("mine", "reviews"):
        if view in user_cols and isinstance(user_cols[view], list):
            valid = [c for c in user_cols[view] if c in COLUMN_DEFS]
            invalid = [c for c in user_cols[view] if c not in COLUMN_DEFS]
            if invalid:
                log.warning("config: unknown column IDs in %s: %s", view, invalid)
            result["columns"][view] = (
                valid if valid else DEFAULT_DISPLAY["columns"][view]
            )
        else:
            result["columns"][view] = DEFAULT_DISPLAY["columns"][view]

    # Column widths
    user_widths = user_display.get("column_widths", {})
    result["column_widths"] = {**DEFAULT_DISPLAY["column_widths"]}
    if isinstance(user_widths, dict):
        for col, width in user_widths.items():
            if isinstance(width, int) and width > 0:
                result["column_widths"][col] = width

    # Truncation suffix
    suffix = user_display.get("truncation_suffix", DEFAULT_DISPLAY["truncation_suffix"])
    result["truncation_suffix"] = (
        suffix if isinstance(suffix, str) else DEFAULT_DISPLAY["truncation_suffix"]
    )

    # Row rules — signal-based styling rules (replaces row_colors)
    user_rules = user_display.get("row_rules")
    # Also accept legacy "row_colors" key for backward compatibility
    if user_rules is None:
        user_rules = user_display.get("row_colors")
    if user_rules is not None and isinstance(user_rules, list):
        valid_rules = []
        for rule in user_rules:
            if isinstance(rule, dict) and ("color" in rule or "bold" in rule or "italic" in rule or "strikethrough" in rule):
                # Normalize legacy format (status/mergeStatus at top level → conditions)
                if "conditions" not in rule and ("status" in rule or "mergeStatus" in rule):
                    conditions = {}
                    if "status" in rule:
                        conditions["status"] = rule["status"]
                    if "mergeStatus" in rule:
                        conditions["mergeStatus"] = rule["mergeStatus"]
                    normalized = {"conditions": conditions}
                    for k in ("color", "bold", "italic", "strikethrough"):
                        if k in rule:
                            normalized[k] = rule[k]
                    valid_rules.append(normalized)
                else:
                    valid_rules.append(rule)
            else:
                log.warning("config: invalid row_rules entry: %s", rule)
        result["row_rules"] = valid_rules
    else:
        result["row_rules"] = list(DEFAULT_DISPLAY["row_rules"])

    # Footer actions — ordered list of action names to show in footer
    user_footer = user_display.get("footer_actions")
    if user_footer is not None and isinstance(user_footer, list):
        valid = [a for a in user_footer if a in ACTION_DEFS]
        invalid = [a for a in user_footer if a not in ACTION_DEFS]
        if invalid:
            log.warning("config: unknown footer actions: %s", invalid)
        result["footer_actions"] = valid
    else:
        result["footer_actions"] = list(DEFAULT_FOOTER_ACTIONS)

    return result


def get_extensions() -> list[dict]:
    """Return validated extension definitions from config."""
    config = load_config()
    return _validate_extensions(config.get("extensions", []))
