"""SQLite database layer for PR Dashboard.

Replaces the JSON-file backend with a relational store for faster
targeted reads/writes and proper concurrency.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from platformdirs import user_data_dir

from .logger import get_logger

log = get_logger()

DATA_DIR = Path(user_data_dir("pr-dashboard", ensure_exists=True))
DB_FILE = DATA_DIR / "dashboard.db"

SCHEMA_VERSION = 1

# Indexed columns extracted from the PR dict for fast queries.
_PR_INDEX_COLS = ("role", "status", "repo_name", "is_draft", "is_mine", "pinned", "last_updated")


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a WAL-mode connection with row factory."""
    path = db_path or DB_FILE
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS sources (
            name      TEXT NOT NULL,
            list_type TEXT NOT NULL CHECK(list_type IN ('discovered', 'include', 'exclude')),
            PRIMARY KEY (name, list_type)
        );

        CREATE TABLE IF NOT EXISTS repos (
            source    TEXT NOT NULL,
            repo      TEXT NOT NULL,
            list_type TEXT NOT NULL CHECK(list_type IN ('discovered', 'include', 'exclude')),
            PRIMARY KEY (source, repo, list_type)
        );

        CREATE TABLE IF NOT EXISTS prs (
            source       TEXT NOT NULL,
            id           INTEGER NOT NULL,
            role         TEXT    DEFAULT 'author',
            status       TEXT    DEFAULT 'active',
            repo_name    TEXT    DEFAULT '',
            is_draft     INTEGER DEFAULT 0,
            is_mine      INTEGER DEFAULT 0,
            pinned       INTEGER DEFAULT 0,
            last_updated TEXT,
            data         TEXT    NOT NULL,
            PRIMARY KEY (source, id)
        );

        CREATE INDEX IF NOT EXISTS idx_prs_role   ON prs(role);
        CREATE INDEX IF NOT EXISTS idx_prs_status ON prs(status);
    """)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    conn.commit()


def _pr_dict_to_row(pr: dict) -> tuple:
    """Extract indexed columns + JSON blob from a PR dict."""
    return (
        pr.get("source", ""),
        pr.get("id", 0),
        pr.get("role", "author"),
        pr.get("status", "active"),
        pr.get("repoName", ""),
        1 if pr.get("isDraft") else 0,
        1 if pr.get("isMine") else 0,
        1 if pr.get("pinned") else 0,
        pr.get("lastUpdated"),
        json.dumps(pr, ensure_ascii=False),
    )


def _row_to_pr_dict(row: sqlite3.Row) -> dict:
    """Parse a PR row back into a dict."""
    pr = json.loads(row["data"])
    # Ensure pinned state from indexed column wins (canonical source)
    pr["pinned"] = bool(row["pinned"])
    return pr


# ── Public API ────────────────────────────────────────────────────────────


class Database:
    """SQLite backend for PR Dashboard data.

    Designed for single-threaded use within an asyncio event loop.
    Do not share instances across OS threads.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or DB_FILE
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _connect(self._db_path)
            _create_schema(self._conn)
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Meta ──────────────────────────────────────────────────────────────

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )
        self.conn.commit()

    # ── Sources ───────────────────────────────────────────────────────────

    def get_sources(self, list_type: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sources WHERE list_type = ? ORDER BY name",
            (list_type,),
        ).fetchall()
        return [r["name"] for r in rows]

    def set_sources(self, list_type: str, names: list[str]) -> None:
        """Replace all sources of a given list_type."""
        self.conn.execute("DELETE FROM sources WHERE list_type = ?", (list_type,))
        self.conn.executemany(
            "INSERT INTO sources (name, list_type) VALUES (?, ?)",
            [(n, list_type) for n in names],
        )
        self.conn.commit()

    def add_source(self, name: str, list_type: str) -> bool:
        """Add a source. Returns True if newly added."""
        try:
            self.conn.execute(
                "INSERT INTO sources (name, list_type) VALUES (?, ?)",
                (name, list_type),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_source(self, name: str, list_type: str) -> bool:
        """Remove a source. Returns True if existed."""
        cur = self.conn.execute(
            "DELETE FROM sources WHERE name = ? AND list_type = ?",
            (name, list_type),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_all_sources(self) -> list[tuple[str, str]]:
        """Return all (name, list_type) pairs."""
        rows = self.conn.execute(
            "SELECT name, list_type FROM sources ORDER BY name, list_type"
        ).fetchall()
        return [(r["name"], r["list_type"]) for r in rows]

    # ── Repos ─────────────────────────────────────────────────────────────

    def get_repos(self, list_type: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT source, repo FROM repos WHERE list_type = ? ORDER BY source, repo",
            (list_type,),
        ).fetchall()
        return [{"source": r["source"], "repo": r["repo"]} for r in rows]

    def set_repos(self, list_type: str, repos: list[dict]) -> None:
        """Replace all repos of a given list_type."""
        self.conn.execute("DELETE FROM repos WHERE list_type = ?", (list_type,))
        self.conn.executemany(
            "INSERT INTO repos (source, repo, list_type) VALUES (?, ?, ?)",
            [(r["source"], r["repo"], list_type) for r in repos],
        )
        self.conn.commit()

    def add_repo(self, source: str, repo: str, list_type: str) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO repos (source, repo, list_type) VALUES (?, ?, ?)",
                (source, repo, list_type),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_repo(self, source: str, repo: str, list_type: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM repos WHERE source = ? AND repo = ? AND list_type = ?",
            (source, repo, list_type),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def repo_in_list(self, source: str, repo: str, list_type: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM repos WHERE source = ? AND repo = ? AND list_type = ?",
            (source, repo, list_type),
        ).fetchone()
        return row is not None

    def get_all_repos(self) -> list[tuple[str, str, str]]:
        """Return all (source, repo, list_type) triples."""
        rows = self.conn.execute(
            "SELECT source, repo, list_type FROM repos ORDER BY source, repo"
        ).fetchall()
        return [(r["source"], r["repo"], r["list_type"]) for r in rows]

    # ── PRs ───────────────────────────────────────────────────────────────

    def load_prs(self) -> list[dict]:
        """Load all PRs as dicts."""
        rows = self.conn.execute("SELECT * FROM prs").fetchall()
        return [_row_to_pr_dict(r) for r in rows]

    def load_prs_by_role(self, role: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM prs WHERE role = ?", (role,)
        ).fetchall()
        return [_row_to_pr_dict(r) for r in rows]

    def load_prs_active(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM prs WHERE status = 'active'"
        ).fetchall()
        return [_row_to_pr_dict(r) for r in rows]

    def get_pr(self, source: str, pr_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM prs WHERE source = ? AND id = ?", (source, pr_id)
        ).fetchone()
        return _row_to_pr_dict(row) if row else None

    def find_pr_by_id(self, pr_id: int, source: str = "") -> dict | None:
        """Find a PR by ID, optionally filtering by source."""
        if source:
            return self.get_pr(source, pr_id)
        row = self.conn.execute(
            "SELECT * FROM prs WHERE id = ?", (pr_id,)
        ).fetchone()
        return _row_to_pr_dict(row) if row else None

    def upsert_pr(self, pr: dict) -> bool:
        """Insert or replace a PR. Preserves pinned state on update.

        Returns True if the PR already existed (update), False if new.
        """
        source = pr.get("source", "")
        pr_id = pr.get("id", 0)

        # Check existing row for pinned state preservation
        existing = self.conn.execute(
            "SELECT pinned FROM prs WHERE source = ? AND id = ?", (source, pr_id)
        ).fetchone()

        existed = existing is not None
        if existed and existing["pinned"] and not pr.get("pinned"):
            pr["pinned"] = True

        row = _pr_dict_to_row(pr)
        self.conn.execute(
            """INSERT OR REPLACE INTO prs
               (source, id, role, status, repo_name, is_draft, is_mine, pinned, last_updated, data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        self.conn.commit()
        return existed

    def upsert_prs_batch(self, prs: list[dict]) -> None:
        """Batch upsert multiple PRs. Preserves pinned states."""
        if not prs:
            return

        # Pre-fetch pinned states for all PRs being upserted
        keys = [(pr.get("source", ""), pr.get("id", 0)) for pr in prs]
        placeholders = ",".join(["(?, ?)"] * len(keys))
        flat_keys = [v for k in keys for v in k]
        existing = self.conn.execute(
            f"SELECT source, id, pinned FROM prs WHERE (source, id) IN (VALUES {placeholders})",
            flat_keys,
        ).fetchall()
        pinned_map = {(r["source"], r["id"]): bool(r["pinned"]) for r in existing}

        # Apply pinned preservation
        for pr in prs:
            key = (pr.get("source", ""), pr.get("id", 0))
            if pinned_map.get(key) and not pr.get("pinned"):
                pr["pinned"] = True

        rows = [_pr_dict_to_row(pr) for pr in prs]
        self.conn.executemany(
            """INSERT OR REPLACE INTO prs
               (source, id, role, status, repo_name, is_draft, is_mine, pinned, last_updated, data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()

    def toggle_pin(self, source: str, pr_id: int) -> bool | None:
        """Toggle pinned state. Returns new state, or None if PR not found."""
        row = self.conn.execute(
            "SELECT pinned, data FROM prs WHERE source = ? AND id = ?",
            (source, pr_id),
        ).fetchone()
        if row is None:
            return None

        new_state = not bool(row["pinned"])
        # Update both indexed column and JSON blob
        pr = json.loads(row["data"])
        pr["pinned"] = new_state
        self.conn.execute(
            "UPDATE prs SET pinned = ?, data = ? WHERE source = ? AND id = ?",
            (1 if new_state else 0, json.dumps(pr, ensure_ascii=False), source, pr_id),
        )
        self.conn.commit()
        log.info("PR #%d pinned=%s", pr_id, new_state)
        return new_state

    def remove_pr(self, pr_id: int, source: str = "") -> bool:
        """Remove a PR. Returns True if found."""
        if source:
            cur = self.conn.execute(
                "DELETE FROM prs WHERE source = ? AND id = ?", (source, pr_id)
            )
        else:
            # Remove first match only (rowid order)
            cur = self.conn.execute(
                "DELETE FROM prs WHERE rowid = (SELECT rowid FROM prs WHERE id = ? LIMIT 1)",
                (pr_id,),
            )
        self.conn.commit()
        removed = cur.rowcount > 0
        if removed:
            log.info("Removed PR #%d", pr_id)
        else:
            log.warning("PR #%d not found for removal", pr_id)
        return removed

    def clean_non_active(self) -> int:
        """Remove non-active PRs. Returns count removed."""
        cur = self.conn.execute("DELETE FROM prs WHERE status != 'active'")
        self.conn.commit()
        removed = cur.rowcount
        if removed:
            log.info("Cleaned %d non-active PRs", removed)
        return removed

    def purge_reviewer_prs(
        self,
        excluded_repo_keys: set[tuple[str, str]],
    ) -> int:
        """Remove reviewer PRs from excluded repos, drafts, or own PRs."""
        if not excluded_repo_keys:
            # Just remove drafts and own reviewer PRs
            cur = self.conn.execute(
                "DELETE FROM prs WHERE role = 'reviewer' AND (is_draft = 1 OR is_mine = 1)"
            )
            self.conn.commit()
            return cur.rowcount

        # Build query for excluded repos + drafts + own
        conditions = []
        params: list = []
        for source, repo in excluded_repo_keys:
            conditions.append("(source = ? AND repo_name = ?)")
            params.extend([source, repo])

        excluded_clause = " OR ".join(conditions)
        cur = self.conn.execute(
            f"""DELETE FROM prs WHERE role = 'reviewer'
                AND (is_draft = 1 OR is_mine = 1 OR ({excluded_clause}))""",
            params,
        )
        self.conn.commit()
        return cur.rowcount

    def remove_reviewer_prs_for_repo(self, source: str, repo: str) -> int:
        """Remove reviewer PRs for a specific repo."""
        cur = self.conn.execute(
            "DELETE FROM prs WHERE role = 'reviewer' AND source = ? AND repo_name = ?",
            (source, repo),
        )
        self.conn.commit()
        return cur.rowcount

    def replace_all_prs(self, prs: list[dict]) -> None:
        """Replace the entire PR table with new data. Used by refresh_all."""
        self.conn.execute("DELETE FROM prs")
        if prs:
            rows = [_pr_dict_to_row(pr) for pr in prs]
            self.conn.executemany(
                """INSERT INTO prs
                   (source, id, role, status, repo_name, is_draft, is_mine, pinned, last_updated, data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        self.conn.commit()

    def pr_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM prs").fetchone()
        return row["cnt"]

    # ── end of Database class ──────────────────────────────────────────


def get_database(db_path: Path | None = None) -> Database:
    """Get a Database instance."""
    return Database(db_path=db_path or DB_FILE)
