# Working on herdr-workthreads

A Herdr plugin. A work thread runs through several repositories: a folder under
`~/.herdr/workthreads/<name>/` holding one Git worktree per repository plus a `.workthread.json`
manifest, surfaced as Herdr workspaces. Users drive it from the board popup (`prefix+f`),
agents and scripts from the `herdr-workthreads` CLI. Both share the same core. A worktree is
done when its pull request is merged (looked up with `gh`, ADR 0008). Read [ARCHITECTURE.md](ARCHITECTURE.md) before
changing anything under `src/`, and [CONTEXT.md](CONTEXT.md) for the vocabulary.

## Rules of the road

- **No runtime dependencies.** Herdr runs the plugin with a system Python 3.11+ found by
  `bin/bootstrap.sh`; end users never install a virtualenv. Standard library plus the `fzf`
  and `gh` commands only. `uv` is for development.
- **Done means merged on GitHub.** Progress comes from `gh`, never from git history alone;
  without `gh` progress is `unknown`, not guessed. Lookups happen only on refresh.
- **Closing a workspace never deletes anything.** Only `remove` and `drop` delete, after
  showing state and confirming. Do not add "cleanup on close" behaviour.
- **The manifest is the source of truth**, written atomically. Herdr workspace ids are hints
  (they change on server restart); liveness comes from pane working directories.
- **Only branches the plugin created are ever deleted** (`branch_created` in the manifest).
- Every prompt goes through `ui.py` and carries a `key`, so the CLI can answer it with a
  flag. If you add a prompt, add the key and the CLI flag together.
- Decisions with a "why" live in `docs/adr/`. Add an ADR when you reverse or extend one.

## Commands

```sh
uv sync                                   # dev environment (.venv) with ruff
uv run python -m unittest discover -s tests -t . -v      # unit tests: pure functions + real git in temp dirs
uv run tests/e2e/run.py                   # inside Herdr only: creates and removes zz-test-* workspaces
uv run ruff check . && uv run ruff format --check .
uv run herdr-workthreads list                 # the CLI from the checkout, without installing anything
```

To run the popup from your checkout instead of an installed copy:

```sh
herdr plugin uninstall workthreads            # if a GitHub install is active
herdr plugin link "$PWD"
herdr-workthreads install-cli                 # re-point ~/.local/bin/herdr-workthreads at this checkout
herdr plugin log list --plugin workthreads    # stderr of action runs
```

`herdr plugin link` skips build steps and picks up file edits immediately; no restart needed.

## Layout

```
herdr-plugin.toml        manifest: 8 actions (all run bin/action.sh), 1 popup pane (bin/bootstrap.sh)
bin/                     action.sh (opens the popup), bootstrap.sh (finds python, runs the package), herdr-workthreads (CLI launcher)
src/herdr_workthreads/       the package; see ARCHITECTURE.md
tests/                   unittest modules; tests/e2e/run.py drives the real thing against fixture repos
                         with a fake fzf (fake_fzf.sh) and a fake gh (fake_gh.sh)
skills/herdr-workthreads/    Claude Code skill installed by install-cli
docs/adr/                design decisions
```

## When you change behaviour

Update the README (user-facing), the skill (agent-facing) and the ADRs (why) in the same
change. Keep examples free of real repository names and personal paths.
