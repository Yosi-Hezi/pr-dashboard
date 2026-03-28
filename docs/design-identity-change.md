# Design: Account Identity Change Handling

## Problem

When a user switches Azure or GitHub accounts (e.g. logs in as a different person), the data file retains PRs, sources, repos, pins, and excludes from the old identity. This creates stale/orphaned data that will never update again.

## Desired Behavior

**Per-provider wipe** — if the Azure identity changes, purge only `ado/*` data. If the GitHub identity changes, purge only `github` data. This avoids collateral damage (changing GH account shouldn't wipe valid ADO data).

## Design

### Data Model

Add a top-level `identity` key to the data file:

```json
{
  "identity": {"az": "user@microsoft.com", "gh": "gh_username"},
  "sources": { ... },
  "repos": { ... },
  "prs": [ ... ]
}
```

### Detection

At sync start, compare stored identity against current authenticated user:

- `az_user` comes from `AdoClient.get_az_username()` (already fetched in `app.py` startup)
- `gh_user` comes from `GhClient.check_auth()` (already fetched in `app.py` startup)

Pass both into `sync(az_user=..., gh_user=...)`.

### Purge Logic

`_purge_provider(data, prefix)` removes all data matching a provider:

- **Sources**: filter `discovered`, `include`, `exclude` lists — remove entries matching prefix
- **Repos**: filter all three lists — remove entries whose `source` matches prefix
- **PRs**: remove PRs whose `source` matches prefix

Prefix matching:
- `"ado/"` → matches `source.startswith("ado/")`
- `"github"` → matches `source == "github"`

### Identity Check Flow

```python
def _check_identity_change(data, az_user, gh_user) -> bool:
    identity = data.setdefault("identity", {"az": "", "gh": ""})
    changed = False

    # Only trigger if BOTH old and new are non-empty (first run = just store)
    if az_user and identity["az"] and identity["az"] != az_user:
        log.warning("Azure identity changed (%s → %s), purging ADO data", ...)
        _purge_provider(data, "ado/")
        changed = True
    if az_user:
        identity["az"] = az_user

    # Same for GitHub
    if gh_user and identity["gh"] and identity["gh"] != gh_user:
        log.warning("GitHub identity changed (%s → %s), purging GitHub data", ...)
        _purge_provider(data, "github")
        changed = True
    if gh_user:
        identity["gh"] = gh_user

    return changed
```

### Call Sites

1. `app.py` startup sync → `store.sync(az_user=self._az_user, gh_user=self._gh_user)`
2. `app.py` manual sync (r key) → same
3. CLI sync → no identity passed (optional, rare usage)

### Edge Cases

- **First run**: No stored identity → just store current, no purge
- **Auth failure**: `az_user=None` → skip comparison for that provider (don't purge on transient failures)
- **Both change simultaneously**: Both providers purged independently → effectively a full wipe

## Effort

~30 lines of code in `data.py`, ~5 lines in `app.py`. No schema changes needed — works with both JSON and future SQLite storage.
