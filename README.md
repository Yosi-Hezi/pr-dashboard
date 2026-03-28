# PR Dashboard

Terminal-based tool for monitoring pull requests across **Azure DevOps** and **GitHub** in a single unified view.

- **TUI** — interactive, keyboard-driven dashboard (powered by [Textual](https://textual.textualize.io/))
- **CLI** — headless mode for scripting and quick lookups (powered by [Rich](https://rich.readthedocs.io/))

Tracks your **authored PRs** and **code reviews** with live status for reviewers, policy checks, comment threads, and activity — all without leaving the terminal.

## Install

```bash
# With uv (recommended)
uv tool install git+https://github.com/Yosi-Hezi/pr-dashboard

# With pipx
pipx install git+https://github.com/Yosi-Hezi/pr-dashboard
```

## Update

```bash
# With uv
uv tool upgrade pr-dashboard

# With pipx
pipx upgrade pr-dashboard
```

## Prerequisites

- **Python** ≥ 3.12
- **Azure CLI** (`az login`) — for ADO authentication
- **GitHub CLI** (`gh auth login`) — for GitHub authentication (optional)

## Quick Start

```bash
# Launch interactive dashboard
pr-dashboard

# Or use CLI commands
pr-dashboard sync            # auto-discover sources & fetch PRs
pr-dashboard list --mine     # list your PRs
pr-dashboard list --reviews  # list reviews assigned to you
```

## TUI Keybindings

| Key | Action |
|-----|--------|
| `?` | Help screen |
| `Tab` | Toggle: My PRs ↔ Reviews |
| `s` | Refresh all PRs |
| `S` | Full sync (discover sources + fetch PRs) |
| `d` / `D` | Remove selected / all done PRs |
| `O` | Open PR in browser |
| `o` | Copy PR URL to clipboard |
| `/` | Filter by title, author, repo, ID |
| `Space` | Pin/unpin selected PR |
| `f` | Toggle pinned-only filter |
| `v` | Peek at description & comments |
| `a` | Add PR by URL |
| `m` | Manage repos (include/exclude) |
| `M` | Manage sources (include/exclude) |
| `i` | Connected sources & accounts |
| `l` | Activity log |
| `Esc` | Clear filter / close modal |

All keybindings are configurable via `config.json`. Press `?` to see current bindings.

## Source & Repo Management

Sources (ADO orgs, GitHub) and repos are auto-discovered on sync. You can include/exclude them to control what gets synced.

### Data Model

Sources and repos each have three lists:
- **discovered** — auto-populated on every sync (overwritten each time)
- **include** — manually added items (persist across syncs)
- **exclude** — items to skip during sync

**Active** = (discovered ∪ include) − exclude

### TUI Management

- Press `M` to manage sources — toggle with `Space` (✓ active / ✗ excluded)
- Press `m` to manage repos — toggle with `Space`, or type a repo URL to add

Toggle behavior:
- Discovered items get excluded (✗ marker) — they stay in the list
- Include-only items get deleted entirely (disappear from the list)

### CLI Management

```bash
# Sources
pr-dashboard sources                          # list all sources with status
pr-dashboard sources include ado/myorg        # manually include a source
pr-dashboard sources exclude ado/myorg        # exclude a source from sync

# Repos
pr-dashboard repos                            # list all repos with status
pr-dashboard repos include ado/myorg MyRepo   # manually include a repo
pr-dashboard repos exclude ado/myorg MyRepo   # exclude a repo from sync

# Repos can reference excluded sources — only those specific repos sync
```

## CLI Commands

```
pr-dashboard                                # launch TUI
pr-dashboard sync                           # discover sources + fetch PRs
pr-dashboard sync --refresh                 # refresh tracked PRs (no discovery)
pr-dashboard sync <id>                      # refresh a specific PR
pr-dashboard list [--mine|--reviews] [--urls] [--json]
pr-dashboard show <id> [--json]
pr-dashboard add <url>                      # add PR by ADO or GitHub URL
pr-dashboard remove <id>
pr-dashboard clean                          # remove completed/abandoned
pr-dashboard config                         # show config file location
pr-dashboard sources [include|exclude] [source]
pr-dashboard repos [include|exclude] [source] [repo]
pr-dashboard exclude <source> <repo>        # shortcut for repos exclude
pr-dashboard include <source> <repo>        # shortcut for repos include
```

## Configuration

Find your config file:
```bash
pr-dashboard config
```

```json
{
  "theme": "dracula",
  "keybindings": {
    "main.refresh": "f5",
    "main.open": "enter"
  },
  "extensions": [
    {
      "key": "x",
      "name": "Run Script",
      "command": "pwsh -File my/script/path/script.ps1 {json_file}"
    }
  ]
}
```

### Extensions

Extensions let you run custom scripts triggered by a hotkey. When you press the extension key, the selected PR's full data is written to a temporary JSON file, and `{json_file}` in the command is replaced with its path.

The JSON file contains all PR fields (title, status, source branch, repo, reviewers, comments, etc.), so your script can read it and take any action — open an IDE, create a worktree, post a notification, trigger a pipeline, etc.

```bash
# Example: your script receives the temp file path as $args[0]
$pr = Get-Content $args[0] | ConvertFrom-Json
Write-Host "PR: $($pr.title) in $($pr.repoName)"
```

### Row Highlighting Rules

Row rules control how PRs are highlighted based on computed signals. 9 default rules ship out of the box (e.g., merge conflicts → red italic, required reviewer → amber).

Each default rule has a stable `id`. You can selectively override, disable, or extend rules:

```json
{
  "display": {
    "row_rules": [
      {"id": "conflicts", "enabled": false},
      {"id": "approved", "color": "#00ff00"},
      {"conditions": {"isDraft": true}, "color": "#333", "action": "Draft"}
    ]
  }
}
```

- **Disable a rule**: `{"id": "<rule-id>", "enabled": false}`
- **Override fields**: `{"id": "<rule-id>", "color": "#..."}` — merges with the default
- **Add custom rules**: rules without a matching default id append after defaults
Press `R` in the TUI to see all active rules with their IDs, conditions, and styles.

Available rule IDs: `conflicts`, `author-comments`, `reviewer-required`, `reviewer-pending-reply`, `reviewer-resolved`, `approved`, `completed`, `abandoned`, `reviewer-optional`

Run `pr-dashboard config defaults` to see the full default configuration including all rules.

## Features

- **Multi-source**: Azure DevOps (multiple orgs) + GitHub side-by-side
- **Auto-discovery**: finds all your ADO orgs and repos automatically on sync
- **Source/repo management**: include/exclude sources and repos via TUI or CLI
- **Code review tracking**: separate views for authored PRs and reviews
- **Add PRs by URL**: manually track any PR from the TUI (`a`) or CLI
- **Pin PRs**: pin important PRs to the top of the list (`f`)
- **Rich detail panel**: reviewers, checks, comments, work items, timestamps
- **Merge conflict detection**: ⚠ indicator for ADO and GitHub
- **Comment threads**: peek shows full thread content with file:line context
- **Animated sync spinner**: visual feedback during sync operations
- **Parallel sync**: sources synced concurrently with rate limiting
- **Extensible**: run custom scripts with PR context via hotkeys

## Development

### Setup

```bash
git clone https://github.com/Yosi-Hezi/pr-dashboard
cd pr-dashboard
uv sync                # create .venv and install dependencies
```

### Run locally

```bash
# Install as editable — creates pr-dashboard.exe in .venv/Scripts
uv sync --reinstall-package pr-dashboard

# Run
.venv/Scripts/pr-dashboard          # TUI
.venv/Scripts/pr-dashboard sync     # CLI
```

After code changes, re-run `uv sync --reinstall-package pr-dashboard` to rebuild.

### Tests, lint, format

```bash
# Run all tests
.venv/Scripts/python -m pytest tests/ -v --tb=short

# Format + lint (install ruff first if needed: uv pip install ruff)
ruff format src/ tests/
ruff check src/ tests/ --fix
```
