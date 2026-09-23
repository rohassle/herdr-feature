# herdr-feature

One [Herdr](https://herdr.dev) workspace per feature, holding Git worktrees from as many
repositories as the feature touches.

Herdr's built-in worktree support binds one worktree to one workspace. That is the right
model until a change spans several repositories and the agent working on it can only see
one of them. This plugin puts every worktree of a feature under one folder and gives that
folder one workspace, so a single agent, `rg`, `fd`, and your editor see all of them as
sibling directories.

```
~/.herdr/features/pay-1234-retry/         <- the workspace's tab starts here
├── payments-api/                   <- worktree on pay-1234-retry
├── payments-api@migration/         <- second worktree of the same repo
├── payments-web/                           <- worktree on pay-1234-retry
└── .feature.json                           <- manifest, the source of truth
```

Closing the workspace never deletes anything. Removal is a separate, explicit, reviewed
command.

Optionally, every worktree of a feature also gets its own Herdr worktree workspace, nested
in the sidebar under its repository's workspace (`workspaces = "both"` in the config):

```
payments-api                          <- the repository's own workspace
  └ pay-1234-retry                    <- worktree workspace for payments-api/
  └ pay-1234-retry@migration          <- worktree workspace for payments-api@migration/
payments-web
  └ pay-1234-retry
pay-1234-retry                        <- the feature workspace, all repos side by side
```

Herdr groups the sidebar by repository, so the nested rows sit under each repository
rather than under the feature. A feature folder that is not itself a repository cannot be
a Herdr group parent; see [docs/adr/0007](docs/adr/0007-optional-nested-worktree-workspaces.md).

## Requirements

- Herdr 0.9.0 or newer
- Python 3.11 or newer (for `tomllib`)
- [`fzf`](https://github.com/junegunn/fzf)
- Git 2.31 or newer
- macOS or Linux

The bootstrap probes common install locations for Python and `fzf` and does not rely on
`PATH`. Override with `HERDR_FEATURE_PYTHON` and `HERDR_FEATURE_FZF` if needed.

## Install

```sh
herdr plugin install rohassle/herdr-feature      # or: herdr plugin link /path/to/checkout
herdr plugin config-dir feature                 # prints where config.toml lives
```

Create `config.toml` there (the first run offers to write this template for you):

```toml
# Folders scanned one level deep for git repositories.
repo_directories = ["~/code"]

# Extra repositories anywhere on disk.
repos = []

# Where feature roots are created.
features_directory = "~/.herdr/features"

# Prepended to every branch the plugin creates. May contain '/'.
branch_prefix = ""
```

Bind the menu in `~/.config/herdr/config.toml` (every action is also bindable on its own:
`feature.new`, `feature.add`, `feature.open`, `feature.drop`, `feature.remove`):

```toml
[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "feature.menu"
description = "feature workspaces menu"
```

## Use

Press `prefix+f`. A popup offers five commands.

**new**: type a feature name, Tab-mark repositories in `fzf` (the preview shows each
repository's recent commits), confirm the plan. Every repository gets a worktree on the
branch `<branch_prefix><feature>`, created from a freshly fetched `origin/<default>`.
The default branch is detected per repository, so a mix of `main` and `master` works,
and repositories without a remote branch from their local default. When the branch
already exists locally or on `origin` it is reused rather than refused. Creation is all
or nothing: a failure in the third repository removes what the first two created. The
workspace opens focused, with one tab rooted at the feature folder.

**add**: from inside a feature's workspace, Tab-mark more repositories. A repository that
is already part of the feature is marked with `●`; picking it again asks for a short
suffix and creates a second worktree, `<repo>@<suffix>` on `<branch>-<suffix>`.

**open**: list every feature on disk with its status (`open`, `closed`, `[interrupted]`).
Open features are focused; closed ones get a fresh workspace. With nested workspaces
enabled, `open` also re-opens any per-repository workspace that is missing.

**drop**: remove individual worktrees from the current feature. Branches are kept.
Worktrees with uncommitted or unpushed work require typing the feature name.

**remove**: show the state of every worktree, confirm, close the workspace (and the nested
per-repository workspaces), remove the worktrees and the folder, then offer to delete the
local branches the plugin itself created. Branches it merely reused, and remote branches,
are never deleted. The repository workspaces Herdr opened as group parents stay open.

If a fetch fails (offline, VPN down) you are asked once whether to continue from the
last fetched state or abort everything.

## Driving it from an agent or a script

`herdr-feature` is the non-interactive twin of the popup: every prompt becomes a flag,
mutating commands are dry runs until `--yes`, and `--json` prints a result object on
stdout (progress goes to stderr). Put it on your PATH once, from the menu (`prefix+f`,
then `install-cli`) or without the UI:

```sh
herdr plugin action invoke install-cli --plugin feature      # opens the popup
"$(herdr plugin list --plugin feature --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["plugins"][0]["plugin_root"])')/bin/herdr-feature" install-cli
```

Both create `~/.local/bin/herdr-feature` and `~/.claude/skills/herdr-feature` as symlinks
into the plugin folder (`--no-skill` to skip the skill, `uninstall-cli` to remove both).

```sh
herdr-feature list --json
herdr-feature new --name pay-1234-retry --repo payments-api --repo payments-web --yes --json
herdr-feature add --feature pay-1234-retry --repo payments-api --suffix payments-api=migration --yes
herdr-feature open --feature pay-1234-retry
herdr-feature drop --feature pay-1234-retry --worktree payments-api@migration --yes
herdr-feature remove --feature pay-1234-retry --yes --delete-branches
```

Inside a feature's workspace `--feature` may be omitted for `add`, `drop` and `remove`.
`--on-fetch-failure continue` replaces the popup's prompt; `--force` replaces the typed
confirmation for worktrees with unsaved work. A ready-made agent skill lives in
[skills/herdr-feature](skills/herdr-feature/SKILL.md).

## Configuration

`herdr plugin config-dir feature` names the folder holding `config.toml`; the first run
writes a template. See [examples/config.toml](examples/config.toml).

| key | meaning |
|---|---|
| `repo_directories` | folders scanned one level deep for repositories |
| `repos` | extra repositories anywhere on disk |
| `features_directory` | where feature roots are created (default `~/.herdr/features`) |
| `branch_prefix` | prepended to every branch the plugin creates |
| `workspaces` | `"feature"` (default): one workspace rooted at the feature folder. `"repos"`: one worktree workspace per entry, nested under its repository in the sidebar. `"both"`: both. |

## How it decides which workspace is a feature's

Herdr reassigns workspace ids when its server restarts, so the manifest only keeps a hint.
The plugin scans the working directories of all live panes; a pane inside a feature folder
marks that workspace as the feature's, and the hint is refreshed. See
[docs/adr/0006](docs/adr/0006-workspace-identity-comes-from-pane-cwd.md). Nested
per-repository workspaces are recognised by the checkout path Herdr records on them, and
are never mistaken for the feature workspace.

## Development

Development uses [uv](https://docs.astral.sh/uv/); the plugin itself has no dependencies and
runs on any system Python 3.11+.

```sh
uv sync
uv run python -m unittest discover -s tests -t .   # pure functions and real git in temp dirs
uv run tests/e2e/run.py                            # inside Herdr: creates and removes zz-test-* workspaces
uv run ruff check .
herdr plugin link "$PWD"                           # run the popup from this checkout
```

See [AGENTS.md](AGENTS.md) for the working rules and [ARCHITECTURE.md](ARCHITECTURE.md) for how
the pieces fit.

Actions are not interactive, so the popup can be driven by `herdr plugin action invoke
menu --plugin feature`. Plugin stderr lands in `herdr plugin log list --plugin feature`.

Vocabulary is in [CONTEXT.md](CONTEXT.md); design decisions in [docs/adr](docs/adr).

## License

MIT
