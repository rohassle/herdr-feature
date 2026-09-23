# Architecture

## The shape of the problem

Herdr binds one Git worktree to one workspace. A feature that touches several repositories
needs one workspace whose working directory contains all of them. So a **feature root**
(`<features_directory>/<name>/`) holds a real worktree per repository as a child folder,
plus `.feature.json`. The workspace is an ordinary Herdr workspace whose single tab starts
in the feature root. Nothing is symlinked, nothing is a repository at the root level.

```
~/.herdr/features/pay-1234-retry/
├── payments-api/              worktree of ~/code/payments-api on pay-1234-retry
├── payments-api@migration/    second worktree of the same repo, on pay-1234-retry-migration
├── payments-web/              worktree of ~/code/payments-web on pay-1234-retry
└── .feature.json
```

## Two front doors, one core

```
 prefix+f ─► action.sh ─► herdr plugin pane open (popup) ─► bootstrap.sh ─► python -m herdr_feature
                                                                           │  FEATURE_ACTION=new|add|...
                                                                           ▼
 herdr-feature new --name … ─► bootstrap.sh cli ─► herdr_feature.cli ──► commands/common.py
                                                                           preflight · execute · rollback
                                                                           │
                                          ┌────────────────────────────────┼─────────────────────┐
                                          ▼                                ▼                     ▼
                                       gitops.py                       manifest.py            herdr.py
                                (git worktree/fetch/branch)        (.feature.json)     (herdr CLI, pane scan)
```

- **Popup path** (`commands/*.py`): interactive. `fzf` pickers and typed prompts via `ui.py`.
  Actions run without a TTY, so `bin/action.sh` only opens the single popup entrypoint and
  forwards the action id and the invoker's workspace in the environment (ADR 0005).
- **CLI path** (`cli.py`): non-interactive. It calls `ui.set_noninteractive(answers)`; every
  prompt in the shared code carries a `key`, and `ui._read` answers from that dict or aborts
  with a message naming the missing option. Progress goes to stderr, `--json` to stdout.
  Mutating commands are dry runs until `--yes`.

Both paths converge in `commands/common.py`:

1. **preflight** validates names, paths and branches; detects each repository's default
   branch; fetches all remotes in parallel; decides per repository how the branch will be
   created (`plan_branch`); enforces `(repo, branch)` uniqueness across every manifest.
   Nothing is written yet. The only interactive moments are a fetch failure and a branch
   already checked out elsewhere; both are keyed prompts.
2. **execute** marks the manifest `creating`, runs `git worktree add` per entry, appends each
   entry to the manifest as it lands, then marks `ready`.
3. **rollback** on any failure undoes this run's worktrees and only the branches this run
   created, then removes the root (for `new`) or rewrites the manifest (for `add`).

Workspace creation happens after the atomic unit: if Herdr refuses, the feature is still on
disk and `open` creates the workspace later.

## Modules

| Module | Responsibility | Talks to |
|---|---|---|
| `__main__.py` | Entry. Dispatches on `FEATURE_ACTION`/argv, handles `cli`, `preview-*` for fzf previews, converts `Abort`/`Cancelled` into exit codes. | everything |
| `cli.py` | argparse surface for agents/scripts; resolves `--repo` names, builds `Request`s, prints JSON. | `commands.common`, `herdr`, `gitops` |
| `commands/common.py` | Pickers, `preflight`, `execute`, `rollback`, status words, state tables. | `gitops`, `manifest`, `herdr`, `ui`, `names` |
| `commands/{new,add,open_,drop,remove,menu,install_cli}.py` | One interactive flow each. Thin. | `common` |
| `ui.py` | fzf wrapper (hidden key column, exit codes, neutralised env), prompts with keys, scripted answers for tests, non-interactive mode, output stream. | fzf |
| `gitops.py` | All git: default-branch chain, parallel fetch with error classification, branch matrix, `worktree add/remove/prune`, state. | git |
| `manifest.py` | `Feature`/`Worktree` dataclasses, versioned load/validate/migrate, atomic save, discovery, branch claims. | filesystem |
| `herdr.py` | `herdr` CLI wrapper with stderr JSON error parsing; invocation context; feature to workspace mapping; workspace create/focus/close. | herdr |
| `discovery.py` | Repository scanner (primary checkouts only). | filesystem |
| `config.py` | TOML config with validation and first-run template. | filesystem, git |
| `names.py` | Name rules, branch/folder derivation, `git check-ref-format`. | git |
| `lock.py` | One mutation at a time (state-dir lock file, stale detection). | filesystem |

## Key decisions and why (details in `docs/adr/`)

- **Workspace identity comes from pane working directories** (0006). Herdr renumbers
  workspaces on server restart and exposes no cwd on workspaces, but `herdr pane list`
  reports every pane's cwd. A feature is live when a pane sits inside its root (the plugin's
  own popup, which runs in the plugin folder, is excluded). The manifest's `workspace` block
  is a hint that is rewritten whenever the scan resolves.
- **Existing branches are reused** (0004), so `branch_created` decides what `remove` may
  delete. Because feature `x` + suffix `y` and feature `x-y` collide on branch `x-y`,
  preflight checks `(repo_path, branch)` against every manifest.
- **`git worktree add --no-track`** for new branches: git would otherwise set upstream to
  `origin/<default>` and "unpushed" detection would be wrong. Remote-only branches use
  `--track -b`.
- **Flat top-level workspace** (0003), not nested under an anchor repository.
- **One popup entrypoint** (0005) because a popup cannot open another popup and cannot be
  addressed via pane APIs; the menu runs sub-commands in-process.

## Manifest (`.feature.json`, version 1)

```json
{ "version": 1, "feature": "pay-1234-retry", "status": "ready",
  "created_at": "…", "updated_at": "…", "branch_prefix": "",
  "workspace": { "id": "wG", "label": "pay-1234-retry", "seen_at": "…" },
  "worktrees": [ { "repo_name": "payments-api", "repo_path": "/home/me/code/payments-api",
                   "folder": "payments-api", "suffix": null, "branch": "pay-1234-retry",
                   "branch_created": true, "branch_source": "new",
                   "base_ref": "refs/remotes/origin/main", "base_commit": "…",
                   "remote": "origin", "added_at": "…" } ] }
```

`folder` is relative so the features directory can move; `repo_path` is absolute so removal
works even when the worktree folder is gone. `status: creating` marks an interrupted run and
is offered for cleanup on the next `new` with that name. Unknown higher versions load
read-only.

## Testing

- `tests/test_*.py` (unittest): pure functions, parsers with fixture strings, and real git in
  temporary repositories. Herdr is mocked in `test_herdr.py`.
- `tests/e2e/run.py`: builds bare origins and clones in a temp dir (mixed `main`/`master`,
  one repo with no remote, one with `origin/HEAD` unset), then runs the real entrypoint with
  a fake `fzf` (`HERDR_FEATURE_FZF`) and scripted answers (`HERDR_FEATURE_INPUTS`), and the
  CLI, against the live Herdr session. Workspaces are labelled `zz-test-*` and only the ones
  the run created are closed.
