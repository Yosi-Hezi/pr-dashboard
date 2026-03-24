# PR Dashboard — Release Notes

## What is PR Dashboard?

PR Dashboard is a terminal-based tool for monitoring pull requests across **Azure DevOps** and **GitHub** in a single unified view. It comes in two modes:

- **TUI** — an interactive, keyboard-driven dashboard (powered by [Textual](https://textual.textualize.io/))
- **CLI** — a headless mode for scripting and quick lookups (powered by [Rich](https://rich.readthedocs.io/))

It tracks your **authored PRs** and **code reviews**, showing live status for reviewers, policy checks, comment threads, and activity — all without leaving the terminal.

---

## Features

### Multi-Source Support

| Source | Auth | Discovery |
|--------|------|-----------|
| Azure DevOps (multiple orgs) | `az login` | Auto-discovers all orgs via VSSPS API |
| GitHub | `gh auth login` | Auto-detected on first run |

```bash
pr-dashboard register all      # auto-discover & register everything
pr-dashboard sources            # list registered sources
pr-dashboard unregister github  # remove a source
```

### Code Review Tracking

PRs are tagged with a **role** — `author` or `reviewer` — so you can see what you wrote and what's waiting for your review in separate views.

- **Sync** fetches both authored and reviewer PRs in parallel
- **Dedup**: if you're both author and reviewer, the PR shows as authored
- **myVote**: your current vote is extracted and displayed in a separate "Me" column (Reviews view)
- **isRequired** (ADO): tracks whether you're a required reviewer
- **Add by URL**: manually track any PR via `pr-dashboard add <url>`

### TUI Dashboard

| Key | Action |
|-----|--------|
| `?` | Help screen |
| `Tab` | Toggle view: My PRs ↔ Reviews |
| `r` | Refresh selected PR |
| `Ctrl+R` | Refresh all PRs |
| `Ctrl+S` | Full sync from all sources |
| `d` | Remove selected PR |
| `Shift+D` | Remove all done/abandoned PRs |
| `o` | Open PR in browser |
| `c` | Copy PR URL to clipboard |
| `/` | Filter by title, author, repo, ID |
| `v` | Quick peek (description + comments) |
| `i` | Connected sources & accounts |
| `l` | Activity log |
| `Escape` | Clear filter / close modal |
| `Ctrl+C` | Exit |

All keybindings are **configurable** via `config.json` in the data directory. Press `?` to see current effective bindings.

**Status bar** shows: auth status per source (🟢/🔴), source count, view mode, PR count.

**Detail panel** (bottom) shows full PR metadata: branches, reviewer votes, check statuses, comment counts, timestamps, and role indicator (`👁 REVIEW` for reviews).

### CLI Commands

```
pr-dashboard sync               # fetch from all sources
pr-dashboard list [--mine|--reviews] [--urls] [--json]
pr-dashboard show <id> [--json]
pr-dashboard refresh <id>       # refresh single PR
pr-dashboard refresh --all      # refresh all
pr-dashboard add <url>          # add PR by ADO or GitHub URL
pr-dashboard remove <id>
pr-dashboard clean              # remove completed/abandoned PRs
pr-dashboard sources [all]
pr-dashboard register {ado|github|all} [org]
pr-dashboard unregister <source>
```

### PR Enrichment

Every PR is enriched with data from the source API:

| Field | ADO | GitHub |
|-------|-----|--------|
| Status | ○ Active · ↻ Waiting · ✓ Approved · ✎ Draft · » Auto-complete · ✓✓ Done · ∅ Abandoned | ○ Open · ✓✓ Merged · ∅ Closed |
| Merge Status | `mergeStatus` field → ⚠ shown for conflicts | `mergeable_state` → ⚠ for dirty/behind |
| Description | PR description captured for quick peek | PR body captured for quick peek |
| Reviews | Full reviewer list with votes + `isRequired` flag | Latest review per user, mapped to standard votes |
| Checks | Policy evaluations (required vs optional, blocking flag) | Check runs by commit SHA |
| Comments | Thread count (active/total) via REST, excluding system threads | Thread count via GraphQL with `isResolved` status |
| Timestamps | Creation, last update (merge commit date), last fetched | Creation, last update, last fetched |
| Branches | Source → target | Head → base |

### Display Formatting

**Status symbols**: `○` Active · `↻` Waiting · `✓` Approved · `✎` Draft · `»` Auto-complete · `✓✓` Done · `∅` Abandoned · `⚠` Merge conflicts

**Review votes**: ✓ Approved · ↻ Changes Requested · ✗ Rejected · `!` Required pending — individual symbols per reviewer, optional no-votes hidden

**Me column** (Reviews view only): Shows your vote separately — `!` required pending, `·` optional pending, or vote symbol

**Checks**: `✓` all pass · `✗ 2/4` required fail · `~ 1/2` optional fail

**Comments**: `✓ 5` (all resolved) or `💬 2/5 3h ago` (unresolved threads)

**Merge conflicts**: `⚠` indicator shown in St column and detail panel when PR has merge conflicts (ADO `mergeStatus`, GitHub `mergeable_state`)

**Time**: relative display — `just now`, `5m ago`, `2h ago`, `3d ago`, `1w ago`

**Repo names**: common prefixes stripped (e.g., `AzNet-ApplicationSecurity-Foo` → `Foo`)

**Sorting**: by repository (ascending), then last updated (newest first)

### Data Persistence

- Stored in `platformdirs.user_data_dir("pr-dashboard")/prs.json`
- Schema versioned (v2) with auto-migration from v1
- Composite key `(source, id)` prevents cross-source duplication
- Async locking for concurrent operations

### Error Handling

- Auth errors surface with actionable messages (`az login` / `gh auth login`)
- API failures per-source don't block other sources from syncing
- Toast notifications in TUI with 8s auto-dismiss
- All operations logged to rotating file + in-app ring buffer (press `l`)

---

## Requirements

- **Python** ≥ 3.13
- **Azure CLI** (`az`) — for ADO authentication
- **GitHub CLI** (`gh`) — for GitHub authentication (optional)

### Dependencies

`textual` · `httpx` · `azure-identity` · `platformdirs` · `rich`

---

## Quick Start

```bash
cd pr-dashboard
uv sync              # install dependencies
uv run pr-dashboard  # launch TUI

# or headless
uv run pr-dashboard register all
uv run pr-dashboard sync
uv run pr-dashboard list --mine
```

---

## Phase 3 Changelog

### Minimalist Symbol Overhaul
- All status emojis replaced with fixed-width text symbols (`○ ↻ ✓ ✎ » ✓✓ ∅`)
- Consistent column widths across all statuses

### Merge Conflict Detection
- ADO: `mergeStatus` field captured — conflicts shown as `⚠` suffix
- GitHub: `mergeable_state` fetched — dirty/behind shown as `⚠` suffix
- Both St column and detail panel show merge conflict indicator

### Quick Peek (`v` hotkey)
- Modal showing **Description** + **Comment Threads** in a scrollable view
- Each section rendered as a separate Markdown widget with visual separators
- Active comment threads show: file:line context, author, and quoted text
- PR description rendered as Markdown (images sanitized to placeholders)
- Comment threads numbered with 💬 emoji headers

### Configurable Keybindings & Theme
- Config file: `config.json` in data directory (next to `prs.json`)
- Override any keybinding with `"keybindings": {"action": "key"}` format
- Configurable theme: `"theme": "dracula"` (supports all Textual themes)
- Supports single chars, ctrl+char, alt+char, shift+char, special keys
- Invalid keys → warning logged, defaults used
- Duplicate detection with warnings
- Help screen (`?`) shows effective bindings dynamically

### View-Scoped PR Count
- Status bar PR count now reflects current view mode
- "My PRs" shows only authored PR count; "Reviews" shows only review count
- Filter denominator also scoped to current view

### PR Description Capture
- ADO: `description` field now stored in enriched PR data
- GitHub: `body` field now stored in enriched PR data
- Used by quick peek modal

### GitHub Gaps (Known Limitations)
- No `isRequired` per reviewer (GitHub uses count-based branch protection)
- No work item linking (GitHub has linked issues, not fetched)
- No required/optional check distinction (all checks treated as required)
- `mergeable_state` may return `unknown` on first fetch (GitHub computes lazily)

---

## Phase 4 Changelog

### Extension Scripts
- User-defined scripts triggered by hotkey, receiving full PR data as JSON
- Configure in `config.json`:
  ```json
  {
    "extensions": [
      {"key": "x", "name": "Open Worktree", "command": "pwsh -File path/to/script.ps1 {json_file}"}
    ]
  }
  ```
- `{json_file}` replaced with temp file containing PR JSON at runtime
- Fire-and-forget async execution with toast notifications (start/success/error)
- All script stdout/stderr logged to activity log (press `l`)
- Extension keys validated against built-in bindings (conflicts rejected)
- Extensions shown in footer and help screen (`?`)

### Bundled Extension: Open Worktree
- `extensions/open-worktree.ps1` — opens VS Code for a PR's source branch
- Scans `C:\repos` for git worktrees matching the PR's source branch
- If worktree exists → opens VS Code there immediately
- If not → finds the repo, fetches, creates a new worktree, opens VS Code
- Handles existing local branches gracefully (no duplicate branch errors)
- Clear status messages for each code path in the log
