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

## Prerequisites

- **Python** ≥ 3.12
- **Azure CLI** (`az login`) — for ADO authentication
- **GitHub CLI** (`gh auth login`) — for GitHub authentication (optional)

## Quick Start

```bash
# Launch interactive dashboard
pr-dashboard

# Or use CLI commands
pr-dashboard register all    # auto-discover sources
pr-dashboard sync            # fetch PRs
pr-dashboard list --mine     # list your PRs
```

## TUI Keybindings

| Key | Action |
|-----|--------|
| `?` | Help screen |
| `Tab` | Toggle: My PRs ↔ Reviews |
| `r` | Refresh selected PR |
| `Ctrl+R` | Refresh all PRs |
| `Ctrl+S` | Full sync from all sources |
| `d` / `Shift+D` | Remove selected / all done PRs |
| `o` | Open PR in browser |
| `c` | Copy PR URL to clipboard |
| `/` | Filter by title, author, repo, ID |
| `v` | Peek at description & comments |
| `i` | Connected sources & accounts |
| `l` | Activity log |
| `Esc` | Clear filter / close modal |

All keybindings are configurable via `config.json`. Press `?` to see current bindings.

## CLI Commands

```
pr-dashboard sync                          # fetch from all sources
pr-dashboard list [--mine|--reviews] [--urls] [--json]
pr-dashboard show <id> [--json]
pr-dashboard refresh <id> | --all
pr-dashboard add <url>                     # add PR by URL
pr-dashboard remove <id>
pr-dashboard clean                         # remove completed/abandoned
pr-dashboard sources [all]
pr-dashboard register {ado|github|all} [org]
pr-dashboard unregister <source>
```

## Configuration

Config file location: `<platformdirs data dir>/pr-dashboard/config.json`

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
      "name": "Open Worktree",
      "command": "pwsh -File path/to/open-worktree.ps1 {json_file}"
    }
  ]
}
```

### Extensions

Extensions are user-defined scripts triggered by hotkey. The selected PR's data is written to a temp JSON file, and `{json_file}` in the command is replaced with its path.

A bundled example (`extensions/open-worktree.ps1`) opens VS Code at the git worktree matching the PR's source branch.

## Features

- **Multi-source**: Azure DevOps (multiple orgs) + GitHub side-by-side
- **Auto-discovery**: finds all your ADO orgs automatically
- **Code review tracking**: separate views for authored PRs and reviews
- **Rich detail panel**: reviewers, checks, comments, work items, timestamps
- **Merge conflict detection**: ⚠ indicator for ADO and GitHub
- **Comment threads**: peek shows full thread content with file:line context
- **Extensible**: run custom scripts with PR context via hotkeys
