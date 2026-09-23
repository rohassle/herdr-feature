---
name: herdr-feature
description: Create and manage cross-repository feature workspaces in Herdr from the command line with `herdr-feature` (new, add, list, open, drop, remove). Use when asked to start a feature spanning several repositories, add another repository's worktree to the current feature, or tear a feature down. Requires running inside Herdr (HERDR_ENV=1).
---

# herdr-feature

A feature is a folder under `~/.herdr/features/<name>/` holding one Git worktree per
repository, plus one Herdr workspace whose tab starts in that folder. `herdr-feature` is
the non-interactive twin of the plugin's `prefix+f` popup.

Check you are inside Herdr first: `test "${HERDR_ENV:-}" = 1`.

## Commands

If `herdr-feature` is not on PATH, run the plugin's `install-cli` action once (`prefix+f`, then `install-cli`).

Every mutating command is a **dry run until you add `--yes`**. Add `--json` for a machine
readable result on stdout; progress lines go to stderr.

```bash
herdr-feature list --json                                   # features, status, workspace id, worktree states
herdr-feature new --name pay-1234-retry --repo payments-api --repo payments-web            # dry run: shows the plan
herdr-feature new --name pay-1234-retry --repo payments-api --repo payments-web --yes --json
herdr-feature add --feature pay-1234-retry --repo shared-charts --yes
herdr-feature add --feature pay-1234-retry --repo payments-api --suffix payments-api=migration --yes
herdr-feature open --feature pay-1234-retry --json         # focuses nothing unless --focus; creates a workspace if none
herdr-feature drop --feature pay-1234-retry --worktree shared-charts --yes
herdr-feature remove --feature pay-1234-retry --yes        # add --delete-branches to also delete branches it created
```

- `--repo` takes a repository name from the configured folders (`~/code` by default)
  or a path. Repeat it per repository.
- A second worktree of the same repository needs `--suffix REPO=NAME`; it lands in
  `REPO@NAME` on branch `<branch>-NAME`.
- `--on-fetch-failure continue` uses the last fetched state when the remote is unreachable;
  the default aborts with nothing created.
- Inside a feature's workspace, `add`, `drop` and `remove` default to that feature, so
  `--feature` can be omitted.
- `drop`/`remove` refuse worktrees with uncommitted or unpushed work unless `--force`.
- `new` leaves the user's focus alone; pass `--focus` only if asked to switch.

## After creating a feature

The JSON result carries `workspace_id` and each worktree's `path`. Continue with the
Herdr CLI, for example to start an agent in the new workspace:

```bash
ws=$(herdr-feature new --name X --repo a --repo b --yes --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["workspace_id"])')
pane=$(herdr pane list --workspace "$ws" | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["panes"][0]["pane_id"])')
herdr agent start worker --kind claude --pane "$pane"
```

Closing a workspace never deletes a feature; only `remove` does.
